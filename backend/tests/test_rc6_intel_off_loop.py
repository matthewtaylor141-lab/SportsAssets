"""RC6 · THE INTEL SHADOW CYCLE'S PYTHON RUNS OFF THE API'S EVENT LOOP.

PRODUCTION EVIDENCE (RC5, release 69a8a07e, 2026-10-08, read-only):
render-ops logs of sportsassets-api, 16:32-20:30Z: 19 of the 24 intel shadow
cycles ('intel shadow cycle:') completed 0-12 s after a `loop stall
recorded` line (a >= 2 s hold of the event loop); twin research and
profitability cycles, 0 of 6. The same day Render restarted the API twice
for an unanswered /healthz (runtime_window.json: server_failed / unhealthy,
16:34:42Z and 18:39:26Z, not OOM).

LOCAL MEASUREMENT (a benchmark database of SYNTHETIC rows at the intel
reads' own LIMITs: 20,000 valuations, 20,000 decisions, 20,000 equity and
mirror snapshots; never production data): one cycle held the loop 5 times
>= 30 ms on 1c874c1f, the longest 0.42 s inside calibration.load_records
(normalising 40,000 rows) and 0.09 s inside attribution.load_paper; after
this change, 0 holds >= 30 ms. The production instance is one shared CPU,
where the same work is several times longer.

PINNED HERE (each fails on 1c874c1f, where all of it ran on the loop):
  1 · a full cycle (synthetic rows, rolled back) runs the calibration
      normalisation, the independence rule, the overlay plan, the
      attribution, and the risk aggregation, classification, equity and
      report in worker threads -- and still writes the same SHADOW results;
  2 · calibration.load_records over 60,000 rows keeps the loop ticking: the
      longest gap the loop sees is a small fraction of the work's length;
  3 · the ACTUAL attribution finds a group's fills through one index, in
      the fills' own order -- the same rows the per-intent scan found.
"""
from __future__ import annotations

import asyncio
import gc
import threading
import time

import asyncpg
import pytest

from sportsassets.intel import attribution as AT
from sportsassets.intel import calibration as CAL
from sportsassets.intel import risk as RK
from sportsassets.intel import runner as RUN

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: every pure step that used to run on the loop, by module and name
OFF_LOOP = ((CAL, "normalize_valuation"), (CAL, "normalize_decision"),
            (CAL, "independent"), (CAL, "overlay_plan"),
            (AT, "attribute"),
            (RK, "aggregate_paper"), (RK, "aggregate_actual"),
            (RK, "classify"), (RK, "equity_risk"), (RK, "report"))


def _spy(monkeypatch, loop_thread):
    seen: dict = {}
    for mod, name in OFF_LOOP:
        real = getattr(mod, name)

        def wrapper(*a, _real=real, _key="%s.%s" % (mod.__name__, name),
                    **k):
            seen.setdefault(_key, set()).add(
                threading.get_ident() == loop_thread)
            return _real(*a, **k)
        monkeypatch.setattr(mod, name, wrapper)
    return seen


async def _seed(conn, now):
    acct = await H.new_account(conn, "intelrc6", now=now - 86400)
    exp = F.uid("INTEL_RC6_EXP_")
    vid = await F.valuation(conn, experiment_id=exp, at=now - 4000, p=0.62,
                            outcome=1)
    d = await F.decision(conn, acct, at=now - 600, p=0.62, vwap=0.52,
                         fees_usd=1.0, qty=100, depth=500.0,
                         valuation_id=vid)
    g = await F.position(conn, acct, slug=d["slug"], qty=100, price=0.53,
                         fee=1.0, at=now - 590, decision_id=d["decision_id"],
                         event_key=d["event_key"])
    await F.book(conn, d["slug"], now - 30, bids=((0.55, 100),),
                 offers=((0.57, 100),))
    await F.equity(conn, acct, at=now - 60, equity_usd=500050.0)
    return acct, exp, d, g


# ── 1 · a full cycle, its Python in worker threads ──────────────────────

