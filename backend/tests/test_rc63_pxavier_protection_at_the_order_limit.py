"""SW-1b, REVIEW ROUND 3: THE ENTRY IS ASKED AT THE ORDER'S LIMIT, NOT ONLY
AT ITS DECISION WALK.

4440cb02 made every paper entry ask paper_xavier.protective_price, but on
the DECISION-TIME walk. A MARKETABLE order is filled by the simulator
(bettor_paper_simulator._marketable) on the first readable book at or after
decision + delay, walking THAT book up to the order's limit. When the
cheaper levels the decision walked are gone, the whole order is booked at
the limit. The reviewer reproduced it through two real paper passes on
4440cb02: COMPLETED_GAME at p 0.999 against [(0.97, 4800), (0.99, 6000)]
walked 4800 @ 0.97 + 150 @ 0.99 (protectable, cost 4814.31), entered with
limit 0.99, and filled 4950 @ 0.99 (cost 4903.91) once the book was
[(0.99, 6000)]: NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR, the PKE failure.

Every paper entry now runs paper_explore.xavier_can_protect_entry: the
decision walk AND the order's whole quantity booked at its limit in one
fill, the highest price the simulator books any fill of it at. Refused by
the same classified name, with no paper order. Nothing here reads or moves
a price, edge, fee, size or risk threshold; the protective price rule is
unchanged.

What the check does not cover is pinned here too: under the DEPLOYED fee
schedule a partial, split or multi-fill booking at or below a limit that
passes is protectable as well (section 3, over the whole cent grid); under
another fee function (a minimum fee per fill) it is not, and the test says
so rather than claiming it.

The pass tests run against a scratch database (RN1X_TEST_DSN) with
SYNTHETIC valuations and books (paper_live_fixture). There is no venue
order: the market-data client counts mutation attempts and each proof
asserts zero.
"""
from __future__ import annotations

import ast
import pathlib
import time

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_rc6_pxavier_every_entry_asks_protection as SW1B

# the SW-1b proofs' fixtures and helpers (the same scratch-account setup)
env, cg_only, strict_only = SW1B.env, SW1B.cg_only, SW1B.strict_only
_pass, _dec, _orders, _cond = SW1B._pass, SW1B._dec, SW1B._orders, SW1B._cond
_derek_ctx, CG = SW1B._derek_ctx, SW1B.CG

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = 1_791_500_000.0                         # 2026-10-08, deployed schedule
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
R = PEX.R_XAVIER_CANNOT_PROTECT
NO_PRICE = "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
WALK, LIMIT = "AT_THE_DECISION_WALK", "AT_THE_ORDER_LIMIT"


# ═════════════════════════════════════════════════════════════════════
# 1 · THE CHECK, PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_reviewers_case_walk_protectable_limit_not_is_refused():
    """4800 @ 0.97 + 150 @ 0.99 is protectable; the same 4950 booked at the
    0.99 limit is not. The entry check refuses it and names the binding
    check; the walk-only check of 4440cb02 said yes."""
    fills = [(0.97, 4800), (0.99, 6000)]
    walk_only = PEX.xavier_can_protect_fills(fills=fills, qty=4950,
                                             limit=0.99, fee_fn=None, at=AT)
    assert walk_only["protectable"] is True        # what 4440cb02 asked
    got = PEX.xavier_can_protect_entry(fills=fills, qty=4950, limit=0.99,
                                       fee_fn=None, at=AT)
    assert got["protectable"] is False
    assert got["binding_check"] == LIMIT and got["refusal"] == NO_PRICE
    assert got["protective_price"] is None and got["limit"] == 0.99
    w, lim = got["checks"][WALK], got["checks"][LIMIT]
    assert w["protectable"] is True and w["protective_price"] == 0.99
    assert w["qty"] == lim["qty"] == got["qty"] == 4950
    assert w["acquisition_cost_usd"] == pytest.approx(4800 * 0.97
                                                      + 150 * 0.99)
    assert lim["acquisition_cost_usd"] == pytest.approx(4950 * 0.99)
    assert lim["fees_usd"] == pytest.approx(float(L._fee(None, 4950, 0.99,
                                                         AT)))
    # the reviewer's figures: booked at the limit the position costs
    # 4903.91 (4900.50 + 3.41), which no cent <= 0.99 recovers with the
    # sale fee and the buffer
    assert lim["cost_basis_usd"] == pytest.approx(4903.91)
    assert PX.protective_price(qty=4950, cost_basis=4903.91, fee_fn=None,
                               at=AT)["ok"] is False
    assert got["cost_basis_usd"] == lim["cost_basis_usd"]


