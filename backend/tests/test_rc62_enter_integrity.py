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


def test_every_paper_policy_runs_its_order_sequence_through_owed_order():
    """Derek, the completed-game / strict benchmark, the maker and the
    exploration decisions: after enter_recorded, the order sequence is
    PD.owed_order's -- no policy submits its ENTRY outside it."""
    for fn in (PD.decide_one, PB.decide_one, PMK.decide_one,
               PEX.decide_one):
        src = inspect.getsource(fn)
        i = src.index("enter_recorded(ctx, did)")
        tail = src[i:]
        assert "owed_order(" in tail, fn.__module__
        assert "async def sequence(progress" in tail, fn.__module__
        assert tail.index("async def sequence(progress") < tail.index(
            "L.submit_order("), fn.__module__
        assert 'progress["outcome"] = "ORDER"' in tail, fn.__module__
        assert 'progress["outcome"] = "ORDER_REFUSED"' in tail, fn.__module__


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
