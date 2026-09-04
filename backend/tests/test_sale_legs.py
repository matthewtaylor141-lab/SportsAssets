"""THE SALE-LEG LEDGER (unit U1): the fold's rules, the SQL, and the
gate.

The ledger exists so that a later unit can write a hand sale back onto
the order row it closed. Everything that can go wrong with it goes
wrong in one of three places, so these tests pin all three:

  THE FOLD
  rule 1  the nested execution order's SIDE beats the
          realized-is-non-zero fallback (the fallback alone misread 444
          zero-P&L sells as buys and booked 23 short-CLOSING BUYS as
          sales -- raw-feed audit 2026-08-19, 6,747 trades);
  rule 2  a break-even sale with no nested order is `unknown` and is
          NEVER folded as a buy -- today's record folds exactly that
          row as an entry, which inflates deployed capital;
  rule 3  a redemption and a merge are distinct shapes from a sale and
          can never become legs;
  rule 4  the venue's own realizedPnl is carried, never recomputed from
          a price we reconstruct.

  THE SQL  every statement is asserted clause by clause -- the insert's
          column order against SaleLeg's own fields, each FILTER
          against SIDES_CASH_IN / SIDES_COUNTED, the archive read's
          cursor and type predicate, the census's three side paths.
          Without that, a fake pool that re-implements the queries in
          Python proves only that the Python is consistent with itself:
          the review's mutation transposing proceeds_usd and
          realized_usd in the INSERT passed the whole suite.

  THE GATE  the per-slug sum of the ledger reproduces track_record's
          `sold_markets` TO THE CENT -- checked against track_record's
          OWN fold and against the SERVED field, on a handmade tape
          covering every shape and on randomized ones. sold_markets is
          already correct; if the ledger disagrees with it, the LEDGER
          is wrong. And the gate FAILS CLOSED: an empty yardstick or an
          incomplete sweep is a refusal, never a green tick.

No database exists in this environment, so the database half of the
gate (this ledger, built from the production archive, against the
production sold_markets) is `sale_legs.gate_report` and has to be run
where the data is. What is pinned here is the arithmetic and the SQL it
will run.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import pathlib
import random
import re

import pytest

from sportsassets.analytics import sale_legs as sl
from sportsassets.api import track_record as tr

_T = "ACTIVITY_TYPE_TRADE"
_R = "ACTIVITY_TYPE_POSITION_RESOLUTION"
TS0 = 1785542400.0        # 2026-08-01T00:00:00Z
DAY = 86400.0
MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"
MIGRATION = MIGRATIONS / "052_copy_exit_legs.sql"
#: The checked-in reservation register the migration number obeys.
PROGRAM_MD = (pathlib.Path(__file__).resolve().parents[2] / "docs"
              / "mirror-to-a-tee-program.md")


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    """An unreachable database HANGS asyncpg rather than failing, so
    every helper that could reach for a pool is kept off the wire
    (same contract as tests/test_track_record.py)."""
    from sportsassets import db

    async def _no_pool(*_a, **_k):
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(db, "get_pool", _no_pool)


def _squash(sql: str) -> str:
    """SQL with its formatting collapsed, so a clause can be asserted
    without pinning where the line happens to wrap."""
    return " ".join(sql.split())


def _trade(aid, slug, qty=100, price=0.7, rp=None, *, side=None,
           nested=None, nested_key="aggressorExecution", order_id=None,
           ts=TS0):
    """One venue TRADE activity in the venue's own shape.

    `side` is the (always-absent in this feed) top-level side; `nested`
    is the execution order's side, which is where the truth lives.
    """
    t: dict = {"marketSlug": slug, "qty": str(qty),
               "price": {"value": str(price), "currency": "USD"}}
    if rp is not None:
        t["realizedPnl"] = {"value": str(rp), "currency": "USD"}
    if side is not None:
        t["side"] = side
    if nested is not None or order_id is not None:
        order: dict = {}
        if nested is not None:
            order["side"] = nested
        if order_id is not None:
            order["id"] = order_id
        t[nested_key] = {"order": order}
    act = {"id": aid, "type": _T, "trade": t}
    if ts is not None:
        act["timestamp"] = ts
    return act


def _leg(act):
    leg, reason = sl.fold_activity(act)
    assert reason == "ok", f"expected a leg, got refusal {reason!r}"
    return leg


def _refusal(act):
    leg, reason = sl.fold_activity(act)
    assert leg is None, f"expected a refusal, got {leg}"
    return reason


# ── RULE 1: the nested execution order's side wins ───────────────────


def test_nested_order_side_wins_over_the_realized_fallback():
    """A break-even SELL. The realized fallback alone says "buy"; the
    nested order says SELL, and the nested order wins."""
    leg = _leg(_trade("a1", "nyy-bos", qty=200, price=0.5, rp=0,
                      nested="ORDER_SIDE_SELL", order_id="ord-1"))
    assert leg.side == sl.SIDE_SELL
    assert leg.side_src == sl.SRC_AGGRESSOR
    assert leg.shares == 200 and leg.proceeds_usd == 100.0
    assert leg.realized_usd == 0.0


def test_a_short_closing_buy_realizes_money_but_takes_no_proceeds():
    """rp != 0 with the nested order saying BUY: a SHORT closing. Its
    realized dollars are real; its qty*price is not cash in, and
    booking it as a sale is exactly the 2026-08-19 defect."""
    leg = _leg(_trade("a2", "lal-bos", qty=300, price=0.4, rp=-12.5,
                      nested="ORDER_SIDE_BUY", order_id="ord-2"))
    assert leg.side == sl.SIDE_BUY_CLOSE
    assert leg.realized_usd == -12.5
    assert leg.proceeds_usd == 0.0 and leg.shares == 0.0


def test_a_short_closing_buy_keeps_the_quantity_it_retired():
    """`shares` is the CASH-IN quantity and is 0 here -- but a short is
    CLOSED BY A BUY, and the writer that comes next reduces a position
    by a share count. Zeroing both would discard the exit quantity of
    every short, and a table CHECK would make it unrecoverable without
    a second migration. `trade_qty` carries the venue's raw qty on
    every leg, whatever the side."""
    leg = _leg(_trade("a2b", "lal-bos", qty=300, price=0.4, rp=-12.5,
                      nested="ORDER_SIDE_BUY"))
    assert leg.shares == 0.0            # not cash in
    assert leg.trade_qty == 300.0       # but the quantity is not lost


@pytest.mark.parametrize("kwargs,side", [
    (dict(nested="ORDER_SIDE_SELL", rp=3.0), sl.SIDE_SELL),
    (dict(nested="ORDER_SIDE_BUY", rp=-3.0), sl.SIDE_BUY_CLOSE),
    (dict(rp=0), sl.SIDE_UNKNOWN),
])
def test_trade_qty_is_the_venues_raw_qty_on_every_leg(kwargs, side):
    leg = _leg(_trade("a2c", "nyy-bos", qty=77, price=0.5, **kwargs))
    assert leg.side == side
    assert leg.trade_qty == 77.0


def test_the_passive_execution_is_read_when_the_aggressor_names_nothing():
    leg = _leg(_trade("a3", "nyy-bos", rp=0, nested="ORDER_SIDE_SELL",
                      nested_key="passiveExecution", order_id="ord-3"))
    assert leg.side == sl.SIDE_SELL
    assert leg.side_src == sl.SRC_PASSIVE
    assert leg.order_id == "ord-3"


def test_the_side_naming_execution_is_preferred_over_pmus_trade_order():
    """A deliberate, documented divergence from `pmus.trade_order`.

    That helper returns the first NON-EMPTY nested order; this fold
    takes the first order that names a SIDE, because the side is what
    the classification turns on, and falls back to the first non-empty
    one only when neither names a side. On a trade whose aggressor
    order carries an id and no side while the passive one carries both,
    the two therefore return different orders. Pinned so a later writer
    joining `order_id` against anything built from `recent_trades`
    knows that, rather than discovering it as a missing row.
    """
    from sportsassets import pmus

    t = {"marketSlug": "nyy-bos", "qty": "10", "price": "0.5",
         "realizedPnl": "1.0",
         "aggressorExecution": {"order": {"id": "A-no-side"}},
         "passiveExecution": {"order": {"id": "B-with-side",
                                        "side": "ORDER_SIDE_SELL"}}}
    leg = _leg({"id": "a3b", "type": _T, "timestamp": TS0, "trade": t})
    assert leg.order_id == "B-with-side"
    assert leg.side_src == sl.SRC_PASSIVE
    assert pmus.trade_order(t)["id"] == "A-no-side"


def test_a_stated_top_level_side_is_used_and_labelled():
    """The slim archive row lifts the nested side to the top level and
    drops the order object; that row must still fold, with its side
    labelled as stated rather than inferred. (The ledger reads the RAW
    archive payload, where the top-level side is always absent, so this
    is parity with `_fold_trade`'s own branch, not a shape this feed
    sends.)"""
    leg = _leg(_trade("a4", "nyy-bos", rp=0, side="TRADE_SIDE_SELL"))
    assert leg.side == sl.SIDE_SELL
    assert leg.side_src == sl.SRC_TOP_LEVEL
    assert leg.order_id is None


def test_the_realized_fallback_is_used_but_says_it_inferred_the_side():
    """No side anywhere and realized P&L present: a buy under
    average-cost accounting never carries realized P&L, so this is a
    sale -- and the row records that the side was INFERRED."""
    leg = _leg(_trade("a5", "nyy-bos", qty=50, price=0.6, rp=7.25))
    assert leg.side == sl.SIDE_SELL
    assert leg.side_src == sl.SRC_REALIZED_FALLBACK
    assert leg.realized_usd == 7.25 and leg.proceeds_usd == 30.0


# ── RULE 2: the break-even sale with no nested order ─────────────────


def test_break_even_with_no_nested_order_is_unknown_and_never_a_buy():
    """THE RULE THIS UNIT EXISTS FOR. Nothing the venue sent separates
    this row from a fresh entry. It is stored NAMED, carries no money
    in either direction, and is never folded as a buy."""
    act = _trade("a6", "nyy-bos", qty=120, price=0.45, rp=0)
    leg = _leg(act)
    assert leg.side == sl.SIDE_UNKNOWN
    assert leg.side_src == sl.SRC_NONE
    assert leg.shares == 0.0 and leg.proceeds_usd == 0.0
    assert leg.realized_usd == 0.0
    # It is neither of the two sides that carry money, and it IS
    # disclosed as a count.
    assert leg.side not in sl.SIDES_COUNTED
    fold = sl.fold_activities([act])
    assert fold.counts()["unknown"] == 1
    assert [g.venue_activity_id for g in fold.unknown_legs] == ["a6"]
    # And it contributes nothing to any total.
    assert sl.per_slug(fold.legs)["nyy-bos"]["qty"] == 0.0
    assert sl.per_slug(fold.legs)["nyy-bos"]["proceeds"] == 0.0
    assert sl.per_slug(fold.legs)["nyy-bos"]["realized"] == 0.0


def test_a_missing_realized_field_is_still_the_unknown_population():
    """The venue sends no realizedPnl at all: same undecidable shape,
    same refusal to guess."""
    leg = _leg(_trade("a7", "nyy-bos", rp=None))
    assert leg.side == sl.SIDE_UNKNOWN


def test_break_even_with_a_sideless_nested_order_keeps_the_order_id():
    """An execution order that names no side is still the order this
    trade belongs to. The classification stays `unknown` -- but the id
    survives, which is the one field that can later be taken to the
    venue and asked."""
    leg = _leg(_trade("a8", "nyy-bos", rp=0, order_id="ord-8"))
    assert leg.side == sl.SIDE_UNKNOWN
    assert leg.order_id == "ord-8"


def test_a_stated_buy_at_break_even_is_an_entry_not_an_unknown():
    """The venue's own order said BUY and no money was realized: that
    is an entry, decided, and it is not a leg at all. `unknown` is
    reserved for what cannot be decided."""
    assert _refusal(_trade("a9", "nyy-bos", rp=0,
                           nested="ORDER_SIDE_BUY")) == "entry_buy"


def test_the_undecidable_population_outside_the_band_is_refused_not_stored():
    """An undecidable row whose price is outside (0,1) is REFUSED, not
    stored as an `unknown` leg: `_fold_trade` ignores it too, and the
    gate requires the two to agree. So the ledger's `unknown` count is
    the undecidable population INSIDE the band -- the whole of it is
    what SIDE_CENSUS_SQL measures, which is why that query exists."""
    assert _refusal(_trade("a10", "nyy-bos", qty=80, price=1.4,
                           rp=0)) == "outside_price_band"
    assert "SIDE_CENSUS_SQL" in sl.__doc__


# ── RULE 3: a redemption and a merge are not sales ───────────────────


@pytest.mark.parametrize("kind,name", [
    ("ACTIVITY_TYPE_POSITION_RESOLUTION", "resolution"),
    ("ACTIVITY_TYPE_REDEMPTION", "redemption"),
    ("ACTIVITY_TYPE_POSITION_REDEEM", "redemption"),
    ("ACTIVITY_TYPE_MERGE", "merge"),
    ("ACTIVITY_TYPE_POSITION_MERGE", "merge"),
    ("ACTIVITY_TYPE_SPLIT", "split"),
    ("ACTIVITY_TYPE_CONVERSION", "conversion"),
    ("ACTIVITY_TYPE_ACCOUNT_DEPOSIT", "deposit"),
])
def test_non_trade_shapes_are_refused_under_their_own_name(kind, name):
    """Each of these moves real money and none of them is a SALE: they
    carry no execution order and no price the venue printed, so folding
    one as a sale would invent both. Named, so a refusal is a census
    line and not a silent drop."""
    act = _trade("b1", "nyy-bos", rp=40.0)
    act["type"] = kind
    assert _refusal(act) == f"not_a_sale:{name}"


def test_a_resolution_payload_never_becomes_a_leg():
    act = {"id": "b2", "type": _R, "timestamp": TS0,
           "positionResolution": {"marketSlug": "nyy-bos",
                                  "afterPosition": {"realized": 42.0}}}
    assert _refusal(act).startswith("not_a_sale:")


def test_an_unknown_future_shape_is_refused_not_guessed():
    act = _trade("b3", "nyy-bos", rp=40.0)
    act["type"] = "ACTIVITY_TYPE_SOMETHING_NEW"
    assert _refusal(act) == "not_a_sale:activity_type_something_new"


# ── RULE 4: the venue's realized figure is carried, never recomputed ─


def test_the_venue_realized_figure_is_carried_never_recomputed():
    """Proceeds and realized are DIFFERENT NUMBERS in different
    columns. The venue's realized figure is not proceeds minus a basis
    we reconstruct, and this ledger never tries."""
    leg = _leg(_trade("c1", "nyy-bos", qty=200, price=0.55, rp=-4.13,
                      nested="ORDER_SIDE_SELL"))
    assert leg.realized_usd == -4.13
    assert leg.proceeds_usd == pytest.approx(110.0)
    assert leg.price == 0.55 and leg.shares == 200.0


def test_amount_shapes_are_all_read_the_same_way():
    """{value}, {value,currency}, bare scalars and numeric strings: the
    same readers the record's own fold uses."""
    act = {"id": "c2", "type": _T, "timestamp": TS0,
           "trade": {"marketSlug": "nyy-bos", "qty": 40,
                     "price": "0.25", "realizedPnl": 3.5,
                     "aggressorExecution": {
                         "order": {"side": "ORDER_SIDE_SELL"}}}}
    leg = _leg(act)
    assert leg.shares == 40.0 and leg.price == 0.25
    assert leg.proceeds_usd == pytest.approx(10.0)
    assert leg.realized_usd == 3.5


