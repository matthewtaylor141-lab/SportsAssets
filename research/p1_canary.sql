-- P1 PRODUCTION CANARY, AS SQL (SELECT only).
--
-- The same questions GET /api/command/canary answers (sportsassets/
-- ops_canary.py, shared with scripts/bettor_canary.py), for a reader holding a
-- read-only connection: per-service boot identity, the latest checkpoint,
-- cursor non-regression across the last restart, retention status, and
-- no-order structural evidence. Every statement is a SELECT; run it inside
-- `BEGIN READ ONLY;` (tests/test_ops_canary.py executes this file that way).
--
-- Lane: 'polymarket-us/institutional/decision-only' (bettor_live_store.LANE).
-- Thresholds mirror ops_canary: cursor receipt slack 120 s, retention stale
-- after 3 h, retention window + 2 cycles, heartbeat stale after 900 s.

-- 1. BOOT HISTORY of the live loop (newest first): one boot_id per worker
--    process boot, with the journal rows each wrote.
SELECT boot_id, count(*) AS rows, min(id) AS first_id, max(id) AS last_id,
       min(written_at) AS first_at, max(written_at) AS last_at
  FROM bettor_live_journal
 WHERE lane = 'polymarket-us/institutional/decision-only'
   AND boot_id IS NOT NULL
 GROUP BY boot_id
 ORDER BY min(id) DESC
 LIMIT 12;

-- 2. PROCESS IDENTITIES per service (runtime_loop_health: commit, host, pid).
SELECT process, commit_sha, host, pid, count(*) AS loops,
       min(last_start_at) AS first_start, max(updated_at) AS last_update
  FROM runtime_loop_health
 GROUP BY process, commit_sha, host, pid
 ORDER BY max(updated_at) DESC
 LIMIT 40;

-- 3. THE WORKERS' BOOT MARKER and every service heartbeat's age.
SELECT 'workers_boot' AS source, value->>'commit_sha' AS commit_sha,
       value->>'at' AS at, value->>'venue_writes' AS venue_writes,
       NULL::double precision AS age_s, NULL::boolean AS stale
  FROM ingestion_state WHERE key = 'workers_boot'
UNION ALL
SELECT 'heartbeat:' || service, COALESCE(detail->>'commit_sha',
       detail->>'commit'), beat_at::text, status,
       extract(epoch FROM now() - beat_at),
       extract(epoch FROM now() - beat_at) > 900
  FROM service_heartbeats;

-- 4. THE LATEST CHECKPOINT, and whether the newest boot wrote it.
WITH newest AS (
    SELECT boot_id FROM bettor_live_journal
     WHERE lane = 'polymarket-us/institutional/decision-only'
       AND boot_id IS NOT NULL
     ORDER BY id DESC LIMIT 1)
SELECT l.boot_id AS checkpoint_boot, l.saved_at AS checkpoint_saved_at,
       l.loop_version, l.schema_version, length(l.snapshot) AS bytes,
       n.boot_id AS newest_boot,
       (l.boot_id = n.boot_id) AS checkpoint_by_current_boot,
       (SELECT count(*) FROM bettor_live_journal
         WHERE lane = 'polymarket-us/institutional/decision-only')
         AS journal_rows,
       (SELECT max(id) FROM bettor_live_journal
         WHERE lane = 'polymarket-us/institutional/decision-only')
         AS journal_max_id,
       (SELECT count(*) FROM bettor_live_cursor
         WHERE lane = 'polymarket-us/institutional/decision-only') AS cursors
  FROM bettor_live_ledger l
  LEFT JOIN newest n ON true
 WHERE l.lane = 'polymarket-us/institutional/decision-only';

-- 5. CURSORS NEVER REGRESSED ACROSS THE LAST RESTART: every market the
--    journal decided on in the last two boots has a cursor, and no cursor is
--    behind the newest source timestamp / receipt the journal wrote for it.
WITH boots AS (
    SELECT boot_id, min(id) AS first_id, max(id) AS last_id
      FROM bettor_live_journal
     WHERE lane = 'polymarket-us/institutional/decision-only'
       AND boot_id IS NOT NULL
     GROUP BY boot_id ORDER BY min(id) DESC LIMIT 2),
j AS (
    SELECT market_id,
           max(CASE WHEN source_ts ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}'
                    THEN source_ts::timestamptz END) AS j_source,
           max(written_at) AS j_written
      FROM bettor_live_journal
     WHERE lane = 'polymarket-us/institutional/decision-only'
       AND kind = 'DECISION' AND market_id IS NOT NULL
       AND boot_id IN (SELECT boot_id FROM boots)
     GROUP BY market_id)
