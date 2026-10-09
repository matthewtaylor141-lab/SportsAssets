-- READ-ONLY. RC6 lane redteam-scenarios: the evidence behind the red-team
-- controls that read RED / UNKNOWN on RC5 (pm-acceptance 37836393458), to
-- classify each one as a software defect, forward evidence still accruing
-- (count, rate, date) or an owner action. Every statement is a SELECT.

\echo T1 DIGITAL_TWIN accrual: fresh marketable PAPER orders (completion.evidence TWIN_SQL predicate) per UTC day since the diagnosis window end 2026-10-07T14:56:26Z
SELECT to_char(date_trunc('day', o.eligible_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
       count(*) AS twin_eligible_orders,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM paper_book_observations b
          WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
            AND b.observed_at >= o.eligible_at AND b.observed_at <= o.expires_at)) AS with_book_in_window,
       count(*) FILTER (WHERE o.role = 'ENTRY') AS entry,
       count(*) FILTER (WHERE o.role = 'EXIT') AS exit_,
       count(*) FILTER (WHERE o.role = 'REDUCE') AS reduce_
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > timestamptz '2026-10-07 14:56:26+00'
 GROUP BY 1 ORDER BY 1;

\echo T2 the same predicate per UTC day over the 21 days BEFORE the window end (the historical accrual rate the twin would have had)
SELECT to_char(date_trunc('day', o.eligible_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
       count(*) AS twin_eligible_orders
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > timestamptz '2026-10-07 14:56:26+00' - interval '21 days'
   AND o.eligible_at <= timestamptz '2026-10-07 14:56:26+00'
 GROUP BY 1 ORDER BY 1;

\echo T3 every PAPER order since the window end by role x time_in_force x state (what is being placed that the twin does or does not count)
SELECT o.role, o.time_in_force AS tif, o.order_type, o.state,
       count(*) AS n, (o.terminal_at IS NOT NULL) AS terminal,
       to_char(min(o.eligible_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS first_eligible,
       to_char(max(o.eligible_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS last_eligible
  FROM paper_orders o
 WHERE o.eligible_at > timestamptz '2026-10-07 14:56:26+00'
 GROUP BY 1, 2, 3, 4, 6 ORDER BY n DESC;

\echo K1 KAREN_VALUE: every twin_agent_scorecards row for KAREN in the newest 3 runs (book, status, sample) -- the red-team reader filters book = PAPER
SELECT s.run_id, to_char(s.computed_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS computed,
       s.metric, s.book, s.status, s.reason, s.sample_n,
       round(s.value::numeric, 4) AS value
  FROM twin_agent_scorecards s
 WHERE s.agent = 'KAREN'
   AND s.run_id IN (SELECT run_id FROM twin_agent_scorecards
                     GROUP BY run_id ORDER BY max(computed_at) DESC LIMIT 3)
 ORDER BY s.computed_at DESC, s.metric, s.book;

\echo K2 twin_agent_scorecards runs: count, newest, and KAREN rows by (metric, book) across ALL runs
SELECT count(DISTINCT run_id) AS runs,
       to_char(max(computed_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI') AS newest,
       to_char(min(computed_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI') AS oldest
  FROM twin_agent_scorecards;
SELECT metric, book, status, count(*) AS rows_, max(sample_n) AS max_sample
  FROM twin_agent_scorecards WHERE agent = 'KAREN'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo H1 SAMPLE_INTEGRITY / MULTIPLE_TESTING: red_team_holdout_registry rows by kind (no writer exists in the code)
SELECT kind, count(*) AS n, max(candidate_count) AS max_candidates,
       to_char(max(at) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI') AS newest
  FROM red_team_holdout_registry GROUP BY 1 ORDER BY 1;
SELECT count(*) AS registry_rows FROM red_team_holdout_registry;

\echo C1 CAPACITY: the 14-day evaluations behind redteam.readiness.capacity_points per size bucket
WITH r AS (
  SELECT fixture, qty_in::float8 AS q, ev_per_contract_usd::float8 AS ev,
         fill_probability::float8 AS fp, all_in_ev_usd::float8 AS net
    FROM paper_profitability_evaluations
   WHERE evaluated_at > now() - interval '14 days'
     AND qty_in IS NOT NULL AND ev_per_contract_usd IS NOT NULL),
b AS (
  SELECT CASE WHEN q <= 10 THEN '001-010' WHEN q <= 50 THEN '011-050'
              WHEN q <= 100 THEN '051-100' WHEN q <= 500 THEN '101-500'
              ELSE '501+' END AS bucket, * FROM r)
SELECT bucket, count(*) AS rows_, count(DISTINCT fixture) AS fixtures,
       round(avg(ev)::numeric, 5) AS mean_ev_per_contract,
       round((count(*) FILTER (WHERE ev > 0))::numeric / count(*), 4) AS share_ev_positive,
       round(avg(fp)::numeric, 4) AS mean_fill_probability,
       round(sum(net)::numeric, 2) AS sum_all_in_ev_usd
  FROM b GROUP BY 1 ORDER BY 1;

\echo C2 CAPACITY accrual: evaluations per UTC day (last 14 days) and the share with positive EV per contract
SELECT to_char(date_trunc('day', evaluated_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
       count(*) AS evaluations,
       count(*) FILTER (WHERE ev_per_contract_usd > 0) AS ev_positive,
       count(*) FILTER (WHERE all_in_ev_usd > 0) AS net_positive
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - interval '14 days'
 GROUP BY 1 ORDER BY 1;

\echo Q1 TRUTH_QUORUM sources: Audrey reconciliation and the execution-mirror snapshot ages (seconds)
SELECT 'smalllive_reconciliations' AS source, count(*) AS n,
       round(extract(epoch FROM now() - max(reconciled_at))::numeric, 0) AS newest_age_s,
       count(*) FILTER (WHERE status = 'DISCREPANCY') AS discrepancies
  FROM smalllive_reconciliations
UNION ALL
SELECT 'execmirror_snapshots', count(*),
       round(extract(epoch FROM now() - max(at))::numeric, 0), NULL
  FROM execmirror_snapshots
UNION ALL
SELECT 'execmirror_fills', count(*), NULL, NULL FROM execmirror_fills;
