"""BOOK FRESHNESS AND TARGET-TO-OBSERVATION DELAY ARE NOT THE SAME
QUANTITY.

Owner review 2026-09-20 §1. THE DEFECT WAS MINE: V3 set
INITIAL_EXIT_MAX_DELAY = 5,000ms and justified it with
institutional_book.FRESHNESS_LIMIT_S. That limit answers "how old may
the book be", computed from the book's own timestamps. It says nothing
about how long after the target we may wait.

The owner's counterexample is the test below: a book sourced 10:00:19
and received 10:00:20 against a 10:00:00 target is 1s old -- fresh and
admissible -- with a 20s observation delay. V3's window would have
refused sound evidence for a reason that was never about staleness.
"""

from __future__ import annotations

import pytest

from sportsassets import institutional_book as ib
from sportsassets import shadow_block_enforcement as blk
from sportsassets import shadow_exit_spec_v4 as v4
from sportsassets import shadow_experiment_registry as reg
from sportsassets import shadow_experiment_versions as ver
from sportsassets import shadow_experiments as xp


# ── §1: the counterexample, as a test ────────────────────────────────


def test_a_fresh_book_can_arrive_long_after_the_target():
    """TARGET 10:00:00, source 10:00:19, received 10:00:20.
    BOOK_AGE = 1s (fresh). EXIT_OBSERVATION_DELAY = 20s.
    The two checks disagree, which is the whole point."""
    book_age_ms = 1_000
    observation_delay_ms = 20_000
    assert book_age_ms <= v4.BOOK_FRESHNESS_LIMIT_MS       # admissible
    assert observation_delay_ms > v4.BOOK_FRESHNESS_LIMIT_MS
    # and V4 does not refuse it, because it declares no delay ceiling
    assert v4.MAX_EXIT_OBSERVATION_DELAY == v4.NOT_ESTABLISHED


def test_the_two_quantities_are_separated_by_name():
    sep = v4.separation()
    assert sep["BOOK_FRESHNESS"]["established"] is True
    assert sep["BOOK_FRESHNESS"]["bound"] == "5000ms"
    assert sep["TARGET_TO_OBSERVATION_DELAY"]["established"] is False
    assert sep["TARGET_TO_OBSERVATION_DELAY"]["bound"] == "NOT_ESTABLISHED"
    assert sep["BOOK_FRESHNESS"]["computedFrom"] != \
        sep["TARGET_TO_OBSERVATION_DELAY"]["computedFrom"]


def test_freshness_is_computed_from_the_books_own_timestamps():
    assert "the book's own source and receipt timestamps" in \
        v4.separation()["BOOK_FRESHNESS"]["computedFrom"]
    assert "BOOK'S OWN" in v4.BOOK_ADMISSIBILITY_RULE


def test_the_freshness_limit_still_matches_the_store():
    assert v4.BOOK_FRESHNESS_LIMIT_MS == int(ib.FRESHNESS_LIMIT_S * 1000)


# ── §5/§8: no delay ceiling is invented ──────────────────────────────


def test_no_target_delay_bound_is_declared():
    assert v4.MAX_EXIT_OBSERVATION_DELAY == "NOT_ESTABLISHED"
    assert reg.X1V4["maxExitObservationDelayMs"] == "NOT_ESTABLISHED"


def test_no_interbook_bound_is_declared():
    assert v4.RESIDUAL_RETRY_MAX_INTERBOOK_DELAY == "NOT_ESTABLISHED"
    assert "NO MAXIMUM INTERBOOK DELAY IS ESTABLISHED" in v4.RETRY_RULE


def test_v4_carries_no_five_second_waiting_window():
    """The specific thing V3 got wrong must not reappear."""
    for rule in (v4.INITIAL_EXIT_RULE, v4.RETRY_RULE,
                 v4.TARGET_DELAY_RULE):
        assert "TARGET + 5" not in rule
        assert "within 5 seconds of" not in rule


def test_the_absence_of_a_bound_does_not_stop_exits():
    assert "WAITS for the first admissible fresh book" in \
        v4.TARGET_DELAY_RULE


# ── §4: the delay is recorded, never relabelled ──────────────────────


def test_every_attempt_preserves_the_four_instants():
    for field in ("TARGET_EXIT_TIMESTAMP", "EXIT_BOOK_SOURCE_TIMESTAMP",
                  "EXIT_BOOK_RECEIVED_TIMESTAMP", "BOOK_AGE_MS",
                  "EXIT_OBSERVATION_DELAY_MS"):
        assert field in v4.EXIT_EVIDENCE_FIELDS, field


def test_a_late_book_is_never_relabelled():
    assert "never relabelled as a +60s execution" in \
        v4.DELAY_DISCLOSURE_RULE
    assert "EXIT_BOOK_RECEIVED_TIMESTAMP - TARGET_EXIT_TIMESTAMP" in \
        v4.DELAY_DISCLOSURE_RULE


def test_the_residual_records_its_own_interbook_delay():
    for field in ("PREVIOUS_ATTEMPT_TIMESTAMP",
                  "NEXT_BOOK_RECEIVED_TIMESTAMP", "INTERBOOK_DELAY_MS",
                  "BOOK_AGE_MS"):
        assert field in v4.RESIDUAL_EVIDENCE_FIELDS, field


# ── §6: a data outage is an economic fact ────────────────────────────


def test_a_data_outage_keeps_capital_deployed():
    assert v4.EXIT_PENDING_DATA == "EXIT_PENDING_DATA"
    rule = v4.DATA_OUTAGE_RULE
    assert "Capital REMAINS DEPLOYED" in rule
    assert "not closed" in rule
    assert "no realized P&L is generated" in rule
    assert "ACTUAL_HOLD_TIME" in rule and "CAPITAL_HOURS" in rule


