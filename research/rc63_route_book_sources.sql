-- RC6.3 route-book lane: which PMUS book source is actually fresh for the
-- PMUS slugs the Kalshi fixtures map to (the canonical route comparison's
-- PMUS leg). SELECT only.
\echo == A0 clock
SELECT now() AS db_now;

\echo == A1 PMUS-mapped ESTABLISHED Kalshi fixtures in the claim window (-4 h .. +36 h)
WITH m AS (
  SELECT f.event_ticker, f.pmus_slug, f.start_at,
         EXISTS (SELECT 1 FROM kalshi_books_current b
                  WHERE b.readable AND (b.ticker = ANY(f.team_tickers)
                        OR b.ticker = f.tie_ticker)) AS kalshi_readable
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours')
SELECT count(*) AS fixtures, count(DISTINCT pmus_slug) AS slugs,
       count(*) FILTER (WHERE kalshi_readable) AS with_readable_kalshi_book,
       count(*) FILTER (WHERE start_at < now()) AS started
  FROM m;

\echo == A2 per mapped slug: newest book per source and its age in seconds
WITH m AS (
  SELECT DISTINCT f.pmus_slug AS slug, min(f.start_at) AS start_at
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'
   GROUP BY 1)
SELECT m.slug, to_char(m.start_at, 'MM-DD HH24:MI') AS start_utc,
       round(extract(epoch FROM now() - p.observed_at)::numeric, 1) AS paper_rest_age_s,
       p.source AS paper_source, p.read_basis AS paper_basis,
       round(extract(epoch FROM now() - s.received_at)::numeric, 1) AS stream_ev_age_s,
       s.current_ok AS stream_ev_ok, s.service AS stream_ev_service,
       jsonb_array_length(coalesce(s.top_n -> 'offers', '[]'::jsonb)) AS stream_ev_offer_lv,
       round(extract(epoch FROM now() - q.stream_received_at)::numeric, 1) AS probe_stream_age_s,
       round(extract(epoch FROM now() - q.retail_response_at)::numeric, 1) AS probe_retail_age_s
  FROM m
  LEFT JOIN LATERAL (
    SELECT observed_at, source, read_basis FROM paper_book_observations o
     WHERE o.us_market_slug = m.slug AND o.error IS NULL
     ORDER BY o.observed_at DESC LIMIT 1) p ON true
  LEFT JOIN LATERAL (
    SELECT received_at, current_ok, service, top_n
      FROM institutional_stream_evidence e
     WHERE e.symbol = m.slug AND e.minute > now() - interval '6 hours'
     ORDER BY e.minute DESC LIMIT 1) s ON true
  LEFT JOIN LATERAL (
    SELECT stream_received_at, retail_response_at
      FROM institutional_same_book_probe b
     WHERE b.retail_slug = m.slug AND b.probed_at > now() - interval '6 hours'
     ORDER BY b.probed_at DESC LIMIT 1) q ON true
 ORDER BY m.start_at;

\echo == A3 age buckets per source over the mapped slugs
WITH m AS (
  SELECT DISTINCT f.pmus_slug AS slug
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'),
a AS (
  SELECT m.slug,
         (SELECT extract(epoch FROM now() - max(o.observed_at))
            FROM paper_book_observations o
           WHERE o.us_market_slug = m.slug AND o.error IS NULL
             AND o.observed_at > now() - interval '24 hours') AS paper_age,
         (SELECT extract(epoch FROM now() - max(e.received_at))
            FROM institutional_stream_evidence e
           WHERE e.symbol = m.slug AND e.minute > now() - interval '24 hours') AS stream_age
    FROM m)
SELECT 'paper_book_observations' AS source,
       count(*) FILTER (WHERE paper_age <= 30) AS le_30s,
       count(*) FILTER (WHERE paper_age > 30 AND paper_age <= 300) AS le_300s,
       count(*) FILTER (WHERE paper_age > 300 AND paper_age <= 900) AS le_900s,
       count(*) FILTER (WHERE paper_age > 900) AS older,
       count(*) FILTER (WHERE paper_age IS NULL) AS none_24h,
       count(*) AS slugs
  FROM a
UNION ALL
SELECT 'institutional_stream_evidence',
       count(*) FILTER (WHERE stream_age <= 30),
       count(*) FILTER (WHERE stream_age > 30 AND stream_age <= 300),
       count(*) FILTER (WHERE stream_age > 300 AND stream_age <= 900),
       count(*) FILTER (WHERE stream_age > 900),
       count(*) FILTER (WHERE stream_age IS NULL),
       count(*)
  FROM a;

\echo == B1 who writes institutional_stream_evidence (last 15 min)
SELECT service, left(process_id, 24) AS process, count(*) AS rows_n,
       count(DISTINCT symbol) AS symbols,
       to_char(max(recorded_at), 'HH24:MI:SS') AS newest_recorded,
       to_char(max(received_at), 'HH24:MI:SS') AS newest_received,
       count(*) FILTER (WHERE current_ok) AS current_ok_rows
  FROM institutional_stream_evidence
 WHERE minute > now() - interval '15 minutes'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;

\echo == B2 sample symbols in recent stream evidence (form of the identifier)
SELECT DISTINCT symbol FROM institutional_stream_evidence
 WHERE minute > now() - interval '15 minutes' AND symbol <> '*'
 ORDER BY 1 LIMIT 12;

\echo == B3 paper_book_observations writers in the last hour (source / basis)
SELECT source, read_basis, count(*) AS rows_n,
       count(DISTINCT us_market_slug) AS slugs,
       count(*) FILTER (WHERE error IS NULL) AS ok_rows,
       to_char(max(observed_at), 'HH24:MI:SS') AS newest
  FROM paper_book_observations
 WHERE observed_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;

\echo == C1 heartbeats of the market-data services
SELECT service, status, to_char(beat_at, 'MM-DD HH24:MI:SS') AS beat,
       round(extract(epoch FROM now() - beat_at)::numeric, 0) AS age_s
  FROM service_heartbeats
 WHERE service ILIKE ANY (ARRAY['%institutional%', '%kalshi%', '%market_plane%',
                                '%market-plane%', '%paper%market%', '%p5%',
                                '%adriana%'])
 ORDER BY 1;

\echo == C2 institutional_md heartbeat detail keys (top level)
SELECT k FROM service_heartbeats h, jsonb_object_keys(h.detail) k
 WHERE h.service = 'institutional_md' ORDER BY 1 LIMIT 60;

\echo == C3 institutional_md stream and books digest
SELECT left((detail -> 'stream')::text, 900) AS stream,
       left((detail -> 'books')::text, 600) AS books,
       left((detail -> 'focus_universe')::text, 600) AS focus
  FROM service_heartbeats WHERE service = 'institutional_md';

\echo == D1 canonical route receipts, 24 h: totals and both-venue-eligible
WITH r AS (
  SELECT receipt_id, candidates FROM canonical_route_receipts
   WHERE computed_at > now() - interval '24 hours'),
c AS (
  SELECT r.receipt_id, x ->> 'venue' AS venue,
         coalesce((x ->> 'eligible')::boolean, false) AS eligible,
         x ->> 'reason' AS reason
    FROM r, jsonb_array_elements(r.candidates) x)
SELECT (SELECT count(*) FROM r) AS receipts,
       (SELECT count(DISTINCT receipt_id) FROM c WHERE venue = 'POLYMARKET_US') AS with_pmus_candidate,
       (SELECT count(*) FROM (SELECT receipt_id FROM c GROUP BY 1
          HAVING bool_or(venue = 'KALSHI' AND eligible)
             AND bool_or(venue = 'POLYMARKET_US' AND eligible)) z) AS both_eligible;

\echo == D2 PMUS route candidates by reason, 24 h
SELECT coalesce(x ->> 'reason', 'ELIGIBLE') AS reason, count(*) AS n
  FROM canonical_route_receipts r, jsonb_array_elements(r.candidates) x
 WHERE r.computed_at > now() - interval '24 hours'
   AND x ->> 'venue' = 'POLYMARKET_US'
 GROUP BY 1 ORDER BY 2 DESC;

\echo == D3 Kalshi route candidates by reason, 24 h
SELECT coalesce(x ->> 'reason', 'ELIGIBLE') AS reason, count(*) AS n
  FROM canonical_route_receipts r, jsonb_array_elements(r.candidates) x
 WHERE r.computed_at > now() - interval '24 hours'
   AND x ->> 'venue' = 'KALSHI'
 GROUP BY 1 ORDER BY 2 DESC;

\echo == D4 newest receipts carrying a PMUS candidate (book age, reason)
SELECT to_char(r.computed_at, 'MM-DD HH24:MI') AS at, left(r.claim_fingerprint, 12) AS fp,
       x ->> 'market_id' AS market, x ->> 'side' AS side, x ->> 'reason' AS reason,
       x ->> 'book_age_s' AS book_age_s, x ->> 'ask' AS ask, x ->> 'depth' AS depth
  FROM canonical_route_receipts r, jsonb_array_elements(r.candidates) x
 WHERE r.computed_at > now() - interval '24 hours'
   AND x ->> 'venue' = 'POLYMARKET_US'
 ORDER BY r.computed_at DESC LIMIT 12;

\echo == E1 canonical claim aliases by venue (current), fingerprints carried by both venues
SELECT venue, count(*) AS aliases,
       count(*) FILTER (WHERE claim_fingerprint IS NOT NULL) AS fingerprinted,
       count(*) FILTER (WHERE observed_at > now() - interval '30 seconds') AS book_le_30s,
       count(*) FILTER (WHERE observed_at > now() - interval '5 minutes') AS book_le_300s,
       count(*) FILTER (WHERE observed_at IS NULL) AS no_book,
       to_char(max(updated_at), 'MM-DD HH24:MI:SS') AS newest_write
  FROM canonical_claim_aliases GROUP BY 1 ORDER BY 1;
SELECT count(*) AS fingerprints_on_both_venues FROM (
  SELECT claim_fingerprint FROM canonical_claim_aliases
   WHERE claim_fingerprint IS NOT NULL GROUP BY 1
  HAVING count(DISTINCT venue) > 1) z;

\echo == E2 PMUS aliases: refusals (why no fingerprint)
SELECT refusals::text AS refusals, count(*) AS n
  FROM canonical_claim_aliases WHERE venue = 'POLYMARKET_US'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
