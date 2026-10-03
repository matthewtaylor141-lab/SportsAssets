-- ══════════════════════════════════════════════════════════════════════
-- 209 · COVERAGE INTEGRITY, AUTOMATIC POSTMORTEMS, THE IMPROVEMENT DRIVER
-- ══════════════════════════════════════════════════════════════════════
--
-- FOUR APPEND/UPSERT RECORDS, all written by Audrey's scheduled steps
-- (agents/coverage_integrity.py, agents/postmortems.py,
-- agents/improvement_driver.py). None of them is read by an order path,
-- and none of them carries a limit, a capital figure or a switch.
--
--   coverage_funnel_snapshots  ONE ROW PER (timezone, local day, league):
--                              the funnel provider -> normalized -> venue
--                              discovered -> mapped -> settlement-supported
--                              -> evaluated -> ENTER/REFUSE -> order -> fill,
--                              in PROVIDER EVENTS. A stage whose source
--                              table is absent or unreadable is NULL with a
--                              named reason in `unavailable` -- never 0.
--                              Today's row is refreshed in place; a past
--                              day's row is final once the day has ended.
--   coverage_collapse_alerts   one row per detected collapse (a stage ratio
--                              below its trailing baseline by more than the
--                              declared threshold, or a league present at
--                              the provider and absent downstream), with
--                              the Audrey finding it raised (or why none).
--   position_postmortems       one row per CLOSED position, PAPER and
--                              ACTUAL kept apart by `book`: realized P&L
--                              decomposed into selection edge, execution
--                              slippage, fees, management (Xavier vs hold)
--                              and outcome variance; an unmeasured
--                              component is NULL with its reason, and the
--                              unexplained remainder is stated so the
--                              measured parts never pretend to sum alone.
--   improvement_deficits       the driver's audit: each ordinary deficit it
--                              observed, the Audrey finding that is its
--                              evidence, the collaboration-loop finding it
--                              opened and the furthest stage it reached --
--                              never past BOUNDED_EXPERIMENT (registration
--                              of a PAPER_ONLY experiment). Promotion,
--                              capital, limits, policy, strategy and
--                              credentials stay with people (CHECK).
--
-- IDEMPOTENT: IF NOT EXISTS throughout. No BEGIN/COMMIT of its own.

CREATE TABLE IF NOT EXISTS coverage_funnel_snapshots (
    tz                    text        NOT NULL,
    day                   date        NOT NULL,
    league                text        NOT NULL,
    sport_family          text,
    provider_events       integer,
    normalized_events     integer,
    venue_discovered      integer,
    mapped_events         integer,
    settlement_supported  integer,
    evaluated_events      integer,
    decided_events        integer,
    entered_events        integer,
    refused_events        integer,
    ordered_events        integer,
    filled_events         integer,
    actual_intents        integer,
    actual_submitted      integer,
    actual_filled         integer,
    venue_catalogue_events integer,
    ratios                jsonb       NOT NULL DEFAULT '{}'::jsonb,
    unavailable           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    sources               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    final                 boolean     NOT NULL DEFAULT false,
    version               text        NOT NULL,
    computed_at           timestamptz NOT NULL,
    first_computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tz, day, league),
    CONSTRAINT coverage_funnel_tz_ck CHECK (
        tz IN ('UTC', 'America/New_York')),
    -- A COUNT IS NEVER NEGATIVE; AN UNMEASURED ONE IS NULL, NOT 0.
    CONSTRAINT coverage_funnel_nonneg_ck CHECK (
        coalesce(provider_events, 0) >= 0
        AND coalesce(normalized_events, 0) >= 0
        AND coalesce(venue_discovered, 0) >= 0
        AND coalesce(mapped_events, 0) >= 0
        AND coalesce(settlement_supported, 0) >= 0
        AND coalesce(evaluated_events, 0) >= 0
        AND coalesce(decided_events, 0) >= 0
        AND coalesce(entered_events, 0) >= 0
        AND coalesce(refused_events, 0) >= 0
        AND coalesce(ordered_events, 0) >= 0
        AND coalesce(filled_events, 0) >= 0),
    CONSTRAINT coverage_funnel_docs_ck CHECK (
        jsonb_typeof(ratios) = 'object'
        AND jsonb_typeof(unavailable) = 'object'
        AND jsonb_typeof(sources) = 'object')
);
CREATE INDEX IF NOT EXISTS coverage_funnel_snapshots_day_idx
    ON coverage_funnel_snapshots (tz, day DESC);

