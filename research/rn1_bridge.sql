-- ============================================================================
-- THE REQUIRED-ACTION EXECUTION BRIDGE, on TARGET_TRANSITION_IDENTIFIED only
-- (2026-09-11, read-only.)
--
-- THE NOTIONAL FORMULA, verified once and stated once. Per event:
--     d_m(t) = max( min(cy_t, cn_t) - min(cy_{t-1}, cn_{t-1}), 0 )
--     dm_leg_notional = SUM over events of  d_m(t) * px(t)
-- It TELESCOPES: sum of d_m over a condition = min(Y, N) = M, so every matched
-- share is counted ONCE, attributed to the single fill that created it, at that
-- fill's own price. It cannot duplicate condition-level economics across dM
-- rows.
--   BUT THE NAME OVERSTATED IT. d_m * px is ONE LEG at ONE LEG'S PRICE --
--   roughly HALF a completed pair's cost. It is NOT Check A's condition-level
--   matched_cost = LEAST(qy,qn) * (vy+vn). The arithmetic confirms it:
--   check A $46,681,156 / 47,324,233 pairs = $0.986 per PAIR, while check B
--   $23,582,618 / 47,583,824 dM shares = $0.4956 per SHARE, and 0.4956*2 =
--   0.991. Renamed dm_leg_notional throughout.
--
-- THE PROBE LADDER IS RN1'S VENUE, NOT BETTOR'S. copy_probes.depth is written
-- as json.dumps(asks[:8]) from GET /book against clob_api_base =
-- "https://clob.polymarket.com" (copy_probe.py:115-120, :158; config.py:37) --
-- POLYMARKET, HIS venue, not Polymarket US where BETTOR executes. Walking it
-- therefore measures THE COST OF REPLICATING HIS FILL ON HIS OWN BOOK AT OUR
-- DETECTION LATENCY. That is the SELECTION + LATENCY component with the VENUE
-- component excluded, and it is named RN1_VENUE_REPLICATION_DRAG for exactly
-- that reason. It is NOT BETTOR execution drag and must never be relabelled as
-- such.
--
-- AND THERE IS NO SUBSTITUTE. Grepping every migration for a stored ladder
-- returns ONE hit -- that column. mirror_shadow keeps bid/ask/mark from
-- _paced_bbo (:2540-2555): US TOP-OF-BOOK ONLY, no depth, and per shadow tick
-- rather than per fill. So no per-fill Polymarket US ask ladder exists in
-- retained data and PMUS-venue depth economics are not historically
-- measurable at all.
--
-- DEPTH IS TRUNCATED TO EIGHT LEVELS, so "short of the required quantity"
-- splits in two and the difference is real:
--   n_levels < 8  the book genuinely held fewer levels -> PARTIALLY_FILLABLE
--   n_levels = 8  the STORED ladder ran out -> DEPTH_TRUNCATED_AT_8, which is
--                 NOT proof of unfillability and is never counted as one.
--
-- THE PROBE IS THE EVENT'S OWN, JOINED p.trade_id = this fill's trades.id.
-- No envelope borrowing, no nearest-fill substitution, no later snapshot. An
-- event is not measurable merely because some probe exists in its envelope.
--
-- QUANTITY comes from the state-free exact integer range and nothing else:
--     required_qty_max (conservative, primary) and required_qty_min
-- No fixed $1k/$5k proxy is used: the ladder is walked to the event's own
-- required quantity, level by level.
--
-- FEE is the internally recorded schedule for polymarket-us (proof2.py:97,
-- :107, :111): fee = 0.06 * shares * price * (1 - price). It is a
-- SCHEDULE_ESTIMATE, not a venue-stated figure -- the $1 pair probe that would
-- validate the venue's actual fee is still open -- and it is labelled so.
--
-- SELL economics are PARTIALLY IDENTIFIED ONLY: a contemporaneous neutral bid
-- where one was retained, the reference price, and above/at/below. No
-- fillability, no VWAP, no queue position, no fill probability, no realised
-- SELL P&L is inferred.
--
-- NO COMBINED BUY+SELL PROFITABILITY NUMBER IS PRODUCED.
--
-- Read-only: five SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. BUY COVERAGE: how much of the BUY cohort has its OWN usable ladder =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px, b.source
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), pair AS (
  -- RN1's condition-level matched PAIR edge, on the same canonical rows Check A
  -- used: 1 - (vwap_yes + vwap_no). Compared against execution drag below.
  SELECT condition_id,
         1.0 - ( sum(sh * px) FILTER (WHERE outcome_index = 0)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0)
               + sum(sh * px) FILTER (WHERE outcome_index = 1)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) ) AS pair_edge
    FROM canon GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px, NULL::text AS source
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px, source FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), tti AS (
  -- TARGET_TRANSITION_IDENTIFIED only: the primary causal cohort.
  SELECT y.*,
         abs(y.as_of_ratio * y.signed_dn) AS d_cont,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9) AS d_int
    FROM l1 y
   WHERE y.ts >= timestamptz '2026-08-05 00:00Z'
     AND y.as_of_long IS NOT NULL AND y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0
     AND y.signed_dn IS NOT NULL AND y.sh > 0
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_long  IS DISTINCT FROM y.as_of_long)
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
), q AS (
  SELECT z.condition_id, z.ts, z.ev, z.sh, z.px, z.source, z.d_m, z.signed_dn, z.d_cont,
         GREATEST(0, z.m - 1) AS required_qty_min,
         CASE WHEN z.d_int THEN z.m ELSE z.m + 1 END AS required_qty_max,
         CASE WHEN z.signed_dn > 0 THEN 'BUY ' ELSE 'SELL' END AS local_side,
         (z.d_cont >= 2.0) AS side_forced
    FROM tti z WHERE z.signed_dn <> 0
), ev AS (
  -- THE EXACT OWN-FILL PROBE AND NOTHING ELSE. p.trade_id = this fill's id.
  -- No envelope borrowing, no nearest-fill substitution, no later snapshot.
  SELECT q.*, pr.pair_edge, mk.sport,
         p.probe_at, p.reaction_s, p.book_ok, p.best_ask, p.depth,
         (p.trade_id IS NOT NULL AND p.book_ok AND p.best_ask IS NOT NULL
          AND p.depth IS NOT NULL AND jsonb_typeof(p.depth) = 'array'
          AND jsonb_array_length(p.depth) > 0) AS probe_available,
         COALESCE(jsonb_array_length(p.depth), 0) AS n_levels
    FROM q
    LEFT JOIN copy_probes p ON p.trade_id = q.ev
    LEFT JOIN pair pr ON pr.condition_id = q.condition_id
    LEFT JOIN markets mk ON mk.condition_id = q.condition_id
), lad AS (
  SELECT e.ev, lv.ord,
         (lv.lvl->>0)::float8 AS lvl_px,
         (lv.lvl->>1)::float8 AS lvl_sz
    FROM ev e
    CROSS JOIN LATERAL jsonb_array_elements(e.depth) WITH ORDINALITY AS lv(lvl, ord)
   WHERE e.probe_available
), cum AS (
  SELECT l.*, sum(l.lvl_sz) OVER w AS cum_sz
    FROM lad l WINDOW w AS (PARTITION BY l.ev ORDER BY l.ord ROWS UNBOUNDED PRECEDING)
), walk AS (
  -- Walk the ACTUAL stored ladder to the event's own required quantity.
  -- take(Q) at a level = LEAST(level_size, GREATEST(Q - shares_above, 0)).
  -- No fixed $1k/$5k proxy is used anywhere.
  SELECT c.ev,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_max,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_max,
         sum(c.lvl_sz) AS depth_total_sh
    FROM cum c JOIN ev e ON e.ev = c.ev
   GROUP BY c.ev
), b AS (
  SELECT e.*, w.sh_at_min, w.cost_at_min, w.sh_at_max, w.cost_at_max, w.depth_total_sh,
         CASE WHEN w.sh_at_min > 0 THEN w.cost_at_min / w.sh_at_min END AS vwap_at_min,
         CASE WHEN w.sh_at_max > 0 THEN w.cost_at_max / w.sh_at_max END AS vwap_at_max,
         CASE WHEN e.required_qty_max > 0
              THEN LEAST(1.0, w.sh_at_max / e.required_qty_max) END AS fill_fraction_at_max,
         GREATEST(e.required_qty_max - COALESCE(w.sh_at_max, 0), 0) AS unfilled_qty,
         CASE
           WHEN NOT e.probe_available THEN 'D NO_USABLE_PROBE (no own-fill probe, or book unreadable)'
           WHEN e.required_qty_max = 0 THEN 'C ZERO REQUIRED QTY (truncation absorbs it)'
           WHEN w.sh_at_max >= e.required_qty_max - 1e-9
             THEN 'A FULLY_MEASURABLE_BUY (ladder covers the conservative qty)'
           WHEN e.n_levels >= 8
             THEN 'B2 DEPTH_TRUNCATED_AT_8 (stored ladder ran out; NOT proven unfillable)'
           ELSE 'B1 PARTIALLY_FILLABLE_BUY (book genuinely thinner than required)'
         END AS measurability
    FROM ev e LEFT JOIN walk w ON w.ev = e.ev
)
SELECT measurability,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS pct_dM,
       round(sum(d_m * px)::numeric, 0) AS dm_leg_notional,
       round((100.0 * sum(d_m * px) / NULLIF(sum(sum(d_m * px)) OVER (), 0))::numeric, 2)
         AS pct_leg_notional,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY reaction_s)::numeric, 2)
         AS p50_reaction_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY reaction_s)::numeric, 2)
         AS p90_reaction_s
  FROM b WHERE local_side = 'BUY '
 GROUP BY 1 ORDER BY 1;


