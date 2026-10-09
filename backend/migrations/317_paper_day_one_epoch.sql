-- Additive only: no existing ledger, order, fill or model row is rewritten.
CREATE TABLE IF NOT EXISTS paper_account_epochs (
 epoch_id text PRIMARY KEY,
 account_id text UNIQUE NOT NULL REFERENCES paper_accounts,
 previous_account_id text NOT NULL REFERENCES paper_accounts,
 opened_at timestamptz NOT NULL,
 release_sha text NOT NULL CHECK (release_sha ~ '^[0-9a-f]{40}$'),
 acceptance_digest text NOT NULL CHECK (acceptance_digest ~ '^[0-9a-f]{64}$'),
 opening_equity_usd numeric(18,6) NOT NULL CHECK (opening_equity_usd=500000),
 opening_receipt jsonb NOT NULL,
 historical_receipt jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_epoch_control (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
 account_id text NOT NULL REFERENCES paper_accounts,
 generation bigint NOT NULL DEFAULT 0
);
INSERT INTO paper_epoch_control(singleton,account_id)
 VALUES(true,'paper_acct_main') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS paper_epoch_events (
 event_id bigserial PRIMARY KEY,
 request_id text UNIQUE NOT NULL,
 kind text NOT NULL CHECK(kind IN ('ACTIVATE','ROLLBACK')),
 epoch_id text NOT NULL REFERENCES paper_account_epochs,
 from_account_id text NOT NULL REFERENCES paper_accounts,
 to_account_id text NOT NULL REFERENCES paper_accounts,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 detail jsonb NOT NULL
);
CREATE OR REPLACE FUNCTION paper_epoch_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'PAPER_EPOCH_HISTORY_IS_APPEND_ONLY'; END $$;
DROP TRIGGER IF EXISTS paper_epoch_immutable_trg ON paper_account_epochs;
CREATE TRIGGER paper_epoch_immutable_trg BEFORE UPDATE OR DELETE ON paper_account_epochs
 FOR EACH ROW EXECUTE FUNCTION paper_epoch_immutable();
DROP TRIGGER IF EXISTS paper_epoch_event_immutable_trg ON paper_epoch_events;
CREATE TRIGGER paper_epoch_event_immutable_trg BEFORE UPDATE OR DELETE ON paper_epoch_events
 FOR EACH ROW EXECUTE FUNCTION paper_epoch_immutable();

-- Serialize order admission with account switching. An in-flight old session
-- may finish its historical audit, but cannot submit into an archived account.
CREATE OR REPLACE FUNCTION paper_epoch_order_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selected text; owner text; opened timestamptz;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM paper_account_epochs) THEN RETURN NEW; END IF;
 SELECT account_id INTO selected FROM paper_epoch_control WHERE singleton FOR SHARE;
 IF (NEW.account_id='paper_acct_main' OR EXISTS(
     SELECT 1 FROM paper_account_epochs WHERE account_id=NEW.account_id))
     AND NEW.account_id<>selected THEN
   RAISE EXCEPTION 'PAPER_EPOCH_ACCOUNT_IS_NOT_SELECTED';
 END IF;
 SELECT account_id INTO owner FROM paper_sessions WHERE session_id=NEW.session_id;
 IF owner IS DISTINCT FROM NEW.account_id THEN
   RAISE EXCEPTION 'PAPER_ORDER_SESSION_ACCOUNT_MISMATCH';
 END IF;
 IF EXISTS(SELECT 1 FROM paper_orders WHERE group_id=NEW.group_id AND account_id<>NEW.account_id) THEN
   RAISE EXCEPTION 'PAPER_GROUP_ACCOUNT_MISMATCH';
 END IF;
 SELECT opened_at INTO opened FROM paper_account_epochs WHERE account_id=NEW.account_id;
 IF opened IS NOT NULL AND NEW.decided_at<opened THEN
   RAISE EXCEPTION 'PAPER_ORDER_PREDATES_EPOCH';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS paper_epoch_order_owner_trg ON paper_orders;
CREATE TRIGGER paper_epoch_order_owner_trg BEFORE INSERT ON paper_orders
 FOR EACH ROW EXECUTE FUNCTION paper_epoch_order_owner();

