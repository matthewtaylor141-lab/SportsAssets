-- READ-ONLY. RC6.3c independent evidence lens, companion to
-- rc63c_ser_measure_cost_on_production.sql (PAPER_SQL measured 10.9-11.2 s
-- on production against the component's 2.0 s bound). What the canonical
-- decision actually recorded for the settlement_exception_risk component on
-- production: every canonical intent, by status and reason, by month, and
-- the newest ten intents verbatim on that one key.

\echo == 1. canonical intents: component status by month
SELECT date_trunc('month', created_at)::date AS month,
       evidence->'settlement_exception_risk'->>'status' AS status,
       left(evidence->'settlement_exception_risk'->>'why', 70) AS why,
       count(*) AS intents, min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_decision_intents
 GROUP BY 1, 2, 3
 ORDER BY 1, intents DESC;

\echo == 2. the newest ten intents, the component as recorded
SELECT created_at, strategy, left(us_market_slug, 40) AS slug,
       left((evidence->'settlement_exception_risk')::text, 160) AS component
  FROM canonical_decision_intents
 ORDER BY created_at DESC
 LIMIT 10;

\echo == 3. intents that carry no settlement_exception_risk key at all
SELECT count(*) AS intents_without_the_key,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_decision_intents
 WHERE NOT (evidence ? 'settlement_exception_risk');

\echo == 4. paper decisions in the last 7 days, by verdict and day (the volume the component runs for)
SELECT date_trunc('day', decided_at)::date AS day, verdict, count(*) AS decisions
  FROM paper_decisions
 WHERE decided_at > now() - interval '7 days'
 GROUP BY 1, 2
 ORDER BY 1, 2;
