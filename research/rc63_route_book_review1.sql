-- RC6.3 route-book lane, review 1 readback: the production shape of the
-- on-demand PMUS route-book read order. The reviewer found that a read
-- refused (market not open, failed, crossed, deferred) was never remembered,
-- so earlier-starting markets (games already started or ended inside the
-- 4 h claim lookback) sorted first on every recording pass and could spend
-- the whole 6-read budget. This reads, for the Kalshi-mapped PMUS slugs in
-- the claim window now: their start, the newest venue word on each market
-- (the paper REST read state, any age in 24 h), the keyless probe read
-- outcomes, the market plane PMX top ages, and the route receipts the
-- deployed release writes. SELECT only.
\echo == R0 clock
SELECT now() AS db_now;

\echo == R1 mapped PMUS slugs in the claim window, in start order (the earlier read order tie break)
WITH m AS (
  SELECT f.pmus_slug AS slug, min(f.start_at) AS start_at
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'
   GROUP BY 1),
p AS (SELECT payload -> 'books' AS books FROM market_plane_events
       WHERE kind = 'PRIORITY_PMX_BOOKS' ORDER BY at DESC LIMIT 1)
SELECT row_number() OVER (ORDER BY m.start_at, m.slug) AS start_rank,
       m.slug,
       round((extract(epoch FROM m.start_at - now()) / 3600.0)::numeric, 2)
         AS starts_in_h,
       CASE WHEN m.start_at < now() - interval '2 hours' THEN 'STARTED_GT_2H'
            WHEN m.start_at < now() THEN 'STARTED_LE_2H'
            ELSE 'UPCOMING' END AS phase,
       pr.market_state AS paper_state,
       round(extract(epoch FROM now() - pr.observed_at)::numeric, 0)
         AS paper_age_s,
       (SELECT count(*) FROM paper_book_observations o
         WHERE o.us_market_slug = m.slug
           AND o.observed_at > now() - interval '24 hours') AS paper_reads_24h,
       (SELECT count(*) FROM paper_book_observations o
         WHERE o.us_market_slug = m.slug AND o.error IS NOT NULL
           AND o.observed_at > now() - interval '24 hours') AS paper_errors_24h,
       sb.retail_ok AS probe_ok,
       left(sb.retail_error, 40) AS probe_error,
       round(extract(epoch FROM now() - sb.probed_at)::numeric, 0)
         AS probe_age_s,
       r.active AS registry_active,
       round(extract(epoch FROM now() - r.last_seen_at)::numeric, 0)
         AS registry_seen_age_s,
       round((extract(epoch FROM now())
              - ((p.books -> m.slug) ->> 'received_at')::float8)::numeric, 1)
         AS pmx_top_age_s
  FROM m
  LEFT JOIN LATERAL (
    SELECT o.market_state, o.observed_at FROM paper_book_observations o
     WHERE o.us_market_slug = m.slug AND o.error IS NULL
       AND o.observed_at > now() - interval '24 hours'
     ORDER BY o.observed_at DESC LIMIT 1) pr ON true
  LEFT JOIN LATERAL (
    SELECT s.retail_ok, s.retail_error, s.probed_at
      FROM institutional_same_book_probe s
     WHERE s.retail_slug = m.slug
       AND s.probed_at > now() - interval '24 hours'
     ORDER BY s.probed_at DESC LIMIT 1) sb ON true
  LEFT JOIN market_plane_registry r ON r.contract_id = m.slug
  LEFT JOIN p ON true
 ORDER BY m.start_at, m.slug;

\echo == R2 summary: phases, newest venue word not open, and the first six in start order
WITH m AS (
  SELECT f.pmus_slug AS slug, min(f.start_at) AS start_at
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'
   GROUP BY 1),
a AS (
  SELECT m.*, row_number() OVER (ORDER BY m.start_at, m.slug) AS rk,
         (SELECT o.market_state FROM paper_book_observations o
           WHERE o.us_market_slug = m.slug AND o.error IS NULL
             AND o.observed_at > now() - interval '24 hours'
           ORDER BY o.observed_at DESC LIMIT 1) AS st
    FROM m)
SELECT count(*) AS mapped_slugs,
       count(*) FILTER (WHERE start_at < now() - interval '2 hours')
         AS started_gt_2h,
       count(*) FILTER (WHERE start_at < now()
                          AND start_at >= now() - interval '2 hours')
         AS started_le_2h,
       count(*) FILTER (WHERE start_at >= now()) AS upcoming,
       count(*) FILTER (WHERE st IS NOT NULL) AS with_paper_state_24h,
       count(*) FILTER (WHERE upper(coalesce(st, '')) NOT IN
         ('', 'MARKET_STATE_OPEN', 'OPEN', 'INSTRUMENT_STATE_OPEN'))
         AS newest_word_not_open,
       count(*) FILTER (WHERE rk <= 6 AND start_at < now()) AS first6_started,
       count(*) FILTER (WHERE rk <= 6 AND start_at < now() - interval '2 hours')
         AS first6_started_gt_2h
  FROM a;

\echo == R3 paper REST read states on the mapped slugs, last 24 h
WITH m AS (
  SELECT DISTINCT f.pmus_slug AS slug
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours')
SELECT coalesce(o.market_state, '(none)') AS market_state,
       (o.error IS NULL) AS error_free, count(*) AS reads,
       count(DISTINCT o.us_market_slug) AS slugs
  FROM paper_book_observations o JOIN m ON m.slug = o.us_market_slug
 WHERE o.observed_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == R4 keyless probe retail outcomes, last 6 h (the error mix a route read would meet)
SELECT coalesce(left(retail_error, 60), '(ok)') AS retail_error,
       count(*) AS samples, count(DISTINCT retail_slug) AS slugs
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 12;

\echo == R5 route receipts by the deployed release, last 2 h: PMUS candidates by reason, both venues eligible
WITH r AS (
  SELECT receipt_id, candidates FROM canonical_route_receipts
   WHERE computed_at > now() - interval '2 hours'),
c AS (
  SELECT r.receipt_id, x ->> 'venue' AS venue,
         coalesce((x ->> 'eligible')::boolean, false) AS eligible,
         coalesce(x ->> 'reason', '(eligible)') AS reason
    FROM r, jsonb_array_elements(r.candidates) x)
SELECT venue, reason, count(*) AS candidates,
       count(DISTINCT receipt_id) AS receipts
  FROM c GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 20;

\echo == R6 kalshi_market_data claims digest (fixture scope and routes)
SELECT (detail -> 'claims' ->> 'fixtures_priced') AS fixtures_priced,
       (detail -> 'claims' ->> 'routes') AS routes,
       (detail -> 'claims' -> 'fixture_scope' ->> 'in_window_cross_venue')
         AS in_window_cross_venue,
       to_char(beat_at, 'MM-DD HH24:MI:SS') AS beat
  FROM service_heartbeats WHERE service = 'kalshi_market_data';
