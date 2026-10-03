-- Rolls back migration 208. Every table here is SHADOW output of
-- sportsassets/intel (no order, limit, ledger or production probability
-- reads it), so dropping it removes only the shadow layer's history.
-- The runner checks to_regclass and idles when the tables are absent.
DROP TABLE IF EXISTS intel_regime_states;
DROP TABLE IF EXISTS intel_audrey_risk_checks;
DROP TABLE IF EXISTS intel_allocations;
DROP TABLE IF EXISTS intel_sizing;
DROP TABLE IF EXISTS intel_attribution;
-- the overlay's freeze trigger is dropped with its table
DROP TABLE IF EXISTS intel_calibration_overlays;
DROP FUNCTION IF EXISTS intel_overlay_frozen();
DROP TABLE IF EXISTS intel_calibration_segments;
DROP TABLE IF EXISTS intel_snapshots;
DROP TABLE IF EXISTS intel_runs;
