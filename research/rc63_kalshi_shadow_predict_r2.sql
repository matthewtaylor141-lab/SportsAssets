\echo rc63 kalshi-shadow PREDICTION r2: the last 7 days of linked PAPER ENTER decisions split in the order the revised planner judges them
\echo 1 account scope (execmirror PAPER_ACCOUNT paper_acct_main, others get no row), 2 the owner live-eligibility allowlist
\echo (PINNACLE_COMPLETED_GAME_PAPER at policy version _V2 or _V3 only), 3 the paper order state and expiry, 4 the certified Kalshi counterpart
\echo reads only

\echo A linked ENTER decisions by account, strategy, policy version and live eligibility
WITH d AS (
  SELECT pd.decision_id, pd.account_id, pd.strategy,
         coalesce(pd.policy_version, po.label->>'policy_version') AS policy_version
    FROM paper_decisions pd
    JOIN LATERAL (SELECT o.label FROM paper_orders o
                   WHERE o.decision_id = pd.decision_id AND o.role = 'ENTRY'
                     AND o.direction = 'BUY'
                   ORDER BY o.created_at, o.order_id LIMIT 1) po ON true
   WHERE pd.verdict = 'ENTER' AND pd.decided_at > now() - interval '7 days'
)
SELECT account_id, strategy, policy_version,
       (strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
        AND policy_version IN ('PINNACLE_COMPLETED_GAME_PAPER_V2',
                               'PINNACLE_COMPLETED_GAME_PAPER_V3')) AS live_eligible,
       count(*) AS decisions
  FROM d GROUP BY 1, 2, 3, 4 ORDER BY 1, 4 DESC, 5 DESC;

