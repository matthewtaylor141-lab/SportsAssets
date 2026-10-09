-- READ-ONLY. RC6 lane api-responsive: the production SIZES of the work the
-- API loop watchdog named (2026-10-09 ring), so the responsiveness harness
-- runs at representative scale rather than at a guess. Counts only: no row
-- content is printed.

\echo W1 coverage census: catalogue rows the census classifies (us_premap re-seen within 3,900 s)
SELECT count(DISTINCT market_slug) AS catalogue_market_slugs
  FROM us_premap
 WHERE market_slug IS NOT NULL
   AND updated_at > now() - interval '3900 seconds';

\echo W2 blocker census: refused entries since the capital-authority cutover (migration 305)
SELECT r.account_id, count(*) AS refused_entries,
       count(DISTINCT (r.us_market_slug, r.holding_side)) AS contract_sides
  FROM paper_entry_refusal_census r
 WHERE r.refused_at >= coalesce((SELECT min(applied_at) FROM schema_migrations
                                  WHERE version LIKE '305\_%'), now())
 GROUP BY 1 ORDER BY 2 DESC LIMIT 5;

\echo W3 research model training set: labelled research observations (all, and named by the latest candidate)
SELECT count(*) AS labelled_research_observations
  FROM derek_research_observations o
  JOIN external_valuations v ON v.id = o.valuation_id
 WHERE v.record_purpose = o.record_purpose
   AND v.outcome_known AND v.outcome IN (0, 1)
   AND v.outcome_at IS NOT NULL;
SELECT model_id, state,
       jsonb_array_length(coalesce(training_provenance->'decision_ids', '[]'::jsonb)) AS named_records,
       length(training_provenance::text) AS provenance_chars,
       length(coalesce(training_provenance->'records', '[]'::jsonb)::text) AS records_chars,
       created_at
  FROM bettor_funded_models
 WHERE training_provenance->>'source' IS NOT NULL
 ORDER BY created_at DESC LIMIT 5;

\echo W4 the PinnAPI cache and its snapshot: the latest feed heartbeat census (counts only)
SELECT service, status, beat_at,
       detail #>> '{cache,events}' AS cache_events,
       detail #>> '{cache,markets}' AS cache_markets,
       detail #>> '{owner,cache,events}' AS owner_cache_events,
       detail #>> '{owner,cache,markets}' AS owner_cache_markets
  FROM service_heartbeats
 WHERE service ILIKE '%pinnapi%' ORDER BY beat_at DESC LIMIT 5;

\echo W5 desk board: the latest desk receipt is in-process only; the sweep log line carries it (render-ops)

\echo W6 the plane heartbeat memory section (cycle boundaries are what RC6 adds)
SELECT service, status, beat_at,
       detail #> '{memory,rss_mb}' AS rss_mb,
       detail #> '{memory,peak_mb}' AS peak_mb,
       detail #> '{refdata,complete}' AS refdata_complete,
       detail #>> '{populate,full}' AS populate_full
  FROM service_heartbeats
 WHERE service IN ('universal_market_plane', 'kalshi_ws_market_data')
 ORDER BY service;
