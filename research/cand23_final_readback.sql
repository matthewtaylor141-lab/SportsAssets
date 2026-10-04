-- Final readback on 4717460 (read-only).
\echo '== F1 · workers boot + writer build =='
SELECT value->>'commit_sha' AS workers_commit, value->>'at' AS boot_at, value->'not_started' AS not_started,
       left(value->>'lock_reason', 90) AS lock_reason FROM ingestion_state WHERE key = 'workers_boot';
SELECT value->'writer'->>'build' AS collector_writer_build, to_timestamp((value->>'at')::float8) AS at
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
\echo '== F2 · migrations =='
SELECT version, applied_at FROM schema_migrations WHERE version ~ '^(20[0-9]|21[0-9])' ORDER BY 1;
\echo '== F3 · coverage today (NY) latest snapshot =='
SELECT league, provider_events, normalized_events, venue_discovered, mapped_events, settlement_supported,
       evaluated_events, decided_events, entered_events, refused_events, ordered_events, filled_events,
       unavailable->>'settlement_supported' AS settlement_why, computed_at
  FROM coverage_funnel_snapshots WHERE tz = 'America/New_York' AND day = '2026-10-03'
 ORDER BY provider_events DESC NULLS LAST LIMIT 6;
SELECT count(*) AS alerts_since_release FROM coverage_collapse_alerts WHERE detected_at > '2026-10-04 03:33:00+00';
\echo '== F4 · same-book comparable samples and stream health since 02:44Z =='
SELECT verdict, count(*) FROM institutional_same_book_probe WHERE probed_at > '2026-10-04 02:44:00+00' GROUP BY 1 ORDER BY 2 DESC;
SELECT service, max(connects_total) connects, max(reconnects_total) reconnects, max(recorded_at) last_at
  FROM institutional_stream_evidence WHERE symbol = '*' AND recorded_at > '2026-10-04 02:44:00+00' GROUP BY 1;
\echo '== F5 · actual intents / orders and control =='
SELECT count(*) AS actual_orders_from_intents FROM execmirror_orders WHERE execution_intent_id IS NOT NULL;
SELECT count(*) AS submitted_intents FROM execution_intents WHERE actual_state NOT IN ('PAPER_ONLY', 'REFUSED');
SELECT enabled, stopped, left(account_fingerprint, 12) AS fp, scale, max_order_usd FROM execmirror_control;
\echo '== F6 · Xavier fresh reviews since 00:19Z =='
SELECT trigger, evidence_state, count(*) FROM xavier_management_assessments WHERE assessed_at > '2026-10-04 00:19:37+00'
 GROUP BY 1, 2 ORDER BY 3 DESC;
