-- READ-ONLY. SMALL LIVE PILOT V1, deliverables 1 and 6 (production Command
-- Center accuracy). Every statement is a SELECT. Sections:
--   A  agents: agent_identities + agent_status as the floor derives a desk
--      (heartbeat age against 3 x the recorded cadence, floor 900 s; a run in
--      progress), each agent's newest recorded output, agent_runs over 24 h,
--      runtime_loop_health rows and the service heartbeats of the agent loops
--   B  PAPER accounting truth: ledger sums, positions recomputed exactly as
--      bettor_paper_ledger.POSITIONS_SQL and _position_from do (realized,
--      fees, open cost basis), the cash identity, orders by state, the latest
--      equity snapshot (the equity curve source), the management epoch inputs
--   C  SMALL LIVE and the legacy mirror controls as stored (mode, halt, stop,
--      cap, newest account snapshot and its age)
--   D  market data freshness the pages draw on (heartbeats of the planes)

\echo A1 AGENT DESKS as the floor derives them (stale bound = greatest of 900 s and 3 x the largest cadence key ending _s)
WITH s AS (
  SELECT i.agent_id, s.state, left(s.activity, 140) AS activity, s.last_heartbeat_at,
         s.last_run_started_at, s.last_run_finished_at, s.runs, s.errors,
         left(s.last_error, 120) AS last_error, s.cadence,
         (SELECT max((v.value)::text::numeric)
            FROM jsonb_each(CASE WHEN jsonb_typeof(s.cadence) = 'object' THEN s.cadence ELSE '{}'::jsonb END) v
           WHERE v.key LIKE '%\_s' AND jsonb_typeof(v.value) = 'number') AS max_cadence_s
    FROM agent_identities i LEFT JOIN agent_status s USING (agent_id))
SELECT agent_id, state, activity,
       last_heartbeat_at, round(extract(epoch FROM now() - last_heartbeat_at)::numeric, 1) AS hb_age_s,
       greatest(900, 3 * coalesce(max_cadence_s, 0)) AS stale_after_s,
       CASE WHEN last_heartbeat_at IS NULL THEN 'STALE_NO_HEARTBEAT'
            WHEN extract(epoch FROM now() - last_heartbeat_at) > greatest(900, 3 * coalesce(max_cadence_s, 0)) THEN 'STALE'
            ELSE 'HEARTBEAT_FRESH' END AS floor_heartbeat_verdict,
       (state = 'EVALUATING' AND last_run_started_at IS NOT NULL
        AND (last_run_finished_at IS NULL OR last_run_finished_at < last_run_started_at)
        AND extract(epoch FROM now() - last_run_started_at) <= greatest(600, greatest(900, 3 * coalesce(max_cadence_s, 0)))) AS run_in_progress,
       last_run_started_at, last_run_finished_at, runs, errors, last_error, cadence::text AS cadence
  FROM s ORDER BY agent_id;

\echo A2 NEWEST RECORDED OUTPUT per agent (the floor active window is 300 s) with counts in the last 300 s and 1 h
SELECT 'DEREK paper_decisions' AS output, max(decided_at) AS newest,
       round(extract(epoch FROM now() - max(decided_at))::numeric, 1) AS age_s,
       count(*) FILTER (WHERE decided_at >= now() - interval '300 seconds') AS n_300s,
       count(*) FILTER (WHERE decided_at >= now() - interval '1 hour') AS n_1h,
       count(*) FILTER (WHERE decided_at >= now() - interval '1 hour' AND verdict = 'ENTER') AS enter_1h
  FROM paper_decisions WHERE decided_at >= now() - interval '24 hours'
UNION ALL
SELECT 'XAVIER xavier_management_assessments', max(assessed_at),
       round(extract(epoch FROM now() - max(assessed_at))::numeric, 1),
       count(*) FILTER (WHERE assessed_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE assessed_at >= now() - interval '1 hour'), NULL
  FROM xavier_management_assessments WHERE assessed_at >= now() - interval '24 hours'
UNION ALL
SELECT 'XAVIER paper_xavier_reviews', max(reviewed_at),
       round(extract(epoch FROM now() - max(reviewed_at))::numeric, 1),
       count(*) FILTER (WHERE reviewed_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE reviewed_at >= now() - interval '1 hour'), NULL
  FROM paper_xavier_reviews WHERE reviewed_at >= now() - interval '24 hours'
