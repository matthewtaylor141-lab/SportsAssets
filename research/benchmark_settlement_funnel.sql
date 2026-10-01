-- READ-ONLY. THE PINNACLE_ONLY_PAPER_BENCHMARK FUNNEL AFTER THE SETTLEMENT
-- CORRECTION: which valuations pass the settlement check, and how many of
-- those then pass freshness, a current book, the 5 pp edge, positive EV after
-- fees, an order and a simulated fill. Nothing here writes.
--
-- $since is the deploy instant, hard-coded below as the first valuation
-- carrying the new `blockers` key (rows written by the corrected collector).
--
-- F0 serving evidence: the first row with recorded blockers; migration 183
-- F1 settlement on new valuations: verdict x established x sport, counts
-- F2 the PRIMARY blocker on new valuations (first recorded), counts
-- F3 benchmark decisions on new valuations: verdict x refusal x first blocker
-- F4 the benchmark funnel stage by stage (contract -> fresh -> book -> edge
--    -> EV -> order -> fill), from each decision's own conditions
-- F5 orders, fills, cash and handoffs of the benchmark since the correction
-- F6 venue-native fixture rows written (soccer) and their scope

\echo '== F0 · first corrected row and migration 183 =='
SELECT min(decided_at) AS first_row_with_blockers, count(*) AS rows_with_blockers
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND settlement_comparison ? 'blockers';
SELECT version, applied_at FROM schema_migrations
 WHERE version LIKE '183%';

\echo '== F1 · settlement on corrected valuations =='
SELECT sport_family,
       settlement_comparison->>'compatibility' AS compatibility,
       settlement_comparison->>'overall_established' AS established,
       settlement_comparison->>'scope_phase' AS phase,
       count(*) AS valuations, count(DISTINCT us_market_slug) AS markets,
       max(decided_at) AS latest
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND settlement_comparison ? 'blockers'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC;

\echo '== F2 · primary settlement blocker on corrected valuations =='
SELECT sport_family,
       COALESCE(settlement_comparison->'blockers'->>0, 'NONE') AS primary_blocker,
       count(*) AS valuations, count(DISTINCT us_market_slug) AS markets,
       min(us_market_slug) AS example_market
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND settlement_comparison ? 'blockers'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== F3 · benchmark decisions on corrected valuations =='
SELECT d.verdict, d.refusal,
       CASE WHEN d.refusal = 'SETTLEMENT_NOT_SUPPORTED'
            THEN d.refusals[array_position(d.refusals, d.refusal) + 1] END
         AS first_blocker,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets,
       max(d.decided_at) AS latest
  FROM paper_decisions d
  JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
   AND v.settlement_comparison ? 'blockers'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo '== F4 · benchmark funnel, stage by stage, corrected valuations =='
WITH c AS (
  SELECT d.decision_id, d.verdict,
         (SELECT bool_and((x->>'passed')::boolean)
            FROM jsonb_array_elements(d.pinnacle->'contract_match'->'checks') x)
           AS contract_ok,
         (d.policy_decision->'conditions'->1->>'passed')::boolean AS fresh_ok,
         (d.policy_decision->'conditions'->2->>'passed')::boolean AS book_ok,
         (d.policy_decision->'conditions'->3->>'passed')::boolean AS edge_ok,
         (d.policy_decision->'conditions'->4->>'passed')::boolean AS ev_ok,
         EXISTS (SELECT 1 FROM paper_orders o
                  WHERE o.decision_id = d.decision_id) AS ordered,
         EXISTS (SELECT 1 FROM paper_orders o JOIN paper_fills f
                     ON f.order_id = o.order_id
                  WHERE o.decision_id = d.decision_id) AS filled
    FROM paper_decisions d
    JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
     AND v.settlement_comparison ? 'blockers')
SELECT count(*) AS decisions,
       count(*) FILTER (WHERE contract_ok) AS pass_contract_and_settlement,
       count(*) FILTER (WHERE contract_ok AND fresh_ok) AS then_fresh,
       count(*) FILTER (WHERE contract_ok AND fresh_ok AND book_ok)
         AS then_current_book,
       count(*) FILTER (WHERE contract_ok AND fresh_ok AND book_ok AND edge_ok)
         AS then_edge_5pp,
       count(*) FILTER (WHERE contract_ok AND fresh_ok AND book_ok AND edge_ok
                        AND ev_ok) AS then_ev_positive,
       count(*) FILTER (WHERE ordered) AS ordered,
       count(*) FILTER (WHERE filled) AS filled
  FROM c;

\echo '== F5 · benchmark orders, fills, cash and handoffs since the correction =='
SELECT o.created_at, o.order_id, o.decision_id, o.role, o.us_market_slug,
       o.qty, o.filled_qty, o.limit_price, o.state, o.terminal_reason
  FROM paper_orders o
 WHERE o.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY o.created_at DESC LIMIT 10;
SELECT f.filled_at, f.fill_id, f.order_id, f.qty, f.price, f.fee_usd,
       f.event_source
  FROM paper_fills f
 WHERE f.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY f.filled_at DESC LIMIT 10;
SELECT l.seq, l.committed_at, l.kind, l.cash_delta_usd, l.reserved_delta_usd,
       l.cash_after_usd, l.reserved_after_usd
  FROM paper_ledger l
 WHERE l.account_id = 'paper_acct_main'
 ORDER BY l.seq DESC LIMIT 10;
SELECT h.created_at, h.handoff_id, h.group_id, h.owner, h.confirmed_qty,
       h.strategy
  FROM paper_handoffs h
 WHERE h.strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK'
 ORDER BY h.created_at DESC LIMIT 10;

\echo '== F6 · venue-native fixture rows (soccer) =='
SELECT venue_fixture_key, competition, phase, game_format, play_has_begun,
       event_state_raw, source_match_id, retrieved_at, refusals
  FROM venue_fixture_metadata
 ORDER BY retrieved_at DESC LIMIT 20;
