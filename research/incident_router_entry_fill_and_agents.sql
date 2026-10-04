-- P0 INCIDENT (2026-10-04) -- ROUTER STREAM: why paper ENTER decisions do not
-- become fills, and the per-agent receipt inputs for Eddie / Karen / Allie /
-- Xavier. READ-ONLY; every statement is bounded by a time window and every
-- row dump has a LIMIT.
--
-- HYPOTHESES UNDER TEST (code at 191b299):
--   H1 bettor_paper_simulator._marketable takes the FIRST observation in
--      [eligible_at, expires_at] even when it is an ERRORED read (our own
--      venue-cooldown / deadline refusal) and expires the order on it.
--   H2 the read made for a pending entry after its 2 s delay is answered by
--      the 6 s shared-read cache with a read received BEFORE eligible_at, so it
--      is recorded outside the order's window and wasted.
--   H3 the books step reads a marketable entry before it is eligible.

\echo == E0 clock ==
SELECT now() AS db_now,
       (SELECT max(created_at) FROM paper_orders) AS last_order,
       (SELECT max(observed_at) FROM paper_book_observations) AS last_obs;

\echo == E1 ENTRY marketable orders (7 d) by strategy x state x terminal_reason ==
SELECT o.strategy, o.state, coalesce(o.terminal_reason, '-') AS terminal_reason,
       count(*) AS orders, count(DISTINCT o.us_market_slug) AS markets
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.order_type = 'MARKETABLE'
   AND o.created_at > now() - interval '7 days'
 GROUP BY 1, 2, 3
 ORDER BY 1, orders DESC
 LIMIT 60;

\echo == E2 per ENTRY order (7 d): the observations of its market around its window ==
WITH o AS (
  SELECT o.order_id, o.strategy, o.state, coalesce(o.terminal_reason, '-') AS tr,
         o.us_market_slug, o.decided_at, o.eligible_at, o.expires_at
    FROM paper_orders o
   WHERE o.role = 'ENTRY' AND o.order_type = 'MARKETABLE'
     AND o.created_at > now() - interval '7 days'),
b AS (
  SELECT o.order_id,
         count(*) FILTER (WHERE b.observed_at < o.eligible_at
                            AND b.recorded_at >= o.decided_at) AS recorded_after_decision_but_observed_before_eligible,
         count(*) FILTER (WHERE b.read_basis = 'ENTRY_AFTER_DELAY'
                            AND b.recorded_at >= o.decided_at
                            AND b.recorded_at <= o.expires_at) AS after_delay_reads,
         count(*) FILTER (WHERE b.read_basis = 'ENTRY_AFTER_DELAY'
                            AND b.recorded_at >= o.decided_at
                            AND b.recorded_at <= o.expires_at
                            AND b.observed_at < o.eligible_at) AS after_delay_reads_observed_before_eligible,
         count(*) FILTER (WHERE b.read_basis = 'ENTRY_AFTER_DELAY'
                            AND b.recorded_at >= o.decided_at
                            AND b.recorded_at <= o.expires_at
                            AND b.source LIKE '%SHARED_READ') AS after_delay_shared_reads,
         count(*) FILTER (WHERE b.read_basis = 'OPEN_ORDER_OR_POSITION'
                            AND b.recorded_at >= o.decided_at
                            AND b.recorded_at < o.eligible_at) AS books_step_reads_before_eligible,
         count(*) FILTER (WHERE b.observed_at >= o.eligible_at
                            AND b.observed_at <= o.expires_at) AS in_window,
         count(*) FILTER (WHERE b.observed_at >= o.eligible_at
                            AND b.observed_at <= o.expires_at
                            AND b.error IS NULL) AS in_window_readable,
         min(b.observed_at) FILTER (WHERE b.observed_at >= o.eligible_at
                                      AND b.observed_at <= o.expires_at) AS first_in_window,
         min(b.observed_at) FILTER (WHERE b.observed_at >= o.eligible_at
                                      AND b.observed_at <= o.expires_at
                                      AND b.error IS NULL) AS first_readable_in_window
    FROM o
    JOIN paper_book_observations b
      ON b.us_market_slug = o.us_market_slug
     AND b.observed_at >= o.decided_at - interval '10 seconds'
     AND b.observed_at <= o.expires_at
   GROUP BY o.order_id)
