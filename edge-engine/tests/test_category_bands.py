"""Derivative (spread/total) band windows, under the net-ROI floor.

Two policy layers produce these windows, and the tests pin both:

1. EVIDENCE (unchanged): the kch123-measured core keeps its measured 2.5c
   threshold; everything unmeasured pays a stricter 3.0c; tails stay shut
   because the venue's whole-cent grid swamps the edge there.

2. THE NET-ROI FLOOR (new): a band must pay
   (threshold - crossing_cost) / price >= 2% or it is excluded even where
   evidence would allow it. Profit is turnover x net ROI, and net ROI per
   fill collapses as price rises — 3.0c at 12c is ~13% net per fill and
   proves itself in hundreds of settlements; the same 3.0c at 85c is ~1.8%
   and needs thousands. The floor is why the windows are NO LONGER
   mirror-symmetric: the de-vig prices both sides of a line with equal
   confidence, but only the cheap side pays for the trip.
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
NET_ROI_FLOOR = 0.02
CROSSING = {"spread": 0.015, "total": 0.015, "prop": 0.006}


def _prices(lo, hi, step=0.01):
    p = lo
    while p < hi - 1e-9:
        yield round(p, 2)
        p = round(p + step, 2)


# ── the floor itself ────────────────────────────────────────────────────

@pytest.mark.parametrize("category", ("spread", "total", "prop"))
def test_every_tradeable_price_clears_the_net_roi_floor(category):
    """The invariant that defines the whole window: nothing is tradeable
    below the benchmark net of what we pay to cross. A band that fails this
    consumes budget and market claims while proving nothing for seasons."""
    for p in _prices(0.02, 0.99):
        th = POLICY.band_threshold(p, category)
        if th is None:
            continue
        net = (th - CROSSING[category]) / (p + 0.005)
        assert net >= NET_ROI_FLOOR - 1e-6, (p, th, net)


def test_the_cheap_half_is_open_and_the_expensive_tail_is_closed():
    """The floor's intended shape, spot-checked: same threshold, same
    evidence class, opposite verdicts purely because of price."""
    assert POLICY.band_threshold(0.12, "spread") == EXT_THRESHOLD
    assert POLICY.band_threshold(0.30, "spread") == EXT_THRESHOLD
    assert POLICY.band_threshold(0.85, "spread") is None
    assert POLICY.band_threshold(0.80, "total") is None


def test_asymmetry_is_the_floor_not_the_devig():
    """The de-vig prices Over and Under together with equal confidence —
    the old policy asserted mirror-symmetric windows for exactly that
    reason. The floor deliberately breaks the symmetry: a 20c side pays
    7.5% net where its 80c mirror pays 1.9%. Same measurement, one
    profitable trade."""
    assert POLICY.band_threshold(0.20, "spread") is not None
    assert POLICY.band_threshold(0.80, "spread") is None
    # ...and near the middle both sides survive, so the asymmetry only
    # appears where the economics actually diverge.
    assert POLICY.band_threshold(0.45, "spread") is not None
    assert POLICY.band_threshold(0.62, "spread") is not None


def test_props_keep_the_wide_window_because_their_costs_are_lower():
    """Props pay a 1c surcharge (3.5-4.0c bars) and cross a measured 0.54c
    spread — so even 90c props clear the floor. The floor is a cost
    calculation, not a price prejudice; cheap-to-trade categories keep
    their expensive prices."""
    assert POLICY.band_threshold(0.90, "prop") is not None
    assert POLICY.band_threshold(0.30, "prop") is not None


# ── evidence layers that survive the floor ──────────────────────────────

@pytest.mark.parametrize("category", DERIVATIVES)
def test_tails_stay_shut(category):
    """Whole-cent ticks mean a 1c error is a third of a 3c price. We cannot
    measure an edge finer than the grid we trade on."""
    for p in (0.01, 0.05, 0.09, 0.91, 0.95, 0.99):
        assert POLICY.band_threshold(p, category) is None, p


@pytest.mark.parametrize("category", DERIVATIVES)
def test_the_surviving_core_keeps_its_measured_threshold(category):
    """kch123's measured zone keeps the measured 2.5c bar where it clears
    the floor (0.40-0.50). Its upper half (0.50-0.55) died on the floor:
    2.5c minus 1.5c crossing at 52c is 1.9% — the measurement stands, the
    trade doesn't pay."""
    for p in _prices(0.40, 0.50):
        assert POLICY.band_threshold(p, category) == CORE_THRESHOLD
    assert POLICY.band_threshold(0.53, category) is None


@pytest.mark.parametrize("category", DERIVATIVES)
def test_the_unmeasured_extension_must_clear_a_higher_bar(category):
    """Everything outside the measured zone is inference, so it pays a
    premium — the widening must never be a loosening."""
    for p in (0.15, 0.30, 0.65):
        th = POLICY.band_threshold(p, category)
        assert th == EXT_THRESHOLD and th > CORE_THRESHOLD


