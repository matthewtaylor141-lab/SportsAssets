-- RC6.2 lane p-freshness (data freshness), read only.
-- The scorecard's priority_members_fresh 174 / 192 (pm-acceptance
-- 37888018192, completion computed 2026-10-09 05:21:11Z) is ONE plane
-- snapshot's coverage-pass instant. This names the members that were not
-- current in that snapshot and in every snapshot since the 732cc0c6 plane
-- booted (03:26:48Z), with the stream's refusal, the plane's own book read
-- outcome, the event phase, the registry row, the candidate evaluations
-- that keep the member in the set, the last stream book the plane recorded
-- for it and the paper runtime's last REST read -- so each can be classed
-- (our scheduler / the read budget / the venue / the measurement).
-- A census member's plane book-read outcome is the one key left once its
-- named keys (contract_id, tier, why, phase, rest_age_s) are removed.
--   A  the plane's boot records and heartbeats now (which build runs)
--   B  the snapshot nearest the scorecard's read: counts, the read pass
--   C  that snapshot's census: reasons, read outcomes, every member named
--   D  those members now and at that instant (registry, candidates, books)
--   E  the not-current members of every snapshot since boot, per member
\echo === A. plane boot records and heartbeats now ===
SELECT service, status,
       round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail ->> 'commit' AS commit_sha,
       to_timestamp((detail ->> 'started_at')::float8) AS started_at,
       detail ? 'freshness_task' AS has_freshness_task,
       detail #>> '{freshness_task,ticks}' AS task_ticks,
       detail #>> '{freshness_task,reads}' AS task_reads,
       detail #>> '{freshness_task,errors}' AS task_errors
  FROM service_heartbeats
 WHERE service IN ('market_plane', 'universal_market_plane')
 ORDER BY 1;
