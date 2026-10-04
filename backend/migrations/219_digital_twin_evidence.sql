-- ══════════════════════════════════════════════════════════════════════
-- 219 · THE ECONOMIC DIGITAL TWIN AND THE PROFITABILITY EVIDENCE LAYER
--       (counterfactual replay, cross-sport transfer research, agent
--        financial scorecards, the evidence ladder, economic kill-switch
--        RECOMMENDATIONS, and the agent / handoff / macro evaluations)
-- ══════════════════════════════════════════════════════════════════════
--
-- EVERYTHING HERE IS RESEARCH. The runner (sportsassets/twin/runner.py)
-- reads the recorded production stream, computes, and persists ONLY into
-- the twin_* tables below. It has NO authority: it places, cancels, sizes
-- and activates nothing, and no module outside sportsassets/twin and the
-- GET-only api/command_twin.py reads what it writes. The database says so
-- where a column could otherwise be talked into saying the opposite:
--
--   * label: twin results are 'COUNTERFACTUAL'; every other table 'RESEARCH'
--     (CHECK). The recorded baseline a result compares against is carried
--     inside it with its own label PAPER or ACTUAL, and one result row is
--     one basis book: PAPER-basis and ACTUAL-basis are never summed
--     (summed_across_books = false, CHECK);
--   * a twin result is research_only = true and production_truth = false
--     (CHECK);
--   * a scenario's spec, a frozen criteria spec and a transfer test's
--     predeclared criteria are IMMUTABLE once stored (trigger), and their
--     identifiers are derived from the spec's sha256 (CHECK);
--   * NO FUTURE INFORMATION: every decision trace stores the instant of the
--     decision and the latest record instant it read; max_input_at <=
--     decision_at (CHECK);
--   * a transfer evaluation only counts target-sport records at or after the
--     test's declaration, and cannot classify TRANSFERABLE / SPORT_SPECIFIC
--     below the predeclared minimum forward sample (trigger);
--   * a kill-switch row can only be recommendation = 'RECOMMEND_PAUSE' with
--     authority 'RESEARCH_NO_AUTHORITY', applied = false and
--     stops_capital = false (CHECK) -- it stops nothing;
--   * an agent scorecard metric that is not measured is NULL with a reason,
--     never 0 (CHECK), and the evidence level cannot skip a level: it is
--     the number of LEADING passed levels (CHECK via twin_leading_level),
--     with profitability confidence UNPROVEN below level 4 (CHECK).
--
-- APPEND-ONLY: every twin_* row is evidence; UPDATE and DELETE are refused
-- by trigger (a later fact is a new row). Growth is bounded by content
-- de-duplication: a scenario result, a transfer evaluation, a snapshot and
-- a kill-switch recommendation are not re-inserted while their inputs are
-- unchanged (UNIQUE on the input / evidence hash).
--
-- INTERFACES. The parallel streams (216 economics, 217 EDDIE / SCOUT,
-- 218 tournaments) are NOT dependencies of this migration. The twin reads
-- them only through the views pos_iface_eddie_execution,
-- pos_iface_scout_feature_effects and pos_iface_tournament_verdicts when
-- (and only when) those views exist with the documented columns; otherwise
-- the dependent scenario / metric / criterion is UNAVAILABLE with a reason.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP-then-CREATE for triggers.
-- No BEGIN/COMMIT of its own (the runner wraps each file in a transaction).

-- ── THE GUARDS (functions) ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION twin_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only research evidence: % refused (a later '
                    'fact is a new row)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

-- the number of LEADING true entries minus one: level L is reached only if
-- levels 0..L all pass, so no level is ever skipped.
CREATE OR REPLACE FUNCTION twin_leading_level(passes boolean[])
RETURNS integer LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce((SELECT min(i) - 1 FROM generate_subscripts(passes, 1) i
                      WHERE passes[i] IS NOT TRUE),
                    coalesce(array_length(passes, 1), 0)) - 1
$$;

-- ── ONE ROW PER COMPONENT PER RUN ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS twin_runs (
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz NOT NULL,
    status              text        NOT NULL,
    error               text,
    duration_ms         integer     NOT NULL,
    summary             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'RESEARCH_NO_AUTHORITY',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, component),
    CONSTRAINT twin_runs_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_runs_authority_ck CHECK (
        authority = 'RESEARCH_NO_AUTHORITY'),
    CONSTRAINT twin_runs_status_ck CHECK (status IN (
        'OK', 'FAILED', 'TIMEOUT', 'SKIPPED')),
    CONSTRAINT twin_runs_component_ck CHECK (component IN (
        'CYCLE', 'TWIN', 'TRANSFER', 'SCORECARDS', 'LADDER',
        'KILL_SWITCHES', 'EVALS'))
);
CREATE INDEX IF NOT EXISTS twin_runs_at_idx
    ON twin_runs (component, started_at DESC);

