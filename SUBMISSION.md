# Outcome-First Agent Loop — Submission

**Name:** Chakradhar Malage · **Language / storage:** Python 3.12, SQLite (WAL). Dependencies: `httpx` (HTTP). `numpy` is used only by the Monte Carlo script, not by the loop.

---

## 0. TL;DR

- A single-process controller (Python + SQLite) ingests every event exactly once, detects bad caller IDs and hallucination spikes from **populations** (never single calls), and changes a workflow only through governance: auto-apply where the policy allows it, otherwise a batched approval request. Every decision is written to our own audit table.
- **public_02:** pickup **+23.3%**, conclusion rate **+26.4%** vs baseline; 8 changes applied (7 beneficial, 1 neutral, **0 harmful**); the supervisor's bad prompt was replaced in **195 minutes** for **6 credits**; ks_cancel engagement went from −3.8% (before the fix) to **+4.5%**.
- **Crash recovery:** killed at four protocol moments (before the call, after the server accepted, after the server applied, after auto-apply). Four of four restarts finished the run with every event counted once and every change applied once.
- **Deliberately not built:** variant experiments and promotion/rollback (C6/C7), call-time optimisation (C4), a supervisor UI (C11), fair credit allocation (C15), a per-call variant log. Reasons in §2.
- **Known miss, found and fixed late:** in public_01 the loop never caught a spam-flagged caller ID on a low-volume workflow (nw_renewal pickup −2.4%). Cause and fix in §11.

---

## 1. Assumptions

1. **Guardrail drops are absolute.** `pickup_rate_max_drop: 0.02` is read as 2 percentage points versus baseline. If it is meant relatively, nw_kyc engagement (−2.3% relative, −1.4 pp absolute in public_02) would be marginally over; see §5.
2. **Time:** D0 = 2027-01-04 00:00 IST as stated in SIMULATOR.md; hours are IST; a fixed 15-minute tick.
3. **History** (before D0) ran only the default config, so it carries baselines and hour/segment patterns, **not** variant effects.
4. **The approvals list includes each request's `proposal_id`.** I verified this indirectly: after a kill following an accepted request, the restart found its request by `proposal_id` (audit: "recovered after duplicate").
5. **The hallucination detector is noisy** (about 1–2% flags even on a good prompt), so a spike is judged against the workflow's own baseline, never against zero.
6. **A supervisor's prompt is never reverted silently.** Any prompt change is a proposal with validation evidence, even when the policy would otherwise be permissive.
7. **We manage only `allocations.default.caller_id`.** Cohort-specific caller-ID distributions and every other field are left exactly as found (the public scenarios have none).
8. **nw_kyc is `next_campaign_only`,** so its caller-ID changes take effect at the next 08:00 campaign, not mid-flight.
9. **Own request cap:** at most one routine approval request per workflow per 24 h (stricter than any policy cap); incident requests may use the policy cap.
10. **`affected_leads`** = the leads dialled under the new setting: the next campaign for approval-path caller-ID changes, leads in flight (batch × cadence days) for prompt changes.
11. **Credit rules:** at most 25 credits per incident and a 30-credit reserve that incidents may not touch.
12. **Detection windows** are chosen per number: the shortest window (2–12 h) in which it has at least 150 dials.

---

## 2. Scope decisions

