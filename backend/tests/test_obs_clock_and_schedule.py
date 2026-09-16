"""Run 83 acceptance tests that need no database and no network.

These cover the parts of the invariant list that are properties of the code
rather than of a deployment: the clock discipline (83B), the offset schedule
(83G), and the depth walk the forward curve is built on.
"""
from __future__ import annotations

import pytest

from sportsassets.obs import clock
from sportsassets.obs.config import OFFSETS, window_for
from sportsassets.obs.schedule import Plan, SnapshotStatus, vwap, walk_depth


# --------------------------------------------------------------- clock (83B)
def test_an_instant_carries_both_clock_families():
    i = clock.now()
    assert isinstance(i.monotonic, float)
    assert i.wall.tzinfo is not None, "wall timestamps are timezone-aware UTC"
    assert i.process_boot_id == clock.PROCESS_BOOT_ID


def test_monotonic_readings_are_non_decreasing():
    """Acceptance test 5, at its source: the clock itself cannot go backwards."""
    prev = clock.now()
    for _ in range(200):
        cur = clock.now()
        assert cur.monotonic >= prev.monotonic
        prev = cur


def test_subtracting_across_process_instances_raises_rather_than_guesses():
    """The whole point of carrying a boot id.

    Python's monotonic origin moves on restart, so a difference across that
    boundary is a plausible number with no meaning. Run 82 is a study in what
    plausible meaningless numbers cost.
    """
    a = clock.now()
    b = clock.Instant(monotonic=a.monotonic + 1.0, wall=a.wall,
                      process_boot_id="some-other-process")
    with pytest.raises(clock.ClockDomainError):
        clock.elapsed_between(a, b)


def test_a_missing_source_timestamp_cannot_be_dressed_as_supplied():
    """Acceptance test 4: no silent source timestamp fallback."""
    ok = clock.SourceTimestamp.missing("polygon_block_timestamp",
                                       clock.ClockDomain.SOURCE_CHAIN)
    assert ok.value is None and ok.status == "MISSING"

    with pytest.raises(ValueError):
        clock.SourceTimestamp(value=None, provenance="p",
                              clock_domain=clock.ClockDomain.SOURCE_CHAIN,
                              status="SUPPLIED")

    import datetime as _dt
    with pytest.raises(ValueError):
        clock.SourceTimestamp(value=_dt.datetime.now(_dt.timezone.utc),
                              provenance="p",
                              clock_domain=clock.ClockDomain.SOURCE_CHAIN,
                              status="MISSING")


def test_a_substituted_local_clock_must_declare_itself():
    import datetime as _dt
    with pytest.raises(ValueError):
        clock.SourceTimestamp(value=_dt.datetime.now(_dt.timezone.utc),
                              provenance="local_wall_fallback",
                              clock_domain=clock.ClockDomain.BETTOR_WALL,
                              status="FALLBACK_SUBSTITUTED",
                              fallback=False)


# ------------------------------------------------------------ schedule (83G)
def test_the_offset_grid_is_the_pre_registered_one():
    """If this fails, the pre-registration and the collector have diverged."""
    assert [lbl for lbl, _ in OFFSETS] == [
        "0ms", "100ms", "250ms", "500ms", "1s", "2s", "5s", "10s", "30s", "60s"]
    assert [t for _, t in OFFSETS] == [
        0.0, 0.100, 0.250, 0.500, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]


def test_slots_are_scheduled_from_the_receipt_anchor():
    p = Plan.for_event(anchor_monotonic=1000.0)
    assert [s.due_at for s in p.slots] == [1000.0 + t for _, t in OFFSETS]
    assert p.horizon_s == 60.0


def test_a_window_is_floored_for_short_offsets_and_proportional_for_long_ones():
    assert window_for(0.0) == pytest.approx(0.050)
    assert window_for(0.100) == pytest.approx(0.050)   # floor wins
    assert window_for(60.0) == pytest.approx(12.0)     # fraction wins


