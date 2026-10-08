"""CAPITAL-CRITICAL: XAVIER'S MANAGEMENT READBACKS JUDGE THE WHOLE HELD BOOK,
AS IT IS NOW.

Two defects on the production management readbacks (pm-acceptance run
37738089957, 2026-10-08 06:39:48Z, release 7fd4574e):

  1. THE READ MODEL SAMPLED THE NEWEST HANDOFFS, NOT THE HELD BOOK.
     GET /api/command/xavier/management (agents.xavier_management.
     management_view) read `paper_handoffs ORDER BY first_fill_at DESC
     LIMIT 100`. The readback held 100 positions, open_positions 0 (first
     fills 2026-10-05 16:24Z .. 2026-10-06 00:47Z, all closed) while the
     canonical reconciliation and the freshness read of the same minute
     held 4 true open positions -- all older than that window. The quality
     scorecard's "positions monitored" census reads the same view (limit
     500) and calls itself "every OPEN position". A held position with no
     handoff at all was never listed either.

  2. A HISTORICAL COMPLETE REVIEW KEPT A STALE PACKET GREEN.
     bettor_paper_freshness.strategy_management_integrity (the strict
     allocation rail under the ledger lock, and the capital-readiness /
     completion gate `xavier_complete`) took the newest review's recorded
     gate as the packet's state NOW. A packet is complete only while its
     time-limited elements are current: the probability until its own
     source stamp + its freshness limit (the 30 s rule, recorded on the
     review's valuation), the book within the 300 s mark SLA, and only for
     the position as it was reviewed (no fill since). A complete review
     whose evidence had expired -- the loop deferred, a review raised, the
     pass did not run -- still read COMPLETE.

Nothing here changes a threshold, a limit or an order path: both fixes are
reads. Synthetic data in a scratch test database; no network, no real order.
"""
from __future__ import annotations

import uuid

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import open_position_canon as CANON
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM

from tests import paper_harness as H
from tests import test_xavier_review_probability_freshness as XRF

#: the strict management entry rail runs its production functions here
#: (conftest seeds the legacy rail for modules that do not declare this)
MANAGEMENT_RAIL_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
NOW = 1_790_000_000.0


# ═════════════════════════════════════════════════════════════════════
# 1 · THE WHOLE HELD BOOK IS IN THE MANAGEMENT READ MODEL
# ═════════════════════════════════════════════════════════════════════

async def _entered(conn, a, *, tag, qty, at, slug=None):
    slug = slug or "xhp-%s-%s" % (tag, uuid.uuid4().hex[:8])
    g = "paper_g_%s_%s" % (a["account_id"][-10:], tag)
    o = H.order(a, key="e-%s" % tag, qty=qty, limit=0.40, slug=slug, at=at,
                group_id=g)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3, offers=[(0.40, qty)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                             fee_fn=H.zero_fee)
    return g, slug


