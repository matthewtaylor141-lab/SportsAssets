-- ══════════════════════════════════════════════════════════════════════
-- 142 · PAIR OBSERVATION ATTEMPTS: EVERY CANDIDATE THE OBSERVER TRIED,
--       AND EXACTLY WHAT HAPPENED TO IT
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP. `bettor_pair_observations` records only structures that were
-- admitted and frozen. A pass that attempted three candidates and recorded
-- nothing left no row at all, and the cycle's result -- the only place the
-- refusals were named -- was overwritten by the next cycle's heartbeat. So
-- "zero observations" could not be told apart from "the observer never ran"
-- or "it ran and every candidate was refused for a stated reason".
--
-- WHAT THIS RECORDS. One row per attempted candidate: where it came from
-- (the venue catalogue, or an identity the entry cycle resolved), the
-- contract and side, the fixture, the outcome, the refusal named at every
-- depth (the first leg's quote, its build, discovery, each sibling), how
-- many observations it wrote, and the venue reads it made.
--
-- It records NOTHING about money: no order, reservation or account. It is a
-- measurement ledger of a non-funded observer.
--
-- APPEND-ONLY. ADDITIVE.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_pair_observation_attempts (
    attempt_id           bigserial   PRIMARY KEY,
    pass_id              text        NOT NULL,
    attempted_at         timestamptz NOT NULL,
    finished_at          timestamptz NOT NULL,
    candidate_source     text        NOT NULL,
    us_market_slug       text        NOT NULL,
    side                 text        NOT NULL,
    fixture              text,
    outcome              text        NOT NULL,
    refusal              text,
    observations_written integer     NOT NULL DEFAULT 0,
    detail               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    venue_reads          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT bettor_pair_obs_attempt_source_ck CHECK (
        candidate_source IN ('VENUE_CATALOGUE', 'ENTRY_IDENTITY')),
    CONSTRAINT bettor_pair_obs_attempt_side_ck CHECK (
        side IN ('ORDER_INTENT_BUY_LONG', 'ORDER_INTENT_BUY_SHORT')),
    CONSTRAINT bettor_pair_obs_attempt_outcome_ck CHECK (
        outcome IN ('RECORDED', 'ALREADY_RECORDED_THIS_BUCKET',
                    'NOTHING_ADMITTED', 'REFUSED', 'ERROR',
                    'ADMITTED_BUT_NOTHING_RECORDABLE')),
    -- A REFUSAL IS NAMED. An attempt that recorded nothing and states no
    -- reason is exactly the unexplained zero this table exists to prevent.
    CONSTRAINT bettor_pair_obs_attempt_named_ck CHECK (
        outcome IN ('RECORDED', 'ALREADY_RECORDED_THIS_BUCKET',
                    'ADMITTED_BUT_NOTHING_RECORDABLE')
        OR refusal IS NOT NULL),
    CONSTRAINT bettor_pair_obs_attempt_written_ck CHECK (
        (outcome = 'RECORDED') = (observations_written > 0)),
    CONSTRAINT bettor_pair_obs_attempt_order_ck CHECK (
        finished_at >= attempted_at)
);

CREATE INDEX IF NOT EXISTS bettor_pair_obs_attempts_at_idx
    ON bettor_pair_observation_attempts (attempted_at DESC);
CREATE INDEX IF NOT EXISTS bettor_pair_obs_attempts_fixture_idx
    ON bettor_pair_observation_attempts (fixture, attempted_at DESC);

CREATE OR REPLACE FUNCTION bettor_pair_obs_attempt_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'observation attempt % is a record; it is not edited',
        OLD.attempt_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_pair_obs_attempt_is_append_only_trg
    ON bettor_pair_observation_attempts;
CREATE TRIGGER bettor_pair_obs_attempt_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_pair_observation_attempts
    FOR EACH ROW EXECUTE FUNCTION bettor_pair_obs_attempt_is_append_only();

COMMIT;