SELECT o.strategy, o.state, o.tr AS terminal_reason, count(*) AS orders,
       count(*) FILTER (WHERE coalesce(b.after_delay_reads, 0) > 0) AS with_after_delay_read,
       count(*) FILTER (WHERE coalesce(b.after_delay_reads_observed_before_eligible, 0) > 0) AS after_delay_read_observed_before_eligible,
       count(*) FILTER (WHERE coalesce(b.after_delay_shared_reads, 0) > 0) AS after_delay_shared,
       count(*) FILTER (WHERE coalesce(b.books_step_reads_before_eligible, 0) > 0) AS books_step_read_before_eligible,
       count(*) FILTER (WHERE coalesce(b.in_window, 0) = 0) AS no_obs_in_window,
       count(*) FILTER (WHERE coalesce(b.in_window, 0) > 0 AND coalesce(b.in_window_readable, 0) = 0) AS only_errored_in_window,
       count(*) FILTER (WHERE b.first_readable_in_window > b.first_in_window) AS errored_first_then_readable,
       round(avg(extract(epoch FROM b.first_in_window - o.eligible_at))::numeric, 2) AS avg_first_obs_after_eligible_s,
       round(avg(extract(epoch FROM b.first_readable_in_window - o.eligible_at))::numeric, 2) AS avg_first_readable_after_eligible_s
  FROM o LEFT JOIN b USING (order_id)
 GROUP BY 1, 2, 3
 ORDER BY 1, orders DESC
 LIMIT 60;

\echo == E3 the errored observations inside ENTRY windows (7 d): which read failed, by whom ==
SELECT o.strategy, b.read_basis, b.source, coalesce(b.error, 'READABLE') AS err,
       count(*) AS observations, count(DISTINCT o.order_id) AS orders
  FROM paper_orders o
  JOIN paper_book_observations b
    ON b.us_market_slug = o.us_market_slug
   AND b.observed_at >= o.eligible_at AND b.observed_at <= o.expires_at
 WHERE o.role = 'ENTRY' AND o.order_type = 'MARKETABLE'
   AND o.created_at > now() - interval '7 days'
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, observations DESC
 LIMIT 60;

\echo == E4 orders expired on an unreadable FIRST observation that later had a READABLE one in window (7 d): the book to replay the walk offline ==
WITH o AS (
  SELECT o.order_id, o.strategy, o.holding_side, o.direction, o.qty,
         o.limit_price, o.allow_partial, o.us_market_slug, o.eligible_at,
         o.expires_at
    FROM paper_orders o
   WHERE o.role = 'ENTRY' AND o.order_type = 'MARKETABLE'
     AND o.terminal_reason = 'THE_OBSERVED_BOOK_WAS_UNREADABLE'
     AND o.created_at > now() - interval '7 days')
SELECT o.order_id, o.strategy, o.holding_side, o.direction, o.qty::float8 AS qty,
       o.limit_price::float8 AS limit_price, o.allow_partial,
       r.obs_id, round(extract(epoch FROM r.observed_at - o.eligible_at)::numeric, 2) AS s_after_eligible,
       r.offers, r.bids
  FROM o
  CROSS JOIN LATERAL (
    SELECT b.obs_id, b.observed_at, b.offers, b.bids
      FROM paper_book_observations b
     WHERE b.us_market_slug = o.us_market_slug
       AND b.observed_at >= o.eligible_at AND b.observed_at <= o.expires_at
       AND b.error IS NULL
     ORDER BY b.observed_at LIMIT 1) r
 ORDER BY o.order_id
 LIMIT 40;

