-- ============================================================================
-- THE CAUSAL LEDGER: REACH -> COMMAND -> EXECUTION -> DAMAGE
-- (2026-09-11, read-only.) Owner-specified restructure of run 73.
-- mirror_live = false throughout; nothing here writes.
--
-- ---------------------------------------------------------------------------
-- WHAT RUN 73 GOT WRONG AND THIS FILE FIXES.
--
-- 1 ONE BUCKET WAS DOING TWO JOBS. Run 73's UNIDENTIFIED_EXECUTION_EFFECT held
--   every event not fully priced, so "BETTOR was not running yet" and "BETTOR
--   acted and we cannot price it" sat in the same 95.31%. Those are different
--   facts: the first is a COVERAGE LIMITATION, the second is UNRESOLVED DAMAGE.
--   The universe is now split six ways, mutually exclusive and exhaustive:
--       OUTSIDE_BETTOR_CAUSAL_REACH
--       IN_REACH_NO_HARMFUL_COMMAND
--       IN_REACH_HARMFUL_COMMAND_NO_EXECUTION
--       IDENTIFIED_REALIZED_DAMAGE
--       PARTIALLY_IDENTIFIED_DAMAGE
--       EXECUTION_EFFECT_UNIDENTIFIED
--
-- 2 LIQUIDATION WAS INFERRED FROM mirror_orders.side. IT CANNOT BE. Migration
--   050 is explicit: side keeps the PLAN spelling (BUY_LONG / SELL_LONG),
--   SIGN-BLIND, and "the sign of the wire rides in `intent`". 825 of run 73's
--   1,535 SELL_LONG rows were kind='increase' -- increasing a SHORT, which adds
--   exposure rather than shedding it. Statements 6 and 7 of run 73 are
--   retracted. Four fields are now kept apart and never conflated:
--       plan_side                    mirror_orders.side, sign-blind plan space
--       wire_intent                  mirror_orders.intent, the signed wire
--       recorded_target_transition   production's own target, before -> after
--       actual_executed_transition   position before -> after the fills
--   Economic direction comes from wire_intent alone:
--       BUY_LONG +filled | SELL_LONG -filled | BUY_SHORT -filled | SELL_SHORT +filled
--
-- ---------------------------------------------------------------------------
-- THREE DENOMINATORS, because three different questions are being asked.
--
--   #1 RN1 RESEARCH UNIVERSE      every proof-certified flip event.
--      Percentages against it answer: how much of RN1's behaviour did the
--      architecture ever stand a chance of touching?
--   #2 BETTOR CAUSAL-REACH UNIVERSE  events where production held enough
--      contemporaneous state to command a target transition -- a tick at or
--      before the event AND a tick after it, so a transition exists to read.
--   #3 BETTOR HARMFUL-COMMAND UNIVERSE  causal-reach events whose RECORDED
--      transition instructed an economically relevant flatten or reversal:
--      class in {LONG_TO_FLAT, SHORT_TO_FLAT, LONG_TO_SHORT, SHORT_TO_LONG}
--      AND a nonzero position existed to act on.
--
-- DAMAGE-IDENTIFICATION PERCENTAGES USE #3. "How much of the
-- architecture-induced turnover can we price?" is a question about commands
-- that were actually issued, not about RN1 events that never reached us.
-- Coverage percentages use #1 and answer a different question. Both are
-- printed on every row of statement 1 so neither can be quoted as the other.
--
-- ---------------------------------------------------------------------------
-- THE BOUNDARY IS AN OBSERVED CAPABILITY BOUNDARY, NOT A DEPLOYMENT ONE.
--
-- Run 74 read the first negative target in mirror_shadow as if it marked a
-- globally atomic P1 -> P2 deployment. IT DOES NOT. It marks the first moment
-- short capability was OBSERVED in retained data; the deployment that enabled
-- it may have been earlier, later on some workers, or staged. No
-- deployment-version metadata exists in the database to settle it -- there is
-- no version column on mirror_shadow, and service_heartbeats carries no build
-- identifier -- so the timestamp is named for what it is:
--
--     FIRST_OBSERVED_NEGATIVE_TARGET_AT   (the OBSERVED_SHORT_CAPABLE_BOUNDARY)
--
-- PER-EVENT EVIDENCE IS PREFERRED OVER THE GLOBAL CUTOFF wherever it exists,
-- and three kinds do:
--     a NEGATIVE TARGET recorded on THIS condition at or before the event
--     a SHORT-INTENT ORDER on THIS condition's book at or before the event
--     flow_base populated on THIS book (E12 sizing, witnessed per book)
-- The first two are direct witnesses that short capability was live for this
-- market, which is stronger than any fleet-wide timestamp.
--
-- SO A ZERO-COLLAPSE EVENT IS LABELLED IN THREE WAYS, and none of them says
-- "other cause" -- the applicable worker version is not known, and inventing a
-- cause for it would be worse than naming the evidence:
--     1 CONSISTENT_WITH_P1_LONG_ONLY
--         no short capability observed anywhere yet
--     2 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (fleet-wide only)
--         after the global boundary, but nothing witnessed on this condition
--     3 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (this condition)
--         a negative target or short-intent order witnessed on this market
-- Only class 1 carries the P1 damage unit, and only class 1 is counterfactualed.
--
-- THE P1 DAMAGE UNIT, unchanged in substance:
--     an existing POSITIVE BETTOR position
--     -> the recorded target collapses to ZERO while RN1's signed state is
--        negative, in a window where nothing shows short capability
--     -> actual liquidation
--     -> optionally, later reacquisition.
--
-- ---------------------------------------------------------------------------
-- TWO CORRECTIONS TO RUN 74, both mine.
--
-- 1 REACH NOW REQUIRES A RECORDED TARGET, NOT MERELY A TICK. Run 74 printed
--   1,509 of 3,183 reach events as "NULL (no straddling tick)" when the ticks
--   demonstrably existed -- the reach test guaranteed them. What was NULL was
--   mirror_shadow.target itself. An event whose straddling ticks carry no
--   target cannot yield a commanded transition, so it now sits OUTSIDE reach
--   under its own reason rather than inflating the reach denominator with
--   events that could never be classified.
--
-- 2 THE RETRACTION RATIONALE FOR RUN 73 WAS WRONG IN ITS DIRECTION. I wrote
--   that SELL_LONG / kind='increase' orders "add exposure rather than shed
--   it". Increasing a short moves long-axis exposure DOWN, not up: statement 4
--   measures signed_long_delta = -228,290 across those orders. The retraction
--   stands for a better reason -- those orders OPEN A SHORT (following RN1 into
--   a new direction), they do not CLOSE AN EXISTING LONG (being forced out of
--   one). Statement 4 now prints that economic meaning per wire_intent so the
--   distinction cannot be lost again.
--
-- ---------------------------------------------------------------------------
-- THE ATTRIBUTION WINDOW IS NOW A MEASURED SENSITIVITY, NOT AN ASSUMPTION.
--
-- Run 74 attributed executions within 900 s of an event and found ZERO
-- liquidations against 79 P1-consistent forced-flatten commands. A zero is
-- exactly where a chosen window must be tested rather than trusted: a commanded
-- flatten may simply have executed later. Statements 7 and 8 therefore run the
-- whole harmful-command universe and the P1-consistent population at 900 s,
-- 1 h, 6 h, 24 h and 30 d, and report how the classification moves.
--
-- ---------------------------------------------------------------------------
-- THE COUNTERFACTUAL, and its horizon stated rather than implied.
--
-- NO_P1_FORCED_FLATTEN_COUNTERFACTUAL keeps the pre-collapse position instead
-- of flattening solely because P1 cannot represent a negative target. With S
-- shares liquidated at p_sell, R rebought at p_buy, and a horizon price p_H:
--
--     actual        = S*p_sell - R*p_buy + (P - S + R)*p_H
--     counterfactual= P*p_H
--     damage        = counterfactual - actual = S*(p_H - p_sell) + R*(p_buy - p_H)
--
-- Statement 9 emits ONE ROW PER WINDOW x HORIZON and never blends them:
--     1 subsequent reacquisition   p_H = p_buy
--     2 last observable mark       p_H = the last mirror_shadow mark
--     3 settlement                 NOT COMPUTED -- no per-share payout is
--       retained per book that can be attributed to a specific episode, and
--       inventing one would be worse than leaving the row empty.
--
-- ---------------------------------------------------------------------------
-- THE BUSINESS BRIDGE, with an explicit refusal condition.
--
-- Statement 10 reports five candidate reconstructions of
-- TOTAL_OBSERVED_BETTOR_LOSS over the same BETTOR window, each with its row
-- and NULL counts, beside IDENTIFIED_P1_FORCED_FLATTEN_DAMAGE and
-- IDENTIFIED_OTHER_REVERSAL_DAMAGE. THE RATIO IS PRODUCED ONLY IF THE
-- CANDIDATES AGREE WITHIN 5%. They are known to be at risk of disagreeing: E3
-- established that settled_pnl is the venue's whole-position figure and
-- already contains realized_pnl, so adding them double counts. A ratio built
-- on a denominator that cannot be reconstructed consistently would be the most
-- quotable and least defensible number in this whole study, so the query
-- refuses to emit it and says why instead.
--
-- ---------------------------------------------------------------------------
-- ACCEPTANCE. Statement 11 runs all seven checks, and EVERY zero-violation
-- test is printed beside a NON-EMPTY WITNESS COUNT. A gate whose population is
-- empty has tested nothing; that vacuity has already cost this work four
-- times, so a zero violation count next to a zero witness count is to be read
-- as NOT TESTED, never as PASS.
--
-- Read-only: eleven SELECTs.
-- ============================================================================


