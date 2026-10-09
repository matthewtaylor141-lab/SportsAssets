-- READ-ONLY. RC6.1 functional PAPER trading demonstration, read from
-- production's own tables after the deploy: what the engine decided (CASH
-- included), what it ordered, what filled, what is held and how Xavier
-- manages it, what settled and its P&L, and that SMALL LIVE stayed SHADOW
-- (no venue order). Every statement is a SELECT; nothing is simulated here.

\echo D1 RUNNING COMMIT per loop (the release under demonstration)
SELECT process, commit_sha, count(*) AS loops, max(last_success_at) AS newest_success
  FROM runtime_loop_health GROUP BY 1, 2 ORDER BY 1, 2;

\echo D2 DECISIONS last 6 h and 24 h by verdict (CASH is a decision)
SELECT verdict,
       count(*) FILTER (WHERE decided_at >= now() - interval '6 hours')  AS last_6h,
       count(*) FILTER (WHERE decided_at >= now() - interval '24 hours') AS last_24h,
       max(decided_at) AS newest
  FROM paper_decisions WHERE decided_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 3 DESC;

\echo D3 DECISIONS last 6 h: the refusals that kept the engine in CASH (top 15)
SELECT coalesce(refusal, '(ENTER)') AS refusal, count(*) AS n
  FROM paper_decisions WHERE decided_at >= now() - interval '6 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo D4 PAPER ORDERS last 24 h by role and state
SELECT role, state, count(*) AS orders, sum(qty) AS qty, sum(filled_qty) AS filled_qty,
       max(created_at) AS newest
  FROM paper_orders WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo D5 PAPER FILLS last 24 h by role and direction
SELECT role, direction, count(*) AS fills, sum(qty) AS contracts,
       round(sum(gross_usd)::numeric, 2) AS gross_usd, round(sum(fee_usd)::numeric, 2) AS fees_usd,
       max(filled_at) AS newest
  FROM paper_fills WHERE filled_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo D6 OPEN PAPER POSITIONS (canonical: bought - sold - latest settlement) with Xavier's latest review
WITH open AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills GROUP BY 1, 2, 3, 4) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9),
rev AS (
    SELECT DISTINCT ON (group_id) group_id, reviewed_at, recommendation, refusal,
           measure->>'evidence_state' AS evidence_state,
           (selection->'management_packet'->'gate'->>'complete') AS packet_complete,
           selection->'management_packet'->'gate'->'missing' AS packet_missing
      FROM paper_xavier_reviews
     WHERE reviewed_at >= now() - interval '24 hours'
     ORDER BY group_id, reviewed_at DESC)
SELECT o.us_market_slug, o.holding_side, o.open_qty, r.reviewed_at AS xavier_reviewed_at,
       r.recommendation, r.refusal, r.evidence_state, r.packet_complete, r.packet_missing
  FROM open o LEFT JOIN rev r ON r.group_id = o.group_id ORDER BY o.us_market_slug;

\echo D7 ONE LINKED TRACE: the newest ENTER decision -> its orders -> its fills -> Xavier
WITH d AS (SELECT decision_id, decided_at, us_market_slug, holding_side, p_pinnacle, p_blended,
                  proposed_qty, limit_price, policy_version
             FROM paper_decisions WHERE verdict = 'ENTER'
            ORDER BY decided_at DESC LIMIT 1)
SELECT 'decision' AS step, d.decided_at AS at, d.us_market_slug AS what,
       jsonb_build_object('side', d.holding_side, 'p_pinnacle', d.p_pinnacle, 'p_blended', d.p_blended,
                          'qty', d.proposed_qty, 'limit', d.limit_price, 'policy', d.policy_version) AS detail
  FROM d
UNION ALL
SELECT 'order', o.created_at, o.role || ' ' || o.state,
       jsonb_build_object('qty', o.qty, 'filled', o.filled_qty, 'limit', o.limit_price,
                          'tif', o.time_in_force, 'terminal', o.terminal_reason)
  FROM paper_orders o JOIN d ON o.decision_id = d.decision_id
UNION ALL
SELECT 'fill', f.filled_at, f.role || ' ' || f.direction,
       jsonb_build_object('qty', f.qty, 'price', f.price, 'fee', f.fee_usd, 'basis', f.basis)
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id JOIN d ON o.decision_id = d.decision_id
UNION ALL
SELECT 'xavier', x.reviewed_at, x.recommendation,
       jsonb_build_object('refusal', x.refusal, 'evidence', x.measure->>'evidence_state',
                          'packet_complete', x.selection->'management_packet'->'gate'->>'complete')
  FROM (SELECT DISTINCT ON (r.group_id) r.* FROM paper_xavier_reviews r
          JOIN paper_orders o ON o.group_id = r.group_id JOIN d ON o.decision_id = d.decision_id
         ORDER BY r.group_id, r.reviewed_at DESC) x
 ORDER BY 2;

\echo D8 SETTLEMENTS last 24 h and all time (latest version per position)
WITH s AS (SELECT DISTINCT ON (position_key) position_key, qty, outcome, payout_usd, settled_at
             FROM paper_settlements ORDER BY position_key, version DESC)
SELECT count(*) FILTER (WHERE settled_at >= now() - interval '24 hours') AS settled_24h,
       round(coalesce(sum(payout_usd) FILTER (WHERE settled_at >= now() - interval '24 hours'), 0)::numeric, 2) AS payout_24h,
       count(*) AS settled_all_time, round(coalesce(sum(payout_usd), 0)::numeric, 2) AS payout_all_time
  FROM s;

\echo D9 HISTORICAL PAPER (immutable): all-time fills and cash flows
SELECT count(*) AS fills_all_time,
       round(sum(CASE WHEN direction = 'BUY' THEN gross_usd ELSE -gross_usd END)::numeric, 2) AS net_bought_usd,
       round(sum(fee_usd)::numeric, 2) AS fees_usd, min(filled_at) AS first_fill, max(filled_at) AS last_fill
  FROM paper_fills;

\echo D10 SMALL LIVE = SHADOW: control state, execution intents by actual state (24 h), venue order events (24 h)
SELECT id, mode, halted, halt_reason, updated_at FROM small_live_control ORDER BY updated_at DESC NULLS LAST LIMIT 3;
SELECT actual_state, coalesce(actual_refusal, '-') AS actual_refusal, count(*)
  FROM execution_intents WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;
SELECT count(*) AS small_live_order_events_24h FROM small_live_order_events
 WHERE observed_at >= now() - interval '24 hours';
