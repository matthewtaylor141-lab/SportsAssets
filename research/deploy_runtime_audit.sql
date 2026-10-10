-- READ-ONLY. Root-cause audit, group deploy-runtime: (a) the applied migration
-- history of production against the repository (224 applied, highest 316), (b)
-- the public table inventory (schema drift: tables no migration creates), (c)
-- the service heartbeats (which commit each long-running process reports and
-- how old the beat is), (d) the existing tables that the migrations of the
-- RC6.3b candidate and of the open lanes (317, 366, 367, 368) would act on:
-- estimated rows, size, and the triggers they already carry. SELECT only.

\echo == 1 schema_migrations summary
SELECT count(*) AS applied, min(version) AS first_version, max(version) AS last_version,
       count(*) FILTER (WHERE content_sha IS NULL) AS without_content_sha,
       min(applied_at) AS first_applied_at, max(applied_at) AS last_applied_at
  FROM schema_migrations;

\echo == 2 applied versions from 300 upward, with the first 12 characters of the recorded content hash
SELECT version, applied_at, left(content_sha, 12) AS content_sha12
  FROM schema_migrations WHERE version >= '300' ORDER BY version;

\echo == 3 versions applied out of version order (applied_at earlier than the previous version)
SELECT count(*) AS out_of_order_applications
  FROM (SELECT version, applied_at, lag(applied_at) OVER (ORDER BY version) AS prev_at FROM schema_migrations) x
 WHERE applied_at < prev_at;

\echo == 4 every recorded content hash, version order (compare with APPLIED_MIGRATIONS.sha256)
SELECT version, content_sha FROM schema_migrations ORDER BY version;

\echo == 5 public table inventory as one list (relkind r and p, no partitions)
SELECT count(*) AS tables,
       string_agg(c.relname, ',' ORDER BY c.relname) AS names
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition;

\echo == 6 installed extensions
SELECT extname, extversion FROM pg_extension ORDER BY 1;

\echo == 7 service heartbeats: process, status, age, reported commit and plane guard mode
SELECT service, status, beat_at,
       round(extract(epoch FROM now() - beat_at))::bigint AS age_s,
       (detail::jsonb) ->> 'commit' AS commit_reported,
       (detail::jsonb) -> 'guard' ->> 'mode' AS plane_mode
  FROM service_heartbeats ORDER BY service;

\echo == 8 tables the candidate and the open-lane migrations act on: estimated rows and size
SELECT c.relname, c.reltuples::bigint AS est_rows, pg_total_relation_size(c.oid) AS total_bytes
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relname IN ('smalllive_reconciliations', 'execmirror_orders', 'execution_intents', 'execmirror_control',
                     'paper_orders', 'paper_fills', 'paper_ledger', 'paper_decisions',
                     'paper_xavier_reviews', 'paper_audrey_reports', 'paper_equity_snapshots', 'paper_accounts',
                     'paper_sessions', 'external_valuations')
 ORDER BY 1;

\echo == 9 triggers already on those tables (non-internal)
SELECT tgrelid::regclass::text AS on_table, tgname
  FROM pg_trigger
 WHERE NOT tgisinternal
   AND tgrelid::regclass::text IN ('smalllive_reconciliations', 'execmirror_orders', 'execution_intents', 'execmirror_control',
                                   'paper_orders', 'paper_fills', 'paper_ledger', 'paper_decisions',
                                   'paper_xavier_reviews', 'paper_audrey_reports', 'paper_equity_snapshots')
 ORDER BY 1, 2;

\echo == 10 whether the new objects of 317, 366, 367 and 368 already exist in production
SELECT t AS object, to_regclass('public.' || t) IS NOT NULL AS exists_now
  FROM unnest(ARRAY['kalshi_shadow_intents', 'kalshi_shadow_account_reads', 'execmirror_exposure_caps',
                    'paper_account_epochs', 'paper_epoch_control', 'paper_epoch_events']) AS t
 ORDER BY 1;

\echo == 11 the columns the 366 and 368 migrations add: present already
SELECT table_name, column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND ((table_name = 'smalllive_reconciliations' AND column_name = 'stale_reason')
     OR (table_name IN ('execmirror_orders', 'execution_intents') AND column_name IN ('live_qty', 'live_qty_exact')))
 ORDER BY 1, 2;

\echo == 12 constraints on the two status columns an earlier 366 draft would have rewritten
SELECT conrelid::regclass::text AS on_table, conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conrelid::regclass::text IN ('execmirror_control', 'smalllive_reconciliations') AND contype = 'c'
 ORDER BY 1, 2;
