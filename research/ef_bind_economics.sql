-- ECONOMIC FUNNEL (owner directive section 6), STEP 3: WHY THE FORWARD
-- CANDIDATES WERE NOT POSITIVE, TERM BY TERM -- MEASURED COSTS SEPARATED FROM
-- MODELLED ONES -- AND WHAT THE REFUSED CANDIDATES WOULD HAVE EARNED
-- (HYPOTHETICAL, NEVER REALIZED P&L), CLUSTERED BY FIXTURE.
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
--
-- SOURCE OF THE TERMS. Every bind evaluation (migration 309) stores its
-- pre-outcome attribution (bettor_paper_profitability_bind.attribution, RC4
-- 9b94ef5c) in detail -> 'attribution' -> 'terms'; the terms sum to the
-- all-in EV given a fill, at the policy's size for a refused entry. They are
-- accumulated here in a fixed order so the first layer at which a candidate
-- stops being positive is visible:
--   A  probability edge over the held-side mid (p_raw - mid) x q
--      (= probability_edge_usd + settlement_difference_usd)
--   B  + spread (mid -> best level)                       MEASURED (book)
--   C  + slippage (best -> walked VWAP)                    MEASURED (book)
--   D  + fees (the simulator's fee schedule on each level) MEASURED (schedule)
--   E  + adverse selection (max(IOC bound, learned markout)) MODELLED
--   F  + residual haircut (learned from realized residuals)  MODELLED
--   G  + freshness decay (probability / book age)            MODELLED
--   H  + management / exit cost (learned exit rate x spread) MODELLED
--   I  + calibration adjustment (p_used - p_raw)             MODELLED
--      = the bind's all-in EV given a fill; x fill probability (MODELLED
--        partial-fill assumption) = the expected EV.
-- WINDOW: migration 309 applied_at to now() (the scoreboard's cutover).
-- INDEPENDENT UNIT: the fixture. Repeated evaluations of one contract-side
-- are the same opportunity re-read; the per-contract-side figures use the
-- FIRST evaluation (the earliest, so nothing later leaks in).

\echo '== 3.1 per strategy: bind evaluations, all rows -- term sums (USD) and layer positivity'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
e AS (
    SELECT x.strategy, x.fixture,
           x.us_market_slug || ':' || coalesce(x.holding_side, '?') AS cs,
           (x.detail -> 'attribution' ->> 'filled_qty')::numeric AS q,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'probability_edge_usd')::numeric, 0)
         + coalesce((x.detail -> 'attribution' -> 'terms' ->> 'settlement_difference_usd')::numeric, 0) AS a_edge,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'spread_usd')::numeric, 0) AS b_spread,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'slippage_usd')::numeric, 0) AS c_slip,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'fees_usd')::numeric, 0) AS d_fees,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'adverse_selection_usd')::numeric, 0) AS e_adv,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'residual_haircut_usd')::numeric, 0) AS f_resid,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'freshness_usd')::numeric, 0) AS g_fresh,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'management_usd')::numeric, 0) AS h_mgmt,
           coalesce((x.detail -> 'attribution' -> 'terms' ->> 'calibration_adjustment_usd')::numeric, 0) AS i_cal,
           x.all_in_ev_usd, x.fill_probability
      FROM paper_profitability_evaluations x CROSS JOIN c
     WHERE x.evaluated_at >= c.t
       AND (x.detail -> 'attribution' ->> 'filled_qty')::numeric > 0),
l AS (
    SELECT e.*, e.a_edge AS la,
           e.a_edge + e.b_spread AS lb,
           e.a_edge + e.b_spread + e.c_slip AS lc,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees AS ld,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees + e.e_adv AS le,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees + e.e_adv + e.f_resid AS lf,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees + e.e_adv + e.f_resid + e.g_fresh AS lg,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees + e.e_adv + e.f_resid + e.g_fresh + e.h_mgmt AS lh,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees + e.e_adv + e.f_resid + e.g_fresh + e.h_mgmt + e.i_cal AS li
      FROM e)
