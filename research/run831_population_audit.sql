-- RUN 83.1 -- POPULATION / INSTRUMENT BOUNDARY AUDIT. Read-only.
--
-- THE QUESTION: why did an experiment intended to observe RN1 receive 12,535
-- events in 12.4 minutes?
--
-- THE INSTRUMENT CANNOT ANSWER THIS FROM ITS OWN ROWS. rn1_obs_events has no
-- whale column, no wallet address, no account identity of any kind -- migration
-- 062 stores source_event_id, tx hash, market, token, price, size, side, and
-- nothing that says WHOSE fill it was. That is the finding, not a limitation of
-- this query: the hook never had RN1 identity in hand, so it could not have
-- filtered on it and cannot now be asked who these events belong to.
--
-- Identity therefore has to come from the production write site. _obs_receipt
-- passes ev.dedupe_key as source_event_id, and the very next statement inserts
-- that same key into trades.dedupe_key alongside whale_id. The join below is
-- that equality -- the instrument's own id against the ledger's -- and it is the
-- only admissible link, because it is the one production actually wrote.
--
-- A ROW THAT DOES NOT JOIN IS NOT AN ORPHAN TO BE EXPLAINED AWAY. The hook is
-- the FIRST statement of ingest_trade_result and the trades INSERT comes after
-- it, so an event whose insert failed, or which was still in flight, is observed
-- with no ledger row behind it. Those are IDENTITY_NOT_AVAILABLE_AT_HOOK and
-- they are counted, never dropped.

\echo == 1. THE ROSTER EVERY LANE WALKS: whales WHERE active AND NOT banned ==
SELECT count(*) FILTER (WHERE active AND NOT banned) AS active_roster,
       count(*) FILTER (WHERE active)                AS active_any,
       count(*) FILTER (WHERE banned)                AS banned,
       count(*)                                      AS whales_total
FROM whales;

\echo
\echo == 2. WHO IS ON IT (the ingestion population, named) ==
SELECT id, username, left(address, 12) AS address_head, active, banned
FROM whales
WHERE active AND NOT banned
ORDER BY id;

\echo
\echo == 3. THE COHORT BY WALLET -- the deciding table ==
-- No class is assumed here. Every observed event is joined to the ledger row
-- production wrote from the same key, and the wallet is whatever that row says.
SELECT COALESCE(w.username, '(no trades row -- identity unavailable)') AS wallet,
       count(*)                                   AS events,
       round(100.0 * count(*) / SUM(count(*)) OVER (), 3) AS pct,
       count(DISTINCT e.source_market_id)         AS markets,
       count(DISTINCT e.source_token_id)          AS tokens,
       round(SUM(e.source_price * e.source_size)::numeric, 2) AS notional
FROM rn1_obs_events e
LEFT JOIN trades t ON t.dedupe_key = e.source_event_id
LEFT JOIN whales w ON w.id = t.whale_id
GROUP BY 1
ORDER BY events DESC;

\echo
\echo == 4. THE CLASSIFICATION, MUTUALLY EXCLUSIVE -- must close to 12,535 ==
-- GENERAL_MARKET_FLOW is a real class with a real test, not a formality: it is
-- an observed event carrying a tx hash that the ledger has never attributed to
-- any roster wallet. Every lane looks its wallet up in the roster before
-- constructing a TradeEvent, so the expected count is zero -- but "the code says
-- it cannot happen" is not evidence, and a zero here is only worth having
-- because the query could have returned something else.
WITH classed AS (
    SELECT e.obs_event_id,
           e.source_lane,
           e.source_market_id,
           e.source_token_id,
           e.source_price * e.source_size AS notional,
           CASE
             WHEN t.dedupe_key IS NULL           THEN 'IDENTITY_NOT_AVAILABLE_AT_HOOK'
             WHEN w.id IS NULL                   THEN 'GENERAL_MARKET_FLOW'
             WHEN w.username = 'RN1'             THEN 'RN1_CONFIRMED'
             ELSE                                     'OTHER_TRACKED_WALLET'
           END AS class
    FROM rn1_obs_events e
    LEFT JOIN trades t ON t.dedupe_key = e.source_event_id
    LEFT JOIN whales w ON w.id = t.whale_id
)
SELECT class,
       count(*)                                    AS events,
       round(100.0 * count(*) / SUM(count(*)) OVER (), 3) AS pct,
       count(DISTINCT source_market_id)            AS markets,
       count(DISTINCT source_token_id)             AS tokens,
       round(SUM(notional)::numeric, 2)            AS notional,
       string_agg(DISTINCT source_lane, '/' ORDER BY source_lane) AS lanes
