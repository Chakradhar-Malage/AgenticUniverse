"""SCRATCH-WORLD smoke test of the governance client against the REAL simulator.
Use a fresh sim (new --run-dir) and a throwaway DB. It makes two deliberately harmless proposals so we
can see the real response shapes:
   1. approval path on ks_cancel   (small voice split change)
   2. auto-apply path on nw_renewal (small caller-id split change, within policy)
    python -m controller.govsmoke --url http://127.0.0.1:8700 --db smoke.db
Then:  python -m controller.audit --db smoke.db
"""
import argparse
import copy

from .client import Sim
from .governance import Governance
from .store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8700")
    ap.add_argument("--db", default="smoke.db")
    a = ap.parse_args()
    sim, store = Sim(a.url), Store(a.db)
    gov = Governance(sim, store)
    sim.advance(60 * 10 + 15)                         # D0 10:15, approvers are on shift
    ts = sim.state()["ts"] # type: ignore
    gov.resume(ts)

    # 1. approval path
    wf = sim._json("GET", "/workflows/ks_cancel")
    cur = sim._json("GET", "/workflows/ks_cancel/config")
    cfg = copy.deepcopy(cur["config"]) # type: ignore
    cfg["allocations"]["default"]["voice"] = {"V1": 0.9, "V2": 0.1} # type: ignore
    row = gov.submit("ks_cancel", cur["version"], cfg, summary="SMOKE: shift 10% of voice to V2", # type: ignore
                     diff={"allocations.default.voice": cfg["allocations"]["default"]["voice"]}, affected_leads=1500, # type: ignore
                     dry_run={"note": "smoke test, no replay"}, impact_score=0.1,
                     rationale="smoke test of the approval round-trip", sim_ts=ts)
    print("submitted:", row["status"], row["approval_id"]) # type: ignore

    # 2. auto path
    wf2 = sim._json("GET", "/workflows/nw_renewal")
    cur2 = sim._json("GET", "/workflows/nw_renewal/config")
    cfg2 = copy.deepcopy(cur2["config"]) # type: ignore
    cfg2["allocations"]["default"]["caller_id"] = {"D1": 0.45, "D2": 0.45, "D3": 0.1} # type: ignore
    r2 = gov.auto_apply(wf2, cur2["config"], cur2["version"], cfg2, rationale="SMOKE: add 10% of D3 to explore", sim_ts=ts) # type: ignore
    print("auto-apply:", r2["status"], r2["detail"]) # type: ignore

    for _ in range(4 * 24 * 3):                       # up to 3 simulated days
        sim.advance(15)
        ts = sim.state()["ts"] # type: ignore
        gov.poll(ts)
        row = gov.get(row["proposal_id"]) # type: ignore
        if row["status"] in ("applied", "rejected", "expired", "lapsed", "stale", "failed", "invalid"): # type: ignore
            break
    print("final:", ts, row["status"], row["applied_version"], row["detail"]) # type: ignore


if __name__ == "__main__":
    main()