def test_a_reading_at_t_not_the_first_reading_after_t():
    """THE rule. A slot whose window has closed is MISSED, never back-filled.

    This is the defect price_path.py was rewritten to remove: taking every
    overdue offset at once produced identical prices under different labels --
    a flat curve by fiat.
    """
    p = Plan.for_event(anchor_monotonic=0.0)

    # Nothing is due before the anchor's first offset.
    slot = p.next_pending(0.0)
    assert slot is not None and slot.label == "0ms"
    assert slot.is_due(0.0)

    # Jump far past several windows, as a stalled worker would.
    slot = p.next_pending(4.0)
    assert slot is not None, "some slot must still be live"
    assert slot.label == "5s", (
        "every offset whose window closed must be missed, not harvested late")
    for s in p.slots:
        if s.target_s < 4.0:
            assert s.status == SnapshotStatus.MISSED_WINDOW
            assert "window closed unread" in (s.miss_reason or "")


def test_an_expired_slot_cannot_be_resurrected_by_a_later_read():
    p = Plan.for_event(anchor_monotonic=0.0)
    p.next_pending(4.0)                      # expires 0ms..2s
    zero = next(s for s in p.slots if s.label == "0ms")
    assert zero.status == SnapshotStatus.MISSED_WINDOW
    p.next_pending(4.1)                      # a later pass must not revive it
    assert zero.status == SnapshotStatus.MISSED_WINDOW


def test_abandoning_an_event_records_misses_rather_than_dropping_slots():
    """Acceptance test 11's spirit: absence is never how a failure is recorded."""
    p = Plan.for_event(anchor_monotonic=0.0)
    p.expire_all(0.0, reason="collector shutting down")
    assert p.is_complete()
    assert p.census() == {SnapshotStatus.MISSED_WINDOW: len(OFFSETS)}
    assert all(s.miss_reason == "collector shutting down" for s in p.slots)


def test_a_completed_plan_has_one_resolved_slot_per_pre_registered_offset():
    """Acceptance test 8: the forward snapshots are captured in a test."""
    p = Plan.for_event(anchor_monotonic=0.0)
    captured = []
    mono = 0.0
    while True:
        slot = p.next_pending(mono)
        if slot is None:
            break
        mono = slot.due_at               # a well-behaved collector reads on time
        assert slot.is_due(mono)
        slot.status = SnapshotStatus.CAPTURED
        captured.append((slot.label, mono - slot.anchor_monotonic))
    assert p.is_complete()
    assert p.census() == {SnapshotStatus.CAPTURED: len(OFFSETS)}
    assert [c[0] for c in captured] == [lbl for lbl, _ in OFFSETS]
    # Actual offsets are non-decreasing -- acceptance test 5 at the plan level.
    assert all(b[1] >= a[1] for a, b in zip(captured, captured[1:]))


# ----------------------------------------------------------------- depth walk
def test_the_depth_walk_matches_the_historical_definition():
    levels = [(0.90, 100.0), (0.91, 200.0), (0.92, 50.0)]
    # 150 shares: 100 @ 0.90 + 50 @ 0.91
    assert walk_depth(levels, 150.0) == pytest.approx(100 * 0.90 + 50 * 0.91)
    assert vwap(levels, 150.0) == pytest.approx((100 * 0.90 + 50 * 0.91) / 150.0)


def test_the_walk_is_price_ascending_regardless_of_stored_order():
    a = [(0.92, 50.0), (0.90, 100.0), (0.91, 200.0)]
    b = [(0.90, 100.0), (0.91, 200.0), (0.92, 50.0)]
    assert walk_depth(a, 150.0) == pytest.approx(walk_depth(b, 150.0))


def test_exhausted_depth_returns_none_and_never_a_partial_cost():
    """DEPTH_EXHAUSTED is NOT IDENTIFIED -- not a bound, not an extrapolation."""
    levels = [(0.90, 10.0)]
    assert walk_depth(levels, 100.0) is None
    assert vwap(levels, 100.0) is None
    assert walk_depth([], 1.0) is None
    assert walk_depth(levels, 0.0) is None


def test_depth_values_are_carried_exactly_not_rounded():
    """Acceptance test 9: the ladder keeps the venue's own numbers."""
    levels = [(0.123456, 1.234567), (0.234567, 2.345678)]
    cost = walk_depth(levels, 1.234567)
    assert cost == pytest.approx(0.123456 * 1.234567, rel=0, abs=1e-15)
