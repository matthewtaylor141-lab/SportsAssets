-- ============================================================================
-- SOURCE-AWARE EXECUTION RECONCILIATION (2026-09-10, read-only).
--
-- WHAT WE NOW KNOW, AND WHAT IT DOES NOT SETTLE.
--
-- The (tx_hash, asset, side) collapse is refuted: of 51,642 multi-row groups
-- NONE carries identical price AND size, the largest collapsing bucket is
-- `backfill` ALONE (27,839 groups / 65,108 rows) which no cross-path story
-- explains, and a collapse removes $25.7M of notional -- preferentially from
-- his large sweeps, since cost falls 21.9% while fill count falls 9.1%.
--
-- But "the coarse key is wrong" does NOT make (tx, asset, side, price, size)
-- right. A real transaction may legitimately contain two executions with the
-- same asset, side, price and size, so equality on those fields is EVIDENCE
-- of duplication and never proof of it. Deduplicating on that tuple would
-- repeat D1's mistake one decimal place further down.
--
-- THE ACTUAL PROBLEM is to reconcile several OBSERVATION SOURCES into the
-- underlying multiset of genuine executions per transaction. So this file
-- builds each source's execution multiset independently and compares them.
--
-- CLASSES, per (tx_hash, asset, side):
--   A  exact source-level duplicate -- the multisets match, one execution
--      set seen twice by two paths
--   B  strict subset -- one source decoded only part of what another saw
--      (task 92's shape: chain decoded 2,880 of a 12,960 order while the
--      poller carried 4,869 + 5,211)
--   C  aggregation vs split -- shares and notional conserve but one source
--      states one row where the other states several
--   D  complementary partials -- each source holds genuine fills the other
--      lacks, so neither alone is the transaction
--   E  irreconcilable -- the underlying multiset cannot be determined
--   S  single source -- no cross-source question; multi-row here is a sweep
--      unless proven otherwise
--
-- CONSERVATION TESTS carried on every class: total shares, total notional,
-- VWAP, fill count and the price/size multiset itself.
--
-- A LIMITATION STATED RATHER THAN HIDDEN: Postgres array containment (<@) is
-- SET containment, not MULTISET containment, so a source holding the same
-- price@size twice against another holding it once reads as "contained".
-- Class B is therefore an upper bound and the sample in statement 4 exists so
-- the boundary cases can be read by eye rather than trusted.
--
-- NOTHING HERE DEFINES A CANONICAL KEY. The reconstruction rule follows from
-- the class distribution these statements return; committing to one first
-- would be the same error in a new place.
--
-- Read-only: six SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. WHAT IS IN dedupe_key? does it carry a native fill identifier? =='
-- dedupe_key is UNIQUE and 1:1 with rows (946,198 of each), so it is NOT
-- collapsing anything today. If it encodes a log index or fill ordinal, that
-- is the native execution identity and no reconstruction is needed at all.
SELECT source,
       count(*) AS rows,
       round(avg(length(dedupe_key))::numeric, 1) AS avg_len,
       count(DISTINCT length(dedupe_key)) AS distinct_lengths,
       count(*) FILTER (WHERE dedupe_key LIKE '%' || tx_hash || '%') AS contains_tx_hash,
       count(*) FILTER (WHERE dedupe_key LIKE '%:%') AS has_colon,
       count(*) FILTER (WHERE dedupe_key LIKE '%|%') AS has_pipe,
       count(*) FILTER (WHERE dedupe_key LIKE '%-%') AS has_dash,
       left(min(dedupe_key), 96) AS sample_min,
       left(max(dedupe_key), 96) AS sample_max
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
 GROUP BY 1 ORDER BY 2 DESC;


