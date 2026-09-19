-- A RECOVERED FILL IS NOT A PROSPECTIVE OBSERVATION, AND THE SCHEMA
-- SAYS SO RATHER THAN THE WRITER.
--
-- Owner directive 2026-09-19, section 7, on fills recovered after an
-- ingestion incident: "THEY MAY NOT BE INSERTED AS THOUGH THEY WERE
-- PROSPECTIVE RN1_SHADOW OBSERVATIONS ... They cannot generate a
-- prospective shadow decision."
--
-- WHY THIS LANDS EVEN THOUGH THE INCIDENT WAS NOT OURS. The 17:01:06Z
-- detection stall classified C: the public feed's own newest row for
-- every one of the twelve configured wallets equals ours exactly, so
-- nothing was lost and there is nothing to recover. The prohibition
-- therefore never engaged -- which is precisely why it should be built
-- now, while no one is under pressure to backfill. A rule that exists
-- only in a writer is a rule that survives until the first outage at
-- three in the morning.
--
-- THE MECHANISM. shadow_decisions already requires an RN1_SHADOW row to
-- name an observation. That FK cannot currently tell a sighting from a
-- recovered row. A generated column pinned to the literal 'OBSERVATION'
-- on each side turns the reference into a composite one, so the
-- database itself refuses to let a decision descend from anything that
-- is not a prospective sighting. No trigger, no application check, no
-- way to be talked out of it in a hurry.
--
-- APPEND-ONLY IS UNAFFECTED. Nothing here updates or deletes a row;
-- recovered fills are still recorded, still queryable, still part of
-- the history. They simply cannot masquerade as evidence of what we
-- knew at T0, because we did not know it at T0.

BEGIN;

-- 1. RECOVERED IS A FIRST-CLASS KIND, not an absence. A fill that
--    arrives late is real evidence about the venue and is kept; what it
--    is not is evidence about our own foresight.
ALTER TABLE rn1_observations DROP CONSTRAINT IF EXISTS rn1_obs_kind;
ALTER TABLE rn1_observations ADD CONSTRAINT rn1_obs_kind
    CHECK (record_kind IN (
        'OBSERVATION',
        'OBSERVATION_INVALIDATED',
        'OBSERVATION_CORRECTED',
        'RECOVERED_AFTER_INGESTION_INCIDENT'));

-- A recovered row must say which incident produced it and when it was
-- recovered, so the dataset can always exclude that window explicitly
-- rather than by guesswork.
ALTER TABLE rn1_observations
    ADD COLUMN IF NOT EXISTS recovery_incident TEXT,
    ADD COLUMN IF NOT EXISTS recovered_at TIMESTAMPTZ;

ALTER TABLE rn1_observations
    DROP CONSTRAINT IF EXISTS rn1_obs_recovery_named;
ALTER TABLE rn1_observations ADD CONSTRAINT rn1_obs_recovery_named
    CHECK (record_kind <> 'RECOVERED_AFTER_INGESTION_INCIDENT'
           OR (recovery_incident IS NOT NULL AND recovered_at IS NOT NULL));

-- The correction constraint predates this kind and would refuse a
-- recovered row for having no row to supersede. Recovery is not a
-- correction: it supersedes nothing, because nothing was there.
ALTER TABLE rn1_observations
    DROP CONSTRAINT IF EXISTS rn1_obs_correction_references;
ALTER TABLE rn1_observations ADD CONSTRAINT rn1_obs_correction_references
    CHECK (
        (record_kind IN ('OBSERVATION',
                         'RECOVERED_AFTER_INGESTION_INCIDENT')
         AND supersedes_observation_id IS NULL)
        OR (record_kind IN ('OBSERVATION_INVALIDATED',
                            'OBSERVATION_CORRECTED')
            AND supersedes_observation_id IS NOT NULL
            AND correction_reason IS NOT NULL));

-- The dedupe index is already partial on record_kind = 'OBSERVATION',
-- so a recovered row never collides with the sighting it duplicates --
-- which is correct: they are two different claims about one fill, and
-- the point of keeping both is to be able to tell them apart.
CREATE UNIQUE INDEX IF NOT EXISTS rn1_observations_recovered_idem_idx
    ON rn1_observations (idempotency_key)
    WHERE record_kind = 'RECOVERED_AFTER_INGESTION_INCIDENT';

-- 2. THE COMPOSITE REFERENCE. Each side carries a column that can only
--    ever hold the literal 'OBSERVATION' when the row is one, so a
--    decision can only be joined to a prospective sighting.
ALTER TABLE rn1_observations
    ADD COLUMN IF NOT EXISTS prospective_kind TEXT
        GENERATED ALWAYS AS (
            CASE WHEN record_kind = 'OBSERVATION' THEN 'OBSERVATION' END
        ) STORED;

CREATE UNIQUE INDEX IF NOT EXISTS rn1_observations_prospective_key
    ON rn1_observations (rn1_observation_id, prospective_kind);

ALTER TABLE shadow_decisions
    ADD COLUMN IF NOT EXISTS rn1_observation_kind TEXT
        GENERATED ALWAYS AS (
            CASE WHEN rn1_observation_id IS NOT NULL
                 THEN 'OBSERVATION' END
        ) STORED;

ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_prospective_only;
ALTER TABLE shadow_decisions
    ADD CONSTRAINT shadow_decisions_prospective_only
    FOREIGN KEY (rn1_observation_id, rn1_observation_kind)
    REFERENCES rn1_observations (rn1_observation_id, prospective_kind)
    NOT VALID;

COMMIT;
