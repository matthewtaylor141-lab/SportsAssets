"""SW-1b: EVERY PAPER ENTRY STRATEGY ASKS WHETHER XAVIER CAN PROTECT IT.

The production member's incomplete Xavier packet had two causes. One was
NO_VALID_ACTIVE_PROTECTION: paper_xavier.protective_price searches only
1..99 cents, and a position bought at 0.98 or 0.99 has no cent at which
selling it recovers its cost, its sale fees and the 0.01 per contract
buffer. Exploration has refused such an entry since RC6
(paper_explore.R_XAVIER_CANNOT_PROTECT). The other paper entry strategies
did not:

  COMPLETED_GAME V3   an edge of 0.5 pp and a positive net EV, no upper
                      price bound. p 0.995 against a 0.98 ask clears both.
                      Enabled in production.
  COMPLETED_GAME MAKER rests one cent below the ask: a 0.99 ask rests 0.98.
  strict benchmark    5 pp edge. Under the deployed fee schedule its entries
  Derek V2            are protectable. With a fee of 0.025 per contract they
                      are not (0.94 + 0.025 + 0.01 > 0.99 - 0.025).

Each now runs paper_explore.xavier_can_protect_entry on the quantity it
would enter (rc6.3 review round 3: the decision walk AND the whole quantity
at the order's limit, the price the simulator can book every fill at; see
tests/test_rc63_pxavier_protection_at_the_order_limit.py) and refuses
R_XAVIER_CANNOT_PROTECT by name, with no paper order. The benchmark and Derek ask again at the size the capital gate
leaves, because fees are rounded to the cent per fill. Nothing here reads or
moves a price, edge, fee, size or risk threshold, and the protective price
rule is unchanged.

The decisions run against a scratch database (RN1X_TEST_DSN) with
SYNTHETIC valuations and books (paper_live_fixture). There is no venue
order: the market-data client counts mutation attempts and each proof
asserts zero.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import time

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_maker as PMK
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = 1_791_500_000.0                         # 2026-10-08, deployed schedule
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
CG, MAKER, EXPLORE = PB.CG_STRATEGY, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY
R = PEX.R_XAVIER_CANNOT_PROTECT


# ═════════════════════════════════════════════════════════════════════
# 1 · THE CHECK, PURE
# ═════════════════════════════════════════════════════════════════════

def test_350_at_098_has_no_protective_price_and_097_does():
    """The reviewer's arithmetic on the deployed schedule: 350 @ 0.98 is
    343.00 + 0.48 fees = 343.48; the protection needs 343.48 + 3.50 =
    346.98; selling 350 at 0.99 returns 346.50 - 0.24 = 346.26."""
    got = PEX.xavier_can_protect_fills(fills=[(0.98, 350)], qty=350,
                                       limit=0.98, fee_fn=None, at=AT)
    assert got["protectable"] is False
    assert got["refusal"] == "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
    assert got["acquisition_cost_usd"] == pytest.approx(343.0)
    assert got["fees_usd"] == pytest.approx(0.48)
    assert got["cost_basis_usd"] == pytest.approx(343.48)
    sell = 350 * 0.99 - float(L._fee(None, 350, 0.99, AT))
    assert sell == pytest.approx(346.26)
    assert sell < 343.48 + 350 * PX.PROTECTION_BUFFER_USD_PER_CONTRACT
    ok = PEX.xavier_can_protect_fills(fills=[(0.97, 350)], qty=350,
                                      limit=0.97, fee_fn=None, at=AT)
    assert ok["protectable"] is True and ok["protective_price"] == 0.99


def test_the_check_is_the_explorations_check_on_the_same_cost_basis():
    """The first `qty` contracts of the walk, each fill with its own buy
    fee, handed to the unchanged xavier_can_protect (and so to
    paper_xavier.protective_price)."""
    fills = [(0.90, 100), (0.95, 100), (0.97, 500)]
    got = PEX.xavier_can_protect_fills(fills=fills, qty=150, limit=0.97,
                                       fee_fn=None, at=AT)
    fees = float(L._fee(None, 100, 0.90, AT)) + float(
        L._fee(None, 50, 0.95, AT))
    ref = PEX.xavier_can_protect(
        econ={"qty": 150, "acquisition_cost_usd": 90.0 + 47.5,
              "fees_usd": fees}, fee_fn=None, at=AT)
    assert got["qty"] == 150
    assert got["acquisition_cost_usd"] == pytest.approx(137.5)
    assert got["fees_usd"] == pytest.approx(fees)
    for k in ("protectable", "cost_basis_usd", "protective_price",
              "refusal", "rule"):
        assert got[k] == ref[k], k
    # a quantity beyond the walk is costed at the order's limit
    beyond = PEX.xavier_can_protect_fills(fills=[(0.90, 10)], qty=30,
                                          limit=0.95, fee_fn=None, at=AT)
    assert beyond["acquisition_cost_usd"] == pytest.approx(9.0 + 20 * 0.95)


def test_protectability_depends_on_the_quantity_entered():
    """Fees are rounded to the cent per fill, so the same price can be
    protectable at one size and not at another: 3 @ 0.98 pays no fee at
    either end and is protected at 0.99; 350 @ 0.98 is not. That is why
    the benchmark and Derek ask again at the size the capital gate leaves."""
    small = PEX.xavier_can_protect_fills(fills=[(0.98, 350)], qty=3,
                                         limit=0.98, fee_fn=None, at=AT)
    assert small["protectable"] is True and small["protective_price"] == 0.99
    big = PEX.xavier_can_protect_fills(fills=[(0.98, 350)], qty=350,
                                       limit=0.98, fee_fn=None, at=AT)
    assert big["protectable"] is False


def test_every_strategy_refuses_under_the_one_classified_name():
    assert PB.R_XAVIER_CANNOT_PROTECT == PD.R_XAVIER_CANNOT_PROTECT == R
    assert R == "XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE"
    assert RT.lookup(R) == ("ECONOMIC", "PRICE", "RISK_ADMISSION")


def _called(fn_name: str, path: pathlib.Path) -> list:
    """The `async def decide_one` call sites of `fn_name` (by name or
    attribute) in a module."""
    tree = ast.parse(path.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "decide_one")
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == fn_name) or
        (isinstance(n.func, ast.Attribute) and n.func.attr == fn_name))]


def test_every_paper_entry_decision_runs_the_check():
    """The four paper entry decisions besides exploration call it (the
    benchmark's decide_one serves both the strict and COMPLETED_GAME
    policies). (rc6.3 review round 3) The maker and exploration now call
    the one entry check, xavier_can_protect_entry (decision walk AND whole
    quantity at the order's limit), that the benchmark's and Derek's
    _xavier_protect wrap; tests/test_rc63_pxavier_protection_at_the_order_
    limit.py pins that no decide_one calls a walk-only check directly."""
    agents = ROOT / "agents"
    assert len(_called("_xavier_protect", agents / "paper_benchmark.py")) == 2
    assert len(_called("_xavier_protect", agents / "paper_derek.py")) == 2
    assert len(_called("xavier_can_protect_entry",
                       agents / "paper_maker.py")) == 1
    assert len(_called("xavier_can_protect_entry",
                       agents / "paper_explore.py")) == 1


def test_calling_the_check_from_the_benchmark_loads_no_forbidden_module():
    """The benchmark imports paper_explore at call time. Calling the check
    loads no funded, venue, order or execution module (with an injected fee
    function: the deployed schedule's own module is the fee path every paper
    fill already uses)."""
    code = (
        "import sys\n"
        "import sportsassets.agents.paper_benchmark as PB\n"
        "from tests import paper_harness as H\n"
        "PB._xavier_protect(fills=[(0.98, 10)], qty=10, limit=0.98,\n"
        "                   fee_fn=H.flat_fee(0.01), at=%r)\n"
        "print('\\n'.join(sorted(m for m in sys.modules\n"
        "                        if m.startswith('sportsassets'))))\n" % AT)
    got = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(ROOT.parent), check=True)
    loaded = got.stdout.split()
    assert "sportsassets.agents.paper_xavier" in loaded
    forbidden = ("funded", "entry_execution", "entry_inventory", "live",
                 "adapter", "submission", "order_router", "polymarket",
                 "ext_pinnacle_loop", "workers", "risk_engine",
                 "market_stream")
    bad = [m for m in loaded if any(f in m for f in forbidden)]
    assert not bad, bad


def test_paper_explore_module_imports_are_the_benchmarks_allowed_ones():
    """paper_explore's module-level imports are each already allowed for the
    benchmark (tests/test_pinnacle_only_paper_benchmark.ALLOWED_IMPORTS) or
    are the benchmark itself, so importing it from the benchmark reaches
    nothing new at import time."""
    from tests import test_pinnacle_only_paper_benchmark as TB
    tree = ast.parse((ROOT / "agents" / "paper_explore.py").read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[-1])
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    names.discard("")
    assert names <= TB.ALLOWED_IMPORTS | {"paper_benchmark", "Any"}, \
        sorted(names - TB.ALLOWED_IMPORTS)


# ═════════════════════════════════════════════════════════════════════
# 2 · THROUGH THE REAL PAPER PASS (and Derek's real decision)
# ═════════════════════════════════════════════════════════════════════

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
    # the migrated launch selection: CG and exploration on, the maker and
    # the strict benchmark off
    _only(CG, EXPLORE)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()


@pytest.fixture
def cg_only(env):
    _only(CG)


@pytest.fixture
def maker_only(env):
    _only(MAKER)


@pytest.fixture
def strict_only(env):
    _only(PB.CONTROL_KEY)


async def _pass(conn, acct, t, now, client, fee_fn=None):
    t.t = max(t.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=fee_fn, sleep=_nosleep)


async def _dec(conn, acct, vid, strategy):
    return await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
        " valuation_id=$2 AND strategy=$3", acct["session_id"], vid, strategy)


async def _orders(conn, did):
    return await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE decision_id=$1", did)


async def _two_books(conn, tag, *, p, refused_ask, entered_ask, fee_fn,
                     strategy, compatibility):
    """One pass over two valuations of the same probability: one whose ask
    leaves no protective price, one whose ask does. Returns both decisions
    and the client."""
    now = time.time() + 5.0
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    acct = await PL.new_account(conn, tag, now=now)
    t = PL.Transport(now)
    client = PL.client(t)
    v1 = await PL.valuation(conn, decided_at=now - 10, p_pin=p,
                            compatibility=compatibility)
    t.set(v1["slug"], offers=[(refused_ask, 2000)],
          bids=[(round(refused_ask - 0.02, 2), 2000)])
    v2 = await PL.valuation(conn, decided_at=now - 9, p_pin=p,
                            compatibility=compatibility)
    t.set(v2["slug"], offers=[(entered_ask, 2000)],
          bids=[(round(entered_ask - 0.02, 2), 2000)])
    out = await _pass(conn, acct, t, now, client, fee_fn=fee_fn)
    assert out["ran"] and not out["errors"], out["errors"]
    d1 = await _dec(conn, acct, v1["valuation_id"], strategy)
    d2 = await _dec(conn, acct, v2["valuation_id"], strategy)
    assert d1 is not None and d2 is not None
    return acct, client, d1, d2


def _cond(d):
    return {c["condition"]: c for c in
            H.j(d["policy_decision"])["conditions"]}


@pg
async def test_completed_game_refuses_an_entry_xavier_cannot_protect(
        cg_only):
    """COMPLETED_GAME V3 on the deployed fee schedule: p 0.995 against a
    0.98 ask (1.5 pp gross, positive net EV) was an ENTER and a paper order
    on b3f1b0cd. It is now REFUSED by name with no order. The same
    probability against a 0.97 ask is protectable and enters."""
    conn = await H.connect()
    try:
        acct, client, d, ok = await _two_books(
            conn, "sw1bcg", p=0.995, refused_ask=0.98, entered_ask=0.97,
            fee_fn=None, strategy=CG, compatibility="INCOMPATIBLE")
        assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
        assert list(d["refusals"]) == [R]
        assert await _orders(conn, d["decision_id"]) == 0
        econ = H.j(d["economics"])
        assert econ["acquisition"]["net_ev_positive"] is True
        prot = econ["acquisition"]["xavier_protection"]
        assert prot["protectable"] is False
        assert prot["refusal"] == "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
        assert prot["qty"] == econ["acquisition"]["qty"]
        c = _cond(d)["xavier_can_place_the_standing_protection"]
        assert c["passed"] is False and c["refusal"] == R
        assert _cond(d)["positive_ev_after_fees"]["passed"] is True
        # the capital gate never ran on it: no shadow, no capital record
        assert H.j(d["economics"])["capital_eligibility"] is None
        assert ok["verdict"] == "ENTER", (ok["refusal"], ok["refusals"])
        assert await _orders(conn, ok["decision_id"]) == 1
        okc = _cond(ok)["xavier_can_place_the_standing_protection"]
        assert okc["passed"] is True and okc["value"] == 0.99
        assert client.mutation_attempts == 0
        # the refusal is in the entry-refusal census under its own name
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_entry_refusal_census WHERE "
            " decision_id=$1 AND refusal=$2", d["decision_id"], R) == 1
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_maker_refuses_a_resting_bid_xavier_cannot_protect(
        maker_only):
    """The maker rests one cent below the ask: a 0.99 ask rests 0.98, which
    no protective price can cover once filled. It placed a resting paper
    order on b3f1b0cd; it is now REFUSED by name. A 0.98 ask rests 0.97,
    which is protectable, and rests."""
    conn = await H.connect()
    try:
        acct, client, d, ok = await _two_books(
            conn, "sw1bmk", p=0.999, refused_ask=0.99, entered_ask=0.98,
            fee_fn=None, strategy=MAKER, compatibility="INCOMPATIBLE")
        assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
        assert list(d["refusals"]) == [R]
        assert H.j(d["policy_decision"])["resting_price"] == 0.98
        assert await _orders(conn, d["decision_id"]) == 0
        prot = H.j(d["economics"])["acquisition_if_filled"][
            "xavier_protection"]
        assert prot["protectable"] is False
        c = _cond(d)["xavier_can_place_the_standing_protection"]
        assert c["passed"] is False and c["refusal"] == R
        assert ok["verdict"] == "ENTER", (ok["refusal"], ok["refusals"])
        assert H.j(ok["policy_decision"])["resting_price"] == 0.97
        assert await _orders(conn, ok["decision_id"]) == 1
        assert _cond(ok)["xavier_can_place_the_standing_protection"][
            "value"] == 0.99
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_strict_benchmark_refuses_an_entry_xavier_cannot_protect(
        strict_only):
    """The strict benchmark's 5 pp edge keeps it below 0.95, where the
    deployed schedule leaves a protective price. With a fee of 0.025 per
    contract, p 0.999 against a 0.94 ask (5.9 pp gross, positive net EV)
    has none: 0.94 + 0.025 + 0.01 > 0.99 - 0.025. It is refused by name; a
    0.90 ask is protectable and enters. The same decide_one serves
    COMPLETED_GAME."""
    conn = await H.connect()
    try:
        acct, client, d, ok = await _two_books(
            conn, "sw1bst", p=0.999, refused_ask=0.94, entered_ask=0.90,
            fee_fn=H.flat_fee(0.025), strategy=PB.STRATEGY,
            compatibility="COMPATIBLE")
        assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
        assert list(d["refusals"]) == [R]
        assert await _orders(conn, d["decision_id"]) == 0
        assert H.j(d["economics"])["acquisition"][
            "net_ev_positive"] is True
        assert ok["verdict"] == "ENTER", (ok["refusal"], ok["refusals"])
        assert await _orders(conn, ok["decision_id"]) == 1
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


def _derek_ctx(acct, client, now, fee_fn):
    ctx = {"session": {"session_id": acct["session_id"],
                       "config": acct["config"]},
           "session_id": acct["session_id"], "account_id": acct["account_id"],
           "config": acct["config"], "market_data": client, "books_read": 0,
           "now": now, "deadline": time.monotonic() + 30, "first_fills": [],
           "fills": 0, "fee_fn": fee_fn,
           # THE RESEARCH MODEL, injected at its seam (`_context` serves
           # ctx["derek"] as is), as tests/test_gross_edge_inputs_are_
           # validated.py does: this proof is about the entry's protection
           "derek": {"model": {"ok": True, "refusal": None,
                               "model_id": "test-model", "features": [],
                               "model_version": "v-test",
                               "approval_status": "CANDIDATE"},
                     "model_attempt": None,
                     "void": {"status": "UNMEASURED"}, "calibration": {}}}
    ctx["clock"] = lambda: ctx["now"]
    return ctx


@pg
async def test_derek_refuses_an_entry_xavier_cannot_protect(monkeypatch):
    """Derek V2 through its real decision and writer, with its research
    model injected at the seam and scoring 0.999. With a fee of 0.025 per
    contract a 0.94 ask (5.9 pp blended, positive net EV) has no protective
    price and is refused by name; a 0.90 ask is protectable and is not."""
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
        out = {}
        for tag, ask in (("refused", 0.94), ("entered", 0.90)):
            acct = await PL.new_account(conn, "sw1bdk" + tag, now=now)
            v = await PL.valuation(conn, decided_at=now - 5, p_pin=0.999)
            t = PL.Transport(now)
            t.set(v["slug"], offers=[(ask, 2000)],
                  bids=[(round(ask - 0.02, 2), 2000)])
            client = PL.client(t)
            t.t = max(t.t, now)
            row = await conn.fetchrow("SELECT * FROM external_valuations "
                                      " WHERE id=$1", v["valuation_id"])
            rec = await PD.decide_one(conn, _derek_ctx(acct, client, now,
                                                       fee), dict(row))
            out[tag] = (await conn.fetchrow(
                "SELECT * FROM paper_decisions WHERE decision_id=$1",
                rec["decision_id"]), client)
        d, c1 = out["refused"]
        assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
        assert list(d["refusals"]) == [R]
        assert await _orders(conn, d["decision_id"]) == 0
        pdx = H.j(d["policy_decision"])
        assert pdx["admitted"] is True                # V2 alone admits it
        assert pdx["xavier_protection"]["protectable"] is False
        assert H.j(d["economics"])["xavier_protection"]["refusal"] == \
            "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
        ok, c2 = out["entered"]
        assert R not in list(ok["refusals"]), ok["refusals"]
        assert H.j(ok["policy_decision"])["xavier_protection"][
            "protectable"] is True
        assert ok["verdict"] == "ENTER", (ok["refusal"], ok["refusals"])
        assert await _orders(conn, ok["decision_id"]) == 1
        assert c1.mutation_attempts == 0 and c2.mutation_attempts == 0
    finally:
        if prev is not unset:
            await PL.restore_two_model_entries(conn, prev)
        await PL.purge_everything(conn)
        await conn.close()


def _min_fee(qty, price, at=None):
    """A stand-in fee with a 0.50 minimum per fill: negligible on a large
    fill, decisive on a small one."""
    return (max(0.50, 0.001 * float(qty)), "TEST_MINIMUM_FEE")


def test_a_minimum_fee_makes_a_small_entry_unprotectable():
    big = PEX.xavier_can_protect_fills(fills=[(0.95, 2000)], qty=2000,
                                       limit=0.95, fee_fn=_min_fee, at=AT)
    small = PEX.xavier_can_protect_fills(fills=[(0.95, 2000)], qty=10,
                                         limit=0.95, fee_fn=_min_fee, at=AT)
    assert big["protectable"] is True and small["protectable"] is False


@pg
async def test_the_size_the_capital_gate_leaves_is_asked_again(
        cg_only, monkeypatch):
    """The capital gate may shrink the entry (lifecycle, profitability
    bind). The benchmark asks the protection again at the size it leaves.
    With a 0.50 minimum fee per fill, the walked 2000 @ 0.95 are protectable
    and 10 of them are not: a gate that leaves 10 is refused by name, with
    no paper order. On b3f1b0cd the 10 were entered."""
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
        acct = await PL.new_account(conn, "sw1bbound", now=now)
        t = PL.Transport(now)
        client = PL.client(t)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.995,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.95, 2000)], bids=[(0.93, 2000)])
        out = await _pass(conn, acct, t, now, client, fee_fn=_min_fee)
        assert not out["errors"], out["errors"]
        d = await _dec(conn, acct, v["valuation_id"], CG)
        econ = H.j(d["economics"])["acquisition"]
        assert econ["xavier_protection"]["protectable"] is True
        assert econ["xavier_protection"]["qty"] == econ["qty"]
        bound = econ["xavier_protection_at_bound_qty"]
        assert bound["qty"] == 10 and bound["protectable"] is False
        assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
        assert list(d["refusals"]) == [R]
        c = _cond(d)["xavier_can_place_the_standing_protection"]
        assert c["passed"] is False and c["refusal"] == R
        assert await _orders(conn, d["decision_id"]) == 0
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()
