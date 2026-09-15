-- ============================================================================
-- SECTION 3: COMPLETION EXECUTION -- could BETTOR have reproduced the matched
-- economics using ONLY information available at the decision timestamp?
-- (2026-09-11, read-only.)
--
-- THE ANTI-LOOKAHEAD RULE, which governs every line below. The replay may not
-- know RN1 was a maker because the completed transaction eventually contained
-- several legs, if that fact was not yet observable when BETTOR had to decide.
-- Two separate fields therefore travel with every fill:
--
--   rn1_maker_taker_ex_post          FOR ATTRIBUTION ONLY. proven_maker when
--       both feeds saw the envelope and it carried several venue legs with a
--       zero fee; proven_taker when both saw it, one leg, full wedge; unknown
--       otherwise. Never consulted by the simulated decision.
--   maker_taker_at_decision_time     WHAT THE MIRROR MAY USE. Derived from the
--       cash row alone, which is all that exists at first_any_detection_at: a
--       cash price sitting on the cent grid reads probable_maker, one that
--       inverts onto the grid through the wedge reads probable_taker, and
--       anything else is unknown. Measured, not assumed -- on the ex-post
--       populations this call is right 13,914 to 130 on takers and 829 to 1 on
--       makers, so it is a reading; it simply does not always fire.
--   maker_taker_inference_available_at   when the ex-post label first BECAME
--       constructible, i.e. when the second feed arrived. Median 275 s after
--       the fill, which is exactly why it cannot price a decision.
--
-- WHAT "COMPLETION" MEANS FOR US, which is not what it means for him. He holds
-- YES and NO separately and completes a pair by buying the second token. The
-- US venue keeps ONE signed position per market, so we cannot hold both sides:
-- our equivalent of his completing NO buy is SELLING our long at the
-- complement. If he acquires NO at v_N, the economically identical action for
-- us is a sale at (1 - v_N). So
--
--     BETTOR theoretical completion price = 1 - (his NO-leg venue price)
--     our pair cost  = our long entry + (1 - our sale price)
--     our edge       = our sale price - our long entry
--
-- and the execution question is whether OUR OBSERVED BID reached that
-- completion price at or after the moment we learned of his fill.
--
-- WHY A TOUCH IS NOT A FILL. Historical queue position and resting depth do
-- not exist in any retained table. A bid EQUAL to the completion price means
-- someone was willing to pay it, not that we would have been filled at it.
-- Every count below is therefore reported twice:
--     optimistic   touch or better would have filled
--     conservative only a strict trade-through would have filled
-- and the two are never averaged into one number.
--
-- THE WINDOWS, from the data gate, and they are short:
--     order-level quote observations   2026-09-06 .. 2026-09-10  (5 days)
--     his_fill_id opportunity join     2026-09-08 partial, 09-09 .. 09-10 clean
--     candidate refusal quotes         2026-09-07 .. 2026-09-10  (4 days)
-- A two-day result can identify a mechanism. It cannot establish long-run
-- expectancy, and nothing here should be read as if it did.
--
-- Read-only: five SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. QUOTE SOURCES: every independent decision-time observation we kept =='
-- Establishes what an OBSERVED_QUOTE_PROXY could be built from, and whether
-- each source is conditioned on us having traded (which would bias section 4).
SELECT 'mirror_orders.bid/ask_at_place' AS source, count(*) AS observations,
       count(DISTINCT us_market_slug) AS markets,
       count(bid_at_place) AS with_bid, count(ask_at_place) AS with_ask,
       to_char(min(placed_at), 'MM-DD HH24:MI') AS first_obs,
       to_char(max(placed_at), 'MM-DD HH24:MI') AS last_obs,
       'keyed by slug; ONLY where we placed -- conditioned on our action' AS caveat
  FROM mirror_orders
UNION ALL
SELECT 'mirror_candidate_refusals.ask', count(*), count(DISTINCT condition_id),
       count(mark), count(ask),
       to_char(min(at), 'MM-DD HH24:MI'), to_char(max(at), 'MM-DD HH24:MI'),
       'keyed by CONDITION; logs markets we REFUSED -- not conditioned on trading'
  FROM mirror_candidate_refusals
