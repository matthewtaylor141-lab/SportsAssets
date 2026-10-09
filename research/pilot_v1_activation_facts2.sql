-- READ-ONLY. SMALL LIVE PILOT V1 activation facts, part 2. Every statement
-- is a SELECT. Names and boolean relations only for credentials (which env
-- NAMES hold an equal identifier or secret: never a value).
--   H1 API census: per pair, which other names hold an EQUAL key id / secret
--   H2 SMALL_LIVE shadow proposals all time by kind, state, exclusion, with
--      the would-be order cost (collateral basis) bucketed against $1 / $25
--   H3 decision-side would-be live quantities at scale 1000 (all time)
--   H4 the live-eligible strategy: newest decision and newest ENTER

\echo H1 API PMUS CENSUS EQUALITY RELATIONS (names only) from the newest CREDENTIAL_CLASSES receipt
SELECT p.key AS pair, p.value->>'shape' AS shape, p.value->'key_id_equals' AS key_id_equals,
       p.value->'secret_equals' AS secret_equals
  FROM (SELECT evidence FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
         ORDER BY computed_at DESC LIMIT 1) r,
       jsonb_each(coalesce(r.evidence->'pmus_census'->'sportsassets-api'->'pairs', '{}'::jsonb)) p
 ORDER BY 1;
SELECT i.key AS identifier, i.value->>'shape' AS shape, i.value->'equals' AS equals
  FROM (SELECT evidence FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
         ORDER BY computed_at DESC LIMIT 1) r,
       jsonb_each(coalesce(r.evidence->'pmus_census'->'sportsassets-api'->'identifiers', '{}'::jsonb)) i
 ORDER BY 1;

\echo H2 SMALL_LIVE SHADOW ROWS ALL TIME by kind, state, exclusion, plan exclusion
SELECT intent_kind, state, coalesce(exclusion, '-') AS exclusion, coalesce(refs->>'plan_exclusion', '-') AS plan_exclusion,
       count(*) AS rows, min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_intent_executions WHERE adapter = 'SMALL_LIVE'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 25;

\echo H2b DECISION proposals: would-be order cost on the collateral basis (long pays wire, short pays 1 - wire), bucketed
WITH d AS (
  SELECT state, coalesce(exclusion, '-') AS exclusion,
         (requested->>'qty')::numeric AS live_qty,
         (requested->>'wire_price')::numeric AS wire,
         requested->>'order_intent' AS order_intent
    FROM canonical_intent_executions
   WHERE adapter = 'SMALL_LIVE' AND intent_kind = 'DECISION' AND requested IS NOT NULL
     AND requested ? 'qty' AND requested ? 'wire_price'),
c AS (
  SELECT *, live_qty * CASE WHEN order_intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire ELSE wire END AS cost
    FROM d)
SELECT state, exclusion, count(*) AS rows,
       count(*) FILTER (WHERE live_qty = 0) AS zero_contracts,
       count(*) FILTER (WHERE live_qty >= 1 AND cost <= 1) AS cost_le_1usd,
       count(*) FILTER (WHERE cost > 1 AND cost <= 25) AS cost_1_to_25usd,
       count(*) FILTER (WHERE cost > 25) AS cost_gt_25usd,
       min(live_qty) AS min_qty, percentile_cont(0.5) WITHIN GROUP (ORDER BY live_qty) AS p50_qty, max(live_qty) AS max_qty,
       round(min(cost), 4) AS min_cost, round((percentile_cont(0.5) WITHIN GROUP (ORDER BY cost))::numeric, 4) AS p50_cost,
       round(max(cost), 4) AS max_cost
  FROM c GROUP BY 1, 2 ORDER BY 3 DESC;

\echo H2c DECISION proposals with exactly one contract: cost distribution (what a 1-contract order costs)
WITH c AS (
  SELECT (requested->>'qty')::numeric AS live_qty,
         (requested->>'qty')::numeric * CASE WHEN requested->>'order_intent' = 'ORDER_INTENT_BUY_SHORT'
                                              THEN 1 - (requested->>'wire_price')::numeric
                                              ELSE (requested->>'wire_price')::numeric END AS cost
    FROM canonical_intent_executions
   WHERE adapter = 'SMALL_LIVE' AND intent_kind = 'DECISION'
     AND requested ? 'qty' AND requested ? 'wire_price')
SELECT live_qty, count(*) AS rows, round(min(cost), 4) AS min_cost, round(avg(cost), 4) AS avg_cost, round(max(cost), 4) AS max_cost
  FROM c WHERE live_qty BETWEEN 0 AND 5 GROUP BY 1 ORDER BY 1;

\echo H3 EXECUTION INTENTS ALL TIME: would-be live qty at the stored scale, by live eligibility
SELECT live_eligible, live_scale, count(*) AS intents,
       count(*) FILTER (WHERE live_qty = 0) AS below_venue_minimum,
       count(*) FILTER (WHERE live_qty >= 1 AND live_qty * wire_price <= 1) AS cost_le_1usd_long_basis,
       min(live_qty) AS min_live_qty, max(live_qty) AS max_live_qty,
       round(avg(paper_target_qty)::numeric, 1) AS avg_paper_qty
  FROM execution_intents GROUP BY 1, 2 ORDER BY 1, 2;

\echo H4 LIVE-ELIGIBLE STRATEGY (PINNACLE_COMPLETED_GAME_PAPER): newest execution intent and newest canonical decision intent
SELECT strategy, policy_version, count(*) AS intents, max(created_at) AS newest
  FROM execution_intents WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER' GROUP BY 1, 2 ORDER BY 4 DESC;
SELECT strategy, strategy_version, sleeve, count(*) AS intents, max(created_at) AS newest
  FROM canonical_decision_intents GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 10;
