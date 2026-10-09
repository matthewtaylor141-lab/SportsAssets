-- FINAL DELIVERY CONTRACT items 3 and 4 readback (read-only audit).
-- Item 3: linked 1:1000 PAPER / SMALL LIVE legs (execution_intents, canonical
-- intents and adapter executions, parity ledger, would-be live sizing).
-- Item 4: the agent floor (agent_status, agent_runs, each agent newest real
-- record, open work, agent loops and service beats).
-- SELECT only. No writes, no credentials, no values beyond counts and ids.

\echo L1 EXECUTION INTENTS 24 h by strategy, state, refusal: paper decision link, paper order link, live eligibility
SELECT ei.strategy, ei.actual_state, coalesce(ei.actual_refusal, '-') AS refusal, count(*) AS intents,
       count(*) FILTER (WHERE ei.live_eligible) AS live_eligible,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_decisions d WHERE d.decision_id = ei.decision_id)) AS with_paper_decision,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = ei.decision_id)) AS with_paper_order,
       count(*) FILTER (WHERE ei.live_qty IS NOT NULL) AS live_qty_recorded,
       min(ei.created_at) AS first_at, max(ei.created_at) AS last_at
  FROM execution_intents ei
 WHERE ei.created_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo L2 WOULD-BE LIVE SIZE of the 24 h intents at scale 1000 (half-even whole contracts, the code rule) and the venue fractional view
WITH ei AS (
  SELECT paper_target_qty AS q, wire_price AS p, order_intent,
         paper_target_qty / 1000.0 AS x,
         CASE WHEN order_intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire_price ELSE wire_price END AS cpc
    FROM execution_intents WHERE created_at >= now() - interval '24 hours'),
s AS (
  SELECT q, p, x, cpc,
         CASE WHEN x - floor(x) = 0.5 THEN floor(x) + (floor(x)::bigint % 2) ELSE round(x) END AS live_whole
    FROM ei)
SELECT count(*) AS intents,
       count(*) FILTER (WHERE live_whole = 0) AS below_one_whole_contract,
       count(*) FILTER (WHERE live_whole = 1) AS one_contract,
       count(*) FILTER (WHERE live_whole BETWEEN 2 AND 5) AS two_to_five,
       count(*) FILTER (WHERE live_whole > 5) AS over_five,
       count(*) FILTER (WHERE x >= 0.01) AS x_at_least_0_01,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY q)::numeric, 2) AS paper_qty_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY q)::numeric, 2) AS paper_qty_p90,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY q * cpc)::numeric, 2) AS paper_usd_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY q * cpc)::numeric, 2) AS paper_usd_p90,
       round(sum(q * cpc)::numeric, 2) AS paper_usd_sum,
       round(sum(live_whole * cpc)::numeric, 4) AS live_whole_usd_sum,
       round((sum(live_whole * cpc) / nullif(sum(q * cpc), 0) * 1000)::numeric, 4) AS live_to_paper_ratio_x1000,
       round(avg(abs(live_whole - x) / nullif(x, 0)) FILTER (WHERE live_whole > 0)::numeric, 4) AS mean_abs_rounding_error_when_sized
  FROM s;

\echo L3 NEWEST 12 INTENTS: decision id, contract, direction, timestamps, paper size, would-be live size, paper order and fill, actual state
WITH ei AS (
  SELECT * FROM execution_intents ORDER BY created_at DESC LIMIT 12)
SELECT ei.intent_id, ei.decision_id, ei.strategy, ei.us_market_slug, ei.order_intent, ei.holding_side,
       ei.decided_at, ei.created_at, ei.paper_target_qty, ei.wire_price,
       round(ei.paper_target_qty * CASE WHEN ei.order_intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - ei.wire_price ELSE ei.wire_price END, 2) AS paper_usd,
       round(ei.paper_target_qty / 1000.0, 4) AS live_exact,
       ei.live_qty AS live_qty_recorded, ei.actual_state, ei.actual_refusal,
       (SELECT o.order_id FROM paper_orders o WHERE o.decision_id = ei.decision_id LIMIT 1) AS paper_order_id,
       (SELECT o.state FROM paper_orders o WHERE o.decision_id = ei.decision_id LIMIT 1) AS paper_order_state,
       (SELECT d.verdict || coalesce(':' || d.refusal, '') FROM paper_decisions d WHERE d.decision_id = ei.decision_id) AS paper_verdict
  FROM ei ORDER BY ei.created_at DESC;

\echo L4 EXECUTION INTENTS all time: live eligible, refusal codes of REFUSED, live_qty recorded, mirror id
SELECT actual_state, coalesce(actual_refusal, '-') AS refusal, count(*) AS n,
       count(*) FILTER (WHERE live_eligible) AS live_eligible, count(*) FILTER (WHERE live_qty IS NOT NULL) AS live_qty_set,
       count(*) FILTER (WHERE actual_mirror_id IS NOT NULL) AS with_mirror, min(created_at) AS first_at, max(created_at) AS last_at
  FROM execution_intents GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;

