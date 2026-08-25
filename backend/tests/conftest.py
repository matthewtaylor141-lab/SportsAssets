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
