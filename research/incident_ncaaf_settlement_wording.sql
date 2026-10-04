-- P0 INCIDENT, NCAAF SETTLEMENT STREAM, read-only. The evidence the NCAAF
-- money-line settlement proof is built from (the R30A NFL design, extended):
--
-- N1/N2  Is the venue's college-football rules wording PRESENT and the SAME on
--        EVERY production cfb valuation row? Grouped by the text with the game
--        name and date MASKED, with NO limit, so every row is counted in
--        exactly one wording group (the NFL stream's W1b shape).
-- N3/N4  The book's priced outcome set kept on every refused cfb row (raw_odds,
--        outcomes_priced): how many outcomes, any Draw, which provider, the
--        overround -- the measurement a league-scoped de-vig admission needs.
-- N5     The ten newest cfb rows as recorded.
-- N6/N7  The venue catalogue rows of cfb money lines (sports_type, team_league,
--        identifier shape) and their dates: slug date vs the kickoff's
--        America/New_York day and UTC day.
-- N8     The paper decisions on cfb rows (72 h): strategy x first refusal.
-- Every query is bounded (time window or LIMIT); the workflow sets the
-- statement timeout.
\echo '== N0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== N1 · every cfb valuation row: rules-text presence (all time) =='
SELECT count(*) AS rows,
       count(DISTINCT us_market_slug) AS contracts,
       min(decided_at) AS first_at, max(decided_at) AS last_at,
       count(*) FILTER (WHERE settlement_comparison ? 'venue_rules_text') AS with_text,
       count(*) FILTER (WHERE NOT coalesce(settlement_comparison ? 'venue_rules_text', false)) AS without_text
  FROM external_valuations
 WHERE us_market_slug LIKE 'aec-cfb-%';

\echo '== N1b · cfb valuation rows by sport_family / market (all time) =='
SELECT coalesce(sport_family, '<null>') AS sport_family, coalesce(market, '<null>') AS market,
       count(*) AS rows, count(DISTINCT us_market_slug) AS contracts
  FROM external_valuations
 WHERE us_market_slug LIKE 'aec-cfb-%'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo '== N2 · every cfb valuation row, grouped by MASKED wording (no limit) =='
SELECT md5(masked) AS masked_md5, count(*) AS rows,
       count(DISTINCT us_market_slug) AS contracts,
       count(DISTINCT settlement_comparison->>'venue_rules_sha256') AS distinct_text_sha,
       min(decided_at) AS first_at, max(decided_at) AS last_at,
       masked AS masked_text
  FROM (SELECT us_market_slug, decided_at, settlement_comparison,
               regexp_replace(settlement_comparison->>'venue_rules_text',
                              'This market will settle to the winner of the .* College Football game scheduled for [A-Za-z]+ [0-9]+, [0-9]{4}\.',
                              '<WINNER-OF-NAMED-GAME-ON-DATE>.') AS masked
          FROM external_valuations
         WHERE us_market_slug LIKE 'aec-cfb-%'
           AND settlement_comparison ? 'venue_rules_text') t
 GROUP BY masked ORDER BY 2 DESC;

\echo '== N2b · the masked game sentence itself: league words and date form =='
SELECT (settlement_comparison->>'venue_rules_text') ~ 'College Football game scheduled for [A-Za-z]+ [0-9]+, [0-9]{4}\.' AS says_college_football_game_on_date,
       (settlement_comparison->>'venue_rules_text') ~* 'NFL game' AS says_nfl_game,
       (settlement_comparison->>'venue_rules_text') ~* '\mtie\M|\mtied\M|\mdraw\M' AS mentions_a_tie,
       count(*) AS rows, count(DISTINCT us_market_slug) AS contracts
  FROM external_valuations
 WHERE us_market_slug LIKE 'aec-cfb-%' AND settlement_comparison ? 'venue_rules_text'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo '== N3 · cfb valuation rows: outcome-set shape (all time) =='
