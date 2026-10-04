-- Rollback of 223: removes the entry trigger on paper_orders and the paper
-- sleeve classification objects. No order, sizing, limit or capital path
-- reads them. REFUSES while a classification recorded AT ENTRY (or by the
-- backstop) exists: those rows are the durable record of each position's
-- economic purpose at the time it was opened. Backfill rows are re-derivable
-- from the paper records and do not block the rollback.
DO $$
BEGIN
    IF to_regclass('paper_sleeve_classifications') IS NOT NULL
       AND EXISTS (SELECT 1 FROM paper_sleeve_classifications
                    WHERE classified_by <> 'MIGRATION_223_BACKFILL') THEN
        RAISE EXCEPTION 'paper_sleeve_classifications holds entry-time '
                        'classifications; rollback refused';
    END IF;
END $$;
DROP TRIGGER IF EXISTS paper_orders_sleeve_at_entry_trg ON paper_orders;
DROP FUNCTION IF EXISTS paper_sleeve_classify_entry();
DROP VIEW IF EXISTS paper_sleeve_current_v;
DROP TABLE IF EXISTS paper_sleeve_classifications;
DROP FUNCTION IF EXISTS paper_sleeve_is_append_only();
DROP FUNCTION IF EXISTS paper_sleeve_basis(text, text);
DROP FUNCTION IF EXISTS paper_sleeve_of(text, text);
