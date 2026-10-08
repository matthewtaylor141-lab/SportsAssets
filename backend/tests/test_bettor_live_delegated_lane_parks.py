"""A STOPPED DELEGATED bettor_live LANE IS NOT RESTARTED EVERY FIVE SECONDS.

THE DEFECT (production, release 08828d04, sportsassets-workers, read back
2026-10-07 through render-ops logs): with BETTOR_INCENTIVE_MANIFEST set and
`bettor_live_observation` = false, every five seconds all day --

    starting loop: bettor_live
    delegating to BETTOR_INCENTIVE_OBSERVE_V1 (BETTOR_INCENTIVE_MANIFEST is set)
    not observing (STOPPED_BY_CONTROL: bare boolean false); effective config ...
    loop bettor_live exited cleanly; restarting in 5s

`main()` delegated ABOVE the hold `_backoff` serves the general path, and the
delegate returned its refusal in ~3 ms, straight into the supervisor's
RESTART_DELAY_SECONDS.

THE CONTRACT PINNED HERE (workers/loop_contract.py):
  * a CONTROL stop parks in place and is released by `obs-run` with no
    deploy -- never LOOP_DISABLED, because operators flip that row live;
  * the delegated lane's ENVIRONMENT kill switch returns LOOP_DISABLED (only
    a deploy re-reads it); the general path is left exactly as it was;
  * a configuration / acquisition refusal backs off on the existing ladder;
  * a started run, a crash and every neighbouring loop are unchanged.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sportsassets import bettor_live_control as ctl
from sportsassets.workers import bettor_incentive_observe as obs
from sportsassets.workers import bettor_live_loop as bl
from sportsassets.workers.loop_contract import LOOP_DISABLED

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "sportsassets" / "workers" / "bettor_live_loop.py"

CONTROL_REASONS = (ctl.W_STOPPED, ctl.W_ABSENT, ctl.W_MALFORMED,
                   ctl.W_UNREADABLE, ctl.B_EXPIRED, ctl.B_EXHAUSTED,
                   ctl.B_UNREADABLE)


class SleepSpy:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


def _closed(why=ctl.W_STOPPED):
    return {"run": False, "why": why, "detail": "test", "readable": True}


def _open():
    return {"run": True, "why": ctl.W_RUN, "detail": "test", "readable": True}


@pytest.fixture
def incentive_mode(monkeypatch):
    """The production configuration: the manifest variable set, the kill
    switch not off, no pool resolved -- `main()` called with no arguments."""
    monkeypatch.setenv(bl._INCENTIVE_ENV, "/nonexistent/manifest.json")
    monkeypatch.delenv(bl.KILL_ENV, raising=False)
    bl._reset_backoff()
    yield
    bl._reset_backoff()


def _script_control(monkeypatch, answers, *, allowance_open=True):
    """`read_control` answers from `answers` (the last one repeats); the
    allowance answers open or closed. Counts every read."""
    reads = {"control": 0, "allowance": 0}

    async def read_control(_pool=None):
        i = min(reads["control"], len(answers) - 1)
        reads["control"] += 1
        return answers[i]

    async def read_allowance(_pool=None, **_kw):
        reads["allowance"] += 1
        if callable(allowance_open):
            ok = allowance_open(reads["allowance"])
        else:
            ok = allowance_open
        return {"open": bool(ok),
                "state": ctl.B_OPEN if ok else ctl.B_EXPIRED}

    monkeypatch.setattr(ctl, "read_control", read_control)
    monkeypatch.setattr(ctl, "read_incentive_allowance", read_allowance)
    return reads


async def _supervise_for(monkeypatch, virtual_s, loops):
    """Run the REAL supervisor over `loops` on a virtual clock: every
    `asyncio.sleep` (the supervisor's 5 s, the park's polls) advances it,
    and the run is cancelled once `virtual_s` has elapsed."""
    from sportsassets.workers import all as workers_all

    clock = {"t": 0.0}
    starts = {name: 0 for name, _ in loops}
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *a, **k):
        clock["t"] += float(delay)
        if clock["t"] > virtual_s:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(workers_all.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(workers_all._LH, "spawn_record",
                        lambda *a, **k: None)
    # A FRESH retirement set, so a LOOP_DISABLED here cannot leak into
    # another test's supervisor.
    monkeypatch.setattr(workers_all, "_DISABLED", set())

    def counted(name, fn):
        async def run():
            starts[name] += 1
            return await fn()
        return run

    tasks = [asyncio.ensure_future(workers_all.supervise(n, counted(n, f)))
             for n, f in loops]
    await asyncio.gather(*tasks, return_exceptions=True)
    return starts, clock["t"]


# ── 1. the production churn, reproduced through the real supervisor ────

def test_a_stopped_delegated_lane_is_not_restarted_every_five_seconds(
        monkeypatch, incentive_mode):
    """Ten virtual minutes with the row at false. The defect was 120
    starts (one per RESTART_DELAY_SECONDS); parked, it is ONE."""
    reads = _script_control(monkeypatch, [_closed()])
    starts, _t = asyncio.run(_supervise_for(
        monkeypatch, 600.0, [("bettor_live", bl.main)]))
    assert starts["bettor_live"] == 1, (
        "%d starts in 600 s: the stopped lane is churning"
        % starts["bettor_live"])
    # Quiescent but still listening: one read per IDLE_POLL_S, no more.
    assert reads["control"] <= 1 + int(600.0 / bl.IDLE_POLL_S)
    assert reads["allowance"] == 0, "a closed control reads nothing else"


def test_over_a_day_a_stopped_lane_restarts_at_most_hourly(
        monkeypatch, incentive_mode):
    """Bounded, not frozen: the park returns after PARK_MAX_S so the
    delegate re-logs its effective configuration -- 24 a day, not 17,280."""
    _script_control(monkeypatch, [_closed()])
    starts, _t = asyncio.run(_supervise_for(
        monkeypatch, 86400.0, [("bettor_live", bl.main)]))
    ceiling = 86400.0 / bl.PARK_MAX_S + 1
    assert 2 <= starts["bettor_live"] <= ceiling, starts


# ── 2. why it is a park and not LOOP_DISABLED ───────────────────────────

def test_obs_run_releases_the_parked_lane_without_a_deploy(
        monkeypatch, incentive_mode):
    """The operator writes the row (render-ops sql obs-run) and the SAME
    process notices on its next poll. LOOP_DISABLED could not do this."""
    reads = _script_control(monkeypatch,
                            [_closed(), _closed(), _closed(), _open()])
    spy = SleepSpy()
    out = asyncio.run(bl.main(sleep=spy))
    assert out != LOOP_DISABLED
    assert out["started"] is False and out["why"] == ctl.W_STOPPED
    assert out["parked"]["released"] is True
    assert spy.delays == [bl.IDLE_POLL_S] * 3
    assert reads["allowance"] == 1, "released only once the allowance read"


def test_release_latency_fits_inside_a_probe_deadline():
    """The 2026-09-22 arithmetic: an armed probe must be picked up many
    times over inside its deadline. Park poll + supervisor restart."""
    from sportsassets.workers import all as workers_all
    worst = bl.IDLE_POLL_S + workers_all.RESTART_DELAY_SECONDS
    assert worst < ctl.PROBE_DEADLINE_S / 10
    assert bl.IDLE_POLL_S <= ctl.CONTROL_EVERY_S, (
        "a parked lane must notice obs-run no later than a running lane "
        "notices obs-stop")


def test_an_open_control_with_a_closed_allowance_stays_parked(
        monkeypatch, incentive_mode):
    """`obs-run` without `obs-arm-incentive` is not permission: the lane
    stays parked until the allowance it would spend from is open too."""
    _script_control(monkeypatch, [_closed(), _open()],
                    allowance_open=lambda n: n >= 3)
    spy = SleepSpy()
    out = asyncio.run(bl.main(sleep=spy))
    assert out["parked"]["released"] is True
    assert out["parked"]["polls"] == 3
    assert spy.delays == [bl.IDLE_POLL_S] * 3


def test_an_unreadable_control_keeps_it_parked_and_never_releases_it(
        monkeypatch, incentive_mode):
    """Fail closed while parked exactly as on the way in: a row nobody can
    read is not a yes. The park ends only at PARK_MAX_S, released=False."""
    _script_control(monkeypatch, [_closed(ctl.W_UNREADABLE)])
    spy = SleepSpy()
    out = asyncio.run(bl.main(sleep=spy))
    assert out["why"] == ctl.W_UNREADABLE
    assert out["parked"]["released"] is False
    assert out["parked"]["last_why"] == ctl.W_UNREADABLE
    assert sum(spy.delays) == bl.PARK_MAX_S
    assert set(spy.delays) == {bl.IDLE_POLL_S}


@pytest.mark.parametrize("why", CONTROL_REASONS)
def test_every_control_reason_from_the_delegate_parks(
        monkeypatch, incentive_mode, why):
    async def refused(**_kw):
        return {"started": False, "why": why, "observe": obs.OBSERVE_VERSION}

    monkeypatch.setattr(obs, "run", refused)
    # The stubbed delegate reads nothing, so the park's first read is the
    # first read: closed once, then open.
    _script_control(monkeypatch, [_closed(), _open()])
    spy = SleepSpy()
    out = asyncio.run(bl.main(sleep=spy))
    assert out["why"] == why and out["parked"]["released"] is True
    assert spy.delays == [bl.IDLE_POLL_S] * 2


# ── 3. what is NOT a park ───────────────────────────────────────────────

def test_the_delegated_lane_s_kill_switch_is_LOOP_DISABLED(
        monkeypatch, incentive_mode):
    """Only a deploy re-reads the environment, so a restart could only
    repeat the answer (loop_contract). Nothing is slept, nothing parked."""
    _script_control(monkeypatch, [_open()])
    monkeypatch.setenv(bl.KILL_ENV, "off")
    spy = SleepSpy()
    assert asyncio.run(bl.main(sleep=spy)) == LOOP_DISABLED
    assert spy.delays == []


def test_the_general_path_is_unchanged_by_this_repair(monkeypatch,
                                                      incentive_mode):
    """Out of scope and pinned elsewhere (test_bettor_live_loop and
    scripts/bettor_lifecycle_integration.py L1): without the manifest
    variable, a kill switch still holds IDLE_POLL_S and returns its dict."""
    monkeypatch.delenv(bl._INCENTIVE_ENV)
    monkeypatch.setenv(bl.KILL_ENV, "off")
    spy = SleepSpy()
    out = asyncio.run(bl.main(sleep=spy))
    assert out == {"started": False, "why": "KILL_SWITCH"}
    assert spy.delays == [bl.IDLE_POLL_S]


def test_the_kill_switch_retires_only_this_loop_and_no_neighbour(
        monkeypatch, incentive_mode):
    from sportsassets.workers import all as workers_all
    monkeypatch.setenv(bl.KILL_ENV, "off")

    async def neighbour():
        return None

    starts, _t = asyncio.run(_supervise_for(
        monkeypatch, 60.0, [("bettor_live", bl.main),
                            ("neighbour", neighbour)]))
    assert starts["bettor_live"] == 1
    assert "bettor_live" in workers_all._DISABLED
    assert starts["neighbour"] >= 10, "a neighbouring loop was stopped"


@pytest.mark.parametrize("why", ["MANIFEST_ABSENT", "INSUFFICIENT_COVERAGE",
                                 "RUN_CLOSED", "RUN_ROW_UNREADABLE",
                                 "NO_CREDENTIALS"])
def test_a_configuration_refusal_backs_off_on_the_acquisition_ladder(
        monkeypatch, incentive_mode, why):
    async def refused(**_kw):
        return {"started": False, "why": why, "observe": obs.OBSERVE_VERSION}

    monkeypatch.setattr(obs, "run", refused)
    spy = SleepSpy()
    asyncio.run(bl.main(sleep=spy))
    asyncio.run(bl.main(sleep=spy))
    assert spy.delays == list(bl.NOSTART_BACKOFF_S[:2])


def test_an_enabled_lane_still_runs_and_is_held_by_nothing(
        monkeypatch, incentive_mode):
    report = {"started": True, "end_why": obs.END_WINDOW,
              "observe": obs.OBSERVE_VERSION}

    async def ran(**_kw):
        return dict(report)

    monkeypatch.setattr(obs, "run", ran)
    spy = SleepSpy()
    assert asyncio.run(bl.main(sleep=spy)) == report
    assert spy.delays == []


def test_a_bounded_run_serves_neither_a_park_nor_a_backoff(
        monkeypatch, incentive_mode):
    _script_control(monkeypatch, [_closed()])
    out = asyncio.run(asyncio.wait_for(bl.main(run_for_s=0.0), 5))
    assert out["why"] == ctl.W_STOPPED and "parked" not in out


def test_a_crash_in_the_delegate_is_still_restarted_by_the_supervisor(
        monkeypatch, incentive_mode):
    async def boom(**_kw):
        raise RuntimeError("delegate crashed")

    monkeypatch.setattr(obs, "run", boom)
    starts, _t = asyncio.run(_supervise_for(
        monkeypatch, 12.0, [("bettor_live", bl.main)]))
    assert starts["bettor_live"] == 3      # t=0, 5, 10: unchanged policy


def test_the_delegate_result_never_goes_straight_to_the_supervisor():
    """The structural cause, pinned in source: `main()` must route the
    delegate's answer through the contract, not return it."""
    src = SRC.read_text()
    assert "return await inc_obs.run(" not in src
    assert "_after_delegate(" in src
