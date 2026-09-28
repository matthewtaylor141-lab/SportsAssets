-- READ-ONLY. DID THE LATENCY LEVERS ACTUALLY CHANGE ANYTHING?
--
-- Owner requirement: "Measure provider age, our added delay, request
-- counts, valid evaluations and refusals on the deployed path. Show
-- whether the previously self-inflicted stale cases decrease."
--
-- WHAT THIS FILE IS FOR, AND WHAT IT CANNOT DO. It reads the MEASUREMENT
-- back from production. It cannot establish an improvement on its own,
-- because an improvement is a COMPARISON and the baseline is the
-- 1,126-row evaluation census taken before the levers existed:
--
--     provider lag (already old on arrival)   median 14.6 s   103 rows > 30 s
--     our processing delay                    median 28.7 s   296 rows stale
--     total evaluation rows                   1,126
--     rows with no fair value                 399, ALL at QUOTE_STALE
--
-- So section 2 prints the new numbers BESIDE those, and the verdict is a
-- comparison a reader makes -- not a claim this file asserts.
--
-- AND ONE HONEST CAVEAT UP FRONT. The lane currently evaluates ZERO
-- candidates per cycle (`ZERO_EVALUATED__INPUT_PATH_BLOCKED`, 154 markets
-- considered). With no evaluations there are no latency samples, so a
-- readback showing nulls means THE BLOCKER UPSTREAM IS STILL THERE, not
-- that the delay is gone. `samples` is printed first for exactly that
-- reason: a median over zero samples is not a fast cycle.

-- ─────────────────────────────────────────────────────────────────────
-- 1 · THE WRITER, FIRST. Nothing below is evidence for a build unless
--     this says which build wrote it.
-- ─────────────────────────────────────────────────────────────────────
SELECT key,
       to_timestamp((value::jsonb ->> 'at')::float8)     AS cycle_at,
       (value::jsonb -> 'writer' ->> 'build')            AS writer_build,
       (value::jsonb -> 'writer' ->> 'source_sha256_12') AS module_digest,
       (value::jsonb ->> 'state')                        AS state,
       (value::jsonb ->> 'cycle_label')                  AS cycle_label,
       (value::jsonb ->> 'evaluated')                    AS evaluated,
       (value::jsonb ->> 'markets_considered')           AS markets,
       ((value::jsonb -> 'odds_freshness') IS NOT NULL)
                                                         AS carries_latency
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby')
 ORDER BY key;

