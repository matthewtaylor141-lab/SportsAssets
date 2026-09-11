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
         count(*) FILTER (WHERE feed = 'venue') AS n_venue_rows,
         min(detected_at) AS first_any_detection_at,
         min(detected_at) FILTER (WHERE feed = 'venue') AS venue_seen_at,
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


\echo '== 4. THE EXECUTION TEST: did OUR observed bid ever reach his completion price? =='
-- Our completion of his pair is a SALE at 1 - (his NO-leg price). The test is
-- whether a bid we actually observed, at or after we learned of the fill,
-- reached that level. Observation delay is carried, never interpolated.
WITH his AS (
  SELECT t.condition_id, t.outcome_index, t.ts, t.detected_at,
         t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), mapped AS (
  SELECT DISTINCT ON (b.condition_id) b.condition_id, b.us_market_slug
    FROM mirror_books b ORDER BY b.condition_id, b.opened_at
), ev AS (
  SELECT h.condition_id, m.us_market_slug, h.ts, h.detected_at, h.sh, h.px,
         (1.0 - h.px) AS completion_price
    FROM his h JOIN mapped m ON m.condition_id = h.condition_id
), q AS (
  SELECT ev.condition_id, ev.us_market_slug, ev.ts, ev.sh, ev.completion_price,
         o.bid_at_place, o.placed_at,
         extract(epoch FROM o.placed_at - ev.detected_at) AS observation_delay_s,
         row_number() OVER (PARTITION BY ev.condition_id, ev.ts
                            ORDER BY o.placed_at) AS rn
    FROM ev JOIN mirror_orders o
      ON o.us_market_slug = ev.us_market_slug
     AND o.placed_at >= ev.detected_at
     AND o.placed_at < ev.detected_at + interval '15 minutes'
     AND o.bid_at_place IS NOT NULL
)
SELECT CASE WHEN bid_at_place > completion_price + 1e-9 THEN '1 TRADE-THROUGH (bid above his level)'
            WHEN bid_at_place >= completion_price - 1e-9 THEN '2 TOUCH (bid AT his level -- fill UNPROVEN)'
            ELSE '3 NO FILL at his economics' END AS status,
       count(*) AS opportunities,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_of_observed,
       round(sum(sh)::numeric, 0) AS his_shares,
       round(sum(sh * 0.10)::numeric, 0) AS intended_BETTOR_shares,
       round(avg(bid_at_place - completion_price)::numeric, 5) AS mean_bid_minus_completion,
       round(avg(observation_delay_s)::numeric, 1) AS mean_observation_delay_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY observation_delay_s)::numeric, 1)
         AS p50_observation_delay_s
  FROM q WHERE rn = 1 GROUP BY 1 ORDER BY 1;


\echo '== 5. COVERAGE: how many of his events have ANY observed quote at all? =='
-- The denominator that keeps statement 4 honest. An execution rate computed
-- only over events we happened to quote is not an execution rate.
WITH his AS (
  SELECT t.condition_id, t.ts, t.detected_at, t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), mapped AS (
  SELECT DISTINCT ON (b.condition_id) b.condition_id, b.us_market_slug
    FROM mirror_books b ORDER BY b.condition_id, b.opened_at
)
SELECT his.ts::date AS day,
       count(*) AS his_BUY_fills,
       count(m.condition_id) AS on_a_market_we_mapped,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM mirror_orders o
          WHERE o.us_market_slug = m.us_market_slug
            AND o.placed_at >= his.detected_at
            AND o.placed_at < his.detected_at + interval '15 minutes'
            AND o.bid_at_place IS NOT NULL)) AS with_an_observed_bid,
       round((100.0 * count(m.condition_id) / count(*))::numeric, 2) AS pct_mapped,
       round(sum(his.sh * his.px)::numeric, 0) AS his_dollars,
       round(sum(his.sh * his.px) FILTER (WHERE m.condition_id IS NOT NULL)::numeric, 0)
         AS mapped_dollars
  FROM his LEFT JOIN mapped m ON m.condition_id = his.condition_id
 GROUP BY 1 ORDER BY 1;
