-- READ-ONLY. ACQUISITION THROUGHPUT BASELINE (before the throughput release).
-- T1 paper book reads per hour by read basis: total, unreadable, deadline-cut
-- T2 decisions per hour by strategy: total, book-deadline refusals, entered
-- T3 in-cycle hook failures per hour by strategy and outcome
-- T4 valuations per hour vs completed-game decisions (missing decisions)
-- T5 book-read error texts in the last 6 hours
-- T6 the last 12 collection-cycle heartbeats we can see (current key only)
\echo '== T1 · book reads per hour by basis (last 12 h) =='
SELECT date_trunc('hour', observed_at) AS hour, read_basis,
       count(*) AS reads,
       count(*) FILTER (WHERE error IS NOT NULL) AS unreadable,
       count(*) FILTER (WHERE error ILIKE '%DEADLINE%') AS deadline_cut,
       count(*) FILTER (WHERE error ILIKE '%COOLDOWN%' OR error ILIKE '%429%')
         AS cooldown_or_429
  FROM paper_book_observations
 WHERE observed_at > now() - interval '12 hours'
 GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC;

\echo '== T2 · decisions per hour by strategy (last 12 h) =='
SELECT date_trunc('hour', decided_at) AS hour, strategy, count(*) AS decisions,
       count(*) FILTER (WHERE refusal =
         'BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE') AS book_deadline,
       count(*) FILTER (WHERE book_obs_id IS NOT NULL) AS with_book,
       count(*) FILTER (WHERE verdict = 'ENTER') AS entered
  FROM paper_decisions
 WHERE decided_at > now() - interval '12 hours'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2;

\echo '== T3 · in-cycle hook failures per hour (last 12 h) =='
SELECT date_trunc('hour', recorded_at) AS hour, strategy, outcome, count(*),
       round(avg(elapsed_s)::numeric, 2) AS avg_elapsed_s
  FROM paper_hook_failures
 WHERE recorded_at > now() - interval '12 hours'
 GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC;

\echo '== T4 · entry-experiment valuations vs completed-game decisions per hour =='
WITH v AS (SELECT id, date_trunc('hour', decided_at) AS hour
             FROM external_valuations
            WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
              AND us_market_slug IS NOT NULL
              AND decided_at > now() - interval '12 hours')
SELECT v.hour, count(*) AS valuations,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_decisions d
                         WHERE d.valuation_id = v.id
                           AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'))
         AS cg_decided,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM paper_decisions d
                         WHERE d.valuation_id = v.id
                           AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'))
         AS cg_missing
  FROM v GROUP BY 1 ORDER BY 1 DESC;

\echo '== T5 · book-read errors (last 6 h) =='
SELECT left(error, 90) AS error, read_basis, count(*)
  FROM paper_book_observations
 WHERE observed_at > now() - interval '6 hours' AND error IS NOT NULL
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo '== T6 · current collection-cycle heartbeat =='
SELECT key, left(value::text, 1500) AS value
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'paper_session_last_pass');
