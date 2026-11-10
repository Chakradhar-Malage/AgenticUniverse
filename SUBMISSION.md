# Outcome-First Agent Loop — Submission

**Name:** Chakradhar Malage · **Date:** 9 Nov 2026 · **Language / storage:** Python 3.12, SQLite (WAL). Dependencies: `httpx` (HTTP). `numpy` is used only by the Monte Carlo script, not by the loop.

> **How I worked:** I used an AI assistant for design discussion, few code files and drafting this document. I ran every test and every scenario myself, and the decisions in §6 and §12 are ones I can explain without it. AI was used to reduce time effort and to avoid silly human mistakes.

---

## 0. TL;DR

- A single-process controller (Python + SQLite) ingests every event exactly once, detects bad caller IDs and hallucination spikes from **populations** (never single calls), and changes a workflow only through governance: auto-apply where the policy allows it, otherwise a batched approval request. Every decision is written to our own audit table.
- **public_02:** pickup **+23.3%**, conclusion rate **+26.4%** vs baseline; 8 changes applied (7 beneficial, 1 neutral, **0 harmful**); the supervisor's bad prompt was replaced in **195 minutes** for **6 credits**; ks_cancel engagement went from −3.8% (before the fix) to **+4.5%**.
- **Crash recovery:** killed at four protocol moments (before the call, after the server accepted, after the server applied, after auto-apply). Four of four restarts finished the run with every event counted once and every change applied once.
- **Deliberately not built:** variant experiments and promotion/rollback (C6/C7), call-time optimisation (C4), a supervisor UI (C11), fair credit allocation (C15), a per-call variant log. Reasons in §2.
- **public_01 and public_03:** public_01 conclusion **+12.7%**, pickup +10.9% (1 harmful change of 6). public_03_load (12 workflows, ~850k events/day): conclusion **+16.4%**, pickup +13.5%; 28 changes (20 beneficial, 4 neutral, 4 harmful); 5 simulated days in **290 s** wall-clock at **56k events/s**, peak **788 MB**.
- **Known miss, found and fixed late:** the first public_01 run never caught a spam-flagged caller ID on a low-volume workflow (nw_renewal pickup −2.4%). After the fix it is **+4.7%** and overall conclusion rose from +10.1% to +12.7% (§11). The remaining harmful changes are the blind first-day load spreading (§13).

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
| C1 | Event ingestion at load scale | **Built** | Chunk = unit of idempotency; counters + chunk marker in one transaction. 12–14k events/s on the dev scenarios and 56k events/s on the load scenario (§8). | Shard by workflow group across processes if 10× is needed |
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
- **Reply:** *Kabir, restart safety is demonstrated, not promised: I killed the controller at four dangerous moments (before a call, after the server accepted a request, after it applied a change, after an auto-apply) and each restart finished with every event counted once and every change applied once, checked against the simulator's own records. State lives in one SQLite file, so there is nothing new to operate at 2 a.m. `status --loop` shows ingest rate, decision lag, memory and credit burn. Page-worthy alerts are limited to failed incidents and reserve breaches. On the load scenario five simulated days took 290 s wall-clock (about 38 s per simulated day including the simulator's own work) at 56k events/s, peak memory 788 MB, p95 decision lag 0.48 s. At 10× the first wall is memory, then per-tick decision time (§8).*

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
- **Reply:** *The deliberate cuts are experiments and promotion/rollback, per-person timing, a supervisor UI, fair allocation and a per-call log. I'd rather defend five things completely than sketch fifteen. What breaks first at 10× is memory, then per-tick decision time (§8); the first production risk is that caller-ID exploration spreads load onto numbers I have not yet judged, which cost one harmful change in public_01 and four in public_03 (§11).*

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

**Measured** (`python -m controller.status --loop`, laptop, final code):

| | public_01 (8 days) | public_02 (10 days) | **public_03_load (5 days, 12 workflows)** |
|---|---|---|---|
| Events ingested (live + history) | 109,631 + 187,400 | 137,042 + 187,274 | **4,200,098 + 5,537,379** (~840k live events/day) |
| Ingest throughput | 11.6k events/s | 11.7k events/s | **56.1k events/s** |
| Decision lag (events arrive → decisions made) p50 / p95 / max | 21 / 33 / 62 ms | 19 / 40 / 75 ms | **0.25 / 0.48 / 2.8 s** |
| Peak memory | 83 MB | 85 MB | **788 MB** |
| Wall-clock, whole run | 27 s | 34 s | **290 s** (history ingest + 5 simulated days) |
| Loop time per simulated day | ~3 s | ~3 s | **~38 s** (of which ~22 s controller, ~16 s the simulator advancing) |
| Changes applied / credits used | 6 / 0 | 8 / 6 | 28 / 0 |

