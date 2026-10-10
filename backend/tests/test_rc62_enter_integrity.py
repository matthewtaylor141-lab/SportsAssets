"""RC6.2 ENTER INTEGRITY: AN ENTER IS TRUTHFUL, AND IT ENDS IN AN ORDER, A
NAMED REFUSAL OR A NAMED ABANDONMENT -- NEVER IN NOTHING.

Production readback, 2026-10-06 05:37:53Z .. 2026-10-09 (research-sql
rc62_enter_integrity_cut, runs 37962750532 / 37964243574):

  LIFECYCLE  PINNACLE_EXPLORATION_PAPER has been QUARANTINED since lifecycle
             event 3 (LOSS_BUDGET_QUARANTINE). Its decision path never read
             the lifecycle (Derek, the completed-game and the strict
             benchmark policies read it in PD.capital_gate), so ~2,700
             decisions recorded ENTER that only the ledger's entry gate
             refused (census stage LEDGER, STRATEGY_LIFECYCLE_QUARANTINED_
             NO_PAPER_ENTRY). The maker policy had the same omission. Now
             both read PD.lifecycle_gate as their last entry condition: a
             no-entry state is a DECISION-stage refusal with the lifecycle's
             code, state and event id; the decision row still exists; the
             ledger gate is unchanged and still refuses on its own.
  THE CUT    13 exploration ENTERs (2026-10-06 20:04:21Z .. 2026-10-09
             16:11:38Z) with neither an order nor a refusal: 12 of 13 were
             carried by a pinnapi reactive evaluation that ended TIMEOUT at
             its 12 s deadline while the exploration decision's order
             sequence (execution intent -> paper order) was running; the
             CancelledError reached PD.bounded_decision, whose finally
             cancelled the decision mid-sequence. Completed-game had 9 more
             on 2026-10-06 (8 of them reactive TIMEOUTs). Now the order
             sequence of a recorded ENTER runs shielded (PD.owed_order): the
             caller's cancellation still stands, but only after the order --
             or its named refusal -- is written; a sequence that cannot
             finish inside the grace is cancelled and named at once
             (ENTER_ORDER_ABANDONED, with its cause).

The cut is reproduced exactly as production cut it: the in-cycle valuation
hook (paper_runtime.decide_valuation) under an `asyncio.timeout` whose
deadline falls while the order sequence is in flight (the reactive
scheduler's `async with asyncio.timeout(self.deadline)`), and the paper pass
under the same. SYNTHETIC valuations and books; no venue order (every client
counts mutation attempts).
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import time

import pytest

from sportsassets import bettor_capital_authority as CA
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_strategy_lifecycle as LC
from sportsassets import decision_hooks as DH
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_maker as PMK
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CG, MAKER, EXPLORE = PB.CG_STRATEGY, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY


async def _nosleep(_):
    return None


def _only(*on):
    for k in (CG, MAKER, EXPLORE, PB.CONTROL_KEY):
        PL.set_policy_control(k, k in on)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    _only(CG, EXPLORE)
    PB._CONTEXT_CACHE.clear()


@pytest.fixture
def explore_only(env):
    _only(EXPLORE)


@pytest.fixture
def maker_only(env):
    _only(MAKER)


async def _setup(conn, tag, now):
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    acct = await PL.new_account(conn, tag, now=now)
    return acct, PL.Transport(now)


async def _pass(conn, acct, t, now, client):
    t.t = max(t.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, sleep=_nosleep)


async def _dec(conn, acct, vid, strategy):
    return await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
        " valuation_id=$2 AND strategy=$3", acct["session_id"], vid, strategy)


async def _state(conn, acct, strategy, to, *, at):
    return await LC.record(conn, account_id=acct["account_id"],
                           strategy=strategy, from_state=None, to_state=to,
                           rule_id="TEST", actor="person:test",
                           evidence={"t": 1}, why="test", at=at - 30)


async def _census(conn, did):
    return [dict(r) for r in await conn.fetch(
        "SELECT stage, refusal, refusals, detail FROM "
        " paper_entry_refusal_census WHERE decision_id=$1 ORDER BY "
        " refusal_id", did)]


async def _findings(conn, acct, did):
    return {r["kind"]: H.j(r["detail"]) for r in await conn.fetch(
        "SELECT kind, detail FROM paper_audrey_findings WHERE account_id=$1 "
        "   AND subject=$2", acct["account_id"], did)}


async def _orders(conn, did):
    return await conn.fetch("SELECT * FROM paper_orders WHERE decision_id=$1",
                            did)


def _condition(dec, name):
    return next(c for c in H.j(dec["policy_decision"])["conditions"]
                if c["condition"] == name)


# ═════════════════════════════════════════════════════════════════════
# 1 · A NO-ENTRY LIFECYCLE STATE IS REFUSED AT THE DECISION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_quarantined_exploration_refuses_at_decision_with_the_lifecycle_code(
        explore_only):
    """The production case: QUARANTINED exploration, a candidate that passes
    every other condition (3 pp at $0.50, positive EV after fees). The
    decision row exists, its verdict is REFUSE by the lifecycle's own code,
    the census row is stage DECISION with the state and event id, and the
    ledger was never reached (no LEDGER row, no order, no risk finding)."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "eiq", now)
        eid = await _state(conn, acct, EXPLORE, LC.QUARANTINED, at=now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.53,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        p = await _pass(conn, acct, t, now, client)
        assert p["ran"] and not p["errors"], p["errors"]
        assert client.mutation_attempts == 0
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d is not None, "the decision population is not shrunk"
        assert d["verdict"] == "REFUSE", d["refusals"]
        assert d["refusal"] == LC.R_LIFECYCLE_QUARANTINED
        assert list(d["refusals"]) == [LC.R_LIFECYCLE_QUARANTINED]
        cond = _condition(d, "strategy_lifecycle_allows_a_paper_entry")
        assert cond["passed"] is False and cond["value"] == LC.QUARANTINED
        assert cond["lifecycle_event_id"] == eid
        # every OTHER condition was evaluated and passed: only the
        # lifecycle refused (the economics are the ones an ENTER had)
        assert H.j(d["economics"])["estimate"]["expected_net_profit_usd"] > 0
        assert d["proposed_qty"] is not None and d["limit_price"] == 0.5
        rows = await _census(conn, d["decision_id"])
        assert [(r["stage"], r["refusal"]) for r in rows] == [
            ("DECISION", LC.R_LIFECYCLE_QUARANTINED)]
        lc = H.j(rows[0]["detail"])["lifecycle"]
        assert lc["state"] == LC.QUARANTINED
        assert lc["lifecycle_event_id"] == eid
        assert await _orders(conn, d["decision_id"]) == []
        assert PB.R_ORDER_REFUSED not in await _findings(
            conn, acct, d["decision_id"])
        # THE SAME SHADOW the ledger recorded when it refused (same bind,
        # same size and EV), now labelled as the decision's
        sh = await conn.fetch(
            "SELECT source, capital_refusal, lifecycle_state, qty, "
            "       total_executable_ev_usd, order_type FROM "
            " paper_shadow_counterfactuals WHERE decision_id=$1",
            d["decision_id"])
        assert [(r["source"], r["capital_refusal"], r["lifecycle_state"],
                 r["order_type"]) for r in sh] == [
            (CA.SRC_DECISION, LC.R_LIFECYCLE_QUARANTINED, LC.QUARANTINED,
             "MARKETABLE")]
        assert float(sh[0]["qty"]) == float(d["proposed_qty"])
        assert float(sh[0]["total_executable_ev_usd"]) > 0
        # the backstop has nothing to name: there is no ENTER
        got = await PD.step_enter_backstop(conn, {
            "account_id": acct["account_id"], "now": now + 61.0})
        assert got["examined"] == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_quarantined_maker_refuses_at_decision_with_the_lifecycle_code(
        maker_only):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "eim", now)
        eid = await _state(conn, acct, MAKER, LC.SHADOW_ONLY, at=now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.515,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        p = await _pass(conn, acct, t, now, client)
        assert p["ran"] and not p["errors"], p["errors"]
        d = await _dec(conn, acct, v["valuation_id"], MAKER)
        assert d is not None and d["verdict"] == "REFUSE", d
        assert list(d["refusals"]) == [LC.R_LIFECYCLE_SHADOW_ONLY]
        cond = _condition(d, "strategy_lifecycle_allows_a_paper_entry")
        assert cond["lifecycle_event_id"] == eid
        assert float(d["limit_price"]) == 0.49, "the resting price it had"
        rows = await _census(conn, d["decision_id"])
        assert [(r["stage"], r["refusal"]) for r in rows] == [
            ("DECISION", LC.R_LIFECYCLE_SHADOW_ONLY)]
        assert H.j(rows[0]["detail"])["lifecycle"]["lifecycle_event_id"] \
            == eid
        assert await _orders(conn, d["decision_id"]) == []
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_reduced_size_still_enters_and_an_unreadable_state_refuses(
        explore_only, monkeypatch):
    """REDUCED_SIZE is not a no-entry state: the decision ENTERs (the size
    cap stays the ledger's, as before). An unreadable lifecycle refuses at
    the decision, fail-closed, as the ledger would."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "eir", now)
        await _state(conn, acct, EXPLORE, LC.REDUCED_SIZE, at=now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.53,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        p = await _pass(conn, acct, t, now, client)
        assert not p["errors"], p["errors"]
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d["verdict"] == "ENTER", d["refusals"]
        cond = _condition(d, "strategy_lifecycle_allows_a_paper_entry")
        assert cond["passed"] is True and cond["size_factor"] == 0.5
        o = await _orders(conn, d["decision_id"])
        assert len(o) == 1
        assert float(o[0]["qty"]) < float(d["proposed_qty"]), \
            "the ledger's REDUCED_SIZE cap, unchanged"

        async def boom(*a, **k):
            raise RuntimeError("lifecycle table unreadable")
        monkeypatch.setattr(LC, "decision_gate", boom)
        v2 = await PL.valuation(conn, decided_at=now - 10, p_pin=0.53,
                                compatibility="INCOMPATIBLE")
        t.set(v2["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        p2 = await _pass(conn, acct, t, now, client)
        assert not p2["errors"], p2["errors"]
        d2 = await _dec(conn, acct, v2["valuation_id"], EXPLORE)
        assert d2["verdict"] == "REFUSE"
        assert list(d2["refusals"]) == [LC.R_LIFECYCLE_UNREADABLE]
        assert [(r["stage"], r["refusal"]) for r in await _census(
            conn, d2["decision_id"])] == [("DECISION",
                                           LC.R_LIFECYCLE_UNREADABLE)]
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_ledger_entry_gate_is_unchanged_and_still_refuses(
        explore_only):
    """DEFENCE IN DEPTH: an ENTRY BUY that reaches the ledger for a
    quarantined strategy (whatever path wrote it) is still refused under the
    account lock by the lifecycle's code, census stage LEDGER."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, _t = await _setup(conn, "eil", now)
        await _state(conn, acct, EXPLORE, LC.QUARANTINED, at=now)
        o = H.order(acct, key="direct", qty=10, limit=0.5, at=now)
        o["strategy"] = EXPLORE
        o["decision_id"] = "paperexp:direct-%s" % acct["account_id"][-6:]
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=now)
        assert got["ok"] is False
        assert got["refusal"] == LC.R_LIFECYCLE_QUARANTINED
        assert got["under_lock"] is True
        rows = await _census(conn, o["decision_id"])
        assert [(r["stage"], r["refusal"]) for r in rows] == [
            ("LEDGER", LC.R_LIFECYCLE_QUARANTINED)]
    finally:
        await PL.purge_everything(conn)
        await conn.close()


