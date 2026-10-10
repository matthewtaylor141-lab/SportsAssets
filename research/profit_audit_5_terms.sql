-- profitability-controls root-cause audit part 5: which learned cost terms make
-- the all-in EV non-positive, the control provenance of every evaluation, the
-- DIRECTIONAL residual by day and premap survival at fit time. SELECT only.
\echo == A. control provenance status across all evaluations (detail.controls)
select c.key control, c.value->>'status' status, count(*) n
  from paper_profitability_evaluations e, jsonb_each(e.detail->'controls') c
 where jsonb_typeof(e.detail->'controls') = 'object'
 group by 1,2 order by 1,2;
\echo == B. bind EV decomposition per contract by strategy (detail.attribution.terms)
select x.strategy, x.key term, count(*) evaluations, round(sum(x.v)::numeric, 2) usd, round(sum(x.q)::numeric, 0) contracts,
       round((sum(x.v) / nullif(sum(x.q), 0))::numeric, 5) per_contract
  from (select e.strategy, t.key, t.value::float8 v, (e.detail->'attribution'->>'filled_qty')::float8 q
          from paper_profitability_evaluations e, jsonb_each_text(e.detail->'attribution'->'terms') t
         where jsonb_typeof(e.detail->'attribution'->'terms') = 'object') x
 group by 1,2 order by 1,2;
\echo == C. percentiles of ev per contract and raw edge over market
select strategy, count(*) n,
       round((percentile_cont(0.1) within group (order by ev_per_contract_usd))::numeric, 4) ev_p10,
       round((percentile_cont(0.5) within group (order by ev_per_contract_usd))::numeric, 4) ev_p50,
       round((percentile_cont(0.9) within group (order by ev_per_contract_usd))::numeric, 4) ev_p90,
       round((percentile_cont(0.5) within group (order by p_raw - market_price))::numeric, 4) rawedge_p50,
       round((percentile_cont(0.9) within group (order by p_raw - market_price))::numeric, 4) rawedge_p90,
       round(avg(calibration_weight)::numeric, 4) avg_w
  from paper_profitability_evaluations where ev_per_contract_usd is not null
 group by 1;
\echo == D. DIRECTIONAL residual by decision day
select (to_timestamp((detail->>'decided_at')::float8))::date day_d, count(*) positions,
       round(sum(model_edge_usd)::numeric,2) model_edge, round(sum(outcome_variance_usd)::numeric,2) variance,
       round(sum(realized_pnl_usd)::numeric,2) realized
  from intel_attribution
 where book = 'PAPER' and detail->>'identity_claimed' = 'true' and realized_pnl_usd is not null
 group by 1 order by 1;
\echo == E. premap survival: shadow slugs and settled window contracts
with s as (select distinct us_market_slug slug from paper_shadow_counterfactuals)
select count(*) shadow_slugs,
       count(*) filter (where exists (select 1 from us_premap p where p.market_slug = s.slug)) in_premap,
       count(*) filter (where exists (select 1 from us_premap p where p.market_slug = s.slug and p.game_start is not null)) with_start
  from s;
select count(*) premap_rows, min(updated_at) oldest, max(updated_at) newest,
       count(*) filter (where game_start is not null) with_start
  from us_premap;
with win as (select us_market_slug s, holding_side h from paper_decisions
              where decided_at >= now() - interval '120 days' and us_market_slug is not null
                and holding_side is not null and coalesce(p_blended, p_pinnacle, p_internal) is not null
              group by 1,2),
     settled as (select distinct us_market_slug s from external_valuations
                  where outcome_basis is not null and outcome_known is true
                    and us_market_slug in (select s from win))
select (select count(*) from win where s in (select s from settled)) settled_contracts,
       (select count(*) from win where s in (select s from settled)
           and exists (select 1 from us_premap p where p.market_slug = win.s)) settled_in_premap,
       (select count(*) from win where s in (select s from settled)
           and exists (select 1 from us_premap p where p.market_slug = win.s and p.game_start is not null)) settled_with_start;
\echo == F. cash decisions binding_refusals keys (last 7 days)
select k.key refusal, count(*) passes, sum((k.value)::text::numeric) hits
  from paper_cash_decisions c, jsonb_each(c.binding_refusals) k
 where c.pass_at > now() - interval '7 days' group by 1 order by hits desc limit 25;
