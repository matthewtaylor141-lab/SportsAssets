-- READ-ONLY. RC6.2 lane p-evcontrols (REWORK stage 2). SELECT / EXPLAIN only.
-- What intel.attribution.load_paper (read once by
-- redteam.controls.attributed_positions for ATTRIBUTION, PROFIT_BREAKERS, the
-- MULTIPLE_TESTING study and the forward scoreboard) costs per POSITION, so
-- its 5,000-position bound can become a measured safety stop instead of a
-- cliff on a population that only grows (the study's population is every
-- position; the scoreboard's is every position since 2026-10-07).
-- The reads are reproduced as backend/sportsassets/intel/attribution.py
-- (e4995085) and redteam/controls.py run them, with the bound parameter
-- arrays written as ARRAY(SELECT ...) over today's WHOLE population (one
-- page holding every position: the worst case today):
--  A1  the population: lifetime, last 60 days, since 2026-10-07 (cohort)
--  A1b positions per UTC day and per strategy (the growth rate)
--  A2  per-position shape: fills, settlement versions, Xavier reviews with
--      an action, valuations
--  A3  EXPLAIN ANALYZE of PAPER_DECISIONS_SQL unwindowed, LIMIT 20001
--  A4  EXPLAIN ANALYZE of each per-position fetch over every position:
--      entry_groups, fills, latest_settlements, Xavier review counts,
--      valuations_by_id, and attributed_positions' fixture read
--  A5  bytes each fetch returns per position

\echo == A0 read instant
SELECT now() AS read_at, extract(epoch FROM now()) AS read_epoch;

\echo == A1 positions (ENTER decisions with an ENTRY paper order), paper_acct_main
SELECT count(*) AS lifetime_positions,
       count(*) FILTER (WHERE d.decided_at >= now() - interval '60 days')
         AS positions_last_60_days,
       count(*) FILTER (WHERE d.decided_at >= timestamptz '2026-10-07 00:00:00+00')
         AS positions_since_cohort_start,
       min(d.decided_at) AS oldest, max(d.decided_at) AS newest
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
   AND EXISTS (SELECT 1 FROM paper_orders o
                WHERE o.decision_id = d.decision_id AND o.role = 'ENTRY');

\echo == A1b positions per UTC day x strategy
SELECT (d.decided_at AT TIME ZONE 'UTC')::date AS day, d.strategy,
       count(*) AS positions
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
   AND EXISTS (SELECT 1 FROM paper_orders o
                WHERE o.decision_id = d.decision_id AND o.role = 'ENTRY')
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == A2 per-position shape over every position
WITH p AS (
  SELECT d.decision_id, d.valuation_id,
         (SELECT o.group_id FROM paper_orders o
           WHERE o.decision_id = d.decision_id AND o.role = 'ENTRY'
           ORDER BY o.created_at LIMIT 1) AS group_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
     AND EXISTS (SELECT 1 FROM paper_orders o
                  WHERE o.decision_id = d.decision_id AND o.role = 'ENTRY')),
s AS (
  SELECT p.decision_id,
         (SELECT count(*) FROM paper_fills f WHERE f.group_id = p.group_id)
           AS fills,
         (SELECT count(*) FROM paper_settlements t
           WHERE t.group_id = p.group_id) AS settlement_rows,
         (SELECT count(*) FROM paper_xavier_reviews x
           WHERE x.group_id = p.group_id) AS reviews_all,
         (SELECT count(*) FROM paper_xavier_reviews x
           WHERE x.group_id = p.group_id AND x.action IS NOT NULL)
           AS reviews_with_action,
         (p.valuation_id IS NOT NULL) AS has_valuation
    FROM p)
SELECT count(*) AS positions,
       avg(fills) AS fills_avg, max(fills) AS fills_max, sum(fills) AS fills_total,
       avg(settlement_rows) AS sett_avg, sum(settlement_rows) AS sett_total,
       avg(reviews_all) AS reviews_avg,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY reviews_all) AS reviews_p95,
       max(reviews_all) AS reviews_max, sum(reviews_all) AS reviews_total,
       sum(reviews_with_action) AS reviews_with_action_total,
       count(*) FILTER (WHERE has_valuation) AS with_valuation
  FROM s;

\echo == A3 EXPLAIN ANALYZE PAPER_DECISIONS_SQL unwindowed ($1 NULL, $2 paper_acct_main, LIMIT 20001)
EXPLAIN (ANALYZE, BUFFERS)
    SELECT d.decision_id, d.decided_at, d.valuation_id, d.us_market_slug,
           d.holding_side, d.p_pinnacle, d.p_blended, d.p_internal,
           d.limit_price, d.economics, d.strategy
      FROM paper_decisions d
     WHERE d.verdict = 'ENTER'
       AND (NULL::float8 IS NULL OR d.decided_at >= to_timestamp(NULL::float8))
       AND ('paper_acct_main'::text IS NULL OR d.account_id = 'paper_acct_main')
       AND EXISTS (SELECT 1 FROM paper_orders o
                    WHERE o.decision_id = d.decision_id
                      AND o.role = 'ENTRY')
     ORDER BY d.decided_at DESC, d.decision_id DESC LIMIT 20001;

\echo == A4a EXPLAIN ANALYZE entry_groups over every position
EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT ON (decision_id) decision_id, group_id
  FROM paper_orders
 WHERE decision_id = ANY(ARRAY(
         SELECT d.decision_id FROM paper_decisions d
          WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
            AND EXISTS (SELECT 1 FROM paper_orders o
                         WHERE o.decision_id = d.decision_id
                           AND o.role = 'ENTRY')))
   AND role = 'ENTRY' ORDER BY decision_id, created_at;

