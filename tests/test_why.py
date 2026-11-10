import json

from controller import why
from controller.store import Store


def test_why_explains_an_approved_prompt_change(tmp_path, capsys, monkeypatch):
    p = str(tmp_path / "s.db")
    st = Store(p)
    pid = "ctl-w-v6-abc"
    st.db.execute("INSERT INTO proposals(proposal_id,workflow_id,kind,base_version,status,approval_id,config_json,body_json,created_sim_ts,updated_sim_ts,applied_version) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (pid, "w", "approval", 6, "applied", "apr_00002", "{}", "{}", "2027-01-07T13:30:00+05:30", "2027-01-07T14:45:00+05:30", 7))
    st.audit("proposal_intent", "w", "2027-01-07T13:30:00+05:30", proposal_id=pid, summary="Replace prompt P2 with P1", rationale="P2 flags 5.6% vs 1.4%", impact_score=0.5)
    st.audit("proposal_approved", "w", "2027-01-07T14:45:00+05:30", proposal_id=pid, detail="apply_by 16:18")
    st.audit("proposal_applied", "w", "2027-01-07T14:45:00+05:30", proposal_id=pid, applied_version=7)
    st.db.execute("INSERT INTO incidents VALUES('hal-w-85','w','hallucination','resolved','2027-01-07T13:30:00+05:30','x',?)",
                  (json.dumps({"proposal_id": pid, "spent": 6, "evidence": {"prompt": "P2", "rate": .056, "baseline_rate": .014, "p_value": 7.9e-5},
                               "after": {"rate": 0.0, "connected": 160, "engagement": .56}}),))
    for h, conn, flags in ((8, 100, 6), (9, 100, 5), (16, 100, 0), (17, 100, 1)):      # day 3 = hour index 72+h
        st.db.execute("INSERT INTO agg(workflow_id,day,hour,config_version,cohort_id,welcome,voice,caller_id,prompt,age_band,lang,city_tier,dials,connected,engaged,halluc) "
                      "VALUES('w',3,?,1,'default','W1','V1','D1','P1','x','x','x',400,?,55,?)", (h, conn, flags))
    st.db.commit() if hasattr(st.db, "commit") else None
    monkeypatch.setattr("sys.argv", ["why", "--db", p, "--workflow", "w", "--at", "2027-01-07T15:00:00+05:30"])
    why.main()
    out = capsys.readouterr().out
    assert "version 7 was applied at 2027-01-07T14:45" in out and "approval path" in out
    assert "Replace prompt P2 with P1" in out and "incident hal-w-85" in out and "apr_00002" in out
    assert "6 hours before" in out and "6 hours after" in out and "verification" in out