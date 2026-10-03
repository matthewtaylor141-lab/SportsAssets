-- cand22 NCAAF post-deploy proof (read-only). Re-run after claude/cand22-ncaaf
-- is deployed to the workers. Proves the venue's `cfb` board reaches the
-- provider fetch, identity, valuation and a RECORDED paper decision (ENTER or
-- a named REFUSE), and that every NCAAF provider event has a ledger row.
-- Window: the last 24 h (UTC) plus the America/New_York day of the read.
\echo '== P0 · read instant, serving build =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et,
       (SELECT value->'writer' FROM ingestion_state
         WHERE key = 'ext_pinnacle_last_cycle') AS writer_build;

\echo '== P1 · last cycle: requested keys, football board, NCAAF funnel =='
SELECT to_timestamp((value->>'at')::float8) AS at,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'venue_football_board' AS venue_football_board,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       (SELECT jsonb_agg(r) FROM jsonb_array_elements(
          coalesce(value->'sports_selection'->'rejected', '[]'::jsonb)) r
         WHERE r->>'key' = 'americanfootball_ncaaf') AS ncaaf_rejected,
       value->'funnel_by_provider_sport'->'americanfootball_ncaaf' AS ncaaf_funnel
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== P2 · NCAAF provider events per cycle: ledger rows by stage / outcome / first refusal (24 h) =='
SELECT stage, outcome, first_refusal, count(*) AS n,
       count(DISTINCT provider_event_id) AS events,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS with_venue_contract,
       (array_agg(DISTINCT home || ' v ' || away))[1:4] AS examples
  FROM ext_candidate_outcomes
 WHERE sport_key = 'americanfootball_ncaaf'
   AND cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo '== P3 · football valuations (24 h) =='
SELECT provider, record_purpose, admissible, count(*) AS n,
       count(DISTINCT us_market_slug) AS contracts,
       (array_agg(DISTINCT us_market_slug))[1:5] AS examples,
       (SELECT jsonb_object_agg(code, c) FROM (
          SELECT unnest(v2.refusals) AS code, count(*) AS c
            FROM external_valuations v2
           WHERE v2.sport_family = 'football'
             AND v2.decided_at > now() - interval '24 hours'
           GROUP BY 1 ORDER BY 2 DESC LIMIT 12) x) AS refusal_counts
  FROM external_valuations
 WHERE sport_family = 'football' AND decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3;

\echo '== P4 · cfb paper decisions by strategy / verdict / refusal (24 h) =='
SELECT strategy, verdict, refusal, count(*) AS n,
       (array_agg(DISTINCT us_market_slug))[1:4] AS examples
  FROM paper_decisions
 WHERE split_part(us_market_slug, '-', 2) = 'cfb'
   AND decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo '== P5 · every football valuation has a Derek decision (24 h): gap must be 0 =='
SELECT count(*) AS football_valuations,
       count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM paper_decisions d WHERE d.valuation_id = v.id
            AND d.strategy = 'DEREK_ENTRY_POLICY_V2')) AS without_derek_decision
  FROM external_valuations v
 WHERE v.sport_family = 'football' AND v.decided_at > now() - interval '24 hours'
   AND v.decided_at < now() - interval '5 minutes';

\echo '== P6 · cfb paper orders / fills / execution intents (24 h) =='
SELECT 'orders' AS stage, count(*) FROM paper_orders
 WHERE split_part(us_market_slug, '-', 2) = 'cfb' AND created_at > now() - interval '24 hours'
UNION ALL
SELECT 'fills', count(*) FROM paper_fills
 WHERE split_part(us_market_slug, '-', 2) = 'cfb' AND filled_at > now() - interval '24 hours'
UNION ALL
SELECT 'execution_intents', count(*) FROM execution_intents
 WHERE split_part(us_market_slug, '-', 2) = 'cfb' AND created_at > now() - interval '24 hours';
