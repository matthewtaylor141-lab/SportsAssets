-- READ-ONLY. RC6.3b software-reds CRITIC readback 1. For the exact 38 QUOTE_STALE_ON_ARRIVAL first-loss events of the
-- packet window: how much slack the metered provider left (30 s limit minus provider lag) in their best delivered-inside-30
-- cycle, against the processing time our queue actually took, and (K2/K3) what a delivered-inside-30 row that WAS priced
-- looks like, by queue band and by slack band, over the last 6 h. Settles whether "cut receipt-to-decision under about
-- 10 s" can clear the 38. SELECT only.
\echo == K1 the 38 events: best delivered-inside-30 cycle per event, by sport and slack band
WITH r AS (
  SELECT provider_event_id, sport_key, cycle_at, provider_lag_s, our_processing_s, quote_age_s, queue_position
    FROM ext_candidate_outcomes
   WHERE cycle_at >= to_timestamp(1791622474.8284767) AND cycle_at < to_timestamp(1791626075.8284767)
     AND provider_event_id IN ('21a7dc0df1a77eb87cc985e844095cf0','234d9123fdc010716a5a844efaf7ab43','2876b41c9c5327639fda141d7027eadb','310dd8851a99203930a45881b0156d36','3350fb5023fd4b47b86d34fcc5f47c17','373204ebaf557738961c85f37a72fe10','3ad908e99942c191b72d855b6f1b508f','3e691b53c660a64495a44816768176e8','416ce0307b7b9d97f5365a29c1729339','43457c08aa50a7e0038aa06eb3437680','43dda5e509a9619bee41f752a2c25fa7','4462f1fa168ab2a5a25fe2807ebec8ac','59f8c98dee8ce77cc17c5b4958e4f325','5a88faaeb5fcbb11e975498eca213c27','5d7901b0ad44ffe1b92e18fc8d511a7e','6050a18b694eb1d661ee7fa571b7ddbb','616eba68e65fe65023aa2335e81096c8','690e2edaba34c383d2ebfa8f8394f973','6c0ea3fe653989dd0d695482738232c3','6e4d435e9afc8a6db1162d2057d74acb','7cc5cec6dac13487c15b70d0f4411ca6','7dd6e0d9f2657c834974c28b286ff58d','80fbdc19285ed9bab284269273562eb9','82b83ec87fc2be5faa2e9f26ac50edef','837b4ddbdcd5e8e3a68a7c954208ff40','92f26a40bba178c47bbade8c9af0ada3','9383cbbbb905d9ee50a3341f6945eae7','979fefd620ed804180ef3fed873d61c2','9e243e91f8f94e6431da5cc5c3cc8fda','61a93fcf57aba065a24baba199c80e70','6625fc9c60426ff3097656d9e604a5e3','b4f0c2295ca3c4421a157ab112c057aa','9ea179b94f97b5ab62469564ac823b7c','a32e431d88dc9e94017ef329c927cf0b','ca3575b85cfe58c71464e24d24a819ef','e1ea9930c10116a19a87a37cfb048757','e923e7caf339e76d705060afb4b5606e','fbed9e8f16fe7532998d46a4276e706c')
     AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
     AND provider_lag_s IS NOT NULL AND provider_lag_s <= 30),
b AS (
  SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key, provider_lag_s, our_processing_s, quote_age_s, queue_position,
         30 - provider_lag_s AS slack_s
    FROM r ORDER BY provider_event_id, provider_lag_s ASC, cycle_at DESC)
SELECT sport_key,
       CASE WHEN slack_s < 5 THEN 'a_lt_5' WHEN slack_s < 8 THEN 'b_5_8' WHEN slack_s < 10 THEN 'c_8_10' WHEN slack_s < 13 THEN 'd_10_13' ELSE 'e_ge_13' END AS slack_band,
       count(*) AS events, round(avg(provider_lag_s)::numeric, 1) AS avg_lag_s, round(avg(our_processing_s)::numeric, 1) AS avg_proc_s,
       round(avg(queue_position)::numeric, 1) AS avg_q, min(queue_position) AS min_q, max(queue_position) AS max_q
  FROM b GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 40;

\echo == K2 delivered-inside-30 rows, last 6 h: queue band against outcome (priced = ADMITTED or ALREADY_RECORDED)
SELECT CASE WHEN queue_position < 3 THEN 'a_q0_2' WHEN queue_position < 6 THEN 'b_q3_5' WHEN queue_position < 11 THEN 'c_q6_10'
            WHEN queue_position < 21 THEN 'd_q11_20' WHEN queue_position < 31 THEN 'e_q21_30' ELSE 'f_q31_plus' END AS queue_band,
       count(*) AS rows_inside_30,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS rows_stale,
       count(*) FILTER (WHERE outcome IN ('ADMITTED', 'ALREADY_RECORDED')) AS rows_priced,
       round(avg(our_processing_s)::numeric, 1) AS avg_proc_all_s,
       round((avg(our_processing_s) FILTER (WHERE outcome IN ('ADMITTED', 'ALREADY_RECORDED')))::numeric, 1) AS avg_proc_priced_s,
       round((max(our_processing_s) FILTER (WHERE outcome IN ('ADMITTED', 'ALREADY_RECORDED')))::numeric, 1) AS max_proc_priced_s,
       round(min(our_processing_s)::numeric, 1) AS min_proc_s
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '6 hours' AND provider_lag_s IS NOT NULL AND provider_lag_s <= 30 AND our_processing_s IS NOT NULL
   AND us_market_slug IS NOT NULL
 GROUP BY 1 ORDER BY 1 LIMIT 10;

\echo == K3 delivered-inside-30 stale rows, last 6 h, by slack band (30 minus provider lag): how much of it a fast queue could ever clear
SELECT CASE WHEN provider_lag_s > 25 THEN 'a_slack_lt_5' WHEN provider_lag_s > 22 THEN 'b_slack_5_8' WHEN provider_lag_s > 20 THEN 'c_slack_8_10'
            WHEN provider_lag_s > 17 THEN 'd_slack_10_13' ELSE 'e_slack_ge_13' END AS slack_band,
       count(*) AS stale_rows, count(DISTINCT provider_event_id) AS stale_events,
       round(avg(our_processing_s)::numeric, 1) AS avg_proc_s, round(avg(queue_position)::numeric, 1) AS avg_q
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '6 hours' AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL' AND provider_lag_s IS NOT NULL AND provider_lag_s <= 30
 GROUP BY 1 ORDER BY 1 LIMIT 10;
