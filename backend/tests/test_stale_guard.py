"""The record's shrink guard must not freeze on a GROWING record.

2026-08-13 overnight: the guard held a settled-stake high-water of
$41,179 / 1,466 settled. The owner's manual sells moved rows out of the
AI cohort, so settled stake legitimately fell below the high-water and
every fresh build was refused. The existing escape hatch needs three
consecutive builds to AGREE within 1% stake and +/-2 settled — but the
overnight slate kept resolving, so the numbers climbed (1473 -> 1480 ->
1491) and never held still. The page served 3-hour-old numbers showing
FEWER settled trades than reality, and would not have self-healed.
"""

from sportsassets.api import track_record as tr


def _reset(settled_high, stake_high):
    tr._persist_state.update(ts=0.0, settled=settled_high, stake=stake_high)
    tr._refused.update(streak=0, stakes=[], settled=[])


def _feed(stakes, settleds):
    """Replay refused builds through the escape-hatch decision."""
    out = []
    for stake, settled in zip(stakes, settleds):
        tr._refused["streak"] += 1
        tr._refused["stakes"] = (tr._refused["stakes"] + [stake])[-5:]
        tr._refused["settled"] = (tr._refused["settled"] + [settled])[-5:]
        s3 = tr._refused["stakes"][-3:]
        n3 = tr._refused["settled"][-3:]
        agree = (len(s3) >= 3 and max(s3) > 0
                 and (max(s3) - min(s3)) <= 0.01 * max(s3)
                 and (max(n3) - min(n3)) <= 2)
        growing = (len(s3) >= 3
                   and all(b >= a for a, b in zip(n3, n3[1:]))
                   and all(b >= a for a, b in zip(s3, s3[1:]))
                   and n3[-1] > n3[0])
        out.append(agree or growing)
    return out


def test_the_real_overnight_series_unfreezes():
    """The exact numbers off the wire that stayed frozen for 5 cycles."""
    _reset(1466.0, 41179.42)
    unlocked = _feed([36413.96, 36969.46, 37250.14],
                     [1473.0, 1480.0, 1491.0])
    assert unlocked[-1] is True, "a growing record must re-baseline"
    assert unlocked[0] is False and unlocked[1] is False, \
        "never on fewer than three builds"


def test_a_shrinking_build_still_refuses():
    """The 2026-08-07 fake-negatives case must still be caught."""
    _reset(1466.0, 41179.42)
    # settled climbs while stake COLLAPSES — missing activities.
    assert _feed([6900.0, 4200.0, 2900.0],
                 [958.0, 961.0, 963.0]) == [False, False, False]


def test_wobbling_stake_still_refuses():
    """Transient loss jitters; it does not climb monotonically."""
    _reset(1466.0, 41179.42)
    assert not _feed([37000.0, 34000.0, 37200.0],
                     [1473.0, 1480.0, 1491.0])[-1]


def test_flat_record_still_uses_the_original_stability_hatch():
    """A quiet, stable, legitimately-lower basis re-baselines as before."""
    _reset(1466.0, 41179.42)
    assert _feed([37000.0, 37050.0, 37010.0],
                 [1470.0, 1470.0, 1471.0])[-1] is True


def test_growth_needs_a_real_increase_not_a_flat_line():
    """Three identical builds are 'stable', not 'growing' — and the
    stability hatch owns that case, with its own tolerances."""
    _reset(1466.0, 41179.42)
    tr._refused.update(streak=0, stakes=[], settled=[])
    s3 = [37000.0, 37000.0, 37000.0]
    n3 = [1470.0, 1470.0, 1470.0]
    growing = (all(b >= a for a, b in zip(n3, n3[1:]))
               and all(b >= a for a, b in zip(s3, s3[1:]))
               and n3[-1] > n3[0])
    assert growing is False


def _recomposed(payload):
    """Replicate the third escape hatch's decision (2026-08-19)."""
    fresh_settled = float(payload["summary"]["settled"])
    return (fresh_settled >= tr._persist_state["settled"]
            and tr._persist_state.get("total", 0.0) > 0
            and tr._total_of(payload)
            >= tr._persist_state["total"] * tr._STAKE_SHRINK_FLOOR)


def test_recomposition_unfreezes_on_the_first_build():
    """2026-08-19 overnight deadlock (streak stuck at 4+): settled grew
    past the high-water while attributed stake shrank — archive
    absorption re-binned copy rows into excluded_unattributed. The
    stability hatch needs settled within +/-2 and the growth hatch
    needs stake non-decreasing, so neither ever fired. The
    whole-account total held up: recomposition, not loss."""
    _reset(2154.0, 88652.8)
    tr._persist_state["total"] = 88652.8 + 58080.42
    payload = {"summary": {"settled": 2166.0, "settled_stake": 86172.37},
               "excluded_unattributed": {"stake": 67001.43}}
    assert _recomposed(payload) is True


def test_real_loss_with_growing_rows_still_refuses():
    """Lost activities can add NEW settled rows while dropping old
    stake — the whole-account total collapses with them, so the
    recomposition hatch must not fire on a genuine loss."""
    _reset(2154.0, 88652.8)
    tr._persist_state["total"] = 146733.0
    payload = {"summary": {"settled": 2166.0, "settled_stake": 40000.0},
               "excluded_unattributed": {"stake": 30000.0}}
    assert _recomposed(payload) is False


def test_recomposition_needs_a_total_baseline():
    """Before any total high-water exists the hatch stays closed —
    fresh processes fall back to the original two hatches."""
    _reset(2154.0, 88652.8)
    tr._persist_state["total"] = 0.0
    payload = {"summary": {"settled": 2166.0, "settled_stake": 86172.37},
               "excluded_unattributed": {"stake": 67001.43}}
    assert _recomposed(payload) is False