\echo == A4b EXPLAIN ANALYZE the fills read over every position group
EXPLAIN (ANALYZE, BUFFERS)
SELECT group_id, role, direction, holding_side, us_market_slug,
       qty, price, fee_usd, gross_usd FROM paper_fills
 WHERE group_id = ANY(ARRAY(
         SELECT DISTINCT o.group_id FROM paper_orders o
           JOIN paper_decisions d ON d.decision_id = o.decision_id
          WHERE o.role = 'ENTRY' AND d.verdict = 'ENTER'
            AND d.account_id = 'paper_acct_main'));

\echo == A4c EXPLAIN ANALYZE latest_settlements over every position group
EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT ON (position_key) position_key, group_id,
       us_market_slug, holding_side, qty, outcome,
       payout_per_contract, payout_usd, evidence_source,
       extract(epoch FROM settled_at)::float8 AS settled_at
  FROM paper_settlements
 WHERE group_id = ANY(ARRAY(
         SELECT DISTINCT o.group_id FROM paper_orders o
           JOIN paper_decisions d ON d.decision_id = o.decision_id
          WHERE o.role = 'ENTRY' AND d.verdict = 'ENTER'
            AND d.account_id = 'paper_acct_main'))
 ORDER BY position_key, version DESC;

\echo == A4d EXPLAIN ANALYZE Xavier review counts over every position group
EXPLAIN (ANALYZE, BUFFERS)
SELECT group_id, count(*) AS n FROM paper_xavier_reviews
 WHERE group_id = ANY(ARRAY(
         SELECT DISTINCT o.group_id FROM paper_orders o
           JOIN paper_decisions d ON d.decision_id = o.decision_id
          WHERE o.role = 'ENTRY' AND d.verdict = 'ENTER'
            AND d.account_id = 'paper_acct_main'))
   AND action IS NOT NULL
 GROUP BY group_id;

\echo == A4e EXPLAIN ANALYZE valuations_by_id over every position
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, version, devig_method, sport_family, market, event_key,
       probability, outcome_known, outcome, outcome_basis,
       buy_intent, settlement_rule, us_market_slug
  FROM external_valuations
 WHERE id = ANY(ARRAY(
         SELECT DISTINCT d.valuation_id FROM paper_decisions d
          WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
            AND d.valuation_id IS NOT NULL
            AND EXISTS (SELECT 1 FROM paper_orders o
                         WHERE o.decision_id = d.decision_id
                           AND o.role = 'ENTRY')));

\echo == A4f EXPLAIN ANALYZE attributed_positions fixture read over every group
EXPLAIN (ANALYZE, BUFFERS)
SELECT group_id, max(fixture) fixture FROM paper_fills
 WHERE group_id = ANY(ARRAY(
         SELECT DISTINCT o.group_id FROM paper_orders o
           JOIN paper_decisions d ON d.decision_id = o.decision_id
          WHERE o.role = 'ENTRY' AND d.verdict = 'ENTER'
            AND d.account_id = 'paper_acct_main'))
 GROUP BY 1;

\echo == A4g the array initplans alone (subtract from A4a..A4f)
EXPLAIN (ANALYZE, BUFFERS)
SELECT cardinality(ARRAY(
         SELECT DISTINCT o.group_id FROM paper_orders o
           JOIN paper_decisions d ON d.decision_id = o.decision_id
          WHERE o.role = 'ENTRY' AND d.verdict = 'ENTER'
            AND d.account_id = 'paper_acct_main')) AS groups,
       cardinality(ARRAY(
         SELECT d.decision_id FROM paper_decisions d
          WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
            AND EXISTS (SELECT 1 FROM paper_orders o
                         WHERE o.decision_id = d.decision_id
                           AND o.role = 'ENTRY'))) AS decisions;

\echo == A5 bytes each fetch returns, per position (text length of the rows)
WITH pos AS (
  SELECT d.decision_id, d.decided_at, d.valuation_id, d.us_market_slug,
         d.holding_side, d.p_pinnacle, d.p_blended, d.p_internal,
         d.limit_price, d.economics, d.strategy
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
     AND EXISTS (SELECT 1 FROM paper_orders o
                  WHERE o.decision_id = d.decision_id AND o.role = 'ENTRY')),
g AS (SELECT DISTINCT o.group_id FROM paper_orders o
        JOIN pos ON pos.decision_id = o.decision_id WHERE o.role = 'ENTRY')
SELECT (SELECT count(*) FROM pos) AS positions,
       (SELECT sum(length(pos::text)) FROM pos) AS decision_bytes,
       (SELECT round(avg(length(pos::text))) FROM pos) AS decision_bytes_avg,
       (SELECT max(length(pos::text)) FROM pos) AS decision_bytes_max,
       (SELECT sum(length(f::text)) FROM (
          SELECT group_id, role, direction, holding_side, us_market_slug,
                 qty, price, fee_usd, gross_usd FROM paper_fills
           WHERE group_id IN (SELECT group_id FROM g)) f) AS fill_bytes,
       (SELECT sum(length(s::text)) FROM (
          SELECT position_key, group_id, us_market_slug, holding_side, qty,
                 outcome, payout_per_contract, payout_usd, evidence_source,
                 settled_at FROM paper_settlements
           WHERE group_id IN (SELECT group_id FROM g)) s) AS settlement_bytes,
       (SELECT sum(length(v::text)) FROM (
          SELECT id, version, devig_method, sport_family, market, event_key,
                 probability, outcome_known, outcome, outcome_basis,
                 buy_intent, settlement_rule, us_market_slug
            FROM external_valuations
           WHERE id IN (SELECT valuation_id FROM pos)) v) AS valuation_bytes;