\echo === B. snapshots 05:05-05:30Z (the scorecard read the newest at or before 05:21:11Z) ===
SELECT at,
       to_timestamp((payload ->> 'computed_at')::float8) AS computed_at,
       to_timestamp((payload #>> '{coverage,computed_at}')::float8) AS coverage_at,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,current_pmx_stream}')::int AS pmx,
       coalesce((payload #>> '{freshness,priority_universe,current_pmx_snapshot_refresh}')::int, 0) AS snap,
       (payload #>> '{freshness,priority_universe,current_rest_fallback}')::int AS rest,
       payload #>> '{freshness,priority_universe,current_rest_fallback_by_origin}' AS rest_origin,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
       (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
       payload #>> '{freshness,priority_universe,census,by_refresh_outcome}' AS read_outcomes,
       payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}' AS due,
       payload #>> '{freshness,priority_universe,active_refresh,last_pass,read}' AS rd,
       payload #>> '{freshness,priority_universe,active_refresh,last_pass,deferred_why}' AS deferred_why,
       payload #>> '{freshness,priority_universe,active_refresh,budget,reads_in_window}' AS reads_in_window,
       payload #>> '{freshness,priority_universe,active_refresh,totals,reads}' AS reads_total,
       payload #>> '{freshness,priority_universe,active_refresh,current_via_refresh}' AS via_read
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT'
   AND at BETWEEN timestamptz '2026-10-09 05:05:00+00' AND timestamptz '2026-10-09 05:30:00+00'
 ORDER BY at;
\echo === C. the scorecard snapshot: census reasons, read outcomes, last read pass ===
WITH s AS (
  SELECT at, payload FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND at BETWEEN timestamptz '2026-10-09 04:50:00+00' AND timestamptz '2026-10-09 05:21:12+00'
   ORDER BY at DESC LIMIT 1)
SELECT at,
       payload #>> '{freshness,priority_universe,census,members}' AS census_members,
       payload #>> '{freshness,priority_universe,census,not_current}' AS census_nc,
       payload #>> '{freshness,priority_universe,census,current_via_refresh}' AS census_via_read,
       payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}' AS by_reason,
       payload #> '{freshness,priority_universe,census,by_refresh_outcome}' AS by_read_outcome,
       payload #> '{freshness,priority_universe,census,quiet_valid_counterfactual}' AS quiet_cf,
       payload #> '{freshness,priority_universe,active_refresh,last_pass,by_reason}' AS pass_by_reason,
       payload #> '{freshness,priority_universe,active_refresh,totals}' AS read_totals
  FROM s;
\echo === C2. the scorecard snapshot: every not-current member the census named ===
WITH s AS (
  SELECT at, payload FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND at BETWEEN timestamptz '2026-10-09 04:50:00+00' AND timestamptz '2026-10-09 05:21:12+00'
   ORDER BY at DESC LIMIT 1)
SELECT m ->> 'contract_id' AS contract_id, m ->> 'tier' AS tier,
       m ->> 'why' AS stream_why, (SELECT string_agg(t.v, ',') FROM jsonb_each_text(m - ARRAY['contract_id','tier','why','phase','rest_age_s']) AS t(k, v)) AS read_outcome,
       m ->> 'phase' AS phase, m ->> 'rest_age_s' AS paper_rest_age_s
  FROM s, jsonb_array_elements(s.payload #> '{freshness,priority_universe,census,sample}') AS m
 ORDER BY 4, 5, 1;
\echo === D. those members: registry now, candidate evaluations, last stream book, last paper read (all at the snapshot instant) ===
WITH s AS (
  SELECT at, payload FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND at BETWEEN timestamptz '2026-10-09 04:50:00+00' AND timestamptz '2026-10-09 05:21:12+00'
   ORDER BY at DESC LIMIT 1),
m AS (
  SELECT s.at AS snap_at, e ->> 'contract_id' AS cid
    FROM s, jsonb_array_elements(s.payload #> '{freshness,priority_universe,census,sample}') AS e),
pb AS (
  SELECT b.k AS cid, max((b.v ->> 'received_at')::float8) AS rcv
    FROM market_plane_events ev, jsonb_each(ev.payload -> 'books') AS b(k, v)
   WHERE ev.kind = 'PRIORITY_PMX_BOOKS'
     AND ev.at BETWEEN timestamptz '2026-10-09 03:26:00+00' AND timestamptz '2026-10-09 05:21:12+00'
   GROUP BY 1)
SELECT m.cid AS contract_id, r.required_reason AS reason_now, r.priority AS prio_now,
       r.active AS active_now, r.event_start,
       round(extract(epoch FROM m.snap_at - r.event_start)::numeric / 3600, 2) AS h_since_start,
       r.refdata ->> 'state' AS refdata_state, r.refdata_at,
       (SELECT max(p.listing_state) FROM us_premap p WHERE p.market_slug = m.cid) AS premap_listing,
       (SELECT count(*) FROM ext_candidate_outcomes c
         WHERE c.us_market_slug = m.cid
           AND c.cycle_at BETWEEN m.snap_at - interval '6 hours' AND m.snap_at) AS evals_6h,
       (SELECT max(c.cycle_at) FROM ext_candidate_outcomes c
         WHERE c.us_market_slug = m.cid AND c.cycle_at <= m.snap_at) AS last_eval_at,
       (SELECT max(c.outcome) FROM ext_candidate_outcomes c
         WHERE c.us_market_slug = m.cid
           AND c.cycle_at BETWEEN m.snap_at - interval '6 hours' AND m.snap_at) AS eval_outcome,
       round((extract(epoch FROM m.snap_at) - pb.rcv)::numeric, 0) AS stream_book_age_s,
       (SELECT max(o.observed_at) FROM paper_book_observations o
         WHERE o.us_market_slug = m.cid AND o.observed_at <= m.snap_at
           AND o.observed_at > m.snap_at - interval '12 hours') AS paper_last_obs,
       (SELECT o.market_state || '|' || coalesce(left(o.error, 40), 'ok')
          FROM paper_book_observations o
         WHERE o.us_market_slug = m.cid AND o.observed_at <= m.snap_at
           AND o.observed_at > m.snap_at - interval '12 hours'
         ORDER BY o.observed_at DESC LIMIT 1) AS paper_last_state
  FROM m
  LEFT JOIN market_plane_registry r ON r.contract_id = m.cid
  LEFT JOIN pb ON pb.cid = m.cid
 ORDER BY r.event_start NULLS LAST, 1;
\echo === E. every snapshot since boot (03:26:48Z) to 09:00Z: not-current members, per member ===
WITH s AS (
  SELECT at, payload #> '{freshness,priority_universe,census,sample}' AS sample
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND at BETWEEN timestamptz '2026-10-09 03:27:00+00' AND timestamptz '2026-10-09 09:00:00+00'),
n AS (SELECT count(*) AS snaps FROM s),
e AS (
  SELECT s.at, x ->> 'contract_id' AS cid, x ->> 'why' AS why,
         (SELECT string_agg(t.v, ',') FROM jsonb_each_text(x - ARRAY['contract_id','tier','why','phase','rest_age_s']) AS t(k, v)) AS rd, x ->> 'phase' AS phase
    FROM s, jsonb_array_elements(coalesce(s.sample, '[]'::jsonb)) AS x)
SELECT e.cid AS contract_id, (SELECT snaps FROM n) AS snaps,
       count(*) AS snaps_not_current, min(e.at) AS first_nc, max(e.at) AS last_nc,
       string_agg(DISTINCT e.phase, ',') AS phases,
       string_agg(DISTINCT replace(e.why, 'STREAM:', ''), ',') AS stream_whys,
       string_agg(DISTINCT coalesce(e.rd, '-'), ',') AS read_outcomes
  FROM e GROUP BY 1 ORDER BY 3 DESC, 1 LIMIT 120;
