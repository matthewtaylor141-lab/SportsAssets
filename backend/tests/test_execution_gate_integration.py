"""The gate's REAL initialization and refresh path, fixture off.

WHY THIS FILE IS SEPARATE FROM test_execution_gate.py.

That file installs a snapshot per test through
`execution_gate._install_snapshot_for_tests`, and that helper does more
than supply a value: it REPLACES the module global `_current`. Under it,
`bind()`, `read_state()` and the live read are never called. So a suite
armed that way proves the *decision* logic and proves nothing about
whether production ever binds the gate, whether the read works, or
whether a stale answer can be reused.

An AST census has the same hole from the other side: it proves every
order path calls `authorize()`, not that `authorize()` receives fresh,
correct state when the process is real.

Nothing here touches `_install_snapshot_for_tests`. Every test drives
the genuine article:

    gate.bind(loop, pool) -> gate.authorize(...) -> gate.read_state(pool)

with a fake pool and a real running event loop, and `submit_fok` called
from a worker thread exactly as production calls it (via
`asyncio.to_thread`). The conftest allowlist does not include this
module, so the gate starts in its production default: unbound.

NO PRODUCTION FLAG IS TOUCHED AND NO VENUE IS REACHED. The pool is a
stub, the venue client is a recorder, and the recorder counts every
call so a leaked submission is a number, not an opinion.
"""

import asyncio
import os
import time

import pytest

from sportsassets import execution_gate as gate

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ── the doubles ──────────────────────────────────────────────────────

class FakePool:
    """Answers the two reads read_state makes, and can fail on demand."""

    def __init__(self, paused="true", loss_stop=None, raises=None,
                 hang_s=0.0):
        self.paused = paused
        self.loss_stop = loss_stop
        self.raises = raises
        self.hang_s = hang_s
        self.reads = 0

    async def fetchval(self, sql, *args):
        self.reads += 1
        if self.hang_s:
            await asyncio.sleep(self.hang_s)
        if self.raises:
            raise self.raises
        if "mirror_loss_stop" in sql:
            return self.loss_stop
        return self.paused


class RecordingVenue:
    def __init__(self):
        self.creates = []
        self.closes = []
        self.previews = []

    @property
    def orders(self):
        return self

    def create(self, params=None):
        self.creates.append(params)
        return {"id": "ord-1", "executions": []}

    def preview(self, params=None):
        self.previews.append(params)
        o = (params or {}).get("request") or {}
        qty = float(o.get("quantity") or 0)
        p = o.get("price")
        px = float((p or {}).get("value") if isinstance(p, dict) else (p or 0))
        return {"order": {"cashOrderQty": {"value": qty * px},
                          "price": {"value": px}, "quantity": qty}}

    def close_position(self, params=None):
        self.closes.append(params)
        return {"id": "ord-2", "executions": []}

    @property
    def submissions(self):
        return len(self.creates) + len(self.closes)


@pytest.fixture
def venue(monkeypatch):
    from sportsassets import pmus
    v = RecordingVenue()
    monkeypatch.setattr(pmus, "_get_client", lambda: v)
    return v


@pytest.fixture(autouse=True)
def _really_unbound():
    """Belt and braces: this module is not on the conftest allowlist,
    but assert the starting state rather than trust it."""
    gate.unbind()
    gate._restore_for_tests()
    yield
    gate.unbind()
    gate._restore_for_tests()


@pytest.fixture
def venue_settings(monkeypatch):
    """active_venue() reads settings(); give it an armed configuration
    so the ONLY thing under test is the gate's own state handling."""
    import types

    from sportsassets import live_executor as le
    monkeypatch.setattr(le, "settings", lambda: types.SimpleNamespace(
        live_trading_enabled=True, pmus_key_id="k", pmus_secret_key="s",
        pm_private_key=None, copy_probe_enabled=True))
    monkeypatch.setattr(le, "copy_halted", lambda: False)

    async def _no(_pool):
        return False
    monkeypatch.setattr(le, "overspend_halt", _no)


# ── 1. cold start ────────────────────────────────────────────────────

