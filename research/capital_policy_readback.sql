-- Owner capital policy (PAPER_CAPITAL_1000_AVERAGE_TARGET_V1) readback, read-only.
-- Entry sizes against the ~$1,000 average target, by strategy and policy version,
-- refusal reasons (incl. cash and same-contract), and the account's cash/ledger.
\echo '== C0 · account balance and the latest ledger entry =='
SELECT a.account_id,
       (SELECT cash_after_usd FROM paper_ledger l WHERE l.account_id = a.account_id ORDER BY seq DESC LIMIT 1) AS cash_usd,
       (SELECT reserved_after_usd FROM paper_ledger l WHERE l.account_id = a.account_id ORDER BY seq DESC LIMIT 1) AS reserved_usd,
       (SELECT max(seq) FROM paper_ledger l WHERE l.account_id = a.account_id) AS last_seq,
       (SELECT max(committed_at) FROM paper_ledger l WHERE l.account_id = a.account_id) AS last_entry_at
  FROM paper_accounts a WHERE a.account_id = 'paper_acct_main';

\echo '== C1 · decisions since the release, by strategy, policy version and verdict =='
SELECT d.strategy, d.policy_version, d.verdict, count(*) AS n, min(d.decided_at) AS first, max(d.decided_at) AS latest
  FROM paper_decisions d
 WHERE d.account_id = 'paper_acct_main' AND d.decided_at > now() - interval '3 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== C2 · refusal reasons since the release (top 15) =='
SELECT d.strategy, coalesce(d.refusal, '(none)') AS refusal, count(*) AS n
  FROM paper_decisions d
 WHERE d.account_id = 'paper_acct_main' AND d.decided_at > now() - interval '3 hours'
   AND d.verdict <> 'ENTER'
 GROUP BY 1, 2 ORDER BY n DESC LIMIT 15;

\echo '== C3 · entry orders since the release: reservation vs ~$1,000 target, fills and cost incl. fees =='
SELECT o.strategy, o.decided_at, o.us_market_slug, o.holding_side, o.state, o.qty, o.filled_qty,
       o.limit_price, o.reserved_usd,
       (SELECT round(sum(f.gross_usd + f.fee_usd), 2) FROM paper_fills f WHERE f.order_id = o.order_id) AS filled_cost_incl_fees_usd,
       (SELECT round(sum(f.fee_usd), 4) FROM paper_fills f WHERE f.order_id = o.order_id) AS fees_usd
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
   AND o.decided_at > now() - interval '3 hours'
 ORDER BY o.decided_at DESC LIMIT 40;

\echo '== C4 · average initial entry size since the release, by strategy (reservation and filled cost) =='
SELECT o.strategy, count(*) AS entries,
       round(avg(o.reserved_usd), 2) AS avg_reservation_usd,
       round(avg(fc.cost), 2) AS avg_filled_cost_incl_fees_usd,
       round(min(fc.cost), 2) AS min_filled_cost, round(max(fc.cost), 2) AS max_filled_cost
  FROM paper_orders o
  LEFT JOIN LATERAL (SELECT sum(f.gross_usd + f.fee_usd) AS cost FROM paper_fills f WHERE f.order_id = o.order_id) fc ON true
 WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
   AND o.decided_at > now() - interval '3 hours'
 GROUP BY 1 ORDER BY 1;

\echo '== C5 · ledger integrity: every FILL row has a fill, and cash_after chains =='
SELECT count(*) AS ledger_rows,
       count(*) FILTER (WHERE kind = 'FILL' AND fill_id IS NULL) AS fill_rows_without_fill_id,
       count(*) FILTER (WHERE prev_cash IS NOT NULL AND abs(prev_cash + cash_delta_usd - cash_after_usd) > 0.000001) AS cash_chain_breaks
  FROM (SELECT l.*, lag(cash_after_usd) OVER (ORDER BY seq) AS prev_cash
          FROM paper_ledger l WHERE l.account_id = 'paper_acct_main') x;
