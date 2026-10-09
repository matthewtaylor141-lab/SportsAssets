-- RC6.2 lane p-freshness (data freshness), read only. Follows
-- rc6_p-freshness_members.sql / rc6_p-freshness_capacity.sql.
--   P1  THE SCORECARD INSTANT EXACTLY: the 05:20:07Z snapshot that
--       completion read (174 / 192) carries the coverage pass verified at
--       05:18:59Z; the 05:19:47Z snapshot's census was taken at that same
--       instant (computed_at = coverage computed_at) and names exactly the
--       members not current then. Every one with its stream refusal, the
--       plane's last book-read outcome, the event phase, the registry row,
--       the instrument state the plane's reference data holds, the stream
--       book age, the paper runtime's last read; and the read pass then.
--   P2  every snapshot since the 732cc0c6 plane booted: the members a
--       budget-limited read can make current. Per snapshot, from its own
--       counts: T = members the venue's own word keeps not current (the
--       plane's last read said the market is not open; or the stream says
--       MARKET_NOT_OPEN; or the event started > 4 h ago) -- estimated as
--       greatest(read-outcome NOT_OPEN, started_gt_4h + stream not-open);
--       Q = members quiet on the stream and refreshable; open_q = Q minus
--       the read-outcome NOT_OPEN; cap = 57 - 0.317 x NOT_OPEN (12 reads a
--       minute x 285 s, less the 900 s re-reads of not-open markets).
--         REST at 12 a minute (RC6.1):  nc_rest = T + not_held + max(0, open_q - cap)
--         + one snapshot call a minute: nc_snap = T + not_held
--       each capped at the snapshot's actual not-current count.
--   P3  paper book reads the coverage pass counts current for priority
--       members (any row inside 300 s, error or not): how many are errors.
-- A census member's plane book-read outcome is the one key left once its
-- named keys (contract_id, tier, why, phase, rest_age_s) are removed.
\echo === P1. the census taken at the scorecard coverage instant (05:18:59Z) ===
SELECT at, to_timestamp((payload ->> 'computed_at')::float8) AS computed_at,
       to_timestamp((payload #>> '{coverage,computed_at}')::float8) AS coverage_at,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
       (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
       payload #> '{freshness,priority_universe,census,by_refresh_outcome}' AS read_outcomes,
       payload #> '{freshness,priority_universe,active_refresh,last_pass,by_reason}' AS pass_by_reason,
       payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}' AS due
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT'
   AND at BETWEEN timestamptz '2026-10-09 05:19:00+00' AND timestamptz '2026-10-09 05:20:00+00';
\echo === P1b. those members ===
WITH s AS (
  SELECT at, payload FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND at BETWEEN timestamptz '2026-10-09 05:19:00+00' AND timestamptz '2026-10-09 05:20:00+00'
   ORDER BY at LIMIT 1),
m AS (
  SELECT s.at AS snap_at, to_timestamp((s.payload ->> 'computed_at')::float8) AS at0,
         e ->> 'contract_id' AS cid, e ->> 'why' AS why, e ->> 'phase' AS phase,
         (SELECT string_agg(t.v, ',') FROM jsonb_each_text(e - ARRAY['contract_id','tier','why','phase','rest_age_s']) AS t(k, v)) AS rd
    FROM s, jsonb_array_elements(s.payload #> '{freshness,priority_universe,census,sample}') AS e),
pb AS (
  SELECT b.k AS cid, max((b.v ->> 'received_at')::float8) AS rcv
    FROM market_plane_events ev, jsonb_each(ev.payload -> 'books') AS b(k, v)
   WHERE ev.kind = 'PRIORITY_PMX_BOOKS'
     AND ev.at BETWEEN timestamptz '2026-10-09 03:26:00+00' AND timestamptz '2026-10-09 05:19:00+00'
   GROUP BY 1)
SELECT m.cid AS contract_id, replace(m.why, 'STREAM:', '') AS stream_why, m.rd AS read_outcome,
       m.phase, r.event_start,
       round(extract(epoch FROM m.at0 - r.event_start)::numeric / 3600, 2) AS h_since_start,
       r.refdata ->> 'state' AS refdata_state_now, r.refdata_at,
       r.required_reason AS reason_now,
       (SELECT max(c.cycle_at) FROM ext_candidate_outcomes c
         WHERE c.us_market_slug = m.cid AND c.cycle_at <= m.at0) AS last_eval_at,
       round((extract(epoch FROM m.at0) - pb.rcv)::numeric, 0) AS stream_book_age_s,
       (SELECT o.market_state || '|' || coalesce(left(o.error, 30), 'ok') || '|' ||
               round(extract(epoch FROM m.at0 - o.observed_at)::numeric, 0)
          FROM paper_book_observations o
         WHERE o.us_market_slug = m.cid AND o.observed_at <= m.at0
           AND o.observed_at > m.at0 - interval '12 hours'
         ORDER BY o.observed_at DESC LIMIT 1) AS paper_last_state_age_s,
       (SELECT o.market_state || '|' ||
               round(extract(epoch FROM o.observed_at - m.at0)::numeric, 0)
          FROM paper_book_observations o
         WHERE o.us_market_slug = m.cid AND o.observed_at > m.at0
           AND o.observed_at < m.at0 + interval '12 hours'
           AND o.error IS NULL
         ORDER BY o.observed_at LIMIT 1) AS paper_next_state_after_s
  FROM m
  LEFT JOIN market_plane_registry r ON r.contract_id = m.cid
  LEFT JOIN pb ON pb.cid = m.cid
 ORDER BY 3, r.event_start NULLS LAST, 1;
\echo === P2. projection per hour since boot (see header for the formulas) ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT at, den, nc, q, no_open, not_held,
         greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q,
         57 - 0.317 * no_open AS cap
    FROM s),
r AS (
  SELECT at, den, nc, t, q, open_q, cap,
         least(nc, t + not_held + greatest(open_q - cap, 0)) AS nc_rest,
         least(nc, t + not_held) AS nc_snap
    FROM p)
SELECT to_timestamp(floor(extract(epoch FROM at) / 3600) * 3600) AS hour, count(*) AS snaps,
       round(avg(den), 1) AS den, round(avg(den - nc), 1) AS num_actual,
       round(avg(den - nc_rest), 1) AS num_rest12, round(avg(den - nc_snap), 1) AS num_snap,
       round(avg(t), 1) AS venue_not_open, round(avg(q), 1) AS q, round(avg(open_q), 1) AS open_q,
       round(avg((den - nc)::numeric / den), 4) AS rate_actual,
       round(avg((den - nc_rest)::numeric / den), 4) AS rate_rest12,
       round(avg((den - nc_snap)::numeric / den), 4) AS rate_snap,
       round(avg((den - nc_snap + t)::numeric / den), 4) AS rate_snap_if_t_were_current,
       count(*) FILTER (WHERE (den - nc)::numeric / den >= 0.95) AS ge95_actual,
       count(*) FILTER (WHERE (den - nc_rest)::numeric / den >= 0.95) AS ge95_rest12,
       count(*) FILTER (WHERE (den - nc_snap)::numeric / den >= 0.95) AS ge95_snap
  FROM r GROUP BY 1 ORDER BY 1;
\echo === P2b. the same, all snapshots since boot ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT den, nc, q, not_held, greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q, 57 - 0.317 * no_open AS cap
    FROM s),
r AS (
  SELECT den, nc, t, q, open_q,
         least(nc, t + not_held + greatest(open_q - cap, 0)) AS nc_rest,
         least(nc, t + not_held) AS nc_snap
    FROM p)
SELECT count(*) AS snaps, sum(den) AS member_snapshots,
       sum(den - nc) AS fresh_actual, sum(den - nc_rest) AS fresh_rest12,
       sum(den - nc_snap) AS fresh_snap, sum(t) AS venue_not_open,
       round(sum(den - nc)::numeric / sum(den), 4) AS rate_actual,
       round(sum(den - nc_rest)::numeric / sum(den), 4) AS rate_rest12,
       round(sum(den - nc_snap)::numeric / sum(den), 4) AS rate_snap,
       round(sum(t)::numeric / sum(den), 4) AS share_venue_not_open,
       count(*) FILTER (WHERE (den - nc)::numeric / den >= 0.95) AS ge95_actual,
       count(*) FILTER (WHERE (den - nc_rest)::numeric / den >= 0.95) AS ge95_rest12,
       count(*) FILTER (WHERE (den - nc_snap)::numeric / den >= 0.95) AS ge95_snap,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY open_q)::numeric, 1) AS open_q_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY open_q)::numeric, 1) AS open_q_p90,
       max(open_q) AS open_q_max
  FROM r;
