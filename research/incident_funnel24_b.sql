-- P0 INCIDENT (coverage -> trade starvation), FULL FUNNEL RECEIPT, PART B (read-only).
-- Decision / order / fill / agent side, latest 24 h, plus the 7-day daily trend.
-- THE UNIQUE OPPORTUNITY = (us_market_slug, holding_side) on a paper decision (the
-- R30A unique-opportunity key without fixture/line/scope, which a market slug fixes);
-- every row beyond the first per opportunity is a RE-EVALUATION and is counted apart.
-- League = venue league token split_part(us_market_slug, '-', 2).
\echo '== B0 . paper sessions =='
SELECT session_id, account_id, status, started_at, stopped_at, left(coalesce(stopped_why,''), 80) AS stopped_why
  FROM paper_sessions ORDER BY started_at DESC LIMIT 4;

\echo '== B1 . DECISIONS 24 h per strategy / policy_version / league: rows, valuations, unique opportunities, ENTER =='
SELECT strategy, policy_version, split_part(coalesce(us_market_slug,'-'),'-',2) AS league_token,
       count(*) AS rows, count(DISTINCT valuation_id) AS valuations,
       count(DISTINCT us_market_slug) AS markets,
       count(DISTINCT (us_market_slug, holding_side)) AS opportunities,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enter_rows,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE verdict = 'ENTER') AS enter_opps,
       count(*) FILTER (WHERE holding_side IS NULL OR us_market_slug IS NULL) AS unkeyed_rows
  FROM paper_decisions WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 1,2,4 DESC;

\echo '== B2 . DECISIONS 24 h: first refusal (refusal column) per strategy / league -> rows, unique opportunities =='
SELECT strategy, split_part(coalesce(us_market_slug,'-'),'-',2) AS league_token,
       coalesce(refusal, 'ENTER') AS first_refusal, count(*) AS rows,
       count(DISTINCT (us_market_slug, holding_side)) AS opportunities
  FROM paper_decisions WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 1,2,5 DESC;

\echo '== B3 . DECISIONS 24 h: EVERY refusal code per strategy / league -> rows, unique opportunities =='
SELECT strategy, split_part(coalesce(us_market_slug,'-'),'-',2) AS league_token, r AS refusal,
       count(*) AS rows, count(DISTINCT (us_market_slug, holding_side)) AS opportunities
  FROM paper_decisions, unnest(refusals) r
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 1,2,5 DESC, 4 DESC;

\echo '== B4 . BINDING BLOCKER per (opportunity, strategy): the first refusal of that strategy''s LATEST evaluation =='
WITH l AS (
    SELECT DISTINCT ON (strategy, us_market_slug, holding_side) strategy, us_market_slug, holding_side,
           verdict, coalesce(refusal,'ENTER') AS binding
      FROM paper_decisions WHERE decided_at > now() - interval '24 hours' AND us_market_slug IS NOT NULL
     ORDER BY strategy, us_market_slug, holding_side, decided_at DESC),
ever AS (
    SELECT strategy, us_market_slug, holding_side, bool_or(verdict = 'ENTER') AS entered_ever
      FROM paper_decisions WHERE decided_at > now() - interval '24 hours' AND us_market_slug IS NOT NULL
     GROUP BY 1,2,3)
SELECT l.strategy, split_part(l.us_market_slug,'-',2) AS league_token, l.binding,
       count(*) AS opportunities, count(*) FILTER (WHERE ever.entered_ever) AS entered_at_some_point
  FROM l JOIN ever USING (strategy, us_market_slug, holding_side)
 GROUP BY 1,2,3 ORDER BY 1,2,4 DESC;

\echo '== B5 . DECISIONS 24 h: probability / economics / book recorded on the decision, per strategy / league =='
SELECT strategy, split_part(coalesce(us_market_slug,'-'),'-',2) AS league_token,
       count(*) AS rows,
       count(*) FILTER (WHERE p_pinnacle IS NOT NULL) AS rows_p_pinnacle,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE p_pinnacle IS NOT NULL) AS opps_p_pinnacle,
       count(*) FILTER (WHERE pinnacle->>'qualification' = 'FRESH') AS rows_pin_fresh,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE pinnacle->>'qualification' = 'FRESH') AS opps_pin_fresh,
       count(*) FILTER (WHERE book_obs_id IS NOT NULL OR book IS NOT NULL) AS rows_with_book,
       count(*) FILTER (WHERE jsonb_typeof(economics) = 'object') AS rows_economics,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE jsonb_typeof(economics) = 'object') AS opps_economics,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE economics->>'net_ev_positive' = 'true') AS opps_net_ev_pos,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE economics->>'clears_min_gross_edge' = 'true') AS opps_clears_gross,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float8)
           FILTER (WHERE pinnacle->>'age_s' ~ '^-?[0-9.]+(e-?[0-9]+)?$') AS pin_age_p50
  FROM paper_decisions WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2 ORDER BY 1,3 DESC;

