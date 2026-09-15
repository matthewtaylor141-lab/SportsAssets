-- ============================================================================
-- RE-CERTIFICATION (part 2) AND THE ORDERING-TIE AUDIT
-- (2026-09-11, read-only.) Owner-specified.
--
-- RUN 69 ALREADY SETTLED, and those results are not re-run here:
--   A telescoping  sum(dM) = endpoint_M_corrected, 0 violations of 26,248
--   B single-leg   11,826 single-leg conditions, all with endpoint M = 0 and
--                  sum(dM) = 0 (max per-condition gap 0.000000000)
--   C two-leg      endpoint M computed zero-safe throughout
--   D monotonicity the NEGATIVE dM class returned NO ROWS
--   E reduction    sum(directional_reduction_qty) = sum(dM) = endpoint M,
--                  all three 47,634,973.280; 0 conditions where they differ
--   and the five headline dollar figures REPRODUCED EXACTLY under corrected
--   zero-safe arithmetic: matched cost $42,578,503, matched gross P&L
--   $588,777, ROI 1.383% / 0.804% bridged / 1.556% unbridged.
--
-- This file covers what run 69 did NOT: the ordering-tie audit that gates any
-- reading of the path classes, and a SECOND missing-leg defect found while
-- classifying the audit sites -- one that is worse than the matched_qty one.
--
-- ---------------------------------------------------------------------------
-- THE SECOND DEFECT: run 62 statement 3's residual_roi_pct.
--
-- It is written
--
--     sum(trading_pnl - mq * (1.0 - pair_cost)) FILTER (settled)
--       / NULLIF(sum(acq_cost - mq * pair_cost) FILTER (settled), 0)
--
-- On a SINGLE-LEG condition pair_cost is NULL by its own CASE guard, so
-- mq * (1 - pair_cost) is NULL, so trading_pnl - NULL is NULL, and sum()
-- SKIPS THE ROW ENTIRELY. The same happens in the denominator. So every
-- single-leg settled condition was silently dropped from the
-- NON_MATCHED_REMAINDER ROI -- the -1.228% bridged / +1.234% unbridged figures
-- behind verdict 4.
--
-- THOSE ARE EXACTLY THE PURELY DIRECTIONAL CONDITIONS. A single-leg condition
-- has M = 0, so ALL of its P&L is non-matched remainder. Dropping them from a
-- measure of non-matched remainder removes the cleanest cases of the very
-- thing being measured.
--
-- AND IT BREAKS THE LEDGER A IDENTITY AS PUBLISHED. In the same run-62 row,
-- total_roi_pct = sum(trading_pnl) / sum(acq_cost) does NOT drop those rows,
-- because trading_pnl is non-null for a single-leg condition. So matched ROI,
-- remainder ROI and total ROI in that table were computed over THREE DIFFERENT
-- POPULATIONS, and MATCHED + REMAINDER = TRADING cannot hold across them. The
-- identity was required to hold exactly. Statement 3 measures the breach in
-- dollars rather than asserting it, under OLD and CORRECTED side by side.
--
-- This is NOT a LEAST() defect. It is the same family -- missing-leg NULL
-- semantics -- reached through a different door, which is why the audit was
-- not allowed to stop at LEAST/GREATEST.
--
-- ---------------------------------------------------------------------------
-- THE ORDERING-TIE AUDIT, and why it gates the path classes.
--
-- The walk orders fills by (ts, id). Telescoped M is INVARIANT under any
-- reordering -- it depends only on final leg totals -- but PATH LABELS ARE
-- NOT: which fill crosses through flat, and therefore FLIPPED vs
-- REDUCED_NOT_FLIPPED vs NEVER_REDUCED, can change when fills share a
-- timestamp.
--
-- `id` is a BIGSERIAL primary key, so (ts, id) IS deterministic. It is NOT
-- economically authoritative: id is OUR insertion order, and rows arrive by
-- two paths (chain listener and poller) whose interleaving reflects our
-- ingestion, not the venue's matching sequence. A later id does not prove a
-- later fill. Database row order is never used and is not consulted anywhere.
--
-- ONE DISTINCTION DOES MOST OF THE WORK, and statement 1 measures it:
--   TIES WITHIN ONE LEG are harmless to every aggregate here. All fills in the
--   group move the same leg, so the reduction, flip-excess and dM totals and
--   the resulting path class are identical under any within-group order.
--   TIES SPANNING BOTH LEGS are the dangerous ones: the order decides which
--   leg was shorter when, hence which fill pairs and which flips.
--
-- Statement 2 runs an explicit ordering sensitivity -- the same walk under
-- (ts, id) and under (ts, id DESC) -- and counts conditions whose path class
-- changes. Where the classification is not stable, the affected conditions are
-- to be labelled WITHIN_TIMESTAMP_ORDER_UNRESOLVED rather than reported.
--
-- Read-only: four SELECTs.
-- ============================================================================