# ── keys, bands and refusals ─────────────────────────────────────────


def test_the_leg_is_keyed_by_the_venue_activity_id():
    leg = _leg(_trade("act-9915", "nyy-bos", rp=5.0))
    assert leg.venue_activity_id == "act-9915"
    assert leg.source == sl.SOURCE_ARCHIVE


def test_the_archive_row_key_overrides_the_payload_id():
    """Folding from the table, the ROW's key is the ledger's key -- it
    is what pmus_activity_archive stores the payload under, and the two
    tables must join."""
    act = _trade("payload-id", "nyy-bos", rp=5.0)
    leg, reason = sl.fold_activity(act, activity_id="row-key")
    assert reason == "ok" and leg.venue_activity_id == "row-key"


def test_an_activity_with_no_id_is_refused_never_keyed_by_a_guess():
    """The PURE path: handed an activity with no id of its own and no
    archive key, the fold refuses rather than invent one."""
    act = _trade("x", "nyy-bos", rp=5.0)
    del act["id"]
    assert _refusal(act) == "no_activity_id"


def test_a_duplicate_id_is_folded_once():
    """The table is append-only with ON CONFLICT DO NOTHING; the
    in-memory fold says the same thing, so a dry run and a write
    agree."""
    a = _trade("dup", "nyy-bos", qty=10, price=0.5, rp=1.0)
    fold = sl.fold_activities([a, dict(a)])
    assert len(fold.legs) == 1 and fold.duplicates == 1


@pytest.mark.parametrize("qty,price,reason", [
    (0, 0.5, "no_qty"),
    (-5, 0.5, "no_qty"),
    (100, 0.0, "outside_price_band"),
    (100, 1.0, "outside_price_band"),
    (100, 1.5, "outside_price_band"),
    (100, -0.2, "outside_price_band"),
])
def test_trades_outside_the_folds_band_are_refused_by_name(qty, price,
                                                           reason):
    """`_fold_trade` counts a closing trade only inside this band, so
    the ledger must refuse outside it or the gate cannot hold. Nothing
    is lost: the leg's key IS the archive's key."""
    assert _refusal(_trade("d1", "nyy-bos", qty=qty, price=price, rp=3.0,
                           nested="ORDER_SIDE_SELL")) == reason


@pytest.mark.parametrize("field,value", [
    ("qty", "NaN"), ("qty", "Infinity"), ("qty", "-Infinity"),
    ("price", "NaN"), ("price", "Infinity"),
    ("realizedPnl", "NaN"), ("realizedPnl", "Infinity"),
])
def test_a_non_finite_amount_is_refused_by_name(field, value):
    """"NaN" and "Infinity" are legal JSON-text scalars and float()
    takes both.

    NaN is the dangerous one: `nan <= 0` is False so the band guard
    would pass it, while `_fold_trade`'s mirror-image `qty > 0` is also
    False so the record folds NOTHING -- the two disagree and the gate
    breaks on a row carrying no measurable money. Infinity is worse: it
    cannot be stored in NUMERIC(24,6), so one such row would abort the
    whole page's insert every time it was retried. Both refused, by
    name, before any classification reads them."""
    act = _trade("d1b", "nyy-bos", qty=10, price=0.5, rp=1.0,
                 nested="ORDER_SIDE_SELL")
    act["trade"][field] = value
    assert _refusal(act) == "non_finite_amount"


