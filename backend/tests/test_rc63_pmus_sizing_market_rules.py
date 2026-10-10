"""CAPITAL-CRITICAL (rc6.3 pmus-sizing; Issue #6 gate 6, contract audit item
3d): THE ACTUAL BUY IS SIZED AND PRICED ON THE MARKET'S OWN TRADING RULES.

THE DEFECT, REPRODUCED ON THE BASE (34cb9544). execmirror.scale_qty sized the
ACTUAL BUY as paper qty / 1,000 rounded HALF-EVEN to whole contracts and never
read the market: 2,702 paper contracts went out as 3 (ABOVE the 1:1000
target), $1,500 PAPER at 0.62 (2,419) went out as 2 ($1.24, not ~$1.50) on a
market that trades in 0.01-contract steps, a market whose minimum is above one
contract would have been sent below it, and no limit price was checked
against the market's tick (§0 pins scale_qty / plan_buy, unchanged for the
SHADOW parity adapter and Kalshi).

THE RULE NOW (execmirror.MARKET_RULES_VERSION; execution_intent steps 2b-4c):
  §1  the market's minimumTradeQty, its quantity increment (the documented
      step its minimum selects: 0.01 below one contract, 1 for whole
      contracts) and its orderPriceMinTickSize come from the market's own
      record; any of them absent, unreadable, outside the documentation, or
      a record that does not name the market, is refused BY NAME -- never
      assumed to be 1;
  §2  the size is ROUNDED DOWN to the market's OWN increment -- 2.419 -> 2.41
      on a 0.01 market, sent as the decimal quantity create-order documents
      for markets whose minimumTradeQty is below 1 (migration 368 stores it
      exactly) -- and below the market's minimum it is BELOW_VENUE_MINIMUM,
      never enlarged; without the market's rules (SHADOW) the lane's whole-
      contract unit alone, and below it R_BELOW_LANE_UNIT, never called a
      venue minimum; fractional inventory is sold exactly (plan_sell);
  §3  the limit price must be on the market's tick and exactly what the order
      carries at 4 decimals -- refused by name, never re-priced;
  §4  through the ACTUAL lane on real Postgres (FakeVenue): $1,500 at 0.50 is
      3 contracts / $1.50; 2,419 at 0.62 is SENT as 2.41, filled, held,
      reconciled by Audrey as MATCHED, shown by the Command Center as 2.41
      and sold back as 2.41; a minimum-5 market refuses 3; missing metadata
      and an off-tick price refuse by name with nothing placed; the per-order
      cap and the buying power still bind; a slow market read can never send
      on a book or decision older than its bound;
  §4b Xavier's ACTUAL management (xavier_management.actual_review_hook, the
      assessor execmirror is wired with) reads the EXACT held quantity: a
      0.83 / 2.41 position is assessed at 0.83 / 2.41 (open_qty, EXIT,
      liquidation, marks; REDUCE floored to the inventory's 0.01 step) and
      the Command Center position room shows it; a whole position as before;
  §5  THE WIRE (the real execmirror.Venue on the pinned SDK over
      httpx.MockTransport): in SHADOW (as shipped) NOTHING reaches the wire
      and every intent -- 0.833 contracts included -- is refused by its true
      reason, the absent canonical authorization; for EVERY combination of
      the two mode constants, no order is POSTed unless the market's record
      was read first (a partial activation reads it); 833 at 0.60 POSTs 0.83
      and 2,419 at 0.62 POSTs 2.41; an authorized order without market rules
      is refused (step 4c).

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
from sportsassets import execmirror_view as V
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
    """TEST-ONLY: the activation this release does not have, in BOTH
    modules."""
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
    1:1000 target -- and 2.419 as 2, reading nothing of the market: the
    defect the ACTUAL lane no longer has."""
    assert M.scale_qty(2702, 1000)[1] == 3
    assert M.scale_qty(2419, 1000)[1] == 2
    p = M.plan_buy({"us_market_slug": "s", "intent": "ORDER_INTENT_BUY_LONG",
                    "qty": Decimal(2702), "wire_price": Decimal("0.555"),
                    "time_in_force": "IOC", "order_type": "MARKETABLE"},
                   scale=1000, buying_power=100, max_order_usd=25)
    # off any 0.01 tick and still planned: the base never checked it
    assert p.state == "PLANNED" and p.live_qty == 3
    assert p.params["price"]["value"] == "0.5550"
    assert p.params["quantity"] == 3 and isinstance(p.params["quantity"], int)
    # the market's own rules: 2.70 on a 0.01-step market, 2 on a whole one
    s = M.size_to_market(2702, 1000, M.market_rules(_market("s")))
    assert s["live_qty"] == Decimal("2.70") and s["rounding_delta"] == Decimal("-0.002")
    w = M.size_to_market(2702, 1000, M.market_rules(_market("s", minimum="1")))
    assert w["live_qty"] == Decimal(2)


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


def test_a_record_that_names_no_market_is_not_this_markets():
    """Review (rc6.3 pmus-sizing r1, non-blocking): a slug-less record passed
    as this market's. It cannot be shown to be this market's: refused."""
    rec = {"minimumTradeQty": "1", "orderPriceMinTickSize": "0.01"}
    r = M.market_rules(rec, slug="this-market")
    assert r["ok"] is False and r["refusal"] == M.R_MARKET_RECORD_UNREADABLE
    assert "names no market" in r["why"]
    # without a slug to check (a pure read of the rules) it still parses
    assert M.market_rules(rec)["ok"] is True


# ═════════════════════════════════════════════════════════════════════
# §2 THE SIZE: ROUNDED DOWN TO THE MARKET'S OWN STEP, NEVER ENLARGED
# ═════════════════════════════════════════════════════════════════════

def test_fifteen_hundred_paper_dollars_at_half_is_three_contracts():
    """$1,500 PAPER at 0.50 = 3,000 contracts -> 3 live contracts / $1.50 on
    a whole-contract market (increment 1) and on the fractional one."""
    for minimum in ("1", "0.01"):
        rules = M.market_rules(_market("m", minimum=minimum))
        s = M.size_to_market(Decimal(1500) / Decimal("0.50"), 1000, rules)
        assert (s["live_qty"], s["rounding_delta"], s["refusal"]) == (
            Decimal(3), Decimal(0), None)
        assert s["basis"] == M.SIZING_BASIS_MARKET and not s["decimal_quantity"]
        assert M.collateral_per_contract("ORDER_INTENT_BUY_LONG",
                                         "0.50") * s["live_qty"] == Decimal("1.50")


