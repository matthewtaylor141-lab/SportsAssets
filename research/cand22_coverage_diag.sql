-- Coverage stage diagnosis (read-only): outcome/stage vocabulary and valuation purposes behind decisions.
\echo '== D1 · ext_candidate_outcomes outcome x stage (last 24 h) =='
SELECT outcome, coalesce(stage, '(null)') AS stage, count(*) AS n, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes WHERE recorded_at > now() - interval '24 hours' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25;
\echo '== D2 · record_purpose of valuations behind paper decisions (last 24 h) =='
SELECT ev.record_purpose, pd.strategy, pd.verdict, count(*) AS n
  FROM paper_decisions pd JOIN external_valuations ev ON ev.id = pd.valuation_id
 WHERE pd.decided_at > now() - interval '24 hours' GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 15;
\echo '== D3 · valuations by record_purpose (last 24 h) =='
SELECT record_purpose, count(*) AS n FROM external_valuations WHERE decided_at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;
