"""Quick look at what the controller knows: python -m controller.status [--db controller_state.db]
Per-workflow, per-day pickup and engagement (kept SEPARATE, never blended)."""
import argparse
import sqlite3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="controller_state.db")
    db = sqlite3.connect(ap.parse_args().db)
    rows = db.execute("""SELECT workflow_id, day, SUM(dials), SUM(connected), SUM(engaged), SUM(halluc)
                         FROM agg GROUP BY workflow_id, day ORDER BY workflow_id, day""").fetchall()
    print(f"{'workflow':<12}{'day':>4}{'dials':>8}{'pickup':>9}{'engage':>9}{'halluc/1k':>11}")
    for wf, day, d, c, e, h in rows:
        print(f"{wf:<12}{day:>4}{d:>8}{c / d:>9.3f}{(e / c if c else 0):>9.3f}{(1000 * h / c if c else 0):>11.1f}")
    print("chunks ingested:", db.execute("SELECT COUNT(*) FROM chunks_done").fetchone()[0])


if __name__ == "__main__":
    main()