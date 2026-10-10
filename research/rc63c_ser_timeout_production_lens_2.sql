-- READ-ONLY. RC6.3c settlement_exception_risk 2.0 s bound, production lens part 2:
-- WHY the PAPER_SQL lateral costs ~60,000 rows per settlement row on production
-- (the backward primary-key scan walks from the newest valuation id down to the
-- newest valuation of each settled position's slug), how fast that distance grows
-- (external_valuations rows per day), and the decision denominators (paper_decisions
-- by day and verdict) beside the canonical intents that carry the component.

\echo == G1 external_valuations rows per day, last 14 d (growth of the scan distance)
SELECT decided_at::date AS day, count(*) AS rows_added, min(id) AS min_id, max(id) AS max_id
  FROM external_valuations
 WHERE decided_at >= now() - interval '14 days'
 GROUP BY 1
 ORDER BY 1;

\echo == G2 for each latest-version settlement row: rows the backward PK scan must pass before the newest valuation of its slug (max(id) - newest matching id)
WITH s AS (SELECT DISTINCT ON (position_key) position_key, us_market_slug, recorded_at
             FROM paper_settlements ORDER BY position_key, version DESC),
     m AS (SELECT max(id) AS max_id FROM external_valuations),
     d AS (SELECT s.position_key, s.us_market_slug, s.recorded_at,
                  (SELECT max(x.id) FROM external_valuations x WHERE x.us_market_slug = s.us_market_slug) AS newest_match_id
             FROM s)
SELECT count(*) AS rows,
       count(*) FILTER (WHERE newest_match_id IS NULL) AS no_match,
       min(m.max_id - newest_match_id) AS min_distance,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY m.max_id - newest_match_id) AS p50_distance,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY m.max_id - newest_match_id) AS p90_distance,
       max(m.max_id - newest_match_id) AS max_distance,
       min(recorded_at) AS oldest_settlement, max(recorded_at) AS newest_settlement,
       (SELECT max_id FROM m) AS max_id_now
  FROM d, m;

\echo == G3 paper_decisions by day and verdict, last 7 d (are decisions still being made while no intents are written?)
SELECT decided_at::date AS day, verdict, count(*) AS decisions,
       count(DISTINCT strategy) AS strategies
  FROM paper_decisions
 WHERE decided_at >= now() - interval '7 days'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo == G4 canonical intents by day, last 7 d, beside the ENTER decisions
SELECT d.day, d.enter_decisions, coalesce(i.intents, 0) AS intents
  FROM (SELECT decided_at::date AS day, count(*) AS enter_decisions
          FROM paper_decisions
         WHERE decided_at >= now() - interval '7 days' AND verdict = 'ENTER'
         GROUP BY 1) d
  LEFT JOIN (SELECT created_at::date AS day, count(*) AS intents
               FROM canonical_decision_intents
              WHERE created_at >= now() - interval '7 days'
              GROUP BY 1) i USING (day)
 ORDER BY 1;

\echo == end
