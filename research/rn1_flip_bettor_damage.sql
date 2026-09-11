-- ============================================================================
-- THE BETTOR CAUSAL BRIDGE: did RN1's PROVEN flips drive harmful BETTOR
-- reversals, and how many dollars did that cost? (2026-09-11, read-only.)
-- Owner-specified. mirror_live = false throughout; nothing here writes.
--
-- ---------------------------------------------------------------------------
-- THE QUESTION, in the owner's words, and the answer's required shape:
--   "How many dollars of BETTOR's disproportionate downside are explained by
--    the old mirror interpreting RN1 complement-driven flips/pair formation as
--    PMUS liquidation/reversal? Counts alone do not answer that question."
-- So every statement below that reports a count also reports the dollars, and
-- the final statement splits the dollars three ways:
--     IDENTIFIED_REALIZED_DAMAGE / PARTIALLY_IDENTIFIED_DAMAGE /
--     UNIDENTIFIED_EXECUTION_EFFECT
-- rather than presenting a single number whose unmeasured part is hidden.
--
-- ---------------------------------------------------------------------------
-- WHY STATEMENTS 1 AND 2 COME BEFORE ANY ATTRIBUTION.
--
-- RN1's analysis window is 2026-08-05 -> 2026-09-11 12:00Z, 37 days. The
-- BETTOR mirror did not exist for most of it: migration 046 (the shadow) is
-- dated 2026-09-02, 047 (live books) 2026-09-02, 050 (shorts) 2026-09-05, 057
-- (flow base) 2026-09-08, and trading was PAUSED by owner order at 15:13Z
-- today. A proven RN1 flip on 2026-08-11 cannot have damaged a mirror that did
-- not yet hold a book, and a bridge built without measuring that first would
-- silently attribute the whole cohort to an architecture that was absent for
-- most of it.
--
-- THIS IS THE VACUITY DISCIPLINE, APPLIED BEFORE THE FACT RATHER THAN AFTER.
-- Four times in this work a filter, a guard, a harness or a check could not
-- fail and looked reassuring for exactly that reason. A damage query whose
-- population is empty produces zeros that read as "no damage" rather than "no
-- data". So statements 1 and 2 measure the reach of the evidence, EVERY later
-- statement prints its own witness count, and any figure computed on an empty
-- or near-empty witness set is to be read as NOT MEASURED, never as zero.
--
-- ---------------------------------------------------------------------------
-- THE OLD RULE IS NOT ONE RULE, and statement 3 measures which was live.
--
-- I was asked to "reconstruct the old production target using the
-- contemporaneous long_asset, other_asset, copy_ratio, and the old signed-net
-- rule". There is no single such rule across this window. Reading the code and
-- the migrations end to end, the target rule changed at least three times:
--
--   P1  (047, 2026-09-02)  target = trunc(ratio * his_net), LONG ONLY:
--       analytics/mirror.py plan_target returns target 0 with
--       why = "short side not admitted" whenever raw < 0. A flip against the
--       book's long axis therefore collapses the target to ZERO -- this is the
--       precise mechanism the owner is asking about, and it is a full
--       liquidation, not a reduction.
--   P2  (050, 2026-09-05)  shorts admitted behind MIRROR_SHORTS, so raw < 0
--       becomes a BUY_SHORT rather than a collapse to zero.
--   E12 (057, 2026-09-08)  the target is sized on his net LESS the block he
--       held before first sight (flow_base), pro-rata ratcheted, so
--       trunc(ratio * his_net) is no longer the target at all.
--
-- RECONSTRUCTING ONE RULE ACROSS THREE REGIMES WOULD BE A FABRICATION. So the
-- PRIMARY evidence here is production's OWN recorded target, not my arithmetic:
--     mirror_shadow.target / target_raw / ratio / his_net   per tick
--     mirror_orders.target_at_place / ledger_at_place       per order
-- The reconstruction is carried only as a CROSS-CHECK, and statement 3 reports
-- how often it agrees with the recorded value, per day. Where it disagrees the
-- reconstruction is wrong about that day's regime and must not be used.
--
-- ---------------------------------------------------------------------------
-- TERMINOLOGY LOCK (research/TERMINOLOGY.md), observed strictly below.
--
--   RN1_SOURCE_FILL_SIDE      his source-venue fill vocabulary. BUY only in
--                             this window. He reduces exposure by BUYING THE
--                             COMPLEMENT. No complement BUY of his is ever
--                             called a SELL anywhere in this file.
--   BETTOR_REQUIRED_ACTION    our PMUS target action: BUY / SELL / HOLD.
--                             mirror_orders.side spells these BUY_LONG /
--                             SELL_LONG and that is BETTOR vocabulary.
-- Bare "SELL" is not used. The four target-transition classes are named
-- BETTOR_TARGET_REVERSAL / BETTOR_REDUCTION_ONLY / BETTOR_ADD_SAME_DIRECTION /
-- BETTOR_HOLD, all in the BETTOR namespace.
--
-- ---------------------------------------------------------------------------
-- SCOPE BOUNDARY THE RUN-72 PROOF DOES *NOT* EXTEND TO, stated before it can
-- be borrowed by accident.
--
-- Run 72 proved the invariance of the FLIPPED BOOLEAN per condition. It
-- explicitly does NOT cover the number of reductions, the number of rebuild
-- episodes, peak directional exposure, or the timing or order of reductions.
-- THE EVENTS BUILT IN STATEMENT 4 ARE EXACTLY SUCH WITHIN-PATH OBJECTS: which
-- fill carries the crossing, and how many crossings there are, are properties
-- of the chosen (ts, id ASC) tiebreak, not of the proof. Therefore:
--     the COHORT is proof-certified (PROVABLY_FLIPPED, 7,924 conditions)
--     the EVENT LIST inside it is NOT
-- Statement 4 reports how many events sit in conditions carrying a cross-leg
-- timestamp tie, which is where the tiebreak can move them. Those events are
-- reported, never counted as proven.
--
-- COHORTS, per instruction: PROVABLY_FLIPPED is primary, PROVABLY_NO_FLIP is
-- the control, ORDER_NOT_PROVEN_INVARIANT is reported separately and never
-- folded into either.
--
-- ---------------------------------------------------------------------------
-- WHAT CANNOT BE IDENTIFIED FROM RETAINED DATA, recorded here so the final
-- ledger's third bucket is not mistaken for laziness:
--
--   PMUS DEPTH IS NOT RETAINED. Grepping every migration for a stored order
--   book returns one column, copy_probes.depth, and that is RN1'S venue
--   (clob.polymarket.com), not Polymarket US. mirror_shadow keeps bid/ask/mark
--   -- US TOP OF BOOK ONLY, per shadow tick, not per fill. So the price a
--   forced liquidation WOULD have got at another size, or the book it ate, is
--   not historically measurable. That is the UNIDENTIFIED_EXECUTION_EFFECT
--   bucket and it stays unidentified.
--
--   FEES ARE NOT RECORDED ON THE ORDER ROW. mirror_orders carries cash_usd
--   (accumulated, read by the day cap as notional) and avg_px x filled. Their
--   difference is reported as cash_vs_px_residual and is NOT certified as a
--   fee: it also absorbs partial-fill booking and rounding.
--
--   TRUE_CASH_PATH = NOT IDENTIFIABLE FROM THIS LEDGER (the standing
--   structural exclusion) is unchanged by anything here.
--
-- Read-only: nine SELECTs.
-- ============================================================================