FROM classed
GROUP BY class
ORDER BY events DESC;

\echo
\echo == 5. THE CLOSURE CHECK: classes must sum to the cohort exactly ==
WITH classed AS (
    SELECT CASE
             WHEN t.dedupe_key IS NULL THEN 'IDENTITY_NOT_AVAILABLE_AT_HOOK'
             WHEN w.id IS NULL         THEN 'GENERAL_MARKET_FLOW'
             WHEN w.username = 'RN1'   THEN 'RN1_CONFIRMED'
             ELSE                           'OTHER_TRACKED_WALLET'
           END AS class
    FROM rn1_obs_events e
    LEFT JOIN trades t ON t.dedupe_key = e.source_event_id
    LEFT JOIN whales w ON w.id = t.whale_id
)
SELECT (SELECT count(*) FROM rn1_obs_events)  AS cohort_rows,
       (SELECT count(*) FROM classed)         AS classified_rows,
       (SELECT count(*) FROM rn1_obs_events)
         - (SELECT count(*) FROM classed)     AS must_be_zero;

\echo
\echo == 6. LANE x WALLET: which ingestion path carried whose flow ==
SELECT e.source_lane,
       COALESCE(w.username, '(unidentified)') AS wallet,
       count(*) AS events
FROM rn1_obs_events e
LEFT JOIN trades t ON t.dedupe_key = e.source_event_id
LEFT JOIN whales w ON w.id = t.whale_id
GROUP BY 1, 2
ORDER BY events DESC
LIMIT 30;

\echo
\echo == 7. JOIN INTEGRITY: is dedupe_key a sound key to classify on? ==
SELECT
    (SELECT count(*) FROM rn1_obs_events)                       AS obs_events,
    (SELECT count(DISTINCT source_event_id) FROM rn1_obs_events) AS distinct_obs_keys,
    (SELECT count(*) FROM rn1_obs_events e
       WHERE EXISTS (SELECT 1 FROM trades t
                      WHERE t.dedupe_key = e.source_event_id))  AS joined,
    (SELECT count(*) FROM rn1_obs_events e
       WHERE NOT EXISTS (SELECT 1 FROM trades t
                          WHERE t.dedupe_key = e.source_event_id)) AS unjoined;

\echo
\echo == 8. THE TRUE ARRIVAL RATE, PER WALLET, IN THE IDENTICAL WINDOW ==
-- Counted from trades, independently of the instrument, over exactly the
-- collector's live interval. This is the number the capacity model needed.
SELECT w.username,
       count(*)                                    AS fills,
       round((count(*) / 744.5)::numeric, 4)       AS per_second,
       round((count(*) / 744.5 * 86400)::numeric)  AS implied_per_day
FROM trades t JOIN whales w ON w.id = t.whale_id
WHERE t.detected_at >= TIMESTAMPTZ '2026-09-12 15:56:37.846+00'
  AND t.detected_at <= TIMESTAMPTZ '2026-09-12 16:09:02.318+00'
GROUP BY w.username
ORDER BY fills DESC;

\echo
\echo == 9. RN1-ONLY DAILY RATE, LAST 10 COMPLETE DAYS (the prospective denominator) ==
SELECT date_trunc('day', t.ts)::date AS day,
       count(*) AS rn1_fills,
       count(*) FILTER (WHERE t.source = 'chain')    AS chain,
       count(*) FILTER (WHERE t.source = 'poll')     AS poll,
       count(*) FILTER (WHERE t.source = 's1')       AS s1,
       count(*) FILTER (WHERE t.source = 'backfill') AS backfill,
       count(DISTINCT t.dedupe_key)                  AS distinct_keys
FROM trades t JOIN whales w ON w.id = t.whale_id
WHERE w.username = 'RN1'
  AND t.ts >= now() - INTERVAL '11 days'
  AND t.ts <  date_trunc('day', now())
GROUP BY 1
ORDER BY 1 DESC;

\echo
\echo == 10. ALL-WALLET DAILY RATE, same days -- the ingestion population ==
SELECT date_trunc('day', t.ts)::date AS day,
       count(*) AS all_fills,
       count(DISTINCT t.whale_id) AS wallets
FROM trades t
WHERE t.ts >= now() - INTERVAL '11 days'
  AND t.ts <  date_trunc('day', now())
GROUP BY 1
ORDER BY 1 DESC;