-- ─────────────────────────────────────────────────────────────────────
-- 2 · THE MEASUREMENT, WITH THE BASELINE BESIDE IT.
--     `samples` FIRST: a median over 0 samples is not a fast cycle.
-- ─────────────────────────────────────────────────────────────────────
WITH f AS (
  SELECT value::jsonb -> 'odds_freshness' AS o
    FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT (o ->> 'samples')                      AS samples,
       (o ->> 'valid_evaluations')             AS valid_evaluations,
       -- ── OUR DELAY, WHICH IS THE ONLY HALF WE CAN FIX ──────────
       (o ->> 'our_processing_s')              AS our_delay_median_s,
       '28.7'                                  AS our_delay_baseline_s,
       (o ->> 'our_processing_max_s')          AS our_delay_max_s,
       -- ── THE PROVIDER'S HALF, WHICH WE CANNOT ──────────────────
       (o ->> 'provider_lag_s')                AS provider_lag_median_s,
       '14.6'                                  AS provider_lag_baseline_s,
       -- ── THE SUM THE 30 s RULE GOVERNS, UNCHANGED ──────────────
       (o ->> 'pinnacle_age_s')                AS total_age_median_s,
       (o ->> 'limit_s')                       AS limit_s,
       -- ── THE NUMBER THE LEVERS HAVE TO MOVE ────────────────────
       (o ->> 'self_inflicted_stale')          AS self_inflicted_now,
       '296'                                   AS self_inflicted_baseline,
       (o ->> 'stale_refusals')                AS stale_refusals_now,
       (o ->> 'provider_stale_on_arrival')     AS provider_stale_on_arrival,
       -- ── WHAT THE LEVERS DID ───────────────────────────────────
       (o ->> 'skipped_stale_on_arrival')      AS lever_a_skipped,
       (o ->> 'deduplicated_requests')         AS lever_c_deduped,
       (o ->> 'venue_requests')                AS venue_reads_spent,
       -- ── AND THE COVERAGE THAT WAS NOT EXAMINED ────────────────
       -- Printed beside the wins on purpose: a better stale rate produced
       -- by evaluating fewer candidates is not an improvement.
       (o ->> 'deferred_candidates')           AS deferred,
       (o ->> 'events_per_odds_fetch')         AS events_per_fetch,
       (o ->> 'is_the_default')                AS knob_at_default
  FROM f;

-- ─────────────────────────────────────────────────────────────────────
-- 3 · WHICH CANDIDATES WERE DEFERRED, BY IDENTITY. Prioritisation decides
--     who falls past MAX_PER_CYCLE, so this is the audit trail for the
--     coverage the reordering cost.
-- ─────────────────────────────────────────────────────────────────────
WITH f AS (
  SELECT value::jsonb -> 'odds_freshness' -> 'deferred_sample' AS d
    FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT (e ->> 'event_id')     AS event_id,
       (e ->> 'sport_key')    AS sport,
       (e ->> 'home')         AS home,
       (e ->> 'away')         AS away,
       (e ->> 'queue_position') AS queue_position,
       to_timestamp((e ->> 'provider_observed_at_epoch_s')::float8)
                              AS provider_observed_at
  FROM f, jsonb_array_elements(coalesce(f.d, '[]'::jsonb)) AS e
 ORDER BY (e ->> 'queue_position')::int;

-- ─────────────────────────────────────────────────────────────────────
-- 4 · THE REFUSAL CENSUS FROM THE ROWS THEMSELVES, not the heartbeat.
--     The heartbeat is one cycle; this is every evaluation row in the
--     window, which is the population the 399/1,126 baseline came from.
-- ─────────────────────────────────────────────────────────────────────
SELECT decision,
       admissible,
       count(*)                                        AS rows_,
       count(DISTINCT us_market_slug)                  AS markets,
       min(decided_at)                                 AS first_at,
       max(decided_at)                                 AS last_at
  FROM external_valuations
 WHERE decided_at > now() - INTERVAL '24 hours'
 GROUP BY decision, admissible
 ORDER BY rows_ DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · THE MAPPED-CANDIDATE LEDGER'S FIRST REFUSAL PER CANDIDATE. This is
--     where QUOTE_STALE_ON_ARRIVAL should appear if lever A is firing,
--     and it is counted SEPARATELY from QUOTE_STALE on purpose: moving
--     cases between two indistinguishable buckets would look like an
--     improvement while changing nothing.
-- ─────────────────────────────────────────────────────────────────────
WITH f AS (
  SELECT value::jsonb -> 'mapped_candidate_ledger' AS l
    FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT (e ->> 'first_refusal')                AS first_refusal,
       (e ->> 'stage')                        AS stage,
       count(*)                               AS candidates,
       round(avg(nullif(e ->> 'age_s', '')::numeric), 3)          AS age_s_avg,
       round(avg(nullif(e ->> 'provider_lag_s', '')::numeric), 3) AS provider_lag_avg,
       round(avg(nullif(e ->> 'our_processing_s', '')::numeric), 3) AS our_delay_avg
  FROM f, jsonb_array_elements(coalesce(f.l, '[]'::jsonb)) AS e
 GROUP BY first_refusal, stage
 ORDER BY candidates DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 6 · THE CONTROL ROWS. Whether any of the above could have happened.
-- ─────────────────────────────────────────────────────────────────────
SELECT key, left(value::text, 120) AS value_head
  FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'ext_pinnacle_shadow',
               'bettor_live_loop_armed', 'funded_exit_enabled')
 ORDER BY key;
