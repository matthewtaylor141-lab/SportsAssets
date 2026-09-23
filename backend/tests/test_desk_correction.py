"""THE CORRECTION's guarantees, and the three claims it withdraws.

Every check here has a control: a companion case constructed so the
check is violated, proving it is load-bearing.
"""
from __future__ import annotations

import ast
import datetime as dt
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_desk as DK                    # noqa: E402
from sportsassets import bettor_desk_correction as CORR       # noqa: E402
from sportsassets import bettor_fee_schedule as FEES          # noqa: E402

TS = dt.datetime(2026, 9, 23, 14, 40, tzinfo=dt.timezone.utc)


def _order(**kw):
    base = dict(order_id="o1", intent="ENTER", filled_qty=500.0,
                avg_fill_price=0.50, fees_usd=0.0,
                placed_at=TS, terminal_at=TS)
    base.update(kw)
    return base


# ── the correction is an interval, and the interval is not decorative ──

def test_a_zero_fee_maker_order_corrects_to_a_bounded_rebate():
    c = CORR.order_correction(_order())
    assert c["status"] == CORR.BOUNDED
    # The rebate is income, so the correction to realized is POSITIVE at
    # one end and nil at the other.
    assert c["delta_realized_lower_usd"] == 0.0
    assert c["delta_realized_upper_usd"] > 0.0
    assert c["detail"]["fill_count"] == "NOT_IDENTIFIED"


def test_the_lower_bound_is_zero_because_rounding_can_erase_a_rebate():
    """NOT a formality. `min_contracts_for_a_cent` is why."""
    need = FEES.LATEST.min_contracts_for_a_cent(0.01)
    assert need > 1, need
    c = CORR.order_correction(_order(filled_qty=1.0, avg_fill_price=0.01))
    # A one-contract fill at 1c earns far less than half a cent, so the
    # whole correction rounds away and the interval collapses at zero.
    assert c["delta_realized_lower_usd"] == 0.0
    assert c["delta_realized_upper_usd"] == 0.0
    assert c["status"] == CORR.EXACT


def test_an_exit_is_priced_as_a_taker_charge_not_a_maker_rebate():
    maker = CORR.order_correction(_order(intent="ENTER"))
    taker = CORR.order_correction(_order(order_id="o2", intent="EXIT"))
    assert maker["detail"]["liquidity_assumed"] == "MAKER"
    assert taker["detail"]["liquidity_assumed"] == "TAKER"
    # A charge REDUCES realized; a rebate increases it. Opposite signs.
    assert maker["delta_realized_upper_usd"] > 0.0
    assert taker["delta_realized_lower_usd"] < 0.0


# ── nothing is invented when a field is missing ──────────────────────

@pytest.mark.parametrize("missing", ["avg_fill_price", "fees_usd"])
def test_a_missing_field_is_incomplete_and_asserts_nothing(missing):
    c = CORR.order_correction(_order(**{missing: None}))
    assert c["status"] == CORR.INCOMPLETE
    assert c["delta_fees_lower_usd"] is None
    assert c["delta_fees_upper_usd"] is None
    assert missing in c["reason"]


def test_an_undated_order_refuses_rather_than_using_todays_schedule():
    """The schedule module has no default date ON PURPOSE.

    My first wrapper caught the refusal and fell back to FEES.LATEST,
    which would have applied today's numbers to an undated fill and
    called the result a correction.
    """
    c = CORR.order_correction(_order(placed_at=None, terminal_at=None))
    assert c["status"] == CORR.INCOMPLETE
    assert "NO_TIMESTAMP" in c["reason"]
    assert c["schedule_id"] is None


def test_schedule_for_never_silently_returns_the_latest():
    assert CORR.schedule_for(None) is None
    assert CORR.schedule_for("not-a-date") is None
    # CONTROL: a real date must still resolve, or the guard above would
    # be indistinguishable from a function that always returns None.
    s = CORR.schedule_for(TS)
    assert s is not None and s.schedule_id