\echo == E5 the same orders: the decision-time book they were priced on (the entry decision's own book_obs_id) ==
SELECT o.order_id, o.strategy, o.holding_side, o.qty::float8 AS qty,
       o.limit_price::float8 AS limit_price,
       round(extract(epoch FROM o.eligible_at - b.observed_at)::numeric, 2) AS s_before_eligible,
       b.offers, b.bids
  FROM paper_orders o
  JOIN paper_decisions d ON d.decision_id = o.decision_id
  JOIN paper_book_observations b ON b.obs_id = d.book_obs_id
 WHERE o.role = 'ENTRY' AND o.order_type = 'MARKETABLE'
   AND o.terminal_reason = 'THE_OBSERVED_BOOK_WAS_UNREADABLE'
   AND o.created_at > now() - interval '7 days'
 ORDER BY o.order_id
 LIMIT 80;

\echo == E6 ENTRY decisions (7 d): seconds from decision to the first READABLE observation of the market at or after eligible, by order outcome ==
SELECT o.strategy, o.state,
       count(*) AS orders,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM fr.observed_at - o.eligible_at)) AS p50_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM fr.observed_at - o.eligible_at)) AS p90_s,
       count(*) FILTER (WHERE fr.observed_at IS NULL) AS none_within_15_min
  FROM paper_orders o
  LEFT JOIN LATERAL (
    SELECT b.observed_at FROM paper_book_observations b
     WHERE b.us_market_slug = o.us_market_slug
       AND b.observed_at >= o.eligible_at
       AND b.observed_at <= o.eligible_at + interval '15 minutes'
       AND b.error IS NULL
     ORDER BY b.observed_at LIMIT 1) fr ON true
 WHERE o.role = 'ENTRY' AND o.order_type = 'MARKETABLE'
   AND o.created_at > now() - interval '7 days'
 GROUP BY 1, 2
 ORDER BY 1, 2
 LIMIT 40;

\echo == A1 EDDIE (24 h and 7 d): ENTER decisions he receives, estimates by recommendation ==
SELECT w.win,
       (SELECT count(*) FROM paper_decisions d WHERE d.verdict = 'ENTER'
         AND d.decided_at > now() - w.iv) AS enter_decisions,
       (SELECT count(DISTINCT e.decision_id) FROM eddie_execution_estimates e
          JOIN paper_decisions d ON d.decision_id = e.decision_id
         WHERE d.decided_at > now() - w.iv) AS estimated,
       (SELECT count(*) FROM paper_decisions d WHERE d.verdict = 'ENTER'
         AND d.decided_at > now() - w.iv
         AND NOT EXISTS (SELECT 1 FROM eddie_execution_estimates e
                          WHERE e.decision_id = d.decision_id)) AS not_estimated
  FROM (VALUES ('24h', interval '24 hours'), ('7d', interval '7 days')) AS w(win, iv);

SELECT e.recommendation, e.recommendation_reason,
       count(*) AS estimates,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = e.decision_id)) AS with_order,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_fills f JOIN paper_orders o USING (order_id) WHERE o.decision_id = e.decision_id)) AS with_fill,
       round(avg(extract(epoch FROM e.estimated_at - e.decided_at))::numeric, 1) AS avg_lag_s
  FROM eddie_execution_estimates e
 WHERE e.estimated_at > now() - interval '7 days'
 GROUP BY 1, 2
 ORDER BY estimates DESC
 LIMIT 40;

\echo == A2 the candidate-review workflow (7 d): per step x agent x status ==
SELECT s.step, s.agent, s.status, count(*) AS steps,
       count(DISTINCT r.decision_id) AS decisions
  FROM pos_candidate_review_steps s
  JOIN pos_candidate_reviews r USING (review_id)
 WHERE s.at > now() - interval '7 days'
 GROUP BY 1, 2, 3
 ORDER BY 1, 3
 LIMIT 60;

