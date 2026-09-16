-- Partial index for the NOSLUG repair lane (mapper-fail diagnosis
-- 2026-08-30).
--
-- The unmapped-funnel census found 22,331 rejected rows whose trade
-- carries NO slug at all — and for 3,447 of them market_tokens
-- already knows the token, so a zero-network catalog join can backfill
-- slug/title/condition_id without a single Gamma call. That join runs
-- every metadata-refresher cycle (~60s) with the predicate
--
--     COALESCE(market_slug, event_slug, '') = ''
--
-- which no existing index serves: once the historical backlog drains,
-- the pass should be an index scan over the (near-empty) slugless
-- remainder, not a full walk of a multi-million-row ledger each
-- minute. Partial on exactly that predicate so the index stays tiny.
CREATE INDEX IF NOT EXISTS trades_noslug_idx ON trades (asset)
    WHERE COALESCE(market_slug, event_slug, '') = '';
