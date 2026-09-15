"""ROSTER_AUTO=off parks the loop; it does not return.

The first hour the flag was off (2026-09-05, the mirror switch): main()
logged its one line and returned, workers/all.py's supervise() treated
the clean exit as a crash-shaped event and restarted it five seconds
later, and the workers log carried three lines every five seconds --
"starting loop: roster_auto", "roster_auto disabled ...", "loop
roster_auto exited cleanly; restarting in 5s" -- for as long as the
roster was manual. Read back from the service at 21:06Z.

Pinned against the REAL main() with the module flag forced off: it logs
the disabled line exactly once, never opens the database, and has not
returned after a generous wait. The enabled path is not this file's
subject and is left to the roster tests.
"""

import asyncio
import logging
import sys
import types

import pytest

sys.modules.setdefault("pywebpush", types.SimpleNamespace(
    webpush=None, WebPushException=Exception))

from sportsassets.workers import roster_auto  # noqa: E402

DISABLED_LINE = "roster_auto disabled (ROSTER_AUTO=off); the roster is manual"


def test_the_disabled_loop_logs_once_and_parks(monkeypatch, caplog):
    monkeypatch.setattr(roster_auto, "ROSTER_AUTO", False)

    async def _no_pool():
        raise AssertionError("a disabled roster_auto must never open the database")

    monkeypatch.setattr(roster_auto, "get_pool", _no_pool)
    caplog.set_level(logging.INFO, logger="sportsassets.workers.roster_auto")

    async def run():
        # main() must still be waiting after a wait far longer than any
        # scheduling jitter; a return would resolve wait_for at once.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(roster_auto.main(), timeout=0.5)

    asyncio.run(run())

    lines = [r.getMessage() for r in caplog.records
             if r.name == "sportsassets.workers.roster_auto"]
    assert lines == [DISABLED_LINE]


def test_the_park_is_an_event_nobody_sets():
    """The park must be a wait on something that cannot fire, not a long
    sleep that would eventually return and restart the churn. The
    0.5 s wait above cannot tell those apart, so the source is pinned
    the way test_arena_cap pins the import-time cap."""
    import inspect

    src = inspect.getsource(roster_auto.main)
    park = src.index("await asyncio.Event().wait()")
    guard = src.index("if not ROSTER_AUTO:")
    enabled = src.index("from .. import edge_gate")
    assert guard < park < enabled, "the park belongs inside the disabled branch"
    assert "asyncio.sleep" not in src[guard:enabled], "a sleep would return and restart the churn"
