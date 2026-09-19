-- THE PIPELINE WATCHES ITSELF, so nobody has to notice that one
-- counter says 31 and another says zero.
--
-- Owner directive 2026-09-19: "Add production health:
-- BETTOR_ORPHAN_OPPORTUNITIES ... This incident should never again
-- require noticing that one counter is 31 while another is zero."
--
-- WHAT WENT WRONG IS WORTH STATING PRECISELY, because the shape of the
-- failure dictates the shape of the guard. BETTOR observed 31
-- opportunities and wrote zero decisions for over an hour. Nothing was
-- broken in a way anything could see: the worker heartbeat said
-- tick_failed with an EMPTY problems list, the opportunity counter kept
-- rising, and the decision counter simply never moved. A failure that
-- is invisible to the health surface is a failure that runs until a
-- human happens to read two numbers side by side.
--
-- TWO OBJECTS, NOT ONE.
--
--   bettor_opportunity_annotations  append-only evidence ABOUT an
--       opportunity, written later, never touching the opportunity row.
--       "Keep the opportunities. Annotate them:
--       DECISION_NOT_RECORDED_DUE_TO_WRITER_INCIDENT."
--
--   bettor_decision_failures        every decision write that did not
--       land, with the database's own error text. "Every legitimate
--       opportunity should either have A DECISION or A NAMED FAILURE /
--       BLOCKER. No silent orphan opportunities."
--
-- AN ANNOTATION IS NOT A DECISION AND CANNOT BECOME ONE. It has no
-- action, no price, no size and no lane; it is a note about a gap. The
-- 31 opportunities stay exactly as prospective observations, and the
-- decisions BETTOR failed to make in that window stay unmade. "They may
-- NOT be presented as prospective shadow decisions."

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_opportunity_annotations (
    annotation_id           TEXT PRIMARY KEY,
    bettor_opportunity_id   TEXT NOT NULL
        REFERENCES bettor_opportunities (bettor_opportunity_id),
    annotation_kind         TEXT NOT NULL,
    incident                TEXT NOT NULL,
    detail                  TEXT,
    -- WHEN THE NOTE WAS MADE, which is emphatically not when the
    -- opportunity was observed. Conflating the two is how retrospective
    -- evidence ends up wearing a prospective timestamp.
    annotated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    observed_at             TIMESTAMPTZ NOT NULL,

    CONSTRAINT bettor_annotation_kind CHECK (annotation_kind IN (
        'DECISION_NOT_RECORDED_DUE_TO_WRITER_INCIDENT',
        'DECISION_NOT_RECORDED_REASON_NOT_IDENTIFIED')),
    -- The note is always made after the thing it is about.
    CONSTRAINT bettor_annotation_is_retrospective
        CHECK (annotated_at >= observed_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS bettor_annotation_once_idx
    ON bettor_opportunity_annotations
       (bettor_opportunity_id, annotation_kind, incident);

CREATE INDEX IF NOT EXISTS bettor_annotation_opportunity_idx
    ON bettor_opportunity_annotations (bettor_opportunity_id);

CREATE TABLE IF NOT EXISTS bettor_decision_failures (
    failure_id              TEXT PRIMARY KEY,
    bettor_opportunity_id   TEXT,
    symbol                  TEXT,
    stage                   TEXT NOT NULL,
    error_class             TEXT NOT NULL,
    -- THE DATABASE'S OWN WORDS, not a paraphrase. The whole reason this
    -- incident lasted an hour is that the worker swallowed the
    -- exception and reported an empty problems list.
    error_text              TEXT NOT NULL,
    failed_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT bettor_failure_stage CHECK (stage IN (
        'OPPORTUNITY_WRITE', 'DECISION_BUILD', 'DECISION_WRITE',
        'MARKET_STATE_WRITE', 'UNIVERSE_READ', 'NOT_IDENTIFIED'))
);

CREATE INDEX IF NOT EXISTS bettor_failure_at_idx
    ON bettor_decision_failures (failed_at DESC);

-- APPEND-ONLY, the same way every other shadow table is. The trigger
-- function already exists from migration 070.
DROP TRIGGER IF EXISTS bettor_opportunity_annotations_immutable
    ON bettor_opportunity_annotations;
CREATE TRIGGER bettor_opportunity_annotations_immutable
    BEFORE UPDATE OR DELETE ON bettor_opportunity_annotations
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

DROP TRIGGER IF EXISTS bettor_decision_failures_immutable
    ON bettor_decision_failures;
CREATE TRIGGER bettor_decision_failures_immutable
    BEFORE UPDATE OR DELETE ON bettor_decision_failures
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- THE ORPHAN DEFINITION LIVES IN ONE PLACE. A view, so the worker, the
-- COMMAND API and any research query cannot drift into three slightly
-- different ideas of what an orphan is -- which is exactly how a
-- health metric quietly stops meaning anything.
--
-- THE ALLOWANCE IS PART OF THE DEFINITION. The worker ticks every 60s
-- and decides within the same tick, so 180s is three chances. An
-- opportunity younger than that is still legitimately processing and is
-- NOT an orphan: "Do not classify an opportunity as orphan while its
-- decision is still legitimately processing."
CREATE OR REPLACE VIEW bettor_orphan_opportunities AS
SELECT o.bettor_opportunity_id,
       o.symbol,
       o.observed_at,
       round(extract(epoch FROM (now() - o.observed_at)))::bigint
           AS age_seconds,
       EXISTS (SELECT 1 FROM bettor_opportunity_annotations a
                WHERE a.bettor_opportunity_id = o.bettor_opportunity_id)
           AS annotated,
       EXISTS (SELECT 1 FROM bettor_decision_failures f
                WHERE f.bettor_opportunity_id = o.bettor_opportunity_id)
           AS failure_recorded
  FROM bettor_opportunities o
 WHERE o.observed_at < now() - interval '180 seconds'
   AND NOT EXISTS (SELECT 1 FROM shadow_decisions d
                    WHERE d.bettor_opportunity_id = o.bettor_opportunity_id);

COMMIT;