\echo '== B5b . key shapes of pinnacle / economics jsonb per strategy (24 h) =='
SELECT strategy, 'pinnacle' AS col, coalesce(pinnacle->>'qualification','<none>') AS qualification,
       (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(
           CASE WHEN jsonb_typeof(pinnacle) = 'object' THEN pinnacle ELSE '{}'::jsonb END) k) AS keys,
       count(*) AS rows
  FROM paper_decisions WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3,4 ORDER BY 1, 5 DESC LIMIT 40;
SELECT strategy, 'economics' AS col,
       (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(
           CASE WHEN jsonb_typeof(economics) = 'object' THEN economics ELSE '{}'::jsonb END) k) AS keys,
       count(*) AS rows
  FROM paper_decisions WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 1, 4 DESC LIMIT 40;

\echo '== B6 . ENTRY ORDERS 24 h per strategy: created, states, acknowledged (order events), filled =='
SELECT po.strategy, split_part(po.us_market_slug,'-',2) AS league_token, po.state,
       count(*) AS orders, count(DISTINCT (po.us_market_slug, po.holding_side)) AS opportunities,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_order_events e WHERE e.order_id = po.order_id
                                          AND e.kind = 'ACKNOWLEDGED')) AS acknowledged,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_fills f WHERE f.order_id = po.order_id)) AS with_fill,
       string_agg(DISTINCT coalesce(po.terminal_reason,'-'), ' | ') AS terminal_reasons
  FROM paper_orders po
 WHERE po.role = 'ENTRY' AND po.created_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 1,2,4 DESC;
SELECT e.kind, count(*) AS events, count(DISTINCT e.order_id) AS orders
  FROM paper_order_events e JOIN paper_orders po ON po.order_id = e.order_id
 WHERE po.role = 'ENTRY' AND po.created_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT strategy, role, count(*) AS fills, count(DISTINCT order_id) AS orders,
       round(sum(qty)::numeric, 2) AS qty, round(sum(gross_usd)::numeric, 2) AS gross_usd
  FROM paper_fills WHERE filled_at > now() - interval '24 hours' GROUP BY 1,2 ORDER BY 3 DESC;

\echo '== B7 . EXECUTION INTENTS 24 h (actual-side mirror of paper decisions) and EXECMIRROR orders =='
SELECT strategy, actual_state, coalesce(actual_refusal,'-') AS actual_refusal, live_eligible,
       count(*) AS intents, count(DISTINCT us_market_slug) AS markets
  FROM execution_intents WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 40;
SELECT coalesce(strategy,'-') AS strategy, role, state, coalesce(exclusion,'-') AS exclusion,
       count(*) AS orders, count(*) FILTER (WHERE venue_order_id IS NOT NULL) AS sent_to_venue,
       count(*) FILTER (WHERE cum_qty > 0) AS with_fill
  FROM execmirror_orders WHERE created_at > now() - interval '24 hours'
 GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 40;

