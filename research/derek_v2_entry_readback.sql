-- Derek exploration V2 ($1,000 per entry incl. fees) readback. Read-only.
-- Running session, the strategy's limits as recorded on its latest decision,
-- the first V2 entry end to end (reservation -> order -> fills -> ledger
-- debit -> Xavier handoff and review -> Audrey findings), and the aggregate
-- headroom. Simulated execution against live market data; training only.

\echo '== V0 · running paper session (one ACTIVE per account) =='
SELECT session_id, status, started_at, config_sha, simulator_version,
       left(config::text, 1200) AS config_head
  FROM paper_sessions WHERE account_id = 'paper_acct_main' AND status = 'ACTIVE';

\echo '== V1 · exploration decisions by policy version and verdict, last 6 h =='
SELECT policy_version, verdict, count(*) AS n, max(decided_at) AS latest
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND strategy = 'PINNACLE_EXPLORATION_PAPER'
   AND decided_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== V2 · the limits the strategy itself recorded on its latest V2 decision =='
SELECT decision_id, decided_at, verdict, refusal,
       policy_decision->'limits' AS limits_at_decision,
       (SELECT c FROM jsonb_array_elements(policy_decision->'conditions') c
         WHERE c->>'condition' = 'entry_cost_incl_fees_within_budget') AS entry_budget_condition
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND policy_version = 'PINNACLE_EXPLORATION_PAPER_V2'
 ORDER BY decided_at DESC LIMIT 1;

\echo '== V3 · first V2 entry: decision, probability source, reservation and order =='
WITH d AS (
  SELECT d.* FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main'
     AND d.policy_version = 'PINNACLE_EXPLORATION_PAPER_V2'
     AND EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id AND o.role = 'ENTRY')
   ORDER BY d.decided_at LIMIT 1)
SELECT d.decision_id, d.decided_at, d.us_market_slug, d.holding_side, d.fixture,
       d.p_pinnacle, d.pinnacle->>'source' AS p_source, d.pinnacle->>'book' AS p_book,
       d.pinnacle->>'age_s' AS p_age_s, d.book_obs_id,
       d.policy_decision->'estimate' AS estimate,
       o.order_id, o.group_id, o.state, o.qty, o.filled_qty, o.limit_price, o.wire_price,
       o.reserved_usd, o.reserved_remaining_usd, o.order_type, o.time_in_force
  FROM d JOIN paper_orders o ON o.decision_id = d.decision_id AND o.role = 'ENTRY';

\echo '== V4 · its fills: executed quantity, price, fees, gross, source book observation =='
WITH o AS (
  SELECT o.* FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
   WHERE d.account_id = 'paper_acct_main' AND d.policy_version = 'PINNACLE_EXPLORATION_PAPER_V2'
     AND o.role = 'ENTRY' ORDER BY d.decided_at LIMIT 1)
SELECT f.fill_id, f.filled_at, f.qty, f.price, f.wire_price, f.fee_usd, f.gross_usd,
       round(f.gross_usd + f.fee_usd, 6) AS cost_incl_fee_usd,
       f.book_obs_id, f.book_observed_at, f.basis
  FROM o JOIN paper_fills f ON f.order_id = o.order_id ORDER BY f.filled_at;

\echo '== V5 · its ledger rows: reservation, fill debit, release (cash and reserved deltas) =='
WITH o AS (
  SELECT o.* FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
   WHERE d.account_id = 'paper_acct_main' AND d.policy_version = 'PINNACLE_EXPLORATION_PAPER_V2'
     AND o.role = 'ENTRY' ORDER BY d.decided_at LIMIT 1)
SELECT l.seq, l.kind, l.cash_delta_usd, l.reserved_delta_usd, l.cash_after_usd,
       l.reserved_after_usd, l.fill_id, l.committed_at, l.data_label
  FROM o JOIN paper_ledger l ON l.order_id = o.order_id ORDER BY l.seq;

\echo '== V6 · Xavier handoff and first reviews of that group =='
WITH o AS (
  SELECT o.* FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
   WHERE d.account_id = 'paper_acct_main' AND d.policy_version = 'PINNACLE_EXPLORATION_PAPER_V2'
     AND o.role = 'ENTRY' ORDER BY d.decided_at LIMIT 1)
SELECT 'HANDOFF' AS rec, h.handoff_id AS id, h.first_fill_at AS at, h.owner AS who,
       h.confirmed_qty::text AS a, h.outstanding_qty::text AS b
  FROM o JOIN paper_handoffs h ON h.group_id = o.group_id
UNION ALL
SELECT 'XAVIER_REVIEW', r.review_id, r.reviewed_at, r.trigger, r.recommendation, r.refusal
  FROM o JOIN paper_xavier_reviews r ON r.group_id = o.group_id
 ORDER BY at LIMIT 8;

\echo '== V7 · Audrey findings that name that group or its order =='
WITH o AS (
  SELECT o.* FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
   WHERE d.account_id = 'paper_acct_main' AND d.policy_version = 'PINNACLE_EXPLORATION_PAPER_V2'
     AND o.role = 'ENTRY' ORDER BY d.decided_at LIMIT 1)
SELECT a.finding_id, a.found_at, a.kind, a.severity, a.subject, left(a.detail::text, 400) AS detail
  FROM o JOIN paper_audrey_findings a
    ON a.subject IN (o.group_id, o.order_id) OR a.detail::text LIKE '%' || o.group_id || '%'
 ORDER BY a.found_at LIMIT 8;

\echo '== V8 · exploration exposure now: open reservations, entries by version, per-fixture count =='
SELECT coalesce(sum(o.reserved_remaining_usd) FILTER (
         WHERE o.direction = 'BUY' AND o.state IN ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED','CANCEL_PENDING')), 0)
         AS open_reservations_usd,
       count(*) FILTER (WHERE o.role = 'ENTRY') AS entry_orders,
       count(DISTINCT o.fixture) FILTER (WHERE o.role = 'ENTRY') AS entry_fixtures,
       max(o.reserved_usd) FILTER (WHERE o.role = 'ENTRY' AND o.decided_at > now() - interval '6 hours') AS max_entry_reservation_6h_usd
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main' AND o.strategy = 'PINNACLE_EXPLORATION_PAPER';
