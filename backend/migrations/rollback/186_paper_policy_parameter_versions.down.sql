-- down for 186. Refuses while any parameter version beyond the shipped
-- default, or any activation / rollback beyond its seed, exists (they are
-- the audit trail of what the paper agent ran under). Otherwise it drops the
-- head, the activation log and the versions; the completed-game policy then
-- runs on its shipped default (the decision path falls back to V1 when the
-- tables are absent).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM paper_policy_parameter_versions
                WHERE source <> 'SHIPPED_DEFAULT')
       OR EXISTS (SELECT 1 FROM paper_policy_parameter_activations
                   WHERE kind <> 'SHIPPED_DEFAULT') THEN
        RAISE EXCEPTION 'paper policy parameter versions or activations '
                        'exist; 186 is not rolled back over them';
    END IF;
    DROP TABLE IF EXISTS paper_policy_parameter_heads;
    DROP TABLE IF EXISTS paper_policy_parameter_activations;
    DROP TABLE IF EXISTS paper_policy_parameter_versions;
END $$;
