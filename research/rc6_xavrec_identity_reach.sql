-- READ-ONLY. RC6 lane xavier-records: how many held PAPER groups (14 days)
-- had Xavier's held PinnAPI read refuse on IDENTITY (NO_FEED_EVENT /
-- STRUCTURED_PARTICIPANTS_NOT_TWO) while the PinnAPI primary matcher had
-- priced the SAME contract (same slug, payout event, complement) and recorded
-- its PinnAPI fixture (settlement_comparison.reference_input.feed_event_id).
-- Every statement is a SELECT.

\echo I1 held groups by identity refusal, and whether the same contract has a PinnAPI-matched fixture on record
WITH ref AS (
  SELECT r.group_id, measure->>'feed_refusal' AS refusal, count(*) AS reviews,
         max(r.reviewed_at) AS last_review
    FROM paper_xavier_reviews r
   WHERE r.reviewed_at > now() - interval '14 days'
     AND measure->>'feed_refusal' IN ('NO_FEED_EVENT',
                                      'STRUCTURED_PARTICIPANTS_NOT_TWO')
   GROUP BY 1, 2),
ent AS (
  SELECT DISTINCT ON (o.group_id) o.group_id, o.us_market_slug, d.strategy,
         v.event_key, v.payout_event, v.payout_is_complement
    FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
    JOIN external_valuations v ON v.id = d.valuation_id
   WHERE o.role = 'ENTRY' AND o.group_id IN (SELECT group_id FROM ref)
   ORDER BY o.group_id, o.created_at)
SELECT e.strategy, ref.refusal, (e.event_key LIKE 'pinnapi:%') AS entry_pinnapi_key,
       (SELECT count(DISTINCT p.settlement_comparison->'reference_input'->>'feed_event_id') > 0
          FROM external_valuations p
         WHERE p.us_market_slug = e.us_market_slug
           AND p.provider = 'pinnapi.com/raw-websocket'
           AND p.payout_event IS NOT DISTINCT FROM e.payout_event
           AND p.payout_is_complement IS NOT DISTINCT FROM e.payout_is_complement
           AND p.settlement_comparison->'reference_input'->>'feed_event_id' IS NOT NULL)
         AS primary_fixture_on_record,
       count(*) AS groups, sum(ref.reviews) AS reviews
  FROM ref JOIN ent e ON e.group_id = ref.group_id
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC;

\echo I2 distinct PinnAPI fixtures recorded per contract by the primary matcher (more than one means a re-posted matchup)
SELECT n_fixtures, count(*) AS contracts FROM (
  SELECT p.us_market_slug, p.payout_event,
         count(DISTINCT p.settlement_comparison->'reference_input'->>'feed_event_id') AS n_fixtures
    FROM external_valuations p
   WHERE p.provider = 'pinnapi.com/raw-websocket'
     AND p.decided_at > now() - interval '14 days'
     AND p.settlement_comparison->'reference_input'->>'feed_event_id' IS NOT NULL
   GROUP BY 1, 2) x GROUP BY 1 ORDER BY 1;

\echo I3 the fixture-match basis the primary recorded on those rows
SELECT p.settlement_comparison->'reference_input'->>'fixture_match' AS fixture_match,
       (p.event_key LIKE 'pinnapi:%') AS pinnapi_key, count(*)
  FROM external_valuations p
 WHERE p.provider = 'pinnapi.com/raw-websocket'
   AND p.decided_at > now() - interval '3 days'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo I4 the planner for the per-contract lookup (estimated, not executed)
EXPLAIN SELECT p.id, p.settlement_comparison->'reference_input'->>'feed_event_id'
  FROM external_valuations p
 WHERE p.us_market_slug = 'atc-brb-csc-cri-2026-10-08-csc'
   AND p.provider = 'pinnapi.com/raw-websocket'
   AND p.payout_event IS NOT DISTINCT FROM 'Ceará'
   AND p.payout_is_complement IS NOT DISTINCT FROM false
   AND p.settlement_comparison->'reference_input'->>'feed_event_id' IS NOT NULL
 ORDER BY p.decided_at DESC LIMIT 1;
SELECT count(*) AS valuations_total FROM external_valuations;
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'external_valuations';
