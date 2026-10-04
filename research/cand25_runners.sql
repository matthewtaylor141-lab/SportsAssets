-- Read-only: Profitability OS runners' forward rows after the fd6cc5b deploy.
SELECT 'pos' AS layer, component, status, count(*) AS n, max(finished_at) AS last_at, left(max(coalesce(error,'')),160) AS err FROM pos_runs GROUP BY 1,2,3
UNION ALL SELECT 'twin', component, status, count(*), max(finished_at), left(max(coalesce(error,'')),160) FROM twin_runs GROUP BY 1,2,3
UNION ALL SELECT 'poslearn', component, status, count(*), max(finished_at), left(max(coalesce(error,'')),160) FROM poslearn_runs GROUP BY 1,2,3
ORDER BY 1, 2;
SELECT DISTINCT ON (metric, book) metric, book, status, value, sample_n, why FROM pos_metric_observations ORDER BY metric, book, computed_at DESC;
SELECT level, confidence_status, confidence, computed_at FROM twin_evidence_ladder ORDER BY computed_at DESC LIMIT 1;
SELECT book, status, why, issued_at FROM pos_forecasts ORDER BY issued_at DESC LIMIT 4;
