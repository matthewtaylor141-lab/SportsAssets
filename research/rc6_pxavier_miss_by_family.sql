-- READ-ONLY. RC6.2 lane p-xavier (diagnosis, fifth pass). Run 37942001674
-- (rc6_pxavier_miss_breakdown.sql) found 622 of 735 missed PinnAPI stamps of
-- held contracts (72 h) with NO review of the contract inside 30 s. The
-- held watch (pinnapi_held.HeldWatch.changed) notifies only on the full-game
-- money-line key; a held LINE contract (spread / total / team total) is
-- priced by the held read on its own line market. Here every held-contract
-- stamp is split by the venue market type (us_premap.sports_type) and by
-- prematch / in play (the stamp against the venue game start), covered or
-- missed with no review inside 30 s. A settled contract leaves us_premap,
-- so the family is read from the venue slug's own first token (asc spread,
-- tsc total, atc / aec winners) and its league from the second. Every
-- statement is a SELECT.

\echo F1 held-contract PinnAPI stamps (72 h) by slug family, league and phase: fresh review inside 30 s, or no review inside 30 s
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
pm AS (
  SELECT DISTINCT ON (market_slug) market_slug, sports_type, game_start
    FROM us_premap WHERE market_slug IN (SELECT slug FROM held)
   ORDER BY market_slug)
SELECT split_part(o.slug, '-', 1) AS slug_family,
       split_part(o.slug, '-', 2) AS league,
       CASE WHEN pm.game_start IS NULL THEN '(left catalogue)'
            WHEN o.t < pm.game_start THEN 'PREMATCH' ELSE 'IN_PLAY' END
         AS phase,
       count(*) AS stamps, count(DISTINCT o.slug) AS contracts,
       count(*) FILTER (WHERE o.next_fresh IS NOT NULL
                        AND o.next_fresh - o.t <= interval '30 seconds')
         AS fresh_review_inside_30s,
       count(*) FILTER (WHERE o.next_any IS NULL
                        OR o.next_any - o.t > interval '30 seconds')
         AS no_review_inside_30s
  FROM o LEFT JOIN pm ON pm.market_slug = o.slug
 WHERE o.is_review = 0
 GROUP BY 1, 2, 3 ORDER BY 4 DESC
 LIMIT 60;
