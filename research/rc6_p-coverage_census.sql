-- RC6.2 lane p-coverage (DIAGNOSE), read only. The sports-and-market
-- coverage unit (scorecard_14 category 7: PRICEABLE / (active - EXTERNAL))
-- read contract by contract from the plane's own registry, so every failing
-- member is counted once and every blocker it carries is named:
--   K0  which code wrote the rows (RC6.1 markers) and the population
--   K1  every coverage reason, in full (no top-40 cut), packed as JSON
--   K2  every settlement reason, in full, packed as JSON
--   K3  the chain matrix: coverage state x when the event is (decision
--       horizon -6 h .. +24 h, read from collector_coverage) x whether a
--       decision valuation with a probability exists in 24 h (the ONLY
--       fair-value source the coverage matrix accepts)
--   K4  the valuation reach: valued slugs in 24 h, their registry state
--   K5  in-horizon, never-valued PMUS contracts: whether their EVENT
--       reached the candidate pipeline (ext_candidate_outcomes, one row per
--       provider event) and its first refusal
--   K6  the ontology gaps by venue code of the event slug and market type
--   K7  rules rows held per venue (published, parse status)
--   K8  mapped PMUS never-attested contracts: market type x reason
-- No write, no secret.
\echo === K0. population and code-version markers ===
SELECT venue, count(*) AS active,
       count(*) FILTER (WHERE coverage_state = 'PRICEABLE') AS priceable,
       count(*) FILTER (WHERE ontology -> 'meaning' ->> 'ontology_version'
                              = 'KALSHI_ONTOLOGY_V1') AS kalshi_ontology_v1,
       count(*) FILTER (WHERE settlement_why LIKE 'LINE_%'
                           OR settlement_why LIKE 'BOOK_TERMS_SCOPE%'
                           OR settlement_why LIKE 'VENUE_LINE%'
                           OR settlement_why LIKE '%BOOK_MARKET_RULES_SECTION%'
                           OR settlement_why LIKE '%BOOK_RULES_FOR_THIS_SPORT%')
         AS rc61_line_whys,
       max(coverage_at) AS last_coverage_at,
       max(settlement_at) AS last_settlement_at, now() AS read_at
  FROM market_plane_registry WHERE active GROUP BY 1 ORDER BY 1;
\echo === K1. every coverage reason: [venue, state, why(140), n] x 25 per line ===
WITH k AS (
  SELECT venue, coalesce(coverage_state, '<null>') AS st,
         left(coalesce(coverage_why, '<null>'), 140) AS why, count(*) AS n
    FROM market_plane_registry WHERE active GROUP BY 1, 2, 3),
b AS (SELECT k.*, (row_number() OVER (ORDER BY n DESC, venue, st, why) - 1)
                  / 25 AS grp FROM k)
SELECT json_agg(json_build_array(venue, st, why, n)
                ORDER BY n DESC, venue, st, why)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === K2. every settlement reason: [venue, state, basis, why(140), n] x 25 per line ===
WITH k AS (
  SELECT venue, coalesce(settlement_state, '<null>') AS st,
         coalesce(settlement_basis, '<null>') AS basis,
         left(coalesce(settlement_why, '<null>'), 140) AS why, count(*) AS n
    FROM market_plane_registry WHERE active GROUP BY 1, 2, 3, 4),
b AS (SELECT k.*, (row_number() OVER (ORDER BY n DESC, venue, st, basis, why)
                   - 1) / 25 AS grp FROM k)
SELECT json_agg(json_build_array(venue, st, basis, why, n)
                ORDER BY n DESC, venue, st, basis, why)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === K3. chain matrix: venue x coverage state x gap kind x when x valued(24h) ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
         probability IS NOT NULL AS has_p
    FROM external_valuations
   WHERE decided_at > now() - interval '24 hours'
     AND us_market_slug IS NOT NULL
   ORDER BY us_market_slug, decided_at DESC, id DESC),
c AS MATERIALIZED (
  SELECT g.venue, g.coverage_state, g.coverage_why,
         CASE WHEN v.slug IS NULL THEN 'NOT_VALUED'
              WHEN v.has_p THEN 'VALUED_WITH_P' ELSE 'VALUED_NO_P' END AS val,
         CASE WHEN g.event_start IS NULL THEN 'NO_START'
              WHEN g.event_start < now() - interval '6 hours' THEN 'STARTED_GT_6H'
              WHEN g.event_start <= now() + interval '24 hours' THEN 'IN_HORIZON'
              WHEN g.event_start <= now() + interval '48 hours' THEN 'H24_48'
              WHEN g.event_start <= now() + interval '7 days' THEN 'D2_7'
              ELSE 'GT_7D' END AS wh
    FROM market_plane_registry g LEFT JOIN v ON v.slug = g.contract_id
   WHERE g.active)
