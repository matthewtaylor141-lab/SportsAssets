-- DRAW THE FROZEN_U2_CONDITION_UNIVERSE SETTLEMENT SNAPSHOT
--
-- Read-only. ONE statement, so the universe, markets and market_tokens are read
-- from ONE transaction snapshot by construction -- there is no window in which
-- one table could move between reads. The workflow additionally runs this under
-- `psql -1` at REPEATABLE READ, and the isolation level in force is recorded in
-- the output header rather than asserted in prose.
--
-- WHAT THIS PRODUCES. stdout IS the artifact. Run under `psql -A -t -X`:
-- unaligned, tuples-only, no column headers, exactly one line per row.
--
--     #KEY<TAB>VALUE            header lines, one per key, first
--     {"condition_id": ...}     one canonical JSON object per condition
--
-- The hash in the header covers ONLY the data lines, newline-joined with no
-- trailing newline. That contract is re-checkable offline by anyone holding the
-- committed file: drop the '#' lines, join with \n, sha256, compare.
--
-- IT IS NOT THE HASH OF THE COMMITTED FILE. The committed file also carries the
-- header lines, so its sha256 differs and is computed separately by the
-- workflow. Both are reported, under separate names, and neither is described
-- as authenticating bytes it did not hash:
--     DB_CANONICAL_CONTENT_SHA256  <- computed here, over the data lines
--     COMMITTED_FILE_SHA256        <- computed by sha256sum over the whole file
--
-- PERMANENT SEMANTICS, carried inside the artifact itself:
--   "Settlement metadata state observed at SNAPSHOT_DRAWN_AT for the frozen U2
--    condition universe."
-- It is NOT what BETTOR knew on 2026-09-12, NOT what the database looked like at
-- the historical cutoff, NOT historical settlement arrival state, and NOT an
-- independently verified settlement outcome. The historical-at-cutoff state is
-- NOT RECONSTRUCTIBLE.
--
-- ABSENCE IS FROZEN, NOT OMITTED (decision 2). The universe is built FIRST from
-- the U2 condition ids, then markets and market_tokens are LEFT JOINed onto it.
-- A condition with no markets row is carried with market_row_present = false; a
-- condition with no token metadata is carried with token_metadata_present =
-- false. A production row that appears after the draw can therefore never enter
-- 81B, because 81B reads this file and the condition is already in it, marked
-- absent.
--
-- CANONICAL RENDERING -- fixed here, before the draw, and never changed after
-- (the full specification is research/SNAPSHOT_CANONICAL_SPEC.md):
--   1. row order        ORDER BY condition_id COLLATE "C" (byte order, so the
--                       result does not depend on the server's locale)
--   2. timestamps       UTC, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"', fixed 6 digits
--   3. NULL             JSON null, always
--   4. booleans         JSON true / false, always
--   5. payouts          fixed-scale strings, to_char(..., 'FM9999990.000000')
--   6. arrays           payouts in stored array order (= outcome index);
--                       tokens ORDER BY outcome_index NULLS LAST, then token_id
--                       COLLATE "C"
--   7. text             JSON string escaping via json_build_object
--   8. key order        explicit in json_build_object, never jsonb's internal
--                       ordering
--
-- GUARDS RIDE IN THE HEADER so they are machine-checkable and describe the same
-- snapshot as the data. The workflow refuses to publish if any is non-zero:
--   GUARD_PAYOUT_ROUNDTRIP_LOSSY  a payout whose value changes under the
--                                 fixed-scale rendering
--   GUARD_PAYOUT_NON_NUMBER       an array element that is not a JSON number
--   GUARD_ROW_HAS_NEWLINE         a rendered row containing a newline or tab,
--                                 which would break the one-line-per-row format
--   GUARD_COUNT_MISMATCH          data rows != distinct U2 conditions

WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u2 AS MATERIALIZED (
  -- The U2 predicate, byte-for-byte as run 81A used it. No price filter, no
  -- depth-level filter: the probe's existence is the binding condition.
  SELECT t.id AS trade_id, t.condition_id
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
), univ AS MATERIALIZED (
  -- THE UNIVERSE. A trade with a NULL condition_id cannot name a condition and
  -- so cannot be in a condition universe; it is counted in the header rather
  -- than silently dropped.
  SELECT DISTINCT condition_id FROM u2 WHERE condition_id IS NOT NULL
), pxe AS (
  -- Payout elements, one row per array slot, with ordinality preserved as the
  -- outcome index. Only conditions whose resolved_prices IS an array reach here.
  SELECT u.condition_id, e.ordinality AS ix, e.value AS el
    FROM univ u
    JOIN markets m ON m.condition_id = u.condition_id
   CROSS JOIN LATERAL jsonb_array_elements(m.resolved_prices) WITH ORDINALITY AS e
   WHERE jsonb_typeof(m.resolved_prices) = 'array'
), px AS (
  SELECT pxe.condition_id,
         count(*)                                                  AS payout_len,
         count(*) FILTER (WHERE jsonb_typeof(pxe.el) <> 'number')  AS non_number,
         count(*) FILTER (
           WHERE jsonb_typeof(pxe.el) = 'number'
             AND (pxe.el #>> '{}')::numeric
                 <> to_char((pxe.el #>> '{}')::numeric,
                            'FM9999990.000000')::numeric)          AS lossy,
         json_agg(
           CASE WHEN jsonb_typeof(pxe.el) = 'number'
                THEN to_char((pxe.el #>> '{}')::numeric, 'FM9999990.000000')
           END ORDER BY pxe.ix)                                    AS payouts
    FROM pxe GROUP BY pxe.condition_id
), tok AS (
  SELECT mt.condition_id,
         count(*)                                                  AS token_count,
         json_agg(json_build_object(
                    'outcome_index', mt.outcome_index,
                    'token_id',      mt.token_id,
                    'outcome',       mt.outcome)
                  ORDER BY mt.outcome_index NULLS LAST,
                           mt.token_id COLLATE "C")                AS tokens
    FROM market_tokens mt
    JOIN univ u ON u.condition_id = mt.condition_id
   GROUP BY mt.condition_id
), rows AS (
  SELECT u.condition_id,
         json_build_object(
           'condition_id',         u.condition_id,
           'market_row_present',   (m.condition_id IS NOT NULL),
           'market_slug',          m.slug,
           'event_slug',           m.event_slug,
           'sport',                m.sport,
           'closed',               m.closed,
           'resolved',             m.resolved,
           'resolved_at',          to_char(m.resolved_at AT TIME ZONE 'UTC',
                                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
           'resolved_prices_type', jsonb_typeof(m.resolved_prices),
           'payout_len',           px.payout_len,
           'payouts',              px.payouts,
           'token_metadata_present', (tok.condition_id IS NOT NULL),
           'token_count',          tok.token_count,
           'tokens',               tok.tokens
         )::text                                                   AS row_json
    FROM univ u
    LEFT JOIN markets m  ON m.condition_id = u.condition_id
    LEFT JOIN px         ON px.condition_id = u.condition_id
    LEFT JOIN tok        ON tok.condition_id = u.condition_id
), agg AS (
  SELECT count(*)                                                  AS n_rows,
         count(*) FILTER (WHERE row_json ~ '[\n\t\r]')             AS bad_line,
         encode(sha256(convert_to(
           string_agg(row_json, E'\n' ORDER BY condition_id COLLATE "C"),
           'UTF8')), 'hex')                                        AS sha
    FROM rows
), meta AS (
  SELECT (SELECT count(DISTINCT condition_id) FROM univ)           AS n_cond,
         (SELECT count(*) FROM u2)                                 AS n_events,
         (SELECT count(*) FROM u2 WHERE condition_id IS NULL)      AS n_null_cond,
         (SELECT COALESCE(sum(non_number), 0) FROM px)             AS non_number,
         (SELECT COALESCE(sum(lossy), 0) FROM px)                  AS lossy
), hdr AS (
  SELECT 1 AS ord, '#SNAPSHOT_FORMAT' || E'\t' || '1' AS line FROM meta
  UNION ALL SELECT 2, '#SNAPSHOT_NAME' || E'\t'
                      || 'FROZEN_U2_CONDITION_UNIVERSE' FROM meta
  UNION ALL SELECT 3, '#SNAPSHOT_MEANING' || E'\t'
                      || 'settlement metadata state observed at '
                      || 'SNAPSHOT_DRAWN_AT for the frozen U2 condition '
                      || 'universe; NOT what BETTOR knew at AUDIT_CUTOFF_TS, '
                      || 'NOT the database state at AUDIT_CUTOFF_TS, NOT '
                      || 'settlement arrival, NOT a verified outcome; the '
                      || 'historical-at-cutoff state is NOT RECONSTRUCTIBLE'
             FROM meta
  UNION ALL SELECT 4, '#AUDIT_CUTOFF_TS' || E'\t'
                      || to_char(timestamptz '2026-09-12 00:00:00+00'
                                 AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM meta
  UNION ALL SELECT 5, '#SNAPSHOT_DRAWN_AT' || E'\t'
                      || to_char(now() AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM meta
  UNION ALL SELECT 6, '#TRANSACTION_ISOLATION' || E'\t'
                      || current_setting('transaction_isolation') FROM meta
  UNION ALL SELECT 7, '#TRANSACTION_READ_ONLY' || E'\t'
                      || current_setting('transaction_read_only') FROM meta
  UNION ALL SELECT 8, '#SNAPSHOT_U2_CONDITION_COUNT' || E'\t'
                      || meta.n_cond::text FROM meta
  UNION ALL SELECT 9, '#SNAPSHOT_U2_EVENT_COUNT' || E'\t'
                      || meta.n_events::text FROM meta
  UNION ALL SELECT 10, '#U2_EVENTS_WITH_NULL_CONDITION' || E'\t'
                      || meta.n_null_cond::text FROM meta
  UNION ALL SELECT 11, '#SNAPSHOT_ROW_COUNT' || E'\t'
                      || agg.n_rows::text FROM agg
  UNION ALL SELECT 12, '#GUARD_COUNT_MISMATCH' || E'\t'
                      || (agg.n_rows - meta.n_cond)::text FROM agg, meta
  UNION ALL SELECT 13, '#GUARD_PAYOUT_NON_NUMBER' || E'\t'
                      || meta.non_number::text FROM meta
  UNION ALL SELECT 14, '#GUARD_PAYOUT_ROUNDTRIP_LOSSY' || E'\t'
                      || meta.lossy::text FROM meta
  UNION ALL SELECT 15, '#GUARD_ROW_HAS_NEWLINE' || E'\t'
                      || agg.bad_line::text FROM agg
  UNION ALL SELECT 16, '#DB_CANONICAL_CONTENT_SHA256' || E'\t'
                      || agg.sha FROM agg
  UNION ALL SELECT 17, '#DB_CANONICAL_CONTENT_SCOPE' || E'\t'
                      || 'sha256 over the data lines only, newline-joined, no '
                      || 'trailing newline, ordered by condition_id COLLATE C; '
                      || 'the header lines are NOT covered'
             FROM meta
)
SELECT line FROM (
  SELECT ord, ''::text AS k, line FROM hdr
  UNION ALL
  SELECT 1000, condition_id, row_json FROM rows
) o ORDER BY o.ord, o.k COLLATE "C";
