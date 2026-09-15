-- ============================================================================
-- CHECK B, CANONICAL: the required action derived from the signed target
-- (2026-09-11, read-only.)
--
-- THE RULE THIS FILE OBEYS. Nothing here may infer BUY or SELL from dM, from
-- which token arrived, or from any "opposite leg" reasoning. The action comes
-- from one signed arithmetic comparison and its sign alone:
--
--   rn1_net_before        = rn1_long_before - rn1_other_before
--   rn1_net_after         = rn1_long_after  - rn1_other_after
--   bettor_target_before  = ratio * rn1_net_before
--   bettor_target_after   = ratio * rn1_net_after
--   required_change       = bettor_target_after - bettor_pre_position
--       > 0 => BUY        < 0 => SELL        = 0 => HOLD
--
-- bettor_pre_position is OUR ACTUAL BOOKED POSITION before the event, rebuilt
-- from our own filled mirror_orders in time order -- not our target, and not
-- an assumption. Where we never traded the market it is 0, which is a real
-- state (FLAT), not missing data.
--
-- WHY THIS IS THE CANONICAL DERIVATION. his_net = long_shares - other_shares
-- (analytics/mirror.py:125) and ledger_net is "long-token shares BY OUR
-- BOOKING" -- ONE signed number per market. The mirror's whole control law is
-- to hold ratio x that signed figure. So the required action is definitionally
-- the difference between where the target now is and where we actually are.
-- Any label-based shortcut ("he bought the other leg, so we sell") is a
-- THEOREM ABOUT that arithmetic under assumptions, not the arithmetic itself,
-- and it is exactly the shortcut that produced the false invariant.
--
-- THE HOLD BAND. Strict equality would make HOLD nearly empty and would call a
-- 0.01-share difference an order. The venue takes whole shares, so a required
-- change under half a share cannot be executed and is HOLD. Statement 1
-- reports the strict-zero count beside it so the band is visible, not hidden.
--
-- THE RATIO. mirror_books.ratio where we actually opened a book, else the
-- standing 10% copy ratio. The ratio is a positive scalar, so where
-- bettor_pre_position = 0 the SIGN of required_change -- and therefore the
-- classification -- is completely ratio-invariant. It can only matter on the
-- 09-06..09-10 subset where we held something. That is stated rather than
-- glossed, because it bounds how much the ratio choice can move the answer.
--
-- THE FOUR DESIGNATION MECHANISMS, and an honest limit. _choose_long
-- (mirror_shadow.py:748) has a branch that picks the long token by HIS LARGER
-- POSITION and branches that take it from the venue's intent; mirror_live.py
-- :13958 adds long_from='catalogue'. Which branch ran is NOT persisted for a
-- book row -- only refusal rows keep long_from. So the split is built from
-- evidence that does exist, and one class is proven rather than assumed:
--   CATALOGUE_LONG            long_from = 'catalogue', direct evidence
--   INTENT_DESIGNATED_LONG    long_asset was his STRICTLY SMALLER leg at the
--                             designation timestamp -- the max-by-position
--                             branch CANNOT have chosen it, so this is proof
--   POSITION_CONSISTENT_LONG  it was his >= leg: consistent with the position
--                             branch but NOT proof, and labelled as such
--   NO_POSITION_AT_DESIGNATION  he had no fills yet; no branch is inferable
-- POST_OPEN_CROSSOVER is separate and orthogonal: the designation is made once
-- at open and never revisited, so it is TRUE when the identity of his larger
-- leg at the event differs from what it was at the designation timestamp.
--
-- Read-only: six SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE CANONICAL DERIVATION: action from sign(required_change) =='
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS desig_at,
         COALESCE(ratio, 0.10) AS ratio, 1 AS pref,
         'book row -- branch not recorded' AS long_from
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 2,
         CASE WHEN long_from = 'catalogue' THEN 'catalogue'
              WHEN long_from IS NULL OR long_from = '' THEN 'mapper -- unlabelled'
              ELSE long_from END
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), desig AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset, d.slug,
         d.desig_at, d.ratio, d.long_from,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.desig_at
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN desig d ON d.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px,
         d.long_asset, d.other_asset, d.slug, d.desig_at, d.ratio,
         sum(CASE WHEN c.asset = d.long_asset  THEN c.sh ELSE 0 END) OVER w AS long_after,
         sum(CASE WHEN c.asset = d.other_asset THEN c.sh ELSE 0 END) OVER w AS other_after
    FROM canon c JOIN desig d ON d.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*,
         c.long_after  - CASE WHEN c.asset = c.long_asset  THEN c.sh ELSE 0 END AS long_before,
         c.other_after - CASE WHEN c.asset = c.other_asset THEN c.sh ELSE 0 END AS other_before,
         (SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
            FROM mirror_orders o
           WHERE o.us_market_slug = c.slug AND o.filled > 0
             AND o.done_at IS NOT NULL AND o.done_at <= c.ts) AS bettor_pre_position
    FROM cum c
), act AS (
  SELECT e.*,
         GREATEST(LEAST(e.long_after, e.other_after)
                  - LEAST(e.long_before, e.other_before), 0) AS d_m,
         (e.long_after - e.other_after) AS net_after,
         e.ratio * (e.long_after - e.other_after)
           - COALESCE(e.bettor_pre_position, 0) AS required_change
    FROM ev e
)
SELECT CASE WHEN d_m > 0.000001 THEN 'B dM COMPLETION EVENT'
            ELSE 'A ENTRY / RESIDUAL-OPENING EVENT' END AS event_class,
       CASE WHEN required_change >  0.5 THEN '1 BUY'
            WHEN required_change < -0.5 THEN '2 SELL'
            ELSE                             '3 HOLD (sub-share, not executable)'
       END AS required_action,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_all_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM,
       round(sum(d_m * px)::numeric, 0) AS dM_capital,
       count(*) FILTER (WHERE required_change = 0) AS strictly_zero_change,
       round(avg(required_change)::numeric, 2) AS mean_required_change
  FROM act GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 2. THE FOUR DESIGNATION MECHANISMS, each with its action split =='
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS desig_at,
         COALESCE(ratio, 0.10) AS ratio, 1 AS pref,
         'book row -- branch not recorded' AS long_from
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 2,
         CASE WHEN long_from = 'catalogue' THEN 'catalogue'
              WHEN long_from IS NULL OR long_from = '' THEN 'mapper -- unlabelled'
              ELSE long_from END
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), desig AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset, d.slug,
         d.desig_at, d.ratio, d.long_from,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.desig_at
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN desig d ON d.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px,
         d.long_asset, d.other_asset, d.slug, d.desig_at, d.ratio, d.long_from,
         sum(CASE WHEN c.asset = d.long_asset  THEN c.sh ELSE 0 END) OVER w AS long_after,
         sum(CASE WHEN c.asset = d.other_asset THEN c.sh ELSE 0 END) OVER w AS other_after
    FROM canon c JOIN desig d ON d.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), dp AS (
  -- his two legs as they stood at the designation timestamp
  SELECT DISTINCT ON (condition_id) condition_id,
         long_after AS long_at_desig, other_after AS other_at_desig
    FROM cum WHERE ts <= desig_at
   ORDER BY condition_id, ts DESC, tx_hash DESC, asset DESC
), ev AS (
  SELECT c.*, dp.long_at_desig, dp.other_at_desig,
         c.long_after  - CASE WHEN c.asset = c.long_asset  THEN c.sh ELSE 0 END AS long_before,
         c.other_after - CASE WHEN c.asset = c.other_asset THEN c.sh ELSE 0 END AS other_before,
         (SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
            FROM mirror_orders o
           WHERE o.us_market_slug = c.slug AND o.filled > 0
             AND o.done_at IS NOT NULL AND o.done_at <= c.ts) AS bettor_pre_position
    FROM cum c LEFT JOIN dp ON dp.condition_id = c.condition_id
), act AS (
  SELECT e.*,
         GREATEST(LEAST(e.long_after, e.other_after)
                  - LEAST(e.long_before, e.other_before), 0) AS d_m,
         e.ratio * (e.long_after - e.other_after)
           - COALESCE(e.bettor_pre_position, 0) AS required_change,
         CASE WHEN e.long_from = 'catalogue'
                THEN '3 CATALOGUE_LONG (his position did not choose it)'
              WHEN e.long_at_desig IS NULL
                THEN '4 NO_POSITION_AT_DESIGNATION (branch not inferable)'
              WHEN e.long_at_desig < e.other_at_desig
                THEN '2 INTENT_DESIGNATED_LONG (PROVEN: long was his smaller leg)'
              ELSE '1 POSITION_CONSISTENT_LONG (consistent, not proven)'
         END AS designation_class,
         CASE WHEN e.long_at_desig IS NULL THEN 'crossover unknown'
              WHEN (e.long_at_desig >= e.other_at_desig)
                   IS DISTINCT FROM (e.long_before >= e.other_before)
                THEN 'POST_OPEN_CROSSOVER = TRUE'
              ELSE 'POST_OPEN_CROSSOVER = false' END AS crossover_before_event
    FROM ev e
)
SELECT designation_class, crossover_before_event,
       count(*) AS events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS dM_capital,
       round((100.0 * count(*) FILTER (WHERE required_change >  0.5) / count(*))::numeric, 2) AS pct_BUY,
       round((100.0 * count(*) FILTER (WHERE required_change < -0.5) / count(*))::numeric, 2) AS pct_SELL,
       round((100.0 * count(*) FILTER (WHERE abs(required_change) <= 0.5) / count(*))::numeric, 2) AS pct_HOLD
  FROM act WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 3. BETTOR PRE-POSITION CLASS, independently, with its action split =='
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS desig_at,
         COALESCE(ratio, 0.10) AS ratio, 1 AS pref
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 2
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), desig AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset, d.slug,
         d.desig_at, d.ratio,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.desig_at
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN desig d ON d.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px,
         d.long_asset, d.other_asset, d.slug, d.ratio,
         sum(CASE WHEN c.asset = d.long_asset  THEN c.sh ELSE 0 END) OVER w AS long_after,
         sum(CASE WHEN c.asset = d.other_asset THEN c.sh ELSE 0 END) OVER w AS other_after
    FROM canon c JOIN desig d ON d.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), act AS (
  SELECT c.*,
         c.long_after  - CASE WHEN c.asset = c.long_asset  THEN c.sh ELSE 0 END AS long_before,
         c.other_after - CASE WHEN c.asset = c.other_asset THEN c.sh ELSE 0 END AS other_before,
         COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
            FROM mirror_orders o
           WHERE o.us_market_slug = c.slug AND o.filled > 0
             AND o.done_at IS NOT NULL AND o.done_at <= c.ts), 0) AS bpp
    FROM cum c
), f AS (
  SELECT a.*,
         GREATEST(LEAST(a.long_after, a.other_after)
                  - LEAST(a.long_before, a.other_before), 0) AS d_m,
         a.ratio * (a.long_after - a.other_after) - a.bpp AS required_change
    FROM act a
)
SELECT CASE WHEN abs(bpp) < 0.5 THEN '1 FLAT'
            WHEN bpp > 0        THEN '2 LONG'
            ELSE                     '3 SHORT' END AS bettor_pre_position_class,
       count(*) AS events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS dM_capital,
       round((100.0 * count(*) FILTER (WHERE required_change >  0.5) / count(*))::numeric, 2) AS pct_BUY,
       round((100.0 * count(*) FILTER (WHERE required_change < -0.5) / count(*))::numeric, 2) AS pct_SELL,
       round((100.0 * count(*) FILTER (WHERE abs(required_change) <= 0.5) / count(*))::numeric, 2) AS pct_HOLD,
       round(avg(bpp)::numeric, 1) AS mean_pre_position
  FROM f WHERE d_m > 0.000001
 GROUP BY 1 ORDER BY 1;


