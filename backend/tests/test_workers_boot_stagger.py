"""The workers' boot is staggered: every loop's FIRST start is offset by
its LOOPS index, and only the first.

THE BOOT STAMPEDE (outage review 2026-09-05). workers/all.py gathers
_record_boot plus every supervised loop in one call, so each boot fired
every loop's opening database read in the same instant. With get_pool
single-flight (db.py) that is one pool, not seventeen -- but seventeen
opening reads still queued on its ten connections, and the API's
/healthz read size == max, idle == 0 at 01:06Z from a worker that had
done no real work yet.

Pinned against the REAL supervise() with asyncio.sleep recorded and
returning at once, and against the REAL main() with LOOPS, supervise
and the boot marker faked around it:

  * supervise(boot_delay=1.5) sleeps 1.5 ONCE, before the first call of
    the factory; a crash and a clean exit are each followed by
    RESTART_DELAY_SECONDS and nothing else -- the boot delay is never
    re-applied on a restart
  * main() hands each LOOPS entry boot_delay = index * BOOT_STAGGER_S,
    in LOOPS order, the poller at 0; _record_boot is gathered beside
    them undelayed and main() itself sleeps nowhere
  * boot_delay=0.0 -- and the old two-argument call -- start the
    factory without any sleep at all
"""

import asyncio
import inspect
import sys
import types

import pytest

# Importing workers.all pulls every worker's third-party deps into the
# test process; the push dependency is optional in this environment
# (the same stub test_retention and test_edge_decomposition use).
sys.modules.setdefault("pywebpush", types.SimpleNamespace(
    webpush=None, WebPushException=Exception))

from sportsassets.workers import all as all_mod  # noqa: E402

_REAL_SLEEP = asyncio.sleep


class _Stop(BaseException):
    """Ends supervise()'s forever loop: not an Exception, so the
    `except Exception` restart path does not swallow it."""


def _record_sleeps(monkeypatch, timeline: list) -> None:
    """all.py calls asyncio.sleep through the module, so the module
    attribute is what it sees. Each call is recorded in the same
    timeline as the factory's starts, so ORDER is assertable, and
    yields once through the real sleep so nothing ever waits."""
    async def _sleep(seconds, *args, **kwargs):
        timeline.append(("sleep", seconds))
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)


# -------------------------------------------------------------- supervise

def test_the_boot_delay_is_slept_once_before_the_first_start_and_never_on_a_restart(
        monkeypatch):
    """Both restart paths -- a crash (the outage's shape) and a clean
    exit -- wait RESTART_DELAY_SECONDS and nothing else."""
    timeline: list = []
    _record_sleeps(monkeypatch, timeline)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        timeline.append(("start", calls["n"]))
        if calls["n"] == 1:
            raise RuntimeError("opening read failed")     # the crash path
        if calls["n"] == 2:
            return                                        # the clean-exit path
        raise _Stop()

    with pytest.raises(_Stop):
        asyncio.run(all_mod.supervise("fake", factory, boot_delay=1.5))

    assert calls["n"] == 3
    assert all_mod.RESTART_DELAY_SECONDS == 5, "the restart wait is not this change's"
    # the boot delay is the first thing that happens, before the factory
    assert timeline[0] == ("sleep", 1.5)
    assert timeline[1] == ("start", 1)
    assert timeline == [("sleep", 1.5), ("start", 1),
                        ("sleep", all_mod.RESTART_DELAY_SECONDS), ("start", 2),
                        ("sleep", all_mod.RESTART_DELAY_SECONDS), ("start", 3)]
    sleeps = [s for kind, s in timeline if kind == "sleep"]
    assert sleeps.count(1.5) == 1, "the boot delay was re-applied on a restart"


@pytest.mark.parametrize("boot_delay", [all_mod.BOOT_STAGGER_S, 0.01, 1.5],
                         ids=["one stagger step", "a hundredth", "1.5s"])
