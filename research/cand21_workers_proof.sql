-- Workers lock proof (read-only): the full boot marker, and whether the legacy
-- order-capable loops' heartbeats and the funded mirror order are still moving.
\echo '== now =='
SELECT now() AS db_now;
\echo '== workers_boot (full) =='
SELECT jsonb_pretty(value::jsonb) FROM ingestion_state WHERE key = 'workers_boot';
\echo '== legacy order-capable loops: last heartbeat =='
SELECT service, status, beat_at, extract(epoch FROM now() - beat_at)::int AS age_s
  FROM service_heartbeats
 WHERE service IN ('mirror_live', 'copy_sweep', 'underdog', 'whale_exits', 'poller',
                   'chain_listener', 'institutional_md')
 ORDER BY service;
\echo '== funded mirror order 7419 last touched =='
SELECT id, state, updated_at, extract(epoch FROM now() - updated_at)::int AS age_s
  FROM mirror_orders WHERE id = 7419;
\echo '== live_orders / mirror_orders written since the workers deploy =='
SELECT (SELECT count(*) FROM live_orders WHERE placed_at > '2026-10-03 15:26:20+00') AS live_orders_new,
       (SELECT count(*) FROM mirror_orders WHERE placed_at > '2026-10-03 15:26:20+00') AS mirror_orders_new,
       (SELECT count(*) FROM mirror_orders WHERE updated_at > '2026-10-03 15:28:30+00') AS mirror_orders_touched_after_handoff;