\echo === P3. paper book reads of priority members the coverage pass counts current (any row in 300 s), last 6 h, hourly ===
SELECT date_trunc('hour', o.observed_at) AS hour, count(*) AS rows,
       count(*) FILTER (WHERE o.error IS NOT NULL) AS error_rows,
       count(DISTINCT o.us_market_slug) AS members,
       count(DISTINCT o.us_market_slug) FILTER (WHERE o.error IS NOT NULL) AS members_with_error_rows,
       count(*) FILTER (WHERE o.error IS NULL AND coalesce(o.market_state, '') NOT IN ('MARKET_STATE_OPEN', 'INSTRUMENT_STATE_OPEN')) AS ok_rows_not_open
  FROM paper_book_observations o
  JOIN market_plane_registry r ON r.contract_id = o.us_market_slug
 WHERE o.observed_at > now() - interval '6 hours' AND r.active AND r.priority <= 10
 GROUP BY 1 ORDER BY 1;
\echo === P3b. paper error reads by error text (last 6 h, priority members) ===
SELECT left(o.error, 60) AS error, count(*) AS rows, count(DISTINCT o.us_market_slug) AS members
  FROM paper_book_observations o
  JOIN market_plane_registry r ON r.contract_id = o.us_market_slug
 WHERE o.observed_at > now() - interval '6 hours' AND r.active AND r.priority <= 10
   AND o.error IS NOT NULL
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
