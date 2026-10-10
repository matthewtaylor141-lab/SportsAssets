-- RC6.3c PROOF A: FINGERPRINT OF EVERY HISTORICAL PAPER ROW (SELECT only; nothing here writes).
--
-- PURPOSE. Prove that the RC6.3c deploy changed NO historical PAPER record: run this file before the deploy and
-- again after it with the SAME cutoff instant, and the two outputs must be byte-identical (apart from took_s).
-- Every digest covers only rows whose own creation clock is before the cutoff, so rows the running system
-- appends after the cutoff never enter it; a row changed, removed or back-dated before the cutoff changes it.
--
-- PLACEHOLDER. 2026-10-10 19:00:00+00 : an ISO timestamp with zone (e.g. 2026-10-10 19:00:00+00), fixed once, used for both
-- runs. It must be at or before the deploy instant.
--
-- THE DIGEST. Per table: the row count before the cutoff, and md5 over (count, three order-independent sums of
-- 60-bit slices of each row's md5). Order independence is by construction (sums), so no sort runs and the read
-- replica's temporary space is never used (the sorted string_agg form spilled to disk on 2026-10-10, run
-- 38078866534). A row is hashed as its whole text (t::text) unless the table is named [scalar columns],
-- [immutable columns], [identity], [terminal ...] or [finished ...] below.
--
-- WHICH TABLES, AND WHY. Sections F1-F3: tables the database itself keeps append-only (a BEFORE trigger on every rewrite or removal
-- raises: migrations 171, 172, 185, 186, 189, 223, 270, 290, 305, 309, 311; production has every trigger,
-- probe run 38078866534 Q3), plus paper_hook_failures (no trigger; no code path writes to an existing row).
-- paper_decisions (4 GB) and paper_xavier_reviews (1.6 GB) are hashed on their scalar columns only -- their jsonb
-- payloads would mean reading 5.6 GB of TOAST per run; their append-only triggers cover the payloads.
-- Section F4: tables the code legitimately writes to after creation, hashed on what the code never touches:
--   paper_orders               bettor_paper_ledger.py:902 (fill: filled_qty, state, reserved_remaining_usd, terminal_at,
--                              terminal_reason, updated_at), :934 (release: state, reserved_remaining_usd, terminal_at,
--                              terminal_reason, updated_at); bettor_paper_simulator.py:759 (queue_basis, queue_ahead_qty,
--                              updated_at), :801 (state=CANCEL_PENDING, updated_at) -- those columns are excluded; and a
--                              second digest hashes the WHOLE row of orders already terminal before the cutoff (no code
--                              path writes to a terminal order: both ledger paths check the state is open first).
--   paper_handoffs             agents/paper_xavier.py:202-210 (ON CONFLICT ... confirmed_qty, outstanding_qty, updated_at).
--   paper_audrey_findings      agents/paper_audrey.py:692 (improvement_task_id, once, from NULL).
--   paper_improvement_proposals agents/paper_learning.py:2190, :2204, :2425, :2430, :2517, :2528 (status, evaluation,
--                              verdict, evaluated_*, active, activated_*, deactivated_at, last_attempt, updated_at).
--   paper_recommendations      agents/paper_ops_audit.py:462 (status, updated_at).
--   paper_exit_intents         agents/paper_exit_intents.py:133 (state machine: state, transitions, updated_at and the
--                              transition's own columns) -- identity (intent_id, decided_at) for every intent decided
--                              before the cutoff, the whole row for intents resolved before it.
--   paper_mark_refresh_runs    agents/paper_mark_refresh.py:581 (the run's one permitted completion write, migration
--                              270 trigger) -- whole row for runs finished before the cutoff.
--   paper_sessions             status / stopped_at / stopped_why may change; config, config_sha, simulator_version,
--                              started_at, account_id are frozen by migration 171's trigger and are what is hashed.
-- Section F5 (counts only, not hashed, each a live counter or control row, not a ledger record):
--   paper_session_health       bettor_paper_session.py:302 record_pass (passes, errors, heartbeats), agents/paper_runtime.py:1709.
--   paper_control              enablement rows (enabled, updated_by, updated_at).
--   paper_liquidity_consumed   bettor_paper_simulator.py:453 ON CONFLICT raises consumed_qty; the table has no creation clock.
--   paper_policy_parameter_heads agents/paper_learning.py:2421, :2512 (the current-version pointer).
-- Section F6: the immutable historical loss record -- realized P&L per account and strategy from fills filled and
-- settlements settled before the cutoff (bettor_paper_ledger._position_from arithmetic: proceeds - avg x sold +
-- payout - avg x settled, avg = BUY cost incl. fees / bought), and the ledger's cash by kind before the cutoff.
--
-- PASS CRITERIA. Before/after outputs identical for every row of F1-F4 and F6 (rows_before_cutoff, digest,
-- newest_before_cutoff). F5 counts may only grow (session health passes/errors) or stay. Any differing digest =
-- a historical PAPER row was changed, removed or back-dated: FAIL, name the table.
--
-- SIZE (probe 38078866534 Q1/Q13): ledger 2,309 rows; fills 1,219; orders 11,628; order_events 414,016 (162 MB);
-- settlements 195; decisions 200,153 (4 GB incl. jsonb); book observations 195,887 (245 MB); xavier reviews
-- 172,313 (1.6 GB incl. jsonb). Every statement below finishes well inside the 600 s statement timeout; took_s
-- is printed per branch (seconds since the statement started).
\echo F1 ledger core (database append-only): rows before the cutoff and their digest
SELECT 'paper_ledger' AS tbl, 'committed_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, committed_at AS c FROM paper_ledger t WHERE committed_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_fills' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_fills t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_order_events' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_order_events t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_settlements' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_settlements t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_accounts' AS tbl, 'created_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, created_at AS c FROM paper_accounts t WHERE created_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_equity_snapshots' AS tbl, 'at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, at AS c FROM paper_equity_snapshots t WHERE at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_book_observations' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_book_observations t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x;
\echo F2 decisions and agent records (database append-only; two large tables on scalar columns)
SELECT 'paper_decisions [scalar columns]' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(row(decision_id, session_id, account_id, decided_at, valuation_id, us_market_slug, holding_side, intent, fixture, verdict, refusal, refusals, p_internal, p_pinnacle, p_blended, book_obs_id, proposed_qty, limit_price, policy_version, simulator_version, recorded_at, strategy)::text) AS h, recorded_at AS c FROM paper_decisions t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_xavier_reviews [scalar columns]' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(row(review_id, session_id, account_id, group_id, reviewed_at, trigger, recommendation, refusal, recorded_at, strategy)::text) AS h, recorded_at AS c FROM paper_xavier_reviews t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_audrey_reports' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_audrey_reports t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_agent_lessons' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_agent_lessons t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_evaluation_attempts' AS tbl, 'at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, at AS c FROM paper_evaluation_attempts t WHERE at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_hook_failures' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_hook_failures t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_sleeve_classifications' AS tbl, 'classified_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, classified_at AS c FROM paper_sleeve_classifications t WHERE classified_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_strategy_lifecycle_events' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_strategy_lifecycle_events t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_management_refusals' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_management_refusals t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x;
\echo F3 capital authority, profitability and policy records (database append-only)
SELECT 'paper_shadow_counterfactuals' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_shadow_counterfactuals t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_shadow_counterfactual_outcomes' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_shadow_counterfactual_outcomes t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_entry_refusal_census' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_entry_refusal_census t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_cash_decisions' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_cash_decisions t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_counterfactual_variants' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_counterfactual_variants t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_counterfactual_variant_outcomes' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_counterfactual_variant_outcomes t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_profitability_models' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_profitability_models t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_profitability_evaluations' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_profitability_evaluations t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_policy_parameter_versions' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_policy_parameter_versions t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_policy_parameter_activations' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_policy_parameter_activations t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_improvement_proposal_events' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, recorded_at AS c FROM paper_improvement_proposal_events t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_recommendation_events' AS tbl, 'at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, at AS c FROM paper_recommendation_events t WHERE at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x;
\echo F4 tables the code writes to after creation: immutable columns, or rows terminal before the cutoff
SELECT 'paper_orders [immutable columns]' AS tbl, 'created_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5((to_jsonb(t) - ARRAY['state', 'filled_qty', 'reserved_remaining_usd', 'terminal_at', 'terminal_reason', 'updated_at', 'queue_basis', 'queue_ahead_qty'])::text) AS h, created_at AS c FROM paper_orders t WHERE created_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_orders [terminal before cutoff, full row]' AS tbl, 'created_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, created_at AS c FROM paper_orders t WHERE created_at < TIMESTAMPTZ '2026-10-10 19:00:00+00' AND terminal_at < TIMESTAMPTZ '2026-10-10 19:00:00+00' AND state IN ('FILLED', 'EXPIRED', 'CANCELED', 'REJECTED')) x
UNION ALL
SELECT 'paper_handoffs [immutable columns]' AS tbl, 'created_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5((to_jsonb(t) - ARRAY['confirmed_qty', 'outstanding_qty', 'updated_at'])::text) AS h, created_at AS c FROM paper_handoffs t WHERE created_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_audrey_findings [immutable columns]' AS tbl, 'recorded_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5((to_jsonb(t) - ARRAY['improvement_task_id'])::text) AS h, recorded_at AS c FROM paper_audrey_findings t WHERE recorded_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_improvement_proposals [immutable columns]' AS tbl, 'created_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5((to_jsonb(t) - ARRAY['status', 'last_attempt', 'evaluation', 'verdict', 'evaluated_at', 'evaluated_by', 'active', 'activated_by', 'activated_at', 'deactivated_at', 'updated_at'])::text) AS h, created_at AS c FROM paper_improvement_proposals t WHERE created_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_recommendations [immutable columns]' AS tbl, 'created_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5((to_jsonb(t) - ARRAY['status', 'updated_at'])::text) AS h, created_at AS c FROM paper_recommendations t WHERE created_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_exit_intents [identity]' AS tbl, 'decided_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(row(intent_id, decided_at)::text) AS h, decided_at AS c FROM paper_exit_intents t WHERE decided_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_exit_intents [resolved before cutoff, full row]' AS tbl, 'decided_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, decided_at AS c FROM paper_exit_intents t WHERE decided_at < TIMESTAMPTZ '2026-10-10 19:00:00+00' AND resolved_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_mark_refresh_runs [finished before cutoff, full row]' AS tbl, 'started_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5(t::text) AS h, started_at AS c FROM paper_mark_refresh_runs t WHERE started_at < TIMESTAMPTZ '2026-10-10 19:00:00+00' AND finished_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x
UNION ALL
SELECT 'paper_sessions [frozen columns]' AS tbl, 'started_at' AS cutoff_col, count(*) AS rows_before_cutoff,
       md5(count(*)::text || ':' || coalesce(sum(('x' || substr(h, 1, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 17, 15))::bit(60)::bigint), 0)::text || ':' || coalesce(sum(('x' || substr(h, 2, 15))::bit(60)::bigint), 0)::text) AS digest,
       to_char(max(c), 'YYYY-MM-DD HH24:MI:SS.US') AS newest_before_cutoff,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM (SELECT md5((to_jsonb(t) - ARRAY['status', 'stopped_at', 'stopped_why'])::text) AS h, started_at AS c FROM paper_sessions t WHERE started_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') x;
\echo F5 live counters and control rows (counts only; see the header for why they are not hashed)
SELECT 'paper_session_health' AS tbl, count(*) AS rows_total, max(passes)::text AS note FROM paper_session_health
UNION ALL SELECT 'paper_control', count(*), count(*) FILTER (WHERE enabled)::text || ' enabled' FROM paper_control
UNION ALL SELECT 'paper_liquidity_consumed', count(*), round(sum(consumed_qty), 6)::text || ' consumed_qty' FROM paper_liquidity_consumed
UNION ALL SELECT 'paper_policy_parameter_heads', count(*), string_agg(policy_key, ',' ORDER BY policy_key) FROM paper_policy_parameter_heads;
\echo F6a the historical loss record: realized P&L per account and strategy from fills and settlements before the cutoff
WITH f AS (
  SELECT account_id, group_id, us_market_slug, holding_side, max(strategy) AS strategy,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY'), 0) AS buy_cost,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold,
         coalesce(sum(gross_usd - fee_usd) FILTER (WHERE direction = 'SELL'), 0) AS proceeds
    FROM paper_fills WHERE filled_at < TIMESTAMPTZ '2026-10-10 19:00:00+00'
   GROUP BY 1, 2, 3, 4),
s AS (
  SELECT DISTINCT ON (position_key) position_key, qty AS settled_qty, payout_usd, outcome
    FROM paper_settlements WHERE settled_at < TIMESTAMPTZ '2026-10-10 19:00:00+00'
   ORDER BY position_key, version DESC),
p AS (
  SELECT f.*, coalesce(s.settled_qty, 0) AS settled_qty, coalesce(s.payout_usd, 0) AS payout_usd,
         CASE WHEN f.bought > 0 THEN f.buy_cost / f.bought ELSE 0 END AS avg_cost
    FROM f LEFT JOIN s ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side)
SELECT account_id, strategy, count(*) AS positions,
       count(*) FILTER (WHERE bought - sold - settled_qty > 1e-9) AS open_positions,
       round(sum(bought), 6) AS bought, round(sum(sold), 6) AS sold, round(sum(settled_qty), 6) AS settled,
       round(sum(buy_cost), 6) AS acquisition_cost_usd, round(sum(proceeds), 6) AS sale_proceeds_net_usd,
       round(sum(payout_usd), 6) AS settlement_payout_usd,
       round(sum((proceeds - avg_cost * sold) + CASE WHEN settled_qty > 0 THEN payout_usd - avg_cost * settled_qty ELSE 0 END), 6) AS realized_pnl_usd
  FROM p GROUP BY 1, 2 ORDER BY 1, 2;
\echo F6b the ledger before the cutoff: rows and cash by kind per account, and the last entry
SELECT account_id, kind, count(*) AS rows_before_cutoff, round(sum(cash_delta_usd), 6) AS cash_delta_usd, round(sum(reserved_delta_usd), 6) AS reserved_delta_usd
  FROM paper_ledger WHERE committed_at < TIMESTAMPTZ '2026-10-10 19:00:00+00' GROUP BY 1, 2 ORDER BY 1, 2;
SELECT DISTINCT ON (account_id) account_id, seq AS last_seq_before_cutoff, cash_after_usd, reserved_after_usd,
       to_char(committed_at, 'YYYY-MM-DD HH24:MI:SS.US') AS committed_at,
       (SELECT round(sum(cash_delta_usd), 6) FROM paper_ledger q WHERE q.account_id = l.account_id AND q.committed_at < TIMESTAMPTZ '2026-10-10 19:00:00+00') AS cash_sum_before_cutoff
  FROM paper_ledger l WHERE committed_at < TIMESTAMPTZ '2026-10-10 19:00:00+00' ORDER BY account_id, seq DESC;
