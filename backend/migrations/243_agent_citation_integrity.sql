-- ══════════════════════════════════════════════════════════════════════
-- 243 · AGENT CITATION / CLAIM INTEGRITY (LAB-B): THE VERDICT LEDGER
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (owner, 2026-10-04). The agent chat numbers its facts [F#],
-- requires every figure in a model reply to be held by SOME fact, rejects
-- claimed actions and falls back to a records-only answer -- and still
-- published directionally correct sentences citing the WRONG fact id: "149.3
-- seconds stale" citing the older 75.2-second review, the account's cash
-- from F143 cited as F139, a review called CURRENT whose cited fact is
-- SUPERSEDED, a valid record of the wrong position. Record existence and
-- figure grounding are necessary, not sufficient.
--
-- WHAT THIS ADDS. The pre-publication verifier (sportsassets/lab/
-- citation_integrity.py) runs inside persona_chat's answer pipeline, the one
-- place an answer is finalised, and records EVERY verdict here, append-only:
--
--   agent_citation_checks    one row per verified text: the model's reply
--                            (MODEL_REPLY), the records-only answer
--                            (RECORDS_ONLY_ANSWER) or an interrupted
--                            partial (INTERRUPTED_PARTIAL); its sentence /
--                            material / cited counts, the verdict counts,
--                            the sentences repaired, the action taken and,
--                            when it was not published as verified, the
--                            named integrity reason. primary_text marks the
--                            text the agent's scorecard measures (the model
--                            reply when a model composed one).
--   agent_citation_verdicts  one row per material sentence: its index and
--                            sha256 (never the prose itself), the cited fact
--                            ids and the records they named (source,
--                            record id, field), the claim categories, the
--                            verdict (PASS / WRONG_FACT /
--                            INSUFFICIENT_SUPPORT / STALE_STATE_CITATION /
--                            ENTITY_MISMATCH / NO_CITATION), the failing
--                            items, the action (PUBLISHED_VERIFIED /
--                            REPAIRED_RECITED / FELL_BACK_TO_RECORDS_ONLY /
--                            STATED_UNSUPPORTED) and the fact ids a
--                            deterministic repair cited.
--
-- AUTHORITY. SHADOW_RESEARCH_ONLY, held by a CHECK: these rows are an agent-
-- quality measurement. Nothing on a decision path reads them; they grant no
-- order, capital, limit, threshold, policy or approval authority. The
-- verifier's only effect is on the chat prose it checks before publication.
--
-- INVARIANTS (CHECK constraints, not conventions)
--   * a verified sentence is a PASS; a PASS is published as verified unless
--     the whole model reply was discarded;
--   * NO_CITATION <-> no cited fact id; every other verdict cites one;
--   * a repaired sentence names the fact(s) the repair cited;
--   * a discarded reply is a MODEL_REPLY, unpublished, with its reason; a
--     verified or repaired text has no integrity reason, a discarded or
--     stated-unsupported one always has one;
--   * counts nest: cited <= material <= sentences.
-- Both tables are append-only (a trigger refuses UPDATE and DELETE).

