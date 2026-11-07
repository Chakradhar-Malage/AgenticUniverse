"""Event ingestion: chunk text -> counters, atomically, exactly once.

Why counters and not raw events? ~850k events/day at load scale. We only need rates by
(workflow, day, hour, version, cohort, variant, lead features). Counters keep the DB tiny and
queries instant. The chunk is the unit of idempotency: counters + 'chunk done' commit together.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta

# Simulated day 0 starts here (SIMULATOR.md, section 1).
D0 = datetime.fromisoformat("2027-01-04T00:00:00+05:30")

AGG_KEY = ("workflow_id day hour config_version cohort_id welcome voice caller_id prompt "
           "age_band lang city_tier").split()
UPSERT = f"""INSERT INTO agg({",".join(AGG_KEY)},dials,connected,engaged,halluc)
VALUES({",".join("?" * len(AGG_KEY))},?,?,?,?)
ON CONFLICT({",".join(AGG_KEY)}) DO UPDATE SET
  dials=dials+excluded.dials, connected=connected+excluded.connected,
  engaged=engaged+excluded.engaged, halluc=halluc+excluded.halluc"""


UPSERT_ATT = """INSERT INTO agg_attempt(workflow_id,day,hour,attempt_no,dials,connected,engaged) VALUES(?,?,?,?,?,?,?)
ON CONFLICT(workflow_id,day,hour,attempt_no) DO UPDATE SET
  dials=dials+excluded.dials, connected=connected+excluded.connected, engaged=engaged+excluded.engaged"""


def day_hour(ts):
    """'2027-01-05T14:15:00+05:30' -> (day index since D0, hour 0-23 in IST). Negative = history."""
    d = datetime.fromisoformat(ts)
    delta = d - D0
    return delta // timedelta(days=1), d.hour


class Ingestor:
    def __init__(self, store):
        self.store = store
        self.features = {}  # lead_id -> (age_band, lang, city_tier); warm cache over the leads table

    def _features(self, lead_id):
        f = self.features.get(lead_id)
        if f is None:
            r = self.store.db.execute(
                "SELECT age_band, lang, city_tier FROM leads WHERE lead_id=?", (lead_id,)).fetchone()
            f = tuple(r) if r else ("?", "?", "?")
            if r:
                self.features[lead_id] = f
        return f

    def ingest_chunk(self, chunk_no, kind, text):
        """Returns number of events counted (0 if the chunk was already done)."""
        st = self.store
        if st.chunk_done(chunk_no):
            return 0
        agg = defaultdict(lambda: [0, 0, 0, 0])
        att = defaultdict(lambda: [0, 0, 0])
        leads, camps, cfgs, apprs = [], [], [], []
        n = max_id = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            n += 1
            max_id = max(max_id, ev.get("event_id", 0))
            t = ev["type"]
            if t == "call":
                f = self._features(ev["lead_id"])
                day, hour = day_hour(ev["ts"])
                v = ev["variant"]
                key = (ev["workflow_id"], day, hour, ev["config_version"], ev["cohort_id"],
                       v["welcome_message"], v["voice"], v["caller_id"], v["prompt_version"], *f)
                a = agg[key]
                a[0] += 1
                a[1] += bool(ev["connected"])
                a[2] += bool(ev["engaged"])
                a[3] += bool((ev.get("qa") or {}).get("hallucination_flag"))
                b = att[(ev["workflow_id"], day, hour, ev.get("attempt_no", 0))]
                b[0] += 1
                b[1] += bool(ev["connected"])
                b[2] += bool(ev["engaged"])
            elif t == "lead":
                ft = ev["features"]
                row = (ev["lead_id"], ev["workflow_id"], ev["person_id"], ft["age_band"], ft["lang"],
                       ft["city_tier"], ft["value_band"])
                leads.append(row)
                self.features[ev["lead_id"]] = (ft["age_band"], ft["lang"], ft["city_tier"])
            elif t == "campaign_created":
                camps.append((ev["campaign_id"], ev["workflow_id"], ev["leads"],
                              ev.get("config_version_at_creation"), ev["ts"]))
            elif t == "config_changed":
                cfgs.append((ev["event_id"], ev["workflow_id"], ev["version"], ev["actor"],
                             json.dumps(ev.get("changed_dims")), ev.get("proposal_id"),
                             ev.get("approval_id"), ev["ts"]))
            elif t == "approval_decided":
                apprs.append((ev["event_id"], ev["approval_id"], ev["workflow_id"], ev["status"],
                              json.dumps(ev.get("reasons")), ev["ts"]))
        with st.tx() as db:  # ONE transaction: counters + chunk marker
            db.executemany("INSERT OR REPLACE INTO leads VALUES(?,?,?,?,?,?,?)", leads)
            db.executemany("INSERT OR REPLACE INTO campaigns VALUES(?,?,?,?,?)", camps)
            db.executemany("INSERT OR IGNORE INTO config_log VALUES(?,?,?,?,?,?,?,?)", cfgs)
            db.executemany("INSERT OR IGNORE INTO approval_log VALUES(?,?,?,?,?,?)", apprs)
            db.executemany(UPSERT, [(*k, *v) for k, v in agg.items()])
            db.executemany(UPSERT_ATT, [(*k, *v) for k, v in att.items()])
            db.execute("INSERT INTO chunks_done VALUES(?,?,?,?,strftime('%s','now'))",
                       (chunk_no, kind, n, max_id))
        return n


def pull_new_chunks(sim, ing, kind):
    """Fetch and ingest every chunk we haven't counted yet. Safe to call any time, any number of times."""
    total = 0
    for ch in sim.list_chunks(kind, ing.store.max_chunk(kind)):
        no = ch["chunk"]
        if not ing.store.chunk_done(no):
            total += ing.ingest_chunk(no, kind, sim.get_chunk(no))
    return total