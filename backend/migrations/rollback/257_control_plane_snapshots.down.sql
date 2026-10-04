-- ROLLBACK 257 · CONTROL PLANE STREAM E (frozen decision snapshots,
-- post-trade observations, the paper replay harness's scratch account).
-- Refused while ANY snapshot, observation or replay run exists: frozen
-- decision-time state is the learning record and is never dropped as
-- cleanup. With none, drops cleanly; migration 217's guard registry loses
-- exactly the four rows 257 added (regenerated from its own rows, so a
-- sibling migration's registrations survive).
DO $$
BEGIN
    IF (to_regclass('cp_decision_snapshots') IS NOT NULL
            AND EXISTS (SELECT 1 FROM cp_decision_snapshots))
       OR (to_regclass('cp_post_trade_observations') IS NOT NULL
            AND EXISTS (SELECT 1 FROM cp_post_trade_observations))
       OR (to_regclass('cp_replay_runs') IS NOT NULL
            AND EXISTS (SELECT 1 FROM cp_replay_runs)) THEN
        RAISE EXCEPTION 'ROLLBACK_257_REFUSED: control-plane snapshots, '
                        'observations or replay runs exist (learning record)';
    END IF;
END $$;

DO $$
DECLARE
    body text;
BEGIN
    IF to_regprocedure('pos_agents_authority_guarded_tables()') IS NULL THEN
        RETURN;
    END IF;
    SELECT string_agg(format('(%L, %L::text[], %L)', g.tbl, g.cols, g.kind),
                      E',\n      ' ORDER BY g.ord)
      INTO body
      FROM pos_agents_authority_guarded_tables()
           WITH ORDINALITY AS g (tbl, cols, kind, ord)
     WHERE g.tbl NOT IN ('cp_decision_snapshots', 'cp_post_trade_observations',
                         'cp_replay_runs', 'cp_replay_events');
    IF body IS NOT NULL THEN
        EXECUTE 'CREATE OR REPLACE FUNCTION pos_agents_authority_guarded_tables() '
                'RETURNS TABLE (tbl text, cols text[], kind text) LANGUAGE sql '
                'IMMUTABLE AS $reg$ VALUES ' || body || ' $reg$';
    END IF;
END $$;

DROP TABLE IF EXISTS cp_replay_events;
DROP TABLE IF EXISTS cp_replay_runs;
DROP TABLE IF EXISTS cp_post_trade_observations;
DROP TABLE IF EXISTS cp_decision_snapshots;
DROP FUNCTION IF EXISTS cp_frozen_complete(jsonb);
DROP FUNCTION IF EXISTS cp_e_append_only();
