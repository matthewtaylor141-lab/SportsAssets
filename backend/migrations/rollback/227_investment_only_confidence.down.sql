-- Rollback of 227: removes the scope columns (sleeve, strategy, policy
-- versions, classifier version, confidence scope) from the north-star
-- metrics, the forecasts, the horizon forecasts and their scores, and puts
-- back the one-forecast-per-book-per-day uniqueness. No order, sizing, limit
-- or capital path reads these tables.
--
-- REFUSES while ANY scoped row exists: a sleeve-scoped metric, forecast or
-- horizon forecast is the record of what the INVESTMENT (or TRAINING /
-- BENCHMARK) sleeve measured or forecast at the time. Dropping the scope
-- would pool those rows back into one ambiguous book-wide history -- the
-- very contamination 227 removed -- and the forecasts must stay scoreable
-- against their own sleeve. Pre-227 (book-wide, sleeve NULL) rows do not
-- block it. Every statement is guarded, so the file applies cleanly on a
-- database where 216 / 220 or 227 itself is absent.
DO $$
DECLARE
    t text;
    hit integer;
BEGIN
    FOREACH t IN ARRAY ARRAY['pos_metric_observations', 'pos_forecasts',
                             'lol_horizon_forecasts'] LOOP
        IF to_regclass(t) IS NOT NULL AND EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_name = t AND column_name = 'sleeve') THEN
            -- (EXECUTE does not set FOUND: read the probe INTO a variable)
            hit := NULL;
            EXECUTE format('SELECT 1 FROM %I WHERE sleeve IS NOT NULL '
                           'LIMIT 1', t) INTO hit;
            IF hit IS NOT NULL THEN
                RAISE EXCEPTION '% holds sleeve-scoped rows; rollback refused',
                    t;
            END IF;
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    t text;
    pfx text;
    c text;
BEGIN
    FOREACH t IN ARRAY ARRAY['pos_metric_observations', 'pos_forecasts',
                             'pos_forecast_scores', 'lol_horizon_forecasts',
                             'lol_horizon_forecast_scores'] LOOP
        IF to_regclass(t) IS NULL THEN
            CONTINUE;
        END IF;
        pfx := CASE t WHEN 'pos_metric_observations' THEN 'pos_metric'
                      WHEN 'pos_forecasts' THEN 'pos_fc'
                      WHEN 'pos_forecast_scores' THEN 'pos_fcs'
                      WHEN 'lol_horizon_forecasts' THEN 'lol_hfc'
                      ELSE 'lol_hfcs' END;
        FOREACH c IN ARRAY ARRAY['_sleeve_ck', '_confidence_scope_ck',
                                 '_production_is_investment_ck',
                                 '_scope_named_ck'] LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', t,
                           pfx || c);
        END LOOP;
    END LOOP;
END $$;

DROP INDEX IF EXISTS pos_metric_scope_at_idx;
DROP INDEX IF EXISTS pos_fc_scope_at_idx;
DROP INDEX IF EXISTS lol_hfc_scope_at_idx;

DO $$
BEGIN
    IF to_regclass('pos_forecasts') IS NOT NULL THEN
        ALTER TABLE pos_forecasts
            DROP CONSTRAINT IF EXISTS pos_fc_one_per_day_per_scope;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'pos_fc_one_per_day') THEN
            ALTER TABLE pos_forecasts ADD CONSTRAINT pos_fc_one_per_day
                UNIQUE (book, issued_day);
        END IF;
    END IF;
    IF to_regclass('lol_horizon_forecasts') IS NOT NULL THEN
        ALTER TABLE lol_horizon_forecasts
            DROP CONSTRAINT IF EXISTS lol_hfc_one_per_day_per_scope;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'lol_hfc_one_per_day') THEN
            ALTER TABLE lol_horizon_forecasts ADD CONSTRAINT lol_hfc_one_per_day
                UNIQUE (book, horizon, issued_day);
        END IF;
    END IF;
END $$;

DO $$
DECLARE
    t text;
    c text;
BEGIN
    FOREACH t IN ARRAY ARRAY['pos_metric_observations', 'pos_forecasts',
                             'lol_horizon_forecasts'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            FOREACH c IN ARRAY ARRAY['sleeve', 'strategy', 'policy_versions',
                                     'classifier_version',
                                     'confidence_scope'] LOOP
                EXECUTE format('ALTER TABLE %I DROP COLUMN IF EXISTS %I', t,
                               c);
            END LOOP;
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['pos_forecast_scores',
                             'lol_horizon_forecast_scores'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            FOREACH c IN ARRAY ARRAY['sleeve', 'strategy',
                                     'confidence_scope'] LOOP
                EXECUTE format('ALTER TABLE %I DROP COLUMN IF EXISTS %I', t,
                               c);
            END LOOP;
        END IF;
    END LOOP;
END $$;
