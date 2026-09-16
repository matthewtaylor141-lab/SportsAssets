-- 054: MIRROR CANDIDATE REFUSALS (W2 / P2, 2026-09-07; investigation
-- gap_planned_unopened.md: 58 mapped tennis and football markets, $218k
-- in 24 h, had a shadow plan on an OPEN two-sided book while he traded
-- and never a mirror_books row -- and the live lane's own verdict on
-- each survived nowhere. The census counts a name once per tick, the
-- heartbeat keeps the last tick's ~20 `recent` events, and four exits
-- of mirror_live._tick_candidate returned silently).
--
-- ONE table, additive, re-runnable, measurement only: no order path
-- reads it. The live lane writes one row per (whale, condition_id)
-- TRANSITION of the candidate's refusal name -- a new name, or the same
-- name re-stamped at most every 900 s (mirror_live.CAND_REFUSAL_RESTAMP_S,
-- the _unmapped_until memo class) -- so the writes are bounded by his
-- active conditions over 900 s. `refusal` is the census name as
-- _mirror_stop counted it (the CENSUS_KEYS spelling, unchanged), and
-- the numbers are what the tick already held when it left: his net in
-- long-token shares, the signed target, the mark, his level, the ask,
-- the executor's side band, the long token (the catalogue's when his
-- fills never touched it), the book counts admission read, his active
-- conditions, the candidate reads spent so far and the tick's wall time
-- so far. NULL is "not reached before the exit", never a guess. The
-- workers never run migrations: mirror_live writes through one
-- statement per tick and, while this table is absent, counts
-- `refusal_write_failed` and goes on (logged once per process).
--
-- NUMBERING. 054: docs/mirror-to-a-tee-program.md:184-187 reserves 048
-- (Phase 0b) and 051 (Phase 6); 052 and 053 are landed. migrate.py
-- applies the sorted glob, so the two gaps are harmless (053's header
-- says the same).
CREATE TABLE IF NOT EXISTS mirror_candidate_refusals (
    id                BIGSERIAL PRIMARY KEY,
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    whale             TEXT NOT NULL,
    condition_id      TEXT NOT NULL,
    us_slug           TEXT,                      -- the venue slug the mapper named, when it did
    refusal           TEXT NOT NULL,             -- the census name the candidate left under
    his_net           DOUBLE PRECISION,          -- long-token shares, signed
    target            INTEGER,                   -- the signed target (a short is negative)
    mark              DOUBLE PRECISION,
    his_px            DOUBLE PRECISION,          -- his level for the wire (1 - p on the short road)
    ask               DOUBLE PRECISION,
    band              DOUBLE PRECISION,          -- LIVE_SIDE_PRICE_BAND as the tick read it
    long_asset        TEXT,
    long_from         TEXT,                      -- 'catalogue' when the mapper named the long token off the catalogue (W2 / P1)
    books_live        INTEGER,
    opened_today      INTEGER,
    active_conditions INTEGER,                   -- his conditions in the lookback at the walk
    cand_reads        INTEGER,                   -- candidate quote reads spent when it left
    tick_s            DOUBLE PRECISION           -- the tick's wall time so far
);
CREATE INDEX IF NOT EXISTS mirror_candidate_refusals_whale_market_at_idx
    ON mirror_candidate_refusals (whale, condition_id, at DESC);
CREATE INDEX IF NOT EXISTS mirror_candidate_refusals_at_idx ON mirror_candidate_refusals (at DESC);
