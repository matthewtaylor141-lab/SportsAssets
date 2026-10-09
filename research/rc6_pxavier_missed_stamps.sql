-- READ-ONLY. RC6.2 lane p-xavier (diagnosis, third pass). Run 37939738904
-- (rc6_pxavier_stamp_coverage.sql) found 47 of 52 PinnAPI source stamps of
-- the held PKE contract (before kickoff) reviewed FRESH inside their 30 s,
-- and 5 with NO review inside 30 s (06:44:48, 06:44:52, 07:14:54, 07:44:48,
-- 07:45:05Z on 2026-10-09). Here: what Xavier and the paper pass did in
-- those windows; the population-wide version of the coverage question
-- rewritten as one ordered pass (the per-review EXISTS form timed out);
-- the RC4 entry refusal that prevents another unprotectable entry; and
-- which held groups could never be protected. Every statement is a SELECT.

\echo Z1 PKE reviews and assessments around each missed stamp (60 s before to 90 s after)
WITH w(stamp) AS (VALUES ('2026-10-09 06:44:48.383+00'::timestamptz),
                         ('2026-10-09 06:44:52.554+00'::timestamptz),
                         ('2026-10-09 07:14:54.048+00'::timestamptz),
                         ('2026-10-09 07:44:48.862+00'::timestamptz),
                         ('2026-10-09 07:45:05.265+00'::timestamptz))
SELECT w.stamp, a.assessed_at,
       round(extract(epoch FROM a.assessed_at - w.stamp)::numeric, 3)
         AS after_stamp_s,
       a.trigger, a.due_at, a.review_latency_s, a.evidence_state,
       a.probability_source, round(a.probability_age_s::numeric, 3) AS p_age_s
  FROM w
  JOIN xavier_management_assessments a
    ON a.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
   AND a.assessed_at BETWEEN w.stamp - interval '60 seconds'
                         AND w.stamp + interval '90 seconds'
 ORDER BY w.stamp, a.assessed_at;

\echo Z2 the paper pass around the missed stamps: decisions and candidate cycles per 15 s
WITH w(t0, t1) AS (VALUES
       ('2026-10-09 06:43:30+00'::timestamptz, '2026-10-09 06:46:30+00'::timestamptz),
       ('2026-10-09 07:14:00+00'::timestamptz, '2026-10-09 07:16:30+00'::timestamptz),
       ('2026-10-09 07:44:00+00'::timestamptz, '2026-10-09 07:47:00+00'::timestamptz)),
d AS (
  SELECT to_timestamp(floor(extract(epoch FROM decided_at) / 15) * 15) AS b,
         count(*) AS decisions
    FROM paper_decisions, w
   WHERE decided_at BETWEEN w.t0 AND w.t1
   GROUP BY 1),
c AS (
  SELECT to_timestamp(floor(extract(epoch FROM cycle_at) / 15) * 15) AS b,
         count(*) AS candidate_rows, count(DISTINCT cycle_id) AS cycles
    FROM ext_candidate_outcomes, w
   WHERE cycle_at BETWEEN w.t0 AND w.t1
   GROUP BY 1),
r AS (
  SELECT to_timestamp(floor(extract(epoch FROM reviewed_at) / 15) * 15) AS b,
         count(*) AS reviews
    FROM paper_xavier_reviews, w
   WHERE reviewed_at BETWEEN w.t0 AND w.t1
   GROUP BY 1)
SELECT coalesce(d.b, c.b, r.b) AS bucket, d.decisions, c.candidate_rows,
       c.cycles, r.reviews
  FROM d FULL JOIN c ON c.b = d.b FULL JOIN r ON r.b = coalesce(d.b, c.b)
 ORDER BY 1;

\echo Z3 every :14 and :44 minute 2026-10-09 04:00Z-08:30Z: Xavier reviews and paper decisions inside the minute vs the minute before
WITH r AS (
  SELECT to_char(reviewed_at, 'MI') AS mi, count(*) AS reviews
    FROM paper_xavier_reviews
   WHERE reviewed_at >= '2026-10-09 04:00+00'
     AND reviewed_at < '2026-10-09 08:30+00'
   GROUP BY 1),
d AS (
  SELECT to_char(decided_at, 'MI') AS mi, count(*) AS decisions
    FROM paper_decisions
   WHERE decided_at >= '2026-10-09 04:00+00'
     AND decided_at < '2026-10-09 08:30+00'
   GROUP BY 1)
SELECT coalesce(r.mi, d.mi) AS minute_of_hour, r.reviews, d.decisions
  FROM r FULL JOIN d ON d.mi = r.mi
 WHERE coalesce(r.mi, d.mi) IN ('12', '13', '14', '15', '16', '42', '43',
                                '44', '45', '46')
 ORDER BY 1;

\echo Z4 ALL GROUPS (72 h): each review against the newest PinnAPI stamp of its contract at the review instant (one ordered pass)
WITH rv AS (
  SELECT x.group_id, x.reviewed_at AS t, x.trigger,
         x.measure->>'evidence_state' AS ev,
         coalesce(x.measure->>'feed_refusal', '-') AS feed_refusal,
         o.us_market_slug AS slug
    FROM paper_xavier_reviews x
    JOIN (SELECT DISTINCT ON (group_id) group_id, us_market_slug
            FROM paper_orders WHERE role = 'ENTRY'
           ORDER BY group_id, created_at) o ON o.group_id = x.group_id
   WHERE x.reviewed_at > now() - interval '72 hours'),
