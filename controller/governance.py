"""Governance client: the ONLY door through which the controller changes a live workflow.

Three ideas, all about not hurting a client when things crash:
 1. INTENT FIRST. Before any state-changing HTTP call we write the proposal to SQLite with a
    DETERMINISTIC id (hash of workflow + base version + exact config). Re-sending after a crash can
    therefore never create a second request: the sim answers 409 duplicate_proposal_id and we look
    up the existing one.
 2. RECONCILE, DON'T GUESS. If we don't know whether a call took effect (timeout, kill -9), we ask
    the sim's own records (approvals list, config history) before doing anything again.
 3. NEVER FIGHT THE POLICY. We check auto-apply limits locally, so a 403 is never logged against us.
"""
import json
import hashlib
from datetime import datetime, timedelta

from .client import SimError

AGENT_DIMS = ("welcome_message", "voice", "caller_id")
TERMINAL = {"applied", "rejected", "expired", "lapsed", "stale", "refused", "invalid", "blocked", "failed"}
COLS = ["proposal_id", "workflow_id", "kind", "base_version", "status", "approval_id", "config_json",
        "body_json", "created_sim_ts", "updated_sim_ts", "applied_version", "detail"]


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)


def has(err, token):
    return token in canon(err.body)


def make_proposal_id(workflow_id, base_version, cfg, salt=""):
    """Same change at the same base version -> same id (idempotent). Pass a different `salt`
    (e.g. the day number) to deliberately re-queue a fresh proposal after an expiry."""
    h = hashlib.sha1(canon([workflow_id, base_version, cfg, salt]).encode()).hexdigest()[:16]
    return f"ctl-{workflow_id}-v{base_version}-{h}"


# ---------------------------------------------------------------- policy maths (pure functions)
def _eff(cfg, cohort, dim):
    a = cfg.get("allocations", {})
    d = (a.get(cohort) or {}).get(dim) or (a.get("default") or {}).get(dim) or {}
    s = sum(d.values()) or 1.0
    return {k: round(v / s, 6) for k, v in d.items() if v}


def tv_distance(p, q):
    return 0.5 * sum(abs(p.get(k, 0) - q.get(k, 0)) for k in set(p) | set(q))


def _cohort_ids(old, new):
    return {"default"} | set(old.get("allocations", {})) | set(new.get("allocations", {}))


def changed_dims(old, new):
    out = set()
    if old.get("cohorts", []) != new.get("cohorts", []):
        out.add("cohorts")
    for dim in AGENT_DIMS:
        if any(_eff(old, c, dim) != _eff(new, c, dim) for c in _cohort_ids(old, new)):
            out.add(dim)
    if old.get("prompt_version") != new.get("prompt_version"):
        out.add("prompt_version")
    if old.get("schedule", {}) != new.get("schedule", {}):
        out.add("schedule")
    return sorted(out)


def max_shift(old, new):
    return max([tv_distance(_eff(old, c, d), _eff(new, c, d)) for c in _cohort_ids(old, new) for d in AGENT_DIMS] or [0])


def auto_apply_check(governance, old, new):
    """Would the sim's auto-apply accept this change? Returns (ok, reasons). Mirrors SIMULATOR.md 4.2."""
    pol = governance.get("auto_apply", {})
    reasons = []
    if not pol.get("enabled"):
        reasons.append("auto_apply disabled for this workflow")
    dims = changed_dims(old, new)
    bad = [d for d in dims if d not in pol.get("allowed_dims", [])]
    if bad:
        reasons.append(f"dims not allowed: {bad}")
    sh = max_shift(old, new)
    if sh > pol.get("max_shift", 0) + 1e-9:
        reasons.append(f"shift {sh:.3f} > max_shift {pol.get('max_shift')}")
    return (not reasons), reasons


def parse_ts(s):
    return datetime.fromisoformat(s)


def approver_on_shift(governance, sim_ts):
    h0, h1 = governance["approver_hours"]
    return h0 <= parse_ts(sim_ts).hour < h1


def requests_last_24h(store, workflow_id, sim_ts):
    t = parse_ts(sim_ts)
    rows = store.db.execute(
        "SELECT created_sim_ts FROM proposals WHERE workflow_id=? AND kind='approval' "
        "AND status NOT IN ('intent','invalid','blocked')", (workflow_id,)).fetchall()
    return sum(1 for (c,) in rows if c and parse_ts(c) > t - timedelta(hours=24))


def can_request(store, governance, workflow_id, sim_ts, own_cap=None):
    """Batching gate: approver on shift and under the daily cap (the stricter of policy and our own cap)."""
    if not approver_on_shift(governance, sim_ts):
        return False, "approver off shift"
    cap = min(governance.get("max_requests_per_24h", 99), own_cap or 99)
    n = requests_last_24h(store, workflow_id, sim_ts)
    return (n < cap), f"{n}/{cap} requests in last 24h"


