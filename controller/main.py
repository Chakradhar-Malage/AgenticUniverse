"""Controller main loop (STEP 1: observe only - it ingests and journals, it changes nothing yet)."""
import argparse
import hashlib
import os
import sys
import time

from .client import Sim, SimError
from .governance import Governance
from .ingest import Ingestor, pull_new_chunks
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
    st = sim.state()
    store.audit("controller_started", sim_ts=st.get("ts"), credits=st.get("credits_remaining"), # type: ignore
                resumed=store.max_chunk("events") >= 0)
    log("started at sim ts", st.get("ts"), "| resumed:", store.max_chunk("events") >= 0) # type: ignore

    try:
        gov.resume(st.get("ts"))                 # type: ignore # settle anything a previous crash left half-done
    except SimError as e:
        log("resume failed (will retry next tick):", e)
    n = pull_new_chunks(sim, ing, "history")
    log(f"history ingested: {n} new events")

    while True:
        st = sim.state()
        if st.get("finished"): # type: ignore
            break
        pull_new_chunks(sim, ing, "events")      # 1. observe everything that already happened
        try:
            gov.poll(st.get("ts"))               # type: ignore # follow open approvals; apply approved ones at once
        except SimError as e:
            log("governance poll failed (will retry next tick):", e)
        # (later steps plug in here: detect -> experiment -> propose)
        r = sim.advance(a.tick)                  # 2. let the world move
        if r.get("finished"):
            break
        pull_new_chunks(sim, ing, "events")      # 3. observe what just happened
        store.meta_set("last_sim_ts", r.get("ts"))

    pull_new_chunks(sim, ing, "events")
    store.audit("controller_finished", sim_ts=sim.state().get("ts")) # type: ignore
    log("scenario finished. credits:", sim.state().get("credits_remaining")) # type: ignore


if __name__ == "__main__":
    main()