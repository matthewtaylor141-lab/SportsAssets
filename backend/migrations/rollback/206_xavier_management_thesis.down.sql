-- Rollback of 206: drops Xavier's management record (theses, assessments,
-- value-add). These are records only -- no order path reads them -- so the
-- reviews keep running without them (every write is guarded and skipped
-- when the tables are absent). The immutability triggers go with the
-- tables; the shared trigger function is dropped last.
DROP TABLE IF EXISTS xavier_value_add;
DROP TABLE IF EXISTS xavier_management_assessments;
DROP TABLE IF EXISTS xavier_entry_theses;
DROP FUNCTION IF EXISTS xavier_management_record_is_immutable();