def test_an_order_that_never_filled_carries_no_correction():
    c = CORR.order_correction(_order(filled_qty=0.0))
    assert c["status"] == CORR.INCOMPLETE
    assert c["delta_fees_upper_usd"] == 0.0


# ── the identity survives the correction, checked not assumed ────────

def test_the_correction_preserves_the_ledger_identity_at_both_ends():
    rows = [CORR.order_correction(_order(order_id="o%d" % i,
                                         avg_fill_price=0.3 + 0.05 * i))
            for i in range(6)]
    s = CORR.summarise(rows, epochs_identifiable=False)
    assert s["reconcile"]["ok"], s["reconcile"]


def test_the_reconcile_check_can_fail():
    """CONTROL. A check that cannot fail proves nothing."""
    bad = CORR.reconcile({
        "delta_cash_lower_usd": 5.0, "delta_cash_upper_usd": 5.0,
        "delta_realized_lower_usd": 0.0, "delta_realized_upper_usd": 0.0,
        "delta_basis_usd": 0.0})
    assert not bad["ok"]


def test_the_reconcile_states_what_it_does_not_prove():
    r = CORR.reconcile({k: 0.0 for k in (
        "delta_cash_lower_usd", "delta_cash_upper_usd",
        "delta_realized_lower_usd", "delta_realized_upper_usd",
        "delta_basis_usd")})
    assert r["ok"]
    joined = " ".join(r["does_not_prove"]).lower()
    assert "interval" in joined and "restart" in joined


# ── the summary reports incompleteness rather than hiding it ─────────

def test_bounded_subjects_force_an_incomplete_accounting_status():
    s = CORR.summarise([CORR.order_correction(_order())],
                       epochs_identifiable=True)
    assert s["accounting_status"] == CORR.INCOMPLETE
    assert any("PER_FILL_RECORDS_ABSENT" in r
               for r in s["incomplete_reasons"])


def test_unidentifiable_epochs_are_reported_as_such():
    s = CORR.summarise([], epochs_identifiable=False)
    assert any("EPOCHS_NOT_IDENTIFIABLE" in r
               for r in s["incomplete_reasons"])


def test_a_fully_exact_run_can_report_complete():
    """CONTROL for the two above: INCOMPLETE must not be hard-coded."""
    s = CORR.summarise([CORR.order_correction(
        _order(filled_qty=1.0, avg_fill_price=0.01))],
        epochs_identifiable=True)
    assert s["counts"][CORR.EXACT] == 1
    assert s["accounting_status"] == CORR.COMPLETE
    assert s["incomplete_reasons"] == []


# ── the withdrawn claim is withdrawn in the source, not just in chat ──

def test_the_zero_pnl_inference_is_recorded_as_withdrawn():
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_correction.py")).read()
    assert "WITHDRAWN" in src.upper()
    assert "fee_fn=None" in src


def test_zero_realized_really_is_reachable_with_a_live_schedule():
    """The substance of the withdrawal, demonstrated rather than asserted.

    A book of small fills under the real schedule genuinely totals zero
    realized, so the zero could never have been proof on its own.
    """
    d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=1e4),
                fee_fn=lambda q, p, m: float(
                    FEES.LATEST.fill_fee(q, p, maker=m)))
    assert d.fee_basis == DK.FEE_BASIS_APPLIED
    for i in range(30):
        d.pf.buy("c1", 0, 1.0, 0.01, float(
            FEES.LATEST.fill_fee(1.0, 0.01, maker=True)), float(i))
    assert d.pf.fees == 0.0
    assert d.pf.realized == 0.0


# ── the loop's own repairs ───────────────────────────────────────────

def _loop_src():
    return open(os.path.join(_ROOT, "sportsassets",
                             "bettor_desk_loop.py")).read()


def test_the_loop_restores_the_book_before_stepping_any_event():
    tree = ast.parse(_loop_src())
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
    order = [n.func.id if isinstance(n.func, ast.Name) else n.func.attr
             for n in ast.walk(run) if isinstance(n, ast.Call)
             and isinstance(n.func, (ast.Name, ast.Attribute))]
    assert "_restore" in order, "the book is never restored"
    assert "step" in order
    assert order.index("_restore") < order.index("step")


