"""Ask the store 'what did the controller decide, and why?'
    python -m controller.audit --workflow ks_cancel [--limit 50] [--kind proposal_applied]
This is the seed of 'why did workflow X change at 14:00 on D3, who approved it, what happened next'."""
import argparse
import json
import sqlite3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="controller_state.db")
    ap.add_argument("--workflow")
    ap.add_argument("--kind")
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()
    import os, sys
    if not os.path.exists(a.db):
        sys.exit(f"no such database: {a.db}")
    db = sqlite3.connect(a.db)
    q, args = "SELECT id, sim_ts, workflow_id, kind, payload FROM audit WHERE 1=1", []
    if a.workflow:
        q += " AND workflow_id=?"; args.append(a.workflow)
    if a.kind:
        q += " AND kind=?"; args.append(a.kind)
    for i, ts, wf, kind, payload in db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, a.limit)).fetchall()[::-1]:
        p = json.loads(payload)
        brief = {k: v for k, v in p.items() if v not in (None, [], {})}
        print(f"#{i:<4} {ts or '-':<26} {wf or '-':<11} {kind:<20} {json.dumps(brief, default=str)[:300]}")
    print("\nproposals:")
    for r in db.execute("SELECT proposal_id, workflow_id, kind, status, approval_id, applied_version, detail FROM proposals"):
        print("  ", r)


if __name__ == "__main__":
    main()