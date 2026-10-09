"""RC6 api-responsive: /healthz never queues on a saturated pool, and its DB
field still fails when the database is genuinely unavailable.

PRODUCTION (RC5, release 69a8a07e). Render restarted the API twice for "HTTP
health check failed (timed out after 5 seconds)" -- 2026-10-08 16:34:42Z and
18:39:26Z (render-ops events). Minutes before each, the pool was saturated:
the feed heartbeat and the rn1x learn heartbeat timed out inside asyncpg's
Pool._acquire (16:32:38Z, 18:37:41Z, 18:37:43Z, 18:38:17Z) and browser reads
took 13-28 s. /healthz queued its SELECT 1 behind every other waiter for up
to its 2 s ceiling, and the loop watchdog's persisted ring (research-sql
rc6_api-responsive_loop_stalls.sql) shows API loop stalls of 2.3-3.5 s in
full: 2 s of queueing on top of a 3.5 s stall is a 5.5 s answer.

THE CONTRACT AFTER RC6 (db.health_probe):
  * every connection out and none idle -> db_ok false AT ONCE, db_probe
    POOL_SATURATED_NOT_QUEUED (what the 2 s race concluded, without the 2 s);
  * a free connection -> SELECT 1 under the same 2 s ceiling: a slow or dead
    database is still db_ok false (TIMEOUT / ERROR:<type>);
  * concurrent checks share one probe;
  * `ok` stays true (the DB is a field, never a veto) and no pool is built.
The real-database half runs the probe through a TCP proxy in front of the
test Postgres, so "genuinely unavailable" is a closed socket and "hung" is a
socket that never answers -- not a stub.
"""
from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import urlsplit, urlunsplit

import pytest

from sportsassets import db as db_mod
from sportsassets.api import app as app_mod

# the probe's names, spelled here so the behaviour (not an import) is what a
# tree without them fails on
SATURATED, TIMEOUT, OK = "POOL_SATURATED_NOT_QUEUED", "TIMEOUT", "OK"


def test_the_names_are_the_modules_own():
    assert (db_mod.PROBE_POOL_SATURATED, db_mod.PROBE_TIMEOUT,
            db_mod.PROBE_OK) == (SATURATED, TIMEOUT, OK)


class _Counters:
    def __init__(self, size, idle, max_size, min_size):
        self._c = (size, idle, max_size, min_size)

    def get_size(self):
        return self._c[0]

    def get_idle_size(self):
        return self._c[1]

    def get_max_size(self):
        return self._c[2]

    def get_min_size(self):
        return self._c[3]


class _Hung(_Counters):
    def __init__(self, *c):
        super().__init__(*c)
        self.calls = 0

    async def fetchval(self, sql, *a):
        self.calls += 1
        await asyncio.Event().wait()


class _Slow(_Counters):
    def __init__(self, *c, delay=0.3):
        super().__init__(*c)
        self.calls, self.delay = 0, delay

    async def fetchval(self, sql, *a):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return 1


def _install(monkeypatch, pool):
    monkeypatch.setattr(db_mod, "_pool", pool)
    monkeypatch.setattr(db_mod, "_probe_inflight", None, raising=False)

    async def _never():
        raise AssertionError("/healthz must not call get_pool()")

    monkeypatch.setattr(app_mod, "get_pool", _never)
    monkeypatch.setattr(db_mod, "get_pool", _never)


