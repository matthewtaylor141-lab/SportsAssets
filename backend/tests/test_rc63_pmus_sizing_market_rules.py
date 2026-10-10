"""CAPITAL-CRITICAL (rc6.3 pmus-sizing; Issue #6 gate 6, contract audit item
3d): THE ACTUAL BUY IS SIZED AND PRICED ON THE MARKET'S OWN TRADING RULES.

THE DEFECT, REPRODUCED ON THE BASE (34cb9544). execmirror.scale_qty sized the
ACTUAL BUY as paper qty / 1,000 rounded HALF-EVEN to whole contracts and never
read the market: 2,702 paper contracts went out as 3 (ABOVE the 1:1000
target; tests/test_execmirror.py asserted the 3), a market whose minimum is
above one contract would have been sent below it, and no limit price was
checked against the market's tick (§0 pins each against scale_qty /
plan_buy, which stay unchanged for the SHADOW parity adapter and Kalshi).

THE RULE NOW (execmirror.MARKET_RULES_VERSION; execution_intent step 4):
  §1  the market's minimumTradeQty, its quantity increment (the documented
      step its minimum selects: 0.01 below one contract, 1 for whole
      contracts) and its orderPriceMinTickSize come from the market's own
      record; any of them absent, unreadable or outside the documentation is
      refused BY NAME -- never assumed to be 1;
  §2  the size is ROUNDED DOWN to a step that is a multiple of the market's
      increment and of the lane's unit (one contract: the lane's quantity
      columns are integers), and below the market's minimum it is
      BELOW_VENUE_MINIMUM -- never enlarged; the exact size at the market's
      own increment is computed and recorded beside it;
  §3  the limit price must be on the market's tick and exactly what the order
      carries at 4 decimals -- refused by name, never re-priced;
  §4  through the ACTUAL lane on real Postgres (test-only non-SHADOW mode,
      FakeVenue): $1,500 at 0.50 is 3 contracts / $1.50; the fractional
      market sizes 2.419 to 2 contracts with the exact 2.41 on the record;
      a minimum-5 market refuses 3; missing metadata and an off-tick price
      refuse by name with nothing placed; the per-order cap and the buying
      power still bind; Audrey reconciles a floored size as MATCHED;
  §5  THE WIRE (the real execmirror.Venue on the pinned SDK over
      httpx.MockTransport): in SHADOW (as shipped) NOTHING reaches the wire --
      no market read, no order; in the test-only mode the market record is
      read first (GET gateway /v1/market/slug/{slug}, public, unsigned), then
      the order is created with the market-sized quantity.

THE MODE NEVER CHANGES OUTSIDE A TEST: SMALL_LIVE_MODE stays SHADOW in
live_authorization and live_parity; the non-SHADOW value exists only inside
the tests that monkeypatch it. Fake venue or mock transport only: no
credential, no network, no order anywhere. Own Postgres (RN1X_TEST_DSN); the
control row and the lane's tables are restored after every test.
"""
from __future__ import annotations

import base64
import json
import os
import random
import threading
import time
import uuid
from decimal import Decimal

import httpx
import pytest

from sportsassets import execmirror as M
from sportsassets import execmirror_probe as EP
from sportsassets import execution_intent as EI
from sportsassets import live_authorization as LA
from sportsassets import live_parity as LP
from sportsassets import venue_pace as VP

from tests import test_execmirror as TE

pg = TE.pg
TEST_MODE = "LIVE_TEST_ONLY_NOT_IN_THIS_RELEASE"
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "pmus_settled_market_2026_09_24_az_col.json")


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


def _live_mode(monkeypatch) -> None:
    """TEST-ONLY: the activation this release does not have, in BOTH modules
    (execution_intent._small_live_is_shadow fails closed if either says
    SHADOW)."""
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", TEST_MODE)
    monkeypatch.setattr(LP, "SMALL_LIVE_MODE", TEST_MODE)


def _market(slug, *, minimum="0.01", tick="0.01", **extra):
    rec = {"slug": slug}
    if minimum is not None:
        rec["minimumTradeQty"] = minimum
    if tick is not None:
        rec["orderPriceMinTickSize"] = tick
    rec.update(extra)
    return rec


