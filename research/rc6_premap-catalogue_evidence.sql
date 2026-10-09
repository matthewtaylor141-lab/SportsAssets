-- READ-ONLY. RC6 LANE P0-COMPLETENESS (PREMAP AND CATALOGUE): THE PRODUCTION
-- BASELINE THE LANE'S FIXES ARE MEASURED AGAINST.
--
--   1 the premap state records (premap_last / _fast / _calendar): mode,
--     events, rows, pages, truncated, err, at -- the receipt body left out
--   2 the full / fast / calendar receipts of the last 48 h by outcome, and
--     every full-lane receipt that was not COMPLETE, with its window pass's
--     stop and error and how long it ran
--   3 the full lane's sweep durations (finished - started) over 48 h
--   4 the metadata heartbeat: status, gamma paging, sports markets kept
--   5 the metadata cache: open sports markets in `markets`, and how many
--      were written in the last hour / day
--   6 the research collector (bettor_state): its heartbeat, its ticks' status
--     counts over 24 h, its rows by universe version over 24 h and the share
--     marked slice_truncated
--
-- Every statement is a SELECT.

\echo == 1. premap state records ==
SELECT key,
       value->>'mode'              AS mode,
       value->>'lane'              AS lane,
       value->>'events'            AS events,
       value->>'rows'              AS rows_written,
       value->>'pages_walked'      AS pages_walked,
       value->>'truncated'         AS truncated,
       value->>'catalogue_complete' AS catalogue_complete,
       left(coalesce(value->>'err', ''), 120) AS err,
       value->>'duration_s'        AS duration_s,
       value->>'at'                AS at
  FROM ingestion_state
 WHERE key IN ('premap_last', 'premap_last_fast', 'premap_last_calendar')
 ORDER BY key;

\echo == 2a. catalogue receipts, last 48 h, by lane and outcome ==
SELECT lane, outcome, count(*) AS receipts,
       min(recorded_at) AS first_at, max(recorded_at) AS last_at,
       round(avg(events_seen)) AS avg_events_seen,
       round(avg(pages_read)) AS avg_pages
  FROM venue_catalogue_receipts
 WHERE recorded_at > now() - interval '48 hours'
 GROUP BY lane, outcome
 ORDER BY lane, outcome;

\echo == 2b. full-lane receipts that were not COMPLETE, last 48 h ==
SELECT id, recorded_at, outcome, pages_read, requests, events_seen,
       sides_written,
       round(extract(epoch FROM finished_at - started_at)) AS seconds,
       receipt->'passes'->'WINDOW'->>'stopped' AS window_stopped,
       left(coalesce(receipt->'passes'->'WINDOW'->>'error', ''), 140)
           AS window_error
  FROM venue_catalogue_receipts
 WHERE lane = 'full' AND outcome <> 'COMPLETE'
   AND recorded_at > now() - interval '48 hours'
 ORDER BY recorded_at DESC
 LIMIT 40;

\echo == 3. full-lane sweep durations, last 48 h ==
SELECT outcome, count(*) AS sweeps,
       round(min(extract(epoch FROM finished_at - started_at))) AS min_s,
       round(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM finished_at - started_at))::numeric)
           AS p50_s,
       round(max(extract(epoch FROM finished_at - started_at))) AS max_s,
       round(avg(events_seen)) AS avg_events,
       round(avg(sides_written)) AS avg_rows
  FROM venue_catalogue_receipts
 WHERE lane = 'full' AND recorded_at > now() - interval '48 hours'
 GROUP BY outcome
 ORDER BY outcome;

\echo == 4. the metadata heartbeat ==
SELECT service, status, beat_at,
       detail->'gamma_paging'          AS gamma_paging,
       detail->>'active_sports_markets' AS active_sports_markets,
       detail->'errors'                AS errors
  FROM service_heartbeats
 WHERE service = 'metadata';

\echo == 5. the metadata cache: open sports markets ==
SELECT count(*) FILTER (WHERE NOT closed) AS open_markets,
       count(*) FILTER (WHERE NOT closed AND sport NOT IN
                        ('unclassified', 'Non-Sports')) AS open_sports_markets,
       count(*) FILTER (WHERE updated_at > now() - interval '1 hour')
           AS written_last_hour,
       count(*) FILTER (WHERE updated_at > now() - interval '24 hours')
           AS written_last_day,
       count(*) FILTER (WHERE NOT closed AND updated_at
                        < now() - interval '24 hours') AS open_not_written_24h
  FROM markets;

\echo == 6a. the research collector heartbeat ==
SELECT service, status, beat_at,
       detail->>'universe'          AS universe,
       detail->>'inSlice'           AS in_slice,
       detail->>'sliceTruncatedBy'  AS slice_truncated_by,
       detail->>'eligible'          AS eligible,
       detail->>'reads'             AS reads,
       detail->>'pacingS'           AS pacing_s
  FROM service_heartbeats
 WHERE service = 'bettor_state';

\echo == 6b. the research collector ticks, last 24 h, by status ==
SELECT universe_version, status, count(*) AS ticks,
       sum(obs_scheduled) AS scheduled, sum(obs_attempted) AS attempted,
       sum(obs_written) AS written
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '24 hours'
 GROUP BY universe_version, status
 ORDER BY universe_version, ticks DESC;

\echo == 6c. the research collector rows, last 24 h, by version ==
SELECT universe_version, count(*) AS rows_observed,
       count(*) FILTER (WHERE slice_truncated) AS slice_truncated_rows,
       round(avg(slice_truncated_by) FILTER (WHERE slice_truncated), 1)
           AS avg_truncated_by,
       max(slice_truncated_by) AS max_truncated_by,
       count(DISTINCT market_id) AS markets
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '24 hours'
 GROUP BY universe_version
 ORDER BY universe_version;
