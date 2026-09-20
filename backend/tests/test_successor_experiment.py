"""THE SUCCESSOR, AND WHY X1 V1 IS CLOSED TO NEW POSITIONS.

Owner directive 2026-09-20, "DO NOT RETROFIT MISSING EXIT SEMANTICS
INTO X1 V1":

    "X1 V1 was frozen without specifying: (1) residual handling when
    the exit book cannot absorb the full position; (2) an admissible
    exit-book timing/delay rule. The first X1 cohort's outcome is
    already known. Therefore choosing those rules now and applying them
    to X1 V1 would define realized economics after seeing the
    evidence. Do not do that."

WHAT THESE TESTS EXIST TO CATCH, in order of how expensive the mistake
would be:

  V1'S FROZEN HASH MOVING. 77c930248609d057 is stored on four
  production positions and every decision row X1 ever wrote. If adding
  exit semantics to the declaration format changed it, `verify_all()`
  would report X1 as an experiment whose rules were edited under a live
  run -- destroying the one alarm that makes the freeze meaningful.

  THE SUCCESSOR INHERITING A TUNED ENTRY. "Do not change the
  signal/threshold merely because X1 V1 lost." The entry half must be
  the same expressions, from the same constants.

  THE DELAY BOUND BEING FITTED. §5 forbids deriving it from X1 P&L,
  X1 markouts, X1C, or which book would have exited best. It comes
  from measured capture cadence and from nothing else.

  A HALF-SPECIFIED SUCCESSOR. V1 satisfied every required field and
  still could not close a position. A successor missing one exit
  sub-rule would look complete in a listing and fail the same way.
"""

from __future__ import annotations

import io
import tokenize

import pytest

from sportsassets import shadow_exit_spec as xspec
from sportsassets import shadow_experiment_registry as reg
from sportsassets import shadow_experiment_signals as sig
from sportsassets import shadow_experiment_versions as ver
from sportsassets import shadow_experimental_engine as eng
from sportsassets import shadow_experimental_store as xstore
from sportsassets import shadow_experiments as xp
from sportsassets import shadow_markout_observability as ob


def code_only(module) -> str:
    """Source with comments and docstrings stripped.

    These modules explain at length what they refuse to do, so a naive
    substring scan finds "X1 profit" in the prose that promises never
    to read it.
    """
    import inspect
    src = inspect.getsource(module)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            out.append(tok.string)
    return " ".join(out)


# ── the frozen hashes, which must not have moved ─────────────────────

FROZEN = {
    "X1_SHORT_HORIZON_DIRECTION": "77c930248609d057",
    "X1C_NULL_CONTROL": "c7f539217424e5d6",
    "X2_RELATIVE_VALUE": "9c2d39996f85beab",
    "X3_MICROPRICE": "9672b96a71b1314b",
    "X4_ORDER_FLOW_IMBALANCE": "76899be7b91d8123",
    "X5_EXTERNAL_DISAGREEMENT": "08f5156bbbfc88ae",
}


@pytest.mark.parametrize("experiment_id,sha", sorted(FROZEN.items()))
def test_an_existing_declaration_keeps_its_hash(experiment_id, sha):
    """Adding exit semantics to the format must not touch a version
    that does not declare them. X1's hash is on production rows."""
    assert reg.BY_ID[experiment_id]["experimentSha"] == sha


def test_the_registry_still_verifies():
    report = reg.registry_report()
    assert report["hashesVerified"] is True
    assert report["mismatched"] == []


def test_an_existing_declaration_carries_no_exit_semantics_keys():
    """Not merely the same hash -- the same BODY. A key added with a
    None value would be invisible in a diff of hashes only if the hash
    were computed differently, so the absence is asserted directly."""
    x1 = reg.BY_ID["X1_SHORT_HORIZON_DIRECTION"]
    for field in xp.EXIT_SEMANTICS_FIELDS:
        assert field not in x1, field
    for field in xp.EXIT_SEMANTICS_OPTIONAL_FIELDS:
        assert field not in x1, field


