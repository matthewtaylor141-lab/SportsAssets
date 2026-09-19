-- TWO SHADOW LANES, PERMANENTLY SEPARATED.
--
-- Owner directive 2026-09-19: build both, do not choose mirror-only, and
-- do not invent P_BETTOR merely to make the BETTOR lane produce trades.
--
--   LANE A  RN1_SHADOW        what BETTOR's own latency, execution,
--                             pairing, cash-out and inventory logic
--                             would have done observing RN1. A
--                             MECHANISM benchmark. P_BETTOR and
--                             INFORMATION_EV are NOT_ESTABLISHED here
--                             and a fair value is never manufactured
--                             from RN1 activity.
--
--   LANE B  BETTOR_EV_SHADOW  the proprietary intelligence lane. RN1 is
--                             excluded from it unless a model is
--                             explicitly registered as an RN1
--                             specialist.
--
-- ADDITIVE, and deliberately a separate migration from 068: that one may
-- already be applied, and editing an applied migration changes nothing
-- while looking like it changed something.
--
-- WHY THE LANE IS NOT NULL WITH NO DEFAULT. A default would let an
-- un-tagged decision land in whichever lane the column happened to
-- prefer, and attribution lost at write time cannot be recovered by any
-- later query. Every row states its lane or is refused.

BEGIN;

-- ── lane tagging ────────────────────────────────────────────────────

ALTER TABLE shadow_decisions
    ADD COLUMN IF NOT EXISTS lane TEXT,
    ADD COLUMN IF NOT EXISTS signal_source TEXT,
    -- the three probability objects, kept distinct. A STATUS beside the
    -- value so "not established" can never be read as "zero".
    ADD COLUMN IF NOT EXISTS p_bettor_status TEXT,
    ADD COLUMN IF NOT EXISTS p_fill_status TEXT,
    -- what the features were and where each one came from
    ADD COLUMN IF NOT EXISTS feature_lineage JSONB,
    ADD COLUMN IF NOT EXISTS rn1_features_used BOOLEAN,
    ADD COLUMN IF NOT EXISTS specialist_outputs JSONB,
    ADD COLUMN IF NOT EXISTS action_ev_components JSONB,
    ADD COLUMN IF NOT EXISTS action_ev_status TEXT;

UPDATE shadow_decisions SET lane = 'RN1_SHADOW' WHERE lane IS NULL;

ALTER TABLE shadow_decisions
    ALTER COLUMN lane SET NOT NULL;

ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_lane;
ALTER TABLE shadow_decisions
    ADD CONSTRAINT shadow_decisions_lane
    CHECK (lane IN ('RN1_SHADOW', 'BETTOR_EV_SHADOW'));

-- THE LINEAGE RULE, ENFORCED IN THE SCHEMA.
--
-- A BETTOR_EV_SHADOW row may not claim RN1 features unless it also
-- declares that it is an RN1-registered specialist. Fail closed: NULL is
-- ambiguity, and ambiguity is refused.
ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_lineage;
ALTER TABLE shadow_decisions
    ADD CONSTRAINT shadow_decisions_lineage
    CHECK (
        lane <> 'BETTOR_EV_SHADOW'
        OR (rn1_features_used IS NOT NULL AND feature_lineage IS NOT NULL)
    );

-- RN1_SHADOW NEVER CARRIES AN INDEPENDENT BELIEF. "Do not manufacture a
-- fair value from RN1 activity" is a schema rule here, not a habit.
ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_rn1_no_belief;
ALTER TABLE shadow_decisions
    ADD CONSTRAINT shadow_decisions_rn1_no_belief
    CHECK (
        lane <> 'RN1_SHADOW'
        OR (p_bettor IS NULL AND information_ev IS NULL)
    );

CREATE INDEX IF NOT EXISTS shadow_decisions_lane_idx
    ON shadow_decisions (lane, decision_ts DESC);

ALTER TABLE shadow_positions
    ADD COLUMN IF NOT EXISTS lane TEXT;
UPDATE shadow_positions SET lane = 'RN1_SHADOW' WHERE lane IS NULL;
ALTER TABLE shadow_positions ALTER COLUMN lane SET NOT NULL;
ALTER TABLE shadow_positions
    DROP CONSTRAINT IF EXISTS shadow_positions_lane;
ALTER TABLE shadow_positions
    ADD CONSTRAINT shadow_positions_lane
    CHECK (lane IN ('RN1_SHADOW', 'BETTOR_EV_SHADOW'));

CREATE INDEX IF NOT EXISTS shadow_positions_lane_idx
    ON shadow_positions (lane);

-- ── the specialist registry ─────────────────────────────────────────
--
-- Interfaces now, models later. "Do not train new models merely because
-- the interfaces exist."

CREATE TABLE IF NOT EXISTS shadow_specialists (
    specialist_id   TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    version         TEXT NOT NULL,
    lane            TEXT NOT NULL,
    -- the ONLY way RN1 features may reach a BETTOR-lane model
    rn1_specialist  BOOLEAN NOT NULL DEFAULT FALSE,
    -- promotion stays prospective and gated
    promoted        BOOLEAN NOT NULL DEFAULT FALSE,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT shadow_specialist_kind CHECK (kind IN (
        'SETTLEMENT_FAIR_VALUE','SHORT_HORIZON_30S','SHORT_HORIZON_60S',
        'SHORT_HORIZON_300S','RELATIVE_VALUE','PAIR_COMPLETION',
        'TOXICITY','FILL','CASHOUT','INVENTORY','SPORT_SPECIFIC')),
    CONSTRAINT shadow_specialist_lane CHECK (lane IN (
        'RN1_SHADOW','BETTOR_EV_SHADOW'))
);

-- ── the disagreement dataset ────────────────────────────────────────
--
-- One row per contemporaneous opportunity. "Do not mine the result to
-- modify historical decisions" -- so this table references the two
-- decisions and never edits them.

CREATE TABLE IF NOT EXISTS shadow_disagreements (
    shadow_disagreement_id TEXT PRIMARY KEY,
    observed_at         TIMESTAMPTZ NOT NULL,
    symbol              TEXT NOT NULL,
    outcome_leg         TEXT NOT NULL,

    rn1_decision_id     TEXT REFERENCES shadow_decisions
                             (shadow_decision_id),
    bettor_decision_id  TEXT REFERENCES shadow_decisions
                             (shadow_decision_id),

    classification      TEXT NOT NULL,

    -- filled prospectively, AFTER the fact, in their own columns
    subsequent_move     NUMERIC,
    executable_markout  NUMERIC,
    settlement_result   TEXT,
    pair_opportunity    NUMERIC,
    rn1_shadow_pnl      NUMERIC,
    bettor_shadow_pnl   NUMERIC,
    outcome_recorded_at TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT shadow_disagreement_class CHECK (classification IN (
        'RN1_ONLY','BETTOR_ONLY','BOTH_AGREE','DISAGREE_DIRECTION',
        'BOTH_NO_TRADE','BETTOR_NOT_YET_ELIGIBLE'))
);

CREATE INDEX IF NOT EXISTS shadow_disagreements_class_idx
    ON shadow_disagreements (classification, observed_at DESC);

COMMIT;
