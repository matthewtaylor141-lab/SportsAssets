-- ============================================================================
-- THE MAKER/TAKER DISCRIMINATOR, recovered from cash on a venue that labels
-- neither (2026-09-11, read-only.)
--
-- WHERE THIS CAME FROM. The stratified canonical sample printed raw rows and
-- four of each class could be checked by hand. Two classes behave completely
-- differently, and neither result is approximate.
--
--   SINGLE-LEG transactions (one venue fill, one chain row):
--       poll 2320 @ 0.75      chain 2320 @ 0.759375
--       0.75 + 0.05 x 0.75 x 0.25                 = 0.759375   exact
--       poll   30 @ 0.77      chain   30 @ 0.778855
--       0.77 + 0.05 x 0.77 x 0.23                 = 0.7788550  exact
--       poll  160 @ 0.86      chain  160 @ 0.86602
--       poll  110 @ 0.90      chain  110 @ 0.9045              exact
--
--   MULTI-LEG transactions (several venue fills, one chain row):
--       poll 403 @ 0.32 + 4877 @ 0.33   chain 5280 @ 0.329237
--       size-weighted venue VWAP        = 1738.37 / 5280 = 0.329237  exact
--       poll 1719.74 @ 0.43 + 1038 @ 0.44   chain 2757.74 @ 0.433764
--       VWAP = 1196.208 / 2757.74       = 0.433764               exact
--       poll 890.75 @ 0.71 + 5078 @ 0.72    chain 5968.75 @ 0.718508
--       poll 1366.45 @ 0.45 + 2049 @ 0.46   chain 3415.45 @ 0.455999
--
--   Four for four with the wedge, four for four WITHOUT it. The canonical
--   economic table says the same thing in aggregate: the observed fee is
--   0.009112 per share on single-leg envelopes and 0.000216 on multi-leg --
--   a factor of 42.
--
-- THE HYPOTHESIS, stated so it can fail. A transaction containing SEVERAL of
-- his fills at SEVERAL price levels is one where HIS RESTING ORDERS WERE HIT:
-- he is the MAKER and pays nothing. A transaction containing ONE fill is one
-- where HE CROSSED: he is the TAKER and pays 0.05 x p x (1-p). If that is
-- right, then eps_vwap = chain - venue_VWAP must sit at zero on the multi-leg
-- population and at the wedge on the single-leg one, and the two must not
-- overlap. If instead the multi-leg residual merely looks smaller, or the
-- populations blur into each other, the hypothesis fails and the fee stays
-- UNKNOWN for anything the cash feed did not see directly.
--
-- WHY IT MATTERS MORE THAN ANY OTHER NUMBER HERE. The gross-vs-net pair test
-- reported three fee bases. Where the fee is OBSERVED on every leg his matched
-- edge survives: 2.937% gross becomes 2.658% net and 98.9% of the gross
-- winners stay winners. Where the fee is MODELLED throughout, 2.016% gross
-- collapses to 0.296% net and the matched P&L goes from +$39,930 to -$160,531.
-- That second figure is MY ASSUMPTION of a 5% taker fee on both legs, not his
-- reality. If he is largely a maker, the modelled basis is simply wrong and
-- the matched book is healthy. If he is largely a taker, it stands and the
-- matched thesis is in trouble. Nothing should be built until this is settled.
--
-- THE EARLIER 'k ~ 0' BUCKET WAS ME MISREADING MY OWN TEST. The implied-
-- coefficient statement put 3,089 rows in a k ~ 0 band with a negative mean
-- and I called them an aggregation artifact. 3,089 is EXACTLY the multi-leg
-- envelope count. They were never an artifact; they are the maker population,
-- and the negative sign came from my comparing a chain VWAP against poll's
-- HIGHEST leg rather than against poll's VWAP. This file compares like with
-- like.
--
-- Read-only: five SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE TWO POPULATIONS, measured against the SAME venue VWAP =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash,
         max(vwap)   FILTER (WHERE feed = 'venue') AS v,
         max(vwap)   FILTER (WHERE feed = 'cash')  AS c
    FROM f GROUP BY 1, 2, 3
   HAVING count(*) = 2
), e AS (
  SELECT CASE WHEN n_venue = 1 THEN 'SINGLE-LEG (he crossed?)'
              ELSE 'MULTI-LEG (he was hit?)' END AS leg_shape,
         n_venue, sh_venue, v, c,
         c - v AS eps_vwap,
         c - (v + 0.05 * v * (1 - v)) AS eps_vwap_plus_wedge,
         0.05 * v * (1 - v) AS predicted_wedge
    FROM g
   WHERE v IS NOT NULL AND c IS NOT NULL AND v > 0 AND v < 1
     AND abs(COALESCE(sh_venue, 0) - COALESCE(sh_cash, 0)) < 0.01
)
SELECT leg_shape, count(*) AS envelopes, round(sum(sh_venue)::numeric, 0) AS shares,
       round(avg(v)::numeric, 4) AS avg_venue_vwap,
       round(avg(eps_vwap)::numeric, 8) AS mean_NO_FEE_residual,
       round(avg(abs(eps_vwap))::numeric, 8) AS mean_abs_NO_FEE,
       round(avg(abs(eps_vwap_plus_wedge))::numeric, 8) AS mean_abs_WITH_WEDGE,
       round(avg(predicted_wedge)::numeric, 8) AS avg_predicted_wedge,
       count(*) FILTER (WHERE abs(eps_vwap) < abs(eps_vwap_plus_wedge)) AS looks_MAKER,
       count(*) FILTER (WHERE abs(eps_vwap_plus_wedge) <= abs(eps_vwap)) AS looks_TAKER,
       round((100.0 * count(*) FILTER (WHERE abs(eps_vwap) < abs(eps_vwap_plus_wedge))
              / count(*))::numeric, 3) AS pct_maker
  FROM e GROUP BY 1 ORDER BY 2 DESC;


