-- ============================================================================
-- RUN 80 -- THE POPULATION AND PROVENANCE RUN FOR THE TWO ESTIMATORS
-- (2026-09-12, read-only. Nothing here writes. mirror_live = false.)
--
-- APPROVED SCOPE, owner 2026-09-12: "approved for RUN 80 ONLY", after design
-- revision 2 plus two final corrections. NO ECONOMICS. No drag figure, no
-- latency figure. Run 81 does NOT follow automatically.
--
-- AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z, immutable. Applied per table on that
-- table's OWN arrival clock, never as one global filter. Printed in statement
-- 0 and re-proved in statement 9: run 79 read U0 as 962,459 in one statement
-- and 962,454 in another because five rows landed mid-run, and that must not
-- recur.
--
-- ---------------------------------------------------------------------------
-- CORRECTION 1 -- BACKFILL IS FENCED OUT OF THE ONLINE HEADLINE.
--
-- Estimator A does not mathematically need a source clock, so a backfill row
-- carrying an exact probe IS a valid retrospective price comparison. It is NOT
-- an observation BETTOR could have reacted to. So:
--
--     A_CHAIN / A_POLL / A_S1
--     A_ONLINE_COMBINED     = chain + poll + s1      <- operational
--     A_BACKFILL_DIAGNOSTIC = backfill, ALONE        <- never pooled in
--
-- No aggregate containing backfill may be called "reactive replicability",
-- "live first observation", or any equivalent.
--
-- ---------------------------------------------------------------------------
-- CORRECTION 2 -- U4 IS RENAMED EVENT_RESOLVED_BY_CUTOFF.
--
-- markets.resolved_at proves the EVENT RESOLVED. It does not prove BETTOR KNEW.
-- gamma.py writes resolved_at = COALESCE(<the venue's own closedTime/endDate>,
-- now()), so the column carries TWO semantics in one field -- the venue's clock
-- where the venue supplied a time, our fetch clock where it did not -- and
-- markets.updated_at is overwritten on every upsert so it bounds nothing.
-- There is no durable settlement-arrival timestamp, so no point-in-time
-- known-settlement cohort can be defined. Statement 8 censuses the column
-- rather than trusting it.
--
-- ---------------------------------------------------------------------------
-- WITNESS DISCIPLINE. A zero violation count beside a zero witness count is
-- NOT TESTED -- never PASS, never a silent NOT APPLICABLE. Statement 9 carries
-- the ledger.
--
-- U1 DOES NOT EXIST. Lane is a CARRIED ATTRIBUTE, never a universal filter.
-- ai_trades / TRUEEDGE are untouched: the name appears nowhere below.
-- ============================================================================

\echo '== 0. CUTOFF HEADER -- the pinned instant, and U0 at it =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
)
SELECT '2026-09-12T00:00:00Z'                       AS audit_cutoff_ts,
       (SELECT count(*) FROM trades
         WHERE whale_id IN (SELECT id FROM rn1))    AS rn1_rows_all_time,
       (SELECT count(*) FROM trades
         WHERE whale_id IN (SELECT id FROM rn1)
           AND ts <= timestamptz '2026-09-12 00:00:00+00'
           AND detected_at <= timestamptz '2026-09-12 00:00:00+00')
                                                    AS u0_at_cutoff,
       (SELECT round(sum(notional)::numeric, 2) FROM trades
         WHERE whale_id IN (SELECT id FROM rn1)
           AND ts <= timestamptz '2026-09-12 00:00:00+00'
           AND detected_at <= timestamptz '2026-09-12 00:00:00+00')
                                                    AS u0_notional,
       now()                                        AS run_started_about;

\echo ''
\echo '== 1. LANE CENSUS -- every lane named, and a HALT row if one is unknown =='
-- A lane that is not in the eligibility matrix is NOT pooled and NOT guessed.
-- It prints HALT, and no later statement in this run may be trusted if it does.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT source, notional, ts FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
)
SELECT source                                       AS lane,
       count(*)                                     AS events,
       round(sum(notional)::numeric, 2)             AS notional,
       to_char(min(ts), 'YYYY-MM-DD HH24:MI')       AS first_event,
       to_char(max(ts), 'YYYY-MM-DD HH24:MI')       AS last_event,
       CASE WHEN source IN ('backfill', 'chain', 'poll', 's1')
            THEN 'known -- see the eligibility matrix'
            ELSE '*** HALT: UNKNOWN LANE, NOT IN THE MATRIX ***'
       END                                          AS matrix_status,
       CASE source
         WHEN 'backfill' THEN 'A: diagnostic stratum ALONE | B: EXCLUDED'
         WHEN 'chain'    THEN 'A: online | B src->det: EXCLUDED (clock-corrupt)'
         WHEN 'poll'     THEN 'A: online | B src->det: POLL_SWEEP_CADENCE only'
         WHEN 's1'       THEN 'A: online | B src->det: S1_CROSS_CLOCK_SHAPE only'
         ELSE 'NONE -- halt'
       END                                          AS eligibility
  FROM u0 GROUP BY source ORDER BY events DESC;