| # | Capability | Built / Partial / Cut | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Event ingestion at load scale | **Built** | Chunk = unit of idempotency; counters + chunk marker in one transaction. ~13–14k events/s measured on dev scenarios. Load-scenario numbers in §8. | Shard by workflow group across processes if 10× is needed |
| C2 | Lead joins + shared cohort object | **Partial** | Lead features are joined at ingest. No cohort object, because no experiments use one | Cohort table keyed by id, used by detection, experiments and any supervisor view |
| C3 | Bootstrap from history | **Partial** | History is ingested into the same counters and used as baseline context. No schedule is derived from it (hour-of-day looked confounded by attempt mix; `agg_attempt` exists to stratify) | Stratified hour-of-day model per cohort |
| C4 | Call-time optimisation | **Cut** | Per-person timing is too sparse (returning people are a minority with few calls each); per-cohort timing needs the cohort work above | Per-cohort `hour_weights` with all hours kept in the table (omitted hours get weight 0) |
| C5 | Caller-ID health | **Built** | Largest, cheapest, policy-permitted win (auto-apply on two workflows) | Cohort-specific caller-ID handling |
| C6 | Variant experiments | **Cut** | Mandatory evidence (crash, load, write-up) and two incident flows came first | Shared cohort object, pre-set sample sizes from the power table (§5), BH correction, 10–15% holdout |
| C7 | Promotion, decay, rollback | **Cut** | Depends on C6. Caller-ID probation/relapse (§5) is the only rollback-like logic built | Decay test vs the holdout, rollback through governance |
| C8 | Hallucination response | **Built** | Detect → validate → propose with evidence → verify; the LLM is a fallback, not the first step | Wire engagement guardrail check into candidate choice |
| C9 | Governance client | **Built** | Intent-first, deterministic ids, reconcile after unknown outcomes, stale/lapse/expiry paths | Per-tenant "quiet hours" rule (see §3, Ops) |
| C10 | Honest impact, affected leads, dry run | **Partial** | Defined and documented (§6); the dry run is a history summary or validation plus measured engagement, not a counterfactual replay | Replay of cohort rates for allocation changes |
| C11 | Supervisor view | **Partial** | `controller.status` and `controller.audit` CLIs only; no consolidated screen | One HTTP endpoint over the same queries |
| C12 | Audit store + query | **Built** | Append-only `audit` table; every proposal and incident step records its evidence; `python -m controller.why --workflow W --at T` explains a change (why, who approved, what happened next) | Add a per-call variant log for per-customer questions (`controller.why` already explains a change end to end) |
| C13 | Crash recovery, demonstrated | **Built** | Four protocol points killed and restarted, verified against the simulator's records (§9) | Random-instant kill test on Linux with `kill -9` |
| C14 | Loop observability | **Built (basic)** | `loop_metrics` per tick; `status --loop` | Export to the platform's existing metrics stack |
| C15 | Fair allocation of credits and approver attention | **Cut** | A flat per-incident cap and reserve protect the credit pool; approver attention is protected by the daily request cap | Weighted fair queue across tenants |

**Principle:** five capabilities done properly rather than fifteen sketched (C1, C5, C8, C9, C12 plus the crash evidence), chosen by *points per hour and risk to clients*: every one is either policy-permitted (auto-apply) or backed by validation evidence, and none can harm a client without a statistical trigger. Experiments are the most valuable thing I did not build and also the riskiest to rush.

---

## 3. Stakeholders

### 01 Sales (Nikhil Rao)
- **Accepted:** speed where policy permits. Caller-ID changes auto-apply on nw_renewal and ks_cancel; the hallucination fix took 195 minutes.
- **Declined:** a single blended score (pickup and engagement stay separate in every table); pushing winners to everyone by evening; rolling out the "persuasive" prompt.
- **Wrong claims:** (1) *"Kestrel has auto-apply for everything."* ks_cancel allows only `schedule`, `caller_id`, `welcome_message`, `voice`, with a maximum shift of 0.2; `cohorts` and `prompt_version` are not allowed. (2) *"Northwind is fine with auto-apply on KYC."* nw_kyc has `auto_apply.enabled: false`; our nw_kyc change went through an approval. (3) The "persuasive" prompt is described in the catalog as "freer objection handling", which is exactly the hallucination risk; it needs validation evidence and approval everywhere.
- **Reply:** *Nikhil, we can show a lift per KPI, not a blended score: on our incident simulation pickup is +23% and conclusion rate +26%, with 0 harmful changes. We move fast wherever the client's policy allows (caller-ID changes apply automatically; a bad prompt was replaced in about three hours). Two corrections: Kestrel's policy does not allow auto-applying prompts or cohorts, and Northwind's KYC workflow has auto-apply switched off. I'd rather show the room a number a compliance head can sign off than one that makes a client uncomfortable. I also can't roll "persuasive" out anywhere without validation evidence.*