def test_every_positive_boot_delay_is_slept_through_the_real_guard(
        monkeypatch, boot_delay):
    """The guard is `boot_delay > 0`, and the smallest delay production
    hands it is ONE stagger step (index 1). Pinning only 0.0 and 1.5
    let `> 1`, `>= 1` and `> 0.5` all pass (mutation review,
    2026-09-05): the second loop in LOOPS would then have started in
    the same instant as the first, which is the stampede this exists
    to break."""
    timeline: list = []
    _record_sleeps(monkeypatch, timeline)

    async def factory():
        timeline.append(("start", 1))
        raise _Stop()

    with pytest.raises(_Stop):
        asyncio.run(all_mod.supervise("fake", factory, boot_delay=boot_delay))
    assert timeline == [("sleep", boot_delay), ("start", 1)]


@pytest.mark.parametrize("kwargs", [{"boot_delay": 0.0}, {}],
                         ids=["boot_delay=0.0", "two-argument call"])
def test_a_zero_boot_delay_starts_the_factory_without_any_sleep(monkeypatch, kwargs):
    timeline: list = []
    _record_sleeps(monkeypatch, timeline)

    async def factory():
        timeline.append(("start", 1))
        raise _Stop()

    with pytest.raises(_Stop):
        asyncio.run(all_mod.supervise("fake", factory, **kwargs))
    assert timeline == [("start", 1)]


def test_boot_delay_is_keyword_only():
    """A positional third argument would let a future LOOPS entry shape
    slip a delay in by accident; the name is the contract."""
    param = inspect.signature(all_mod.supervise).parameters["boot_delay"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default == 0.0


# ------------------------------------------------------------------- main

def _fake_main(monkeypatch, loops):
    """main() with LOOPS replaced, supervise a recorder and the boot
    marker a counter: what main() HANDS each loop, in the order it
    hands it, is the assertion. The recorder's default is a sentinel,
    so a main() that stopped passing boot_delay records 'not passed',
    not a coincidental 0.0."""
    handed: list = []
    booted = {"n": 0}

    async def _supervise(name, factory, *, boot_delay="not passed"):
        handed.append((name, factory, boot_delay))

    async def _record_boot():
        booted["n"] += 1

    monkeypatch.setattr(all_mod, "LOOPS", loops)
    monkeypatch.setattr(all_mod, "supervise", _supervise)
    monkeypatch.setattr(all_mod, "_record_boot", _record_boot)
    return handed, booted


def test_main_hands_each_loop_its_index_times_the_stagger_in_loops_order(monkeypatch):
    timeline: list = []
    _record_sleeps(monkeypatch, timeline)

    async def first():
        pass

    async def second():
        pass

    async def third():
        pass

    handed, booted = _fake_main(
        monkeypatch, [("first", first), ("second", second), ("third", third)])

    asyncio.run(all_mod.main())

    assert all_mod.BOOT_STAGGER_S == 0.75
    assert handed == [("first", first, 0.0), ("second", second, 0.75), ("third", third, 1.5)]
    assert booted["n"] == 1
    assert timeline == [], "main() itself sleeps nowhere; the boot marker is not delayed"


def test_the_real_list_starts_the_poller_at_once_and_spreads_the_rest_by_index(monkeypatch):
    """The real LOOPS, in its real order, through the faked supervise:
    the poller is index 0 and starts at once; every later loop waits
    exactly one stagger more than the one before it."""
    real = list(all_mod.LOOPS)
    handed, _booted = _fake_main(monkeypatch, real)

    asyncio.run(all_mod.main())

    assert [(n, f) for n, f, _d in handed] == real, "LOOPS order is the start order"
    delays = [d for _n, _f, d in handed]
    assert handed[0][0] == "poller" and delays[0] == 0.0
    assert delays == [i * all_mod.BOOT_STAGGER_S for i in range(len(real))]
    assert all(b - a == all_mod.BOOT_STAGGER_S for a, b in zip(delays, delays[1:]))
