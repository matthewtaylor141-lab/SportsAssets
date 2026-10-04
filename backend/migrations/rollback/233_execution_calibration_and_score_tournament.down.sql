-- ROLLBACK 233 · R30C execution evidence guard + the Opportunity Score
-- V1 / V2 shadow tournament. Refused while ANY tournament entry exists: the
-- scores were recorded at their decision instants and cannot be recomputed
-- without look-ahead, so they are never dropped as cleanup. With none,
-- drops only 233's objects (the small_live_order_events guard trigger and
-- function, the tournament table and its append-only and at-decision
-- functions); every 225 object is untouched.
DO $$
BEGIN
    IF to_regclass('opportunity_score_tournament') IS NOT NULL
       AND EXISTS (SELECT 1 FROM opportunity_score_tournament) THEN
        RAISE EXCEPTION 'ROLLBACK_233_REFUSED: opportunity_score_tournament '
                        'holds decision-time scores (cannot be recomputed '
                        'without look-ahead)';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('small_live_order_events') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS small_live_order_events_live_only_trg
            ON small_live_order_events;
    END IF;
END $$;
DROP TABLE IF EXISTS opportunity_score_tournament;
DROP FUNCTION IF EXISTS opportunity_tournament_at_decision();
DROP FUNCTION IF EXISTS opportunity_tournament_append_only();
DROP FUNCTION IF EXISTS small_live_order_event_live_only();
