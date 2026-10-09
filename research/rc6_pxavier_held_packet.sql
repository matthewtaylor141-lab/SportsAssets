-- READ-ONLY. RC6.2 lane p-xavier (diagnosis): the scorecard unit
-- held_positions_with_complete_current_packet read 0 / 1 on pm-acceptance
-- 37888018192 (2026-10-09 05:28Z): one open PAPER position (group
-- paperexpgrp:fa090b4b3ee1f3d3e1979d07, contract
-- atc-idnsl-pke-mau-2026-10-09-pke, SHORT 350), evidence
-- STALE_ENTRY_TIME_PROBABILITY. Which packet elements
-- (xavier_packet.ELEMENTS) are missing, why, and is a denominator of 1 the
-- whole population? Every statement is a SELECT.

\echo X0 read instant
SELECT now() AS read_at;

\echo X1 POPULATION: canonical open PAPER positions now, every account and strategy
SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
       round(c.open_qty::numeric, 6) AS open_qty,
       (SELECT o.strategy FROM paper_orders o WHERE o.group_id = c.group_id
           AND o.role = 'ENTRY' ORDER BY o.created_at LIMIT 1) AS strategy,
       (SELECT min(f.filled_at) FROM paper_fills f
         WHERE f.group_id = c.group_id) AS first_fill_at,
       (SELECT max(f.filled_at) FROM paper_fills f
         WHERE f.group_id = c.group_id) AS last_fill_at,
       (SELECT count(*) FROM paper_handoffs h
         WHERE h.group_id = c.group_id) AS handoffs
  FROM (SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
               f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
          FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                       coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                           AS bought,
                       coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                           AS sold
                  FROM paper_fills
                 GROUP BY account_id, group_id, us_market_slug,
                          holding_side) f
          LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                       FROM paper_settlements
                      ORDER BY position_key, version DESC) s
            ON s.position_key = 'paperpos:' || f.account_id || ':'
                                || f.group_id || ':' || f.us_market_slug
                                || ':' || f.holding_side
         WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9) c
 ORDER BY c.account_id, c.group_id;

\echo X2 POPULATION: actual (small-live) handoffs by state and venue
SELECT venue, state, count(*) AS handoffs, max(updated_at) AS newest
  FROM smalllive_handoffs GROUP BY 1, 2 ORDER BY 1, 2;

\echo X3 the PKE group: every fill
SELECT filled_at, account_id, direction, role, qty, price, gross_usd, fee_usd,
       order_id
  FROM paper_fills
 WHERE group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
 ORDER BY filled_at;

\echo X4 the PKE group: every order with its state (bounded row text)
SELECT o.created_at, o.role, o.state, o.expires_at, o.terminal_at,
       left((to_jsonb(o) - 'group_id' - 'account_id')::text, 700) AS row_text
  FROM paper_orders o
 WHERE o.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
 ORDER BY o.created_at;

\echo X5 the PKE group: settlements
SELECT left(to_jsonb(s)::text, 600) AS settlement
  FROM paper_settlements s
 WHERE s.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
 ORDER BY s.version;

\echo X6 the PKE contract and its event in the venue catalogue (us_premap)
SELECT market_slug, identifier, event_slug, event_title, kind, side_norm,
       team_name, team_league, sports_type, signed, line, game_start,
       left(question, 140) AS question
  FROM us_premap
 WHERE event_slug = (SELECT event_slug FROM us_premap
                      WHERE market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
                      LIMIT 1)
    OR market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
 ORDER BY market_slug
 LIMIT 40;

\echo X7 the entry decision and the valuation it stood on
SELECT d.decision_id, d.decided_at, d.strategy, d.verdict, d.p_pinnacle,
       d.valuation_id, v.provider, v.event_key, v.buy_intent, v.payout_event,
       v.payout_is_complement, v.observed_at, v.received_at, v.decided_at
         AS valuation_decided_at, v.market, v.line,
       v.settlement_comparison->'reference_input'->>'feed_event_id'
         AS pinnapi_fixture
  FROM paper_orders o
  JOIN paper_decisions d ON d.decision_id = o.decision_id
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE o.group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
   AND o.role = 'ENTRY'
 LIMIT 3;

\echo X8 PROBABILITY SUPPLY: valuations of the PKE contract per provider per hour, last 48 h
SELECT date_trunc('hour', decided_at) AS hour, provider,
       count(*) AS valuations,
       count(DISTINCT observed_at) AS distinct_source_stamps,
       max(observed_at) AS newest_source_stamp,
       max(received_at) AS newest_receipt,
       max(decided_at) AS newest_decided,
       round(min(probability)::numeric, 4) AS p_min,
       round(max(probability)::numeric, 4) AS p_max
  FROM external_valuations
 WHERE us_market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
   AND decided_at > now() - interval '48 hours'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2
 LIMIT 120;

