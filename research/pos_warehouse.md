# Profitability data warehouse (migration 216)

RESEARCH / SHADOW. No venue, order, sizing, limit, threshold or capital
authority. PAPER, ACTUAL and COUNTERFACTUAL are separate books and are never
summed. An unmeasured value is NULL with a named reason, never 0.

North star: repeatable forward **net economic profit per unit of risk and
capital-time**. Trade count, win rate and headline EV are not the target.

Code: `backend/sportsassets/profitability/` (pure modules plus one loader,
one writer and one runner). Reads: `backend/sportsassets/api/command_profitability.py`.
Schema: `backend/migrations/216_profitability_warehouse.sql`, with rollback
`backend/migrations/rollback/216_profitability_warehouse.down.sql`.

## Design: lineage, not copies

The warehouse does not copy the trading records. Each position gets one
**lineage row per revision** in `pos_lineage`. The row holds arrays of
**source record ids** for every stage of the chain and for its context. Any
economic number in `pos_position_economics` points to its lineage row
(`lineage_id`). The lineage row points to the source rows. All source rows
are append-only or carry their own history: paper fills, settlements,
ledger, book observations, Xavier records, Karen events and execmirror
fills are append-only.

A revision is written only when the content changes. Content means the
chain, the state, the low-frequency context and the gaps. Rows therefore
grow with events, not with cycles. A new revision is the **union** of the
previous revision's ids and the current ones. A record that a source table
later prunes stays referenced.

Some references grow while a position is merely held: Xavier reviews and
assessments, and paper ledger seqs. These **streams** are refreshed whenever
a revision is written for another reason, but they never cause a revision
on their own. Their source tables are append-only and keyed by `group_id`,
so nothing is lost between revisions.

## Tables (all: `label = 'RESEARCH'`, `authority = 'SHADOW_NO_AUTHORITY'`, append-only)

A trigger (`pos_record_is_append_only`) refuses UPDATE, DELETE and TRUNCATE
on every table.

| table | key | one row per | purpose |
|---|---|---|---|
| `pos_runs` | (run_id, component) | component per cycle | run log: status, error, duration, summary |
| `pos_lineage` | lineage_id; unique (book, position_key, revision) | position revision | source-id arrays per stage, `stages` (present / ids / why), `gaps` |
| `pos_position_economics` | econ_id; unique (book, position_key, revision) | position revision | capital-hour economics; COUNTERFACTUAL rows carry `counterfactual_kind`, `basis_book`, `basis_position_key` |
| `pos_capacity` | capacity_id; unique (candidate_id, content_sha256) | assessed candidate | capacity from the recorded book at the decision |
| `pos_metric_observations` | observation_id | metric × book, when it changes | the five north-star metrics: value, n, CI, status, why, period, data_as_of; trend in `detail` |
| `pos_snapshots` | snapshot_id | component × book, when it changes | aggregate payloads: CAPITAL (PAPER / ACTUAL / COUNTERFACTUAL), CAPACITY, WAREHOUSE |
| `pos_forecasts` | forecast_id; unique (book, issued_day) | book per UTC day | the 30-day revenue forecast, persisted for scoring |
| `pos_forecast_scores` | forecast_id (FK) | ended forecast | realized P&L over the horizon, PIT, inside P10–P90, Brier of P(positive) |

### Views

| view | what |
|---|---|
| `pos_lineage_latest`, `pos_economics_latest`, `pos_capacity_latest` | latest revision per key |
| `pos_warehouse` | one row per real position: latest lineage joined with latest economics |
| `pos_paper_chain_v` | **view over the existing paper records**: per paper position, fill / order / book / decision / valuation / settlement ids computed live from `paper_fills`, `paper_orders`, `paper_decisions` and `paper_settlements` |

### Database-enforced honesty (CHECKs)

- `book IN ('PAPER','ACTUAL','COUNTERFACTUAL')`. A COUNTERFACTUAL row must
  name its kind and the real position it shadows. A real row can never carry
  a counterfactual kind.
- An OPEN position cannot have `net_profit_usd`.
- `net_profit_usd`, `expected_net_profit_usd`, `capital_hours`,
  `expected_capital_hours`, `realized_profit_per_capital_hour` and
  `expected_profit_per_capital_hour`: each is either measured or has a key in
  `unmeasured` that gives the reason.
- `pos_capacity`: an `UNAVAILABLE` row must carry `why` and **no** capacity
  number.
- `pos_metric_observations`: `MEASURED` / `INSUFFICIENT_SAMPLE` ⇔ value
  present. An absent value needs a `why`. The only books are PAPER and ACTUAL.
- `pos_forecasts`: `UNAVAILABLE` ⇒ a `why` and no numbers. Status is one of
  `UNPROVEN | FORWARD_VALIDATED | UNAVAILABLE`.

## Position identity

