-- THE L2 EVIDENCE BRIDGE: institutional market data, no trading capability.
--
-- Owner directive 2026-09-19 23:0xZ. The production PMX private key
-- exists ONLY as a GitHub repository secret and cannot be recovered.
-- Render therefore cannot reach institutional L2 directly, so the
-- authenticated GitHub lane becomes the temporary credential holder
-- and carries MARKET DATA ACROSS -- never the credential.
--
--   BETTOR writes a REQUEST  ->  the GitHub lane reads production
--   L2 read-only  ->  the lane writes IMMUTABLE EVIDENCE here  ->
--   the experimental engine reconstructs execution from it.
--
-- WHAT THIS BRIDGE STRUCTURALLY CANNOT BECOME. There is no order,
-- cancel, funding or position table here and the lane's read module
-- has no such path in it (a job step greps for them before a
-- credential is staged). The bridge moves BOOKS. A trading capability
-- would need a different table, a different module and a different
-- review, which is the point of writing it this way rather than
-- widening something that already exists.
--
-- THE EVIDENCE IS IMMUTABLE. A book is a fact about an instant. A
-- correction to a recorded book is a new observation, never an edit --
-- the execution reconstructed against it would otherwise change
-- retroactively, which is the whole failure this ledger prevents.

BEGIN;

-- ── what BETTOR asked for ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bettor_l2_requests (
    l2_request_id           TEXT PRIMARY KEY,
    requested_at            TIMESTAMPTZ NOT NULL,
    requested_by            TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    -- The binding the request was made under, so evidence fetched for
    -- one identity can never be silently reused for another.
    identity_binding_sha    TEXT,
    purpose                 TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'PENDING',
    claimed_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bettor_l2_request_status
        CHECK (status IN ('PENDING', 'SERVED', 'FAILED', 'EXPIRED')),
    CONSTRAINT bettor_l2_request_purpose
        CHECK (purpose IN ('X1_ARRIVAL', 'MARKOUT', 'IDENTITY_CONFIRM',
                           'SIBLING_ENUMERATION'))
);

CREATE INDEX IF NOT EXISTS bettor_l2_requests_pending_idx
    ON bettor_l2_requests (status, requested_at)
    WHERE status = 'PENDING';

-- The request row's STATUS is the one mutable field on this bridge --
-- a queue needs to be claimable. Everything the evidence says is
-- append-only below.

-- ── what the venue actually answered ─────────────────────────────────

CREATE TABLE IF NOT EXISTS bettor_l2_evidence (
    l2_evidence_id          TEXT PRIMARY KEY,
    l2_request_id           TEXT
        REFERENCES bettor_l2_requests (l2_request_id),

    -- §: every field the directive named
    request_id              TEXT,               -- the VENUE's request id
    instrument_id           TEXT NOT NULL,
    identity_binding_sha    TEXT,
    source_timestamp        TEXT,               -- venue transactTime
    received_timestamp      TIMESTAMPTZ NOT NULL,
    l2_book_sha             TEXT NOT NULL,
    bids                    JSONB NOT NULL,
    offers                  JSONB NOT NULL,
    price_scale             INTEGER,
    quantity_scale          INTEGER,
    evidence_class          TEXT NOT NULL,

    -- THE REAL TRANSPORT COST, MEASURED. "Do not manufacture latency."
    -- Two numbers, kept apart: the venue call itself, and the whole
    -- bridge round trip from BETTOR's request to evidence landing.
    venue_request_ms        DOUBLE PRECISION,
    bridge_latency_ms       DOUBLE PRECISION,
    -- The regime this evidence belongs to. A book fetched through a
    -- CI runner minutes after the request is not the same execution
    -- environment as a persistent worker milliseconds after it, and
    -- results from the two must never be pooled.
    latency_regime          TEXT NOT NULL,

    venue_state             TEXT,
    bbo                     JSONB,
    instrument_record       JSONB,
    bridge_run_id           TEXT,
    bridge_revision         TEXT,
    written_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT bettor_l2_evidence_class
        CHECK (evidence_class = 'OBSERVED_PRODUCTION'),
    CONSTRAINT bettor_l2_evidence_regime
        CHECK (latency_regime IN ('GITHUB_BRIDGE',
                                  'PERSISTENT_INSTITUTIONAL_WORKER')),
    -- A book converted to prices needs both scales; without them the
    -- evidence is recorded but cannot be priced, and saying so here is
    -- cheaper than discovering it at reconstruction time.
    CONSTRAINT bettor_l2_evidence_scales_together
        CHECK ((price_scale IS NULL) = (quantity_scale IS NULL))
);

DROP TRIGGER IF EXISTS bettor_l2_evidence_immutable ON bettor_l2_evidence;
CREATE TRIGGER bettor_l2_evidence_immutable
    BEFORE UPDATE OR DELETE ON bettor_l2_evidence
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

CREATE INDEX IF NOT EXISTS bettor_l2_evidence_instrument_idx
    ON bettor_l2_evidence (instrument_id, received_timestamp DESC);
CREATE INDEX IF NOT EXISTS bettor_l2_evidence_request_idx
    ON bettor_l2_evidence (l2_request_id);

-- The experimental decision points at the evidence it walked, so a
-- reconstruction can always be re-derived from the exact book.
ALTER TABLE bettor_experimental_decisions
    ADD COLUMN IF NOT EXISTS l2_evidence_id TEXT
        REFERENCES bettor_l2_evidence (l2_evidence_id);
ALTER TABLE bettor_experimental_decisions
    ADD COLUMN IF NOT EXISTS latency_regime TEXT;
ALTER TABLE bettor_experimental_decisions
    ADD COLUMN IF NOT EXISTS observed_arrival_latency_ms DOUBLE PRECISION;

COMMIT;
