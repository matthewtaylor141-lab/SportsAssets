-- RC6.2 lane p-freshness (data freshness), read only. Follows
-- rc6_p-freshness_projection.sql (P3: the coverage pass counts ANY paper
-- book row inside 300 s current -- error rows and not-open rows included).
--   Q1  the market_state values the paper runtime records on its
--       error-free book reads (last 24 h, the slugs evaluated as candidates
--       in the last 30 h): is a NULL / unknown state ever recorded?
--   Q2  the error texts on those slugs' reads (last 24 h)
--   Q3  at EVERY plane snapshot's coverage instant (last 24 h): the
--       candidate members (evaluated in the 6 h before the instant, the
--       populate CANDIDATE_SQL rule) the coverage pass's REST_BOOK_SQL
--       credits through a paper row inside 300 s, against what each fixed
--       rule credits: HELD_RULE = the newest error-free row inside 300 s
--       does not state a not-open market (bettor_paper_freshness /
--       freshness_window); OPEN_ONLY = it states an OPEN market. The
--       credits each rule removes, and the snapshot's own paper count.
\echo === Q1. paper book reads without error: market_state by source (last 24 h, candidate slugs) ===
WITH cand AS (
  SELECT DISTINCT us_market_slug AS slug FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '30 hours' AND us_market_slug IS NOT NULL)
SELECT coalesce(o.market_state, '<NULL>') AS market_state,
       split_part(o.source, ':', 1) AS source, count(*) AS rows,
       count(DISTINCT o.us_market_slug) AS slugs,
       min(o.observed_at) AS first_at, max(o.observed_at) AS last_at
  FROM paper_book_observations o
 WHERE o.us_market_slug IN (SELECT slug FROM cand)
   AND o.observed_at > now() - interval '24 hours' AND o.error IS NULL
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;
\echo === Q2. paper book reads with an error (last 24 h, candidate slugs) ===
WITH cand AS (
  SELECT DISTINCT us_market_slug AS slug FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '30 hours' AND us_market_slug IS NOT NULL)
