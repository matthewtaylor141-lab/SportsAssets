-- 026: MERIDIAN conversation mirror (owner order 2026-08-21 evening:
-- "when I prompt Meridian, the actual conversation happens here and is
-- stored here as if we were typing back and forth"). Every voice
-- exchange on the MERIDIAN page is mirrored to this table; the
-- diagnostic workflow prints unseen turns into the engine session's
-- probe at every check-in (and marks them seen), so the voice
-- conversation and the console session hold ONE continuous record.
CREATE TABLE IF NOT EXISTS meridian_exchange (
    id       BIGSERIAL PRIMARY KEY,
    role     TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text     TEXT NOT NULL,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    seen_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS meridian_exchange_unseen
    ON meridian_exchange (id) WHERE seen_at IS NULL;
