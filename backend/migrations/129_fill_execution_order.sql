-- 129: THE VENUE'S OWN EXECUTION INSTANT, AND WHOSE ORDER WE ARE USING.
--
-- WHY THIS COLUMN EXISTS. The published taker fee is CUMULATIVE ACROSS AN
-- ORDER'S FILLS: each fill is charged its banker's-rounded fee adjusted so the
-- total never exceeds the banker's rounding of the cumulative exact fee. So the
-- per-fill amounts depend on the SEQUENCE, and the sequence has to be the
-- venue's, because the venue is the one applying the cap.
--
-- `bettor_funded_fills.at` IS OUR CLOCK. It is written from `now` at ingest, and
-- `recorded_at` defaults to `now()`. Ordering by either gives OUR ARRIVAL ORDER,
-- and arrival order is not automatically venue execution order: two executions
-- can be delivered out of order, redelivered, or arrive together after a
-- reconnect. Computing the cap on arrival order then attributes the adjustment to
-- the wrong fill -- the TOTAL is unaffected, and a per-fill reconciliation
-- against the venue's own statement disagrees on every multi-fill order.
--
-- I HAD ORDERED BY `at, fill_id` AND CALLED IT DETERMINISTIC. It is
-- deterministic, which is not the same as correct: a stable wrong order is still
-- a wrong order, and stability is only what stops the number CHANGING between
-- runs.
--
-- SO: the venue's instant is stored when the venue states one, and the ordering
-- BASIS is stored alongside it so a row can say which order produced its
-- expected fee. A fee computed on arrival order is not silently presented as one
-- computed on execution order.

BEGIN;

ALTER TABLE bettor_funded_fills
    -- The venue's own execution instant, parsed from the execution report.
    -- NULL means the venue did not state one, which is NOT the same as "it
    -- executed when we received it".
    ADD COLUMN IF NOT EXISTS venue_executed_at timestamptz,
    -- The venue's own sequence number for the execution, where it provides one.
    -- Preferred over any timestamp: a sequence cannot tie.
    ADD COLUMN IF NOT EXISTS venue_sequence bigint,
    -- Which order the cumulative fee for this row was computed on. One of
    -- VENUE_SEQUENCE, VENUE_EXECUTED_AT, ARRIVAL_ORDER.
    ADD COLUMN IF NOT EXISTS fee_order_basis text;

COMMENT ON COLUMN bettor_funded_fills.venue_executed_at IS
    'The venue''s own execution instant. NULL means the venue stated none, '
    'which is not the same as "it executed when we received it".';

COMMENT ON COLUMN bettor_funded_fills.venue_sequence IS
    'The venue''s own execution sequence, where provided. Preferred over any '
    'timestamp for ordering, because a sequence cannot tie.';

COMMENT ON COLUMN bettor_funded_fills.fee_order_basis IS
    'Which order the cumulative taker fee for this row was computed on: '
    'VENUE_SEQUENCE, VENUE_EXECUTED_AT or ARRIVAL_ORDER. ARRIVAL_ORDER means '
    'the per-fill attribution may disagree with the venue''s own statement '
    'even where the order total agrees.';

-- ORDERING THE WAY THE READ DOES, so the sequence read is an index scan rather
-- than a sort over every fill of every order.
CREATE INDEX IF NOT EXISTS bettor_funded_fills_exec_order_idx
    ON bettor_funded_fills (intent_id, direction, venue_sequence,
                            venue_executed_at, at, fill_id);

COMMIT;
