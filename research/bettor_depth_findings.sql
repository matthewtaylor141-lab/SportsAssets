-- DEPTH SHAPE, SETTLEMENT MATURITY, COMPLEMENT IDENTITY -- findings only.
--
-- SPLIT FROM THE EXPORT. Section 8's 40-row JSON export is thousands of
-- lines and pushed these findings out of the retrievable end of the log, so
-- the analysis that has to be READ lives here and the bulk export lives in
-- bettor_replay_export_v3.sql. A result nobody can retrieve is not a result.
--
-- WHY v3 EXISTS. v2 added `yes_depth` to the export and the normalizer was
-- changed in the same commit to REQUIRE depth. The committed sample
-- (`replay_sample_rows.json`) was never regenerated, so it carries no depth
-- and every one of its 36 rows is now rejected NO_DEPTH_REPORTED, while
-- RELEASE_CANDIDATE.md still reports "15 accepted, 15/15 NO_TRADE" from
-- before that change. The document and the code disagree and the code is
-- right.
--
-- AND THE COLUMN I DID EXPORT WAS THE WRONG ONE. The schema probe measured
-- it: on bsv_4d941e01... the quoted ask is 0.7200 with 17 contracts behind
-- it, and `yes_depth.ask` reports 4903.69 -- the sum of all five ask levels,
-- whose other 4886 contracts sit at 0.73 to 0.76. So having found the depth
-- column I read the wrong field out of it. The export now carries the
-- LADDER, which is the only thing an order can be sized from.
--
-- Every column named here was verified against information_schema by
-- bettor_schema_probe.sql first. psql runs with ON_ERROR_STOP=1, so a
-- guessed column name loses the whole run.
--
-- READ ONLY. Counts, aggregates and a bounded export. Nothing is altered.

\echo == 1. SETTLEMENT TABLE SHAPE, which the probe log truncated ==
SELECT ordinal_position AS pos, column_name, data_type
  FROM information_schema.columns
 WHERE table_name = 'bettor_state_settlements'
 ORDER BY ordinal_position;

\echo
\echo == 2. TOP OF BOOK vs FIVE-LEVEL SUM, across every row that has both ==
-- One row proved the shape. This asks how general it is: for each row take
-- the ladder's best ask level and the sum of its levels, and count which one
-- `yes_depth.ask` equals. If it matches the sum on essentially every row,
-- then every size the engine has ever computed was a cumulative figure used
-- as if it were executable at one price.
WITH lad AS (
  SELECT o.observation_id,
         (o.yes_depth->>'ask')::numeric AS reported,
         (SELECT (lv->>'qty')::numeric
            FROM jsonb_array_elements(o.multi_level_depth->'ask') AS lv
           WHERE (lv->>'level')::int = 0
             AND lv->>'qty' ~ '^[0-9.]+$')                  AS top_qty,
         (SELECT sum((lv->>'qty')::numeric)
            FROM jsonb_array_elements(o.multi_level_depth->'ask') AS lv
           WHERE lv->>'qty' ~ '^[0-9.]+$')                  AS sum_qty
    FROM bettor_state_observations o
   WHERE o.observed_at > now() - interval '7 days'
     AND o.yes_depth ? 'ask'
     AND o.multi_level_depth ? 'ask'
     -- Some rows carry {"status": "NOT_IDENTIFIED"} instead of a
     -- number, so every cast is guarded rather than assumed.
     AND o.yes_depth->>'ask' ~ '^[0-9.]+$')
SELECT count(*)                                              AS compared,
       count(*) FILTER (WHERE reported = top_qty)            AS equals_top,
       count(*) FILTER (WHERE reported = sum_qty)            AS equals_sum,
       count(*) FILTER (WHERE reported <> top_qty
                          AND reported <> sum_qty)           AS equals_neither,
       count(*) FILTER (WHERE top_qty = sum_qty)             AS top_is_sum,
       round(min(sum_qty / nullif(top_qty, 0)), 2)           AS min_ratio,
       round(percentile_cont(0.5) WITHIN GROUP (
             ORDER BY sum_qty / nullif(top_qty, 0))::numeric, 2) AS median_ratio,
       round(max(sum_qty / nullif(top_qty, 0)), 2)           AS max_ratio
  FROM lad;

