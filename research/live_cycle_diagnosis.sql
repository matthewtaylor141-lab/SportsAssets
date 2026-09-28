-- THE LATEST COMPLETED CYCLE, AND WHY IT EVALUATED NOTHING. Read-only.
--
-- Codex's correction stands: the Sept 24-27 valuation cohort is HISTORY, and a
-- diagnosis of the live path cannot be assembled from it. The live cycle at
-- 2026-09-28 21:38:25 UTC reports zero evaluated and zero written with matching
-- and book-currency refusals. This asks that row for its own funnel.
--
-- ALSO CORRECTS MY OWN READING ERROR. `_settlement_compatibility` writes
-- `compatibility`; I queried `settlement_comparison ->> 'verdict'`, got NULL on
-- every row, and reported that settlement evidence had never been captured. It
-- had. Section 5 uses the field the code actually writes, and the field
-- `bettor_entry_execution` actually reads.

\echo == 1 · THE WRITER LAST CYCLE, IN FULL ==
SELECT key,
       left(value::text, 3000) AS cycle
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_standby_last_cycle')
 ORDER BY key;

\echo == 2 · THE LANE HEARTBEAT ROWS AND THEIR AGE ==
SELECT service, status,
       beat_at::timestamptz(0) AS beat_at,
       extract(epoch FROM (now() - beat_at))::int AS age_s,
       left(detail::text, 2200) AS detail
  FROM service_heartbeats
 WHERE service LIKE '%pinnacle%' OR service LIKE '%ext%' OR service LIKE '%entry%'
 ORDER BY beat_at DESC
 LIMIT 4;

\echo == 3 · SETTLEMENT COMPATIBILITY ON THE CORRECT FIELD ==
SELECT coalesce(settlement_comparison ->> 'compatibility', 'NOT_RECORDED') AS compatibility,
       count(*)                                                            AS n,
       count(*) FILTER (WHERE estimated_edge_per_contract > 0)             AS positive_edge,
       round(max(estimated_edge_per_contract)::numeric, 4)                 AS best_edge
  FROM external_valuations
 GROUP BY 1
 ORDER BY 2 DESC;

\echo == 4 · THE POSITIVE-EDGE ROWS AGAINST THE ONE-CENT MINIMUM ==
SELECT round(estimated_edge_per_contract::numeric, 4)                     AS edge,
       coalesce(settlement_comparison ->> 'compatibility', 'NOT_RECORDED') AS compatibility,
       count(*)                                                            AS n,
       count(*) FILTER (WHERE 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(refusals)) AS also_no_net_edge
  FROM external_valuations
 WHERE estimated_edge_per_contract > 0
 GROUP BY 1, 2
 ORDER BY 1 DESC
 LIMIT 30;

\echo == 5 · WHAT MATCHING ACTUALLY REFUSED, MOST RECENT FIRST ==
SELECT key, left(value::text, 1200) AS value
  FROM ingestion_state
 WHERE key LIKE '%match%' OR key LIKE '%currency%' OR key LIKE '%premap%'
 ORDER BY key
 LIMIT 8;
