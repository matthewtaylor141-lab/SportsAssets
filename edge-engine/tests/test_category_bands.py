"""Derivative (spread/total) band windows.

The old windows traded 3 of 20 spread bands and 2 of 20 total bands because a
single account's ROI-by-price table was read as a map of where edge exists,
rather than a map of where that account bet. These tests pin the corrected
policy AND the constraints that keep it honest: the unmeasured extension must
clear a stricter bar than the measured core, and the tails stay shut.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from edge.execution.engine import Policy, strategy_filter

# Most tests here exercise loop and pricing MECHANICS, not trading policy.
# `blocked_categories` globally quarantines moneyline (measured -2.34c drift,
# retention 0.239 on our own fills), which would otherwise make every
# moneyline fixture untradeable and turn these into vacuous passes. The
# quarantine itself is pinned by its own tests in test_loop_health.py.
POLICY = Policy.load()
POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}
DERIVATIVES = ("spread", "total")
REPO = Path(__file__).resolve().parents[1]

CORE_THRESHOLD = 0.025
EXT_THRESHOLD = 0.030


def _prices(lo, hi, step=0.01):
    p = lo
    while p < hi - 1e-9:
        yield round(p, 2)
        p = round(p + step, 2)


# ── coverage ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("category", DERIVATIVES)
def test_the_whole_middle_of_the_book_is_tradeable(category):
    """0.10-0.90 continuous. Sports derivatives are engineered coin flips —
    if we can only buy a 30c band of them we are not in the business."""
    for p in _prices(0.10, 0.90):
        assert POLICY.band_threshold(p, category) is not None, p


@pytest.mark.parametrize("category", DERIVATIVES)
def test_tails_stay_shut(category):
    """Whole-cent ticks mean a 1c error is a third of a 3c price. We cannot
    measure an edge finer than the grid we trade on."""
    for p in (0.01, 0.05, 0.09, 0.91, 0.95, 0.99):
        assert POLICY.band_threshold(p, category) is None, p


@pytest.mark.parametrize("category", DERIVATIVES)
def test_the_measured_core_keeps_its_measured_threshold(category):
    for p in _prices(0.40, 0.55):
        assert POLICY.band_threshold(p, category) == CORE_THRESHOLD


@pytest.mark.parametrize("category", DERIVATIVES)
def test_the_unmeasured_extension_must_clear_a_higher_bar(category):
    """Everything outside the measured zone is inference, so it pays a
    premium — the widening must never be a loosening."""
    for p in (0.15, 0.30, 0.70, 0.85):
        th = POLICY.band_threshold(p, category)
        assert th == EXT_THRESHOLD and th > CORE_THRESHOLD


@pytest.mark.parametrize("category", DERIVATIVES)
def test_extension_is_stricter_than_moneyline_everywhere(category):
    """The claim in the config comment, asserted: a derivative price never
    trades on easier terms than the same moneyline price, whose bar rests on
    5.34M measured fills."""
    for p in _prices(0.10, 0.90):
        ml = POLICY.band_threshold(p, "moneyline")
        deriv = POLICY.band_threshold(p, category)
        if ml is not None:
            assert deriv >= ml, (p, ml, deriv)


@pytest.mark.parametrize("category", DERIVATIVES)
def test_both_sides_of_a_line_are_treated_alike(category):
    """The de-vig prices Over and Under together; a policy that trades one
    side and forbids its mirror is asserting an asymmetry the maths doesn't
    have.

    Checked from 0.11 up: bands are half-open [lo, hi), so the very bottom
    cent's mirror lands exactly on the excluded 0.90 bound. That is an
    interval-arithmetic artifact at one price, not an asymmetric policy."""
    for p in _prices(0.11, 0.50):
        assert (POLICY.band_threshold(p, category)
                == POLICY.band_threshold(round(1 - p, 2), category)), p


def test_the_moneyline_dead_zone_does_not_leak_into_derivatives():
    """40-45c is a moneyline phenomenon of the reference account's mechanism,
    and it is exactly where the kch123 spread edge was measured."""
    assert POLICY.band_threshold(0.43, "moneyline") is None
    assert POLICY.band_threshold(0.43, "spread") == CORE_THRESHOLD


def test_an_unknown_category_is_still_never_tradeable():
    assert POLICY.band_threshold(0.50, "player_prop") is None


# ── what it means at the decision layer ─────────────────────────────────

def test_an_expensive_spread_with_real_edge_now_trades():
    """fair 0.75 vs a 0.71 ask: 4c of edge that the old 0.40-0.55 window
    threw away for being on the expensive side of the line."""
    v = strategy_filter(POLICY, "nfl", 0.71, 0.75, category="spread")
    assert v.ok, v.reason


def test_a_thin_edge_in_the_extension_is_still_refused():
    """Widening the window is not lowering the bar: 2.6c clears the measured
    core but not the extension."""
    assert strategy_filter(POLICY, "nfl", 0.474, 0.50, category="spread").ok
    assert not strategy_filter(POLICY, "nfl", 0.704, 0.73, category="spread").ok


def test_category_blocks_still_win_over_a_wider_window():
    """NFL moneyline stays blocked no matter how wide the spread window is."""
    assert not strategy_filter(POLICY, "nfl", 0.50, 0.56, category="moneyline").ok


def test_the_implausibility_guard_still_applies_to_derivatives():
    assert not strategy_filter(POLICY, "nfl", 0.20, 0.50, category="spread").ok


# ── the generator ───────────────────────────────────────────────────────

def test_regeneration_is_idempotent_and_leaves_moneyline_alone():
    """bands.yaml has two owners. Running the derivative generator must not
    touch a single byte of the measured moneyline policy."""
    path = REPO / "config" / "bands.yaml"
    before = path.read_text()
    subprocess.run([sys.executable, "scripts/gen_category_bands.py"],
                   cwd=REPO, check=True, capture_output=True)
    after = path.read_text()
    assert after == before

    head = before.split("\n# Per-category band policy")[0]
    assert '"0.85-0.90": {edge_cents: 1.36' in head
    assert 'dead_zones: ["0.40-0.45", "0.65-0.70", "0.90-0.95", "0.95-1.00"]' in head


def test_generator_derives_the_mirror_rather_than_hardcoding_it():
    sys.path.insert(0, str(REPO / "scripts"))
    import gen_category_bands as g

    basis = {cent: b for cent, _th, b in g.windows_for("total")}
    assert basis[40] == "measured"       # kch123 core: 40-50c
    assert basis[60] == "mirror"         # the other side of the same de-vig
    assert basis[70] == "extension"


@pytest.mark.parametrize("category", DERIVATIVES)
def test_the_window_is_closed_under_mirroring(category):
    """Structural check on the generator, not the rendered file: the set of
    tradeable cents maps onto itself under c -> 100-c. If it doesn't, the
    policy is claiming an asymmetry the de-vig cannot support."""
    sys.path.insert(0, str(REPO / "scripts"))
    import gen_category_bands as g

    cents = {c for c, _th, _b in g.windows_for(category)}
    assert {100 - c for c in cents} == cents
