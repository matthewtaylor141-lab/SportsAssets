-- READ-ONLY. NCAA samples from the native discovery digest. SELECT only.
SELECT k, left(v::text, 2500) FROM ingestion_state,
       jsonb_each(coalesce(value->'native_discovery'->'receipt_sample', '{}')) AS e(k, v)
 WHERE key = 'pinnapi_feed_last';
SELECT left((value->'native_discovery'->'venue_events_without_a_fixture')::text, 3000)
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT event_slug, min(team_name) a, max(team_name) b, min(game_start)
  FROM us_premap WHERE team_league = 'cfb' AND sports_type = 'football_team_full_game_winner'
   AND game_start > now() AND game_start < now() + interval '96 hours'
 GROUP BY 1 ORDER BY 4 LIMIT 120;
