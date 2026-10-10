-- RC6.3c PROOF C: PAPER PASS CONTINUITY OVER A WINDOW (SELECT only; nothing here writes).
--
-- PURPOSE. Prove at least 60 minutes of consecutive completed PAPER passes with zero cut or skipped steps and no
-- unexplained missed scheduled pass, from the records the pass itself writes (agents/paper_runtime.py and
-- bettor_paper_session.py on dd25c588). Read this file repeatedly and combine the reads (rules below): no single
-- read can see 60 minutes.
--
-- WHAT THE RECORDS SHOW, AND WHAT THEY CANNOT (every claim checked on dd25c588):
--   * A pass that RAN ends in S.record_pass (paper_runtime.py:862-871, the last thing _run does): ONE ring beat
--     {at = the pass START (epoch s), ok, elapsed_s, slowest_step, slowest_step_s, summary}, passes + 1, and
--     errors + 1 when the pass's errors dict was non-empty (bettor_paper_session.py:287, :302-316). Every cut step
--     (PAPER_STEP_EXCEEDED_PASS_TIME), skipped step (PAPER_STEP_SKIPPED_PASS_TIME_SPENT, or NOT_STARTED_ENTER_OVERRUN),
--     transaction left open (PAPER_STEP_LEFT_A_TRANSACTION_OPEN), step that raised, step that returned an error
--     (RESULT_ERROR_STEPS) and failed record (HEALTH) is a key of that dict, so ok = false AND errors + 1.
--   * A pass cut AS A WHOLE by HARD_TIMEOUT_S (90 s), or that raised outside its steps, never reaches record_pass:
--     run_once (paper_runtime.py:961-1000) catches it and writes ONLY the ingestion_state heartbeat
--     paper_session_last_pass (ran = false, refusal PAPER_PASS_RAISED_OR_TIMED_OUT, in_step, steps_ended). No beat,
--     no counter change; the next pass overwrites that heartbeat about 60 s later. Such a pass is visible ONLY as a
--     60 s slot with no beat (a gap of about 120 s between two beats), and only while BOTH beats are in a ring that
--     was read.
--   * The ring keeps the newest RECENT_HEARTBEATS_KEPT = 20 beats (bettor_paper_session.py:52), ordered by at: about
--     19-20 minutes at the 60 s cadence. Two reads whose rings share no beat leave a hole that no read checked.
--   * schedule() coalesces a tick that arrives while a pass is running (paper_runtime.py:1025-1029: no run_once, no
--     heartbeat, no beat). The servicing task ticks every SERVICING_INTERVAL_S = 60 s start to start
--     (workers/ext_pinnacle_loop.py:9353, :9642 -> agents/runtime.paper_pass_hook -> paper_runtime.schedule); the
--     collection cycle (CYCLE_S = 900 s, agents/runtime.py:295, trigger COLLECTION_CYCLE) and the held-review and
--     entry-fill schedulers add passes. So beats can be CLOSER than 60 s and the passes counter legitimately exceeds
--     elapsed / 60: the counter is therefore checked against the beats seen, EXACTLY, never against elapsed time.
--   * A pass that runs past 60 s coalesces the next tick and leaves a slot too (its beat shows elapsed_s > 60).
--   * WHERE A BEAT'S INSTANT COMES FROM. The servicing task schedules the paper pass AFTER its own service
--     (_service_once -> _agents_after_service, ext_pinnacle_loop.py:9511, :9642), and the next servicing pass starts
--     max(60 s, previous pass duration + 5 s) after the previous one started (:9827-9828). So a beat-to-beat gap is
--     60 s PLUS THE CHANGE in the servicing pass's own duration: a servicing pass that takes 35 s longer than the one
--     before it shows as a 95 s beat gap with no paper pass missed (the gaps after it are 60 s again, and 25 s when
--     it shortens back). C10 prints the servicing task's own cadence record (ingestion_state
--     ext_pinnacle_last_servicing: last / max pass elapsed, recent start gaps) so such a gap can be told from a
--     missed pass; it is still a FAIL of the strict rules below and must be explained by C10 in the report.
--     On RC6.3c the settle step is subsecond and a pass is well under the 20 s pass budget, so that must not happen
--     either: the slot rule below has no tolerance.
--
-- HOW TO RUN AND COMBINE (the 60-minute proof). Read this file every <= 15 minutes from the deploy: deploy +12,
-- +24, +36, +48, +60, +72 (reads R1..R6; one more read for every read that is void). Save every log. Combine them
-- with research/rc63c_pass_window_combine.py <log1> <log2> ... (it parses the C1 rows and the C8 boot row and
-- applies exactly these rules), or by hand from the C1 rows. EVERY RULE MUST HOLD, else FAIL:
--   OVERLAP   every ring Ri (i >= 2) shares at least one beat (the same at_epoch text) with ring Ri-1. A read whose
--             ring shares none is VOID: repeat it at once; the chain is broken until a read overlaps the previous one.
--   UNION     the beats of all rings, de-duplicated by at_epoch and ordered by at. THE WINDOW is from the first union
--             beat at or after the deploy boot (C8 workers_boot_at; earlier beats are PRE_DEPLOY) to the newest beat
--             of the last read. It must span >= 60 minutes, and EVERY consecutive gap inside it must be <= 90 s
--             (1.5 x the 60 s cadence).
--   SLOTS     implied_unrecorded_60s_slots over the window = sum over its gaps of greatest(round(gap / 60) - 1, 0)
--             = 0 (a whole-pass cut, a pass that raised before its record, a crashed process and a pass that ran
--             past 60 s all leave a slot).
--   COUNTERS  for every i >= 2: passes(Ri) - passes(Ri-1) EQUALS the number of union beats with at > the newest beat
--             of Ri-1 (exact, no tolerance; with OVERLAP those beats are all in Ri) and errors(Ri) - errors(Ri-1) = 0.
--             C1 reads the counters, the ring and the heartbeat in ONE statement (one snapshot), so they agree by
--             construction; last_error is kept by coalesce and never cleared, so counts are compared, not it.
--   BEATS     every beat in the window has ok = true.
--   ATTEMPT   the HEARTBEAT row (the newest attempt) shows ran = true at every read. ran = false FAILS the window:
--             refusal PAPER_PASS_RAISED_OR_TIMED_OUT is a pass cut as a whole or raised (its in_step names where);
--             ANOTHER_PAPER_PASS_IS_RUNNING (the lock held by another process) or PAPER_SESSION_NOT_ENABLED must not
--             occur while one servicing process runs the session, and must be explained before the window restarts.
-- The deploy restart itself is the one expected gap, BEFORE the window.
--
-- PER-READ PASS CRITERIA: the C1 SUMMARY_SINCE_BOOT row carries no FAIL flag (not_ok_beats = 0, gaps_over_90s = 0,
-- implied_unrecorded_60s_slots = 0, beats_with_a_step_over_20s = 0 -- the settle step was 30-43 s on 4534b43f and
-- is subsecond on RC6.3c); the HEARTBEAT row carries no FAIL flag (ran = true, no exceeded_step, no skipped steps,
-- no error keys); C9 total = 0; C5 settle under 1 s; C8 every process on the release commit.
--
-- C1 COLUMNS BY ROW KIND. COUNTERS: passes, errors, at = heartbeat_at, gap_s = heartbeat age in s, detail = session,
-- mutation_attempts, read_at, boot_at / boot_commit (C8 key), last_error_kept. HEARTBEAT: at = the attempt's pass
-- start, ok = ran, elapsed_s, slowest_step = exceeded_step, gap_s = written_at - at, flag = FAIL_... or ok, detail =
-- trigger, refusal, why, skipped, error_keys, in_step, written. BEAT (oldest first): at_epoch = the exact text of
-- the beat's at (the de-duplication key), gap_s = seconds since the previous beat in the ring, flag = PRE_DEPLOY /
-- NOT_OK / GAP_OVER_90S / PREVIOUS_BEAT_RAN_OVER_60S. SUMMARY_RING (all 20 beats) and SUMMARY_SINCE_BOOT (beats at
-- or after boot_at, gaps measured inside that subset only): flag = every FAIL_... that applies, detail = the counts.

