-- READ-ONLY. SMALL LIVE PILOT V1, deliverable 4 (idempotent submission,
-- partial fills, cancellation, duplicate prevention, position reconciliation,
-- emergency stand-down) for the Polymarket US retail ACTUAL lane
-- (execution_intent.ActualLane -> execmirror_orders -> execmirror.Venue).
-- Every statement is a SELECT. No credential value is read: the account
-- fingerprint is reported only as present / absent.

\echo D4-1 SMALL LIVE CONTROL singleton (mode, halted, halt reason, clear)
SELECT id, mode, halted, halted_at, halt_reason, halt_parity_id, cleared_by,
       cleared_at, updated_at
  FROM small_live_control;

\echo D4-2 SMALL LIVE CONTROL EVENTS by action (all time) and the newest 10
SELECT action, count(*) AS n, min(at) AS first_at, max(at) AS last_at
  FROM small_live_control_events GROUP BY 1 ORDER BY 1;
SELECT event_id, action, actor, reason, parity_id, at
  FROM small_live_control_events ORDER BY at DESC LIMIT 10;

\echo D4-3 EXECUTION MIRROR CONTROL (the ACTUAL lane switch and emergency stop; fingerprint presence only)
SELECT enabled, stopped, flatten_on_stop, stop_done_at, scale, rounding,
       max_order_usd, cutover_at, (account_fingerprint IS NOT NULL) AS account_fingerprint_set,
       actor, revision, updated_at
  FROM execmirror_control;

\echo D4-4 GLOBAL EXECUTION GATE SWITCH live_trading_paused (ingestion_state) and its type
SELECT key, value, jsonb_typeof(value) AS value_type
  FROM ingestion_state WHERE key IN ('live_trading_paused', 'mirror_live');

\echo D4-5 EXECUTION INTENTS last 24 h by actual_state and actual_refusal
SELECT actual_state, coalesce(actual_refusal, '-') AS actual_refusal, count(*) AS n,
       count(*) FILTER (WHERE live_eligible) AS live_eligible_n,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM execution_intents WHERE created_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo D4-6 EXECUTION INTENTS all time by actual_state, with actual_mirror_id set
SELECT actual_state, count(*) AS n, count(actual_mirror_id) AS with_mirror_id,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM execution_intents GROUP BY 1 ORDER BY 2 DESC;

\echo D4-7 EXECUTION INTENTS last 24 h by strategy and policy version
SELECT strategy, coalesce(policy_version, '-') AS policy_version, actual_state, count(*) AS n
  FROM execution_intents WHERE created_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo D4-8 SMALL LIVE ORDER EVENTS all time (count, by state, newest 10)
SELECT count(*) AS n_all_time, min(observed_at) AS first_at, max(observed_at) AS last_at
  FROM small_live_order_events;
SELECT state, count(*) AS n FROM small_live_order_events GROUP BY 1 ORDER BY 1;
SELECT event_id, execution_id, venue_order_id, state, cum_qty, avg_price, source, observed_at
  FROM small_live_order_events ORDER BY observed_at DESC LIMIT 10;

\echo D4-9 SMALLLIVE RECONCILIATIONS: status counts, newest instant, newest 10 rows
SELECT status, count(*) AS n, max(reconciled_at) AS newest_reconciled_at,
       max(changed_at) AS newest_changed_at
  FROM smalllive_reconciliations GROUP BY 1 ORDER BY 1;
SELECT group_id, venue, status, reconciled_at, changed_at,
       jsonb_array_length(discrepancies) AS n_discrepancies,
       left(discrepancies::text, 300) AS discrepancies_head
  FROM smalllive_reconciliations ORDER BY reconciled_at DESC LIMIT 10;
SELECT now() AS db_now,
       extract(epoch FROM now() - max(reconciled_at))::int AS newest_reconciliation_age_s
  FROM smalllive_reconciliations;

\echo D4-10 SMALLLIVE RECONCILIATIONS with a DISCREPANCY (all, newest 20)
SELECT group_id, venue, reconciled_at, left(discrepancies::text, 400) AS discrepancies
  FROM smalllive_reconciliations WHERE status = 'DISCREPANCY'
 ORDER BY reconciled_at DESC LIMIT 20;

\echo D4-11 EXECMIRROR ORDERS all time by state, role, exclusion (venue order id present)
SELECT state, role, coalesce(exclusion, '-') AS exclusion, count(*) AS n,
       count(venue_order_id) AS with_venue_order_id,
       count(execution_intent_id) AS with_intent,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM execmirror_orders GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 60;

