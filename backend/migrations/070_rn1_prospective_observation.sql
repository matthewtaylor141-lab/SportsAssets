-- PROSPECTIVE RN1 OBSERVATION. The minimum store required to START.
--
-- Owner directive 2026-09-19 (the approved reorder): "START ACCUMULATING
-- PROSPECTIVE RN1_SHADOW EVIDENCE AS SOON AS POSSIBLE. Do not wait for
-- COMMAND UI, PDF reporting or the complete API before prospective
-- collection begins."
--
-- Four things must exist before row 1, and they are all here:
--
--   1. A POLICY FREEZE. A prospective ledger whose rules can drift is a
--      ledger of nothing: two decisions a week apart would be
--      incomparable and no later reader could tell. shadow_policy_versions
--      is written once per rule-set and shadow_decisions.policy_version
--      carries a FOREIGN KEY into it -- a decision under an unfrozen
--      policy cannot be written at all.
--
--   2. DEDUPLICATION SOLVED BEFORE COLLECTION, not after. rn1_observations
--      carries an idempotency key built from VENUE-NATIVE identifiers and
--      a UNIQUE index on it. The key is never (market, side, price):
--      "Do not deduplicate economically distinct RN1 fills merely because
--      they have the same market, side and price."
--
--   3. REORGS AND CORRECTIONS THAT STAY APPEND-ONLY. A chain reorg or a
--      venue correction does not edit the observation it contradicts. It
--      appends an OBSERVATION_INVALIDATED or OBSERVATION_CORRECTED row
--      that REFERENCES the original. What BETTOR saw at T0 remains what
--      BETTOR saw at T0, beside the later correction, forever.
--
--   4. THE MARKET STATE BETTOR ACTUALLY HAD, named by its evidence
--      source. A decision that borrows a book it never saw is not
--      prospective, and one that borrows RN1_PRICE as its own executable
--      price is not a decision -- it is RN1's fill with our name on it.
--      Hence the four prices, kept in four columns.
--
-- OBSERVATION IS NOT AUTOMATICALLY A TRADE. Nothing in this migration
-- creates a decision. An observation row is a sighting; the decision
-- that may or may not follow it is a separate row in shadow_decisions,
-- and NO_TRADE is a decision that must be retained.

BEGIN;

-- ── 1. THE POLICY FREEZE ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS shadow_policy_versions (
    policy_version   TEXT PRIMARY KEY,

    -- the declared rule set, hashed. Not a file hash: a comment edit
    -- must not invent a new policy generation.
    policy_sha       TEXT NOT NULL,
    -- the implementing modules' bytes, hashed separately, so a rule
    -- whose CODE changed while its DECLARATION did not is still visible.
    -- 'NOT_IDENTIFIED' when the modules could not be read -- never a
    -- placeholder that looks like a hash.
    policy_code_sha  TEXT NOT NULL,

    lane             TEXT NOT NULL,
    action_set       JSONB NOT NULL,

    pairing_rule_version              TEXT NOT NULL,
    cashout_rule_version              TEXT NOT NULL,
    latency_policy_version            TEXT NOT NULL,
    execution_reconstruction_version  TEXT NOT NULL,
    sizing_policy_version             TEXT NOT NULL,

    declaration      JSONB NOT NULL,
    frozen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT shadow_policy_lane CHECK (lane IN (
        'RN1_SHADOW','BETTOR_EV_SHADOW'))
);

DROP TRIGGER IF EXISTS shadow_policy_versions_immutable
    ON shadow_policy_versions;
CREATE TRIGGER shadow_policy_versions_immutable
    BEFORE UPDATE OR DELETE ON shadow_policy_versions
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- A DECISION CANNOT NAME A POLICY THAT WAS NEVER FROZEN.
ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_policy_frozen;
ALTER TABLE shadow_decisions
    ADD CONSTRAINT shadow_decisions_policy_frozen
    FOREIGN KEY (policy_version)
    REFERENCES shadow_policy_versions (policy_version);

