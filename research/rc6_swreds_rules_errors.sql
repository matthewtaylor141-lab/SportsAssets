-- READ-ONLY. RC6 lane C (software reds): which venue rules-text answers the
-- valuations carried since 2026-10-08 16:00Z -- the text, the venue saying it
-- lists / publishes nothing, or a transport failure -- and whether that
-- answer came from the hourly cache (a cached transport failure is a failure
-- re-served for an hour). SELECT only.

\echo E1 rules answers by error and cache flag
SELECT coalesce(v.settlement_comparison->>'venue_rules_error', 'NONE') AS rules_error,
       v.settlement_comparison->>'venue_rules_from_cache' AS from_cache,
       v.settlement_comparison->>'venue_rules_read' AS text_read,
       count(*) AS valuations, count(DISTINCT v.us_market_slug) AS slugs,
       min(v.decided_at) AS first_at, max(v.decided_at) AS last_at
  FROM external_valuations v
 WHERE v.decided_at >= timestamptz '2026-10-08 16:00:00+00'
 GROUP BY 1, 2, 3 ORDER BY valuations DESC
 LIMIT 40;
