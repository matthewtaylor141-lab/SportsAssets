-- READ-ONLY. SMALL LIVE PILOT V1, DELIVERABLE 2 (end-to-end PAPER chain), RUN 3:
-- the Audrey and capital links of the two chains read back in run 2
-- (research/pilot_d2_chain_readback.sql, run 37971626397):
--   A papercggrp:922c858de129273d99f3ca43, B papercggrp:0963bc4364f3e9e9e2092592
-- Audrey per-event audits (subjects = fill, handoff, settlement, decision ids),
-- the report versions that did not reconcile and which check failed, whether
-- any report was ever written final, the decision book levels against the
-- observed book, Allie at the decision across every canonical intent, the
-- ENTRY orders since the capital authority deployed, and the newest
-- protection-filled group (Xavier activity today). SELECT only.

\echo A1 AUDREY EVENT AUDITS for A and B (subject = fill, handoff, settlement or decision id)
WITH grp(tag, group_id) AS (VALUES ('A', 'papercggrp:922c858de129273d99f3ca43'),
                                   ('B', 'papercggrp:0963bc4364f3e9e9e2092592')),
subj AS (SELECT grp.tag, f.fill_id AS s, 'fill:' || f.role AS what FROM grp JOIN paper_fills f ON f.group_id = grp.group_id
         UNION ALL SELECT grp.tag, h.handoff_id, 'handoff' FROM grp JOIN paper_handoffs h ON h.group_id = grp.group_id
         UNION ALL SELECT grp.tag, s.settlement_id, 'settlement' FROM grp JOIN paper_settlements s ON s.group_id = grp.group_id
         UNION ALL SELECT grp.tag, o.decision_id, 'decision' FROM grp JOIN paper_orders o ON o.group_id = grp.group_id
                    WHERE o.role = 'ENTRY')
SELECT subj.tag, subj.what, subj.s AS subject, af.finding_id, af.kind, af.severity, af.found_at,
       af.detail->>'passed' AS passed, af.improvement_task_id
  FROM subj LEFT JOIN paper_audrey_findings af ON af.subject = subj.s
 ORDER BY subj.tag, af.found_at NULLS LAST, subj.what;

\echo A2 AUDREY REPORT VERSIONS that did not reconcile, by day, and the failing checks
SELECT r.report_day, count(*) FILTER (WHERE NOT r.reconciles) AS not_reconciling_versions,
       count(*) AS versions, min(r.generated_at) FILTER (WHERE NOT r.reconciles) AS first_bad,
       max(r.generated_at) FILTER (WHERE NOT r.reconciles) AS last_bad
  FROM paper_audrey_reports r
 WHERE r.report_day BETWEEN date '2026-10-04' AND date '2026-10-09'
 GROUP BY 1 ORDER BY 1;
SELECT r.report_day, c->>'check' AS check_name, count(*) AS failing_versions
  FROM paper_audrey_reports r, jsonb_array_elements(r.report->'reconciliation'->'checks') c
 WHERE r.report_day BETWEEN date '2026-10-04' AND date '2026-10-09' AND NOT r.reconciles
   AND (c->>'passed') = 'false'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo A3 REPORT_DOES_NOT_RECONCILE findings 2026-10-04 .. 2026-10-09
SELECT subject AS report_day, severity, count(*) AS n, min(found_at) AS first_at, max(found_at) AS last_at
  FROM paper_audrey_findings WHERE kind = 'REPORT_DOES_NOT_RECONCILE'
   AND found_at >= timestamptz '2026-10-04 00:00:00+00'
 GROUP BY 1, 2 ORDER BY 1;

\echo A4 AUDREY REPORTS EVER WRITTEN final, all time, and the newest report
SELECT count(*) AS reports, count(*) FILTER (WHERE final) AS final_reports,
       count(DISTINCT report_day) AS days, max(generated_at) AS newest
  FROM paper_audrey_reports;
SELECT report_day, version, final, reconciles, generated_at
  FROM paper_audrey_reports ORDER BY generated_at DESC LIMIT 3;

\echo A5 DECISION A BOOK LEVELS against the observed book at the decision (obs 54406) and at the fill (obs 54408)
SELECT 'decision_levels' AS what, left((d.book->'levels')::text, 900) AS body
  FROM paper_decisions d WHERE d.decision_id = 'papercg:922c858de129273d99f3ca43'
UNION ALL
SELECT 'obs_' || b.obs_id || '_bids_at_' || b.observed_at, left(b.bids::text, 900)
  FROM paper_book_observations b WHERE b.obs_id IN (54406, 54408)
UNION ALL
SELECT 'obs_' || b.obs_id || '_offers', left(b.offers::text, 400)
  FROM paper_book_observations b WHERE b.obs_id IN (54406, 54408);

\echo A6 ALLIE AT THE DECISION across every canonical decision intent, by strategy and status
SELECT strategy, coalesce(allie->>'status', '(none)') AS allie_status, left(coalesce(allie->>'why', '-'), 60) AS why,
       count(*) AS intents, min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_decision_intents GROUP BY 1, 2, 3 ORDER BY 1, 5 DESC;

\echo A7 ENTRY ORDERS created since the capital authority migration (305, 2026-10-06 05:53:34Z) by strategy and state
SELECT strategy, state, count(*) AS n, min(created_at) AS first_at, max(created_at) AS last_at
  FROM paper_orders WHERE role = 'ENTRY' AND created_at >= timestamptz '2026-10-06 05:53:34+00'
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT strategy, count(*) AS enter_decisions, min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions WHERE verdict = 'ENTER' AND decided_at >= timestamptz '2026-10-06 05:53:34+00'
 GROUP BY 1 ORDER BY 1;
SELECT strategy, stage, refusal, count(*) AS n
  FROM paper_entry_refusal_census WHERE stage = 'LEDGER'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 15;

\echo A8 THE NEWEST PROTECTION-FILLED GROUP (newest PAPER fill overall): its orders, fills and last Xavier reviews
WITH g AS (SELECT group_id FROM paper_fills ORDER BY filled_at DESC LIMIT 1)
SELECT o.order_id, o.strategy, o.role, o.state, o.qty, o.filled_qty, o.limit_price, o.created_at, o.terminal_at,
       o.terminal_reason
  FROM paper_orders o JOIN g ON g.group_id = o.group_id ORDER BY o.created_at DESC LIMIT 12;
WITH g AS (SELECT group_id FROM paper_fills ORDER BY filled_at DESC LIMIT 1)
SELECT f.fill_id, f.role, f.direction, f.qty, f.price, f.filled_at
  FROM paper_fills f JOIN g ON g.group_id = f.group_id ORDER BY f.filled_at;
WITH g AS (SELECT group_id FROM paper_fills ORDER BY filled_at DESC LIMIT 1)
SELECT x.review_id, x.reviewed_at, x.trigger, x.recommendation, x.refusal, x.action->>'taken' AS taken
  FROM paper_xavier_reviews x JOIN g ON g.group_id = x.group_id ORDER BY x.reviewed_at DESC LIMIT 5;

\echo A9 XAVIER REVIEWS in the last 24 h (any group), by recommendation
SELECT coalesce(recommendation, '-') AS recommendation, count(*) AS n, max(reviewed_at) AS newest
  FROM paper_xavier_reviews WHERE reviewed_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;