\echo ''
\echo '== 2. U2 CONSTRUCTION, ONE PREDICATE AT A TIME =='
-- U2 is Estimator A''s primary population. It requires NO source clock and NO
-- settlement: it is defined by what we OBSERVED. Each step prints what it costs
-- in events, conditions and dollars, so the definition is auditable rather than
-- a single number at the end.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id, notional, condition_id, price FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), cp AS MATERIALIZED (
  SELECT trade_id, book_ok, error, (depth IS NOT NULL) AS has_depth,
         jsonb_array_length(COALESCE(depth, '[]'::jsonb)) AS lv
    FROM copy_probes
   WHERE whale_id IN (SELECT id FROM rn1)
     AND trade_id IS NOT NULL
     AND probe_at <= timestamptz '2026-09-12 00:00:00+00'
), j AS (
  SELECT u.id, u.notional, u.condition_id, u.price,
         (c.trade_id IS NOT NULL)                       AS s1_linked,
         COALESCE(c.book_ok, false)                     AS s2_book_ok,
         (c.trade_id IS NOT NULL AND c.error IS NULL)   AS s3_no_error,
         (u.price > 0 AND u.price < 1)                  AS s4_price_valid,
         COALESCE(c.has_depth, false)                   AS s5_depth,
         COALESCE(c.lv, 0) > 0                          AS s6_levels
    FROM u0 u LEFT JOIN cp c ON c.trade_id = u.id
), steps(ord, predicate, events, conditions, notional) AS (
  SELECT 0, 'U0 at the cutoff', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2) FROM j
  UNION ALL SELECT 1, '+ exact trade_id linkage to a probe', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM j WHERE s1_linked
  UNION ALL SELECT 2, '+ book_ok', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2) FROM j WHERE s1_linked AND s2_book_ok
  UNION ALL SELECT 3, '+ probe error IS NULL', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM j WHERE s1_linked AND s2_book_ok AND s3_no_error
  UNION ALL SELECT 4, '+ source price valid (0 < p_h < 1)', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM j WHERE s1_linked AND s2_book_ok AND s3_no_error AND s4_price_valid
  UNION ALL SELECT 5, '+ depth array present', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM j WHERE s1_linked AND s2_book_ok AND s3_no_error AND s4_price_valid
           AND s5_depth
  UNION ALL SELECT 6, '= U2 (+ at least one depth level)', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM j WHERE s1_linked AND s2_book_ok AND s3_no_error AND s4_price_valid
           AND s5_depth AND s6_levels
)
SELECT predicate, events, conditions, notional,
       lag(events) OVER (ORDER BY ord) - events       AS events_lost_here,
       lag(notional) OVER (ORDER BY ord) - notional   AS notional_lost_here
  FROM steps ORDER BY ord;

\echo ''
\echo '== 3. U2 LANE MIX -- the five Estimator-A strata, backfill fenced out =='
-- A_ONLINE_COMBINED is chain + poll + s1. A_BACKFILL_DIAGNOSTIC stands alone
-- and is NEVER pooled into it.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u2 AS MATERIALIZED (
  SELECT t.id, t.source, t.notional, t.condition_id, t.size
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND t.price > 0 AND t.price < 1
), strata(ord, stratum, events, conditions, notional, shares) AS (
  SELECT 1, 'A_CHAIN', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2), round(sum(size)::numeric, 0)
    FROM u2 WHERE source = 'chain'
  UNION ALL SELECT 2, 'A_POLL', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2), round(sum(size)::numeric, 0)
    FROM u2 WHERE source = 'poll'
  UNION ALL SELECT 3, 'A_S1', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2), round(sum(size)::numeric, 0)
    FROM u2 WHERE source = 's1'
  UNION ALL SELECT 4, 'A_ONLINE_COMBINED (chain+poll+s1)', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2),
         round(sum(size)::numeric, 0)
    FROM u2 WHERE source IN ('chain', 'poll', 's1')
  UNION ALL SELECT 5, 'A_BACKFILL_DIAGNOSTIC (never pooled)', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2),
         round(sum(size)::numeric, 0)
    FROM u2 WHERE source = 'backfill'
  UNION ALL SELECT 6, 'U2 TOTAL (online + diagnostic)', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2),
         round(sum(size)::numeric, 0)
    FROM u2
)
SELECT stratum, events, conditions, notional, shares,
       CASE WHEN events = 0 THEN 'NOT TESTED -- no rows in this stratum'
            ELSE '' END AS note
  FROM strata ORDER BY ord;

