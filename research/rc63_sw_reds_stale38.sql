-- READ-ONLY. RC6.3b software-reds audit part 7: the exact 38 QUOTE_STALE_ON_ARRIVAL first-loss events of the
-- packet census window (provider_event_id list taken from the offline replay of run 38055963121), their
-- last-cycle ledger row by lag class and by the PinnAPI refusal beside the code. SELECT only.
\echo == H1 the 38 events: last row in the window, lag class and ws refusal beside
WITH r AS (
  SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key, codes, provider_lag_s, our_processing_s, quote_age_s, queue_position, cycle_at
    FROM ext_candidate_outcomes
   WHERE cycle_at >= to_timestamp(1791622474.8284767) AND cycle_at < to_timestamp(1791626075.8284767)
     AND provider_event_id IN ('21a7dc0df1a77eb87cc985e844095cf0','234d9123fdc010716a5a844efaf7ab43','2876b41c9c5327639fda141d7027eadb','310dd8851a99203930a45881b0156d36','3350fb5023fd4b47b86d34fcc5f47c17','373204ebaf557738961c85f37a72fe10','3ad908e99942c191b72d855b6f1b508f','3e691b53c660a64495a44816768176e8','416ce0307b7b9d97f5365a29c1729339','43457c08aa50a7e0038aa06eb3437680','43dda5e509a9619bee41f752a2c25fa7','4462f1fa168ab2a5a25fe2807ebec8ac','59f8c98dee8ce77cc17c5b4958e4f325','5a88faaeb5fcbb11e975498eca213c27','5d7901b0ad44ffe1b92e18fc8d511a7e','6050a18b694eb1d661ee7fa571b7ddbb','616eba68e65fe65023aa2335e81096c8','690e2edaba34c383d2ebfa8f8394f973','6c0ea3fe653989dd0d695482738232c3','6e4d435e9afc8a6db1162d2057d74acb','7cc5cec6dac13487c15b70d0f4411ca6','7dd6e0d9f2657c834974c28b286ff58d','80fbdc19285ed9bab284269273562eb9','82b83ec87fc2be5faa2e9f26ac50edef','837b4ddbdcd5e8e3a68a7c954208ff40','92f26a40bba178c47bbade8c9af0ada3','9383cbbbb905d9ee50a3341f6945eae7','979fefd620ed804180ef3fed873d61c2','9e243e91f8f94e6431da5cc5c3cc8fda','61a93fcf57aba065a24baba199c80e70','6625fc9c60426ff3097656d9e604a5e3','b4f0c2295ca3c4421a157ab112c057aa','9ea179b94f97b5ab62469564ac823b7c','a32e431d88dc9e94017ef329c927cf0b','ca3575b85cfe58c71464e24d24a819ef','e1ea9930c10116a19a87a37cfb048757','e923e7caf339e76d705060afb4b5606e','fbed9e8f16fe7532998d46a4276e706c')
   ORDER BY provider_event_id, cycle_at DESC, id DESC)
SELECT CASE WHEN provider_lag_s IS NULL THEN 'LAG_UNMEASURED' WHEN provider_lag_s > 30 THEN 'PROVIDER_LAG_GT_30' ELSE 'DELIVERED_INSIDE_30' END AS lag_class,
       codes ->> 1 AS ws_refusal_beside, sport_key, count(*) AS events,
       round(avg(provider_lag_s)::numeric, 1) AS avg_provider_lag_s, round(avg(our_processing_s)::numeric, 1) AS avg_our_processing_s,
       round(avg(quote_age_s)::numeric, 1) AS avg_quote_age_s, min(queue_position) AS min_q, max(queue_position) AS max_q
  FROM r GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;
\echo == H2 the same 38 events by lag class only
WITH r AS (
  SELECT DISTINCT ON (provider_event_id) provider_event_id, provider_lag_s
    FROM ext_candidate_outcomes
   WHERE cycle_at >= to_timestamp(1791622474.8284767) AND cycle_at < to_timestamp(1791626075.8284767)
     AND provider_event_id IN ('21a7dc0df1a77eb87cc985e844095cf0','234d9123fdc010716a5a844efaf7ab43','2876b41c9c5327639fda141d7027eadb','310dd8851a99203930a45881b0156d36','3350fb5023fd4b47b86d34fcc5f47c17','373204ebaf557738961c85f37a72fe10','3ad908e99942c191b72d855b6f1b508f','3e691b53c660a64495a44816768176e8','416ce0307b7b9d97f5365a29c1729339','43457c08aa50a7e0038aa06eb3437680','43dda5e509a9619bee41f752a2c25fa7','4462f1fa168ab2a5a25fe2807ebec8ac','59f8c98dee8ce77cc17c5b4958e4f325','5a88faaeb5fcbb11e975498eca213c27','5d7901b0ad44ffe1b92e18fc8d511a7e','6050a18b694eb1d661ee7fa571b7ddbb','616eba68e65fe65023aa2335e81096c8','690e2edaba34c383d2ebfa8f8394f973','6c0ea3fe653989dd0d695482738232c3','6e4d435e9afc8a6db1162d2057d74acb','7cc5cec6dac13487c15b70d0f4411ca6','7dd6e0d9f2657c834974c28b286ff58d','80fbdc19285ed9bab284269273562eb9','82b83ec87fc2be5faa2e9f26ac50edef','837b4ddbdcd5e8e3a68a7c954208ff40','92f26a40bba178c47bbade8c9af0ada3','9383cbbbb905d9ee50a3341f6945eae7','979fefd620ed804180ef3fed873d61c2','9e243e91f8f94e6431da5cc5c3cc8fda','61a93fcf57aba065a24baba199c80e70','6625fc9c60426ff3097656d9e604a5e3','b4f0c2295ca3c4421a157ab112c057aa','9ea179b94f97b5ab62469564ac823b7c','a32e431d88dc9e94017ef329c927cf0b','ca3575b85cfe58c71464e24d24a819ef','e1ea9930c10116a19a87a37cfb048757','e923e7caf339e76d705060afb4b5606e','fbed9e8f16fe7532998d46a4276e706c')
   ORDER BY provider_event_id, cycle_at DESC, id DESC)
SELECT CASE WHEN provider_lag_s IS NULL THEN 'LAG_UNMEASURED' WHEN provider_lag_s > 30 THEN 'PROVIDER_LAG_GT_30' ELSE 'DELIVERED_INSIDE_30' END AS lag_class, count(*) AS events FROM r GROUP BY 1 ORDER BY 2 DESC LIMIT 5;
