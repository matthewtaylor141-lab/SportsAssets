-- ══════════════════════════════════════════════════════════════════════
-- 217 · EDDIE (HEAD OF EXECUTION) AND SCOUT (MARKET INTELLIGENCE):
--       TWO PERSISTED AGENTS, SHADOW / RESEARCH ONLY, WITH NO VENUE OR
--       CAPITAL AUTHORITY -- ENFORCED HERE, INDEPENDENTLY OF THE CODE
-- ══════════════════════════════════════════════════════════════════════
--
-- EDDIE preserves Derek's theoretical edge between decision and fill. He
-- does NOT predict outcomes. For each Derek candidate he records a SHADOW
-- execution estimate (theoretical edge, fees, spread, slippage, adverse
-- selection, fill probability, time to fill, capital-hours, maximum
-- economically executable size, expected net executable edge) and a SHADOW
-- recommendation (EXECUTE_NOW / REST_LIMIT / SPLIT / WAIT / SKIP_EXECUTION).
-- After a paper or actual fill he records PREDICTED vs REALIZED execution
-- loss. Authority: SHADOW_ONLY.
--
-- SCOUT discovers external information. Every feature carries its source,
-- timestamps, event identity, confidence, freshness, provenance and a
-- licensing classification; only a source that passed the declared
-- compliance check is ingested. A feature is tested PROSPECTIVELY against
-- the PinnAPI baseline (a frozen spec, a predeclared metric and minimum
-- sample, predictions frozen before outcomes) and is REJECTED without
-- incremental out-of-sample value. Scout cannot validate or promote his own
-- feature. Authority: RESEARCH_SHADOW_ONLY.
--
-- WHAT THIS MIGRATION ADDS
--   1. EDDIE and SCOUT as agent identities, persona owners and Slack bridge
--      identities (CHECKs widened; their own bot tokens or nothing).
--   2. The collaboration loop (203) admits EDDIE and SCOUT as proposers and
--      peers -- never at RELEASE_ELIGIBILITY (no promotion of any kind).
--   3. THE HARD RULE IN THE DATABASE: an estimate that recommends executing
--      (EXECUTE_NOW / REST_LIMIT / SPLIT) must carry a positive expected net
--      executable edge AND a positive expected executable EV -- whatever its
--      theoretical edge. Unmeasured is NULL with a named reason.
--   4. Scout's source registry (with its compliance record), feature
--      registry, observations, feature tournaments and frozen samples.
--   5. The candidate-review workflow: Derek candidate -> Karen challenge ->
--      Scout evidence -> Eddie execution estimate -> Allocator ranking ->
--      Audrey risk check -> Xavier management plan, one grounded,
--      append-only step each (question, agent, evidence, response,
--      disagreement, resolution, experiment, result).
--   6. NO AUTHORITY, IN THE DATABASE: a trigger on every approval /
--      activation / promotion / control / decision-of-record table AND on
--      every order, intent and fill table refuses EDDIE or SCOUT as the
--      acting identity -- named in an actor column, or declared as the
--      session's acting agent (bettor.acting_agent, which their runners set
--      on every transaction). agent_task_events refuses either moving a task
--      to APPROVAL_READY / APPROVED / RELEASED / ROLLED_BACK.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP-then-ADD. No BEGIN/COMMIT of
-- its own (the runner wraps each file in one transaction). It re-asserts the
-- widened CHECKs, so re-running an older migration (207 / 212) and then this
-- one restores them.

-- ── 1 · IDENTITIES, PERSONAS, SLACK ──────────────────────────────────
ALTER TABLE agent_identities
    DROP CONSTRAINT IF EXISTS agent_identities_agent_id_check;
ALTER TABLE agent_identities
    ADD CONSTRAINT agent_identities_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT'));

ALTER TABLE agent_persona_versions
    DROP CONSTRAINT IF EXISTS agent_persona_versions_agent_id_check;
ALTER TABLE agent_persona_versions
    ADD CONSTRAINT agent_persona_versions_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT'));
ALTER TABLE agent_chat_conversations
    DROP CONSTRAINT IF EXISTS agent_chat_conversations_agent_id_check;
ALTER TABLE agent_chat_conversations
    ADD CONSTRAINT agent_chat_conversations_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT'));

ALTER TABLE agent_slack_delivery
    DROP CONSTRAINT IF EXISTS agent_slack_delivery_agent_check;
ALTER TABLE agent_slack_delivery
    ADD CONSTRAINT agent_slack_delivery_agent_check
    CHECK (agent IN ('derek', 'xavier', 'audrey', 'karen', 'eddie',
                     'scout'));

