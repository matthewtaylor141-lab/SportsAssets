"""The reconciler touches a live account. Prove it cannot trade.

This module runs against the real venue with real credentials, which is
the whole point of it -- internal-ledger agreement is not venue
confirmation. That also makes it the most dangerous read in the
repository, so "it only reads" has to be a checked property rather than
a sentence in a docstring.

The checks are structural: an AST walk over the module's own source for
any reference to an order-capable name, and a behavioural pass proving
the matching arithmetic is right without a venue at all.
"""

import ast
import inspect
import os

import pytest

from sportsassets import venue_reconcile as vr

# Everything in pmus (and elsewhere) that can move money or an order.
FORBIDDEN = (
    "submit_fok", "cancel_order", "close_position", "place_order",
    "post_order", "mirror_exit", "execute_copy", "maybe_execute",
    "_execute_manual", "_execute_manual_limit", "_execute_manual_sell",
    "execute_manual_sell", "replace_order", "amend_order",
)


def _tree():
    return ast.parse(inspect.getsource(vr))


def test_the_module_names_no_order_function():
    """THE CONTROL. Not 'we did not call one' -- the name does not
    appear, so no later edit can reach one by accident either."""
    src = inspect.getsource(vr)
    tree = ast.parse(src)
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name:
                seen.add(name)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.Name):
            seen.add(node.id)
    for bad in FORBIDDEN:
        assert bad not in seen, "%s is reachable from venue_reconcile" % bad


def test_it_never_writes_to_the_database():
    """A reconciliation that edits its inputs is not a reconciliation.
    Only SELECT reaches the pool."""
    src = inspect.getsource(vr).upper()
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE",
                 "ALTER TABLE", "DROP "):
        assert verb not in src, verb
    calls = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            calls.add(getattr(node.func, "attr", None))
    assert "execute" not in calls, "pool.execute is a write path"
    assert "executemany" not in calls


def test_no_credential_is_read_printed_or_returned():
    src = inspect.getsource(vr)
    for tok in ("PMUS_KEY_ID", "PMUS_SECRET_KEY", "PM_PRIVATE_KEY",
                "api_key", "secret_key", "SECRET"):
        assert tok not in src, tok


# ── the arithmetic, without a venue ──────────────────────────────────

def _act(oid, slug, shares, px, ts, tid="t1"):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"id": tid, "marketSlug": slug,
                      "quantity": shares, "price": px,
                      "createTime": ts,
                      "order": {"id": oid}}}


def test_several_executions_of_one_order_sum_to_one_row(monkeypatch):
    """One order can fill in pieces; the ledger records the order. If
    these were not summed every partially-filled order would report a
    quantity mismatch against a venue that did nothing wrong."""
    monkeypatch.setattr(vr.pmus, "trade_own_order",
                        lambda t: t.get("order") or {})
    acts = [_act("o1", "mkt", 100, 0.40, 1_700_000_000, "t1"),
            _act("o1", "mkt", 50, 0.44, 1_700_000_060, "t2")]
    idx = vr.index_venue_trades(acts)
    assert set(idx) == {"o1"}
    assert idx["o1"]["shares"] == 150
    assert idx["o1"]["executions"] == 2
    # 100*0.40 + 50*0.44 = 62.0 over 150 shares
    assert round(idx["o1"]["notional"], 6) == 62.0
    assert round(idx["o1"]["avg_price"], 6) == round(62.0 / 150, 6)


def test_a_matching_row_is_a_match(monkeypatch):
    monkeypatch.setattr(vr.pmus, "trade_own_order",
                        lambda t: t.get("order") or {})
    idx = vr.index_venue_trades([_act("o1", "mkt", 100, 0.40, 1)])
    rows = vr.reconcile_rows([{
        "id": 1, "order_id": "o1", "us_market_slug": "mkt",
        "status": "filled", "side": "BUY", "placed_at": "x",
        "filled_shares": 100, "filled_usd": 40.0, "fill_price": 0.40}], idx)
    assert rows[0]["verdict"] == "MATCH"


