-- READ-ONLY. RC6.2 lane p-xavier (diagnosis, second pass). The first pass
-- (rc6_pxavier_held_packet.sql, research-sql run 37939204370) showed the
-- PKE position (paperexpgrp:fa090b4b3ee1f3d3e1979d07) settled LOST and the
-- open population is now 0. Here: (1) the population AT the pm-acceptance
-- read instant 2026-10-09 05:28:53Z, every account; (2) the exact review
-- the packet judged; (3) STAMP COVERAGE -- for every PinnAPI source stamp
-- of the held contract before kickoff, did a review stand on it while it
-- was inside the 30 s limit (a software miss is a stamp with no fresh
-- review); (4) the same question over every group (72 h): a STALE review
-- whose contract had a PinnAPI stamp inside the 30 s limit at the review
-- instant. Every statement is a SELECT.

\echo Y1 POPULATION AT 2026-10-09 05:28:53Z: canonical open PAPER positions, every account
SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
       round((f.bought - f.sold - coalesce(s.qty, 0))::numeric, 6) AS open_qty
  FROM (SELECT account_id, group_id, us_market_slug, holding_side,
               coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
               coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
          FROM paper_fills
         WHERE filled_at <= '2026-10-09 05:28:53.837+00'
         GROUP BY 1, 2, 3, 4) f
  LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
               FROM paper_settlements
              WHERE settled_at <= '2026-10-09 05:28:53.837+00'
              ORDER BY position_key, version DESC) s
    ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                        || ':' || f.us_market_slug || ':' || f.holding_side
 WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
 ORDER BY 1, 2;

\echo Y2 the PKE group: every review 2026-10-09 05:15Z to 05:30Z, the packet the 05:28:53Z read judged
SELECT reviewed_at, trigger, recommendation,
       measure->>'evidence_state' AS ev, measure->>'source' AS src,
       round((measure->>'probability_age_s')::numeric, 3) AS p_age_s,
       measure->>'probability_source_at' AS p_source_at,
       measure->>'probability_received_at' AS p_received_at,
       measure->>'valuation_id' AS valuation_id,
       coalesce(measure->>'feed_refusal', '-') AS feed_refusal,
       left(coalesce((measure->'feed_detail')::text, ''), 300) AS feed_detail,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'valuation'->>'expires_at' AS valuation_expires_at,
       selection->'management_packet'->'book'->>'mark_class' AS book,
       selection->'management_packet'->'book'->>'observed_at' AS book_observed_at,
       selection->'management_packet'->'protection'->>'state' AS protection,
       left(coalesce((standing->'protective_price')::text, ''), 160)
         AS protective_price
  FROM paper_xavier_reviews
 WHERE group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
   AND reviewed_at BETWEEN '2026-10-09 05:15:00+00' AND '2026-10-09 05:30:00+00'
 ORDER BY reviewed_at;

\echo Y3 the assessments of the same window (due instant, latency, the probability clocks)
SELECT assessed_at, trigger, due_at, review_latency_s, evidence_state,
       probability_source, round(probability_age_s::numeric, 3) AS p_age_s,
       recommendation
  FROM xavier_management_assessments
 WHERE group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
   AND assessed_at BETWEEN '2026-10-09 05:15:00+00' AND '2026-10-09 05:30:00+00'
 ORDER BY assessed_at;

\echo Y4 STAMP COVERAGE, PKE before kickoff (08:30Z): each PinnAPI source stamp of the held side and the first review inside its 30 s
WITH st AS (
  SELECT DISTINCT observed_at
    FROM external_valuations
   WHERE us_market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
     AND provider = 'pinnapi.com/raw-websocket'
     AND buy_intent = 'ORDER_INTENT_BUY_SHORT'
     AND observed_at >= '2026-10-05 10:08:00+00'
     AND observed_at < '2026-10-09 08:30:00+00'),
cov AS (
  SELECT st.observed_at,
         (SELECT min(v.received_at) FROM external_valuations v
           WHERE v.us_market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
             AND v.provider = 'pinnapi.com/raw-websocket'
             AND v.observed_at = st.observed_at) AS received_at,
         (SELECT count(*) FROM paper_xavier_reviews r
           WHERE r.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
             AND r.reviewed_at >= st.observed_at
             AND r.reviewed_at <= st.observed_at + interval '30 seconds')
           AS reviews_in_window,
         (SELECT count(*) FROM paper_xavier_reviews r
           WHERE r.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
             AND r.reviewed_at >= st.observed_at
             AND r.reviewed_at <= st.observed_at + interval '30 seconds'
             AND r.measure->>'evidence_state' = 'FRESH_CURRENT_PROBABILITY')
           AS fresh_reviews_in_window,
         (SELECT string_agg(DISTINCT coalesce(r.measure->>'feed_refusal',
                                              r.measure->>'source'), ',')
            FROM paper_xavier_reviews r
           WHERE r.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
             AND r.reviewed_at >= st.observed_at
             AND r.reviewed_at <= st.observed_at + interval '30 seconds')
           AS reasons_in_window
    FROM st)
