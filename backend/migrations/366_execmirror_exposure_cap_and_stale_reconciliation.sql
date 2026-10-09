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
-- 1 · execmirror_control.max_open_notional_usd (audit item 2). The lane had
--     a per-order cap (max_order_usd) and the account's buying power, and no
--     cap on what it holds and has working in total: two decisions on two
--     markets each within the per-order cap made twice the exposure. The
--     ACTUAL lane now refuses a claim when the account's open + held actual
--     notional plus the order's cost exceeds this cap
--     (execution_intent.R_AGGREGATE_ABOVE_CAP; execmirror.aggregate_cap_usd).
--     NULL -- the default, and the value every existing row gets -- means
--     FAIL-CLOSED SMALL: the cap equals max_order_usd, so at most one order's
--     worth is ever open or held. Setting a larger value is an owner act
--     with a named actor, like the per-order cap; nothing sets it here.
--
-- 2 · smalllive_reconciliations.status 'STALE' (audit item 4). Audrey read
--     the newest account snapshot with no age bound and wrote MATCHED /
--     NOT_MIRRORED from one 159 h old. A group she would call MATCHED or
--     NOT_MIRRORED while the newest snapshot is missing or older than its
--     admissibility bound (180 s, execmirror.ACCOUNT_SNAPSHOT_MAX_AGE_S) is
--     now STALE, with what she would have said kept in the chain.
ALTER TABLE execmirror_control
    ADD COLUMN IF NOT EXISTS max_open_notional_usd numeric(12,2);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'execmirror_control_open_notional_ck') THEN
        ALTER TABLE execmirror_control
            ADD CONSTRAINT execmirror_control_open_notional_ck CHECK (
                max_open_notional_usd IS NULL OR max_open_notional_usd > 0);
    END IF;
END $$;
COMMENT ON COLUMN execmirror_control.max_open_notional_usd IS
    'Cap on the ACTUAL lane''s open + held notional (USD, collateral). NULL = '
    'fail-closed small: equal to max_order_usd (migration 366).';

ALTER TABLE smalllive_reconciliations
    DROP CONSTRAINT IF EXISTS smalllive_reconciliations_status_check;
ALTER TABLE smalllive_reconciliations
    ADD CONSTRAINT smalllive_reconciliations_status_check
    CHECK (status IN ('MATCHED', 'DISCREPANCY', 'PENDING', 'NOT_MIRRORED',
                      'STALE'));
