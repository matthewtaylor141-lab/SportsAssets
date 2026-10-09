-- READ-ONLY. RC6 lane xavier-records, second pass. The first pass
-- (rc6_xavrec_held_positions.sql) took its per-group population from fills
-- alone and so listed settled groups; here every per-group statement uses
-- the CANONICAL open set (bought - sold - the latest settlement version's
-- qty > 1e-9, open_position_canon.CANONICAL_OPEN_POSITIONS_SQL), plus the
-- three groups the 2026-10-08 20:03Z packet held (named), to show how the
-- two that left the book left it. Every statement is a SELECT.

\echo P1 the packets three groups: canonical open qty now, fills and settlements
WITH g(group_id) AS (VALUES ('papergrp:bfaade40fbe297f01e7dbac7'),
                            ('paperexpgrp:73296cf5e61c08e0a67e450f'),
                            ('paperexpgrp:fa090b4b3ee1f3d3e1979d07'))
SELECT g.group_id,
       (SELECT string_agg(f.role || ' ' || f.direction || ' ' || f.qty || ' @' ||
                          f.price || ' ' || f.filled_at, '; ' ORDER BY f.filled_at)
          FROM paper_fills f WHERE f.group_id = g.group_id) AS fills,
       (SELECT string_agg(s.outcome || ' v' || s.version || ' qty ' || s.qty ||
                          ' pays ' || s.payout_per_contract || ' ' || s.settled_at ||
                          ' via ' || s.evidence_source, '; ' ORDER BY s.version)
          FROM paper_settlements s WHERE s.group_id = g.group_id) AS settlements
  FROM g ORDER BY 1;

\echo P2 the last 5 reviews of the two groups that left the book, before they left it
SELECT group_id, reviewed_at, trigger, recommendation,
       measure->>'source' AS src, measure->>'evidence_state' AS ev,
       coalesce(measure->>'feed_refusal', '') AS feed_refusal,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'management_packet'->'protection'->>'state' AS protection,
       action->>'taken' AS taken
  FROM (SELECT r.*, row_number() OVER (PARTITION BY group_id
                                       ORDER BY reviewed_at DESC) rn
          FROM paper_xavier_reviews r
         WHERE r.group_id IN ('papergrp:bfaade40fbe297f01e7dbac7',
                              'paperexpgrp:73296cf5e61c08e0a67e450f')) x
 WHERE rn <= 5 ORDER BY group_id, reviewed_at DESC;

\echo P3 the CSC group: every held-read refusal of its reviews, all time
SELECT coalesce(measure->>'feed_refusal', measure->>'source') AS reason,
       measure->>'evidence_state' AS ev,
       left(coalesce((measure->'feed_detail')::text, ''), 160) AS feed_detail,
       count(*), min(reviewed_at), max(reviewed_at)
  FROM paper_xavier_reviews
 WHERE group_id = 'paperexpgrp:73296cf5e61c08e0a67e450f'
 GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 25;

