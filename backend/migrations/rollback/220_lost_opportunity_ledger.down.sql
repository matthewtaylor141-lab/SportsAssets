-- Rollback of 220: drops the lost opportunity ledger and the opportunity
-- scores. No order, sizing or limit path reads these tables, so nothing
-- else changes. REFUSES while the ledger holds a classification: the
-- ledger is the append-only record of what each classifier version said
-- at the time, and dropping it would erase that audit trail. The
-- opportunity scores are re-derivable and do not block the rollback; the
-- horizon forecasts are the forward record that is scored, and block it.
DO $$
BEGIN
    IF to_regclass('lol_ledger') IS NOT NULL
       AND EXISTS (SELECT 1 FROM lol_ledger) THEN
        RAISE EXCEPTION 'lol_ledger holds classifications; rollback refused';
    END IF;
    IF to_regclass('lol_horizon_forecasts') IS NOT NULL
       AND EXISTS (SELECT 1 FROM lol_horizon_forecasts) THEN
        RAISE EXCEPTION 'lol_horizon_forecasts holds forecasts; rollback refused';
    END IF;
END $$;
DROP VIEW IF EXISTS lol_opportunity_scores_latest;
DROP TABLE IF EXISTS lol_horizon_forecast_scores;
DROP TABLE IF EXISTS lol_horizon_forecasts;
DROP TABLE IF EXISTS lol_opportunity_scores;
DROP TABLE IF EXISTS lol_ledger;
DROP TABLE IF EXISTS lol_runs;
DROP FUNCTION IF EXISTS lol_record_is_append_only();
