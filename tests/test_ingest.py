import json

from controller.ingest import Ingestor, day_hour
from controller.store import Store

LEAD = {"type": "lead", "ts": "2027-01-04T08:00:00+05:30", "workflow_id": "w", "campaign_id": "w:c1",
        "lead_id": "w:l1", "person_id": "w:p1",
        "features": {"lang": "hi", "city_tier": "2", "age_band": "60+", "value_band": "mid"}, "event_id": 1}


def call(eid, connected, engaged, halluc=False, ts="2027-01-04T10:15:00+05:30"):
    return {"type": "call", "ts": ts, "workflow_id": "w", "campaign_id": "w:c1", "lead_id": "w:l1",
            "person_id": "w:p1", "attempt_no": 1, "campaign_day_index": 0, "config_version": 1,
            "cohort_id": "default", "variant": {"welcome_message": "W1", "voice": "V1", "caller_id": "D1",
                                                "prompt_version": "P1"},
            "connected": connected, "engaged": engaged,
            "qa": {"hallucination_flag": halluc, "flow_adherence": 0.9} if connected else None, "event_id": eid}


def text(*evs):
    return "\n".join(json.dumps(e) for e in evs)


def test_day_hour():
    assert day_hour("2027-01-04T10:15:00+05:30") == (0, 10)
    assert day_hour("2027-01-03T23:45:00+05:30") == (-1, 23)


def test_counts_and_feature_join(tmp_path):
    ing = Ingestor(Store(str(tmp_path / "s.db")))
    ing.ingest_chunk(0, "events", text(LEAD, call(2, True, True), call(3, True, False, True), call(4, False, False)))
    d, c, e, h, age = ing.store.db.execute(
        "SELECT SUM(dials), SUM(connected), SUM(engaged), SUM(halluc), MIN(age_band) FROM agg").fetchone()
    assert (d, c, e, h, age) == (3, 2, 1, 1, "60+")


def test_chunk_is_idempotent(tmp_path):
    ing = Ingestor(Store(str(tmp_path / "s.db")))
    t = text(LEAD, call(2, True, True))
    assert ing.ingest_chunk(0, "events", t) == 2
    assert ing.ingest_chunk(0, "events", t) == 0     # replay after a crash: counted once only
    assert ing.store.db.execute("SELECT SUM(dials) FROM agg").fetchone()[0] == 1


def test_restart_keeps_state(tmp_path):
    p = str(tmp_path / "s.db")
    Ingestor(Store(p)).ingest_chunk(0, "events", text(LEAD, call(2, True, True)))
    ing2 = Ingestor(Store(p))                         # "restart": fresh objects, same file
    assert ing2.store.max_chunk("events") == 0
    ing2.ingest_chunk(1, "events", text(call(3, True, False)))   # lead features come back from SQLite
    assert ing2.store.db.execute("SELECT MIN(age_band) FROM agg").fetchone()[0] == "60+"