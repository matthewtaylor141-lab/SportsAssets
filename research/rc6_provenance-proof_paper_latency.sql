-- READ-ONLY. RC6 lane provenance-proof: the PAPER decision's own latency and
-- its deadline cuts in production -- the baseline the provenance offload
-- (verify_provenance on the API's CPU lane) is measured against. Every PAPER
-- decision first reads its research-model context (paper_derek._context ->
-- research_model -> verify_provenance), so a slower check shows up here as
-- decision latency and as hook TIMEOUTs at the 8 s decision deadline.
-- Counts and percentiles only: no row content is printed.

\echo L1 hook decisions NOT made (paper_hook_failures), by hour, strategy and outcome, last 24 h
SELECT date_trunc('hour', recorded_at) AS hour, strategy, outcome,
       count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP
             (ORDER BY elapsed_s::float8)::numeric, 3) AS elapsed_p50_s,
       round(percentile_cont(0.95) WITHIN GROUP
             (ORDER BY elapsed_s::float8)::numeric, 3) AS elapsed_p95_s,
       round(max(elapsed_s)::numeric, 3) AS elapsed_max_s
  FROM paper_hook_failures
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1 DESC, 2, 3;

\echo L2 decisions recorded: seconds from the decision instant to its row (recorded_at - decided_at), by strategy, before and since the RC6 deploy (2026-10-09 03:26Z)
SELECT strategy,
       (recorded_at >= timestamptz '2026-10-09 03:26:00+00') AS since_rc6,
       count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM
             recorded_at - decided_at))::numeric, 3) AS p50_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM
             recorded_at - decided_at))::numeric, 3) AS p95_s,
       round(max(extract(epoch FROM recorded_at - decided_at))::numeric, 3)
         AS max_s,
       count(*) FILTER (WHERE recorded_at - decided_at
                        > interval '8 seconds') AS over_8s
  FROM paper_decisions
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo L3 decisions per hour (all strategies), last 24 h: the load the context step serves
SELECT date_trunc('hour', recorded_at) AS hour, count(*) AS decisions,
       count(DISTINCT valuation_id) AS valuations
  FROM paper_decisions
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY 1
 ORDER BY 1 DESC;

\echo L4 how many decisions start within the same second (concurrent cold contexts), last 24 h
SELECT per_second AS decisions_in_one_second, count(*) AS seconds
  FROM (SELECT date_trunc('second', decided_at) AS s, count(*) AS per_second
          FROM paper_decisions
         WHERE recorded_at > now() - interval '24 hours'
         GROUP BY 1) t
 GROUP BY 1
 ORDER BY 1;
