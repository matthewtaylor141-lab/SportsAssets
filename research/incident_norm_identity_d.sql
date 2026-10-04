-- P0 INCIDENT (2026-10-04) · segment: NORMALIZATION + EVENT IDENTITY, fourth read.
-- READ-ONLY, bounded to 72 h. Does the coverage funnel's "normalized" gap measure
-- normalization at all? coverage_integrity.REACH_SQL ranks a row by its `stage`
-- column; this shows which refusals arrive with stage NULL (reach 0) and what
-- the provider->normalized gap per sport is actually made of.

\echo '== D1 · ledger rows 72 h by first_refusal and stage (NULL stage = reach 0 in coverage_integrity) =='
SELECT sport_key, coalesce(first_refusal, outcome) AS first_refusal, coalesce(stage, 'NULL') AS stage,
       (us_market_slug IS NOT NULL) AS has_slug, count(*) AS rows, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '72 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC LIMIT 120;

\echo '== D2 · per sport, 72 h: events the funnel counts as NOT normalized (max reach < 3), split by their precise cause =='
WITH r AS (
  SELECT sport_key, provider_event_id, first_refusal, codes, mapped_by,
         CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
              WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
                   greatest(substr(stage, 1, 1)::int,
                            CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
              WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END AS reach
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '72 hours' AND provider_event_id IS NOT NULL
), e AS (
  SELECT sport_key, provider_event_id, max(reach) AS reach,
         bool_and(codes ? 'NO_PINNACLE_ON_EVENT') AS never_priced,
         bool_or(codes::text LIKE '%VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM%') AS one_team,
         bool_or(codes::text LIKE '%NO_VENUE_NATIVE_EVENT_FOR_FIXTURE%') AS vn_no_event,
         bool_or(first_refusal = 'VENUE_CONTRACT_IS_A_SEGMENT_NOT_FULL_GAME') AS global_segment,
         bool_or(mapped_by IS NOT NULL) AS ever_mapped
    FROM r GROUP BY 1, 2
)
SELECT sport_key, count(*) AS provider_events,
       count(*) FILTER (WHERE reach >= 3) AS normalized_by_funnel,
       count(*) FILTER (WHERE reach < 3) AS not_normalized_by_funnel,
       count(*) FILTER (WHERE reach < 3 AND never_priced) AS of_which_never_priced_no_pinnacle,
       count(*) FILTER (WHERE reach < 3 AND NOT never_priced AND one_team) AS of_which_participant_mismatch,
       count(*) FILTER (WHERE reach < 3 AND NOT never_priced AND NOT one_team AND vn_no_event) AS of_which_vn_no_event,
       count(*) FILTER (WHERE reach < 3 AND NOT never_priced AND global_segment) AS of_which_global_segment_block,
       count(*) FILTER (WHERE reach < 3 AND NOT never_priced AND NOT one_team AND NOT vn_no_event
                          AND NOT global_segment) AS of_which_other,
       count(*) FILTER (WHERE ever_mapped AND reach < 3) AS mapped_but_reach_lt3
  FROM e GROUP BY 1 ORDER BY 2 DESC;
