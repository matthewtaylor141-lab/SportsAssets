"""THE V2 REVIEW'S FOUR FINDINGS, PINNED SO THEY CANNOT RECUR.

Owner review 2026-09-20, "DO NOT ARM X1_SHORT_HORIZON_DIRECTION_V2".

Each test below corresponds to one defect the review found in work I
had already reported as correct. They are written as regressions
because that is what they are.
"""

from __future__ import annotations

import inspect
import io
import tokenize

import pytest

from sportsassets import shadow_block_enforcement as blk
from sportsassets import shadow_exit_spec_v3 as xspec3
from sportsassets import shadow_experiment_registry as reg
from sportsassets import shadow_experiment_versions as ver
from sportsassets import shadow_experiments as xp


def code_only(module) -> str:
    out = []
    for tok in tokenize.generate_tokens(
            io.StringIO(inspect.getsource(module)).readline):
        if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            out.append(tok.string)
    return " ".join(out)


# ── §6: a frozen hash may not depend on replaceable telemetry ────────


def test_v3_does_not_import_the_telemetry_module():
    """THE DEFECT: V2 computed its bound as
    ceil(shadow_markout_observability.CAPTURE_MAX_S), so re-measuring
    the grid would have silently changed a frozen experiment's sha."""
    code = code_only(xspec3)
    assert "shadow_markout_observability" not in code
    assert "ceil" not in code


def test_v3s_hash_does_not_move_when_telemetry_does(monkeypatch):
    """The property the import broke, asserted directly."""
    from sportsassets import shadow_markout_observability as ob
    before = reg.X1V3["experimentSha"]
    monkeypatch.setattr(ob, "CAPTURE_MAX_S", 999.0)
    monkeypatch.setattr(ob, "CAPTURE_P95_S", 998.0)
    import importlib
    importlib.reload(xspec3)
    assert xspec3.OPERATIONAL_HARD_BOUND_MS == 5000
    assert reg.X1V3["experimentSha"] == before


def test_every_provenance_value_is_a_literal():
    for name in ("BOOK_AGE_N", "BOOK_AGE_P50_MS", "BOOK_AGE_P95_MS",
                 "BOOK_AGE_MAX_MS", "OBSERVED_SWEEP_S",
                 "OPERATIONAL_HARD_BOUND_MS"):
        assert isinstance(getattr(xspec3, name), (int, float)), name


# ── §4: the bound must be on the right quantity ──────────────────────


def test_the_bound_is_the_stores_own_freshness_limit():
    """Not a new free parameter -- the contract the ENTRY already
    answers to, applied symmetrically to the exit."""
    from sportsassets import institutional_book as ib
    assert xspec3.OPERATIONAL_HARD_BOUND_MS == int(
        ib.FRESHNESS_LIMIT_S * 1000)


def test_the_bound_is_not_the_evidence_trail_cadence():
    """V2's 65s came from bettor_l2_evidence spacing, which is
    institutional_md.EVIDENCE_EVERY_S = 60.0 -- a table the DIRECT
    execution path never reads."""
    from sportsassets.workers import institutional_md as md
    assert md.EVIDENCE_EVERY_S == 60.0
    assert xspec3.OPERATIONAL_HARD_BOUND_MS < md.EVIDENCE_EVERY_S * 1000
    assert xspec3.OPERATIONAL_HARD_BOUND_MS != 65_000
    assert xspec3.OPERATIONAL_HARD_BOUND_MS != 71_000


def test_the_configured_contract_matches_the_collector():
    from sportsassets.workers import institutional_md as md
    from sportsassets import pmx_institutional as pmx
    assert xspec3.CONFIGURED_CAPTURE_PERIOD_S == md.SWEEP_S
    assert xspec3.BOOKS_PER_CYCLE == md.MAX_INSTRUMENTS
    assert xspec3.READ_PACING_S == md.READ_PACING_S
    assert xspec3.REQUEST_TIMEOUT_CONNECT_S == pmx.TIMEOUT[0]
    assert xspec3.REQUEST_TIMEOUT_READ_S == pmx.TIMEOUT[1]
    assert xspec3.FAILURE_BACKOFF_S == md.BOOTSTRAP_BACKOFF_S


def test_the_worst_case_is_stated_and_finite():
    assert xspec3.THEORETICAL_WORST_CASE_CYCLE_S == pytest.approx(243.2)
    assert xspec3.OPERATIONAL_HARD_BOUND_ESTABLISHED is True


def test_the_basis_names_its_exclusions():
    basis = xspec3.OPERATIONAL_BOUND_BASIS
    for excluded in ("X1 P&L", "X1 markouts", "X1C"):
        assert excluded in basis, excluded


# ── §9: initial and residual anchors declared separately ─────────────


def test_initial_and_residual_anchors_are_different_and_said_so():
    assert "TARGET_EXIT_TIMESTAMP" in xspec3.INITIAL_EXIT_ANCHOR
    assert "PREVIOUS ATTEMPT" in xspec3.RESIDUAL_RETRY_ANCHOR
    assert "not the original" in xspec3.RESIDUAL_RETRY_ANCHOR
    assert "different anchor" in xspec3.RESIDUAL_RETRY_DELAY_BASIS


def test_both_windows_have_the_same_length_and_basis():
    assert (xspec3.INITIAL_EXIT_MAX_DELAY_MS
            == xspec3.RESIDUAL_RETRY_MAX_INTERBOOK_DELAY_MS
            == xspec3.OPERATIONAL_HARD_BOUND_MS)


def test_an_unfilled_residual_stays_open():
    assert "stays open" in xspec3.RESIDUAL_RETRY_DELAY_BASIS
    assert "never interpolated, never closed" in \
        xspec3.RESIDUAL_RETRY_DELAY_BASIS


