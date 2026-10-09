-- READ-ONLY. SMALL LIVE PILOT V1, DELIVERABLE 2 (end-to-end PAPER chain), RUN 2:
-- the full linked readback of two historical PAPER positions chosen from the
-- run-1 census (research/pilot_d2_chain_census.sql, run 37971174595):
--   A papercggrp:922c858de129273d99f3ca43  the newest group by first ENTRY fill
--     (2026-10-06 00:47:19Z), closed by Xavier's standing-protection sale
--   B papercggrp:0963bc4364f3e9e9e2092592  the newest group closed by a
--     settlement (first ENTRY fill 2026-10-06 00:30:49Z, settled LOST)
-- Every link by id and timestamp: discovery valuation, market plane registry
-- row, the decision (p, EV, executable price, book), the canonical decision
-- intent (risk rails, sizing, Allie), the execution intent (SMALL LIVE
-- SHADOW sibling), capital authority / profitability bind / refusal census /
-- shadow rows, the ledger reservation, the orders and their events, fills,
-- the Derek -> Xavier handoff, Xavier's theses, reviews, assessments,
-- management intents, protection orders, exit intents and refusals, the
-- settlement, and Audrey's daily report and findings. SELECT only.

\echo R0 MIGRATION APPLY TIMES for the chain tables (which links existed when the positions were opened)
SELECT version, applied_at FROM schema_migrations
 WHERE version ~ '^(171|172|182|199|206|225|264|270|290|303|305|309|311|312|313)_'
 ORDER BY version;

\echo R1 THE DECISIONS (ENTRY orders of A and B -> paper_decisions)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, d.decision_id, d.strategy, d.decided_at, d.recorded_at, d.verdict, d.refusal,
       d.valuation_id, d.us_market_slug, d.holding_side, d.intent, d.fixture,
       d.p_internal, d.p_pinnacle, d.p_blended, d.proposed_qty, d.limit_price, d.book_obs_id,
       d.policy_version, d.simulator_version,
       jsonb_array_length(d.qualification_gaps) AS qualification_gaps
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id ORDER BY grp.tag;

\echo R1b DECISION EVIDENCE: economics, policy decision, book, pinnacle (truncated text)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, 'economics' AS part, left(d.economics::text, 700) AS body
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id
UNION ALL
SELECT grp.tag, 'policy_decision', left(d.policy_decision::text, 700)
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id
UNION ALL
SELECT grp.tag, 'book', left(d.book::text, 500)
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id
UNION ALL
SELECT grp.tag, 'pinnacle', left(d.pinnacle::text, 400)
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id
UNION ALL
SELECT grp.tag, 'label', left(d.label::text, 300)
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id
 ORDER BY 1, 2;

\echo R1c THE DECISION BOOK OBSERVATION (executable pricing input)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, b.obs_id, b.us_market_slug, b.observed_at, b.source, b.read_basis, b.market_state, b.error,
       left(b.offers::text, 200) AS offers, left(b.bids::text, 200) AS bids
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id
  JOIN paper_book_observations b ON b.obs_id = d.book_obs_id ORDER BY grp.tag;

\echo R2 DISCOVERY: the external valuation the decision was made on
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, v.id AS valuation_id, v.experiment_id, v.source_class, v.provider, v.book, v.venue,
       v.us_market_slug, v.market, v.period, v.line, v.mapped_outcome, v.mapping_match,
       v.contract_identity_basis, v.probability, v.executable_price, v.observed_at, v.received_at,
       v.age_s, v.decision, v.admissible, v.record_purpose, v.outcome_known, v.outcome, v.outcome_at
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN paper_decisions d ON d.decision_id = o.decision_id
  JOIN external_valuations v ON v.id = d.valuation_id ORDER BY grp.tag;

\echo R3 MARKET PLANE REGISTRY row for the contract (current row; the registry is upserted in place)
WITH grp(tag, slug) AS (VALUES ('A', 'aec-mlb-nyy-tb-2026-10-05'),
                               ('B', 'tsc-mlb-nyy-tb-2026-10-05-6pt5'))