UNION ALL
SELECT 'AUDREY paper_audrey_findings', max(found_at),
       round(extract(epoch FROM now() - max(found_at))::numeric, 1),
       count(*) FILTER (WHERE found_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE found_at >= now() - interval '1 hour'), NULL
  FROM paper_audrey_findings WHERE found_at >= now() - interval '24 hours'
UNION ALL
SELECT 'AUDREY audrey_audit_reports', max(computed_at),
       round(extract(epoch FROM now() - max(computed_at))::numeric, 1),
       count(*) FILTER (WHERE computed_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE computed_at >= now() - interval '1 hour'), NULL
  FROM audrey_audit_reports WHERE computed_at >= now() - interval '7 days'
UNION ALL
SELECT 'KAREN karen_challenges', max(challenged_at),
       round(extract(epoch FROM now() - max(challenged_at))::numeric, 1),
       count(*) FILTER (WHERE challenged_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE challenged_at >= now() - interval '1 hour'), NULL
  FROM karen_challenges WHERE challenged_at >= now() - interval '24 hours'
UNION ALL
SELECT 'ARCHER eddie_execution_estimates', max(estimated_at),
       round(extract(epoch FROM now() - max(estimated_at))::numeric, 1),
       count(*) FILTER (WHERE estimated_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE estimated_at >= now() - interval '1 hour'), NULL
  FROM eddie_execution_estimates WHERE estimated_at >= now() - interval '24 hours'
UNION ALL
SELECT 'SCOUT scout_features', max(proposed_at),
       round(extract(epoch FROM now() - max(proposed_at))::numeric, 1),
       count(*) FILTER (WHERE proposed_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE proposed_at >= now() - interval '1 hour'), NULL
  FROM scout_features
UNION ALL
SELECT 'ADRIANA adriana_arb_scans', max(finished_at),
       round(extract(epoch FROM now() - max(finished_at))::numeric, 1),
       count(*) FILTER (WHERE finished_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE finished_at >= now() - interval '1 hour'), NULL
  FROM adriana_arb_scans WHERE started_at >= now() - interval '24 hours'
UNION ALL
SELECT 'ALLOCATOR intel_runs CYCLE', max(finished_at),
       round(extract(epoch FROM now() - max(finished_at))::numeric, 1),
       count(*) FILTER (WHERE finished_at >= now() - interval '300 seconds'),
       count(*) FILTER (WHERE finished_at >= now() - interval '1 hour'), NULL
  FROM intel_runs WHERE component = 'CYCLE' AND started_at >= now() - interval '24 hours';

\echo A3 DECISIONS per 10 minutes over the last 2 hours (is the entry lane still deciding)
SELECT date_trunc('hour', decided_at) + floor(extract(minute FROM decided_at) / 10) * interval '10 minutes' AS bucket,
       count(*) AS decisions, count(*) FILTER (WHERE verdict = 'ENTER') AS enter
  FROM paper_decisions WHERE decided_at >= now() - interval '2 hours' GROUP BY 1 ORDER BY 1;

\echo A4 AGENT RUNS over 24 h per agent (newest start, newest finish, unfinished, outcomes)
SELECT agent_id, count(*) AS runs, max(started_at) AS newest_start, max(finished_at) AS newest_finish,
       count(*) FILTER (WHERE finished_at IS NULL) AS unfinished,
       jsonb_object_agg(coalesce(outcome, 'NULL'), 1) AS outcomes_seen
  FROM agent_runs WHERE started_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 1;

\echo A5 RUNTIME LOOP HEALTH rows (every row; status by the 3 x cadence rule on this row alone)
SELECT process, loop_name, left(commit_sha, 8) AS commit, cadence_s, last_start_at, last_success_at,
       round(extract(epoch FROM now() - last_success_at)::numeric, 1) AS success_age_s,
       CASE WHEN last_success_at IS NOT NULL AND extract(epoch FROM now() - last_success_at) <= 3 * cadence_s THEN 'HEALTHY'
            WHEN last_success_at IS NULL AND last_start_at IS NOT NULL AND extract(epoch FROM now() - last_start_at) <= 3 * cadence_s THEN 'STARTING'
            ELSE 'UNHEALTHY' END AS row_status,
       starts, successes, errors, last_error_at, left(last_error, 80) AS last_error, updated_at
  FROM runtime_loop_health ORDER BY process, loop_name;

