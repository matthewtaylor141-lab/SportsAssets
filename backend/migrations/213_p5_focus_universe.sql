-- ══════════════════════════════════════════════════════════════════════
-- 213 · P5 FOCUS UNIVERSE, SAME-BOOK AGREEMENT FIELDS, C12 DECISION PROOF
-- ══════════════════════════════════════════════════════════════════════
--
-- WHY. P5_LIVE_STREAM_BOOK_V1's same-book premise (S1) needs 30 comparable
-- stream-vs-retail samples at >= 95 % agreement. The probe sampled only the
-- experimental lane's focus set (3-4 F1 / NFL-prop instruments, one mapping
-- exactly), so it never observed the MLB / soccer contracts BETTOR actually
-- evaluates. `institutional_focus_universe` (sportsassets.
-- institutional_focus_universe) records the prioritized universe the stream,
-- recorder and probe now hold -- each member's tier, why, and its exact
-- identity or the reason it is UNAVAILABLE. The probe table gains the
-- per-observation agreement fields. `p5_c12_decision_proof` ties a decision
-- the decision path priced from the resident stream book to that book.
--
-- NOTHING HERE CHANGES THE RULE (30 samples, 95 %, the 2 s bounds, C1-C13)
-- OR ANY GATE. Nothing here can make a strategy live-eligible:
-- institutional_focus_universe.grants_live_eligibility is CHECKed false,
-- and both evidence tables keep orders_placed CHECKed to 0.
--
-- IDEMPOTENT: IF NOT EXISTS / guarded constraint creation. No BEGIN/COMMIT:
-- the runner applies the file inside one transaction with its
-- schema_migrations row. Rollback: rollback/213_p5_focus_universe.down.sql.

-- ── 1 · the same-book probe: every observation's agreement evidence ─────
ALTER TABLE institutional_same_book_probe
    ADD COLUMN IF NOT EXISTS institutional_symbol   text,
    ADD COLUMN IF NOT EXISTS identity_exact         boolean,
    ADD COLUMN IF NOT EXISTS outcome_side           text,
    ADD COLUMN IF NOT EXISTS bettor_event           text,
    ADD COLUMN IF NOT EXISTS retail_event_slug      text,
    ADD COLUMN IF NOT EXISTS inst_best_bid          numeric(18,6),
    ADD COLUMN IF NOT EXISTS inst_best_ask          numeric(18,6),
    ADD COLUMN IF NOT EXISTS retail_best_bid        numeric(18,6),
    ADD COLUMN IF NOT EXISTS retail_best_ask        numeric(18,6),
    ADD COLUMN IF NOT EXISTS gap_state              text,
    ADD COLUMN IF NOT EXISTS inst_receipt_age_s     double precision,
    ADD COLUMN IF NOT EXISTS retail_receipt_age_s   double precision,
    ADD COLUMN IF NOT EXISTS compared_at            timestamptz,
    ADD COLUMN IF NOT EXISTS agreed                 boolean,
    ADD COLUMN IF NOT EXISTS incomparable_reason    text,
    ADD COLUMN IF NOT EXISTS focus_tier             text,
    ADD COLUMN IF NOT EXISTS focus_tier_rank        integer,
    ADD COLUMN IF NOT EXISTS focus_why              text,
    ADD COLUMN IF NOT EXISTS focus_universe_id      text;

DO $$
BEGIN
    -- an agreement result exists only for a comparable sample
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'institutional_same_book_probe_agreed_ck') THEN
        ALTER TABLE institutional_same_book_probe
            ADD CONSTRAINT institutional_same_book_probe_agreed_ck
            CHECK (agreed IS NULL OR verdict <> 'NOT_COMPARABLE');
    END IF;
    -- an incomparable reason exists only for a NOT_COMPARABLE sample
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'institutional_same_book_probe_nc_reason_ck') THEN
        ALTER TABLE institutional_same_book_probe
            ADD CONSTRAINT institutional_same_book_probe_nc_reason_ck
            CHECK (incomparable_reason IS NULL OR verdict = 'NOT_COMPARABLE');
    END IF;
    -- a comparable verdict is never recorded against a non-exact identity
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'institutional_same_book_probe_exact_ck') THEN
        ALTER TABLE institutional_same_book_probe
            ADD CONSTRAINT institutional_same_book_probe_exact_ck
            CHECK (verdict = 'NOT_COMPARABLE' OR identity_exact IS NOT FALSE);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS institutional_same_book_probe_tier_idx
    ON institutional_same_book_probe (focus_tier, probed_at DESC);

