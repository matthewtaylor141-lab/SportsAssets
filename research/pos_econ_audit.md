# Profitability Operating System, part 1: audit matrix

Branch `claude/pos-econ`, based on production SHA
`4717460d7d19acd43eb5e48593abe2cf658f3a62`. Migration
`216_profitability_warehouse.sql`, with rollback. Everything in this document
is RESEARCH / SHADOW and has no authority. Nothing here is COMPLETE. The
gate, deployment and production readback are PENDING because they happen at
integration.

- **Implementation SHA:** `IMPL_SHA_PENDING` (filled in by the follow-up
  commit on this branch).
- **Owner:** the `claude/pos-econ` stream.
- **Tests:** 67 tests across 5 files, all on
  `backend/tools/capital_critical_tests.txt`. Only these and the directly
  affected registry files were run: `test_calibration_only_records_cannot_trade.py`,
  `test_paper_records_cannot_reach_the_funded_path.py` and
  `test_intel_is_shadow_only.py`, 51 tests, all passing.

| file | tests |
|---|---|
| `tests/test_profitability_is_research_only.py` | 14 (authority, DB guards, runtime boundary, fail-closed, routes, arming) |
| `tests/test_profitability_economics.py` | 13 (unit, fail-closed, counterfactual) |
| `tests/test_profitability_capacity.py` | 18 (unit, fail-closed) |
| `tests/test_profitability_metrics_forecast.py` | 15 (unit, out-of-sample, books never summed, validation) |
| `tests/test_profitability_warehouse_cycle.py` | 7 (integration through the cycle, endpoints) |

## Matrix: one row per capability

Abbreviations:

- **P** = PENDING (integration happens later).
- **NB** = not built in this part.
- **§DC-n** = data contract n below.
- **§AB-n** = authority boundary n below.
- **AL** = authority level. Every row is `SHADOW_NO_AUTHORITY`: it writes
  pos_* only and reaches no venue, order, size, limit or capital.

