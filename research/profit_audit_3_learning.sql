-- profitability-controls root-cause audit part 3: the learning loop (shadow
-- counterfactuals, variants, calibration population, fitted models). SELECT only.
\echo == A. bind own result inside the lifecycle-refused evaluation rows
select strategy, coalesce(detail->'bind'->>'refusal','(bind passed)') bind_refusal, count(*) n
  from paper_profitability_evaluations
 where refusal = 'STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY'
 group by 1,2 order by n desc;
\echo == B. control provenance status across all evaluations
select c.key control, c.value->>'status' status, count(*) n
  from paper_profitability_evaluations e, jsonb_each(e.detail->'bind'->'controls') c
 where jsonb_typeof(e.detail->'bind'->'controls') = 'object'
 group by 1,2 order by 1,2;
\echo == C. shadow counterfactuals recorded
select strategy, source, capital_refusal, count(*) n, min(decided_at) first_at, max(decided_at) last_at
  from paper_shadow_counterfactuals group by 1,2,3 order by n desc;
\echo == C2. shadow counterfactual outcomes
select outcome, count(*) n, round(sum(counterfactual_pnl_usd)::numeric,2) pnl_usd, round(sum(filled_qty)::numeric,0) filled_qty
  from paper_shadow_counterfactual_outcomes group by 1 order by 1;
\echo == D. counterfactual variants by stage verdict variant
select stage, verdict, variant, count(*) n, min(decided_at) first_at, max(decided_at) last_at
  from paper_counterfactual_variants group by 1,2,3 order by 1,2,3;
\echo == D2. variant outcomes
select v.variant, o.outcome, count(*) n, round(sum(o.counterfactual_pnl_usd)::numeric,2) pnl_usd
  from paper_counterfactual_variant_outcomes o join paper_counterfactual_variants v using (variant_id)
 group by 1,2 order by 1,2;
\echo == E1. priced decisions in the 120 day window versus the newest 20000 read
select count(*) priced_120d, min(decided_at) oldest, max(decided_at) newest,
       count(distinct (us_market_slug, holding_side)) contracts
  from paper_decisions
 where decided_at >= now() - interval '120 days' and us_market_slug is not null
   and holding_side is not null and coalesce(p_blended, p_pinnacle, p_internal) is not null;
select count(*) n, min(decided_at) oldest, max(decided_at) newest,
       count(distinct (us_market_slug, holding_side)) contracts
  from (select us_market_slug, holding_side, decided_at from paper_decisions
         where decided_at >= now() - interval '120 days' and us_market_slug is not null
           and holding_side is not null and coalesce(p_blended, p_pinnacle, p_internal) is not null
         order by decided_at desc limit 20000) x;
\echo == E2. contracts with a settled outcome row: whole window versus newest 20000
with win as (select us_market_slug s, holding_side h, decided_at from paper_decisions
              where decided_at >= now() - interval '120 days' and us_market_slug is not null
                and holding_side is not null and coalesce(p_blended, p_pinnacle, p_internal) is not null),
     allc as (select s, h from win group by s, h),
     newest as (select s, h from (select s, h, decided_at from win order by decided_at desc limit 20000) z group by s, h),
     settled as (select distinct us_market_slug s from external_valuations
                  where outcome_basis is not null and outcome_known is true
                    and us_market_slug in (select s from allc))
select (select count(*) from allc) contracts_window,
       (select count(*) from allc where s in (select s from settled)) settled_window,
       (select count(*) from newest) contracts_newest,
       (select count(*) from newest where s in (select s from settled)) settled_newest;
\echo == F. latest CALIBRATION model cells
with m as (select payload, observations, fitted_at from paper_profitability_models
            where kind = 'CALIBRATION' order by fitted_at desc, model_id desc limit 1)
select m.observations, m.fitted_at, c.key cell, (c.value->>'n')::int n, c.value->>'status' status,
       c.value->>'mean_p' mean_p, c.value->>'observed' observed, c.value->>'brier' brier
  from m, jsonb_each(m.payload->'cells') c order by n desc limit 60;
\echo == F2. calibration cells count by status
with m as (select payload from paper_profitability_models
            where kind = 'CALIBRATION' order by fitted_at desc, model_id desc limit 1)
select c.value->>'status' status, count(*) cells, sum((c.value->>'n')::int) obs
  from m, jsonb_each(m.payload->'cells') c group by 1;
\echo == G. latest RESIDUAL model cells
with m as (select payload, observations from paper_profitability_models
            where kind = 'RESIDUAL' order by fitted_at desc, model_id desc limit 1)
select m.observations, c.key cell, c.value->>'n' n, c.value->>'paper' paper, c.value->>'shadow' shadow,
       c.value->>'expected_ev_usd' expected, c.value->>'realized_pnl_usd' realized,
       c.value->>'residual_usd' residual, c.value->>'residual_per_contract' resid_pc
  from m, jsonb_each(m.payload->'cells') c order by (c.value->>'n')::int desc limit 30;
\echo == H. latest EXECUTION model
with m as (select payload from paper_profitability_models
            where kind = 'EXECUTION' order by fitted_at desc, model_id desc limit 1)
select m.payload->>'pooled_markout_per_contract' pooled, m.payload->>'observations' obs,
       c.key row_key, c.value->>'fills' fills, c.value->>'markout_per_contract_raw' mk_raw,
       c.value->>'markout_per_contract_shrunk' mk_shrunk, c.value->>'terminal_orders' term_orders,
       c.value->>'fill_rate_raw' fill_rate, c.value->>'fill_probability' fill_prob, c.value->>'status' status
  from m, jsonb_each(m.payload->'by_strategy_style') c order by 3;
\echo == I. latest MANAGEMENT model
with m as (select payload from paper_profitability_models
            where kind = 'MANAGEMENT' order by fitted_at desc, model_id desc limit 1)
select c.key strategy, c.value->>'closed' closed, c.value->>'exited' exited, c.value->>'exit_rate_raw' exit_rate_raw,
       c.value->>'exit_rate' exit_rate, c.value->>'exit_cost_measured' cost_n, c.value->>'exit_cost_per_contract' exit_cost_pc
  from m, jsonb_each(m.payload->'by_strategy') c order by 1;
\echo == J. entry refusal census by stage and refusal (top 40)
select stage, left(refusal, 90) refusal, count(*) n from paper_entry_refusal_census
 group by 1,2 order by n desc limit 40;
