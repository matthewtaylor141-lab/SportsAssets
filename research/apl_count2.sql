-- READ-ONLY. Valuations on markets with a settled paper outcome (the
-- immutable ledger's venue-reported payout per side), by family / market /
-- purpose; plus how many have a venue book at or before the decision.
WITH st AS (
  SELECT DISTINCT ON (us_market_slug) us_market_slug, holding_side, payout_per_contract, outcome, settled_at
    FROM paper_settlements ORDER BY us_market_slug, settled_at DESC)
SELECT v.sport_family, v.market, v.record_purpose, v.buy_intent, count(*) n,
       count(DISTINCT v.us_market_slug) slugs, min(v.decided_at), max(v.decided_at)
  FROM external_valuations v JOIN st USING (us_market_slug)
 WHERE v.probability IS NOT NULL AND v.executable_price IS NOT NULL AND v.decided_at < st.settled_at
 GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 40;
SELECT outcome, holding_side, payout_per_contract, count(*) FROM paper_settlements GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 12;
