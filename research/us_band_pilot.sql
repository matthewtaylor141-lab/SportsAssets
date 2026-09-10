-- ============================================================================
-- US PRICE-BAND PILOT STUDY  (2026-09-10)
--
-- A SMALL, HONEST STUDY ON A SHORT PANEL. This is not the 12-month study the
-- research brief asks for -- that one is not testable, for the reasons in
-- docs/us-band-study-data-audit.md. This is the pilot that brief's section 15
-- would classify as evidence about METHOD, not about expectancy.
--
-- THE PANEL. mirror_shadow.bid/ask is the venue's executable top of book for
-- the LONG SIDE of us_market_slug: workers/mirror_shadow.py:2498 reads one
-- paced BBO per slug, and _paced_bbo takes max(bids) and min(asks), so these
-- are the real touch prices, not a mid or a mark. The unit of analysis is
-- therefore the US SLUG, never the condition_id -- one condition can carry
-- several US contracts and their books are different.
--
-- WHAT IS MODELLED (the brief's Model 2, top-of-book, plus its Model 4 hybrid
-- exit). Entry crosses the ask, so it is a TAKER. The target is a resting sell
-- at a limit, so it is a MAKER and fills at exactly the limit. The stop is an
-- aggressive exit, so it is a TAKER and fills at whatever the bid actually was
-- when it first crossed -- which may be well below the intended stop, and that
-- gap is kept, not smoothed.
--
-- WHAT IS NOT MODELLED, and why no fill-based (Model 3) claim can be made:
-- there is no bid-side depth anywhere in the database, so queue position for
-- the resting target cannot be reconstructed. Every "target" here assumes the
-- rest filled when the bid reached it. Our own measured resting-sell fill rate
-- is 30.7%, so the true target count is materially lower than the count below.
-- Read every target_first number as an UPPER BOUND.
--
-- FEES. 6% of shares x p x (1-p) per contract on TAKING fills only, which is
-- what the venue's own settlement rows showed (n=6, five of six at that rate,
-- and the one position built entirely from rests paid nothing). An estimate,
-- not a published schedule.
--
-- NO LOOKAHEAD. An entry is decided from the row at t0 alone. Every outcome
-- reads strictly at > t0. Settlement is only credited to markets that resolved
-- after the entry.
--
-- Read-only: five SELECTs. Nothing here writes.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 1. THE PANEL ITSELF. What we are standing on before any strategy is applied.
-- ---------------------------------------------------------------------------
\echo '== 1. PANEL =='
WITH q AS (
  SELECT us_market_slug AS slug, at, bid::float8 AS bid, ask::float8 AS ask
    FROM mirror_shadow
   WHERE us_market_slug IS NOT NULL AND bid IS NOT NULL AND ask IS NOT NULL
     AND bid > 0 AND ask < 1 AND ask > bid
), n AS (SELECT slug, count(*) AS c FROM q GROUP BY 1)
SELECT count(*) AS obs,
       count(DISTINCT q.slug) AS slugs,
       (SELECT count(*) FROM n WHERE c >= 30) AS slugs_ge_30_obs,
       (SELECT count(*) FROM n WHERE c >= 100) AS slugs_ge_100_obs,
       min(at)::date AS first_day, max(at)::date AS last_day,
       count(DISTINCT date_trunc('day', at)) AS days,
       round(avg(ask - bid)::numeric, 4) AS mean_spread,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY ask - bid))::numeric, 4) AS median_spread,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY ask - bid))::numeric, 4) AS p90_spread
  FROM q;


