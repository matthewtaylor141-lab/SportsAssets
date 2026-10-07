-- 315 rollback: the red-team receipts (no ledger, no authority depends on
-- them). The two alias / route columns are dropped with their tables only
-- by 314's own rollback; here they are removed explicitly.
ALTER TABLE IF EXISTS canonical_route_receipts DROP COLUMN IF EXISTS fee_evidence;
ALTER TABLE IF EXISTS canonical_claim_aliases DROP COLUMN IF EXISTS certificate_status;
ALTER TABLE IF EXISTS canonical_claim_aliases DROP COLUMN IF EXISTS rules_sha256;
DROP TABLE IF EXISTS red_team_holdout_registry;
DROP TABLE IF EXISTS red_team_kalshi_series_fees;
DROP TABLE IF EXISTS red_team_settlement_certificates;
DROP TABLE IF EXISTS red_team_control_receipts;
DROP TABLE IF EXISTS red_team_release_receipts;
DROP TABLE IF EXISTS red_team_claim_exposure_receipts;
DROP TABLE IF EXISTS red_team_pair_execution_receipts;
DROP TABLE IF EXISTS red_team_profit_breaker_receipts;
DROP TABLE IF EXISTS red_team_readiness_receipts;
DROP FUNCTION IF EXISTS red_team_append_only();
