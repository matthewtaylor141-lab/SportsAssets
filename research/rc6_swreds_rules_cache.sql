-- READ-ONLY. RC6 lane C (software reds): how much of a candidate's decision
-- lag (book read -> decision) is the venue rules read. Every calibration-only
-- valuation since 2026-10-08 16:00Z by sport family and whether its venue
-- rules text came from the hourly cache (settlement_comparison
-- venue_rules_from_cache, persisted on the row), with the decision lag the
-- record carries. SELECT only.

\echo L1 decision lag by family and rules-cache hit
SELECT v.sport_family,
       v.settlement_comparison->>'venue_rules_from_cache' AS rules_from_cache,
       count(*) AS n,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY (v.calibration_only_evidence->>'decision_lag_s')::float8)::numeric, 2) AS dlag_p50,
       round(percentile_disc(0.25) WITHIN GROUP (ORDER BY (v.calibration_only_evidence->>'decision_lag_s')::float8)::numeric, 2) AS dlag_p25,
       round(percentile_disc(0.75) WITHIN GROUP (ORDER BY (v.calibration_only_evidence->>'decision_lag_s')::float8)::numeric, 2) AS dlag_p75
  FROM external_valuations v
 WHERE v.decided_at >= timestamptz '2026-10-08 16:00:00+00'
   AND v.record_purpose = 'CALIBRATION_ONLY'
   AND (v.calibration_only_evidence->>'no_book_read') IS NULL
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo L2 NCAAF slugs: how long since the rules text was read when the candidate decided
SELECT round(percentile_disc(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM v.decided_at) - (v.settlement_comparison->>'venue_rules_retrieved_at')::float8)::numeric, 0) AS rules_age_p50,
       count(*) FILTER (WHERE v.settlement_comparison->>'venue_rules_from_cache' = 'false') AS fresh_reads,
       count(*) FILTER (WHERE v.settlement_comparison->>'venue_rules_from_cache' = 'true') AS cached,
       count(*) AS n
  FROM external_valuations v
 WHERE v.decided_at >= timestamptz '2026-10-08 16:00:00+00'
   AND v.record_purpose = 'CALIBRATION_ONLY'
   AND v.us_market_slug LIKE 'aec-cfb-%';