\echo '== 4. HOW MUCH OF THE dM COMPLETION POPULATION REQUIRES A BUY =='
-- The question the bridge turns on. If a material share of genuine dM
-- completions require a BUY, then ASK-side depth -- which copy_probes does
-- retain -- makes those completions measurable, and "fully depth-measured
-- matched pairs = 0" cannot stand as written.
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS desig_at,
         COALESCE(ratio, 0.10) AS ratio, 1 AS pref
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 2
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), desig AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset, d.slug, d.ratio,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.desig_at
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN desig d ON d.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), rp AS (
  SELECT c.id, EXISTS (SELECT 1 FROM copy_probes p
                        WHERE p.trade_id = c.id AND p.book_ok
                          AND p.best_ask IS NOT NULL) AS has_ask_ladder
    FROM canon c
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.asset, c.sh, c.px,
         d.long_asset, d.other_asset, d.slug, d.ratio,
         sum(CASE WHEN c.asset = d.long_asset  THEN c.sh ELSE 0 END) OVER w AS long_after,
         sum(CASE WHEN c.asset = d.other_asset THEN c.sh ELSE 0 END) OVER w AS other_after
    FROM canon c JOIN desig d ON d.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), f AS (
  SELECT c.*, rp.has_ask_ladder,
         GREATEST(LEAST(c.long_after, c.other_after)
                  - LEAST(c.long_after - CASE WHEN c.asset = c.long_asset THEN c.sh ELSE 0 END,
                          c.other_after - CASE WHEN c.asset = c.other_asset THEN c.sh ELSE 0 END), 0) AS d_m,
         c.ratio * (c.long_after - c.other_after)
           - COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
                         FROM mirror_orders o
                        WHERE o.us_market_slug = c.slug AND o.filled > 0
                          AND o.done_at IS NOT NULL AND o.done_at <= c.ts), 0) AS required_change
    FROM cum c JOIN rp ON rp.id = c.id
)
SELECT CASE WHEN required_change >  0.5 THEN 'COMPLETION REQUIRING A BUY'
            WHEN required_change < -0.5 THEN 'COMPLETION REQUIRING A SELL'
            ELSE                             'COMPLETION REQUIRING NO ORDER' END AS completion_action,
       CASE WHEN has_ask_ladder THEN 'fill-specific ASK ladder retained'
            ELSE 'no ask ladder for this fill' END AS ask_side_evidence,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM,
       round(sum(d_m * px)::numeric, 0) AS dM_capital
  FROM f WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 5. SANITY: does the derivation reproduce the label shortcut, or not? =='
