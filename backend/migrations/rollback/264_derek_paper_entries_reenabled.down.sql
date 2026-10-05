-- Rollback of 264: Derek's PAPER entries back OFF, with a reason that is
-- true (not migration 182's stale one). Touches only the row 264 wrote, and
-- only while it still carries 264's own state; a later decision is kept.
-- Decisions, orders, fills and positions recorded while it was on are
-- history and are never touched. Re-applying 264 after this rollback leaves
-- the row OFF: the rollback is a later decision, and 264 flips only a row
-- still carrying migration 182's state (tests/test_derek_paper_entries_
-- reenabled.py pins it); switching entries on again is an explicit
-- paper_control decision.
UPDATE paper_control
   SET enabled = FALSE,
       why = 'rollback of migration 264: Derek''s PAPER entries off again; '
             'the strategy keeps recording its decisions',
       updated_by = 'rollback 264',
       updated_at = now()
 WHERE control_key = 'PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2'
   AND enabled = TRUE
   AND updated_by = 'migration 264';
