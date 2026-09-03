-- 049: MIRROR FIDELITY COLUMNS (owner order 2026-09-02, "I want us to
-- match everything ... mirror the whales to a tee"). Measurement only:
-- no rule keys on any column added here. The to-a-tee program's
-- timing lens found that nowhere in the system records whether HIS
-- fill was a maker or a taker fill (001 has no column; the chain
-- decoders read maker/taker topics and drop them; every Data-API
-- caller asks takerOnly=false so maker fills are INCLUDED but nothing
-- marks which rows are which), and that a mirror order carries no
-- pointer to the fill that triggered it, so the per-fill fidelity
-- join (his fill -> our order) is ambiguous whenever he prints several
-- fills at one cent inside seconds (five at 0.46 inside 9 s on the one
-- fully read book).
--
-- 047 stays CREATE-only (test_mirror_live_migration pins it); every
-- statement here is an ALTER ... ADD COLUMN IF NOT EXISTS so the file
-- is re-runnable and adds nothing but nullable columns: an unwritten
-- row reads NULL, which every reader treats as "unknown", never as a
-- guess.

-- trades.taker: NULL = unknown (the default for every row ever
-- ingested); true/false only from the hourly reconciler's takerOnly
-- census, and only when that census's match_rate clears its floor.
-- The timing lens's market refutation: by count 2 of 66 on the one
-- read book, by dollars anywhere in [~0, 68%] -- so the column is a
-- census, and the reading refuses (stays NULL) rather than guess.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS taker BOOLEAN NULL;

-- mirror_orders.trigger_trade_id: the trades row (BIGSERIAL id, 001)
-- whose fill this order was placed for, so fidelity per HIS fill is a
-- key join, not a (level, window) guess. NULL for orders no single
-- fill triggered (flattens, takes, adoption).
ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS trigger_trade_id BIGINT NULL REFERENCES trades (id);

-- mirror_orders.his_fill_ts: the venue timestamp of that fill, copied
-- at placement so react_s = placed_at - his_fill_ts survives a later
-- trades enrichment or a re-ingest.
ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS his_fill_ts TIMESTAMPTZ NULL;

-- mirror_orders.first_fill_at: when OUR order first filled (any
-- quantity), so time-to-fill is measured from his fill to our first
-- fill, not to done_at (which a partial never reaches until the TTL).
ALTER TABLE mirror_orders ADD COLUMN IF NOT EXISTS first_fill_at TIMESTAMPTZ NULL;
