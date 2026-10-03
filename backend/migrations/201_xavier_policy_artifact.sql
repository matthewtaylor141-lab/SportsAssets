-- ══════════════════════════════════════════════════════════════════════
-- 201 · VERSIONED AGENT POLICY ARTIFACTS, AND XAVIER_SMALL_LIVE_MANAGEMENT_V1
--       STORED READY_FOR_OWNER_APPROVAL (NOT APPROVED, NOT ACTIVATED)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHY. Production reports Xavier's policy_version as CODE_DEFAULT: no
-- ACTIVE agent_policy_versions row exists. A code default is a fallback, not
-- a management-approved policy. This migration stores the bounded small-live
-- management behaviour that is already implemented and tested as ONE
-- immutable, hashed document the owner can approve or reject
-- (agents/xavier_small_live_policy.py holds the same document; a test pins
-- this file's literal and hash against it).
--
-- WHY A NEW TABLE. agent_policy_versions (152) holds PARAMETERS that the
-- runtime LOADS: its ACTIVE state is binding. An artifact here is a
-- DOCUMENT that nothing loads; its status records a management decision and
-- never changes behaviour. Folding it into agent_policy_versions would make
-- "approved" and "binding" the same flip, which is exactly what must not
-- happen silently.
--
-- WHAT THE DATABASE ENFORCES.
--   * APPROVED only with an owner approval record: owner_approval_actor
--     (non-blank, never DEREK / XAVIER / AUDREY / AGENT*) and
--     owner_approved_at (CHECK). Approval fields exist only on APPROVED or
--     SUPERSEDED rows.
--   * The stored sha256 IS the SHA-256 of the stored canonical text, and the
--     jsonb document is that text (CHECK).
--   * No artifact carries a risk limit, capital authority, credential,
--     account authority, submission switch, approval or activation key
--     (CHECK on top-level keys).
--   * Content is immutable once stored; status moves only forward
--     (DRAFT -> READY -> APPROVED | REJECTED | SUPERSEDED, APPROVED ->
--     SUPERSEDED); a row is never deleted (trigger).
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / ON CONFLICT DO NOTHING.
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction together with its schema_migrations row.

CREATE TABLE IF NOT EXISTS agent_policy_artifacts (
    policy_id              text        NOT NULL,
    version                text        NOT NULL,
    agent_id               text        NOT NULL,
    title                  text        NOT NULL,
    canonical_json         text        NOT NULL,
    document               jsonb       NOT NULL,
    sha256                 text        NOT NULL,
    status                 text        NOT NULL,
    created_by             text        NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    owner_approval_actor   text,
    owner_approved_at      timestamptz,
    owner_approval_statement text,
    status_changed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_id, version),
    CONSTRAINT agent_policy_artifacts_agent_ck CHECK (
        agent_id IN ('DEREK', 'XAVIER', 'AUDREY')),
    CONSTRAINT agent_policy_artifacts_status_ck CHECK (status IN (
        'DRAFT', 'READY_FOR_OWNER_APPROVAL', 'APPROVED', 'REJECTED',
        'SUPERSEDED')),
    CONSTRAINT agent_policy_artifacts_sha_ck CHECK (
        sha256 ~ '^[0-9a-f]{64}$'
        AND sha256 = encode(sha256(convert_to(canonical_json, 'UTF8')),
                            'hex')),
    CONSTRAINT agent_policy_artifacts_document_ck CHECK (
        jsonb_typeof(document) = 'object'
        AND document = canonical_json::jsonb
        AND document->>'policy_id' = policy_id
        AND document->>'version' = version),
    -- A POLICY DESCRIPTION IS NOT A GRANT OF LIMITS OR AUTHORITY.
    CONSTRAINT agent_policy_artifacts_no_authority_ck CHECK (
        NOT (document ?| ARRAY[
            'risk_limits', 'limits', 'capital_usd', 'max_downside_usd',
            'max_incremental_capital_usd', 'per_order_usd', 'max_order_usd',
            'event_exposure_usd', 'daily_loss_stop_usd', 'credentials',
            'api_key', 'account_authority', 'capital_authority',
            'submission_enabled', 'approved', 'approved_by', 'approved_at',
            'activation', 'activate'])),
    -- APPROVED ONLY WITH AN OWNER APPROVAL RECORD (actor, approved_at).
    CONSTRAINT agent_policy_artifacts_owner_approval_ck CHECK (
        status <> 'APPROVED'
        OR (owner_approval_actor IS NOT NULL
            AND btrim(owner_approval_actor) <> ''
            AND owner_approved_at IS NOT NULL)),
    -- THE APPROVAL RECORD BELONGS ONLY TO AN APPROVED (OR LATER SUPERSEDED)
    -- ROW, AND ITS ACTOR IS NEVER AN AGENT.
    CONSTRAINT agent_policy_artifacts_approval_fields_ck CHECK (
        (owner_approval_actor IS NULL AND owner_approved_at IS NULL)
        OR status IN ('APPROVED', 'SUPERSEDED')),
    CONSTRAINT agent_policy_artifacts_actor_not_agent_ck CHECK (
        owner_approval_actor IS NULL
        OR (upper(btrim(owner_approval_actor)) NOT IN
                ('DEREK', 'XAVIER', 'AUDREY')
            AND upper(btrim(owner_approval_actor)) NOT LIKE 'AGENT%'))
);

