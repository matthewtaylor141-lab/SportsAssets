-- WEEK 38 SLEEVES — settled copy orders, Mon 2026-09-14 .. Sun 2026-09-20 ET.
-- ET is UTC-4 in September, so the week is 2026-09-14T04:00Z .. 2026-09-21T04:00Z.
-- Read only. Aggregates only.
SELECT 'A_BY_WHALE' AS section, whale_username AS k,
       'settled=' || count(*)::text
       || ' W-L=' || count(*) FILTER (WHERE pnl > 0)::text
       || '-' || count(*) FILTER (WHERE pnl < 0)::text
       || ' staked=' || round(coalesce(sum(filled_usd), 0)::numeric, 2)::text
       || ' pnl=' || round(coalesce(sum(pnl), 0)::numeric, 2)::text AS v
  FROM live_orders
 WHERE settled_at >= '2026-09-14T04:00:00Z'::timestamptz
   AND settled_at <  '2026-09-21T04:00:00Z'::timestamptz
   AND pnl IS NOT NULL
 GROUP BY whale_username

UNION ALL

SELECT 'B_BY_ET_DAY',
       to_char(settled_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD Dy'),
       'settled=' || count(*)::text
       || ' pnl=' || round(coalesce(sum(pnl), 0)::numeric, 2)::text
       || ' staked=' || round(coalesce(sum(filled_usd), 0)::numeric, 2)::text
  FROM live_orders
 WHERE settled_at >= '2026-09-14T04:00:00Z'::timestamptz
   AND settled_at <  '2026-09-21T04:00:00Z'::timestamptz
   AND pnl IS NOT NULL
 GROUP BY 2

UNION ALL

SELECT 'C_WEEK_TOTAL', 'ALL_SLEEVES',
       'settled=' || count(*)::text
       || ' W-L=' || count(*) FILTER (WHERE pnl > 0)::text
       || '-' || count(*) FILTER (WHERE pnl < 0)::text
       || ' staked=' || round(coalesce(sum(filled_usd), 0)::numeric, 2)::text
       || ' pnl=' || round(coalesce(sum(pnl), 0)::numeric, 2)::text
  FROM live_orders
 WHERE settled_at >= '2026-09-14T04:00:00Z'::timestamptz
   AND settled_at <  '2026-09-21T04:00:00Z'::timestamptz
   AND pnl IS NOT NULL

ORDER BY 1, 2