\echo '== 1. TIE CENSUS: fills sharing a timestamp, split by whether they span both legs =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), grp AS MATERIALIZED (
  SELECT condition_id, ts,
         count(*) AS n_in_group,
         count(DISTINCT outcome_index) AS legs_in_group,
         sum(sh * px) AS group_notional
    FROM buys GROUP BY 1, 2
)
SELECT CASE
         WHEN n_in_group = 1 THEN '1 NO TIE (single fill at this timestamp)'
         WHEN legs_in_group = 1
           THEN '2 TIED WITHIN ONE LEG (order cannot change any aggregate here)'
         ELSE '3 TIED ACROSS BOTH LEGS (order decides which leg was shorter)'
       END AS tie_class,
       count(*) AS fill_groups,
       sum(n_in_group) AS fills,
       round((100.0 * sum(n_in_group) / sum(sum(n_in_group)) OVER ())::numeric, 3)
         AS pct_fills,
       count(DISTINCT condition_id) AS conditions_touched,
       round(sum(group_notional)::numeric, 0) AS notional_usd,
       round((100.0 * sum(group_notional) / sum(sum(group_notional)) OVER ())::numeric, 3)
         AS pct_notional,
       max(n_in_group) AS largest_group
  FROM grp GROUP BY 1 ORDER BY 1;


\echo '== 2. ORDERING SENSITIVITY: path class under (ts,id) vs (ts,id DESC) =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), w AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS ay,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS an,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS ra,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id DESC
                 ROWS UNBOUNDED PRECEDING) AS by_,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id DESC
                 ROWS UNBOUNDED PRECEDING) AS bn,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id DESC) AS rb,
         count(*) OVER (PARTITION BY b.condition_id) AS nf
    FROM buys b
), s AS MATERIALIZED (
  SELECT w.*,
         w.ay - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS ay0,
         w.an - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS an0,
         w.by_ - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS by0,
         w.bn - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS bn0
    FROM w
), cls AS MATERIALIZED (
  SELECT condition_id,
         sum(sh * px) AS acq_cost,
         CASE WHEN count(*) FILTER (WHERE sign(ay0 - an0) <> 0 AND sign(ay - an) <> 0
                                      AND sign(ay0 - an0) <> sign(ay - an)) > 0
                THEN '3 FLIPPED'
              WHEN max(abs(ay - an)) - max(abs(ay - an)) FILTER (WHERE ra = nf) > 1e-9
                THEN '2 REDUCED_NOT_FLIPPED'
              ELSE '1 NEVER_REDUCED' END AS class_fwd,
         CASE WHEN count(*) FILTER (WHERE sign(by0 - bn0) <> 0 AND sign(by_ - bn) <> 0
                                      AND sign(by0 - bn0) <> sign(by_ - bn)) > 0
                THEN '3 FLIPPED'
              WHEN max(abs(by_ - bn)) - max(abs(by_ - bn)) FILTER (WHERE rb = nf) > 1e-9
                THEN '2 REDUCED_NOT_FLIPPED'
              ELSE '1 NEVER_REDUCED' END AS class_rev,
         sum(LEAST(ay, an) - LEAST(ay0, an0)) AS dm_fwd,
         sum(LEAST(by_, bn) - LEAST(by0, bn0)) AS dm_rev
    FROM s GROUP BY 1
)
SELECT class_fwd AS path_class_forward_order,
       count(*) AS conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       count(*) FILTER (WHERE class_fwd <> class_rev) AS class_changed_under_reverse_tiebreak,
       round((100.0 * count(*) FILTER (WHERE class_fwd <> class_rev)
              / count(*))::numeric, 3) AS pct_changed,
       round(sum(acq_cost) FILTER (WHERE class_fwd <> class_rev)::numeric, 0)
         AS acq_cost_of_changed,
       round(sum(dm_fwd - dm_rev)::numeric, 6) AS dm_difference_must_be_zero,
       round(max(abs(dm_fwd - dm_rev))::numeric, 9) AS max_abs_dm_difference
  FROM cls GROUP BY 1 ORDER BY 1;


