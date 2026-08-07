-- Cross-venue copy claims (owner directive 2026-08-07: both venues
-- firing, one copy per whale position ACROSS venues). The engine's
-- Kalshi sleeve reports each FILLED copy here so the PMUS paths (fresh
-- executor + hourly recovery sweep) can never buy the same whale
-- position a second time. Before this table the guard was one-way
-- only: the engine skipped pmus_copied identities, but a position PM
-- missed and Kalshi copied was invisible to the PM sweep and could be
-- double-bought on its next pass.
CREATE TABLE IF NOT EXISTS kalshi_claims (
    asset       text PRIMARY KEY,
    whale       text,
    ticker      text,
    claimed_at  timestamptz NOT NULL DEFAULT now()
);
