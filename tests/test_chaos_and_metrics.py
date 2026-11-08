import copy

import pytest

from controller import chaos
from controller.governance import Governance
from controller.metrics import record_tick, rss_mb, summary
from controller.store import Store
from tests.fake_sim import FakeSim
from tests.test_governance import CFG0, POLICY, TS, cfg_with, kw


class Killed(BaseException):
    """Stands in for os._exit(137) so a test can survive the 'crash'."""


@pytest.fixture()
def die(monkeypatch):
    def _die():
        raise Killed()
    monkeypatch.setattr(chaos, "_die", _die)


def test_chaos_is_inert_without_env(monkeypatch):
    monkeypatch.delenv("CHAOS_POINT", raising=False)
    chaos.maybe_crash("after_put")                      # must do nothing


def test_chaos_fires_once_then_marker_blocks_it(monkeypatch, tmp_path, die):
    monkeypatch.setenv("CHAOS_POINT", "after_put")
    monkeypatch.setenv("CHAOS_MARKER", str(tmp_path / "m"))
    chaos.maybe_crash("after_post")                     # different point: nothing
    with pytest.raises(Killed):
        chaos.maybe_crash("after_put")
    chaos.maybe_crash("after_put")                      # marker exists: the restart runs clean


@pytest.mark.parametrize("point", ["after_intent", "after_put"])
def test_kill_during_auto_apply_then_restart_applies_exactly_once(monkeypatch, tmp_path, die, point):
    sim, store = FakeSim(), Store(str(tmp_path / "s.db"))
    monkeypatch.setenv("CHAOS_POINT", point)
    monkeypatch.setenv("CHAOS_MARKER", str(tmp_path / "m"))
    gov = Governance(sim, store)
    with pytest.raises(Killed):
        gov.auto_apply(POLICY, CFG0, 1, cfg_with(caller_id={"D1": .4, "D2": .4, "D3": .2}), rationale="spread", sim_ts=TS)
    assert sim.applied == (1 if point == "after_put" else 0)      # the server did (or did not) act before the kill
    gov2 = Governance(sim, Store(str(tmp_path / "s.db")))          # "restart"
    gov2.resume(TS)
    assert sim.applied == 1 and len(gov2._rows(["applied"])) == 1 and gov2._rows(["intent"]) == []


@pytest.mark.parametrize("point", ["after_intent", "after_post", "after_apply"])
def test_kill_during_approval_path_then_restart_ends_applied_once(monkeypatch, tmp_path, die, point):
    sim, store = FakeSim(), Store(str(tmp_path / "s.db"))
    monkeypatch.setenv("CHAOS_POINT", point)
    monkeypatch.setenv("CHAOS_MARKER", str(tmp_path / "m"))
    gov = Governance(sim, store)
    try:
        row = gov.submit("w", 1, cfg_with(voice={"V2": 1}), **kw())  #type: ignore
        sim.decide(row["approval_id"], "approved") # type: ignore
        gov.poll(TS)
    except Killed:
        pass
    gov2 = Governance(sim, Store(str(tmp_path / "s.db")))
    gov2.resume(TS)
    for _ in range(3):
        if sim.approvals:
            for aid in sim.approvals:
                if sim.approvals[aid]["status"] == "pending":
                    sim.decide(aid, "approved")
        gov2.poll(TS)
    assert sim.created == 1 and sim.applied == 1                  # one request, one apply, whatever moment we died
    assert [r["status"] for r in gov2._rows(["applied", "intent", "applying", "pending", "approved"])] == ["applied"]


def test_metrics_summary(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    for i in range(8):
        record_tick(store, f"2027-01-04T0{i}:00:00+05:30", 1000, 0.05 * (i + 1), 0.01, 0.2, 0.06 * (i + 1), 120.0 + i, 200 - i)
    s = summary(store.db)
    assert s["ticks"] == 8 and s["events_live"] == 8000 and s["credits_used"] == 7
    assert s["ingest_events_per_sec"] > 0 and s["decision_lag_s_max"] >= s["decision_lag_s_p50"]


def test_rss_is_a_positive_number_or_none():
    v = rss_mb()
    assert v is None or v > 1