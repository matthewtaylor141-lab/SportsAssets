-- READ-ONLY. THE PINNACLE_COMPLETED_GAME_PAPER (V1) LIVE FUNNEL, BESIDE THE
-- STRICT BENCHMARK: serving evidence, the eligibility funnel stage by stage,
-- the refusals by name, and the first simulated entry with its ledger debit
-- and Xavier handoff. Nothing here writes.
--
-- C0 serving evidence: migration 184, the policy's kill-switch row, the
--    first decision of the policy
-- C1 decisions by strategy x verdict x first refusal (last 24 h)
-- C2 the completed-game funnel, stage by stage, from each decision's own
--    checks and conditions
-- C3 the completed-game match: which check failed first, counted
-- C4 orders, simulated fills, ledger debits and Xavier handoffs of the policy
-- C5 the account: one INITIAL_FUNDING entry, current cash and reservations
-- C6 settlements of the policy's positions (exceptional outcomes apart)
-- C7 top candidates by Pinnacle edge at the book among completed-game
--    decisions that passed the match (what would qualify, what did not)

\echo '== C0 · serving evidence =='
SELECT version, applied_at FROM schema_migrations WHERE version LIKE '18%'
 ORDER BY version;
SELECT control_key, enabled, updated_by, updated_at FROM paper_control
 ORDER BY control_key;
SELECT strategy, min(decided_at) AS first_decision, max(decided_at) AS latest,
       count(*) AS decisions,
       min(policy_version) AS policy_version_min,
       max(policy_version) AS policy_version_max
  FROM paper_decisions
 WHERE strategy IN ('PINNACLE_ONLY_PAPER_BENCHMARK',
                    'PINNACLE_COMPLETED_GAME_PAPER')
 GROUP BY 1 ORDER BY 1;

\echo '== C1 · decisions by strategy, verdict and first refusal, last 24 h =='
SELECT strategy, verdict, coalesce(refusal, 'ENTER') AS first_refusal,
       count(*) AS decisions, count(DISTINCT us_market_slug) AS markets,
       max(decided_at) AS latest
  FROM paper_decisions
 WHERE strategy IN ('PINNACLE_ONLY_PAPER_BENCHMARK',
                    'PINNACLE_COMPLETED_GAME_PAPER')
   AND decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