class TestASaturatedPoolIsNeverQueued:
    def test_saturated_answers_at_once_with_db_ok_false_and_a_name(
            self, monkeypatch):
        """The production shape: size == max, idle == 0, the SELECT 1 would
        wait. Before RC6 this took the full 2 s ceiling (it awaited the
        hung pool); now it does not even ask."""
        pool = _Hung(10, 0, 10, 1)
        _install(monkeypatch, pool)
        t0 = time.monotonic()
        out = asyncio.run(app_mod.healthz())
        took = time.monotonic() - t0
        assert took < 0.5, "queued on a saturated pool for %.2f s" % took
        assert out["ok"] is True
        assert out["db_ok"] is False
        assert out["db_probe"] == SATURATED
        assert out["pool"] == {"size": 10, "idle": 0, "max": 10, "min": 1}
        assert pool.calls == 0, "no SELECT 1 is queued on a saturated pool"

    def test_a_free_connection_still_probes_and_a_slow_db_still_fails(
            self, monkeypatch):
        """idle > 0: the probe runs under the unchanged 2 s ceiling, and a
        database that does not answer is db_ok false, named TIMEOUT."""
        pool = _Hung(4, 2, 10, 1)
        _install(monkeypatch, pool)
        t0 = time.monotonic()
        out = asyncio.run(app_mod.healthz())
        took = time.monotonic() - t0
        assert 1.5 <= took < 3.0
        assert out["db_ok"] is False and out["ok"] is True
        assert out["db_probe"] == TIMEOUT
        assert pool.calls == 1

    def test_room_to_grow_is_not_saturation(self, monkeypatch):
        """size < max: an acquire opens a connection rather than waiting."""
        pool = _Slow(3, 0, 10, 1, delay=0.0)
        _install(monkeypatch, pool)
        out = asyncio.run(app_mod.healthz())
        assert out["db_ok"] is True and out["db_probe"] == OK
        assert pool.calls == 1

    def test_concurrent_checks_share_one_probe(self, monkeypatch):
        pool = _Slow(4, 2, 10, 1, delay=0.3)
        _install(monkeypatch, pool)

        async def go():
            return await asyncio.gather(*(app_mod.healthz()
                                          for _ in range(6)))
        outs = asyncio.run(go())
        assert all(o["db_ok"] is True for o in outs)
        assert pool.calls == 1, "six concurrent checks, %d probes" % \
            pool.calls

    def test_an_empty_slot_queue_is_saturation_whatever_the_counters(
            self, monkeypatch):
        """A pool opening connections for a burst reads size < max ("room
        to grow") while every slot is taken: what decides whether an acquire
        waits is the pool's queue of free slots, and an empty one is not
        queued on."""
        class _Q:
            def __init__(self, empty):
                self._e = empty

            def empty(self):
                return self._e

        pool = _Hung(1, 0, 3, 1)
        pool._queue = _Q(True)
        _install(monkeypatch, pool)
        t0 = time.monotonic()
        out = asyncio.run(app_mod.healthz())
        assert time.monotonic() - t0 < 0.5
        assert out["db_ok"] is False and out["db_probe"] == SATURATED
        assert pool.calls == 0
        # a free slot in the queue: the probe runs (and its ceiling decides)
        pool2 = _Slow(10, 0, 10, 1, delay=0.0)
        pool2._queue = _Q(False)
        _install(monkeypatch, pool2)
        out2 = asyncio.run(app_mod.healthz())
        assert out2["db_ok"] is True and out2["db_probe"] == OK
        assert pool2.calls == 1

    def test_saturation_reads_the_counters_never_an_acquire(self):
        assert db_mod.pool_saturated({"size": 10, "idle": 0, "max": 10,
                                      "min": 1}) is True
        assert db_mod.pool_saturated({"size": 10, "idle": 1, "max": 10,
                                      "min": 1}) is False
        assert db_mod.pool_saturated({"size": 9, "idle": 0, "max": 10,
                                      "min": 1}) is False
        # unknown counters are not saturation: the probe and its ceiling
        # decide, exactly as before
        assert db_mod.pool_saturated(None) is False
        assert db_mod.pool_saturated({"size": None}) is False


# ── the real database, behind a proxy we can close or black-hole ─────────

DSN = os.environ.get("RN1X_TEST_DSN", "") or os.environ.get("DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not DSN.startswith("postgres"),
                              reason="needs DATABASE_URL (a real Postgres)")


