-- THE REASON, AS A COLUMN.
--
-- Owner directive 2026-09-19 21:2xZ §5/§11: management must eventually
-- see which refusals prevented the most trades. That question is only
-- answerable by GROUPING on the reason, and a reason buried inside a
-- JSONB provenance blob is not a column anybody groups on -- it is a
-- string somebody has to remember to dig out. The experimental lane
-- wrote its `why` into l2_reference; this gives it the same first-class
-- place the decision-grade ledger gives its blockers.
--
-- The blob keeps its own copy of what it already recorded. Nothing is
-- rewritten: the table is append-only, so the column is simply NULL on
-- the rows written before it existed, and that is honest -- it says
-- "this row predates the column", not "this row had no reason".

BEGIN;

ALTER TABLE bettor_experimental_decisions
    ADD COLUMN IF NOT EXISTS why TEXT;

CREATE INDEX IF NOT EXISTS bettor_exp_decisions_status_idx
    ON bettor_experimental_decisions (execution_status, action);

COMMIT;
