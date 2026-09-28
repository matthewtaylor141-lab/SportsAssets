-- ══════════════════════════════════════════════════════════════════════
-- 132 · THE FUNDED DECISION LEDGER: what was decided, before the outcome
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT IS MISSING WITHOUT IT. The RN1X lane records a prospective valuation and
-- later joins the venue's own settlement to it, so its record is scorable.
-- The FUNDED lane does not: it decides, acts, and the decision is gone. The
-- funded book holds the realised cash -- `bettor_funded_economics` carries
-- signed ENTRY_COST, EXIT_PROCEEDS, FEE and SETTLEMENT rows -- but nothing ever
-- states what was expected BEFORE that cash existed, so nothing can be checked
-- against it. "Learning from its own results" needs the first half written down
-- first.
--
-- ADDITIVE ONLY. One table, its own indexes, no change to any existing object
-- and no data migration. Unlike 131 this cannot alter the behaviour of a path
-- already running: nothing reads it yet.
--
-- ── WHAT IS SCORABLE HERE, AND WHAT IS NOT ──────────────────────────
--
-- THE RANKING CANNOT BE VALIDATED FROM ITS OWN CHOICES. If the system chose
-- HOLD, what EXIT would have returned is never observed -- not at the price that
-- was quoted, because a sale moves against itself through depth, and not at that
-- moment, because the moment passed. Comparing a realised HOLD against a
-- decision-time EXIT price would be scoring a counterfactual as though it had
-- happened. So the alternatives are stored for the record and explicitly NOT
-- scored.
--
-- WHAT IS FALSIFIABLE IS THE WORST CASE. It is not a forecast: it is the payout
-- in the worst outcome the fixture admits, minus what was paid including fees,
-- derived from the venue's own settlement terms. So it is a claimed LOWER BOUND
-- on the realised net. If a realised net comes in BELOW it, the model of the
-- world was wrong -- the outcome space, the fee, the described structure, or an
-- unhedged remainder -- and which of those it was is worth finding out. That is
-- a real test with no probability in it, and it is what this table exists to
-- make possible.
--
-- KEEP THIS MIGRATION OUT OF PRODUCTION until its consumer is released with it.
-- An unread table is harmless, but a migration applied without the code that
-- uses it is a schema-only pass, which is the pattern the owner ruled out.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_funded_decisions (
    -- CHOSEN BY THE CALLER, so a re-evaluated cycle reaches the same row rather
    -- than writing a second record of one decision. The same reason the
    -- reservation machine is addressed by `operation_id`.
    decision_id     text PRIMARY KEY,
    account_id      text        NOT NULL,
    venue           text        NOT NULL,
    fixture         text        NOT NULL,
    group_id        text,
    action          text        NOT NULL,
    -- NULL IS A REAL ANSWER HERE. The valuation withholds its verdict when fees
    -- are not priced, and a NULL says "no bound was claimed" -- which is not the
    -- same as a bound of zero and must not be scored as one.
    worst_case_usd  numeric,
    -- THE WHOLE RANKING AS IT STOOD, including the actions that were NOT taken
    -- and the ones that could not be ranked with their refusals. Stored for the
    -- record and for later diagnosis; never scored, because their outcomes do
    -- not exist.
    ranked          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    unrankable      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    inputs_present  text[]      NOT NULL DEFAULT '{}',
    inputs_missing  text[]      NOT NULL DEFAULT '{}',
    decided_at      timestamptz NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now(),

    -- ── THE OUTCOME HALF, WRITTEN LATER AND ONLY FROM THE BOOK ──────
    realised_known  boolean     NOT NULL DEFAULT false,
    realised_net_usd numeric,
    realised_at     timestamptz,
    -- WHERE THE NUMBER CAME FROM. A realised net taken from a price snapshot or
    -- a model would be unfalsifiable in the one direction that matters, so the
    -- basis is recorded and the only basis the code writes is the funded
    -- economics ledger.
    realised_basis  text,

    CONSTRAINT bettor_funded_decision_action_ck CHECK (
        action IN ('HOLD', 'ACQUIRE_HEDGE', 'REDUCE', 'EXIT',
                   'NO_ACTION_WAS_RANKABLE')),
    CONSTRAINT bettor_funded_decision_realised_ck CHECK (
        realised_known = (realised_net_usd IS NOT NULL)
        AND realised_known = (realised_at IS NOT NULL)
        AND realised_known = (realised_basis IS NOT NULL))
);

--: THE UNJOINED QUEUE, which is what a scoring pass iterates.
CREATE INDEX IF NOT EXISTS bettor_funded_decisions_unjoined_idx
    ON bettor_funded_decisions (decided_at)
    WHERE NOT realised_known;

CREATE INDEX IF NOT EXISTS bettor_funded_decisions_group_idx
    ON bettor_funded_decisions (group_id)
    WHERE group_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS bettor_funded_decisions_account_idx
    ON bettor_funded_decisions (account_id, decided_at);

-- ── A DECISION IS NOT REWRITTEN AFTER IT WAS MADE ───────────────────
--
-- The whole value of this table is that the first half was written BEFORE the
-- outcome existed. If the action, the claimed worst case, the ranking or the
-- decision time could be edited afterwards, the record would be a rationalisation
-- and its worst-case check would be unfalsifiable -- exactly the failure mode the
-- RN1X ledger's `baseline_p` comment describes as "never filled in afterwards".
--
-- Only the outcome half may be written, and only once.
CREATE OR REPLACE FUNCTION bettor_funded_decision_is_not_rewritten()
RETURNS trigger AS $$
BEGIN
    IF NEW.decision_id IS DISTINCT FROM OLD.decision_id
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.venue IS DISTINCT FROM OLD.venue
       OR NEW.fixture IS DISTINCT FROM OLD.fixture
       OR NEW.group_id IS DISTINCT FROM OLD.group_id
       OR NEW.action IS DISTINCT FROM OLD.action
       OR NEW.worst_case_usd IS DISTINCT FROM OLD.worst_case_usd
       OR NEW.ranked IS DISTINCT FROM OLD.ranked
       OR NEW.unrankable IS DISTINCT FROM OLD.unrankable
       OR NEW.inputs_present IS DISTINCT FROM OLD.inputs_present
       OR NEW.inputs_missing IS DISTINCT FROM OLD.inputs_missing
       OR NEW.decided_at IS DISTINCT FROM OLD.decided_at THEN
        RAISE EXCEPTION 'decision % was recorded before its outcome and is not '
                        'rewritten: a decision edited after the result is a '
                        'rationalisation, and its worst-case claim would no '
                        'longer be falsifiable', OLD.decision_id;
    END IF;
    IF OLD.realised_known AND (
           NEW.realised_net_usd IS DISTINCT FROM OLD.realised_net_usd
           OR NEW.realised_basis IS DISTINCT FROM OLD.realised_basis) THEN
        RAISE EXCEPTION 'decision % already has a realised outcome (% on %); it '
                        'is not restated',
            OLD.decision_id, OLD.realised_net_usd, OLD.realised_basis;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_decision_is_not_rewritten_trg
    ON bettor_funded_decisions;
CREATE TRIGGER bettor_funded_decision_is_not_rewritten_trg
    BEFORE UPDATE ON bettor_funded_decisions
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_decision_is_not_rewritten();

COMMIT;
