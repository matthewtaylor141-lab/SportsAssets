-- ══════════════════════════════════════════════════════════════════════
-- 188 · PAPER ONLY: THE OWNER'S 0.5 pp ENTRY THRESHOLD FOR THE
--       COMPLETED-GAME PAPER POLICY (PINNACLE_COMPLETED_GAME_PAPER_V2)
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER DECISION, 2026-10-01, PAPER ONLY (real-money execution stays
-- disabled). It replaces the earlier 5.0 pp floor (and a proposed 2.0 pp
-- rule that never shipped). The entry condition of the active paper
-- experiment becomes:
--
--     Pinnacle reference probability - simulated acquisition price >= 0.005
--     AND conditional expected profit after fees strictly > 0
--
-- half a probability point on a $0/$1 contract: an ABSOLUTE difference, not
-- a return target, with no upper edge limit. Everything else is unchanged:
-- exact contract matching, fresh probability evidence, book-read deadlines,
-- displayed depth, conservative simulated fills, fees, exposure limits and
-- exceptional-settlement disclosure.
--
-- WHAT THIS FILE DOES
--   * bounds CHECK 5.0..6.0 -> 0.5..6.0 (0.5 grid). 0.5 is now the floor
--     automatic learning can never go below (paper_learning.PARAM_BOUNDS and
--     paper_benchmark.CG_PARAMETER_BOUNDS carry the same numbers; a test
--     pins all three).
--   * a new version source / activation kind OWNER_DECISION, which must
--     name its approver.
--   * V2 = {"min_gross_edge_pp": 0.5}, activated from V1 by owner decision.
--
-- PRESERVED: the account, its session, its ledger (one INITIAL_FUNDING
-- entry), positions and every historical decision. V1 stays in the
-- append-only version table and its decisions keep their recorded version
-- and 5.0 pp threshold; the audited rollback route can still restore V1.
--
-- No BEGIN/COMMIT of its own: the runner applies it in one transaction.

ALTER TABLE paper_policy_parameter_versions
    DROP CONSTRAINT IF EXISTS paper_policy_parameter_versions_bounds_ck;
ALTER TABLE paper_policy_parameter_versions
    ADD CONSTRAINT paper_policy_parameter_versions_bounds_ck CHECK (
        (params->>'min_gross_edge_pp')::numeric BETWEEN 0.5 AND 6.0
        AND mod((params->>'min_gross_edge_pp')::numeric * 2, 1) = 0);

ALTER TABLE paper_policy_parameter_versions
    DROP CONSTRAINT IF EXISTS paper_policy_parameter_versions_source_ck;
ALTER TABLE paper_policy_parameter_versions
    ADD CONSTRAINT paper_policy_parameter_versions_source_ck CHECK (
        source IN ('SHIPPED_DEFAULT', 'EVALUATED_PROPOSAL',
                   'OWNER_DECISION'));

ALTER TABLE paper_policy_parameter_versions
    DROP CONSTRAINT IF EXISTS paper_policy_parameter_versions_provenance_ck;
ALTER TABLE paper_policy_parameter_versions
    ADD CONSTRAINT paper_policy_parameter_versions_provenance_ck CHECK (
        source = 'SHIPPED_DEFAULT'
        OR (source = 'OWNER_DECISION' AND approved_by IS NOT NULL)
        OR (source = 'EVALUATED_PROPOSAL' AND proposal_id IS NOT NULL
            AND evaluation_id IS NOT NULL AND approved_by IS NOT NULL));

ALTER TABLE paper_policy_parameter_activations
    DROP CONSTRAINT IF EXISTS paper_policy_parameter_activations_kind_ck;
ALTER TABLE paper_policy_parameter_activations
    ADD CONSTRAINT paper_policy_parameter_activations_kind_ck CHECK (
        kind IN ('SHIPPED_DEFAULT', 'ACTIVATE', 'ROLLBACK',
                 'OWNER_DECISION'));
ALTER TABLE paper_policy_parameter_activations
    DROP CONSTRAINT IF EXISTS paper_policy_parameter_activations_owner_ck;
ALTER TABLE paper_policy_parameter_activations
    ADD CONSTRAINT paper_policy_parameter_activations_owner_ck CHECK (
        kind <> 'OWNER_DECISION' OR (previous_version_id IS NOT NULL
                                     AND reason IS NOT NULL));

INSERT INTO paper_policy_parameter_versions (version_id, policy_key,
    version_no, params, params_sha256, source, approved_by, created_at)
VALUES ('paperparam:PINNACLE_COMPLETED_GAME_PAPER:V2',
        'PINNACLE_COMPLETED_GAME_PAPER', 2,
        '{"min_gross_edge_pp": 0.5}'::jsonb,
        encode(sha256('{"min_gross_edge_pp": 0.5}'::bytea), 'hex'),
        'OWNER_DECISION',
        'OWNER (account holder): written paper-only authorization 2026-10-01',
        now())
ON CONFLICT DO NOTHING;

INSERT INTO paper_policy_parameter_activations (activation_id, policy_key,
    kind, version_id, previous_version_id, actor, reason, at)
SELECT 'paperact:PINNACLE_COMPLETED_GAME_PAPER:V2:OWNER_DECISION',
       'PINNACLE_COMPLETED_GAME_PAPER', 'OWNER_DECISION',
       'paperparam:PINNACLE_COMPLETED_GAME_PAPER:V2', h.active_version_id,
       'migration 188',
       'owner decision 2026-10-01 (paper only): p_pinnacle - simulated '
       'acquisition price >= 0.005 AND conditional expected profit after '
       'fees > 0 (PINNACLE_COMPLETED_GAME_PAPER_V2); replaces the 5.0 pp '
       'floor', now()
  FROM paper_policy_parameter_heads h
 WHERE h.policy_key = 'PINNACLE_COMPLETED_GAME_PAPER'
ON CONFLICT DO NOTHING;

UPDATE paper_policy_parameter_heads
   SET active_version_id = 'paperparam:PINNACLE_COMPLETED_GAME_PAPER:V2',
       activation_id = 'paperact:PINNACLE_COMPLETED_GAME_PAPER:V2:OWNER_DECISION',
       updated_at = now()
 WHERE policy_key = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND EXISTS (SELECT 1 FROM paper_policy_parameter_activations
                WHERE activation_id =
                      'paperact:PINNACLE_COMPLETED_GAME_PAPER:V2:OWNER_DECISION');
