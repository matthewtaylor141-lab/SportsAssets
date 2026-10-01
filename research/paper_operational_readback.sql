-- Operational snapshot, not a release gate or profitability certificate.
-- Read only. Tested against the deployed paper schema.
-- Account-wide cash includes funding recorded before session creation.
-- No fills means NOT_EXERCISED, never a successful execution proof.
WITH
target AS (SELECT 'paper_acct_main'::text AS account_id),
session AS (
 SELECT s.* FROM paper_sessions s JOIN target t USING(account_id)
 ORDER BY s.started_at DESC LIMIT 1
),
ledger_rows AS (
 SELECT l.*,
 sum(cash_delta_usd) OVER (ORDER BY seq) AS computed_cash,
 sum(reserved_delta_usd) OVER (ORDER BY seq) AS computed_reserved
 FROM paper_ledger l JOIN target t USING(account_id)
),
balance AS (SELECT * FROM ledger_rows ORDER BY seq DESC LIMIT 1),
decision_counts AS (
 SELECT strategy, verdict, refusal, count(*) AS decisions,
 max(decided_at) AS last_decision_at
 FROM paper_decisions WHERE session_id IN (SELECT session_id FROM session)
 GROUP BY strategy, verdict, refusal
),
orders AS (SELECT * FROM paper_orders WHERE session_id IN (SELECT session_id FROM session)),
fills AS (SELECT * FROM paper_fills WHERE session_id IN (SELECT session_id FROM session)),
handoffs AS (SELECT * FROM paper_handoffs WHERE session_id IN (SELECT session_id FROM session)),
reviews AS (SELECT * FROM paper_xavier_reviews WHERE session_id IN (SELECT session_id FROM session)),
first_fill AS (SELECT * FROM fills ORDER BY filled_at, fill_id LIMIT 1),
first_trace AS (
 SELECT f.fill_id, f.order_id, f.strategy, f.group_id, f.us_market_slug,
 f.direction, f.role, f.qty, f.price, f.fee_usd, f.filled_at,
 o.decision_id,
 EXISTS(SELECT 1 FROM paper_decisions d WHERE d.decision_id=o.decision_id
   AND d.strategy=f.strategy AND d.session_id=f.session_id) AS decision_link_present,
 (SELECT count(*) FROM ledger_rows l WHERE l.fill_id=f.fill_id) AS ledger_entries,
 (SELECT sum(l.cash_delta_usd) FROM ledger_rows l WHERE l.fill_id=f.fill_id) AS cash_delta_usd,
 (SELECT count(*) FROM handoffs h WHERE h.entry_order_id=f.order_id
   AND h.strategy=f.strategy) AS handoffs_for_order,
 (SELECT count(*) FROM reviews r WHERE r.group_id=f.group_id
   AND r.strategy=f.strategy AND r.reviewed_at>=f.filled_at) AS subsequent_reviews
 FROM first_fill f LEFT JOIN orders o ON o.order_id=f.order_id
),
latest_audit AS (
 SELECT report_id, generated_at, report_day, version, final, reconciles
 FROM paper_audrey_reports WHERE session_id IN (SELECT session_id FROM session)
 ORDER BY generated_at DESC LIMIT 1
)
SELECT jsonb_build_object(
 'observed_at', now(),
 'scope', 'Latest paper session; account-wide ledger; counts are not linked lifecycle proof',
 'session', (SELECT jsonb_build_object('id',session_id,'status',status,'started_at',started_at) FROM session),
 'health', (SELECT jsonb_build_object('heartbeat_at',h.heartbeat_at,
   'heartbeat_age_seconds',extract(epoch FROM now()-h.heartbeat_at),
   'passes',h.passes,'errors',h.errors,'venue_mutation_attempts',h.mutation_attempts)
   FROM paper_session_health h WHERE h.session_id IN (SELECT session_id FROM session)),
 'account', jsonb_build_object(
   'ledger_entries',(SELECT count(*) FROM ledger_rows),
   'funding_entries',(SELECT count(*) FROM ledger_rows WHERE kind='INITIAL_FUNDING'),
   'cash_usd',(SELECT cash_after_usd FROM balance),
   'reserved_usd',(SELECT reserved_after_usd FROM balance),
   'available_usd',(SELECT cash_after_usd-reserved_after_usd FROM balance),
   'last_ledger_event_at',(SELECT committed_at FROM balance),
   'running_balance_mismatches',(SELECT count(*) FROM ledger_rows
       WHERE cash_after_usd IS DISTINCT FROM computed_cash
          OR reserved_after_usd IS DISTINCT FROM computed_reserved)),
 'decisions',coalesce((SELECT jsonb_agg(to_jsonb(d) ORDER BY strategy,verdict,refusal) FROM decision_counts d),'[]'::jsonb),
 'activity',jsonb_build_object(
   'orders',(SELECT count(*) FROM orders),
   'fills',(SELECT count(*) FROM fills),
   'handoffs',(SELECT count(*) FROM handoffs),
   'xavier_reviews',(SELECT count(*) FROM reviews),
   'audrey_findings',(SELECT count(*) FROM paper_audrey_findings WHERE session_id IN (SELECT session_id FROM session)),
   'findings_with_improvement_task',(SELECT count(*) FROM paper_audrey_findings
       WHERE session_id IN (SELECT session_id FROM session) AND improvement_task_id IS NOT NULL)),
 'execution_status',CASE
   WHEN NOT EXISTS(SELECT 1 FROM session) THEN 'NO_SESSION'
   WHEN NOT EXISTS(SELECT 1 FROM fills) THEN 'NOT_EXERCISED'
   ELSE 'FILLS_PRESENT_INSPECT_LINKS_AND_OUTCOMES' END,
 'first_fill_trace',(SELECT to_jsonb(f) FROM first_trace f),
 'latest_audrey_report',(SELECT to_jsonb(a) FROM latest_audit a),
 'audit_scope_note','A report existing or reconciling does not prove that it audited each fill or produced an evaluated improvement.'
) AS operational_receipt;