| CAPABILITY | OWNER | BRANCH | SHA | MIGRATION | DATA CONTRACT | AUTHORITY LEVEL | UNIT TEST | INTEGRATION TEST | CAPITAL-BOUNDARY TEST | OOS TEST | COUNTERFACTUAL TEST | FAIL-CLOSED TEST | AUDREY AUDIT | KAREN REVIEW | COMMAND CENTER | SLACK | EXACT-SHA GATE | DEPLOYMENT | PRODUCTION READBACK | FORWARD SAMPLE | ECONOMIC RESULT | STATUS | BLOCKER |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 Profitability data warehouse (lineage) | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | 216 (`pos_lineage`, views `pos_warehouse`, `pos_paper_chain_v`) | §DC-1 | AL (§AB-1) | via cycle | yes (`warehouse_cycle` ×4) | yes (`research_only` §1–§3) | n/a | yes (CF econ row links the real lineage) | yes (absent stage → `why`; absent table → named gap) | NB | NB | endpoint ready `/warehouse/{key}`; UI by another stream | n/a | P | P | P | 0 production positions | none yet (synthetic only) | READY_FOR_REVIEW | integration gate, deploy, first production cycle; named gaps (§DC-1) |
| 2 Capital-hour economics (per position + portfolio) | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | 216 (`pos_position_economics`, CAPITAL snapshots) | §DC-2 | AL (§AB-2) | yes (13) | yes | yes | yes (expected release uses only settlements recorded before the first fill) | yes | yes (no p / no event start / no lag / no account capital / no buy → null + reason) | NB | NB | endpoint ready `/capital` | n/a | P | P | P | 0 | none yet | READY_FOR_REVIEW | as above; ACTUAL fill and settlement times are proxies (gaps) |
| 3 Counterfactual HOLD_TO_SETTLEMENT | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | 216 (book COUNTERFACTUAL + CHECK) | §DC-3 | AL (§AB-2) | yes | yes | yes | n/a | yes (labelled, separate, never summed; DB CHECK) | yes (no sale or unknown settlement → none) | NB | NB | in `/capital` and `/warehouse` | n/a | P | P | P | 0 | none yet | READY_FOR_REVIEW | only one counterfactual kind (hold to settlement) |
| 4 Capacity model | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | 216 (`pos_capacity`, CAPACITY snapshot) | §DC-4 | AL (§AB-3) | yes (18) | yes | yes | n/a (book at decision, no future data) | n/a | yes (10 cases → UNAVAILABLE with no number; DB CHECK) | NB | NB | endpoint ready `/capacity` | n/a | P | P | P | 0 | none yet | READY_FOR_REVIEW | fill probability and deployment time are PAPER-simulated rates; stream evidence not joined |
| 5 Five north-star metrics | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | 216 (`pos_metric_observations`) | §DC-5 | AL (§AB-4) | yes (15, shared) | yes | yes | n/a | yes (PAPER / ACTUAL never summed; CF never enters) | yes (UNAVAILABLE / UNPROVEN / INSUFFICIENT_SAMPLE) | NB | NB | endpoint ready `/north-star` | n/a | P | P | P | 0 | none yet | READY_FOR_REVIEW | needs ≥30 closed positions per book (and ≥30 days for metric 3) to be MEASURED |
| 6 Predictable monthly revenue engine | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | 216 (`pos_forecasts`, `pos_forecast_scores`) | §DC-6 | AL (§AB-5) | yes | yes (persist + score) | yes | yes (ignores outcomes after issue; scored only after the horizon) | yes (own book only) | yes (UNAVAILABLE below 14 days / 10 positions) | NB | NB | endpoint ready `/forecast` | n/a | P | P | P | 0 scored forecasts | none; **UNPROVEN** | RESEARCH | needs ≥12 scored forecasts (≥ 12 × 30 days of forward time) before it can be FORWARD_VALIDATED |
| 7 Runner (bounded, isolated, locked, kill switch) | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | 216 (`pos_runs`) | §DC-7 | AL (§AB-6) | n/a | yes | yes (full cycle leaves protected tables unchanged) | n/a | n/a | yes (component failure isolated; no 216 → idle) | NB | NB | `/api/command/profitability` index | n/a | P | P | P | 0 cycles in production | n/a | READY_FOR_REVIEW | deploy (armed in app lifespan, `POS_ECON=off` kill switch) |
| 8 Read endpoints | pos-econ | claude/pos-econ | IMPL_SHA_PENDING | none | §DC-8 | AL read-only (§AB-7) | n/a | yes (envelopes, EMPTY, UNAVAILABLE) | yes (GET-only, 401, read pos_* only) | n/a | yes (books as separate keys) | yes (failed read → UNAVAILABLE, never zeros) | NB | NB | endpoints ready; UI page by another stream | n/a | P | P | P | n/a | n/a | READY_FOR_REVIEW | UI build (other stream) |

### The original brief's checklist

These rows summarize the matrix against the checklist in the original brief.

| capability | DESIGNED | SCHEMA | CODED | UNIT TESTED | INTEGRATION TESTED | AUTHORITY TESTED | ECONOMIC METRIC DEFINED | SHADOW RUN | EVAL | COMMAND CENTER |
|---|---|---|---|---|---|---|---|---|---|---|
| warehouse | yes | yes | yes | via cycle | yes | yes | n/a (lineage) | **test-DB only**¹ | NB | endpoint ready |
| capital-hour economics | yes | yes | yes | yes | yes | yes | yes (§DC-2) | test-DB only¹ | NB | endpoint ready |
| capacity | yes | yes | yes | yes | yes | yes | yes (§DC-4) | test-DB only¹ | NB | endpoint ready |
| north-star metrics | yes | yes | yes | yes | yes | yes | yes (§DC-5) | test-DB only¹ | NB | endpoint ready |
| revenue engine | yes | yes | yes | yes | yes | yes | yes (§DC-6) | test-DB only¹ | scoring built; no scored forecast yet | endpoint ready |

