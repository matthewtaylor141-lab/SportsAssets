-- RC6.2 lane p-coverage (DIAGNOSE), read only. WHY THE VALUED MONEY LINES
-- STOP AT SETTLEMENT. Of the active contracts with a decision valuation and
-- a probability in the last 24 h (the coverage matrix's only fair-value
-- source), 248 soccer money lines read SETTLEMENT_VERDICT:UNKNOWN (census
-- run 37939676400 K4b) while football / basketball / hockey ones reach
-- SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED. This reads, per sport, what the
-- decision recorded about its own comparison: verdict, quote context, the
-- fixture scope (phase, game format) and how it was acquired, the per-
-- condition verdicts, and the priced settlement difference's eligibility on
-- the latest paper decision. No write, no secret.
\echo === M1. latest h2h valuation per active slug (24 h): sport x verdict x context x phase x format x scope source x has_p ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.settlement_comparison AS s,
         e.probability IS NOT NULL AS has_p
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '24 hours'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT v.sport_family,
       coalesce(v.s ->> 'verdict', v.s ->> 'status', v.s ->> 'compatibility')
         AS verdict,
       coalesce(v.s ->> 'quote_context', '-') AS ctx,
       coalesce(v.s ->> 'scope_phase', '-') AS phase,
       coalesce(v.s ->> 'scope_game_format', '-') AS fmt,
       coalesce(v.s ->> 'fixture_source', '-') AS src,
       v.has_p, count(*) AS n,
       count(*) FILTER (WHERE g.active) AS active_n
  FROM v LEFT JOIN market_plane_registry g ON g.contract_id = v.slug
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 8 DESC LIMIT 60;
\echo === M2. the same, soccer and one football row each: acquisition / context why / book-side-absent why (first 160 chars) ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.settlement_comparison AS s
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '24 hours'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.probability IS NOT NULL
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT sport_family,
       left(coalesce(s ->> 'fixture_acquisition', '-'), 160) AS acquisition,
       left(coalesce(s ->> 'quote_context_why', '-'), 120) AS ctx_why,
       left(coalesce(s ->> 'why_book_side_absent', s ->> 'refusal', '-'), 160)
         AS book_side,
       count(*) AS n
  FROM v GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC LIMIT 60;
\echo === M3. per-condition verdicts of the latest h2h valuation with p, by sport ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.settlement_comparison AS s
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '24 hours'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.probability IS NOT NULL
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT v.sport_family, pc.key AS condition,
       coalesce(pc.value ->> 'verdict', '?') AS verdict,
       left(coalesce(pc.value ->> 'book_payout', '-'), 40) AS book,
       left(coalesce(pc.value ->> 'venue_payout', '-'), 40) AS venue,
       count(*) AS n
  FROM v, jsonb_each(CASE WHEN jsonb_typeof(v.s -> 'per_condition') = 'object'
                          THEN v.s -> 'per_condition' ELSE '{}'::jsonb END) pc
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 6 DESC LIMIT 80;
\echo === M4. top-level keys of the recorded comparison, by sport (structure) ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.settlement_comparison AS s
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '24 hours'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.probability IS NOT NULL
     AND jsonb_typeof(e.settlement_comparison) = 'object'
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT sport_family, k, count(*) AS n
  FROM v, jsonb_object_keys(v.s) k GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
 LIMIT 120;
\echo === M5. settlement refusals on those valuations, by sport ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.refusals
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '24 hours'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.probability IS NOT NULL
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT sport_family, r, count(*) AS n
  FROM v, unnest(coalesce(v.refusals, ARRAY[]::text[])) r
 WHERE r LIKE 'SETTLEMENT%' OR r LIKE '%SCOPE%' OR r LIKE '%PHASE%'
    OR r LIKE '%FORMAT%' OR r LIKE '%CONTEXT%'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 60;
\echo === M6. latest paper decision, priced settlement difference on those slugs, by sport ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT e.us_market_slug AS slug, e.sport_family
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '24 hours'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.probability IS NOT NULL),
p AS MATERIALIZED (
  SELECT DISTINCT ON (d.us_market_slug) d.us_market_slug AS slug,
         d.pinnacle -> 'settlement_difference_eligibility' AS el,
         d.pinnacle ? 'settlement_difference_policy' AS has_policy
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '24 hours'
     AND d.us_market_slug IN (SELECT slug FROM v)
   ORDER BY d.us_market_slug, d.decided_at DESC)
SELECT v.sport_family, (p.slug IS NOT NULL) AS has_paper_decision,
       coalesce(p.has_policy, false) AS has_policy,
       coalesce(p.el ->> 'eligible', '-') AS eligible,
       left(coalesce(p.el ->> 'refusal', '-'), 90) AS refusal, count(*) AS n
  FROM v LEFT JOIN p ON p.slug = v.slug
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 6 DESC LIMIT 60;
