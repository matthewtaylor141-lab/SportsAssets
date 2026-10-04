-- XAVIER FRESHNESS, SPLIT (cand24 quality scorecard): SELECT only.
-- Mirrors agents/quality_scorecard.py on branch claude/cand24-xfresh:
--   freshness_when_market_changed, scheduled_review_no_provider_change,
--   market_change_to_review_sla (+ median / p90 latency),
--   positions_monitored_share, freshness_at_discretionary_action,
--   and the old reviews_fresh_share (all paper reviews) for comparison.
-- Window: the scorecard's 7 days (and the prior 7 days for the trend).

\echo === read time and window
SELECT now() AS read_at, now() - interval '7 days' AS window_start;

\echo === assessments in window: freshness split (current, prior)
SELECT w.label,
       count(a.*) AS total,
       count(a.*) FILTER (WHERE a.evidence_state = 'FRESH_CURRENT_PROBABILITY') AS fresh_all,
       count(a.*) FILTER (WHERE a.trigger = 'MARKET_EVENT') AS market_n,
       count(a.*) FILTER (WHERE a.trigger = 'MARKET_EVENT'
                          AND a.evidence_state = 'FRESH_CURRENT_PROBABILITY') AS market_fresh,
       round(count(a.*) FILTER (WHERE a.trigger = 'MARKET_EVENT'
                          AND a.evidence_state = 'FRESH_CURRENT_PROBABILITY')::numeric
             / nullif(count(a.*) FILTER (WHERE a.trigger = 'MARKET_EVENT'), 0), 6) AS freshness_when_market_changed,
       count(a.*) FILTER (WHERE a.trigger = 'SCHEDULED_BACKSTOP') AS backstop_n,
       count(a.*) FILTER (WHERE a.trigger = 'SCHEDULED_BACKSTOP'
                          AND a.evidence_state = 'FRESH_CURRENT_PROBABILITY') AS backstop_fresh,
       count(a.*) FILTER (WHERE a.trigger = 'SCHEDULED_BACKSTOP'
                          AND a.evidence_state = 'STALE_ENTRY_TIME_PROBABILITY') AS backstop_stale,
       count(a.*) FILTER (WHERE a.trigger = 'SCHEDULED_BACKSTOP'
                          AND a.evidence_state = 'PROBABILITY_UNAVAILABLE') AS backstop_unavailable,
       count(a.*) FILTER (WHERE a.trigger = 'SCHEDULED_BACKSTOP'
                          AND a.evidence_state <> 'FRESH_CURRENT_PROBABILITY') AS no_change,
       round(count(a.*) FILTER (WHERE a.trigger = 'SCHEDULED_BACKSTOP'
                          AND a.evidence_state <> 'FRESH_CURRENT_PROBABILITY')::numeric
             / nullif(count(a.*), 0), 6) AS scheduled_no_change_share,
       round(count(a.*) FILTER (WHERE a.evidence_state = 'FRESH_CURRENT_PROBABILITY')::numeric
             / nullif(count(a.*) - count(a.*) FILTER (WHERE a.trigger = 'SCHEDULED_BACKSTOP'
                          AND a.evidence_state <> 'FRESH_CURRENT_PROBABILITY'), 0), 6)
             AS fresh_share_excluding_unchanged_quotes,
       count(a.*) FILTER (WHERE a.recommendation IN ('EXIT', 'REDUCE', 'REALLOCATE')
                          OR a.discretionary_permitted) AS disc_n,
       count(a.*) FILTER (WHERE (a.recommendation IN ('EXIT', 'REDUCE', 'REALLOCATE')
                          OR a.discretionary_permitted)
                          AND a.evidence_state = 'FRESH_CURRENT_PROBABILITY') AS disc_fresh,
       count(a.*) FILTER (WHERE a.recommendation IN ('EXIT', 'REDUCE', 'REALLOCATE')) AS rec_n,
       count(a.*) FILTER (WHERE a.recommendation IN ('EXIT', 'REDUCE', 'REALLOCATE')
                          AND a.evidence_state = 'FRESH_CURRENT_PROBABILITY') AS rec_fresh,
       max(a.assessed_at) AS last_assessed_at
  FROM (VALUES ('current', now() - interval '7 days', now()),
               ('prior', now() - interval '14 days', now() - interval '7 days')) AS w(label, s, e)
  LEFT JOIN xavier_management_assessments a
    ON a.assessed_at >= w.s AND a.assessed_at < w.e
 GROUP BY w.label ORDER BY w.label;

\echo === assessments by kind, trigger, evidence state (current window)
SELECT position_kind, trigger, evidence_state, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY probability_age_s)::numeric, 3) AS median_prob_age_s,
       round(max(probability_age_s)::numeric, 3) AS max_prob_age_s
  FROM xavier_management_assessments
 WHERE assessed_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo === market change -> review SLA (30 s) and latency (current, prior)
SELECT w.label,
       count(a.*) AS market_reviews,
       count(a.review_latency_s) AS with_change_instant,
       count(a.*) - count(a.review_latency_s) AS without_change_instant,
       count(a.*) FILTER (WHERE a.review_latency_s <= 30) AS within_sla,
       round(count(a.*) FILTER (WHERE a.review_latency_s <= 30)::numeric
             / nullif(count(a.review_latency_s), 0), 6) AS sla_share,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY a.review_latency_s)::numeric, 3) AS median_s,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY a.review_latency_s)::numeric, 3) AS p90_s,
       round(max(a.review_latency_s)::numeric, 3) AS max_s
  FROM (VALUES ('current', now() - interval '7 days', now()),
               ('prior', now() - interval '14 days', now() - interval '7 days')) AS w(label, s, e)
  LEFT JOIN xavier_management_assessments a
    ON a.trigger = 'MARKET_EVENT' AND a.assessed_at >= w.s AND a.assessed_at < w.e
 GROUP BY w.label ORDER BY w.label;