### 02 Client Compliance (Meera Iyer, Northwind)
- **Accepted:** (1) no customer-facing change without human approval: we never auto-apply welcome message, voice or prompt; only caller-ID rotation is automatic, and only where the policy allows it. (2) Script changes carry `validation_run_id` evidence. (4) Slow and right: no experiments on Northwind; incident fixes only after validation. (5) Requests are sent only while approvers are on shift.
- **Partly met (3):** we can answer *which config version and variant policy was live at time T and why it changed* from our audit and config log. We **cannot** answer *what one particular customer heard* from our own store, because we keep counters, not per-call rows. Fix: persist `(lead_id, ts, config_version, cohort, variant)` for regulated tenants, about 650k rows a day (tens of MB).
- **Wrong claim:** "caller-ID rotation is covered by the signed policy" is true for nw_renewal but **not** for nw_kyc (auto-apply disabled). Our nw_kyc caller-ID change went through an approval.
- **Not built:** the weekly summary of operational changes (the audit table has everything needed to generate it).
- **Reply:** *Meera, we accept all five conditions. Customer-facing changes (opening line, voice, script) only ever go through a named approval, and script changes carry validation evidence. Caller-ID rotation is automatic only where your signed policy allows it, which is the renewal workflow and not KYC. Today we can tell an auditor what configuration was live at any time and why it changed; per-customer answers need a call-level log, which I'd add before go-live. One correction to the memo: KYC's policy does not permit automatic rotation, so those changes are requested like any other.*

### 03 Platform & SRE (Kabir Menon)
- **Accepted:** kill/restart evidence, idempotency everywhere, no new stateful infrastructure (SQLite, one process), loop health without reading logs, alerts only when actionable.
- **Pushed back:** nothing. I report honestly where it breaks at 10× (§8).
- **Wrong claims:** none found.
- **Reply:** *Kabir, restart safety is demonstrated, not promised: I killed the controller at four dangerous moments (before a call, after the server accepted a request, after it applied a change, after an auto-apply) and each restart finished with every event counted once and every change applied once, checked against the simulator's own records. State lives in one SQLite file, so there is nothing new to operate at 2 a.m. `status --loop` shows ingest rate, decision lag, memory and credit burn. Page-worthy alerts are limited to failed incidents and reserve breaches. First thing to break at 10× is single-process ingest (§8).*

### 04 Ops supervisor (Anita Desai, Kestrel)
- **Accepted:** never undo her edits behind her back (we only propose, with evidence; we touch one field); batching (at most one routine request per workflow per day; incident requests are separate); the "word for word script" warning (our prompt-candidate screen rejects verbatim/strict/urgency wording).
- **Partly met:** "one screen": CLI views only.
- **Not implemented:** "never before 11:00": we submit from the start of approver hours (10:00). It is a one-line per-tenant setting I did not add.
- **Pushed back:** "tell me before anything changes on a running campaign." Caller-ID rotation is an operational setting inside the policy and is applied automatically; I'd put it in her daily summary rather than pre-announce each step. Customer-facing changes (anything she or her callers would hear) always wait for her approval.
- **Wrong claim:** her "two a day" is stricter than the policy cap of four; we adopt our own stricter cap anyway.
- **Reply:** *Anita, your edits are safe: we never change a prompt on our own, and if we think one is hurting calls we send you a request with the numbers and a tested alternative. In our test, your more aggressive script drew about four times the usual flags; we proposed the previous script, validated at 98.7%, and the change went in only after approval. We send at most one routine request a day per workflow. Caller-ID changes are operational and apply automatically within your policy; they'll appear in a daily summary rather than as pre-announcements. I haven't built the "not before 11:00" rule yet; I'll add it.*

### 05 Product (Tara Kulkarni)
- **Accepted:** pickup and engagement separate everywhere; an expired or rejected proposal is re-queued from fresh data with a new id, never resubmitted; only what actually ran counts (counters are keyed by the `config_version`, cohort and variant carried on each call event); changes affect that workflow only.
- **Pushed back:** three P0s do not fit. Built: caller-ID health. Partly: supervisor view (CLI). Cut: per-person call time. Returning people are 20–40% of leads with a handful of calls each, too sparse to learn from reliably; a headline built on it would be fragile.
- **Open questions answered:** there is no cohort size because no cohorts are used. For caller-ID the minimum is 150 dials per number in the shortest window that has them (2–12 hours); a longer window is slower to react but fewer false flags. Power table (§5): detecting +10% on a 0.55 rate needs about 1,263 per arm.
- **Wrong claims:** "a cohort is one shared object" is moot here; "impact score will be used to rank the queue" is addressed in §6 (it is defined, not arbitrary).
- **Reply:** *Tara, pickup and engagement are never blended, and every record keeps what actually ran. Of your three P0s I built caller-ID health, gave you a first version of the supervisor view (a command-line status and audit), and cut per-person call time: returning people are a minority with few calls each, so a per-person schedule would be noise. Impact scores are defined (§6) so queue ranking means something. I'd put per-cohort call time first after launch.*

