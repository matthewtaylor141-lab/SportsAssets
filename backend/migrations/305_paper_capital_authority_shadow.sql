-- ══════════════════════════════════════════════════════════════════════
-- 305 · PAPER CAPITAL AUTHORITY: ZERO-CAPITAL SHADOW COUNTERFACTUALS AND
--       THE ENTRY-REFUSAL CENSUS (PAPER ONLY, NO CAPITAL AUTHORITY,
--       APPEND-ONLY)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS RECORDS (bettor_capital_authority):
--
--   paper_shadow_counterfactuals          one row per decision of a strategy
--       WITHOUT capital authority (a lifecycle no-entry state, a stopping
--       rule firing at the entry, or forward economics UNKNOWN / NEGATIVE)
--       that passed every other entry condition: the decision instant, the
--       book observed at that instant, the depth walk, fees, the
--       adverse-selection estimate, the executable EV, and the
--       decision -> execution delay of the simulator configuration. It is
--       NEVER a paper order: no paper_orders row, no reservation, no
--       exposure, no ledger entry.
--   paper_shadow_counterfactual_outcomes  the counterfactual result once the
--       contract settles (authoritative settlement evidence only), its own
--       append-only row; the decision row is never rewritten.
--   paper_entry_refusal_census            every refused PAPER entry (decision
--       or ledger stage) with strategy, contract, side, fixture, edge, edge
--       shortfall, expected fees, execution-cost estimate, executable EV and
--       the exact refusal, so refusals rank by unique opportunity.
--
-- LABELS. Every shadow row carries evidence_class = 'SHADOW_COUNTERFACTUAL'
-- and pnl_class = 'NOT_REALIZED_PNL' (CHECK constraints): a counterfactual is
-- never realized PAPER P&L, never summed with it, never ACTUAL.
--
-- APPEND-ONLY. UPDATE, DELETE and TRUNCATE are refused by trigger on all
-- three tables: history is never rewritten.
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

CREATE TABLE IF NOT EXISTS paper_shadow_counterfactuals (
    shadow_id           bigserial   PRIMARY KEY,
    shadow_key          text        NOT NULL UNIQUE,
    account_id          text        NOT NULL,
    strategy            text        NOT NULL,
    decision_id         text,
    order_key           text,
    source              text        NOT NULL,
    capital_refusal     text        NOT NULL,
    lifecycle_state     text,
    us_market_slug      text        NOT NULL,
    holding_side        text        NOT NULL,
    fixture             text,
    payout_event        text,
    order_type          text        NOT NULL DEFAULT 'MARKETABLE',
    decided_at          timestamptz NOT NULL,
    eligible_at         timestamptz NOT NULL,
    expires_at          timestamptz NOT NULL,
    decision_to_execution_delay_s numeric NOT NULL,
    simulator_version   text,
    p                   numeric     NOT NULL,
    limit_price         numeric     NOT NULL,
    qty                 numeric     NOT NULL,
    book_obs_id         bigint,
    book_observed_at    timestamptz,
    levels              jsonb       NOT NULL,
    fills               jsonb       NOT NULL,
    vwap                numeric,
    cost_usd            numeric     NOT NULL,
    fees_usd            numeric     NOT NULL,
    slippage_usd        numeric,
    adverse_selection_usd numeric   NOT NULL,
    total_executable_ev_usd numeric NOT NULL,
    evidence            jsonb       NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    evidence_class      text        NOT NULL DEFAULT 'SHADOW_COUNTERFACTUAL',
    pnl_class           text        NOT NULL DEFAULT 'NOT_REALIZED_PNL',
    authority           text        NOT NULL DEFAULT 'PAPER_ONLY_NO_CAPITAL_AUTHORITY',
    CONSTRAINT paper_shadow_evidence_class_ck CHECK (
        evidence_class = 'SHADOW_COUNTERFACTUAL'),
    CONSTRAINT paper_shadow_pnl_class_ck CHECK (pnl_class = 'NOT_REALIZED_PNL'),
    CONSTRAINT paper_shadow_authority_ck CHECK (
        authority = 'PAPER_ONLY_NO_CAPITAL_AUTHORITY'),
    CONSTRAINT paper_shadow_side_ck CHECK (holding_side IN ('LONG', 'SHORT')),
    CONSTRAINT paper_shadow_source_ck CHECK (source IN (
        'DECISION_CAPITAL_GATE', 'LEDGER_CAPITAL_AUTHORITY')),
    CONSTRAINT paper_shadow_order_type_ck CHECK (order_type IN (
        'MARKETABLE', 'RESTING')),
    CONSTRAINT paper_shadow_qty_ck CHECK (qty > 0),
    CONSTRAINT paper_shadow_limit_ck CHECK (limit_price > 0 AND limit_price < 1),
    CONSTRAINT paper_shadow_p_ck CHECK (p >= 0 AND p <= 1),
    CONSTRAINT paper_shadow_ev_positive_ck CHECK (total_executable_ev_usd > 0),
    CONSTRAINT paper_shadow_timing_ck CHECK (eligible_at >= decided_at
        AND expires_at >= eligible_at),
    CONSTRAINT paper_shadow_evidence_ck CHECK (jsonb_typeof(evidence) = 'object')
);
CREATE INDEX IF NOT EXISTS paper_shadow_strategy_idx
    ON paper_shadow_counterfactuals (account_id, strategy, decided_at DESC);
