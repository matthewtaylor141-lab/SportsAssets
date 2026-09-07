-- 056: MIRROR REGISTERED POSITIONS (E5 / P1, 2026-09-07; owner question
-- 16:5xZ "when he wins we win, when he loses we lose?"). The 16:42Z
-- heartbeat held 8 frozen books; one of them, book 77 (Shelton /
-- Tsitsipas), read venue 1,128 / ledger 0 / manual 0 since 2026-09-06:
-- a placement whose response was lost, whose fill the venue reports and
-- the ledger never booked. Nothing could explain those shares -- the
-- desk's `manual` sleeve explains only its own live_orders rows -- so
-- the book froze `venue_ledger_disagree` on every tick, cancelled every
-- order and followed nothing.
--
-- ONE table, additive, re-runnable: THE OPERATOR REGISTER. A row is the
-- owner's dated, named statement that `shares` on the slug are outside
-- the book. Written ONLY by the render-ops `mirror-register` dispatch
-- (confirm=DO, arg `<book_id>=<shares> <note>`), which takes the slug,
-- the asset and the condition from the book's own row -- never typed --
-- prints the venue's reading the worker last wrote (mirror_books
-- .venue_net) beside the ledger and the desk's manual shares, and
-- refuses unless the figure equals venue - ledger - manual EXACTLY, with
-- the sign of the book's own leg (never a negative on a long book: that
-- would let the live plan sell shares the venue does not hold). The
-- live worker (mirror_live._read_market, _SQL_REGISTERED_SHARES) reads
-- the signed sum per slug beside the manual shares and treats it
-- exactly as `manual` in the venue == ledger comparison; it never
-- writes here and never adopts a fill into it. `shares` is signed like
-- the ledger (negative is the short side); `side` says which in words.
-- The workers never run migrations: while this table is absent the
-- worker counts `registered_unreadable` (logged once per process) and
-- the register explains nothing.
--
-- NUMBERING. 056: 054 and 055 are landed; 048 and 051 stay reserved by
-- docs/mirror-to-a-tee-program.md:184-187 (migrate.py applies the
-- sorted glob, so the gaps are harmless).
CREATE TABLE IF NOT EXISTS mirror_registered_positions (
    id                BIGSERIAL PRIMARY KEY,
    whale             TEXT NOT NULL,
    us_market_slug    TEXT NOT NULL,             -- the book's own slug (mirror_books.us_market_slug)
    condition_id      TEXT NOT NULL,             -- the book's own condition
    asset             TEXT NOT NULL,             -- the token the position sits on: the book's long_asset, its other_asset on a short
    shares            DOUBLE PRECISION NOT NULL, -- signed like the ledger; negative is the short side
    side              TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    registered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_by     TEXT NOT NULL,             -- who dispatched it (the Actions actor)
    note              TEXT NOT NULL,             -- why: the owner's statement, in words
    source            TEXT NOT NULL              -- 'render-ops mirror-register book <id>'
);
CREATE INDEX IF NOT EXISTS mirror_registered_positions_slug_idx
    ON mirror_registered_positions (us_market_slug);
