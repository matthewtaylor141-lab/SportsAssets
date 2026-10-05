-- Institutional stream liveness evidence (read-only).
SELECT service, stream_state, left(stream_state_why, 120) AS why, connected,
       count(*) AS n, max(minute) AS last_minute, count(DISTINCT symbol) AS symbols
  FROM institutional_stream_evidence
 WHERE minute > now() - interval '48 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY last_minute DESC LIMIT 20;

SELECT service, max(computed_at) AS last, count(DISTINCT retail_slug) AS slugs
  FROM institutional_focus_universe
 WHERE computed_at > now() - interval '48 hours' GROUP BY 1;
