-- READ ONLY. Frontend root-cause audit (group frontend), part F: how many
-- recorded ENTER decisions carry a paper order per day, and which named
-- findings the system recorded about the ones that do not. SELECT only.
\echo == F1. ENTER decisions per day and strategy, with a paper order ==
SELECT date_trunc('day', d.decided_at) AS day, d.strategy,
       count(*) AS enter,
       count(o.order_id) AS with_order
  FROM paper_decisions d
  LEFT JOIN paper_orders o ON o.decision_id = d.decision_id
 WHERE d.verdict = 'ENTER' AND d.decided_at > now() - interval '5 days'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2;

\echo == F2. paper audit findings by kind since 2026-10-08 ==
SELECT kind, severity, count(*) AS n, min(found_at) AS first_found, max(found_at) AS last_found
  FROM paper_audrey_findings WHERE found_at > timestamptz '2026-10-08 00:00:00+00'
 GROUP BY kind, severity ORDER BY n DESC LIMIT 25;

\echo == F3. order events and terminal reasons of the last orders ==
SELECT order_id, role, state, terminal_reason, created_at
  FROM paper_orders ORDER BY created_at DESC LIMIT 5;
