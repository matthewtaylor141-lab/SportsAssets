-- 306: THE HELD-MARK REFRESH RECORDS WHERE EACH MARK CAME FROM (PAPER ONLY).
--
-- agents/paper_mark_refresh already computed skipped_cooldown, stream_books
-- and cooldown_waited_s on every run and returned them, but 270's table had
-- no column for them, so the durable record lost them. P0 market-data
-- freshness (2026-10-06) adds the source order institutional stream ->
-- retail stream -> harvest -> paced REST through one paper market-data owner
-- (sportsassets/paper_market_data), and the run now also records how many
-- held marks each source supplied and the owner's telemetry at the end of
-- the run (requests / min, 2xx, 429, cache hits, coalesced reads, queue
-- depth, stream update counts).
--
-- Additive, nullable-free with defaults; the append-only trigger of 270 is
-- unchanged (the run's single completion UPDATE writes these). No
-- threshold, SLA, limit, key, live switch or funded control is touched.

ALTER TABLE paper_mark_refresh_runs
    ADD COLUMN IF NOT EXISTS skipped_cooldown    integer          NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stream_books        integer          NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS institutional_books integer          NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cooldown_waited_s   double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sources             jsonb            NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS market_data         jsonb            NOT NULL DEFAULT '{}'::jsonb;
