-- ══════════════════════════════════════════════════════════════════════
-- 311 · PAPER PROFITABILITY STACK: LEARNED MANAGEMENT / EXIT COST AND THE
--       COUNTERFACTUAL VARIANT LEDGER (PAPER ONLY, NO CAPITAL AUTHORITY,
--       APPEND-ONLY)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS RECORDS (bettor_paper_profitability_bind /
-- bettor_paper_profitability_stack):
--
--   paper_profitability_models        gains the kind MANAGEMENT: the learned
--       early-exit rate and exit cost per contract of each strategy (from its
--       own closed positions and SELL fills), charged by the entry bind as an
--       expected management / exit cost. Nothing else about the table changes.
--   paper_counterfactual_variants     for every evaluation the bind recorded
--       with executable evidence (ENTER and CASH -- refused trades included):
--       the counterfactual variants of the same decision valued on the same
--       all-in economics -- AS_BOUND, POLICY_SIZE (taker, unshrunk),
--       HALF_SIZE, MAKER (resting one tick inside at the learned maker fill
--       probability and markout), HOLD_TO_SETTLEMENT and EARLY_EXIT. Never an
--       order; expected values only at the decision.
--   paper_counterfactual_variant_outcomes   one row per variant once its
--       contract settled: the outcome and the counterfactual P&L
--       (NOT_REALIZED_PNL), never summed with PAPER P&L.
--
-- AUTHORITY. Every row is PAPER / PAPER_ONLY_NO_CAPITAL_AUTHORITY (CHECK).
-- APPEND-ONLY. UPDATE, DELETE and TRUNCATE are refused by trigger (the
-- function migration 309 declared). No BEGIN/COMMIT of its own. Idempotent.

ALTER TABLE paper_profitability_models
    DROP CONSTRAINT IF EXISTS paper_prof_models_kind_ck;
ALTER TABLE paper_profitability_models
    ADD CONSTRAINT paper_prof_models_kind_ck CHECK (kind IN (
        'CALIBRATION', 'EXECUTION', 'RESIDUAL', 'MANAGEMENT'));

CREATE TABLE IF NOT EXISTS paper_counterfactual_variants (
    variant_id          bigserial   PRIMARY KEY,
    account_id          text        NOT NULL,
    strategy            text        NOT NULL,
    eval_id             bigint,
    stage               text        NOT NULL,
    verdict             text        NOT NULL,
    refusal             text,
    us_market_slug      text,
    holding_side        text,
    fixture             text,
    variant             text        NOT NULL,
    style               text        NOT NULL,
    qty                 numeric     NOT NULL,
    vwap                numeric,
    p_used              numeric,
    fill_probability    numeric,
    cost_usd            numeric,
    fees_usd            numeric,
    expected_ev_usd     numeric,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    decided_at          timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text        NOT NULL DEFAULT 'PAPER',
    authority           text        NOT NULL DEFAULT 'PAPER_ONLY_NO_CAPITAL_AUTHORITY',
    evidence_class      text        NOT NULL DEFAULT 'COUNTERFACTUAL_VARIANT',
    CONSTRAINT paper_cf_var_label_ck CHECK (label = 'PAPER'),
    CONSTRAINT paper_cf_var_authority_ck CHECK (
        authority = 'PAPER_ONLY_NO_CAPITAL_AUTHORITY'),
    CONSTRAINT paper_cf_var_class_ck CHECK (
        evidence_class = 'COUNTERFACTUAL_VARIANT'),
    CONSTRAINT paper_cf_var_stage_ck CHECK (stage IN ('DECISION', 'LEDGER')),
    CONSTRAINT paper_cf_var_verdict_ck CHECK (verdict IN ('ENTER', 'CASH')),
    CONSTRAINT paper_cf_var_variant_ck CHECK (variant IN (
        'AS_BOUND', 'POLICY_SIZE', 'HALF_SIZE', 'MAKER',
        'HOLD_TO_SETTLEMENT', 'EARLY_EXIT')),
    CONSTRAINT paper_cf_var_style_ck CHECK (style IN ('MAKER', 'TAKER')),
    CONSTRAINT paper_cf_var_qty_ck CHECK (qty >= 0),
    CONSTRAINT paper_cf_var_detail_ck CHECK (jsonb_typeof(detail) = 'object'),
    UNIQUE (eval_id, variant)
);
CREATE INDEX IF NOT EXISTS paper_cf_var_contract_idx
    ON paper_counterfactual_variants (account_id, us_market_slug,
                                      holding_side, decided_at DESC);
CREATE INDEX IF NOT EXISTS paper_cf_var_strategy_idx
    ON paper_counterfactual_variants (account_id, strategy, decided_at DESC);

CREATE TABLE IF NOT EXISTS paper_counterfactual_variant_outcomes (
    outcome_id          bigserial   PRIMARY KEY,
    variant_id          bigint      NOT NULL UNIQUE
                        REFERENCES paper_counterfactual_variants (variant_id),
    outcome             text        NOT NULL,
    payout_per_contract numeric     NOT NULL,
    counterfactual_pnl_usd numeric  NOT NULL,
    settled_at          timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text        NOT NULL DEFAULT 'PAPER',
    pnl_class           text        NOT NULL DEFAULT 'NOT_REALIZED_PNL',
    CONSTRAINT paper_cf_out_label_ck CHECK (label = 'PAPER'),
    CONSTRAINT paper_cf_out_class_ck CHECK (pnl_class = 'NOT_REALIZED_PNL'),
    CONSTRAINT paper_cf_out_outcome_ck CHECK (outcome IN ('WON', 'LOST')),
    CONSTRAINT paper_cf_out_payout_ck CHECK (
        payout_per_contract BETWEEN 0 AND 1)
);

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['paper_counterfactual_variants',
                             'paper_counterfactual_variant_outcomes'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION '
                       'paper_profitability_bind_is_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION '
                       'paper_profitability_bind_is_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;