¹ A cycle was run, then rolled back, over the existing rows of the migrated
test database (`posecon`, from template `c24t`, which holds leftovers of
other suites; it is not production data). All 6 components were OK in
0.05–0.11 s. It produced 4 PAPER positions per account, 12 MEASURED and 4
UNAVAILABLE (`NO_RECORDED_BOOK_OBSERVATION`) capacity rows, PAPER metrics at
INSUFFICIENT_SAMPLE, ACTUAL metrics UNAVAILABLE, and forecasts UNAVAILABLE.
**No production shadow run has happened.**

## Data contracts

Units: USD unless stated; times are epoch seconds (timestamptz in the DB);
capital-hours are USD·h. "null+reason" means NULL with a key in
`unmeasured` that names the reason. Every row carries `label` and
`authority`.

### §DC-1 Warehouse (`pos_lineage`)

- **Inputs:** `paper_fills`, `paper_orders`, `paper_settlements` (all
  versions), `paper_decisions`, `external_valuations` (by id),
  `execution_intents`, `execmirror_orders` / `execmirror_fills`,
  `kalshi_live_intents` / `kalshi_live_fills`, `smalllive_handoffs` /
  `smalllive_reviews`, `paper_xavier_reviews`, `paper_handoffs`,
  `xavier_entry_theses` / `xavier_management_assessments` / `xavier_value_add`,
  `position_postmortems`, `karen_challenges`, `paper_audrey_findings`,
  `agent_decisions`, `paper_ledger`, `intel_attribution` / `intel_sizing` /
  `intel_allocations` / `intel_regime_states`, and `pos_capacity`. Reads are
  bounded: a 90-day lookback over positions with fills, and a 20,000-row
  LIMIT per read.
- **Outputs:** one revision per content change. It holds identity (book,
  position_key, venue, account, group, slug, side, strategy, state, opened_at
  and closed_at), 26 id arrays (see `research/pos_warehouse.md`), `stages`
  and `gaps`.
- **Nullability:** id arrays are never NULL (empty means none was found).
  An absent stage carries `why`. A source table missing from the database
  produces the named gap `<NAME>_TABLE_ABSENT`, never an empty list.
- **Named gaps:** these cannot currently be reproduced.
  - Standing: `PORTFOLIO_STATE_AT_DECISION`, `AGENT_PERSONA_VERSION_AT_DECISION`,
    `INTEL_RISK_AND_REGIME_HISTORY_PRUNED_AFTER_30D`,
    `INTEL_ATTRIBUTION_AND_SIZING_ARE_LATEST_ONLY`, `ORDER_STATE_ROWS_ARE_MUTABLE`.
  - Per position: `PROVIDER_INPUTS`, `PROVIDER_RAW_ODDS`, `BOOK_AT_DECISION`,
    `PROBABILITY_AT_DECISION`, `FEATURES`, `ACTUAL_VENUE_FILL_TIME`,
    `ACTUAL_SETTLEMENT_CASH_TIME`.

### §DC-2 Capital-hour economics (`pos_position_economics`, CAPITAL snapshots)

- **Inputs:** the position record built by `reads.paper_positions` /
  `reads.actual_positions` (fills in cost space with fee, PAPER entry
  reservation, latest settlement, the same contract's settlement, decision p,
  event start), plus settlement-lag samples (first WON/LOST settlement per
  market against `us_premap.game_start`).
- **Outputs per position:**
  - `capital_committed_usd`, `peak_capital_usd`, `open_cost_basis_usd`
  - `capital_hours` (USD·h), `time_committed_h` (h)
  - `net_profit_usd`, `expected_net_profit_usd`
  - `expected_release_at` with `release_basis`, `expected_capital_hours`
  - `realized_profit_per_capital_hour` and `expected_profit_per_capital_hour`
    (USD per USD·h; served in upper case as REALIZED_ / EXPECTED_PROFIT_PER_CAPITAL_HOUR)
  - `predicted_edge_per_dollar`, `realized_edge_per_dollar`