\echo '== 2. PER-SOURCE MULTISETS, CLASSIFIED A-E, with conservation =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh,
         t.price::float8 AS px, t.notional::float8 AS usd, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), s AS (
  SELECT tx_hash, asset, side, source,
         count(*) AS n, sum(sh) AS shares, sum(usd) AS usd,
         -- the multiset as ONE delimited string: array_agg of a text[] flattens
         -- in Postgres, so the per-source multiset is carried as text and the
         -- array is rebuilt with string_to_array where containment is needed
         string_agg(round(px::numeric, 6)::text || '@' || round(sh::numeric, 4)::text,
                    '|' ORDER BY px, sh) AS ms
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         count(*) AS n_sources, sum(n) AS rows_total,
         min(n) AS n_min, max(n) AS n_max,
         min(shares) AS sh_min, max(shares) AS sh_max,
         min(usd) AS usd_min, max(usd) AS usd_max,
         (array_agg(ms ORDER BY source))[1] AS ms1,
         (array_agg(ms ORDER BY source))[2] AS ms2,
         string_agg(DISTINCT source, '+' ORDER BY source) AS srcs
    FROM s GROUP BY 1, 2, 3
), c AS (
  SELECT g.*,
         CASE
           WHEN n_sources = 1 AND rows_total = 1 THEN 'S1 single source, one row'
           WHEN n_sources = 1                    THEN 'S2 single source, MULTI-ROW sweep'
           WHEN ms1 = ms2                        THEN 'A exact source duplicate'
           WHEN string_to_array(ms1, '|') <@ string_to_array(ms2, '|')
             OR string_to_array(ms2, '|') <@ string_to_array(ms1, '|')
                                                 THEN 'B one source a subset'
           WHEN abs(sh_max - sh_min) < 0.01
                AND abs(usd_max - usd_min) < 0.01
                AND n_min <> n_max               THEN 'C aggregated vs split'
           WHEN abs(sh_max - sh_min) >= 0.01     THEN 'D complementary partials'
           ELSE                                       'E irreconcilable'
         END AS class
    FROM g
)
SELECT class, count(*) AS groups, sum(rows_total) AS observation_rows,
       round(sum(usd_max)::numeric, 0) AS notional_max_source,
       round(sum(usd_min)::numeric, 0) AS notional_min_source,
       round(sum(usd_max - usd_min)::numeric, 0) AS notional_disagreement,
       round(avg(sh_max - sh_min)::numeric, 2) AS avg_share_disagreement,
       round(avg(n_max)::numeric, 2) AS avg_fills_richest_source,
       round(avg(n_min)::numeric, 2) AS avg_fills_poorest_source,
       count(*) FILTER (WHERE abs(sh_max - sh_min) < 0.01) AS shares_conserve,
       count(*) FILTER (WHERE abs(usd_max - usd_min) < 0.01) AS notional_conserves
  FROM c GROUP BY 1 ORDER BY 1;


\echo '== 3. THE SAME CLASSES BY SOURCE COMBINATION =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh,
         t.price::float8 AS px, t.notional::float8 AS usd, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), s AS (
  SELECT tx_hash, asset, side, source, count(*) AS n, sum(sh) AS shares, sum(usd) AS usd,
         string_agg(round(px::numeric, 6)::text || '@' || round(sh::numeric, 4)::text,
                    '|' ORDER BY px, sh) AS ms
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n_sources, sum(n) AS rows_total,
         min(n) AS n_min, max(n) AS n_max,
         min(shares) AS sh_min, max(shares) AS sh_max,
         min(usd) AS usd_min, max(usd) AS usd_max,
         (array_agg(ms ORDER BY source))[1] AS ms1,
         (array_agg(ms ORDER BY source))[2] AS ms2,
         string_agg(DISTINCT source, '+' ORDER BY source) AS srcs,
         min(ts)::date AS d
    FROM s GROUP BY 1, 2, 3
)
SELECT srcs AS sources, n_sources,
       CASE WHEN n_sources = 1 AND rows_total = 1 THEN 'S1 one row'
            WHEN n_sources = 1                    THEN 'S2 multi-row sweep'
            WHEN ms1 = ms2                        THEN 'A exact duplicate'
            WHEN string_to_array(ms1, '|') <@ string_to_array(ms2, '|')
              OR string_to_array(ms2, '|') <@ string_to_array(ms1, '|')
                                                  THEN 'B subset'
            WHEN abs(sh_max - sh_min) < 0.01 AND n_min <> n_max THEN 'C aggregated vs split'
            WHEN abs(sh_max - sh_min) >= 0.01     THEN 'D complementary'
            ELSE 'E irreconcilable' END AS class,
       count(*) AS groups, sum(rows_total) AS rows,
       round(sum(usd_max)::numeric, 0) AS notional_richest,
       round(sum(usd_max - usd_min)::numeric, 0) AS disagreement,
       min(d) AS first_day, max(d) AS last_day
  FROM g GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;


