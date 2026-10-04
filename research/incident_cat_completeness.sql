-- P0 INCIDENT, stream VENUE CATALOGUE / PAGINATION / MARKET RETENTION (R30A
-- inc-catalogue). Read-only, bounded: every statement is windowed or grouped,
-- every row dump carries a LIMIT.
--
-- QUESTION. Is `us_premap` (the US venue catalogue written by
-- workers/premap.refresh) the venue's FULL tradable universe, or a sample of
-- it? Each block measures one item of the owner's list:
--   K0  the sweep's own receipt (pages, events, rows, truncation, window)
--   K1  per-event market caps: contracts per event, and whether many events
--       sit at one identical maximum (the signature of a per-event cap)
--   K2  keys collapsing distinct sides: shared-identifier markets (both sides
--       carry identifier = market slug, distinct only by side_norm/intent)
--       that hold ONE row instead of two
--   K3  the start-time window: rows whose game_start lies outside the sweep's
--       [seen-12h, seen+96h] window (zero = the window is the only door), the
--       futures family by league and start offset
--   K4  live retention: events already started, by sport, and how recently
--       the sweep re-saw them (live counterparts kept or aged out)
--   K5  board reader bounds: real soccer / football tokens beyond the board's
--       LIMIT 30, and tokens with more events than the titles carried
--   K6  sides without a usable intent / contract-kind rows, by sport
\echo '== K0 · read instant and the premap receipts (full + fast lanes) =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;
SELECT key, value::text AS receipt
  FROM ingestion_state WHERE key IN ('premap_last', 'premap_last_fast');

\echo '== K1a · contracts per venue event (current listing), distribution per sport =='
WITH ev AS (
    SELECT event_slug,
           max(CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
                    ELSE coalesce(nullif(split_part(coalesce(sports_type, ''), '_', 1), ''), '(none)') END) AS sport,
           count(DISTINCT market_slug) AS contracts, count(*) AS sides
      FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
     GROUP BY 1)
SELECT sport, count(*) AS events, max(contracts) AS max_contracts,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY contracts) AS p50_contracts,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY contracts) AS p90_contracts,
       max(sides) AS max_sides
  FROM ev GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo '== K1b · the most-populated venue events and how many events share each top count (cap signature) =='
WITH ev AS (
    SELECT event_slug, count(DISTINCT market_slug) AS contracts, count(*) AS sides
      FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
     GROUP BY 1)
SELECT contracts, count(*) AS events_with_exactly_this_many,
       min(event_slug) AS example, max(sides) AS sides
  FROM ev GROUP BY 1 ORDER BY 1 DESC LIMIT 25;

\echo '== K2a · rows per market slug, by market shape (shared identifier = both sides carry the market slug) =='
WITH m AS (
    SELECT market_slug, sports_type,
           bool_and(identifier = market_slug) AS shared_identifier,
           count(*) AS rows_n, count(DISTINCT intent) AS intents,
           count(DISTINCT side_norm) AS side_norms
      FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
       AND kind = 'side'
     GROUP BY 1, 2)
SELECT shared_identifier, rows_n, intents, count(*) AS markets,
       min(market_slug) AS example
  FROM m GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT 40;

\echo '== K2b · shared-identifier markets holding ONE row (a side lost to the (identifier, side_norm) key), by sports_type =='
WITH m AS (
    SELECT market_slug, coalesce(sports_type, '(none)') AS sports_type,
           count(*) AS rows_n, min(intent) AS intent, min(side_norm) AS side_norm,
           min(split_part(market_slug, '-', 1)) AS prefix
      FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
       AND kind = 'side' AND identifier = market_slug
     GROUP BY 1, 2)
SELECT sports_type, prefix, intent, count(*) AS one_row_markets,
       min(market_slug) AS example, min(side_norm) AS side_norm_example
  FROM m WHERE rows_n = 1
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo '== K2c · one-row shared-identifier markets: sample rows (side_norm vs question) =='
WITH m AS (
    SELECT market_slug FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
       AND kind = 'side' AND identifier = market_slug
     GROUP BY 1 HAVING count(*) = 1)
SELECT p.market_slug, p.sports_type, p.side_norm, p.intent, p.line, p.signed,
       left(p.question, 100) AS question
  FROM us_premap p JOIN m USING (market_slug)
 ORDER BY p.sports_type, p.market_slug LIMIT 30;

