"""An open position can already contain a realised gain or loss.

Owner requirement: "An open position can contain realized gains or losses
from quantities already sold. Show proceeds, allocated basis, fees and
remaining inventory separately. Do not treat open-position net cash or
closed-position-only P&L as a substitute."

THE GAP. `realised()` filters `closed_at IS NOT NULL`, which is right for
the drawdown and the loss stop -- an open position's outcome is not yet
determined -- but it reports 0.00 for a position that has already sold
part of its inventory at a loss. The controlled demonstration showed this
directly: 9 of 15 contracts sold at 0.41 on a 0.60 basis, and the lane
reported realised 0.00 with -$5.71 of open-position net cash.

NEITHER SUBSTITUTE IS THE NUMBER. Net cash is (basis out - proceeds back
+ fees) over the WHOLE clip, so it overstates the partial loss by the
remaining basis. These tests pin the arithmetic that separates them.
"""

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB

from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    ACCT, DSN, EVENT, PAYS_ON, SLUG, VENUE, _clean, _seed, pg,
)


async def _entry(conn, *, intent_id, qty, price, fee=None):
    from sportsassets import bettor_funded_execution as FX

    coll = FX.collateral_for(price, qty, FX.LONG)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
        order_intent=FX.LONG, limit_price=price, quantity=qty,
        collateral_usd=coll, effective_digest="d", payout_event=PAYS_ON,
        held_is_long=True)
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, intent_id,
                                    venue_order_id="vo-%s" % intent_id,
                                    status="open")
    f = {"qty": float(qty), "price": price, "venue_fill_id": "vf-e-%s" % intent_id}
    if fee is not None:
        f["commission_usd"] = fee
    await FB.ingest_fills(conn, intent_id, [f])
    return got


async def _sell(conn, monkeypatch, *, parent, qty, price, fee=None):
    """Sell through the PRODUCTION path: submit_exit reserves and books.

    `record_intent` does not accept `kind`/`parent_intent_id` -- the EXIT
    child is created by `_reserve_exit` inside `submit_exit`, which is the
    only writer of that row. My first version of this helper tried to
    insert the child directly and got a TypeError, which was the code
    correctly refusing to let a test fabricate an exit.
    """
    from sportsassets import bettor_funded_management as FM

    from tests.test_the_funded_lifecycle_is_complete import _level, _transport

    execs = [{"id": "vx-%s-%s" % (parent, qty),
              "type": "EXECUTION_TYPE_FILL",
              "lastPx": {"value": "%.2f" % price},
              "lastShares": int(qty),
              "order": {"state": "ORDER_STATE_FILLED"}}]
    if fee is not None:
        execs[0]["commissionNotionalTotalCollected"] = {"value": "%.4f" % fee}
    _transport(monkeypatch, order_id="vo-x-%s" % parent,
               bids=[_level(price, int(qty))], exec_by_call=[execs])
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
    import time as _t
    got = await FM.submit_exit(
        conn, intent_id=parent, limit_price=price, quantity=qty,
        venue=VENUE, inputs_expire_at=_t.time() + 600.0)
    assert got.get("submitted") is True, got
    return got


