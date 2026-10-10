-- ══════════════════════════════════════════════════════════════════════
-- 366 · THE ACTUAL LANE'S AGGREGATE EXPOSURE CAP, AND AUDREY'S STALE
--       (rc6.2 pmus-exec: the independent audit of the retail path,
--       ActualLane -> execmirror.Venue on the PMUS_EXECMIRROR account).
-- ══════════════════════════════════════════════════════════════════════
--
-- NO CAPITAL, NO MODE CHANGE. Nothing here sends, enables or resumes
-- anything; no value of any existing column is changed; SMALL LIVE stays
-- SHADOW (migration 225's slc_shadow_only_ck is untouched).
--
-- ADDITIVE ONLY, BY DESIGN. This migration adds ONE new table and ONE
-- nullable column. It adds no constraint, trigger or unique index to, and
-- changes no constraint, default, type or nullability of, any table that
-- exists before it: the previous release (production runs ec8b892d, highest
-- migration 316) must keep working on the schema this leaves behind, and
-- tools/upgrade_path_receipt.py (compatibility()) can only PROVE that for a
-- schema in which every existing table is untouched except for a new
-- nullable column. An earlier draft of 366 added a CHECK to execmirror_control
-- and re-wrote smalllive_reconciliations' status CHECK; the receipt named
-- both ROLLBACK_CONSTRAINT_*, NOT_PROVEN_COMPATIBLE. 366 was never applied
-- anywhere, so it is rewritten rather than followed by a 367.
--
-- 1 · execmirror_exposure_caps (audit item 2). The lane had a per-order cap
--     (execmirror_control.max_order_usd) and the account's buying power, and
--     no cap on what it holds and has working in total: two decisions on two
--     markets each within the per-order cap made twice the exposure. The
--     ACTUAL lane now refuses a claim when the account's open + held actual
--     notional plus the order's cost exceeds the aggregate cap
--     (execution_intent.R_AGGREGATE_ABOVE_CAP; execmirror.aggregate_cap).
--       * No row for the account -- the state of every account here, and
--         what nothing in this migration changes -- means FAIL-CLOSED SMALL:
--         the cap equals max_order_usd, so at most one order's worth is ever
--         open or held.
--       * A row is an owner act with a named actor, like the per-order cap;
--         nothing inserts one here. The database guarantees what a row says:
--         the cap is NOT NULL and POSITIVE (a zero or negative cap would be
--         a refusal of everything or of nothing, and "no cap" is the absence
--         of the row, never a value in it), the actor is named, and there is
--         one cap per account (the primary key).
--       * It is a NEW table, so the previous release cannot meet a constraint
--         on it: it does not know the table. A release that has this table
--         dropped (rollback/366) reads no row -> max_order_usd.
CREATE TABLE IF NOT EXISTS execmirror_exposure_caps (
    account_id            text          PRIMARY KEY,
    max_open_notional_usd numeric(12,2) NOT NULL,
    actor                 text          NOT NULL,
    set_at                timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT execmirror_exposure_caps_open_notional_ck
        CHECK (max_open_notional_usd > 0),
    CONSTRAINT execmirror_exposure_caps_actor_ck
        CHECK (btrim(actor) <> '')
);
COMMENT ON TABLE execmirror_exposure_caps IS
    'Cap on the ACTUAL lane''s open + held notional (USD, collateral) per '
    'account. No row = fail-closed small: equal to execmirror_control.'
    'max_order_usd (migration 366).';

-- 2 · smalllive_reconciliations.stale_reason (audit item 4). Audrey read the
--     newest account snapshot with no age bound and wrote MATCHED /
--     NOT_MIRRORED from one 159 h old. A group she would call MATCHED or
--     NOT_MIRRORED while the newest snapshot is missing or older than its
--     admissibility bound (180 s, execmirror.ACCOUNT_SNAPSHOT_MAX_AGE_S) is
--     one she cannot decide; she records that under a name of its own, STALE,
--     with what she would have said kept in the chain.
--       The status column's CHECK (migration 198) is NOT touched -- widening
--     it would change an existing table's constraint. STALE is stored as
--     status PENDING (the existing "no final answer yet") plus this column,
--     the reason, written in the same statement as the status; every reader
--     of the current release reads PENDING + a reason as STALE
--     (audrey_reconciliation_status.effective). NULL for every other row.
--     The previous release does not know the column, names its own columns
--     in its INSERT, and reads the row as PENDING: not MATCHED, not
--     NOT_MIRRORED, and never a DISCREPANCY.
--       A nullable column with no default and no constraint: existing rows
--     are not rewritten and the previous release's INSERTs are unaffected.
ALTER TABLE smalllive_reconciliations
    ADD COLUMN IF NOT EXISTS stale_reason text;
COMMENT ON COLUMN smalllive_reconciliations.stale_reason IS
    'Why Audrey could not decide the group (the named code, e.g. '
    'AUDREY_ACCOUNT_SNAPSHOT_NOT_CURRENT), set only together with status '
    'PENDING, and PENDING + a reason is read as STALE (migration 366).';