\echo '== C2 · completed-game funnel, stage by stage =='
WITH c AS (
  SELECT d.decision_id, d.verdict, v.sport_family,
         (SELECT bool_and((x->>'passed')::boolean)
            FROM jsonb_array_elements(d.pinnacle->'contract_match'->'checks') x
           WHERE x->>'check' IN ('fixture_participants_date_side_period',
                                 'polymarket_us_contract',
                                 'probability_qualified_by_the_lane'))
           AS identity_ok,
         (SELECT bool_and((x->>'passed')::boolean)
            FROM jsonb_array_elements(d.pinnacle->'contract_match'->'checks') x
           WHERE x->>'check' IN ('payout_outcome_match', 'market_and_line',
                                 'grading_period_full_game'))
           AS outcome_market_ok,
         (SELECT bool_and((x->>'passed')::boolean)
            FROM jsonb_array_elements(d.pinnacle->'contract_match'->'checks') x
           WHERE x->>'check' = 'ordinary_completion_grading_period')
           AS grading_ok,
         (d.policy_decision->'conditions'->0->>'passed')::boolean AS match_ok,
         (d.policy_decision->'conditions'->1->>'passed')::boolean AS fresh_ok,
         (d.policy_decision->'conditions'->2->>'passed')::boolean AS book_ok,
         (d.policy_decision->'conditions'->3->>'passed')::boolean AS edge_ok,
         (d.policy_decision->'conditions'->4->>'passed')::boolean AS ev_ok,
         EXISTS (SELECT 1 FROM paper_orders o
                  WHERE o.decision_id = d.decision_id
                    AND o.role = 'ENTRY') AS ordered,
         EXISTS (SELECT 1 FROM paper_orders o JOIN paper_fills f
                     ON f.order_id = o.order_id
                  WHERE o.decision_id = d.decision_id) AS filled
    FROM paper_decisions d
    JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER')
SELECT coalesce(sport_family, 'ALL') AS sport_family,
       count(*) AS decisions,
       count(*) FILTER (WHERE identity_ok) AS identity,
       count(*) FILTER (WHERE identity_ok AND outcome_market_ok)
         AS then_outcome_market_period,
       count(*) FILTER (WHERE match_ok) AS then_ordinary_grading,
       count(*) FILTER (WHERE match_ok AND fresh_ok) AS then_fresh_pinnacle,
       count(*) FILTER (WHERE match_ok AND fresh_ok AND book_ok)
         AS then_current_book,
       count(*) FILTER (WHERE match_ok AND fresh_ok AND book_ok AND edge_ok)
         AS then_edge_5pp,
       count(*) FILTER (WHERE match_ok AND fresh_ok AND book_ok AND edge_ok
                        AND ev_ok) AS then_conditional_ev_positive,
       count(*) FILTER (WHERE ordered) AS ordered,
       count(*) FILTER (WHERE filled) AS filled
  FROM c GROUP BY ROLLUP (sport_family) ORDER BY 2 DESC;

\echo '== C3 · completed-game match: first failing check and its detail =='
SELECT x->>'check' AS failing_check, x->>'refusal' AS refusal,
       left(x->>'detail', 120) AS detail_example,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d,
       LATERAL (SELECT y FROM jsonb_array_elements(
                  d.pinnacle->'contract_match'->'checks') WITH ORDINALITY
                  AS t(y, n)
                 WHERE (y->>'passed')::boolean IS NOT TRUE
                 ORDER BY n LIMIT 1) f(x)
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25;

\echo '== C4 · completed-game orders, fills, ledger debits, handoffs =='
SELECT o.created_at, o.order_id, o.decision_id, o.role, o.us_market_slug,
       o.qty, o.filled_qty, o.limit_price, o.state, o.terminal_reason
  FROM paper_orders o
 WHERE o.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 ORDER BY o.created_at LIMIT 10;
SELECT f.filled_at, f.fill_id, f.order_id, f.qty, f.price, f.fee_usd,
       f.event_source,
       (SELECT l.seq FROM paper_ledger l WHERE l.fill_id = f.fill_id
         AND l.kind = 'FILL' LIMIT 1) AS ledger_seq,
       (SELECT -l.cash_delta_usd FROM paper_ledger l WHERE l.fill_id = f.fill_id
         AND l.kind = 'FILL' LIMIT 1) AS ledger_debit_usd,
       (SELECT l.cash_after_usd FROM paper_ledger l WHERE l.fill_id = f.fill_id
         AND l.kind = 'FILL' LIMIT 1) AS cash_after_usd
  FROM paper_fills f
 WHERE f.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 ORDER BY f.filled_at LIMIT 10;
SELECT h.created_at, h.handoff_id, h.group_id, h.owner, h.confirmed_qty,
       h.strategy
  FROM paper_handoffs h
 WHERE h.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 ORDER BY h.created_at LIMIT 10;
SELECT r.reviewed_at, r.group_id, r.strategy, r.trigger,
       r.recommendation, r.refusal, left(r.measure::text, 300) AS measure,
       left(r.exceptional::text, 300) AS exceptional
  FROM paper_xavier_reviews r
 WHERE r.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 ORDER BY r.reviewed_at DESC LIMIT 5;

\echo '== C5 · the account: one funding entry, cash, reservations =='
SELECT account_id,
       count(*) FILTER (WHERE kind = 'INITIAL_FUNDING') AS funding_entries,
       (array_agg(cash_after_usd ORDER BY seq DESC))[1] AS cash_usd,
       (array_agg(reserved_after_usd ORDER BY seq DESC))[1] AS reserved_usd,
       max(seq) AS last_seq
  FROM paper_ledger GROUP BY 1;

\echo '== C6 · settlements of completed-game positions =='
SELECT s.settled_at, s.group_id, s.us_market_slug, s.outcome, s.qty,
       s.payout_per_contract, s.payout_usd, s.evidence_source
  FROM paper_settlements s
 WHERE s.group_id IN (SELECT group_id FROM paper_orders
                       WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER')
 ORDER BY s.settled_at DESC LIMIT 10;

\echo '== C7 · top completed-game candidates past the match, by book edge =='
SELECT d.decided_at, d.valuation_id, left(d.us_market_slug, 40) AS market,
       d.verdict, d.refusal, d.p_pinnacle,
       (d.economics->>'best_level_edge_pp')::float8 AS best_level_edge_pp,
       d.economics->'acquisition'->>'expected_net_profit_usd' AS cond_ev_usd,
       d.policy_decision->'shortfall' AS shortfall
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND (d.policy_decision->'conditions'->0->>'passed')::boolean
 ORDER BY (d.economics->>'best_level_edge_pp')::float8 DESC NULLS LAST
 LIMIT 15;
