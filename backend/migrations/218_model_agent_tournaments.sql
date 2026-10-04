-- ══════════════════════════════════════════════════════════════════════
-- 218 · THE PROFITABILITY OPERATING SYSTEM, PART 3: EVALUATION AND LEARNING
--       (model tournament, agent tournament, edge-confidence meta-model,
--        avoidance model, causal experiment platform)
-- ══════════════════════════════════════════════════════════════════════
--
-- EVERYTHING HERE IS SHADOW / RESEARCH. The runner
-- (sportsassets/poslearn/runner.py) registers, forecasts, scores and
-- displays. It has NO authority over production probabilities, thresholds,
-- sizing, allowlists, orders or capital, and nothing in production reads a
-- poslearn_* table. The database says so where a column could otherwise be
-- talked into saying the opposite:
--
--   * every table carries label = 'SHADOW' (CHECK) and, where a row could
--     be mistaken for a decision, production_effect = 'NONE' or
--     applied = false (CHECK);
--   * a REGISTRATION (model, agent variant or meta-model) is an immutable,
--     SHA-256-hashed canonical document carrying its training window,
--     features, parameters, validation method, minimum sample, promotion
--     threshold, failure threshold and hypothesis family size. Content can
--     never change after insert; only its status moves, forward; a row is
--     never deleted; registered_at cannot be back-dated (trigger);
--   * a FORECAST is recorded BEFORE its outcome: the trigger refuses it when
--     the opportunity already has an outcome here or its source valuation
--     already knows its outcome, when the registration is not
--     ACTIVE_FORWARD, or when the opportunity predates the registration
--     (scoring is on outcomes after registration only). Forecasts,
--     opportunities and outcomes are append-only;
--   * PROMOTION is a ladder no agent can finish: CRITERIA_MET (runner) ->
--     KAREN_CHALLENGE (Karen, NOT_BLOCKED) -> AUDREY_EVALUATION (Audrey,
--     PASS) -> poslearn_human_approvals, whose approver can never be an
--     agent, a system actor or a migration (CHECK), which needs Audrey's PASS
--     first (trigger), and which no code path in this repository writes
--     (tests/test_poslearn_authority.py). Mirrors the owner-approval
--     pattern of migrations 201 / 204. Even an approval has
--     production_effect = 'NONE': deploying a promoted model is a separate
--     owner action;
--   * an EXPERIMENT's hypothesis, metrics, stopping rule, failure criteria,
--     randomization, arms and minimum sample are refused any change once it
--     has started; it can start only after a Karen design challenge row
--     NOT_BLOCKED; every ASSIGNMENT is verified against the seeded SHA-256
--     draw by the database, is written before the unit's outcome, can never
--     be changed or deleted (no reassignment, no hindsight exclusion), and
--     an outcome can only follow its assignment.
--
-- UNMEASURED IS NULL WITH A REASON, NEVER ZERO.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP-then-CREATE for triggers.
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. No foreign key into any
-- table outside this migration (external_valuations is consulted by the
-- forecast trigger through to_regclass + EXECUTE only), so the rollback is
-- independent.

-- ── WHO IS AN AGENT / SYSTEM ACTOR (never a human approver) ───────────
CREATE OR REPLACE FUNCTION poslearn_is_agent_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'CLAUDE', 'SYSTEM',
                               'RUNNER', 'POS_LEARN_RUNNER', 'MIGRATION',
                               'ROOT', 'POSTGRES', 'BOT', 'AGENT')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|SCOUT|ALLOCATOR)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM)'
$$;

CREATE OR REPLACE FUNCTION poslearn_no_authority(doc jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT doc IS NULL OR jsonb_typeof(doc) <> 'object' OR NOT (
        doc ?| ARRAY['risk_limits', 'limits', 'capital_usd',
                     'max_downside_usd', 'max_incremental_capital_usd',
                     'per_order_usd', 'max_order_usd', 'daily_loss_stop_usd',
                     'credentials', 'api_key', 'account_authority',
                     'capital_authority', 'submission_enabled', 'approved',
                     'approved_by', 'approved_at', 'approval', 'activate',
                     'activation', 'policy_activation', 'promote',
                     'promoted', 'release', 'order', 'submit', 'allowlist',
                     'production_threshold', 'live_sizing'])
$$;

-- the seeded, deterministic per-unit draw in [0, 1): the first 52 bits of
-- SHA-256(seed ':' experiment_id ':' unit_id). poslearn/experiments.py
-- computes the same number; the assignment trigger recomputes it.
CREATE OR REPLACE FUNCTION poslearn_draw(seed text, experiment_id text,
                                         unit_id text)
RETURNS double precision LANGUAGE sql IMMUTABLE AS $$
    SELECT (('x' || substr(encode(sha256(convert_to(
                seed || ':' || experiment_id || ':' || unit_id, 'UTF8')),
                'hex'), 1, 13))::bit(52)::bigint)::double precision
           / 4503599627370496.0
$$;

CREATE OR REPLACE FUNCTION poslearn_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%: append-only, % refused', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'check_violation';
END $$ LANGUAGE plpgsql;

-- ── ONE ROW PER COMPONENT PER RUN ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS poslearn_runs (
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz,
    status              text        NOT NULL,
    error               text,
    duration_ms         integer,
    summary             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, component),
    CONSTRAINT poslearn_runs_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_runs_status_ck CHECK (status IN (
        'OK', 'FAILED', 'TIMEOUT', 'SKIPPED')),
    CONSTRAINT poslearn_runs_component_ck CHECK (component IN (
        'CYCLE', 'REGISTER', 'CAPTURE', 'FORECAST', 'OUTCOMES',
        'MODEL_TOURNAMENT', 'PROMOTION', 'EDGE_CONFIDENCE', 'AVOIDANCE',
        'AGENT_TOURNAMENT', 'EXPERIMENTS'))
);
CREATE INDEX IF NOT EXISTS poslearn_runs_at_idx
    ON poslearn_runs (component, started_at DESC);

