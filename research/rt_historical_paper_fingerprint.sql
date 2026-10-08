-- HISTORICAL PAPER FINGERPRINT (closeout 2026-10-08), read only.
-- Every PAPER row recorded before the fixed cutoff 2026-10-08 02:00:00Z,
-- hashed row by row in key order. Run before and after a deploy: identical
-- digests prove no historical PAPER result, loss or ledger row changed.
\echo === paper_ledger (committed_at < cutoff, full rows by seq) ===
SELECT count(*) AS rows, max(seq) AS max_seq,
       sum(cash_delta_usd) AS cash_delta_total_usd,
       md5(string_agg(md5(l::text), '' ORDER BY seq)) AS digest
  FROM paper_ledger l
 WHERE committed_at < timestamptz '2026-10-08 02:00:00+00';
\echo === paper_fills (filled_at < cutoff, full rows by fill_id) ===
SELECT count(*) AS rows, sum(qty * price) AS notional_usd, sum(fee_usd) AS fees_usd,
       md5(string_agg(md5(f::text), '' ORDER BY fill_id)) AS digest
  FROM paper_fills f
 WHERE filled_at < timestamptz '2026-10-08 02:00:00+00';
\echo === paper_settlements (recorded_at < cutoff, full rows by settlement_id) ===
SELECT count(*) AS rows, sum(payout_usd) AS payout_total_usd,
       md5(string_agg(md5(s::text), '' ORDER BY settlement_id)) AS digest
  FROM paper_settlements s
 WHERE recorded_at < timestamptz '2026-10-08 02:00:00+00';
\echo === paper_order_events (recorded_at < cutoff, full rows by event_id) ===
SELECT count(*) AS rows, max(event_id) AS max_event_id,
       md5(string_agg(md5(e::text), '' ORDER BY event_id)) AS digest
  FROM paper_order_events e
 WHERE recorded_at < timestamptz '2026-10-08 02:00:00+00';
\echo === paper_decisions (decided_at < cutoff, economic fields by decision_id) ===
SELECT count(*) AS rows,
       md5(string_agg(md5(concat_ws('|', decision_id, verdict, refusal,
                                    proposed_qty::text, limit_price::text,
                                    decided_at::text)),
                      '' ORDER BY decision_id)) AS digest
  FROM paper_decisions
 WHERE decided_at < timestamptz '2026-10-08 02:00:00+00';
\echo === paper_accounts (all rows) ===
SELECT count(*) AS rows, sum(starting_cash_usd) AS starting_cash_usd,
       md5(string_agg(md5(a::text), '' ORDER BY account_id)) AS digest
  FROM paper_accounts a;
