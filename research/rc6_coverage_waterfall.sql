-- RC6 lane D2: THE CONTRACT-LEVEL COVERAGE WATERFALL, read only.
--
-- Every active registry contract is classified ONCE (CTE c) and every table
-- below is an aggregate of that one classification, so the stages sum
-- exactly: catalogue = TARGET_A + TARGET_B + EXCLUDED, and within a tier
--   target = lost_external + lost_mapping + lost_settlement
--          + lost_fair_value + lost_fresh_book + priceable + unclassified.
-- The stage of loss is the plane's own sequential terminal state
-- (market_plane.coverage.terminal: external -> mapped -> settlement proven
-- -> fair-value source -> fresh canonical book), read from the registry.
--
-- TARGET UNIVERSE (proposed from research/codex_continuous_coverage_
-- readiness.md "Required outcome": "the complete available, entitled venue
-- catalogue, pregame and in-play, including moneyline, totals, spreads,
-- alternate spreads and team totals"; no sport, league or horizon limit is
-- stated there, so none is applied):
--   TARGET_A  full-game moneyline / spread (every line, alternates included)
--             / game total / team total, every sport and league listed
--   TARGET_B  the same four families on a period or segment (halves,
--             quarters, periods, innings, sets, maps, regulation)
--   EXCLUDED  named classes only: PLAYER_PROP, OTHER_PROP (exact score,
--             first scorer, team stat totals, corners, ...), OUTRIGHT
--             (futures), NON_SPORTS (crypto price markets), and Kalshi
--             series whose ticker names no game family.
-- Kalshi family is read from the series ticker suffix ONLY for this
-- waterfall (research classification; the mapping is the Kalshi lane's).
\echo === W0. catalogue = TARGET_A + TARGET_B + EXCLUDED, per venue (ROLLUP row = all) ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
SELECT coalesce(venue, 'ALL') AS venue, count(*) AS catalogue,
       count(*) FILTER (WHERE tier = 'TARGET_A') AS target_a,
       count(*) FILTER (WHERE tier = 'TARGET_B') AS target_b,
       count(*) FILTER (WHERE tier = 'EXCLUDED') AS excluded,
       count(*) FILTER (WHERE stage = 'PRICEABLE') AS priceable,
       count(*) FILTER (WHERE tier = 'TARGET_A' AND stage = 'PRICEABLE') AS priceable_a,
       count(*) FILTER (WHERE tier = 'TARGET_B' AND stage = 'PRICEABLE') AS priceable_b,
       count(*) FILTER (WHERE valued_24h) AS valued_24h
  FROM t GROUP BY ROLLUP (venue) ORDER BY venue NULLS LAST;
\echo === W1. stage of loss per venue x tier x family x segment (n = sum of the stage columns) ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
SELECT venue, tier, fam, seg, count(*) AS n,
       count(*) FILTER (WHERE stage = 'LOST_EXTERNAL') AS l_ext,
       count(*) FILTER (WHERE stage = 'LOST_MAPPING') AS l_map,
       count(*) FILTER (WHERE stage = 'LOST_SETTLEMENT') AS l_settle,
       count(*) FILTER (WHERE stage = 'LOST_FAIR_VALUE') AS l_fv,
       count(*) FILTER (WHERE stage = 'LOST_FRESH_BOOK') AS l_fresh,
       count(*) FILTER (WHERE stage = 'PRICEABLE') AS priceable,
       count(*) FILTER (WHERE stage = 'UNCLASSIFIED') AS unclass,
       count(*) FILTER (WHERE valued_24h) AS valued24,
       count(*) FILTER (WHERE within_48h) AS in48,
       count(*) FILTER (WHERE within_48h AND stage = 'PRICEABLE') AS in48_pr
  FROM t GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 5 DESC;
\echo === W2. TARGET_A on POLYMARKET_US: stage of loss per sport x family ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
SELECT sport, fam, count(*) AS n,
       count(*) FILTER (WHERE stage = 'LOST_EXTERNAL') AS l_ext,
       count(*) FILTER (WHERE stage = 'LOST_MAPPING') AS l_map,
       count(*) FILTER (WHERE stage = 'LOST_SETTLEMENT') AS l_settle,
       count(*) FILTER (WHERE stage = 'LOST_FAIR_VALUE') AS l_fv,
       count(*) FILTER (WHERE stage = 'LOST_FRESH_BOOK') AS l_fresh,
       count(*) FILTER (WHERE stage = 'PRICEABLE') AS priceable,
       count(*) FILTER (WHERE stage = 'UNCLASSIFIED') AS unclass,
       count(*) FILTER (WHERE valued_24h) AS valued24,
       count(*) FILTER (WHERE within_48h) AS in48,
       count(*) FILTER (WHERE within_48h AND stage = 'PRICEABLE') AS in48_pr
  FROM t WHERE tier = 'TARGET_A' AND venue = 'POLYMARKET_US'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 80;
\echo === W3. TARGET_A moneylines on POLYMARKET_US: per sport x league (top 70 by n) ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
SELECT sport, league, count(*) AS n,
       count(*) FILTER (WHERE stage = 'LOST_EXTERNAL') AS l_ext,
       count(*) FILTER (WHERE stage = 'LOST_MAPPING') AS l_map,
       count(*) FILTER (WHERE stage = 'LOST_SETTLEMENT') AS l_settle,
       count(*) FILTER (WHERE stage = 'LOST_FAIR_VALUE') AS l_fv,
       count(*) FILTER (WHERE stage = 'LOST_FRESH_BOOK') AS l_fresh,
       count(*) FILTER (WHERE stage = 'PRICEABLE') AS priceable,
       count(*) FILTER (WHERE stage = 'UNCLASSIFIED') AS unclass,
       count(*) FILTER (WHERE valued_24h) AS valued24,
       count(*) FILTER (WHERE within_48h) AS in48,
       count(*) FILTER (WHERE within_48h AND stage = 'PRICEABLE') AS in48_pr
  FROM t WHERE tier = 'TARGET_A' AND venue = 'POLYMARKET_US' AND fam = 'MONEYLINE'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 70;
\echo === W4. named drop reasons, TARGET_A and TARGET_B (stage, reason truncated to 110; top 70) ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
SELECT tier, venue, stage, left(why, 110) AS reason, count(*) AS n,
       count(*) FILTER (WHERE valued_24h) AS valued24,
       count(*) FILTER (WHERE within_48h) AS in48
  FROM t WHERE tier <> 'EXCLUDED' AND stage <> 'PRICEABLE'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 70;
\echo === W5. TARGET_A POLYMARKET_US: settlement_state x valued in 24 h x stage, per family ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
SELECT fam, coalesce(settlement_state, '<null>') AS settlement_state,
       valued_24h, stage, count(*) AS n, count(*) FILTER (WHERE within_48h) AS in48
  FROM t WHERE tier = 'TARGET_A' AND venue = 'POLYMARKET_US'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;
\echo === W6. TARGET_A POLYMARKET_US moneylines within 48 h never valued in 24 h: the latest collector first refusal (24 h) ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
, nv AS (SELECT contract_id, sport FROM t
             WHERE tier = 'TARGET_A' AND venue = 'POLYMARKET_US'
               AND fam = 'MONEYLINE' AND within_48h AND NOT valued_24h),
 lc AS (SELECT DISTINCT ON (o.us_market_slug) o.us_market_slug,
               o.first_refusal, o.stage
          FROM ext_candidate_outcomes o
         WHERE o.cycle_at > now() - interval '24 hours'
           AND o.us_market_slug IN (SELECT contract_id FROM nv)
         ORDER BY o.us_market_slug, o.cycle_at DESC)
SELECT nv.sport, coalesce(lc.stage, '-') AS stage,
       coalesce(lc.first_refusal, 'NOT_A_COLLECTOR_CANDIDATE_IN_24H') AS first_refusal,
       count(*) AS n
  FROM nv LEFT JOIN lc ON lc.us_market_slug = nv.contract_id
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;
\echo === W7. EXCLUDED: per venue x class x segment (every excluded contract, exact) ===
WITH c AS MATERIALIZED (
  SELECT g.contract_id, g.venue, coalesce(g.sport, 'UNKNOWN') AS sport,
         coalesce(g.competition, '?') AS league, g.market_type,
         g.coverage_state, coalesce(g.coverage_why, '') AS why,
         g.settlement_state, g.event_start,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ 'TEAMTOTAL$' THEN 'TEAM_TOTAL'
                  WHEN g.competition ~ 'SPREAD$' THEN 'SPREAD'
                  WHEN g.competition ~ 'TOTAL$' THEN 'TOTAL'
                  WHEN g.competition ~ 'GAME$' THEN 'MONEYLINE'
                  ELSE 'KALSHI_SERIES_NOT_A_GAME_FAMILY' END
           WHEN g.market_type IS NULL AND (g.event_id ~ '^(btc|eth|sol)-'
                OR g.competition IN ('range', 'above')) THEN 'NON_SPORTS'
           WHEN g.market_type = 'futures' THEN 'OUTRIGHT'
           WHEN g.market_type ~ '(^|_)player_' THEN 'PLAYER_PROP'
           WHEN g.market_type ~ '(_winner(_[0-9]+)?$|^moneyline$)' THEN 'MONEYLINE'
           WHEN g.market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
           WHEN g.market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$'
             THEN CASE WHEN g.contract_id ~ '-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$' THEN 'TEAM_TOTAL'
                       ELSE 'TOTAL' END
           WHEN g.market_type IS NULL THEN 'NO_MARKET_TYPE'
           ELSE 'OTHER_PROP' END AS fam,
         CASE
           WHEN g.venue = 'KALSHI' THEN
             CASE WHEN g.competition ~ '(1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5)[A-Z]*$' THEN 'PERIOD'
                  ELSE 'FULL' END
           WHEN g.market_type ~ '(first|second|third|fourth)_(half|quarter|period)|_regulation_|_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|_game_total_kills|_rounds_handicap_[0-9]'
             THEN 'PERIOD'
           ELSE 'FULL' END AS seg,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued_24h
    FROM market_plane_registry g WHERE g.active),
t AS (
  SELECT c.*,
         CASE WHEN fam IN ('MONEYLINE', 'SPREAD', 'TOTAL', 'TEAM_TOTAL')
              THEN CASE WHEN seg = 'FULL' THEN 'TARGET_A' ELSE 'TARGET_B' END
              ELSE 'EXCLUDED' END AS tier,
         CASE WHEN coverage_state IS NULL THEN 'UNCLASSIFIED'
              WHEN coverage_state = 'EXTERNAL_DATA_UNAVAILABLE' THEN 'LOST_EXTERNAL'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP'
                   AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%' THEN 'LOST_MAPPING'
              WHEN coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' THEN 'LOST_SETTLEMENT'
              WHEN coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE' THEN 'LOST_FAIR_VALUE'
              WHEN coverage_state = 'CODE_CONTROLLED_GAP' THEN 'LOST_FRESH_BOOK'
              WHEN coverage_state = 'PRICEABLE' THEN 'PRICEABLE'
              ELSE 'UNCLASSIFIED' END AS stage,
         coalesce(event_start BETWEEN now() - interval '6 hours'
                                  AND now() + interval '48 hours', false) AS within_48h
    FROM c)
SELECT venue, fam, seg, count(*) AS n,
       count(*) FILTER (WHERE stage = 'PRICEABLE') AS priceable
  FROM t WHERE tier = 'EXCLUDED' GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;
\echo === W8. slug grammar: POLYMARKET_US event id first token x (futures / no type / other), with the token after it (top 60) ===
SELECT split_part(event_id, '-', 1) AS tok1,
       CASE WHEN market_type = 'futures' THEN 'futures'
            WHEN market_type IS NULL THEN 'no_type' ELSE 'typed' END AS kind,
       count(*) AS n, count(DISTINCT split_part(event_id, '-', 2)) AS tok2_values,
       min(split_part(event_id, '-', 2)) AS tok2_min,
       max(split_part(event_id, '-', 2)) AS tok2_max
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 60;
