-- RC6 lane P0-ECONOMICS, read only: THE POST-DEPLOY READBACK of the
-- profitability bind's control 25 (rc6/econ-binding). Before the release
-- carrying it is deployed, sections A-C read zero rows WITH control
-- provenance (that is the expected "not deployed" answer, never a pass);
-- after it, every bind evaluation names each control PASS / REFUSED /
-- NOT_REACHED, a missing input is CASH under its own code, and no paper
-- ENTRY order exists without its LEDGER evaluation and its 6 variants.
\echo === A. bind evaluations (6 h): how many carry control provenance ===
SELECT e.stage, e.strategy,
       count(*) AS evaluations,
       count(*) FILTER (WHERE e.detail ? 'controls'
                          AND jsonb_typeof(e.detail -> 'controls') = 'object')
         AS with_control_provenance,
       min(e.evaluated_at) FILTER (WHERE e.detail ? 'controls'
                                    AND jsonb_typeof(e.detail -> 'controls') = 'object')
         AS first_with_provenance
  FROM paper_profitability_evaluations e
 WHERE e.evaluated_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === B. each control status over the evaluations that carry provenance (6 h) ===
SELECT c.key AS control, c.value ->> 'status' AS status,
       coalesce(c.value ->> 'code', '') AS code, count(*) AS evaluations
  FROM paper_profitability_evaluations e,
       jsonb_each(CASE WHEN jsonb_typeof(e.detail -> 'controls') = 'object'
                       THEN e.detail -> 'controls' ELSE '{}'::jsonb END) c
 WHERE e.evaluated_at > now() - interval '6 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
\echo === C. refusals by the new control-input codes (6 h): evaluations and census ===
SELECT 'evaluation' AS source, e.stage, e.refusal, count(*) AS n
  FROM paper_profitability_evaluations e
 WHERE e.evaluated_at > now() - interval '6 hours'
   AND (e.refusal LIKE 'CASH_WAIT_%_NOT_CURRENT'
        OR e.refusal LIKE 'CASH_WAIT_%_AT_BIND'
        OR e.refusal LIKE 'CASH_WAIT_%_OBSERVATION_TIME_MISSING'
        OR e.refusal = 'CASH_WAIT_PROBABILITY_NOT_IN_ZERO_ONE')
 GROUP BY 1, 2, 3
UNION ALL
SELECT 'census', n.stage, n.refusal, count(*)
  FROM paper_entry_refusal_census n
 WHERE n.refused_at > now() - interval '6 hours'
   AND (n.refusal LIKE 'CASH_WAIT_%_NOT_CURRENT'
        OR n.refusal LIKE 'CASH_WAIT_%_AT_BIND'
        OR n.refusal LIKE 'CASH_WAIT_%_OBSERVATION_TIME_MISSING'
        OR n.refusal = 'CASH_WAIT_PROBABILITY_NOT_IN_ZERO_ONE'
        OR n.refusal = 'ENTRY_EVALUATION_OR_COUNTERFACTUAL_VARIANTS_NOT_RECORDED')
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC;
\echo === D. paper ENTRY orders (6 h) and their record: LEDGER ENTER evaluation, variants, provenance ===
SELECT o.strategy, count(*) AS entry_orders,
       count(*) FILTER (WHERE e.eval_id IS NULL) AS without_ledger_enter_evaluation,
       count(*) FILTER (WHERE e.eval_id IS NOT NULL AND e.p_used IS NOT NULL
                          AND coalesce(v.n, 0) < 6) AS priced_without_full_variants,
       count(*) FILTER (WHERE e.eval_id IS NOT NULL
                          AND NOT (e.detail ? 'controls')) AS without_control_provenance
  FROM paper_orders o
  LEFT JOIN LATERAL (
       SELECT x.eval_id, x.p_used, x.detail FROM paper_profitability_evaluations x
        WHERE x.order_key = o.idempotency_key AND x.stage = 'LEDGER'
          AND x.verdict = 'ENTER'
        ORDER BY x.eval_id DESC LIMIT 1) e ON true
  LEFT JOIN LATERAL (
       SELECT count(*) AS n FROM paper_counterfactual_variants y
        WHERE y.eval_id = e.eval_id) v ON true
 WHERE o.role = 'ENTRY' AND o.direction = 'BUY'
   AND o.decided_at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 1;
\echo === E. the learned model ages now, by kind (control 25 bound: 3600 s) ===
SELECT m.account_id, m.kind,
       round(extract(epoch FROM now() - max(m.fitted_at))::numeric, 1) AS age_s
  FROM paper_profitability_models m
 WHERE m.fitted_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === F. CASH decisions (6 h) and the read instant ===
SELECT c.strategy, count(*) AS cash_passes, sum(c.decisions_evaluated) AS decisions,
       max(c.pass_at) AS last_cash_pass, now() AS read_at
  FROM paper_cash_decisions c
 WHERE c.pass_at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 1;