st AS (
  SELECT DISTINCT us_market_slug AS slug, observed_at AS t
    FROM external_valuations
   WHERE provider = 'pinnapi.com/raw-websocket'
     AND decided_at > now() - interval '74 hours'
     AND us_market_slug IN (SELECT DISTINCT slug FROM rv)),
u AS (
  SELECT slug, t, 1 AS is_review, group_id, trigger, ev, feed_refusal,
         NULL::timestamptz AS stamp
    FROM rv
  UNION ALL
  SELECT slug, t, 0, NULL, NULL, NULL, NULL, t FROM st),
o AS (
  SELECT u.*, max(stamp) OVER (PARTITION BY slug ORDER BY t, is_review
                               ROWS UNBOUNDED PRECEDING) AS last_stamp
    FROM u)
SELECT ev, feed_refusal,
       count(*) AS reviews, count(DISTINCT group_id) AS groups,
       count(*) FILTER (WHERE last_stamp IS NOT NULL
                        AND t - last_stamp <= interval '30 seconds')
         AS stamp_within_30s,
       count(DISTINCT group_id) FILTER (WHERE last_stamp IS NOT NULL
                        AND t - last_stamp <= interval '30 seconds')
         AS groups_stamp_within_30s,
       count(*) FILTER (WHERE last_stamp IS NULL) AS no_stamp_in_window
  FROM o WHERE is_review = 1
 GROUP BY 1, 2 ORDER BY 3 DESC
 LIMIT 40;

\echo Z5 ALL GROUPS (72 h): PinnAPI stamps of held contracts and whether a FRESH review followed inside 30 s
WITH rv AS (
  SELECT x.group_id, x.reviewed_at AS t,
         x.measure->>'evidence_state' AS ev, o.us_market_slug AS slug
    FROM paper_xavier_reviews x
    JOIN (SELECT DISTINCT ON (group_id) group_id, us_market_slug
            FROM paper_orders WHERE role = 'ENTRY'
           ORDER BY group_id, created_at) o ON o.group_id = x.group_id
   WHERE x.reviewed_at > now() - interval '72 hours'),
held AS (
  SELECT slug, min(t) AS first_review, max(t) AS last_review
    FROM rv GROUP BY 1),
st AS (
  SELECT DISTINCT v.us_market_slug AS slug, v.observed_at AS t
    FROM external_valuations v JOIN held h ON h.slug = v.us_market_slug
   WHERE v.provider = 'pinnapi.com/raw-websocket'
     AND v.decided_at > now() - interval '74 hours'
     AND v.observed_at BETWEEN h.first_review AND h.last_review),
u AS (
  SELECT slug, t, 0 AS is_review, NULL::text AS ev FROM st
  UNION ALL
  SELECT slug, t, 1, ev FROM rv WHERE ev = 'FRESH_CURRENT_PROBABILITY'),
o AS (
  SELECT u.*, min(CASE WHEN is_review = 1 THEN t END)
                OVER (PARTITION BY slug ORDER BY t DESC, is_review DESC
                      ROWS UNBOUNDED PRECEDING) AS next_fresh_review
    FROM u)
SELECT count(*) AS stamps, count(DISTINCT slug) AS contracts,
       count(*) FILTER (WHERE next_fresh_review IS NOT NULL
                        AND next_fresh_review - t <= interval '30 seconds')
         AS fresh_review_within_30s,
       count(*) FILTER (WHERE next_fresh_review IS NULL
                        OR next_fresh_review - t > interval '30 seconds')
         AS missed,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM
             next_fresh_review - t))::numeric, 2) AS p50_stamp_to_fresh_review_s
  FROM o WHERE is_review = 0;

\echo Z6 the RC4 entry refusals that keep an unprotectable or unpriceable entry out (since 2026-10-08)
SELECT refusal, count(*) AS decisions, min(decided_at) AS first,
       max(decided_at) AS last
  FROM paper_decisions
 WHERE decided_at > '2026-10-08 00:00+00'
   AND refusal IN ('XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE',
                   'XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT')
 GROUP BY 1 ORDER BY 2 DESC;

\echo Z7 entry fills at 0.98 or above (where no protective price can exist), by day
SELECT date_trunc('day', filled_at) AS day, count(*) AS fills,
       count(DISTINCT group_id) AS groups, min(price) AS min_px,
       max(price) AS max_px
  FROM paper_fills
 WHERE role = 'ENTRY' AND direction = 'BUY' AND price >= 0.98
 GROUP BY 1 ORDER BY 1 DESC LIMIT 20;

\echo Z8 groups whose reviews recorded no protective price below one dollar (all time)
SELECT group_id, count(*) AS reviews, min(reviewed_at) AS first,
       max(reviewed_at) AS last
  FROM paper_xavier_reviews
 WHERE standing->'protective_price'->>'refusal'
       = 'NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
