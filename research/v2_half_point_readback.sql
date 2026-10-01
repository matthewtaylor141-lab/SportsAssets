-- READ-ONLY. THE COMPLETED-GAME V2 (0.5 pp) RELEASE, READ BACK FROM PRODUCTION.
-- V0 the active parameter version and its audit trail
-- V1 every decision recorded under V2: funnel by refusal (observations and
--    distinct contracts), with the threshold each decision recorded
-- V2 the closest candidates: probability, best price, gross edge, fee per
--    contract at the fee stop, net EV after fees, exact refusal
-- V3 orders, simulated fills, cash debits, Xavier handoffs and reviews,
--    Audrey findings for completed-game fills since V2
-- V4 the account: one funding entry, cash, reserved
-- V5 the latest collection cycle and its refusals
\echo '== V0 · active parameter version =='
SELECT h.active_version_id, v.params, v.source, v.approved_by, h.updated_at
  FROM paper_policy_parameter_heads h
  JOIN paper_policy_parameter_versions v ON v.version_id = h.active_version_id;
SELECT activation_id, kind, version_id, previous_version_id, actor, at
  FROM paper_policy_parameter_activations ORDER BY at;

\echo '== V1 · decisions under V2, by first refusal =='
SELECT coalesce(refusal, 'ENTER') AS first_refusal, count(*) AS observations,
       count(DISTINCT us_market_slug) AS contracts,
       min(policy_decision->>'threshold_edge_pp') AS threshold_pp_recorded,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V2'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) AS v2_decisions,
       count(*) FILTER (WHERE (policy_decision->'conditions'->0->>'passed')::boolean) AS exact_match,
       count(*) FILTER (WHERE (policy_decision->'conditions'->1->>'passed')::boolean) AS fresh_pinnacle,
       count(*) FILTER (WHERE (policy_decision->'conditions'->2->>'passed')::boolean) AS usable_book,
       count(*) FILTER (WHERE (policy_decision->'conditions'->3->>'passed')::boolean) AS gross_edge_clears,
       count(*) FILTER (WHERE (policy_decision->'conditions'->4->>'passed')::boolean) AS ev_after_fees_positive,
       count(*) FILTER (WHERE verdict = 'ENTER') AS entered
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V2';

\echo '== V2 · the closest candidates under V2 =='
SELECT d.decided_at, left(d.us_market_slug, 34) AS market,
       round(d.p_pinnacle::numeric, 4) AS p_pinnacle,
       (d.economics->'levels'->0->>'price')::numeric AS best_price,
       round((d.economics->>'best_level_edge_pp')::numeric, 3) AS gross_edge_pp,
       round((d.economics->'fee_stop'->>'fee_per_contract_usd')::numeric * 100, 3)
         AS fee_pp_at_stop,
       round((d.economics->'fee_stop'->>'net_edge_pp')::numeric, 3) AS net_edge_pp,
       d.economics->'acquisition'->>'expected_net_profit_usd' AS ev_after_fees_usd,
       d.economics->'acquisition'->>'fees_usd' AS fees_usd,
       d.refusal
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND d.policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V2'
   AND d.economics->>'best_level_edge_pp' IS NOT NULL
 ORDER BY (d.economics->>'best_level_edge_pp')::float8 DESC LIMIT 15;
SELECT d.decided_at, left(d.us_market_slug, 34) AS market, d.refusal,
       d.refusals
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND d.policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V2'
   AND d.economics->>'best_level_edge_pp' IS NULL
 ORDER BY d.decided_at DESC LIMIT 15;

\echo '== V3 · orders, fills, debits, handoffs, reviews, findings under V2 =='
WITH d AS (SELECT decision_id FROM paper_decisions
            WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
              AND policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V2')
SELECT o.created_at, o.order_id, o.decision_id, o.us_market_slug, o.qty,
       o.filled_qty, o.limit_price, o.state, o.group_id
  FROM paper_orders o JOIN d USING (decision_id) ORDER BY o.created_at;
SELECT f.filled_at, f.fill_id, f.order_id, f.qty, f.price, f.fee_usd,
       l.seq AS ledger_seq, l.cash_delta_usd, l.cash_after_usd
  FROM paper_fills f
  JOIN paper_orders o ON o.order_id = f.order_id
  LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id
 WHERE o.decision_id IN (SELECT decision_id FROM paper_decisions
                          WHERE policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V2')
 ORDER BY f.filled_at;
SELECT h.created_at, h.handoff_id, h.group_id, h.owner, h.confirmed_qty,
       (SELECT count(*) FROM paper_xavier_reviews r WHERE r.group_id = h.group_id)
         AS xavier_reviews
  FROM paper_handoffs h
 WHERE h.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND h.created_at > now() - interval '1 day'
 ORDER BY h.created_at;
SELECT found_at, finding_id, kind, severity, subject
  FROM paper_audrey_findings
 WHERE found_at > now() - interval '6 hours'
   AND kind LIKE 'PAPER_EVENT_%' ORDER BY found_at;

\echo '== V4 · the account =='
SELECT count(*) FILTER (WHERE kind = 'INITIAL_FUNDING') AS funding_entries,
       (array_agg(cash_after_usd ORDER BY seq DESC))[1] AS cash_usd,
       (array_agg(reserved_after_usd ORDER BY seq DESC))[1] AS reserved_usd,
       max(seq) AS last_seq
  FROM paper_ledger;

\echo '== V5 · the latest collection cycle =='
SELECT s.value->>'at' AS cycle_at, s.value->'writer'->>'build' AS build,
       s.value->>'elapsed_s' AS elapsed_s, s.value->>'written' AS written,
       left((s.value->'refusals')::text, 400) AS refusals
  FROM ingestion_state s WHERE s.key = 'ext_pinnacle_last_cycle';
