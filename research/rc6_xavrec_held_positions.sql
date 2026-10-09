-- READ-ONLY. RC6 lane xavier-records: every CURRENTLY held PAPER position,
-- individually, with each input Xavier's management packet needs and why it
-- is missing or stale: identity and remaining quantity, the newest reviews'
-- probability (source-event, receipt and review instants kept apart), the
-- held PinnAPI read's refusal, the packet's missing elements, the book, the
-- protection and every associated order with its ACTUAL state, the EXIT
-- intents, the entry valuation's provider fixture, and the feed heartbeat.
-- Every statement is a SELECT. Positions are taken from the canonical open
-- set (bought - sold - latest settlement > 1e-9), never assumed.

\echo Q1 canonical open positions: identity, remaining qty, fills, event
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
SELECT now() AS read_at, c.account_id, c.group_id, c.us_market_slug,
       c.holding_side, c.open_qty,
       x.first_fill, x.last_fill, x.strategy, x.fills,
       (SELECT jsonb_build_object(
                 'game_start', to_jsonb(p)->>'game_start',
                 'market_state', to_jsonb(p)->>'market_state',
                 'sports_type', to_jsonb(p)->>'sports_type',
                 'team_league', to_jsonb(p)->>'team_league',
                 'event_title', to_jsonb(p)->>'event_title',
                 'event_slug', to_jsonb(p)->>'event_slug')::text
          FROM us_premap p WHERE p.market_slug = c.us_market_slug LIMIT 1)
         AS premap
  FROM c
  LEFT JOIN LATERAL (
       SELECT min(filled_at) AS first_fill, max(filled_at) AS last_fill,
              max(strategy) AS strategy, count(*) AS fills
         FROM paper_fills y
        WHERE y.account_id = c.account_id AND y.group_id = c.group_id
          AND y.us_market_slug = c.us_market_slug
          AND y.holding_side = c.holding_side) x ON true
 ORDER BY c.group_id;

\echo Q2 the newest 4 reviews of each open group: probability clocks, held-read refusal, packet gaps, action
WITH og AS (
  SELECT DISTINCT f.group_id FROM paper_fills f
   GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
  HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
       - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9),
r AS (
  SELECT r.*, row_number() OVER (PARTITION BY r.group_id
                                 ORDER BY r.reviewed_at DESC) AS rn
    FROM paper_xavier_reviews r
   WHERE r.group_id IN (SELECT group_id FROM og)
     AND r.reviewed_at > now() - interval '3 days')
SELECT group_id, reviewed_at, trigger, recommendation, refusal,
       measure->>'source' AS m_source,
       measure->>'evidence_state' AS ev_state,
       to_timestamp((measure->>'probability_source_at')::float8) AS p_source_at,
       to_timestamp((measure->>'probability_received_at')::float8) AS p_received_at,
       round((measure->>'probability_age_s')::numeric, 1) AS p_age_s,
       measure->>'feed_refusal' AS feed_refusal,
       measure->>'why' AS why,
       left(coalesce(measure->>'refresh_detail', ''), 80) AS refresh_detail,
       left(coalesce((measure->'feed_detail')::text,
                     (measure->'feed')::text, ''), 220) AS feed,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'management_packet'->'book'->>'mark_class' AS book,
       selection->'management_packet'->'protection'->>'state' AS protection,
       action->>'taken' AS taken,
       left(coalesce((measure->'exit_intent')::text, ''), 160) AS exit_intent
  FROM r WHERE rn <= 4
 ORDER BY group_id, reviewed_at DESC;

\echo Q3 per open group, last 48 h: reviews by evidence state, held-read refusal and packet completeness
WITH og AS (
  SELECT DISTINCT f.group_id FROM paper_fills f
   GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
  HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
       - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9)
