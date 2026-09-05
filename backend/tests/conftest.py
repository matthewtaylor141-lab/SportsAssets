"""Test-wide default: the emergency halt is OFF inside the suite.

The 2026-08-25 halt fails closed in production — `copy_halted()` is
True unless LIVE_COPY_HALT=off — and it sits at the common gate that
every copy crosses. That is correct for the money path and useless for
the suite: it short-circuits maybe_execute before any downstream gate
runs, so 42 tests of the cell gate, verified-only gate, first-fill
gate, quarantine and sizing would pass while testing nothing.

So the suite lifts the halt and `test_copy_halt.py` asserts the
PRODUCTION default separately, with the env cleared. Lifting it here
cannot hide a regression in the halt itself.
"""

import pytest


@pytest.fixture(autouse=True)
def _lift_emergency_halt(monkeypatch):
    monkeypatch.setenv("LIVE_COPY_HALT", "off")


@pytest.fixture(autouse=True)
def _edge_gate_seeded(monkeypatch):
    """The 95% gate has a proven verdict for the fixture whales.

    Same stance as the halt above. The gate fails CLOSED when it cannot
    read a published benchmark, which is right for money and useless
    here: no test publishes `whale_edge_benchmark`, so every one of the
    39 tests downstream of it would refuse at the gate and pass while
    proving nothing about the sizing, mapping and first-fill logic they
    exist to check.

    Seeding the module cache is exactly the state a SUCCESSFUL read
    produces — not a bypass, and there is no bypass to reach for: the
    gate reads no environment variable by design.

    test_edge_gate.py pins the production behaviour — every fail-closed
    path, the interval rule, and the placement below the exit path —
    against these same functions with the cache unseeded.
    """
    import time

    from sportsassets import edge_gate

    monkeypatch.setitem(
        edge_gate._cache, "per_whale",
        {"rn1": {"edge_ci95": [0.0028, 0.046], "edge_roi": 0.0244},
         "swisstony": {"edge_ci95": [0.005, 0.04], "edge_roi": 0.02},
         "homerunhazard": {"edge_ci95": [0.004, 0.05], "edge_roi": 0.026},
         "kch123": {"edge_ci95": [0.004, 0.05], "edge_roi": 0.026},
         "ferrarichampions2026": {"edge_ci95": [0.003, 0.05],
                                  "edge_roi": 0.02},
         "0x076daa87": {"edge_ci95": [0.003, 0.05], "edge_roi": 0.02},
         "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465":
             {"edge_ci95": [0.003, 0.05], "edge_roi": 0.02}})
    import datetime as _dt
    monkeypatch.setitem(edge_gate._cache, "measured_at",
                        _dt.datetime.now(_dt.timezone.utc).isoformat())
    monkeypatch.setitem(edge_gate._cache, "read_at", time.monotonic())
    monkeypatch.setitem(edge_gate._cache, "err", None)
    # refresh() would re-read the (absent) row and clear nothing; make
    # it a no-op so the seeded cache survives the money path calling it.
    async def _noop(_pool):
        return None
    monkeypatch.setattr(edge_gate, "refresh", _noop)


@pytest.fixture(autouse=True)
def _permissive_ask(monkeypatch):
    """The pre-trade ask check needs a readable book.

    It fails CLOSED on an unreadable ask, which is right for money and
    wrong for the suite: the stub venues here expose no order book, so
    every test of an upstream gate would refuse at the ask check and
    pass while proving nothing. Answer with an ask far below any limit
    under test, so the check is satisfied and the gate under test is
    what actually decides.

    test_ask_guard.py exercises the real check against real numbers.
    """
    from sportsassets import pmus

    monkeypatch.setattr(pmus, "side_ask", lambda slug, intent: 0.01)
    # The side-price-band check compares that ask to the WHALE's price,
    # and the fixture whales pay ~0.55 — so a fixed 0.01 stub would trip
    # the band on every test. Widen the band past 1.0 for the suite;
    # test_side_band.py pins the real width and the real refusals.
    monkeypatch.setenv("LIVE_SIDE_PRICE_BAND", "2.0")


@pytest.fixture(autouse=True)
def _clear_overspend_breaker(monkeypatch):
    """Same problem, different switch.

    The overspend breaker reads ingestion_state through the pool, and
    the stub pools in this suite answer EVERY fetchval with a truthy
    value — so the breaker reads "tripped" and short-circuits
    maybe_execute before any gate under test runs. Neutralized here;
    test_overspend_breaker.py exercises the real function directly.
    """
    from sportsassets import live_executor as le

    async def _clear(_pool):
        return None

    monkeypatch.setattr(le, "overspend_halt", _clear)


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Module-level caches must not leak between tests.

    _SIBLING_CACHE lives on live_executor and is keyed by token with a
    300s TTL, so one test populating it makes the NEXT test's stub pool
    unreachable -- _sibling_from_positions answers from the cache and
    never calls it. test_sibling_fallback passed in isolation and failed
    in the suite, and only surfaced because an unrelated new test file
    changed collection order.

    A test that passes only in one ordering is proving nothing, and the
    ordering it needs is invisible. Cleared before and after, so a test
    inherits nothing and leaves nothing.
    """
    from sportsassets import live_executor as _le

    _le._SIBLING_CACHE.clear()
    _le._SIBLING_CACHE_AT = None
    yield
    _le._SIBLING_CACHE.clear()
    _le._SIBLING_CACHE_AT = None


@pytest.fixture(autouse=True)
def _clear_kalshi_board_cache():
    """The Kalshi board cache (2026-09-05) is module state on the API
    app: 20 s TTL, keyed by series set, holding an asyncio.Lock and
    the background refresh task per key. Two tests that stub
    _kalshi_sweep for the same league inside one 20 s window would
    read each other's board, and a lock that once waited is bound to
    that test's event loop -- the next loop cannot use it. Same stance
    as the cache above: cleared before and after. Only when the app
    module is already imported; importing it here would pull FastAPI
    into every test that never touches it. A test that makes a board
    stale awaits the refresh task it started before it returns."""
    import sys

    def _clear() -> None:
        mod = sys.modules.get("sportsassets.api.app")
        cache = getattr(mod, "_kalshi_board_cache", None)
        if cache is not None:
            cache.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _override_state_does_not_leak():
    """The roster/clip overrides are module state, and since 2026-09-05
    a read that FAILS adopts a CLOSED value (UNREADABLE) instead of
    keeping the last one -- including a pool that cannot be obtained.
    A test that drives a money path against an unreachable pool
    (test_live_executor_mapping's staleness test) therefore left the
    gate closed for every test after it: eleven sizing tests read a
    0.0 clip and failed only in the suite, passing alone. Same stance
    as the cache above: snapshot, then restore, so a test inherits
    nothing and leaves nothing. The files that pin the closed state
    (test_gate_fails_closed, test_verified_set, test_clips_follow_the_
    rules) reset it themselves as well."""
    from sportsassets import live_executor as _le

    names = ("_roster_override", "_clip_override", "_roster_read_at",
             "_closed_read_at", "_closed_since", "_closed_error")
    saved = {n: getattr(_le, n) for n in names}
    yield
    for n, v in saved.items():
        setattr(_le, n, v)