The SRE target (one simulated day of the load scenario in under 10 wall-clock minutes on 4 vCPU / 8 GB) is met about 15× over on my laptop. I did not run on a 4-vCPU/8 GB machine; the numbers above are from a laptop, so treat the margin as indicative.

**What breaks first at 10× (linear extrapolation, not measured):**
1. **Memory.** 788 MB at 12 workflows; the loop reads each chunk fully into memory and keeps a lead-feature cache, so memory grows with events per chunk and with leads. Projected to approach the 8 GB box at 10×. Fix: stream chunks line by line instead of loading the whole body, and bound the lead cache (fall back to SQLite lookups).
2. **Per-tick decision time.** Median 0.25 s at 12 workflows is about 2.5 s at 120, so about 4 minutes of decision time per simulated day at 96 ticks. Fix: cache each workflow's config by version from `config_changed` events instead of one `GET /config` per workflow per tick, and skip workflows with no new data.
3. **Single-process ingest.** 56k events/s: 8.5M events a day is about 2.5 minutes. Not the first wall, but together with (1) and (2) a day would be near the 10-minute budget.
4. **SQLite's single writer** and the growing `agg` table (the 7-day hallucination baseline is the heaviest query).

**Partitioning:** hash workflows to N worker processes (4 vCPU → 4), each with its own SQLite file; governance, caller-ID and incident logic are all per workflow, so there is no cross-shard coordination. Tenant-level fairness (C15) would sit above the shards. Move aggregates to Postgres only if a cross-shard view is needed.

---

## 9. Reliability

**Crash-recovery test** (`bash scripts/crash_test.sh public_02_incidents <point>`; evidence in `reports/crash_recovery_<point>.txt`). The process is killed with exit code 137 (`os._exit`: no cleanup, equivalent to `kill -9` for SQLite durability) at one protocol moment, restarted on the same database and world, and then checked against the **simulator's** records:

| Kill point | What the server had done | Restart behaviour (from the audit) | Result |
|---|---|---|---|
| `after_intent` | nothing yet | intent row found, request sent | PASS |
| `after_put` (auto-apply) | applied the change | "confirmed in config history", not re-sent | PASS |
| `after_post` (approval) | accepted the request | "recovered after duplicate" via the 409 | PASS |
| `after_apply` | applied the approved change | reconciled from history | PASS |

All four runs were repeated on the final code. Each run checks: SQLite integrity; **every event counted exactly once** (324,316 counted = 324,316 emitted by the simulator); per workflow the controller's versions in the simulator's history equal our applied proposals one-to-one (no duplicates, none lost); no proposal left half-done.

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

Real output (`reports/why_ks_cancel_D3.txt`, from the final run):

