-- READ-ONLY. RC6.3c lane QL-1 (rc6/quiet-line-evidence) readback for the C1
-- decision: of the 34 events the software-reds audit named -- the 33 of the
-- 38 QUOTE_STALE_ON_ARRIVAL first-loss events of the approved-judge packet
-- window (run 38055963121's list, re-used verbatim by rc63_sw_reds_stale38.sql)
-- whose last packet-window row carries FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE
-- or FEED_QUOTE_OLDER_THAN_LIMIT beside the arrival code, plus the one event
-- whose first loss IS FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE (North Texas v
-- Charlotte, 2168132b20b82bf78e08f1787b6f83cc) -- how many, per hour over the
-- last 24 h, carry the lane's quiet-line evidence code saying the held-read
-- rule WOULD have admitted the price (QUIET_LINE_WOULD_PASS_ON_CONFIRMATION)
-- against those saying it would not. BEFORE the lane is deployed every
-- evidence count is 0 by construction: the rows carry only the refusal, and the
-- first two columns of each hour are the denominator the deployed lane fills.
-- SELECT only; the 38 ids are the audit's own list.

\echo == Q0 the 34 events: 33 of the 38 whose last packet-window row carries a feed freshness refusal beside the arrival code, plus the AGE_UNKNOWN first loss
WITH stale38 AS (
  SELECT unnest(ARRAY['21a7dc0df1a77eb87cc985e844095cf0','234d9123fdc010716a5a844efaf7ab43','2876b41c9c5327639fda141d7027eadb','310dd8851a99203930a45881b0156d36','3350fb5023fd4b47b86d34fcc5f47c17','373204ebaf557738961c85f37a72fe10','3ad908e99942c191b72d855b6f1b508f','3e691b53c660a64495a44816768176e8','416ce0307b7b9d97f5365a29c1729339','43457c08aa50a7e0038aa06eb3437680','43dda5e509a9619bee41f752a2c25fa7','4462f1fa168ab2a5a25fe2807ebec8ac','59f8c98dee8ce77cc17c5b4958e4f325','5a88faaeb5fcbb11e975498eca213c27','5d7901b0ad44ffe1b92e18fc8d511a7e','6050a18b694eb1d661ee7fa571b7ddbb','616eba68e65fe65023aa2335e81096c8','690e2edaba34c383d2ebfa8f8394f973','6c0ea3fe653989dd0d695482738232c3','6e4d435e9afc8a6db1162d2057d74acb','7cc5cec6dac13487c15b70d0f4411ca6','7dd6e0d9f2657c834974c28b286ff58d','80fbdc19285ed9bab284269273562eb9','82b83ec87fc2be5faa2e9f26ac50edef','837b4ddbdcd5e8e3a68a7c954208ff40','92f26a40bba178c47bbade8c9af0ada3','9383cbbbb905d9ee50a3341f6945eae7','979fefd620ed804180ef3fed873d61c2','9e243e91f8f94e6431da5cc5c3cc8fda','61a93fcf57aba065a24baba199c80e70','6625fc9c60426ff3097656d9e604a5e3','b4f0c2295ca3c4421a157ab112c057aa','9ea179b94f97b5ab62469564ac823b7c','a32e431d88dc9e94017ef329c927cf0b','ca3575b85cfe58c71464e24d24a819ef','e1ea9930c10116a19a87a37cfb048757','e923e7caf339e76d705060afb4b5606e','fbed9e8f16fe7532998d46a4276e706c']) AS provider_event_id),
last_row AS (
  SELECT DISTINCT ON (o.provider_event_id) o.provider_event_id, o.sport_key, o.codes ->> 1 AS beside
    FROM ext_candidate_outcomes o JOIN stale38 s USING (provider_event_id)
   WHERE o.cycle_at >= to_timestamp(1791622474.8284767) AND o.cycle_at < to_timestamp(1791626075.8284767)
   ORDER BY o.provider_event_id, o.cycle_at DESC, o.id DESC),
the34 AS (
  SELECT provider_event_id FROM last_row
   WHERE beside IN ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 'FEED_QUOTE_OLDER_THAN_LIMIT')
  UNION SELECT '2168132b20b82bf78e08f1787b6f83cc')
SELECT (SELECT count(*) FROM last_row) AS of_the_38_with_a_window_row,
       (SELECT count(*) FROM last_row WHERE beside IN ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 'FEED_QUOTE_OLDER_THAN_LIMIT')) AS the_33,
       (SELECT count(*) FROM the34) AS the_34;

