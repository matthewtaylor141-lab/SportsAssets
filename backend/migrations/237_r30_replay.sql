-- ══════════════════════════════════════════════════════════════════════
-- 237 · THE HISTORICAL R30 DECISION REPLAY (RESEARCH / TOURNAMENT EVIDENCE)
-- ══════════════════════════════════════════════════════════════════════
--
-- Owner red-team acceptance addendum (specs/HISTORICAL_REPLAY.md): replay
-- each historical opportunity through the R30 chain with ONLY the evidence
-- recorded by the replay clock, price the counterfactuals and the Alpha
-- Attribution V2 decomposition per independent event, and keep every run.
--
--   r30_replay_runs        one row per run: run id, the code sha that ran
--                          it, its parameters (+ sha), the clock range and
--                          the summary
--   r30_replay_decisions   one row per replayed decision: its decision clock
--                          and the LATEST RECORDED STAMP its decision-step
--                          inputs used -- CHECKed <= the decision clock (the
--                          anti-hindsight rule, enforced in the database
--                          too) -- and its outcome inputs used, CHECKed <=
--                          the run's horizon
--   r30_replay_events      one row per independent event of a run
--
-- NO AUTHORITY. Every row CHECKs label = 'REPLAY_NOT_FORWARD_EVIDENCE',
-- authority = 'NO_AUTHORITY_RESEARCH_TOURNAMENT_EVIDENCE' and
-- production_effect = 'NONE': a replay result is never forward evidence,
-- never activation evidence and cannot promote production policy. Nothing
-- here touches an order, a decision, a rail, a threshold, a control or
-- capital.
--
-- APPEND-ONLY: UPDATE, DELETE and TRUNCATE are refused on all three tables.
-- A run is written in one transaction (run, decisions, events).
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

CREATE OR REPLACE FUNCTION r30_replay_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'R30_REPLAY_APPEND_ONLY: % on % is refused (append-only research record)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;

