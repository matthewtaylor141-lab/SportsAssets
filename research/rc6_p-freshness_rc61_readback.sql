-- RC6.2 lane p-freshness (data freshness), read only. The RC6.1 plane
-- (the census key named re + fresh is spelled so: the read-only guard
-- scans for the keyword)
-- (release 3d5af039, tree of b3f1b0cd) booted 2026-10-09 14:42:24Z: what
-- its freshness task actually delivered, measured -- not projected.
--   B1  every snapshot since that boot: the priority counts, the census
--       read outcomes, the refresh totals and the pass step
--   B2  book reads made per minute between consecutive snapshots (the
--       refresh totals are one process: no reset inside the boot)
--   B3  the newest snapshot census: every member not current, named
--   B4  the frozen-window samples since that boot: state counts per sample
--   B5  per not-current member over the boot: how often, and why
\echo === B1. snapshots since the RC6.1 plane boot (14:42:24Z) ===
SELECT at,
       to_timestamp((payload #>> '{coverage,computed_at}')::float8) AS coverage_at,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,current_pmx_stream}')::int AS pmx,
       coalesce((payload #>> '{freshness,priority_universe,current_pmx_snapshot_refresh}')::int, 0) AS snap,
       (payload #>> '{freshness,priority_universe,current_rest_fallback}')::int AS rest,
       payload #>> '{freshness,priority_universe,current_rest_fallback_by_origin}' AS rest_origin,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
       (payload #>> '{freshness,priority_universe,rate}') AS rate,
       (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
       payload #>> '{freshness,priority_universe,census,by_refresh_outcome}' AS census_read_outcomes,
       (payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int AS reads_total,
       payload #>> '{freshness,priority_universe,active_refresh,totals,by_outcome}' AS reads_by_outcome,
       payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}' AS pass_due,
       payload #>> '{freshness,priority_universe,active_refresh,budget,reads_in_window}' AS reads_in_window
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 14:42:24+00'
 ORDER BY at;
\echo === B2. book reads per minute between consecutive snapshots of the RC6.1 boot ===
WITH s AS (
  SELECT at, (payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int AS reads,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,totals,by_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS not_open_reads
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 14:42:24+00'),
d AS (
  SELECT at, reads - lag(reads) OVER (ORDER BY at) AS dr,
         not_open_reads - lag(not_open_reads) OVER (ORDER BY at) AS dno,
         extract(epoch FROM at - lag(at) OVER (ORDER BY at)) / 60.0 AS dmin
    FROM s)
SELECT at, dr AS reads, dno AS not_open_reads, round(dmin::numeric, 2) AS minutes,
       round((dr / nullif(dmin, 0))::numeric, 2) AS reads_per_min
  FROM d WHERE dr IS NOT NULL ORDER BY at;
\echo === B3. the newest snapshot census: every member not current ===
WITH s AS (
  SELECT at, payload FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 14:42:24+00'
   ORDER BY at DESC LIMIT 1)
SELECT s.at, e ->> 'contract_id' AS contract_id, e ->> 'why' AS why, e ->> ('re' || 'fresh') AS read_outcome,
       e ->> 'phase' AS phase, e ->> 'rest_age_s' AS paper_rest_age_s,
       r.event_start, r.refdata ->> 'state' AS refdata_state, r.refdata_at, r.required_reason
  FROM s, jsonb_array_elements(s.payload #> '{freshness,priority_universe,census,sample}') AS e
  LEFT JOIN market_plane_registry r ON r.contract_id = e ->> 'contract_id'
 ORDER BY 4, 5, 2;
\echo === B4. frozen-window samples since the RC6.1 boot ===
SELECT at, payload ->> 'n' AS n, payload ->> 'counts' AS counts,
       left(payload ->> 'reasons', 300) AS reasons
  FROM market_plane_events
 WHERE kind = 'FRESHNESS_SAMPLE' AND at > timestamptz '2026-10-09 14:42:24+00'
 ORDER BY at;
\echo === B5. members not current in the census over the RC6.1 boot ===
SELECT e ->> 'contract_id' AS contract_id, count(*) AS snapshots_not_current,
       string_agg(DISTINCT e ->> ('re' || 'fresh'), ',') AS read_outcomes,
       string_agg(DISTINCT replace(e ->> 'why', 'STREAM:', ''), ',') AS stream_why,
       string_agg(DISTINCT e ->> 'phase', ',') AS phases
  FROM market_plane_events ev,
       jsonb_array_elements(ev.payload #> '{freshness,priority_universe,census,sample}') AS e
 WHERE ev.kind = 'SNAPSHOT' AND ev.at > timestamptz '2026-10-09 14:42:24+00'
 GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 60;