def test_every_paper_policy_reads_the_same_lifecycle_gate():
    """One reader: capital_gate (Derek, completed-game, strict benchmark)
    and the exploration and maker decisions all go through
    PD.lifecycle_gate -> bettor_strategy_lifecycle.decision_gate."""
    assert "lifecycle_gate(" in inspect.getsource(PD.capital_gate)
    assert "LC.decision_gate(" in inspect.getsource(PD.lifecycle_gate)
    for mod in (PEX, PMK):
        src = inspect.getsource(mod.decide_one)
        assert "PD.lifecycle_gate(" in src, mod.__name__
        # the lifecycle is the LAST entry condition: read after every other
        # refusal, immediately before the verdict
        assert src.index("PD.lifecycle_gate(") < src.index(
            "verdict = DP.ENTER if not refusals else DP.REFUSE")
        assert src.index("PD.recheck_primary_reference(") < src.index(
            "PD.lifecycle_gate(")
    # the ledger's gate is still there, under the lock
    assert "LC.entry_gate(" in inspect.getsource(L._submit_order)


# ═════════════════════════════════════════════════════════════════════
# 2 · THE CUT: A RECORDED ENTER ENDS IN AN ORDER, A REFUSAL OR A NAME
# ═════════════════════════════════════════════════════════════════════

class _Deadline:
    """The reactive scheduler's `async with asyncio.timeout(deadline)`, its
    deadline made to fall at the instant the order sequence is in flight."""

    def __init__(self):
        self.cm = None
        self.fired = 0

    def now(self):
        self.fired += 1
        self.cm.reschedule(asyncio.get_running_loop().time())


