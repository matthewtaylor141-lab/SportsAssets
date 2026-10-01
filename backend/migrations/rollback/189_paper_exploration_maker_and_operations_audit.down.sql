-- down for 189. Refuses while any record of the two strategies, any
-- evaluation attempt or any recommendation exists: they are the audit trail
-- of paper decisions, orders and the operational audit.
DO $$
DECLARE t text;
BEGIN
    IF EXISTS (SELECT 1 FROM paper_decisions WHERE strategy IN (
                 'PINNACLE_COMPLETED_GAME_MAKER_PAPER',
                 'PINNACLE_EXPLORATION_PAPER'))
       OR EXISTS (SELECT 1 FROM paper_orders WHERE strategy IN (
                 'PINNACLE_COMPLETED_GAME_MAKER_PAPER',
                 'PINNACLE_EXPLORATION_PAPER'))
       OR EXISTS (SELECT 1 FROM paper_evaluation_attempts)
       OR EXISTS (SELECT 1 FROM paper_recommendations) THEN
        RAISE EXCEPTION '189 has records (maker / exploration decisions or '
                        'orders, evaluation attempts or recommendations); '
                        'not rolled back over them';
    END IF;
    DROP TABLE IF EXISTS paper_recommendation_events;
    DROP TABLE IF EXISTS paper_recommendations;
    DROP TABLE IF EXISTS paper_evaluation_attempts;
    DELETE FROM paper_control WHERE control_key IN (
        'PINNACLE_COMPLETED_GAME_MAKER_PAPER', 'PINNACLE_EXPLORATION_PAPER');
    FOREACH t IN ARRAY ARRAY['paper_decisions', 'paper_orders',
                             'paper_handoffs', 'paper_fills',
                             'paper_xavier_reviews'] LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I',
                       t, t || '_strategy_ck');
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I CHECK (strategy IN '
            '(''DEREK_ENTRY_POLICY_V2'', ''PINNACLE_ONLY_PAPER_BENCHMARK'', '
            '''PINNACLE_COMPLETED_GAME_PAPER''))', t, t || '_strategy_ck');
    END LOOP;
END $$;
