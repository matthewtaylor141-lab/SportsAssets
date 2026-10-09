-- READ-ONLY. RC6 lane identity-debt, discovery pass. SELECT only.
--   (a) the analytics positions persist dead-letter: the (whale, token)
--       position states with no condition_id on any fill and no
--       market_tokens row, classified by activity and by owner; and OUR
--       books (paper and actual) checked for active rows with no identity.
--   (b) the reconciler runs that logged "ingested N missed fills", per
--       wallet (whale id / username, never the address), and the fills
--       themselves with what our ledgers hold for each.
--   (c) the mirror_shadow heartbeat: status and positions source.
--   (d) paper ledger volumes around the management epoch (2026-10-05
--       00:00 America/New_York = 2026-10-05 04:00:00Z).
\echo == D0 clock ==
SELECT now() AS db_now;

\echo == A1 dead-lettered position keys by activity class (one scan of trades) ==
WITH k AS (
  SELECT whale_id, asset,
         bool_or(coalesce(condition_id, '') <> '') AS has_cid,
         count(*) AS fills,
         coalesce(sum(size) FILTER (WHERE side = 'BUY'), 0) AS bought,
         coalesce(sum(size) FILTER (WHERE side = 'SELL'), 0) AS sold,
         coalesce(sum(notional) FILTER (WHERE side = 'BUY'), 0) AS buy_usd,
         min(ts) FILTER (WHERE side = 'BUY') AS first_buy,
         min(ts) FILTER (WHERE side = 'SELL') AS first_sell,
         min(ts) AS first_ts, max(ts) AS last_ts,
         max(market_slug) AS slug,
         string_agg(DISTINCT source, ',') AS sources
    FROM trades GROUP BY whale_id, asset
), dl AS (
  SELECT k.*, k.bought - k.sold AS naive_net,
         (k.first_sell IS NOT NULL
          AND (k.first_buy IS NULL OR k.first_sell < k.first_buy)) AS sell_first
    FROM k
   WHERE NOT k.has_cid
     AND NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = k.asset)
), c AS (
  SELECT dl.*,
         CASE WHEN abs(naive_net) <= 0.000001 AND NOT sell_first THEN 'FLAT'
              WHEN naive_net < -0.000001 OR sell_first THEN 'SELLS_BEYOND_TRACKED_BUYS'
              WHEN last_ts < now() - interval '30 days' THEN 'OPEN_LAST_FILL_OVER_30D'
              WHEN last_ts < now() - interval '7 days' THEN 'OPEN_LAST_FILL_7_TO_30D'
              ELSE 'OPEN_LAST_FILL_WITHIN_7D' END AS cls,
         EXISTS (SELECT 1 FROM markets m WHERE dl.slug IS NOT NULL AND m.slug = dl.slug) AS slug_in_markets
    FROM dl
)
SELECT coalesce(c.cls, 'ALL') AS cls, c.whale_id, max(w.username) AS username,
       bool_or(w.pinned) AS pinned, bool_or(w.active) AS active,
       min(w.source_rank) AS source_rank,
       count(*) AS keys, sum(c.fills) AS fills,
       round(sum(greatest(c.naive_net, 0))::numeric, 2) AS open_shares_naive,
       round(sum(c.buy_usd)::numeric, 2) AS buy_usd,
       min(c.first_ts) AS first_fill, max(c.last_ts) AS last_fill,
       sum(CASE WHEN c.slug IS NOT NULL THEN 1 ELSE 0 END) AS with_slug,
       sum(CASE WHEN c.slug_in_markets THEN 1 ELSE 0 END) AS slug_in_markets,
       string_agg(DISTINCT c.sources, '|') AS sources
  FROM c LEFT JOIN whales w ON w.id = c.whale_id
 GROUP BY GROUPING SETS ((c.cls), (c.whale_id), ())
 ORDER BY (c.whale_id IS NOT NULL), keys DESC
 LIMIT 80;

\echo == A2 whales roster: how many, pinned, active (no addresses) ==
SELECT count(*) AS whales, sum(CASE WHEN pinned THEN 1 ELSE 0 END) AS pinned,
       sum(CASE WHEN active THEN 1 ELSE 0 END) AS active,
       sum(CASE WHEN banned THEN 1 ELSE 0 END) AS banned,
       sum(CASE WHEN source_rank IS NOT NULL THEN 1 ELSE 0 END) AS from_leaderboard
  FROM whales;

