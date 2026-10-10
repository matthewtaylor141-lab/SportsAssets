-- profitability-controls root-cause audit part 7: does a recorded expected EV
-- exist for the PAPER positions the residual feedback needs. SELECT only.
\echo == A. economics keys of ENTER decisions that became an ENTRY order, by strategy
select d.strategy, k.key, count(*) n
  from paper_decisions d, jsonb_object_keys(d.economics) k(key)
 where d.verdict = 'ENTER' and d.economics is not null and jsonb_typeof(d.economics) = 'object'
   and exists (select 1 from paper_orders o where o.decision_id = d.decision_id and o.role = 'ENTRY')
 group by 1,2 order by 1, n desc, 2;
\echo == B. SUBMITTED event detail keys of ENTRY orders (capital_authority present or not)
select o.strategy, date_trunc('day', o.created_at)::date d, count(*) orders,
       count(*) filter (where e.detail ? 'capital_authority') with_capital_authority,
       count(*) filter (where e.detail->'capital_authority' ? 'total_executable_ev_usd') with_ev
  from paper_orders o left join paper_order_events e on e.order_id = o.order_id and e.kind = 'SUBMITTED'
 where o.role = 'ENTRY' and o.direction = 'BUY' and o.filled_qty > 0
 group by 1,2 order by 2,1;
\echo == C. one economics sample (keys with scalar values) for an exploration ENTER
select jsonb_pretty(d.economics - 'levels' - 'fills') econ_sample
  from paper_decisions d
 where d.verdict = 'ENTER' and d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   and exists (select 1 from paper_orders o where o.decision_id = d.decision_id and o.role = 'ENTRY')
 order by d.decided_at desc limit 1;
