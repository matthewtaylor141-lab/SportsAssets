# Paper trading read models: `/api/command/paper/*`

Read-only, COMMAND auth (the `require_command` read credential). A request
without it gets 401. Every figure is **LIVE MARKET DATA / SIMULATED
EXECUTION** on the fictional $500,000 paper account (`paper_acct_main`).
Paper fills are simulated by `PAPER_SIM_V1`; they are not verified
execution. No route reads a funded table, so paper totals are never mixed
with funded totals.

Every payload carries `data_label`, `labels`, `as_of` (epoch s) and
`last_updated_at` (epoch s of the latest committed ledger entry or
heartbeat). Every section is independently:

```json
{"status": "OK" | "EMPTY" | "UNAVAILABLE", "why": "<reason or null>", "data": ...}
```

EMPTY carries a named reason; UNAVAILABLE names the failed read.

## GET /api/command/paper/account?entries=50

```json
{
  "data_label": "LIVE MARKET DATA / SIMULATED EXECUTION",
  "labels": {"market_data": "LIVE_MARKET_DATA", "execution": "SIMULATED_EXECUTION",
             "money": "FICTIONAL_USD_NOT_REAL_MONEY", "real_money_submission": "DISABLED"},
  "as_of": 0.0, "account_id": "paper_acct_main", "last_updated_at": 0.0,
  "session": {"active": true, "reason": null, "session_id": "paper_session_...",
              "started_at": 0.0, "starting_cash_usd": 500000.0,
              "last_heartbeat_at": 0.0, "real_money_submission": "DISABLED"},
  "account": {"status": "OK", "why": null, "data": {
      "cash_usd": 0.0, "reserved_usd": 0.0, "reserved_is": "part of cash, not extra",
      "available_usd": 0.0, "open_position_value_usd": 0.0,
      "open_position_value_marked_only_usd": 0.0, "marks_complete": true,
      "unmarked_positions": [], "unmarked_cost_basis_usd": 0.0, "stale_marks": [],
      "total_equity_usd": 0.0, "equity_excluding_unmarked_usd": 0.0, "equity_basis": "...",
      "realized_pnl_usd": 0.0, "unrealized_pnl_usd": 0.0,
      "unrealized_pnl_marked_only_usd": 0.0, "fees_paid_usd": 0.0,
      "starting_cash_usd": 500000.0, "last_updated_at": 0.0, "last_sequence": 1,
      "open_positions": [{"position_key": "...", "group_id": "...", "us_market_slug": "...",
          "holding_side": "LONG|SHORT", "fixture": "...", "label": {"participant": "..."},
          "open_qty": 0.0, "avg_cost_per_contract_incl_fees": 0.0, "cost_basis_usd": 0.0,
          "realized_pnl_usd": 0.0,
          "mark": {"status": "OK|STALE|UNAVAILABLE", "price": 0.0, "source": "paper_book_observations:<id>",
                   "observed_at": 0.0, "age_s": 0.0, "stale": false, "method": "..."},
          "marked_value_usd": 0.0, "unrealized_pnl_usd": 0.0}],
      "mark_method": "...", "mark_stale_after_s": 300.0,
      "ledger_entries": 1, "ledger_consistent": true, "real_money_submission": "DISABLED"}},
  "ledger": {"status": "OK", "why": null, "data": ["<ledger entry, newest first>"]},
  "drawdown": {"status": "OK|EMPTY", "data": {"peak_equity_usd": 0.0, "current_equity_usd": 0.0,
               "current_drawdown_usd": 0.0, "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
               "snapshots": 0, "skipped_incomplete_marks": 0}}
}
```

`total_equity_usd`, `open_position_value_usd` and `unrealized_pnl_usd` are
`null` when any open position has no available mark; the marked-only figures
beside them exclude those positions and say so. A missing mark is never zero.

A **ledger entry**:

```json
{"sequence": 7, "kind": "INITIAL_FUNDING|ORDER_SUBMITTED|FILL|RESERVATION_RELEASED|SALE|SETTLEMENT|CORRECTION",
 "idempotency_key": "...", "account_id": "...", "session_id": "...",
 "cash_delta_usd": 0.0, "reserved_delta_usd": 0.0,
 "cash_after_usd": 0.0, "reserved_after_usd": 0.0, "available_after_usd": 0.0,
 "order_id": null, "fill_id": null, "group_id": null, "position_key": null,
 "settlement_key": null, "corrects_seq": null,
 "event_source": "PAPER_LEDGER|SIMULATOR|AUTHORITATIVE_SETTLEMENT_EVIDENCE",
 "simulator_version": "PAPER_SIM_V1", "data_label": "...", "detail": {}, "committed_at": 0.0}
```

## GET /api/command/paper/stream (Server-Sent Events)

Publishes only COMMITTED ledger entries, in sequence (the ledger numbers
entries after the account row lock, so sequence order is commit order).
Reconnect with the `Last-Event-ID` header (or `?last=<seq>`) to replay every
entry after that sequence. Events:

* `snapshot` (first frame when no Last-Event-ID; `id` = current sequence):
  `{sequence, balances: <account.data>, latest_entries: [...], last_updated_at, data_label, labels}`
* `ledger` (`id` = sequence): `{sequence, entry: <ledger entry>,
  running_balances: {cash_usd, reserved_usd, available_usd}, committed_at,
  last_updated_at, balances: <account.data, on the last frame of each poll batch>}`
* `heartbeat` (every 15 s): `{sequence, at}`
* `unavailable`: `{why}` when migration 171 is not applied.

## GET /api/command/paper/session

`session` (id, account, `started_at`, frozen `config`, `config_sha`,
`simulator_version`, `reporting_tz`, `status`), `enablement` (env flag and
control row), `health` (`heartbeat_at`, `passes`, `errors`,
`mutation_attempts` (expected 0), `last_mutation_attempt`, `last_pass`,
`recent_heartbeats`), plus top-level `mutation_attempts` and `heartbeats`.

## GET /api/command/paper/derek?limit=100

`opportunities` (paper decisions, newest first: market / side / fixture and
`label` inputs, `p_internal` with `internal_model` {model_id, version,
approval_status, label EXPERIMENTAL_RESEARCH_MODEL, at}, `p_pinnacle` with
`pinnacle` {at, age, qualification}, `p_blended`, the observed `book` and
depth, `proposed_qty`, `limit_price`, `economics` (EV and costs), `verdict`,
`refusal`, `refusals`, `qualification_gaps`, `policy_version`,
`alternatives`, `optimistic`), `refusal_summary_24h`, `orders` (entry paper
orders), `fills` (simulated entry fills), `handoffs`.

## GET /api/command/paper/xavier?limit=100

`positions` (open paper positions, marked as in the account), `standing_orders`
(standing protection, hedge, exit and reduce paper orders), `recommendations`
(the latest review per group: `recommendation`, `alternatives`, `selection`,
`exposure`, `standing`, `confirmed_protection`, `incomplete_search`,
`exceptional`, `measure`, `reviewed_at`).

## GET /api/command/paper/audrey?limit=50

`daily_reports` (latest version per America/New_York day; see
`agents.paper_audrey` for the report fields) and `audit_entries` (findings).

Beside the sections, `days_not_reconciled` (not a section; the page does not
read it): `{kind: "AUDREY_DAY_NOT_RECONCILED", count, days: [{session_id,
day, found_at, activity_total, improvement_task_id}] newest first, at most
`limit`, basis, why}`. `count` is every such finding of the account: a
completed day with paper activity and no Audrey report, one per session and
day (no report is written for it after the fact). On a failed read, `count`
and `days` are null and `why` names the exception.