\echo X9 PROBABILITY SUPPLY: the newest 25 valuations of the PKE contract with their three clocks
SELECT id, provider, buy_intent, payout_event, payout_is_complement,
       round(probability::numeric, 5) AS p, observed_at, received_at,
       decided_at,
       round(extract(epoch FROM decided_at - observed_at)::numeric, 3)
         AS source_to_decided_s,
       event_key,
       settlement_comparison->'reference_input'->>'feed_event_id'
         AS pinnapi_fixture,
       left(coalesce((settlement_comparison->'reference_input')::text, ''),
            300) AS reference_input
  FROM external_valuations
 WHERE us_market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
 ORDER BY decided_at DESC
 LIMIT 25;

\echo X10 PROBABILITY SUPPLY: source-stamp gaps of the PKE contract (consecutive distinct observed_at), last 48 h
WITH s AS (
  SELECT DISTINCT provider, observed_at
    FROM external_valuations
   WHERE us_market_slug = 'atc-idnsl-pke-mau-2026-10-09-pke'
     AND decided_at > now() - interval '48 hours'),
g AS (
  SELECT provider, observed_at,
         extract(epoch FROM observed_at - lag(observed_at) OVER
                 (PARTITION BY provider ORDER BY observed_at)) AS gap_s
    FROM s)
SELECT provider, count(*) AS stamps,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s)::numeric, 1)
         AS p50_gap_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY gap_s)::numeric, 1)
         AS p90_gap_s,
       round(max(gap_s)::numeric, 1) AS max_gap_s,
       count(*) FILTER (WHERE gap_s <= 30) AS gaps_within_30s,
       min(observed_at) AS first_stamp, max(observed_at) AS last_stamp
  FROM g GROUP BY 1 ORDER BY 1;

\echo X11 XAVIER: the newest 15 reviews of every open group, every packet element
WITH c AS (
  SELECT DISTINCT f.group_id
    FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                 coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                 coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
            FROM paper_fills GROUP BY 1, 2, 3, 4) f
    LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                 FROM paper_settlements ORDER BY position_key, version DESC) s
      ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                          || ':' || f.us_market_slug || ':' || f.holding_side
   WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT group_id, reviewed_at, trigger, recommendation, refusal,
       measure->>'evidence_state' AS ev,
       measure->>'source' AS src,
       round((measure->>'probability_age_s')::numeric, 1) AS p_age_s,
       measure->>'probability_source_at' AS p_source_at,
       measure->>'probability_received_at' AS p_received_at,
       measure->>'valuation_id' AS valuation_id,
       coalesce(measure->>'feed_refusal', '-') AS feed_refusal,
       left(coalesce((measure->'feed_detail')::text, ''), 400) AS feed_detail,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'management_packet'->'book'->>'mark_class' AS book,
       round((selection->'management_packet'->'book'->>'age_s')::numeric, 1)
         AS book_age_s,
       selection->'management_packet'->'exit_depth'->>'at_mark' AS depth,
       selection->'management_packet'->'residual'->>'present' AS qty_ok,
       selection->'management_packet'->'settlement'->>'present' AS ident_ok,
       selection->'management_packet'->'protection'->>'state' AS protection,
       left(coalesce((standing->'protective_price')::text, ''), 200)
         AS protective_price,
       action->>'taken' AS taken
  FROM (SELECT r.*, row_number() OVER (PARTITION BY group_id
                                       ORDER BY reviewed_at DESC) rn
          FROM paper_xavier_reviews r
         WHERE r.group_id IN (SELECT group_id FROM c)) x
 WHERE rn <= 15 ORDER BY group_id, reviewed_at DESC;

\echo X12 XAVIER: the PKE group, all reviews since its first fill, by evidence, feed refusal and packet gaps
SELECT measure->>'evidence_state' AS ev,
       coalesce(measure->>'source', '-') AS src,
       coalesce(measure->>'feed_refusal', '-') AS feed_refusal,
       coalesce(measure->'feed_detail'->>'identity_basis',
                measure->'feed'->>'identity_basis', '-') AS identity,
       coalesce(measure->'feed_detail'->'held_fixture'->>'basis',
                measure->'feed'->'held_fixture'->>'basis', '-')
         AS held_fixture_basis,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'management_packet'->'protection'->>'state' AS protection,
       count(*) AS reviews, min(reviewed_at) AS first, max(reviewed_at) AS last
  FROM paper_xavier_reviews
 WHERE group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
 GROUP BY 1, 2, 3, 4, 5, 6, 7
 ORDER BY 8 DESC
 LIMIT 40;