def test_the_reviewers_pure_case_5147_contracts():
    """The reviewer's pure version: 5147 walked as 4800 @ 0.97 + 347 @
    0.99 is protectable at a cost of 5009.48; at the 0.99 limit (cost
    5099.07) it is not."""
    fills = [(0.97, 4800), (0.99, 6000)]
    got = PEX.xavier_can_protect_entry(fills=fills, qty=5147, limit=0.99,
                                       fee_fn=None, at=AT)
    assert got["checks"][WALK]["protectable"] is True
    assert got["checks"][WALK]["cost_basis_usd"] == pytest.approx(5009.48)
    assert got["checks"][LIMIT]["protectable"] is False
    assert got["checks"][LIMIT]["cost_basis_usd"] == pytest.approx(5099.07)
    assert got["protectable"] is False and got["refusal"] == NO_PRICE


def test_both_protectable_reports_the_order_limits_worst_booking():
    """Both protectable: entered, and the figures recorded are the order
    limit's (the most the order can cost), not the cheaper walk's."""
    fills = [(0.95, 100), (0.97, 1000)]
    got = PEX.xavier_can_protect_entry(fills=fills, qty=600, limit=0.97,
                                       fee_fn=None, at=AT)
    assert got["protectable"] is True and got["binding_check"] == LIMIT
    assert got["cost_basis_usd"] == got["checks"][LIMIT]["cost_basis_usd"]
    assert got["cost_basis_usd"] > got["checks"][WALK]["cost_basis_usd"]
    assert got["protective_price"] == 0.99


def _dear_below_090(qty, price, at=None):
    """A stand-in fee DEARER at lower prices (0.20 per contract below 0.90,
    nothing at or above): the walk can then be worse than the limit, so the
    walk is still asked."""
    return ((0.20 * float(qty)) if float(price) < 0.90 else 0.0, "TEST")


def test_the_decision_walk_is_still_asked_when_the_limit_passes():
    got = PEX.xavier_can_protect_entry(fills=[(0.85, 100)], qty=100,
                                       limit=0.95, fee_fn=_dear_below_090,
                                       at=AT)
    assert got["checks"][LIMIT]["protectable"] is True
    assert got["checks"][WALK]["protectable"] is False
    assert got["protectable"] is False
    assert got["binding_check"] == WALK and got["refusal"] == NO_PRICE


def test_a_resting_order_asks_the_same_question_once():
    """The maker's resting order is booked at its own limit: its walk IS the
    whole quantity at the limit, so the two checks agree."""
    for lim, ok in ((0.97, True), (0.98, False)):
        got = PEX.xavier_can_protect_entry(fills=[(lim, 350)], qty=350,
                                           limit=lim, fee_fn=None, at=AT)
        assert got["protectable"] is ok
        assert got["checks"][WALK]["cost_basis_usd"] == \
            got["checks"][LIMIT]["cost_basis_usd"]