\echo '== 1. THE CAUSAL LEDGER: reach -> command -> execution -> damage =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), lc AS MATERIALIZED (
  SELECT e.*,
         CASE WHEN e.reach <> '1 IN_REACH' THEN '1 OUTSIDE_BETTOR_CAUSAL_REACH'
              WHEN NOT e.harmful_command THEN '2 IN_REACH_NO_HARMFUL_COMMAND'
              WHEN e.exec_shares = 0 THEN '3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION'
              WHEN e.exec_priced_shares < e.exec_shares
                THEN '6 EXECUTION_EFFECT_UNIDENTIFIED'
              WHEN e.exec_signed_delta * e.commanded_direction
                   >= 0.99 * e.commanded_change
                THEN '4 IDENTIFIED_REALIZED_DAMAGE'
              ELSE '5 PARTIALLY_IDENTIFIED_DAMAGE' END AS causal_class
    FROM exj e
)
SELECT causal_class,
       count(*) AS events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(fsh * fpx)::numeric, 0) AS rn1_event_fill_notional,
       sum(count(*)) OVER () AS universe_1_rn1_research,
       sum(count(*) FILTER (WHERE reach = '1 IN_REACH')) OVER ()
         AS universe_2_causal_reach,
       sum(count(*) FILTER (WHERE harmful_command)) OVER ()
         AS universe_3_harmful_command,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2)
         AS pct_of_universe_1,
       round((100.0 * count(*) FILTER (WHERE reach = '1 IN_REACH')
              / NULLIF(sum(count(*) FILTER (WHERE reach = '1 IN_REACH'))
                       OVER (), 0))::numeric, 2) AS pct_of_universe_2,
       round((100.0 * count(*) FILTER (WHERE harmful_command)
              / NULLIF(sum(count(*) FILTER (WHERE harmful_command))
                       OVER (), 0))::numeric, 2) AS pct_of_universe_3,
       round(sum(commanded_shed_shares)::numeric, 0) AS commanded_shed_shares,
       round(sum(exec_shares)::numeric, 0) AS executed_shares,
       round(sum(exec_notional)::numeric, 2) AS executed_notional,
       round(sum(exec_realized)::numeric, 2) AS executed_realized
  FROM lc GROUP BY 1 ORDER BY 1;

\echo '== 2. OUTSIDE_BETTOR_CAUSAL_REACH: a coverage limitation, named and priced =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), lc AS MATERIALIZED (
  SELECT e.*,
         CASE WHEN e.reach <> '1 IN_REACH' THEN '1 OUTSIDE_BETTOR_CAUSAL_REACH'
              WHEN NOT e.harmful_command THEN '2 IN_REACH_NO_HARMFUL_COMMAND'
              WHEN e.exec_shares = 0 THEN '3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION'
              WHEN e.exec_priced_shares < e.exec_shares
                THEN '6 EXECUTION_EFFECT_UNIDENTIFIED'
              WHEN e.exec_signed_delta * e.commanded_direction
                   >= 0.99 * e.commanded_change
                THEN '4 IDENTIFIED_REALIZED_DAMAGE'
              ELSE '5 PARTIALLY_IDENTIFIED_DAMAGE' END AS causal_class
    FROM exj e
), d2 AS MATERIALIZED (
  SELECT lc.*, a.acq_cost,
         row_number() OVER (PARTITION BY lc.reach, lc.condition_id) AS nth
    FROM lc JOIN acq a ON a.condition_id = lc.condition_id
   WHERE lc.reach <> '1 IN_REACH'
)
SELECT reach AS outside_reach_reason,
       count(*) AS events,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2)
         AS pct_of_outside_reach,
       round(sum(fsh * fpx)::numeric, 0) AS rn1_event_fill_notional,
       round(sum(acq_cost) FILTER (WHERE nth = 1)::numeric, 0)
         AS rn1_acq_cost_of_those_conditions,
       to_char(min(event_ts), 'MM-DD HH24:MI') AS first_event,
       to_char(max(event_ts), 'MM-DD HH24:MI') AS last_event
  FROM d2 GROUP BY 1 ORDER BY 1;

\echo '== 3. RECORDED_TARGET_TRANSITION, economic classes, on the causal-reach universe =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), lc AS MATERIALIZED (
  SELECT e.*,
         CASE WHEN e.reach <> '1 IN_REACH' THEN '1 OUTSIDE_BETTOR_CAUSAL_REACH'
              WHEN NOT e.harmful_command THEN '2 IN_REACH_NO_HARMFUL_COMMAND'
              WHEN e.exec_shares = 0 THEN '3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION'
              WHEN e.exec_priced_shares < e.exec_shares
                THEN '6 EXECUTION_EFFECT_UNIDENTIFIED'
              WHEN e.exec_signed_delta * e.commanded_direction
                   >= 0.99 * e.commanded_change
                THEN '4 IDENTIFIED_REALIZED_DAMAGE'
              ELSE '5 PARTIALLY_IDENTIFIED_DAMAGE' END AS causal_class
    FROM exj e
)
SELECT recorded_target_transition,
       count(*) AS events,
       count(DISTINCT condition_id) AS conditions,
       count(*) FILTER (WHERE harmful_command) AS harmful_command_events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_of_reach,
       round(avg(ledger_before)::numeric, 1) AS mean_pre_position,
       round(sum(commanded_shed_shares)::numeric, 0) AS commanded_shed_shares,
       round(sum(commanded_change)::numeric, 0) AS commanded_change_shares,
       round(sum(exec_signed_delta * commanded_direction)::numeric, 0)
         AS executed_progress_toward_target,
       round(sum(exec_shares)::numeric, 0) AS executed_shares,
       round(sum(exec_notional)::numeric, 2) AS executed_notional,
       count(*) FILTER (WHERE executions > 0) AS events_with_an_execution
  FROM lc WHERE reach = '1 IN_REACH' GROUP BY 1 ORDER BY 1;

