\echo rc63 kalshi-shadow BEFORE: the Kalshi small-live control, the Kalshi live tables, the SMALL LIVE mode,
\echo the linked PAPER ENTER decisions a SHADOW planner would see, and the certified Kalshi counterparts it could plan against
\echo reads only

\echo A kalshi_smalllive_control (the existing disabled control)
SELECT id, enabled, stopped, kalshi_env, (key_fingerprint IS NOT NULL) AS has_key,
       reconciliation_id, scale, max_order_usd, revision, updated_at
  FROM kalshi_smalllive_control;

\echo B row counts of the migration 196 Kalshi live tables
SELECT (SELECT count(*) FROM kalshi_live_intents) AS kalshi_live_intents,
       (SELECT count(*) FROM kalshi_live_fills) AS kalshi_live_fills,
       (SELECT count(*) FROM kalshi_live_events) AS kalshi_live_events,
       (SELECT count(*) FROM kalshi_account_reconciliations) AS kalshi_account_reconciliations;

\echo C small_live_control mode
SELECT id, mode, halted FROM small_live_control;

\echo D linked PAPER ENTER decisions (ENTER verdict with an ENTRY BUY paper order) by window and strategy
WITH d AS (
  SELECT pd.decision_id, pd.decided_at, coalesce(pd.strategy, po.strategy) AS strategy,
         pd.us_market_slug, pd.holding_side, po.qty, po.time_in_force, po.order_type,
         (pd.p_blended IS NOT NULL OR pd.p_pinnacle IS NOT NULL) AS has_fair
    FROM paper_decisions pd
    JOIN LATERAL (SELECT o.qty, o.time_in_force, o.order_type, o.strategy
                    FROM paper_orders o
                   WHERE o.decision_id = pd.decision_id AND o.role = 'ENTRY'
                     AND o.direction = 'BUY'
                   ORDER BY o.created_at LIMIT 1) po ON true
   WHERE pd.verdict = 'ENTER' AND pd.decided_at > now() - interval '7 days'
)
SELECT strategy,
       count(*) FILTER (WHERE decided_at > now() - interval '24 hours') AS d24h,
       count(*) AS d7d,
       count(*) FILTER (WHERE qty > 500) AS qty_over_500_d7d,
       count(*) FILTER (WHERE qty >= 1500) AS qty_1500_plus_d7d,
       max(qty) AS max_qty_d7d,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY qty) AS p50_qty_d7d,
       count(*) FILTER (WHERE time_in_force = 'FOK') AS fok_d7d,
       count(*) FILTER (WHERE order_type = 'RESTING') AS resting_d7d,
       count(*) FILTER (WHERE NOT has_fair) AS no_fair_p_d7d
  FROM d GROUP BY ROLLUP (strategy) ORDER BY strategy NULLS LAST;

\echo E canonical_claim_aliases by venue, side, certificate, mapping and settlement status
SELECT venue, side, coalesce(certificate_status, 'NULL') AS certificate_status,
       mapping_status, settlement_status,
       count(*) AS aliases, count(claim_fingerprint) AS with_fingerprint,
       max(updated_at) AS newest
  FROM canonical_claim_aliases
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5;

\echo F claim fingerprints carried by a CERTIFIED PMUS alias and a CERTIFIED Kalshi YES alias (a plannable certified pair)
WITH p AS (
  SELECT claim_fingerprint, market_id AS slug, side
    FROM canonical_claim_aliases
   WHERE venue = 'POLYMARKET_US' AND claim_fingerprint IS NOT NULL
     AND certificate_status = 'CERTIFIED'
), k AS (
  SELECT claim_fingerprint, market_id AS ticker, side
    FROM canonical_claim_aliases
   WHERE venue = 'KALSHI' AND claim_fingerprint IS NOT NULL
     AND certificate_status = 'CERTIFIED' AND mapping_status = 'ESTABLISHED'
     AND settlement_status = 'PROVEN'
)
SELECT (SELECT count(*) FROM p) AS pmus_certified,
       (SELECT count(*) FROM k) AS kalshi_certified,
       (SELECT count(*) FROM k WHERE side = 'YES') AS kalshi_certified_yes,
       (SELECT count(DISTINCT p.claim_fingerprint) FROM p JOIN k USING (claim_fingerprint)) AS shared_fingerprints,
       (SELECT count(DISTINCT p.claim_fingerprint) FROM p JOIN k USING (claim_fingerprint) WHERE k.side = 'YES') AS shared_with_kalshi_yes;

\echo G last 7 days linked ENTER decisions whose held side has a CERTIFIED PMUS alias, and a certified Kalshi YES alias of the same claim
WITH d AS (
  SELECT pd.decision_id, pd.us_market_slug AS slug,
         CASE WHEN pd.holding_side = 'SHORT' THEN 'NO' ELSE 'YES' END AS side
    FROM paper_decisions pd
   WHERE pd.verdict = 'ENTER' AND pd.decided_at > now() - interval '7 days'
     AND EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = pd.decision_id
                    AND o.role = 'ENTRY' AND o.direction = 'BUY')
), pa AS (
  SELECT d.decision_id, a.claim_fingerprint
    FROM d JOIN canonical_claim_aliases a
      ON a.venue = 'POLYMARKET_US' AND a.market_id = d.slug AND a.side = d.side
     AND a.claim_fingerprint IS NOT NULL AND a.certificate_status = 'CERTIFIED'
)
SELECT (SELECT count(*) FROM d) AS linked_enter_d7d,
       (SELECT count(DISTINCT slug) FROM d) AS distinct_slugs_d7d,
       (SELECT count(*) FROM d WHERE EXISTS (SELECT 1 FROM canonical_claim_aliases a
          WHERE a.venue = 'POLYMARKET_US' AND a.market_id = d.slug)) AS with_any_pmus_alias,
       (SELECT count(DISTINCT decision_id) FROM pa) AS with_certified_pmus_alias,
       (SELECT count(DISTINCT pa.decision_id) FROM pa JOIN canonical_claim_aliases k
          ON k.claim_fingerprint = pa.claim_fingerprint AND k.venue = 'KALSHI'
         AND k.certificate_status = 'CERTIFIED' AND k.mapping_status = 'ESTABLISHED'
         AND k.settlement_status = 'PROVEN' AND k.side = 'YES') AS with_certified_kalshi_yes;

\echo H Kalshi fee terms and current Kalshi books
SELECT (SELECT count(*) FROM kalshi_fee_terms) AS fee_terms,
       (SELECT count(DISTINCT series_ticker) FROM kalshi_fee_terms) AS fee_series,
       (SELECT count(*) FROM kalshi_books_current) AS books,
       (SELECT count(*) FROM kalshi_books_current WHERE readable AND observed_at > now() - interval '30 seconds') AS books_current_30s;