\echo '== 1. COVERAGE GATE A: does BETTOR evidence exist, and over what window? =='
-- Nothing downstream means anything until this is read. The RN1 window is 37
-- days; if the mirror tables cover six of them, every later figure speaks to
-- six days and must be labelled so.
WITH rn1 AS (
  SELECT min(t.ts) AS first_fill, max(t.ts) AS last_fill,
         count(DISTINCT t.ts::date) AS days
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
     AND t.ts <  timestamptz '2026-09-11 12:00Z'
), src AS (
  SELECT 'RN1 canonical BUY fills (the analysis window)' AS surface,
         (SELECT days FROM rn1) AS distinct_days,
         to_char((SELECT first_fill FROM rn1), 'YYYY-MM-DD HH24:MI') AS first_row,
         to_char((SELECT last_fill  FROM rn1), 'YYYY-MM-DD HH24:MI') AS last_row,
         NULL::bigint AS rows_rn1_only
  UNION ALL
  SELECT 'mirror_shadow (per-tick target record)',
         count(DISTINCT at::date),
         to_char(min(at), 'YYYY-MM-DD HH24:MI'), to_char(max(at), 'YYYY-MM-DD HH24:MI'),
         count(*) FILTER (WHERE lower(whale) = 'rn1')
    FROM mirror_shadow
  UNION ALL
  SELECT 'mirror_books (live books)',
         count(DISTINCT opened_at::date),
         to_char(min(opened_at), 'YYYY-MM-DD HH24:MI'), to_char(max(opened_at), 'YYYY-MM-DD HH24:MI'),
         count(*) FILTER (WHERE lower(whale) = 'rn1')
    FROM mirror_books
  UNION ALL
  SELECT 'mirror_orders (BETTOR_REQUIRED_ACTION executions)',
         count(DISTINCT placed_at::date),
         to_char(min(placed_at), 'YYYY-MM-DD HH24:MI'), to_char(max(placed_at), 'YYYY-MM-DD HH24:MI'),
         count(*) FILTER (WHERE lower(whale) = 'rn1')
    FROM mirror_orders
)
SELECT * FROM src;


\echo '== 2. COVERAGE GATE B: how much of the proven-flip cohort is even REACHABLE? =='
-- Per proof class: how many conditions, and how many of them BETTOR ever saw
-- at all (a shadow tick), ever opened a book on, and ever sent an order on.
-- The dollars are RN1's acquisition cost, so the unreachable share is priced.
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
), blk AS MATERIALIZED (
  SELECT condition_id, ts,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) AS ytot,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) AS ntot
    FROM buys GROUP BY 1, 2
), bs AS MATERIALIZED (
  SELECT b.*,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING) AS d_after,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING)
           - (b.ytot - b.ntot) AS d_before
    FROM blk b
), proof AS MATERIALIZED (
  SELECT condition_id,
         CASE WHEN (bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
               AND (bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9)) THEN 1
              WHEN NOT (((bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
                          OR bool_or(d_before + ytot >  1e-9))
                    AND ((bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9))
                          OR bool_or(d_before - ntot < -1e-9))) THEN 2
              ELSE 3 END AS proof_class
    FROM bs GROUP BY 1
), econ AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost, min(ts) AS first_fill, max(ts) AS last_fill,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS qy,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS qn,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 0), 0) AS cy,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 1), 0) AS cn
    FROM buys GROUP BY 1
), saw AS MATERIALIZED (
  SELECT condition_id, count(*) AS shadow_ticks, min(at) AS first_seen
    FROM mirror_shadow WHERE lower(whale) = 'rn1' GROUP BY 1
), bk AS MATERIALIZED (
  SELECT condition_id, count(*) AS books, min(opened_at) AS first_book
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), ords AS MATERIALIZED (
  SELECT b.condition_id, count(*) AS orders,
         count(*) FILTER (WHERE o.filled > 0) AS orders_filled
    FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id
   WHERE lower(o.whale) = 'rn1' GROUP BY 1
), k AS MATERIALIZED (
  SELECT p.condition_id, p.proof_class, e.acq_cost, e.first_fill,
         COALESCE(LEAST(e.qy, e.qn)
                  * (CASE WHEN e.qy > 0 AND e.qn > 0
                          THEN e.cy / e.qy + e.cn / e.qn END), 0) AS matched_cost,
         COALESCE(s.shadow_ticks, 0) AS shadow_ticks,
         COALESCE(bo.books, 0) AS books, COALESCE(o.orders, 0) AS orders,
         COALESCE(o.orders_filled, 0) AS orders_filled
    FROM proof p
    JOIN econ e  ON e.condition_id = p.condition_id
    LEFT JOIN saw s  ON s.condition_id = p.condition_id
    LEFT JOIN bk  bo ON bo.condition_id = p.condition_id
    LEFT JOIN ords o ON o.condition_id = p.condition_id
)
SELECT CASE proof_class WHEN 1 THEN '1 PROVABLY_FLIPPED (primary cohort)'
                        WHEN 2 THEN '2 PROVABLY_NO_FLIP (control cohort)'
                        ELSE '3 ORDER_NOT_PROVEN_INVARIANT (reported apart)' END AS cohort,
       count(*) AS conditions,
       round(sum(acq_cost)::numeric, 0) AS rn1_acq_cost,
       round(sum(matched_cost)::numeric, 0) AS rn1_matched_cost,
       count(*) FILTER (WHERE shadow_ticks > 0) AS bettor_ever_saw,
       count(*) FILTER (WHERE books > 0) AS bettor_opened_a_book,
       count(*) FILTER (WHERE orders > 0) AS bettor_sent_an_order,
       count(*) FILTER (WHERE orders_filled > 0) AS bettor_got_a_fill,
       round((100.0 * count(*) FILTER (WHERE books > 0) / count(*))::numeric, 2)
         AS pct_conditions_with_a_book,
       round(sum(acq_cost) FILTER (WHERE books > 0)::numeric, 0) AS acq_cost_with_a_book,
       round((100.0 * sum(acq_cost) FILTER (WHERE books > 0)
              / NULLIF(sum(acq_cost), 0))::numeric, 2) AS pct_acq_cost_with_a_book,
       -- the temporal half of the gate: conditions whose RN1 activity began
       -- before the mirror held any book at all are structurally unreachable
       count(*) FILTER (WHERE first_fill < (SELECT min(opened_at) FROM mirror_books))
         AS conditions_starting_before_the_first_book
  FROM k GROUP BY 1 ORDER BY 1;


