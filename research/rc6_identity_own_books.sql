-- READ-ONLY. RC6 lane identity-debt: OUR OWN BOOKS' identity census, the
-- exact two statements backend/sportsassets/analytics/identity_census.py
-- runs every analytics cycle (OWN_ACTIVE_SQL) and hourly
-- (OWN_HISTORICAL_SQL), with $1 = now() - 30 days. SELECT only.
--   ACTIVE: every ACTUAL row (open live orders, small-live / Kalshi fills,
--   funded ENTRY intents, registered positions) and PAPER rows inside the
--   window; HISTORICAL: PAPER rows outside it. unknown_n = rows whose market
--   identity is unknown (no slug, no condition, token not in the catalog).
\echo == OWN0 clock ==
SELECT now() AS db_now, now() - interval '30 days' AS window_start;

\echo == OWN1 ACTIVE (identity_census.OWN_ACTIVE_SQL) ==
SELECT 'ACTUAL_LIVE_ORDERS' AS book, count(*) AS rows_n,
       count(*) FILTER (WHERE coalesce(btrim(lo.us_market_slug), '') = ''
                          AND coalesce(btrim(lo.condition_id), '') = ''
                          AND NOT EXISTS (SELECT 1 FROM market_tokens mt
                                           WHERE mt.token_id = lo.asset))
           AS unknown_n
  FROM live_orders lo
 WHERE lo.status IN ('filled', 'exiting') AND lo.filled_shares > 0
UNION ALL
SELECT 'ACTUAL_SMALL_LIVE_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(f.us_market_slug), '') = '')
  FROM execmirror_fills f
UNION ALL
SELECT 'ACTUAL_FUNDED_ENTRIES', count(*),
       count(*) FILTER (WHERE coalesce(btrim(i.us_market_slug), '') = '')
  FROM bettor_funded_intents i WHERE i.kind = 'ENTRY'
UNION ALL
SELECT 'ACTUAL_KALSHI_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(k.ticker), '') = '')
  FROM kalshi_live_fills k
UNION ALL
SELECT 'OWNER_REGISTERED_POSITIONS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(r.us_market_slug), '') = '')
  FROM mirror_registered_positions r WHERE r.shares <> 0
UNION ALL
SELECT 'PAPER_AI_FOLLOWER', count(*),
       count(*) FILTER (WHERE coalesce(btrim(a.condition_id), '') = ''
                          AND NOT EXISTS (SELECT 1 FROM market_tokens mt
                                           WHERE mt.token_id = a.asset))
  FROM ai_trades a WHERE a.status = 'open' AND a.placed_at >= (now() - interval '30 days')
UNION ALL
SELECT 'PAPER_LEDGER_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(pf.us_market_slug), '') = ''
                           OR pf.holding_side NOT IN ('LONG', 'SHORT'))
  FROM paper_fills pf WHERE pf.filled_at >= (now() - interval '30 days')
UNION ALL
SELECT 'PAPER_RN1X_POSITIONS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(x.condition_id), '') = '')
  FROM rn1x_positions x WHERE x.decision_ts >= (now() - interval '30 days');

\echo == OWN2 HISTORICAL (identity_census.OWN_HISTORICAL_SQL) ==
SELECT 'PAPER_AI_FOLLOWER' AS book, count(*) AS rows_n,
       count(*) FILTER (WHERE coalesce(btrim(a.condition_id), '') = ''
                          AND NOT EXISTS (SELECT 1 FROM market_tokens mt
                                           WHERE mt.token_id = a.asset))
           AS unknown_n,
       max(a.placed_at) AS newest_at
  FROM ai_trades a WHERE a.status = 'open' AND a.placed_at < (now() - interval '30 days')
UNION ALL
SELECT 'PAPER_LEDGER_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(pf.us_market_slug), '') = ''
                           OR pf.holding_side NOT IN ('LONG', 'SHORT')),
       max(pf.filled_at)
  FROM paper_fills pf WHERE pf.filled_at < (now() - interval '30 days')
UNION ALL
SELECT 'PAPER_RN1X_POSITIONS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(x.condition_id), '') = ''),
       max(x.decision_ts)
  FROM rn1x_positions x WHERE x.decision_ts < (now() - interval '30 days');
