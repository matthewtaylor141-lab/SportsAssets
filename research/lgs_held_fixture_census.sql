-- READ-ONLY. BETTOR LIVE GAME STATE V1 (migration 316): THE HELD-FIXTURE
-- MAPPING CENSUS -- for the current held POLYMARKET_US PAPER positions of
-- paper_acct_main, the fixture identity the display collector WOULD
-- establish, by league, with the collector's own named refusal for every
-- market it would not. Runs on today's production: it reads only canonical
-- tables (paper_fills, paper_settlements, us_premap, venue_fixture_metadata)
-- and needs neither migration 316 nor a running collector.
--
-- IT MIRRORS THE COLLECTOR CHECK FOR CHECK, IN THE COLLECTOR'S ORDER
-- (backend/sportsassets/live_game_state/storage.py fixture_query +
-- fixture_from_row, then core.Fixture.__post_init__, then the conflict rule
-- in PostgresStore.fixtures):
--   held      CANONICAL_OPEN_POSITIONS_SQL narrowed to the account, one row
--             per distinct us_market_slug, ordered by slug; the collector
--             reads 1001 and keeps 1000 (rn > 1000 is named, never dropped)
--   premap    us_premap identifier = market_slug = the slug, newest first
--   fixture   venue_fixture_metadata ('PMUS'|'POLYMARKET_US',
--             'event:'||premap.event_slug), newest retrieval first
--   1 CANONICAL_VENUE_EVENT_MISSING          no premap event_slug
--   2 CANONICAL_SCORE_FIXTURE_FIELDS_MISSING home/away (fixture row, else
--             premap home_team/away_team), league (fixture competition,
--             else premap league, else premap sport, through the package's
--             league registry + aliases) or start (premap game_start, else
--             fixture scheduled_kickoff) absent
--   3 CANONICAL_FIXTURE_JOIN_MISMATCH        fixture key <> 'event:'||event
--   4 CANONICAL_HOME_AWAY_UNPROVEN           fixture orientation present and
--             not HOME_AWAY / HOME_VS_AWAY / home_away
--   5 CANONICAL_PARTICIPANTS_INVALID         a normalised name empty, or
--             both names equal
--   6 CANONICAL_FIXTURE_EVIDENCE_MISSING     start outside [0, 4102444800]
--   7 GAME_NUMBER_INVALID                    fixture raw.game_number not an
--             integer 1..9
--   8 CONFLICTING_CANONICAL_FIXTURE_ROWS     two held markets of one event
--             disagree on league / home / away / start / game number (the
--             whole event is excluded, as the collector excludes it)
--   ESTABLISHED                              the collector would poll it
-- The name normalisation is the SQL rendering of core.norm (casefold,
-- punctuation -> space, whitespace collapsed); NFKC folding is not applied
-- here, which can only matter for check 5 on non-ASCII names.
--
-- WHAT THE COLUMNS PREFIXED diag_ ARE: facts the adapter does NOT read,
-- shown so a code-controlled gap is named rather than guessed at (e.g. the
-- venue's own lower-case team_league on the premap row, and how many
-- distinct venue team_name rows the event carries). They are never counted
-- as coverage. Nothing here parses a title, a slug or a question.
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
             WHEN f.rn > 1000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
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
 ORDER BY collector_verdict, j.league NULLS LAST, j.us_market_slug;

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
    SELECT us_market_slug, count(*) AS positions,
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
), registry (league) AS (VALUES
    ('NFL'), ('NCAAF'), ('MLB'), ('NBA'), ('WNBA'), ('NCAAB'), ('NCAAW'),
    ('NHL'), ('EPL'), ('MLS'), ('UCL')
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
             WHEN f.rn > 1000 THEN 'NOT_READ_COLLECTOR_TRUNCATES_ABOVE_1000_MARKETS'
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
), final AS (
    SELECT j.*,
           CASE WHEN j.row_verdict = 'ESTABLISHED' AND j.event_id IN (SELECT event_id FROM conflicts)
                THEN 'CONFLICTING_CANONICAL_FIXTURE_ROWS' ELSE j.row_verdict END AS collector_verdict
      FROM judged j
)
SELECT coalesce(league, 'UNRESOLVED (venue team_league=' || coalesce(premap ->> 'team_league', 'null') || ')') AS league,
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
)
SELECT count(*) AS held_markets,
       count(*) FILTER (WHERE premap IS NOT NULL) AS premap_row,
       count(*) FILTER (WHERE coalesce(btrim(premap ->> 'event_slug'), '') <> '') AS premap_event_slug,
       count(*) FILTER (WHERE premap ? 'home_team' OR premap ? 'away_team') AS premap_has_home_away_keys,
       count(*) FILTER (WHERE premap ? 'league' OR premap ? 'sport') AS premap_has_league_or_sport_keys,
       count(*) FILTER (WHERE coalesce(premap ->> 'game_start', '') <> '') AS premap_game_start,
       count(*) FILTER (WHERE fixture_metadata IS NOT NULL) AS fixture_metadata_row,
       count(*) FILTER (WHERE coalesce(fixture_metadata ->> 'home_team', '') <> ''
                          AND coalesce(fixture_metadata ->> 'away_team', '') <> '') AS fixture_home_and_away,
       count(*) FILTER (WHERE upper(coalesce(fixture_metadata ->> 'competition', '')) IN
                        ('NFL', 'NCAAF', 'MLB', 'NBA', 'WNBA', 'NCAAB', 'NCAAW', 'NHL', 'EPL', 'MLS', 'UCL')) AS fixture_competition_in_registry,
       count(*) FILTER (WHERE upper(coalesce(premap ->> 'team_league', '')) IN
                        ('NFL', 'NCAAF', 'CFB', 'MLB', 'NBA', 'WNBA', 'NCAAB', 'NCAAW', 'NHL', 'EPL', 'MLS', 'UCL')) AS diag_venue_team_league_in_registry,
       string_agg(DISTINCT fixture_metadata ->> 'orientation', ',') AS fixture_orientation_values,
       string_agg(DISTINCT premap ->> 'team_league', ',') AS diag_venue_team_league_values
  FROM joined;

\echo == 5. venue_fixture_metadata as a whole: which competitions it covers (any event, not only held) ==
SELECT venue, competition, source, orientation, count(*) AS rows,
       max(retrieved_at) AS newest_retrieval
  FROM venue_fixture_metadata
 GROUP BY 1, 2, 3, 4
 ORDER BY rows DESC;