# ── §1: V1 is closed to new positions, and not because it lost ───────


def test_x1_v1_may_not_create_new_positions():
    out = ver.position_creation("X1_SHORT_HORIZON_DIRECTION",
                                declaration=reg.X1)
    assert out["permitted"] is False
    assert out["decision"] == ver.EXIT_SEMANTICS_INCOMPLETE
    assert out["blocks"] == ver.BLOCKS == "POSITION_CREATION_ONLY"


def test_the_control_is_closed_with_its_candidate():
    """X1C carries the IDENTICAL frozen exit rule, so it has the
    identical incomplete lifecycle -- and a control that kept opening
    positions after its candidate stopped would no longer be a control,
    it would be a second experiment on a different population."""
    out = ver.position_creation("X1C_NULL_CONTROL", declaration=reg.X1C)
    assert out["permitted"] is False
    assert out["decision"] == ver.EXIT_SEMANTICS_INCOMPLETE
    assert reg.X1C["exitRule"] == reg.X1["exitRule"]


def test_no_blocker_is_performance_based():
    """§1: 'This is not a performance-based stop.' Asserted on every
    blocker, not just the two written today."""
    report = ver.report(reg.EXPERIMENTS)
    assert report["noBlockerIsPerformanceBased"] is True
    for row in ver.POSITION_CREATION_BLOCKED.values():
        assert row["performanceBased"] is False


def test_the_blocker_cannot_read_a_profit_and_loss():
    """The strongest available form of 'not performance-based': the
    module has no access to an outcome at all."""
    code = code_only(ver)
    for forbidden in ("pnl", "markout", "realized", "profit", "loss",
                      "executed_notional", "entry_notional"):
        assert forbidden not in code.lower(), forbidden


def test_blocking_did_not_change_the_declaration():
    """§1's blocker lives OUTSIDE the hashed declaration. Flipping
    readiness to RETIRED would have changed X1's hash and raised the
    'rules edited under a live run' alarm falsely."""
    assert reg.X1["readiness"] == xp.ARMED
    assert reg.X1["experimentSha"] == FROZEN["X1_SHORT_HORIZON_DIRECTION"]


def test_a_blocker_stops_positions_and_not_observations():
    report = ver.report(reg.EXPERIMENTS)
    assert "POSITION CREATION only" in report["note"].replace(
        "POSITION CREATION only", "POSITION CREATION only")
    assert "observations" in report["note"]
    assert "decisions" in report["note"]


# ── §2: the successor exists, complete, and is not armed ─────────────


def test_the_successor_is_declared_and_supersedes_v1():
    assert reg.X1V2["experimentId"] == "X1_SHORT_HORIZON_DIRECTION_V2"
    assert reg.X1V2["supersedes"] == "X1_SHORT_HORIZON_DIRECTION"
    assert reg.X1V2["experimentSha"] != reg.X1["experimentSha"]


def test_v1_was_not_overwritten():
    """'Do not overwrite X1 V1.' Both exist, separately."""
    assert "X1_SHORT_HORIZON_DIRECTION" in reg.BY_ID
    assert "X1_SHORT_HORIZON_DIRECTION_V2" in reg.BY_ID
    assert reg.X1 is not reg.X1V2


def test_the_successor_has_a_complete_lifecycle():
    assert xp.lifecycle_completeness(reg.X1V2) == xp.LIFECYCLE_COMPLETE
    assert xp.lifecycle_completeness(reg.X1CV2) == xp.LIFECYCLE_COMPLETE
    assert xspec.completeness()["lifecycleCompletable"] is True
    assert xspec.completeness()["missing"] == []


def test_v1_does_not_have_a_complete_lifecycle():
    """The contrast is the finding, so it is asserted rather than
    implied."""
    assert xp.lifecycle_completeness(reg.X1) == \
        xp.LIFECYCLE_EXIT_INCOMPLETE


