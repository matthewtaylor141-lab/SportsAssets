-- 310 · BETTOR CAPITAL READINESS LAB
-- Additive PAPER/SHADOW evidence only. No live authority. Append-only.

CREATE TABLE IF NOT EXISTS capital_readiness_runs (
    run_id              bigserial PRIMARY KEY,
    version             text NOT NULL,
    source_sha          text,
    status              text NOT NULL,
    readiness_status    text NOT NULL,
    readiness_score     numeric,
    recommended_capital_usd numeric NOT NULL DEFAULT 0,
    hard_gates          jsonb NOT NULL,
    blocking_gates      jsonb NOT NULL,
    payload             jsonb NOT NULL,
    computed_at         timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text NOT NULL DEFAULT 'RESEARCH',
    authority           text NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT capital_readiness_runs_status_ck CHECK (status IN ('OK','EMPTY','UNAVAILABLE')),
    CONSTRAINT capital_readiness_runs_readiness_ck CHECK (readiness_status IN ('GREEN','RED')),
    CONSTRAINT capital_readiness_runs_capital_ck CHECK (recommended_capital_usd >= 0),
    CONSTRAINT capital_readiness_runs_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT capital_readiness_runs_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT capital_readiness_runs_payload_ck CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX IF NOT EXISTS capital_readiness_runs_latest_idx
    ON capital_readiness_runs (computed_at DESC, run_id DESC);

CREATE TABLE IF NOT EXISTS capital_readiness_shadow_court (
    court_id            bigserial PRIMARY KEY,
    decision_id         text NOT NULL,
    chosen              text,
    shadow_winner       text NOT NULL,
    disagreement        boolean NOT NULL,
    alternatives        jsonb NOT NULL,
    result              jsonb,
    decided_at          timestamptz,
    scored_at           timestamptz,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    version             text NOT NULL,
    label               text NOT NULL DEFAULT 'RESEARCH',
    authority           text NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT capital_readiness_court_alt_ck CHECK (jsonb_typeof(alternatives) = 'array'),
    CONSTRAINT capital_readiness_court_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT capital_readiness_court_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY')
);
CREATE INDEX IF NOT EXISTS capital_readiness_shadow_decision_idx
    ON capital_readiness_shadow_court (decision_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS capital_readiness_agent_economics (
    observation_id      bigserial PRIMARY KEY,
    agent               text NOT NULL,
    role_metric         text NOT NULL,
    subject_id          text NOT NULL,
    economic_alpha_usd  numeric,
    detail              jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at         timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    version             text NOT NULL,
    label               text NOT NULL DEFAULT 'RESEARCH',
    authority           text NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT capital_readiness_agent_detail_ck CHECK (jsonb_typeof(detail) = 'object'),
    CONSTRAINT capital_readiness_agent_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT capital_readiness_agent_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    UNIQUE(agent, role_metric, subject_id, version)
);
CREATE INDEX IF NOT EXISTS capital_readiness_agent_obs_idx
    ON capital_readiness_agent_economics (agent, observed_at DESC);

CREATE TABLE IF NOT EXISTS capital_readiness_scale_trials (
    trial_id            bigserial PRIMARY KEY,
    scope_key           text NOT NULL,
    capital_usd         numeric NOT NULL,
    expected_net_usd    numeric,
    lower_bound_usd     numeric,
    effective_edge_bps  numeric,
    positive_lower_bound boolean NOT NULL,
    inputs              jsonb NOT NULL,
    computed_at         timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    version             text NOT NULL,
    label               text NOT NULL DEFAULT 'RESEARCH',
    authority           text NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT capital_readiness_scale_capital_ck CHECK (capital_usd >= 0),
    CONSTRAINT capital_readiness_scale_inputs_ck CHECK (jsonb_typeof(inputs) = 'object'),
    CONSTRAINT capital_readiness_scale_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT capital_readiness_scale_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY')
);
CREATE INDEX IF NOT EXISTS capital_readiness_scale_scope_idx
    ON capital_readiness_scale_trials (scope_key, computed_at DESC);

CREATE OR REPLACE FUNCTION capital_readiness_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (%): capital-readiness history is immutable', TG_TABLE_NAME, TG_OP;
END $$;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'capital_readiness_runs',
        'capital_readiness_shadow_court',
        'capital_readiness_agent_economics',
        'capital_readiness_scale_trials'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION capital_readiness_append_only()', t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION capital_readiness_append_only()', t || '_no_truncate_trg', t);
    END LOOP;
END $$;
