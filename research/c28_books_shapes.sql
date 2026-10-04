-- C28 BOOKS STREAM: READ-ONLY SHAPES FOR PAPER SLEEVES AND SMALL-LIVE TRUTH.
-- Counts and states only; no credential column is selected (fingerprints are
-- read as booleans). Nothing here writes.

\echo '--- 1. paper groups by entry strategy and decision policy version ---'
SELECT o.strategy, pd.policy_version, count(DISTINCT o.group_id) AS groups,
       min(o.created_at) AS first_at, max(o.created_at) AS last_at
  FROM paper_orders o
  LEFT JOIN paper_decisions pd ON pd.decision_id = o.decision_id
 WHERE o.role = 'ENTRY'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '--- 2. groups whose orders carry more than one strategy ---'
SELECT count(*) AS mixed_groups FROM (
  SELECT group_id FROM paper_orders GROUP BY group_id
  HAVING count(DISTINCT strategy) > 1) x;

\echo '--- 3. groups with no ENTRY order (management-only) ---'
SELECT count(DISTINCT group_id) AS groups_without_entry FROM paper_orders o
 WHERE NOT EXISTS (SELECT 1 FROM paper_orders e
                    WHERE e.group_id = o.group_id AND e.role = 'ENTRY');

\echo '--- 4. ledger rows by kind; rows with group_id not in paper_orders ---'
SELECT kind, count(*) AS n, count(group_id) AS with_group,
       sum(cash_delta_usd) AS cash_delta
  FROM paper_ledger GROUP BY kind ORDER BY kind;
SELECT count(*) AS ledger_groups_unknown FROM (
  SELECT DISTINCT group_id FROM paper_ledger l WHERE group_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.group_id = l.group_id)) x;

\echo '--- 5. fills by strategy (open qty proxy) ---'
SELECT strategy, count(*) AS fills, count(DISTINCT group_id) AS groups
  FROM paper_fills GROUP BY strategy ORDER BY strategy;

\echo '--- 6. decision policy versions by strategy (ENTER only) ---'
SELECT strategy, policy_version, count(*) FROM paper_decisions
 WHERE verdict = 'ENTER' GROUP BY 1, 2 ORDER BY 1, 2;

\echo '--- 7. execmirror_control (no fingerprint value) ---'
SELECT enabled, stopped, flatten_on_stop, stop_done_at, scale, max_order_usd,
       cutover_at, (account_fingerprint IS NOT NULL) AS has_account,
       revision, updated_at FROM execmirror_control;

\echo '--- 8. execmirror_orders by origin prefix and state ---'
SELECT CASE WHEN execution_intent_id IS NOT NULL THEN 'INTENT_LANE'
            WHEN paper_order_id IS NOT NULL THEN 'PAPER_ORDER_COPY'
            ELSE 'OTHER' END AS origin,
       state, count(*) AS n, count(venue_order_id) AS with_venue_id,
       count(accepted_at) AS accepted, max(created_at) AS last_at
  FROM execmirror_orders GROUP BY 1, 2 ORDER BY 1, 2;
SELECT count(*) AS fills, max(observed_at) AS last_fill FROM execmirror_fills;
SELECT max(at) AS last_snapshot, count(*) AS snapshots FROM execmirror_snapshots;

\echo '--- 9. execution_intents ---'
SELECT strategy, live_eligible, actual_state, count(*) AS n,
       max(created_at) AS last_at
  FROM execution_intents GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '--- 10. kalshi ---'
SELECT enabled, stopped, kalshi_env, (key_fingerprint IS NOT NULL) AS has_key,
       cutover_at, updated_at FROM kalshi_smalllive_control;
SELECT state, count(*) FROM kalshi_live_intents GROUP BY 1 ORDER BY 1;
SELECT count(*) AS recons FROM kalshi_account_reconciliations;

\echo '--- 11. equity snapshots ---'
SELECT count(*) AS n, max(at) AS last_at FROM paper_equity_snapshots;

\echo '--- 12. schema presence ---'
SELECT to_regclass('eddie_execution_estimates') AS eddie,
       to_regclass('pos_runs') AS pos, to_regclass('execution_intents') AS ei,
       (SELECT max(version) FROM schema_migrations) AS max_migration;
