"""Post-run proof that a crash/restart lost nothing and duplicated nothing.
    python -m controller.verify --url http://127.0.0.1:8700 --db controller_state.db
Checks our own store against the simulator's records (HTTP only):
  1. SQLite integrity.
  2. EVERY event counted exactly once: sum of events in our done-chunks == the sim's last_event_id.
  3. For every workflow: the controller-made versions in the sim's config history match our applied proposals
     one-to-one: none lost, none applied twice.
  4. No proposal left half-done (intent / applying).
  5. How many times the controller started (evidence that a restart really happened)."""
import argparse
import os
import sqlite3
import sys

from .client import Sim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8700")
    ap.add_argument("--db", default="controller_state.db")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit(f"no such database: {a.db}")
    sim, db = Sim(a.url), sqlite3.connect(a.db)
    ok_all = True

    def check(name, ok, detail=""):
        nonlocal ok_all
        ok_all &= bool(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    check("sqlite integrity_check", db.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    counted = db.execute("SELECT COALESCE(SUM(n_events),0) FROM chunks_done").fetchone()[0]
    last = sim.state()["last_event_id"] # type: ignore
    check("every event counted exactly once", counted == last, f"counted {counted}, simulator emitted {last}")
    for wf in sim.workflows():
        wid = wf["workflow_id"] # type: ignore
        hist = [h for h in sim._json("GET", f"/workflows/{wid}/config/history") if str(h.get("actor", "")).startswith("controller")]  #type: ignore
        keys = [h.get("approval_id") or h.get("proposal_id") for h in hist]  #type: ignore
        mine = db.execute("SELECT kind, proposal_id, approval_id FROM proposals WHERE workflow_id=? AND status='applied'", (wid,)).fetchall()
        mine_keys = [(approval if kind == "approval" else pid) for kind, pid, approval in mine]
        check(f"{wid}: applied changes match the simulator's history one-to-one",
              len(keys) == len(set(keys)) and sorted(map(str, keys)) == sorted(map(str, mine_keys)),
              f"simulator {len(keys)} controller versions, store {len(mine_keys)} applied proposals, duplicates {len(keys) - len(set(keys))}")
    stuck = db.execute("SELECT COUNT(*) FROM proposals WHERE status IN ('intent','applying')").fetchone()[0]
    check("no proposal left half-done", stuck == 0, f"{stuck} stuck")
    starts = db.execute("SELECT COUNT(*), SUM(json_extract(payload,'$.resumed')) FROM audit WHERE kind='controller_started'").fetchone()
    print(f"[INFO] controller started {starts[0]} time(s), {int(starts[1] or 0)} of them resumed from saved state")
    print("\nRESULT:", "ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()