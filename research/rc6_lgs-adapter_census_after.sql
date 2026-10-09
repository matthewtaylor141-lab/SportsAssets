-- READ-ONLY. (RC6 lane G2 copy of research/lgs_held_fixture_census.sql at the
-- lane head, run on production before the deploy.) BETTOR LIVE GAME STATE V1 (migration 316): THE HELD-FIXTURE
-- MAPPING CENSUS -- for the current held POLYMARKET_US PAPER positions of
-- paper_acct_main, the fixture identity the display collector WOULD
-- establish, by league, with the collector's own named refusal for every
-- market it would not. Runs on today's production: it reads only canonical
-- tables (paper_fills, paper_settlements, us_premap, venue_fixture_metadata)
-- and needs neither migration 316 nor a running collector.
--
-- LANE G2 (requirement register F3). The adapter used to read premap keys
-- home_team / away_team / league / sport, which us_premap has never carried
-- (workers/premap._ensure_table creates none of them; research-sql run
-- 37880110851 lists the 22 columns). What the table DOES carry, on every
-- catalogue row of an event, is the venue's own team object of the row's
-- side: team_id, team_name, team_safe_name, team_abbr, team_league, plus the
-- market's game_start and sports_type. Every candidate event of the next
-- 48 h in a score league, and every event a PAPER account has ever held,
-- names exactly two team ids with one league code and one start (runs
-- 37880110851 section 4, 37880299272 section 5). The adapter now reads them;
-- this census mirrors it.
--
-- IT MIRRORS THE COLLECTOR CHECK FOR CHECK, IN THE COLLECTOR'S ORDER
-- (backend/sportsassets/live_game_state/storage.py fixture_query +
-- fixture_from_row + venue_participants, then core.Fixture.__post_init__,
-- then the conflict rule in PostgresStore.fixtures):
--   held      CANONICAL_OPEN_POSITIONS_SQL narrowed to the account, one row
--             per distinct us_market_slug, ordered by slug; the collector
--             reads 1001 and keeps 1000 (rn > 1000 is named, never dropped)
--   premap    us_premap identifier = market_slug = the slug, newest first
--   fixture   venue_fixture_metadata ('PMUS'|'POLYMARKET_US',
--             'event:'||premap.event_slug), newest retrieval first
--   teams     the DISTINCT (team_id, team_name, team_safe_name, team_abbr,
--             team_league, game_start, sport family) tuples of every us_premap
--             row of the held event that has a team_id, at most 16 read
--   1 CANONICAL_VENUE_EVENT_MISSING          no premap event_slug
--   when NO row states home or away (fixture row, else premap keys), the
--   venue's two team objects are the participants, home/away left to the
--   score provider (orientation PROVIDER_HOME_AWAY):
--   2 CANONICAL_VENUE_PARTICIPANTS_NOT_TWO     more than 16 tuples, or not
--             exactly two team ids (a futures board, a team-less market)
--   3 CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN one team id under two
--             (team_name, team_safe_name) pairs, or under no name at all
--   4 CANONICAL_VENUE_LEAGUE_NOT_ONE           the team rows state no league
--             code, or more than one
--   5 CANONICAL_EVENT_START_NOT_ONE_INSTANT    the team rows state no start,
--             several, or not the held row's game_start
--   6 SCORE_LEAGUE_UNSUPPORTED                 no explicit competition and the
--             venue code is not in core.VENUE_LEAGUE_CODES (e.g. idnsl; and
--             ncaams / ncaaws, which are NCAA SOCCER on the venue)
--   7 CANONICAL_VENUE_SPORT_FAMILY_MISMATCH    the team rows' sportsMarketType
--             family (prefix to the first '_') is not that league's sport
--   then, for every path:
--   8 CANONICAL_SCORE_FIXTURE_FIELDS_MISSING home/away (fixture row, else
--             premap home_team/away_team, else the two venue participants),
--             league (fixture competition, else premap league, else premap
--             sport, through the package's league registry + aliases, else the
--             venue code) or start (premap game_start, else fixture
--             scheduled_kickoff, else the team rows' one start) absent
--   9 CANONICAL_FIXTURE_JOIN_MISMATCH        fixture key <> 'event:'||event
--  10 CANONICAL_HOME_AWAY_UNPROVEN           fixture orientation present and
--             not HOME_AWAY / HOME_VS_AWAY / home_away
--  11 CANONICAL_PARTICIPANTS_INVALID         a normalised name empty, both
--             names equal, or one participant's venue names (team_name,
--             team_safe_name) naming the other
--  12 CANONICAL_FIXTURE_EVIDENCE_MISSING     start outside [0, 4102444800]
--  13 GAME_NUMBER_INVALID                    fixture raw.game_number not an
--             integer 1..9
--  14 CONFLICTING_CANONICAL_FIXTURE_ROWS     two held markets of one event
--             disagree on league / orientation / names / start / game number
--             (the whole event is excluded, as the collector excludes it)
--   ESTABLISHED                              the collector would poll it
-- The name normalisation is the SQL rendering of core.norm (casefold,
-- punctuation -> space, whitespace collapsed); NFKC folding is not applied
-- here, which can only matter for check 11 on non-ASCII names.
--
-- ESTABLISHED IS AN IDENTITY, NOT A SCORE. A PROVIDER_HOME_AWAY fixture is
-- bound to a provider game only when each of the game's two teams equals
-- one venue participant name IN FULL (core.assign_sides): no substring, city
-- or abbreviation, and no provider is authorized today (flags OFF), so an
-- established fixture still shows UNAVAILABLE until one is.
--
-- WHAT THE COLUMNS PREFIXED diag_ ARE: facts shown beside the verdict (the
-- held row's own team_league and how many distinct venue team_name values
-- the event carries); they are never counted as coverage. Nothing here
-- parses a title, a slug or a question.
--
-- Every statement is a SELECT. The account literal appears once per
-- statement ('paper_acct_main'); tests/test_trader_live_game_real_db.py
-- runs this file against seeded rows with that literal replaced and asserts
-- the refusals equal PostgresStore.fixtures() on the same database.

