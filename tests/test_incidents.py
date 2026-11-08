import copy
import math

from controller.governance import Governance
from controller.incidents import HallucinationResponder, detect, parse_candidates, parse_validation
from controller.stats import binom_sf
from controller.store import Store
from tests.fake_sim import FakeSim

CAT = {"P1": "Current production prompt", "P2": "Supervisor edit: adds payment-urgency language and benefit claims",
       "P3": "Grounded: benefit claims restricted to policy-schedule fields; tighter objection handling",
       "P4": "Persuasive: freer objection handling and richer benefit explanations",
       "P5": "Strict: near-verbatim compliance script"}
WF = {"workflow_id": "w", "options": {"prompt_version": list(CAT)}, "prompt_catalog": CAT, "cadence": [2, 2, 2],
      "daily_batch": {"size": 1000},
      "governance": {"approver_hours": [10, 19], "max_requests_per_24h": 4, "min_validation_pass_rate": 0.97,
                     "auto_apply": {"enabled": False, "allowed_dims": [], "max_shift": 0}}}
CFG = {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1}, "caller_id": {"D1": 1}}},
       "prompt_version": "P2", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}


def put(store, prompt, day, hour, conn, flags, engaged=0):
    store.db.execute("INSERT INTO agg(workflow_id,day,hour,config_version,cohort_id,welcome,voice,caller_id,prompt,age_band,lang,city_tier,"
                     "dials,connected,engaged,halluc) VALUES('w',?,?,1,'default','W1','V1','D1',?,'x','x','x',?,?,?,?)",
                     (day, hour, prompt, conn * 4, conn, engaged, flags))


def setup(tmp_path):
    sim, store = FakeSim(), Store(str(tmp_path / "s.db"))
    sim.config = copy.deepcopy(CFG)
    gov = Governance(sim, store)
    for d in (-3, -2, -1):                                     # healthy history on P1: 1.2% flags, 3,000 connected
        put(store, "P1", d, 12, 1000, 12, engaged=550)
    for h in (11, 12, 13):                                     # the supervisor's P2: 6.7% flags
        put(store, "P2", 0, h, 60, 4, engaged=25)
    return sim, store, gov, HallucinationResponder(sim, store, gov, {"w": WF}, log=lambda *a: None)


def status(store):
    return store.db.execute("SELECT status FROM incidents").fetchone()[0]


def test_binom_sf_exact():
    assert abs(binom_sf(5, 10, 0.5) - 0.623046875) < 1e-9
    assert binom_sf(0, 10, .3) == 1.0 and binom_sf(11, 10, .3) == 0.0
    assert binom_sf(12, 180, 0.012) < 1e-4 < binom_sf(4, 180, 0.012)


def test_parsers_accept_plausible_shapes():
    assert parse_candidates({"suggestions": [{"prompt_version": "P3", "summary": "x"}]}) == [("P3", "x")]
    assert parse_candidates([{"prompt": "P4"}, "P5"]) == [("P4", ""), ("P5", "")]
    assert parse_candidates({"weird": 1}) == []
    assert parse_validation({"run_id": "v1", "pass_rate": "0.98"}) == ("v1", 0.98)


def test_no_false_alarm_on_normal_noise(tmp_path):
    sim, store, gov, hr = setup(tmp_path)
    ts = "2027-01-04T13:45:00+05:30"
    for rate_flags, conn in ((1, 100), (4, 200)):               # 1% (normal) and 2% on 600 calls (under 2x the 1.2% baseline)
        store.db.execute("DELETE FROM agg WHERE day=0")
        for h in (11, 12, 13):
            put(store, "P1", 0, h, conn, rate_flags)
        assert detect(store, "w", ts) is None


def test_cheapest_fix_first_full_cycle(tmp_path):
    sim, store, gov, hr = setup(tmp_path)
    hr.tick("2027-01-04T13:45:00+05:30")
    assert status(store) == "proposed"
    assert sim.llm_calls == 0 and sim.credits == 194            # reverted to P1 for 6 credits; no LLM needed
    body = sim.posted[0]
    assert body["proposed_config"]["prompt_version"] == "P1" and body["evidence"] == {"validation_run_id": "val_00001"}
    assert body["proposed_config"]["allocations"] == CFG["allocations"]          # nothing else touched
    sim.decide("apr_00001", "approved")
    gov.poll("2027-01-04T14:00:00+05:30")
    hr.tick("2027-01-04T14:00:00+05:30")
    assert status(store) == "verifying"
    for h in (14, 15):
        put(store, "P1", 0, h, 100, 1, engaged=55)
    hr.tick("2027-01-04T15:15:00+05:30")
    assert status(store) == "resolved"
    kinds = [r[0] for r in store.db.execute("SELECT kind FROM audit")]
    assert "incident_detected" in kinds and "credits_spent" in kinds and "incident_resolved" in kinds


def test_falls_back_to_llm_and_screens_risky_candidates(tmp_path):
    sim, store, gov, hr = setup(tmp_path)
    sim.pass_rates = {"P1": 0.90}                               # reverting fails validation
    sim.suggestions = [{"prompt_version": "P5", "summary": "Strict"}, {"prompt_version": "P3", "summary": "Grounded"}]
    hr.tick("2027-01-04T13:45:00+05:30")
    assert sim.llm_calls == 1 and sim.credits == 200 - 6 - 3 - 6
    assert sim.posted[0]["proposed_config"]["prompt_version"] == "P3"                # P5 (verbatim script) never validated or proposed
    screened = [r for r in store.db.execute("SELECT payload FROM audit WHERE kind='llm_candidate'")]
    assert len(screened) == 2 and '"kept": false' in screened[0][0]


def test_credit_reserve_is_protected(tmp_path):
    sim, store, gov, hr = setup(tmp_path)
    sim.credits = 33                                            # spending 6 would leave 27 < the 30-credit reserve
    hr.tick("2027-01-04T13:45:00+05:30")
    assert status(store) == "failed" and sim.credits == 33 and sim.created == 0
    assert any(r[0] == "alert" for r in store.db.execute("SELECT kind FROM audit"))


def test_waits_for_approver_shift_then_proposes(tmp_path):
    sim, store, gov, hr = setup(tmp_path)
    store.db.execute("DELETE FROM agg WHERE day=0")
    for h in (20, 21, 22):
        put(store, "P2", 0, h, 60, 4)
    hr.tick("2027-01-04T22:30:00+05:30")                        # 22:30: approvers are off
    assert status(store) == "ready" and sim.created == 0
    hr.tick("2027-01-05T10:00:00+05:30")                        # next morning, on shift
    assert status(store) == "proposed" and sim.created == 1


def test_risk_screen_matches_whole_words_only():
    from controller.incidents import RISKY
    assert not RISKY.search(CAT["P1"]) and not RISKY.search(CAT["P3"])        # 'restricted' must NOT trigger 'strict'
    assert all(RISKY.search(CAT[p]) for p in ("P2", "P4", "P5"))