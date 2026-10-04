-- ══════════════════════════════════════════════════════════════════════
-- 229 · RUNTIME LOOP HEALTH: one current row per recurring loop
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP (owner audit 2026-10-04, "Architecture / reliability"): capital-
-- critical recurring work runs as API lifespan tasks and workers loops, and
-- nothing recorded, per loop, when it last started, last SUCCEEDED, last
-- failed and with what. The evidence that a loop was alive was scattered
-- over service_heartbeats (some loops), ingestion_state keys (others), the
-- runners' own run tables (others) and nothing at all (the rest) -- and the
-- 2026-10-04 production readback shows what that costs: the bettor_state
-- loop raised on every heartbeat for hours and simply had no row, so its
-- silence read as nothing.
--
-- WHAT THIS ADDS. runtime_loop_health: ONE CURRENT ROW per (loop, process),
-- written by sportsassets/loop_health.record at a loop's start, at each
-- success and at each failure. It is TELEMETRY, the same class as
-- service_heartbeats (one row per service, overwritten), not a financial,
-- research or audit record: nothing reads it to decide, size, approve or
-- trade, and GET /api/command/loop-health is its only reader. The counters
-- only grow; a row is never deleted by the application.
--
-- INVARIANTS, CHECKED HERE: a positive cadence; a known process; a bounded
-- loop name; an error text only beside an error time and bounded; counters
-- never negative; detail a JSON object.

CREATE TABLE IF NOT EXISTS runtime_loop_health (
    loop_name        text        NOT NULL,
    process          text        NOT NULL,
    cadence_s        double precision NOT NULL,
    last_start_at    timestamptz,
    last_success_at  timestamptz,
    last_error_at    timestamptz,
    last_error       text,
    starts           bigint      NOT NULL DEFAULT 0,
    successes        bigint      NOT NULL DEFAULT 0,
    errors           bigint      NOT NULL DEFAULT 0,
    commit_sha       text,
    host             text,
    pid              integer,
    detail           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (loop_name, process),
    CONSTRAINT rlh_name_ck CHECK (loop_name ~ '^[a-z0-9_.]{1,64}$'),
    CONSTRAINT rlh_process_ck CHECK (process IN ('api', 'workers')),
    CONSTRAINT rlh_cadence_ck CHECK (cadence_s > 0
                                     AND cadence_s <= 7 * 86400),
    CONSTRAINT rlh_counts_ck CHECK (starts >= 0 AND successes >= 0
                                    AND errors >= 0),
    CONSTRAINT rlh_error_ck CHECK (
        (last_error IS NULL OR last_error_at IS NOT NULL)
        AND (last_error IS NULL OR length(last_error) <= 500)),
    CONSTRAINT rlh_detail_ck CHECK (jsonb_typeof(detail) = 'object')
);

-- the application never deletes a loop's row: only its own upsert writes it
CREATE OR REPLACE FUNCTION runtime_loop_health_no_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'runtime_loop_health: % refused (one current row per '
                    'loop; the row is overwritten, never removed)', TG_OP
        USING ERRCODE = 'restrict_violation';
END $$;
DROP TRIGGER IF EXISTS runtime_loop_health_no_delete_trg ON runtime_loop_health;
CREATE TRIGGER runtime_loop_health_no_delete_trg
    BEFORE DELETE ON runtime_loop_health
    FOR EACH ROW EXECUTE FUNCTION runtime_loop_health_no_delete();
