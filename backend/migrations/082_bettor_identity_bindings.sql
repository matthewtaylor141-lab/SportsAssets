-- THE IDENTITY BINDING, RESOLVED BEFORE THE SIGNAL NEEDS IT.
--
-- Owner directive 2026-09-20 (identity blocker): 13 prospective BUY
-- decisions were written with IDENTITY_BINDING_STATUS = NOT_IDENTIFIED
-- and EXECUTION_STATUS = BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE, so
-- POSITIONS = 0. "Do not wait until X1 says BUY and then begin
-- resolving identity."
--
-- WHY THE BINDING NEEDS ITS OWN TABLE rather than being derived on the
-- hot path. Until now the binding was recomputed every tick from
-- whatever instrument record the GitHub bridge happened to have written
-- into bettor_l2_evidence. The direct worker writes no such record, so
-- the lookup returned nothing and every binding collapsed to
-- NOT_IDENTIFIED -- with the honest message "no institutional
-- instrument record has been observed for this symbol", which was true
-- and was not the point. A binding resolved ahead of time is a fact
-- about the MARKET, not about what a book fetch happened to carry.
--
-- APPEND-ONLY, KEYED BY THE BINDING'S OWN SHA. A binding is a claim
-- that two venues' keys name one economic contract. When that claim
-- changes -- a venue relists, a sibling appears, a scale is corrected
-- -- the new claim is a NEW ROW, and the old one stays exactly as it
-- was. Decisions carry identity_binding_sha, so a trade can always be
-- read back against the binding it was actually executed under rather
-- than against whatever we believe today.
--
-- ELIGIBILITY IS INDEPENDENT OF MODEL OUTCOME (§6). Nothing in this
-- table is derived from a price, a signal, a direction or a P&L. It is
-- venue-native identity only: no fuzzy title matching, no team-name
-- matching, no price matching, and no instrument chosen because its
-- current price looks similar.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_identity_bindings (
    identity_binding_sha        TEXT        PRIMARY KEY,
    binding_version             TEXT        NOT NULL,

    -- the retail side, by the retail venue's OWN keys
    market_id                   TEXT        NOT NULL,
    retail_native_id            TEXT,
    outcome_leg                 TEXT        NOT NULL,
    event_slug                  TEXT,

    -- the institutional side, by the institutional venue's OWN keys
    institutional_instrument_id TEXT,
    institutional_event_id      TEXT,
    outcome_strike              TEXT,
    event_outcome               TEXT,

    -- the verdict, and what has to be true to price it
    identity_status             TEXT        NOT NULL,
    execution_eligible          BOOLEAN     NOT NULL,
    settlement_equivalence      TEXT,
    price_scale                 INTEGER,
    quantity_scale              INTEGER,
    payout_value                TEXT,

    -- the reasoning, both directions, kept verbatim
    agree                       JSONB,
    why                         JSONB,

    resolved_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    evidence_environment        TEXT        NOT NULL,

    -- Every verdict shadow_identity can reach, spelled out. A test
    -- asserts this list and that module's own constants are the same
    -- set, so a new verdict cannot be introduced on one side only.
    CONSTRAINT bettor_identity_status_known
        CHECK (identity_status IN (
            'EXACT_SAME_CONTRACT',
            'EXACT_ONE_TO_COMPLEMENT_BASKET',
            'STRUCTURALLY_IDENTIFIED_COMPLEMENT_PENDING_INSTITUTIONAL_CONFIRMATION',
            'DIFFERENT_CONTRACT',
            'AMBIGUOUS',
            'NOT_IDENTIFIED')),

    -- AN ELIGIBLE BINDING MUST BE PRICEABLE. An exact contract whose
    -- scales are unknown is not an executable one: a book cannot be
    -- converted without them, and this lane will not assume 100.
    CONSTRAINT bettor_identity_eligible_is_priceable
        CHECK (NOT execution_eligible
               OR (price_scale IS NOT NULL AND quantity_scale IS NOT NULL
                   AND institutional_instrument_id IS NOT NULL))
);

-- THE NEWEST BINDING PER (MARKET, LEG) is what the hot path reads.
CREATE INDEX IF NOT EXISTS bettor_identity_market_leg_idx
    ON bettor_identity_bindings (market_id, outcome_leg, resolved_at DESC);

CREATE INDEX IF NOT EXISTS bettor_identity_eligible_idx
    ON bettor_identity_bindings (execution_eligible, resolved_at DESC)
    WHERE execution_eligible;

-- APPEND-ONLY, ENFORCED. The same trigger the rest of this ledger uses:
-- a binding that could be edited in place would let a trade's recorded
-- identity change after the trade.
DROP TRIGGER IF EXISTS bettor_identity_bindings_append_only
    ON bettor_identity_bindings;
CREATE TRIGGER bettor_identity_bindings_append_only
    BEFORE UPDATE OR DELETE ON bettor_identity_bindings
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

COMMIT;
