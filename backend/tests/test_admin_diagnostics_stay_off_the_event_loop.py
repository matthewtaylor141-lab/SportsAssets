"""Admin diagnostics never hold the API's event loop.

Production evidence (loop watchdog ring, 2026-10-02 14:48-14:50Z, while the
push-triggered engine-diagnostic workflow was reading the production API):
  * 5.10 s and 3.42 s stalls with the loop thread inside the edge
    decomposition and size-edge scoring (pure CPU over every resolved buy);
  * 4.42 s and 3.02 s stalls with the loop thread inside the bid-truth
    probe's synchronous venue client (retrieve_by_slug, bbo) and the venue
    pacing gate's blocking wait.
A 5.1 s stall is past /healthz's 5 s deadline. These tests keep a 50 ms
ticker running while each diagnostic does slow work.
"""
import ast
import asyncio
import inspect
import textwrap
import time

import pytest

from sportsassets.analytics import decompose as D
from sportsassets.analytics import size_edge as S


def _busy(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:                # pure-Python busy work
        sum(range(500))


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, *a, **k):
        return self.rows

    async def fetchrow(self, *a, **k):
        return {"buys": 1, "marked": 1, "pre_game_marked": 1, "resolved": 1}


async def _ticking(coro):
    ticks, gaps = [], []

    async def ticker():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            ticks.append(now)

    t = asyncio.create_task(ticker())
    try:
        out = await coro
    finally:
        t.cancel()
    return out, ticks, gaps


ROW = {"size": 10.0, "price": 0.5, "outcome_index": 0,
       "resolved_prices": [1, 0], "event_key": "e1",
       "p_5m": 0.5, "p_10m": 0.5, "p_60m": 0.5, "p_pre": 0.5, "id": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fn", [(S, "cohort_size_edge"),
                                    (D, "cohort_decompose")])
async def test_scoring_runs_while_the_loop_keeps_serving(monkeypatch, mod, fn):
    real = mod.score

    def slow_score(rows):
        _busy(1.2)
        return real(rows)
    monkeypatch.setattr(mod, "score", slow_score)
    out, ticks, gaps = await _ticking(
        getattr(mod, fn)(_Pool([dict(ROW)]), "rn1", 30))
    assert out["whale"] == "rn1" and out["n_rows"] == 1
    assert len(ticks) >= 8, len(ticks)            # served during scoring
    assert max(gaps) < 0.6, max(gaps)             # never blocked for long


def test_the_bid_truth_probe_calls_the_venue_client_only_in_a_worker_thread():
    from sportsassets.api import app as app_mod
    src = textwrap.dedent(inspect.getsource(app_mod.api_bid_truth))
    tree = ast.parse(src)
    offenders = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        # client.markets.<method>(...) or a getattr'd method `fn(...)`
        direct = (isinstance(f, ast.Attribute)
                  and isinstance(f.value, ast.Attribute)
                  and f.value.attr == "markets")
        bound = isinstance(f, ast.Name) and f.id == "fn"
        if direct or bound:
            offenders.append(ast.unparse(n))
    assert offenders == [], offenders
    # and the reads are still made, through asyncio.to_thread
    assert src.count("asyncio.to_thread(") >= 5