SELECT venue, coverage_state,
       CASE WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                 AND coverage_why LIKE 'ONTOLOGY_GAPS%' THEN 'ONTOLOGY'
            WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'BOOK'
            ELSE '-' END AS gap, wh, val, count(*) AS n
  FROM c GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5;
\echo === K3b. PMUS reason (first 70 chars) x when x valued: [why, wh, val, n] x 30 per line ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
         probability IS NOT NULL AS has_p
    FROM external_valuations
   WHERE decided_at > now() - interval '24 hours'
     AND us_market_slug IS NOT NULL
   ORDER BY us_market_slug, decided_at DESC, id DESC),
k AS (
  SELECT left(coalesce(g.coverage_state, '?') || ':'
              || coalesce(g.coverage_why, ''), 90) AS why,
         CASE WHEN g.event_start IS NULL THEN 'NO_START'
              WHEN g.event_start < now() - interval '6 hours' THEN 'STARTED_GT_6H'
              WHEN g.event_start <= now() + interval '24 hours' THEN 'IN_HORIZON'
              WHEN g.event_start <= now() + interval '48 hours' THEN 'H24_48'
              WHEN g.event_start <= now() + interval '7 days' THEN 'D2_7'
              ELSE 'GT_7D' END AS wh,
         CASE WHEN v.slug IS NULL THEN 'NOT_VALUED'
              WHEN v.has_p THEN 'VALUED_WITH_P' ELSE 'VALUED_NO_P' END AS val,
         count(*) AS n
    FROM market_plane_registry g LEFT JOIN v ON v.slug = g.contract_id
   WHERE g.active AND g.venue = 'POLYMARKET_US' GROUP BY 1, 2, 3),
b AS (SELECT k.*, (row_number() OVER (ORDER BY why, wh, val) - 1) / 30 AS grp
        FROM k)
SELECT json_agg(json_build_array(why, wh, val, n) ORDER BY why, wh, val)::text
  FROM b GROUP BY grp ORDER BY grp;
\echo === K4. valuation reach (24 h): valued slugs and their registry state ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
         probability IS NOT NULL AS has_p, market, sport_family, period,
         record_purpose
    FROM external_valuations
   WHERE decided_at > now() - interval '24 hours'
     AND us_market_slug IS NOT NULL
   ORDER BY us_market_slug, decided_at DESC, id DESC)
SELECT coalesce(v.market, '?') AS market, coalesce(v.sport_family, '?') AS sport,
       v.has_p, v.record_purpose,
       coalesce(g.coverage_state, CASE WHEN g.contract_id IS NULL
                THEN '<not in registry>' ELSE '<inactive>' END) AS state,
       count(*) AS n,
       count(*) FILTER (WHERE g.active) AS active_n
  FROM v LEFT JOIN market_plane_registry g ON g.contract_id = v.slug
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 120;
\echo === K4b. valued-with-p active contracts: settlement state x why(110) ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
         probability IS NOT NULL AS has_p
    FROM external_valuations
   WHERE decided_at > now() - interval '24 hours'
     AND us_market_slug IS NOT NULL
   ORDER BY us_market_slug, decided_at DESC, id DESC)
SELECT g.coverage_state, g.settlement_state, g.settlement_basis,
       left(coalesce(g.settlement_why, '<null>'), 110) AS why, count(*) AS n
  FROM v JOIN market_plane_registry g ON g.contract_id = v.slug AND g.active
 WHERE v.has_p
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 80;
\echo === K5. in-horizon never-valued PMUS contracts: did their EVENT reach the candidate pipeline in 24 h (latest row per event), by sport x market-type family ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT us_market_slug AS slug FROM external_valuations
   WHERE decided_at > now() - interval '24 hours'
     AND us_market_slug IS NOT NULL AND probability IS NOT NULL),
