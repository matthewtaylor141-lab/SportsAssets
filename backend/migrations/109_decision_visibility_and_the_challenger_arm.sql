-- 109 — THE WHOLE DECISION ON THE ROW, AND A SECOND ARM TO COMPARE.
--
-- Owner directive, "MAKE THE FULL EXIT POLICY OPERATIONAL" §5:
--
--     "For each decision, persist and display: position and inventory;
--      actual decision time; input availability and freshness; policy
--      version; probability/payout identity; alternatives considered;
--      selected action and size; governing rule; order and fill state;
--      resulting inventory; and reconciled accounting. HOLD needs a
--      reason too. A missing input must be distinguishable from a
--      deliberate decision to hold."
--
-- WHAT `rn1x_decisions` COULD ALREADY DO, so nothing here is rebuilt:
-- decision_ts, evidence_ts, evidence_id, selected_action,
-- selection_reason, alternatives (the full candidate set INCLUDING
-- refusals), ev_at_decision_usd, ev_basis, conditional_on_our_fill and
-- input_labels all exist from migration 100. The table was already
-- designed so "what else was on the table" survives.
--
-- WHAT IT COULD NOT DO, AND WHY EACH GAP MATTERS.
--
--   * SELECTED SIZE. `selected_action` with no quantity cannot express
--     "sell 70 of 100". A partial exit and a full one read identically,
--     and the partial is the interesting decision.
--
--   * POLICY VERSION. Two policies now manage positions. Without the
--     version on the row, a champion decision and a challenger decision
--     are indistinguishable and the comparison the challenger exists
--     for cannot be made.
--
--   * THE HOLD DISTINCTION. This is the important one. A HOLD reached
--     BY DECISION and a HOLD reached because no input arrived leave the
--     position in the same state and mean opposite things:
--
--         HOLD_BY_DECISION         priced against alternatives and won
--         HOLD_BY_FALLBACK_RULE    EV_HOLD unknown; the declared
--                                  exposure trigger evaluated real
--                                  inputs and did not fire
--         HOLD_FOR_MISSING_INPUT   nothing was evaluated at all
--
--     Stored as one nullable `operating_state` plus an explicit
--     `input_available` boolean, so a reader cannot collapse them and a
--     count of "held" cannot quietly include the blind cycles.
--
--   * PROBABILITY / PAYOUT IDENTITY. The hold value comes from an
--     external probability, and after 42a68c4 nobody should accept one
--     without the event it describes. The identity travels onto the
--     decision row so the decision can be re-checked without a join
--     that might not find the source row later.
--
--   * FRESHNESS. `decision_ts` says when we decided. It does not say
--     how old the evidence was. A decision on a 29-minute-old line and
--     one on a 3-second-old line are different decisions.
--
--   * RESULTING INVENTORY AND RECONCILED ACCOUNTING. Stored per
--     decision so the book can be read AT any decision, not only at the
--     end of a run.

BEGIN;

ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS selected_qty numeric;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS policy_version text;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS governing_rule text;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS operating_state text;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS is_a_deliberate_hold boolean;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS input_available boolean;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS input_freshness jsonb;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS payout_identity jsonb;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS hold_value_usd double precision;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS hold_value_basis text;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS venue_translation jsonb;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS order_state jsonb;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS resulting_inventory jsonb;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS accounting_reconciles boolean;

COMMENT ON COLUMN rn1x_decisions.selected_qty IS
    'HOW MUCH of the position the selected action applies to. NULL only '
    'when the action touches no quantity. A partial exit and a full one '
    'are different decisions and this is what separates them.';

COMMENT ON COLUMN rn1x_decisions.operating_state IS
    'HOLD_BY_DECISION | HOLD_BY_FALLBACK_RULE | HOLD_FOR_MISSING_INPUT | '
    'ORDER_WORKING | WAIT_CANCEL_ACK | CLOSING | NO_RESIDUAL | '
    'HOLD_NO_FEASIBLE_PAIR. The first three all leave the position '
    'unchanged and mean different things.';

COMMENT ON COLUMN rn1x_decisions.input_available IS
    'Whether the decision had the inputs it needed. FALSE with a HOLD '
    'operating state means the policy was BLIND, not patient.';

COMMENT ON COLUMN rn1x_decisions.payout_identity IS
    'Which event the probability described, which event the position '
    'pays on, the matched venue side and the intent -- so the decision '
    'can be re-checked against the defect migration 108 exists for, '
    'without depending on the source row still being there.';

COMMENT ON COLUMN rn1x_decisions.hold_value_basis IS
    'Where EV_HOLD came from, or the NAMED refusal. Never NULL on a '
    'challenger row: an absent hold value is a fact to record.';

-- A HOLD MUST CARRY ITS REASON, AND SO MUST EVERY OTHER OUTCOME.
-- `selection_reason` is already NOT NULL from migration 100. This adds
-- the check the directive actually asks for: a row that claims a
-- deliberate hold must also claim its input was available, because a
-- deliberate decision taken on no input is not deliberate.
ALTER TABLE rn1x_decisions
    DROP CONSTRAINT IF EXISTS rn1x_deliberate_hold_had_an_input;
ALTER TABLE rn1x_decisions
    ADD CONSTRAINT rn1x_deliberate_hold_had_an_input
    CHECK (is_a_deliberate_hold IS NOT TRUE
           OR input_available IS TRUE
           OR governing_rule LIKE '%FALLBACK %')
    NOT VALID;

-- ── the challenger's own experiment row ─────────────────────────────
--
-- A SEPARATE experiment_id, not a flag on the champion's. The two are
-- never summed, and `rn1x_positions.position_id` already derives from
-- (experiment_id, policy, trade_id), so a separate id is what keeps one
-- source trade from colliding across the two arms.
INSERT INTO rn1x_experiments
    (experiment_id, code_version, seed_rule, policy_register,
     execution_basis, notes)
VALUES (
    'RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1',
    'SHADOW_CHALLENGER_HOLD_RANKED_V1',
    '{"seed": "the same RN1 fill the champion is seeded from, so the '
    'two arms start from IDENTICAL assigned inventory and any '
    'difference is a difference in management"}'::jsonb,
    '{"policy": "SHADOW_CHALLENGER_HOLD_RANKED_V1", '
    '"class": "DECLARED_RULE_OVER_A_LABELLED_EXTERNAL_FORECAST", '
    '"benchmark": "MANAGEMENT_PAIR_091_STOP_16_V1, frozen 2026-09-23, '
    'untouched by this arm", '
    '"hold_value_from": "bettor_hold_value.ev_hold over '
    'external_valuations ELIGIBLE rows", '
    '"fallback_when_hold_unpriced": "EXPOSURE_TRIGGER_RULE_V1", '
    '"is_not": "an EV optimisation. No calibration interval is '
    'established for the probability source on these markets"}'::jsonb,
    'PRINT_THROUGH_WITH_QUEUE_SHARE_V1',
    'Ranks every available action INCLUDING hold. Shadow only; funded '
    'trading disabled and the accounting-uncertain account paused.')
ON CONFLICT (experiment_id) DO NOTHING;

COMMIT;
