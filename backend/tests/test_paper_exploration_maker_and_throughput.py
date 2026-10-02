"""MIGRATION 189, THROUGH THE REAL PAPER PASS AND HOOK.

  EXPLORATION  the bounded training strategy enters a candidate that FAILS
               the investment rule (0.5 pp gross, after-fee EV negative),
               records its estimate, selection probability and purpose, is
               filled by the simulator, debited on the one ledger, handed to
               Xavier and audited by Audrey; every owner limit refuses by
               name (per-position budget incl. fees, aggregate exposure, one
               per fixture, no overlap with another strategy, loss stop, the
               sample); the data safeguards still refuse (stale Pinnacle).
  MAKER        the resting price is the highest cent below the ask that
               clears the threshold AND the taker fee; the order rests (not a
               fill), reserves cash, never fills on a touch, fills only on a
               strict cross after the queue ahead, is charged the taker fee,
               and is cancelled when a newer reading removes the edge.
  THROUGHPUT   a read this process made seconds earlier answers the paper
               client without a venue request; a cooldown-cut read earns ONE
               bounded retry inside the Pinnacle freshness window; every
               attempt is a paper_evaluation_attempts row.
  AUDREY       the operational audit writes findings and OPERATIONAL
               recommendations with the owner's response and later
               measurements; a learning claim is a separate category.
  READ MODEL   the experiment read answers from persisted records, says
               CONNECTED, and labels exploration positions as training.

SYNTHETIC valuations and books (paper_live_fixture). No real money and no
venue order: every client counts mutation attempts and each proof asserts
zero.
"""
from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_paper_experiment as EXP
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_maker as PMK
from sportsassets.agents import paper_ops_audit as POA
from sportsassets.agents import paper_runtime as PR
from sportsassets.workers import ext_pinnacle_loop as LOOP

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CG, MAKER, EXPLORE = PB.CG_STRATEGY, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY


async def _nosleep(_):
    return None


def _only(*on):
    """Exactly these benchmark-family strategies on (the others off)."""
    for k in (CG, MAKER, EXPLORE, PB.CONTROL_KEY):
        PL.set_policy_control(k, k in on)


@pytest.fixture
def env(monkeypatch):
    """The environment flags on; each proof's own fixture below sets which
    strategies' rows are on (synchronously, before the proof runs)."""
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    # back to the migrated launch selection: CG and exploration on, maker
    # and strict off
    _only(CG, EXPLORE)
    PB._CONTEXT_CACHE.clear()


@pytest.fixture
def explore_only(env):
    _only(EXPLORE)


@pytest.fixture
def maker_only(env):
    _only(MAKER)


@pytest.fixture
def cg_and_explore(env):
    _only(CG, EXPLORE)


@pytest.fixture
def all_three(env):
    _only(CG, MAKER, EXPLORE)


@pytest.fixture
def cg_only(env):
    _only(CG)


async def _pass(conn, acct, t, now, client, fee_fn=None):
    t.t = max(t.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=fee_fn, sleep=_nosleep)


async def _dec(conn, acct, vid, strategy):
    return await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
        " valuation_id=$2 AND strategy=$3", acct["session_id"], vid, strategy)


async def _setup(conn, tag, now):
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    acct = await PL.new_account(conn, tag, now=now)
    return acct, PL.Transport(now)


# ═════════════════════════════════════════════════════════════════════
# 1 · EXPLORATION
# ═════════════════════════════════════════════════════════════════════

def test_the_owner_limits_and_labels_are_the_authorized_ones():
    d = PEX.describe()
    assert d["limits"] == {"max_entry_cost_usd_incl_fees": 250.0,
                           "max_aggregate_exposure_usd": 5000.0,
                           "loss_stop_realized_usd": 1000.0,
                           "positions_per_fixture": 1,
                           "overlap_with_other_strategies": "refused"}
    assert PEX.LABEL == "Training / simulated execution"
    assert "not investment performance" in PB.EXPLORE_DISCLOSURE
    assert PB.EXPLORE_POLICY["kind"] == "EXPLORATION"
    assert EXPLORE in PB.BENCHMARK_STRATEGIES
    assert EXPLORE in PB.COMPLETED_GAME_STRATEGIES
    # the investment policy and its entry requirements are unchanged
    assert PB.CG_VERSION == "PINNACLE_COMPLETED_GAME_PAPER_V2"
    assert PB.CG_PARAMETERS_V2 == {"min_gross_edge_pp": 0.5}


