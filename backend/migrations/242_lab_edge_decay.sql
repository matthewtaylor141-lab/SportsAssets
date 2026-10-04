-- ══════════════════════════════════════════════════════════════════════
-- 242 · LAB-A EDGE DECAY & LATENCY ECONOMICS: SHADOW RESEARCH SNAPSHOTS
-- ══════════════════════════════════════════════════════════════════════
--
-- WHY A TABLE AT ALL. The edge-decay endpoint (GET /api/command/lab/
-- edge-decay) recomputes its historical answer on demand from the paper
-- record through the lab's point-in-time accessor; that needs no storage.
-- Two things do: (1) the FAST-LANE component-latency measurement is taken
-- by a measurement harness (sportsassets/scripts/lab_fastlane_measure.py)
-- that times the real canonical components on production-shaped rows -- it
-- cannot run inside a read-only request without touching the process-wide
-- component cache the decision path uses, so its result is recorded here and
-- the endpoint reads it; (2) the FORWARD SHADOW plan keeps dated edge-decay
-- summaries so the forward window can be compared with the retrospective one
-- without recomputing history that later rows could change.
--
-- WHAT IT IS NOT. Nothing reads this table on a decision path; no gate,
-- threshold, allowlist, policy, sizing or order consults it (the lab's
-- import-guard test proves no decision module imports the lab). Every row
-- is authority SHADOW_RESEARCH_ONLY (CHECK), append-only (trigger), and a
-- retrospective row can never be labelled FORWARD_VALIDATED (CHECK).
CREATE TABLE IF NOT EXISTS lab_edge_decay_snapshots (
    snapshot_id     bigserial PRIMARY KEY,
    kind            text        NOT NULL,
    version         text        NOT NULL,
    authority       text        NOT NULL DEFAULT 'SHADOW_RESEARCH_ONLY',
    evidence_status text        NOT NULL,
    window_start    timestamptz,
    window_end      timestamptz,
    source          text        NOT NULL,
    code_sha        text,
    payload         jsonb       NOT NULL,
    recorded_by     text        NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT lab_edge_decay_kind_ck CHECK (kind IN (
        'EDGE_DECAY_SUMMARY', 'COMPONENT_LATENCY')),
    CONSTRAINT lab_edge_decay_authority_ck CHECK (
        authority = 'SHADOW_RESEARCH_ONLY'),
    -- the lab's status ladder; FORWARD_VALIDATED is never written from
    -- retrospective evidence, and this table records no other kind
    CONSTRAINT lab_edge_decay_status_ck CHECK (evidence_status IN (
        'BUILT', 'TESTED', 'SHADOW_RUNNING',
        'FORWARD_EVIDENCE_INSUFFICIENT', 'RETROSPECTIVE_ONLY')),
    CONSTRAINT lab_edge_decay_window_ck CHECK (
        window_start IS NULL OR window_end IS NULL
        OR window_start <= window_end),
    CONSTRAINT lab_edge_decay_payload_ck CHECK (
        jsonb_typeof(payload) = 'object'),
    CONSTRAINT lab_edge_decay_recorder_ck CHECK (length(recorded_by) > 0)
);
CREATE INDEX IF NOT EXISTS lab_edge_decay_snapshots_kind_idx
    ON lab_edge_decay_snapshots (kind, recorded_at DESC);

CREATE OR REPLACE FUNCTION lab_edge_decay_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'LAB_EDGE_DECAY_APPEND_ONLY: % on % is refused',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;

DROP TRIGGER IF EXISTS lab_edge_decay_snapshots_append_only_trg
    ON lab_edge_decay_snapshots;
CREATE TRIGGER lab_edge_decay_snapshots_append_only_trg
    BEFORE UPDATE OR DELETE ON lab_edge_decay_snapshots
    FOR EACH ROW EXECUTE FUNCTION lab_edge_decay_append_only();
