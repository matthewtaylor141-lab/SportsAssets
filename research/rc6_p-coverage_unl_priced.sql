-- RC6.2 lane p-coverage (FIX), read only. What the priced settlement
-- difference policy did with the soccer money lines whose scope WAS
-- established and whose verdict was INCOMPATIBLE (the state the lane's
-- PinnAPI context fix produces for the UNKNOWN ones), and what the plane
-- said about them:
--   P1  soccer h2h valuations (14 d, latest per slug, scope held): verdict x
--       latest paper decision's settlement-difference eligibility / refusal
--       / priced p x registry state now
--   P2  the active UEFA club-competition money lines listed now (ucl / uel /
--       uecl / unl / uwwcq / u21eq): events, contracts, earliest start,
--       valued in 24 h
-- No write, no secret.
\echo === P1. established-scope soccer valuations x priced settlement difference ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.settlement_comparison AS s, e.decided_at,
         e.probability IS NOT NULL AS has_p
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family = 'soccer'
     AND e.settlement_comparison ->> 'scope_phase' IS NOT NULL
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC),
p AS MATERIALIZED (
  SELECT DISTINCT ON (d.us_market_slug) d.us_market_slug AS slug,
         d.pinnacle -> 'settlement_difference_eligibility' AS el,
         d.pinnacle -> 'settlement_difference_policy' AS pol, d.verdict,
         d.refusal
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '14 days'
     AND d.us_market_slug IN (SELECT slug FROM v)
   ORDER BY d.us_market_slug, d.decided_at DESC)
SELECT coalesce(v.s ->> 'verdict', v.s ->> 'compatibility', '-') AS verdict,
       v.has_p,
       coalesce(p.el ->> 'eligible', '-') AS eligible,
       left(coalesce(p.el ->> 'refusal', '-'), 70) AS el_refusal,
       (p.pol ->> 'p') IS NOT NULL AS priced,
       coalesce(p.verdict, '-') AS decision, left(coalesce(p.refusal, '-'), 50)
         AS decision_refusal,
       coalesce(g.settlement_state, '<no row>') AS plane_state,
       count(*) AS n
  FROM v LEFT JOIN p ON p.slug = v.slug
  LEFT JOIN market_plane_registry g ON g.contract_id = v.slug
 GROUP BY 1, 2, 3, 4, 5, 6, 7, 8 ORDER BY 9 DESC LIMIT 40;
\echo === P2. active UEFA-organised money lines now ===
WITH r AS MATERIALIZED (
  SELECT contract_id, split_part(event_id, '-', 1) AS code, event_id,
         event_start
    FROM market_plane_registry
   WHERE active AND venue = 'POLYMARKET_US'
     AND market_type = 'soccer_team_full_time_winner'
     AND split_part(event_id, '-', 1) IN ('ucl', 'uel', 'uecl', 'unl',
                                          'uwwcq', 'u21eq')),
v AS MATERIALIZED (
  SELECT DISTINCT us_market_slug AS slug FROM external_valuations
   WHERE decided_at > now() - interval '24 hours'
     AND us_market_slug IN (SELECT contract_id FROM r))
SELECT r.code, count(DISTINCT r.event_id) AS events, count(*) AS contracts,
       min(r.event_start)::text AS earliest, max(r.event_start)::text AS latest,
       count(v.slug) AS valued_24h
  FROM r LEFT JOIN v ON v.slug = r.contract_id
 GROUP BY 1 ORDER BY 1;