def test_a_nan_quantity_would_otherwise_have_reached_the_table():
    """The guard is not theoretical: without it `nan <= 0` is False,
    so the band check passes it through, while `_fold_trade`'s
    mirror-image `qty > 0` is also False, so the record folds nothing.
    The old code stored a leg with NaN shares and NaN proceeds against
    a yardstick that had folded nothing at all."""
    assert not (float("nan") <= 0)
    assert not math.isfinite(float("nan"))
    act = _trade("d1c", "nyy-bos", qty=10, price=0.5, rp=1.0,
                 nested="ORDER_SIDE_SELL")
    act["trade"]["qty"] = "NaN"
    assert _refusal(act) == "non_finite_amount"
    # the record ignores it too, so refusing keeps the gate green
    assert sl.gate_from_activities([act])["ok"]


@pytest.mark.parametrize("field,value", [("qty", "Infinity"),
                                         ("realizedPnl", "NaN"),
                                         ("realizedPnl", "Infinity")])
def test_a_non_finite_amount_the_record_DOES_fold_makes_the_gate_fail(
        field, value):
    """The other direction, and it must be LOUD. `_fold_trade` has no
    finite guard: `inf > 0` is true, so it folds an infinite qty into
    proceeds, and a NaN realizedPnl straight into realized. This ledger
    cannot store either (NUMERIC(24,6) takes neither, and one such row
    would abort the whole page's insert every time it was retried), so
    it refuses -- and the gate then reports a MISMATCH rather than
    rounding an unstorable number into agreement. A red gate on a
    payload the venue should never send is the right outcome; silence
    is not."""
    act = _trade("d1d", "nyy-bos", qty=10, price=0.5, rp=1.0,
                 nested="ORDER_SIDE_SELL")
    act["trade"][field] = value
    assert _refusal(act) == "non_finite_amount"
    res = sl.gate_from_activities([act])
    assert not res["ok"]
    assert res["refused"]["non_finite_amount"] == 1


def test_a_trade_with_no_market_slug_is_refused():
    act = _trade("d2", "nyy-bos", rp=3.0)
    del act["trade"]["marketSlug"]
    assert _refusal(act) == "no_market_slug"


def test_an_unreadable_payload_is_refused_not_guessed():
    assert _refusal("{not json") == "unreadable_payload"
    assert _refusal(None) == "unreadable_payload"


def test_a_json_text_payload_folds_the_same_as_a_dict():
    """asyncpg hands jsonb back as text or as a dict depending on the
    codec in force; both are the same row."""
    act = _trade("d3", "nyy-bos", qty=10, price=0.5, rp=1.0)
    assert _leg(json.dumps(act)) == _leg(act)


def test_an_undated_sale_is_stored_undated_never_dated_now():
    act = _trade("d4", "nyy-bos", rp=5.0, ts=None)
    assert _leg(act).ts is None


def test_the_ts_is_the_records_own_clock():
    """`_act_ts(act) or _act_ts(trade)` -- the same expression the
    record dates a sale with, so this ledger's ts and sold_markets'
    last_ts are the same clock."""
    act = _trade("d5", "nyy-bos", rp=5.0, ts=None)
    act["trade"]["createTime"] = (TS0 + 60) * 1000
    assert _leg(act).ts == TS0 + 60


def test_the_order_id_is_null_when_the_venue_names_none():
    """Unknown is not a placeholder. The order id is the ONE field that
    can link a sale to the order that made it; a fabricated one would
    link a sale to the wrong row."""
    assert _leg(_trade("d6", "nyy-bos", rp=5.0,
                       nested="ORDER_SIDE_SELL")).order_id is None


# ── THE GATE (pure) ──────────────────────────────────────────────────


def _mixed_tape():
    """One tape carrying every shape the fold can meet."""
    return [
        # two sales on ONE slug -- the case the in-memory fold cannot
        # tell apart at all, and the reason this ledger is per LEG.
        _trade("g1", "nyy-bos", qty=100, price=0.62, rp=12.0,
               nested="ORDER_SIDE_SELL", order_id="o-1", ts=TS0),
        _trade("g2", "nyy-bos", qty=50, price=0.64, rp=7.0,
               nested="ORDER_SIDE_SELL", order_id="o-2", ts=TS0 + 60),
        # a short closing: realized, no proceeds
        _trade("g3", "lal-bos", qty=300, price=0.4, rp=-12.5,
               nested="ORDER_SIDE_BUY", order_id="o-3", ts=TS0 + DAY),
        # a break-even sale with the nested order present
        _trade("g4", "lal-bos", qty=10, price=0.5, rp=0,
               nested="ORDER_SIDE_SELL", ts=TS0 + DAY + 5),
        # THE UNDECIDABLE ONE: break-even, no side anywhere
        _trade("g5", "mia-phi", qty=80, price=0.3, rp=0, ts=TS0 + 2 * DAY),
        # a plain entry
        _trade("g6", "mia-phi", qty=80, price=0.3, rp=0,
               nested="ORDER_SIDE_BUY", ts=TS0 + 2 * DAY),
        # the realized fallback with no side stated
        _trade("g7", "chi-det", qty=25, price=0.8, rp=3.33, ts=TS0 + 3 * DAY),
        # out of band, and slugless: counted by neither side
        _trade("g8", "chi-det", qty=25, price=1.4, rp=3.0,
               nested="ORDER_SIDE_SELL", ts=TS0 + 3 * DAY),
        # shapes that are not sales
        {"id": "g9", "type": _R, "timestamp": TS0 + 4 * DAY,
         "positionResolution": {"marketSlug": "nyy-bos",
                                "afterPosition": {"realized": 42.0}}},
        dict(_trade("g10", "nyy-bos", qty=5, price=0.5, rp=9.0),
             type="ACTIVITY_TYPE_REDEMPTION"),
        dict(_trade("g11", "nyy-bos", qty=5, price=0.5, rp=9.0),
             type="ACTIVITY_TYPE_MERGE"),
    ]


def test_the_gate_holds_on_a_tape_carrying_every_shape():
    """THE GATE, stated as the plan states it: the per-slug sum of the
    ledger reproduces the record's own sold ledger to the cent."""
    res = sl.gate_from_activities(_mixed_tape())
    assert res["ok"], res["mismatches"]
    assert res["mismatch_count"] == 0
    assert res["slugs_checked"] == 4
    # and the shapes were classified the way the rules say
    assert res["fold"] == {"legs": 6, "sell": 4, "buy_close": 1,
                           "unknown": 1, "duplicates": 0}
    assert res["refused"]["not_a_sale:redemption"] == 1
    assert res["refused"]["not_a_sale:merge"] == 1
    assert res["refused"]["not_a_sale:resolution"] == 1
    assert res["refused"]["entry_buy"] == 1
    assert res["refused"]["outside_price_band"] == 1


def test_the_gate_reproduces_the_served_sold_markets_field():
    """Against the SERVED field, not a re-derivation of it: this is the
    number `/api/track-record` publishes."""
    tape = _mixed_tape()
    served = tr.build({}, tape, TS0)["sold_markets"]
    assert served, "the fixture must produce sold markets to compare"
    yard = {m["slug"]: m for m in served}
    res = sl.compare(sl.per_slug(sl.fold_activities(tape).legs), yard,
                     slugs=list(yard))
    assert res["ok"], res["mismatches"]
    # the two sales on one slug are ONE line there and TWO legs here --
    # which is the whole reason the ledger exists.
    assert yard["nyy-bos"]["realized"] == 19.0
    legs = [g for g in sl.fold_activities(tape).legs
            if g.market_slug == "nyy-bos"]
    assert sorted(g.order_id for g in legs) == ["o-1", "o-2"]


def test_the_unknown_population_is_the_ledgers_and_the_records_gap():
    """The record folds the undecidable row as an ENTRY (it appears in
    no sold market at all); the ledger names it. Both agree on the
    money -- which is the point: naming it changes no total, and so no
    comparison against sold_markets can ever SEE rule 2. Its size comes
    from SIDE_CENSUS_SQL, never from the gate."""
    tape = _mixed_tape()
    assert "mia-phi" not in {m["slug"] for m in
                             tr.build({}, tape, TS0)["sold_markets"]}
    fold = sl.fold_activities(tape)
    assert [g.venue_activity_id for g in fold.unknown_legs] == ["g5"]
    assert sl.gate_from_activities(tape)["ok"]
    assert "cannot see rule 2" in sl.gate_from_activities.__doc__


def test_an_id_less_activity_makes_the_gate_FAIL_rather_than_agree():
    """The one shape the ledger refuses that the yardstick counts. It
    must surface as a MISMATCH -- money the ledger is missing has to be
    loud, never rounded into agreement."""
    tape = list(_mixed_tape())
    nameless = _trade("gone", "phx-den", qty=10, price=0.5, rp=4.0,
                      nested="ORDER_SIDE_SELL")
    del nameless["id"]
    tape.append(nameless)
    res = sl.gate_from_activities(tape)
    assert not res["ok"]
    assert res["refused"]["no_activity_id"] == 1
    bad = [m for m in res["mismatches"] if m["slug"] == "phx-den"]
    assert bad and bad[0]["realized"]["sold_markets"] == 4.0
    assert bad[0]["realized"]["ledger"] == 0.0


def test_the_bounded_comparison_says_what_it_did_not_look_at():
    """`compare` bounded to fewer slugs than the ledger holds reports
    the rest as `ledger_only`, never as agreement. (Read the docstring
    with it: when the bound IS the yardstick's own keys -- which is
    what the served-sold_markets path does -- `ledger_only` is empty by
    construction and the coverage is reported by `gate_report`
    instead.)"""
    tape = _mixed_tape()
    ledger = sl.per_slug(sl.fold_activities(tape).legs)
    yard = sl.sold_ledger(tape)
    res = sl.compare(ledger, yard, slugs=["nyy-bos"])
    assert res["ok"] and res["slugs_checked"] == 1
    assert set(res["ledger_only"]) == {"chi-det", "lal-bos", "mia-phi"}
    assert res["ledger_only_count"] == 3
    assert "EMPTY BY CONSTRUCTION" in sl.compare.__doc__