-- ── 2 · THE COLLABORATION LOOP ───────────────────────────────────────
ALTER TABLE agent_findings DROP CONSTRAINT IF EXISTS agent_findings_proposer_ck;
ALTER TABLE agent_findings
    ADD CONSTRAINT agent_findings_proposer_ck CHECK (
        proposer IN ('DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'SCOUT'));
ALTER TABLE agent_finding_stages
    DROP CONSTRAINT IF EXISTS agent_finding_stages_actor_ck;
ALTER TABLE agent_finding_stages
    ADD CONSTRAINT agent_finding_stages_actor_ck CHECK (
        actor IN ('DEREK', 'XAVIER', 'AUDREY')
        OR (actor IN ('EDDIE', 'SCOUT') AND stage <> 'RELEASE_ELIGIBILITY')
        OR (actor = 'KAREN' AND stage = 'PEER_CHALLENGE'));

-- ── WHO IS "EDDIE" / "SCOUT" AS AN ACTING IDENTITY ───────────────────
-- The agent id, or a machine-style label (agent:eddie, slack:scout,
-- eddie-bot, scout research). A person called Eddie ("Eddie Ruiz") is NOT
-- matched.
CREATE OR REPLACE FUNCTION pos_agent_actor(v text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN v IS NULL THEN NULL
        WHEN upper(btrim(v)) = 'EDDIE'
          OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+EDDIE([^A-Z]|$)'
          OR upper(btrim(v)) ~ 'EDDIE[ ._:-]*(AGENT|BOT|EXECUTION)'
            THEN 'EDDIE'
        WHEN upper(btrim(v)) = 'SCOUT'
          OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+SCOUT([^A-Z]|$)'
          OR upper(btrim(v)) ~ 'SCOUT[ ._:-]*(AGENT|BOT|INTEL|RESEARCH)'
            THEN 'SCOUT'
        ELSE NULL END
$$;

-- The session's declared acting agent (the runners set it per transaction
-- with set_config('bettor.acting_agent', 'EDDIE', true)).
CREATE OR REPLACE FUNCTION pos_session_agent()
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT pos_agent_actor(current_setting('bettor.acting_agent', true))
$$;

CREATE OR REPLACE FUNCTION pos_refs_grounded(refs jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN refs IS NULL OR jsonb_typeof(refs) <> 'array' THEN false
        WHEN jsonb_array_length(refs) NOT BETWEEN 1 AND 50 THEN false
        ELSE NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(refs) e
             WHERE jsonb_typeof(e) <> 'object'
                OR coalesce(btrim(e->>'kind'), '') = ''
                OR coalesce(btrim(e->>'id'), '') = '')
    END
$$;

CREATE OR REPLACE FUNCTION pos_no_authority(doc jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT doc IS NULL OR jsonb_typeof(doc) <> 'object' OR NOT (
        doc ?| ARRAY['risk_limits', 'limits', 'capital_usd',
                     'max_downside_usd', 'max_incremental_capital_usd',
                     'per_order_usd', 'max_order_usd', 'daily_loss_stop_usd',
                     'credentials', 'api_key', 'account_authority',
                     'submission_enabled', 'approved', 'approved_by',
                     'approval', 'activate', 'activation', 'active',
                     'policy_activation', 'promote', 'promotion', 'release',
                     'order', 'submit', 'cancel', 'order_id_to_submit'])
$$;

CREATE OR REPLACE FUNCTION pos_compliance_all_true(doc jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_typeof(doc) = 'object'
       AND (SELECT count(*) FROM jsonb_each(doc)) > 0
       AND NOT EXISTS (SELECT 1 FROM jsonb_each(doc) c
                        WHERE c.value <> 'true'::jsonb)
$$;

-- ── 3 · EDDIE: SHADOW EXECUTION ESTIMATES ────────────────────────────
CREATE TABLE IF NOT EXISTS eddie_execution_estimates (
    estimate_id                      text        PRIMARY KEY,
    decision_id                      text        NOT NULL,
    estimator_version                text        NOT NULL,
    estimated_at                     timestamptz NOT NULL,
    decided_at                       timestamptz,
    us_market_slug                   text,
    holding_side                     text,
    proposed_qty                     numeric,
    limit_price                      numeric,
    probability                      double precision,
    probability_basis                text,
    book_obs_id                      bigint,
    book_age_s                       double precision,
    -- THE ESTIMATE (probability points per contract unless named otherwise)
    theoretical_edge_pp              double precision,
    spread_cost_pp                   double precision,
    expected_fees_pp                 double precision,
    expected_slippage_pp             double precision,
    expected_adverse_selection_pp    double precision,
    expected_execution_loss_pp       double precision,
    expected_net_executable_edge_pp  double precision,
    expected_fill_probability        double precision,
    expected_time_to_fill_s          double precision,
    expected_capital_hours           double precision,
    max_executable_qty               numeric,
    expected_executable_ev_usd       double precision,
    execution_style                  text,
    recommendation                   text        NOT NULL,
    recommendation_reason            text        NOT NULL,
    -- field -> named reason, for every estimate field that is NULL
    unmeasured                       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- the evaluation dimensions: MEASURED (with source) or UNAVAILABLE (why)
    dimensions                       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    inputs                           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs                    jsonb       NOT NULL,
    authority                        text        NOT NULL DEFAULT 'SHADOW_ONLY',
    production_effect                text        NOT NULL DEFAULT 'NONE',
    created_at                       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT eddie_estimates_once_ck UNIQUE (decision_id, estimator_version),
    CONSTRAINT eddie_estimates_authority_ck CHECK (authority = 'SHADOW_ONLY'),
    CONSTRAINT eddie_estimates_no_production_effect_ck CHECK (
        production_effect = 'NONE'),
    CONSTRAINT eddie_estimates_grounded_ck CHECK (
        pos_refs_grounded(evidence_refs)),
    CONSTRAINT eddie_estimates_no_authority_ck CHECK (
        pos_no_authority(inputs) AND pos_no_authority(dimensions)),
    CONSTRAINT eddie_estimates_recommendation_ck CHECK (
        recommendation IN ('EXECUTE_NOW', 'REST_LIMIT', 'SPLIT', 'WAIT',
                           'SKIP_EXECUTION')),
    CONSTRAINT eddie_estimates_style_ck CHECK (
        execution_style IS NULL
        OR execution_style IN ('TAKER_MARKETABLE', 'MAKER_RESTING',
                               'SPLIT_TAKER')),
    -- THE HARD RULE: never recommend executing a candidate whose expected
    -- executable EV is <= 0 (or unmeasured), whatever its theoretical EV.
    CONSTRAINT eddie_estimates_hard_rule_ck CHECK (
        recommendation NOT IN ('EXECUTE_NOW', 'REST_LIMIT', 'SPLIT')
        OR (expected_net_executable_edge_pp IS NOT NULL
            AND expected_net_executable_edge_pp > 0
            AND expected_executable_ev_usd IS NOT NULL
            AND expected_executable_ev_usd > 0
            AND expected_fill_probability IS NOT NULL
            AND expected_fill_probability > 0)),
    CONSTRAINT eddie_estimates_probability_ck CHECK (
        (expected_fill_probability IS NULL
         OR expected_fill_probability BETWEEN 0 AND 1)
        AND (probability IS NULL OR probability BETWEEN 0 AND 1)),
    CONSTRAINT eddie_estimates_nonneg_ck CHECK (
        (expected_time_to_fill_s IS NULL OR expected_time_to_fill_s >= 0)
        AND (expected_capital_hours IS NULL OR expected_capital_hours >= 0)
        AND (max_executable_qty IS NULL OR max_executable_qty >= 0)),
    -- UNMEASURED IS NULL WITH A REASON, never a silent zero
    CONSTRAINT eddie_estimates_unmeasured_named_ck CHECK (
        jsonb_typeof(unmeasured) = 'object'
        AND (theoretical_edge_pp IS NOT NULL OR unmeasured ? 'theoretical_edge')
        AND (expected_fees_pp IS NOT NULL OR unmeasured ? 'fees')
        AND (spread_cost_pp IS NOT NULL OR unmeasured ? 'spread_cost')
        AND (expected_slippage_pp IS NOT NULL OR unmeasured ? 'slippage')
        AND (expected_adverse_selection_pp IS NOT NULL
             OR unmeasured ? 'adverse_selection')
        AND (expected_fill_probability IS NOT NULL
             OR unmeasured ? 'fill_probability')
        AND (expected_time_to_fill_s IS NOT NULL
             OR unmeasured ? 'time_to_fill')
        AND (expected_capital_hours IS NOT NULL
             OR unmeasured ? 'capital_hours')
        AND (max_executable_qty IS NOT NULL OR unmeasured ? 'max_executable_size')
        AND (expected_net_executable_edge_pp IS NOT NULL
             OR unmeasured ? 'net_executable_edge'))
);
CREATE INDEX IF NOT EXISTS eddie_estimates_at_idx
    ON eddie_execution_estimates (estimated_at DESC);
CREATE INDEX IF NOT EXISTS eddie_estimates_decision_idx
    ON eddie_execution_estimates (decision_id);

-- PREDICTED vs REALIZED execution loss, after a paper or actual fill
CREATE TABLE IF NOT EXISTS eddie_execution_outcomes (
    outcome_id                     text        PRIMARY KEY,
    estimate_id                    text        NOT NULL
                                   REFERENCES eddie_execution_estimates,
    decision_id                    text        NOT NULL,
    source                         text        NOT NULL,
    measured_at                    timestamptz NOT NULL,
    filled_qty                     numeric,
    fill_vwap                      numeric,
    decision_mid                   double precision,
    realized_fees_pp               double precision,
    realized_spread_cost_pp        double precision,
    realized_slippage_pp           double precision,
    realized_adverse_selection_pp  double precision,
    realized_execution_loss_pp     double precision,
    predicted_execution_loss_pp    double precision,
    naive_execution_loss_pp        double precision,
    decision_to_submit_s           double precision,
    submit_to_ack_s                double precision,
    ack_to_fill_s                  double precision,
    decision_to_fill_s             double precision,
    capital_hours                  double precision,
    unmeasured                     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs                  jsonb       NOT NULL,
    production_effect              text        NOT NULL DEFAULT 'NONE',
    created_at                     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT eddie_outcomes_once_ck UNIQUE (estimate_id, source),
    CONSTRAINT eddie_outcomes_source_ck CHECK (source IN ('PAPER', 'ACTUAL')),
    CONSTRAINT eddie_outcomes_grounded_ck CHECK (
        pos_refs_grounded(evidence_refs)),
    CONSTRAINT eddie_outcomes_no_production_effect_ck CHECK (
        production_effect = 'NONE'),
    CONSTRAINT eddie_outcomes_unmeasured_named_ck CHECK (
        jsonb_typeof(unmeasured) = 'object'
        AND (realized_execution_loss_pp IS NOT NULL
             OR unmeasured ? 'realized_execution_loss')
        AND (predicted_execution_loss_pp IS NOT NULL
             OR unmeasured ? 'predicted_execution_loss'))
);

-- ── 4 · SCOUT: SOURCES, FEATURES, OBSERVATIONS, TOURNAMENTS ──────────
CREATE TABLE IF NOT EXISTS scout_sources (
    source_id          text        PRIMARY KEY,
    name               text        NOT NULL,
    url                text,
    access_method      text        NOT NULL,
    licensing_class    text        NOT NULL,
    usage_terms        text        NOT NULL,
    compliance         jsonb       NOT NULL,
    compliance_passed  boolean     NOT NULL,
    declared_by        text        NOT NULL,
    declared_at        timestamptz NOT NULL,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    CONSTRAINT scout_sources_licensing_ck CHECK (licensing_class IN (
        'OFFICIAL_PUBLIC_API_INTERNAL_RESEARCH_ONLY',
        'LICENSED_COMMERCIAL', 'OPEN_DATA_LICENSE', 'INTERNAL_RECORD',
        'UNKNOWN', 'PROHIBITED')),
    CONSTRAINT scout_sources_access_ck CHECK (access_method IN (
        'EXISTING_INGESTED_TABLE', 'LICENSED_FEED', 'INTERNAL_TABLE')),
    -- a source passes only with a known, permitted licence and every
    -- declared check true
    CONSTRAINT scout_sources_compliance_ck CHECK (
        jsonb_typeof(compliance) = 'object'
        AND (NOT compliance_passed OR (
            licensing_class NOT IN ('UNKNOWN', 'PROHIBITED')
            AND pos_compliance_all_true(compliance)))),
    CONSTRAINT scout_sources_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);

CREATE TABLE IF NOT EXISTS scout_features (
    feature_id              text        PRIMARY KEY,
    feature                 text        NOT NULL,
    source_id               text        NOT NULL REFERENCES scout_sources,
    licensing_class         text        NOT NULL,
    event_scope             text        NOT NULL,
    identity_basis          text        NOT NULL,
    expected_mechanism      text        NOT NULL,
    predeclared_hypothesis  text        NOT NULL,
    proposed_by             text        NOT NULL DEFAULT 'SCOUT',
    proposed_at             timestamptz NOT NULL,
    state                   text        NOT NULL DEFAULT 'PROPOSED',
    state_reason            text,
    state_set_by            text,
    state_set_at            timestamptz,
    forward_test_id         text,
    incremental_value       jsonb,
    adopted_by              text,
    adopted_at              timestamptz,
    production_effect       text        NOT NULL DEFAULT 'NONE',
    CONSTRAINT scout_features_name_ck UNIQUE (feature, source_id),
    CONSTRAINT scout_features_state_ck CHECK (state IN (
        'PROPOSED', 'UNDER_TEST', 'VALIDATED', 'REJECTED', 'ADOPTED')),
    CONSTRAINT scout_features_proposer_ck CHECK (proposed_by = 'SCOUT'),
    CONSTRAINT scout_features_text_ck CHECK (
        length(btrim(expected_mechanism)) BETWEEN 10 AND 4000
        AND length(btrim(predeclared_hypothesis)) BETWEEN 10 AND 4000),
    -- SCOUT CANNOT VALIDATE, REJECT-AS-EVALUATOR OR PROMOTE HIS OWN FEATURE
    CONSTRAINT scout_features_not_self_judged_ck CHECK (
        state IN ('PROPOSED', 'UNDER_TEST')
        OR (state_set_by IS NOT NULL
            AND pos_agent_actor(state_set_by) IS NULL
            AND state_set_at IS NOT NULL)),
    CONSTRAINT scout_features_adoption_ck CHECK (
        (state <> 'ADOPTED' AND adopted_by IS NULL AND adopted_at IS NULL)
        OR (state = 'ADOPTED' AND adopted_by IS NOT NULL
            AND pos_agent_actor(adopted_by) IS NULL
            AND upper(btrim(adopted_by)) NOT IN (
                'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CALIBRATION_ENGINE')
            AND adopted_at IS NOT NULL)),
    CONSTRAINT scout_features_verdict_needs_test_ck CHECK (
        state NOT IN ('VALIDATED', 'ADOPTED')
        OR (forward_test_id IS NOT NULL AND incremental_value IS NOT NULL)),
    CONSTRAINT scout_features_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);

CREATE TABLE IF NOT EXISTS scout_feature_observations (
    observation_id      text             PRIMARY KEY,
    feature_id          text             NOT NULL REFERENCES scout_features,
    source_id           text             NOT NULL REFERENCES scout_sources,
    source_timestamp    timestamptz      NOT NULL,
    observed_timestamp  timestamptz      NOT NULL,
    event_key           text             NOT NULL,
    identity            jsonb            NOT NULL,
    value               double precision,
    value_label         text,
    confidence          double precision NOT NULL,
    freshness_s         double precision NOT NULL,
    provenance          jsonb            NOT NULL,
    licensing_class     text             NOT NULL,
    production_effect   text             NOT NULL DEFAULT 'NONE',
    CONSTRAINT scout_obs_once_ck UNIQUE (feature_id, event_key, source_timestamp),
    CONSTRAINT scout_obs_time_ck CHECK (observed_timestamp >= source_timestamp),
    CONSTRAINT scout_obs_confidence_ck CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT scout_obs_freshness_ck CHECK (freshness_s >= 0),
    CONSTRAINT scout_obs_provenance_ck CHECK (pos_refs_grounded(provenance)),
    CONSTRAINT scout_obs_identity_ck CHECK (jsonb_typeof(identity) = 'object'),
    CONSTRAINT scout_obs_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS scout_obs_event_idx
    ON scout_feature_observations (feature_id, event_key);

CREATE TABLE IF NOT EXISTS scout_feature_tournaments (
    tournament_id     text             PRIMARY KEY,
    feature_id        text             NOT NULL REFERENCES scout_features,
    baseline          text             NOT NULL DEFAULT 'PINNAPI',
    challenger        text             NOT NULL,
    metric            text             NOT NULL,
    min_sample        integer          NOT NULL,
    min_improvement   double precision NOT NULL,
    adjustment        jsonb            NOT NULL,
    frozen_at         timestamptz      NOT NULL,
    frozen_by         text             NOT NULL,
    n                 integer,
    baseline_score    double precision,
    challenger_score  double precision,
    improvement       double precision,
    verdict           text,
    verdict_reason    text,
    evaluated_by      text,
    evaluated_at      timestamptz,
    result            jsonb,
    production_effect text             NOT NULL DEFAULT 'NONE',
    CONSTRAINT scout_tournaments_one_per_feature_ck UNIQUE (feature_id),
    CONSTRAINT scout_tournaments_baseline_ck CHECK (baseline = 'PINNAPI'),
    CONSTRAINT scout_tournaments_metric_ck CHECK (metric IN ('BRIER',
                                                              'LOG_LOSS')),
    CONSTRAINT scout_tournaments_sample_ck CHECK (min_sample BETWEEN 30 AND
                                                  100000),
    CONSTRAINT scout_tournaments_margin_ck CHECK (min_improvement > 0),
    CONSTRAINT scout_tournaments_verdict_ck CHECK (
        (verdict IS NULL AND evaluated_by IS NULL AND evaluated_at IS NULL)
        OR (verdict IN ('VALIDATED', 'REJECTED')
            AND evaluated_by IS NOT NULL
            AND pos_agent_actor(evaluated_by) IS NULL
            AND evaluated_at IS NOT NULL AND evaluated_at >= frozen_at
            AND n IS NOT NULL AND n >= min_sample
            AND baseline_score IS NOT NULL AND challenger_score IS NOT NULL
            AND improvement IS NOT NULL
            AND length(btrim(verdict_reason)) > 0)),
    -- VALIDATED only with the predeclared out-of-sample improvement
    CONSTRAINT scout_tournaments_validated_ck CHECK (
        verdict IS DISTINCT FROM 'VALIDATED' OR improvement >= min_improvement),
    CONSTRAINT scout_tournaments_no_authority_ck CHECK (
        pos_no_authority(adjustment) AND pos_no_authority(result)),
    CONSTRAINT scout_tournaments_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);

CREATE TABLE IF NOT EXISTS scout_tournament_samples (
    tournament_id   text             NOT NULL REFERENCES scout_feature_tournaments,
    event_key       text             NOT NULL,
    valuation_id    text             NOT NULL,
    predicted_at    timestamptz      NOT NULL,
    p_baseline      double precision NOT NULL,
    feature_value   double precision,
    p_challenger    double precision NOT NULL,
    outcome         smallint,
    outcome_at      timestamptz,
    recorded_at     timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (tournament_id, event_key),
    CONSTRAINT scout_samples_prob_ck CHECK (
        p_baseline > 0 AND p_baseline < 1
        AND p_challenger > 0 AND p_challenger < 1),
    CONSTRAINT scout_samples_outcome_ck CHECK (
        (outcome IS NULL AND outcome_at IS NULL)
        OR (outcome IN (0, 1) AND outcome_at IS NOT NULL
            AND outcome_at > predicted_at))
);

-- ── 5 · THE CANDIDATE-REVIEW WORKFLOW ────────────────────────────────
CREATE TABLE IF NOT EXISTS pos_candidate_reviews (
    review_id          text        PRIMARY KEY,
    decision_id        text        NOT NULL UNIQUE,
    opened_at          timestamptz NOT NULL,
    steps_recorded     integer     NOT NULL DEFAULT 0,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    CONSTRAINT pos_reviews_steps_ck CHECK (steps_recorded BETWEEN 0 AND 7),
    CONSTRAINT pos_reviews_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);

CREATE TABLE IF NOT EXISTS pos_candidate_review_steps (
    review_id       text        NOT NULL REFERENCES pos_candidate_reviews,
    seq             integer     NOT NULL,
    step            text        NOT NULL,
    agent           text        NOT NULL,
    question        text        NOT NULL,
    status          text        NOT NULL,
    evidence_refs   jsonb       NOT NULL,
    response        text        NOT NULL,
    disagreement    jsonb,
    resolution      text,
    experiment_ref  jsonb,
    result          jsonb,
    result_at       timestamptz,
    at              timestamptz NOT NULL,
    recorded_by     text        NOT NULL DEFAULT 'POS_WORKFLOW',
    production_effect text      NOT NULL DEFAULT 'NONE',
    PRIMARY KEY (review_id, seq),
    CONSTRAINT pos_steps_order_ck CHECK ((seq, step, agent) IN (
        (1, 'DEREK_CANDIDATE', 'DEREK'),
        (2, 'KAREN_CHALLENGE', 'KAREN'),
        (3, 'SCOUT_EVIDENCE', 'SCOUT'),
        (4, 'EDDIE_EXECUTION_ESTIMATE', 'EDDIE'),
        (5, 'ALLOCATOR_RANKING', 'CHIEF_ALLOCATOR'),
        (6, 'AUDREY_RISK_CHECK', 'AUDREY'),
        (7, 'XAVIER_MANAGEMENT_PLAN', 'XAVIER'))),
    CONSTRAINT pos_steps_status_ck CHECK (status IN (
        'ANSWERED', 'NO_RECORD', 'NOT_APPLICABLE')),
    CONSTRAINT pos_steps_grounded_ck CHECK (pos_refs_grounded(evidence_refs)),
    CONSTRAINT pos_steps_text_ck CHECK (
        length(btrim(question)) BETWEEN 1 AND 2000
        AND length(btrim(response)) BETWEEN 1 AND 4000),
    CONSTRAINT pos_steps_no_authority_ck CHECK (
        pos_no_authority(disagreement) AND pos_no_authority(result)
        AND pos_no_authority(experiment_ref)),
    CONSTRAINT pos_steps_result_ck CHECK ((result IS NULL) = (result_at IS NULL)),
    CONSTRAINT pos_steps_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);

-- ── GUARDS: FIXED AT CREATION, APPEND-ONLY, ORDERED ──────────────────
CREATE OR REPLACE FUNCTION pos_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%: a record is fixed at creation (% refused)',
        TG_TABLE_NAME, TG_OP;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS eddie_estimates_append_only_trg
    ON eddie_execution_estimates;
CREATE TRIGGER eddie_estimates_append_only_trg
    BEFORE UPDATE OR DELETE ON eddie_execution_estimates
    FOR EACH ROW EXECUTE FUNCTION pos_append_only();
DROP TRIGGER IF EXISTS eddie_outcomes_append_only_trg
    ON eddie_execution_outcomes;
CREATE TRIGGER eddie_outcomes_append_only_trg
    BEFORE UPDATE OR DELETE ON eddie_execution_outcomes
    FOR EACH ROW EXECUTE FUNCTION pos_append_only();
DROP TRIGGER IF EXISTS scout_obs_append_only_trg
    ON scout_feature_observations;
CREATE TRIGGER scout_obs_append_only_trg
    BEFORE UPDATE OR DELETE ON scout_feature_observations
    FOR EACH ROW EXECUTE FUNCTION pos_append_only();

-- a feature or an observation only from a source that PASSED compliance,
-- carrying that source's licensing classification
CREATE OR REPLACE FUNCTION scout_compliant_source_guard() RETURNS trigger
AS $$
DECLARE
    s scout_sources%ROWTYPE;
BEGIN
    SELECT * INTO s FROM scout_sources WHERE source_id = NEW.source_id;
    IF NOT FOUND OR NOT s.compliance_passed THEN
        RAISE EXCEPTION 'SCOUT_SOURCE_NOT_COMPLIANT: % has not passed the '
                        'declared compliance check; nothing is ingested',
                        NEW.source_id;
    END IF;
    IF NEW.licensing_class IS DISTINCT FROM s.licensing_class THEN
        RAISE EXCEPTION 'SCOUT_LICENSING_MISMATCH: % carries %, the source '
                        'is %', TG_TABLE_NAME, NEW.licensing_class,
                        s.licensing_class;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scout_features_source_trg ON scout_features;
CREATE TRIGGER scout_features_source_trg
    BEFORE INSERT ON scout_features
    FOR EACH ROW EXECUTE FUNCTION scout_compliant_source_guard();
DROP TRIGGER IF EXISTS scout_obs_source_trg ON scout_feature_observations;
CREATE TRIGGER scout_obs_source_trg
    BEFORE INSERT ON scout_feature_observations
    FOR EACH ROW EXECUTE FUNCTION scout_compliant_source_guard();

-- a source's compliance record is fixed once declared; a change is a new
-- source id
DROP TRIGGER IF EXISTS scout_sources_append_only_trg ON scout_sources;
CREATE TRIGGER scout_sources_append_only_trg
    BEFORE UPDATE OR DELETE ON scout_sources
    FOR EACH ROW EXECUTE FUNCTION pos_append_only();

-- THE FEATURE LIFECYCLE: PROPOSED -> UNDER_TEST -> VALIDATED | REJECTED ->
-- (VALIDATED) ADOPTED by a named human. The definition is fixed; VALIDATED
-- / REJECTED must equal the evaluator's recorded tournament verdict.
CREATE OR REPLACE FUNCTION scout_features_guard() RETURNS trigger AS $$
DECLARE
    t scout_feature_tournaments%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scout_features: a feature is never deleted (%)',
            OLD.feature_id;
    END IF;
    IF NEW.feature_id IS DISTINCT FROM OLD.feature_id
       OR NEW.feature IS DISTINCT FROM OLD.feature
       OR NEW.source_id IS DISTINCT FROM OLD.source_id
       OR NEW.licensing_class IS DISTINCT FROM OLD.licensing_class
       OR NEW.event_scope IS DISTINCT FROM OLD.event_scope
       OR NEW.identity_basis IS DISTINCT FROM OLD.identity_basis
       OR NEW.expected_mechanism IS DISTINCT FROM OLD.expected_mechanism
       OR NEW.predeclared_hypothesis IS DISTINCT FROM
          OLD.predeclared_hypothesis
       OR NEW.proposed_by IS DISTINCT FROM OLD.proposed_by
       OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at THEN
        RAISE EXCEPTION 'scout_features: % is fixed at creation (the '
                        'hypothesis is predeclared)', OLD.feature_id;
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state THEN
        IF NOT ((OLD.state = 'PROPOSED' AND NEW.state IN ('UNDER_TEST',
                                                           'REJECTED'))
                OR (OLD.state = 'UNDER_TEST'
                    AND NEW.state IN ('VALIDATED', 'REJECTED'))
                OR (OLD.state = 'VALIDATED' AND NEW.state = 'ADOPTED')) THEN
            RAISE EXCEPTION 'scout_features: % -> % is not a feature '
                            'transition', OLD.state, NEW.state;
        END IF;
        IF NEW.state IN ('VALIDATED', 'REJECTED') AND OLD.state = 'UNDER_TEST'
        THEN
            SELECT * INTO t FROM scout_feature_tournaments
             WHERE feature_id = NEW.feature_id;
            IF NOT FOUND OR t.verdict IS DISTINCT FROM NEW.state
               OR NEW.forward_test_id IS DISTINCT FROM t.tournament_id THEN
                RAISE EXCEPTION 'scout_features: % follows only the '
                                'evaluator''s recorded tournament verdict',
                                NEW.state;
            END IF;
        END IF;
        IF NEW.state = 'UNDER_TEST' AND NOT EXISTS (
               SELECT 1 FROM scout_feature_tournaments
                WHERE feature_id = NEW.feature_id
                  AND tournament_id = NEW.forward_test_id) THEN
            RAISE EXCEPTION 'scout_features: UNDER_TEST needs a frozen '
                            'forward test';
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scout_features_guard_trg ON scout_features;
CREATE TRIGGER scout_features_guard_trg
    BEFORE UPDATE OR DELETE ON scout_features
    FOR EACH ROW EXECUTE FUNCTION scout_features_guard();

-- THE FROZEN SPEC: nothing of the spec changes after freezing; the verdict
-- is recorded once, by someone other than Scout.
CREATE OR REPLACE FUNCTION scout_tournaments_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scout_feature_tournaments: never deleted (%)',
            OLD.tournament_id;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.verdict IS NOT NULL OR NEW.n IS NOT NULL THEN
            RAISE EXCEPTION 'scout_feature_tournaments: a tournament starts '
                            'frozen and unevaluated';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.tournament_id IS DISTINCT FROM OLD.tournament_id
       OR NEW.feature_id IS DISTINCT FROM OLD.feature_id
       OR NEW.baseline IS DISTINCT FROM OLD.baseline
       OR NEW.challenger IS DISTINCT FROM OLD.challenger
       OR NEW.metric IS DISTINCT FROM OLD.metric
       OR NEW.min_sample IS DISTINCT FROM OLD.min_sample
       OR NEW.min_improvement IS DISTINCT FROM OLD.min_improvement
       OR NEW.adjustment IS DISTINCT FROM OLD.adjustment
       OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at
       OR NEW.frozen_by IS DISTINCT FROM OLD.frozen_by THEN
        RAISE EXCEPTION 'scout_feature_tournaments: the spec of % is frozen',
            OLD.tournament_id;
    END IF;
    IF OLD.verdict IS NOT NULL THEN
        RAISE EXCEPTION 'scout_feature_tournaments: the verdict of % is '
                        'recorded once', OLD.tournament_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scout_tournaments_guard_trg ON scout_feature_tournaments;
CREATE TRIGGER scout_tournaments_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON scout_feature_tournaments
    FOR EACH ROW EXECUTE FUNCTION scout_tournaments_guard();

-- A SAMPLE IS PROSPECTIVE: predicted at/after the freeze, its prediction
-- fixed; only the outcome is added, once, after the prediction.
CREATE OR REPLACE FUNCTION scout_samples_guard() RETURNS trigger AS $$
DECLARE
    fz timestamptz;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scout_tournament_samples: never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT frozen_at INTO fz FROM scout_feature_tournaments
         WHERE tournament_id = NEW.tournament_id;
        IF NEW.predicted_at < fz THEN
            RAISE EXCEPTION 'scout_tournament_samples: a prediction before '
                            'the freeze (%) is not out of sample', fz;
        END IF;
        IF NEW.outcome IS NOT NULL THEN
            RAISE EXCEPTION 'scout_tournament_samples: a sample is frozen '
                            'before its outcome is known';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.tournament_id IS DISTINCT FROM OLD.tournament_id
       OR NEW.event_key IS DISTINCT FROM OLD.event_key
       OR NEW.valuation_id IS DISTINCT FROM OLD.valuation_id
       OR NEW.predicted_at IS DISTINCT FROM OLD.predicted_at
       OR NEW.p_baseline IS DISTINCT FROM OLD.p_baseline
       OR NEW.feature_value IS DISTINCT FROM OLD.feature_value
       OR NEW.p_challenger IS DISTINCT FROM OLD.p_challenger
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at
       OR OLD.outcome IS NOT NULL THEN
        RAISE EXCEPTION 'scout_tournament_samples: the prediction is fixed; '
                        'the outcome is recorded once';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scout_samples_guard_trg ON scout_tournament_samples;
CREATE TRIGGER scout_samples_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON scout_tournament_samples
    FOR EACH ROW EXECUTE FUNCTION scout_samples_guard();

-- THE WORKFLOW STEPS: in order, never skipped, append-only (a step's result
-- is added once, later, when the outcome is measured)
CREATE OR REPLACE FUNCTION pos_steps_guard() RETURNS trigger AS $$
DECLARE
    have integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'pos_candidate_review_steps: never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT steps_recorded INTO have FROM pos_candidate_reviews
         WHERE review_id = NEW.review_id FOR UPDATE;
        IF NEW.seq <> coalesce(have, -1) + 1 THEN
            RAISE EXCEPTION 'pos_candidate_review_steps: step % cannot follow '
                            'step % (none is skipped)', NEW.seq, have;
        END IF;
        UPDATE pos_candidate_reviews SET steps_recorded = NEW.seq
         WHERE review_id = NEW.review_id;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - 'result' - 'result_at')
           IS DISTINCT FROM (to_jsonb(OLD) - 'result' - 'result_at')
       OR OLD.result IS NOT NULL THEN
        RAISE EXCEPTION 'pos_candidate_review_steps: a step is fixed; its '
                        'result is recorded once';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pos_steps_guard_trg ON pos_candidate_review_steps;
CREATE TRIGGER pos_steps_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON pos_candidate_review_steps
    FOR EACH ROW EXECUTE FUNCTION pos_steps_guard();

CREATE OR REPLACE FUNCTION pos_reviews_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'pos_candidate_reviews: never deleted';
    END IF;
    IF NEW.review_id IS DISTINCT FROM OLD.review_id
       OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
       OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
       OR NEW.steps_recorded < OLD.steps_recorded THEN
        RAISE EXCEPTION 'pos_candidate_reviews: % is fixed', OLD.review_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pos_reviews_guard_trg ON pos_candidate_reviews;
CREATE TRIGGER pos_reviews_guard_trg
    BEFORE UPDATE OR DELETE ON pos_candidate_reviews
    FOR EACH ROW EXECUTE FUNCTION pos_reviews_guard();

-- ── 6 · NO AUTHORITY, IN THE DATABASE ────────────────────────────────
-- One trigger function; its arguments are the table's acting-identity
-- columns. Either agent named in one of them, or declared as the session's
-- acting agent, refuses the write (INSERT, UPDATE or DELETE).
CREATE OR REPLACE FUNCTION pos_agents_refuse_authority() RETURNS trigger AS $$
DECLARE
    col  text;
    doc  jsonb;
    who  text;
BEGIN
    who := pos_session_agent();
    IF who IS NOT NULL THEN
        RAISE EXCEPTION '%_HAS_NO_AUTHORITY: the session acting as % cannot '
                        '% %', who, who, TG_OP, TG_TABLE_NAME;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    -- a table guarded by the session declaration only has no column
    -- arguments (TG_ARGV is then NULL, never looped over)
    IF TG_NARGS = 0 THEN
        RETURN NEW;
    END IF;
    doc := to_jsonb(NEW);
    FOREACH col IN ARRAY TG_ARGV LOOP
        who := pos_agent_actor(doc->>col);
        IF who IS NOT NULL THEN
            RAISE EXCEPTION '%_HAS_NO_AUTHORITY: % cannot be %.% -- % holds '
                            'no order, cancel, capital, approval, activation, '
                            'promotion or control authority', who, doc->>col,
                            TG_TABLE_NAME, col, who;
        END IF;
    END LOOP;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

-- The guarded tables and their acting-identity columns: every approval /
-- activation / promotion / release / control / decision-of-record table
-- (the same list as Karen's), and every order, intent and fill table. A
-- table absent in a given database is skipped (guarded by a later re-run).
CREATE OR REPLACE FUNCTION pos_agents_authority_guarded_tables()
RETURNS TABLE (tbl text, cols text[], kind text) LANGUAGE sql IMMUTABLE AS $$
    VALUES
      ('agent_policy_versions',              ARRAY['created_by', 'approved_by'], 'APPROVAL'),
      ('agent_policy_artifacts',             ARRAY['created_by', 'owner_approval_actor'], 'APPROVAL'),
      ('live_rule_artifacts',                ARRAY['created_by', 'owner_approval_actor'], 'APPROVAL'),
      ('bettor_funded_models',               ARRAY['approved_by'], 'APPROVAL'),
      ('calibration_lifecycles',             ARRAY['approved_by'], 'APPROVAL'),
      ('improvement_candidates',             ARRAY['proposed_by', 'approved_by'], 'PROMOTION'),
      ('improvement_releases',               ARRAY['released_by'], 'PROMOTION'),
      ('paper_improvement_proposals',        ARRAY['proposed_by', 'activated_by'], 'ACTIVATION'),
      ('paper_policy_parameter_activations', ARRAY['actor'], 'ACTIVATION'),
      ('paper_policy_parameter_versions',    ARRAY['approved_by'], 'APPROVAL'),
      ('execmirror_control',                 ARRAY['actor'], 'CONTROL'),
      ('kalshi_smalllive_control',           ARRAY['actor'], 'CONTROL'),
      ('agent_slack_control_audit',          ARRAY['actor'], 'CONTROL'),
      ('paper_control',                      ARRAY[]::text[], 'CONTROL'),
      ('bettor_funded_owner_authorization_audit',
                                             ARRAY['operator', 'authenticated_by'], 'CONTROL'),
      ('management_directive_events',        ARRAY['actor_label'], 'CONTROL'),
      ('derek_entry_decisions',              ARRAY['decided_by'], 'DECISION'),
      -- the order path: orders, intents, fills, their events and plans
      ('paper_orders',                       ARRAY['strategy', 'event_source'], 'ORDER'),
      ('paper_order_events',                 ARRAY['event_source'], 'ORDER'),
      ('paper_fills',                        ARRAY['strategy', 'event_source'], 'ORDER'),
      ('execution_intents',                  ARRAY['strategy'], 'ORDER'),
      ('bettor_funded_intents',              ARRAY[]::text[], 'ORDER'),
      ('bettor_funded_fills',                ARRAY[]::text[], 'ORDER'),
      ('live_orders',                        ARRAY['lane'], 'ORDER'),
      ('mirror_orders',                      ARRAY[]::text[], 'ORDER'),
      ('execmirror_orders',                  ARRAY['strategy'], 'ORDER'),
      ('execmirror_fills',                   ARRAY['source'], 'ORDER'),
      ('kalshi_live_intents',                ARRAY[]::text[], 'ORDER'),
      ('kalshi_live_fills',                  ARRAY['source'], 'ORDER'),
      ('bettor_desk_orders',                 ARRAY[]::text[], 'ORDER'),
      ('bettor_desk_order_events',           ARRAY[]::text[], 'ORDER'),
      ('bettor_desk_fills',                  ARRAY[]::text[], 'ORDER'),
      ('bettor_standing_order_plans',        ARRAY[]::text[], 'ORDER'),
      ('bettor_standing_order_events',       ARRAY['source'], 'ORDER'),
      ('rn1x_orders',                        ARRAY[]::text[], 'ORDER'),
      ('rn1x_fills',                         ARRAY[]::text[], 'ORDER')
$$;

-- The trigger is named aa_... so it fires FIRST (PostgreSQL fires a
-- table's row triggers in name order): the refusal names the agent before
-- any table-specific guard (append-only, lifecycle) answers.
-- 217's first draft named it pos_agents_no_authority_trg: drop that name.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT * FROM pos_agents_authority_guarded_tables() LOOP
        IF to_regclass(r.tbl) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS pos_agents_no_authority_trg '
                           'ON %I', r.tbl);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    r record;
    c text;
    usable text[];
BEGIN
    FOR r IN SELECT * FROM pos_agents_authority_guarded_tables() LOOP
        IF to_regclass(r.tbl) IS NOT NULL THEN
            -- only the columns this database's table actually has
            usable := ARRAY[]::text[];
            FOREACH c IN ARRAY r.cols LOOP
                IF EXISTS (SELECT 1 FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = r.tbl AND column_name = c) THEN
                    usable := usable || c;
                END IF;
            END LOOP;
            EXECUTE format('DROP TRIGGER IF EXISTS aa_pos_agents_no_authority_trg '
                           'ON %I', r.tbl);
            EXECUTE format(
                'CREATE TRIGGER aa_pos_agents_no_authority_trg BEFORE INSERT OR '
                'UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION '
                'pos_agents_refuse_authority(%s)', r.tbl,
                coalesce((SELECT string_agg(quote_literal(x), ', ')
                            FROM unnest(usable) x), ''));
        END IF;
    END LOOP;
END $$;

-- Eddie and Scout may write tasks (write.agent_tasks) but never move one
-- into an approval, release or rollback state.
CREATE OR REPLACE FUNCTION pos_task_events_no_authority() RETURNS trigger
AS $$
DECLARE
    who text;
BEGIN
    who := coalesce(pos_agent_actor(NEW.actor), pos_session_agent());
    IF who IS NOT NULL
       AND (NEW.detail->>'status_to' IN ('APPROVAL_READY', 'APPROVED',
                                         'RELEASED', 'ROLLED_BACK')
            OR upper(NEW.kind) IN ('APPROVED', 'APPROVAL', 'RELEASED',
                                   'RELEASE', 'ACTIVATED', 'PROMOTED',
                                   'ROLLED_BACK')) THEN
        RAISE EXCEPTION '%_HAS_NO_AUTHORITY: % cannot move task % to % (%)',
                        who, who, NEW.task_id,
                        coalesce(NEW.detail->>'status_to', NEW.kind), NEW.kind;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pos_task_events_no_authority_trg ON agent_task_events;
CREATE TRIGGER pos_task_events_no_authority_trg
    BEFORE INSERT ON agent_task_events
    FOR EACH ROW EXECUTE FUNCTION pos_task_events_no_authority();

-- ── 7 · THE INTERFACE VIEWS OTHER STREAMS READ (read-only) ───────────
-- (a) the edge-confidence layer reads eddie_execution_estimates by name for
--     `fill_probability` and `execution_uncertainty`: defined here, from
--     Eddie's own columns, never anything else --
--       fill_probability      = expected_fill_probability
--       execution_uncertainty = 1 - expected_fill_probability (the chance
--                               the planned execution does not fill); NULL
--                               when unmeasured
ALTER TABLE eddie_execution_estimates
    ADD COLUMN IF NOT EXISTS fill_probability double precision
    GENERATED ALWAYS AS (expected_fill_probability) STORED;
ALTER TABLE eddie_execution_estimates
    ADD COLUMN IF NOT EXISTS execution_uncertainty double precision
    GENERATED ALWAYS AS (1 - expected_fill_probability) STORED;

-- (b) pos_iface_eddie_execution: one row per estimate. BASELINE = the naive
--     execution (cross the spread now for the whole proposed size);
--     EDDIE = his SHADOW plan (the taker walk of his executable size, or
--     resting at the exit-side best price). Eddie's VWAP / fee are NULL
--     when his recommendation does not execute (WAIT / SKIP_EXECUTION):
--     his plan is not to fill.
CREATE OR REPLACE VIEW pos_iface_eddie_execution AS
SELECT e.estimate_id,
       e.decision_id,
       e.estimated_at                                        AS at,
       e.proposed_qty                                        AS qty,
       (e.inputs->'naive_walk'->>'vwap')::double precision   AS baseline_vwap,
       CASE WHEN e.recommendation IN ('EXECUTE_NOW', 'REST_LIMIT', 'SPLIT')
            THEN (e.inputs->>'planned_vwap')::double precision END
                                                             AS eddie_vwap,
       (e.inputs->>'fee_pp_taker')::double precision
           * e.proposed_qty::double precision                AS baseline_fee_usd,
       CASE WHEN e.recommendation IN ('EXECUTE_NOW', 'REST_LIMIT', 'SPLIT')
            THEN e.expected_fees_pp
                 * (e.inputs->>'planned_qty')::double precision END
                                                             AS eddie_fee_usd,
       (e.inputs->'styles'->'TAKER_MARKETABLE'->>'fill_probability')
           ::double precision                                AS baseline_fill_ratio,
       e.expected_fill_probability                           AS eddie_fill_ratio,
       e.recommendation,
       e.execution_style,
       e.expected_net_executable_edge_pp,
       e.expected_executable_ev_usd,
       e.authority
  FROM eddie_execution_estimates e;

-- (c) pos_iface_scout_feature_effects: one row per (feature, Derek paper
--     decision on a game Scout observed). p_without = the decision's
--     recorded PinnAPI (else blended) probability; p_with = the feature's
--     PREDECLARED adjustment applied to it, using only the latest
--     observation made at or before the decision. status = the feature's
--     state (UNDER_TEST features inform no live decision).
CREATE OR REPLACE VIEW pos_iface_scout_feature_effects AS
SELECT f.feature_id,
       d.decision_id,
       d.decided_at                                          AS at,
       coalesce(d.p_pinnacle, d.p_blended)                   AS p_without,
       CASE WHEN o.value >= 0.5
             AND t.adjustment->>'rule' = 'SHRINK_TOWARD_HALF_WHEN_FEATURE_IS_1'
            THEN 0.5 + (coalesce(d.p_pinnacle, d.p_blended) - 0.5)
                       * (1 - (t.adjustment->>'k')::double precision)
            ELSE coalesce(d.p_pinnacle, d.p_blended) END     AS p_with,
       f.state                                               AS status,
       o.observation_id,
       o.value                                               AS feature_value,
       t.tournament_id
  FROM scout_features f
  JOIN scout_feature_tournaments t ON t.feature_id = f.feature_id
  JOIN LATERAL (
        SELECT DISTINCT v.condition_id, v.us_market_slug
          FROM external_valuations v
         WHERE v.condition_id IS NOT NULL AND v.us_market_slug IS NOT NULL
           AND EXISTS (SELECT 1 FROM scout_feature_observations x
                        WHERE x.feature_id = f.feature_id
                          AND x.event_key = v.condition_id)) g ON true
  JOIN paper_decisions d ON d.us_market_slug = g.us_market_slug
  JOIN LATERAL (
        SELECT x.observation_id, x.value FROM scout_feature_observations x
         WHERE x.feature_id = f.feature_id AND x.event_key = g.condition_id
           AND x.source_timestamp <= d.decided_at
         ORDER BY x.source_timestamp DESC LIMIT 1) o ON true
 WHERE coalesce(d.p_pinnacle, d.p_blended) IS NOT NULL;

