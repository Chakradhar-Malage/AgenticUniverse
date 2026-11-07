import json
from controller.ingest import Ingestor
from controller.store import Store
from controller import explore
from tests.test_ingest import LEAD, call, text


def test_explore_runs_on_tiny_db(tmp_path, capsys, monkeypatch):
    p = str(tmp_path / "s.db")
    ing = Ingestor(Store(p))
    ing.ingest_chunk(0, "history", text({**LEAD, "ts": "2027-01-03T08:00:00+05:30"},
                                        call(2, True, True, ts="2027-01-03T10:15:00+05:30"),
                                        call(3, False, False, ts="2027-01-03T11:15:00+05:30")))
    monkeypatch.setattr("sys.argv", ["explore", "--db", p])
    explore.main()
    out = capsys.readouterr().out
    assert "pickup by caller_id" in out and "D1" in out