def test_a_ledger_that_disagrees_is_reported_to_the_cent():
    """The gate has to be able to FAIL, and to say by how much."""
    ledger = {"nyy-bos": {"qty": 150.0, "proceeds": 94.0,
                          "realized": 19.01, "last_ts": TS0}}
    yard = {"nyy-bos": {"qty": 150.0, "proceeds": 94.0,
                        "realized": 19.0, "last_ts": TS0}}
    res = sl.compare(ledger, yard)
    assert not res["ok"]
    assert res["mismatches"][0]["realized"]["delta"] == 0.01


# ── the randomized harness ───────────────────────────────────────────

_SIDES = ["BUY", "SELL", "TRADE_SIDE_SELL", "TRADE_SIDE_BUY", None, ""]
_NESTED = ["ORDER_SIDE_SELL", "ORDER_SIDE_BUY", "SELL", "BUY", None]
_QTYS = [0, 1, 2, 3, 5, 10, 25, 100, 240, 587, 0.5]
_PRICES = [0.03, 0.09, 0.225, 0.25, 0.30, 0.44, 0.5, 0.585, 0.85, 0.0,
           1.0, 1.5, -0.1]
_RPS = [0.0, 0.0, 0.0, 0.0, 0.4, -0.6, 0.05, 5.04, -435.07, 1.0, 140.0]
_SLUGS = ["nyy-bos", "lal-bos", "mia-phi", "chi-det", "phx-den"]


def _amount(rng, v):
    return rng.choice([{"value": v}, {"value": str(v)},
                       {"value": v, "currency": "USD"}, v, str(v)])


def _rand_activity(rng, i):
    """A random activity in the venue's shapes -- every side layout,
    every amount encoding, every timestamp placement, plus the
    non-sale shapes. `both` puts an order on BOTH executions with
    independent sides and ids, which is the only shape where WHICH
    execution the fold reads decides the answer."""
    kind = rng.choice([_T] * 8 + [_R, "ACTIVITY_TYPE_REDEMPTION",
                                  "ACTIVITY_TYPE_MERGE",
                                  "ACTIVITY_TYPE_ACCOUNT_DEPOSIT"])
    slug = rng.choice(_SLUGS)
    ts = TS0 + rng.randrange(0, 10) * DAY + rng.randrange(0, 86400)
    if kind == _R:
        return {"id": f"r{i}", "type": kind, "timestamp": ts,
                "positionResolution": {
                    "marketSlug": slug,
                    "afterPosition": {"realized": _amount(rng, 4.0)}}}
    t: dict = {"marketSlug": rng.choice([slug, slug, slug, None]),
               "qty": _amount(rng, rng.choice(_QTYS)),
               "price": _amount(rng, rng.choice(_PRICES))}
    if rng.random() < 0.85:
        t["realizedPnl"] = rng.choice([_amount(rng, rng.choice(_RPS)), None])
    layout = rng.choice(["top", "deep", "deep", "none", "both", "twin"])
    if layout in ("top", "both"):
        t["side"] = rng.choice(_SIDES)
    if layout in ("deep", "both"):
        if layout == "deep":
            t["side"] = None
        order = {"side": rng.choice(_NESTED)}
        if rng.random() < 0.6:
            order["id"] = f"o{i}"
        t[rng.choice(["aggressorExecution", "passiveExecution"])] = {
            "order": order}
    if layout == "twin":
        # BOTH executions, independent sides and ids: the shape that
        # decides which execution the fold trusts.
        t["side"] = None
        for k, tag in (("aggressorExecution", "agg"),
                       ("passiveExecution", "pas")):
            o: dict = {"side": rng.choice(_NESTED)}
            if rng.random() < 0.7:
                o["id"] = f"o{i}-{tag}"
            t[k] = {"order": o}
        t["isAggressor"] = rng.choice([True, False])
    act = {"id": f"t{i}", "type": kind, "trade": t}
    where = rng.choice(["top", "trade", "trade", "both"])
    if where in ("top", "both"):
        act["timestamp"] = rng.choice([ts * 1000, ts])
    if where in ("trade", "both"):
        t["createTime"] = (ts + (999 if where == "both" else 0)) * 1000
    return act


@pytest.mark.parametrize("seed", range(40))
def test_the_gate_holds_on_randomized_tapes(seed):
    """40 tapes x 10 shapes each, every side layout and amount encoding
    the venue has been seen to send. The yardstick is track_record's
    OWN fold, imported rather than copied, so the two cannot drift."""
    rng = random.Random(seed)
    tape = [_rand_activity(rng, i) for i in range(10)]
    res = sl.gate_from_activities(tape)
    assert res["ok"], (seed, res["mismatches"])


@pytest.mark.parametrize("seed", range(6))
def test_the_gate_holds_against_the_served_field_on_randomized_tapes(seed):
    rng = random.Random(1000 + seed)
    tape = [_rand_activity(rng, i) for i in range(60)]
    yard = {m["slug"]: m for m in tr.build({}, tape, TS0)["sold_markets"]}
    res = sl.compare(sl.per_slug(sl.fold_activities(tape).legs), yard,
                     slugs=list(yard))
    assert res["ok"], (seed, res["mismatches"])


def test_the_randomized_harness_is_not_vacuous():
    """A gate that only ever compares zeroes proves nothing. Across the
    seeds the harness must produce all three sides, real proceeds, real
    realized dollars in both directions, and trades carrying BOTH
    executions (the shape that decides which one the fold reads)."""
    sides, proceeds, gains, losses, twins = set(), 0.0, 0, 0, 0
    for seed in range(40):
        rng = random.Random(seed)
        tape = [_rand_activity(rng, i) for i in range(10)]
        twins += sum(1 for a in tape
                     if isinstance(a.get("trade"), dict)
                     and a["trade"].get("aggressorExecution")
                     and a["trade"].get("passiveExecution"))
        for g in sl.fold_activities(tape).legs:
            sides.add(g.side)
            proceeds += g.proceeds_usd
            gains += g.realized_usd > 0
            losses += g.realized_usd < 0
    assert sides == {sl.SIDE_SELL, sl.SIDE_BUY_CLOSE, sl.SIDE_UNKNOWN}
    assert proceeds > 100.0 and gains > 5 and losses > 5
    assert twins > 20, twins


# ── THE SQL, asserted clause by clause ───────────────────────────────
#
# A fake pool re-implements each query in Python, so nothing it answers
# can prove the SQL right. These tests read the statements themselves.


def _insert_columns() -> list[str]:
    m = re.search(r"INSERT INTO copy_exit_legs\s*\((.*?)\)", sl._INSERT_SQL,
                  re.S)
    assert m, sl._INSERT_SQL
    return [c.strip() for c in m.group(1).split(",")]


def _sentinel_leg() -> sl.SaleLeg:
    """A leg whose every field holds a value nothing else holds, so a
    transposition of two columns cannot survive the comparison."""
    return sl.SaleLeg(venue_activity_id="ID", ts=11.0, market_slug="SLUG",
                      shares=22.0, trade_qty=33.0, price=0.44,
                      proceeds_usd=55.0, realized_usd=66.0, order_id="ORD",
                      side=sl.SIDE_SELL, side_src=sl.SRC_AGGRESSOR,
                      source="SRC")


def test_the_insert_writes_each_field_into_its_own_column():
    """THE MUTATION THIS PINS: transpose $6 and $7 and every leg stores
    the venue's realized figure as proceeds and qty*price as realized.
    Only a buy_close leg would trip a CHECK; a sell-only slug stores it
    silently. Asserted field by field, by name."""
    cols = _insert_columns()
    assert cols == list(sl.INSERT_COLUMNS)
    leg = _sentinel_leg()
    got = dict(zip(cols, leg.as_params()))
    assert got == {c: getattr(leg, c) for c in cols}
    assert got["proceeds_usd"] == 55.0 and got["realized_usd"] == 66.0
    assert got["shares"] == 22.0 and got["trade_qty"] == 33.0
    # every column has exactly one placeholder, in order
    values = re.search(r"VALUES\s*\((.*?)\)\s*ON CONFLICT", sl._INSERT_SQL,
                       re.S).group(1)
    assert [int(n) for n in re.findall(r"\$(\d+)", values)] == list(
        range(1, len(cols) + 1))
    # the five money/quantity columns are cast to NUMERIC, nothing else
    numeric = {cols[int(n) - 1] for n in
               re.findall(r"\$(\d+)::float8::numeric", values)}
    assert numeric == {"shares", "trade_qty", "price", "proceeds_usd",
                       "realized_usd"}


def test_the_insert_is_append_only():
    stmt = _squash(sl._INSERT_SQL).upper()
    assert "ON CONFLICT (VENUE_ACTIVITY_ID) DO NOTHING" in stmt
    assert "UPDATE" not in stmt.replace("DO NOTHING", "")
    assert "row_id" not in sl._INSERT_SQL


def test_the_ledger_totals_sql_is_the_folds_own_side_rules():
    """The FILTERs ARE the contract -- they are what makes the gate's
    arithmetic the yardstick's arithmetic, and migration 052's CHECKs
    make several of them redundant, which is exactly why no fake pool
    can tell a correct one from a broken one."""
    sql = _squash(sl.LEDGER_TOTALS_SQL)
    assert "sum(shares) FILTER (WHERE side IN ('sell'))" in sql
    assert "sum(proceeds_usd) FILTER (WHERE side IN ('sell'))" in sql
    assert ("sum(realized_usd) FILTER (WHERE side IN ('sell', 'buy_close'))"
            in sql)
    # the ts of an undecidable leg is not a sale time
    assert "max(ts) FILTER (WHERE side <> 'unknown')" in sql
    assert "count(*) FILTER (WHERE side = 'unknown') AS unknown_legs" in sql
    # cash-in never counts a short's closing buy; realized always does
    assert "sum(shares) FILTER (WHERE side IN ('sell', 'buy_close'))" not in sql
    # and the literals come from the module's own constants
    assert sl.SIDES_CASH_IN == ("sell",)
    assert sl.SIDES_COUNTED == ("sell", "buy_close")


