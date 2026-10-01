-- Does any recorded Derek decision carry a NEGATIVE Pinnacle age (observed_at
-- after the decision instant), and was it qualified FRESH? Read-only.
\echo '== N1 · age_s distribution on recorded paper decisions (all time) =='
SELECT count(*) AS decisions,
       count(*) FILTER (WHERE (pinnacle->>'age_s') IS NOT NULL) AS with_age,
       count(*) FILTER (WHERE (pinnacle->>'age_s')::float < 0) AS negative,
       count(*) FILTER (WHERE (pinnacle->>'age_s')::float < -1) AS below_minus_1s,
       count(*) FILTER (WHERE (pinnacle->>'age_s')::float < -5) AS below_minus_5s,
       min((pinnacle->>'age_s')::float) AS min_age_s,
       percentile_disc(0.01) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float) AS p01,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float) AS p50,
       max((pinnacle->>'age_s')::float) AS max_age_s
  FROM paper_decisions;

\echo '== N2 · negative-age decisions by qualification, verdict and path =='
SELECT pinnacle->>'qualification' AS qualification, verdict,
       pinnacle->>'decided_via' AS decided_via,
       count(*) AS n, min((pinnacle->>'age_s')::float) AS min_age_s,
       max((pinnacle->>'age_s')::float) AS max_age_s,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE (pinnacle->>'age_s')::float < 0
 GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 20;

\echo '== N3 · fills whose decision had a negative age =='
SELECT d.decision_id, d.decided_at, d.verdict, (d.pinnacle->>'age_s')::float AS age_s,
       d.pinnacle->>'decided_via' AS decided_via
  FROM paper_decisions d
 WHERE (d.pinnacle->>'age_s')::float < 0 AND d.verdict NOT LIKE 'REFUSE%'
 ORDER BY d.decided_at DESC LIMIT 20;