SELECT count(*) AS rows,
       count(DISTINCT us_market_slug) AS contracts,
       count(*) FILTER (WHERE outcomes_priced = 2) AS priced_2,
       count(*) FILTER (WHERE outcomes_priced = 3) AS priced_3,
       count(*) FILTER (WHERE outcomes_priced IS NULL) AS priced_null,
       count(*) FILTER (WHERE outcomes_priced NOT IN (2, 3)) AS priced_other,
       count(*) FILTER (WHERE raw_odds ? 'Draw' OR raw_odds ? 'draw' OR raw_odds ? 'Tie' OR raw_odds ? 'tie' OR raw_odds ? 'X') AS with_draw_key,
       count(*) FILTER (WHERE raw_odds IS NOT NULL) AS with_raw_odds,
       count(*) FILTER (WHERE book = 'pinnacle') AS book_pinnacle,
       count(*) FILTER (WHERE probability IS NOT NULL) AS with_probability
  FROM external_valuations
 WHERE us_market_slug LIKE 'aec-cfb-%';

\echo '== N4 · per provider: outcome counts, overround range =='
SELECT provider, outcomes_priced, expected_outcomes, count(*) AS n,
       count(DISTINCT us_market_slug) AS contracts,
       round(min(overround)::numeric, 5) AS overround_min,
       round(max(overround)::numeric, 5) AS overround_max
  FROM external_valuations
 WHERE us_market_slug LIKE 'aec-cfb-%'
 GROUP BY 1, 2, 3 ORDER BY 1, 2 LIMIT 40;

\echo '== N5 · ten newest cfb rows: the priced set as recorded =='
SELECT id, decided_at, us_market_slug, contract_selection, provider,
       outcomes_priced, raw_odds, round(overround::numeric, 5) AS overround,
       round(extract(epoch FROM (received_at - observed_at))::numeric, 3) AS receipt_minus_observed_s,
       record_purpose, refusals[1:4] AS first_refusals,
       settlement_comparison->>'compatibility' AS compatibility,
       settlement_comparison->>'venue_rules_sha256' AS venue_rules_sha256
  FROM external_valuations
 WHERE us_market_slug LIKE 'aec-cfb-%'
 ORDER BY decided_at DESC LIMIT 10;

\echo '== N6 · cfb catalogue rows (us_premap, game_start > now - 6 h): the fields the census reads =='
SELECT coalesce(sports_type, '<null>') AS sports_type, lower(coalesce(team_league, '<null>')) AS team_league,
       split_part(identifier, '-', 1) || '-' || split_part(identifier, '-', 2) AS identifier_prefix,
       (identifier = market_slug) AS identifier_is_market_slug,
       kind, (line IS NULL OR line::text = '' OR line::text = '00') AS line_blank,
       count(*) AS rows, count(DISTINCT market_slug) AS contracts,
       count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb' AND game_start > now() - interval '6 hours'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 7 DESC LIMIT 30;

\echo '== N7 · cfb money-line catalogue dates: slug date vs the kickoff ET day / UTC day =='
SELECT count(*) AS rows, count(DISTINCT market_slug) AS contracts,
       min(d_et) AS first_et_day, max(d_et) AS last_et_day,
       count(*) FILTER (WHERE substring(market_slug FROM '([0-9]{4}-[0-9]{2}-[0-9]{2})$')::date = d_et) AS slug_date_equals_et_day,
       count(*) FILTER (WHERE substring(market_slug FROM '([0-9]{4}-[0-9]{2}-[0-9]{2})$')::date = d_utc) AS slug_date_equals_utc_day,
       count(*) FILTER (WHERE substring(market_slug FROM '([0-9]{4}-[0-9]{2}-[0-9]{2})$') IS NULL) AS slug_without_date
  FROM (SELECT market_slug, (game_start AT TIME ZONE 'America/New_York')::date AS d_et,
               (game_start AT TIME ZONE 'UTC')::date AS d_utc
          FROM us_premap
         WHERE market_slug LIKE 'aec-cfb-%' AND sports_type = 'football_team_full_game_winner'
           AND game_start > now() - interval '3 days' AND game_start < now() + interval '10 days') t;

\echo '== N8 · paper decisions on cfb rows (72 h): strategy x verdict x first refusal =='
SELECT d.strategy, d.verdict, coalesce(d.refusal, '<enter>') AS refusal,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d
 WHERE d.decided_at > now() - interval '72 hours' AND d.us_market_slug LIKE 'aec-cfb-%'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo '== N8b · every refusal code on cfb decisions (72 h), unnested =='
SELECT d.strategy, r AS refusal_code, count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d, unnest(d.refusals) AS r
 WHERE d.decided_at > now() - interval '72 hours' AND d.us_market_slug LIKE 'aec-cfb-%'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 60;
