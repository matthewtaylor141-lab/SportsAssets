-- READ-ONLY. DO HELD-POSITION REVIEWS AND FILL RECOVERY WAIT BEHIND THE
-- COLLECTION CYCLE? THE MEASURED SCHEDULE, FROM PRODUCTION'S OWN ROWS.
--
-- The cycle heartbeat (`ingestion_state['ext_pinnacle_last_cycle']`) is ONE
-- row, overwritten every cycle -- it has no history. So the intervals over
-- the last 24 h are measured from the durable per-cycle / per-pass rows:
--   C  collection cycles  : ext_candidate_outcomes (one cycle_id per LIVE
--                           cycle that saw >= 1 provider event; cycle_at is
--                           the cycle's START, recorded_at is when the entry
--                           lane's rows were written, i.e. entry-lane end)
--   O  observation passes : bettor_pair_observation_attempts (pass_id,
--                           attempted_at .. finished_at)
--   X  Xavier reviews     : bettor_xavier_decisions (one row per position per
--                           review; decided_at is the pass instant)
--   R  recovery / servicing reads : bettor_xavier_execution_events,
--                           bettor_funded_operation_evidence, investigations,
--                           settlement rechecks, the funded decision ledger
--   H  the heartbeats now : elapsed_s, and -- once the decoupling is deployed
--                           -- step_timing_s, servicing_cadence and the
--                           servicing task's own row
--                           (ingestion_state['ext_pinnacle_last_servicing']).
--
-- X3 is the direct test of the coupling: at b51378d servicing ran at the TOP
-- of each cycle, so every Xavier pass instant sits a few seconds after a
-- cycle start and the review-to-review gap equals the cycle-to-cycle gap.
-- After the decoupling the review gap is ~SERVICING_INTERVAL_S (60 s) and is
-- unrelated to cycle starts.
--
-- No balance, cash, buying power, equity or credential is selected.

\echo '== H0 · the cycle heartbeat now: build, state, elapsed, per-step timing, servicing cadence =='
SELECT to_timestamp((value->>'at')::float8)                          AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1)
                                                                      AS age_s,
       value->'writer'->>'build'                                      AS writer_build,
       value->'writer'->>'source_sha256_12'                           AS writer_source,
       value->>'state'                                                AS state,
       value->>'elapsed_s'                                            AS cycle_elapsed_s,
       value->'step_timing_s'                                         AS step_timing_s,
       value->'servicing_cadence'                                     AS servicing_cadence
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== H1 · the servicing task row (absent before the decoupling is deployed) =='
SELECT to_timestamp((value->>'at')::float8)                          AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1)
                                                                      AS age_s,
       value->'writer'->>'build'                                      AS writer_build,
       value->>'state'                                                AS state,
       value->>'source'                                               AS source,
       to_timestamp((value->>'pass_at')::float8)                      AS pass_at,
       value->>'elapsed_s'                                            AS pass_elapsed_s,
       value->>'slow_half_ran'                                        AS slow_half_ran,
       value->>'review_interval_s'                                    AS review_interval_s,
       value->'servicing_cadence'                                     AS servicing_cadence
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_servicing';

\echo '== H2 · what the last servicing decided (cycle row digest): recovery, Xavier briefs, daily review =='
SELECT value->'funded_servicing'->'recovered'                        AS recovered,
       value->'funded_servicing'->>'positions_serviced'               AS positions_serviced,
       value->'funded_servicing'->'xavier'                            AS xavier_briefs,
       value->'xavier_review'                                         AS xavier_daily_review
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== C1 · collection cycles, last 24 h: start-to-start interval (s) by writer =='
WITH cyc AS (
    SELECT cycle_id, writer, min(cycle_at) AS started_at,
           max(recorded_at) AS entry_rows_at
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours'
     GROUP BY cycle_id, writer),
gaps AS (
    SELECT writer, started_at,
           extract(epoch FROM started_at
                   - lag(started_at) OVER (ORDER BY started_at)) AS gap_s
      FROM cyc)
SELECT coalesce(writer, '-')                                          AS writer_build,
       count(*)                                                       AS cycles,
       round(min(gap_s)::numeric, 1)                                  AS min_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric, 1)
                                                                      AS median_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY gap_s))::numeric, 1)
                                                                      AS p90_s,
       round(max(gap_s)::numeric, 1)                                  AS max_s,
       min(started_at)                                                AS first_start,
       max(started_at)                                                AS last_start
  FROM gaps
 GROUP BY writer
 ORDER BY last_start DESC;

