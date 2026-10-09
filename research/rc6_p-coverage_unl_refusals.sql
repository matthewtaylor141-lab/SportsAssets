-- RC6.2 lane p-coverage (FIX), read only. The full refusal list of every
-- paper decision (14 d) on a soccer money line whose recorded comparison was
-- INCOMPATIBLE with the fixture scope held: which code of the priced
-- settlement-difference policy refused it, per strategy.
-- No write, no secret.
\echo === Q1. INCOMPATIBLE established-scope soccer: strategy x refusals ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT e.id
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '14 days'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family = 'soccer'
     AND e.settlement_comparison ->> 'scope_phase' IS NOT NULL
     AND coalesce(e.settlement_comparison ->> 'verdict',
                  e.settlement_comparison ->> 'compatibility')
         = 'INCOMPATIBLE')
SELECT d.strategy, d.verdict,
       left(array_to_string(coalesce(d.refusals, ARRAY[]::text[]), ' | '),
            300) AS refusals,
       count(*) AS n
  FROM paper_decisions d JOIN v ON v.id = d.valuation_id
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;