\echo '== 2. DO THE TWO POPULATIONS OVERLAP, or are they cleanly separated? =='
-- If maker and taker are real categories the zero-fee residual must be tight
-- on one and wide on the other, with no middle ground.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash,
         max(vwap)   FILTER (WHERE feed = 'venue') AS v,
         max(vwap)   FILTER (WHERE feed = 'cash')  AS c
    FROM f GROUP BY 1, 2, 3 HAVING count(*) = 2
), e AS (
  SELECT CASE WHEN n_venue = 1 THEN 'SINGLE-LEG' ELSE 'MULTI-LEG' END AS leg_shape,
         (c - v) / NULLIF(0.05 * v * (1 - v), 0) AS fee_fraction_paid
    FROM g
   WHERE v IS NOT NULL AND c IS NOT NULL AND v > 0.02 AND v < 0.98
     AND abs(COALESCE(sh_venue, 0) - COALESCE(sh_cash, 0)) < 0.01
)
SELECT leg_shape,
       CASE WHEN fee_fraction_paid <  0.05 THEN '1 pays ~NOTHING  (maker)'
            WHEN fee_fraction_paid <  0.50 THEN '2 pays a little'
            WHEN fee_fraction_paid <  0.95 THEN '3 pays partly'
            WHEN fee_fraction_paid <= 1.05 THEN '4 pays the FULL wedge (taker)'
            ELSE                                '5 pays MORE than the wedge'
       END AS bucket,
       count(*) AS envelopes,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY leg_shape))::numeric, 3)
         AS pct_of_shape,
       round(avg(fee_fraction_paid)::numeric, 6) AS avg_fraction,
       round(stddev_samp(fee_fraction_paid)::numeric, 6) AS sd_fraction
  FROM e GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 3. IS LEG COUNT REALLY THE DISCRIMINATOR, or is it clip size? =='
-- A maker gets hit in big pieces; a taker crosses in small ones. If the
-- discriminator is really size rather than leg count the fee fraction will
-- track size WITHIN each shape, and the story changes.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash,
         max(vwap)   FILTER (WHERE feed = 'venue') AS v,
         max(vwap)   FILTER (WHERE feed = 'cash')  AS c
    FROM f GROUP BY 1, 2, 3 HAVING count(*) = 2
), e AS (
  SELECT CASE WHEN n_venue = 1 THEN 'SINGLE-LEG' ELSE 'MULTI-LEG' END AS leg_shape,
         n_venue, sh_venue,
         (c - v) / NULLIF(0.05 * v * (1 - v), 0) AS fee_fraction_paid
    FROM g
   WHERE v IS NOT NULL AND c IS NOT NULL AND v > 0.02 AND v < 0.98
     AND abs(COALESCE(sh_venue, 0) - COALESCE(sh_cash, 0)) < 0.01
)
SELECT leg_shape,
       CASE WHEN sh_venue <    50 THEN '1 under 50 sh'
            WHEN sh_venue <   250 THEN '2 50-250'
            WHEN sh_venue <  1000 THEN '3 250-1k'
            WHEN sh_venue <  5000 THEN '4 1k-5k'
            ELSE                       '5 5k+' END AS clip,
       count(*) AS envelopes,
       round(avg(sh_venue)::numeric, 0) AS avg_shares,
       round(avg(fee_fraction_paid)::numeric, 5) AS avg_fee_fraction,
       round((100.0 * count(*) FILTER (WHERE fee_fraction_paid < 0.05)
              / count(*))::numeric, 2) AS pct_pays_nothing
  FROM e GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 4. HOW MUCH OF HIS BOOK IS MAKER, by shares and by dollars =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.ts, t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares,
         min(ts) AS ts, sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side, min(ts) AS ts,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash,
         max(vwap)   FILTER (WHERE feed = 'venue') AS v,
         max(vwap)   FILTER (WHERE feed = 'cash')  AS c
    FROM f GROUP BY 1, 2, 3 HAVING count(*) = 2
), e AS (
  SELECT date_trunc('week', ts)::date AS wk,
         COALESCE(sh_venue, sh_cash) AS shares, v,
         ((c - v) / NULLIF(0.05 * v * (1 - v), 0)) < 0.05 AS is_maker
    FROM g
   WHERE v IS NOT NULL AND c IS NOT NULL AND v > 0.02 AND v < 0.98
     AND abs(COALESCE(sh_venue, 0) - COALESCE(sh_cash, 0)) < 0.01
)
SELECT wk AS week, count(*) AS envelopes,
       count(*) FILTER (WHERE is_maker) AS maker_envelopes,
       round((100.0 * count(*) FILTER (WHERE is_maker) / count(*))::numeric, 2) AS pct_env_maker,
       round(sum(shares)::numeric, 0) AS shares,
       round((100.0 * sum(shares) FILTER (WHERE is_maker)
              / NULLIF(sum(shares), 0))::numeric, 2) AS PCT_SHARES_MAKER,
       round(sum(shares * v)::numeric, 0) AS dollars,
       round((100.0 * sum(shares * v) FILTER (WHERE is_maker)
              / NULLIF(sum(shares * v), 0))::numeric, 2) AS PCT_DOLLARS_MAKER
  FROM e GROUP BY 1 ORDER BY 1;