\echo ''
\echo '== 4. DEPTH-ARRAY CENSUS -- what sizes the retained book can honestly price =='
-- DEPTH_EXHAUSTED means UNKNOWN BEYOND OBSERVED DEPTH. It is never an
-- extrapolated fill. The probe retains the TOP EIGHT ask levels only, so a size
-- the top eight cannot cover is not priceable from this snapshot at all.
--   q_a = 10% of his notional, uncapped     q_b = his own shares
--   q_c = a fixed $1,000 clip
-- All three are HYPOTHETICAL sizes, none canonical.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u2 AS MATERIALIZED (
  SELECT t.id, t.source, t.notional, t.size, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND t.price > 0 AND t.price < 1
), d AS (
  SELECT u.id, u.source, u.notional, u.size,
         jsonb_array_length(u.depth)                       AS levels,
         (SELECT COALESCE(sum((e ->> 0)::float8 * (e ->> 1)::float8), 0)
            FROM jsonb_array_elements(u.depth) AS e)       AS depth_usd,
         (SELECT COALESCE(sum((e ->> 1)::float8), 0)
            FROM jsonb_array_elements(u.depth) AS e)       AS depth_shares
    FROM u2 u
)
SELECT CASE WHEN source = 'backfill' THEN 'A_BACKFILL_DIAGNOSTIC'
            ELSE 'A_ONLINE_COMBINED' END                   AS stratum,
       count(*)                                            AS events,
       round(avg(levels)::numeric, 2)                      AS avg_levels,
       min(levels)                                         AS min_levels,
       max(levels)                                         AS max_levels,
       count(*) FILTER (WHERE levels >= 8)                 AS at_the_8_level_cap,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY depth_usd)::numeric, 2)              AS depth_usd_p50,
       count(*) FILTER (WHERE depth_usd < 0.10 * notional) AS exhausted_q_a,
       count(*) FILTER (WHERE depth_shares < size)         AS exhausted_q_b,
       count(*) FILTER (WHERE depth_usd < 1000)            AS exhausted_q_c,
       round(100.0 * count(*) FILTER (WHERE depth_usd < 0.10 * notional)
             / NULLIF(count(*), 0), 2)                     AS pct_exhausted_q_a,
       round(100.0 * count(*) FILTER (WHERE depth_shares < size)
             / NULLIF(count(*), 0), 2)                     AS pct_exhausted_q_b,
       round(100.0 * count(*) FILTER (WHERE depth_usd < 1000)
             / NULLIF(count(*), 0), 2)                     AS pct_exhausted_q_c
  FROM d GROUP BY 1 ORDER BY 1;

\echo ''
\echo '== 5. THE 120 s GATE vs reaction_s -- two different variables, side by side =='
-- The gate tests latency_s = max(detected_at - ts, 0), CLAMPED AT ZERO, so
-- every negative-lag row presents 0.000 and the gate never rejects one. The
-- stored column is reaction_s = probe_at - ts, stamped later, downstream of
-- detection, so it is always the larger quantity. A row over 120 s is NOT a
-- gate violation. The gate is not redefined here; both are printed.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u2 AS MATERIALIZED (
  SELECT t.source, t.notional, c.reaction_s,
         greatest(extract(epoch FROM t.detected_at - t.ts), 0) AS gate_latency_s
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
)
SELECT source                                              AS lane,
       count(*)                                            AS events,
       count(*) FILTER (WHERE reaction_s > 120)            AS reaction_over_120,
       round(100.0 * count(*) FILTER (WHERE reaction_s > 120)
             / NULLIF(count(*), 0), 3)                     AS pct_over_120,
       round(sum(notional) FILTER (WHERE reaction_s > 120)::numeric, 2)
                                                           AS notional_over_120,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY gate_latency_s)::numeric, 3)         AS gate_latency_p50,
       count(*) FILTER (WHERE gate_latency_s = 0)          AS gate_clamped_to_zero,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY reaction_s)::numeric, 3)             AS reaction_s_p50,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY reaction_s - gate_latency_s)::numeric, 3)
                                                           AS dispatch_p50_s,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY reaction_s - gate_latency_s)::numeric, 3)
                                                           AS dispatch_p90_s
  FROM u2 GROUP BY source ORDER BY events DESC;

