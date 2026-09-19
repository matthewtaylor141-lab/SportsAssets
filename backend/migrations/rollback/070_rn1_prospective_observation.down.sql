-- Rollback 070. Drops the prospective RN1 observation store.
--
-- NOTE FOR WHOEVER RUNS THIS: rn1_observations is the raw prospective
-- evidence -- the sightings, in arrival order, with the three clocks
-- kept apart. It cannot be rebuilt from the decisions that descend from
-- it and it cannot be rebuilt from the venue, because the one fact it
-- records that nobody else holds is WHEN BETTOR HEARD. Export first.

BEGIN;

ALTER TABLE shadow_executions
    DROP COLUMN IF EXISTS arrival_market_state_id,
    DROP COLUMN IF EXISTS price_at_shadow_arrival;

ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_rn1_observed;
ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_policy_frozen;

DROP INDEX IF EXISTS shadow_decisions_observation_idx;

ALTER TABLE shadow_decisions
    DROP COLUMN IF EXISTS price_when_bettor_decided,
    DROP COLUMN IF EXISTS price_when_bettor_observed,
    DROP COLUMN IF EXISTS rn1_price,
    DROP COLUMN IF EXISTS market_state_id,
    DROP COLUMN IF EXISTS rn1_observation_id;

DROP TABLE IF EXISTS shadow_market_states;
DROP TABLE IF EXISTS rn1_observations;
DROP TABLE IF EXISTS shadow_policy_versions;

COMMIT;
