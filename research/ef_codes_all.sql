-- ECONOMIC FUNNEL (owner directive section 6), STEP 1c: EVERY DECISION CODE
-- EVER WRITTEN, PER STRATEGY, FROZEN POLICY AND COHORT (historical = before
-- migration 309 applied_at; forward = at or after it).
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
-- The codes are classified locally by the RC4 production code (9b94ef5c),
-- never by hand, and the mapping is carried into ef_funnel_all.sql.

\echo '== 1c.1 paper_decisions, all time: every code in refusals[] per strategy x policy x cohort'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT d.strategy, d.policy_version,
       CASE WHEN d.decided_at >= c.t THEN 'FORWARD_BIND' ELSE 'HISTORICAL' END AS cohort,
       x.code, count(*) AS decisions, count(DISTINCT d.fixture) AS fixtures
  FROM paper_decisions d CROSS JOIN c
  CROSS JOIN LATERAL unnest(d.refusals) AS x(code)
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, 3, decisions DESC;

\echo '== 1c.2 paper_decisions, all time: verdict totals per strategy x policy x cohort'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT d.strategy, d.policy_version,
       CASE WHEN d.decided_at >= c.t THEN 'FORWARD_BIND' ELSE 'HISTORICAL' END AS cohort,
       d.verdict, count(*) AS decisions, count(DISTINCT d.fixture) AS fixtures,
       count(*) FILTER (WHERE d.fixture IS NULL) AS without_fixture,
       count(*) FILTER (WHERE d.verdict = 'REFUSE' AND cardinality(d.refusals) = 0) AS refuse_without_codes,
       min(d.decided_at) AS first_at, max(d.decided_at) AS last_at
  FROM paper_decisions d CROSS JOIN c
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, 3, 4;
