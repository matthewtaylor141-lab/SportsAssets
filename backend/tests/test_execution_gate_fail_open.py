"""The two fail-open paths an independent review reproduced at 195ee01.

BOTH LET AN ORDER THROUGH. Neither was hypothetical:

  1. `_current()` fell back to a cached snapshot up to MAX_STALE_S old
     whenever the authoritative read failed, and returned the cache
     outright when called on the loop thread. Install a recent ALLOWED
     snapshot, make `run_coroutine_threadsafe` raise TimeoutError, call
     `authorize("submit", lane="manual")` -- and it returned
     successfully. The module's contract says unreadable authorization
     denies. The code said a five-second-old yes was good enough.

  2. `_parse_switch()` ended `return bool(parsed)`. Python truthiness is
     not JSON's boolean, so "0", "[]", "{}", "null" and '""' all decoded
     to falsy and therefore to NOT PAUSED. Five kinds of nonsense in the
     kill switch read as permission.

EVERY TEST HERE COUNTS MOCKED VENUE SUBMISSIONS AND ASSERTS ZERO. A
denial that raises the right exception while the adapter has already
sent an order is not a fix, so the assertion is on the adapter, not on
the exception type alone.

THE FIXTURE IS OFF in this module -- it is not in conftest's allowlist.
Under `_install_snapshot_for_tests` the live read path is replaced
wholesale and none of this would be exercised.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from sportsassets import execution_gate as gate


# ── a pool and a venue that record whether they were used ────────────

class FakePool:
    """Returns whatever the test puts in `values`, by key."""

    def __init__(self, values=None, raises=None):
        self.values = values if values is not None else {}
        self.raises = raises
        self.reads = 0

    async def fetchval(self, sql, *args):
        self.reads += 1
        if self.raises is not None:
            raise self.raises
        if args:
            return self.values.get(args[0])
        if "mirror_loss_stop" in sql:
            return self.values.get("mirror_loss_stop")
        return None


class CountingVenue:
    """Stands in for the pmus adapter. Counts orders it was asked to
    send. The number this ends on is the only thing that matters."""

    def __init__(self):
        self.submissions = []

    def submit(self, slug, **kw):
        self.submissions.append((slug, kw))
        return {"ok": True}


@pytest.fixture
def venue():
    return CountingVenue()


@pytest.fixture(autouse=True)
def _unbound_between_tests():
    gate.unbind()
    yield
    gate.unbind()


@pytest.fixture
def allowing_env(monkeypatch):
    """Everything else says yes, so the only thing under test is the
    switch. Without this a denial could come from the venue gate and
    the test would pass for the wrong reason."""
    from sportsassets import live_executor as le

    monkeypatch.setattr(le, "active_venue", lambda: "polymarket-us")
    monkeypatch.setattr(le, "copy_halted", lambda: False)

    async def _no_overspend(pool):
        return False

    monkeypatch.setattr(le, "overspend_halt", _no_overspend)
    return None


def _loop_in_thread():
    """A real event loop on its own thread, as production has."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(5)
    return loop, t


def _stop(loop, t):
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)
    loop.close()


def _submit_through_gate(venue, slug="any-slug", lane="manual"):
    """What a real order route does: authorize, then send. If the gate
    raises, the adapter is never reached -- which is the property under
    test."""
    gate.authorize("submit", lane=lane, slug=slug)
    return venue.submit(slug)


# ── DEFECT 1: a cached yes must not survive a failed read ────────────

