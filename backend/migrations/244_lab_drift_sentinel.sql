-- ══════════════════════════════════════════════════════════════════════
-- 244 · LAB-F POLICY / MARKET DRIFT SENTINEL: THE SHADOW RECORD
--       (BETTOR INDEPENDENT PROFITABILITY LAB, SHADOW_RESEARCH_ONLY)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS HOLDS. A drift run (sportsassets/lab/drift_sentinel.py)
-- compares, per ACTIVE strategy and its active policy version, the
-- distribution of each monitored metric in the version's FIRST-RUN window
-- (no policy version records a validation window at this base) against the
-- recent comparison window: KS / chi-square p-values, Benjamini-Hochberg
-- q-values, PSI, quantile shifts with bootstrap intervals, and a status of
-- NORMAL / WATCH / MATERIAL_DRIFT / UNAVAILABLE (with the reason).
--
--   lab_drift_runs      ONE ROW PER RECORDED RUN: the clock it was computed
--                       AS OF, the parameters, the answer to the owner's PM
--                       question H, the per-strategy roll-up with its
--                       confidence modifier (INFORMATION ONLY), and the
--                       SHA-256 of the canonical report.
--   lab_drift_findings  ONE ROW PER (run, strategy, version, league, metric,
--                       evidence class): status, statistic, p, q, n_ref,
--                       n_cmp, windows. UNAVAILABLE carries its reason; a
--                       tested status carries p and q (CHECK).
--   lab_drift_tasks     THE WORK A MATERIAL DRIFT CREATES, recorded once per
--                       drift episode (deterministic id): Audrey's policy
--                       revalidation and Scout's research question (shaped
--                       as agent_work_requests rows and NOT enqueued -- the
--                       226 queue admits only Xavier's fresh-evidence kinds;
--                       the seven-agent queue is in flight), and the
--                       tournament / research task record.
--
-- NO AUTHORITY. Written only by sportsassets/lab/drift_store.py, called by
-- the operator CLI `python -m sportsassets.lab.drift_runner --record`; read
-- by nothing on a decision path (an import-guard test pins both
-- directions). Drift never changes a policy, threshold, size, limit,
-- allowlist or order. The database says so:
--   * authority = 'SHADOW_RESEARCH_ONLY', production_effect = 'NONE'
--     (CHECK) on every table;
--   * APPEND-ONLY: UPDATE, DELETE and TRUNCATE are refused by a trigger;
--   * a work item is never enqueued from here: integration_status is
--     'NOT_ENQUEUED_RETURNED_FOR_LATER_INTEGRATION' (CHECK);
--   * the agents named are exactly Audrey and Scout (CHECK).
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP TRIGGER IF EXISTS. No
-- BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction together with its schema_migrations row.

