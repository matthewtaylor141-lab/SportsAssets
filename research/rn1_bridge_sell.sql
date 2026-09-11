-- ============================================================================
-- THE BRIDGE, STATEMENT 3 ONLY -- the selection test and the SELL side.
-- Split out of rn1_bridge.sql UNCHANGED after run 56: statements 1 and 2 both
-- COMPLETED and their results are recorded, and statement 3 was still running
-- when `timeout` hit the 17-minute wall (psql exit=124). Nothing about it
-- failed; it simply had no clock left, because each statement rebuilds the
-- whole designation-stream + probe-scan chain from scratch and two of those
-- had already been paid for. Run alone it gets the full budget.
--
-- NOT A DEFINITION CHANGE: byte-for-byte the same statement, same cohort, same
-- window (ts < 2026-09-11 12:00Z), so it composes with runs 56's blocks 1-2.
--
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
-- ---------------------------------------------------------------------------
-- WHY RUNS 51 AND 53 NEVER RETURNED -- MEASURED, NOT REASONED (runs 52, 54).
--
-- I guessed twice and was wrong twice, so run 54 measured instead, and it
-- REFUTED MY OWN STATED SUSPECT. I had written that the killer was the direct
-- join to copy_probes on the unindexed trade_id column. There is indeed no
-- index on trade_id (run 54 statement 2: only pkey(id), probe_at, and
-- whale_id+probe_at), but run 52's plan shows the planner already choosing a
-- HASH RIGHT JOIN -- one sequential pass over copy_probes hashed against the
-- events. That is the shape check A settled on. The probe join was never the
-- problem and is UNCHANGED here.
--
-- THE ACTUAL CAUSE IS A CARDINALITY COLLAPSE. Run 52's plan estimates the
-- canon CTE at rows=1. Run 54 statement 3 measures it at 368,544. The old
-- canon was a three-column self-join whose third condition was
--     b.feed = (CASE WHEN bool_or(base.feed = 'venue') THEN 'venue' ELSE ... )
-- a CASE over an aggregate, for which the planner has no statistics; the
-- selectivities multiplied down and clamped to the floor of 1.
--
-- rows=1 then propagated into every node above it, and the fatal consequence
-- is visible verbatim in run 52's plan for statement 2:
--     Nested Loop Left Join (rows=1)
--       -> CTE Scan on ev                       <- the OUTER side
--       -> GroupAggregate -> Merge Join -> WindowAgg -> Nested Loop
--            -> Function Scan on jsonb_array_elements
-- With a one-row outer estimate a nested loop is free, so the planner put the
-- ENTIRE LADDER WALK -- sort, window, group over 7.5M ladder levels -- on the
-- INNER side, to be re-executed once per event. At ~31k real events that does
-- not finish, which is exactly what runs 51 and 53 did.
--
-- THREE FIXES, none of which changes a definition, a denominator, a cohort or
-- a measured quantity:
--   1 canon becomes a ONE-PASS WINDOW dedup with identical semantics, phrased
--     as "drop the losers" so the planner's default lands near 1.0 rather than
--     near 0. (The equality phrasing would have estimated 0.005 and clamped
--     again -- the point is the ESTIMATE, not the tidiness.)
--   2 lad / cum / walk are AS MATERIALIZED. This is the belt to the braces:
--     a materialized CTE is computed ONCE into a tuplestore and physically
--     CANNOT be re-driven per outer row, whatever the estimate turns out to
--     be. Fixing the estimate alone would leave the same trap one bad
--     selectivity guess away.
--   3 the event window gains an UPPER bound, ts < 2026-09-11 12:00Z. The read
--     replica is live and RN1 is still trading: run 54 counted the SAME
--     canonical population twice in one file and got 368,544 then 368,549,
--     seconds apart. Without a fixed upper edge no two runs measure the same
--     population and no figure here is reproducible. This cut is EARLIER than
--     run 47, so the TTI counts below will be slightly SMALLER than run 47's
--     31,265 -- that is the bound, not a discrepancy.
--
-- base itself is deliberately NOT time-filtered: legs are derived from it, and
-- a condition whose second token last traded before the window would lose its
-- leg identity and manufacture phantom dM. That is check B defect (a), fixed
-- once already, and it is not being reintroduced for a speed gain.
-- ---------------------------------------------------------------------------
--
-- Read-only: three SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 3. SELECTION TEST: is the measurable BUY subset like the rest of the cohort? =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS (
  -- THE CROSS-FEED DEDUP, NOW A ONE-PASS WINDOW INSTEAD OF A THREE-COLUMN
  -- SELF-JOIN. Identical rows out: a 'cash' row is dropped only when the same
  -- (tx_hash, asset) also carries a 'venue' row. The JOIN FORM WAS THE WHOLE
  -- PROBLEM. Its third condition was a CASE over an aggregate, which the
  -- planner has no statistics for, so it multiplied the selectivities down and
  -- CLAMPED canon TO rows=1 -- against 368,544 actual (run 54 statement 3).
  -- Run 52's plan shows every downstream node inheriting that rows=1.
  -- Written as "drop the losers" rather than "keep the winners" deliberately:
  -- NOT (cash AND a venue row exists) estimates near 1.0, which is the truth,
  -- where the equality form would have estimated 0.005 and clamped again.
  SELECT z.condition_id, z.ts, z.id, z.outcome_index, z.sh, z.px, z.source
    FROM (SELECT b.*, bool_or(b.feed = 'venue')
                        OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
            FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
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
     AND y.ts <  timestamptz '2026-09-11 12:00Z'
     AND y.as_of_long IS NOT NULL AND y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0
     AND y.signed_dn IS NOT NULL AND y.sh > 0
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_long  IS DISTINCT FROM y.as_of_long)
     AND NOT (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
), q AS (
  SELECT z.condition_id, z.ts, z.ev, z.sh, z.px, z.source, z.d_m, z.signed_dn, z.d_cont,
         GREATEST(0, z.m - 1) AS required_qty_min,
         CASE WHEN z.d_int THEN z.m ELSE z.m + 1 END AS required_qty_max,
         CASE WHEN z.signed_dn > 0 THEN 'BUY ' ELSE 'SELL' END AS local_side,
         -- WHICH TOKEN DID HE ACTUALLY FILL? For a SELL-required transition the
         -- signed coordinate is negative precisely BECAUSE his fill was on the
         -- token that is NOT the designated long. So px is the COMPLEMENT's
         -- price and is NOT comparable to a bid quoted on the long.
         --
         -- ONLY THE BOOLEAN IS PROJECTED, and the reason is HYGIENE, NOT A
         -- MEASURED FAULT. Read the correction below before citing this.
         --
         -- ay / an / as_of_long are ~77-byte CTF token id strings. Carrying
         -- three of them per row widens ev, then b, then the UNION in `s`,
         -- whose window sorts every SELL event together with every retained
         -- shadow bid row for those conditions, and a sort's row width is set
         -- by its widest input row. That is a real cost and a good reason to
         -- project the boolean instead of the strings it was computed from.
         --
         -- IT IS NOT, HOWEVER, WHY RUN 58 WAS CANCELLED, AND I SAID IT WAS.
         -- I claimed run 58 had passed 23 minutes and blown its ceiling. It
         -- had not. Its step started 14:40:06Z and I cancelled it at 14:50:44Z
         -- -- NINE AND A HALF MINUTES IN, against run 57's 10m19s on the same
         -- statement. It was almost certainly about to finish, and I killed it.
         -- I had been inferring elapsed wall-clock from my own progress through
         -- the work instead of reading a clock, so the "23 minutes" that this
         -- fix was justified by never existed.
         --
         -- So: the change is harmless and probably worth keeping, but it is an
         -- UNTESTED HYPOTHESIS about width, not a diagnosis. Nothing here
         -- measured a spill. If this statement is ever slow again, measure it
         -- -- read the clock, read the plan -- before changing anything.
         (CASE WHEN z.oi = 0 THEN z.ay ELSE z.an END = z.as_of_long) AS fill_on_long,
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
), lad AS MATERIALIZED (
  SELECT e.ev, lv.ord,
         (lv.lvl->>0)::float8 AS lvl_px,
         (lv.lvl->>1)::float8 AS lvl_sz
    FROM ev e
    CROSS JOIN LATERAL jsonb_array_elements(e.depth) WITH ORDINALITY AS lv(lvl, ord)
   WHERE e.probe_available
), cum AS MATERIALIZED (
  SELECT l.*, sum(l.lvl_sz) OVER w AS cum_sz
    FROM lad l WINDOW w AS (PARTITION BY l.ev ORDER BY l.ord ROWS UNBOUNDED PRECEDING)
), walk AS MATERIALIZED (
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
), sell_conds AS (
  -- RESTRICT FIRST. Previously this unioned EVERY mirror_shadow row carrying a
  -- bid (~250k) against the SELL events, so conditions with no SELL event at
  -- all still formed window partitions and were sorted. Same shape as the
  -- check B landmine; reintroduced by me and removed here.
  SELECT DISTINCT condition_id FROM b WHERE local_side = 'SELL'
), bidrows AS (
  SELECT ms.condition_id, ms.at, ms.bid
    FROM mirror_shadow ms JOIN sell_conds sc ON sc.condition_id = ms.condition_id
   WHERE ms.bid IS NOT NULL
), s AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, bid,
         NULL::float8 AS px, NULL::float8 AS dm, NULL::float8 AS rq, NULL::boolean AS fol
    FROM bidrows
  UNION ALL
  SELECT condition_id, ts, 1, ev, NULL, px, d_m, required_qty_max, fill_on_long
    FROM b WHERE local_side = 'SELL'
), sr AS (
  SELECT s.*, count(s.bid) OVER wq AS gb,
         first_value(s.bid) OVER wq AS dummy
    FROM s WINDOW wq AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), sc AS (
  SELECT sr.*,
         first_value(sr.bid) OVER wg AS as_of_bid,
         first_value(sr.ts)  OVER wg AS as_of_bid_ts,
         -- THE REFERENCE MUST BE QUOTED ON THE TOKEN WE WOULD SELL. We reduce a
         -- LONG, so we sell the DESIGNATED LONG, and mirror_shadow.bid is the
         -- venue's quote for the long side (migration 046:25) -- the bid is the
         -- right one. px was not: on a SELL-required transition his fill is on
         -- the COMPLEMENT, so bid - px compared p against 1-p and produced the
         -- +33c / -39c medians of the first run, which measure the price level
         -- and nothing about execution. Where he filled the long itself, px IS
         -- the reference; otherwise the long-side reference is 1 - px, which
         -- RESTS ON PAIR PARITY and is labelled as an assumption, not a quote.
         CASE WHEN sr.fol THEN sr.px
              WHEN sr.px IS NOT NULL THEN 1.0 - sr.px END AS ref_long
    FROM sr WINDOW wg AS (PARTITION BY sr.condition_id, sr.gb ORDER BY sr.ts, sr.pri, sr.ev)
)
SELECT CASE WHEN ev IS NULL THEN NULL
            WHEN as_of_bid IS NULL THEN 'D NO NEUTRAL BID RETAINED BEFORE THE EVENT'
            WHEN fol IS NOT TRUE AND ref_long IS NULL
              THEN 'E REFERENCE NOT AVAILABLE ON THE LONG TOKEN'
            WHEN as_of_bid > ref_long + 0.0001 THEN 'A BID ABOVE the long-side reference'
            WHEN as_of_bid < ref_long - 0.0001 THEN 'C BID BELOW the long-side reference'
            ELSE 'B BID AT the long-side reference (within 1bp)' END AS bid_vs_reference,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(dm)::numeric, 0) AS dM_shares,
       round(sum(dm * px)::numeric, 0) AS dm_leg_notional,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (as_of_bid - ref_long) * 100.0)::numeric, 3)
         AS p50_bid_minus_ref_cents,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - as_of_bid_ts) / 60.0)::numeric, 2) AS p50_bid_age_min,
       round(percentile_cont(0.9) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - as_of_bid_ts) / 60.0)::numeric, 2) AS p90_bid_age_min
  FROM sc WHERE ev IS NOT NULL
 GROUP BY 1 ORDER BY 1;