\echo '== 3. WHICH OLD RULE WAS LIVE? recorded target vs reconstructed, per day =='
-- The reconstruction trunc(ratio * his_net) is P1's rule. Where it matches the
-- recorded target the regime is P1-shaped and the reconstruction may be used as
-- a cross-check; where it does not, the day was running P2 shorts or E12 flow
-- sizing and the reconstruction is simply wrong about that day. This is a
-- measurement, not an assumption, and it decides which days statement 5 may
-- reconstruct on.
SELECT at::date AS day,
       count(*) AS shadow_ticks,
       count(*) FILTER (WHERE target IS NOT NULL) AS with_recorded_target,
       count(*) FILTER (WHERE ratio IS NOT NULL AND his_net IS NOT NULL)
         AS with_ratio_and_net,
       -- P1: trunc toward zero, long only (raw < 0 -> 0)
       count(*) FILTER (WHERE target IS NOT NULL AND ratio IS NOT NULL
                          AND his_net IS NOT NULL
                          AND target = CASE WHEN ratio * his_net < 0 THEN 0
                                            ELSE trunc(ratio * his_net)::bigint END)
         AS agrees_with_P1_long_only,
       -- P2: the same, but a negative raw is admitted as a short target
       count(*) FILTER (WHERE target IS NOT NULL AND ratio IS NOT NULL
                          AND his_net IS NOT NULL
                          AND target = trunc(ratio * his_net)::bigint)
         AS agrees_with_P2_signed,
       count(*) FILTER (WHERE his_net < 0) AS ticks_with_his_net_NEGATIVE,
       count(*) FILTER (WHERE his_net < 0 AND target = 0)
         AS negative_net_TARGET_COLLAPSED_TO_ZERO,
       count(*) FILTER (WHERE his_net < 0 AND target < 0)
         AS negative_net_target_went_short,
       count(*) FILTER (WHERE capped) AS ticks_dollar_capped
  FROM mirror_shadow
 WHERE lower(whale) = 'rn1'
 GROUP BY 1 ORDER BY 1;


