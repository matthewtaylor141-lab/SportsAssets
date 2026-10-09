-- READ-ONLY. RC6 LANE G2 (LIVE GAME STATE FIXTURE ADAPTER): THE CANDIDATE
-- FIXTURE CENSUS, BEFORE AND AFTER. The held census
-- (lgs_held_fixture_census.sql) judges only paper_acct_main's open markets,
-- one today (an Indonesian Liga 1 market, not a score league). This file
-- runs the SAME judging chains over every venue event whose game_start is in
-- [now - 6 h, now + 48 h] -- one market per event, the event's first market
-- row whose identifier is its slug -- with no account and no truncation:
--   1 the base adapter's chain (412c4962 census statement 2), by verdict
--   2 the fixed adapter's chain (lane G2 census statement 2), by verdict
--   3 the fixed adapter's chain by league x verdict (census statement 3)
--   4 the fixed adapter: the established identities, a sample per league
-- Generated from the two census files by substituting only the population
-- CTE; every judging line is the census's own. SELECT only; nothing here
-- parses a title, slug or question.

\echo == 1. BASE adapter (412c4962) over candidate events, by verdict ==
SELECT 'BASE_412c4962' AS adapter, collector_verdict, count(*) AS candidate_events
  FROM (
WITH held AS (
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
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
), acct AS (
    -- one market per candidate event: the catalogue's own market row whose
    -- identifier is its market slug, the first such slug of the event
    SELECT DISTINCT ON (u.event_slug) u.market_slug AS us_market_slug,
           1.0::float8 AS open_qty
      FROM us_premap u
     WHERE u.event_slug IS NOT NULL AND u.identifier = u.market_slug
       AND u.game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
     ORDER BY u.event_slug, u.market_slug
), markets AS (
    SELECT us_market_slug, count(*) AS positions, sum(open_qty) AS open_qty,
           row_number() OVER (ORDER BY us_market_slug) AS rn
      FROM acct GROUP BY us_market_slug
), joined AS (
    SELECT m.*, to_jsonb(pm) AS premap, to_jsonb(fm) AS fixture_metadata
      FROM markets m
      LEFT JOIN LATERAL (
        SELECT u.* FROM us_premap u
         WHERE u.identifier = m.us_market_slug AND u.market_slug = m.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
      LEFT JOIN LATERAL (
        SELECT v.* FROM venue_fixture_metadata v
         WHERE v.venue IN ('PMUS', 'POLYMARKET_US')
           AND v.venue_fixture_key = 'event:' || pm.event_slug
         ORDER BY v.retrieved_at DESC LIMIT 1) fm ON true
), registry (league, espn_path, odds_sport_key) AS (VALUES
    ('NFL', 'football/nfl', 'americanfootball_nfl'),
    ('NCAAF', 'football/college-football', 'americanfootball_ncaaf'),
    ('MLB', 'baseball/mlb', 'baseball_mlb'),
    ('NBA', 'basketball/nba', 'basketball_nba'),
    ('WNBA', 'basketball/wnba', 'basketball_wnba'),
    ('NCAAB', 'basketball/mens-college-basketball', 'basketball_ncaab'),
    ('NCAAW', 'basketball/womens-college-basketball', 'basketball_wncaab'),
    ('NHL', 'hockey/nhl', 'icehockey_nhl'),
    ('EPL', 'soccer/eng.1', 'soccer_epl'),
    ('MLS', 'soccer/usa.1', 'soccer_usa_mls'),
    ('UCL', 'soccer/uefa.champions', 'soccer_uefa_champs_league')
), aliases (name, league) AS (VALUES
    ('CFB', 'NCAAF'), ('NCAA FOOTBALL', 'NCAAF'),
    ('NCAA MEN''S BASKETBALL', 'NCAAB'), ('NCAA WOMEN''S BASKETBALL', 'NCAAW'),
    ('MAJOR LEAGUE BASEBALL', 'MLB'), ('NATIONAL FOOTBALL LEAGUE', 'NFL'),
    ('NATIONAL BASKETBALL ASSOCIATION', 'NBA'), ('NATIONAL HOCKEY LEAGUE', 'NHL'),
    ('WOMEN''S NATIONAL BASKETBALL ASSOCIATION', 'WNBA'),
    ('ENGLISH PREMIER LEAGUE', 'EPL'), ('MAJOR LEAGUE SOCCER', 'MLS'),
    ('UEFA CHAMPIONS LEAGUE', 'UCL')
), raw_fields AS (
    SELECT j.*,
           left(btrim(coalesce(j.premap ->> 'event_slug', '')), 250) AS event_id,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'home_team', ''),
                          j.premap ->> 'home_team', '')) AS home,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'away_team', ''),
                          j.premap ->> 'away_team', '')) AS away,
           upper(btrim(coalesce(j.fixture_metadata ->> 'competition', ''))) AS lg1,
           upper(btrim(coalesce(j.premap ->> 'league', ''))) AS lg2,
           upper(btrim(coalesce(j.premap ->> 'sport', ''))) AS lg3,
           coalesce(nullif(j.premap ->> 'game_start', ''),
                    nullif(j.fixture_metadata ->> 'scheduled_kickoff', ''))::timestamptz AS start_at,
           j.fixture_metadata -> 'raw' -> 'game_number' AS game_number_json
      FROM joined j
), fields AS (
    SELECT r.*,
           coalesce(r1.league, r2.league, r3.league) AS league,
           extract(epoch FROM r.start_at) AS start_epoch,
           btrim(regexp_replace(regexp_replace(lower(r.home), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS nhome,
           btrim(regexp_replace(regexp_replace(lower(r.away), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS naway,
           CASE WHEN r.game_number_json IS NULL OR jsonb_typeof(r.game_number_json) = 'null' THEN 'ABSENT'
                WHEN jsonb_typeof(r.game_number_json) = 'boolean' THEN 'INVALID'
                WHEN jsonb_typeof(r.game_number_json) <> 'number' THEN 'ABSENT'
                WHEN r.game_number_json::text !~ '^-?[0-9]+$' THEN 'ABSENT'
                WHEN (r.game_number_json::text)::numeric BETWEEN 1 AND 9 THEN 'VALID'
                ELSE 'INVALID' END AS game_number_state
      FROM raw_fields r
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg1), r.lg1)) r1 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg2), r.lg2)) r2 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg3), r.lg3)) r3 ON true
), judged AS (
    SELECT f.*,
           CASE
             WHEN f.rn > 1000000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
             WHEN f.event_id = '' THEN 'CANONICAL_VENUE_EVENT_MISSING'
             WHEN f.home = '' OR f.away = '' OR f.league IS NULL OR f.start_at IS NULL
               THEN 'CANONICAL_SCORE_FIXTURE_FIELDS_MISSING'
             WHEN f.fixture_metadata IS NOT NULL
                  AND (f.fixture_metadata ->> 'venue_fixture_key') IS DISTINCT FROM 'event:' || f.event_id
               THEN 'CANONICAL_FIXTURE_JOIN_MISMATCH'
             WHEN coalesce(f.fixture_metadata ->> 'orientation', '') <> ''
                  AND f.fixture_metadata ->> 'orientation' NOT IN ('HOME_AWAY', 'HOME_VS_AWAY', 'home_away')
               THEN 'CANONICAL_HOME_AWAY_UNPROVEN'
             WHEN f.nhome = '' OR f.naway = '' OR f.nhome = f.naway THEN 'CANONICAL_PARTICIPANTS_INVALID'
             WHEN f.start_epoch < 0 OR f.start_epoch > 4102444800 THEN 'CANONICAL_FIXTURE_EVIDENCE_MISSING'
             WHEN f.game_number_state = 'INVALID' THEN 'GAME_NUMBER_INVALID'
             ELSE 'ESTABLISHED'
           END AS row_verdict
      FROM fields f
), conflicts AS (
    SELECT event_id
      FROM judged WHERE row_verdict = 'ESTABLISHED'
     GROUP BY event_id
    HAVING count(DISTINCT (league, nhome, naway, start_epoch,
                           CASE WHEN game_number_state = 'VALID' THEN game_number_json::text END)) > 1
)
SELECT j.us_market_slug, j.positions, j.open_qty, j.event_id,
       CASE WHEN j.row_verdict = 'ESTABLISHED' AND j.event_id IN (SELECT event_id FROM conflicts)
            THEN 'CONFLICTING_CANONICAL_FIXTURE_ROWS' ELSE j.row_verdict END AS collector_verdict,
       j.league AS collector_league,
       CASE WHEN j.league IS NOT NULL THEN 'ESPN ' || g.espn_path || ' scoreboard dates='
                 || to_char(j.start_at AT TIME ZONE 'America/New_York', 'YYYYMMDD') END AS primary_source_request,
       j.home AS collector_home, j.away AS collector_away, j.start_at AS collector_start,
       j.premap IS NOT NULL AS premap_row,
       j.premap ->> 'sports_type' AS premap_sports_type,
       j.premap ->> 'listing_state' AS premap_listing_state,
       j.fixture_metadata IS NOT NULL AS fixture_metadata_row,
       j.fixture_metadata ->> 'source' AS fixture_source,
       j.fixture_metadata ->> 'competition' AS fixture_competition,
       j.fixture_metadata ->> 'orientation' AS fixture_orientation,
       round(extract(epoch FROM now() - (j.fixture_metadata ->> 'retrieved_at')::timestamptz)) AS fixture_retrieved_age_s,
       j.premap ->> 'team_league' AS diag_venue_team_league,
       (SELECT count(DISTINCT u.team_name) FROM us_premap u
         WHERE u.event_slug = j.premap ->> 'event_slug' AND u.team_name IS NOT NULL) AS diag_venue_team_names_on_event
  FROM judged j
  LEFT JOIN registry g ON g.league = j.league
 ORDER BY collector_verdict, j.league NULLS LAST, j.us_market_slug
) d
 GROUP BY 1, 2
 ORDER BY 3 DESC;