\echo == Q1 the 34 events per hour, last 24 h: rows, rows with a feed freshness refusal recorded (first refusal or beside), rows carrying the QL-1 evidence code by its answer (events in brackets)
WITH stale38 AS (
  SELECT unnest(ARRAY['21a7dc0df1a77eb87cc985e844095cf0','234d9123fdc010716a5a844efaf7ab43','2876b41c9c5327639fda141d7027eadb','310dd8851a99203930a45881b0156d36','3350fb5023fd4b47b86d34fcc5f47c17','373204ebaf557738961c85f37a72fe10','3ad908e99942c191b72d855b6f1b508f','3e691b53c660a64495a44816768176e8','416ce0307b7b9d97f5365a29c1729339','43457c08aa50a7e0038aa06eb3437680','43dda5e509a9619bee41f752a2c25fa7','4462f1fa168ab2a5a25fe2807ebec8ac','59f8c98dee8ce77cc17c5b4958e4f325','5a88faaeb5fcbb11e975498eca213c27','5d7901b0ad44ffe1b92e18fc8d511a7e','6050a18b694eb1d661ee7fa571b7ddbb','616eba68e65fe65023aa2335e81096c8','690e2edaba34c383d2ebfa8f8394f973','6c0ea3fe653989dd0d695482738232c3','6e4d435e9afc8a6db1162d2057d74acb','7cc5cec6dac13487c15b70d0f4411ca6','7dd6e0d9f2657c834974c28b286ff58d','80fbdc19285ed9bab284269273562eb9','82b83ec87fc2be5faa2e9f26ac50edef','837b4ddbdcd5e8e3a68a7c954208ff40','92f26a40bba178c47bbade8c9af0ada3','9383cbbbb905d9ee50a3341f6945eae7','979fefd620ed804180ef3fed873d61c2','9e243e91f8f94e6431da5cc5c3cc8fda','61a93fcf57aba065a24baba199c80e70','6625fc9c60426ff3097656d9e604a5e3','b4f0c2295ca3c4421a157ab112c057aa','9ea179b94f97b5ab62469564ac823b7c','a32e431d88dc9e94017ef329c927cf0b','ca3575b85cfe58c71464e24d24a819ef','e1ea9930c10116a19a87a37cfb048757','e923e7caf339e76d705060afb4b5606e','fbed9e8f16fe7532998d46a4276e706c']) AS provider_event_id),
last_row AS (
  SELECT DISTINCT ON (o.provider_event_id) o.provider_event_id, o.codes ->> 1 AS beside
    FROM ext_candidate_outcomes o JOIN stale38 s USING (provider_event_id)
   WHERE o.cycle_at >= to_timestamp(1791622474.8284767) AND o.cycle_at < to_timestamp(1791626075.8284767)
   ORDER BY o.provider_event_id, o.cycle_at DESC, o.id DESC),
the34 AS (
  SELECT provider_event_id FROM last_row
   WHERE beside IN ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 'FEED_QUOTE_OLDER_THAN_LIMIT')
  UNION SELECT '2168132b20b82bf78e08f1787b6f83cc'),
r AS (
  SELECT o.id, date_trunc('hour', o.cycle_at) AS hr, o.provider_event_id,
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(o.codes) c
                  WHERE c IN ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 'FEED_QUOTE_OLDER_THAN_LIMIT',
                              'WS_REFERENCE_NOT_USABLE:FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE',
                              'WS_REFERENCE_NOT_USABLE:FEED_QUOTE_OLDER_THAN_LIMIT')) AS freshness_refused,
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(o.codes) c
                  WHERE split_part(c, ':', 1) = 'QUIET_LINE_WOULD_PASS_ON_CONFIRMATION') AS would_pass,
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(o.codes) c
                  WHERE split_part(c, ':', 1) = 'QUIET_LINE_WOULD_NOT_PASS_ON_CONFIRMATION') AS would_not_pass
    FROM ext_candidate_outcomes o JOIN the34 USING (provider_event_id)
   WHERE o.cycle_at >= now() - interval '24 hours')
SELECT hr, count(*) AS rows_of_the_34, count(DISTINCT provider_event_id) AS events,
       count(*) FILTER (WHERE freshness_refused) AS freshness_refused_rows,
       count(DISTINCT provider_event_id) FILTER (WHERE freshness_refused) AS freshness_refused_events,
       count(*) FILTER (WHERE would_pass) AS would_pass_rows,
       count(DISTINCT provider_event_id) FILTER (WHERE would_pass) AS would_pass_events,
       count(*) FILTER (WHERE would_not_pass) AS would_not_pass_rows,
       count(DISTINCT provider_event_id) FILTER (WHERE would_not_pass) AS would_not_pass_events
  FROM r GROUP BY 1 ORDER BY 1 LIMIT 30;