@pytest.fixture(autouse=True)
async def _restore_control():
    """Every DB test here leaves execmirror_control and the lane's tables as
    it found them."""
    if not TE.DSN:
        yield
        return
    conn = await TE._conn()
    try:
        before = dict(await conn.fetchrow(
            "SELECT * FROM execmirror_control WHERE id = 1"))
    finally:
        await conn.close()
    yield
    conn = await TE._conn()
    try:
        cols = [c for c in before if c != "id"]
        await conn.execute(
            "UPDATE execmirror_control SET %s WHERE id = 1" % ", ".join(
                "%s = $%d" % (c, i + 1) for i, c in enumerate(cols)),
            *[before[c] for c in cols])
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                           "smalllive_reconciliations")
        await conn.execute("TRUNCATE execmirror_fills, execmirror_events, "
                           "execmirror_snapshots, execmirror_orders")
        await conn.execute("DELETE FROM execution_intents")
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §0 THE BASE RULE, PINNED (unchanged for the SHADOW adapter and Kalshi)
# ═════════════════════════════════════════════════════════════════════

def test_the_base_rule_enlarged_and_read_no_market():
    """scale_qty / plan_buy (the whole-contract half-even rule the SMALL LIVE
    parity adapter and kalshi_orders still use) size 2.702 as 3 -- above the
    1:1000 target -- and read nothing of the market: the defect the ACTUAL
    lane no longer has."""
    assert M.scale_qty(2702, 1000)[1] == 3
    p = M.plan_buy({"us_market_slug": "s", "intent": "ORDER_INTENT_BUY_LONG",
                    "qty": Decimal(2702), "wire_price": Decimal("0.555"),
                    "time_in_force": "IOC", "order_type": "MARKETABLE"},
                   scale=1000, buying_power=100, max_order_usd=25)
    # off any 0.01 tick and still planned: the base never checked it
    assert p.state == "PLANNED" and p.live_qty == 3
    assert p.params["price"]["value"] == "0.5550"
    s = M.size_to_market(2702, 1000, M.market_rules(_market("s")))
    assert s["live_qty"] == 2 and s["rounding_delta"] == Decimal("-0.702")


# ═════════════════════════════════════════════════════════════════════
# §1 THE MARKET'S OWN RECORD, OR A REFUSAL BY NAME
# ═════════════════════════════════════════════════════════════════════

def test_the_measured_market_record_states_its_rules():
    """The venue's own listing row (preserved verbatim from production,
    2026-09-25): minimumTradeQty 0.01 and orderPriceMinTickSize 0.01 -> a
    fractional market, steps of 0.01 contracts, a 0.01 tick."""
    with open(FIXTURE) as f:
        listing = json.load(f)["listing_market"]
    r = M.market_rules(listing, slug=listing["slug"])
    assert r["ok"] is True and r["refusal"] is None and r["missing"] == []
    assert (r["minimum_trade_qty"], r["quantity_increment"], r["price_tick"]) == (
        Decimal("0.01"), Decimal("0.01"), Decimal("0.01"))
    assert r["fields"] == {"minimum_trade_qty": "minimumTradeQty",
                           "price_tick": "orderPriceMinTickSize"}
    assert r["increment_basis"].startswith("DOCUMENTED")
    # the get-market-by-slug envelope is read the same way
    assert M.market_rules({"market": listing})["ok"] is True


@pytest.mark.parametrize("minimum,step", [
    ("0.01", "0.01"), (0.01, "0.01"), ("0.25", "0.01"),
    ("1", "1"), (1.0, "1"), ("5", "1"), (5, "1"),
])
def test_the_documented_step_follows_the_markets_minimum(minimum, step):
    r = M.market_rules(_market("m", minimum=minimum))
    assert r["ok"] is True and r["quantity_increment"] == Decimal(step)
    assert r["minimum_trade_qty"] == Decimal(str(minimum))


@pytest.mark.parametrize("record,code", [
    (None, M.R_MARKET_RECORD_UNREADABLE),
    ({}, M.R_MARKET_RECORD_UNREADABLE),
    ("not a record", M.R_MARKET_RECORD_UNREADABLE),
    (_market("m", minimum=None), M.R_MIN_QTY_ABSENT),
    (_market("m", minimum=""), M.R_MIN_QTY_ABSENT),
    (_market("m", minimum="0"), M.R_MIN_QTY_ABSENT),
    (_market("m", minimum="-1"), M.R_MIN_QTY_ABSENT),
    (_market("m", minimum="abc"), M.R_MIN_QTY_ABSENT),
    (_market("m", minimum=True), M.R_MIN_QTY_ABSENT),
    (_market("m", minimum="0.005"), M.R_QTY_INCREMENT_NOT_ESTABLISHED),
    (_market("m", minimum="2.5"), M.R_QTY_INCREMENT_NOT_ESTABLISHED),
    (_market("m", tick=None), M.R_PRICE_TICK_ABSENT),
    (_market("m", tick="0"), M.R_PRICE_TICK_ABSENT),
    (_market("m", tick="1"), M.R_PRICE_TICK_ABSENT),
    (_market("m", tick="x"), M.R_PRICE_TICK_ABSENT),
])
def test_missing_or_undocumented_metadata_is_refused_by_name(record, code):
    r = M.market_rules(record)
    assert r["ok"] is False and r["refusal"] == code, r
    assert code in r["missing"]
    # nothing is ever assumed: an unusable field stays None
    if code == M.R_MIN_QTY_ABSENT:
        assert r["minimum_trade_qty"] is None and r["quantity_increment"] is None
    if code == M.R_PRICE_TICK_ABSENT:
        assert r["price_tick"] is None


