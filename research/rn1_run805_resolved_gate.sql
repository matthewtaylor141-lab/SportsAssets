-- ============================================================================
-- RUN 80.5 -- THE DATA-INTEGRITY GATE ON resolved_at < fill ts
-- (2026-09-12, read-only. Nothing here writes. mirror_live = false.)
--
-- APPROVED SCOPE, owner 2026-09-12: "RUN 80.5 ONLY." Investigate the 1,902
-- conditions where markets.resolved_at precedes a recorded RN1 fill, BEFORE any
-- settlement-dependent economics. Produce exactly one settlement-eligibility
-- verdict and, if exclusions are needed, a MECHANICAL exclusion rule.
--
-- NO ECONOMICS. Run 81 does not follow. ai_trades / TRUEEDGE untouched: the
-- name appears nowhere below.
--
-- AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z, applied using each table's designated
-- cutoff field WITH THAT FIELD'S SEMANTICS CARRIED -- trades on ts and
-- detected_at (event and our sight of it), copy_probes on probe_at (the
-- observation's own clock), markets on resolved_at (WHEN THE EVENT RESOLVED,
-- which is NOT an arrival timestamp and is NOT settlement known to BETTOR).
--
-- ---------------------------------------------------------------------------
-- NO EXPLANATION IS CHOSEN IN ADVANCE. The classes below rest on facts the
-- data can carry -- which lanes the post-resolved fills came from, whether the
-- condition-to-market join holds -- and anything they cannot support is
-- UNRESOLVED, not a guess.
--
-- CLASS C IS DELIBERATELY NOT ASSIGNED. The owner admitted
-- LIKELY_FETCH_FALLBACK_TIMESTAMP "only if the write-branch semantics can
-- actually be supported". They cannot: markets has no ALTER in any migration
-- and no raw venue payload is retained anywhere, so the venue's own
-- closedTime/endDate is NOT RETAINED and the gamma.py branch that produced a
-- given row cannot be recovered. Promoting C would be exactly the speculative
-- category the order forbids. The sub-second/whole-second split is therefore
-- reported as a CROSS-CUTTING FLAG (RESOLVED_AT_ORIGIN_HEURISTIC) laid over
-- the evidence classes, never as a class that steals rows from them.
--
-- CLASS PRIORITY, and why: D before B. A suspect condition-to-market join
-- means the two timestamps are not known to describe the same market at all,
-- so no timing conclusion may be drawn from them.
--
--   D  MARKET_METADATA_OR_JOIN_SUSPECT
--   B  LIVE_FILL_AFTER_VENUE_TIME          (chain / poll / s1 after resolved_at)
--   A  BACKFILL_ONLY_POST_RESOLVED         (archival fills only)
--   E  TIMESTAMP_SEMANTICS_UNRESOLVED      (no safe explanation)
--
-- ESTIMATOR A IS NOT AFFECTED. It needs no settlement, so anomaly rows are
-- never excluded from it. This gate governs settlement-dependent work only.
-- ============================================================================

\echo '== 0. THE ANOMALY POPULATION, sized against every relevant denominator =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id, condition_id, asset, source, notional, ts, sport
    FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), u2ids AS MATERIALIZED (
  SELECT t.id, t.condition_id
    FROM u0 t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at, resolved_prices, updated_at
    FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), anom AS MATERIALIZED (
  SELECT r.condition_id
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at
  HAVING max(t.ts) > r.resolved_at
), f AS (
  SELECT t.*, (t.condition_id IN (SELECT condition_id FROM anom)) AS in_anom
    FROM u0 t
)
SELECT '2026-09-12T00:00:00Z'                                  AS audit_cutoff_ts,
       (SELECT count(*) FROM anom)                             AS anomaly_conditions,
       (SELECT count(*) FROM res)                              AS resolved_conditions_all,
       (SELECT count(DISTINCT condition_id) FROM u0)           AS u0_conditions,
       count(*) FILTER (WHERE in_anom)                         AS anomaly_fills,
       round(sum(notional) FILTER (WHERE in_anom)::numeric, 2) AS anomaly_acq_notional,
       count(*)                                                AS u0_fills,
       round(sum(notional)::numeric, 2)                        AS u0_notional,
       round(100.0 * count(*) FILTER (WHERE in_anom)
             / NULLIF(count(*), 0), 3)                         AS pct_of_u0_fills,
       round(100.0 * sum(notional) FILTER (WHERE in_anom)
             / NULLIF(sum(notional), 0), 3)                    AS pct_of_u0_notional,
       (SELECT count(*) FROM u2ids
         WHERE condition_id IN (SELECT condition_id FROM anom)) AS anomaly_u2_events,
       (SELECT count(*) FROM u2ids)                            AS u2_events,
       round(100.0 * (SELECT count(*) FROM u2ids
                       WHERE condition_id IN (SELECT condition_id FROM anom))
             / NULLIF((SELECT count(*) FROM u2ids), 0), 3)     AS pct_of_u2_events,
       round(100.0 * (SELECT count(*) FROM anom)
             / NULLIF((SELECT count(*) FROM res), 0), 3)       AS pct_of_resolved_conditions
  FROM f;