def _calls(module: str, fn: str = "decide_one") -> set:
    tree = ast.parse((ROOT / "agents" / module).read_text())
    f = next(n for n in tree.body if isinstance(
        n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == fn)
    out = set()
    for n in ast.walk(f):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                out.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                out.add(n.func.attr)
    return out


def test_every_paper_entry_decision_asks_at_the_order_limit():
    """Every paper entry decide_one reaches xavier_can_protect_entry (the
    benchmark's serves strict and COMPLETED_GAME; the benchmark and Derek
    through their _xavier_protect wrapper), and none calls a walk-only check
    directly. On 4440cb02 the maker called xavier_can_protect_fills, the
    others the walk only."""
    walk_only = {"xavier_can_protect_fills", "xavier_can_protect"}
    for module in ("paper_benchmark.py", "paper_derek.py"):
        calls = _calls(module)
        assert "_xavier_protect" in calls, module
        assert not calls & walk_only, (module, calls & walk_only)
        assert _calls(module, "_xavier_protect") >= {
            "xavier_can_protect_entry"}, module
        assert not _calls(module, "_xavier_protect") & walk_only, module
    for module in ("paper_maker.py", "paper_explore.py"):
        calls = _calls(module)
        assert "xavier_can_protect_entry" in calls, module
        assert not calls & walk_only, (module, calls & walk_only)


# ═════════════════════════════════════════════════════════════════════
# 2 · THROUGH THE REAL PAPER PASS (and Derek's real decision)
# ═════════════════════════════════════════════════════════════════════

async def _fills_for(conn, slug) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM paper_fills WHERE us_market_slug=$1", slug)


@pg
async def test_completed_game_refuses_a_walk_it_could_protect_at_a_limit_it_cannot(  # noqa
        cg_only):
    """THE REVIEWER'S PROBE, as a regression. COMPLETED_GAME at p 0.999
    against [(0.97, 4800), (0.99, 6000)]: the decision walks 4800 @ 0.97 +
    150 @ 0.99 (protectable) with the order's limit at 0.99. On 4440cb02 it
    was an ENTER with a paper order, and when the book became [(0.99,
    6000)] the order filled 4950 @ 0.99, unprotectable. Now it is REFUSED by
    name at the decision, no paper order, nothing booked on the later book.
    The same probability against [(0.96, 4800), (0.97, 6000)] (limit 0.97)
    is protectable at its limit and enters."""
    conn = await H.connect()
    try:
        now = time.time() + 5.0
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "r3lim", now=now)
        t = PL.Transport(now - 6.0)
        client = PL.client(t)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.999,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.97, 4800), (0.99, 6000)],
              bids=[(0.95, 2000)])
        ok_v = await PL.valuation(conn, decided_at=now - 9, p_pin=0.999,
                                  compatibility="INCOMPATIBLE")
        t.set(ok_v["slug"], offers=[(0.96, 4800), (0.97, 6000)],
              bids=[(0.94, 2000)])
        out = await _pass(conn, acct, t, now, client)
        assert out["ran"] and not out["errors"], out["errors"]
        d = await _dec(conn, acct, v["valuation_id"], CG)
        assert d is not None
        econ = H.j(d["economics"])["acquisition"]
        assert [(w["price"], w["take"]) for w in econ["walk"]] == [
            (0.97, 4800.0), (0.99, 150.0)]
        assert float(d["limit_price"]) == 0.99
        assert econ["net_ev_positive"] is True
        assert d["verdict"] == "REFUSE", (d["refusal"], list(d["refusals"]))
        assert list(d["refusals"]) == [R]
        assert await _orders(conn, d["decision_id"]) == 0
        prot = econ["xavier_protection"]
        assert prot["protectable"] is False
        assert prot["binding_check"] == LIMIT and prot["refusal"] == NO_PRICE
        assert prot["checks"][WALK]["protectable"] is True
        assert prot["checks"][LIMIT]["protectable"] is False
        assert prot["checks"][LIMIT]["qty"] == 4950
        assert prot["checks"][LIMIT]["cost_basis_usd"] == pytest.approx(
            4903.91)
        c = _cond(d)["xavier_can_place_the_standing_protection"]
        assert c["passed"] is False and c["refusal"] == R
        assert H.j(d["economics"])["capital_eligibility"] is None
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_entry_refusal_census WHERE "
            " decision_id=$1 AND refusal=$2", d["decision_id"], R) == 1
        # the control: protectable at its 0.97 limit, entered
        ok = await _dec(conn, acct, ok_v["valuation_id"], CG)
        assert ok["verdict"] == "ENTER", (ok["refusal"], list(ok["refusals"]))
        assert float(ok["limit_price"]) == 0.97
        assert await _orders(conn, ok["decision_id"]) == 1
        okp = H.j(ok["economics"])["acquisition"]["xavier_protection"]
        assert okp["protectable"] is True and okp["binding_check"] == LIMIT
        assert okp["checks"][LIMIT]["protective_price"] == 0.99
        # the later book that filled 4440cb02's order at the limit: nothing
        # is booked on it now, because there is no order
        t.set(v["slug"], offers=[(0.99, 6000)], bids=[(0.95, 2000)])
        t.t = now + 3.0
        out2 = await _pass(conn, acct, t, now + 5.0, client)
        assert not out2["errors"], out2["errors"]
        assert await _fills_for(conn, v["slug"]) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1 AND "
            " us_market_slug=$2", acct["account_id"], v["slug"]) == 0
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_strict_benchmark_asks_at_its_limit(strict_only):
    """The strict benchmark (same decide_one) with a fee of 0.025 per
    contract, p 0.999 against [(0.90, 2000), (0.94, 6000)]: the decision
    walks 2000 @ 0.90 and the rest @ 0.94, protectable on average; the whole
    order at its 0.94 limit is not (0.94 + 0.025 + 0.01 > 0.99 - 0.025).
    On 4440cb02 it entered. Now REFUSED by name, no paper order."""
    conn = await H.connect()
    try:
        now = time.time() + 5.0
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "r3strict", now=now)
        t = PL.Transport(now)
        client = PL.client(t)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.999,
                               compatibility="COMPATIBLE")
        t.set(v["slug"], offers=[(0.90, 2000), (0.94, 6000)],
              bids=[(0.88, 2000)])
        out = await _pass(conn, acct, t, now, client,
                          fee_fn=H.flat_fee(0.025))
        assert out["ran"] and not out["errors"], out["errors"]
        d = await _dec(conn, acct, v["valuation_id"], PB.STRATEGY)
        assert d is not None
        econ = H.j(d["economics"])["acquisition"]
        assert float(d["limit_price"]) == 0.94
        assert econ["walk"][0]["price"] == 0.90
        assert econ["net_ev_positive"] is True
        assert d["verdict"] == "REFUSE", (d["refusal"], list(d["refusals"]))
        assert list(d["refusals"]) == [R]
        assert await _orders(conn, d["decision_id"]) == 0
        prot = econ["xavier_protection"]
        assert prot["checks"][WALK]["protectable"] is True
        assert prot["checks"][LIMIT]["protectable"] is False
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_derek_asks_at_its_limit(monkeypatch):
    """Derek V2 through its real decision and writer (research model
    injected at its seam, scoring 0.999), fee 0.025 per contract, against
    [(0.90, 2000), (0.94, 6000)]: the walk is protectable, the whole order
    at its 0.94 limit is not. On 4440cb02 it entered; now REFUSED by name,
    no paper order."""
    monkeypatch.setattr(PD, "score", lambda model, **kw: {
        "ok": True, "p": 0.999, "features": {}, "feature_basis": "test"})
    fee = H.flat_fee(0.025)
    conn = await H.connect()
    unset = object()
    prev = unset
    try:
        now = time.time() + 3.0
        await PL.purge_everything(conn)
        prev = await PL.two_model_entries(conn, True)
        acct = await PL.new_account(conn, "r3derek", now=now)
        v = await PL.valuation(conn, decided_at=now - 5, p_pin=0.999)
        t = PL.Transport(now)
        t.set(v["slug"], offers=[(0.90, 2000), (0.94, 6000)],
              bids=[(0.88, 2000)])
        client = PL.client(t)
        t.t = max(t.t, now)
        row = await conn.fetchrow("SELECT * FROM external_valuations "
                                  " WHERE id=$1", v["valuation_id"])
        rec = await PD.decide_one(conn, _derek_ctx(acct, client, now, fee),
                                  dict(row))
        d = await conn.fetchrow("SELECT * FROM paper_decisions WHERE "
                                " decision_id=$1", rec["decision_id"])
        pdx = H.j(d["policy_decision"])
        assert pdx["admitted"] is True                # V2 alone admits it
        assert float(d["limit_price"]) == 0.94
        assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
        assert list(d["refusals"]) == [R]
        assert await _orders(conn, d["decision_id"]) == 0
        prot = pdx["xavier_protection"]
        assert prot["checks"][WALK]["protectable"] is True
        assert prot["checks"][LIMIT]["protectable"] is False
        assert prot["binding_check"] == LIMIT
        assert client.mutation_attempts == 0
    finally:
        if prev is not unset:
            await PL.restore_two_model_entries(conn, prev)
        await PL.purge_everything(conn)
        await conn.close()


