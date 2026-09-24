-- 106 — THE VENUE-NATIVE CONTRACT IDENTITY, kept distinct from the global one.
--
-- WHY THIS EXISTS. Every venue book read the external valuation loop made
-- answered NotFoundError -- six in run 24, on current fixtures with plain
-- moneyline slugs. The cause was an identity error, not a market problem:
-- `pmus.book_read` takes the venue's OWN market slug, and the loop was
-- passing `markets.slug`, the global catalogue's slug for the same fixture.
-- The venue has never heard of it. The working readers take theirs from
-- `us_premap.market_slug` (`workers/bettor_state.PREMAP_SQL`,
-- `shadow_bettor.UNIVERSE_SQL`), and `bettor_evidence_matrix.TAPE_MARKET_JOIN`
-- already records the rule: the join key is "an equality on the venue's own
-- market slug. No fuzzy title matching, no team-name matching".
--
-- WHY A COLUMN AND NOT A COERCION. `us_premap` carries no `condition_id`.
-- There is therefore no global id to write for a contract discovered through
-- the venue's catalogue, and the two available shortcuts are both wrong:
--
--   * inventing a condition_id would put a fabricated primary identity into
--     the record that nothing else can join to;
--   * forcing a global-to-US equivalence -- picking the `markets` row whose
--     teams and date look closest -- would assert exactly the fuzzy match the
--     evidence matrix says the key must never be.
--
-- So the venue-native identity gets its own column, `contract_identity_basis`
-- says which identifier is authoritative for the row, and a row may carry
-- one or both. Existing rows are preserved untouched and labelled
-- GLOBAL_CONDITION_ID, because that is what they are.

BEGIN;

ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS us_market_slug text;

ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS contract_identity_basis text;

COMMENT ON COLUMN external_valuations.us_market_slug IS
    'The venue''s OWN market slug (us_premap.market_slug), which is what '
    'pmus.book_read accepts. NULL when the contract was reached through the '
    'global catalogue instead. Never derived from a condition_id.';

COMMENT ON COLUMN external_valuations.contract_identity_basis IS
    'Which identifier is authoritative for this row: VENUE_NATIVE_US_SLUG, '
    'GLOBAL_CONDITION_ID, or BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED. It is '
    'never set to BOTH on the strength of a title-and-date resemblance.';

-- EXISTING ROWS ARE LABELLED, NOT REWRITTEN. Every row written before this
-- migration carries a global condition_id and no venue-native slug, which is
-- a true description of how it was produced.
UPDATE external_valuations
   SET contract_identity_basis = 'GLOBAL_CONDITION_ID'
 WHERE contract_identity_basis IS NULL
   AND condition_id IS NOT NULL;

UPDATE external_valuations
   SET contract_identity_basis = 'NEITHER_IDENTITY_RECORDED'
 WHERE contract_identity_basis IS NULL;

-- AT LEAST ONE IDENTITY. A valuation of a contract nobody can look up
-- afterwards is not evidence of anything. Dropped first so the migration is
-- re-runnable.
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_has_a_contract_identity;
ALTER TABLE external_valuations
    ADD CONSTRAINT external_valuations_has_a_contract_identity
    CHECK (condition_id IS NOT NULL OR us_market_slug IS NOT NULL)
    NOT VALID;   -- NOT VALID: pre-existing rows are not re-examined, and a
                 -- row with neither identity is labelled above rather than
                 -- deleted. New rows are checked.

ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_identity_basis_declared;
ALTER TABLE external_valuations
    ADD CONSTRAINT external_valuations_identity_basis_declared
    CHECK (contract_identity_basis IS NULL OR contract_identity_basis IN (
        'VENUE_NATIVE_US_SLUG',
        'GLOBAL_CONDITION_ID',
        'BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED',
        'NEITHER_IDENTITY_RECORDED'));

-- ── one observation, one row, whichever identity it carries ──────────
--
-- Migration 105's index keys on coalesce(condition_id, ''), so two
-- valuations of two DIFFERENT venue-native contracts in the same second
-- would collide on the empty string and the second would be silently
-- skipped. The venue-native slug joins the key.
DROP INDEX IF EXISTS external_valuations_one_per_observation;

-- Deduplicate on the NEW key before building it, keeping the earliest row
-- and preferring one whose outcome is already known -- the same rule
-- migration 105 used, for the same reason: the row that has been joined to
-- an outcome is the one other records point at.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY experiment_id,
                            coalesce(condition_id, ''),
                            coalesce(us_market_slug, ''),
                            contract_selection,
                            coalesce(event_key, ''),
                            coalesce(observed_at, '-infinity'::timestamptz)
               ORDER BY outcome_known DESC, id ASC) AS rn
      FROM external_valuations)
DELETE FROM external_valuations v
 USING ranked r
 WHERE v.id = r.id AND r.rn > 1;

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

COMMIT;
