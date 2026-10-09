-- ══════════════════════════════════════════════════════════════════════
-- 365 · THE RESEARCH TRAINING SET'S CHANGE STAMP: a transactional counter
--       every change to a verified training record moves, so a cached
--       provenance verification is served only while it provably still
--       holds.
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (RC6 provenance, D2). paper_derek._context caches a session's
-- context -- the research model and its provenance verification included --
-- for CONTEXT_TTL_S = 300 s. A label corrected one second after a verified
-- context was not seen by any PAPER decision of that session until the TTL
-- lapsed: each decision used a model whose training set no longer
-- reproduced (tests/test_rc6_provenance_open_defects.py, D2).
--
-- THE CONTRACT THIS SERVES. A cached verification is served only when this
-- stamp, read AFTER the decision arrived, equals the stamp read BEFORE that
-- verification read the ledger (paper_derek._training_set_stamp). The stamp
-- moves, in the writer's own transaction, on every change that can alter
-- what a research model's verification reads:
--
--   derek_research_observations   any UPDATE or DELETE (the table is
--       append-only by trigger, migration 170; a write here is a repair
--       made with that trigger off -- exactly what the check exists for),
--       and TRUNCATE. An INSERT cannot alter a named record: observation_id
--       is the primary key and valuation_id is UNIQUE.
--   external_valuations           any UPDATE or DELETE of a row whose
--       outcome was ALREADY KNOWN (OLD.outcome_known), and TRUNCATE. A
--       verified training record's valuation is labelled, which requires
--       outcome_known (labelled_observations); so the first change to it
--       after a verification is a change to a row with OLD.outcome_known,
--       and moves the stamp. Every production writer of this table updates
--       only rows with outcome_known = FALSE (the outcome joins), so the
--       trigger does nothing on the hot path: its WHEN is false there and
--       no function runs.
--   bettor_funded_models          an UPDATE of a column the verification or
--       the scoring reads (training_provenance -- the stored digest the
--       records are compared with --, params, features, model_key, model_id,
--       estimator, kernel), any DELETE, and TRUNCATE. evaluation / state
--       updates do not move it.
--
-- WHY A COUNTER AND NOT A CONTENT FINGERPRINT. An md5 over the 28,303
-- named records (the prototype) costs ~257 ms of Postgres per cache hit at
-- production size and grows with every record; production makes up to 9
-- PAPER decisions in one second (2.3 s of database time per second). This
-- stamp is one primary-key row and two small catalog reads (well under a
-- millisecond), and it is maintained only by corrections, which no
-- production writer makes.
--
-- WHY IT CANNOT BE BYPASSED UNNOTICED.
--   * ENABLE ALWAYS: the triggers fire with session_replication_role =
--     replica too (the way the append-only trigger is bypassed for a
--     repair, and how logical replication applies rows).
--   * The stamp the reader compares includes, besides the counter: the
--     counter row's own version (xmin and ctid: a row deleted and
--     re-inserted, or edited by hand back to an old value, is a different
--     version), every trigger's catalog row (oid, enabled state, version)
--     and the trigger function's version (a
--     trigger disabled, dropped, re-created or its function replaced -- and
--     restored -- reads as a change), and each table's storage identity
--     (relfilenode: a rewrite, e.g. ALTER COLUMN TYPE ... USING, which
--     changes values without row triggers, reads as a change). A trigger
--     missing or not ENABLE ALWAYS makes the stamp NULL: nothing is served
--     from the cache.
--
-- ADDITIVE. One table, one function, six triggers; nothing existing is
-- altered. Rollback: rollback/365_research_training_set_change_stamp.down.sql
-- (the code then reads no stamp and never serves a cached verification).

-- `watched_tables` / `watched_triggers`: the six (table, trigger) pairs
-- below, pairwise. The reader checks each pair against the catalog (the
-- trigger exists ON that table, is ENABLE ALWAYS and calls the counter
-- function) and reads each table's storage identity, so it names no table
-- itself; a changed list is a changed row version, hence a changed stamp.
CREATE TABLE IF NOT EXISTS research_training_set_changes (
    scope            text        PRIMARY KEY,
    changes          bigint      NOT NULL DEFAULT 0,
    last_changed_at  timestamptz,
    last_change      jsonb,
    watched_tables   regclass[]  NOT NULL,
    watched_triggers text[]      NOT NULL,
    CONSTRAINT research_training_set_changes_scope_ck CHECK (
        scope = 'DEREK_RESEARCH_TRAINING_SET'),
    CONSTRAINT research_training_set_changes_count_ck CHECK (changes >= 0),
    CONSTRAINT research_training_set_changes_pairs_ck CHECK (
        cardinality(watched_tables) = cardinality(watched_triggers))
);