class TestFailedReadDoesNotAuthorize:

    def test_the_exact_reproduction_from_the_review(self, venue,
                                                    allowing_env,
                                                    monkeypatch):
        """Recent allowed snapshot + TimeoutError on the read = DENY.

        This is the review's reproduction, run against the repaired
        code. Before the fix it returned successfully.
        """
        loop, t = _loop_in_thread()
        try:
            pool = FakePool({"live_trading_paused": "false"})
            gate.bind(loop, pool)

            # A real, successful, ALLOWED read lands in the cache.
            ok = gate.authorize("submit", lane="manual")
            assert ok.ok and not ok.paused
            assert gate._last_known().ok, "the cache is primed and allowing"

            # Now the authoritative read stops working.
            def _boom(coro, _loop):
                coro.close()                 # no 'never awaited' warning
                raise asyncio.TimeoutError("read unavailable")

            monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _boom)

            with pytest.raises(gate.Denied) as e:
                _submit_through_gate(venue)
            assert e.value.reason == "authorization_unavailable"
            assert "does not authorize a later submission" in e.value.detail
        finally:
            _stop(loop, t)

        assert venue.submissions == [], (
            "an order was sent on the strength of a cached snapshot "
            "after the authorization read failed")

    def test_a_failed_read_immediately_after_an_allowed_read(self, venue,
                                                             allowing_env,
                                                             monkeypatch):
        """Zero seconds of staleness is still not permission.

        The old code's window was MAX_STALE_S. This asserts there is no
        window at all: the allowed read is microseconds old and the
        submission is still refused.
        """
        loop, t = _loop_in_thread()
        try:
            pool = FakePool({"live_trading_paused": "false"})
            gate.bind(loop, pool)
            first = gate.authorize("submit", lane="manual")
            age = time.time() - first.read_at
            assert age < gate.MAX_STALE_S, "the cache is well inside the "\
                                           "old staleness allowance"

            def _boom(coro, _loop):
                coro.close()
                raise RuntimeError("database gone")

            monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _boom)
            with pytest.raises(gate.Denied):
                _submit_through_gate(venue)
        finally:
            _stop(loop, t)
        assert venue.submissions == []

    def test_the_read_raising_inside_read_state_also_denies(self, venue,
                                                            allowing_env):
        """A pool that raises produces ok=False, not a fallback."""
        loop, t = _loop_in_thread()
        try:
            good = FakePool({"live_trading_paused": "false"})
            gate.bind(loop, good)
            gate.authorize("submit", lane="manual")          # prime cache

            gate.bind(loop, FakePool(raises=RuntimeError("connection reset")))
            with pytest.raises(gate.Denied) as e:
                _submit_through_gate(venue)
            assert e.value.reason == "authorization_unavailable"
        finally:
            _stop(loop, t)
        assert venue.submissions == []

    def test_describe_may_be_stale_and_says_so(self, allowing_env):
        """Diagnostics keep the cache. They just cannot authorize.

        The split is the fix: describe() still shows the last reading,
        and labels it.
        """
        loop, t = _loop_in_thread()
        try:
            gate.bind(loop, FakePool({"live_trading_paused": "false"}))
            gate.authorize("submit", lane="manual")
            d = gate.describe()
            assert d["readable"] is True
            assert d["paused"] is False
            assert d["stale"] is False
            assert "never is" in d["note"]
        finally:
            _stop(loop, t)


class TestLoopThreadDoesNotAuthorizeFromCache:

    def test_sync_authorize_on_the_loop_thread_denies(self, venue,
                                                      allowing_env):
        """The second fail-open: on the loop's own thread the old code
        returned the cached snapshot outright."""
        results = {}

        async def main():
            pool = FakePool({"live_trading_paused": "false"})
            gate.bind(asyncio.get_running_loop(), pool)
            # Prime the cache with a real allowed read, from this loop.
            await gate.authorize_async("submit", lane="manual")
            assert gate._last_known().ok

            try:
                _submit_through_gate(venue)         # SYNC, on the loop
                results["outcome"] = "ALLOWED"
            except gate.Denied as d:
                results["outcome"] = "denied"
                results["reason"] = d.reason
                results["detail"] = d.detail

        asyncio.run(main())
        assert results["outcome"] == "denied"
        assert "authorize_async" in results["detail"]
        assert venue.submissions == []

    def test_authorize_async_is_the_supported_path_and_is_fresh(self,
                                                                allowing_env):
        """And it really reads: flip the switch under it and it flips."""
        seen = {}

        async def main():
            pool = FakePool({"live_trading_paused": "false"})
            gate.bind(asyncio.get_running_loop(), pool)
            snap = await gate.authorize_async("submit", lane="manual")
            seen["first"] = snap.ok
            seen["reads_after_first"] = pool.reads

            pool.values["live_trading_paused"] = "true"
            try:
                await gate.authorize_async("submit", lane="manual")
                seen["second"] = "ALLOWED"
            except gate.Denied as d:
                seen["second"] = d.reason

        asyncio.run(main())
        assert seen["first"] is True
        assert seen["reads_after_first"] >= 1, "it did a real read"
        assert seen["second"] == "live_trading_paused", (
            "authorize_async served a stale answer instead of reading")

    def test_authorize_async_denies_when_the_read_fails(self, venue,
                                                        allowing_env):
        outcome = {}

        async def main():
            good = FakePool({"live_trading_paused": "false"})
            gate.bind(asyncio.get_running_loop(), good)
            await gate.authorize_async("submit", lane="manual")

            gate.bind(asyncio.get_running_loop(),
                      FakePool(raises=RuntimeError("gone")))
            try:
                await gate.authorize_async("submit", lane="manual")
                venue.submit("slug")
                outcome["r"] = "ALLOWED"
            except gate.Denied as d:
                outcome["r"] = d.reason

        asyncio.run(main())
        assert outcome["r"] == "authorization_unavailable"
        assert venue.submissions == []