UNION ALL
SELECT 'mirror_shadow.bid/ask', count(*), count(DISTINCT condition_id),
       count(bid), count(ask),
       to_char(min(at), 'MM-DD HH24:MI'), to_char(max(at), 'MM-DD HH24:MI'),
       'shadow instrument; check whether it overlaps the order window'
  FROM mirror_shadow
UNION ALL
SELECT 'copy_probes.best_ask + DEPTH', count(*), count(DISTINCT asset),
       count(best_ask_usd), count(best_ask),
       to_char(min(probe_at), 'MM-DD HH24:MI'), to_char(max(probe_at), 'MM-DD HH24:MI'),
       'joins to trades.id; book snapshot AT the moment our executor would act'
  FROM copy_probes
 ORDER BY 2 DESC;


\echo '== 1b. copy_probes IN THE WINDOW: real depth, not an assumed fill =='
-- This table exists precisely for the question section 3 asks: it snapshots
-- the residual book at detection+fetch and precomputes the achievable price
-- for standard clips. best_ask_usd is notional actually available, and
-- vwap_1k / vwap_5k are depth-weighted. Where it has rows, fill coverage is a
-- reading rather than an assumption -- so its window decides how much of
-- section 3 can escape the touch-versus-fill problem.
SELECT probe_at::date AS day, count(*) AS probes,
       count(*) FILTER (WHERE book_ok) AS book_readable,
       count(best_ask) AS with_best_ask,
       count(best_ask_usd) AS with_depth_at_best,
       count(*) FILTER (WHERE fillable_1k) AS fillable_1k,
       count(*) FILTER (WHERE fillable_5k) AS fillable_5k,
       round(avg(reaction_s)::numeric, 2) AS mean_reaction_s,
       round(avg(slippage_cents)::numeric, 3) AS mean_slippage_cents,
       count(depth) AS with_depth_ladder
  FROM copy_probes
 WHERE probe_at >= now() - interval '14 days'
 GROUP BY 1 ORDER BY 1;