\echo '== 4. ACTION SEMANTICS: plan_side, wire_intent and kind kept APART =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), oj AS MATERIALIZED (
  SELECT o.plan_side, o.wire_intent, o.kind,
         count(*) AS orders,
         count(*) FILTER (WHERE o.filled > 0) AS orders_with_a_fill,
         sum(o.filled) AS filled_shares,
         sum(o.signed_long_delta) AS signed_long_delta,
         sum(o.filled * COALESCE(o.avg_px, 0)) AS notional,
         sum(o.cash_usd) AS cash_usd,
         sum(o.realized) AS realized
    FROM ords o GROUP BY 1, 2, 3
)
SELECT plan_side, wire_intent, kind, orders, orders_with_a_fill,
       round(filled_shares::numeric, 0) AS filled_shares,
       round(signed_long_delta::numeric, 0) AS signed_long_delta,
       CASE WHEN signed_long_delta > 0 THEN 'ADDS long-axis exposure'
            WHEN signed_long_delta < 0 THEN 'REMOVES long-axis exposure'
            ELSE 'net zero' END AS long_axis_direction,
       CASE WHEN wire_intent = 'ORDER_INTENT_SELL_LONG'
              THEN 'CLOSES an existing long'
            WHEN wire_intent = 'ORDER_INTENT_BUY_SHORT'
              THEN 'OPENS or adds a short (NOT a long liquidation)'
            WHEN wire_intent = 'ORDER_INTENT_SELL_SHORT'
              THEN 'COVERS an existing short'
            ELSE 'OPENS or adds a long' END AS economic_meaning,
       round(notional::numeric, 2) AS px_notional,
       round(cash_usd::numeric, 2) AS cash_usd,
       round(realized::numeric, 2) AS realized
  FROM oj ORDER BY plan_side, wire_intent, kind;

\echo '== 5. RECORDED command vs ACTUAL executed transition (both retained, never inferred) =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), lc AS MATERIALIZED (
  SELECT e.*,
         CASE WHEN e.reach <> '1 IN_REACH' THEN '1 OUTSIDE_BETTOR_CAUSAL_REACH'
              WHEN NOT e.harmful_command THEN '2 IN_REACH_NO_HARMFUL_COMMAND'
              WHEN e.exec_shares = 0 THEN '3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION'
              WHEN e.exec_priced_shares < e.exec_shares
                THEN '6 EXECUTION_EFFECT_UNIDENTIFIED'
              WHEN e.exec_signed_delta * e.commanded_direction
                   >= 0.99 * e.commanded_change
                THEN '4 IDENTIFIED_REALIZED_DAMAGE'
              ELSE '5 PARTIALLY_IDENTIFIED_DAMAGE' END AS causal_class
    FROM exj e
), ax AS MATERIALIZED (
  SELECT lc.*,
         (COALESCE(lc.ledger_before, 0) + lc.exec_signed_delta) AS pos_after_exec
    FROM lc WHERE lc.reach = '1 IN_REACH' AND lc.executions > 0
), ay AS MATERIALIZED (
  SELECT ax.*,
         CASE WHEN COALESCE(ax.ledger_before, 0) = ax.pos_after_exec THEN 'HOLD'
              WHEN COALESCE(ax.ledger_before, 0) > 0 AND ax.pos_after_exec = 0
                THEN 'LONG_TO_FLAT'
              WHEN COALESCE(ax.ledger_before, 0) < 0 AND ax.pos_after_exec = 0
                THEN 'SHORT_TO_FLAT'
              WHEN COALESCE(ax.ledger_before, 0) > 0 AND ax.pos_after_exec < 0
                THEN 'LONG_TO_SHORT'
              WHEN COALESCE(ax.ledger_before, 0) < 0 AND ax.pos_after_exec > 0
                THEN 'SHORT_TO_LONG'
              WHEN ax.pos_after_exec > COALESCE(ax.ledger_before, 0)
                   AND ax.pos_after_exec > 0
                   AND COALESCE(ax.ledger_before, 0) >= 0 THEN 'INCREASE_LONG'
              WHEN COALESCE(ax.ledger_before, 0) > 0
                   AND ax.pos_after_exec < COALESCE(ax.ledger_before, 0)
                   AND ax.pos_after_exec > 0 THEN 'REDUCE_LONG'
              WHEN ax.pos_after_exec < COALESCE(ax.ledger_before, 0)
                   AND ax.pos_after_exec < 0
                   AND COALESCE(ax.ledger_before, 0) <= 0 THEN 'INCREASE_SHORT'
              WHEN COALESCE(ax.ledger_before, 0) < 0
                   AND ax.pos_after_exec > COALESCE(ax.ledger_before, 0)
                   AND ax.pos_after_exec < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS actual_executed_transition
    FROM ax
)
SELECT recorded_target_transition, actual_executed_transition,
       count(*) AS events, count(DISTINCT condition_id) AS conditions,
       count(*) FILTER (WHERE harmful_command) AS harmful_command_events,
       round(sum(exec_shares)::numeric, 0) AS executed_shares,
       round(sum(exec_notional)::numeric, 2) AS executed_notional,
       round(sum(exec_realized)::numeric, 2) AS executed_realized
  FROM ay GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;