\echo C1 ONE SNAPSHOT (one statement): COUNTERS, the HEARTBEAT of the newest attempt, every ring BEAT oldest first, SUMMARY_RING and SUMMARY_SINCE_BOOT
WITH h AS (
  SELECT session_id, passes, errors, mutation_attempts, heartbeat_at, last_error, recent_heartbeats
    FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1),
hb AS (SELECT value AS v FROM ingestion_state WHERE key = 'paper_session_last_pass'),
boot AS (
  SELECT (SELECT (value->>'at')::timestamptz FROM ingestion_state WHERE key = 'workers_boot') AS boot_at,
         (SELECT value->>'commit' FROM ingestion_state WHERE key = 'workers_boot') AS boot_commit),
ring AS (
  SELECT b->>'at' AS at_raw, (b->>'at')::float8 AS at_s, b,
         coalesce((b->>'at')::float8 >= extract(epoch FROM (SELECT boot_at FROM boot)), true) AS since_boot
    FROM h, jsonb_array_elements(h.recent_heartbeats) b),
g AS (
  SELECT at_raw, at_s, b, since_boot,
         at_s - lag(at_s) OVER (ORDER BY at_s) AS gap_s,
         at_s - lag(at_s) OVER (PARTITION BY since_boot ORDER BY at_s) AS gap_in_subset_s,
         (lag(b) OVER (ORDER BY at_s))->>'elapsed_s' AS prev_elapsed_s
    FROM ring),