\echo '== 5. THE PAIR ECONOMICS REDONE with the maker share respected =='
-- The gross-vs-net test again, but the fee is applied ONLY where the cash
-- feed shows he actually paid it. Where he was a maker the fee is zero, not
-- modelled at 5%. Where a leg was never seen in cash the fee is genuinely
-- UNKNOWN and is NOT modelled at all: that basis is reported with a BRACKET
-- -- the best case (every unseen leg was a maker, fee zero) and the worst
-- case (every unseen leg was a taker, full wedge) -- so a single invented
-- number can never be mistaken for a measurement. The earlier test's
-- -$160,531 was exactly that mistake: it applied the full taker wedge to a
-- population now known to be about half maker.
WITH t AS (
  SELECT t.condition_id, t.outcome_index, t.tx_hash, t.asset, t.side, t.ts,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.condition_id IS NOT NULL
     AND t.ts >= now() - interval '30 days'
), f AS (
  SELECT condition_id, outcome_index, tx_hash, asset, feed,
         count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4, 5
), g AS (
  -- PER ENVELOPE, not per condition. Collapsing to (condition, outcome) here
  -- would make max(vwap) the HIGHEST price he ever paid on that leg rather
  -- than the size-weighted average, and the pair cost would read above $1.00
  -- as an artifact. The size weighting belongs in `m`, below.
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
         -- BEST CASE for an unseen leg: he was a maker and paid nothing.
         CASE WHEN c IS NOT NULL THEN c ELSE v END AS cash_best,
         -- WORST CASE for an unseen leg: he was a taker and paid the wedge.
         CASE WHEN c IS NOT NULL THEN c
              ELSE v + 0.05 * v * (1 - v) END AS cash_worst,
         (v IS NOT NULL AND c IS NULL) AS fee_unknown
    FROM g
), m AS (
  SELECT condition_id,
         bool_or(fee_unknown) AS any_unknown,
         sum(shares) FILTER (WHERE outcome_index = 0) AS y,
         sum(shares) FILTER (WHERE outcome_index = 1) AS n,
         sum(shares * venue_price) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 0), 0) AS v0,
         sum(shares * venue_price) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 1), 0) AS v1,
         sum(shares * cash_best) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 0), 0) AS b0,
         sum(shares * cash_best) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 1), 0) AS b1,
         sum(shares * cash_worst) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 0), 0) AS w0,
         sum(shares * cash_worst) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(shares) FILTER (WHERE outcome_index = 1), 0) AS w1
    FROM leg GROUP BY 1
)
SELECT CASE WHEN any_unknown THEN 'B some legs never seen in cash -- BRACKETED'
            ELSE 'A fee OBSERVED on every leg -- measured' END AS basis,
       count(*) AS markets,
       round(sum(LEAST(y, n))::numeric, 0) AS matched_sh,
       round(avg(v0 + v1)::numeric, 5) AS gross_pair_cost,
       round(avg(1 - (v0 + v1))::numeric, 5) AS gross_matched_edge,
       round(avg(1 - (b0 + b1))::numeric, 5) AS net_edge_BEST_all_maker,
       round(avg(1 - (w0 + w1))::numeric, 5) AS net_edge_WORST_all_taker,
       count(*) FILTER (WHERE v0 + v1 < 1) AS gross_under_one,
       count(*) FILTER (WHERE b0 + b1 < 1) AS under_one_BEST,
       count(*) FILTER (WHERE w0 + w1 < 1) AS under_one_WORST,
       round(sum(LEAST(y, n) * (1 - (v0 + v1)))::numeric, 0) AS gross_matched_pnl,
       round(sum(LEAST(y, n) * (1 - (b0 + b1)))::numeric, 0) AS net_pnl_BEST,
       round(sum(LEAST(y, n) * (1 - (w0 + w1)))::numeric, 0) AS net_pnl_WORST
  FROM m WHERE y > 0 AND n > 0 GROUP BY 1 ORDER BY 2 DESC;
