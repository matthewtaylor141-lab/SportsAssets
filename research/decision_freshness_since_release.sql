-- READ-ONLY. DECISION FRESHNESS SINCE THE 91fd4fc RELEASE (15:08:15Z):
-- every completed-game decision with how it was decided, the Pinnacle age at
-- the decision, the delay after the valuation, the book age and the refusal;
-- the in-cycle hook failures (migration 187); and the collection cycles.
\echo '== F1 · completed-game decisions since 15:08:15Z =='
SELECT d.decided_at, d.valuation_id, left(d.us_market_slug, 34) AS market,
       d.pinnacle->>'decided_via' AS decided_via,
       (d.pinnacle->>'age_s')::float8 AS pinnacle_age_s,
       (d.pinnacle->>'decision_lag_after_valuation_s')::float8 AS lag_s,
       (d.economics->>'book_age_s')::float8 AS book_age_s,
       (d.economics->>'best_level_edge_pp')::float8 AS edge_pp,
       d.verdict, d.refusal
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND d.decided_at > '2026-10-01 15:08:15+00'
 ORDER BY d.decided_at;
\echo '== F2 · summary since release =='
SELECT d.pinnacle->>'decided_via' AS decided_via, coalesce(d.refusal,'ENTER') AS refusal,
       count(*) AS n,
       round(avg((d.pinnacle->>'age_s')::float8)::numeric, 2) AS avg_age_s,
       round(max((d.pinnacle->>'age_s')::float8)::numeric, 2) AS max_age_s,
       round(max((d.pinnacle->>'decision_lag_after_valuation_s')::float8)::numeric, 2) AS max_lag_s
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND d.decided_at > '2026-10-01 15:08:15+00'
 GROUP BY 1, 2 ORDER BY 3 DESC;
\echo '== F3 · in-cycle hook failures =='
SELECT recorded_at, valuation_id, strategy, stage, outcome, elapsed_s,
       left(error, 160) AS error
  FROM paper_hook_failures ORDER BY recorded_at DESC LIMIT 20;
\echo '== F4 · valuation bursts since 14:30Z (cycle cadence) =='
WITH v AS (
  SELECT decided_at,
         CASE WHEN decided_at - lag(decided_at) OVER (ORDER BY decided_at)
                   > interval '120 seconds' THEN 1 ELSE 0 END AS new_cycle
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at > '2026-10-01 14:30:00+00'),
c AS (SELECT decided_at, sum(new_cycle) OVER (ORDER BY decided_at) AS cyc FROM v)
SELECT cyc, min(decided_at) AS first_row, count(*) AS rows,
       extract(epoch FROM min(decided_at) - lag(min(decided_at)) OVER (ORDER BY cyc))::int AS since_previous_s
  FROM c GROUP BY cyc ORDER BY cyc;
\echo '== F5 · account =='
SELECT count(*) FILTER (WHERE kind='INITIAL_FUNDING') AS funding_entries,
       (array_agg(cash_after_usd ORDER BY seq DESC))[1] AS cash_usd,
       (array_agg(reserved_after_usd ORDER BY seq DESC))[1] AS reserved_usd,
       max(seq) AS last_seq, max(committed_at) AS last_ledger_at
  FROM paper_ledger WHERE account_id = 'paper_acct_main';
SELECT (SELECT count(*) FROM paper_orders WHERE account_id='paper_acct_main') AS orders,
       (SELECT count(*) FROM paper_fills WHERE account_id='paper_acct_main') AS fills,
       (SELECT count(*) FROM paper_handoffs WHERE account_id='paper_acct_main') AS handoffs;
