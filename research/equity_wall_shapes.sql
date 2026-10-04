-- EQUITY WALL: real shapes for the live equity read (read-only; no credential
-- columns; the account fingerprint is not selected).

\echo == execmirror_control (Polymarket US small live) ==
SELECT enabled, stopped, flatten_on_stop, stop_done_at, scale, max_order_usd,
       cutover_at, updated_at, baseline -> 'balances' AS baseline_balances,
       baseline -> 'at' AS baseline_at
  FROM execmirror_control;

\echo == execmirror_snapshots: latest 3 ==
SELECT snapshot_id, at, balances, positions, open_orders, reconciliation
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 3;

\echo == execmirror_snapshots per day (last 14 days) ==
SELECT date_trunc('day', at) AS day, count(*) AS n, min(at), max(at)
  FROM execmirror_snapshots WHERE at > now() - interval '14 days'
 GROUP BY 1 ORDER BY 1;

\echo == execmirror_fills ==
SELECT count(*) AS fills, min(observed_at), max(observed_at),
       sum(qty) AS qty, sum(fee_usd) AS fees
  FROM execmirror_fills;

\echo == execmirror_orders by state ==
SELECT state, count(*) FROM execmirror_orders GROUP BY 1 ORDER BY 2 DESC;

\echo == kalshi_smalllive_control ==
SELECT enabled, stopped, kalshi_env, scale, max_order_usd, cutover_at,
       (key_fingerprint IS NOT NULL) AS has_key_fingerprint,
       reconciliation_id, updated_at
  FROM kalshi_smalllive_control;

\echo == kalshi_account_reconciliations ==
SELECT count(*) AS n, max(at) AS latest FROM kalshi_account_reconciliations;
SELECT reconciliation_id, at, kalshi_env, verdict, complete, balance_usd,
       jsonb_array_length(positions) AS positions,
       jsonb_array_length(resting_orders) AS resting, errors
  FROM kalshi_account_reconciliations ORDER BY at DESC LIMIT 3;

\echo == kalshi_live_fills ==
SELECT count(*) AS fills, max(observed_at) FROM kalshi_live_fills;

\echo == paper account ==
SELECT account_id, starting_cash_usd, currency, reporting_tz, created_at
  FROM paper_accounts;
SELECT account_id, count(*) AS entries, sum(cash_delta_usd) AS cash,
       sum(reserved_delta_usd) AS reserved, max(committed_at) AS last_commit
  FROM paper_ledger GROUP BY 1;

\echo == paper_equity_snapshots cadence ==
SELECT account_id, count(*) AS n, min(at), max(at),
       count(*) FILTER (WHERE at > now() - interval '24 hours') AS n_24h,
       count(DISTINCT equity_usd) FILTER (WHERE at > now() - interval '24 hours') AS distinct_equity_24h,
       count(*) FILTER (WHERE equity_usd IS NULL) AS null_equity
  FROM paper_equity_snapshots GROUP BY 1;
SELECT at, cash_usd, reserved_usd, marked_value_usd, equity_usd,
       equity_excluding_unmarked_usd, unmarked_positions, realized_pnl_usd,
       unrealized_pnl_usd, last_sequence
  FROM paper_equity_snapshots ORDER BY at DESC LIMIT 8;

\echo == paper open positions (fills minus sells, without settlements) ==
SELECT count(*) AS groups_with_fills,
       count(*) FILTER (WHERE s.position_key IS NULL) AS unsettled
  FROM (SELECT DISTINCT account_id, group_id, us_market_slug, holding_side
          FROM paper_fills) f
  LEFT JOIN (SELECT DISTINCT position_key FROM paper_settlements) s
    ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
       || ':' || f.us_market_slug || ':' || f.holding_side;

\echo == paper_book_observations freshness ==
SELECT max(observed_at) AS newest,
       count(*) FILTER (WHERE observed_at > now() - interval '1 hour') AS n_1h
  FROM paper_book_observations;

\echo == paper session health ==
SELECT session_id, heartbeat_at, passes, errors
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 2;
