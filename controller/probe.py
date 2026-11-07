"""Print the real shape of the simulator's responses so we code against facts, not guesses.
    python -m controller.probe --url http://127.0.0.1:8700 > probe_output.txt
HTTP only; touches nothing."""
import argparse
import json

from .client import Sim, SimError


def show(title, fn, limit=2500):
    print(f"\n===== {title} =====")
    try:
        s = json.dumps(fn(), indent=1, default=str)
        print(s[:limit] + (f"\n... [{len(s) - limit} more chars]" if len(s) > limit else ""))
    except SimError as e:
        print("ERROR", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8700")
    sim = Sim(ap.parse_args().url)
    show("GET /sim/state", sim.state)
    wfs = sim.workflows()
    show("GET /workflows (first item)", lambda: wfs[0] if isinstance(wfs, list) else wfs)
    wid = (wfs[0] if isinstance(wfs, list) else next(iter(wfs.values())))["workflow_id"] # type: ignore
    show(f"GET /workflows/{wid}", lambda: sim._json("GET", f"/workflows/{wid}"), 4000)
    show(f"GET /workflows/{wid}/config", lambda: sim._json("GET", f"/workflows/{wid}/config"), 3000)
    show(f"GET /workflows/{wid}/config/history", lambda: sim._json("GET", f"/workflows/{wid}/config/history"))
    show("GET /events/chunks?kind=events", lambda: sim.list_chunks("events")[:2])
    show("GET /approvals", lambda: sim._json("GET", "/approvals"))


if __name__ == "__main__":
    main()