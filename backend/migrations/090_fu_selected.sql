-- FU_SELECTED: the batch the budget actually admitted, kept apart from
-- FU_DUE, which now records TRUE outstanding demand.
--
-- Before this, fu_due was len(mids_due(limit=budget)) -- counted after
-- the limit, so it could never exceed the budget and FU_DUE ==
-- FU_ATTEMPTED was a tautology. Demand and selection are two different
-- quantities and now have two different columns.
BEGIN;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS fu_selected INTEGER NOT NULL DEFAULT 0;
COMMIT;
