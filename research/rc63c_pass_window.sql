-- RC6.3c PROOF C: PAPER PASS CONTINUITY OVER A WINDOW (SELECT only; nothing here writes).
--
-- PURPOSE. Prove at least 60 minutes of consecutive completed PAPER passes with zero cut or skipped steps and no
-- unexplained missed scheduled pass, from the records the pass itself writes (agents/paper_runtime.py on dd25c588,
-- bettor_paper_session.py record_pass):
--   * paper_session_health (one row per session): passes (+1 per pass that ran), errors (+1 per pass whose errors
--     dict was non-empty: a cut step PAPER_STEP_EXCEEDED_PASS_TIME, a skipped step PAPER_STEP_SKIPPED_PASS_TIME_SPENT,
--     a step that left a transaction open PAPER_STEP_LEFT_A_TRANSACTION_OPEN, a step that raised, a step that returned
--     an error, or a failed record -- every one of them is a key of `errors`, and record_pass adds 1 whenever the
--     joined error string is not NULL), heartbeat_at, last_pass (the digest), last_error, recent_heartbeats: the
--     ring of the newest 20 beats, each {at (the pass START, epoch seconds), ok (= no error), elapsed_s,
--     slowest_step, slowest_step_s, summary}. The ring is ordered by `at`, oldest first, and keeps 20 (SW-2 fix).
--   * ingestion_state key paper_session_last_pass: the heartbeat of the newest ATTEMPT, including attempts that did
--     not run (ran=false with a refusal: disabled, busy, PAPER_PASS_RAISED_OR_TIMED_OUT with in_step / steps_ended).
--     A pass cut by HARD_TIMEOUT_S (90 s) as a whole writes ran=false here and no health row.
--
-- CADENCE. The servicing task schedules one pass every SERVICING_INTERVAL_S = 60 s (workers/ext_pinnacle_loop.py:9353,
-- :9642 -> agents/runtime.py paper_pass_hook -> paper_runtime.schedule); the collection cycle and the held-review
-- schedulers also schedule passes, and a schedule while a pass runs is coalesced (never queued). So consecutive
-- beats are at most ~60 s apart when every pass completes within the interval; a gap over 90 s (1.5 x cadence) is
-- flagged below. A gap of up to ~150 s can still be a pass that ran long (HARD_TIMEOUT_S = 90 s) and swallowed one
-- scheduling tick: such a beat shows elapsed_s near 90 and ok=false. Anything longer is a missed scheduled pass
-- and needs an explanation (process restart = the deploy itself: workers_boot / runtime_loop_health commit changes).
--
-- HOW TO RUN AND COMBINE (the 60-minute proof). Run this file at deploy + ~22 min, + ~44 min and + ~66 min (three
-- reads R1, R2, R3). Each read gives the 20 newest beats (~19-20 minutes of 60 s passes) and the counters.
--   PASSES.  passes(R3) - passes(R1) = passes recorded in the ~44 min between the reads; it must be within +-2 of
--            (heartbeat_at(R3) - heartbeat_at(R1)) / 60 s. The beats of the three rings, de-duplicated by `at`, are
--            the passes seen; where more passes happened between two reads than a ring holds (20), the counter delta
--            covers the ones that fell out of the ring. No gap over 90 s inside any ring (C3 gaps_over_90s = 0 in
--            every read), and the first beat of ring N+1 at most 90 s after the last beat of ring N when the rings
--            overlap (they overlap when the reads are at most 19 minutes apart; at 22-minute spacing use the counter
--            delta between the reads for the 2-3 minutes between rings: delta must equal the beats seen plus the
--            number of 60 s slots not covered).
--   CUT / SKIPPED. errors(R3) - errors(R1) = 0 proves no pass in the window had ANY error: no cut step, no skipped
--            step, no transaction left open, no step raised (every one of those is counted). Every ring beat must
--            show ok=true. A cut step would show as: errors +1, that beat ok=false, exceeded_step naming the step,
--            errors key "<step>: PAPER_STEP_EXCEEDED_PASS_TIME ..."; a skipped step as errors +1, ok=false,
--            skipped_steps {step: PAPER_STEP_SKIPPED_PASS_TIME_SPENT}. last_error keeps the newest error string
--            (coalesce: it is NOT cleared by a clean pass), so compare errors COUNTS, not last_error.
--   MISSED.  C4 heartbeat ran=false with a refusal at any read, or a ring gap over 90 s, is a missed / cut pass.
--            The deploy restart itself is the one expected gap, before R1's window.
--   60 MIN.  The window proven = from the oldest beat of R1 to the newest beat of R3 (>= 60 min when R1 is at
--            deploy + 22 and R3 at deploy + 66), given the above.
--
-- PASS CRITERIA per read: C1 errors unchanged since the previous read; C3 not_ok_beats = 0, gaps_over_90s = 0,
-- beats_with_a_step_over_20s = 0 (the settle step is now subsecond), max_elapsed_s well under the 20 s pass budget;
-- C4 ran = true, exceeded_step NULL, skipped NULL, error_keys NULL; C5 settle and shadow_settlement under 1 s.