-- ── THE PAYLOAD EACH READ ENDPOINT SERVES (latest per component) ──────
CREATE TABLE IF NOT EXISTS poslearn_snapshots (
    snapshot_id         bigserial PRIMARY KEY,
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    payload             jsonb       NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT poslearn_snapshots_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_snapshots_component_ck CHECK (component IN (
        'MODEL_TOURNAMENT', 'AGENT_TOURNAMENT', 'EDGE_CONFIDENCE',
        'AVOIDANCE', 'EXPERIMENTS')),
    CONSTRAINT poslearn_snapshots_payload_ck CHECK (
        payload->>'label' = 'SHADOW'),
    CONSTRAINT poslearn_snapshots_one_per_run UNIQUE (run_id, component)
);
CREATE INDEX IF NOT EXISTS poslearn_snapshots_latest_idx
    ON poslearn_snapshots (component, computed_at DESC);

-- ── 1 / 2 / 3 / 4 · REGISTRATIONS: IMMUTABLE, HASHED, BEFORE FORWARD DATA ─
CREATE TABLE IF NOT EXISTS poslearn_registrations (
    registration_id       text        PRIMARY KEY,
    kind                  text        NOT NULL,
    subject_id            text        NOT NULL,
    version               integer     NOT NULL,
    family                text        NOT NULL,
    role                  text        NOT NULL,
    canonical_json        text        NOT NULL,
    document              jsonb       NOT NULL,
    sha256                text        NOT NULL,
    training_window_start timestamptz,
    training_window_end   timestamptz,
    min_sample            integer     NOT NULL,
    family_size           integer     NOT NULL,
    registered_at         timestamptz NOT NULL,
    status                text        NOT NULL,
    status_reason         text,
    status_changed_at     timestamptz NOT NULL DEFAULT now(),
    production_effect     text        NOT NULL DEFAULT 'NONE',
    label                 text        NOT NULL DEFAULT 'SHADOW',
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT poslearn_reg_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_reg_effect_ck CHECK (production_effect = 'NONE'),
    CONSTRAINT poslearn_reg_kind_ck CHECK (kind IN (
        'MODEL', 'AGENT_VARIANT', 'META_MODEL')),
    CONSTRAINT poslearn_reg_role_ck CHECK (role IN (
        'CHAMPION', 'CHALLENGER', 'META')),
    CONSTRAINT poslearn_reg_id_ck CHECK (
        subject_id ~ '^[A-Z][A-Z0-9_]{1,80}$'
        AND registration_id = subject_id || '@' || version::text
        AND version >= 1),
    CONSTRAINT poslearn_reg_status_ck CHECK (status IN (
        'ACTIVE_FORWARD', 'AWAITING_FEATURES', 'AWAITING_SOURCE',
        'AWAITING_INTERFACE', 'RETIRED', 'FAILED_FORWARD')),
    CONSTRAINT poslearn_reg_sha_ck CHECK (
        sha256 ~ '^[0-9a-f]{64}$'
        AND sha256 = encode(sha256(convert_to(canonical_json, 'UTF8')),
                            'hex')),
    -- THE PREDECLARED CRITERIA ARE IN THE HASHED DOCUMENT, OR NO ROW
    CONSTRAINT poslearn_reg_document_ck CHECK (
        jsonb_typeof(document) = 'object'
        AND document = canonical_json::jsonb
        AND document->>'subject_id' = subject_id
        AND (document->>'version')::int = version
        AND document->>'kind' = kind
        AND document->>'family' = family
        AND document->>'role' = role
        AND document ?& ARRAY['training_window', 'features', 'parameters',
                              'validation_method', 'minimum_sample',
                              'promotion_threshold', 'failure_threshold',
                              'hypothesis_family', 'family_size']
        AND (document->>'minimum_sample')::int = min_sample
        AND (document->>'family_size')::int = family_size),
    CONSTRAINT poslearn_reg_sample_ck CHECK (
        min_sample >= 1 AND family_size >= 1),
    -- NO PEEKING: the training window closes at or before registration
    CONSTRAINT poslearn_reg_window_ck CHECK (
        (training_window_start IS NULL AND training_window_end IS NULL)
        OR (training_window_start < training_window_end
            AND training_window_end <= registered_at)),
    CONSTRAINT poslearn_reg_no_authority_ck CHECK (
        poslearn_no_authority(document)
        AND poslearn_no_authority(document->'parameters')),
    CONSTRAINT poslearn_reg_once UNIQUE (subject_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS poslearn_reg_one_live_idx
    ON poslearn_registrations (subject_id)
    WHERE status IN ('ACTIVE_FORWARD', 'AWAITING_FEATURES', 'AWAITING_SOURCE',
                     'AWAITING_INTERFACE');

CREATE OR REPLACE FUNCTION poslearn_registrations_guard() RETURNS trigger
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'poslearn_registrations: a registration is never '
                        'deleted (%)', OLD.registration_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        -- NO BACK-DATING: a model cannot be "registered" before data it
        -- has already seen.
        IF NEW.registered_at < now() - interval '5 minutes'
           OR NEW.registered_at > now() + interval '5 minutes' THEN
            RAISE EXCEPTION 'poslearn_registrations: registered_at must be '
                            'the time of the insert (no back-dating) for %',
                            NEW.registration_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status IN ('RETIRED', 'FAILED_FORWARD') THEN
            RAISE EXCEPTION 'poslearn_registrations: % is registered live, '
                            'not %', NEW.registration_id, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.registration_id IS DISTINCT FROM OLD.registration_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
       OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.family IS DISTINCT FROM OLD.family
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.canonical_json IS DISTINCT FROM OLD.canonical_json
       OR NEW.document IS DISTINCT FROM OLD.document
       OR NEW.sha256 IS DISTINCT FROM OLD.sha256
       OR NEW.training_window_start IS DISTINCT FROM OLD.training_window_start
       OR NEW.training_window_end IS DISTINCT FROM OLD.training_window_end
       OR NEW.min_sample IS DISTINCT FROM OLD.min_sample
       OR NEW.family_size IS DISTINCT FROM OLD.family_size
       OR NEW.registered_at IS DISTINCT FROM OLD.registered_at
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION 'poslearn_registrations: the registered criteria of '
                        '% are immutable; a change is a new version',
                        OLD.registration_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status IN ('AWAITING_FEATURES', 'AWAITING_SOURCE',
                           'AWAITING_INTERFACE')
            AND NEW.status = 'RETIRED')
        OR (OLD.status = 'ACTIVE_FORWARD'
            AND NEW.status IN ('RETIRED', 'FAILED_FORWARD'))) THEN
        RAISE EXCEPTION 'poslearn_registrations: % -> % is not a permitted '
                        'status change for %', OLD.status, NEW.status,
                        OLD.registration_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_changed_at := now();
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS poslearn_registrations_guard_trg
    ON poslearn_registrations;
