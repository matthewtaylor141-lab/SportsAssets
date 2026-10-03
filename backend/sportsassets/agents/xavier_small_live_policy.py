"""XAVIER_SMALL_LIVE_MANAGEMENT_V1: A VERSIONED POLICY ARTIFACT, NOT ACTIVATED.

WHY THIS EXISTS. Production reports Xavier's `policy_version` as
CODE_DEFAULT (`agents.registry._policy_version_label`: no ACTIVE row in
`agent_policy_versions`). A code default is the code's fallback; it is NOT a
management-approved policy and must never be read as one. This module writes
down -- as ONE canonical, hashed document -- the bounded small-live
management behaviour that is ALREADY implemented and tested, so the owner
has a specific, immutable text to approve or reject.

WHAT IT IS NOT.
  * It is not an activation. Nothing in the runtime reads this document to
    decide anything; Xavier's behaviour is exactly what the code does today.
  * It carries no risk limit, capital authority, credential, account
    authority or submission switch (`FORBIDDEN_TOP_LEVEL_KEYS`, refused here
    and by the table's CHECK in migration 201).
  * Its stored status is READY_FOR_OWNER_APPROVAL. The table refuses
    APPROVED without an owner approval record (actor, approved_at), and
    refuses an agent as that actor. No code path in this repository writes
    APPROVED; even an APPROVED row would only RECORD the approval (`activated`
    stays False here) -- activating anything is a separate, reviewed change.

THE HASH. `sha256` is the SHA-256 of `canonical_json(DOCUMENT)` (sorted keys,
compact separators, ASCII). Migration 201 stores that exact text and the
table CHECKs that the stored hash is the hash of the stored text, so the
document the owner approves is byte-for-byte the one hashed here (a test pins
the migration's literal against this module).
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

POLICY_ID = "XAVIER_SMALL_LIVE_MANAGEMENT_V1"
VERSION = "1"
AGENT_ID = "XAVIER"
TABLE = "agent_policy_artifacts"

STATUS_DRAFT = "DRAFT"
STATUS_READY = "READY_FOR_OWNER_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUSES = (STATUS_DRAFT, STATUS_READY, STATUS_APPROVED, STATUS_REJECTED,
            STATUS_SUPERSEDED)

#: How the artifact was read for a payload.
SRC_STORED = "STORED_ARTIFACT"
SRC_CODE = "CODE_ARTIFACT_NOT_YET_STORED"
INTEGRITY_OK = "SHA256_MATCHES_CODE"
INTEGRITY_MISMATCH = "STORED_SHA256_DIFFERS_FROM_CODE"
INTEGRITY_NOT_STORED = "NOT_STORED"

#: Top-level keys no policy artifact may carry (the table CHECKs the same
#: list): a policy description is not a grant of limits or authority.
FORBIDDEN_TOP_LEVEL_KEYS = (
    "risk_limits", "limits", "capital_usd", "max_downside_usd",
    "max_incremental_capital_usd", "per_order_usd", "max_order_usd",
    "event_exposure_usd", "daily_loss_stop_usd", "credentials", "api_key",
    "account_authority", "capital_authority", "submission_enabled",
    "approved", "approved_by", "approved_at", "activation", "activate")

CODE_DEFAULT_MEANING = (
    "CODE_DEFAULT is the code's fallback because no ACTIVE "
    "agent_policy_versions row exists; it is NOT a management-approved "
    "policy")

_T = "tests/test_xavier_review_probability_freshness.py"
_TX = "tests/test_execmirror.py"
_TV = "tests/test_small_live_view.py"

#: THE POLICY DOCUMENT. Every rule names the code that implements it and the
#: tests that pin it: this artifact describes, it does not change.
DOCUMENT: dict[str, Any] = {
    "policy_id": POLICY_ID,
    "version": VERSION,
    "agent_id": AGENT_ID,
    "title": "Xavier bounded small-live position management",
    "describes": "BEHAVIOUR_ALREADY_IMPLEMENTED_AND_TESTED",
    "scope": {
        "applies_to": (
            "actual (small-live) positions of the 1:1,000 execution mirror "
            "(execmirror) whose live ENTRY acquired venue-confirmed "
            "inventory, and the paper review that drives them "
            "(agents.paper_xavier)"),
        "actions": ["HOLD", "EXIT", "REDUCE", "STANDING_PROTECTION",
                    "HEDGE"],
        "not_covered": [
            "new entries (Derek's lane)",
            "the funded (non-mirror) lane's own management policy "
            "(agents.xavier_policy, policy key XAVIER_MANAGEMENT_POLICY)",
            "any change to sizing, scale, order caps or account controls"],
    },
    "authority_granted": "NONE",
    "authority_statement": (
        "This document grants no risk limit, no capital authority, no "
        "credential, no account authority and no submission switch. "
        "Approving it records management's acceptance of the described "
        "behaviour; it activates nothing by itself."),
    "evidence_states": {
        "FRESH_CURRENT_PROBABILITY": (
            "the measure is current AND its own source stamp is within the "
            "Pinnacle freshness limit (ext_pinnacle_loop.PINNACLE_MAX_AGE_S) "
            "at the review instant"),
        "STALE_ENTRY_TIME_PROBABILITY": (
            "the probability is older than the limit or is the entry "
            "decision's; it is labelled stale and stated with "
            "probability_limitation"),
        "PROBABILITY_UNAVAILABLE": (
            "no probability at all; null, never 0, never invented"),
    },
    "rules": [
        {"id": "R1_FRESH_PINNAPI_EVIDENCE_REQUIRED_FOR_DISCRETIONARY_EV",
         "statement": (
             "A discretionary sale (EXIT / REDUCE) is ranked on expected "
             "value only when the review's measure.evidence_state is "
             "FRESH_CURRENT_PROBABILITY. On any other state no discretionary "
             "sale is ranked (blocker "
             "MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE)."),
         "implemented_by": [
             "agents.paper_xavier.probability_evidence",
             "agents.paper_xavier.B_STALE_MEASURE",
             "agents.paper_xavier.live_position_evidence"],
         "tested_by": [
             _T + "::test_a_fresh_valuation_for_the_same_contract_is_used_"
                  "and_recorded",
             _T + "::test_without_fresh_evidence_the_review_says_so_and_"
                  "never_sells"]},
        {"id": "R2_STALE_PROBABILITY_IS_LABELLED_NEVER_CURRENT_EV",
         "statement": (
             "A STALE_ENTRY_TIME_PROBABILITY is labelled stale with its "
             "source, age and limit; its hold value appears only as "
             "entry_time_hold_value_usd and current_hold_value_usd is null. "
             "A stale probability never masquerades as current expected "
             "value."),
         "implemented_by": ["agents.paper_xavier.probability_evidence"],
         "tested_by": [
             _T + "::test_the_three_states_and_no_placeholder_zero",
             _T + "::test_a_current_claim_outside_the_limit_is_not_fresh",
             _T + "::test_a_future_stamped_or_mis_mapped_row_is_never_fresh"]},
        {"id": "R3_EXECUTABLE_EXIT_AND_REDUCE_ALTERNATIVES_COMPARED",
         "statement": (
             "HOLD, EXIT and REDUCE are compared on the same settlement "
             "measure through agents.xavier_policy.run -> "
             "bettor_funded_decision.decide: EXIT is the walked proceeds of "
             "a sale into the OBSERVED bids after fees, REDUCE the same for "
             "half the position; with no executable depth the sale is not "
             "rankable (NO_EXECUTABLE_EXIT_DEPTH_IN_THE_OBSERVED_BOOK)."),
         "implemented_by": ["agents.paper_xavier (review)",
                            "agents.xavier_policy.run"],
         "tested_by": [
             _T + "::test_without_fresh_evidence_the_review_says_so_and_"
                  "never_sells",
             _TX + "::test_exits_sell_the_same_fraction_of_live_inventory_"
                   "and_never_more"]},
        {"id": "R4_STANDING_PROTECTION_IS_NOT_FILLED_PROTECTION",
         "statement": (
             "A resting protective order is not filled protection: the "
             "standing (resting) quantity and the filled protection "
             "quantity are recorded and shown separately and never added "
             "together; a protective floor is never realized P&L until a "
             "sale or settlement books it."),
         "implemented_by": [
             "execmirror.Mirror.xavier_live_reviews",
             "execmirror_view.PROTECTION_RULE",
             "agents.paper_xavier.protective_price"],
         "tested_by": [
             _TX + "::test_an_accepted_resting_order_is_not_a_fill",
             _TX + "::test_protection_waits_for_live_inventory_and_resizes_"
                   "with_it",
             _TV + "::test_an_actual_position_xavier_record_is_shown_when_"
                   "one_exists"]},
        {"id": "R5_NO_INVENTED_HEDGE",
         "statement": (
             "No hedge is assumed or invented. On the paper book the "
             "indirect hedge search does not run, so the indirect hedge is "
             "NOT_RANKABLE with the search recorded INCOMPLETE "
             "(INDIRECT_HEDGE_SEARCH_NOT_RUN_ON_THE_PAPER_BOOK), never "
             "assumed absent or present. A mirrored HEDGE is live only for "
             "a group whose live ENTRY acquired inventory."),
         "implemented_by": [
             "agents.paper_xavier.B_INDIRECT_NOT_SEARCHED",
             "execmirror.live_entry_qty"],
         "tested_by": [
             _TX + "::test_a_hedge_is_live_only_for_a_group_whose_entry_"
                   "acquired_live_inventory"]},
        {"id": "R6_CROSS_VENUE_HEDGE_ONLY_WITH_PROVEN_SETTLEMENT_"
               "COMPATIBILITY",
         "statement": (
             "A hedge on another contract or venue is admissible only when "
             "settlement/payoff compatibility is established (one grading "
             "variable, bettor_funded_pair_cycle.discover; "
             "settlement_compatibility COMPATIBLE_SAME_GRADING_KEY). "
             "Otherwise it is NOT_ESTABLISHED / "
             "THE_TWO_CONTRACTS_ARE_NOT_GRADED_BY_ONE_VARIABLE and not "
             "admitted."),
         "implemented_by": [
             "bettor_funded_pair_cycle.discover",
             "agents.xavier_ladder (settlement_compatibility)"],
         "tested_by": [
             "tests/test_indirect_pair_priority.py::test_indirect_"
             "preference_does_not_override_incompatible_periods",
             "tests/test_settlement_terms_govern_selection.py::test_a_"
             "payout_difference_under_one_condition_is_incompatible"]},
        {"id": "R7_UNAVAILABLE_STAYS_UNAVAILABLE_NO_FORCED_EXIT",
         "statement": (
             "PROBABILITY_UNAVAILABLE is null, never 0, and is never a "
             "reason to sell: a missing fresh probability alone never "
             "liquidates a position; cost-recovery protection (priced from "
             "quantity, basis and fees, never the probability) is "
             "unaffected. An unwired or failed probability reader records "
             "UNAVAILABLE and sells nothing."),
         "implemented_by": [
             "agents.paper_xavier.E_NONE",
             "execmirror.Mirror._live_probability"],
         "tested_by": [
             _T + "::test_an_unavailable_probability_is_null_and_"
                  "liquidates_nothing",
             _T + "::test_an_unwired_reader_records_unavailable_and_sells_"
                  "nothing",
             _T + "::test_the_actual_position_states_stale_evidence_and_is_"
                  "not_sold"]},
        {"id": "R8_MANAGEMENT_ONLY_FOR_ACQUIRED_LIVE_INVENTORY",
         "statement": (
             "EXIT / REDUCE / STANDING_PROTECTION are mirrored only against "
             "venue-confirmed live inventory not already committed to "
             "another live exit, and never sell what was not bought; a "
             "HEDGE needs live ENTRY inventory. Otherwise the mirror row is "
             "EXCLUDED NO_LIVE_INVENTORY (protection is re-planned when "
             "inventory arrives)."),
         "implemented_by": ["execmirror.plan_sell",
                            "execmirror.live_entry_qty",
                            "execmirror.NO_LIVE_INVENTORY"],
         "tested_by": [
             _TX + "::test_exits_follow_live_inventory_and_never_sell_what_"
                   "was_not_bought",
             _TX + "::test_a_hedge_is_live_only_for_a_group_whose_entry_"
                   "acquired_live_inventory"]},
    ],
    "approval_requirement": (
        "Status becomes APPROVED only with an owner approval record "
        "(owner_approval_actor, owner_approved_at) on agent_policy_artifacts; "
        "no agent (DEREK, XAVIER, AUDREY) may be that actor; the content and "
        "its sha256 are immutable once stored."),
}


def canonical_json(doc: dict) -> str:
    """The exact text that is hashed and stored. Pure."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def sha256_of(doc: dict) -> str:
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()