### 06 CTO (Aryamann)
- **Accepted:** choose and say no; spend credits only where they change a decision (an incident costs 6 credits; the LLM was not needed in public_02); trust the contract over recollection (every memo claim was checked against `/workflows` policies); write for the person on call (§14).
- **Pushed back:** nothing.
- **Reply:** *The deliberate cuts are experiments and promotion/rollback, per-person timing, a supervisor UI, fair allocation and a per-call log. I'd rather defend five things completely than sketch fifteen. What breaks first at 10× is single-process ingest (§8); the first production risk is that caller-ID exploration spreads load onto numbers I have not yet judged, which cost one harmful change in public_01 (§11).*

---

## 4. Architecture

```
simulator (HTTP only)
  │ GET chunks      POST /sim/advance      POST /approvals · /apply   PUT /config   /validation   /llm
  ▼                       ▲                          ▲
client.py  (retries 5xx; 4xx = answers; advance never blindly retried)
  │
ingest.py   chunk → counters + "chunk done"        ← ONE transaction (exactly once)
  │
SQLite (WAL):  agg · agg_attempt · leads · config_log · approval_log
               proposals · did_state · incidents · credits_log · loop_metrics · audit · meta
  │
main loop, every 15 simulated minutes:
   1 pull new events  →  2 governance.poll  →  3 callerid.tick  →  4 incidents.tick  →  5 advance  →  6 pull new events
                          │                      │                   │
                          └─────── all changes go through governance.py ────────┘
                                   intent row → API call → (reconcile if unsure) → audit
```

- **State** is entirely in one SQLite file: counters, leads, proposals (status machine), per-number health, incidents, credit ledger, metrics and the audit journal.
- **Restart:** `main.py` fingerprints the simulated world by hashing its first history chunk; the same world resumes, a different world archives the old DB and starts clean. `governance.resume()` settles every proposal whose outcome is unknown by asking the simulator's own records (approvals list, config history). Event ingestion resumes from the done-chunks table.
- **What is lost on a crash:** at most one credit-spending call's response (validation/LLM) if killed between the reply and its ledger row. The worst case is re-spending 6 credits, bounded by the 25-credit incident cap.

---

## 5. The learning loop

