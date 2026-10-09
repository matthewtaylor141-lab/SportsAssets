"""A short CPU lane job, timed while a long computation runs (RC6.1
api-stall2): is anything queued on the lane kept waiting behind it?

The job is a real one: the Command read /api/command/paper/capital-
authority's census (bettor_capital_authority.blocker_census), whose tally
runs on the CPU lane, over 3,000 production-shaped refusal rows -- the
shape the review of 62842fc1 (a first version, never merged) measured:
38.97 s with the rn1x fit on the lane, 0.16 s with it on its own thread.
Each run is bounded by the paper hook's deadline (paper_runtime.
VALUATION_HOOK_TIMEOUT_S, 8 s): a paper decision's context rebuild puts its
model check on the same lane under it.

ALL DATA HERE IS SYNTHETIC; nothing touches a database.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import random
import sys
import time

#: the longest a short lane job may wait while one of the computations
#: under test runs: four slices (cpu_lane.SLICE_S) of margin, an eighth of
#: the paper hook's 8 s deadline
LANE_WAIT_S = 1.0


def paper_hook_deadline_s() -> float:
    from sportsassets.agents.paper_runtime import VALUATION_HOOK_TIMEOUT_S
    return float(VALUATION_HOOK_TIMEOUT_S)


class CensusConn:
    """The capital authority's refusal read, answered from memory."""

    def __init__(self, n: int = 3000):
        rng = random.Random(1)
        now = dt.datetime.now(dt.timezone.utc)
        self.rows = [{
            "strategy": rng.choice(["S1", "S2"]), "stage": "ENTRY",
            "refusal": rng.choice(["R_A", "R_B", "R_C"]),
            "us_market_slug": "slug-%d" % rng.randrange(300),
            "holding_side": rng.choice(["YES", "NO"]), "fixture": "fx",
            "line": None, "scope": "GAME", "gross_edge_pp": 1.0,
            "edge_shortfall_pp": 0.5, "expected_fees_usd": 0.1,
            "slippage_usd": 0.0, "adverse_selection_usd": 0.0,
            "total_executable_ev_usd": rng.uniform(-1, 1), "refused_at": now}
            for _ in range(n)]

    async def fetch(self, sql, *a):
        return self.rows


async def command_reads_while(started, finished, *, n: int = 3) -> tuple:
    """Once `started` (a threading.Event) is set, and 0.1 s more, time `n`
    runs of the census, each under the paper hook's deadline (None when it
    is cut). Returns (latencies in seconds, whether `finished()` was still
    False after the last run -- i.e. whether every run overlapped the
    computation)."""
    from sportsassets import bettor_capital_authority as CA
    while not started.is_set():
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.1)
    deadline = paper_hook_deadline_s()
    conn, lat = CensusConn(), []
    for _ in range(n):
        t = time.monotonic()
        try:
            got = await asyncio.wait_for(
                CA.blocker_census(conn, "paper_acct_main", since=0.0),
                deadline)
            assert got
            lat.append(round(time.monotonic() - t, 3))
        except asyncio.TimeoutError:
            lat.append(None)
        await asyncio.sleep(0.05)
    return lat, not finished()


@contextlib.contextmanager
def production_gil():
    """The API process's GIL switch interval (api.app.GIL_SWITCH_INTERVAL_S)
    for the duration."""
    from sportsassets.api.app import GIL_SWITCH_INTERVAL_S
    was = sys.getswitchinterval()
    sys.setswitchinterval(GIL_SWITCH_INTERVAL_S)
    try:
        yield
    finally:
        sys.setswitchinterval(was)
