-- Execution mirror watch (read-only): the control row, the runner's account
-- snapshots, mirror events, mirror orders and exclusions, completed-game
-- paper ENTRY orders since the cutover, and the small-live management rows.
\echo '== M0 · control =='
SELECT enabled, stopped, cutover_at, left(account_fingerprint, 12) AS fp, scale, rounding,
       max_order_usd, revision, actor, updated_at,
       baseline->'balances' AS baseline_balances, baseline->'positions_net' AS baseline_positions,
       baseline->'open_orders' AS baseline_open_orders
  FROM execmirror_control;
\echo '== M1 · account snapshots (latest 5) =='
SELECT at, left(account_fingerprint, 12) AS fp, left(balances::text, 200) AS balances,
       jsonb_array_length(coalesce(positions, '[]'::jsonb)) AS positions,
       left(reconciliation::text, 400) AS reconciliation
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 5;
SELECT count(*) AS snapshots_since_cutover, min(at) AS first_at, max(at) AS last_at
  FROM execmirror_snapshots
 WHERE at >= (SELECT cutover_at FROM execmirror_control WHERE id = 1);
\echo '== M2 · mirror events (latest 20) =='
SELECT at, kind, mirror_id, paper_order_id, left(detail::text, 300) AS detail
  FROM execmirror_events ORDER BY at DESC LIMIT 20;
\echo '== M3 · mirror orders =='
SELECT mirror_id, paper_order_id, strategy, role, state, venue_state, exclusion, us_market_slug,
       intent, paper_qty, scaled_qty, live_qty, rounding_delta, wire_price, venue_order_id,
       cum_qty, avg_px, fees_usd, latency_ms, created_at, submit_started_at, accepted_at, updated_at
  FROM execmirror_orders ORDER BY created_at DESC LIMIT 20;
\echo '== M4 · completed-game paper ENTRY orders since the cutover =='
SELECT o.order_id, o.decision_id, o.strategy, o.state, o.qty, o.limit_price, o.filled_qty,
       o.created_at, o.label->>'policy_version' AS label_policy
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
   AND o.created_at >= (SELECT cutover_at FROM execmirror_control WHERE id = 1)
 ORDER BY o.created_at DESC LIMIT 20;
\echo '== M5 · completed-game decisions since the cutover, by verdict and refusal =='
SELECT d.verdict, d.refusal, count(*) AS n,
       round(max((d.policy_decision->>'gross_edge_pp')::numeric), 3) AS best_gross_edge_pp
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND d.decided_at >= (SELECT cutover_at FROM execmirror_control WHERE id = 1)
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;
\echo '== M6 · small-live management rows =='
SELECT (SELECT count(*) FROM smalllive_handoffs) AS handoffs,
       (SELECT count(*) FROM smalllive_reviews) AS reviews,
       (SELECT count(*) FROM smalllive_reconciliations) AS reconciliations;
SELECT reconciled_at, changed_at, group_id, venue, status, left(discrepancies::text, 300) AS discrepancies
  FROM smalllive_reconciliations ORDER BY reconciled_at DESC LIMIT 5;
