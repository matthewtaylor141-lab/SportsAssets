-- ECONOMIC FUNNEL (owner directive section 6), STEP 0: THE SHAPES.
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
--
-- Before any funnel number is computed this file establishes, from
-- production rows only:
--   * the forward cutover (migration 309 applied_at, the instant the
--     profitability bind became active; the scoreboard's own cutover);
--   * which strategies and frozen policy versions actually decided, entered,
--     filled and settled, before and after that cutover;
--   * the lifecycle state of every strategy (CASH / QUARANTINED / ...);
--   * the size of every table the funnel will read, so the later queries
--     can be bounded.
-- Nothing here is aggregated into a profitability claim.

\echo '== 0.1 cutover instants (schema_migrations)'
SELECT version, applied_at, extract(epoch FROM applied_at)::float8 AS epoch
  FROM schema_migrations
 WHERE version IN ('290_paper_strategy_lifecycle.sql',
                   '305_paper_capital_authority_shadow.sql',
                   '309_paper_profitability_bind.sql',
                   '311_paper_profitability_stack.sql',
                   '313_paper_exit_intents.sql',
                   '314_kalshi_canonical_venue.sql',
                   '315_red_team_closeout.sql')
 ORDER BY version;

\echo '== 0.2 now'
SELECT now() AS db_now, extract(epoch FROM now())::float8 AS epoch;

\echo '== 0.3 paper_decisions by strategy x policy_version x verdict (all time / since 309)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT d.strategy, d.policy_version, d.verdict,
       count(*)                                         AS rows_all,
       count(*) FILTER (WHERE d.decided_at >= c.t)      AS rows_fwd,
       count(DISTINCT d.fixture)                        AS fixtures_all,
       count(DISTINCT d.fixture) FILTER (WHERE d.decided_at >= c.t)
                                                        AS fixtures_fwd,
       min(d.decided_at) AS first_at, max(d.decided_at) AS last_at
  FROM paper_decisions d CROSS JOIN c
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo '== 0.4 paper_profitability_evaluations by strategy x stage x verdict (since 309)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT e.strategy, e.stage, e.verdict,
       count(*)                                          AS rows,
       count(DISTINCT (e.us_market_slug, e.holding_side)) AS contract_sides,
       count(DISTINCT e.fixture)                         AS fixtures,
       count(*) FILTER (WHERE e.all_in_ev_usd > 0)       AS rows_all_in_ev_pos,
       count(*) FILTER (WHERE e.detail ->> 'bind_refusal' IS NULL)
                                                         AS rows_bind_passed,
       min(e.evaluated_at) AS first_at, max(e.evaluated_at) AS last_at
  FROM paper_profitability_evaluations e CROSS JOIN c
 WHERE e.evaluated_at >= c.t
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo '== 0.5 paper_profitability_evaluations before 309 (should be none)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT count(*) AS rows_before_cutover
  FROM paper_profitability_evaluations e CROSS JOIN c
 WHERE e.evaluated_at < c.t;

\echo '== 0.6 paper_orders by strategy x role x state (all time / since 309)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT o.strategy, o.role, o.direction, o.state,
       count(*) AS orders_all,
       count(*) FILTER (WHERE o.created_at >= c.t) AS orders_fwd,
       count(*) FILTER (WHERE o.filled_qty > 0) AS with_fill_all,
       count(*) FILTER (WHERE o.filled_qty > 0 AND o.created_at >= c.t)
           AS with_fill_fwd,
       min(o.created_at) AS first_at, max(o.created_at) AS last_at
  FROM paper_orders o CROSS JOIN c
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, 3, 4;

\echo '== 0.7 paper_fills by strategy x role x direction'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT f.strategy, f.role, f.direction,
       count(*) AS fills, count(DISTINCT f.group_id) AS groups,
       count(*) FILTER (WHERE f.filled_at >= c.t) AS fills_fwd,
       round(sum(f.qty * f.price), 2) AS notional_usd,
       round(sum(f.fee_usd), 4) AS fees_usd,
       min(f.filled_at) AS first_at, max(f.filled_at) AS last_at
  FROM paper_fills f CROSS JOIN c
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo '== 0.8 paper_settlements / paper_ledger sizes'
SELECT 'paper_settlements' AS t, count(*) AS rows,
       count(DISTINCT position_key) AS keys, min(settled_at) AS first_at,
       max(settled_at) AS last_at FROM paper_settlements
UNION ALL
SELECT 'paper_ledger', count(*), max(seq), min(committed_at), max(committed_at)
  FROM paper_ledger;

\echo '== 0.9 paper_ledger by kind'
SELECT account_id, kind, count(*) AS rows, round(sum(cash_delta_usd), 6) AS cash_delta,
       max(seq) AS last_seq
  FROM paper_ledger GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 0.10 paper_ledger last row per account'
SELECT DISTINCT ON (account_id) account_id, seq, kind, cash_after_usd,
       reserved_after_usd, committed_at
  FROM paper_ledger ORDER BY account_id, seq DESC;

\echo '== 0.11 lifecycle: latest state per strategy and every transition'
SELECT account_id, strategy, from_state, to_state, rule_id, actor,
       recorded_at, left(why, 160) AS why
  FROM paper_strategy_lifecycle_events
 ORDER BY strategy, event_id;

\echo '== 0.12 paper_cash_decisions per strategy (since 309)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT k.strategy, count(*) AS cash_passes,
       sum(k.decisions_evaluated) AS decisions_evaluated,
       min(k.pass_at) AS first_at, max(k.pass_at) AS last_at
  FROM paper_cash_decisions k CROSS JOIN c
 WHERE k.pass_at >= c.t
 GROUP BY 1 ORDER BY 1;

\echo '== 0.13 shadow counterfactuals (305) per strategy x capital_refusal'
SELECT s.strategy, s.capital_refusal, count(*) AS rows,
       count(DISTINCT s.fixture) AS fixtures,
       count(o.outcome_id) AS with_outcome,
       count(o.outcome_id) FILTER (WHERE o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')
                                     AND o.filled_qty > 0) AS settled_filled,
       round(sum(o.counterfactual_pnl_usd) FILTER (
             WHERE o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')
               AND o.filled_qty > 0), 4) AS cf_pnl_usd,
       min(s.decided_at) AS first_at, max(s.decided_at) AS last_at
  FROM paper_shadow_counterfactuals s
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 0.14 counterfactual variants (311) per strategy x variant x verdict'
SELECT v.strategy, v.variant, v.verdict, count(*) AS rows,
       count(DISTINCT v.fixture) AS fixtures,
       round(sum(v.expected_ev_usd), 4) AS expected_ev_usd,
       count(o.outcome_id) AS settled,
       round(sum(o.counterfactual_pnl_usd), 4) AS cf_pnl_usd
  FROM paper_counterfactual_variants v
  LEFT JOIN paper_counterfactual_variant_outcomes o USING (variant_id)
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== 0.15 sleeves (current classification) per strategy'
SELECT sleeve, strategy, policy_version, count(*) AS groups
  FROM paper_sleeve_current_v GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== 0.16 table sizes (estimates) the funnel reads'
SELECT c.relname, c.reltuples::bigint AS est_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relkind = 'r'
   AND c.relname IN ('ext_candidate_outcomes', 'external_valuations',
                     'paper_decisions', 'paper_profitability_evaluations',
                     'paper_orders', 'paper_fills', 'paper_settlements',
                     'paper_ledger', 'paper_book_observations',
                     'paper_entry_refusal_census', 'paper_cash_decisions',
                     'paper_shadow_counterfactuals',
                     'paper_counterfactual_variants',
                     'adriana_arb_opportunities', 'adriana_arb_scans',
                     'canonical_route_receipts', 'us_premap',
                     'paper_xavier_reviews', 'paper_profitability_models')
 ORDER BY c.relname;
