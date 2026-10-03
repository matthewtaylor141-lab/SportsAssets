-- 206: XAVIER'S MANAGEMENT RECORD -- IMMUTABLE ENTRY THESES, EVERY REVIEW'S
-- ASSESSMENT, AND THE VALUE-ADD AGAINST COUNTERFACTUALS FROZEN AT ENTRY.
--
-- Records only. Nothing here grants order authority, changes a limit or is
-- read by an order path: the paper review and the actual (small-live)
-- review WRITE these rows beside the records they already keep
-- (paper_xavier_reviews / smalllive_reviews); the Command Centre reads them.
--
--   xavier_entry_theses          ONE per position (paper or actual) written
--                                when the position comes into existence
--                                (first fill / handoff): the entry
--                                probability with its source stamp, the EV,
--                                assumptions, evidence references, the
--                                expiry (the Pinnacle 30 s rule for the
--                                probability, event start for a pre-event
--                                thesis) and the PREDECLARED counterfactuals
--                                (HOLD_TO_SETTLEMENT, IMMEDIATE_EXIT at the
--                                entry-time executable exit, ACTUAL_XAVIER).
--                                Never updated, never deleted.
--   xavier_management_assessments ONE per review: trigger, latency against
--                                the bound, the probability's evidence state
--                                (FRESH_CURRENT_PROBABILITY /
--                                STALE_ENTRY_TIME_PROBABILITY /
--                                PROBABILITY_UNAVAILABLE), the venue
--                                economics read, the thesis state
--                                (STILL_VALID / THESIS_CHANGED /
--                                EVIDENCE_EXPIRED / NO_ENTRY_THESIS), every
--                                alternative (HOLD / EXIT / REDUCE /
--                                VERIFIED_HEDGE / REALLOCATE, the last a
--                                SHADOW recommendation only) and the
--                                management-policy record the review ran
--                                under. A CHECK refuses a discretionary
--                                action (EXIT / REDUCE / REALLOCATE) on
--                                anything but a fresh probability. Append-only.
--   xavier_value_add             the counterfactual outcomes once known
--                                (exit / settlement), computed by the rules
--                                frozen on the thesis -- never a policy
--                                chosen in hindsight. A correction is a NEW
--                                row (its content hash differs). Append-only.
--
-- Rollback: migrations/rollback/206_xavier_management_thesis.down.sql.