\echo
\echo == 3. HOW MUCH IS EXECUTABLE AT THE TOUCH, in contracts ==
-- The capacity question asked of the real ladder rather than of a guess.
-- $500,000/day at ~$0.50 needs 1,000,000 contract executions/day.
SELECT count(*) AS rows,
       round(percentile_cont(0.10) WITHIN GROUP (ORDER BY q)::numeric, 2) AS p10,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY q)::numeric, 2) AS median,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY q)::numeric, 2) AS p90,
       round(sum(q)::numeric, 0) AS total_top_of_book_contracts
  FROM (SELECT (SELECT (lv->>'qty')::numeric
                  FROM jsonb_array_elements(o.multi_level_depth->'ask') AS lv
                 WHERE (lv->>'level')::int = 0
                   AND lv->>'qty' ~ '^[0-9.]+$') AS q
          FROM bettor_state_observations o
         WHERE o.observed_at > now() - interval '7 days'
           AND o.multi_level_depth ? 'ask') t
 WHERE q IS NOT NULL;

\echo
\echo == 4. THE 11 CANDIDATES: what identity the capture actually holds ==
-- Two rows sharing a market_id with different outcome_leg values is NOT a
-- complement. The probe found NO venue_outcome_id and NO settlement
-- predicate column, so what can be checked is instrument_id, condition_id
-- and identity_status. If the two legs share one instrument_id they are one
-- instrument relabelled, not two.
SELECT market_id,
       count(*)                                   AS rows,
       count(DISTINCT outcome_leg)                AS legs,
       array_agg(DISTINCT outcome_leg)            AS leg_labels,
       count(DISTINCT instrument_id)              AS distinct_instruments,
       count(DISTINCT condition_id)               AS distinct_conditions,
       array_agg(DISTINCT identity_status)        AS identity_status,
       array_agg(DISTINCT market_type)            AS market_types,
       max(observed_at)                           AS last_seen
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY market_id
HAVING count(DISTINCT outcome_leg) > 1
 ORDER BY last_seen DESC
 LIMIT 40;

\echo
\echo == 5. IS A SETTLEMENT PREDICATE ANYWHERE IN THE CAPTURE ==
-- If nothing records how a market resolves, then "these two legs settle
-- under one rule to exactly one winner" is not checkable from our data. That
-- is a finding about the CAPTURE, not about the markets, and the two must
-- not be reported as one.
SELECT count(*) AS rows,
       count(*) FILTER (WHERE condition_id <> 'NOT_IDENTIFIED') AS has_condition,
       count(*) FILTER (WHERE identity_status = 'VENUE_NATIVE_RESOLVED')
                                                                AS venue_native,
       count(DISTINCT identity_status)                          AS id_statuses,
       array_agg(DISTINCT identity_status)                      AS which
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days';

\echo
\echo == 6. MATURITY: has anything we observed had time to resolve ==
-- The bound on "nothing has matured". time_to_event_s is seconds until the
-- event starts, so a NEGATIVE value means the event has already begun. A
-- market whose game began two days ago and still has no settlement row is an
-- INGESTION gap, not an immature outcome. The two were conflated.
SELECT count(*)                                                AS rows,
       count(DISTINCT market_id)                               AS markets,
       count(*) FILTER (WHERE time_to_event_s ~ '^-')          AS event_started,
       count(*) FILTER (WHERE live_status <> 'PREGAME')        AS not_pregame,
       array_agg(DISTINCT live_status)                         AS live_statuses,
       min(observed_at)                                        AS earliest_obs
  FROM bettor_state_observations;

\echo
\echo == 7. CLOCK: source-to-receipt delay is NOT clock skew ==
-- A positive delay is consistent with network latency, our clock being
-- behind, or both. Only a NEGATIVE delay -- a source stamp in our receipt's
-- future -- demonstrates disagreement rather than transport, because
-- transport only runs forward. Both tails are reported so the distinction is
-- visible instead of asserted.
SELECT count(*) AS rows,
       count(*) FILTER (WHERE book_received_ts < book_source_ts::timestamptz)
              AS source_after_receipt,
       round(min(extract(epoch FROM book_received_ts
                                  - book_source_ts::timestamptz))::numeric, 3)
              AS min_delay_s,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY extract(epoch FROM book_received_ts
                                       - book_source_ts::timestamptz))
             ::numeric, 3) AS median_delay_s,
       round(max(extract(epoch FROM book_received_ts
                                  - book_source_ts::timestamptz))::numeric, 3)
              AS max_delay_s
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND book_source_ts <> 'NOT_IDENTIFIED'
   AND book_received_ts IS NOT NULL;

\echo