\echo ''
\echo '== 6. t_send / t_reply POPULATION GATE -- mirror or not, MEASURED =='
-- Code evidence says these are written by live_executor (lane ioc/rest) and
-- that file excludes lane=mirror in its own reads. This statement measures the
-- population rather than trusting that. If the rows are NOT mirror rows the
-- fields stay an ENGINEERING DIAGNOSTIC and are not promoted to the primary
-- mirror estimator.
WITH lo AS MATERIALIZED (
  SELECT COALESCE(lane, '(null)') AS lane,
         lower(COALESCE(whale_username, '(null)')) AS whale,
         placed_at,
         (raw ? 't_send')   AS has_send,
         (raw ? 't_reply')  AS has_reply,
         (raw ? 't_detect') AS has_detect,
         -- THE CAST IS GUARDED. Under ON_ERROR_STOP=1 a single malformed value
         -- in one JSON body aborts the whole file and the run reads as a
         -- failure of the query rather than of one row. The regex admits only
         -- an epoch-shaped number, so a surprise string yields NULL and is
         -- counted as unparsable instead of killing the run.
         CASE WHEN (raw ->> 't_reply') ~ '^[0-9]+(\.[0-9]+)?$'
               AND (raw ->> 't_send')  ~ '^[0-9]+(\.[0-9]+)?$'
              THEN (raw ->> 't_reply')::float8 - (raw ->> 't_send')::float8
         END AS rtt_s
    FROM live_orders
   WHERE placed_at <= timestamptz '2026-09-12 00:00:00+00'
     AND raw IS NOT NULL AND jsonb_typeof(raw) = 'object'
)
SELECT lane, whale,
       count(*)                                         AS rows_with_raw,
       count(*) FILTER (WHERE has_send)                 AS with_t_send,
       count(*) FILTER (WHERE has_reply)                AS with_t_reply,
       count(*) FILTER (WHERE has_detect)               AS with_t_detect,
       to_char(min(placed_at) FILTER (WHERE has_send),
               'YYYY-MM-DD HH24:MI')                    AS first_timed,
       to_char(max(placed_at) FILTER (WHERE has_send),
               'YYYY-MM-DD HH24:MI')                    AS last_timed,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY rtt_s)::numeric, 3)               AS rtt_p50_s,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY rtt_s)::numeric, 3)               AS rtt_p90_s,
       CASE WHEN count(*) FILTER (WHERE has_send) = 0
            THEN 'NOT TESTED -- no timed rows in this lane'
            WHEN lane = 'mirror' THEN 'MIRROR LANE -- transportability not needed'
            ELSE 'NON-MIRROR -- ENGINEERING DIAGNOSTIC ONLY'
       END                                              AS population_verdict
  FROM lo GROUP BY lane, whale
 HAVING count(*) FILTER (WHERE has_send) > 0 OR count(*) > 500
 ORDER BY with_t_send DESC, rows_with_raw DESC;

