-- cand24: what happens to football (NCAAF) events after identity, in production (read-only)
\echo '== F1 · ncaaf ledger rows, last 6 h, by outcome / stage / refusal =='
SELECT outcome, stage, first_refusal, (us_market_slug IS NOT NULL) AS has_slug,
       count(*) AS rows, count(DISTINCT provider_event_id) AS events, max(cycle_at) AS last
  FROM ext_candidate_outcomes
 WHERE sport_key = 'americanfootball_ncaaf' AND cycle_at > now() - interval '6 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 30;
\echo '== F2 · football valuations ever since 2026-10-03 18:00Z =='
SELECT sport_family, record_purpose, count(*) AS n, min(decided_at) AS first, max(decided_at) AS last,
       (array_agg(us_market_slug ORDER BY decided_at DESC))[1:4] AS slugs,
       (array_agg(array_to_string(refusals, ',') ORDER BY decided_at DESC))[1:2] AS refusals
  FROM external_valuations
 WHERE decided_at > timestamptz '2026-10-03 18:00Z' AND sport_family = 'football'
 GROUP BY 1, 2;
\echo '== F3 · latest ncaaf rows with codes (mapped ones) =='
SELECT cycle_at, provider_event_id, home, away, commence_time, us_market_slug, stage, outcome,
       first_refusal, left(codes::text, 300) AS codes
  FROM ext_candidate_outcomes
 WHERE sport_key = 'americanfootball_ncaaf' AND us_market_slug IS NOT NULL
 ORDER BY cycle_at DESC LIMIT 12;
\echo '== F4 · collector heartbeat: calibration-only counters and venue errors =='
SELECT value->'valuations_recorded_inadmissible_for_calibration' AS cal_only,
       left((value->'calibration_only')::text, 600) AS cal_only_detail,
       left((value->'venue_errors')::text, 1200) AS venue_errors,
       left((value->'tally')::text, 800) AS tally
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
