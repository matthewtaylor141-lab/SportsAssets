# Audit: the learning engine, area by area

**Method.** The running deployment (`workers/all.py`), every `CREATE
TABLE` under `backend/`, an import scan for machine-learning
dependencies, the module headers of every relevant layer, and one
read-only production query (`render-ops sql cohort-inventory`, run
35858684521, read 2026-09-23T12:10:17Z).

**Verdict key.** `RUNNING` = deployed and executing in production.
`IMPLEMENTED` = code exists and is tested but is not deployed.
`RESEARCH` = exists under `research/`, not importable from a deployed
path. `MISSING` = does not exist.

---

## 0. The single most important measurement

`trades` holds **6,106,954 cohort fills** across **22 accounts**, from
2024-11-16 to the moment of the read, with **47,412 in the last 24
hours** and **366,380 in the last 7 days**. Ingestion is live.

But `trades` carries two clocks — `ts` (the fill) and `detected_at`
(when our pipeline first saw it) — and the gap between them is not
small:

| Account | Fills | Markets | Active days | Sells | **Median detection lag** |
|---|---:|---:|---:|---:|---|
| RN1 | 1,119,767 | 58,233 | 241 | 64 | **431 s** |
| nigiri99 | 435,983 | 22,345 | 83 | 1,514 | **265 s** |
| 0x076daa87 | 171,148 | 29,721 | 96 | 0 | **370 s** |
| 0x99a093burst | 660,447 | 9,197 | 137 | 22,006 | **41 min** |
| DoNotTailMe | 6,853 | 447 | 41 | 171 | **11.5 h** |
| HomeRunHazard | 650,271 | 34,423 | 149 | 0 | **31 days** |
| ferrariChampions2026 | 908,046 | 35,120 | 98 | 0 | **87 days** |
| kch123 | 174,941 | 2,604 | 280 | 99 | **200 days** |
| swisstony | 889,154 | 46,321 | 152 | 0 | **229 days** |

**Three of the four named case studies were backfilled, not observed.**
Ferrari, swisstony and kch123 entered our database weeks to months
after the fills happened. HomeRunHazard likewise.

This does not make them useless — they remain excellent material for
learning **behaviour** and reconstructing **economics**. It makes one
specific claim unavailable: nothing trained on those rows can support a
statement about acting in time, because our system did not know those
events until long after any action was possible. The mandate asks for
exactly this separation and it is now measured rather than assumed.

The **prospectively actionable** subset is RN1, nigiri99, 0x076daa87
and 0x99a093burst. Even the best of those is a ~4½-minute median lag,
which is a hard ceiling on any strategy conditioned on cohort activity.

**A second structural fact.** Ferrari, swisstony, HomeRunHazard,
0x076daa87, SDTrading and casualbet2020 have **zero SELL fills**. On a
binary venue an account closes by **buying the complement**, so exits
must be reconstructed from complement purchases. `whale_pairs.py`
already establishes this; the corpus confirms it at scale.

---

## 1. Continuous cohort activity ingestion — **RUNNING**

| | |
|---|---|
| Path | `workers/poller.py` → `ingestion/poller.py`; `workers/chain_listener.py` → `ingestion/chain.py` |
| Deployed | yes, `workers/all.py` LOOPS indices 0 and 1 |
| Evidence | 47,412 fills in the last 24 h; `last = 2026-09-23 12:09:49+00`, i.e. seconds before the read |
| Dedup | `trades.dedupe_key TEXT NOT NULL UNIQUE` — enforced by the database, not by a skippable code path |
| Two clocks | `ts` and `detected_at`, both NOT NULL and both populated on all 6.1 M rows |

**Reuse, do not rebuild.** This is the strongest component in the
system and every learning dataset below is built on it.

**Gap:** there is no *gap detector*. Nothing measures or records
periods when the poller was down, so a quiet stretch in `trades` is
indistinguishable from an outage. That is being added.

## 2. Historical position and cash-flow reconstruction — **RESEARCH**

| | |
|---|---|
| Path | `research/beta48/shadow/whale_pairs.py` (916 lines), `inventory_state.py`, `position_state.py` |
| Production twin | `bettor_inventory.py` — but its header states the arithmetic lives in the research module |
| Deployed | no |
| Tested | yes, under `research/beta48/shadow/test_whale_pairs.py`, `test_inventory.py`, `test_position_state.py` |

