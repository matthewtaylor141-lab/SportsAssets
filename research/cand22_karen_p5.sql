-- Karen identity, zero-authority controls, runner and evaluator; same-book progress (read-only).
\echo '== K1 · identities =='
SELECT agent_id, display_name FROM agent_identities ORDER BY agent_id;
\echo '== K2 · Karen guard triggers present =='
SELECT c.relname AS table_name, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
 WHERE NOT t.tgisinternal AND (t.tgname ILIKE '%karen%') ORDER BY 1, 2 LIMIT 40;
\echo '== K3 · challenges: state, evaluator, target (all) =='
SELECT target_agent, state, resolved_by, count(*) AS n, max(challenged_at) AS last_at
  FROM karen_challenges GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 12;
SELECT detector, count(*) AS n FROM karen_challenges GROUP BY 1 ORDER BY 2 DESC LIMIT 12;
\echo '== K4 · Karen heartbeat / status =='
SELECT service, status, beat_at FROM service_heartbeats WHERE service ILIKE '%karen%' OR service ILIKE '%peer%';
\echo '== P1 · same-book probe progress (since 02:44Z) =='
SELECT verdict, count(*) AS n, sum(orders_placed) AS orders_placed, max(probed_at) AS last_at
  FROM institutional_same_book_probe WHERE probed_at > '2026-10-04 02:44:00+00' GROUP BY 1 ORDER BY 2 DESC;
\echo '== P2 · stream process: reconnects / gaps since 02:44Z =='
SELECT max(connects_total) AS connects, max(reconnects_total) AS reconnects, max(recorded_at) AS last_at, count(*) AS rows
  FROM institutional_stream_evidence WHERE symbol = '*' AND recorded_at > '2026-10-04 02:44:00+00';
SELECT sum(jsonb_array_length(coalesce(gap_events, '[]'::jsonb))) AS gap_events, sum(regressions_in_minute) AS regressions
  FROM institutional_stream_evidence WHERE symbol <> '*' AND recorded_at > '2026-10-04 02:44:00+00';