def test_a_fractional_increment_market_is_sized_exactly_at_its_increment():
    """$1,500 PAPER at 0.62 is 2,419 contracts: 2.419 at 1:1000. On the
    market's own 0.01 increment that is EXACTLY 2.41 ($1.4942 at 0.62) -- the
    size SENT -- never 2.42 and never the whole-contract 2 ($1.24)."""
    rules = M.market_rules(_market("m", minimum="0.01"))
    s = M.size_to_market(2419, 1000, rules)
    assert (s["step"], s["live_qty"], s["refusal"]) == (
        Decimal("0.01"), Decimal("2.41"), None)
    assert s["decimal_quantity"] is True and s["raw_qty"] == Decimal("2.419")
    assert s["live_qty"] * Decimal("0.62") == Decimal("1.4942")
    rec = M.sizing_record(s, rules)
    assert (rec["live_qty"], rec["step"], rec["market_increment"],
            rec["market_minimum"], rec["decimal_quantity"]) == (
        "2.41", "0.01", "0.01", "0.01", True)
    assert rec["market_rules"]["minimum_trade_qty"] == "0.01"
    # on a whole-contract market the same paper decision is 2 contracts
    whole = M.size_to_market(2419, 1000, M.market_rules(_market("m", minimum="1")))
    assert (whole["step"], whole["live_qty"]) == (Decimal(1), Decimal(2))


def test_a_size_below_one_contract_is_sent_where_the_market_accepts_it():
    """THE REVIEW'S R3 (rc6.3 pmus-sizing r1): 833 paper at 1:1000 is 0.833,
    0.83 at the market's 0.01 step -- ABOVE its minimum 0.01. It is planned
    and sent as 0.83, never refused BELOW_VENUE_MINIMUM; 1,875 is 1.87, not
    1; only a size below the market's own minimum is refused, with the true
    numbers in its reason."""
    rules = M.market_rules({"slug": "s", "minimumTradeQty": 0.01,
                            "orderPriceMinTickSize": 0.01}, slug="s")
    o = {"us_market_slug": "s", "intent": "ORDER_INTENT_BUY_LONG",
         "qty": Decimal(833), "wire_price": Decimal("0.60"),
         "time_in_force": "IOC", "order_type": "MARKETABLE"}
    p = M.plan_entry_on_market(o, scale=1000, buying_power=100,
                               max_order_usd=25, rules=rules)
    assert (p.state, p.exclusion, p.live_qty) == ("PLANNED", None, Decimal("0.83"))
    assert p.params["quantity"] == 0.83 and json.dumps(p.params["quantity"]) == "0.83"
    assert p.detail["cost_usd"] == "0.4980"
    assert M.size_to_market(1875, 1000, rules)["live_qty"] == Decimal("1.87")
    tiny = M.plan_entry_on_market(dict(o, qty=Decimal(9)), scale=1000,
                                  buying_power=100, max_order_usd=25, rules=rules)
    assert tiny.exclusion == M.BELOW_VENUE_MINIMUM
    assert "0.009" in tiny.detail["why"] and "minimum 0.01" in tiny.detail["why"]
    assert tiny.detail["computed_qty"] == "0.00"


def test_below_the_markets_minimum_is_refused_never_enlarged():
    """A market whose minimum is 5 contracts: 3,000 paper (3 at 1:1000) is
    BELOW_VENUE_MINIMUM -- never sent as 5; 5,000 is 5; 7,999 is 7. A
    fractional market with minimum 0.25: 0.2 refused, 0.25 and 0.37 sent."""
    rules = M.market_rules(_market("m", minimum="5"))
    s = M.size_to_market(3000, 1000, rules)
    assert s["refusal"] == M.BELOW_VENUE_MINIMUM
    assert (s["computed_qty"], s["live_qty"], s["market_minimum"]) == (
        Decimal(3), Decimal(0), Decimal(5))
    assert M.size_to_market(5000, 1000, rules)["live_qty"] == Decimal(5)
    assert M.size_to_market(7999, 1000, rules)["live_qty"] == Decimal(7)
    q = M.market_rules(_market("m", minimum="0.25"))
    assert M.size_to_market(200, 1000, q)["refusal"] == M.BELOW_VENUE_MINIMUM
    assert M.size_to_market(250, 1000, q)["live_qty"] == Decimal("0.25")
    assert M.size_to_market(379, 1000, q)["live_qty"] == Decimal("0.37")


def test_the_size_is_never_enlarged_and_short_by_less_than_one_step():
    rng = random.Random(20261010)
    markets = [M.market_rules(_market("m", minimum=m)) for m in
               ("0.01", "0.25", "1", "5")] + [None]
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
                assert s["live_qty"] >= s["minimum"]
                M.wire_qty(s["live_qty"])          # always sendable as is
            else:
                assert s["refusal"] == (M.BELOW_VENUE_MINIMUM if rules
                                        else M.R_BELOW_LANE_UNIT)


def test_without_the_markets_record_the_size_is_the_lanes_unit_never_a_venue_minimum():
    """SHADOW reads no market. The size is the lane's whole-contract unit,
    labelled so -- and below one contract it is R_BELOW_LANE_UNIT, never
    BELOW_VENUE_MINIMUM: the market's minimum is unknown (most markets
    accept 0.83)."""
    s = M.size_to_market(2702, 1000, None)
    assert (s["live_qty"], s["step"], s["minimum"], s["basis"]) == (
        Decimal(2), Decimal(1), Decimal(1), M.SIZING_BASIS_LANE_UNIT_ONLY)
    assert s["market_increment"] is None and s["market_minimum"] is None
    t = M.size_to_market(833, 1000, None)
    assert (t["refusal"], t["live_qty"]) == (M.R_BELOW_LANE_UNIT, Decimal(0))
    p = M.plan_entry_on_market(
        {"us_market_slug": "s", "intent": "ORDER_INTENT_BUY_LONG",
         "qty": Decimal(833), "wire_price": Decimal("0.60"),
         "time_in_force": "IOC", "order_type": "MARKETABLE"},
        scale=1000, buying_power=100, max_order_usd=25, rules=None)
    assert p.exclusion == M.R_BELOW_LANE_UNIT and p.params == {}
    assert "not a venue minimum" in p.detail["why"]
    assert p.detail["market_minimum"] is None