class _Proxy:
    """A TCP forwarder to the test Postgres. mode: OPEN forwards, HUNG
    accepts and forwards nothing, and close() drops every socket and stops
    listening (the database is gone)."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.mode = "OPEN"
        self.server = None
        self.writers: list = []

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1",
                                                 0)
        return self.server.sockets[0].getsockname()[1]

    async def _pump(self, r, w):
        try:
            while True:
                data = await r.read(65536)
                if not data:
                    break
                if self.mode == "OPEN":
                    w.write(data)
                    await w.drain()
        except Exception:                                       # noqa: BLE001
            pass
        finally:
            try:
                w.close()
            except Exception:                                   # noqa: BLE001
                pass

    async def _client(self, cr, cw):
        self.writers.append(cw)
        if self.mode != "OPEN":
            # accept, read, never answer: a hung database
            await self._pump(cr, cw)
            return
        try:
            ur, uw = await asyncio.open_connection(self.host, self.port)
        except OSError:
            cw.close()
            return
        self.writers.append(uw)
        await asyncio.gather(self._pump(cr, uw), self._pump(ur, cw))

    def drop_all(self):
        for w in self.writers:
            try:
                w.transport.abort()
            except Exception:                                   # noqa: BLE001
                pass
        self.writers.clear()

    async def close(self):
        self.server.close()
        self.drop_all()
        await self.server.wait_closed()


def _via(port: int) -> str:
    u = urlsplit(DSN)
    netloc = u.netloc.rsplit("@", 1)
    host = "127.0.0.1:%d" % port
    return urlunsplit(u._replace(netloc=(netloc[0] + "@" + host)
                                 if len(netloc) == 2 else host))


@needs_pg
def test_the_real_database_saturated_then_recovered_then_gone(monkeypatch):
    import asyncpg

    monkeypatch.setattr(db_mod, "_probe_inflight", None, raising=False)

    async def go():
        u = urlsplit(DSN)
        px = _Proxy(u.hostname or "127.0.0.1", u.port or 5432)
        port = await px.start()
        pool = await asyncpg.create_pool(_via(port), min_size=1, max_size=3)
        monkeypatch.setattr(db_mod, "_pool", pool)
        out = {}
        try:
            out["healthy"] = await app_mod.healthz()
            # SATURATE: every pooled connection busy for 3 s
            hold = [asyncio.create_task(pool.fetchval("SELECT pg_sleep(3)"))
                    for _ in range(3)]
            await asyncio.sleep(0.4)
            t0 = time.monotonic()
            out["saturated"] = await app_mod.healthz()
            out["saturated_s"] = time.monotonic() - t0
            await asyncio.gather(*hold)
            # RECOVERED: a free connection again
            out["recovered"] = await app_mod.healthz()
            # GONE: every socket dropped, nothing listening
            await px.close()
            t0 = time.monotonic()
            out["gone"] = await app_mod.healthz()
            out["gone_s"] = time.monotonic() - t0
        finally:
            pool.terminate()
            monkeypatch.setattr(db_mod, "_pool", None)
        return out

    out = asyncio.run(go())
    assert out["healthy"]["db_ok"] is True
    assert out["healthy"]["db_probe"] == OK
    sat = out["saturated"]
    assert sat["ok"] is True and sat["db_ok"] is False
    assert sat["db_probe"] == SATURATED
    assert sat["pool"]["size"] == sat["pool"]["max"] == 3
    assert sat["pool"]["idle"] == 0
    assert out["saturated_s"] < 0.5, out["saturated_s"]
    assert out["recovered"]["db_ok"] is True
    gone = out["gone"]
    assert gone["ok"] is True and gone["db_ok"] is False, gone
    assert gone["db_probe"] != OK
    assert out["gone_s"] < 2.5


@needs_pg
def test_a_pool_still_opening_its_connections_is_never_queued_on(
        monkeypatch):
    """The real pool, the moment a burst takes every slot of a pool that
    has not opened them all yet: size < max, idle 0, the slot queue empty.
    The counters alone read "room to grow" and the probe queued its full
    2 s (the responsiveness harness, phase B, 2 of 5 runs)."""
    import asyncpg

    monkeypatch.setattr(db_mod, "_probe_inflight", None, raising=False)

    async def go():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
        monkeypatch.setattr(db_mod, "_pool", pool)
        try:
            hold = [asyncio.create_task(pool.fetchval("SELECT pg_sleep(1.5)"))
                    for _ in range(3)]
            await asyncio.sleep(0)          # every slot taken, two opening
            seen = {"size": pool.get_size(), "max": pool.get_max_size()}
            t0 = time.monotonic()
            got = await app_mod.healthz()
            took = time.monotonic() - t0
            await asyncio.gather(*hold)
            after = await app_mod.healthz()
            return seen, got, took, after
        finally:
            await pool.close()
            monkeypatch.setattr(db_mod, "_pool", None)

    seen, got, took, after = asyncio.run(go())
    assert seen["size"] < seen["max"], seen      # the counters say "room"
    assert got["ok"] is True and got["db_ok"] is False
    assert got["db_probe"] == SATURATED, got
    assert took < 0.5, took
    assert after["db_ok"] is True and after["db_probe"] == OK


@needs_pg
def test_a_hung_database_is_db_ok_false_within_the_ceiling(monkeypatch):
    """The database accepts and never answers (the full-disk shape of
    2026-09-04/05): db_ok false, named TIMEOUT, inside the 2 s ceiling."""
    import asyncpg

    monkeypatch.setattr(db_mod, "_probe_inflight", None, raising=False)

    async def go():
        u = urlsplit(DSN)
        px = _Proxy(u.hostname or "127.0.0.1", u.port or 5432)
        port = await px.start()
        pool = await asyncpg.create_pool(_via(port), min_size=1, max_size=2)
        monkeypatch.setattr(db_mod, "_pool", pool)
        try:
            px.mode = "HUNG"
            px.drop_all()          # the live connection dies; new ones hang
            await asyncio.sleep(0.2)
            t0 = time.monotonic()
            got = await app_mod.healthz()
            return got, time.monotonic() - t0
        finally:
            pool.terminate()
            monkeypatch.setattr(db_mod, "_pool", None)
            await px.close()

    got, took = asyncio.run(go())
    assert got["ok"] is True and got["db_ok"] is False, got
    assert got["db_probe"] in (TIMEOUT,) or \
        str(got["db_probe"]).startswith("ERROR:"), got
    assert took < 2.6, took