\echo '== 6. THE OBSERVED SHORT-CAPABLE BOUNDARY, and per-event capability evidence =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), p1 AS MATERIALIZED (
  SELECT e.*,
         (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
          AND COALESCE(e.net_after, 0) < 0) AS zero_collapse_shape,
         CASE
           WHEN NOT (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
                     AND COALESCE(e.net_after, 0) < 0) THEN NULL
           WHEN e.short_capability_observed_on_this_condition
             THEN '3 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (this condition)'
           WHEN NOT e.before_short_capability_observed_anywhere
             THEN '2 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (fleet-wide only)'
           ELSE '1 CONSISTENT_WITH_P1_LONG_ONLY'
         END AS regime_label
    FROM exj e
)
SELECT to_char((SELECT t0 FROM win), 'YYYY-MM-DD HH24:MI') AS first_shadow_tick,
       to_char((SELECT short_cap_at FROM win), 'YYYY-MM-DD HH24:MI')
         AS first_observed_negative_target_at,
       to_char((SELECT min(opened_at) FROM mirror_books WHERE lower(whale) = 'rn1'),
               'YYYY-MM-DD HH24:MI') AS first_book_opened,
       count(*) AS proven_flip_events,
       count(*) FILTER (WHERE short_capability_observed_on_this_condition)
         AS events_with_short_capability_on_this_condition,
       count(*) FILTER (WHERE e12_flow_sizing_observed_on_this_book)
         AS events_with_e12_flow_sizing_on_this_book,
       count(*) FILTER (WHERE zero_collapse_shape) AS zero_collapse_shape_events,
       count(*) FILTER (WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY')
         AS consistent_with_p1_long_only,
       count(DISTINCT condition_id)
         FILTER (WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY')
         AS consistent_with_p1_conditions,
       count(*) FILTER (WHERE regime_label LIKE '2 %')
         AS zero_collapse_after_capability_FLEET_WIDE_only,
       count(*) FILTER (WHERE regime_label LIKE '3 %')
         AS zero_collapse_after_capability_ON_THIS_CONDITION,
       count(*) FILTER (WHERE COALESCE(ledger_before, 0) > 0)
         AS shape_part_pre_position_gt_0,
       count(*) FILTER (WHERE target_after = 0) AS shape_part_target_after_0,
       count(*) FILTER (WHERE COALESCE(net_after, 0) < 0)
         AS shape_part_rn1_net_negative
  FROM p1;

\echo '== 7. ATTRIBUTION-WINDOW SENSITIVITY on the whole harmful-command universe =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), lc AS MATERIALIZED (
  SELECT e.*,
         CASE WHEN e.reach <> '1 IN_REACH' THEN '1 OUTSIDE_BETTOR_CAUSAL_REACH'
              WHEN NOT e.harmful_command THEN '2 IN_REACH_NO_HARMFUL_COMMAND'
              WHEN e.exec_shares = 0 THEN '3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION'
              WHEN e.exec_priced_shares < e.exec_shares
                THEN '6 EXECUTION_EFFECT_UNIDENTIFIED'
              WHEN e.exec_signed_delta * e.commanded_direction
                   >= 0.99 * e.commanded_change
                THEN '4 IDENTIFIED_REALIZED_DAMAGE'
              ELSE '5 PARTIALLY_IDENTIFIED_DAMAGE' END AS causal_class
    FROM exj e
), wins AS MATERIALIZED (
  SELECT * FROM (VALUES (900, '1 900 s (the run-74 window)'),
                        (3600, '2 1 h'),
                        (21600, '3 6 h'),
                        (86400, '4 24 h'),
                        (2592000, '5 30 d (effectively unbounded)'))
         v(secs, label)
), hw AS MATERIALIZED (
  SELECT c.fill_id, c.condition_id, c.commanded_change, c.commanded_direction,
         w.label AS attribution_window,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized
    FROM lc c CROSS JOIN wins w
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + make_interval(secs => w.secs)
                    AND o.filled > 0
   WHERE c.harmful_command
   GROUP BY 1, 2, 3, 4, 5
)
SELECT attribution_window,
       count(*) AS harmful_command_events,
       count(*) FILTER (WHERE exec_shares = 0) AS no_execution,
       count(*) FILTER (WHERE exec_shares > 0) AS with_an_execution,
       round((100.0 * count(*) FILTER (WHERE exec_shares = 0)
              / count(*))::numeric, 2) AS pct_no_execution,
       count(*) FILTER (WHERE exec_signed_delta * commanded_direction
                              >= 0.99 * commanded_change) AS command_fully_executed,
       round(sum(exec_shares)::numeric, 0) AS executed_shares,
       round(sum(exec_notional)::numeric, 2) AS executed_notional,
       round(sum(exec_realized)::numeric, 2) AS executed_realized
  FROM hw GROUP BY 1 ORDER BY 1;

\echo '== 8. CONSISTENT_WITH_P1_LONG_ONLY forced-flatten detail, BY ATTRIBUTION WINDOW =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), p1 AS MATERIALIZED (
  SELECT e.*,
         (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
          AND COALESCE(e.net_after, 0) < 0) AS zero_collapse_shape,
         CASE
           WHEN NOT (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
                     AND COALESCE(e.net_after, 0) < 0) THEN NULL
           WHEN e.short_capability_observed_on_this_condition
             THEN '3 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (this condition)'
           WHEN NOT e.before_short_capability_observed_anywhere
             THEN '2 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (fleet-wide only)'
           ELSE '1 CONSISTENT_WITH_P1_LONG_ONLY'
         END AS regime_label
    FROM exj e
), wins AS MATERIALIZED (
  SELECT * FROM (VALUES (900, '1 900 s (the run-74 window)'),
                        (3600, '2 1 h'),
                        (21600, '3 6 h'),
                        (86400, '4 24 h'),
                        (2592000, '5 30 d (effectively unbounded)'))
         v(secs, label)
), p1sel AS MATERIALIZED (
  SELECT * FROM p1 WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY'
), liq AS MATERIALIZED (
  SELECT p.condition_id, p.fill_id, p.event_ts, w.label AS attribution_window,
         p.ledger_before AS pre_position, p.commanded_shed_shares,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0
                           THEN -o.signed_long_delta ELSE 0 END), 0) AS liq_shares,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0
                           THEN -o.signed_long_delta * o.avg_px ELSE 0 END), 0)
           AS liq_proceeds,
         max(o.placed_at) FILTER (WHERE o.signed_long_delta < 0) AS last_liq_at
    FROM p1sel p CROSS JOIN wins w
    LEFT JOIN ords o ON o.condition_id = p.condition_id
                    AND o.placed_at >= p.event_ts
                    AND o.placed_at <  p.event_ts + make_interval(secs => w.secs)
                    AND o.filled > 0 AND o.avg_px IS NOT NULL
   GROUP BY 1, 2, 3, 4, 5, 6
), rq AS MATERIALIZED (
  SELECT l.*,
         COALESCE(sum(o.signed_long_delta), 0) AS reacq_shares,
         COALESCE(sum(o.signed_long_delta * o.avg_px), 0) AS reacq_cost
    FROM liq l
    LEFT JOIN ords o ON o.condition_id = l.condition_id
                    AND l.last_liq_at IS NOT NULL
                    AND o.placed_at >  l.last_liq_at
                    AND o.placed_at <= l.last_liq_at + interval '24 hours'
                    AND o.signed_long_delta > 0 AND o.avg_px IS NOT NULL
   GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
), lastmark AS MATERIALIZED (
  SELECT condition_id, mark FROM (
    SELECT condition_id, mark,
           row_number() OVER (PARTITION BY condition_id ORDER BY at DESC) AS rn
      FROM sh WHERE mark IS NOT NULL) z WHERE rn = 1
), cf AS MATERIALIZED (
  SELECT r.*, lm.mark AS last_mark,
         r.liq_proceeds / NULLIF(r.liq_shares, 0)   AS p_sell,
         r.reacq_cost   / NULLIF(r.reacq_shares, 0) AS p_buy
    FROM rq r LEFT JOIN lastmark lm ON lm.condition_id = r.condition_id
)
SELECT attribution_window,
       count(*) AS p1_consistent_events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(pre_position)::numeric, 0) AS bettor_pre_position_shares,
       round(sum(commanded_shed_shares)::numeric, 0) AS commanded_shares_to_flatten,
       round(sum(liq_shares)::numeric, 0) AS actual_shares_liquidated,
       round(sum(liq_proceeds)::numeric, 2) AS liquidation_proceeds,
       count(*) FILTER (WHERE liq_shares > 0) AS events_with_a_liquidation,
       round(sum(reacq_shares)::numeric, 0) AS later_reacquired_shares,
       round(sum(reacq_cost)::numeric, 2) AS later_reacquisition_cost,
       count(*) FILTER (WHERE reacq_shares > 0) AS events_with_a_reacquisition,
       round(sum(CASE WHEN reacq_shares > 0 AND liq_shares > 0
                      THEN LEAST(liq_shares, reacq_shares)
                           * (COALESCE(p_buy, 0) - COALESCE(p_sell, 0))
                      ELSE 0 END)::numeric, 2)
         AS realized_round_trip_loss_where_identifiable
  FROM cf GROUP BY 1 ORDER BY 1;

