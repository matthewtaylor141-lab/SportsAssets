"""An entry still in flight PINS the exit; it does not erase it.

Round three (2026-09-01) of the rest-lane review: the in-flight wait in
mirror_exit returned its reason through _exit_stop, whose contract is to
return None. whale_exits reads None as "handled", advances its baseline
and never re-offers the asset -- so a bid that filled after the whale had
already left would be held to resolution against him, while the exit
census showed mx_entry_in_flight ticking up as if the case were handled.
Two source-grep tests pinned the defect, and a mutation that disabled the
wait outright survived the suite.

This file DRIVES mirror_exit and asserts its RETURN VALUE, which is the
only thing whale_exits ever sees.
"""
import asyncio
import types

import pytest

from sportsassets import live_executor as le


class _Row(dict):
    pass


class _Proceeded(BaseException):
    """Raised from the first collaborator past the lookup: proof that
    mirror_exit went on WITH the row instead of refusing. BaseException
    so no `except Exception` on the way out can turn it into a reason."""


class _Pool:
    def __init__(self, settles_after=None, age_s=0.01, in_flight=True,
                 status_raises=False, lookup_raises=False):
        # age_s is the row's age; the `fast` fixture shrinks the window
        # to 50 ms, so a "young" row here is younger than that.
        self.settles_after = settles_after   # polls before 'filled'; None = never
        self.age_s = age_s
        self.in_flight = in_flight
        self.status_raises = status_raises
        self.lookup_raises = lookup_raises
        self.status_reads = 0
        self.position_reads = 0

    def _filled(self):
        return (self.settles_after is not None
                and self.status_reads >= self.settles_after)

    async def fetchrow(self, sql, *a):
        if "age_s" in sql and "'submitting'" in sql:
            if self.lookup_raises:
                raise RuntimeError("db blip")
            return _Row(id=42, age_s=self.age_s) if self.in_flight else None
        if "status = 'filled'" in sql:
            self.position_reads += 1
            if self._filled():
                return _Row(id=7, us_market_slug="aec-atp-a-b-2026-09-01",
                            qty=100.0, orig_qty=100.0, entry=0.5,
                            intent="ORDER_INTENT_BUY_LONG")
            return None
        return None

    async def fetchval(self, sql, *a):
        if "SELECT status FROM live_orders" in sql:
            self.status_reads += 1
            if self.status_raises:
                raise RuntimeError("db blip")
            return "filled" if self._filled() else "submitting"
        if "UPDATE live_orders SET status='exiting'" in sql:
            return 7
        return None

    async def execute(self, *a):
        return None

    async def fetch(self, *a):
        return []


@pytest.fixture
def fast(monkeypatch):
    """Milliseconds instead of seconds; the real asyncio.sleep."""
    monkeypatch.setattr(le, "REST_BID_TTL_S", 0.0)
    monkeypatch.setattr(le, "_INFLIGHT_POLL_S", 0.001)
    monkeypatch.setattr(le, "_INFLIGHT_GRACE_S", 0.05)


def _drive(pool, monkeypatch):
    async def _get_pool():
        return pool

    async def _false(_p):
        return False

    async def _held(_slug):
        raise _Proceeded()

    monkeypatch.setattr(le, "get_pool", _get_pool)
    monkeypatch.setattr(le, "copy_halted", lambda: False)
    monkeypatch.setattr(le, "_whale_set", lambda _n: {"rn1"})
    monkeypatch.setattr(le, "_is_paused", _false)
    monkeypatch.setattr(le, "overspend_halt", _false)
    monkeypatch.setattr(le, "_pm_held", _held)
    monkeypatch.setattr(le, "settings", lambda: types.SimpleNamespace(
        copy_probe_enabled=True))
    payload = {"side": "SELL", "whale_username": "rn1", "asset": "0xasset",
               "closed_frac": 1.0}
    try:
        return asyncio.run(le.mirror_exit(payload))
    except _Proceeded:
        return "proceeded"