\echo '== 2. BUY EXECUTION on the fully measurable subset: drag, fill, fee vs pair edge =='
-- RN1_VENUE_REPLICATION_DRAG = vwap on HIS book at the required quantity minus
-- the price HE got. Not BETTOR venue drag -- see the header.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px, b.source
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), pair AS (
  -- RN1's condition-level matched PAIR edge, on the same canonical rows Check A
  -- used: 1 - (vwap_yes + vwap_no). Compared against execution drag below.
  SELECT condition_id,
         1.0 - ( sum(sh * px) FILTER (WHERE outcome_index = 0)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0)
               + sum(sh * px) FILTER (WHERE outcome_index = 1)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) ) AS pair_edge
    FROM canon GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px, NULL::text AS source
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px, source FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), tti AS (
  -- TARGET_TRANSITION_IDENTIFIED only: the primary causal cohort.
  SELECT y.*,
         abs(y.as_of_ratio * y.signed_dn) AS d_cont,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9) AS d_int
    FROM l1 y
   WHERE y.ts >= timestamptz '2026-08-05 00:00Z'
     AND y.as_of_long IS NOT NULL AND y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0
     AND y.signed_dn IS NOT NULL AND y.sh > 0
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_long  IS DISTINCT FROM y.as_of_long)
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
), q AS (
  SELECT z.condition_id, z.ts, z.ev, z.sh, z.px, z.source, z.d_m, z.signed_dn, z.d_cont,
         GREATEST(0, z.m - 1) AS required_qty_min,
         CASE WHEN z.d_int THEN z.m ELSE z.m + 1 END AS required_qty_max,
         CASE WHEN z.signed_dn > 0 THEN 'BUY ' ELSE 'SELL' END AS local_side,
         (z.d_cont >= 2.0) AS side_forced
    FROM tti z WHERE z.signed_dn <> 0
), ev AS (
  -- THE EXACT OWN-FILL PROBE AND NOTHING ELSE. p.trade_id = this fill's id.
  -- No envelope borrowing, no nearest-fill substitution, no later snapshot.
  SELECT q.*, pr.pair_edge, mk.sport,
         p.probe_at, p.reaction_s, p.book_ok, p.best_ask, p.depth,
         (p.trade_id IS NOT NULL AND p.book_ok AND p.best_ask IS NOT NULL
          AND p.depth IS NOT NULL AND jsonb_typeof(p.depth) = 'array'
          AND jsonb_array_length(p.depth) > 0) AS probe_available,
         COALESCE(jsonb_array_length(p.depth), 0) AS n_levels
    FROM q
    LEFT JOIN copy_probes p ON p.trade_id = q.ev
    LEFT JOIN pair pr ON pr.condition_id = q.condition_id
    LEFT JOIN markets mk ON mk.condition_id = q.condition_id
), lad AS (
  SELECT e.ev, lv.ord,
         (lv.lvl->>0)::float8 AS lvl_px,
         (lv.lvl->>1)::float8 AS lvl_sz
    FROM ev e
    CROSS JOIN LATERAL jsonb_array_elements(e.depth) WITH ORDINALITY AS lv(lvl, ord)
   WHERE e.probe_available
), cum AS (
  SELECT l.*, sum(l.lvl_sz) OVER w AS cum_sz
    FROM lad l WINDOW w AS (PARTITION BY l.ev ORDER BY l.ord ROWS UNBOUNDED PRECEDING)
), walk AS (
  -- Walk the ACTUAL stored ladder to the event's own required quantity.
  -- take(Q) at a level = LEAST(level_size, GREATEST(Q - shares_above, 0)).
  -- No fixed $1k/$5k proxy is used anywhere.
  SELECT c.ev,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_max,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_max,
         sum(c.lvl_sz) AS depth_total_sh
    FROM cum c JOIN ev e ON e.ev = c.ev
   GROUP BY c.ev
), b AS (
  SELECT e.*, w.sh_at_min, w.cost_at_min, w.sh_at_max, w.cost_at_max, w.depth_total_sh,
         CASE WHEN w.sh_at_min > 0 THEN w.cost_at_min / w.sh_at_min END AS vwap_at_min,
         CASE WHEN w.sh_at_max > 0 THEN w.cost_at_max / w.sh_at_max END AS vwap_at_max,
         CASE WHEN e.required_qty_max > 0
              THEN LEAST(1.0, w.sh_at_max / e.required_qty_max) END AS fill_fraction_at_max,
         GREATEST(e.required_qty_max - COALESCE(w.sh_at_max, 0), 0) AS unfilled_qty,
         CASE
           WHEN NOT e.probe_available THEN 'D NO_USABLE_PROBE (no own-fill probe, or book unreadable)'
           WHEN e.required_qty_max = 0 THEN 'C ZERO REQUIRED QTY (truncation absorbs it)'
           WHEN w.sh_at_max >= e.required_qty_max - 1e-9
             THEN 'A FULLY_MEASURABLE_BUY (ladder covers the conservative qty)'
           WHEN e.n_levels >= 8
             THEN 'B2 DEPTH_TRUNCATED_AT_8 (stored ladder ran out; NOT proven unfillable)'
           ELSE 'B1 PARTIALLY_FILLABLE_BUY (book genuinely thinner than required)'
         END AS measurability
    FROM ev e LEFT JOIN walk w ON w.ev = e.ev
), m AS (
  SELECT b.*,
         (b.vwap_at_max - b.px) * 100.0 AS drag_cents_max,
         (b.vwap_at_min - b.px) * 100.0 AS drag_cents_min,
         CASE WHEN b.px > 0 THEN (b.vwap_at_max / b.px - 1.0) * 10000.0 END AS drag_bps,
         0.06 * b.sh_at_max * b.vwap_at_max * (1.0 - b.vwap_at_max) AS fee_usd,
         b.sh_at_max * b.vwap_at_max AS deployed_usd
    FROM b
   WHERE b.local_side = 'BUY '
     AND b.measurability LIKE 'A %'
     AND b.vwap_at_max IS NOT NULL AND b.px > 0
)
SELECT CASE WHEN side_forced THEN '1 SIDE FORCED (d >= 2)'
            ELSE '2 HOLD-AMBIGUOUS (d < 2)' END AS cohort,
       count(*) AS events,
       round(sum(deployed_usd)::numeric, 0) AS deployed_usd,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY drag_cents_max)::numeric, 3) AS p50_drag_c,
       round(percentile_cont(0.75) WITHIN GROUP (ORDER BY drag_cents_max)::numeric, 3) AS p75_drag_c,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY drag_cents_max)::numeric, 3) AS p90_drag_c,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY drag_cents_max)::numeric, 3) AS p95_drag_c,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY drag_bps)::numeric, 1) AS p50_drag_bps,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY drag_bps)::numeric, 1) AS p90_drag_bps,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY fill_fraction_at_max)::numeric, 4)
         AS p50_fill_frac,
       round((100.0 * sum(fee_usd) / NULLIF(sum(deployed_usd), 0))::numeric, 3) AS FEE_PCT_OF_DEPLOYED,
       round((100.0 * sum((m.vwap_at_max - m.px) * m.sh_at_max) / NULLIF(sum(deployed_usd), 0))::numeric, 3)
         AS DRAG_PCT_OF_DEPLOYED,
       round((100.0 * (sum((m.vwap_at_max - m.px) * m.sh_at_max) + sum(fee_usd))
              / NULLIF(sum(deployed_usd), 0))::numeric, 3) AS TOTAL_DRAG_PCT,
       round((100.0 * percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_edge))::numeric, 3)
         AS p50_RN1_PAIR_EDGE_PCT
  FROM m GROUP BY 1 ORDER BY 1;