SELECT grp.tag, r.contract_id, r.venue, r.sport, r.competition, r.event_id, r.market_type, r.family,
       r.period, r.active, r.desired_subscription, r.priority, r.required_reason, r.coverage_state,
       r.settlement_state, r.event_start, r.last_seen_at, r.updated_at
  FROM grp JOIN market_plane_registry r ON r.contract_id = grp.slug ORDER BY grp.tag;

\echo R4 CANONICAL DECISION INTENT (risk rails, sizing basis, Allie capital, binding constraints)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, c.intent_id, c.decision_id, c.strategy, c.created_at, c.expires_at, c.target_qty,
       c.limit_price, c.order_type, c.time_in_force, c.content_sha,
       left(c.sizing_basis::text, 400) AS sizing_basis, left(c.risk_rails::text, 500) AS risk_rails,
       left(c.allie::text, 400) AS allie, left(c.binding_constraints::text, 300) AS binding_constraints
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN canonical_decision_intents c ON c.decision_id = o.decision_id ORDER BY grp.tag;

\echo R4b CANONICAL INTENT EXECUTIONS by adapter (PAPER and SMALL LIVE) and the EXECUTION INTENT
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, e.execution_id, e.adapter, e.mode, e.state, e.exclusion, e.capital_scale, e.created_at
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN canonical_decision_intents c ON c.decision_id = o.decision_id
  JOIN canonical_intent_executions e ON e.intent_id = c.intent_id ORDER BY grp.tag, e.adapter;
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, x.intent_id, x.decision_id, x.paper_target_qty, x.limit_price, x.live_eligible,
       x.live_qty, x.actual_state, x.actual_refusal, x.created_at, x.updated_at,
       left(x.live_eligibility::text, 300) AS live_eligibility
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY'
  JOIN execution_intents x ON x.decision_id = o.decision_id ORDER BY grp.tag;

\echo R5 CAPITAL AUTHORITY AND SIZING RECORDS: profitability bind, refusal census, shadow (by decision or order key)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592')),
eo AS (SELECT grp.tag, o.decision_id, o.idempotency_key FROM grp
         JOIN paper_orders o ON o.group_id = grp.group_id AND o.role = 'ENTRY')
SELECT eo.tag, 'paper_profitability_evaluations' AS source, count(e.eval_id) AS n
  FROM eo LEFT JOIN paper_profitability_evaluations e
    ON e.decision_id = eo.decision_id OR e.order_key = eo.idempotency_key GROUP BY 1
UNION ALL
SELECT eo.tag, 'paper_entry_refusal_census', count(c.refusal_id)
  FROM eo LEFT JOIN paper_entry_refusal_census c ON c.decision_id = eo.decision_id GROUP BY 1
UNION ALL
SELECT eo.tag, 'paper_shadow_counterfactuals', count(s.shadow_id)
  FROM eo LEFT JOIN paper_shadow_counterfactuals s ON s.decision_id = eo.decision_id GROUP BY 1
 ORDER BY 1, 2;

\echo R6 EVERY ORDER OF THE GROUPS (entry and Xavier management) with state
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, o.order_id, o.strategy, o.role, o.direction, o.order_type, o.time_in_force, o.state,
       o.qty, o.filled_qty, o.limit_price, o.wire_price, o.reserved_usd, o.decision_id IS NOT NULL AS has_decision,
       o.decided_at, o.eligible_at, o.expires_at, o.created_at, o.terminal_at, o.terminal_reason
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id ORDER BY grp.tag, o.created_at;

\echo R7 ORDER EVENTS (state transitions) for every order of the groups
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, o.role, ev.order_id, ev.event_id, ev.kind, ev.event_source, ev.at, ev.recorded_at,
       left(ev.detail::text, 260) AS detail
  FROM grp JOIN paper_orders o ON o.group_id = grp.group_id
  JOIN paper_order_events ev ON ev.order_id = o.order_id
 ORDER BY grp.tag, ev.at, ev.event_id LIMIT 120;

\echo R8 FILLS of the groups
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, f.fill_id, f.order_id, f.role, f.direction, f.qty, f.price, f.wire_price, f.fee_usd,
       f.gross_usd, f.book_obs_id, f.book_observed_at, f.filled_at, f.recorded_at, f.basis, f.event_source
  FROM grp JOIN paper_fills f ON f.group_id = grp.group_id ORDER BY grp.tag, f.filled_at;

