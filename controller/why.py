"""Explain one change from OUR OWN store (never the simulator's logs).

    python -m controller.why --workflow ks_cancel --at 2027-01-07T14:45:00+05:30
    python -m controller.why --workflow ks_cancel                      (the latest applied change)

Answers: WHY it changed (inputs and numbers), WHO approved it, and WHAT HAPPENED NEXT."""
import argparse
import json
import os
import sqlite3
import sys

from .governance import parse_ts
from .ingest import day_hour


def _idx(ts):
    d, h = day_hour(ts)
    return d * 24 + h


def _kpi(db, wf, lo, hi):
    n, c, e, h = db.execute(
        "SELECT COALESCE(SUM(dials),0), COALESCE(SUM(connected),0), COALESCE(SUM(engaged),0), COALESCE(SUM(halluc),0) FROM agg "
        "WHERE workflow_id=? AND day BETWEEN ? AND ? AND day*24+hour BETWEEN ? AND ?", (wf, lo // 24, hi // 24, lo, hi)).fetchone()
    if not n:
        return "no calls in this window"
    return (f"pickup {c / n:.3f} ({c}/{n} dials) | engagement {e / c if c else 0:.3f} ({e}/{c} connected) | "
            f"hallucination flags {1000 * h / c if c else 0:.1f} per 1,000 connected")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="controller_state.db")
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--at", help="simulated timestamp; picks the latest applied change at or before it")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit(f"no such database: {a.db}")
    db = sqlite3.connect(a.db)
    rows = db.execute("SELECT proposal_id, kind, approval_id, applied_version, updated_sim_ts FROM proposals "
                      "WHERE workflow_id=? AND status='applied' ORDER BY updated_sim_ts", (a.workflow,)).fetchall()
    if not rows:
        sys.exit(f"no applied changes recorded for {a.workflow}")
    pick = rows[-1]
    if a.at:
        t = parse_ts(a.at)
        before = [r for r in rows if parse_ts(r[4]) <= t]
        pick = before[-1] if before else min(rows, key=lambda r: abs(parse_ts(r[4]) - t))
    pid, kind, approval_id, version, applied_ts = pick
    print(f"WORKFLOW {a.workflow}: config version {version} was applied at {applied_ts}")
    print(f"  route: {'approval path (a named human approver decided)' if kind == 'approval' else 'auto-apply (no human; permitted by the workflow policy)'}")

    print("\n1. WHY (the controller's recorded reasoning at decision time)")
    first = db.execute("SELECT payload FROM audit WHERE json_extract(payload,'$.proposal_id')=? AND kind IN "
                       "('proposal_intent','proposal_blocked') ORDER BY id LIMIT 1", (pid,)).fetchone()
    if first:
        p = json.loads(first[0])
        for k in ("summary", "rationale", "impact_score", "affected_leads", "diff", "dry_run", "evidence"):
            if p.get(k) not in (None, [], {}):
                print(f"   {k}: {json.dumps(p[k], default=str)}")
        ctx = p.get("context")
        if ctx:
            print(f"   statistical context: {json.dumps(ctx, default=str)[:600]}")
    inc = db.execute("SELECT incident_id, opened_ts, data FROM incidents WHERE json_extract(data,'$.proposal_id')=?", (pid,)).fetchone()
    if inc:
        d = json.loads(inc[2])
        ev = d.get("evidence", {})
        print(f"   incident {inc[0]} opened {inc[1]}: prompt {ev.get('prompt')} flagged on {ev.get('rate', 0):.1%} of calls vs "
              f"{ev.get('baseline_rate', 0):.1%} baseline (p={ev.get('p_value', 0):.1e}); credits spent {d.get('spent')}")

    print("\n2. WHO / WHEN (decision trail)")
    for ts, k, payload in db.execute("SELECT sim_ts, kind, payload FROM audit WHERE json_extract(payload,'$.proposal_id')=? ORDER BY id", (pid,)):
        p = json.loads(payload)
        extra = p.get("approval_id") or p.get("detail") or p.get("applied_version") or ""
        print(f"   {ts}  {k:<20} {extra}")
    if approval_id:
        print(f"   approval id: {approval_id}")

    print("\n3. WHAT HAPPENED NEXT")
    i = _idx(applied_ts)
    print(f"   6 hours before: {_kpi(db, a.workflow, i - 6, i - 1)}")
    print(f"   6 hours after : {_kpi(db, a.workflow, i + 1, i + 6)}")
    if inc:
        res = json.loads(inc[2]).get("after")
        if res:
            print(f"   verification: after the fix, flag rate {res['rate']:.1%} on {res['connected']} calls, engagement {res['engagement']:.1%}")
    nxt = db.execute("SELECT sim_ts, kind FROM audit WHERE workflow_id=? AND sim_ts>? AND kind NOT LIKE 'credits%' ORDER BY id LIMIT 5", (a.workflow, applied_ts)).fetchall()
    for ts, k in nxt:
        print(f"   next decision: {ts}  {k}")
    sup = db.execute("SELECT ts, version, changed_dims FROM config_log WHERE workflow_id=? AND actor='supervisor' ORDER BY event_id", (a.workflow,)).fetchall()
    if sup:
        print("\n4. SUPERVISOR EDITS SEEN ON THIS WORKFLOW (we never overwrite or revert them silently)")
        for ts, v, dims in sup:
            print(f"   {ts}  version {v}  changed {dims}")


if __name__ == "__main__":
    main()