\echo ''
\echo '== 1. POST-RESOLVED FILL MEASURES -- the derived quantities, by lane =='
-- POST_RESOLVED_* count only fills whose ts is AFTER the stored resolved_at.
-- "live" means chain / poll / s1. backfill is archival and is separated.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id, condition_id, source, notional, ts
    FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), pr AS MATERIALIZED (
  SELECT t.condition_id, t.source, t.notional, t.ts, r.resolved_at,
         (t.ts > r.resolved_at) AS post_resolved
    FROM u0 t JOIN res r ON r.condition_id = t.condition_id
)
SELECT source                                                  AS lane,
       count(*)                                                AS fills_on_resolved_conditions,
       count(*) FILTER (WHERE post_resolved)                    AS post_resolved_fill_count,
       round(sum(notional) FILTER (WHERE post_resolved)::numeric, 2)
                                                               AS post_resolved_fill_notional,
       count(*) FILTER (WHERE post_resolved
                          AND source IN ('chain', 'poll', 's1')) AS post_resolved_live_fill_count,
       round(sum(notional) FILTER (WHERE post_resolved
                          AND source IN ('chain', 'poll', 's1'))::numeric, 2)
                                                               AS post_resolved_live_fill_notional,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY extract(epoch FROM ts - resolved_at) / 3600.0)
             FILTER (WHERE post_resolved)::numeric, 3)         AS hours_past_resolve_p50,
       round(max(extract(epoch FROM ts - resolved_at) / 3600.0)
             FILTER (WHERE post_resolved)::numeric, 3)         AS hours_past_resolve_max,
       CASE WHEN count(*) FILTER (WHERE post_resolved) = 0
            THEN 'NOT TESTED -- no post-resolved fill in this lane'
            ELSE '' END                                        AS note
  FROM pr GROUP BY source ORDER BY post_resolved_fill_count DESC;

\echo ''
\echo '== 2. CLASSIFICATION -- evidence-supported, D before B, C not assigned =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id, condition_id, asset, source, notional, ts, sport
    FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at, resolved_prices FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), tok AS MATERIALIZED (
  SELECT condition_id, count(*) AS token_count
    FROM market_tokens GROUP BY condition_id
), cond AS MATERIALIZED (
  SELECT r.condition_id, r.resolved_at,
         COALESCE(k.token_count, 0)                            AS token_count,
         jsonb_array_length(COALESCE(r.resolved_prices, '[]'::jsonb)) AS price_count,
         count(t.id)                                           AS fills,
         round(sum(t.notional)::numeric, 2)                    AS acq_notional,
         count(t.id) FILTER (WHERE t.ts > r.resolved_at)        AS post_fills,
         count(t.id) FILTER (WHERE t.ts > r.resolved_at
                               AND t.source IN ('chain','poll','s1')) AS post_live,
         count(t.id) FILTER (WHERE t.ts > r.resolved_at
                               AND t.source = 'backfill')       AS post_backfill,
         count(t.id) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM market_tokens mt
                 WHERE mt.condition_id = r.condition_id
                   AND mt.token_id = t.asset))                  AS fills_with_no_token_row
    FROM res r
    JOIN u0 t ON t.condition_id = r.condition_id
    LEFT JOIN tok k ON k.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at, k.token_count, r.resolved_prices
  HAVING max(t.ts) > r.resolved_at
), classed AS (
  SELECT c.*,
         CASE
           WHEN c.token_count = 0
             OR c.fills_with_no_token_row > 0
             OR (c.price_count > 0 AND c.price_count <> c.token_count)
             THEN 'D MARKET_METADATA_OR_JOIN_SUSPECT'
           WHEN c.post_live > 0
             THEN 'B LIVE_FILL_AFTER_VENUE_TIME'
           WHEN c.post_backfill > 0 AND c.post_live = 0
             THEN 'A BACKFILL_ONLY_POST_RESOLVED'
           ELSE 'E TIMESTAMP_SEMANTICS_UNRESOLVED'
         END AS anomaly_class
    FROM cond c
)
SELECT anomaly_class,
       count(*)                                   AS conditions,
       sum(fills)                                 AS fills,
       round(sum(acq_notional)::numeric, 2)       AS acq_notional,
       sum(post_fills)                            AS post_resolved_fills,
       sum(post_live)                             AS post_resolved_live_fills,
       sum(post_backfill)                         AS post_resolved_backfill_fills,
       count(*) FILTER (WHERE token_count = 0)    AS with_zero_tokens,
       count(*) FILTER (WHERE fills_with_no_token_row > 0)
                                                  AS with_a_fill_off_the_token_list,
       count(*) FILTER (WHERE price_count > 0 AND price_count <> token_count)
                                                  AS price_vector_len_mismatch
  FROM classed GROUP BY anomaly_class ORDER BY conditions DESC;