\echo C1 counters: session, passes, errors, mutation_attempts, heartbeat_at, last_error (kept, not cleared), read_at
SELECT session_id, passes, errors, mutation_attempts, to_char(heartbeat_at, 'YYYY-MM-DD HH24:MI:SS') AS heartbeat_at,
       left(last_error, 160) AS last_error_kept, to_char(now(), 'YYYY-MM-DD HH24:MI:SS') AS read_at,
       round(extract(epoch FROM now() - heartbeat_at)::numeric, 1) AS heartbeat_age_s
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 3;

\echo C2 the ring: every beat oldest first, with the gap to the previous beat and the 1.5x-cadence flag
WITH h AS (SELECT recent_heartbeats FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1),
ring AS (SELECT (b->>'at')::float8 AS at_s, b FROM h, jsonb_array_elements(h.recent_heartbeats) b),
g AS (SELECT at_s, b, at_s - lag(at_s) OVER (ORDER BY at_s) AS gap_s FROM ring)
SELECT to_char(to_timestamp(at_s), 'MM-DD HH24:MI:SS') AS at, b->>'ok' AS ok, round((b->>'elapsed_s')::numeric, 2) AS elapsed_s,
       b->>'slowest_step' AS slowest_step, round((b->>'slowest_step_s')::numeric, 2) AS slowest_s,
       round(gap_s::numeric, 1) AS gap_s, CASE WHEN gap_s > 90 THEN 'GAP_OVER_1.5X_CADENCE' END AS flag,
       b->'summary'->>'decisions_recorded' AS decisions, b->'summary'->>'orders_submitted' AS orders,
       b->'summary'->>'fills' AS fills, b->'summary'->>'reviews' AS reviews, b->'summary'->>'budget_exhausted' AS budget_exhausted
  FROM g ORDER BY at_s;

