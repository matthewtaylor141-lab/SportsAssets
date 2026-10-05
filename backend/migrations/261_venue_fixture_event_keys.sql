-- 261 · ONE VENUE FIXTURE, ONE EVENT KEY, WHICHEVER DISCOVERY FOUND IT
--
-- THE HAZARD (R30A P0 incident, PinnAPI-native discovery). A venue-native
-- valuation has no global condition, so the paper ledger counts its FIXTURE
-- as "event:<event_key>" (agents.derek_policy.fixture_of), and that key is
-- what the fixture rails compare: exclusive fixture ownership across
-- strategies, one live entry per fixture, exploration's fixture lock and its
-- sampling draw. Until the incident repair every event key came from the one
-- metered discovery provider. Now a fixture can be discovered by that
-- provider (its event id) OR by the PinnAPI feed ("pinnapi:<matchup id>",
-- pinnapi_discovery), so one venue event could reach the ledger under two
-- keys -- two "fixtures" -- and a second strategy could hold another
-- contract of a fixture a first already holds. That would loosen a rail,
-- which the repair must never do.
--
-- THE FIX. The event key of a venue-native valuation is the FIRST key
-- recorded for any contract of the same VENUE event (us_premap.event_slug)
-- in the experiment, and it is fixed here once
-- (ext_pinnacle_loop.sticky_event_key): one row per venue event, inserted
-- once (ON CONFLICT DO NOTHING) and never rewritten. Two writers racing on a
-- never-valued event -- the scheduled cycle and a reactive worker -- both
-- read back the row that won.
--
-- Append-only (UPDATE / DELETE refused). No order, threshold, rail or
-- control reads anything new: the rails compare the same key they always
-- did, it is only never split. IDEMPOTENT.
CREATE OR REPLACE FUNCTION venue_fixture_event_keys_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (a fixture''s event key is '
                    'fixed once)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE TABLE IF NOT EXISTS venue_fixture_event_keys (
    venue_event_slug        text        PRIMARY KEY,
    event_key               text        NOT NULL,
    basis                   text        NOT NULL,
    first_provider_event_id text,
    recorded_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT venue_fixture_event_keys_slug_ck
        CHECK (length(venue_event_slug) BETWEEN 1 AND 200),
    CONSTRAINT venue_fixture_event_keys_key_ck
        CHECK (length(event_key) BETWEEN 1 AND 200),
    CONSTRAINT venue_fixture_event_keys_basis_ck
        CHECK (length(basis) BETWEEN 1 AND 200)
);

DROP TRIGGER IF EXISTS venue_fixture_event_keys_append_only_trg
    ON venue_fixture_event_keys;
CREATE TRIGGER venue_fixture_event_keys_append_only_trg
    BEFORE UPDATE OR DELETE ON venue_fixture_event_keys
    FOR EACH ROW EXECUTE FUNCTION venue_fixture_event_keys_append_only();

COMMENT ON TABLE venue_fixture_event_keys IS
    'One row per venue event: the event key its venue-native valuations carry, '
    'fixed at the first valuation whichever discovery (metered provider or '
    'PinnAPI-native) found the fixture, so the fixture rails never see one '
    'fixture as two (migration 261, R30A P0 incident).';
