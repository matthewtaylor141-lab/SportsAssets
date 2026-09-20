"""THE W3 ALLOCATION RULE, VALIDATED OFFLINE BEFORE DEPLOYMENT.

Every scheduling defect so far cost a deploy and a thirty-minute
window to find, and each window tested exactly one arrival pattern.
These run the allocation rule against sustained backlog, starved
budgets, rate limiting and W1's measured tick spacing, deterministically.

Nothing here observes a venue. No result in this file is evidence about
markets, fills or profitability -- it exercises an allocation rule.
"""

from sportsassets import bettor_schedule_sim as sim

W3 = dict(rotate=True, per_horizon_cap=True, early_s=30,
          rotate_on_service=True)
W3_TICK_INDEX_ROTATION = dict(rotate=True, per_horizon_cap=True,
                              early_s=30, rotate_on_service=False)
CURRENT = dict(rotate=False, per_horizon_cap=False, early_s=0)

# W1 measured 65/72/76/82. A single fixed value is a claim about that
# value; cycling the measured ones is the closer model.
MEASURED_SPACINGS = (65.0, 72.0, 76.0, 82.0)


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


def test_tick_index_rotation_aliases_against_the_budget_one_parity_rule():
    """THE DEFECT I PREVIOUSLY REPORTED AS CAPACITY.

    I wrote that at budget=1 the 900s horizon is "lost to capacity" and
    left a test asserting the loss. It was not capacity. At budget=1
    follow-ups run only on ODD ticks, and a rotation keyed to the tick
    index takes its offset as i % 4, so serving ticks only ever use
    offsets 1 and 3. Two of the four horizons are never at the head of
    the order, and with one read and a per-horizon cap of one the head
    takes it whenever it has any candidate at all.

    900s therefore received ZERO attempts against a standing backlog of
    eligible observations -- not attempts that arrived late."""
    r = sim.run(ticks=80, spacing=72.0, budget_fn=lambda i: 1,
                **W3_TICK_INDEX_ROTATION)
    assert r["horizonsNeverAtRotationHead"] == [60, 900]
    assert r["allocatedAttempts"][900] == 0
    # and it was not for want of work to do
    assert r["eligibleDemandDistinctObservations"][900] > 20
    assert r["starvedHorizons"] == [900]


def test_service_opportunity_rotation_removes_the_aliasing():
    """Advancing the rotation once per tick that can actually serve a
    follow-up cannot alias against the parity rule: consecutive serving
    ticks take consecutive offsets by construction."""
    r = sim.run(ticks=80, spacing=72.0, budget_fn=lambda i: 1, **W3)
    assert r["horizonsNeverAtRotationHead"] == []
    assert r["starvedHorizons"] == []
    heads = r["rotationHeadOnServingTicks"]
    assert len(set(heads.values())) == 1, heads    # exactly even
    for h in sim.HORIZONS:
        assert r["allocatedAttempts"][h] > 0, h


def test_the_repair_is_a_no_op_at_the_deployed_budget_floor():
    """The deployed worker floors the tick budget at 2, where both
    passes run every tick and the parity rule never engages. The change
    must therefore alter nothing at or above that floor."""
    for budget in (2, 3, 7):
        a = sim.run(ticks=80, spacing=MEASURED_SPACINGS,
                    budget_fn=lambda i, b=budget: b,
                    **W3_TICK_INDEX_ROTATION)
        b = sim.run(ticks=80, spacing=MEASURED_SPACINGS,
                    budget_fn=lambda i, b=budget: b, **W3)
        assert a["onTimeResults"] == b["onTimeResults"], budget
        assert a["allocatedAttempts"] == b["allocatedAttempts"], budget


def test_service_is_not_the_same_as_meeting_the_timing_requirement():
    """Every horizon being served does not mean every horizon can be
    served ON TIME. At budget=1 all four receive attempts and three
    still return zero on-time reads. That is an observed capacity and
    timing limitation, reported rather than papered over, and it is NOT
    a reason to widen the tolerance."""
    r = sim.run(ticks=80, spacing=MEASURED_SPACINGS,
                budget_fn=lambda i: 1, **W3)
    assert r["starvedHorizons"] == []
    for h in sim.HORIZONS:
        assert r["allocatedAttempts"][h] > 0, h
    missed = [h for h in sim.HORIZONS if r["onTimeResults"][h] == 0]
    assert missed, "budget=1 is not expected to meet every horizon"
    assert sim.TOLERANCE_S == 30       # unchanged by any of this


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


