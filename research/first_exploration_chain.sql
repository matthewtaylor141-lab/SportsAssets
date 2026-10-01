-- READ-ONLY. THE FIRST LIVE-DATA EXPLORATION POSITION, LINK BY LINK.
-- C1 the valuation the decision was made on (instants, Pinnacle age)
-- C2 the decision (estimate, fees, selection, purpose)
-- C3 the order, its events, the fill and the book the fill used
-- C4 the ledger rows of that order (reservation, debit)
-- C5 the handoff, Xavier's reviews and his protection order
-- C6 Audrey's findings on that group (ids, found_at vs recorded_at, passed)
-- C7 duplicates: per group, entry orders / fills / FILL ledger rows / handoffs
\echo '== C1/C2 · the decision and its valuation =='
SELECT d.decision_id, d.strategy, d.decided_at, d.recorded_at, d.valuation_id,
       v.decided_at AS valuation_decided_at, v.observed_at AS pinnacle_observed_at,
       d.us_market_slug, d.fixture, d.holding_side, d.verdict, d.p_pinnacle,
       d.limit_price, d.proposed_qty, d.book_obs_id, d.policy_version,
       left((d.policy_decision->'estimate')::text, 600) AS estimate,
       left((d.policy_decision->'selection')::text, 500) AS selection,
       d.policy_decision->>'training_purpose' AS training_purpose,
       d.policy_decision->>'position_label' AS position_label
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.strategy = 'PINNACLE_EXPLORATION_PAPER' AND d.verdict = 'ENTER'
 ORDER BY d.decided_at;

\echo '== C3 · orders, fills and the books used =='
SELECT o.order_id, o.decision_id, o.group_id, o.role, o.order_type,
       o.time_in_force, o.qty, o.limit_price, o.wire_price, o.filled_qty,
       o.state, o.decided_at, o.eligible_at, o.expires_at
  FROM paper_orders o WHERE o.strategy = 'PINNACLE_EXPLORATION_PAPER'
 ORDER BY o.decided_at;
SELECT f.fill_id, f.order_id, f.group_id, f.qty, f.price, f.wire_price,
       f.fee_usd, f.gross_usd, f.basis, f.book_obs_id, f.book_observed_at,
       f.filled_at, o.eligible_at,
       (f.book_observed_at >= o.eligible_at) AS book_after_eligible,
       (f.book_obs_id IS DISTINCT FROM d.book_obs_id) AS not_the_decision_book,
       b.source AS book_source, b.error AS book_error
  FROM paper_fills f JOIN paper_orders o USING (order_id)
  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
  LEFT JOIN paper_book_observations b ON b.obs_id = f.book_obs_id
 WHERE f.strategy = 'PINNACLE_EXPLORATION_PAPER' ORDER BY f.filled_at;

\echo '== C4 · ledger rows of those orders =='
SELECT l.seq, l.kind, l.order_id, l.fill_id, l.cash_delta_usd,
       l.reserved_delta_usd, l.cash_after_usd, l.reserved_after_usd,
       l.data_label, l.committed_at
  FROM paper_ledger l
 WHERE l.order_id IN (SELECT order_id FROM paper_orders
                       WHERE strategy = 'PINNACLE_EXPLORATION_PAPER')
 ORDER BY l.seq;

\echo '== C5 · handoffs, Xavier reviews, protection =='
SELECT h.handoff_id, h.group_id, h.entry_order_id, h.first_fill_id,
       h.first_fill_at, h.owner, h.confirmed_qty, h.outstanding_qty,
       h.created_at
  FROM paper_handoffs h WHERE h.strategy = 'PINNACLE_EXPLORATION_PAPER'
 ORDER BY h.first_fill_at;
SELECT r.review_id, r.group_id, r.reviewed_at, r.trigger, r.recommendation,
       r.refusal, left(r.action::text, 400) AS action,
       left(r.measure::text, 300) AS measure
  FROM paper_xavier_reviews r WHERE r.strategy = 'PINNACLE_EXPLORATION_PAPER'
 ORDER BY r.reviewed_at LIMIT 30;
SELECT order_id, group_id, role, order_type, qty, limit_price, state,
       decided_at, expires_at
  FROM paper_orders WHERE strategy = 'PINNACLE_EXPLORATION_PAPER'
   AND role <> 'ENTRY' ORDER BY decided_at;

\echo '== C6 · Audrey findings on those groups =='
SELECT f.finding_id, f.kind, f.severity, f.subject, f.found_at,
       f.recorded_at, f.detail->>'passed' AS passed,
       left(f.detail::text, 700) AS detail
  FROM paper_audrey_findings f
 WHERE f.subject IN (SELECT group_id FROM paper_orders
                      WHERE strategy = 'PINNACLE_EXPLORATION_PAPER')
    OR f.kind LIKE 'PINNACLE_EXPLORATION%'
 ORDER BY f.recorded_at;

\echo '== C7 · duplicates per group =='
SELECT o.group_id,
       count(DISTINCT o.order_id) FILTER (WHERE o.role = 'ENTRY') AS entry_orders,
       (SELECT count(*) FROM paper_fills f WHERE f.group_id = o.group_id
          AND f.role = 'ENTRY') AS entry_fills,
       (SELECT count(*) FROM paper_ledger l JOIN paper_fills f USING (fill_id)
         WHERE f.group_id = o.group_id AND l.kind = 'FILL') AS fill_ledger_rows,
       (SELECT count(*) FROM paper_handoffs h WHERE h.group_id = o.group_id)
         AS handoffs,
       (SELECT count(*) FROM paper_decisions d
         WHERE d.decision_id = min(o.decision_id)) AS decisions
  FROM paper_orders o WHERE o.strategy = 'PINNACLE_EXPLORATION_PAPER'
 GROUP BY o.group_id ORDER BY 1;
SELECT kind, count(*) FROM paper_ledger
 WHERE account_id = 'paper_acct_main' GROUP BY 1 ORDER BY 1;