\echo == 2. FIXED adapter (lane G2) over candidate events, by verdict ==
SELECT 'LANE_G2' AS adapter, collector_verdict, count(*) AS candidate_events
  FROM (
WITH held AS (
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
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
), acct AS (
    -- one market per candidate event: the catalogue's own market row whose
    -- identifier is its market slug, the first such slug of the event
    SELECT DISTINCT ON (u.event_slug) u.market_slug AS us_market_slug,
           1.0::float8 AS open_qty
      FROM us_premap u
     WHERE u.event_slug IS NOT NULL AND u.identifier = u.market_slug
       AND u.game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
     ORDER BY u.event_slug, u.market_slug
), markets AS (
    SELECT us_market_slug, count(*) AS positions, sum(open_qty) AS open_qty,
           row_number() OVER (ORDER BY us_market_slug) AS rn
      FROM acct GROUP BY us_market_slug
), joined AS (
    SELECT m.*, to_jsonb(pm) AS premap, to_jsonb(fm) AS fixture_metadata
      FROM markets m
      LEFT JOIN LATERAL (
        SELECT u.* FROM us_premap u
         WHERE u.identifier = m.us_market_slug AND u.market_slug = m.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
      LEFT JOIN LATERAL (
        SELECT v.* FROM venue_fixture_metadata v
         WHERE v.venue IN ('PMUS', 'POLYMARKET_US')
           AND v.venue_fixture_key = 'event:' || pm.event_slug
         ORDER BY v.retrieved_at DESC LIMIT 1) fm ON true
), team_rows AS (
    -- the venue's own team object on every catalogue row of a held event
    -- (one pass over us_premap for all held events, as the adapter reads it)
    SELECT DISTINCT u.event_slug, u.team_id, u.team_name, u.team_safe_name, u.team_abbr,
           u.team_league, u.game_start, split_part(u.sports_type, '_', 1) AS sport_family
      FROM us_premap u
     WHERE u.team_id IS NOT NULL
       AND u.event_slug IN (SELECT j.premap ->> 'event_slug' FROM joined j)
), ranked AS (
    SELECT t.*, count(*) OVER (PARTITION BY t.event_slug) AS tuples,
           row_number() OVER (PARTITION BY t.event_slug
                              ORDER BY t.team_id, t.team_name, t.team_safe_name, t.team_abbr,
                                       t.team_league, t.game_start, t.sport_family) AS rk
      FROM team_rows t
), read_rows AS (
    SELECT * FROM ranked WHERE rk <= 16
), per_team AS (
    SELECT event_slug, team_id,
           count(DISTINCT (btrim(coalesce(team_name, '')), btrim(coalesce(team_safe_name, '')))) AS name_pairs,
           min(btrim(coalesce(team_name, ''))) AS tname,
           min(btrim(coalesce(team_safe_name, ''))) AS tsafe
      FROM read_rows GROUP BY event_slug, team_id
), per_team_n AS (
    SELECT p.*,
           btrim(regexp_replace(regexp_replace(lower(p.tname), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS n1,
           btrim(regexp_replace(regexp_replace(lower(p.tsafe), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS n2,
           row_number() OVER (PARTITION BY p.event_slug ORDER BY p.team_id) AS pno
      FROM per_team p
), venue_event AS (
    SELECT r.event_slug, max(r.tuples) AS tuples,
           count(DISTINCT r.team_id) AS ids,
           count(DISTINCT lower(btrim(coalesce(r.team_league, '')))) AS codes,
           bool_or(btrim(coalesce(r.team_league, '')) = '') AS code_blank,
           min(lower(btrim(r.team_league))) AS code,
           count(DISTINCT r.game_start) AS starts,
           bool_or(r.game_start IS NULL) AS start_null,
           min(r.game_start) AS team_start,
           string_agg(DISTINCT nullif(lower(btrim(coalesce(r.sport_family, ''))), ''), ','
                      ORDER BY nullif(lower(btrim(coalesce(r.sport_family, ''))), '')) AS families
      FROM read_rows r GROUP BY r.event_slug
), venue_pair AS (
    SELECT v.*,
           (SELECT bool_or(p.name_pairs <> 1 OR (p.n1 = '' AND p.n2 = ''))
              FROM per_team_n p WHERE p.event_slug = v.event_slug) AS name_bad,
           p1.team_id AS p1_id, p2.team_id AS p2_id,
           CASE WHEN p1.n1 <> '' THEN p1.tname ELSE p1.tsafe END AS p1_primary,
           CASE WHEN p2.n1 <> '' THEN p2.tname ELSE p2.tsafe END AS p2_primary,
           ARRAY(SELECT DISTINCT x FROM unnest(ARRAY[p1.n1, p1.n2]) x WHERE x <> '' ORDER BY x) AS p1_names,
           ARRAY(SELECT DISTINCT x FROM unnest(ARRAY[p2.n1, p2.n2]) x WHERE x <> '' ORDER BY x) AS p2_names
      FROM venue_event v
      LEFT JOIN per_team_n p1 ON p1.event_slug = v.event_slug AND p1.pno = 1
      LEFT JOIN per_team_n p2 ON p2.event_slug = v.event_slug AND p2.pno = 2
), registry (league, sport, espn_path, odds_sport_key) AS (VALUES
    ('NFL', 'football', 'football/nfl', 'americanfootball_nfl'),
    ('NCAAF', 'football', 'football/college-football', 'americanfootball_ncaaf'),
    ('MLB', 'baseball', 'baseball/mlb', 'baseball_mlb'),
    ('NBA', 'basketball', 'basketball/nba', 'basketball_nba'),
    ('WNBA', 'basketball', 'basketball/wnba', 'basketball_wnba'),
    ('NCAAB', 'basketball', 'basketball/mens-college-basketball', 'basketball_ncaab'),
    ('NCAAW', 'basketball', 'basketball/womens-college-basketball', 'basketball_wncaab'),
    ('NHL', 'hockey', 'hockey/nhl', 'icehockey_nhl'),
    ('EPL', 'soccer', 'soccer/eng.1', 'soccer_epl'),
    ('MLS', 'soccer', 'soccer/usa.1', 'soccer_usa_mls'),
    ('UCL', 'soccer', 'soccer/uefa.champions', 'soccer_uefa_champs_league')
), aliases (name, league) AS (VALUES
    ('CFB', 'NCAAF'), ('NCAA FOOTBALL', 'NCAAF'),
    ('NCAA MEN''S BASKETBALL', 'NCAAB'), ('NCAA WOMEN''S BASKETBALL', 'NCAAW'),
    ('MAJOR LEAGUE BASEBALL', 'MLB'), ('NATIONAL FOOTBALL LEAGUE', 'NFL'),
    ('NATIONAL BASKETBALL ASSOCIATION', 'NBA'), ('NATIONAL HOCKEY LEAGUE', 'NHL'),
    ('WOMEN''S NATIONAL BASKETBALL ASSOCIATION', 'WNBA'),
    ('ENGLISH PREMIER LEAGUE', 'EPL'), ('MAJOR LEAGUE SOCCER', 'MLS'),
    ('UEFA CHAMPIONS LEAGUE', 'UCL')
), venue_codes (code, league) AS (VALUES
    -- core.VENUE_LEAGUE_CODES['POLYMARKET_US']
    ('nfl', 'NFL'), ('cfb', 'NCAAF'), ('mlb', 'MLB'), ('nba', 'NBA'), ('wnba', 'WNBA'),
    ('nhl', 'NHL'), ('epl', 'EPL'), ('mls', 'MLS'), ('ucl', 'UCL')
), raw_fields AS (
    SELECT j.us_market_slug, j.positions, j.open_qty, j.rn, j.premap, j.fixture_metadata,
           left(btrim(coalesce(j.premap ->> 'event_slug', '')), 250) AS event_id,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'home_team', ''),
                          j.premap ->> 'home_team', '')) AS home_x,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'away_team', ''),
                          j.premap ->> 'away_team', '')) AS away_x,
           upper(btrim(coalesce(j.fixture_metadata ->> 'competition', ''))) AS lg1,
           upper(btrim(coalesce(j.premap ->> 'league', ''))) AS lg2,
           upper(btrim(coalesce(j.premap ->> 'sport', ''))) AS lg3,
           coalesce(nullif(j.premap ->> 'game_start', ''),
                    nullif(j.fixture_metadata ->> 'scheduled_kickoff', ''))::timestamptz AS held_start,
           j.fixture_metadata -> 'raw' -> 'game_number' AS game_number_json,
           vp.event_slug IS NOT NULL AS has_team_rows, vp.tuples, vp.ids, vp.codes,
           vp.code_blank, vp.code, vp.starts, vp.start_null, vp.team_start, vp.families,
           vp.name_bad, vp.p1_id, vp.p2_id, vp.p1_primary, vp.p2_primary,
           vp.p1_names, vp.p2_names
      FROM joined j
      LEFT JOIN venue_pair vp ON vp.event_slug = j.premap ->> 'event_slug'
), fields AS (
    SELECT r.*,
           coalesce(r1.league, r2.league, r3.league) AS explicit_league,
           vc.league AS venue_code_league,
           CASE WHEN r.home_x <> '' OR r.away_x <> '' OR NOT r.has_team_rows THEN 'NONE'
                WHEN r.tuples > 16 OR r.ids <> 2 THEN 'CANONICAL_VENUE_PARTICIPANTS_NOT_TWO'
                WHEN r.name_bad THEN 'CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN'
                WHEN r.codes <> 1 OR r.code_blank THEN 'CANONICAL_VENUE_LEAGUE_NOT_ONE'
                WHEN r.starts <> 1 OR r.start_null
                     OR (r.held_start IS NOT NULL AND r.team_start <> r.held_start)
                  THEN 'CANONICAL_EVENT_START_NOT_ONE_INSTANT'
                ELSE 'OK' END AS venue_state
      FROM raw_fields r
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg1), r.lg1)) r1 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg2), r.lg2)) r2 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg3), r.lg3)) r3 ON true
      LEFT JOIN venue_codes vc ON vc.code = r.code
), resolved AS (
    SELECT f.*,
           CASE WHEN f.venue_state = 'OK' THEN f.p1_primary ELSE f.home_x END AS home,
           CASE WHEN f.venue_state = 'OK' THEN f.p2_primary ELSE f.away_x END AS away,
           coalesce(f.explicit_league,
                    CASE WHEN f.venue_state = 'OK' THEN f.venue_code_league END) AS league,
           CASE WHEN f.venue_state = 'OK' THEN coalesce(f.held_start, f.team_start)
                ELSE f.held_start END AS start_at,
           CASE WHEN f.venue_state = 'OK' THEN 'PROVIDER_HOME_AWAY'
                ELSE 'VENUE_HOME_AWAY' END AS orientation,
           CASE WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL THEN 'VENUE_TEAM_LEAGUE_CODE'
                WHEN f.explicit_league IS NOT NULL THEN 'EXPLICIT_COMPETITION' END AS league_basis
      FROM fields f
), normed AS (
    SELECT s.*,
           extract(epoch FROM s.start_at) AS start_epoch,
           btrim(regexp_replace(regexp_replace(lower(s.home), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS nhome,
           btrim(regexp_replace(regexp_replace(lower(s.away), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS naway,
           (SELECT g.sport FROM registry g WHERE g.league = s.venue_code_league) AS venue_code_sport,
           CASE WHEN s.game_number_json IS NULL OR jsonb_typeof(s.game_number_json) = 'null' THEN 'ABSENT'
                WHEN jsonb_typeof(s.game_number_json) = 'boolean' THEN 'INVALID'
                WHEN jsonb_typeof(s.game_number_json) <> 'number' THEN 'ABSENT'
                WHEN s.game_number_json::text !~ '^-?[0-9]+$' THEN 'ABSENT'
                WHEN (s.game_number_json::text)::numeric BETWEEN 1 AND 9 THEN 'VALID'
                ELSE 'INVALID' END AS game_number_state
      FROM resolved s
), named AS (
    SELECT n.*,
           CASE WHEN n.venue_state = 'OK' THEN n.p1_names ELSE ARRAY[n.nhome] END AS home_names,
           CASE WHEN n.venue_state = 'OK' THEN n.p2_names ELSE ARRAY[n.naway] END AS away_names
      FROM normed n
), judged AS (
    SELECT f.*,
           CASE
             WHEN f.rn > 1000000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
             WHEN f.event_id = '' THEN 'CANONICAL_VENUE_EVENT_MISSING'
             WHEN f.venue_state NOT IN ('NONE', 'OK') THEN f.venue_state
             WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL
                  AND f.venue_code_league IS NULL THEN 'SCORE_LEAGUE_UNSUPPORTED'
             WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL
                  AND f.families IS DISTINCT FROM f.venue_code_sport
               THEN 'CANONICAL_VENUE_SPORT_FAMILY_MISMATCH'
             WHEN f.home = '' OR f.away = '' OR f.league IS NULL OR f.start_at IS NULL
               THEN 'CANONICAL_SCORE_FIXTURE_FIELDS_MISSING'
             WHEN f.fixture_metadata IS NOT NULL
                  AND (f.fixture_metadata ->> 'venue_fixture_key') IS DISTINCT FROM 'event:' || f.event_id
               THEN 'CANONICAL_FIXTURE_JOIN_MISMATCH'
             WHEN coalesce(f.fixture_metadata ->> 'orientation', '') <> ''
                  AND f.fixture_metadata ->> 'orientation' NOT IN ('HOME_AWAY', 'HOME_VS_AWAY', 'home_away')
               THEN 'CANONICAL_HOME_AWAY_UNPROVEN'
             WHEN f.nhome = '' OR f.naway = '' OR f.nhome = f.naway
                  OR f.home_names && f.away_names THEN 'CANONICAL_PARTICIPANTS_INVALID'
             WHEN f.start_epoch < 0 OR f.start_epoch > 4102444800 THEN 'CANONICAL_FIXTURE_EVIDENCE_MISSING'
             WHEN f.game_number_state = 'INVALID' THEN 'GAME_NUMBER_INVALID'
             ELSE 'ESTABLISHED'
           END AS row_verdict
      FROM named f
), conflicts AS (
    SELECT event_id
      FROM judged WHERE row_verdict = 'ESTABLISHED'
     GROUP BY event_id
    HAVING count(DISTINCT (league, orientation, home_names::text, away_names::text, start_epoch,
                           CASE WHEN game_number_state = 'VALID' THEN game_number_json::text END)) > 1
), final AS (
    SELECT j.*,
           CASE WHEN j.row_verdict = 'ESTABLISHED' AND j.event_id IN (SELECT event_id FROM conflicts)
                THEN 'CONFLICTING_CANONICAL_FIXTURE_ROWS' ELSE j.row_verdict END AS collector_verdict
      FROM judged j
)
SELECT f.us_market_slug, f.positions, f.open_qty, f.event_id, f.collector_verdict,
       f.league AS collector_league, f.league_basis AS collector_league_basis,
       f.orientation AS collector_orientation,
       CASE WHEN f.league IS NOT NULL THEN 'ESPN ' || g.espn_path || ' scoreboard dates='
                 || to_char(f.start_at AT TIME ZONE 'America/New_York', 'YYYYMMDD') END AS primary_source_request,
       f.home AS collector_home, f.away AS collector_away, f.start_at AS collector_start,
       f.home_names AS collector_home_names, f.away_names AS collector_away_names,
       f.venue_state, f.code AS venue_league_code, f.families AS venue_sport_families,
       f.p1_id AS venue_team_id_1, f.p2_id AS venue_team_id_2,
       f.premap IS NOT NULL AS premap_row,
       f.premap ->> 'sports_type' AS premap_sports_type,
       f.premap ->> 'listing_state' AS premap_listing_state,
       f.fixture_metadata IS NOT NULL AS fixture_metadata_row,
       f.fixture_metadata ->> 'source' AS fixture_source,
       f.fixture_metadata ->> 'competition' AS fixture_competition,
       f.fixture_metadata ->> 'orientation' AS fixture_orientation,
       round(extract(epoch FROM now() - (f.fixture_metadata ->> 'retrieved_at')::timestamptz)) AS fixture_retrieved_age_s,
       f.premap ->> 'team_league' AS diag_venue_team_league,
       (SELECT count(DISTINCT t.team_name) FROM team_rows t
         WHERE t.event_slug = f.event_id AND t.team_name IS NOT NULL) AS diag_venue_team_names_on_event
  FROM final f
  LEFT JOIN registry g ON g.league = f.league
 ORDER BY f.collector_verdict, f.league NULLS LAST, f.us_market_slug
) d
 GROUP BY 1, 2
 ORDER BY 3 DESC;

\echo == 3. FIXED adapter by league x verdict over candidate events ==
WITH held AS (
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
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
), acct AS (
    -- one market per candidate event: the catalogue's own market row whose
    -- identifier is its market slug, the first such slug of the event
    SELECT DISTINCT ON (u.event_slug) u.market_slug AS us_market_slug,
           1.0::float8 AS open_qty
      FROM us_premap u
     WHERE u.event_slug IS NOT NULL AND u.identifier = u.market_slug
       AND u.game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
     ORDER BY u.event_slug, u.market_slug
), markets AS (
    SELECT us_market_slug, count(*) AS positions, sum(open_qty) AS open_qty,
           row_number() OVER (ORDER BY us_market_slug) AS rn
      FROM acct GROUP BY us_market_slug
), joined AS (
    SELECT m.*, to_jsonb(pm) AS premap, to_jsonb(fm) AS fixture_metadata
      FROM markets m
      LEFT JOIN LATERAL (
        SELECT u.* FROM us_premap u
         WHERE u.identifier = m.us_market_slug AND u.market_slug = m.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
      LEFT JOIN LATERAL (
        SELECT v.* FROM venue_fixture_metadata v
         WHERE v.venue IN ('PMUS', 'POLYMARKET_US')
           AND v.venue_fixture_key = 'event:' || pm.event_slug
         ORDER BY v.retrieved_at DESC LIMIT 1) fm ON true
), team_rows AS (
    -- the venue's own team object on every catalogue row of a held event
    -- (one pass over us_premap for all held events, as the adapter reads it)
    SELECT DISTINCT u.event_slug, u.team_id, u.team_name, u.team_safe_name, u.team_abbr,
           u.team_league, u.game_start, split_part(u.sports_type, '_', 1) AS sport_family
      FROM us_premap u
     WHERE u.team_id IS NOT NULL
       AND u.event_slug IN (SELECT j.premap ->> 'event_slug' FROM joined j)
), ranked AS (
    SELECT t.*, count(*) OVER (PARTITION BY t.event_slug) AS tuples,
           row_number() OVER (PARTITION BY t.event_slug
                              ORDER BY t.team_id, t.team_name, t.team_safe_name, t.team_abbr,
                                       t.team_league, t.game_start, t.sport_family) AS rk
      FROM team_rows t
), read_rows AS (
    SELECT * FROM ranked WHERE rk <= 16
), per_team AS (
    SELECT event_slug, team_id,
           count(DISTINCT (btrim(coalesce(team_name, '')), btrim(coalesce(team_safe_name, '')))) AS name_pairs,
           min(btrim(coalesce(team_name, ''))) AS tname,
           min(btrim(coalesce(team_safe_name, ''))) AS tsafe
      FROM read_rows GROUP BY event_slug, team_id
), per_team_n AS (
    SELECT p.*,
           btrim(regexp_replace(regexp_replace(lower(p.tname), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS n1,
           btrim(regexp_replace(regexp_replace(lower(p.tsafe), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS n2,
           row_number() OVER (PARTITION BY p.event_slug ORDER BY p.team_id) AS pno
      FROM per_team p
), venue_event AS (
    SELECT r.event_slug, max(r.tuples) AS tuples,
           count(DISTINCT r.team_id) AS ids,
           count(DISTINCT lower(btrim(coalesce(r.team_league, '')))) AS codes,
           bool_or(btrim(coalesce(r.team_league, '')) = '') AS code_blank,
           min(lower(btrim(r.team_league))) AS code,
           count(DISTINCT r.game_start) AS starts,
           bool_or(r.game_start IS NULL) AS start_null,
           min(r.game_start) AS team_start,
           string_agg(DISTINCT nullif(lower(btrim(coalesce(r.sport_family, ''))), ''), ','
                      ORDER BY nullif(lower(btrim(coalesce(r.sport_family, ''))), '')) AS families
      FROM read_rows r GROUP BY r.event_slug
), venue_pair AS (
    SELECT v.*,
           (SELECT bool_or(p.name_pairs <> 1 OR (p.n1 = '' AND p.n2 = ''))
              FROM per_team_n p WHERE p.event_slug = v.event_slug) AS name_bad,
           p1.team_id AS p1_id, p2.team_id AS p2_id,
           CASE WHEN p1.n1 <> '' THEN p1.tname ELSE p1.tsafe END AS p1_primary,
           CASE WHEN p2.n1 <> '' THEN p2.tname ELSE p2.tsafe END AS p2_primary,
           ARRAY(SELECT DISTINCT x FROM unnest(ARRAY[p1.n1, p1.n2]) x WHERE x <> '' ORDER BY x) AS p1_names,
           ARRAY(SELECT DISTINCT x FROM unnest(ARRAY[p2.n1, p2.n2]) x WHERE x <> '' ORDER BY x) AS p2_names
      FROM venue_event v
      LEFT JOIN per_team_n p1 ON p1.event_slug = v.event_slug AND p1.pno = 1
      LEFT JOIN per_team_n p2 ON p2.event_slug = v.event_slug AND p2.pno = 2
), registry (league, sport, espn_path, odds_sport_key) AS (VALUES
    ('NFL', 'football', 'football/nfl', 'americanfootball_nfl'),
    ('NCAAF', 'football', 'football/college-football', 'americanfootball_ncaaf'),
    ('MLB', 'baseball', 'baseball/mlb', 'baseball_mlb'),
    ('NBA', 'basketball', 'basketball/nba', 'basketball_nba'),
    ('WNBA', 'basketball', 'basketball/wnba', 'basketball_wnba'),
    ('NCAAB', 'basketball', 'basketball/mens-college-basketball', 'basketball_ncaab'),
    ('NCAAW', 'basketball', 'basketball/womens-college-basketball', 'basketball_wncaab'),
    ('NHL', 'hockey', 'hockey/nhl', 'icehockey_nhl'),
    ('EPL', 'soccer', 'soccer/eng.1', 'soccer_epl'),
    ('MLS', 'soccer', 'soccer/usa.1', 'soccer_usa_mls'),
    ('UCL', 'soccer', 'soccer/uefa.champions', 'soccer_uefa_champs_league')
), aliases (name, league) AS (VALUES
    ('CFB', 'NCAAF'), ('NCAA FOOTBALL', 'NCAAF'),
    ('NCAA MEN''S BASKETBALL', 'NCAAB'), ('NCAA WOMEN''S BASKETBALL', 'NCAAW'),
    ('MAJOR LEAGUE BASEBALL', 'MLB'), ('NATIONAL FOOTBALL LEAGUE', 'NFL'),
    ('NATIONAL BASKETBALL ASSOCIATION', 'NBA'), ('NATIONAL HOCKEY LEAGUE', 'NHL'),
    ('WOMEN''S NATIONAL BASKETBALL ASSOCIATION', 'WNBA'),
    ('ENGLISH PREMIER LEAGUE', 'EPL'), ('MAJOR LEAGUE SOCCER', 'MLS'),
    ('UEFA CHAMPIONS LEAGUE', 'UCL')
), venue_codes (code, league) AS (VALUES
    -- core.VENUE_LEAGUE_CODES['POLYMARKET_US']
    ('nfl', 'NFL'), ('cfb', 'NCAAF'), ('mlb', 'MLB'), ('nba', 'NBA'), ('wnba', 'WNBA'),
    ('nhl', 'NHL'), ('epl', 'EPL'), ('mls', 'MLS'), ('ucl', 'UCL')
), raw_fields AS (
    SELECT j.us_market_slug, j.positions, j.open_qty, j.rn, j.premap, j.fixture_metadata,
           left(btrim(coalesce(j.premap ->> 'event_slug', '')), 250) AS event_id,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'home_team', ''),
                          j.premap ->> 'home_team', '')) AS home_x,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'away_team', ''),
                          j.premap ->> 'away_team', '')) AS away_x,
           upper(btrim(coalesce(j.fixture_metadata ->> 'competition', ''))) AS lg1,
           upper(btrim(coalesce(j.premap ->> 'league', ''))) AS lg2,
           upper(btrim(coalesce(j.premap ->> 'sport', ''))) AS lg3,
           coalesce(nullif(j.premap ->> 'game_start', ''),
                    nullif(j.fixture_metadata ->> 'scheduled_kickoff', ''))::timestamptz AS held_start,
           j.fixture_metadata -> 'raw' -> 'game_number' AS game_number_json,
           vp.event_slug IS NOT NULL AS has_team_rows, vp.tuples, vp.ids, vp.codes,
           vp.code_blank, vp.code, vp.starts, vp.start_null, vp.team_start, vp.families,
           vp.name_bad, vp.p1_id, vp.p2_id, vp.p1_primary, vp.p2_primary,
           vp.p1_names, vp.p2_names
      FROM joined j
      LEFT JOIN venue_pair vp ON vp.event_slug = j.premap ->> 'event_slug'
), fields AS (
    SELECT r.*,
           coalesce(r1.league, r2.league, r3.league) AS explicit_league,
           vc.league AS venue_code_league,
           CASE WHEN r.home_x <> '' OR r.away_x <> '' OR NOT r.has_team_rows THEN 'NONE'
                WHEN r.tuples > 16 OR r.ids <> 2 THEN 'CANONICAL_VENUE_PARTICIPANTS_NOT_TWO'
                WHEN r.name_bad THEN 'CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN'
                WHEN r.codes <> 1 OR r.code_blank THEN 'CANONICAL_VENUE_LEAGUE_NOT_ONE'
                WHEN r.starts <> 1 OR r.start_null
                     OR (r.held_start IS NOT NULL AND r.team_start <> r.held_start)
                  THEN 'CANONICAL_EVENT_START_NOT_ONE_INSTANT'
                ELSE 'OK' END AS venue_state
      FROM raw_fields r
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg1), r.lg1)) r1 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg2), r.lg2)) r2 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg3), r.lg3)) r3 ON true
      LEFT JOIN venue_codes vc ON vc.code = r.code
), resolved AS (
    SELECT f.*,
           CASE WHEN f.venue_state = 'OK' THEN f.p1_primary ELSE f.home_x END AS home,
           CASE WHEN f.venue_state = 'OK' THEN f.p2_primary ELSE f.away_x END AS away,
           coalesce(f.explicit_league,
                    CASE WHEN f.venue_state = 'OK' THEN f.venue_code_league END) AS league,
           CASE WHEN f.venue_state = 'OK' THEN coalesce(f.held_start, f.team_start)
                ELSE f.held_start END AS start_at,
           CASE WHEN f.venue_state = 'OK' THEN 'PROVIDER_HOME_AWAY'
                ELSE 'VENUE_HOME_AWAY' END AS orientation,
           CASE WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL THEN 'VENUE_TEAM_LEAGUE_CODE'
                WHEN f.explicit_league IS NOT NULL THEN 'EXPLICIT_COMPETITION' END AS league_basis
      FROM fields f
), normed AS (
    SELECT s.*,
           extract(epoch FROM s.start_at) AS start_epoch,
           btrim(regexp_replace(regexp_replace(lower(s.home), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS nhome,
           btrim(regexp_replace(regexp_replace(lower(s.away), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS naway,
           (SELECT g.sport FROM registry g WHERE g.league = s.venue_code_league) AS venue_code_sport,
           CASE WHEN s.game_number_json IS NULL OR jsonb_typeof(s.game_number_json) = 'null' THEN 'ABSENT'
                WHEN jsonb_typeof(s.game_number_json) = 'boolean' THEN 'INVALID'
                WHEN jsonb_typeof(s.game_number_json) <> 'number' THEN 'ABSENT'
                WHEN s.game_number_json::text !~ '^-?[0-9]+$' THEN 'ABSENT'
                WHEN (s.game_number_json::text)::numeric BETWEEN 1 AND 9 THEN 'VALID'
                ELSE 'INVALID' END AS game_number_state
      FROM resolved s
), named AS (
    SELECT n.*,
           CASE WHEN n.venue_state = 'OK' THEN n.p1_names ELSE ARRAY[n.nhome] END AS home_names,
           CASE WHEN n.venue_state = 'OK' THEN n.p2_names ELSE ARRAY[n.naway] END AS away_names
      FROM normed n
), judged AS (
    SELECT f.*,
           CASE
             WHEN f.rn > 1000000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
             WHEN f.event_id = '' THEN 'CANONICAL_VENUE_EVENT_MISSING'
             WHEN f.venue_state NOT IN ('NONE', 'OK') THEN f.venue_state
             WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL
                  AND f.venue_code_league IS NULL THEN 'SCORE_LEAGUE_UNSUPPORTED'
             WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL
                  AND f.families IS DISTINCT FROM f.venue_code_sport
               THEN 'CANONICAL_VENUE_SPORT_FAMILY_MISMATCH'
             WHEN f.home = '' OR f.away = '' OR f.league IS NULL OR f.start_at IS NULL
               THEN 'CANONICAL_SCORE_FIXTURE_FIELDS_MISSING'
             WHEN f.fixture_metadata IS NOT NULL
                  AND (f.fixture_metadata ->> 'venue_fixture_key') IS DISTINCT FROM 'event:' || f.event_id
               THEN 'CANONICAL_FIXTURE_JOIN_MISMATCH'
             WHEN coalesce(f.fixture_metadata ->> 'orientation', '') <> ''
                  AND f.fixture_metadata ->> 'orientation' NOT IN ('HOME_AWAY', 'HOME_VS_AWAY', 'home_away')
               THEN 'CANONICAL_HOME_AWAY_UNPROVEN'
             WHEN f.nhome = '' OR f.naway = '' OR f.nhome = f.naway
                  OR f.home_names && f.away_names THEN 'CANONICAL_PARTICIPANTS_INVALID'
             WHEN f.start_epoch < 0 OR f.start_epoch > 4102444800 THEN 'CANONICAL_FIXTURE_EVIDENCE_MISSING'
             WHEN f.game_number_state = 'INVALID' THEN 'GAME_NUMBER_INVALID'
             ELSE 'ESTABLISHED'
           END AS row_verdict
      FROM named f
), conflicts AS (
    SELECT event_id
      FROM judged WHERE row_verdict = 'ESTABLISHED'
     GROUP BY event_id
    HAVING count(DISTINCT (league, orientation, home_names::text, away_names::text, start_epoch,
                           CASE WHEN game_number_state = 'VALID' THEN game_number_json::text END)) > 1
), final AS (
    SELECT j.*,
           CASE WHEN j.row_verdict = 'ESTABLISHED' AND j.event_id IN (SELECT event_id FROM conflicts)
                THEN 'CONFLICTING_CANONICAL_FIXTURE_ROWS' ELSE j.row_verdict END AS collector_verdict
      FROM judged j
)
SELECT coalesce(league,
                CASE WHEN collector_verdict = 'SCORE_LEAGUE_UNSUPPORTED'
                     THEN 'UNSUPPORTED (venue team_league=' || coalesce(code, 'null') || ')'
                     ELSE 'UNRESOLVED (venue team_league=' || coalesce(premap ->> 'team_league', 'null') || ')'
                END) AS league,
       collector_verdict,
       count(*) AS markets,
       sum(positions) AS positions,
       count(DISTINCT nullif(event_id, '')) AS events
  FROM final
 GROUP BY 1, 2
 ORDER BY (collector_verdict = 'ESTABLISHED') DESC, markets DESC, 1;

\echo == 4. FIXED adapter: established identities, up to 6 per league ==
SELECT collector_league, event_id, collector_orientation, collector_home,
       collector_away, collector_home_names, collector_away_names, collector_start
  FROM (SELECT d.*, row_number() OVER (PARTITION BY collector_league
                                       ORDER BY collector_start, event_id) AS k
          FROM (
WITH held AS (
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
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
), acct AS (
    -- one market per candidate event: the catalogue's own market row whose
    -- identifier is its market slug, the first such slug of the event
    SELECT DISTINCT ON (u.event_slug) u.market_slug AS us_market_slug,
           1.0::float8 AS open_qty
      FROM us_premap u
     WHERE u.event_slug IS NOT NULL AND u.identifier = u.market_slug
       AND u.game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
     ORDER BY u.event_slug, u.market_slug
), markets AS (
    SELECT us_market_slug, count(*) AS positions, sum(open_qty) AS open_qty,
           row_number() OVER (ORDER BY us_market_slug) AS rn
      FROM acct GROUP BY us_market_slug
), joined AS (
    SELECT m.*, to_jsonb(pm) AS premap, to_jsonb(fm) AS fixture_metadata
      FROM markets m
      LEFT JOIN LATERAL (
        SELECT u.* FROM us_premap u
         WHERE u.identifier = m.us_market_slug AND u.market_slug = m.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
      LEFT JOIN LATERAL (
        SELECT v.* FROM venue_fixture_metadata v
         WHERE v.venue IN ('PMUS', 'POLYMARKET_US')
           AND v.venue_fixture_key = 'event:' || pm.event_slug
         ORDER BY v.retrieved_at DESC LIMIT 1) fm ON true
), team_rows AS (
    -- the venue's own team object on every catalogue row of a held event
    -- (one pass over us_premap for all held events, as the adapter reads it)
    SELECT DISTINCT u.event_slug, u.team_id, u.team_name, u.team_safe_name, u.team_abbr,
           u.team_league, u.game_start, split_part(u.sports_type, '_', 1) AS sport_family
      FROM us_premap u
     WHERE u.team_id IS NOT NULL
       AND u.event_slug IN (SELECT j.premap ->> 'event_slug' FROM joined j)
), ranked AS (
    SELECT t.*, count(*) OVER (PARTITION BY t.event_slug) AS tuples,
           row_number() OVER (PARTITION BY t.event_slug
                              ORDER BY t.team_id, t.team_name, t.team_safe_name, t.team_abbr,
                                       t.team_league, t.game_start, t.sport_family) AS rk
      FROM team_rows t
), read_rows AS (
    SELECT * FROM ranked WHERE rk <= 16
), per_team AS (
    SELECT event_slug, team_id,
           count(DISTINCT (btrim(coalesce(team_name, '')), btrim(coalesce(team_safe_name, '')))) AS name_pairs,
           min(btrim(coalesce(team_name, ''))) AS tname,
           min(btrim(coalesce(team_safe_name, ''))) AS tsafe
      FROM read_rows GROUP BY event_slug, team_id
), per_team_n AS (
    SELECT p.*,
           btrim(regexp_replace(regexp_replace(lower(p.tname), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS n1,
           btrim(regexp_replace(regexp_replace(lower(p.tsafe), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS n2,
           row_number() OVER (PARTITION BY p.event_slug ORDER BY p.team_id) AS pno
      FROM per_team p
), venue_event AS (
    SELECT r.event_slug, max(r.tuples) AS tuples,
           count(DISTINCT r.team_id) AS ids,
           count(DISTINCT lower(btrim(coalesce(r.team_league, '')))) AS codes,
           bool_or(btrim(coalesce(r.team_league, '')) = '') AS code_blank,
           min(lower(btrim(r.team_league))) AS code,
           count(DISTINCT r.game_start) AS starts,
           bool_or(r.game_start IS NULL) AS start_null,
           min(r.game_start) AS team_start,
           string_agg(DISTINCT nullif(lower(btrim(coalesce(r.sport_family, ''))), ''), ','
                      ORDER BY nullif(lower(btrim(coalesce(r.sport_family, ''))), '')) AS families
      FROM read_rows r GROUP BY r.event_slug
), venue_pair AS (
    SELECT v.*,
           (SELECT bool_or(p.name_pairs <> 1 OR (p.n1 = '' AND p.n2 = ''))
              FROM per_team_n p WHERE p.event_slug = v.event_slug) AS name_bad,
           p1.team_id AS p1_id, p2.team_id AS p2_id,
           CASE WHEN p1.n1 <> '' THEN p1.tname ELSE p1.tsafe END AS p1_primary,
           CASE WHEN p2.n1 <> '' THEN p2.tname ELSE p2.tsafe END AS p2_primary,
           ARRAY(SELECT DISTINCT x FROM unnest(ARRAY[p1.n1, p1.n2]) x WHERE x <> '' ORDER BY x) AS p1_names,
           ARRAY(SELECT DISTINCT x FROM unnest(ARRAY[p2.n1, p2.n2]) x WHERE x <> '' ORDER BY x) AS p2_names
      FROM venue_event v
      LEFT JOIN per_team_n p1 ON p1.event_slug = v.event_slug AND p1.pno = 1
      LEFT JOIN per_team_n p2 ON p2.event_slug = v.event_slug AND p2.pno = 2
), registry (league, sport, espn_path, odds_sport_key) AS (VALUES
    ('NFL', 'football', 'football/nfl', 'americanfootball_nfl'),
    ('NCAAF', 'football', 'football/college-football', 'americanfootball_ncaaf'),
    ('MLB', 'baseball', 'baseball/mlb', 'baseball_mlb'),
    ('NBA', 'basketball', 'basketball/nba', 'basketball_nba'),
    ('WNBA', 'basketball', 'basketball/wnba', 'basketball_wnba'),
    ('NCAAB', 'basketball', 'basketball/mens-college-basketball', 'basketball_ncaab'),
    ('NCAAW', 'basketball', 'basketball/womens-college-basketball', 'basketball_wncaab'),
    ('NHL', 'hockey', 'hockey/nhl', 'icehockey_nhl'),
    ('EPL', 'soccer', 'soccer/eng.1', 'soccer_epl'),
    ('MLS', 'soccer', 'soccer/usa.1', 'soccer_usa_mls'),
    ('UCL', 'soccer', 'soccer/uefa.champions', 'soccer_uefa_champs_league')
), aliases (name, league) AS (VALUES
    ('CFB', 'NCAAF'), ('NCAA FOOTBALL', 'NCAAF'),
    ('NCAA MEN''S BASKETBALL', 'NCAAB'), ('NCAA WOMEN''S BASKETBALL', 'NCAAW'),
    ('MAJOR LEAGUE BASEBALL', 'MLB'), ('NATIONAL FOOTBALL LEAGUE', 'NFL'),
    ('NATIONAL BASKETBALL ASSOCIATION', 'NBA'), ('NATIONAL HOCKEY LEAGUE', 'NHL'),
    ('WOMEN''S NATIONAL BASKETBALL ASSOCIATION', 'WNBA'),
    ('ENGLISH PREMIER LEAGUE', 'EPL'), ('MAJOR LEAGUE SOCCER', 'MLS'),
    ('UEFA CHAMPIONS LEAGUE', 'UCL')
), venue_codes (code, league) AS (VALUES
    -- core.VENUE_LEAGUE_CODES['POLYMARKET_US']
    ('nfl', 'NFL'), ('cfb', 'NCAAF'), ('mlb', 'MLB'), ('nba', 'NBA'), ('wnba', 'WNBA'),
    ('nhl', 'NHL'), ('epl', 'EPL'), ('mls', 'MLS'), ('ucl', 'UCL')
), raw_fields AS (
    SELECT j.us_market_slug, j.positions, j.open_qty, j.rn, j.premap, j.fixture_metadata,
           left(btrim(coalesce(j.premap ->> 'event_slug', '')), 250) AS event_id,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'home_team', ''),
                          j.premap ->> 'home_team', '')) AS home_x,
           btrim(coalesce(nullif(j.fixture_metadata ->> 'away_team', ''),
                          j.premap ->> 'away_team', '')) AS away_x,
           upper(btrim(coalesce(j.fixture_metadata ->> 'competition', ''))) AS lg1,
           upper(btrim(coalesce(j.premap ->> 'league', ''))) AS lg2,
           upper(btrim(coalesce(j.premap ->> 'sport', ''))) AS lg3,
           coalesce(nullif(j.premap ->> 'game_start', ''),
                    nullif(j.fixture_metadata ->> 'scheduled_kickoff', ''))::timestamptz AS held_start,
           j.fixture_metadata -> 'raw' -> 'game_number' AS game_number_json,
           vp.event_slug IS NOT NULL AS has_team_rows, vp.tuples, vp.ids, vp.codes,
           vp.code_blank, vp.code, vp.starts, vp.start_null, vp.team_start, vp.families,
           vp.name_bad, vp.p1_id, vp.p2_id, vp.p1_primary, vp.p2_primary,
           vp.p1_names, vp.p2_names
      FROM joined j
      LEFT JOIN venue_pair vp ON vp.event_slug = j.premap ->> 'event_slug'
), fields AS (
    SELECT r.*,
           coalesce(r1.league, r2.league, r3.league) AS explicit_league,
           vc.league AS venue_code_league,
           CASE WHEN r.home_x <> '' OR r.away_x <> '' OR NOT r.has_team_rows THEN 'NONE'
                WHEN r.tuples > 16 OR r.ids <> 2 THEN 'CANONICAL_VENUE_PARTICIPANTS_NOT_TWO'
                WHEN r.name_bad THEN 'CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN'
                WHEN r.codes <> 1 OR r.code_blank THEN 'CANONICAL_VENUE_LEAGUE_NOT_ONE'
                WHEN r.starts <> 1 OR r.start_null
                     OR (r.held_start IS NOT NULL AND r.team_start <> r.held_start)
                  THEN 'CANONICAL_EVENT_START_NOT_ONE_INSTANT'
                ELSE 'OK' END AS venue_state
      FROM raw_fields r
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg1), r.lg1)) r1 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg2), r.lg2)) r2 ON true
      LEFT JOIN LATERAL (SELECT g.league FROM registry g
                          WHERE g.league = coalesce((SELECT a.league FROM aliases a WHERE a.name = r.lg3), r.lg3)) r3 ON true
      LEFT JOIN venue_codes vc ON vc.code = r.code
), resolved AS (
    SELECT f.*,
           CASE WHEN f.venue_state = 'OK' THEN f.p1_primary ELSE f.home_x END AS home,
           CASE WHEN f.venue_state = 'OK' THEN f.p2_primary ELSE f.away_x END AS away,
           coalesce(f.explicit_league,
                    CASE WHEN f.venue_state = 'OK' THEN f.venue_code_league END) AS league,
           CASE WHEN f.venue_state = 'OK' THEN coalesce(f.held_start, f.team_start)
                ELSE f.held_start END AS start_at,
           CASE WHEN f.venue_state = 'OK' THEN 'PROVIDER_HOME_AWAY'
                ELSE 'VENUE_HOME_AWAY' END AS orientation,
           CASE WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL THEN 'VENUE_TEAM_LEAGUE_CODE'
                WHEN f.explicit_league IS NOT NULL THEN 'EXPLICIT_COMPETITION' END AS league_basis
      FROM fields f
), normed AS (
    SELECT s.*,
           extract(epoch FROM s.start_at) AS start_epoch,
           btrim(regexp_replace(regexp_replace(lower(s.home), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS nhome,
           btrim(regexp_replace(regexp_replace(lower(s.away), '[^[:alnum:]_[:space:]]', ' ', 'g'),
                                '[[:space:]]+', ' ', 'g')) AS naway,
           (SELECT g.sport FROM registry g WHERE g.league = s.venue_code_league) AS venue_code_sport,
           CASE WHEN s.game_number_json IS NULL OR jsonb_typeof(s.game_number_json) = 'null' THEN 'ABSENT'
                WHEN jsonb_typeof(s.game_number_json) = 'boolean' THEN 'INVALID'
                WHEN jsonb_typeof(s.game_number_json) <> 'number' THEN 'ABSENT'
                WHEN s.game_number_json::text !~ '^-?[0-9]+$' THEN 'ABSENT'
                WHEN (s.game_number_json::text)::numeric BETWEEN 1 AND 9 THEN 'VALID'
                ELSE 'INVALID' END AS game_number_state
      FROM resolved s
), named AS (
    SELECT n.*,
           CASE WHEN n.venue_state = 'OK' THEN n.p1_names ELSE ARRAY[n.nhome] END AS home_names,
           CASE WHEN n.venue_state = 'OK' THEN n.p2_names ELSE ARRAY[n.naway] END AS away_names
      FROM normed n
), judged AS (
    SELECT f.*,
           CASE
             WHEN f.rn > 1000000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
             WHEN f.event_id = '' THEN 'CANONICAL_VENUE_EVENT_MISSING'
             WHEN f.venue_state NOT IN ('NONE', 'OK') THEN f.venue_state
             WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL
                  AND f.venue_code_league IS NULL THEN 'SCORE_LEAGUE_UNSUPPORTED'
             WHEN f.venue_state = 'OK' AND f.explicit_league IS NULL
                  AND f.families IS DISTINCT FROM f.venue_code_sport
               THEN 'CANONICAL_VENUE_SPORT_FAMILY_MISMATCH'
             WHEN f.home = '' OR f.away = '' OR f.league IS NULL OR f.start_at IS NULL
               THEN 'CANONICAL_SCORE_FIXTURE_FIELDS_MISSING'
             WHEN f.fixture_metadata IS NOT NULL
                  AND (f.fixture_metadata ->> 'venue_fixture_key') IS DISTINCT FROM 'event:' || f.event_id
               THEN 'CANONICAL_FIXTURE_JOIN_MISMATCH'
             WHEN coalesce(f.fixture_metadata ->> 'orientation', '') <> ''
                  AND f.fixture_metadata ->> 'orientation' NOT IN ('HOME_AWAY', 'HOME_VS_AWAY', 'home_away')
               THEN 'CANONICAL_HOME_AWAY_UNPROVEN'
             WHEN f.nhome = '' OR f.naway = '' OR f.nhome = f.naway
                  OR f.home_names && f.away_names THEN 'CANONICAL_PARTICIPANTS_INVALID'
             WHEN f.start_epoch < 0 OR f.start_epoch > 4102444800 THEN 'CANONICAL_FIXTURE_EVIDENCE_MISSING'
             WHEN f.game_number_state = 'INVALID' THEN 'GAME_NUMBER_INVALID'
             ELSE 'ESTABLISHED'
           END AS row_verdict
      FROM named f
), conflicts AS (
    SELECT event_id
      FROM judged WHERE row_verdict = 'ESTABLISHED'
     GROUP BY event_id
    HAVING count(DISTINCT (league, orientation, home_names::text, away_names::text, start_epoch,
                           CASE WHEN game_number_state = 'VALID' THEN game_number_json::text END)) > 1
), final AS (
    SELECT j.*,
           CASE WHEN j.row_verdict = 'ESTABLISHED' AND j.event_id IN (SELECT event_id FROM conflicts)
                THEN 'CONFLICTING_CANONICAL_FIXTURE_ROWS' ELSE j.row_verdict END AS collector_verdict
      FROM judged j
)
SELECT f.us_market_slug, f.positions, f.open_qty, f.event_id, f.collector_verdict,
       f.league AS collector_league, f.league_basis AS collector_league_basis,
       f.orientation AS collector_orientation,
       CASE WHEN f.league IS NOT NULL THEN 'ESPN ' || g.espn_path || ' scoreboard dates='
                 || to_char(f.start_at AT TIME ZONE 'America/New_York', 'YYYYMMDD') END AS primary_source_request,
       f.home AS collector_home, f.away AS collector_away, f.start_at AS collector_start,
       f.home_names AS collector_home_names, f.away_names AS collector_away_names,
       f.venue_state, f.code AS venue_league_code, f.families AS venue_sport_families,
       f.p1_id AS venue_team_id_1, f.p2_id AS venue_team_id_2,
       f.premap IS NOT NULL AS premap_row,
       f.premap ->> 'sports_type' AS premap_sports_type,
       f.premap ->> 'listing_state' AS premap_listing_state,
       f.fixture_metadata IS NOT NULL AS fixture_metadata_row,
       f.fixture_metadata ->> 'source' AS fixture_source,
       f.fixture_metadata ->> 'competition' AS fixture_competition,
       f.fixture_metadata ->> 'orientation' AS fixture_orientation,
       round(extract(epoch FROM now() - (f.fixture_metadata ->> 'retrieved_at')::timestamptz)) AS fixture_retrieved_age_s,
       f.premap ->> 'team_league' AS diag_venue_team_league,
       (SELECT count(DISTINCT t.team_name) FROM team_rows t
         WHERE t.event_slug = f.event_id AND t.team_name IS NOT NULL) AS diag_venue_team_names_on_event
  FROM final f
  LEFT JOIN registry g ON g.league = f.league
 ORDER BY f.collector_verdict, f.league NULLS LAST, f.us_market_slug
) d
         WHERE collector_verdict = 'ESTABLISHED') e
 WHERE k <= 6
 ORDER BY collector_league, collector_start, event_id;
