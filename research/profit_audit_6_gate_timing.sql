-- profitability-controls root-cause audit part 6: gate timing after the
-- ec8b892 deploy, the digital-twin population, the standing-protection churn
-- and the forward-economics inputs. SELECT only.
\echo == A. exploration ENTER decisions per hour since 2026-10-10 00:00Z (ec8b892 workers boot 03:52:40Z)
select date_trunc('hour', d.decided_at) hr, d.strategy, count(distinct d.decision_id) enter_decisions, count(distinct o.order_id) entry_orders
  from paper_decisions d left join paper_orders o on o.decision_id = d.decision_id and o.role = 'ENTRY'
 where d.verdict = 'ENTER' and d.decided_at >= timestamptz '2026-10-10 00:00:00+00'
 group by 1,2 order by 1,2;
\echo == B. digital twin fresh population by role state tif since 2026-10-07 14:56:26Z
select role, time_in_force tif, state, count(*) n, min(eligible_at) first_at, max(eligible_at) last_at
  from paper_orders where eligible_at > timestamptz '2026-10-07 14:56:26+00'
 group by 1,2,3 order by 1,2,3;
\echo == C. twin history terminal IOC FOK ENTRY EXIT REDUCE orders by day
select date_trunc('day', eligible_at)::date d, role, count(*) n
  from paper_orders where time_in_force in ('IOC','FOK') and terminal_at is not null and role in ('ENTRY','EXIT','REDUCE')
 group by 1,2 order by 1,2;
\echo == D. standing protection churn per group
select count(distinct group_id) groups, count(*) orders, round(count(*)::numeric / nullif(count(distinct group_id),0), 2) orders_per_group,
       count(*) filter (where state = 'FILLED') filled, count(*) filter (where state = 'PARTIALLY_FILLED') partial,
       count(*) filter (where state = 'EXPIRED') expired, count(*) filter (where state = 'CANCELED') canceled,
       count(*) filter (where state in ('RESTING','CANCEL_PENDING')) live
  from paper_orders where role = 'STANDING_PROTECTION';
\echo == E. forward-economics inputs: shadow observations since the forward window by strategy
select s.strategy, count(*) settled_filled, round(sum(o.counterfactual_pnl_usd)::numeric,2) net_pnl,
       round(avg(o.counterfactual_pnl_usd)::numeric,2) mean_pnl,
       count(*) filter (where o.counterfactual_pnl_usd > 0) winners
  from paper_shadow_counterfactuals s join paper_shadow_counterfactual_outcomes o using (shadow_id)
 where s.decided_at >= to_timestamp(1791172800) and o.outcome not in ('NO_FILL','VOID_REFUND') and o.filled_qty > 0
 group by 1 order by 1;
\echo == F. the lifecycle state each strategy holds now
select strategy, state, rule_id, actor, recorded_at from paper_strategy_lifecycle_current_v order by strategy;
\echo == G. calibration of the probability the bind uses versus the market price (packet scoreboard says brier 0.1602 vs market 0.1599)
select count(*) n, round(avg(power(coalesce(d.p_blended, d.p_pinnacle, d.p_internal) - e.outcome, 2))::numeric, 5) brier_p
  from paper_decisions d
  join (select distinct on (us_market_slug) us_market_slug, outcome from external_valuations
         where outcome_known is true and outcome_basis is not null order by us_market_slug, id desc) e
    on e.us_market_slug = d.us_market_slug
 where d.verdict = 'ENTER' and d.holding_side = 'LONG' and coalesce(d.p_blended, d.p_pinnacle, d.p_internal) is not null
   and e.outcome in (0,1);
