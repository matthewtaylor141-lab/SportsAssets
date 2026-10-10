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
select date_trunc('day', d.decided_at)::date dy, d.strategy, count(distinct d.decision_id) enter_decisions,
       count(distinct o.order_id) entry_orders
  from paper_decisions d
  left join paper_orders o on o.decision_id = d.decision_id and o.role = 'ENTRY'
 where d.verdict = 'ENTER' and d.decided_at > now() - interval '8 days'
 group by 1,2 order by 1 desc, 2;
\echo == E. refusal census by strategy (last 3 days), first gate that bound
select strategy, left(coalesce(refusal,'(ENTER)'), 110) refusal, count(*) n
  from paper_decisions where decided_at > now() - interval '3 days'
 group by 1,2 order by n desc limit 90;
\echo == F. sample economic CASH_WAIT evaluations (the only rows where the bind reached EV)
select eval_id, strategy, sport, market_family, regime, round(p_raw::numeric,4) p_raw, round(p_used::numeric,4) p_used,
       round(market_price::numeric,4) mkt, round(calibration_weight::numeric,4) w, round(qty_in::numeric,2) qty_in,
       round(all_in_ev_usd::numeric,4) all_in_ev, round(ev_per_contract_usd::numeric,5) ev_pc,
       round(ev_per_capital_hour::numeric,6) evch, round(fill_probability::numeric,3) fillp,
       round(residual_haircut_per_contract::numeric,5) resid_hc, round(learned_adverse_per_contract::numeric,5) adv,
       refusal
  from paper_profitability_evaluations where refusal like 'CASH_WAIT%'
 order by eval_id desc limit 12;
\echo == G. control provenance of one economic refusal and one quarantine refusal
select refusal, left(detail::text, 2200) detail
  from (select refusal, detail, row_number() over (partition by refusal order by eval_id desc) rn
          from paper_profitability_evaluations) x where rn = 1;
\echo == H. regime, sport and family spread of all evaluations
select regime, sport, market_family, strategy, count(*) n
  from paper_profitability_evaluations group by 1,2,3,4 order by n desc limit 40;