async def _sold_out(conn, a, *, g, slug, qty, at):
    sale = H.order(a, key="s-%s" % g[-6:], direction="SELL", role="EXIT",
                   qty=qty, limit=0.30, slug=slug, at=at, group_id=g)
    got = await L.submit_order(conn, sale, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3, bids=[(0.35, 10 * qty)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                             fee_fn=H.zero_fee)


@pg
async def test_every_held_position_is_in_the_view_whatever_the_limit():
    """The production shape: the held positions are OLDER than the newest
    handoffs, which are all closed. limit=1 used to show one closed handoff
    and no held position at all."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "xhpview", now=H.T0 - 40 * 86400)
        b = await H.new_account(conn, "xhpnoho", now=H.T0 - 40 * 86400)
        # HELD, handed to Xavier 30 days before everything else
        t_old = H.T0 - 30 * 86400
        g_held, slug_held = await _entered(conn, a, tag="held", qty=60,
                                           at=t_old)
        await PX.step_handoff(conn, XRF._ctx(a, t_old + 5))
        # newer handoffs, every one of them closed since (each in its own
        # account: the strict rail refuses growth beside an unmanaged one)
        for i in range(3):
            t = H.T0 + 1000 * i
            c = await H.new_account(conn, "xhpgone%d" % i, now=t - 100)
            g, slug = await _entered(conn, c, tag="gone%d" % i, qty=10, at=t)
            await PX.step_handoff(conn, XRF._ctx(c, t + 5))
            await _sold_out(conn, c, g=g, slug=slug, qty=10, at=t + 10)
        # HELD but never handed off (its account's handoff step never ran)
        g_lost, _ = await _entered(conn, b, tag="lost", qty=25,
                                   at=t_old + 60)
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_handoffs WHERE group_id=$1",
            g_lost) == 0

        mv = await XM.management_view(conn, limit=1, now=H.T0 + 5000)
        by = {(p["position_kind"], p["group_id"]): p
              for p in mv["positions"]}
        held = by.get((XM.K_PAPER, g_held))
        assert held is not None, "a held position fell outside the limit"
        assert held["state"] == "OPEN"
        assert held["open_qty"] == pytest.approx(60.0)
        assert held["handed_off"] is True
        lost = by.get((XM.K_PAPER, g_lost))
        assert lost is not None, "a held position without a handoff is hidden"
        assert lost["state"] == "OPEN" and lost["handed_off"] is False
        assert lost["latest_review"] is None
        assert lost["why_no_review"] == XM.R_NOT_HANDED_OFF
        # the closed history is what `limit` bounds -- and only that
        closed = [p for p in mv["positions"]
                  if p["position_kind"] == XM.K_PAPER
                  and p["state"] == "CLOSED"]
        assert len(closed) <= 1
        assert not [p for p in closed
                    if p["group_id"] in (g_held, g_lost)]
        s = mv["summary"]
        assert s["population"]["open"] == XM.POPULATION_RULE
        assert s["population"]["open_complete"] is True
        assert s["population"]["closed_history_limit"] == 1
        assert s["open_not_handed_off"] >= 1
        # the COUNT is whole; only the listing of the groups is bounded
        assert len(s["open_not_handed_off_groups"]) == min(
            s["open_not_handed_off"], XM.NOT_HANDED_OFF_LISTED)
        assert s["open_not_handed_off"] == sum(
            1 for p in mv["positions"] if p["position_kind"] == XM.K_PAPER
            and p["state"] == "OPEN" and p["handed_off"] is False)
        # a held position with no review is counted as one
        assert s["open_without_review"] >= 1
    finally:
        await conn.close()


@pg
async def test_the_views_open_paper_count_is_the_canonical_open_count():
    """THE CENSUS INVARIANT: every canonical open paper position, and
    nothing else, is OPEN in the view -- whatever `limit` says."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "xhpcount")
        await _entered(conn, a, tag="c1", qty=5, at=H.T0 + 7)
        for limit in (1, 100):
            mv = await XM.management_view(conn, limit=limit, now=H.T0 + 60)
            canon = await conn.fetchval(
                "SELECT count(*) FROM (" + CANON.CANONICAL_OPEN_POSITIONS_SQL
                + ") c")
            open_paper = [p for p in mv["positions"]
                          if p["position_kind"] == XM.K_PAPER
                          and p["state"] == "OPEN"]
            assert len(open_paper) == canon, limit
            keys = {(p["group_id"], p["market"], p["holding_side"])
                    for p in open_paper}
            assert len(keys) == len(open_paper)
    finally:
        await conn.close()


@pg
async def test_the_monitored_census_counts_a_held_position_outside_the_window(
        monkeypatch):
    """quality_scorecard.positions_monitored_metric reads management_view as
    "every OPEN position": a held, never-reviewed position outside the
    newest handoffs is in its denominator and not in its numerator."""
    from sportsassets.agents import quality_scorecard as Q
    conn = await H.connect()
    try:
        b = await H.new_account(conn, "xhpmon", now=H.T0 - 40 * 86400)
        g_lost, _ = await _entered(conn, b, tag="monl", qty=9,
                                   at=H.T0 - 35 * 86400)
        monkeypatch.setattr(Q, "MONITORED_VIEW_LIMIT", 1)
        m = await Q.positions_monitored_metric(conn, H.T0 + 60)
        canon = await conn.fetchval(
            "SELECT count(*) FROM (" + CANON.CANONICAL_OPEN_POSITIONS_SQL
            + ") c")
        assert m["detail"]["by_kind"][XM.K_PAPER]["open"] == canon
        assert m["detail"]["by_kind"][XM.K_PAPER]["without_review"] >= 1
        assert m["numerator"] < m["denominator"]
        assert m["detail"]["view_truncated_for"] == []
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · A HISTORICAL COMPLETE REVIEW NEVER KEEPS A STALE PACKET GREEN
# ═════════════════════════════════════════════════════════════════════

def _complete(**over):
    d = {"complete": True, "reviewed_at": NOW - 10.0,
         "expires_at": NOW + 15.0, "book_observed_at": NOW - 20.0}
    d.update(over)
    return d


def test_a_complete_packet_is_current_only_inside_its_own_limits():
    ok = PMF.packet_currency(_complete(), now=NOW, last_fill_at=NOW - 3600)
    assert ok == {"current": True, "why": None}
    # the probability expired (its own source stamp + its limit)
    v = PMF.packet_currency(_complete(expires_at=NOW - 0.001), now=NOW,
                            last_fill_at=NOW - 3600)
    assert v == {"current": False, "why": PMF.PK_PROBABILITY_EXPIRED}
    # no recorded expiry: never assumed current
    v = PMF.packet_currency(_complete(expires_at=None), now=NOW,
                            last_fill_at=None)
    assert v == {"current": False, "why": PMF.PK_NO_RECORDED_EXPIRY}
    # the book it was walked on is past the 300 s mark SLA now
    v = PMF.packet_currency(
        _complete(book_observed_at=NOW - PMF.SLA_S - 1), now=NOW,
        last_fill_at=None)
    assert v == {"current": False, "why": PMF.PK_BOOK_PAST_SLA}
    v = PMF.packet_currency(_complete(book_observed_at=None), now=NOW,
                            last_fill_at=None)
    assert v == {"current": False, "why": PMF.PK_BOOK_PAST_SLA}
    # the position filled again after the review: it reviewed another qty
    v = PMF.packet_currency(_complete(), now=NOW, last_fill_at=NOW - 1)
    assert v == {"current": False, "why": PMF.PK_POSITION_CHANGED}
    # a recorded INCOMPLETE packet is incomplete whatever its stamps
    v = PMF.packet_currency(_complete(complete=False), now=NOW,
                            last_fill_at=None)
    assert v == {"current": False, "why": PMF.PK_RECORDED_INCOMPLETE}
    assert PMF.packet_currency(None, now=NOW, last_fill_at=None) == {
        "current": False, "why": PMF.PK_NO_REVIEW}


def test_the_limits_are_the_existing_ones():
    """No new threshold: the book limit IS the mark SLA, and the probability
    limit is the one the review recorded with its evidence."""
    assert PMF.SLA_S == L.MARK_STALE_AFTER_S == 300.0
    # the probability's limit is read off the review's own valuation block
    rec = PMF.packet_record({
        "complete": True, "reviewed_at": NOW - 10.0,
        "expires_at": NOW + 15.0, "expiry_missing": False,
        "book_observed_at": NOW - 20.0, "book_missing": False})
    assert rec["expires_at"] == NOW + 15.0
    assert PMF.packet_record(dict(rec, expiry_missing=True))[
        "expires_at"] is None


async def _held_hold(conn, tag):
    """A held, protected position whose review at AT is COMPLETE on a fresh
    probability (source AT-8, 30 s limit -> expires AT+22) and HOLDs (p 0.95
    out-values the 0.80 bid): nothing is cancelled, the protection rests."""
    a, g, slug = await XRF._held(conn, tag, entry_age_s=3600)
    await XRF._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0, p=0.95)
    rv, m, alts, sales = await XRF._review(conn, a, g)
    pk = H.j(rv["selection"])["management_packet"]
    assert pk["gate"]["complete"] is True, pk["gate"]
    assert m["evidence_state"] == PX.E_FRESH
    assert sales == 0
    exp = H.j(rv["selection"])["valuation"]["expires_at"]
    assert exp == pytest.approx(AT + 22.0)
    return a, g, slug