@pytest.mark.parametrize("n,p", [(1, 1.0), (6, 1.0), (12, 1.0), (60, 0.70)])
def test_the_inclusion_probability_is_inverse_to_sport_frequency(n, p):
    assert PEX.inclusion_probability(n) == pytest.approx(p)


def test_the_draw_is_deterministic_per_fixture():
    a, b = PEX.draw("condition:x"), PEX.draw("condition:x")
    assert a == b and 0.0 <= a < 1.0
    assert PEX.draw("condition:y") != a


def test_sizing_keeps_entry_cost_with_fees_within_100_dollars():
    at = time.time()
    for px in (0.05, 0.27, 0.50, 0.71, 0.95):
        s = PEX.size_entry([{"price": px, "wire": px, "qty": 100000}],
                           consumed={}, fee_fn=None, at=at, budget_usd=100.0)
        assert s["qty"] >= 1, px
        assert s["reservation_usd"] <= 100.0 + 1e-9, (px, s)
        # a fill can only cost less than its reservation
        cost = s["qty"] * px + float(L._fee(None, s["qty"], px, at))
        assert cost <= s["reservation_usd"] + 1e-9
    # depth caps the quantity; one contract above budget refuses by name
    s = PEX.size_entry([{"price": 0.5, "wire": 0.5, "qty": 7}], consumed={},
                       fee_fn=None, at=at, budget_usd=100.0)
    assert s["qty"] == 7
    s = PEX.size_entry([{"price": 0.5, "wire": 0.5, "qty": 70}], consumed={},
                       fee_fn=None, at=at, budget_usd=0.40)
    assert s["qty"] == 0 and s["why"] == PEX.R_TOO_DEAR