def _small_buys_pay(qty, price, at=None):
    """A stand-in fee: 0.04 per contract on a buy fill under 100 contracts
    below 0.96, nothing otherwise (so a sale at 0.99 is free). The walked
    2010 are protectable at the walk and at the limit; ten of them are
    protectable at the walk's cheapest level (0.90) and not at the 0.95
    limit."""
    q, p = float(qty), float(price)
    return ((0.04 * q) if (q < 100 and p < 0.96) else 0.0, "TEST_SMALL_BUY")


@pg
async def test_the_size_the_capital_gate_leaves_is_asked_at_the_limit(
        cg_only, monkeypatch):
    """The capital gate may shrink the entry. 4440cb02 asked the bound size
    again on the first contracts of the walk (10 @ 0.90: protectable); the
    simulator can book all ten at the 0.95 limit, where they are not. Now
    REFUSED by name with no paper order."""
    real_gate = PD.capital_gate

    async def gate(conn, ctx, **kw):
        got = await real_gate(conn, ctx, **kw)
        assert got.get("capital_eligible"), got.get("refusals")
        return dict(got, qty=10)

    monkeypatch.setattr(PD, "capital_gate", gate)
    conn = await H.connect()
    try:
        now = time.time() + 5.0
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "r3bound", now=now)
        t = PL.Transport(now)
        client = PL.client(t)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.995,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.90, 10), (0.95, 2000)],
              bids=[(0.88, 2000)])
        out = await _pass(conn, acct, t, now, client,
                          fee_fn=_small_buys_pay)
        assert not out["errors"], out["errors"]
        d = await _dec(conn, acct, v["valuation_id"], CG)
        econ = H.j(d["economics"])["acquisition"]
        assert econ["xavier_protection"]["protectable"] is True
        bound = econ["xavier_protection_at_bound_qty"]
        assert bound["qty"] == 10
        assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
        assert list(d["refusals"]) == [R]
        assert await _orders(conn, d["decision_id"]) == 0
        assert bound["checks"][WALK]["protectable"] is True
        assert bound["checks"][LIMIT]["protectable"] is False
        assert bound["protectable"] is False
        c = _cond(d)["xavier_can_place_the_standing_protection"]
        assert c["passed"] is False and c["refusal"] == R
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · WHAT THE CHECK COVERS, AND WHAT IT DOES NOT
# ═════════════════════════════════════════════════════════════════════

