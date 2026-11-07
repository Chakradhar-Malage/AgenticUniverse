"""Look at the HISTORY (day < 0) the way an analyst would, using only data the controller ingested.
    python -m controller.explore [--db controller_state.db] [--workflow nw_renewal]

CAUTION for reading it: history ran ONE default config, so differences here are differences between
kinds of people and times of day, NOT proof that a variant works. Variant effects must be measured in
randomised experiments (later step)."""
import argparse
import sqlite3

from .stats import wilson


def table(db, wf, dim, where="day<0", rate="pickup"):
    num, den = ("SUM(connected)", "SUM(dials)") if rate == "pickup" else ("SUM(engaged)", "SUM(connected)")
    rows = db.execute(f"SELECT {dim}, {num}, {den} FROM agg WHERE workflow_id=? AND {where} "
                      f"GROUP BY {dim} ORDER BY {dim}", (wf,)).fetchall()
    out = []
    for key, k, n in rows:
        if n:
            lo, hi = wilson(k, n)
            out.append(f"   {str(key):<8} n={n:<7} {rate}={k / n:.3f}  [{lo:.3f},{hi:.3f}]")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="controller_state.db")
    ap.add_argument("--workflow")
    a = ap.parse_args()
    db = sqlite3.connect(a.db)
    wfs = [a.workflow] if a.workflow else [r[0] for r in db.execute("SELECT DISTINCT workflow_id FROM agg ORDER BY 1")]
    for wf in wfs:
        print(f"\n================ {wf} (history) ================")
        for dim in ("caller_id", "hour", "age_band", "lang", "city_tier"):
            print(f"\n-- pickup by {dim}\n" + table(db, wf, dim))
        for dim in ("age_band", "lang", "city_tier"):
            print(f"\n-- engagement by {dim}\n" + table(db, wf, dim, rate="engage"))
        print("\n-- pickup by day, per caller_id (does a number get 'burned' as it is used?)")
        dids = [r[0] for r in db.execute("SELECT DISTINCT caller_id FROM agg WHERE workflow_id=? AND day<0", (wf,))]
        days = sorted(r[0] for r in db.execute("SELECT DISTINCT day FROM agg WHERE workflow_id=? AND day<0", (wf,)))
        print("   day  " + "  ".join(f"{d:>7}" for d in sorted(dids)))
        for day in days:
            cells = []
            for d in sorted(dids):
                r = db.execute("SELECT SUM(connected), SUM(dials) FROM agg WHERE workflow_id=? AND day=? AND caller_id=?",
                               (wf, day, d)).fetchone()
                cells.append(f"{r[0] / r[1]:>7.3f}" if r[1] else f"{'-':>7}")
            print(f"   {day:>3}  " + "  ".join(cells))


if __name__ == "__main__":
    main()