-- ── 2 · the focus universe: one row per member per computed snapshot ────
CREATE TABLE IF NOT EXISTS institutional_focus_universe (
    id                       bigserial PRIMARY KEY,
    computed_at              timestamptz NOT NULL,
    recorded_at              timestamptz NOT NULL DEFAULT now(),
    process_id               text        NOT NULL,
    service                  text        NOT NULL,
    version                  text        NOT NULL,
    universe_id              text        NOT NULL,
    bound                    integer     NOT NULL,
    rank                     integer     NOT NULL,
    tier                     text        NOT NULL,
    tier_rank                integer     NOT NULL,
    why                      text        NOT NULL,
    reasons                  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    retail_slug              text        NOT NULL,
    outcome_side             text,
    bettor_event             text,
    strategy                 text,
    diagnostic_only          boolean     NOT NULL DEFAULT false,
    identity_status          text        NOT NULL,
    unavailable_reason       text,
    institutional_symbol     text,
    market_type              text,
    period                   text,
    retail_event_slug        text,
    institutional_event_id   text,
    settlement               jsonb,
    identity                 jsonb,
    stream_wanted            boolean,
    refs                     jsonb,
    grants_live_eligibility  boolean     NOT NULL DEFAULT false,
    orders_placed            integer     NOT NULL DEFAULT 0,
    CONSTRAINT institutional_focus_universe_key
        UNIQUE (universe_id, retail_slug),
    CONSTRAINT institutional_focus_universe_tier_ck CHECK (
        (tier, tier_rank) IN (('ACTUAL_OPEN_POSITION', 1),
                              ('EXECUTION_INTENT', 2),
                              ('PAPER_INVESTMENT_POSITION', 3),
                              ('V3_CANDIDATE', 4),
                              ('MAPPED_INVESTMENT_UNIVERSE', 5),
                              ('EXPLORATION_DIAGNOSTIC', 6),
                              ('BROADER_DISCOVERY', 7))),
    CONSTRAINT institutional_focus_universe_status_ck
        CHECK (identity_status IN ('EXACT', 'UNAVAILABLE')),
    CONSTRAINT institutional_focus_universe_reason_ck
        CHECK ((identity_status = 'EXACT') = (unavailable_reason IS NULL)),
    CONSTRAINT institutional_focus_universe_exact_ck
        CHECK (identity_status <> 'EXACT' OR institutional_symbol = retail_slug),
    CONSTRAINT institutional_focus_universe_diag_ck
        CHECK (tier <> 'EXPLORATION_DIAGNOSTIC' OR diagnostic_only),
    CONSTRAINT institutional_focus_universe_no_live_ck
        CHECK (grants_live_eligibility = false),
    CONSTRAINT institutional_focus_universe_no_orders_ck
        CHECK (orders_placed = 0)
);
CREATE INDEX IF NOT EXISTS institutional_focus_universe_at_idx
    ON institutional_focus_universe (computed_at DESC);
CREATE INDEX IF NOT EXISTS institutional_focus_universe_service_idx
    ON institutional_focus_universe (service, computed_at DESC);

-- ── 3 · C12: the decision priced from the resident stream book ──────────
CREATE TABLE IF NOT EXISTS p5_c12_decision_proof (
    id                        bigserial PRIMARY KEY,
    recorded_at               timestamptz NOT NULL DEFAULT now(),
    version                   text        NOT NULL,
    decision_id               text        NOT NULL,
    execution_intent_id       text        NOT NULL,
    strategy                  text,
    policy_version            text,
    us_market_slug            text        NOT NULL,
    order_intent              text,
    proof_status              text        NOT NULL,
    refusal                   text,
    price_source              text,
    stream_symbol             text,
    connection_epoch          integer,
    connection_id             text,
    obs_id                    text,
    book_received_at          timestamptz,
    book_venue_ts             timestamptz,
    book_age_s                double precision,
    decision_executable_price numeric(18,6),
    limit_price               numeric(18,6),
    p5_verdict                text,
    failed_components         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    c12_passed                boolean,
    live_eligible             boolean     NOT NULL,
    actual_state              text,
    actual_refusal            text,
    evidence                  jsonb,
    CONSTRAINT p5_c12_decision_proof_decision_key UNIQUE (decision_id),
    CONSTRAINT p5_c12_decision_proof_status_ck CHECK (proof_status IN (
        'PRICED_FROM_STREAM', 'REFUSED_BEFORE_SUBMISSION')),
    CONSTRAINT p5_c12_decision_proof_refusal_ck CHECK (
        (proof_status = 'REFUSED_BEFORE_SUBMISSION') = (refusal IS NOT NULL)),
    -- a PRICED proof names the exact symbol, epoch, book instant, age and
    -- the executable price, on an ESTABLISHED verdict with C12 passed
    CONSTRAINT p5_c12_decision_proof_priced_ck CHECK (
        proof_status <> 'PRICED_FROM_STREAM' OR (
            stream_symbol = us_market_slug
            AND connection_epoch IS NOT NULL
            AND book_received_at IS NOT NULL
            AND book_age_s IS NOT NULL
            AND decision_executable_price IS NOT NULL
            AND c12_passed IS TRUE
            AND p5_verdict = 'ESTABLISHED')),
    -- a refused stream book never made a live-eligible intent
    CONSTRAINT p5_c12_decision_proof_refused_not_live_ck CHECK (
        proof_status <> 'REFUSED_BEFORE_SUBMISSION' OR NOT live_eligible)
);
CREATE INDEX IF NOT EXISTS p5_c12_decision_proof_at_idx
    ON p5_c12_decision_proof (recorded_at DESC);
