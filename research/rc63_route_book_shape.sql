-- RC6.3 route-book lane, readback 2: the production shape the PMUS route
-- book would serve (routed PMUS aliases per pass, cross-venue classes),
-- the plane PMX tops the market plane publishes, and the Kalshi receipt
-- clock refusals by hour. SELECT only.
\echo == F0 clock
SELECT now() AS db_now;

\echo == F1 aliases of fixtures in the claim window, by venue and fingerprint state
WITH w AS (
  SELECT a.* FROM canonical_claim_aliases a
   WHERE a.updated_at > now() - interval '10 minutes')
SELECT venue, count(*) AS aliases,
       count(*) FILTER (WHERE claim_fingerprint IS NOT NULL) AS fingerprinted,
       count(DISTINCT market_id) AS markets,
       count(DISTINCT event_key) AS events,
       count(*) FILTER (WHERE certificate_status = 'CERTIFIED') AS certified
  FROM w GROUP BY 1 ORDER BY 1;

\echo == F2 fingerprints in the window carried by more than one venue (cross-venue classes)
SELECT count(*) AS cross_venue_fingerprints FROM (
  SELECT claim_fingerprint FROM canonical_claim_aliases
   WHERE updated_at > now() - interval '10 minutes'
     AND claim_fingerprint IS NOT NULL
   GROUP BY 1 HAVING count(DISTINCT venue) > 1) z;

\echo == F3 PMUS aliases in the window: refusal reasons
SELECT refusals::text AS refusals, count(*) AS n,
       count(DISTINCT market_id) AS markets
  FROM canonical_claim_aliases
 WHERE venue = 'POLYMARKET_US' AND updated_at > now() - interval '10 minutes'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

\echo == F4 PMUS settlement evidence for the mapped slugs (draw rule, overtime, void)
WITH m AS (
  SELECT DISTINCT f.pmus_slug AS slug, f.league
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours')
SELECT m.league,
       r.evidence -> 'settlement' ->> 'draw_rule' AS draw_rule,
       r.evidence -> 'settlement' ->> 'overtime_included' AS overtime,
       r.evidence -> 'settlement' ->> 'void_rule' AS void_rule,
       r.evidence ->> 'status' AS status, count(*) AS slugs
  FROM m LEFT JOIN market_plane_rules r ON r.contract_id = m.slug
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 20;

\echo == F5 Kalshi settlement evidence for the same fixtures (team tickers)
WITH k AS (
  SELECT DISTINCT unnest(f.team_tickers) AS ticker, f.league
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours')
SELECT k.league,
       r.evidence -> 'settlement' ->> 'draw_rule' AS draw_rule,
       r.evidence -> 'settlement' ->> 'overtime_included' AS overtime,
       r.evidence -> 'settlement' ->> 'void_rule' AS void_rule,
       r.evidence ->> 'status' AS status, count(*) AS tickers
  FROM k LEFT JOIN market_plane_rules r ON r.contract_id = 'kalshi:' || k.ticker
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 20;

\echo == G1 the market plane PMX tops (PRIORITY_PMX_BOOKS): newest event, its age, size
SELECT to_char(at, 'MM-DD HH24:MI:SS') AS at,
       round(extract(epoch FROM now() - at)::numeric, 0) AS age_s,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(payload -> 'books', '{}'::jsonb))) AS books
  FROM market_plane_events WHERE kind = 'PRIORITY_PMX_BOOKS'
 ORDER BY at DESC LIMIT 3;

\echo == G2 one PRIORITY_PMX_BOOKS entry (the fields it carries)
SELECT left(e.value::text, 300) AS entry
  FROM (SELECT payload FROM market_plane_events WHERE kind = 'PRIORITY_PMX_BOOKS'
         ORDER BY at DESC LIMIT 1) p,
       jsonb_each(coalesce(p.payload -> 'books', '{}'::jsonb)) e LIMIT 2;

\echo == G3 mapped slugs present in the newest PRIORITY_PMX_BOOKS event
WITH m AS (
  SELECT DISTINCT f.pmus_slug AS slug
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours'),
p AS (SELECT payload FROM market_plane_events WHERE kind = 'PRIORITY_PMX_BOOKS'
       ORDER BY at DESC LIMIT 1)
SELECT count(*) AS mapped_slugs,
       count(*) FILTER (WHERE (SELECT payload -> 'books' FROM p) ? m.slug) AS in_plane_tops
  FROM m;

\echo == G4 mapped slugs in the plane registry (priority, refdata)
WITH m AS (
  SELECT DISTINCT f.pmus_slug AS slug
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL
     AND f.start_at BETWEEN now() - interval '4 hours'
                        AND now() + interval '36 hours')
SELECT r.priority, r.active, (r.refdata IS NOT NULL) AS has_refdata,
       count(*) AS slugs
  FROM m LEFT JOIN market_plane_registry r ON r.contract_id = m.slug
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo == H1 Kalshi route candidates by reason and hour (BOOK_TIME_IN_FUTURE since RC6.2)
SELECT to_char(date_trunc('hour', r.computed_at), 'MM-DD HH24') AS hour,
       count(*) FILTER (WHERE x ->> 'reason' IS NULL) AS eligible,
       count(*) FILTER (WHERE x ->> 'reason' = 'BOOK_TIME_IN_FUTURE') AS future,
       count(*) FILTER (WHERE x ->> 'reason' = 'INSUFFICIENT_DEPTH') AS depth,
       count(*) FILTER (WHERE x ->> 'reason' = 'STALE_BOOK') AS stale
  FROM canonical_route_receipts r, jsonb_array_elements(r.candidates) x
 WHERE r.computed_at > now() - interval '12 hours'
   AND x ->> 'venue' = 'KALSHI'
 GROUP BY 1 ORDER BY 1;

\echo == H2 kalshi_market_data heartbeat: claims digest (fixtures priced, routes, scope)
SELECT (detail -> 'claims' ->> 'fixtures_priced') AS fixtures_priced,
       (detail -> 'claims' ->> 'aliases') AS aliases,
       (detail -> 'claims' ->> 'routes') AS routes,
       (detail -> 'claims' -> 'fixture_scope' ->> 'scanned_cross_venue') AS scanned_cross_venue,
       (detail -> 'claims' -> 'fixture_scope' ->> 'in_window_cross_venue') AS in_window_cross_venue,
       to_char(beat_at, 'HH24:MI:SS') AS beat
  FROM service_heartbeats WHERE service = 'kalshi_market_data';
