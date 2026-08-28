-- Manual desk resting orders (owner order 2026-08-28, venue parity):
-- a desk GTC limit ticket rests on the venue's book, so the audit row
-- needs a live 'open' state and a terminal 'cancelled' one. Scope is
-- the MANUAL sleeve only — every autonomous path still speaks
-- FOK/IOC and never produces these states. Status scans elsewhere
-- filter by explicit status lists, so 'open' rows are invisible to
-- settlement, re-buy guards, and the public record until they fill.
ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_status_check;
ALTER TABLE live_orders ADD CONSTRAINT live_orders_status_check
    CHECK (status IN ('submitting', 'filled', 'unfilled', 'rejected',
                      'error', 'settled', 'cashed_out', 'exiting',
                      'open', 'cancelled'));
