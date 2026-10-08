"""RC5: A STALE OR RESTARTED PLANE CERTIFIES NOTHING; CONSUMER USE BY SOURCE
IS ON THE PRIMARY BLOCK; THE CENSUS NAMES THE REAL CAUSE.

Production (2026-10-08, release 7fd4574e): the dedicated market plane was
OOM-killed at 06:10:01Z and 06:12:10Z and is cycling every ~20-60 min; its
priority census named SHARD_NOT_CONNECTED for 19 of 19 misses while the
same shard held 14,229 fresh books.

  §1  completion readback: the plane's SNAPSHOT is CURRENT only while the
      plane's own heartbeat is inside UMP_HEARTBEAT_MAX_AGE_S and the
      snapshot was computed by the RUNNING incarnation; otherwise the
      figures are withheld by name and the harness reads PMX primary as
      REST (thresholds unchanged)
  §2  consumer use by source is on the PMX primary block (evidence only;
      the held-mark majority rule is unchanged)
  §3  the consumer parity bridge names a stale plane publication
  §4  the gRPC transport's own connected flag is true for a connection's
      life and false after it (the plane's shard digest and census read it)

FEED_OWNERSHIP_NOT_HELD: test_rc5_feed_ownership_authority.py.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from sportsassets import institutional_stream as IS
from sportsassets.api import command_market_plane as CMP
from sportsassets.completion import read as CR
from sportsassets.market_plane.sharded_stream import Manager
from sportsassets.pm_bind import acceptance as PMA

from tests import paper_harness as H
from tests.test_institutional_md_grpc_transport import (  # noqa: F401
    REC, SYM, Wait, ack, book, books_for, heartbeat, in_thread, transport,
    until, venue)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


# ═════════════════════════════════════════════════════════════════════
# §1 THE PLANE'S SNAPSHOT CERTIFIES ONLY A LIVE, SAME-INCARNATION PLANE
# ═════════════════════════════════════════════════════════════════════

def _ump(age_s):
    return {"status": "ok", "detail": {"runtime": "DEDICATED_READ_ONLY"},
            "beat_at": NOW - age_s}


def _boot(started_at):
    return {"status": "ok", "detail": {"started_at": started_at},
            "beat_at": NOW - 10}


def test_a_snapshot_of_a_live_running_incarnation_stands():
    snap = {"computed_at": NOW - 30}
    assert CR.plane_snapshot_refusal(snap, _ump(5), _boot(NOW - 900),
                                     now=NOW) is None
    # an older deployment with no boot record: the heartbeat rule alone
    assert CR.plane_snapshot_refusal(snap, _ump(5), None, now=NOW) is None


def test_an_absent_or_stale_plane_heartbeat_certifies_nothing():
    snap = {"computed_at": NOW - 30}
    assert CR.plane_snapshot_refusal(snap, None, None, now=NOW) == \
        CR.R_PLANE_HEARTBEAT_ABSENT
    got = CR.plane_snapshot_refusal(
        snap, _ump(CR.UMP_HEARTBEAT_MAX_AGE_S + 1), _boot(NOW - 900),
        now=NOW)
    assert got == "MARKET_PLANE_HEARTBEAT_STALE_%ds" % int(
        CR.UMP_HEARTBEAT_MAX_AGE_S + 1)
    # the existing thresholds, unchanged
    assert (CR.UMP_HEARTBEAT_MAX_AGE_S, CR.SNAPSHOT_MAX_AGE_S) == (300.0,
                                                                   600.0)


def test_a_snapshot_from_before_the_plane_restarted_certifies_nothing():
    # the 06:10:01Z / 06:12:10Z OOM restarts: the new process beats at once,
    # but the last snapshot was computed by the process that died
    snap = {"computed_at": NOW - 120}
    got = CR.plane_snapshot_refusal(snap, _ump(2), _boot(NOW - 60), now=NOW)
    assert got == CR.R_PLANE_PREVIOUS_RUNTIME
    # the new incarnation's own first snapshot stands again
    assert CR.plane_snapshot_refusal({"computed_at": NOW - 5}, _ump(2),
                                     _boot(NOW - 60), now=NOW) is None


def test_the_harness_then_reads_pmx_primary_as_rest():
    for why in (CR.R_PLANE_PREVIOUS_RUNTIME, "MARKET_PLANE_HEARTBEAT_STALE_400s"):
        md = CR.market_data_block(None, why, {})
        assert md["snapshot"] == why
        assert md["priority_freshness"]["rate"] is None
        md["fresh"] = 14229          # what a previous incarnation reported
        e, prov = PMA.collect(red={"completion": {"market_data": md}},
                              scoreboard={}, release=None, now=NOW)
        assert e["pmx_primary_source"] == "REST"
        assert "pmx_grpc_fresh_count" not in e
        assert why in prov["pmx_primary_source"]["source"]


@pg
def test_the_readback_withholds_a_stale_planes_snapshot():
    import asyncpg

    async def go():
        c = await asyncpg.connect(H.DSN)
        tx = c.transaction()
        await tx.start()
        try:
            now = time.time()
            snap = {"computed_at": now - 20, "freshness": {
                "priority_universe": {"denominator": 135,
                                      "current_pmx_stream": 119,
                                      "current_rest_fallback": 1,
                                      "external_unavailable": 0}},
                "subscription": {"subscription_mode":
                                 "SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST",
                                 "fresh": 14229}}
            await c.execute(
                "INSERT INTO market_plane_events (event_key, kind, payload, "
                " at) VALUES ($1, 'SNAPSHOT', $2::jsonb, clock_timestamp())",
                "snapshot:rc5-test:%f" % now, json.dumps(snap))
            for svc, age, detail in (
                    ("universal_market_plane", 900.0,
                     {"runtime": "DEDICATED_READ_ONLY"}),
                    ("market_plane", 30.0, {"started_at": now - 3600})):
                await c.execute(
                    "INSERT INTO service_heartbeats (service, status, detail, "
                    " beat_at) VALUES ($1, 'ok', $2::jsonb, "
                    " now() - make_interval(secs => $3)) "
                    "ON CONFLICT (service) DO UPDATE SET status = "
                    " EXCLUDED.status, detail = EXCLUDED.detail, "
                    " beat_at = EXCLUDED.beat_at", svc, json.dumps(detail),
                    age)
            stale = await CR.read(c, now=now)
            await c.execute(
                "UPDATE service_heartbeats SET beat_at = now() "
                " WHERE service = 'universal_market_plane'")
            live = await CR.read(c, now=time.time())
            return stale, live
        finally:
            await tx.rollback()
            await c.close()
    stale, live = asyncio.run(go())
    assert stale["market_data"]["snapshot"].startswith(
        "MARKET_PLANE_HEARTBEAT_STALE_")
    assert stale["market_data"]["priority_freshness"]["rate"] is None
    assert live["market_data"]["snapshot"] == "CURRENT"
    assert live["market_data"]["priority_freshness"]["numerator"] == 120


# ═════════════════════════════════════════════════════════════════════
# §2 CONSUMER USE BY SOURCE ON THE PMX PRIMARY BLOCK
# ═════════════════════════════════════════════════════════════════════

def test_consumer_reads_by_source_ride_the_primary_block_as_evidence():
    bs = {"scope": "PROCESS_SINCE_IMPORT", "enabled": True, "rule": "r",
          "totals": {"PMX_GRPC": 30, "REST": 10}, "pmx_share": 0.75,
          "by_consumer": {
              "PAPER_MARKET_DATA_OWNER": {
                  "PMX_GRPC": 20, "REST": 4,
                  "fallback_reasons": {"PMX_SAME_BOOK_NOT_PROVEN_FOR_THIS_"
                                       "SYMBOL": 3, "X": 1}},
              "COLLECTOR_VENUE_QUOTE": {
                  "PMX_GRPC": 10, "REST": 6,
                  "fallback_reasons": {"PMX_SAME_BOOK_NOT_PROVEN_FOR_THIS_"
                                       "SYMBOL": 6}}}}
    got = CR.consumer_reads_block(bs)
    assert got["status"] == "MEASURED"
    assert got["totals"] == {"PMX_GRPC": 30, "REST": 10}
    assert got["pmx_share"] == 0.75
    assert got["by_consumer"]["COLLECTOR_VENUE_QUOTE"] == {"PMX_GRPC": 10,
                                                           "REST": 6}
    assert got["fallback_reasons_top"] == {
        "PMX_SAME_BOOK_NOT_PROVEN_FOR_THIS_SYMBOL": 9, "X": 1}
    assert CR.consumer_reads_block(None)["status"] == "UNREAD"
    run = {"run_id": 1, "finished_at": NOW - 10,
           "market_data": {"book_sources": bs, "streams": {"institutional": {
               "state": "CONNECTED", "current_books": 3}}}}
    blk = CR.pmx_primary_block(run, {"fresh_marks_by_source": {
        "INSTITUTIONAL_STREAM": 0, "REST": 4}}, markable=4, now=NOW)
    assert blk["consumer_reads"]["totals"] == {"PMX_GRPC": 30, "REST": 10}
    # the held-mark majority rule is unchanged: consumer reads are evidence
    assert blk["source"] == "REST"
    assert "NO_HELD_MARK_FROM_THE_STREAM" in blk["why"]


# ═════════════════════════════════════════════════════════════════════
# §3 THE CONSUMER PARITY BRIDGE NAMES A STALE PLANE PUBLICATION
# ═════════════════════════════════════════════════════════════════════

def test_the_parity_staleness_bound_is_the_readbacks_snapshot_bound():
    assert CMP.PARITY_STALE_AFTER_S == CR.SNAPSHOT_MAX_AGE_S


@pg
async def test_a_publication_older_than_the_bound_is_stale(monkeypatch):
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        # the newest publication (market_plane_events is append-only)
        await conn.execute(
            "INSERT INTO market_plane_events (event_key, kind, payload, at) "
            " VALUES ($1, 'PRIORITY_PMX_BOOKS', $2::jsonb, "
            " clock_timestamp() + interval '1 minute')",
            "pbooks:rc5-test", json.dumps({"books": {}}))
        got = await CMP.parity_read(conn)
        assert got["status"] == "OK" and "published_age_s" in got

        class Later:
            @staticmethod
            def time():
                return time.time() + 60 + CMP.PARITY_STALE_AFTER_S + 30
        monkeypatch.setattr(CMP, "time", Later)
        got = await CMP.parity_read(conn)
        assert got["status"] == "STALE_PLANE_PUBLICATION"
        assert got["published_age_s"] > CMP.PARITY_STALE_AFTER_S
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 THE TRANSPORT'S CONNECTED FLAG (the plane's census reads it)
# ═════════════════════════════════════════════════════════════════════

def test_the_transport_is_connected_for_its_connections_life(venue):  # noqa: F811
    b = books_for()
    gate = Wait()
    venue.scripts = [[ack(), heartbeat(), book(), gate]]
    t = transport(b, venue)
    assert t._connected is False
    th, out = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"])
    assert t._connected is True
    gate.set()
    th.join(10)
    assert out["r"] == "ended" and t._connected is False


def test_the_plane_shard_digest_reports_a_live_shard_connected(venue):  # noqa: F811
    gate = Wait()
    venue.scripts = [[ack(), heartbeat(), book(), gate]]

    def factory(books, token_fn, **kw):
        return IS.GrpcBidiTransport(
            books, token_fn, target=venue.target,
            channel_factory=lambda target: __import__(
                "grpc").insecure_channel(target),
            sleep=lambda s: time.sleep(min(s, 0.05)), **kw)
    mgr = Manager(token_fn=lambda: "tok-NOT-REAL-good", max_per_stream=10,
                  max_streams=1, transport_factory=factory)
    try:
        mgr.sync({SYM: 0}, {SYM: REC})
        until(lambda: mgr.current(SYM)["ok"])
        (d,) = mgr.shard_digest()
        # the census's `connected.get(shard) is False` -> SHARD_NOT_CONNECTED
        # now fires only for a shard that is really not connected
        assert d["connected"] is True
    finally:
        gate.set()
        mgr.stop()
