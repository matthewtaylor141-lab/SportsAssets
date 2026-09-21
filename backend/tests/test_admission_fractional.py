"""Admission accounting: the V3 defect, and what replaces it.

THE DEFECT, reproduced by independent review at 195ee01. V3 read

    max(ADMIT_FLOOR_OBS, int(cap.sustainable_obs_per_tick))

and at the configuration the capacity report itself recommended -- a
45-second tick, two reserved follow-up reads, four horizons --
sustainable_obs_per_tick is 0.5, int(0.5) is 0, and the floor turns that
into 1. Every tick. Backlog 100 included.

    admitted        1 obs/tick  = 1.333 obs/min
    tasks created   x 4 horizons = 5.333 tasks/min
    service          2 reads/45s = 2.667 reads/min

Twice capacity, and twice the 0.667 obs/min the report claimed. The
fractional intake in the report was never implemented.

WHAT THESE TESTS HOLD THE REPLACEMENT TO:
  * the long-run rate matches the sustainable rate, not a rounding of it
  * zero admissions on some ticks -- necessary, and different from a
    permanent latch, which is asserted separately
  * forward obligations across all four horizons are counted
  * banked credits are bounded, so an outage cannot burst on recovery
  * recovery happens after capacity returns, without shoving new work
    into a queue that is still draining
  * varying capacity, read failures and backlog drainage
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_admission as adm

HORIZONS = 4
LONGEST_HORIZON_S = 3600.0


def cap(*, reserve=2, tick_s=45.0, backlog=0, outstanding=0, sample=2,
        success=1.0, total=4):
    return adm.TickCapacity(
        total_budget_reads=total,
        fu_reserve_reads=reserve,
        sample_budget_reads=sample,
        backlog_tasks=backlog,
        horizons_per_obs=HORIZONS,
        tick_period_s=tick_s,
        tolerance_s=30.0,
        outstanding_tasks=outstanding,
        longest_horizon_s=LONGEST_HORIZON_S,
        service_success_rate=success,
    )


# ── the defect, kept as a fixture ────────────────────────────────────

class TestV3IsARecordedDefect:

    def test_v3_admits_one_per_tick_with_a_backlog_of_one_hundred(self):
        """The review's exact reproduction. V3 stays in the module so
        any replacement can be run against it."""
        c = cap(backlog=100, outstanding=400)
        assert c.sustainable_obs_per_tick == pytest.approx(0.5)

        d = adm.policy_v3(c)
        assert d.admit_obs == 1, "V3's defect has been edited away; the "\
                                 "regression fixture no longer reproduces"
        assert d.saturated is True, "and it knew the queue was saturated"

    def test_v3_creates_exactly_twice_the_service_rate(self):
        c = cap(backlog=100, outstanding=400)
        admit = adm.policy_v3(c).admit_obs

        obs_per_min = admit * 60.0 / c.tick_period_s
        tasks_per_min = obs_per_min * HORIZONS
        service_per_min = c.fu_reserve_reads * 60.0 / c.tick_period_s

        assert obs_per_min == pytest.approx(1.333, abs=0.001)
        assert tasks_per_min == pytest.approx(5.333, abs=0.001)
        assert service_per_min == pytest.approx(2.667, abs=0.001)
        assert tasks_per_min == pytest.approx(2 * service_per_min, abs=0.01)

    def test_the_v1_latch_is_still_reproducible_too(self):
        """Both regressions, not just the newest one."""
        c = cap(reserve=2, backlog=100)
        assert adm.policy_v1(c).admit_obs == 0, "2 // 4 == 0"


# ── the replacement: the rate is right over the long run ─────────────

class TestFractionalRateIsTheSustainableRate:

    def test_one_observation_every_two_ticks_at_the_example_capacity(self):
        """The theoretical maximum the review names, before headroom."""
        acct = adm.AdmissionAccount()
        c = cap()
        admits = [acct.decide(c).admit_obs for _ in range(40)]

        assert set(admits) <= {0, 1}, "no tick may admit more than the "\
                                      "credits earned"
        # 0.5/tick nominal, x TARGET_UTILISATION for recovery headroom.
        expected = 0.5 * adm.TARGET_UTILISATION
        assert acct.admitted_obs_per_tick == pytest.approx(expected,
                                                           abs=0.03)

    def test_intake_never_exceeds_service(self):
        """The property the whole thing exists for, stated in reads."""
        acct = adm.AdmissionAccount()
        c = cap()
        for _ in range(200):
            acct.decide(c)

        tasks_per_tick = acct.admitted_obs_per_tick * HORIZONS
        service_per_tick = c.effective_reserve_reads
        assert tasks_per_tick <= service_per_tick, (
            "%.3f tasks/tick created against %.3f reads/tick of service"
            % (tasks_per_tick, service_per_tick))

    def test_zero_ticks_happen_and_are_not_a_latch(self):
        """Zero on SOME ticks is necessary. Zero on ALL ticks is V1."""
        acct = adm.AdmissionAccount()
        c = cap()
        for _ in range(40):
            acct.decide(c)

        assert acct.zero_ticks > 0, "a sub-unit rate must produce zero "\
                                    "ticks; otherwise it is rounding up"
        assert acct.admitted_total > 0, "it admitted nothing at all -- "\
                                        "that is the V1 latch"

    def test_a_larger_reserve_admits_more_than_one_per_tick(self):
        """The accumulator is not a cap of one. At reserve 5 on a 71s
        tick the sustainable rate is 1.25/tick and it must reach it."""
        acct = adm.AdmissionAccount()
        c = cap(reserve=5, tick_s=71.0, sample=5, total=10)
        for _ in range(200):
            acct.decide(c)

        expected = c.sustainable_obs_per_tick * adm.TARGET_UTILISATION
        assert expected > 1.0
        assert acct.admitted_obs_per_tick == pytest.approx(expected,
                                                           abs=0.05)


# ── forward obligations across all four horizons ─────────────────────

class TestForwardObligations:

    def test_obligations_not_yet_due_still_stop_admission(self):
        """An observation admitted a moment ago owes four reads and
        none is due yet, so backlog is 0 while demand is real."""
        acct = adm.AdmissionAccount()
        # 400 committed reads at 2 effective reads per 45s tick is
        # 9,000s of work against a 3,630s window.
        c = cap(backlog=0, outstanding=400)
        assert c.obligation_time_s > c.commitment_window_s

        d = acct.decide(c)
        assert d.admit_obs == 0
        assert d.saturated is True
        assert "commitment window" in d.why

    def test_a_modest_forward_book_still_admits(self):
        acct = adm.AdmissionAccount()
        c = cap(backlog=0, outstanding=40)
        for _ in range(10):
            acct.decide(c)
        assert acct.admitted_total > 0

    def test_a_missing_outstanding_count_is_reported_not_assumed(self):
        """Silently substituting backlog_tasks would understate demand,
        which is the permissive choice. It says so instead."""
        acct = adm.AdmissionAccount()
        c = adm.TickCapacity(
            total_budget_reads=4, fu_reserve_reads=2, sample_budget_reads=2,
            backlog_tasks=0, horizons_per_obs=4, tick_period_s=45.0,
            tolerance_s=30.0)          # outstanding_tasks left unset
        assert c.obligation_time_s is None
        d = acct.decide(c)
        assert "did NOT run" in d.why


# ── bounded credits: an outage must not bank a burst ─────────────────

class TestCreditsAreBounded:

    def test_a_long_saturated_stretch_does_not_bank_a_burst(self):
        acct = adm.AdmissionAccount()
        blocked = cap(backlog=1000, outstanding=4000)
        for _ in range(100):
            assert acct.decide(blocked).admit_obs == 0

        assert acct.credits_obs <= max(1.0, 0.5 * adm.TARGET_UTILISATION), (
            "100 blocked ticks banked %.2f observations of credit"
            % acct.credits_obs)

        clear = cap(backlog=0, outstanding=0)
        first = acct.decide(clear).admit_obs
        assert first <= 1, "recovery admitted a burst of %d" % first

    def test_recovery_reaches_the_steady_rate_after_the_queue_clears(self):
        acct = adm.AdmissionAccount()
        blocked = cap(backlog=1000, outstanding=4000)
        for _ in range(50):
            acct.decide(blocked)
        assert acct.admitted_total == 0

        clear = cap(backlog=0, outstanding=0)
        admitted_before = acct.admitted_total
        for _ in range(100):
            acct.decide(clear)
        recovered = acct.admitted_total - admitted_before

        expected = 100 * 0.5 * adm.TARGET_UTILISATION
        assert recovered == pytest.approx(expected, abs=3), (
            "recovered rate %d over 100 ticks, expected about %.0f"
            % (recovered, expected))

    def test_no_reserve_earns_no_credit(self):
        """Capacity gone means no rate to bank, not a debt to repay."""
        acct = adm.AdmissionAccount()
        dead = cap(reserve=0, sample=0)
        for _ in range(50):
            assert acct.decide(dead).admit_obs == 0
        assert acct.credits_obs == 0.0


# ── varying capacity, failures, drainage ─────────────────────────────

class TestVaryingConditions:

    def test_measured_read_failures_reduce_intake(self):
        """Failed reads consume reserve without discharging an
        obligation, so nominal reserve overstates service by exactly
        the success rate."""
        good = adm.AdmissionAccount()
        bad = adm.AdmissionAccount()
        for _ in range(300):
            good.decide(cap(success=1.0))
            bad.decide(cap(success=0.5))

        assert bad.admitted_obs_per_tick == pytest.approx(
            good.admitted_obs_per_tick * 0.5, abs=0.03)

    def test_capacity_that_rises_and_falls(self):
        acct = adm.AdmissionAccount()
        created = 0
        served = 0.0
        for i in range(400):
            reserve = [1, 2, 3, 5][i % 4]
            c = cap(reserve=reserve, sample=reserve, total=reserve * 2)
            created += acct.decide(c).admit_obs * HORIZONS
            served += c.effective_reserve_reads
        assert created <= served, (
            "%d tasks created against %.0f reads of service" % (created,
                                                                served))

    def test_a_backlog_actually_drains(self):
        """Arrivals equal to nominal service is not a stable queue -- an
        excursion never recovers. Headroom is what drains it.

        THE ASSERTION IS NOT "REACHES ZERO", and my first version of it
        was. This run drains 60 to 2 and holds at 2, which is correct:
        a collector that is admitting at all always has work in flight,
        and an empty queue would mean it had stopped. What has to be
        true is that the excursion is REMOVED and the residual is
        BOUNDED -- one observation's worth of obligations, not a
        backlog that merely stops growing.
        """
        acct = adm.AdmissionAccount()
        backlog = 60.0
        peak = backlog
        for _ in range(400):
            c = cap(backlog=int(backlog), outstanding=int(backlog))
            backlog += acct.decide(c).admit_obs * HORIZONS
            backlog = max(0.0, backlog - c.effective_reserve_reads)
            peak = max(peak, backlog)

        assert backlog <= HORIZONS, (
            "backlog settled at %.1f, above one observation's worth of "
            "obligations" % backlog)
        assert peak <= 60.0, "the backlog grew to %.1f before draining" % peak
        assert acct.admitted_total > 0, "it drained by refusing to collect"

    def test_admission_never_exceeds_the_sample_budget(self):
        acct = adm.AdmissionAccount()
        acct.credits_obs = 50.0                     # absurd, on purpose
        d = acct.decide(cap(sample=2, reserve=5, tick_s=71.0))
        assert d.admit_obs <= 2


class TestTheFloorIsNoLongerMandatory:

    def test_admit_floor_is_not_applied_by_the_account(self):
        """ADMIT_FLOOR_OBS stays in the module as the record of why V1
        failed. It must not be in the admission path."""
        import inspect

        src = inspect.getsource(adm.AdmissionAccount)
        assert "ADMIT_FLOOR_OBS" not in src, (
            "the mandatory per-tick floor is back; it cannot express a "
            "rate below one observation per tick")

    def test_all_four_horizons_are_still_assumed(self):
        c = cap()
        assert c.horizons_per_obs == 4
        assert c.longest_horizon_s == 3600.0
