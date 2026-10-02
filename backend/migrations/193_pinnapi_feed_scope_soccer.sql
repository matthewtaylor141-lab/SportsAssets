-- 193 · PINNAPI FEED SCOPE: BASEBALL (6) AND SOCCER (1)
--
-- Xavier's held positions now read the in-process PinnAPI feed when the
-- 900 s valuation is stale (paper_benchmark.xavier_measure, 30 s limit
-- unchanged). 10 of the 17 open paper positions on 2026-10-02 were soccer,
-- which the coverage census reported OUT_OF_FEED_SCOPE_SPORT under the
-- default scope [6]. The owner reads this row once at start, so it applies
-- from the next process start (this deploy).
--
-- Inserted ONLY when no scope row exists: an operator's own scope is never
-- overwritten. Bounds stay the cache's (MAX_EVENTS 4000, MAX_MARKETS
-- 120000, oldest-touched eviction, counted in the heartbeat). Paper data
-- input only: no order, threshold or control is read from here.
INSERT INTO ingestion_state (key, value)
VALUES ('pinnapi_feed_scope',
        '{"sport_ids": [6, 1], "streams": ["live", "prematch"],
          "why": "migration 193: soccer added for held paper positions"}'::jsonb)
ON CONFLICT (key) DO NOTHING;
