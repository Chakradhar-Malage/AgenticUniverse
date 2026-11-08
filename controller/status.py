"""Quick look at what the controller knows.
    python -m controller.status [--db controller_state.db]          per-workflow, per-day pickup/engagement (kept SEPARATE)
    python -m controller.status --loop                               the loop's own health: throughput, lag, memory, credits"""
import argparse
import os
import sqlite3
import sys

from .metrics import summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="controller_state.db")
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit(f"no such database: {a.db}")
    db = sqlite3.connect(a.db)
    if a.loop:
        for k, v in summary(db).items():
            print(f"{k:<32} {v}")
        return
    rows = db.execute("""SELECT workflow_id, day, SUM(dials), SUM(connected), SUM(engaged), SUM(halluc)
                         FROM agg GROUP BY workflow_id, day ORDER BY workflow_id, day""").fetchall()
    print(f"{'workflow':<12}{'day':>4}{'dials':>8}{'pickup':>9}{'engage':>9}{'halluc/1k':>11}")
    for wf, day, d, c, e, h in rows:
        print(f"{wf:<12}{day:>4}{d:>8}{c / d:>9.3f}{(e / c if c else 0):>9.3f}{(1000 * h / c if c else 0):>11.1f}")
    print("chunks ingested:", db.execute("SELECT COUNT(*) FROM chunks_done").fetchone()[0])


if __name__ == "__main__":
    main()