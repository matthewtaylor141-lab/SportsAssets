-- ══════════════════════════════════════════════════════════════════════
-- 226 · THE AGENTS' FRESH-EVIDENCE WORK QUEUE AND ONE CURRENT REVIEW PER
--       POSITION
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (owner R30, 2026-10-04). "Xavier is NOT idle when he owns open
-- positions." 59 paper groups are handed to Xavier; the latest assessment of
-- 27 of them is WAITING_FOR_FRESH_EVIDENCE -- and a review on stale evidence
-- enqueued NOTHING: the next look at the position was the 60 s backstop,
-- which found the same stale evidence again. Meanwhile the floor read
-- Xavier IDLE (only FUNDED inventory counted as owned), and "the current
-- review" of a position was whichever row a reader happened to sort first.
--
-- WHAT THIS ADDS
--
--   agent_work_requests        ONE concrete evidence-acquisition request,
--                              append-only: which agent asked, for which
--                              position, of which KIND --
--                                PROBABILITY             a fresh PinnAPI /
--                                                        Pinnacle probability
--                                                        (the held/reactive
--                                                        re-evaluation path)
--                                VENUE_BOOK              the venue's current
--                                                        marks / depth (a
--                                                        priority book read)
--                                GAME_STATE              the fixture's
--                                                        current reported
--                                                        state (fixture
--                                                        identity required)
--                                MANAGEMENT_REASSESSMENT Xavier's re-review
--                                                        once evidence lands
--                              -- why (the review state that raised it and
--                              the review id), and when it expires.
--   agent_work_request_events  its lifecycle, append-only: ENQUEUED (once,
--                              first), DISPATCHED (at most once: what the
--                              acquisition path answered), then exactly one
--                              terminal COMPLETED (naming the evidence row
--                              it produced: kind + id, required) or FAILED
--                              (naming why, required). Nothing follows a
--                              terminal event.
--   agent_work_open            THE DEDUPE: at most one OPEN request per
--                              (agent, position, kind) -- the primary key.
--                              A row is inserted with its request and
--                              removed ONLY by the terminal event's own
--                              trigger (a DELETE without a terminal event
--                              is refused; UPDATE is refused). It is an
--                              index of open work, not history: the history
--                              is the two tables above.
--   xavier_current_review      EXACTLY ONE CURRENT REVIEW PER POSITION
--                              (primary key position_kind + group_id). It is
--                              advanced by an AFTER INSERT trigger on
--                              paper_xavier_reviews (PAPER) and on
--                              xavier_management_assessments (ACTUAL), so the
--                              pointer moves in the SAME transaction as the
--                              review insert, by construction -- no caller
--                              can write a review and forget the pointer.
--                              Newest wins (reviewed_at, then id); it never
--                              moves backwards (the guard refuses) and is
--                              never deleted. It references the review row
--                              by foreign key. Both review tables stay
--                              append-only and untouched.
--
-- BOUNDED BY CONSTRUCTION: one open request per position and kind, each with
-- an expiry (expires_at > enqueued_at, at most one hour); the per-pass caps
-- live in sportsassets.agents.work_queue.
--
-- NO CAPITAL AUTHORITY: records only. Nothing here places, cancels or sizes
-- an order, and no freshness, EV, risk or settlement threshold is defined or
-- changed (the freshness rule that strips EXIT / REDUCE on stale evidence is
-- unchanged: a re-review still decides only on fresh evidence).
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP TRIGGER IF EXISTS /
-- ON CONFLICT DO NOTHING; running it twice changes nothing.

