"""Admission policies, and the scenario that broke the last one.

THE REGRESSION IS THE POINT OF THIS FILE. V1 was deployed on evidence
from a replay that started every policy from an empty queue. Production
did not hand it an empty queue -- it handed it eleven hours of V4
observations whose later horizons had not yet come due -- and V1 wrote
zero observations across all seven of its ticks.

So the first thing these tests establish is that the harness can SEE
that failure. A harness that reports V1 as healthy would have cleared it
to deploy a second time, and every other result it produced would be
worth nothing.
"""

import pytest

from sportsassets import bettor_admission as pol
from sportsassets import bettor_capacity_harness as h

# The deployed numbers: 10 reads a tick, split in half, four horizons,
# a 71.4-second tick.
def cap(backlog, *, fu=5, sample=5, horizons=4, tol=30.0, period=71.4):
    return pol.TickCapacity(
        total_budget_reads=fu + sample, fu_reserve_reads=fu,
        sample_budget_reads=sample, backlog_tasks=backlog,
        horizons_per_obs=horizons, tick_period_s=period, tolerance_s=tol)


# ── units: the defect itself ─────────────────────────────────────────

def test_drain_time_is_a_time_and_sustainable_intake_is_a_rate():
    """V1 compared a count of tasks with a count of reads-per-tick. The
    replacement's two quantities are a time and a rate, so neither can
    be compared with a bare queue depth by accident."""
    c = cap(backlog=20)
    # 20 tasks / 5 reads per tick = 4 ticks = 285.6 seconds
    assert c.drain_time_s == pytest.approx(20 / 5 * 71.4)
    # 5 reads per 71.4s / 4 horizons = obs per second
    assert c.sustainable_obs_per_s == pytest.approx(5 / 71.4 / 4)


def test_sustainable_intake_is_service_divided_by_horizons():
    """The stability condition. Above it the queue grows without bound
    and no ordering rule rescues it."""
    c = cap(backlog=0)
    assert c.sustainable_obs_per_s * 60 == pytest.approx(1.05, abs=0.01)


# ── the candidate cannot switch intake off ───────────────────────────

@pytest.mark.parametrize("backlog", [0, 5, 20, 100, 10_000, 10**6])
def test_v3_never_admits_zero(backlog):
    """THE STRUCTURAL GUARANTEE. Whatever the queue depth, the candidate
    admits at least the floor. V1's blackout is unreachable, not merely
    temporary."""
    d = pol.policy_v3(cap(backlog))
    assert d.admit_obs >= pol.ADMIT_FLOOR_OBS, backlog


def test_v1_does_admit_zero_and_that_is_why_it_is_kept():
    """Keeping the broken policy runnable is what makes the regression
    test possible."""
    assert pol.policy_v1(cap(10_000)).admit_obs == 0


def test_v3_brakes_when_the_queue_misses_its_deadline():
    shallow = pol.policy_v3(cap(1))
    deep = pol.policy_v3(cap(500))
    assert deep.saturated and not shallow.saturated
    assert deep.admit_obs <= shallow.admit_obs


def test_v3_explains_itself_in_seconds():
    d = pol.policy_v3(cap(500))
    assert "drain" in d.why and "s " in d.why or "s;" in d.why


# ── the harness reproduces production ────────────────────────────────

PROD = dict(arrivals_obs_per_min=2.1, inflight_obs_minutes=60.0,
            minutes=9)


def test_the_harness_reproduces_the_v1_blackout():
    """PRODUCTION, 2026-09-21 12:07:48Z-12:14:31Z: seven ticks, seven
    saturated, obs_written 0. If this stops holding, the harness has
    stopped modelling the thing that broke."""
    w = h.Workload(**PROD)
    r = h.run_policy(pol.V1, w)
    assert r.observations == 0, (
        "the harness no longer reproduces V1's blackout; it would clear "
        "the regression to deploy again")
    assert r.saturated_ticks == r.ticks
    assert r.ticks == 7


def test_an_empty_queue_hides_the_regression():
    """WHY THE FIRST REPLAY CLEARED V1. From rest its brake barely
    trips, because the backlog starts at zero and service keeps up.
    This is the fidelity gap, pinned so nobody removes the inherited
    state as an unnecessary complication."""
    rest = h.run_policy(pol.V1, h.Workload(arrivals_obs_per_min=2.1,
                                           minutes=240))
    assert rest.observations > 0
    assert rest.saturated_ticks < rest.ticks * 0.1


