-- READ-ONLY. RC6 lane identity-debt, follow-up. SELECT only.
--   (a) our legacy books with open rows: ai_trades / live_orders /
--       engine_fills by age and identity; the one rn1x condition absent from
--       markets; and whether ANY of our books ever traded a token the
--       positions dead-letter holds.
--   (b) reconciler run 5698 (finished 2026-10-09 03:35:42Z, "ingested 10
--       missed fills"): whale 40's poll rows detected inside the run window
--       with their detection lag and what our ledgers hold for each; the
--       same split for every run with misses today; and the walk's coverage
--       holes between consecutive runs, per wallet (whale id only).
\echo == A4 ai_trades open rows by age and identity (legacy paper AI follower) ==
SELECT CASE WHEN placed_at >= now() - interval '7 days' THEN 'PLACED_WITHIN_7D'
            WHEN placed_at >= now() - interval '30 days' THEN 'PLACED_7_TO_30D'
            ELSE 'PLACED_OVER_30D' END AS age,
       count(*) AS open_rows,
       sum(CASE WHEN coalesce(condition_id, '') = '' THEN 1 ELSE 0 END) AS no_condition_id,
       sum(CASE WHEN NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = a.asset)
                THEN 1 ELSE 0 END) AS token_unknown,
       sum(CASE WHEN coalesce(condition_id, '') = '' AND NOT EXISTS (
                SELECT 1 FROM market_tokens mt WHERE mt.token_id = a.asset)
                THEN 1 ELSE 0 END) AS identity_unknown,
       round(sum(filled_notional)::numeric, 2) AS filled_notional,
       sum(CASE WHEN shares > 0 THEN 1 ELSE 0 END) AS with_shares,
       min(placed_at) AS first_placed, max(placed_at) AS last_placed
  FROM ai_trades a WHERE status = 'open'
 GROUP BY 1 ORDER BY 1;
SELECT 'ai_trades all statuses' AS k, status, count(*) AS n, max(placed_at) AS last_placed
  FROM ai_trades GROUP BY status ORDER BY status;

\echo == A5 live_orders open (filled / exiting) by venue, status and age (ACTUAL) ==
SELECT coalesce(venue, '?') AS venue, status,
       CASE WHEN placed_at >= now() - interval '7 days' THEN 'PLACED_WITHIN_7D'
            WHEN placed_at >= now() - interval '30 days' THEN 'PLACED_7_TO_30D'
            ELSE 'PLACED_OVER_30D' END AS age,
       count(*) AS n, round(sum(filled_usd)::numeric, 2) AS filled_usd,
       sum(CASE WHEN coalesce(btrim(us_market_slug), '') <> '' THEN 1 ELSE 0 END) AS with_venue_slug,
       sum(CASE WHEN coalesce(condition_id, '') <> '' THEN 1 ELSE 0 END) AS with_condition_id,
       sum(CASE WHEN EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = lo.asset)
                THEN 1 ELSE 0 END) AS token_in_catalog,
       min(placed_at) AS first_placed, max(placed_at) AS last_placed
  FROM live_orders lo
 WHERE status IN ('filled', 'exiting') AND filled_shares > 0
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == A6 engine_fills unsettled by age (legacy paper engine) ==
SELECT venue, CASE WHEN ts >= now() - interval '7 days' THEN 'WITHIN_7D'
                   WHEN ts >= now() - interval '30 days' THEN '7_TO_30D'
                   ELSE 'OVER_30D' END AS age,
       count(*) AS n, min(ts) AS first_ts, max(ts) AS last_ts
  FROM engine_fills WHERE NOT settled GROUP BY 1, 2 ORDER BY 1, 2;

\echo == A7 rn1x_positions whose condition is absent from markets ==
SELECT x.experiment_id, x.policy, x.condition_id IS NOT NULL AS has_cid,
       EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.condition_id = x.condition_id) AS in_token_catalog,
       x.source_ts, x.decision_ts, x.entry_kind,
       (SELECT count(*) FROM rn1x_outcomes o WHERE o.position_id = x.position_id) AS outcomes,
       (SELECT count(*) FROM rn1x_terminal_settlements s WHERE s.position_id = x.position_id) AS terminal_settlements
  FROM rn1x_positions x
 WHERE NOT EXISTS (SELECT 1 FROM markets m WHERE m.condition_id = x.condition_id);

\echo == A8 did any of OUR books ever hold a token the positions dead-letter holds? ==
WITH k AS (
  SELECT whale_id, asset FROM trades GROUP BY whale_id, asset
  HAVING NOT bool_or(coalesce(condition_id, '') <> '')
), dl AS (
  SELECT DISTINCT k.asset FROM k
   WHERE NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = k.asset)
)
SELECT (SELECT count(*) FROM dl) AS dead_letter_tokens,
       (SELECT count(*) FROM live_orders lo WHERE lo.asset IN (SELECT asset FROM dl)) AS live_orders_rows,
       (SELECT count(*) FROM live_orders lo WHERE lo.asset IN (SELECT asset FROM dl)
           AND lo.status IN ('filled', 'exiting')) AS live_orders_open,
       (SELECT count(*) FROM ai_trades a WHERE a.asset IN (SELECT asset FROM dl)) AS ai_trades_rows,
       (SELECT count(*) FROM ai_trades a WHERE a.asset IN (SELECT asset FROM dl)
           AND a.status = 'open') AS ai_trades_open,
       (SELECT count(*) FROM engine_fills e WHERE e.outcome_id IN (SELECT asset FROM dl)) AS engine_fills_rows,
       (SELECT count(*) FROM mirror_registered_positions r WHERE r.asset IN (SELECT asset FROM dl)) AS registered_rows;

