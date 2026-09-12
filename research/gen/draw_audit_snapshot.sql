-- DRAW THE AUDIT SNAPSHOT V1 -- U2_SNAPSHOT_V1 + SETTLEMENT_SNAPSHOT_V1
--
-- Read-only. ONE statement, so the event selection, the universe derived FROM
-- that selection, markets and market_tokens are all read from ONE transaction
-- snapshot by construction -- there is no window in which any of them could
-- move between reads. The workflow additionally runs this under `psql -1` at
-- REPEATABLE READ, and the isolation level actually in force is READ FROM THE
-- SERVER into the manifest rather than asserted in prose.
--
-- Canonical rules: research/SNAPSHOT_CANONICAL_SPEC.md (version v1).
--
-- OUTPUT. stdout is the artifact. Run under `psql -A -t -X`: unaligned,
-- tuples-only, no column headers, exactly one line per row, tagged so the
-- workflow can split three components without ambiguity:
--
--     #KEY<TAB>VALUE      manifest lines, first
--     E<TAB>{...}         one U2 event, ordered by probe_id
--     C<TAB>{...}         one condition, ordered by condition_id COLLATE "C"
--
-- NAMING IS LOAD-BEARING AND PERMANENT. What is drawn here is U2_SNAPSHOT_V1,
-- observed at U2_SNAPSHOT_V1_DRAWN_AT. It is NOT "the frozen Run-81A
-- population": run 81A's exact event set was never materialised and copy_probes
-- retention has deleted rows oldest-first ever since, so that set is NOT
-- RECONSTRUCTIBLE. RUN 81A ORIGINAL stays a valid historical measurement of the
-- population that existed while it ran. The two are never called identical, and
-- the manifest prints the delta against 81A's three controls.
--
-- THE PROBE-SELECTION RULE IS PRESERVED VERBATIM: THERE IS NONE. Run 81A wrote
-- a plain `trades t JOIN copy_probes c ON c.trade_id = t.id` with no DISTINCT,
-- no LATERAL ... LIMIT 1 and no aggregation, so a trade carrying n qualifying
-- probes contributed n rows and 81A's 214,651 counts (trade, probe) PAIRS, not
-- distinct trades -- and its source notional counts such a trade n times for the
-- same reason. That rule is deterministic and is kept exactly. Every row is
-- keyed on copy_probes.id, the probe's own primary key, so the snapshot names
-- THE EXACT RETAINED PROBE ROW and never "some probe for this trade". The
-- multiplicity census is printed rather than assumed.
--
-- ALL NUMERICS ARE EXACT CANONICAL TEXT (v::numeric::text) RENDERED AS JSON
-- STRINGS. numeric has an exact decimal representation and never renders in
-- scientific notation, so the round trip is lossless BY CONSTRUCTION rather than
-- by a guard that could only ever pass. Strings rather than JSON numbers because
-- a float round-trip through most JSON parsers is not identity, and a consumer
-- must not be able to renormalise the evidence by reading it.
--
-- ABSENCE IS DATA. The universe is built from the selected events and the
-- mutable tables are LEFT JOINed onto it, so market_row_present and
-- token_metadata_present are carried false rather than the condition vanishing.
-- A production row appearing after the draw can never enter later analysis.

WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), sel AS MATERIALIZED (
  -- THE SELECTED EVENT ROWS. The U2 predicate byte-for-byte as run 81A used it.
  SELECT c.id AS probe_id, t.id AS trade_id, t.condition_id, t.asset,
         t.outcome, t.outcome_index, t.side, t.ts, t.detected_at, t.source,
         t.sport, t.market_slug, t.event_slug, t.size, t.price, t.notional,
         c.probe_at, c.reaction_s, c.his_price, c.his_size, c.his_notional,
         c.best_ask, c.best_ask_usd, c.book_ok, c.error, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
), dlv AS (
  -- Depth levels, one row per retained ask level, ordinality preserved as the
  -- stored array order. Only probes whose depth IS an array are expanded; one
  -- whose depth is some other JSON type is counted by GUARD_DEPTH_NOT_ARRAY.
  SELECT s.probe_id, d.ordinality AS ix,
         d.value -> 0 AS px, d.value -> 1 AS sh,
         jsonb_typeof(d.value) AS lvl_type,
         CASE WHEN jsonb_typeof(d.value) = 'array'
              THEN jsonb_array_length(d.value) END AS lvl_len
    FROM sel s
   CROSS JOIN LATERAL jsonb_array_elements(s.depth) WITH ORDINALITY AS d
   WHERE jsonb_typeof(s.depth) = 'array'
), dep AS (
  SELECT dlv.probe_id,
         count(*)                                                  AS levels,
         count(*) FILTER (WHERE dlv.lvl_type IS DISTINCT FROM 'array'
                             OR dlv.lvl_len IS DISTINCT FROM 2)    AS malformed,
         count(*) FILTER (WHERE jsonb_typeof(dlv.px) IS DISTINCT FROM 'number'
                             OR jsonb_typeof(dlv.sh) IS DISTINCT FROM 'number')
                                                                   AS non_number,
         json_agg(json_build_array(
             CASE WHEN jsonb_typeof(dlv.px) = 'number'
                  THEN (dlv.px #>> '{}')::numeric::text END,
             CASE WHEN jsonb_typeof(dlv.sh) = 'number'
                  THEN (dlv.sh #>> '{}')::numeric::text END)
           ORDER BY dlv.ix)                                        AS levels_json
    FROM dlv GROUP BY dlv.probe_id
), ev AS (
  SELECT s.probe_id,
         json_build_object(
           'probe_id',      s.probe_id,
           'trade_id',      s.trade_id,
           'condition_id',  s.condition_id,
           'asset',         s.asset,
           'outcome',       s.outcome,
           'outcome_index', s.outcome_index,
           'side',          s.side,
           'ts',            to_char(s.ts AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
           'detected_at',   to_char(s.detected_at AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
           'source',        s.source,
           'sport',         s.sport,
           'market_slug',   s.market_slug,
           'event_slug',    s.event_slug,
           'size',          s.size::text,
           'price',         s.price::text,
           'notional',      s.notional::text,
           'probe_at',      to_char(s.probe_at AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
           'reaction_s',    s.reaction_s::text,
           'his_price',     s.his_price::text,
           'his_size',      s.his_size::text,
           'his_notional',  s.his_notional::text,
           'best_ask',      s.best_ask::text,
           'best_ask_usd',  s.best_ask_usd::text,
           'book_ok',       s.book_ok,
           'error',         s.error,
           'depth_type',    jsonb_typeof(s.depth),
           'depth_levels',  COALESCE(dep.levels, 0),
           'depth',         COALESCE(dep.levels_json, '[]'::json)
         )::text                                                   AS ev_json
    FROM sel s LEFT JOIN dep ON dep.probe_id = s.probe_id
), univ AS MATERIALIZED (
  -- DERIVED FROM THE SELECTED EVENT ROWS, in this same statement and therefore
  -- this same snapshot. It is never re-derived from live copy_probes.
  SELECT DISTINCT condition_id FROM sel WHERE condition_id IS NOT NULL
), pxe AS (
  SELECT u.condition_id, e.ordinality AS ix, e.value AS el
    FROM univ u
    JOIN markets m ON m.condition_id = u.condition_id
   CROSS JOIN LATERAL jsonb_array_elements(m.resolved_prices) WITH ORDINALITY AS e
   WHERE jsonb_typeof(m.resolved_prices) = 'array'
), px AS (
  SELECT pxe.condition_id,
         count(*)                                                  AS payout_len,
         count(*) FILTER (WHERE jsonb_typeof(pxe.el) <> 'number')  AS non_number,
         json_agg(CASE WHEN jsonb_typeof(pxe.el) = 'number'
                       THEN (pxe.el #>> '{}')::numeric::text END
                  ORDER BY pxe.ix)                                 AS payouts
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
), cond AS (
  SELECT u.condition_id,
         json_build_object(
           'condition_id',           u.condition_id,
           'market_row_present',     (m.condition_id IS NOT NULL),
           'market_slug',            m.slug,
           'event_slug',             m.event_slug,
           'sport',                  m.sport,
           'closed',                 m.closed,
           'resolved',               m.resolved,
           'resolved_at',            to_char(m.resolved_at AT TIME ZONE 'UTC',
                                             'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
           'resolved_prices_type',   jsonb_typeof(m.resolved_prices),
           'payout_len',             px.payout_len,
           'payouts',                px.payouts,
           'token_metadata_present', (tok.condition_id IS NOT NULL),
           'token_count',            tok.token_count,
           'tokens',                 tok.tokens
         )::text                                                   AS cond_json
    FROM univ u
    LEFT JOIN markets m ON m.condition_id = u.condition_id
    LEFT JOIN px        ON px.condition_id = u.condition_id
    LEFT JOIN tok       ON tok.condition_id = u.condition_id
), eagg AS (
  SELECT count(*)                                                  AS n,
         count(*) FILTER (WHERE ev_json ~ '[\n\t\r]')              AS bad_line,
         encode(sha256(convert_to(
           string_agg(ev_json, E'\n' ORDER BY probe_id), 'UTF8')), 'hex') AS sha
    FROM ev
), cagg AS (
  SELECT count(*)                                                  AS n,
         count(*) FILTER (WHERE cond_json ~ '[\n\t\r]')            AS bad_line,
         encode(sha256(convert_to(
           string_agg(cond_json, E'\n' ORDER BY condition_id COLLATE "C"),
           'UTF8')), 'hex')                                        AS sha
    FROM cond
), mult AS (
  SELECT count(*)                                                  AS trades_with_probe,
         count(*) FILTER (WHERE n = 1)                             AS trades_1,
         count(*) FILTER (WHERE n > 1)                             AS trades_gt1,
         COALESCE(max(n), 0)                                       AS max_probes,
         COALESCE(sum(n) FILTER (WHERE n > 1), 0)                  AS rows_from_gt1
    FROM (SELECT trade_id, count(*) AS n FROM sel GROUP BY trade_id) q
), meta AS (
  SELECT (SELECT count(*) FROM sel)                                AS n_events,
         (SELECT count(DISTINCT condition_id) FROM univ)           AS n_cond,
         (SELECT count(*) FROM sel WHERE condition_id IS NULL)     AS n_null_cond,
         (SELECT COALESCE(sum(notional), 0) FROM sel)              AS src_notional,
         (SELECT count(*) FROM u0)                                 AS n_u0,
         (SELECT COALESCE(sum(non_number), 0) FROM dep)            AS dep_non_number,
         (SELECT COALESCE(sum(malformed), 0) FROM dep)             AS dep_malformed,
         (SELECT count(*) FROM sel
           WHERE jsonb_typeof(depth) IS DISTINCT FROM 'array')     AS dep_not_array,
         (SELECT COALESCE(sum(non_number), 0) FROM px)             AS px_non_number
), hdr AS (
  SELECT 1 AS ord, '#SNAPSHOT_NAME' || E'\t' || 'AUDIT_SNAPSHOT_V1' AS line FROM meta
  UNION ALL SELECT 2, '#CANONICAL_SPEC_VERSION' || E'\t'
                      || 'research/SNAPSHOT_CANONICAL_SPEC.md@v1' FROM meta
  UNION ALL SELECT 3, '#SNAPSHOT_MEANING' || E'\t'
                      || 'U2_SNAPSHOT_V1 is the immutable evidence population '
                      || 'observed at U2_SNAPSHOT_V1_DRAWN_AT. '
                      || 'SETTLEMENT_SNAPSHOT_V1 is settlement metadata state '
                      || 'observed at that same instant for the condition '
                      || 'universe derived from it. Neither is what BETTOR knew '
                      || 'at AUDIT_CUTOFF_TS, nor the database state at that '
                      || 'cutoff, nor settlement arrival, nor a verified '
                      || 'outcome. The historical-at-cutoff state is NOT '
                      || 'RECONSTRUCTIBLE.' FROM meta
  UNION ALL SELECT 4, '#RUN_81A_ORIGINAL_NOTE' || E'\t'
                      || 'RUN 81A ORIGINAL is a historical result on the live '
                      || 'U2 population that existed while it ran; that event '
                      || 'set was never materialised and copy_probes retention '
                      || 'has since deleted rows oldest-first, so it is NOT '
                      || 'RECONSTRUCTIBLE and is never called identical to '
                      || 'U2_SNAPSHOT_V1.' FROM meta
  UNION ALL SELECT 5, '#AUDIT_CUTOFF_TS' || E'\t'
                      || to_char(timestamptz '2026-09-12 00:00:00+00'
                                 AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM meta
  UNION ALL SELECT 6, '#U2_SNAPSHOT_V1_DRAWN_AT' || E'\t'
                      || to_char(now() AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') FROM meta
  UNION ALL SELECT 7, '#TRANSACTION_ISOLATION' || E'\t'
                      || current_setting('transaction_isolation') FROM meta
  UNION ALL SELECT 8, '#TRANSACTION_READ_ONLY' || E'\t'
                      || current_setting('transaction_read_only') FROM meta
  UNION ALL SELECT 9, '#U2_SNAPSHOT_V1_EVENT_COUNT' || E'\t'
                      || meta.n_events::text FROM meta
  UNION ALL SELECT 10, '#U2_SNAPSHOT_V1_CONDITION_COUNT' || E'\t'
                      || meta.n_cond::text FROM meta
  UNION ALL SELECT 11, '#U2_SNAPSHOT_V1_SOURCE_NOTIONAL' || E'\t'
                      || round(meta.src_notional, 2)::text FROM meta
  UNION ALL SELECT 12, '#U2_EVENTS_WITH_NULL_CONDITION' || E'\t'
                      || meta.n_null_cond::text FROM meta
  UNION ALL SELECT 13, '#U0_TRADES_AT_CUTOFF' || E'\t'
                      || meta.n_u0::text FROM meta
  -- RUN 81A ORIGINAL controls and the delta, printed here so the difference is
  -- part of the sealed record rather than a claim made later in prose.
  UNION ALL SELECT 14, '#RUN81A_ORIGINAL_EVENT_COUNT' || E'\t' || '214651' FROM meta
  UNION ALL SELECT 15, '#RUN81A_ORIGINAL_CONDITION_COUNT' || E'\t' || '17755' FROM meta
  UNION ALL SELECT 16, '#RUN81A_ORIGINAL_SOURCE_NOTIONAL' || E'\t'
                      || '48327388.19' FROM meta
  UNION ALL SELECT 17, '#DELTA_EVENTS' || E'\t'
                      || (meta.n_events - 214651)::text FROM meta
  UNION ALL SELECT 18, '#DELTA_CONDITIONS' || E'\t'
                      || (meta.n_cond - 17755)::text FROM meta
  UNION ALL SELECT 19, '#DELTA_SOURCE_NOTIONAL' || E'\t'
                      || round(meta.src_notional - 48327388.19, 2)::text FROM meta
  -- The probe-multiplicity census: load-bearing, because it says whether an
  -- event count is a trade count.
  UNION ALL SELECT 20, '#U2_TRADES_WITH_0_PROBES' || E'\t'
                      || (meta.n_u0 - mult.trades_with_probe)::text
             FROM meta, mult
  UNION ALL SELECT 21, '#U2_TRADES_WITH_1_PROBE' || E'\t'
                      || mult.trades_1::text FROM mult
  UNION ALL SELECT 22, '#U2_TRADES_WITH_GT1_PROBE' || E'\t'
                      || mult.trades_gt1::text FROM mult
  UNION ALL SELECT 23, '#U2_MAX_PROBES_PER_TRADE' || E'\t'
                      || mult.max_probes::text FROM mult
  UNION ALL SELECT 24, '#U2_ROWS_FROM_GT1_TRADES' || E'\t'
                      || mult.rows_from_gt1::text FROM mult
  UNION ALL SELECT 25, '#U2_PROBE_SELECTION_RULE' || E'\t'
                      || 'none -- run 81A joined trades to copy_probes with no '
                      || 'deduplication, so every qualifying probe is its own '
                      || 'row and the snapshot is keyed on copy_probes.id; the '
                      || 'event count counts (trade, probe) pairs' FROM meta
  UNION ALL SELECT 30, '#SETTLEMENT_SNAPSHOT_ROW_COUNT' || E'\t'
                      || cagg.n::text FROM cagg
  UNION ALL SELECT 31, '#GUARD_EVENT_COUNT_MISMATCH' || E'\t'
                      || (eagg.n - meta.n_events)::text FROM eagg, meta
  UNION ALL SELECT 32, '#GUARD_COND_COUNT_MISMATCH' || E'\t'
                      || (cagg.n - meta.n_cond)::text FROM cagg, meta
  UNION ALL SELECT 33, '#GUARD_DEPTH_NOT_ARRAY' || E'\t'
                      || meta.dep_not_array::text FROM meta
  UNION ALL SELECT 34, '#GUARD_DEPTH_MALFORMED' || E'\t'
                      || meta.dep_malformed::text FROM meta
  UNION ALL SELECT 35, '#GUARD_DEPTH_NON_NUMBER' || E'\t'
                      || meta.dep_non_number::text FROM meta
  UNION ALL SELECT 36, '#GUARD_PAYOUT_NON_NUMBER' || E'\t'
                      || meta.px_non_number::text FROM meta
  UNION ALL SELECT 37, '#GUARD_ROW_HAS_NEWLINE' || E'\t'
                      || (eagg.bad_line + cagg.bad_line)::text FROM eagg, cagg
  UNION ALL SELECT 40, '#U2_EVENT_DB_CANONICAL_SHA256' || E'\t'
                      || eagg.sha FROM eagg
  UNION ALL SELECT 41, '#SETTLEMENT_DB_CANONICAL_SHA256' || E'\t'
                      || cagg.sha FROM cagg
  UNION ALL SELECT 42, '#DB_CANONICAL_SCOPE' || E'\t'
                      || 'each DB hash covers ONLY its own payload lines, '
                      || 'newline-joined, no trailing newline, in canonical '
                      || 'order; the manifest lines are NOT covered by either'
             FROM meta
)
SELECT o.line FROM (
  SELECT hdr.ord, 0::bigint AS nkey, ''::text AS tkey, hdr.line FROM hdr
  UNION ALL
  SELECT 1000, ev.probe_id, '', 'E' || E'\t' || ev.ev_json FROM ev
  UNION ALL
  SELECT 2000, 0::bigint, cond.condition_id, 'C' || E'\t' || cond.cond_json
    FROM cond
) o ORDER BY o.ord, o.nkey, o.tkey COLLATE "C";