def test_a_fractional_quantity_goes_on_the_wire_as_the_documented_number():
    """create-order's quantity is a number (double); decimals only where
    minimumTradeQty < 1. A whole quantity keeps the int body every order has
    always carried; a fractional one is the JSON number; anything finer than
    the 0.01 step is refused before any client, never rounded."""
    assert M.wire_qty(3) == 3 and isinstance(M.wire_qty(Decimal("3.00")), int)
    assert M.wire_qty(Decimal("2.41")) == 2.41
    assert json.dumps({"quantity": M.wire_qty(Decimal("2.41"))}) == '{"quantity": 2.41}'
    for bad in (Decimal("2.419"), Decimal("-0.5"), "0.005"):
        with pytest.raises(ValueError):
            M.wire_qty(bad)
    body = M.venue_params({"us_market_slug": "m", "intent": "ORDER_INTENT_BUY_LONG",
                           "wire_price": Decimal("0.62"), "time_in_force": "IOC"},
                          Decimal("2.41"))
    assert body["quantity"] == 2.41 and body["price"]["value"] == "0.6200"
    # the integer column never under-states; the exact column holds the size
    assert M.live_qty_columns(Decimal("2.41")) == (3, Decimal("2.41"))
    assert M.live_qty_columns(Decimal("0.83")) == (1, Decimal("0.83"))
    assert M.live_qty_columns(Decimal("3.000")) == (3, None)
    assert M.live_qty_columns(0) == (0, None)
    assert M.row_live_qty({"live_qty": 3, "live_qty_exact": Decimal("2.410000")}) \
        == Decimal("2.41")
    assert M.row_live_qty({"live_qty": 2, "live_qty_exact": None}) == 2
    assert M.row_live_qty({"live_qty": 2}) == 2
    assert M.qty_num(Decimal("2.000000")) == 2 and isinstance(
        M.qty_num(Decimal("2.000000")), int)


def test_fractional_inventory_is_sold_exactly_and_whole_inventory_as_before():
    """plan_sell: a close of 2.41 held sells 2.41 (the whole-contract
    truncation sold 2 and stranded 0.41); half of 2.41 is 1.20 (half-even at
    0.01); fractional committed quantities count exactly. Whole inventory is
    unchanged: half of 3 is 2 (an int), a close of 1 is 1."""
    sell = {"order_id": "p", "group_id": "g", "role": "EXIT",
            "us_market_slug": "m", "intent": "ORDER_INTENT_SELL_LONG",
            "wire_price": Decimal("0.70"), "qty": Decimal(2419),
            "order_type": "MARKETABLE", "time_in_force": "IOC"}
    close = M.plan_sell(sell, scale=1000, paper_open_qty=2419,
                        live_held=Decimal("2.41"), live_committed=0,
                        opened_intent="ORDER_INTENT_BUY_LONG")
    assert (close.state, close.live_qty) == ("PLANNED", Decimal("2.41"))
    assert close.params["quantity"] == 2.41 and close.detail["closes"] is True
    half = M.plan_sell(dict(sell, qty=Decimal(1000)), scale=1000,
                       paper_open_qty=2000, live_held=Decimal("2.41"),
                       live_committed=0, opened_intent="ORDER_INTENT_BUY_LONG")
    assert half.live_qty == Decimal("1.20") and half.params["quantity"] == 1.2
    part = M.plan_sell(sell, scale=1000, paper_open_qty=2419, live_held=3,
                       live_committed=Decimal("1.63"),
                       opened_intent="ORDER_INTENT_BUY_LONG")
    assert part.live_qty == Decimal("1.37")
    w = M.plan_sell(dict(sell, qty=Decimal(1000)), scale=1000,
                    paper_open_qty=2000, live_held=3, live_committed=0,
                    opened_intent="ORDER_INTENT_BUY_LONG")
    assert w.live_qty == 2 and isinstance(w.live_qty, int)
    assert w.params["quantity"] == 2 and isinstance(w.params["quantity"], int)
    tiny = M.plan_sell(dict(sell, qty=Decimal(1)), scale=1000,
                       paper_open_qty=5000, live_held=Decimal("0.41"),
                       live_committed=0, opened_intent="ORDER_INTENT_BUY_LONG")
    assert tiny.exclusion == M.BELOW_VENUE_MINIMUM


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
    # a fractional size is capped on its own cost: 2.41 x 0.62 = $1.4942
    frac = M.market_rules(_market("m"))
    over = M.plan_entry_on_market(_order(2419, "0.62"), scale=1000,
                                  buying_power=100, max_order_usd="1.49",
                                  rules=frac)
    assert (over.exclusion, over.live_qty, over.detail["cost_usd"]) == (
        M.ABOVE_ORDER_CAP, Decimal("2.41"), "1.4942")
    fits = M.plan_entry_on_market(_order(2419, "0.62"), scale=1000,
                                  buying_power=100, max_order_usd="1.50",
                                  rules=frac)
    assert fits.state == "PLANNED" and fits.params["quantity"] == 2.41


def test_audreys_check_follows_the_rule_that_sized_the_row():
    floor = {"step": "1", "rule": M.ENTRY_SIZING_RULE}
    assert not M.live_qty_rule_broken(2, "2.702", floor)       # floored
    assert M.live_qty_rule_broken(3, "2.702", floor)           # enlarged
    assert M.live_qty_rule_broken(1, "2.702", floor)           # a step short
    assert not M.live_qty_rule_broken(2, "2.702", json.dumps(floor))
    frac = {"step": "0.01", "rule": M.ENTRY_SIZING_RULE}
    assert not M.live_qty_rule_broken(Decimal("2.41"), "2.419", frac)
    assert M.live_qty_rule_broken(Decimal("2.42"), "2.419", frac)   # enlarged
    assert M.live_qty_rule_broken(Decimal("2.40"), "2.419", frac)   # a step short
    assert M.live_qty_rule_broken(2, "2.419", frac)    # the whole-contract cut
    # a row sized before this rule (nearest whole contract): as before
    assert not M.live_qty_rule_broken(3, "2.702", None)
    assert M.live_qty_rule_broken(2, "2.702", None)


