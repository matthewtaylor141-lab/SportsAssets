-- READ-ONLY. The wk39 weekly management report's anchors.
-- ET week that just ended: Mon 2026-09-21 -> Sun 2026-09-27.
-- `day` is TEXT in venue_truth_days, so it is compared as text.

-- 1 · Crawl freshness. wk38 had to report Sunday absent; this states the
--     fact rather than assuming either way.
SELECT max(day) AS max_day,
       max(updated_at) AS max_updated_at,
       now() - max(updated_at) AS staleness,
       count(*) AS total_rows
  FROM venue_truth_days;

-- 2 · Is the table one row per day, or one row per (day, venue)? A sum that
--     silently spans venues would be a different number from wk38's.
SELECT day, venue, settled, wins, losses, cost, realized, updated_at
  FROM venue_truth_days
 WHERE day >= '2026-09-21' AND day <= '2026-09-27'
 ORDER BY day, venue;

-- 3 · The week fold, from the database's own aggregate.
SELECT count(*)                      AS day_rows,
       count(DISTINCT day)           AS distinct_days,
       sum(settled)                  AS settled,
       sum(wins)                     AS wins,
       sum(losses)                   AS losses,
       round(sum(cost)::numeric, 2)  AS cost,
       round(sum(realized)::numeric, 2) AS realized,
       round((100.0 * sum(realized) / nullif(sum(cost), 0))::numeric, 4) AS roi_pct
  FROM venue_truth_days
 WHERE day >= '2026-09-21' AND day <= '2026-09-27';

-- 4 · The same fold recomputed from the printed per-day lines, so the report
--     can state that an independent recompute agrees to the cent.
WITH d AS (
  SELECT day, sum(settled) s, sum(wins) w, sum(losses) l,
         sum(cost) c, sum(realized) r
    FROM venue_truth_days
   WHERE day >= '2026-09-21' AND day <= '2026-09-27'
   GROUP BY day
)
SELECT count(*) AS days_printed,
       sum(s) AS settled_recomputed,
       sum(w) AS wins_recomputed,
       sum(l) AS losses_recomputed,
       round(sum(c)::numeric, 2) AS cost_recomputed,
       round(sum(r)::numeric, 2) AS realized_recomputed
  FROM d;

-- 5 · The sleeves, full ET week including Sunday, by whale.
SELECT coalesce(whale_username, '(none)') AS sleeve,
       coalesce(lane, '(none)')           AS lane,
       count(*)                           AS settled_orders,
       count(*) FILTER (WHERE pnl > 0)    AS wins,
       count(*) FILTER (WHERE pnl < 0)    AS losses,
       round(coalesce(sum(filled_usd), 0)::numeric, 2) AS staked,
       round(coalesce(sum(pnl), 0)::numeric, 2)        AS pnl
  FROM live_orders
 WHERE settled_at >= TIMESTAMPTZ '2026-09-21 04:00:00+00'
   AND settled_at <  TIMESTAMPTZ '2026-09-28 04:00:00+00'
 GROUP BY 1, 2 ORDER BY 7 DESC NULLS LAST;

-- 6 · And the sleeve total, so the report does not have to add rows by hand.
SELECT count(*) AS settled_orders,
       round(coalesce(sum(filled_usd), 0)::numeric, 2) AS staked,
       round(coalesce(sum(pnl), 0)::numeric, 2) AS pnl
  FROM live_orders
 WHERE settled_at >= TIMESTAMPTZ '2026-09-21 04:00:00+00'
   AND settled_at <  TIMESTAMPTZ '2026-09-28 04:00:00+00';

-- 7 · The control rows, read from the database rather than inferred.
SELECT key, left(value::text, 60) AS value
  FROM ingestion_state
 WHERE key IN ('mirror_live', 'live_trading_paused', 'mirror_loss_stop',
               'copy_overspend_halt', 'ext_pinnacle_shadow')
 ORDER BY key;

-- 8 · Week over week: the prior ET week (wk38, Sep 14-20) on the same basis.
SELECT '2026-09-14..20' AS week,
       count(DISTINCT day) AS days,
       sum(settled) AS settled,
       round(sum(cost)::numeric, 2) AS cost,
       round(sum(realized)::numeric, 2) AS realized
  FROM venue_truth_days
 WHERE day >= '2026-09-14' AND day <= '2026-09-20'
UNION ALL
SELECT '2026-09-21..27', count(DISTINCT day), sum(settled),
       round(sum(cost)::numeric, 2), round(sum(realized)::numeric, 2)
  FROM venue_truth_days
 WHERE day >= '2026-09-21' AND day <= '2026-09-27';
