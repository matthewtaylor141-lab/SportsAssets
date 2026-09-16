-- HIS EDGE, DECOMPOSED INTO SELECTION AND TIMING.
--
-- Owner question 2026-09-01 (evening): can the whales' selection be
-- learned from their history instead of copied? That turns on one
-- number nobody has measured: how much of a whale's edge survives when
-- his TIMING is removed. Score every one of his buys three ways -- at
-- his fill price (his real edge), at the market price minutes later
-- (timing removed), and at the price just before the game starts (all
-- timing removed: pure "did he pick the right side"). If the last one
-- still clears zero at 95%, there is a learnable WHAT under the WHEN.
--
-- The marks come from the public CLOB price history for the token,
-- fetched once per token by workers/edge_marks.py and stored here so
-- the analysis is a query, not a crawl. NULL means the mark could not
-- be read (no point inside tolerance, no game start, in-game trade for
-- p_pre); NULL is dropped from that leg, never zero-filled.
CREATE TABLE IF NOT EXISTS trade_marks (
    trade_id    BIGINT PRIMARY KEY REFERENCES trades (id) ON DELETE CASCADE,
    p_5m        DOUBLE PRECISION,
    p_10m       DOUBLE PRECISION,
    p_60m       DOUBLE PRECISION,
    p_pre       DOUBLE PRECISION,   -- last mark before game start; NULL if in-game or unknown
    game_start  TIMESTAMPTZ,
    fidelity_m  INTEGER,            -- minutes between series points the marks were read from
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    err         TEXT
);

CREATE INDEX IF NOT EXISTS trade_marks_fetched_idx ON trade_marks (fetched_at DESC);

-- Game start per condition, from the CLOB market record; cached so a
-- token's marks never wait on a second metadata call.
CREATE TABLE IF NOT EXISTS market_starts (
    condition_id TEXT PRIMARY KEY,
    game_start   TIMESTAMPTZ,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    err          TEXT
);
