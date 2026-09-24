-- 105 · ONE ROW PER PROVIDER OBSERVATION
--
-- THE DEFECT. `external_valuations` had no uniqueness at all, and the
-- recurring loop re-reads the same slate every cycle. A quote that has not
-- moved between cycles was therefore written again, so the refusal census
-- and any future return series would count one observation as many. A test
-- drove two cycles over an unchanged quote and the row count went 1 -> 2.
--
-- WHAT MAKES AN OBSERVATION DISTINCT. The provider's own `last_update`
-- for the market, together with the contract it was valued against:
--
--     (experiment_id, condition_id, contract_selection, event_key,
--      observed_at)
--
-- `observed_at` is the BOOK's clock, not ours. That is the point: a new
-- cycle that re-reads an unchanged price carries the same observed_at and
-- collapses onto the existing row, while a genuinely new price carries a
-- new one and gets its own. Keying on our receipt time instead would have
-- made every cycle "new data" by definition, which is the bug with extra
-- steps.
--
-- COALESCE, NOT BARE COLUMNS. In Postgres two NULLs are distinct for a
-- unique index, so a refused row with no timestamp -- exactly the rows
-- this experiment produces most of -- would duplicate freely. `NULLS NOT
-- DISTINCT` would express this directly but is Postgres 15+, and the
-- production version is not something to assume, so the index is written
-- as an expression that behaves the same on any version.

-- DEDUPLICATE BEFORE INDEXING. The index cannot be created while
-- duplicates exist, and they do exist wherever the loop has already run
-- against an unchanged quote. The EARLIEST row for each observation is
-- kept, because that is the one whose decision was actually made first;
-- keeping the newest would rewrite history to the last cycle's copy.
--
-- Rows that carried an outcome are never the ones deleted: `outcome_known`
-- sorts first, so a settled copy always wins over an unsettled duplicate
-- and no joined outcome is lost.
DELETE FROM external_valuations ev
 WHERE ev.id <> (
    SELECT keep.id FROM external_valuations keep
     WHERE keep.experiment_id = ev.experiment_id
       AND coalesce(keep.condition_id, '') = coalesce(ev.condition_id, '')
       AND keep.contract_selection = ev.contract_selection
       AND coalesce(keep.event_key, '') = coalesce(ev.event_key, '')
       AND coalesce(keep.observed_at, '-infinity'::timestamptz)
           = coalesce(ev.observed_at, '-infinity'::timestamptz)
     ORDER BY keep.outcome_known DESC, keep.decided_at ASC, keep.id ASC
     LIMIT 1);

CREATE UNIQUE INDEX IF NOT EXISTS external_valuations_one_per_observation
    ON external_valuations (
        experiment_id,
        coalesce(condition_id, ''),
        contract_selection,
        coalesce(event_key, ''),
        coalesce(observed_at, '-infinity'::timestamptz));

COMMENT ON INDEX external_valuations_one_per_observation IS
    'One row per provider observation of one contract. Re-reading an '
    'unchanged quote on a later cycle must not add a row; a moved quote '
    'carries a new observed_at and gets its own.';
