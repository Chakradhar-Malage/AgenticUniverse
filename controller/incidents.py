"""Hallucination incident response (capability C8).

FLOW (a small state machine, one row per incident in the `incidents` table):
  detected -> validating -> [suggesting -> validating] -> ready -> proposed -> verifying -> resolved | failed

DESIGN CHOICES (each is defensible in the review):
  * DETECT on populations: the flag rate under the CURRENT prompt, over the last 3 hours, against a baseline
    from the workflow's own history. Needs >= 150 connected calls, >= 8 flags, >= 2x baseline AND an exact
    binomial p < 1e-4. The detector is noisy (about 1% false flags even on a good prompt), so a ratio alone is not enough.
  * CHEAPEST FIX FIRST: the previous production prompt is a known quantity (we have its measured engagement).
    Validate it first (6 credits). Only if it fails do we spend 3 credits on LLM suggestions. "Spend credits where
    they change a decision."
  * AN LLM NEVER DECIDES: it only proposes candidates. We screen them against the catalog text (the Ops memo's
    warning: a 'fix' that reads the compliance script verbatim kills engagement), then VALIDATE (safety), and the
    approver independently checks the validation evidence.
  * NO SILENT OVERRIDE: a prompt change is never auto-applied. It goes through /approvals with evidence, a diff
    and the numbers, so the supervisor who made the edit sees exactly why.
  * VERIFY AFTER APPLY: we re-measure the flag rate under the new prompt and close the incident only when it is back to baseline.
  * CREDIT SAFETY: per-incident cap and a global reserve, so we never run dry mid-incident.
"""
import copy
import json
import math
import re

from .client import SimError
from .governance import can_request, parse_ts
from .ingest import day_hour
from .stats import binom_sf

WINDOW_H = 3
MIN_CONN = 150
MIN_FLAGS = 8
RATIO = 2.0
P_ALERT = 1e-4
BASE_DAYS = 7
BASE_MIN_CONN = 1500
VALIDATION_N = 600
LLM_COST = 3
INCIDENT_CREDIT_CAP = 25
CREDIT_RESERVE = 30
VERIFY_MIN_CONN = 150
COOLDOWN_H = 24
MAX_ATTEMPTS = 3
NO_PRIOR_P = 1e-6      # without a known-good prior prompt, changing a customer-facing prompt needs much stronger evidence
# WHOLE-WORD match: a bare 'strict' also matches 'restricted' (found by a test: it wrongly screened out the good prompt P3).
RISKY = re.compile(r"\b(verbatim|word[- ]for[- ]word|strict|persuasive|aggressive|urgency|freer|compliance script)\b", re.I)
CLOSED = ("resolved", "failed")


# ------------------------------------------------------------------ queries and pure helpers
def _idx(ts):
    d, h = day_hour(ts)
    return d * 24 + h