def test_the_successor_is_not_armed():
    """§10: 'Then stop for review before the successor opens its first
    position.' Fully specified and allowed to trade are different
    facts."""
    assert reg.X1V2["readiness"] == xp.DECLARED_AWAITING_REVIEW
    assert reg.X1CV2["readiness"] == xp.DECLARED_AWAITING_REVIEW
    armed = [e["experimentId"] for e in reg.armed()]
    assert "X1_SHORT_HORIZON_DIRECTION_V2" not in armed
    assert "X1C_NULL_CONTROL_V2" not in armed


def test_the_successor_may_not_create_a_position_yet():
    """V2 WAS reviewed, and rejected (owner 2026-09-20). So its blocker
    is no longer AWAITING_REVIEW -- the answer is known. The
    awaiting-review case now belongs to V3."""
    out = ver.position_creation("X1_SHORT_HORIZON_DIRECTION_V2",
                                declaration=reg.X1V2)
    assert out["permitted"] is False
    assert out["decision"] == ver.NOT_APPROVED
    assert out["performanceBased"] is False

    pending = ver.position_creation("X1_SHORT_HORIZON_DIRECTION_V3",
                                    declaration=reg.X1V3)
    assert pending["permitted"] is False
    assert pending["decision"] == ver.AWAITING_REVIEW
    assert pending["performanceBased"] is False


def test_nothing_at_all_may_open_a_position_today():
    """The whole registry, checked in one place: after this directive
    there is no experiment version permitted to create a position."""
    permitted = [e["experimentId"] for e in reg.EXPERIMENTS
                 if ver.position_creation(e["experimentId"],
                                          declaration=e)["permitted"]]
    assert permitted == []


# ── §2: the entry half is carried forward, NOT retuned ───────────────


def test_the_successor_signal_is_the_same_function():
    assert reg.SIGNAL_FOR["X1_SHORT_HORIZON_DIRECTION_V2"] is sig.M1
    assert reg.SIGNAL_FOR["X1_SHORT_HORIZON_DIRECTION"] is sig.M1
    assert reg.SIGNAL_FOR["X1C_NULL_CONTROL_V2"] is sig.C0


def test_the_successor_entry_rule_is_identical():
    assert reg.X1V2["entryRule"] == reg.X1["entryRule"]
    assert reg.X1V2["directionRule"] == reg.X1["directionRule"]


def test_the_successor_threshold_and_horizon_are_identical():
    assert reg.X1V2["horizon"] == reg.X1["horizon"] == "60S"
    assert reg.X1V2["target"] == reg.X1["target"]
    assert reg.X1V2["intendedNotionalUsd"] == reg.X1["intendedNotionalUsd"]
    assert reg.X1V2["featureSet"] == reg.X1["featureSet"]
    assert reg.X1V2["modelVersion"] == reg.X1["modelVersion"]
    # and the constants behind the rule string have not been edited
    assert ("%.4f" % sig.M1_ENTRY_THRESHOLD) in reg.X1V2["directionRule"]
    assert ("%.2f" % sig.M1_MAX_SPREAD_RELATIVE) in reg.X1V2["entryRule"]


def test_only_the_exit_differs():
    """Everything hashed, compared field by field. The only expected
    differences are the exit contract, the identity of the version and
    its own freeze instant."""
    expected = {
        "experimentId", "policyVersion", "startTimestamp", "notes",
        "exitRule", "experimentSha", "supersedes",
        # V1 is ARMED (and separately blocked from new positions);
        # the successor awaits review. Both are expected.
        "readiness",
    } | set(xp.EXIT_SEMANTICS_FIELDS) | set(
        xp.EXIT_SEMANTICS_OPTIONAL_FIELDS)
    differ = {k for k in set(reg.X1) | set(reg.X1V2)
              if reg.X1.get(k) != reg.X1V2.get(k)}
    assert differ <= expected, differ - expected


# ── §5: the delay bound, and where it came from ──────────────────────


def test_the_delay_bound_is_the_measured_grid_maximum():
    import math
    assert xspec.MAX_EXIT_OBSERVATION_DELAY_S == math.ceil(ob.CAPTURE_MAX_S)
    assert xspec.MAX_EXIT_OBSERVATION_DELAY_MS == 65_000


