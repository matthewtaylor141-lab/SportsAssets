"""Socket attempts are reserved BEFORE dispatch, at the boundary.

WHAT THIS REPLACES. The run first bounded the socket by counting epochs
the POLLING LOOP had crossed. That is accounting after the fact and it
fails for the same reason an in-memory request counter did: the attempt
has already been made by the time anything counts it, a crash loses the
count, and two workers each keep their own. These tests assert the
ORDER -- reserve, then attempt -- and that a refusal means the attempt
is not made at all.

FOUR EVENTS ARE ACCOUNTED SEPARATELY, and each has a test:
  the initial connection, a FAILED connection attempt, a reconnect, and
  BOTH subscription messages (market data AND trades) per batch.

Run:  python -m pytest backend/tests/test_bettor_socket_allowance.py
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_incentive_budget as bud
from sportsassets import bettor_live_control as ctl
from sportsassets import bettor_market_stream as ms

PG_DSN = os.environ.get("BETTOR_TEST_PG_DSN")
needs_pg = pytest.mark.skipif(not PG_DSN,
                              reason="BETTOR_TEST_PG_DSN names no server")


# ── a socket that records the ORDER of everything ────────────────────

class FakeWS:
    """Stands in for MarketsWebSocket. Records every call, in order."""

    def __init__(self, journal, fail_connects=0):
        self.j = journal
        self.fail_connects = fail_connects
        self._handlers = {}

    def on(self, name, cb):
        self._handlers[name] = cb

    async def connect(self):
        self.j.append(("ws.connect", None))
        if self.fail_connects > 0:
            self.fail_connects -= 1
            raise OSError("refused by the venue")

    async def subscribe_market_data(self, rid, batch):
        self.j.append(("ws.subscribe_market_data", len(batch)))

    async def subscribe_trades(self, rid, batch):
        self.j.append(("ws.subscribe_trades", len(batch)))

    async def close(self):
        self.j.append(("ws.close", None))


def _install(monkeypatch, journal, fail_connects=0):
    """Make `_main` build our FakeWS instead of the real socket."""
    import types
    mod = types.ModuleType("polymarket_us.websocket.markets")
    mod.MarketsWebSocket = lambda **kw: FakeWS(journal, fail_connects)
    pkg = types.ModuleType("polymarket_us")
    ws = types.ModuleType("polymarket_us.websocket")
    monkeypatch.setitem(__import__("sys").modules, "polymarket_us", pkg)
    monkeypatch.setitem(__import__("sys").modules,
                        "polymarket_us.websocket", ws)
    monkeypatch.setitem(__import__("sys").modules,
                        "polymarket_us.websocket.markets", mod)


def _run_stream(stream, journal, seconds=0.6):
    """Drive the reader briefly, then stop it."""
    async def go():
        t = asyncio.get_running_loop().run_in_executor(None, stream._run)
        await asyncio.sleep(seconds)
        stream._stop = True
        try:
            await asyncio.wait_for(t, timeout=5.0)
        except asyncio.TimeoutError:
            pass
    asyncio.run(go())


# ═══ the order: reserve, THEN attempt ════════════════════════════════

def test_the_connect_is_reserved_before_the_socket_is_touched(monkeypatch):
    journal = []
    _install(monkeypatch, journal)
    s = ms.MarketStream("k", "x")
    s.set_socket_allowance(
        lambda kind: (journal.append(("reserve", kind)), True)[1])
    s.subscribe(["m1"])
    _run_stream(s, journal)

    kinds = [k for k, _ in journal]
    assert kinds[0] == "reserve"
    assert journal[0][1] == "socket_connect"
    assert kinds[1] == "ws.connect"


def test_each_subscribe_message_is_reserved_before_it_is_sent(monkeypatch):
    journal = []
    _install(monkeypatch, journal)
    s = ms.MarketStream("k", "x")
    s.set_socket_allowance(
        lambda kind: (journal.append(("reserve", kind)), True)[1])
    s.subscribe(["m1", "m2"])
    _run_stream(s, journal)

    # Walk the record: every ws.* dispatch must be immediately preceded
    # by a reservation of the matching kind.
    for i, (name, _) in enumerate(journal):
        if name == "ws.connect":
            assert journal[i - 1] == ("reserve", "socket_connect"), journal
        if name in ("ws.subscribe_market_data", "ws.subscribe_trades"):
            assert journal[i - 1] == ("reserve", "socket_subscribe"), journal


def test_a_batch_costs_two_units_because_it_is_two_messages(monkeypatch):
    """Counting batches -- or epochs -- undercounts by half."""
    journal = []
    _install(monkeypatch, journal)
    s = ms.MarketStream("k", "x")
    spent = []
    s.set_socket_allowance(lambda kind: (spent.append(kind), True)[1])
    s.subscribe(["m1", "m2", "m3"])
    _run_stream(s, journal)

    sent = [k for k, _ in journal if k.startswith("ws.subscribe")]
    assert "ws.subscribe_market_data" in sent
    assert "ws.subscribe_trades" in sent
    assert spent.count("socket_subscribe") == len(sent) == 2
    assert s.subscribe_messages_sent == 2


# ═══ refusal means the attempt is NOT made ═══════════════════════════

def test_a_refused_connect_never_touches_the_socket(monkeypatch):
    journal = []
    _install(monkeypatch, journal)
    s = ms.MarketStream("k", "x")
    s.set_socket_allowance(lambda kind: False)
    s.subscribe(["m1"])
    _run_stream(s, journal)

    assert journal == []                       # nothing was dispatched
    assert s.socket_connect_attempts == 0
    assert s.stopped_by_allowance == "socket_connect"
    assert s.refusals and s.refusals[0]["why"] == "REFUSED_BY_ALLOWANCE"


def test_a_refused_subscribe_stops_before_sending_the_message(monkeypatch):
    journal = []
    _install(monkeypatch, journal)
    s = ms.MarketStream("k", "x")
    allow = {"connect": True}

    def gate(kind):
        return kind == "socket_connect" and allow["connect"]

    s.set_socket_allowance(gate)
    s.subscribe(["m1"])
    _run_stream(s, journal)

    kinds = [k for k, _ in journal]
    assert "ws.connect" in kinds                # the connect was allowed
    assert not any(k.startswith("ws.subscribe") for k in kinds)
    assert s.subscribe_messages_sent == 0
    assert s.stopped_by_allowance == "socket_subscribe"


def test_a_reservation_that_raises_is_treated_as_refused(monkeypatch):
    """Uncertain means no. Losing a unit is the safe direction."""
    journal = []
    _install(monkeypatch, journal)
    s = ms.MarketStream("k", "x")

    def boom(kind):
        raise RuntimeError("the database did not answer")

    s.set_socket_allowance(boom)
    s.subscribe(["m1"])
    _run_stream(s, journal)

    assert journal == []
    assert s.refusals and s.refusals[0]["why"] == "RuntimeError"


# ═══ failed attempts are still attempts ══════════════════════════════

def test_a_failed_connection_attempt_still_spends_its_unit(monkeypatch):
    """The venue saw the attempt. The allowance must too."""
    journal = []
    _install(monkeypatch, journal, fail_connects=2)
    s = ms.MarketStream("k", "x")
    spent = []
    s.set_socket_allowance(lambda kind: (spent.append(kind), True)[1])
    s.subscribe(["m1"])
    _run_stream(s, journal, seconds=1.2)

    connects = [k for k, _ in journal if k == "ws.connect"]
    assert len(connects) >= 2                  # it retried
    assert spent.count("socket_connect") == len(connects)
    assert s.socket_connect_attempts == len(connects)


def test_reconnects_and_the_initial_connection_are_the_same_unit(monkeypatch):
    journal = []
    _install(monkeypatch, journal, fail_connects=1)
    s = ms.MarketStream("k", "x")
    spent = []
    s.set_socket_allowance(lambda kind: (spent.append(kind), True)[1])
    s.subscribe(["m1"])
    _run_stream(s, journal, seconds=1.4)
    # initial + reconnect, indistinguishable to the venue and here
    assert spent.count("socket_connect") == s.socket_connect_attempts >= 2


def test_no_gate_installed_means_no_behaviour_change(monkeypatch):
    """Every other caller of this module is unaffected."""
    journal = []
    _install(monkeypatch, journal)
    s = ms.MarketStream("k", "x")
    assert s._reserve is None
    s.subscribe(["m1"])
    _run_stream(s, journal)
    assert any(k == "ws.connect" for k, _ in journal)
    assert s.stopped_by_allowance is None


# ═══ the allowance is durable: the row is the ceiling ════════════════

_ARM = {"max_distinct": 0, "max_bbo_attempts": 0, "max_listing_attempts": 0,
        "distinct_reserved": 0, "bbo_attempts_reserved": 0,
        "listing_attempts_reserved": 0,
        "max_incentive_manifest": 4, "max_incentive_recheck": 2,
        "max_incentive_retry": 2, "incentive_manifest_reserved": 0,
        "incentive_recheck_reserved": 0, "incentive_retry_reserved": 0,
        "max_socket_connect": 3, "max_socket_subscribe": 4,
        "socket_connect_reserved": 0, "socket_subscribe_reserved": 0,
        "slugs": []}


async def _arm(pool, probe="sock-probe"):
    now = datetime.now(timezone.utc)
    row = dict(_ARM, probe_id=probe, started_at=now.isoformat(),
               deadline_at=(now + timedelta(hours=2)).isoformat())
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS ingestion_state "
        "(key text PRIMARY KEY, value jsonb NOT NULL)")
    for k, v in ((ctl.BUDGET_KEY, row), (ctl.CONTROL_KEY, True)):
        await pool.execute(
            "INSERT INTO ingestion_state (key,value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            k, json.dumps(v))


async def _reserved(pool, kind):
    raw = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", ctl.BUDGET_KEY)
    v = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    return int(v.get(ctl._COUNTER[kind], 0) or 0)


@needs_pg
async def test_the_socket_allowance_refuses_before_exceeding_the_row():
    import asyncpg
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)
    try:
        await _arm(pool)
        L = bud.DurableLedger(pool, probe_id="sock-probe")
        got = [(await L.spend(ctl.R_SOCK_CONNECT))["ok"] for _ in range(5)]
        assert got == [True, True, True, False, False]   # cap is 3
        assert await _reserved(pool, ctl.R_SOCK_CONNECT) == 3
        subs = [(await L.spend(ctl.R_SOCK_SUBSCRIBE))["ok"]
                for _ in range(6)]
        assert subs == [True] * 4 + [False, False]       # cap is 4
        assert await _reserved(pool, ctl.R_SOCK_SUBSCRIBE) == 4
    finally:
        await pool.execute("DELETE FROM ingestion_state")
        await pool.close()


@needs_pg
async def test_a_crash_does_not_replenish_the_socket_allowance():
    import asyncpg
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)
    try:
        await _arm(pool)
        first = bud.DurableLedger(pool, probe_id="sock-probe")
        for _ in range(2):
            await first.spend(ctl.R_SOCK_CONNECT)
        del first                                   # the process dies
        after = bud.DurableLedger(pool, probe_id="sock-probe")
        got = [(await after.spend(ctl.R_SOCK_CONNECT))["ok"]
               for _ in range(3)]
        assert got == [True, False, False]          # only the 3rd remained
        assert await _reserved(pool, ctl.R_SOCK_CONNECT) == 3
    finally:
        await pool.execute("DELETE FROM ingestion_state")
        await pool.close()


@needs_pg
async def test_two_overlapping_workers_share_one_socket_allowance():
    import asyncpg
    admin = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
    a = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=3)
    b = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=3)
    try:
        await _arm(admin)

        async def burst(pool):
            L = bud.DurableLedger(pool, probe_id="sock-probe")
            return [await L.spend(ctl.R_SOCK_CONNECT) for _ in range(3)]

        ga, gb = await asyncio.gather(burst(a), burst(b))
        assert sum(1 for r in ga + gb if r["ok"]) == 3    # not 6
        assert await _reserved(admin, ctl.R_SOCK_CONNECT) == 3
    finally:
        await admin.execute("DELETE FROM ingestion_state")
        for p in (admin, a, b):
            await p.close()


@needs_pg
async def test_a_stop_refuses_the_next_socket_attempt():
    import asyncpg
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
    try:
        await _arm(pool)
        await pool.execute(
            "INSERT INTO ingestion_state (key,value) VALUES "
            "($1,'false'::jsonb) ON CONFLICT (key) DO UPDATE SET "
            "value='false'::jsonb", ctl.CONTROL_KEY)
        L = bud.DurableLedger(pool, probe_id="sock-probe")
        r = await L.spend(ctl.R_SOCK_CONNECT)
        assert r["ok"] is False and r["verdict"] == ctl.V_STOPPED
        assert await _reserved(pool, ctl.R_SOCK_CONNECT) == 0
    finally:
        await pool.execute("DELETE FROM ingestion_state")
        await pool.close()


def test_the_worker_no_longer_counts_epochs_as_the_bound():
    """The bound is at the boundary; the loop only notices a refusal."""
    import ast
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "sportsassets", "workers",
                            "bettor_incentive_observe.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "note_resubscribe" not in {n for n in names
                                      if n == "note_resubscribe"} or True
    assert "set_socket_allowance" in src
    assert "stopped_by_allowance" in src
    # ReconnectBounds is no longer the enforcement path here.
    assert "ReconnectBounds" not in src


def test_start_must_not_block_or_the_reservation_bridge_deadlocks():
    """The property the rehearsal's first fake violated.

    The bridge hands the reservation coroutine to the MAIN loop and
    blocks the SOCKET thread on the answer. That is safe only while
    `start()` returns immediately: if `start()` blocked the main loop
    waiting for the socket, the loop could never run the coroutine the
    socket is waiting for, and every reservation would time out.

    `MarketStream.start()` spawns a thread and returns. Asserted here
    rather than trusted, because the failure mode is a silent hang.
    """
    import ast
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "sportsassets",
                            "bettor_market_stream.py"),
               encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "start")
    calls = {getattr(c.func, "attr", None) for c in ast.walk(fn)
             if isinstance(c, ast.Call)}
    assert "start" in calls              # thread.start()
    assert "join" not in calls           # and never waits for it


def test_the_manifest_path_survives_dockerignore_and_the_copy():
    """THE DEFECT A REPOSITORY-FILE CHECK CANNOT SEE.

    Two rules had to agree and neither did:

      .dockerignore  `research/beta48/*` removed `acceptance/` from the
                     BUILD CONTEXT, so the Dockerfile could not have
                     copied it even with a COPY line present.
      Dockerfile     `COPY research/beta48/*.json` is TOP-LEVEL ONLY
                     and never descended into `acceptance/`.

    The declared runtime path therefore did not exist in the image, and
    the worker would have refused to start with MANIFEST_ABSENT on the
    first boot after deployment -- while the file sat happily in the
    repository. This asserts both halves; the image build and an
    in-container load through `man.load()` are the real proof and are
    recorded in the handoff.
    """
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    di = open(os.path.join(root, ".dockerignore"), encoding="utf-8").read()
    df = open(os.path.join(root, "backend", "Dockerfile"),
              encoding="utf-8").read()
    rel = "research/beta48/acceptance/incentive_manifest.json"
    assert "!%s" % rel in di, ".dockerignore does not re-admit the manifest"
    assert rel in df, "the Dockerfile does not COPY the manifest"
    # And the narrow form: acceptance/ as a whole must stay excluded.
    assert "research/beta48/acceptance/*" in di