\echo == Q2 every event, per hour, last 24 h: rows with a feed freshness refusal recorded and rows carrying the QL-1 evidence code (the denominator the deployed lane fills; evidence 0 before deploy)
WITH r AS (
  SELECT o.id, date_trunc('hour', o.cycle_at) AS hr, o.provider_event_id,
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(o.codes) c
                  WHERE c IN ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 'FEED_QUOTE_OLDER_THAN_LIMIT',
                              'WS_REFERENCE_NOT_USABLE:FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE',
                              'WS_REFERENCE_NOT_USABLE:FEED_QUOTE_OLDER_THAN_LIMIT')) AS freshness_refused,
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(o.codes) c
                  WHERE split_part(c, ':', 1) IN ('QUIET_LINE_WOULD_PASS_ON_CONFIRMATION', 'QUIET_LINE_WOULD_NOT_PASS_ON_CONFIRMATION')) AS has_evidence,
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(o.codes) c
                  WHERE split_part(c, ':', 1) = 'QUIET_LINE_WOULD_PASS_ON_CONFIRMATION') AS would_pass
    FROM ext_candidate_outcomes o
   WHERE o.cycle_at >= now() - interval '24 hours')
SELECT hr, count(*) AS rows_all, count(*) FILTER (WHERE freshness_refused) AS freshness_refused_rows,
       count(DISTINCT provider_event_id) FILTER (WHERE freshness_refused) AS freshness_refused_events,
       count(*) FILTER (WHERE has_evidence) AS evidence_rows,
       count(*) FILTER (WHERE would_pass) AS would_pass_rows,
       count(DISTINCT provider_event_id) FILTER (WHERE would_pass) AS would_pass_events
  FROM r GROUP BY 1 ORDER BY 1 LIMIT 30;

\echo == Q3 the evidence detail where present (post-deploy): no_observed_change_s and age_since_confirmation_s off the code, by answer, last 24 h
WITH c AS (
  SELECT o.cycle_at, o.provider_event_id, c AS code, split_part(c, ':', 1) AS head,
         nullif(split_part(split_part(c, 'no_observed_change_s=', 2), ';', 1), 'none')::float8 AS no_observed_change_s,
         nullif(split_part(split_part(c, 'age_since_confirmation_s=', 2), ';', 1), 'none')::float8 AS age_since_confirmation_s
    FROM ext_candidate_outcomes o, jsonb_array_elements_text(o.codes) c
   WHERE o.cycle_at >= now() - interval '24 hours'
     AND split_part(c, ':', 1) IN ('QUIET_LINE_WOULD_PASS_ON_CONFIRMATION', 'QUIET_LINE_WOULD_NOT_PASS_ON_CONFIRMATION'))
SELECT head, count(*) AS rows_with_evidence, count(DISTINCT provider_event_id) AS events,
       round(min(no_observed_change_s)::numeric, 1) AS min_no_change_s, round(percentile_cont(0.5) WITHIN GROUP (ORDER BY no_observed_change_s)::numeric, 1) AS p50_no_change_s, round(max(no_observed_change_s)::numeric, 1) AS max_no_change_s,
       round(min(age_since_confirmation_s)::numeric, 1) AS min_conf_age_s, round(percentile_cont(0.5) WITHIN GROUP (ORDER BY age_since_confirmation_s)::numeric, 1) AS p50_conf_age_s, round(max(age_since_confirmation_s)::numeric, 1) AS max_conf_age_s,
       count(*) FILTER (WHERE age_since_confirmation_s IS NULL) AS never_confirmed_rows
  FROM c GROUP BY 1 ORDER BY 1 LIMIT 5;

\echo == Q4 context, the feed owner heartbeat: the cache-wide counterfactual the census already measures (never a decision input) and the held reads admitted on confirmation
SELECT value->>'beat_at' AS beat_at, value->>'state' AS state,
       value->'cache'->>'markets' AS markets, value->'cache'->>'markets_age_unknown' AS markets_age_unknown,
       value->'cache'->>'markets_unconfirmed' AS markets_unconfirmed, value->'cache'->>'fresh_now' AS fresh_now_by_change,
       value->'cache'->'fresh_now_if_measured_from_confirmation'->>'markets' AS fresh_now_if_measured_from_confirmation,
       value->'cache'->'counts'->>'held_reads_admitted_on_provider_confirmation' AS held_reads_admitted_on_provider_confirmation,
       value->'cache'->'confirmations' AS confirmations_by_kind
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == Q5 context, the collector heartbeat: the last cycle quiet_line counters (present only once the lane is deployed)
SELECT key, jsonb_typeof(value::jsonb) AS t, value::jsonb->'latency'->'quiet_line' AS quiet_line, value::jsonb->>'started_at' AS started_at
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
