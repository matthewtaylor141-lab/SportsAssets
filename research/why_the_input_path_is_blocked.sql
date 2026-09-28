-- READ-ONLY. WHY the scheduled loop admits nothing.
--
-- The census established WHERE evaluation stops: the execution estimate is
-- produced on 59 of 1,126 rows (5.2%), and that single gap sits upstream of
-- both economics buckets. It did NOT establish why the estimate is absent.
--
-- `bettor_external_shadow` records the execution plan's OWN refusal codes
-- alongside the gate's, precisely so that
-- EXECUTION_ESTIMATE_NOT_IDENTIFIED can be told apart from "the ladder was
-- unreadable" and "the ladder was priced beyond break-even". Those are
-- different problems with different fixes. This reads them.

-- 1 · Every refusal code by frequency, so nothing is inferred from absence.
SELECT code, count(*) AS rows_carrying_it,
       round(100.0 * count(*) / (SELECT count(*) FROM external_valuations), 1)
         AS pct_of_rows
  FROM external_valuations, unnest(coalesce(refusals, ARRAY[]::text[])) AS code
 GROUP BY code
 ORDER BY rows_carrying_it DESC;

-- 2 · Among rows MISSING the execution estimate, which plan-level code
--     co-occurs. This is the actual diagnosis: the gate's code is generic,
--     the plan's code names the cause.
WITH no_est AS (
    SELECT id, coalesce(refusals, ARRAY[]::text[]) AS r
      FROM external_valuations
     WHERE 'EXECUTION_ESTIMATE_NOT_IDENTIFIED'
           = ANY(coalesce(refusals, ARRAY[]::text[]))
)
SELECT code, count(*) AS rows_
  FROM no_est, unnest(r) AS code
 WHERE code <> 'EXECUTION_ESTIMATE_NOT_IDENTIFIED'
 GROUP BY code
 ORDER BY rows_ DESC
 LIMIT 25;

-- 3 · And the 59 rows where it DID run: what distinguishes them.
SELECT count(*)                                         AS rows_with_estimate,
       count(DISTINCT us_market_slug)                    AS distinct_markets,
       min(observed_at)                                  AS earliest,
       max(observed_at)                                  AS latest
  FROM external_valuations
 WHERE execution_estimate IS NOT NULL
   AND (execution_estimate::jsonb ->> 'p_fill') IS NOT NULL;

-- 4 · Is the absence structural or intermittent? Per day, so a fix can be
--     aimed at a cause rather than at an average.
SELECT date_trunc('day', observed_at)::date AS day,
       count(*)                             AS evaluations,
       count(*) FILTER (
         WHERE (execution_estimate::jsonb ->> 'p_fill') IS NOT NULL
       )                                    AS with_estimate
  FROM external_valuations
 GROUP BY 1
 ORDER BY 1;