def test_the_waiting_time_is_not_deleted_from_performance():
    assert "rather than deleting the trade from performance" in \
        v4.DATA_OUTAGE_RULE
    assert "ACTUAL_HOLD_TIME_MS" in v4.EXIT_EVIDENCE_FIELDS


# ── §9: settlement still unresolved ──────────────────────────────────


def test_a_residual_at_close_is_not_a_settled_pnl():
    assert v4.EXIT_INCOMPLETE_AT_CLOSE == "EXIT_INCOMPLETE_AT_MARKET_CLOSE"
    assert "CONFLICTING_VENUE_PROSE" in v4.MARKET_CLOSE_RULE
    assert "NOT automatically converted into settled P&L" in \
        v4.MARKET_CLOSE_RULE


# ── §2/§10: V3 preserved, V4 separate ────────────────────────────────


def test_v3_is_unmutated_and_its_shas_are_not_reused():
    assert reg.X1V3["experimentSha"] == "066cdad7a8816f44"
    assert reg.X1CV3["experimentSha"] == "5c1d3bcdcf18f463"
    shas = [e["experimentSha"] for e in reg.EXPERIMENTS]
    assert len(shas) == len(set(shas))


def test_v3_review_result_is_recorded_outside_the_declaration():
    assert reg.V3_REVIEW["X1_SHORT_HORIZON_DIRECTION_V3"][
        "result"] == "NOT_APPROVED"
    assert reg.V3_REVIEW["X1_SHORT_HORIZON_DIRECTION_V3"]["positions"] == 0
    for key in ("result", "reviewResult"):
        assert key not in reg.X1V3


def test_v3_may_not_create_a_position():
    for eid in ("X1_SHORT_HORIZON_DIRECTION_V3", "X1C_NULL_CONTROL_V3"):
        out = ver.position_creation(eid, declaration=reg.BY_ID[eid])
        assert out["permitted"] is False
        assert out["decision"] == ver.NOT_APPROVED


def test_v3_is_not_armed_at_1500z():
    assert reg.X1V3["readiness"] == xp.DECLARED_AWAITING_REVIEW
    assert "X1_SHORT_HORIZON_DIRECTION_V3" not in [
        e["experimentId"] for e in reg.armed()]


# ── §11: V4 declares no start it has not earned ──────────────────────


def test_v4_declares_no_activation_instant():
    """V2 declared a start 8h before its freeze; V3 named a future
    instant chosen before review. Both claim an activation that has not
    happened. V4 declares none."""
    assert reg.X1V4["startTimestamp"] == \
        reg.V4_ACTIVATION_EPOCH_SENTINEL == \
        "ACTIVATION_EPOCH_NOT_YET_ESTABLISHED"
    assert reg.X1CV4["startTimestamp"] == reg.X1V4["startTimestamp"]


def test_v4_start_is_not_a_timestamp_at_all():
    assert not reg.X1V4["startTimestamp"].startswith("2026")


# ── §8/§10: the economics carried forward ────────────────────────────


def test_v4_entry_is_the_same_string_as_v1s():
    assert reg.X1V4["entryRule"] == reg.X1["entryRule"]
    assert reg.X1V4["directionRule"] == reg.X1["directionRule"]
    assert reg.X1V4["horizon"] == reg.X1["horizon"] == "60S"
    assert reg.X1V4["target"] == reg.X1["target"]
    assert reg.X1V4["intendedNotionalUsd"] == reg.X1["intendedNotionalUsd"]
    assert reg.SIGNAL_FOR["X1_SHORT_HORIZON_DIRECTION_V4"] is \
        reg.SIGNAL_FOR["X1_SHORT_HORIZON_DIRECTION"]


def test_v4_has_a_complete_lifecycle():
    assert xp.lifecycle_completeness(reg.X1V4) == xp.LIFECYCLE_COMPLETE
    assert xp.lifecycle_completeness(reg.X1CV4) == xp.LIFECYCLE_COMPLETE


def test_v4_keeps_its_control():
    assert reg.X1CV4["role"] == xp.CONTROL
    assert reg.X1CV4["controlFor"] == "X1_SHORT_HORIZON_DIRECTION_V4"
    assert reg.X1CV4["exitRule"] == reg.X1V4["exitRule"]


# ── §12: the epoch is now an observed fact ───────────────────────────


def test_the_enforcement_epoch_is_established():
    assert blk.OBSERVED_EPOCH == "2026-09-20T14:25:22.609978Z"
    assert blk.OBSERVED_EPOCH_REVISION == "c95e3f2"
    assert blk.OBSERVED_POST_EPOCH_LEAKS == 0
    out = blk.enforcement_epoch(worker_boot_at=blk.OBSERVED_EPOCH)
    assert out["established"] is True
    assert "No grace interval" in out["rule"]


# ── §14: nothing may open a position ─────────────────────────────────


def test_nothing_in_the_registry_may_open_a_position():
    permitted = [e["experimentId"] for e in reg.EXPERIMENTS
                 if ver.position_creation(e["experimentId"],
                                          declaration=e)["permitted"]]
    assert permitted == []


def test_v4_is_awaiting_review():
    out = ver.position_creation("X1_SHORT_HORIZON_DIRECTION_V4",
                                declaration=reg.X1V4)
    assert out["permitted"] is False
    assert out["decision"] == ver.AWAITING_REVIEW
    assert reg.X1V4["readiness"] == xp.DECLARED_AWAITING_REVIEW


def test_the_registry_still_verifies():
    assert reg.registry_report()["hashesVerified"] is True
