-- One whale exit must be mirrored ONCE.
--
-- mirror_exit runs BEFORE maybe_execute's live_orders INSERT and never
-- writes a row keyed to the EXIT trade, so both of copy_sweep's
-- idempotency guards miss it:
--
--   the asset guard  tests lo.asset = t.asset, and the exit trade's
--                    asset is the COMPLEMENT leg, on which we hold
--                    nothing
--   the trade guard  tests lo2.trade_id = t.id, and no row with that
--                    trade_id was ever inserted
--
-- A FULL exit is saved by accident: the position row becomes
-- 'cashed_out', so the next sweep finds nothing to sell and stops.
--
-- A PARTIAL exit is not. The remainder-preserving branch writes the
-- row back to status='filled', so ~120 seconds later copy_sweep
-- re-detects the same complement buy, classify_exit classifies it
-- again, and mirror_exit sells the same fraction of what is LEFT.
-- A single 50% trim by the whale becomes 50%, then 50% of the rest,
-- and so on — we exit a position he only trimmed, in a staircase, and
-- pay the spread on every step.
--
-- This is the exit leg's equivalent of the dedupe_key on trades: a
-- terminal record that one exit was applied, taken atomically so two
-- concurrent tasks cannot both act.
CREATE TABLE IF NOT EXISTS copy_exit_applied (
    trade_id    BIGINT PRIMARY KEY,
    row_id      BIGINT NOT NULL,
    closed_frac DOUBLE PRECISION,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