- **Outputs per book (snapshot):**
  - capital locked (positions and reservations)
  - release schedule buckets, next releases, release basis
  - waiting for settlement, overdue
  - realized and expected profit per capital-hour, with samples
  - capital-hours in the window, average locked capital
  - account capital and its basis, utilization, idle capital
- **Nullability:**
  - net profit is NULL while OPEN (DB CHECK);
  - expected values are null+reason without p, event start or ≥5 lag samples
    before entry;
  - utilization and idle capital are null+reason without account capital;
  - ACTUAL reservations are null+reason.
- **Sources:** as listed under Inputs. Formulas are in
  `profitability/economics.py`.

### §DC-3 Counterfactual (`pos_position_economics` book COUNTERFACTUAL)

- **Inputs:** a real position with ≥1 sale and a known settlement of the
  same contract (`paper_settlements` of the same slug and side, any position).
- **Outputs:** the same columns as §DC-2 for "all buys held to that
  settlement", with `counterfactual_kind = HOLD_TO_SETTLEMENT`, `basis_book`
  and `basis_position_key`, and `lineage_id` set to the real position's
  lineage.
- **Nullability:** no row at all when there is no sale or the settlement is
  unknown.
- **Never summed:** a separate book (DB CHECK). The CAPITAL snapshot for
  COUNTERFACTUAL holds only its own totals.

### §DC-4 Capacity (`pos_capacity`, CAPACITY snapshot)

- **Inputs:**
  - paper decisions from the last 24 h not yet assessed (≤1,500 per cycle);
  - the probability (`p_blended` > `p_pinnacle` > `p_internal`) and holding
    side;
  - the recorded book: the decision's `book_obs_id`, else the latest readable
    `paper_book_observations` row for the market at or before the decision,
    within 300 s;
  - the fee: published Polymarket US taker schedule for the decision date,
    θ·p·(1−p) per contract, unrounded (published, not verified-as-applied).
- **Outputs per candidate (USD unless stated):**
  - `best_price`; `visible_depth_usd` and `_contracts`
  - **THEORETICAL_OPPORTUNITY_DOLLARS** = max(0, p − best) × visible
    contracts (the headline)
  - **EXECUTABLE_OPPORTUNITY_DOLLARS** = Σ over levels with marginal net
    edge > 0 of q·(p − price − fee), conditional on fill (alias
    `executable_opportunity_usd`)
  - **EXECUTABLE_CAPACITY_USD**: capital at that size; `max_executable_contracts`
  - **CAPACITY_CEILING_USD**: break-even size (cumulative expected net profit
    back to 0), with `capacity_ceiling_depth_bound`
  - **EXPECTED_EDGE_AT_SIZE**: a grid of $10 … $10,000 giving contracts,
    VWAP, impact, expected net profit and edge per dollar, or
    EXCEEDS_VISIBLE_DEPTH
  - `edge_decay_per_1000_usd`; `expected_price_impact` (VWAP − best, price
    units)
  - `exit_liquidity_usd` and `exit_unabsorbed_contracts` (selling the
    executable size into the opposite side)
- **Book-level rates** (`rates`, each value-or-null with n, basis and why):
  - fill probability: PAPER-simulated entry fill quantity share over 14 days,
    ≥20 orders;
  - deployment time: median decision → first fill, ≥5 orders;
  - time to exit: median exit order → first fill, ≥5 orders;
  - ACTUAL mirror fill share.
- **Aggregate:**
  - the latest candidate per (market, side);
  - sums of THEORETICAL / EXECUTABLE opportunity, EXECUTABLE_CAPACITY_USD and
    CAPACITY_CEILING_USD over MEASURED candidates only;
  - EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS (× fill probability, null+reason
    when unmeasured);
  - daily executable opportunity;
  - UNAVAILABLE counts by reason.
