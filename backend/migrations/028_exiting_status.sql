-- 'exiting' — an ATOMIC CLAIM on a position we are about to sell.
--
-- mirror_exit selected the newest status='filled' row, sold against it,
-- and only afterwards wrote 'cashed_out'. No FOR UPDATE, no status
-- precondition on the write, and it runs OUTSIDE the 4-slot copy
-- semaphore. A whale who buys the complement in five fills inside one
-- second produces five distinct trades, five dedupe keys, five
-- concurrent execute_copy tasks — and five concurrent mirror_exit calls
-- that every one read the same 'filled' row and every one issue a sell.
-- The only thing between that and a 5x oversell was the venue's own
-- position read happening to reach zero between calls.
--
-- With this status the claim is the UPDATE itself:
--     UPDATE live_orders SET status='exiting'
--      WHERE id=$1 AND status='filled' RETURNING id
-- Exactly one caller gets a row back. The rest see nothing and stop.
-- A refusal that spent no money restores 'filled'.
--
-- It is deliberately NOT in the sweep's blocking list as a synonym for
-- filled: a row mid-exit must not look re-buyable, and 'cashed_out'
-- was missing from that list too — copy_sweep would re-buy every
-- position we exited, at whatever price the market had moved to.
ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_status_check;
ALTER TABLE live_orders ADD CONSTRAINT live_orders_status_check
    CHECK (status IN ('submitting', 'filled', 'unfilled', 'rejected',
                      'error', 'settled', 'cashed_out', 'exiting'));
