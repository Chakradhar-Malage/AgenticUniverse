"""Controller main loop. Every tick: observe -> govern -> detect/decide -> let the world move -> observe."""
import argparse
import hashlib
import os
import sys
import time

from .callerid import CallerIdManager
from .client import Sim, SimError
from .governance import Governance
from .incidents import HallucinationResponder
from .ingest import Ingestor, pull_new_chunks
from .metrics import record_tick, rss_mb, summary
from .store import Store


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def open_store_for_this_world(sim, path):
    """A leftover DB from a DIFFERENT simulated world would poison our counters.
    We fingerprint the world by hashing its first history chunk; mismatch -> archive and start fresh.
    Same world after a crash -> fingerprint matches -> we resume."""
    chunks = sim.list_chunks("history")
    fp = hashlib.sha1(sim.get_chunk(chunks[0]["chunk"]).encode()).hexdigest() if chunks else "no-history"
    store = Store(path)
    old = store.meta_get("world_fp")
    if old and old != fp:
        store.db.close()
        os.rename(path, f"{path}.old.{int(time.time())}")
        log("different world detected -> archived old DB, starting fresh")
        store = Store(path)
        old = None
    if not old:
        store.meta_set("world_fp", fp)
    return store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--db", default="controller_state.db")
    ap.add_argument("--tick", type=int, default=int(os.environ.get("TICK_MINUTES", 15)))
    a = ap.parse_args()

    sim = Sim(a.url)
    store = open_store_for_this_world(sim, a.db)
    ing = Ingestor(store)
    gov = Governance(sim, store)
    wfs = {w["workflow_id"]: w for w in sim.workflows()} # type: ignore
    cid = CallerIdManager(sim, store, gov, wfs, log=log)
    hal = HallucinationResponder(sim, store, gov, wfs, log=log)
    st = sim.state()
    resumed = store.max_chunk("events") >= 0 or store.max_chunk("history") >= 0
    store.audit("controller_started", sim_ts=st.get("ts"), credits=st.get("credits_remaining"), resumed=resumed) # type: ignore
    log("started at sim ts", st.get("ts"), "| resumed:", resumed) # type: ignore
    try:
        gov.resume(st.get("ts"))    # type: ignore             # settle anything a previous crash left half-done
    except SimError as e:
        log("resume failed (will retry next tick):", e)
    n = pull_new_chunks(sim, ing, "history")
    log(f"history ingested: {n} new events")

    while True:
        st = sim.state()
        if st.get("finished"): # type: ignore
            break
        ts = st.get("ts") # type: ignore
        t0 = time.perf_counter()
        n1 = pull_new_chunks(sim, ing, "events")  # 1. observe everything that already happened
        t1 = time.perf_counter()
        for name, fn in (("governance poll", gov.poll), ("caller-id", cid.tick), ("incidents", hal.tick)):
            try:
                fn(ts)                           # 2. follow approvals, detect, decide
            except SimError as e:
                log(f"{name} failed (will retry next tick):", e)
        t2 = time.perf_counter()
        r = sim.advance(a.tick)                  # 3. let the world move
        t3 = time.perf_counter()
        if r.get("finished"):
            break
        n2 = pull_new_chunks(sim, ing, "events")  # 4. observe what just happened
        t4 = time.perf_counter()
        store.meta_set("last_sim_ts", r.get("ts"))
        record_tick(store, r.get("ts"), n1 + n2, (t1 - t0) + (t4 - t3), t2 - t1, t3 - t2,
                    (t4 - t3) + (t2 - t1), rss_mb(), st.get("credits_remaining")) # type: ignore

    pull_new_chunks(sim, ing, "events")
    store.audit("controller_finished", sim_ts=sim.state().get("ts")) # type: ignore
    log("scenario finished. credits:", sim.state().get("credits_remaining"))  # type: ignore
    for k, v in summary(store.db).items():
        log(f"  {k}: {v}")


if __name__ == "__main__":
    main()