# ═════════════════════════════════════════════════════════════════════
# §4 THROUGH THE ACTUAL LANE (real Postgres, FakeVenue)
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
        assert (it["live_qty"], it["live_qty_exact"], it["live_raw_qty"],
                it["rounding_delta"]) == (3, None, Decimal(3), Decimal(0))
        ls = it["evidence"]["live_sizing"]
        assert ls["basis"] == M.SIZING_BASIS_MARKET
        assert [Decimal(ls[k]) for k in ("live_qty", "step", "market_minimum")] == [
            3, 1, 1]
        assert Decimal(ls["market_rules"]["price_tick"]) == Decimal("0.01")
        row = await TE._row(conn, po["order_id"])
        assert (row["live_qty"], row["live_qty_exact"]) == (3, None)
        assert Decimal(_j(row["detail"])["cost_usd"]) == Decimal("1.50")
        # the market read precedes every clock check (finding 3)
        tl = _j(it["timeline"])
        assert tl["market_rules_end"]["mono_ns"] <= tl["book_check_start"]["mono_ns"]
    finally:
        await conn.close()


@pg
async def test_the_lane_sends_a_fractional_size_and_carries_it_through_the_lifecycle(
        monkeypatch):
    """$1,500 PAPER at 0.62 (2,419) on a 0.01-step market: SENT as 2.41
    ($1.4942), stored exactly (live_qty_exact 2.41, live_qty rounded up to 3),
    filled 2.41 from the venue's own order record, held 2.41, reconciled by
    Audrey as MATCHED, shown by the Command Center as 2.41, and sold back as
    2.41 by the paper exit -- inventory 0, nothing stranded."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        grp = "paper_group_%s" % uuid.uuid4().hex[:8]
        did = await TE._decision(conn, acct, "mlb-test-%s" % grp[-4:])
        po = await TE._paper_order(conn, acct, qty=2419, wire=0.62, group=grp,
                                   decision_id=did)
        venue.markets[po["slug"]] = _market(po["slug"], minimum="0.01")
        venue.behaviour = [{"fill": 2.41}]
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == EI.A_SUBMITTED, res
        assert [p["quantity"] for p in venue.placed] == [2.41]   # never 2
        assert venue.placed[0]["price"]["value"] == "0.6200"
        it = await _intent(conn, po)
        assert (it["live_qty"], it["live_qty_exact"]) == (3, Decimal("2.41"))
        ls = it["evidence"]["live_sizing"]
        assert [Decimal(ls[k]) for k in ("raw_qty", "live_qty", "step",
                                         "market_increment")] == [
            Decimal("2.419"), Decimal("2.41"), Decimal("0.01"), Decimal("0.01")]
        assert ls["decimal_quantity"] is True
        row = await TE._row(conn, po["order_id"])
        assert (row["live_qty"], row["live_qty_exact"]) == (3, Decimal("2.41"))
        assert Decimal(_j(row["detail"])["cost_usd"]) == Decimal("1.4942")
        assert row["state"] == "FILLED" and row["cum_qty"] == Decimal("2.41")
        fills = await conn.fetch("SELECT qty FROM execmirror_fills WHERE mirror_id = $1",
                                 row["mirror_id"])
        assert [f["qty"] for f in fills] == [Decimal("2.41")]
        inv = await M.live_inventory(conn, grp)
        assert inv["held"] == Decimal("2.41") and inv["committed"] == 0
        # AUDREY: the exact quantity, not the integer column's 3
        await TE._paper_fill(conn, acct, po, qty=2419, price=0.62)
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", grp)
        disc = [d["code"] for d in _j(rec["discrepancies"])]
        assert rec["status"] == "MATCHED", (rec["status"], disc)
        link = _j(rec["chain"])["links"][0]
        assert Decimal(str(link["live_intended_qty"])) == Decimal("2.41")
        assert Decimal(link["live_fill_qty"]) == Decimal("2.41")
        # the account snapshot reconciles the fractional position exactly
        snap = await mirror.snapshot(conn, await M.control(conn))
        assert snap["reconciled"] is True, snap
        # THE COMMAND CENTER shows what was sent
        v = await V.view(conn)
        o = next(x for x in v["orders"]
                 if x.get("execution_intent_id") == po["intent_id"])
        assert o["live"]["qty"] == 2.41
        # THE EXIT: the paper position closes, the live side sells 2.41
        ex = await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=2419,
                                   group=grp, wire=0.70)
        venue.behaviour = [{"fill": 2.41}]
        await mirror.tick(conn)
        sell = await TE._row(conn, ex["order_id"])
        assert sell["exclusion"] is None, sell
        assert (sell["live_qty"], sell["live_qty_exact"]) == (3, Decimal("2.41"))
        assert venue.placed[-1]["intent"] == "ORDER_INTENT_SELL_LONG"
        assert venue.placed[-1]["quantity"] == 2.41
        assert (await M.live_inventory(conn, grp))["held"] == 0
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
        assert "below the market's minimum 5" in ev["why"]
        it = await _intent(conn, po)
        assert (it["live_qty"], it["live_qty_exact"]) == (0, None)
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
    ("no_slug", M.R_MARKET_RECORD_UNREADABLE),
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
            "no_slug": {"minimumTradeQty": "1", "orderPriceMinTickSize": "0.01"},
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
        # a fractional size is capped on its own cost too ($1.4942 > $1.00)
        frac = await TE._paper_order(conn, acct, qty=2419, wire=0.62)
        venue.markets[frac["slug"]] = _market(frac["slug"])
        res = await mirror.lane._run(conn, frac["intent_id"])
        assert (res["refusal"], Decimal(res["cost_usd"])) == (
            M.ABOVE_ORDER_CAP, Decimal("1.4942"))
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
        venue.markets[po["slug"]] = _market(po["slug"], minimum="1")
        venue.behaviour = [{"fill": 2}]
        assert (await mirror.lane._run(conn, po["intent_id"]))["state"] == EI.A_SUBMITTED
        assert venue.placed[-1]["quantity"] == 2       # whole market: 2, never 3
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
        # and the EXACT column is what Audrey reads: 2.71 recorded is enlarged
        await conn.execute("UPDATE execmirror_orders SET live_qty = 2,"
                           " live_qty_exact = 2.71 WHERE group_id = $1", grp)
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", grp)
        assert "LIVE_QTY_NOT_THE_ROUNDED_SCALED_QTY" in [
            d["code"] for d in _j(rec["discrepancies"])]
    finally:
        await conn.close()


@pg
async def test_audrey_names_a_fill_above_the_exact_fractional_quantity(monkeypatch):
    """LIVE_FILLED_MORE_THAN_INTENDED compares the venue fills with the
    order's EXACT quantity: 2.42 filled on a 2.41 order is named, although
    the integer column's rounded-up 3 would have hidden it."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        grp = "paper_group_%s" % uuid.uuid4().hex[:8]
        did = await TE._decision(conn, acct, "mlb-test-%s" % grp[-4:])
        po = await TE._paper_order(conn, acct, qty=2419, wire=0.62, group=grp,
                                   decision_id=did)
        venue.markets[po["slug"]] = _market(po["slug"])
        venue.behaviour = [{"fill": 2.41}]
        assert (await mirror.lane._run(conn, po["intent_id"]))["state"] == EI.A_SUBMITTED
        row = await TE._row(conn, po["order_id"])
        await conn.execute(
            """INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id,
                 group_id, us_market_slug, intent, qty, price, fee_usd)
               VALUES ($1,$2,$3,$4,$5,'ORDER_INTENT_BUY_LONG',0.01,0.62,0)""",
            "extra:%s" % row["mirror_id"], row["mirror_id"], row["venue_order_id"],
            grp, po["slug"])
        await TE._paper_fill(conn, acct, po, qty=2419, price=0.62)
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", grp)
        assert "LIVE_FILLED_MORE_THAN_INTENDED" in [
            d["code"] for d in _j(rec["discrepancies"])]
    finally:
        await conn.close()