@pg
async def test_a_complete_review_whose_evidence_expired_is_not_complete_now():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held_hold(conn, "xhpstale")
        slugs.append(slug)
        acct = a["account_id"]
        pk = next(p["position_key"] for p in await L.positions(conn, acct)
                  if p["group_id"] == g)
        # INSIDE the probability's own life: complete, protected, no refusal
        iv = await PMF.strategy_management_integrity(conn, acct, XRF.CG,
                                                     now=AT + 5)
        assert iv["packet_incomplete"] == [], iv
        assert iv["refusal"] is None, iv
        # no review since, and the probability expired at AT+22: the old
        # COMPLETE gate is history, not the packet's state now
        iv = await PMF.strategy_management_integrity(conn, acct, XRF.CG,
                                                     now=AT + 120)
        assert pk in iv["packet_incomplete"], iv
        assert iv["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION
        assert iv["packet_not_current"] == [
            {"position_key": pk, "why": PMF.PK_PROBABILITY_EXPIRED}]
        # the protection itself is still valid: only the packet is stale
        assert iv["protection_not_valid"] == []
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_the_entry_rail_refuses_growth_on_a_stale_complete_packet():
    """The allocation rail under the ledger lock reads the same integrity:
    a new ENTRY of the strategy is refused once the complete packet's
    evidence has expired with no newer review."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held_hold(conn, "xhprail")
        slugs.append(slug)
        got = await PMF.allocation_refusal(
            conn, account_id=a["account_id"], strategy=XRF.CG, now=AT + 120)
        assert got is not None
        assert got["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_the_xavier_complete_gate_is_red_on_a_stale_complete_packet():
    """The capital-readiness / completion gate `xavier_complete` reads the
    same integrity: GREEN while current, RED once the evidence expired."""
    from sportsassets.capital_readiness import feeds as CRF
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held_hold(conn, "xhpgate")
        slugs.append(slug)
        now_ok = await CRF.gate_xavier_complete(
            conn, {"account_id": a["account_id"], "now": AT + 5})
        assert now_ok["value"] is True, now_ok
        later = await CRF.gate_xavier_complete(
            conn, {"account_id": a["account_id"], "now": AT + 120})
        assert later["value"] is False
        assert later["reason"] == "XAVIER_PACKETS_INCOMPLETE"
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_fill_after_the_complete_review_makes_it_history():
    """The position changed after its complete review (a new fill): the
    review's packet was about another quantity."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held_hold(conn, "xhpfill")
        slugs.append(slug)
        acct = a["account_id"]
        # a sale of 1 contract fills at AT+4 (after the AT review)
        sale = H.order(a, key="s1", direction="SELL", role="EXIT", qty=1,
                       limit=0.30, slug=slug, at=AT + 1, group_id=g)
        await conn.execute(
            "UPDATE paper_orders SET state='CANCEL_PENDING' WHERE group_id=$1"
            " AND role='STANDING_PROTECTION' AND state='RESTING'", g)
        oid = await conn.fetchval(
            "SELECT order_id FROM paper_orders WHERE group_id=$1 AND role="
            "'STANDING_PROTECTION' AND state='CANCEL_PENDING'", g)
        await SIM.simulate_order(conn, oid, now=AT + 1, fee_fn=H.zero_fee)
        got = await L.submit_order(conn, sale, now=AT + 1)
        assert got["ok"], got
        await H.observe(conn, slug, AT + 3.5, bids=[(0.80, 100)])
        filled = await SIM.simulate_order(conn, got["order"]["order_id"],
                                          now=AT + 4, fee_fn=H.zero_fee)
        assert filled["state"] == "FILLED", filled
        iv = await PMF.strategy_management_integrity(conn, acct, XRF.CG,
                                                     now=AT + 5)
        pk = next(p["position_key"] for p in await L.positions(conn, acct)
                  if p["group_id"] == g)
        assert pk in iv["packet_incomplete"]
        assert {"position_key": pk, "why": PMF.PK_POSITION_CHANGED} in \
            iv["packet_not_current"]
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_protection_lost_since_the_complete_review_is_not_complete_now():
    """The packet's protection element is judged LIVE: a complete review
    followed by a cancelled protection (an EXIT's cancel-first, an expiry)
    is not a complete packet now -- and the xavier_complete gate, which
    reads only the packet list, sees it."""
    from sportsassets.capital_readiness import feeds as CRF
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held_hold(conn, "xhpprot")
        slugs.append(slug)
        acct = a["account_id"]
        pk = next(p["position_key"] for p in await L.positions(conn, acct)
                  if p["group_id"] == g)
        oid = await conn.fetchval(
            "SELECT order_id FROM paper_orders WHERE group_id=$1 AND role="
            "'STANDING_PROTECTION' AND state='RESTING'", g)
        got = await SIM.request_cancel(conn, oid, now=AT + 1,
                                       reason="TEST_CANCEL_AFTER_REVIEW")
        assert got["ok"], got
        # AT+5: the probability is still inside its life (expires AT+22)
        iv = await PMF.strategy_management_integrity(conn, acct, XRF.CG,
                                                     now=AT + 5)
        assert pk in iv["packet_incomplete"], iv
        assert {"position_key": pk, "why": PMF.PK_PROTECTION_NOT_VALID_NOW} \
            in iv["packet_not_current"]
        gate = await CRF.gate_xavier_complete(
            conn, {"account_id": acct, "now": AT + 5})
        assert gate["value"] is False, gate
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()