def test_every_missing_field_is_named_and_another_markets_record_refused():
    r = M.market_rules({"slug": "m", "question": "?"})
    assert r["missing"] == [M.R_MIN_QTY_ABSENT, M.R_PRICE_TICK_ABSENT]
    assert r["refusal"] == M.R_MIN_QTY_ABSENT
    other = M.market_rules(_market("another-market"), slug="this-market")
    assert other["ok"] is False and other["refusal"] == M.R_MARKET_RECORD_UNREADABLE
    assert "another-market" in other["why"]


# ═════════════════════════════════════════════════════════════════════
# §2 THE SIZE: ROUNDED DOWN TO THE MARKET'S STEP, NEVER ENLARGED
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("inc,unit,step", [
    ("0.01", "1", "1"), ("1", "1", "1"), ("0.3", "1", "3"),
    ("0.01", "0.01", "0.01"), ("0.25", "0.1", "0.5"), ("5", "1", "5"),
])
def test_the_lane_step_is_the_least_common_multiple(inc, unit, step):
    assert M.lane_qty_step(Decimal(inc), Decimal(unit)) == Decimal(step)


def test_fifteen_hundred_paper_dollars_at_half_is_three_contracts():
    """$1,500 PAPER at 0.50 = 3,000 contracts -> 3 live contracts / $1.50 on
    a whole-contract market (increment 1) and on the fractional one."""
    for minimum in ("1", "0.01"):
        rules = M.market_rules(_market("m", minimum=minimum))
        s = M.size_to_market(Decimal(1500) / Decimal("0.50"), 1000, rules)
        assert (s["live_qty"], s["rounding_delta"], s["refusal"]) == (
            Decimal(3), Decimal(0), None)
        assert s["basis"] == M.SIZING_BASIS_MARKET
        assert M.collateral_per_contract("ORDER_INTENT_BUY_LONG",
                                         "0.50") * s["live_qty"] == Decimal("1.50")


def test_a_fractional_increment_market_is_sized_exactly_at_its_increment():
    """$1,500 PAPER at 0.62 is 2,419 contracts: 2.419 at 1:1000. At the
    market's own 0.01 increment that is EXACTLY 2.41 ($1.4942 at 0.62), never
    2.42; the lane records and sends whole contracts (its quantity columns
    are integers), so it sends 2 -- never 3 -- and records the exact 2.41."""
    rules = M.market_rules(_market("m", minimum="0.01"))
    exact = M.size_to_market(2419, 1000, rules, unit=Decimal("0.01"))
    assert exact["step"] == Decimal("0.01")
    assert exact["live_qty"] == Decimal("2.41") and exact["refusal"] is None
    assert exact["market_exact_qty"] == Decimal("2.41")
    assert exact["live_qty"] * Decimal("0.62") == Decimal("1.4942")
    lane = M.size_to_market(2419, 1000, rules)
    assert (lane["step"], lane["live_qty"], lane["market_exact_qty"]) == (
        Decimal(1), Decimal(2), Decimal("2.41"))
    assert lane["raw_qty"] == Decimal("2.419")
    rec = M.sizing_record(lane, rules)
    assert (rec["live_qty"], rec["market_exact_qty"], rec["step"],
            rec["lane_unit"], rec["market_increment"]) == (
        "2", "2.41", "1", "1", "0.01")
    assert rec["market_rules"]["minimum_trade_qty"] == "0.01"


def test_below_the_markets_minimum_is_refused_never_enlarged():
    """A market whose minimum is 5 contracts: 3,000 paper (3 at 1:1000) is
    BELOW_VENUE_MINIMUM -- never sent as 5; 5,000 is 5; 7,999 is 7."""
    rules = M.market_rules(_market("m", minimum="5"))
    s = M.size_to_market(3000, 1000, rules)
    assert s["refusal"] == M.BELOW_VENUE_MINIMUM
    assert (s["computed_qty"], s["live_qty"], s["market_minimum"]) == (
        Decimal(3), Decimal(0), Decimal(5))
    assert M.size_to_market(5000, 1000, rules)["live_qty"] == Decimal(5)
    assert M.size_to_market(7999, 1000, rules)["live_qty"] == Decimal(7)
    # below one contract on a fractional market: the lane's own unit binds
    tiny = M.size_to_market(600, 1000, M.market_rules(_market("m")))
    assert (tiny["refusal"], tiny["live_qty"], tiny["market_exact_qty"]) == (
        M.BELOW_VENUE_MINIMUM, Decimal(0), Decimal("0.6"))