def _slow_hook(dl: _Deadline, calls: list, *, sleep_s: float):
    """The execution-intent hook (decision_hooks.DECISION_HOOK, written
    between the ENTER row and the paper order): the reactive deadline falls
    as it starts, and it takes `sleep_s` -- production measured 0.010 ..
    0.083 s for the intent write, then the submit."""
    async def hook(conn, payload):
        if dl.fired == 0:
            dl.now()
        await asyncio.sleep(sleep_s)
        calls.append(payload["decision_id"])
        return {"intent_id": None, "actual_lane": "PAPER_ONLY"}
    return hook


async def _hook_decision(conn, acct, v, dl: _Deadline, client):
    """paper_runtime.decide_valuation (production's in-cycle path) under the
    reactive deadline. Returns what the deadline raised (None: nothing)."""
    try:
        async with asyncio.timeout(None) as cm:
            dl.cm = cm
            await PR.decide_valuation(
                conn, valuation_id=v["valuation_id"], market_data=client,
                account_id=acct["account_id"],
                schedule_fill=lambda: {"scheduled": False}, book_retry=False)
    except TimeoutError as exc:
        return exc
    return None


@pg
async def test_a_reactive_deadline_inside_the_order_sequence_no_longer_cuts_it(
        explore_only, monkeypatch):
    """THE PRODUCTION CUT, REPRODUCED. ACTIVE exploration ENTERs in cycle;
    the reactive evaluation's deadline falls while its execution intent is
    being written. Before the fix the CancelledError cut the sequence: the
    ENTER row stayed with no paper order and no refusal, and 60 s later the
    backstop named it ENTER_WITHOUT_ORDER (the 13 production rows). Now the
    order is written, the evaluation still times out, and the backstop has
    nothing to name."""
    conn = await H.connect()
    now = time.time()
    dl, calls = _Deadline(), []
    monkeypatch.setattr(DH, "DECISION_HOOK", _slow_hook(dl, calls,
                                                        sleep_s=0.3))
    try:
        acct, t = await _setup(conn, "eic", now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.53,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        # (getattr: the same proof runs on the base, where it must fail on
        # the missing order, not on a missing name)
        before = dict(getattr(PD, "OWED_ORDER_COUNTS", {}))
        raised = await _hook_decision(conn, acct, v, dl, client)
        assert isinstance(raised, TimeoutError), \
            "the caller's deadline still stands"
        assert dl.fired == 1
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d is not None and d["verdict"] == "ENTER", d
        o = await _orders(conn, d["decision_id"])
        assert len(o) == 1, ("a recorded ENTER whose order sequence the "
                             "caller's deadline cut has no order")
        assert calls == [d["decision_id"]], "the intent hook completed"
        assert o[0]["strategy"] == EXPLORE and o[0]["role"] == "ENTRY"
        assert PD.OWED_ORDER_COUNTS["completed_after_cancellation"] == \
            before["completed_after_cancellation"] + 1
        assert client.mutation_attempts == 0
        got = await PD.step_enter_backstop(conn, {
            "account_id": acct["account_id"], "now": time.time() + 61.0})
        assert got["enter_without_order"] == 0, got
        assert PD.F_ENTER_WITHOUT_ORDER not in await _findings(
            conn, acct, d["decision_id"])
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_quarantined_exploration_cut_still_ends_in_its_named_refusal(
        explore_only, monkeypatch):
    """The production rows exactly: QUARANTINED exploration on the BASE
    decision path recorded ENTER and the deadline cut the ledger's refusal.
    Now the decision refuses at once (no ENTER, nothing owed), so the
    deadline has nothing to cut: the refusal and its census row exist."""
    conn = await H.connect()
    now = time.time()
    dl, calls = _Deadline(), []
    monkeypatch.setattr(DH, "DECISION_HOOK", _slow_hook(dl, calls,
                                                        sleep_s=0.3))
    try:
        acct, t = await _setup(conn, "eiqc", now)
        await _state(conn, acct, EXPLORE, LC.QUARANTINED, at=now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.53,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        raised = await _hook_decision(conn, acct, v, dl, PL.client(t))
        assert raised is None and dl.fired == 0 and calls == []
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d["verdict"] == "REFUSE"
        assert d["refusal"] == LC.R_LIFECYCLE_QUARANTINED
        assert [r["stage"] for r in await _census(conn, d["decision_id"])] \
            == ["DECISION"]
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_sequence_that_cannot_finish_in_the_grace_is_named_at_once(
        explore_only, monkeypatch):
    """The caller's deadline falls and the intent hook is wedged past the
    grace: the sequence is cancelled and the ENTER is named AT ONCE --
    ENTER_ORDER_ABANDONED, cause, the stage it reached -- not 60 s later
    without a cause. No order is placed late; the backstop does not name it
    twice."""
    conn = await H.connect()
    now = time.time()
    dl, calls = _Deadline(), []
    monkeypatch.setattr(DH, "DECISION_HOOK", _slow_hook(dl, calls,
                                                        sleep_s=30.0))
    monkeypatch.setattr(PD, "ENTER_ORDER_GRACE_S", 0.3)
    try:
        acct, t = await _setup(conn, "eig", now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.53,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        t0 = time.monotonic()
        raised = await _hook_decision(conn, acct, v, dl, PL.client(t))
        assert isinstance(raised, TimeoutError)
        assert time.monotonic() - t0 < 10.0, "bounded by the grace"
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d["verdict"] == "ENTER" and calls == []
        assert await _orders(conn, d["decision_id"]) == []
        f = await _findings(conn, acct, d["decision_id"])
        ab = f[PD.F_ENTER_ORDER_ABANDONED]
        assert ab["cause"] == PD.C_GRACE_EXCEEDED
        assert ab["stage_reached"] == "EXECUTION_HOOK"
        assert ab["strategy"] == EXPLORE and ab["grace_s"] == 0.3
        got = await PD.step_enter_backstop(conn, {
            "account_id": acct["account_id"], "now": time.time() + 61.0})
        assert got["examined"] == 0, "named once, by owed_order"
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_sequence_that_raises_is_named_with_its_error(explore_only,
                                                               monkeypatch):
    conn = await H.connect()
    now = time.time()

    async def broken_submit(*a, **k):
        raise RuntimeError("connection lost mid-submit")
    monkeypatch.setattr(L, "submit_order", broken_submit)
    try:
        acct, t = await _setup(conn, "eie", now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.53,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        g = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"], market_data=PL.client(t),
            account_id=acct["account_id"],
            schedule_fill=lambda: {"scheduled": False}, book_retry=False)
        assert "RuntimeError" in (
            g["paper_family"][EXPLORE].get("error") or ""), g
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d["verdict"] == "ENTER"
        ab = (await _findings(conn, acct, d["decision_id"]))[
            PD.F_ENTER_ORDER_ABANDONED]
        assert ab["cause"] == PD.C_RAISED and ab["error"] == "RuntimeError"
        assert ab["stage_reached"] == "PAPER_ORDER_SUBMITTING"
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_paper_pass_hard_timeout_no_longer_cuts_a_maker_entry(
        maker_only, monkeypatch):
    """The paper pass's own hard timeout (paper_runtime.run_once wraps the
    pass in wait_for(HARD_TIMEOUT_S)) is the other outer canceller. The
    maker's resting order is still written when the deadline falls during
    its submit."""
    conn = await H.connect()
    now = time.time() + 5.0
    dl = _Deadline()
    real = L.submit_order

    async def slow_submit(c, order, **kw):
        if order.get("strategy") == MAKER and order.get("role") == "ENTRY" \
                and dl.fired == 0:
            dl.now()
            await asyncio.sleep(0.3)
        return await real(c, order, **kw)
    monkeypatch.setattr(L, "submit_order", slow_submit)
    try:
        acct, t = await _setup(conn, "eip", now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.515,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(None) as cm:
                dl.cm = cm
                await _pass(conn, acct, t, now, client)
        assert dl.fired == 1
        d = await _dec(conn, acct, v["valuation_id"], MAKER)
        assert d["verdict"] == "ENTER", d["refusals"]
        o = await _orders(conn, d["decision_id"])
        assert len(o) == 1 and o[0]["order_type"] == "RESTING"
    finally:
        await PL.purge_everything(conn)
        await conn.close()


def test_every_paper_policy_runs_its_enter_row_and_order_through_owed_order():
    """Derek, the completed-game / strict benchmark, the maker and the
    exploration decisions: the ENTER row's INSERT is PD.owed_order's
    `record` (its first step) and the order sequence is its `sequence` -- no
    policy writes an ENTER row or submits its ENTRY outside it, and none
    signals enter_recorded itself (owed_order does, before the INSERT).
    The REFUSE branch inserts its own row (nothing is owed). Behavioural
    twin: test_every_policy_inserts_its_enter_row_inside_owed_order."""
    for fn in (PD.decide_one, PB.decide_one, PMK.decide_one,
               PEX.decide_one):
        src = inspect.getsource(fn)
        m = fn.__module__
        assert "enter_recorded(" not in src, m
        assert src.count("INSERT INTO paper_decisions") == 1, m
        assert src.index("async def insert_row(") < src.index(
            "INSERT INTO paper_decisions"), m
        refuse = src.index("if verdict != DP.ENTER:")
        assert src.count("await insert_row()") == 1, m
        assert src.index("await insert_row()") > refuse, m
        tail = src[src.index("owed_order(", refuse):]
        assert "record=insert_row" in tail, m
        assert "duplicate=lambda: dict(rec, duplicate=True)" in tail, m
        enter = src[src.index("async def sequence(progress"):]
        assert enter.index("async def sequence(progress") < enter.index(
            "L.submit_order("), m
        assert 'progress["outcome"] = "ORDER"' in enter, m
        assert 'progress["outcome"] = "ORDER_REFUSED"' in enter, m
        # what an ENTER needs after its row (capital evidence, the order)
        # is built INSIDE the owed sequence: a raise there is named
        assert enter.index("capital_evidence()" if "def capital_evidence"
                           in src else "build_order()") < enter.index(
            "L.submit_order("), m


def test_the_abandonment_code_is_classified():
    c = RT.classify(PD.F_ENTER_ORDER_ABANDONED)
    assert c["class"] == "SOFTWARE", c
    assert RT.classify(PD.F_ENTER_WITHOUT_ORDER)["class"] == "SOFTWARE"


# ═════════════════════════════════════════════════════════════════════
# 3 · owed_order ITSELF (no database: a recording connection)
# ═════════════════════════════════════════════════════════════════════

class _Conn:
    def __init__(self):
        self.writes = []

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        return "INSERT 0 1"


_CTX = {"session_id": "paper_session_t", "account_id": "paper_test_x",
        "now": 1_791_500_000.0}


def _kinds(conn):
    return [a[4] for _sql, a in conn.writes]


async def test_owed_order_completes_after_the_callers_cancellation():
    conn, done = _Conn(), []

    async def seq(progress):
        await asyncio.sleep(0.2)
        progress["outcome"] = "ORDER"
        done.append(1)
        return {"order_id": "o"}

    task = asyncio.ensure_future(PD.owed_order(
        conn, _CTX, decision_id="d1", strategy="S", sequence=seq))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert done == [1] and conn.writes == []


async def test_owed_order_names_a_grace_overrun_and_a_second_cancellation():
    conn = _Conn()

    async def wedged(progress):
        progress["stage"] = "EXECUTION_HOOK"
        await asyncio.sleep(30)

    task = asyncio.ensure_future(PD.owed_order(
        conn, _CTX, decision_id="d2", strategy="S", sequence=wedged,
        grace_s=0.1))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _kinds(conn) == [PD.F_ENTER_ORDER_ABANDONED]
    assert '"cause": "%s"' % PD.C_GRACE_EXCEEDED in conn.writes[0][1][7]

    conn2 = _Conn()
    task = asyncio.ensure_future(PD.owed_order(
        conn2, _CTX, decision_id="d3", strategy="S", sequence=wedged,
        grace_s=10.0))
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()                       # e.g. a process shutting down
    with pytest.raises(asyncio.CancelledError):
        await task
    assert '"cause": "%s"' % PD.C_CANCELLED_AGAIN in conn2.writes[0][1][7]


async def test_owed_order_names_a_raise_unless_the_outcome_was_durable():
    conn = _Conn()

    async def raises(progress):
        progress["stage"] = "PAPER_ORDER_SUBMITTING"
        raise ValueError("x")

    with pytest.raises(ValueError):
        await PD.owed_order(conn, _CTX, decision_id="d4", strategy="S",
                            sequence=raises)
    assert _kinds(conn) == [PD.F_ENTER_ORDER_ABANDONED]

    conn2 = _Conn()

    async def raises_after(progress):
        progress["outcome"] = "ORDER"
        raise ValueError("after the order")

    with pytest.raises(ValueError):
        await PD.owed_order(conn2, _CTX, decision_id="d5", strategy="S",
                            sequence=raises_after)
    assert conn2.writes == [], "the order exists: nothing to abandon"


async def test_bounded_decision_grace_overrun_is_named_once_not_graced_twice():
    """bounded_decision's own grace (EnterOrderGraceExceeded) cancels the
    decision; owed_order's grace is measured from the ENTER row, so it is
    already spent: abandoned and named at once, not waited for again."""
    conn = _Conn()

    async def decision(c):
        PD.enter_recorded(c, "d6")

        async def wedged(progress):
            await asyncio.sleep(30)
        return await PD.owed_order(conn, _CTX, decision_id="d6",
                                   strategy="S", sequence=wedged,
                                   grace_s=0.2)

    t0 = time.monotonic()
    with pytest.raises(PD.EnterOrderGraceExceeded):
        await PD.bounded_decision(decision, {}, timeout_s=0.05, grace_s=0.2)
    assert time.monotonic() - t0 < 2.0
    assert _kinds(conn) == [PD.F_ENTER_ORDER_ABANDONED]


def test_the_backstop_does_not_name_an_abandoned_enter_twice():
    src = inspect.getsource(PD.step_enter_backstop)
    assert "F_ENTER_ORDER_ABANDONED" in src


# ═════════════════════════════════════════════════════════════════════
# 4 · THE REWORK (review of 952e9cb7, both defects reproduced there)
#
#  (1) THE ENTER ROW'S OWN INSERT. It takes FOR KEY SHARE on paper_accounts
#      (the FK), which waits behind the ledger's `_lock` (SELECT ... FOR
#      UPDATE) held by any concurrent submit or pass step. A deadline that
#      fell during that wait cancelled the client while the server went on
#      and COMMITTED the row; owed_order was never entered, so only the 60 s
#      backstop named it. Now the INSERT is owed_order's first step and the
#      decision deadline stops cutting as it starts.
#  (2) A SECOND CANCELLATION made bounded_decision return while owed_order's
#      sequence still ran on the caller's connection: the caller's audit,
#      then its pool release, collided with it ('another operation is in
#      progress'). Now nothing returns while a task it started still runs
#      on the connection.
# ═════════════════════════════════════════════════════════════════════

class _AccountLock:
    """A concurrent ledger submit: a second connection takes the account row
    FOR UPDATE (bettor_paper_ledger._lock) the instant `strategy`'s
    lifecycle gate returns -- the last await before its decision INSERT --
    so the INSERT's FK check waits on it. `blocked` is set once the
    decision's backend is seen waiting on that lock in its INSERT INTO
    paper_decisions (the cut lands exactly there, nowhere else)."""

    def __init__(self, monkeypatch, strategy):
        self.strategy, self.armed, self.pid = strategy, False, None
        self.locker = self.watcher = None
        self.blocked = asyncio.Event()
        self.blocked_at = self.released_at = None
        self.held = False
        real = PD.lifecycle_gate

        async def gate(c, ctx, *, strategy, at):
            got = await real(c, ctx, strategy=strategy, at=at)
            if strategy == self.strategy and not self.armed and \
                    not got.get("refusal"):
                self.armed = True
                await self.locker.execute("BEGIN")
                await self.locker.execute(
                    "SELECT 1 FROM paper_accounts WHERE account_id=$1 "
                    "FOR UPDATE", ctx["account_id"])
                self.held = True
                self.pid = c.get_server_pid()
                asyncio.get_running_loop().create_task(self._watch())
            return got
        monkeypatch.setattr(PD, "lifecycle_gate", gate)

    async def open(self):
        self.locker = await H.connect()
        self.watcher = await H.connect()

    async def _watch(self):
        for _ in range(2000):
            if await self.watcher.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE pid=$1 AND "
                    " wait_event_type='Lock' AND query LIKE "
                    " 'INSERT INTO paper_decisions%'", self.pid):
                self.blocked_at = time.monotonic()
                self.blocked.set()
                return
            await asyncio.sleep(0.005)

    async def release(self):
        if self.held:
            self.held = False
            self.released_at = time.monotonic()
            await self.locker.execute("COMMIT")

    async def close(self):
        for c in (self.locker, self.watcher):
            if c is None:
                continue
            if c is self.locker:
                with contextlib.suppress(Exception):
                    await c.execute("ROLLBACK")
            await c.close()


@pg
async def test_a_reactive_deadline_while_the_enter_insert_waits_on_the_account_lock(
        explore_only, monkeypatch):
    """REVIEW DEFECT 1, the reviewer's reproduction (12 of 13 runs on
    952e9cb7: an ENTER row, 0 orders, 0 findings; the backstop alone named
    it 60 s later). ACTIVE exploration in cycle; its INSERT waits on the
    account row lock a concurrent ledger submit holds; the reactive deadline
    falls; the submit commits. Now the INSERT is owed: it completes, the
    order is placed, the caller's deadline still stands, nothing is left to
    the backstop."""
    conn = await H.connect()
    lock = _AccountLock(monkeypatch, EXPLORE)
    await lock.open()
    now = time.time()
    dl = _Deadline()
    before = dict(PD.OWED_ORDER_COUNTS)
    try:
        acct, t = await _setup(conn, "eiins", now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.53,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        task = asyncio.ensure_future(_hook_decision(conn, acct, v, dl,
                                                    client))
        await asyncio.wait_for(lock.blocked.wait(), 30.0)
        dl.now()                         # the reactive deadline falls ...
        await asyncio.sleep(0)           # ... and its CancelledError is
        await asyncio.sleep(0)           # delivered while the INSERT waits
        await lock.release()             # the ledger submit commits
        raised = await asyncio.wait_for(task, 60.0)
        assert isinstance(raised, TimeoutError), \
            "the caller's deadline still stands"
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d is not None and d["verdict"] == "ENTER", \
            "the owed INSERT is never cut"
        o = await _orders(conn, d["decision_id"])
        assert len(o) == 1, ("ENTER %s committed with no order (findings: "
                             "%s)" % (d["decision_id"], sorted(
                                 await _findings(conn, acct,
                                                 d["decision_id"]))))
        assert o[0]["strategy"] == EXPLORE and o[0]["role"] == "ENTRY"
        assert PD.OWED_ORDER_COUNTS["completed_after_cancellation"] == \
            before["completed_after_cancellation"] + 1
        assert client.mutation_attempts == 0
        got = await PD.step_enter_backstop(conn, {
            "account_id": acct["account_id"], "now": time.time() + 61.0})
        assert got["examined"] == 0, got
    finally:
        await lock.close()
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_decision_deadline_while_the_enter_insert_waits_no_longer_cuts_it(
        maker_only, monkeypatch):
    """REVIEW DEFECT 1, bounded_decision's OWN decision deadline: it cut the
    decision while its ENTER INSERT waited on the account lock (enter_
    recorded ran only after the INSERT). The concurrent submit commits AT
    the deadline -- the instant the cut lands -- so on 952e9cb7 the server
    commits the row the client no longer owns: an ENTER with no order. Now
    the deadline stops cutting as the INSERT starts: the resting order is
    placed and the result says it completed after the decision deadline."""
    conn = await H.connect()
    lock = _AccountLock(monkeypatch, MAKER)
    await lock.open()
    monkeypatch.setattr(PR, "VALUATION_HOOK_TIMEOUT_S", 2.0)
    real_bd = PD.bounded_decision

    async def bounded(make, ctx, *, timeout_s, grace_s=None):
        # the concurrent submit commits exactly when the decision deadline
        # falls (the decision reaches its INSERT well before it)
        loop = asyncio.get_running_loop()
        loop.call_at(loop.time() + float(timeout_s),
                     lambda: loop.create_task(lock.release()))
        return await real_bd(make, ctx, timeout_s=timeout_s,
                             grace_s=grace_s)
    monkeypatch.setattr(PD, "bounded_decision", bounded)
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "eiinsd", now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.515,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        g = await asyncio.wait_for(PR.decide_valuation(
            conn, valuation_id=v["valuation_id"], market_data=client,
            account_id=acct["account_id"],
            schedule_fill=lambda: {"scheduled": False}, book_retry=False),
            60.0)
        assert lock.blocked.is_set() and lock.released_at is not None
        assert lock.blocked_at < lock.released_at, \
            "the INSERT was waiting when the decision deadline fell"
        res = g["paper_family"][MAKER]
        d = await _dec(conn, acct, v["valuation_id"], MAKER)
        assert d is not None and d["verdict"] == "ENTER", (res, d)
        o = await _orders(conn, d["decision_id"])
        assert len(o) == 1 and o[0]["order_type"] == "RESTING", res
        assert res["order_id"] == o[0]["order_id"]
        late = res["order_after_decision_deadline"]
        assert late["decision_id"] == d["decision_id"]
        assert late["decision_deadline_s"] == 2.0
        got = await PD.step_enter_backstop(conn, {
            "account_id": acct["account_id"], "now": time.time() + 61.0})
        assert got["examined"] == 0, got
    finally:
        await lock.release()
        await lock.close()
        await PL.purge_everything(conn)
        await conn.close()


@pytest.fixture(params=["DEREK", CG, MAKER, EXPLORE])
def enter_policy(request, env):
    """One policy's entries on, the others off (set outside the event
    loop: set_policy_control runs its own)."""
    if request.param == "DEREK":
        _only()
    else:
        _only(request.param)
    return request.param


@pg
async def test_every_policy_inserts_its_enter_row_inside_owed_order(
        enter_policy, monkeypatch):
    """BEHAVIOURAL (all four policies that write ENTER rows): when
    PD.owed_order is entered for an ENTER, its row does NOT exist yet and
    owed_order holds the INSERT (`record`); afterwards the row exists with
    its order or its named order refusal. On 952e9cb7 every policy wrote the
    row before owed_order -- outside the protected region."""
    conn = await H.connect()
    now = time.time() + 5.0
    seen: list = []
    real = PD.owed_order

    async def spy(c, ctx, *, decision_id, strategy, sequence, record=None,
                  **kw):
        seen.append({"strategy": strategy, "decision_id": decision_id,
                     "record": record is not None,
                     "row_before": await c.fetchval(
                         "SELECT count(*) FROM paper_decisions WHERE "
                         " decision_id=$1", decision_id)})
        return await real(c, ctx, decision_id=decision_id,
                          strategy=strategy, sequence=sequence,
                          record=record, **kw)
    monkeypatch.setattr(PD, "owed_order", spy)
    prev = None
    policy = enter_policy
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        kw = {"compatibility": "INCOMPATIBLE"}
        offers, bids = [(0.50, 5000)], [(0.48, 5000)]
        if policy == "DEREK":
            prev = await PL.two_model_entries(conn, True)
            await PL.train_model(conn, monkeypatch,
                                 model_id="derek-research-model-ei-rework")
            p_pin, kw = 0.62, {}
            offers = [(0.40, 3000), (0.42, 3000), (0.60, 5000)]
            bids = [(0.38, 5000)]
        else:
            p_pin = {CG: 0.62, MAKER: 0.515, EXPLORE: 0.53}[policy]
        acct = await PL.new_account(conn, "eiown", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=p_pin, **kw)
        t.set(v["slug"], offers=offers, bids=bids)
        t.t = max(t.t, now)
        p = await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                                market_data=PL.client(t),
                                config=acct["config"], force=True,
                                fee_fn=H.flat_fee(0.01), sleep=_nosleep)
        assert p["ran"] and not p["errors"], p["errors"]
        strat = PD.STRATEGY if policy == "DEREK" else policy
        mine = [s for s in seen if s["strategy"] == strat]
        assert mine, ("no ENTER reached owed_order for %s (seen: %s)"
                      % (strat, seen))
        for s in seen:
            assert s["record"] is True, s
            assert s["row_before"] == 0, ("the ENTER row was written "
                                          "before owed_order: %s" % s)
        for s in mine:
            d = await conn.fetchrow(
                "SELECT verdict FROM paper_decisions WHERE decision_id=$1",
                s["decision_id"])
            assert d is not None and d["verdict"] == "ENTER", s
            o = await _orders(conn, s["decision_id"])
            f = await _findings(conn, acct, s["decision_id"])
            assert o or PB.R_ORDER_REFUSED in f, (s, sorted(f))
    finally:
        if prev is not None:
            await PL.restore_two_model_entries(conn, prev)
        await PL.purge_research_models(conn)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_second_cancellation_in_the_hook_path_names_the_enter_and_frees_the_connection(
        explore_only, monkeypatch):
    """REVIEW DEFECT 2, end to end. The reactive deadline falls while the
    exploration ENTER's intent hook runs (owed_order waits out its grace),
    then the job is stopped (Scheduler.stop on shutdown or a lost lease).
    On 952e9cb7 bounded_decision returned at the second cancellation while
    the sequence still ran on the job's connection. Now the stop is
    forwarded: the sequence is abandoned and the ENTER named AT ONCE
    (CANCELLED_AGAIN), and only then does the caller resume -- on a
    connection nothing else is using."""
    conn = await H.connect()
    now = time.time()
    dl, calls = _Deadline(), []
    monkeypatch.setattr(DH, "DECISION_HOOK", _slow_hook(dl, calls,
                                                        sleep_s=3.0))
    try:
        acct, t = await _setup(conn, "eidc", now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.53,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        task = asyncio.ensure_future(_hook_decision(conn, acct, v, dl,
                                                    PL.client(t)))
        for _ in range(3000):
            if dl.fired:
                break
            await asyncio.sleep(0.005)
        assert dl.fired == 1
        await asyncio.sleep(0.2)         # owed_order waits out its grace
        t0 = time.monotonic()
        task.cancel()                    # the job is stopped
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - t0 < 2.0, "abandoned, not waited out"
        # THE CALLER'S CONNECTION IS FREE the instant it resumes
        assert await conn.fetchval("SELECT 1") == 1
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d["verdict"] == "ENTER" and calls == []
        assert await _orders(conn, d["decision_id"]) == []
        ab = (await _findings(conn, acct, d["decision_id"])).get(
            PD.F_ENTER_ORDER_ABANDONED)
        assert ab is not None, "named at once, before the caller resumed"
        assert ab["cause"] == PD.C_CANCELLED_AGAIN
        assert ab["stage_reached"] == "EXECUTION_HOOK"
        got = await PD.step_enter_backstop(conn, {
            "account_id": acct["account_id"], "now": time.time() + 61.0})
        assert got["examined"] == 0, got
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_second_cancellation_never_hands_a_pooled_connection_back_in_use():
    """REVIEW DEFECT 2, the reviewer's real-pool reproduction: a job holds
    the pool's only connection, runs bounded_decision under its 0.2 s
    deadline, the decision's owed sequence is in a 1.5 s statement, and the
    job is cancelled again at 0.5 s. On 952e9cb7: the job's audit failed
    with InterfaceError ('another operation is in progress'), the pool
    terminated the connection mid-sequence, the abandonment was not written.
    Now the job resumes only after the sequence ended and the ENTER was
    named; its audit runs; the same backend goes back to the pool."""
    import asyncpg
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=1)
    conn0 = await H.connect()
    now = time.time()
    events: list = []
    before = dict(PD.OWED_ORDER_COUNTS)
    try:
        acct = await PL.new_account(conn0, "eipool", now=now)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": now}
        did = "paperexp:double-cancel-%s" % acct["account_id"][-10:]
        pids: dict = {}

        async def job():
            async with pool.acquire() as conn:
                pids["job"] = conn.get_server_pid()

                async def decision(c):
                    async def seq(progress):
                        progress["stage"] = "PAPER_ORDER_SUBMITTING"
                        try:
                            await conn.execute("SELECT pg_sleep(1.5)")
                            progress["outcome"] = "ORDER"
                            return {"order_id": "o1"}
                        finally:
                            events.append("SEQUENCE_ENDED")
                    return await PD.owed_order(conn, c, decision_id=did,
                                               strategy=EXPLORE,
                                               sequence=seq)
                try:
                    async with asyncio.timeout(0.2):      # the deadline
                        await PD.bounded_decision(decision, ctx,
                                                  timeout_s=8.0)
                except asyncio.CancelledError:
                    events.append("JOB_CANCELLED")
                    raise
                finally:
                    # _complete_audit(self, attempt, conn)
                    await conn.execute("SELECT 1")
                    events.append("AUDIT_OK")

        task = asyncio.ensure_future(job())
        await asyncio.sleep(0.5)          # inside owed_order's grace
        task.cancel()                     # Scheduler.stop()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ["SEQUENCE_ENDED", "JOB_CANCELLED", "AUDIT_OK"]
        async with pool.acquire(timeout=2.0) as c2:
            assert c2.get_server_pid() == pids["job"], \
                "the same backend went back to the pool, intact"
            assert await c2.fetchval("SELECT 1") == 1
        f = await _findings(conn0, acct, did)
        ab = f[PD.F_ENTER_ORDER_ABANDONED]
        assert ab["cause"] == PD.C_CANCELLED_AGAIN
        assert ab["stage_reached"] == "PAPER_ORDER_SUBMITTING"
        assert PD.OWED_ORDER_COUNTS["abandoned_unrecorded"] == \
            before["abandoned_unrecorded"]
        assert PD.OWED_ORDER_COUNTS["connections_terminated"] == \
            before["connections_terminated"]
    finally:
        await pool.close()
        await PL.purge_everything(conn0)
        await conn0.close()


#: how long the recording connection's statements take (default 0.01 s)
_SLOW_SQL = {"SLOW": 1.5, "SLOW_INSERT": 0.3, "WEDGED_INSERT": 30.0}


class _ExclusiveConn:
    """asyncpg's rule, recorded: ONE operation at a time on a connection (a
    second one while the first runs is an overlap, raised as asyncpg raises
    it); a terminated connection refuses everything."""

    def __init__(self, *, existing=None):
        self.busy, self.overlaps, self.terminated = False, 0, False
        self.writes: list = []
        self.existing = existing

    async def _op(self, sql):
        if self.terminated:
            raise ConnectionError("the connection was terminated")
        if self.busy:
            self.overlaps += 1
            raise RuntimeError("cannot perform operation: another operation "
                               "is in progress")
        self.busy = True
        try:
            await asyncio.sleep(_SLOW_SQL.get(sql, 0.01))
        finally:
            self.busy = False

    async def execute(self, sql, *args):
        await self._op(sql)
        self.writes.append((sql, args))
        return "OK"

    async def fetchval(self, sql, *args):
        await self._op(sql)
        return self.existing

    def terminate(self):
        self.terminated = True

    def findings(self):
        return [a for sql, a in self.writes
                if "paper_audrey_findings" in sql]


async def _seq_never(progress):
    raise AssertionError("the sequence never runs")


async def test_owed_order_runs_the_enter_insert_as_its_first_step():
    """The ENTER is owed BEFORE its INSERT starts (the event bounded_
    decision watches is set first); a caller's cancellation while the
    INSERT is in flight lets it finish and the sequence run; a duplicate is
    an outcome with nothing owed."""
    conn = _ExclusiveConn()
    ev = asyncio.Event()
    ctx = dict(_CTX, **{PD.CTX_ENTER_RECORDED: ev})
    log: list = []

    async def record():
        log.append(("record", ev.is_set()))
        await conn.execute("SLOW_INSERT")
        return "d7"

    async def seq(progress):
        log.append(("sequence", progress["stage"], progress["enter_row"]))
        progress["outcome"] = "ORDER"
        return {"order_id": "o7"}

    task = asyncio.ensure_future(PD.owed_order(
        conn, ctx, decision_id="d7", strategy="S", record=record,
        sequence=seq))
    await asyncio.sleep(0.05)            # the INSERT is in flight
    task.cancel()                        # the caller's deadline
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log == [("record", True), ("sequence", "ENTER_RECORDED", True)]
    assert conn.findings() == [] and conn.overlaps == 0

    async def dup():
        return None
    got = await PD.owed_order(conn, dict(_CTX), decision_id="d8",
                              strategy="S", record=dup,
                              duplicate=lambda: {"duplicate": True, "x": 8},
                              sequence=_seq_never)
    assert got == {"duplicate": True, "x": 8}


async def test_an_enter_insert_cut_past_the_grace_is_named_only_if_its_row_committed():
    """The INSERT itself outruns the grace and is cancelled. On the same
    connection the row's fate is then definite: committed -> the ENTER is
    named (stage ENTER_ROW_INSERTING); never committed -> nothing is owed,
    nothing is named, it is counted."""
    for existing, named in ((None, False), ("ENTER", True)):
        conn = _ExclusiveConn(existing=existing)
        before = dict(PD.OWED_ORDER_COUNTS)

        async def record(conn=conn):
            await conn.execute("WEDGED_INSERT")
            return "d9"
        task = asyncio.ensure_future(PD.owed_order(
            conn, dict(_CTX), decision_id="d9", strategy="S", record=record,
            sequence=_seq_never, grace_s=0.1))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        f = conn.findings()
        if named:
            assert [a[4] for a in f] == [PD.F_ENTER_ORDER_ABANDONED]
            assert '"stage_reached": "%s"' % PD.ST_ENTER_ROW_INSERTING \
                in f[0][7]
            assert PD.OWED_ORDER_COUNTS["abandoned"] == \
                before["abandoned"] + 1
        else:
            assert f == []
            assert PD.OWED_ORDER_COUNTS["cut_before_the_enter_row"] == \
                before["cut_before_the_enter_row"] + 1
            assert PD.OWED_ORDER_COUNTS["abandoned"] == before["abandoned"]
        assert conn.overlaps == 0


async def test_bounded_decision_never_returns_while_the_owed_sequence_runs():
    """REVIEW DEFECT 2, no database: cancelled at its deadline, then again
    while owed_order waits out its grace. bounded_decision resumes its
    caller only after the sequence ended and the ENTER was named
    (CANCELLED_AGAIN); the caller's next statement never overlaps."""
    conn = _ExclusiveConn()
    order: list = []

    async def decision(c):
        async def seq(progress):
            progress["stage"] = "PAPER_ORDER_SUBMITTING"
            try:
                await conn.execute("SLOW")
                progress["outcome"] = "ORDER"
                return {"order_id": "o"}
            finally:
                order.append("SEQUENCE_ENDED")
        return await PD.owed_order(conn, c, decision_id="dd1", strategy="S",
                                   sequence=seq)

    async def job():
        try:
            async with asyncio.timeout(0.1):
                await PD.bounded_decision(decision, dict(_CTX),
                                          timeout_s=8.0)
        finally:
            order.append("CALLER_RESUMED")
            await conn.execute("AUDIT")
    task = asyncio.ensure_future(job())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert order == ["SEQUENCE_ENDED", "CALLER_RESUMED"]
    assert conn.overlaps == 0
    f = conn.findings()
    assert [a[4] for a in f] == [PD.F_ENTER_ORDER_ABANDONED]
    assert '"cause": "%s"' % PD.C_CANCELLED_AGAIN in f[0][7]
    assert conn.writes[-1][0] == "AUDIT"


async def test_a_sequence_that_outlives_its_cancellation_gets_its_connection_terminated(
        monkeypatch):
    """A sequence that does not end when cancelled is never left running on
    a connection someone else will use: after ENTER_ABANDON_WAIT_S the
    connection is terminated (the backstop names the ENTER), and owed_order
    resumes its caller -- bounded, its further cancellations absorbed."""
    monkeypatch.setattr(PD, "ENTER_ABANDON_WAIT_S", 0.1)
    conn = _ExclusiveConn()
    before = dict(PD.OWED_ORDER_COUNTS)

    async def stubborn(progress):
        progress["stage"] = "EXECUTION_HOOK"
        end = time.monotonic() + 3.0       # bounded: a base run fails, it
        while not conn.terminated and \
                time.monotonic() < end:    # does not hang
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                continue                   # ignores its cancellation
        raise ConnectionError("the connection was terminated")

    monkeypatch.setattr(PD, "ENTER_TERMINATED_WAIT_S", 0.05)
    monkeypatch.setattr(PD, "ENTER_ORDER_GRACE_S", 0.05)
    task = asyncio.ensure_future(PD.owed_order(
        conn, dict(_CTX), decision_id="dt1", strategy="S", sequence=stubborn))
    await asyncio.sleep(0.02)
    t_cancel = time.monotonic()
    task.cancel()
    await asyncio.sleep(0.1)
    task.cancel()                          # absorbed, not escaped
    with pytest.raises(asyncio.CancelledError):
        await task
    # held past the caller's cancellation by no more than the published
    # bound (every wait spent in full here except the record's)
    assert time.monotonic() - t_cancel <= \
        PD.owed_enter_overrun_bound_s() + 0.5
    assert conn.terminated is True
    assert conn.findings() == []
    assert PD.OWED_ORDER_COUNTS["connections_terminated"] == \
        before["connections_terminated"] + 1
    assert PD.OWED_ORDER_COUNTS["abandoned_unrecorded"] == \
        before["abandoned_unrecorded"] + 1


def test_the_owed_enter_overrun_bound_is_every_budget_spent():
    """The published bound on how long an owed ENTER can hold its caller
    past the caller's cancellation: grace 15 + the sequence's settle (5 + 1)
    + the abandonment record's (5 + 5 + 1) = 32 s with the defaults."""
    assert PD.owed_enter_overrun_bound_s() == 32.0
    assert PD.owed_enter_overrun_bound_s() == (
        PD.ENTER_ORDER_GRACE_S + 3 * PD.ENTER_ABANDON_WAIT_S
        + 2 * PD.ENTER_TERMINATED_WAIT_S)


async def test_before_the_enter_is_owed_a_repeat_cancellation_waits_for_the_decision():
    """Before its ENTER is owed, a decision cancelled twice is waited for --
    its own cleanup (a savepoint's rollback) runs to the end and is never
    cut by the second cancellation -- and CancelledError is re-raised once
    it is done. On 952e9cb7 the second cancellation returned at once."""
    state: list = []

    async def decision(c):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            state.append("cancelled")
            await asyncio.sleep(0.2)       # its cleanup
            state.append("cleanup done")
            raise

    t = asyncio.ensure_future(PD.bounded_decision(decision, {},
                                                  timeout_s=5.0))
    await asyncio.sleep(0.05)
    t.cancel()
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert state == ["cancelled", "cleanup done"]
