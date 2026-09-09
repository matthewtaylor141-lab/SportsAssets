-- 060: MIRROR FILL ANSWERS (T2, 2026-09-08; FILL program lane 4; owner
-- "I want to know when he makes money we make money. This needs to be
-- right"). The fills-missed preset at 17:37Z (hourly_1737 rows
-- 1659-1660) read 3,234 fills of his on our markets in 24 h, of which
-- 1,105 / $517,203.44 were `unseen` -- no plan held the fill -- and
-- $397,223.87 of that (76.8%) fell on markets with a LIVE or closing
-- book: the tick DID name those fills, on the plan's `his_fills_seen`
-- list, and the list is bounded at HIS_FILLS_SEEN_MAX (20, E9's
-- contract) and rides the book's last plan, so on a 301-fill book (611,
-- post_fvv_1707 row 334; 118 `unseen`, hourly_1737 row 1687) the name
-- the tick gave a fill was gone twenty fills later and gone for good
-- at the book's close. Nothing durable said which order answered which
-- fill of his, or why none did.
--
-- ONE ROW PER FILL OF HIS THE MIRROR HELD, written once: `whale`, his
-- `condition_id`, the fill's id (`fill_id`: its trades row id, else its
-- stamp -- the plan's own key), its stamp (`fill_ts`), the ingest's
-- clock (`detected_at`), the tick's clock when the entry was first
-- named (`at`), the book, the order row the tick placed on the book
-- when it named the fill (`order_id`, NULL when none), the NAME the
-- tick gave it (`name`: the plan's reason key -- `rest_placed`,
-- `on_target`, `open_order_pending`, ...) and the tick's sequence
-- number (`tick`). UNIQUE (whale, fill_id) with ON CONFLICT DO NOTHING
-- at the write keeps the FIRST name, the file's rule for the plan's
-- list ("written once and never renamed"). Additive, re-runnable, no
-- DEFAULT on any column (the serial id's nextval is the plan's own
-- BIGSERIAL); NULL in `order_id` / `book_id` / `tick` / `fill_ts` /
-- `detected_at` means "not reached", never a figure. NO ORDER PATH
-- READS THIS TABLE: it is the record the fills-missed preset keys on
-- first (then the plan's list, then the 120 s order window) and the
-- fill-answers preset's health line. Read the 059 way: the worker
-- probes the table once per tick after the 050/057/058/059 probes and,
-- absent, queues nothing (heartbeat `fill_answers_absent`, logged once
-- per process); a write that fails or times out is counted
-- `fill_answer_write_failed` and its rows are kept for the next tick.
-- The workers never run migrations (the API's start.sh applies the
-- sorted glob on boot, best-effort).
--
-- NUMBERING. 060: 059 is landed; 048 and 051 stay reserved by
-- docs/mirror-to-a-tee-program.md:184-187 (migrate.py applies the
-- sorted glob, so the gaps are harmless).
CREATE TABLE IF NOT EXISTS mirror_fill_answers (
    id BIGSERIAL PRIMARY KEY,
    whale TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    fill_ts DOUBLE PRECISION NULL,
    detected_at DOUBLE PRECISION NULL,
    at DOUBLE PRECISION NOT NULL,
    book_id BIGINT NULL,
    order_id BIGINT NULL,
    name TEXT NOT NULL,
    tick BIGINT NULL,
    UNIQUE (whale, fill_id)
);
CREATE INDEX IF NOT EXISTS mirror_fill_answers_condition_ts ON mirror_fill_answers (condition_id, fill_ts);