@pg
async def test_in_shadow_the_lane_reads_no_market_and_labels_its_size(monkeypatch):
    """SHADOW (as shipped), the authorization stated by the lane-mechanics
    fixture: the market's record is NOT read (no wire call); the size is the
    lane's unit rule alone, labelled so; and a size below one contract is
    refused R_BELOW_LANE_UNIT -- never BELOW_VENUE_MINIMUM -- only after the
    canonical check."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        assert EI._authorization_issuable() is False
        po = await TE._paper_order(conn, acct, qty=2702, wire=0.50)
        venue.behaviour = [{"fill": 2}]
        await mirror.lane._run(conn, po["intent_id"])
        assert venue.market_reads == []
        ls = (await _intent(conn, po))["evidence"]["live_sizing"]
        assert (ls["basis"], Decimal(ls["live_qty"]), ls["market_rules"]) == (
            M.SIZING_BASIS_LANE_UNIT_ONLY, 2, None)
        small = await TE._paper_order(conn, acct, qty=833, wire=0.60)
        res = await mirror.lane._run(conn, small["intent_id"])
        assert res["refusal"] == M.R_BELOW_LANE_UNIT, res
        assert "not a venue minimum" in res["why"]
        tl = _j((await _intent(conn, small))["timeline"])
        assert "canonical_check_start" in tl          # refused AFTER 4b
        assert venue.market_reads == [] and len(venue.placed) == 1
    finally:
        await conn.close()


@pg
@pytest.mark.parametrize("which", ["book", "decision"])
async def test_a_slow_market_read_never_sends_on_a_stale_book_or_decision(
        monkeypatch, which):
    """THE REVIEW'S R2 (rc6.3 pmus-sizing r1): the market read came after the
    10 s book / decision checks, so a slow but successful read sent an IOC on
    a 14 s-old book. The read is now made BEFORE those checks and every age
    is measured after it: the slow read refuses by the stale code, nothing
    placed."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        if which == "book":
            po = await TE._paper_order(conn, acct, qty=3000, wire=0.50,
                                       book_offset_s=-9.5)
            code = EI.R_BOOK_STALE
        else:
            po = await TE._paper_order(conn, acct, qty=3000, wire=0.50,
                                       decided_offset_s=-9.5, book_offset_s=9.3)
            code = EI.R_DECISION_STALE
        venue.markets[po["slug"]] = _market(po["slug"], minimum="1")
        real_market = venue.market

        def slow_market(slug):
            time.sleep(1.0)             # a slow but successful venue read
            return real_market(slug)
        venue.market = slow_market
        venue.behaviour = [{"fill": 3}]
        res = await mirror.lane._run(conn, po["intent_id"])
        assert (res["state"], res["refusal"]) == ("REFUSED", code), res
        assert venue.placed == [] and venue.market_reads == [po["slug"]]
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4b XAVIER'S ACTUAL MANAGEMENT READS THE EXACT INVENTORY (review r2)
# ═════════════════════════════════════════════════════════════════════
#
# THE REVIEW'S BLOCKING FINDING (rc6.3 pmus-sizing r2, reproduced on 99b590b0):
# the lane makes FRACTIONAL ACTUAL positions the normal case on 0.01-step
# markets, but the management assessor execmirror is wired with
# (api/app.py: management_assessor=_XM.actual_review_hook) int()-truncated the
# held quantity: a 0.83 position recorded open_qty 0, HOLD $0.00 and EXIT /
# REDUCE NOTHING_TO_SELL with no liquidation or mark; a 2.41 position recorded
# open_qty 2, EXIT 2 and a liquidation of $0.77 for 2 contracts. The Command
# Center position room shows these rows. Now the exact quantity is read
# throughout; a whole position (3) is recorded exactly as before.