\echo B predicted row by the revised planner order (paper order state is its state NOW, an upper bound on ended at plan time)
WITH d AS (
  SELECT pd.decision_id, pd.account_id, pd.strategy, pd.decided_at,
         coalesce(pd.policy_version, po.label->>'policy_version') AS policy_version,
         coalesce(po.us_market_slug, pd.us_market_slug) AS slug,
         CASE WHEN po.holding_side = 'SHORT' THEN 'NO' ELSE 'YES' END AS side,
         po.holding_side, po.qty, po.state AS paper_state, po.expires_at
    FROM paper_decisions pd
    JOIN LATERAL (SELECT o.label, o.qty, o.holding_side, o.us_market_slug,
                         o.state, o.expires_at
                    FROM paper_orders o
                   WHERE o.decision_id = pd.decision_id AND o.role = 'ENTRY'
                     AND o.direction = 'BUY'
                   ORDER BY o.created_at, o.order_id LIMIT 1) po ON true
   WHERE pd.verdict = 'ENTER' AND pd.decided_at > now() - interval '7 days'
), c AS (
  SELECT d.*,
         (d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
          AND d.policy_version IN ('PINNACLE_COMPLETED_GAME_PAPER_V2',
                                   'PINNACLE_COMPLETED_GAME_PAPER_V3')) AS eligible,
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
SELECT CASE WHEN account_id <> 'paper_acct_main' THEN '0 NO_ROW:OUTSIDE_THE_MIRRORED_PAPER_ACCOUNT'
            WHEN NOT eligible THEN '1 STRATEGY_NOT_LIVE_ELIGIBLE:' || coalesce(strategy, 'NULL')
            WHEN holding_side NOT IN ('LONG', 'SHORT') OR slug IS NULL THEN '2 MAPPING_NOT_ESTABLISHED'
            WHEN paper_state IN ('CANCELED', 'EXPIRED', 'REJECTED', 'CANCEL_PENDING')
                 THEN '3 PAPER_ORDER_ENDED_BEFORE_PLAN:' || paper_state
            WHEN expires_at <= decided_at + interval '15 seconds'
                 THEN '4 PAPER_ORDER_EXPIRED_WITHIN_ONE_PASS'
            WHEN NOT has_alias THEN '5 NO_CERTIFIED_COUNTERPART:NO_CANONICAL_ALIAS_FOR_THE_HELD_SIDE'
            WHEN fp IS NULL THEN '5 NO_CERTIFIED_COUNTERPART:THE_HELD_SIDE_ALIAS_IS_NOT_A_CERTIFIED_CLAIM'
            WHEN NOT EXISTS (SELECT 1 FROM canonical_claim_aliases k
                              WHERE k.venue = 'KALSHI' AND k.claim_fingerprint = c.fp
                                AND k.certificate_status = 'CERTIFIED'
                                AND k.mapping_status = 'ESTABLISHED'
                                AND k.settlement_status = 'PROVEN')
                 THEN '5 NO_CERTIFIED_COUNTERPART:NO_CERTIFIED_KALSHI_ALIAS_CARRIES_THE_CLAIM'
            WHEN NOT EXISTS (SELECT 1 FROM canonical_claim_aliases k
                              WHERE k.venue = 'KALSHI' AND k.claim_fingerprint = c.fp
                                AND k.certificate_status = 'CERTIFIED'
                                AND k.mapping_status = 'ESTABLISHED'
                                AND k.settlement_status = 'PROVEN' AND k.side = 'YES')
                 THEN '6 COUNTERPART_ONLY_A_NO_LEG'
            ELSE '7 HAS_A_CERTIFIED_KALSHI_YES_COUNTERPART' END AS predicted,
       count(*) AS decisions,
       count(*) FILTER (WHERE qty > 500) AS would_size_at_least_one_contract,
       count(*) FILTER (WHERE qty <= 500) AS would_be_below_venue_minimum
  FROM c GROUP BY 1 ORDER BY 1;

\echo C live-eligible decisions in the mirrored account: paper order state now and expiry minus decision time
SELECT po.state AS paper_state, po.time_in_force, po.order_type,
       count(*) AS decisions,
       min(extract(epoch FROM po.expires_at - pd.decided_at))::numeric(12,1) AS min_ttl_s,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM po.expires_at - pd.decided_at))::numeric(12,1) AS median_ttl_s
  FROM paper_decisions pd
  JOIN LATERAL (SELECT o.state, o.time_in_force, o.order_type, o.expires_at, o.label
                  FROM paper_orders o
                 WHERE o.decision_id = pd.decision_id AND o.role = 'ENTRY'
                   AND o.direction = 'BUY'
                 ORDER BY o.created_at, o.order_id LIMIT 1) po ON true
 WHERE pd.verdict = 'ENTER' AND pd.decided_at > now() - interval '7 days'
   AND pd.account_id = 'paper_acct_main' AND pd.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND coalesce(pd.policy_version, po.label->>'policy_version') IN
       ('PINNACLE_COMPLETED_GAME_PAPER_V2', 'PINNACLE_COMPLETED_GAME_PAPER_V3')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo D shared claim fingerprints between certified PMUS and certified Kalshi aliases today
SELECT count(DISTINCT p.claim_fingerprint) AS shared_certified_fingerprints
  FROM canonical_claim_aliases p
  JOIN canonical_claim_aliases k ON k.claim_fingerprint = p.claim_fingerprint
 WHERE p.venue = 'POLYMARKET_US' AND k.venue = 'KALSHI'
   AND p.certificate_status = 'CERTIFIED' AND k.certificate_status = 'CERTIFIED'
   AND p.mapping_status = 'ESTABLISHED' AND k.mapping_status = 'ESTABLISHED'
   AND p.settlement_status = 'PROVEN' AND k.settlement_status = 'PROVEN';

\echo E ENTER decisions in the last 7 days outside the mirrored account, by account (no row is written for them)
SELECT account_id, count(*) AS enter_decisions
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at > now() - interval '7 days'
   AND account_id <> 'paper_acct_main'
 GROUP BY 1 ORDER BY 2 DESC;
