-- P0 INCIDENT, stream inc-catalogue, second pass: the venue's market-type
-- vocabulary (to check the family classifier against every type production
-- holds) and the futures population (to correct the before/after estimate the
-- first pass overstated). Read-only, bounded: every statement is windowed to
-- the 26 h prune horizon or the last 90 minutes, grouped, and row dumps carry
-- a LIMIT.
--
-- R1  every sportsMarketType the catalogue holds (26 h), with rows, markets
--     and events: the classifier's test vocabulary.
-- R2  every FUTURES event that aged out at the -12 h window edge in 26 h,
--     listed (the first pass counted 11 / 76 and called them all futures the
--     repair keeps; reviewers found weather markets and a finished
--     qualifying session among them).
-- R3  the futures population listed now (last 90 min): which leagues carry
--     the `futures` type at all, and how many contracts each.
\echo '== R0 · read instant =='
SELECT now() AS read_at;

\echo '== R1 · sportsMarketType vocabulary, last 26 h =='
SELECT coalesce(nullif(sports_type, ''), '(none)') AS sports_type,
       count(*) AS rows_n,
       count(DISTINCT market_slug) AS markets,
       count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE updated_at >= now() - interval '26 hours'
 GROUP BY 1
 ORDER BY 3 DESC, 1
 LIMIT 400;

\echo '== R2 · futures events aged out at the -12 h edge, last 26 h (all, listed) =='
WITH ev AS (
    SELECT event_slug, max(left(event_title, 70)) AS title,
           bool_or(sports_type = 'futures') AS has_futures,
           min(game_start) AS start_at, max(updated_at) AS last_seen,
           count(DISTINCT market_slug) AS contracts
      FROM us_premap
     WHERE updated_at >= now() - interval '26 hours' AND game_start IS NOT NULL
     GROUP BY 1)
SELECT split_part(event_slug, '-', 1) AS league, event_slug, title, contracts,
       start_at, last_seen,
       round(extract(epoch FROM (last_seen - start_at)) / 3600.0, 2) AS hours_listed_after_start
  FROM ev
 WHERE has_futures
   AND last_seen >= start_at + interval '11 hours 15 minutes'
   AND last_seen <  start_at + interval '12 hours 5 minutes'
   AND last_seen <  now() - interval '40 minutes'
 ORDER BY contracts DESC, event_slug
 LIMIT 40;

\echo '== R2b · the same edge, every family: events and contracts by league prefix (futures vs not) =='
WITH ev AS (
    SELECT event_slug,
           bool_or(sports_type = 'futures') AS has_futures,
           min(game_start) AS start_at, max(updated_at) AS last_seen,
           count(DISTINCT market_slug) AS contracts
      FROM us_premap
     WHERE updated_at >= now() - interval '26 hours' AND game_start IS NOT NULL
     GROUP BY 1)
SELECT split_part(event_slug, '-', 1) AS league, has_futures,
       count(*) AS events, sum(contracts) AS contracts
  FROM ev
 WHERE last_seen >= start_at + interval '11 hours 15 minutes'
   AND last_seen <  start_at + interval '12 hours 5 minutes'
   AND last_seen <  now() - interval '40 minutes'
 GROUP BY 1, 2
 ORDER BY 3 DESC, 1
 LIMIT 60;

\echo '== R3 · futures listed now (last 90 min), by league prefix =='
SELECT split_part(event_slug, '-', 1) AS league,
       count(DISTINCT event_slug) AS events,
       count(DISTINCT market_slug) AS contracts,
       min(game_start) AS earliest_start, max(game_start) AS latest_start,
       max(left(event_title, 60)) AS a_title
  FROM us_premap
 WHERE updated_at >= now() - interval '90 minutes'
   AND sports_type = 'futures'
 GROUP BY 1
 ORDER BY 3 DESC, 1
 LIMIT 60;