CREATE OR REPLACE FUNCTION agent_policy_artifacts_guard() RETURNS trigger
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent_policy_artifacts: an artifact is never '
                        'deleted (%@%)', OLD.policy_id, OLD.version;
    END IF;
    IF NEW.policy_id IS DISTINCT FROM OLD.policy_id
       OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.agent_id IS DISTINCT FROM OLD.agent_id
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.canonical_json IS DISTINCT FROM OLD.canonical_json
       OR NEW.document IS DISTINCT FROM OLD.document
       OR NEW.sha256 IS DISTINCT FROM OLD.sha256
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'agent_policy_artifacts: the content of %@% is '
                        'immutable; a change is a new version',
                        OLD.policy_id, OLD.version;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'DRAFT'
            AND NEW.status IN ('READY_FOR_OWNER_APPROVAL', 'REJECTED'))
        OR (OLD.status = 'READY_FOR_OWNER_APPROVAL'
            AND NEW.status IN ('APPROVED', 'REJECTED', 'SUPERSEDED'))
        OR (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED')) THEN
        RAISE EXCEPTION 'agent_policy_artifacts: % -> % is not a permitted '
                        'status change for %@%', OLD.status, NEW.status,
                        OLD.policy_id, OLD.version;
    END IF;
    IF OLD.owner_approval_actor IS NOT NULL AND (
           NEW.owner_approval_actor IS DISTINCT FROM OLD.owner_approval_actor
           OR NEW.owner_approved_at IS DISTINCT FROM OLD.owner_approved_at)
    THEN
        RAISE EXCEPTION 'agent_policy_artifacts: the owner approval record '
                        'of %@% is write-once', OLD.policy_id, OLD.version;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_policy_artifacts_guard_trg
    ON agent_policy_artifacts;
CREATE TRIGGER agent_policy_artifacts_guard_trg
    BEFORE UPDATE OR DELETE ON agent_policy_artifacts
    FOR EACH ROW EXECUTE FUNCTION agent_policy_artifacts_guard();

-- ── XAVIER_SMALL_LIVE_MANAGEMENT_V1, READY_FOR_OWNER_APPROVAL ──────────
-- sha256 = c59f3957e01691787c3ebccfbb67b5b7786fcc76929f0ffcc52ba2a5e416375e
-- Inserted NOT approved: no owner approval record, and nothing reads this
-- row to change Xavier's behaviour.
INSERT INTO agent_policy_artifacts (policy_id, version, agent_id, title,
                                    canonical_json, document, sha256, status,
                                    created_by)
SELECT 'XAVIER_SMALL_LIVE_MANAGEMENT_V1', '1', 'XAVIER',
       'Xavier bounded small-live position management',
       d.txt, d.txt::jsonb, 'c59f3957e01691787c3ebccfbb67b5b7786fcc76929f0ffcc52ba2a5e416375e',
       'READY_FOR_OWNER_APPROVAL', 'migration 201'
  FROM (SELECT $policy${"agent_id":"XAVIER","approval_requirement":"Status becomes APPROVED only with an owner approval record (owner_approval_actor, owner_approved_at) on agent_policy_artifacts; no agent (DEREK, XAVIER, AUDREY) may be that actor; the content and its sha256 are immutable once stored.","authority_granted":"NONE","authority_statement":"This document grants no risk limit, no capital authority, no credential, no account authority and no submission switch. Approving it records management's acceptance of the described behaviour; it activates nothing by itself.","describes":"BEHAVIOUR_ALREADY_IMPLEMENTED_AND_TESTED","evidence_states":{"FRESH_CURRENT_PROBABILITY":"the measure is current AND its own source stamp is within the Pinnacle freshness limit (ext_pinnacle_loop.PINNACLE_MAX_AGE_S) at the review instant","PROBABILITY_UNAVAILABLE":"no probability at all; null, never 0, never invented","STALE_ENTRY_TIME_PROBABILITY":"the probability is older than the limit or is the entry decision's; it is labelled stale and stated with probability_limitation"},"policy_id":"XAVIER_SMALL_LIVE_MANAGEMENT_V1","rules":[{"id":"R1_FRESH_PINNAPI_EVIDENCE_REQUIRED_FOR_DISCRETIONARY_EV","implemented_by":["agents.paper_xavier.probability_evidence","agents.paper_xavier.B_STALE_MEASURE","agents.paper_xavier.live_position_evidence"],"statement":"A discretionary sale (EXIT / REDUCE) is ranked on expected value only when the review's measure.evidence_state is FRESH_CURRENT_PROBABILITY. On any other state no discretionary sale is ranked (blocker MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE).","tested_by":["tests/test_xavier_review_probability_freshness.py::test_a_fresh_valuation_for_the_same_contract_is_used_and_recorded","tests/test_xavier_review_probability_freshness.py::test_without_fresh_evidence_the_review_says_so_and_never_sells"]},{"id":"R2_STALE_PROBABILITY_IS_LABELLED_NEVER_CURRENT_EV","implemented_by":["agents.paper_xavier.probability_evidence"],"statement":"A STALE_ENTRY_TIME_PROBABILITY is labelled stale with its source, age and limit; its hold value appears only as entry_time_hold_value_usd and current_hold_value_usd is null. A stale probability never masquerades as current expected value.","tested_by":["tests/test_xavier_review_probability_freshness.py::test_the_three_states_and_no_placeholder_zero","tests/test_xavier_review_probability_freshness.py::test_a_current_claim_outside_the_limit_is_not_fresh","tests/test_xavier_review_probability_freshness.py::test_a_future_stamped_or_mis_mapped_row_is_never_fresh"]},{"id":"R3_EXECUTABLE_EXIT_AND_REDUCE_ALTERNATIVES_COMPARED","implemented_by":["agents.paper_xavier (review)","agents.xavier_policy.run"],"statement":"HOLD, EXIT and REDUCE are compared on the same settlement measure through agents.xavier_policy.run -> bettor_funded_decision.decide: EXIT is the walked proceeds of a sale into the OBSERVED bids after fees, REDUCE the same for half the position; with no executable depth the sale is not rankable (NO_EXECUTABLE_EXIT_DEPTH_IN_THE_OBSERVED_BOOK).","tested_by":["tests/test_xavier_review_probability_freshness.py::test_without_fresh_evidence_the_review_says_so_and_never_sells","tests/test_execmirror.py::test_exits_sell_the_same_fraction_of_live_inventory_and_never_more"]},{"id":"R4_STANDING_PROTECTION_IS_NOT_FILLED_PROTECTION","implemented_by":["execmirror.Mirror.xavier_live_reviews","execmirror_view.PROTECTION_RULE","agents.paper_xavier.protective_price"],"statement":"A resting protective order is not filled protection: the standing (resting) quantity and the filled protection quantity are recorded and shown separately and never added together; a protective floor is never realized P&L until a sale or settlement books it.","tested_by":["tests/test_execmirror.py::test_an_accepted_resting_order_is_not_a_fill","tests/test_execmirror.py::test_protection_waits_for_live_inventory_and_resizes_with_it","tests/test_small_live_view.py::test_an_actual_position_xavier_record_is_shown_when_one_exists"]},{"id":"R5_NO_INVENTED_HEDGE","implemented_by":["agents.paper_xavier.B_INDIRECT_NOT_SEARCHED","execmirror.live_entry_qty"],"statement":"No hedge is assumed or invented. On the paper book the indirect hedge search does not run, so the indirect hedge is NOT_RANKABLE with the search recorded INCOMPLETE (INDIRECT_HEDGE_SEARCH_NOT_RUN_ON_THE_PAPER_BOOK), never assumed absent or present. A mirrored HEDGE is live only for a group whose live ENTRY acquired inventory.","tested_by":["tests/test_execmirror.py::test_a_hedge_is_live_only_for_a_group_whose_entry_acquired_live_inventory"]},{"id":"R6_CROSS_VENUE_HEDGE_ONLY_WITH_PROVEN_SETTLEMENT_COMPATIBILITY","implemented_by":["bettor_funded_pair_cycle.discover","agents.xavier_ladder (settlement_compatibility)"],"statement":"A hedge on another contract or venue is admissible only when settlement/payoff compatibility is established (one grading variable, bettor_funded_pair_cycle.discover; settlement_compatibility COMPATIBLE_SAME_GRADING_KEY). Otherwise it is NOT_ESTABLISHED / THE_TWO_CONTRACTS_ARE_NOT_GRADED_BY_ONE_VARIABLE and not admitted.","tested_by":["tests/test_indirect_pair_priority.py::test_indirect_preference_does_not_override_incompatible_periods","tests/test_settlement_terms_govern_selection.py::test_a_payout_difference_under_one_condition_is_incompatible"]},{"id":"R7_UNAVAILABLE_STAYS_UNAVAILABLE_NO_FORCED_EXIT","implemented_by":["agents.paper_xavier.E_NONE","execmirror.Mirror._live_probability"],"statement":"PROBABILITY_UNAVAILABLE is null, never 0, and is never a reason to sell: a missing fresh probability alone never liquidates a position; cost-recovery protection (priced from quantity, basis and fees, never the probability) is unaffected. An unwired or failed probability reader records UNAVAILABLE and sells nothing.","tested_by":["tests/test_xavier_review_probability_freshness.py::test_an_unavailable_probability_is_null_and_liquidates_nothing","tests/test_xavier_review_probability_freshness.py::test_an_unwired_reader_records_unavailable_and_sells_nothing","tests/test_xavier_review_probability_freshness.py::test_the_actual_position_states_stale_evidence_and_is_not_sold"]},{"id":"R8_MANAGEMENT_ONLY_FOR_ACQUIRED_LIVE_INVENTORY","implemented_by":["execmirror.plan_sell","execmirror.live_entry_qty","execmirror.NO_LIVE_INVENTORY"],"statement":"EXIT / REDUCE / STANDING_PROTECTION are mirrored only against venue-confirmed live inventory not already committed to another live exit, and never sell what was not bought; a HEDGE needs live ENTRY inventory. Otherwise the mirror row is EXCLUDED NO_LIVE_INVENTORY (protection is re-planned when inventory arrives).","tested_by":["tests/test_execmirror.py::test_exits_follow_live_inventory_and_never_sell_what_was_not_bought","tests/test_execmirror.py::test_a_hedge_is_live_only_for_a_group_whose_entry_acquired_live_inventory"]}],"scope":{"actions":["HOLD","EXIT","REDUCE","STANDING_PROTECTION","HEDGE"],"applies_to":"actual (small-live) positions of the 1:1,000 execution mirror (execmirror) whose live ENTRY acquired venue-confirmed inventory, and the paper review that drives them (agents.paper_xavier)","not_covered":["new entries (Derek's lane)","the funded (non-mirror) lane's own management policy (agents.xavier_policy, policy key XAVIER_MANAGEMENT_POLICY)","any change to sizing, scale, order caps or account controls"]},"title":"Xavier bounded small-live position management","version":"1"}$policy$::text AS txt) d
ON CONFLICT (policy_id, version) DO NOTHING;
