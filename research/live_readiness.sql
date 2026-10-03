-- Small-live readiness (read-only): execution mirror control and records,
-- paper ENTRY orders and simulated fills by strategy, market subscription
-- and book-currency state, and the paused funded desk account.
\echo '== L0 · execution mirror control =='
SELECT enabled, stopped, flatten_on_stop, scale, rounding, cutover_at,
       left(account_fingerprint, 12) AS fingerprint_prefix,
       (baseline <> '{}'::jsonb) AS has_baseline, max_order_usd, actor, revision, updated_at
  FROM execmirror_control;
\echo '== L1 · execution mirror records =='
SELECT (SELECT count(*) FROM execmirror_orders) AS mirror_orders,
       (SELECT count(*) FROM execmirror_fills) AS mirror_fills;
\echo '== L2 · paper ENTRY orders and simulated fills by strategy (48 h) =='
SELECT o.strategy, count(*) AS entry_orders,
       count(*) FILTER (WHERE o.filled_qty > 0) AS filled_orders,
       round(avg(o.qty)::numeric, 1) AS avg_qty,
       round(avg(o.wire_price)::numeric, 4) AS avg_wire_price,
       min(o.created_at) AS first_at, max(o.created_at) AS last_at
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
   AND o.created_at > now() - interval '48 hours'
 GROUP BY 1 ORDER BY 2 DESC;
\echo '== L3 · completed-game ENTER decisions (48 h) with order and fill =='
SELECT d.decision_id, d.decided_at, d.us_market_slug, d.p_pinnacle, d.proposed_qty, d.limit_price,
       d.policy_decision->>'gross_edge_pp' AS gross_edge_pp,
       d.policy_decision->>'net_expected_profit_usd' AS net_ev_usd,
       o.order_id, o.state, o.filled_qty,
       (SELECT string_agg(f.fill_id || '@' || f.price::text || ' fee ' || f.fee_usd::text, '; ')
          FROM paper_fills f WHERE f.order_id = o.order_id) AS fills
  FROM paper_decisions d LEFT JOIN paper_orders o ON o.decision_id = d.decision_id AND o.role = 'ENTRY'
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND d.verdict = 'ENTER'
   AND d.decided_at > now() - interval '48 hours'
 ORDER BY d.decided_at DESC LIMIT 15;
\echo '== L4 · subscription / currency / pacing state rows =='
SELECT key, left(value::text, 700) AS value
  FROM ingestion_state
 WHERE key ILIKE '%subscription%' OR key ILIKE '%stream%' OR key ILIKE '%currency%' OR key ILIKE '%pace%'
 ORDER BY key LIMIT 12;
\echo '== L5 · last cycle venue errors and subscription block =='
SELECT left((value->'venue_errors')::text, 900) AS venue_errors,
       left((value->'market_subscription')::text, 900) AS market_subscription,
       left((value->'venue_rate_controls')::text, 500) AS venue_rate_controls
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
