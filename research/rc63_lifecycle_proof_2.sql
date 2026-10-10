-- RC6.3 lifecycle-proof lane, readback 2 (SELECT only).
-- E1: every paper position of the three quarantined strategies on
--     paper_acct_main, in the exact POSITIONS_SQL shape
--     (bettor_paper_ledger.positions), one JSON array per row, so the
--     release-tree code (_position_from -> bettor_strategy_lifecycle.metrics /
--     rules_firing / check_manual) can be run on it offline.
-- F: Allie at the decision, per intent, with the size of the
--     settlement-lag read input at that instant; decisions in the last 24 h.

\echo == E0 row count
SELECT count(*) AS positions FROM (
    SELECT 1 FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side) x;

\echo == E1 positions (json: group_id, slug, side, fixture, strategy, bought, buy_gross, buy_fees, sold, sale_gross, sale_fees, first_fill_epoch, last_fill_epoch, settled_qty, payout_usd, settled_outcome, settlement_version, settled_at_epoch)
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction='BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction='BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction='SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction='SELL') AS sale_fees,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd,
           outcome, version, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC)
SELECT json_build_array(f.group_id, f.us_market_slug, f.holding_side, f.fixture, f.strategy,
         f.bought::text, f.buy_gross::text, f.buy_fees::text, f.sold::text,
         f.sale_gross::text, f.sale_fees::text,
         extract(epoch FROM f.first_fill_at)::float8, extract(epoch FROM f.last_fill_at)::float8,
         s.qty::text, s.payout_usd::text, s.outcome, s.version,
         extract(epoch FROM s.settled_at)::float8)::text AS row_json
  FROM f LEFT JOIN s ON s.position_key =
       'paperpos:paper_acct_main:' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side
 ORDER BY f.first_fill_at, f.group_id, f.us_market_slug, f.holding_side;

\echo == E2 settlement corrections (versions above 1) on paper_acct_main
SELECT position_key, version, outcome, payout_usd, settled_at, recorded_at
  FROM paper_settlements WHERE account_id = 'paper_acct_main' AND version > 1
 ORDER BY recorded_at;

\echo == F1 every canonical intent (7 d): Allie and the other components, latency, and the settled-market count the lag read scanned us_premap for
SELECT i.created_at, split_part(i.decision_id, ':', 1) AS kind, i.strategy,
       i.allie->>'status' AS allie, left(coalesce(i.allie->>'why', ''), 40) AS allie_why,
       i.opportunity_score->>'status' AS opp, left(coalesce(i.opportunity_score->>'why', ''), 40) AS opp_why,
       i.eddie->>'status' AS eddie, i.karen->>'state' AS karen,
       round(extract(epoch FROM i.recorded_at - i.created_at)::numeric, 3) AS latency_s,
       (SELECT count(DISTINCT s.us_market_slug) FROM paper_settlements s
         WHERE s.outcome IN ('WON', 'LOST') AND s.recorded_at <= i.created_at
           AND s.settled_at >= i.created_at - interval '180 days') AS lag_markets_at_decision
  FROM canonical_decision_intents i
 WHERE i.created_at >= now() - interval '7 days'
 ORDER BY i.created_at;

\echo == F2 Allie UNAVAILABLE reasons, all time
SELECT allie->>'status' AS allie, coalesce(allie->>'why', '') AS why, count(*) AS n,
       min(created_at) AS first, max(created_at) AS last
  FROM canonical_decision_intents GROUP BY 1, 2 ORDER BY 1, 2;

\echo == F3 paper decisions in the last 24 h by decision kind, verdict and refusal (no ENTER means no canonical intent, so no Allie component)
SELECT split_part(decision_id, ':', 1) AS kind, verdict, coalesce(refusal, '') AS refusal, count(*) AS n,
       max(decided_at) AS last
  FROM paper_decisions WHERE account_id = 'paper_acct_main' AND decided_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;