\echo '== 3. SELECTION TEST: is the measurable BUY subset like the rest of the cohort? =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px, b.source
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), pair AS (
  -- RN1's condition-level matched PAIR edge, on the same canonical rows Check A
  -- used: 1 - (vwap_yes + vwap_no). Compared against execution drag below.
  SELECT condition_id,
         1.0 - ( sum(sh * px) FILTER (WHERE outcome_index = 0)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0)
               + sum(sh * px) FILTER (WHERE outcome_index = 1)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) ) AS pair_edge
    FROM canon GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px, NULL::text AS source
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px, source FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), tti AS (
  -- TARGET_TRANSITION_IDENTIFIED only: the primary causal cohort.
  SELECT y.*,
         abs(y.as_of_ratio * y.signed_dn) AS d_cont,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9) AS d_int
    FROM l1 y
   WHERE y.ts >= timestamptz '2026-08-05 00:00Z'
     AND y.as_of_long IS NOT NULL AND y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0
     AND y.signed_dn IS NOT NULL AND y.sh > 0
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_long  IS DISTINCT FROM y.as_of_long)
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
), q AS (
  SELECT z.condition_id, z.ts, z.ev, z.sh, z.px, z.source, z.d_m, z.signed_dn, z.d_cont,
         GREATEST(0, z.m - 1) AS required_qty_min,
         CASE WHEN z.d_int THEN z.m ELSE z.m + 1 END AS required_qty_max,
         CASE WHEN z.signed_dn > 0 THEN 'BUY ' ELSE 'SELL' END AS local_side,
         (z.d_cont >= 2.0) AS side_forced
    FROM tti z WHERE z.signed_dn <> 0
), ev AS (
  -- THE EXACT OWN-FILL PROBE AND NOTHING ELSE. p.trade_id = this fill's id.
  -- No envelope borrowing, no nearest-fill substitution, no later snapshot.
  SELECT q.*, pr.pair_edge, mk.sport,
         p.probe_at, p.reaction_s, p.book_ok, p.best_ask, p.depth,
         (p.trade_id IS NOT NULL AND p.book_ok AND p.best_ask IS NOT NULL
          AND p.depth IS NOT NULL AND jsonb_typeof(p.depth) = 'array'
          AND jsonb_array_length(p.depth) > 0) AS probe_available,
         COALESCE(jsonb_array_length(p.depth), 0) AS n_levels
    FROM q
    LEFT JOIN copy_probes p ON p.trade_id = q.ev
    LEFT JOIN pair pr ON pr.condition_id = q.condition_id
    LEFT JOIN markets mk ON mk.condition_id = q.condition_id
), lad AS (
  SELECT e.ev, lv.ord,
         (lv.lvl->>0)::float8 AS lvl_px,
         (lv.lvl->>1)::float8 AS lvl_sz
    FROM ev e
    CROSS JOIN LATERAL jsonb_array_elements(e.depth) WITH ORDINALITY AS lv(lvl, ord)
   WHERE e.probe_available
), cum AS (
  SELECT l.*, sum(l.lvl_sz) OVER w AS cum_sz
    FROM lad l WINDOW w AS (PARTITION BY l.ev ORDER BY l.ord ROWS UNBOUNDED PRECEDING)
), walk AS (
  -- Walk the ACTUAL stored ladder to the event's own required quantity.
  -- take(Q) at a level = LEAST(level_size, GREATEST(Q - shares_above, 0)).
  -- No fixed $1k/$5k proxy is used anywhere.
  SELECT c.ev,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_max,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_max,
         sum(c.lvl_sz) AS depth_total_sh
    FROM cum c JOIN ev e ON e.ev = c.ev
   GROUP BY c.ev
), b AS (
  SELECT e.*, w.sh_at_min, w.cost_at_min, w.sh_at_max, w.cost_at_max, w.depth_total_sh,
         CASE WHEN w.sh_at_min > 0 THEN w.cost_at_min / w.sh_at_min END AS vwap_at_min,
         CASE WHEN w.sh_at_max > 0 THEN w.cost_at_max / w.sh_at_max END AS vwap_at_max,
         CASE WHEN e.required_qty_max > 0
              THEN LEAST(1.0, w.sh_at_max / e.required_qty_max) END AS fill_fraction_at_max,
         GREATEST(e.required_qty_max - COALESCE(w.sh_at_max, 0), 0) AS unfilled_qty,
         CASE
           WHEN NOT e.probe_available THEN 'D NO_USABLE_PROBE (no own-fill probe, or book unreadable)'
           WHEN e.required_qty_max = 0 THEN 'C ZERO REQUIRED QTY (truncation absorbs it)'
           WHEN w.sh_at_max >= e.required_qty_max - 1e-9
             THEN 'A FULLY_MEASURABLE_BUY (ladder covers the conservative qty)'
           WHEN e.n_levels >= 8
             THEN 'B2 DEPTH_TRUNCATED_AT_8 (stored ladder ran out; NOT proven unfillable)'
           ELSE 'B1 PARTIALLY_FILLABLE_BUY (book genuinely thinner than required)'
         END AS measurability
    FROM ev e LEFT JOIN walk w ON w.ev = e.ev
)
SELECT CASE WHEN measurability LIKE 'A %' THEN '1 MEASURABLE (own ladder covers required qty)'
            WHEN measurability LIKE 'D %' THEN '3 NO_USABLE_PROBE'
            ELSE '2 PARTIAL or DEPTH-TRUNCATED' END AS subset,
       count(*) AS events,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY px)::numeric, 4) AS p50_rn1_price,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY sh)::numeric, 1) AS p50_rn1_size_sh,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY sh)::numeric, 1) AS p90_rn1_size_sh,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY d_m)::numeric, 1) AS p50_dM_sh,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY required_qty_max)::numeric, 1)
         AS p50_required_qty,
       round((100.0 * percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_edge))::numeric, 3)
         AS p50_pair_edge_pct,
       round((100.0 * count(*) FILTER (WHERE source IN ('poll', 'backfill'))
              / NULLIF(count(*), 0))::numeric, 2) AS pct_detect_venue_feed,
       round((100.0 * count(*) FILTER (WHERE side_forced) / NULLIF(count(*), 0))::numeric, 2)
         AS pct_side_forced,
       count(DISTINCT sport) AS distinct_sports,
       mode() WITHIN GROUP (ORDER BY sport) AS modal_sport
  FROM b WHERE local_side = 'BUY '
 GROUP BY 1 ORDER BY 1;


