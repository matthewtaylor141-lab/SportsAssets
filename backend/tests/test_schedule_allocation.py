"""THE W3 ALLOCATION RULE, VALIDATED OFFLINE BEFORE DEPLOYMENT.

Every scheduling defect so far cost a deploy and a thirty-minute
window to find, and each window tested exactly one arrival pattern.
These run the allocation rule against sustained backlog, starved
budgets, rate limiting and W1's measured tick spacing, deterministically.

Nothing here observes a venue. No result in this file is evidence about
markets, fills or profitability -- it exercises an allocation rule.
"""

from sportsassets import bettor_schedule_sim as sim

W3 = dict(rotate=True, per_horizon_cap=True, early_s=30)
CURRENT = dict(rotate=False, per_horizon_cap=False, early_s=0)


def test_the_current_scheduler_starves_every_horizon_after_the_first():
    """Reproduces the W1 shape: 10 reads at 60s, zero at 300s, 900s and
    3600s. The first horizon in the list claims the whole budget every
    tick, so the rest are starved deterministically -- not by capacity."""
    r = sim.run(ticks=60, spacing=72.0, budget_fn=lambda i: 3, **CURRENT)
    assert r["starvedHorizons"] == [300, 900, 3600]
    assert r["served"][60]["on_time"] > 0


def test_rotation_and_a_per_horizon_cap_remove_the_starvation():
    r = sim.run(ticks=60, spacing=72.0, budget_fn=lambda i: 3, **W3)
    assert r["starvedHorizons"] == []
    for h in sim.HORIZONS:
        total = r["served"][h]["on_time"] + r["served"][h]["late"]
        assert total > 0, h


def test_a_higher_on_time_count_can_mean_worse_coverage():
    """THE TRAP. The current scheduler posts a HIGHER on-time total
    than W3 precisely because it serves only the easiest horizon and
    ignores three others. On-time count alone is not a coverage
    measure."""
    cur = sim.run(ticks=60, spacing=72.0, budget_fn=lambda i: 3, **CURRENT)
    w3 = sim.run(ticks=60, spacing=72.0, budget_fn=lambda i: 3, **W3)
    cur_on = sum(v["on_time"] for v in cur["served"].values())
    w3_on = sum(v["on_time"] for v in w3["served"].values())
    assert cur_on > w3_on            # looks better
    assert len(cur["starvedHorizons"]) == 3   # is worse
    assert w3["starvedHorizons"] == []


# ── the budget=1 case the owner named ────────────────────────────────

def test_a_single_read_tick_alternates_instead_of_starving_one_side():
    """total//2 == 0 at total_budget=1, so a static split gives the
    follow-up pass nothing forever. Alternating by tick parity gives
    each side one read per two ticks."""
    r = sim.run(ticks=80, spacing=72.0, budget_fn=lambda i: 1, **W3)
    assert not r["anyTickOverBudget"]
    served = sum(v["on_time"] + v["late"] for v in r["served"].values())
    assert served > 0, "follow-ups must not be starved to zero"
    assert r["observations"] > 0, "sampling must not be starved to zero"


def test_at_one_read_per_tick_some_horizon_is_still_lost():
    """Reported, not hidden. Capacity of 0.5 follow-ups/tick cannot
    serve four horizons; the allocation rule decides which is lost, it
    does not prevent the loss."""
    r = sim.run(ticks=80, spacing=72.0, budget_fn=lambda i: 1, **W3)
    assert r["starvedHorizons"], (
        "if this ever passes with no starvation at budget=1, the "
        "capacity arithmetic has changed and should be re-derived")


# ── the budget is never exceeded, under any stress ───────────────────

def test_requests_never_exceed_the_tick_budget_in_any_scenario():
    scenarios = (
        ("flat 1", lambda i: 1), ("flat 2", lambda i: 2),
        ("flat 3", lambda i: 3), ("flat 7", lambda i: 7),
        ("oscillating", lambda i: 1 + (i % 7)),
    )
    for name, fn in scenarios:
        for spacing in (65.0, 72.0, 82.0):
            for arrivals in (2.6, 6.0):
                r = sim.run(ticks=60, spacing=spacing, budget_fn=fn,
                            arrivals_per_tick=arrivals, **W3)
                assert not r["anyTickOverBudget"], (name, spacing,
                                                    arrivals)


def test_rate_limiting_reduces_but_does_not_zero_the_follow_up_share():
    r = sim.run(ticks=80, spacing=72.0, budget_fn=lambda i: 3,
                rate_limit_every=3, **W3)
    assert not r["anyTickOverBudget"]
    assert r["starvedHorizons"] == []


def test_a_sustained_backlog_does_not_reintroduce_starvation():
    r = sim.run(ticks=80, spacing=72.0, budget_fn=lambda i: 3,
                arrivals_per_tick=6, **W3)
    assert r["starvedHorizons"] == []


def test_measured_tick_spacing_is_used_not_the_nominal_sixty():
    """Ticks are not 60s apart. W1 measured min 65, p50 72, max 82."""
    assert sim.W1_TICK_SPACING["p50"] == 72.0
    assert sim.W1_TICK_SPACING["min"] > 60.0
    for spacing in (65.0, 72.0, 82.0):
        r = sim.run(ticks=40, spacing=spacing, budget_fn=lambda i: 3,
                    **W3)
        assert not r["anyTickOverBudget"], spacing


# ── on-time opportunities outrank late recovery ──────────────────────

def test_on_time_candidates_are_served_before_late_recoveries():
    """A late recovery is still worth taking, but never at the cost of
    an observation that could still be read on time."""
    import inspect
    src = inspect.getsource(sim.run)
    assert "ON-TIME OPPORTUNITIES FIRST" in src
    r = sim.run(ticks=60, spacing=72.0, budget_fn=lambda i: 3,
                arrivals_per_tick=6, **W3)
    on = sum(v["on_time"] for v in r["served"].values())
    late = sum(v["late"] for v in r["served"].values())
    assert on > late, (on, late)


# ── the tolerance itself is untouched ────────────────────────────────

def test_early_eligibility_is_a_sampling_change_not_a_tolerance_change():
    """Selecting an observation 30s before its horizon does not widen
    what counts as on time: |lag - horizon| <= 30 is unchanged, and a
    read at lag 45 for a 60s horizon was always on time."""
    assert sim.TOLERANCE_S == 30
    assert sim.on_time(45, 60) is True      # early, inside tolerance
    assert sim.on_time(90, 60) is True      # late, inside tolerance
    assert sim.on_time(95, 60) is False
    assert sim.on_time(25, 60) is False
    import inspect
    doc = inspect.getdoc(sim.eligible)
    assert "NOT a change to the tolerance" in doc or "NOT" in doc
    assert "versioned sampling change" in doc.lower()
