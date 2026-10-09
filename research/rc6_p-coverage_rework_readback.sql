-- RC6.2 lane p-coverage (REWORK): THE PRODUCTION READBACK of the two rework
-- behaviours, read only. Run once before the deploy (the baseline) and once
-- after (the proof). With rc6_p-coverage_population.sql (P1 / P4) and
-- rc6_p-coverage_readback.sql (R1..R7).
--   W1  every active registry row whose event id's first segment is gtasc
--       (Guatemalan soccer): sport, family, period, gaps, coverage state.
--       AFTER rc6/p-coverage: the soccer money lines the catalogue lists
--       (P1 gtasc listed_active), sport soccer, gaps []. AFTER RC6.1 alone
--       (b3f1b0cd): none -- the defect
--   W2  active POLYMARKET_US registry rows whose market type names a sport
--       and whose code is one of the remaining NON_SPORTS codes. AFTER: any
--       such row is present (never dropped by its code); today none exists
--   W3  settlement readings made under a venue fixture scope
--       (settlement_evidence.fixture_scope): state x reason head. AFTER: never
--       SETTLEMENT_PROVEN_COMPATIBLE / ..._DIFFERENT_BUT_PRICED on basis
--       RULES_TERMS_COMPARISON; FIXTURE_SCOPED_READING_IS_NOT_A_PROOF only if
--       a capture ever reads COMPATIBLE in both contexts (none does today)
-- No write, no secret.
\echo === W1. active gtasc registry rows ===
SELECT coalesce(sport, '-') AS sport, coalesce(market_type, '-') AS market_type,
       coalesce(family, '-') AS family, coalesce(period, '-') AS period,
       coalesce((ontology -> 'gaps')::text, '-') AS gaps,
       coalesce(coverage_state, '-') AS coverage_state,
       split_part(coalesce(settlement_why, '-'), ':', 1) AS settle_head,
       count(*) AS n, max(event_id) AS sample_event
  FROM market_plane_registry
 WHERE active AND lower(split_part(event_id, '-', 1)) = 'gtasc'
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 8 DESC;
\echo === W2. sports-typed active rows under a remaining NON_SPORTS code ===
WITH codes(code) AS (VALUES ('btc'),('eth'),('sol'),('ntflx'),('nobel'),
       ('temp'),('oscars'),('emmys'),('grammys'),('box'),('pol'),
       ('us'),('uscpi'),('uscpicore'),('usfed'),('usgas'),('usunemp'),
       ('usnfp'),('fed'),('cut'),('hike'),('ecb'),('boj'),('boe'),('boc'),
       ('boi'),('bcb'),('cbr'))
SELECT lower(split_part(r.event_id, '-', 1)) AS code, r.market_type,
       coalesce(r.sport, '-') AS sport, count(*) AS n
  FROM market_plane_registry r
 WHERE r.active AND r.venue = 'POLYMARKET_US'
   AND lower(split_part(r.event_id, '-', 1)) IN (SELECT code FROM codes)
   AND replace(lower(btrim(coalesce(r.market_type, ''))), '-', '_') ~
       -- market_plane.ontology.NAMES_SPORT_SQL_REGEX, verbatim
       ('^(?:americanfootball|table_tennis(?:_|$)|basketball|motorsport|'
        || 'pickleball|volleyball|efootball|icehockey|baseball|football|'
        || 'handball|cricket|esports|boxing|hockey|nascar|soccer|tennis|'
        || 'darts|rugby|golf|wnba|mlb|mma|nba|nfl|nhl|ufc|f1)')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;
\echo === W3. settlement readings under a venue fixture scope: state x basis x reason head ===
SELECT settlement_state, settlement_basis,
       split_part(coalesce(settlement_why, '-'), ':', 1) AS head,
       left(coalesce(settlement_why, '-'), 90) AS why,
       settlement_evidence -> 'fixture_scope' ->> 'competition' AS competition,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND settlement_evidence ? 'fixture_scope'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 40;