\echo D4-12 EXECMIRROR ORDERS still non-terminal (open states) and their age
SELECT mirror_id, role, state, venue_state, live_qty, cum_qty,
       (venue_order_id IS NOT NULL) AS has_venue_order_id, created_at, updated_at,
       last_polled_at, left(coalesce(error::text, ''), 200) AS error_head
  FROM execmirror_orders
 WHERE state IN ('PLANNED', 'SUBMITTING', 'UNKNOWN', 'OPEN', 'PARTIALLY_FILLED', 'CANCEL_REQUESTED')
 ORDER BY created_at DESC LIMIT 40;

\echo D4-13 EXECMIRROR FILLS all time and SMALLLIVE HANDOFFS by state
SELECT count(*) AS fills, coalesce(sum(qty), 0) AS qty, coalesce(sum(fee_usd), 0) AS fees,
       min(observed_at) AS first_at, max(observed_at) AS last_at
  FROM execmirror_fills;
SELECT state, count(*) AS n, coalesce(sum(live_held), 0) AS live_held,
       max(updated_at) AS last_at
  FROM smalllive_handoffs GROUP BY 1 ORDER BY 1;

\echo D4-14 NET LIVE INVENTORY by market from venue fills (buys minus sells), nonzero only
SELECT us_market_slug,
       sum(CASE WHEN intent LIKE 'ORDER_INTENT_BUY%' THEN qty ELSE -qty END) AS net_qty,
       count(*) AS fills, max(observed_at) AS last_fill_at
  FROM execmirror_fills GROUP BY 1
HAVING sum(CASE WHEN intent LIKE 'ORDER_INTENT_BUY%' THEN qty ELSE -qty END) <> 0
 ORDER BY 4 DESC LIMIT 40;

\echo D4-15 EXECMIRROR EVENTS by kind: all time, and last 24 h
SELECT kind, count(*) AS n_all, count(*) FILTER (WHERE at > now() - interval '24 hours') AS n_24h,
       max(at) AS last_at
  FROM execmirror_events GROUP BY 1 ORDER BY 4 DESC LIMIT 60;

\echo D4-16 CONTROL and STOP events (all time, newest 30)
SELECT event_id, kind, at, detail->>'actor' AS actor, detail->>'flatten' AS flatten,
       left(detail::text, 300) AS detail_head
  FROM execmirror_events
 WHERE kind LIKE 'CONTROL_%' OR kind LIKE 'EMERGENCY_STOP%' OR kind LIKE 'STOP_%'
    OR kind LIKE 'HALTED_%' OR kind LIKE 'FLATTEN_%'
 ORDER BY at DESC LIMIT 30;

\echo D4-17 EXECMIRROR SNAPSHOTS newest (account snapshot age; no values beyond counts)
SELECT count(*) AS snapshots, max(at) AS newest_at,
       extract(epoch FROM now() - max(at))::int AS newest_age_s
  FROM execmirror_snapshots;
SELECT snapshot_id, at, (account_fingerprint IS NOT NULL) AS fingerprint_set, open_orders,
       jsonb_typeof(positions) AS positions_type,
       CASE WHEN jsonb_typeof(positions) = 'array' THEN jsonb_array_length(positions) END AS positions_n,
       CASE WHEN jsonb_typeof(balances) = 'array' THEN jsonb_array_length(balances) END AS balances_n,
       left(reconciliation::text, 300) AS reconciliation_head
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 3;

\echo D4-18 CANONICAL INTENT EXECUTIONS last 24 h by adapter, mode, state, exclusion
SELECT adapter, mode, state, coalesce(exclusion, '-') AS exclusion, count(*) AS n,
       max(created_at) AS last_at
  FROM canonical_intent_executions WHERE created_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;

\echo D4-19 SMALL LIVE adapter all time: any mode other than SHADOW, any venue order id
SELECT mode, count(*) AS n, count(*) FILTER (WHERE refs ? 'venue_order_id') AS with_venue_order_id
  FROM canonical_intent_executions WHERE adapter = 'SMALL_LIVE' GROUP BY 1;

\echo D4-20 LIVE PARITY LEDGER last 24 h by parity_state
SELECT intent_kind, parity_state, count(*) AS n, max(created_at) AS last_at
  FROM live_parity_ledger WHERE created_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo D4-21 LOOP HEALTH of the ACTUAL lane runner and Audrey (execmirror and audrey loops)
SELECT loop_name, process, left(commit_sha, 12) AS commit_sha, cadence_s, last_start_at,
       last_success_at, last_error_at, left(coalesce(last_error, ''), 160) AS last_error,
       successes, errors, detail->>'state' AS last_state, updated_at
  FROM runtime_loop_health
 WHERE loop_name ILIKE '%execmirror%' OR loop_name ILIKE '%audrey%'
    OR loop_name ILIKE '%small%live%' OR loop_name ILIKE '%mirror%'
 ORDER BY loop_name, process;