\echo == A3 OUR books: active rows whose market identity is missing or unknown ==
WITH pf AS (
  SELECT account_id, group_id, us_market_slug, holding_side,
         sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
    FROM paper_fills GROUP BY 1, 2, 3, 4
), ps AS (
  SELECT DISTINCT ON (position_key) position_key, qty
    FROM paper_settlements ORDER BY position_key, version DESC
), po AS (
  SELECT pf.*, pf.bought - pf.sold - coalesce(ps.qty, 0) AS open_qty
    FROM pf LEFT JOIN ps ON ps.position_key = 'paperpos:' || pf.account_id || ':'
         || pf.group_id || ':' || pf.us_market_slug || ':' || pf.holding_side
)
SELECT 'paper_fills (PAPER)' AS book, count(*) AS positions,
       sum(CASE WHEN open_qty > 0.000001 THEN 1 ELSE 0 END) AS open_positions,
       sum(CASE WHEN open_qty > 0.000001 AND (coalesce(btrim(us_market_slug), '') = ''
                OR holding_side NOT IN ('LONG', 'SHORT')) THEN 1 ELSE 0 END) AS open_identity_missing,
       sum(CASE WHEN open_qty > 0.000001 AND NOT EXISTS (
                SELECT 1 FROM paper_book_observations o
                 WHERE o.us_market_slug = po.us_market_slug) THEN 1 ELSE 0 END)
           AS open_never_observed_on_venue
  FROM po
UNION ALL
SELECT 'live_orders (ACTUAL, all lanes)', count(*),
       sum(CASE WHEN status IN ('filled', 'exiting') AND filled_shares > 0 THEN 1 ELSE 0 END),
       sum(CASE WHEN status IN ('filled', 'exiting') AND filled_shares > 0
                AND coalesce(btrim(us_market_slug), '') = ''
                AND coalesce(condition_id, '') = ''
                AND NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = live_orders.asset)
                THEN 1 ELSE 0 END),
       sum(CASE WHEN status IN ('filled', 'exiting') AND filled_shares > 0
                AND coalesce(btrim(us_market_slug), '') = ''
                AND NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = live_orders.asset)
                THEN 1 ELSE 0 END)
  FROM live_orders
UNION ALL
SELECT 'ai_trades (PAPER AI follower, legacy)', count(*),
       sum(CASE WHEN status = 'open' THEN 1 ELSE 0 END),
       sum(CASE WHEN status = 'open' AND coalesce(condition_id, '') = ''
                AND NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = ai_trades.asset)
                THEN 1 ELSE 0 END),
       sum(CASE WHEN status = 'open'
                AND NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = ai_trades.asset)
                THEN 1 ELSE 0 END)
  FROM ai_trades
UNION ALL
SELECT 'engine_fills (PAPER engine, legacy)', count(*),
       sum(CASE WHEN NOT settled THEN 1 ELSE 0 END),
       sum(CASE WHEN NOT settled AND coalesce(market_id, '') = '' THEN 1 ELSE 0 END),
       sum(CASE WHEN NOT settled AND venue = 'polymarket'
                AND NOT EXISTS (SELECT 1 FROM market_tokens mt WHERE mt.token_id = engine_fills.outcome_id)
                THEN 1 ELSE 0 END)
  FROM engine_fills
UNION ALL
SELECT 'execmirror_fills (ACTUAL small live) by market/group', count(*),
       sum(CASE WHEN held <> 0 THEN 1 ELSE 0 END),
       sum(CASE WHEN held <> 0 AND coalesce(btrim(us_market_slug), '') = '' THEN 1 ELSE 0 END),
       sum(CASE WHEN held <> 0 AND coalesce(group_id, '') = '' THEN 1 ELSE 0 END)
  FROM (SELECT us_market_slug, group_id,
               sum(CASE WHEN intent ILIKE '%SELL%' THEN -qty ELSE qty END) AS held
          FROM execmirror_fills GROUP BY 1, 2) f
UNION ALL
SELECT 'bettor_funded_intents ENTRY (ACTUAL funded)', count(*),
       sum(CASE WHEN public.bettor_funded_holds_inventory(residual_qty, closed_at) THEN 1 ELSE 0 END),
       sum(CASE WHEN public.bettor_funded_holds_inventory(residual_qty, closed_at)
                AND coalesce(btrim(us_market_slug), '') = '' THEN 1 ELSE 0 END),
       0
  FROM bettor_funded_intents WHERE kind = 'ENTRY'
