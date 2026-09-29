-- READ-ONLY. EVIDENCE SURVEY FOR THE OPERATIONAL DEPENDENCIES OF aca3564.
--
--   C  the calibration cohort: every external valuation ever written, by
--      source version, eligibility and whether its outcome has been joined;
--   U  the venue identity catalogue (us_premap): size, freshness, kinds, and
--      how many events carry two or more contracts (a pair can be observed
--      only on those).
--
-- No balance, cash, equity or credential is selected.

\echo '== C1 · valuations all time: by version, eligibility and outcome join =='
SELECT version, coalesce(eligibility, '(null)') AS eligibility,
       count(*)                                        AS rows,
       count(*) FILTER (WHERE outcome_known)           AS outcome_known,
       count(DISTINCT event_key)                       AS events,
       count(DISTINCT event_key) FILTER (WHERE outcome_known) AS events_known,
       min(decided_at)                                 AS first,
       max(decided_at)                                 AS last
  FROM external_valuations
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '== C2 · valuations by sport family and outcome basis (outcome-joined only) =='
SELECT sport_family, coalesce(outcome_basis, '(null)') AS outcome_basis,
       count(*) AS rows, count(DISTINCT event_key) AS events
  FROM external_valuations
 WHERE outcome_known
 GROUP BY 1, 2
 ORDER BY 3 DESC
 LIMIT 20;

\echo '== C3 · valuations by day (last 30 days) =='
SELECT date_trunc('day', decided_at)::date AS day, count(*) AS rows,
       count(*) FILTER (WHERE admissible) AS admissible,
       count(*) FILTER (WHERE outcome_known) AS outcome_known
  FROM external_valuations
 WHERE decided_at > now() - interval '30 days'
 GROUP BY 1
 ORDER BY 1 DESC;

\echo '== U1 · us_premap: size, freshness, kinds =='
SELECT coalesce(kind, '(null)') AS kind, count(*) AS rows,
       count(DISTINCT event_slug) AS events,
       min(updated_at) AS oldest, max(updated_at) AS newest
  FROM us_premap
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 20;

\echo '== U2 · us_premap events carrying 2+ distinct contracts, by slug prefix =='
SELECT split_part(market_slug, '-', 1) || '-' || split_part(market_slug, '-', 2)
                                         AS slug_family,
       count(*) AS events_with_2plus
  FROM (SELECT event_slug, min(market_slug) AS market_slug,
               count(DISTINCT market_slug) AS contracts
          FROM us_premap
         WHERE event_slug IS NOT NULL
         GROUP BY event_slug) e
 WHERE contracts >= 2
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 20;

\echo '== U3 · the 15 most recently updated premap events: contracts and kinds =='
SELECT event_slug, count(DISTINCT market_slug) AS contracts,
       string_agg(DISTINCT coalesce(kind, '?'), ',') AS kinds,
       string_agg(DISTINCT coalesce(intent, '?'), ',') AS intents,
       max(updated_at) AS updated
  FROM us_premap
 WHERE event_slug IS NOT NULL
 GROUP BY event_slug
 ORDER BY max(updated_at) DESC
 LIMIT 15;