\echo ''
\echo '== 7. MIRROR-PERIOD REPEATED-OBSERVATION CENSUS -- does a B cohort exist? =='
-- price_path stops 2026-09-04 23:15 and the mirror begins 2026-09-06 00:40, so
-- price_path can never answer the mirror-period question. The only candidate
-- repeated PMUS observation inside the mirror window is the shadow tick''s own
-- quote. This counts it. NOTE THE LIMIT IN ADVANCE: a shadow tick carries a
-- TOP-OF-BOOK quote, not a depth, so even a dense series supports a
-- top-of-book decay shape ONLY -- never a depth-walked executable price.
WITH sh AS MATERIALIZED (
  SELECT condition_id, at, ask
    FROM mirror_shadow
   WHERE lower(whale) = 'rn1'
     AND at >= timestamptz '2026-09-06 00:40:00+00'
     AND at <= timestamptz '2026-09-12 00:00:00+00'
), per_cond AS (
  SELECT condition_id,
         count(*)                              AS ticks,
         count(ask)                            AS ticks_with_ask,
         count(DISTINCT date_trunc('second', at)) AS distinct_seconds,
         extract(epoch FROM max(at) - min(at))  AS span_s
    FROM sh GROUP BY condition_id
)
SELECT count(*)                                         AS conditions_observed,
       sum(ticks)                                       AS total_ticks,
       sum(ticks_with_ask)                              AS ticks_carrying_a_quote,
       count(*) FILTER (WHERE ticks_with_ask >= 2)      AS conditions_with_2plus,
       count(*) FILTER (WHERE ticks_with_ask >= 10)     AS conditions_with_10plus,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY ticks_with_ask)::numeric, 1)      AS quotes_per_condition_p50,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY span_s)::numeric, 1)              AS observed_span_p50_s,
       CASE WHEN sum(ticks_with_ask) = 0
            THEN 'MIRROR_PERIOD_TIME_DECAY_CURVE = NOT IDENTIFIABLE FROM '
                 'RETAINED DATA (0 witnesses: NOT TESTED, not zero decay)'
            WHEN count(*) FILTER (WHERE ticks_with_ask >= 2) = 0
            THEN 'MIRROR_PERIOD_TIME_DECAY_CURVE = NOT IDENTIFIABLE -- no '
                 'condition has two quotes to compare'
            ELSE 'A REPEATED TOP-OF-BOOK SERIES EXISTS -- top-of-book shape '
                 'only, NO DEPTH; pre-mirror price_path may NOT substitute'
       END                                              AS decay_verdict
  FROM per_cond;

\echo ''
\echo '== 8. EVENT_RESOLVED_BY_CUTOFF -- the selection profile, and the column''s own provenance =='
-- RENAMED from "U4 / settlement known" by owner order. resolved_at proves the
-- EVENT RESOLVED; it does not prove BETTOR KNEW. The column is written as
-- COALESCE(<the venue''s own closedTime/endDate>, now()), so it carries two
-- semantics in one field and the two branches are not separable from retained
-- data unless a marker is found. This statement reports the selection AND
-- probes for such a marker.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u2 AS MATERIALIZED (
  SELECT t.id, t.source, t.notional, t.condition_id, t.asset, t.price, t.sport
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND t.price > 0 AND t.price < 1
), res AS MATERIALIZED (
  SELECT mt.token_id, m.resolved_at
    FROM market_tokens mt JOIN markets m USING (condition_id)
   WHERE m.resolved AND mt.outcome_index IS NOT NULL
     AND m.resolved_prices IS NOT NULL
     AND jsonb_array_length(m.resolved_prices) > mt.outcome_index
     AND m.resolved_at IS NOT NULL
     AND m.resolved_at <= timestamptz '2026-09-12 00:00:00+00'
), k AS (
  SELECT u.*, (r.token_id IS NOT NULL) AS resolved_by_cutoff
    FROM u2 u LEFT JOIN res r ON r.token_id = u.asset
)
SELECT CASE WHEN source = 'backfill' THEN 'A_BACKFILL_DIAGNOSTIC'
            ELSE 'A_ONLINE_COMBINED' END                  AS stratum,
       count(*)                                           AS u2_events,
       round(sum(notional)::numeric, 2)                   AS u2_notional,
       count(*) FILTER (WHERE resolved_by_cutoff)         AS resolved_events,
       round(sum(notional) FILTER (WHERE resolved_by_cutoff)::numeric, 2)
                                                          AS resolved_notional,
       round(100.0 * count(*) FILTER (WHERE resolved_by_cutoff)
             / NULLIF(count(*), 0), 2)                    AS retention_pct_events,
       round(100.0 * sum(notional) FILTER (WHERE resolved_by_cutoff)
             / NULLIF(sum(notional), 0), 2)               AS retention_pct_notional,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY price)::numeric, 4)
                                                          AS u2_price_p50,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY price)
             FILTER (WHERE resolved_by_cutoff)::numeric, 4)
                                                          AS resolved_price_p50,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY notional)::numeric, 2)
                                                          AS u2_size_p50,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY notional)
             FILTER (WHERE resolved_by_cutoff)::numeric, 2)
                                                          AS resolved_size_p50,
       count(DISTINCT sport)                              AS sports_in_u2,
       count(DISTINCT sport) FILTER (WHERE resolved_by_cutoff)
                                                          AS sports_resolved
  FROM k GROUP BY 1 ORDER BY 1;