SELECT l.strategy, count(*) AS rows, count(DISTINCT l.cs) AS contract_sides,
       count(DISTINCT l.fixture) AS fixtures, round(sum(l.q), 2) AS contracts,
       round(sum(l.a_edge), 2) AS a_edge_vs_mid_usd, round(sum(l.b_spread), 2) AS b_spread_usd,
       round(sum(l.c_slip), 2) AS c_slippage_usd, round(sum(l.d_fees), 2) AS d_fees_usd,
       round(sum(l.e_adv), 2) AS e_adverse_usd, round(sum(l.f_resid), 2) AS f_residual_usd,
       round(sum(l.g_fresh), 2) AS g_freshness_usd, round(sum(l.h_mgmt), 2) AS h_management_usd,
       round(sum(l.i_cal), 2) AS i_calibration_usd, round(sum(l.li), 2) AS all_in_given_fill_usd,
       round(sum(l.all_in_ev_usd), 2) AS recorded_all_in_usd,
       count(*) FILTER (WHERE l.la > 0) AS rows_a_pos, count(*) FILTER (WHERE l.lb > 0) AS rows_b_pos,
       count(*) FILTER (WHERE l.lc > 0) AS rows_c_pos, count(*) FILTER (WHERE l.ld > 0) AS rows_d_pos,
       count(*) FILTER (WHERE l.le > 0) AS rows_e_pos, count(*) FILTER (WHERE l.lf > 0) AS rows_f_pos,
       count(*) FILTER (WHERE l.lg > 0) AS rows_g_pos, count(*) FILTER (WHERE l.lh > 0) AS rows_h_pos,
       count(*) FILTER (WHERE l.li > 0) AS rows_i_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.la > 0) AS fx_a_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.lb > 0) AS fx_b_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.lc > 0) AS fx_c_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.ld > 0) AS fx_d_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.le > 0) AS fx_e_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.lf > 0) AS fx_f_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.lg > 0) AS fx_g_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.lh > 0) AS fx_h_pos,
       count(DISTINCT l.fixture) FILTER (WHERE l.li > 0) AS fx_i_pos
  FROM l GROUP BY 1 ORDER BY 1;

\echo '== 3.2 per strategy: FIRST evaluation per contract-side -- term sums per contract and layer positivity'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
f AS (
    SELECT DISTINCT ON (x.strategy, x.us_market_slug, x.holding_side) x.*
      FROM paper_profitability_evaluations x CROSS JOIN c
     WHERE x.evaluated_at >= c.t
       AND (x.detail -> 'attribution' ->> 'filled_qty')::numeric > 0
     ORDER BY x.strategy, x.us_market_slug, x.holding_side, x.evaluated_at, x.eval_id),
e AS (
    SELECT f.strategy, f.fixture,
           (f.detail -> 'attribution' ->> 'filled_qty')::numeric AS q,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'probability_edge_usd')::numeric, 0)
         + coalesce((f.detail -> 'attribution' -> 'terms' ->> 'settlement_difference_usd')::numeric, 0) AS a_edge,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'spread_usd')::numeric, 0) AS b_spread,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'slippage_usd')::numeric, 0) AS c_slip,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'fees_usd')::numeric, 0) AS d_fees,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'adverse_selection_usd')::numeric, 0) AS e_adv,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'residual_haircut_usd')::numeric, 0) AS f_resid,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'freshness_usd')::numeric, 0) AS g_fresh,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'management_usd')::numeric, 0) AS h_mgmt,
           coalesce((f.detail -> 'attribution' -> 'terms' ->> 'calibration_adjustment_usd')::numeric, 0) AS i_cal
      FROM f),
l AS (
    SELECT e.*,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees AS ld,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees + e.e_adv + e.f_resid + e.g_fresh + e.h_mgmt AS lh,
           e.a_edge + e.b_spread + e.c_slip + e.d_fees + e.e_adv + e.f_resid + e.g_fresh + e.h_mgmt + e.i_cal AS li
      FROM e)
SELECT l.strategy, count(*) AS contract_sides, count(DISTINCT l.fixture) AS fixtures,
       round(sum(l.q), 2) AS contracts,
       round(sum(l.a_edge) / nullif(sum(l.q), 0), 5) AS a_edge_pc,
       round(sum(l.b_spread) / nullif(sum(l.q), 0), 5) AS b_spread_pc,
       round(sum(l.c_slip) / nullif(sum(l.q), 0), 5) AS c_slippage_pc,
       round(sum(l.d_fees) / nullif(sum(l.q), 0), 5) AS d_fees_pc,
       round(sum(l.e_adv) / nullif(sum(l.q), 0), 5) AS e_adverse_pc,
       round(sum(l.f_resid) / nullif(sum(l.q), 0), 5) AS f_residual_pc,
       round(sum(l.g_fresh) / nullif(sum(l.q), 0), 5) AS g_freshness_pc,
       round(sum(l.h_mgmt) / nullif(sum(l.q), 0), 5) AS h_management_pc,
       round(sum(l.i_cal) / nullif(sum(l.q), 0), 5) AS i_calibration_pc,
       round(sum(l.ld), 2) AS after_measured_costs_usd,
       round(sum(l.lh), 2) AS after_modelled_costs_uncalibrated_usd,
       round(sum(l.li), 2) AS all_in_given_fill_usd,
       count(*) FILTER (WHERE l.a_edge > 0) AS cs_edge_vs_mid_pos,
       count(*) FILTER (WHERE l.ld > 0) AS cs_pos_after_measured,
       count(*) FILTER (WHERE l.lh > 0) AS cs_pos_after_modelled_uncal,
       count(*) FILTER (WHERE l.li > 0) AS cs_pos_all_in,
       count(DISTINCT l.fixture) FILTER (WHERE l.ld > 0) AS fx_pos_after_measured,
       count(DISTINCT l.fixture) FILTER (WHERE l.lh > 0) AS fx_pos_after_modelled_uncal,
       count(DISTINCT l.fixture) FILTER (WHERE l.li > 0) AS fx_pos_all_in
  FROM l GROUP BY 1 ORDER BY 1;

