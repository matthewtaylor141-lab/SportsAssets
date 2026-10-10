\echo rc63 kalshi-shadow PREDICTION: what the SHADOW planner would record over the last 7 days of linked PAPER ENTER decisions,
\echo by the planner rules (counterpart first, then 1:1000 sizing), and the scan size of its population query
\echo reads only

\echo A predicted exclusion by counterpart rule (PMUS alias of the held side, certified, then a certified Kalshi YES alias of the same claim)
WITH d AS (
  SELECT pd.decision_id, pd.us_market_slug AS slug,
         CASE WHEN po.holding_side = 'SHORT' THEN 'NO' ELSE 'YES' END AS side,
         po.qty
    FROM paper_decisions pd
    JOIN LATERAL (SELECT o.qty, o.holding_side FROM paper_orders o
                   WHERE o.decision_id = pd.decision_id AND o.role = 'ENTRY'
                     AND o.direction = 'BUY'
                   ORDER BY o.created_at, o.order_id LIMIT 1) po ON true
   WHERE pd.verdict = 'ENTER' AND pd.decided_at > now() - interval '7 days'
), c AS (
  SELECT d.*,
         (SELECT a.claim_fingerprint FROM canonical_claim_aliases a
           WHERE a.venue = 'POLYMARKET_US' AND a.market_id = d.slug AND a.side = d.side
             AND a.claim_fingerprint IS NOT NULL AND a.certificate_status = 'CERTIFIED'
             AND a.mapping_status = 'ESTABLISHED' AND a.settlement_status = 'PROVEN'
           LIMIT 1) AS fp,
         EXISTS (SELECT 1 FROM canonical_claim_aliases a
                  WHERE a.venue = 'POLYMARKET_US' AND a.market_id = d.slug
                    AND a.side = d.side) AS has_alias
    FROM d
)
SELECT CASE WHEN NOT has_alias THEN 'NO_CERTIFIED_COUNTERPART:NO_CANONICAL_ALIAS_FOR_THE_HELD_SIDE'
            WHEN fp IS NULL THEN 'NO_CERTIFIED_COUNTERPART:THE_HELD_SIDE_ALIAS_IS_NOT_A_CERTIFIED_CLAIM'
            WHEN NOT EXISTS (SELECT 1 FROM canonical_claim_aliases k
                              WHERE k.venue = 'KALSHI' AND k.claim_fingerprint = c.fp
                                AND k.certificate_status = 'CERTIFIED'
                                AND k.mapping_status = 'ESTABLISHED'
                                AND k.settlement_status = 'PROVEN')
                 THEN 'NO_CERTIFIED_COUNTERPART:NO_CERTIFIED_KALSHI_ALIAS_CARRIES_THE_CLAIM'
            WHEN NOT EXISTS (SELECT 1 FROM canonical_claim_aliases k
                              WHERE k.venue = 'KALSHI' AND k.claim_fingerprint = c.fp
                                AND k.certificate_status = 'CERTIFIED'
                                AND k.mapping_status = 'ESTABLISHED'
                                AND k.settlement_status = 'PROVEN' AND k.side = 'YES')
                 THEN 'COUNTERPART_ONLY_A_NO_LEG'
            ELSE 'HAS_A_CERTIFIED_KALSHI_YES_COUNTERPART' END AS predicted,
       count(*) AS decisions,
       count(*) FILTER (WHERE qty > 500) AS would_size_at_least_one_contract,
       count(*) FILTER (WHERE qty <= 500) AS would_be_below_venue_minimum
  FROM c GROUP BY 1 ORDER BY 1;

\echo B population scan size: paper_decisions in the backfill window (7 days) and the steady window (1 hour), all verdicts
SELECT count(*) FILTER (WHERE decided_at > now() - interval '7 days') AS decisions_7d,
       count(*) FILTER (WHERE decided_at > now() - interval '7 days' AND verdict = 'ENTER') AS enter_7d,
       count(*) FILTER (WHERE decided_at > now() - interval '1 hour') AS decisions_1h,
       count(*) FILTER (WHERE decided_at > now() - interval '1 hour' AND verdict = 'ENTER') AS enter_1h,
       max(decided_at) FILTER (WHERE verdict = 'ENTER') AS newest_enter
  FROM paper_decisions WHERE decided_at > now() - interval '7 days';