def test_the_delay_bound_is_never_below_the_measured_grid():
    """A bound under the measured maximum spacing would refuse books
    for sampler-cadence reasons rather than market reasons."""
    assert xspec.MAX_EXIT_OBSERVATION_DELAY_S >= ob.CAPTURE_MAX_S
    assert xspec.MAX_EXIT_OBSERVATION_DELAY_S >= ob.CAPTURE_P95_S
    assert xspec.MAX_EXIT_OBSERVATION_DELAY_S >= ob.CAPTURE_P50_S


def test_the_delay_basis_names_its_source_and_its_exclusions():
    basis = xspec.MAX_EXIT_DELAY_BASIS
    assert "OPERATIONAL_CAPTURE_GRID_PERIOD" in basis
    assert str(ob.CAPTURE_MAX_S) in basis
    assert ob.CAPTURE_REGIME in basis
    # the exclusions §5 lists, stated on the record
    for excluded in ("X1 P&L", "X1 markouts", "X1C"):
        assert excluded in basis, excluded


def test_the_exit_spec_cannot_read_an_outcome():
    """§5 in the strongest available form: the module that derives the
    bound has no access to a P&L, a markout or a position."""
    code = code_only(xspec)
    # IT READS EXACTLY ONE EXTERNAL THING: the capture telemetry. That
    # module's name contains "markout" -- it measures the CAPTURE GRID
    # the markouts are taken on, not any markout's value -- so the scan
    # removes the import before looking for outcome words.
    assert "shadow_markout_observability" in code
    code = code.replace("shadow_markout_observability", "").lower()
    for forbidden in ("pnl", "markout", "realized", "profit",
                      "position", "notional"):
        assert forbidden not in code, forbidden
    # and every name it reads off that module is grid telemetry
    reads = {t for t in code_only(xspec).split() if t.startswith("CAPTURE_")}
    assert reads and all(
        r in ("CAPTURE_MAX_S", "CAPTURE_P50_S", "CAPTURE_P95_S",
              "CAPTURE_MEASURED_AT", "CAPTURE_MEASURED_BY",
              "CAPTURE_REGIME") for r in reads), reads


def test_the_bound_may_not_be_widened_in_place():
    rule = xspec.MAX_EXIT_DELAY_REVISION_RULE
    assert "NEW experiment version" in rule
    assert "never widened in place" in rule


# ── §3/§4: quantity, partials, residual, retry, timing ───────────────


def test_the_intended_exit_quantity_is_everything_remaining():
    assert "ALL REMAINING OPEN QUANTITY" in xspec.EXIT_INTENDED_QTY_RULE


def test_depth_is_never_invented():
    assert "NO MORE THAN ACTUAL AVAILABLE DEPTH" in \
        xspec.PARTIAL_EXIT_RULE
    assert "never invented to force a full exit" in \
        xspec.PARTIAL_EXIT_RULE


def test_the_residual_is_an_obligation_not_a_hold():
    assert "EXIT OBLIGATION" in xspec.RESIDUAL_RULE
    assert "not discretionary holding" in xspec.RESIDUAL_RULE
    assert "never re-classified as a held position" in xspec.RESIDUAL_RULE


def test_the_retry_rule_ends_at_flat_and_only_then_closes():
    assert "REMAINING_QTY reaches 0" in xspec.EXIT_RETRY_RULE
    assert "POSITION_CLOSED" in xspec.EXIT_RETRY_RULE
    assert xspec.NO_ADMISSIBLE_BOOK in xspec.EXIT_RETRY_RULE
    assert "not interpolated, not closed" in xspec.EXIT_RETRY_RULE


def test_a_late_book_is_never_relabelled_as_on_time():
    """§4: 'Do not relabel a late book as though it occurred exactly at
    +60s.' Three instants, recorded separately."""
    for field in ("EXIT_TARGET_TIMESTAMP", "EXIT_BOOK_TIMESTAMP",
                  "EXIT_OBSERVATION_DELAY_MS"):
        assert field in xspec.EXIT_EVIDENCE_FIELDS, field
        assert field in xspec.EXIT_TIMING_RULE, field
    assert "never relabelled" in xspec.EXIT_TIMING_RULE


