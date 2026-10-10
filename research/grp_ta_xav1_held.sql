-- READ-ONLY. Root-cause audit, group truth-agents, part 2: why the PAPER book
-- holds nothing (held_positions_with_complete_current_packet is 0 of 0
-- UNMEASURED), when it last held something, and what stands between the
-- strategies and a new entry. SELECT only.

\echo == 1 paper_control, paper_sessions and the session heartbeat
SELECT control_key, enabled, why, updated_by, updated_at FROM paper_control ORDER BY control_key;
SELECT s.session_id, s.account_id, s.status, s.started_at, s.stopped_at, h.heartbeat_at,
       round(extract(epoch FROM (now() - h.heartbeat_at))::numeric, 1) AS hb_age_s,
       h.passes, h.errors, left(coalesce(h.last_error, ''), 300) AS last_error
  FROM paper_sessions s LEFT JOIN paper_session_health h ON h.session_id = s.session_id
 ORDER BY s.started_at DESC LIMIT 4;

\echo == 2 canonical open paper positions now
SELECT count(*) AS open_positions, coalesce(sum(open_qty), 0) AS open_qty FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9) x;

\echo == 3 paper fills per UTC day, last 9 days (BUY and SELL)
SELECT date_trunc('day', filled_at)::date AS day,
       count(*) FILTER (WHERE direction = 'BUY') AS buys,
       count(*) FILTER (WHERE direction = 'SELL') AS sells,
       count(DISTINCT group_id) FILTER (WHERE direction = 'BUY') AS groups_bought,
       max(filled_at) FILTER (WHERE direction = 'BUY') AS last_buy_at,
       max(filled_at) FILTER (WHERE direction = 'SELL') AS last_sell_at
  FROM paper_fills WHERE filled_at >= now() - interval '9 days'
 GROUP BY 1 ORDER BY 1;

\echo == 4 paper decisions per UTC day by verdict, last 5 days
SELECT date_trunc('day', decided_at)::date AS day, verdict, count(*) AS n, max(decided_at) AS newest
  FROM paper_decisions WHERE decided_at >= now() - interval '5 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 5 ENTER decisions with and without an order, last 48 h by hour (newest 24 rows)
SELECT date_trunc('hour', d.decided_at) AS hr, count(*) AS enter_decisions, count(o.order_id) AS with_order
  FROM paper_decisions d LEFT JOIN paper_orders o ON o.decision_id = d.decision_id
 WHERE d.verdict = 'ENTER' AND d.decided_at >= now() - interval '48 hours'
 GROUP BY 1 ORDER BY 1 DESC LIMIT 24;

\echo == 6 newest strategy lifecycle state per strategy
SELECT DISTINCT ON (account_id, strategy) account_id, strategy, to_state, rule_id, recorded_at, left(why, 220) AS why
  FROM paper_strategy_lifecycle_events ORDER BY account_id, strategy, event_id DESC;

\echo == 7 entry refusals (census) last 24 h by stage and refusal (top 25)
SELECT stage, refusal, count(*) AS n, max(refused_at) AS newest
  FROM paper_entry_refusal_census WHERE refused_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY n DESC LIMIT 25;

\echo == 8 paper audrey findings last 24 h by kind and severity
SELECT kind, severity, count(*) AS n, max(found_at) AS newest
  FROM paper_audrey_findings WHERE found_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY n DESC LIMIT 20;

\echo == 9 xavier packet population: handoffs, reviews, current reviews
SELECT count(*) AS handoffs, count(*) FILTER (WHERE outstanding_qty > 0) AS handoffs_with_outstanding_qty,
       max(updated_at) AS newest_update FROM paper_handoffs;
SELECT count(*) AS reviews, max(reviewed_at) AS newest_review,
       count(*) FILTER (WHERE reviewed_at >= now() - interval '24 hours') AS reviews_24h
  FROM paper_xavier_reviews;
SELECT position_kind, count(*) AS n, max(reviewed_at) AS newest FROM xavier_current_review GROUP BY 1;

\echo == 10 paper mark refresh runs: newest 5 (status columns only)
SELECT run_id, trigger, started_at, finished_at, held_markets, read_attempted, read_ok, read_failed, left(coalesce(error, ''), 160) AS error
  FROM paper_mark_refresh_runs ORDER BY run_id DESC LIMIT 5;