def test_actual_alternatives_size_and_value_the_exact_inventory():
    """Pure: HOLD is held x p; EXIT the whole inventory and REDUCE half of
    it, each floored to the inventory's step (0.01 fractional, 1 whole --
    as plan_sell), fees charged on that exact size; NOTHING_TO_SELL only
    below the step. Whole inventory is valued and sized exactly as before
    (EXIT 4 / REDUCE 2 ints, the existing loop tests' numbers)."""
    from sportsassets.agents import xavier_management as XM
    calls = []

    def fee(q, px, *, at=None):
        calls.append(q)
        return 0.01 * q

    re_ = {"recommended": False, "blocker": XM.B_STALE}
    ev = {"evidence_state": XM.E_FRESH, "probability": 0.5}

    def alts(held):
        calls.clear()
        got = XM.actual_alternatives(evidence=ev, held=held, exit_px=0.8,
                                     fee_fn=fee, at=1.0, reallocate=re_)
        return got, {a["action"]: a for a in got["alternatives"]}, list(calls)

    got, a, fees = alts(Decimal("0.83"))
    assert a[XM.A_HOLD]["value_usd"] == pytest.approx(0.415)
    assert a[XM.A_EXIT]["qty"] == 0.83 and a[XM.A_EXIT]["blocker"] is None
    assert a[XM.A_EXIT]["value_usd"] == pytest.approx(0.83 * 0.8 - 0.0083)
    assert a[XM.A_REDUCE]["qty"] == 0.41          # 0.415 floored to 0.01
    assert a[XM.A_REDUCE]["value_usd"] == pytest.approx(
        0.41 * 0.8 - 0.0041 + 0.42 * 0.5)
    assert fees == pytest.approx([0.83, 0.41])
    assert got["recommendation"] == XM.A_EXIT
    _, a, _ = alts(Decimal("2.41"))
    assert (a[XM.A_EXIT]["qty"], a[XM.A_REDUCE]["qty"]) == (2.41, 1.2)
    assert a[XM.A_HOLD]["value_usd"] == pytest.approx(1.205)
    # below the inventory's step only: 0.01 sells, half of it does not
    _, a, _ = alts(Decimal("0.01"))
    assert a[XM.A_EXIT]["qty"] == 0.01
    assert a[XM.A_REDUCE]["blocker"] == "NOTHING_TO_SELL"
    _, a, _ = alts(Decimal("0.005"))               # finer than the step
    assert a[XM.A_EXIT]["blocker"] == "NOTHING_TO_SELL"
    assert a[XM.A_HOLD]["value_usd"] == pytest.approx(0.0025)
    # whole inventory: exactly as before
    for held in (4, Decimal("4.000000")):
        _, a, fees = alts(held)
        assert (a[XM.A_EXIT]["qty"], a[XM.A_REDUCE]["qty"]) == (4, 2)
        assert isinstance(a[XM.A_EXIT]["qty"], int)
        assert isinstance(a[XM.A_REDUCE]["qty"], int)
        assert fees == [4.0, 2.0]
    _, a, _ = alts(3)
    assert (a[XM.A_EXIT]["qty"], a[XM.A_REDUCE]["qty"]) == (3, 1)
    _, a, _ = alts(1)
    assert a[XM.A_EXIT]["qty"] == 1
    assert a[XM.A_REDUCE]["blocker"] == "NOTHING_TO_SELL"
    _, a, _ = alts(0)
    assert a[XM.A_EXIT]["blocker"] == a[XM.A_REDUCE]["blocker"] == \
        "NOTHING_TO_SELL"
    assert a[XM.A_HOLD]["value_usd"] == 0.0
    # the steps are execmirror.plan_sell's own
    assert XM.held_step(Decimal("2.41")) == M.FRACTIONAL_QTY_STEP
    assert XM.held_step(3) == XM.held_step(Decimal("3.000000")) == \
        M.WHOLE_QTY_STEP


@pg
@pytest.mark.parametrize("paper_qty,minimum,fill,held,exit_q,reduce_q", [
    (833, "0.01", 0.83, Decimal("0.83"), 0.83, 0.41),
    (2419, "0.01", 2.41, Decimal("2.41"), 2.41, 1.2),
    (3000, "1", 3, Decimal(3), 3, 1),
])
async def test_xavier_records_the_exact_actual_position_through_the_mirror_tick(
        monkeypatch, paper_qty, minimum, fill, held, exit_q, reduce_q):
    """Through the ACTUAL lane and mirror.tick with the production wiring
    (probability reader paper_xavier.live_position_evidence, assessor
    xavier_management.actual_review_hook): the ACTUAL assessment records
    open_qty, EXIT and REDUCE at the exact held quantity, the liquidation
    and marks on it, and the Command Center position room shows the same.
    A whole position (3) is recorded exactly as before (ints)."""
    from sportsassets import bettor_paper_ledger as BL
    from sportsassets import position_rooms as PR
    from sportsassets.agents import paper_xavier as PX
    from sportsassets.agents import xavier_management as XM
    from tests import test_xavier_review_probability_freshness as XRF
    conn = await TE._conn()
    po = None
    try:
        acct, venue, mirror = await _lane_world(conn, monkeypatch)
        mirror._probability_reader = PX.live_position_evidence
        mirror._management_assessor = XM.actual_review_hook
        _live_mode(monkeypatch)
        po = await TE._paper_order(conn, acct, qty=paper_qty, wire=0.60)
        now = time.time()
        vid = await XRF._reading(conn, po["slug"], decided_at=now - 3600,
                                 pin_age_s=5.0, p=0.62)
        did = await XRF._decision(conn, acct, slug=po["slug"], vid=vid,
                                  p=0.62, at=now - 3600)
        await conn.execute("UPDATE paper_orders SET decision_id=$2 "
                           " WHERE order_id=$1", po["order_id"], did)
        await conn.execute("UPDATE execution_intents SET decision_id=$2 "
                           " WHERE group_id=$1", po["group_id"], did)
        venue.markets[po["slug"]] = _market(po["slug"], minimum=minimum)
        venue.behaviour = [{"fill": fill}]
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == EI.A_SUBMITTED, res
        assert [p["quantity"] for p in venue.placed] == [fill]
        await TE._paper_fill(conn, acct, po, qty=paper_qty, price=0.60)
        await mirror.tick(conn)
        inv = await M.live_inventory(conn, po["group_id"])
        assert inv["held"] == held
        rev = await conn.fetchrow(
            "SELECT r.live_held, r.detail FROM smalllive_reviews r JOIN "
            " smalllive_handoffs h USING (handoff_id) WHERE h.group_id=$1",
            po["group_id"])
        assert rev["live_held"] == held
        mgmt = _j(rev["detail"])["management"]
        assert mgmt.get("ok", True) is not False, mgmt
        a = dict(await conn.fetchrow(
            "SELECT * FROM xavier_management_assessments WHERE group_id=$1 "
            "   AND position_kind='ACTUAL'", po["group_id"]))
        ve = _j(a["venue_economics"])
        marks = ve["marks"]
        assert marks["open_qty"] == exit_q             # never int()-truncated
        assert isinstance(marks["open_qty"], type(exit_q))
        px = ve["exit_px_held_side"]
        assert px == pytest.approx(0.40)
        fee = float(BL._fee(None, held, px, a["assessed_at"].timestamp()))
        assert ve["liquidation_value_usd"] == pytest.approx(
            float(held) * px - fee, abs=1e-6)
        assert marks["actual_mark_pnl_usd"] == pytest.approx(
            marks["cash_to_date_usd"] + ve["liquidation_value_usd"], abs=1e-6)
        assert marks["exit_net_per_contract"] == pytest.approx(
            ve["liquidation_value_usd"] / float(held), abs=1e-6)
        alts = {x["action"]: x for x in _j(a["alternatives"])}
        assert a["probability"] is not None       # the entry decision's 0.62
        assert alts[XM.A_HOLD]["value_usd"] == pytest.approx(
            float(held) * float(a["probability"]), abs=1e-6)
        assert alts[XM.A_HOLD]["value_usd"] > 0
        assert alts[XM.A_EXIT]["qty"] == exit_q
        assert alts[XM.A_EXIT]["blocker"] != "NOTHING_TO_SELL"
        assert alts[XM.A_REDUCE]["qty"] == reduce_q
        th = await conn.fetchrow(
            "SELECT entry_qty FROM xavier_entry_theses WHERE group_id=$1 "
            "   AND position_kind='ACTUAL'", po["group_id"])
        assert th is not None and Decimal(str(th["entry_qty"])) == held
        # THE COMMAND CENTER POSITION ROOM shows the same exact record
        raw = await PR.load(conn, book=PR.B_ACTUAL, venue=PR.V_PM)
        room = next(r for r in PR.build_rooms(raw)
                    if po["group_id"] in r["groups"])
        xp = next(p for p in room["xavier"] if p["group_id"] == po["group_id"])
        assert xp["status"] == "OK", xp
        assert xp["venue_economics"]["marks"]["open_qty"] == exit_q
        rows = {r["recorded_action"]: r for r in xp["alternatives"]}
        assert rows[XM.A_EXIT]["qty"] == pytest.approx(exit_q)
        assert rows[XM.A_REDUCE]["qty"] == pytest.approx(reduce_q)
    finally:
        if po is not None:
            from tests import test_xavier_review_probability_freshness as XRF
            await XRF._purge(conn, [po["slug"]])
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 THE WIRE: SHADOW IS INERT; NO POST WITHOUT THE MARKET READ FIRST
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
                    "cumQuantity": q, "avgPx": body["price"],
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

    def market_read_before_every_post(self) -> bool:
        """No POST /v1/orders without this market's GET before it."""
        get = ("GET", "gateway.polymarket.us",
               "/v1/market/slug/%s" % self.market["slug"])
        for i, s in enumerate(self.seen):
            if s == ("POST", "api.polymarket.us", "/v1/orders") and \
                    get not in self.seen[:i]:
                return False
        return True


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


