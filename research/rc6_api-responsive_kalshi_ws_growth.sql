-- READ-ONLY. RC6 lane api-responsive: does the Kalshi WebSocket runtime
-- (workers/kalshi_ws_market_data, in the dedicated market-plane service)
-- hold books for markets it no longer tracks?
--
-- Its wanted set is the ESTABLISHED fixtures starting within [now - 4 h,
-- now + 36 h] (at most 2,000 tickers, re-read every 60 s). Its book map, its
-- written-key map and its subscription only ever add. This compares the
-- heartbeat's book count with the wanted set now, and the WS-basis rows in
-- kalshi_books_current with that set. Counts only; no ticker is printed.

\echo K1 the runtime heartbeat now (book map size, states, subscription)
SELECT status, beat_at, now() - beat_at AS age,
       detail->'ws'->>'markets' AS books_held,
       detail->'ws'->>'current' AS books_current,
       detail->'ws'->'by_state' AS by_state,
       detail->>'subscribed_markets' AS subscribed_markets,
       detail->>'connections' AS connections,
       detail->>'resubscribes' AS resubscribes,
       detail->'freshness'->>'numerator' AS freshness_numerator,
       detail->'freshness'->>'denominator' AS freshness_denominator
  FROM service_heartbeats WHERE service = 'kalshi_ws_market_data';

\echo K2 the wanted set now (the runtime query, counted)
WITH w AS (
  SELECT DISTINCT t FROM (
    SELECT unnest(team_tickers) AS t FROM kalshi_fixtures_current
     WHERE mapping_status = 'ESTABLISHED'
       AND start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'
    UNION ALL
    SELECT tie_ticker FROM kalshi_fixtures_current
     WHERE mapping_status = 'ESTABLISHED' AND tie_ticker IS NOT NULL
       AND start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours') x)
SELECT count(*) AS wanted_now, least(count(*), 2000) AS wanted_capped
  FROM w;

\echo K3 WS-basis book rows: wanted now or not, readable, last observed
WITH w AS (
  SELECT DISTINCT t FROM (
    SELECT unnest(team_tickers) AS t FROM kalshi_fixtures_current
     WHERE mapping_status = 'ESTABLISHED'
       AND start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'
    UNION ALL
    SELECT tie_ticker FROM kalshi_fixtures_current
     WHERE mapping_status = 'ESTABLISHED' AND tie_ticker IS NOT NULL
       AND start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours') x)
SELECT (b.ticker IN (SELECT t FROM w)) AS wanted_now, b.readable,
       count(*) AS book_rows,
       min(b.updated_at) AS oldest_update, max(b.updated_at) AS newest_update,
       count(*) FILTER (WHERE b.observed_at > now() - interval '1 minute')
         AS observed_last_minute
  FROM kalshi_books_current b
 WHERE b.book_basis LIKE 'KALSHI_WS%'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo K4 the plane heartbeat memory now (RSS by step)
SELECT beat_at, detail->'memory'->>'rss_mb' AS rss_mb,
       detail->'memory'->>'peak_mb' AS peak_mb,
       detail->'memory'->'by_step' AS by_step,
       detail->'memory'->'cycles' AS cycles
  FROM service_heartbeats WHERE service = 'universal_market_plane';