SELECT group_id, measure->>'evidence_state' AS ev_state,
       coalesce(measure->>'feed_refusal', measure->>'why',
                measure->>'source') AS reason,
       measure->'feed'->>'identity_basis' AS identity,
       (selection->'management_packet'->'gate'->>'complete') AS complete,
       count(*) AS reviews, min(reviewed_at) AS first, max(reviewed_at) AS last
  FROM paper_xavier_reviews
 WHERE group_id IN (SELECT group_id FROM og)
   AND reviewed_at > now() - interval '48 hours'
 GROUP BY 1,2,3,4,5 ORDER BY 1, 6 DESC;

\echo Q4 per open group: the last complete-packet reviews (what made them current, what was done)
WITH og AS (
  SELECT DISTINCT f.group_id FROM paper_fills f
   GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
  HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
       - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9),
r AS (
  SELECT r.*, row_number() OVER (PARTITION BY r.group_id
                                 ORDER BY r.reviewed_at DESC) AS rn
    FROM paper_xavier_reviews r
   WHERE r.group_id IN (SELECT group_id FROM og)
     AND (r.selection->'management_packet'->'gate'->>'complete')::boolean)
SELECT group_id, reviewed_at, recommendation, measure->>'source' AS src,
       round((measure->>'probability_age_s')::numeric, 1) AS p_age_s,
       to_timestamp((measure->>'probability_source_at')::float8) AS p_source_at,
       measure->'feed'->>'identity_basis' AS identity,
       measure->'feed'->>'freshness_basis' AS freshness_basis,
       measure->'feed'->>'confirmed_by' AS confirmed_by,
       action->>'taken' AS taken,
       action->>'exit_intent_id' AS exit_intent
  FROM r WHERE rn <= 6 ORDER BY group_id, reviewed_at DESC;

\echo Q5 every order of each open group with its ACTUAL state (newest 15 per group)
WITH og AS (
  SELECT DISTINCT f.group_id FROM paper_fills f
   GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
  HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
       - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9),
o AS (
  SELECT o.*, row_number() OVER (PARTITION BY o.group_id
                                 ORDER BY o.created_at DESC) AS rn
    FROM paper_orders o WHERE o.group_id IN (SELECT group_id FROM og))
SELECT group_id, order_id, role, direction, state, qty, filled_qty,
       limit_price, order_type, time_in_force, created_at, expires_at,
       terminal_at, terminal_reason
  FROM o WHERE rn <= 15 ORDER BY group_id, created_at DESC;

\echo Q6 EXIT intents of each open group
SELECT i.group_id, i.intent_id, i.selection, i.state, i.decided_at,
       i.decided_probability_source_at, i.cancel_requested_at,
       i.cancel_deadline_at, i.cancel_terminal_at, i.revalidate_by,
       i.resolved_at, i.resolution, i.exit_order_id, i.protection_order_id,
       jsonb_array_length(i.transitions) AS transitions
  FROM paper_exit_intents i
 WHERE i.group_id IN (
   SELECT DISTINCT f.group_id FROM paper_fills f
    GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
   HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
        - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9)
 ORDER BY i.group_id, i.decided_at DESC;

\echo Q7 the entry valuation of each open group (by id from its ENTRY decision): provider fixture, market, line, clocks
WITH og AS (
  SELECT DISTINCT f.group_id FROM paper_fills f
   GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
  HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
       - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9)
SELECT o.group_id, o.order_id AS entry_order, d.decision_id, v.id AS valuation_id,
       v.provider, v.book, v.source_class, v.version, v.sport_family,
       v.market, v.period, v.line, v.event_key, v.contract_selection,
       v.mapped_outcome, to_jsonb(v)->>'payout_event' AS payout_event,
       to_jsonb(v)->>'payout_is_complement' AS complement,
       v.probability, v.observed_at, v.received_at, v.decided_at
  FROM og JOIN LATERAL (
       SELECT * FROM paper_orders x
        WHERE x.group_id = og.group_id AND x.role = 'ENTRY'
        ORDER BY x.created_at LIMIT 1) o ON true
  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
 ORDER BY o.group_id;

\echo Q8 valuations of the held contracts since entry: newest, count 24 h, and Xavier's probability snapshots
WITH oc AS (
  SELECT DISTINCT f.group_id, f.us_market_slug FROM paper_fills f
   GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
  HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
       - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9)
