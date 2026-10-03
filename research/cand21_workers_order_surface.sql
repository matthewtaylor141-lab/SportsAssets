-- cand21: sportsassets-workers order-capable surface, READ-ONLY.
-- What the workers process (47086de, branch claude/session-njaewf) is doing
-- right now on every lane that can place, cancel or modify a venue order:
-- the control rows each lane reads, every heartbeat, and every order table's
-- activity in the last 7 days. SELECT only; research-sql runs it with
-- default_transaction_read_only=on.

\echo == 1. workers boot marker and the order-path control rows ==
SELECT key, left(value::text, 300) AS value
  FROM ingestion_state
 WHERE key IN ('workers_boot', 'live_trading_paused', 'mirror_live', 'mirror_live_whales',
               'mirror_live_demoted', 'mirror_loss_stop', 'mirror_flatten', 'mirror_post_only_block',
               'copy_overspend_halt', 'live_verified_whales', 'premap_live', 'mapping_quarantine',
               'bettor_live_observation', 'rn1x_shadow', 'rn1x_learn', 'live_clip_overrides',
               'underdog_enabled', 'whale_exit_enabled')
    OR key LIKE 'mirror\_%' ESCAPE '\'
 ORDER BY key;

\echo == 2. every service heartbeat, newest first (detail truncated) ==
SELECT service, status, beat_at::timestamptz(0) AS beat_at,
       round(extract(epoch FROM now() - beat_at))::int AS age_s,
       left(detail::text, 700) AS detail
  FROM service_heartbeats
 ORDER BY beat_at DESC;

\echo == 3. live_orders (copy / manual / mirror standing rows, funded retail key) ==
SELECT 'last_7d' AS win, COALESCE(lane, '(null)') AS lane, status, count(*) AS n,
       min(placed_at)::timestamptz(0) AS first, max(placed_at)::timestamptz(0) AS last
  FROM live_orders WHERE placed_at > now() - interval '7 days'
 GROUP BY 2, 3
UNION ALL
SELECT 'all_time_last', '-', '-', count(*), NULL, max(placed_at)::timestamptz(0) FROM live_orders
 ORDER BY 1, 2, 3;

\echo == 4. live_orders rows in a NON-TERMINAL status (could still be worked by a reaper) ==
SELECT status, COALESCE(lane, '(null)') AS lane, count(*) AS n, max(placed_at)::timestamptz(0) AS newest
  FROM live_orders
 WHERE status NOT IN ('rejected', 'unfilled', 'settled', 'merged', 'cashed_out', 'cancelled', 'error')
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 5. mirror_orders activity, last 7 days (placed, or touched) ==
SELECT state, count(*) AS n,
       max(placed_at)::timestamptz(0) AS last_placed,
       max(updated_at)::timestamptz(0) AS last_touched,
       max(done_at)::timestamptz(0) AS last_done
  FROM mirror_orders
 WHERE placed_at > now() - interval '7 days' OR updated_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 1;
SELECT 'mirror_orders_newest_touch' AS what, max(updated_at)::timestamptz(0) AS at FROM mirror_orders;

\echo == 6. mirror_orders NOT in a closed state (placing/open/unknown) ==
SELECT id, book_id, left(us_market_slug, 40) AS slug, kind, side, tif, state, venue_state,
       qty, filled, order_id IS NOT NULL AS has_order_id,
       placed_at::timestamptz(0), updated_at::timestamptz(0), done_at::timestamptz(0)
  FROM mirror_orders
 WHERE state IN ('placing', 'open', 'unknown')
 ORDER BY placed_at DESC LIMIT 20;

\echo == 7. mirror_books not closed (what a SELL/flatten path would act on) ==
SELECT id, whale, left(us_market_slug, 40) AS slug, intent, state, frozen_reason,
       ledger_net, venue_net, open_order_id, opened_at::timestamptz(0), updated_at::timestamptz(0)
  FROM mirror_books WHERE state <> 'closed' ORDER BY id;

\echo == 8. every table whose name contains "order": size and write counters since stats reset ==
SELECT relname, n_live_tup, n_tup_ins, n_tup_upd, n_tup_del,
       last_autoanalyze::timestamptz(0) AS last_autoanalyze
  FROM pg_stat_user_tables WHERE relname LIKE '%order%' ORDER BY relname;
SELECT stats_reset::timestamptz(0) AS db_stats_reset FROM pg_stat_database WHERE datname = current_database();
