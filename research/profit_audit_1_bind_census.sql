-- profitability-controls root-cause audit part 1: did the entry bind bind?
-- SELECT only. Source tables: paper_profitability_evaluations (migration 309),
-- paper_cash_decisions, paper_profitability_models, paper_decisions.
\echo == A. evaluations by stage and verdict (all time)
select stage, verdict, count(*) n, min(evaluated_at) first_at, max(evaluated_at) last_at
  from paper_profitability_evaluations group by 1,2 order by 1,2;
\echo == B. evaluation refusal census (all time)
select refusal, count(*) n, count(distinct strategy) strategies,
       min(evaluated_at) first_at, max(evaluated_at) last_at
  from paper_profitability_evaluations where refusal is not null
 group by 1 order by n desc;
\echo == C. evaluation refusal by stage and strategy (last 7 days)
select stage, strategy, verdict, left(coalesce(refusal,'-'),80) refusal, count(*) n
  from paper_profitability_evaluations
 where evaluated_at > now() - interval '7 days'
 group by 1,2,3,4 order by n desc limit 120;
\echo == D. profitability models fitted
select kind, version, count(*) n, max(fitted_at) last_fit, min(fitted_at) first_fit, max(observations) max_obs
  from paper_profitability_models group by 1,2 order by 1,2;
\echo == E. cash decisions census (per strategy)
select strategy, count(*) passes, sum(decisions_evaluated) evaluated, min(pass_at) first_at, max(pass_at) last_at
  from paper_cash_decisions group by 1 order by passes desc;
\echo == F. paper_decisions verdict census last 30 days
select date_trunc('day', decided_at)::date d, verdict, count(*) n
  from paper_decisions where decided_at > now() - interval '30 days'
 group by 1,2 order by 1 desc,2 limit 80;
