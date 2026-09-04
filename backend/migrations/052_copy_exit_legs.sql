-- 052: THE SALE-LEG LEDGER (cash-out program, unit U1). MEASUREMENT
-- ONLY: nothing served reads this table, no money path writes it, and
-- no endpoint's numbers move because it exists. It is the fact base
-- the next unit needs and the repository does not have.
--
-- NUMBERING. 052, not 050. docs/mirror-to-a-tee-program.md:180 is a
-- checked-in reservation register -- "048 = Phase 0b, 049 = Phase 4,
-- 050 = Phase 5 (shorts), 051 = Phase 6 (game claims)" -- and the same
-- register is already enforced by tests/test_mirror_live_migration.py
-- (:399, :421). 049 is landed; 048, 050 and 051 are spoken for by an
-- in-progress workstream whose own house tests count files by prefix
-- (`sum(f.startswith("050_")) == 1`), so a second 050 would fail Phase
-- 5's test as well as this unit's. The gap at 048/050/051 is
-- deliberate and already sanctioned by
-- test_049_exists_and_sorts_after_047_with_only_the_reserved_048_between;
-- migrate.py applies the sorted glob, so gaps are harmless.
--
-- WHY IT HAS TO EXIST. When a position of ours is sold by hand in the
-- venue app the repository records NOTHING: our order row sits
-- status='filled' for ever, counted as live exposure with shares
-- behind it we no longer own, and no resolution ever arrives to grade
-- it. Writing that row back is a WRITE, and a write needs a fact to
-- write from -- specifically the venue's own execution ORDER ID, the
-- field that can tie one sale to the order that made it.
--
-- The repository already reads every sale, twice, and drops that id
-- both times:
--
--   the in-memory fold   _fold_trade (api/track_record.py) reduces a
--                        sale to ONE dict per market slug --
--                        {qty, proceeds, realized, last_ts} -- so two
--                        sales on one slug are indistinguishable and
--                        the execution order id is never carried.
--   the slim archive     _slim (api/track_record.py:1200-1222) lifts
--                        the nested order's SIDE to the top level and
--                        then discards the order object, id included.
--
-- The TABLE it slims from -- pmus_activity_archive (track_record.py
-- :1843-1846) -- keeps the venue's full payload, is append-only, and
-- is keyed by the venue's own activity id. So the id survives in
-- Postgres and nowhere else. This ledger is that survival made
-- queryable: one row per SALE LEG, keyed by the same archive key, so a
-- fold that runs twice cannot double-count and a leg can be pointed at
-- one of our order rows exactly once.
--
-- WHAT THE KEY IS, EXACTLY. venue_activity_id is the ARCHIVE ROW'S
-- KEY: the venue's own activity id wherever the venue sent one, and a
-- content hash -- sha256 of the sorted-key payload, track_record.py
-- :1864-1867 -- where it did not. The archive itself makes that
-- substitution and this ledger stores the archive's key verbatim so
-- the two tables join. What is never done is INVENTING a key here: an
-- activity folded outside the archive path with no id of its own is
-- refused by the fold, not stored. A reader taking this id back to the
-- VENUE must therefore check it against pmus_activity_archive first --
-- a 64-hex key is ours, not theirs.
--
-- WHAT A ROW IS, AND WHAT IT IS NOT.
--
--   side='sell'       the venue's own order said SELL, or (side_src
--                     ='realized_fallback') it named no side at all and
--                     the trade carried realized P&L, which a buy
--                     under average-cost accounting never does. Cash
--                     came in: shares and proceeds_usd count.
--   side='buy_close'  a BUY that carried realized P&L -- a SHORT being
--                     closed. Its realized dollars are real and count;
--                     its qty*price is NOT proceeds and must never pad
--                     a cash-in figure (the 2026-08-19 raw-feed audit
--                     found 23 of these booked as sales). shares and
--                     proceeds_usd are therefore 0 on these rows --
--                     but trade_qty carries the venue's raw quantity,
--                     because a short IS closed by a BUY and the next
--                     unit has to know how many shares that leg
--                     retired.
--   side='unknown'    THE UNDECIDABLE POPULATION, and the reason this
--                     column exists. A trade that names no side
--                     anywhere and carries realized P&L of exactly
--                     zero cannot be told apart from a fresh buy by
--                     any field the venue sent. Today the record folds
--                     it as an ENTRY, which inflates deployed capital.
--                     It is stored here NAMED, contributing nothing to
--                     any total, so it can be counted instead of
--                     silently booked as either thing.
--
-- READERS MUST FILTER ON side. The per-slug totals this ledger is
-- gated against are
--     sum(shares)       FILTER (WHERE side = 'sell')
--     sum(proceeds_usd) FILTER (WHERE side = 'sell')
--     sum(realized_usd) FILTER (WHERE side IN ('sell', 'buy_close'))
-- and that sum must reproduce track_record's sold_markets to the cent
-- for every slug. sold_markets is built from the venue's own tape and
-- already counts hand sales correctly: it is the yardstick, and a
-- disagreement means THIS ledger is wrong.
--
-- realized_usd IS THE VENUE'S OWN FIGURE, CARRIED, NEVER RECOMPUTED.
-- Not proceeds minus a basis we reconstruct, not a price read off a
-- bid: the number the venue stamped on its own trade row. proceeds_usd
-- is qty*price and is kept in its own column precisely so the two can
-- never be confused for one another.
--
-- RETENTION. Append-only by design: no DELETE, no TTL, no partition.
-- At the ceiling the venue crawl states (~25,000 activities/day) a full
-- day is ~10 MB and a year ~3.7 GB. Nothing here needs a prune to be
-- correct, and a prune can be added later without losing a fact: every
-- row is rebuildable from pmus_activity_archive by re-running the
-- sweep (the write is ON CONFLICT DO NOTHING and the pass is
-- resumable), which is exactly why no retention policy is invented now.
--
-- Re-runnable on purpose: CREATE TABLE IF NOT EXISTS, then ADD COLUMN
-- IF NOT EXISTS for every column (a no-op on a fresh create; it
-- repairs a table an older copy of this file created), then the
-- constraints dropped and re-added the way 045 does it, since Postgres
-- has no ADD CONSTRAINT IF NOT EXISTS. The repair path cannot add a
-- column NOT NULL to a table that already has rows, so the columns
-- that must never be NULL are pinned by a CHECK instead -- a CHECK is
-- satisfied by NULL, so each one names IS NOT NULL explicitly.