_FEES: dict = {}


def _fee(q, p) -> float:
    k = (round(float(q), 6), round(float(p), 2))
    if k not in _FEES:
        _FEES[k] = float(L._fee(None, k[0], k[1], AT))
    return _FEES[k]


def _protectable(fills) -> bool:
    """A booking [(price, qty), ...] (each a fill paying its own buy fee),
    asked exactly as Xavier's review asks it: paper_xavier.protective_price
    on the position's quantity and cost basis. The sale at 0.99 is tried
    first only to save time; protective_price decides when it fails."""
    q = sum(x for _, x in fills)
    cost = sum(px * x + _fee(x, px) for px, x in fills)
    need = cost + PX.PROTECTION_BUFFER_USD_PER_CONTRACT * q
    if q * 0.99 - _fee(q, 0.99) >= need - 1e-9:
        return True
    return bool(PX.protective_price(qty=q, cost_basis=cost, fee_fn=None,
                                    at=AT)["ok"])


def _at_limit(qty, lim) -> bool:
    return PEX.xavier_can_protect_entry(fills=[(lim, qty)], qty=qty,
                                        limit=lim, fee_fn=None,
                                        at=AT)["protectable"]


CENTS = [c / 100.0 for c in range(1, 100)]
WHOLE = list(range(1, 41)) + [100, 350, 1000, 4950]


