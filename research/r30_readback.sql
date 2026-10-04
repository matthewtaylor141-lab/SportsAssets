-- R30 production readback (read only): migration 225 objects, the SMALL LIVE
-- control (SHADOW, halt), canonical intents and both adapters' records, the
-- parity ledger by state, a sample intent with its components, and proof
-- that nothing reached the venue (no SMALL LIVE venue events; the execution
-- mirror's orders unchanged).
\echo == migrations 225-226
SELECT version::text FROM schema_migrations WHERE version::text >= '225' ORDER BY 1;
\echo == workers / api boot
SELECT left(value::text, 200) AS workers_boot FROM ingestion_state WHERE key = 'workers_boot';
\echo == small_live_control
SELECT mode, halted, halted_at, halt_reason, halt_parity_id, cleared_by, cleared_at FROM small_live_control;
SELECT action, actor, reason, parity_id, at FROM small_live_control_events ORDER BY at DESC LIMIT 10;
\echo == canonical intents
SELECT strategy, strategy_version, sleeve, count(*), min(created_at), max(created_at)
  FROM canonical_decision_intents GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT sleeve, action, evidence_state, count(*), max(created_at)
  FROM canonical_management_intents GROUP BY 1,2,3 ORDER BY 1,2,3;
\echo == adapter executions
SELECT intent_kind, adapter, mode, state, exclusion, count(*)
  FROM canonical_intent_executions GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5;
\echo == parity ledger
SELECT intent_kind, sleeve, parity_state, count(*), max(created_at)
  FROM live_parity_ledger GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT parity_id, intent_kind, parity_state, divergence_fields, created_at
  FROM live_parity_ledger WHERE parity_state = 'LOGIC_DIVERGENCE' ORDER BY created_at DESC LIMIT 10;
\echo == latest decision intent and its components
SELECT intent_id, decision_id, strategy_version, holding_side, order_intent, limit_price, wire_price, target_qty,
       derek->>'verdict' AS derek, karen->>'state' AS karen, allie->>'status' AS allie,
       allie->>'shadow_usd' AS allie_usd, eddie->>'status' AS eddie, eddie->>'recommendation' AS eddie_rec,
       opportunity_score->>'status' AS opp, opportunity_score->>'opportunity_score' AS opp_score,
       left(coalesce(opportunity_score->>'why', eddie->>'why', ''), 160) AS why, created_at
  FROM canonical_decision_intents ORDER BY created_at DESC LIMIT 5;
\echo == the paper order equals the intent (latest 10)
SELECT i.decision_id, o.order_id, (o.qty = i.target_qty) AS qty_eq, (o.limit_price = i.limit_price) AS limit_eq,
       (o.wire_price = i.wire_price) AS wire_eq, (o.holding_side = i.holding_side) AS side_eq,
       (o.intent = i.order_intent) AS intent_eq
  FROM canonical_decision_intents i JOIN paper_orders o ON o.decision_id = i.decision_id AND o.role = 'ENTRY'
 ORDER BY i.created_at DESC LIMIT 10;
\echo == nothing reached the venue
SELECT (SELECT count(*) FROM small_live_order_events) AS small_live_venue_events,
       (SELECT count(*) FROM execmirror_orders) AS execmirror_orders_total,
       (SELECT count(*) FROM execmirror_orders WHERE venue_order_id IS NOT NULL) AS execmirror_with_venue_id;
SELECT enabled, stopped, scale, max_order_usd FROM execmirror_control;
