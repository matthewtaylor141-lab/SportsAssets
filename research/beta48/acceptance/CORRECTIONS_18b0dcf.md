# CORRECTIONS TO 18b0dcf

Six defects, all reproduced, all mine. The synthetic demonstration and the
real-data NO_TRADE replay stand; what follows corrects what was claimed
around them.

## 1. "Prospective runner" was a file processor — CORRECTED

It loaded a JSON file once, processed it, exited, and stamped historical rows
`PROSPECTIVE_SHADOW` using **the age stored in the row**. Dedup does not make
history prospective, and I described it as a live feed needing only
deployment.

`bettor_prospective_runner.py` now has two non-interchangeable modes.
`--replay` stamps `REPLAY_DECISION`. `--live` computes freshness from **three
clocks** — venue source, our receipt, decision time — and rejects
`NO_VENUE_SOURCE_TIMESTAMP`, `CLOCK_SKEW_UNVERIFIABLE`,
`SOURCE_TIMESTAMP_IN_FUTURE`, `STALE_AT_DECISION`. Records carry
`policy_id`, engine and adapter versions, and the inputs available at the
decision. Only `--live` produces `PROSPECTIVE_SHADOW`, and `--live` **refuses
to run from the script**: the reader is injected by the deployment, because a
standalone script must not hold a venue client.

Self-test asserts the replay path never emits a prospective record and that
a two-hour-old book is rejected at decision time.

## 2. `pair_legs()` could pair a label with itself — CORRECTED

It sorted legs and took `[:2]`, so a contract observed twice on one side
produced a `no/no` "pair". It also never checked complement semantics
(`over` and `no` both differ from each other and are not complements) and
never checked time (quotes hours apart are two markets).

Now requires: same `market_id`, both labels from one declared
`COMPLEMENT_FAMILIES` entry (`{yes,no}`, `{over,under}`), and **source**
timestamps within `PAIR_ALIGNMENT_S = 2.0`. Returns `source_skew_s`,
`provenance`, `depth`, and `executable`.

**The 11 two-label contracts are NOT verified executable pairs.** That was a
count of label diversity; none has been checked for family, alignment or
depth. Stated in the module.

## 3. Depth exists and I said it did not — CORRECTED

I wrote `ABSENT_IN_CAPTURE_SCHEMA` after reading the base table and never
querying the columns that hold it. Migration 088 declares `yes_depth`,
`no_depth`, `multi_level_depth` as JSONB and the store writes all three.
Measured: **1,481 rows carry a `multi_level_depth` object**, e.g.
`{"ask":"2702.0000","bid":"2565.0000","levelsCaptured":5}` with a five-level
ladder of per-level qty and price.

The adapter reads real depth. A row **without** depth is now `REJECTED`, not
zeroed — zero depth and unrecorded depth are different facts.

## 4. "No outcome has matured" was unfounded — CORRECTED

`bettor_state_store.record_settlement()` is **defined and never called**;
`workers/bettor_state.py` calls only `record_mid`. So zero rows is a **missing
ingestion path**, not evidence nothing resolved. The hard-coded claim is
removed from the replay.

Separately measured: all 1,469 distinct markets were observed within 48 h, so
most genuinely may not have resolved yet — but we could not tell either way,
which is the point.

## 5. The evaluation deviated from its own frozen protocol — MARKED

`PREREGISTRATION_MAKER_V1` §4 declares the exit grid as
`{AGGRESSIVE_AT_BID, HOLD_TO_SETTLEMENT}` and a `p_fill` grid of
`{0.01, 0.05, 0.20, 0.50}`.

**The replay evaluated `AGGRESSIVE` and `PASSIVE`, omitted `SETTLEMENT`
entirely, and never used the `p_fill` grid.** It therefore did not implement
the stated protocol. v1 and its numbers are preserved; the deviation is
recorded rather than retrofitted.

**The acceptance rules also contradict themselves.** Rule 2 says a candidate
needing a NOT_IDENTIFIED term is `UNRESOLVED`, never refuted. I then called
the crossing route **REFUTED** while it used a hypothetical fee and an assumed
price move. Under v1's own rule that verdict is not available.

**Corrected verdict: the crossing-route result is a SCENARIO RESULT under
hypothetical fees and assumed movement — not an empirical refutation of maker
trading.** A v2 protocol must be defined, including the settlement route and
the `p_fill` grid, **before** any new holdout is read. Exploratory results stay
separate.

## 6. Capacity arithmetic — CORRECTED, and it was 10× wrong

At $0.50/contract:

| | correct | what I wrote |
|---|---|---|
| Contract executions/day | **1,000,000** | 100,000 |
| Contract executions/second | **11.57** | 1.2 |
| Fully-filled orders/day (10/order) | **100,000** | — |
| Orders/second | **1.16** | — |

**I labelled the order count as the contract count.** Dependent figures:
contracts per market per day at 810 markets is **1,234.6**, not 123.

**Throughput claim narrowed.** 21,917 obs/sec is a **CPU benchmark of
normalize+decide only**. It excludes feed ingestion, persistence, order
lifecycle, venue API limits and rate limiting. It does **not** establish
executable throughput, and the collector's own measured gateway behaviour
(refusals in every one of 19 hours) is the relevant bound instead.

**Working capital, properly.** $50,000 was one turn's notional and is not
sufficient on its own. Required capital = notional per cycle × cycles
outstanding, where cycles depend on **inventory duration** and **settlement
timing**:

> `capital ≈ (daily notional ÷ turns per day)`, and
> `turns per day = 86,400 ÷ (inventory duration + settlement lag)`

At $500k/day: a 1-hour hold with same-day settlement gives ~24 turns →
~$21k. A hold to event settlement — **hours to days**, which is what these
contracts actually do — gives ≤ 1 turn → **$500k or more**. Inventory duration
and settlement lag are both **unmeasured**, so required capital is bounded
below by ~$21k and plausibly the full $500k. The single $50,000 figure was
meaningless without those cycles.

---

## What stands unchanged

The synthetic demonstration (nine sections, independent cash and inventory
assertions, residual 0) and the real-data replay (15 of 36 accepted, 15/15
NO_TRADE, 0 orders) are unaffected. They establish bounded integration
progress and nothing about profitability.