\echo ''
\echo '== 3. SEGMENTS -- lane mix, sport, outcome structure, mapping, precision =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id, condition_id, asset, source, notional, ts, sport
    FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), tok AS MATERIALIZED (
  SELECT condition_id, count(*) AS token_count
    FROM market_tokens GROUP BY condition_id
), mapped AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM mirror_books WHERE lower(whale) = 'rn1'
  UNION
  SELECT DISTINCT condition_id FROM mirror_shadow WHERE lower(whale) = 'rn1'
), cond AS MATERIALIZED (
  SELECT r.condition_id, r.resolved_at,
         max(t.sport)                                          AS sport,
         COALESCE(k.token_count, 0)                            AS token_count,
         count(t.id)                                           AS fills,
         round(sum(t.notional)::numeric, 2)                    AS acq_notional,
         count(t.id) FILTER (WHERE t.ts > r.resolved_at)        AS post_fills,
         count(t.id) FILTER (WHERE t.ts > r.resolved_at
                               AND t.source IN ('chain','poll','s1')) AS post_live,
         (m.condition_id IS NOT NULL)                          AS pmus_mapped_ever
    FROM res r
    JOIN u0 t ON t.condition_id = r.condition_id
    LEFT JOIN tok k ON k.condition_id = r.condition_id
    LEFT JOIN mapped m ON m.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at, k.token_count, m.condition_id
  HAVING max(t.ts) > r.resolved_at
)
SELECT COALESCE(sport, '(null)')                               AS sport,
       CASE WHEN token_count = 2 THEN 'two-leg'
            WHEN token_count = 1 THEN 'single-leg'
            WHEN token_count = 0 THEN 'NO TOKEN ROWS'
            ELSE token_count || '-leg' END                     AS outcome_structure,
       CASE WHEN pmus_mapped_ever THEN 'mapped (coarse proxy)'
            ELSE 'never mapped' END                            AS pmus_status,
       CASE WHEN extract(epoch FROM resolved_at)
                 = floor(extract(epoch FROM resolved_at))
            THEN 'whole-second' ELSE 'sub-second' END          AS resolved_at_origin_heuristic,
       count(*)                                                AS conditions,
       sum(fills)                                              AS fills,
       round(sum(acq_notional)::numeric, 2)                    AS acq_notional,
       sum(post_fills)                                         AS post_resolved_fills,
       sum(post_live)                                          AS post_resolved_live_fills
  FROM cond
 GROUP BY 1, 2, 3, 4
 ORDER BY conditions DESC
 LIMIT 40;

