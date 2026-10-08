-- ECONOMIC FUNNEL (owner directive section 6), STEP 5: THE NON-DIRECTIONAL
-- MECHANISMS -- XAVIER MANAGEMENT (value-add vs frozen counterfactuals),
-- ADRIANA STRUCTURAL ARBITRAGE (same / cross venue, SHADOW ONLY), ROUTING
-- SAVINGS (canonical route receipts), ALLOCATION (Allie's shadow weights).
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
-- Nothing here is realized PAPER P&L except where it says so: Xavier's
-- ACTUAL_XAVIER is the realized cash of positions the ledger already holds;
-- every other figure is a counterfactual / theoretical value and is labelled.

\echo '== 5.1 Xavier value-add (migration 206): theses and value-add status per strategy'
SELECT t.strategy, count(DISTINCT t.thesis_id) AS theses,
       count(DISTINCT t.group_id) AS groups,
       count(DISTINCT v.thesis_id) FILTER (WHERE v.status = 'FINAL') AS final,
       count(DISTINCT v.thesis_id) FILTER (WHERE v.status <> 'FINAL') AS not_final,
       min(t.entered_at) AS first_entry, max(t.entered_at) AS last_entry
  FROM xavier_entry_theses t
  LEFT JOIN xavier_value_add v ON v.thesis_id = t.thesis_id
 GROUP BY ROLLUP (1) ORDER BY 1 NULLS FIRST;

\echo '== 5.2 Xavier value-add, FINAL, latest per thesis: ACTUAL vs HOLD_TO_SETTLEMENT vs IMMEDIATE_EXIT, clustered by fixture'
WITH v AS (
    SELECT DISTINCT ON (x.thesis_id) x.thesis_id, x.group_id, x.counterfactuals
      FROM xavier_value_add x
     WHERE x.status = 'FINAL'
     ORDER BY x.thesis_id, x.computed_at DESC),
g AS (SELECT group_id, max(fixture) AS fixture, max(strategy) AS strategy
        FROM paper_fills GROUP BY 1),
r AS (
    SELECT g.strategy, g.fixture,
           (v.counterfactuals -> 'ACTUAL_XAVIER' ->> 'pnl_usd')::numeric AS actual,
           (v.counterfactuals -> 'HOLD_TO_SETTLEMENT' ->> 'pnl_usd')::numeric AS hold,
           (v.counterfactuals -> 'IMMEDIATE_EXIT' ->> 'pnl_usd')::numeric AS ex
      FROM v JOIN g USING (group_id)),
fx AS (
    SELECT r.strategy, r.fixture, count(*) AS theses, sum(r.actual) AS actual,
           sum(r.hold) AS hold, sum(r.ex) AS ex,
           sum(r.actual - r.hold) AS vs_hold, sum(r.actual - r.ex) AS vs_exit,
           count(*) FILTER (WHERE r.ex IS NULL) AS exit_cf_missing
      FROM r GROUP BY 1, 2)
SELECT fx.strategy, count(*) AS fixtures, sum(fx.theses) AS theses,
       round(sum(fx.actual), 2) AS actual_realized_usd, round(sum(fx.hold), 2) AS hold_cf_usd,
       round(sum(fx.ex), 2) AS immediate_exit_cf_usd,
       round(sum(fx.vs_hold), 2) AS actual_minus_hold_usd,
       round(sum(fx.vs_exit), 2) AS actual_minus_exit_usd,
       sum(fx.exit_cf_missing) AS exit_cf_missing,
       round(avg(fx.vs_hold), 4) AS mean_vs_hold_per_fixture,
       round(avg(fx.vs_hold) - 2.0537 * stddev_samp(fx.vs_hold) / sqrt(count(*))::numeric, 4) AS lb98_z_vs_hold,
       round(avg(fx.vs_exit), 4) AS mean_vs_exit_per_fixture,
       round(avg(fx.vs_exit) - 2.0537 * stddev_samp(fx.vs_exit) / sqrt(count(*))::numeric, 4) AS lb98_z_vs_exit
  FROM fx GROUP BY ROLLUP (1) ORDER BY 1 NULLS FIRST;

\echo '== 5.3 Adriana scans (SHADOW ONLY): status, mode, opportunities found'
SELECT s.mode, s.status, s.engine_version, count(*) AS scans,
       sum(s.markets_read) AS markets_read, sum(s.structures_considered) AS structures,
       sum(s.opportunities) AS opportunities, min(s.started_at) AS first_at,
       max(s.started_at) AS last_at
  FROM adriana_arb_scans s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== 5.4 Adriana opportunities by kind x verdict x mode (theoretical profit only, never realized)'
SELECT o.structure_kind, o.verdict, o.mode, o.production_effect, count(*) AS rows,
       count(DISTINCT o.event_key) AS events, round(sum(o.net_profit_usd), 2) AS theoretical_net_usd,
       sum(o.max_qty) AS max_qty, min(o.decided_at) AS first_at, max(o.decided_at) AS last_at
  FROM adriana_arb_opportunities o GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo '== 5.5 pair execution receipts (red-team sentinel): matched / locked'
SELECT r.state, r.mode, r.execution_label, count(*) AS receipts, count(DISTINCT r.pair_id) AS pairs,
       round(sum(r.matched_qty), 2) AS matched_qty,
       count(*) FILTER (WHERE r.execution_locked) AS execution_locked
  FROM red_team_pair_execution_receipts r GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== 5.6 canonical route receipts (routing savings, counterfactual): volume, refusals, comparables'
SELECT r.mode, r.production_effect, coalesce(r.refusal, '(chosen)') AS refusal,
       count(*) AS receipts, count(DISTINCT r.event_key) AS events,
       count(*) FILTER (WHERE r.best_single IS NOT NULL) AS with_best_single,
       count(*) FILTER (WHERE r.runner_up IS NOT NULL
                          AND (r.runner_up ->> 'all_in_per_contract') IS NOT NULL) AS with_runner_up,
       min(r.computed_at) AS first_at, max(r.computed_at) AS last_at
  FROM canonical_route_receipts r GROUP BY 1, 2, 3 ORDER BY receipts DESC LIMIT 40;

\echo '== 5.7 routing: chosen venue x runner-up venue, and the all-in saving per contract (counterfactual)'
SELECT r.best_single ->> 'venue' AS chosen_venue, r.runner_up ->> 'venue' AS runner_up_venue,
       count(*) AS receipts, count(DISTINCT r.event_key) AS events,
       round(avg((r.runner_up ->> 'all_in_per_contract')::numeric
                 - (r.best_single ->> 'all_in_per_contract')::numeric), 5) AS mean_saving_pc,
       round(sum(((r.runner_up ->> 'all_in_per_contract')::numeric
                  - (r.best_single ->> 'all_in_per_contract')::numeric) * r.qty), 2) AS saving_usd_at_qty
  FROM canonical_route_receipts r
 WHERE (r.best_single ->> 'all_in_per_contract') IS NOT NULL
   AND (r.runner_up ->> 'all_in_per_contract') IS NOT NULL
 GROUP BY 1, 2 ORDER BY receipts DESC;

\echo '== 5.8 Allie (allocation): shadow weights only'
SELECT a.label, a.candidate_kind, count(*) AS rows, count(DISTINCT a.run_id) AS runs,
       round(sum(a.shadow_usd)::numeric, 2) AS shadow_usd, min(a.computed_at) AS first_at,
       max(a.computed_at) AS last_at
  FROM intel_allocations a GROUP BY 1, 2 ORDER BY 1, 2;
