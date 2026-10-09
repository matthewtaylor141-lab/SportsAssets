-- READ-ONLY. RC6.2 lane p-xavier (diagnosis, fourth pass). Run 37941361339
-- (rc6_pxavier_missed_stamps.sql, Z5) counted 2,287 PinnAPI source stamps
-- of held contracts in 72 h, 735 of them with no FRESH review inside 30 s.
-- Here each missed stamp is classed by what happened next: no review of
-- the contract at all inside 30 s (scheduling / latency), or a review that
-- read the contract stale (by its held-read refusal: read or identity).
-- Every statement is a SELECT.

\echo M1 missed PinnAPI stamps of held contracts (72 h) by what the next review inside 30 s did
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
  SELECT slug, t, 1, ev FROM rv),
o AS (
  SELECT u.*,
         min(CASE WHEN is_review = 1 AND ev = 'FRESH_CURRENT_PROBABILITY'
                  THEN t END)
           OVER (PARTITION BY slug ORDER BY t DESC, is_review DESC
                 ROWS UNBOUNDED PRECEDING) AS next_fresh,
         min(CASE WHEN is_review = 1 THEN t END)
           OVER (PARTITION BY slug ORDER BY t DESC, is_review DESC
                 ROWS UNBOUNDED PRECEDING) AS next_any
    FROM u),
miss AS (
  SELECT slug, t, next_any
    FROM o
   WHERE is_review = 0
     AND (next_fresh IS NULL OR next_fresh - t > interval '30 seconds')),
cls AS (
  SELECT m.slug, m.t,
         CASE WHEN m.next_any IS NULL
                   OR m.next_any - m.t > interval '30 seconds'
              THEN 'NO_REVIEW_INSIDE_30S'
              ELSE 'REVIEWED_STALE' END AS what,
         (SELECT r.feed_refusal FROM rv r
           WHERE r.slug = m.slug AND r.t = m.next_any LIMIT 1) AS next_refusal,
         (SELECT r.trigger FROM rv r
           WHERE r.slug = m.slug AND r.t = m.next_any LIMIT 1) AS next_trigger,
         round(extract(epoch FROM m.next_any - m.t)::numeric, 1) AS to_next_s,
         to_char(m.t, 'MI')::int % 30 BETWEEN 13 AND 16 AS in_half_hourly_burst
    FROM miss m)
SELECT what,
       CASE WHEN what = 'REVIEWED_STALE' THEN next_refusal ELSE '-' END
         AS refusal_of_the_next_review,
       count(*) AS stamps, count(DISTINCT slug) AS contracts,
       count(*) FILTER (WHERE in_half_hourly_burst) AS in_minutes_13_16_43_46,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY to_next_s)::numeric,
             1) AS p50_to_next_review_s
  FROM cls
 GROUP BY 1, 2 ORDER BY 3 DESC
 LIMIT 30;