\echo ''
\echo '== 4. RESOLVED_AT_ORIGIN_HEURISTIC -- association only, NOT proof of branch =='
-- A LEAD, NOT A FACT. Postgres now() carries microsecond precision and a parsed
-- venue closedTime usually does not, but the venue COULD emit sub-second times
-- and the raw field is NOT RETAINED (markets has no ALTER in any migration and
-- no raw venue payload is stored anywhere), so the branch that wrote a given
-- row cannot be recovered. This measures whether the flag is ASSOCIATED with
-- the anomaly. It does not license the label "fetch fallback".
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT condition_id, ts FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), cond AS (
  SELECT r.condition_id,
         CASE WHEN extract(epoch FROM r.resolved_at)
                   = floor(extract(epoch FROM r.resolved_at))
              THEN 'whole-second' ELSE 'sub-second' END AS precision_class,
         (max(t.ts) > r.resolved_at)                    AS is_anomalous
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at
)
SELECT precision_class,
       count(*)                                              AS conditions,
       count(*) FILTER (WHERE is_anomalous)                   AS anomalous,
       round(100.0 * count(*) FILTER (WHERE is_anomalous)
             / NULLIF(count(*), 0), 3)                        AS anomaly_rate_pct,
       'RESOLVED_AT_ORIGIN_HEURISTIC -- association, not branch proof; the raw '
       'venue closedTime/endDate is NOT RETAINED'              AS standing_label
  FROM cond GROUP BY precision_class ORDER BY conditions DESC;

\echo ''
\echo '== 5. THE WORST CONDITIONS BY POST-RESOLVED NOTIONAL -- a bounded sample =='
-- Bounded to 30 rows by the runner's output cap. This is a SAMPLE for eyeballing
-- provenance, not the population: statements 0-4 carry the population.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id, condition_id, asset, source, notional, ts, sport, market_slug, event_slug
    FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at, resolved_prices, updated_at FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), cond AS (
  SELECT r.condition_id, r.resolved_at, r.resolved_prices, r.updated_at,
         max(t.sport)                                          AS sport,
         max(t.market_slug)                                    AS market_slug,
         min(t.ts)                                             AS first_fill_ts,
         max(t.ts)                                             AS last_fill_ts,
         count(t.id)                                           AS fills,
         string_agg(DISTINCT t.source, ',' ORDER BY t.source)  AS lanes,
         round(sum(t.notional)::numeric, 2)                    AS acq_notional,
         count(t.id) FILTER (WHERE t.ts > r.resolved_at)        AS post_fills,
         round(sum(t.notional) FILTER (WHERE t.ts > r.resolved_at)::numeric, 2)
                                                               AS post_notional,
         count(t.id) FILTER (WHERE t.ts > r.resolved_at
                               AND t.source IN ('chain','poll','s1')) AS post_live
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at, r.resolved_prices, r.updated_at
  HAVING max(t.ts) > r.resolved_at
)
SELECT left(condition_id, 14) || '...'                         AS condition_id,
       left(COALESCE(market_slug, '(null)'), 34)               AS market_slug,
       COALESCE(sport, '(null)')                               AS sport,
       lanes,
       fills, post_fills, post_live,
       post_notional,
       to_char(resolved_at, 'MM-DD HH24:MI:SS')                AS resolved_at,
       to_char(last_fill_ts, 'MM-DD HH24:MI:SS')               AS last_fill,
       round(extract(epoch FROM last_fill_ts - resolved_at) / 3600.0, 2)
                                                               AS hours_past,
       COALESCE(resolved_prices::text, '(null)')               AS resolved_prices
  FROM cond ORDER BY post_notional DESC NULLS LAST LIMIT 30;

\echo ''
\echo '== 6. RAW VENUE FIELD AVAILABILITY -- named, not skipped =='
SELECT 'markets.<raw venue closedTime / endDate>'              AS requested_field,
       'NOT RETAINED'                                          AS availability,
       'the markets table carries no schema change in any migration and no raw '
       'venue payload table exists; gamma.py parses closedTime/closed_time/'
       'endDate/end_date_iso and stores ONLY the parsed result in resolved_at'
                                                               AS evidence,
       'the gamma.py write branch for a given row is NOT RECOVERABLE'
                                                               AS consequence;