\echo '== C2 · collection cycles, last 24 h: entry-lane duration (cycle start -> entry rows written), s =='
WITH cyc AS (
    SELECT cycle_id, min(cycle_at) AS started_at, max(recorded_at) AS entry_rows_at
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours'
     GROUP BY cycle_id)
SELECT count(*)                                                       AS cycles,
       round(min(extract(epoch FROM entry_rows_at - started_at))::numeric, 1)
                                                                      AS min_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY
             extract(epoch FROM entry_rows_at - started_at)))::numeric, 1)
                                                                      AS median_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY
             extract(epoch FROM entry_rows_at - started_at)))::numeric, 1)
                                                                      AS p90_s,
       round(max(extract(epoch FROM entry_rows_at - started_at))::numeric, 1)
                                                                      AS max_s
  FROM cyc;

\echo '== C3 · the 12 most recent collection cycles: start, gap to previous, entry-lane duration =='
WITH cyc AS (
    SELECT cycle_id, writer, min(cycle_at) AS started_at,
           max(recorded_at) AS entry_rows_at, count(*) AS events
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours'
     GROUP BY cycle_id, writer)
SELECT started_at,
       round(extract(epoch FROM started_at
             - lag(started_at) OVER (ORDER BY started_at))::numeric, 1) AS gap_s,
       round(extract(epoch FROM entry_rows_at - started_at)::numeric, 1)
                                                                      AS entry_lane_s,
       events,
       writer                                                         AS writer_build
  FROM cyc
 ORDER BY started_at DESC
 LIMIT 12;

\echo '== O1 · pair observation passes, last 24 h: duration and start-to-start interval, s =='
WITH p AS (
    SELECT pass_id, min(attempted_at) AS started_at, max(finished_at) AS ended_at,
           count(*) AS attempts
      FROM bettor_pair_observation_attempts
     WHERE attempted_at > now() - interval '24 hours'
     GROUP BY pass_id),
g AS (
    SELECT started_at, ended_at, attempts,
           extract(epoch FROM ended_at - started_at) AS dur_s,
           extract(epoch FROM started_at
                   - lag(started_at) OVER (ORDER BY started_at)) AS gap_s
      FROM p)
SELECT count(*)                                                       AS passes,
       round(min(dur_s)::numeric, 1)                                  AS dur_min_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY dur_s))::numeric, 1)
                                                                      AS dur_median_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY dur_s))::numeric, 1)
                                                                      AS dur_p90_s,
       round(max(dur_s)::numeric, 1)                                  AS dur_max_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric, 1)
                                                                      AS gap_median_s,
       round(max(gap_s)::numeric, 1)                                  AS gap_max_s,
       max(started_at)                                                AS last_pass
  FROM g;

\echo '== X1 · Xavier held-position reviews, last 24 h: review-to-review gap per position, s =='
WITH r AS (
    SELECT intent_id, decided_at,
           extract(epoch FROM decided_at
                   - lag(decided_at) OVER (PARTITION BY intent_id
                                           ORDER BY decided_at)) AS gap_s
      FROM bettor_xavier_decisions
     WHERE decided_at > now() - interval '24 hours')
SELECT count(DISTINCT intent_id)                                      AS positions,
       count(*)                                                       AS reviews,
       count(gap_s)                                                   AS gaps,
       round(min(gap_s)::numeric, 1)                                  AS min_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric, 1)
                                                                      AS median_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY gap_s))::numeric, 1)
                                                                      AS p90_s,
       round(max(gap_s)::numeric, 1)                                  AS max_s,
       max(decided_at)                                                AS newest_review
  FROM r;

\echo '== X2 · Xavier: the newest review per position, and how overdue its promised next review is =='
SELECT DISTINCT ON (intent_id)
       intent_id,
       decided_at,
       next_review_at,
       round(extract(epoch FROM next_review_at - decided_at)::numeric, 1)  AS promised_interval_s,
       round(extract(epoch FROM now() - next_review_at)::numeric, 1)       AS overdue_s,
       responsibility_state,
       coalesce(chosen_action, '-')                                        AS chosen_action,
       split_part(execution_eligibility, ':', 1)                           AS eligibility
  FROM bettor_xavier_decisions
 ORDER BY intent_id, decided_at DESC;

