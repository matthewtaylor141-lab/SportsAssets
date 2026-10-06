-- ══════════════════════════════════════════════════════════════════════
-- 309 · PAPER PROFITABILITY BIND: LEARNED MODELS, PER-ENTRY ALL-IN
--       EVALUATIONS AND EXPLICIT CASH DECISIONS (PAPER ONLY, NO CAPITAL
--       AUTHORITY, APPEND-ONLY)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS RECORDS (bettor_paper_profitability_bind):
--
--   paper_profitability_models        each fit of a learned input the PAPER
--       entry gate reads: CALIBRATION (sport x market family x regime,
--       fitted on settled outcomes), EXECUTION (learned fill probability
--       and post-fill markout per strategy x style from the simulator's
--       own fills) and RESIDUAL (expected EV at the decision vs the
--       realized / settled-counterfactual result, by strategy x sport x
--       family). A new fit is a new row; the gate reads the newest.
--   paper_profitability_evaluations   one row per PAPER entry the bind
--       evaluated (decision stage or ledger stage): the descriptors, raw
--       and calibrated probability, the all-in executable EV and its
--       terms, EV per capital-hour, the capacity / correlation / capital-
--       hour size factors (each <= 1), the verdict ENTER or CASH and the
--       binding refusal. Churn control reads it (a re-entry must improve
--       the EV materially) and residual learning reads the expected EV.
--   paper_cash_decisions              the explicit CASH decision of a
--       strategy for a paper pass in which it evaluated candidates and
--       none was entered.
--
-- AUTHORITY. Every row is PAPER / PAPER_ONLY_NO_CAPITAL_AUTHORITY (CHECK):
-- nothing here raises a cap, a size, a limit or a live authority; every
-- size factor is in [0, 1] (CHECK) -- the bind only refuses or shrinks.
--
-- APPEND-ONLY. UPDATE, DELETE and TRUNCATE are refused by trigger.
-- No BEGIN/COMMIT of its own. Idempotent.

CREATE TABLE IF NOT EXISTS paper_profitability_models (
    model_id            bigserial   PRIMARY KEY,
    account_id          text        NOT NULL,
    kind                text        NOT NULL,
    version             text        NOT NULL,
    observations        integer     NOT NULL,
    payload             jsonb       NOT NULL,
    fitted_at           timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text        NOT NULL DEFAULT 'PAPER',
    authority           text        NOT NULL DEFAULT 'PAPER_ONLY_NO_CAPITAL_AUTHORITY',
    CONSTRAINT paper_prof_models_kind_ck CHECK (kind IN (
        'CALIBRATION', 'EXECUTION', 'RESIDUAL')),
    CONSTRAINT paper_prof_models_label_ck CHECK (label = 'PAPER'),
    CONSTRAINT paper_prof_models_authority_ck CHECK (
        authority = 'PAPER_ONLY_NO_CAPITAL_AUTHORITY'),
    CONSTRAINT paper_prof_models_obs_ck CHECK (observations >= 0),
    CONSTRAINT paper_prof_models_payload_ck CHECK (
        jsonb_typeof(payload) = 'object')
);
CREATE INDEX IF NOT EXISTS paper_prof_models_latest_idx
    ON paper_profitability_models (account_id, kind, fitted_at DESC,
                                   model_id DESC);

