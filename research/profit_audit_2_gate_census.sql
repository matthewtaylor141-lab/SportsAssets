-- profitability-controls root-cause audit part 2: which gate refused what, and
-- which strategies hold the authority to enter. SELECT only.
\echo == A. strategy lifecycle events (every transition)
select event_id, strategy, from_state, to_state, rule_id, actor, recorded_at,
       left(why, 160) why
  from paper_strategy_lifecycle_events order by event_id;
\echo == B. paper_control rows
select control_key, enabled, updated_by, updated_at, left(coalesce(why,'-'), 140) why
  from paper_control order by control_key;
\echo == C. ENTRY orders per day and strategy (last 14 days)
select date_trunc('day', created_at)::date d, strategy, count(*) orders,
       count(*) filter (where filled_qty > 0) filled_orders
  from paper_orders where role = 'ENTRY' and created_at > now() - interval '14 days'
 group by 1,2 order by 1 desc, 2;
\echo == D. ENTER decisions and the entry orders they produced (last 8 days)
select date_trunc('day', d.decided_at)::date day, d.strategy, count(distinct d.decision_id) enter_decisions,
       count(distinct o.order_id) entry_orders
  from paper_decisions d
  left join paper_orders o on o.decision_id = d.decision_id and o.role = 'ENTRY'
 where d.verdict = 'ENTER' and d.decided_at > now() - interval '8 days'
 group by 1,2 order by 1 desc, 2;
\echo == E. refusal census by strategy (last 3 days), first gate that bound
select strategy, left(coalesce(refusal,'(ENTER)'), 110) refusal, count(*) n
  from paper_decisions where decided_at > now() - interval '3 days'
 group by 1,2 order by n desc limit 90;