\echo '== 3. RUN 62 residual_roi_pct: OLD null-dropping vs CORRECTED zero-safe =='
WITH base AS MATERIALIZED (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), leg AS MATERIALIZED (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY') AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY') AS cbuy
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1, 2
), cond AS MATERIALIZED (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN l.qbuy END), 0) AS qy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN l.qbuy END), 0) AS qn,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN l.cbuy END), 0) AS cy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN l.cbuy END), 0) AS cn
    FROM leg l GROUP BY 1
), br1 AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM (
    SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
  ) u WHERE condition_id IS NOT NULL
), s AS MATERIALIZED (
  SELECT c.*, (br1.condition_id IS NOT NULL) AS bridged,
         LEAST(c.qy, c.qn) AS mq,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL
              THEN (mk.resolved_prices->>0)::float8 * c.qy
                 + (mk.resolved_prices->>1)::float8 * c.qn - c.acq_cost END AS trading_pnl
    FROM cond c
    LEFT JOIN br1 ON br1.condition_id = c.condition_id
    LEFT JOIN markets mk ON mk.condition_id = c.condition_id
), f AS MATERIALIZED (
  SELECT s.* FROM s WHERE s.trading_pnl IS NOT NULL
)
SELECT CASE WHEN bridged THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS settled_conditions,
       count(*) FILTER (WHERE pair_cost IS NULL) AS single_leg_dropped_by_old,
       round(sum(acq_cost) FILTER (WHERE pair_cost IS NULL)::numeric, 0)
         AS acq_cost_dropped_by_old,
       round(sum(trading_pnl) FILTER (WHERE pair_cost IS NULL)::numeric, 0)
         AS trading_pnl_dropped_by_old,
       round((100.0 * sum(trading_pnl - mq * (1.0 - pair_cost))
              / NULLIF(sum(acq_cost - mq * pair_cost), 0))::numeric, 3)
         AS residual_roi_OLD,
       round((100.0 * sum(trading_pnl - COALESCE(mq * (1.0 - pair_cost), 0))
              / NULLIF(sum(acq_cost - COALESCE(mq * pair_cost, 0)), 0))::numeric, 3)
         AS residual_roi_CORRECTED,
       round(sum(COALESCE(mq * (1.0 - pair_cost), 0))::numeric, 0) AS matched_gross_pnl,
       round(sum(trading_pnl - COALESCE(mq * (1.0 - pair_cost), 0))::numeric, 0)
         AS remainder_pnl_corrected,
       round(sum(trading_pnl)::numeric, 0) AS trading_pnl,
       round(sum(COALESCE(mq * (1.0 - pair_cost), 0)
                 + (trading_pnl - COALESCE(mq * (1.0 - pair_cost), 0))
                 - trading_pnl)::numeric, 6) AS ledger_a_identity_gap
  FROM f GROUP BY 1 ORDER BY 1;


\echo '== 4. REMAINING mq-DOWNSTREAM METRICS: OLD vs CORRECTED =='
WITH base AS MATERIALIZED (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), leg AS MATERIALIZED (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY') AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY') AS cbuy
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1, 2
), cond AS MATERIALIZED (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN l.qbuy END), 0) AS qy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN l.qbuy END), 0) AS qn,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN l.cbuy END), 0) AS cy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN l.cbuy END), 0) AS cn,
         -- lint: allow-missing-leg -- this REPRODUCES the published defect on
         -- purpose, so the correction can be measured against it. Do not fix.
         LEAST(max(CASE WHEN l.outcome_index = 0 THEN l.qbuy END),
               max(CASE WHEN l.outcome_index = 1 THEN l.qbuy END)) AS mq_old
    FROM leg l GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2 AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0) AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), e AS MATERIALIZED (
  SELECT c.*, LEAST(c.qy, c.qn) AS mq_new,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost
    FROM cond c JOIN tok t ON t.condition_id = c.condition_id
)
SELECT 'STRUCTURALLY_ELIGIBLE population' AS scope,
       count(*) AS conditions,
       round(sum(mq_old)::numeric, 0) AS matched_qty_OLD,
       round(sum(mq_new)::numeric, 0) AS matched_qty_CORRECTED,
       round((100.0 * sum(mq_old) / NULLIF(sum(qy + qn), 0))::numeric, 3)
         AS matched_share_of_shares_OLD,
       round((100.0 * sum(mq_new) / NULLIF(sum(qy + qn), 0))::numeric, 3)
         AS matched_share_of_shares_CORRECTED,
       round((sum(mq_old * COALESCE(pair_cost, 0)) / NULLIF(sum(mq_old), 0))::numeric, 6)
         AS qty_weighted_pair_cost_OLD,
       round((sum(mq_new * COALESCE(pair_cost, 0)) / NULLIF(sum(mq_new), 0))::numeric, 6)
         AS qty_weighted_pair_cost_CORRECTED,
       round(sum(COALESCE(mq_old * pair_cost, 0))::numeric, 0) AS matched_cost_OLD,
       round(sum(COALESCE(mq_new * pair_cost, 0))::numeric, 0) AS matched_cost_CORRECTED,
       round(sum(COALESCE(mq_old * (1.0 - pair_cost), 0))::numeric, 0) AS matched_gross_OLD,
       round(sum(COALESCE(mq_new * (1.0 - pair_cost), 0))::numeric, 0) AS matched_gross_CORRECTED,
       count(*) FILTER (WHERE (mq_old > 0) <> (mq_new > 0))
         AS conditions_whose_has_matched_flag_changes
  FROM e;