\echo '== 4. STRATIFIED SAMPLE: raw observation rows, worst cases first =='
-- Actual rows so the reconstruction can be read by eye rather than trusted.
WITH t AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh,
         t.price::float8 AS px, t.notional::float8 AS usd, t.ts, t.detected_at,
         t.condition_id, t.outcome_index, t.market_slug
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), g AS (
  SELECT tx_hash, asset, side, count(*) AS rows_total,
         count(DISTINCT source) AS n_sources,
         string_agg(DISTINCT source, '+' ORDER BY source) AS srcs,
         sum(usd) AS usd
    FROM t GROUP BY 1, 2, 3 HAVING count(*) > 1
), pick AS (
  SELECT DISTINCT ON (stratum) stratum, tx_hash, asset, side, srcs, rows_total, usd
    FROM (
      SELECT g.*, 'a backfill-only sweep'  AS stratum FROM g WHERE srcs = 'backfill'
      UNION ALL SELECT g.*, 'b poll-only'   FROM g WHERE srcs = 'poll'
      UNION ALL SELECT g.*, 'c chain-only'  FROM g WHERE srcs = 'chain'
      UNION ALL SELECT g.*, 'd chain+poll'  FROM g WHERE srcs = 'chain+poll'
      UNION ALL SELECT g.*, 'e poll+s1'     FROM g WHERE srcs = 'poll+s1'
      UNION ALL SELECT g.*, 'f largest notional'    FROM g
      UNION ALL SELECT g.*, 'g largest multiplicity' FROM g
    ) u
   ORDER BY stratum,
            CASE WHEN stratum = 'f largest notional'    THEN usd
                 WHEN stratum = 'g largest multiplicity' THEN rows_total::float8
                 ELSE usd END DESC
)
SELECT p.stratum, left(p.tx_hash, 14) || '..' AS tx, left(p.asset, 12) || '..' AS asset,
       p.side, p.srcs, p.rows_total AS rows_in_group,
       t.source, t.px AS price, t.sh AS size, round(t.usd::numeric, 2) AS notional,
       to_char(t.ts, 'MM-DD HH24:MI:SS') AS exchange_ts,
       to_char(t.detected_at, 'MM-DD HH24:MI:SS') AS detected_at,
       t.outcome_index AS idx
  FROM pick p JOIN t ON t.tx_hash = p.tx_hash AND t.asset = p.asset AND t.side = p.side
 ORDER BY p.stratum, t.source, t.px, t.sh LIMIT 90;


