-- READ-ONLY. Latest review per open PAPER group crossed with the held
-- contract's catalogue row (type, league, start vs now) and the newest
-- external valuation's age for the held contract. Every statement is a SELECT.
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection, strategy
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '6 hours'
   ORDER BY group_id, reviewed_at DESC),
held AS (
  SELECT l.*, o.us_market_slug
    FROM latest l
    JOIN LATERAL (SELECT us_market_slug FROM paper_orders o
                   WHERE o.group_id = l.group_id AND o.role = 'ENTRY' LIMIT 1) o ON true)
SELECT selection->'management_packet'->'book'->>'mark_class' AS book,
       coalesce(measure->>'feed_refusal', measure->>'probability_source') AS prob,
       p.sports_type, p.team_league,
       CASE WHEN p.game_start IS NULL THEN 'no_start'
            WHEN p.game_start > now() + interval '1 day' THEN 'gt1d'
            WHEN p.game_start > now() THEN 'lt1d'
            WHEN p.game_start > now() - interval '4 hours' THEN 'inplay_lt4h'
            ELSE 'started_gt4h' END AS start_bucket,
       count(*)
  FROM held h LEFT JOIN us_premap p ON p.market_slug = h.us_market_slug
 GROUP BY 1,2,3,4,5 ORDER BY 6 DESC LIMIT 60;
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '6 hours'
   ORDER BY group_id, reviewed_at DESC),
held AS (
  SELECT l.*, o.us_market_slug
    FROM latest l
    JOIN LATERAL (SELECT us_market_slug FROM paper_orders o
                   WHERE o.group_id = l.group_id AND o.role = 'ENTRY' LIMIT 1) o ON true)
SELECT CASE WHEN v.age IS NULL THEN 'none_7d'
            WHEN v.age < 30 THEN 'a<30s' WHEN v.age < 300 THEN 'b<5m'
            WHEN v.age < 3600 THEN 'c<1h' WHEN v.age < 86400 THEN 'd<1d'
            ELSE 'e>1d' END AS newest_valuation_age,
       selection->'management_packet'->'book'->>'mark_class' AS book,
       count(*)
  FROM held h LEFT JOIN LATERAL (
       SELECT extract(epoch FROM now() - max(observed_at)) AS age
         FROM external_valuations e
        WHERE e.us_market_slug = h.us_market_slug
          AND e.decided_at > now() - interval '7 days') v ON true
 GROUP BY 1,2 ORDER BY 1,2;
SELECT version, count(*), max(decided_at), max(observed_at)
  FROM external_valuations WHERE decided_at > now() - interval '1 hour'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
SELECT strategy, count(DISTINCT group_id) FROM paper_xavier_reviews
 WHERE reviewed_at > now() - interval '6 hours' GROUP BY 1;
SELECT p.market_state, count(*) FROM (
  SELECT DISTINCT o.us_market_slug FROM paper_orders o
   JOIN paper_xavier_reviews r ON r.group_id = o.group_id
  WHERE r.reviewed_at > now() - interval '1 hour') s
  LEFT JOIN us_premap p ON p.market_slug = s.us_market_slug GROUP BY 1;
