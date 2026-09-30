-- ══════════════════════════════════════════════════════════════════════
-- 172 · PAPER TRADING: DEREK'S PAPER DECISIONS, THE HANDOFF TO XAVIER,
--       XAVIER'S PAPER REVIEWS, AUDREY'S PAPER REPORTS AND FINDINGS, AND
--       THE EQUITY SNAPSHOTS DRAWDOWN IS MEASURED ON
-- ══════════════════════════════════════════════════════════════════════
--
-- Every table is paper_*, keyed to a paper session, and never read by a
-- funded module. Decisions are PERSISTED BEFORE any simulated execution:
-- `paper_decisions` is append-only and a paper order names the decision it
-- executes (paper_orders.decision_id), never the reverse.

BEGIN;

CREATE TABLE IF NOT EXISTS paper_decisions (
    decision_id         text PRIMARY KEY,
    session_id          text        NOT NULL REFERENCES paper_sessions,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    decided_at          timestamptz NOT NULL,
    valuation_id        bigint,
    us_market_slug      text,
    holding_side        text,
    intent              text,
    fixture             text,
    -- THE LABEL INPUTS (team, market, line, side, period, competition,
    -- event date) the UI labels the record with.
    label               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    verdict             text        NOT NULL,
    refusal             text,
    refusals            text[]      NOT NULL DEFAULT '{}',
    p_internal          double precision,
    internal_model      jsonb       NOT NULL,
    p_pinnacle          double precision,
    pinnacle            jsonb       NOT NULL,
    p_blended           double precision,
    book_obs_id         bigint,
    book                jsonb,
    proposed_qty        numeric(18,6),
    limit_price         numeric(18,6),
    economics           jsonb,
    qualification_gaps  jsonb       NOT NULL,
    policy_version      text        NOT NULL,
    policy_decision     jsonb,
    alternatives        jsonb,
    optimistic          jsonb,
    simulator_version   text        NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_decisions_paper_id_ck CHECK (decision_id LIKE 'paper%'),
    CONSTRAINT paper_decisions_verdict_ck CHECK (verdict IN (
        'ENTER', 'REFUSE')),
    CONSTRAINT paper_decisions_refusal_ck CHECK (
        (verdict = 'ENTER') = (refusal IS NULL)),
    CONSTRAINT paper_decisions_gaps_ck CHECK (
        jsonb_typeof(qualification_gaps) = 'array')
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_decisions_one_per_valuation_idx
    ON paper_decisions (session_id, valuation_id)
    WHERE valuation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS paper_decisions_at_idx
    ON paper_decisions (decided_at);

-- ── DEREK -> XAVIER: from the FIRST partial paper fill, one owner per group
CREATE TABLE IF NOT EXISTS paper_handoffs (
    handoff_id          text PRIMARY KEY,
    session_id          text        NOT NULL REFERENCES paper_sessions,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    group_id            text        NOT NULL UNIQUE,
    decision_id         text,
    entry_order_id      text        NOT NULL,
    first_fill_id       text        NOT NULL,
    first_fill_at       timestamptz NOT NULL,
    owner               text        NOT NULL DEFAULT 'XAVIER',
    confirmed_qty       numeric(18,6) NOT NULL,
    outstanding_qty     numeric(18,6) NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_handoffs_paper_id_ck CHECK (handoff_id LIKE 'paper%'),
    CONSTRAINT paper_handoffs_owner_ck CHECK (owner = 'XAVIER')
);

CREATE TABLE IF NOT EXISTS paper_xavier_reviews (
    review_id           text PRIMARY KEY,
    session_id          text        NOT NULL REFERENCES paper_sessions,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    group_id            text        NOT NULL,
    reviewed_at         timestamptz NOT NULL,
    trigger             text        NOT NULL,
    recommendation      text,
    refusal             text,
    alternatives        jsonb       NOT NULL,
    selection           jsonb,
    exposure            jsonb       NOT NULL,
    standing            jsonb,
    confirmed_protection jsonb,
    incomplete_search   jsonb,
    exceptional         jsonb,
    measure             jsonb,
    action              jsonb,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_xavier_reviews_paper_id_ck CHECK (
        review_id LIKE 'paper%'),
    CONSTRAINT paper_xavier_reviews_trigger_ck CHECK (trigger IN (
        'FIRST_FILL', 'FILL_EVENT', 'MARKET_EVENT', 'SCHEDULED_BACKSTOP'))
);
CREATE INDEX IF NOT EXISTS paper_xavier_reviews_group_idx
    ON paper_xavier_reviews (group_id, reviewed_at DESC);

CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
    session_id          text        NOT NULL REFERENCES paper_sessions,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    at                  timestamptz NOT NULL,
    cash_usd            numeric(18,6) NOT NULL,
    reserved_usd        numeric(18,6) NOT NULL,
    marked_value_usd    numeric(18,6) NOT NULL,
    equity_usd          numeric(18,6),
    equity_excluding_unmarked_usd numeric(18,6) NOT NULL,
    unmarked_positions  integer     NOT NULL,
    realized_pnl_usd    numeric(18,6) NOT NULL,
    unrealized_pnl_usd  numeric(18,6),
    last_sequence       bigint,
    PRIMARY KEY (session_id, at)
);

CREATE TABLE IF NOT EXISTS paper_audrey_reports (
    report_id           text PRIMARY KEY,
    session_id          text        NOT NULL REFERENCES paper_sessions,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    report_day          date        NOT NULL,
    reporting_tz        text        NOT NULL,
    version             integer     NOT NULL,
    generated_at        timestamptz NOT NULL,
    final               boolean     NOT NULL,
    reconciles          boolean     NOT NULL,
    report              jsonb       NOT NULL,
    digest              text        NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_audrey_reports_paper_id_ck CHECK (
        report_id LIKE 'paper%'),
    CONSTRAINT paper_audrey_reports_version_ck UNIQUE (
        session_id, report_day, version)
);

CREATE TABLE IF NOT EXISTS paper_audrey_findings (
    finding_id          text PRIMARY KEY,
    session_id          text        NOT NULL REFERENCES paper_sessions,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    found_at            timestamptz NOT NULL,
    kind                text        NOT NULL,
    severity            text        NOT NULL,
    subject             text,
    detail              jsonb       NOT NULL,
    improvement_task_id text,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_audrey_findings_paper_id_ck CHECK (
        finding_id LIKE 'paper%'),
    CONSTRAINT paper_audrey_findings_severity_ck CHECK (severity IN (
        'INFO', 'WARNING', 'CRITICAL'))
);
CREATE INDEX IF NOT EXISTS paper_audrey_findings_at_idx
    ON paper_audrey_findings (found_at DESC);

DROP TRIGGER IF EXISTS paper_decisions_append_only_trg ON paper_decisions;
CREATE TRIGGER paper_decisions_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_decisions
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_xavier_reviews_append_only_trg
    ON paper_xavier_reviews;
CREATE TRIGGER paper_xavier_reviews_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_xavier_reviews
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_audrey_reports_append_only_trg
    ON paper_audrey_reports;
CREATE TRIGGER paper_audrey_reports_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_audrey_reports
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_equity_snapshots_append_only_trg
    ON paper_equity_snapshots;
CREATE TRIGGER paper_equity_snapshots_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_equity_snapshots
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

COMMIT;