def forbidden_keys(doc: dict) -> list:
    return sorted(k for k in dict(doc or {}) if k in FORBIDDEN_TOP_LEVEL_KEYS)


assert not forbidden_keys(DOCUMENT)

SHA256 = sha256_of(DOCUMENT)


def document() -> dict:
    return copy.deepcopy(DOCUMENT)


def artifact() -> dict:
    """The artifact as code declares it: id, version, hash, document and the
    status it is stored with. Pure."""
    return {"policy_id": POLICY_ID, "version": VERSION, "agent_id": AGENT_ID,
            "sha256": SHA256, "status": STATUS_READY,
            "canonical_json": canonical_json(DOCUMENT),
            "document": document()}


def _view(*, status: str, source: str, integrity: str, stored_sha=None,
          approval: dict | None = None, why: str | None = None) -> dict:
    """What a payload carries. `approved` is true only for a stored APPROVED
    row whose hash matches the code; `activated` is ALWAYS false: no runtime
    path reads this artifact to change behaviour."""
    approved = (status == STATUS_APPROVED and integrity == INTEGRITY_OK)
    return {"policy_id": POLICY_ID, "version": VERSION, "sha256": SHA256,
            "status": status, "source": source, "integrity": integrity,
            "stored_sha256": stored_sha, "approved": approved,
            "owner_approval": approval if approved else None,
            "activated": False,
            "governs_runtime": False,
            "authority_granted": "NONE",
            "runtime_behaviour": (
                "unchanged: Xavier runs the implemented code; this artifact "
                "describes it for owner approval and activates nothing"),
            "code_default_meaning": CODE_DEFAULT_MEANING,
            "why": why,
            "href": "/api/command/agents/xavier/management-policy"}


