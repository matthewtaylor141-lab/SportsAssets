-- down for 184. Refuses while any PINNACLE_COMPLETED_GAME_PAPER record or
-- SETTLED_AT_VENUE_PRICE settlement exists (they are its audit trail).
DO $$
DECLARE t text;
BEGIN
    IF EXISTS (SELECT 1 FROM paper_decisions
                WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER')
       OR EXISTS (SELECT 1 FROM paper_orders
                   WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER')
       OR EXISTS (SELECT 1 FROM paper_settlements
                   WHERE outcome = 'SETTLED_AT_VENUE_PRICE') THEN
        RAISE EXCEPTION 'PINNACLE_COMPLETED_GAME_PAPER records exist; 184 is '
                        'not rolled back over them';
    END IF;
    FOREACH t IN ARRAY ARRAY['paper_decisions', 'paper_orders',
                             'paper_handoffs', 'paper_fills',
                             'paper_xavier_reviews'] LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I',
                       t, t || '_strategy_ck');
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I CHECK (strategy IN '
            '(''DEREK_ENTRY_POLICY_V2'', ''PINNACLE_ONLY_PAPER_BENCHMARK''))',
            t, t || '_strategy_ck');
    END LOOP;
    ALTER TABLE paper_settlements DROP CONSTRAINT IF EXISTS
        paper_settlements_outcome_ck;
    ALTER TABLE paper_settlements ADD CONSTRAINT paper_settlements_outcome_ck
        CHECK (outcome IN ('WON', 'LOST', 'VOID_REFUND'));
    DELETE FROM paper_control
     WHERE control_key = 'PINNACLE_COMPLETED_GAME_PAPER';
END $$;
