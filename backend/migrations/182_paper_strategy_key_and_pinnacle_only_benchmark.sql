-- ══════════════════════════════════════════════════════════════════════
-- 182 · PAPER ONLY: A STRATEGY KEY ON PAPER DECISIONS, ORDERS AND HANDOFFS,
--       AND THE KILL-SWITCH ROW OF THE PINNACLE_ONLY_PAPER_BENCHMARK
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER-AUTHORIZED, PAPER ONLY. `agents.paper_benchmark` is an EXPERIMENTAL
-- execution benchmark on the existing fictional paper account: it decides on
-- the stored de-vigged Pinnacle probability alone and is NOT evidence of
-- qualified or proven profitability. It runs beside the original two-model
-- strategy (DEREK_ENTRY_POLICY_V2), never instead of it.
--
-- TWO STRATEGIES MAY DECIDE THE SAME VALUATION. Migration 172 allowed ONE
-- paper decision per (session, valuation). This adds `strategy` (default:
-- the existing two-model label, so every existing row and every row the
-- two-model path writes keeps it) and replaces the unique index with
-- (session_id, valuation_id, strategy): each strategy still records at most
-- one decision per valuation, and neither collides with the other.
--
-- A POSITION NEVER SWITCHES POLICY. `paper_orders.strategy` is set from the
-- entry decision and carried by Xavier onto every management order of the
-- group; `paper_fills.strategy` is its order's, so a position (derived from
-- fills) carries it; `paper_handoffs.strategy` and
-- `paper_xavier_reviews.strategy` are the group's.
--
-- EVERY KEY IS STRATEGY-SPECIFIC. A benchmark decision id ('paperbench:'),
-- group ('paperbenchgrp:'), entry idempotency key and order id, fill keys
-- (order id + observation + level), position key (group), handoff key
-- (group) and Xavier order keys (group / position) are all derived from the
-- strategy-specific decision id; the two-model strategy keeps its own
-- ('paperdec:' / 'papergrp:'). Neither can overwrite or suppress the other.
--
-- ONE CASH LEDGER. No per-strategy bankroll and no second funding: both
-- strategies reserve against the same paper_ledger under the same account
-- row lock (migration 171), so available cash is never committed twice.
--
-- NOTHING ELSE CHANGES: no account, session, funding, ledger kind, rail or
-- funded table is touched. Adding a column with a constant default fires no
-- row trigger, so the append-only triggers of migrations 171/172 are intact.
--
-- THE SWITCH. The benchmark runs only when the process environment sets
-- PAPER_BENCHMARK in (on, 1, true, yes) AND this row is enabled (AND the paper
-- session itself is enabled). The row is inserted ENABLED so the environment
-- flag is the single operator switch (default OFF: unset); the row is the
-- kill switch:
--     UPDATE paper_control SET enabled = FALSE, updated_by = '<who>',
--            why = '<why>', updated_at = now()
--      WHERE control_key = 'PINNACLE_ONLY_PAPER_BENCHMARK';

-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction together with its schema_migrations row (as 181 does).

ALTER TABLE paper_decisions
    ADD COLUMN IF NOT EXISTS strategy text NOT NULL
        DEFAULT 'DEREK_ENTRY_POLICY_V2';
ALTER TABLE paper_orders
    ADD COLUMN IF NOT EXISTS strategy text NOT NULL
        DEFAULT 'DEREK_ENTRY_POLICY_V2';
ALTER TABLE paper_handoffs
    ADD COLUMN IF NOT EXISTS strategy text NOT NULL
        DEFAULT 'DEREK_ENTRY_POLICY_V2';
ALTER TABLE paper_fills
    ADD COLUMN IF NOT EXISTS strategy text NOT NULL
        DEFAULT 'DEREK_ENTRY_POLICY_V2';
ALTER TABLE paper_xavier_reviews
    ADD COLUMN IF NOT EXISTS strategy text NOT NULL
        DEFAULT 'DEREK_ENTRY_POLICY_V2';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'paper_decisions_strategy_ck') THEN
        ALTER TABLE paper_decisions ADD CONSTRAINT paper_decisions_strategy_ck
            CHECK (strategy IN ('DEREK_ENTRY_POLICY_V2',
                                'PINNACLE_ONLY_PAPER_BENCHMARK'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'paper_orders_strategy_ck') THEN
        ALTER TABLE paper_orders ADD CONSTRAINT paper_orders_strategy_ck
            CHECK (strategy IN ('DEREK_ENTRY_POLICY_V2',
                                'PINNACLE_ONLY_PAPER_BENCHMARK'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'paper_handoffs_strategy_ck') THEN
        ALTER TABLE paper_handoffs ADD CONSTRAINT paper_handoffs_strategy_ck
            CHECK (strategy IN ('DEREK_ENTRY_POLICY_V2',
                                'PINNACLE_ONLY_PAPER_BENCHMARK'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'paper_fills_strategy_ck') THEN
        ALTER TABLE paper_fills ADD CONSTRAINT paper_fills_strategy_ck
            CHECK (strategy IN ('DEREK_ENTRY_POLICY_V2',
                                'PINNACLE_ONLY_PAPER_BENCHMARK'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'paper_xavier_reviews_strategy_ck') THEN
        ALTER TABLE paper_xavier_reviews
            ADD CONSTRAINT paper_xavier_reviews_strategy_ck
            CHECK (strategy IN ('DEREK_ENTRY_POLICY_V2',
                                'PINNACLE_ONLY_PAPER_BENCHMARK'));
    END IF;
END $$;

DROP INDEX IF EXISTS paper_decisions_one_per_valuation_idx;
CREATE UNIQUE INDEX IF NOT EXISTS paper_decisions_one_per_valuation_strategy_idx
    ON paper_decisions (session_id, valuation_id, strategy)
    WHERE valuation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS paper_decisions_strategy_idx
    ON paper_decisions (strategy, decided_at);
CREATE INDEX IF NOT EXISTS paper_orders_strategy_idx
    ON paper_orders (strategy, created_at);

INSERT INTO paper_control (control_key, enabled, why, updated_by)
VALUES ('PINNACLE_ONLY_PAPER_BENCHMARK', TRUE,
        'kill switch, inserted enabled at migration 182: the benchmark also '
        'needs PAPER_BENCHMARK=on in the process environment (default off) '
        'and the paper session enabled. PAPER ONLY; experimental execution, '
        'not evidence of qualified or proven profitability',
        'migration 182')
ON CONFLICT DO NOTHING;

-- ── THE PER-STRATEGY ENTRY SWITCH OF THE TWO-MODEL STRATEGY: OFF. It keeps
-- deciding and recording every valuation (evidence), but a decision that
-- would ENTER is recorded REFUSE / STRATEGY_ENTRIES_DISABLED and places no
-- paper order while this row is off (absent = off). Only the benchmark is
-- enabled for NEW paper entries. Positions already held keep their policy.
INSERT INTO paper_control (control_key, enabled, why, updated_by)
VALUES ('PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2', FALSE,
        'owner: only the PINNACLE_ONLY_PAPER_BENCHMARK may open new paper '
        'entries; the two-model strategy keeps recording its decisions',
        'migration 182')
ON CONFLICT DO NOTHING;

COMMENT ON COLUMN paper_decisions.strategy IS
    'DEREK_ENTRY_POLICY_V2 (the original two-model paper strategy) or '
    'PINNACLE_ONLY_PAPER_BENCHMARK (experimental execution on the de-vigged '
    'Pinnacle probability alone; not evidence of qualified or proven '
    'profitability). One decision per (session, valuation, strategy).';