def test_the_ledger_read_is_bounded_and_pages_by_slug():
    sql = _squash(sl.LEDGER_TOTALS_SQL)
    assert "($1::text[] IS NULL OR market_slug = ANY($1::text[]))" in sql
    assert "market_slug > $2" in sql          # the page cursor
    assert "ORDER BY 1 LIMIT $3" in sql       # ordered, so paging is total
    assert "LIMIT" in _squash(sl.OUTSIDE_SCOPE_SQL)


def test_the_outside_scope_read_is_the_ledger_the_gate_did_not_compare():
    sql = _squash(sl.OUTSIDE_SCOPE_SQL)
    assert "NOT (market_slug = ANY($1::text[]))" in sql
    assert "count(DISTINCT market_slug) AS slugs" in sql
    assert ("sum(realized_usd) FILTER (WHERE side IN ('sell', 'buy_close'))"
            in sql)


@pytest.mark.parametrize("stmt", ["_ARCHIVE_STREAM_SQL", "_ARCHIVE_PAGE_SQL"])
def test_the_archive_read_is_strictly_after_the_cursor_and_trades_only(stmt):
    """`id >= $1` would re-fold the cursor row every pass; dropping the
    type predicate would parse every resolution's payload for nothing.
    Both mutations pass a fake pool that filters in Python."""
    sql = _squash(getattr(sl, stmt))
    assert "WHERE id > $1" in sql and "id >= $1" not in sql
    assert "AND payload->>'type' = $2" in sql
    assert "ORDER BY id" in sql
    assert "FROM pmus_activity_archive" in sql
    if stmt == "_ARCHIVE_PAGE_SQL":
        assert "LIMIT $3" in sql
    else:
        # the streamed read is bounded by the cursor's fetch size, and
        # must NOT carry a LIMIT (it is one scan, not repeated pages --
        # track_record.py:1702-1706)
        assert "LIMIT" not in sql


def test_the_side_census_reads_the_three_side_paths_and_survives_junk():
    """This query IS the plan's section 2.5 measurement -- the only
    measurement of the population rule 2 exists for. A typo in one of
    its three JSON paths silently moves rows into the NULL bucket and
    inflates the very number it is run to find."""
    sql = _squash(sl.SIDE_CENSUS_SQL)
    for path in ("payload->'trade'->'aggressorExecution'->'order'->>'side'",
                 "payload->'trade'->'passiveExecution'->'order'->>'side'",
                 "payload->'trade'->>'side'"):
        assert path in sql, path
    assert "payload->>'type' = 'ACTIVITY_TYPE_TRADE'" in sql
    assert "GROUP BY 1" in sql and "LIMIT 50" in sql
    # the realized read is guarded: a non-numeric string must not abort
    # the whole grouped scan, it must land in break_even (over-stating
    # the ambiguous population, which is the safe direction)
    assert "CASE WHEN payload->'trade'->'realizedPnl'->>'value' ~" in sql
    assert "^-?[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$" in sql


# ── the database path (no database: a recording fake) ────────────────
#
# THE POOL ANSWERS THE LEDGER READ BY RUNNING THE STATEMENT IT IS
# HANDED. It parses the select list out of the SQL -- each aggregate,
# its FILTER and its ALIAS -- and applies that to the rows the INSERT
# wrote. It does not re-implement the query in Python and it never
# echoes back a total the test computed with `per_slug`: a pool that
# did could only ever prove that `per_slug` agrees with itself, and two
# mutations that leave every substring the SQL text tests assert
# exactly where it is (transposing `AS qty` with `AS proceeds`, and
# deleting the `last_ts` FILTER) passed the whole suite against one.
#
# What stays Python here, because it is pinned textually instead (see
# test_the_ledger_read_is_bounded_and_pages_by_slug): the WHERE clause,
# the GROUP BY, the ORDER BY and the LIMIT.


def _ptype(payload):
    doc = payload if isinstance(payload, dict) else json.loads(payload)
    return doc.get("type")


