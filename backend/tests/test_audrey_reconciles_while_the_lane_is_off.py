"""CAPITAL-CRITICAL: AUDREY'S RECONCILIATION KEEPS RUNNING WHILE THE SMALL-LIVE
LANE IS OFF OR STOPPED -- RECORDS ONLY, BOUNDED, NO VENUE CALL (RC6).

Production pm-acceptance 37836393458 (release 69a8a07e, 2026-10-08 20:10Z):
the red-team TRUTH_QUORUM read MISSING_SOURCE + STALE_SOURCE:
AUDREY_RECONCILIATION while execmirror.tick was HEALTHY in the API. The
owner's emergency stop (2026-10-03 02:39Z; SMALL LIVE = SHADOW) keeps the
lane STOPPED, and `Mirror.tick` ran `audrey_reconcile` only in its RUNNING
branch -- so the newest smalllive_reconciliations row stayed days old.

Proven here against Postgres with a venue factory that REFUSES to be built
(any venue access fails the test):

  * stopped (stop completed) and disabled: Audrey reconciles on her cadence,
    the paper-only group reads NOT_MIRRORED, nothing is placed or
    cancelled, and the red-team quorum's AUDREY_RECONCILIATION source is
    current (no MISSING / STALE for it) -- the venue sources stay missing,
    so TRUTH_QUORUM stays RED;
  * the lane-off pass is bounded: AUDREY_LANE_OFF_BATCH groups, never
    reconciled first, then the oldest; not due again inside her cadence.
"""
from __future__ import annotations

import time

import pytest

from sportsassets import execmirror as M
from sportsassets.redteam import controls as C

from tests import test_execmirror as TE

pg = pytest.mark.skipif(not TE.DSN, reason="needs RN1X_TEST_DSN")


def _no_venue():
    raise AssertionError("Audrey's lane-off pass must never build a venue")


async def _world(conn, monkeypatch, n_groups: int = 1):
    """`n_groups` paper-only decisions (PINNACLE_EXPLORATION_PAPER is never
    live-eligible: its execution intent is PAPER_ONLY), the mirror built
    with a venue factory that refuses."""
    acct, venue, mirror = await TE._setup(conn, monkeypatch)
    pos = [await TE._paper_order(
        conn, acct, qty=2702, strategy="PINNACLE_EXPLORATION_PAPER",
        policy_version="PINNACLE_EXPLORATION_PAPER_V3")
        for _ in range(n_groups)]
    for po in pos:
        assert await conn.fetchval(
            "SELECT actual_state FROM execution_intents WHERE group_id=$1",
            po["group_id"]) == "PAPER_ONLY"
    mirror._venue, mirror._venue_factory = None, _no_venue
    return acct, venue, mirror, pos


async def _quorum(conn):
    now = time.time()
    rows, disc = await C.quorum_rows(conn, now=now, venue_confirmed=False,
                                     market_data_green=True)
    return C.quorum(rows, now=now, audrey_open_discrepancies=disc)


@pg
@pytest.mark.parametrize("lane", ["STOPPED", "DISABLED"])
async def test_audrey_reconciles_while_the_lane_is_stopped_or_off(
        monkeypatch, lane):
    conn = await TE._conn()
    try:
        _acct, venue, mirror, (po,) = await _world(conn, monkeypatch)
        if lane == "STOPPED":
            await conn.execute("UPDATE execmirror_control SET stopped = true,"
                               " stop_done_at = now() WHERE id = 1")
        else:
            await conn.execute("UPDATE execmirror_control SET enabled = false"
                               " WHERE id = 1")
        q = await _quorum(conn)
        assert "MISSING_SOURCE:AUDREY_RECONCILIATION" in q["blockers"]
        out = await M.Mirror.tick(mirror, conn)
        assert out["state"] == lane
        assert out["audrey_reconciled"] == 1
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations "
                                  " WHERE group_id = $1", po["group_id"])
        assert rec is not None and rec["status"] == "NOT_MIRRORED", rec
        assert time.time() - rec["reconciled_at"].timestamp() < 60
        # records only: nothing placed, cancelled or flattened
        assert venue.placed == [] and venue.cancelled == []
        assert venue.cancel_all_calls == 0 and venue.closed == []
        # the quorum's Audrey source is current; the venue sources are not
        # (owner's funded retail key), so the control stays RED
        q = await _quorum(conn)
        assert not [b for b in q["blockers"] if "AUDREY" in b], q
        assert "AUDREY_RECONCILIATION" in q["evidence"]["sources_current"]
        assert "MISSING_SOURCE:VENUE_POSITIONS" in q["blockers"]
        assert q["status"] == C.RED
    finally:
        await conn.execute("UPDATE execmirror_control SET enabled = false, "
                           " stopped = false, stop_done_at = NULL WHERE id = 1")
        await conn.close()