\echo == B4 run 5698: whale 40 poll rows detected inside the run window ==
WITH r AS (SELECT id, started_at, finished_at FROM reconciliation_runs WHERE id = 5698)
SELECT t.id AS trade_id, t.side, t.size, t.price, t.notional, t.ts, t.detected_at,
       round(extract(epoch FROM t.detected_at - t.ts)::numeric, 1) AS detect_lag_s,
       (coalesce(t.condition_id, '') <> '') AS has_cid, t.outcome, t.outcome_index,
       t.market_slug, t.sport, t.venue_seen_at IS NOT NULL AS venue_seen, t.taker,
       EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = t.asset) AS token_known,
       (SELECT count(*) FROM notification_outbox n WHERE n.trade_id = t.id) AS outbox,
       (SELECT count(*) FROM copy_probes cp WHERE cp.trade_id = t.id) AS copy_probes,
       (SELECT count(*) FROM ai_trades a WHERE a.trade_id = t.id) AS ai_trades,
       (SELECT count(*) FROM live_orders lo WHERE lo.trade_id = t.id) AS live_orders,
       (SELECT count(*) FROM rn1x_positions x WHERE x.source_trade_id = t.id) AS rn1x_positions,
       (SELECT count(*) FROM rn1_observations o WHERE o.source_event_id = t.dedupe_key) AS rn1_obs,
       (SELECT count(*) FROM paper_fills pf WHERE pf.us_market_slug = t.market_slug) AS paper_fills_same_slug
  FROM r JOIN trades t ON t.whale_id = 40 AND t.source = 'poll'
   AND t.detected_at BETWEEN r.started_at AND r.finished_at
 ORDER BY t.detected_at;

\echo == B5 every run with misses since 2026-10-08 00:00Z: whale rows in window, split by detection lag ==
WITH runs AS (
  SELECT id, started_at, finished_at, missed FROM reconciliation_runs
   WHERE started_at >= timestamptz '2026-10-08 00:00:00+00' AND missed > 0
), wl AS (
  SELECT r.id AS run_id, w.id AS whale_id, (e.value)::text::int AS missed
    FROM runs r JOIN reconciliation_runs rr ON rr.id = r.id
    CROSS JOIN LATERAL jsonb_each(rr.details -> 'per_wallet') e
    JOIN whales w ON w.address = e.key
   WHERE jsonb_typeof(e.value) = 'number' AND (e.value)::text::int > 0
)
SELECT wl.run_id, wl.whale_id, wl.missed AS logged_missed,
       count(t.id) AS poll_rows_in_window,
       sum(CASE WHEN t.detected_at - t.ts > interval '10 minutes' THEN 1 ELSE 0 END) AS lag_over_10m,
       sum(CASE WHEN t.detected_at - t.ts <= interval '10 minutes' THEN 1 ELSE 0 END) AS lag_10m_or_less,
       min(t.ts) AS oldest_fill, max(t.ts) AS newest_fill,
       sum(CASE WHEN EXISTS (SELECT 1 FROM ai_trades a WHERE a.trade_id = t.id)
                  OR EXISTS (SELECT 1 FROM live_orders lo WHERE lo.trade_id = t.id)
                  OR EXISTS (SELECT 1 FROM rn1x_positions x WHERE x.source_trade_id = t.id)
                THEN 1 ELSE 0 END) AS rows_with_any_book_link
  FROM wl JOIN runs ON runs.id = wl.run_id
  LEFT JOIN trades t ON t.whale_id = wl.whale_id AND t.source = 'poll'
   AND t.detected_at BETWEEN runs.started_at AND runs.finished_at
 GROUP BY 1, 2, 3 ORDER BY 1, 2;

\echo == B6 walk coverage holes between consecutive runs, per wallet, since 2026-10-08 00:00Z ==
WITH cov AS (
  SELECT r.id AS run_id, w.id AS whale_id,
         (r.details -> 'per_wallet' -> ('cov:' || w.address) ->> 'oldest')::float8 AS oldest,
         (r.details -> 'per_wallet' -> ('cov:' || w.address) ->> 'newest')::float8 AS newest,
         (r.details -> 'per_wallet' -> ('cov:' || w.address) ->> 'complete')::boolean AS complete,
         (r.details -> 'per_wallet' -> ('cov:' || w.address) ->> 'dirty')::int AS dirty
    FROM reconciliation_runs r CROSS JOIN whales w
   WHERE r.started_at >= timestamptz '2026-10-08 00:00:00+00'
     AND r.details -> 'per_wallet' ? ('cov:' || w.address)
), seq AS (
  SELECT cov.*, lag(newest) OVER (PARTITION BY whale_id ORDER BY run_id) AS prev_newest
    FROM cov
)
SELECT whale_id, count(*) AS runs,
       sum(CASE WHEN complete THEN 1 ELSE 0 END) AS complete_runs,
       sum(CASE WHEN dirty > 0 THEN 1 ELSE 0 END) AS dirty_runs,
       sum(CASE WHEN NOT complete AND prev_newest IS NOT NULL AND oldest > prev_newest
                THEN 1 ELSE 0 END) AS runs_with_hole,
       round(sum(CASE WHEN NOT complete AND prev_newest IS NOT NULL AND oldest > prev_newest
                      THEN oldest - prev_newest ELSE 0 END)::numeric, 0) AS hole_seconds,
       round(max(CASE WHEN NOT complete AND prev_newest IS NOT NULL AND oldest > prev_newest
                      THEN oldest - prev_newest END)::numeric, 0) AS max_hole_s
  FROM seq GROUP BY whale_id ORDER BY hole_seconds DESC NULLS LAST, whale_id;
