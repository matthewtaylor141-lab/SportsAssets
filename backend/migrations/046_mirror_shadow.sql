-- POSITION MIRRORING, PHASE P0: THE SHADOW (owner order 2026-09-02,
-- "go for it, let's get this working").
--
-- The copy sleeve reacted to a whale's fills one at a time; a whale who
-- runs a two-sided book (RN1: 66 buys, 64 as a maker, 28,162 matched
-- pairs and a 24,423-share residual on one match) was copied as one
-- clip, sold in six pieces, and 55 of his 66 fills were thrown away.
-- The mirror reads his POSITION: per whale and market one number, his
-- net holding, and our job is to hold a fixed fraction of it.
--
-- Phase P0 places NOTHING. On every tick the shadow worker writes what
-- it read and what it WOULD have done, one row per (whale, market):
--
--   his_long / his_other  his position per outcome token, derived from
--                         the fills we ingest (BUY adds, SELL subtracts)
--   snap_long / snap_other the same from the exit worker's positions
--                         snapshot, so derived-vs-snapshot drift is a
--                         number before any order depends on it
--   his_net               long minus other, in long-token shares (on our
--                         netting venue his matched pairs cancel)
--   ratio / target        $50 measuring clip over his median opening
--                         burst; target = ratio x his_net, capped at the
--                         mark, whole shares, long-only in P0
--   ledger_net / venue_net what we hold by our ledger and by the venue
--   bid / ask / mark      the venue's quote for the long side at the tick
--   would_*               the one order the mirror would place (side,
--                         qty, resting price) and whether the book at
--                         the tick would already fill it
--   reason                why that order, or why none
--
-- Thirty games of this decide the ratio, the drift bound and the
-- resting-fill rate before phase P1 turns orders on.
CREATE TABLE IF NOT EXISTS mirror_shadow (
    id              BIGSERIAL PRIMARY KEY,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    whale           TEXT NOT NULL,
    condition_id    TEXT NOT NULL,
    us_market_slug  TEXT,
    long_asset      TEXT,
    other_asset     TEXT,
    his_long        DOUBLE PRECISION,
    his_other       DOUBLE PRECISION,
    his_net         DOUBLE PRECISION,
    snap_long       DOUBLE PRECISION,
    snap_other      DOUBLE PRECISION,
    ratio           DOUBLE PRECISION,
    target          INTEGER,
    target_raw      DOUBLE PRECISION,
    capped          BOOLEAN,
    ledger_net      INTEGER,
    venue_net       DOUBLE PRECISION,
    bid             DOUBLE PRECISION,
    ask             DOUBLE PRECISION,
    mark            DOUBLE PRECISION,
    his_last_px     DOUBLE PRECISION,
    would_side      TEXT,
    would_qty       INTEGER,
    would_px        DOUBLE PRECISION,
    would_fill      BOOLEAN,
    reason          TEXT,
    detail          JSONB
);

CREATE INDEX IF NOT EXISTS mirror_shadow_whale_market_at_idx
    ON mirror_shadow (whale, condition_id, at DESC);
CREATE INDEX IF NOT EXISTS mirror_shadow_at_idx ON mirror_shadow (at DESC);
