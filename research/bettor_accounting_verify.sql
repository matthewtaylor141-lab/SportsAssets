\echo ''
\echo '=== BETTOR MANAGEMENT ACCOUNTING: PRODUCTION VERIFICATION ==='
-- Owner standing rule: "Do not call the fix successful merely because
-- tests pass. Verify it prospectively in production."
--
-- FIVE QUESTIONS, IN ORDER, and each one is answerable from a row
-- rather than from an inference:
--
--   1. Which build is actually running, and did it apply 074?
--   2. Is the $1,000 standard frozen, and with which hash?
--   3. Is BETTOR still collecting -- was the reporting work paid for
--      with a pause in the thing it reports on?
--   4. Did the P_FILL correction take effect PROSPECTIVELY, leaving
--      the rows already written exactly as they were?
--   5. Are the accounting numbers the honest zeros, or has something
--      appeared that nobody authorised?
--
-- Read-only by construction: SELECT statements only.

\echo ''
\echo '--- 1. RUNNING BUILD, from the workers own boot marker ---'
-- "Do not infer deployment from Git SHA alone."
SELECT 'build|' || COALESCE(value ->> 'commit', 'NOT_IDENTIFIED')
       || '|' || COALESCE(value ->> 'at', 'NOT_IDENTIFIED')
       || '|age_s=' || COALESCE(round(extract(epoch FROM
             (now() - (value ->> 'at')::timestamptz)))::text,
             'NOT_IDENTIFIED')
       || '|policyFreeze=' || COALESCE(value ->> 'policyFreeze', 'ABSENT')
       || '|sizingFreeze=' || COALESCE(value ->> 'sizingFreeze', 'ABSENT')
       || '|standardNotional='
       || COALESCE(value ->> 'standardNotionalUsd', 'ABSENT')
  FROM ingestion_state WHERE key = 'workers_boot';

\echo ''
\echo '--- MIGRATION 074 OBJECTS ---'
SELECT 'object|bettor_sizing_policies|' || CASE
         WHEN to_regclass('public.bettor_sizing_policies') IS NULL
         THEN 'ABSENT' ELSE 'PRESENT' END;

SELECT 'object|bettor_capital_timeline|' || CASE
         WHEN to_regclass('public.bettor_capital_timeline') IS NULL
         THEN 'ABSENT' ELSE 'PRESENT' END;

-- THE DOLLAR COLUMNS, named individually. "present" as a count would
-- hide which one is missing.
SELECT 'column|shadow_executions.' || c.column_name || '|PRESENT'
  FROM information_schema.columns c
 WHERE c.table_name = 'shadow_executions'
   AND c.column_name IN ('execution_leg_kind', 'sizing_policy_version',
                         'intended_notional_usd', 'executed_notional_usd',
                         'unfilled_notional_usd', 'fees_usd',
                         'spread_cost_usd', 'slippage_cost_usd',
                         'adverse_selection_usd')
 ORDER BY c.column_name;

SELECT 'constraint|' || conname || '|PRESENT'
  FROM pg_constraint
 WHERE conname IN ('shadow_exec_leg_kind', 'shadow_exec_notional_balances',
                   'shadow_exec_no_invented_liquidity',
                   'shadow_exec_notional_non_negative',
                   'bettor_sizing_lane', 'bettor_sizing_positive')
 ORDER BY conname;

\echo ''
\echo '--- 2. THE FROZEN $1,000 STANDARD ---'
SELECT 'sizing|' || s.sizing_policy_version || '|' || s.cohort || '|'
       || s.lane || '|' || s.standard_notional_usd::text || '|'
       || left(s.policy_sha, 16) || '|' || s.frozen_at::text
  FROM bettor_sizing_policies s
 ORDER BY s.frozen_at;

-- EXACTLY ONE, and it must be BETTOR's. A second row under another
-- lane would mean RN1 economics had been given a sizing rule here.
SELECT 'sizing_count|' || count(*)::text || '|non_bettor='
       || count(*) FILTER (WHERE lane <> 'BETTOR_EV_SHADOW')::text
  FROM bettor_sizing_policies;

-- THE EV POLICY MUST BE UNDISTURBED. Its declaration hash is what the
-- decision foreign key effectively protects; if this moved, the freeze
-- was edited under a version already carrying rows.
SELECT 'ev_policy|' || p.policy_version || '|' || left(p.policy_sha, 16)
       || '|code=' || left(COALESCE(p.policy_code_sha, 'NONE'), 16)
       || '|sizing_declared=' || COALESCE(p.sizing_policy_version, 'NULL')
  FROM shadow_policy_versions p
 ORDER BY p.policy_version;

\echo ''
\echo '--- 3. IS BETTOR STILL COLLECTING? ---'
-- "Do not delay prospective BETTOR collection while adding this
-- reporting." The newest opportunity and decision must be RECENT, not
-- merely nonzero.
SELECT 'collection|opportunities|' || count(*)::text || '|newest='
       || COALESCE(max(observed_at)::text, 'NONE') || '|age_s='
       || COALESCE(round(extract(epoch FROM
             (now() - max(observed_at))))::text, 'NONE')
  FROM bettor_opportunities;

