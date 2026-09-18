-- ONE APPROVAL PERMITS AT MOST ONE SEND ATTEMPT.
--
-- WHAT THIS FIXES. `guarded_submit` took a caller-supplied row, an
-- optional persistence callable, and sent BEFORE recording. Nothing in
-- that shape stopped two callers reaching the venue for the same approved
-- ticket, and nothing survived a crash between the send and the record.
-- One lifecycle row is not, by itself, one venue submission: the
-- lifecycle says an order was APPROVED, and says nothing about how many
-- times the send was attempted.
--
-- The claim is a ROW, and the database is what makes it exclusive. The
-- unique constraint on client_order_id means the second caller's INSERT
-- fails; there is no window between a check and an act for it to slip
-- through, because the check IS the act.
--
-- ONE ATTEMPT EVER, not one unresolved attempt. A partial index keyed on
-- an unresolved state would let a resolved-as-not-sent attempt be retried
-- against the same approval, and the whole rule management stated is that
-- an approval permits at most one attempt. A second attempt needs a
-- second approval, with its own client_order_id.
--
-- AMBIGUITY IS A DURABLE STATE, not an absence of one. An attempt whose
-- response was lost sits in SENT_OUTCOME_UNKNOWN until a human or a
-- reconciliation resolves it, and `unresolved_attempt` is what a restart
-- reads to find it. The pre-image and the attempt identity are written
-- BEFORE the network call, so a process that dies mid-send leaves behind
-- exactly what the reconciliation needs.
--
-- Additive only. No existing row is read, rewritten or deleted, and
-- calibration_lifecycles is untouched.

CREATE TABLE IF NOT EXISTS calibration_send_attempts (
    attempt_id        TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,

    -- ONE ATTEMPT PER APPROVED TICKET. This is the whole guarantee.
    client_order_id   TEXT NOT NULL UNIQUE
        REFERENCES calibration_lifecycles (client_order_id),

    claimed_by        TEXT NOT NULL,
    claimed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- CLAIMED               the slot is held; nothing has been sent
    -- PRE_IMAGE_RECORDED    the resting ids are durable; about to send
    -- SENT_OUTCOME_UNKNOWN  the send happened, the response did not
    -- RESOLVED              the venue's answer is known and recorded
    -- NOT_SENT              refused before the network; nothing was sent
    state             TEXT NOT NULL DEFAULT 'CLAIMED'
        CHECK (state IN ('CLAIMED', 'PRE_IMAGE_RECORDED',
                         'SENT_OUTCOME_UNKNOWN', 'RESOLVED', 'NOT_SENT')),

    -- THE FIELDS A HUMAN APPROVED, bound server-side at claim time so the
    -- send cannot be made with anything else. Any later drift is visible
    -- against this snapshot rather than trusted from the caller.
    bound_fields      JSONB NOT NULL,

    -- Written BEFORE the network call. Without it an ambiguous send has
    -- nothing to be reconciled against (book 863).
    pre_open_order_ids JSONB,
    pre_image_at      TIMESTAMPTZ,

    -- Stamped around the call, so attribution has a real interval.
    sent_at           TIMESTAMPTZ,
    read_before       TIMESTAMPTZ,

    venue_order_id    TEXT,
    outcome           TEXT,
    reason            TEXT,
    resolved_at       TIMESTAMPTZ,
    resolved_by       TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS calibration_attempts_unresolved
    ON calibration_send_attempts (session_id)
 WHERE state IN ('CLAIMED', 'PRE_IMAGE_RECORDED', 'SENT_OUTCOME_UNKNOWN');

-- THE HIGH-WATER MARK, DURABLE. `record_spend` added a delta the caller
-- computed from a prior total it was holding. Two processes holding the
-- same prior total book the same cash twice, and a caller-provided prior
-- total cannot establish idempotency across processes. `cash_booked_usd`
-- is the venue's cumulative cash for this lifecycle as the DATABASE last
-- saw it, and the booking is GREATEST(...) in one statement, so re-reading
-- one terminal status ten times books the cash once.
ALTER TABLE calibration_lifecycles
    ADD COLUMN IF NOT EXISTS cash_booked_usd NUMERIC(12,4) NOT NULL DEFAULT 0
        CHECK (cash_booked_usd >= 0);
