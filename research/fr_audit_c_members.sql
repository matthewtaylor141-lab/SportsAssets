-- Freshness root-cause audit (group freshness), read only.
-- Which priority members are not current and why (source, age, state), and
-- why there is no held-position denominator.
--   C1  the snapshot the scorecard read (completion priority_freshness
--       218/265, plane snapshot computed 2026-10-10 09:52:12Z): census
--       totals and every named member with its registry row
--   C2  the last 24 h of census samples: read outcome x phase
--   C3  the members whose last book read said the market is not open
--       (ACTIVE_REFRESH_MARKET_NOT_OPEN): venue state in the registry,
--       event start, last candidate evaluation
--   C4  the PAPER book: open positions now, positions opened a day, how long
--       a position stays open, the newest BUY and SELL
\echo === C1a. the scorecard snapshot: totals ===
SELECT at,
       to_timestamp((payload ->> 'computed_at')::float8) AS computed_at,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,current_pmx_stream}')::int AS pmx,
       (payload #>> '{freshness,priority_universe,current_rest_fallback}')::int AS rest,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
       (payload #>> '{freshness,priority_universe,external_unavailable}')::int AS ext,
       payload #>> '{freshness,priority_universe,rate}' AS rate,
       (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
       payload #> '{freshness,priority_universe,census,by_refresh_outcome}' AS read_outcomes,
       payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}' AS by_reason,
       payload #> '{freshness,priority_universe,census,quiet_valid_counterfactual}' AS quiet_cf,
       payload #> '{freshness,priority_universe,active_refresh,last_pass}' AS last_pass
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT'
   AND abs((payload ->> 'computed_at')::float8 - 1791625932.2113914) < 120
 ORDER BY abs((payload ->> 'computed_at')::float8 - 1791625932.2113914) LIMIT 1;
\echo === C1b. the scorecard snapshot: the named not-current members with the registry row ===
WITH s AS (
  SELECT at, payload FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND abs((payload ->> 'computed_at')::float8 - 1791625932.2113914) < 120
   ORDER BY abs((payload ->> 'computed_at')::float8 - 1791625932.2113914) LIMIT 1),
m AS (
  SELECT s.at AS snap_at, e ->> 'contract_id' AS cid, e ->> 'why' AS stream_why,
         e ->> 'phase' AS phase, e ->> concat('ref','resh') AS read_outcome,
         e ->> 'rest_age_s' AS paper_rest_age_s
    FROM s, jsonb_array_elements(s.payload #> '{freshness,priority_universe,census,sample}') AS e)
SELECT m.cid AS contract_id, m.phase, m.read_outcome, replace(m.stream_why, 'STREAM:', '') AS stream_why,
       m.paper_rest_age_s,
       r.refdata ->> 'state' AS refdata_state_now, r.event_start,
       round(extract(epoch FROM m.snap_at - r.event_start)::numeric / 3600, 1) AS h_since_start,
       r.priority AS prio_now, r.required_reason AS reason_now, r.active AS active_now,
       (SELECT max(c.cycle_at) FROM ext_candidate_outcomes c
         WHERE c.us_market_slug = m.cid AND c.cycle_at <= m.snap_at) AS last_eval_at,
       (SELECT o.market_state FROM paper_book_observations o
         WHERE o.us_market_slug = m.cid AND o.observed_at <= m.snap_at
           AND o.observed_at > m.snap_at - interval '12 hours' AND o.error IS NULL
         ORDER BY o.observed_at DESC LIMIT 1) AS paper_last_ok_state
  FROM m LEFT JOIN market_plane_registry r ON r.contract_id = m.cid
 ORDER BY m.read_outcome, m.phase, 1;
\echo === C2. census samples, last 24 h: last read outcome x phase (member-snapshots and distinct members) ===
WITH s AS (
  SELECT at, x FROM market_plane_events,
       jsonb_array_elements(payload #> '{freshness,priority_universe,census,sample}') AS x
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours')
SELECT coalesce(x ->> concat('ref','resh'), '-') AS last_read_outcome, x ->> 'phase' AS phase,
       replace(x ->> 'why', 'STREAM:', '') AS stream_why,
       count(*) AS member_snapshots, count(DISTINCT x ->> 'contract_id') AS distinct_members
  FROM s GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;
\echo === C3. members whose last read said the market is not open (last 24 h of samples): venue state and age ===
WITH s AS (
  SELECT at, x ->> 'contract_id' AS cid FROM market_plane_events,
       jsonb_array_elements(payload #> '{freshness,priority_universe,census,sample}') AS x
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours'
     AND x ->> concat('ref','resh') = 'ACTIVE_REFRESH_MARKET_NOT_OPEN'),
d AS (SELECT cid, count(*) AS snaps, min(at) AS first_seen, max(at) AS last_seen FROM s GROUP BY 1)
SELECT coalesce(r.refdata ->> 'state', 'NO_REFDATA') AS refdata_state_now,
       count(*) AS members, sum(d.snaps) AS member_snapshots,
       round(avg(extract(epoch FROM d.last_seen - r.event_start) / 3600)::numeric, 1) AS avg_h_since_start_at_last_seen,
       round(min(extract(epoch FROM d.last_seen - r.event_start) / 3600)::numeric, 1) AS min_h_since_start,
       round(max(extract(epoch FROM d.last_seen - r.event_start) / 3600)::numeric, 1) AS max_h_since_start,
       count(*) FILTER (WHERE r.event_start IS NULL) AS no_event_start,
       count(*) FILTER (WHERE r.active) AS still_active
  FROM d LEFT JOIN market_plane_registry r ON r.contract_id = d.cid
 GROUP BY 1 ORDER BY 2 DESC;
\echo === C3b. those members: how long they sat in the priority set (first to last census) ===
WITH s AS (
  SELECT at, x ->> 'contract_id' AS cid FROM market_plane_events,
       jsonb_array_elements(payload #> '{freshness,priority_universe,census,sample}') AS x
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours'
     AND x ->> concat('ref','resh') = 'ACTIVE_REFRESH_MARKET_NOT_OPEN'),
d AS (SELECT cid, count(*) AS snaps, min(at) AS first_seen, max(at) AS last_seen FROM s GROUP BY 1)
SELECT count(*) AS members,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM last_seen - first_seen) / 3600)::numeric, 2) AS span_h_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM last_seen - first_seen) / 3600)::numeric, 2) AS span_h_p90,
       round(max(extract(epoch FROM last_seen - first_seen) / 3600)::numeric, 2) AS span_h_max,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY snaps)::numeric, 1) AS snaps_p50
  FROM d;