@pg
async def test_a_full_cycle_runs_its_pure_steps_off_the_loop(monkeypatch):
    seen = _spy(monkeypatch, threading.get_ident())
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, exp, d, g = await _seed(conn, now)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  experiment_id=exp, slug_prefix="intel-mkt-",
                                  include_actual=True)
        assert got["ran"] is True, got
        assert set(got["components"].values()) <= {"OK"}, got
        # the same SHADOW results as the capital-critical proof's cycle
        attr = await conn.fetchrow(
            "SELECT * FROM intel_attribution WHERE subject_id=$1", g)
        assert attr["label"] == "SHADOW"
        assert attr["model_edge_usd"] == pytest.approx(10.0)
        assert attr["slippage_usd"] == pytest.approx(1.0)
        siz = await conn.fetchrow(
            "SELECT * FROM intel_sizing WHERE decision_id=$1",
            d["decision_id"])
        assert siz["paper_qty"] == 100 and siz["applied"] is False
    finally:
        await tr.rollback()
        await conn.close()
    missing = ["%s.%s" % (m.__name__, n) for m, n in OFF_LOOP
               if "%s.%s" % (m.__name__, n) not in seen]
    assert not missing, "never called: %s" % missing
    on_loop = sorted(k for k, v in seen.items() if True in v)
    assert not on_loop, "ran on the event loop: %s" % on_loop


# ── 2 · the loop keeps ticking through the normalisation ────────────────

def _valuation_rows(n):
    return [{"id": i, "version": "v1", "devig_method": "power",
             "sport_family": "baseball", "market": "h2h",
             "event_key": "ev%d" % (i % 3000), "payout_event": "HOME",
             "probability": 0.2 + (i % 60) / 100.0,
             "outcome_known": bool(i % 2), "outcome": (i % 2) or None,
             "outcome_basis": "VENUE_SETTLEMENT_PRICE",
             "buy_intent": "ORDER_INTENT_BUY_LONG",
             "us_market_slug": "s%d" % (i % 3000),
             "execution_estimate": '{"depth_usd": 120.0}',
             "executable_price": 0.5,
             "observed_at_epoch": 1.79e9 - i,
             "decided_at_epoch": 1.79e9 - i} for i in range(n)]


class _Conn:
    """Answers load_records' reads: n valuations, nothing else."""

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        return self.rows if "FROM external_valuations" in sql and \
            "WHERE decided_at" in sql else []


async def test_load_records_keeps_the_loop_ticking():
    rows = _valuation_rows(60000)
    gaps = []
    done = asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    # the collector is paused for the measurement (a full collection stops
    # every thread; what is measured is where the normalisation runs)
    gc.collect()
    gc.disable()
    try:
        t = asyncio.get_running_loop().create_task(ticker())
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        recs = await CAL.load_records(_Conn(rows), now=1.8e9)
        took = time.monotonic() - t0
        done.set()
        await t
    finally:
        gc.enable()
    assert len(recs) == 60000
    worst = max(gaps)
    # the work is in a worker thread: the loop runs every switch interval,
    # so the worst gap is a small fraction of the work, never all of it
    assert worst < max(0.1, took / 4), (worst, took)


# ── 3 · the ACTUAL attribution's per-group fills ─────────────────────────

def test_actual_rows_reads_each_groups_fills_in_their_own_order():
    fills = [{"group_id": "g%d" % (i % 3), "us_market_slug": "s",
              "intent": "ORDER_INTENT_BUY_LONG", "qty": 1 + i,
              "price": 0.5, "fee_usd": 0.0, "role": "ENTRY"}
             for i in range(9)]
    rows = [{"group_id": "g%d" % j, "us_market_slug": "s",
             "order_intent": "ORDER_INTENT_BUY_LONG", "decision_id": None,
             "valuation_id": None, "wire_price": 0.5, "strategy": "x",
             "decided_at": None} for j in (0, 1, 2, 1)]
    got = AT.actual_rows(rows, fills, {}, {}, {})
    assert [r["subject_id"] for r in got] == ["g0", "g1", "g2"]
    by = {r["subject_id"]: r for r in got}
    for j in range(3):
        mine = [f for f in fills if f["group_id"] == "g%d" % j]
        assert by["g%d" % j]["entry_qty"] == pytest.approx(
            sum(f["qty"] for f in mine))
