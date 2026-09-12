-- ============================================================================
-- RUN 80.5b -- IS THE ANOMALY ARCHIVAL TIMING SEMANTICS? MEASURED, NOT EYEBALLED
-- (2026-09-12, read-only. Nothing here writes. mirror_live = false.)
--
-- WHY THIS EXISTS, AND WHY IT IS INSIDE THE 80.5 MANDATE RATHER THAN BEYOND IT.
-- The order requires 80.5 to "determine whether the anomaly is concentrated in:
-- 1. archival timing semantics, 2. actual live fills after venue
-- close/resolution timestamps, 3. metadata/join defects, or remains
-- mixed/unresolved." Run 80.5 answered 3 (zero join defects) and split 1 vs 2 BY
-- INGESTION LANE -- which is not the same question. This closes it.
--
-- THE LEAD, from 30/30 rows of run 80.5's sample and from the code:
--
--   gamma.py:67-79
--     for key in ("closedTime", "closed_time", "endDate", "end_date_iso"):
--         parsed = datetime.fromisoformat(val.replace("Z","+00:00")...)
--         resolved_time = min(parsed, now())
--
--   datetime.fromisoformat("2025-11-05") == 2025-11-05 00:00:00, so an endDate
--   supplied as a BARE DATE becomes MIDNIGHT UTC of that date and is stored in
--   resolved_at, a column the rest of the system reads as "when the event
--   resolved". Every fill during that day's game then lands AFTER it.
--
-- Every sampled anomaly -- class A and class B alike -- carried resolved_at at
-- exactly MM-DD 00:00:00. If that holds at population scale then the A/B split
-- is only WHICH LANE INGESTED THE FILLS, not two mechanisms, and class B is NOT
-- evidence of trading after resolution.
--
-- THIS STATEMENT DOES NOT ASSUME THAT. It measures it, and it measures the
-- control: how often NON-anomalous conditions also carry a midnight stamp. A
-- midnight rate that is high everywhere would refute the reading.
--
-- No economics. Estimator A unaffected. ai_trades / TRUEEDGE untouched.
-- AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z, each table on its designated field.
-- ============================================================================

\echo '== 1. MIDNIGHT x ANOMALOUS -- the cross-tab, with its own control =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT condition_id, notional, ts FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), cond AS (
  SELECT r.condition_id, r.resolved_at,
         (r.resolved_at = date_trunc('day', r.resolved_at)) AS midnight_utc,
         (max(t.ts) > r.resolved_at)                        AS anomalous,
         count(t.condition_id)                              AS fills,
         sum(t.notional)                                    AS notional
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at
)
SELECT CASE WHEN midnight_utc THEN 'resolved_at IS exactly midnight UTC'
            ELSE 'resolved_at has a time of day' END          AS stamp_shape,
       CASE WHEN anomalous THEN 'ANOMALOUS (a fill after it)'
            ELSE 'clean' END                                  AS status,
       count(*)                                               AS conditions,
       sum(fills)                                             AS fills,
       round(sum(notional)::numeric, 2)                       AS notional,
       round(100.0 * count(*) / NULLIF(sum(count(*)) OVER (), 0), 3)
                                                              AS pct_of_conditions
  FROM cond GROUP BY 1, 2 ORDER BY 1, 2;

\echo ''
\echo '== 2. THE RATES THAT SETTLE IT =='
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
         (r.resolved_at = date_trunc('day', r.resolved_at)) AS midnight_utc,
         (max(t.ts) > r.resolved_at)                        AS anomalous
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at
)
SELECT count(*)                                                AS conditions,
       count(*) FILTER (WHERE anomalous)                       AS anomalous,
       count(*) FILTER (WHERE midnight_utc)                    AS midnight_stamps,
       count(*) FILTER (WHERE anomalous AND midnight_utc)      AS anomalous_and_midnight,
       round(100.0 * count(*) FILTER (WHERE anomalous AND midnight_utc)
             / NULLIF(count(*) FILTER (WHERE anomalous), 0), 3)
                                                               AS pct_of_anomalies_at_midnight,
       round(100.0 * count(*) FILTER (WHERE anomalous AND midnight_utc)
             / NULLIF(count(*) FILTER (WHERE midnight_utc), 0), 3)
                                                               AS anomaly_rate_within_midnight,
       round(100.0 * count(*) FILTER (WHERE anomalous AND NOT midnight_utc)
             / NULLIF(count(*) FILTER (WHERE NOT midnight_utc), 0), 3)
                                                               AS anomaly_rate_within_timed,
       count(*) FILTER (WHERE anomalous AND NOT midnight_utc)  AS anomalous_with_a_real_time,
       CASE
         WHEN count(*) FILTER (WHERE anomalous) = 0
           THEN 'NOT TESTED -- no anomalies'
         WHEN count(*) FILTER (WHERE anomalous AND NOT midnight_utc) = 0
           THEN 'EVERY anomaly carries a midnight stamp -- the endDate-as-date '
                'reading is supported and no anomaly needs another explanation'
         ELSE 'MIXED -- some anomalies carry a real time of day and are NOT '
              'explained by the midnight mechanism; those are the residual'
       END                                                     AS verdict;

