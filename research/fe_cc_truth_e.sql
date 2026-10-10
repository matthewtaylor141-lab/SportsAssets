-- READ ONLY. Frontend root-cause audit (group frontend), part E: whether the
-- live game score display (migration 316) has ever recorded anything.
-- SELECT statements only.
\echo == E1. score display health rows ==
SELECT worker, heartbeat_at, now() - heartbeat_at AS hb_age, left(payload::text, 400) AS payload
  FROM trader_display_score_health;

\echo == E2. observation and binding row counts ==
SELECT (SELECT count(*) FROM trader_display_score_observations) AS observations,
       (SELECT count(*) FROM trader_display_score_bindings) AS bindings;