-- ---------------------------------------------------------------------------
-- 2. THE ENTRY-BAND SWEEP. Symmetric +/-0.05 offsets from the executed ask,
--    one entry per slug per band (the FIRST time the ask enters that band --
--    no pyramiding, no overlapping entries in the same contract).
--    `events` counts distinct conditions so the clustering is visible: three
--    soccer 1X2 contracts on one match are three slugs but ONE event.
-- ---------------------------------------------------------------------------
\echo '== 2. ENTRY BANDS, target = ask+0.05 (resting), stop = ask-0.05 (aggressive) =='
WITH q AS (
  SELECT us_market_slug AS slug, long_asset, condition_id, at,
         bid::float8 AS bid, ask::float8 AS ask
    FROM mirror_shadow
   WHERE us_market_slug IS NOT NULL AND bid IS NOT NULL AND ask IS NOT NULL
     AND bid > 0 AND ask < 1 AND ask > bid
), k AS (SELECT slug FROM q GROUP BY 1 HAVING count(*) >= 30),
p AS (SELECT q.* FROM q JOIN k USING (slug)),
b (lo, hi) AS (VALUES
  (0.10,0.15),(0.15,0.20),(0.20,0.25),(0.25,0.30),(0.30,0.35),(0.35,0.40),
  (0.40,0.45),(0.45,0.50),(0.50,0.55),(0.55,0.60),(0.60,0.65),(0.65,0.70),
  (0.70,0.75),(0.75,0.80),(0.80,0.85),(0.85,0.90)),
e AS (
  SELECT DISTINCT ON (p.slug, b.lo)
         p.slug, p.long_asset, p.condition_id, b.lo, b.hi, p.at AS t0, p.ask AS px
    FROM p JOIN b ON p.ask >= b.lo AND p.ask < b.hi
   ORDER BY p.slug, b.lo, p.at
),
x AS (
  SELECT e.*,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid >= e.px + 0.05) AS t_tgt,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid <= e.px - 0.05) AS t_stp,
    (SELECT f.bid FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid <= e.px - 0.05
      ORDER BY f.at LIMIT 1) AS stp_bid,
    (SELECT max((m.resolved_prices ->> kt.outcome_index)::float8)
       FROM market_tokens kt JOIN markets m ON m.condition_id = kt.condition_id
      WHERE kt.token_id = e.long_asset AND m.resolved
        AND jsonb_typeof(m.resolved_prices) = 'array'
        AND (m.resolved_at IS NULL OR m.resolved_at > e.t0)) AS payout
    FROM e
),
o AS (
  SELECT x.*,
    CASE WHEN t_tgt IS NOT NULL AND (t_stp IS NULL OR t_tgt < t_stp) THEN 'target'
         WHEN t_stp IS NOT NULL AND (t_tgt IS NULL OR t_stp < t_tgt) THEN 'stop'
         WHEN payout IS NOT NULL THEN 'settled'
         ELSE 'censored' END AS outcome,
    CASE WHEN t_tgt IS NOT NULL AND (t_stp IS NULL OR t_tgt < t_stp) THEN 0.05
         WHEN t_stp IS NOT NULL AND (t_tgt IS NULL OR t_stp < t_tgt) THEN stp_bid - px
         WHEN payout IS NOT NULL THEN payout - px
         ELSE NULL END AS gross
    FROM x
)
SELECT lo, hi,
       count(*) AS entries,
       count(DISTINCT condition_id) AS events,
       count(*) FILTER (WHERE outcome = 'target')   AS target_first,
       count(*) FILTER (WHERE outcome = 'stop')     AS stop_first,
       count(*) FILTER (WHERE outcome = 'settled')  AS settled,
       count(*) FILTER (WHERE outcome = 'censored') AS censored,
       round((100.0 * count(*) FILTER (WHERE outcome = 'target')
              / NULLIF(count(*) FILTER (WHERE outcome IN ('target','stop')), 0))::numeric, 1)
         AS target_first_pct,
       round(avg(px)::numeric, 4) AS avg_entry_px,
       round(avg(gross)::numeric, 4) AS gross_per_share,
       round(avg(gross - 0.06 * px * (1 - px)
                 - CASE WHEN outcome = 'stop' THEN 0.06 * stp_bid * (1 - stp_bid)
                        ELSE 0 END)::numeric, 4) AS net_per_share,
       round((100.0 * avg(gross - 0.06 * px * (1 - px)
                 - CASE WHEN outcome = 'stop' THEN 0.06 * stp_bid * (1 - stp_bid)
                        ELSE 0 END)
              / NULLIF(avg(px) FILTER (WHERE gross IS NOT NULL), 0))::numeric, 2) AS net_roi_pct
  FROM o GROUP BY 1, 2 ORDER BY 1;