CREATE OR REPLACE FUNCTION paper_epoch_fill_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner text; session text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM paper_account_epochs) THEN RETURN NEW; END IF;
 SELECT account_id,session_id INTO owner,session FROM paper_orders WHERE order_id=NEW.order_id;
 IF owner IS DISTINCT FROM NEW.account_id OR session IS DISTINCT FROM NEW.session_id THEN
   RAISE EXCEPTION 'PAPER_FILL_ORDER_ACCOUNT_MISMATCH';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS paper_epoch_fill_owner_trg ON paper_fills;
CREATE TRIGGER paper_epoch_fill_owner_trg BEFORE INSERT ON paper_fills
 FOR EACH ROW EXECUTE FUNCTION paper_epoch_fill_owner();

CREATE OR REPLACE FUNCTION paper_epoch_ledger_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM paper_account_epochs) THEN RETURN NEW; END IF;
 IF NEW.order_id IS NOT NULL THEN
   SELECT account_id INTO owner FROM paper_orders WHERE order_id=NEW.order_id;
   IF owner IS DISTINCT FROM NEW.account_id THEN RAISE EXCEPTION 'PAPER_LEDGER_ORDER_ACCOUNT_MISMATCH'; END IF;
 END IF;
 IF NEW.fill_id IS NOT NULL THEN
   SELECT account_id INTO owner FROM paper_fills WHERE fill_id=NEW.fill_id;
   IF owner IS DISTINCT FROM NEW.account_id THEN RAISE EXCEPTION 'PAPER_LEDGER_FILL_ACCOUNT_MISMATCH'; END IF;
 END IF;
 IF NEW.corrects_seq IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM paper_ledger WHERE account_id=NEW.account_id AND seq=NEW.corrects_seq) THEN
     RAISE EXCEPTION 'PAPER_LEDGER_CORRECTION_ACCOUNT_MISMATCH';
   END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS paper_epoch_ledger_owner_trg ON paper_ledger;
CREATE TRIGGER paper_epoch_ledger_owner_trg BEFORE INSERT ON paper_ledger
 FOR EACH ROW EXECUTE FUNCTION paper_epoch_ledger_owner();

CREATE OR REPLACE FUNCTION paper_epoch_session_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner text; opened timestamptz; source_at timestamptz;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM paper_account_epochs) THEN RETURN NEW; END IF;
 SELECT account_id INTO owner FROM paper_sessions WHERE session_id=NEW.session_id;
 IF owner IS DISTINCT FROM NEW.account_id THEN RAISE EXCEPTION 'PAPER_AGENT_SESSION_ACCOUNT_MISMATCH'; END IF;
 IF TG_TABLE_NAME='paper_decisions' THEN
   SELECT opened_at INTO opened FROM paper_account_epochs WHERE account_id=NEW.account_id;
   IF opened IS NOT NULL AND NEW.valuation_id IS NOT NULL THEN
     SELECT received_at INTO source_at FROM external_valuations WHERE id=NEW.valuation_id;
     IF source_at IS NULL OR source_at<opened THEN RAISE EXCEPTION 'PAPER_VALUATION_PREDATES_EPOCH'; END IF;
   END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS paper_epoch_decision_owner_trg ON paper_decisions;
CREATE TRIGGER paper_epoch_decision_owner_trg BEFORE INSERT ON paper_decisions FOR EACH ROW EXECUTE FUNCTION paper_epoch_session_owner();
DROP TRIGGER IF EXISTS paper_epoch_review_owner_trg ON paper_xavier_reviews;
CREATE TRIGGER paper_epoch_review_owner_trg BEFORE INSERT ON paper_xavier_reviews FOR EACH ROW EXECUTE FUNCTION paper_epoch_session_owner();
DROP TRIGGER IF EXISTS paper_epoch_audrey_owner_trg ON paper_audrey_reports;
CREATE TRIGGER paper_epoch_audrey_owner_trg BEFORE INSERT ON paper_audrey_reports FOR EACH ROW EXECUTE FUNCTION paper_epoch_session_owner();
DROP TRIGGER IF EXISTS paper_epoch_equity_owner_trg ON paper_equity_snapshots;
CREATE TRIGGER paper_epoch_equity_owner_trg BEFORE INSERT ON paper_equity_snapshots FOR EACH ROW EXECUTE FUNCTION paper_epoch_session_owner();
