-- BETTOR'S OWN PROSPECTIVE DATASET. The primary product's evidence.
--
-- Owner clarification 2026-09-19: "BETTOR EV ENGINE IS THE PRIMARY
-- PRODUCT. RN1_SHADOW is a secondary benchmark/research lane." And:
-- "BETTOR_EV_SHADOW does NOT need P_BETTOR to begin accumulating
-- prospective evidence. Do not wait for RN1 to generate a BETTOR
-- observation."
--
-- THE POINT OF THIS TABLE. RN1's lane gets its subjects handed to it --
-- he acts, we observe. BETTOR has no such generator, and waiting for
-- one would make the primary product a passenger of the benchmark. So
-- BETTOR selects its own subjects from the venue's live universe on a
-- declared, frozen rule, captures the market state and features it
-- ACTUALLY HAD at that instant, and records a decision. Until
-- independent EV exists that decision is NO_TRADE with a named reason
-- -- and the directive is explicit that this is a valid BETTOR
-- decision, not an absence of one.
--
-- A REFUSAL IS THE DATASET. "A BETTOR opportunity rejected
-- prospectively is part of the proprietary dataset." The blockers are
-- the most valuable column here: they are what will eventually answer
-- whether BETTOR's refusals saved money.
--
-- THE INDEPENDENCE WALL IS STRUCTURAL ON THIS TABLE. rn1_features_used
-- is NOT NULL and CHECKed FALSE. A BETTOR opportunity that consumed an
-- RN1 feature cannot be written here at all -- not refused at runtime,
-- REJECTED BY THE DATABASE. "Do not weaken it to improve apparent
-- BETTOR performance" is not a policy if the schema permits it.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_opportunities (
    bettor_opportunity_id TEXT PRIMARY KEY,

    -- WHEN BETTOR LOOKED. Its own clock, and the only one it has here:
    -- unlike the RN1 lane there is no external actor whose instant we
    -- are chasing, so there is no second timestamp to keep apart.
    observed_at         TIMESTAMPTZ NOT NULL,
    written_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- the subject
    event_id            TEXT,
    market_id           TEXT,
    symbol              TEXT NOT NULL,
    outcome_leg         TEXT,
    sport               TEXT,
    league              TEXT,

    -- HOW THIS MARKET CAME TO BE LOOKED AT. A dataset whose selection
    -- rule is unrecorded cannot be reasoned about later: every
    -- measurement over it is conditioned on a filter nobody can state.
    universe_version    TEXT NOT NULL,
    universe_source     TEXT NOT NULL,
    selection_reason    TEXT NOT NULL,

    -- EVIDENCE SOURCE TRAVELS WITH EVERY ROW. "Do not silently
    -- substitute one market-data source for another." Today this is the
    -- retail book; when the institutional L2 overlap is established it
    -- becomes that, and rows drawn from each remain distinguishable
    -- forever because the name is ON the row.
    evidence_source     TEXT NOT NULL,
    market_state_id     TEXT REFERENCES shadow_market_states
                             (market_state_id),

    -- the feature bundles, kept apart by KIND rather than flattened,
    -- so a later attribution can say which family carried the signal
    microstructure      JSONB,
    relative_value      JSONB,
    external_consensus  JSONB,
    sport_features      JSONB,
    execution_features  JSONB,
    latency             JSONB,
    model_outputs       JSONB,

    -- THE WALL, IN THE SCHEMA.
    feature_lineage     JSONB NOT NULL,
    rn1_features_used   BOOLEAN NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT bettor_opportunity_independent
        CHECK (rn1_features_used IS FALSE)
);

CREATE INDEX IF NOT EXISTS bettor_opportunities_at_idx
    ON bettor_opportunities (observed_at DESC);
CREATE INDEX IF NOT EXISTS bettor_opportunities_symbol_idx
    ON bettor_opportunities (symbol, observed_at DESC);

DROP TRIGGER IF EXISTS bettor_opportunities_immutable
    ON bettor_opportunities;
CREATE TRIGGER bettor_opportunities_immutable
    BEFORE UPDATE OR DELETE ON bettor_opportunities
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- The decision points back at the opportunity that produced it, as the
-- RN1 lane's decision points back at its observation. Symmetry on
-- purpose: the two lanes are different products, not different
-- standards of evidence.
ALTER TABLE shadow_decisions
    ADD COLUMN IF NOT EXISTS bettor_opportunity_id TEXT
        REFERENCES bettor_opportunities (bettor_opportunity_id);

-- EVERY BETTOR_EV_SHADOW DECISION DESCENDS FROM AN OPPORTUNITY, exactly
-- as every RN1_SHADOW decision descends from an observation. A row with
-- nothing behind it is not in the lane, whatever it is tagged.
--
-- NOT VALID, and this one genuinely needs it: shadow_decisions may
-- already hold BETTOR-lane rows written before this column existed, and
-- a migration that refused to apply would take the whole boot with it.
-- New rows are checked from this moment; the old ones are visible as
-- the exception they are rather than being silently blessed.
ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_bettor_observed;
ALTER TABLE shadow_decisions
    ADD CONSTRAINT shadow_decisions_bettor_observed
    CHECK (lane <> 'BETTOR_EV_SHADOW' OR bettor_opportunity_id IS NOT NULL)
    NOT VALID;

CREATE INDEX IF NOT EXISTS shadow_decisions_opportunity_idx
    ON shadow_decisions (bettor_opportunity_id);

COMMIT;
