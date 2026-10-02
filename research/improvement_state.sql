-- Improvement-loop state (read-only): proposals, their events, the stale-measure
-- lesson series and the owner's set (b) research turns.
\echo '== I0 · proposals =='
SELECT proposal_id, agent_id, change_class, strategy, status, verdict, active,
       proposed_by, proposed_at, evaluation_start, evaluation_end,
       left(rationale, 300) AS rationale
  FROM paper_improvement_proposals ORDER BY proposed_at DESC LIMIT 20;
\echo '== I1 · proposal events =='
SELECT proposal_id, kind, actor, at, left(detail::text, 300) AS detail
  FROM paper_improvement_proposal_events ORDER BY event_id DESC LIMIT 20;
\echo '== I2 · lessons (latest per series) =='
SELECT DISTINCT ON (series_key) series_key, agent_id, version, learned_at
  FROM paper_agent_lessons ORDER BY series_key, version DESC LIMIT 40;
\echo '== I3 · set (b) turns =='
SELECT t.spec->>'source_key' AS flow, t.task_id, t.assignee, t.status,
       t.outcome->>'provider_mode' AS mode, t.updated_at
  FROM agent_tasks t
 WHERE t.kind = 'AGENT_CAPABILITY_REVIEW_V1'
   AND t.spec->>'source_key' LIKE '%owner-20261002b-%'
 ORDER BY t.updated_at;
\echo '== I4 · learning activation control =='
SELECT control_key, enabled, updated_by, updated_at FROM paper_control
 WHERE control_key LIKE 'PAPER_LEARNING%';
