-- Market-data websocket subscription state and REST read pressure (read-only).
SELECT key, jsonb_pretty(value->'market_subscription') AS sub
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby');

SELECT key, jsonb_pretty(value->'venue_rate_controls') AS rc
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

SELECT key, left(value::text, 600) FROM ingestion_state
 WHERE key ILIKE '%subscription%' OR key ILIKE '%stream%' OR key ILIKE '%incentive%' ORDER BY key;

SELECT read_basis, count(*) AS n,
       count(*) FILTER (WHERE error IS NULL) AS ok
  FROM paper_book_observations
 WHERE observed_at > now() - interval '60 minutes'
 GROUP BY 1 ORDER BY 2 DESC;

SELECT source, count(*) FROM paper_book_observations
 WHERE observed_at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;