\echo '== 3.3 per strategy: the pre-outcome inputs the bind used (first evaluation per contract-side)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
f AS (
    SELECT DISTINCT ON (x.strategy, x.us_market_slug, x.holding_side) x.*
      FROM paper_profitability_evaluations x CROSS JOIN c
     WHERE x.evaluated_at >= c.t
     ORDER BY x.strategy, x.us_market_slug, x.holding_side, x.evaluated_at, x.eval_id)
SELECT f.strategy, count(*) AS contract_sides,
       round(avg(f.p_raw), 4) AS mean_p_raw, round(avg(f.p_used), 4) AS mean_p_used,
       round(avg(f.market_price), 4) AS mean_best_price,
       round(avg(f.p_raw - f.market_price), 4) AS mean_raw_edge_vs_best,
       round(avg(f.calibration_weight), 4) AS mean_cal_weight,
       round(max(f.calibration_weight), 4) AS max_cal_weight,
       count(*) FILTER (WHERE f.detail -> 'bind' ->> 'calibration_status' = 'MEASURED') AS cal_measured,
       count(*) FILTER (WHERE f.detail -> 'bind' ->> 'calibration_status' = 'INSUFFICIENT') AS cal_insufficient,
       count(*) FILTER (WHERE f.detail -> 'bind' ->> 'calibration_status' = 'NO_DATA') AS cal_no_data,
       round(avg(f.fill_probability), 4) AS mean_fill_probability,
       round(avg(f.learned_adverse_per_contract), 5) AS mean_learned_adverse_pc,
       round(avg(f.residual_haircut_per_contract), 5) AS mean_residual_haircut_pc,
       round(avg((f.detail -> 'bind' ->> 'management_cost_per_contract')::numeric), 5) AS mean_management_pc,
       round(avg((f.detail -> 'bind' ->> 'freshness_haircut_per_contract')::numeric), 5) AS mean_freshness_pc,
       round(avg((f.detail -> 'bind' ->> 'freshness_age_s')::numeric), 2) AS mean_input_age_s,
       round(avg((f.detail -> 'bind' ->> 'spread')::numeric), 4) AS mean_spread,
       round(avg(f.qty_in), 1) AS mean_policy_qty,
       round(avg(f.expected_hold_hours), 2) AS mean_expected_hold_h,
       count(f.ev_per_capital_hour) AS with_ev_per_capital_hour
  FROM f GROUP BY 1 ORDER BY 1;

\echo '== 3.4 per strategy x sport x family x regime (first evaluation per contract-side)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
f AS (
    SELECT DISTINCT ON (x.strategy, x.us_market_slug, x.holding_side) x.*
      FROM paper_profitability_evaluations x CROSS JOIN c
     WHERE x.evaluated_at >= c.t
     ORDER BY x.strategy, x.us_market_slug, x.holding_side, x.evaluated_at, x.eval_id)
SELECT f.strategy, f.sport, f.market_family, f.regime, count(*) AS contract_sides,
       count(DISTINCT f.fixture) AS fixtures,
       round(avg(f.p_raw - f.market_price), 4) AS mean_raw_edge_vs_best,
       round(avg(f.ev_per_contract_usd), 5) AS mean_all_in_ev_pc,
       count(*) FILTER (WHERE f.all_in_ev_usd > 0) AS all_in_pos
  FROM f GROUP BY 1, 2, 3, 4 ORDER BY 1, contract_sides DESC;

\echo '== 3.5 HYPOTHETICAL (NOT_REALIZED_PNL): counterfactual variants of the refused forward candidates, first evaluation per contract-side, settled, clustered by fixture'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
v AS (
    SELECT DISTINCT ON (x.strategy, x.variant, x.us_market_slug, x.holding_side)
           x.strategy, x.variant, x.fixture, x.us_market_slug, x.holding_side,
           x.qty, x.expected_ev_usd, x.fill_probability, o.counterfactual_pnl_usd, o.outcome
      FROM paper_counterfactual_variants x CROSS JOIN c
      JOIN paper_counterfactual_variant_outcomes o ON o.variant_id = x.variant_id
     WHERE x.decided_at >= c.t
     ORDER BY x.strategy, x.variant, x.us_market_slug, x.holding_side, x.decided_at, x.variant_id),
