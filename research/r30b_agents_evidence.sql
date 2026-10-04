-- R30B AGENTS STREAM -- production evidence, READ ONLY (SELECT only).
--
-- Why it exists: the R30B agents stream builds (17) durable work queues for
-- all seven agents, (18) agent scorecards by decision quality, (19) memory
-- usefulness and (20) root-cause clustering of repeated Karen / Audrey
-- findings. Its tests must use the row SHAPES production writes, and the
-- clustering must be checked against the real repetition (the R30 baseline
-- recorded 666 HOLD_ON_STALE_PROBABILITY challenges UPHELD). This file reads:
--   (A) Karen's challenges per detector / target / state with first and last
--       seen, and the stale-HOLD rule's own defect rate per day (the
--       measured-effect baseline a linked fix will be compared against);
--   (B) one upheld stale-HOLD challenge and its target review, field shapes
--       only (no free text beyond the recorded claim);
--   (C) Audrey's findings per kind / severity and how many have no task;
--   (D) the improvement pipeline's items per source (one per challenge?);
--   (E) Derek's stale-evidence refusals (the candidates awaiting fresh
--       evidence), Eddie's backlog, Scout's features, the allocator's runs;
--   (F) the lesson stores (agent_memory_events, paper_agent_lessons) and the
--       lost-opportunity classes the scorecards join to;
--   (G) whether the R30 work-queue tables exist yet (226 is not deployed).

\echo '== A1 · karen_challenges by detector / target / state (all time) =='
SELECT detector, target_agent, state, count(*) AS n,
       count(DISTINCT target_id) AS targets,
       min(record_at) AS first_record_at, max(record_at) AS last_record_at,
       min(challenged_at) AS first_challenged, max(challenged_at) AS last_challenged
  FROM karen_challenges GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo '== A2 · stale-HOLD rule (karen_runner.RULES) over paper_xavier_reviews per UTC day, 21 d =='
SELECT date_trunc('day', reviewed_at) AS day, count(*) AS reviews,
       count(*) FILTER (WHERE recommendation = 'HOLD') AS holds,
       count(*) FILTER (WHERE recommendation = 'HOLD' AND (measure->>'stale' = 'true' OR (
           jsonb_typeof(measure->'probability_age_s') = 'number'
           AND jsonb_typeof(measure->'probability_limit_s') = 'number'
           AND (measure->>'probability_age_s')::numeric > (measure->>'probability_limit_s')::numeric))) AS stale_holds,
       count(*) FILTER (WHERE recommendation = 'WAITING_FOR_FRESH_EVIDENCE') AS waiting
  FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '21 days'
 GROUP BY 1 ORDER BY 1;

\echo '== A3 · upheld stale-HOLD challenges: groups / strategies / markets affected =='
SELECT count(*) AS upheld, count(DISTINCT r.group_id) AS groups,
       count(DISTINCT o.strategy) AS strategies,
       count(DISTINCT o.us_market_slug) AS markets,
       min(k.record_at) AS first_seen, max(k.record_at) AS last_seen
  FROM karen_challenges k
  JOIN paper_xavier_reviews r ON r.review_id = k.target_id
  LEFT JOIN LATERAL (SELECT po.strategy, po.us_market_slug FROM paper_orders po
                      WHERE po.group_id = r.group_id AND po.role = 'ENTRY'
                      ORDER BY po.created_at LIMIT 1) o ON true
 WHERE k.detector = 'HOLD_ON_STALE_PROBABILITY' AND k.state = 'UPHELD';
SELECT o.strategy, count(*) AS upheld, count(DISTINCT r.group_id) AS groups
  FROM karen_challenges k
  JOIN paper_xavier_reviews r ON r.review_id = k.target_id
  LEFT JOIN LATERAL (SELECT po.strategy FROM paper_orders po
                      WHERE po.group_id = r.group_id AND po.role = 'ENTRY'
                      ORDER BY po.created_at LIMIT 1) o ON true
 WHERE k.detector = 'HOLD_ON_STALE_PROBABILITY' AND k.state = 'UPHELD'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

\echo '== B1 · one upheld stale-HOLD challenge (shape) =='
SELECT challenge_id, target_agent, target_kind, target_id, detector, severity,
       state, response_stance, responded_by, resolved_by, outcome,
       record_at, challenged_at, responded_at, resolved_at, account_id,
       jsonb_typeof(evidence_refs) AS refs_type, evidence_refs,
       (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(body) k) AS body_keys,
       improvement_finding_id, improvement_proposal_id, finding_id
  FROM karen_challenges WHERE detector = 'HOLD_ON_STALE_PROBABILITY'
   AND state = 'UPHELD' ORDER BY challenged_at DESC LIMIT 1;
