-- Rollback of 251 (the venue side in external_valuations' uniqueness key).
-- Refuses while any complement-side valuation exists: those rows are the
-- record of what the lane valued on the other side of each contract and of
-- what the paper strategies decided on them, and are never deleted as
-- cleanup -- and migration 106's narrower key cannot hold them beside their
-- priced side. With none present, migration 106's index is restored
-- exactly.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM external_valuations
                WHERE payout_is_complement) THEN
        RAISE EXCEPTION 'external_valuations holds complement-side '
                        'valuations (migration 251); rollback refused';
    END IF;
END $$;

DROP INDEX IF EXISTS external_valuations_one_per_observation;

CREATE UNIQUE INDEX IF NOT EXISTS external_valuations_one_per_observation
    ON external_valuations (
        experiment_id,
        coalesce(condition_id, ''),
        coalesce(us_market_slug, ''),
        contract_selection,
        coalesce(event_key, ''),
        coalesce(observed_at, '-infinity'::timestamptz));

COMMENT ON INDEX external_valuations_one_per_observation IS
    'One row per (experiment, contract identity, selection, event, source '
    'observation instant). The identity half is a coalesce over BOTH '
    'identifier columns, so a venue-native contract and a global one are '
    'different keys rather than colliding on the empty string.';