\echo R9 LEDGER ENTRIES of the groups (reservation, fill debits, sale credits, releases, settlement)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, l.seq, l.kind, l.cash_delta_usd, l.reserved_delta_usd, l.order_id, l.fill_id,
       l.position_key, l.settlement_key, l.event_source, l.committed_at
  FROM grp JOIN paper_ledger l ON l.group_id = grp.group_id ORDER BY grp.tag, l.seq;

\echo R10 DEREK -> XAVIER HANDOFF and XAVIER ENTRY THESIS
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, h.handoff_id, h.decision_id, h.entry_order_id, h.first_fill_id, h.first_fill_at, h.owner,
       h.confirmed_qty, h.outstanding_qty, h.created_at, h.updated_at
  FROM grp JOIN paper_handoffs h ON h.group_id = grp.group_id ORDER BY grp.tag;
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, t.thesis_id, t.decision_id, t.entered_at, t.first_fill_at, t.entry_qty, t.entry_cost_usd,
       t.entry_probability, t.probability_source, t.probability_source_at, t.entry_ev_usd,
       t.event_start_at, t.thesis_expires_at, t.recorded_at
  FROM grp JOIN xavier_entry_theses t ON t.group_id = grp.group_id ORDER BY grp.tag;

\echo R11 XAVIER REVIEWS by trigger, recommendation, refusal and action taken
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, x.trigger, coalesce(x.recommendation, '-') AS recommendation,
       coalesce(x.refusal, '-') AS refusal, coalesce(x.action->>'taken', '-') AS action_taken,
       count(*) AS n, min(x.reviewed_at) AS first_at, max(x.reviewed_at) AS last_at
  FROM grp JOIN paper_xavier_reviews x ON x.group_id = grp.group_id
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 7;

\echo R11b XAVIER REVIEWS: the first 3 and the last 3 per group, with evidence state and packet gate
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592')),
r AS (SELECT grp.tag, x.*, row_number() OVER (PARTITION BY grp.tag ORDER BY x.reviewed_at) AS rn_up,
             row_number() OVER (PARTITION BY grp.tag ORDER BY x.reviewed_at DESC) AS rn_dn
        FROM grp JOIN paper_xavier_reviews x ON x.group_id = grp.group_id)
SELECT tag, review_id, reviewed_at, recorded_at, trigger, recommendation, refusal, action->>'taken' AS taken,
       measure->>'evidence_state' AS evidence_state,
       selection->'management_packet'->'gate'->>'complete' AS packet_complete,
       left((selection->'management_packet'->'gate'->'missing')::text, 160) AS packet_missing,
       left(confirmed_protection::text, 160) AS confirmed_protection
  FROM r WHERE rn_up <= 3 OR rn_dn <= 3 ORDER BY tag, reviewed_at;

\echo R12 XAVIER MANAGEMENT ASSESSMENTS by recommendation and evidence state, and MANAGEMENT INTENTS
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, a.recommendation, a.evidence_state, a.thesis_state, count(*) AS n,
       count(*) FILTER (WHERE a.within_bound) AS within_latency_bound,
       min(a.assessed_at) AS first_at, max(a.assessed_at) AS last_at
  FROM grp JOIN xavier_management_assessments a ON a.group_id = grp.group_id
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, m.recommendation, m.action, m.evidence_state, count(*) AS n,
       min(m.created_at) AS first_at, max(m.created_at) AS last_at
  FROM grp JOIN canonical_management_intents m ON m.group_id = grp.group_id
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;

\echo R13 EXIT INTENTS and MANAGEMENT REFUSALS of the groups
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, i.intent_id, i.state, i.resolution, i.decided_at, i.resolved_at, i.exit_order_id,
       i.protection_order_id
  FROM grp JOIN paper_exit_intents i ON i.group_id = grp.group_id ORDER BY grp.tag, i.decided_at;
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, mr.kind, mr.refusal, count(*) AS n, min(mr.refused_at) AS first_at, max(mr.refused_at) AS last_at
  FROM grp JOIN paper_management_refusals mr ON mr.group_id = grp.group_id
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

