-- cand22: coverage_integrity's NCAAF funnel rows (read-only). Requires
-- migration 209 (coverage_funnel_snapshots) in production. League identity:
-- americanfootball_ncaaf = venue token cfb = "NCAAF".
\echo '== CI1 · NCAAF coverage_funnel_snapshots, newest days =='
SELECT tz, day, league, provider_events, normalized_events, venue_discovered,
       mapped_events, settlement_supported, evaluated_events, decided_events,
       entered_events, refused_events, ordered_events, filled_events,
       venue_catalogue_events, computed_at
  FROM coverage_funnel_snapshots
 WHERE league = 'americanfootball_ncaaf'
 ORDER BY day DESC, computed_at DESC LIMIT 5;