sm AS (
  SELECT subset, count(*) AS beats,
         count(*) FILTER (WHERE b->>'ok' = 'true') AS ok_beats, count(*) FILTER (WHERE b->>'ok' <> 'true') AS not_ok_beats,
         round(max(gap_x)::numeric, 1) AS max_gap_s, round(avg(gap_x)::numeric, 1) AS avg_gap_s,
         count(*) FILTER (WHERE gap_x > 90) AS gaps_over_90s,
         coalesce(sum(greatest(round(gap_x / 60.0) - 1, 0)), 0)::int AS implied_unrecorded_60s_slots,
         min(at_s) AS first_s, max(at_s) AS last_s, round((max(at_s) - min(at_s))::numeric, 0) AS span_s,
         round(max((b->>'elapsed_s')::numeric), 2) AS max_elapsed_s, round(avg((b->>'elapsed_s')::numeric), 2) AS avg_elapsed_s,
         count(*) FILTER (WHERE (b->>'slowest_step_s')::numeric >= 20) AS beats_with_a_step_over_20s,
         count(*) FILTER (WHERE (b->>'slowest_step_s')::numeric >= 5) AS beats_with_a_step_over_5s,
         count(*) FILTER (WHERE (b->>'elapsed_s')::numeric > 60) AS beats_over_60s,
         string_agg(DISTINCT b->>'slowest_step', ',') AS slowest_steps_seen
    FROM (SELECT 'SUMMARY_RING' AS subset, gap_s AS gap_x, b, at_s FROM g
          UNION ALL
          SELECT 'SUMMARY_SINCE_BOOT', gap_in_subset_s, b, at_s FROM g WHERE since_boot) q
   GROUP BY 1),