\echo === market change SLA by kind (current window)
SELECT position_kind, count(*) AS n, count(review_latency_s) AS with_change_instant,
       count(*) FILTER (WHERE review_latency_s <= 30) AS within_sla,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY review_latency_s)::numeric, 3) AS median_s,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY review_latency_s)::numeric, 3) AS p90_s
  FROM xavier_management_assessments
 WHERE trigger = 'MARKET_EVENT' AND assessed_at >= now() - interval '7 days'
 GROUP BY 1 ORDER BY 1;

\echo === held watch heartbeat (persisted telemetry; cumulative since feed start)
SELECT value->>'beat_at' AS beat_at,
       to_timestamp((value->>'beat_at')::float8) AS beat_ts,
       value->'held_priority_targets'->>'held_slugs' AS held_slugs,
       value->'held_priority_targets'->>'held_events' AS held_events,
       value->'held_priority_targets'->'unmatched' AS unmatched,
       value->'held_priority_targets'->'counts' AS counts
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo === old reviews_fresh_share: all paper reviews (current window)
SELECT count(*) AS n,
       count(*) FILTER (WHERE measure->>'evidence_state' = 'FRESH_CURRENT_PROBABILITY') AS k,
       round(count(*) FILTER (WHERE measure->>'evidence_state' = 'FRESH_CURRENT_PROBABILITY')::numeric
             / nullif(count(*), 0), 6) AS share
  FROM paper_xavier_reviews WHERE reviewed_at >= now() - interval '7 days';

\echo === paper reviews by trigger and evidence (current window)
SELECT trigger, coalesce(measure->>'evidence_state', '(none)') AS evidence_state, count(*) AS n
  FROM paper_xavier_reviews WHERE reviewed_at >= now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo === discretion cross-check: paper reviews recommending EXIT / REDUCE / REALLOCATE
SELECT count(*) AS recommended,
       count(*) FILTER (WHERE coalesce(measure->>'evidence_state', '') <> 'FRESH_CURRENT_PROBABILITY') AS not_fresh
  FROM paper_xavier_reviews
 WHERE recommendation IN ('EXIT', 'REDUCE', 'REALLOCATE')
   AND reviewed_at >= now() - interval '7 days';

\echo === positions_monitored_share (management_view, limit 500 per book)
WITH cad AS (
    SELECT coalesce((SELECT (config->'cadence'->>'xavier_backstop_s')::float8
                       FROM paper_sessions ORDER BY started_at DESC LIMIT 1), 60.0) AS paper_s,
           60.0::float8 AS actual_s),
paper AS (
    SELECT 'PAPER'::text AS kind, h.group_id,
           (coalesce((SELECT sum(qty) FILTER (WHERE direction = 'BUY')
                           - coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0)
                        FROM paper_fills f WHERE f.group_id = h.group_id), 0) > 1e-9
            AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = h.group_id))
           AS is_open
      FROM paper_handoffs h JOIN paper_orders o ON o.order_id = h.entry_order_id
     ORDER BY h.first_fill_at DESC LIMIT 500),
actual AS (
    SELECT 'ACTUAL'::text AS kind, group_id, (state = 'OPEN') AS is_open
      FROM smalllive_handoffs ORDER BY first_live_fill_at DESC LIMIT 500),
pos AS (SELECT * FROM paper UNION ALL SELECT * FROM actual),
latest AS (
    SELECT DISTINCT ON (group_id, position_kind) group_id, position_kind, assessed_at
      FROM xavier_management_assessments
     WHERE group_id IN (SELECT group_id FROM pos)
     ORDER BY group_id, position_kind, assessed_at DESC, assessment_id DESC),
j AS (
    SELECT p.kind, p.is_open, l.assessed_at,
           CASE WHEN p.kind = 'PAPER' THEN c.paper_s ELSE c.actual_s END AS cad_s
      FROM pos p CROSS JOIN cad c
      LEFT JOIN latest l ON l.group_id = p.group_id AND l.position_kind = p.kind)
SELECT coalesce(kind, 'ALL') AS kind,
       count(*) AS positions_read,
       count(*) FILTER (WHERE is_open) AS open_positions,
       count(*) FILTER (WHERE is_open AND assessed_at IS NULL) AS open_without_review,
       count(*) FILTER (WHERE is_open AND assessed_at IS NOT NULL
                        AND now() > assessed_at + make_interval(secs => 2 * cad_s)) AS reviews_overdue,
       count(*) FILTER (WHERE is_open AND assessed_at IS NOT NULL
                        AND now() <= assessed_at + make_interval(secs => 2 * cad_s)) AS monitored,
       round(count(*) FILTER (WHERE is_open AND assessed_at IS NOT NULL
                        AND now() <= assessed_at + make_interval(secs => 2 * cad_s))::numeric
             / nullif(count(*) FILTER (WHERE is_open), 0), 6) AS positions_monitored_share
  FROM j GROUP BY ROLLUP (kind) ORDER BY 1;
