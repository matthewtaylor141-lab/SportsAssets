"""RC6 · THE OBSERVATION PASS'S LABEL READS ARE PARSED OFF THE EVENT LOOP.

PRODUCTION EVIDENCE (RC5, release 69a8a07e, 2026-10-08, read-only
render-ops logs of sportsassets-api, 16:35-20:40Z): 13 of the 16
`ext_pinnacle: {...}` cycle completions came within 6 s of a `loop stall
recorded` line (a >= 2 s hold, watchdog overrun 0.0 s: Python on the loop
thread), i.e. in the cycle's last steps -- the pair observation pass's tail
(model planning and candidate evaluation), Derek's after-cycle step, the
heartbeat. Render restarted the API twice that day for an unanswered
/healthz (runtime_window.json, 16:34:42Z and 18:39:26Z). Inferred from
timing, not a stack: the watchdog now names the holder (loop_watchdog).

THE READ: `bettor_pair_observations.labelled` has no LIMIT -- every
labelled observation ever recorded -- and `labelled_conditional` partitions
each one's payout table and hashes its features. The pass reaches them
through `plan_windows` (twice per model key, two keys) and every candidate
evaluation, once per ext_pinnacle cycle, all on the API's event loop.
LOCAL BENCHMARK ONLY (synthetic rows, fake connection): 0.14 s for
`labelled` and 0.70 s for `labelled_conditional` at 10,000 observations.

PINNED HERE (each fails on 1c874c1f, where the rows were parsed inline):
  1 · the loop keeps ticking through both reads (10,000 observations for
      the conditional read, 60,000 for the plain one);
  2 · the per-row conditional rule runs in a worker thread;
  3 · the record shape is unchanged: same rows, same order, same labels,
      same exclusions.
"""
from __future__ import annotations

import asyncio
import gc
import json
import threading
import time

import pytest

from sportsassets import bettor_pair_observations as PO
from tests import test_indirect_structures as T

STRUCTURE = T.clean(T.spread("A", "-5/2"), T.ml("B")).to_dict()
FEATURES = {"f%02d" % i: 0.01 * i for i in range(24)}


def _row(i):
    pw, hw = bool(i % 2), bool(i % 3)
    return {"observation_id": "obs%06d" % i, "features": json.dumps(FEATURES),
            "structure": json.dumps(STRUCTURE), "middle_occurred": pw and hw,
            "fixture": "fx%d" % (i // 3), "obs_epoch": 1.0e9 + i,
            "feature_sha": "sha%d" % i, "avail_epoch": 1.0e9 + i + 9,
            "primary_slug": "a%d" % i,
            "primary_side": "ORDER_INTENT_BUY_LONG",
            "primary_settlement_price": 1.0 if pw else 0.0,
            "hedge_slug": "b%d" % i, "hedge_side": "ORDER_INTENT_BUY_LONG",
            "hedge_settlement_price": 1.0 if hw else 0.0,
            "primary_won": pw, "hedge_won": hw, "label_version": 1}


class _Conn:
    """Both reads answer the same synthetic labelled observations."""

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        return self.rows


@pytest.fixture
def schema(monkeypatch):
    async def _yes(conn):
        return True
    monkeypatch.setattr(PO, "has_schema", _yes)


async def _ticking(coro):
    """Run `coro` while a ticker measures the loop; (result, worst gap,
    duration)."""
    gaps = []
    done = asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    # THE COLLECTOR IS PAUSED FOR THE MEASUREMENT: a full collection stops
    # every thread, and late in a long test session (a large heap) one can
    # land inside the window whatever runs where. What is measured here is
    # where the parse runs, not the collector.
    gc.collect()
    gc.disable()
    try:
        t = asyncio.get_running_loop().create_task(ticker())
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        got = await coro
        took = time.monotonic() - t0
        done.set()
        await t
    finally:
        gc.enable()
    return got, max(gaps), took


# ── 1 · the loop keeps ticking ────────────────────────────────────────────

async def test_labelled_conditional_keeps_the_loop_ticking(schema):
    rows = [_row(i) for i in range(10000)]
    got, worst, took = await _ticking(PO.labelled_conditional(_Conn(rows)))
    assert got["ok"] is True and got["n"] > 0
    assert worst < max(0.1, took / 4), (worst, took)


async def test_labelled_keeps_the_loop_ticking(schema):
    rows = [_row(i) for i in range(60000)]
    got, worst, took = await _ticking(PO.labelled(_Conn(rows)))
    assert got["ok"] is True and got["n"] == 60000
    assert worst < max(0.1, took / 4), (worst, took)


# ── 2 · the per-row rule runs in a worker thread ─────────────────────────

async def test_the_conditional_rule_runs_in_a_worker_thread(schema,
                                                            monkeypatch):
    loop_thread = threading.get_ident()
    where = set()
    real = PO.conditional_row

    def spy(row):
        where.add(threading.get_ident() == loop_thread)
        return real(row)

    monkeypatch.setattr(PO, "conditional_row", spy)
    got = await PO.labelled_conditional(_Conn([_row(i) for i in range(30)]))
    assert got["ok"] is True
    assert where == {False}, "ran on the event loop"


# ── 3 · the record shape is unchanged ─────────────────────────────────────

async def test_the_records_are_the_same_rows_in_the_same_order(schema):
    rows = [_row(i) for i in range(60)]
    lab = await PO.labelled(_Conn(rows))
    assert lab["decision_ids"] == [r["observation_id"] for r in rows]
    assert lab["labels"] == [1.0 if r["middle_occurred"] else 0.0
                             for r in rows]
    assert lab["rows"] == [FEATURES] * 60
    assert lab["feature_shas"] == [r["feature_sha"] for r in rows]
    assert lab["n"] == 60 and lab["n_events"] == 20
    cond = await PO.labelled_conditional(_Conn(rows))
    want = [PO.conditional_row(r) for r in rows]
    kept = [w for w in want if w["include"]]
    assert cond["labels"] == [w["label"] for w in kept]
    assert cond["decision_ids"] == [r["observation_id"] for r, w
                                    in zip(rows, want) if w["include"]]
    assert cond["n_excluded"] == len(want) - len(kept)
    assert sum(cond["excluded"].values()) == len(want) - len(kept)
