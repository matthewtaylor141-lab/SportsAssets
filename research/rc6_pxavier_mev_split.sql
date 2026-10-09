-- READ-ONLY. RC6.2 lane p-xavier (rework after independent review). The
-- fix stage cited "557 of 6,255 PAPER MARKET_EVENT assessments in 72 h were
-- made 30 s or more after their change" (run 37945613145 P4) as the
-- population of the in-pass held review (SW-2). A MARKET_EVENT is due for
-- one of two reasons (paper_xavier.step): a held-market change on the
-- PinnAPI feed (due_at = the feed's provider time for the slug) or a move of
-- the venue book's best exit (due_at = that paper_book_observations
-- instant). SW-2 only reaches the first. Here the same population, split by
-- what made each assessment due, the feed class cross-checked against the
-- persisted PinnAPI stamps, and the late ones by minute of the hour (the
-- 30-minute desk sweep runs at :14-:15 and :44-:45). Every statement is a
-- SELECT.

\echo S0 read instant
SELECT now() AS read_at;

\echo S1 PAPER MARKET_EVENT assessments (72 h) by origin of the due instant (BOOK_MOVE = a paper_book_observations instant of the slug within 2 ms; FEED_CHANGE = otherwise) and due-to-review latency
WITH o AS (
  SELECT DISTINCT ON (group_id) group_id, us_market_slug
    FROM paper_orders WHERE role = 'ENTRY'
   ORDER BY group_id, created_at),
a AS (
  SELECT a.group_id, a.assessed_at, a.due_at, a.review_latency_s,
         a.evidence_state, o.us_market_slug AS slug
    FROM xavier_management_assessments a
    LEFT JOIN o ON o.group_id = a.group_id
   WHERE a.position_kind = 'PAPER' AND a.trigger = 'MARKET_EVENT'
     AND a.assessed_at > now() - interval '72 hours'),
c AS (
  SELECT a.*,
         CASE WHEN a.due_at IS NULL THEN 'NO_DUE_AT'
              WHEN a.slug IS NULL THEN 'NO_SLUG'
              WHEN EXISTS (
                SELECT 1 FROM paper_book_observations b
                 WHERE b.us_market_slug = a.slug
                   AND b.observed_at BETWEEN a.due_at - interval '2 milliseconds'
                                         AND a.due_at + interval '2 milliseconds')
                THEN 'BOOK_MOVE'
              ELSE 'FEED_CHANGE' END AS origin
    FROM a)
SELECT origin,
       CASE WHEN review_latency_s IS NULL THEN 'unknown'
            WHEN review_latency_s < 30 THEN 'a <30s'
            WHEN review_latency_s <= 60 THEN 'b 30-60s'
            WHEN review_latency_s <= 120 THEN 'c 60-120s'
            ELSE 'd >120s' END AS latency,
       count(*) AS assessments,
       count(*) FILTER (WHERE evidence_state = 'FRESH_CURRENT_PROBABILITY')
         AS fresh,
       count(DISTINCT group_id) AS groups,
       count(DISTINCT slug) AS slugs
  FROM c GROUP BY 1, 2 ORDER BY 1, 2;

\echo S2 the same population at 30 s or more: by origin and whether the review fell in a desk-sweep minute (:14, :15, :44, :45)
WITH o AS (
  SELECT DISTINCT ON (group_id) group_id, us_market_slug
    FROM paper_orders WHERE role = 'ENTRY'
   ORDER BY group_id, created_at),
a AS (
  SELECT a.group_id, a.assessed_at, a.due_at, a.review_latency_s,
         o.us_market_slug AS slug
    FROM xavier_management_assessments a
    LEFT JOIN o ON o.group_id = a.group_id
   WHERE a.position_kind = 'PAPER' AND a.trigger = 'MARKET_EVENT'
     AND a.assessed_at > now() - interval '72 hours'
     AND a.review_latency_s >= 30),
c AS (
  SELECT a.*,
         CASE WHEN a.due_at IS NULL THEN 'NO_DUE_AT'
              WHEN a.slug IS NULL THEN 'NO_SLUG'
              WHEN EXISTS (
                SELECT 1 FROM paper_book_observations b
                 WHERE b.us_market_slug = a.slug
                   AND b.observed_at BETWEEN a.due_at - interval '2 milliseconds'
                                         AND a.due_at + interval '2 milliseconds')
                THEN 'BOOK_MOVE'
              ELSE 'FEED_CHANGE' END AS origin
    FROM a)
SELECT origin,
       extract(minute FROM due_at)::int IN (14, 15, 44, 45) AS due_in_desk_minute,
       count(*) AS assessments,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY review_latency_s)
             ::numeric, 3) AS latency_p50_s,
       round(max(review_latency_s)::numeric, 3) AS latency_max_s,
       count(DISTINCT group_id) AS groups
  FROM c GROUP BY 1, 2 ORDER BY 1, 2;

\echo S3 the FEED_CHANGE class cross-checked against the persisted PinnAPI stamps (external_valuations, provider pinnapi.com/raw-websocket, same slug, due_at within 1 ms); a confirmation of an unchanged price is not persisted as a valuation, so an unmatched member is not proof of a book move
WITH o AS (
  SELECT DISTINCT ON (group_id) group_id, us_market_slug
    FROM paper_orders WHERE role = 'ENTRY'
   ORDER BY group_id, created_at),
a AS (
  SELECT a.group_id, a.due_at, a.review_latency_s,
         o.us_market_slug AS slug,
         round(extract(epoch FROM a.due_at) * 1000)::bigint AS k
    FROM xavier_management_assessments a
    JOIN o ON o.group_id = a.group_id
   WHERE a.position_kind = 'PAPER' AND a.trigger = 'MARKET_EVENT'
     AND a.assessed_at > now() - interval '72 hours'
     AND a.due_at IS NOT NULL
     AND NOT EXISTS (
       SELECT 1 FROM paper_book_observations b
        WHERE b.us_market_slug = o.us_market_slug
          AND b.observed_at BETWEEN a.due_at - interval '2 milliseconds'
                                AND a.due_at + interval '2 milliseconds')),
st AS (
  SELECT DISTINCT us_market_slug AS slug,
         round(extract(epoch FROM observed_at) * 1000)::bigint AS k
    FROM external_valuations
   WHERE provider = 'pinnapi.com/raw-websocket'
     AND decided_at > now() - interval '74 hours'
     AND us_market_slug IN (SELECT DISTINCT slug FROM a)),
m AS (
  SELECT a.*, (s0.k IS NOT NULL OR s1.k IS NOT NULL OR s2.k IS NOT NULL)
           AS stamp_matched
    FROM a
    LEFT JOIN st s0 ON s0.slug = a.slug AND s0.k = a.k
    LEFT JOIN st s1 ON s1.slug = a.slug AND s1.k = a.k - 1
    LEFT JOIN st s2 ON s2.slug = a.slug AND s2.k = a.k + 1)
SELECT review_latency_s >= 30 AS late_30s, stamp_matched,
       count(*) AS assessments, count(DISTINCT group_id) AS groups
  FROM m GROUP BY 1, 2 ORDER BY 1, 2;