\echo '== B2 · its target review (shape) =='
SELECT r.review_id, r.account_id, r.group_id, r.reviewed_at, r.recommendation,
       (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(r.measure) k) AS measure_keys,
       r.measure->>'stale' AS stale, r.measure->>'probability_age_s' AS age_s,
       r.measure->>'probability_limit_s' AS limit_s,
       r.measure->>'evidence_state' AS evidence_state
  FROM paper_xavier_reviews r WHERE r.review_id = (
       SELECT target_id FROM karen_challenges WHERE detector = 'HOLD_ON_STALE_PROBABILITY'
          AND state = 'UPHELD' ORDER BY challenged_at DESC LIMIT 1);
\echo '== B3 · karen_challenges columns =='
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'karen_challenges' ORDER BY ordinal_position;

\echo '== C1 · paper_audrey_findings by kind / severity, 14 d =='
SELECT kind, severity, count(*) AS n,
       count(*) FILTER (WHERE improvement_task_id IS NULL) AS no_task,
       count(DISTINCT subject) AS subjects, min(found_at) AS first_seen,
       max(found_at) AS last_seen
  FROM paper_audrey_findings WHERE found_at > now() - interval '14 days'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;

\echo '== D1 · improve_items per source kind and stage =='
SELECT source_kind, stage, count(*) FROM improve_items GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;
\echo '== D2 · Karen-sourced improve_items per detector =='
SELECT k.detector, count(*) AS items FROM improve_items i
  JOIN karen_challenges k ON k.challenge_id = i.source_key
 WHERE i.source_kind = 'KAREN_UPHELD_CHALLENGE' GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

\echo '== E1 · stale / unknown-freshness refusals (Derek candidates awaiting fresh evidence), 24 h =='
SELECT r AS refusal, strategy, count(*) AS rows, count(DISTINCT us_market_slug) AS markets,
       max(decided_at) AS last_at
  FROM paper_decisions, unnest(refusals) r
 WHERE decided_at > now() - interval '24 hours'
   AND (r LIKE '%STALE%' OR r LIKE '%FRESH%')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
\echo '== E2 · Eddie: ENTER without an estimate (2 d), estimates without an outcome =='
SELECT (SELECT count(*) FROM paper_decisions d WHERE d.verdict = 'ENTER'
          AND d.decided_at > now() - interval '2 days'
          AND NOT EXISTS (SELECT 1 FROM eddie_execution_estimates e
                           WHERE e.decision_id = d.decision_id)) AS enter_without_estimate,
       (SELECT count(*) FROM eddie_execution_estimates e WHERE NOT EXISTS (
          SELECT 1 FROM eddie_execution_outcomes x WHERE x.estimate_id = e.estimate_id)) AS estimates_without_outcome,
       (SELECT count(*) FROM eddie_execution_outcomes) AS outcomes;
\echo '== E3 · Scout features by state; tournaments by verdict =='
SELECT state, count(*) FROM scout_features GROUP BY 1 ORDER BY 2 DESC;
SELECT coalesce(verdict, '(open)') AS verdict, count(*) FROM scout_feature_tournaments GROUP BY 1;
\echo '== E4 · allocator runs (latest 3) and ENTER decisions after the latest =='
SELECT run_id, component, status, started_at, finished_at FROM intel_runs
 WHERE component = 'ALLOCATOR' ORDER BY started_at DESC LIMIT 3;
\echo '== E5 · agent_tasks OPEN by assignee; agent_status =='
SELECT assignee, status, count(*) FROM agent_tasks GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
SELECT agent_id, state, activity, last_heartbeat_at FROM agent_status ORDER BY 1;

\echo '== F1 · agent_memory_events by agent / kind; superseded =='
SELECT agent_id, memory_kind, count(*) AS n,
       count(*) FILTER (WHERE superseded_by IS NOT NULL) AS superseded,
       min(learned_at) AS first, max(learned_at) AS last
  FROM agent_memory_events GROUP BY 1, 2 ORDER BY 1, 2;
SELECT agent_id, deriver, subject_type, count(*) FROM agent_memory_events
 WHERE memory_kind = 'LESSON' GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;
\echo '== F2 · paper_agent_lessons by agent / kind / strategy (latest versions) =='
SELECT agent_id, kind, coalesce(strategy, '(none)') AS strategy, count(*) AS series,
       max(learned_at) AS last
  FROM paper_agent_lessons l WHERE NOT EXISTS (
       SELECT 1 FROM paper_agent_lessons n WHERE n.account_id = l.account_id
          AND n.series_key = l.series_key AND n.version > l.version)
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25;
\echo '== F3 · lol_ledger classes by strategy (latest per decision) =='
SELECT strategy, classification, count(*) FROM (
  SELECT DISTINCT ON (decision_ref) decision_ref, strategy, classification
    FROM lol_ledger ORDER BY decision_ref, classified_at DESC, ledger_id DESC) q
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
\echo '== F4 · paper sleeves (223) per sleeve =='
SELECT sleeve, count(*) FROM paper_sleeve_classifications GROUP BY 1 ORDER BY 2 DESC;

\echo '== G1 · R30 tables present? =='
SELECT to_regclass('agent_work_requests') AS awr, to_regclass('xavier_current_review') AS xcr,
       to_regclass('live_parity_cutover') AS cutover;
