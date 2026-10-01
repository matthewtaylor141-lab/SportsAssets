"""THE OWNER'S 0.5 pp PAPER ENTRY THRESHOLD (PINNACLE_COMPLETED_GAME_PAPER_V2,
migration 188), THROUGH THE REAL PAPER PASS.

    p_pinnacle - simulated acquisition price >= 0.005 at every level used
    AND conditional expected profit after fees strictly > 0

Half a probability point -- not 5 points, not a 0.5 % return. V2 also sizes
net of fees: a level whose per-contract fee consumes its edge is never
bought, and a candidate that clears 0.5 pp gross but not its fees is refused
by name. Every decision records the policy version and the threshold it ran.

The fee cases use the DEPLOYED fee schedule (bettor_paper_ledger.default_
fee_fn): about 1.74 pp per contract at $0.50 and 0.63 pp at $0.10.

SYNTHETIC valuations and books (paper_live_fixture). No real money, no venue
order: the market-data client counts mutation attempts and each proof
asserts zero.
"""
from __future__ import annotations

import time

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CG = PB.CG_STRATEGY
ZERO_FEE = H.flat_fee(0.0)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RULE, PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_shipped_rule_is_half_a_probability_point():
    assert PB.CG_VERSION == "PINNACLE_COMPLETED_GAME_PAPER_V2"
    assert PB.CG_PARAMETERS_V2 == {"min_gross_edge_pp": 0.5}
    assert PB.CG_PARAMETER_BOUNDS["min_gross_edge_pp"][0] == 0.5
    d = PB.describe()["completed_game"]
    assert d["shipped_min_edge_probability"] == 0.005
    assert d["previous_versions"] == {PB.CG_VERSION_V1: {"min_edge_pp": 5.0}}


@pytest.mark.parametrize("p,price,clears", [
    (0.5049, 0.50, False),        # just below
    (0.505, 0.50, True),          # exactly at: 50.5 % against $0.50
    (0.5051, 0.50, True),         # just above
    (0.106, 0.10, True),
    (0.104, 0.10, False),
    (0.995, 0.99, True),
])
def test_the_gross_boundary_is_an_absolute_probability_difference(
        p, price, clears):
    lv = [{"price": price, "wire": "%.2f" % price, "qty": 100}]
    got = PB.level_edges(lv, p, min_edge=0.005)[0]
    assert got["clears_min_edge"] is clears
    assert got["min_edge_pp"] == 0.5
    # NOT a return target: 0.5 % of a $0.50 price would be 0.25 pp
    assert DP.clears(DP.gross_edge(0.5025, 0.50), 0.005) is False


def test_sizing_never_buys_a_level_whose_fee_consumes_its_edge():
    at = time.time()
    fee = lambda px: PB.fee_per_contract(None, px, at)       # noqa: E731
    assert fee(0.50) == pytest.approx(0.017375, abs=1e-6)
    assert fee(0.10) == pytest.approx(0.006255, abs=1e-6)
    kw = dict(consumed={}, target_usd=1000.0, cap_usd=1000.0,
              fee_per_contract_max=0.0175, min_edge=0.005)
    # clears 0.5 pp gross at $0.50, but the fee is 1.74 pp
    s = PB.size_within_edge([{"price": 0.50, "wire": "0.50", "qty": 500}],
                            p=0.51, net_fee_fn=fee, **kw)
    assert s["qty"] == 0
    assert s["why"] == "FEES_CONSUME_THE_EDGE_AT_EVERY_CLEARING_LEVEL"
    assert s["fee_stop"]["net_edge_pp"] < 0
    # 1 pp at $0.10 against a 0.63 pp fee: bought
    s = PB.size_within_edge([{"price": 0.10, "wire": "0.10", "qty": 500}],
                            p=0.11, net_fee_fn=fee, **kw)
    assert s["qty"] > 0 and s["limit"] == 0.10
    # a deeper level that clears gross but not its fee is not walked into
    s = PB.size_within_edge(
        [{"price": 0.10, "wire": "0.10", "qty": 50},
         {"price": 0.104, "wire": "0.104", "qty": 500}],
        p=0.11, net_fee_fn=fee, **kw)
    assert s["levels_used"] == 1 and s["levels_clearing_gross"] == 2
    assert s["limit"] == 0.10
    # the strict policy (no net_fee_fn) is unchanged
    s = PB.size_within_edge([{"price": 0.50, "wire": "0.50", "qty": 500}],
                            p=0.51, **kw)
    assert s["qty"] > 0