\echo '== 2. THE dM EVENT POPULATION, by ex-post and decision-time label =='
WITH base AS (
  SELECT t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.detected_at,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed,
         -- `s` is already one row per feed, so count(*) here would count FEEDS.
         -- The venue LEG COUNT -- which is what separates maker from taker --
         -- is the inner n.
         max(n) FILTER (WHERE feed = 'venue') AS n_venue_rows,
         min(min_det) AS first_any_detection_at,
         min(min_det) FILTER (WHERE feed = 'venue') AS venue_seen_at,
         max(min_det) AS reconstruction_available_at,
         max(vwap) FILTER (WHERE feed = 'venue') AS venue_price,
         max(vwap) FILTER (WHERE feed = 'cash')  AS effective_cash_price
    FROM (SELECT tx_hash, asset, side, feed, min(detected_at) AS min_det,
                 sum(sh * px) / NULLIF(sum(sh), 0) AS vwap, count(*) AS n
            FROM base GROUP BY 1, 2, 3, 4) s
   GROUP BY 1, 2, 3
), lab AS (
  SELECT e.*,
         CASE WHEN venue_price IS NULL OR effective_cash_price IS NULL THEN 'unknown'
              WHEN n_venue_rows > 1 THEN 'proven_maker'
              WHEN abs((effective_cash_price - venue_price)
                       - 0.05 * venue_price * (1 - venue_price)) < 5e-5 THEN 'proven_taker'
              ELSE 'unknown' END AS ex_post,
         CASE WHEN effective_cash_price IS NULL THEN 'unknown'
              WHEN abs(effective_cash_price
                       - round(effective_cash_price::numeric, 2)::float8) < 1e-6
                 THEN 'decision_time_probable_maker'
              WHEN 1.1025 - 0.2 * effective_cash_price >= 0
                   AND abs(((1.05 - sqrt(1.1025 - 0.2 * effective_cash_price)) / 0.1)
                           - round((((1.05 - sqrt(1.1025 - 0.2 * effective_cash_price)) / 0.1))::numeric, 2)::float8)
                       < 1e-6
                 THEN 'decision_time_probable_taker'
              ELSE 'unknown' END AS at_decision
    FROM env e
), canon AS (
  SELECT b.* FROM base b JOIN env e
    ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.side = b.side
   AND e.canon_feed = b.feed
), r AS (
  SELECT c.condition_id, c.tx_hash, c.asset, c.side, c.ts, c.sh, c.px, c.outcome_index,
         sum(CASE WHEN outcome_index = 0
                  THEN CASE WHEN side = 'BUY' THEN sh ELSE -sh END ELSE 0 END)
           OVER w AS y,
         sum(CASE WHEN outcome_index = 1
                  THEN CASE WHEN side = 'BUY' THEN sh ELSE -sh END ELSE 0 END)
           OVER w AS n
    FROM canon c
  WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), dm AS (
  SELECT condition_id, tx_hash, asset, side, ts, sh, px, outcome_index,
         LEAST(GREATEST(y, 0), GREATEST(n, 0)) AS m,
         COALESCE(LEAST(GREATEST(lag(y) OVER w2, 0), GREATEST(lag(n) OVER w2, 0)), 0) AS m_prev
    FROM r
  WINDOW w2 AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
)
SELECT lab.ex_post AS rn1_maker_taker_ex_post,
       lab.at_decision AS maker_taker_at_decision_time,
       count(*) AS dM_events,
       round(sum(GREATEST(dm.m - dm.m_prev, 0))::numeric, 0) AS dM_shares,
       round(sum(GREATEST(dm.m - dm.m_prev, 0) * dm.px)::numeric, 0) AS dM_dollars,
       round(sum(GREATEST(dm.m - dm.m_prev, 0) * 0.10)::numeric, 0) AS intended_BETTOR_shares,
       round(avg(extract(epoch FROM lab.first_any_detection_at - dm.ts))::numeric, 2)
         AS mean_detect_lag_s,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM lab.reconstruction_available_at
                               - lab.first_any_detection_at))::numeric, 1)
         AS p50_label_lookahead_s
  FROM dm JOIN lab ON lab.tx_hash = dm.tx_hash AND lab.asset = dm.asset
                  AND lab.side = dm.side
 WHERE dm.m - dm.m_prev > 0.000001
 GROUP BY 1, 2 ORDER BY 3 DESC;


\echo '== 3. HIS MATCHED ECONOMICS on those events, gross and after measured fee =='
WITH base AS (
  SELECT t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), f AS (
  SELECT condition_id, outcome_index, tx_hash, asset, feed,
         count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM base GROUP BY 1, 2, 3, 4, 5
), g AS (
  SELECT condition_id, outcome_index, tx_hash, asset,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash,
         max(vwap)   FILTER (WHERE feed = 'venue') AS v,
         max(vwap)   FILTER (WHERE feed = 'cash')  AS c
    FROM f GROUP BY 1, 2, 3, 4
), leg AS (
  SELECT condition_id, outcome_index,
         COALESCE(sh_venue, sh_cash) AS shares,
         COALESCE(v, c) AS venue_price,
         COALESCE(c, v) AS effective_cash_price,
         CASE WHEN v IS NULL OR c IS NULL THEN 'unknown'
              WHEN n_venue > 1 THEN 'proven_maker'
              WHEN abs((c - v) - 0.05 * v * (1 - v)) < 5e-5 THEN 'proven_taker'
              ELSE 'unknown' END AS ex_post
    FROM g
), m AS (
  SELECT condition_id,
         bool_or(ex_post = 'proven_maker')  AS any_proven_maker,
         bool_or(ex_post = 'proven_taker')  AS any_proven_taker,
         bool_and(ex_post <> 'unknown')     AS fee_fully_observed,
         sum(shares) FILTER (WHERE outcome_index = 0) AS y,
         sum(shares) FILTER (WHERE outcome_index = 1) AS n,
         sum(shares * venue_price) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 0), 0) AS v0,
         sum(shares * venue_price) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 1), 0) AS v1,
         sum(shares * effective_cash_price) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 0), 0) AS c0,
         sum(shares * effective_cash_price) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 1), 0) AS c1
    FROM leg GROUP BY 1
)
SELECT CASE WHEN fee_fully_observed THEN 'OBSERVED_FEE_SUBSET (every leg proven)'
            WHEN any_proven_maker OR any_proven_taker THEN 'partly proven'
            ELSE 'fee UNKNOWN on every leg' END AS basis,
       count(*) AS markets,
       round(sum(LEAST(y, n))::numeric, 0) AS matched_sh,
       round(sum(LEAST(y, n) * 0.10)::numeric, 0) AS intended_BETTOR_sh,
       round(avg(v0 + v1)::numeric, 5) AS rn1_gross_pair_cost,
       round(avg(c0 + c1)::numeric, 5) AS rn1_effective_pair_cost,
       round(avg(1 - (v0 + v1))::numeric, 5) AS gross_matched_edge,
       round(avg(1 - (c0 + c1))::numeric, 5) AS net_matched_edge,
       round(avg((c0 + c1) - (v0 + v1))::numeric, 5) AS fee_drag_per_pair
  FROM m WHERE y > 0 AND n > 0 GROUP BY 1 ORDER BY 2 DESC;