\echo '== 4. RN1_PROVEN_FLIP_EVENT: construction, and how much of it is reachable =='
-- An event is a fill of his at which the LAST NONZERO sign of D = qY - qN
-- changes. Flat is carried across, so + -> 0 -> - is one event at the fill that
-- lands negative, and + -> 0 -> + is none. Cohort = PROVABLY_FLIPPED only.
--
-- NOT PROOF-CERTIFIED: which fill carries a crossing, and how many crossings
-- there are, depend on the (ts, id ASC) tiebreak. The run-72 proof covers the
-- boolean, not the path shape. The tie column below is where that matters.
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
), blk AS MATERIALIZED (
  SELECT condition_id, ts,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) AS ytot,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) AS ntot,
         count(*) AS n, count(DISTINCT outcome_index) AS legs
    FROM buys GROUP BY 1, 2
), bs AS MATERIALIZED (
  SELECT b.*,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING) AS d_after,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING)
           - (b.ytot - b.ntot) AS d_before
    FROM blk b
), proof AS MATERIALIZED (
  SELECT condition_id,
         CASE WHEN (bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
               AND (bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9)) THEN 1
              WHEN NOT (((bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
                          OR bool_or(d_before + ytot >  1e-9))
                    AND ((bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9))
                          OR bool_or(d_before - ntot < -1e-9))) THEN 2
              ELSE 3 END AS proof_class,
         bool_or(n > 1 AND legs = 2) AS has_cross_leg_tie
    FROM bs GROUP BY 1
), w AS MATERIALIZED (
  SELECT b.*,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_y,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_n
    FROM buys b JOIN proof p ON p.condition_id = b.condition_id
   WHERE p.proof_class = 1
), s AS MATERIALIZED (
  SELECT w.*,
         w.cum_y - w.cum_n AS d_after,
         (w.cum_y - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END)
           - (w.cum_n - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END) AS d_bef,
         w.cum_y - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS cum_y_bef,
         w.cum_n - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS cum_n_bef
    FROM w
), sg AS MATERIALIZED (
  -- carry the LAST NONZERO sign across exact-flat states: nz is NULL at flat,
  -- and grp counts the non-null values seen so far, so every flat run shares a
  -- group with the nonzero state that preceded it
  SELECT s.*,
         CASE WHEN abs(s.d_after) > 1e-9 THEN sign(s.d_after) END AS nz,
         count(CASE WHEN abs(s.d_after) > 1e-9 THEN 1 END)
           OVER (PARTITION BY s.condition_id ORDER BY s.rn ROWS UNBOUNDED PRECEDING) AS grp
    FROM s
), carry AS MATERIALIZED (
  SELECT sg.*,
         first_value(sg.nz) OVER (PARTITION BY sg.condition_id, sg.grp ORDER BY sg.rn)
           AS sign_after
    FROM sg
), ev AS MATERIALIZED (
  SELECT c.*,
         lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn) AS sign_before,
         LEAST(c.cum_y, c.cum_n) - LEAST(c.cum_y_bef, c.cum_n_bef) AS d_m,
         LEAST(c.sh, abs(c.d_bef)) AS directional_reduction_qty,
         GREATEST(c.sh - abs(c.d_bef), 0) AS flip_excess_qty
    FROM carry c
), flips AS MATERIALIZED (
  SELECT e.*, p.has_cross_leg_tie
    FROM ev e JOIN proof p ON p.condition_id = e.condition_id
   WHERE e.sign_before IS NOT NULL AND e.sign_after IS NOT NULL
     AND e.sign_before <> e.sign_after
), bk AS MATERIALIZED (
  SELECT condition_id, min(opened_at) AS first_book, max(COALESCE(closed_at, now())) AS last_book
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), saw AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen
    FROM mirror_shadow WHERE lower(whale) = 'rn1' GROUP BY 1
)
SELECT count(*) AS rn1_proven_flip_events,
       count(DISTINCT f.condition_id) AS conditions_carrying_an_event,
       round(sum(f.sh * f.px)::numeric, 0) AS event_fill_notional,
       round(sum(f.d_m)::numeric, 1) AS delta_m_at_events,
       round(sum(f.directional_reduction_qty)::numeric, 1) AS directional_reduction_qty,
       round(sum(f.flip_excess_qty)::numeric, 1) AS flip_excess_qty,
       count(*) FILTER (WHERE f.sign_before > 0) AS positive_to_negative,
       count(*) FILTER (WHERE f.sign_before < 0) AS negative_to_positive,
       -- the order caveat, priced rather than asserted
       count(*) FILTER (WHERE f.has_cross_leg_tie) AS events_in_tie_conditions_NOT_PROVEN,
       -- the coverage caveat, priced the same way
       count(*) FILTER (WHERE s.first_seen IS NOT NULL AND f.ts >= s.first_seen)
         AS events_after_BETTOR_first_saw_the_market,
       count(*) FILTER (WHERE b.first_book IS NOT NULL
                          AND f.ts >= b.first_book AND f.ts <= b.last_book)
         AS events_INSIDE_an_open_book,
       round(sum(f.sh * f.px) FILTER (WHERE b.first_book IS NOT NULL
                          AND f.ts >= b.first_book AND f.ts <= b.last_book)::numeric, 0)
         AS notional_inside_an_open_book,
       to_char(min(f.ts) FILTER (WHERE b.first_book IS NOT NULL
                          AND f.ts >= b.first_book), 'YYYY-MM-DD HH24:MI')
         AS first_reachable_event
  FROM flips f
  LEFT JOIN bk  b ON b.condition_id = f.condition_id
  LEFT JOIN saw s ON s.condition_id = f.condition_id;


