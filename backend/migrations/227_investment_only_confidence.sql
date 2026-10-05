-- ══════════════════════════════════════════════════════════════════════
-- 227 · PRODUCTION CONFIDENCE IS INVESTMENT-ONLY: EVERY PERSISTED METRIC,
--       FORECAST AND FORECAST SCORE NAMES ITS BOOK, SLEEVE, STRATEGY AND
--       POLICY VERSIONS (RESEARCH / SHADOW, NO CAPITAL AUTHORITY)
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (owner audit 2026-10-04, P0 #5). Migration 223 gave every
-- paper position group ONE durable sleeve (INVESTMENT / TRAINING /
-- BENCHMARK / UNCLASSIFIED), but the Profitability OS's top-level numbers
-- were still computed per BOOK: the hourly runner wrote ONE north-star
-- metric row per (book, metric) -- every paper strategy pooled -- and ONE
-- 30-day forecast per (book, day); the lost-opportunity horizon forecasts
-- were per (book, horizon, day). A TRAINING (exploration) loss or win, or a
-- BENCHMARK control arm's, therefore moved the very statistics a live-capital
-- decision would read. The schema could not even say which sleeve a row
-- measured: pos_forecasts allowed one forecast per book per day.
--
-- THE FIX. Each of those rows now carries its SCOPE:
--
--   sleeve              INVESTMENT | TRAINING | BENCHMARK | UNCLASSIFIED
--                       (the durable classification of migration 223; a
--                       position without one is UNCLASSIFIED, never
--                       INVESTMENT)
--   strategy            one strategy of that sleeve, or 'ALL' (every
--                       strategy of the sleeve)
--   policy_versions     the deciding policy versions in the sample
--   classifier_version  the sleeve classifier version the scope was read at
--   confidence_scope    PRODUCTION_CONFIDENCE (INVESTMENT only -- CHECKed)
--                       or RESEARCH_NOT_PRODUCTION_CONFIDENCE
--
-- and the per-day uniqueness of a forecast is per (book, sleeve, strategy,
-- day) [per horizon for the horizon forecasts], so the TRAINING and BENCHMARK
-- sleeves stay visible -- separately -- without ever entering an INVESTMENT
-- row.
--
-- ROWS WRITTEN BEFORE THIS MIGRATION (book-wide, every strategy pooled) keep
-- sleeve NULL: that is exactly what they measured, and the append-only
-- tables are never rewritten. Every NEW row must name its scope: the
-- `*_scope_named_ck` constraints are added NOT VALID, so Postgres enforces
-- them on every insert from now on and does not apply them to the history.
-- A reader treats a NULL-sleeve row as BOOK_WIDE_PRE_227 -- never as
-- INVESTMENT. A forecast score copies its forecast's scope (NULL for a
-- pre-227 forecast, which is still scored against the whole book it
-- forecast).
--
-- DEPLOY ORDER. Between this migration and the new code, the previous
-- runner's book-wide inserts are refused by the NOT VALID checks; its
-- components record FAILED (failure-isolated, research only) until the new
-- code runs. Nothing else reads or writes these tables.
--
-- NO AUTHORITY. Nothing in an order, sizing, limit, threshold, gate,
-- allowlist or capital path reads these tables (pinned by
-- tests/test_profitability_is_research_only.py and
-- tests/test_investment_only_confidence.py). No row is updated or deleted.
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

-- ── 1 · THE NORTH-STAR METRICS ───────────────────────────────────────
ALTER TABLE pos_metric_observations ADD COLUMN IF NOT EXISTS sleeve text;
ALTER TABLE pos_metric_observations ADD COLUMN IF NOT EXISTS strategy text;
ALTER TABLE pos_metric_observations
    ADD COLUMN IF NOT EXISTS policy_versions text[];
ALTER TABLE pos_metric_observations
    ADD COLUMN IF NOT EXISTS classifier_version text;
ALTER TABLE pos_metric_observations
    ADD COLUMN IF NOT EXISTS confidence_scope text;

-- ── 2 · THE MONTHLY REVENUE FORECASTS AND THEIR SCORES ───────────────
ALTER TABLE pos_forecasts ADD COLUMN IF NOT EXISTS sleeve text;
ALTER TABLE pos_forecasts ADD COLUMN IF NOT EXISTS strategy text;
ALTER TABLE pos_forecasts ADD COLUMN IF NOT EXISTS policy_versions text[];
ALTER TABLE pos_forecasts ADD COLUMN IF NOT EXISTS classifier_version text;
ALTER TABLE pos_forecasts ADD COLUMN IF NOT EXISTS confidence_scope text;
ALTER TABLE pos_forecast_scores ADD COLUMN IF NOT EXISTS sleeve text;
ALTER TABLE pos_forecast_scores ADD COLUMN IF NOT EXISTS strategy text;
ALTER TABLE pos_forecast_scores ADD COLUMN IF NOT EXISTS confidence_scope text;

-- ── 3 · THE 24H / 7D / 30D HORIZON FORECASTS (migration 220) ─────────
ALTER TABLE lol_horizon_forecasts ADD COLUMN IF NOT EXISTS sleeve text;
ALTER TABLE lol_horizon_forecasts ADD COLUMN IF NOT EXISTS strategy text;
ALTER TABLE lol_horizon_forecasts
    ADD COLUMN IF NOT EXISTS policy_versions text[];
