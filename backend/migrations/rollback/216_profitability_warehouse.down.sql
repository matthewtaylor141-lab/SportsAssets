-- Rollback of 216: drops the profitability research warehouse. No order,
-- sizing or limit path reads these tables, so nothing else changes.
-- REFUSES while a forecast exists: forecasts are persisted to be scored
-- against what happened, and dropping them would erase the forward record
-- that decides whether the revenue engine is ever more than UNPROVEN.
-- Lineage, economics, capacity, metrics and snapshots are re-derivable
-- from the source records and do not block the rollback.
DO $$
BEGIN
    IF to_regclass('pos_forecasts') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pos_forecasts) THEN
        RAISE EXCEPTION 'pos_forecasts holds forecasts; rollback refused';
    END IF;
END $$;
DROP VIEW IF EXISTS pos_paper_chain_v;
DROP VIEW IF EXISTS pos_warehouse;
DROP VIEW IF EXISTS pos_capacity_latest;
DROP VIEW IF EXISTS pos_economics_latest;
DROP VIEW IF EXISTS pos_lineage_latest;
DROP TABLE IF EXISTS pos_forecast_scores;
DROP TABLE IF EXISTS pos_forecasts;
DROP TABLE IF EXISTS pos_snapshots;
DROP TABLE IF EXISTS pos_metric_observations;
DROP TABLE IF EXISTS pos_capacity;
DROP TABLE IF EXISTS pos_position_economics;
DROP TABLE IF EXISTS pos_lineage;
DROP TABLE IF EXISTS pos_runs;
DROP FUNCTION IF EXISTS pos_record_is_append_only();
