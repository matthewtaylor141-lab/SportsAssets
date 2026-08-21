-- 025: MERIDIAN's journal (owner invitation 2026-08-21: "make this
-- page your own"). The co-CEO session writes short entries in the repo
-- (ops/meridian-journal.md); the diagnostic workflow publishes the
-- newest entry here; the /jarvis (MERIDIAN) page displays it. The
-- entry_hash unique key makes publishing idempotent — every workflow
-- run may re-post the top entry and only new ones land.
CREATE TABLE IF NOT EXISTS meridian_journal (
    id         BIGSERIAL PRIMARY KEY,
    entry      TEXT NOT NULL,
    mood       TEXT NOT NULL DEFAULT 'steady',
    entry_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