-- ---------------------------------------------------------------------------
-- 3. THE HEADLINE CONFIGURATION AND ITS NEIGHBOURS. Entry 0.30-0.35 with
--    ABSOLUTE target/stop levels, the way the brief states it. The break-even
--    column is computed from the REALISED average win and loss on these very
--    trades, not from the nominal levels: required = avg_loss/(avg_win+avg_loss).
--    `margin` is observed minus required. Positive is the only interesting sign.
-- ---------------------------------------------------------------------------
\echo '== 3. ENTRY 0.30-0.35, absolute target/stop grid, realised break-even =='
WITH q AS (
  SELECT us_market_slug AS slug, long_asset, condition_id, at,
         bid::float8 AS bid, ask::float8 AS ask
    FROM mirror_shadow
   WHERE us_market_slug IS NOT NULL AND bid IS NOT NULL AND ask IS NOT NULL
     AND bid > 0 AND ask < 1 AND ask > bid
), k AS (SELECT slug FROM q GROUP BY 1 HAVING count(*) >= 30),
p AS (SELECT q.* FROM q JOIN k USING (slug)),
e AS (
  SELECT DISTINCT ON (p.slug) p.slug, p.long_asset, p.condition_id,
         p.at AS t0, p.ask AS px
    FROM p WHERE p.ask >= 0.30 AND p.ask < 0.35
   ORDER BY p.slug, p.at
),
g (tgt, stp) AS (VALUES
  (0.35,0.30),(0.35,0.25),(0.35,0.20),
  (0.38,0.28),(0.38,0.25),
  (0.40,0.30),(0.40,0.25),(0.40,0.20),(0.40,0.15),
  (0.45,0.25),(0.45,0.20),
  (0.50,0.25),(0.50,0.20)),
x AS (
  SELECT e.*, g.tgt, g.stp,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid >= g.tgt) AS t_tgt,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid <= g.stp) AS t_stp,
    (SELECT f.bid FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid <= g.stp
      ORDER BY f.at LIMIT 1) AS stp_bid,
    (SELECT max((m.resolved_prices ->> kt.outcome_index)::float8)
       FROM market_tokens kt JOIN markets m ON m.condition_id = kt.condition_id
      WHERE kt.token_id = e.long_asset AND m.resolved
        AND jsonb_typeof(m.resolved_prices) = 'array'
        AND (m.resolved_at IS NULL OR m.resolved_at > e.t0)) AS payout
    FROM e CROSS JOIN g
),
o AS (
  SELECT x.*,
    CASE WHEN t_tgt IS NOT NULL AND (t_stp IS NULL OR t_tgt < t_stp) THEN 'target'
         WHEN t_stp IS NOT NULL AND (t_tgt IS NULL OR t_stp < t_tgt) THEN 'stop'
         WHEN payout IS NOT NULL THEN 'settled'
         ELSE 'censored' END AS outcome
    FROM x
),
n AS (
  SELECT o.*,
    CASE outcome
      WHEN 'target'  THEN  tgt - px - 0.06 * px * (1 - px)
      WHEN 'stop'    THEN  stp_bid - px - 0.06 * px * (1 - px)
                            - 0.06 * stp_bid * (1 - stp_bid)
      WHEN 'settled' THEN  payout - px - 0.06 * px * (1 - px)
      ELSE NULL END AS net
    FROM o
)
SELECT tgt, stp,
       count(*) AS entries,
       count(*) FILTER (WHERE outcome = 'target')   AS target_first,
       count(*) FILTER (WHERE outcome = 'stop')     AS stop_first,
       count(*) FILTER (WHERE outcome = 'settled')  AS settled,
       count(*) FILTER (WHERE outcome = 'censored') AS censored,
       round(avg(net) FILTER (WHERE outcome = 'target')::numeric, 4) AS avg_win,
       round((-avg(net) FILTER (WHERE outcome = 'stop'))::numeric, 4) AS avg_loss,
       round((100.0 * (-avg(net) FILTER (WHERE outcome = 'stop'))
              / NULLIF(avg(net) FILTER (WHERE outcome = 'target')
                       - avg(net) FILTER (WHERE outcome = 'stop'), 0))::numeric, 1)
         AS required_pct,
       round((100.0 * count(*) FILTER (WHERE outcome = 'target')
              / NULLIF(count(*) FILTER (WHERE outcome IN ('target','stop')), 0))::numeric, 1)
         AS observed_pct,
       round(avg(net)::numeric, 4) AS net_per_share,
       round((100.0 * avg(net) / NULLIF(avg(px) FILTER (WHERE net IS NOT NULL), 0))::numeric, 2) AS net_roi_pct
  FROM n GROUP BY 1, 2 ORDER BY 1, 2;