UNION ALL
SELECT 'kalshi_live_fills (ACTUAL kalshi) by ticker', count(*),
       sum(CASE WHEN held <> 0 THEN 1 ELSE 0 END),
       sum(CASE WHEN held <> 0 AND coalesce(btrim(ticker), '') = '' THEN 1 ELSE 0 END), 0
  FROM (SELECT ticker, sum(CASE WHEN action ILIKE 'sell' THEN -count ELSE count END) AS held
          FROM kalshi_live_fills GROUP BY 1) k
UNION ALL
SELECT 'mirror_registered_positions (owner-registered)', count(*),
       sum(CASE WHEN shares <> 0 THEN 1 ELSE 0 END),
       sum(CASE WHEN shares <> 0 AND coalesce(btrim(us_market_slug), '') = '' THEN 1 ELSE 0 END), 0
  FROM mirror_registered_positions
UNION ALL
SELECT 'rn1x_positions (PAPER experiment)', count(*), NULL,
       sum(CASE WHEN coalesce(condition_id, '') = '' THEN 1 ELSE 0 END),
       sum(CASE WHEN NOT EXISTS (SELECT 1 FROM markets m WHERE m.condition_id = rn1x_positions.condition_id)
                THEN 1 ELSE 0 END)
  FROM rn1x_positions;

\echo == B1 reconciler runs in the last 36 h ==
SELECT r.id, r.started_at, r.finished_at, r.missed,
       (SELECT count(*) FROM jsonb_each(r.details -> 'per_wallet') e
         WHERE e.key NOT LIKE 'cov:%' AND e.key NOT LIKE 'failed:%') AS wallets,
       (SELECT count(*) FROM jsonb_each(r.details -> 'per_wallet') e
         WHERE e.key LIKE 'failed:%') AS failed,
       (SELECT count(*) FROM jsonb_each(r.details -> 'per_wallet') e
         WHERE e.key LIKE 'cov:%' AND (e.value ->> 'complete')::boolean) AS cov_complete,
       (SELECT count(*) FROM jsonb_each(r.details -> 'per_wallet') e
         WHERE e.key LIKE 'cov:%' AND (e.value ->> 'dirty')::int > 0) AS cov_dirty,
       length(r.details::text) AS details_chars
  FROM reconciliation_runs r
 WHERE r.started_at > now() - interval '36 hours'
 ORDER BY r.id;

\echo == B2 per wallet, runs with missed > 0 in the last 36 h (whale id, never the address) ==
SELECT r.id AS run_id, r.finished_at, w.id AS whale_id, w.username,
       (e.value)::text::int AS missed,
       cv.value ->> 'complete' AS complete, cv.value ->> 'dirty' AS dirty,
       to_timestamp((cv.value ->> 'oldest')::float8) AS cov_oldest,
       to_timestamp((cv.value ->> 'newest')::float8) AS cov_newest
  FROM reconciliation_runs r
  CROSS JOIN LATERAL jsonb_each(r.details -> 'per_wallet') e
  LEFT JOIN whales w ON w.address = e.key
  LEFT JOIN LATERAL (SELECT r.details -> 'per_wallet' -> ('cov:' || e.key) AS value) cv ON true
 WHERE r.started_at > now() - interval '36 hours' AND r.missed > 0
   AND e.key NOT LIKE 'cov:%' AND e.key NOT LIKE 'failed:%'
   AND jsonb_typeof(e.value) = 'number' AND (e.value)::text::int > 0
 ORDER BY r.id, w.id;

