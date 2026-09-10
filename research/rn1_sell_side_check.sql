-- ============================================================================
-- IS "RN1 NEVER SELLS" REAL, OR IS IT AN INGESTION GAP?  (2026-09-10, read-only.)
--
-- rn1_unmatched_behaviour.sql found ZERO SELL fills for RN1 across 12,928
-- markets and 14 days -- every one of his 158,793 fills is a BUY. That is
-- either the most important fact about him or the most important defect in
-- our pipeline, and the two must not be confused.
--
-- The discriminator is simple: SELL rows either exist in this table or they
-- do not.
--
--   * If OTHER whales have SELL rows and RN1 has none, the pipeline can
--     record a sale and RN1 genuinely never makes one. That is a finding.
--   * If NOBODY has SELL rows, the ingest never writes the side and the
--     result says nothing about RN1 at all.
--   * If RN1 has SELLs outside the 14-day window, the behaviour changed and
--     the window, not the whale, produced the zero.
--
-- Statement 4 adds the venue's own view: `positions.realized_pnl` is
-- non-zero only where shares left a position before settlement, so a book of
-- pure buys held to settlement shows realized_pnl at zero almost everywhere.
-- It is an independent witness that does not read `side` at all.
--
-- Read-only: four SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. DOES THE SIDE COLUMN EVER SAY SELL, FOR ANYONE? =='
SELECT COALESCE(w.username, '(null)') AS whale,
       count(*) AS fills,
       count(*) FILTER (WHERE t.side = 'BUY') AS buys,
       count(*) FILTER (WHERE t.side = 'SELL') AS sells,
       round((100.0 * count(*) FILTER (WHERE t.side = 'SELL')
              / NULLIF(count(*), 0))::numeric, 2) AS sell_pct,
       min(t.ts)::date AS first_day, max(t.ts)::date AS last_day
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE t.ts >= now() - interval '14 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 25;


\echo '== 2. RN1 OVER HIS WHOLE HISTORY, BY MONTH -- did he ever sell? =='
SELECT to_char(date_trunc('month', t.ts), 'YYYY-MM') AS month,
       count(*) AS fills,
       count(*) FILTER (WHERE t.side = 'BUY') AS buys,
       count(*) FILTER (WHERE t.side = 'SELL') AS sells,
       count(DISTINCT t.condition_id) AS markets,
       round(sum(t.notional)::numeric, 0) AS usd,
       count(*) FILTER (WHERE t.source = 'chain') AS via_chain,
       count(*) FILTER (WHERE t.source = 'poll') AS via_poll,
       count(*) FILTER (WHERE t.taker) AS taker_true,
       count(*) FILTER (WHERE t.taker IS NULL) AS taker_unrecorded
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
 GROUP BY 1 ORDER BY 1;


\echo '== 3. BY SIDE AND SOURCE -- can either ingest path write a SELL? =='
SELECT t.source, t.side, count(*) AS fills,
       count(DISTINCT t.whale_id) AS whales,
       round(sum(t.notional)::numeric, 0) AS usd,
       min(t.ts)::date AS first_day, max(t.ts)::date AS last_day
  FROM trades t
 WHERE t.ts >= now() - interval '30 days'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;


\echo '== 4. THE INDEPENDENT WITNESS: realized_pnl on his positions =='
-- Shares can only leave a position before settlement by being sold or
-- merged. A whale of pure buys held to settlement carries realized_pnl at
-- zero nearly everywhere; a whale who trades out does not. This reads the
-- rebuilt position ledger and never looks at `side`.
SELECT COALESCE(w.username, '(null)') AS whale,
       count(*) AS positions,
       count(*) FILTER (WHERE p.realized_pnl <> 0) AS with_realized,
       round((100.0 * count(*) FILTER (WHERE p.realized_pnl <> 0)
              / NULLIF(count(*), 0))::numeric, 2) AS pct_with_realized,
       round(sum(p.realized_pnl)::numeric, 0) AS realized_usd,
       count(*) FILTER (WHERE p.net_shares > 0) AS still_open,
       count(*) FILTER (WHERE p.resolved) AS resolved,
       round(sum(p.notional_in)::numeric, 0) AS deployed_usd
  FROM positions p JOIN whales w ON w.id = p.whale_id
 WHERE p.last_trade_ts >= now() - interval '30 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 25;
