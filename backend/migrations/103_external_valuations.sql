-- EXTERNAL BOOKMAKER VALUATIONS, and the refusals, which are the point.
--
-- A row is written whether or not the entry cleared. "No opportunity" is a
-- measurement and it needs the same durability as an opportunity, or the
-- only thing the command centre can ever show is the days we traded.
--
-- WHAT EACH COLUMN IS FOR, since the directive names them individually:
--
--   raw_odds        the bookmaker's decimal odds for the COMPLETE outcome
--                   set, stored as given. A probability with no odds behind
--                   it cannot be re-derived or disputed later.
--   book / provider who said it. `source_class` is EXTERNAL_BOOKMAKER_
--                   VALUATION and there is a CHECK on it, because the one
--                   thing this table must never accumulate is rows that a
--                   later reader mistakes for a trained model's output.
--   observed_at     the bookmaker's own stamp for the quote.
--   received_at     when WE got it. Both, because ageing against receipt
--                   time makes a stale quote look fresh the moment we
--                   happen to fetch it -- the engine's props audit.
--   mapping_*       which outcome this contract was mapped to and how. An
--                   EXACT_AFTER_NORMALISATION match is recorded so an
--                   ambiguous one can never be confused with a confident
--                   one after the fact.
--   devig_method    declared per row. Two methods exist and which one
--                   calibrates better is an open question, so a row that
--                   does not say which was used is unusable for answering
--                   it.
--   probability     the de-vigged number for the mapped outcome.
--   executable_price the SAME-VENUE ask actually available. Not a midpoint
--                   and not a resting price: a resting price invents a
--                   queue position we never held.
--   cost_per_contract, estimated_edge_per_contract
--                   probability - ask - cost, stored rather than recomputed,
--                   so the edge the decision was made on survives a later
--                   change to the fee schedule.
--   decision        BUY or NO_TRADE. There is no third value: this
--                   experiment has no exit logic and touches no position.
--   refusals        EVERY reason, not the first. A single first-failure
--                   column would hide that an opportunity missed on four
--                   requirements at once.
--   outcome_*       filled in later, by UPDATE, once the event settles.
--                   NULL at insert and a trigger enforces it, exactly as
--                   rn1x_model_predictions does: a row that arrives with
--                   its outcome attached is a description of the past.
--
-- order_submitted is NOT NULL DEFAULT FALSE with a CHECK that it is FALSE.
-- Funded trading is disabled and this table is not the place it changes;
-- a schema that can only store `false` cannot be talked into storing
-- `true` by a future caller.

CREATE TABLE IF NOT EXISTS external_valuations (
    id                bigserial   PRIMARY KEY,
    experiment_id     text        NOT NULL,
    version           text        NOT NULL,
    source_class      text        NOT NULL,
    provider          text        NOT NULL,
    book              text        NOT NULL,
    devig_method      text        NOT NULL,

    -- what we would have bought
    venue             text        NOT NULL,
    condition_id      text,
    contract_selection text       NOT NULL,
    sport_family      text        NOT NULL,
    market            text        NOT NULL,
    period            text,
    line              double precision,
    settlement_rule   text,
    event_key         text,

    -- the quote
    raw_odds          jsonb       NOT NULL,
    outcomes_priced   integer     NOT NULL,
    expected_outcomes integer     NOT NULL,
    overround         double precision,
    observed_at       timestamptz,
    received_at       timestamptz,
    age_s             double precision,
    outcome_books     integer,

    -- the mapping
    mapped_outcome    text,
    mapping_match     text,

    -- the comparison
    probability       double precision,
    executable_price  double precision,
    cost_per_contract double precision,
    estimated_edge_per_contract double precision,

    -- the decision
    decision          text        NOT NULL,
    admissible        boolean     NOT NULL,
    refusals          text[]      NOT NULL DEFAULT '{}',
    why               text        NOT NULL DEFAULT '',
    proposed_size     double precision,
    order_submitted   boolean     NOT NULL DEFAULT FALSE,

    decided_at        timestamptz NOT NULL DEFAULT now(),

    -- the outcome, later
    outcome_known     boolean     NOT NULL DEFAULT FALSE,
    outcome           integer,
    outcome_at        timestamptz,
    realised_net_usd  double precision,

    CONSTRAINT external_valuations_source_class_is_external
        CHECK (source_class = 'EXTERNAL_BOOKMAKER_VALUATION'),

    CONSTRAINT external_valuations_decision_known
        CHECK (decision IN ('BUY', 'NO_TRADE')),

    CONSTRAINT external_valuations_method_declared
        CHECK (devig_method IN ('power', 'multiplicative')),

    -- Shadow only, enforced by the column's domain and not by a promise.
    CONSTRAINT external_valuations_never_submits
        CHECK (order_submitted = FALSE),

    CONSTRAINT external_valuations_probability_is_a_probability
        CHECK (probability IS NULL
               OR (probability >= 0.0 AND probability <= 1.0)),

    -- An admissible row must carry the numbers it was admitted on. This is
    -- the constraint that stops a BUY with no price behind it.
    CONSTRAINT external_valuations_admissible_is_complete
        CHECK (NOT admissible
               OR (probability IS NOT NULL
                   AND executable_price IS NOT NULL
                   AND cost_per_contract IS NOT NULL
                   AND estimated_edge_per_contract IS NOT NULL
                   AND mapped_outcome IS NOT NULL
                   AND observed_at IS NOT NULL)),

    CONSTRAINT external_valuations_decision_matches_admissible
        CHECK ((admissible AND decision = 'BUY')
               OR (NOT admissible AND decision = 'NO_TRADE')),

    CONSTRAINT external_valuations_outcome_matches_flag
        CHECK ((outcome_known = FALSE AND outcome IS NULL
                AND outcome_at IS NULL)
               OR (outcome_known = TRUE AND outcome IN (0, 1)
                   AND outcome_at IS NOT NULL)),

    CONSTRAINT external_valuations_outcome_is_later
        CHECK (outcome_at IS NULL OR outcome_at >= decided_at)
);

CREATE INDEX IF NOT EXISTS external_valuations_recent_idx
    ON external_valuations (experiment_id, decided_at DESC);

CREATE INDEX IF NOT EXISTS external_valuations_pending_idx
    ON external_valuations (experiment_id, outcome_known, decided_at);

-- The outcome may only arrive AFTERWARDS. A CHECK cannot tell an INSERT
-- from an UPDATE, and this table's whole value as calibration evidence
-- depends on the probability being recorded before the result is known.
CREATE OR REPLACE FUNCTION external_valuations_must_be_prospective()
RETURNS trigger AS $$
BEGIN
    IF NEW.outcome_known OR NEW.outcome IS NOT NULL
       OR NEW.outcome_at IS NOT NULL THEN
        RAISE EXCEPTION
            'an external valuation must be recorded BEFORE its outcome: '
            'insert with outcome_known = false and join the result by UPDATE'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS external_valuations_prospective_only
    ON external_valuations;
CREATE TRIGGER external_valuations_prospective_only
    BEFORE INSERT ON external_valuations
    FOR EACH ROW EXECUTE FUNCTION external_valuations_must_be_prospective();