SELECT oc.group_id, oc.us_market_slug, v.n24, v.newest_decided,
       v.newest_observed, v.providers,
       s.snapshots, s.newest_snapshot_source_at, s.newest_snapshot_recorded
  FROM oc
  LEFT JOIN LATERAL (
       SELECT count(*) FILTER (WHERE e.decided_at > now() - interval '24 hours') AS n24,
              max(e.decided_at) AS newest_decided,
              max(e.observed_at) AS newest_observed,
              string_agg(DISTINCT e.provider, ',') AS providers
         FROM external_valuations e
        WHERE e.us_market_slug = oc.us_market_slug
          AND e.decided_at > now() - interval '7 days') v ON true
  LEFT JOIN LATERAL (
       SELECT count(*) AS snapshots, max(x.source_at) AS newest_snapshot_source_at,
              max(x.recorded_at) AS newest_snapshot_recorded
         FROM xavier_probability_snapshots x
        WHERE x.group_id = oc.group_id) s ON true
 ORDER BY oc.group_id;

\echo Q9 the held markets' latest venue book and the last hour's observations
WITH oc AS (
  SELECT DISTINCT f.us_market_slug FROM paper_fills f
   GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
  HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
       - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9)
SELECT oc.us_market_slug, b.observed_at, b.source, b.market_state, b.error,
       left(coalesce(b.bids::text, ''), 90) AS bids,
       left(coalesce(b.offers::text, ''), 90) AS offers,
       (SELECT count(*) FROM paper_book_observations z
         WHERE z.us_market_slug = oc.us_market_slug
           AND z.observed_at > now() - interval '1 hour') AS obs_1h
  FROM oc LEFT JOIN LATERAL (
       SELECT * FROM paper_book_observations y
        WHERE y.us_market_slug = oc.us_market_slug
        ORDER BY y.observed_at DESC LIMIT 1) b ON true
 ORDER BY 1;

\echo Q10 recorded packet refusals of the open groups, last 24 h, by missing set
SELECT group_id, refusal, missing::text AS missing, count(*),
       min(refused_at), max(refused_at)
  FROM paper_management_refusals
 WHERE refused_at > now() - interval '24 hours'
   AND group_id IN (
   SELECT DISTINCT f.group_id FROM paper_fills f
    GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
   HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
        - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9)
 GROUP BY 1,2,3 ORDER BY 1, 4 DESC;

\echo Q11 the newest assessment of each open group (what the management readback serves)
SELECT DISTINCT ON (a.group_id) a.group_id, a.assessed_at, a.trigger,
       a.evidence_state, a.probability_source,
       round(a.probability_age_s::numeric, 1) AS p_age_s,
       a.thesis_state, a.recommendation, a.recommendation_state,
       left(coalesce(a.valuation::text, ''), 300) AS valuation
  FROM xavier_management_assessments a
 WHERE a.position_kind = 'PAPER' AND a.group_id IN (
   SELECT DISTINCT f.group_id FROM paper_fills f
    GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
   HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
        - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) > 1e-9)
 ORDER BY a.group_id, a.assessed_at DESC;

\echo Q12 PinnAPI feed heartbeat: state, authority, cache counts, confirmations, frames by sport and type, held watch
SELECT value->>'beat_at' AS beat_at,
       to_timestamp((value->>'beat_at')::float8) AS beat_ts,
       value->>'state' AS state, value->>'refused' AS refused,
       left(coalesce((value->'cache'->'authority')::text, ''), 300) AS authority
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT left((value->'cache'->'counts')::text, 1500) AS counts,
       (value->'cache'->'confirmations')::text AS confirmations
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT (value->'cache'->'frames_by_sport_type')::text AS frames_by_sport_type
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT left(coalesce((value->'held_priority_targets')::text, ''), 1500) AS held_watch,
       left(coalesce((value->'last_owner_restart')::text, ''), 300) AS last_restart,
       left(coalesce((value->'owner_task')::text, ''), 300) AS owner_task
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