# ═════════════════════════════════════════════════════════════════════
# 1 · THE ARITHMETIC, ON THE DEMONSTRATION'S OWN NUMBERS
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_partial_loss_is_reported_on_the_sold_quantity(monkeypatch):
    """Long 15 @ 0.60, sell 9 @ 0.41. Fee-free so the basis is visible."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await _seed(conn)
        await _entry(conn, intent_id="pe-1", qty=15, price=0.60, fee=0.0)
        await _sell(conn, monkeypatch, parent="pe-1", qty=9,
                    price=0.41, fee=0.0)

        r = await FB.realised_on_sold(conn, "pe-1")
        assert r["ok"] is True
        # THE COMPONENTS, EACH ON ITS OWN LINE.
        assert r["sold_qty"] == pytest.approx(9.0)
        assert r["exit_proceeds_usd"] == pytest.approx(3.69, abs=1e-6)
        assert r["basis_per_contract"] == pytest.approx(0.60, abs=1e-8)
        assert r["allocated_basis_usd"] == pytest.approx(5.40, abs=1e-6)
        assert r["fees_on_sold_usd"] == pytest.approx(0.0, abs=1e-9)
        # 3.69 - 5.40 = -1.71. THE number the demonstration could not show.
        assert r["realised_on_sold_usd"] == pytest.approx(-1.71, abs=1e-6)
        # AND THE SIDE STILL AT RISK, KEPT APART.
        assert r["residual_qty"] == pytest.approx(6.0)
        assert r["remaining_basis_usd"] == pytest.approx(3.60, abs=1e-6)
        assert r["position_closed"] is False
        # The identity holds on the returned components themselves.
        assert r["realised_on_sold_usd"] == pytest.approx(
            r["exit_proceeds_usd"] - r["allocated_basis_usd"]
            - r["fees_on_sold_usd"], abs=1e-9)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_realised_now_books_the_partial_while_the_position_is_open(monkeypatch):
    """The partial result IS realised P&L. This test used to deny that.

    ── WHAT THIS TEST USED TO ASSERT, AND WHY IT WAS WRONG ───────────
    It was named `test_closure_based_realised_still_reads_zero_and_that_is
    _correct` and it required `pnl()["realised_pnl_usd"] == 0.00` on a
    position that had already sold 9 of 15 contracts at a loss. The
    docstring said "both numbers coexist; neither replaces the other".

    The coexistence was real. The word "correct" was not. Every consumer of
    MAX_DRAWDOWN reads that zero -- `check_rails`, which refuses new
    exposure, the management surface's loss stop, and this very function --
    so the approved stop could be breached by any amount without tripping.
    A test asserting that a control cannot see the loss it exists to stop
    had turned the defect into a requirement, which is the second time this
    repository has done that (the first was the M1 sequencing premise).

    `realised()` now books each exit fill as an increment at its own
    instant, so the loss appears here while the position is still open, and
    `closed_positions` still correctly reads 0 -- nothing HAS closed. Those
    two facts were never in tension; only the inference was.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await _seed(conn)
        await _entry(conn, intent_id="pe-2", qty=15, price=0.60, fee=0.0)
        await _sell(conn, monkeypatch, parent="pe-2", qty=9,
                    price=0.41, fee=0.0)

        pl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        sold = await FB.realised_on_sold(conn, "pe-2")
        # THE LOSS IS BOOKED, while the position is open.
        assert pl["realised_pnl_usd"] == pytest.approx(-1.71, abs=1e-6)
        assert pl["max_drawdown_usd"] == pytest.approx(1.71, abs=1e-6)
        # AND NOTHING HAS CLOSED. Both are true at once; the old test read
        # the second as licence to report the first as zero.
        assert pl["closed_positions"] == 0
        assert pl["partially_realised_open_positions"] == 1
        assert pl["partially_realised_usd"] == pytest.approx(-1.71, abs=1e-6)
        # THE SINGLE-POSITION READER AGREES, because both use
        # `partial_realisation`.
        assert sold["realised_on_sold_usd"] == pytest.approx(-1.71, abs=1e-6)
        # AND NET CASH IS NEITHER. -9.00 out, 3.69 back, no fees = -5.31.
        net = pl["open_position_net_cash_usd"]
        assert net == pytest.approx(-5.31, abs=1e-6)
        assert net == pytest.approx(
            sold["realised_on_sold_usd"] - sold["remaining_basis_usd"]
            - sold["entry_fees_allocated_to_residual_usd"], abs=1e-6), (
            "net cash must equal the sold result MINUS the basis still held "
            "MINUS the residual's share of the entry fee; if that stops "
            "holding, one of the two figures is wrong")
        assert sold["net_cash_exceeds_this_by_usd"] == pytest.approx(
            3.60, abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_fees_are_allocated_to_the_sold_fraction(monkeypatch):
    """An entry fee was paid on the whole clip; only its sold share counts."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await _seed(conn)
        # Entry fee 0.30 on 15 contracts; sell 9, so 9/15 = 0.18 belongs.
        await _entry(conn, intent_id="pe-3", qty=15, price=0.60, fee=0.30)
        await _sell(conn, monkeypatch, parent="pe-3", qty=9,
                    price=0.41, fee=0.09)
        r = await FB.realised_on_sold(conn, "pe-3")
        assert r["entry_fees_total_usd"] == pytest.approx(0.30, abs=1e-6)
        assert r["entry_fees_allocated_to_sold_usd"] == pytest.approx(
            0.18, abs=1e-6)
        assert r["exit_fees_usd"] == pytest.approx(0.09, abs=1e-6)
        # ALL of the exit fee, only the sold share of the entry fee.
        assert r["fees_on_sold_usd"] == pytest.approx(0.27, abs=1e-6)
        assert r["realised_on_sold_usd"] == pytest.approx(
            3.69 - 5.40 - 0.27, abs=1e-6)
        # AND THE RESIDUAL'S SHARE IS KEPT, NOT DISCARDED: 0.30 * 6/15.
        assert r["entry_fees_allocated_to_residual_usd"] == pytest.approx(
            0.12, abs=1e-6)
        # THE TWO SHARES MUST EXHAUST THE ENTRY FEE. If they did not, some of
        # what the account paid would belong to neither side.
        assert (r["entry_fees_allocated_to_sold_usd"]
                + r["entry_fees_allocated_to_residual_usd"]) == pytest.approx(
            r["entry_fees_total_usd"], abs=1e-9)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_net_cash_differs_by_two_terms_not_one_when_fees_exist(monkeypatch):
    """THE IDENTITY I FIRST GOT WRONG, pinned with fees present.

    I wrote that open-position net cash differs from the sold result by the
    remaining basis. With fees on the clip that is FALSE: net cash also
    carries the residual's share of the entry fee, because that fee was paid
    on contracts still held. On the demonstration's own numbers the one-term
    identity misses by 0.10. This test exists so it cannot be written that
    way again.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await _seed(conn)
        await _entry(conn, intent_id="pe-7", qty=15, price=0.60, fee=0.25)
        await _sell(conn, monkeypatch, parent="pe-7", qty=9,
                    price=0.41, fee=0.15)
        r = await FB.realised_on_sold(conn, "pe-7")
        pl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        net = pl["open_position_net_cash_usd"]

        # THE DEMONSTRATION'S OWN FIGURES, now separated.
        assert r["fees_on_sold_usd"] == pytest.approx(0.30, abs=1e-6)
        assert r["realised_on_sold_usd"] == pytest.approx(-2.01, abs=1e-6)
        assert r["entry_fees_allocated_to_residual_usd"] == pytest.approx(
            0.10, abs=1e-6)
        assert net == pytest.approx(-5.71, abs=1e-6)

        # ONE TERM IS NOT ENOUGH -- this is the error, stated as an assertion.
        one_term = r["realised_on_sold_usd"] - r["remaining_basis_usd"]
        assert one_term == pytest.approx(-5.61, abs=1e-6)
        assert abs(one_term - net) == pytest.approx(0.10, abs=1e-6)
        # TWO TERMS CLOSE IT EXACTLY.
        assert net == pytest.approx(
            r["realised_on_sold_usd"] - r["remaining_basis_usd"]
            - r["entry_fees_allocated_to_residual_usd"], abs=1e-6)
        assert r["net_cash_exceeds_this_by_usd"] == pytest.approx(
            3.70, abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 1b · THE DEFECT THIS WORK FOUND: A DROPPED VENUE COMMISSION
# ═════════════════════════════════════════════════════════════════════

def test_the_venues_own_commission_keys_are_read():
    """`executions_of` must read the keys the VENUE actually sends.

    THE DEFECT. It read `commission_usd`/`commissionUsd` only -- keys that
    exist in the already-parsed `order_status` shape. The venue's own
    executions name it `commissionNotionalCollected` or
    `commissionNotionalTotalCollected`, and `submit_fok` attaches parsed
    records ONLY on the post-only mirror path. So on every funded submit the
    commission was dropped, `observed_fee_usd` came back None, the schedule's
    expectation was booked PROVISIONAL, and FEE_DISAGREES could never fire on
    a funded exit. This is what made the partial-exit arithmetic read a fee
    the venue had said was zero.
    """
    got = FB.executions_of({"raw": {"response": {"executions": [
        {"id": "x1", "type": "EXECUTION_TYPE_FILL",
         "lastPx": {"value": "0.41"}, "lastShares": 9,
         "commissionNotionalTotalCollected": {"value": "0.0400"}}]}}})
    ex = got["executions"]
    assert len(ex) == 1, got
    assert ex[0]["commission_usd"] == pytest.approx(0.04)
    assert ex[0]["commission_read_from"] == (
        "commissionNotionalTotalCollected")


def test_a_stated_zero_commission_is_an_observation_not_an_absence():
    """0.00 stated and nothing stated are different facts.

    Only the first can reconcile against the schedule; the second leaves the
    fee provisional. Returning 0.0 for the absent case would turn "unknown"
    into "free", which is the direction that understates cost.
    """
    stated = FB.executions_of([
        {"id": "z", "type": "EXECUTION_TYPE_FILL",
         "lastPx": {"value": "0.41"}, "lastShares": 9,
         "commissionNotionalCollected": {"value": "0.0000"}}])["executions"][0]
    assert stated["commission_usd"] == pytest.approx(0.0)
    assert FB._observed_fee_of(stated) == pytest.approx(0.0)

    absent = FB.executions_of([
        {"id": "z", "type": "EXECUTION_TYPE_FILL",
         "lastPx": {"value": "0.41"}, "lastShares": 9}])["executions"][0]
    assert "commission_usd" not in absent
    assert FB._observed_fee_of(absent) is None


def test_a_refused_commission_value_stays_absent_rather_than_becoming_zero():
    """A bool or a non-finite reading is not an observation of zero."""
    for bad in (True, {"value": True}, {"value": "nan"}, {"value": "1e400"}):
        ex = FB.executions_of([
            {"id": "b", "type": "EXECUTION_TYPE_FILL",
             "lastPx": {"value": "0.41"}, "lastShares": 9,
             "commissionNotionalCollected": bad}])["executions"][0]
        assert "commission_usd" not in ex, bad
        assert FB._observed_fee_of(ex) is None, bad


def test_both_venue_commission_keys_are_covered():
    assert FB.VENUE_COMMISSION_KEYS == ("commissionNotionalCollected",
                                        "commissionNotionalTotalCollected")
    for k in FB.VENUE_COMMISSION_KEYS:
        ex = FB.executions_of([
            {"id": "k", "type": "EXECUTION_TYPE_FILL",
             "lastPx": {"value": "0.41"}, "lastShares": 9,
             k: {"value": "0.0700"}}])["executions"][0]
        assert ex["commission_usd"] == pytest.approx(0.07), k
        assert ex["commission_read_from"] == k


@pg
@pytest.mark.asyncio
async def test_with_nothing_sold_the_sold_result_is_zero_not_a_loss(monkeypatch):
    """A freshly filled entry has sold nothing; it has not lost anything."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await _seed(conn)
        await _entry(conn, intent_id="pe-4", qty=15, price=0.60, fee=0.0)
        r = await FB.realised_on_sold(conn, "pe-4")
        assert r["sold_qty"] == pytest.approx(0.0)
        assert r["realised_on_sold_usd"] == pytest.approx(0.0)
        assert r["residual_qty"] == pytest.approx(15.0)
        assert r["remaining_basis_usd"] == pytest.approx(9.0, abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · WHAT IT REFUSES TO CLAIM
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_residual_is_not_marked_and_says_so(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await _seed(conn)
        await _entry(conn, intent_id="pe-5", qty=15, price=0.60, fee=0.0)
        await _sell(conn, monkeypatch, parent="pe-5", qty=9,
                    price=0.41, fee=0.0)
        r = await FB.realised_on_sold(conn, "pe-5")
        assert r["unrealised_on_the_residual"] == FB.NOT_IDENTIFIED
        assert "no funded mark source" in r["why_unrealised_is_not_identified"]
        assert "rather than 0" in r["why_unrealised_is_not_identified"]
    finally:
        await _clean(conn)
        await conn.close()


def test_it_names_the_attribution_and_does_not_invent_one():
    """The convention is the one `remaining_basis` already uses."""
    import inspect
    src = inspect.getsource(FB.realised_on_sold)
    assert "AVERAGE_ENTRY_COST_PER_CONTRACT" in src
    assert "remaining_basis" in src
    assert "Not invented here" in src


def test_it_states_that_net_cash_is_not_the_number():
    """Open-position net cash is still not this figure, and says so.

    THIS TEST LOST HALF ITS CONTENT ON PURPOSE (2026-09-28), and the
    reason is worth recording where the next reader will find it.

    It used to also require `this_is_not_realised_pnl` -- a field whose
    text read "`realised()` books on POSITION CLOSURE and is what the
    drawdown and the loss stop read ... both are correct for their own
    question". The first clause was a true description of the code. The
    second was a WRONG CONCLUSION about it: a loss stop whose measurement
    omits a loss already taken is not correct for its own question, it is
    a stop that cannot trip. `realised()` now folds this result into the
    equity curve, so the field was replaced rather than kept.

    Asserting on that field would now pin the withdrawn claim in place --
    which is how a mistake becomes a requirement, and it has already
    happened once in this repository with the M1 sequencing premise. The
    replacement assertion is below and `test_the_withdrawn_claim_is_gone`
    guards the direction.
    """
    import inspect
    src = inspect.getsource(FB.realised_on_sold)
    assert "this_is_not_open_position_net_cash" in src
    assert "overstates a" in src
    # THE FIELD THAT REPLACED IT, asserting the corrected relationship.
    assert "this_is_now_inside_realised_pnl" in src


def test_the_withdrawn_claim_is_gone_from_the_module():
    """The loss stop must never again be documented as blind by design.

    A DIRECTIONAL GUARD, not a spelling check. The specific failure this
    prevents: someone reads `realised()`'s closure filter, concludes it is
    intentional, and restores the old note -- at which point the code and
    its documentation agree again and the control is quietly disarmed for
    a second time. The phrasings banned here are the exact ones that
    carried the wrong conclusion.
    """
    import inspect
    src = inspect.getsource(FB)
    assert "this_is_not_realised_pnl" not in src
    assert "Both\n            are correct for their own question" not in src
    # AND THE CORRECTED BASIS MUST BE PRESENT, so deleting the note is not
    # a way to pass this test.
    # THE COMPONENT NAMES OF THE INCREMENT LEDGER. These replaced the
    # short-lived `REALISED_ON_SOLD_WHILE_OPEN` label when the unit became
    # the per-fill increment rather than a per-position aggregate.
    assert "EXIT_FILL" in src
    assert "POSITION_TERMINAL" in src
    assert "includes_open_position_partial_results" in src
    # AND THE AGGREGATE APPROACH MUST NOT COME BACK: a terminal increment
    # that books the whole economics net would double-count every exit.
    assert "closure_does_not_double_count" in src
    assert "the_past_does_not_change" in src


@pg
@pytest.mark.asyncio
async def test_a_missing_position_refuses_rather_than_returning_zeros(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        r = await FB.realised_on_sold(conn, "no-such-intent-at-all")
        assert r["ok"] is False
        assert r["refusal"] == FB.R_NO_SUCH_INTENT
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · AND IT REACHES THE OPERATOR VIEW
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_command_centre_shows_the_partial_result_on_held_inventory(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await _seed(conn)
        await _entry(conn, intent_id="pe-6", qty=15, price=0.60, fee=0.0)
        await _sell(conn, monkeypatch, parent="pe-6", qty=9,
                    price=0.41, fee=0.0)

        cc = await FB.command_center(conn)
        held = [d for d in cc["unresolved_discrepancies"]
                if d["kind"] == "RESIDUAL_INVENTORY_STILL_HELD"
                and d["intent_id"] == "pe-6"]
        assert held, "the held position is not in the operator view"
        p = held[0]["partial_exit_result"]
        assert p["sold_qty"] == pytest.approx(9.0)
        assert p["exit_proceeds_usd"] == pytest.approx(3.69, abs=1e-6)
        assert p["allocated_basis_usd"] == pytest.approx(5.40, abs=1e-6)
        assert p["realised_on_sold_usd"] == pytest.approx(-1.71, abs=1e-6)
        assert p["remaining_basis_usd"] == pytest.approx(3.60, abs=1e-6)
        assert p["attribution"] == "AVERAGE_ENTRY_COST_PER_CONTRACT"
        assert "NOT `realised_pnl_usd`" in held[0][
            "and_what_is_already_realised"]
    finally:
        await _clean(conn)
        await conn.close()
