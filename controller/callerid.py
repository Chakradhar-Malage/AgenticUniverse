"""Caller-ID health (capability C5).

WHAT IT DOES
  Every tick, for every workflow with 2+ caller IDs ("DIDs"):
   1. Compare each DID's recent pickup rate to its PEERS' in the same workflow. Because the sim draws the
      DID independently for every dial, DIDs get the same mix of people and hours, so the comparison is fair.
   2. A DID is flagged BAD only if it is significantly AND materially worse (p < 1e-4 and >= 20% relative
      drop) on two consecutive ticks. Thresholds, never single calls.
   3. Bad DIDs are cut to a small floor share (so we can notice recovery); the rest is spread evenly,
      which also gives never-tried DIDs a fair trial instead of leaving numbers unused.
   4. The change goes through governance: auto-apply where the policy allows caller_id, otherwise ONE
      batched approval request per day.

WHAT IT DELIBERATELY DOES NOT DO
  * It never touches cohorts, voice, welcome message, prompt or schedule: only default.caller_id.
  * It never reverts a supervisor edit: it always reads the live config first and changes one field.
  * It does not use any hidden scenario knowledge; it only sees pickup counts from events.
"""
from datetime import timedelta

from .client import SimError
from .governance import can_request, parse_ts, tv_distance
from .ingest import day_hour
from .stats import two_prop

MIN_N = 150            # dials a DID needs in the window before we judge it
P_BAD = 1e-4           # one-sided p-value needed to call a DID worse than its peers
REL_GAP = 0.20         # ...and the pickup drop must be at least 20% relative (materiality)
STRIKES = 2            # consecutive ticks flagged before acting
FLOOR = 0.03           # traffic share kept on a bad DID so recovery is noticed
WINDOW_H = 2           # recent window for detection (short: fresh evidence is not diluted by healthy hours)
RECOVER_H = 24         # longer window for judging recovery (bad DIDs get little traffic)
RECOVER_MIN_N = 150
COOLDOWN_MIN = 60      # minimum minutes between EXPLORATORY rebalances (bypassed while any number is bad)
MIN_TV = 0.05          # ignore rebalances smaller than this


# ------------------------------------------------------------------ pure functions (unit-tested)
def did_window(store, wf, now_idx, hours):
    """{did: (dials, connected)} over the last `hours` hours (inclusive of the current partial hour)."""
    now_day = now_idx // 24
    rows = store.db.execute(
        "SELECT caller_id, SUM(dials), SUM(connected) FROM agg WHERE workflow_id=? AND day BETWEEN ? AND ? "
        "AND day*24+hour BETWEEN ? AND ? GROUP BY caller_id",
        (wf, now_day - 2, now_day, now_idx - hours + 1, now_idx)).fetchall()
    return {d: (n or 0, k or 0) for d, n, k in rows}


def _pool(stats, dids, min_n):
    n = k = 0
    for d in dids:
        dn, dk = stats.get(d, (0, 0))
        if dn >= min_n:
            n += dn
            k += dk
    return n, k


def assess(options, stats, recov, prev):
    """Update each DID's status from fresh window stats.
    prev: {did: (status, strikes)}. Returns (new {did: (status, strikes)}, evidence list)."""
    out, ev = {}, []
    for d in options:
        st, strikes = prev.get(d, ("unknown", 0))
        peers = [x for x in options if x != d and prev.get(x, ("unknown", 0))[0] != "bad"]
        if st == "bad":                                           # can it come back?
            rn, rk = recov.get(d, (0, 0))
            pn, pk = _pool(recov, peers, MIN_N)
            if rn >= RECOVER_MIN_N and pn >= MIN_N and pk:
                t = two_prop(rk, rn, pk, pn)
                gap = 1 - (rk / rn) / (pk / pn)
                if t["p_less"] > 0.05 and gap < 0.10:
                    st, strikes = "good", 0
                    ev.append({"did": d, "event": "recovered", "n": rn, "pickup": rk / rn, "peer_pickup": pk / pn})
            out[d] = (st, strikes)
            continue
        n, k = stats.get(d, (0, 0))
        pn, pk = _pool(stats, peers, MIN_N)
        if n < MIN_N or pn < MIN_N or not pk:                     # not enough evidence either way
            out[d] = (st, strikes)
            continue
        t = two_prop(k, n, pk, pn)
        gap = 1 - (k / n) / (pk / pn)
        flagged = t["p_less"] < P_BAD and gap >= REL_GAP
        if flagged:
            strikes += 1
            if strikes >= STRIKES:
                st = "bad"
            ev.append({"did": d, "event": "bad" if st == "bad" else "strike", "n": n, "pickup": k / n,
                       "peer_pickup": pk / pn, "p_less": t["p_less"], "rel_gap": gap, "strikes": strikes})
        else:
            strikes = 0
            if st == "unknown":
                st = "good"
        out[d] = (st, strikes)
    return out, ev


