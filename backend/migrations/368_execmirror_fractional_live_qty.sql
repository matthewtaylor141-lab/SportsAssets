-- 368 · THE EXACT LIVE QUANTITY OF AN ACTUAL ORDER ON A FRACTIONAL MARKET
-- (rc6.3 pmus-sizing; contract audit item 3d, Issue #6 gate 6).
--
-- Polymarket US documents a market's minimumTradeQty ("0.01 = 1% of a
-- contract, 1.0 = one whole contract") and create-order's quantity as a
-- number that "Supports decimal quantities on markets whose minimumTradeQty
-- is less than 1"; most markets trade in steps of 0.01 contracts. The ACTUAL
-- BUY is now sized at 1:1000 to the market's own step (execmirror.
-- size_to_market): $1,500 PAPER at 0.62 is 2,419 contracts, 2.41 live, not 2.
--
-- execmirror_orders.live_qty (192) and execution_intents.live_qty (199) are
-- INTEGER columns, and retyping either is ROLLBACK_COLUMN_TYPE_CHANGED (the
-- previous release must run on this schema; tools/upgrade_path_receipt.py).
-- So the exact quantity gets its OWN additive column, nullable, no default,
-- no constraint (nothing the previous release writes can be refused):
--
--   live_qty_exact   the order's quantity when it is NOT a whole number of
--                    contracts (2.41); NULL for every whole-contract row,
--                    which keeps its quantity in live_qty exactly as before.
--   live_qty         for such a row, the quantity ROUNDED UP to whole
--                    contracts (3 for 2.41): a reader that knows only the
--                    integer column never under-counts what the order may
--                    tie up. Every reader in this release reads
--                    coalesce(live_qty_exact, live_qty) (execmirror.
--                    LIVE_QTY_SQL / row_live_qty).
--
-- No row is rewritten: every existing row is a whole-contract row and keeps
-- live_qty_exact NULL.
ALTER TABLE execmirror_orders ADD COLUMN IF NOT EXISTS live_qty_exact numeric(18,6);
ALTER TABLE execution_intents ADD COLUMN IF NOT EXISTS live_qty_exact numeric(18,6);

COMMENT ON COLUMN execmirror_orders.live_qty_exact IS
    'rc6.3 pmus-sizing (368): the order quantity when it is not a whole number of contracts (a fractional market, minimumTradeQty < 1); NULL for whole-contract rows. live_qty then holds it rounded UP. Read coalesce(live_qty_exact, live_qty).';
COMMENT ON COLUMN execution_intents.live_qty_exact IS
    'rc6.3 pmus-sizing (368): the ACTUAL size when it is not a whole number of contracts; NULL otherwise. live_qty then holds it rounded UP. Read coalesce(live_qty_exact, live_qty).';