-- ── THE PAYLOAD A READ ENDPOINT SERVES (latest per component) ────────
CREATE TABLE IF NOT EXISTS twin_snapshots (
    snapshot_id         bigserial PRIMARY KEY,
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    payload             jsonb       NOT NULL,
    content_sha256      text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT twin_snapshots_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_snapshots_component_ck CHECK (component IN (
        'TWIN', 'TRANSFER', 'SCORECARDS', 'LADDER', 'KILL_SWITCHES',
        'EVALS')),
    CONSTRAINT twin_snapshots_sha_ck CHECK (
        content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT twin_snapshots_one_per_run UNIQUE (run_id, component)
);
CREATE INDEX IF NOT EXISTS twin_snapshots_latest_idx
    ON twin_snapshots (component, computed_at DESC);

-- ── FROZEN CRITERIA (ladder, kill switches, transfer, eval rubric) ───
CREATE TABLE IF NOT EXISTS twin_frozen_specs (
    spec_id             text PRIMARY KEY,
    kind                text        NOT NULL,
    version             integer     NOT NULL,
    spec                jsonb       NOT NULL,
    spec_sha256         text        NOT NULL,
    frozen_at           timestamptz NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT twin_specs_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_specs_kind_ck CHECK (kind IN (
        'LADDER_CRITERIA', 'CONFIDENCE_RULES', 'KILL_SWITCH_CRITERIA',
        'TRANSFER_CRITERIA', 'EVAL_RUBRIC', 'SCORECARD_RULES')),
    CONSTRAINT twin_specs_sha_ck CHECK (spec_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT twin_specs_id_ck CHECK (
        spec_id = kind || ':v' || version || ':' || left(spec_sha256, 16)),
    CONSTRAINT twin_specs_once UNIQUE (kind, version)
);

-- ── THE FROZEN, VERSIONED SCENARIO REGISTER ───────────────────────────
CREATE TABLE IF NOT EXISTS twin_scenarios (
    scenario_id         text PRIMARY KEY,
    scenario_key        text        NOT NULL,
    version             integer     NOT NULL,
    world               text        NOT NULL,
    spec                jsonb       NOT NULL,
    spec_sha256         text        NOT NULL,
    engine_version      text        NOT NULL,
    frozen_at           timestamptz NOT NULL,
    label               text        NOT NULL DEFAULT 'COUNTERFACTUAL',
    authority           text        NOT NULL DEFAULT 'RESEARCH_NO_AUTHORITY',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT twin_scenarios_label_ck CHECK (label = 'COUNTERFACTUAL'),
    CONSTRAINT twin_scenarios_authority_ck CHECK (
        authority = 'RESEARCH_NO_AUTHORITY'),
    CONSTRAINT twin_scenarios_sha_ck CHECK (spec_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT twin_scenarios_id_ck CHECK (
        scenario_id = 'twinscn:' || left(spec_sha256, 24)),
    CONSTRAINT twin_scenarios_world_ck CHECK (world IN (
        'RECORDED', 'DEREK_THRESHOLD', 'SIZING', 'XAVIER_ALWAYS_HOLD',
        'XAVIER_IMMEDIATE_EXIT', 'ALLOCATOR', 'EDDIE_EXECUTION',
        'SCOUT_FEATURE', 'KAREN_BLOCK')),
    CONSTRAINT twin_scenarios_key_version UNIQUE (scenario_key, version),
    CONSTRAINT twin_scenarios_spec_once UNIQUE (spec_sha256)
);

-- ── ONE COUNTERFACTUAL RESULT PER SCENARIO PER BASIS PER INPUT SET ───
CREATE TABLE IF NOT EXISTS twin_scenario_results (
    result_id           text PRIMARY KEY,
    run_id              text        NOT NULL,
    scenario_id         text        NOT NULL REFERENCES twin_scenarios,
    spec_sha256         text        NOT NULL,
    basis_book          text        NOT NULL,
    status              text        NOT NULL,
    unavailable_reason  text,
    window_start        timestamptz NOT NULL,
    window_end          timestamptz NOT NULL,
    input_sha256        text        NOT NULL,
    output_sha256       text        NOT NULL,
    engine_version      text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    baseline            jsonb,
    world               jsonb,
    comparison          jsonb,
    counts              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    research_only       boolean     NOT NULL DEFAULT true,
    production_truth    boolean     NOT NULL DEFAULT false,
    summed_across_books boolean     NOT NULL DEFAULT false,
    label               text        NOT NULL DEFAULT 'COUNTERFACTUAL',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT twin_results_label_ck CHECK (label = 'COUNTERFACTUAL'),
    CONSTRAINT twin_results_basis_ck CHECK (basis_book IN ('PAPER', 'ACTUAL')),
    CONSTRAINT twin_results_status_ck CHECK (status IN ('OK', 'UNAVAILABLE')),
    CONSTRAINT twin_results_research_ck CHECK (
        research_only AND NOT production_truth AND NOT summed_across_books),
    CONSTRAINT twin_results_unavailable_ck CHECK (
        (status = 'OK' AND unavailable_reason IS NULL AND world IS NOT NULL)
        OR (status = 'UNAVAILABLE' AND unavailable_reason IS NOT NULL
            AND world IS NULL)),
    CONSTRAINT twin_results_baseline_label_ck CHECK (
        baseline IS NULL OR baseline->>'label' = basis_book),
    CONSTRAINT twin_results_world_label_ck CHECK (
        world IS NULL OR world->>'label' = 'COUNTERFACTUAL'),
    CONSTRAINT twin_results_window_ck CHECK (window_start <= window_end),
    CONSTRAINT twin_results_sha_ck CHECK (
        input_sha256 ~ '^[0-9a-f]{64}$' AND output_sha256 ~ '^[0-9a-f]{64}$'
        AND spec_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT twin_results_once UNIQUE (scenario_id, basis_book,
                                         input_sha256)
);
CREATE INDEX IF NOT EXISTS twin_results_latest_idx
    ON twin_scenario_results (scenario_id, basis_book, computed_at DESC);

-- ── EVERY WORLD DECISION, WITH WHAT IT COULD SEE ──────────────────────
CREATE TABLE IF NOT EXISTS twin_decision_traces (
    result_id           text        NOT NULL REFERENCES twin_scenario_results,
    seq                 integer     NOT NULL,
    subject_id          text        NOT NULL,
    decision_kind       text        NOT NULL,
    decision_at         timestamptz NOT NULL,
    max_input_at        timestamptz,
    inputs_read         integer     NOT NULL,
    recorded_action     text,
    world_action        text        NOT NULL,
    pnl_usd             double precision,
    pnl_basis           text,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    label               text        NOT NULL DEFAULT 'COUNTERFACTUAL',
    PRIMARY KEY (result_id, seq),
    CONSTRAINT twin_traces_label_ck CHECK (label = 'COUNTERFACTUAL'),
    CONSTRAINT twin_traces_kind_ck CHECK (decision_kind IN (
        'ENTRY', 'SIZE', 'MANAGE', 'ALLOCATE', 'EXECUTE', 'BLOCK')),
    -- NO FUTURE INFORMATION: a world decision at t read nothing after t
    CONSTRAINT twin_traces_no_future_ck CHECK (
        max_input_at IS NULL OR max_input_at <= decision_at),
    CONSTRAINT twin_traces_unmeasured_ck CHECK (
        pnl_usd IS NOT NULL OR unmeasured ? 'pnl_usd')
);

-- ── CROSS-SPORT TRANSFER: THE PREREGISTRATION ─────────────────────────
CREATE TABLE IF NOT EXISTS twin_transfer_tests (
    test_id             text PRIMARY KEY,
    dimension           text        NOT NULL,
    source_sport        text        NOT NULL,
    target_sport        text        NOT NULL,
    hypothesis          text        NOT NULL,
    metric              text        NOT NULL,
    criteria            jsonb       NOT NULL,
    criteria_sha256     text        NOT NULL,
    criteria_spec_id    text        NOT NULL REFERENCES twin_frozen_specs,
    declared_at         timestamptz NOT NULL,
    source_window_end   timestamptz NOT NULL,
    source_n            integer     NOT NULL,
    source_value        double precision,
    source_ci_low       double precision,
    source_ci_high      double precision,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT twin_transfer_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_transfer_dimension_ck CHECK (dimension IN (
        'FAVORITE_UNDERDOG_CALIBRATION', 'PROBABILITY_BANDS',
        'SPREAD_BEHAVIOUR', 'LIQUIDITY', 'LIVE_EXECUTION', 'TIME_TO_EVENT',
        'ADVERSE_SELECTION')),
    CONSTRAINT twin_transfer_sports_ck CHECK (
        source_sport <> target_sport
        AND length(btrim(source_sport)) > 0 AND length(btrim(target_sport)) > 0),
    CONSTRAINT twin_transfer_source_before_ck CHECK (
        source_window_end <= declared_at),
    CONSTRAINT twin_transfer_criteria_ck CHECK (
        criteria ? 'min_forward_n' AND criteria ? 'tolerance'
        AND criteria_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT twin_transfer_source_ck CHECK (
        source_n > 0 AND source_value IS NOT NULL),
    CONSTRAINT twin_transfer_once UNIQUE (dimension, source_sport,
                                          target_sport, criteria_sha256)
);

-- ── CROSS-SPORT TRANSFER: FORWARD EVALUATIONS (target sport only) ────
CREATE TABLE IF NOT EXISTS twin_transfer_evaluations (
    evaluation_id       bigserial PRIMARY KEY,
    test_id             text        NOT NULL REFERENCES twin_transfer_tests,
    run_id              text        NOT NULL,
    evaluated_at        timestamptz NOT NULL,
    forward_start       timestamptz NOT NULL,
    forward_n           integer     NOT NULL,
    target_value        double precision,
    target_ci_low       double precision,
    target_ci_high      double precision,
    diff                double precision,
    diff_ci_low         double precision,
    diff_ci_high        double precision,
    classification      text        NOT NULL,
    reason              text        NOT NULL,
    input_sha256        text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT twin_transfer_eval_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_transfer_eval_class_ck CHECK (classification IN (
        'TRANSFERABLE', 'SPORT_SPECIFIC', 'UNKNOWN')),
    CONSTRAINT twin_transfer_eval_n_ck CHECK (forward_n >= 0),
    CONSTRAINT twin_transfer_eval_empty_ck CHECK (
        forward_n > 0 OR (target_value IS NULL AND diff IS NULL
                          AND classification = 'UNKNOWN')),
    CONSTRAINT twin_transfer_eval_once UNIQUE (test_id, input_sha256)
);
CREATE INDEX IF NOT EXISTS twin_transfer_eval_latest_idx
    ON twin_transfer_evaluations (test_id, evaluated_at DESC);

CREATE OR REPLACE FUNCTION twin_transfer_forward_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t record;
BEGIN
    SELECT declared_at, criteria INTO t FROM twin_transfer_tests
     WHERE test_id = NEW.test_id;
    IF NEW.forward_start < t.declared_at THEN
        RAISE EXCEPTION 'twin_transfer_evaluations: forward window starts '
                        'before the test was declared (in-sample data)';
    END IF;
    IF NEW.classification <> 'UNKNOWN'
       AND NEW.forward_n < (t.criteria->>'min_forward_n')::int THEN
        RAISE EXCEPTION 'twin_transfer_evaluations: % below the predeclared '
                        'minimum forward sample', NEW.classification;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS twin_transfer_forward_only_trg
    ON twin_transfer_evaluations;
CREATE TRIGGER twin_transfer_forward_only_trg
    BEFORE INSERT ON twin_transfer_evaluations
    FOR EACH ROW EXECUTE FUNCTION twin_transfer_forward_only();

-- ── AGENT FINANCIAL SCORECARDS: ECONOMIC CONTRIBUTION ONLY ────────────
CREATE TABLE IF NOT EXISTS twin_agent_scorecards (
    run_id              text        NOT NULL,
    agent               text        NOT NULL,
    metric              text        NOT NULL,
    book                text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    value               double precision,
    numerator           double precision,
    denominator         double precision,
    sample_n            integer,
    ci_low              double precision,
    ci_high             double precision,
    ci_method           text,
    status              text        NOT NULL,
    reason              text,
    basis               text        NOT NULL,
    unit                text,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    PRIMARY KEY (run_id, agent, metric, book),
    CONSTRAINT twin_scorecards_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_scorecards_agent_ck CHECK (agent IN (
        'DEREK', 'XAVIER', 'EDDIE', 'SCOUT', 'KAREN', 'ALLOCATOR',
        'AUDREY')),
    CONSTRAINT twin_scorecards_book_ck CHECK (book IN (
        'ACTUAL', 'PAPER', 'COUNTERFACTUAL')),
    CONSTRAINT twin_scorecards_status_ck CHECK (status IN (
        'MEASURED', 'INSUFFICIENT_SAMPLE', 'UNAVAILABLE', 'UNPROVEN')),
    -- UNMEASURED IS NULL WITH A REASON, NEVER 0
    CONSTRAINT twin_scorecards_null_ck CHECK (
        (status IN ('MEASURED', 'INSUFFICIENT_SAMPLE') AND value IS NOT NULL)
        OR (status IN ('UNAVAILABLE', 'UNPROVEN') AND value IS NULL
            AND reason IS NOT NULL)),
    -- ECONOMIC CONTRIBUTION, NEVER VOLUME OF ANALYSIS
    CONSTRAINT twin_scorecards_economic_ck CHECK (
        metric !~* '(volume|analyses|reports_written|messages|words|tokens)')
);
CREATE INDEX IF NOT EXISTS twin_scorecards_at_idx
    ON twin_agent_scorecards (computed_at DESC);

-- ── THE PROFITABILITY EVIDENCE LADDER ─────────────────────────────────
CREATE TABLE IF NOT EXISTS twin_evidence_ladder (
    run_id              text PRIMARY KEY,
    computed_at         timestamptz NOT NULL,
    level               integer     NOT NULL,
    passes              boolean[]   NOT NULL,
    levels              jsonb       NOT NULL,
    criteria_spec_id    text        NOT NULL REFERENCES twin_frozen_specs,
    criteria_sha256     text        NOT NULL,
    confidence_status   text        NOT NULL,
    confidence          jsonb       NOT NULL,
    confidence_spec_id  text        NOT NULL REFERENCES twin_frozen_specs,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    CONSTRAINT twin_ladder_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_ladder_range_ck CHECK (level BETWEEN 0 AND 6
                                           AND array_length(passes, 1) = 7),
    -- NEVER SKIP A LEVEL
    CONSTRAINT twin_ladder_leading_ck CHECK (
        level = twin_leading_level(passes)),
    CONSTRAINT twin_ladder_confidence_ck CHECK (confidence_status IN (
        'UNPROVEN', 'LOW', 'MODERATE', 'HIGH')),
    -- profitability confidence beyond UNPROVEN needs statistically
    -- credible small-live net edge (level 4) first
    CONSTRAINT twin_ladder_confidence_level_ck CHECK (
        confidence_status = 'UNPROVEN' OR level >= 4)
);
CREATE INDEX IF NOT EXISTS twin_ladder_at_idx
    ON twin_evidence_ladder (computed_at DESC);

-- ── ECONOMIC KILL SWITCHES: RECOMMENDATIONS, NEVER ACTIONS ────────────
CREATE TABLE IF NOT EXISTS twin_kill_switch_recommendations (
    recommendation_id   text PRIMARY KEY,
    run_id              text        NOT NULL,
    criterion           text        NOT NULL,
    book                text        NOT NULL,
    strategy            text        NOT NULL,
    recommendation      text        NOT NULL DEFAULT 'RECOMMEND_PAUSE',
    evidence            jsonb       NOT NULL,
    evidence_sha256     text        NOT NULL,
    criteria_spec_id    text        NOT NULL REFERENCES twin_frozen_specs,
    criteria_sha256     text        NOT NULL,
    authority           text        NOT NULL DEFAULT 'RESEARCH_NO_AUTHORITY',
    applied             boolean     NOT NULL DEFAULT false,
    stops_capital       boolean     NOT NULL DEFAULT false,
    activates_capital   boolean     NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    CONSTRAINT twin_kill_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_kill_rec_ck CHECK (recommendation = 'RECOMMEND_PAUSE'),
    CONSTRAINT twin_kill_authority_ck CHECK (
        authority = 'RESEARCH_NO_AUTHORITY'),
    CONSTRAINT twin_kill_never_applied_ck CHECK (
        NOT applied AND NOT stops_capital AND NOT activates_capital),
    CONSTRAINT twin_kill_criterion_ck CHECK (criterion IN (
        'REALIZED_EDGE_BELOW_PREDICTED', 'CALIBRATION_FAILURE',
        'EXECUTION_LOSS_CONSUMES_EDGE', 'STRATEGY_DRAWDOWN',
        'CHALLENGER_BEATS_CHAMPION', 'REGIME_SHIFT')),
    CONSTRAINT twin_kill_book_ck CHECK (book IN ('PAPER', 'ACTUAL', 'NONE')),
    CONSTRAINT twin_kill_sha_ck CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT twin_kill_once UNIQUE (criterion, book, strategy,
                                      evidence_sha256)
);
CREATE INDEX IF NOT EXISTS twin_kill_at_idx
    ON twin_kill_switch_recommendations (created_at DESC);

-- ── EVALUATIONS: AGENT, HANDOFF, MACRO -- OVER PERSISTED RECORDS ─────
CREATE TABLE IF NOT EXISTS twin_evals (
    run_id              text        NOT NULL,
    scope               text        NOT NULL,
    subject             text        NOT NULL,
    dimension           text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    n_evaluated         integer     NOT NULL,
    n_passed            integer,
    pass_rate           double precision,
    ci_low              double precision,
    ci_high             double precision,
    status              text        NOT NULL,
    reason              text,
    failures            jsonb       NOT NULL DEFAULT '[]'::jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    PRIMARY KEY (run_id, scope, subject, dimension),
    CONSTRAINT twin_evals_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT twin_evals_scope_ck CHECK (scope IN (
        'AGENT', 'HANDOFF', 'MACRO')),
    CONSTRAINT twin_evals_dimension_ck CHECK (dimension IN (
        'TOOL_SELECTION', 'EVIDENCE_GROUNDING', 'INSTRUCTION_ADHERENCE',
        'HANDOFF_QUALITY', 'COMPLETENESS', 'UNSUPPORTED_CLAIMS',
        'AUTHORITY_COMPLIANCE', 'ECONOMIC_CONTRIBUTION',
        'FAILURE_ORIGIN')),
    CONSTRAINT twin_evals_status_ck CHECK (status IN (
        'MEASURED', 'INSUFFICIENT_SAMPLE', 'UNAVAILABLE')),
    CONSTRAINT twin_evals_null_ck CHECK (
        (status = 'UNAVAILABLE' AND pass_rate IS NULL AND n_passed IS NULL
         AND reason IS NOT NULL)
        OR (status <> 'UNAVAILABLE' AND n_evaluated > 0
            AND n_passed IS NOT NULL AND n_passed <= n_evaluated))
);
CREATE INDEX IF NOT EXISTS twin_evals_at_idx ON twin_evals (computed_at DESC);

-- ── APPEND-ONLY, EVERY TABLE ──────────────────────────────────────────
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['twin_runs', 'twin_snapshots',
        'twin_frozen_specs', 'twin_scenarios', 'twin_scenario_results',
        'twin_decision_traces', 'twin_transfer_tests',
        'twin_transfer_evaluations', 'twin_agent_scorecards',
        'twin_evidence_ladder', 'twin_kill_switch_recommendations',
        'twin_evals']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION twin_append_only()',
                       t || '_append_only_trg', t);
    END LOOP;
END $$;