\echo R14 SETTLEMENTS of the groups (every version)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, s.settlement_id, s.position_key, s.settlement_event_key, s.version, s.qty, s.outcome,
       s.payout_per_contract, s.payout_usd, s.evidence_source, s.settled_at, s.recorded_at
  FROM grp JOIN paper_settlements s ON s.group_id = grp.group_id ORDER BY grp.tag, s.version;

\echo R15 AUDREY DAILY REPORTS for the New York days of the fills and the settlement (versions, final, reconciles)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592')),
days AS (SELECT DISTINCT grp.tag, f.account_id, (f.filled_at AT TIME ZONE 'America/New_York')::date AS day
           FROM grp JOIN paper_fills f ON f.group_id = grp.group_id
         UNION
         SELECT DISTINCT grp.tag, s.account_id, (s.settled_at AT TIME ZONE 'America/New_York')::date
           FROM grp JOIN paper_settlements s ON s.group_id = grp.group_id)
SELECT days.tag, days.day, count(r.report_id) AS versions, max(r.version) AS last_version,
       bool_or(r.final) AS has_final, bool_and(r.reconciles) AS all_reconcile,
       min(r.generated_at) AS first_generated, max(r.generated_at) AS last_generated
  FROM days LEFT JOIN paper_audrey_reports r ON r.account_id = days.account_id AND r.report_day = days.day
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo R15b AUDREY FINAL (or latest) REPORT of each such day: reconciliation checks
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592')),
days AS (SELECT DISTINCT f.account_id, (f.filled_at AT TIME ZONE 'America/New_York')::date AS day
           FROM grp JOIN paper_fills f ON f.group_id = grp.group_id
         UNION
         SELECT DISTINCT s.account_id, (s.settled_at AT TIME ZONE 'America/New_York')::date
           FROM grp JOIN paper_settlements s ON s.group_id = grp.group_id),
last AS (SELECT DISTINCT ON (r.report_day) r.* FROM paper_audrey_reports r
           JOIN days ON days.account_id = r.account_id AND days.day = r.report_day
          ORDER BY r.report_day, r.version DESC)
SELECT last.report_day, last.report_id, last.version, last.final, last.reconciles, last.generated_at,
       c->>'check' AS check_name, c->>'passed' AS passed
  FROM last, jsonb_array_elements(last.report->'reconciliation'->'checks') c
 ORDER BY last.report_day, check_name;

\echo R16 AUDREY FINDINGS on the groups (subject = group, position key, or one of its orders)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592'))
SELECT grp.tag, af.finding_id, af.kind, af.severity, af.subject, af.found_at, af.improvement_task_id,
       left(af.detail::text, 300) AS detail
  FROM grp JOIN paper_audrey_findings af
    ON af.subject = grp.group_id OR af.subject LIKE 'paperpos:%:' || grp.group_id || ':%'
       OR af.subject IN (SELECT o.order_id FROM paper_orders o WHERE o.group_id = grp.group_id)
 ORDER BY grp.tag, af.found_at;

\echo R17 OPEN PAPER POSITIONS NOW (bought - sold - latest settlement), any strategy
WITH f AS (SELECT account_id, group_id, us_market_slug, holding_side, strategy,
                  coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
                  coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
             FROM paper_fills GROUP BY 1, 2, 3, 4, 5),
s AS (SELECT DISTINCT ON (position_key) position_key, qty FROM paper_settlements
       ORDER BY position_key, version DESC)
SELECT f.strategy, count(*) AS open_positions, sum(f.bought - f.sold - coalesce(s.qty, 0)) AS open_qty
  FROM f LEFT JOIN s ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id || ':'
                                        || f.us_market_slug || ':' || f.holding_side
 WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9 GROUP BY 1 ORDER BY 1;

\echo R18 MARKET PLANE EVENTS for the two contracts (append-only; full scan of an unindexed column, last)
SELECT contract_id, kind, count(*) AS n, min(at) AS first_at, max(at) AS last_at
  FROM market_plane_events
 WHERE contract_id IN ('aec-mlb-nyy-tb-2026-10-05', 'tsc-mlb-nyy-tb-2026-10-05-6pt5')
 GROUP BY 1, 2 ORDER BY 1, 2;