-- ── 0 · shared guards ────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION agent_work_record_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (work history is never '
                    'rewritten)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

-- ── 1 · THE REQUESTS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_work_requests (
    request_id        text        PRIMARY KEY,
    agent_id          text        NOT NULL,
    kind              text        NOT NULL,
    position_kind     text        NOT NULL,
    group_id          text        NOT NULL,
    us_market_slug    text,
    fixture_identity  text,
    reason            text        NOT NULL,
    source_table      text,
    source_id         text,
    batch_id          text        NOT NULL,
    enqueued_at       timestamptz NOT NULL,
    expires_at        timestamptz NOT NULL,
    detail            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    production_effect text        NOT NULL DEFAULT 'NONE',
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_work_requests_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT')),
    CONSTRAINT agent_work_requests_kind_ck CHECK (kind IN (
        'PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
        'MANAGEMENT_REASSESSMENT')),
    CONSTRAINT agent_work_requests_position_kind_ck CHECK (
        position_kind IN ('PAPER', 'ACTUAL')),
    CONSTRAINT agent_work_requests_group_ck CHECK (
        length(btrim(group_id)) BETWEEN 1 AND 200),
    -- what raised it: a review that could not decide on fresh evidence
    CONSTRAINT agent_work_requests_reason_ck CHECK (reason IN (
        'WAITING_FOR_FRESH_EVIDENCE', 'MANAGEMENT_UNAVAILABLE_STALE_INPUT')),
    -- the slug a book / probability request acts on is named
    CONSTRAINT agent_work_requests_slug_ck CHECK (
        kind NOT IN ('PROBABILITY', 'VENUE_BOOK')
        OR length(btrim(coalesce(us_market_slug, ''))) >= 1),
    -- a game-state request exists only where a fixture identity exists
    CONSTRAINT agent_work_requests_fixture_ck CHECK (
        kind <> 'GAME_STATE'
        OR length(btrim(coalesce(fixture_identity, ''))) >= 1),
    CONSTRAINT agent_work_requests_expiry_ck CHECK (
        expires_at > enqueued_at
        AND expires_at <= enqueued_at + interval '1 hour'),
    CONSTRAINT agent_work_requests_detail_ck CHECK (
        jsonb_typeof(detail) = 'object'),
    CONSTRAINT agent_work_requests_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS agent_work_requests_position_idx
    ON agent_work_requests (agent_id, position_kind, group_id, kind,
                            enqueued_at DESC);
CREATE INDEX IF NOT EXISTS agent_work_requests_batch_idx
    ON agent_work_requests (batch_id);
DROP TRIGGER IF EXISTS agent_work_requests_append_only_trg
    ON agent_work_requests;
CREATE TRIGGER agent_work_requests_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_work_requests
    FOR EACH ROW EXECUTE FUNCTION agent_work_record_is_append_only();

-- ── 2 · THEIR LIFECYCLE ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_work_request_events (
    event_id       bigserial   PRIMARY KEY,
    request_id     text        NOT NULL
                   REFERENCES agent_work_requests (request_id),
    state          text        NOT NULL,
    at             timestamptz NOT NULL,
    evidence_table text,
    evidence_id    text,
    failure        text,
    detail         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_work_events_state_ck CHECK (state IN (
        'ENQUEUED', 'DISPATCHED', 'COMPLETED', 'FAILED')),
    -- COMPLETED names the evidence it produced; nothing else may
    CONSTRAINT agent_work_events_evidence_ck CHECK (
        (state = 'COMPLETED'
         AND length(btrim(coalesce(evidence_table, ''))) >= 1
         AND length(btrim(coalesce(evidence_id, ''))) >= 1)
        OR (state <> 'COMPLETED' AND evidence_table IS NULL
            AND evidence_id IS NULL)),
    -- FAILED names why; nothing else carries a failure
    CONSTRAINT agent_work_events_failure_ck CHECK (
        (state = 'FAILED' AND length(btrim(coalesce(failure, ''))) >= 1)
        OR (state <> 'FAILED' AND failure IS NULL)),
    CONSTRAINT agent_work_events_detail_ck CHECK (
        jsonb_typeof(detail) = 'object')
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_work_events_one_enqueued
    ON agent_work_request_events (request_id) WHERE state = 'ENQUEUED';
CREATE UNIQUE INDEX IF NOT EXISTS agent_work_events_one_dispatch
    ON agent_work_request_events (request_id) WHERE state = 'DISPATCHED';
CREATE UNIQUE INDEX IF NOT EXISTS agent_work_events_one_terminal
    ON agent_work_request_events (request_id)
    WHERE state IN ('COMPLETED', 'FAILED');
CREATE INDEX IF NOT EXISTS agent_work_events_at_idx
    ON agent_work_request_events (at DESC);

-- order: ENQUEUED first, nothing after a terminal event
CREATE OR REPLACE FUNCTION agent_work_event_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state <> 'ENQUEUED' AND NOT EXISTS (
            SELECT 1 FROM agent_work_request_events
             WHERE request_id = NEW.request_id AND state = 'ENQUEUED') THEN
        RAISE EXCEPTION 'agent_work_request_events: % before ENQUEUED for %',
            NEW.state, NEW.request_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM agent_work_request_events
                WHERE request_id = NEW.request_id
                  AND state IN ('COMPLETED', 'FAILED')) THEN
        RAISE EXCEPTION 'agent_work_request_events: % is closed; % refused',
            NEW.request_id, NEW.state
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_work_events_guard_trg
    ON agent_work_request_events;
CREATE TRIGGER agent_work_events_guard_trg
    BEFORE INSERT ON agent_work_request_events
    FOR EACH ROW EXECUTE FUNCTION agent_work_event_guard();
DROP TRIGGER IF EXISTS agent_work_events_append_only_trg
    ON agent_work_request_events;
CREATE TRIGGER agent_work_events_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_work_request_events
    FOR EACH ROW EXECUTE FUNCTION agent_work_record_is_append_only();

-- ── 3 · THE ONE OPEN REQUEST PER (AGENT, POSITION, KIND) ─────────────
CREATE TABLE IF NOT EXISTS agent_work_open (
    agent_id      text        NOT NULL,
    position_kind text        NOT NULL,
    group_id      text        NOT NULL,
    kind          text        NOT NULL,
    request_id    text        NOT NULL UNIQUE
                  REFERENCES agent_work_requests (request_id),
    opened_at     timestamptz NOT NULL,
    PRIMARY KEY (agent_id, position_kind, group_id, kind)
);

CREATE OR REPLACE FUNCTION agent_work_open_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'agent_work_open: UPDATE refused (a request is '
                        'closed by its terminal event, never re-pointed)'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_work_request_events
                    WHERE request_id = OLD.request_id
                      AND state IN ('COMPLETED', 'FAILED')) THEN
        RAISE EXCEPTION 'agent_work_open: % has no terminal event; DELETE '
                        'refused', OLD.request_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
END;
$$;
DROP TRIGGER IF EXISTS agent_work_open_guard_trg ON agent_work_open;
CREATE TRIGGER agent_work_open_guard_trg
    BEFORE UPDATE OR DELETE ON agent_work_open
    FOR EACH ROW EXECUTE FUNCTION agent_work_open_guard();

-- a terminal event closes its request's open slot, in the same statement
CREATE OR REPLACE FUNCTION agent_work_event_close()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state IN ('COMPLETED', 'FAILED') THEN
        DELETE FROM agent_work_open WHERE request_id = NEW.request_id;
    END IF;
    RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS agent_work_events_close_trg
    ON agent_work_request_events;
CREATE TRIGGER agent_work_events_close_trg
    AFTER INSERT ON agent_work_request_events
    FOR EACH ROW EXECUTE FUNCTION agent_work_event_close();

-- ── 4 · EXACTLY ONE CURRENT REVIEW PER POSITION ──────────────────────
CREATE TABLE IF NOT EXISTS xavier_current_review (
    position_kind        text        NOT NULL,
    group_id             text        NOT NULL,
    review_table         text        NOT NULL,
    paper_review_id      text
        REFERENCES paper_xavier_reviews (review_id),
    assessment_id        text
        REFERENCES xavier_management_assessments (assessment_id),
    reviewed_at          timestamptz NOT NULL,
    recommendation       text,
    recommendation_state text,
    advances             integer     NOT NULL DEFAULT 1,
    advanced_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (position_kind, group_id),
    CONSTRAINT xavier_current_review_kind_ck CHECK (
        position_kind IN ('PAPER', 'ACTUAL')),
    -- a PAPER position's review is its paper review; an ACTUAL position's
    -- is its management assessment -- exactly one reference, the right one
    CONSTRAINT xavier_current_review_ref_ck CHECK (
        (review_table = 'paper_xavier_reviews' AND position_kind = 'PAPER'
         AND paper_review_id IS NOT NULL AND assessment_id IS NULL)
        OR (review_table = 'xavier_management_assessments'
            AND position_kind = 'ACTUAL'
            AND assessment_id IS NOT NULL AND paper_review_id IS NULL)),
    CONSTRAINT xavier_current_review_advances_ck CHECK (advances >= 1)
);

-- never backwards, never re-keyed, never deleted
CREATE OR REPLACE FUNCTION xavier_current_review_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'xavier_current_review: DELETE refused (a position '
                        'always keeps its one current review)'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.position_kind <> OLD.position_kind
       OR NEW.group_id <> OLD.group_id THEN
        RAISE EXCEPTION 'xavier_current_review: the position key is fixed'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.reviewed_at < OLD.reviewed_at THEN
        RAISE EXCEPTION 'xavier_current_review: % / % may not move back to '
                        'an older review', OLD.position_kind, OLD.group_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS xavier_current_review_guard_trg
    ON xavier_current_review;
CREATE TRIGGER xavier_current_review_guard_trg
    BEFORE UPDATE OR DELETE ON xavier_current_review
    FOR EACH ROW EXECUTE FUNCTION xavier_current_review_guard();

-- the pointer advances in the review's own transaction (newest wins:
-- reviewed_at, then the id, so equal instants still have one winner)
CREATE OR REPLACE FUNCTION xavier_current_review_advance_paper()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO xavier_current_review AS c (
        position_kind, group_id, review_table, paper_review_id,
        reviewed_at, recommendation, recommendation_state)
    VALUES ('PAPER', NEW.group_id, 'paper_xavier_reviews', NEW.review_id,
            NEW.reviewed_at, NEW.recommendation,
            NEW.selection ->> 'recommendation_state')
    ON CONFLICT (position_kind, group_id) DO UPDATE SET
        paper_review_id = EXCLUDED.paper_review_id,
        reviewed_at = EXCLUDED.reviewed_at,
        recommendation = EXCLUDED.recommendation,
        recommendation_state = EXCLUDED.recommendation_state,
        advances = c.advances + 1, advanced_at = now()
    WHERE (EXCLUDED.reviewed_at, EXCLUDED.paper_review_id)
          > (c.reviewed_at, c.paper_review_id);
    RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS paper_xavier_reviews_current_trg
    ON paper_xavier_reviews;