\echo '== X3 · THE COUPLING TEST: each Xavier pass instant vs the latest collection cycle start before it =='
WITH passes AS (
    SELECT date_trunc('second', decided_at) AS pass_at, count(*) AS positions
      FROM bettor_xavier_decisions
     WHERE decided_at > now() - interval '24 hours'
     GROUP BY 1),
starts AS (
    SELECT DISTINCT min(cycle_at) OVER (PARTITION BY cycle_id) AS started_at
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '25 hours'),
paired AS (
    SELECT p.pass_at, p.positions,
           (SELECT max(s.started_at) FROM starts s
             WHERE s.started_at <= p.pass_at) AS cycle_started_at
      FROM passes p)
SELECT count(*)                                                       AS xavier_passes,
       count(cycle_started_at)                                        AS with_a_prior_cycle,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY
             extract(epoch FROM pass_at - cycle_started_at)))::numeric, 1)
                                                                      AS median_s_after_cycle_start,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY
             extract(epoch FROM pass_at - cycle_started_at)))::numeric, 1)
                                                                      AS p90_s_after_cycle_start,
       count(*) FILTER (WHERE extract(epoch FROM pass_at - cycle_started_at) < 30)
                                                                      AS within_30s_of_a_cycle_start
  FROM paired;

\echo '== X4 · Xavier passes, the 12 most recent: instant, gap to previous pass, positions =='
WITH passes AS (
    SELECT date_trunc('second', decided_at) AS pass_at, count(*) AS positions
      FROM bettor_xavier_decisions
     WHERE decided_at > now() - interval '24 hours'
     GROUP BY 1)
SELECT pass_at,
       round(extract(epoch FROM pass_at
             - lag(pass_at) OVER (ORDER BY pass_at))::numeric, 1)     AS gap_s,
       positions
  FROM passes
 ORDER BY pass_at DESC
 LIMIT 12;

\echo '== R1 · recovery and execution facts, last 24 h, by kind and reader: count and newest =='
SELECT event_kind, source, count(*) AS n, max(occurred_at) AS newest,
       max(recorded_at) AS newest_recorded
  FROM bettor_xavier_execution_events
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY event_kind, source
 ORDER BY newest DESC;

\echo '== R2 · reservation-recovery venue reads (operation evidence), last 24 h: newest and read-to-read gap, s =='
WITH e AS (
    SELECT date_trunc('second', read_at) AS read_at, count(*) AS reads
      FROM bettor_funded_operation_evidence
     WHERE read_at > now() - interval '24 hours'
     GROUP BY 1),
g AS (
    SELECT read_at, reads,
           extract(epoch FROM read_at - lag(read_at) OVER (ORDER BY read_at)) AS gap_s
      FROM e)
SELECT count(*)                                                       AS read_instants,
       sum(reads)                                                     AS reads,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric, 1)
                                                                      AS median_gap_s,
       round(max(gap_s)::numeric, 1)                                  AS max_gap_s,
       max(read_at)                                                   AS newest
  FROM g;

\echo '== R3 · servicing reads that run every pass: investigations, settlement rechecks, decision ledger (newest) =='
SELECT (SELECT max(last_read_at) FROM bettor_funded_investigations)   AS investigation_last_read,
       (SELECT count(*) FROM bettor_funded_investigations
         WHERE resolved_at IS NULL)                                   AS investigations_open,
       (SELECT max(read_at) FROM bettor_funded_settlement_rechecks)   AS settlement_recheck_last_read,
       (SELECT count(DISTINCT date_trunc('second', read_at))
          FROM bettor_funded_settlement_rechecks
         WHERE read_at > now() - interval '24 hours')                 AS recheck_instants_24h,
       (SELECT max(decided_at) FROM bettor_funded_decisions)          AS funded_decision_newest,
       (SELECT count(DISTINCT date_trunc('second', decided_at))
          FROM bettor_funded_decisions
         WHERE decided_at > now() - interval '24 hours')              AS funded_decision_instants_24h;

\echo '== R4 · funded intents still awaiting an answer (identity and age only) =='
SELECT state, count(*) AS n,
       min(sent_at) AS oldest_sent,
       round(extract(epoch FROM now() - min(sent_at))::numeric, 0) AS oldest_age_s
  FROM bettor_funded_intents
 WHERE resolved_at IS NULL AND sent_at IS NOT NULL
 GROUP BY state
 ORDER BY n DESC;
