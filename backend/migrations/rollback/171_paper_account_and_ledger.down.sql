-- down for 171. Refuses while the paper ledger holds anything beyond the one
-- INITIAL_FUNDING entry: the ledger is the paper account's only record, and
-- rolling it back would erase a bankroll that is defined to carry forward.
DO $$
BEGIN
    IF to_regclass('paper_ledger') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM paper_ledger
                    WHERE kind <> 'INITIAL_FUNDING') THEN
            RAISE EXCEPTION 'paper_ledger holds paper activity; 171 is not '
                            'rolled back over it';
        END IF;
    END IF;
    DROP TABLE IF EXISTS paper_ledger;
    DROP SEQUENCE IF EXISTS paper_ledger_seq;
    DROP TABLE IF EXISTS paper_settlements;
    DROP TABLE IF EXISTS paper_liquidity_consumed;
    DROP TABLE IF EXISTS paper_fills;
    DROP TABLE IF EXISTS paper_order_events;
    DROP TABLE IF EXISTS paper_orders;
    DROP TABLE IF EXISTS paper_book_observations;
    DROP TABLE IF EXISTS paper_session_health;
    DROP TABLE IF EXISTS paper_sessions;
    DROP TABLE IF EXISTS paper_control;
    DROP TABLE IF EXISTS paper_accounts;
    DROP FUNCTION IF EXISTS paper_ledger_before_insert();
    DROP FUNCTION IF EXISTS paper_ledger_after_insert();
    DROP FUNCTION IF EXISTS paper_session_frozen();
    DROP FUNCTION IF EXISTS paper_record_is_append_only();
END $$;