\echo X13 XAVIER: the PKE group per 6 h, reviews, fresh probability, packet complete, each element missing
SELECT to_timestamp(floor(extract(epoch FROM reviewed_at) / 21600) * 21600)
         AS bucket,
       count(*) AS reviews,
       count(*) FILTER (WHERE measure->>'evidence_state'
                        = 'FRESH_CURRENT_PROBABILITY') AS fresh_p,
       count(*) FILTER (WHERE (selection->'management_packet'->'gate'
                               ->>'complete')::boolean) AS complete,
       count(*) FILTER (WHERE selection->'management_packet'->'gate'->'missing'
                        ? 'NO_FRESH_PROBABILITY') AS miss_probability,
       count(*) FILTER (WHERE selection->'management_packet'->'gate'->'missing'
                        ? 'NO_VALID_ACTIVE_PROTECTION') AS miss_protection,
       count(*) FILTER (WHERE selection->'management_packet'->'gate'->'missing'
                        ? 'NO_CURRENT_EXECUTABLE_BOOK') AS miss_book,
       count(*) FILTER (WHERE selection->'management_packet'->'gate'->'missing'
                        ? 'NO_EXECUTABLE_EXIT_DEPTH') AS miss_depth,
       count(*) FILTER (WHERE selection->'management_packet'->'gate'->'missing'
                        ? 'NO_SETTLEMENT_IDENTITY') AS miss_identity,
       count(*) FILTER (WHERE selection->'management_packet'->'gate'->'missing'
                        ? 'NO_RECONCILED_POSITION_QTY') AS miss_qty,
       count(*) FILTER (WHERE trigger = 'MARKET_EVENT') AS market_event,
       count(*) FILTER (WHERE trigger = 'MARKET_EVENT' AND
                        measure->>'evidence_state'
                        = 'FRESH_CURRENT_PROBABILITY') AS market_event_fresh
  FROM paper_xavier_reviews
 WHERE group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
 GROUP BY 1 ORDER BY 1 DESC
 LIMIT 40;

\echo X14 XAVIER: the PKE group, every FRESH-probability review (all time): what kept the packet incomplete
SELECT reviewed_at, trigger, measure->>'source' AS src,
       round((measure->>'probability_age_s')::numeric, 1) AS p_age_s,
       measure->>'valuation_id' AS valuation_id,
       measure->'feed'->>'freshness_basis' AS basis,
       selection->'management_packet'->'gate'->>'missing' AS missing,
       selection->'management_packet'->'protection'->>'state' AS protection
  FROM paper_xavier_reviews
 WHERE group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
   AND measure->>'evidence_state' = 'FRESH_CURRENT_PROBABILITY'
 ORDER BY reviewed_at DESC
 LIMIT 40;

\echo X15 XAVIER ASSESSMENTS: the PKE group per trigger (24 h): latency from due to review and evidence at review
SELECT trigger, count(*) AS assessments,
       count(*) FILTER (WHERE evidence_state = 'FRESH_CURRENT_PROBABILITY')
         AS fresh,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY review_latency_s)
             ::numeric, 1) AS p50_latency_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY review_latency_s)
             ::numeric, 1) AS p90_latency_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY probability_age_s)
             ::numeric, 1) AS p50_probability_age_s,
       round(min(probability_age_s)::numeric, 1) AS min_probability_age_s,
       count(*) FILTER (WHERE probability_age_s <= 30) AS age_within_30s,
       max(assessed_at) AS newest
  FROM xavier_management_assessments
 WHERE group_id = 'paperexpgrp:fa090b4b3ee1f3d3e1979d07'
   AND assessed_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo X16 FEED: the held watch and the feed state as the PinnAPI heartbeat reports them
SELECT to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->>'state' AS state,
       value->>'refused' AS refused,
       value->'cache'->'authority' AS authority,
       value->'held_priority_targets' AS held_targets,
       value->'cache'->'confirmations' AS confirmations,
       value->'cache'->'events_by_sport' AS events_by_sport
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo X17 ALL GROUPS (72 h): reviews by packet gap set, for the wider population
SELECT selection->'management_packet'->'gate'->>'missing' AS missing,
       measure->>'evidence_state' AS ev,
       count(*) AS reviews, count(DISTINCT group_id) AS groups,
       max(reviewed_at) AS newest
  FROM paper_xavier_reviews
 WHERE reviewed_at > now() - interval '72 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC
 LIMIT 30;