\echo '== 5. EVENT -> BETTOR TARGET TRANSITION, from production RECORDED targets =='
-- For every reachable event: the recorded target on the last shadow tick at or
-- before the event, and on the first tick after it. Classified in BETTOR
-- vocabulary. The reconstruction is carried beside it, never in place of it.
--
-- WITNESS COUNT FIRST. If reachable_events is small, every class count below
-- is a description of a handful of events and not a behavioural finding.
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
), blk AS MATERIALIZED (
  SELECT condition_id, ts,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) AS ytot,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) AS ntot
    FROM buys GROUP BY 1, 2
), bs AS MATERIALIZED (
  SELECT b.*,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING) AS d_after,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING)
           - (b.ytot - b.ntot) AS d_before
    FROM blk b
), proof AS MATERIALIZED (
  SELECT condition_id,
         CASE WHEN (bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
               AND (bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9)) THEN 1
              WHEN NOT (((bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
                          OR bool_or(d_before + ytot >  1e-9))
                    AND ((bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9))
                          OR bool_or(d_before - ntot < -1e-9))) THEN 2
              ELSE 3 END AS proof_class
    FROM bs GROUP BY 1
), w AS MATERIALIZED (
  SELECT b.*,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_y,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_n
    FROM buys b JOIN proof p ON p.condition_id = b.condition_id
   WHERE p.proof_class = 1
), sg AS MATERIALIZED (
  SELECT w.*, w.cum_y - w.cum_n AS d_after,
         CASE WHEN abs(w.cum_y - w.cum_n) > 1e-9 THEN sign(w.cum_y - w.cum_n) END AS nz,
         count(CASE WHEN abs(w.cum_y - w.cum_n) > 1e-9 THEN 1 END)
           OVER (PARTITION BY w.condition_id ORDER BY w.rn ROWS UNBOUNDED PRECEDING) AS grp
    FROM w
), carry AS MATERIALIZED (
  SELECT sg.*,
         first_value(sg.nz) OVER (PARTITION BY sg.condition_id, sg.grp ORDER BY sg.rn)
           AS sign_after
    FROM sg
), flips AS MATERIALIZED (
  SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn) AS sign_before
    FROM carry c
), ev AS MATERIALIZED (
  SELECT * FROM flips
   WHERE sign_before IS NOT NULL AND sign_after IS NOT NULL AND sign_before <> sign_after
), sh AS MATERIALIZED (
  -- ONE pass of mirror_shadow. A correlated lookup per event cannot use
  -- mirror_shadow_whale_market_at_idx, because lower(whale) is not the indexed
  -- expression, so it would degrade to a scan for every event.
  SELECT condition_id, at, target, his_net, ratio, ledger_net, bid, ask
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), seen AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM sh
), evs AS MATERIALIZED (
  -- EVERY proven event is carried, not only the ones BETTOR saw. Filtering to
  -- seen markets here would narrow the denominator silently and make the class
  -- shares below describe a cohort nobody named.
  SELECT e.condition_id, e.rn, e.ts, e.sh AS fill_sh, e.px,
         e.sign_before, e.sign_after,
         (s.condition_id IS NOT NULL) AS ever_seen
    FROM ev e LEFT JOIN seen s ON s.condition_id = e.condition_id
), mix AS MATERIALIZED (
  -- ticks and events on one timeline; at an identical instant the tick sorts
  -- BEFORE the event, so a tick stamped exactly at the event counts as "before"
  SELECT condition_id, at AS t, 0 AS kind,
         target, his_net, ratio, ledger_net, bid, ask,
         NULL::float8 AS fill_sh, NULL::float8 AS px,
         NULL::float8 AS sign_before, NULL::float8 AS sign_after,
         NULL::boolean AS ever_seen
    FROM sh
   WHERE condition_id IN (SELECT condition_id FROM evs)
  UNION ALL
  SELECT condition_id, ts, 1,
         NULL::int, NULL::float8, NULL::float8, NULL::int,
         NULL::float8, NULL::float8,
         fill_sh, px, sign_before, sign_after, ever_seen
    FROM evs
), g AS MATERIALIZED (
  SELECT m.*,
         count(CASE WHEN m.kind = 0 THEN 1 END)
           OVER (PARTITION BY m.condition_id ORDER BY m.t, m.kind
                 ROWS UNBOUNDED PRECEDING) AS grp_prev,
         count(CASE WHEN m.kind = 0 THEN 1 END)
           OVER (PARTITION BY m.condition_id ORDER BY m.t DESC, m.kind DESC
                 ROWS UNBOUNDED PRECEDING) AS grp_next
    FROM mix m
), nb AS MATERIALIZED (
  SELECT g.*,
         first_value(g.target)     OVER pv AS target_before,
         first_value(g.his_net)    OVER pv AS net_before,
         first_value(g.ratio)      OVER pv AS ratio_before,
         first_value(g.ledger_net) OVER pv AS ledger_before,
         first_value(g.t)          OVER pv AS t_before,
         first_value(g.target)     OVER nx AS target_after,
         first_value(g.his_net)    OVER nx AS net_after,
         first_value(g.ledger_net) OVER nx AS ledger_after,
         first_value(g.bid)        OVER nx AS bid_after,
         first_value(g.ask)        OVER nx AS ask_after,
         first_value(g.t)          OVER nx AS t_after
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), cls AS MATERIALIZED (
  SELECT nb.condition_id, nb.t AS event_ts, nb.fill_sh AS sh, nb.px,
         nb.sign_before, nb.sign_after, COALESCE(nb.ever_seen, false) AS ever_seen,
         CASE WHEN nb.grp_prev > 0 THEN nb.target_before END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.net_before    END AS net_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.ledger_before END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.target_after  END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.net_after     END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.bid_after     END AS bid_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ask_after     END AS ask_after,
         CASE WHEN nb.grp_next > 0 THEN nb.t_after       END AS at_after
    FROM nb WHERE nb.kind = 1
), lab AS MATERIALIZED (
  SELECT cls.*,
         CASE
           WHEN (target_before IS NULL OR target_after IS NULL) AND NOT ever_seen
             THEN '5 NOT_RECORDED: BETTOR never saw this market'
           WHEN target_before IS NULL OR target_after IS NULL
             THEN '6 NOT_RECORDED: seen, but no tick straddles the event'
           WHEN target_before > 0 AND target_after <= 0
             THEN '1 BETTOR_TARGET_REVERSAL'
           WHEN target_before < 0 AND target_after >= 0
             THEN '1 BETTOR_TARGET_REVERSAL'
           WHEN abs(target_after) < abs(target_before)
             THEN '2 BETTOR_REDUCTION_ONLY'
           WHEN abs(target_after) > abs(target_before)
             THEN '3 BETTOR_ADD_SAME_DIRECTION'
           ELSE '4 BETTOR_HOLD' END AS bettor_required_action
    FROM cls
)
SELECT bettor_required_action, count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(sh * px)::numeric, 0) AS rn1_event_fill_notional,
       round(avg(target_before)::numeric, 1) AS mean_target_before,
       round(avg(target_after)::numeric, 1) AS mean_target_after,
       round(sum(GREATEST(COALESCE(ledger_before, 0) - COALESCE(target_after, 0), 0))::numeric, 0)
         AS shares_the_old_rule_required_BETTOR_to_shed,
       count(*) FILTER (WHERE COALESCE(net_after, 0) < 0 AND COALESCE(target_after, 0) = 0)
         AS collapsed_to_zero_on_a_negative_net,
       round(avg(EXTRACT(epoch FROM (at_after - event_ts)))::numeric, 1)
         AS mean_seconds_event_to_next_tick,
       round(avg(ask_after - bid_after)::numeric, 4) AS mean_pmus_spread_after
  FROM lab GROUP BY 1 ORDER BY 1;