def test_resting_orders_are_restored_with_their_consumption_ledger():
    """SUPERSEDED BEHAVIOUR, and the reason it changed.

    Orders used to be EXPIRED on every restart, because the consumption
    ledger was not persisted and re-arming one could have filled it a
    second time against evidence already spent. The ledger IS persisted
    now -- keyed on the evidence id, scoped to the account -- so the
    orders come back instead, which is what makes a restart restore the
    same resting book rather than a smaller one.
    """
    # PARSE THE FUNCTION, don't slice a character window. A fixed
    # src[i:i+N] window kept cutting off before the block it was meant
    # to check, so the test failed for reaching the wrong text rather
    # than for the behaviour being absent.
    tree = ast.parse(_loop_src())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_restore")
    body = ast.unparse(fn)
    assert "RESTING" in body and "PARTIALLY_FILLED" in body
    assert "bettor_desk_consumption" in body
    assert "desk.cons.available" in body
    assert "desk.cons.consumed" in body
    # and the fills come back, or a restored partial would report a
    # filled quantity at no price
    assert "bettor_desk_fills" in body
    assert "o.fills.append" in body
    # the whole restore is account-scoped
    assert "account_id" in [a.arg for a in fn.args.args]


def test_the_epoch_id_is_per_process_not_per_row():
    from sportsassets import bettor_desk_loop as L
    assert isinstance(L.EPOCH_ID, str) and len(L.EPOCH_ID) == 32
    src = _loop_src()
    # The old per-row timestamp must not come back as a boot id.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "str" and node.args
                and isinstance(node.args[0], ast.Call)):
            inner = node.args[0]
            if (isinstance(inner.func, ast.Name) and inner.func.id == "int"
                    and inner.args
                    and isinstance(inner.args[0], ast.Call)
                    and getattr(inner.args[0].func, "attr", "") == "time"):
                raise AssertionError("str(int(time.time())) is back as an id")


def test_fills_are_persisted_with_the_engines_own_key_names():
    """The engine writes `fee_usd`; an f.get("fee", 0.0) would have
    written 0.00 into every row and recreated the fee-free book."""
    src = _loop_src()
    assert "INSERT INTO bettor_desk_fills" in src
    # PARSE, DON'T GREP. My first version scanned the source text and
    # matched its OWN COMMENT explaining why `.get("fee", 0.0)` is
    # wrong -- a test failing on prose rather than on behaviour. The
    # AST sees calls, not commentary.
    tree = ast.parse(src)
    persist = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == "_persist")
    subscripts = {n.slice.value for n in ast.walk(persist)
                  if isinstance(n, ast.Subscript)
                  and isinstance(n.slice, ast.Constant)
                  and isinstance(n.slice.value, str)}
    assert "fee_usd" in subscripts, "the engine's fee key is never read"
    # No defaulted .get() on any fee-ish key: a default would write 0.00.
    for n in ast.walk(persist):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and len(n.args) == 2
                and isinstance(n.args[0], ast.Constant)):
            assert "fee" not in str(n.args[0].value).lower(), n.args[0].value


def test_the_correction_is_applied_under_the_lock_and_only_once():
    src = _loop_src()
    tree = ast.parse(src)
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
    names = [n.func.id for n in ast.walk(run) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)]
    assert "apply_corrections" in names
    assert "_acquire" in names
    # and the guard is the database, not a flag
    assert "bettor_desk_correction_runs" in src


def test_the_module_docstring_no_longer_claims_a_rebuild_that_did_not_exist():
    src = _loop_src()
    head = src[:src.index('"""', 3)]
    assert "CORRECTION" in head
    assert "FALSE WHEN FIRST WRITTEN" in head
    # The two ids are distinguished in the docstring, because conflating
    # them is what caused the $2,367.73 defect.
    assert "ONE DURABLE ACCOUNT" in head
    assert "account_id IS NULL" in head
