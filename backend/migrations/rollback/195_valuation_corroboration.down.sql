-- Rollback of 195. The corroboration rows are evidence for PinnAPI valuations
-- that qualified the outcome-depth floor: export them before dropping. Roll
-- back the code first -- the collector writes this table in the same
-- transaction as every PinnAPI valuation and refuses to persist without it.
DROP TABLE IF EXISTS valuation_corroboration;