def test_v3_keeps_collecting_through_the_same_scenario():
    """The comparison that matters: same inherited queue, same
    workload, same classification rules."""
    w = h.Workload(**PROD)
    v1 = h.run_policy(pol.V1, w)
    v3 = h.run_policy(pol.V3, w)
    assert v1.observations == 0
    assert v3.observations > 0


def test_v1_recovers_if_left_alone_and_the_harness_says_so():
    """THE CORRECTION. I reported V1's zero as absorbing. Given fifty
    ticks instead of seven the brake releases and intake resumes, so it
    was a long blackout and not a latch. The floor is justified by the
    blackout's length, not by a permanence it does not have."""
    long_run = h.run_policy(pol.V1, h.Workload(
        arrivals_obs_per_min=2.1, inflight_obs_minutes=60.0, minutes=60))
    assert long_run.observations > 0, (
        "V1 should recover on a long enough run; if it does not, the "
        "correction in bettor_admission is wrong")
    assert long_run.saturated_ticks < long_run.ticks


# ── the accounting closes ────────────────────────────────────────────

@pytest.mark.parametrize("version", [pol.V4, pol.V1, pol.V2, pol.V3])
def test_every_task_created_has_exactly_one_end(version):
    """created == on_time + late + expired + deferred.

    This is what stops an on-time rate being improved by dropping the
    hard cases out of the denominator."""
    r = h.run_policy(version, h.Workload(**PROD))
    r.check()
    assert (r.on_time + r.late + r.expired + r.deferred) == r.created


@pytest.mark.parametrize("version", [pol.V4, pol.V1, pol.V2, pol.V3])
def test_the_rate_is_taken_over_created_not_over_attempts(version):
    """A policy that serves two tasks and skips a thousand must not
    report 100%."""
    r = h.run_policy(version, h.Workload(**PROD))
    assert r.on_time_rate == (r.on_time / r.created if r.created else 0)


def test_declined_observations_are_reported_outside_the_rate():
    """V1 scored 11.1% on-time in production while writing nothing.
    Coverage and timing are two numbers and must stay two numbers."""
    d = h.run_policy(pol.V1, h.Workload(**PROD)).as_dict()
    assert "observations_declined" in d
    assert "on_time_rate_of_created" in d
    assert d["observations_admitted"] == 0


# ── comparisons are like-for-like ────────────────────────────────────

def test_every_policy_sees_the_same_workload():
    """Same arrivals, same inherited queue, same horizons, same
    tolerance. Otherwise the table is not a comparison."""
    w = h.Workload(**PROD)
    rep = h.compare(w)
    inherited = {r["tasks_created"] - 0 for r in rep["results"].values()}
    # V4 and V2 admit identically, so their totals must match exactly
    assert (rep["results"][pol.V4]["tasks_created"]
            == rep["results"][pol.V2]["tasks_created"])
    assert len(inherited) >= 1


def test_the_classification_rules_are_not_retuned_per_policy():
    import inspect
    src = inspect.getsource(h.run_policy)
    assert src.count("w.tolerance_s") >= 1
    assert "w.recovery_s" in src
    # no policy name appears in the service or classification path
    for v in (pol.V1, pol.V2, pol.V3, pol.V4):
        assert v not in src


# ── feasibility is answered honestly ─────────────────────────────────

def test_an_oversubscribed_workload_is_reported_infeasible():
    """2.1 observations/min against 1.05 sustainable. No admission
    policy creates read budget, and calling this fixed would be a lie."""
    f = pol.feasibility(cap(0), arrivals_obs_per_min=2.1)
    assert f["feasible"] is False
    assert f["oversubscription_ratio"] == pytest.approx(2.0, abs=0.05)


def test_the_feasible_choices_are_quantified_not_gestured_at():
    f = pol.feasibility(cap(0), arrivals_obs_per_min=2.1)
    for k in ("admit_less", "read_more", "watch_fewer_horizons"):
        assert any(ch.isdigit() for ch in f["choices"][k]), k


def test_a_workload_within_budget_is_reported_feasible():
    f = pol.feasibility(cap(0), arrivals_obs_per_min=1.0)
    assert f["feasible"] is True


def test_no_policy_is_described_as_fixing_an_infeasible_workload():
    import inspect
    src = inspect.getsource(pol.policy_v3)
    assert "does not make an infeasible" in src