\echo ''
\echo '== 8b. resolved_at PROVENANCE PROBE -- can the two write branches be told apart? =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), pairs AS MATERIALIZED (
  SELECT m.condition_id, m.resolved_at, max(t.ts) AS last_fill_ts
    FROM markets m
    JOIN trades t ON t.condition_id = m.condition_id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND m.resolved AND m.resolved_at IS NOT NULL
     AND m.resolved_at <= timestamptz '2026-09-12 00:00:00+00'
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
   GROUP BY m.condition_id, m.resolved_at
)
SELECT count(*)                                          AS resolved_conditions,
       count(*) FILTER (WHERE resolved_at < last_fill_ts) AS resolved_before_a_fill,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY extract(epoch FROM resolved_at - last_fill_ts)
             / 3600.0)::numeric, 2)                      AS hours_fill_to_resolve_p50,
       count(*) FILTER (WHERE extract(epoch FROM resolved_at) = floor(extract(epoch FROM resolved_at)))
                                                          AS whole_second_stamps,
       'the two gamma.py branches (venue closedTime vs our now()) carry no '
       'marker column; NOT SEPARABLE from retained data'  AS branch_verdict
  FROM pairs;

\echo ''
\echo '== 9. WITNESS LEDGER + THE CUTOFF DRIFT PROOF =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), w(ord, test_name, witnesses, why_if_zero) AS (
  SELECT 1, 'statement 1: U0 rows censused by lane',
         (SELECT count(*) FROM trades
           WHERE whale_id IN (SELECT id FROM rn1)
             AND ts <= timestamptz '2026-09-12 00:00:00+00'
             AND detected_at <= timestamptz '2026-09-12 00:00:00+00'),
         'no RN1 fills at or before the cutoff'
  UNION ALL
  SELECT 2, 'statement 2/3/4/5/8: U2 rows built',
         (SELECT count(*) FROM trades t JOIN copy_probes c ON c.trade_id = t.id
           WHERE t.whale_id IN (SELECT id FROM rn1)
             AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
             AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
             AND c.whale_id IN (SELECT id FROM rn1)
             AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
             AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
             AND t.price > 0 AND t.price < 1),
         'no exact fill-level observation survives the cutoff'
  UNION ALL
  SELECT 3, 'statement 6: live_orders rows carrying a JSON body',
         (SELECT count(*) FROM live_orders
           WHERE placed_at <= timestamptz '2026-09-12 00:00:00+00'
             AND raw IS NOT NULL AND jsonb_typeof(raw) = 'object'),
         'no raw body was ever stored'
  UNION ALL
  SELECT 4, 'statement 7: mirror-window shadow ticks',
         (SELECT count(*) FROM mirror_shadow
           WHERE lower(whale) = 'rn1'
             AND at >= timestamptz '2026-09-06 00:40:00+00'
             AND at <= timestamptz '2026-09-12 00:00:00+00'),
         'no shadow tick inside the mirror window'
  UNION ALL
  SELECT 5, 'statement 8b: resolved conditions RN1 traded',
         (SELECT count(DISTINCT m.condition_id) FROM markets m
            JOIN trades t ON t.condition_id = m.condition_id
           WHERE t.whale_id IN (SELECT id FROM rn1)
             AND m.resolved AND m.resolved_at IS NOT NULL
             AND m.resolved_at <= timestamptz '2026-09-12 00:00:00+00'),
         'no condition resolved at or before the cutoff'
  UNION ALL
  SELECT 6, 'statement 9: U0 RE-READ at end of run (must equal statement 0)',
         (SELECT count(*) FROM trades
           WHERE whale_id IN (SELECT id FROM rn1)
             AND ts <= timestamptz '2026-09-12 00:00:00+00'
             AND detected_at <= timestamptz '2026-09-12 00:00:00+00'),
         'no RN1 fills at or before the cutoff'
)
SELECT test_name, witnesses,
       CASE WHEN witnesses = 0 THEN 'NOT TESTED' ELSE 'TESTED' END AS verdict,
       CASE WHEN witnesses = 0 THEN why_if_zero ELSE '' END AS reason_if_not_tested
  FROM w ORDER BY ord;

\echo ''
\echo '== RUN 80 ENDS. No drag figure. No latency figure. mirror_live=false. =='