@pytest.mark.parametrize("category", DERIVATIVES)
def test_extension_is_stricter_than_moneyline_everywhere(category):
    """A derivative price never trades on easier terms than the same
    moneyline price, whose bar rests on 5.34M measured fills."""
    for p in _prices(0.10, 0.90):
        ml = POLICY.band_threshold(p, "moneyline")
        deriv = POLICY.band_threshold(p, category)
        if ml is not None and deriv is not None:
            assert deriv >= ml, (p, ml, deriv)


def test_the_moneyline_dead_zone_does_not_leak_into_derivatives():
    """40-45c is a moneyline phenomenon of the reference account's mechanism,
    and it is exactly where the kch123 spread edge was measured."""
    assert POLICY.band_threshold(0.43, "moneyline") is None
    assert POLICY.band_threshold(0.43, "spread") == CORE_THRESHOLD


def test_an_unknown_category_is_still_never_tradeable():
    assert POLICY.band_threshold(0.50, "player_prop") is None


def test_moneyline_floor_gates_on_measured_capture_not_threshold():
    """The floor's numerator is the band's MEASURED edge where one exists
    (never below the threshold). 0.45-0.50 measured +2.94c and nets 3.0%
    after the 1.5c crossing — open. 0.75-0.90 measured +1.4-2.6c and
    cannot pay the benchmark at those prices — dead despite being
    measured-positive. The cheap half pays 5-60% and stays open."""
    assert POLICY.band_threshold(0.47, "moneyline") is not None
    for p in (0.53, 0.62, 0.78, 0.87):
        assert POLICY.band_threshold(p, "moneyline") is not None, p
        for p in (0.42, 0.67, 0.93, 0.97):
            assert POLICY.band_threshold(p, "moneyline") is None, p
    for p in (0.07, 0.17, 0.32, 0.37):
        assert POLICY.band_threshold(p, "moneyline") is not None, p


# ── what it means at the decision layer ─────────────────────────────────

def test_an_expensive_spread_with_real_edge_now_trades():
    """fair 0.75 vs a 0.71 ask: 4c of edge on the expensive-but-still-
    paying side of the line (0.61-0.75)."""
    v = strategy_filter(POLICY, "nfl", 0.71, 0.75, category="spread")
    assert v.ok, v.reason


def test_beyond_the_floor_even_a_real_edge_is_refused():
    """3.5c at 0.85 clears every evidence bar and still doesn't trade:
    net of crossing it pays under the benchmark, and its settlements would
    prove nothing. This is the floor refusing a real edge, on purpose."""
    assert not strategy_filter(POLICY, "nfl", 0.85, 0.885,
                               category="spread").ok


def test_a_thin_edge_in_the_extension_is_still_refused():
    """Widening the window is not lowering the bar: 2.6c clears the measured
    core but not the extension."""
    assert strategy_filter(POLICY, "nfl", 0.474, 0.50, category="spread").ok
    assert not strategy_filter(POLICY, "nfl", 0.114, 0.14, category="spread").ok


def test_category_blocks_still_win_over_a_wider_window():
    """NFL moneyline stays blocked no matter how wide the spread window is."""
    assert not strategy_filter(POLICY, "nfl", 0.30, 0.36, category="moneyline").ok


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
    # The floor moved the expensive bands into the dead list; the measured
    # edge figures stay in the file via the tradeable half.
    assert '"0.30-0.35": {edge_cents: 3.59' in head
    assert '"0.65-0.70"' in head.split("dead_zones:")[1]
    assert '"0.85-0.90"' not in head.split("dead_zones:")[1], \
        "50-90c reopened under maker-first economics (2026-08-04)"


def test_generator_still_derives_the_mirror_before_the_floor():
    """Mirroring is still how the evidence propagates — the floor is
    applied AFTER it. With the floor lifted, the mirror label must
    reappear exactly where the old policy had it; with it applied, no
    surviving band may violate the floor."""
    sys.path.insert(0, str(REPO / "scripts"))
    import gen_category_bands as g

    old_floor = g.NET_ROI_FLOOR
    try:
        g.NET_ROI_FLOOR = -1.0          # lift: pure evidence view
        basis = {cent: b for cent, _th, b in g.windows_for("total")}
        assert basis[40] == "measured"   # kch123 core: 40-50c
        assert basis[60] == "mirror"     # the other side of the same de-vig
        assert basis[70] == "extension"
    finally:
        g.NET_ROI_FLOOR = old_floor

    for cent, th, _b in g.windows_for("total"):
        net = (th - g.CROSSING["total"]) / ((cent + 0.5) / 100)
        assert net >= g.NET_ROI_FLOOR - 1e-9, (cent, th)