def target_dist(options, status, floor=FLOOR):
    """Bad DIDs keep a small floor; everything else (good + not-yet-judged) shares the rest evenly."""
    bad = [d for d in options if status.get(d, ("unknown", 0))[0] == "bad"]
    ok = [d for d in options if d not in bad]
    if not ok:
        return None
    w = {d: floor for d in bad}
    w.update({d: (1 - floor * len(bad)) / len(ok) for d in ok})
    return w


def step_toward(cur, tgt, max_tv):
    """Move from cur toward tgt but never further than max_tv in total-variation distance."""
    tv = tv_distance(cur, tgt)
    alpha = 1.0 if tv <= max_tv or tv == 0 else max_tv / tv
    keys = set(cur) | set(tgt)
    new = {k: cur.get(k, 0) + alpha * (tgt.get(k, 0) - cur.get(k, 0)) for k in keys}
    new = {k: round(v, 4) for k, v in new.items() if v > 1e-4}
    s = sum(new.values())
    return {k: round(v / s, 4) for k, v in new.items()}


def normalise(d):
    s = sum(d.values()) or 1.0
    return {k: v / s for k, v in d.items() if v}


# ------------------------------------------------------------------ the manager
class CallerIdManager:
    def __init__(self, sim, store, gov, workflows, log=print):
        self.sim, self.store, self.gov, self.wfs, self.log = sim, store, gov, workflows, log

    # state ---------------------------------------------------------------------------------
    def _prev(self, wid):
        rows = self.store.db.execute("SELECT caller_id, status, strikes FROM did_state WHERE workflow_id=?", (wid,)).fetchall()
        return {d: (s, k) for d, s, k in rows}

    def _save(self, wid, new, prev, ts):
        with self.store.tx() as db:
            for d, (st, k) in new.items():
                if prev.get(d) != (st, k):
                    db.execute("INSERT INTO did_state VALUES(?,?,?,?,?) ON CONFLICT(workflow_id,caller_id) DO UPDATE SET "
                               "status=excluded.status, strikes=excluded.strikes, since_ts=CASE WHEN status<>excluded.status "
                               "THEN excluded.since_ts ELSE since_ts END", (wid, d, st, k, ts))

    def _cooldown_ok(self, wid, ts):
        last = self.store.meta_get(f"cid_last_{wid}")
        return last is None or parse_ts(ts) - parse_ts(last) >= timedelta(minutes=COOLDOWN_MIN)

    # main entry ----------------------------------------------------------------------------
    def tick(self, sim_ts):
        day, hour = day_hour(sim_ts)
        idx = day * 24 + hour
        for wid, wf in self.wfs.items():
            opts = wf["options"]["caller_id"]
            if len(opts) < 2:
                continue
            try:
                self._tick_wf(wid, wf, opts, sim_ts, idx)
            except SimError as e:
                self.log(f"caller-id {wid}: {e}")

    def _tick_wf(self, wid, wf, opts, ts, idx):
        prev = self._prev(wid)
        stats = did_window(self.store, wid, idx, WINDOW_H)
        recov = did_window(self.store, wid, idx, RECOVER_H)
        new, events = assess(opts, stats, recov, prev)
        newly_bad = [e for e in events if e["event"] == "bad"]
        for e in events:
            self.store.audit("did_" + e["event"], wid, ts, **e)
        self._save(wid, new, prev, ts)
        tgt = target_dist(opts, new)
        if tgt is None:
            return
        live = self.sim._json("GET", f"/workflows/{wid}/config")
        cfg, version = live["config"], live["version"]
        cur = normalise(cfg.get("allocations", {}).get("default", {}).get("caller_id", {}))
        if tv_distance(cur, tgt) < MIN_TV:
            return
        any_bad = any(st == "bad" for st, _ in new.values())
        if not any_bad and not self._cooldown_ok(wid, ts):     # protective steps run every tick until done
            return
        pol = wf["governance"]["auto_apply"]
        auto_ok = pol.get("enabled") and "caller_id" in pol.get("allowed_dims", [])
        status_view = {d: s for d, (s, _) in new.items()}
        ctx = {"window_hours": WINDOW_H, "window_stats": {d: list(v) for d, v in stats.items()},
               "status": status_view, "evidence": events, "current": cur, "target": tgt}
        if auto_ok:
            nxt = step_toward(cur, tgt, pol.get("max_shift", 0) * 0.9)
            new_cfg = _with_caller_id(cfg, nxt)
            why = _rationale(events, status_view, cur, nxt)
            row = self.gov.auto_apply(wf, cfg, version, new_cfg, rationale=why, sim_ts=ts, context=ctx)
            self.store.meta_set(f"cid_last_{wid}", ts)
            self.log(f"{ts} {wid} caller-id auto-apply -> {row['status']} {nxt}")
        else:
            self._propose(wid, wf, cfg, version, cur, tgt, new, events, ctx, ts, idx)

    # approval path (workflows whose policy forbids auto-applying caller_id) ----------------
    def _propose(self, wid, wf, cfg, version, cur, tgt, status, events, ctx, ts, idx):
        open_ = self.store.db.execute(
            "SELECT 1 FROM proposals WHERE workflow_id=? AND kind='approval' AND status IN "
            "('intent','pending','approved','applying') LIMIT 1", (wid,)).fetchone()
        if open_:
            return
        ok, _ = can_request(self.store, wf["governance"], wid, ts, own_cap=1)    # <= 1 request / 24h: batching
        if not ok:
            return
        new_cfg = _with_caller_id(cfg, {d: round(v, 4) for d, v in tgt.items()})
        hist = _history_summary(self.store, wid, idx // 24)
        evidence_based = any(e["event"] == "bad" for e in events)
        tv = tv_distance(cur, tgt)
        # impact score: evidence-based removal of a bad number can be high; an untested rebalance is capped low.
        impact = min(1.0, 0.4 + 0.6 * max(e.get("rel_gap", 0) for e in events if e["event"] == "bad")) if evidence_based \
            else round(min(0.25, 0.5 * tv), 3)
        diff = {"allocations.default.caller_id": {"from": {k: round(v, 3) for k, v in cur.items()},
                                                  "to": {k: round(v, 3) for k, v in tgt.items()}}}
        dry = {"method": "history summary (no replay possible for numbers with no history)",
               "history": hist, "bad_numbers": [d for d, (s, _) in status.items() if s == "bad"],
               "expected_pickup_change": "measured effect available only for removing flagged numbers; "
                                         "rebalance onto untried numbers is an exploration with bounded downside",
               "monitoring": "per-number pickup re-checked every 15 min; flagged numbers cut to a 3% floor"}
        why = _rationale(events, {d: s for d, (s, _) in status.items()}, cur, tgt) + \
            " Caller-ID is an operational setting, not something the customer hears."
        row = self.gov.submit(wid, version, new_cfg, summary=f"Rebalance caller-ID traffic ({', '.join(sorted(tgt))})",
                              diff=diff, affected_leads=int(wf["daily_batch"]["size"]), dry_run=dry,
                              impact_score=impact, rationale=why, sim_ts=ts, salt=f"d{idx // 24}", context=ctx)
        self.log(f"{ts} {wid} caller-id approval request -> {row['status']}")


def _with_caller_id(cfg, dist):
    import copy
    c = copy.deepcopy(cfg)
    c.setdefault("allocations", {}).setdefault("default", {})["caller_id"] = dist
    return c


def _rationale(events, status, cur, nxt):
    bad = [e for e in events if e["event"] == "bad"]
    if bad:
        b = bad[0]
        head = (f"Caller ID {b['did']} picked up {b['pickup']:.1%} vs {b['peer_pickup']:.1%} for its peers "
                f"(n={b['n']} dials, p={b['p_less']:.1e}, {b['strikes']} consecutive checks).")
    elif any(s == "bad" for s in status.values()):
        head = "Continuing to keep flagged caller IDs at the floor share."
    else:
        head = "No caller ID is flagged; spreading load evenly so every number gets a fair trial."
    return head + f" Weights {_fmt(cur)} -> {_fmt(nxt)}."


def _fmt(d):
    return "{" + ", ".join(f"{k}:{v:.2f}" for k, v in sorted(d.items())) + "}"


def _history_summary(store, wf, today):
    rows = store.db.execute("SELECT day, SUM(dials), SUM(connected) FROM agg WHERE workflow_id=? AND day<? "
                            "GROUP BY day ORDER BY day", (wf, today)).fetchall()
    per = store.db.execute("SELECT caller_id, SUM(dials), SUM(connected) FROM agg WHERE workflow_id=? AND day<? "
                           "GROUP BY caller_id", (wf, today)).fetchall()
    def rate(rs):
        n = sum(r[1] for r in rs)
        return round(sum(r[2] for r in rs) / n, 4) if n else None
    return {"days": len(rows), "pickup_first3d": rate(rows[:3]), "pickup_last3d": rate(rows[-3:]),
            "dials_per_number": {d: n for d, n, _ in per}}