@pg
async def test_the_lane_off_pass_is_bounded_oldest_first_and_on_cadence(
        monkeypatch):
    conn = await TE._conn()
    try:
        monkeypatch.setattr(M, "AUDREY_LANE_OFF_BATCH", 2)
        _acct, _venue, mirror, pos = await _world(conn, monkeypatch, 3)
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " stop_done_at = now() WHERE id = 1")
        t = [1_000_000.0]
        mirror._now = lambda: t[0]
        mirror._last_management = 0.0
        out = await M.Mirror.tick(mirror, conn)
        assert out["audrey_reconciled"] == 2
        first = {r["group_id"]: r["reconciled_at"] for r in await conn.fetch(
            "SELECT group_id, reconciled_at FROM smalllive_reconciliations")}
        assert len(first) == 2
        # inside her cadence: not due, nothing reconciled
        t[0] += M.MANAGEMENT_EVERY_S - 1
        out = await M.Mirror.tick(mirror, conn)
        assert "audrey_reconciled" not in out
        # due again: the never-reconciled group first, then the oldest
        t[0] += 2
        out = await M.Mirror.tick(mirror, conn)
        assert out["audrey_reconciled"] == 2
        rows = {r["group_id"]: r["reconciled_at"] for r in await conn.fetch(
            "SELECT group_id, reconciled_at FROM smalllive_reconciliations")}
        assert set(rows) == {p["group_id"] for p in pos}
        oldest = min(first, key=lambda g: (first[g], g))
        kept = (set(first) - {oldest}).pop()
        assert rows[oldest] > first[oldest] and rows[kept] == first[kept]
    finally:
        await conn.execute("UPDATE execmirror_control SET enabled = false, "
                           " stopped = false, stop_done_at = NULL WHERE id = 1")
        await conn.close()


@pg
async def test_an_open_actual_position_is_reconciled_on_every_lane_off_pass(
        monkeypatch):
    """runtime_slo's RECONCILIATION_AGE holds an OPEN actual position's
    reconciliation to 3 x her cadence: its group leads every bounded pass,
    ahead of never-reconciled and older groups."""
    conn = await TE._conn()
    try:
        monkeypatch.setattr(M, "AUDREY_LANE_OFF_BATCH", 2)
        _acct, _venue, mirror, pos = await _world(conn, monkeypatch, 3)

        async def no_reviews(conn_, **kw):
            return 0
        monkeypatch.setattr(mirror, "xavier_live_reviews", no_reviews)
        held = max(p["group_id"] for p in pos)    # last by id: never first
        await conn.execute(
            "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, "
            " us_market_slug, entry_mirror_id, opened_intent, live_held, "
            " live_bought, avg_entry_px, first_live_fill_at) VALUES "
            " ($1,'POLYMARKET',$2,'slug-held','em:x','ORDER_INTENT_BUY_LONG',"
            " 3,3,0.5,now())", "livehand:t:" + held, held)
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " stop_done_at = now() WHERE id = 1")
        t = [2_000_000.0]
        mirror._now = lambda: t[0]
        mirror._last_management = 0.0
        await M.Mirror.tick(mirror, conn)
        first = {r["group_id"]: r["reconciled_at"] for r in await conn.fetch(
            "SELECT group_id, reconciled_at FROM smalllive_reconciliations")}
        assert held in first and len(first) == 2
        t[0] += M.MANAGEMENT_EVERY_S + 1
        await M.Mirror.tick(mirror, conn)
        rows = {r["group_id"]: r["reconciled_at"] for r in await conn.fetch(
            "SELECT group_id, reconciled_at FROM smalllive_reconciliations")}
        assert set(rows) == {p["group_id"] for p in pos}
        assert rows[held] > first[held]           # again, though newest
    finally:
        await conn.execute("UPDATE execmirror_control SET enabled = false, "
                           " stopped = false, stop_done_at = NULL WHERE id = 1")
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs")
        await conn.close()
