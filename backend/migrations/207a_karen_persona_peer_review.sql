-- ══════════════════════════════════════════════════════════════════════
-- 207a · KAREN, PART TWO: HER PERSONA, THE CHALLENGE CATEGORY, THE CHIEF
--        ALLOCATOR AS A TARGET, AND THE INDEPENDENT EVALUATION'S EVIDENCE
-- ══════════════════════════════════════════════════════════════════════
--
-- Numbered 207a so it stays inside Karen's reserved migration number and
-- sorts directly after 207 (it depends on 207's tables) and before 208+.
--
-- 1. PERSONA. agent_persona_versions and agent_chat_conversations (migration
--    180) admit KAREN, so her persona is versioned and her conversations are
--    stored like the other three agents'.
-- 2. CATEGORY. Each challenge carries a category (EVIDENCE_GAP,
--    STALE_INPUT, OPEN_DISCREPANCY, EXECUTION_REFUSAL, ALLOCATION_RISK,
--    GOVERNANCE_GAP, LOOP_DEFECT, OTHER), fixed at creation. Rows made before
--    this migration are categorised from their detector.
-- 3. CHIEF_ALLOCATOR is a challenge target (the shadow allocator's
--    decisions and large allocations). It is a role, not an agent identity:
--    it holds no tool and no authority; its rule-based responder answers for
--    it and Audrey evaluates its disputes.
-- 4. THE INDEPENDENT EVALUATION'S EVIDENCE. resolution_evidence_refs is
--    recorded with the outcome (grounded), once.
-- 5. DISPUTES ARE RESOLVED BY SOMEONE ELSE. A challenge the target DISPUTED
--    is resolved (UPHELD or REJECTED) by neither the target nor Karen. 207's
--    guards are unchanged; this only adds to them.
--
-- IDEMPOTENT. No BEGIN/COMMIT of its own.

-- ── 1 · PERSONA TABLES ADMIT KAREN ───────────────────────────────────
ALTER TABLE agent_persona_versions
    DROP CONSTRAINT IF EXISTS agent_persona_versions_agent_id_check;
ALTER TABLE agent_persona_versions
    ADD CONSTRAINT agent_persona_versions_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN'));
ALTER TABLE agent_chat_conversations
    DROP CONSTRAINT IF EXISTS agent_chat_conversations_agent_id_check;
ALTER TABLE agent_chat_conversations
    ADD CONSTRAINT agent_chat_conversations_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN'));

-- ── 2 · CATEGORY ─────────────────────────────────────────────────────
ALTER TABLE karen_challenges ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE karen_challenges
    ADD COLUMN IF NOT EXISTS resolution_evidence_refs jsonb;

UPDATE karen_challenges SET category = CASE detector
        WHEN 'DECISION_WITHOUT_EVIDENCE' THEN 'EVIDENCE_GAP'
        WHEN 'ENTRY_WITHOUT_PROBABILITY' THEN 'EVIDENCE_GAP'
        WHEN 'HOLD_ON_STALE_PROBABILITY' THEN 'STALE_INPUT'
        WHEN 'AUDIT_DISCREPANCY_LEFT_OPEN' THEN 'OPEN_DISCREPANCY'
        WHEN 'RECONCILIATION_DISCREPANCY_OPEN' THEN 'OPEN_DISCREPANCY'
        WHEN 'ADMISSION_REFUSED_DECISION' THEN 'EXECUTION_REFUSAL'
        WHEN 'FINDING_RESTS_ON_UPHELD_DEFECT' THEN 'LOOP_DEFECT'
        WHEN 'LOOP_PEER_CHALLENGE' THEN 'LOOP_DEFECT'
        ELSE 'OTHER' END
 WHERE category IS NULL;

ALTER TABLE karen_challenges
    DROP CONSTRAINT IF EXISTS karen_challenges_category_ck;
ALTER TABLE karen_challenges
    ADD CONSTRAINT karen_challenges_category_ck CHECK (
        category IN ('EVIDENCE_GAP', 'STALE_INPUT', 'OPEN_DISCREPANCY',
                     'EXECUTION_REFUSAL', 'ALLOCATION_RISK',
                     'GOVERNANCE_GAP', 'LOOP_DEFECT', 'OTHER'));
ALTER TABLE karen_challenges ALTER COLUMN category SET DEFAULT 'OTHER';
ALTER TABLE karen_challenges ALTER COLUMN category SET NOT NULL;

-- ── 3 · THE CHIEF ALLOCATOR AS A TARGET ──────────────────────────────
ALTER TABLE karen_challenges DROP CONSTRAINT IF EXISTS karen_challenges_target_ck;
ALTER TABLE karen_challenges
    ADD CONSTRAINT karen_challenges_target_ck CHECK (
        target_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'CHIEF_ALLOCATOR'));

-- ── 4 · THE EVALUATION'S EVIDENCE ────────────────────────────────────
ALTER TABLE karen_challenges
    DROP CONSTRAINT IF EXISTS karen_challenges_resolution_refs_ck;
ALTER TABLE karen_challenges
    ADD CONSTRAINT karen_challenges_resolution_refs_ck CHECK (
        resolution_evidence_refs IS NULL
        OR (karen_refs_grounded(resolution_evidence_refs)
            AND state IN ('UPHELD', 'REJECTED')));

-- ── 5 · A DISPUTE IS RESOLVED BY A THIRD PARTY ───────────────────────
ALTER TABLE karen_challenges
    DROP CONSTRAINT IF EXISTS karen_challenges_dispute_independent_ck;
ALTER TABLE karen_challenges
    ADD CONSTRAINT karen_challenges_dispute_independent_ck CHECK (
        NOT (response_stance = 'DISPUTE'
             AND state IN ('UPHELD', 'REJECTED')
             AND (upper(btrim(resolved_by)) = target_agent
                  OR karen_is_actor(resolved_by))));

-- The category and the evaluation's evidence are fixed: the category at
-- creation, the evidence with the outcome. (207's own guard is unchanged.)
CREATE OR REPLACE FUNCTION karen_challenges_207a_guard() RETURNS trigger
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.resolution_evidence_refs IS NOT NULL THEN
            RAISE EXCEPTION 'karen_challenges: a challenge starts with no '
                            'evaluation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.category IS DISTINCT FROM OLD.category THEN
        RAISE EXCEPTION 'karen_challenges: the category of % is fixed at '
                        'creation', OLD.challenge_id;
    END IF;
    IF NEW.resolution_evidence_refs IS DISTINCT FROM
           OLD.resolution_evidence_refs
       AND NOT (OLD.resolved_by IS NULL AND NEW.resolved_by IS NOT NULL
                AND OLD.resolution_evidence_refs IS NULL) THEN
        RAISE EXCEPTION 'karen_challenges: the evaluation evidence of % is '
                        'recorded once, with the outcome', OLD.challenge_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS karen_challenges_207a_guard_trg ON karen_challenges;
CREATE TRIGGER karen_challenges_207a_guard_trg
    BEFORE INSERT OR UPDATE ON karen_challenges
    FOR EACH ROW EXECUTE FUNCTION karen_challenges_207a_guard();