CREATE TABLE IF NOT EXISTS paper_profitability_evaluations (
    eval_id             bigserial   PRIMARY KEY,
    account_id          text        NOT NULL,
    strategy            text        NOT NULL,
    stage               text        NOT NULL,
    decision_id         text,
    order_key           text,
    us_market_slug      text,
    holding_side        text,
    fixture             text,
    sport               text,
    market_family       text,
    regime              text,
    p_raw               numeric,
    p_used              numeric,
    market_price        numeric,
    calibration_weight  numeric,
    qty_in              numeric,
    qty_out             numeric     NOT NULL DEFAULT 0,
    capacity_factor     numeric,
    correlation_factor  numeric,
    capital_hour_factor numeric,
    all_in_ev_usd       numeric,
    ev_per_contract_usd numeric,
    ev_per_capital_hour numeric,
    expected_hold_hours numeric,
    residual_haircut_per_contract numeric,
    learned_adverse_per_contract numeric,
    fill_probability    numeric,
    verdict             text        NOT NULL,
    refusal             text,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evaluated_at        timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text        NOT NULL DEFAULT 'PAPER',
    authority           text        NOT NULL DEFAULT 'PAPER_ONLY_NO_CAPITAL_AUTHORITY',
    CONSTRAINT paper_prof_eval_label_ck CHECK (label = 'PAPER'),
    CONSTRAINT paper_prof_eval_authority_ck CHECK (
        authority = 'PAPER_ONLY_NO_CAPITAL_AUTHORITY'),
    CONSTRAINT paper_prof_eval_stage_ck CHECK (stage IN ('DECISION', 'LEDGER')),
    CONSTRAINT paper_prof_eval_verdict_ck CHECK (verdict IN ('ENTER', 'CASH')),
    CONSTRAINT paper_prof_eval_refusal_ck CHECK (
        (verdict = 'ENTER') = (refusal IS NULL)),
    -- THE BIND ONLY SHRINKS: every size factor is within [0, 1] and the
    -- quantity out never exceeds the quantity in
    CONSTRAINT paper_prof_eval_factors_ck CHECK (
        (capacity_factor IS NULL OR capacity_factor BETWEEN 0 AND 1)
        AND (correlation_factor IS NULL OR correlation_factor BETWEEN 0 AND 1)
        AND (capital_hour_factor IS NULL OR capital_hour_factor BETWEEN 0 AND 1)),
    CONSTRAINT paper_prof_eval_qty_ck CHECK (
        qty_out >= 0 AND (qty_in IS NULL OR qty_out <= qty_in)),
    -- NEVER INFLATED: the probability used never exceeds the raw one
    CONSTRAINT paper_prof_eval_p_ck CHECK (
        p_used IS NULL OR p_raw IS NULL OR p_used <= p_raw),
    CONSTRAINT paper_prof_eval_detail_ck CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS paper_prof_eval_contract_idx
    ON paper_profitability_evaluations (account_id, strategy, us_market_slug,
                                        holding_side, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS paper_prof_eval_fixture_idx
    ON paper_profitability_evaluations (account_id, fixture, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS paper_prof_eval_order_idx
    ON paper_profitability_evaluations (order_key);

CREATE TABLE IF NOT EXISTS paper_cash_decisions (
    cash_id             bigserial   PRIMARY KEY,
    account_id          text        NOT NULL,
    strategy            text        NOT NULL,
    pass_at             timestamptz NOT NULL,
    window_start        timestamptz NOT NULL,
    window_end          timestamptz NOT NULL,
    decisions_evaluated integer     NOT NULL,
    entries             integer     NOT NULL DEFAULT 0,
    binding_refusals    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    best_candidate      jsonb,
    decision            text        NOT NULL DEFAULT 'CASH',
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text        NOT NULL DEFAULT 'PAPER',
    CONSTRAINT paper_cash_decision_ck CHECK (decision = 'CASH'),
    CONSTRAINT paper_cash_entries_ck CHECK (entries = 0),
    CONSTRAINT paper_cash_label_ck CHECK (label = 'PAPER'),
    CONSTRAINT paper_cash_window_ck CHECK (window_end >= window_start),
    CONSTRAINT paper_cash_refusals_ck CHECK (
        jsonb_typeof(binding_refusals) = 'object'),
    UNIQUE (account_id, strategy, pass_at)
);
CREATE INDEX IF NOT EXISTS paper_cash_decisions_at_idx
    ON paper_cash_decisions (account_id, strategy, pass_at DESC);

CREATE OR REPLACE FUNCTION paper_profitability_bind_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (%): history is never rewritten',
                    TG_TABLE_NAME, TG_OP;
END $$;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['paper_profitability_models',
                             'paper_profitability_evaluations',
                             'paper_cash_decisions'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION '
                       'paper_profitability_bind_is_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION '
                       'paper_profitability_bind_is_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;