def test_exceeding_the_bound_does_not_close_the_position():
    assert xspec.NO_ADMISSIBLE_BOOK == "NOT_IDENTIFIED_NO_ADMISSIBLE_BOOK"
    assert "no " in xspec.EXIT_TIMING_RULE
    assert "interpolation" in xspec.EXIT_TIMING_RULE
    assert "is NOT closed" in xspec.EXIT_TIMING_RULE


def test_an_outstanding_residual_at_close_is_not_a_settled_pnl():
    """Settlement semantics on this venue are CONFLICTING_VENUE_PROSE
    and unresolved, so they cannot be used to close an exit."""
    assert xspec.EXIT_INCOMPLETE_AT_CLOSE == "EXIT_INCOMPLETE_AT_MARKET_CLOSE"
    assert "NOT converted into a settled P&L" in xspec.EXIT_AT_CLOSE_RULE
    assert "CONFLICTING_VENUE_PROSE" in xspec.EXIT_AT_CLOSE_RULE


# ── §6: one authoritative anchor ─────────────────────────────────────


def test_the_successor_anchors_on_arrival():
    assert xspec.EXIT_ANCHOR == "ARRIVAL"
    assert xspec.EXIT_ANCHOR_COLUMN == "modeled_arrival_timestamp"
    assert reg.X1V2["exitAnchor"] == "ARRIVAL"
    assert "T0 + %ds" % xspec.EXIT_HORIZON_S in xspec.EXIT_ANCHOR_RULE


def test_the_anchor_matches_the_frozen_economic_statement():
    """'mid at +60s versus mid at arrival' -- the successor's anchor is
    read off that sentence, not chosen freshly."""
    assert "arrival" in reg.X1V2["target"]
    assert xspec.EXIT_HORIZON_S == 60
    assert reg.X1V2["horizon"] == "60S"


def test_v1s_anchor_disagreement_was_not_silently_resolved():
    """§6: 'Do not silently choose between them for X1 V1.' V1 still
    carries no anchor field at all -- the disagreement is named in
    shadow_exit_semantics and left standing."""
    assert "exitAnchor" not in reg.X1


# ── the declaration format's own guards ──────────────────────────────


def test_a_half_specified_exit_is_refused():
    """V1 satisfied every required field and still could not close a
    position. A successor missing one sub-rule must not slip through."""
    partial = dict(xspec.exit_semantics())
    partial.pop("residualRule")
    with pytest.raises(xp.ExperimentRefusal) as exc:
        xp.declare(
            experiment_id="X_BROKEN", policy_version="P",
            model_version="m", feature_set=("microstructure.mid",),
            target="t", horizon="60S", direction_rule="d",
            entry_rule="e", exit_rule="x",
            pairing_rule="p", cashout_rule="c",
            latency_assumption="l",
            start_timestamp="2026-09-20T00:00:00Z",
            exit_semantics=partial,
            readiness=xp.DECLARED_AWAITING_REVIEW)
    # the refusal must name the field, or a reader cannot fix it
    assert "residualRule" in str(exc.value) or "exit semantics" in str(
        exc.value)


def test_a_mistyped_exit_field_is_refused_not_ignored():
    with pytest.raises(xp.ExperimentRefusal) as exc:
        xp.declare(
            experiment_id="X_TYPO", policy_version="P", model_version="m",
            feature_set=("microstructure.mid",), target="t",
            horizon="60S", direction_rule="d", entry_rule="e",
            exit_rule="x", pairing_rule="p", cashout_rule="c",
            latency_assumption="l", start_timestamp="2026-09-20T00:00:00Z",
            exit_semantics=dict(xspec.exit_semantics(),
                                exitRetryRuel="typo"))
    assert "unknown exit semantics" in str(exc.value)