def _balanced(text: str, start: int) -> tuple[str, int]:
    """The contents of the parenthesised group opening at `start`, and
    the index just past its closing paren."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
    raise AssertionError(f"unbalanced parentheses: {text[start:start + 60]!r}")


def _split_top_level(text: str) -> list[str]:
    """Split on commas OUTSIDE parentheses, so `COALESCE(x, 0)` is one
    select item and not two."""
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def _predicate(expr: str):
    """`side IN ('a', 'b')`, `side = 'a'` and `side <> 'a'` -- the three
    shapes this module's FILTERs use.

    Anything else RAISES. A FILTER the fake cannot read must never be
    treated as no filter at all: that is precisely the failure this
    machinery exists to make impossible.
    """
    m = re.fullmatch(r"(\w+)\s+IN\s*\((.*)\)", expr, re.I | re.S)
    if m:
        col = m.group(1)
        vals = {v.strip().strip("'") for v in m.group(2).split(",")}
        return lambda r: r[col] in vals
    m = re.fullmatch(r"(\w+)\s*(=|<>)\s*'([^']*)'", expr, re.S)
    if m:
        col, op, val = m.groups()
        if op == "=":
            return lambda r: r[col] == val
        return lambda r: r[col] != val
    raise AssertionError(
        f"the fake pool cannot evaluate the FILTER {expr!r}: teach it, "
        f"never let an unread clause pass as no clause")


_AGG = re.compile(r"\b(count|sum|max)\s*\(", re.I)


def _select_list(sql: str) -> list[tuple[str, tuple]]:
    """(alias, spec) per select item of the statement."""
    body = re.search(r"\bSELECT\b(.*?)\bFROM\b", _squash(sql), re.I | re.S)
    assert body, sql
    out: list[tuple[str, tuple]] = []
    for item in _split_top_level(body.group(1)):
        m = re.search(r"\bAS\s+(\w+)$", item, re.I)
        alias = m.group(1) if m else item
        expr = item[:m.start()] if m else item
        agg = _AGG.search(expr)
        if not agg:
            out.append((alias, ("column", expr.strip())))
            continue
        inner, end = _balanced(expr, agg.end() - 1)
        inner = inner.strip()
        distinct = bool(re.match(r"DISTINCT\s", inner, re.I))
        col = re.sub(r"^DISTINCT\s+", "", inner, flags=re.I).strip()
        pred = None
        f = re.search(r"\bFILTER\s*\(", expr[end:], re.I)
        if f:
            ftxt, _ = _balanced(expr[end:], f.end() - 1)
            w = re.match(r"\s*WHERE\s+(.*)$", ftxt, re.I | re.S)
            assert w, ftxt
            pred = _predicate(w.group(1).strip())
        out.append((alias, (agg.group(1).lower(), col, pred, distinct,
                            "COALESCE(" in expr.upper())))
    return out


def _eval_select(items, rows: list[dict]) -> dict:
    got: dict = {}
    for alias, spec in items:
        if spec[0] == "column":
            got[alias] = rows[0][spec[1]] if rows else None
            continue
        fn, col, pred, distinct, coalesce = spec
        sel = [r for r in rows if pred is None or pred(r)]
        if fn == "count":
            if col == "*":
                got[alias] = len(sel)
            else:
                vals = [r[col] for r in sel if r[col] is not None]
                got[alias] = len(set(vals)) if distinct else len(vals)
            continue
        vals = [r[col] for r in sel if r[col] is not None]
        v = (sum(vals) if fn == "sum" else max(vals)) if vals else None
        got[alias] = 0.0 if v is None and coalesce else v
    return got


class _FakeCursor:
    def __init__(self, pool, rows, fail_at=None):
        self.pool = pool
        self.rows = list(rows)
        self.i = 0
        self.fail_at = fail_at
        self.fetches = 0

    async def fetch(self, n):
        self.pool.statements.append(("cursor.fetch", n))
        self.fetches += 1
        if self.fail_at is not None and self.fetches >= self.fail_at:
            raise asyncio.TimeoutError()
        out = self.rows[self.i:self.i + n]
        self.i += len(out)
        return out


class _FakeCon:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        pool = self.pool

        class _Tx:
            async def __aenter__(self):
                pool.calls.append(("transaction", "", ()))
                return None

            async def __aexit__(self, *a):
                return False

        return _Tx()

    async def cursor(self, sql, *args):
        self.pool.calls.append(("cursor", sql, args))
        after, kind = args
        rows = [r for r in self.pool.archive
                if str(r["id"]) > after and _ptype(r["payload"]) == kind]
        return _FakeCursor(self.pool, sorted(rows, key=lambda r: str(r["id"])),
                           fail_at=self.pool.fail_fetch_at)


class _FakePool:
    """Records every statement and answers the ones the module runs.

    It does not simulate Postgres. Two different things are pinned two
    different ways: the statements' TEXT is asserted directly above,
    and the ledger read's ARITHMETIC is run out of the statement itself
    (`_select_list`) against the rows the INSERT wrote -- so a FILTER
    that stopped filtering, or two aliases transposed, moves the number
    the gate compares. The rest is control flow: that a pass is a pass,
    that an interrupted read keeps its legs, that the write is
    append-only, and that every statement goes out under a timeout.

    `legs` seeds the `copy_exit_legs` table with rows written exactly as
    `_INSERT_SQL` writes them (`INSERT_COLUMNS` zipped onto
    `as_params()`), so the read path and the write path meet on the
    same row shape.
    """

    def __init__(self, archive=None, legs=None, fail_fetch_at=None):
        self.archive = list(archive or [])
        self.rows: list[dict] = []
        self.calls: list[tuple] = []
        self.statements: list[tuple] = []
        self.written: list[tuple] = []
        self.cursor = None
        self.fail_fetch_at = fail_fetch_at
        for leg in (legs or []):
            self._store(leg.as_params())

    def _store(self, params) -> None:
        """One INSERT, as migration 052 defines it: keyed by
        venue_activity_id, ON CONFLICT DO NOTHING."""
        row = dict(zip(sl.INSERT_COLUMNS, params))
        if any(r["venue_activity_id"] == row["venue_activity_id"]
               for r in self.rows):
            return
        self.rows.append(row)

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                pool.calls.append(("acquire", "", ()))
                return _FakeCon(pool)

            async def __aexit__(self, *a):
                return False

        return _Acq()

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        self.statements.append(("fetch", sql))
        if "pmus_activity_archive" in sql:
            after, kind, limit = args
            rows = [r for r in self.archive
                    if str(r["id"]) > after and _ptype(r["payload"]) == kind]
            return sorted(rows, key=lambda r: str(r["id"]))[:limit]
        if "count(DISTINCT market_slug)" in sql:
            # OUTSIDE_SCOPE_SQL: one ungrouped row over the ledger the
            # comparison did NOT name.
            want = set(args[0])
            keep = [r for r in self.rows if r["market_slug"] not in want]
            return [_eval_select(_select_list(sql), keep)]
        if "copy_exit_legs" in sql:
            # LEDGER_TOTALS_SQL: WHERE / GROUP BY / ORDER BY / LIMIT in
            # Python, the AGGREGATES out of the statement itself.
            want, after, limit = args
            keep = [r for r in self.rows
                    if (want is None or r["market_slug"] in set(want))
                    and r["market_slug"] > (after or "")]
            groups: dict[str, list[dict]] = {}
            for r in keep:
                groups.setdefault(r["market_slug"], []).append(r)
            items = _select_list(sql)
            return [_eval_select(items, groups[s])
                    for s in sorted(groups)[:limit]]
        return []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        self.statements.append(("fetchval", sql))
        return self.cursor

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        self.statements.append(("execute", sql))
        if "ingestion_state" in sql:
            self.cursor = args[1]

    async def executemany(self, sql, args):
        self.calls.append(("executemany", sql, tuple(args)))
        self.statements.append(("executemany", sql))
        self.written.extend(args)
        if "copy_exit_legs" in sql:
            for params in args:
                self._store(params)


def _archive_rows(acts):
    return [{"id": a["id"], "payload": a} for a in acts]


async def test_ingest_writes_one_row_per_leg_and_never_updates():
    pool = _FakePool(archive=_archive_rows(_mixed_tape()))
    res = await sl.ingest_page(pool, limit=50)
    assert res["legs_written"] == 6
    assert res["sides"] == {"sell": 4, "buy_close": 1, "unknown": 1}
    assert res["done"] is True
    insert = [c for c in pool.calls if c[0] == "executemany"][0][1]
    assert "INSERT INTO copy_exit_legs" in insert
    # the written tuples are the legs, in the migration's column order
    assert [w[0] for w in pool.written] == ["g1", "g2", "g3", "g4",
                                            "g5", "g7"]
    assert all(len(w) == len(sl.INSERT_COLUMNS) for w in pool.written)


async def test_the_database_path_stores_the_archives_own_key_hash_and_all():
    """The archive keys an activity the venue did not name by
    sha256(payload) (track_record.py:1864-1867) and hands the fold that
    key. Such a row IS folded and IS stored, under the archive's key --
    the two tables have to join. What is refused is inventing a key
    here: the same payload down the PURE path, with no archive key, is
    refused `no_activity_id`. Both paths pinned, because the sweep only
    ever takes the first one."""
    act = _trade("unused", "nyy-bos", qty=10, price=0.5, rp=4.0,
                 nested="ORDER_SIDE_SELL")
    del act["id"]
    digest = "d" * 64
    pool = _FakePool(archive=[{"id": digest, "payload": act}])
    res = await sl.ingest_page(pool, limit=10)
    assert res["legs_written"] == 1
    assert pool.written[0][0] == digest
    assert _refusal(act) == "no_activity_id"
    assert "content hash" in MIGRATION.read_text()


async def test_a_page_that_fills_its_limit_is_not_done_and_resumes():
    pool = _FakePool(archive=_archive_rows(_mixed_tape()))
    first = await sl.ingest_page(pool, limit=3)
    assert first["done"] is False and first["scanned"] == 3
    second = await sl.ingest_page(pool, after_id=first["last_id"], limit=3)
    assert second["last_id"] > first["last_id"]


async def test_every_statement_the_module_runs_is_wrapped_in_a_timeout(
        monkeypatch):
    """Bounded in time as well as in rows. Counted, not sampled: the
    old form asserted that every RECORDED timeout equalled the one
    passed in, which stays true when a statement is not wrapped at
    all -- deleting the write's wait_for left the suite green."""
    seen: list[float] = []
    real = asyncio.wait_for

    async def _spy(coro, timeout, *a, **k):
        seen.append(timeout)
        return await real(coro, timeout, *a, **k)

    monkeypatch.setattr(asyncio, "wait_for", _spy)
    pool = _FakePool(archive=_archive_rows(_mixed_tape()))
    await sl.ingest_page(pool, limit=50, timeout=7.0)
    assert pool.statements, "the page must have run statements"
    assert len(seen) == len(pool.statements), (seen, pool.statements)
    assert all(t == 7.0 for t in seen)

    seen.clear()
    pool2 = _FakePool(archive=_archive_rows(_mixed_tape()))
    await sl.sweep(pool2, chunk=2, max_chunks=10, timeout=7.0)
    assert len(seen) == len(pool2.statements), (seen, pool2.statements)
    assert all(t == 7.0 for t in seen)


# ── the sweep: a PASS, not a high-water mark ─────────────────────────


async def test_the_sweep_reads_one_streaming_cursor_not_repeated_pages():
    """track_record.py:1702-1706 measured what repeated `id > $1 ORDER
    BY id LIMIT n` pages do to this table: a fresh scan per chunk,
    TimeoutError at chunk 6. The backfill uses the shape that
    measurement forced on the hydrate."""
    pool = _FakePool(archive=_archive_rows(_mixed_tape()))
    res = await sl.sweep(pool, chunk=3, max_chunks=10)
    assert res["caught_up"] is True and res["complete_pass"] is True
    assert res["legs_written"] == 6
    assert [c[0] for c in pool.calls].count("cursor") == 1
    assert ("fetch", sl._ARCHIVE_PAGE_SQL) not in pool.statements
    assert sl.CURSOR_KEY in str(pool.calls)
    assert pool.cursor and json.loads(pool.cursor)["pass_complete"] is True


async def test_a_leg_whose_archive_id_sorts_BELOW_the_cursor_is_still_folded():
    """THE LOSS THIS DESIGN EXISTS TO PREVENT. Archive ids are not
    monotonic in arrival: `pmus_activity_archive.id` is the venue's own
    opaque id, and an activity the venue did not name is keyed by
    sha256(payload) -- uniformly random in text order. A high-water
    mark would skip this sale for ever and still report `caught_up`.
    A PASS finds it on the next run."""
    first = _trade("zz-first-sweep", "nyy-bos", qty=10, price=0.5, rp=1.0,
                   nested="ORDER_SIDE_SELL")
    pool = _FakePool(archive=_archive_rows([first]))
    one = await sl.sweep(pool, chunk=5, max_chunks=5)
    assert one["legs_written"] == 1 and one["caught_up"] is True

    late = _trade("aa-arrived-later", "lal-bos", qty=100, price=0.4, rp=40.0,
                  nested="ORDER_SIDE_SELL")
    pool.archive.extend(_archive_rows([late]))
    two = await sl.sweep(pool, chunk=5, max_chunks=5)
    assert two["scanned"] == 2, "a completed pass starts again from the top"
    assert two["caught_up"] is True
    assert "aa-arrived-later" in [w[0] for w in pool.written]
    # and the money is in the ledger exactly once (ON CONFLICT DO NOTHING)
    assert [w[0] for w in pool.written].count("zz-first-sweep") == 2
    assert "HIGH-WATER MARK" in sl.sweep.__doc__


async def test_an_interrupted_pass_resumes_and_says_it_is_not_caught_up():
    pool = _FakePool(archive=_archive_rows(_mixed_tape()))
    part = await sl.sweep(pool, chunk=2, max_chunks=1)
    assert part["caught_up"] is False
    assert part["coverage"] == "incomplete:max_chunks"
    assert part["stopped"] == "max_chunks"
    assert json.loads(pool.cursor)["pass_complete"] is False
    scanned_first = part["scanned"]

    rest = await sl.sweep(pool, chunk=50, max_chunks=5)
    assert rest["coverage"] == "resumed_pass" and rest["caught_up"] is True
    # it continued where it stopped rather than starting over
    assert rest["scanned"] < len(_mixed_tape())
    assert rest["scanned"] + scanned_first == len(
        [a for a in _mixed_tape() if a["type"] == _T])


async def test_a_read_timeout_stops_the_pass_and_keeps_what_it_folded():
    """The recorded failure mode on this table. It must not raise: the
    legs already folded are written (the write is on its own
    connection, so the reader's transaction cannot roll them back), the
    resume point is stored, and the caller is told to re-run."""
    pool = _FakePool(archive=_archive_rows(_mixed_tape()), fail_fetch_at=2)
    res = await sl.sweep(pool, chunk=3, max_chunks=10)
    assert res["stopped"] == "read_timeout"
    assert res["caught_up"] is False
    assert res["coverage"] == "incomplete:read_timeout"
    assert res["legs_written"] > 0 and pool.written
    assert json.loads(pool.cursor)["pass_complete"] is False