\echo '== 6. TARGET TRANSITION -> ACTUAL BETTOR ORDERS AND FILLS (dollars, not counts) =='
-- Every mirror order on the event's book inside 900 s after the event, with
-- what it executed. This is where the damage becomes money: qty, avg_px,
-- cash, realized, and the slippage against the book we recorded at placement.
--
-- cash_vs_px_residual is |cash_usd| - filled * avg_px. It is NOT certified as
-- a fee: it also absorbs partial-fill booking and rounding.
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
), blk AS MATERIALIZED (
  SELECT condition_id, ts,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) AS ytot,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) AS ntot
    FROM buys GROUP BY 1, 2
), bs AS MATERIALIZED (
  SELECT b.*,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING) AS d_after,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING)
           - (b.ytot - b.ntot) AS d_before
    FROM blk b
), proof AS MATERIALIZED (
  SELECT condition_id,
         CASE WHEN (bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
               AND (bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9)) THEN 1
              WHEN NOT (((bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
                          OR bool_or(d_before + ytot >  1e-9))
                    AND ((bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9))
                          OR bool_or(d_before - ntot < -1e-9))) THEN 2
              ELSE 3 END AS proof_class
    FROM bs GROUP BY 1
), w AS MATERIALIZED (
  SELECT b.*,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_y,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_n
    FROM buys b JOIN proof p ON p.condition_id = b.condition_id
   WHERE p.proof_class = 1
), sg AS MATERIALIZED (
  SELECT w.*,
         CASE WHEN abs(w.cum_y - w.cum_n) > 1e-9 THEN sign(w.cum_y - w.cum_n) END AS nz,
         count(CASE WHEN abs(w.cum_y - w.cum_n) > 1e-9 THEN 1 END)
           OVER (PARTITION BY w.condition_id ORDER BY w.rn ROWS UNBOUNDED PRECEDING) AS grp
    FROM w
), carry AS MATERIALIZED (
  SELECT sg.*,
         first_value(sg.nz) OVER (PARTITION BY sg.condition_id, sg.grp ORDER BY sg.rn)
           AS sign_after
    FROM sg
), ev AS MATERIALIZED (
  SELECT * FROM (
    SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
             AS sign_before
      FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), ob AS MATERIALIZED (
  SELECT o.id, o.book_id, bk.condition_id, o.kind, o.side, o.intent, o.qty,
         o.filled, o.avg_px, o.cash_usd, o.realized, o.state, o.placed_at,
         o.bid_at_place, o.ask_at_place, o.target_at_place, o.ledger_at_place,
         bk.us_market_slug
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), j AS MATERIALIZED (
  SELECT e.condition_id, e.ts AS event_ts, e.sh AS his_fill_size, e.px AS his_fill_px,
         o.id AS order_id, o.kind, o.side, o.qty, o.filled, o.avg_px, o.cash_usd,
         o.realized, o.state, o.placed_at, o.bid_at_place, o.ask_at_place,
         o.target_at_place, o.ledger_at_place
    FROM ev e
    JOIN ob o ON o.condition_id = e.condition_id
            AND o.placed_at >= e.ts
            AND o.placed_at <  e.ts + interval '900 seconds'
)
SELECT COALESCE(side, 'ALL') AS bettor_action_side,
       COALESCE(kind, 'ALL') AS order_kind,
       count(*) AS orders,
       count(DISTINCT condition_id) AS conditions,
       count(*) FILTER (WHERE filled > 0) AS orders_with_a_fill,
       round(sum(filled)::numeric, 0) AS shares_executed,
       round(sum(filled * COALESCE(avg_px, 0))::numeric, 2) AS executed_notional,
       round(sum(cash_usd)::numeric, 2) AS cash_usd,
       round((sum(cash_usd) - sum(filled * COALESCE(avg_px, 0)))::numeric, 2)
         AS cash_vs_px_residual,
       round(sum(realized)::numeric, 2) AS realized,
       -- slippage against the book WE RECORDED at placement, which is the only
       -- PMUS price evidence retained: a SELL_LONG below the recorded bid and a
       -- BUY_LONG above the recorded ask are both adverse
       round(sum(CASE WHEN side = 'SELL_LONG' AND bid_at_place IS NOT NULL
                      THEN (bid_at_place - COALESCE(avg_px, bid_at_place)) * filled
                      WHEN side = 'BUY_LONG' AND ask_at_place IS NOT NULL
                      THEN (COALESCE(avg_px, ask_at_place) - ask_at_place) * filled
                 END)::numeric, 2) AS adverse_vs_recorded_touch,
       count(*) FILTER (WHERE bid_at_place IS NULL AND ask_at_place IS NULL)
         AS orders_with_NO_recorded_touch,
       round(avg(EXTRACT(epoch FROM (placed_at - event_ts)))::numeric, 1)
         AS mean_seconds_event_to_order
  FROM j
 GROUP BY GROUPING SETS ((side, kind), (side), ())
 ORDER BY 1, 2;


\echo '== 7. REACQUISITION: did BETTOR buy back what the flip made it shed? =='
-- The second half of the damage. A liquidation is not costly in itself; a
-- liquidation followed by a repurchase at a worse price is. Per book, the
-- executed SELL_LONG shares and their average price, then the BUY_LONG shares
-- executed AFTERWARDS on the same book and theirs.
WITH ord AS MATERIALIZED (
  SELECT o.book_id, bk.condition_id, bk.us_market_slug, o.side, o.filled,
         o.avg_px, o.placed_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1' AND o.filled > 0 AND o.avg_px IS NOT NULL
), firstsell AS MATERIALIZED (
  SELECT book_id, min(placed_at) AS first_shed_at
    FROM ord WHERE side = 'SELL_LONG' GROUP BY 1
), agg AS MATERIALIZED (
  SELECT o.book_id, o.condition_id, f.first_shed_at,
         sum(o.filled)  FILTER (WHERE o.side = 'SELL_LONG') AS shed_shares,
         sum(o.filled * o.avg_px) FILTER (WHERE o.side = 'SELL_LONG') AS shed_proceeds,
         sum(o.filled)  FILTER (WHERE o.side = 'BUY_LONG'
                                  AND o.placed_at > f.first_shed_at) AS reacq_shares,
         sum(o.filled * o.avg_px) FILTER (WHERE o.side = 'BUY_LONG'
                                  AND o.placed_at > f.first_shed_at) AS reacq_cost
    FROM ord o JOIN firstsell f ON f.book_id = o.book_id
   GROUP BY 1, 2, 3
), r AS MATERIALIZED (
  SELECT a.*,
         COALESCE(a.shed_proceeds, 0) / NULLIF(a.shed_shares, 0)  AS shed_px,
         COALESCE(a.reacq_cost, 0)    / NULLIF(a.reacq_shares, 0) AS reacq_px,
         LEAST(COALESCE(a.shed_shares, 0), COALESCE(a.reacq_shares, 0)) AS round_trip_shares
    FROM agg a
)
SELECT CASE WHEN COALESCE(reacq_shares, 0) = 0 THEN '2 shed and never rebought'
            ELSE '1 SHED THEN REBOUGHT on the same book' END AS pattern,
       count(*) AS books, count(DISTINCT condition_id) AS conditions,
       round(sum(shed_shares)::numeric, 0) AS shares_shed,
       round(sum(shed_proceeds)::numeric, 2) AS shed_proceeds,
       round(sum(reacq_shares)::numeric, 0) AS shares_rebought,
       round(sum(reacq_cost)::numeric, 2) AS reacquisition_cost,
       -- the CASE keeps the population whole: a book that was never rebought has
       -- a NULL reacq_px, and a bare sum() would drop the row from this column
       -- rather than contribute zero -- the family-2 defect, in its own shape
       round(sum(CASE WHEN reacq_px IS NOT NULL AND shed_px IS NOT NULL
                      THEN round_trip_shares * (reacq_px - shed_px) ELSE 0 END)::numeric, 2)
         AS round_trip_cost_rebought_above_shed,
       count(*) FILTER (WHERE reacq_px > shed_px) AS books_rebought_HIGHER,
       count(*) FILTER (WHERE reacq_px < shed_px) AS books_rebought_lower
  FROM r GROUP BY 1 ORDER BY 1;


\echo '== 8. CONTROLS: flipped vs no-flip, stratified so size does not masquerade =='
-- Flip conditions are much larger, so a raw comparison confounds size with
-- architecture. Every row below is a (cohort x size band) cell and the
-- per-RN1-dollar ratios are the comparable quantities, not the totals.
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
), blk AS MATERIALIZED (
  SELECT condition_id, ts,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) AS ytot,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) AS ntot
    FROM buys GROUP BY 1, 2
), bs AS MATERIALIZED (
  SELECT b.*,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING) AS d_after,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING)
           - (b.ytot - b.ntot) AS d_before
    FROM blk b
), proof AS MATERIALIZED (
  SELECT condition_id,
         CASE WHEN (bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
               AND (bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9)) THEN 1
              WHEN NOT (((bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
                          OR bool_or(d_before + ytot >  1e-9))
                    AND ((bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9))
                          OR bool_or(d_before - ntot < -1e-9))) THEN 2
              ELSE 3 END AS proof_class
    FROM bs GROUP BY 1
), econ AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
), bk AS MATERIALIZED (
  SELECT bk.condition_id,
         count(*) AS books,
         max(bk.ratio) AS ratio,
         min(split_part(bk.us_market_slug, '-', 2)) AS league_code,
         sum(bk.gross_buy_usd) AS gross_buy_usd,
         sum(bk.gross_sell_usd) AS gross_shed_usd,
         sum(bk.realized_pnl) AS realized_pnl,
         sum(COALESCE(bk.own_book_pnl, bk.realized_pnl)) AS own_book_pnl
    FROM mirror_books bk WHERE lower(bk.whale) = 'rn1' GROUP BY 1
), act AS MATERIALIZED (
  SELECT b.condition_id,
         count(*) FILTER (WHERE o.side = 'SELL_LONG') AS bettor_shed_actions,
         count(*) FILTER (WHERE o.side = 'SELL_LONG' AND o.filled > 0)
           AS bettor_shed_executions,
         count(*) FILTER (WHERE o.kind IN ('flatten_paired', 'flatten_vanished'))
           AS bettor_flatten_actions,
         sum(o.filled * COALESCE(o.avg_px, 0)) AS executed_notional
    FROM mirror_orders o JOIN mirror_books b ON b.id = o.book_id
   WHERE lower(o.whale) = 'rn1' GROUP BY 1
), k AS MATERIALIZED (
  SELECT p.proof_class, e.condition_id, e.acq_cost,
         COALESCE(bo.books, 0) AS books, bo.ratio, bo.league_code,
         COALESCE(bo.gross_buy_usd, 0) AS gross_buy_usd,
         COALESCE(bo.gross_shed_usd, 0) AS gross_shed_usd,
         COALESCE(bo.own_book_pnl, 0) AS own_book_pnl,
         COALESCE(a.bettor_shed_actions, 0) AS bettor_shed_actions,
         COALESCE(a.bettor_flatten_actions, 0) AS bettor_flatten_actions,
         COALESCE(a.executed_notional, 0) AS executed_notional,
         CASE WHEN e.acq_cost <  1000 THEN '1 under $1k'
              WHEN e.acq_cost < 10000 THEN '2 $1k-10k'
              WHEN e.acq_cost < 50000 THEN '3 $10k-50k'
              ELSE '4 $50k+' END AS size_band
    FROM proof p
    JOIN econ e ON e.condition_id = p.condition_id
    LEFT JOIN bk  bo ON bo.condition_id = p.condition_id
    LEFT JOIN act a  ON a.condition_id = p.condition_id
   WHERE p.proof_class IN (1, 2)
)
SELECT CASE proof_class WHEN 1 THEN '1 PROVABLY_FLIPPED' ELSE '2 PROVABLY_NO_FLIP' END
         AS cohort,
       size_band,
       count(*) AS conditions,
       count(*) FILTER (WHERE books > 0) AS with_a_book,
       round(sum(acq_cost)::numeric, 0) AS rn1_acq_cost,
       round(sum(acq_cost) FILTER (WHERE books > 0)::numeric, 0) AS rn1_acq_cost_bridged,
       round(sum(executed_notional)::numeric, 2) AS bettor_executed_notional,
       -- the comparable quantity: BETTOR turnover per RN1 dollar, on the
       -- bridged part only, because the unbridged part has no turnover BY
       -- CONSTRUCTION and averaging it in would manufacture a difference
       round((sum(executed_notional)
              / NULLIF(sum(acq_cost) FILTER (WHERE books > 0), 0))::numeric, 5)
         AS turnover_per_rn1_dollar_bridged,
       sum(bettor_shed_actions) AS bettor_shed_actions,
       sum(bettor_flatten_actions) AS bettor_flatten_actions,
       round((sum(bettor_shed_actions)::numeric
              / NULLIF(count(*) FILTER (WHERE books > 0), 0)), 3)
         AS shed_actions_per_bridged_condition,
       round(sum(gross_shed_usd)::numeric, 2) AS gross_shed_usd,
       round(sum(own_book_pnl)::numeric, 2) AS bettor_own_book_pnl,
       round(avg(ratio)::numeric, 5) AS mean_copy_ratio
  FROM k GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 9. THE DAMAGE LEDGER: identified / partially identified / unidentified =='