- **Cohorts:** not built (cut with C6). Counters already carry age band, language and city tier, so cohort-level analysis is available when needed.
- **Experiment design and winner selection:** not built; the statistical toolkit **is** built and tested (`stats.py`): Wilson intervals, two-proportion z-test, sample-size calculator, Benjamini–Hochberg, non-inferiority check. Measured with my own Monte Carlo (`python -m controller.montecarlo`), 3 candidates vs control, 1,263 per arm: **false-winner rate 11.4% without correction, 4.8% with BH**; power to find a real +10% lift is 64% at 1,000 per arm and 96% at 2,526. Samples needed per arm to detect a +10% lift in engagement: ~1,263 overall, ~924 in a 60+ cohort of ~140 connected calls a day (about 13 days), so only large effects on coarse cohorts are detectable in 8–10 day scenarios. This is why the design favours few arms and fixed samples.
- **Decay and rollback:** for the one thing I manage (caller IDs): a flagged number drops to a 3% floor; recovery needs evidence only from **after** the flag (≥300 dials, within 5% of peers, no sign of being worse), then a 12-hour **probation** ramp back to a full share; relapse during probation returns it to *bad*. (The first version used a 24-hour window that still contained the healthy hours before the incident, declared recovery 30 minutes after cutting the number, and caused two harmful changes in the first graded run; the audit trail showed it in a minute.)
- **Call time:** cut. Hour-of-day looked strong in history (pickup 0.235 at 15:00 vs 0.298 at 13:00) but retries are forced ≥2 hours after attempt 1 and so concentrate later in the day, confounding it; I did not ship schedule changes from a confounded table.
- **Caller ID:** each number's pickup is compared with its peers' in the same workflow (the dial's number is sampled independently, so mix and hours match). Flag = one-sided p < 1e-4 **and** ≥20% relative drop **and** two consecutive ticks; the window is the shortest of 2/3/4/6/8/12 hours in which the number has ≥150 dials. Flagged numbers go to a 3% floor; everything else shares evenly, which also gives never-tried numbers a fair trial. Steps are limited to 90% of the policy's `max_shift`.
- **Guardrail KPI:** caller-ID moves act on pickup. The non-inferiority function exists but is **not wired into caller-ID decisions**; in public_02 nw_kyc (primary pickup, guardrail engagement) showed engagement −2.3% relative (−1.4 pp absolute) while pickup rose 25.7%, plausibly because more marginal callers now pick up. Within a 2 pp absolute guardrail, but I did not guarantee it by design (§13).

---

## 6. Governance

**Proposal state machine** (rows in `proposals`; every transition is also an audit row):

`intent → pending → approved → applying → applied`
`intent → blocked` (our local policy check refused it before sending) · `pending → rejected | expired | stale` · `approved → lapsed | stale` · `intent/applying → (reconciled against config history) → applied`

- **Stale base** (a supervisor edit moved the version): the proposal ends `stale`; the next evaluation recomputes from the live config and re-proposes with a new id. **Expired/rejected:** treated as a rejection; re-queued from fresh data under a new id (incident proposals retry up to 3 times).
- **Batching and timing:** routine requests only while approvers are on shift, at most one per workflow per 24 h; incident requests may use the policy cap. Approved changes are applied on the first tick after approval (apply windows are 2 hours; observed latency ≤15 simulated minutes).
- **Auto-apply, when:** only for caller-ID, and only if the workflow's policy lists it and the step stays within 90% of `max_shift`; a local check mirrors the policy so a 403 is never logged against us. **Voluntarily not:** anything a customer hears (welcome, voice, prompt, script), and any change to a supervisor-set value.
- **Impact score** (in [0,1]): a removal of a confirmed-bad number = `min(1, 0.4 + 0.6 × relative pickup gap)`; an untested rebalance is capped at 0.25 (`0.5 × TV distance`); a prompt fix = `min(1, 0.3 + 10 × (flag rate − baseline))`. **Affected leads:** see assumption 10. **Dry run:** the history summary or validation run plus our own measured engagement for a candidate that has run before; for a never-run candidate the engagement is stated as unknown. It is not a counterfactual replay.
- **Supervisor edits mid-experiment:** logged from the `config_changed` event; stale proposals re-derive from the live config; counts remain attributed to the config version actually used.

---

## 7. Incidents

**Detection thresholds**

| Incident | Rule | Expected false positives |
|---|---|---|
| Hallucination spike | Current-prompt flag rate over 3 h vs the workflow's own baseline: ≥150 connected calls, ≥8 flags, ≥2× baseline, exact binomial p < 1e-4 | About 1e-4 per independent window, roughly 10 independent windows a day, so ~1e-3 per workflow-day, bounded in cost: ≤6 credits and one approval request, never auto-applied |
| Bad caller ID | As in §5 | The p-value does most of the work (strikes are correlated); in public_02 the only flagged number was the real one |

**Choosing a fix:** the cheapest known-good option first: validate the previous production prompt (6 credits). Only if it fails does the loop spend 3 credits on LLM suggestions, which are screened (whole-word match against the catalog text for verbatim, strict, persuasive, urgency, aggressive, freer), validated (up to two), and independently re-checked by the approver. **The LLM never decides what is chosen.** If there is no previous prompt, the loop needs p < 1e-6 before it will propose changing a customer-facing prompt at all.

**Verification after apply:** the flag rate under the new prompt is re-measured; the incident closes only when it is back near baseline, otherwise a page-level alert row is written.

**Timeline, public_02 (from the audit table):**

| Sim time | Event |
|---|---|
| D2 10:00 | ks_cancel caller ID D2 spam-flagged (incident) |
| D2 11:15 / 11:30 | strike 1, then flagged *bad* (12.3% pickup vs 26.7%, n=203, p=2.7e-5) |
| D2 11:30 / 11:45 | D2 cut 0.33 → 0.15, then to the 3% floor: **1 h 45 m after onset** |
| D3 11:30 | supervisor ships prompt P2 |
| D3 13:30 | spike detected: 5.6% flags (12 of 214 calls) vs 1.4% baseline, p = 7.9e-5 |
| D3 13:30 | previous prompt P1 validated at 98.67% (needs 97%): 6 credits; approval request sent |
| D3 14:45 | approved and applied (**MTTR 195 minutes**, of which ~75 are approver latency) |
| D3 16:30 | verified: 0 flags in 160 calls, engagement 56%; incident resolved |

Credits: 6 of 200. Detection time is bounded by volume: at ks_cancel's ~100 connected calls an hour, eight flags at a 5.6% rate need about 1.4 hours. Lowering the 8-flag floor would speed this up at the cost of more false alarms.

---

## 8. Scale

**Measured on the dev scenarios** (`python -m controller.status --loop`):

| | public_01 (8 days) | public_02 (10 days) |
|---|---|---|
| Ingest throughput | 13.0k events/s | 14.1k events/s |
| Decision lag (events arrive → decisions made) p50 / p95 / max | 17 / 29 / 57 ms | 17 / 28 / 56 ms |
| Peak memory | 84 MB | 84 MB |
| Wall-clock for the whole scenario | 24 s | 28 s |

**Measured on public_03_load** (12 workflows, 4,500 dials/hour each, 5 days): [FILL from `reports/public_03_load.loop.txt`: events/s, decision lag, peak memory, wall-clock per simulated day. Target: under 10 minutes per simulated day on 4 vCPU / 8 GB.]

**What breaks first at 10×:** single-process ingest. At the measured ~13k events/s, 850k events/day is about a minute; 8.5M events/day is about eleven minutes, over the 10-minute budget, and SQLite's single writer becomes the next ceiling. Plan: (1) a substring pre-filter before `json.loads`; (2) shard workflow groups across processes (4 vCPU), each with its own database; (3) move aggregates to Postgres only if cross-shard queries are needed. The aggregate table grows with config versions, so a retention job for old hours is also needed.

---

## 9. Reliability

**Crash-recovery test** (`bash scripts/crash_test.sh public_02_incidents <point>`; evidence in `reports/crash_recovery_<point>.txt`). The process is killed with exit code 137 (`os._exit`: no cleanup, equivalent to `kill -9` for SQLite durability) at one protocol moment, restarted on the same database and world, and then checked against the **simulator's** records:

| Kill point | What the server had done | Restart behaviour (from the audit) | Result |
|---|---|---|---|
| `after_intent` | nothing yet | intent row found, request sent | PASS |
| `after_put` (auto-apply) | applied the change | "confirmed in config history", not re-sent | PASS |
| `after_post` (approval) | accepted the request | "recovered after duplicate" via the 409 | PASS |
| `after_apply` | applied the approved change | reconciled from history | PASS |

Each run checks: SQLite integrity; **every event counted exactly once** (324,316 counted = 324,316 emitted by the simulator); per workflow the controller's versions in the simulator's history equal our applied proposals one-to-one (no duplicates, none lost); no proposal left half-done.

**Idempotency keys:** proposal ids are `ctl-<workflow>-v<base>-<hash(config)>`; the same change at the same base can never be sent twice; a deliberately fresh retry uses a different id.

**When the simulator returns 4xx/5xx:** 5xx and network errors are retried with backoff (6 tries, 0.5 s → 8 s). 4xx are answers (`stale_base_version`, `duplicate_proposal_id`, 402 out of credits, 403 policy) and map to proposal states; none is retried blindly. `advance` is never retried blindly: after a failure the client re-reads the clock. A failing component is logged and retried next tick without stopping the others.

**Honest limits:** the kills are injected at chosen points rather than at random instants; the transactional design covers random instants, but I did not demonstrate that separately.

---

## 10. Audit & observability

**"Why did ks_cancel change at D3 14:45, who approved it, what happened after?"**

```
$ python -m controller.audit --workflow ks_cancel
#21  2027-01-07T13:30  incident_detected   {"prompt":"P2","connected":214,"flags":12,"rate":0.056,"baseline_rate":0.0145,"baseline_n":8166,"p_value":7.9e-05,...}
#23  2027-01-07T13:30  credits_spent       {"endpoint":"validation","cost":6,"ref":"P1 n=600"}
#24  2027-01-07T13:30  incident_validating {"last_validation":{"prompt":"P1","pass_rate":0.9867,"run_id":"val_00001","threshold":0.97}}
#26  2027-01-07T13:30  proposal_intent     {"summary":"Replace prompt P2 with P1 (hallucination spike)","rationale":"P2 (set by a supervisor) produces hallucination flags on 5.6% of calls versus 1.4% before ... This is a proposal, not an override."}
#27  2027-01-07T13:30  proposal_pending    {"approval_id":"apr_00002","expires_at":"2027-01-08T01:30"}
#29  2027-01-07T14:45  proposal_approved   {"apply_by":"2027-01-07T16:18"}
#31  2027-01-07T14:45  proposal_applied    {"applied_version":7}
#33  2027-01-07T16:30  incident_resolved   {"after":{"prompt":"P1","connected":160,"flags":0,"engagement":0.5625},"minutes_from_detection_to_verified":180}
```

This answers *why* (evidence and rationale), *who approved* (approval id `apr_00002`; the approver is the simulator's, decided 14:45), and *what happened next* (verified resolution).

The same answer in one command, built for the review:

```
python -m controller.why --workflow ks_cancel --at 2027-01-07T14:45:00+05:30
```

[FILL: paste the real output of this command here after your final public_02 run: it prints WHY (summary, rationale, impact, evidence), WHO/WHEN (decision trail with the approval id), and WHAT HAPPENED NEXT (pickup, engagement and flag rate 6 hours before and after, the verification, and supervisor edits seen).]

**Loop health** without logs: `python -m controller.status --loop` (ingest events/s, decision lag, peak memory, decisions per sim-hour, credits used).

**Page-worthy alerts** (`alert` rows with `severity=page`): a hallucination incident that failed or ran out of credit budget (needs a human). **Not paged** (self-healing or morning work): rejected proposals, stale bases, expiries, a caller ID flagged and handled, approver queue delays. **Not yet built:** an alert for a proposal stuck in `intent`/`applying` for over an hour.

---

## 11. Results

**public_02_incidents** (controller vs no-op baseline, `reports/public_02_incidents.md`):

| workflow | conclusion | pickup | engagement | engagement gap captured | hallucinated calls (baseline) |
|---|---|---|---|---|---|
| ks_cancel | +45.8% | +34.8% | **+4.5%** | 30% | 89 (330) |
| nw_kyc | +24.6% | +25.7% | −2.3% | 0% | 45 (31) |
| nw_renewal | +12.0% | +11.3% | −0.0% | 5% | 56 (55) |
| ALL | +26.4% | +23.3% | +0.7% | 15% | 190 (416) |

8 changes applied: **7 beneficial, 1 neutral, 0 harmful**; 0 auto-apply rejections, 0 apply conflicts, 0 lapses; MTTR 195 minutes; 6 credits.

**public_01_dev** (earlier run, before the adaptive-window fix below):

| workflow | conclusion | pickup | engagement |
|---|---|---|---|
| ks_cancel | +10.2% | +9.2% | +0.4% |
| nw_kyc | +31.8% | +25.7% | +1.8% |
| nw_renewal | **−2.0%** | **−2.4%** | +0.4% |
| ALL | +10.1% | +8.0% | +1.0% |

5 changes: 3 beneficial, 1 neutral, **1 harmful** (nw_renewal v2: the first-day spread onto four numbers, one of which was already spam-flagged).

**Interpretation:**
- Where I helped: pickup on all three workflows in public_02 through load balancing across numbers and removing the flagged one fast; ks_cancel engagement through replacing the bad prompt.
- Where I was harmful, and why: in public_01, nw_renewal ended below baseline. The loop spread traffic evenly over four numbers immediately, one of which was already bad, and then **never flagged it**: my detector required 150 dials per number inside a 2-hour window, and at nw_renewal's volume (300 dials/hour over four numbers) each number only reaches about 150 in two hours, so judgement was skipped about half the time. Only the busier ks_cancel qualified. **Fix:** each number is now judged on the shortest of six windows (2–12 h) in which it has enough data, with regression tests. [FILL: rerun public_01 and public_02 after the fix and replace the table above with the final numbers.]
- What I'd change next: ramp never-tried numbers in gradually instead of spreading evenly on day one, and wire the guardrail check into caller-ID decisions.

**public_03_load:** [FILL the grade table if you ran it, and refer to §8 for throughput.]

---

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / what instead |
|---|---|---|
| Postgres + Redis | Already in production at Formi | SRE asked for no new stateful infrastructure; one SQLite file is enough at this rate (§8 says when it stops being enough) |
| Store raw events | Full replay | ~850k events/day; counters give the same rates in a tiny database. Cost: no per-call audit (§13) |
| Thompson sampling / bandit for caller IDs | Explores and exploits automatically | Hard to explain to a compliance reviewer and non-deterministic; chose thresholds with a floor |
| Weight numbers by their pickup | Looks optimal | Concentrates load, which burns numbers; even spread plus a floor is simpler and safer |
| Always ask the LLM for a fix | Matches the brief's "LLM suggests" | The previous prompt is known and cheaper; spend credits only where they change a decision |
| Auto-apply prompt fixes | Faster | Policy forbids it on these workflows and a supervisor's edit must not be silently undone |
| Normal approximation for flag counts | Simple | Flag counts are rare; used an exact binomial tail |
| Random-instant `kill -9` test | Most convincing | Not reproducible; used deterministic injection at the dangerous protocol moments |
| Per-person call timing | Product's headline | Too sparse; per-cohort timing first |

---

## 13. Known weaknesses

1. **Experiments are not built,** so engagement-gap capture is low (15% overall in public_02, 5% on nw_renewal). The biggest lever I left on the table.
2. **First-day exploration spreads blindly** onto numbers that were never judged; in public_01 that was one harmful change and a pickup loss on nw_renewal. A gradual ramp would limit this.
3. **The caller-ID detector missed a bad number** on a low-volume workflow until the adaptive-window fix; post-fix numbers need confirming on a fresh run.
4. **The engagement guardrail is not enforced in caller-ID decisions;** nw_kyc engagement fell 2.3% relative in public_02.
5. **No per-call variant log,** so a per-customer audit question (Compliance condition 3) cannot be answered from our own store.
6. **Hallucination detection speed is volume-bound:** about 2 hours on a busy workflow, much longer on a quiet one; MTTR also includes approver latency I cannot control.
7. **Dry run and impact score are heuristic;** there is no counterfactual replay, and the prompt-fix engagement for a never-run candidate is unknown.
8. **"Not before 11:00" (Ops) and the weekly summary (Compliance) are not implemented.**
9. **Crash demonstration uses injected kill points,** not random-instant kills; memory measurement on Windows uses `ctypes` and may report none on other platforms.
10. **The aggregate table grows without a retention job.**

---

## 14. If this went to production at Formi next month

1. **Days 1–2:** read-only shadow mode against live traffic: ingest and decide, but write proposals to a log instead of calling the API; compare against what supervisors do by hand.
2. **Days 3–5:** add the per-call variant log for regulated tenants, the "quiet hours" setting and the weekly operational summary; a stuck-proposal alert.
3. **Week 2:** enable caller-ID auto-apply on one low-risk workflow with the engagement guardrail wired in; alert thresholds and a runbook ("an incident failed: here is what to check"); Prometheus export of `loop_metrics`.
4. **Week 2–3:** load-test at 10× with workflow-group sharding; add retention for old aggregate hours.
5. **Then:** the experiment engine (cohort object, fixed samples, BH correction, holdout, decay test) behind the same governance client.

*How to run:* `pip install -r requirements.txt`; `./run.sh <SIM_URL>` (Python 3.11+; state in `controller_state.db`, delete it for a clean start); `bash scripts/eval.sh <scenario>` runs baseline, controller, grade and audit end to end; `bash scripts/crash_test.sh <scenario> <point>` runs the kill/restart proof; `python -m controller.why --workflow <id> --at <time>` explains any change from the audit store. On Windows set `PYTHONUTF8=1` (or use `python -X utf8`) before `sim grade`.