\echo A6 SERVICE HEARTBEATS of the agent loops and the paper runtime (status, age)
SELECT service, status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       left(detail::text, 200) AS detail
  FROM service_heartbeats
 WHERE service LIKE 'agent%' OR service LIKE '%paper%' OR service IN ('improvement_pipeline', 'red_team_readiness', 'market_plane', 'universal_market_plane', 'kalshi_ws_market_data', 'execmirror')
 ORDER BY service;

\echo A7 INGESTION STATE heartbeats the loop health route reads for agents and the paper pass (key, age)
SELECT key, left(value::text, 220) AS value,
       CASE WHEN jsonb_typeof(value) = 'object' AND value ? 'at' AND (value->>'at') ~ '^[0-9.]+$'
            THEN round((extract(epoch FROM now()) - (value->>'at')::numeric), 1) END AS age_s_if_epoch_at
  FROM ingestion_state
 WHERE key LIKE 'agent.%' OR key LIKE 'paper%' OR key LIKE 'xavier%' OR key LIKE 'audrey%' OR key LIKE 'pinnapi_feed%'
 ORDER BY key LIMIT 60;

\echo B1 PAPER LEDGER sums for paper_acct_main (cash and reserved by sum, running balance at the last row)
SELECT count(*) AS entries, max(seq) AS last_seq, max(committed_at) AS last_committed_at,
       sum(cash_delta_usd) AS cash_by_sum, sum(reserved_delta_usd) AS reserved_by_sum,
       sum(cash_delta_usd) - sum(reserved_delta_usd) AS available_by_sum,
       (SELECT cash_after_usd FROM paper_ledger WHERE account_id = 'paper_acct_main' ORDER BY seq DESC LIMIT 1) AS last_cash_after,
       (SELECT reserved_after_usd FROM paper_ledger WHERE account_id = 'paper_acct_main' ORDER BY seq DESC LIMIT 1) AS last_reserved_after,
       (SELECT starting_cash_usd FROM paper_accounts WHERE account_id = 'paper_acct_main') AS starting_cash
  FROM paper_ledger WHERE account_id = 'paper_acct_main';

\echo B2 PAPER LEDGER cash by kind
SELECT kind, count(*) AS n, sum(cash_delta_usd) AS cash, sum(reserved_delta_usd) AS reserved,
       min(committed_at) AS first_at, max(committed_at) AS last_at
  FROM paper_ledger WHERE account_id = 'paper_acct_main' GROUP BY 1 ORDER BY 1;

\echo B3 POSITIONS recomputed as bettor_paper_ledger.balances does (realized incl. fees, open cost basis, fees)
WITH f AS (
  SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(gross_usd) FILTER (WHERE direction = 'BUY'), 0) AS buy_gross,
         coalesce(sum(fee_usd) FILTER (WHERE direction = 'BUY'), 0) AS buy_fees,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold,
         coalesce(sum(gross_usd) FILTER (WHERE direction = 'SELL'), 0) AS sale_gross,
         coalesce(sum(fee_usd) FILTER (WHERE direction = 'SELL'), 0) AS sale_fees
    FROM paper_fills WHERE account_id = 'paper_acct_main' GROUP BY 1, 2, 3),
s AS (
  SELECT DISTINCT ON (position_key) position_key, qty, payout_usd
    FROM paper_settlements WHERE account_id = 'paper_acct_main' ORDER BY position_key, version DESC),
p AS (
  SELECT f.*, coalesce(s.qty, 0) AS settled, coalesce(s.payout_usd, 0) AS payout,
         CASE WHEN f.bought > 0 THEN (f.buy_gross + f.buy_fees) / f.bought ELSE 0 END AS avg
    FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side),
q AS (
  SELECT *, bought - sold - settled AS open_qty,
         (sale_gross - sale_fees - avg * sold) + CASE WHEN settled > 0 THEN payout - avg * settled ELSE 0 END AS realized
    FROM p)
