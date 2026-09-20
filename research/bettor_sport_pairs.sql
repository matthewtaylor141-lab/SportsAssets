-- THE DISTINCT (sportsMarketType, team_league) PAIRS ON BETTOR'S OWN
-- OBSERVATIONS, WITH COUNTS.
--
-- Why pairs and not the mapped answer: the mapping lives in
-- backend/sportsassets/bettor_sport_mapping.py and must have exactly
-- ONE implementation. Re-deriving it in SQL would give two, and two
-- implementations of the same rule eventually disagree -- at which
-- point the prettier number wins, which is the failure this whole
-- programme is built to avoid.
--
-- So this returns the venue's raw inputs and their counts, and the
-- Python classifier produces the §1 report from them.
--
-- DISTINCT on the opportunity id throughout: us_premap carries one row
-- per market SIDE, so a naive join doubles every count.
--
-- READ-ONLY.

SELECT 'SPORT_PAIRS' AS section,
       coalesce(p.sports_type, '')             AS sports_type,
       coalesce(p.team_league, '')             AS team_league,
       count(DISTINCT o.bettor_opportunity_id) AS observations
  FROM bettor_opportunities o
  LEFT JOIN us_premap p ON p.market_slug = o.symbol
 GROUP BY 1, 2, 3
 ORDER BY observations DESC
 LIMIT 400;

-- The denominator, so the report's rate is over the true total rather
-- than over whatever the LIMIT above returned.
SELECT 'SPORT_PAIRS_TOTAL' AS section,
       count(DISTINCT o.bettor_opportunity_id)      AS observations,
       count(DISTINCT (coalesce(p.sports_type, '')
                    || '|' || coalesce(p.team_league, '')))
                                                    AS distinct_pairs
  FROM bettor_opportunities o
  LEFT JOIN us_premap p ON p.market_slug = o.symbol;

-- ── the observation rate, stated once and precisely ──────────────────
SELECT 'OBSERVATION_RATE_SUMMARY' AS section,
       count(*)                                          AS observations,
       min(o.observed_at)                                AS first_at,
       max(o.observed_at)                                AS last_at,
       round(EXTRACT(EPOCH FROM (max(o.observed_at)
                               - min(o.observed_at))) / 3600.0, 4)
                                                         AS span_hours,
       round(count(*) / nullif(EXTRACT(EPOCH FROM (max(o.observed_at)
                               - min(o.observed_at))) / 3600.0, 0), 1)
                                                         AS per_hour_all,
       count(*) FILTER (WHERE o.observed_at > now() - interval '6 hours')
                                                         AS last_6h,
       round(count(*) FILTER (WHERE o.observed_at > now()
                                - interval '6 hours') / 6.0, 1)
                                                         AS per_hour_recent
  FROM bettor_opportunities o;

-- ── the eligible rate, which is the rate that would matter ───────────
-- A hypothetical maker order can only be defined against a two-sided
-- book, so this is the true input rate to a P_FILL builder.
SELECT 'ELIGIBLE_RATE' AS section,
       count(*) FILTER (WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL)
                                                        AS eligible,
       count(*)                                         AS observations,
       round(count(*) FILTER (WHERE s.bid IS NOT NULL
                                AND s.ask IS NOT NULL)::numeric
             / nullif(count(*), 0), 4)                  AS eligible_rate,
       count(*) FILTER (WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL
                          AND o.observed_at > now() - interval '6 hours')
                                                        AS eligible_last_6h,
       round(count(*) FILTER (WHERE s.bid IS NOT NULL
                                AND s.ask IS NOT NULL
                                AND o.observed_at > now()
                                  - interval '6 hours') / 6.0, 1)
                                                        AS eligible_per_hour
  FROM bettor_opportunities o
  LEFT JOIN shadow_market_states s ON s.market_state_id = o.market_state_id;

-- ── how many distinct markets get re-observed, and how often ─────────
-- A P_FILL label needs a T0 snapshot AND a later look at the same
-- market. A market observed once can never be labelled, so the
-- re-observation rate bounds the labelling rate from above.
SELECT 'REOBSERVATION' AS section,
       count(*)                                          AS markets,
       count(*) FILTER (WHERE n = 1)                     AS observed_once,
       count(*) FILTER (WHERE n >= 2)                    AS observed_twice_plus,
       round(avg(n), 2)                                  AS mean_observations,
       max(n)                                            AS max_observations,
       round(avg(span_s) FILTER (WHERE n >= 2))          AS mean_span_s
  FROM (SELECT o.symbol,
               count(*) AS n,
               EXTRACT(EPOCH FROM (max(o.observed_at)
                                 - min(o.observed_at))) AS span_s
          FROM bettor_opportunities o
         GROUP BY o.symbol) t;