\echo L5 CANONICAL DECISION INTENTS by strategy: 24 h and all time, with Derek Karen Allie Archer blocks present, venue
SELECT strategy, venue, count(*) AS all_time,
       count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS last_24h,
       count(*) FILTER (WHERE allie IS NOT NULL AND allie <> 'null'::jsonb) AS with_allie,
       count(*) FILTER (WHERE eddie IS NOT NULL AND eddie <> 'null'::jsonb) AS with_archer,
       count(*) FILTER (WHERE derek IS NOT NULL AND derek <> 'null'::jsonb) AS with_derek,
       count(*) FILTER (WHERE karen IS NOT NULL AND karen <> 'null'::jsonb) AS with_karen,
       max(created_at) AS newest
  FROM canonical_decision_intents GROUP BY 1, 2 ORDER BY 3 DESC;

\echo L6 CANONICAL INTENT EXECUTIONS all time by kind, adapter, mode, state, exclusion (top 30) and newest per adapter
SELECT intent_kind, adapter, mode, state, coalesce(exclusion, '-') AS exclusion, count(*) AS n,
       count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS n_24h, max(created_at) AS newest
  FROM canonical_intent_executions GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 30;

\echo L7 NEWEST 8 SMALL LIVE SHADOW records: intent, state, exclusion, capital scale, requested live qty, venue params present
SELECT execution_id, intent_kind, state, exclusion, capital_scale,
       requested ->> 'qty' AS requested_qty, requested ->> 'action' AS action, requested ->> 'slug' AS slug,
       (venue_params IS NOT NULL) AS has_venue_params, created_at
  FROM canonical_intent_executions WHERE adapter = 'SMALL_LIVE' ORDER BY created_at DESC LIMIT 8;

\echo L8 LIVE PARITY LEDGER all time by kind and parity state, and the newest row
SELECT intent_kind, parity_state, count(*) AS n, max(created_at) AS newest,
       count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS n_24h
  FROM live_parity_ledger GROUP BY 1, 2 ORDER BY 3 DESC;

\echo L9 EXECMIRROR PAPER VS ACTUAL tables: smalllive handoffs, reviews, reconciliations by status (all time)
SELECT 'smalllive_handoffs' AS t, count(*) AS n, max(created_at) AS newest FROM smalllive_handoffs
UNION ALL SELECT 'smalllive_reviews', count(*), max(reviewed_at) FROM smalllive_reviews
UNION ALL SELECT 'execmirror_fills', count(*), max(observed_at) FROM execmirror_fills;

\echo A1 AGENT STATUS (registry heartbeat) every agent: state, activity, heartbeat age, runs, errors
SELECT agent_id, state, left(activity, 140) AS activity, last_heartbeat_at,
       round(extract(epoch FROM now() - last_heartbeat_at)::numeric, 0) AS hb_age_s,
       last_run_started_at, last_run_finished_at, runs, errors, left(last_error, 100) AS last_error,
       left(cadence::text, 120) AS cadence
  FROM agent_status ORDER BY agent_id;

\echo A2 AGENT RUNS per agent: 24 h runs and failures, newest run and outcome
SELECT agent_id, count(*) FILTER (WHERE started_at >= now() - interval '24 hours') AS runs_24h,
       count(*) FILTER (WHERE started_at >= now() - interval '24 hours' AND outcome = 'FAILED') AS failed_24h,
       count(*) FILTER (WHERE started_at >= now() - interval '24 hours' AND finished_at IS NULL) AS unfinished_24h,
       max(started_at) AS newest_started, max(finished_at) AS newest_finished,
       (array_agg(outcome ORDER BY started_at DESC))[1] AS newest_outcome
  FROM agent_runs WHERE started_at >= now() - interval '7 days' GROUP BY 1 ORDER BY 1;

\echo A3 AGENT DECISIONS index per agent and kind (24 h) and newest
SELECT agent_id, kind, count(*) FILTER (WHERE decided_at >= now() - interval '24 hours') AS n_24h,
       max(decided_at) AS newest, (array_agg(verdict ORDER BY decided_at DESC))[1] AS newest_verdict
  FROM agent_decisions WHERE decided_at >= now() - interval '7 days' GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo A4 DEREK: paper decisions of DEREK_ENTRY_POLICY_V2 24 h by verdict and the newest decision ever
SELECT verdict, count(*) AS n, max(decided_at) AS newest
  FROM paper_decisions WHERE policy_version LIKE 'DEREK%' AND decided_at >= now() - interval '24 hours' GROUP BY 1;
SELECT decision_id, decided_at, verdict, refusal, us_market_slug, holding_side, policy_version
  FROM paper_decisions WHERE policy_version LIKE 'DEREK%' ORDER BY decided_at DESC LIMIT 1;
SELECT decision_id, decided_at, verdict, us_market_slug, policy_version
  FROM paper_decisions WHERE policy_version LIKE 'DEREK%' AND verdict = 'ENTER' ORDER BY decided_at DESC LIMIT 1;

