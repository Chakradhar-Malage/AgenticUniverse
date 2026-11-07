import copy

import pytest

from controller.client import SimError
from controller.governance import (Governance, auto_apply_check, can_request, changed_dims, make_proposal_id,
                                   max_shift, tv_distance)
from controller.store import Store
from tests.fake_sim import FakeSim

TS = "2027-01-04T10:15:00+05:30"
CFG0 = {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1}, "caller_id": {"D1": .5, "D2": .5}}},
        "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
POLICY = {"workflow_id": "w", "governance": {"auto_apply": {"enabled": True, "allowed_dims": ["schedule", "caller_id"], "max_shift": 0.5},
                                             "approver_hours": [10, 19], "max_requests_per_24h": 3}}


def cfg_with(**dim):
    c = copy.deepcopy(CFG0)
    for k, v in dim.items():
        c["allocations"]["default"][k] = v
    return c


def kw(**o):
    d = dict(summary="s", diff={"x": 1}, affected_leads=10, dry_run={"ok": 1}, impact_score=.3, rationale="r", sim_ts=TS)
    d.update(o)
    return d


@pytest.fixture()
def env(tmp_path):
    sim, store = FakeSim(), Store(str(tmp_path / "s.db"))
    return sim, store, Governance(sim, store), tmp_path


# ---- pure policy maths
def test_changed_dims_and_shift():
    new = cfg_with(voice={"V1": .5, "V2": .5})
    assert changed_dims(CFG0, new) == ["voice"]
    assert changed_dims(CFG0, copy.deepcopy(CFG0)) == []
    assert abs(max_shift(CFG0, new) - 0.5) < 1e-9
    assert tv_distance({"a": 1}, {"b": 1}) == 1


def test_auto_apply_check():
    gv = POLICY["governance"]
    assert auto_apply_check(gv, CFG0, cfg_with(caller_id={"D1": .4, "D2": .4, "D3": .2}))[0]
    ok, why = auto_apply_check(gv, CFG0, cfg_with(voice={"V2": 1}))
    assert not ok and "dims not allowed" in why[0]
    ok, why = auto_apply_check(gv, CFG0, cfg_with(caller_id={"D3": 1}))
    assert not ok and "max_shift" in " ".join(why)
    assert not auto_apply_check({"auto_apply": {"enabled": False, "allowed_dims": [], "max_shift": 0}}, CFG0, cfg_with(voice={"V2": 1}))[0]


def test_proposal_id_deterministic_and_salted():
    a = make_proposal_id("w", 1, CFG0)
    assert a == make_proposal_id("w", 1, copy.deepcopy(CFG0))
    assert a != make_proposal_id("w", 2, CFG0) and a != make_proposal_id("w", 1, CFG0, salt="D3")


# ---- approval path
def test_submit_is_idempotent(env):
    sim, store, gov, _ = env
    r1 = gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())
    r2 = gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())
    assert r1["status"] == "pending" and r1["proposal_id"] == r2["proposal_id"] and sim.created == 1


def test_crash_after_server_accepted_then_restart(env):
    sim, store, gov, tmp = env
    sim.crash_after = "post"                     # server stores the request, we never see the reply
    with pytest.raises(SimError):
        gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())
    gov2 = Governance(sim, Store(str(tmp / "s.db")))      # restart: new objects, same DB file
    gov2.resume(TS)
    rows = gov2._rows(["pending"])
    assert len(rows) == 1 and rows[0]["approval_id"] == "apr_00001" and sim.created == 1   # recovered, not duplicated


def test_full_cycle_and_crash_during_apply(env):
    sim, store, gov, tmp = env
    row = gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())
    sim.decide(row["approval_id"], "approved")
    sim.crash_after = "apply"                    # apply happens server-side, reply lost
    with pytest.raises(SimError):
        gov.poll(TS)
    assert gov.get(row["proposal_id"])["status"] == "applying"
    gov2 = Governance(sim, Store(str(tmp / "s.db")))
    gov2.resume(TS)
    done = gov2.get(row["proposal_id"])
    assert done["status"] == "applied" and done["applied_version"] == 2 and sim.applied == 1   # type: ignore # exactly once


def test_rejected_and_stale(env):
    sim, store, gov, _ = env
    row = gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())
    sim.decide(row["approval_id"], "rejected")
    gov.poll(TS)
    assert gov.get(row["proposal_id"])["status"] == "rejected"
    sim.version = 5                              # a supervisor edit moved the config
    stale = gov.submit("w", 1, cfg_with(voice={"V3": 1}), **kw())
    assert stale["status"] == "stale"


def test_approved_then_config_moved_is_stale(env):
    sim, store, gov, _ = env
    row = gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())
    sim.decide(row["approval_id"], "approved")
    sim.version = 2
    gov.poll(TS)
    assert gov.get(row["proposal_id"])["status"] == "stale" and sim.applied == 0


# ---- auto path
def test_auto_apply_ok_blocked_and_crash(env):
    sim, store, gov, tmp = env
    blocked = gov.auto_apply(POLICY, CFG0, 1, cfg_with(voice={"V2": 1}), rationale="x", sim_ts=TS)
    assert blocked["status"] == "blocked" and sim.applied == 0 and sim.version == 1     # never even sent
    sim.crash_after = "put"
    with pytest.raises(SimError):
        gov.auto_apply(POLICY, CFG0, 1, cfg_with(caller_id={"D1": .4, "D2": .4, "D3": .2}), rationale="spread", sim_ts=TS)
    gov2 = Governance(sim, Store(str(tmp / "s.db")))
    gov2.resume(TS)
    assert sim.applied == 1 and gov2._rows(["applied"])[0]["applied_version"] == 2


def test_can_request_batching_gate(env):
    sim, store, gov, _ = env
    gv = POLICY["governance"]
    assert can_request(store, gv, "w", TS)[0]
    assert not can_request(store, gv, "w", "2027-01-04T08:00:00+05:30")[0]       # approver off shift
    gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())
    assert not can_request(store, gv, "w", TS, own_cap=1)[0]                      # our own stricter cap