CREATE TRIGGER poslearn_registrations_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_registrations
    FOR EACH ROW EXECUTE FUNCTION poslearn_registrations_guard();

-- ── THE SHARED PROSPECTIVE OPPORTUNITY SET (every model and variant
--    sees the same future events) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS poslearn_opportunities (
    opportunity_id      text        PRIMARY KEY,
    source_kind         text        NOT NULL,
    source_id           bigint      NOT NULL,
    unit                text        NOT NULL,
    opportunity_at      timestamptz NOT NULL,
    features_as_of      timestamptz NOT NULL,
    captured_at         timestamptz NOT NULL,
    us_market_slug      text,
    sport               text        NOT NULL,
    league              text        NOT NULL,
    market              text        NOT NULL,
    live_state          text        NOT NULL,
    record_purpose      text,
    p_reference         double precision,
    price               double precision,
    price_basis         text,
    fee                 double precision,
    features            jsonb       NOT NULL,
    unavailable         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT poslearn_opp_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_opp_source_ck CHECK (
        source_kind = 'EXTERNAL_VALUATION'),
    CONSTRAINT poslearn_opp_unit_once UNIQUE (source_kind, unit),
    CONSTRAINT poslearn_opp_source_once UNIQUE (source_kind, source_id),
    -- NO LOOK-AHEAD: every feature is as of the opportunity instant
    CONSTRAINT poslearn_opp_asof_ck CHECK (
        features_as_of <= opportunity_at AND opportunity_at <= captured_at),
    CONSTRAINT poslearn_opp_prob_ck CHECK (
        p_reference IS NULL OR (p_reference >= 0 AND p_reference <= 1)),
    CONSTRAINT poslearn_opp_price_ck CHECK (
        price IS NULL OR (price > 0 AND price < 1 AND price_basis IS NOT NULL)),
    CONSTRAINT poslearn_opp_live_ck CHECK (live_state IN (
        'PREGAME', 'LIVE', 'UNKNOWN')),
    -- NO POST-OUTCOME FEATURE CAN BE STORED
    CONSTRAINT poslearn_opp_no_outcome_feature_ck CHECK (
        NOT (features ?| ARRAY['outcome', 'outcome_known', 'outcome_at',
                               'outcome_basis', 'realised_net_usd',
                               'settlement_read', 'realized_edge',
                               'settlement_comparison']))
);
CREATE INDEX IF NOT EXISTS poslearn_opp_at_idx
    ON poslearn_opportunities (opportunity_at DESC);

CREATE OR REPLACE FUNCTION poslearn_source_outcome_known(kind text, sid bigint)
RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE
    known boolean;
BEGIN
    IF kind <> 'EXTERNAL_VALUATION'
       OR to_regclass('external_valuations') IS NULL THEN
        RETURN false;
    END IF;
    EXECUTE 'SELECT outcome_known FROM external_valuations WHERE id = $1'
       INTO known USING sid;
    RETURN coalesce(known, false);
END $$;

CREATE OR REPLACE FUNCTION poslearn_opportunities_guard() RETURNS trigger
AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'poslearn_opportunities: append-only, % refused',
            TG_OP USING ERRCODE = 'check_violation';
    END IF;
    NEW.recorded_at := clock_timestamp();
    IF poslearn_source_outcome_known(NEW.source_kind, NEW.source_id) THEN
        RAISE EXCEPTION 'poslearn_opportunities: % already knows its outcome; '
                        'an opportunity is captured before its outcome',
                        NEW.opportunity_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS poslearn_opportunities_guard_trg
    ON poslearn_opportunities;