# ═════════════════════════════════════════════════════════════════════
# 2 · THROUGH THE PAPER PASS: BELOW / AT / ABOVE, FEES, LEDGER
# ═════════════════════════════════════════════════════════════════════

async def _nosleep(_):
    return None


@pytest.fixture
def cg_on(monkeypatch):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


async def _pass(conn, acct, transport, now, *, fee_fn, client):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=fee_fn, sleep=_nosleep)


async def _run(conn, cases, *, fee_fn, label):
    """One account, one pass, one valuation per case (p, offer price)."""
    now = time.time() + 5.0
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    acct = await PL.new_account(conn, label, now=now)
    t = PL.Transport(now)
    vids = {}
    for name, (p, px) in cases.items():
        # the recorded venue wording (ordinary grading period stated)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=p,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(px, 2000)], bids=[(round(px - 0.02, 4),
                                                     2000)])
        vids[name] = v["valuation_id"]
    client = PL.client(t)
    out = await _pass(conn, acct, t, now, fee_fn=fee_fn, client=client)
    assert out["ran"] and not out["errors"], out["errors"]
    assert client.mutation_attempts == 0
    dec = {}
    for name, vid in vids.items():
        dec[name] = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"], vid, CG)
        assert dec[name] is not None, name
    return acct, t, client, now, dec


def _recorded(d):
    pdx = H.j(d["policy_decision"])
    econ = H.j(d["economics"])
    assert d["policy_version"] == PB.CG_VERSION
    assert pdx["policy_version"] == PB.CG_VERSION
    assert pdx["threshold_edge_pp"] == 0.5
    assert pdx["threshold_edge_probability"] == 0.005
    assert econ["threshold_edge_pp"] == 0.5
    assert pdx["parameters"]["version_id"] == PB.CG_V2_VERSION_ID
    assert pdx["parameters"]["values"] == {"min_gross_edge_pp": 0.5}
    return pdx, econ