\echo ''
\echo '== 7. SETTLEMENT-ELIGIBILITY VERDICT + the mechanical exclusion rule =='
-- ONE verdict. The exclusion rule is a PREDICATE, never judgment by row.
--   EXCLUSION RULE (if needed):
--     exclude every condition C where
--       markets.resolved_at IS NOT NULL
--       AND EXISTS a retained RN1 fill on C with ts > markets.resolved_at
--     at the pinned cutoff. Whole conditions, not individual fills, because a
--     condition with a corrupted resolution stamp corrupts every payout on it.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id, condition_id, notional, ts FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), u2 AS MATERIALIZED (
  SELECT t.id, t.condition_id, t.notional
    FROM u0 t JOIN copy_probes c ON c.trade_id = t.id
   WHERE c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), anom AS MATERIALIZED (
  SELECT r.condition_id
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at
  HAVING max(t.ts) > r.resolved_at
), s AS (
  SELECT u.condition_id, u.notional,
         (u.condition_id IN (SELECT condition_id FROM anom)) AS excluded,
         (u.condition_id IN (SELECT condition_id FROM res))  AS resolved_by_cutoff
    FROM u2 u
)
SELECT count(*) FILTER (WHERE resolved_by_cutoff)               AS settlement_cohort_events,
       round(sum(notional) FILTER (WHERE resolved_by_cutoff)::numeric, 2)
                                                               AS settlement_cohort_notional,
       count(DISTINCT condition_id) FILTER (WHERE excluded)     AS excluded_conditions,
       count(*) FILTER (WHERE excluded AND resolved_by_cutoff)  AS excluded_events,
       round(sum(notional) FILTER (WHERE excluded
                                     AND resolved_by_cutoff)::numeric, 2)
                                                               AS excluded_notional,
       round(100.0 * count(*) FILTER (WHERE excluded AND resolved_by_cutoff)
             / NULLIF(count(*) FILTER (WHERE resolved_by_cutoff), 0), 3)
                                                               AS excluded_pct_of_cohort,
       count(*) FILTER (WHERE resolved_by_cutoff AND NOT excluded)
                                                               AS surviving_events,
       round(sum(notional) FILTER (WHERE resolved_by_cutoff
                                     AND NOT excluded)::numeric, 2)
                                                               AS surviving_notional,
       CASE
         WHEN count(DISTINCT condition_id) FILTER (WHERE excluded) = 0
           THEN 'SETTLEMENT_POPULATION_SAFE_AS_IS'
         WHEN count(*) FILTER (WHERE resolved_by_cutoff AND NOT excluded) = 0
           THEN 'SETTLEMENT_POPULATION_NOT_SAFE -- nothing survives the rule'
         ELSE 'SETTLEMENT_POPULATION_SAFE_WITH_EXCLUSIONS -- exclude whole '
              'conditions carrying a fill after their own resolved_at'
       END                                                     AS verdict
  FROM s;

\echo ''
\echo '== 8. WITNESS LEDGER =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), w(ord, test_name, witnesses, why_if_zero) AS (
  SELECT 1, 'resolved conditions RN1 traded (the frame)',
         (SELECT count(DISTINCT m.condition_id) FROM markets m
            JOIN trades t ON t.condition_id = m.condition_id
           WHERE t.whale_id IN (SELECT id FROM rn1)
             AND m.resolved AND m.resolved_at IS NOT NULL
             AND m.resolved_at <= timestamptz '2026-09-12 00:00:00+00'
             AND t.ts <= timestamptz '2026-09-12 00:00:00+00'),
         'no condition resolved at or before the cutoff'
  UNION ALL
  SELECT 2, 'RN1 fills on resolved conditions (classification input)',
         (SELECT count(*) FROM trades t
            JOIN markets m ON m.condition_id = t.condition_id
           WHERE t.whale_id IN (SELECT id FROM rn1)
             AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
             AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
             AND m.resolved AND m.resolved_at IS NOT NULL
             AND m.resolved_at <= timestamptz '2026-09-12 00:00:00+00'),
         'no fills on resolved conditions'
  UNION ALL
  SELECT 3, 'market_tokens rows for those conditions (join test input)',
         (SELECT count(*) FROM market_tokens mt
           WHERE mt.condition_id IN (
                 SELECT DISTINCT t.condition_id FROM trades t
                  WHERE t.whale_id IN (SELECT id FROM rn1)
                    AND t.ts <= timestamptz '2026-09-12 00:00:00+00')),
         'no token rows -- the join test would be vacuous'
)
SELECT test_name, witnesses,
       CASE WHEN witnesses = 0 THEN 'NOT TESTED' ELSE 'TESTED' END AS verdict,
       CASE WHEN witnesses = 0 THEN why_if_zero ELSE '' END AS reason_if_not_tested
  FROM w ORDER BY ord;

\echo ''
\echo '== RUN 80.5 ENDS. No economics. Estimator A is unaffected. mirror_live=false. =='