fx AS (
    SELECT v.strategy, v.variant, v.fixture, count(*) AS cs,
           sum(v.counterfactual_pnl_usd) AS pnl, sum(v.expected_ev_usd) AS exp,
           sum(v.qty) AS qty
      FROM v GROUP BY 1, 2, 3)
SELECT fx.strategy, fx.variant, count(*) AS fixtures, sum(fx.cs) AS contract_sides,
       round(sum(fx.qty), 1) AS contracts,
       round(sum(fx.exp), 2) AS expected_ev_usd, round(sum(fx.pnl), 2) AS cf_pnl_usd,
       round(sum(fx.pnl) - sum(fx.exp), 2) AS cf_minus_expected_usd,
       round(avg(fx.pnl), 4) AS mean_per_fixture,
       round(stddev_samp(fx.pnl), 4) AS sd_per_fixture,
       round(avg(fx.pnl) - 2.0537 * stddev_samp(fx.pnl) / sqrt(count(*))::numeric, 4) AS lb98_z_per_fixture,
       round(min(fx.pnl), 2) AS worst_fixture, round(max(fx.pnl), 2) AS best_fixture,
       count(*) FILTER (WHERE fx.pnl > 0) AS fixtures_positive
  FROM fx GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 3.6 HYPOTHETICAL: counterfactual variants, ALL rows (the scoreboard basis, repeated evaluations NOT clustered)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT x.strategy, x.variant, count(*) AS rows, count(o.outcome_id) AS settled,
       round(sum(x.expected_ev_usd), 2) AS expected_ev_usd,
       round(sum(o.counterfactual_pnl_usd), 2) AS cf_pnl_usd
  FROM paper_counterfactual_variants x CROSS JOIN c
  LEFT JOIN paper_counterfactual_variant_outcomes o ON o.variant_id = x.variant_id
 WHERE x.decided_at >= c.t
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 3.7 HYPOTHETICAL: 305 shadow counterfactuals (decided before 309; capital-authority refusals), first per contract-side, clustered by fixture'
WITH s AS (
    SELECT DISTINCT ON (x.strategy, x.us_market_slug, x.holding_side)
           x.strategy, x.capital_refusal, x.fixture, x.qty, x.total_executable_ev_usd,
           o.outcome, o.filled_qty, o.exec_cost_usd, o.exec_fees_usd, o.counterfactual_pnl_usd
      FROM paper_shadow_counterfactuals x
      JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
     WHERE o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') AND o.filled_qty > 0
     ORDER BY x.strategy, x.us_market_slug, x.holding_side, x.decided_at, x.shadow_id),
fx AS (
    SELECT s.strategy, s.fixture, count(*) AS cs, sum(s.counterfactual_pnl_usd) AS pnl,
           sum(s.total_executable_ev_usd) AS exp, sum(s.exec_fees_usd) AS fees
      FROM s GROUP BY 1, 2)
SELECT fx.strategy, count(*) AS fixtures, sum(fx.cs) AS contract_sides,
       round(sum(fx.exp), 2) AS expected_executable_ev_usd, round(sum(fx.pnl), 2) AS cf_pnl_usd,
       round(sum(fx.fees), 2) AS cf_fees_usd,
       round(avg(fx.pnl), 4) AS mean_per_fixture, round(stddev_samp(fx.pnl), 4) AS sd_per_fixture,
       round(avg(fx.pnl) - 2.0537 * stddev_samp(fx.pnl) / sqrt(count(*))::numeric, 4) AS lb98_z_per_fixture,
       count(*) FILTER (WHERE fx.pnl > 0) AS fixtures_positive
  FROM fx GROUP BY 1 ORDER BY 1;

\echo '== 3.8 HYPOTHETICAL: 305 shadow counterfactuals, all rows (repeated decisions not clustered)'
SELECT x.strategy, x.capital_refusal, count(*) AS rows,
       count(*) FILTER (WHERE o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') AND o.filled_qty > 0) AS settled_filled,
       count(*) FILTER (WHERE o.outcome = 'NO_FILL') AS no_fill,
       round(sum(x.total_executable_ev_usd), 2) AS expected_executable_ev_usd,
       round(sum(o.counterfactual_pnl_usd) FILTER (
             WHERE o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') AND o.filled_qty > 0), 2) AS cf_pnl_usd,
       min(x.decided_at) AS first_at, max(x.decided_at) AS last_at
  FROM paper_shadow_counterfactuals x
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 GROUP BY 1, 2 ORDER BY 1, 2;