```
WORKFLOW ks_cancel: config version 7 was applied at 2027-01-07T14:45:00+05:30
  route: approval path (a named human approver decided)

1. WHY (the controller's recorded reasoning at decision time)
   summary: "Replace prompt P2 with P1 (hallucination spike)"
   rationale: "P2 (set by a supervisor) produces hallucination flags on 5.6% of calls versus 1.4% before (12 of 214 calls, p=7.9e-05). Proposed P1, validated at 98.7% (needs 97%). This is a proposal, not an override."
   impact_score: 0.716
   affected_leads: 4500
   diff: {"prompt_version": {"from": "P2", "to": "P1"}}
   dry_run: {"method": "validation run + own measured history (no counterfactual replay is possible for a prompt)", "current_prompt": "P2", "current_flag_rate": 0.0561, "baseline_flag_rate": 0.0145, "validation": {"run_id": "val_00001", "pass_rate": 0.9867, "n": 600, "required": 0.97}, "engagement_current_prompt_24h": 0.4626, "engagement_candidate_last_7d": 0.4923, "caveat": "validation measures safety only; engagement of a never-run candidate is unknown"}
   evidence: {"validation_run_id": "val_00001"}
   incident hal-ks_cancel-85 opened 2027-01-07T13:30:00+05:30: prompt P2 flagged on 5.6% of calls vs 1.4% baseline (p=7.9e-05); credits spent 6

2. WHO / WHEN (decision trail)
   2027-01-07T13:30:00+05:30  proposal_intent
   2027-01-07T13:30:00+05:30  proposal_pending     apr_00002
   2027-01-07T14:45:00+05:30  proposal_approved    {"apply_by":"2027-01-07T16:18:00+05:30"}
   2027-01-07T14:45:00+05:30  proposal_applying
   2027-01-07T14:45:00+05:30  proposal_applied     7
   approval id: apr_00002

3. WHAT HAPPENED NEXT
   6 hours before: pickup 0.266 (531/2000 dials) | engagement 0.512 (272/531 connected) | hallucination flags 30.1 per 1,000 connected
   6 hours after : pickup 0.235 (563/2400 dials) | engagement 0.526 (296/563 connected) | hallucination flags 12.4 per 1,000 connected
   verification: after the fix, flag rate 0.0% on 160 calls, engagement 56.2%
   next decision: 2027-01-07T16:30:00+05:30  incident_resolved

4. SUPERVISOR EDITS SEEN ON THIS WORKFLOW (we never overwrite or revert them silently)
   2027-01-07T11:30:00+05:30  version 6  changed ["prompt_version"]
```

How to read it: *why* (evidence, validated alternative, impact score 0.716, 4,500 affected leads, honest dry run that says engagement of a never-run candidate is unknown), *who* (approval `apr_00002`, decided 14:45, applied inside its window), *what happened next* (flags per 1,000 calls fell from 30.1 to 12.4 and engagement rose from 0.512 to 0.526). Pickup is lower in the "after" window (0.235 vs 0.266) only because those six hours cover the late-afternoon dip and the later-attempt mix seen in the history; a prompt change does not act on pickup, so flags and engagement are the relevant measures. The supervisor's own edit (version 6, 11:30) is listed and was never silently reverted: the fix is a separate, approved version 7.

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

**public_01_dev** (final code, `reports/public_01_dev.md`):

| workflow | conclusion | pickup | engagement |
|---|---|---|---|
| ks_cancel | +9.8% | +8.2% | +1.4% |
| nw_kyc | +33.8% | +29.1% | +1.0% |
| nw_renewal | +4.1% | +4.7% | −0.7% |
| ALL | **+12.7%** | **+10.9%** | +0.9% |

6 changes: **4 beneficial, 1 neutral, 1 harmful** (nw_renewal v2, the blind first-day spread). The new beneficial change nw_renewal v4 is the controller finally cutting the degraded number. **Before the adaptive-window fix** the same scenario gave ALL conclusion +10.1% / pickup +8.0% and nw_renewal −2.0% / −2.4% (5 changes, 1 harmful).

**public_03_load** (12 workflows, 4 tenants × renewal/KYC/cancel; `reports/public_03_load.md`): ALL conclusion **+16.4%**, pickup **+13.5%**, engagement +0.6%.

| use case (4 workflows each) | conclusion | pickup | engagement |
|---|---|---|---|
| renewal | +5.9% to +8.2% | +5.8% to +6.4% | −0.6% to +1.3% |
| cancel | +6.7% to +7.9% | +7.1% to +8.2% | −0.6% to +0.2% |
| KYC | +27.6% to +39.5% | +22.1% to +32.7% | −0.5% to +0.3% |

28 changes: **20 beneficial, 4 neutral, 4 harmful**. Governance: 10 approval requests (8 approved, 2 rejected as "judgement call" by the simulated approver, 0 expired, 0 lapsed), 20 auto-applies, 0 conflicts, peak 1 request per 24 h per workflow. No incidents in this scenario, 0 credits used.