def _wired(v, market) -> _Wire:
    w = _Wire(market)
    v._c._http = httpx.Client(transport=httpx.MockTransport(w.handler))
    return w


@pg
async def test_shadow_never_touches_the_wire_and_refuses_by_the_true_reason(
        monkeypatch):
    """SHADOW AS SHIPPED, the production canonical tail: nothing reaches the
    socket -- no market read, no order -- and every intent is refused by the
    absent canonical authorization, 0.833 contracts included (it is NOT
    called BELOW_VENUE_MINIMUM: the market's minimum was never read, and
    most markets accept 0.83). The census of what SHADOW refuses changes
    accordingly: sub-contract intents read NO_LIVE_AUTHORIZATION, their
    lane-unit sizing on the record."""
    conn = await TE._conn()
    try:
        acct, mirror, v = await _wire_world(conn, monkeypatch)
        issued = _canonical_stand_in(monkeypatch)
        for qty in (3000, 833, 300):
            po = await TE._paper_order(conn, acct, qty=qty, wire=0.50)
            wire = _wired(v, _market(po["slug"], minimum="1", tick="0.01"))
            res = await mirror.lane._run(conn, po["intent_id"])
            assert (res["state"], res["refusal"]) == (
                "REFUSED", LP.R_NO_LIVE_AUTHORIZATION), (qty, res)
            assert wire.seen == [] and issued == []      # NOTHING on the wire
            it = await _intent(conn, po)
            assert it["actual_refusal"] == LP.R_NO_LIVE_AUTHORIZATION
            ls = it["evidence"]["live_sizing"]
            assert ls["basis"] == M.SIZING_BASIS_LANE_UNIT_ONLY
            assert ls["refusal"] == (None if qty >= 1000 else M.R_BELOW_LANE_UNIT)
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await conn.close()


@pg
@pytest.mark.parametrize("lp_live,la_live", [
    (False, False), (False, True), (True, False), (True, True)])
