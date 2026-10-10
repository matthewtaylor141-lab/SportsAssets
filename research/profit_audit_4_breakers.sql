-- profitability-controls root-cause audit part 4: why the EXECUTION_ALPHA and
-- XAVIER_MANAGEMENT sleeves read DISABLED, and whether the premap descriptor
-- survives to fit time. SELECT only.
\echo == A. intel_attribution PAPER components by strategy (identity claimed)
select strategy, count(*) positions, round(sum(entry_qty)::numeric,0) qty,
       round(sum(model_edge_usd)::numeric,2) model_edge, round(sum(fees_usd)::numeric,2) fees,
       round(sum(slippage_usd)::numeric,2) slippage, round(sum(execution_edge_usd)::numeric,2) execution_edge,
       round(sum(management_usd)::numeric,2) management, round(sum(outcome_variance_usd)::numeric,2) outcome_variance,
       round(sum(settlement_usd)::numeric,2) settlement, round(sum(realized_pnl_usd)::numeric,2) realized
  from intel_attribution
 where book = 'PAPER' and detail->>'identity_claimed' = 'true' and realized_pnl_usd is not null
 group by rollup(strategy) order by 1;
\echo == B. execution edge sign: positions where execution edge is positive versus fees paid
select count(*) positions, count(*) filter (where execution_edge_usd > 0) exec_pos,
       count(*) filter (where slippage_usd < 0) price_improved, count(*) filter (where slippage_usd = 0) zero_slip,
       count(*) filter (where slippage_usd > 0) paid_up,
       round(avg(fees_usd)::numeric,3) avg_fee, round(avg(slippage_usd)::numeric,3) avg_slip,
       round(avg(execution_edge_usd)::numeric,3) avg_exec_edge
  from intel_attribution
 where book = 'PAPER' and detail->>'identity_claimed' = 'true' and realized_pnl_usd is not null;
\echo == C. management by whether Xavier acted and by the settlement outcome
select coalesce(detail->>'management_basis','?') basis, coalesce(detail->>'settlement_outcome','?') outcome,
       count(*) n, round(sum(management_usd)::numeric,2) management, round(sum(entry_qty)::numeric,0) qty
  from intel_attribution
 where book = 'PAPER' and detail->>'identity_claimed' = 'true' and realized_pnl_usd is not null
 group by 1,2 order by 1,2;
\echo == D. management by sell phase relative to the bind cutover 2026-10-06 17:40:42Z
with sf as (select group_id, us_market_slug, holding_side, min(filled_at) first_sell, max(filled_at) last_sell
              from paper_fills where direction = 'SELL' group by 1,2,3)
select case when sf.last_sell < timestamptz '2026-10-06 17:40:42+00' then 'ALL_SELLS_BEFORE_BIND'
            when sf.first_sell >= timestamptz '2026-10-06 17:40:42+00' then 'ALL_SELLS_AFTER_BIND'
            else 'MIXED' end phase,
       count(*) positions, round(sum(a.management_usd)::numeric,2) management, round(sum(a.entry_qty)::numeric,0) qty,
       min(sf.first_sell) first_sell, max(sf.last_sell) last_sell
  from intel_attribution a join sf on sf.group_id = a.subject_id
 where a.book = 'PAPER' and a.detail->>'identity_claimed' = 'true' and a.realized_pnl_usd is not null
 group by 1 order by 1;
\echo == E. sell order roles that closed positions, with management
with so as (select group_id, array_to_string(array_agg(distinct role order by role), '+') roles
              from paper_orders where direction = 'SELL' and filled_qty > 0 group by group_id)
select so.roles, count(*) positions, round(sum(a.management_usd)::numeric,2) management, round(sum(a.entry_qty)::numeric,0) qty
  from intel_attribution a join so on so.group_id = a.subject_id
 where a.book = 'PAPER' and a.detail->>'identity_claimed' = 'true' and a.realized_pnl_usd is not null
 group by 1 order by 3;
\echo == F. xavier review action kinds (top)
select coalesce(action->>'kind', action->>'type', action->>'action', '(keys: ' || (select string_agg(k, ',') from jsonb_object_keys(action) k) || ')') kind,
       count(*) n from paper_xavier_reviews where action is not null and jsonb_typeof(action) = 'object'
 group by 1 order by n desc limit 20;
\echo == G. DIRECTIONAL residual by entry day
select date_trunc('day', decided_at)::date day_d, count(*) positions, round(sum(model_edge_usd)::numeric,2) model_edge,
       round(sum(outcome_variance_usd)::numeric,2) variance, round(sum(realized_pnl_usd)::numeric,2) realized
  from intel_attribution
 where book = 'PAPER' and detail->>'identity_claimed' = 'true' and realized_pnl_usd is not null
 group by 1 order by 1;
\echo == H. premap presence for shadow slugs and settled window contracts
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