def test_a_ledger_row_the_venue_never_saw_is_named(monkeypatch):
    """THE FINDING THAT MATTERS MOST. A fill record with no venue trade
    behind it is not a small discrepancy -- it is a position we believe
    we hold and may not."""
    monkeypatch.setattr(vr.pmus, "trade_own_order",
                        lambda t: t.get("order") or {})
    rows = vr.reconcile_rows([{
        "id": 7, "order_id": "ghost", "us_market_slug": "mkt",
        "status": "filled", "side": "BUY", "placed_at": "x",
        "filled_shares": 100, "filled_usd": 40.0, "fill_price": 0.40}], {})
    assert rows[0]["verdict"] == "NOT_FOUND_AT_VENUE"


def test_a_quantity_difference_is_not_filed_as_a_price_difference(
        monkeypatch):
    monkeypatch.setattr(vr.pmus, "trade_own_order",
                        lambda t: t.get("order") or {})
    idx = vr.index_venue_trades([_act("o1", "mkt", 60, 0.40, 1)])
    rows = vr.reconcile_rows([{
        "id": 1, "order_id": "o1", "us_market_slug": "mkt",
        "status": "filled", "side": "BUY", "placed_at": "x",
        "filled_shares": 100, "filled_usd": 40.0, "fill_price": 0.40}], idx)
    assert rows[0]["verdict"] == "QUANTITY_MISMATCH"
    assert rows[0]["delta_shares"] == 40.0


def test_same_quantity_different_money_is_its_own_verdict(monkeypatch):
    """Fees live in one figure and not the other. That is a known,
    explainable difference and must not be filed next to a quantity
    mismatch, which is not."""
    monkeypatch.setattr(vr.pmus, "trade_own_order",
                        lambda t: t.get("order") or {})
    idx = vr.index_venue_trades([_act("o1", "mkt", 100, 0.40, 1)])
    rows = vr.reconcile_rows([{
        "id": 1, "order_id": "o1", "us_market_slug": "mkt",
        "status": "filled", "side": "BUY", "placed_at": "x",
        "filled_shares": 100, "filled_usd": 41.25, "fill_price": 0.4125}],
        idx)
    assert rows[0]["verdict"] == "PRICE_OR_FEE_DIFFERENCE"
    assert round(rows[0]["delta_usd"], 2) == 1.25


def test_a_row_with_no_order_id_cannot_be_matched_either_way(monkeypatch):
    rows = vr.reconcile_rows([{
        "id": 3, "order_id": None, "us_market_slug": "mkt",
        "status": "filled", "side": "BUY", "placed_at": "x",
        "filled_shares": 10, "filled_usd": 4.0, "fill_price": 0.40}], {})
    assert rows[0]["verdict"] == "NO_ORDER_ID_RECORDED"


def test_resolution_proceeds_are_summed_per_market():
    acts = [{"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
             "positionResolution": {"marketSlug": "mkt", "payout": 120.0,
                                    "quantity": 120, "createTime": 5}},
            {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
             "positionResolution": {"marketSlug": "mkt", "payout": 30.0,
                                    "quantity": 30, "createTime": 9}}]
    idx = vr.index_resolutions(acts)
    assert idx["mkt"]["proceeds"] == 150.0
    assert idx["mkt"]["events"] == 2
    assert idx["mkt"]["ts"] == 9


def test_the_summary_keeps_the_valuation_methods_apart():
    """Acquisition cost, cost basis, mark to bid and settled proceeds
    are four different numbers. Reporting one under another's name is
    how $16,180.53 of historical cost became 'current exposure'."""
    s = vr.summarise([], {}, [], {}, [], {}, "now")
    m = s["valuation_methods"]
    assert set(m) == {"historical_acquisition_cost",
                      "remaining_holdings_cost_basis",
                      "remaining_holdings_mark_to_bid",
                      "settled_proceeds",
                      "outstanding_commitments"}
    for k in ("historical_acquisition_cost_all_records",
              "remaining_holdings_cost_basis",
              "remaining_holdings_mark_to_bid",
              "settled_proceeds", "outstanding_order_commitments"):
        assert k in s


def test_this_test_file_calls_no_order_function():
    src = open(__file__).read()
    called = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name:
                called.add(name)
    for bad in FORBIDDEN:
        assert bad not in called, bad