CREATE TRIGGER paper_xavier_reviews_current_trg
    AFTER INSERT ON paper_xavier_reviews
    FOR EACH ROW EXECUTE FUNCTION xavier_current_review_advance_paper();

CREATE OR REPLACE FUNCTION xavier_current_review_advance_assessment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.position_kind <> 'ACTUAL' THEN
        -- a PAPER assessment records the paper review it belongs to; the
        -- paper review is that position's current review
        RETURN NULL;
    END IF;
    INSERT INTO xavier_current_review AS c (
        position_kind, group_id, review_table, assessment_id,
        reviewed_at, recommendation, recommendation_state)
    VALUES ('ACTUAL', NEW.group_id, 'xavier_management_assessments',
            NEW.assessment_id, NEW.assessed_at, NEW.recommendation,
            NEW.recommendation_state)
    ON CONFLICT (position_kind, group_id) DO UPDATE SET
        assessment_id = EXCLUDED.assessment_id,
        reviewed_at = EXCLUDED.reviewed_at,
        recommendation = EXCLUDED.recommendation,
        recommendation_state = EXCLUDED.recommendation_state,
        advances = c.advances + 1, advanced_at = now()
    WHERE (EXCLUDED.reviewed_at, EXCLUDED.assessment_id)
          > (c.reviewed_at, c.assessment_id);
    RETURN NULL;
