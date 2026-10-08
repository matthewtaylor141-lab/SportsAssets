-- HISTORICAL PAPER FINGERPRINT (pm-acceptance, READ ONLY, 2026-10-08).
--
-- One JSON object: every immutable PAPER row set below the FIXED cutoff
-- :'cutoff', hashed row by row in key order, with the committed-row
-- watermark that makes the set closed. pm-acceptance runs it twice: once
-- before a deploy (the PRE receipt, its own cutoff) and once after, at the
-- PRE receipt's cutoff (the POST receipt); pm_bind.acceptance.paper_history
-- compares them. Run with psql -v cutoff=<ISO instant>, under
-- default_transaction_read_only=on and TimeZone=UTC (row text renders
-- timestamptz in the session zone, so the zone is recorded and compared).
--
-- THE ROW SETS. The PM review fixed the business column per table
-- (committed_at / filled_at / recorded_at / decided_at). filled_at and
-- decided_at are stamped by the APPLICATION, so a row stamped below the
-- cutoff can still be inserted later; such a row is a backdated insert,
-- not part of the closed set. Each set is therefore ALSO bounded by the
-- database's own stamp (recorded_at / committed_at / created_at, all
-- now() or clock_timestamp() defaults), and the late rows are counted
-- apart (late_recorded_rows) so a backdated insert is visible.
--
-- THE WATERMARK. The set below the cutoff is closed once no transaction
-- that began before the cutoff is still open: writers_open_since_before_
-- cutoff counts the application role's writing transactions (backend_xid
-- assigned) in THIS database that began before it, and cutoff_lag_s says how far behind
-- the capture the cutoff lies. The binder admits a receipt only with zero
-- such writers and a settled lag.
--
-- paper_accounts and paper_orders are PROJECTIONS: accounts gain rows,
-- open orders change state. Their immutable parts (accounts created
-- before the cutoff, the entry terms of orders created before it) are in
-- "tables"; the moving parts are in "projections", reported and reconciled
-- separately, never counted as history.
SELECT json_build_object(
  'version', 'PAPER_HISTORY_FINGERPRINT_V1',
  'cutoff', to_char(CAST(:'cutoff' AS timestamptz) AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'captured_at', to_char(statement_timestamp() AT TIME ZONE 'UTC',
                         'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'database', current_database(),
  'server_version_num', current_setting('server_version_num'),
  'settings', json_build_object(
      'TimeZone', current_setting('TimeZone'),
      'DateStyle', current_setting('DateStyle'),
      'IntervalStyle', current_setting('IntervalStyle'),
      'extra_float_digits', current_setting('extra_float_digits')),
  'watermark', json_build_object(
      'snapshot', pg_current_snapshot()::text,
      'cutoff_lag_s', round(extract(epoch FROM statement_timestamp()
                            - CAST(:'cutoff' AS timestamptz))::numeric, 3),
      'writers_open_since_before_cutoff', (
          SELECT count(*) FROM pg_stat_activity a
           WHERE a.pid <> pg_backend_pid()
             AND a.datname = current_database()
             AND a.usename = current_user
             AND a.backend_xid IS NOT NULL
             AND a.xact_start < CAST(:'cutoff' AS timestamptz)),
      'oldest_writer_xact_start', (
          SELECT to_char(min(a.xact_start) AT TIME ZONE 'UTC',
                         'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            FROM pg_stat_activity a
           WHERE a.pid <> pg_backend_pid()
             AND a.datname = current_database()
             AND a.usename = current_user
             AND a.backend_xid IS NOT NULL),
      'other_role_client_sessions', (
          SELECT count(*) FROM pg_stat_activity a
           WHERE a.datname = current_database()
             AND a.usename IS DISTINCT FROM current_user
             AND a.backend_type = 'client backend')),
  'tables', json_build_object(
      'paper_ledger', (
          SELECT json_build_object(
                   'rows', count(*), 'max_seq', max(seq),
                   'cash_delta_total_usd', sum(cash_delta_usd)::text,
                   'digest', md5(string_agg(md5(l::text), '' ORDER BY seq)),
                   'scope', 'committed_at < cutoff')
            FROM paper_ledger l
           WHERE committed_at < CAST(:'cutoff' AS timestamptz)),
      'paper_fills', (
          SELECT json_build_object(
                   'rows', count(*),
                   'notional_usd', sum(qty * price)::text,
                   'fees_usd', sum(fee_usd)::text,
                   'digest', md5(string_agg(md5(f::text), '' ORDER BY fill_id)),
                   'late_recorded_rows', (
                       SELECT count(*) FROM paper_fills x
                        WHERE x.filled_at < CAST(:'cutoff' AS timestamptz)
                          AND x.recorded_at >= CAST(:'cutoff' AS timestamptz)),
                   'scope', 'filled_at < cutoff AND recorded_at < cutoff')
            FROM paper_fills f
           WHERE filled_at < CAST(:'cutoff' AS timestamptz)
             AND recorded_at < CAST(:'cutoff' AS timestamptz)),
      'paper_settlements', (
          SELECT json_build_object(
                   'rows', count(*),
                   'payout_total_usd', sum(payout_usd)::text,
                   'digest', md5(string_agg(md5(s::text), ''
                                            ORDER BY settlement_id)),
                   'scope', 'recorded_at < cutoff')
            FROM paper_settlements s
           WHERE recorded_at < CAST(:'cutoff' AS timestamptz)),
      'paper_order_events', (
          SELECT json_build_object(
                   'rows', count(*), 'max_event_id', max(event_id),
                   'digest', md5(string_agg(md5(e::text), ''
                                            ORDER BY event_id)),
                   'scope', 'recorded_at < cutoff')
            FROM paper_order_events e
           WHERE recorded_at < CAST(:'cutoff' AS timestamptz)),
      'paper_decisions', (
          SELECT json_build_object(
                   'rows', count(*),
                   'digest', md5(string_agg(md5(concat_ws('|', decision_id,
                                 verdict, refusal, proposed_qty::text,
                                 limit_price::text, decided_at::text)),
                                 '' ORDER BY decision_id)),
                   'late_recorded_rows', (
                       SELECT count(*) FROM paper_decisions x
                        WHERE x.decided_at < CAST(:'cutoff' AS timestamptz)
                          AND x.recorded_at >= CAST(:'cutoff' AS timestamptz)),
                   'scope', 'decided_at < cutoff AND recorded_at < cutoff; '
                            'economic fields only')
            FROM paper_decisions
           WHERE decided_at < CAST(:'cutoff' AS timestamptz)
             AND recorded_at < CAST(:'cutoff' AS timestamptz)),
      'paper_accounts', (
          SELECT json_build_object(
                   'rows', count(*),
                   'starting_cash_usd', sum(starting_cash_usd)::text,
                   'digest', md5(string_agg(md5(a::text), ''
                                            ORDER BY account_id)),
                   'scope', 'created_at < cutoff')
            FROM paper_accounts a
           WHERE created_at < CAST(:'cutoff' AS timestamptz)),
      'paper_orders', (
          SELECT json_build_object(
                   'rows', count(*),
                   'digest', md5(string_agg(md5(concat_ws('|', order_id,
                                 idempotency_key, account_id, session_id,
                                 group_id, role, direction, holding_side,
                                 intent, us_market_slug, fixture,
                                 label::text, order_type, time_in_force,
                                 allow_partial::text, qty::text,
                                 limit_price::text, wire_price::text,
                                 reserved_usd::text, decision_id,
                                 decided_at::text, eligible_at::text,
                                 expires_at::text, event_source,
                                 simulator_version, created_at::text,
                                 strategy)), '' ORDER BY order_id)),
                   'scope', 'created_at < cutoff; entry terms only')
            FROM paper_orders
           WHERE created_at < CAST(:'cutoff' AS timestamptz))),
  'projections', json_build_object(
      'paper_accounts', (
          SELECT json_build_object(
                   'rows_all', count(*),
                   'digest_all', md5(string_agg(md5(a::text), ''
                                                ORDER BY account_id)),
                   'rows_since_cutoff', count(*) FILTER (
                       WHERE created_at >= CAST(:'cutoff' AS timestamptz)))
            FROM paper_accounts a),
      'paper_orders', (
          SELECT json_build_object(
                   'rows', count(*) FILTER (
                       WHERE created_at < CAST(:'cutoff' AS timestamptz)),
                   'digest', md5(string_agg(md5(concat_ws('|', order_id,
                                 state, filled_qty::text,
                                 reserved_remaining_usd::text,
                                 terminal_at::text, terminal_reason)), ''
                                 ORDER BY order_id) FILTER (
                       WHERE created_at < CAST(:'cutoff' AS timestamptz))),
                   'open_rows', count(*) FILTER (
                       WHERE created_at < CAST(:'cutoff' AS timestamptz)
                         AND terminal_at IS NULL),
                   'rows_since_cutoff', count(*) FILTER (
                       WHERE created_at >= CAST(:'cutoff' AS timestamptz)))
            FROM paper_orders)),
  'columns', (
      SELECT json_object_agg(x.table_name, x.cols ORDER BY x.table_name)
        FROM (SELECT c.table_name,
                     string_agg(c.column_name || ':' || c.data_type, ','
                                ORDER BY c.ordinal_position) AS cols
                FROM information_schema.columns c
               WHERE c.table_schema = current_schema()
                 AND (c.table_name IN ('paper_ledger', 'paper_fills',
                                       'paper_settlements',
                                       'paper_order_events',
                                       'paper_accounts')
                      OR (c.table_name = 'paper_decisions'
                          AND c.column_name IN ('decision_id', 'verdict',
                              'refusal', 'proposed_qty', 'limit_price',
                              'decided_at', 'recorded_at'))
                      OR (c.table_name = 'paper_orders'
                          AND c.column_name IN ('order_id',
                              'idempotency_key', 'account_id', 'session_id',
                              'group_id', 'role', 'direction',
                              'holding_side', 'intent', 'us_market_slug',
                              'fixture', 'label', 'order_type',
                              'time_in_force', 'allow_partial', 'qty',
                              'limit_price', 'wire_price', 'reserved_usd',
                              'decision_id', 'decided_at', 'eligible_at',
                              'expires_at', 'event_source',
                              'simulator_version', 'created_at',
                              'strategy')))
               GROUP BY c.table_name) x)
) AS fingerprint;