\echo '== 4. COMPLETION EXECUTION against the OBSERVED_QUOTE_PROXY (mirror_shadow) =='
-- mirror_shadow is the right source and mirror_orders is not. The shadow runs
-- on EVERY candidate, keyed by condition_id, so its quotes are not conditioned
-- on us having chosen to trade -- 247,607 observations over 6,513 conditions,
-- against 11,183 order-side quotes on 1,081 slugs that exist only where we
-- placed. Using the order quotes here would have measured our own selection.
--
-- Our completion of his pair is a SALE at 1 - (his NO-leg price). The test is
-- whether a bid INDEPENDENTLY OBSERVED at or after we learned of his fill
-- reached that level. The delay from the learning moment to the observation is
-- carried on every row and never interpolated away.
WITH his AS (
  SELECT t.condition_id, t.ts, t.detected_at,
         t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), q AS (
  -- LATERAL with LIMIT 1, not a range join plus row_number(). The window form
  -- materialises every shadow row inside each fill's 15-minute box before
  -- discarding all but the first; the lateral stops at the first one. Same
  -- answer, and it is the difference between finishing and timing out.
  SELECT h.condition_id, h.ts, h.sh, h.px, (1.0 - h.px) AS completion_price,
         s.bid, s.ask,
         extract(epoch FROM s.at - h.detected_at) AS observation_delay_s
    FROM his h
    CROSS JOIN LATERAL (
      SELECT sh2.bid, sh2.ask, sh2.at
        FROM mirror_shadow sh2
       WHERE sh2.condition_id = h.condition_id
         AND sh2.at >= h.detected_at
         AND sh2.at < h.detected_at + interval '15 minutes'
         AND sh2.bid IS NOT NULL
       ORDER BY sh2.at
       LIMIT 1) s
)
SELECT CASE WHEN bid > completion_price + 1e-9 THEN '1 TRADE-THROUGH (bid above his level)'
            WHEN bid >= completion_price - 1e-9 THEN '2 TOUCH (bid AT his level -- fill UNPROVEN)'
            ELSE '3 NO FILL at his economics' END AS status,
       count(*) AS opportunities,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_of_observed,
       round(sum(sh)::numeric, 0) AS his_shares,
       round(sum(sh * 0.10)::numeric, 0) AS intended_BETTOR_shares,
       round(avg(bid - completion_price)::numeric, 5) AS mean_bid_minus_completion,
       round(avg(observation_delay_s)::numeric, 1) AS mean_observation_delay_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY observation_delay_s)::numeric, 1)
         AS p50_observation_delay_s
  FROM q WHERE rn = 1 GROUP BY 1 ORDER BY 1;