def test_a_tick_inside_the_band_is_not_a_request_completing_inside_it():
    """Three different quantities, and conflating them would have had
    me report a scheduler defect that does not exist.

      tick in band         a tick fell inside [T0+h, T0+h+30]
      SERVING tick in band that tick could also issue a follow-up
      on-time result       a request actually completed inside it

    At budget=1 the 300s horizon has 38 observations with a tick in
    band and ZERO with a serving tick in band, because sampling runs on
    even ticks and follow-ups on odd ones, so every follow-up lag is an
    odd multiple of the spacing. The chances were never chances."""
    r = sim.run(ticks=80, spacing=72.0, budget_fn=lambda i: 1, **W3)
    assert r["observationsWithATickInBand"][300] > 0
    assert r["observationsWithASERVINGTickInBand"][300] == 0
    assert r["onTimeResults"][300] == 0
    for h in sim.HORIZONS:
        assert (r["observationsWithASERVINGTickInBand"][h]
                <= r["observationsWithATickInBand"][h]), h


def test_one_fixed_spacing_decides_which_horizons_are_reachable():
    """WHY THE SIMULATOR MUST NOT RUN AT A SINGLE SPACING.

    At exactly 72.0s, 900/72 = 12.5, so the nearest attainable lags are
    864 and 936 -- both 36s out, both outside the unchanged +-30 band.
    900s is unreachable on time at that spacing for ANY allocation
    rule. At 65, 76 and 82 it is reachable. A zero measured at one
    spacing is a fact about the spacing, not about the scheduler, and
    production spacing is not fixed."""
    pathological = sim.run(ticks=80, spacing=72.0,
                           budget_fn=lambda i: 3, **W3)
    assert pathological["observationsWithASERVINGTickInBand"][900] == 0
    assert pathological["onTimeResults"][900] == 0
    assert pathological["allocatedAttempts"][900] > 0   # served, not starved

    for spacing in (65.0, 76.0, 82.0):
        r = sim.run(ticks=80, spacing=spacing, budget_fn=lambda i: 3,
                    **W3)
        assert r["onTimeResults"][900] > 0, spacing


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


# ── the measured opportunity result, pinned ─────────────────────────

def test_the_phase_approximation_is_withdrawn_against_measurement():
    """I estimated 42% of observations would have a tick inside the
    300s band from a phase argument. Measured, it was 3%. The estimate
    assumed T0 independent of tick boundaries; the same tick loop
    writes the observation and runs the follow-up pass, so lags are
    quantised to multiples of the tick spacing."""
    from sportsassets import bettor_state_capture as sc
    m = sc.MEASURED_ONTIME_OPPORTUNITY
    assert m["hadATickInBand"][300] == 2
    assert m["bandElapsed"][300] == 66
    assert "withdrawn" in m["phaseApproximationWithdrawn"]

    # the quantised model reproduces every measured figure
    sp, off = m["tickSpacingP50S"], m["obsOffsetFromItsTickP50S"]
    reachable = {}
    for h in sim.HORIZONS:
        lo, hi = (h + off) / sp, (h + 30 + off) / sp
        reachable[h] = any(lo <= k <= hi for k in range(80))
    assert reachable == {60: True, 300: False, 900: True, 3600: False}
    # and the measured counts agree: reachable horizons are near-total
    # or substantial, unreachable ones are jitter only
    assert m["hadATickInBand"][60] == m["bandElapsed"][60]
    assert m["hadATickInBand"][3600] == 0


def test_early_eligibility_makes_the_300s_horizon_reachable_at_all():
    """The mechanism of the change, checked as arithmetic rather than
    asserted in prose."""
    from sportsassets import bettor_state_capture as sc
    m = sc.MEASURED_ONTIME_OPPORTUNITY
    sp, off = m["tickSpacingP50S"], m["obsOffsetFromItsTickP50S"]

    def reachable(h, early):
        lo, hi = (h - early + off) / sp, (h + 30 + off) / sp
        return any(lo <= k <= hi for k in range(80))

    assert not reachable(300, 0)                              # V2
    assert reachable(300, sc.HORIZON_EARLY_ELIGIBILITY_S)     # V3
    # and it is honest about what it does not fix
    assert not reachable(3600, sc.HORIZON_EARLY_ELIGIBILITY_S)