def window(store, wf, prompt, lo, hi, exclude=False):
    """(connected, flags, engaged) for calls under `prompt` (or NOT under it) in hour-index range [lo, hi]."""
    op = "!=" if exclude else "="
    r = store.db.execute(
        f"SELECT COALESCE(SUM(connected),0), COALESCE(SUM(halluc),0), COALESCE(SUM(engaged),0) FROM agg "
        f"WHERE workflow_id=? AND day BETWEEN ? AND ? AND day*24+hour BETWEEN ? AND ? AND prompt {op} ?",
        (wf, lo // 24, hi // 24, lo, hi, prompt)).fetchone()
    return tuple(int(x) for x in r)


def current_prompt(store, wf):
    r = store.db.execute("SELECT prompt FROM agg WHERE workflow_id=? ORDER BY day DESC, hour DESC LIMIT 1", (wf,)).fetchone()
    return r[0] if r else None


def previous_prompt(store, wf, cur):
    r = store.db.execute("SELECT prompt FROM agg WHERE workflow_id=? AND prompt!=? ORDER BY day DESC, hour DESC LIMIT 1", (wf, cur)).fetchone()
    return r[0] if r else None


def baseline(store, wf, cur, idx):
    n, k, _ = window(store, wf, cur, idx - 24 * BASE_DAYS, idx, exclude=True)
    if n >= BASE_MIN_CONN:
        return n, k, "other prompts, last 7 days"
    n, k, _ = window(store, wf, cur, idx - 24 * BASE_DAYS, idx - 24)
    if n >= BASE_MIN_CONN:
        return n, k, "same prompt, older than 24h"
    return None


def detect(store, wf, ts):
    idx = _idx(ts)
    cur = current_prompt(store, wf)
    if not cur:
        return None
    n, k, _ = window(store, wf, cur, idx - WINDOW_H + 1, idx)
    if n < MIN_CONN or k < MIN_FLAGS:
        return None
    base = baseline(store, wf, cur, idx)
    if not base:
        return None
    p0 = max(base[1] / base[0], 1e-4)
    p = binom_sf(k, n, p0)
    if k / n >= RATIO * p0 and p < P_ALERT:
        return {"prompt": cur, "connected": n, "flags": k, "rate": k / n, "baseline_rate": p0,
                "baseline_n": base[0], "baseline_basis": base[2], "p_value": p, "window_hours": WINDOW_H}
    return None


def parse_candidates(resp):
    """Tolerant parser for /llm/suggest-fixes: returns [(prompt_version, summary)]. The real response shape was not
    observable before the first live run, so we accept the plausible variants and log the raw reply to the audit trail."""
    items = resp
    if isinstance(resp, dict):
        for key in ("suggestions", "fixes", "candidates", "alternatives", "results"):
            if isinstance(resp.get(key), list):
                items = resp[key]
                break
        else:
            lists = [v for v in resp.values() if isinstance(v, list)]
            items = lists[0] if lists else ([resp] if "prompt_version" in resp else [])
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            out.append((it, ""))
        elif isinstance(it, dict):
            pv = it.get("prompt_version") or it.get("prompt") or it.get("version") or it.get("id")
            if pv:
                out.append((str(pv), str(it.get("summary") or it.get("description") or "")))
    return out


def parse_validation(resp):
    run_id = next((resp[k] for k in ("validation_run_id", "run_id", "id") if resp.get(k)), None)
    return run_id, float(resp["pass_rate"])


# ------------------------------------------------------------------ the responder
class HallucinationResponder:
    def __init__(self, sim, store, gov, workflows, log=print):
        self.sim, self.store, self.gov, self.wfs, self.log = sim, store, gov, workflows, log

    # ---- storage
    def _load(self, row):
        iid, wid, kind, status, opened, updated, data = row
        return {"incident_id": iid, "workflow_id": wid, "status": status, "opened_ts": opened, "data": json.loads(data)}

    def _open(self, wid):
        r = self.store.db.execute("SELECT * FROM incidents WHERE workflow_id=? AND status NOT IN ('resolved','failed')", (wid,)).fetchone()
        return self._load(r) if r else None

    def _cooling(self, wid, ts):
        for (u,) in self.store.db.execute("SELECT updated_ts FROM incidents WHERE workflow_id=? AND status IN ('resolved','failed')", (wid,)):
            if u and parse_ts(ts) - parse_ts(u) < __import__("datetime").timedelta(hours=COOLDOWN_H):
                return True
        return False

    def _set(self, inc, status, ts, **upd):
        inc["data"].update(upd)
        inc["status"] = status
        with self.store.tx() as db:
            db.execute("UPDATE incidents SET status=?, updated_ts=?, data=? WHERE incident_id=?",
                       (status, ts, json.dumps(inc["data"], default=str), inc["incident_id"]))
            self.store.audit("incident_" + status, inc["workflow_id"], ts, incident_id=inc["incident_id"],
                             details={k: v for k, v in upd.items()})
        return True

    def _fail(self, inc, ts, reason, **upd):
        self._set(inc, "failed", ts, reason=reason, **upd)
        self.store.audit("alert", inc["workflow_id"], ts, severity="page", incident_id=inc["incident_id"],
                         message=f"hallucination incident needs a human: {reason}")
        self.log(f"{ts} {inc['workflow_id']} INCIDENT FAILED: {reason}")
        return False

    def _spend(self, inc, ts, endpoint, cost, ref):
        with self.store.tx() as db:
            db.execute("INSERT INTO credits_log(sim_ts, workflow_id, endpoint, cost, ref) VALUES(?,?,?,?,?)",
                       (ts, inc["workflow_id"], endpoint, cost, ref))
            self.store.audit("credits_spent", inc["workflow_id"], ts, incident_id=inc["incident_id"], endpoint=endpoint, cost=cost, ref=ref)
        inc["data"]["spent"] = inc["data"].get("spent", 0) + cost

    def _budget_ok(self, inc, cost):
        left = float(self.sim.state()["credits_remaining"])
        return inc["data"].get("spent", 0) + cost <= INCIDENT_CREDIT_CAP and left - cost >= CREDIT_RESERVE

    # ---- driver
    def tick(self, ts):
        idx = _idx(ts)
        for wid, wf in self.wfs.items():
            try:
                inc = self._open(wid)
                if inc is None and not self._cooling(wid, ts):
                    det = detect(self.store, wid, ts)
                    if det:
                        inc = self._create(wid, det, ts, idx)
                for _ in range(8):                                   # run as far as we can this tick
                    if not inc or not self._step(wf, inc, ts, idx):
                        break
            except SimError as e:
                self.log(f"incident {wid}: {e}")

    def _create(self, wid, det, ts, idx):
        iid = f"hal-{wid}-{idx}"
        prev = previous_prompt(self.store, wid, det["prompt"])
        data = {"evidence": det, "cur_prompt": det["prompt"], "prev_prompt": prev, "pending": [], "validated": {},
                "llm_used": False, "spent": 0, "attempts": 0}
        with self.store.tx() as db:
            db.execute("INSERT OR IGNORE INTO incidents VALUES(?,?,?,?,?,?,?)", (iid, wid, "hallucination", "detected", ts, ts, json.dumps(data)))
            self.store.audit("incident_detected", wid, ts, incident_id=iid, evidence=det, previous_prompt=prev)
        self.log(f"{ts} {wid} hallucination spike on {det['prompt']}: {det['rate']:.1%} vs {det['baseline_rate']:.1%} (p={det['p_value']:.1e})")
        return self._open(wid)

    def _step(self, wf, inc, ts, idx):
        return {"detected": self._detected, "validating": self._validating, "suggesting": self._suggesting,
                "ready": self._ready, "proposed": self._proposed, "verifying": self._verifying}.get(inc["status"], lambda *a: False)(wf, inc, ts, idx)

    # ---- states
    def _catalog_text(self, wf, pv, fallback=""):
        return wf.get("prompt_catalog", {}).get(pv) or fallback

    def _detected(self, wf, inc, ts, idx):
        d = inc["data"]
        prev = d["prev_prompt"]
        if not prev and d["evidence"]["p_value"] > NO_PRIOR_P:
            return self._set(inc, "resolved", ts, note="no known-good prior prompt and evidence not strong enough to change a customer-facing prompt")
        ok_prev = prev and prev in wf["options"]["prompt_version"] and not RISKY.search(self._catalog_text(wf, prev))
        return self._set(inc, "validating", ts, pending=[prev] if ok_prev else [])

    def _validating(self, wf, inc, ts, idx):
        d, wid = inc["data"], inc["workflow_id"]
        thr = wf["governance"].get("min_validation_pass_rate", 0.97)
        if any(v["pass_rate"] >= thr for v in d["validated"].values()):
            return self._set(inc, "ready", ts)
        if not d["pending"]:
            return self._set(inc, "suggesting", ts) if not d["llm_used"] else self._fail(inc, ts, "no candidate prompt passed validation")
        pv, cost = d["pending"][0], math.ceil(VALIDATION_N / 100)
        if not self._budget_ok(inc, cost):
            return self._fail(inc, ts, "credit budget exhausted before a fix was validated")
        try:
            resp = self.sim._json("POST", "/validation/runs", json={"workflow_id": wid, "prompt_version": pv, "n": VALIDATION_N})
            run_id, pass_rate = parse_validation(resp)
        except (SimError, KeyError, ValueError) as e:
            return self._fail(inc, ts, f"validation call failed: {e}")
        self._spend(inc, ts, "validation", cost, f"{pv} n={VALIDATION_N}")
        d["validated"][pv] = {"run_id": run_id, "pass_rate": pass_rate, "n": VALIDATION_N}
        return self._set(inc, "validating", ts, pending=d["pending"][1:], validated=d["validated"], spent=d["spent"],
                         last_validation={"prompt": pv, "pass_rate": pass_rate, "run_id": run_id, "threshold": thr})

    def _suggesting(self, wf, inc, ts, idx):
        d, wid = inc["data"], inc["workflow_id"]
        if not self._budget_ok(inc, LLM_COST):
            return self._fail(inc, ts, "credit budget exhausted before LLM suggestions")
        ev = d["evidence"]
        problem = (f"Prompt {ev['prompt']} shows hallucination flags on {ev['rate']:.1%} of connected calls versus a {ev['baseline_rate']:.1%} "
                   f"baseline ({ev['flags']} of {ev['connected']} calls). Suggest safer prompt versions that keep customers engaged; "
                   f"do not read the compliance script verbatim.")
        resp = self.sim._json("POST", "/llm/suggest-fixes", json={"workflow_id": wid, "problem": problem})
        self._spend(inc, ts, "llm_suggest", LLM_COST, "suggest-fixes")
        raw = parse_candidates(resp)
        keep = []
        for pv, summ in raw:
            text = self._catalog_text(wf, pv, summ)
            reason = None
            if pv not in wf["options"]["prompt_version"]:
                reason = "not an allowed option"
            elif pv == ev["prompt"] or pv in d["validated"]:
                reason = "current or already tried"
            elif RISKY.search(text):
                reason = f"screened out: '{text[:60]}' (engagement or safety risk)"
            self.store.audit("llm_candidate", wid, ts, incident_id=inc["incident_id"], prompt=pv, summary=summ, kept=reason is None, reason=reason)
            if reason is None:
                keep.append(pv)
        keep = keep[:2]
        if not keep:
            return self._fail(inc, ts, "LLM produced no usable candidate", raw_response=resp)
        return self._set(inc, "validating", ts, pending=keep, llm_used=True, spent=d["spent"])

    def _ready(self, wf, inc, ts, idx):
        d, wid = inc["data"], inc["workflow_id"]
        thr = wf["governance"].get("min_validation_pass_rate", 0.97)
        passing = [(p, v) for p, v in d["validated"].items() if v["pass_rate"] >= thr]
        if not passing:
            return self._fail(inc, ts, "no validated candidate")
        chosen, val = passing[0]
        ok, why = can_request(self.store, wf["governance"], wid, ts, own_cap=wf["governance"].get("max_requests_per_24h"))
        if not ok:
            return False                                              # wait for approver hours / cap
        live = self.sim._json("GET", f"/workflows/{wid}/config")
        cfg, version = live["config"], live["version"]
        if cfg.get("prompt_version") == chosen:
            return self._set(inc, "resolved", ts, note="already on the chosen prompt")
        new_cfg = copy.deepcopy(cfg)
        new_cfg["prompt_version"] = chosen
        ev = d["evidence"]
        lo = _idx(ts) - 24 * BASE_DAYS
        cn, ck, ce = window(self.store, wid, chosen, lo, _idx(ts))
        cur_n, _, cur_e = window(self.store, wid, ev["prompt"], _idx(ts) - 24, _idx(ts))
        dry = {"method": "validation run + own measured history (no counterfactual replay is possible for a prompt)",
               "current_prompt": ev["prompt"], "current_flag_rate": round(ev["rate"], 4), "baseline_flag_rate": round(ev["baseline_rate"], 4),
               "validation": {"run_id": val["run_id"], "pass_rate": val["pass_rate"], "n": val["n"], "required": thr},
               "engagement_current_prompt_24h": round(cur_e / cur_n, 4) if cur_n else None,
               "engagement_candidate_last_7d": round(ce / cn, 4) if cn >= 300 else None,
               "caveat": "validation measures safety only; engagement of a never-run candidate is unknown"}
        impact = round(min(1.0, 0.3 + 10 * (ev["rate"] - ev["baseline_rate"])), 3)
        rationale = (f"{ev['prompt']} (set {'by a supervisor' if d['prev_prompt'] else 'earlier'}) produces hallucination flags on {ev['rate']:.1%} of calls "
                     f"versus {ev['baseline_rate']:.1%} before ({ev['flags']} of {ev['connected']} calls, p={ev['p_value']:.1e}). "
                     f"Proposed {chosen}, validated at {val['pass_rate']:.1%} (needs {thr:.0%}). This is a proposal, not an override.")
        row = self.gov.submit(wid, version, new_cfg, summary=f"Replace prompt {ev['prompt']} with {chosen} (hallucination spike)",
                              diff={"prompt_version": {"from": cfg.get("prompt_version"), "to": chosen}},
                              affected_leads=int(wf["daily_batch"]["size"]) * len(wf.get("cadence", [1])), dry_run=dry,
                              impact_score=impact, rationale=rationale, sim_ts=ts, evidence={"validation_run_id": val["run_id"]},
                              salt=f"{inc['incident_id']}-a{d['attempts']}", context={"incident_id": inc["incident_id"], "evidence": ev})
        return self._set(inc, "proposed", ts, chosen=chosen, proposal_id=row["proposal_id"])

    def _proposed(self, wf, inc, ts, idx):
        d = inc["data"]
        row = self.gov.get(d["proposal_id"])
        st = row["status"] if row else "missing"
        if st == "applied":
            a_idx = _idx(row["updated_sim_ts"]) if row.get("updated_sim_ts") else idx
            return self._set(inc, "verifying", ts, applied_idx=a_idx)
        if st in ("rejected", "expired", "lapsed", "stale", "invalid", "failed", "missing"):
            d["attempts"] += 1
            if d["attempts"] >= MAX_ATTEMPTS:
                return self._fail(inc, ts, f"proposal ended '{st}' {d['attempts']} times")
            return self._set(inc, "ready", ts, attempts=d["attempts"], last_proposal_outcome=st)    # re-propose with fresh data
        return False

    def _verifying(self, wf, inc, ts, idx):
        d, wid = inc["data"], inc["workflow_id"]
        n, k, e = window(self.store, wid, d["chosen"], d["applied_idx"], idx)
        if n < VERIFY_MIN_CONN:
            return False
        base = max(d["evidence"]["baseline_rate"], 1e-4)
        p = binom_sf(k, n, base)
        after = {"prompt": d["chosen"], "connected": n, "flags": k, "rate": k / n, "engagement": e / n, "p_vs_baseline": p}
        if k / n >= RATIO * base and p < 0.01:
            return self._fail(inc, ts, "flag rate still elevated after the fix", after=after)
        mins = (parse_ts(ts) - parse_ts(inc["opened_ts"])).total_seconds() / 60
        return self._set(inc, "resolved", ts, after=after, minutes_from_detection_to_verified=round(mins))