CREATE TABLE IF NOT EXISTS r30_replay_runs (
    run_id             text PRIMARY KEY,
    run_version        text NOT NULL,
    code_sha           text NOT NULL,
    parameters         jsonb NOT NULL,
    params_sha         text NOT NULL,
    clock_start        timestamptz NOT NULL,
    clock_end          timestamptz NOT NULL,
    started_at         timestamptz NOT NULL,
    finished_at        timestamptz NOT NULL,
    decisions_n        integer NOT NULL,
    events_n           integer NOT NULL,
    summary            jsonb NOT NULL,
    triggered_by       text NOT NULL,
    label              text NOT NULL DEFAULT 'REPLAY_NOT_FORWARD_EVIDENCE',
    authority          text NOT NULL
                       DEFAULT 'NO_AUTHORITY_RESEARCH_TOURNAMENT_EVIDENCE',
    production_effect  text NOT NULL DEFAULT 'NONE',
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT r30rr_id_ck CHECK (run_id ~ '^r30rp_[0-9a-f]{24}$'),
    CONSTRAINT r30rr_params_sha_ck CHECK (params_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT r30rr_code_sha_ck CHECK (
        code_sha ~ '^[0-9a-f]{40}$' OR code_sha LIKE 'UNAVAILABLE:%'),
    CONSTRAINT r30rr_params_ck CHECK (jsonb_typeof(parameters) = 'object'),
    CONSTRAINT r30rr_clock_ck CHECK (clock_start <= clock_end),
    -- a replay reads only what was recorded: its horizon is never after
    -- the run started
    CONSTRAINT r30rr_horizon_ck CHECK (clock_end <= started_at),
    CONSTRAINT r30rr_finish_ck CHECK (finished_at >= started_at),
    CONSTRAINT r30rr_counts_ck CHECK (decisions_n >= 0 AND events_n >= 0
                                      AND events_n <= decisions_n),
    CONSTRAINT r30rr_trigger_ck CHECK (
        length(btrim(triggered_by)) BETWEEN 1 AND 200),
    CONSTRAINT r30rr_label_ck CHECK (label = 'REPLAY_NOT_FORWARD_EVIDENCE'),
    CONSTRAINT r30rr_authority_ck CHECK (
        authority = 'NO_AUTHORITY_RESEARCH_TOURNAMENT_EVIDENCE'),
    CONSTRAINT r30rr_no_production_effect_ck CHECK (production_effect = 'NONE'),
    CONSTRAINT r30rr_summary_label_ck CHECK (
        summary->>'label' = 'REPLAY_NOT_FORWARD_EVIDENCE')
);
CREATE INDEX IF NOT EXISTS r30rr_recorded_idx ON r30_replay_runs (recorded_at DESC);

CREATE TABLE IF NOT EXISTS r30_replay_decisions (
    run_id                              text NOT NULL REFERENCES r30_replay_runs,
    decision_id                         text NOT NULL,
    event_key                           text NOT NULL,
    sleeve                              text NOT NULL,
    verdict                             text NOT NULL,
    decision_clock                      timestamptz NOT NULL,
    clock_end                           timestamptz NOT NULL,
    decision_evidence_max_recorded_at   timestamptz,
    outcome_evidence_max_recorded_at    timestamptz,
    payload                             jsonb NOT NULL,
    label                               text NOT NULL
                                        DEFAULT 'REPLAY_NOT_FORWARD_EVIDENCE',
    production_effect                   text NOT NULL DEFAULT 'NONE',
    recorded_at                         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, decision_id),
    CONSTRAINT r30rd_sleeve_ck CHECK (sleeve IN ('INVESTMENT', 'TRAINING',
                                                 'BENCHMARK', 'UNCLASSIFIED')),
    CONSTRAINT r30rd_verdict_ck CHECK (verdict IN ('ENTER', 'REFUSE')),
    CONSTRAINT r30rd_clock_ck CHECK (decision_clock <= clock_end),
    -- THE ANTI-HINDSIGHT RULE: no input of the replayed decision was
    -- recorded after its clock
    CONSTRAINT r30rd_no_hindsight_ck CHECK (
        decision_evidence_max_recorded_at IS NULL
        OR decision_evidence_max_recorded_at <= decision_clock),
    CONSTRAINT r30rd_horizon_ck CHECK (
        outcome_evidence_max_recorded_at IS NULL
        OR outcome_evidence_max_recorded_at <= clock_end),
    CONSTRAINT r30rd_label_ck CHECK (label = 'REPLAY_NOT_FORWARD_EVIDENCE'
                                     AND payload->>'label' = label),
    CONSTRAINT r30rd_no_production_effect_ck CHECK (production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS r30rd_event_idx ON r30_replay_decisions (run_id, event_key);

CREATE TABLE IF NOT EXISTS r30_replay_events (
    run_id                    text NOT NULL REFERENCES r30_replay_runs,
    event_key                 text NOT NULL,
    sleeves                   text[] NOT NULL,
    investment_only           boolean NOT NULL,
    decisions_n               integer NOT NULL,
    clock_end                 timestamptz NOT NULL,
    evidence_max_recorded_at  timestamptz,
    payload                   jsonb NOT NULL,
    label                     text NOT NULL DEFAULT 'REPLAY_NOT_FORWARD_EVIDENCE',
    production_effect         text NOT NULL DEFAULT 'NONE',
    recorded_at               timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, event_key),
    CONSTRAINT r30re_n_ck CHECK (decisions_n >= 1),
    CONSTRAINT r30re_investment_ck CHECK (
        investment_only = (sleeves = ARRAY['INVESTMENT']::text[])),
    CONSTRAINT r30re_horizon_ck CHECK (
        evidence_max_recorded_at IS NULL
        OR evidence_max_recorded_at <= clock_end),
    CONSTRAINT r30re_label_ck CHECK (label = 'REPLAY_NOT_FORWARD_EVIDENCE'
                                     AND payload->>'label' = label),
    CONSTRAINT r30re_no_production_effect_ck CHECK (production_effect = 'NONE')
);

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['r30_replay_runs', 'r30_replay_decisions',
                             'r30_replay_events'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION r30_replay_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION r30_replay_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;
