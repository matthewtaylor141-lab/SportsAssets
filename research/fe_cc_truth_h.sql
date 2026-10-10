-- READ ONLY. Frontend audit part H: why recorded ENTER decisions get no paper
-- order, from the findings the system recorded. SELECT statements only.
\echo == H1. order refusals since 2026-10-08 by the refusal the finding carries ==
SELECT coalesce(detail->>'refusal', detail->>'why', detail->>'reason', 'unnamed') AS refusal,
       count(*) AS n, min(found_at) AS first_found, max(found_at) AS last_found
  FROM paper_audrey_findings
 WHERE kind = 'PAPER_RISK_REFUSED_THE_ORDER' AND found_at > timestamptz '2026-10-08 00:00:00+00'
 GROUP BY 1 ORDER BY n DESC LIMIT 10;

\echo == H2. newest two of those findings ==
SELECT found_at, left(detail::text, 420) AS detail
  FROM paper_audrey_findings WHERE kind = 'PAPER_RISK_REFUSED_THE_ORDER'
 ORDER BY found_at DESC LIMIT 2;
