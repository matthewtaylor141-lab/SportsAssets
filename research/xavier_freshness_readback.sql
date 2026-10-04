-- XAVIER FRESHNESS READBACK (c28 XFRESH): SELECT only.
-- For every currently HELD paper position (handed to Xavier, net open > 0,
-- not settled): Xavier's LATEST review and assessment, the probability it
-- stood on (source time, age now, the recorded freshness limit, expiry),
-- whether a NEWER valuation of the same contract exists, and whether the
-- stored recommendation is an action word on evidence that is not fresh
-- (the P0 defect: HOLD surviving by default). Pre-deploy this documents the
-- defect; post-deploy (migration 222) the same query shows the new
-- WAITING_FOR_FRESH_EVIDENCE / valuation columns.

\echo === read time
SELECT now() AS read_at;

\echo === held paper positions: latest Xavier decision and its evidence
WITH held AS (
    SELECT f.group_id, min(f.us_market_slug) AS slug,
           min(f.holding_side) AS side,
           sum(CASE WHEN f.direction = 'BUY' THEN f.qty ELSE -f.qty END)
             AS open_qty
      FROM paper_fills f
      JOIN paper_handoffs h ON h.group_id = f.group_id
     WHERE NOT EXISTS (SELECT 1 FROM paper_settlements s
                        WHERE s.group_id = f.group_id)
     GROUP BY f.group_id
    HAVING sum(CASE WHEN f.direction = 'BUY' THEN f.qty ELSE -f.qty END) > 0
), rv AS (
    SELECT DISTINCT ON (r.group_id) r.group_id, r.review_id, r.reviewed_at,
           r.trigger, r.recommendation, r.measure
      FROM paper_xavier_reviews r JOIN held USING (group_id)
     ORDER BY r.group_id, r.reviewed_at DESC, r.review_id DESC
), am AS (
    SELECT DISTINCT ON (a.group_id) a.group_id, a.assessment_id,
           a.assessed_at, a.recommendation AS a_recommendation,
           a.evidence_state AS a_evidence_state, a.probability_age_s,
           to_jsonb(a) -> 'valuation' AS a_valuation,
           to_jsonb(a) ->> 'recommendation_state' AS a_recommendation_state
      FROM xavier_management_assessments a JOIN held USING (group_id)
     WHERE a.position_kind = 'PAPER'
     ORDER BY a.group_id, a.assessed_at DESC, a.assessment_id DESC
), lv AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, v.id AS latest_valuation_id,
           v.probability AS latest_valuation_p,
           v.observed_at AS latest_valuation_at
      FROM paper_orders o
      JOIN held ON held.group_id = o.group_id
      JOIN paper_decisions d ON d.decision_id = o.decision_id
      JOIN external_valuations c ON c.id = d.valuation_id
      JOIN external_valuations v
        ON v.us_market_slug = o.us_market_slug
       AND v.buy_intent = CASE WHEN o.holding_side = 'LONG'
                               THEN 'ORDER_INTENT_BUY_LONG'
                               ELSE 'ORDER_INTENT_BUY_SHORT' END
       AND v.payout_event = c.payout_event
       AND v.payout_is_complement = c.payout_is_complement
       AND v.probability IS NOT NULL
       AND v.decided_at > now() - interval '6 hours'
     WHERE o.role = 'ENTRY'
     ORDER BY o.group_id, v.decided_at DESC, v.id DESC
), x AS (
    SELECT held.group_id AS position_id, held.slug, held.side,
           held.open_qty, rv.review_id, rv.reviewed_at, rv.trigger,
           rv.recommendation AS latest_decision,
           rv.measure ->> 'evidence_state' AS evidence_state,
           rv.measure ->> 'source' AS valuation_source,
           (rv.measure ->> 'valuation_id') AS valuation_id,
           to_timestamp(coalesce(
               (rv.measure ->> 'probability_source_at')::float8,
               extract(epoch FROM rv.reviewed_at)
                 - (rv.measure ->> 'probability_age_s')::float8))
             AS valuation_ts,
           (rv.measure ->> 'probability_age_s')::float8 AS age_at_review_s,
           coalesce((rv.measure ->> 'probability_limit_s')::float8,
                    (rv.measure ->> 'pinnacle_limit_s')::float8)
             AS freshness_limit_s,
           am.assessment_id, am.a_recommendation, am.a_evidence_state,
           am.a_recommendation_state,
           lv.latest_valuation_id, lv.latest_valuation_p,
           lv.latest_valuation_at
      FROM held
      LEFT JOIN rv ON rv.group_id = held.group_id
      LEFT JOIN am ON am.group_id = held.group_id
      LEFT JOIN lv ON lv.group_id = held.group_id
)
SELECT position_id, slug, side, open_qty, review_id, reviewed_at, trigger,
       latest_decision, evidence_state, valuation_source, valuation_id,
       valuation_ts,
       valuation_ts + make_interval(secs => coalesce(freshness_limit_s, 30))
         AS freshness_expiry,
       round(extract(epoch FROM now() - valuation_ts)::numeric, 3)
         AS age_now_s,
       age_at_review_s, freshness_limit_s,
       (now() > valuation_ts
                + make_interval(secs => coalesce(freshness_limit_s, 30)))
         AS expired_now,
       (latest_valuation_at IS NOT NULL
        AND latest_valuation_at > valuation_ts + interval '1 millisecond')
         AS newer_valuation_exists,
       latest_valuation_id, latest_valuation_p, latest_valuation_at,
       (latest_decision IN ('HOLD', 'EXIT', 'REDUCE', 'REALLOCATE')
        AND coalesce(evidence_state, '') <> 'FRESH_CURRENT_PROBABILITY')
         AS action_word_on_stale_evidence,
       assessment_id, a_recommendation, a_evidence_state,
       a_recommendation_state
  FROM x
 ORDER BY reviewed_at DESC NULLS LAST
 LIMIT 200;

\echo === the last 24 h: reviews by recorded decision and evidence state
SELECT r.recommendation, r.measure ->> 'evidence_state' AS evidence_state,
       r.trigger, count(*) AS n, max(r.reviewed_at) AS latest
  FROM paper_xavier_reviews r
 WHERE r.reviewed_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 60;

\echo === per held position: reviews in the last hour and how many stood on stale evidence
SELECT r.group_id, count(*) AS reviews_1h,
       count(*) FILTER (WHERE r.measure ->> 'evidence_state'
                        <> 'FRESH_CURRENT_PROBABILITY') AS stale_1h,
       count(*) FILTER (WHERE r.recommendation = 'HOLD'
                        AND r.measure ->> 'evidence_state'
                        <> 'FRESH_CURRENT_PROBABILITY') AS hold_on_stale_1h,
       min((r.measure ->> 'probability_age_s')::float8) AS min_age_s,
       max((r.measure ->> 'probability_age_s')::float8) AS max_age_s
  FROM paper_xavier_reviews r
 WHERE r.reviewed_at > now() - interval '1 hour'
 GROUP BY r.group_id ORDER BY reviews_1h DESC LIMIT 60;

\echo === table sizes (read-time context cost)
SELECT relname, reltuples::bigint AS est_rows
  FROM pg_class
 WHERE relname IN ('external_valuations', 'paper_xavier_reviews',
                   'xavier_management_assessments', 'paper_book_observations')
 ORDER BY relname;