SELECT (SELECT count(*) FROM boots) AS boots_compared,
       (SELECT bool_or(p.last_id > c.first_id) FROM boots p, boots c
         WHERE p.first_id < c.first_id) AS writers_overlapped,
       count(j.market_id) AS markets_compared,
       count(*) FILTER (WHERE c.market_id IS NULL) AS missing_cursor,
       count(*) FILTER (WHERE c.last_source_ts ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}'
                          AND j.j_source IS NOT NULL
                          AND c.last_source_ts::timestamptz < j.j_source)
         AS behind_source,
       count(*) FILTER (WHERE c.last_seen_at IS NOT NULL
                          AND c.last_seen_at
                              < extract(epoch FROM j.j_written) - 120)
         AS behind_seen,
       count(*) FILTER (WHERE c.market_id IS NULL
           OR (c.last_source_ts ~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}'
               AND j.j_source IS NOT NULL
               AND c.last_source_ts::timestamptz < j.j_source)
           OR (c.last_seen_at IS NOT NULL
               AND c.last_seen_at < extract(epoch FROM j.j_written) - 120))
         AS cursor_regressions
  FROM j
  LEFT JOIN bettor_live_cursor c
    ON c.lane = 'polymarket-us/institutional/decision-only'
   AND c.market_id = j.market_id;

-- 6. RETENTION: the last cycle's age and refusal, and rows past each pinned
--    window (keep_days from the state row, else the consumer floor) plus two
--    hourly cycles.
WITH r AS (SELECT value FROM ingestion_state WHERE key = 'retention_last')
SELECT r.value->>'at' AS last_cycle_at,
       extract(epoch FROM now() - (r.value->>'at')::timestamptz)
         AS retention_age_s,
       extract(epoch FROM now() - (r.value->>'at')::timestamptz) > 10800
         AS retention_stale,
       r.value->>'refused' AS retention_refused,
       (r.value->>'deleted_total')::bigint AS deleted_total,
       (SELECT count(*) FROM ai_trades WHERE placed_at < now()
          - make_interval(days => COALESCE((r.value#>>'{tables,ai_trades,keep_days}')::int, 97))
          - interval '2 hours') AS ai_trades_past_window,
       (SELECT count(*) FROM copy_probes WHERE probe_at < now()
          - make_interval(days => COALESCE((r.value#>>'{tables,copy_probes,keep_days}')::int, 37))
          - interval '2 hours') AS copy_probes_past_window
  FROM r;

-- 7. NO ORDER: SMALL LIVE mode (SHADOW, or STOPPED when halted), the other
--    live controls, the effective cutover, venue orders since it in every
--    live-order table, and our own journal's executed / sized records.
--    With NO cutover recorded effective_cutover_at is NULL and the
--    since-cutover count is 0 by construction: read it as NOT ESTABLISHED.
WITH cut AS (SELECT recorded_at FROM live_parity_effective_cutover),
since AS (SELECT COALESCE((SELECT recorded_at FROM cut),
                          'infinity'::timestamptz) AS t)
SELECT (SELECT CASE WHEN halted THEN 'STOPPED' ELSE mode END
          FROM small_live_control WHERE id = 1) AS small_live_effective,
       (SELECT recorded_at FROM cut) AS effective_cutover_at,
       (SELECT enabled AND NOT stopped FROM execmirror_control WHERE id = 1)
         AS execmirror_armed,
       (SELECT enabled AND NOT stopped FROM kalshi_smalllive_control
         WHERE id = 1) AS kalshi_smalllive_armed,
       (SELECT value->>'venue_writes' FROM ingestion_state
         WHERE key = 'workers_boot') AS workers_venue_writes,
       (SELECT count(*) FROM small_live_order_events, since
         WHERE observed_at >= since.t)
       + (SELECT count(*) FROM canonical_intent_executions, since
           WHERE adapter = 'SMALL_LIVE'
             AND (mode <> 'SHADOW' OR refs ? 'venue_order_id')
             AND created_at >= since.t)
       + (SELECT count(*) FROM live_orders, since WHERE placed_at >= since.t)
       + (SELECT count(*) FROM bettor_funded_intents, since
           WHERE venue_order_id IS NOT NULL AND created_at >= since.t)
       + (SELECT count(*) FROM execmirror_orders, since
           WHERE (venue_order_id IS NOT NULL OR submit_started_at IS NOT NULL)
             AND COALESCE(submit_started_at, accepted_at) >= since.t)
       + (SELECT count(*) FROM kalshi_live_intents, since
           WHERE (venue_order_id IS NOT NULL OR submit_started_at IS NOT NULL)
             AND COALESCE(submit_started_at, accepted_at, created_at)
                 >= since.t) AS live_orders_since_cutover,
       (SELECT count(*) FROM bettor_live_journal
         WHERE lane = 'polymarket-us/institutional/decision-only'
           AND ((record->>'executed') = 'true'
                OR COALESCE((record->>'size_contracts')::float, 0) <> 0))
         AS journal_executed_or_sized;