-- ---------------------------------------------------------------------------
-- 4. THE HEADLINE CELL BY SPORT. Entry 0.30-0.35, target 0.40, stop 0.20 --
--    exactly the configuration in the brief -- split by the sport on the
--    global market row. Minimum-sample discipline is the reader's job: the
--    entries column is printed first for that reason.
-- ---------------------------------------------------------------------------
\echo '== 4. ENTRY 0.30-0.35 -> TARGET 0.40 / STOP 0.20, by sport =='
WITH q AS (
  SELECT us_market_slug AS slug, long_asset, condition_id, at,
         bid::float8 AS bid, ask::float8 AS ask
    FROM mirror_shadow
   WHERE us_market_slug IS NOT NULL AND bid IS NOT NULL AND ask IS NOT NULL
     AND bid > 0 AND ask < 1 AND ask > bid
), k AS (SELECT slug FROM q GROUP BY 1 HAVING count(*) >= 30),
p AS (SELECT q.* FROM q JOIN k USING (slug)),
e AS (
  SELECT DISTINCT ON (p.slug) p.slug, p.long_asset, p.condition_id,
         p.at AS t0, p.ask AS px
    FROM p WHERE p.ask >= 0.30 AND p.ask < 0.35
   ORDER BY p.slug, p.at
),
x AS (
  SELECT e.*,
    COALESCE(NULLIF(mk.sport, 'unclassified'), 'unclassified') AS sport,
    split_part(e.slug, '-', 1) AS family,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid >= 0.40) AS t_tgt,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid <= 0.20) AS t_stp,
    (SELECT f.bid FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid <= 0.20
      ORDER BY f.at LIMIT 1) AS stp_bid,
    (SELECT max((m.resolved_prices ->> kt.outcome_index)::float8)
       FROM market_tokens kt JOIN markets m ON m.condition_id = kt.condition_id
      WHERE kt.token_id = e.long_asset AND m.resolved
        AND jsonb_typeof(m.resolved_prices) = 'array'
        AND (m.resolved_at IS NULL OR m.resolved_at > e.t0)) AS payout
    FROM e LEFT JOIN markets mk ON mk.condition_id = e.condition_id
),
n AS (
  SELECT x.*,
    CASE WHEN t_tgt IS NOT NULL AND (t_stp IS NULL OR t_tgt < t_stp) THEN 'target'
         WHEN t_stp IS NOT NULL AND (t_tgt IS NULL OR t_stp < t_tgt) THEN 'stop'
         WHEN payout IS NOT NULL THEN 'settled'
         ELSE 'censored' END AS outcome
    FROM x
),
z AS (
  SELECT n.*,
    CASE outcome
      WHEN 'target'  THEN  0.40 - px - 0.06 * px * (1 - px)
      WHEN 'stop'    THEN  stp_bid - px - 0.06 * px * (1 - px)
                            - 0.06 * stp_bid * (1 - stp_bid)
      WHEN 'settled' THEN  payout - px - 0.06 * px * (1 - px)
      ELSE NULL END AS net
    FROM n
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS entries,
       count(DISTINCT condition_id) AS events,
       count(*) FILTER (WHERE outcome = 'target')   AS target_first,
       count(*) FILTER (WHERE outcome = 'stop')     AS stop_first,
       count(*) FILTER (WHERE outcome = 'settled')  AS settled,
       count(*) FILTER (WHERE outcome = 'censored') AS censored,
       round((100.0 * count(*) FILTER (WHERE outcome = 'target')
              / NULLIF(count(*) FILTER (WHERE outcome IN ('target','stop')), 0))::numeric, 1)
         AS observed_pct,
       round(avg(net)::numeric, 4) AS net_per_share,
       round((100.0 * avg(net) / NULLIF(avg(px) FILTER (WHERE net IS NOT NULL), 0))::numeric, 2) AS net_roi_pct
  FROM z GROUP BY ROLLUP (sport) ORDER BY 2 DESC;


-- ---------------------------------------------------------------------------
-- 5. THE SAME CELL BY SLUG FAMILY, and the holding-time distribution. The
--    family prefix is the market type: aec- moneyline/2-way, atc- per-outcome,
--    tsc- total, asc- spread, astatc- prop. The brief forbids pooling market
--    types without reporting them apart; this is that report.
-- ---------------------------------------------------------------------------
\echo '== 5. SAME CELL BY MARKET-TYPE FAMILY, with time to exit =='
WITH q AS (
  SELECT us_market_slug AS slug, long_asset, condition_id, at,
         bid::float8 AS bid, ask::float8 AS ask
    FROM mirror_shadow
   WHERE us_market_slug IS NOT NULL AND bid IS NOT NULL AND ask IS NOT NULL
     AND bid > 0 AND ask < 1 AND ask > bid
), k AS (SELECT slug FROM q GROUP BY 1 HAVING count(*) >= 30),
p AS (SELECT q.* FROM q JOIN k USING (slug)),
e AS (
  SELECT DISTINCT ON (p.slug) p.slug, p.long_asset, p.condition_id,
         p.at AS t0, p.ask AS px
    FROM p WHERE p.ask >= 0.30 AND p.ask < 0.35
   ORDER BY p.slug, p.at
),
x AS (
  SELECT e.*, split_part(e.slug, '-', 1) AS family,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid >= 0.40) AS t_tgt,
    (SELECT min(f.at) FROM p f
      WHERE f.slug = e.slug AND f.at > e.t0 AND f.bid <= 0.20) AS t_stp
    FROM e
)
SELECT family,
       count(*) AS entries,
       count(*) FILTER (WHERE t_tgt IS NOT NULL AND (t_stp IS NULL OR t_tgt < t_stp)) AS target_first,
       count(*) FILTER (WHERE t_stp IS NOT NULL AND (t_tgt IS NULL OR t_stp < t_tgt)) AS stop_first,
       round((100.0 * count(*) FILTER (WHERE t_tgt IS NOT NULL AND (t_stp IS NULL OR t_tgt < t_stp))
              / NULLIF(count(*) FILTER (WHERE t_tgt IS NOT NULL OR t_stp IS NOT NULL), 0))::numeric, 1)
         AS observed_pct,
       round((percentile_cont(0.5) WITHIN GROUP (
                ORDER BY CASE WHEN t_tgt IS NULL AND t_stp IS NULL THEN NULL
                              ELSE extract(epoch FROM LEAST(COALESCE(t_tgt, t_stp),
                                                            COALESCE(t_stp, t_tgt)) - t0) END))::numeric, 0)
         AS median_seconds_to_exit
  FROM x GROUP BY 1 ORDER BY 2 DESC;