- **Nullability (fail-closed):** any of the following gives status
  UNAVAILABLE with `why` and no capacity number (DB CHECK):
  - no or invalid p;
  - unknown side;
  - no recorded book;
  - book more than 300 s from the decision;
  - empty or unparsable take side;
  - no fee schedule.
- **Measured zero:** a readable book with no positive-edge level is
  MEASURED with executable 0, and impact is null+reason.

### §DC-5 North-star metrics (`pos_metric_observations`)

- **Inputs:** this run's economics rows of ONE book (PAPER or ACTUAL), in a
  trailing 30-day period (closed by `released_at`). Metric 3 uses the 90-day
  lookback daily series.
- **Outputs per (book, metric):**
  - value, `sample_n`, `ci_low` / `ci_high` (90% percentile bootstrap, seeded,
    with a bounded draw budget)
  - status, why, period
  - `data_as_of` (the newest source event); freshness age is computed at read
  - in `detail`: trend (direction, delta, improving, against the latest
    observation ≥20 h old), unit, ci_why, and metric detail
- **Definitions:**
  1. **REALIZED_NET_EDGE** = Σ net profit / Σ capital committed (USD per USD).
  2. **PROFIT_PER_CAPITAL_HOUR** = Σ net profit / Σ capital-hours (USD per USD·h).
  3. **PROB_POSITIVE_ROLLING_30D_PNL** = P(30-day sum > 0) under a circular
     block bootstrap (5-day blocks) of daily realized P&L. Days with no
     closure count as measured 0 once history begins. The CI comes from an
     outer bootstrap. The empirical share of positive rolling windows is
     reported once history reaches 30 days.
  4. **MAX_DRAWDOWN** = peak-to-trough of cumulative realized P&L by release
     time (USD ≥ 0). No CI: it is a path statistic, and that is stated.
  5. **EDGE_CALIBRATION** = Σ realized net / Σ expected net at entry
     (1.0 = predicted edge fully realized). The OLS slope of realized on
     predicted edge per dollar and quintile reliability are also reported.
- **Statuses:**
  - MEASURED: n ≥ 30 (and for metric 3, ≥ 30 days).
  - INSUFFICIENT_SAMPLE: a value exists but the sample is below that floor.
  - UNAVAILABLE: nothing closed.
  - UNPROVEN: calibration with no prediction, or Σ predicted ≤ 0.
- **Nullability:** DB CHECK: value present ⇔ MEASURED / INSUFFICIENT_SAMPLE;
  an absent value needs a why.

### §DC-6 Revenue engine (`pos_forecasts`, `pos_forecast_scores`)

- **Inputs:**
  - the daily realized net P&L series of ONE book over the 90-day lookback,
    with releases before the issue instant only;
  - economics capital paths;
  - the capacity aggregate's daily executable opportunity and fill
    probability;
  - prior scores of this book.
- **Outputs per forecast:**
  - expected 30-day net P&L, P10 / P50 / P90, P(positive)
  - expected, P95 and **worst modelled** max drawdown (max over 2,000 paths)
  - **capital required** (time-weighted average locked capital)
  - **capital-hours required** (30 × average daily capital-hours)
  - turnover required (30 × average daily capital committed)
  - capacity ceiling (30 × daily executable opportunity × fill probability,
    or × 1.0 conditional-on-fill when the fill probability is unmeasured,
    with the basis stated)
  - a quantile grid (5% steps), seed, inputs_sha256, validation, and status
- **Status:**
  - UNPROVEN, unless validation passes: ≥12 scored forecasts, P10–P90
    coverage within [0.70, 0.90] and mean Brier ≤ 0.25. Then it is
    FORWARD_VALIDATED, still research.
  - UNAVAILABLE below 14 days or 10 closed positions, with no numbers (DB
    CHECK).
