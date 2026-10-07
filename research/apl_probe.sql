-- READ-ONLY. Alpha Proof Lab extract probe: candidate decisions on settled
-- markets, the economics keys recorded, and label coverage.
WITH st AS (SELECT DISTINCT us_market_slug FROM paper_settlements)
SELECT d.strategy, d.verdict, count(*) n, count(*) FILTER (WHERE d.book_obs_id IS NOT NULL) with_book,
       count(DISTINCT d.us_market_slug) slugs
  FROM paper_decisions d JOIN st USING (us_market_slug)
 WHERE d.p_pinnacle IS NOT NULL GROUP BY 1,2 ORDER BY 3 DESC;
SELECT k, count(*) FROM (SELECT jsonb_object_keys(economics) k FROM paper_decisions
  WHERE economics IS NOT NULL AND jsonb_typeof(economics)='object' AND decided_at > now()-interval '1 day' LIMIT 20000) x
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
SELECT left(economics::text, 1200) FROM paper_decisions WHERE verdict='ENTER' AND decided_at > now()-interval '3 days' LIMIT 1;
SELECT count(DISTINCT us_market_slug) slugs_with_outcome_basis FROM external_valuations WHERE outcome_basis IS NOT NULL;