\echo '== 9. NO_P1_FORCED_FLATTEN_COUNTERFACTUAL, one row per WINDOW x HORIZON =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), p1 AS MATERIALIZED (
  SELECT e.*,
         (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
          AND COALESCE(e.net_after, 0) < 0) AS zero_collapse_shape,
         CASE
           WHEN NOT (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
                     AND COALESCE(e.net_after, 0) < 0) THEN NULL
           WHEN e.short_capability_observed_on_this_condition
             THEN '3 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (this condition)'
           WHEN NOT e.before_short_capability_observed_anywhere
             THEN '2 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (fleet-wide only)'
           ELSE '1 CONSISTENT_WITH_P1_LONG_ONLY'
         END AS regime_label
    FROM exj e
), wins AS MATERIALIZED (
  SELECT * FROM (VALUES (900, '1 900 s (the run-74 window)'),
                        (3600, '2 1 h'),
                        (21600, '3 6 h'),
                        (86400, '4 24 h'),
                        (2592000, '5 30 d (effectively unbounded)'))
         v(secs, label)
), p1sel AS MATERIALIZED (
  SELECT * FROM p1 WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY'
), liq AS MATERIALIZED (
  SELECT p.condition_id, p.fill_id, p.event_ts, w.label AS attribution_window,
         p.ledger_before AS pre_position, p.commanded_shed_shares,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0
                           THEN -o.signed_long_delta ELSE 0 END), 0) AS liq_shares,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0
                           THEN -o.signed_long_delta * o.avg_px ELSE 0 END), 0)
           AS liq_proceeds,
         max(o.placed_at) FILTER (WHERE o.signed_long_delta < 0) AS last_liq_at
    FROM p1sel p CROSS JOIN wins w
    LEFT JOIN ords o ON o.condition_id = p.condition_id
                    AND o.placed_at >= p.event_ts
                    AND o.placed_at <  p.event_ts + make_interval(secs => w.secs)
                    AND o.filled > 0 AND o.avg_px IS NOT NULL
   GROUP BY 1, 2, 3, 4, 5, 6
), rq AS MATERIALIZED (
  SELECT l.*,
         COALESCE(sum(o.signed_long_delta), 0) AS reacq_shares,
         COALESCE(sum(o.signed_long_delta * o.avg_px), 0) AS reacq_cost
    FROM liq l
    LEFT JOIN ords o ON o.condition_id = l.condition_id
                    AND l.last_liq_at IS NOT NULL
                    AND o.placed_at >  l.last_liq_at
                    AND o.placed_at <= l.last_liq_at + interval '24 hours'
                    AND o.signed_long_delta > 0 AND o.avg_px IS NOT NULL
   GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
), lastmark AS MATERIALIZED (
  SELECT condition_id, mark FROM (
    SELECT condition_id, mark,
           row_number() OVER (PARTITION BY condition_id ORDER BY at DESC) AS rn
      FROM sh WHERE mark IS NOT NULL) z WHERE rn = 1
), cf AS MATERIALIZED (
  SELECT r.*, lm.mark AS last_mark,
         r.liq_proceeds / NULLIF(r.liq_shares, 0)   AS p_sell,
         r.reacq_cost   / NULLIF(r.reacq_shares, 0) AS p_buy
    FROM rq r LEFT JOIN lastmark lm ON lm.condition_id = r.condition_id
), hz AS MATERIALIZED (
  SELECT '1 horizon = SUBSEQUENT REACQUISITION' AS valuation_horizon,
         cf.*, cf.p_buy AS p_h
    FROM cf WHERE cf.reacq_shares > 0 AND cf.liq_shares > 0 AND cf.p_buy IS NOT NULL
  UNION ALL
  SELECT '2 horizon = LAST OBSERVABLE MARK', cf.*, cf.last_mark
    FROM cf WHERE cf.liq_shares > 0 AND cf.last_mark IS NOT NULL
  UNION ALL
  SELECT '3 horizon = SETTLEMENT (NOT COMPUTED: no per-share payout retained)',
         cf.*, NULL::float8
    FROM cf WHERE cf.liq_shares > 0
)
SELECT attribution_window, valuation_horizon,
       count(*) AS events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(liq_shares)::numeric, 0) AS shares_liquidated,
       round(sum(reacq_shares)::numeric, 0) AS shares_rebought,
       round(sum(liq_shares * p_sell)::numeric, 2) AS actual_path_liq_proceeds,
       round(sum(liq_shares * (p_h - p_sell)
                 + reacq_shares * (COALESCE(p_buy, p_h) - p_h))::numeric, 2)
         AS counterfactual_minus_actual_DAMAGE,
       round(avg(p_sell)::numeric, 4) AS mean_p_sell,
       round(avg(p_h)::numeric, 4) AS mean_p_horizon
  FROM hz GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 10. BRIDGE TO THE BUSINESS PROBLEM: total observed BETTOR loss, and the ratio =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), p1 AS MATERIALIZED (
  SELECT e.*,
         (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
          AND COALESCE(e.net_after, 0) < 0) AS zero_collapse_shape,
         CASE
           WHEN NOT (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
                     AND COALESCE(e.net_after, 0) < 0) THEN NULL
           WHEN e.short_capability_observed_on_this_condition
             THEN '3 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (this condition)'
           WHEN NOT e.before_short_capability_observed_anywhere
             THEN '2 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (fleet-wide only)'
           ELSE '1 CONSISTENT_WITH_P1_LONG_ONLY'
         END AS regime_label
    FROM exj e
), wins AS MATERIALIZED (
  SELECT * FROM (VALUES (900, '1 900 s (the run-74 window)'),
                        (3600, '2 1 h'),
                        (21600, '3 6 h'),
                        (86400, '4 24 h'),
                        (2592000, '5 30 d (effectively unbounded)'))
         v(secs, label)
), p1sel AS MATERIALIZED (
  SELECT * FROM p1 WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY'
), liq AS MATERIALIZED (
  SELECT p.condition_id, p.fill_id, p.event_ts, w.label AS attribution_window,
         p.ledger_before AS pre_position, p.commanded_shed_shares,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0
                           THEN -o.signed_long_delta ELSE 0 END), 0) AS liq_shares,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0
                           THEN -o.signed_long_delta * o.avg_px ELSE 0 END), 0)
           AS liq_proceeds,
         max(o.placed_at) FILTER (WHERE o.signed_long_delta < 0) AS last_liq_at
    FROM p1sel p CROSS JOIN wins w
    LEFT JOIN ords o ON o.condition_id = p.condition_id
                    AND o.placed_at >= p.event_ts
                    AND o.placed_at <  p.event_ts + make_interval(secs => w.secs)
                    AND o.filled > 0 AND o.avg_px IS NOT NULL
   GROUP BY 1, 2, 3, 4, 5, 6
), rq AS MATERIALIZED (
  SELECT l.*,
         COALESCE(sum(o.signed_long_delta), 0) AS reacq_shares,
         COALESCE(sum(o.signed_long_delta * o.avg_px), 0) AS reacq_cost
    FROM liq l
    LEFT JOIN ords o ON o.condition_id = l.condition_id
                    AND l.last_liq_at IS NOT NULL
                    AND o.placed_at >  l.last_liq_at
                    AND o.placed_at <= l.last_liq_at + interval '24 hours'
                    AND o.signed_long_delta > 0 AND o.avg_px IS NOT NULL
   GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
), lastmark AS MATERIALIZED (
  SELECT condition_id, mark FROM (
    SELECT condition_id, mark,
           row_number() OVER (PARTITION BY condition_id ORDER BY at DESC) AS rn
      FROM sh WHERE mark IS NOT NULL) z WHERE rn = 1
), cf AS MATERIALIZED (
  SELECT r.*, lm.mark AS last_mark,
         r.liq_proceeds / NULLIF(r.liq_shares, 0)   AS p_sell,
         r.reacq_cost   / NULLIF(r.reacq_shares, 0) AS p_buy
    FROM rq r LEFT JOIN lastmark lm ON lm.condition_id = r.condition_id
), wnd AS MATERIALIZED (
  SELECT min(placed_at) AS o0, max(placed_at) AS o1
    FROM mirror_orders WHERE lower(whale) = 'rn1'
), cand AS MATERIALIZED (
  SELECT 'A mirror_books.own_book_pnl' AS source, 'book, mark-inclusive' AS concept,
         count(*) AS rows_in_window,
         count(*) FILTER (WHERE own_book_pnl IS NULL) AS rows_null,
         COALESCE(sum(own_book_pnl), 0) AS total_pnl
    FROM mirror_books, wnd
   WHERE lower(whale) = 'rn1' AND opened_at >= wnd.o0 AND opened_at <= wnd.o1
  UNION ALL
  SELECT 'B mirror_books.realized_pnl', 'REALIZED ONLY', count(*),
         count(*) FILTER (WHERE realized_pnl IS NULL), COALESCE(sum(realized_pnl), 0)
    FROM mirror_books, wnd
   WHERE lower(whale) = 'rn1' AND opened_at >= wnd.o0 AND opened_at <= wnd.o1
  UNION ALL
  SELECT 'C mirror_books.settled_pnl', 'SETTLEMENT-INCLUSIVE', count(*),
         count(*) FILTER (WHERE settled_pnl IS NULL), COALESCE(sum(settled_pnl), 0)
    FROM mirror_books, wnd
   WHERE lower(whale) = 'rn1' AND opened_at >= wnd.o0 AND opened_at <= wnd.o1
  UNION ALL
  SELECT 'D mirror_orders.realized', 'REALIZED ONLY', count(*),
         count(*) FILTER (WHERE realized IS NULL), COALESCE(sum(realized), 0)
    FROM mirror_orders, wnd
   WHERE lower(whale) = 'rn1' AND placed_at >= wnd.o0 AND placed_at <= wnd.o1
  UNION ALL
  SELECT 'E live_orders.pnl lane=mirror', 'SETTLEMENT-INCLUSIVE', count(*),
         count(*) FILTER (WHERE pnl IS NULL), COALESCE(sum(pnl), 0)
    FROM live_orders, wnd
   WHERE lane = 'mirror' AND lower(COALESCE(whale_username, '')) = 'rn1'
     AND placed_at >= wnd.o0 AND placed_at <= wnd.o1
), dmg AS MATERIALIZED (
  SELECT COALESCE(sum(CASE WHEN reacq_shares > 0 AND liq_shares > 0
                           THEN LEAST(liq_shares, reacq_shares) * (p_buy - p_sell)
                           ELSE 0 END), 0) AS p1_damage
    FROM cf WHERE attribution_window = '5 30 d (effectively unbounded)'
), oth AS MATERIALIZED (
  SELECT COALESCE(sum(e.exec_realized), 0) AS other_reversal_realized,
         count(*) AS other_reversal_events
    FROM p1 e
   WHERE e.harmful_command
     AND COALESCE(e.regime_label, 'x') <> '1 CONSISTENT_WITH_P1_LONG_ONLY'
     AND e.executions > 0
)
SELECT c.source, c.concept, c.rows_in_window, c.rows_null,
       round(c.total_pnl::numeric, 2) AS total_pnl_candidate,
       to_char((SELECT o0 FROM wnd), 'YYYY-MM-DD HH24:MI') AS window_start,
       to_char((SELECT o1 FROM wnd), 'YYYY-MM-DD HH24:MI') AS window_end,
       round((SELECT p1_damage FROM dmg)::numeric, 2)
         AS identified_p1_forced_flatten_damage_30d_reacq,
       round((SELECT other_reversal_realized FROM oth)::numeric, 2)
         AS identified_other_reversal_realized,
       (SELECT other_reversal_events FROM oth) AS other_reversal_events,
       CASE WHEN (SELECT max(total_pnl) - min(total_pnl) FROM cand)
                 > 0.05 * NULLIF(abs((SELECT max(abs(total_pnl)) FROM cand)), 0)
            THEN 'RATIO NOT PRODUCED: the candidates disagree by more than 5%, '
                 || 'so TOTAL_OBSERVED_BETTOR_LOSS is not reconstructible '
                 || 'consistently from retained data'
            ELSE 'candidates agree within 5%' END AS denominator_verdict
  FROM cand c ORDER BY 1;