@pg
async def test_exploration_enters_a_losing_candidate_and_the_chain_completes(
        explore_only):
    """0.5 pp gross at $0.50 against a 1.74 pp taker fee: the investment
    policy would refuse; exploration ENTERS, says the EV is negative and why
    it trades, and the whole chain runs: fill, debit, Xavier, Audrey."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "explore1", now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.505,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        assert client.mutation_attempts == 0
        d = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert d is not None and d["verdict"] == "ENTER", (
            d and d["refusals"])
        assert d["decision_id"].startswith("paperexp:")
        pdx = H.j(d["policy_decision"])
        est = pdx["estimate"]
        assert est["gross_edge_pp_at_best"] == pytest.approx(0.5)
        assert est["fee_per_contract_usd"] == pytest.approx(0.017375,
                                                            abs=1e-6)
        assert est["expected_net_profit_usd"] < 0, "a research cost"
        assert est["passes_positive_after_fees"] is False
        assert pdx["training"] is True and pdx["training_purpose"]
        assert pdx["position_label"] == "Training / simulated execution"
        sel = pdx["selection"]
        assert sel["method"] == PEX.SELECTION_METHOD
        assert sel["selected"] is True and 0 < sel["selection_probability"]
        assert sel["draw"] < sel["selection_probability"]
        assert pdx["threshold_edge_pp"] is None
        assert pdx["investment_policy_threshold_pp_not_applied"] == 0.5
        # the order: marketable IOC at the best level, within $250 including fees
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", d["decision_id"])
        assert o["strategy"] == EXPLORE and o["role"] == "ENTRY"
        assert o["order_type"] == "MARKETABLE"
        assert float(o["limit_price"]) == 0.50
        assert 100.0 < float(o["reserved_usd"]) <= 250.0
        assert H.j(o["label"])["position_label"] == \
            "Training / simulated execution"
        # the investment policy made no decision here (it was off)
        assert await _dec(conn, acct, v["valuation_id"], CG) is None

        # THE FILL AFTER THE DELAY, THE DEBIT, THE HANDOFF, THE REVIEW
        p2 = await _pass(conn, acct, t, now + 5, client)
        assert not p2["errors"], p2["errors"]
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", d["decision_id"])
        assert o["state"] == "FILLED"
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1", o["order_id"])
        assert fills and {f["strategy"] for f in fills} == {EXPLORE}
        cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        fees = sum(float(f["fee_usd"]) for f in fills)
        assert 100.0 < cost + fees <= 250.0
        debit = await conn.fetchval(
            "SELECT -sum(cash_delta_usd) FROM paper_ledger WHERE "
            " order_id=$1 AND kind='FILL'", o["order_id"])
        assert float(debit) == pytest.approx(cost + fees)
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert h is not None and h["strategy"] == EXPLORE
        p3 = await _pass(conn, acct, t, now + 10, client)
        assert not p3["errors"], p3["errors"]
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at LIMIT 1",
                                 o["group_id"])
        assert rv is not None and rv["strategy"] == EXPLORE
        meas = H.j(rv["measure"])
        assert meas["strategy"] == EXPLORE
        assert meas["measure_is"] == "CONDITIONAL_ON_ORDINARY_COMPLETION"
        # AUDREY: the fill audit of the exploration strategy
        f = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE account_id=$1 AND "
            " kind=$2 AND subject=$3", acct["account_id"],
            PB.EXPLORE_POLICY["audit_kind"], o["group_id"])
        assert f is not None and H.j(f["detail"])["passed"] is True
        b = await L.balances(conn, acct["account_id"], now=now + 11)
        assert b["ledger_consistent"] is True
        assert b["cash_usd"] == pytest.approx(500000.0 - cost - fees)
        funding = await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
            " kind='INITIAL_FUNDING'", acct["account_id"])
        assert funding == 1, "no reset, no second funding"
        lim = await PEX.limits_state(conn, acct["account_id"])
        assert lim["open_positions"] == 1
        assert lim["exposure_usd"] == pytest.approx(cost + fees, abs=0.01)
        # THE EXPERIMENT READ: connected, the position labelled training
        x = await EXP.experiment(conn, now=now + 12,
                                 account_id=acct["account_id"])
        assert x["state"]["connected"] is True
        rows = [r for r in x["positions"]["data"]["open_positions"]
                if r["group_id"] == o["group_id"]]
        assert rows and rows[0]["label"] == "Training / simulated execution"
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_each_owner_limit_and_safeguard_refuses_by_name(explore_only,
                                                               monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "explore2", now)
        client = PL.client(t)

        async def one(p=0.505, pin_age=5.0, slug=None):
            v = await PL.valuation(conn, decided_at=now - 10, p_pin=p,
                                   compatibility="INCOMPATIBLE",
                                   pin_age_s=pin_age, slug=slug)
            t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
            await _pass(conn, acct, t, now, client)
            return v, await _dec(conn, acct, v["valuation_id"], EXPLORE)

        # stale Pinnacle: the safeguard is never relaxed
        v, d = await one(pin_age=60.0)
        assert d["verdict"] == "REFUSE"
        assert d["refusal"] == PD.DP.R_STALE
        assert d["book_obs_id"] is None
        # not sampled: refused before any book read
        monkeypatch.setattr(PEX, "inclusion_probability", lambda n: 0.0)
        v, d = await one()
        assert d["refusal"] == PEX.R_NOT_SAMPLED and d["book_obs_id"] is None
        monkeypatch.undo()
        monkeypatch.setenv(PB.ENV_FLAG, "on")
        monkeypatch.setenv(PL.S.ENV_FLAG, "on")
        # one enters; a second valuation of the SAME fixture is refused
        v1, d1 = await one()
        assert d1["verdict"] == "ENTER", d1["refusals"]
        t.t += 1.0
        v2 = await PL.valuation(conn, slug=v1["slug"], decided_at=now - 9,
                                p_pin=0.505, compatibility="INCOMPATIBLE")
        await _pass(conn, acct, t, now, client)
        d2 = await _dec(conn, acct, v2["valuation_id"], EXPLORE)
        assert d2["refusal"] == PEX.R_FIXTURE_TAKEN
        # the aggregate limit: with $100 aggregate, the next fixture refuses
        monkeypatch.setattr(PEX, "MAX_AGGREGATE_EXPOSURE_USD", 100.0)
        v3, d3 = await one()
        assert d3["refusal"] == PEX.R_AGGREGATE
        monkeypatch.setattr(PEX, "MAX_AGGREGATE_EXPOSURE_USD", 5000.0)
        # the realized-loss stop
        monkeypatch.setattr(PEX, "LOSS_STOP_USD", 0.0)
        v4, d4 = await one()
        assert d4["refusal"] == PEX.R_LOSS_STOP
        monkeypatch.setattr(PEX, "LOSS_STOP_USD", 1000.0)
        # UNDER THE LOCK too: the check refuses an order the limits forbid
        st = await PEX.limits_state(conn, acct["account_id"])
        chk = PEX.locked_check_for("condition:other", "other-slug")
        got = await chk(conn, {"account_id": acct["account_id"]},
                        L.D(5000.0 - st["exposure_usd"] + 1.0))
        assert got["refusal"] in (PEX.R_AGGREGATE, PEX.R_TOO_DEAR)
        got = await chk(conn, {"account_id": acct["account_id"]}, L.D(50))
        assert got is None
        got = await PEX.locked_check_for(
            None, v1["slug"])(conn, {"account_id": acct["account_id"]},
                              L.D(50))
        assert got["refusal"] == PEX.R_FIXTURE_TAKEN
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_no_overlap_with_the_investment_policy_either_way(cg_and_explore):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "explore3", now)
        client = PL.client(t)
        # the investment policy enters first (12 pp at $0.50)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        await _pass(conn, acct, t, now, client)
        cg = await _dec(conn, acct, v["valuation_id"], CG)
        assert cg["verdict"] == "ENTER"
        ex = await _dec(conn, acct, v["valuation_id"], EXPLORE)
        assert ex["verdict"] == "REFUSE"
        assert ex["refusal"] == PB.R_CROSS_STRATEGY
        orders = await conn.fetch("SELECT strategy FROM paper_orders WHERE "
                                  " session_id=$1 AND role='ENTRY'",
                                  acct["session_id"])
        assert [o["strategy"] for o in orders] == [CG]
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · MAKER ENTRY
# ═════════════════════════════════════════════════════════════════════

FEE = (lambda at: (lambda px: PB.fee_per_contract(None, px, at)))


def test_the_resting_price_clears_threshold_and_taker_fee_below_the_ask():
    fee = FEE(time.time())
    lv = lambda a: [{"price": a, "wire": a, "qty": 100}]  # noqa: E731
    # today's closest refusals, rested one cent below the ask
    r = PMK.resting_price(lv(0.27), p=0.2763, min_edge=0.005, fee_pc=fee)
    assert r["limit"] == 0.26 and r["net_edge_pp"] > 0
    r = PMK.resting_price(lv(0.67), p=0.6782, min_edge=0.005, fee_pc=fee)
    assert r["limit"] == 0.66
    # never at or above the ask
    for p in (0.30, 0.55, 0.9):
        r = PMK.resting_price(lv(0.50), p=p, min_edge=0.005, fee_pc=fee)
        assert r["limit"] is None or r["limit"] < 0.50
    # p below every price that could clear: none
    r = PMK.resting_price(lv(0.02), p=0.012, min_edge=0.005, fee_pc=fee)
    assert r["limit"] is None and r["refusal"] == PMK.R_NO_PRICE
    # at the chosen price both rules hold exactly
    r = PMK.resting_price(lv(0.50), p=0.515, min_edge=0.005, fee_pc=fee)
    assert r["limit"] == 0.49
    assert r["gross_edge_pp"] >= 0.5 and r["net_edge_pp"] > 0
    assert PMK.FEE_BASIS["maker_rebate_status"].startswith(
        "PUBLISHED_NOT_VERIFIED_APPLIED")


def test_the_cancellation_conditions():
    fee = FEE(time.time())
    kw = dict(limit=0.49, min_edge=0.005, fee_pc=fee)
    assert PMK.check_resting(p_new=0.515, reading_age_s=30, enabled=True,
                             **kw)["cancel"] is False
    c = PMK.check_resting(p_new=0.50, reading_age_s=30, enabled=True, **kw)
    assert c["cancel"] and c["condition"] == PMK.C_EDGE_GONE
    c = PMK.check_resting(p_new=0.515, reading_age_s=5000, enabled=True,
                          **kw)
    assert c["condition"] == PMK.C_UNVERIFIED
    c = PMK.check_resting(p_new=0.515, reading_age_s=30, enabled=False, **kw)
    assert c["condition"] == PMK.C_SWITCHED_OFF


@pg
async def test_the_maker_order_rests_never_fills_on_a_touch_and_cancels(
        maker_only):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        acct, t = await _setup(conn, "maker1", now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.515,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client)
        assert not p1["errors"], p1["errors"]
        d = await _dec(conn, acct, v["valuation_id"], MAKER)
        assert d["verdict"] == "ENTER", d["refusals"]
        pdx = H.j(d["policy_decision"])
        assert pdx["resting_price"] == 0.49
        assert pdx["threshold_edge_pp"] == 0.5
        assert pdx["order_is_not_a_fill"] is True
        assert pdx["rationale"] and pdx["cancel_conditions"]
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", d["decision_id"])
        assert o["order_type"] == "RESTING" and o["time_in_force"] == "GTD"
        assert o["state"] == "RESTING" and float(o["filled_qty"]) == 0
        assert float(o["limit_price"]) == 0.49
        assert L._epoch(o["expires_at"]) == pytest.approx(
            L._epoch(o["decided_at"]) + PMK.MAKER_TTL_S)
        lb = H.j(o["label"])
        assert lb["post_only"] is True and lb["cancel_conditions"]
        assert float(o["reserved_usd"]) > 0, "an order reserves cash"
        b = await L.balances(conn, acct["account_id"], now=now + 1)
        assert b["reserved_usd"] == pytest.approx(float(o["reserved_usd"]))
        assert b["cash_usd"] == pytest.approx(500000.0), "not a fill"
        # A TOUCH (an offer AT 0.49) IS NOT A FILL
        t.set(v["slug"], offers=[(0.49, 300)], bids=[(0.47, 300)])
        await _pass(conn, acct, t, now + 20, client)
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " order_id=$1", o["order_id"])
        assert float(o["filled_qty"]) == 0 and o["state"] == "RESTING"
        # A STRICT CROSS (an offer BELOW 0.49): filled at OUR limit, partial
        t.set(v["slug"], offers=[(0.48, 40)], bids=[(0.46, 300)])
        await _pass(conn, acct, t, now + 40, client)
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " order_id=$1", o["order_id"])
        assert float(o["filled_qty"]) == 40
        assert o["state"] == "PARTIALLY_FILLED"
        f = await conn.fetchrow("SELECT * FROM paper_fills WHERE "
                                " order_id=$1", o["order_id"])
        assert float(f["price"]) == 0.49
        assert f["basis"] == "CROSSING_LIQUIDITY_AFTER_QUEUE"
        # the TAKER fee is charged on the maker fill (rebate not assumed)
        assert float(f["fee_usd"]) == pytest.approx(
            float(L._fee(None, 40, 0.49, L._epoch(f["filled_at"]))))
        assert float(f["fee_usd"]) > 0
        # A NEWER READING REMOVES THE EDGE: cancel requested, then confirmed
        v2 = await PL.valuation(conn, slug=v["slug"], decided_at=now + 45,
                                p_pin=0.49, compatibility="INCOMPATIBLE")
        assert v2
        p4 = await _pass(conn, acct, t, now + 60, client)
        assert p4["steps"]["maker_maintain"]["cancel_requested"] == 1, \
            p4["steps"]["maker_maintain"]
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " order_id=$1", o["order_id"])
        assert o["state"] == "CANCELED"
        assert float(o["reserved_remaining_usd"]) == 0
        ev = await conn.fetch("SELECT kind, detail FROM paper_order_events "
                              " WHERE order_id=$1 ORDER BY event_id",
                              o["order_id"])
        kinds = [e["kind"] for e in ev]
        assert "NO_FILL_EVIDENCE" in kinds and "CANCEL_REQUESTED" in kinds
        cr = [H.j(e["detail"]) for e in ev if e["kind"] == "CANCEL_REQUESTED"]
        assert cr[0]["reason"] == PMK.C_EDGE_GONE
        rel = await conn.fetchrow(
            "SELECT * FROM paper_ledger WHERE order_id=$1 AND "
            " kind='RESERVATION_RELEASED'", o["order_id"])
        assert rel is not None and float(rel["reserved_delta_usd"]) < 0
        # on the newer reading (p 0.49 against an offer at 0.48) a NEW
        # resting bid may be placed at its own price; never two at once
        live = await conn.fetch(
            "SELECT order_id, limit_price FROM paper_orders WHERE "
            " account_id=$1 AND strategy=$2 AND role='ENTRY' AND "
            " state = ANY($3::text[])", acct["account_id"], MAKER,
            list(L.OPEN_STATES))
        assert len(live) <= 1
        b = await L.balances(conn, acct["account_id"], now=now + 61)
        assert b["ledger_consistent"] is True
        open_res = await conn.fetchval(
            "SELECT coalesce(sum(reserved_remaining_usd), 0) FROM "
            " paper_orders WHERE account_id=$1 AND state = ANY($2::text[])",
            acct["account_id"], list(L.OPEN_STATES))
        assert b["reserved_usd"] == pytest.approx(float(open_res))
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · THROUGHPUT
# ═════════════════════════════════════════════════════════════════════

def test_a_recent_successful_read_is_shared_with_its_receipt_instant(
        monkeypatch):
    LOOP._RECENT_BOOKS.clear()
    LOOP._remember_book("s1", {"marketData": H.md(offers=[(0.5, 10)])})
    got = LOOP.recent_book("s1", max_age_s=6.0)
    assert got["shared_read"] is True and got["marketData"]
    at = got["observed_at"]
    assert LOOP.recent_book("s1", max_age_s=6.0, now=at + 7.0) is None
    # an error is never remembered
    LOOP._remember_book("s2", {"marketData": None, "error": "X"})
    assert LOOP.recent_book("s2", max_age_s=6.0) is None

    def boom(*a, **k):
        raise AssertionError("no venue request for a shared read")
    monkeypatch.setattr(LOOP, "_read_book_blocking", boom)
    out = G._default_transport("s1")
    assert out["shared_read"] is True and out["observed_at"] == at
    LOOP._RECENT_BOOKS.clear()


def test_the_retry_budget_is_one_retry_inside_the_freshness_window():
    pin = {"age_s": 10.0, "limit_s": 30.0}
    cut = {"refused_by": "OUR_REQUEST_GATE",
           "gate_detail": {"seconds_left": 5.0}}
    r = PB.book_retry_plan({"book_retry_ok": True}, cut, pin)
    assert r["retry"] and r["after_s"] == pytest.approx(5.25)
    assert not PB.book_retry_plan({}, cut, pin)["retry"]
    r = PB.book_retry_plan({"book_retry_ok": True}, cut,
                           {"age_s": 25.0, "limit_s": 30.0})
    assert not r["retry"]
    assert r["why"] == "PINNACLE_WOULD_BE_STALE_AFTER_THE_COOLDOWN"
    long = {"refused_by": "OUR_REQUEST_GATE",
            "gate_detail": {"seconds_left": 40.0}}
    assert PB.book_retry_plan({"book_retry_ok": True}, long, pin)[
        "why"] == "COOLDOWN_LONGER_THAN_THE_RETRY_CAP"


@pg
async def test_a_cooldown_cut_read_retries_once_and_every_attempt_is_a_row(
        all_three):
    conn = await H.connect()
    now = time.time()
    try:
        acct, t = await _setup(conn, "retry1", now)
        # 1.5 pp at $0.50: the investment policy refuses on its fee, the
        # maker rests a bid on the SAME book read
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.515,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)
        calls = []

        def cut(slug):
            calls.append(slug)
            return {"marketData": None, "error":
                    "VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE",
                    "refused_by": "OUR_REQUEST_GATE",
                    "gate_detail": {"seconds_left": 3.0}}
        scheduled = []
        g = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"],
            market_data=G.PaperMarketDataClient(cut),
            account_id=acct["account_id"],
            schedule_fill=lambda: {"scheduled": False},
            schedule_retry=lambda **kw: (scheduled.append(kw) or
                                         {"scheduled": True, **kw}))
        assert g["benchmark_completed_game"]["deferred"] is True
        assert len(calls) == 1, "one read for the three strategies"
        assert scheduled and scheduled[0]["strategies"] == [CG, MAKER,
                                                            EXPLORE]
        assert scheduled[0]["after_s"] == pytest.approx(3.25)
        assert await _dec(conn, acct, v["valuation_id"], CG) is None
        rows = await conn.fetch(
            "SELECT strategy, via, attempt_no, outcome FROM "
            " paper_evaluation_attempts WHERE valuation_id=$1 ORDER BY "
            " attempt_id", v["valuation_id"])
        outs = [(r["strategy"], r["outcome"]) for r in rows]
        assert (CG, "DEFERRED_FOR_BOOK_RETRY") in outs
        assert (EXPLORE, "DEFERRED_FOR_BOOK_RETRY") in outs
        assert (CG, "RETRY_SCHEDULED") in outs
        # THE RETRY: a readable book now; decided and recorded
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        g2 = await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"],
            market_data=PL.client(t), account_id=acct["account_id"],
            schedule_fill=lambda: {"scheduled": False},
            strategies=scheduled[0]["strategies"], via="BOOK_RETRY",
            attempt_no=2)
        assert g2["decided"] is True and g2["via"] == "BOOK_RETRY"
        d = await _dec(conn, acct, v["valuation_id"], CG)
        assert d is not None and d["refusal"] == PB.R_FEES_CONSUME_EDGE
        mk = await _dec(conn, acct, v["valuation_id"], MAKER)
        assert mk["verdict"] == "ENTER", mk["refusals"]
        r2 = await conn.fetch(
            "SELECT strategy, outcome, book_source FROM "
            " paper_evaluation_attempts WHERE valuation_id=$1 AND "
            " via='BOOK_RETRY'", v["valuation_id"])
        assert {(r["strategy"], r["outcome"]) for r in r2} >= {
            (CG, "DECIDED"), (MAKER, "DECIDED"), (EXPLORE, "DECIDED")}
        assert {r["book_source"] for r in r2 if r["strategy"] == CG} == {
            "FRESH_VENUE_READ"}
        assert {r["book_source"] for r in r2 if r["strategy"] == MAKER} == {
            "REUSED_IN_THIS_EVALUATION"}
        # no retry budget: the cut read is decided at once, by name
        v3 = await PL.valuation(conn, decided_at=time.time() - 2, p_pin=0.62,
                                compatibility="INCOMPATIBLE", pin_age_s=2.0)
        g3 = await PR.decide_valuation(
            conn, valuation_id=v3["valuation_id"],
            market_data=G.PaperMarketDataClient(cut),
            account_id=acct["account_id"],
            schedule_fill=lambda: {"scheduled": False}, book_retry=False)
        assert g3["benchmark_completed_game"]["refusal"] == PD.R_BOOK_DEADLINE
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · AUDREY'S OPERATIONAL AUDIT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_audrey_assigns_operational_recommendations_with_responses(
        cg_only):
    conn = await H.connect()
    now = time.time()
    try:
        acct, t = await _setup(conn, "opsaudit", now)
        for k in POA.RESPONSES:
            await conn.execute(
                "DELETE FROM paper_recommendation_events WHERE "
                " recommendation_id=$1", POA._rid(acct["account_id"], k))
            await conn.execute(
                "DELETE FROM paper_recommendations WHERE recommendation_id"
                "=$1", POA._rid(acct["account_id"], k))
        # a cut read, recorded as the deadline refusal (no retry here)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62,
                               compatibility="INCOMPATIBLE", pin_age_s=2.0)

        def cut(slug):
            return {"marketData": None, "error":
                    "VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE",
                    "refused_by": "OUR_REQUEST_GATE",
                    "gate_detail": {"seconds_left": 3.0}}
        await PR.decide_valuation(
            conn, valuation_id=v["valuation_id"],
            market_data=G.PaperMarketDataClient(cut),
            account_id=acct["account_id"],
            schedule_fill=lambda: {"scheduled": False}, book_retry=False)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": now + 1}
        res = await POA.audit(conn, ctx, now=now + 1)
        assert res["book_reads"]["cut"] >= 1
        rid = POA._rid(acct["account_id"], "BOOK_READ_CUTS")
        rec = await conn.fetchrow("SELECT * FROM paper_recommendations WHERE"
                                  " recommendation_id=$1", rid)
        assert rec["owner_agent"] == "DEREK"
        assert rec["category"] == "OPERATIONAL"
        assert H.j(rec["baseline"])["value"] == pytest.approx(1.0)
        ev = await conn.fetch("SELECT actor, kind, body FROM "
                              " paper_recommendation_events WHERE "
                              " recommendation_id=$1 ORDER BY event_id", rid)
        # THE TEMPLATE IS SYSTEM'S, LABELLED; NO RESPONSE IS ATTRIBUTED TO
        # DEREK WITHOUT HIS OWN REVIEW
        assert [(e["actor"], e["kind"]) for e in ev] == [
            ("AUDREY", "ASSIGNED"), ("SYSTEM", "AUTOMATED_ACKNOWLEDGEMENT")]
        assert ev[1]["body"].startswith(POA.ACK_LABEL)
        assert "not an agent review" in ev[1]["body"]
        assert "Addressed to DEREK" in ev[1]["body"]
        for actor, kind in (("SYSTEM", "RESPONSE"),
                            ("DEREK", "AUTOMATED_ACKNOWLEDGEMENT"),
                            ("AUDREY", "RESPONSE")):
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO paper_recommendation_events "
                    " (recommendation_id, actor, kind, body) VALUES "
                    " ($1, $2, $3, 'x')", rid, actor, kind)
        x = await EXP.agents(conn, now + 1)
        r0 = [r for r in x["audrey"]["recommendations"]
              if r["recommendation_id"] == rid][0]
        assert r0["agent_responses"] == 0
        assert r0["automated_acknowledgements"] == 1
        f = await conn.fetchrow("SELECT * FROM paper_audrey_findings WHERE "
                                " finding_id=$1", rec["finding_id"])
        assert f["kind"] == "OPS_AUDIT_BOOK_READ_CUTS"
        assert f["severity"] == "WARNING"
        # a later audit MEASURES the same metric (it never declares success)
        await POA.audit(conn, ctx, now=now + 1 + POA.MEASURE_EVERY_S + 1)
        ev = await conn.fetch("SELECT kind FROM paper_recommendation_events "
                              " WHERE recommendation_id=$1", rid)
        assert [e["kind"] for e in ev].count("MEASUREMENT") == 1
        rec = await conn.fetchrow("SELECT status FROM paper_recommendations "
                                  " WHERE recommendation_id=$1", rid)
        assert rec["status"] == "MEASURING"
        # OPERATIONAL and LEARNING_CLAIM are kept apart by the database
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO paper_recommendations (recommendation_id, "
                " account_id, owner_agent, category, kind, metric, baseline,"
                " recommendation, evidence) VALUES ('paperrec:x', $1, "
                " 'DEREK', 'PROFIT', 'K', 'm', '{}', 'r', '{}')",
                acct["account_id"])
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · THE EXPERIMENT READ
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_experiment_read_says_connected_and_explains_no_fills():
    conn = await H.connect()
    try:
        x = await EXP.experiment(conn)
        assert x["state"]["connected"] is True
        assert x["freshness"]["status"] == "OK"
        assert x["freshness"]["data"]["database"] == "CONNECTED"
        for k in ("session", "opportunities", "refusals", "closest",
                  "orders", "fills", "positions", "agents", "throughput"):
            assert x[k]["status"] in ("OK", "EMPTY"), (k, x[k])
        assert x["real_money"] == "DISABLED"
        json.dumps(x, default=str)
    finally:
        await conn.close()
