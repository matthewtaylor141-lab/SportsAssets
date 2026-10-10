-- RC6.3c PROOF D: ORDER INTEGRITY AND AUTHORITY SINCE THE DEPLOY (SELECT only; nothing here writes).
--
-- PURPOSE. Prove that nothing phantom appeared with the RC6.3c deploy: every PAPER order since 2026-10-10 18:00:00+00 is
-- the execution of a recorded ENTER decision (migration 172: a paper order names the decision it executes,
-- paper_orders.decision_id, never the reverse) or a Xavier management order on a held group; every fill belongs
-- to such an order; the venue path recorded no mutation attempt from the paper path (paper_session_health.
-- mutation_attempts, expected 0, bettor_paper_session.health); no real-money / ACTUAL order row appeared on any
-- venue table; and the authority state in the database is unchanged: SMALL LIVE = SHADOW (small_live_control.mode,
-- execmirror_control stopped), Kalshi live money disabled (kalshi_smalllive_control.enabled = false), Adriana SHADOW
-- only (adriana_arb_scans.mode / production_effect / authority, agent_status), PAPER_SESSION control on.
--
-- PLACEHOLDER. 2026-10-10 18:00:00+00 : the deploy instant (ISO timestamp with zone), e.g. 2026-10-10 18:00:00+00.
--
-- WHAT COUNTS AS AN ORPHAN. D2: an ENTRY order whose decision_id is NULL or names no paper_decisions row with
-- verdict ENTER (agents/paper_derek.py owed_order writes the decision first); a fill whose order is not an ENTRY
-- with an ENTER decision and not a management order (role HEDGE / EXIT / REDUCE / STANDING_PROTECTION, which Xavier
-- submits with decision_id NULL -- agents/paper_xavier.py:1409, :1529 -- on a group that has a filled ENTRY order).
-- D2 also counts ENTER decisions since the deploy with no order (ENTER_WITHOUT_ORDER; the backstop names them as
-- findings after 60 s, agents/paper_derek.py:1712) and the hook-failure rows that explain a deferred ENTER.
--
-- PASS CRITERIA. D2: entry_orders_without_enter_decision = 0, fills_on_orphan_orders = 0,
-- management_orders_on_unknown_group = 0 (since the deploy; the all-time row is the baseline). D5: mutation_attempts
-- = 0 and unchanged. D6: every venue table's rows_since_deploy = 0, canonical_intent_executions since the deploy
-- carry adapter PAPER or mode SHADOW only. D7: small_live_control.mode = SHADOW; execmirror_control.stopped = true;
-- kalshi_smalllive_control.enabled = false; adriana_arb_scans newest rows mode SHADOW with no production effect;
-- paper_control PAPER_SESSION enabled; the shadow flags in ingestion_state unchanged (true).

\echo D1 PAPER rows since the deploy, per table (decisions, orders, fills, order events, settlements, ledger, reviews, shadows, findings, hook failures)
SELECT 'paper_decisions' AS tbl, count(*) AS rows_since_deploy, to_char(max(recorded_at), 'MM-DD HH24:MI:SS') AS newest FROM paper_decisions WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_orders', count(*), to_char(max(created_at), 'MM-DD HH24:MI:SS') FROM paper_orders WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_fills', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_fills WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_order_events', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_order_events WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_settlements', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_settlements WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_ledger', count(*), to_char(max(committed_at), 'MM-DD HH24:MI:SS') FROM paper_ledger WHERE committed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_xavier_reviews', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_xavier_reviews WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_shadow_counterfactuals', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_shadow_counterfactuals WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_shadow_counterfactual_outcomes', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_shadow_counterfactual_outcomes WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_audrey_findings', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_audrey_findings WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_hook_failures', count(*), to_char(max(recorded_at), 'MM-DD HH24:MI:SS') FROM paper_hook_failures WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
UNION ALL SELECT 'paper_equity_snapshots', count(*), to_char(max(at), 'MM-DD HH24:MI:SS') FROM paper_equity_snapshots WHERE at >= TIMESTAMPTZ '2026-10-10 18:00:00+00';

\echo D2 orphans: ENTRY orders without an ENTER decision, fills on orphan orders, management orders on unknown groups, ENTER decisions without an order (since the deploy, and all time)
WITH o AS (
  SELECT o.*, d.verdict AS decision_verdict,
         EXISTS (SELECT 1 FROM paper_orders e WHERE e.group_id = o.group_id AND e.role = 'ENTRY' AND e.filled_qty > 0) AS group_has_filled_entry
    FROM paper_orders o LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id),
f AS (SELECT f.fill_id, f.recorded_at, o.role, o.decision_verdict, o.group_has_filled_entry FROM paper_fills f JOIN o ON o.order_id = f.order_id),
e AS (
  SELECT d.decision_id, d.recorded_at FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND NOT EXISTS (SELECT 1 FROM paper_orders x WHERE x.decision_id = d.decision_id))