INSERT INTO research_training_set_changes (scope, watched_tables,
                                           watched_triggers)
VALUES ('DEREK_RESEARCH_TRAINING_SET',
        ARRAY['derek_research_observations'::regclass,
              'derek_research_observations'::regclass,
              'external_valuations'::regclass,
              'external_valuations'::regclass,
              'bettor_funded_models'::regclass,
              'bettor_funded_models'::regclass],
        ARRAY['research_training_set_observation_changed',
              'research_training_set_observations_truncated',
              'research_training_set_valuation_changed',
              'research_training_set_valuations_truncated',
              'research_training_set_model_changed',
              'research_training_set_models_truncated'])
ON CONFLICT (scope) DO NOTHING;

COMMENT ON TABLE research_training_set_changes IS
    'The research training set''s change stamp: moved, in the writer''s own '
    'transaction, by every UPDATE / DELETE / TRUNCATE that can alter a '
    'verified research model''s training records or its stored digest '
    '(migration 365). paper_derek serves a cached provenance verification '
    'only while the stamp is the one read before that verification.';

CREATE OR REPLACE FUNCTION research_training_set_changed()
RETURNS trigger AS $$
BEGIN
    UPDATE research_training_set_changes
       SET changes = changes + 1,
           last_changed_at = clock_timestamp(),
           last_change = jsonb_build_object('table', TG_TABLE_NAME,
                                            'op', TG_OP, 'level', TG_LEVEL)
     WHERE scope = 'DEREK_RESEARCH_TRAINING_SET';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- ── the research observations: every UPDATE / DELETE, and TRUNCATE ────
DROP TRIGGER IF EXISTS research_training_set_observation_changed
    ON derek_research_observations;
CREATE TRIGGER research_training_set_observation_changed
    AFTER UPDATE OR DELETE ON derek_research_observations
    FOR EACH ROW EXECUTE FUNCTION research_training_set_changed();
ALTER TABLE derek_research_observations
    ENABLE ALWAYS TRIGGER research_training_set_observation_changed;

DROP TRIGGER IF EXISTS research_training_set_observations_truncated
    ON derek_research_observations;
CREATE TRIGGER research_training_set_observations_truncated
    AFTER TRUNCATE ON derek_research_observations
    FOR EACH STATEMENT EXECUTE FUNCTION research_training_set_changed();
ALTER TABLE derek_research_observations
    ENABLE ALWAYS TRIGGER research_training_set_observations_truncated;

-- ── the labels and settlements: a row whose outcome was already known ──
DROP TRIGGER IF EXISTS research_training_set_valuation_changed
    ON external_valuations;
CREATE TRIGGER research_training_set_valuation_changed
    AFTER UPDATE OR DELETE ON external_valuations
    FOR EACH ROW WHEN (OLD.outcome_known)
    EXECUTE FUNCTION research_training_set_changed();
ALTER TABLE external_valuations
    ENABLE ALWAYS TRIGGER research_training_set_valuation_changed;

DROP TRIGGER IF EXISTS research_training_set_valuations_truncated
    ON external_valuations;
CREATE TRIGGER research_training_set_valuations_truncated
    AFTER TRUNCATE ON external_valuations
    FOR EACH STATEMENT EXECUTE FUNCTION research_training_set_changed();
ALTER TABLE external_valuations
    ENABLE ALWAYS TRIGGER research_training_set_valuations_truncated;

-- ── the registry: the stored digest and what the model scores with ─────
DROP TRIGGER IF EXISTS research_training_set_model_changed
    ON bettor_funded_models;
CREATE TRIGGER research_training_set_model_changed
    AFTER UPDATE OF training_provenance, params, features, model_key,
                    model_id, estimator, kernel
       OR DELETE ON bettor_funded_models
    FOR EACH ROW EXECUTE FUNCTION research_training_set_changed();
ALTER TABLE bettor_funded_models
    ENABLE ALWAYS TRIGGER research_training_set_model_changed;

DROP TRIGGER IF EXISTS research_training_set_models_truncated
    ON bettor_funded_models;
CREATE TRIGGER research_training_set_models_truncated
    AFTER TRUNCATE ON bettor_funded_models
    FOR EACH STATEMENT EXECUTE FUNCTION research_training_set_changed();
ALTER TABLE bettor_funded_models
    ENABLE ALWAYS TRIGGER research_training_set_models_truncated;
