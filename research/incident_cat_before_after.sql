-- P0 INCIDENT, stream inc-catalogue: BEFORE / AFTER estimate of what the
-- catalogue repair adds. Read-only, bounded (every statement windowed to the
-- prune horizon of 26 h or grouped; row dumps carry a LIMIT).
--
-- B1  the window edge: events whose LAST sighting sits in the final sweep
--     before game_start + 12 h (the sweep's startTimeMin = now - 12 h) and
--     that were never re-seen -- aged out by the window, not closed by the
--     venue (a closed market stops being re-seen at its close, earlier).
--     These are the multi-day / series / tournament listings the repaired
--     STARTED_EARLIER pass keeps.
-- B2  the board's LIMIT 30: real soccer / football tokens beyond row 30, and
--     whether BETTOR maps them (VENUE_TOKEN_TO_PROVIDER_KEY /
--     VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY as of 96fd349).
-- B3  the titles cut: tokens whose fixtures exceeded the 12 / 120 titles
--     carried to the fixture confirmation.
-- B4  the forward edge: rows starting in the last 6 h of the +96 h window,
--     by sport (a board that reaches the edge continues past it).
-- B5  what BETTOR valued in 24 h, per venue league (the denominator).
\echo '== B0 · read instant =='
SELECT now() AS read_at;

\echo '== B1 · events aged out at the -12 h window edge (not re-seen since), last 26 h, by sport x family =='
WITH ev AS (
    SELECT event_slug,
           max(CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
                    ELSE coalesce(nullif(split_part(coalesce(sports_type, ''), '_', 1), ''), '(none)') END) AS sport,
           bool_or(sports_type = 'futures') AS has_futures,
           bool_or(sports_type ~ 'winner$') AS has_winner,
           min(game_start) AS start_at, max(updated_at) AS last_seen,
           count(DISTINCT market_slug) AS contracts
      FROM us_premap
     WHERE updated_at >= now() - interval '26 hours'
       AND game_start IS NOT NULL
     GROUP BY 1)
SELECT sport,
       count(*) FILTER (WHERE last_seen >= start_at + interval '11 hours 15 minutes'
                          AND last_seen <  start_at + interval '12 hours 5 minutes'
                          AND last_seen <  now() - interval '40 minutes') AS aged_out_at_window_edge,
       sum(contracts) FILTER (WHERE last_seen >= start_at + interval '11 hours 15 minutes'
                                AND last_seen <  start_at + interval '12 hours 5 minutes'
                                AND last_seen <  now() - interval '40 minutes') AS contracts_aged_out,
       count(*) FILTER (WHERE has_futures
                          AND last_seen >= start_at + interval '11 hours 15 minutes'
                          AND last_seen <  start_at + interval '12 hours 5 minutes'
                          AND last_seen <  now() - interval '40 minutes') AS futures_events_aged_out,
       count(*) FILTER (WHERE last_seen < start_at + interval '11 hours 15 minutes'
                          AND last_seen < now() - interval '40 minutes') AS left_before_the_edge,
       count(*) AS events_26h
  FROM ev GROUP BY 1 ORDER BY 2 DESC NULLS LAST, 6 DESC LIMIT 30;

\echo '== B1b · sample of the edge-aged events (titles), last 26 h =='
WITH ev AS (
    SELECT event_slug, max(left(event_title, 70)) AS title,
           max(sports_type) AS a_type, min(game_start) AS start_at,
           max(updated_at) AS last_seen, count(DISTINCT market_slug) AS contracts
      FROM us_premap
     WHERE updated_at >= now() - interval '26 hours' AND game_start IS NOT NULL
     GROUP BY 1)
SELECT event_slug, title, a_type, start_at, last_seen, contracts
  FROM ev
 WHERE last_seen >= start_at + interval '11 hours 15 minutes'
   AND last_seen <  start_at + interval '12 hours 5 minutes'
   AND last_seen <  now() - interval '40 minutes'
 ORDER BY contracts DESC LIMIT 25;

\echo '== B2 · board tokens beyond the old LIMIT 30, and whether BETTOR maps them =='
WITH mapped(token, provider_key) AS (VALUES
        ('unl', 'soccer_uefa_nations_league'), ('mls', 'soccer_usa_mls'),
        ('lmx', 'soccer_mexico_ligamx'), ('uwcl', 'soccer_uefa_champs_league_women'),
        ('cnl', 'soccer_concacaf_nations_league'), ('uslc', 'soccer_usa_usl_championship'),
        ('arg2', 'soccer_argentina_primera_nacional'), ('brb', 'soccer_brazil_serie_b'),
        ('lco', 'soccer_colombia_primera_a'), ('uru1', 'soccer_uruguay_primera_division'),
        ('nwsl', 'soccer_usa_nwsl'), ('cfb', 'americanfootball_ncaaf'),
        ('nfl', 'americanfootball_nfl')),
t AS (
    SELECT split_part(sports_type, '_', 1) AS fam,
           split_part(market_slug, '-', 2) AS token,
           count(DISTINCT event_slug) AS events,
           count(DISTINCT left(event_title, 80)) AS titles
      FROM us_premap
     WHERE (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%')
       AND game_start > now() - interval '6 hours'
       AND lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) !~
           '(ebattles|e-battles|esoccer|e-soccer|ebasketball|efootball|e-football|simulated|cyber|virtual|e-cricket|ehockey|e-hockey|etennis|e-tennis)'
     GROUP BY 1, 2),
r AS (SELECT t.*, row_number() OVER (PARTITION BY fam ORDER BY events DESC, token) AS board_row
        FROM t)
SELECT r.fam, count(*) AS tokens,
       count(*) FILTER (WHERE board_row > 30) AS tokens_beyond_30,
       coalesce(sum(events) FILTER (WHERE board_row > 30), 0) AS events_beyond_30,
       count(*) FILTER (WHERE board_row > 30 AND m.token IS NOT NULL) AS mapped_tokens_beyond_30,
       coalesce(sum(events) FILTER (WHERE board_row > 30 AND m.token IS NOT NULL), 0) AS mapped_events_beyond_30,
       string_agg(r.token, ',') FILTER (WHERE board_row > 30 AND m.token IS NOT NULL) AS mapped_tokens_cut
  FROM r LEFT JOIN mapped m ON m.token = r.token
 GROUP BY 1 ORDER BY 1;

\echo '== B3 · fixture titles the confirmation never saw (12 soccer / 120 football carried) =='
WITH t AS (
    SELECT split_part(sports_type, '_', 1) AS fam,
           split_part(market_slug, '-', 2) AS token,
           count(DISTINCT left(event_title, 80)) AS titles
      FROM us_premap
     WHERE (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%')
       AND game_start > now() - interval '6 hours'
     GROUP BY 1, 2)
SELECT fam, token, titles,
       greatest(0, titles - CASE WHEN fam = 'soccer' THEN 12 ELSE 120 END) AS titles_never_carried
  FROM t
 WHERE titles > CASE WHEN fam = 'soccer' THEN 12 ELSE 120 END
 ORDER BY 4 DESC LIMIT 30;

\echo '== B4 · the forward edge: rows and events starting in [now+90h, now+96h], by sport =='
SELECT CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
            ELSE coalesce(nullif(split_part(coalesce(sports_type, ''), '_', 1), ''), '(none)') END AS sport,
       count(*) AS rows_n, count(DISTINCT event_slug) AS events,
       max(game_start) AS latest_start
  FROM us_premap
 WHERE updated_at >= now() - interval '90 minutes'
   AND game_start >= now() + interval '90 hours'
 GROUP BY 1 ORDER BY 3 DESC LIMIT 30;

\echo '== B5 · what BETTOR valued in 24 h, per venue league (distinct venue contracts and provider events) =='
SELECT lower(split_part(coalesce(us_market_slug, ''), '-', 2)) AS league,
       count(DISTINCT us_market_slug) AS contracts,
       count(DISTINCT event_key) AS provider_events
  FROM external_valuations
 WHERE decided_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;