\echo '== 5. COVERAGE: numerator and denominator for statement 4, every day =='
-- An execution rate computed only over events we happened to quote is not an
-- execution rate. This is the denominator.
WITH his AS (
  SELECT t.condition_id, t.ts, t.detected_at, t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), seen AS (
  SELECT DISTINCT h.condition_id, h.ts, h.px
    FROM his h JOIN mirror_shadow s
      ON s.condition_id = h.condition_id
     AND s.at >= h.detected_at AND s.at < h.detected_at + interval '15 minutes'
     AND s.bid IS NOT NULL
)
SELECT his.ts::date AS day,
       count(*) AS his_BUY_fills,
       count(seen.condition_id) AS with_an_INDEPENDENT_bid,
       round((100.0 * count(seen.condition_id) / count(*))::numeric, 2) AS pct_covered,
       round(sum(his.sh * his.px)::numeric, 0) AS his_dollars,
       round(sum(his.sh * his.px) FILTER (WHERE seen.condition_id IS NOT NULL)::numeric, 0)
         AS covered_dollars
  FROM his LEFT JOIN seen
    ON seen.condition_id = his.condition_id AND seen.ts = his.ts AND seen.px = his.px
 GROUP BY 1 ORDER BY 1;


\echo '== 6. ENTRY EXECUTION from copy_probes -- real depth, 37 days, not 5 =='
-- The probe table snapshots the residual book at detection+fetch and
-- precomputes the achievable price for standard clips, so entry drag is
-- MEASURED rather than modelled, and fill coverage rests on retained depth
-- instead of an assumption about queue position. Stratified by the
-- decision-time maker/taker call, which is the only label a live mirror could
-- have used. The ex-post label is deliberately absent here.
WITH p AS (
  SELECT cp.probe_at, cp.reaction_s::float8 AS reaction_s,
         cp.his_price::float8 AS his_price, cp.his_size::float8 AS his_size,
         cp.best_ask::float8 AS best_ask, cp.best_ask_usd::float8 AS depth_usd,
         cp.slippage_cents::float8 AS slippage_cents,
         cp.vwap_1k::float8 AS vwap_1k, cp.vwap_5k::float8 AS vwap_5k,
         cp.fillable_1k, cp.fillable_5k, cp.book_ok,
         CASE WHEN abs(cp.his_price::float8
                       - round(cp.his_price::numeric, 2)::float8) < 1e-6
                THEN 'decision_time_probable_maker'
              WHEN 1.1025 - 0.2 * cp.his_price::float8 >= 0
                   AND abs(((1.05 - sqrt(1.1025 - 0.2 * cp.his_price::float8)) / 0.1)
                           - round((((1.05 - sqrt(1.1025 - 0.2 * cp.his_price::float8)) / 0.1))::numeric, 2)::float8)
                       < 1e-6
                THEN 'decision_time_probable_taker'
              ELSE 'unknown' END AS at_decision
    FROM copy_probes cp JOIN whales w ON w.id = cp.whale_id
   WHERE lower(w.username) = 'rn1' AND cp.side = 'BUY'
     AND cp.probe_at >= timestamptz '2026-08-05 00:00Z'
     AND cp.book_ok AND cp.best_ask IS NOT NULL
)
SELECT at_decision AS maker_taker_at_decision_time,
       count(*) AS probes,
       round(avg(his_price)::numeric, 4) AS mean_his_price,
       round(avg(best_ask)::numeric, 4) AS mean_best_ask_we_saw,
       round(avg(slippage_cents)::numeric, 3) AS mean_ENTRY_DRAG_cents,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY slippage_cents)::numeric, 3)
         AS p50_entry_drag_cents,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY slippage_cents)::numeric, 3)
         AS p90_entry_drag_cents,
       count(*) FILTER (WHERE best_ask <= his_price + 1e-9) AS ask_AT_or_BETTER_than_his,
       round((100.0 * count(*) FILTER (WHERE best_ask <= his_price + 1e-9)
              / count(*))::numeric, 2) AS pct_at_or_better,
       count(*) FILTER (WHERE fillable_1k) AS depth_covers_1k,
       count(*) FILTER (WHERE fillable_5k) AS depth_covers_5k,
       round(avg(depth_usd)::numeric, 0) AS mean_usd_at_best_ask,
       round(avg(reaction_s)::numeric, 2) AS mean_reaction_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY reaction_s)::numeric, 2)
         AS p90_reaction_s
  FROM p GROUP BY 1 ORDER BY 2 DESC;