- **Persistence:** one forecast per book per UTC day (`UNIQUE (book,
  issued_day)`).
- **Scoring:** once, after `horizon_end`. Each score records realized P&L
  (Σ net of positions released in the horizon), PIT, whether it fell inside
  P10–P90, whether it was positive, Brier, and |error vs P50|.

### §DC-7 Runner (`pos_runs`)

- **Order:** CAPACITY → ECONOMICS → WAREHOUSE → CAPITAL → NORTH_STAR →
  FORECAST.
- **Scheduling:** every 3,600 s after a 300 s delay, with advisory lock
  `0x504F5331`.
- **Bounds:** a savepoint and `statement_timeout` of 20 s per component, a
  120 s asyncio timeout, and heavy computation off the event loop.
- **Kill switch:** `POS_ECON` in {off, 0, false, no}.
- **Without migration 216:** `{ran: false, why: MIGRATION_216_NOT_APPLIED}`.
- **Failures:** a failed ECONOMICS skips WAREHOUSE, CAPITAL, NORTH_STAR and
  FORECAST (recorded SKIPPED). A failed CAPACITY leaves the forecast's
  capacity ceiling null+reason.

### §DC-8 Endpoints

The shapes are in `research/pos_warehouse.md` (§Endpoints).

## Authority boundaries

The following holds for **every** capability below:

- The modules import only the standard library, `sportsassets.profitability`,
  the intel layer's pure or SELECT-only `common`, `reads` and `attribution`,
  and `bettor_fee_schedule` (pure). This covers the transitive closure, and
  no venue, order, execution, ledger-writing, sizing, allocator or desk
  module is reachable (`test_the_transitive_import_closure…`).
- Every SQL write is an `INSERT INTO pos_*` in `store.py`, and nothing else
  writes (`test_every_sql_write_is_an_insert…`).
- The database refuses UPDATE, DELETE and TRUNCATE on pos_*, and the CHECKs
  pin `label = RESEARCH` and `authority = SHADOW_NO_AUTHORITY`.
- A full cycle leaves every protected table's row count unchanged. That
  covers the paper order, fill, ledger, settlement, decision, control and
  account tables, `external_valuations`, `execution_intents`, execmirror,
  Kalshi, smalllive, `live_rule_artifacts`, `agent_policy_artifacts` and
  every `bettor_funded_*` table (`test_a_full_cycle_writes_only_research_rows`).
- No production system reads pos_*. No order, sizing, limit, threshold or
  capital path imports this package.

Per capability:

- **§AB-1 Warehouse:** it references source ids and copies nothing. It
  cannot alter a source row because it only SELECTs from them.
- **§AB-2 Economics and counterfactual:** the expected values are reporting
  only. A COUNTERFACTUAL row cannot be mistaken for a real book (CHECK).
- **§AB-3 Capacity:** EXECUTABLE_CAPACITY_USD is not a size and no sizing
  module reads it. The fee is the published schedule, flagged not
  verified-as-applied.
- **§AB-4 Metrics:** displayed only. No threshold, limit or gate reads them.
- **§AB-5 Forecast:** UNPROVEN by construction. "Capital required" and
  "turnover required" are descriptive and allocate nothing.
- **§AB-6 Runner:** writes pos_* only. It holds its own advisory lock, which
  is distinct from the intel runner's. The kill switch stops it from
  starting.
- **§AB-7 Endpoints:** GET only, `require_read`, 401 without a session. They
  read pos_* only and write nothing (`test_the_read_routes_read_only_pos_tables`).

The registry classifications are honest:

- `test_calibration_only_records_cannot_trade.py` READERS:
  - `profitability/reads.py` is BY_ID_OF_THE_HELD_POSITIONS_DECISION_RESEARCH_LINEAGE_ONLY (2).
  - `profitability/warehouse.py` is PROSE_ONLY (1).
