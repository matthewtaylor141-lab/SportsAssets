-- ══════════════════════════════════════════════════════════════════════
-- 368 · EVERY CANDIDATE ROW SAYS WHERE ITS VENUE READ SPENT ITS TIME, AND
--       WHICH SOURCE SERVED ITS BOOK (RC6.3c COLL-1 phase a)
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP. `ext_candidate_outcomes` (137, 143) holds one row per provider
-- event per cycle with the age of its price split into the provider's lag and
-- OUR processing (143). In the approved-judge packet's hour (08:54-09:54Z
-- 2026-10-10) QUOTE_STALE_ON_ARRIVAL was the largest SOFTWARE first loss (38
-- of 84 events); 83 of those events' cycles were quotes the provider delivered
-- INSIDE the 30 s rule that our serial, paced venue reads then carried past it
-- (our_processing_s 10.5-29 s at queue positions 6-41). The row said how long
-- we took and not WHY: whether the time was the pacer's queue and gap, the
-- hard not-before hold a 429 set, the escalating 429 cooldown, or the venue's
-- own answer time -- and that is the question the next decision (a bounded
-- pipeline inside the pacer's rate, or not) turns on.
--
-- WHAT THIS ADDS, per row, measured at the transport by the logical read
-- (venue_request_gate.READ_TELEMETRY_RULE) and summed over its requests:
--
--   queue_wait_s     seconds in the pacer's queue and gap (venue_pace.pace)
--   gate_wait_s      seconds slept out in the hard not-before hold
--   cooldown_wait_s  seconds waited out in the escalating 429 cooldown
--   http_s           seconds from dispatch to the venue's answer (or failure)
--   book_source      'PMX_GRPC' or 'REST': which source served the book
--                    (ext_pinnacle_loop.PMX_BOOK_BEFORE_REST_RULE)
--
-- NULL means no venue read was made for the row (refused before the read, or
-- skipped without one), never zero; a PMX book made no request and records
-- 0.0 waits with its source.
--
-- ADDITIVE AND NULLABLE. Five nullable columns, no constraint, trigger, index
-- or default on any existing object; no existing row or column changes. The
-- writer (`_persist_candidate_outcomes`) checks for the columns and falls back
-- to the 143 (or 137) row -- saying so on its result -- on a database where
-- this is not applied, so the previous release runs unchanged on this schema
-- and this code runs unchanged on the previous one.

BEGIN;

ALTER TABLE ext_candidate_outcomes
    ADD COLUMN IF NOT EXISTS queue_wait_s    double precision,
    ADD COLUMN IF NOT EXISTS gate_wait_s     double precision,
    ADD COLUMN IF NOT EXISTS cooldown_wait_s double precision,
    ADD COLUMN IF NOT EXISTS http_s          double precision,
    ADD COLUMN IF NOT EXISTS book_source     text;

COMMIT;
