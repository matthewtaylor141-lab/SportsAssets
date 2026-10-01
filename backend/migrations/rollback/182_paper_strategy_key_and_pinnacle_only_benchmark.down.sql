-- down for 182. Refuses while any PINNACLE_ONLY_PAPER_BENCHMARK record
-- exists (they are the benchmark's audit trail, and without the strategy
-- key a benchmark decision would read as a two-model one). Otherwise it
-- restores migration 172's one-decision-per-valuation index, drops the
-- strategy columns and the benchmark's control row.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM paper_decisions
                WHERE strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK')
       OR EXISTS (SELECT 1 FROM paper_orders
                   WHERE strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK')
       OR EXISTS (SELECT 1 FROM paper_handoffs
                   WHERE strategy = 'PINNACLE_ONLY_PAPER_BENCHMARK') THEN
        RAISE EXCEPTION 'PINNACLE_ONLY_PAPER_BENCHMARK records exist; 182 is '
                        'not rolled back over them';
    END IF;
    DROP INDEX IF EXISTS paper_decisions_one_per_valuation_strategy_idx;
    DROP INDEX IF EXISTS paper_decisions_strategy_idx;
    DROP INDEX IF EXISTS paper_orders_strategy_idx;
    CREATE UNIQUE INDEX IF NOT EXISTS paper_decisions_one_per_valuation_idx
        ON paper_decisions (session_id, valuation_id)
        WHERE valuation_id IS NOT NULL;
    ALTER TABLE paper_decisions DROP CONSTRAINT IF EXISTS
        paper_decisions_strategy_ck;
    ALTER TABLE paper_orders DROP CONSTRAINT IF EXISTS
        paper_orders_strategy_ck;
    ALTER TABLE paper_handoffs DROP CONSTRAINT IF EXISTS
        paper_handoffs_strategy_ck;
    ALTER TABLE paper_decisions DROP COLUMN IF EXISTS strategy;
    ALTER TABLE paper_orders DROP COLUMN IF EXISTS strategy;
    ALTER TABLE paper_handoffs DROP COLUMN IF EXISTS strategy;
    DELETE FROM paper_control
     WHERE control_key = 'PINNACLE_ONLY_PAPER_BENCHMARK';
END $$;
