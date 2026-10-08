-- ECONOMIC FUNNEL (owner directive section 6), STEP 1: EVERY CODE THAT
-- STOPPED A FORWARD CANDIDATE, PER STRATEGY.
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
--
-- THE WINDOW is the profitability bind's forward cohort: from migration 309's
-- applied_at (2026-10-06 17:38:06.220476Z, the cutover the production
-- scoreboard itself uses, bettor_paper_profitability_stack.bind_cutover) to
-- the database's now(). Nothing before it is mixed in.
--
-- WHY CODES FIRST. The stage and the SOFTWARE / ECONOMIC / EXTERNAL class of
-- a code live in Python (refusal_taxonomy_table, bettor_external_shadow.
-- EVALUABILITY_OF, coverage_first_loss.chain_stage). This step lists every
-- distinct code with its counts; the codes are then classified locally by the
-- RC4 production code (9b94ef5c) itself -- never by hand -- and the mapping
-- is carried into step 2 (ef_funnel_forward.sql) as a literal table.

\echo '== 1.1 paper_decisions since 309: every code in refusals[], per strategy'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT d.strategy, x.code,
       count(*)                       AS decisions,
       count(DISTINCT d.fixture)      AS fixtures,
       min(x.pos)                     AS min_position,
       count(*) FILTER (WHERE x.pos = 1) AS as_first_code
  FROM paper_decisions d CROSS JOIN c
  CROSS JOIN LATERAL unnest(d.refusals) WITH ORDINALITY AS x(code, pos)
 WHERE d.decided_at >= c.t
 GROUP BY 1, 2
 ORDER BY 1, decisions DESC;

\echo '== 1.2 paper_decisions since 309: the first refusal and the verdict, per strategy'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT d.strategy, d.verdict, coalesce(d.refusal, '(none)') AS refusal,
       count(*) AS decisions, count(DISTINCT d.fixture) AS fixtures,
       count(DISTINCT d.us_market_slug || ':' || coalesce(d.holding_side, '?'))
           AS contract_sides,
       count(*) FILTER (WHERE cardinality(d.refusals) = 0) AS empty_refusals
  FROM paper_decisions d CROSS JOIN c
 WHERE d.decided_at >= c.t
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, decisions DESC;

\echo '== 1.3 external_valuations since 309: every code in refusals[]'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT v.record_purpose, x.code, count(*) AS valuations,
       count(DISTINCT v.event_key) AS event_keys,
       count(DISTINCT v.us_market_slug) AS venue_contracts
  FROM external_valuations v CROSS JOIN c
  CROSS JOIN LATERAL unnest(v.refusals) AS x(code)
 WHERE v.decided_at >= c.t
 GROUP BY 1, 2
 ORDER BY 1, valuations DESC
 LIMIT 200;

\echo '== 1.4 external_valuations since 309: totals by purpose x admissible'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT v.record_purpose, v.admissible, v.experiment_id,
       count(*) AS valuations,
       count(DISTINCT v.event_key) AS event_keys,
       count(DISTINCT v.us_market_slug) AS venue_contracts,
       count(*) FILTER (WHERE cardinality(v.refusals) = 0) AS no_refusal,
       min(v.decided_at) AS first_at, max(v.decided_at) AS last_at
  FROM external_valuations v CROSS JOIN c
 WHERE v.decided_at >= c.t
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo '== 1.5 ext_candidate_outcomes since 309: outcome x stage x first_refusal (rows and provider events)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT o.outcome, coalesce(o.stage, '(null)') AS stage,
       coalesce(o.first_refusal, '(none)') AS first_refusal,
       count(*) AS rows,
       count(DISTINCT (o.sport_key, o.provider_event_id)) AS provider_events,
       count(DISTINCT o.us_market_slug) AS venue_contracts
  FROM ext_candidate_outcomes o CROSS JOIN c
 WHERE o.cycle_at >= c.t AND o.provider_event_id IS NOT NULL
 GROUP BY 1, 2, 3
 ORDER BY provider_events DESC
 LIMIT 250;

\echo '== 1.6 ext_candidate_outcomes since 309: totals'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT count(*) AS rows,
       count(DISTINCT (o.sport_key, o.provider_event_id)) AS provider_events,
       count(DISTINCT (o.sport_key, o.provider_event_id)) FILTER (
           WHERE o.us_market_slug IS NOT NULL) AS with_venue_contract,
       count(DISTINCT (o.sport_key, o.provider_event_id)) FILTER (
           WHERE o.outcome IN ('ADMITTED', 'ALREADY_RECORDED')) AS admitted,
       count(DISTINCT o.sport_key) AS sport_keys,
       min(o.cycle_at) AS first_at, max(o.cycle_at) AS last_at
  FROM ext_candidate_outcomes o CROSS JOIN c
 WHERE o.cycle_at >= c.t AND o.provider_event_id IS NOT NULL;

\echo '== 1.7 paper_entry_refusal_census since 309: strategy x stage x refusal'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT r.strategy, r.stage, r.refusal, count(*) AS rows,
       count(DISTINCT r.fixture) AS fixtures,
       count(DISTINCT r.us_market_slug || ':' || coalesce(r.holding_side, '?'))
           AS contract_sides,
       count(*) FILTER (WHERE r.total_executable_ev_usd > 0) AS ev_positive_rows
  FROM paper_entry_refusal_census r CROSS JOIN c
 WHERE r.refused_at >= c.t
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, rows DESC;

\echo '== 1.8 paper_profitability_evaluations since 309: final refusal x bind refusal'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT e.strategy, e.stage, e.refusal,
       coalesce(e.detail ->> 'bind_refusal', '(bind passed)') AS bind_refusal,
       count(*) AS rows, count(DISTINCT e.fixture) AS fixtures,
       count(DISTINCT (e.us_market_slug, e.holding_side)) AS contract_sides,
       count(*) FILTER (WHERE (e.detail -> 'bind' ->> 'calibration_status')
                              = 'MEASURED') AS cal_measured,
       count(*) FILTER (WHERE (e.detail -> 'bind' ->> 'calibration_status')
                              = 'INSUFFICIENT') AS cal_insufficient,
       count(*) FILTER (WHERE (e.detail -> 'bind' ->> 'calibration_status')
                              = 'NO_DATA') AS cal_no_data
  FROM paper_profitability_evaluations e CROSS JOIN c
 WHERE e.evaluated_at >= c.t
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, rows DESC;
