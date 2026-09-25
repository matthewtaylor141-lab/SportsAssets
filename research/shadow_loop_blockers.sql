-- WHAT IS ACTUALLY STOPPING THE AUTONOMOUS SHADOW LOOP, by name and count.
--
-- Read-only. Every query is a SELECT. Run through research-sql.
--
-- WHY THIS FILE EXISTS. The stage census says 44 candidates stopped at
-- 1_PROBABILITY and 35 at 4_SETTLEMENT_SCOPE, but a stage groups several
-- refusal codes and the remedy differs per code. "No probability" could be
-- an absent odds credential, a book that does not quote the fixture, or a
-- de-vig that refused a partial outcome set -- three different problems.
-- So this asks for the CODES, and for the calibration row separately,
-- because calibration blocks at stage 7 and no candidate has reached it.

\echo '== 1 · refusal codes, 24h window, entry lane =='
SELECT unnest(refusals) AS refusal, count(*) AS n
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC, 1;

\echo ''
\echo '== 2 · the window totals these codes belong to =='
SELECT count(*) AS evaluated,
       count(*) FILTER (WHERE admissible)          AS admissible,
       count(*) FILTER (WHERE NOT admissible)      AS refused,
       count(*) FILTER (WHERE probability IS NOT NULL) AS priced,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS venue_native,
       count(*) FILTER (WHERE condition_id IS NOT NULL)   AS global_id,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '24 hours';

\echo ''
\echo '== 3 · IS THE SOURCE CALIBRATED? (a row, not a constant) =='
SELECT source_version, sample_size, metric, score, tolerance,
       within_tolerance, measured_by, measured_at
  FROM external_source_calibration
 ORDER BY measured_at DESC LIMIT 5;

\echo '(zero rows above means NOT MEASURED, which is the honest state)'

\echo ''
\echo '== 4 · how many candidates got FAR ENOUGH for calibration to matter =='
-- Calibration gates stage 7 (RISK_GATE_BLOCKED / MODEL_TRUST_DRIFT). A
-- candidate only reaches it after probability, freshness, identity,
-- settlement scope, the execution estimate and sizing all succeeded. If
-- this is 0, calibration is NOT the binding blocker, whatever else is true.
SELECT count(*) AS reached_risk_stage
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '24 hours'
   AND ('RISK_GATE_BLOCKED' = ANY(refusals)
        OR 'MODEL_TRUST_DRIFT' = ANY(refusals));

\echo ''
\echo '== 5 · did any candidate clear probability AND settlement scope? =='
SELECT count(*) AS cleared_1_and_4
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '24 hours'
   AND probability IS NOT NULL
   AND NOT ('SETTLEMENT_SCOPE_NOT_ESTABLISHED' = ANY(refusals))
   AND NOT ('VOID_ABANDONMENT_RULE_NOT_ESTABLISHED' = ANY(refusals))
   AND NOT ('OVERTIME_RULE_NOT_ESTABLISHED' = ANY(refusals))
   AND NOT ('SETTLEMENT_TERMS_CONFLICT' = ANY(refusals))
   AND NOT ('UNRESOLVED_SETTLEMENT_SEMANTICS' = ANY(refusals));

\echo ''
\echo '== 6 · inventory: which positions exist, and were they autonomous? =='
-- An acceptance-seeded position is NOT an autonomous entry. The two are
-- separated here so a dashboard number can never conflate them.
SELECT experiment_id, policy_version,
       count(*) AS positions,
       min(decision_ts) AS first_at, max(decision_ts) AS last_at
  FROM rn1x_positions
 GROUP BY 1, 2 ORDER BY 5 DESC NULLS LAST LIMIT 20;

\echo ''
\echo '== 7 · orders, fills and outcomes attached to those positions =='
SELECT p.experiment_id, p.policy_version,
       count(DISTINCT p.position_id) AS positions,
       count(DISTINCT o.order_id)    AS orders,
       count(DISTINCT f.fill_id)     AS fills,
       count(DISTINCT x.position_id) AS with_outcome
  FROM rn1x_positions p
  LEFT JOIN rn1x_orders   o ON o.position_id = p.position_id
  LEFT JOIN rn1x_fills    f ON f.order_id    = o.order_id
  LEFT JOIN rn1x_outcomes x ON x.position_id = p.position_id
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo ''
\echo '== 8 · management decisions per position, newest first =='
SELECT d.position_id, count(*) AS decisions,
       min(d.decided_at) AS first_at, max(d.decided_at) AS last_at
  FROM rn1x_decisions d
 GROUP BY 1 ORDER BY 4 DESC NULLS LAST LIMIT 15;

\echo ''
\echo '== 9 · is the entry lane armed, and when did it last cycle? =='
SELECT key, left(value::text, 300) AS value
  FROM ingestion_state
 WHERE key LIKE '%ext_pinnacle%' OR key LIKE '%rn1x%'
    OR key LIKE '%EXT_PINNACLE%'
 ORDER BY 1;
