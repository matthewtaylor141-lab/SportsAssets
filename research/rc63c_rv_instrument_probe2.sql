-- RC6.3c PROOF INSTRUMENTS, INDEPENDENT VERIFICATION PROBE (SELECT only; nothing here writes).
--
-- Checks the premises the five proof files rest on, against the production schema and state:
--   Q1  every table whose name contains "paper", with its row estimate and its BEFORE-rewrite / BEFORE-removal
--       triggers (tgtype bits: 2 = BEFORE, 8 = removal, 16 = rewrite, 32 = whole-table removal) -- the
--       fingerprint's "database append-only" claim, and whether a table exists that the fingerprint does not name
--   Q2  the cutoff columns the fingerprint filters on: data type, nullability and default (an immutable creation
--       clock; a nullable one leaves rows out of the digest)
--   Q3  the scalar columns of paper_decisions / paper_xavier_reviews the fingerprint omits (must be jsonb payloads)
--   Q4  other tables that may hold PAPER history (handoff, canonical, counterfactual, ledger, settlement, fill)
--   Q5  runtime_loop_health in full: which PROCESS runs ext_pinnacle.servicing (the paper pass's scheduler) and
--       the paper loops, each process's commit, and whether a boot instant is recorded per process
--   Q6  ingestion_state boot / servicing / paper keys (the window anchor the pass-window proof uses is
--       workers_boot; the paper pass runs in whichever process services)
--   Q7  the servicing heartbeat's top-level keys and its cadence digest (C10's source)
--   Q8  the paper heartbeat age and the health counters now
--   Q9  candidate real-money / venue tables that Proof D's D6 does not name: rows all time and the newest row
--   Q10 canonical_intent_executions adapter / mode NULLs (D6's <> filters are NULL-blind) and the distinct pairs
--   Q11 paper_settlements constraints (the outcome set) and paper_orders roles / states present
--   Q12 the digest formula run twice in one file on paper_fills (stability across statements)
\echo Q1 tables named paper*: rows (estimate), BEFORE triggers on rewrite / removal / whole-table removal, trigger names
SELECT c.relname AS tbl, c.reltuples::bigint AS est_rows,
       (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND (t.tgtype & 2) <> 0 AND (t.tgtype & 16) <> 0) AS before_rewrite_trg,
       (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND (t.tgtype & 2) <> 0 AND (t.tgtype & 8) <> 0) AS before_removal_trg,
       (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND (t.tgtype & 2) <> 0 AND (t.tgtype & 32) <> 0) AS before_whole_removal_trg,
       (SELECT string_agg(t.tgname, ',' ORDER BY t.tgname) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal) AS triggers
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public' AND c.relname LIKE '%paper%'
 ORDER BY 1;

\echo Q2 the fingerprint cutoff columns: type, nullable, default
SELECT table_name, column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name LIKE 'paper%'
   AND column_name IN ('recorded_at', 'created_at', 'committed_at', 'at', 'classified_at', 'decided_at', 'started_at',
                       'filled_at', 'settled_at', 'finished_at', 'resolved_at', 'terminal_at', 'found_at', 'generated_at')
 ORDER BY 1, 2;

\echo Q3 paper_decisions and paper_xavier_reviews: every column with its type (the fingerprint hashes the scalar ones only)
SELECT table_name, column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name IN ('paper_decisions', 'paper_xavier_reviews')
 ORDER BY 1, ordinal_position;

\echo Q4 other tables that may hold PAPER history (name match), with row estimates and BEFORE rewrite / removal triggers
SELECT c.relname AS tbl, c.reltuples::bigint AS est_rows,
       (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND (t.tgtype & 2) <> 0 AND (t.tgtype & 16) <> 0) AS before_rewrite_trg,
       (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND (t.tgtype & 2) <> 0 AND (t.tgtype & 8) <> 0) AS before_removal_trg
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public' AND c.relname NOT LIKE '%paper%'
   AND (c.relname LIKE '%handoff%' OR c.relname LIKE 'canonical_%' OR c.relname LIKE '%counterfactual%'
        OR c.relname LIKE '%settlement%' OR c.relname LIKE '%ledger%' OR c.relname LIKE '%fill%' OR c.relname LIKE 'derek_%'
        OR c.relname LIKE 'audrey_%' OR c.relname LIKE '%xavier%')
 ORDER BY 1;

\echo Q5 runtime_loop_health in full: process per loop, commit, starts / successes / errors, last instants, detail
SELECT loop_name, process, left(commit_sha, 12) AS commit_sha, round(cadence_s::numeric, 0) AS cadence_s, starts, successes, errors,
       to_char(last_start_at, 'MM-DD HH24:MI:SS') AS last_start, to_char(last_success_at, 'MM-DD HH24:MI:SS') AS last_success,
       to_char(last_error_at, 'MM-DD HH24:MI:SS') AS last_error_at, left(last_error, 80) AS last_error, pid, left(host, 24) AS host,
       to_char(updated_at, 'MM-DD HH24:MI:SS') AS updated, left(detail::text, 160) AS detail
  FROM runtime_loop_health ORDER BY process, loop_name;

\echo Q6 ingestion_state keys naming a boot, the servicing task or the paper session
SELECT key, pg_column_size(value) AS bytes, left(value::text, 240) AS value_head
  FROM ingestion_state
 WHERE key LIKE '%boot%' OR key LIKE '%servic%' OR key LIKE 'paper_%' OR key LIKE '%_shadow'
 ORDER BY 1;

\echo Q7 the servicing heartbeat: top-level keys, writer, cadence digest
SELECT string_agg(k, ',' ORDER BY k) AS top_level_keys FROM ingestion_state, jsonb_object_keys(value) k WHERE key = 'ext_pinnacle_last_servicing';
SELECT value->'writer' AS writer, value->'servicing_cadence' AS servicing_cadence FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';

\echo Q8 the paper heartbeat age and the health counters now
SELECT to_char(now(), 'YYYY-MM-DD HH24:MI:SS') AS read_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written,
       round(extract(epoch FROM now()) - (value->>'written_at')::float8) AS hb_age_s,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS elapsed_s,
       (SELECT passes FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1) AS passes,
       (SELECT errors FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1) AS errors,
       (SELECT jsonb_array_length(recent_heartbeats) FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1) AS ring_len
  FROM ingestion_state WHERE key = 'paper_session_last_pass';

\echo Q9 candidate venue / real-money tables not named by D6: rows all time and the newest row where a clock is known
SELECT 'bettor_desk_fills' AS tbl, count(*) AS rows_all_time, to_char(max(created_at), 'YYYY-MM-DD HH24:MI') AS newest FROM bettor_desk_fills
UNION ALL SELECT 'bettor_desk_order_events', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM bettor_desk_order_events
UNION ALL SELECT 'execmirror_events', count(*), to_char(max(at), 'YYYY-MM-DD HH24:MI') FROM execmirror_events
UNION ALL SELECT 'bettor_standing_order_events', count(*), to_char(max(recorded_at), 'YYYY-MM-DD HH24:MI') FROM bettor_standing_order_events
UNION ALL SELECT 'bettor_standing_order_plans', count(*), NULL FROM bettor_standing_order_plans
UNION ALL SELECT 'bettor_xavier_execution_events', count(*), NULL FROM bettor_xavier_execution_events
UNION ALL SELECT 'canonical_management_intents', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM canonical_management_intents
UNION ALL SELECT 'kalshi_shadow_intents', count(*), to_char(max(planned_at), 'YYYY-MM-DD HH24:MI') FROM kalshi_shadow_intents
UNION ALL SELECT 'smalllive_handoffs', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM smalllive_handoffs
UNION ALL SELECT 'manual_kalshi_queue', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM manual_kalshi_queue
UNION ALL SELECT 'bettor_live_journal', count(*), to_char(max(written_at), 'YYYY-MM-DD HH24:MI') FROM bettor_live_journal
UNION ALL SELECT 'pinnapi_reactive_attempts', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM pinnapi_reactive_attempts
UNION ALL SELECT 'execution_intents', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM execution_intents
UNION ALL SELECT 'canonical_intent_executions', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM canonical_intent_executions
ORDER BY 1;

\echo Q10 canonical_intent_executions: adapter / mode pairs and NULLs (D6 filters with <>, which is NULL-blind)
SELECT coalesce(adapter, '<NULL>') AS adapter, coalesce(mode, '<NULL>') AS mode, count(*) AS rows_n, to_char(max(created_at), 'YYYY-MM-DD HH24:MI') AS newest
  FROM canonical_intent_executions GROUP BY 1, 2 ORDER BY 1, 2;

\echo Q11 paper_settlements constraints and the paper_orders roles / states present
SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conrelid = 'paper_settlements'::regclass ORDER BY 1;
SELECT role, state, count(*) AS orders, count(*) FILTER (WHERE decision_id IS NULL) AS without_decision_id FROM paper_orders GROUP BY 1, 2 ORDER BY 1, 2;

\echo Q12 the digest formula twice in one file (paper_fills before 2026-10-10 19:00:00+00): the two rows must be identical
SELECT 'run1' AS run, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest
  FROM (SELECT md5(t::text) AS h FROM paper_fills t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x;
SELECT 'run2' AS run, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest
  FROM (SELECT md5(t::text) AS h FROM paper_fills t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x;