# ---------------------------------------------------------------- the client
class Governance:
    def __init__(self, sim, store):
        self.sim, self.store = sim, store

    # ----- storage helpers
    def get(self, pid):
        r = self.store.db.execute(f"SELECT {','.join(COLS)} FROM proposals WHERE proposal_id=?", (pid,)).fetchone()
        return dict(zip(COLS, r)) if r else None

    def _rows(self, statuses):
        q = ",".join("?" * len(statuses))
        rs = self.store.db.execute(f"SELECT {','.join(COLS)} FROM proposals WHERE status IN ({q})", tuple(statuses)).fetchall()
        return [dict(zip(COLS, r)) for r in rs]

    def _transition(self, row, status, sim_ts, **extra):
        """Status change + audit record in ONE transaction."""
        sets = {"status": status, "updated_sim_ts": sim_ts, **{k: v for k, v in extra.items() if k in COLS}}
        with self.store.tx() as db:
            db.execute(f"UPDATE proposals SET {','.join(k + '=?' for k in sets)} WHERE proposal_id=?",
                       (*sets.values(), row["proposal_id"]))
            self.store.audit(f"proposal_{status}", row["workflow_id"], sim_ts, proposal_id=row["proposal_id"],
                             **{k: v for k, v in extra.items() if k not in ("config_json", "body_json")})
        row.update(sets)

    def _insert_intent(self, pid, wf, kind, base_version, cfg, body, sim_ts, status="intent", context=None, detail=None):
        with self.store.tx() as db:
            db.execute("INSERT OR IGNORE INTO proposals(proposal_id,workflow_id,kind,base_version,status,config_json,"
                       "body_json,created_sim_ts,updated_sim_ts,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (pid, wf, kind, base_version, status, canon(cfg), canon(body), sim_ts, sim_ts, detail))
            # the audit record carries the WHY: summary, rationale, stats context, impact, dry run
            self.store.audit(f"proposal_{status}", wf, sim_ts, proposal_id=pid, proposal_kind=kind, base_version=base_version,
                             summary=body.get("change_summary"), rationale=body.get("rationale"),
                             impact_score=body.get("impact_score"), affected_leads=body.get("affected_leads"),
                             diff=body.get("diff"), dry_run=body.get("dry_run"), evidence=body.get("evidence"),
                             context=context, detail=detail)

    # ----- approval path
    def submit(self, workflow_id, base_version, cfg, *, summary, diff, affected_leads, dry_run, impact_score,
               rationale, sim_ts, evidence=None, expires_in_hours=None, salt="", context=None):
        pid = make_proposal_id(workflow_id, base_version, cfg, salt)
        row = self.get(pid)
        if row:                                   # already known: idempotent, do nothing new
            if row["status"] == "intent":
                self._post_approval(row, sim_ts)
            return row
        body = {"proposal_id": pid, "workflow_id": workflow_id, "base_version": base_version,
                "proposed_config": cfg, "change_summary": summary, "diff": diff, "affected_leads": int(affected_leads),
                "dry_run": dry_run, "impact_score": float(impact_score), "rationale": rationale}
        if evidence:
            body["evidence"] = evidence
        if expires_in_hours:
            body["expires_in_hours"] = expires_in_hours
        self._insert_intent(pid, workflow_id, "approval", base_version, cfg, body, sim_ts, context=context)
        row = self.get(pid)
        self._post_approval(row, sim_ts)
        return row

    def _find_approval(self, workflow_id, pid):
        for a in self.sim._json("GET", "/approvals", params={"workflow_id": workflow_id}):
            if a.get("proposal_id") == pid:
                return a
        return None

    def _post_approval(self, row, sim_ts):
        try:
            r = self.sim._json("POST", "/approvals", json=json.loads(row["body_json"]))
        except SimError as e:
            if e.status >= 500:
                raise                               # unknown outcome: stay 'intent', resume() will reconcile
            if e.status == 409 and has(e, "duplicate_proposal_id"):
                a = self._find_approval(row["workflow_id"], row["proposal_id"])
                if a:
                    self._transition(row, "pending", sim_ts, approval_id=a.get("approval_id"), detail="recovered after duplicate")
                else:
                    self.store.audit("proposal_unresolved", row["workflow_id"], sim_ts, proposal_id=row["proposal_id"],
                                     problem="duplicate id but not found in /approvals")
            elif e.status == 409 and has(e, "stale_base_version"):
                self._transition(row, "stale", sim_ts, detail=canon(e.body))
            else:
                self._transition(row, "invalid", sim_ts, detail=f"{e.status}: {canon(e.body)}")
            return
        self._transition(row, "pending", sim_ts, approval_id=r.get("approval_id"),
                         detail=canon({k: r.get(k) for k in ("expires_at", "status") if k in r}))

    def poll(self, sim_ts):
        """Call every tick: follow every open approval to its end, apply approved ones immediately
        (apply windows are short: 2 hours in the public scenarios)."""
        for row in self._rows(["pending", "approved"]):
            a = self.sim._json("GET", f"/approvals/{row['approval_id']}")
            st = a.get("status")
            if st == "applied":
                self._reconcile_applied(row, sim_ts)
            elif st in ("rejected", "expired", "lapsed"):
                self._transition(row, st, sim_ts, detail=canon({"reasons": a.get("reasons")}))
            elif st == "approved":
                if row["status"] != "approved":
                    self._transition(row, "approved", sim_ts, detail=canon({"apply_by": a.get("apply_by")}))
                self.apply(row["proposal_id"], sim_ts)

    def apply(self, pid, sim_ts):
        row = self.get(pid)
        if not row or row["status"] not in ("approved", "applying"):
            return row
        if row["status"] == "approved":
            self._transition(row, "applying", sim_ts)
        try:
            r = self.sim._json("POST", f"/workflows/{row['workflow_id']}/apply", json={"approval_id": row["approval_id"]})
        except SimError as e:
            if e.status >= 500:
                raise                               # unknown outcome: stays 'applying'; resume() reconciles
            if e.status == 410 or has(e, "lapsed"):
                self._transition(row, "lapsed", sim_ts)
            elif e.status == 409 and has(e, "stale_base_version"):
                self._transition(row, "stale", sim_ts, detail="config changed after we proposed (supervisor edit?)")
            elif not self._reconcile_applied(row, sim_ts):
                self._transition(row, "failed", sim_ts, detail=f"{e.status}: {canon(e.body)}")
            return self.get(pid)
        self._transition(row, "applied", sim_ts, applied_version=r.get("version"))
        return self.get(pid)

    # ----- auto-apply path
    def auto_apply(self, workflow, old_cfg, base_version, new_cfg, *, rationale, sim_ts, evidence=None, salt="", context=None):
        """`workflow` = the /workflows/{id} dict (carries the governance policy)."""
        wf = workflow["workflow_id"]
        pid = make_proposal_id(wf, base_version, new_cfg, salt)
        row = self.get(pid)
        if row:
            return row
        body = {"proposal_id": pid, "base_version": base_version, "proposed_config": new_cfg, "rationale": rationale,
                "change_summary": rationale, "diff": changed_dims(old_cfg, new_cfg)}
        if evidence:
            body["evidence"] = evidence
        ok, reasons = auto_apply_check(workflow["governance"], old_cfg, new_cfg)
        if not ok:
            self._insert_intent(pid, wf, "auto", base_version, new_cfg, body, sim_ts, status="blocked",
                                context=context, detail="; ".join(reasons))
            return self.get(pid)
        self._insert_intent(pid, wf, "auto", base_version, new_cfg, body, sim_ts, context=context)
        row = self.get(pid)
        self._put_auto(row, sim_ts)
        return self.get(pid)

    def _put_auto(self, row, sim_ts):
        body = {k: v for k, v in json.loads(row["body_json"]).items() if k in ("proposal_id", "base_version", "proposed_config", "rationale", "evidence")}
        try:
            r = self.sim._json("PUT", f"/workflows/{row['workflow_id']}/config", json=body)
        except SimError as e:
            if e.status >= 500:
                raise
            if e.status == 403:
                self._transition(row, "refused", sim_ts, detail=canon(e.body))
            elif e.status == 409 and has(e, "stale_base_version"):
                if not self._reconcile_applied(row, sim_ts):
                    self._transition(row, "stale", sim_ts)
            elif not self._reconcile_applied(row, sim_ts):
                self._transition(row, "invalid", sim_ts, detail=f"{e.status}: {canon(e.body)}")
            return
        self._transition(row, "applied", sim_ts, applied_version=r.get("version"))

    # ----- reconciliation
    def _history_hit(self, row):
        key, val = ("approval_id", row["approval_id"]) if row["kind"] == "approval" else ("proposal_id", row["proposal_id"])
        if not val:
            return None
        for h in self.sim._json("GET", f"/workflows/{row['workflow_id']}/config/history"):
            if h.get(key) == val:
                return h
        return None

    def _reconcile_applied(self, row, sim_ts):
        h = self._history_hit(row)
        if h:
            self._transition(row, "applied", sim_ts, applied_version=h.get("version"), detail="confirmed in config history")
            return True
        return False

    def resume(self, sim_ts):
        """Run once at startup (and after any outage). Resolves every proposal whose outcome we don't know."""
        for row in self._rows(["intent"]):
            if row["kind"] == "approval":
                self._post_approval(row, sim_ts)
            elif not self._reconcile_applied(row, sim_ts):
                self._put_auto(row, sim_ts)
        for row in self._rows(["applying"]):
            if not self._reconcile_applied(row, sim_ts):
                self._transition(row, "approved", sim_ts, detail="apply outcome unknown; back to approved to retry")
                self.apply(row["proposal_id"], sim_ts)