SELECT count(*) AS positions, count(*) FILTER (WHERE open_qty > 0.000000001) AS open_positions,
       round(sum(realized), 6) AS realized_total_incl_fees,
       round(sum(avg * open_qty) FILTER (WHERE open_qty > 0.000000001), 6) AS open_cost_basis,
       round(sum(buy_fees + sale_fees), 6) AS fees_total,
       round(sum(sale_gross - sale_fees) + sum(payout) - sum(buy_gross + buy_fees), 6) AS net_fill_and_settlement_cash,
       round(sum(realized) + sum(buy_fees + sale_fees), 6) AS realized_gross_of_fees,
       count(*) FILTER (WHERE open_qty < -0.000000001) AS negative_open
  FROM q;

\echo B4 POSITIONS by strategy (realized incl. fees, fees, open)
WITH f AS (
  SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(gross_usd) FILTER (WHERE direction = 'BUY'), 0) AS buy_gross,
         coalesce(sum(fee_usd) FILTER (WHERE direction = 'BUY'), 0) AS buy_fees,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold,
         coalesce(sum(gross_usd) FILTER (WHERE direction = 'SELL'), 0) AS sale_gross,
         coalesce(sum(fee_usd) FILTER (WHERE direction = 'SELL'), 0) AS sale_fees,
         min(filled_at) AS first_fill, max(filled_at) AS last_fill
    FROM paper_fills WHERE account_id = 'paper_acct_main' GROUP BY 1, 2, 3),
s AS (
  SELECT DISTINCT ON (position_key) position_key, qty, payout_usd
    FROM paper_settlements WHERE account_id = 'paper_acct_main' ORDER BY position_key, version DESC),
q AS (
  SELECT f.*, coalesce(s.qty, 0) AS settled, coalesce(s.payout_usd, 0) AS payout,
         CASE WHEN f.bought > 0 THEN (f.buy_gross + f.buy_fees) / f.bought ELSE 0 END AS avg
    FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side)
SELECT coalesce(strategy, '(null)') AS strategy, count(*) AS positions,
       count(*) FILTER (WHERE bought - sold - settled > 0.000000001) AS open,
       round(sum((sale_gross - sale_fees - avg * sold) + CASE WHEN settled > 0 THEN payout - avg * settled ELSE 0 END), 2) AS realized_incl_fees,
       round(sum(buy_fees + sale_fees), 2) AS fees, min(first_fill) AS first_fill, max(last_fill) AS last_fill
  FROM q GROUP BY 1 ORDER BY 1;

\echo B5 FILLS and SETTLEMENTS totals (fills count, fees, gross by direction, newest; settlement rows, latest versions, payout)
SELECT (SELECT count(*) FROM paper_fills WHERE account_id = 'paper_acct_main') AS fills,
       (SELECT sum(fee_usd) FROM paper_fills WHERE account_id = 'paper_acct_main') AS fees,
       (SELECT sum(gross_usd) FILTER (WHERE direction = 'BUY') FROM paper_fills WHERE account_id = 'paper_acct_main') AS buy_gross,
       (SELECT sum(gross_usd) FILTER (WHERE direction = 'SELL') FROM paper_fills WHERE account_id = 'paper_acct_main') AS sell_gross,
       (SELECT max(filled_at) FROM paper_fills WHERE account_id = 'paper_acct_main') AS newest_fill,
       (SELECT count(*) FROM paper_settlements WHERE account_id = 'paper_acct_main') AS settlement_rows,
       (SELECT count(DISTINCT position_key) FROM paper_settlements WHERE account_id = 'paper_acct_main') AS settled_positions,
       (SELECT max(settled_at) FROM paper_settlements WHERE account_id = 'paper_acct_main') AS newest_settlement,
       (SELECT count(*) FROM paper_fills f WHERE f.account_id = 'paper_acct_main'
           AND NOT EXISTS (SELECT 1 FROM paper_ledger l WHERE l.fill_id = f.fill_id AND l.kind IN ('FILL', 'SALE'))) AS fills_without_ledger_row;

\echo B6 ORDERS by state and role (open states are PENDING_SIMULATION RESTING PARTIALLY_FILLED CANCEL_PENDING)
SELECT state, role, count(*) AS orders, sum(qty) AS qty, sum(filled_qty) AS filled_qty,
       sum(reserved_remaining_usd) AS reserved_remaining, max(updated_at) AS newest_update
  FROM paper_orders WHERE account_id = 'paper_acct_main' GROUP BY 1, 2 ORDER BY 1, 2;