\echo C3 ring summary: beats, ok, gaps, span, elapsed
WITH h AS (SELECT recent_heartbeats FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1),
ring AS (SELECT (b->>'at')::float8 AS at_s, b FROM h, jsonb_array_elements(h.recent_heartbeats) b),
g AS (SELECT at_s, b, at_s - lag(at_s) OVER (ORDER BY at_s) AS gap_s FROM ring)
SELECT count(*) AS beats, count(*) FILTER (WHERE b->>'ok' = 'true') AS ok_beats, count(*) FILTER (WHERE b->>'ok' <> 'true') AS not_ok_beats,
       round(max(gap_s)::numeric, 1) AS max_gap_s, round(avg(gap_s)::numeric, 1) AS avg_gap_s,
       count(*) FILTER (WHERE gap_s > 90) AS gaps_over_90s, count(*) FILTER (WHERE gap_s > 180) AS gaps_over_180s,
       to_char(to_timestamp(min(at_s)), 'MM-DD HH24:MI:SS') AS first_at, to_char(to_timestamp(max(at_s)), 'MM-DD HH24:MI:SS') AS last_at,
       round((max(at_s) - min(at_s))::numeric, 0) AS span_s,
       round(max((b->>'elapsed_s')::numeric), 2) AS max_elapsed_s, round(avg((b->>'elapsed_s')::numeric), 2) AS avg_elapsed_s,
       count(*) FILTER (WHERE (b->>'slowest_step_s')::numeric >= 20) AS beats_with_a_step_over_20s,
       count(*) FILTER (WHERE (b->>'slowest_step_s')::numeric >= 5) AS beats_with_a_step_over_5s,
       (SELECT string_agg(DISTINCT r2.b->>'slowest_step', ',') FROM ring r2) AS slowest_steps_seen
  FROM g;

\echo C4 the newest attempt's heartbeat: ran, trigger, refusal, elapsed, cut step, skipped steps, error keys, in-flight step
SELECT to_char(to_timestamp((value->>'written_at')::float8), 'MM-DD HH24:MI:SS') AS written, to_char(to_timestamp((value->>'at')::float8), 'MM-DD HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'refusal' AS refusal, left(value->>'why', 160) AS why,
       value->>'elapsed_s' AS elapsed_s, value->>'exceeded_step' AS exceeded_step,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(value->'skipped_steps', '{}'::jsonb)) k) AS skipped,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(value->'errors', '{}'::jsonb)) k) AS error_keys,
       value->>'in_step' AS in_step, value->>'budget_exhausted' AS budget_exhausted,
       value->>'decisions_recorded' AS decisions, value->>'orders_submitted' AS orders, value->>'reviews' AS reviews,
       value->'pass_time' AS pass_time
  FROM ingestion_state WHERE key = 'paper_session_last_pass';

\echo C5 the last pass: step_elapsed_s, slowest first (top 12)
SELECT key AS step, value AS elapsed_s
  FROM jsonb_each(coalesce((SELECT value->'step_elapsed_s' FROM ingestion_state WHERE key = 'paper_session_last_pass'), '{}'::jsonb))
 ORDER BY (value::text)::float8 DESC NULLS LAST LIMIT 12;

\echo C6 the last pass: every error in full (none expected)
SELECT key, left(value::text, 300) AS error
  FROM jsonb_each(coalesce((SELECT value->'errors' FROM ingestion_state WHERE key = 'paper_session_last_pass'), '{}'::jsonb));

\echo C7 the health row's own last_pass digest (must agree with C4 when the newest attempt ran): elapsed, cut, skipped, error keys, steps count
SELECT to_char(heartbeat_at, 'MM-DD HH24:MI:SS') AS heartbeat_at, last_pass->>'elapsed_s' AS elapsed_s, last_pass->>'exceeded_step' AS exceeded_step,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(last_pass->'skipped_steps', '{}'::jsonb)) k) AS skipped,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(last_pass->'errors', '{}'::jsonb)) k) AS error_keys,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(last_pass->'steps', '{}'::jsonb))) AS steps_recorded,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(last_pass->'step_elapsed_s', '{}'::jsonb))) AS steps_timed,
       last_pass->>'trigger' AS trigger
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1;

\echo C8 the running release of each process (the deploy restart is the one expected gap): loop health commits and the workers boot
SELECT process, left(commit_sha, 12) AS commit_sha, count(*) AS loops, to_char(max(updated_at), 'MM-DD HH24:MI:SS') AS newest_update,
       to_char(max(last_success_at), 'MM-DD HH24:MI:SS') AS newest_success
  FROM runtime_loop_health GROUP BY 1, 2 ORDER BY 1, 2;
SELECT value->>'commit' AS workers_commit, value->>'at' AS workers_boot_at FROM ingestion_state WHERE key = 'workers_boot';
