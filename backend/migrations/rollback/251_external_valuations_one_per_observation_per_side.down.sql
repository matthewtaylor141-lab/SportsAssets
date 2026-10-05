-- Rollback of 251 (the venue side and the no-instant fallback in
-- external_valuations' uniqueness key).
--
-- Refuses while any row exists that migration 106's narrower key cannot
-- hold: a complement-side valuation beside its priced side, or two
-- valuations of one contract side that both carry no source instant. Those
-- rows are the record of what the lane valued and of what the paper
-- strategies decided on them, and are never deleted as cleanup. With none
-- present, migration 106's index is restored exactly.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM external_valuations
                WHERE payout_is_complement) THEN
        RAISE EXCEPTION 'external_valuations holds complement-side '
                        'valuations (migration 251); rollback refused';
    END IF;
    IF EXISTS (SELECT 1 FROM external_valuations
                GROUP BY experiment_id, coalesce(condition_id, ''),
                         coalesce(us_market_slug, ''), contract_selection,
                         coalesce(event_key, ''),
                         coalesce(observed_at, '-infinity'::timestamptz)
               HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'external_valuations holds valuations that migration '
                        '106''s key would merge (no source instant, or one '
                        'per side); rollback refused';
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