CREATE TRIGGER poslearn_opportunities_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_opportunities
    FOR EACH ROW EXECUTE FUNCTION poslearn_opportunities_guard();

-- ── OUTCOMES (joined after the fact; append-only) ─────────────────────
CREATE TABLE IF NOT EXISTS poslearn_outcomes (
    opportunity_id      text        PRIMARY KEY
                        REFERENCES poslearn_opportunities,
    outcome_class       text        NOT NULL,
    outcome             smallint,
    basis               text,
    outcome_at          timestamptz,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT poslearn_out_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_out_class_ck CHECK (outcome_class IN (
        'RESOLVED', 'VOID', 'UNVERIFIED')),
    CONSTRAINT poslearn_out_value_ck CHECK (
        (outcome_class = 'RESOLVED' AND outcome IN (0, 1))
        OR (outcome_class <> 'RESOLVED' AND outcome IS NULL))
);
DROP TRIGGER IF EXISTS poslearn_outcomes_append_only_trg ON poslearn_outcomes;
CREATE TRIGGER poslearn_outcomes_append_only_trg
    BEFORE UPDATE OR DELETE ON poslearn_outcomes
    FOR EACH ROW EXECUTE FUNCTION poslearn_append_only();

-- ── FORECASTS: models, agent variants, edge confidence, avoidance ─────
CREATE TABLE IF NOT EXISTS poslearn_forecasts (
    registration_id     text        NOT NULL
                        REFERENCES poslearn_registrations,
    opportunity_id      text        NOT NULL
                        REFERENCES poslearn_opportunities,
    probability         double precision,
    predicted_net_edge  double precision,
    action              text        NOT NULL,
    abstain_reason      text,
    shadow_usd          double precision,
    edge_confidence     double precision,
    expected_net_edge   double precision,
    expected_net_edge_ci_low  double precision,
    expected_net_edge_ci_high double precision,
    avoidance_risk      double precision,
    avoidance_level     text,
    reasons             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    output              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    predicted_at        timestamptz NOT NULL,
    applied             boolean     NOT NULL DEFAULT false,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (registration_id, opportunity_id),
    CONSTRAINT poslearn_fc_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_fc_never_applied_ck CHECK (applied = false),
    CONSTRAINT poslearn_fc_action_ck CHECK (action IN (
        'ENTER', 'PASS', 'ABSTAIN', 'SCORE', 'HOLD_TO_SETTLEMENT',
        'TAKE_PROFIT_PLAN', 'EXIT_ON_EDGE_LOSS_PLAN', 'ALLOCATE')),
    CONSTRAINT poslearn_fc_abstain_ck CHECK (
        action <> 'ABSTAIN' OR length(btrim(abstain_reason)) > 0),
    CONSTRAINT poslearn_fc_prob_ck CHECK (
        probability IS NULL OR (probability >= 0 AND probability <= 1)),
    CONSTRAINT poslearn_fc_ec_ck CHECK (
        edge_confidence IS NULL OR (edge_confidence >= 0
                                    AND edge_confidence <= 1)),
    CONSTRAINT poslearn_fc_ec_interval_ck CHECK (
        expected_net_edge_ci_low IS NULL OR expected_net_edge_ci_high IS NULL
        OR expected_net_edge_ci_low <= expected_net_edge_ci_high),
    CONSTRAINT poslearn_fc_avoid_ck CHECK (
        avoidance_level IS NULL OR avoidance_level IN (
            'NORMAL', 'CAUTION', 'AVOID', 'UNMEASURED')),
    CONSTRAINT poslearn_fc_avoid_risk_ck CHECK (
        avoidance_risk IS NULL OR (avoidance_risk >= 0
                                   AND avoidance_risk <= 1)),
    CONSTRAINT poslearn_fc_usd_ck CHECK (
        shadow_usd IS NULL OR shadow_usd >= 0),
    CONSTRAINT poslearn_fc_no_authority_ck CHECK (
        poslearn_no_authority(output))
);
CREATE INDEX IF NOT EXISTS poslearn_fc_opp_idx
    ON poslearn_forecasts (opportunity_id);

CREATE OR REPLACE FUNCTION poslearn_forecasts_guard() RETURNS trigger AS $$
DECLARE
    r record;
    o record;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'poslearn_forecasts: append-only, % refused', TG_OP
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.recorded_at := clock_timestamp();
    SELECT status, registered_at INTO r FROM poslearn_registrations
     WHERE registration_id = NEW.registration_id;
    IF r.status IS DISTINCT FROM 'ACTIVE_FORWARD' THEN
        RAISE EXCEPTION 'poslearn_forecasts: % is not ACTIVE_FORWARD (%)',
            NEW.registration_id, r.status USING ERRCODE = 'check_violation';
    END IF;
    SELECT opportunity_at, source_kind, source_id INTO o
      FROM poslearn_opportunities WHERE opportunity_id = NEW.opportunity_id;
    -- FORWARD ONLY: an opportunity that predates the registration is never
    -- scored for it
    IF o.opportunity_at < r.registered_at THEN
        RAISE EXCEPTION 'poslearn_forecasts: opportunity % predates the '
                        'registration of %', NEW.opportunity_id,
                        NEW.registration_id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.predicted_at < r.registered_at
       OR NEW.predicted_at < o.opportunity_at THEN
        RAISE EXCEPTION 'poslearn_forecasts: predicted_at precedes the '
                        'registration or the opportunity'
            USING ERRCODE = 'check_violation';
    END IF;
    -- BEFORE THE OUTCOME
    IF EXISTS (SELECT 1 FROM poslearn_outcomes
                WHERE opportunity_id = NEW.opportunity_id)
       OR poslearn_source_outcome_known(o.source_kind, o.source_id) THEN
        RAISE EXCEPTION 'poslearn_forecasts: the outcome of % is already '
                        'known; a forecast is recorded before its outcome',
                        NEW.opportunity_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS poslearn_forecasts_guard_trg ON poslearn_forecasts;
