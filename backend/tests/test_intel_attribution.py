"""CAPITAL-CRITICAL (SHADOW): ALPHA VS EXECUTION ATTRIBUTION ADDS UP, AND
SAYS NULL WHERE IT CANNOT KNOW.

  §1 the identity, by hand: model + execution + management + settlement +
     outcome variance = realized = cash P&L, for held, sold-early, hedged
     and exceptional-settlement positions
  §2 nulls: an unsettled position has no settlement / realized figures and
     a sold-early unsettled one has no management figure; no fill, no edge
  §3 the paper read over synthetic decision -> order -> fill -> settlement
     rows reconciles; the actual read converts venue wire prices by side
"""
from __future__ import annotations

import time

import asyncpg
import pytest

from sportsassets.intel import attribution as A

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

ENTRY = [{"qty": 100, "price": 0.54, "fee_usd": 1.0}]


def _attr(**kw):
    base = dict(subject_id="g", book="PAPER", p=0.62, p_basis="P_PINNACLE",
                d=0.52, d_basis="X", entry_fills=ENTRY, sell_fills=[],
                hedge_legs=[], payoff=1.0, settlement_outcome="WON",
                payoff_basis="PAPER_SETTLEMENT")
    base.update(kw)
    return A.attribute(**base)


def _sums(r):
    return (r["model_edge_usd"] + r["execution_edge_usd"]
            + r["management_usd"] + r["settlement_usd"]
            + r["outcome_variance_usd"])


# ── §1 the identity ──────────────────────────────────────────────────

def test_a_held_winning_position_decomposes_exactly():
    r = _attr()
    assert r["fill_vwap"] == pytest.approx(0.54)
    assert r["model_edge_pc"] == pytest.approx(0.10)
    assert r["model_edge_usd"] == pytest.approx(10.0)
    assert r["executable_edge_pc"] == pytest.approx(0.08)
    assert r["executable_edge_usd"] == pytest.approx(8.0)
    assert r["slippage_pc"] == pytest.approx(0.02)
    assert r["slippage_usd"] == pytest.approx(2.0)
    assert r["fees_usd"] == pytest.approx(1.0)
    assert r["execution_edge_usd"] == pytest.approx(-3.0)
    assert r["management_usd"] == 0.0
    assert r["management_basis"] == "NO_MANAGEMENT_ACTION_HELD_TO_SETTLEMENT"
    assert r["settlement_class"] == "ORDINARY"
    assert r["settlement_usd"] == 0.0
    assert r["outcome_variance_usd"] == pytest.approx(38.0)
    assert r["realized_pnl_usd"] == pytest.approx(45.0)
    assert r["cash_pnl_usd"] == pytest.approx(45.0)
    assert _sums(r) == pytest.approx(45.0)
    assert r["reconciles"] is True and r["identity_claimed"] is True


def test_selling_early_is_managements_contribution_against_holding():
    r = _attr(sell_fills=[{"qty": 40, "price": 0.70, "fee_usd": 0.4}],
              payoff=0.0, settlement_outcome="LOST", actions=2)
    assert r["management_usd"] == pytest.approx(27.6)
    assert r["outcome_variance_usd"] == pytest.approx(-62.0)
    assert r["realized_pnl_usd"] == pytest.approx(-27.4)
    assert r["cash_pnl_usd"] == pytest.approx(-27.4)
    assert _sums(r) == pytest.approx(-27.4)
    assert r["reconciles"] is True
    assert r["management_actions"] == 2


def test_a_hedge_leg_counts_toward_management():
    r = _attr(hedge_legs=[{"qty": 50, "cost_usd": 20.0,
                           "payoff_per_contract": 1.0}])
    assert r["management_usd"] == pytest.approx(30.0)
    assert r["realized_pnl_usd"] == pytest.approx(75.0)
    assert r["reconciles"] is True


def test_an_exceptional_settlement_is_separated_from_the_coin():
    r = _attr(payoff=0.8, settlement_outcome="SETTLED_AT_VENUE_PRICE")
    assert r["settlement_class"] == "EXCEPTIONAL"
    assert r["settlement_usd"] == pytest.approx(18.0)
    assert r["outcome_variance_usd"] == 0.0
    assert r["realized_pnl_usd"] == pytest.approx(100 * (0.8 - 0.54) - 1)
    assert r["reconciles"] is True


# ── §2 nulls ─────────────────────────────────────────────────────────

def test_an_unsettled_position_has_no_realized_figures():
    r = _attr(payoff=None, settlement_outcome=None, payoff_basis=None)
    assert r["model_edge_usd"] == pytest.approx(10.0)     # known at fill
    for k in ("settlement_usd", "outcome_variance_usd", "realized_pnl_usd",
              "cash_pnl_usd"):
        assert r[k] is None and r["unmeasured"][k], k
    assert r["reconciles"] is None
    sold = _attr(payoff=None, settlement_outcome=None,
                 sell_fills=[{"qty": 10, "price": 0.6, "fee_usd": 0}])
    assert sold["management_usd"] is None
    assert sold["unmeasured"]["management_usd"] == (
        "POSITION_NOT_SETTLED_HOLD_COUNTERFACTUAL_UNKNOWN")


def test_no_fill_means_no_edge_and_no_planned_price_means_no_model_edge():
    r = _attr(entry_fills=[])
    for k in ("fill_vwap", "model_edge_usd", "execution_edge_usd",
              "realized_pnl_usd"):
        assert r[k] is None and r["unmeasured"][k] == "NO_ENTRY_FILL"
    r = _attr(d=None, d_basis=None)
    assert r["model_edge_usd"] is None and r["slippage_usd"] is None
    assert r["executable_edge_usd"] == pytest.approx(8.0)
    assert r["realized_pnl_usd"] == pytest.approx(45.0)
    assert r["identity_claimed"] is False and r["reconciles"] is True


