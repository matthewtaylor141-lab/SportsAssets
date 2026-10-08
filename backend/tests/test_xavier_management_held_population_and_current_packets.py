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