\echo A5 XAVIER: paper reviews and management assessments 24 h and newest ever, exit intents by state, management orders and fills
SELECT 'paper_xavier_reviews' AS t, count(*) FILTER (WHERE reviewed_at >= now() - interval '24 hours') AS n_24h,
       max(reviewed_at) AS newest, (array_agg(coalesce(recommendation, 'REFUSED:' || refusal) ORDER BY reviewed_at DESC))[1] AS newest_what
  FROM paper_xavier_reviews
UNION ALL
SELECT 'xavier_management_assessments', count(*) FILTER (WHERE assessed_at >= now() - interval '24 hours'),
       max(assessed_at), (array_agg(recommendation ORDER BY assessed_at DESC))[1]
  FROM xavier_management_assessments;
SELECT state, coalesce(resolution, '-') AS resolution, count(*) AS n, max(decided_at) AS newest
  FROM paper_exit_intents GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;
SELECT role, state, count(*) AS n, max(created_at) AS newest,
       count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS n_24h
  FROM paper_orders WHERE role <> 'ENTRY' GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 15;
SELECT role, count(*) AS fills, max(filled_at) AS newest FROM paper_fills WHERE role <> 'ENTRY' GROUP BY 1 ORDER BY 3 DESC;

\echo A6 AUDREY: paper findings 24 h and newest, ledger reports newest
SELECT count(*) FILTER (WHERE found_at >= now() - interval '24 hours') AS findings_24h, max(found_at) AS newest_finding,
       (array_agg(kind || '/' || severity ORDER BY found_at DESC))[1] AS newest_kind
  FROM paper_audrey_findings;

\echo A7 KAREN: challenges 24 h by state and outcome, newest challenge, blocked
SELECT state, coalesce(outcome, '-') AS outcome, count(*) FILTER (WHERE challenged_at >= now() - interval '24 hours') AS n_24h,
       count(*) AS all_time, count(*) FILTER (WHERE blocked) AS blocked_all_time, max(challenged_at) AS newest
  FROM karen_challenges GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 15;
SELECT challenge_id, challenged_at, target_agent, detector, severity, state, left(claim, 120) AS claim
  FROM karen_challenges ORDER BY challenged_at DESC LIMIT 1;

\echo A8 ARCHER: execution estimates 24 h by recommendation and newest
SELECT recommendation, count(*) AS n_24h, max(estimated_at) AS newest
  FROM eddie_execution_estimates WHERE estimated_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;
SELECT estimate_id, decision_id, estimated_at, recommendation, left(recommendation_reason, 80) AS reason, authority
  FROM eddie_execution_estimates ORDER BY estimated_at DESC LIMIT 1;

\echo A9 ALLIE (CHIEF_ALLOCATOR): intel ALLOCATOR runs 24 h by status, newest, allocations newest
SELECT status, count(*) AS n_24h, max(started_at) AS newest
  FROM intel_runs WHERE component = 'ALLOCATOR' AND started_at >= now() - interval '24 hours' GROUP BY 1;
SELECT run_id, started_at, finished_at, status, left(error, 100) AS error, left(summary::text, 200) AS summary
  FROM intel_runs WHERE component = 'ALLOCATOR' ORDER BY started_at DESC LIMIT 1;
SELECT count(*) FILTER (WHERE computed_at >= now() - interval '24 hours') AS allocations_24h, max(computed_at) AS newest,
       count(*) FILTER (WHERE computed_at >= now() - interval '24 hours' AND shadow_usd > 0) AS positive_shadow_24h
  FROM intel_allocations;

\echo A10 ADRIANA: scans 24 h by status, newest scan, refusals 24 h by code (top 10)
SELECT status, count(*) AS n_24h, max(finished_at) AS newest, sum(opportunities) AS opportunities, max(mode) AS mode
  FROM adriana_arb_scans WHERE started_at >= now() - interval '24 hours' GROUP BY 1;
SELECT primary_code, count(*) AS n FROM adriana_arb_refusals
 WHERE decided_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

\echo A11 OPEN AGENT WORK by agent and kind, oldest open
SELECT agent_id, kind, count(*) AS open_items, min(opened_at) AS oldest_open
  FROM agent_work_open GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo A12 AGENT LOOPS and BEATS: runtime loop rows naming an agent, and agent service heartbeats
SELECT loop_name, process, left(commit_sha, 8) AS commit, cadence_s, last_success_at, successes, errors,
       last_error_at, left(last_error, 60) AS last_error
  FROM runtime_loop_health
 WHERE loop_name ~* '(agent|derek|xavier|audrey|karen|archer|allie|alloc|adriana|intel|paper|peer|capab)'
 ORDER BY 1, 2;
SELECT service, status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 0) AS age_s
  FROM service_heartbeats WHERE service LIKE 'agent%' OR service LIKE 'intel%' ORDER BY 1;