# ── §7: V2 preserved exactly, V3 separate ────────────────────────────


def test_v2_is_unmutated_and_its_shas_are_not_reused():
    assert reg.X1V2["experimentSha"] == "0a1319e68fe02ff9"
    assert reg.X1CV2["experimentSha"] == "d7643c9f622b9e3d"
    shas = [e["experimentSha"] for e in reg.EXPERIMENTS]
    assert len(shas) == len(set(shas))


def test_v2_carries_no_review_verdict_inside_its_declaration():
    """Writing NOT_APPROVED into V2 would change its hash, and a
    rejected experiment whose hash moved is not the thing rejected."""
    for key in ("result", "reviewResult", "NOT_APPROVED"):
        assert key not in reg.X1V2


def test_v2_review_result_is_recorded_outside():
    assert reg.V2_REVIEW["X1_SHORT_HORIZON_DIRECTION_V2"][
        "result"] == "NOT_APPROVED"
    assert reg.V2_REVIEW["X1_SHORT_HORIZON_DIRECTION_V2"]["positions"] == 0
    assert len(reg.V2_REVIEW["X1_SHORT_HORIZON_DIRECTION_V2"][
        "reasons"]) == 4


def test_v2_may_not_create_a_position():
    for eid in ("X1_SHORT_HORIZON_DIRECTION_V2", "X1C_NULL_CONTROL_V2"):
        out = ver.position_creation(eid, declaration=reg.BY_ID[eid])
        assert out["permitted"] is False
        assert out["decision"] == ver.NOT_APPROVED


# ── §5: a prospective experiment cannot predate its own rules ────────


def test_v3_starts_after_v2s_actual_freeze():
    assert reg.V3_START_UTC > "2026-09-20T13:11:40Z"
    assert reg.V3_START_UTC > reg.SUCCESSOR_START_UTC
    assert reg.X1V3["startTimestamp"] == reg.V3_START_UTC


# ── §8: the economics carried forward byte-for-byte ──────────────────


def test_v3_entry_is_the_same_string_as_v1s():
    assert reg.X1V3["entryRule"] == reg.X1["entryRule"]
    assert reg.X1V3["directionRule"] == reg.X1["directionRule"]
    assert reg.X1V3["horizon"] == reg.X1["horizon"]
    assert reg.X1V3["target"] == reg.X1["target"]
    assert reg.X1V3["intendedNotionalUsd"] == reg.X1["intendedNotionalUsd"]
    assert reg.X1V3["featureSet"] == reg.X1["featureSet"]
    assert reg.SIGNAL_FOR["X1_SHORT_HORIZON_DIRECTION_V3"] is \
        reg.SIGNAL_FOR["X1_SHORT_HORIZON_DIRECTION"]


# ── §11: nothing may open a position ─────────────────────────────────


def test_nothing_in_the_registry_may_open_a_position():
    permitted = [e["experimentId"] for e in reg.EXPERIMENTS
                 if ver.position_creation(e["experimentId"],
                                          declaration=e)["permitted"]]
    assert permitted == []


def test_v3_is_not_armed():
    assert reg.X1V3["readiness"] == xp.DECLARED_AWAITING_REVIEW
    assert reg.X1CV3["readiness"] == xp.DECLARED_AWAITING_REVIEW
    assert "X1_SHORT_HORIZON_DIRECTION_V3" not in [
        e["experimentId"] for e in reg.armed()]


def test_v3_has_a_complete_lifecycle():
    assert xp.lifecycle_completeness(reg.X1V3) == xp.LIFECYCLE_COMPLETE
    assert xp.lifecycle_completeness(reg.X1CV3) == xp.LIFECYCLE_COMPLETE


# ── §2: the arbitrary window is gone ─────────────────────────────────


def test_there_is_no_grace_interval():
    """THE DEFECT: I defined correctness as 'no positions more than 120
    seconds after blocking began', which silently accepts leakage
    inside 120 seconds and cannot tell a slow deploy from a real leak."""
    code = code_only(blk)
    assert "120" not in code
    assert blk.enforcement_epoch()["established"] is False


def test_an_unestablished_epoch_makes_no_claim_either_way():
    out = blk.leak_verdict(epoch=blk.EPOCH_NOT_ESTABLISHED,
                           positions_after_epoch=["xpos_1"])
    assert out["epochEstablished"] is False
    assert out["clean"] is False
    assert "no epoch established" in out["why"]


def test_the_epoch_comes_from_an_observed_boot():
    out = blk.enforcement_epoch(worker_boot_at="2026-09-20T13:11:44Z")
    assert out["established"] is True
    assert out["BLOCK_ENFORCEMENT_EPOCH"] == "2026-09-20T13:11:44Z"
    assert "No grace interval" in out["rule"]


def test_only_the_named_rows_are_exempt():
    out = blk.leak_verdict(epoch="2026-09-20T13:11:44Z",
                           positions_after_epoch=["xpos_a", "xpos_b"],
                           contaminated_ids=["xpos_a"])
    assert out["POST_EPOCH_BLOCK_LEAKS"] == 1
    assert out["leakedPositionIds"] == ["xpos_b"]
    assert out["clean"] is False


def test_the_contamination_is_not_a_timing_allowance():
    rec = blk.CONTAMINATION_RECORD
    assert rec["classification"] == "DEPLOY_TRANSITION_CONTAMINATION"
    assert rec["remainsPartOf"] == "X1C_V1_ALL_RECORDED_EVIDENCE"
    assert rec["excludedFrom"] == "X1_VS_X1C_PAIRED_COMPARISON"
    assert "not a grace interval" in rec["isNotATimingAllowance"]