SELECT 'collection|decisions|' || count(*)::text || '|newest='
       || COALESCE(max(created_at)::text, 'NONE') || '|age_s='
       || COALESCE(round(extract(epoch FROM
             (now() - max(created_at))))::text, 'NONE')
  FROM shadow_decisions WHERE lane = 'BETTOR_EV_SHADOW';

-- Since the running build booted: the only window that tests THIS code.
SELECT 'collection|since_boot|opportunities='
       || (SELECT count(*) FROM bettor_opportunities o
            WHERE o.observed_at > (SELECT (value ->> 'at')::timestamptz
                                     FROM ingestion_state
                                    WHERE key = 'workers_boot'))::text
       || '|decisions='
       || (SELECT count(*) FROM shadow_decisions d
            WHERE d.lane = 'BETTOR_EV_SHADOW'
              AND d.created_at > (SELECT (value ->> 'at')::timestamptz
                                    FROM ingestion_state
                                   WHERE key = 'workers_boot'))::text;

SELECT 'collection|orphans|' || count(*)::text
  FROM bettor_orphan_opportunities;

SELECT 'collection|write_failures|' || count(*)::text || '|newest='
       || COALESCE(max(failed_at)::text, 'NONE')
  FROM bettor_decision_failures;

\echo ''
\echo '--- 4. P_FILL VOCABULARY, PROSPECTIVE ONLY ---'
-- The correction must show up in NEW rows and leave the old ones
-- exactly as written. Two buckets, split at the running build's boot.
SELECT 'p_fill|' || CASE
         WHEN d.created_at > (SELECT (value ->> 'at')::timestamptz
                                FROM ingestion_state WHERE key = 'workers_boot')
         THEN 'AFTER_BOOT' ELSE 'BEFORE_BOOT' END
       || '|' || COALESCE(d.p_fill_status, 'NULL')
       || '|' || count(*)::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- pBETTOR must still be NOT_ESTABLISHED everywhere: a BELIEF and a
-- QUANTITY fall back to different words, and only pFill moved.
SELECT 'p_bettor|' || COALESCE(d.p_bettor_status, 'NULL') || '|'
       || count(*)::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 GROUP BY 1 ORDER BY 1;

-- THE ROWS ALREADY WRITTEN ARE UNTOUCHED. An append-only table cannot
-- be updated at all, but the count and the oldest instant are printed
-- so the claim is checkable rather than asserted.
SELECT 'pre_correction_rows|' || count(*)::text || '|oldest='
       || COALESCE(min(created_at)::text, 'NONE') || '|newest='
       || COALESCE(max(created_at)::text, 'NONE')
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
   AND d.p_fill_status = 'NOT_ESTABLISHED';

\echo ''
\echo '--- 5. THE ACCOUNTING NUMBERS THEMSELVES ---'
-- Everything must still be the honest zero. Anything else means a
-- shadow entry appeared, which nothing today is authorised to produce.
SELECT 'acct|bettor_positions|' || count(*)::text
  FROM shadow_positions p
  JOIN shadow_decisions d ON d.shadow_decision_id = p.originating_decision_id
 WHERE d.lane = 'BETTOR_EV_SHADOW';

SELECT 'acct|bettor_executions|' || count(*)::text
  FROM shadow_executions e
  JOIN shadow_decisions d ON d.shadow_decision_id = e.shadow_decision_id
 WHERE d.lane = 'BETTOR_EV_SHADOW';

SELECT 'acct|entry_notional_played|'
       || COALESCE(sum(CASE WHEN e.execution_leg_kind = 'ENTRY'
                            THEN COALESCE(e.executed_notional_usd, 0) END),
                   0)::text
       || '|gross_turnover|'
       || COALESCE(sum(COALESCE(e.executed_notional_usd, 0)), 0)::text
  FROM shadow_executions e
  JOIN shadow_decisions d ON d.shadow_decision_id = e.shadow_decision_id
 WHERE d.lane = 'BETTOR_EV_SHADOW'
   AND e.execution_class IN ('MARKETABLE_RECONSTRUCTED', 'ACTUAL_FILL');

SELECT 'acct|capital_timeline_rows|' || count(*)::text
  FROM bettor_capital_timeline;

-- "Do not consider NO_TRADE a failure" -- but an ACTING decision on
-- this lane today would be a finding, because the frozen action set is
-- exactly [NO_TRADE].
SELECT 'acct|action|' || d.proposed_action || '|' || count(*)::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 GROUP BY 1 ORDER BY 1;

\echo ''
\echo '--- SAFETY, RESTATED FROM ROWS ---'
SELECT 'safety|shadow_mode_false_rows|' || count(*)::text
  FROM shadow_decisions WHERE shadow_mode IS NOT TRUE;

SELECT 'safety|capital_at_risk_nonzero_rows|' || count(*)::text
  FROM shadow_decisions WHERE capital_at_risk <> 0;

SELECT 'safety|bettor_rn1_features_used|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND rn1_features_used IS NOT FALSE;

\echo ''
\echo '--- WORKER HEARTBEATS ---'
SELECT 'heartbeat|' || h.service || '|' || h.status || '|age_s='
       || round(extract(epoch FROM (now() - h.beat_at)))::text
  FROM service_heartbeats h
 WHERE h.service IN ('shadow_bettor', 'shadow_rn1', 'chain_listener')
 ORDER BY h.service;