\echo '== 5. CONSERVATION: do sources agree on shares and notional per tx? =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh,
         t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), s AS (
  SELECT tx_hash, asset, side, source, count(*) AS n,
         sum(sh) AS shares, sum(usd) AS usd,
         sum(usd) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n_sources,
         max(shares) - min(shares) AS sh_gap,
         max(usd) - min(usd) AS usd_gap,
         max(vwap) - min(vwap) AS vwap_gap,
         max(n) - min(n) AS n_gap, max(usd) AS usd_rich
    FROM s GROUP BY 1, 2, 3 HAVING count(*) > 1
)
SELECT CASE WHEN abs(sh_gap) < 0.01 AND abs(usd_gap) < 0.01 THEN '1 shares AND notional conserve'
            WHEN abs(sh_gap) < 0.01                          THEN '2 shares conserve, notional differs'
            WHEN abs(usd_gap) < 0.01                         THEN '3 notional conserves, shares differ'
            ELSE                                                  '4 neither conserves' END AS conservation,
       count(*) AS groups,
       round(sum(usd_rich)::numeric, 0) AS notional_richest_source,
       round(avg(sh_gap)::numeric, 2) AS avg_share_gap,
       round(avg(usd_gap)::numeric, 2) AS avg_notional_gap,
       round(avg(vwap_gap)::numeric, 6) AS avg_vwap_gap,
       round(avg(n_gap)::numeric, 2) AS avg_fillcount_gap
  FROM g GROUP BY 1 ORDER BY 1;


\echo '== 6. D1 IMPACT ON THE LIVE MIRROR: his_net as stored vs from raw rows =='
-- The second structural bug, isolated. mirror_books.his_long / his_other were
-- written by the D1-collapsed reading. Recomputing them from raw observation
-- rows shows how far the mirror's view of his inventory was displaced, and
-- therefore how far our TARGET was.
WITH b AS (
  SELECT id, condition_id, us_market_slug, whale, ratio, target, his_long, his_other,
         his_net, state, ledger_net, venue_net
    FROM mirror_books
   WHERE whale = 'rn1' AND condition_id IS NOT NULL
), raw AS (
  SELECT t.condition_id,
         COALESCE(sum(t.size::float8) FILTER (WHERE t.outcome_index = 0 AND t.side = 'BUY'), 0)
           - COALESCE(sum(t.size::float8) FILTER (WHERE t.outcome_index = 0 AND t.side = 'SELL'), 0) AS raw_long,
         COALESCE(sum(t.size::float8) FILTER (WHERE t.outcome_index = 1 AND t.side = 'BUY'), 0)
           - COALESCE(sum(t.size::float8) FILTER (WHERE t.outcome_index = 1 AND t.side = 'SELL'), 0) AS raw_other,
         count(*) AS raw_fills
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.outcome_index IN (0, 1)
   GROUP BY 1
)
SELECT b.state,
       count(*) AS books,
       count(*) FILTER (WHERE raw.condition_id IS NOT NULL) AS books_with_raw,
       round(avg(b.his_net)::numeric, 0) AS stored_his_net_avg,
       round(avg(raw.raw_long - raw.raw_other)::numeric, 0) AS raw_his_net_avg,
       round(sum(abs(COALESCE(b.his_net, 0) - (raw.raw_long - raw.raw_other)))::numeric, 0) AS total_abs_net_gap,
       count(*) FILTER (WHERE abs(COALESCE(b.his_net, 0) - (raw.raw_long - raw.raw_other)) > 1) AS books_disagreeing,
       round((100.0 * count(*) FILTER (WHERE abs(COALESCE(b.his_net, 0)
              - (raw.raw_long - raw.raw_other)) > 1) / NULLIF(count(*), 0))::numeric, 1) AS pct_disagreeing,
       round(avg(CASE WHEN raw.raw_long - raw.raw_other <> 0
                      THEN 100.0 * COALESCE(b.his_net, 0) / (raw.raw_long - raw.raw_other) END)::numeric, 1)
         AS stored_as_pct_of_raw,
       round(sum(b.target)::numeric, 0) AS stored_target_sum,
       round(sum(b.ratio * (raw.raw_long - raw.raw_other))::numeric, 0) AS target_if_raw
  FROM b LEFT JOIN raw ON raw.condition_id = b.condition_id
 GROUP BY 1 ORDER BY 2 DESC;
