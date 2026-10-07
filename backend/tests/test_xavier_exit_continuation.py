"""CAPITAL-CRITICAL: AN EXIT THAT CANCELLED ITS OWN PROTECTION IS CONTINUED
FROM A PERSISTED INTENT -- EXECUTED, OR EXPLICITLY ABANDONED WITH THE
PROTECTION RESTORED. NEVER SILENTLY TURNED INTO A HOLD, NEVER LEFT
UNPROTECTED PAST ITS BOUND, NEVER PRICED ON THE DECIDING REVIEW'S EXPIRED
PROBABILITY.

Production 2026-10-07 (05:11:31, 05:59:42, 05:59:43): three complete-packet
EXIT reviews cancelled protection; a FRESHNESS_EXPIRY review ~25 s later
(cancel still pending) became "the latest review", and the review that saw
the cancel terminal (stale probability) re-protected with no record. The
replay test below reproduces that sequence and asserts the new outcome.

Through the real review (`paper_xavier.review_group`) on SYNTHETIC data in a
scratch test database (no network, no real order)."""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_exit_intents as XI
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import test_xavier_management_packet as XMP
from tests import test_xavier_review_probability_freshness as XRF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
QTY = XRF.QTY


async def _review(conn, a, g, at, trigger=PX.T_BACKSTOP):
    await PX.review_group(conn, XRF._ctx(a, at), g, trigger=trigger)
    rv = await conn.fetchrow(
        "SELECT * FROM paper_xavier_reviews WHERE group_id=$1 "
        " ORDER BY reviewed_at DESC LIMIT 1", g)
    return rv, H.j(rv["action"]), H.j(rv["measure"])


async def _intent(conn, g):
    r = await conn.fetchrow("SELECT * FROM paper_exit_intents WHERE "
                            " group_id=$1 ORDER BY decided_at DESC LIMIT 1", g)
    return None if r is None else dict(r, transitions=H.j(r["transitions"]))


async def _orders(conn, g, roles):
    return await conn.fetch(
        "SELECT * FROM paper_orders WHERE group_id=$1 AND role = "
        " ANY($2::text[]) ORDER BY created_at, order_id", g, list(roles))


async def _live_protection(conn, g):
    return [o for o in await _orders(conn, g, ["STANDING_PROTECTION"])
            if o["state"] in L.OPEN_STATES and o["state"] != "CANCEL_PENDING"]


async def _exit_decided(conn, tag):
    """A held position, a fresh probability (source AT-8, p 0.71) and a 0.80
    bid: the review at AT selects EXIT and cancels the protection."""
    a, g, slug = await XMP._held(conn, tag)
    await XRF._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0, p=0.71)
    rv, act, m = await _review(conn, a, g, AT)
    assert act["taken"] == "CANCEL_STANDING_BEFORE_EXIT", act
    xi = await _intent(conn, g)
    assert xi["state"] == XI.S_CANCEL_REQUESTED
    assert xi["decided_review_id"] == rv["review_id"]
    assert xi["selection"] in ("EXIT", "REDUCE")
    assert list(xi["cancel_orders"]) == act["orders"]
    assert act["exit_intent_id"] == xi["intent_id"]
    return a, g, slug, act["orders"]


async def _confirm_cancel(conn, orders, at):
    for oid in orders:
        got = await SIM.simulate_order(conn, oid, now=at, fee_fn=H.zero_fee)
        assert got["state"] == "CANCELED", got


