-- READ-ONLY. THE THREE-AGENT FOUNDATION RELEASE, READ BACK FROM PRODUCTION.
--
-- A1  serving build (cycle + servicing heartbeats) and the migration ledger
-- A2  the three identities, their current status, heartbeat age, cadence,
--     runs / errors, and what each is waiting on
-- A3  agent runs by agent and outcome (last 24 h) -- scheduled execution
-- A4  tasks by assignee / kind / status, and the newest ten
-- A5  Derek: coverage census (newest), entry decisions by verdict / refusal
-- A6  Xavier: responsibility handoffs, decisions by action (last 7 d)
-- A7  Audrey: audit reports (newest five), directives by status
-- A8  improvement: candidates by class / state (artifact attached or not)
-- A9  market-data subscription and servicing digests from the heartbeats
-- A10 funded activity (none is expected; funded submission stays disabled)
--
-- Nothing here selects a credential, a balance or a message body.

\echo '== A1 · serving build and migrations =='
SELECT key, to_timestamp((value->>'at')::float8) AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1)
           AS age_s,
       value->'writer'->>'build' AS writer_build, value->>'state' AS state
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_servicing');
SELECT version, applied_at FROM schema_migrations
 ORDER BY version DESC LIMIT 8;

\echo '== A2 · identities and status =='
SELECT i.agent_id, i.policy_version, i.model_version, i.code_version,
       s.state, s.activity, s.last_heartbeat_at,
       round(extract(epoch FROM now() - s.last_heartbeat_at)::numeric, 1)
           AS heartbeat_age_s,
       s.runs, s.errors, left(s.last_error, 160) AS last_error,
       s.waiting_on, s.cadence
  FROM agent_identities i LEFT JOIN agent_status s USING (agent_id)
 ORDER BY i.agent_id;

\echo '== A3 · runs in the last 24 h =='
SELECT agent_id, coalesce(outcome, 'RUNNING') AS outcome, count(*) AS runs,
       min(started_at) AS first, max(started_at) AS last
  FROM agent_runs WHERE started_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== A4 · tasks =='
SELECT assignee, kind, status, count(*) AS tasks
  FROM agent_tasks GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
SELECT task_id, assignee, kind, status, directive_id, created_at
  FROM agent_tasks ORDER BY created_at DESC LIMIT 10;

\echo '== A5 · Derek: coverage and entry decisions =='
SELECT census_id, at, categories, blocked_by_reason
  FROM derek_coverage_census ORDER BY at DESC LIMIT 1;
SELECT verdict, refusal, count(*) AS decisions,
       min(decided_at) AS first, max(decided_at) AS last
  FROM derek_entry_decisions GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo '== A6 · Xavier: handoffs and decisions (7 d) =='
SELECT owner_agent, count(*) AS handoffs, sum(confirmed_qty) AS confirmed_qty,
       sum(outstanding_qty) AS outstanding_qty
  FROM agent_position_handoffs GROUP BY 1;
SELECT coalesce(chosen_action, 'NONE') AS chosen_action, count(*) AS decisions,
       max(decided_at) AS last
  FROM bettor_xavier_decisions
 WHERE decided_at > now() - interval '7 days' GROUP BY 1 ORDER BY 2 DESC;

\echo '== A7 · Audrey: audits and directives =='
SELECT report_id, version, audit_day, computed_at, summary
  FROM audrey_audit_reports ORDER BY computed_at DESC LIMIT 5;
SELECT status, objective_kind, assigned_agent, count(*) AS directives
  FROM management_directives GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo '== A8 · improvement candidates =='
SELECT change_class, state, (artifact_ref IS NOT NULL) AS has_artifact,
       count(*) AS candidates
  FROM improvement_candidates GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== A9 · subscription and servicing digests =='
SELECT jsonb_pretty(value->'market_subscription') AS market_subscription
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT jsonb_pretty(value->'servicing_cadence') AS servicing_cadence
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';

\echo '== A10 · funded activity =='
SELECT (SELECT count(*) FROM bettor_funded_intents)   AS funded_intents,
       (SELECT count(*) FROM bettor_funded_fills)     AS funded_fills,
       (SELECT count(*) FROM bettor_xavier_decisions) AS xavier_decisions_all;
