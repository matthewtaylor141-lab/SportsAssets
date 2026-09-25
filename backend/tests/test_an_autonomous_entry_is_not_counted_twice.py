"""WHOSE ACQUISITION IS IT: the seed, or the ledger?

THE DEFECT, MEASURED. The external-valuation entry lane writes its
acquisition TWICE by design -- once as the position's `seed_qty`/`seed_price`
(a summary: the filled quantity and its vwap) and once as the order and fills
that produced it, with our fees. `reload_managed` booked BOTH: the seed
through `Portfolio.buy` in `Managed.__init__`, then the same contracts again
by replaying the fills.

Measured on the controlled positive-branch run: a 900-contract entry -- one
order, three fills of 400/300/200 -- reloaded as **1800 held**, and the
manager wrote `HOLD x1800` valuing contracts it did not hold (EV 167.57
against the correct 83.79). Residual inventory did not survive reload, which
is the acceptance assertion this closes.

WHY THE SEED BUY EXISTS AT ALL, in `Managed.__init__`'s own words: "the seed
is ASSIGNED at RN1's own fill price -- it is not an execution of ours and no
fee of ours was paid." That is exactly the distinction. An RN1-seeded
position's acquisition is SOMEONE ELSE'S fill and is not in our ledger, so it
must be booked. An autonomous entry's acquisition is OURS and IS in our
ledger, so it must not be booked twice.

`source_trade_id` carries that distinction already and is not invented here:
non-null for a seeded position, NULL for an autonomous entry. A position row
that does not carry the column keeps the old behaviour, so no existing caller
changes.
"""

from __future__ import annotations

from sportsassets import bettor_mgmt_lifecycle as lc

SEED_QTY = 900.0
SEED_PX = 0.635556
AT = 1_790_000_000.0


def _fee(qty, price, maker=False):
    return round(abs(float(qty)) * 0.02 * float(price), 6)


def _position(**over):
    p = {"position_id": "P1", "condition_id": "c1", "outcome_index": 0,
         "seed_qty": SEED_QTY, "seed_price": SEED_PX, "decision_ts": AT}
    p.update(over)
    return p


def _entry_order_and_fills():
    """The entry lane's own shape: ONE order, THREE fills summing 900."""
    order = {"order_id": "P1:o1", "condition_id": "c1", "outcome_index": 0,
             "side": "BUY", "intent": "ORDER_INTENT_BUY_LONG",
             "limit_price": 0.7087, "qty": SEED_QTY, "placed_at": AT,
             "decision_id": "d1"}
    fills = [{"order_id": "P1:o1", "at": AT + 1, "qty": 400.0,
              "price": 0.62, "fee_usd": 6.55},
             {"order_id": "P1:o1", "at": AT + 2, "qty": 300.0,
              "price": 0.64, "fee_usd": 4.80},
             {"order_id": "P1:o1", "at": AT + 3, "qty": 200.0,
              "price": 0.66, "fee_usd": 3.12}]
    return [order], fills


def test_an_autonomous_entry_reloads_to_exactly_what_its_fills_bought():
    orders, fills = _entry_order_and_fills()
    m = lc.reload_managed(position=_position(source_trade_id=None),
                          orders=orders, fills=fills, fee_fn=_fee)
    assert m.held() == 900.0, (
        "the ledger's fills bought 900; booking the seed as well made it "
        "1800 and the manager valued HOLD on contracts it did not hold")
    assert m.assign_seed is False
    # AND THE BASIS COMES FROM THE FILLS, fees included -- which is the
    # true cost, not the fee-free vwap the summary carries.
    assert m.pf.inventory_cost() > 0.0


def test_a_seeded_position_still_books_its_assigned_entry():
    """RN1's behaviour is unchanged: its seed is not in our ledger."""
    orders, fills = _entry_order_and_fills()
    m = lc.reload_managed(
        position=_position(source_trade_id="0xdeadbeef"),
        orders=orders, fills=fills, fee_fn=_fee)
    assert m.assign_seed is True
    assert m.held() == 1800.0, (
        "an assigned seed of 900 plus 900 of our own later executions is "
        "1800 held, and that is correct for a seeded position")


def test_a_position_row_without_the_column_keeps_the_old_behaviour():
    """No existing caller changes. Absent is not NULL."""
    orders, fills = _entry_order_and_fills()
    m = lc.reload_managed(position=_position(), orders=orders, fills=fills,
                          fee_fn=_fee)
    assert m.assign_seed is True
    assert m.held() == 1800.0


def test_the_caller_can_say_so_explicitly_either_way():
    orders, fills = _entry_order_and_fills()
    off = lc.reload_managed(position=_position(source_trade_id="x"),
                            orders=orders, fills=fills, fee_fn=_fee,
                            assign_seed=False)
    assert off.held() == 900.0
    on = lc.reload_managed(position=_position(source_trade_id=None),
                           orders=orders, fills=fills, fee_fn=_fee,
                           assign_seed=True)
    assert on.held() == 1800.0


def test_withholding_the_seed_leaves_no_phantom_basis():
    """A skipped seed must not leave `seed_basis` describing contracts the
    portfolio does not hold, or the ranking gets its per-contract basis
    from a number with no inventory behind it."""
    m = lc.reload_managed(position=_position(source_trade_id=None),
                          orders=(), fills=(), fee_fn=_fee)
    assert m.held() == 0.0
    assert m.seed_basis == 0.0
    # The summary is still readable -- it is just not booked.
    assert m.seed_qty == SEED_QTY
    assert m.seed_price == SEED_PX
