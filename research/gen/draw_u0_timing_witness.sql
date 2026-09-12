-- DRAW U0_SETTLEMENT_TIMING_WITNESS_V1
--
-- Read-only. ONE statement. Its SOLE purpose is to make the STRONGER timing
-- quarantine reproducible offline. It is a SUPPLEMENT to AUDIT SNAPSHOT V1 and
-- REPLACES NOTHING: U2_SNAPSHOT_V1 stays authoritative and is not redrawn.
--
-- WHY IT EXISTS. The approved quarantine predicate witnesses ANY qualifying U0
-- fill. The sealed event snapshot carries only U2 -- the probe-backed subset --
-- so a quarantine computed from it alone can miss a condition whose only
-- post-resolution fill was never probed. That is a weaker rule wearing the
-- stronger rule's name, and this artifact removes the gap without touching the
-- authoritative snapshot.
--
-- SCOPE IS DELIBERATELY MINIMAL: the fields the timing predicate needs, and
-- nothing else. No copy_probes. No prices, sizes or notionals -- none of them
-- enter the predicate, and freezing economics that no test consumes would
-- invite someone later to compute with them from an artifact never validated
-- for that purpose.
--
-- A NULL condition_id IS PRESERVED AS NULL. No linkage is invented here: the
-- token-recovery gate belongs to the event snapshot, which has the asset column
-- this artifact does not carry. A null-condition U0 row cannot witness a timing
-- anomaly on any condition, and it is retained and counted rather than filtered
-- away so the count reconciles against the U0 control.
--
-- Canonical rules: research/SNAPSHOT_CANONICAL_SPEC.md@v1, applied unchanged --
-- row order by trade_id, timestamps UTC at fixed 6-digit precision, explicit
-- key order via json_build_object, NULL as JSON null.
--
-- OUTPUT under `psql -A -t -X`, one line per row:
--     #KEY<TAB>VALUE      manifest lines first
--     W<TAB>{...}         one U0 witness row, ordered by trade_id

WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  -- THE U0 PREDICATE, byte-for-byte as runs 80/81 used it.
  SELECT t.id AS trade_id, t.condition_id, t.ts, t.source
    FROM trades t
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
), w AS (
  SELECT u0.trade_id,
         json_build_object(
           'trade_id',     u0.trade_id,
           'condition_id', u0.condition_id,
           'ts',           to_char(u0.ts AT TIME ZONE 'UTC',
                                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
           'source',       u0.source
         )::text AS w_json
    FROM u0
), agg AS (
  SELECT count(*)                                               AS n,
         count(*) FILTER (WHERE w_json ~ '[\n\t\r]')            AS bad_line,
         encode(sha256(convert_to(
           string_agg(w_json, E'\n' ORDER BY trade_id), 'UTF8')), 'hex') AS sha
    FROM w
), meta AS (
  SELECT (SELECT count(*) FROM u0)                              AS n_events,
         (SELECT count(DISTINCT condition_id) FROM u0
           WHERE condition_id IS NOT NULL)                      AS n_cond,
         (SELECT count(*) FROM u0 WHERE condition_id IS NULL)   AS n_null_cond
), hdr AS (
  SELECT 1 AS ord, '#ARTIFACT_NAME' || E'\t'
                   || 'U0_SETTLEMENT_TIMING_WITNESS_V1' AS line FROM meta
  UNION ALL SELECT 2, '#CANONICAL_SPEC_VERSION' || E'\t'
                      || 'research/SNAPSHOT_CANONICAL_SPEC.md@v1' FROM meta
  UNION ALL SELECT 3, '#PURPOSE' || E'\t'
                      || 'supplemental artifact whose sole purpose is to make '
                      || 'the stronger timing quarantine reproducible offline; '
                      || 'it SUPPLEMENTS and does not replace U2_SNAPSHOT_V1, '
                      || 'which remains authoritative and is not redrawn'
             FROM meta
  UNION ALL SELECT 4, '#AUDIT_CUTOFF_TS' || E'\t'
                      || to_char(timestamptz '2026-09-12 00:00:00+00'
                                 AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM meta
  UNION ALL SELECT 5, '#U0_WITNESS_DRAWN_AT' || E'\t'
                      || to_char(now() AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM meta
  UNION ALL SELECT 6, '#TRANSACTION_ISOLATION' || E'\t'
                      || current_setting('transaction_isolation') FROM meta
  UNION ALL SELECT 7, '#TRANSACTION_READ_ONLY' || E'\t'
                      || current_setting('transaction_read_only') FROM meta
  UNION ALL SELECT 8, '#U0_WITNESS_EVENT_COUNT' || E'\t'
                      || meta.n_events::text FROM meta
  UNION ALL SELECT 9, '#U0_WITNESS_CONDITION_COUNT' || E'\t'
                      || meta.n_cond::text FROM meta
  UNION ALL SELECT 10, '#U0_WITNESS_NULL_CONDITION_ROWS' || E'\t'
                      || meta.n_null_cond::text FROM meta
  -- THE CONTROL. Runs 80 and 81A both measured U0 at 962,509 and it held
  -- exactly across that window. If it has moved, the artifact must not be used
  -- until the drift is explained -- the workflow refuses to seal on a mismatch.
  UNION ALL SELECT 11, '#U0_EXPECTED_CONTROL' || E'\t' || '962509' FROM meta
  UNION ALL SELECT 12, '#U0_CONTROL_DELTA' || E'\t'
                      || (meta.n_events - 962509)::text FROM meta
  UNION ALL SELECT 13, '#U0_CONTROL_REPRODUCES' || E'\t'
                      || CASE WHEN meta.n_events = 962509 THEN 'yes'
                              ELSE 'NO -- STOP AND REPORT THE DRIFT' END
             FROM meta
  UNION ALL SELECT 14, '#GUARD_ROW_HAS_NEWLINE' || E'\t'
                      || agg.bad_line::text FROM agg
  UNION ALL SELECT 15, '#GUARD_COUNT_MISMATCH' || E'\t'
                      || (agg.n - meta.n_events)::text FROM agg, meta
  UNION ALL SELECT 16, '#U0_WITNESS_UNCOMPRESSED_CANONICAL_SHA256' || E'\t'
                      || agg.sha FROM agg
  UNION ALL SELECT 17, '#DB_CANONICAL_SCOPE' || E'\t'
                      || 'the hash covers ONLY the W payload lines, '
                      || 'newline-joined, no trailing newline, ordered by '
                      || 'trade_id; the manifest lines are NOT covered'
             FROM meta
)
SELECT o.line FROM (
  SELECT hdr.ord, 0::bigint AS nkey, hdr.line FROM hdr
  UNION ALL
  SELECT 1000, w.trade_id, 'W' || E'\t' || w.w_json FROM w
) o ORDER BY o.ord, o.nkey;