CREATE TRIGGER poslearn_forecasts_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_forecasts
    FOR EACH ROW EXECUTE FUNCTION poslearn_forecasts_guard();

-- ── THE PROMOTION LADDER (no agent can finish it) ─────────────────────
CREATE TABLE IF NOT EXISTS poslearn_promotion_steps (
    step_id             bigserial   PRIMARY KEY,
    registration_id     text        NOT NULL
                        REFERENCES poslearn_registrations,
    step                text        NOT NULL,
    actor               text        NOT NULL,
    outcome             text        NOT NULL,
    evidence            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    at                  timestamptz NOT NULL DEFAULT now(),
    production_effect   text        NOT NULL DEFAULT 'NONE',
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT poslearn_step_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_step_effect_ck CHECK (production_effect = 'NONE'),
    CONSTRAINT poslearn_step_once UNIQUE (registration_id, step),
    CONSTRAINT poslearn_step_kind_ck CHECK (
        (step = 'CRITERIA_MET' AND actor = 'POS_LEARN_RUNNER'
         AND outcome = 'CRITERIA_MET')
        OR (step = 'KAREN_CHALLENGE' AND actor = 'KAREN'
            AND outcome IN ('NOT_BLOCKED', 'BLOCKED'))
        OR (step = 'AUDREY_EVALUATION' AND actor = 'AUDREY'
            AND outcome IN ('PASS', 'FAIL'))
        OR (step = 'CLOSED' AND length(btrim(actor)) > 0
            AND length(btrim(outcome)) > 0)),
    CONSTRAINT poslearn_step_no_authority_ck CHECK (
        poslearn_no_authority(evidence))
);

CREATE OR REPLACE FUNCTION poslearn_promotion_steps_guard() RETURNS trigger
AS $$
DECLARE
    reg_role text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'poslearn_promotion_steps: append-only, % refused',
            TG_OP USING ERRCODE = 'check_violation';
    END IF;
    SELECT role INTO reg_role FROM poslearn_registrations
     WHERE registration_id = NEW.registration_id;
    IF reg_role IS DISTINCT FROM 'CHALLENGER' THEN
        RAISE EXCEPTION 'poslearn_promotion_steps: only a CHALLENGER can be a '
                        'promotion candidate (% is %)', NEW.registration_id,
                        reg_role USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM poslearn_promotion_steps
                WHERE registration_id = NEW.registration_id
                  AND step = 'CLOSED') THEN
        RAISE EXCEPTION 'poslearn_promotion_steps: % is closed',
            NEW.registration_id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'KAREN_CHALLENGE' AND NOT EXISTS (
           SELECT 1 FROM poslearn_promotion_steps
            WHERE registration_id = NEW.registration_id
              AND step = 'CRITERIA_MET') THEN
        RAISE EXCEPTION 'poslearn_promotion_steps: Karen challenges only a '
                        'candidate whose predeclared criteria were met'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'AUDREY_EVALUATION' AND NOT EXISTS (
           SELECT 1 FROM poslearn_promotion_steps
            WHERE registration_id = NEW.registration_id
              AND step = 'KAREN_CHALLENGE' AND outcome = 'NOT_BLOCKED') THEN
        RAISE EXCEPTION 'poslearn_promotion_steps: Audrey evaluates only '
                        'after a Karen challenge that did not block'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS poslearn_promotion_steps_guard_trg
    ON poslearn_promotion_steps;
CREATE TRIGGER poslearn_promotion_steps_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_promotion_steps
    FOR EACH ROW EXECUTE FUNCTION poslearn_promotion_steps_guard();

-- ── THE HUMAN APPROVAL RECORD (no agent can write it) ─────────────────
-- NO CODE PATH IN THIS REPOSITORY INSERTS HERE (pinned by
-- tests/test_poslearn_authority.py). The owner records a decision as an
-- operator action, by SQL, naming themself. Even an APPROVE has
-- production_effect = 'NONE': deploying the promoted model or policy is a
-- separate, owner-approved release.
CREATE TABLE IF NOT EXISTS poslearn_human_approvals (
    approval_id         text        PRIMARY KEY,
    registration_id     text        NOT NULL UNIQUE
                        REFERENCES poslearn_registrations,
    decision            text        NOT NULL,
    approver            text        NOT NULL,
    statement           text        NOT NULL,
    approved_at         timestamptz NOT NULL DEFAULT now(),
    production_effect   text        NOT NULL DEFAULT 'NONE',
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT poslearn_appr_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_appr_effect_ck CHECK (production_effect = 'NONE'),
    CONSTRAINT poslearn_appr_decision_ck CHECK (decision IN (
        'APPROVE_PROMOTION', 'REJECT_PROMOTION')),
    CONSTRAINT poslearn_appr_human_ck CHECK (
        NOT poslearn_is_agent_actor(approver)
        AND length(btrim(approver)) BETWEEN 2 AND 100),
    CONSTRAINT poslearn_appr_statement_ck CHECK (
        length(btrim(statement)) BETWEEN 20 AND 4000)
);