def test_a_never_settling_entry_returns_a_pending_reason(monkeypatch, fast):
    """The case that matters: the row is still 'submitting' when the
    window closes. The VALUE whale_exits receives must be pending."""
    pool = _Pool(settles_after=None)
    out = _drive(pool, monkeypatch)
    assert out == "mx_entry_in_flight"
    assert out in le.EXIT_PENDING_REASONS
    assert pool.status_reads >= 1, "the wait must actually poll the row"
    assert pool.position_reads == 2, "and look the position up again"


def test_an_entry_that_fills_inside_the_window_is_found_and_sold(monkeypatch, fast):
    pool = _Pool(settles_after=3)
    out = _drive(pool, monkeypatch)
    assert out == "proceeded"
    assert pool.status_reads == 3
    assert pool.position_reads == 2


def test_an_old_in_flight_row_is_pending_without_the_wait(monkeypatch, fast):
    """A rest_unknown row stays 'submitting' for minutes; waiting a
    window on it buys nothing. Pin the asset and move on."""
    pool = _Pool(settles_after=None, age_s=600.0)
    out = _drive(pool, monkeypatch)
    assert out == "mx_entry_in_flight"
    assert pool.status_reads == 0
    assert pool.position_reads == 1


def test_no_in_flight_row_is_still_no_position(monkeypatch, fast):
    pool = _Pool(in_flight=False)
    assert _drive(pool, monkeypatch) == "mx_no_position_of_ours"


def test_a_db_blip_during_the_wait_is_pending_not_no_position(monkeypatch, fast):
    """The poll and the re-query are the only unguarded reads in the
    exit path's front half; a blip there must not file the exit as
    'nothing to sell'."""
    pool = _Pool(settles_after=None, status_raises=True)
    out = _drive(pool, monkeypatch)
    assert out == "mx_entry_in_flight"
    assert pool.status_reads >= 1


def test_the_wait_is_bounded_by_the_window(monkeypatch):
    """No fixture: TTL 0 and a 50 ms grace. The deadline is checked
    after each poll, so the wait ends within one poll of the window."""
    import time
    monkeypatch.setattr(le, "REST_BID_TTL_S", 0.0)
    monkeypatch.setattr(le, "_INFLIGHT_POLL_S", 0.005)
    monkeypatch.setattr(le, "_INFLIGHT_GRACE_S", 0.05)
    t0 = time.monotonic()
    out = _drive(_Pool(settles_after=None), monkeypatch)
    assert out == "mx_entry_in_flight"
    assert time.monotonic() - t0 < 1.0


def test_an_unreadable_in_flight_lookup_is_pending_not_no_position(monkeypatch, fast):
    """ROUND FOUR: a DB error in the lookup itself returned 'no position'
    and dropped the exit. Unknown is pending."""
    pool = _Pool(lookup_raises=True)
    out = _drive(pool, monkeypatch)
    assert out == "mx_inflight_unreadable"
    assert out in le.EXIT_PENDING_REASONS


def test_the_in_flight_horizon_covers_a_rest_unknown_row():
    """The ledger reaper reconciles at ten minutes; a 60-second horizon
    made a rest_unknown row invisible after its first minute. The SQL
    carries exactly one interval literal, and it is the constant -- a
    narrower predicate beside the right constant would be invisible to
    a stub (round four)."""
    import inspect
    assert le._INFLIGHT_HORIZON == "30 minutes"     # past the reaper's worst case
    assert le._NAMED_HORIZON == "48 hours"          # a named position waits
    src = inspect.getsource(le._entry_in_flight)
    # exactly two interval literals, both the module constants: a
    # narrower predicate beside the right constant would be invisible
    # to a stub (round four)
    assert src.count("interval '") == 2
    assert "interval '{_INFLIGHT_HORIZON}'" in src
    assert "interval '{_NAMED_HORIZON}'" in src
    assert "error LIKE 'venue holds a POSITION%'" in src.replace("\"\n            \"", "")
