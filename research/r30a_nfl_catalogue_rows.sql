-- R30A NFL STREAM (review fixes), read-only. The venue catalogue rows of the
-- current NFL money-line contracts EXACTLY as persisted, so the census's
-- family test (pinnapi_census.family_of) can be run on the production object
-- itself. The previous read (r30a_nfl_wording_and_catalogue_shape.sql, W2)
-- found every NFL row's `line` NOT blank (28 rows / 14 contracts); this shows
-- what the line is and the question it was stamped from. No secrets: venue
-- catalogue text only.
\echo '== R0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== R1 · NFL catalogue rows, the census columns (all current rows) =='
SELECT identifier, side_norm, event_slug, event_title, kind, team_name,
       team_league, question, signed, line, sports_type,
       extract(epoch FROM game_start)::float8 AS game_start
  FROM us_premap
 WHERE market_slug LIKE 'aec-nfl-%' AND game_start > now() - interval '6 hours'
 ORDER BY game_start, identifier, side_norm;
