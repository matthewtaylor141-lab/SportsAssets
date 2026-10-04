-- LAB-A EDGE DECAY: sizing of the qualified-opportunity set and of the
-- recorded evidence after each decision (read-only, bounded to 7 days).
-- Nothing here is a result; it sizes what the per-opportunity extraction
-- (lab_a_edge_decay_extract*.sql) will find.
\echo == 1 INVESTMENT-sleeve decisions by strategy / policy / verdict, 7 d (with a book read)
SELECT strategy, policy_version, verdict, count(*) AS n,
       count(book_obs_id) AS with_book, min(decided_at) AS first_at,
       max(decided_at) AS last_at
  FROM paper_decisions
 WHERE decided_at > now() - interval '7 days'
   AND strategy IN ('PINNACLE_COMPLETED_GAME_PAPER', 'DEREK_ENTRY_POLICY_V2')
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == 2 refusal sets of decisions that READ a book (CG), 7 d, with best-level gross edge quartiles
SELECT refusals, count(*) AS n,
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP
         (ORDER BY (economics->>'best_level_edge_pp')::float8) AS edge_pp_q,
       count(*) FILTER (WHERE (economics->>'best_level_edge_pp')::float8 > 0)
         AS positive_gross_edge
  FROM paper_decisions
 WHERE decided_at > now() - interval '7 days'
   AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND book_obs_id IS NOT NULL
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

\echo == 3 later book observations of the same market after each CG decision with a book (7 d), by gap bucket
WITH d AS (
  SELECT decision_id, verdict, us_market_slug, decided_at
    FROM paper_decisions
   WHERE decided_at > now() - interval '7 days'
     AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND book_obs_id IS NOT NULL
     AND (verdict = 'ENTER' OR refusals <@ ARRAY['BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
          'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY']::text[])
   LIMIT 5000)
SELECT d.verdict,
       count(DISTINCT d.decision_id) AS decisions,
       count(b.obs_id) FILTER (WHERE b.observed_at <= d.decided_at + interval '1 second') AS b_le_1s,
       count(b.obs_id) FILTER (WHERE b.observed_at <= d.decided_at + interval '2 seconds') AS b_le_2s,
       count(b.obs_id) FILTER (WHERE b.observed_at <= d.decided_at + interval '5 seconds') AS b_le_5s,
       count(b.obs_id) FILTER (WHERE b.observed_at <= d.decided_at + interval '10 seconds') AS b_le_10s,
       count(b.obs_id) FILTER (WHERE b.observed_at <= d.decided_at + interval '30 seconds') AS b_le_30s,
       count(b.obs_id) FILTER (WHERE b.observed_at <= d.decided_at + interval '60 seconds') AS b_le_60s,
       count(b.obs_id) FILTER (WHERE b.observed_at <= d.decided_at + interval '300 seconds') AS b_le_300s,
       count(b.obs_id) AS b_le_600s
  FROM d LEFT JOIN paper_book_observations b
    ON b.us_market_slug = d.us_market_slug
   AND b.observed_at > d.decided_at
   AND b.observed_at <= d.decided_at + interval '600 seconds'
   AND b.error IS NULL
 GROUP BY 1 ORDER BY 1;

\echo == 4 decisions with at least one later book within N seconds (share of decisions)
WITH d AS (
  SELECT decision_id, verdict, us_market_slug, decided_at
    FROM paper_decisions
   WHERE decided_at > now() - interval '7 days'
     AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND book_obs_id IS NOT NULL
     AND (verdict = 'ENTER' OR refusals <@ ARRAY['BELOW_MIN_GROSS_EDGE',
          'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
          'NET_EV_NOT_POSITIVE_AFTER_FEES', 'NO_SIZED_QUANTITY']::text[])
   LIMIT 5000),
f AS (
  SELECT d.decision_id, d.verdict,
         (SELECT min(extract(epoch FROM b.observed_at - d.decided_at))
            FROM paper_book_observations b
           WHERE b.us_market_slug = d.us_market_slug AND b.error IS NULL
             AND b.observed_at > d.decided_at
             AND b.observed_at <= d.decided_at + interval '600 seconds') AS first_gap_s,
         (SELECT count(*) FROM external_valuations v
           WHERE v.us_market_slug = d.us_market_slug
             AND v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
             AND v.decided_at > d.decided_at
             AND v.decided_at <= d.decided_at + interval '60 seconds') AS vals_60s
    FROM d)
SELECT verdict, count(*) AS n,
       count(*) FILTER (WHERE first_gap_s IS NOT NULL) AS any_book_600s,
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY first_gap_s) AS first_gap_q,
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY vals_60s) AS valuations_60s_q
  FROM f GROUP BY 1 ORDER BY 1;

\echo == 5 the recorded latency chain of CG decisions (pinnacle json), 7 d, quartiles in seconds
SELECT verdict, count(*) AS n,
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP
         (ORDER BY (pinnacle->>'received_at')::float8 - (pinnacle->>'at')::float8) AS provider_to_receipt_q,
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP
         (ORDER BY (pinnacle->>'decision_lag_after_valuation_s')::float8) AS valuation_to_decision_q,
       percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP
         (ORDER BY (pinnacle->>'age_s')::float8) AS provider_to_decision_q,
       count(*) FILTER (WHERE pinnacle->>'decided_via' IS NOT NULL) AS with_via
  FROM paper_decisions
 WHERE decided_at > now() - interval '7 days'
   AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND book_obs_id IS NOT NULL
 GROUP BY 1 ORDER BY 1;

\echo == 6 decided_via split (CG, book read, 7 d)
SELECT pinnacle->>'decided_via' AS via, verdict, count(*)
  FROM paper_decisions
 WHERE decided_at > now() - interval '7 days'
   AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND book_obs_id IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 7 evaluation attempt elapsed (CG, decided, 7 d), quartiles in seconds
SELECT via, verdict, count(*) AS n,
       percentile_cont(ARRAY[0.25, 0.5, 0.75, 0.9]) WITHIN GROUP (ORDER BY elapsed_s) AS elapsed_q
  FROM paper_evaluation_attempts
 WHERE at > now() - interval '7 days'
   AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND outcome = 'DECIDED'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 8 R30 objects present in production (225/226 not deployed per the brief)
SELECT to_regclass('canonical_decision_intents') AS cdi,
       to_regclass('canonical_intent_executions') AS cie,
       to_regclass('eddie_execution_estimates') AS eddie,
       to_regclass('karen_challenges') AS karen,
       to_regclass('intel_allocations') AS allie;
