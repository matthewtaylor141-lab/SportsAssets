-- Rollback of 249 (the venue catalogue's completeness receipts and the
-- us_premap listing state). Refuses while any refresh receipt exists: the
-- receipts are the record of what the catalogue held and dropped, and are
-- never dropped as cleanup. The three us_premap columns are a cache of the
-- venue's own flags, rewritten by every sweep; they and their CHECKs go.
-- Touches only 249's objects. A second run is a no-op: the guard reads the
-- table only when it exists (EXECUTE, because a PL/pgSQL `IF a AND b` still
-- plans `b`, and planning a query on a dropped table raises -- incident
-- release, verifier finding 4; as rollback 261).
DO $$
DECLARE
    held boolean := false;
BEGIN
    IF to_regclass('venue_catalogue_receipts') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM venue_catalogue_receipts)'
           INTO held;
    END IF;
    IF held THEN
        RAISE EXCEPTION 'venue_catalogue_receipts holds refresh receipts; '
                        'rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS venue_catalogue_receipts;
DROP FUNCTION IF EXISTS venue_catalogue_receipts_append_only();
DO $$
BEGIN
    IF to_regclass('us_premap') IS NULL THEN
        RETURN;
    END IF;
    ALTER TABLE us_premap DROP CONSTRAINT IF EXISTS us_premap_listing_state_known;
    ALTER TABLE us_premap DROP CONSTRAINT IF EXISTS us_premap_listing_state_source_known;
    ALTER TABLE us_premap DROP CONSTRAINT IF EXISTS us_premap_listing_state_sourced;
    ALTER TABLE us_premap DROP CONSTRAINT IF EXISTS us_premap_listing_pass_known;
    ALTER TABLE us_premap DROP COLUMN IF EXISTS listing_state;
    ALTER TABLE us_premap DROP COLUMN IF EXISTS listing_state_source;
    ALTER TABLE us_premap DROP COLUMN IF EXISTS listing_pass;
END $$;