| book | position_key | source |
|---|---|---|
| PAPER | `paperpos:{account}:{group}:{slug}:{side}` (the ledger's own key) | `paper_fills`, `paper_orders`, `paper_settlements` |
| ACTUAL | `actualpos:{POLYMARKET_US\|KALSHI}:{group}:{slug}:{side}` | `execmirror_fills` and `execmirror_orders`; `kalshi_live_fills` and `kalshi_live_intents` |
| COUNTERFACTUAL | `cf:HOLD_TO_SETTLEMENT:{real position_key}` | the real position's buys and the same contract's settlement |

## The chain and its sources (`pos_lineage` columns → source)

| stage | lineage column(s) | source table (id) |
|---|---|---|
| CANDIDATE | `valuation_ids` | `external_valuations.id` (via `paper_decisions.valuation_id` / `execution_intents.valuation_id`) |
| PROBABILITY | `decision_ids[0]` + `stages.PROBABILITY.value/basis` | `paper_decisions.p_blended / p_pinnacle / p_internal` |
| DECISION | `decision_ids` | `paper_decisions.decision_id` |
| EXECUTION_INTENT | `intent_ids` | `execution_intents.intent_id` (by group or decision) |
| ORDER | `order_ids` | `paper_orders.order_id` / `execmirror_orders.mirror_id` / `kalshi_live_intents.link_id` |
| FILL | `fill_ids`, `book_obs_ids` | `paper_fills.fill_id` / `execmirror_fills.fill_key` / `kalshi_live_fills.trade_id`; `paper_book_observations.obs_id` |
| POSITION | `position_key`, `state`, `opened_at`, `closed_at` | derived |
| MANAGEMENT_REVIEW | `review_ids`, `thesis_ids`, `assessment_ids`, `value_add_ids` | `paper_xavier_reviews` / `smalllive_reviews`; `xavier_entry_theses`, `xavier_management_assessments`, `xavier_value_add` |
| SETTLEMENT | `settlement_ids` (every version) | `paper_settlements.settlement_id` |
| PNL | `ledger_seqs`, + economics | `paper_ledger.seq`; `pos_position_economics` |

| context | lineage column(s) | source |
|---|---|---|
| agent actions | `review_ids` (acted), `handoff_ids`, `agent_decision_refs` | `paper_handoffs`, `smalllive_handoffs`, `agent_decisions` |
| challenges | `challenge_ids` | `karen_challenges` (target_id ∈ {group, decision, position key}) |
| audit findings | `audit_finding_ids`, `postmortem_keys` | `paper_audrey_findings` (subject), `position_postmortems` |
| features | `stages`/`gaps` FEATURES | `paper_decisions.internal_model` (by decision id) |
| model version | `model_versions` | `valuation:{version}`, `devig:{method}`, `internal_model:{version}` |
| policy version | `policy_versions` | `paper_decisions.policy_version`, `execution_intents.policy_version` |
| experiment | `experiment_ids` | `external_valuations.experiment_id` |
| simulator | `simulator_versions` | `paper_fills/paper_decisions.simulator_version` |
| counterfactual | `thesis_ids` / `value_add_ids` (Xavier counterfactuals) + COUNTERFACTUAL economics row | `xavier_*`, `pos_position_economics` |
| risk | `attribution_refs`, `sizing_refs`, `allocation_refs` | `intel_attribution`, `intel_sizing`, `intel_allocations` (first NEW_DECISION allocation) |
| capacity | `capacity_ids` | `pos_capacity` |
| regime | `regime_run_ids` | `intel_regime_states` in force at the decision instant |

## Reproducing a decision (data retention)

| needed to reproduce | where it is | status |
|---|---|---|
| provider inputs | `external_valuations` (raw_odds, devig, observed/received times) by `valuation_ids` | reproducible when linked; per-position gaps `PROVIDER_INPUTS` (no valuation) / `PROVIDER_RAW_ODDS` (no raw odds) |
| probabilities | `paper_decisions` p fields by `decision_ids` | reproducible; gap `PROBABILITY_AT_DECISION` when absent |
| books | `paper_book_observations` by `book_obs_ids` | reproducible when the decision/fill recorded an obs id; gap `BOOK_AT_DECISION` |
| timestamps | each source row's own times | reproducible; ACTUAL fill time is OBSERVATION time (gap `ACTUAL_VENUE_FILL_TIME`) |
| identity | position_key, group, slug, side; valuation `event_key` | reproducible |
| settlement | `paper_settlements` every version | reproducible; ACTUAL settlement time is the paper settlement of the same contract (gap `ACTUAL_SETTLEMENT_CASH_TIME`) |
| model / policy / experiment versions | `model_versions`, `policy_versions`, `experiment_ids`, `simulator_versions` | reproducible |
| agent versions | — | **GAP `AGENT_PERSONA_VERSION_AT_DECISION`**: `agent_persona_versions` is not linked to decisions or reviews |
| risk state | `intel_*` refs | **GAP `INTEL_RISK_AND_REGIME_HISTORY_PRUNED_AFTER_30D`**, **GAP `INTEL_ATTRIBUTION_AND_SIZING_ARE_LATEST_ONLY`** |
| portfolio state at decision | — | **GAP `PORTFOLIO_STATE_AT_DECISION`**: only periodic `paper_equity_snapshots` |
| execution state | orders / intents / fills by id | order rows are mutable: **GAP `ORDER_STATE_ROWS_ARE_MUTABLE`**, history in `paper_order_events` / `execmirror_events` |
| management actions | `review_ids`, `thesis_ids`, `assessment_ids`, `value_add_ids`, `handoff_ids` | reproducible (append-only sources) |
| audit findings | `audit_finding_ids`, `challenge_ids`, `postmortem_keys` | reproducible |
| outcome | `settlement_ids` + `pos_position_economics` | reproducible |

The standing gaps are listed in `warehouse.STANDING_GAPS` and appear in
every WAREHOUSE snapshot. Per-position gaps are stored in `pos_lineage.gaps`.

## Capital-hour economics (`pos_position_economics`)

| column | unit | definition | null when (reason) |
|---|---|---|---|
| capital_committed_usd | USD | acquisition cost of all contracts bought, fees included | no buy fill (`NO_BUY_FILL`) |
| capital_hours | USD·h | ∫ capital path dt. The capital path is the pre-fill PAPER entry reservation (from order creation to first fill), then the open cost basis (+q·p+fee per buy, −avg·q per sale), released at settlement or at the full exit. OPEN rows accrue to `last_event_at`; to date = + `open_cost_basis_usd` × hours since | no buy fill |
| time_committed_h | h | first capital lock → release | OPEN |
| net_profit_usd | USD | sale proceeds net of fees + settlement payout − acquisition cost | OPEN (`POSITION_OPEN_NO_REALIZED_PROFIT`) |
| expected_net_profit_usd | USD | bought × p − acquisition cost (p = the entry decision's probability that the held side pays) | no p (`ENTRY_DECISION_CARRIES_NO_PROBABILITY`) |
| expected_release_at | epoch | event start (us_premap.game_start, else the thesis event start) + median settlement lag, measured from settlements **recorded before the first fill** (≥ 5 samples; no look-ahead) | no event start / too few lag samples |
| expected_capital_hours | USD·h | capital committed × (expected release − first capital lock) | as above |
| realized_profit_per_capital_hour | USD / USD·h | net_profit / capital_hours | OPEN / zero capital-hours |
| expected_profit_per_capital_hour | USD / USD·h | expected_net_profit / expected_capital_hours | as its inputs |
| predicted / realized_edge_per_dollar | ratio | expected / realized net ÷ capital committed | — |

**Portfolio** (`pos_snapshots` CAPITAL, one per book) contains:

- capital locked: open cost basis, plus open PAPER order reservations (ACTUAL
  reservations are `null`, named);
- the release schedule in buckets (OVERDUE / ≤6h / ≤24h / ≤72h / later /
  UNKNOWN), with its basis;
- capital waiting for settlement (event started, not settled);
- capital overdue settlement;
- REALIZED_PROFIT_PER_CAPITAL_HOUR (closed in the 30-day window);
- EXPECTED_PROFIT_PER_CAPITAL_HOUR (opened in the window);
- capital-hours in the window;
- average locked capital;
- capital utilization = window capital-hours / (account capital × window hours);
- idle capital;
- account capital and its basis. PAPER uses the latest `paper_equity_snapshots`
  row, else `starting_cash_usd`. ACTUAL uses the execmirror snapshot
  `currentBalance + assetNotional`; Kalshi is not included.

The COUNTERFACTUAL snapshot holds the totals of the hold-to-settlement book
only.

## Capacity, metrics and forecast

Definitions are in the module docstrings and in `research/pos_econ_audit.md`
(data contracts). The modules are `profitability/capacity.py`, `metrics.py`
and `forecast.py`.

## Endpoints (GET, `require_read`, 401 without a session)

All endpoints return the envelope `{label: "RESEARCH", authority: "SHADOW_NO_AUTHORITY", status: OK|EMPTY|UNAVAILABLE, why, computed_at, data, disclosure, …}`.

| route | data |
|---|---|
| `/api/command/profitability` | latest `pos_runs` row per component |
| `/api/command/profitability/north-star` | `data.{PAPER,ACTUAL}.{METRIC}` = value, sample_n, ci_low/high, ci_level, status, why, period, trend, freshness {data_as_of, data_age_s} |
| `/api/command/profitability/capital?book=&limit=` | `data.{PAPER,ACTUAL,COUNTERFACTUAL}` = CAPITAL snapshot; `positions[]` = latest economics with `capital_hours_to_date`, REALIZED_/EXPECTED_PROFIT_PER_CAPITAL_HOUR |
| `/api/command/profitability/capacity?limit=` | `data` = aggregate (THEORETICAL / EXECUTABLE_OPPORTUNITY_DOLLARS, EXECUTABLE_CAPACITY_USD, CAPACITY_CEILING_USD, EXPECTED_EXECUTABLE_…, rates); `candidates[]` with EXPECTED_EDGE_AT_SIZE |
| `/api/command/profitability/forecast?history=` | `data.{PAPER,ACTUAL}` = latest forecast; `scores[]`; `forecast_label` |
| `/api/command/profitability/warehouse/{position_key}` | `data.lineage`, `lineage_revisions`, `economics.{BOOK}`, `economics_revisions` (incl. the COUNTERFACTUAL of that key) |
