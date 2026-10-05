-- Same-book probe verdicts and held-market coverage by the institutional stream (read-only).
SELECT verdict, coalesce(verdict_reason, incomparable_reason) AS why, count(*) AS n,
       max(probed_at) AS last
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '72 hours'
 GROUP BY 1, 2 ORDER BY n DESC LIMIT 25;

SELECT date_trunc('day', probed_at) AS d, verdict, count(*)
  FROM institutional_same_book_probe
 GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC LIMIT 40;

WITH held AS (
  SELECT DISTINCT jsonb_object_keys(outcomes) AS us_market_slug
    FROM paper_mark_refresh_runs
   WHERE run_id = (SELECT max(run_id) FROM paper_mark_refresh_runs)
), fu AS (
  SELECT DISTINCT retail_slug FROM institutional_focus_universe
   WHERE computed_at > now() - interval '15 minutes'
)
SELECT (SELECT count(*) FROM held) AS held_markets,
       (SELECT count(*) FROM held h JOIN fu ON fu.retail_slug = h.us_market_slug) AS held_in_focus;

SELECT p.verdict, count(*) AS n
  FROM institutional_same_book_probe p
 WHERE p.probed_at > now() - interval '72 hours'
   AND p.retail_slug IN (SELECT DISTINCT jsonb_object_keys(outcomes)
                           FROM paper_mark_refresh_runs
                          WHERE run_id = (SELECT max(run_id) FROM paper_mark_refresh_runs))
 GROUP BY 1;