o AS MATERIALIZED (
  SELECT DISTINCT ON (ev) ev, outcome, stage, first_refusal
    FROM (SELECT substring(regexp_replace(us_market_slug,
                   '^(atc|aec|asc|tsc|astatc|cpc)-', '')
                   from '^(.*?-[0-9]{4}-[0-9]{2}-[0-9]{2})') AS ev,
                 outcome, stage, first_refusal, cycle_at
            FROM ext_candidate_outcomes
           WHERE cycle_at > now() - interval '24 hours'
             AND us_market_slug IS NOT NULL) x
   WHERE ev IS NOT NULL
   ORDER BY ev, cycle_at DESC),
h AS MATERIALIZED (
  SELECT g.contract_id, g.event_id, coalesce(g.sport, '?') AS sport,
         CASE WHEN g.market_type IS NULL THEN '<null>'
              WHEN g.market_type = 'futures' THEN 'futures'
              WHEN g.market_type ~ '(^|_)player_' THEN 'player_prop'
              WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'winner'
              WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'spread'
              WHEN g.market_type ~ '_total' THEN 'total'
              ELSE 'other' END AS fam,
         (g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]')
           AS seg
    FROM market_plane_registry g
   WHERE g.active AND g.venue = 'POLYMARKET_US'
     AND g.event_start BETWEEN now() - interval '6 hours'
                           AND now() + interval '24 hours'
     AND NOT EXISTS (SELECT 1 FROM v WHERE v.slug = g.contract_id))
SELECT h.sport, h.fam, h.seg AS period_seg,
       coalesce(o.outcome, '<event not a candidate in 24 h>') AS event_outcome,
       coalesce(o.first_refusal, '-') AS first_refusal, count(*) AS n
  FROM h LEFT JOIN o ON o.ev = h.event_id
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 120;
\echo === K5b. candidate events in 24 h by outcome x first refusal (event level) ===
WITH o AS MATERIALIZED (
  SELECT DISTINCT ON (coalesce(provider_event_id, us_market_slug))
         sport_key, outcome, first_refusal, us_market_slug
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   ORDER BY coalesce(provider_event_id, us_market_slug), cycle_at DESC)
SELECT outcome, coalesce(first_refusal, '-') AS first_refusal, count(*) AS events,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS with_venue_slug
  FROM o GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 60;
\echo === K6. ontology gaps: [venue, why(80), first slug code, second slug code, market_type, n] x 30 per line ===
WITH k AS (
  SELECT venue, left(coverage_why, 80) AS why,
         lower(split_part(coalesce(event_id, ''), '-', 1)) AS c1,
         lower(split_part(coalesce(event_id, ''), '-', 2)) AS c2,
         coalesce(market_type, '<null>') AS mt, count(*) AS n
    FROM market_plane_registry
   WHERE active AND venue = 'POLYMARKET_US'
     AND coverage_state = 'CODE_CONTROLLED_GAP'
     AND coverage_why LIKE 'ONTOLOGY_GAPS%'
   GROUP BY 1, 2, 3, 4, 5),
b AS (SELECT k.*, (row_number() OVER (ORDER BY n DESC, c1, c2, mt) - 1) / 30
                  AS grp FROM k)
SELECT json_agg(json_build_array(venue, why, c1, c2, mt, n)
                ORDER BY n DESC, c1, c2, mt)::text
  FROM b GROUP BY grp ORDER BY grp;
\echo === K7. rules rows held: venue x row x published x parse status ===
SELECT g.venue, (r.contract_id IS NOT NULL) AS has_row, r.rules_published,
       r.parse_status, count(*) AS n
  FROM market_plane_registry g
  LEFT JOIN market_plane_rules r USING (contract_id)
 WHERE g.active GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;
\echo === K8. PMUS never-attested mapped contracts: [market_type, why(100), n] x 30 per line ===
WITH k AS (
  SELECT coalesce(market_type, '<null>') AS mt,
         left(coalesce(coverage_state, '?') || ':'
              || coalesce(coverage_why, ''), 100) AS why, count(*) AS n
    FROM market_plane_registry
   WHERE active AND venue = 'POLYMARKET_US'
     AND NOT (coverage_state = 'CODE_CONTROLLED_GAP'
              AND coverage_why LIKE 'ONTOLOGY_GAPS%')
   GROUP BY 1, 2),
b AS (SELECT k.*, (row_number() OVER (ORDER BY mt, n DESC, why) - 1) / 30
                  AS grp FROM k)
SELECT json_agg(json_build_array(mt, why, n) ORDER BY mt, n DESC, why)::text
  FROM b GROUP BY grp ORDER BY grp;