CREATE TABLE IF NOT EXISTS copy_exit_legs (
    -- The ARCHIVE ROW'S key (see WHAT THE KEY IS above): the venue's
    -- own activity id where the venue sent one, the archive's content
    -- hash where it did not. Never invented here.
    venue_activity_id TEXT PRIMARY KEY,
    -- Venue trade time, epoch seconds. NULL = the venue dated nothing;
    -- unknown is not now().
    ts                DOUBLE PRECISION,
    market_slug       TEXT NOT NULL,
    -- NUMERIC, not double precision: the gate is stated to the CENT
    -- and sum(double precision) is order-dependent at the last bits.
    --
    -- shares is the CASH-IN quantity: it is 0 on a buy_close and on an
    -- unknown, so a reader summing it without a side filter cannot
    -- subtract a short's closing buy from a long's exit.
    shares            NUMERIC(24, 6) NOT NULL DEFAULT 0,
    -- The venue's RAW qty on the trade, whatever the side. This is the
    -- quantity the leg actually moved -- the number a writer needs to
    -- reduce a position by, including for a SHORT, which is closed by
    -- a BUY and therefore carries shares = 0. Kept beside shares
    -- rather than inside it so neither can be mistaken for the other.
    trade_qty         NUMERIC(24, 6) NOT NULL DEFAULT 0,
    price             NUMERIC(24, 6),
    proceeds_usd      NUMERIC(24, 6) NOT NULL DEFAULT 0,
    realized_usd      NUMERIC(24, 6) NOT NULL DEFAULT 0,
    -- The nested execution order's own id. NULL when the venue named
    -- none -- unknown, never a placeholder. Nothing else in the
    -- repository keeps this field. Read the caveat with it: the fold
    -- takes the first execution that names a side (else the first
    -- non-empty one) and the venue's `isAggressor` flag has never been
    -- observed carrying a value in production (api/proof2.py:738-740),
    -- so WHICH execution is ours is not established by this data.
    -- side_src records which execution the side was read from.
    order_id          TEXT,
    side              TEXT NOT NULL,
    -- WHERE the side came from, so a reader can tell a side the venue
    -- STATED from one inferred from realized P&L:
    -- 'top_level' | 'nested_aggressor' | 'nested_passive' |
    -- 'realized_fallback' | 'none'.
    side_src          TEXT NOT NULL,
    -- Which tape the leg was folded from ('pmus_activity_archive').
    source            TEXT NOT NULL,
    -- OUR live_orders row, once something can attribute it. Written by
    -- a later unit, never by the fold: the fold knows what the venue
    -- did, not which of our rows it closed, and when two of our rows
    -- share one slug that question has no answer in this data at all.
    row_id            BIGINT REFERENCES live_orders (id),
    folded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS ts DOUBLE PRECISION;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS market_slug TEXT;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS shares NUMERIC(24, 6) NOT NULL DEFAULT 0;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS trade_qty NUMERIC(24, 6) NOT NULL DEFAULT 0;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS price NUMERIC(24, 6);
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS proceeds_usd NUMERIC(24, 6) NOT NULL DEFAULT 0;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS realized_usd NUMERIC(24, 6) NOT NULL DEFAULT 0;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS order_id TEXT;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS side TEXT;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS side_src TEXT;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS row_id BIGINT REFERENCES live_orders (id);
ALTER TABLE copy_exit_legs ADD COLUMN IF NOT EXISTS folded_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- The three sides are the whole classification. A fourth value would
-- mean the fold learned a shape this ledger's readers do not know how
-- to sum, and the database refuses it rather than let it through into
-- a total. IS NOT NULL is stated because a CHECK is satisfied by NULL,
-- and the ADD COLUMN repair path above cannot mark a column NOT NULL:
-- a leg with a NULL side would be excluded by every reader's
-- FILTER (WHERE side = 'sell') while still carrying money.
-- Dropped and re-added so the file is re-runnable (045's pattern).
ALTER TABLE copy_exit_legs DROP CONSTRAINT IF EXISTS copy_exit_legs_side_check;
ALTER TABLE copy_exit_legs ADD CONSTRAINT copy_exit_legs_side_check
    CHECK (side IS NOT NULL AND side IN ('sell', 'buy_close', 'unknown'));

-- The other three columns the CREATE declares NOT NULL, pinned the
-- same way for the same reason.
ALTER TABLE copy_exit_legs DROP CONSTRAINT IF EXISTS copy_exit_legs_named_columns;
ALTER TABLE copy_exit_legs ADD CONSTRAINT copy_exit_legs_named_columns
    CHECK (market_slug IS NOT NULL AND side_src IS NOT NULL AND source IS NOT NULL);

-- An 'unknown' leg is not an exit and carries no money by
-- construction. Pinning that at the database means no later writer can
-- quietly start booking dollars onto the undecidable population.
-- trade_qty is deliberately NOT pinned to 0 here: the raw quantity is
-- a fact about the trade, not a claim about money.
ALTER TABLE copy_exit_legs DROP CONSTRAINT IF EXISTS copy_exit_legs_unknown_is_moneyless;
ALTER TABLE copy_exit_legs ADD CONSTRAINT copy_exit_legs_unknown_is_moneyless
    CHECK (side <> 'unknown' OR (proceeds_usd = 0 AND realized_usd = 0 AND shares = 0));

-- A short-closing BUY realizes money but takes none in: its qty*price
-- is not cash. Same reasoning, same place. Again trade_qty is exempt:
-- it is the quantity that leg retired, which is precisely what a
-- writer closing a short needs to read.
ALTER TABLE copy_exit_legs DROP CONSTRAINT IF EXISTS copy_exit_legs_buy_close_no_proceeds;
ALTER TABLE copy_exit_legs ADD CONSTRAINT copy_exit_legs_buy_close_no_proceeds
    CHECK (side <> 'buy_close' OR (proceeds_usd = 0 AND shares = 0));

-- The gate reads per slug, and pages by slug. That is the only index
-- this unit's own reads need; an index nothing reads is write
-- amplification, so there are two, not four.
CREATE INDEX IF NOT EXISTS copy_exit_legs_slug_idx
    ON copy_exit_legs (market_slug);
-- Partial, and tiny: the attributed legs are the small minority and
-- the only ones the writer that comes next ever looks up by our row.
CREATE INDEX IF NOT EXISTS copy_exit_legs_row_idx
    ON copy_exit_legs (row_id) WHERE row_id IS NOT NULL;