`shadow_positions` and `shadow_position_events` hold **0 rows**. The
position-lifecycle machinery (`shadow_position_lifecycle.py`, which
folds state from append-only events rather than storing it) has never
run against real data.

## 3. Behavioral model training — **MISSING**, now being built

This is the core of the mandate and it did not exist:

| Check | Result |
|---|---|
| `sklearn` / `torch` / `xgboost` / `lightgbm` / `statsmodels` under `backend/` | none |
| `numpy` installed anywhere | absent, and not a dependency |
| Production code fitting a parameter to data | none. The only `.fit(` calls in the repository are `research/beta48/shadow/p_bettor_independent_v{2,3}.py` |
| `bettor_exit_ml.py` | its own header: *"BUILT NOW, TRAINED LATER, NEVER GUESSED"* |
| `bettor_p_fill.py` | P_FILL is `NOT_IDENTIFIED` |

`shadow_policy_versions` versions **hand-written policies**. A rule with
a version number is not a trained model and this document does not call
it one.

**Now implemented:** `backend/sportsassets/learn/kernel.py` — L2
logistic regression by IRLS, isotonic (PAV) calibration,
gradient-boosted stumps, and a discrete-time hazard model that handles
censoring by person-period expansion. Pure standard library,
deterministic, 31 tests against closed-form answers.
`backend/sportsassets/learn/metrics.py` — log loss, Brier, tie-aware
AUC, reliability bins with ECE/MCE, and a skill score that refuses to
report a model's loss without the base-rate loss beside it.

## 4. Predictions recorded before subsequent cohort actions — **IMPLEMENTED, NOT DEPLOYED**

| | |
|---|---|
| Path | `workers/bettor_prospective.py` (265 lines) |
| Deployed | **no**, deliberately — its header says so and `workers/all.py` omits it |
| What it records | BETTOR's own decision on a live book, before any outcome, keyed by `observation_id`, never overwritten |

**It does not predict cohort behaviour.** It records our decision, not a
forecast of what an account will do next. There is no prediction ledger
keyed to a cohort action anywhere in the system, and no table for one.

## 5. Model evaluation and versioning — **RESEARCH**

| | |
|---|---|
| Path | `research/beta48/shadow/model_registry.py` (452 lines), `learning_ledger.py` (381) |
| What they do | promotion gate, rollback, kill switches, lineage, point-in-time feature store, refusal of post-hoc metric selection |
| Deployed | no |
| Persisted | **no** — no table, so nothing survives the process |
| Their own headers | *"NOTHING IS TRAINED HERE. AUTO_PRODUCTION_PROMOTION = DISABLED."* |

The governance is genuinely good and is being reused rather than
rewritten. What it lacks is a database behind it and any model to
govern.

## 6. Integration of learned outputs into the EV engine — **MISSING**

| | |
|---|---|
| EV machinery | `bettor_ev_bridge.py` (784), `bettor_decision_engine.py` (866) — both wired and running |
| Whale evidence as prior | `research/beta48/shadow/whale_bridge.py` (888) — **research only** |
| Learned inputs | none. Every prior the EV engine consumes is hand-set or `NOT_IDENTIFIED` |

The engine has a place for a learned probability and nothing is
supplying one.

## 7. Autonomous execution and capital management — **IMPLEMENTED, GATED**

| Component | Path | State |
|---|---|---|
| Risk rails | `bettor_risk_engine.py` | implemented, fail-closed for exposure-increasing actions |
| Capital allocation | `bettor_capital_allocator.py` | implemented, ranks by net dollars per capital-hour |
| Execution gate | `execution_gate.py` | **running**, refuses every order unless explicitly bound |
| Live executor | `live_executor.py` | exists; not authorized |
| Shadow execution | `bettor_shadow_execution.py` | simulated fills only |

Autonomous execution is **correctly not running**. Nothing in this
mandate changes that, and the funded-pilot package is where it would be
requested.

---

## What this audit changes about the plan

1. The learning layer is built in pure Python because there is no
   numerical library in the image and adding one is a separate release.
2. Training data is abundant (6.1 M fills) but **latency-stratified**:
   behaviour and economics can be learned from all of it; *actionability*
   can only be claimed for the ~4-account live subset.
3. Exits must be reconstructed from complement buys, because most of the
   cohort never sells.
4. A gap detector is needed before any coverage claim, since nothing
   currently distinguishes a quiet poller from a quiet cohort.
