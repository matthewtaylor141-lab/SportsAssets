"""THE IN-CYCLE PAPER DECISION RECORDS AN ANSWER INSIDE ITS OWN DEADLINE, AND
A DECISION THAT DID NOT HAPPEN IS A ROW, NOT A LOG LINE.

Valuation 2102 (2026-10-01): the completed-game decision was not recorded in
the cycle; the scheduled backstop decided it ~55 s later and found the
Pinnacle probability 83 s old. The confirmed mismatch: the paper book read
carried NO deadline (the venue request gate may hold an undeadlined read up to
20 s, the SDK's HTTP timeout is 30 s) while the decision around it was
cancelled at 8 s -- and a cancelled decision records nothing. Proved here:

  * a book read slower than the decision's budget is cut BEFORE the deadline
    and the decision is RECORDED as a named refusal;
  * the default transport hands the deadline to the venue request gate;
  * a decision that raises or times out leaves a paper_hook_failures row with
    valuation, strategy, stage, elapsed time and error;
  * the active entry policy decides first, closest to the valuation instant;
  * the collector's cadence is start-to-start at CYCLE_S.
SYNTHETIC valuations and books; no venue is contacted.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from sportsassets import bettor_paper_guard as G
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.workers import ext_pinnacle_loop as X

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)


@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


def test_the_cadence_is_start_to_start():
    assert X.next_cycle_delay(ran=True, elapsed_s=270.0) == \
        pytest.approx(X.CYCLE_S - 270.0)
    assert X.next_cycle_delay(ran=True, elapsed_s=0.0) == X.CYCLE_S
    assert X.next_cycle_delay(ran=True, elapsed_s=5000.0) == X.IDLE_POLL_S
    assert X.next_cycle_delay(ran=False, elapsed_s=10.0) == X.IDLE_POLL_S


def test_the_default_transport_hands_the_deadline_to_the_gate(monkeypatch):
    seen = {}

    def fake(slug, *, deadline_epoch_s=None):
        seen.update(slug=slug, dl=deadline_epoch_s)
        return {"marketData": {"ok": True}}
    monkeypatch.setattr(X, "_read_book_blocking", fake)
    c = G.PaperMarketDataClient()
    dl = time.time() + 5.0
    got = asyncio.run(c.read_book("s-1", deadline_epoch_s=dl, timeout_s=5.0))
    assert got["marketData"] == {"ok": True}
    assert seen == {"slug": "s-1", "dl": dl}
    assert G._accepts_deadline(G._default_transport)


def test_a_slow_read_is_cut_at_its_timeout_with_a_named_error():
    def slow(slug):
        time.sleep(1.0)
        return {"marketData": {"ok": True}}
    c = G.PaperMarketDataClient(slow)

    async def go():
        t0 = time.monotonic()
        got = await c.read_book("s-2", deadline_epoch_s=time.time() + 0.2,
                                timeout_s=0.2)
        return got, time.monotonic() - t0
    # (asyncio.run then waits for the abandoned read thread at shutdown;
    # the decision has its answer at the timeout, which is what is measured)
    got, took = asyncio.run(go())
    assert took < 0.6
    assert got["error"] == G.R_BOOK_READ_DEADLINE


@pg
async def test_a_slow_book_still_records_the_decision_inside_the_deadline(
        cg_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "hookdl", now=now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        monkeypatch.setattr(PR, "VALUATION_HOOK_TIMEOUT_S", 3.0)

        def slow(slug):
            time.sleep(4.0)
            return {"marketData": None}
        t0 = time.monotonic()
        g = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"], now=now,
            market_data=G.PaperMarketDataClient(slow),
            account_id=acct["account_id"], fee_fn=FEE,
            schedule_fill=lambda: {"scheduled": False},
            # WITHOUT the one bounded retry (migration 189): the cut read is
            # decided at once; the retry itself is proved in
            # test_paper_exploration_maker_and_throughput.py
            book_retry=False)
        took = time.monotonic() - t0
        cg = g["benchmark_completed_game"]
        assert cg["decided"] is True, cg
        assert cg["verdict"] == "REFUSE"
        assert cg["refusal"] == PD.R_BOOK_DEADLINE
        assert cg["elapsed_s"] < 3.0
        d = await conn.fetchrow(
            "SELECT refusal FROM paper_decisions WHERE valuation_id=$1 AND "
            " strategy=$2", v["valuation_id"], PB.CG_STRATEGY)
        assert d["refusal"] == PD.R_BOOK_DEADLINE
        assert took < 3.0 + 8.0   # CG cut inside its budget; Derek after
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_hook_failures WHERE valuation_id=$1",
            v["valuation_id"]) == 0, "a recorded refusal is not a failure"
    finally:
        await PL.purge_everything(conn)
        await conn.execute("DELETE FROM paper_hook_failures WHERE "
                           " account_id LIKE 'paper_test_%'")
        await conn.close()


@pg
async def test_a_raised_or_timed_out_decision_leaves_a_failure_row(
        cg_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "hookfail", now=now)
        t = PL.Transport(now)
        v1 = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                                compatibility="INCOMPATIBLE")
        v2 = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                                compatibility="INCOMPATIBLE")
        real = PB.decide_one

        async def boom(conn_, ctx, row, pol=None):
            raise RuntimeError("synthetic failure")
        monkeypatch.setattr(PB, "decide_one", boom)
        await PR.decide_valuation(
            conn, valuation_id=v1["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})

        async def hang(conn_, ctx, row, pol=None):
            await asyncio.sleep(5.0)
            return await real(conn_, ctx, row, pol)
        monkeypatch.setattr(PB, "decide_one", hang)
        monkeypatch.setattr(PR, "VALUATION_HOOK_TIMEOUT_S", 0.5)
        await PR.decide_valuation(
            conn, valuation_id=v2["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        rows = {r["valuation_id"]: r for r in await conn.fetch(
            "SELECT * FROM paper_hook_failures WHERE account_id=$1 AND "
            " strategy=$2", acct["account_id"], PB.CG_STRATEGY)}
        e = rows[v1["valuation_id"]]
        assert e["outcome"] == "ERROR"
        assert "synthetic failure" in e["error"]
        assert e["stage"] == "IN_CYCLE_VALUATION_HOOK"
        assert e["elapsed_s"] is not None and e["session_id"]
        tmo = rows[v2["valuation_id"]]
        assert tmo["outcome"] == "TIMEOUT"
        assert 0.4 <= tmo["elapsed_s"] < 2.0
        # no decision row was written for either: the failure row is the
        # only trace, which is why it must exist
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_decisions WHERE strategy=$1 AND "
            " valuation_id = ANY($2::bigint[])", PB.CG_STRATEGY,
            [v1["valuation_id"], v2["valuation_id"]]) == 0
    finally:
        await PL.purge_everything(conn)
        await conn.execute("DELETE FROM paper_hook_failures WHERE "
                           " account_id LIKE 'paper_test_%'")
        await conn.close()


@pg
async def test_the_active_entry_policy_decides_first(cg_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    order = []
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "hookorder", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        real_b, real_d = PB.decide_one, PD.decide_one

        async def b(conn_, ctx, row, pol=None):
            order.append((pol or PB.STRICT_POLICY)["strategy"])
            return await real_b(conn_, ctx, row, pol)

        async def d(conn_, ctx, row):
            order.append("DEREK_ENTRY_POLICY_V2")
            return await real_d(conn_, ctx, row)
        monkeypatch.setattr(PB, "decide_one", b)
        monkeypatch.setattr(PD, "decide_one", d)
        g = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        assert order[0] == PB.CG_STRATEGY, order
        assert g["benchmark_completed_game"]["verdict"] == "ENTER"
    finally:
        await PL.purge_everything(conn)
        await conn.close()