\echo '== 4. BUY price-band and sport detail, measurable vs not =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px, b.source
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), pair AS (
  -- RN1's condition-level matched PAIR edge, on the same canonical rows Check A
  -- used: 1 - (vwap_yes + vwap_no). Compared against execution drag below.
  SELECT condition_id,
         1.0 - ( sum(sh * px) FILTER (WHERE outcome_index = 0)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0)
               + sum(sh * px) FILTER (WHERE outcome_index = 1)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) ) AS pair_edge
    FROM canon GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px, NULL::text AS source
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px, source FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), tti AS (
  -- TARGET_TRANSITION_IDENTIFIED only: the primary causal cohort.
  SELECT y.*,
         abs(y.as_of_ratio * y.signed_dn) AS d_cont,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9) AS d_int
    FROM l1 y
   WHERE y.ts >= timestamptz '2026-08-05 00:00Z'
     AND y.as_of_long IS NOT NULL AND y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0
     AND y.signed_dn IS NOT NULL AND y.sh > 0
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_long  IS DISTINCT FROM y.as_of_long)
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
), q AS (
  SELECT z.condition_id, z.ts, z.ev, z.sh, z.px, z.source, z.d_m, z.signed_dn, z.d_cont,
         GREATEST(0, z.m - 1) AS required_qty_min,
         CASE WHEN z.d_int THEN z.m ELSE z.m + 1 END AS required_qty_max,
         CASE WHEN z.signed_dn > 0 THEN 'BUY ' ELSE 'SELL' END AS local_side,
         (z.d_cont >= 2.0) AS side_forced
    FROM tti z WHERE z.signed_dn <> 0
), ev AS (
  -- THE EXACT OWN-FILL PROBE AND NOTHING ELSE. p.trade_id = this fill's id.
  -- No envelope borrowing, no nearest-fill substitution, no later snapshot.
  SELECT q.*, pr.pair_edge, mk.sport,
         p.probe_at, p.reaction_s, p.book_ok, p.best_ask, p.depth,
         (p.trade_id IS NOT NULL AND p.book_ok AND p.best_ask IS NOT NULL
          AND p.depth IS NOT NULL AND jsonb_typeof(p.depth) = 'array'
          AND jsonb_array_length(p.depth) > 0) AS probe_available,
         COALESCE(jsonb_array_length(p.depth), 0) AS n_levels
    FROM q
    LEFT JOIN copy_probes p ON p.trade_id = q.ev
    LEFT JOIN pair pr ON pr.condition_id = q.condition_id
    LEFT JOIN markets mk ON mk.condition_id = q.condition_id
), lad AS (
  SELECT e.ev, lv.ord,
         (lv.lvl->>0)::float8 AS lvl_px,
         (lv.lvl->>1)::float8 AS lvl_sz
    FROM ev e
    CROSS JOIN LATERAL jsonb_array_elements(e.depth) WITH ORDINALITY AS lv(lvl, ord)
   WHERE e.probe_available
), cum AS (
  SELECT l.*, sum(l.lvl_sz) OVER w AS cum_sz
    FROM lad l WINDOW w AS (PARTITION BY l.ev ORDER BY l.ord ROWS UNBOUNDED PRECEDING)
), walk AS (
  -- Walk the ACTUAL stored ladder to the event's own required quantity.
  -- take(Q) at a level = LEAST(level_size, GREATEST(Q - shares_above, 0)).
  -- No fixed $1k/$5k proxy is used anywhere.
  SELECT c.ev,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_max,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_max,
         sum(c.lvl_sz) AS depth_total_sh
    FROM cum c JOIN ev e ON e.ev = c.ev
   GROUP BY c.ev
), b AS (
  SELECT e.*, w.sh_at_min, w.cost_at_min, w.sh_at_max, w.cost_at_max, w.depth_total_sh,
         CASE WHEN w.sh_at_min > 0 THEN w.cost_at_min / w.sh_at_min END AS vwap_at_min,
         CASE WHEN w.sh_at_max > 0 THEN w.cost_at_max / w.sh_at_max END AS vwap_at_max,
         CASE WHEN e.required_qty_max > 0
              THEN LEAST(1.0, w.sh_at_max / e.required_qty_max) END AS fill_fraction_at_max,
         GREATEST(e.required_qty_max - COALESCE(w.sh_at_max, 0), 0) AS unfilled_qty,
         CASE
           WHEN NOT e.probe_available THEN 'D NO_USABLE_PROBE (no own-fill probe, or book unreadable)'
           WHEN e.required_qty_max = 0 THEN 'C ZERO REQUIRED QTY (truncation absorbs it)'
           WHEN w.sh_at_max >= e.required_qty_max - 1e-9
             THEN 'A FULLY_MEASURABLE_BUY (ladder covers the conservative qty)'
           WHEN e.n_levels >= 8
             THEN 'B2 DEPTH_TRUNCATED_AT_8 (stored ladder ran out; NOT proven unfillable)'
           ELSE 'B1 PARTIALLY_FILLABLE_BUY (book genuinely thinner than required)'
         END AS measurability
    FROM ev e LEFT JOIN walk w ON w.ev = e.ev
)
SELECT CASE WHEN px < 0.10 THEN '1 <10c' WHEN px < 0.25 THEN '2 10-25c'
            WHEN px < 0.50 THEN '3 25-50c' WHEN px < 0.75 THEN '4 50-75c'
            WHEN px < 0.90 THEN '5 75-90c' ELSE '6 >=90c' END AS price_band,
       count(*) AS events,
       round((100.0 * count(*) FILTER (WHERE measurability LIKE 'A %')
              / NULLIF(count(*), 0))::numeric, 2) AS PCT_MEASURABLE,
       round((100.0 * count(*) FILTER (WHERE measurability LIKE 'D %')
              / NULLIF(count(*), 0))::numeric, 2) AS pct_no_probe,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS dm_leg_notional,
       round((100.0 * percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_edge))::numeric, 3)
         AS p50_pair_edge_pct
  FROM b WHERE local_side = 'BUY '
 GROUP BY 1 ORDER BY 1;