rows_out AS (
  SELECT 0 AS ord, 0::float8 AS k, 'COUNTERS' AS kind, passes::text AS passes, errors::text AS errors,
         extract(epoch FROM heartbeat_at)::text AS at_epoch, to_char(heartbeat_at, 'MM-DD HH24:MI:SS') AS at,
         NULL::text AS ok, NULL::text AS elapsed_s, NULL::text AS slowest_step, NULL::text AS slowest_s,
         round(extract(epoch FROM now() - heartbeat_at)::numeric, 1)::text AS gap_s,
         NULL::text AS flag,
         'session=' || session_id || '; mutation_attempts=' || mutation_attempts
           || '; read_at=' || to_char(now(), 'YYYY-MM-DD HH24:MI:SS') || '; boot_at=' || coalesce(to_char(boot.boot_at, 'YYYY-MM-DD HH24:MI:SS'), '?')
           || '; boot_commit=' || coalesce(boot.boot_commit, '?') || '; last_error_kept=' || left(coalesce(last_error, ''), 110) AS detail
    FROM h CROSS JOIN boot
  UNION ALL
  SELECT 1, 0, 'HEARTBEAT', NULL, NULL, v->>'at', to_char(to_timestamp((v->>'at')::float8), 'MM-DD HH24:MI:SS'),
         v->>'ran', v->>'elapsed_s', v->>'exceeded_step', NULL,
         round(((v->>'written_at')::float8 - (v->>'at')::float8)::numeric, 1)::text,
         CASE WHEN v->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_RAN_FALSE:' || coalesce(v->>'refusal', '?')
              WHEN v->>'exceeded_step' IS NOT NULL THEN 'FAIL_CUT_STEP:' || (v->>'exceeded_step')
              WHEN jsonb_typeof(v->'skipped_steps') = 'object' AND v->'skipped_steps' <> '{}'::jsonb THEN 'FAIL_SKIPPED_STEPS'
              WHEN jsonb_typeof(v->'errors') = 'object' AND v->'errors' <> '{}'::jsonb THEN 'FAIL_ERRORS'
              ELSE 'ok' END,
         'trigger=' || coalesce(v->>'trigger', '?') || '; refusal=' || coalesce(v->>'refusal', '-') || '; why=' || left(coalesce(v->>'why', '-'), 100)
           || '; skipped=' || coalesce((SELECT string_agg(k, ',') FROM jsonb_object_keys(CASE WHEN jsonb_typeof(v->'skipped_steps') = 'object' THEN v->'skipped_steps' ELSE '{}'::jsonb END) k), '-')
           || '; error_keys=' || coalesce((SELECT string_agg(k, ',') FROM jsonb_object_keys(CASE WHEN jsonb_typeof(v->'errors') = 'object' THEN v->'errors' ELSE '{}'::jsonb END) k), '-')
           || '; in_step=' || coalesce(v->>'in_step', '-') || '; written=' || to_char(to_timestamp((v->>'written_at')::float8), 'MM-DD HH24:MI:SS')
    FROM hb
  UNION ALL
  SELECT 2, at_s, 'BEAT', NULL, NULL, at_raw, to_char(to_timestamp(at_s), 'MM-DD HH24:MI:SS'),
         b->>'ok', round((b->>'elapsed_s')::numeric, 2)::text, b->>'slowest_step', round((b->>'slowest_step_s')::numeric, 2)::text,
         round(gap_s::numeric, 1)::text,
         nullif(concat_ws(' ', CASE WHEN NOT since_boot THEN 'PRE_DEPLOY' END, CASE WHEN b->>'ok' <> 'true' THEN 'NOT_OK' END,
                               CASE WHEN gap_s > 90 THEN 'GAP_OVER_90S' END,
                               CASE WHEN gap_s > 90 AND prev_elapsed_s::float8 > 60 THEN 'PREVIOUS_BEAT_RAN_OVER_60S' END), ''),
         'decisions=' || coalesce(b->'summary'->>'decisions_recorded', '-') || '; orders=' || coalesce(b->'summary'->>'orders_submitted', '-')
           || '; fills=' || coalesce(b->'summary'->>'fills', '-') || '; reviews=' || coalesce(b->'summary'->>'reviews', '-')
           || '; budget_exhausted=' || coalesce(b->'summary'->>'budget_exhausted', '-') || '; held_reviews=' || coalesce(b->>'held_in_pass_reviews', '-')
    FROM g
  UNION ALL
  SELECT 3, CASE subset WHEN 'SUMMARY_RING' THEN 0 ELSE 1 END, subset, NULL, NULL, NULL,
         to_char(to_timestamp(first_s), 'MM-DD HH24:MI:SS') || '..' || to_char(to_timestamp(last_s), 'HH24:MI:SS'),
         NULL, NULL, NULL, NULL, NULL,
         coalesce(nullif(concat_ws(' ', CASE WHEN not_ok_beats > 0 THEN 'FAIL_NOT_OK_BEATS=' || not_ok_beats END,
                                        CASE WHEN gaps_over_90s > 0 THEN 'FAIL_GAPS_OVER_90S=' || gaps_over_90s END,
                                        CASE WHEN implied_unrecorded_60s_slots > 0 THEN 'FAIL_IMPLIED_UNRECORDED_60S_SLOTS=' || implied_unrecorded_60s_slots END,
                                        CASE WHEN beats_with_a_step_over_20s > 0 THEN 'FAIL_BEATS_WITH_A_STEP_OVER_20S=' || beats_with_a_step_over_20s END), ''), 'ok'),
         'beats=' || beats || '; ok_beats=' || ok_beats || '; not_ok_beats=' || not_ok_beats || '; max_gap_s=' || coalesce(max_gap_s::text, '-')
           || '; avg_gap_s=' || coalesce(avg_gap_s::text, '-') || '; gaps_over_90s=' || gaps_over_90s || '; implied_unrecorded_60s_slots=' || implied_unrecorded_60s_slots
           || '; span_s=' || span_s || '; max_elapsed_s=' || max_elapsed_s || '; avg_elapsed_s=' || avg_elapsed_s || '; beats_over_60s=' || beats_over_60s
           || '; beats_with_a_step_over_20s=' || beats_with_a_step_over_20s || '; beats_with_a_step_over_5s=' || beats_with_a_step_over_5s
           || '; slowest_steps_seen=' || coalesce(slowest_steps_seen, '-')
    FROM sm)
