-- Candidate 22 follow-up readback (read-only): the K9 intent, NCAAF funnel, alert pairs.
\echo '== F1 · live-eligible intents without LIVE_ADMISSIBLE (detail) =='
SELECT intent_id, strategy, created_at, actual_state, actual_refusal,
       live_eligibility->'admission'->>'verdict' AS admission,
       (SELECT applied_at FROM schema_migrations WHERE version LIKE '200%') AS m200_applied_at
  FROM execution_intents
 WHERE live_eligible AND coalesce(live_eligibility->'admission'->>'verdict', '') <> 'LIVE_ADMISSIBLE';
\echo '== F2 · NCAAF coverage rows (all tz/days) =='
SELECT tz, day, provider_events, normalized_events, venue_discovered, mapped_events,
       settlement_supported, evaluated_events, decided_events, refused_events, computed_at
  FROM coverage_funnel_snapshots WHERE league ILIKE '%ncaaf%' ORDER BY computed_at DESC LIMIT 6;
\echo '== F3 · alerts by tz/day/league =='
SELECT tz, day, league, kind, stage_to, count(*) FROM coverage_collapse_alerts GROUP BY 1,2,3,4,5 ORDER BY 2 DESC LIMIT 12;
\echo '== F4 · settlement_supported definition inputs: decisions by settlement refusal (2 h) =='
SELECT strategy, verdict, refusal, count(*) FROM paper_decisions
 WHERE decided_at > now() - interval '2 hours' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 12;
\echo '== F5 · Xavier stale assessments: why (latest per group) =='
SELECT evidence_state, probability_source, count(*) AS n,
       round(min(probability_age_s)::numeric,0) AS min_age, round(max(probability_age_s)::numeric,0) AS max_age
  FROM xavier_management_assessments WHERE assessed_at > now() - interval '30 minutes'
 GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10;
