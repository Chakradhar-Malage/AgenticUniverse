"""SQLite state store. This is the controller's memory AND the audit trail.

Crash-safety idea: anything that must stay consistent is written in ONE transaction
(e.g. 'events of chunk 17 were counted' + 'chunk 17 is done'). Either both happen or neither.
"""
import json
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

-- which event chunks are already counted. Makes ingestion idempotent and restartable.
CREATE TABLE IF NOT EXISTS chunks_done(
  chunk INTEGER PRIMARY KEY, kind TEXT, n_events INTEGER, max_event_id INTEGER, at_wall REAL);

CREATE TABLE IF NOT EXISTS leads(
  lead_id TEXT PRIMARY KEY, workflow_id TEXT, person_id TEXT,
  age_band TEXT, lang TEXT, city_tier TEXT, value_band TEXT);

CREATE TABLE IF NOT EXISTS campaigns(
  campaign_id TEXT PRIMARY KEY, workflow_id TEXT, leads INTEGER,
  config_version_at_creation INTEGER, ts TEXT);

-- Counters of what ACTUALLY ran. day<0 = history. One row per distinct combination.
CREATE TABLE IF NOT EXISTS agg(
  workflow_id TEXT, day INTEGER, hour INTEGER, config_version INTEGER, cohort_id TEXT,
  welcome TEXT, voice TEXT, caller_id TEXT, prompt TEXT,
  age_band TEXT, lang TEXT, city_tier TEXT,
  dials INTEGER DEFAULT 0, connected INTEGER DEFAULT 0, engaged INTEGER DEFAULT 0,
  halluc INTEGER DEFAULT 0,
  PRIMARY KEY(workflow_id, day, hour, config_version, cohort_id, welcome, voice, caller_id, prompt,
              age_band, lang, city_tier));

-- Same counters but split by ATTEMPT NUMBER. Retries pick up less than first attempts and happen later
-- in the day, so without this an "hour-of-day" or "drift" signal is partly just attempt mix.
CREATE TABLE IF NOT EXISTS agg_attempt(
  workflow_id TEXT, day INTEGER, hour INTEGER, attempt_no INTEGER,
  dials INTEGER DEFAULT 0, connected INTEGER DEFAULT 0, engaged INTEGER DEFAULT 0,
  PRIMARY KEY(workflow_id, day, hour, attempt_no));

-- every proposal we make (approval path or auto-apply). Written as an INTENT before we call the sim.
CREATE TABLE IF NOT EXISTS proposals(
  proposal_id TEXT PRIMARY KEY, workflow_id TEXT, kind TEXT, base_version INTEGER, status TEXT,
  approval_id TEXT, config_json TEXT, body_json TEXT, created_sim_ts TEXT, updated_sim_ts TEXT,
  applied_version INTEGER, detail TEXT);

-- every config change we SEE (supervisor edits, our own applies)
CREATE TABLE IF NOT EXISTS config_log(
  event_id INTEGER PRIMARY KEY, workflow_id TEXT, version INTEGER, actor TEXT,
  changed_dims TEXT, proposal_id TEXT, approval_id TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS approval_log(
  event_id INTEGER PRIMARY KEY, approval_id TEXT, workflow_id TEXT, status TEXT, reasons TEXT, ts TEXT);

-- append-only decision journal: the answer to "why did workflow X change at time T".
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at_wall REAL, sim_ts TEXT, workflow_id TEXT,
  kind TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS audit_wf ON audit(workflow_id, id);
"""


class Store:
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def meta_get(self, k, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def meta_set(self, k, v):
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    def audit(self, kind, workflow_id=None, sim_ts=None, **payload):
        self.db.execute(
            "INSERT INTO audit(at_wall, sim_ts, workflow_id, kind, payload) VALUES(?,?,?,?,?)",
            (time.time(), sim_ts, workflow_id, kind, json.dumps(payload, default=str)))

    def chunk_done(self, n):
        return self.db.execute("SELECT 1 FROM chunks_done WHERE chunk=?", (n,)).fetchone() is not None

    def max_chunk(self, kind):
        r = self.db.execute("SELECT MAX(chunk) FROM chunks_done WHERE kind=?", (kind,)).fetchone()[0]
        return -1 if r is None else r