SELECT left(o.error, 60) AS error, coalesce(o.market_state, '<NULL>') AS market_state,
       count(*) AS rows, count(DISTINCT o.us_market_slug) AS slugs
  FROM paper_book_observations o
 WHERE o.us_market_slug IN (SELECT slug FROM cand)
   AND o.observed_at > now() - interval '24 hours' AND o.error IS NOT NULL
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
\echo === Q3. per snapshot instant (last 24 h): paper credits under the current rule and the two fixed rules ===
WITH snaps AS MATERIALIZED (
  SELECT at AS snap_at,
         to_timestamp((payload #>> '{coverage,computed_at}')::float8) AS t,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         (payload #>> '{freshness,priority_universe,current_rest_fallback_by_origin,PAPER_BOOK_OBSERVATION}')::int AS paper_credit_snapshot
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours'
     AND payload #>> '{coverage,computed_at}' IS NOT NULL),
cand AS MATERIALIZED (
  SELECT DISTINCT us_market_slug AS slug FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '31 hours' AND us_market_slug IS NOT NULL),
obs AS MATERIALIZED (
  SELECT o.us_market_slug AS slug, o.observed_at, o.error, o.market_state
    FROM paper_book_observations o
   WHERE o.us_market_slug IN (SELECT slug FROM cand)
     AND o.observed_at > now() - interval '25 hours'),
w AS (
  SELECT s.snap_at, s.t, o.slug,
         (array_agg(o.market_state ORDER BY o.observed_at DESC)
            FILTER (WHERE o.error IS NULL))[1] AS newest_ok_state,
         count(*) FILTER (WHERE o.error IS NULL) AS ok_rows
    FROM snaps s
    JOIN obs o ON o.observed_at > s.t - interval '300 seconds' AND o.observed_at <= s.t
   GROUP BY 1, 2, 3),
wm AS (
  SELECT w.*,
         EXISTS (SELECT 1 FROM ext_candidate_outcomes c
                  WHERE c.us_market_slug = w.slug
                    AND c.cycle_at > w.t - interval '6 hours' AND c.cycle_at <= w.t) AS member,
         (w.ok_rows > 0 AND upper(coalesce(w.newest_ok_state, '')) NOT IN (
            'MARKET_STATE_EXPIRED','MARKET_STATE_CLOSED','MARKET_STATE_TERMINATED',
            'MARKET_STATE_MATCH_AND_CLOSE_AUCTION','MARKET_STATE_SETTLED','MARKET_STATE_RESOLVED',
            'EXPIRED','CLOSED','SETTLED','RESOLVED','INSTRUMENT_STATE_CLOSED',
            'INSTRUMENT_STATE_EXPIRED','INSTRUMENT_STATE_TERMINATED',
            'INSTRUMENT_STATE_MATCH_AND_CLOSE_AUCTION','MARKET_STATE_HALTED',
            'MARKET_STATE_SUSPENDED','MARKET_STATE_PREOPEN','MARKET_STATE_PAUSED','HALTED',
            'SUSPENDED','PREOPEN','PAUSED','INSTRUMENT_STATE_PREOPEN',
            'INSTRUMENT_STATE_SUSPENDED','INSTRUMENT_STATE_HALTED','INSTRUMENT_STATE_PENDING'))
           AS held_rule,
         (w.ok_rows > 0 AND w.newest_ok_state IN ('MARKET_STATE_OPEN', 'INSTRUMENT_STATE_OPEN'))
           AS open_only
    FROM w)
SELECT date_trunc('hour', s.t) AS hour, count(DISTINCT s.snap_at) AS snaps,
       round(avg(s.den), 1) AS den, round(avg(s.nc), 1) AS nc,
       round(avg(s.paper_credit_snapshot), 2) AS paper_credit_snapshot,
       round(count(*) FILTER (WHERE wm.member)::numeric / count(DISTINCT s.snap_at), 2) AS credit_now_per_snap,
       round(count(*) FILTER (WHERE wm.member AND wm.held_rule)::numeric / count(DISTINCT s.snap_at), 2) AS credit_held_rule_per_snap,
       round(count(*) FILTER (WHERE wm.member AND wm.open_only)::numeric / count(DISTINCT s.snap_at), 2) AS credit_open_only_per_snap,
       count(*) FILTER (WHERE wm.member AND NOT wm.held_rule AND wm.ok_rows = 0) AS removed_error_only,
       count(*) FILTER (WHERE wm.member AND NOT wm.held_rule AND wm.ok_rows > 0) AS removed_not_open,
       count(*) FILTER (WHERE wm.member AND wm.held_rule AND NOT wm.open_only) AS removed_only_by_open_only
  FROM snaps s LEFT JOIN wm ON wm.snap_at = s.snap_at
 GROUP BY 1 ORDER BY 1;
\echo === Q3b. the same over every snapshot of the last 24 h ===
WITH snaps AS MATERIALIZED (
  SELECT at AS snap_at,
         to_timestamp((payload #>> '{coverage,computed_at}')::float8) AS t
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours'
     AND payload #>> '{coverage,computed_at}' IS NOT NULL),
cand AS MATERIALIZED (
  SELECT DISTINCT us_market_slug AS slug FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '31 hours' AND us_market_slug IS NOT NULL),
obs AS MATERIALIZED (
  SELECT o.us_market_slug AS slug, o.observed_at, o.error, o.market_state
    FROM paper_book_observations o
   WHERE o.us_market_slug IN (SELECT slug FROM cand)
     AND o.observed_at > now() - interval '25 hours'),
w AS (
  SELECT s.snap_at, s.t, o.slug,
         (array_agg(o.market_state ORDER BY o.observed_at DESC)
            FILTER (WHERE o.error IS NULL))[1] AS newest_ok_state,
         count(*) FILTER (WHERE o.error IS NULL) AS ok_rows
    FROM snaps s
    JOIN obs o ON o.observed_at > s.t - interval '300 seconds' AND o.observed_at <= s.t
   GROUP BY 1, 2, 3)
SELECT coalesce(w.newest_ok_state, CASE WHEN w.ok_rows = 0 THEN '<ERROR_ROWS_ONLY>' ELSE '<NULL>' END) AS newest_ok_state,
       count(*) AS member_snapshot_credits, count(DISTINCT w.slug) AS slugs,
       count(DISTINCT w.snap_at) AS snapshots
  FROM w
 WHERE EXISTS (SELECT 1 FROM ext_candidate_outcomes c
                WHERE c.us_market_slug = w.slug
                  AND c.cycle_at > w.t - interval '6 hours' AND c.cycle_at <= w.t)
 GROUP BY 1 ORDER BY 2 DESC;
