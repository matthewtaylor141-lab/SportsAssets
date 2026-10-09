-- Logical rollback preserves every account and financial row. This DDL
-- reversal is allowed only before activation has recorded any evidence.
DO $$ BEGIN
 IF to_regclass('paper_account_epochs') IS NOT NULL THEN
  IF EXISTS(SELECT 1 FROM paper_account_epochs) THEN
   RAISE EXCEPTION 'Refusing rollback 317: epoch evidence must be preserved';
  END IF;
 END IF;
 IF to_regclass('paper_epoch_events') IS NOT NULL THEN
  IF EXISTS(SELECT 1 FROM paper_epoch_events) THEN
   RAISE EXCEPTION 'Refusing rollback 317: epoch events must be preserved';
  END IF;
 END IF;
DROP TRIGGER IF EXISTS paper_epoch_order_owner_trg ON paper_orders;
DROP TRIGGER IF EXISTS paper_epoch_fill_owner_trg ON paper_fills;
DROP TRIGGER IF EXISTS paper_epoch_ledger_owner_trg ON paper_ledger;
DROP TRIGGER IF EXISTS paper_epoch_decision_owner_trg ON paper_decisions;
DROP TRIGGER IF EXISTS paper_epoch_review_owner_trg ON paper_xavier_reviews;
DROP TRIGGER IF EXISTS paper_epoch_audrey_owner_trg ON paper_audrey_reports;
DROP TRIGGER IF EXISTS paper_epoch_equity_owner_trg ON paper_equity_snapshots;
DROP TABLE IF EXISTS paper_epoch_events;
DROP TABLE IF EXISTS paper_epoch_control;
DROP TABLE IF EXISTS paper_account_epochs;
DROP FUNCTION IF EXISTS paper_epoch_order_owner();
DROP FUNCTION IF EXISTS paper_epoch_fill_owner();
DROP FUNCTION IF EXISTS paper_epoch_ledger_owner();
DROP FUNCTION IF EXISTS paper_epoch_session_owner();
DROP FUNCTION IF EXISTS paper_epoch_immutable();

END $$;