**Interpretation:**
- **Where I helped:** pickup on every workflow in all three scenarios (caller-ID load balancing and removing degraded numbers), and the hallucination incident in public_02, which also recovered `ks_cancel` engagement.
- **Where I was harmful:** all 5 harmful changes across public_01 and public_03 are the same change: the controller's first action, before any data exists, spreads traffic evenly over never-used numbers. On the four renewal workflows one of those numbers is already degraded (per the public scenario's documented truth, used here only to explain the result after the fact, never by the controller), and the first-day spread moves 25% of traffic onto it. The adaptive-window detector then flags and cuts it, which the grader labels beneficial (v4 +0.10), but the first-day cost remains.
- **The earlier miss:** a fixed 2-hour window with a 150-dial minimum skipped judgement about half the time on quiet workflows (about 75 dials an hour per number), so the degraded number was never flagged. Fixed with per-number windows of 2 to 12 hours and a regression test that fails on the old behaviour. It worked: nw_renewal in public_01 went from −2.4% to +4.7% pickup.
- **Engagement guardrail on nw_kyc:** −2.3% relative in public_02 is about −1.4 points absolute, inside a 2-point guardrail if the guardrail is absolute (assumption 1). The grader did not flag a violation, but I did not verify its exact definition.
- **Hallucinated-call counts** in the grader's table rise on KYC workflows (e.g. 45 vs 31) because many more calls connect (pickup +25 to +33%); I did not separate rate from count in my own reporting, so I cannot claim the rate is unchanged from this table alone.
- **Engagement gap captured is 0 to 15%**, the cost of cutting the experiment engine.
- **What I'd change next:** ramp never-tried numbers in gradually instead of spreading evenly on day one, and wire the guardrail check into caller-ID decisions.

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
2. **First-day exploration spreads blindly** onto numbers that were never judged: 1 of 6 changes harmful in public_01 and 4 of 28 in public_03 (one per renewal workflow). The adaptive-window detector later cuts the degraded number, but the first-day cost remains. A gradual ramp would limit it.
3. **The caller-ID detector missed a bad number** on low-volume workflows until the adaptive-window fix. Confirmed on the rerun: public_01 nw_renewal pickup went from −2.4% to +4.7%.
4. **The engagement guardrail is not enforced in caller-ID decisions;** nw_kyc engagement fell 2.3% relative in public_02.
5. **No per-call variant log,** so a per-customer audit question (Compliance condition 3) cannot be answered from our own store.
6. **Hallucination detection speed is volume-bound:** about 2 hours on a busy workflow, much longer on a quiet one; MTTR also includes approver latency I cannot control.
7. **Dry run and impact score are heuristic;** there is no counterfactual replay, and the prompt-fix engagement for a never-run candidate is unknown.
8. **"Not before 11:00" (Ops) and the weekly summary (Compliance) are not implemented.**
9. **Crash demonstration uses injected kill points,** not random-instant kills; memory measurement on Windows uses `ctypes` and may report none on other platforms.
10. **The aggregate table grows without a retention job.**
11. **Memory and tail latency:** peak 788 MB on public_03 (whole chunks are read into memory) and a worst decision lag of 2.8 s; projected to approach the 8 GB box at 10×. I did not run on a 4-vCPU / 8 GB machine.
12. **Approver rejections** (2 of 10 requests in public_03) are retried with fresh data the next day; I did not measure how much value is lost to that delay.

---

## 14. If this went to production at Formi next month

1. **Days 1–2:** read-only shadow mode against live traffic: ingest and decide, but write proposals to a log instead of calling the API; compare against what supervisors do by hand.
2. **Days 3–5:** add the per-call variant log for regulated tenants, the "quiet hours" setting and the weekly operational summary; a stuck-proposal alert.
3. **Week 2:** enable caller-ID auto-apply on one low-risk workflow with the engagement guardrail wired in; alert thresholds and a runbook ("an incident failed: here is what to check"); Prometheus export of `loop_metrics`.
4. **Week 2–3:** load-test at 10× with workflow-group sharding; add retention for old aggregate hours.
5. **Then:** the experiment engine (cohort object, fixed samples, BH correction, holdout, decay test) behind the same governance client.

*How to run:* `pip install -r requirements.txt`; `./run.sh <SIM_URL>` (Python 3.11+; state in `controller_state.db`, delete it for a clean start); `bash scripts/eval.sh <scenario>` runs baseline, controller, grade and audit end to end; `bash scripts/crash_test.sh <scenario> <point>` runs the kill/restart proof; `python -m controller.why --workflow <id> --at <time>` explains any change from the audit store. On Windows set `PYTHONUTF8=1` (or use `python -X utf8`) before `sim grade`.