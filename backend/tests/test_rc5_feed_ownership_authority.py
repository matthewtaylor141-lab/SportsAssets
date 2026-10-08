"""RC5: FEED_OWNERSHIP_NOT_HELD BY THE PINNAPI AUTHORITY'S STATE; A RESYNC
WAIT BEFORE EVERY CYCLE.

Production (2026-10-08, release 7fd4574e): FEED_OWNERSHIP_NOT_HELD was 18 / h
at the NORMALIZED stage (collector ledger rows of events with no other
Pinnacle source) with nothing saying which authority state produced it:
`pinnapi_primary.select` collapses every not-synced state into the one
name. The code and its class (SOFTWARE) are unchanged; each refusal is now
counted by the authority's state at that instant, the counts and the last
pre-cycle resync wait reach the heartbeat, and every cycle after a hold's
first waits (bounded, FIRST_SYNC_WAIT_S) for a resync exactly as the
first cycle already did.
"""
from __future__ import annotations

import inspect

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O
from sportsassets.workers import ext_pinnacle_loop as LOOP


# ═════════════════════════════════════════════════════════════════════
# FEED_OWNERSHIP_NOT_HELD BY THE AUTHORITY'S STATE; RESYNC PER CYCLE
# ═════════════════════════════════════════════════════════════════════

def test_the_code_name_is_the_feeds_own():
    assert LOOP.R_FEED_OWNERSHIP == F.R_NO_AUTHORITY


def test_the_authority_state_is_named(monkeypatch):
    monkeypatch.setitem(RT._STATE, "owner", None)
    assert LOOP.pinnapi_authority_state() == "NO_OWNER_IN_THIS_PROCESS"
    c = F.FeedCache()
    o = O.FeedOwner(c, sport_ids=[6], lease_factory=None, connect=None)
    monkeypatch.setitem(RT._STATE, "owner", o)
    o.state = "CONNECTING"
    assert LOOP.pinnapi_authority_state() == \
        "NOT_GRANTED:FEED_OWNERSHIP_NOT_HELD:CONNECTING"
    ep = c.new_connection([("live", 6)])
    o.state = "RESYNCHRONIZING"
    assert LOOP.pinnapi_authority_state() == \
        "GRANTED_AWAITING_RESYNC:RESYNCHRONIZING"
    c.apply({"type": "snapshot", "stream": "live", "sport_id": 6, "ts": 1,
             "events": []}, epoch=ep)
    assert LOOP.pinnapi_authority_state() == "SYNCED"
    c.lost(O.R_SOCKET_CLOSED)
    o.state = "BACKOFF"
    assert LOOP.pinnapi_authority_state() == \
        "NOT_GRANTED:%s:BACKOFF" % O.R_SOCKET_CLOSED
    lat = {}
    LOOP._note_authority_at_refusal(lat)
    LOOP._note_authority_at_refusal(lat)
    assert lat["pinnapi_authority_at_refusal"] == {
        "NOT_GRANTED:%s:BACKOFF" % O.R_SOCKET_CLOSED: 2}


def test_the_cycle_counts_each_ownership_refusal_by_state_on_its_record():
    src = inspect.getsource(LOOP.cycle)
    i = src.index("_np = no_pinnacle_codes(_ws_why, event)")
    assert "_note_authority_at_refusal(lat)" in src[i:i + 400]
    j = src.index('_ws_code = "WS_REFERENCE_NOT_USABLE:%s" % _ws_reason')
    assert "_note_authority_at_refusal(lat)" in src[j:j + 400]
    assert '"pinnapi_authority_at_refusal"' in src


async def test_the_resync_wait_names_its_outcome(monkeypatch):
    monkeypatch.setitem(RT._STATE, "owner", None)
    assert await LOOP.wait_for_feed_resync() == "NOT_STARTED"
    assert LOOP._FEED_RESYNC["last"] == "NOT_STARTED"

    async def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(RT, "wait_synced", boom)
    assert await LOOP.wait_for_feed_resync() == "WAIT_FAILED:RuntimeError"
    assert LOOP._FEED_RESYNC["last"] == "WAIT_FAILED:RuntimeError"
    assert LOOP._FEED_RESYNC["waited_s"] >= 0.0


def test_every_cycle_after_the_first_waits_for_a_resync():
    src = inspect.getsource(LOOP._hold_once)
    first = src.index("_feed.wait_synced()")
    loop_at = src.index("while True:", first)
    i = src.index("await wait_for_feed_resync()", loop_at)
    assert i < src.index("out = await cycle(conn)", i)
    # the first cycle is not made to wait twice
    assert "if _cycles_in_hold:" in src[loop_at:i]


def test_the_heartbeat_carries_the_states_and_the_last_wait(monkeypatch):
    # `_heartbeat` persists only the digest, so counts kept on the cycle's
    # return alone are gone once the cycle returns
    monkeypatch.setitem(LOOP._FEED_RESYNC, "last", "TIMEOUT_AFTER_60S")
    monkeypatch.setitem(LOOP._FEED_RESYNC, "waited_s", 60.0)
    st = "NOT_GRANTED:%s:BACKOFF" % O.R_SOCKET_CLOSED
    d = LOOP._freshness_digest({
        "odds_freshness": {},
        "latency": {"pinnapi_authority_at_refusal": {st: 4}}})
    assert d["pinnapi_authority_at_refusal"] == {st: 4}
    assert d["pinnapi_resync_before_cycle"] == {
        "last": "TIMEOUT_AFTER_60S", "waited_s": 60.0}