CREATE OR REPLACE FUNCTION poslearn_human_approvals_guard() RETURNS trigger
AS $$
DECLARE
    audrey_at timestamptz;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'poslearn_human_approvals: write-once, % refused',
            TG_OP USING ERRCODE = 'check_violation';
    END IF;
    SELECT at INTO audrey_at FROM poslearn_promotion_steps
     WHERE registration_id = NEW.registration_id
       AND step = 'AUDREY_EVALUATION' AND outcome = 'PASS';
    IF audrey_at IS NULL THEN
        RAISE EXCEPTION 'poslearn_human_approvals: % has no Audrey PASS '
                        '(criteria met -> Karen -> Audrey come first)',
                        NEW.registration_id USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM poslearn_promotion_steps
                WHERE registration_id = NEW.registration_id
                  AND step = 'CLOSED') THEN
        RAISE EXCEPTION 'poslearn_human_approvals: % is closed',
            NEW.registration_id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.approved_at < audrey_at THEN
        RAISE EXCEPTION 'poslearn_human_approvals: an approval follows '
                        'Audrey''s evaluation' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM poslearn_promotion_steps
                WHERE registration_id = NEW.registration_id
                  AND upper(btrim(actor)) = upper(btrim(NEW.approver))) THEN
        RAISE EXCEPTION 'poslearn_human_approvals: the approver acted in the '
                        'ladder' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS poslearn_human_approvals_guard_trg
    ON poslearn_human_approvals;
CREATE TRIGGER poslearn_human_approvals_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_human_approvals
    FOR EACH ROW EXECUTE FUNCTION poslearn_human_approvals_guard();