\echo ''
\echo '== 3. THE RESIDUAL -- anomalies that midnight does NOT explain =='
-- If this is empty the mechanism accounts for the whole population. If it is
-- not, these rows are the ones that still need an explanation and they are
-- named here rather than folded away.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT condition_id, notional, ts, source, market_slug, sport FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), res AS MATERIALIZED (
  SELECT condition_id, resolved_at FROM markets
   WHERE resolved AND resolved_at IS NOT NULL
     AND resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), cond AS (
  SELECT r.condition_id, r.resolved_at,
         max(t.market_slug)                                  AS market_slug,
         max(t.sport)                                        AS sport,
         string_agg(DISTINCT t.source, ',' ORDER BY t.source) AS lanes,
         max(t.ts)                                           AS last_fill,
         count(*) FILTER (WHERE t.ts > r.resolved_at)         AS post_fills,
         round(sum(t.notional) FILTER (WHERE t.ts > r.resolved_at)::numeric, 2)
                                                             AS post_notional
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at
  HAVING max(t.ts) > r.resolved_at
     AND r.resolved_at <> date_trunc('day', r.resolved_at)
)
SELECT left(condition_id, 14) || '...'                        AS condition_id,
       left(COALESCE(market_slug, '(null)'), 32)              AS market_slug,
       COALESCE(sport, '(null)')                              AS sport,
       lanes, post_fills, post_notional,
       to_char(resolved_at, 'YYYY-MM-DD HH24:MI:SS')          AS resolved_at,
       to_char(last_fill, 'YYYY-MM-DD HH24:MI:SS')            AS last_fill,
       round(extract(epoch FROM last_fill - resolved_at) / 60.0, 2)
                                                              AS minutes_past
  FROM cond ORDER BY post_notional DESC NULLS LAST LIMIT 40;

\echo ''
\echo '== 4. TWO CANDIDATE EXCLUSION RULES, priced side by side =='
-- RULE 1 (run 80.5, blunt): exclude every condition carrying a fill after its
--   own resolved_at.
-- RULE 2 (mechanism-aware): exclude only conditions carrying a fill after a
--   resolved_at that is NOT a midnight stamp -- i.e. the residual that the
--   endDate-as-date reading does not explain.
-- BOTH ARE MECHANICAL PREDICATES. Neither is judgment by row. The choice
-- between them is the owner's, and it turns on whether a midnight resolved_at
-- is taken to corrupt the PAYOUT or only the TIME.
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
  SELECT r.condition_id,
         (r.resolved_at = date_trunc('day', r.resolved_at)) AS midnight_utc
    FROM res r JOIN u0 t ON t.condition_id = r.condition_id
   GROUP BY r.condition_id, r.resolved_at
  HAVING max(t.ts) > r.resolved_at
), s AS (
  SELECT u.condition_id, u.notional,
         (u.condition_id IN (SELECT condition_id FROM res))   AS in_cohort,
         (u.condition_id IN (SELECT condition_id FROM anom))  AS rule1_excluded,
         (u.condition_id IN (SELECT condition_id FROM anom
                              WHERE NOT midnight_utc))        AS rule2_excluded
    FROM u2 u
)
SELECT count(*) FILTER (WHERE in_cohort)                       AS cohort_events,
       round(sum(notional) FILTER (WHERE in_cohort)::numeric, 2)
                                                               AS cohort_notional,
       count(*) FILTER (WHERE in_cohort AND rule1_excluded)     AS rule1_excluded_events,
       round(sum(notional) FILTER (WHERE in_cohort
                                     AND rule1_excluded)::numeric, 2)
                                                               AS rule1_excluded_notional,
       round(100.0 * count(*) FILTER (WHERE in_cohort AND rule1_excluded)
             / NULLIF(count(*) FILTER (WHERE in_cohort), 0), 3) AS rule1_pct,
       count(*) FILTER (WHERE in_cohort AND rule2_excluded)     AS rule2_excluded_events,
       round(sum(notional) FILTER (WHERE in_cohort
                                     AND rule2_excluded)::numeric, 2)
                                                               AS rule2_excluded_notional,
       round(100.0 * count(*) FILTER (WHERE in_cohort AND rule2_excluded)
             / NULLIF(count(*) FILTER (WHERE in_cohort), 0), 3) AS rule2_pct
  FROM s;

\echo ''
\echo '== 5. WITNESS LEDGER =='
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
  SELECT 2, 'conditions carrying a NON-midnight resolved_at (the control)',
         (SELECT count(DISTINCT m.condition_id) FROM markets m
            JOIN trades t ON t.condition_id = m.condition_id
           WHERE t.whale_id IN (SELECT id FROM rn1)
             AND m.resolved AND m.resolved_at IS NOT NULL
             AND m.resolved_at <= timestamptz '2026-09-12 00:00:00+00'
             AND m.resolved_at <> date_trunc('day', m.resolved_at)
             AND t.ts <= timestamptz '2026-09-12 00:00:00+00'),
         'every stamp is midnight -- the cross-tab has no control arm and '
         'statement 2 is NOT TESTED rather than supportive'
)
SELECT test_name, witnesses,
       CASE WHEN witnesses = 0 THEN 'NOT TESTED' ELSE 'TESTED' END AS verdict,
       CASE WHEN witnesses = 0 THEN why_if_zero ELSE '' END AS reason_if_not_tested
  FROM w ORDER BY ord;

\echo ''
\echo '== RUN 80.5b ENDS. No economics. mirror_live=false. =='