def test_the_size_is_never_enlarged_and_short_by_less_than_one_step():
    rng = random.Random(20261010)
    markets = [M.market_rules(_market("m", minimum=m)) for m in
               ("0.01", "1", "5")] + [None]
    for _ in range(4000):
        qty = Decimal(rng.randint(1, 60000)) + Decimal(rng.randint(0, 99)) / 100
        for rules in markets:
            s = M.size_to_market(qty, 1000, rules)
            raw = s["raw_qty"]
            assert s["computed_qty"] <= raw < s["computed_qty"] + s["step"]
            assert s["live_qty"] <= raw
            assert (s["refusal"] is None) == (s["live_qty"] > 0)
            if s["refusal"] is None:
                assert s["live_qty"] % s["step"] == 0
                assert s["live_qty"] >= s["market_minimum"]


def test_without_the_markets_record_the_size_is_the_lanes_unit_labelled():
    s = M.size_to_market(2702, 1000, None)
    assert (s["live_qty"], s["step"], s["market_minimum"], s["basis"]) == (
        Decimal(2), Decimal(1), Decimal(1), M.SIZING_BASIS_LANE_UNIT_ONLY)


# ═════════════════════════════════════════════════════════════════════
# §3 THE LIMIT PRICE ON THE MARKET'S TICK, NEVER RE-PRICED
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("price,tick", [
    ("0.55", "0.01"), ("0.5525", "0.0025"), ("0.555", "0.005"),
    ("0.551", "0.001"), (0.62, "0.01"),
])
def test_a_price_on_the_tick_passes(price, tick):
    assert M.price_tick_refusal(price, M.market_rules(
        _market("m", tick=tick))) is None


@pytest.mark.parametrize("price,tick,below,above", [
    ("0.555", "0.01", "0.55", "0.56"),
    ("0.5537", "0.0025", "0.5525", "0.5550"),
    ("0.55", "0.1", "0.5", "0.6"),
])
def test_an_off_tick_price_is_refused_by_name_never_repriced(price, tick,
                                                            below, above):
    off = M.price_tick_refusal(price, M.market_rules(_market("m", tick=tick)))
    assert off["refusal"] == M.R_PRICE_OFF_TICK
    assert (Decimal(off["tick_below"]), Decimal(off["tick_above"])) == (
        Decimal(below), Decimal(above))
    assert off["wire_price"] == price and "canonical intent" in off["never_repriced"]


def test_a_price_the_order_would_change_or_outside_the_unit_interval_is_refused():
    fine = M.market_rules(_market("m", tick="0.00001"))
    off = M.price_tick_refusal("0.55005", fine)          # on the tick ...
    assert off["refusal"] == M.R_PRICE_OFF_TICK          # ... not at 4 dp
    assert off["price_sent"] != off["wire_price"]
    for p in ("0", "1", "1.01", "-0.5"):
        assert M.price_tick_refusal(p, M.market_rules(_market("m")))[
            "refusal"] == M.R_PRICE_OFF_TICK


def _order(qty, wire="0.50", intent="ORDER_INTENT_BUY_LONG"):
    return {"us_market_slug": "m", "intent": intent, "qty": Decimal(str(qty)),
            "wire_price": Decimal(wire), "time_in_force": "IOC",
            "order_type": "MARKETABLE"}