CREATE INDEX IF NOT EXISTS paper_shadow_slug_idx
    ON paper_shadow_counterfactuals (us_market_slug, holding_side);

CREATE TABLE IF NOT EXISTS paper_shadow_counterfactual_outcomes (
    outcome_id          bigserial   PRIMARY KEY,
    shadow_id           bigint      NOT NULL UNIQUE
                        REFERENCES paper_shadow_counterfactuals (shadow_id),
    outcome             text        NOT NULL,
    payout_per_contract numeric,
    execution_basis     text        NOT NULL,
    execution_obs_id    bigint,
    filled_qty          numeric     NOT NULL,
    exec_cost_usd       numeric     NOT NULL,
    exec_fees_usd       numeric     NOT NULL,
    counterfactual_pnl_usd numeric  NOT NULL,
    evidence            jsonb       NOT NULL,
    settled_at          timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    evidence_class      text        NOT NULL DEFAULT 'SHADOW_COUNTERFACTUAL',
    pnl_class           text        NOT NULL DEFAULT 'NOT_REALIZED_PNL',
    CONSTRAINT paper_shadow_out_evidence_class_ck CHECK (
        evidence_class = 'SHADOW_COUNTERFACTUAL'),
    CONSTRAINT paper_shadow_out_pnl_class_ck CHECK (
        pnl_class = 'NOT_REALIZED_PNL'),
    CONSTRAINT paper_shadow_out_outcome_ck CHECK (outcome IN (
        'WON', 'LOST', 'VOID_REFUND', 'SETTLED_AT_VENUE_PRICE', 'NO_FILL')),
    CONSTRAINT paper_shadow_out_filled_ck CHECK (filled_qty >= 0),
    CONSTRAINT paper_shadow_out_evidence_ck CHECK (
        jsonb_typeof(evidence) = 'object')
);

CREATE TABLE IF NOT EXISTS paper_entry_refusal_census (
    refusal_id          bigserial   PRIMARY KEY,
    account_id          text        NOT NULL,
    strategy            text        NOT NULL,
    stage               text        NOT NULL,
    decision_id         text,
    order_key           text,
    refusal             text        NOT NULL,
    refusals            text[]      NOT NULL DEFAULT '{}',
    us_market_slug      text,
    holding_side        text,
    fixture             text,
    line                text,
    scope               text,
    p                   numeric,
    best_price          numeric,
    gross_edge_pp       numeric,
    threshold_edge_pp   numeric,
    edge_shortfall_pp   numeric,
    expected_fees_usd   numeric,
    slippage_usd        numeric,
    adverse_selection_usd numeric,
    total_executable_ev_usd numeric,
    qty                 numeric,
    limit_price         numeric,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    refused_at          timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    label               text        NOT NULL DEFAULT 'PAPER',
    CONSTRAINT paper_refusal_census_label_ck CHECK (label = 'PAPER'),
    CONSTRAINT paper_refusal_census_stage_ck CHECK (stage IN (
        'DECISION', 'LEDGER')),
    CONSTRAINT paper_refusal_census_detail_ck CHECK (
        jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS paper_refusal_census_at_idx
    ON paper_entry_refusal_census (account_id, refused_at DESC);
CREATE INDEX IF NOT EXISTS paper_refusal_census_contract_idx
    ON paper_entry_refusal_census (us_market_slug, holding_side);

CREATE OR REPLACE FUNCTION paper_capital_authority_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (%): history is never rewritten',
                    TG_TABLE_NAME, TG_OP;
END $$;

DROP TRIGGER IF EXISTS paper_shadow_append_only_trg
    ON paper_shadow_counterfactuals;
CREATE TRIGGER paper_shadow_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_shadow_counterfactuals
    FOR EACH ROW EXECUTE FUNCTION paper_capital_authority_is_append_only();
DROP TRIGGER IF EXISTS paper_shadow_no_truncate_trg
    ON paper_shadow_counterfactuals;
CREATE TRIGGER paper_shadow_no_truncate_trg
    BEFORE TRUNCATE ON paper_shadow_counterfactuals
    FOR EACH STATEMENT EXECUTE FUNCTION paper_capital_authority_is_append_only();

DROP TRIGGER IF EXISTS paper_shadow_out_append_only_trg
    ON paper_shadow_counterfactual_outcomes;
CREATE TRIGGER paper_shadow_out_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_shadow_counterfactual_outcomes
    FOR EACH ROW EXECUTE FUNCTION paper_capital_authority_is_append_only();
DROP TRIGGER IF EXISTS paper_shadow_out_no_truncate_trg
    ON paper_shadow_counterfactual_outcomes;
CREATE TRIGGER paper_shadow_out_no_truncate_trg
    BEFORE TRUNCATE ON paper_shadow_counterfactual_outcomes
    FOR EACH STATEMENT EXECUTE FUNCTION paper_capital_authority_is_append_only();

DROP TRIGGER IF EXISTS paper_refusal_census_append_only_trg
    ON paper_entry_refusal_census;
CREATE TRIGGER paper_refusal_census_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_entry_refusal_census
    FOR EACH ROW EXECUTE FUNCTION paper_capital_authority_is_append_only();
DROP TRIGGER IF EXISTS paper_refusal_census_no_truncate_trg
    ON paper_entry_refusal_census;
CREATE TRIGGER paper_refusal_census_no_truncate_trg
    BEFORE TRUNCATE ON paper_entry_refusal_census
    FOR EACH STATEMENT EXECUTE FUNCTION paper_capital_authority_is_append_only();
