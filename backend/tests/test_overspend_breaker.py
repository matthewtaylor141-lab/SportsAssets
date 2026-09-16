"""The overspend breaker: bounded damage when prevention is impossible.

I told the owner that running live was safe because submit_fok's
preview cost guard would refuse any order the venue priced above what
we authorized. That was WRONG, and PRICE-TRUTH is what proved it:

    PTRUTH BUY_LONG:  venue_cost=$3.0 ratio=1.0 matches OUR price
    PTRUTH BUY_SHORT: venue_cost=$3.0 ratio=1.0 matches OUR price

The venue's preview simply echoes our own price * quantity. So
prev_cost always equals expected_cost, the ratio is always 1.000, and
that guard can NEVER see an overcharge. The overcharge happens at
EXECUTION, which no pre-trade check observes.

What IS observable is the fill. So the breaker is post-fill: the first
overspend costs one clip and stops the sleeve, rather than repeating
across every copy for the rest of the night. That is bounded damage,
not prevention — and the distinction is the whole point of this file.

It is deliberately NOT clearable by env. LIVE_COPY_HALT is the owner's
lever; this one is evidence of a live money defect and needs a
deliberate admin clear after someone has read the receipts.
"""

import inspect

import pytest

from sportsassets import live_executor as le

# Captured at IMPORT time, before conftest's autouse fixture stubs it
# out for the rest of the suite. These tests are the one place that
# must exercise the real function.
_REAL_OVERSPEND_HALT = le.overspend_halt


class _Pool:
    def __init__(self, value=None, raises=False):
        self._v, self._raises = value, raises
        self.executed = []

    async def fetchval(self, *a, **k):
        if self._raises:
            raise RuntimeError("connection reset")
        return self._v

    async def execute(self, *a, **k):
        self.executed.append(a)


class TestBreakerRead:
    @pytest.mark.asyncio
    async def test_an_untripped_breaker_reads_none(self):
        assert await _REAL_OVERSPEND_HALT(_Pool(None)) is None

    @pytest.mark.asyncio
    async def test_a_tripped_breaker_returns_its_record(self):
        rec = {"ratio": 3.87, "why": "venue filled above our limit"}
        assert await _REAL_OVERSPEND_HALT(_Pool(rec)) == rec

    @pytest.mark.asyncio
    async def test_an_unreadable_breaker_counts_as_tripped(self):
        """"The database did not answer" is not evidence that nothing is
        wrong. A read failure must not open the money path."""
        out = await _REAL_OVERSPEND_HALT(_Pool(raises=True))
        assert out, "an unreadable breaker must refuse, not pass"
        assert "unreadable" in str(out)


class TestItGuardsTheCommonGate:
    def test_the_breaker_is_checked_in_maybe_execute(self):
        src = inspect.getsource(le.maybe_execute)
        assert "overspend_halt(pool)" in src

    def test_it_is_checked_before_anything_is_decided(self):
        """It reads the DB, so it cannot precede `pool = await
        get_pool()`. What matters is that nothing about the order has
        been decided yet when it refuses — no sizing, no pricing, no
        row written."""
        src = inspect.getsource(le.maybe_execute)
        at = src.index("overspend_halt(pool)")
        for later in ("plan_order(", "INSERT INTO live_orders",
                      "submit_fok"):
            assert at < src.index(later), \
                f"the breaker must be checked before {later}"

    def test_no_env_var_can_clear_it(self):
        """LIVE_COPY_HALT is the owner's lever. This breaker is evidence
        of a money defect and must not share that switch — otherwise
        lifting the halt would silently clear the breaker too."""
        src = inspect.getsource(_REAL_OVERSPEND_HALT)
        assert "environ" not in src and "getenv" not in src


class TestTheDetectorThatTripsIt:
    """The arithmetic the breaker fires on — see test_overspend.py for
    the full table. Pinned here so the two cannot drift apart."""

    def test_the_real_rows_all_trip(self):
        rows = [(249.78, 1086, 0.89), (249.92, 781, 0.6853),
                (249.75, 675, 0.65), (249.75, 555, 0.56),
                (249.60, 520, 0.55)]
        for req, sh, px in rows:
            assert le.is_overspend(req, sh, px) is True

    def test_an_honest_fill_does_not_trip_it(self):
        assert le.is_overspend(249.75, 675, 0.37) is False
        assert le.is_overspend(249.92, 781, 0.32) is False
