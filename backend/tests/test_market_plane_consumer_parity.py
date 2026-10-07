"""THE MARKET-PLANE CONSUMER BRIDGE IS SHADOW PARITY ONLY (owner closeout
item 7): the worker's PMX tops of the priority members are compared with
the REST/public book the paper runtime recorded for the SAME venue slug
inside the window; equal and different tops are counted; nothing reaches a
decision and the module imports no order / funding path."""
from __future__ import annotations

import json
import time
import uuid

import asyncpg
import pytest

from sportsassets.api import command_market_plane as CMP
from sportsassets.workers import universal_market_plane as UMP

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


@pg
async def test_parity_counts_equal_and_different_tops_on_exact_identity():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        tag = uuid.uuid4().hex[:8]
        same, diff, absent = ("par-%s-%s" % (k, tag)
                              for k in ("same", "diff", "absent"))
        await H.observe(conn, same, now - 5, bids=[(0.41, 10)],
                        offers=[(0.44, 10)])
        await H.observe(conn, diff, now - 5, bids=[(0.40, 10)],
                        offers=[(0.44, 10)])
        await conn.execute(
            "INSERT INTO market_plane_events (event_key, kind, payload, at) "
            " VALUES ($1, $2, $3::jsonb, clock_timestamp())",
            "pbooks:test:%s" % tag, UMP.PRIORITY_BOOKS_KIND, json.dumps(
                {"at": now, "mode": "SHADOW_PARITY_NO_DECISION_EFFECT",
                 "books": {
                     same: {"best_bid": 0.41, "best_offer": 0.44,
                            "received_at": now - 2},
                     diff: {"best_bid": 0.42, "best_offer": 0.44,
                            "received_at": now - 2},
                     absent: {"best_bid": 0.5, "best_offer": 0.52,
                              "received_at": now - 2}}}))
        got = await CMP.parity_read(conn)
        assert got["mode"] == "SHADOW_PARITY_NO_DECISION_EFFECT"
        assert got["identity"] == "EXACT_VENUE_SLUG"
        c = got["counts"]
        assert c == {"pmx_books": 3, "rest_in_window": 2, "rest_absent": 1,
                     "top_equal": 1, "top_different": 1}
        assert got["parity_rate"] == 0.5
        (d,) = got["differences"]
        assert d["contract_id"] == diff and d["rest_bid"] == 0.40
    finally:
        await tx.rollback()
        await conn.close()


def test_the_bridge_has_no_order_or_funding_path():
    import inspect
    src = inspect.getsource(CMP)
    for bad in ("submit_order", "request_cancel", "bettor_funded",
                "live_executor", "place_order"):
        assert bad not in src, bad
