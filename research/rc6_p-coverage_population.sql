-- RC6.2 lane p-coverage (REWORK), read only. THE POPULATION THE NON-SPORTS
-- EXCLUSION REMOVES. RC6.1 (b3f1b0cd) reads the venue league code off the
-- FIRST event-slug segment (market_plane.populate.league_of) and drops every
-- catalogue market whose code is in ontology.NON_SPORTS_LEAGUES before its
-- market type is read; 732cc0c6 read the SECOND segment, so a code such as
-- `gtasc` was never matched there. The review found `gtasc` lists only
-- soccer games (rc6_p-coverage_fix_evidence F1, code_collisions H1).
--   P1  every NON_SPORTS code of b3f1b0cd: catalogue markets by code x
--       whether the venue's own market type names a sport (the ontology's
--       SPORT_PREFIXES heads) x head, listed-active (populate's rule: an
--       active listing state, updated within 3 h), one event slug + title
--   P2  every `gtasc` catalogue market in full: market slug, event slug,
--       title, market type, listing state, start, updated
--   P3  open PAPER positions (open_position_canon's rule) on a market whose
--       code (market slug grammar or catalogue event slug) is a NON_SPORTS
--       code, with the registry row production holds for it
--   P4  production's active registry rows whose event id's first segment is
--       a NON_SPORTS code: code x sport x gaps x required reason
--   P5  rc6_p-coverage_scope_context G1's '<no premap row>' refusals: the
--       venue code read from the valuation's own recorded fixture key
--       (settlement_comparison.fixture_venue_key = 'event:<venue event
--       slug>'), with the newest / oldest decision
-- No write, no secret.
\echo === P1. NON_SPORTS codes: code x names_sport x head x markets x listed_active ===
WITH codes(code) AS (VALUES ('btc'),('eth'),('sol'),('ntflx'),('nobel'),
       ('temp'),('gtasc'),('oscars'),('emmys'),('grammys'),('box'),('pol'),
       ('us'),('uscpi'),('uscpicore'),('usfed'),('usgas'),('usunemp'),
       ('usnfp'),('fed'),('cut'),('hike'),('ecb'),('boj'),('boe'),('boc'),
       ('boi'),('bcb'),('cbr')),
m AS MATERIALIZED (
  SELECT market_slug, max(event_slug) AS ev, max(event_title) AS title,
         max(sports_type) AS st, max(team_league) AS tl,
         (max(listing_state) IN ('PREGAME','LIVE','NOT_LIVE','STARTED',
                                 'UNKNOWN')
          AND max(updated_at) > now() - interval '3 hours') AS listed_active
    FROM us_premap WHERE market_slug IS NOT NULL GROUP BY market_slug),
k AS (
  SELECT m.*,
         CASE WHEN coalesce(ev, '') = '' THEN lower(coalesce(tl, ''))
              WHEN lower(split_part(ev, '-', 1)) IN ('atc','aec','asc','tsc',
                                                     'astatc','cpc')
              THEN lower(split_part(ev, '-', 2))
              ELSE lower(split_part(ev, '-', 1)) END AS code,
         replace(lower(btrim(coalesce(st, ''))), '-', '_') AS raw
    FROM m)
SELECT k.code,
       k.raw ~ ('^(americanfootball|football|nfl|basketball|nba|wnba|'
                || 'baseball|mlb|hockey|icehockey|nhl|soccer|tennis|'
                || 'efootball|esports|ufc|mma|boxing|golf|cricket|darts|'
                || 'volleyball|handball|rugby|motorsport|nascar|f1|'
                || 'pickleball|table_tennis(_|$))') AS names_sport,
       CASE WHEN k.raw = '' THEN '<none>'
            WHEN k.raw LIKE 'table_tennis%' THEN 'table_tennis'
            ELSE split_part(k.raw, '_', 1) END AS head,
       count(*) AS markets,
       count(*) FILTER (WHERE k.listed_active) AS listed_active,
       max(k.ev) AS sample_event, max(left(k.title, 60)) AS sample_title
  FROM k JOIN codes c ON c.code = k.code
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
\echo === P2. every gtasc catalogue market: [market_slug, event_slug, title, sports_type, listing_state, game_start, updated_at] ===
WITH m AS MATERIALIZED (
  SELECT market_slug, max(event_slug) AS ev, max(event_title) AS title,
         max(sports_type) AS st, max(listing_state) AS ls,
         max(game_start) AS gs, max(updated_at) AS ua
    FROM us_premap WHERE market_slug IS NOT NULL
     AND (lower(split_part(event_slug, '-', 1)) = 'gtasc'
          OR lower(split_part(market_slug, '-', 2)) = 'gtasc')
   GROUP BY market_slug),
b AS (SELECT m.*, (row_number() OVER (ORDER BY ev, market_slug) - 1) / 25
                  AS grp FROM m)
SELECT json_agg(json_build_array(market_slug, ev, left(title, 80), st, ls,
                                 gs::text, ua::text)
                ORDER BY ev, market_slug)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === P3. open PAPER positions on a NON_SPORTS-coded market, with the registry row ===
WITH codes(code) AS (VALUES ('btc'),('eth'),('sol'),('ntflx'),('nobel'),
       ('temp'),('gtasc'),('oscars'),('emmys'),('grammys'),('box'),('pol'),
       ('us'),('uscpi'),('uscpicore'),('usfed'),('usgas'),('usunemp'),
       ('usnfp'),('fed'),('cut'),('hike'),('ecb'),('boj'),('boe'),('boc'),
       ('boi'),('bcb'),('cbr')),
o AS MATERIALIZED (
  SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
         f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                     AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                     AS sold
            FROM paper_fills
           GROUP BY account_id, group_id, us_market_slug, holding_side) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements
                ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':'
                          || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
     AND f.us_market_slug IS NOT NULL),
p AS MATERIALIZED (
  SELECT DISTINCT ON (market_slug) market_slug, event_slug, sports_type
    FROM us_premap WHERE market_slug IN (SELECT us_market_slug FROM o)
   ORDER BY market_slug, updated_at DESC),
x AS (
  SELECT o.us_market_slug AS slug, o.group_id, o.holding_side,
         round(o.open_qty::numeric, 4) AS open_qty,
         CASE WHEN lower(split_part(o.us_market_slug, '-', 1)) IN
                   ('atc','aec','asc','tsc','astatc','cpc')
              THEN lower(split_part(o.us_market_slug, '-', 2))
              ELSE lower(split_part(o.us_market_slug, '-', 1)) END
           AS slug_code,
         lower(split_part(p.event_slug, '-', 1)) AS premap_code,
         p.event_slug, p.sports_type
    FROM o LEFT JOIN p ON p.market_slug = o.us_market_slug)
SELECT x.slug, x.group_id, x.holding_side, x.open_qty, x.slug_code,
       coalesce(x.premap_code, '-') AS premap_code,
       coalesce(x.sports_type, '-') AS sports_type,
       coalesce(r.active::text, '<no registry row>') AS reg_active,
       coalesce(r.sport, '-') AS reg_sport,
       coalesce(r.required_reason, '-') AS reg_reason,
       coalesce((r.ontology -> 'gaps')::text, '-') AS reg_gaps,
       coalesce(r.priority::text, '-') AS reg_priority
  FROM x LEFT JOIN market_plane_registry r ON r.contract_id = x.slug
 WHERE x.slug_code IN (SELECT code FROM codes)
    OR x.premap_code IN (SELECT code FROM codes)
 ORDER BY 1 LIMIT 100;
\echo === P3b. active registry stubs NOT_IN_CURRENT_CATALOGUE whose market the catalogue does hold, by catalogue code ===
SELECT lower(split_part(p.event_slug, '-', 1)) AS premap_code,
       r.required_reason, count(*) AS n, max(r.contract_id) AS sample
  FROM market_plane_registry r
  JOIN (SELECT DISTINCT ON (market_slug) market_slug, event_slug
          FROM us_premap WHERE market_slug IS NOT NULL
         ORDER BY market_slug, updated_at DESC) p
    ON p.market_slug = r.contract_id
 WHERE r.active AND r.ontology -> 'gaps' ? 'NOT_IN_CURRENT_CATALOGUE'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;
\echo === P4. production active registry rows by event-id first segment in the NON_SPORTS set ===
WITH codes(code) AS (VALUES ('btc'),('eth'),('sol'),('ntflx'),('nobel'),
       ('temp'),('gtasc'),('oscars'),('emmys'),('grammys'),('box'),('pol'),
       ('us'),('uscpi'),('uscpicore'),('usfed'),('usgas'),('usunemp'),
       ('usnfp'),('fed'),('cut'),('hike'),('ecb'),('boj'),('boe'),('boc'),
       ('boi'),('bcb'),('cbr'))
SELECT lower(split_part(r.event_id, '-', 1)) AS code,
       coalesce(r.sport, '-') AS sport,
       coalesce(r.market_type, '-') AS market_type,
       coalesce((r.ontology -> 'gaps')::text, '-') AS gaps,
       coalesce(r.required_reason, '-') AS reason,
       split_part(coalesce(r.settlement_why, '-'), ':', 1) AS settle_head,
       count(*) AS n
  FROM market_plane_registry r
 WHERE r.active AND r.venue = 'POLYMARKET_US'
   AND lower(split_part(r.event_id, '-', 1)) IN (SELECT code FROM codes)
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 7 DESC LIMIT 80;
\echo === P5. G1 no-premap-row generic-key soccer refusals: venue code from the valuation own fixture key ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.decided_at, e.settlement_comparison AS s
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family = 'soccer'
     AND e.settlement_comparison -> 'fixture_acquisition' ->> 'refusal'
         LIKE '%:pinnapi_soccer'
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC),
n AS (
  SELECT v.* FROM v
   WHERE NOT EXISTS (SELECT 1 FROM us_premap p
                      WHERE p.market_slug = v.slug))
SELECT CASE WHEN coalesce(s ->> 'fixture_venue_key', '') = ''
            THEN '<no fixture key recorded>'
            ELSE lower(split_part(regexp_replace(s ->> 'fixture_venue_key',
                                                 '^event:', ''), '-', 1))
       END AS code_from_fixture_key,
       count(*) AS n, min(decided_at)::text AS oldest,
       max(decided_at)::text AS newest,
       max(s ->> 'fixture_venue_key') AS sample_key,
       max(slug) AS sample_slug
  FROM n GROUP BY 1 ORDER BY 2 DESC LIMIT 60;
\echo === P5b. the same rows: newest decision age against the catalogue 26 h prune ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.decided_at
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family = 'soccer'
     AND e.settlement_comparison -> 'fixture_acquisition' ->> 'refusal'
         LIKE '%:pinnapi_soccer'
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT (EXISTS (SELECT 1 FROM us_premap p WHERE p.market_slug = v.slug))
         AS premap_row_now,
       (v.decided_at < now() - interval '26 hours') AS newest_older_than_26h,
       count(*) AS n
  FROM v GROUP BY 1, 2 ORDER BY 1, 2;
