-- ============================================================================
-- CAN THE MODEL LANE BE GRADED, AND WOULD GRADING IT MEASURE **OUR MODEL**?
-- (2026-09-10, read-only.)
--
-- Three questions, in the order they have to be answered:
--
--   Q1  Is engine_fills OUR software's own selections, or is it entangled with
--       the whale copying? The `whale_alignment` column is recorded and echoed
--       back (app.py:4004) but never read by any of our logic, and the engine
--       also runs a Kalshi whale-copy sleeve (app.py /api/engine/kalshi-claim).
--       So "is this our model or a whale copy" is a real question, not a
--       formality. Statement 1 counts how many rows carry an alignment payload
--       and shows what is inside it.
--
--   Q2  Are these SELECTIONS or EVALUATIONS? A bet list is bounded below by
--       whatever edge the engine required. An evaluation log is not. Statement
--       2 is the edge distribution -- if there is meaningful mass at negative
--       edge, no selection rule produced these rows and grading them all would
--       measure the wrong thing.
--
--   Q3  CAN it be graded at all? Grading needs outcome_id to reach a
--       settlement. Statement 3 tests every join route that exists and reports
--       the share of rows each one reaches; statement 4 shows the raw shapes so
--       a failed join can be diagnosed rather than guessed at.
--
-- If Q3 says the rows are reachable, statement 5 is the prize: it is not a
-- P&L, it is a CALIBRATION test. For rows we can settle, it compares the
-- model's fair_value against the market's own limit_price as predictors of the
-- actual result, by Brier score. That answers "is our devigged number better
-- than the market price" WITHOUT needing the engine's bet rule, without any
-- execution assumption, and without a single dollar. A model that cannot beat
-- the market price on its own evaluations has no edge to execute.
--
-- Read-only: five SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. IS IT OUR MODEL OR THE WHALE COPY? =='
SELECT count(*) AS rows,
       count(*) FILTER (WHERE whale_alignment IS NULL) AS alignment_null,
       count(*) FILTER (WHERE whale_alignment IS NOT NULL
                          AND whale_alignment::text IN ('{}', 'null')) AS alignment_empty,
       count(*) FILTER (WHERE whale_alignment IS NOT NULL
                          AND whale_alignment::text NOT IN ('{}', 'null')) AS alignment_populated,
       count(*) FILTER (WHERE venue = 'kalshi') AS on_kalshi,
       count(*) FILTER (WHERE venue = 'polymarket-us') AS on_pmus,
       left(string_agg(DISTINCT left(whale_alignment::text, 120), ' | ')
            FILTER (WHERE whale_alignment IS NOT NULL
                      AND whale_alignment::text NOT IN ('{}', 'null')), 600) AS sample_payloads
  FROM engine_fills;


\echo '== 2. SELECTIONS OR EVALUATIONS? the edge distribution =='
SELECT CASE WHEN edge IS NULL          THEN '0 no edge recorded'
            WHEN edge < -0.10          THEN '1 edge below -0.10'
            WHEN edge < -0.02          THEN '2 edge -0.10 to -0.02'
            WHEN edge <  0.00          THEN '3 edge -0.02 to 0'
            WHEN edge <  0.02          THEN '4 edge 0 to +0.02'
            WHEN edge <  0.05          THEN '5 edge +0.02 to +0.05'
            WHEN edge <  0.10          THEN '6 edge +0.05 to +0.10'
            ELSE                            '7 edge above +0.10' END AS edge_bucket,
       count(*) AS rows,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct,
       count(*) FILTER (WHERE would_fill) AS would_fill,
       round(avg(limit_price)::numeric, 4) AS avg_limit_px,
       round(avg(fair_value)::numeric, 4) AS avg_fair_value,
       round(sum(size_usd)::numeric, 0) AS notional
  FROM engine_fills
 GROUP BY 1 ORDER BY 1;


\echo '== 3. CAN IT BE GRADED? every join route, by share of rows reached =='
SELECT count(*) AS rows,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM market_tokens kt WHERE kt.token_id = ef.outcome_id)) AS outcome_is_token_id,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM market_tokens kt JOIN markets m ON m.condition_id = kt.condition_id
          WHERE kt.token_id = ef.outcome_id AND m.resolved
            AND jsonb_typeof(m.resolved_prices) = 'array')) AS token_and_resolved,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM markets m WHERE m.condition_id = ef.market_id)) AS market_is_condition_id,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM markets m WHERE m.condition_id = ef.market_id AND m.resolved
            AND jsonb_typeof(m.resolved_prices) = 'array')) AS condition_and_resolved,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM markets m WHERE m.slug = ef.market_id)) AS market_is_slug,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM us_premap u WHERE u.identifier = ef.market_id)) AS market_is_us_identifier
  FROM engine_fills ef;


\echo '== 4. THE RAW SHAPES, so a failed join is diagnosable =='
SELECT venue,
       count(*) AS rows,
       left(min(market_id), 60) AS market_id_min,
       left(max(market_id), 60) AS market_id_max,
       round(avg(length(market_id))::numeric, 1) AS avg_market_id_len,
       left(min(outcome_id), 70) AS outcome_id_min,
       left(max(outcome_id), 70) AS outcome_id_max,
       round(avg(length(outcome_id))::numeric, 1) AS avg_outcome_id_len,
       count(*) FILTER (WHERE outcome_id ~ '^[0-9]+$') AS outcome_all_digits
  FROM engine_fills
 GROUP BY 1 ORDER BY 2 DESC;


\echo '== 5. THE REAL TEST: does our fair_value beat the market price? (Brier) =='
-- Lower Brier is better. `market_brier` uses the price the engine would have
-- paid as the probability; `model_brier` uses our devigged fair_value. If the
-- model does not beat the market on its OWN evaluations there is no edge to
-- execute, whatever the bet rule was. Rows that cannot be settled are excluded
-- and counted, never defaulted.
WITH g AS (
  SELECT ef.venue, ef.league, ef.limit_price::float8 AS px, ef.fair_value::float8 AS fv,
         (SELECT max((m.resolved_prices ->> kt.outcome_index)::float8)
            FROM market_tokens kt JOIN markets m ON m.condition_id = kt.condition_id
           WHERE kt.token_id = ef.outcome_id AND m.resolved
             AND jsonb_typeof(m.resolved_prices) = 'array') AS payout
    FROM engine_fills ef
   WHERE ef.fair_value IS NOT NULL
)
SELECT COALESCE(venue, 'ALL') AS venue,
       COALESCE(league, 'ALL') AS league,
       count(*) AS evaluated,
       count(*) FILTER (WHERE payout IS NOT NULL) AS gradeable,
       round(avg(payout)::numeric, 4) AS base_rate,
       round(avg((px - payout) ^ 2)::numeric, 5) AS market_brier,
       round(avg((fv - payout) ^ 2)::numeric, 5) AS model_brier,
       round((avg((px - payout) ^ 2) - avg((fv - payout) ^ 2))::numeric, 5) AS model_minus_market,
       round((100.0 * (avg((px - payout) ^ 2) - avg((fv - payout) ^ 2))
              / NULLIF(avg((px - payout) ^ 2), 0))::numeric, 2) AS skill_pct
  FROM g
 WHERE payout IS NOT NULL
 GROUP BY ROLLUP (venue, league)
 ORDER BY count(*) DESC
 LIMIT 40;