def test_the_fixture_is_off_in_this_module():
    """If the allowlist ever grows to include this file, every test
    below silently stops testing what it claims to."""
    d = gate.describe()
    assert d["bound"] is False
    assert d["readable"] is False
    assert gate._current is gate._current_real, (
        "_current has been replaced -- the live read path is not being "
        "exercised and these tests prove nothing about it")


def test_cold_start_denies(venue):
    """A process that has just booted and not yet bound is a process
    that cannot read the kill switch."""
    from sportsassets import pmus
    with pytest.raises(gate.Denied) as e:
        pmus.submit_fok("mkt", 0.5, 10, intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "authorization_unavailable"
    assert venue.submissions == 0


def test_cold_start_denies_close_too(venue):
    from sportsassets import pmus
    with pytest.raises(gate.Denied):
        pmus.close_position("mkt", slippage_bips=300)
    assert venue.submissions == 0


# ── 2. the real read, through a real loop ────────────────────────────

def _submit_from_worker_thread(fn, *a, **k):
    """Exactly how production calls it: the event loop stays free and
    the submission happens on a worker thread, which is the only
    arrangement in which run_coroutine_threadsafe can work."""
    async def go():
        return await asyncio.to_thread(fn, *a, **k)
    return asyncio.run(go())


def _bind_and_submit(pool, venue_fn, *a, **k):
    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        return await asyncio.to_thread(venue_fn, *a, **k)
    return asyncio.run(go())


def test_an_authorized_bound_gate_permits_the_submission(venue,
                                                         venue_settings):
    """THE POSITIVE CASE, through bind() and read_state(), with no
    snapshot injected anywhere. Without this the file would pass
    against a gate that refuses everything."""
    from sportsassets import pmus
    pool = FakePool(paused="false")
    _bind_and_submit(pool, pmus.submit_fok, "mkt", 0.5, 10,
                     intent="ORDER_INTENT_BUY_LONG")
    assert len(venue.creates) == 1
    assert pool.reads >= 1, "the gate did not actually read the switch"


def test_the_switch_is_read_at_every_submission_not_once(venue,
                                                         venue_settings):
    """Authorization is not cached across calls. Two submissions, two
    reads of the kill switch."""
    from sportsassets import pmus
    pool = FakePool(paused="false")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        await asyncio.to_thread(pmus.submit_fok, "mkt", 0.5, 10,
                                intent="ORDER_INTENT_BUY_LONG")
        first = pool.reads
        await asyncio.to_thread(pmus.submit_fok, "mkt", 0.5, 10,
                                intent="ORDER_INTENT_BUY_LONG")
        return first, pool.reads

    first, second = asyncio.run(go())
    assert second > first, "the second submission reused the first answer"


# ── 3. every bad state denies, through the real read ─────────────────

@pytest.mark.parametrize("label,pool,reason", [
    ("paused true", FakePool(paused="true"), "live_trading_paused"),
    ("row absent", FakePool(paused=None), "authorization_unavailable"),
    ("malformed", FakePool(paused="{not json"), "authorization_unavailable"),
    ("read raises", FakePool(raises=RuntimeError("connection reset")),
     "authorization_unavailable"),
])
def test_bad_control_state_denies_through_the_real_path(
        venue, venue_settings, label, pool, reason):
    from sportsassets import pmus
    with pytest.raises(gate.Denied) as e:
        _bind_and_submit(pool, pmus.submit_fok, "mkt", 0.5, 10,
                         intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == reason, label
    assert venue.submissions == 0, label


def test_a_read_that_never_returns_denies(venue, venue_settings):
    """A gate that hangs is a gate somebody removes. The read is
    bounded, and the bound denies rather than waiting."""
    from sportsassets import pmus
    pool = FakePool(paused="false", hang_s=gate.READ_TIMEOUT_S + 1.5)
    t0 = time.time()
    with pytest.raises(gate.Denied) as e:
        _bind_and_submit(pool, pmus.submit_fok, "mkt", 0.5, 10,
                         intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "authorization_unavailable"
    assert time.time() - t0 < gate.READ_TIMEOUT_S + 3
    assert venue.submissions == 0


def test_no_active_venue_denies_through_the_real_path(venue, monkeypatch):
    import types

    from sportsassets import live_executor as le
    from sportsassets import pmus
    monkeypatch.setattr(le, "settings", lambda: types.SimpleNamespace(
        live_trading_enabled=False, pmus_key_id="k", pmus_secret_key="s",
        pm_private_key=None, copy_probe_enabled=True))
    with pytest.raises(gate.Denied) as e:
        _bind_and_submit(FakePool(paused="false"), pmus.submit_fok,
                         "mkt", 0.5, 10, intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "no_active_venue"
    assert venue.submissions == 0


# ── 4. a pause after the fact blocks queued and retried work ─────────

def test_a_pause_between_queueing_and_submitting_blocks_it(venue,
                                                           venue_settings):
    """The realistic sequence: a route authorizes, the work is queued,
    the operator pauses, the queued work runs. Through the real read,
    with no snapshot injected."""
    from sportsassets import pmus
    pool = FakePool(paused="false")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        # The early check, made the way production makes it: on a
        # worker thread. It succeeds -- nothing is paused yet.
        await asyncio.to_thread(gate.authorize, "submit", lane="copy")
        pool.paused = "true"                       # operator pauses
        return await asyncio.to_thread(
            pmus.submit_fok, "mkt", 0.5, 10,
            intent="ORDER_INTENT_BUY_LONG")

    with pytest.raises(gate.Denied) as e:
        asyncio.run(go())
    assert e.value.reason == "live_trading_paused"
    assert venue.submissions == 0, (
        "the queued work submitted on an authorization taken before "
        "the pause")


def test_a_retry_after_a_halt_is_blocked(venue, venue_settings,
                                         monkeypatch):
    """Not a pause but a copy halt, arriving between the first attempt
    and the retry."""
    from sportsassets import live_executor as le
    from sportsassets import pmus
    pool = FakePool(paused="false")
    state = {"halted": False}
    monkeypatch.setattr(le, "copy_halted", lambda: state["halted"])

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        await asyncio.to_thread(pmus.submit_fok, "mkt", 0.5, 10,
                                intent="ORDER_INTENT_BUY_LONG")
        state["halted"] = True                     # halt lands
        return await asyncio.to_thread(            # the retry
            pmus.submit_fok, "mkt", 0.5, 10,
            intent="ORDER_INTENT_BUY_LONG")

    with pytest.raises(gate.Denied) as e:
        asyncio.run(go())
    assert e.value.reason == "copy_halted"
    assert len(venue.creates) == 1, "only the first attempt should land"


def test_a_stale_snapshot_is_not_reused_past_its_bound(venue,
                                                       venue_settings):
    """When the live read fails, a very recent answer may stand in and
    nothing older may. Bind, succeed once, then break the pool and age
    the snapshot past MAX_STALE_S."""
    from sportsassets import pmus
    pool = FakePool(paused="false")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        await asyncio.to_thread(pmus.submit_fok, "mkt", 0.5, 10,
                                intent="ORDER_INTENT_BUY_LONG")
        pool.raises = RuntimeError("db gone")
        with gate._B.lock:
            gate._B.snapshot.read_at -= (gate.MAX_STALE_S + 60)
        return await asyncio.to_thread(
            pmus.submit_fok, "mkt", 0.5, 10,
            intent="ORDER_INTENT_BUY_LONG")

    with pytest.raises(gate.Denied) as e:
        asyncio.run(go())
    assert e.value.reason == "authorization_unavailable"
    assert len(venue.creates) == 1


# ── 5. production really binds it ────────────────────────────────────

def test_the_workers_service_binds_the_gate_before_any_loop_runs():
    """workers/all.py registers whale_exits, the mirror reconciler and
    underdog -- all order-capable. _bind_execution_gate is AWAITED
    before the loops start, so no tick can run against an unbound gate
    and produce a refusal indistinguishable from a pause.

    READ AS TEXT, NOT IMPORTED. workers/all.py pulls in the notification
    stack, which needs pywebpush -- absent in this environment. A
    control that cannot be checked because an unrelated dependency is
    missing is a control nobody checks, so the ordering is asserted
    against the source file itself."""
    src = open(os.path.join(BACKEND, "sportsassets", "workers",
                            "all.py")).read()
    assert "await _bind_execution_gate()" in src
    assert src.index("await _bind_execution_gate()") < src.index(
        "asyncio.gather")
    assert "gate.bind_current_loop()" in src


def test_the_binding_helper_actually_binds(monkeypatch):
    """Not that the call exists -- that RUNNING it leaves the gate
    bound. This is the initialization the armed fixture concealed, and
    the reason the helper lives in execution_gate rather than inside a
    service module that will not import here."""
    pool = FakePool(paused="false")

    async def _pool():
        return pool
    monkeypatch.setattr("sportsassets.db.get_pool", _pool)

    async def go():
        ok = await gate.bind_current_loop()
        return ok, gate.describe()["bound"]

    ok, bound = asyncio.run(go())
    assert ok is True and bound is True


def test_a_binding_failure_leaves_the_gate_denying(monkeypatch):
    """If get_pool raises at boot the service still starts -- but it
    starts refusing orders, not permitting them."""
    async def _boom():
        raise RuntimeError("no database")
    monkeypatch.setattr("sportsassets.db.get_pool", _boom)

    async def go():
        ok = await gate.bind_current_loop()        # must not raise
        return ok, gate.describe()

    ok, d = asyncio.run(go())
    assert ok is False
    assert d["bound"] is False and d["readable"] is False


def test_the_api_service_binds_the_gate_in_its_lifespan():
    """The API hosts the manual desk -- _execute_manual,
    _execute_manual_limit, _execute_manual_sell -- and the manual sell
    is the route that consulted active_venue() and nothing else."""
    src = open(os.path.join(BACKEND, "sportsassets", "api", "app.py")).read()
    i = src.index("async def lifespan(")
    body = src[i:i + 4000]
    assert "bind_current_loop()" in body
    assert "execution gate NOT bound" in body


def test_every_order_capable_entry_point_is_covered_by_a_binding():
    """The two services that host order routes both bind. If a third
    ever hosts one, this list is where the omission shows. Read as
    text for the same reason as above."""
    for rel in ("sportsassets/workers/all.py", "sportsassets/api/app.py"):
        src = open(os.path.join(BACKEND, rel)).read()
        assert "bind_current_loop()" in src, rel


# ── 6. lane identity cannot be forged into extra permission ──────────

def test_an_undeclared_lane_gets_the_copy_controls(venue, venue_settings,
                                                   monkeypatch):
    """Undeclared is "unknown", and unknown is treated as a copy lane.
    Through the real read, with the loss stop set."""
    from sportsassets import pmus
    pool = FakePool(paused="false", loss_stop="tripped")
    assert gate.current_lane() == "unknown"
    with pytest.raises(gate.Denied) as e:
        _bind_and_submit(pool, pmus.submit_fok, "mkt", 0.5, 10,
                         intent="ORDER_INTENT_BUY_LONG")
    assert e.value.reason == "mirror_loss_stop"
    assert venue.submissions == 0


@pytest.mark.parametrize("bogus", ["", "MANUAL", "manual ", " manual",
                                   "admin", "root", "manual;copy",
                                   "global", "none", "bypass"])
def test_a_lane_that_is_not_exactly_manual_gets_the_copy_controls(
        venue, venue_settings, bogus):
    """The exemption is an exact-match allowlist of one. A route cannot
    talk its way out of the breakers by naming itself something
    manual-adjacent."""
    from sportsassets import pmus
    pool = FakePool(paused="false", loss_stop="tripped")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        with gate.lane(bogus):
            return await asyncio.to_thread(
                pmus.submit_fok, "mkt", 0.5, 10,
                intent="ORDER_INTENT_BUY_LONG")

    with pytest.raises(gate.Denied) as e:
        asyncio.run(go())
    assert e.value.reason == "mirror_loss_stop", bogus
    assert venue.submissions == 0


def test_the_manual_lane_is_still_bound_by_the_global_controls(
        venue, venue_settings):
    """Exempt from the copy breakers, never from the kill switch."""
    from sportsassets import pmus
    pool = FakePool(paused="true")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        with gate.lane("manual"):
            return await asyncio.to_thread(
                pmus.submit_fok, "mkt", 0.5, 10,
                intent="ORDER_INTENT_BUY_LONG")

    with pytest.raises(gate.Denied) as e:
        asyncio.run(go())
    assert e.value.reason == "live_trading_paused"
    assert venue.submissions == 0


def test_the_lane_does_not_leak_out_of_its_scope(venue, venue_settings):
    """A manual desk call must not leave the process exempt afterwards."""
    from sportsassets import pmus
    pool = FakePool(paused="false", loss_stop="tripped")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        with gate.lane("manual"):
            await asyncio.to_thread(pmus.submit_fok, "mkt", 0.5, 10,
                                    intent="ORDER_INTENT_BUY_LONG")
        # scope closed: the next submission is "unknown" again
        return await asyncio.to_thread(
            pmus.submit_fok, "mkt", 0.5, 10,
            intent="ORDER_INTENT_BUY_LONG")

    with pytest.raises(gate.Denied) as e:
        asyncio.run(go())
    assert e.value.reason == "mirror_loss_stop"
    assert len(venue.creates) == 1


# ── 7. cancellation stays available while everything else is shut ────

def test_cancel_works_while_paused(monkeypatch):
    """The point of the exemption: a paused system must still be able
    to pull its resting orders. Exercised, not inspected."""
    from sportsassets import pmus

    called = {}

    class CancelClient:
        @property
        def orders(self):
            return self

        def cancel(self, order_id, params=None):
            called["order_id"] = order_id
            called["params"] = params
            return {"ok": True}

    monkeypatch.setattr(pmus, "_get_client", lambda: CancelClient())

    async def go():
        gate.bind(asyncio.get_running_loop(), FakePool(paused="true"))
        # the gate is engaged; a submission here would raise
        with pytest.raises(gate.Denied):
            gate.authorize("submit", lane="manual")
        return await asyncio.to_thread(pmus.cancel_order, "ord-1", "mkt")

    asyncio.run(go())
    assert called, "cancel did not reach the venue while paused"


def test_this_file_injects_no_snapshot():
    """The whole point. If a future edit reaches for the injection
    hook, these tests stop exercising initialization."""
    import ast
    src = open(__file__).read()
    called = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            f = node.func
            nm = getattr(f, "attr", None) or getattr(f, "id", None)
            if nm:
                called.add(nm)
    assert "_install_snapshot_for_tests" not in called


# ── 8. the loop-thread rule, stated and pinned ───────────────────────

def test_authorize_on_the_loop_thread_denies_without_a_fresh_snapshot(
        venue_settings):
    """A blocking read is impossible from the loop's own thread:
    run_coroutine_threadsafe would schedule the read on the loop this
    thread is blocking. The gate does not hang on that -- it denies.

    Production never lands here (every order path goes through
    asyncio.to_thread), but a future caller that checks early from
    async code should get a refusal, not a two-and-a-half second stall
    followed by one."""
    pool = FakePool(paused="false")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        t0 = time.time()
        try:
            gate.authorize("submit", lane="copy")
            return None, time.time() - t0
        except gate.Denied as d:
            return d.reason, time.time() - t0

    reason, elapsed = asyncio.run(go())
    assert reason == "authorization_unavailable"
    assert elapsed < 1.0, "it stalled instead of refusing"


def test_the_loop_thread_may_use_a_snapshot_inside_the_bound(
        venue_settings):
    """Having read successfully a moment ago on a worker thread, an
    async caller may use that answer -- and only within MAX_STALE_S."""
    pool = FakePool(paused="false")

    async def go():
        gate.bind(asyncio.get_running_loop(), pool)
        await asyncio.to_thread(gate.authorize, "submit", lane="copy")
        fresh = gate.authorize("submit", lane="copy")   # loop thread
        with gate._B.lock:
            gate._B.snapshot.read_at -= (gate.MAX_STALE_S + 60)
        try:
            gate.authorize("submit", lane="copy")
            return fresh.ok, None
        except gate.Denied as d:
            return fresh.ok, d.reason

    ok, stale_reason = asyncio.run(go())
    assert ok is True
    assert stale_reason == "authorization_unavailable"
