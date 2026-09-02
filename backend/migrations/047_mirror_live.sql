-- 047: MIRROR LIVE, PHASE P1 (owner order 2026-09-02, "maximum effort
-- and certainty"). Phase P0 (046) only watched; P1 holds a long-only
-- fraction of a whale's net position on the venue, one BOOK per
-- (whale, market), and rests orders to keep it at target.
--
-- POSITIONS are live_orders rows: one standing 'filled' row per book,
-- lane='mirror', from the moment the book opens. Every consumer --
-- settlement, the record, the caps, the referees, the 045 asset claim --
-- therefore reads a book as the copy position it is, with no new status
-- and no index change. The INSERT of that row IS the claim on the asset.
--
-- ORDERS live here, in mirror_orders, so no reaper that keys on
-- live_orders.status='submitting', a missing order_id or a cent+qty
-- fingerprint can see one: a table they never read is a table they
-- cannot cancel or adopt. The BOOK state machine and the grading
-- numbers (target, his net, our ledger, the venue read, gross buys,
-- peak exposure, realized including partial sales, the settle
-- cross-check) live in mirror_books, because the global loss breaker
-- reads only terminal live_orders rows and cannot see a partial sale.
--
-- Two partial unique indexes are the hard guarantees the reconciler
-- leans on: at most ONE open book per (whale, market), and at most ONE
-- non-terminal order per book -- while an order is 'placing', 'open' or
-- 'unknown' nothing new can be placed on that book, whatever the tick
-- believes (review of the P1 design: an order whose state cannot be
-- read freezes its book; the index makes that a database fact).
--
-- Nothing in live_orders changes shape: the statuses a book uses
-- ('filled', 'cashed_out', 'cancelled'), `lane` (041) and `orig_shares`
-- (040) already exist, and 045's two claim indexes are untouched. The
-- one addition on live_orders is a partial index so the mirror's own
-- standing-row reads by slug do not scan the ledger.
CREATE TABLE IF NOT EXISTS mirror_books (
    id                BIGSERIAL PRIMARY KEY,
    whale             TEXT NOT NULL,
    condition_id      TEXT NOT NULL,
    us_market_slug    TEXT NOT NULL,
    game_key          TEXT,                      -- live_executor._us_game_key(us_market_slug)
    long_asset        TEXT NOT NULL,             -- his token that is the venue's LONG side
    other_asset       TEXT,
    intent            TEXT NOT NULL DEFAULT 'ORDER_INTENT_BUY_LONG',   -- constant in P1; P2 admits BUY_SHORT
    map_source        TEXT,                      -- 'ledger' | 'premap'
    ratio             DOUBLE PRECISION,          -- shadow ratio x min(1, clip/50)
    anchor_usd        DOUBLE PRECISION,
    standing_row_id   BIGINT REFERENCES live_orders (id),
    episode           INTEGER NOT NULL DEFAULT 1,
    flat_reopens      INTEGER NOT NULL DEFAULT 0,
    state             TEXT NOT NULL DEFAULT 'live'
                      CHECK (state IN ('live', 'frozen', 'closing', 'closed')),
    frozen_reason     TEXT, frozen_at TIMESTAMPTZ, frozen_ticks INTEGER NOT NULL DEFAULT 0,
    target            INTEGER, target_raw DOUBLE PRECISION,
    his_net           DOUBLE PRECISION, his_long DOUBLE PRECISION, his_other DOUBLE PRECISION,
    snap_long         DOUBLE PRECISION, snap_other DOUBLE PRECISION, drift DOUBLE PRECISION,
    his_level         DOUBLE PRECISION,
    ledger_net        INTEGER NOT NULL DEFAULT 0,       -- long-token shares BY OUR BOOKING
    venue_net         DOUBLE PRECISION,                 -- last venue read
    open_order_id     BIGINT,
    take_armed_at     TIMESTAMPTZ,
    last_reason       TEXT, last_plan JSONB,
    gross_buy_usd     DOUBLE PRECISION NOT NULL DEFAULT 0,
    gross_sell_usd    DOUBLE PRECISION NOT NULL DEFAULT 0,
    peak_exposure_usd DOUBLE PRECISION NOT NULL DEFAULT 0,   -- max over life of ledger_net x avg_cost: the STAKE
    avg_cost          DOUBLE PRECISION,
    realized_pnl      DOUBLE PRECISION NOT NULL DEFAULT 0,   -- every sale, partials included
    settled_pnl       DOUBLE PRECISION,                      -- the standing row's settled pnl, copied
    own_book_pnl      DOUBLE PRECISION,                      -- realized + remainder x (payout - avg_cost)
    settle_disagree   BOOLEAN,
    opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at         TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS mirror_books_one_open_per_market
    ON mirror_books (whale, us_market_slug) WHERE state <> 'closed';
CREATE INDEX IF NOT EXISTS mirror_books_state_idx ON mirror_books (state, updated_at DESC);
CREATE INDEX IF NOT EXISTS mirror_books_assets_idx ON mirror_books (long_asset, other_asset) WHERE state <> 'closed';
CREATE INDEX IF NOT EXISTS mirror_books_game_idx ON mirror_books (game_key) WHERE state <> 'closed';

CREATE TABLE IF NOT EXISTS mirror_orders (
    id                  BIGSERIAL PRIMARY KEY,
    book_id             BIGINT NOT NULL REFERENCES mirror_books (id),
    whale               TEXT NOT NULL,
    us_market_slug      TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('increase','reduce','flatten_paired','flatten_vanished','take','adjust')),
    side                TEXT NOT NULL CHECK (side IN ('BUY_LONG','SELL_LONG')),
    tif                 TEXT NOT NULL CHECK (tif IN ('GTC','GTD','IOC','CLOSE')),
    post_only           BOOLEAN NOT NULL DEFAULT false,
    good_till           TEXT,
    his_level           DOUBLE PRECISION,        -- his price, or 1 - q
    price               DOUBLE PRECISION NOT NULL,   -- outcome space
    wire                DOUBLE PRECISION NOT NULL,   -- the cent actually sent (rest_tick/wire_limit); the fingerprint
    qty                 INTEGER NOT NULL,
    order_id            TEXT,
    state               TEXT NOT NULL DEFAULT 'placing'
                        CHECK (state IN ('placing','open','filled','cancelled','expired','rejected','unknown','lost')),
    venue_state         TEXT,
    filled              DOUBLE PRECISION NOT NULL DEFAULT 0,
    booked_filled       DOUBLE PRECISION NOT NULL DEFAULT 0,   -- the idempotency cursor
    avg_px              DOUBLE PRECISION,
    cash_usd            DOUBLE PRECISION NOT NULL DEFAULT 0,
    realized            DOUBLE PRECISION NOT NULL DEFAULT 0,
    maker               BOOLEAN,
    taker_at_placement  BOOLEAN NOT NULL DEFAULT false,
    pre_ids             JSONB,                   -- open-order ids on the slug BEFORE we placed: a lost response is matched only against orders that were not already there (the rest lane's discipline)
    target_at_place     INTEGER, ledger_at_place INTEGER,
    bid_at_place        DOUBLE PRECISION, ask_at_place DOUBLE PRECISION,
    reason              TEXT,
    receipt             JSONB,
    placed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at             TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS mirror_orders_order_id ON mirror_orders (order_id) WHERE order_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS mirror_orders_one_open_per_book
    ON mirror_orders (book_id) WHERE state IN ('placing', 'open', 'unknown');
CREATE INDEX IF NOT EXISTS mirror_orders_live_idx ON mirror_orders (state) WHERE state IN ('placing','open','unknown','lost');
CREATE INDEX IF NOT EXISTS mirror_orders_book_idx ON mirror_orders (book_id, placed_at DESC);
CREATE INDEX IF NOT EXISTS live_orders_mirror_idx ON live_orders (us_market_slug) WHERE lane = 'mirror';
