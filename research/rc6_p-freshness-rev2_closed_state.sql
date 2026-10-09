-- RC6.2 lane p-freshness, independent review rev2, read only.
-- The lane holds a member out of the plane's book-read plan, with no time
-- bound, once its newest REST book read states any of
-- freshness_window.TERMINAL_STATES -- which include INSTRUMENT_STATE_CLOSED
-- (the venue enum's 0 value) and MARKET_STATE_CLOSED. Is CLOSED a state the
-- venue later leaves for OPEN (not terminal), or only an ended market?
--   C1  priority members now (active, priority <= 10): refdata state x phase
--   C2  every active POLYMARKET_US registry contract: refdata state x phase
--   C3  priority members now whose refdata state is not OPEN, pregame first
--   C4  paper book reads (error-free, 72 h): states, rows and slugs
--   C5  paper book reads (error-free, 72 h): state -> next different state
--       for the same slug (does any TERMINAL-set word precede an OPEN read?)
\echo === C1. priority members now: refdata state x phase ===
SELECT coalesce(refdata ->> 'state', '(none)') AS refdata_state,
       CASE WHEN event_start IS NULL THEN 'NO_START'
            WHEN event_start > now() THEN 'PREGAME'
            WHEN now() - event_start > interval '4 hours' THEN 'STARTED_GT_4H'
            ELSE 'IN_PLAY_OR_RECENT' END AS phase,
       count(*) AS members
  FROM market_plane_registry
 WHERE active AND priority <= 10
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === C2. every active POLYMARKET_US contract: refdata state x phase ===
SELECT coalesce(refdata ->> 'state', '(none)') AS refdata_state,
       CASE WHEN event_start IS NULL THEN 'NO_START'
            WHEN event_start > now() THEN 'PREGAME'
            WHEN now() - event_start > interval '4 hours' THEN 'STARTED_GT_4H'
            ELSE 'IN_PLAY_OR_RECENT' END AS phase,
       count(*) AS contracts,
       min(event_start) AS min_event_start, max(event_start) AS max_event_start
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === C3. priority members now whose refdata state is not OPEN ===
SELECT contract_id, priority, required_reason, event_start,
       refdata ->> 'state' AS refdata_state, refdata_at
  FROM market_plane_registry
 WHERE active AND priority <= 10
   AND coalesce(refdata ->> 'state', '') <> 'INSTRUMENT_STATE_OPEN'
 ORDER BY (event_start > now()) DESC, event_start
 LIMIT 40;
\echo === C4. paper book reads, error-free, 72 h: states ===
SELECT coalesce(upper(market_state), '(none)') AS market_state,
       count(*) AS reads, count(DISTINCT us_market_slug) AS slugs,
       min(observed_at) AS first_at, max(observed_at) AS last_at
  FROM paper_book_observations
 WHERE observed_at > now() - interval '72 hours' AND error IS NULL
 GROUP BY 1 ORDER BY 2 DESC;
\echo === C5. paper book reads, error-free, 72 h: state -> next different state per slug ===
WITH o AS (
  SELECT us_market_slug AS s, observed_at, upper(market_state) AS st
    FROM paper_book_observations
   WHERE observed_at > now() - interval '72 hours' AND error IS NULL
     AND market_state IS NOT NULL),
t AS (
  SELECT s, st, observed_at,
         lead(st) OVER (PARTITION BY s ORDER BY observed_at) AS nst,
         lead(observed_at) OVER (PARTITION BY s ORDER BY observed_at) AS nat
    FROM o)
SELECT st AS from_state, nst AS to_state, count(*) AS transitions,
       count(DISTINCT s) AS slugs, min(nat) AS first_at, max(nat) AS last_at,
       (array_agg(s ORDER BY nat DESC))[1:5] AS example_slugs
  FROM t
 WHERE nst IS NOT NULL AND nst <> st
 GROUP BY 1, 2 ORDER BY 3 DESC;
\echo === C6. per hour since 03:00Z: candidates that were members at the hour start and left the live list inside it (aged out of the 6 h candidate set) ===
-- A member at the hour start = a slug evaluated in (start - 6 h, start]. It
-- leaves the live list at (its last evaluation up to the hour's end) + 6 h;
-- frozen-only seconds = the rest of the hour after that. The average count
-- is the number of frozen-only members the RC6.2 refresh also serves in that
-- hour (an upper bound on the book reads they take: only quiet ones read).
WITH e AS (
  SELECT us_market_slug AS s, date_trunc('minute', cycle_at) AS m
    FROM ext_candidate_outcomes
   WHERE cycle_at > timestamptz '2026-10-08 20:00:00+00'
     AND us_market_slug IS NOT NULL
   GROUP BY 1, 2),
h AS (
  SELECT generate_series(timestamptz '2026-10-09 03:00:00+00',
                         date_trunc('hour', now()) - interval '1 hour',
                         interval '1 hour') AS hs),
a AS (
  SELECT h.hs, e.s, max(e.m) AS last_eval
    FROM h JOIN e ON e.m > h.hs - interval '6 hours'
                 AND e.m <= h.hs + interval '1 hour'
   GROUP BY 1, 2
  HAVING bool_or(e.m <= h.hs)),
f AS (
  SELECT a.hs, a.s, a.last_eval + interval '6 hours' AS left_at,
         r.event_start,
         greatest(0, extract(epoch FROM (a.hs + interval '1 hour')
                     - greatest(a.hs, a.last_eval + interval '6 hours'))) AS fo_s
    FROM a LEFT JOIN market_plane_registry r ON r.contract_id = a.s)
SELECT hs AS hour, count(*) AS candidates_at_start,
       count(*) FILTER (WHERE fo_s > 0) AS left_inside_hour,
       count(*) FILTER (WHERE fo_s > 0 AND event_start > left_at) AS left_pregame,
       round((sum(fo_s) / 3600)::numeric, 1) AS avg_frozen_only,
       round((sum(fo_s) FILTER (WHERE event_start > left_at) / 3600)::numeric, 1) AS avg_frozen_only_pregame
  FROM f GROUP BY 1 ORDER BY 1;
