-- RC6.2 lane p-coverage (FIX), read only. Why valued soccer / baseball
-- money lines whose fixture scope WAS established still read UNKNOWN
-- (rc6_p-coverage_fix_evidence F4: UNL rows with the UEFA source and
-- LEAGUE_OR_GROUP_STAGE read UNKNOWN 27 times, INCOMPATIBLE 5), and which
-- competitions the generic provider key 'pinnapi_soccer' hid:
--   G1  soccer h2h valuations (14 d) refused for the generic key, by the
--       venue league code of the contract's own event (us_premap)
--   G2  h2h valuations (14 d) with an established scope: verdict x quote
--       context x its refusal / why x the provider's stream label on the
--       reference input x book-side-absent refusals
--   G3  one full comparison of each (sport, verdict) from G2, keys trimmed
-- No write, no secret.
\echo === G1. generic-key soccer refusals by venue league code ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.decided_at
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family = 'soccer'
     AND e.settlement_comparison -> 'fixture_acquisition' ->> 'refusal'
         LIKE '%:pinnapi_soccer'
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC),
p AS MATERIALIZED (
  SELECT DISTINCT ON (market_slug) market_slug, event_slug, event_title
    FROM us_premap WHERE market_slug IN (SELECT slug FROM v)
   ORDER BY market_slug, updated_at DESC)
SELECT coalesce(split_part(p.event_slug, '-', 1), '<no premap row>') AS code,
       count(*) AS n, max(v.decided_at)::date::text AS newest,
       max(p.event_slug) AS sample_event, max(left(p.event_title, 70)) AS title
  FROM v LEFT JOIN p ON p.market_slug = v.slug
 GROUP BY 1 ORDER BY 2 DESC LIMIT 80;
\echo === G2. established-scope h2h valuations (14 d): verdict x context x stream label x book-side refusals ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.settlement_comparison AS s
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family IN ('soccer', 'baseball')
     AND e.settlement_comparison ->> 'scope_phase' IS NOT NULL
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT sport_family,
       coalesce(s ->> 'verdict', s ->> 'compatibility', '-') AS verdict,
       left(coalesce(s ->> 'quote_context', '-'), 40) AS ctx,
       left(coalesce(s ->> 'quote_context_why', '-'), 110) AS ctx_why,
       coalesce(s -> 'reference_input' ->> 'provider', '-') AS provider,
       coalesce(s -> 'reference_input' ->> 'stream', '-') AS stream,
       coalesce(s ->> 'fixture_play_has_begun', '-') AS begun,
       left(coalesce(s ->> 'book_side_absent_refusals',
                     s -> 'compatibility' ->> 'book_side_absent_refusals',
                     '-'), 90) AS absent,
       count(*) AS n
  FROM v GROUP BY 1, 2, 3, 4, 5, 6, 7, 8 ORDER BY 1, 9 DESC LIMIT 60;
\echo === G3. one comparison per (sport, verdict), scalar keys only ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.sport_family,
                      coalesce(e.settlement_comparison ->> 'verdict', '-'))
         e.us_market_slug AS slug, e.sport_family, e.decided_at,
         e.settlement_comparison AS s
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family IN ('soccer', 'baseball')
     AND e.settlement_comparison ->> 'scope_phase' IS NOT NULL
   ORDER BY e.sport_family,
            coalesce(e.settlement_comparison ->> 'verdict', '-'),
            e.decided_at DESC)
SELECT v.sport_family, v.slug, v.decided_at, k.key,
       left(k.value::text, 220) AS value
  FROM v, jsonb_each(v.s) k
 WHERE k.key NOT IN ('venue_rules_text', 'per_condition')
 ORDER BY 1, 2, 4 LIMIT 200;
