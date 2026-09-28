-- ══════════════════════════════════════════════════════════════════════
-- 134 · THREE CORRECTIONS TO THE FUNDED DECISION LEDGER
-- ══════════════════════════════════════════════════════════════════════
--
-- An independent review named three specific defects in 132's ledger. Each one
-- makes the ledger's own numbers wrong in a different way, so each gets its own
-- structure rather than a comment.
--
-- ── 1 · THE SAME ECONOMIC RESULT WAS COUNTED ONCE PER DECISION ───────
--
-- `join_realised` attached the WHOLE GROUP's realised net to a decision, and
-- `score` summed `realised_net_usd` over every decision. A position evaluated on
-- five cycles therefore contributed its result FIVE TIMES to the portfolio total.
-- The more often the system thought about a position, the more money it appeared
-- to make -- which is the worst possible direction for that error.
--
-- DECISION-LEVEL EVALUATION AND PORTFOLIO P&L ARE DIFFERENT QUESTIONS. "Did this
-- decision's claimed floor hold?" is asked per decision, and needs the group's
-- result attached to each. "What did the account make?" is asked per GROUP, once.
-- `group_id` was already on the row; the P&L side now deduplicates on it, and
-- `bettor_funded_group_results` records the group's result exactly once so the
-- deduplication is a fact in the schema rather than a habit in one query.
--
-- ── 2 · PROVISIONAL FEES WERE FINAL THE MOMENT THEY WERE WRITTEN ─────
--
-- 132 accepted a realised net containing PROVISIONAL fee events and then refused
-- to let it be restated. So the first read of an unsettled fee became permanent,
-- and the correction that arrives later had nowhere to go.
--
-- DECISIONS STAY IMMUTABLE; OUTCOMES BECOME VERSIONED. A decision is a record of
-- what was expected BEFORE the result, and rewriting it would make its claim
-- unfalsifiable -- that rule stands. But an OUTCOME is a measurement, and a
-- measurement that cannot be corrected is not a measurement. Outcomes now live in
-- an append-only table with a version per correction, each carrying whether it was
-- final and what superseded the last. Scoring reads the latest, and can say
-- whether it is final.
--
-- ── 3 · A FLOOR IS CONDITIONAL ON THE ACTION THAT ESTABLISHED IT ─────
--
-- The worst case was computed for a SPECIFIC action on a SPECIFIC holding. If the
-- system later sells voluntarily, acquires only part of a hedge, or the filled
-- quantity differs from the one valued, the realised net is the result of a
-- DIFFERENT position -- and comparing it to the old floor reports a violated model
-- when nothing about the settlement model was wrong.
--
-- So every bound records the ACTION, the FILLED QUANTITY and the HOLDING POLICY it
-- was conditional on. A later divergence makes the check INVALIDATED rather than
-- VIOLATED, which is a different finding and must not be filed as a modelling
-- error.
--
-- ADDITIVE: new columns default NULL, one new table each. KEEP OUT OF PRODUCTION.

BEGIN;

-- ── 3 · THE SCOPE EVERY BOUND IS CONDITIONAL ON ─────────────────────
ALTER TABLE bettor_funded_decisions
    ADD COLUMN IF NOT EXISTS bound_action text,
    ADD COLUMN IF NOT EXISTS bound_filled_qty numeric,
    ADD COLUMN IF NOT EXISTS bound_holding_policy text;

COMMENT ON COLUMN bettor_funded_decisions.bound_holding_policy IS
    'HOLD_TO_SETTLEMENT or MAY_EXIT_EARLY. A floor computed on the assumption '
    'the position is held to settlement says nothing about a position that was '
    'sold on cycle three.';

-- ── 2 · OUTCOMES, APPEND-ONLY AND VERSIONED ─────────────────────────
CREATE TABLE IF NOT EXISTS bettor_funded_decision_outcomes (
    outcome_id      text PRIMARY KEY,
    decision_id     text        NOT NULL
                        REFERENCES bettor_funded_decisions(decision_id),
    version         int         NOT NULL,
    realised_net_usd numeric    NOT NULL,
    realised_basis  text        NOT NULL,
    is_final        boolean     NOT NULL,
    provisional_events int      NOT NULL DEFAULT 0,
    -- WHAT THIS CORRECTS, and why. A version with no stated reason is a silent
    -- restatement wearing a version number.
    supersedes_version int,
    correction_reason text,
    by_kind         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    read_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT bettor_funded_outcome_version_ck CHECK (version >= 1),
    --: A CORRECTION MUST SAY WHAT IT CORRECTS AND WHY.
    CONSTRAINT bettor_funded_outcome_correction_ck CHECK (
        version = 1
        OR (supersedes_version IS NOT NULL AND correction_reason IS NOT NULL)),
    --: AND A FINAL OUTCOME CARRIES NO PROVISIONAL EVENTS. That is what final
    --: means; a "final" figure containing an estimate is the defect this closes.
    CONSTRAINT bettor_funded_outcome_final_is_final_ck CHECK (
        NOT is_final OR provisional_events = 0),
    CONSTRAINT bettor_funded_outcome_one_version_ck
        UNIQUE (decision_id, version)
);

CREATE INDEX IF NOT EXISTS bettor_funded_outcomes_decision_idx
    ON bettor_funded_decision_outcomes (decision_id, version DESC);

-- ── NO UPDATE. DELETE IS ALLOWED, AND THE DIFFERENCE MATTERS ────────
--
-- A correction must be a NEW version, never an edit of an old one: otherwise the
-- audit trail is whatever the last writer thought, and "version 2 supersedes
-- version 1" is a claim about a row that may since have changed.
--
-- DELETE IS NOT BLOCKED, and the first version of this trigger got that wrong. It
-- refused DELETE as well, which is not restatement -- it is removal -- and it made
-- the table impossible to prune and impossible to clean up after a test. Worse,
-- the outcomes reference the decision, so a legitimately deleted decision could
-- never have its rows removed: the trigger would have made the parent
-- undeletable forever. Removing a row destroys evidence, which a reader can see;
-- silently altering one does not.
CREATE OR REPLACE FUNCTION bettor_funded_outcome_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'outcome % version % is not edited; record a NEW version '
                    'with supersedes_version and a correction_reason',
        OLD.decision_id, OLD.version;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_outcome_is_append_only_trg
    ON bettor_funded_decision_outcomes;
CREATE TRIGGER bettor_funded_outcome_is_append_only_trg
    BEFORE UPDATE ON bettor_funded_decision_outcomes
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_outcome_is_append_only();

-- ── 1 · THE GROUP'S RESULT, RECORDED ONCE ───────────────────────────
CREATE TABLE IF NOT EXISTS bettor_funded_group_results (
    group_id        text PRIMARY KEY
                        REFERENCES bettor_funded_portfolio_groups(group_id),
    realised_net_usd numeric    NOT NULL,
    realised_basis  text        NOT NULL,
    is_final        boolean     NOT NULL,
    provisional_events int      NOT NULL DEFAULT 0,
    by_kind         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version         int         NOT NULL DEFAULT 1,
    read_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT bettor_funded_group_result_final_ck CHECK (
        NOT is_final OR provisional_events = 0)
);

COMMENT ON TABLE bettor_funded_group_results IS
    'One row per group. Portfolio P&L sums THIS table, never the decision '
    'ledger: a position evaluated on five cycles has five decisions and one '
    'economic result, and summing the decisions multiplied it by five.';

COMMIT;
