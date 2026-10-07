-- READ-ONLY. Alpha Proof Lab dataset sizing: settled valuations with a
-- probability and an executable price, by sport family / market / purpose.
SELECT sport_family, market, record_purpose, decision, count(*) n,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) with_slug,
       min(decided_at), max(decided_at)
  FROM external_valuations
 WHERE outcome_known AND probability IS NOT NULL AND executable_price IS NOT NULL
 GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 40;
SELECT count(*) settled_total, count(DISTINCT us_market_slug) slugs,
       count(DISTINCT event_key) events FROM external_valuations
 WHERE outcome_known AND probability IS NOT NULL AND executable_price IS NOT NULL;
SELECT count(*) book_obs, min(observed_at), max(observed_at) FROM paper_book_observations;