- `test_paper_records_cannot_reach_the_funded_path.py`: no profitability
  module imports a paper module, so no allowlist entry was needed.

## Simplifications and departures from the brief (stated honestly)

1. **Position universe:**
   - PAPER covers groups with a fill in the last 90 days, for the
     configured paper account only (`paper_acct_main`).
   - ACTUAL covers Polymarket US mirror groups with a fill in 90 days and
     Kalshi fills observed in 90 days.
   - Older long-lived positions are outside the lookback.
2. **ACTUAL timing proxies:**
   - Capital-hours start at the fill's OBSERVATION time, not the venue match
     time.
   - Settlement and release time is the PAPER settlement of the same
     contract. The economic outcome of the same contract is exact; the cash
     time is a proxy.
   - Kalshi prices are taken as the traded leg's own price (the postmortem
     convention).
   - Venue open-order reservations are not in these records (null+reason).
3. **Reservations:** the PAPER pre-fill reservation counts only from the
   first entry order's creation to the first fill. Later add-on orders'
   reservations before their fills are not counted.
4. **Expected release:** event start plus the median settlement lag. This
   needs `us_premap.game_start` (else the Xavier thesis event start) and ≥5
   lag samples recorded before entry. Otherwise the expected values are
   null+reason (no assumed event duration). "Waiting for settlement" means
   the event has started: in-play and concluded-unsettled cannot be told
   apart.
5. **Capacity:**
   - Evidence is `paper_book_observations` only. `institutional_stream_evidence`
     is keyed by institutional symbol, with no identity map to a slug in this
     layer, so it is **not joined** (named here, not faked).
   - Fill probability, deployment time and time to exit are PAPER-SIMULATED
     empirical rates at book level, not per-candidate models. EXECUTABLE_*
     values are reported conditional on fill, and the expected
     (× fill probability) value is given only when that rate is measured.
   - Candidates are paper decisions from the last 24 h, both ENTER and REFUSE
     verdicts.
   - Each assessment is immutable (one per candidate).
   - Time to exit is book-level, not per candidate.
6. **Metrics:**
   - Sample floors are fixed: 30 positions, and 30 days for metric 3.
   - CIs are percentile bootstraps with a draw budget of 300,000 per CI.
   - The trend compares against the latest observation at least 20 h old.
   - Observations are per book, not per account: one paper account per
     deployment is assumed.
7. **Forecast:**
   - Circular block bootstrap of daily realized P&L. It is stationary, has no
     regime conditioning, and does not scale with capital.
   - The capacity ceiling is an upper bound, not a forecast.
   - Validation thresholds are fixed: n ≥ 12, coverage within [0.70, 0.90],
     Brier ≤ 0.25.
   - One forecast per book per UTC day: the first issued that day stands.
8. **Warehouse:**
   - Stream references (Xavier reviews and assessments, ledger seqs) refresh
     only when another event writes a revision, or at the close.
   - The regime reference is the intel regime state in force at the decision,
     and intel history is pruned after 30 days (a named gap).
   - Only the first intel allocation per decision is referenced.
   - `agent_findings` are reached only through Karen challenges.
   - No ad-hoc search of `evidence_refs` JSON.
9. **Snapshots:**
   - CAPITAL snapshots change every cycle because utilization moves with
     time. That is ≈1 small row per book per hour, append-only with no
     pruning.
   - Every other table grows with events, not cycles.
   - The rollback refuses while forecasts exist (the forward record).
10. **No indexes on other streams' tables:** the migration creates pos_*
    objects only. Some reads (`paper_settlements` by group or slug,
    `execution_intents` by group) have no supporting index, and are bounded
    by `statement_timeout` and the lookback.
11. **Not built in part 1:**
    - an Audrey independent recompute of these metrics;
    - a Karen detector over pos_*;
    - the Command Center UI page (another stream);
    - Slack (n/a).