@pg
async def test_below_at_and_above_with_the_deployed_fee_schedule(cg_on):
    conn = await H.connect()
    try:
        acct, t, client, now, dec = await _run(conn, {
            "below_mid": (0.5049, 0.50),
            "at_mid": (0.505, 0.50),          # clears gross, fee 1.74 pp
            "above_mid": (0.515, 0.50),       # 1.5 pp < 1.74 pp fee
            "above_low_fee_short": (0.106, 0.10),   # 0.6 pp < 0.63 pp
            "above_low_fee_clear": (0.11, 0.10),    # 1.0 pp > 0.63 pp
        }, fee_fn=None, label="v2fees")
        for name, d in dec.items():
            _recorded(d)
        assert dec["below_mid"]["refusal"] == PB.R_EDGE
        for name in ("at_mid", "above_mid", "above_low_fee_short"):
            d = dec[name]
            assert d["verdict"] == "REFUSE", name
            assert d["refusal"] == PB.R_FEES_CONSUME_EDGE, (name, d["refusal"])
            pdx, econ = _recorded(d)
            cond = {c["condition"]: c for c in pdx["conditions"]}
            assert cond["edge_at_least_min_gross_edge_pp_at_every_level_"
                        "used"]["passed"] is True
            assert cond["positive_ev_after_fees"]["passed"] is False
            assert econ["fee_stop"]["fee_per_contract_usd"] > 0
            assert d["book_obs_id"] is not None, "the book was read"
        ok = dec["above_low_fee_clear"]
        assert ok["verdict"] == "ENTER", (ok["refusal"], ok["refusals"])
        pdx, econ = _recorded(ok)
        acq = econ["acquisition"]
        assert acq["net_ev_positive"] is True
        assert acq["expected_net_profit_usd"] > 0
        assert acq["expected_net_profit_usd"] == pytest.approx(
            acq["expected_gross_profit_usd"] - acq["fees_usd"], abs=1e-6)

        # ONLY THE QUALIFYING CANDIDATE HAS AN ORDER
        orders = await conn.fetch("SELECT decision_id FROM paper_orders "
                                  " WHERE session_id=$1 AND role='ENTRY'",
                                  acct["session_id"])
        assert [o["decision_id"] for o in orders] == [ok["decision_id"]]

        # THE FILL, THE EXACT CASH DEBIT, ONE FUNDING ENTRY, THE HANDOFF
        p2 = await _pass(conn, acct, t, now + 5, fee_fn=None, client=client)
        assert not p2["errors"], p2["errors"]
        assert client.mutation_attempts == 0
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", ok["decision_id"])
        assert o["state"] == "FILLED"
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1", o["order_id"])
        assert fills and {f["strategy"] for f in fills} == {CG}
        cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        fees = sum(float(f["fee_usd"]) for f in fills)
        assert fees > 0, "the deployed schedule charged its fee"
        b = await L.balances(conn, acct["account_id"], now=now + 6)
        assert b["ledger_consistent"] is True
        assert b["cash_usd"] == pytest.approx(500000.0 - cost - fees,
                                              abs=1e-6)
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
            " kind='INITIAL_FUNDING'", acct["account_id"]) == 1
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert h is not None and h["strategy"] == CG
        assert float(h["confirmed_qty"]) == sum(float(f["qty"])
                                                for f in fills)
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_exactly_at_the_threshold_enters_when_fees_allow(cg_on):
    """With a zero-fee function the gross rule decides alone: 0.5049 vs
    $0.50 is refused, 0.505 vs $0.50 (exactly 0.5 pp) enters, and its
    conditional EV is strictly positive."""
    conn = await H.connect()
    try:
        acct, t, client, now, dec = await _run(conn, {
            "below": (0.5049, 0.50), "at": (0.505, 0.50),
            "above": (0.52, 0.50)}, fee_fn=ZERO_FEE, label="v2zero")
        assert dec["below"]["refusal"] == PB.R_EDGE
        for name in ("at", "above"):
            d = dec[name]
            assert d["verdict"] == "ENTER", (name, d["refusals"])
            _, econ = _recorded(d)
            assert econ["acquisition"]["expected_net_profit_usd"] > 0
        assert H.j(dec["at"]["economics"])["best_level_edge_pp"] == \
            pytest.approx(0.5)
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_trade_whose_fees_equal_its_edge_is_refused(cg_on):
    """Strictly positive: a flat fee of exactly the edge leaves zero."""
    conn = await H.connect()
    try:
        acct, t, client, now, dec = await _run(conn, {
            "zero_net": (0.51, 0.50)}, fee_fn=H.flat_fee(0.01),
            label="v2flat")
        d = dec["zero_net"]
        assert d["verdict"] == "REFUSE"
        assert d["refusal"] == PB.R_FEES_CONSUME_EDGE
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE session_id=$1",
            acct["session_id"]) == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_active_parameter_version_is_the_owner_decision():
    conn = await H.connect()
    try:
        got = await PB.cg_parameters(conn, {"now": time.time()})
        assert got["source"] == PB.P_ACTIVE, got
        assert got["version_id"] == PB.CG_V2_VERSION_ID
        assert got["values"] == {"min_gross_edge_pp": 0.5}
        v1 = await conn.fetchrow(
            "SELECT params, source FROM paper_policy_parameter_versions "
            " WHERE version_id=$1", PB.CG_V1_VERSION_ID)
        assert H.j(v1["params"]) == {"min_gross_edge_pp": 5.0}
        act = await conn.fetchrow(
            "SELECT kind, previous_version_id FROM "
            " paper_policy_parameter_activations WHERE version_id=$1",
            PB.CG_V2_VERSION_ID)
        assert act["kind"] == "OWNER_DECISION"
        assert act["previous_version_id"] == PB.CG_V1_VERSION_ID
        # THE DATABASE REFUSES ANYTHING BELOW THE 0.5 pp FLOOR
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO paper_policy_parameter_versions (version_id, "
                " policy_key, version_no, params, params_sha256, source, "
                " approved_by, created_at) VALUES ('paperparam:x:low', "
                " 'PINNACLE_COMPLETED_GAME_PAPER', 99, "
                " '{\"min_gross_edge_pp\": 0.0}'::jsonb, 'x', "
                " 'OWNER_DECISION', 'test', now())")
    finally:
        await conn.close()


async def test_a_failed_parameter_read_never_runs_below_half_a_point():
    class Broken:
        async def fetchval(self, *a):
            raise RuntimeError("down")
    got = await PB.cg_parameters(Broken(), {"now": 1.0})
    assert got["source"] == PB.P_FALLBACK
    assert got["values"] == {"min_gross_edge_pp": 0.5}