SELECT 'since_deploy' AS scope,
       (SELECT count(*) FROM o WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' AND role = 'ENTRY') AS entry_orders,
       (SELECT count(*) FROM o WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' AND role = 'ENTRY' AND coalesce(decision_verdict, '') <> 'ENTER') AS entry_orders_without_enter_decision,
       (SELECT count(*) FROM o WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' AND role <> 'ENTRY') AS management_orders,
       (SELECT count(*) FROM o WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' AND role <> 'ENTRY' AND NOT group_has_filled_entry) AS management_orders_on_unknown_group,
       (SELECT count(*) FROM f WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00') AS fills,
       (SELECT count(*) FROM f WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
                                 AND NOT ((role = 'ENTRY' AND decision_verdict = 'ENTER') OR (role <> 'ENTRY' AND group_has_filled_entry))) AS fills_on_orphan_orders,
       (SELECT count(*) FROM e WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00') AS enter_decisions_without_order,
       (SELECT count(*) FROM e WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' AND recorded_at < now() - interval '60 seconds') AS enter_without_order_older_than_60s
UNION ALL
SELECT 'all_time',
       (SELECT count(*) FROM o WHERE role = 'ENTRY'),
       (SELECT count(*) FROM o WHERE role = 'ENTRY' AND coalesce(decision_verdict, '') <> 'ENTER'),
       (SELECT count(*) FROM o WHERE role <> 'ENTRY'),
       (SELECT count(*) FROM o WHERE role <> 'ENTRY' AND NOT group_has_filled_entry),
       (SELECT count(*) FROM f),
       (SELECT count(*) FROM f WHERE NOT ((role = 'ENTRY' AND decision_verdict = 'ENTER') OR (role <> 'ENTRY' AND group_has_filled_entry))),
       (SELECT count(*) FROM e),
       (SELECT count(*) FROM e WHERE recorded_at < now() - interval '60 seconds');

\echo D2b the ENTER-without-order explanations since the deploy: backstop findings and hook-failure rows by kind
SELECT 'paper_audrey_findings' AS src, kind, count(*) AS n, to_char(max(found_at), 'MM-DD HH24:MI:SS') AS newest
  FROM paper_audrey_findings WHERE found_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' GROUP BY 1, 2
UNION ALL
SELECT 'paper_hook_failures', coalesce(h.strategy, '-') || ' ' || coalesce(h.stage, '-') || ' ' || coalesce(h.outcome, '-'), count(*), to_char(max(h.recorded_at), 'MM-DD HH24:MI:SS')
  FROM paper_hook_failures h WHERE h.recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'
 GROUP BY 1, 2
ORDER BY 1, 3 DESC;

\echo D3 paper decisions since the deploy by strategy and verdict (and the top refusals)
SELECT strategy, verdict, count(*) AS decisions, count(DISTINCT us_market_slug) AS contracts,
       to_char(min(decided_at), 'HH24:MI:SS') AS first_at, to_char(max(decided_at), 'HH24:MI:SS') AS last_at
  FROM paper_decisions WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' GROUP BY 1, 2 ORDER BY 1, 2;
SELECT strategy, refusal, count(*) AS decisions FROM paper_decisions
 WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' AND verdict = 'REFUSE' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo D4 paper orders since the deploy by role and state; fills by role and direction; ledger rows by kind
SELECT role, state, count(*) AS orders, round(sum(qty), 2) AS qty, round(sum(filled_qty), 2) AS filled_qty, count(*) FILTER (WHERE decision_id IS NULL) AS without_decision_id
  FROM paper_orders WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' GROUP BY 1, 2 ORDER BY 1, 2;
SELECT role, direction, count(*) AS fills, round(sum(qty), 2) AS qty, round(sum(gross_usd), 2) AS gross_usd, round(sum(fee_usd), 4) AS fees_usd, basis, event_source
  FROM paper_fills WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' GROUP BY 1, 2, 7, 8 ORDER BY 1, 2;
SELECT kind, event_source, count(*) AS rows_n, round(sum(cash_delta_usd), 4) AS cash_delta_usd, round(sum(reserved_delta_usd), 4) AS reserved_delta_usd
  FROM paper_ledger WHERE committed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' GROUP BY 1, 2 ORDER BY 1, 2;

\echo D5 venue mutation attempts from the paper path (must stay 0)
SELECT session_id, mutation_attempts, left(last_mutation_attempt::text, 200) AS last_mutation_attempt, passes, to_char(heartbeat_at, 'MM-DD HH24:MI:SS') AS heartbeat_at
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST;

\echo D6 real-money / ACTUAL venue tables: rows since the deploy (must be 0) and the newest row ever
SELECT 'execmirror_orders' AS tbl, count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00') AS rows_since_deploy, count(*) AS rows_all_time, to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') AS newest_ever FROM execmirror_orders
UNION ALL SELECT 'execmirror_fills', count(*) FILTER (WHERE observed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(observed_at), 'YYYY-MM-DD HH24:MI:SS') FROM execmirror_fills
UNION ALL SELECT 'kalshi_live_intents', count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM kalshi_live_intents
UNION ALL SELECT 'kalshi_live_fills', count(*) FILTER (WHERE observed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(observed_at), 'YYYY-MM-DD HH24:MI:SS') FROM kalshi_live_fills
UNION ALL SELECT 'kalshi_live_events', count(*) FILTER (WHERE at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(at), 'YYYY-MM-DD HH24:MI:SS') FROM kalshi_live_events
UNION ALL SELECT 'small_live_order_events', count(*) FILTER (WHERE observed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(observed_at), 'YYYY-MM-DD HH24:MI:SS') FROM small_live_order_events
UNION ALL SELECT 'canonical_intent_executions [adapter not PAPER and mode not SHADOW]', count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' AND adapter <> 'PAPER' AND mode <> 'SHADOW'), count(*) FILTER (WHERE adapter <> 'PAPER' AND mode <> 'SHADOW'), to_char(max(created_at) FILTER (WHERE adapter <> 'PAPER' AND mode <> 'SHADOW'), 'YYYY-MM-DD HH24:MI:SS') FROM canonical_intent_executions
UNION ALL SELECT 'bettor_funded_intents', count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM bettor_funded_intents
UNION ALL SELECT 'bettor_funded_fills', count(*) FILTER (WHERE recorded_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(recorded_at), 'YYYY-MM-DD HH24:MI:SS') FROM bettor_funded_fills
UNION ALL SELECT 'mirror_orders', count(*) FILTER (WHERE placed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(placed_at), 'YYYY-MM-DD HH24:MI:SS') FROM mirror_orders
UNION ALL SELECT 'live_orders', count(*) FILTER (WHERE placed_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(placed_at), 'YYYY-MM-DD HH24:MI:SS') FROM live_orders
UNION ALL SELECT 'bettor_desk_orders', count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00'), count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM bettor_desk_orders
ORDER BY 1;
SELECT adapter, mode, state, count(*) AS executions, to_char(max(created_at), 'MM-DD HH24:MI:SS') AS newest
  FROM canonical_intent_executions WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
SELECT actual_state, coalesce(actual_refusal, '-') AS actual_refusal, count(*) AS intents, count(actual_mirror_id) AS with_mirror_id, to_char(max(created_at), 'MM-DD HH24:MI:SS') AS newest
  FROM execution_intents WHERE created_at >= TIMESTAMPTZ '2026-10-10 18:00:00+00' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;

\echo D7 authority state recorded in the database: SMALL LIVE, execution mirror, Kalshi small live, approvals, red-team control receipts, Adriana, paper controls, shadow flags
SELECT 'small_live_control' AS src, 'mode=' || mode || ' halted=' || halted::text || ' halt_reason=' || coalesce(halt_reason, '-') AS state, to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at FROM small_live_control
UNION ALL SELECT 'execmirror_control', 'enabled=' || enabled::text || ' stopped=' || stopped::text || ' stop_done_at=' || coalesce(to_char(stop_done_at, 'YYYY-MM-DD HH24:MI'), '-') || ' scale=' || scale::text || ' max_order_usd=' || coalesce(max_order_usd::text, '-'), to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') FROM execmirror_control
UNION ALL SELECT 'kalshi_smalllive_control', 'enabled=' || enabled::text || ' stopped=' || stopped::text || ' kalshi_env=' || coalesce(kalshi_env, '-') || ' revision=' || revision::text, to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') FROM kalshi_smalllive_control
UNION ALL SELECT 'paper_control:' || control_key, 'enabled=' || enabled::text || ' by ' || coalesce(updated_by, '-'), to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') FROM paper_control
UNION ALL SELECT 'ingestion_state:' || key, left(value::text, 60), NULL FROM ingestion_state WHERE key IN ('ext_pinnacle_shadow', 'research_shadow_uncalibrated', 'rn1x_shadow')
ORDER BY 1;
SELECT subject_kind, subject_id, subject_version, decision, approved_by, to_char(recorded_at, 'YYYY-MM-DD HH24:MI:SS') AS recorded_at
  FROM live_approvals ORDER BY recorded_at DESC LIMIT 5;
SELECT DISTINCT ON (control) control, status, left(implementation_sha, 12) AS implementation_sha, to_char(computed_at, 'MM-DD HH24:MI:SS') AS computed_at, left(blockers::text, 120) AS blockers
  FROM red_team_control_receipts ORDER BY control, computed_at DESC;
SELECT to_char(started_at, 'MM-DD HH24:MI:SS') AS started, status, mode, production_effect, left(authority::text, 160) AS authority, opportunities, refusals_total
  FROM adriana_arb_scans ORDER BY started_at DESC LIMIT 3;
SELECT agent_id, state, activity, runs, errors, to_char(last_heartbeat_at, 'MM-DD HH24:MI:SS') AS last_heartbeat_at, left(last_error, 100) AS last_error
  FROM agent_status WHERE agent_id IN ('ADRIANA', 'XAVIER', 'AUDREY', 'ALLIE', 'DEREK') ORDER BY agent_id;
