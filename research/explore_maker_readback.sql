-- READ-ONLY. THE PAPER EXPERIMENT AFTER THE EXPLORATION / MAKER / THROUGHPUT
-- RELEASE. Every row is a persisted record; nothing here writes.
-- R0 migration 189 + the two new controls + the session and its funding
-- R1 evaluation attempts per hour by strategy, via and outcome (every attempt)
-- R2 book reads per hour: total, shared, unreadable, deadline-cut (vs baseline)
-- R3 decisions per hour by strategy: decided, book-deadline refusals, entered
-- R4 refusal breakdown per strategy (last 6 h)
-- R5 the first and latest resting maker entry orders, with rationale + expiry
-- R6 every exploration decision (estimate, fees, selection, purpose)
-- R7 every order of the two new strategies with its events
-- R8 every fill since the release, its ledger rows and its handoff
-- R9 Xavier's reviews of the new strategies' groups
-- R10 Audrey: fill-chain findings + operational recommendations and events
-- R11 the account: cash, reserved, ledger by kind, by strategy
\echo '== R0 · migration 189, controls, session, funding =='
SELECT version, applied_at FROM schema_migrations
 WHERE version::text LIKE '189%' OR version::text LIKE '188%' ORDER BY 1;
SELECT control_key, enabled, why, updated_by, updated_at FROM paper_control
 ORDER BY control_key;
SELECT session_id, account_id, status, started_at, stopped_at FROM paper_sessions
 ORDER BY started_at DESC LIMIT 3;
SELECT seq, kind, cash_delta_usd, committed_at FROM paper_ledger
 WHERE kind = 'INITIAL_FUNDING' ORDER BY seq;

\echo '== R1 · evaluation attempts per hour (last 6 h) =='
SELECT date_trunc('hour', at) AS hour, strategy, via, outcome, count(*),
       round(avg(elapsed_s)::numeric, 2) AS avg_elapsed_s,
       count(*) FILTER (WHERE book_source LIKE '%SHARED%'
                           OR book_source LIKE 'REUSED%') AS book_shared
  FROM paper_evaluation_attempts
 WHERE at > now() - interval '6 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1 DESC, 2, 5 DESC;

\echo '== R1b · book sources on attempts (last 6 h) =='
SELECT strategy, coalesce(book_source, '(none)') AS book_source, count(*)
  FROM paper_evaluation_attempts
 WHERE at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== R2 · book reads per hour (last 8 h) =='
SELECT date_trunc('hour', observed_at) AS hour, count(*) AS reads,
       count(*) FILTER (WHERE source LIKE '%SHARED_READ%') AS shared,
       count(*) FILTER (WHERE error IS NOT NULL) AS unreadable,
       count(*) FILTER (WHERE error ILIKE '%DEADLINE%') AS deadline_cut,
       count(*) FILTER (WHERE error ILIKE '%COOLDOWN%' OR error ILIKE '%429%')
         AS cooldown_or_429
  FROM paper_book_observations
 WHERE observed_at > now() - interval '8 hours'
 GROUP BY 1 ORDER BY 1 DESC;

\echo '== R3 · decisions per hour by strategy (last 8 h) =='
SELECT date_trunc('hour', decided_at) AS hour, strategy, count(*) AS decisions,
       count(*) FILTER (WHERE refusal =
         'BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE') AS book_cut,
       count(*) FILTER (WHERE book_obs_id IS NOT NULL) AS with_book,
       count(*) FILTER (WHERE verdict = 'ENTER') AS entered
  FROM paper_decisions
 WHERE decided_at > now() - interval '8 hours'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2;

\echo '== R4 · refusals per strategy (last 6 h) =='
SELECT strategy, coalesce(refusal, verdict) AS reason, count(*)
  FROM paper_decisions
 WHERE decided_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== R5 · resting maker entry orders: first and latest 10 =='
(SELECT 'FIRST' AS which, order_id, us_market_slug, fixture, order_type,
        time_in_force, qty, limit_price, wire_price, reserved_usd,
        reserved_remaining_usd, filled_qty, state, decided_at, eligible_at,
        expires_at, queue_ahead_qty,
        left((label->'rationale')::text, 400) AS rationale,
        left((label->'cancel_conditions')::text, 300) AS cancel_conditions
   FROM paper_orders
  WHERE strategy = 'PINNACLE_COMPLETED_GAME_MAKER_PAPER' AND role = 'ENTRY'
  ORDER BY decided_at LIMIT 1)
UNION ALL
(SELECT 'LATEST', order_id, us_market_slug, fixture, order_type,
        time_in_force, qty, limit_price, wire_price, reserved_usd,
        reserved_remaining_usd, filled_qty, state, decided_at, eligible_at,
        expires_at, queue_ahead_qty,
        left((label->'rationale')::text, 400),
        left((label->'cancel_conditions')::text, 300)
   FROM paper_orders
  WHERE strategy = 'PINNACLE_COMPLETED_GAME_MAKER_PAPER'
  ORDER BY decided_at DESC LIMIT 10);