async def test_the_sweep_folds_the_records_own_yardstick_as_it_streams():
    """A completed pass hands back the archive-wide `sold` ledger --
    every slug, no [:30] -- produced by track_record's OWN fold. That
    is what lets the gate check the WHOLE ledger."""
    tape = _mixed_tape()
    pool = _FakePool(archive=_archive_rows(tape))
    res = await sl.sweep(pool, chunk=4, max_chunks=10)
    assert res["yardstick"] == sl.sold_ledger(tape)
    assert res["yardstick_slugs"] == 3
    assert res["yardstick_errors"] == 0


async def test_an_incomplete_pass_hands_back_no_yardstick():
    pool = _FakePool(archive=_archive_rows(_mixed_tape()))
    res = await sl.sweep(pool, chunk=2, max_chunks=1)
    assert res["yardstick"] is None


async def test_a_payload_the_records_fold_chokes_on_is_counted_not_fatal():
    """`_fold_trade` does not guard `isinstance(order, dict)`; this
    fold does. A payload shape exists that the ledger folds and the
    record raises on -- it cannot be in the archive today (the record
    would be down), and if one arrives the pass counts it instead of
    dying, because a yardstick error invalidates the comparison and
    has to be reported rather than crash the sweep."""
    bad = _trade("y1", "nyy-bos", qty=10, price=0.5, rp=1.0)
    bad["trade"]["aggressorExecution"] = {"order": "x"}
    pool = _FakePool(archive=_archive_rows([bad]))
    res = await sl.sweep(pool, chunk=5, max_chunks=5)
    assert res["yardstick_errors"] == 1
    assert res["caught_up"] is True


# ── the gate against the database ────────────────────────────────────


def _short_by(legs, slug, **delta):
    """The same legs with ONE leg on `slug` moved by `delta` -- a wrong
    row in the table, not a doctored total: the totals the gate reads
    are produced from these rows by LEDGER_TOTALS_SQL's own clauses."""
    out = list(legs)
    for i, g in enumerate(out):
        if g.market_slug == slug:
            out[i] = dataclasses.replace(
                g, **{k: getattr(g, k) + v for k, v in delta.items()})
            return out
    raise AssertionError(f"no leg on {slug!r}")


async def test_the_ledger_read_is_the_statements_own_arithmetic():
    """The totals the gate compares are LEDGER_TOTALS_SQL's select list
    applied to the rows the INSERT wrote -- and they reproduce the
    in-memory fold. Everything below rests on this, so it is stated
    once, on its own."""
    tape = _mixed_tape()
    legs = sl.fold_activities(tape).legs
    got = await sl.db_per_slug(_FakePool(legs=legs), None)
    mine = sl.per_slug(legs)
    assert set(got) == set(mine)
    for slug, v in mine.items():
        assert got[slug]["qty"] == pytest.approx(v["qty"])
        assert got[slug]["proceeds"] == pytest.approx(v["proceeds"])
        assert got[slug]["realized"] == pytest.approx(v["realized"])
        assert got[slug]["last_ts"] == v["last_ts"]
    assert got["mia-phi"]["unknown"] == 1


async def test_gate_report_compares_the_ledger_to_the_served_field():
    tape = _mixed_tape()
    pool = _FakePool(legs=sl.fold_activities(tape).legs)
    served = tr.build({}, tape, TS0)["sold_markets"]
    res = await sl.gate_report(pool, served)
    assert res["ok"], res["mismatches"]
    assert res["slugs_checked"] == len(served)
    assert res["unknown_legs"] == 0   # mia-phi is not a sold market


async def test_gate_report_fails_loudly_when_a_cent_is_missing():
    tape = _mixed_tape()
    legs = _short_by(sl.fold_activities(tape).legs, "nyy-bos",
                     realized_usd=-0.01)
    pool = _FakePool(legs=legs)
    served = tr.build({}, tape, TS0)["sold_markets"]
    res = await sl.gate_report(pool, served)
    assert not res["ok"]
    assert res["mismatches"][0]["realized"]["delta"] == -0.01
    # and it names the three non-ledger causes before anyone acts on it
    assert res["before_blaming_the_ledger"] == list(sl.NOT_THE_LEDGERS_FAULT)


@pytest.mark.parametrize("served", [[], None, [{}], [{"slug": None}]])
async def test_the_gate_REFUSES_an_empty_yardstick_instead_of_reporting_ok(
        served):
    """FAIL CLOSED, on the acceptance criterion itself.

    With no slugs the comparison loop never runs, so the old form
    returned ok=True having compared nothing -- and `[]` is exactly
    what /api/track-record serves with no venue credentials
    ({'configured': False}, track_record.py:2115) or during a venue
    outage ({'configured': True, 'error': ...}, :2178). A gate that
    goes green on a box that cannot see the venue is worse than no
    gate."""
    tape = _mixed_tape()
    pool = _FakePool(legs=sl.fold_activities(tape).legs)
    res = await sl.gate_report(pool, served)
    assert res["ok"] is False
    assert res["reason"] == "empty_yardstick"
    assert res["slugs_checked"] == 0
    # it did not even read the ledger: nothing was compared
    assert not pool.calls


async def test_the_gate_REFUSES_to_run_on_a_sweep_that_did_not_finish():
    """An incomplete sweep produces a red gate whose cause is the
    SWEEP, and the operator is then told the ledger is wrong. Refuse
    instead, and say which it is."""
    tape = _mixed_tape()
    pool = _FakePool(archive=_archive_rows(tape))
    part = await sl.sweep(pool, chunk=2, max_chunks=1)
    served = tr.build({}, tape, TS0)["sold_markets"]
    res = await sl.gate_report(pool, served, sweep_result=part)
    assert res["ok"] is False and res["reason"] == "sweep_incomplete"
    assert "incomplete:max_chunks" in res["detail"]


async def test_the_gate_reports_the_ledger_the_served_window_never_named():
    """On the production path `sold_markets` is the newest 30 markets,
    so at most 30 slugs are compared and `ledger_only` is empty BY
    CONSTRUCTION -- the read asks for those slugs and gets them. The
    rows the next unit exists to retire are stranded copies weeks old:
    precisely the markets outside that window. So the gate states its
    coverage, with the money on it."""
    tape = _mixed_tape()
    legs = sl.fold_activities(tape).legs
    totals = sl.per_slug(legs)
    pool = _FakePool(legs=legs)
    served = [m for m in tr.build({}, tape, TS0)["sold_markets"]
              if m["slug"] == "nyy-bos"]
    res = await sl.gate_report(pool, served)
    assert res["ok"] and res["slugs_checked"] == 1
    assert res["ledger_only"] == []          # structurally, not a claim
    out = res["scope"]["outside"]
    assert out["slugs"] == 3
    assert out["realized"] == pytest.approx(
        round(sum(v["realized"] for s, v in totals.items()
                  if s != "nyy-bos"), 2))
    assert out["proceeds"] > 0
    assert "newest 30" in res["scope"]["note"]


async def test_the_gate_can_check_the_WHOLE_ledger_and_then_ledger_only_bites():
    """Handed the archive-wide yardstick the sweep folds as it streams,
    the gate compares every slug -- no [:30] -- and a ledger slug the
    yardstick does not carry is finally reportable."""
    tape = _mixed_tape()
    pool = _FakePool(legs=sl.fold_activities(tape).legs)
    served = [m for m in tr.build({}, tape, TS0)["sold_markets"]
              if m["slug"] == "nyy-bos"]
    res = await sl.gate_report(pool, served, archive_yardstick=sl.sold_ledger(tape))
    assert res["ok"], res.get("full_ledger")
    # every slug in either side is looked at -- including mia-phi,
    # which the ledger holds (one `unknown` leg) and the yardstick
    # does not, and which carries no money either way
    assert res["full_ledger"]["slugs_checked"] == 4
    assert res["full_ledger"]["ledger_slugs"] == 4
    assert res["full_ledger"]["yardstick_slugs"] == 3


async def test_the_whole_ledger_gate_catches_a_slug_outside_the_newest_30():
    """The failure the 30-market gate cannot see: money wrong on an old
    market. Green on the served slug, RED overall."""
    tape = _mixed_tape()
    # a stranded old market whose ONE leg carries $5 the record never saw
    legs = _short_by(sl.fold_activities(tape).legs, "chi-det",
                     realized_usd=5.0)
    pool = _FakePool(legs=legs)
    served = [m for m in tr.build({}, tape, TS0)["sold_markets"]
              if m["slug"] == "nyy-bos"]
    plain = await sl.gate_report(pool, served)
    assert plain["ok"], "the bounded gate cannot see it -- that is the point"
    full = await sl.gate_report(pool, served,
                                archive_yardstick=sl.sold_ledger(tape))
    assert full["ok"] is False
    bad = [m for m in full["full_ledger"]["mismatches"]
           if m["slug"] == "chi-det"]
    assert bad and bad[0]["realized"]["delta"] == 5.0


def _mutate(sql: str, *pairs: tuple[str, str]) -> str:
    out = sql
    for old, new in pairs:
        assert old in out, old
        out = out.replace(old, new, 1)
    return out