\echo == A3 KAREN (7 d): challenges by target x detector x state x outcome ==
SELECT k.target_agent, k.detector, k.state, coalesce(k.outcome, '-') AS outcome,
       count(*) AS challenges, count(DISTINCT k.target_id) AS targets
  FROM karen_challenges k
 WHERE k.challenged_at > now() - interval '7 days'
 GROUP BY 1, 2, 3, 4
 ORDER BY challenges DESC
 LIMIT 60;

\echo == A4 XAVIER (24 h): groups handed off / held, reviews by trigger x recommendation x refusal ==
SELECT (SELECT count(*) FROM paper_handoffs h WHERE h.created_at > now() - interval '24 hours') AS handoffs_24h,
       (SELECT count(DISTINCT h.group_id) FROM paper_handoffs h) AS handoffs_all,
       (SELECT count(DISTINCT r.group_id) FROM paper_xavier_reviews r WHERE r.reviewed_at > now() - interval '24 hours') AS groups_reviewed_24h,
       (SELECT count(*) FROM paper_xavier_reviews r WHERE r.reviewed_at > now() - interval '24 hours') AS reviews_24h;

SELECT r.trigger, coalesce(r.recommendation, '-') AS recommendation,
       coalesce(split_part(r.refusal, ':', 1), '-') AS refusal,
       count(*) AS reviews, count(DISTINCT r.group_id) AS groups
  FROM paper_xavier_reviews r
 WHERE r.reviewed_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY reviews DESC
 LIMIT 60;

SELECT o.role, o.order_type, o.state, coalesce(o.terminal_reason, '-') AS terminal_reason,
       count(*) AS orders, count(*) FILTER (WHERE o.filled_qty > 0) AS with_fill
  FROM paper_orders o
 WHERE o.role <> 'ENTRY' AND o.created_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4
 ORDER BY orders DESC
 LIMIT 40;

\echo == A5 attempts (24 h) per strategy x outcome: offered vs decided (the router itself) ==
SELECT a.strategy, a.via, a.outcome, count(*) AS attempts,
       count(DISTINCT a.valuation_id) AS valuations
  FROM paper_evaluation_attempts a
 WHERE a.at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, attempts DESC
 LIMIT 60;

\echo == A6 entry valuations (24 h) not decided by an enabled strategy, per strategy ==
SELECT s.strategy,
       count(*) AS valuations,
       count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM paper_decisions d
          WHERE d.valuation_id = v.id AND d.strategy = s.strategy)) AS not_decided
  FROM external_valuations v
 CROSS JOIN (VALUES ('PINNACLE_COMPLETED_GAME_PAPER'), ('PINNACLE_EXPLORATION_PAPER'),
                    ('DEREK_ENTRY_POLICY_V2')) AS s(strategy)
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND v.decided_at > now() - interval '24 hours'
   AND v.us_market_slug IS NOT NULL
 GROUP BY 1
 ORDER BY 1;

\echo == L1 the live slate: venue full-game winner events starting in the next 12 h, by sports_type, and how far each got ==
WITH ev AS (
  SELECT DISTINCT p.sports_type, p.market_slug
    FROM us_premap p
   WHERE p.game_start >= now() AND p.game_start < now() + interval '12 hours'
     AND p.sports_type LIKE '%winner%'),
val AS (
  SELECT DISTINCT v.us_market_slug FROM external_valuations v
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.decided_at > now() - interval '24 hours'),
dec AS (
  SELECT d.us_market_slug, d.strategy, bool_or(d.verdict = 'ENTER') AS entered
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '24 hours'
   GROUP BY 1, 2)
SELECT ev.sports_type, count(*) AS venue_markets,
       count(*) FILTER (WHERE ev.market_slug IN (SELECT us_market_slug FROM val)) AS valued,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM dec WHERE dec.us_market_slug = ev.market_slug
                                        AND dec.strategy = 'PINNACLE_COMPLETED_GAME_PAPER')) AS cg_decided,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM dec WHERE dec.us_market_slug = ev.market_slug
                                        AND dec.entered)) AS entered_any
  FROM ev
 GROUP BY 1
 ORDER BY venue_markets DESC
 LIMIT 20;