\echo P4 canonical open positions now
WITH c AS (
  SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
         f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT now(), c.* FROM c ORDER BY group_id;

\echo P5 each open group: its reviews over the last 72 h, by evidence, held-read reason, identity, packet gaps
WITH c AS (
  SELECT f.group_id
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT group_id, measure->>'evidence_state' AS ev,
       coalesce(measure->>'feed_refusal', measure->>'source') AS reason,
       coalesce(measure->'feed'->>'identity_basis',
                measure->'feed_detail'->>'identity_basis') AS identity,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       count(*) AS reviews, min(reviewed_at), max(reviewed_at)
  FROM paper_xavier_reviews
 WHERE group_id IN (SELECT group_id FROM c)
   AND reviewed_at > now() - interval '72 hours'
 GROUP BY 1,2,3,4,5 ORDER BY 1, 6 DESC;

\echo P6 each open group: every FRESH-probability review (72 h) and what kept its packet incomplete
WITH c AS (
  SELECT f.group_id
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT group_id, reviewed_at, trigger, measure->>'source' AS src,
       round((measure->>'probability_age_s')::numeric, 1) AS p_age_s,
       measure->>'valuation_id' AS valuation_id,
       measure->'feed'->>'freshness_basis' AS basis,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'management_packet'->'book'->>'mark_class' AS book,
       selection->'management_packet'->'book'->>'reason' AS book_reason,
       round((selection->'management_packet'->'book'->>'age_s')::numeric, 1) AS book_age_s,
       selection->'management_packet'->'protection'->>'state' AS protection,
       recommendation, action->>'taken' AS taken
  FROM paper_xavier_reviews
 WHERE group_id IN (SELECT group_id FROM c)
   AND reviewed_at > now() - interval '72 hours'
   AND measure->>'evidence_state' = 'FRESH_CURRENT_PROBABILITY'
 ORDER BY group_id, reviewed_at;

\echo P7 each open group: its newest 6 reviews in full detail
WITH c AS (
  SELECT f.group_id
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT group_id, reviewed_at, trigger, recommendation,
       measure->>'source' AS src, measure->>'feed_refusal' AS feed_refusal,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'management_packet'->'book'->>'mark_class' AS book,
       selection->'management_packet'->'book'->>'reason' AS book_reason,
       selection->'management_packet'->'protection'->>'state' AS protection,
       selection->'management_packet'->'protection'->>'order_id' AS prot_order,
       left(coalesce((standing->'live_orders')::text, ''), 260) AS live_orders,
       left(coalesce((standing->'protective_price')::text, ''), 160) AS protective_price,
       left(coalesce(exposure::text, ''), 220) AS exposure,
       action->>'taken' AS taken
  FROM (SELECT r.*, row_number() OVER (PARTITION BY group_id
                                       ORDER BY reviewed_at DESC) rn
          FROM paper_xavier_reviews r
         WHERE r.group_id IN (SELECT group_id FROM c)) x
 WHERE rn <= 6 ORDER BY group_id, reviewed_at DESC;

\echo P8 each open group: every order with its ACTUAL state
WITH c AS (
  SELECT f.group_id
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT o.group_id, o.order_id, o.role, o.direction, o.state, o.qty,
       o.filled_qty, o.limit_price, o.time_in_force, o.created_at,
       o.expires_at, o.terminal_at, o.terminal_reason,
       round(extract(epoch FROM o.terminal_at - o.expires_at)::numeric, 1)
         AS terminal_after_expiry_s,
       round(extract(epoch FROM o.created_at - lag(o.terminal_at) OVER (
               PARTITION BY o.group_id, o.role ORDER BY o.created_at))::numeric, 1)
         AS gap_after_previous_terminal_s
  FROM paper_orders o WHERE o.group_id IN (SELECT group_id FROM c)
 ORDER BY o.group_id, o.created_at DESC LIMIT 120;

\echo P9 each open group: protection coverage over the last 72 h (seconds with no live unexpired protective order)
WITH c AS (
  SELECT f.group_id
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9),
p AS (
  SELECT o.group_id, o.created_at,
         least(o.expires_at, coalesce(o.terminal_at, now())) AS covered_until,
         lead(o.created_at) OVER (PARTITION BY o.group_id ORDER BY o.created_at)
           AS next_created
    FROM paper_orders o
   WHERE o.group_id IN (SELECT group_id FROM c)
     AND o.role = 'STANDING_PROTECTION'
     AND o.created_at > now() - interval '72 hours')
SELECT group_id, count(*) AS protective_orders,
       round(sum(greatest(0, extract(epoch FROM
             coalesce(next_created, now()) - covered_until)))::numeric, 1)
         AS uncovered_s,
       round(max(greatest(0, extract(epoch FROM
             coalesce(next_created, now()) - covered_until)))::numeric, 1)
         AS longest_gap_s,
       round(extract(epoch FROM now() - min(created_at))::numeric, 1) AS window_s
  FROM p GROUP BY 1 ORDER BY 1;

\echo P10 each open group: entry valuation (by id from its ENTRY decision) and the held contracts valuations since
WITH c AS (
  SELECT f.group_id, f.us_market_slug
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT c.group_id, v.id AS entry_valuation, v.provider, v.book, v.version,
       v.market, v.period, v.line, v.event_key, v.contract_selection,
       to_jsonb(v)->>'payout_event' AS payout_event,
       to_jsonb(v)->>'payout_is_complement' AS complement,
       v.probability, v.observed_at, v.received_at,
       (SELECT count(*) FROM external_valuations e
         WHERE e.us_market_slug = c.us_market_slug
           AND e.decided_at > now() - interval '72 hours') AS valuations_72h,
       (SELECT max(e.observed_at) FROM external_valuations e
         WHERE e.us_market_slug = c.us_market_slug
           AND e.decided_at > now() - interval '72 hours') AS newest_observed,
       (SELECT string_agg(DISTINCT e.event_key, ',') FROM external_valuations e
         WHERE e.us_market_slug = c.us_market_slug
           AND e.decided_at > now() - interval '72 hours') AS event_keys_72h
  FROM c JOIN LATERAL (SELECT * FROM paper_orders x
                        WHERE x.group_id = c.group_id AND x.role = 'ENTRY'
                        ORDER BY x.created_at LIMIT 1) o ON true
  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
  LEFT JOIN external_valuations v ON v.id = d.valuation_id;

\echo P11 each open group: the newest venue book and the last hour of observations
WITH c AS (
  SELECT f.us_market_slug
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1,2,3,4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT c.us_market_slug, b.observed_at, b.source, b.market_state, b.error,
       left(coalesce(b.bids::text, ''), 100) AS bids,
       left(coalesce(b.offers::text, ''), 100) AS offers,
       (SELECT count(*) FROM paper_book_observations z
         WHERE z.us_market_slug = c.us_market_slug
           AND z.observed_at > now() - interval '1 hour') AS obs_1h
  FROM c LEFT JOIN LATERAL (
       SELECT * FROM paper_book_observations y
        WHERE y.us_market_slug = c.us_market_slug
        ORDER BY y.observed_at DESC LIMIT 1) b ON true;

\echo P12 each open group: EXIT intents
SELECT i.group_id, i.intent_id, i.selection, i.state, i.decided_at,
       i.cancel_terminal_at, i.revalidate_by, i.resolved_at, i.resolution,
       i.exit_order_id, i.protection_order_id
  FROM paper_exit_intents i
 WHERE i.group_id IN ('paperexpgrp:fa090b4b3ee1f3d3e1979d07',
                      'papergrp:bfaade40fbe297f01e7dbac7',
                      'paperexpgrp:73296cf5e61c08e0a67e450f')
 ORDER BY i.group_id, i.decided_at DESC LIMIT 30;

\echo P13 PinnAPI feed heartbeat: state, authority, counts, confirmations, frames by sport and type
SELECT to_timestamp((value->>'beat_at')::float8) AS beat_ts,
       value->>'state' AS state, value->>'refused' AS refused,
       left(coalesce((value->'cache'->'authority')::text, ''), 300) AS authority
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT left((value->'cache'->'counts')::text, 1500) AS counts,
       (value->'cache'->'confirmations')::text AS confirmations
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT (value->'cache'->'frames_by_sport_type')::text AS frames_by_sport_type
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT left(coalesce((value->'held_priority_targets')::text, ''), 1500) AS held_watch
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