-- NOT a classifier -- the action above is already fixed by sign. This only
-- measures how often the discarded shortcut ("he bought the other leg so we
-- sell") would have agreed with the arithmetic. A high disagreement rate is
-- the quantitative statement of why the shortcut was wrong.
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS desig_at,
         COALESCE(ratio, 0.10) AS ratio, 1 AS pref
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 2
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), desig AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset, d.slug, d.ratio,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.desig_at
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN desig d ON d.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.asset, c.sh, c.px,
         d.long_asset, d.other_asset, d.slug, d.ratio,
         sum(CASE WHEN c.asset = d.long_asset  THEN c.sh ELSE 0 END) OVER w AS long_after,
         sum(CASE WHEN c.asset = d.other_asset THEN c.sh ELSE 0 END) OVER w AS other_after
    FROM canon c JOIN desig d ON d.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), f AS (
  SELECT c.*,
         GREATEST(LEAST(c.long_after, c.other_after)
                  - LEAST(c.long_after - CASE WHEN c.asset = c.long_asset THEN c.sh ELSE 0 END,
                          c.other_after - CASE WHEN c.asset = c.other_asset THEN c.sh ELSE 0 END), 0) AS d_m,
         c.ratio * (c.long_after - c.other_after)
           - COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
                         FROM mirror_orders o
                        WHERE o.us_market_slug = c.slug AND o.filled > 0
                          AND o.done_at IS NOT NULL AND o.done_at <= c.ts), 0) AS required_change
    FROM cum c
)
SELECT CASE WHEN asset = other_asset THEN 'shortcut would say SELL (fill on other leg)'
            WHEN asset = long_asset  THEN 'shortcut would say BUY  (fill on long leg)'
            ELSE 'shortcut undefined (token matches neither leg)' END AS discarded_shortcut,
       CASE WHEN required_change >  0.5 THEN 'arithmetic says BUY'
            WHEN required_change < -0.5 THEN 'arithmetic says SELL'
            ELSE                             'arithmetic says HOLD' END AS canonical_action,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS dM_capital
  FROM f WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 6. WORKED EXAMPLES carrying every field the derivation used =='
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS desig_at,
         COALESCE(ratio, 0.10) AS ratio, 1 AS pref,
         'book row -- branch not recorded' AS long_from, 'A book' AS desig_source
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 2,
         COALESCE(NULLIF(long_from, ''), 'mapper -- unlabelled'), 'B refusal'
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), desig AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset, d.slug,
         d.desig_at, d.ratio, d.long_from, d.desig_source,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.desig_at
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN desig d ON d.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.asset, c.sh, c.px,
         d.long_asset, d.other_asset, d.slug, d.ratio, d.long_from,
         d.desig_at, d.desig_source,
         sum(CASE WHEN c.asset = d.long_asset  THEN c.sh ELSE 0 END) OVER w AS long_after,
         sum(CASE WHEN c.asset = d.other_asset THEN c.sh ELSE 0 END) OVER w AS other_after
    FROM canon c JOIN desig d ON d.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), dp AS (
  SELECT DISTINCT ON (condition_id) condition_id,
         long_after AS long_at_desig, other_after AS other_at_desig
    FROM cum WHERE ts <= desig_at
   ORDER BY condition_id, ts DESC, asset DESC
), f AS (
  SELECT c.*, dp.long_at_desig, dp.other_at_desig,
         c.long_after  - CASE WHEN c.asset = c.long_asset  THEN c.sh ELSE 0 END AS long_before,
         c.other_after - CASE WHEN c.asset = c.other_asset THEN c.sh ELSE 0 END AS other_before,
         COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
                     FROM mirror_orders o
                    WHERE o.us_market_slug = c.slug AND o.filled > 0
                      AND o.done_at IS NOT NULL AND o.done_at <= c.ts), 0) AS bpp
    FROM cum c LEFT JOIN dp ON dp.condition_id = c.condition_id
), g AS (
  SELECT f.*,
         GREATEST(LEAST(f.long_after, f.other_after)
                  - LEAST(f.long_before, f.other_before), 0) AS d_m,
         f.ratio * (f.long_after - f.other_after) - f.bpp AS required_change,
         CASE WHEN f.bpp > 0.5 THEN 'LONG' WHEN f.bpp < -0.5 THEN 'SHORT' ELSE 'FLAT' END AS pre_cls
    FROM f
), h AS (
  SELECT g.*,
         CASE WHEN required_change >  0.5 THEN 'BUY'
              WHEN required_change < -0.5 THEN 'SELL' ELSE 'HOLD' END AS action,
         row_number() OVER (PARTITION BY
           CASE WHEN required_change > 0.5 THEN 'BUY'
                WHEN required_change < -0.5 THEN 'SELL' ELSE 'HOLD' END, pre_cls
           ORDER BY d_m DESC) AS rn
    FROM g WHERE d_m > 0.000001
)
SELECT action, pre_cls AS bettor_pre_position_class,
       left(condition_id, 10) AS condition, slug,
       to_char(ts, 'MM-DD HH24:MI:SS') AS fill_ts,
       desig_source, long_from,
       to_char(desig_at, 'MM-DD HH24:MI') AS designation_ts,
       round(long_before::numeric, 1)  AS rn1_long_before,
       round(other_before::numeric, 1) AS rn1_other_before,
       round(long_after::numeric, 1)   AS rn1_long_after,
       round(other_after::numeric, 1)  AS rn1_other_after,
       round((long_before - other_before)::numeric, 1) AS rn1_net_before,
       round((long_after  - other_after)::numeric, 1)  AS rn1_net_after,
       ratio,
       round((ratio * (long_before - other_before))::numeric, 1) AS bettor_target_before,
       round((ratio * (long_after  - other_after))::numeric, 1)  AS bettor_target_after,
       round(bpp::numeric, 1) AS bettor_pre_position,
       round(required_change::numeric, 1) AS REQUIRED_POSITION_CHANGE,
       round(d_m::numeric, 1) AS delta_M, px AS rn1_fill_price
  FROM h WHERE rn <= 2 ORDER BY action, pre_cls, delta_M DESC;