\echo '== 5. SELL side: PARTIALLY IDENTIFIED against a contemporaneous neutral bid =='
-- The only retained neutral quote is mirror_shadow's US top-of-book bid for the
-- long side (_paced_bbo). It is per shadow TICK, not per fill, so it is joined
-- AS-OF: the most recent shadow bid at or before the event, with its age
-- reported. No depth exists, therefore NO fillability, NO VWAP, NO queue
-- position, NO fill probability and NO realised SELL P&L is inferred here.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px, b.source
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), pair AS (
  -- RN1's condition-level matched PAIR edge, on the same canonical rows Check A
  -- used: 1 - (vwap_yes + vwap_no). Compared against execution drag below.
  SELECT condition_id,
         1.0 - ( sum(sh * px) FILTER (WHERE outcome_index = 0)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0)
               + sum(sh * px) FILTER (WHERE outcome_index = 1)
                 / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) ) AS pair_edge
    FROM canon GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px, NULL::text AS source
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px, source FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), tti AS (
  -- TARGET_TRANSITION_IDENTIFIED only: the primary causal cohort.
  SELECT y.*,
         abs(y.as_of_ratio * y.signed_dn) AS d_cont,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9) AS d_int
    FROM l1 y
   WHERE y.ts >= timestamptz '2026-08-05 00:00Z'
     AND y.as_of_long IS NOT NULL AND y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0
     AND y.signed_dn IS NOT NULL AND y.sh > 0
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_long  IS DISTINCT FROM y.as_of_long)
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
), q AS (
  SELECT z.condition_id, z.ts, z.ev, z.sh, z.px, z.source, z.d_m, z.signed_dn, z.d_cont,
         GREATEST(0, z.m - 1) AS required_qty_min,
         CASE WHEN z.d_int THEN z.m ELSE z.m + 1 END AS required_qty_max,
         CASE WHEN z.signed_dn > 0 THEN 'BUY ' ELSE 'SELL' END AS local_side,
         (z.d_cont >= 2.0) AS side_forced
    FROM tti z WHERE z.signed_dn <> 0
), ev AS (
  -- THE EXACT OWN-FILL PROBE AND NOTHING ELSE. p.trade_id = this fill's id.
  -- No envelope borrowing, no nearest-fill substitution, no later snapshot.
  SELECT q.*, pr.pair_edge, mk.sport,
         p.probe_at, p.reaction_s, p.book_ok, p.best_ask, p.depth,
         (p.trade_id IS NOT NULL AND p.book_ok AND p.best_ask IS NOT NULL
          AND p.depth IS NOT NULL AND jsonb_typeof(p.depth) = 'array'
          AND jsonb_array_length(p.depth) > 0) AS probe_available,
         COALESCE(jsonb_array_length(p.depth), 0) AS n_levels
    FROM q
    LEFT JOIN copy_probes p ON p.trade_id = q.ev
    LEFT JOIN pair pr ON pr.condition_id = q.condition_id
    LEFT JOIN markets mk ON mk.condition_id = q.condition_id
), lad AS (
  SELECT e.ev, lv.ord,
         (lv.lvl->>0)::float8 AS lvl_px,
         (lv.lvl->>1)::float8 AS lvl_sz
    FROM ev e
    CROSS JOIN LATERAL jsonb_array_elements(e.depth) WITH ORDINALITY AS lv(lvl, ord)
   WHERE e.probe_available
), cum AS (
  SELECT l.*, sum(l.lvl_sz) OVER w AS cum_sz
    FROM lad l WINDOW w AS (PARTITION BY l.ev ORDER BY l.ord ROWS UNBOUNDED PRECEDING)
), walk AS (
  -- Walk the ACTUAL stored ladder to the event's own required quantity.
  -- take(Q) at a level = LEAST(level_size, GREATEST(Q - shares_above, 0)).
  -- No fixed $1k/$5k proxy is used anywhere.
  SELECT c.ev,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_min - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_min,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0)))
           AS sh_at_max,
         sum(LEAST(c.lvl_sz, GREATEST(e.required_qty_max - (c.cum_sz - c.lvl_sz), 0))
             * c.lvl_px) AS cost_at_max,
         sum(c.lvl_sz) AS depth_total_sh
    FROM cum c JOIN ev e ON e.ev = c.ev
   GROUP BY c.ev
), b AS (
  SELECT e.*, w.sh_at_min, w.cost_at_min, w.sh_at_max, w.cost_at_max, w.depth_total_sh,
         CASE WHEN w.sh_at_min > 0 THEN w.cost_at_min / w.sh_at_min END AS vwap_at_min,
         CASE WHEN w.sh_at_max > 0 THEN w.cost_at_max / w.sh_at_max END AS vwap_at_max,
         CASE WHEN e.required_qty_max > 0
              THEN LEAST(1.0, w.sh_at_max / e.required_qty_max) END AS fill_fraction_at_max,
         GREATEST(e.required_qty_max - COALESCE(w.sh_at_max, 0), 0) AS unfilled_qty,
         CASE
           WHEN NOT e.probe_available THEN 'D NO_USABLE_PROBE (no own-fill probe, or book unreadable)'
           WHEN e.required_qty_max = 0 THEN 'C ZERO REQUIRED QTY (truncation absorbs it)'
           WHEN w.sh_at_max >= e.required_qty_max - 1e-9
             THEN 'A FULLY_MEASURABLE_BUY (ladder covers the conservative qty)'
           WHEN e.n_levels >= 8
             THEN 'B2 DEPTH_TRUNCATED_AT_8 (stored ladder ran out; NOT proven unfillable)'
           ELSE 'B1 PARTIALLY_FILLABLE_BUY (book genuinely thinner than required)'
         END AS measurability
    FROM ev e LEFT JOIN walk w ON w.ev = e.ev
), bidrows AS (
  SELECT condition_id, at, bid FROM mirror_shadow WHERE bid IS NOT NULL
), s AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, bid,
         NULL::float8 AS px, NULL::float8 AS dm, NULL::float8 AS rq
    FROM bidrows
  UNION ALL
  SELECT condition_id, ts, 1, ev, NULL, px, d_m, required_qty_max
    FROM b WHERE local_side = 'SELL'
), sr AS (
  SELECT s.*, count(s.bid) OVER wq AS gb,
         first_value(s.bid) OVER wq AS dummy
    FROM s WINDOW wq AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), sc AS (
  SELECT sr.*,
         first_value(sr.bid) OVER wg AS as_of_bid,
         first_value(sr.ts)  OVER wg AS as_of_bid_ts
    FROM sr WINDOW wg AS (PARTITION BY sr.condition_id, sr.gb ORDER BY sr.ts, sr.pri, sr.ev)
)
SELECT CASE WHEN ev IS NULL THEN NULL
            WHEN as_of_bid IS NULL THEN 'D NO NEUTRAL BID RETAINED BEFORE THE EVENT'
            WHEN as_of_bid > px + 0.0001 THEN 'A BID ABOVE his fill price'
            WHEN as_of_bid < px - 0.0001 THEN 'C BID BELOW his fill price'
            ELSE 'B BID AT his fill price (within 1bp)' END AS bid_vs_reference,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(dm)::numeric, 0) AS dM_shares,
       round(sum(dm * px)::numeric, 0) AS dm_leg_notional,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (as_of_bid - px) * 100.0)::numeric, 3)
         AS p50_bid_minus_ref_cents,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - as_of_bid_ts) / 60.0)::numeric, 2) AS p50_bid_age_min,
       round(percentile_cont(0.9) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - as_of_bid_ts) / 60.0)::numeric, 2) AS p90_bid_age_min
  FROM sc WHERE ev IS NOT NULL
 GROUP BY 1 ORDER BY 1;