\echo == B3 the fills those runs ingested late: poll rows detected inside the run window ==
WITH runs AS (
  SELECT r.id, r.started_at, r.finished_at, r.missed
    FROM reconciliation_runs r
   WHERE r.started_at > now() - interval '36 hours' AND r.missed > 0
), wl AS (
  SELECT r.id AS run_id, w.id AS whale_id
    FROM reconciliation_runs r
    CROSS JOIN LATERAL jsonb_each(r.details -> 'per_wallet') e
    JOIN whales w ON w.address = e.key
   WHERE r.id IN (SELECT id FROM runs)
     AND jsonb_typeof(e.value) = 'number' AND (e.value)::text::int > 0
)
SELECT runs.id AS run_id, t.id AS trade_id, t.whale_id, t.side,
       t.size, t.price, t.notional, t.ts, t.detected_at,
       round(extract(epoch FROM t.detected_at - t.ts)::numeric, 1) AS detect_lag_s,
       (coalesce(t.condition_id, '') <> '') AS has_cid, t.outcome, t.outcome_index,
       t.market_slug, t.sport, t.source, t.venue_seen_at IS NOT NULL AS venue_seen,
       (SELECT count(*) FROM rn1_observations o WHERE o.source_event_id = t.dedupe_key) AS rn1_obs,
       (SELECT string_agg(DISTINCT o.source_type, ',') FROM rn1_observations o
         WHERE o.source_event_id = t.dedupe_key) AS rn1_obs_lane,
       (SELECT count(*) FROM rn1x_positions x WHERE x.source_trade_id = t.id) AS rn1x_positions,
       (SELECT count(*) FROM ai_trades a WHERE a.trade_id = t.id) AS ai_trades,
       (SELECT count(*) FROM live_orders lo WHERE lo.trade_id = t.id) AS live_orders,
       (SELECT count(*) FROM copy_probes cp WHERE cp.trade_id = t.id) AS copy_probes
  FROM runs
  JOIN wl ON wl.run_id = runs.id
  JOIN trades t ON t.whale_id = wl.whale_id
               AND t.detected_at BETWEEN runs.started_at AND coalesce(runs.finished_at, now())
               AND t.source = 'poll'
 ORDER BY runs.id, t.detected_at
 LIMIT 300;

\echo == C1 mirror_shadow heartbeat ==
SELECT status, beat_at, now() - beat_at AS age,
       detail -> 'positions_source' ->> 'source' AS pos_source,
       detail -> 'positions_source' ->> 'authority' AS pos_authority,
       detail -> 'positions_source' ->> 'primary_refusal' AS primary_refusal,
       detail -> 'positions_source' ->> 'refusal' AS refusal,
       detail -> 'positions_source' ->> 'pmus_slot_shape' AS slot_shape,
       detail ->> 'positions_unreadable' AS unreadable, detail ->> 'abandoned' AS abandoned,
       detail ->> 'venue_positions' AS venue_positions, detail ->> 'rows' AS rows_written
  FROM service_heartbeats WHERE service = 'mirror_shadow';

\echo == D1 paper accounts and ledger volumes around the epoch ==
SELECT a.account_id, a.starting_cash_usd,
       (SELECT count(*) FROM paper_ledger l WHERE l.account_id = a.account_id) AS ledger_rows,
       (SELECT count(*) FROM paper_ledger l WHERE l.account_id = a.account_id
           AND l.committed_at >= timestamptz '2026-10-05 04:00:00+00') AS ledger_rows_post_epoch,
       (SELECT count(*) FROM paper_fills f WHERE f.account_id = a.account_id) AS fills,
       (SELECT count(DISTINCT (group_id, us_market_slug, holding_side)) FROM paper_fills f
         WHERE f.account_id = a.account_id) AS positions,
       (SELECT count(*) FROM paper_settlements s WHERE s.account_id = a.account_id) AS settlement_versions,
       (SELECT max(length(l.cash_delta_usd::text)) FROM paper_ledger l WHERE l.account_id = a.account_id) AS max_amount_chars,
       (SELECT count(*) FROM paper_ledger l WHERE l.account_id = a.account_id
           AND l.cash_delta_usd <> round(l.cash_delta_usd, 2)) AS sub_cent_cash_rows,
       (SELECT count(*) FROM paper_fills f WHERE f.account_id = a.account_id
           AND f.fee_usd <> round(f.fee_usd, 2)) AS sub_cent_fee_fills
  FROM paper_accounts a ORDER BY a.account_id;

\echo == D2 ledger kinds by side of the epoch, paper_acct_main ==
SELECT kind, committed_at >= timestamptz '2026-10-05 04:00:00+00' AS post_epoch,
       count(*) AS n, sum(cash_delta_usd) AS cash, sum(reserved_delta_usd) AS reserved
  FROM paper_ledger WHERE account_id = 'paper_acct_main'
 GROUP BY 1, 2 ORDER BY 1, 2;
