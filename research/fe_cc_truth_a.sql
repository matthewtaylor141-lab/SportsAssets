-- READ ONLY. Frontend root-cause audit (group frontend), part A: what the
-- paper runtime and the paper book hold right now, i.e. what the Command
-- Center is able to show. SELECT statements only.
\echo == A1. paper pass last-attempt heartbeat (ingestion_state) ==
SELECT key,
       value->>'ran' AS ran,
       value->>'refusal' AS refusal,
       left(value->>'why', 200) AS why,
       to_timestamp((value->>'written_at')::float8) AS written_at,
       now() - to_timestamp((value->>'written_at')::float8) AS age,
       value->>'elapsed_s' AS elapsed_s,
       value->>'trigger' AS trig,
       value->>'at' AS at_field,
       length(value::text) AS value_chars,
       now() AS db_now
  FROM ingestion_state
 WHERE key = 'paper_session_last_pass';

\echo == A2. paper_session_health of the active session ==
SELECT h.session_id,
       h.heartbeat_at,
       now() - h.heartbeat_at AS heartbeat_age,
       h.passes,
       h.errors,
       left(h.last_error, 200) AS last_error,
       h.last_pass->>'ran' AS last_pass_ran,
       to_timestamp((h.last_pass->>'at')::float8) AS last_pass_at,
       h.last_pass->>'elapsed_s' AS last_pass_elapsed_s,
       jsonb_array_length(h.recent_heartbeats) AS beats_kept
  FROM paper_session_health h
  JOIN paper_sessions s ON s.session_id = h.session_id
 WHERE s.status = 'ACTIVE';

\echo == A3. newest recent_heartbeats (ring) ==
SELECT to_timestamp((x->>'at')::float8) AS beat_at,
       left(x::text, 260) AS beat
  FROM paper_session_health h
  JOIN paper_sessions s ON s.session_id = h.session_id,
       jsonb_array_elements(h.recent_heartbeats) AS x
 WHERE s.status = 'ACTIVE'
 ORDER BY 1 DESC NULLS LAST
 LIMIT 12;

\echo == A4. paper control and session ==
SELECT control_key, enabled, left(why, 120) AS why, updated_by, updated_at FROM paper_control;
SELECT session_id, account_id, started_at, status, stopped_at FROM paper_sessions ORDER BY started_at DESC LIMIT 3;

\echo == A5. ledger newest entries and totals ==
SELECT count(*) AS entries, max(seq) AS max_seq, max(committed_at) AS last_commit,
       now() - max(committed_at) AS commit_age
  FROM paper_ledger;
SELECT seq, kind, cash_delta_usd, cash_after_usd, reserved_after_usd, committed_at
  FROM paper_ledger ORDER BY seq DESC LIMIT 6;

\echo == A6. canonical open positions (open_position_canon rule) ==
SELECT count(*) AS open_positions
  FROM (
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
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
  ) c;

\echo == A7. fills and settlements totals and newest ==
SELECT count(*) AS fills, max(filled_at) AS last_fill, now() - max(filled_at) AS fill_age,
       count(DISTINCT group_id) AS groups
  FROM paper_fills;
SELECT count(*) AS settlements, max(settled_at) AS last_settlement FROM paper_settlements;

\echo == A8. decisions and orders recently ==
SELECT count(*) FILTER (WHERE decided_at > now() - interval '1 hour') AS dec_1h,
       count(*) FILTER (WHERE decided_at > now() - interval '24 hours') AS dec_24h,
       count(*) FILTER (WHERE verdict = 'ENTER' AND decided_at > now() - interval '24 hours') AS enter_24h,
       count(*) FILTER (WHERE verdict = 'ENTER' AND decided_at > now() - interval '1 hour') AS enter_1h,
       max(decided_at) AS latest_decision,
       now() - max(decided_at) AS latest_age
  FROM paper_decisions
 WHERE decided_at > now() - interval '3 days';
SELECT state, count(*) AS n, max(created_at) AS newest
  FROM paper_orders
 WHERE created_at > now() - interval '24 hours'
 GROUP BY state ORDER BY n DESC;
SELECT state, count(*) AS n FROM paper_orders
 WHERE state IN ('PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED', 'CANCEL_PENDING')
 GROUP BY state;

\echo == A9. Xavier reviews and handoffs newest ==
SELECT count(*) AS reviews_24h, max(reviewed_at) AS newest_review FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '24 hours';
SELECT count(*) AS handoffs, max(created_at) AS newest_handoff FROM paper_handoffs;