-- The answer's required shape. Nothing is netted across buckets and nothing
-- unmeasured is presented as zero.
--
--   IDENTIFIED_REALIZED_DAMAGE    a BETTOR execution that (a) followed a proven
--       RN1 flip event inside 900 s, (b) had a recorded target transition, and
--       (c) has a realized figure on the order row. Money we can name.
--   PARTIALLY_IDENTIFIED_DAMAGE   an execution that followed a proven event but
--       whose target transition is NOT RECORDED (no straddling shadow tick), so
--       the causal step is inferred rather than evidenced.
--   UNIDENTIFIED_EXECUTION_EFFECT the proven flip events with NO reachable
--       BETTOR evidence at all -- before the mirror existed, on an unmapped
--       market, or with no book open. Priced in RN1 acquisition dollars because
--       that is the only currency available for them, and NOT claimed as loss.
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
), blk AS MATERIALIZED (
  SELECT condition_id, ts,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) AS ytot,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) AS ntot
    FROM buys GROUP BY 1, 2
), bs AS MATERIALIZED (
  SELECT b.*,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING) AS d_after,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING)
           - (b.ytot - b.ntot) AS d_before
    FROM blk b
), proof AS MATERIALIZED (
  SELECT condition_id,
         CASE WHEN (bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
               AND (bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9)) THEN 1
              WHEN NOT (((bool_or(d_after > 1e-9) OR bool_or(d_before > 1e-9))
                          OR bool_or(d_before + ytot >  1e-9))
                    AND ((bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9))
                          OR bool_or(d_before - ntot < -1e-9))) THEN 2
              ELSE 3 END AS proof_class
    FROM bs GROUP BY 1
), w AS MATERIALIZED (
  SELECT b.*,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_y,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cum_n
    FROM buys b JOIN proof p ON p.condition_id = b.condition_id
   WHERE p.proof_class = 1
), sg AS MATERIALIZED (
  SELECT w.*,
         CASE WHEN abs(w.cum_y - w.cum_n) > 1e-9 THEN sign(w.cum_y - w.cum_n) END AS nz,
         count(CASE WHEN abs(w.cum_y - w.cum_n) > 1e-9 THEN 1 END)
           OVER (PARTITION BY w.condition_id ORDER BY w.rn ROWS UNBOUNDED PRECEDING) AS grp
    FROM w
), carry AS MATERIALIZED (
  SELECT sg.*,
         first_value(sg.nz) OVER (PARTITION BY sg.condition_id, sg.grp ORDER BY sg.rn)
           AS sign_after
    FROM sg
), ev AS MATERIALIZED (
  SELECT * FROM (
    SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
             AS sign_before
      FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
), span AS MATERIALIZED (
  -- existence of a straddling tick needs only the per-condition span, so one
  -- grouped pass replaces a correlated lookup per event
  SELECT condition_id, min(at) AS first_tick, max(at) AS last_tick
    FROM mirror_shadow WHERE lower(whale) = 'rn1' GROUP BY 1
), oj AS MATERIALIZED (
  SELECT bk.condition_id, o.placed_at, o.filled, o.avg_px, o.realized
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), ex AS MATERIALIZED (
  SELECT e.condition_id, e.rn, e.ts AS event_ts, e.sh, e.px,
         (s.first_tick IS NOT NULL AND s.first_tick <= e.ts AND s.last_tick > e.ts)
           AS transition_recorded,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS executed_notional,
         COALESCE(sum(o.realized), 0) AS realized,
         count(o.placed_at) FILTER (WHERE o.filled > 0) AS executions
    FROM ev e
    LEFT JOIN span s ON s.condition_id = e.condition_id
    LEFT JOIN oj o ON o.condition_id = e.condition_id
                  AND o.placed_at >= e.ts
                  AND o.placed_at <  e.ts + interval '900 seconds'
   GROUP BY e.condition_id, e.rn, e.ts, e.sh, e.px,
            s.first_tick, s.last_tick
), lbl AS MATERIALIZED (
  SELECT x.*, a.acq_cost,
         CASE WHEN x.executions > 0 AND x.transition_recorded
                THEN '1 IDENTIFIED_REALIZED_DAMAGE'
              WHEN x.executions > 0
                THEN '2 PARTIALLY_IDENTIFIED_DAMAGE (no recorded transition)'
              ELSE '3 UNIDENTIFIED_EXECUTION_EFFECT (no reachable BETTOR evidence)'
         END AS bucket
    FROM ex x JOIN acq a ON a.condition_id = x.condition_id
), dedup AS MATERIALIZED (
  -- a condition can carry events in more than one bucket, so its acquisition
  -- cost is counted once PER BUCKET via a first-row marker. sum(DISTINCT
  -- acq_cost) would silently collapse two different conditions that happen to
  -- share a cost, which is a population error dressed as a total.
  SELECT lbl.*,
         row_number() OVER (PARTITION BY bucket, condition_id) AS nth_in_bucket
    FROM lbl
)
SELECT bucket,
       count(*) AS rn1_proven_flip_events,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(sh * px)::numeric, 0) AS rn1_event_fill_notional,
       round(sum(executed_notional)::numeric, 2) AS bettor_executed_notional,
       round(sum(realized)::numeric, 2) AS bettor_realized_on_those_orders,
       sum(executions) AS bettor_executions,
       -- for bucket 3 this is the RN1 capital the question cannot reach;
       -- it is NOT a loss figure and must never be quoted as one
       round(sum(acq_cost) FILTER (WHERE nth_in_bucket = 1)::numeric, 0)
         AS rn1_acq_cost_of_those_conditions
  FROM dedup GROUP BY 1 ORDER BY 1;
