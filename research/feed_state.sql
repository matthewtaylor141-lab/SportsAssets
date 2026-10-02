-- PinnAPI feed + Pinnacle valuation freshness for HELD paper contracts (read-only).
\echo '== F0 · feed control, scope, heartbeat (truncated) =='
SELECT key, left(value::text, 1500) AS value FROM ingestion_state
 WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope', 'pinnapi_feed_last', 'ext_pinnacle_last_cycle');
\echo '== F1 · external_valuations written per hour (last 6 h) =='
SELECT date_trunc('hour', observed_at) AS hour, count(*) AS rows,
       count(DISTINCT us_market_slug) AS markets
  FROM external_valuations WHERE observed_at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 1;
\echo '== F2 · held paper markets: last valuation age =='
WITH held AS (
  SELECT DISTINCT o.us_market_slug FROM paper_orders o
   JOIN paper_fills f ON f.order_id = o.order_id
  WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
    AND f.filled_at > now() - interval '3 days')
SELECT h.us_market_slug,
       (SELECT round(extract(epoch FROM now() - max(v.observed_at))) FROM external_valuations v
         WHERE v.us_market_slug = h.us_market_slug) AS last_valuation_age_s,
       (SELECT count(*) FROM external_valuations v WHERE v.us_market_slug = h.us_market_slug
           AND v.observed_at > now() - interval '2 hours') AS rows_2h
  FROM held h ORDER BY 2 NULLS FIRST LIMIT 30;
