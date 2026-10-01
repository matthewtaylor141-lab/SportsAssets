-- THE COMPLETED-GAME PAPER POLICY: ITS OWN STRATEGY KEY, AND A VENUE-PRICE
-- SETTLEMENT OUTCOME. PAPER ONLY; real-money execution stays disabled.
--
-- PINNACLE_COMPLETED_GAME_PAPER (version PINNACLE_COMPLETED_GAME_PAPER_V1)
-- is an owner-authorized EXPERIMENTAL paper policy. It requires an exact
-- match on fixture, participant, selected outcome, market, line and the
-- grading period of an ORDINARILY COMPLETED game; it records the
-- postponement / abandonment / suspension terms as DISCLOSED RESEARCH RISKS
-- and never as settlement compatibility. Its economics are CONDITIONAL on
-- ordinary completion and labelled so; exceptional-settlement payoffs are
-- shown separately with their probabilities UNMEASURED.
--
-- The strict PINNACLE_ONLY_PAPER_BENCHMARK and the two-model strategy, and
-- every record they wrote, are unchanged. A record's strategy never
-- switches: this widens the five strategy CHECKs to admit the new key.
--
-- SETTLED_AT_VENUE_PRICE: a position settled at the venue's OWN published
-- settlement price (e.g. "the last fair market price" when a game is not
-- completed), credited per contract at that price -- never an assumed
-- purchase-price refund. A position whose payout the venue has not published
-- stays open and pending.

DO $$
DECLARE t text;
BEGIN
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

ALTER TABLE paper_settlements DROP CONSTRAINT IF EXISTS
    paper_settlements_outcome_ck;
ALTER TABLE paper_settlements ADD CONSTRAINT paper_settlements_outcome_ck
    CHECK (outcome IN ('WON', 'LOST', 'VOID_REFUND',
                       'SETTLED_AT_VENUE_PRICE'));

INSERT INTO paper_control (control_key, enabled, why, updated_by)
VALUES ('PINNACLE_COMPLETED_GAME_PAPER', TRUE,
        'kill switch, inserted enabled at migration 184: the completed-game '
        'paper policy also needs PAPER_BENCHMARK=on in the process '
        'environment and the paper session enabled. PAPER ONLY; '
        'conditional, experimental economics -- not risk-adjusted, not '
        'proven positive EV, not a qualification for real money',
        'migration 184')
ON CONFLICT DO NOTHING;

COMMENT ON COLUMN paper_decisions.strategy IS
    'DEREK_ENTRY_POLICY_V2 (the original two-model paper strategy), '
    'PINNACLE_ONLY_PAPER_BENCHMARK (strict: every settlement condition must '
    'be compatible) or PINNACLE_COMPLETED_GAME_PAPER (experimental: ordinary '
    'completed-game terms must match; exceptional terms are disclosed '
    'research risks). One decision per (session, valuation, strategy).';
