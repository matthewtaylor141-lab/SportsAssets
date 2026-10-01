-- READ-ONLY. THE NEWEST COLLECTED VALUATIONS CARRYING THE VENUE'S OWN RULES
-- TEXT, AND EACH ONE'S COMPLETE PAPER DECISION TRACE. Nothing here writes.
--
-- T0 the active entry policies (paper_control) and the serving migrations
-- T1 the newest valuations with venue_rules_text: source field, reader,
--    retrieval instant, cache flag, sha256, the exact terms (first 600 chars)
-- T2 every paper decision on those valuations: strategy, policy version,
--    verdict, refusals, the completed-game checks, conditions, economics
-- T3 orders, fills, ledger entries and Xavier handoffs/reviews downstream of
--    those decisions (empty when nothing qualified)

\echo '== T0 · active entry policies and migrations =='
SELECT control_key, enabled, updated_by, updated_at, left(why, 160) AS why
  FROM paper_control ORDER BY control_key;
SELECT version, applied_at FROM schema_migrations
 WHERE version LIKE '18%' ORDER BY version;

\echo '== T1 · newest valuations carrying venue rules text =='
SELECT v.id, v.decided_at, v.sport_family, v.us_market_slug, v.record_purpose,
       v.probability, v.observed_at AS pinnacle_observed_at,
       v.settlement_comparison->>'compatibility' AS strict_compat,
       v.settlement_comparison->>'venue_rules_source' AS rules_source,
       v.settlement_comparison->>'venue_rules_field' AS rules_field,
       v.settlement_comparison->>'venue_rules_reader' AS rules_reader,
       to_timestamp((v.settlement_comparison->>'venue_rules_retrieved_at')
                    ::float8) AS rules_retrieved_at,
       v.settlement_comparison->>'venue_rules_from_cache' AS rules_from_cache,
       v.settlement_comparison->>'venue_rules_sha256' AS rules_sha256,
       left(v.settlement_comparison->>'venue_rules_text', 600) AS rules_text
  FROM external_valuations v
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND v.settlement_comparison ? 'venue_rules_text'
   AND v.settlement_comparison->>'venue_rules_text' IS NOT NULL
 ORDER BY v.id DESC LIMIT 5;
SELECT count(*) AS valuations_with_text,
       count(DISTINCT us_market_slug) AS markets,
       min(decided_at) AS first_at, max(decided_at) AS latest_at
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND settlement_comparison->>'venue_rules_text' IS NOT NULL;

\echo '== T2 · paper decisions on those valuations =='
WITH v AS (
  SELECT id FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND settlement_comparison->>'venue_rules_text' IS NOT NULL
   ORDER BY id DESC LIMIT 5)
SELECT d.valuation_id, d.decided_at, d.strategy, d.policy_version, d.verdict,
       d.refusals, d.pinnacle->>'decided_via' AS decided_via,
       d.pinnacle->>'age_s' AS pinnacle_age_s,
       (SELECT jsonb_agg(jsonb_build_object('check', x->>'check',
                 'passed', x->'passed', 'refusal', x->'refusal'))
          FROM jsonb_array_elements(d.pinnacle->'contract_match'->'checks') x)
         AS checks,
       d.pinnacle->'contract_match'->'exceptional_terms'->>'status'
         AS exceptional_status,
       (SELECT jsonb_agg(jsonb_build_object('c', c->>'condition',
                 'passed', c->'passed'))
          FROM jsonb_array_elements(d.policy_decision->'conditions') c)
         AS conditions,
       d.economics->>'best_level_edge_pp' AS best_level_edge_pp,
       d.economics->'acquisition'->>'expected_net_profit_usd' AS cond_ev_usd,
       d.economics->'acquisition'->>'fees_usd' AS fees_usd,
       d.economics->>'book_age_s' AS book_age_s,
       d.economics->>'label' AS economics_label,
       d.book_obs_id, d.proposed_qty, d.limit_price
  FROM paper_decisions d JOIN v ON v.id = d.valuation_id
 ORDER BY d.valuation_id DESC, d.strategy;

\echo '== T3 · downstream: orders, fills, ledger, handoffs, reviews =='
WITH d AS (
  SELECT decision_id FROM paper_decisions
   WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND verdict = 'ENTER')
SELECT o.created_at, o.order_id, o.decision_id, o.role, o.us_market_slug,
       o.qty, o.filled_qty, o.limit_price, o.state, o.group_id
  FROM paper_orders o JOIN d USING (decision_id)
 ORDER BY o.created_at LIMIT 10;
SELECT f.filled_at, f.fill_id, f.order_id, f.qty, f.price, f.fee_usd,
       l.seq AS ledger_seq, l.kind, l.cash_delta_usd, l.cash_after_usd
  FROM paper_fills f
  LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id AND l.kind = 'FILL'
 WHERE f.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 ORDER BY f.filled_at LIMIT 10;
SELECT h.created_at, h.handoff_id, h.group_id, h.owner, h.confirmed_qty,
       h.strategy,
       (SELECT count(*) FROM paper_xavier_reviews r
         WHERE r.group_id = h.group_id) AS xavier_reviews
  FROM paper_handoffs h
 WHERE h.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 ORDER BY h.created_at LIMIT 10;
