"""THE FEED ONLY LIVES BESIDE THE DECIDER, ONLY WHILE ARMED, AND NEVER FIGHTS.

Real Postgres (advisory locks, ingestion_state), fake provider socket:
  ARMING     absent / false control row -> no lease, no socket; arming opens
             one; disarming revokes authority and closes the socket.
  DECIDER    the owner checks that the ext writer's backend still holds the
             writer lock; when that backend dies the feed revokes, closes and
             never re-contends (WRITER_LOCK_LOST).
  EVICTION   repeated closes we did not request after a healthy stream stop
             the owner (EVICTION_LOOP_SUSPECTED) instead of fighting another
             holder of the account key.
  HEARTBEAT  bounded JSON; C1 states it changes no decision.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import pinnapi_owner as O

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import FakeWS, frames_for, until

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
WRITER_KEY = 7723901544120034


class Pool:
    """The minimum of an asyncpg pool the runtime uses."""

    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return Ctx()


async def _set(conn, key, value):
    if value is None:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", key)
    else:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            key, json.dumps(value))


def _owner(cache, sockets, pool, monkeypatch, writer_pid=None, ws=None):
    monkeypatch.setenv("pinnapi_key", "k-test")

    async def lease_factory():
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        w = ws() if ws else FakeWS(frames_for())
        sockets.append(w)
        return w
    return O.FeedOwner(cache, sport_ids=[6], lease_factory=lease_factory,
                       connect=connect, liveness_s=0.2, standby_s=0.2,
                       writer_pid=writer_pid, writer_key=WRITER_KEY,
                       armed=lambda: FR.armed(pool))


@pg
async def test_absent_or_false_control_row_opens_nothing_and_disarm_closes(
        monkeypatch):
    conn = await H.connect()
    pool = Pool(conn)
    try:
        await _set(conn, FR.CONTROL_KEY, None)
        socks, cache = [], F.FeedCache()
        o = _owner(cache, socks, pool, monkeypatch)
        t = asyncio.create_task(o.run())
        assert await until(lambda: o.state == "DISARMED")
        await _set(conn, FR.CONTROL_KEY, "true")      # a string is not true
        await asyncio.sleep(0.6)
        assert socks == [] and o.state == "DISARMED"
        await _set(conn, FR.CONTROL_KEY, True)
        assert await until(lambda: o.state == "OWNER_SYNCED")
        assert cache.read(1, "s;0;m", evaluated_ms=2_000)["ok"] is True
        await _set(conn, FR.CONTROL_KEY, False)
        assert await until(lambda: not cache.authority.granted)
        assert cache.read(1, "s;0;m")["ok"] is False
        assert await until(lambda: socks[0].closed)
        assert await until(lambda: o.state == "DISARMED")
        assert len(socks) == 1
        o.stop()
        await asyncio.wait_for(t, 10)
    finally:
        await _set(conn, FR.CONTROL_KEY, None)
        await conn.close()


@pg
async def test_losing_the_deciders_writer_lock_stops_the_feed_for_good(
        monkeypatch):
    conn = await H.connect()
    writer = await H.connect()
    pool = Pool(conn)
    try:
        await _set(conn, FR.CONTROL_KEY, True)
        assert await writer.fetchval("SELECT pg_try_advisory_lock($1)",
                                     WRITER_KEY)
        wpid = await writer.fetchval("SELECT pg_backend_pid()")
        socks, cache = [], F.FeedCache()
        o = _owner(cache, socks, pool, monkeypatch, writer_pid=wpid)
        t = asyncio.create_task(o.run())
        assert await until(lambda: o.state == "OWNER_SYNCED")
        await conn.fetchval("SELECT pg_terminate_backend($1)", wpid)
        assert await until(lambda: not cache.authority.granted)
        await asyncio.wait_for(t, 10)
        assert o.state == "WRITER_LOCK_LOST"
        assert socks[0].closed and len(socks) == 1, "never re-contends"
        assert cache.read(1, "s;0;m")["ok"] is False
        # and the lease it held is free again
        assert await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                   O.FEED_LOCK_KEY)
        await conn.execute("SELECT pg_advisory_unlock($1)", O.FEED_LOCK_KEY)
    finally:
        await _set(conn, FR.CONTROL_KEY, None)
        await conn.close()
        try:
            await writer.close()
        except Exception:                                       # noqa: BLE001
            pass


class EvictedWS(FakeWS):
    """Delivers its frames, then the server closes on us."""

    async def recv(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        raise ConnectionError("server closed the socket")


@pg
async def test_an_eviction_loop_stops_instead_of_fighting(monkeypatch):
    conn = await H.connect()
    pool = Pool(conn)
    try:
        await _set(conn, FR.CONTROL_KEY, True)
        monkeypatch.setattr(O, "BACKOFF", (0.05,))
        socks, cache = [], F.FeedCache()
        o = _owner(cache, socks, pool, monkeypatch,
                   ws=lambda: EvictedWS(frames_for()))
        await asyncio.wait_for(o.run(), 15)
        assert o.state == "EVICTION_LOOP_SUSPECTED"
        assert len(socks) == O.EVICTIONS_MAX, "stopped at the threshold"
        assert cache.read(1, "s;0;m")["ok"] is False
    finally:
        await _set(conn, FR.CONTROL_KEY, None)
        await conn.close()


def test_the_heartbeat_is_bounded_and_says_c1_changes_no_decision():
    big = {"cache": {"markets_by_sport_type_phase":
                     {str(i): i for i in range(20000)}}, "state": "X"}
    s = FR._capped(big)
    assert len(s) <= FR.HEARTBEAT_MAX_BYTES and "TRUNCATED" in s
    assert FR.digest()["state"] == "NOT_STARTED"


def test_env_is_a_kill_switch_not_the_arm(monkeypatch):
    """Arming must not need a deploy (a Render env change redeploys): the
    module starts unless PINNAPI_FEED is explicitly off; the control row is
    the arm."""
    monkeypatch.delenv("PINNAPI_FEED", raising=False)
    assert FR.enabled() is True
    for off in ("off", "0", "false", "NO", " Off "):
        monkeypatch.setenv("PINNAPI_FEED", off)
        assert FR.enabled() is False
        r = asyncio.run(FR.start_default(None, writer_pid=1,
                                         writer_lock_key=WRITER_KEY))
        assert r["state"] == "KILLED_BY_ENV_PINNAPI_FEED_OFF"
    assert FR.digest()["state"] == "NOT_STARTED"


@pg
async def test_started_but_unarmed_takes_no_lease_and_reads_no_catalogue(
        monkeypatch):
    """The default after deploy: started, control row absent -> DISARMED, no
    lease, no socket, and the heartbeat skips the census (no us_premap read)."""
    conn = await H.connect()
    pool = Pool(conn)
    try:
        await _set(conn, FR.CONTROL_KEY, None)
        monkeypatch.delenv("PINNAPI_FEED", raising=False)
        monkeypatch.setattr(FR, "HEARTBEAT_S", 0.05)
        leases, sockets, census_calls = [], [], []

        async def lf():
            leases.append(1)
            return await O.Lease.open(H.DSN)

        async def connect(url, key):
            sockets.append(1)
            return FakeWS(frames_for())

        async def no_census(_pool):
            census_calls.append(1)
            return {}
        monkeypatch.setattr(FR, "_census_once", no_census)
        r = await FR.start_default(pool, writer_pid=None,
                                   writer_lock_key=WRITER_KEY,
                                   lease_factory=lf, connect=connect)
        assert r["state"] == "STARTED"
        await asyncio.sleep(0.5)
        d = FR.digest()
        assert d["state"] == "DISARMED"
        assert leases == [] and sockets == [] and census_calls == []
        assert d["coverage_census"] == {"skipped": "FEED_NOT_SYNCED"}
        beat = FR._jsonish(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            FR.HEARTBEAT_KEY))
        assert beat["state"] == "DISARMED"
    finally:
        await FR.shutdown_default(wait_s=2.0)
        await _set(conn, FR.HEARTBEAT_KEY, None)
        await conn.close()


def test_scope_defaults_to_baseball_and_is_bounded():
    assert FR.DEFAULT_SCOPE == {"sport_ids": [6],
                                "streams": ["live", "prematch"]}


# ── coverage census: every contract one state, reconciled ───────────
def test_census_gives_every_contract_one_state_and_reconciles():
    from sportsassets import pinnapi_census as C
    now = 1_790_900_000.0
    rows = [
        # matched MLB event: moneyline supported, total not
        {"identifier": "a", "event_slug": "e1", "event_title":
         "Tampa Bay Rays vs New York Yankees", "kind": "moneyline",
         "line": None, "sports_type": "baseball_mlb",
         "game_start": now - 600},
        {"identifier": "b", "event_slug": "e1", "event_title":
         "Tampa Bay Rays vs New York Yankees", "kind": "total",
         "line": "8.5", "sports_type": "baseball_mlb",
         "game_start": now - 600},
        # no provider event
        {"identifier": "c", "event_slug": "e2", "event_title":
         "Boston Red Sox vs Chicago Cubs", "kind": "moneyline", "line": None,
         "sports_type": "baseball_mlb", "game_start": now + 3600},
        # out of scope sport, unmapped sport, no sides
        {"identifier": "d", "event_slug": "e3", "event_title": "A vs B",
         "kind": "moneyline", "line": None, "sports_type": "soccer_epl",
         "game_start": now + 60},
        {"identifier": "e", "event_slug": "e4", "event_title": "X vs Y",
         "kind": "moneyline", "line": None, "sports_type": "curling",
         "game_start": now + 60},
        {"identifier": "f", "event_slug": "e5", "event_title":
         "Who wins the pennant", "kind": "moneyline", "line": None,
         "sports_type": "baseball_mlb", "game_start": now + 60},
    ]
    view = {6: [{"id": 99, "home": "New York Yankees",
                 "away": "Tampa Bay Rays", "start": now - 500,
                 "live": True}]}
    out = C.census(rows, view, subscribed_sports={6}, synced=True, now=now)
    assert out["reconciled"] is True and out["total_contracts"] == 6
    assert out["states"] == {"MATCHED_SUPPORTED": 1,
                             "MATCHED_UNSUPPORTED_FAMILY": 1,
                             "NO_FEED_EVENT": 1,
                             "OUT_OF_FEED_SCOPE_SPORT": 1,
                             "UNMAPPED_SPORT": 1,
                             "NO_TWO_SIDED_TITLE": 1}
    assert out["unsupported_reasons"] == {"TOTALS_GRADING_NOT_PROVED": 1}
    assert out["by_sport_family_phase_state"][
        "6|MONEYLINE|IN_PLAY|MATCHED_SUPPORTED"] == 1
    # unsynced feed: nothing in scope reads as matched
    out2 = C.census(rows, view, subscribed_sports={6}, synced=False, now=now)
    assert out2["states"].get("FEED_NOT_SYNCED") == 4
    assert "MATCHED_SUPPORTED" not in out2["states"]


def test_census_refuses_a_squad_qualifier_mismatch_and_far_start():
    from sportsassets import pinnapi_census as C
    now = 1_790_900_000.0
    feed = [{"id": 1, "home": "Arsenal Women", "away": "Chelsea Women",
             "start": now}, {"id": 2, "home": "Arsenal", "away": "Chelsea",
                             "start": now + 6 * 3600}]
    assert C.match_event(("Arsenal", "Chelsea"), now, feed)[0] == \
        C.S_NO_FEED_EVENT