SELECT kind, passes, errors, at_epoch, at, ok, elapsed_s, slowest_step, slowest_s, gap_s, flag, detail
  FROM rows_out ORDER BY ord, k;

\echo C4 the heartbeat of the newest attempt in full (its own snapshot; the C1 HEARTBEAT row is the one the rules use)
SELECT to_char(to_timestamp((value->>'written_at')::float8), 'MM-DD HH24:MI:SS') AS written, to_char(to_timestamp((value->>'at')::float8), 'MM-DD HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'refusal' AS refusal, left(value->>'why', 160) AS why,
       value->>'elapsed_s' AS elapsed_s, value->>'exceeded_step' AS exceeded_step,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(CASE WHEN jsonb_typeof(value->'skipped_steps') = 'object' THEN value->'skipped_steps' ELSE '{}'::jsonb END) k) AS skipped,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(CASE WHEN jsonb_typeof(value->'errors') = 'object' THEN value->'errors' ELSE '{}'::jsonb END) k) AS error_keys,
       value->>'in_step' AS in_step, value->'steps_ended' AS steps_ended, value->>'budget_exhausted' AS budget_exhausted,
       value->>'decisions_recorded' AS decisions, value->>'orders_submitted' AS orders, value->>'reviews' AS reviews,
       value->'pass_time' AS pass_time
  FROM ingestion_state WHERE key = 'paper_session_last_pass';

\echo C5 the last pass: step_elapsed_s, slowest first (top 12); settle must be under 1 s on RC6.3c
SELECT key AS step, value AS elapsed_s
  FROM jsonb_each((SELECT CASE WHEN jsonb_typeof(value->'step_elapsed_s') = 'object' THEN value->'step_elapsed_s' ELSE '{}'::jsonb END FROM ingestion_state WHERE key = 'paper_session_last_pass'))
 ORDER BY (value::text)::float8 DESC NULLS LAST LIMIT 12;

\echo C6 the last pass: every error in full (none expected)
SELECT key, left(value::text, 300) AS error
  FROM jsonb_each((SELECT CASE WHEN jsonb_typeof(value->'errors') = 'object' THEN value->'errors' ELSE '{}'::jsonb END FROM ingestion_state WHERE key = 'paper_session_last_pass'));

\echo C7 the last_pass digest on the health row (must agree with C4 when the newest attempt ran): elapsed, cut, skipped, error keys, steps count
SELECT to_char(heartbeat_at, 'MM-DD HH24:MI:SS') AS heartbeat_at, last_pass->>'elapsed_s' AS elapsed_s, last_pass->>'exceeded_step' AS exceeded_step,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(CASE WHEN jsonb_typeof(last_pass->'skipped_steps') = 'object' THEN last_pass->'skipped_steps' ELSE '{}'::jsonb END) k) AS skipped,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(CASE WHEN jsonb_typeof(last_pass->'errors') = 'object' THEN last_pass->'errors' ELSE '{}'::jsonb END) k) AS error_keys,
       (SELECT count(*) FROM jsonb_object_keys(CASE WHEN jsonb_typeof(last_pass->'steps') = 'object' THEN last_pass->'steps' ELSE '{}'::jsonb END)) AS steps_recorded,
       (SELECT count(*) FROM jsonb_object_keys(CASE WHEN jsonb_typeof(last_pass->'step_elapsed_s') = 'object' THEN last_pass->'step_elapsed_s' ELSE '{}'::jsonb END)) AS steps_timed,
       last_pass->>'trigger' AS trigger
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1;

\echo C8 the running release of each process (the deploy restart is the one expected gap, before the window): loop health commits and the workers boot
SELECT process, left(commit_sha, 12) AS commit_sha, count(*) AS loops, to_char(max(updated_at), 'MM-DD HH24:MI:SS') AS newest_update,
       to_char(max(last_success_at), 'MM-DD HH24:MI:SS') AS newest_success
  FROM runtime_loop_health GROUP BY 1, 2 ORDER BY 1, 2;
SELECT value->>'commit' AS workers_commit, value->>'at' AS workers_boot_at FROM ingestion_state WHERE key = 'workers_boot';

