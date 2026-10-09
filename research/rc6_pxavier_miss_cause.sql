-- READ-ONLY. RC6.2 lane p-xavier (diagnosis, sixth pass). Runs 37942001674
-- and 37942279322: 622 PinnAPI stamps of held contracts (72 h) had NO review
-- of the contract inside 30 s, nearly all on soccer winner contracts (atc-),
-- whose full-game money line the held watch does listen on. Here each such
-- stamp is classed by the NEXT review of the contract at any delay: its
-- trigger, its evidence and its held-read refusal (an identity refusal --
-- NO_FEED_EVENT, STRUCTURED_PARTICIPANTS_NOT_TWO -- means the held watch
-- could not target the contract either: it resolves the same identity), and
-- the delay. Every statement is a SELECT.

\echo C1 stamps with no review inside 30 s (72 h), by the next review trigger, evidence and held-read refusal
WITH rv AS (
  SELECT x.group_id, x.reviewed_at AS t, x.trigger,
         x.measure->>'evidence_state' AS ev,
         coalesce(x.measure->>'feed_refusal', '-') AS feed_refusal,
         coalesce(x.measure->'feed'->>'identity_basis',
                  x.measure->'feed_detail'->>'identity_basis', '-')
           AS identity,
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
  SELECT slug, t, 0 AS is_review FROM st
  UNION ALL
  SELECT slug, t, 1 FROM rv),
o AS (
  SELECT u.*,
         min(CASE WHEN is_review = 1 THEN t END)
           OVER (PARTITION BY slug ORDER BY t DESC, is_review DESC
                 ROWS UNBOUNDED PRECEDING) AS next_any
    FROM u),
miss AS (
  SELECT slug, t, next_any FROM o
   WHERE is_review = 0
     AND (next_any IS NULL OR next_any - t > interval '30 seconds')),
nx AS (
  SELECT m.slug, m.t, m.next_any,
         (SELECT r.trigger || '|' || r.ev || '|' || r.feed_refusal || '|'
                 || r.identity
            FROM rv r WHERE r.slug = m.slug AND r.t = m.next_any LIMIT 1)
           AS nxt
    FROM miss m)
SELECT split_part(nxt, '|', 1) AS next_trigger,
       split_part(nxt, '|', 2) AS next_evidence,
       split_part(nxt, '|', 3) AS next_refusal,
       split_part(nxt, '|', 4) AS next_identity,
       count(*) AS stamps, count(DISTINCT slug) AS contracts,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM
             next_any - t))::numeric, 1) AS p50_delay_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM
             next_any - t))::numeric, 1) AS p90_delay_s
  FROM nx
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC
 LIMIT 40;
