-- READ-ONLY. WHY NO IN-SCOPE CALIBRATION FIXTURE WAS RECORDED ON 28-29 SEP:
-- valuations per day by version / devig / family / market / purpose, and the
-- cycle heartbeat's newest state. Nothing is modified.

\echo '== G1 · valuations per day, last 10 days, by scope key =='
SELECT date_trunc('day', decided_at)::date AS day,
       coalesce(version, '?') AS version, coalesce(devig_method, '?') AS devig,
       coalesce(sport_family, '?') AS family, coalesce(market, '?') AS market,
       record_purpose, count(*) AS rows, count(DISTINCT event_key) AS fixtures
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '10 days'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 7 DESC;

\echo '== G2 · all valuations per day (any experiment), last 10 days =='
SELECT date_trunc('day', decided_at)::date AS day, experiment_id,
       count(*) AS rows
  FROM external_valuations
 WHERE decided_at >= now() - interval '10 days'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;