END;
$$;
DROP TRIGGER IF EXISTS xavier_management_assessments_current_trg
    ON xavier_management_assessments;
CREATE TRIGGER xavier_management_assessments_current_trg
    AFTER INSERT ON xavier_management_assessments
    FOR EACH ROW EXECUTE FUNCTION xavier_current_review_advance_assessment();

-- the existing history, once: each position's newest review becomes its
-- current review (ON CONFLICT DO NOTHING -- a second run changes nothing)
INSERT INTO xavier_current_review (
    position_kind, group_id, review_table, paper_review_id, reviewed_at,
    recommendation, recommendation_state)
SELECT DISTINCT ON (group_id) 'PAPER', group_id, 'paper_xavier_reviews',
       review_id, reviewed_at, recommendation,
       selection ->> 'recommendation_state'
  FROM paper_xavier_reviews
 ORDER BY group_id, reviewed_at DESC, review_id DESC
ON CONFLICT (position_kind, group_id) DO NOTHING;
INSERT INTO xavier_current_review (
    position_kind, group_id, review_table, assessment_id, reviewed_at,
    recommendation, recommendation_state)
SELECT DISTINCT ON (group_id) 'ACTUAL', group_id,
       'xavier_management_assessments', assessment_id, assessed_at,
       recommendation, recommendation_state
  FROM xavier_management_assessments WHERE position_kind = 'ACTUAL'
 ORDER BY group_id, assessed_at DESC, assessment_id DESC
ON CONFLICT (position_kind, group_id) DO NOTHING;