CREATE TABLE IF NOT EXISTS lab_drift_runs (
    run_id              text        PRIMARY KEY,
    as_of               timestamptz NOT NULL,
    computed_at         timestamptz NOT NULL,
    sentinel_version    text        NOT NULL,
    stats_version       text        NOT NULL,
    params              jsonb       NOT NULL,
    question_h          jsonb       NOT NULL,
    strategies          jsonb       NOT NULL,
    findings_n          integer     NOT NULL,
    tests_n             integer     NOT NULL,
    report_sha256       text        NOT NULL,
    recorded_by         text        NOT NULL,
    authority           text        NOT NULL DEFAULT 'SHADOW_RESEARCH_ONLY',
    production_effect   text        NOT NULL DEFAULT 'NONE',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lab_drift_runs_id_ck CHECK (run_id ~ '^labdriftrun:[0-9a-f]{24}$'),
    CONSTRAINT lab_drift_runs_sha_ck CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT lab_drift_runs_clock_ck CHECK (as_of <= computed_at),
    CONSTRAINT lab_drift_runs_counts_ck CHECK (
        findings_n >= 0 AND tests_n >= 0 AND tests_n <= findings_n),
    CONSTRAINT lab_drift_runs_json_ck CHECK (
        jsonb_typeof(params) = 'object'
        AND jsonb_typeof(question_h) = 'object'
        AND question_h ? 'answer'
        AND jsonb_typeof(strategies) = 'array'),
    CONSTRAINT lab_drift_runs_authority_ck CHECK (
        authority = 'SHADOW_RESEARCH_ONLY'),
    CONSTRAINT lab_drift_runs_effect_ck CHECK (production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS lab_drift_runs_as_of_idx
    ON lab_drift_runs (as_of DESC);

CREATE TABLE IF NOT EXISTS lab_drift_findings (
    run_id              text        NOT NULL REFERENCES lab_drift_runs,
    finding_no          integer     NOT NULL,
    strategy            text        NOT NULL,
    version_key         text        NOT NULL,
    league              text        NOT NULL,
    metric              text        NOT NULL,
    evidence_class      text        NOT NULL,
    status              text        NOT NULL,
    why                 text,
    p                   double precision,
    q                   double precision,
    n_ref               integer     NOT NULL,
    n_cmp               integer     NOT NULL,
    statistic           jsonb,
    windows             jsonb       NOT NULL,
    source              text        NOT NULL,
    authority           text        NOT NULL DEFAULT 'SHADOW_RESEARCH_ONLY',
    PRIMARY KEY (run_id, finding_no),
    CONSTRAINT lab_drift_findings_status_ck CHECK (status IN (
        'NORMAL', 'WATCH', 'MATERIAL_DRIFT', 'UNAVAILABLE')),
    CONSTRAINT lab_drift_findings_evidence_ck CHECK (evidence_class IN (
        'OBSERVED_AT_DECISION', 'PAPER_SIMULATION', 'LIVE_SHADOW',
        'ACTUAL')),
    -- UNAVAILABLE NAMES WHY; A TESTED STATUS CARRIES ITS p AND q
    CONSTRAINT lab_drift_findings_unavailable_ck CHECK (
        status <> 'UNAVAILABLE'
        OR (length(btrim(coalesce(why, ''))) >= 1
            AND p IS NULL AND q IS NULL)),
    -- (every operand IS NOT NULL first: a CHECK passes on NULL)
    CONSTRAINT lab_drift_findings_tested_ck CHECK (
        status = 'UNAVAILABLE'
        OR (p IS NOT NULL AND q IS NOT NULL AND statistic IS NOT NULL
            AND p BETWEEN 0 AND 1 AND q BETWEEN 0 AND 1 AND q >= p
            AND n_ref >= 1 AND n_cmp >= 1)),
    CONSTRAINT lab_drift_findings_n_ck CHECK (n_ref >= 0 AND n_cmp >= 0),
    CONSTRAINT lab_drift_findings_authority_ck CHECK (
        authority = 'SHADOW_RESEARCH_ONLY')
);
CREATE INDEX IF NOT EXISTS lab_drift_findings_strategy_idx
    ON lab_drift_findings (strategy, metric, run_id);

CREATE TABLE IF NOT EXISTS lab_drift_tasks (
    task_id             text        PRIMARY KEY,
    run_id              text        NOT NULL REFERENCES lab_drift_runs,
    task_class          text        NOT NULL,
    agent_id            text,
    kind                text        NOT NULL,
    strategy            text        NOT NULL,
    version_key         text        NOT NULL,
    record              jsonb       NOT NULL,
    integration_status  text,
    authority           text        NOT NULL DEFAULT 'SHADOW_RESEARCH_ONLY',
    production_effect   text        NOT NULL DEFAULT 'NONE',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lab_drift_tasks_class_ck CHECK (task_class IN (
        'WORK_ITEM', 'RESEARCH_TASK')),
    CONSTRAINT lab_drift_tasks_id_ck CHECK (
        (task_class = 'WORK_ITEM' AND task_id ~ '^labdrift:[0-9a-f]{24}$')
        OR (task_class = 'RESEARCH_TASK'
            AND task_id ~ '^labdrifttask:[0-9a-f]{24}$')),
    -- REVALIDATION WORK GOES TO AUDREY AND SCOUT ONLY, AND IS NEVER
    -- ENQUEUED FROM HERE
    CONSTRAINT lab_drift_tasks_work_ck CHECK (
        task_class <> 'WORK_ITEM'
        OR (coalesce(agent_id, '') IN ('AUDREY', 'SCOUT')
            AND kind IN ('POLICY_REVALIDATION', 'RESEARCH_QUESTION')
            AND coalesce(integration_status, '')
                = 'NOT_ENQUEUED_RETURNED_FOR_LATER_INTEGRATION')),
    CONSTRAINT lab_drift_tasks_research_ck CHECK (
        task_class <> 'RESEARCH_TASK'
        OR (agent_id IS NULL AND kind = 'TOURNAMENT_REVALIDATION'
            AND integration_status IS NULL)),
    CONSTRAINT lab_drift_tasks_record_ck CHECK (
        jsonb_typeof(record) = 'object'),
    CONSTRAINT lab_drift_tasks_authority_ck CHECK (
        authority = 'SHADOW_RESEARCH_ONLY'),
    CONSTRAINT lab_drift_tasks_effect_ck CHECK (production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS lab_drift_tasks_strategy_idx
    ON lab_drift_tasks (strategy, version_key, recorded_at DESC);

-- ── APPEND-ONLY: every lab_drift_* table refuses UPDATE, DELETE, TRUNCATE
CREATE OR REPLACE FUNCTION lab_drift_record_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (a later run is a new row)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['lab_drift_runs', 'lab_drift_findings',
                             'lab_drift_tasks'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION '
                       'lab_drift_record_is_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION '
                       'lab_drift_record_is_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;