CREATE TABLE IF NOT EXISTS coverage_collapse_alerts (
    alert_id          text PRIMARY KEY,
    tz                text        NOT NULL,
    day               date        NOT NULL,
    league            text        NOT NULL,
    kind              text        NOT NULL,
    stage_from        text,
    stage_to          text        NOT NULL,
    ratio             double precision,
    baseline          double precision,
    threshold         jsonb       NOT NULL,
    severity          text        NOT NULL,
    detail            jsonb       NOT NULL,
    audrey_finding_id text,
    audrey_refusal    text,
    detected_at       timestamptz NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT coverage_collapse_alerts_once UNIQUE (
        tz, day, league, kind, stage_to),
    CONSTRAINT coverage_collapse_alerts_kind_ck CHECK (
        kind IN ('RATIO_COLLAPSE', 'ABSENT_DOWNSTREAM')),
    CONSTRAINT coverage_collapse_alerts_severity_ck CHECK (
        severity IN ('INFO', 'WARNING', 'CRITICAL')),
    -- an alert either names its Audrey finding or says why there is none
    CONSTRAINT coverage_collapse_alerts_routed_ck CHECK (
        audrey_finding_id IS NOT NULL OR audrey_refusal IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS coverage_collapse_alerts_at_idx
    ON coverage_collapse_alerts (detected_at DESC);

CREATE TABLE IF NOT EXISTS position_postmortems (
    book                    text        NOT NULL,
    position_key            text        NOT NULL,
    venue                   text        NOT NULL,
    account_id              text,
    group_id                text,
    us_market_slug          text,
    holding_side            text,
    fixture                 text,
    strategy                text,
    decision_id             text,
    opened_at               timestamptz,
    closed_at               timestamptz,
    qty                     numeric,
    realized_pnl_usd        numeric     NOT NULL,
    selection_edge_usd      numeric,
    execution_slippage_usd  numeric,
    fees_usd                numeric,
    management_value_usd    numeric,
    outcome_variance_usd    numeric,
    unexplained_usd         numeric     NOT NULL,
    decomposition_complete  boolean     NOT NULL,
    components              jsonb       NOT NULL,
    detail                  jsonb       NOT NULL,
    content_sha             text        NOT NULL,
    revision                integer     NOT NULL DEFAULT 1,
    version                 text        NOT NULL,
    computed_at             timestamptz NOT NULL,
    first_computed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (book, position_key),
    -- PAPER AND ACTUAL ARE NEVER MIXED
    CONSTRAINT position_postmortems_book_ck CHECK (
        book IN ('PAPER', 'ACTUAL')),
    -- THE PARTS SUM TO THE REALIZED FIGURE: measured components plus the
    -- stated unexplained remainder, to the cent.
    CONSTRAINT position_postmortems_sums_ck CHECK (
        abs(coalesce(selection_edge_usd, 0) + coalesce(execution_slippage_usd, 0)
            + coalesce(fees_usd, 0) + coalesce(management_value_usd, 0)
            + coalesce(outcome_variance_usd, 0) + unexplained_usd
            - realized_pnl_usd) <= 0.01),
    CONSTRAINT position_postmortems_docs_ck CHECK (
        jsonb_typeof(components) = 'object' AND jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS position_postmortems_closed_idx
    ON position_postmortems (book, closed_at DESC);

CREATE TABLE IF NOT EXISTS improvement_deficits (
    deficit_id         text PRIMARY KEY,
    kind               text        NOT NULL,
    subject            text        NOT NULL,
    observed_at        timestamptz NOT NULL,
    evidence           jsonb       NOT NULL,
    proposer           text        NOT NULL,
    challenger         text        NOT NULL,
    audrey_finding_id  text,
    agent_finding_id   text,
    stage_reached      text,
    challenge_outcome  text,
    refusal            text,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    updated_at         timestamptz NOT NULL,
    CONSTRAINT improvement_deficits_kind_ck CHECK (kind IN (
        'COVERAGE_COLLAPSE', 'CALIBRATION_DRIFT', 'STALE_REVIEWS',
        'ADMISSION_REFUSAL_SPIKE')),
    -- THE PEER CHALLENGE IS BY A DIFFERENT AGENT
    CONSTRAINT improvement_deficits_agents_ck CHECK (
        proposer IN ('DEREK', 'XAVIER', 'AUDREY')
        AND challenger IN ('DEREK', 'XAVIER', 'AUDREY')
        AND proposer <> challenger),
    -- THE DRIVER STOPS AT REGISTRATION OF A PAPER EXPERIMENT: it never
    -- records a candidate, an evaluation or release eligibility, and it
    -- changes nothing in production.
    CONSTRAINT improvement_deficits_stage_ck CHECK (
        stage_reached IS NULL OR stage_reached IN (
            'EVIDENCE', 'HYPOTHESIS', 'PEER_CHALLENGE',
            'BOUNDED_EXPERIMENT', 'CLOSED')),
    CONSTRAINT improvement_deficits_no_production_effect_ck CHECK (
        production_effect = 'NONE'),
    CONSTRAINT improvement_deficits_evidence_ck CHECK (
        jsonb_typeof(evidence) = 'object'
        -- no limit, capital, credential, approval or activation key (the
        -- same list as migration 203, inlined so 203 stays independent)
        AND NOT (evidence ?| ARRAY['risk_limits', 'limits', 'capital_usd',
                     'max_downside_usd', 'max_incremental_capital_usd',
                     'per_order_usd', 'max_order_usd', 'daily_loss_stop_usd',
                     'credentials', 'api_key', 'account_authority',
                     'submission_enabled', 'approved', 'approved_by',
                     'approval', 'activate', 'activation', 'active',
                     'policy_activation']))
);
CREATE INDEX IF NOT EXISTS improvement_deficits_at_idx
    ON improvement_deficits (observed_at DESC);
