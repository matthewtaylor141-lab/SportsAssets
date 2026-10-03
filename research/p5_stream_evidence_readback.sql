-- READ-ONLY. THE INSTITUTIONAL gRPC STREAM'S RUNTIME EVIDENCE AND THE
-- SAME-BOOK PROBE, READ BACK FROM PRODUCTION (claude/cand22-stream,
-- migration 210; research/p5_runtime_activation.md on that branch).
--
-- Run with the research-sql workflow, dispatched with ref=claude/command-center:
--     file = p5_stream_evidence_readback.sql
--
-- WHAT IT ANSWERS, IN ORDER:
--   0  are the evidence tables there (migration 210 applied)?
--   1  the workers' stream: process rows (state, connection id + epoch,
--      connects / reconnects, last disconnect, message counts)
--   2  per symbol, the newest minute: current() answer, receipt age, skew,
--      first complete book after (re)connect, gap open, state, top-of-book
--   3  per symbol, the last 60 minutes: minutes current, connects,
--      disconnects, gap events, updates, receipt age and skew distribution,
--      the largest gap between updates, minutes inside P5's 2 s bounds
--   4  every gap event in the last 60 minutes
--   5  the same-book probe, 24 h: verdicts per symbol, disagreements while
--      the stream book was stable, the window distribution
--   6  the newest 20 same-book samples with both books and the diff
--   7  the P5 artifact row (owner approval)
--   8  the workers heartbeat: stream digest, evidence and probe counters
--
-- EMPTY RESULTS MEAN THE STREAM IS NOT ON in the workers process
-- (INSTITUTIONAL_MD_STREAM), or the workers do not run the build with
-- migration 210 -- section 8 says which build and stream state they report.
-- Nothing here is REST polling: these rows are written only from gRPC
-- stream events.

\echo == 0 · the evidence tables ==
SELECT to_regclass('institutional_stream_evidence') AS stream_evidence_table,
       to_regclass('institutional_same_book_probe') AS same_book_table,
       (SELECT max(version) FROM schema_migrations
         WHERE version LIKE '210_%')               AS migration_210;

\echo == 1 · the workers stream, process rows (last 30 min) ==
SELECT DISTINCT ON (process_id)
       process_id, minute, recorded_at, stream_state,
       left(stream_state_why, 120)                    AS why,
       connection_id, connection_epoch, connects_total, reconnects_total,
       first_connect_at, last_connect_at, last_disconnect_at,
       left(last_disconnect_why, 120)                 AS last_disconnect_why,
       messages_total, messages_in_minute,
       extra -> 'heartbeats_in_minute'                AS heartbeats_in_minute,
       extra -> 'subscription_errors_in_minute'       AS subscription_errors,
       extra -> 'last_refusal'                        AS last_refusal,
       extra -> 'wanted_symbols'                      AS wanted_symbols
  FROM institutional_stream_evidence
 WHERE symbol = '*' AND recorded_at > now() - interval '30 minutes'
 ORDER BY process_id, minute DESC, recorded_at DESC;

\echo == 2 · per symbol, the newest minute ==
SELECT DISTINCT ON (symbol)
       symbol, minute, connection_epoch, connected, current_ok,
       current_refusal, receipt_age_s, venue_receipt_skew_s,
       first_complete_book_after_connect_s, gap_open, instrument_state,
       state_source, price_scale, qty_scale, depth_bids, depth_offers,
       top_n -> 'bids' -> 0                           AS best_bid,
       top_n -> 'offers' -> 0                         AS best_offer,
       identity ->> 'ok'                              AS identity_exact,
       identity ->> 'refusal'                         AS identity_refusal
  FROM institutional_stream_evidence
 WHERE symbol <> '*' AND recorded_at > now() - interval '30 minutes'
 ORDER BY symbol, minute DESC, recorded_at DESC;

\echo == 3 · per symbol, the last 60 minutes (P5 bounds: receipt age 2 s, skew 2 s) ==
SELECT symbol,
       count(*)                                         AS minutes,
       count(*) FILTER (WHERE current_ok)               AS minutes_current,
       count(*) FILTER (WHERE receipt_age_s <= 2.0
                          AND abs(venue_receipt_skew_s) <= 2.0
                          AND gap_open IS NULL)         AS minutes_inside_p5_bounds,
       sum(connects_in_minute)                          AS connects,
       sum(disconnects_in_minute)                       AS disconnects,
       sum(jsonb_array_length(gap_events))              AS gap_events,
       sum(updates_in_minute)                           AS updates,
       sum(skew_samples)                                AS skew_samples,
       round(min(skew_min_s)::numeric, 3)               AS skew_min_s,
       round((percentile_cont(0.5) WITHIN GROUP
              (ORDER BY skew_p50_s))::numeric, 3)       AS skew_p50_s,
       round(max(skew_max_s)::numeric, 3)               AS skew_max_s,
       round((percentile_cont(0.5) WITHIN GROUP
              (ORDER BY receipt_age_s))::numeric, 3)    AS receipt_age_p50_s,
       round(max(receipt_age_s)::numeric, 3)            AS receipt_age_max_s,
       round(max(max_interarrival_s)::numeric, 3)       AS max_gap_between_updates_s,
       round(max(first_complete_book_after_connect_s)::numeric, 3)
                                                        AS first_book_after_connect_max_s
  FROM institutional_stream_evidence
 WHERE symbol <> '*' AND recorded_at > now() - interval '60 minutes'
 GROUP BY symbol
 ORDER BY symbol;