\echo '== K3a · rows outside the sweep window relative to their own last sighting (expect 0 when the window is the only door) =='
SELECT count(*) AS rows_all,
       count(*) FILTER (WHERE game_start < updated_at - interval '12 hours') AS started_over_12h_before_sighting,
       count(*) FILTER (WHERE game_start > updated_at + interval '96 hours') AS starting_over_96h_after_sighting,
       count(*) FILTER (WHERE game_start IS NULL) AS no_game_start,
       max(game_start - updated_at) AS furthest_ahead_of_sighting,
       min(game_start - updated_at) AS furthest_behind_sighting
  FROM us_premap
 WHERE updated_at >= now() - interval '90 minutes';

\echo '== K3b · the futures family in the current table: league, events, contracts, start offset from sighting =='
SELECT lower(split_part(coalesce(event_slug, ''), '-', 1)) AS league,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS contracts,
       count(*) AS sides,
       min(game_start - updated_at) AS min_start_offset,
       max(game_start - updated_at) AS max_start_offset,
       (array_agg(DISTINCT left(event_title, 60)))[1:4] AS titles
  FROM us_premap
 WHERE sports_type = 'futures' AND updated_at >= now() - interval '26 hours'
 GROUP BY 1 ORDER BY 3 DESC LIMIT 40;

\echo '== K3c · start-offset histogram of every row re-seen in 90 min (hours from sighting; window edges -12 / +96) =='
SELECT width_bucket(extract(epoch FROM game_start - updated_at) / 3600.0, -24, 120, 12) AS bucket,
       min(round((extract(epoch FROM game_start - updated_at) / 3600.0)::numeric, 1)) AS from_h,
       max(round((extract(epoch FROM game_start - updated_at) / 3600.0)::numeric, 1)) AS to_h,
       count(*) AS rows_n, count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE updated_at >= now() - interval '90 minutes'
 GROUP BY 1 ORDER BY 1 LIMIT 20;

\echo '== K4 · live retention: events started in the last 12 h, by sport, and when the sweep last re-saw them =='
WITH ev AS (
    SELECT event_slug,
           max(CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
                    ELSE coalesce(nullif(split_part(coalesce(sports_type, ''), '_', 1), ''), '(none)') END) AS sport,
           min(game_start) AS start_at, max(updated_at) AS last_seen,
           count(DISTINCT market_slug) AS contracts
      FROM us_premap
     WHERE game_start >= now() - interval '12 hours' AND game_start <= now()
     GROUP BY 1)
SELECT sport, count(*) AS started_events,
       count(*) FILTER (WHERE last_seen >= now() - interval '10 minutes') AS reseen_10m,
       count(*) FILTER (WHERE last_seen >= now() - interval '35 minutes') AS reseen_35m,
       count(*) FILTER (WHERE last_seen < now() - interval '90 minutes') AS not_reseen_90m,
       sum(contracts) FILTER (WHERE last_seen >= now() - interval '35 minutes') AS live_contracts_reseen_35m
  FROM ev GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

\echo '== K5a · real board tokens per family and their rank (the board SQL keeps LIMIT 30 per family) =='
WITH t AS (
    SELECT split_part(sports_type, '_', 1) AS fam,
           split_part(market_slug, '-', 2) AS token,
           count(DISTINCT event_slug) AS events,
           count(DISTINCT left(event_title, 80)) AS titles
      FROM us_premap
     WHERE (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%')
       AND game_start > now() - interval '6 hours'
       AND lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) !~
           '(ebattles|e-battles|esoccer|e-soccer|ebasketball|efootball|e-football|simulated|cyber|virtual|e-cricket|ehockey|e-hockey|etennis|e-tennis)'
     GROUP BY 1, 2)
SELECT fam, token, events, titles,
       row_number() OVER (PARTITION BY fam ORDER BY events DESC, token) AS board_row,
       CASE WHEN row_number() OVER (PARTITION BY fam ORDER BY events DESC, token) > 30
            THEN 'BEYOND_LIMIT_30' ELSE '' END AS board_cut,
       CASE WHEN fam = 'soccer' AND titles > 12 THEN 'TITLES_OVER_12'
            WHEN fam = 'football' AND titles > 120 THEN 'TITLES_OVER_120' ELSE '' END AS titles_cut
  FROM t ORDER BY fam, events DESC, token LIMIT 200;

\echo '== K6 · sides the catalogue holds without an orderable intent, and contract-kind rows, by sport (current listing) =='
SELECT CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
            ELSE coalesce(nullif(split_part(coalesce(sports_type, ''), '_', 1), ''), '(none)') END AS sport,
       count(*) AS sides,
       count(*) FILTER (WHERE intent IS NULL) AS no_intent,
       count(*) FILTER (WHERE kind = 'contract') AS contract_kind,
       count(*) FILTER (WHERE coalesce(line, '') <> '' AND sports_type ~ 'winner$') AS winner_with_line,
       count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;