\echo '== B8 . PER AGENT: RECEIVED / REVIEWED / REJECTED / ENTER / ORDERED / FILLED (24 h) =='
-- RECEIVED = valuations of the entry experiment carrying a venue contract (what the
-- valuation hook hands every paper strategy); REVIEWED = distinct valuations that got
-- a decision of that strategy; ORDERED / FILLED = ENTRY orders / fills of those decisions.
WITH recv AS (
    SELECT count(*) AS valuations, count(DISTINCT us_market_slug) AS markets
      FROM external_valuations
     WHERE decided_at > now() - interval '24 hours' AND us_market_slug IS NOT NULL
       AND experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'),
d AS (
    SELECT strategy, decision_id, valuation_id, verdict, us_market_slug, holding_side
      FROM paper_decisions WHERE decided_at > now() - interval '24 hours')
SELECT d.strategy, (SELECT valuations FROM recv) AS received_valuations,
       (SELECT markets FROM recv) AS received_markets,
       count(DISTINCT d.valuation_id) AS reviewed_valuations,
       count(DISTINCT (d.us_market_slug, d.holding_side)) AS reviewed_opps,
       count(*) FILTER (WHERE d.verdict = 'REFUSE') AS rejected_rows,
       count(DISTINCT (d.us_market_slug, d.holding_side)) FILTER (WHERE d.verdict = 'REFUSE') AS rejected_opps,
       count(*) FILTER (WHERE d.verdict = 'ENTER') AS enter_rows,
       count(DISTINCT po.order_id) AS ordered,
       count(DISTINCT pf.order_id) AS filled_orders
  FROM d
  LEFT JOIN paper_orders po ON po.decision_id = d.decision_id AND po.role = 'ENTRY'
  LEFT JOIN paper_fills pf ON pf.order_id = po.order_id
 GROUP BY 1 ORDER BY 1;
SELECT strategy, via, outcome, coalesce(refusal,'-') AS refusal, count(*) AS attempts,
       count(DISTINCT valuation_id) AS valuations
  FROM paper_evaluation_attempts WHERE at > now() - interval '24 hours'
 GROUP BY 1,2,3,4 ORDER BY 1, 5 DESC LIMIT 60;
SELECT strategy, stage, outcome, left(coalesce(error,'-'), 80) AS error, count(*) AS n,
       count(DISTINCT valuation_id) AS valuations
  FROM paper_hook_failures WHERE recorded_at > now() - interval '24 hours'
 GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 30;

\echo '== B8b . EDDIE (execution estimates) 24 h, joined to the decision it estimated =='
SELECT coalesce(pd.strategy,'<no decision>') AS strategy, coalesce(pd.verdict,'-') AS verdict, e.recommendation,
       count(*) AS estimates, count(DISTINCT e.decision_id) AS decisions,
       count(*) FILTER (WHERE e.expected_executable_ev_usd > 0) AS ev_pos,
       count(*) FILTER (WHERE e.expected_executable_ev_usd IS NULL) AS ev_unmeasured
  FROM eddie_execution_estimates e LEFT JOIN paper_decisions pd ON pd.decision_id = e.decision_id
 WHERE e.estimated_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 30;
SELECT left(e.recommendation_reason, 100) AS reason, count(*) AS estimates
  FROM eddie_execution_estimates e WHERE e.estimated_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo '== B8c . KAREN challenges 24 h =='
SELECT target_agent, target_kind, detector, state, severity, blocked, count(*) AS challenges
  FROM karen_challenges WHERE challenged_at > now() - interval '24 hours'
 GROUP BY 1,2,3,4,5,6 ORDER BY 7 DESC LIMIT 30;

\echo '== B8d . ALLIE (Chief Allocator, intel_allocations SHADOW) 24 h =='
SELECT candidate_kind, (shadow_usd > 0) AS shadow_funded, coalesce(binding_constraint,'-') AS binding_constraint,
       count(*) AS rows, count(DISTINCT decision_id) AS decisions, count(DISTINCT run_id) AS runs
  FROM intel_allocations WHERE computed_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20;
SELECT component, status, count(*) AS runs, max(started_at) AS last_started, left(max(coalesce(error,'')), 120) AS error
  FROM intel_runs WHERE started_at > now() - interval '24 hours' GROUP BY 1,2 ORDER BY 1,2;

\echo '== B8e . XAVIER (positions) 24 h: reviews, handoffs, open groups =='
SELECT strategy, trigger, coalesce(recommendation,'-') AS recommendation, coalesce(refusal,'-') AS refusal,
       count(*) AS reviews, count(DISTINCT group_id) AS groups
  FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '24 hours'
 GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 30;
SELECT strategy, count(*) AS handoffs_24h, sum(CASE WHEN outstanding_qty > 0 THEN 1 ELSE 0 END) AS outstanding
  FROM paper_handoffs WHERE created_at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;

\echo '== B9 . 7-DAY TREND per UTC day: valuations, unique opportunities decided, EV-evaluated, positive-EV, ENTER, orders, fills =='
WITH d AS (
    SELECT date_trunc('day', decided_at) AS day, strategy, us_market_slug, holding_side, verdict, refusals,
           economics
      FROM paper_decisions WHERE decided_at > date_trunc('day', now()) - interval '7 days')
SELECT day, strategy, count(*) AS rows,
       count(DISTINCT (us_market_slug, holding_side)) AS opps_decided,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE jsonb_typeof(economics) = 'object') AS opps_ev_evaluated,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE economics->>'net_ev_positive' = 'true'
                                                               OR economics->>'clears_min_gross_edge' = 'true') AS opps_positive_ev,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE verdict = 'ENTER') AS opps_enter
  FROM d GROUP BY 1,2 ORDER BY 1,2;
SELECT date_trunc('day', decided_at) AS day, record_purpose, count(*) AS valuations,
       count(DISTINCT us_market_slug) AS markets, count(DISTINCT event_key) AS events,
       count(DISTINCT us_market_slug) FILTER (WHERE probability IS NOT NULL) AS markets_with_probability
  FROM external_valuations WHERE decided_at > date_trunc('day', now()) - interval '7 days'
 GROUP BY 1,2 ORDER BY 1,2;
SELECT date_trunc('day', created_at) AS day, strategy, count(*) AS entry_orders,
       count(*) FILTER (WHERE filled_qty > 0) AS filled_orders
  FROM paper_orders WHERE role = 'ENTRY' AND created_at > date_trunc('day', now()) - interval '7 days'
 GROUP BY 1,2 ORDER BY 1,2;
SELECT date_trunc('day', cycle_at) AS day, count(DISTINCT provider_event_id) AS provider_events,
       count(DISTINCT provider_event_id) FILTER (WHERE us_market_slug IS NOT NULL) AS mapped_events,
       count(DISTINCT sport_key) AS sport_keys, count(DISTINCT cycle_id) AS cycles
  FROM ext_candidate_outcomes WHERE cycle_at > date_trunc('day', now()) - interval '7 days'
 GROUP BY 1 ORDER BY 1;