\echo C9 implied unrecorded 60 s slots since the deploy boot (own snapshot): every gap over 90 s with the slots it implies and the previous beat; the total must be 0 on RC6.3c
WITH h AS (SELECT recent_heartbeats FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1),
boot AS (SELECT (value->>'at')::timestamptz AS boot_at FROM ingestion_state WHERE key = 'workers_boot'),
ring AS (SELECT (b->>'at')::float8 AS at_s, b FROM h, jsonb_array_elements(h.recent_heartbeats) b
          WHERE coalesce((b->>'at')::float8 >= extract(epoch FROM (SELECT boot_at FROM boot)), true)),
g AS (SELECT at_s, b, at_s - lag(at_s) OVER (ORDER BY at_s) AS gap_s, lag(b) OVER (ORDER BY at_s) AS prev_b FROM ring)
SELECT to_char(to_timestamp(at_s - gap_s), 'MM-DD HH24:MI:SS') AS from_beat, to_char(to_timestamp(at_s), 'MM-DD HH24:MI:SS') AS to_beat,
       round(gap_s::numeric, 1) AS gap_s, greatest(round(gap_s / 60.0) - 1, 0)::int AS implied_unrecorded_60s_slots,
       prev_b->>'elapsed_s' AS previous_beat_elapsed_s, prev_b->>'ok' AS previous_beat_ok,
       CASE WHEN (prev_b->>'elapsed_s')::float8 > 60 THEN 'the previous pass ran past 60 s and coalesced a tick (still a FAIL on RC6.3c: passes are under 20 s)'
            ELSE 'UNEXPLAINED: a pass cut as a whole or raised before its record (heartbeat ran=false, overwritten), a crashed process, or a tick never scheduled' END AS explanation
  FROM g WHERE gap_s > 90 ORDER BY at_s;
WITH h AS (SELECT recent_heartbeats FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1),
boot AS (SELECT (value->>'at')::timestamptz AS boot_at FROM ingestion_state WHERE key = 'workers_boot'),
ring AS (SELECT (b->>'at')::float8 AS at_s, b FROM h, jsonb_array_elements(h.recent_heartbeats) b
          WHERE coalesce((b->>'at')::float8 >= extract(epoch FROM (SELECT boot_at FROM boot)), true)),
g AS (SELECT at_s, at_s - lag(at_s) OVER (ORDER BY at_s) AS gap_s FROM ring)
SELECT count(*) AS beats_since_boot, count(*) FILTER (WHERE gap_s > 90) AS gaps_over_90s_since_boot,
       coalesce(sum(greatest(round(gap_s / 60.0) - 1, 0)), 0)::int AS implied_unrecorded_60s_slots_since_boot,
       to_char((SELECT boot_at FROM boot), 'YYYY-MM-DD HH24:MI:SS') AS boot_at,
       CASE WHEN coalesce(sum(greatest(round(gap_s / 60.0) - 1, 0)), 0) = 0 AND count(*) FILTER (WHERE gap_s > 90) = 0 THEN 'ok' ELSE 'FAIL' END AS verdict_this_ring
  FROM g;

\echo C10 the servicing task that schedules the paper pass (ingestion_state ext_pinnacle_last_servicing): its own cadence, so a beat gap over 90 s can be told from a late tick
SELECT CASE WHEN jsonb_typeof(value->'at') = 'number' THEN to_char(to_timestamp((value->>'at')::float8), 'MM-DD HH24:MI:SS') END AS written,
       value->>'state' AS state, value->>'source' AS source,
       CASE WHEN jsonb_typeof(value->'pass_at') = 'number' THEN to_char(to_timestamp((value->>'pass_at')::float8), 'HH24:MI:SS') END AS pass_at,
       value->>'elapsed_s' AS pass_elapsed_s, value->'servicing_cadence'->>'servicer' AS servicer, value->'servicing_cadence'->>'task_active' AS task_active,
       value->'servicing_cadence'->>'passes' AS passes, value->'servicing_cadence'->>'skipped_busy' AS skipped_busy, value->'servicing_cadence'->>'errors' AS errors,
       value->'servicing_cadence'->>'last_pass_elapsed_s' AS last_pass_elapsed_s, value->'servicing_cadence'->>'max_pass_elapsed_s' AS max_pass_elapsed_s,
       value->'servicing_cadence'->'recent_start_gaps_s' AS recent_start_gaps_s, left(value->'writer'->>'commit', 12) AS writer_commit
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';
