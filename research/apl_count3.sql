-- READ-ONLY. Why the labelled join is empty: valuation columns on settled markets.
WITH st AS (SELECT DISTINCT us_market_slug, min(settled_at) settled_at FROM paper_settlements GROUP BY 1)
SELECT count(*) n, count(*) FILTER (WHERE v.probability IS NOT NULL) with_p,
       count(*) FILTER (WHERE v.executable_price IS NOT NULL) with_px,
       count(*) FILTER (WHERE v.decided_at < st.settled_at) before_settle,
       count(DISTINCT v.us_market_slug) slugs
  FROM external_valuations v JOIN st USING (us_market_slug);
SELECT count(*) settled_slugs FROM (SELECT DISTINCT us_market_slug FROM paper_settlements) s;
SELECT d.strategy, count(*) n, count(*) FILTER (WHERE d.p_pinnacle IS NOT NULL) p_pin,
       count(*) FILTER (WHERE d.limit_price IS NOT NULL) px, min(d.decided_at), max(d.decided_at)
  FROM paper_decisions d GROUP BY 1 ORDER BY 2 DESC;
SELECT column_name FROM information_schema.columns WHERE table_name='paper_decisions' ORDER BY ordinal_position;