-- ── 2. RN1 OBSERVATIONS ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS rn1_observations (
    rn1_observation_id  TEXT PRIMARY KEY,

    -- OBSERVATION            a sighting
    -- OBSERVATION_INVALIDATED  a reorg/withdrawal, appended
    -- OBSERVATION_CORRECTED    a restatement, appended
    record_kind         TEXT NOT NULL DEFAULT 'OBSERVATION',
    supersedes_observation_id TEXT REFERENCES rn1_observations
                                   (rn1_observation_id),
    correction_reason   TEXT,

    -- THE IDEMPOTENCY KEY, and the basis it was built on. A key whose
    -- basis is unrecorded is a key nobody can audit later.
    idempotency_key     TEXT NOT NULL,
    idempotency_basis   TEXT NOT NULL,

    -- venue-native identity
    source_fill_id      TEXT,
    source_event_id     TEXT,
    source_type         TEXT NOT NULL,
    source_reference    TEXT,
    raw_evidence_reference TEXT,
    ingest_version      TEXT NOT NULL,

    -- THE THREE CLOCKS, kept apart. RN1 acted at one instant, we heard
    -- about it at a second, and we wrote it down at a third. Collapsing
    -- them is how a latency figure becomes a measurement of our own
    -- database.
    rn1_source_ts       TIMESTAMPTZ,
    bettor_received_ts  TIMESTAMPTZ NOT NULL,
    observation_written_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_ts_status    TEXT NOT NULL DEFAULT 'SOURCE_SUPPLIED',

    -- the subject
    event_id            TEXT,
    market_id           TEXT,
    symbol              TEXT,
    outcome_leg         TEXT,
    side                TEXT NOT NULL,
    rn1_price           NUMERIC NOT NULL,
    rn1_quantity        NUMERIC NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT rn1_obs_kind CHECK (record_kind IN (
        'OBSERVATION','OBSERVATION_INVALIDATED','OBSERVATION_CORRECTED')),
    CONSTRAINT rn1_obs_side CHECK (side IN ('BUY','SELL')),
    CONSTRAINT rn1_obs_basis CHECK (idempotency_basis IN (
        'VENUE_NATIVE_FILL_ID','DERIVED_FILL_TUPLE')),
    CONSTRAINT rn1_obs_source_type CHECK (source_type IN (
        'CHAIN','POLL','S1','RECONCILER','BACKFILL')),
    CONSTRAINT rn1_obs_ts_status CHECK (source_ts_status IN (
        'SOURCE_SUPPLIED','MISSING','FALLBACK_SUBSTITUTED')),
    -- A CORRECTION MUST POINT AT WHAT IT CORRECTS, and a sighting must
    -- not pretend to be one. Both directions, because either mistake
    -- turns the append-only chain into a pile of unrelated rows.
    CONSTRAINT rn1_obs_correction_references CHECK (
        (record_kind = 'OBSERVATION'
         AND supersedes_observation_id IS NULL)
        OR (record_kind <> 'OBSERVATION'
            AND supersedes_observation_id IS NOT NULL
            AND correction_reason IS NOT NULL))
);

-- THE DEDUPE, AND ONLY OVER SIGHTINGS. A correction legitimately
-- carries the same idempotency key as the row it supersedes -- that is
-- how they are known to be about the same fill -- so the uniqueness is
-- partial by construction, not by oversight.
CREATE UNIQUE INDEX IF NOT EXISTS rn1_observations_idem_idx
    ON rn1_observations (idempotency_key)
    WHERE record_kind = 'OBSERVATION';

CREATE INDEX IF NOT EXISTS rn1_observations_received_idx
    ON rn1_observations (bettor_received_ts DESC);
CREATE INDEX IF NOT EXISTS rn1_observations_symbol_idx
    ON rn1_observations (symbol, bettor_received_ts DESC);