\echo === C4a. PAPER book: canonically open positions now ===
SELECT count(*) AS open_positions_now, coalesce(sum(open_qty), 0) AS open_qty
  FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9) c;
\echo === C4b. PAPER fills per day, last 8 days (BUY / SELL fills and groups) ===
SELECT date_trunc('day', filled_at) AS day,
       count(*) FILTER (WHERE direction = 'BUY') AS buy_fills,
       count(DISTINCT group_id) FILTER (WHERE direction = 'BUY') AS buy_groups,
       count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
       max(filled_at) FILTER (WHERE direction = 'BUY') AS last_buy,
       max(filled_at) FILTER (WHERE direction = 'SELL') AS last_sell
  FROM paper_fills WHERE filled_at > now() - interval '8 days'
 GROUP BY 1 ORDER BY 1;
\echo === C4c. how long a PAPER position stays open (groups bought in the last 4 days, first BUY to last SELL) ===
WITH g AS (
  SELECT account_id, group_id, us_market_slug,
         min(filled_at) FILTER (WHERE direction = 'BUY') AS first_buy,
         max(filled_at) FILTER (WHERE direction = 'SELL') AS last_sell,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
    FROM paper_fills GROUP BY 1, 2, 3
  HAVING min(filled_at) FILTER (WHERE direction = 'BUY') > now() - interval '4 days')
SELECT count(*) AS positions, count(*) FILTER (WHERE bought - sold > 1e-9) AS not_fully_sold,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM last_sell - first_buy) / 60)
             FILTER (WHERE bought - sold <= 1e-9 AND last_sell IS NOT NULL)::numeric, 1) AS open_minutes_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM last_sell - first_buy) / 60)
             FILTER (WHERE bought - sold <= 1e-9 AND last_sell IS NOT NULL)::numeric, 1) AS open_minutes_p90,
       round(max(extract(epoch FROM last_sell - first_buy) / 60)
             FILTER (WHERE bought - sold <= 1e-9 AND last_sell IS NOT NULL)::numeric, 1) AS open_minutes_max
  FROM g;