def test_the_decision_price_prefers_the_planned_vwap():
    assert A.decision_price({"economics": {"acquisition": {"vwap": 0.51}},
                             "limit_price": 0.56}) == (
        0.51, "DECISION_PLANNED_ACQUISITION_VWAP")
    assert A.decision_price({"economics": {"levels": [{"price": 0.5}]},
                             "limit_price": 0.56})[1] == (
        "DECISION_BEST_LEVEL_PRICE")
    assert A.decision_price({"economics": None, "limit_price": 0.56})[1] == (
        "DECISION_LIMIT_PRICE")
    assert A.decision_price({}) == (None, None)


def test_the_summary_never_sums_books_and_counts_measured_values():
    rows = [_attr(), dict(_attr(), book="ACTUAL", subject_id="a"),
            _attr(payoff=None, settlement_outcome=None)]
    s = A.summarize(rows)
    assert s["summed_across_books"] is False
    assert s["PAPER"]["positions"] == 2
    assert s["PAPER"]["realized_pnl_usd"] == pytest.approx(45.0)
    assert s["PAPER"]["realized_pnl_usd_measured_n"] == 1
    assert s["ACTUAL"]["positions"] == 1


# ── §3 the reads ─────────────────────────────────────────────────────

@pg
async def test_the_paper_read_reconciles_to_cash():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct = await H.new_account(conn, "intelattr", now=now - 86400)
        d = await F.decision(conn, acct, at=now - 5000, p=0.62, limit=0.56,
                             vwap=0.52, qty=100)
        g = "paper_group_" + F.uid()
        oid = await F.order(conn, acct, group_id=g, slug=d["slug"], qty=100,
                            price=0.54, decision_id=d["decision_id"],
                            at=now - 5000)
        await F.fill(conn, acct, order_id=oid, group_id=g, slug=d["slug"],
                     qty=100, price=0.54, fee=1.0, at=now - 4998)
        xo = await F.order(conn, acct, group_id=g, slug=d["slug"],
                           role="EXIT", direction="SELL", qty=40, price=0.70,
                           at=now - 3000)
        await F.fill(conn, acct, order_id=xo, group_id=g, slug=d["slug"],
                     role="EXIT", direction="SELL", qty=40, price=0.70,
                     fee=0.4, at=now - 2998)
        await F.settle(conn, acct, group_id=g, slug=d["slug"], qty=60,
                       outcome="LOST", payout_per_contract=0.0, at=now - 100)
        rows = await A.load_paper(conn, now=now,
                                  account_id=acct["account_id"])
        assert len(rows) == 1
        r = rows[0]
        assert r["decision_id"] == d["decision_id"] and r["group_id"] == g
        assert r["model_edge_usd"] == pytest.approx(10.0)
        assert r["slippage_usd"] == pytest.approx(2.0)
        assert r["management_usd"] == pytest.approx(27.6)
        assert r["realized_pnl_usd"] == pytest.approx(-27.4)
        assert r["cash_pnl_usd"] == pytest.approx(-27.4)
        assert r["reconciles"] is True
        assert r["label"] == "SHADOW"
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_actual_read_converts_venue_wire_prices_by_side():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct = await H.new_account(conn, "intelact", now=now - 86400)
        d = await F.decision(conn, acct, at=now - 5000, p=0.62, side="SHORT",
                             vwap=0.40, qty=1000)
        g = "paper_group_" + F.uid()
        mid = F.uid("exm-")
        iid = F.uid("int-")
        await conn.execute(
            "INSERT INTO execution_intents (intent_id, decision_id, strategy,"
            " us_market_slug, order_intent, group_id, order_type, "
            " time_in_force, paper_target_qty, limit_price, wire_price, "
            " decided_at, live_eligible, actual_state, actual_refusal, "
            " actual_mirror_id, holding_side) VALUES ($1,$2,$3,$4,"
            " 'ORDER_INTENT_BUY_SHORT',$5,'MARKETABLE','IOC',1000,0.40,0.60,"
            " to_timestamp($6),false,'PAPER_ONLY','TEST',$7,'SHORT')",
            iid, d["decision_id"], F.STRATEGY, d["slug"], g, now - 5000, mid)
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, group_id, role, "
            " us_market_slug, intent, order_type, tif, live_qty, state, "
            " cum_qty) VALUES ($1,$2,'ENTRY',$3,'ORDER_INTENT_BUY_SHORT',"
            " 'LIMIT','IOC',1,'FILLED',1)", mid, g, d["slug"])
        # the venue reports the LONG-side wire price 0.58: cost 0.42
        await conn.execute(
            "INSERT INTO execmirror_fills (fill_key, mirror_id, "
            " venue_order_id, group_id, us_market_slug, intent, qty, price, "
            " fee_usd, observed_at) VALUES ($1,$2,'v1',$3,$4,"
            " 'ORDER_INTENT_BUY_SHORT',1,0.58,0.01,to_timestamp($5))",
            F.uid("fk-"), mid, g, d["slug"], now - 4990)
        rows = [r for r in await A.load_actual(conn, now=now)
                if r["group_id"] == g]
        assert len(rows) == 1
        r = rows[0]
        assert r["book"] == "ACTUAL" and r["holding_side"] == "SHORT"
        assert r["decision_price"] == pytest.approx(0.40)
        assert r["fill_vwap"] == pytest.approx(0.42)
        assert r["slippage_pc"] == pytest.approx(0.02)
        assert r["executable_edge_pc"] == pytest.approx(0.20)
        assert r["settlement_usd"] is None
        assert r["unmeasured"]["settlement_usd"] == "POSITION_NOT_SETTLED"
    finally:
        await tr.rollback()
        await conn.close()
