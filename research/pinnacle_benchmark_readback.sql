-- READ-ONLY. THE PINNACLE_ONLY_PAPER_BENCHMARK, READ BACK FROM THE DATABASE.
--
-- PAPER ONLY. An EXPERIMENTAL paper execution benchmark on the fictional
-- paper account (agents/paper_benchmark.py, migration 182): it decides on the
-- stored de-vigged Pinnacle probability alone. It is NOT evidence of
-- qualified or proven profitability; every fill is SIMULATED (PAPER_SIM_V1)
-- and the venue book's currency is NOT_ESTABLISHED (P5).
--
-- B1  the switches: the paper_control rows (the session, the benchmark's
--     kill switch, the two-model strategy's entry switch), migration 182,
--     the active session
-- B2  the newest benchmark decisions (p_internal / p_blended must be NULL)
-- B3  benchmark decisions by verdict and named refusal (24 h and all time)
-- B4  the strongest candidates by edge, with their exact shortfalls
--     (edge pp vs 5.0, EV after fees, depth within the limit, ages)
-- B5  benchmark paper orders
-- B6  simulated fills, or the named no-fill reason of each unfilled order
-- B7  ledger movements of benchmark orders and groups (one cash ledger)
-- B8  handoffs to Xavier and his latest review measure per group
-- B9  Audrey: benchmark findings and the benchmark section of her report
-- B10 the account: balances from the ledger sums, one funding entry,
--     running balances agree, venue mutation attempts (expected 0)
-- B11 coexistence: valuations decided by both strategies
--
-- Every statement below is a SELECT. Nothing here selects a credential.
-- Run with psql against the production database:  \i this file

\echo '== B1 · switch, migration 182, active session =='
SELECT control_key, enabled, why, updated_by, updated_at
  FROM paper_control
 WHERE control_key IN ('PAPER_SESSION', 'PINNACLE_ONLY_PAPER_BENCHMARK',
                       'PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2');
SELECT version, applied_at FROM schema_migrations
 WHERE version LIKE '182_%';
SELECT session_id, account_id, started_at, status, simulator_version
  FROM paper_sessions WHERE status = 'ACTIVE';

\echo '== B2 · newest benchmark decisions =='
SELECT d.decided_at, d.valuation_id, d.us_market_slug, d.holding_side,
       d.verdict, d.refusal, d.p_internal, d.p_blended,
       d.internal_model->>'available' AS internal_model_available,
       round(d.p_pinnacle::numeric, 6) AS p_pinnacle,
       d.pinnacle->>'qualification' AS pinnacle_qualification,
       (d.pinnacle->>'age_s')::float8 AS pinnacle_age_s,
       d.pinnacle->>'decided_via' AS decided_via,
       (d.economics->>'best_level_edge_pp')::float8 AS best_edge_pp,
       d.limit_price, d.proposed_qty,
       (d.economics->'acquisition'->>'expected_net_profit_usd')::float8
           AS ev_after_fees_usd,
       d.book->'book_currency'->>'verdict' AS book_currency,
       d.label->>'strategy' AS label_strategy, d.strategy, d.policy_version
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY d.decided_at DESC LIMIT 50;

\echo '== B3 · benchmark decisions by verdict and refusal =='
SELECT d.verdict, coalesce(d.refusal, 'ENTER') AS reason,
       count(*) FILTER (WHERE d.decided_at > now() - interval '24 hours')
           AS last_24h,
       count(*) AS all_time,
       count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 GROUP BY 1, 2 ORDER BY all_time DESC;

\echo '== B4 · strongest candidates by edge, with their shortfalls =='
SELECT d.decided_at, d.valuation_id, d.us_market_slug, d.verdict, d.refusal,
       (d.economics->'shortfall'->>'edge_pp')::float8 AS edge_pp,
       (d.economics->'shortfall'->>'edge_threshold_pp')::float8
           AS threshold_pp,
       (d.economics->'shortfall'->>'edge_shortfall_pp')::float8
           AS edge_shortfall_pp,
       (d.economics->'shortfall'->>'ev_after_fees_usd')::float8
           AS ev_after_fees_usd,
       (d.economics->'shortfall'->>'depth_within_limit')::float8
           AS depth_within_limit,
       (d.economics->'shortfall'->>'qty')::float8 AS qty,
       (d.economics->'shortfall'->>'pinnacle_age_s')::float8
           AS pinnacle_age_s,
       (d.economics->'shortfall'->>'book_age_s')::float8 AS book_age_s,
       d.economics->'levels'->0 AS best_level
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
   AND d.economics->>'best_level_edge_pp' IS NOT NULL
 ORDER BY (d.economics->>'best_level_edge_pp')::float8 DESC,
          d.decided_at DESC
 LIMIT 25;
-- Candidates refused before any book was read (stale Pinnacle, identity,
-- payout outcome or settlement terms) carry no edge; their freshness
-- shortfall is here:
SELECT d.refusal, count(*) AS decisions,
       round(avg((d.economics->'shortfall'->>'pinnacle_age_s')::float8)
             ::numeric, 1) AS mean_pinnacle_age_s,
       min((d.economics->'shortfall'->>'pinnacle_age_s')::float8)
           AS min_pinnacle_age_s
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
   AND d.economics->>'best_level_edge_pp' IS NULL
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== B5 · benchmark paper orders =='
SELECT o.created_at, o.order_id, o.decision_id, o.group_id, o.role,
       o.direction, o.holding_side, o.us_market_slug, o.order_type,
       o.time_in_force, o.qty, o.filled_qty, o.limit_price,
       o.reserved_usd, o.reserved_remaining_usd, o.state, o.eligible_at,
       o.expires_at, o.terminal_reason, o.label->>'strategy' AS label_strategy,
       o.label->>'book_currency' AS book_currency, o.strategy
  FROM paper_orders o
 WHERE o.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY o.created_at DESC LIMIT 100;

\echo '== B6 · simulated fills, or the named no-fill reason =='
SELECT f.filled_at, f.fill_id, f.order_id, o.role, f.direction, f.qty,
       f.price, f.fee_usd, f.gross_usd, f.basis, f.book_obs_id,
       f.book_observed_at, o.eligible_at, f.event_source,
       f.label->>'strategy' AS label_strategy, f.strategy
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 WHERE o.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY f.filled_at DESC LIMIT 100;
SELECT o.order_id, o.role, o.state, o.qty, o.filled_qty, o.terminal_reason,
       (SELECT e.kind || ': ' || coalesce(e.detail->>'reason', '')
          FROM paper_order_events e WHERE e.order_id = o.order_id
         ORDER BY e.event_id DESC LIMIT 1) AS last_event
  FROM paper_orders o
 WHERE o.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
   AND o.filled_qty < o.qty
 ORDER BY o.created_at DESC LIMIT 100;

\echo '== B7 · ledger movements of benchmark orders and groups =='
SELECT l.seq, l.committed_at, l.kind, l.cash_delta_usd, l.reserved_delta_usd,
       l.cash_after_usd, l.reserved_after_usd, l.order_id, l.fill_id,
       l.group_id, l.settlement_key, l.event_source
  FROM paper_ledger l
 WHERE l.group_id IN (SELECT o.group_id FROM paper_orders o
                       WHERE o.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK')
 ORDER BY l.seq DESC LIMIT 200;
SELECT l.kind, count(*) AS entries, sum(l.cash_delta_usd) AS cash_usd,
       sum(l.reserved_delta_usd) AS reserved_usd
  FROM paper_ledger l
 WHERE l.group_id IN (SELECT o.group_id FROM paper_orders o
                       WHERE o.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK')
 GROUP BY 1 ORDER BY 1;
SELECT s.settled_at, s.position_key, s.version, s.outcome, s.qty,
       s.payout_per_contract, s.payout_usd, s.evidence_source
  FROM paper_settlements s
 WHERE s.group_id IN (SELECT o.group_id FROM paper_orders o
                       WHERE o.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK')
 ORDER BY s.settled_at DESC;

\echo '== B8 · handoffs to Xavier and his latest review per group =='
SELECT h.created_at, h.handoff_id, h.group_id, h.decision_id, h.owner,
       h.confirmed_qty, h.outstanding_qty, h.first_fill_at, h.strategy
  FROM paper_handoffs h
 WHERE h.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY h.created_at DESC LIMIT 100;
SELECT DISTINCT ON (r.group_id) r.group_id, r.reviewed_at, r.trigger,
       r.recommendation, r.refusal, r.measure->>'strategy' AS measure_strategy,
       r.measure->>'source' AS measure_source,
       (r.measure->>'p')::float8 AS measure_p,
       r.measure->>'stale' AS measure_stale, r.action->>'taken' AS action,
       r.strategy
  FROM paper_xavier_reviews r
 WHERE r.group_id IN (SELECT h.group_id FROM paper_handoffs h
                       WHERE h.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK')
 ORDER BY r.group_id, r.reviewed_at DESC;

\echo '== B9 · Audrey: benchmark findings and report section =='
SELECT f.found_at, f.kind, f.severity, f.subject,
       f.detail->>'passed' AS passed,
       f.detail->>'refusal' AS refusal, f.improvement_task_id
  FROM paper_audrey_findings f
 WHERE f.kind LIKE 'PINNACLE_ONLY_PAPER_BENCHMARK%'
    OR f.detail->>'strategy' = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY f.found_at DESC LIMIT 100;
SELECT r.report_day, r.version, r.generated_at, r.reconciles,
       r.report->'pinnacle_only_paper_benchmark'->'fills' AS benchmark_fills,
       r.report->'pinnacle_only_paper_benchmark'->'positions'
           AS benchmark_positions,
       r.report->'pinnacle_only_paper_benchmark'->'decisions'
           AS benchmark_decisions,
       r.report->'pinnacle_only_paper_benchmark'->>'disclosure' AS disclosure
  FROM paper_audrey_reports r
 WHERE r.report ? 'pinnacle_only_paper_benchmark'
 ORDER BY r.generated_at DESC LIMIT 5;

\echo '== B10 · the account: one ledger, one funding, zero mutations =='
SELECT l.account_id, sum(l.cash_delta_usd) AS cash_usd,
       sum(l.reserved_delta_usd) AS reserved_usd,
       sum(l.cash_delta_usd) - sum(l.reserved_delta_usd) AS available_usd,
       count(*) FILTER (WHERE l.kind = 'INITIAL_FUNDING') AS funding_entries,
       count(*) AS entries
  FROM paper_ledger l GROUP BY 1 ORDER BY 1;
SELECT l.account_id, l.seq, l.cash_after_usd, l.reserved_after_usd,
       (l.cash_after_usd = t.cash AND l.reserved_after_usd = t.res)
           AS running_balance_agrees
  FROM (SELECT DISTINCT ON (account_id) account_id, seq, cash_after_usd,
               reserved_after_usd
          FROM paper_ledger ORDER BY account_id, seq DESC) l
  JOIN (SELECT account_id, sum(cash_delta_usd) AS cash,
               sum(reserved_delta_usd) AS res
          FROM paper_ledger GROUP BY 1) t USING (account_id)
 ORDER BY 1;
SELECT h.session_id, h.heartbeat_at, h.passes, h.errors,
       h.mutation_attempts, h.last_mutation_attempt
  FROM paper_session_health h
  JOIN paper_sessions s ON s.session_id = h.session_id
 WHERE s.status = 'ACTIVE';

\echo '== B11 · coexistence: valuations decided by both strategies =='
SELECT count(*) AS valuations_with_both,
       count(*) FILTER (WHERE x.bench_verdict = 'ENTER') AS benchmark_entered,
       count(*) FILTER (WHERE x.two_model_verdict = 'ENTER')
           AS two_model_entered
  FROM (SELECT d.session_id, d.valuation_id,
               max(d.verdict) FILTER (WHERE d.strategy =
                   'PINNACLE_ONLY_PAPER_BENCHMARK') AS bench_verdict,
               max(d.verdict) FILTER (WHERE d.strategy =
                   'DEREK_ENTRY_POLICY_V2') AS two_model_verdict
          FROM paper_decisions d WHERE d.valuation_id IS NOT NULL
         GROUP BY 1, 2
        HAVING count(DISTINCT d.strategy) = 2) x;
-- the two-model strategy keeps recording; with its entry switch off a
-- decision its policy admitted is REFUSE / STRATEGY_ENTRIES_DISABLED
SELECT d.strategy, d.verdict, coalesce(d.refusal, 'ENTER') AS reason,
       count(*) AS decisions,
       count(*) FILTER (WHERE d.policy_decision->>'admitted' = 'true')
           AS policy_admitted
  FROM paper_decisions d
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;
SELECT o.strategy, o.role, o.state, count(*) AS orders
  FROM paper_orders o GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