def test_a_version_with_no_exit_semantics_is_blocked_by_the_backstop():
    """A version added later that forgets its exit contract is refused
    even if nobody remembers to list it in the blocker table."""
    incomplete = xp.declare(
        experiment_id="X_FUTURE", policy_version="P", model_version="m",
        feature_set=("microstructure.mid",), target="t", horizon="60S",
        direction_rule="d", entry_rule="e", exit_rule="x",
        pairing_rule="p", cashout_rule="c", latency_assumption="l",
        start_timestamp="2026-09-20T00:00:00Z", readiness=xp.ARMED)
    out = ver.position_creation("X_FUTURE", declaration=incomplete)
    assert out["permitted"] is False
    assert out["decision"] == ver.EXIT_SEMANTICS_INCOMPLETE


# ── the blocked decision is recorded, and carries no position ────────


def test_a_version_blocked_entry_carries_no_position_or_economics():
    execution = {"experimentalDecisionId": "xdec_1",
                 "positionId": "xpos_1", "executionStatus": eng.PARTIAL,
                 "executedNotionalUsd": 920.0,
                 "unfilledNotionalUsd": 80.0, "filledQty": 3000.0,
                 "vwap": 0.306667, "l2BookSha": "96b9207fe6737ec6"}
    verdict = ver.position_creation("X1_SHORT_HORIZON_DIRECTION",
                                    declaration=reg.X1)
    out = xstore.refused_execution(execution, verdict,
                                   status=eng.BLOCKED_VERSION)
    assert out["executionStatus"] == eng.BLOCKED_VERSION
    assert out["positionId"] is None
    assert out["executedNotionalUsd"] is None
    assert out["filledQty"] is None
    # the book stays: it was really observed
    assert out["l2BookSha"] == "96b9207fe6737ec6"
    # and the caller's dict is untouched
    assert execution["positionId"] == "xpos_1"


def test_an_unenumerated_refusal_status_is_refused():
    """Migration 087's invariant covers a named list. A status outside
    it would escape the constraint, so the writer refuses first."""
    with pytest.raises(ValueError):
        xstore.refused_execution({"experimentalDecisionId": "x"},
                                 {}, status="SOMETHING_ELSE")


def test_both_no_position_statuses_are_in_the_migration():
    import pathlib
    sql = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "migrations", "087_version_blocked_status.sql").read_text()
    for status in eng.NO_POSITION_STATUSES:
        assert status in sql, status
    assert "bettor_exp_refusal_has_no_position" in sql


def test_the_worker_asks_the_version_before_the_reentry_clause():
    """There is no point asking whether THIS entry re-enters too soon
    when the version may not enter at all."""
    import inspect
    from sportsassets.workers import shadow_experimental as worker
    src = inspect.getsource(worker._persist)
    assert src.index("ver.position_creation") < src.index(
        "reentry_verdict")
    assert src.index("ver.position_creation") < src.index(
        "record_decision")


# ── §9: the hierarchy is unchanged ───────────────────────────────────


def test_the_successor_is_not_decision_grade():
    for e in (reg.X1V2, reg.X1CV2):
        assert e["notDecisionGrade"] is True
        assert e["realOrderSubmissionEnabled"] is False
        assert e["capitalAtRisk"] == 0
        assert e["lane"] == xp.EXPERIMENTAL_LANE


def test_the_successor_keeps_its_control():
    """§9: 'DID THE MODEL ADD VALUE?' is only answerable with a null
    beside the candidate on the same population."""
    assert reg.X1CV2["role"] == xp.CONTROL
    assert reg.X1CV2["controlFor"] == "X1_SHORT_HORIZON_DIRECTION_V2"
    assert reg.X1CV2["exitRule"] == reg.X1V2["exitRule"]
    assert reg.X1CV2["entryRule"] != reg.X1V2["entryRule"]  # direction only


def test_no_real_orders_anywhere_in_the_successor():
    code = code_only(xspec) + " " + code_only(ver)
    for forbidden in ("submit_order", "place_order", "create_order",
                      "cancel_order"):
        assert forbidden not in code, forbidden