def test_the_deployed_schedule_passes_exactly_these_limits():
    """Under the deployed schedule (0.0695 x C x p x (1 - p), cent-rounded
    per fill) the whole quantity at the limit is protectable for every
    limit up to 0.97, at 0.98 only while no fee rounds up (1 to 3
    contracts), and never at 0.99."""
    for lim in CENTS:
        for qty in WHOLE:
            want = lim <= 0.97 + 1e-9 or (abs(lim - 0.98) < 1e-9
                                          and qty <= 3)
            assert _at_limit(qty, lim) is want, (lim, qty)


SIZES = [0.25, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 3.7, 4, 7, 7.3, 8, 15, 36.5,
         100, 350, 4800]


def test_under_the_deployed_schedule_every_booking_of_a_passing_order_is_protectable():  # noqa
    """What the simulator can book from an order that passed: a part of its
    quantity, at the limit or split with any cheaper cent, or (resting) the
    quantity in many fills at the limit, each fill's fee rounded on its own.
    For every limit that passes (above), every such booking of the sizes
    tried is protectable: one fill at the limit, two fills (the limit and
    every cheaper cent), and k equal fills at the limit."""
    checked = 0
    for lim in CENTS:
        if lim > 0.98 + 1e-9:
            continue
        cap = 3 if abs(lim - 0.98) < 1e-9 else None   # the passing orders
        sizes = [s for s in SIZES if cap is None or s <= cap]
        for s in sizes:
            assert _protectable([(lim, s)]), (lim, s)
            checked += 1
        for px in CENTS:
            if px >= lim - 1e-9:
                break
            for a in sizes:
                for b in sizes:
                    if cap is not None and a + b > cap + 1e-9:
                        continue
                    assert _protectable([(px, a), (lim, b)]), (px, a, lim, b)
                    checked += 1
        for s in sizes:
            for k in (2, 3, 5, 10, 25, 60):
                if cap is not None and k * s > cap + 1e-9:
                    continue
                assert _protectable([(lim, s)] * k), (lim, s, k)
                checked += 1
    assert checked > 100_000


def test_under_another_fee_function_a_partial_booking_is_not_covered():
    """STATED, NOT CLAIMED: with a 0.50 minimum fee per fill the whole 2000
    at the 0.95 limit passes, and a partial fill of ten would not be
    protectable. The entry check does not ask every partial quantity; the
    guarantee above is the deployed schedule's."""
    def min_fee(qty, price, at=None):
        return (max(0.50, 0.001 * float(qty)), "TEST_MINIMUM_FEE")
    full = PEX.xavier_can_protect_entry(fills=[(0.95, 2000)], qty=2000,
                                        limit=0.95, fee_fn=min_fee, at=AT)
    part = PEX.xavier_can_protect_entry(fills=[(0.95, 2000)], qty=10,
                                        limit=0.95, fee_fn=min_fee, at=AT)
    assert full["protectable"] is True and part["protectable"] is False
    assert "deployed fee schedule" in full["basis"]