\echo '== R5b · maker decisions (last 6 h) with the resting price =='
SELECT decision_id, decided_at, us_market_slug, verdict, refusal,
       limit_price, proposed_qty, left(economics::text, 500) AS economics,
       left(label::text, 500) AS label
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_COMPLETED_GAME_MAKER_PAPER'
   AND decided_at > now() - interval '6 hours'
 ORDER BY (verdict = 'ENTER') DESC, decided_at DESC LIMIT 12;

\echo '== R6 · exploration decisions (all, newest first, 25) =='
SELECT decision_id, decided_at, us_market_slug, fixture, verdict, refusal,
       limit_price, proposed_qty, left(economics::text, 500) AS economics,
       left(label::text, 700) AS label, left(provenance::text, 300) AS provenance
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_EXPLORATION_PAPER'
 ORDER BY (verdict = 'ENTER') DESC, decided_at DESC LIMIT 25;
SELECT verdict, coalesce(refusal, '-') AS refusal, count(*)
  FROM paper_decisions WHERE strategy = 'PINNACLE_EXPLORATION_PAPER'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== R7 · orders of the new strategies and their events =='
SELECT o.strategy, o.order_id, o.role, o.us_market_slug, o.order_type,
       o.time_in_force, o.qty, o.limit_price, o.filled_qty, o.state,
       o.reserved_usd, o.decided_at, o.expires_at
  FROM paper_orders o
 WHERE o.strategy IN ('PINNACLE_COMPLETED_GAME_MAKER_PAPER',
                      'PINNACLE_EXPLORATION_PAPER')
 ORDER BY o.decided_at DESC LIMIT 30;
SELECT e.order_id, e.* FROM paper_order_events e
  JOIN paper_orders o USING (order_id)
 WHERE o.strategy IN ('PINNACLE_COMPLETED_GAME_MAKER_PAPER',
                      'PINNACLE_EXPLORATION_PAPER')
 ORDER BY 1 LIMIT 60;

\echo '== R8 · fills since the release, ledger rows, handoffs =='
SELECT f.strategy, f.fill_id, f.order_id, f.role, f.direction,
       f.us_market_slug, f.qty, f.price, f.fee_usd, f.gross_usd, f.basis,
       f.book_observed_at, f.filled_at, left(f.evidence::text, 500) AS evidence
  FROM paper_fills f
 WHERE f.filled_at > now() - interval '24 hours'
 ORDER BY f.filled_at LIMIT 30;
SELECT l.seq, l.kind, l.cash_delta_usd, l.reserved_delta_usd,
       l.cash_after_usd, l.reserved_after_usd, l.order_id, l.fill_id,
       l.data_label, l.committed_at
  FROM paper_ledger l
 WHERE l.kind <> 'INITIAL_FUNDING'
 ORDER BY l.seq DESC LIMIT 40;
SELECT h.strategy, h.handoff_id, h.group_id, h.entry_order_id,
       h.first_fill_id, h.first_fill_at, h.owner, h.confirmed_qty,
       h.outstanding_qty
  FROM paper_handoffs h ORDER BY h.first_fill_at DESC LIMIT 20;

\echo '== R9 · Xavier reviews of the new strategies =='
SELECT r.strategy, r.review_id, r.group_id, r.reviewed_at, r.trigger,
       r.recommendation, r.refusal, left(r.action::text, 300) AS action,
       left(r.confirmed_protection::text, 300) AS protection
  FROM paper_xavier_reviews r
 WHERE r.strategy IN ('PINNACLE_COMPLETED_GAME_MAKER_PAPER',
                      'PINNACLE_EXPLORATION_PAPER',
                      'PINNACLE_COMPLETED_GAME_PAPER')
 ORDER BY r.reviewed_at DESC LIMIT 20;

\echo '== R10 · Audrey: findings (6 h) and operational recommendations =='
SELECT kind, severity, count(*), max(found_at) AS latest
  FROM paper_audrey_findings
 WHERE found_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 4 DESC;
SELECT recommendation_id, owner_agent, category, kind, metric, status,
       created_at, updated_at, left(recommendation, 220) AS recommendation,
       left(baseline::text, 300) AS baseline
  FROM paper_recommendations ORDER BY created_at DESC LIMIT 20;
SELECT recommendation_id, at, actor, kind, left(body, 260) AS body
  FROM paper_recommendation_events ORDER BY event_id DESC LIMIT 40;

\echo '== R11 · the account =='
SELECT account_id, starting_cash_usd,
       (SELECT cash_after_usd FROM paper_ledger l WHERE l.account_id = a.account_id
         ORDER BY seq DESC LIMIT 1) AS cash_usd,
       (SELECT reserved_after_usd FROM paper_ledger l
         WHERE l.account_id = a.account_id ORDER BY seq DESC LIMIT 1) AS reserved_usd
  FROM paper_accounts a;
SELECT kind, count(*), sum(cash_delta_usd) AS cash, sum(reserved_delta_usd)
       AS reserved
  FROM paper_ledger GROUP BY 1 ORDER BY 1;
SELECT o.strategy, count(DISTINCT o.order_id) AS orders,
       count(DISTINCT f.fill_id) AS fills,
       coalesce(sum(f.fee_usd), 0) AS fees_usd
  FROM paper_orders o LEFT JOIN paper_fills f USING (order_id)
 GROUP BY 1 ORDER BY 1;