\echo '== 11. ACCEPTANCE CHECKS, each with a NON-EMPTY witness count beside it =='
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
), acq AS MATERIALIZED (
  SELECT condition_id, sum(sh * px) AS acq_cost FROM buys GROUP BY 1
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
  SELECT z.condition_id, z.id AS fill_id, z.ts, z.sh AS fsh, z.px AS fpx,
         z.sign_before, z.sign_after
    FROM (SELECT c.*, lag(c.sign_after) OVER (PARTITION BY c.condition_id ORDER BY c.rn)
                        AS sign_before FROM carry c) z
   WHERE z.sign_before IS NOT NULL AND z.sign_after IS NOT NULL
     AND z.sign_before <> z.sign_after
), sh AS MATERIALIZED (
  SELECT condition_id, at, target, his_net, ledger_net, mark
    FROM mirror_shadow WHERE lower(whale) = 'rn1'
), win AS MATERIALIZED (
  SELECT min(at) AS t0, max(at) AS t1,
         min(at) FILTER (WHERE target < 0) AS short_cap_at
    FROM sh
), seen AS MATERIALIZED (
  SELECT condition_id, min(at) AS first_seen, max(at) AS last_seen FROM sh GROUP BY 1
), capcond AS MATERIALIZED (
  -- a NEGATIVE TARGET recorded on THIS condition: short capability, witnessed
  -- on the market itself rather than inferred from a fleet-wide timestamp
  SELECT condition_id, min(at) AS first_neg_target_at
    FROM sh WHERE target < 0 GROUP BY 1
), capord AS MATERIALIZED (
  -- a SHORT-INTENT ORDER on this condition's book: the same capability
  -- witnessed on the order path instead of the target path
  SELECT bk.condition_id, min(o.placed_at) AS first_short_intent_at
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
     AND o.intent IN ('ORDER_INTENT_BUY_SHORT', 'ORDER_INTENT_SELL_SHORT')
   GROUP BY 1
), capflow AS MATERIALIZED (
  -- E12 flow sizing, witnessed per BOOK by flow_base being populated at all
  SELECT condition_id,
         min(opened_at) FILTER (WHERE flow_base IS NOT NULL) AS first_flow_book_at
    FROM mirror_books WHERE lower(whale) = 'rn1' GROUP BY 1
), mix AS MATERIALIZED (
  SELECT condition_id, at AS t, 0 AS kind, target, his_net, ledger_net, mark,
         NULL::bigint AS fill_id, NULL::float8 AS fsh, NULL::float8 AS fpx
    FROM sh WHERE condition_id IN (SELECT condition_id FROM ev)
  UNION ALL
  SELECT condition_id, ts, 1, NULL::int, NULL::float8, NULL::int, NULL::float8,
         fill_id, fsh, fpx
    FROM ev
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
         first_value(g.target)     OVER pv AS tb,
         first_value(g.ledger_net) OVER pv AS lb,
         first_value(g.target)     OVER nx AS ta,
         first_value(g.his_net)    OVER nx AS na,
         first_value(g.mark)       OVER nx AS ma,
         first_value(g.t)          OVER nx AS ata
    FROM g
  WINDOW pv AS (PARTITION BY g.condition_id, g.grp_prev ORDER BY g.t, g.kind),
         nx AS (PARTITION BY g.condition_id, g.grp_next ORDER BY g.t DESC, g.kind DESC)
), evx AS MATERIALIZED (
  SELECT nb.condition_id, nb.fill_id, nb.t AS event_ts, nb.fsh, nb.fpx,
         (nb.grp_prev > 0) AS has_tick_before,
         (nb.grp_next > 0) AS has_tick_after,
         CASE WHEN nb.grp_prev > 0 THEN nb.tb END AS target_before,
         CASE WHEN nb.grp_prev > 0 THEN nb.lb END AS ledger_before,
         CASE WHEN nb.grp_next > 0 THEN nb.ta END AS target_after,
         CASE WHEN nb.grp_next > 0 THEN nb.na END AS net_after,
         CASE WHEN nb.grp_next > 0 THEN nb.ma END AS mark_after
    FROM nb WHERE nb.kind = 1
), rch AS MATERIALIZED (
  -- REACH NOW REQUIRES A RECORDED TARGET ON BOTH SIDES, not merely a tick on
  -- both sides. Run 74 exposed the difference: 1,509 of 3,183 reach events
  -- printed as "no straddling tick" when the ticks plainly existed -- my own
  -- reach test guaranteed them -- and what was actually NULL was
  -- mirror_shadow.target itself. An event whose straddling ticks carry no
  -- target cannot yield a commanded transition, so it is a coverage
  -- limitation and belongs OUTSIDE reach, with its own reason.
  SELECT e.*, s.first_seen, s.last_seen,
         CASE WHEN e.has_tick_before AND e.has_tick_after
                   AND e.target_before IS NOT NULL AND e.target_after IS NOT NULL
                THEN '1 IN_REACH'
              WHEN e.event_ts < (SELECT t0 FROM win)
                OR e.event_ts > (SELECT t1 FROM win)
                THEN '2 OUTSIDE_REACH: outside the mirror operating window'
              WHEN s.condition_id IS NULL
                THEN '3 OUTSIDE_REACH: market never observed'
              WHEN NOT e.has_tick_before
                THEN '4 OUTSIDE_REACH: observed, but only after the event'
              WHEN NOT e.has_tick_after
                THEN '5 OUTSIDE_REACH: observed, but no tick after the event'
              ELSE '6 OUTSIDE_REACH: straddling ticks exist but carry no '
                   || 'recorded target'
         END AS reach
    FROM evx e LEFT JOIN seen s ON s.condition_id = e.condition_id
), tcl AS MATERIALIZED (
  SELECT r.*,
         CASE WHEN r.target_before IS NULL OR r.target_after IS NULL THEN NULL
              WHEN r.target_before = r.target_after THEN 'HOLD'
              WHEN r.target_before > 0 AND r.target_after = 0 THEN 'LONG_TO_FLAT'
              WHEN r.target_before < 0 AND r.target_after = 0 THEN 'SHORT_TO_FLAT'
              WHEN r.target_before > 0 AND r.target_after < 0 THEN 'LONG_TO_SHORT'
              WHEN r.target_before < 0 AND r.target_after > 0 THEN 'SHORT_TO_LONG'
              WHEN r.target_after > r.target_before AND r.target_after > 0
                   AND r.target_before >= 0 THEN 'INCREASE_LONG'
              WHEN r.target_before > 0 AND r.target_after < r.target_before
                   AND r.target_after > 0 THEN 'REDUCE_LONG'
              WHEN r.target_after < r.target_before AND r.target_after < 0
                   AND r.target_before <= 0 THEN 'INCREASE_SHORT'
              WHEN r.target_before < 0 AND r.target_after > r.target_before
                   AND r.target_after < 0 THEN 'REDUCE_SHORT'
              ELSE 'UNCLASSIFIED' END AS recorded_target_transition
    FROM rch r
), cmd AS MATERIALIZED (
  SELECT t.*,
         (t.reach = '1 IN_REACH'
          AND t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                               'LONG_TO_SHORT', 'SHORT_TO_LONG')
          AND abs(COALESCE(t.ledger_before, 0)) > 0) AS harmful_command,
         CASE WHEN t.recorded_target_transition IN ('LONG_TO_FLAT', 'SHORT_TO_FLAT',
                                                    'LONG_TO_SHORT', 'SHORT_TO_LONG')
              THEN abs(COALESCE(t.ledger_before, 0)) ELSE 0 END AS commanded_shed_shares,
         abs(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_change,
         sign(COALESCE(t.target_after, 0) - COALESCE(t.ledger_before, 0))
           AS commanded_direction,
         (t.event_ts < COALESCE((SELECT short_cap_at FROM win),
                                'infinity'::timestamptz))
           AS before_short_capability_observed_anywhere,
         (LEAST(COALESCE(cc.first_neg_target_at, 'infinity'::timestamptz),
                COALESCE(co.first_short_intent_at, 'infinity'::timestamptz))
            <= t.event_ts) AS short_capability_observed_on_this_condition,
         (cf.first_flow_book_at IS NOT NULL
          AND cf.first_flow_book_at <= t.event_ts)
           AS e12_flow_sizing_observed_on_this_book
    FROM tcl t
    LEFT JOIN capcond cc ON cc.condition_id = t.condition_id
    LEFT JOIN capord  co ON co.condition_id = t.condition_id
    LEFT JOIN capflow cf ON cf.condition_id = t.condition_id
), ords AS MATERIALIZED (
  SELECT bk.condition_id, o.id AS order_id, o.book_id, o.placed_at,
         o.side AS plan_side, o.intent AS wire_intent, o.kind, o.qty,
         o.filled, o.avg_px, o.realized, o.cash_usd,
         o.target_at_place, o.ledger_at_place, o.bid_at_place, o.ask_at_place,
         CASE o.intent WHEN 'ORDER_INTENT_BUY_LONG'   THEN  o.filled
                       WHEN 'ORDER_INTENT_SELL_LONG'  THEN -o.filled
                       WHEN 'ORDER_INTENT_BUY_SHORT'  THEN -o.filled
                       WHEN 'ORDER_INTENT_SELL_SHORT' THEN  o.filled END
           AS signed_long_delta
    FROM mirror_orders o JOIN mirror_books bk ON bk.id = o.book_id
   WHERE lower(o.whale) = 'rn1'
), exj AS MATERIALIZED (
  SELECT c.condition_id, c.fill_id, c.event_ts, c.fsh, c.fpx, c.reach,
         c.recorded_target_transition, c.harmful_command, c.commanded_shed_shares,
         c.before_short_capability_observed_anywhere,
         c.short_capability_observed_on_this_condition,
         c.e12_flow_sizing_observed_on_this_book,
         c.target_before, c.target_after, c.ledger_before,
         c.net_after, c.mark_after, c.commanded_change, c.commanded_direction,
         COALESCE(sum(o.filled), 0) AS exec_shares,
         COALESCE(sum(o.filled) FILTER (WHERE o.avg_px IS NOT NULL), 0)
           AS exec_priced_shares,
         COALESCE(sum(o.signed_long_delta), 0) AS exec_signed_delta,
         COALESCE(sum(CASE WHEN o.signed_long_delta < 0 THEN -o.signed_long_delta
                           ELSE 0 END), 0) AS exec_shed_shares,
         COALESCE(sum(o.filled * COALESCE(o.avg_px, 0)), 0) AS exec_notional,
         COALESCE(sum(o.realized), 0) AS exec_realized,
         count(o.order_id) AS orders_in_window,
         count(o.order_id) FILTER (WHERE o.filled > 0) AS executions
    FROM cmd c
    LEFT JOIN ords o ON o.condition_id = c.condition_id
                    AND o.placed_at >= c.event_ts
                    AND o.placed_at <  c.event_ts + interval '900 seconds'
   GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19
), lc AS MATERIALIZED (
  SELECT e.*,
         CASE WHEN e.reach <> '1 IN_REACH' THEN '1 OUTSIDE_BETTOR_CAUSAL_REACH'
              WHEN NOT e.harmful_command THEN '2 IN_REACH_NO_HARMFUL_COMMAND'
              WHEN e.exec_shares = 0 THEN '3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION'
              WHEN e.exec_priced_shares < e.exec_shares
                THEN '6 EXECUTION_EFFECT_UNIDENTIFIED'
              WHEN e.exec_signed_delta * e.commanded_direction
                   >= 0.99 * e.commanded_change
                THEN '4 IDENTIFIED_REALIZED_DAMAGE'
              ELSE '5 PARTIALLY_IDENTIFIED_DAMAGE' END AS causal_class
    FROM exj e
), p1 AS MATERIALIZED (
  SELECT e.*,
         (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
          AND COALESCE(e.net_after, 0) < 0) AS zero_collapse_shape,
         CASE
           WHEN NOT (COALESCE(e.ledger_before, 0) > 0 AND e.target_after = 0
                     AND COALESCE(e.net_after, 0) < 0) THEN NULL
           WHEN e.short_capability_observed_on_this_condition
             THEN '3 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (this condition)'
           WHEN NOT e.before_short_capability_observed_anywhere
             THEN '2 ZERO_COLLAPSE_AFTER_SHORT_CAPABILITY_OBSERVED (fleet-wide only)'
           ELSE '1 CONSISTENT_WITH_P1_LONG_ONLY'
         END AS regime_label
    FROM exj e
), pairs AS MATERIALIZED (
  SELECT o.book_id, o.placed_at, o.ledger_at_place, o.signed_long_delta,
         lag(o.ledger_at_place) OVER (PARTITION BY o.book_id
                                      ORDER BY o.placed_at, o.order_id) AS prev_ledger,
         lag(o.signed_long_delta) OVER (PARTITION BY o.book_id
                                      ORDER BY o.placed_at, o.order_id) AS prev_delta
    FROM ords o
)
SELECT
  (SELECT count(*) FROM lc WHERE harmful_command) AS chk1_witness_harmful_events,
  (SELECT count(*) FROM lc WHERE harmful_command
     AND causal_class NOT IN ('3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION',
                              '4 IDENTIFIED_REALIZED_DAMAGE',
                              '5 PARTIALLY_IDENTIFIED_DAMAGE',
                              '6 EXECUTION_EFFECT_UNIDENTIFIED'))
    AS chk1_unclassified_expected_0,
  (SELECT count(*) FROM lc WHERE reach <> '1 IN_REACH') AS chk2_witness_outside_reach,
  (SELECT count(*) FROM lc WHERE reach <> '1 IN_REACH' AND harmful_command)
    AS chk2_outside_reach_in_denominator_expected_0,
  (SELECT count(*) FROM pairs
    WHERE ledger_at_place IS NOT NULL AND prev_ledger IS NOT NULL
      AND prev_delta IS NOT NULL) AS chk3_witness_comparable_pairs,
  (SELECT count(*) FROM pairs
    WHERE ledger_at_place IS NOT NULL AND prev_ledger IS NOT NULL
      AND prev_delta IS NOT NULL
      AND abs((ledger_at_place - prev_ledger) - prev_delta) > 1)
    AS chk3_intent_vs_ledger_disagreements,
  (SELECT count(*) FROM p1 WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY')
    AS chk4_witness_p1_consistent_events,
  (SELECT count(*) FROM p1 WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY'
     AND NOT (COALESCE(ledger_before, 0) > 0 AND target_after = 0
              AND COALESCE(net_after, 0) < 0)) AS chk4_violations_expected_0,
  (SELECT count(*) FROM p1 WHERE regime_label LIKE '2 %' OR regime_label LIKE '3 %')
    AS chk5_witness_post_capability_collapses,
  (SELECT count(*) FROM p1 WHERE regime_label = '1 CONSISTENT_WITH_P1_LONG_ONLY'
     AND (short_capability_observed_on_this_condition
          OR NOT before_short_capability_observed_anywhere))
    AS chk5_capability_observed_labeled_p1_expected_0,
  (SELECT count(*) FROM lc WHERE harmful_command) AS chk6_harmful_universe,
  (SELECT count(*) FROM lc WHERE causal_class IN
     ('3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION', '4 IDENTIFIED_REALIZED_DAMAGE',
      '5 PARTIALLY_IDENTIFIED_DAMAGE', '6 EXECUTION_EFFECT_UNIDENTIFIED'))
    AS chk6_sum_of_the_four_classes,
  (SELECT count(*) FROM lc WHERE harmful_command)
    - (SELECT count(*) FROM lc WHERE causal_class IN
        ('3 IN_REACH_HARMFUL_COMMAND_NO_EXECUTION', '4 IDENTIFIED_REALIZED_DAMAGE',
         '5 PARTIALLY_IDENTIFIED_DAMAGE', '6 EXECUTION_EFFECT_UNIDENTIFIED'))
    AS chk6_closure_gap_expected_0,
  (SELECT count(*) FROM lc) AS chk6_full_universe,
  (SELECT count(*) FROM lc WHERE causal_class IS NULL)
    AS chk6_unclassified_events_expected_0,
  (SELECT count(*) FROM lc WHERE reach = '1 IN_REACH')
    AS chk7_witness_classified_transitions,
  (SELECT count(*) FROM lc WHERE recorded_target_transition = 'UNCLASSIFIED'
     OR (reach = '1 IN_REACH' AND recorded_target_transition IS NULL))
    AS chk7_unclassified_transition_expected_0;
