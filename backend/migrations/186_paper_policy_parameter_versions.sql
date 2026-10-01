-- ══════════════════════════════════════════════════════════════════════
-- 186 · PAPER ONLY: VERSIONED, BOUNDED PARAMETERS FOR THE COMPLETED-GAME
--       PAPER POLICY, ACTIVATED ONLY FROM AN EVALUATED PROPOSAL, AUDITED,
--       WITH ATOMIC ROLLBACK
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER DIRECTIVE, PAPER ONLY. Real-money execution stays disabled. This is
-- the connection between a passed improvement proposal (migration 185) and
-- what the running paper agent does -- for ONE policy and ONE parameter:
--
--     policy     PINNACLE_COMPLETED_GAME_PAPER (paper_benchmark.CG_POLICY)
--     parameter  min_gross_edge_pp   shipped default (V1) 5.0
--     bounds     4.0 <= value <= 6.0, on a 0.5 pp grid, and an activation
--                moves it at most 1.0 pp from the version it replaces
--
-- WHY THE 4.0 pp FLOOR. The deployed fee schedule charges up to ~1.75 pp
-- per contract at mid prices (theta x C x p x (1 - p), largest at p = 0.5;
-- 1.74 USD per 100 contracts at 0.50). The completed-game policy's EV is
-- CONDITIONAL on ordinary completion (exceptional settlements are priced
-- separately with UNMEASURED frequency), the venue book's currency is not
-- established (P5) and the de-vigged Pinnacle probability carries its own
-- error. 4.0 pp keeps at least ~2.25 pp of gross edge after the largest
-- fee for those unmeasured risks, and is never more than one 1 pp step
-- below the shipped 5 pp. The ceiling, 6.0 pp, bounds tightening the same
-- way. Any other parameter, policy or value is refused here (CHECK) and in
-- code.
--
-- THE TABLES
--   paper_policy_parameter_versions     IMMUTABLE versions (append-only):
--                                       the full parameter set, its SHA-256,
--                                       and for every version after V1 the
--                                       proposal, its evaluation id and the
--                                       named approver it came from.
--   paper_policy_parameter_heads        ONE row per policy: the ACTIVE
--                                       version (so exactly one is active),
--                                       changed only inside the activation /
--                                       rollback transaction, under its row
--                                       lock.
--   paper_policy_parameter_activations  the append-only audit of every
--                                       activation and rollback: from which
--                                       version to which, why, who, the
--                                       proposal / evaluation, the control
--                                       row's state at that instant.
--
-- HISTORICAL DECISIONS ARE UNTOUCHED. A decision made under a version
-- records the version id, the values used and the proposal provenance in
-- its existing policy_decision / economics JSON; nothing is added to, or
-- rewritten in, earlier rows.
--
-- NOTHING FUNDED READS THESE TABLES (an import-isolation test pins it). The
-- only reader is the paper completed-game decision path; the only writer is
-- agents/paper_learning.py (activation behind PAPER_LEARNING_PROPOSAL_
-- ACTIVATION and a named approver; rollback audited).
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction together with its schema_migrations row.

CREATE TABLE IF NOT EXISTS paper_policy_parameter_versions (
    version_id          text PRIMARY KEY,
    policy_key          text        NOT NULL,
    version_no          integer     NOT NULL,
    params              jsonb       NOT NULL,
    params_sha256       text        NOT NULL,
    source              text        NOT NULL,
    proposal_id         text        REFERENCES paper_improvement_proposals,
    evaluation_id       text,
    approved_by         text,
    created_at          timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_policy_parameter_versions_paper_id_ck CHECK (
        version_id LIKE 'paper%'),
    CONSTRAINT paper_policy_parameter_versions_policy_ck CHECK (
        policy_key = 'PINNACLE_COMPLETED_GAME_PAPER'),
    -- THE WHITELIST AND THE BOUNDS: one parameter, 4.0 .. 6.0, 0.5 grid.
    CONSTRAINT paper_policy_parameter_versions_whitelist_ck CHECK (
        jsonb_typeof(params) = 'object'
        AND params ? 'min_gross_edge_pp'
        AND (params - 'min_gross_edge_pp') = '{}'::jsonb
        AND jsonb_typeof(params->'min_gross_edge_pp') = 'number'),
    CONSTRAINT paper_policy_parameter_versions_bounds_ck CHECK (
        (params->>'min_gross_edge_pp')::numeric BETWEEN 4.0 AND 6.0
        AND mod((params->>'min_gross_edge_pp')::numeric * 2, 1) = 0),
    CONSTRAINT paper_policy_parameter_versions_source_ck CHECK (
        source IN ('SHIPPED_DEFAULT', 'EVALUATED_PROPOSAL')),
    -- A VERSION AFTER THE SHIPPED DEFAULT NAMES ITS PROPOSAL, ITS
    -- EVALUATION AND ITS HUMAN APPROVER.
    CONSTRAINT paper_policy_parameter_versions_provenance_ck CHECK (
        source = 'SHIPPED_DEFAULT'
        OR (proposal_id IS NOT NULL AND evaluation_id IS NOT NULL
            AND approved_by IS NOT NULL)),
    CONSTRAINT paper_policy_parameter_versions_no_ck UNIQUE (
        policy_key, version_no)
);

