import copy

from controller.callerid import (CallerIdManager, assess, did_window, normalise, step_toward, target_dist)
from controller.governance import Governance, tv_distance
from controller.store import Store
from tests.fake_sim import FakeSim

OPTS = ["D1", "D2", "D3"]


def run_assess(stats, n=2, prev=None, recov=None):
    prev = prev or {}
    for _ in range(n):
        prev, ev = assess(OPTS, stats, recov or stats, prev)
    return prev, ev


def test_equal_numbers_are_never_flagged():
    st, _ = run_assess({"D1": (300, 80), "D2": (300, 78), "D3": (300, 82)}, n=5)
    assert all(s == "good" for s, _ in st.values())


def test_bad_number_needs_two_consecutive_strikes():
    stats = {"D1": (300, 80), "D2": (300, 24), "D3": (300, 81)}      # D2: 8% vs 27%
    one, _ = run_assess(stats, n=1)
    assert one["D2"][0] != "bad" and one["D2"][1] == 1               # first strike: wait, do not act
    two, ev = run_assess(stats, n=2)
    assert two["D2"][0] == "bad" and any(e["event"] == "bad" for e in ev)


def test_not_enough_data_means_no_judgement():
    st, _ = run_assess({"D1": (300, 80), "D2": (40, 2)}, n=3)
    assert st["D2"][0] == "unknown"


def test_small_gap_is_not_material():
    st, _ = run_assess({"D1": (3000, 800), "D2": (3000, 740)}, n=3)   # 24.7% vs 26.7%: ~8% relative, below 20%
    assert st["D2"][0] != "bad"


def test_bad_number_recovers_only_when_comparable():
    prev = {"D1": ("good", 0), "D2": ("bad", 0), "D3": ("good", 0)}
    still, _ = assess(OPTS, {}, {"D1": (800, 220), "D2": (200, 20), "D3": (800, 216)}, prev)
    assert still["D2"][0] == "bad"
    back, ev = assess(OPTS, {}, {"D1": (800, 220), "D2": (200, 52), "D3": (800, 216)}, prev)
    assert back["D2"][0] == "good" and ev[0]["event"] == "recovered"


def test_target_floor_and_even_split():
    t = target_dist(OPTS, {"D1": ("good", 0), "D2": ("bad", 0), "D3": ("unknown", 0)})
    assert abs(sum(t.values()) - 1) < 1e-9 and t["D2"] == 0.03 and abs(t["D1"] - 0.485) < 1e-9 # type: ignore
    assert target_dist(["D1"], {"D1": ("bad", 0)}) is None          # never zero out every number


def test_step_toward_respects_max_shift():
    cur, tgt = {"D1": .5, "D2": .5}, {"D1": 1 / 3, "D2": 1 / 3, "D3": 1 / 3}
    nxt = step_toward(cur, tgt, 0.18)
    assert tv_distance(cur, nxt) <= 0.18 + 1e-3 and abs(sum(nxt.values()) - 1) < 1e-3
    full = step_toward(cur, tgt, 0.9)                         # limit larger than needed: go all the way
    assert all(abs(full[k] - 1 / 3) < 1e-3 for k in ("D1", "D2", "D3"))


# ---- integration with the fake sim: incident -> detection -> auto-apply
CFG = {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1}, "caller_id": {"D1": .5, "D2": .5}}},
       "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
WF = {"workflow_id": "w", "options": {"caller_id": OPTS}, "daily_batch": {"size": 1000},
      "governance": {"auto_apply": {"enabled": True, "allowed_dims": ["schedule", "caller_id"], "max_shift": 0.5},
                     "approver_hours": [10, 19], "max_requests_per_24h": 3}}


def put_agg(store, did, hour, dials, conn, day=0):
    store.db.execute("INSERT INTO agg(workflow_id,day,hour,config_version,cohort_id,welcome,voice,caller_id,prompt,age_band,lang,city_tier,"
                     "dials,connected,engaged,halluc) VALUES('w',?,?,1,'default','W1','V1',?,'P1','x','x','x',?,?,0,0)",
                     (day, hour, did, dials, conn))


def test_incident_end_to_end(tmp_path):
    sim, store = FakeSim(), Store(str(tmp_path / "s.db"))
    sim.config = copy.deepcopy(CFG)
    mgr = CallerIdManager(sim, store, Governance(sim, store), {"w": WF}, log=lambda *a: None)
    for h in (9, 10, 11):                                   # D2 is spam-flagged: 8% pickup vs ~27%
        put_agg(store, "D1", h, 100, 27)
        put_agg(store, "D2", h, 100, 8)
        put_agg(store, "D3", h, 100, 27)
    assert did_window(store, "w", 11, 3)["D2"] == (300, 24)
    mgr.tick("2027-01-04T11:30:00+05:30")
    mgr.tick("2027-01-04T11:45:00+05:30")
    status = dict(store.db.execute("SELECT caller_id, status FROM did_state").fetchall())
    assert status["D2"] == "bad"
    w = sim.config["allocations"]["default"]["caller_id"]
    assert w["D2"] < w["D1"] and w["D2"] < 0.3                # traffic moved away from the bad number
    assert tv_distance(normalise(CFG["allocations"]["default"]["caller_id"]), normalise(sim.config["allocations"]["default"]["caller_id"])) <= 0.5
    kinds = [r[0] for r in store.db.execute("SELECT kind FROM audit")]
    assert "did_bad" in kinds and "proposal_applied" in kinds
    assert sim.config["prompt_version"] == "P1" and sim.config["allocations"]["default"]["voice"] == {"V1": 1}   # touched nothing else


def test_approval_path_batches_to_one_request_per_day(tmp_path):
    sim, store = FakeSim(), Store(str(tmp_path / "s.db"))
    sim.config = copy.deepcopy(CFG)
    wf = copy.deepcopy(WF)
    wf["governance"]["auto_apply"] = {"enabled": False, "allowed_dims": [], "max_shift": 0}
    mgr = CallerIdManager(sim, store, Governance(sim, store), {"w": wf}, log=lambda *a: None)
    for h in (9, 10, 11):
        put_agg(store, "D1", h, 100, 27)
        put_agg(store, "D2", h, 100, 8)
    for m in ("10:30", "10:45", "11:00", "11:15"):
        mgr.tick(f"2027-01-04T{m}:00+05:30")
    assert sim.created == 1                                  # one request, not one per tick
    assert sim.version == 1                                  # nothing applied without approval


def test_protective_steps_are_not_throttled_by_cooldown(tmp_path):
    """Once a number is bad we keep stepping every tick (policy max_shift limits each step) until the floor is reached."""
    sim, store = FakeSim(), Store(str(tmp_path / "s.db"))
    sim.config = copy.deepcopy(CFG)
    sim.config["allocations"]["default"]["caller_id"] = {"D1": .34, "D2": .33, "D3": .33}
    wf = copy.deepcopy(WF)
    wf["governance"]["auto_apply"]["max_shift"] = 0.2
    mgr = CallerIdManager(sim, store, Governance(sim, store), {"w": wf}, log=lambda *a: None)
    for h in (10, 11):
        put_agg(store, "D1", h, 100, 27)
        put_agg(store, "D2", h, 100, 8)
        put_agg(store, "D3", h, 100, 27)
    for m in ("11:15", "11:30", "11:45", "12:00", "12:15"):
        mgr.tick(f"2027-01-04T{m}:00+05:30")
    assert sim.config["allocations"]["default"]["caller_id"]["D2"] <= 0.04 + 1e-9      # reached the floor within a few ticks