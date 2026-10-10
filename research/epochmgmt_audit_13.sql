-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 13:
-- the priced settlement-difference policy in production decisions: which settlement refusals remain.
\echo == A refusals of the last 24 hours by code (top 25)
SELECT coalesce(refusal, 'NONE') AS refusal, strategy, count(*) AS decisions
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND decided_at >= now() - interval '24 hours' AND verdict = 'REFUSE'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25;

\echo == B settlement and difference codes among all refusals of the last 7 days
SELECT coalesce(refusal, 'NONE') AS refusal, count(*) AS decisions, min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND decided_at >= now() - interval '7 days'
   AND (refusal ILIKE '%SETTLEMENT%' OR refusal ILIKE '%DIFFERENCE%' OR refusal ILIKE '%VOID%')
 GROUP BY 1 ORDER BY 2 DESC LIMIT 25;

\echo == C ENTER decisions by strategy in the last 7 days, and how many became an order
SELECT d.strategy, count(*) AS enters,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)) AS with_an_order
  FROM paper_decisions d
 WHERE d.account_id = 'paper_acct_main' AND d.decided_at >= now() - interval '7 days' AND d.verdict = 'ENTER'
 GROUP BY 1 ORDER BY 2 DESC;

\echo == D the priced settlement-difference policy on stored valuations: valuations whose refusals name it, last 7 days
SELECT count(*) AS valuations,
       count(*) FILTER (WHERE refusals::text LIKE '%SETTLEMENT_DIFFERENCE%') AS naming_a_settlement_difference_code,
       count(*) FILTER (WHERE refusals::text LIKE '%SETTLEMENT_NOT_SUPPORTED%') AS naming_settlement_not_supported
  FROM external_valuations WHERE decided_at >= now() - interval '7 days';
