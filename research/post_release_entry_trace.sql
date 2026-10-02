-- Post-release entry trace (read-only): every ENTRY decided since the 50ca411
-- release (live 2026-10-02T13:47:57Z) -- decision, reservation, fills, ledger
-- debit, Xavier handoff and reviews, Audrey findings -- plus same-contract
-- duplicate refusals since the release.
\echo '== T0 · entries since the release (decision -> order -> fills -> ledger) =='
SELECT o.strategy, d.policy_version, o.decided_at, o.us_market_slug, o.holding_side, o.state,
       o.qty, o.filled_qty, o.limit_price, o.reserved_usd,
       (SELECT round(sum(f.gross_usd + f.fee_usd), 2) FROM paper_fills f WHERE f.order_id = o.order_id) AS filled_cost_incl_fees,
       (SELECT round(sum(f.fee_usd), 4) FROM paper_fills f WHERE f.order_id = o.order_id) AS fees,
       (SELECT round(-sum(l.cash_delta_usd), 2) FROM paper_ledger l WHERE l.order_id = o.order_id AND l.kind = 'FILL') AS ledger_fill_debit,
       (SELECT string_agg(l.seq::text, ',' ORDER BY l.seq) FROM paper_ledger l WHERE l.order_id = o.order_id) AS ledger_seqs,
       o.group_id
  FROM paper_orders o LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
 WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
   AND o.decided_at >= '2026-10-02 13:47:57+00'
 ORDER BY o.decided_at;

\echo '== T1 · Xavier handoffs and first reviews for those groups =='
SELECT h.group_id, h.first_fill_at, h.confirmed_qty, h.outstanding_qty,
       (SELECT count(*) FROM paper_xavier_reviews r WHERE r.group_id = h.group_id) AS reviews,
       (SELECT r.reviewed_at || ' ' || coalesce(r.recommendation, '-') || ' / ' || coalesce(r.refusal, '-')
          FROM paper_xavier_reviews r WHERE r.group_id = h.group_id ORDER BY r.reviewed_at LIMIT 1) AS first_review
  FROM paper_handoffs h
 WHERE h.account_id = 'paper_acct_main'
   AND h.group_id IN (SELECT group_id FROM paper_orders WHERE account_id = 'paper_acct_main'
                        AND role = 'ENTRY' AND decided_at >= '2026-10-02 13:47:57+00')
 ORDER BY h.first_fill_at;

\echo '== T2 · Audrey findings and the latest report since the release =='
SELECT a.found_at, a.kind, a.severity, a.subject, left(a.detail::text, 220) AS detail
  FROM paper_audrey_findings a
 WHERE a.account_id = 'paper_acct_main' AND a.found_at >= '2026-10-02 13:47:57+00'
 ORDER BY a.found_at DESC LIMIT 12;
SELECT report_id, report_day, version, generated_at, reconciles, final
  FROM paper_audrey_reports WHERE account_id = 'paper_acct_main'
 ORDER BY generated_at DESC LIMIT 2;

\echo '== T3 · decisions and refusals since the release, by strategy (incl. same-contract) =='
SELECT strategy, policy_version, verdict, coalesce(refusal, '(none)') AS refusal, count(*) AS n
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND decided_at >= '2026-10-02 13:47:57+00'
 GROUP BY 1, 2, 3, 4 ORDER BY n DESC LIMIT 20;
