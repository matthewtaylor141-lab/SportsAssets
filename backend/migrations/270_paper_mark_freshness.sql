-- 270: PAPER MARK FRESHNESS -- THE HELD-MARK REFRESH RECORD AND THE
-- MANAGEMENT REFUSALS (PAPER ONLY).
--
-- THE DEFECT (production, 2026-10-05): the Command Center showed "158 stale
-- marks > 300s, 12 unmarked, 71/241 fresh" and Xavier BLOCKED_ON_MARKET_DATA.
-- The paper pass read at most max_book_reads_per_pass // 2 = 6 books per pass
-- (agents/paper_runtime.step_books), held positions LAST, behind due entries,
-- work-queue slugs and every resting order; a failed read was invisible to
-- the marks (latest_marks reads only error IS NULL), and a skipped read left
-- no record at all.
--
-- THE REPAIR (agents/paper_mark_refresh): a separate, explicitly budgeted
-- refresh of EVERY held market, stalest first, each run recorded here --
-- every market that was due, read or not, with its outcome and its failure
-- or skip reason. bettor_paper_freshness classifies every open position
-- into exactly one of FRESH / QUIET_VALID / STALE / FEED_GAP / UNMARKED /
-- EXTERNAL_UNAVAILABLE from the observations and the latest run.
--
-- AND THE REFUSALS: Xavier's management packet (a HOLD / EXIT / REDUCE /
-- HEDGE needs reconciled qty, a FRESH probability, a current executable book
-- with exit depth, settlement identity and protection state) and the
-- allocation rail (no new ENTER for a strategy whose open positions cannot
-- be freshly managed) record every refusal here, with the missing elements.
--
-- Append-only records. No threshold, limit, cap, policy, live / SMALL LIVE
-- switch or funded control is touched.

CREATE TABLE IF NOT EXISTS paper_mark_refresh_runs (
    run_id              bigserial   PRIMARY KEY,
    account_id          text        NOT NULL,
    trigger             text        NOT NULL,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz,
    held_markets        integer     NOT NULL DEFAULT 0,
    due                 integer     NOT NULL DEFAULT 0,
    not_due             integer     NOT NULL DEFAULT 0,
    harvested           integer     NOT NULL DEFAULT 0,
    read_attempted      integer     NOT NULL DEFAULT 0,
    read_ok             integer     NOT NULL DEFAULT 0,
    read_failed         integer     NOT NULL DEFAULT 0,
    skipped_budget      integer     NOT NULL DEFAULT 0,
    skipped_terminal    integer     NOT NULL DEFAULT 0,
    budget              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    outcomes            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error               text,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_mark_refresh_runs_paper_ck CHECK (
        account_id LIKE 'paper%')
);
CREATE INDEX IF NOT EXISTS paper_mark_refresh_runs_acct_idx
    ON paper_mark_refresh_runs (account_id, started_at DESC);

CREATE TABLE IF NOT EXISTS paper_management_refusals (
    refusal_id          bigserial   PRIMARY KEY,
    account_id          text        NOT NULL,
    kind                text        NOT NULL,
    refusal             text        NOT NULL,
    strategy            text,
    group_id            text,
    position_key        text,
    us_market_slug      text,
    review_id           text,
    order_key           text,
    missing             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    refused_at          timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_management_refusals_paper_ck CHECK (
        account_id LIKE 'paper%'),
    CONSTRAINT paper_management_refusals_kind_ck CHECK (kind IN (
        'XAVIER_PACKET_INCOMPLETE', 'ENTRY_ALLOCATION_STALE_MANAGEMENT'))
);
CREATE INDEX IF NOT EXISTS paper_management_refusals_acct_idx
    ON paper_management_refusals (account_id, kind, refused_at DESC);

-- APPEND-ONLY: a recorded run or refusal is history.
CREATE OR REPLACE FUNCTION paper_mark_freshness_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'paper_mark_refresh_runs'
       AND OLD.finished_at IS NULL THEN
        -- the run's own completion (counts, outcomes, finished_at) is the
        -- one permitted update, once
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '% is append-only (%)', TG_TABLE_NAME, TG_OP;
END $$;

DROP TRIGGER IF EXISTS paper_mark_refresh_runs_append_only
    ON paper_mark_refresh_runs;
CREATE TRIGGER paper_mark_refresh_runs_append_only
    BEFORE UPDATE OR DELETE ON paper_mark_refresh_runs
    FOR EACH ROW EXECUTE FUNCTION paper_mark_freshness_append_only();

DROP TRIGGER IF EXISTS paper_management_refusals_append_only
    ON paper_management_refusals;
CREATE TRIGGER paper_management_refusals_append_only
    BEFORE UPDATE OR DELETE ON paper_management_refusals
    FOR EACH ROW EXECUTE FUNCTION paper_mark_freshness_append_only();