async def test_no_order_is_posted_unless_the_market_was_read_first_in_every_mode(
        monkeypatch, lp_live, la_live):
    """THE REVIEW'S R1 (rc6.3 pmus-sizing r1): the read was switched by
    "either module says SHADOW" while the authorization follows live_parity
    alone, so a PARTIAL activation (live_parity not SHADOW, live_
    authorization SHADOW) POSTed quantity 3 on a minimum-5 market at an
    off-tick 0.555 with no market read. Now, for every combination of the two
    mode constants: no POST without the market's GET before it; where an
    authorization can be issued the market is read and its rules refuse
    (off tick, below minimum) or size the order; where none can, nothing at
    all reaches the wire."""
    conn = await TE._conn()
    try:
        acct, mirror, v = await _wire_world(conn, monkeypatch)
        issued = _canonical_stand_in(monkeypatch)
        if lp_live:
            monkeypatch.setattr(LP, "SMALL_LIVE_MODE", TEST_MODE)
        if la_live:
            monkeypatch.setattr(LA, "SMALL_LIVE_MODE", TEST_MODE)
        await conn.execute("UPDATE execmirror_control SET max_order_usd = 25"
                           " WHERE id = 1")
        get = lambda s: ("GET", "gateway.polymarket.us", "/v1/market/slug/%s" % s)  # noqa: E731
        # (a) the review's case: minimum 5, tick 0.01, wire 0.555 (off tick)
        a = await TE._paper_order(conn, acct, qty=3000, wire=0.555)
        wa = _wired(v, _market(a["slug"], minimum="5", tick="0.01"))
        ra = await mirror.lane._run(conn, a["intent_id"])
        # (b) on the tick, still below the minimum of 5
        b = await TE._paper_order(conn, acct, qty=3000, wire=0.55)
        wb = _wired(v, _market(b["slug"], minimum="5", tick="0.01"))
        rb = await mirror.lane._run(conn, b["intent_id"])
        # (c) a fractional market: 833 at 0.60 -> 0.83
        c = await TE._paper_order(conn, acct, qty=833, wire=0.60)
        wc = _wired(v, _market(c["slug"], minimum="0.01", tick="0.01"))
        rc = await mirror.lane._run(conn, c["intent_id"])
        for w in (wa, wb, wc):
            assert w.market_read_before_every_post(), w.seen
        if not lp_live:
            # no authorization can be issued: nothing reaches the wire
            for r in (ra, rb, rc):
                assert r["refusal"] == LP.R_NO_LIVE_AUTHORIZATION, r
            assert wa.seen == wb.seen == wc.seen == [] and issued == []
            return
        assert (ra["refusal"], wa.seen) == (M.R_PRICE_OFF_TICK, [get(a["slug"])])
        assert (rb["refusal"], wb.seen) == (M.BELOW_VENUE_MINIMUM, [get(b["slug"])])
        assert rc["state"] == EI.A_SUBMITTED, (rc, wc.seen)
        assert wc.seen == [get(c["slug"]),
                           ("POST", "api.polymarket.us", "/v1/orders"),
                           ("GET", "api.polymarket.us", "/v1/order/ord-1")]
        assert wc.bodies[1]["quantity"] == 0.83          # the decimal quantity
        assert wc.bodies[1]["price"] == {"value": "0.6000", "currency": "USD"}
        it = await _intent(conn, c)
        assert it["evidence"]["live_sizing"]["basis"] == M.SIZING_BASIS_MARKET
        assert (it["live_qty"], it["live_qty_exact"]) == (1, Decimal("0.83"))
        row = await conn.fetchrow("SELECT * FROM execmirror_orders"
                                  " WHERE execution_intent_id = $1", c["intent_id"])
        assert row["cum_qty"] == Decimal("0.83")
        assert len(issued) == 1
    finally:
        await conn.close()


@pg
async def test_the_wire_carries_two_point_four_one_for_fifteen_hundred_at_sixty_two(
        monkeypatch):
    """THE AUDIT'S OWN EXAMPLE on the real adapter: $1,500 PAPER at 0.62
    (2,419) on a 0.01-step market POSTs quantity 2.41 -- not 2 -- after the
    market's GET; the venue's order record (cumQuantity 2.41) books a 2.41
    fill."""
    conn = await TE._conn()
    try:
        acct, mirror, v = await _wire_world(conn, monkeypatch)
        issued = _canonical_stand_in(monkeypatch)
        _live_mode(monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2419, wire=0.62)
        wire = _wired(v, _market(po["slug"], minimum="0.01", tick="0.01"))
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == EI.A_SUBMITTED, (res, wire.seen)
        assert len(issued) == 1 and wire.market_read_before_every_post()
        create = wire.bodies[wire.seen.index(
            ("POST", "api.polymarket.us", "/v1/orders"))]
        assert create == {"marketSlug": po["slug"],
                          "intent": "ORDER_INTENT_BUY_LONG",
                          "type": "ORDER_TYPE_LIMIT",
                          "price": {"value": "0.6200", "currency": "USD"},
                          "quantity": 2.41,
                          "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                          "synchronousExecution": True}
        row = await conn.fetchrow("SELECT * FROM execmirror_orders"
                                  " WHERE execution_intent_id = $1", po["intent_id"])
        assert (row["live_qty"], row["live_qty_exact"], row["cum_qty"]) == (
            3, Decimal("2.41"), Decimal("2.41"))
        fills = await conn.fetch("SELECT qty FROM execmirror_fills WHERE mirror_id = $1",
                                 row["mirror_id"])
        assert [f["qty"] for f in fills] == [Decimal("2.41")]
    finally:
        await conn.close()


@pg
async def test_an_authorized_order_without_market_rules_is_refused(monkeypatch):
    """STEP 4c, the lane's own invariant: were the read ever switched off
    while an authorization can still be issued (simulated here by forcing
    _authorization_issuable False under a non-SHADOW live_parity), the lane
    refuses the authorized order by name -- nothing reaches the wire."""
    conn = await TE._conn()
    try:
        acct, mirror, v = await _wire_world(conn, monkeypatch)
        issued = _canonical_stand_in(monkeypatch)
        _live_mode(monkeypatch)
        monkeypatch.setattr(EI, "_authorization_issuable", lambda: False)
        po = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        wire = _wired(v, _market(po["slug"], minimum="1", tick="0.01"))
        res = await mirror.lane._run(conn, po["intent_id"])
        assert (res["state"], res["refusal"]) == (
            "REFUSED", M.R_MARKET_RULES_NOT_READ), res
        assert len(issued) == 1 and wire.seen == []
        assert res["sizing_basis"] == M.SIZING_BASIS_LANE_UNIT_ONLY
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await conn.close()


def test_the_read_is_switched_by_the_authorizations_own_condition(monkeypatch):
    """_authorization_issuable is exactly the condition under which
    live_parity issues and verifies an authorization (its own mode only):
    live_authorization's constant plays no part."""
    assert EI._authorization_issuable() is False
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", TEST_MODE)
    assert EI._authorization_issuable() is False       # live_parity still SHADOW
    assert LP.issue_live_authorization(
        {"intent_id": "i", "content_sha": "0" * 64},
        governance={"admissible": True}, now=0.0) is None
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", LA.SHADOW)
    monkeypatch.setattr(LP, "SMALL_LIVE_MODE", TEST_MODE)
    assert EI._authorization_issuable() is True        # an authorization CAN issue
    tok = LP.issue_live_authorization(
        {"intent_id": "i", "content_sha": "0" * 64},
        governance={"admissible": True}, now=0.0)
    assert LP.canonical_live_authorized(tok) is True
