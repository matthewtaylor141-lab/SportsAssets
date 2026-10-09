-- RC6.2 lane p-coverage (FIX), read only. The production rows the fixes are
-- written against and measured on:
--   F1  soccer venue league codes (first segment of the venue EVENT slug):
--       events, date range and one event title each -- which code is the
--       UEFA Nations League / Champions League / ... on the venue
--   F2  venue_fixture_metadata as held: competition x phase x format x
--       reader x refusals, with the newest retrieval and one key
--   F3  every active Polymarket US market type with the registry's current
--       family / period labels and the settlement reason head, in full
--   F4  h2h valuations (14 days) by sport x venue league code x the fixture
--       acquisition refusal and source -- what happened to the scope reads
--   F5  active PMUS rows with a SPORT_NOT_NORMALIZED / LEAGUE_CODE_AMBIGUOUS
--       gap by first slug segment, with one event id, title and question
--   F6  wbc rows: event id, title and question, all of them
-- No write, no secret.
\echo === F1. soccer league codes: [code, events, first_start, last_start, sample_event_slug, sample_title] ===
WITH s AS MATERIALIZED (
  SELECT split_part(event_slug, '-', 1) AS code, event_slug,
         max(event_title) AS title, max(game_start) AS gs
    FROM us_premap
   WHERE sports_type LIKE 'soccer%' AND event_slug IS NOT NULL
     AND updated_at > now() - interval '45 days'
   GROUP BY 1, 2),
k AS (
  SELECT code, count(*) AS events, min(gs) AS first_start, max(gs) AS last_start,
         max(event_slug) AS sample_slug, max(title) AS sample_title
    FROM s GROUP BY 1),
b AS (SELECT k.*, (row_number() OVER (ORDER BY events DESC, code) - 1) / 20
                  AS grp FROM k)
SELECT json_agg(json_build_array(code, events, first_start::date::text,
                                 last_start::date::text, sample_slug,
                                 left(sample_title, 80))
                ORDER BY events DESC, code)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === F2. venue_fixture_metadata held ===
SELECT venue, sport_family, coalesce(competition, '-') AS competition,
       coalesce(phase, '-') AS phase, coalesce(game_format, '-') AS fmt,
       reader_version, refusals::text AS refusals, count(*) AS n,
       max(retrieved_at) AS newest, max(venue_fixture_key) AS sample_key
  FROM venue_fixture_metadata
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 8 DESC LIMIT 60;
\echo === F3. active PMUS market types: [market_type, family, period, settlement reason head, n] ===
WITH k AS (
  SELECT coalesce(market_type, '<null>') AS mt,
         coalesce(family, '<null>') AS fam, coalesce(period, '<null>') AS per,
         coalesce(split_part(settlement_why, ':', 1), '<null>') AS why,
         count(*) AS n
    FROM market_plane_registry
   WHERE active AND venue = 'POLYMARKET_US'
   GROUP BY 1, 2, 3, 4),
b AS (SELECT k.*, (row_number() OVER (ORDER BY n DESC, mt, why) - 1) / 30
                  AS grp FROM k)
SELECT json_agg(json_build_array(mt, fam, per, why, n)
                ORDER BY n DESC, mt, why)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === F4. h2h valuations (14 d, latest per slug): sport x league code x acquisition refusal x fixture source ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.settlement_comparison AS s, e.decided_at
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT v.sport_family,
       coalesce(split_part(g.event_id, '-', 1), '?') AS code,
       left(coalesce(v.s -> 'fixture_acquisition' ->> 'refusal',
                     v.s ->> 'fixture_read', '-'), 70) AS acq,
       left(coalesce(v.s ->> 'fixture_source', '-'), 40) AS src,
       coalesce(v.s ->> 'scope_phase', '-') AS phase,
       coalesce(v.s ->> 'verdict', v.s ->> 'compatibility', '-') AS verdict,
       count(*) AS n, max(v.decided_at)::date::text AS newest
  FROM v LEFT JOIN market_plane_registry g ON g.contract_id = v.slug
 WHERE v.sport_family IN ('soccer', 'baseball')
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 7 DESC LIMIT 120;
\echo === F5. unmapped-sport PMUS rows by first slug segment: [code, gap, n, sample event id, title, question] ===
WITH k AS (
  SELECT split_part(coalesce(event_id, contract_id), '-', 1) AS code,
         coalesce(coverage_why, '<null>') AS why, count(*) AS n,
         max(event_id) AS ev,
         max(left(ontology ->> 'event_title', 70)) AS title,
         max(left(ontology ->> 'question', 90)) AS q
    FROM market_plane_registry
   WHERE active AND venue = 'POLYMARKET_US'
     AND (coverage_why LIKE '%SPORT_NOT_NORMALIZED%'
          OR coverage_why LIKE '%LEAGUE_CODE_AMBIGUOUS%')
   GROUP BY 1, 2),
b AS (SELECT k.*, (row_number() OVER (ORDER BY n DESC, code) - 1) / 20
                  AS grp FROM k)
SELECT json_agg(json_build_array(code, why, n, ev, title, q)
                ORDER BY n DESC, code)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === F6. wbc rows: [contract_id, event_id, market_type, title, question] ===
WITH k AS (
  SELECT contract_id, event_id, market_type,
         left(ontology ->> 'event_title', 80) AS title,
         left(ontology ->> 'question', 110) AS q
    FROM market_plane_registry
   WHERE active AND venue = 'POLYMARKET_US'
     AND split_part(coalesce(event_id, contract_id), '-', 1) = 'wbc'),
b AS (SELECT k.*, (row_number() OVER (ORDER BY contract_id) - 1) / 25
                  AS grp FROM k)
SELECT json_agg(json_build_array(contract_id, event_id, market_type, title, q)
                ORDER BY contract_id)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