def code_view(*, why: str | None = None) -> dict:
    return _view(status=STATUS_READY, source=SRC_CODE,
                 integrity=INTEGRITY_NOT_STORED, why=why)


async def load_view(conn) -> dict:
    """THE ARTIFACT'S STATE FOR A PAYLOAD. Never raises.

    The stored row when migration 201 is applied (its hash checked against
    the code's); else the code's declaration, labelled not stored and
    READY_FOR_OWNER_APPROVAL. Reads only."""
    try:
        if await conn.fetchval("SELECT to_regclass($1)", TABLE) is None:
            return code_view(why="MIGRATION_201_NOT_APPLIED")
        r = await conn.fetchrow(
            "SELECT status, sha256, owner_approval_actor, owner_approved_at "
            "  FROM agent_policy_artifacts WHERE policy_id=$1 AND version=$2",
            POLICY_ID, VERSION)
    except Exception as exc:                                    # noqa: BLE001
        return code_view(why="ARTIFACT_READ_FAILED:%s" % type(exc).__name__)
    if r is None:
        return code_view(why="NO_STORED_ROW")
    integrity = (INTEGRITY_OK if r["sha256"] == SHA256
                 else INTEGRITY_MISMATCH)
    at = r["owner_approved_at"]
    return _view(status=str(r["status"]), source=SRC_STORED,
                 integrity=integrity, stored_sha=r["sha256"],
                 approval={"actor": r["owner_approval_actor"],
                           "approved_at": (at.timestamp()
                                           if hasattr(at, "timestamp")
                                           else at)},
                 why=(None if integrity == INTEGRITY_OK else
                      "the stored text is not this code's document"))


async def read_artifact(conn) -> dict:
    """THE FULL ARTIFACT (document + state) for the read-only route."""
    view = await load_view(conn)
    return dict(view, document=document(),
                canonical_json_sha256=SHA256,
                hash_rule=("sha256 of the canonical JSON: sorted keys, "
                           "separators (',', ':'), ASCII"))
