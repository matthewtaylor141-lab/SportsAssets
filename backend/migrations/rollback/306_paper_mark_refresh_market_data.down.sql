-- Rollback of 306: drops the per-source and telemetry columns of
-- paper_mark_refresh_runs. The runs themselves (270) are kept.
ALTER TABLE paper_mark_refresh_runs
    DROP COLUMN IF EXISTS market_data,
    DROP COLUMN IF EXISTS sources,
    DROP COLUMN IF EXISTS cooldown_waited_s,
    DROP COLUMN IF EXISTS institutional_books,
    DROP COLUMN IF EXISTS stream_books,
    DROP COLUMN IF EXISTS skipped_cooldown;