DROP TRIGGER IF EXISTS paper_policy_parameter_versions_append_only_trg
    ON paper_policy_parameter_versions;
CREATE TRIGGER paper_policy_parameter_versions_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_policy_parameter_versions
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

CREATE TABLE IF NOT EXISTS paper_policy_parameter_activations (
    activation_id       text PRIMARY KEY,
    policy_key          text        NOT NULL,
    kind                text        NOT NULL,
    version_id          text        NOT NULL
                        REFERENCES paper_policy_parameter_versions,
    previous_version_id text        REFERENCES
                                    paper_policy_parameter_versions,
    proposal_id         text        REFERENCES paper_improvement_proposals,
    evaluation_id       text,
    actor               text        NOT NULL,
    reason              text,
    control             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    at                  timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_policy_parameter_activations_paper_id_ck CHECK (
        activation_id LIKE 'paper%'),
    CONSTRAINT paper_policy_parameter_activations_kind_ck CHECK (
        kind IN ('SHIPPED_DEFAULT', 'ACTIVATE', 'ROLLBACK')),
    CONSTRAINT paper_policy_parameter_activations_activate_ck CHECK (
        kind <> 'ACTIVATE' OR (proposal_id IS NOT NULL
                               AND evaluation_id IS NOT NULL
                               AND previous_version_id IS NOT NULL)),
    CONSTRAINT paper_policy_parameter_activations_rollback_ck CHECK (
        kind <> 'ROLLBACK' OR previous_version_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS paper_policy_parameter_activations_idx
    ON paper_policy_parameter_activations (policy_key, at DESC);

DROP TRIGGER IF EXISTS paper_policy_parameter_activations_append_only_trg
    ON paper_policy_parameter_activations;
CREATE TRIGGER paper_policy_parameter_activations_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_policy_parameter_activations
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

CREATE TABLE IF NOT EXISTS paper_policy_parameter_heads (
    policy_key          text PRIMARY KEY,
    active_version_id   text        NOT NULL
                        REFERENCES paper_policy_parameter_versions,
    activation_id       text        NOT NULL
                        REFERENCES paper_policy_parameter_activations,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_policy_parameter_heads_policy_ck CHECK (
        policy_key = 'PINNACLE_COMPLETED_GAME_PAPER')
);

-- ── V1, THE SHIPPED DEFAULT, ACTIVE ────────────────────────────────────
INSERT INTO paper_policy_parameter_versions (version_id, policy_key,
    version_no, params, params_sha256, source, created_at)
VALUES ('paperparam:PINNACLE_COMPLETED_GAME_PAPER:V1',
        'PINNACLE_COMPLETED_GAME_PAPER', 1,
        '{"min_gross_edge_pp": 5.0}'::jsonb,
        encode(sha256('{"min_gross_edge_pp": 5.0}'::bytea), 'hex'),
        'SHIPPED_DEFAULT', now())
ON CONFLICT DO NOTHING;
INSERT INTO paper_policy_parameter_activations (activation_id, policy_key,
    kind, version_id, actor, reason, at)
VALUES ('paperact:PINNACLE_COMPLETED_GAME_PAPER:V1',
        'PINNACLE_COMPLETED_GAME_PAPER', 'SHIPPED_DEFAULT',
        'paperparam:PINNACLE_COMPLETED_GAME_PAPER:V1', 'migration 186',
        'the shipped default (PINNACLE_COMPLETED_GAME_PAPER_V1)', now())
ON CONFLICT DO NOTHING;
INSERT INTO paper_policy_parameter_heads (policy_key, active_version_id,
    activation_id)
VALUES ('PINNACLE_COMPLETED_GAME_PAPER',
        'paperparam:PINNACLE_COMPLETED_GAME_PAPER:V1',
        'paperact:PINNACLE_COMPLETED_GAME_PAPER:V1')
ON CONFLICT DO NOTHING;