CREATE TABLE IF NOT EXISTS xavier_entry_theses (
    thesis_id                 text PRIMARY KEY,
    position_kind             text        NOT NULL,
    group_id                  text        NOT NULL,
    position_ref              text        NOT NULL,
    decision_id               text,
    strategy                  text,
    us_market_slug            text        NOT NULL,
    holding_side              text        NOT NULL,
    entered_at                timestamptz NOT NULL,
    first_fill_at             timestamptz,
    entry_qty                 numeric(18,6) NOT NULL,
    entry_cost_per_contract   numeric(18,6),
    entry_cost_usd            numeric(18,6),
    entry_fees_usd            numeric(18,6),
    entry_probability         double precision,
    probability_source        text,
    probability_source_at     timestamptz,
    probability_limit_s       double precision NOT NULL,
    entry_ev_per_contract_usd double precision,
    entry_ev_usd              double precision,
    assumptions               jsonb       NOT NULL,
    evidence_refs             jsonb       NOT NULL,
    event_start_at            timestamptz,
    evidence_expires_at       timestamptz,
    thesis_expires_at         timestamptz,
    expiry_basis              text        NOT NULL,
    counterfactuals           jsonb       NOT NULL,
    content_sha256            text        NOT NULL,
    recorded_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT xavier_entry_theses_kind_ck CHECK (
        position_kind IN ('PAPER', 'ACTUAL')),
    CONSTRAINT xavier_entry_theses_side_ck CHECK (
        holding_side IN ('LONG', 'SHORT')),
    CONSTRAINT xavier_entry_theses_qty_ck CHECK (entry_qty > 0),
    CONSTRAINT xavier_entry_theses_p_ck CHECK (
        entry_probability IS NULL
        OR (entry_probability >= 0 AND entry_probability <= 1)),
    CONSTRAINT xavier_entry_theses_p_source_ck CHECK (
        entry_probability IS NULL OR probability_source IS NOT NULL),
    CONSTRAINT xavier_entry_theses_counterfactuals_ck CHECK (
        jsonb_typeof(counterfactuals) = 'object'
        AND counterfactuals ?& ARRAY['HOLD_TO_SETTLEMENT', 'IMMEDIATE_EXIT',
                                     'ACTUAL_XAVIER']),
    CONSTRAINT xavier_entry_theses_sha_ck CHECK (
        content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT xavier_entry_theses_one_per_position UNIQUE (position_kind,
                                                            group_id)
);
CREATE INDEX IF NOT EXISTS xavier_entry_theses_group
    ON xavier_entry_theses (group_id);

CREATE TABLE IF NOT EXISTS xavier_management_assessments (
    assessment_id             text PRIMARY KEY,
    position_kind             text        NOT NULL,
    group_id                  text        NOT NULL,
    review_id                 text        NOT NULL,
    thesis_id                 text REFERENCES xavier_entry_theses,
    assessed_at               timestamptz NOT NULL,
    trigger                   text        NOT NULL,
    due_at                    timestamptz,
    review_latency_s          double precision,
    latency_bound_s           double precision,
    within_bound              boolean,
    evidence_state            text        NOT NULL,
    probability               double precision,
    probability_source        text,
    probability_age_s         double precision,
    venue_economics           jsonb       NOT NULL,
    thesis_state              text        NOT NULL,
    thesis_detail             jsonb       NOT NULL,
    alternatives              jsonb       NOT NULL,
    recommendation            text,
    discretionary_permitted   boolean     NOT NULL,
    reallocate                jsonb       NOT NULL,
    policy                    jsonb       NOT NULL,
    recorded_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT xavier_assessments_kind_ck CHECK (
        position_kind IN ('PAPER', 'ACTUAL')),
    CONSTRAINT xavier_assessments_evidence_ck CHECK (evidence_state IN (
        'FRESH_CURRENT_PROBABILITY', 'STALE_ENTRY_TIME_PROBABILITY',
        'PROBABILITY_UNAVAILABLE')),
    CONSTRAINT xavier_assessments_thesis_ck CHECK (thesis_state IN (
        'STILL_VALID', 'THESIS_CHANGED', 'EVIDENCE_EXPIRED',
        'NO_ENTRY_THESIS')),
    CONSTRAINT xavier_assessments_unavailable_is_null_ck CHECK (
        evidence_state <> 'PROBABILITY_UNAVAILABLE' OR probability IS NULL),
    -- NO DISCRETIONARY ACTION ON A STALE OR ABSENT PROBABILITY
    CONSTRAINT xavier_assessments_fresh_for_discretion_ck CHECK (
        evidence_state = 'FRESH_CURRENT_PROBABILITY'
        OR (NOT discretionary_permitted
            AND (recommendation IS NULL
                 OR recommendation NOT IN ('EXIT', 'REDUCE', 'REALLOCATE')))),
    -- REALLOCATE IS A SHADOW RECOMMENDATION: it never places an order
    CONSTRAINT xavier_assessments_reallocate_shadow_ck CHECK (
        jsonb_typeof(reallocate) = 'object'
        AND coalesce(reallocate->>'mode', '') = 'SHADOW'),
    CONSTRAINT xavier_assessments_policy_ck CHECK (
        jsonb_typeof(policy) = 'object' AND policy ? 'status'
        AND (policy->>'status' <> 'APPROVED'
             OR (policy->>'approved_by' IS NOT NULL
                 AND policy->>'approved_at' IS NOT NULL
                 AND policy->>'sha256' IS NOT NULL)))
);
CREATE INDEX IF NOT EXISTS xavier_assessments_group
    ON xavier_management_assessments (group_id, position_kind,
                                      assessed_at DESC);
CREATE INDEX IF NOT EXISTS xavier_assessments_at
    ON xavier_management_assessments (assessed_at DESC);

CREATE TABLE IF NOT EXISTS xavier_value_add (
    value_add_id              text PRIMARY KEY,
    thesis_id                 text        NOT NULL REFERENCES xavier_entry_theses,
    position_kind             text        NOT NULL,
    group_id                  text        NOT NULL,
    status                    text        NOT NULL,
    outcome_basis             text        NOT NULL,
    outcome_evidence          jsonb       NOT NULL,
    counterfactuals           jsonb       NOT NULL,
    incremental               jsonb       NOT NULL,
    method                    text        NOT NULL,
    content_sha256            text        NOT NULL,
    computed_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT xavier_value_add_kind_ck CHECK (
        position_kind IN ('PAPER', 'ACTUAL')),
    CONSTRAINT xavier_value_add_status_ck CHECK (status IN (
        'FINAL', 'PENDING_SETTLEMENT_FOR_HOLD_COUNTERFACTUAL')),
    CONSTRAINT xavier_value_add_counterfactuals_ck CHECK (
        counterfactuals ?& ARRAY['HOLD_TO_SETTLEMENT', 'IMMEDIATE_EXIT',
                                 'ACTUAL_XAVIER']),
    CONSTRAINT xavier_value_add_sha_ck CHECK (
        content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT xavier_value_add_once UNIQUE (thesis_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS xavier_value_add_thesis
    ON xavier_value_add (thesis_id, computed_at DESC);

-- IMMUTABILITY: a thesis, an assessment and a value-add row are evidence;
-- none is ever rewritten or removed (a later fact is a new row).
CREATE OR REPLACE FUNCTION xavier_management_record_is_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is immutable: % refused (a later fact is a new row)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS xavier_entry_theses_immutable_trg
    ON xavier_entry_theses;
CREATE TRIGGER xavier_entry_theses_immutable_trg
    BEFORE UPDATE OR DELETE ON xavier_entry_theses
    FOR EACH ROW EXECUTE FUNCTION xavier_management_record_is_immutable();
DROP TRIGGER IF EXISTS xavier_management_assessments_immutable_trg
    ON xavier_management_assessments;
CREATE TRIGGER xavier_management_assessments_immutable_trg
    BEFORE UPDATE OR DELETE ON xavier_management_assessments
    FOR EACH ROW EXECUTE FUNCTION xavier_management_record_is_immutable();
DROP TRIGGER IF EXISTS xavier_value_add_immutable_trg ON xavier_value_add;
CREATE TRIGGER xavier_value_add_immutable_trg
    BEFORE UPDATE OR DELETE ON xavier_value_add
    FOR EACH ROW EXECUTE FUNCTION xavier_management_record_is_immutable();