ALTER TABLE lol_horizon_forecasts
    ADD COLUMN IF NOT EXISTS classifier_version text;
ALTER TABLE lol_horizon_forecasts
    ADD COLUMN IF NOT EXISTS confidence_scope text;
ALTER TABLE lol_horizon_forecast_scores ADD COLUMN IF NOT EXISTS sleeve text;
ALTER TABLE lol_horizon_forecast_scores
    ADD COLUMN IF NOT EXISTS strategy text;
ALTER TABLE lol_horizon_forecast_scores
    ADD COLUMN IF NOT EXISTS confidence_scope text;

-- ── THE INVARIANTS (CHECKs), added once ──────────────────────────────
DO $$
DECLARE
    t text;
    pfx text;
BEGIN
    FOREACH t IN ARRAY ARRAY['pos_metric_observations', 'pos_forecasts',
                             'pos_forecast_scores', 'lol_horizon_forecasts',
                             'lol_horizon_forecast_scores'] LOOP
        pfx := CASE t WHEN 'pos_metric_observations' THEN 'pos_metric'
                      WHEN 'pos_forecasts' THEN 'pos_fc'
                      WHEN 'pos_forecast_scores' THEN 'pos_fcs'
                      WHEN 'lol_horizon_forecasts' THEN 'lol_hfc'
                      ELSE 'lol_hfcs' END;
        -- a named sleeve is one of migration 223's four
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = pfx || '_sleeve_ck') THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I CHECK (sleeve IS NULL OR '
                'sleeve IN (''INVESTMENT'', ''TRAINING'', ''BENCHMARK'', '
                '''UNCLASSIFIED''))', t, pfx || '_sleeve_ck');
        END IF;
        -- the scope label is one of two
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = pfx || '_confidence_scope_ck') THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I CHECK (confidence_scope IS '
                'NULL OR confidence_scope IN (''PRODUCTION_CONFIDENCE'', '
                '''RESEARCH_NOT_PRODUCTION_CONFIDENCE''))', t,
                pfx || '_confidence_scope_ck');
        END IF;
        -- PRODUCTION CONFIDENCE IS INVESTMENT-ONLY: a TRAINING, BENCHMARK or
        -- UNCLASSIFIED row can never be labelled production confidence
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = pfx || '_production_is_investment_ck')
        THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I CHECK (confidence_scope IS '
                'DISTINCT FROM ''PRODUCTION_CONFIDENCE'' OR sleeve = '
                '''INVESTMENT'')', t, pfx || '_production_is_investment_ck');
        END IF;
    END LOOP;
    -- EVERY NEW metric / forecast row names its scope (NOT VALID: enforced
    -- on every insert from now on; the pre-227 book-wide history keeps its
    -- NULL, which is what it measured)
    FOREACH t IN ARRAY ARRAY['pos_metric_observations', 'pos_forecasts',
                             'lol_horizon_forecasts'] LOOP
        pfx := CASE t WHEN 'pos_metric_observations' THEN 'pos_metric'
                      WHEN 'pos_forecasts' THEN 'pos_fc'
                      ELSE 'lol_hfc' END;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = pfx || '_scope_named_ck') THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I CHECK (sleeve IS NOT NULL '
                'AND strategy IS NOT NULL AND policy_versions IS NOT NULL '
                'AND confidence_scope IS NOT NULL) NOT VALID', t,
                pfx || '_scope_named_ck');
        END IF;
    END LOOP;
END $$;

-- ── ONE FORECAST PER SCOPE PER DAY (was: one per book per day) ───────
-- Pre-227 rows (sleeve NULL) never collide with a scoped row: NULLs are
-- distinct in a UNIQUE constraint, and no new NULL-sleeve row can be written.
ALTER TABLE pos_forecasts DROP CONSTRAINT IF EXISTS pos_fc_one_per_day;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pos_fc_one_per_day_per_scope') THEN
        ALTER TABLE pos_forecasts ADD CONSTRAINT pos_fc_one_per_day_per_scope
            UNIQUE (book, sleeve, strategy, issued_day);
    END IF;
END $$;
ALTER TABLE lol_horizon_forecasts DROP CONSTRAINT IF EXISTS lol_hfc_one_per_day;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'lol_hfc_one_per_day_per_scope') THEN
        ALTER TABLE lol_horizon_forecasts
            ADD CONSTRAINT lol_hfc_one_per_day_per_scope
            UNIQUE (book, sleeve, strategy, horizon, issued_day);
    END IF;
END $$;

-- ── THE READS BY SCOPE ───────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS pos_metric_scope_at_idx
    ON pos_metric_observations (book, sleeve, strategy, metric,
                                computed_at DESC);
CREATE INDEX IF NOT EXISTS pos_fc_scope_at_idx
    ON pos_forecasts (book, sleeve, strategy, issued_at DESC);
CREATE INDEX IF NOT EXISTS lol_hfc_scope_at_idx
    ON lol_horizon_forecasts (book, sleeve, strategy, horizon,
                              issued_at DESC);