-- ── 5 · THE CAUSAL EXPERIMENT PLATFORM ────────────────────────────────
CREATE TABLE IF NOT EXISTS poslearn_experiments (
    experiment_id       text        PRIMARY KEY,
    hypothesis          text        NOT NULL,
    primary_metric      jsonb       NOT NULL,
    secondary_metrics   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    assignment_unit     text        NOT NULL,
    randomization       jsonb       NOT NULL,
    seed                text        NOT NULL,
    arms                jsonb       NOT NULL,
    policy_versions     jsonb       NOT NULL,
    start_at            timestamptz NOT NULL,
    stop_at             timestamptz NOT NULL,
    min_sample          integer     NOT NULL,
    power_target        double precision NOT NULL,
    alpha               double precision NOT NULL,
    family              text        NOT NULL,
    family_size         integer     NOT NULL,
    stopping_rule       jsonb       NOT NULL,
    failure_criteria    jsonb       NOT NULL,
    design_sha256       text        NOT NULL,
    scope               text        NOT NULL DEFAULT 'PAPER_SHADOW_ONLY',
    status              text        NOT NULL DEFAULT 'REGISTERED',
    status_reason       text,
    result              jsonb,
    registered_at       timestamptz NOT NULL DEFAULT now(),
    status_changed_at   timestamptz NOT NULL DEFAULT now(),
    production_effect   text        NOT NULL DEFAULT 'NONE',
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT poslearn_exp_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_exp_effect_ck CHECK (production_effect = 'NONE'),
    CONSTRAINT poslearn_exp_scope_ck CHECK (scope = 'PAPER_SHADOW_ONLY'),
    CONSTRAINT poslearn_exp_status_ck CHECK (status IN (
        'REGISTERED', 'RUNNING', 'STOPPED', 'ANALYZED', 'ABANDONED')),
    CONSTRAINT poslearn_exp_text_ck CHECK (
        length(btrim(hypothesis)) BETWEEN 10 AND 4000
        AND length(btrim(assignment_unit)) > 0
        AND length(btrim(seed)) >= 8),
    CONSTRAINT poslearn_exp_metric_ck CHECK (
        jsonb_typeof(primary_metric) = 'object'
        AND primary_metric ?& ARRAY['name', 'direction']
        AND jsonb_typeof(secondary_metrics) = 'array'),
    CONSTRAINT poslearn_exp_arms_ck CHECK (
        jsonb_typeof(arms) = 'array' AND jsonb_array_length(arms) >= 2),
    CONSTRAINT poslearn_exp_window_ck CHECK (
        registered_at <= start_at AND start_at < stop_at),
    CONSTRAINT poslearn_exp_design_ck CHECK (
        min_sample >= 2 AND power_target >= 0.5 AND power_target < 1
        AND alpha > 0 AND alpha < 0.5 AND family_size >= 1
        AND design_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT poslearn_exp_rules_ck CHECK (
        jsonb_typeof(stopping_rule) = 'object'
        AND jsonb_typeof(failure_criteria) = 'object'
        AND poslearn_no_authority(stopping_rule)
        AND poslearn_no_authority(failure_criteria)
        AND poslearn_no_authority(randomization))
);

CREATE OR REPLACE FUNCTION poslearn_experiments_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'poslearn_experiments: an experiment is never '
                        'deleted (%)', OLD.experiment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'REGISTERED' THEN
            RAISE EXCEPTION 'poslearn_experiments: % starts REGISTERED',
                NEW.experiment_id USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
       OR NEW.registered_at IS DISTINCT FROM OLD.registered_at
       OR NEW.scope IS DISTINCT FROM OLD.scope THEN
        RAISE EXCEPTION 'poslearn_experiments: identity of % is fixed',
            OLD.experiment_id USING ERRCODE = 'check_violation';
    END IF;
    -- NO METRIC, STOPPING-RULE OR DESIGN CHANGE AFTER START
    IF (OLD.status <> 'REGISTERED' OR now() >= OLD.start_at) AND (
           NEW.hypothesis IS DISTINCT FROM OLD.hypothesis
        OR NEW.primary_metric IS DISTINCT FROM OLD.primary_metric
        OR NEW.secondary_metrics IS DISTINCT FROM OLD.secondary_metrics
        OR NEW.assignment_unit IS DISTINCT FROM OLD.assignment_unit
        OR NEW.randomization IS DISTINCT FROM OLD.randomization
        OR NEW.seed IS DISTINCT FROM OLD.seed
        OR NEW.arms IS DISTINCT FROM OLD.arms
        OR NEW.policy_versions IS DISTINCT FROM OLD.policy_versions
        OR NEW.start_at IS DISTINCT FROM OLD.start_at
        OR NEW.stop_at IS DISTINCT FROM OLD.stop_at
        OR NEW.min_sample IS DISTINCT FROM OLD.min_sample
        OR NEW.power_target IS DISTINCT FROM OLD.power_target
        OR NEW.alpha IS DISTINCT FROM OLD.alpha
        OR NEW.family IS DISTINCT FROM OLD.family
        OR NEW.family_size IS DISTINCT FROM OLD.family_size
        OR NEW.stopping_rule IS DISTINCT FROM OLD.stopping_rule
        OR NEW.failure_criteria IS DISTINCT FROM OLD.failure_criteria
        OR NEW.design_sha256 IS DISTINCT FROM OLD.design_sha256) THEN
        RAISE EXCEPTION 'poslearn_experiments: the design of % (hypothesis, '
                        'metrics, stopping rule, failure criteria, '
                        'randomization, arms, sample) is fixed once it has '
                        'started', OLD.experiment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
               (OLD.status = 'REGISTERED'
                AND NEW.status IN ('RUNNING', 'ABANDONED'))
            OR (OLD.status = 'RUNNING' AND NEW.status = 'STOPPED')
            OR (OLD.status = 'STOPPED' AND NEW.status = 'ANALYZED')) THEN
            RAISE EXCEPTION 'poslearn_experiments: % -> % is not permitted '
                            'for %', OLD.status, NEW.status, OLD.experiment_id
                USING ERRCODE = 'check_violation';
        END IF;
        -- THE KAREN DESIGN-CHALLENGE HOOK: no start without it
        IF NEW.status = 'RUNNING' AND NOT EXISTS (
               SELECT 1 FROM poslearn_experiment_reviews
                WHERE experiment_id = OLD.experiment_id
                  AND kind = 'KAREN_DESIGN_CHALLENGE'
                  AND outcome = 'NOT_BLOCKED') THEN
            RAISE EXCEPTION 'poslearn_experiments: % cannot start without a '
                            'Karen design challenge that did not block',
                            OLD.experiment_id
                USING ERRCODE = 'check_violation';
        END IF;
        NEW.status_changed_at := now();
    END IF;
    IF OLD.result IS NOT NULL
       AND NEW.result IS DISTINCT FROM OLD.result THEN
        RAISE EXCEPTION 'poslearn_experiments: the result of % is write-once',
            OLD.experiment_id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.result IS NOT NULL AND NEW.status <> 'ANALYZED' THEN
        RAISE EXCEPTION 'poslearn_experiments: a result belongs to an '
                        'ANALYZED experiment' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS poslearn_experiment_reviews (
    review_id           bigserial   PRIMARY KEY,
    experiment_id       text        NOT NULL REFERENCES poslearn_experiments,
    kind                text        NOT NULL,
    actor               text        NOT NULL,
    outcome             text        NOT NULL,
    findings            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    at                  timestamptz NOT NULL DEFAULT now(),
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT poslearn_rev_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_rev_kind_ck CHECK (
        (kind = 'KAREN_DESIGN_CHALLENGE' AND actor = 'KAREN'
         AND outcome IN ('NOT_BLOCKED', 'BLOCKED'))
        OR (kind = 'AUDREY_RANDOMIZATION_AUDIT' AND actor = 'AUDREY'
            AND outcome IN ('PASS', 'FAIL'))),
    CONSTRAINT poslearn_rev_no_authority_ck CHECK (
        poslearn_no_authority(findings))
);
DROP TRIGGER IF EXISTS poslearn_experiment_reviews_append_only_trg
    ON poslearn_experiment_reviews;
CREATE TRIGGER poslearn_experiment_reviews_append_only_trg
    BEFORE UPDATE OR DELETE ON poslearn_experiment_reviews
    FOR EACH ROW EXECUTE FUNCTION poslearn_append_only();

DROP TRIGGER IF EXISTS poslearn_experiments_guard_trg ON poslearn_experiments;
CREATE TRIGGER poslearn_experiments_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_experiments
    FOR EACH ROW EXECUTE FUNCTION poslearn_experiments_guard();