CREATE INDEX IF NOT EXISTS rn1_observations_supersedes_idx
    ON rn1_observations (supersedes_observation_id)
    WHERE supersedes_observation_id IS NOT NULL;

DROP TRIGGER IF EXISTS rn1_observations_immutable ON rn1_observations;
CREATE TRIGGER rn1_observations_immutable
    BEFORE UPDATE OR DELETE ON rn1_observations
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── 3. THE MARKET STATE BETTOR ACTUALLY HAD ─────────────────────────
--
-- Its own table, referenced by the decision, because the same captured
-- book may serve several decisions and because a book must be able to
-- record that it was UNREADABLE without that fact being mistaken for a
-- price of zero.

CREATE TABLE IF NOT EXISTS shadow_market_states (
    market_state_id     TEXT PRIMARY KEY,
    captured_at         TIMESTAMPTZ NOT NULL,
    symbol              TEXT NOT NULL,
    outcome_leg         TEXT,

    -- EVIDENCE SOURCE TRAVELS WITH THE BOOK. "Do not merge sources
    -- silently" starts here: a mid built from two feeds is two mids.
    evidence_source     TEXT NOT NULL,
    readable            BOOLEAN NOT NULL,
    why_unreadable      TEXT,

    bid                 NUMERIC,
    ask                 NUMERIC,
    mid                 NUMERIC,
    spread              NUMERIC,
    available_depth     JSONB,
    l2_reference        JSONB,

    -- how old the book already was when we looked, and how often the
    -- source can tick at all (the horizon-observability input)
    staleness_ms        NUMERIC,
    source_interval_s   NUMERIC,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT shadow_market_state_unreadable CHECK (
        readable IS TRUE OR why_unreadable IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS shadow_market_states_symbol_idx
    ON shadow_market_states (symbol, captured_at DESC);

DROP TRIGGER IF EXISTS shadow_market_states_immutable
    ON shadow_market_states;
CREATE TRIGGER shadow_market_states_immutable
    BEFORE UPDATE OR DELETE ON shadow_market_states
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── 4. THE FOUR PRICES, NEVER SUBSTITUTED FOR ONE ANOTHER ───────────
--
-- RN1_PRICE is what RN1 got. It is NOT BETTOR's executable price and is
-- never used as one. Between it and any shadow fill sit two facts we
-- can measure -- the price when we observed, the price when we decided
-- -- and one we reconstruct, the price at shadow arrival.

ALTER TABLE shadow_decisions
    ADD COLUMN IF NOT EXISTS rn1_observation_id TEXT
        REFERENCES rn1_observations (rn1_observation_id),
    ADD COLUMN IF NOT EXISTS market_state_id TEXT
        REFERENCES shadow_market_states (market_state_id),
    ADD COLUMN IF NOT EXISTS rn1_price NUMERIC,
    ADD COLUMN IF NOT EXISTS price_when_bettor_observed NUMERIC,
    ADD COLUMN IF NOT EXISTS price_when_bettor_decided NUMERIC;

-- EVERY RN1_SHADOW DECISION DESCENDS FROM AN OBSERVATION. The lane is
-- defined as "what BETTOR would have done OBSERVING RN1"; a row with no
-- observation behind it is not in that lane, whatever it is tagged.
ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_rn1_observed;
ALTER TABLE shadow_decisions
    ADD CONSTRAINT shadow_decisions_rn1_observed
    CHECK (lane <> 'RN1_SHADOW' OR rn1_observation_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS shadow_decisions_observation_idx
    ON shadow_decisions (rn1_observation_id);

ALTER TABLE shadow_executions
    ADD COLUMN IF NOT EXISTS price_at_shadow_arrival NUMERIC,
    ADD COLUMN IF NOT EXISTS arrival_market_state_id TEXT
        REFERENCES shadow_market_states (market_state_id);

COMMIT;