def test_the_plan_keeps_the_caps_on_the_market_size():
    rules = M.market_rules(_market("m", minimum="1"))
    cap = M.plan_entry_on_market(_order(3000), scale=1000, buying_power=100,
                                 max_order_usd="1.00", rules=rules)
    assert (cap.state, cap.exclusion, cap.live_qty) == (
        "EXCLUDED", M.ABOVE_ORDER_CAP, 3)                 # never shrunk to 2
    assert (cap.detail["cost_usd"], cap.detail["cap_usd"]) == ("1.50", "1.00")
    ok = M.plan_entry_on_market(_order(3000), scale=1000, buying_power=100,
                                max_order_usd="1.50", rules=rules)
    assert ok.state == "PLANNED" and ok.live_qty == 3
    assert ok.params["quantity"] == 3 and isinstance(ok.params["quantity"], int)
    assert ok.params["price"] == {"value": "0.5000", "currency": "USD"}
    assert ok.detail["sizing_basis"] == M.SIZING_BASIS_MARKET
    cash = M.plan_entry_on_market(_order(3000), scale=1000, buying_power="1.00",
                                  max_order_usd="25", rules=rules)
    assert cash.exclusion == M.INSUFFICIENT_CASH
    short = M.plan_entry_on_market(_order(3000, "0.80", "ORDER_INTENT_BUY_SHORT"),
                                   scale=1000, buying_power=100,
                                   max_order_usd="25", rules=rules)
    assert short.state == "PLANNED" and short.detail["cost_usd"] == "0.60"
    off = M.plan_entry_on_market(_order(3000, "0.555"), scale=1000,
                                 buying_power=100, max_order_usd="25",
                                 rules=rules)
    assert off.exclusion == M.R_PRICE_OFF_TICK and off.params == {}
    five = M.plan_entry_on_market(_order(3000), scale=1000, buying_power=100,
                                  max_order_usd="25",
                                  rules=M.market_rules(_market("m", minimum="5")))
    assert (five.exclusion, five.live_qty) == (M.BELOW_VENUE_MINIMUM, 0)
    bad = M.plan_entry_on_market(_order(3000), scale=1000, buying_power=100,
                                 max_order_usd="25",
                                 rules=M.market_rules(_market("m", tick=None)))
    assert bad.exclusion == M.R_PRICE_TICK_ABSENT and bad.params == {}


def test_audreys_check_follows_the_rule_that_sized_the_row():
    floor = {"step": "1", "rule": M.ENTRY_SIZING_RULE}
    assert not M.live_qty_rule_broken(2, "2.702", floor)       # floored
    assert M.live_qty_rule_broken(3, "2.702", floor)           # enlarged
    assert M.live_qty_rule_broken(1, "2.702", floor)           # a step short
    assert not M.live_qty_rule_broken(2, "2.702", json.dumps(floor))
    # a row sized before this rule (nearest whole contract): as before
    assert not M.live_qty_rule_broken(3, "2.702", None)
    assert M.live_qty_rule_broken(2, "2.702", None)


# ═════════════════════════════════════════════════════════════════════
# §4 THROUGH THE ACTUAL LANE (real Postgres, FakeVenue, test-only mode)
# ═════════════════════════════════════════════════════════════════════

async def _lane_world(conn, monkeypatch, *, cap=25, bp=100.0):
    acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=cap)
    venue.bp = bp
    await mirror.snapshot(conn, await M.control(conn))
    return acct, venue, mirror


async def _intent(conn, po):
    r = await conn.fetchrow("SELECT * FROM execution_intents WHERE intent_id = $1",
                            po["intent_id"])
    return dict(r, evidence=_j(r["evidence"]))


async def _refusal(conn, po):
    it = await _intent(conn, po)
    return it["actual_refusal"], it["evidence"].get("actual_refusal_evidence") or {}


@pg
async def test_the_lane_sends_three_contracts_for_fifteen_hundred_paper_dollars(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch, cap=1.5)
        _live_mode(monkeypatch)
        po = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        venue.markets[po["slug"]] = _market(po["slug"], minimum="1", tick="0.01")
        venue.behaviour = [{"fill": 3}]
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == EI.A_SUBMITTED, res
        assert venue.market_reads == [po["slug"]]
        assert [p["quantity"] for p in venue.placed] == [3]
        assert venue.placed[0]["price"]["value"] == "0.5000"
        it = await _intent(conn, po)
        assert (it["live_qty"], it["live_raw_qty"], it["rounding_delta"]) == (
            3, Decimal(3), Decimal(0))
        ls = it["evidence"]["live_sizing"]
        assert ls["basis"] == M.SIZING_BASIS_MARKET
        assert [Decimal(ls[k]) for k in ("live_qty", "step", "market_minimum")] == [
            3, 1, 1]
        assert Decimal(ls["market_rules"]["price_tick"]) == Decimal("0.01")
        row = await TE._row(conn, po["order_id"])
        assert row["live_qty"] == 3
        assert Decimal(_j(row["detail"])["cost_usd"]) == Decimal("1.50")
    finally:
        await conn.close()


@pg
async def test_the_lane_sizes_a_fractional_market_down_and_records_the_exact_size(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2419, wire=0.62)
        venue.markets[po["slug"]] = _market(po["slug"], minimum="0.01")
        venue.behaviour = [{"fill": 2}]
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == EI.A_SUBMITTED, res
        assert [p["quantity"] for p in venue.placed] == [2]          # never 3
        ls = (await _intent(conn, po))["evidence"]["live_sizing"]
        assert [Decimal(ls[k]) for k in ("raw_qty", "live_qty", "market_exact_qty",
                                         "market_increment", "lane_unit")] == [
            Decimal("2.419"), 2, Decimal("2.41"), Decimal("0.01"), 1]
    finally:
        await conn.close()