CREATE TABLE IF NOT EXISTS agent_citation_checks (
    check_id                  bigserial   PRIMARY KEY,
    agent_id                  text        NOT NULL
        CHECK (agent_id ~ '^[A-Z][A-Z_]{1,31}$'),
    conversation_id           text        NOT NULL,
    message_id                text,
    turn_id                   text,
    stage                     text        NOT NULL
        CHECK (stage IN ('MODEL_REPLY', 'RECORDS_ONLY_ANSWER',
                         'INTERRUPTED_PARTIAL')),
    composer                  text        NOT NULL
        CHECK (composer IN ('MODEL', 'RECORDS_ONLY')),
    primary_text              boolean     NOT NULL,
    published                 boolean     NOT NULL,
    checked_at                timestamptz NOT NULL,
    sentences                 integer     NOT NULL CHECK (sentences >= 0),
    material_sentences        integer     NOT NULL
        CHECK (material_sentences >= 0 AND material_sentences <= sentences),
    cited_material_sentences  integer     NOT NULL
        CHECK (cited_material_sentences >= 0
               AND cited_material_sentences <= material_sentences),
    verdict_counts            jsonb       NOT NULL
        CHECK (jsonb_typeof(verdict_counts) = 'object'),
    repaired_sentences        integer     NOT NULL DEFAULT 0
        CHECK (repaired_sentences >= 0
               AND repaired_sentences <= material_sentences),
    action                    text        NOT NULL
        CHECK (action IN ('PUBLISHED_VERIFIED', 'REPAIRED_RECITED',
                          'FELL_BACK_TO_RECORDS_ONLY',
                          'STATED_UNSUPPORTED')),
    integrity_reason          text
        CHECK (integrity_reason IS NULL
               OR integrity_reason ~ '^CITATION_INTEGRITY:[A-Z_]+$'),
    verifier_version          text        NOT NULL,
    authority                 text        NOT NULL
        DEFAULT 'SHADOW_RESEARCH_ONLY'
        CHECK (authority = 'SHADOW_RESEARCH_ONLY'),
    recorded_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_citation_checks_stage_composer_ck CHECK (
        (stage = 'RECORDS_ONLY_ANSWER') = (composer = 'RECORDS_ONLY')),
    CONSTRAINT agent_citation_checks_fallback_ck CHECK (
        action <> 'FELL_BACK_TO_RECORDS_ONLY'
        OR (stage = 'MODEL_REPLY' AND published = false)),
    CONSTRAINT agent_citation_checks_reason_ck CHECK (
        (action IN ('PUBLISHED_VERIFIED', 'REPAIRED_RECITED'))
        = (integrity_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS agent_citation_checks_agent_idx
    ON agent_citation_checks (agent_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS agent_citation_checks_message_idx
    ON agent_citation_checks (message_id);

CREATE TABLE IF NOT EXISTS agent_citation_verdicts (
    verdict_id         bigserial   PRIMARY KEY,
    check_id           bigint      NOT NULL
        REFERENCES agent_citation_checks (check_id),
    agent_id           text        NOT NULL
        CHECK (agent_id ~ '^[A-Z][A-Z_]{1,31}$'),
    conversation_id    text        NOT NULL,
    message_id         text,
    sentence_index     integer     NOT NULL CHECK (sentence_index >= 0),
    sentence_sha256    text        NOT NULL
        CHECK (sentence_sha256 ~ '^[0-9a-f]{64}$'),
    cited_fact_ids     text[]      NOT NULL,
    cited_records      jsonb       NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(cited_records) = 'array'),
    categories         text[]      NOT NULL DEFAULT '{}',
    verdict            text        NOT NULL
        CHECK (verdict IN ('PASS', 'WRONG_FACT', 'INSUFFICIENT_SUPPORT',
                           'STALE_STATE_CITATION', 'ENTITY_MISMATCH',
                           'NO_CITATION')),
    failing            jsonb       NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(failing) = 'array'),
    action             text        NOT NULL
        CHECK (action IN ('PUBLISHED_VERIFIED', 'REPAIRED_RECITED',
                          'FELL_BACK_TO_RECORDS_ONLY',
                          'STATED_UNSUPPORTED')),
    repaired_fact_ids  text[]      NOT NULL DEFAULT '{}',
    verifier_version   text        NOT NULL,
    authority          text        NOT NULL
        DEFAULT 'SHADOW_RESEARCH_ONLY'
        CHECK (authority = 'SHADOW_RESEARCH_ONLY'),
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_citation_verdicts_sentence_uq
        UNIQUE (check_id, sentence_index),
    CONSTRAINT agent_citation_verdicts_verified_is_pass_ck CHECK (
        action <> 'PUBLISHED_VERIFIED' OR verdict = 'PASS'),
    CONSTRAINT agent_citation_verdicts_pass_published_ck CHECK (
        verdict <> 'PASS'
        OR action IN ('PUBLISHED_VERIFIED', 'FELL_BACK_TO_RECORDS_ONLY')),
    CONSTRAINT agent_citation_verdicts_no_citation_ck CHECK (
        (verdict = 'NO_CITATION') = (cardinality(cited_fact_ids) = 0)),
    CONSTRAINT agent_citation_verdicts_repair_names_ck CHECK (
        action <> 'REPAIRED_RECITED' OR cardinality(repaired_fact_ids) > 0),
    CONSTRAINT agent_citation_verdicts_failing_ck CHECK (
        (verdict = 'PASS') = (jsonb_array_length(failing) = 0))
);
CREATE INDEX IF NOT EXISTS agent_citation_verdicts_check_idx
    ON agent_citation_verdicts (check_id);
CREATE INDEX IF NOT EXISTS agent_citation_verdicts_agent_idx
    ON agent_citation_verdicts (agent_id, recorded_at DESC);

CREATE OR REPLACE FUNCTION agent_citation_record_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: a citation verdict is a record',
        TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_citation_checks_append_only_trg
    ON agent_citation_checks;
CREATE TRIGGER agent_citation_checks_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_citation_checks
    FOR EACH ROW EXECUTE FUNCTION agent_citation_record_is_append_only();

DROP TRIGGER IF EXISTS agent_citation_verdicts_append_only_trg
    ON agent_citation_verdicts;
CREATE TRIGGER agent_citation_verdicts_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_citation_verdicts
    FOR EACH ROW EXECUTE FUNCTION agent_citation_record_is_append_only();