@pytest.mark.parametrize("pairs,what", [
    ((("0)::float8 AS qty,", "0)::float8 AS @,"),
      ("0)::float8 AS proceeds,", "0)::float8 AS qty,"),
      ("0)::float8 AS @,", "0)::float8 AS proceeds,")),
     "the qty and proceeds ALIASES transposed"),
    ((("max(ts) FILTER (WHERE side <> 'unknown')", "max(ts)"),),
     "the last_ts FILTER deleted, so an undecidable leg dates a sale"),
])
async def test_a_gutted_ledger_read_turns_the_gate_RED(monkeypatch, pairs,
                                                       what):
    """THE MUTATIONS THIS PINS, and why the SQL text assertions above
    are not enough by themselves.

    Both of these leave every substring those assertions name exactly
    where it is -- the first moves only two ALIASES, which no text
    assertion mentions at all -- so both are invisible to them. And
    against a fake pool that answered a `copy_exit_legs` read by
    echoing back totals the test had itself computed with `per_slug`,
    both passed the entire suite: the gate's arithmetic could be
    transposed in production and nothing here said a word.

    The pool now runs the select list OUT OF THE STATEMENT it is
    handed, against the rows the INSERT wrote, so a moved alias and a
    FILTER that stopped filtering both move the number the gate
    compares -- and the gate goes red, which is the only outcome a
    yardstick is worth anything for.
    """
    monkeypatch.setattr(sl, "LEDGER_TOTALS_SQL",
                        _mutate(sl.LEDGER_TOTALS_SQL, *pairs))
    tape = _mixed_tape()
    pool = _FakePool(legs=sl.fold_activities(tape).legs)
    served = tr.build({}, tape, TS0)["sold_markets"]
    res = await sl.gate_report(pool, served,
                               archive_yardstick=sl.sold_ledger(tape))
    assert res["ok"] is False, what


async def test_the_write_path_feeds_the_read_path():
    """The sweep's INSERT and the gate's SELECT meet on one row shape:
    sweep the archive, then gate what the sweep actually wrote. Nothing
    in between re-states the totals in Python."""
    tape = _mixed_tape()
    pool = _FakePool(archive=_archive_rows(tape))
    swept = await sl.sweep(pool, chunk=3, max_chunks=10)
    assert swept["complete_pass"] is True
    served = tr.build({}, tape, TS0)["sold_markets"]
    res = await sl.gate_report(pool, served, sweep_result=swept,
                               archive_yardstick=swept["yardstick"])
    assert res["ok"], (res.get("mismatches"), res.get("full_ledger"))
    assert res["full_ledger"]["ledger_slugs"] == 4


async def test_the_gate_refuses_more_slugs_than_it_can_read_in_one_page():
    """Truncating the comparison and reporting `ok` would be the exact
    failure this ledger exists to prevent."""
    pool = _FakePool()
    with pytest.raises(ValueError):
        await sl.db_per_slug(pool, [f"s{i}" for i in range(201)])


async def test_the_whole_ledger_read_pages_and_refuses_to_truncate():
    """The unbounded read has the same hazard from the other end: a
    LIMIT with no cursor would stop silently. It pages, and running out
    of pages RAISES rather than reporting on part of the ledger."""
    legs = [sl.SaleLeg(venue_activity_id=f"k{i:03d}", ts=TS0,
                       market_slug=f"slug-{i:03d}", shares=1.0,
                       trade_qty=1.0, price=0.5, proceeds_usd=1.0,
                       realized_usd=1.0, order_id=None, side=sl.SIDE_SELL,
                       side_src=sl.SRC_AGGRESSOR)
            for i in range(25)]
    pool = _FakePool(legs=legs)
    got = await sl.db_all_slugs(pool, page=4, max_pages=20)
    assert len(got) == 25
    with pytest.raises(ValueError):
        await sl.db_all_slugs(pool, page=4, max_pages=2)


# ── the migration ────────────────────────────────────────────────────


def _reserved_numbers() -> set[int]:
    """The checked-in migration reservations, read from the register
    itself (docs/mirror-to-a-tee-program.md:180), not remembered."""
    txt = PROGRAM_MD.read_text()
    line = [ln for ln in txt.splitlines() if "Migration numbers reserved"
            in ln]
    assert line, "the reservation register moved; re-read it before renaming"
    # the register runs over the following lines too
    i = txt.index(line[0])
    block = txt[i:i + 600]
    return {int(n) for n in re.findall(r"\b0(\d\d)\s*=\s*Phase", block)}


def test_the_migration_number_is_unique_and_not_one_the_register_reserves():
    """052, not 050. docs/mirror-to-a-tee-program.md:180 reserves 048,
    050 and 051 for the in-progress to-a-tee phases and names
    `migrations/050_mirror_shorts.sql` at :481 -- the same register
    tests/test_mirror_live_migration.py already enforces for 048/049.
    Phase 5's own house test counts files by prefix, so a second 050
    would fail it as well as this unit.

    Asserted as UNIQUENESS, never as "the highest": maximality would
    fail the day anyone lands a legitimate later migration, forcing an
    unrelated author to edit this unit's test. The gap is deliberate
    and already sanctioned (test_049_exists_and_sorts_after_047_with
    _only_the_reserved_048_between); migrate.py applies the sorted
    glob, so gaps are harmless."""
    assert MIGRATION.exists()
    num = int(MIGRATION.name[:3])
    reserved = _reserved_numbers()
    assert reserved, reserved
    assert 50 in reserved and 51 in reserved
    assert num not in reserved, (num, reserved)
    names = [p.name for p in MIGRATIONS.glob("*.sql")]
    assert sum(1 for f in names if f.startswith(f"{num:03d}_")) == 1
    assert not [f for f in names
                if f.startswith("050_") or f.startswith("051_")]


def test_the_migration_is_rerunnable():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS copy_exit_legs" in sql
    for col in ("ts", "market_slug", "shares", "trade_qty", "price",
                "proceeds_usd", "realized_usd", "order_id", "side",
                "side_src", "source", "row_id", "folded_at"):
        assert f"ADD COLUMN IF NOT EXISTS {col} " in sql, col
    # every constraint is dropped before it is added (045's pattern:
    # Postgres has no ADD CONSTRAINT IF NOT EXISTS)
    for name in ("copy_exit_legs_side_check",
                 "copy_exit_legs_named_columns",
                 "copy_exit_legs_unknown_is_moneyless",
                 "copy_exit_legs_buy_close_no_proceeds"):
        assert f"DROP CONSTRAINT IF EXISTS {name}" in sql, name
        assert f"ADD CONSTRAINT {name}" in sql, name
    # only the indexes this unit's own reads use
    assert sql.count("CREATE INDEX IF NOT EXISTS") == 2
    assert "DROP TABLE" not in sql and "DELETE FROM" not in sql


def test_the_repair_path_cannot_leave_a_money_column_unnamed():
    """CREATE TABLE declares market_slug/side/side_src/source NOT NULL;
    `ADD COLUMN IF NOT EXISTS` on a table that already has rows cannot.
    A NULL `side` would pass `CHECK (side IN (...))` -- a CHECK fails
    only on false -- and then be excluded by every reader's
    `FILTER (WHERE side = 'sell')` while still carrying money. So the
    NOT NULL is restated as an IS NOT NULL CHECK, which the repair path
    does apply."""
    sql = MIGRATION.read_text()
    assert "CHECK (side IS NOT NULL AND side IN " in sql
    assert ("CHECK (market_slug IS NOT NULL AND side_src IS NOT NULL "
            "AND source IS NOT NULL)" in sql)


def test_the_migration_carries_every_field_the_fold_produces():
    """The plan names the ledger's columns; the fold produces exactly
    them, and the insert writes exactly them."""
    sql = MIGRATION.read_text()
    leg = _sentinel_leg()
    for name in leg.__dataclass_fields__:
        assert name in sql, name
    assert len(leg.as_params()) == len(sl.INSERT_COLUMNS) == 12
    # row_id is a column the FOLD never writes: which of OUR rows a
    # sale closed is not a question this data can answer
    assert "row_id" in sql and "row_id" not in sl.INSERT_COLUMNS


def test_the_database_pins_what_the_fold_promises():
    """The CHECKs are the fold's rules restated where no later writer
    can get around them: only three sides exist, an `unknown` leg
    carries no money, and a short-closing BUY takes no proceeds -- but
    KEEPS its trade_qty, because a short is closed by a BUY and the
    next unit has to know how many shares that leg retired."""
    sql = MIGRATION.read_text()
    assert "CHECK (side IS NOT NULL AND side IN ('sell', 'buy_close', 'unknown'))" in sql
    assert ("CHECK (side <> 'unknown' OR (proceeds_usd = 0 AND "
            "realized_usd = 0 AND shares = 0))" in sql)
    assert ("CHECK (side <> 'buy_close' OR (proceeds_usd = 0 AND "
            "shares = 0))" in sql)
    # trade_qty is deliberately outside both money CHECKs
    for line in sql.splitlines():
        if line.strip().startswith("CHECK"):
            assert "trade_qty" not in line, line


def test_the_migration_states_its_retention_story():
    """Append-only, no TTL, no partition -- and rebuildable from the
    archive, which is why no prune has to be invented now. Stated on
    the file's own face rather than left to be discovered."""
    sql = MIGRATION.read_text()
    assert "RETENTION" in sql
    assert "rebuildable" in sql


def test_no_served_number_reads_this_ledger():
    """U1 changes no served figure. Nothing outside this unit imports
    the module, and no endpoint or worker names the table.

    This test failing is not a bug in it: it means something started
    READING the ledger, and at that moment "changes no served number"
    stops being true and has to be re-argued -- with the gate's
    production half run first. Update it deliberately, never to make
    the suite green.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
    hits = [str(p) for p in root.rglob("*.py")
            if p.name != "sale_legs.py"
            and ("copy_exit_legs" in p.read_text()
                 or "sale_legs" in p.read_text())]
    assert hits == [], f"the shadow ledger has a reader now: {hits}"