@pg
async def test_the_lane_refuses_below_a_minimum_of_five_and_never_enlarges(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        po = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        venue.markets[po["slug"]] = _market(po["slug"], minimum="5")
        res = await mirror.lane._run(conn, po["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", M.BELOW_VENUE_MINIMUM)
        assert venue.placed == []
        code, ev = await _refusal(conn, po)
        assert code == M.BELOW_VENUE_MINIMUM
        assert [Decimal(ev[k]) for k in ("computed_qty", "market_minimum",
                                         "step")] == [3, 5, 1]   # never 5
        it = await _intent(conn, po)
        assert it["live_qty"] == 0
        assert Decimal(it["evidence"]["live_sizing"]["computed_qty"]) == 3
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await conn.close()


@pg
@pytest.mark.parametrize("record,code", [
    ("missing_min", M.R_MIN_QTY_ABSENT),
    ("missing_tick", M.R_PRICE_TICK_ABSENT),
    ("undocumented_min", M.R_QTY_INCREMENT_NOT_ESTABLISHED),
    ("read_fails", M.R_MARKET_RECORD_UNREADABLE),
    ("other_market", M.R_MARKET_RECORD_UNREADABLE),
])
async def test_missing_market_metadata_refuses_by_name_with_nothing_placed(
        monkeypatch, record, code):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        po = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        s = po["slug"]
        venue.markets[s] = {
            "missing_min": _market(s, minimum=None),
            "missing_tick": _market(s, tick=None),
            "undocumented_min": _market(s, minimum="0.005"),
            "read_fails": TE._Err(503, "venue unavailable"),
            "other_market": _market("some-other-market"),
        }[record]
        res = await mirror.lane._run(conn, po["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", code), res
        assert venue.placed == [] and venue.market_reads == [s]
        got, ev = await _refusal(conn, po)
        assert got == code and ev["market_rules"]["ok"] is False
        assert code in ev["market_rules"]["missing"]
        if record == "read_fails":
            assert ev["market_rules"]["error"]
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await conn.close()


@pg
async def test_an_off_tick_limit_is_refused_by_name_and_never_repriced(monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        po = await TE._paper_order(conn, acct, qty=3000, wire=0.555)
        venue.markets[po["slug"]] = _market(po["slug"], tick="0.01")
        res = await mirror.lane._run(conn, po["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", M.R_PRICE_OFF_TICK)
        assert (Decimal(res["tick_below"]), Decimal(res["tick_above"])) == (
            Decimal("0.55"), Decimal("0.56"))
        assert venue.placed == []
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await conn.close()


@pg
async def test_the_caps_still_bind_on_the_market_size(monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch, cap=1.0)
        _live_mode(monkeypatch)
        big = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        venue.markets[big["slug"]] = _market(big["slug"], minimum="1")
        res = await mirror.lane._run(conn, big["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", M.ABOVE_ORDER_CAP)
        assert (Decimal(res["cost_usd"]), Decimal(res["cap_usd"])) == (
            Decimal("1.50"), Decimal("1.00"))
        assert (await _intent(conn, big))["live_qty"] == 3       # never shrunk
        await conn.execute("UPDATE execmirror_control SET max_order_usd = 25"
                           " WHERE id = 1")
        mirror._buying_power = 1.00
        poor = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        venue.markets[poor["slug"]] = _market(poor["slug"], minimum="1")
        res = await mirror.lane._run(conn, poor["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", M.INSUFFICIENT_CASH)
        assert venue.placed == []
    finally:
        await conn.close()


@pg
async def test_audrey_reconciles_a_floored_actual_size_as_matched(monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        grp = "paper_group_%s" % uuid.uuid4().hex[:8]
        did = await TE._decision(conn, acct, "mlb-test-%s" % grp[-4:])
        po = await TE._paper_order(conn, acct, qty=2702, group=grp, decision_id=did)
        venue.markets[po["slug"]] = _market(po["slug"])
        venue.behaviour = [{"fill": 2}]
        assert (await mirror.lane._run(conn, po["intent_id"]))["state"] == EI.A_SUBMITTED
        await TE._paper_fill(conn, acct, po, qty=2702)
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", grp)
        disc = [d["code"] for d in _j(rec["discrepancies"])]
        assert "LIVE_QTY_NOT_THE_ROUNDED_SCALED_QTY" not in disc, disc
        assert rec["status"] == "MATCHED", (rec["status"], disc)
        # a row ENLARGED above its scaled quantity is named
        await conn.execute("UPDATE execmirror_orders SET live_qty = 3"
                           " WHERE group_id = $1", grp)
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", grp)
        assert "LIVE_QTY_NOT_THE_ROUNDED_SCALED_QTY" in [
            d["code"] for d in _j(rec["discrepancies"])]
    finally:
        await conn.close()


@pg
async def test_in_shadow_the_lane_reads_no_market_and_labels_its_size(monkeypatch):
    """SHADOW (as shipped), the authorization stated by the lane-mechanics
    fixture: the market's record is NOT read (no wire call) and the size is
    the lane's unit rule alone, labelled so on the record."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        assert EI._small_live_is_shadow() is True
        po = await TE._paper_order(conn, acct, qty=2702, wire=0.50)
        venue.behaviour = [{"fill": 2}]
        await mirror.lane._run(conn, po["intent_id"])
        assert venue.market_reads == []
        ls = (await _intent(conn, po))["evidence"]["live_sizing"]
        assert (ls["basis"], Decimal(ls["live_qty"]), ls["market_rules"]) == (
            M.SIZING_BASIS_LANE_UNIT_ONLY, 2, None)
    finally:
        await conn.close()


def test_either_module_saying_shadow_keeps_the_lane_off_the_wire(monkeypatch):
    assert EI._small_live_is_shadow() is True
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", TEST_MODE)
    assert EI._small_live_is_shadow() is True          # live_parity still SHADOW
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", LA.SHADOW)
    monkeypatch.setattr(LP, "SMALL_LIVE_MODE", TEST_MODE)
    assert EI._small_live_is_shadow() is True          # live_authorization SHADOW
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", TEST_MODE)
    assert EI._small_live_is_shadow() is False


# ═════════════════════════════════════════════════════════════════════
# §5 THE WIRE: SHADOW IS INERT; OUTSIDE IT THE MARKET IS READ FIRST
# ═════════════════════════════════════════════════════════════════════

nacl_signing = pytest.importorskip("nacl.signing")


class _Wire:
    """A scripted Polymarket US API behind httpx.MockTransport: the public
    gateway's get-market-by-slug and the authenticated orders endpoints;
    every request that reaches the socket is recorded, anything unscripted
    answers 418."""

    def __init__(self, market: dict):
        self.market = market
        self.seen: list = []
        self.bodies: list = []
        self.lock = threading.Lock()
        self.orders: dict = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            m, host, p = request.method, request.url.host, request.url.path
            self.seen.append((m, host, p))
            body = json.loads(request.content) if request.content else None
            self.bodies.append(body)
            if (m, host) == ("GET", "gateway.polymarket.us") and \
                    p == "/v1/market/slug/%s" % self.market["slug"]:
                assert "X-PM-Signature" not in request.headers   # public read
                return httpx.Response(200, json={"market": self.market},
                                      request=request)
            if (m, host, p) == ("POST", "api.polymarket.us", "/v1/orders"):
                oid = "ord-%d" % (len(self.orders) + 1)
                q = body["quantity"]
                self.orders[oid] = {
                    "id": oid, "marketSlug": body["marketSlug"],
                    "intent": body["intent"], "quantity": q,
                    "price": body["price"], "tif": body["tif"],
                    "cumQuantity": str(q), "avgPx": body["price"],
                    "commissionNotionalTotalCollected": {"value": "0.03"},
                    "state": "ORDER_STATE_FILLED"}
                return httpx.Response(200, json={"id": oid, "executions": []},
                                      request=request)
            if m == "GET" and host == "api.polymarket.us" and \
                    p.startswith("/v1/order/"):
                return httpx.Response(200, json={"order": dict(
                    self.orders[p.rsplit("/", 1)[1]])}, request=request)
            return httpx.Response(418, json={"message": "NOT_SCRIPTED"},
                                  request=request)


def _canonical_stand_in(monkeypatch) -> list:
    """live_parity.authorize_live_exposure replaced by its own tail: the
    token is whatever live_parity.issue_live_authorization issues in the
    current mode (a real LiveAuthorization outside SHADOW, None in SHADOW ->
    the production refusal); governance stated admissible by the test."""
    issued: list = []

    async def authorize(conn, *, decision_id=None, named=None, order=None,
                        now=None, **kw):
        tok = LP.issue_live_authorization(
            {"intent_id": "cdi_test_%s" % decision_id, "content_sha": "0" * 64},
            governance={"admissible": True, "stated_by_test": True},
            now=time.time() if now is None else now)
        if not LP.canonical_live_authorized(tok):
            return {"ok": False, "refusal": LP.R_NO_LIVE_AUTHORIZATION,
                    "detail": {"mode": LP.SMALL_LIVE_MODE}, "token": None}
        issued.append(tok)
        return {"ok": True, "refusal": None, "token": tok,
                "detail": {"intent_id": tok.intent_id}}
    monkeypatch.setattr(LP, "authorize_live_exposure", authorize)
    return issued


async def _wire_world(conn, monkeypatch):
    """The production adapter (M.Venue() from the PMUS_EXECMIRROR names, the
    pinned SDK, retries off) over a mock socket, with a throwaway Ed25519
    key; the lane's world from tests/test_execmirror (cap $1.50)."""
    acct, _fake, mirror = await TE._setup(conn, monkeypatch, cap=1.5)
    sk = nacl_signing.SigningKey.generate()
    kid = str(uuid.uuid4())
    monkeypatch.setenv(EP.KEY_ID_ENV, kid)
    monkeypatch.setenv(EP.SECRET_ENV, base64.b64encode(bytes(sk)).decode())
    await conn.execute("UPDATE execmirror_control SET account_fingerprint = $1"
                       " WHERE id = 1", EP.fingerprint(kid))
    await conn.execute(
        """INSERT INTO execmirror_snapshots (account_fingerprint, balances,
             positions, open_orders, reconciliation)
           VALUES ('fp', '[{"currency": "USD", "buyingPower": 100,
                            "currentBalance": 100}]'::jsonb, '[]'::jsonb, 0,
                   '{"reconciled": true, "differences": {}}'::jsonb)""")
    monkeypatch.setattr(M, "PACE_S", 0.0)
    VP._penalty_until = 0.0
    v = M.Venue()
    assert v._c.max_retries == 0
    mirror._venue = v
    mirror._buying_power = None
    return acct, mirror, v


@pg
async def test_shadow_never_touches_the_wire_and_outside_it_the_market_is_read_first(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, mirror, v = await _wire_world(conn, monkeypatch)
        issued = _canonical_stand_in(monkeypatch)
        # ── SHADOW, AS SHIPPED ──
        po = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        wire = _Wire(_market(po["slug"], minimum="1", tick="0.01"))
        v._c._http = httpx.Client(transport=httpx.MockTransport(wire.handler))
        res = await mirror.lane._run(conn, po["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", LP.R_NO_LIVE_AUTHORIZATION)
        assert wire.seen == [] and issued == []          # NOTHING on the wire
        ls = (await _intent(conn, po))["evidence"]["live_sizing"]
        assert ls["basis"] == M.SIZING_BASIS_LANE_UNIT_ONLY
        assert Decimal(ls["live_qty"]) == 3
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
        # ── TEST-ONLY NON-SHADOW MODE ──
        _live_mode(monkeypatch)
        po2 = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        wire2 = _Wire(_market(po2["slug"], minimum="1", tick="0.01"))
        v._c._http = httpx.Client(transport=httpx.MockTransport(wire2.handler))
        res = await mirror.lane._run(conn, po2["intent_id"])
        assert res["state"] == EI.A_SUBMITTED, (res, wire2.seen)
        assert len(issued) == 1
        assert wire2.seen == [
            ("GET", "gateway.polymarket.us", "/v1/market/slug/%s" % po2["slug"]),
            ("POST", "api.polymarket.us", "/v1/orders"),
            ("GET", "api.polymarket.us", "/v1/order/ord-1")], wire2.seen
        create = wire2.bodies[1]
        assert create == {"marketSlug": po2["slug"],
                          "intent": "ORDER_INTENT_BUY_LONG",
                          "type": "ORDER_TYPE_LIMIT",
                          "price": {"value": "0.5000", "currency": "USD"},
                          "quantity": 3,
                          "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                          "synchronousExecution": True}
        ls = (await _intent(conn, po2))["evidence"]["live_sizing"]
        assert ls["basis"] == M.SIZING_BASIS_MARKET
        assert Decimal(ls["market_rules"]["minimum_trade_qty"]) == 1
        # a market whose minimum is 5: read, refused, nothing created
        po3 = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        wire3 = _Wire(_market(po3["slug"], minimum="5", tick="0.01"))
        v._c._http = httpx.Client(transport=httpx.MockTransport(wire3.handler))
        res = await mirror.lane._run(conn, po3["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", M.BELOW_VENUE_MINIMUM)
        assert wire3.seen == [
            ("GET", "gateway.polymarket.us", "/v1/market/slug/%s" % po3["slug"])]
    finally:
        await conn.close()
