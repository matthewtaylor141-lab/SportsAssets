-- RC6.3 route-book lane, readback 3: the expected production effect of the
-- PMUS route book on the CURRENT claims (route candidates per venue, the
-- paper read age each PMUS route candidate would be judged on, the reads a
-- recording pass would need), the market plane PMX top ages, and the keyless
-- public read rate the workers already make. SELECT only.
\echo == P0 clock
SELECT now() AS db_now;

\echo == P1 claim aliases written in the last 10 min, by venue
SELECT venue, count(*) AS aliases,
       count(*) FILTER (WHERE claim_fingerprint IS NOT NULL) AS fingerprinted,
       count(*) FILTER (WHERE certificate_status = 'CERTIFIED') AS certified,
       count(DISTINCT market_id) AS markets,
       count(DISTINCT event_key) AS events
  FROM canonical_claim_aliases
 WHERE updated_at > now() - interval '10 minutes'
 GROUP BY 1 ORDER BY 1;

\echo == P2 fingerprints carried by more than one venue (cross-venue classes)
SELECT count(*) AS cross_venue_fingerprints FROM (
  SELECT claim_fingerprint FROM canonical_claim_aliases
   WHERE updated_at > now() - interval '10 minutes'
     AND claim_fingerprint IS NOT NULL
   GROUP BY 1 HAVING count(DISTINCT venue) > 1) z;

\echo == P3 PMUS aliases in the last 10 min: refusal reasons
SELECT refusals::text AS refusals, count(*) AS n,
       count(DISTINCT market_id) AS markets
  FROM canonical_claim_aliases
 WHERE venue = 'POLYMARKET_US' AND updated_at > now() - interval '10 minutes'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

\echo == P4 PMUS route candidates now (fingerprinted, certified): newest paper read age
WITH c AS (
  SELECT DISTINCT market_id FROM canonical_claim_aliases
   WHERE venue = 'POLYMARKET_US' AND updated_at > now() - interval '10 minutes'
     AND claim_fingerprint IS NOT NULL)
SELECT c.market_id,
       round(extract(epoch FROM now() - p.observed_at)::numeric, 1) AS paper_age_s,
       p.market_state
  FROM c LEFT JOIN LATERAL (
    SELECT observed_at, market_state FROM paper_book_observations o
     WHERE o.us_market_slug = c.market_id AND o.error IS NULL
       AND o.observed_at > now() - interval '900 seconds'
     ORDER BY o.observed_at DESC LIMIT 1) p ON true
 ORDER BY 1 LIMIT 40;

\echo == P5 the production-shaped candidate set: Kalshi-mapped PMUS slugs in the claim window, paper read age buckets
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
             AND o.observed_at > now() - interval '900 seconds') AS paper_age
    FROM m)
SELECT count(*) AS mapped_slugs,
       count(*) FILTER (WHERE paper_age <= 20) AS used_without_a_read_le_20s,
       count(*) FILTER (WHERE paper_age > 20 AND paper_age <= 30) AS read_if_budget_20_30s,
       count(*) FILTER (WHERE paper_age > 30) AS needs_read_older_than_30s,
       count(*) FILTER (WHERE paper_age IS NULL) AS needs_read_none_in_900s
  FROM a;

\echo == P6 market plane PMX tops: newest events, age, members
SELECT to_char(at, 'MM-DD HH24:MI:SS') AS at,
       round(extract(epoch FROM now() - at)::numeric, 0) AS event_age_s,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(payload -> 'books', '{}'::jsonb))) AS books
  FROM market_plane_events WHERE kind = 'PRIORITY_PMX_BOOKS'
 ORDER BY at DESC LIMIT 3;

\echo == P7 mapped slugs in the newest PMX tops event, and the age of each top receipt
WITH m AS (
  SELECT DISTINCT f.pmus_slug AS slug
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'),
p AS (SELECT payload -> 'books' AS books FROM market_plane_events
       WHERE kind = 'PRIORITY_PMX_BOOKS' ORDER BY at DESC LIMIT 1)
SELECT m.slug,
       round((extract(epoch FROM now())
              - ((p.books -> m.slug) ->> 'received_at')::float8)::numeric, 1) AS top_receipt_age_s,
       (p.books -> m.slug) ? 'best_bid' AS has_top,
       (p.books -> m.slug) ? 'bid_size' OR (p.books -> m.slug) ? 'bids' AS has_sizes
  FROM m, p WHERE p.books ? m.slug ORDER BY 1;

\echo == P8 keyless public reads the workers already make (institutional_md same-book probe)
SELECT left((detail -> 'sameBook')::text, 500) AS same_book,
       left((detail -> 'venueRateLimit')::text, 500) AS venue_rate_limit,
       to_char(beat_at, 'HH24:MI:SS') AS beat
  FROM service_heartbeats WHERE service = 'institutional_md';

\echo == P9 same-book probe retail reads per hour (last 6 h) and retail errors
SELECT to_char(date_trunc('hour', probed_at), 'MM-DD HH24') AS hour,
       count(*) AS samples,
       count(*) FILTER (WHERE retail_ok) AS retail_ok,
       count(*) FILTER (WHERE retail_ok IS FALSE) AS retail_failed,
       count(*) FILTER (WHERE retail_error ILIKE '%429%' OR retail_error ILIKE '%ratelimit%') AS retail_429
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 1;

\echo == P10 route receipts, last 2 h: totals, PMUS candidates by reason, both venues eligible
WITH r AS (
  SELECT receipt_id, candidates FROM canonical_route_receipts
   WHERE computed_at > now() - interval '2 hours'),
c AS (
  SELECT r.receipt_id, x ->> 'venue' AS venue,
         coalesce((x ->> 'eligible')::boolean, false) AS eligible,
         x ->> 'reason' AS reason
    FROM r, jsonb_array_elements(r.candidates) x)
SELECT (SELECT count(*) FROM r) AS receipts,
       (SELECT count(*) FROM c WHERE venue = 'POLYMARKET_US') AS pmus_candidates,
       (SELECT count(*) FROM c WHERE venue = 'KALSHI') AS kalshi_candidates,
       (SELECT count(*) FROM c WHERE venue = 'KALSHI' AND eligible) AS kalshi_eligible,
       (SELECT count(*) FROM (SELECT receipt_id FROM c GROUP BY 1
          HAVING bool_or(venue = 'KALSHI' AND eligible)
             AND bool_or(venue = 'POLYMARKET_US' AND eligible)) z) AS both_eligible;

\echo == P11 kalshi_market_data heartbeat claims digest
SELECT (detail -> 'claims' ->> 'fixtures_priced') AS fixtures_priced,
       (detail -> 'claims' ->> 'aliases') AS aliases,
       (detail -> 'claims' ->> 'routes') AS routes,
       (detail -> 'claims' -> 'fixture_scope' ->> 'in_window_cross_venue') AS in_window_cross_venue,
       to_char(beat_at, 'HH24:MI:SS') AS beat
  FROM service_heartbeats WHERE service = 'kalshi_market_data';