CREATE TABLE IF NOT EXISTS poslearn_experiment_assignments (
    experiment_id       text        NOT NULL REFERENCES poslearn_experiments,
    unit_id             text        NOT NULL,
    opportunity_id      text        REFERENCES poslearn_opportunities,
    arm                 text        NOT NULL,
    draw                double precision NOT NULL,
    assigned_at         timestamptz NOT NULL,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (experiment_id, unit_id),
    CONSTRAINT poslearn_asg_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_asg_draw_ck CHECK (draw >= 0 AND draw < 1)
);

CREATE OR REPLACE FUNCTION poslearn_assignments_guard() RETURNS trigger AS $$
DECLARE
    e record;
    want double precision;
    cum double precision := 0;
    chosen text := NULL;
    a record;
    o record;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'poslearn_experiment_assignments: no reassignment '
                        'and no exclusion (% refused)', TG_OP
            USING ERRCODE = 'check_violation';
    END IF;
    -- the database's clock, not the caller's
    NEW.recorded_at := clock_timestamp();
    SELECT status, seed, arms, start_at, stop_at INTO e
      FROM poslearn_experiments WHERE experiment_id = NEW.experiment_id;
    IF e.status IS DISTINCT FROM 'RUNNING' THEN
        RAISE EXCEPTION 'poslearn_experiment_assignments: % is not RUNNING',
            NEW.experiment_id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.assigned_at < e.start_at OR NEW.assigned_at >= e.stop_at THEN
        RAISE EXCEPTION 'poslearn_experiment_assignments: outside the '
                        'experiment window' USING ERRCODE = 'check_violation';
    END IF;
    -- THE SEEDED DRAW, RECOMPUTED: the recorded draw and arm are the
    -- deterministic ones, not a choice
    want := poslearn_draw(e.seed, NEW.experiment_id, NEW.unit_id);
    IF abs(want - NEW.draw) > 1e-12 THEN
        RAISE EXCEPTION 'poslearn_experiment_assignments: draw % is not the '
                        'seeded draw % for unit %', NEW.draw, want,
                        NEW.unit_id USING ERRCODE = 'check_violation';
    END IF;
    FOR a IN SELECT x->>'arm' AS arm, (x->>'weight')::double precision AS w
               FROM jsonb_array_elements(e.arms) WITH ORDINALITY AS t(x, i)
              ORDER BY i LOOP
        cum := cum + a.w;
        IF chosen IS NULL AND want < cum THEN
            chosen := a.arm;
        END IF;
    END LOOP;
    IF chosen IS DISTINCT FROM NEW.arm THEN
        RAISE EXCEPTION 'poslearn_experiment_assignments: arm % is not the '
                        'seeded arm % for unit %', NEW.arm, chosen,
                        NEW.unit_id USING ERRCODE = 'check_violation';
    END IF;
    -- BEFORE THE OUTCOME
    IF NEW.opportunity_id IS NOT NULL THEN
        SELECT source_kind, source_id INTO o FROM poslearn_opportunities
         WHERE opportunity_id = NEW.opportunity_id;
        IF EXISTS (SELECT 1 FROM poslearn_outcomes
                    WHERE opportunity_id = NEW.opportunity_id)
           OR poslearn_source_outcome_known(o.source_kind, o.source_id) THEN
            RAISE EXCEPTION 'poslearn_experiment_assignments: the outcome of '
                            '% is already known', NEW.opportunity_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS poslearn_assignments_guard_trg
    ON poslearn_experiment_assignments;
CREATE TRIGGER poslearn_assignments_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_experiment_assignments
    FOR EACH ROW EXECUTE FUNCTION poslearn_assignments_guard();

CREATE TABLE IF NOT EXISTS poslearn_experiment_outcomes (
    experiment_id       text        NOT NULL,
    unit_id             text        NOT NULL,
    metrics             jsonb       NOT NULL,
    outcome_at          timestamptz,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    label               text        NOT NULL DEFAULT 'SHADOW',
    PRIMARY KEY (experiment_id, unit_id),
    FOREIGN KEY (experiment_id, unit_id)
        REFERENCES poslearn_experiment_assignments (experiment_id, unit_id),
    CONSTRAINT poslearn_eout_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT poslearn_eout_no_authority_ck CHECK (
        poslearn_no_authority(metrics))
);

CREATE OR REPLACE FUNCTION poslearn_experiment_outcomes_guard()
RETURNS trigger AS $$
DECLARE
    asg_at timestamptz;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'poslearn_experiment_outcomes: append-only, % '
                        'refused', TG_OP USING ERRCODE = 'check_violation';
    END IF;
    NEW.recorded_at := clock_timestamp();
    SELECT recorded_at INTO asg_at FROM poslearn_experiment_assignments
     WHERE experiment_id = NEW.experiment_id AND unit_id = NEW.unit_id;
    IF asg_at IS NULL OR NEW.recorded_at <= asg_at THEN
        RAISE EXCEPTION 'poslearn_experiment_outcomes: an outcome follows '
                        'its persisted assignment'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS poslearn_experiment_outcomes_guard_trg
    ON poslearn_experiment_outcomes;
CREATE TRIGGER poslearn_experiment_outcomes_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON poslearn_experiment_outcomes
    FOR EACH ROW EXECUTE FUNCTION poslearn_experiment_outcomes_guard();
