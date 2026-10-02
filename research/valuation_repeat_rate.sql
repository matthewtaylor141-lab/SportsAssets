-- How often the paper lanes see a NEW valuation of the SAME contract (read-only).
-- Each new valuation is a new decision key, so without a same-contract rule a
-- strategy would re-enter a contract it already holds on every one of these.
\echo '== R1 · entry-experiment decisions per contract, last 6 h (all strategies) =='
SELECT strategy, count(*) AS decisions, count(DISTINCT us_market_slug) AS contracts,
       round(count(*)::numeric / nullif(count(DISTINCT us_market_slug), 0), 1) AS decisions_per_contract,
       max(n) AS max_decisions_one_contract
  FROM (SELECT strategy, us_market_slug,
               count(*) OVER (PARTITION BY strategy, us_market_slug) AS n
          FROM paper_decisions
         WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '6 hours'
           AND us_market_slug IS NOT NULL) d
 GROUP BY strategy ORDER BY decisions DESC;

\echo '== R2 · distinct valuations per contract per hour, last 6 h (paper decisions keyed by valuation) =='
SELECT date_trunc('hour', decided_at) AS hour, count(DISTINCT valuation_id) AS valuations,
       count(DISTINCT us_market_slug) AS contracts
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '6 hours'
   AND valuation_id IS NOT NULL
 GROUP BY 1 ORDER BY 1;