\echo B7 EQUITY SNAPSHOTS newest 3 (the equity curve source) and the count in the last hour
SELECT at, cash_usd, reserved_usd, marked_value_usd, equity_usd, realized_pnl_usd, unrealized_pnl_usd,
       unmarked_positions, last_sequence,
       (SELECT count(*) FROM paper_equity_snapshots WHERE account_id = 'paper_acct_main' AND at >= now() - interval '1 hour') AS snapshots_1h
  FROM paper_equity_snapshots WHERE account_id = 'paper_acct_main' ORDER BY at DESC LIMIT 3;

\echo B8 MANAGEMENT EPOCH inputs (epoch 2026-10-05 04:00 UTC): ledger cash at the epoch and after, fees after the epoch
SELECT sum(cash_delta_usd) FILTER (WHERE committed_at < timestamptz '2026-10-05 04:00:00+00') AS ledger_cash_at_epoch,
       sum(reserved_delta_usd) FILTER (WHERE committed_at < timestamptz '2026-10-05 04:00:00+00') AS ledger_reserved_at_epoch,
       sum(cash_delta_usd) FILTER (WHERE committed_at >= timestamptz '2026-10-05 04:00:00+00') AS ledger_cash_flow_after_epoch,
       count(*) FILTER (WHERE committed_at >= timestamptz '2026-10-05 04:00:00+00') AS entries_after_epoch,
       (SELECT sum(f.fee_usd) FROM paper_fills f LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id AND l.kind IN ('FILL', 'SALE')
         WHERE f.account_id = 'paper_acct_main' AND coalesce(l.committed_at, f.recorded_at) >= timestamptz '2026-10-05 04:00:00+00') AS fees_after_epoch,
       (SELECT sum(f.fee_usd) FROM paper_fills f LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id AND l.kind IN ('FILL', 'SALE')
         WHERE f.account_id = 'paper_acct_main' AND coalesce(l.committed_at, f.recorded_at) < timestamptz '2026-10-05 04:00:00+00') AS fees_before_epoch
  FROM paper_ledger WHERE account_id = 'paper_acct_main';

\echo C1 SMALL LIVE control (SHADOW only by CHECK) and its halt events
SELECT id, mode, halted, halted_at, left(halt_reason, 120) AS halt_reason, cleared_by, cleared_at, updated_at,
       (SELECT count(*) FROM small_live_control_events) AS events,
       (SELECT max(at) FROM small_live_control_events) AS newest_event
  FROM small_live_control;

\echo C2 LEGACY MIRROR control (stop, cap, scale, fingerprint prefix) and the newest account snapshot age
SELECT enabled, stopped, stop_done_at, scale, max_order_usd, rounding, cutover_at,
       left(account_fingerprint, 8) AS fp8, revision, updated_at,
       (SELECT max(at) FROM execmirror_snapshots) AS newest_snapshot_at,
       (SELECT round((extract(epoch FROM now() - max(at)) / 3600.0)::numeric, 2) FROM execmirror_snapshots) AS newest_snapshot_age_h
  FROM execmirror_control;

\echo C3 KILL SWITCH inputs (ingestion_state live_trading_paused and mirror_loss_stop)
SELECT key, left(value::text, 200) AS value FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'mirror_loss_stop') ORDER BY 1;

\echo D1 MARKET DATA the pages draw on: newest equity snapshot, newest decision, plane heartbeats
SELECT 'paper_equity_snapshots newest' AS what,
       (SELECT max(at) FROM paper_equity_snapshots WHERE account_id = 'paper_acct_main') AS newest, NULL::bigint AS n
UNION ALL
SELECT 'market_plane heartbeat', (SELECT beat_at FROM service_heartbeats WHERE service = 'market_plane'), NULL
UNION ALL
SELECT 'universal_market_plane heartbeat', (SELECT beat_at FROM service_heartbeats WHERE service = 'universal_market_plane'), NULL
UNION ALL
SELECT 'kalshi_ws_market_data heartbeat', (SELECT beat_at FROM service_heartbeats WHERE service = 'kalshi_ws_market_data'), NULL
UNION ALL
SELECT 'db now', now(), NULL;