\echo == 4 · gap events, last 60 minutes ==
SELECT e.symbol, e.minute, g ->> 'event' AS event, g ->> 'reason' AS reason,
       to_timestamp((g ->> 'at')::float8) AS at, g ->> 'seq' AS epoch
  FROM institutional_stream_evidence e,
       LATERAL jsonb_array_elements(e.gap_events) g
 WHERE e.recorded_at > now() - interval '60 minutes'
 ORDER BY at DESC
 LIMIT 100;

\echo == 5 · same-book probe, 24 h: verdicts per symbol ==
SELECT symbol, verdict, verdict_reason,
       count(*)                                         AS samples,
       count(*) FILTER (WHERE NOT stream_changed_in_window)
                                                        AS stream_stable_in_window,
       round((percentile_cont(0.5) WITHIN GROUP
              (ORDER BY window_s))::numeric, 3)         AS window_p50_s,
       round(max(window_s)::numeric, 3)                 AS window_max_s,
       min(probed_at)                                   AS first_at,
       max(probed_at)                                   AS last_at,
       sum(orders_placed)                               AS orders_placed
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '24 hours'
 GROUP BY symbol, verdict, verdict_reason
 ORDER BY symbol, verdict, verdict_reason;

\echo == 5b · same-book totals, 24 h (S1 needs >= 30 comparable, >= 95 % agree, 0 stable disagreements) ==
SELECT count(*) FILTER (WHERE verdict IN ('AGREE_TOP_N', 'AGREE_TOUCH_ONLY',
                                          'DISAGREE'))   AS comparable,
       count(*) FILTER (WHERE verdict = 'AGREE_TOP_N')  AS agree_top_n,
       count(*) FILTER (WHERE verdict = 'AGREE_TOUCH_ONLY')
                                                        AS agree_touch_only,
       count(*) FILTER (WHERE verdict = 'DISAGREE')     AS disagree,
       count(*) FILTER (WHERE verdict = 'DISAGREE'
                          AND NOT stream_changed_in_window)
                                                        AS disagree_stream_stable,
       count(*) FILTER (WHERE verdict = 'NOT_COMPARABLE')
                                                        AS not_comparable,
       sum(orders_placed)                               AS orders_placed
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '24 hours';

\echo == 6 · the newest 20 same-book samples ==
SELECT probed_at, symbol, verdict, verdict_reason, identity_ok,
       stream_refusal, retail_error, window_s, matched_stream_read,
       stream_changed_in_window, connection_epoch,
       stream_book -> 'bids' -> 0   AS stream_best_bid,
       stream_book -> 'offers' -> 0 AS stream_best_offer,
       retail_book -> 'bids' -> 0   AS retail_best_bid,
       retail_book -> 'offers' -> 0 AS retail_best_offer,
       diff -> 'levels'             AS level_diff
  FROM institutional_same_book_probe
 ORDER BY probed_at DESC
 LIMIT 20;

\echo == 7 · the P5 artifact (owner approval) ==
SELECT rule_id, version, status, sha256,
       owner_approval_actor, owner_approved_at, status_changed_at
  FROM live_rule_artifacts
 WHERE rule_id = 'P5_LIVE_STREAM_BOOK_V1';

\echo == 8 · the workers heartbeat (stream digest, evidence, probe) ==
SELECT service, status, beat_at,
       detail -> 'stream' ->> 'state'               AS stream_state,
       detail -> 'stream' ->> 'connection_seq'      AS connection_seq,
       detail -> 'stream' ->> 'messages'            AS messages,
       detail -> 'stream' -> 'by_refusal'           AS by_refusal,
       detail -> 'stream' -> 'start'                AS stream_start,
       detail -> 'streamEvidence'                   AS stream_evidence,
       detail -> 'sameBook'                         AS same_book,
       detail ->> 'marketDataMechanism'             AS rest_mechanism
  FROM service_heartbeats
 WHERE service = 'institutional_md';
