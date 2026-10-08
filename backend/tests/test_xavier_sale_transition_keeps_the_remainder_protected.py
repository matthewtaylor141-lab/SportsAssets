"""CAPITAL-CRITICAL: A MANAGEMENT SALE NEVER LEAVES THE INVENTORY IT DOES NOT
SELL UNPROTECTED, AND THE CONTINUATION NEVER CRASHES ON A MISSING PRICE.

An EXIT / REDUCE ranked on a complete packet first cancels the standing
protection (one live sale per group: the protection commits the whole
position), then -- the cancel terminal, the evidence fresh again -- submits
the sale (paper_exit_intents, migration 313). Before this:

  1. THE UNSOLD REMAINDER WAS LEFT BARE. A REDUCE sells floor(q / 2); an
     EXIT sells only what the book absorbs (`alternatives`: qty = the walk's
     `sold`). The review submitted the sale and placed nothing on the rest,
     so the inventory the sale does NOT commit had no protection until a
     later review (ORDER_EVENT after the IOC sale went terminal -- at least
     one more pass). The fix protects exactly the uncommitted remainder in
     the same review, and the standing order's target is the position less
     what a pending sale already commits (so the two never overlap: no
     oversell, and no cancel / replace churn while the sale is pending).

  2. A MISSING PROTECTIVE PRICE CRASHED THE RECOVERY. `_maintain_standing`
     read prot["price"] unconditionally; the EXIT-refused and no-walkable-
     sale branches of the continuation call it to restore protection, so a
     position with no cent that recovers cost + fees + buffer (production:
     the idnsl SHORT bought at 0.99) raised KeyError there -- no review row,
     the intent left open. It now records the reason instead.

Through the real review on SYNTHETIC data in a scratch test database (no
network, no real order). No threshold, price rule or order type changes.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import paper_exit_intents as XI
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import test_xavier_exit_continuation as XC
from tests import test_xavier_management_packet as XMP
from tests import test_xavier_review_probability_freshness as XRF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
QTY = XRF.QTY


async def _open_sells(conn, g):
    return await conn.fetch(
        "SELECT role, qty, filled_qty, state FROM paper_orders WHERE "
        " group_id=$1 AND direction='SELL' AND state = ANY($2::text[]) "
        " ORDER BY created_at, order_id", g, list(L.OPEN_STATES))


async def _open_qty(conn, a, g):
    return next(p["open_qty"] for p in await L.positions(conn, a["account_id"])
                if p["group_id"] == g)


async def _reduce_decided(conn, tag):
    """Held 100 at 0.40, fresh p 0.71 (source AT-8), a book that pays 0.80
    for 50 and only 0.50 below: REDUCE (50 @ 0.80 + 50 held at 0.71 = 75.5)
    beats EXIT (65.0) and HOLD (71.0). The review at AT cancels the
    protection first and persists the intent."""
    a, g, slug = await XMP._held(conn, tag)
    await H.observe(conn, slug, AT - 1, offers=[(0.82, QTY)],
                    bids=[(0.80, QTY // 2), (0.50, QTY)])
    await XRF._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0, p=0.71)
    rv, act, m = await XC._review(conn, a, g, AT)
    assert H.j(rv["selection"])["mechanical_selection"] == PX.A_REDUCE, \
        H.j(rv["selection"])
    assert act["taken"] == "CANCEL_STANDING_BEFORE_EXIT", act
    return a, g, slug, act["orders"]


@pg
async def test_a_reduce_protects_the_half_it_does_not_sell_in_the_same_review():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _reduce_decided(conn, "xtrred")
        slugs.append(slug)
        await XC._confirm_cancel(conn, orders, AT + 5)
        await H.observe(conn, slug, AT + 8, offers=[(0.82, QTY)],
                        bids=[(0.80, QTY // 2), (0.50, QTY)])
        rv, act, m = await XC._review(conn, a, g, AT + 10, PX.T_ORDER)
        assert act["taken"] == "SUBMIT_REDUCE" and act["ok"] is True, act
        xi = await XC._intent(conn, g)
        assert xi["resolution"] == XI.R_EXIT_ORDER_CREATED
        # THE REMAINDER IS PROTECTED NOW, not a pass later
        rem = act["remainder_protection"]
        assert rem["taken"] == "PLACE_STANDING" and rem["ok"] is True, rem
        assert xi["protection_order_id"] == rem["order_id"]
        sells = await _open_sells(conn, g)
        by_role = {r["role"]: float(r["qty"]) - float(r["filled_qty"])
                   for r in sells}
        assert by_role == {"REDUCE": pytest.approx(QTY / 2),
                           "STANDING_PROTECTION": pytest.approx(QTY / 2)}
        # NEVER MORE THAN HELD: the sale and the protection together are
        # exactly the position
        assert sum(by_role.values()) == pytest.approx(
            await _open_qty(conn, a, g))
        # a review while the REDUCE is still pending neither cancels the
        # remainder's protection nor places a second one (no churn)
        rv, act, m = await XC._review(conn, a, g, AT + 11, PX.T_EXPIRY)
        assert act["taken"] == "KEEP_STANDING", act
        assert {r["role"] for r in await _open_sells(conn, g)} == {
            "REDUCE", "STANDING_PROTECTION"}
        assert len([r for r in await _open_sells(conn, g)
                    if r["role"] == "STANDING_PROTECTION"]) == 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_full_exit_commits_everything_and_places_nothing_beside_it():
    """The other side of the same rule: an EXIT that commits the whole
    position leaves nothing uncommitted -- no protection beside it (that
    would be a second sale of the same inventory)."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await XC._exit_decided(conn, "xtrfull")
        slugs.append(slug)
        await XC._confirm_cancel(conn, orders, AT + 5)
        rv, act, m = await XC._review(conn, a, g, AT + 10, PX.T_ORDER)
        assert act["taken"] in ("SUBMIT_EXIT", "SUBMIT_REDUCE"), act
        sells = await _open_sells(conn, g)
        assert sum(float(r["qty"]) - float(r["filled_qty"]) for r in sells) \
            <= await _open_qty(conn, a, g) + 1e-9
        if act["taken"] == "SUBMIT_EXIT" and float(
                act["requested"]["qty"]) >= QTY - 1e-9:
            assert act["remainder_protection"]["why"] == \
                "NO_UNCOMMITTED_INVENTORY", act
            assert [r["role"] for r in sells] == ["EXIT"]
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_refused_exit_with_no_protective_price_is_recorded_not_raised(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await XC._exit_decided(conn, "xtrnop")
        slugs.append(slug)
        await XC._confirm_cancel(conn, orders, AT + 5)

        async def refused(conn, ctx, **kw):
            return {"taken": "SUBMIT_EXIT", "ok": False,
                    "refusal": "TEST_REFUSED", "order_id": None}
        monkeypatch.setattr(PX, "_submit_sale", refused)
        monkeypatch.setattr(PX, "protective_price", lambda **kw: {
            "ok": False, "refusal": "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"})
        rv, act, m = await XC._review(conn, a, g, AT + 10, PX.T_ORDER)
        xi = await XC._intent(conn, g)
        assert xi["state"] == XI.S_ABANDONED
        assert xi["resolution"] == XI.R_EXIT_REFUSED
        assert act["protection_restored"]["taken"] == "NONE"
        assert act["protection_restored"]["why"] == \
            "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"
        assert not [r for r in await _open_sells(conn, g)]
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_the_standing_target_excludes_what_a_pending_sale_commits():
    """_maintain_standing's target is the position less a pending EXIT /
    REDUCE: a protection that already covers exactly the uncommitted
    remainder is KEPT, never cancelled for a 'mismatch' with open_qty."""
    from sportsassets import bettor_xavier_standing_orders as SPO
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug, orders = await _reduce_decided(conn, "xtrtgt")
        slugs.append(slug)
        await XC._confirm_cancel(conn, orders, AT + 5)
        await H.observe(conn, slug, AT + 8, offers=[(0.82, QTY)],
                        bids=[(0.80, QTY // 2), (0.50, QTY)])
        rv, act, m = await XC._review(conn, a, g, AT + 10, PX.T_ORDER)
        assert act["remainder_protection"]["ok"] is True, act
        pos = next(p for p in await L.positions(conn, a["account_id"])
                   if p["group_id"] == g)
        standing = await conn.fetch(
            "SELECT * FROM paper_orders WHERE group_id=$1 AND role="
            "'STANDING_PROTECTION' AND state = ANY($2::text[])", g,
            list(L.OPEN_STATES))
        prot = PX.protective_price(qty=pos["open_qty"],
                                   cost_basis=pos["cost_basis_usd"],
                                   fee_fn=H.zero_fee, at=AT + 12)
        got = await PX._maintain_standing(
            conn, XRF._ctx(a, AT + 12), pos=pos,
            standing=[dict(s) for s in standing], prot=prot, md=None,
            at=AT + 12, SPO=SPO)
        assert got["taken"] == "KEEP_STANDING", got
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_resting_protection_is_kept_when_no_price_can_be_compared():
    """No cent recovers cost + fees + buffer now: the resting protection is
    KEPT (never cancelled merely because a replacement cannot be priced) and
    nothing raises."""
    from sportsassets import bettor_xavier_standing_orders as SPO
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XMP._held(conn, "xtrkeep")
        slugs.append(slug)
        pos = next(p for p in await L.positions(conn, a["account_id"])
                   if p["group_id"] == g)
        standing = await conn.fetch(
            "SELECT * FROM paper_orders WHERE group_id=$1 AND role="
            "'STANDING_PROTECTION' AND state = ANY($2::text[])", g,
            list(L.OPEN_STATES))
        assert len(standing) == 1
        got = await PX._maintain_standing(
            conn, XRF._ctx(a, AT), pos=pos,
            standing=[dict(s) for s in standing],
            prot={"ok": False,
                  "refusal": "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"},
            md=None, at=AT, SPO=SPO)
        assert got == {"taken": "KEEP_STANDING",
                       "order_id": standing[0]["order_id"],
                       "why": "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"}
        assert await conn.fetchval(
            "SELECT state FROM paper_orders WHERE order_id=$1",
            standing[0]["order_id"]) == "RESTING"
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()
