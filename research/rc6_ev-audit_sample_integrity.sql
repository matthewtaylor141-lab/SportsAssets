-- READ-ONLY. RC6 lane ev-audit: SAMPLE INTEGRITY OF THE PROBABILITY EVIDENCE
-- (completion.evidence PROB_SQL, 30-day window) and SETTLEMENT VERSIONS.
-- Does the label CTE read superseded settlement versions? Are samples
-- duplicated (the same market's two sides, or one market-side under several
-- strategies)? SELECT only.

\echo == 1 paper_settlements by outcome and version
SELECT outcome, version, count(*) n, min(settled_at) first_at, max(settled_at) last_at
  FROM paper_settlements GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 2 position settlements with more than one version (the chain, oldest first)
SELECT position_key, settlement_event_key,
       string_agg(outcome || '@v' || version || ':' || payout_per_contract, ' -> ' ORDER BY version) chain,
       max(settled_at) last_at
  FROM paper_settlements
 GROUP BY 1, 2 HAVING count(*) > 1
 ORDER BY last_at DESC LIMIT 60;

\echo == 3 markets whose WON/LOST label rows disagree (dropped by HAVING min(y)=max(y)) or whose latest version is not WON/LOST
WITH v AS (
  SELECT DISTINCT ON (position_key, settlement_event_key)
         position_key, settlement_event_key, us_market_slug, holding_side,
         outcome latest_outcome, version latest_version
    FROM paper_settlements
   ORDER BY position_key, settlement_event_key, version DESC),
stale AS (
  SELECT s.us_market_slug, s.position_key, s.outcome old_outcome, s.version,
         v.latest_outcome, v.latest_version
    FROM paper_settlements s
    JOIN v ON v.position_key = s.position_key
          AND v.settlement_event_key = s.settlement_event_key
   WHERE s.version < v.latest_version AND s.outcome IN ('WON', 'LOST'))
SELECT old_outcome, latest_outcome, count(*) n,
       count(DISTINCT us_market_slug) markets
  FROM stale GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 4 PROB_SQL sample (first settled ENTER per strategy x market x side, 30 d): rows, distinct market-sides, distinct markets, two-sided markets
WITH d0 AS (
  SELECT DISTINCT ON (d.strategy, d.us_market_slug, d.holding_side)
         d.decision_id, d.decided_at, d.strategy, d.us_market_slug slug,
         d.holding_side side, d.p_blended, (d.economics->>'probability')::float8 p_used,
         d.book_obs_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - interval '30 days'
   ORDER BY d.strategy, d.us_market_slug, d.holding_side, d.decided_at),
lab AS (
  SELECT us_market_slug slug FROM paper_settlements WHERE outcome IN ('WON','LOST')
  UNION SELECT us_market_slug FROM external_valuations WHERE outcome_known AND outcome IN (0,1)),
k AS (SELECT d0.* FROM d0 WHERE slug IN (SELECT slug FROM lab))
SELECT count(*) rows_, count(DISTINCT slug || '|' || side) market_sides,
       count(DISTINCT slug) markets,
       (SELECT count(*) FROM (SELECT slug FROM k GROUP BY slug
                               HAVING count(DISTINCT side) > 1) x) markets_with_both_sides,
       (SELECT count(*) FROM (SELECT slug, side FROM k GROUP BY slug, side
                               HAVING count(DISTINCT strategy) > 1) x) market_sides_under_2plus_strategies,
       (SELECT count(*) FROM (SELECT slug, side FROM k GROUP BY slug, side
                               HAVING count(DISTINCT strategy) > 1
                                  AND max(coalesce(p_used, p_blended)) - min(coalesce(p_used, p_blended)) < 1e-9) x)
           market_sides_identical_p_across_strategies
  FROM k;

\echo == 5 the same, by strategy
WITH d0 AS (
  SELECT DISTINCT ON (d.strategy, d.us_market_slug, d.holding_side)
         d.strategy, d.us_market_slug slug, d.holding_side side
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - interval '30 days'
   ORDER BY d.strategy, d.us_market_slug, d.holding_side, d.decided_at),
lab AS (
  SELECT us_market_slug slug FROM paper_settlements WHERE outcome IN ('WON','LOST')
  UNION SELECT us_market_slug FROM external_valuations WHERE outcome_known AND outcome IN (0,1))
SELECT strategy, side, count(*) n FROM d0 WHERE slug IN (SELECT slug FROM lab)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 6 orientation of p_blended vs economics.probability on SHORT decisions (is p_blended the held side?)
SELECT holding_side, count(*) n,
       round(avg(abs(p_blended - (economics->>'probability')::float8))::numeric, 4) mean_abs_diff_same,
       round(avg(abs((1 - p_blended) - (economics->>'probability')::float8))::numeric, 4) mean_abs_diff_flipped
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at > now() - interval '30 days'
   AND p_blended IS NOT NULL AND economics ? 'probability'
 GROUP BY 1 ORDER BY 1;

\echo == 7 decisions scored with p_blended only (no economics.probability) by side
SELECT holding_side, coalesce(strategy, '-') strategy, count(*) n
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at > now() - interval '30 days'
   AND p_blended IS NOT NULL AND NOT (economics ? 'probability')
 GROUP BY 1, 2 ORDER BY 1, 2;
