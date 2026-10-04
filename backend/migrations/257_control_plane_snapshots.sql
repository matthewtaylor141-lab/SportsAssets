-- ══════════════════════════════════════════════════════════════════════
-- 257 · BETTOR PROFITABILITY CONTROL PLANE, STREAM E: FROZEN DECISION
--       SNAPSHOTS, POST-TRADE OBSERVATIONS, AND THE PAPER REPLAY
--       HARNESS'S SCRATCH ACCOUNT
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER REQUIREMENT (section 6). "Every evaluated opportunity becomes data.
-- Freeze the decision-time state ... Then collect future observations: +10
-- sec, +30 sec, +1 min, +5 min, +15 min where useful, close / final
-- pre-resolution market where defined, settlement ... All learning data must
-- be event-safe and leakage-safe. Do not train on future information
-- accidentally exposed through joins or mutable tables. Create reproducible
-- frozen decision snapshots."
--
-- THE DEFECT THIS CLOSES. The decision-time state of an evaluated
-- opportunity exists today only spread across MUTABLE and append-only
-- tables: paper_decisions (append-only), external_valuations (whose outcome
-- columns are rewritten in place hours later, stamped with the VENUE's
-- settlement time, not the write), paper_orders (state / filled_qty /
-- queue rewritten by the simulator on every step), the canonical intent
-- (225, append-only) and the book observation. A learner that JOINs them
-- "as of now" reads the future: an order's terminal state, a valuation's
-- joined outcome. These tables freeze what was knowable at the decision,
-- once, with its sha256, and record every later observation with the
-- recorded stamp of the evidence it used -- CHECKed <= the horizon clock.
--
--   cp_decision_snapshots        ONE frozen row per evaluated decision.
--                                snapshot_id = 'snap_' + sha256(
--                                opportunity_id | decision clock | code sha)
--                                [:24] (control_plane/ids.py): the
--                                opportunity AT THE DECISION INSTANT under
--                                one code. Several strategies may evaluate
--                                the same opportunity at the same instant
--                                (a paper pass's clock is shared), so the
--                                row key is (snapshot_id, decision_id).
--                                `frozen` carries EVERY owner-listed field,
--                                each MEASURED with its value or
--                                UNAVAILABLE / NOT_APPLICABLE with its
--                                reason -- never a manufactured zero
--                                (cp_frozen_complete CHECKs all 22 and
--                                refuses a value on an unavailable field).
--   cp_post_trade_observations   per snapshot decision and horizon (+10s,
--                                +30s, +1m, +5m, +15m, close, settlement):
--                                the venue bid / ask / mid from a RECORDED
--                                book observation, price movement, CLV
--                                (close only), realized P&L from the paper
--                                ledger (settlement only) and the metrics.
--                                THE LEAKAGE GUARD: the evidence row's own
--                                recorded stamp and its observation instant
--                                are both CHECKed <= the horizon clock.
--   cp_replay_runs /             the deterministic PAPER REPLAY harness's
--   cp_replay_events             scratch account: one run (its inputs sha,
--                                stages, output sha) and its ordered event
--                                stream. The account id is CHECKed
--                                'cpreplay_<24 hex>': it can never be the
--                                production paper account, and no paper_*
--                                table is ever written by a replay.
--
-- AUTHORITY. Every row is SHADOW (CHECKed): nothing here changes an order, a
-- size, an admission order, a gate, a threshold, a limit or a rail, and no
-- decision path reads these tables. Migration 217's registry of tables the
-- agents hold no authority over gains these four, and the guard is attached
-- exactly as migration 225 section 8 attaches it.
--
-- APPEND-ONLY: UPDATE, DELETE and TRUNCATE are refused on all four.
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

-- ── shared append-only guard ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION cp_e_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'CONTROL_PLANE_APPEND_ONLY: % on % is refused (append-only record)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;

-- ── the owner's field list: present, and measured or with a reason ─────
-- The 22 fields of section 6, in the owner's order. A MEASURED field
-- carries `value`; an UNAVAILABLE or NOT_APPLICABLE field carries a
-- non-empty `why` and NO value (an unavailable field holding a 0 is exactly
-- the manufactured zero this refuses).
CREATE OR REPLACE FUNCTION cp_frozen_complete(f jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT f IS NOT NULL AND jsonb_typeof(f) = 'object' AND NOT EXISTS (
        SELECT 1 FROM unnest(ARRAY[
            'timestamp', 'sport', 'league', 'event', 'market', 'contract',
            'pinnacle_raw_odds', 'devigged_fair_probability',
            'other_model_probabilities', 'venue_bid', 'venue_ask', 'spread',
            'depth', 'freshness', 'theoretical_ev', 'executable_ev',
            'agent_recommendations', 'meta_controller_decision',
            'requested_size', 'approved_size', 'execution_policy',
            'order_details']) AS k
         WHERE NOT (f ? k)
            OR jsonb_typeof(f -> k) <> 'object'
            OR coalesce(f -> k ->> 'status', '') NOT IN (
                   'MEASURED', 'UNAVAILABLE', 'NOT_APPLICABLE')
            OR (f -> k ->> 'status' = 'MEASURED' AND NOT (f -> k ? 'value'))
            OR (f -> k ->> 'status' <> 'MEASURED'
                AND (f -> k ? 'value'
                     OR length(btrim(coalesce(f -> k ->> 'why', ''))) = 0)))
$$;

-- ── 1 · frozen decision snapshots ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS cp_decision_snapshots (
    snapshot_id              text NOT NULL,
    decision_id              text NOT NULL,
    opportunity_id           text NOT NULL,
    opportunity_key          text NOT NULL,
    intent_id                text,
    -- the decision instant (the clock the decision was taken at) and the
    -- instant the frozen content is AS OF: the decision's own record set
    -- (decision row, canonical intent, entry order) complete. Every input
    -- row used was recorded at or before it.
    decided_at               timestamptz NOT NULL,
    as_of                    timestamptz NOT NULL,
    evidence_max_recorded_at timestamptz,
    sport                    text,
    league                   text,
    event                    text,
    market                   text,
    contract                 jsonb NOT NULL,
    strategy                 text NOT NULL,
    strategy_version         text,
    sleeve                   text NOT NULL,
    verdict                  text NOT NULL,
    model_version            text,
    config_sha               text,
    code_sha                 text NOT NULL,
    frozen                   jsonb NOT NULL,
    frozen_sha               text NOT NULL,
    snapshot_version         text NOT NULL,
    source                   text NOT NULL,
    authority                text NOT NULL DEFAULT 'SHADOW',
    recorded_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cp_snap_pk PRIMARY KEY (snapshot_id, decision_id),
    CONSTRAINT cp_snap_one_per_decision_and_code UNIQUE (decision_id, code_sha),
    CONSTRAINT cp_snap_id_ck CHECK (snapshot_id ~ '^snap_[0-9a-f]{24}$'),
    CONSTRAINT cp_snap_opp_ck CHECK (opportunity_id ~ '^opp_[0-9a-f]{24}$'),
    -- fixture | us_market_slug | holding_side | line | scope
    CONSTRAINT cp_snap_opp_key_ck CHECK (
        opportunity_key ~ '^[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*$'),
    CONSTRAINT cp_snap_intent_ck CHECK (
        intent_id IS NULL OR intent_id ~ '^cdi_[0-9a-f]{24}$'),
    CONSTRAINT cp_snap_sha_ck CHECK (frozen_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cp_snap_code_ck CHECK (
        code_sha ~ '^[0-9a-f]{7,40}$' OR code_sha LIKE 'UNAVAILABLE:%'),
    CONSTRAINT cp_snap_clock_ck CHECK (as_of >= decided_at),
    -- THE FREEZE GUARD: no input recorded after the instant it is as of
    CONSTRAINT cp_snap_evidence_ck CHECK (
        evidence_max_recorded_at IS NULL OR evidence_max_recorded_at <= as_of),
    CONSTRAINT cp_snap_sleeve_ck CHECK (sleeve IN ('INVESTMENT', 'TRAINING',
                                                   'BENCHMARK', 'UNCLASSIFIED')),
    CONSTRAINT cp_snap_verdict_ck CHECK (verdict IN ('ENTER', 'REFUSE')),
    CONSTRAINT cp_snap_source_ck CHECK (source IN ('RECORDS_OFFLINE',
                                                   'SHADOW_HOOK')),
    CONSTRAINT cp_snap_shadow_ck CHECK (authority = 'SHADOW'),
    CONSTRAINT cp_snap_complete_ck CHECK (cp_frozen_complete(frozen -> 'fields')),
    -- the indexed columns say exactly what the frozen field says: NULL
    -- when (and only when) the field is not MEASURED
    CONSTRAINT cp_snap_sport_ck CHECK (
        (sport IS NULL) = (frozen #>> '{fields,sport,status}' <> 'MEASURED')),
    CONSTRAINT cp_snap_league_ck CHECK (
        (league IS NULL) = (frozen #>> '{fields,league,status}' <> 'MEASURED')),
    CONSTRAINT cp_snap_event_ck CHECK (
        (event IS NULL) = (frozen #>> '{fields,event,status}' <> 'MEASURED')),
    CONSTRAINT cp_snap_market_ck CHECK (
        (market IS NULL) = (frozen #>> '{fields,market,status}' <> 'MEASURED'))
);
CREATE INDEX IF NOT EXISTS cp_snap_decided_idx ON cp_decision_snapshots (decided_at DESC);
CREATE INDEX IF NOT EXISTS cp_snap_opp_idx ON cp_decision_snapshots (opportunity_id, decided_at);
CREATE INDEX IF NOT EXISTS cp_snap_strategy_idx ON cp_decision_snapshots (strategy, decided_at DESC);
CREATE INDEX IF NOT EXISTS cp_snap_as_of_idx ON cp_decision_snapshots (as_of);

-- ── 2 · post-trade observations ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cp_post_trade_observations (
    observation_id       text PRIMARY KEY,
    snapshot_id          text NOT NULL,
    decision_id          text NOT NULL,
    opportunity_id       text NOT NULL,
    horizon              text NOT NULL,
    horizon_clock        timestamptz NOT NULL,
    observation_version  integer NOT NULL DEFAULT 1,
    status               text NOT NULL,
    unavailable_reason   text,
    observed_at          timestamptz,
    evidence_recorded_at timestamptz,
    evidence             jsonb NOT NULL DEFAULT '{}'::jsonb,
    venue_bid            numeric(12,6),
    venue_ask            numeric(12,6),
    venue_mid            numeric(12,6),
    price_move           numeric(12,6),
    clv                  numeric(12,6),
    pnl_usd              numeric(18,6),
    metrics              jsonb NOT NULL,
    observer_version     text NOT NULL,
    code_sha             text NOT NULL,
    authority            text NOT NULL DEFAULT 'SHADOW',
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cp_pto_snapshot_fk FOREIGN KEY (snapshot_id, decision_id)
        REFERENCES cp_decision_snapshots (snapshot_id, decision_id),
    CONSTRAINT cp_pto_once UNIQUE (snapshot_id, decision_id, horizon,
                                   observation_version),
    CONSTRAINT cp_pto_id_ck CHECK (observation_id ~ '^cpo_[0-9a-f]{24}$'),
    CONSTRAINT cp_pto_horizon_ck CHECK (horizon IN (
        '+10s', '+30s', '+1m', '+5m', '+15m', 'close', 'settlement')),
    CONSTRAINT cp_pto_version_ck CHECK (observation_version >= 1),
    CONSTRAINT cp_pto_status_ck CHECK (status IN ('OBSERVED', 'UNAVAILABLE')),
    CONSTRAINT cp_pto_reason_ck CHECK (
        (status = 'UNAVAILABLE') = (unavailable_reason IS NOT NULL
                                    AND length(btrim(unavailable_reason)) > 0)),
    -- an unavailable observation carries no number (never a zero)
    CONSTRAINT cp_pto_unavailable_empty_ck CHECK (
        status = 'OBSERVED' OR (venue_bid IS NULL AND venue_ask IS NULL
                                AND venue_mid IS NULL AND price_move IS NULL
                                AND clv IS NULL AND pnl_usd IS NULL)),
    -- THE LEAKAGE GUARD: the evidence was observed AND recorded no later
    -- than the horizon's clock
    CONSTRAINT cp_pto_observed_ck CHECK (
        observed_at IS NULL OR observed_at <= horizon_clock),
    CONSTRAINT cp_pto_recorded_ck CHECK (
        evidence_recorded_at IS NULL OR evidence_recorded_at <= horizon_clock),
    CONSTRAINT cp_pto_evidence_named_ck CHECK (
        status = 'UNAVAILABLE' OR evidence_recorded_at IS NOT NULL),
    CONSTRAINT cp_pto_clv_ck CHECK (clv IS NULL OR horizon = 'close'),
    CONSTRAINT cp_pto_pnl_ck CHECK (pnl_usd IS NULL OR horizon = 'settlement'),
    CONSTRAINT cp_pto_shadow_ck CHECK (authority = 'SHADOW')
);
CREATE INDEX IF NOT EXISTS cp_pto_snapshot_idx ON cp_post_trade_observations (snapshot_id, decision_id);
CREATE INDEX IF NOT EXISTS cp_pto_horizon_idx ON cp_post_trade_observations (horizon, recorded_at DESC);

-- ── 3 · the paper replay harness: one run and its event stream ────────
CREATE TABLE IF NOT EXISTS cp_replay_runs (
    run_id            text PRIMARY KEY,
    account_id        text NOT NULL,
    inputs_sha        text NOT NULL,
    config            jsonb NOT NULL,
    config_sha        text NOT NULL,
    stages            jsonb NOT NULL,
    code_sha          text NOT NULL,
    clock_start       timestamptz NOT NULL,
    clock_end         timestamptz NOT NULL,
    starting_cash_usd numeric(18,6) NOT NULL,
    events            integer NOT NULL,
    output_sha        text NOT NULL,
    summary           jsonb NOT NULL,
    evidence_class    text NOT NULL DEFAULT 'RETROSPECTIVE_REPLAY',
    authority         text NOT NULL DEFAULT 'REPLAY_PAPER_ACCOUNT_ONLY',
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cp_rr_id_ck CHECK (run_id ~ '^cprun_[0-9a-f]{24}$'),
    -- the dedicated replay account: never the production paper account,
    -- never anything carrying the paper prefix
    CONSTRAINT cp_rr_account_ck CHECK (account_id ~ '^cpreplay_[0-9a-f]{24}$'),
    CONSTRAINT cp_rr_sha_ck CHECK (inputs_sha ~ '^[0-9a-f]{64}$'
                                   AND config_sha ~ '^[0-9a-f]{64}$'
                                   AND output_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cp_rr_clock_ck CHECK (clock_end >= clock_start),
    CONSTRAINT cp_rr_events_ck CHECK (events >= 0),
    -- retrospective replay is never forward evidence
    CONSTRAINT cp_rr_class_ck CHECK (evidence_class = 'RETROSPECTIVE_REPLAY'),
    CONSTRAINT cp_rr_authority_ck CHECK (authority = 'REPLAY_PAPER_ACCOUNT_ONLY')
);

CREATE TABLE IF NOT EXISTS cp_replay_events (
    run_id          text NOT NULL REFERENCES cp_replay_runs (run_id),
    seq             integer NOT NULL,
    clock           timestamptz NOT NULL,
    stage           text NOT NULL,
    kind            text NOT NULL,
    opportunity_id  text,
    snapshot_id     text,
    payload         jsonb NOT NULL,
    payload_sha     text NOT NULL,
    CONSTRAINT cp_re_pk PRIMARY KEY (run_id, seq),
    CONSTRAINT cp_re_seq_ck CHECK (seq >= 0),
    CONSTRAINT cp_re_sha_ck CHECK (payload_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cp_re_stage_ck CHECK (stage IN (
        'ranker', 'agents', 'meta', 'allocator', 'execution', 'risk',
        'fill', 'post_trade', 'settlement', 'counterfactual',
        'league_table', 'promotion', 'ledger'))
);

-- ── 4 · append-only triggers ────────────────────────────────────────────
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['cp_decision_snapshots',
                             'cp_post_trade_observations',
                             'cp_replay_runs', 'cp_replay_events'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION cp_e_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION cp_e_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;

-- ── 5 · the agents hold no authority over these records ────────────────
-- Migration 217's registry of guarded tables gains the four tables above.
-- The registry is EXTENDED, not re-declared from a copy: the control-plane
-- migrations 253..259 (and other R30 streams) land in parallel, and a
-- static re-declaration taken from 225's list would silently drop whatever
-- a sibling migration registered before this one ran. The function is
-- regenerated from its own current rows plus ours (ours replaced if a
-- re-run finds them), in its existing order; then the guard is attached
-- exactly as 217 / 225 attach it.
DO $$
DECLARE
    body text;
BEGIN
    IF to_regprocedure('pos_agents_authority_guarded_tables()') IS NULL THEN
        RETURN;
    END IF;
    SELECT string_agg(format('(%L, %L::text[], %L)', x.tbl, x.cols, x.kind),
                      E',\n      ' ORDER BY x.ord)
      INTO body
      FROM (SELECT g.tbl, g.cols, g.kind, g.ord
              FROM pos_agents_authority_guarded_tables()
                   WITH ORDINALITY AS g (tbl, cols, kind, ord)
             WHERE g.tbl NOT IN ('cp_decision_snapshots',
                                 'cp_post_trade_observations',
                                 'cp_replay_runs', 'cp_replay_events')
            UNION ALL
            SELECT n.tbl, n.cols, n.kind, 1000000 + n.ord
              FROM (VALUES ('cp_decision_snapshots', ARRAY[]::text[], 'DECISION', 1),
                           ('cp_post_trade_observations', ARRAY[]::text[], 'DECISION', 2),
                           ('cp_replay_runs', ARRAY[]::text[], 'DECISION', 3),
                           ('cp_replay_events', ARRAY[]::text[], 'DECISION', 4))
                   AS n (tbl, cols, kind, ord)) x;
    EXECUTE 'CREATE OR REPLACE FUNCTION pos_agents_authority_guarded_tables() '
            'RETURNS TABLE (tbl text, cols text[], kind text) LANGUAGE sql '
            'IMMUTABLE AS $reg$ VALUES ' || body || ' $reg$';
END $$;

DO $$
DECLARE
    r record;
BEGIN
    IF to_regproc('pos_agents_refuse_authority') IS NULL
       OR to_regprocedure('pos_agents_authority_guarded_tables()') IS NULL THEN
        RETURN;
    END IF;
    FOR r IN SELECT * FROM pos_agents_authority_guarded_tables()
              WHERE tbl IN ('cp_decision_snapshots', 'cp_post_trade_observations',
                            'cp_replay_runs', 'cp_replay_events')
    LOOP
        -- these tables carry no acting-identity column: the guard refuses
        -- a session declared as Eddie / Scout (no column arguments)
        EXECUTE format('DROP TRIGGER IF EXISTS aa_pos_agents_no_authority_trg '
                       'ON %I', r.tbl);
        EXECUTE format(
            'CREATE TRIGGER aa_pos_agents_no_authority_trg BEFORE INSERT OR '
            'UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION '
            'pos_agents_refuse_authority()', r.tbl);
    END LOOP;
END $$;

COMMENT ON TABLE cp_decision_snapshots IS
    'Control plane (stream E): the frozen decision-time state of every '
    'evaluated opportunity, sha256-stamped, SHADOW. Append-only.';
COMMENT ON TABLE cp_post_trade_observations IS
    'Control plane (stream E): post-trade horizons from RECORDED evidence '
    'only; the evidence recorded stamp is CHECKed <= the horizon clock.';
