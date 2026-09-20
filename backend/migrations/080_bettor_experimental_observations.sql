-- THE EXPERIMENTAL LANE'S OWN OBSERVATIONS.
--
-- Owner directive 2026-09-19 21:2xZ (two lanes) and 22:4xZ §6.
--
-- WHY THIS TABLE HAD TO EXIST, found from production rather than
-- reasoned about. At 00:01Z the collector's corrected rows read:
--
--   opportunities_1h|YES_CONTRACT_BOOK|...|n=17|symbols=17
--
-- Seventeen rows across seventeen symbols: ONE SAMPLE PER MARKET PER
-- HOUR. The decision-grade collector selects its subjects with
-- `ORDER BY updated_at DESC LIMIT 10` over a board that churns, so it
-- almost never looks at the same market twice. X1's frozen rule is the
-- signed drift of the mid over the last five captured samples with a
-- minimum of three, against a 60-second horizon -- so on that feed the
-- rule could never fire, and the lane would have sat at zero trades
-- reporting healthy forever.
--
-- THE FIX IS COLLECTION, NOT THE RULE. Lowering the sample minimum
-- after seeing that the rule cannot fire is tuning a frozen experiment
-- to produce activity, which §4 forbids outright ("Do not tune these
-- after seeing X1 P&L. If changed later: X1_V2"). Stretching the
-- window instead would feed a 60-second momentum rule samples an hour
-- apart and call the result short-horizon direction. Neither is
-- honest. What X1 declared it needs is successive observations of the
-- same market, so the lane samples a small focus set every tick and
-- writes the result HERE.
--
-- WHY NOT bettor_opportunities. That table is the DECISION-GRADE
-- lane's dataset: its counters, its orphan sweep and its pipeline
-- health all read it. Writing a research lane's rows into it would
-- blend the two lanes' accounting in exactly the way the directive
-- forbids -- and the blending would be invisible, because the rows
-- would look like every other row. Two lanes, two tables.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_experimental_observations (
    experimental_observation_id TEXT PRIMARY KEY,

    observed_at             TIMESTAMPTZ NOT NULL,
    symbol                  TEXT NOT NULL,
    outcome_leg             TEXT,
    event_id                TEXT,

    -- HOW THIS MARKET CAME TO BE LOOKED AT. A dataset whose selection
    -- rule is unrecorded cannot be reasoned about later: every
    -- measurement over it is conditioned on a filter nobody can state.
    selection_reason        TEXT NOT NULL,
    evidence_source         TEXT NOT NULL,
    cadence_s               INTEGER,

    -- An unreadable book at a known instant is evidence and is kept.
    -- Dropping it would leave a hole indistinguishable from a market
    -- nobody looked at.
    readable                BOOLEAN NOT NULL,
    why_unreadable          TEXT,

    bid                     DOUBLE PRECISION,
    ask                     DOUBLE PRECISION,
    mid                     DOUBLE PRECISION,
    spread                  DOUBLE PRECISION,
    spread_relative         DOUBLE PRECISION,
    venue_state             TEXT,

    -- Which leg this book actually describes, and which collector
    -- semantics produced it. §2/§3.
    bbo_binding             TEXT NOT NULL,
    feature_source_version  TEXT NOT NULL,
    microstructure          JSONB NOT NULL,

    written_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS bettor_experimental_observations_immutable
    ON bettor_experimental_observations;
CREATE TRIGGER bettor_experimental_observations_immutable
    BEFORE UPDATE OR DELETE ON bettor_experimental_observations
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

CREATE INDEX IF NOT EXISTS bettor_exp_obs_symbol_idx
    ON bettor_experimental_observations (symbol, observed_at DESC);
CREATE INDEX IF NOT EXISTS bettor_exp_obs_at_idx
    ON bettor_experimental_observations (observed_at DESC);

-- The subject a seal and its decision were taken on. Kept beside the
-- decision-grade lane's own id rather than replacing it: a row carries
-- whichever it actually came from, and a reader can always tell which
-- feed a decision was made on.
ALTER TABLE bettor_experimental_seals
    ADD COLUMN IF NOT EXISTS experimental_observation_id TEXT;
ALTER TABLE bettor_experimental_decisions
    ADD COLUMN IF NOT EXISTS experimental_observation_id TEXT;

CREATE INDEX IF NOT EXISTS bettor_exp_seals_observation_idx
    ON bettor_experimental_seals (experimental_observation_id);

-- THE NEW COLUMN JOINS THE SEAL'S IMMUTABLE BODY. A column added
-- after the trigger was written is a column the trigger does not
-- guard, and the subject a decision was taken on is exactly the kind
-- of field that must not move afterwards -- it is how the features
-- are re-derived.
CREATE OR REPLACE FUNCTION bettor_experimental_seal_body_immutable()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'refused: a sealed experimental decision is never deleted '
            '(%)', OLD.experimental_decision_id;
    END IF;
    IF NEW.seal IS DISTINCT FROM OLD.seal
       OR NEW.seal_sha IS DISTINCT FROM OLD.seal_sha
       OR NEW.action IS DISTINCT FROM OLD.action
       OR NEW.sealed_at IS DISTINCT FROM OLD.sealed_at
       OR NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
       OR NEW.experiment_sha IS DISTINCT FROM OLD.experiment_sha
       OR NEW.symbol IS DISTINCT FROM OLD.symbol
       OR NEW.bettor_opportunity_id IS DISTINCT FROM
          OLD.bettor_opportunity_id
       OR NEW.experimental_observation_id IS DISTINCT FROM
          OLD.experimental_observation_id
       OR NEW.eligible_population_id IS DISTINCT FROM
          OLD.eligible_population_id THEN
        RAISE EXCEPTION
            'refused: the sealed decision % may not be altered after T0; '
            'only status, claimed_at, l2_request_id may be set',
            OLD.experimental_decision_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
