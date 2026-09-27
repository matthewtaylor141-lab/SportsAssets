"""AN AUTHORIZED EXECUTION GATE FOR TESTS, WITHOUT WEAKENING THE GATE.

Owner directive, "COMPLETE THE AUTONOMOUS TRADING SYSTEM" §9: "Classify
the 107 failures you identified in the capital-relevant ring by their
actual effect. 'Environment valid' and 'safe application' are different
conclusions."

────────────────────────────────────────────────────────────────────
WHAT THE 56 GATE DENIALS ACTUALLY WERE, MEASURED RATHER THAN DESCRIBED.

The standing gate baseline of 152 failures includes 56 raising

    execution_gate.Denied: authorization_unavailable: execution gate is
    not bound to a loop and pool, so the kill switch cannot be read
    from this process

Parsed out of the run's JUnit XML, those 56 are not spread across the
capital path. They are **55 in tests/test_pmus_post_only.py and 1 in
tests/test_mirror_live_worker.py** -- essentially one module.

That matters because of what that module is for. Its two promises are
that `submit_fok`'s params dict is byte-identical to what every existing
caller sends, and that only under `post_only=True` is a 4xx read as the
venue's refusal. The gate denies BEFORE either can be inspected, so all
55 currently prove that an unbound gate refuses -- which was never in
doubt -- and prove nothing about the params dict or the refusal
semantics they were written to pin.

    A GATE THAT REFUSES BECAUSE IT IS UNBOUND IS A COVERAGE GAP.
    IT IS NOT A SAFETY RESULT.

────────────────────────────────────────────────────────────────────
WHY THIS BINDS A REAL LOOP INSTEAD OF PATCHING THE GATE.

The tempting fix is to monkeypatch `execution_gate.authorize` to a
no-op. That would make the 55 pass and would delete the gate from the
very path the tests exist to cover -- the submission path -- so a future
regression that bypassed authorization would pass too.

This harness instead does what production does. `_authorize_read`
requires a real event loop running on ANOTHER thread, because it calls
`asyncio.run_coroutine_threadsafe(read_state(pool), loop)` and blocks on
the result; production reaches it through `asyncio.to_thread(submit_fok,
...)`. So the harness starts a real loop in a background thread, binds
it with a pool double, and lets `read_state` and `_decide` run
COMPLETELY UNMODIFIED. Every control the gate consults is exercised:
the kill switch is parsed by `_parse_switch` from real JSON text, the
copy halt, overspend halt and loss stop are all read, and `_decide`
makes the real decision.

That is the difference between a test that skips the gate and a test
that satisfies it. These tests now exercise the gate's decision path.

────────────────────────────────────────────────────────────────────
THREE THINGS THAT KEEP THIS FROM BECOMING A PRODUCTION HOLE.

1. IT LIVES IN `backend/tests/`, NOT IN THE PACKAGE. `sportsassets`
   cannot import it, so no shipped module can reach it. A test asserts
   this rather than trusting the directory layout.

2. IT NEVER TOUCHES `live_trading_enabled` OR ANY CREDENTIAL. The one
   substitution on the venue side is `live_executor.active_venue`,
   patched to name a venue. Flipping the real config flag would leave a
   process whose settings say trading is armed; naming the venue leaves
   the config untouched and the substitution visible in one line.

3. IT ALWAYS UNBINDS. `unbind()` runs in a `finally`, the loop is
   stopped and closed, and a companion test asserts the gate denies
   again afterwards -- so an escaped binding fails the suite instead of
   silently authorizing a later test.

WHAT THIS DOES NOT ESTABLISH. That the submission path is safe in
production. It establishes that these tests now reach the code they were
written to cover, with the gate's own logic deciding. Funded submission
remains disabled by the four code constants, the process-bound gate, the
authorization row and the eligible-account check -- none of which this
harness touches.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading

from sportsassets import execution_gate as eg
from sportsassets import live_executor as le


class PoolDouble:
    """The two `ingestion_state` reads `read_state` makes, and nothing else.

    Deliberately not a general fake: it answers the exact two queries
    the gate issues and raises on anything else, so a new read added to
    `read_state` fails loudly here instead of silently returning None
    and being treated as an absent row.
    """

    def __init__(self, *, paused: bool = False, loss_stop: bool = False):
        # Stored as the JSON TEXT a real row holds, so `_parse_switch`
        # does its real parsing rather than being handed a bool.
        self.pause_value = "true" if paused else "false"
        self.loss_stop = loss_stop
        self.queries: list[str] = []

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        if "WHERE key=$1" in sql:
            if args and args[0] == eg.PAUSE_KEY:
                return self.pause_value
            raise AssertionError(
                "PoolDouble was asked for ingestion_state key %r, which it "
                "does not model" % (args[0] if args else None,))
        if "mirror_loss_stop" in sql:
            return "tripped" if self.loss_stop else None
        raise AssertionError(
            "PoolDouble was issued a query it does not model, so the gate "
            "reads something this harness is not exercising:\n  %s" % sql)


@contextlib.contextmanager
def authorized_gate(monkeypatch, *, paused: bool = False,
                    loss_stop: bool = False, copy_halted: bool = False,
                    overspend: bool = False,
                    venue: str | None = "polymarket-us"):
    """Bind the gate to a real background loop and a pool double.

    Yields the `PoolDouble` so a test can assert the gate actually read
    it -- a harness that authorized without reading would mean the gate
    had stopped consulting its controls.

    Every keyword drives the REAL control it names, so the same harness
    is used to prove the gate still DENIES: `paused=True` must produce
    `live_trading_paused`, not a pass.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True,
                              name="gate-harness-loop")
    thread.start()

    pool = PoolDouble(paused=paused, loss_stop=loss_stop)

    # The venue read is substituted rather than the config flipped: see
    # point 2 of the module docstring.
    monkeypatch.setattr(le, "active_venue", lambda: venue)
    monkeypatch.setattr(le, "copy_halted", lambda: copy_halted)

    async def _overspend(_pool):
        return overspend

    monkeypatch.setattr(le, "overspend_halt", _overspend)

    eg.bind(loop, pool)
    try:
        yield pool
    finally:
        eg.unbind()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        if not thread.is_alive():
            loop.close()
