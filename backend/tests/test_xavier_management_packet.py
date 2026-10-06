"""CAPITAL-CRITICAL: XAVIER CANNOT MANAGE A PAPER POSITION WITHOUT A COMPLETE
MANAGEMENT PACKET, AND EVERY REFUSAL IS RECORDED.

Through the real review (`paper_xavier.review_group`) on SYNTHETIC data in a
scratch test database (no network, no real order):

  * on the ENTRY-TIME probability (no fresh reading for the contract) the
    review emits no HOLD / EXIT / REDUCE: it records the refusal
    XAVIER_MANAGEMENT_PACKET_INCOMPLETE with NO_FRESH_PROBABILITY, on the
    review row and in paper_management_refusals, and sells nothing although
    the bid out-values holding at the stale probability;
  * on a FRESH probability but a book older than the 300 s mark SLA, the
    review still refuses (NO_CURRENT_EXECUTABLE_BOOK): a fresh probability
    alone never prices a sale on an old book;
  * with every element present the same position is managed exactly as
    before (the sale the fresh evidence supports is placed) -- the gate only
    removes management on missing evidence;
  * the cost-recovery protection is maintained under a refusal.
"""
from __future__ import annotations

import uuid

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import xavier_freshness as XF
from sportsassets import xavier_packet as XPK
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_xavier_review_probability_freshness as XRF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
QTY = XRF.QTY


async def _held(conn, tag, *, entry_age_s=3600, book_age_s=2.0):
    """A held completed-game paper position handed to Xavier (as
    test_xavier_review_probability_freshness._held), its latest book
    `book_age_s` old at the review instant AT, bidding 0.80."""
    a = await H.new_account(conn, tag, now=AT - 5000)
    slug = "%sxp-%s" % (PL.SYN, uuid.uuid4().hex[:10])
    g = "paper_g_%s_xp" % a["account_id"][-10:]
    vid = await XRF._reading(conn, slug, decided_at=AT - entry_age_s,
                             pin_age_s=5.0, p=0.62)
    did = await XRF._decision(conn, a, slug=slug, vid=vid, p=0.62,
                              at=AT - entry_age_s)
    first = min(AT - 60, AT - book_age_s - 5)
    o = H.order(a, key="e", qty=QTY, limit=0.40, slug=slug, at=first,
                group_id=g)
    o.update(decision_id=did, strategy=XRF.CG)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=first)
    assert got["ok"], got
    await H.observe(conn, slug, first + 3, offers=[(0.40, QTY)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=first + 4,
                             fee_fn=H.zero_fee)
    await PX.step_handoff(conn, XRF._ctx(a, first + 5))
    await XRF._protect(conn, a, g, at=first + 6)
    await H.observe(conn, slug, AT - book_age_s, offers=[(0.82, QTY)],
                    bids=[(0.80, QTY)])
    return a, g, slug


async def _refusals(conn, g):
    return await conn.fetch(
        "SELECT * FROM paper_management_refusals WHERE group_id=$1 "
        "   AND kind=$2 ORDER BY refusal_id", g, PMF.K_PACKET)


@pg
async def test_xavier_cannot_manage_from_an_entry_time_probability():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held(conn, "xpentry")
        slugs.append(slug)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_STALE
        assert m["source"] == "ENTRY_TIME_MEASURE"
        # NO management action: no HOLD, no sale -- a recorded refusal
        assert rv["recommendation"] not in XPK.MANAGEMENT_ACTIONS
        assert rv["recommendation"] == XF.REC_WAITING
        assert rv["refusal"] == XPK.R_XAVIER_PACKET_INCOMPLETE
        assert sales == 0
        mp = m["management_packet"]
        assert mp["complete"] is False
        assert XPK.P_PROBABILITY in mp["missing"]
        # the book, qty, identity and protection were all present: the
        # probability alone refuses
        assert mp["missing"] == [XPK.P_PROBABILITY]
        sel = H.j(rv["selection"])
        assert sel["management_packet"]["probability"]["present"] is False
        assert sel["management_packet"]["probability"]["source"] in (
            "ENTRY_TIME_MEASURE",)
        assert XPK.R_XAVIER_PACKET_INCOMPLETE in H.j(rv["exceptional"])
        rec = await _refusals(conn, g)
        assert len(rec) == 1
        assert rec[0]["refusal"] == XPK.R_XAVIER_PACKET_INCOMPLETE
        assert H.j(rec[0]["missing"]) == [XPK.P_PROBABILITY]
        assert rec[0]["review_id"] == rv["review_id"]
        # protection still maintained
        assert H.j(rv["action"])["taken"] in ("PLACE_STANDING",
                                              "KEEP_STANDING")
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_fresh_probability_on_an_old_book_is_still_refused():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held(conn, "xpbook", book_age_s=400.0)
        slugs.append(slug)
        await XRF._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                           p=0.71)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_FRESH
        mp = m["management_packet"]
        assert mp["complete"] is False
        assert mp["mark_class"] == PMF.STALE
        assert XPK.P_BOOK in mp["missing"] and XPK.P_DEPTH in mp["missing"]
        assert XPK.P_PROBABILITY not in mp["missing"]
        # the 0.80 bid would out-value holding at 0.71: no sale on a book
        # 400 s old, no HOLD recorded -- MANAGEMENT_UNAVAILABLE_STALE_INPUT
        assert sales == 0
        assert rv["recommendation"] == XF.REC_UNAVAILABLE
        assert rv["refusal"] == XPK.R_XAVIER_PACKET_INCOMPLETE
        blocked = {x["action"] for x in alts["not_rankable"]
                   if x.get("blocker") == PX.B_PACKET_INCOMPLETE}
        assert "EXIT" in blocked
        rec = await _refusals(conn, g)
        assert len(rec) == 1
        assert set(H.j(rec[0]["missing"])) == {XPK.P_BOOK, XPK.P_DEPTH}
        # the read-time state is not CURRENT either
        v = XF.validity(recommendation=rv["recommendation"],
                        evidence_state=m["evidence_state"],
                        valuation=H.j(rv["selection"])["valuation"],
                        now=AT, assessed_at=AT)
        assert v["is_current"] is False
        assert v["current_recommendation"] is None
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_complete_packet_is_managed_as_before():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held(conn, "xpok")
        slugs.append(slug)
        await XRF._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                           p=0.71)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_FRESH
        mp = m["management_packet"]
        assert mp["complete"] is True and mp["missing"] == []
        assert mp["mark_class"] in PMF.FRESHLY_MANAGEABLE
        sel = H.j(rv["selection"])
        pk = sel["management_packet"]
        assert pk["residual"]["present"] and pk["settlement"]["present"]
        assert pk["settlement"]["fingerprint"].startswith("settle:")
        assert pk["book"]["bid"] == pytest.approx(0.80)
        assert pk["exit_depth"]["at_mark"] == pytest.approx(QTY)
        # the 0.80 bid out-values holding at 0.71: the sale is decided; the
        # valid standing protection commits the inventory, so the exit first
        # cancels it (terminal confirmation before the sale -- never two
        # potentially live sell orders)
        assert rv["recommendation"] in ("EXIT", "REDUCE")
        assert rv["refusal"] != XPK.R_XAVIER_PACKET_INCOMPLETE
        assert H.j(rv["action"])["taken"] == "CANCEL_STANDING_BEFORE_EXIT"
        assert sales == 0
        assert pk["protection"]["present"] is True
        assert pk["probability"]["valuation_id"] is not None
        assert not await _refusals(conn, g)
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()