\echo == 1. population: held PAPER positions, markets, events (paper_acct_main) ==
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
    SELECT * FROM held WHERE account_id = 'paper_acct_main'
), markets AS (
    SELECT us_market_slug FROM acct GROUP BY us_market_slug
), ev AS (
    SELECT m.us_market_slug, pm.event_slug
      FROM markets m
      LEFT JOIN LATERAL (
        SELECT u.event_slug FROM us_premap u
         WHERE u.identifier = m.us_market_slug AND u.market_slug = m.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
)
SELECT now() AS census_at,
       (SELECT count(*) FROM acct) AS held_positions,
       (SELECT count(*) FROM markets) AS held_markets,
       (SELECT count(DISTINCT event_slug) FROM ev WHERE event_slug IS NOT NULL) AS held_events,
       (SELECT count(*) FROM ev WHERE event_slug IS NULL) AS markets_without_premap_event,
       (SELECT count(*) FROM markets) > 1000 AS collector_would_truncate,
       'POLYMARKET_US' AS venue,
       'the only automatic holdings adapter is POLYMARKET_US PAPER; KALSHI holdings are not connected' AS scope;

\echo == 2. per held market: the identity the collector would establish, or its refusal ==
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
    SELECT * FROM held WHERE account_id = 'paper_acct_main'
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
             WHEN f.rn > 1000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
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
 ORDER BY f.collector_verdict, f.league NULLS LAST, f.us_market_slug;

\echo == 3. by league x verdict: markets, positions and events the collector would / would not poll ==
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
    SELECT * FROM held WHERE account_id = 'paper_acct_main'
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
             WHEN f.rn > 1000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
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

\echo == 4. which canonical field is missing: availability over the held markets (diag_ columns are NOT read by the adapter) ==
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
    SELECT * FROM held WHERE account_id = 'paper_acct_main'
), markets AS (
    SELECT us_market_slug FROM acct GROUP BY us_market_slug
), joined AS (
    SELECT m.us_market_slug, to_jsonb(pm) AS premap, to_jsonb(fm) AS fixture_metadata
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
), team_ids AS (
    SELECT u.event_slug, count(DISTINCT u.team_id) AS ids,
           count(DISTINCT u.team_league) AS codes, min(lower(btrim(u.team_league))) AS code
      FROM us_premap u
     WHERE u.team_id IS NOT NULL
       AND u.event_slug IN (SELECT j.premap ->> 'event_slug' FROM joined j)
     GROUP BY u.event_slug
)
SELECT count(*) AS held_markets,
       count(*) FILTER (WHERE premap IS NOT NULL) AS premap_row,
       count(*) FILTER (WHERE coalesce(btrim(premap ->> 'event_slug'), '') <> '') AS premap_event_slug,
       count(*) FILTER (WHERE premap ? 'home_team' OR premap ? 'away_team') AS premap_has_home_away_keys,
       count(*) FILTER (WHERE premap ? 'league' OR premap ? 'sport') AS premap_has_league_or_sport_keys,
       count(*) FILTER (WHERE coalesce(premap ->> 'game_start', '') <> '') AS premap_game_start,
       count(*) FILTER (WHERE t.event_slug IS NOT NULL) AS event_has_venue_team_rows,
       count(*) FILTER (WHERE t.ids = 2) AS event_names_two_venue_team_ids,
       count(*) FILTER (WHERE t.ids = 2 AND t.codes = 1
                          AND t.code IN ('nfl', 'cfb', 'mlb', 'nba', 'wnba', 'nhl', 'epl', 'mls', 'ucl')) AS event_venue_code_in_score_registry,
       count(*) FILTER (WHERE fixture_metadata IS NOT NULL) AS fixture_metadata_row,
       count(*) FILTER (WHERE coalesce(fixture_metadata ->> 'home_team', '') <> ''
                          AND coalesce(fixture_metadata ->> 'away_team', '') <> '') AS fixture_home_and_away,
       count(*) FILTER (WHERE upper(coalesce(fixture_metadata ->> 'competition', '')) IN
                        ('NFL', 'NCAAF', 'MLB', 'NBA', 'WNBA', 'NCAAB', 'NCAAW', 'NHL', 'EPL', 'MLS', 'UCL')) AS fixture_competition_in_registry,
       string_agg(DISTINCT fixture_metadata ->> 'orientation', ',') AS fixture_orientation_values,
       string_agg(DISTINCT t.code, ',') AS venue_league_codes_on_held_events,
       string_agg(DISTINCT premap ->> 'team_league', ',') AS diag_venue_team_league_values
  FROM joined j
  LEFT JOIN team_ids t ON t.event_slug = j.premap ->> 'event_slug';

\echo == 5. venue_fixture_metadata as a whole: which competitions it covers (any event, not only held) ==
SELECT venue, competition, source, orientation, count(*) AS rows,
       max(retrieved_at) AS newest_retrieval
  FROM venue_fixture_metadata
 GROUP BY 1, 2, 3, 4
 ORDER BY rows DESC;