SELECT observed_at, received_at,
       round(extract(epoch FROM received_at - observed_at)::numeric, 3)
         AS stamp_to_receipt_s,
       reviews_in_window, fresh_reviews_in_window, reasons_in_window
  FROM cov ORDER BY observed_at;

\echo Y5 STAMP COVERAGE, PKE before kickoff: totals
WITH st AS (
  SELECT DISTINCT observed_at
    FROM external_valuations
   WHERE us_market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
     AND provider = 'pinnapi.com/raw-websocket'
     AND buy_intent = 'ORDER_INTENT_BUY_SHORT'
     AND observed_at >= '2026-10-05 10:08:00+00'
     AND observed_at < '2026-10-09 08:30:00+00')
SELECT count(*) AS stamps,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM paper_xavier_reviews r
          WHERE r.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
            AND r.reviewed_at BETWEEN st.observed_at
                AND st.observed_at + interval '30 seconds'
            AND r.measure->>'evidence_state' = 'FRESH_CURRENT_PROBABILITY'))
         AS stamps_with_fresh_review,
       count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM paper_xavier_reviews r
          WHERE r.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
            AND r.reviewed_at BETWEEN st.observed_at
                AND st.observed_at + interval '30 seconds'))
         AS stamps_with_no_review,
       round(sum(30)::numeric / 3600, 3) AS max_fresh_hours_if_every_stamp_covered,
       round(extract(epoch FROM '2026-10-09 08:30:00+00'::timestamptz
                     - '2026-10-05 10:08:09+00'::timestamptz)::numeric / 3600, 1)
         AS prematch_hours_held
  FROM st;

\echo Y6 ALL GROUPS (72 h): STALE reviews whose contract had a PinnAPI stamp inside 30 s of the review, by our read refusal
WITH r AS (
  SELECT x.group_id, x.reviewed_at, x.trigger,
         coalesce(x.measure->>'feed_refusal', '-') AS feed_refusal,
         x.measure->>'source' AS src,
         (SELECT o.us_market_slug FROM paper_orders o
           WHERE o.group_id = x.group_id AND o.role = 'ENTRY' LIMIT 1) AS slug
    FROM paper_xavier_reviews x
   WHERE x.reviewed_at > now() - interval '72 hours'
     AND x.measure->>'evidence_state' <> 'FRESH_CURRENT_PROBABILITY')
SELECT feed_refusal, src, count(*) AS stale_reviews,
       count(DISTINCT group_id) AS groups,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM external_valuations v
          WHERE v.us_market_slug = r.slug
            AND v.provider = 'pinnapi.com/raw-websocket'
            AND v.observed_at BETWEEN r.reviewed_at - interval '30 seconds'
                                  AND r.reviewed_at))
         AS with_pinnapi_stamp_within_30s,
       count(DISTINCT group_id) FILTER (WHERE EXISTS (
         SELECT 1 FROM external_valuations v
          WHERE v.us_market_slug = r.slug
            AND v.provider = 'pinnapi.com/raw-websocket'
            AND v.observed_at BETWEEN r.reviewed_at - interval '30 seconds'
                                  AND r.reviewed_at))
         AS groups_with_stamp_within_30s
  FROM r
 GROUP BY 1, 2 ORDER BY 3 DESC
 LIMIT 40;

\echo Y7 FEED COUNTERS: prematch frame kinds by sport and the matchup-version counters
SELECT to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->'cache'->'frames_by_sport_type' AS frames_by_sport_type,
       (value->'cache'->'counts')->>'matchup_version_advanced_markets_pending'
         AS version_advanced_pending,
       (value->'cache'->'counts')->>'frames' AS frames,
       (value->'cache'->'counts')->>'price_changes' AS price_changes,
       value->'cache'->'stamp_to_receipt' AS stamp_to_receipt,
       value->'cache'->'connected_at' AS connected_at
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
