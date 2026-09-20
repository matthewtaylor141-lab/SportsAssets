-- THE VENUE'S OWN SPORT/LEAGUE VOCABULARY, ENUMERATED.
--
-- Owner directive §1: "Build the canonical mapping from the actual
-- venue payload into the observation schema... Do not simply patch
-- UNKNOWN with an inferred string."
--
-- A mapping table written from memory is an inferred string with extra
-- steps. This enumerates every distinct value the venue has actually
-- stated so the table is grounded in observation, and so a value the
-- table does not cover is a KNOWN gap rather than a surprise.
--
-- READ-ONLY.

-- ── 1. EVERY DISTINCT sportsMarketType, WITH ITS LEADING TOKENS ──────
-- market.sportsMarketType is present on 100% of us_premap rows. The
-- sport is its leading token, but NOT simply split_part(x,'_',1):
-- 'table_tennis_match_winner' would read 'table'. Both the 1-token and
-- 2-token prefixes are listed so the declared prefix set can be
-- longest-match and grounded.
SELECT 'SPORTS_TYPE_PREFIXES' AS section,
       split_part(p.sports_type, '_', 1)                      AS token1,
       split_part(p.sports_type, '_', 1) || '_'
         || split_part(p.sports_type, '_', 2)                 AS token2,
       count(*)                                               AS rows,
       count(DISTINCT p.sports_type)                          AS distinct_types,
       count(DISTINCT p.team_league)                          AS distinct_leagues,
       min(p.sports_type)                                     AS example
  FROM us_premap p
 WHERE p.sports_type IS NOT NULL
 GROUP BY 1, 2, 3
 ORDER BY rows DESC
 LIMIT 60;

-- ── 2. EVERY DISTINCT team_league, AND WHAT SPORT ITS MARKETS NAME ───
-- The cross-tab is the evidence for a LEAGUE -> SPORT entry: a league
-- whose markets all carry one sport prefix is stated by the venue, not
-- inferred by us. A league spanning several prefixes is a gap.
SELECT 'LEAGUE_TO_SPORT_EVIDENCE' AS section,
       p.team_league,
       count(*)                                               AS rows,
       count(DISTINCT split_part(p.sports_type, '_', 1))      AS distinct_token1,
       string_agg(DISTINCT split_part(p.sports_type, '_', 1), ',')
                                                              AS tokens_seen
  FROM us_premap p
 WHERE p.team_league IS NOT NULL
 GROUP BY 1, 2
 ORDER BY rows DESC
 LIMIT 120;

-- ── 3. THE ROWS WITH NO LEAGUE: what do they carry instead ───────────
-- 22.7% of joined premap rows have team_league NULL. If their
-- sports_type still names a sport, the SPORT is identifiable even where
-- the LEAGUE is not -- which are different facts and must not be
-- collapsed.
SELECT 'NO_LEAGUE_ROWS' AS section,
       split_part(p.sports_type, '_', 1)                  AS token1,
       count(*)                                           AS rows,
       count(*) FILTER (WHERE p.sports_type = 'futures')  AS futures_rows,
       min(p.market_slug)                                 AS example_slug
  FROM us_premap p
 WHERE p.team_league IS NULL
   AND p.sports_type IS NOT NULL
 GROUP BY 1, 2
 ORDER BY rows DESC
 LIMIT 40;

-- ── 4. WHAT THE BETTOR OBSERVATIONS THEMSELVES WOULD MAP TO ──────────
-- The decisive §1 number: of the 9,702 observations, how many would
-- acquire a sport and a league from data ALREADY STORED. DISTINCT on
-- the opportunity id because us_premap carries one row per market SIDE
-- and the join fans out.
SELECT 'OBSERVATIONS_MAPPABLE' AS section,
       count(DISTINCT o.bettor_opportunity_id)                  AS observations,
       count(DISTINCT o.bettor_opportunity_id)
         FILTER (WHERE p.sports_type IS NOT NULL
                   AND p.sports_type <> 'futures')              AS sport_token_present,
       count(DISTINCT o.bettor_opportunity_id)
         FILTER (WHERE p.sports_type = 'futures')               AS futures_no_sport,
       count(DISTINCT o.bettor_opportunity_id)
         FILTER (WHERE p.team_league IS NOT NULL)                AS league_present,
       count(DISTINCT o.bettor_opportunity_id)
         FILTER (WHERE p.sports_type IS NULL)                    AS no_sports_type
  FROM bettor_opportunities o
  LEFT JOIN us_premap p ON p.market_slug = o.symbol;

-- ── 5. THE SPORTS AND LEAGUES THE OBSERVED SET WOULD REPORT ──────────
SELECT 'OBSERVED_SPORTS_AND_LEAGUES' AS section,
       split_part(p.sports_type, '_', 1)          AS token1,
       coalesce(p.team_league, '(no league)')     AS team_league,
       count(DISTINCT o.bettor_opportunity_id)    AS observations
  FROM bettor_opportunities o
  JOIN us_premap p ON p.market_slug = o.symbol
 GROUP BY 1, 2, 3
 ORDER BY observations DESC
 LIMIT 60;

-- ── 6. DOES THE VENUE BOOK FEED CARRY SIZE? (the depth question) ─────
-- shadow_market_states.available_depth is NULL on every row. This says
-- whether that is the venue's limit or our read path's: l2_reference
-- records which feed answered and what it declared about depth.
SELECT 'DEPTH_SOURCE' AS section,
       m.l2_reference ->> 'feed'   AS feed,
       m.l2_reference ->> 'depth'  AS depth_declared,
       count(*)                                          AS rows,
       count(*) FILTER (WHERE m.available_depth IS NOT NULL)
           AS depth_present,
       count(*) FILTER (WHERE m.readable IS TRUE)         AS readable
  FROM shadow_market_states m
 WHERE m.evidence_source IS NOT NULL
 GROUP BY 1, 2, 3
 ORDER BY rows DESC
 LIMIT 20;
