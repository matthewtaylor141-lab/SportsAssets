-- READ-ONLY. Follow-up to derek_entry_lane_investigation.sql (2026-10-01).
-- F1 aec-mlb-bal-nyy-2026-09-27: each row's stored settlement read
-- F2 the 09-24 rows with no buy_intent/ladder_side: how many ENTRY rows lack the payout side, by day
-- F3 the global catalogue's view of BAL-NYY 09-27 (closed/resolved)
-- F4 ENTRY_DECISION admissibility over the whole run (were any ever admitted?)

\echo '== F1 bal-nyy 09-27 rows =='
SELECT id, decided_at, probability IS NOT NULL AS has_pinnacle, buy_intent, ladder_side,
       outcome_known, outcome_basis, outcome_side_map, settlement_read, settlement_read_at
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND us_market_slug = 'aec-mlb-bal-nyy-2026-09-27'
 ORDER BY decided_at;

\echo '== F2 valuations without a payout side (buy_intent or ladder_side null), by day and purpose =='
SELECT date_trunc('day', decided_at)::date AS day, record_purpose, count(*) AS rows,
       count(*) FILTER (WHERE buy_intent IS NULL OR ladder_side IS NULL) AS no_side,
       count(*) FILTER (WHERE (buy_intent IS NULL OR ladder_side IS NULL) AND NOT outcome_known) AS no_side_unlabelled
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== F3 global catalogue rows for BAL-NYY 2026-09-27 =='
SELECT slug, closed, resolved, sport, updated_at, left(title, 80) AS title
  FROM markets
 WHERE slug LIKE 'mlb-bal-nyy-2026-09-27%'
 ORDER BY slug LIMIT 10;

\echo '== F4 ENTRY_DECISION admissibility by day =='
SELECT date_trunc('day', decided_at)::date AS day, count(*) AS rows,
       count(*) FILTER (WHERE admissible) AS admissible
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND record_purpose = 'ENTRY_DECISION'
 GROUP BY 1 ORDER BY 1;