\echo
\echo == 11. BURST SHAPE: RN1 fills per second, the distribution that sizes a pool ==
WITH per_s AS (
    SELECT date_trunc('second', t.detected_at) AS sec, count(*) AS n
    FROM trades t JOIN whales w ON w.id = t.whale_id
    WHERE w.username = 'RN1'
      AND t.detected_at >= now() - INTERVAL '24 hours'
    GROUP BY 1
)
SELECT count(*)                                        AS active_seconds,
       round(avg(n)::numeric, 3)                       AS mean_per_active_s,
       max(n)                                          AS max_in_one_second,
       percentile_disc(0.50) WITHIN GROUP (ORDER BY n) AS p50,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY n) AS p95,
       percentile_disc(0.99) WITHIN GROUP (ORDER BY n) AS p99
FROM per_s;

\echo
\echo == 12. MEASURED REQUEST DURATION: what sizes the network pool ==
-- From the 381 reads that actually reached the venue. Concurrency required is
-- arrival rate x duration (Little's law), and this is the duration term --
-- measured, not assumed.
SELECT count(*)                                                       AS reads,
       round(avg(EXTRACT(epoch FROM (response_wall - request_start_wall)))::numeric, 4) AS mean_s,
       round(min(EXTRACT(epoch FROM (response_wall - request_start_wall)))::numeric, 4) AS min_s,
       round(percentile_disc(0.50) WITHIN GROUP (
             ORDER BY EXTRACT(epoch FROM (response_wall - request_start_wall)))::numeric, 4) AS p50_s,
       round(percentile_disc(0.95) WITHIN GROUP (
             ORDER BY EXTRACT(epoch FROM (response_wall - request_start_wall)))::numeric, 4) AS p95_s,
       round(max(EXTRACT(epoch FROM (response_wall - request_start_wall)))::numeric, 4) AS max_s
FROM rn1_obs_snapshots
WHERE response_wall IS NOT NULL AND request_start_wall IS NOT NULL;

\echo
\echo == 13. COHORT SEAL -- activation interval, boot, configuration, counts ==
SELECT 'RUN83_ACTIVATION_FAILED_V1'                          AS cohort_id,
       (SELECT min(receipt_wall) FROM rn1_obs_events)::text  AS first_receipt_wall,
       (SELECT max(receipt_wall) FROM rn1_obs_events)::text  AS last_receipt_wall,
       (SELECT max(row_written_at) FROM rn1_obs_snapshots)::text AS last_row_written,
       (SELECT count(DISTINCT process_boot_id) FROM rn1_obs_events) AS boot_ids,
       (SELECT min(process_boot_id)::text FROM rn1_obs_events)      AS process_boot_id;

\echo
\echo == 14. COHORT SEAL -- row counts per table ==
SELECT 'rn1_obs_events' AS tbl, count(*) AS rows FROM rn1_obs_events
UNION ALL SELECT 'rn1_obs_snapshots',   count(*) FROM rn1_obs_snapshots
UNION ALL SELECT 'rn1_obs_transitions', count(*) FROM rn1_obs_transitions
UNION ALL SELECT 'rn1_obs_clock_sync',  count(*) FROM rn1_obs_clock_sync
ORDER BY tbl;

\echo
\echo == 15. COHORT SEAL -- canonical identity digests (sha256 over sorted ids) ==
-- Placed LAST on purpose: if sha256() is unavailable on this server the file
-- aborts here under ON_ERROR_STOP and every statement above has already
-- printed, rather than the whole audit being lost to one function name.
SELECT
  encode(sha256(convert_to(
    (SELECT string_agg(source_event_id, E'\n' ORDER BY source_event_id)
       FROM rn1_obs_events), 'UTF8')), 'hex')            AS event_identity_digest,
  encode(sha256(convert_to(
    (SELECT string_agg(obs_event_id::text || '|' || offset_label || '|' || status,
                       E'\n' ORDER BY obs_event_id::text, offset_label)
       FROM rn1_obs_snapshots), 'UTF8')), 'hex')         AS snapshot_identity_digest;

\echo
\echo == 16. COHORT SEAL -- transitions and clock sync digests ==
SELECT
  encode(sha256(convert_to(
    (SELECT string_agg(obs_event_id::text || '|' || stage || '|' || state,
                       E'\n' ORDER BY obs_event_id::text, stage, state)
       FROM rn1_obs_transitions), 'UTF8')), 'hex')       AS transition_identity_digest,
  encode(sha256(convert_to(
    (SELECT string_agg(sync_id::text || '|' || COALESCE(host_sync_status, 'NULL'),
                       E'\n' ORDER BY sync_id)
       FROM rn1_obs_clock_sync), 'UTF8')), 'hex')        AS clock_sync_identity_digest;