# ── DEFECT 2: the switch must hold an actual JSON boolean ────────────

# Every one of these decoded to a falsy Python value under the old
# `bool(parsed)` and therefore read as NOT PAUSED.
NOT_BOOLEANS = [
    ("0", "zero is a number, not false"),
    ("1", "one is a number, not true"),
    ("[]", "an empty list is not false"),
    ("{}", "an empty object is not false"),
    ("null", "null is not false"),
    ('""', "an empty string is not false"),
    ('"false"', "the WORD false, quoted, is a string"),
    ("0.0", "a float is not a boolean"),
    ("[false]", "a list containing false is not false"),
]


class TestKillSwitchMustBeABoolean:

    @pytest.mark.parametrize("raw,why", NOT_BOOLEANS)
    def test_non_boolean_values_deny(self, raw, why, venue, allowing_env):
        loop, t = _loop_in_thread()
        try:
            gate.bind(loop, FakePool({"live_trading_paused": raw}))
            with pytest.raises(gate.Denied) as e:
                _submit_through_gate(venue)
            assert e.value.reason == "authorization_unavailable", why
        finally:
            _stop(loop, t)
        assert venue.submissions == [], (
            "%r released the kill switch: %s" % (raw, why))

    @pytest.mark.parametrize("raw", ["not json at all", "{", "tru"])
    def test_malformed_values_deny(self, raw, venue, allowing_env):
        loop, t = _loop_in_thread()
        try:
            gate.bind(loop, FakePool({"live_trading_paused": raw}))
            with pytest.raises(gate.Denied):
                _submit_through_gate(venue)
        finally:
            _stop(loop, t)
        assert venue.submissions == []

    def test_an_absent_row_denies(self, venue, allowing_env):
        """Unchanged, and re-asserted here because it is the same family
        of defect: absence is not permission."""
        loop, t = _loop_in_thread()
        try:
            gate.bind(loop, FakePool({}))       # no row
            with pytest.raises(gate.Denied) as e:
                _submit_through_gate(venue)
            assert e.value.reason == "authorization_unavailable"
        finally:
            _stop(loop, t)
        assert venue.submissions == []

    @pytest.mark.parametrize("raw,paused", [("true", True), ("false", False)])
    def test_the_two_real_values_work(self, raw, paused, venue,
                                      allowing_env):
        """THE LEGITIMATE PATH, KEPT. render-ops pause-on/pause-off write
        'true'::jsonb and 'false'::jsonb, so these are what the switch
        actually holds. A gate that denies everything is not fixed."""
        loop, t = _loop_in_thread()
        try:
            gate.bind(loop, FakePool({"live_trading_paused": raw}))
            if paused:
                with pytest.raises(gate.Denied) as e:
                    _submit_through_gate(venue)
                assert e.value.reason == "live_trading_paused"
                assert venue.submissions == []
            else:
                _submit_through_gate(venue)
                assert venue.submissions == [("any-slug", {})], (
                    "an explicit false must still allow trading")
        finally:
            _stop(loop, t)

    def test_a_real_python_bool_from_a_decoding_driver(self, venue,
                                                       allowing_env):
        """asyncpg with a jsonb codec hands back real objects, not text.
        A True/False that never went through json.loads still works."""
        loop, t = _loop_in_thread()
        try:
            gate.bind(loop, FakePool({"live_trading_paused": False}))
            _submit_through_gate(venue)
            assert len(venue.submissions) == 1
        finally:
            _stop(loop, t)

    def test_parse_switch_directly(self):
        """The unit, so the reason is visible without a loop."""
        assert gate._parse_switch("true") is True
        assert gate._parse_switch("false") is False
        assert gate._parse_switch(True) is True
        for raw, _why in NOT_BOOLEANS:
            with pytest.raises(gate.Denied) as e:
                gate._parse_switch(raw)
            assert e.value.reason in ("kill_switch_not_boolean",
                                      "kill_switch_malformed")


# ── the controls that must NOT have been broken by the repair ────────

class TestCancellationStaysOpen:

    def test_cancel_is_not_gated_even_when_everything_denies(self):
        """A kill switch that traps resting orders is worse than none.
        Re-asserted here because the repair touched the read path that
        every other control goes through."""
        import inspect

        from sportsassets import pmus

        src = inspect.getsource(pmus.cancel_order)
        assert "authorize" not in src, (
            "cancel_order acquired a gate call; cancels reduce exposure "
            "and must stay available while paused")

    def test_submit_and_close_are_gated(self):
        import inspect

        from sportsassets import pmus

        for fn in (pmus.submit_fok, pmus.close_position):
            assert "_gate.authorize" in inspect.getsource(fn), fn.__name__
