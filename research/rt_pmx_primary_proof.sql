-- PMX gRPC PRIMARY: production proof (READ ONLY; for research-sql).
-- 1. The deciding process (sportsassets-api): the live stream digest the
--    held-mark refresh saves on every run (migration 306 market_data).
SELECT run_id, started_at, finished_at, held_markets, due,
       institutional_books, stream_books, read_ok, sources,
       market_data->'streams'->'institutional'->>'state'          AS api_state,
       market_data->'streams'->'institutional'->>'connected'      AS api_connected,
       market_data->'streams'->'institutional'->>'symbols'        AS requested,
       market_data->'streams'->'institutional'->>'acked'          AS acked,
       market_data->'streams'->'institutional'->>'current_books'  AS current_l2,
       market_data->'streams'->'institutional'->>'held_mark_current_books' AS held_mark_current_l2,
       market_data->'streams'->'institutional'->'book_age_s'      AS book_age_s,
       market_data->'streams'->'institutional'->'by_refusal'      AS by_refusal,
       market_data->'streams'->'institutional'->'api_stream'      AS api_stream,
       market_data->'institutional_refusals'                      AS fallback_reasons
  FROM paper_mark_refresh_runs
 WHERE account_id = 'paper_acct_main'
 ORDER BY started_at DESC LIMIT 20;

-- 2. The workers' stream (evidence lane): heartbeat digest + cadence.
SELECT service, status, beat_at, now() - beat_at AS age,
       detail->'stream'->>'state'      AS state,
       detail->'stream'->>'connected'  AS connected,
       detail->'stream'->>'symbols'    AS requested,
       detail->'stream'->'by_refusal'  AS by_refusal,
       detail->>'symbols'              AS focus_set,
       detail->>'universeSubscribed'   AS universe_exact_subscribed,
       detail->'focusUniverse'         AS focus_universe,
       detail->>'focusUniverseError'   AS focus_universe_error,
       detail->>'marketDataMechanism'  AS mechanism
  FROM service_heartbeats WHERE service IN ('institutional_md');

-- 3. Who wants what, and why a member is NOT subscribed (both processes).
SELECT service, identity_status, unavailable_reason, stream_wanted,
       count(*) AS members, max(recorded_at) AS newest
  FROM institutional_focus_universe
 WHERE recorded_at > now() - interval '30 minutes'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;

-- 4. Held marks by source in the last hour (the accounting the gate needs).
SELECT coalesce(read_basis, source) AS basis, count(*) AS observations,
       count(DISTINCT us_market_slug) AS markets, max(observed_at) AS newest
  FROM paper_book_observations
 WHERE observed_at > now() - interval '1 hour' AND error IS NULL
 GROUP BY 1 ORDER BY 2 DESC;

-- 5. The workers' durable stream evidence (process rows, symbol '*').
SELECT process_id, max(minute) AS last_minute, max(stream_state) AS state,
       max(connection_epoch) AS epoch, sum(updates_in_minute) AS updates,
       sum(messages_in_minute) AS messages
  FROM institutional_stream_evidence
 WHERE minute > now() - interval '30 minutes' AND symbol = '*'
 GROUP BY 1 ORDER BY 2 DESC;