@pg
async def test_cancel_inside_30s_exit_is_created_on_still_fresh_evidence():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _exit_decided(conn, "xcin")
        slugs.append(slug)
        await _confirm_cancel(conn, orders, AT + 5)
        # AT+10: the probability's own source stamp (AT-8) is 18 s old --
        # fresh by the 30 s rule at THIS review; the walk is re-done now
        rv, act, m = await _review(conn, a, g, AT + 10, PX.T_ORDER)
        assert m["evidence_state"] == PX.E_FRESH
        assert m["exit_continuation"]["intent_id"]
        assert act["taken"] in ("SUBMIT_EXIT", "SUBMIT_REDUCE"), act
        assert act["ok"] is True
        xi = await _intent(conn, g)
        assert xi["state"] == XI.S_EXIT_SUBMITTED
        assert xi["resolution"] == XI.R_EXIT_ORDER_CREATED
        assert xi["exit_order_id"] == act["order_id"]
        assert [t["to"] for t in xi["transitions"]] == [
            XI.S_CANCEL_REQUESTED, XI.S_CANCEL_TERMINAL, XI.S_EXIT_SUBMITTED]
        assert len(await _orders(conn, g, ["EXIT", "REDUCE"])) == 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_cancel_after_the_probability_expires_waits_then_abandons_and_restores_protection():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _exit_decided(conn, "xcexp")
        slugs.append(slug)
        await _confirm_cancel(conn, orders, AT + 40)
        # AT+45: the AT-8 probability is 53 s old -- the old decision is
        # NOT current; the intent waits (bounded) for its own fresh reading
        rv, act, m = await _review(conn, a, g, AT + 45, PX.T_ORDER)
        assert m["evidence_state"] != PX.E_FRESH
        assert act["taken"] == "EXIT_REVALIDATION_PENDING", act
        xi = await _intent(conn, g)
        assert xi["state"] == XI.S_CANCEL_TERMINAL
        assert L._epoch(xi["cancel_terminal_at"]) == pytest.approx(AT + 40)
        assert L._epoch(xi["revalidate_by"]) == pytest.approx(AT + 70)
        assert not await _orders(conn, g, ["EXIT", "REDUCE"])
        # the deadline is a review trigger
        assert PX._trigger(
            group=g, new_handoffs=[], last={"reviewed_at": AT + 45,
                                            "measure": m},
            last_fill_at=None, book_at=None, best_exit=None, at=AT + 70.5,
            backstop_s=1e9, exit_intent_due_at=AT + 70) == PX.T_EXIT_INTENT
        # AT+71: still no fresh probability -> explicitly abandoned and the
        # protection restored in the same review
        rv, act, m = await _review(conn, a, g, AT + 71, PX.T_EXIT_INTENT)
        assert act["taken"] == "PLACE_STANDING", act
        xi = await _intent(conn, g)
        assert xi["state"] == XI.S_ABANDONED
        assert xi["resolution"] == XI.R_NO_FRESH
        prot = await _live_protection(conn, g)
        assert len(prot) == 1 and xi["protection_order_id"] == \
            prot[0]["order_id"]
        assert float(prot[0]["qty"]) == pytest.approx(QTY)
        assert not await _orders(conn, g, ["EXIT", "REDUCE"])
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_refreshed_probability_that_still_favors_exit_creates_the_exit():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _exit_decided(conn, "xcref")
        slugs.append(slug)
        await _confirm_cancel(conn, orders, AT + 40)
        await XRF._reading(conn, slug, decided_at=AT + 50, pin_age_s=5.0,
                           p=0.70)
        rv, act, m = await _review(conn, a, g, AT + 52, PX.T_VALUATION)
        assert m["evidence_state"] == PX.E_FRESH
        assert m["probability_source_at"] == pytest.approx(AT + 45)
        assert m["exit_continuation"]["probability_source_at"] == \
            pytest.approx(AT + 45)
        assert act["taken"] in ("SUBMIT_EXIT", "SUBMIT_REDUCE"), act
        xi = await _intent(conn, g)
        assert xi["resolution"] == XI.R_EXIT_ORDER_CREATED
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_refreshed_probability_that_favors_hold_abandons_and_restores_protection():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _exit_decided(conn, "xchold")
        slugs.append(slug)
        await _confirm_cancel(conn, orders, AT + 40)
        await XRF._reading(conn, slug, decided_at=AT + 50, pin_age_s=5.0,
                           p=0.97)
        rv, act, m = await _review(conn, a, g, AT + 52, PX.T_VALUATION)
        assert m["evidence_state"] == PX.E_FRESH
        assert H.j(rv["selection"])["mechanical_selection"] == PX.A_HOLD
        assert act["taken"] == "PLACE_STANDING", act
        xi = await _intent(conn, g)
        assert xi["state"] == XI.S_ABANDONED
        assert xi["resolution"] == XI.R_NOT_EXIT
        assert len(await _live_protection(conn, g)) == 1
        assert not await _orders(conn, g, ["EXIT", "REDUCE"])
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_no_exit_depth_abandons_at_the_bound_and_restores_protection():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _exit_decided(conn, "xcdep")
        slugs.append(slug)
        await _confirm_cancel(conn, orders, AT + 40)
        await H.observe(conn, slug, AT + 48, offers=[(0.82, QTY)], bids=[])
        await XRF._reading(conn, slug, decided_at=AT + 50, pin_age_s=5.0,
                           p=0.70)
        rv, act, m = await _review(conn, a, g, AT + 52, PX.T_VALUATION)
        assert m["exit_walk"]["fresh"] is False
        assert act["taken"] == "EXIT_REVALIDATION_PENDING", act
        rv, act, m = await _review(conn, a, g, AT + 71, PX.T_EXIT_INTENT)
        xi = await _intent(conn, g)
        assert xi["state"] == XI.S_ABANDONED
        assert xi["resolution"] == XI.R_NO_DEPTH
        assert len(await _live_protection(conn, g)) == 1
        assert not await _orders(conn, g, ["EXIT", "REDUCE"])
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_cancel_never_confirmed_is_abandoned_without_a_second_live_sale():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _exit_decided(conn, "xcfail")
        slugs.append(slug)
        at = AT + XI.CANCEL_CONFIRM_DEADLINE_S + 1
        rv, act, m = await _review(conn, a, g, at, PX.T_EXIT_INTENT)
        xi = await _intent(conn, g)
        assert xi["state"] == XI.S_ABANDONED
        assert xi["resolution"] == XI.R_CANCEL_UNCONFIRMED
        # the order pending cancel is still potentially live: no second sale
        st = await _orders(conn, g, ["STANDING_PROTECTION"])
        assert [o["state"] for o in st if o["state"] in L.OPEN_STATES] == [
            "CANCEL_PENDING"]
        assert act["taken"] == "WAIT_FOR_TERMINAL", act
        # once it is terminal the ordinary path restores the protection
        await _confirm_cancel(conn, orders, at + 5)
        rv, act, m = await _review(conn, a, g, at + 10, PX.T_ORDER)
        assert act["taken"] == "PLACE_STANDING", act
        assert len(await _live_protection(conn, g)) == 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_production_replay_expiry_review_in_between_no_longer_drops_the_exit():
    """05:11:31 EXIT -> 05:11:56 FRESHNESS_EXPIRY (cancel pending) -> cancel
    confirmed ~100 s later -> ORDER_EVENT review on a stale probability.
    Before: PLACE_STANDING with no record (EXIT silently became HOLD).
    Now: the intent survives the in-between review, waits its bounded
    window for a fresh probability, then is ABANDONED with the protection
    restored -- every transition recorded."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _exit_decided(conn, "xcrep")
        slugs.append(slug)
        rv, act, m = await _review(conn, a, g, AT + 25, PX.T_EXPIRY)
        assert act["taken"] == "WAIT_FOR_TERMINAL", act
        assert act["exit_intent_state"] == XI.S_CANCEL_REQUESTED
        await _confirm_cancel(conn, orders, AT + 104)
        rv, act, m = await _review(conn, a, g, AT + 138, PX.T_ORDER)
        # cancel terminal at AT+104, revalidate_by AT+134 already passed and
        # this review has no fresh probability: abandoned + restored at once
        assert act["taken"] == "PLACE_STANDING", act
        xi = await _intent(conn, g)
        assert [t["to"] for t in xi["transitions"]] == [
            XI.S_CANCEL_REQUESTED, XI.S_CANCEL_TERMINAL, XI.S_ABANDONED]
        assert xi["resolution"] == XI.R_NO_FRESH
        assert act["exit_intent_resolution"] == XI.R_NO_FRESH
        assert len(await _live_protection(conn, g)) == 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_an_intent_whose_position_closed_is_resolved():
    conn = await H.connect()
    try:
        n = await XI.close_orphans(conn, "paper_test_none_x", [], at=AT)
        assert n == 0
    finally:
        await conn.close()


def test_this_proof_is_capital_critical():
    from pathlib import Path
    listed = (Path(__file__).resolve().parents[1] / "tools" /
              "capital_critical_tests.txt").read_text().splitlines()
    assert "tests/test_xavier_exit_continuation.py" in listed
