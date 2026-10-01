"""ONE PINNAPI SOCKET, AND LOSING IT MEANS LOSING AUTHORITY.

Against a real Postgres (advisory locks), with a fake provider socket:
  OVERLAP      two owners (an old and a new deploy) contend; only one opens a
               socket, the other stands by.
  SAMPLER      the bounded sampler is refused while the owner holds the lease;
               the owner waits while the sampler holds it.
  LEASE LOSS   the owner's lease backend is terminated: its reads go to
               FEED_OWNERSHIP_NOT_HELD before anything else, its socket is
               closed, the standby takes over on a NEW epoch, and nothing from
               the old epoch is ever served again.
  RESYNC       a new epoch serves nothing until every subscribed snapshot has
               arrived; reconnect drops earlier state.
  TIME         a snapshot, a re-sent unchanged price or a heartbeat never
               makes a quote fresh; only an observed change does; a future
               change time is refused; the four clocks stay distinct.
  BOUNDS       events/markets beyond the caps are evicted and counted.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_owner as O
from sportsassets import pinnapi_probe as PP

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

MKT = {"key": "s;0;m", "type": "moneyline", "period": 0, "status": "open",
       "prices": [{"designation": "home", "price": -120},
                  {"designation": "away", "price": 105}]}


def snap(stream, sport, ts, events):
    return {"type": "snapshot", "stream": stream, "sport_id": sport,
            "ts": ts, "events": events}


def live(sport, ts, rec, op="upd"):
    return {"type": "live", "sport_id": sport, "op": op, "ts": ts,
            "topic": "matchups/reg/sp/3/live/ld", "rec": rec}


# ── pure cache: time, authority, resync, bounds ─────────────────────
def test_a_snapshot_never_makes_a_price_fresh_only_a_change_does():
    c = F.FeedCache()
    ep = c.new_connection([("live", 6)])
    assert c.read(1, "s;0;m")["reason"] == F.R_NOT_SYNCED
    c.apply(snap("live", 6, 1_000_000, [{"id": 1, "markets": [MKT]}]),
            epoch=ep, received_ms=1_000_050)
    r = c.read(1, "s;0;m", evaluated_ms=1_000_100)
    assert r["reason"] == F.R_NO_CHANGE_TIME, "snapshot: age unknown"
    # heartbeat and an unchanged re-send do not stamp a change
    c.apply({"type": "ping", "ts": 1_005_000}, epoch=ep,
            received_ms=1_005_000)
    c.apply(live(6, 1_006_000, {"id": 1, "markets": [MKT]}), epoch=ep,
            received_ms=1_006_010)
    assert c.read(1, "s;0;m", evaluated_ms=1_006_100)["reason"] == \
        F.R_NO_CHANGE_TIME
    # an observed change at ts=1_010_000 -> fresh, with distinct clocks
    moved = dict(MKT, prices=[{"designation": "home", "price": -125},
                              {"designation": "away", "price": 110}])
    c.apply(live(6, 1_010_000, {"id": 1, "markets": [moved]}), epoch=ep,
            received_ms=1_010_040)
    r = c.read(1, "s;0;m", evaluated_ms=1_012_000)
    assert r["ok"] is True
    p = r["provenance"]
    assert p["source_change_ms"] == 1_010_000
    assert p["received_ms"] == 1_010_040 and p["evaluated_ms"] == 1_012_000
    assert p["quote_age_s"] == pytest.approx(2.0)
    # 31 s later with no change -> stale, never re-aged by later heartbeats
    c.apply({"type": "ping", "ts": 1_040_000}, epoch=ep,
            received_ms=1_040_000)
    assert c.read(1, "s;0;m", evaluated_ms=1_041_001)["reason"] == F.R_STALE
    # a change stamped in the future is refused
    c.apply(live(6, 1_100_000, {"id": 1, "markets": [MKT]}), epoch=ep,
            received_ms=1_050_000)
    assert c.read(1, "s;0;m", evaluated_ms=1_060_000)["reason"] == F.R_FUTURE


def test_prematch_authoritative_markets_stamp_only_changed_prices():
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 1)])
    c.apply(snap("prematch", 1, 500, [{"id": 7, "markets": [MKT]}]),
            epoch=ep, received_ms=510)
    other = dict(MKT, key="s;0;ou;2.5", type="total",
                 prices=[{"designation": "over", "price": 101,
                          "points": 2.5},
                         {"designation": "under", "price": -111,
                          "points": 2.5}])
    moved = dict(MKT, prices=[{"designation": "home", "price": -130},
                              {"designation": "away", "price": 115}])
    c.apply({"type": "prematch_markets", "sport_id": 1, "matchup_id": 7,
             "ts": 9_000, "data": [moved, other]}, epoch=ep,
            received_ms=9_020)
    assert c.read(7, "s;0;m", evaluated_ms=9_500)["ok"] is True
    assert c.read(7, "s;0;ou;2.5", evaluated_ms=9_500)["reason"] == \
        F.R_NO_CHANGE_TIME, "first sight in an authoritative snapshot"
    # a later authoritative snapshot WITHOUT the total closes it
    c.apply({"type": "prematch_markets", "sport_id": 1, "matchup_id": 7,
             "ts": 9_900, "data": [moved]}, epoch=ep, received_ms=9_910)
    assert c.read(7, "s;0;ou;2.5", evaluated_ms=9_950)["reason"] == \
        F.R_UNKNOWN_MARKET
    q = c.read(7, "s;0;m", evaluated_ms=9_950)
    assert q["provenance"]["source_change_ms"] == 9_000, "unchanged keeps"


def test_close_signals_period_closes_and_deletes_remove_prices():
    c = F.FeedCache()
    ep = c.new_connection([("live", 6)])
    c.apply(snap("live", 6, 1, [{"id": 1, "markets": [MKT]}]), epoch=ep)
    c.apply(live(6, 2, {"id": 1, "markets": [dict(MKT, status="closed")]}),
            epoch=ep)
    assert c.read(1, "s;0;m")["reason"] == F.R_UNKNOWN_MARKET
    c.apply(live(6, 3, {"id": 1, "markets": [MKT]}), epoch=ep)
    c.apply(live(6, 4, {"id": 1, "periods": [{"period": 0,
                                              "status": "settled"}]}),
            epoch=ep)
    assert c.read(1, "s;0;m")["reason"] == F.R_UNKNOWN_MARKET
    c.apply(live(6, 5, {"id": 1, "markets": [MKT]}), epoch=ep)
    c.apply(live(6, 6, {"id": 1}, op="del"), epoch=ep)
    assert 1 not in c.events and not c.quotes


def test_revocation_is_immediate_and_a_reconnect_drops_old_prices():
    c = F.FeedCache()
    ep1 = c.new_connection([("live", 6)])
    c.apply(snap("live", 6, 1, [{"id": 1, "markets": [MKT]}]), epoch=ep1)
    moved = dict(MKT, prices=[{"designation": "home", "price": -150},
                              {"designation": "away", "price": 130}])
    c.apply(live(6, 10_000, {"id": 1, "markets": [moved]}), epoch=ep1,
            received_ms=10_001)
    assert c.read(1, "s;0;m", evaluated_ms=10_500)["ok"]
    c.lost(O.R_LEASE_LOST)
    assert c.read(1, "s;0;m", evaluated_ms=10_500)["reason"] == \
        O.R_LEASE_LOST
    # frames still arriving on the revoked epoch are ignored
    assert c.apply(live(6, 10_600, {"id": 1, "markets": [MKT]}),
                   epoch=ep1) == "IGNORED_REVOKED_EPOCH"
    ep2 = c.new_connection([("live", 6)])
    assert ep2 == ep1 + 1 and not c.quotes, "old state dropped"
    assert c.read(1, "s;0;m")["reason"] == F.R_NOT_SYNCED
    c.apply(live(6, 11_000, {"id": 1, "markets": [moved]}), epoch=ep1)
    assert not c.quotes, "a late frame from the old socket is ignored"


def test_caps_evict_and_count():
    c = F.FeedCache(max_events=3, max_markets=100)
    ep = c.new_connection([("live", 6)])
    c.apply(snap("live", 6, 1, [{"id": i, "markets": [MKT]}
                                for i in range(10)]), epoch=ep)
    assert len(c.events) == 3 and len(c.quotes) == 3
    assert c.counts["events_evicted"] == 7
    assert c.census()["bounds"]["max_events"] == 3


def test_an_unvalidated_record_contributes_nothing():
    c = F.FeedCache()
    ep = c.new_connection([("live", 6)])
    c.apply(snap("live", 6, 1, [{"id": 1, "markets": "not-a-list"}]),
            epoch=ep)
    assert not c.quotes and c.counts["unparsed"] == 1


# ── ownership against a real Postgres ───────────────────────────────
class FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent, self.closed = [], False

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if self.closed:
            raise ConnectionError("closed")
        if self.frames:
            return json.dumps(self.frames.pop(0))
        await asyncio.sleep(0.05)
        return json.dumps({"type": "ping", "ts": 1})

    async def close(self):
        self.closed = True


def frames_for(sport=6):
    moved = dict(MKT, prices=[{"designation": "home", "price": -140},
                              {"designation": "away", "price": 120}])
    return [snap("live", sport, 1, [{"id": 1, "markets": [MKT]}]),
            snap("prematch", sport, 1, []),
            live(sport, 2, {"id": 1, "markets": [moved]})]


async def until(pred, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


def owner(cache, sockets, monkeypatch):
    monkeypatch.setenv("pinnapi_key", "k-test")

    async def lease_factory():
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        assert key == "k-test" and "k-test" not in url
        ws = FakeWS(frames_for())
        sockets.append(ws)
        return ws
    return O.FeedOwner(cache, sport_ids=[6], lease_factory=lease_factory,
                       connect=connect, liveness_s=0.2, standby_s=0.2)


@pg
async def test_overlapping_deploys_open_one_socket(monkeypatch):
    sa, sb = [], []
    ca, cb = F.FeedCache(), F.FeedCache()
    a, b = owner(ca, sa, monkeypatch), owner(cb, sb, monkeypatch)
    ta = asyncio.create_task(a.run())
    assert await until(lambda: a.state == "OWNER_SYNCED")
    tb = asyncio.create_task(b.run())
    assert await until(lambda: b.state == "STANDBY")
    await asyncio.sleep(0.6)
    assert len(sa) == 1 and sb == [], "the new deploy never opened a socket"
    assert cb.read(1, "s;0;m")["reason"] == F.R_NO_AUTHORITY
    # the old deploy shuts down: socket closed, lease released, B takes over
    a.stop()
    await asyncio.wait_for(ta, 10)
    assert sa[0].closed and ca.read(1, "s;0;m")["ok"] is False
    assert await until(lambda: b.state == "OWNER_SYNCED")
    assert len(sb) == 1
    b.stop()
    await asyncio.wait_for(tb, 10)


@pg
async def test_the_sampler_and_the_owner_exclude_each_other(monkeypatch):
    s, c = [], F.FeedCache()
    o = owner(c, s, monkeypatch)
    t = asyncio.create_task(o.run())
    assert await until(lambda: o.state == "OWNER_SYNCED")

    async def lease():
        return await PP._feed_lease()
    monkeypatch.setattr("sportsassets.db._dsn", lambda: H.DSN)
    opened = []
    out = await PP.ws_sample([6], seconds=1,
                             connect=lambda u, k: opened.append(u))
    assert out["reason"] == "INGESTION_OWNER_HOLDS_THE_FEED_LEASE"
    assert opened == []
    o.stop()
    await asyncio.wait_for(t, 10)
    # now the sampler holds the lease: a new owner must stand by
    conn, held = await PP._feed_lease()
    try:
        assert held
        s2, c2 = [], F.FeedCache()
        o2 = owner(c2, s2, monkeypatch)
        t2 = asyncio.create_task(o2.run())
        assert await until(lambda: o2.state == "STANDBY")
        await asyncio.sleep(0.5)
        assert s2 == [], "no socket while the sampler holds the lease"
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", PP.FEED_LOCK_KEY)
        await conn.close()
    assert await until(lambda: o2.state == "OWNER_SYNCED")
    o2.stop()
    await asyncio.wait_for(t2, 10)


@pg
async def test_losing_the_lease_connection_revokes_authority_first(
        monkeypatch):
    sa, sb = [], []
    ca, cb = F.FeedCache(), F.FeedCache()
    a, b = owner(ca, sa, monkeypatch), owner(cb, sb, monkeypatch)
    ta = asyncio.create_task(a.run())
    assert await until(lambda: a.state == "OWNER_SYNCED")
    assert ca.read(1, "s;0;m", evaluated_ms=2_000)["ok"] is True
    old_epoch = ca.authority.epoch
    tb = asyncio.create_task(b.run())
    assert await until(lambda: b.state == "STANDBY")
    # kill A's lease backend from outside (a network partition / pod loss)
    killer = await H.connect()
    try:
        await killer.fetchval(
            "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE "
            "locktype='advisory' AND granted AND "
            "((classid::bigint << 32) | objid::bigint) = $1",
            PP.FEED_LOCK_KEY)
    finally:
        await killer.close()
    assert await until(lambda: not ca.authority.granted)
    assert ca.read(1, "s;0;m", evaluated_ms=2_000)["ok"] is False
    assert await until(lambda: sa[0].closed), "old socket closed"
    assert await until(lambda: b.state == "OWNER_SYNCED")
    assert len(sb) == 1, "exactly one new socket"
    # A may contend again but must not hold the lease nor serve old prices
    assert ca.read(1, "s;0;m")["ok"] is False
    assert cb.authority.synced and cb.read(1, "s;0;m",
                                           evaluated_ms=2_000)["ok"]
    for o, t in ((a, ta), (b, tb)):
        o.stop()
        await asyncio.wait_for(t, 10)
    assert old_epoch >= 1


@pg
async def test_a_provider_refusal_stops_without_reconnect_storm(monkeypatch):
    monkeypatch.setenv("pinnapi_key", "k-test")
    socks = []

    async def connect(url, key):
        ws = FakeWS([{"type": "error", "code": "plan_lacks_ws"}])
        socks.append(ws)
        return ws

    async def lease_factory():
        return await O.Lease.open(H.DSN)
    c = F.FeedCache()
    o = O.FeedOwner(c, sport_ids=[6], lease_factory=lease_factory,
                    connect=connect, liveness_s=0.2, standby_s=0.2)
    await asyncio.wait_for(o.run(), 10)
    assert o.state == "REFUSED_BY_PROVIDER" and o.refused == "plan_lacks_ws"
    assert len(socks) == 1 and c.read(1, "s;0;m")["ok"] is False


# ── the parser on the schema OBSERVED in the 2026-10-01 bounded capture ──
OBSERVED_LIVE_UPD = {
    "type": "live", "sport_id": 1, "op": "upd", "ts": 1790896801500,
    "topic": "pinbook/sport/29/event/1637543257",
    "rec": {
        "id": 1637543257, "type": "matchup", "status": "started",
        "startTime": "2026-10-01T21:30:00Z", "isLive": True,
        "parentId": 1637360364, "version": 1790896801,
        "participants": [
            {"name": "Fuerte San Francisco", "alignment": "home", "order": 1,
             "state": {"score": 0, "redCards": 1}},
            {"name": "Luis Angel Firpo", "alignment": "away", "order": 0,
             "state": {"score": 1, "redCards": 0}}],
        "state": {"minutes": 39, "state": 3},
        "periods": [{"period": 0, "status": "open"},
                    {"period": 1, "status": "settled"}],
        "markets": [
            {"matchupId": 1637543257, "version": 1790896801, "period": 0,
             "status": "open", "key": "s;0;s;0.25", "type": "spread",
             "isAlternate": False,
             "prices": [{"designation": "home", "price": -164,
                         "points": 0.25},
                        {"designation": "away", "price": 127,
                         "points": -0.25}],
             "limits": [{"type": "maxRiskStake", "amount": 400}]},
            {"matchupId": 1637543257, "version": 1790896801, "period": 1,
             "status": "open", "key": "s;1;m", "type": "moneyline",
             "isAlternate": False,
             "prices": [{"designation": "home", "price": 300},
                        {"designation": "away", "price": -400}]}]}}


def test_observed_schema_american_prices_participants_and_settled_period():
    assert F.american_to_decimal(-164) == pytest.approx(1.609756, abs=1e-6)
    assert F.american_to_decimal(127) == pytest.approx(2.27)
    assert F.american_to_decimal(50) is None, "not an American price"
    rec = OBSERVED_LIVE_UPD["rec"]
    assert F.participants(rec) == {"home": "Fuerte San Francisco",
                                   "away": "Luis Angel Firpo"}
    c = F.FeedCache()
    ep = c.new_connection([("live", 1)])
    c.apply(snap("live", 1, 1790896790000, []), epoch=ep)
    c.apply(OBSERVED_LIVE_UPD, epoch=ep, received_ms=1790896801600)
    # period 1 is settled: its market is not kept even though listed open
    assert (1637543257, "s;1;m") not in c.quotes
    r = c.read(1637543257, "s;0;s;0.25", evaluated_ms=1790896803500)
    assert r["ok"] is True
    q = r["quote"]
    assert q.market_type == "spread" and q.line == 0.25
    assert q.decimal_prices() == {"home": pytest.approx(1.609756, abs=1e-6),
                                  "away": pytest.approx(2.27)}
    p = r["provenance"]
    assert p["source_change_ms"] == 1790896801500   # the frame stamp
    assert q.market_version == 1790896801            # provenance only
    assert p["received_ms"] == 1790896801600
    assert p["quote_age_s"] == pytest.approx(2.0)
    assert F.PARSER_VERSION.startswith("ARCADIA_RAW_V1_OBSERVED")
