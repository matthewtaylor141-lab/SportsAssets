-- WHICH SOCCER COMPETITIONS THE US VENUE ACTUALLY LISTS. Read-only, bounded.
--
-- WHY THIS EXISTS: I CAUGHT MY OWN MEASUREMENT BEING WRONG BEFORE SHIPPING IT.
-- Run 258 (`epl_resolution_trace.sql`) put our open-market leading token beside
-- `us_premap` rows matching `'%-' || token || '-%'`, and I read the result as
-- competition coverage:
--
--     sea   1812      col   1256      por   726      tur   570
--
-- Those numbers do not mean what I took them to mean. `sea` also matches
-- Seattle, `col` Colorado, `por` Portland, in slugs like
-- `aec-mlb-sea-tex-2026-09-27`. A TEAM abbreviation and a LEAGUE token live in
-- the same slug and that pattern cannot tell them apart, so the figure I was
-- about to order a credit budget by may be mostly baseball.
--
-- The venue publishes its own competition label. This asks for that instead:
-- `team_league`, and the slug's SECOND segment, which is where the league token
-- sits (`atc-afcq-bdi-dza-...`, `asc-lmx-leo-ju-...`) rather than anywhere it
-- happens to appear.
--
-- SIMULATED COMPETITIONS ARE COUNTED SEPARATELY, NOT DROPPED. The venue lists
-- 538 `efootball_team_full_time_winner` events whose titles carry real club
-- names ("eBattles: Arsenal vs. Chelsea"). Those are not soccer coverage, and
-- silently excluding them would hide the reason the EPL looked mappable.

\echo == 1 · REAL SOCCER BY THE VENUE OWN LEAGUE LABEL AND SLUG POSITION ==
SELECT coalesce(team_league, 'null')             AS venue_league_label,
       split_part(market_slug, '-', 2)            AS slug_league_token,
       count(*)                                  AS premap_rows,
       count(DISTINCT event_slug)                 AS events,
       min(left(event_title, 46))                 AS example_event,
       min(game_start)::timestamptz(0)            AS earliest_kickoff
  FROM us_premap
 WHERE sports_type LIKE 'soccer%'
   AND lower(coalesce(event_title, '') || ' ' || coalesce(question, ''))
       NOT LIKE '%ebattles%'
   AND lower(coalesce(event_title, '')) NOT LIKE '%esoccer%'
 GROUP BY 1, 2
 ORDER BY 4 DESC, 3 DESC
 LIMIT 40;

\echo == 2 · THE SIMULATED ONES, COUNTED APART SO THEY ARE NOT MISTAKEN ==
SELECT coalesce(sports_type, 'null')  AS sports_type,
       split_part(market_slug, '-', 2) AS slug_league_token,
       count(*)                       AS premap_rows,
       count(DISTINCT event_slug)      AS events,
       min(left(event_title, 46))      AS example_event
  FROM us_premap
 WHERE sports_type LIKE 'efootball%'
    OR sports_type LIKE 'esports%'
    OR lower(coalesce(event_title, '')) LIKE '%ebattles%'
    OR lower(coalesce(event_title, '')) LIKE '%esoccer%'
 GROUP BY 1, 2
 ORDER BY 4 DESC
 LIMIT 20;

\echo == 3 · HOW BADLY THE TOKEN MATCH MISLED ME, PER TOKEN ==
SELECT t.token,
       (SELECT count(*) FROM us_premap q
         WHERE q.market_slug LIKE '%-' || t.token || '-%')
         AS rows_anywhere_in_slug,
       (SELECT count(*) FROM us_premap q
         WHERE split_part(q.market_slug, '-', 2) = t.token)
         AS rows_in_the_league_position,
       (SELECT count(*) FROM us_premap q
         WHERE q.market_slug LIKE '%-' || t.token || '-%'
           AND q.sports_type LIKE 'soccer%')
         AS rows_anywhere_and_soccer,
       (SELECT min(q.market_slug) FROM us_premap q
         WHERE q.market_slug LIKE '%-' || t.token || '-%')
         AS example_of_what_it_matched
  FROM (VALUES ('sea'), ('col'), ('por'), ('tur'), ('mls'),
               ('bra'), ('arg'), ('kor'), ('lmx'), ('epl')) AS t(token)
 ORDER BY 2 DESC;

\echo == 4 · AND WHICH OF OUR OPEN SOCCER MARKETS SHARE A LEAGUE TOKEN ==
SELECT m.ours                                    AS our_league_token,
       count(*)                                  AS our_open_markets,
       (SELECT count(DISTINCT q.event_slug) FROM us_premap q
         WHERE split_part(q.market_slug, '-', 2) = m.ours
           AND q.sports_type LIKE 'soccer%')
         AS venue_real_soccer_events_same_token,
       min(left(m.event_title, 46))               AS our_example
  FROM (SELECT slug, event_title,
               split_part(slug, '-', 1) AS ours
          FROM markets
         WHERE coalesce(closed, false) = false
           AND coalesce(resolved, false) = false
           AND sport ILIKE '%occer%') m
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 25;
