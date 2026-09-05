"""The money gate fails CLOSED when its storage is unreadable.

Disk-full incident, 2026-09-04 23:15Z to 2026-09-05 ~14:30Z:
sportsassets-db filled, Render degraded it and its hostname stopped
resolving, and every roster read raised. The stored roster was intact
and UNREADABLE. The reader fell through to the five-whale code default
and the hardcoded clips, and /api/admin/gates reported exactly that
(source=default; swisstony 250, ferrari 100, 0x076daa87 250 -- three
whales the owner had cut to $0 in the row we could not read). Nothing
traded only because the workers were down.

These pins hold the three states apart on the REAL read functions and
the REAL order-path decisions:

  (1) a row is stored      -> it is used
  (2) no row is stored     -> env, then the code default (legitimate)
  (3) the read FAILED      -> CLOSED: no whale verified, every clip 0.0,
                              cached (not hammering a dead database),
                              until a later read SUCCEEDS

and that the gates payload says what the executor will DO. The fake
pool below is the only I/O; every assertion is on the function that
production calls, not on a rebuilt copy of it.
"""
import ast
import asyncio
import inspect
import json
import logging
import time

import pytest

from sportsassets import live_executor as le

SLUG = "tsc-epl-ars-che-2026-09-05-o3pt5"
LOGGER = "sportsassets.live_executor"


class _Pool:
    """The override reads, three ways: a row, no row, or a raise.

    `boom` raises for every key -- the incident's shape, where the host
    did not resolve. `boom_keys` raises for one key, so the clips can
    fail while the roster reads."""

    def __init__(self, roster=None, clips=None, boom=False, boom_keys=()):
        self.roster, self.clips = roster, clips
        self.boom, self.boom_keys = boom, set(boom_keys)
        self.reads: list[str] = []

    async def fetchval(self, sql, *a):
        assert sql == le._OVERRIDE_READ_SQL, sql
        key = a[0]
        self.reads.append(key)
        if self.boom or key in self.boom_keys:
            raise ConnectionError(
                "DB connect failed ([Errno -2] Name or service not known)")
        if key == le._ROSTER_DB_KEY:
            return None if self.roster is None else json.dumps(self.roster)
        if key == le._CLIPS_DB_KEY:
            return None if self.clips is None else json.dumps(self.clips)
        return None

    async def execute(self, *a, **k):
        raise AssertionError("the override reads must not write")


class _GatePool(_Pool):
    """For _mapping_admitted and volume_normalized_clip, whose own
    reads use the plain SELECT: answer nothing (circuit not tripped,
    no day spend) so the closed state is the only thing deciding."""

    async def fetchval(self, sql, *a):
        if sql == le._OVERRIDE_READ_SQL:
            return await super().fetchval(sql, *a)
        return None


def _fresh():
    le._roster_override = None
    le._clip_override = None
    le._roster_read_at = 0.0
    le._closed_read_at = 0.0
    le._closed_since = 0.0
    le._closed_error = None


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("LIVE_VERIFIED_WHALES", "LIVE_HOLD_WHALES", "LIVE_PREMAP_WHALES",
              "LIVE_PREMAP"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
    _fresh()
    yield
    _fresh()


def _refresh(pool):
    """One forced refresh: both clocks expired."""
    le._roster_read_at = 0.0
    le._closed_read_at = 0.0
    asyncio.run(le.refresh_whale_overrides(pool))


def _admit(username):
    """The entry-path decision, from the function maybe_execute calls."""
    return asyncio.run(le._mapping_admitted(_GatePool(), username, "premap", SLUG))


def _clip(username):
    """The sizing decision, from the function the copy path calls."""
    return asyncio.run(le.volume_normalized_clip(_GatePool(), username, SLUG))


STORED = ["swisstony", "newbie"]
CLIPS = {"swisstony": 50.0, "newbie": 50.0}
HARDCODED_SWISSTONY = min(le.PER_FILL_BY_WHALE["swisstony"], le.LIVE_MAX_CLIP_USD)


# ───────────────────────── state (1): a row is stored ─────────────────────────

def test_state_1_a_stored_row_is_what_the_order_path_uses():
    _refresh(_Pool(roster=STORED, clips=CLIPS))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set(STORED)
    assert _admit("swisstony") == (True, None)
    ok, why = _admit("rn1")
    assert not ok and why.startswith("not verified-profitable:")
    assert le.per_fill_usd("swisstony") == 50.0
    assert _clip("swisstony") == 50.0
    assert "newbie" in le.exitable_whales()
    assert not le.overrides_unreadable()
    assert le.closed_state()["closed"] is False


# ─────────────────────── state (2): no row is stored ──────────────────────────

def test_state_2_no_row_is_the_env_then_the_default(monkeypatch):
    _refresh(_Pool(roster=None, clips=None))
    assert not le.overrides_unreadable()
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "rn1")
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"rn1"}
    assert _admit("rn1") == (True, None)
    monkeypatch.delenv("LIVE_VERIFIED_WHALES")
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set(
        le.VERIFIED_PROFITABLE_DEFAULT.split(","))
    assert le.per_fill_usd("swisstony") == HARDCODED_SWISSTONY, \
        "with no stored clips the hardcoded clip is legitimate"
    assert le.per_fill_usd("nobody") == min(le.PENNY_TRIAL_PER_FILL_USD,
                                            le.LIVE_MAX_CLIP_USD)


# ───────────────────── state (3): the read FAILED -> closed ───────────────────

def test_state_3_a_failed_read_is_closed_not_the_last_value_and_not_the_default(caplog):
    _refresh(_Pool(roster=STORED, clips=CLIPS))       # a good value adopted
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        _refresh(_Pool(boom=True))                    # then the DB goes away
    assert le.overrides_unreadable()
    (rec,) = [r for r in caplog.records if "CLOSED" in r.message]
    assert "UNREADABLE" in rec.message and "UNREAD at boot" not in rec.message, \
        "a closure after an adoption is not a boot closure"
    # not the last good value
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set(), \
        "the last adopted roster is not a roster we can read"
    assert le.per_fill_usd("swisstony") == 0.0
    # not the code default either: rn1 is in VERIFIED_PROFITABLE_DEFAULT
    # and PER_FILL_BY_WHALE, and he gets nothing
    assert "rn1" in le.VERIFIED_PROFITABLE_DEFAULT
    ok, why = _admit("rn1")
    assert not ok and "UNREADABLE" in why and "CLOSED" in why
    assert why.startswith("not verified-profitable:"), \
        "gate_edge files the refusal under the verified gate by its prefix"
    assert le.per_fill_usd("rn1") == 0.0
    assert _clip("rn1") == 0.0 and _clip("swisstony") == 0.0
    # every whale, including one the stored map named
    for w in (*STORED, *le.PER_FILL_BY_WHALE, "nobody"):
        assert le.per_fill_usd(w) == 0.0, w
        assert le.per_fill_usd(w, SLUG) == 0.0, w
    # the rules-only whale is no longer named by a map we cannot read;
    # the hardcoded sets still stand so hardcoded exits keep mirroring
    assert "newbie" not in le.exitable_whales()
    assert "swisstony" in le.exitable_whales()
    st = le.closed_state()
    assert st["closed"] is True and st["verified_effective"] == []
    assert st["clips_effective"] == "all 0.0"
    assert "Name or service not known" in st["last_error"]
    assert st["since"] is not None
    # the payload carries the LATEST failure while closed (an outage
    # whose shape changes is visible), and `since` stays the edge
    class _Later(_Pool):
        async def fetchval(self, sql, *a):
            raise TimeoutError("canceling statement due to statement timeout")

    le._closed_read_at = 0.0
    asyncio.run(le.refresh_whale_overrides(_Later()))
    st2 = le.closed_state()
    assert "statement timeout" in st2["last_error"]
    assert st2["since"] == st["since"]


def test_a_failure_at_boot_is_closed_not_the_default(caplog):
    """Fleet round 49 made this LOUD; the incident makes it CLOSED. With
    nothing ever adopted, 'keep last' was the code default."""
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        _refresh(_Pool(boom=True))
    assert le.overrides_unreadable()
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set()
    assert _admit("rn1")[0] is False
    assert le.per_fill_usd("rn1") == 0.0
    assert any("UNREAD at boot" in r.message and "CLOSED" in r.message
               for r in caplog.records)


def test_an_unreadable_empty_set_does_not_disable_the_gate(monkeypatch):
    """A DELIBERATELY empty LIVE_VERIFIED_WHALES is a full resume and
    disables the verified gate. The unreadable state answers the same
    empty set to _whale_set, and it must NOT be read as a resume."""
    _refresh(_Pool(roster=None, clips=None))
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set()
    assert _admit("nobody") == (True, None), "the deliberate case: open"
    _refresh(_Pool(boom=True))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set()
    assert _admit("nobody")[0] is False, "the unreadable case: closed"
    assert _admit("rn1")[0] is False


def test_the_clips_failing_alone_closes_the_pair():
    """A whale is live only if verified AND his clip is above zero. The
    two are read by one call; half a read is not a decision."""
    _refresh(_Pool(roster=STORED, clips=CLIPS))
    _refresh(_Pool(roster=STORED, clips=CLIPS, boom_keys=[le._CLIPS_DB_KEY]))
    assert le.overrides_unreadable()
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set()
    assert _admit("swisstony")[0] is False
    assert le.per_fill_usd("swisstony") == 0.0


def test_a_stored_roster_that_is_not_a_roster_stays_closed():
    _refresh(_Pool(boom=True))
    pool = _Pool(roster={"not": "a list"}, clips=CLIPS)
    _refresh(pool)
    assert le.overrides_unreadable(), "a row that reads but is not a roster"
    assert le.per_fill_usd("swisstony") == 0.0
    assert le._CLIPS_DB_KEY not in pool.reads, \
        "the clips are not asked for when the roster is not a roster"
    assert "not a list" in le.closed_state()["last_error"]
    # and the same rule for the clips: a row that reads but is not a
    # map cannot reopen a closed pair onto None (the hardcoded clips)
    _refresh(_Pool(roster=STORED, clips=["not", "a", "map"]))
    assert le.overrides_unreadable(), "a clips row that is not a map"
    assert le.per_fill_usd("swisstony") == 0.0
    assert "not a map" in le.closed_state()["last_error"]
    # while OPEN the same shapes keep the last adopted value (state 1)
    _refresh(_Pool(roster=STORED, clips=CLIPS))
    _refresh(_Pool(roster={"not": "a list"}, clips=["not", "a", "map"]))
    assert not le.overrides_unreadable()
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set(STORED)
    assert le.per_fill_usd("swisstony") == 50.0


# ─────────────────────────── the way back: a read succeeds ────────────────────

def test_a_later_successful_read_reopens_on_the_stored_value():
    _refresh(_Pool(boom=True))
    assert le.overrides_unreadable()
    _refresh(_Pool(roster=STORED, clips=CLIPS))
    assert not le.overrides_unreadable()
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set(STORED)
    assert _admit("swisstony") == (True, None)
    assert le.per_fill_usd("swisstony") == 50.0 and _clip("swisstony") == 50.0
    assert le.closed_state() == {
        "closed": False, "verified_effective": None, "clips_effective": None,
        "since": None, "last_error": None, "retry_s": le._CLOSED_RETRY_S}


def test_reopening_onto_no_row_is_state_2_not_a_stuck_close():
    _refresh(_Pool(boom=True))
    _refresh(_Pool(roster=None, clips=None))
    assert not le.overrides_unreadable()
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set(
        le.VERIFIED_PROFITABLE_DEFAULT.split(","))


# ───────────────────────────── the closed cache ───────────────────────────────

def test_the_closed_value_is_cached_and_the_retry_is_paced():
    pool = _Pool(boom=True)
    _refresh(pool)
    first = len(pool.reads)
    assert first == 2, "two attempts on the roster; the clips are not asked"
    for _ in range(20):                       # the hot path, event after event
        asyncio.run(le.refresh_whale_overrides(pool))
    assert len(pool.reads) == first, "inside the retry window: no read"
    assert le.overrides_unreadable() and le.per_fill_usd("rn1") == 0.0, \
        "the cached value is CLOSED"
    le._closed_read_at = time.time() - le._CLOSED_RETRY_S - 1
    asyncio.run(le.refresh_whale_overrides(pool))
    assert len(pool.reads) == first + 2, "after the window: one retry"
    assert le._CLOSED_RETRY_S < le._ROSTER_TTL_S, \
        "closed retries sooner than the open TTL"


def test_a_failure_does_not_start_the_open_ttl():
    """Fleet round 49: the TTL is the OPEN cache. A failed read must
    not buy itself thirty seconds; the closed clock paces it."""
    _refresh(_Pool(boom=True))
    assert le._roster_read_at == 0.0
    assert le._closed_read_at > 0.0


def test_the_read_is_bounded_by_a_timeout(monkeypatch):
    class _Hang(_Pool):
        async def fetchval(self, sql, *a):
            self.reads.append(a[0])
            await asyncio.sleep(30)

    monkeypatch.setattr(le, "_OVERRIDE_READ_TIMEOUT_S", 0.05)
    t0 = time.monotonic()
    _refresh(_Hang())
    assert time.monotonic() - t0 < 2.0
    assert le.overrides_unreadable()
    assert "TimeoutError" in le.closed_state()["last_error"]
    # the fake never executes SQL, so the text is pinned here
    assert le._OVERRIDE_READ_SQL == \
        "SELECT value FROM ingestion_state WHERE key=$1 LIMIT 1"


# ────────────────── the pair is adopted atomically, or not at all ─────────────

def test_nothing_is_adopted_until_both_reads_return():
    """Against a half-alive database the roster row can read while the
    clip read hangs. The first build published the roster (and started
    the TTL) inside the roster read, before asking for the clips, so
    for up to two read timeouts a concurrent event in the same loop
    saw an OPEN gate on a roster the database had just served and the
    hardcoded clips underneath: two reviewers each reproduced swisstony
    admitted and sized at $250, for a whale the owner had cut to $0 in
    the row not yet read. Nothing is adopted until BOTH reads return."""
    class _HalfAlive(_GatePool):
        def __init__(self):
            super().__init__(roster=STORED, clips=CLIPS)
            self.release = asyncio.Event()

        async def fetchval(self, sql, *a):
            if sql == le._OVERRIDE_READ_SQL and a[0] == le._CLIPS_DB_KEY:
                self.reads.append(a[0])
                await self.release.wait()             # the clip read hangs
                return json.dumps(self.clips)
            return await super().fetchval(sql, *a)

    async def scenario():
        # boot during the outage: closed, nothing ever adopted
        await le.refresh_whale_overrides(_Pool(boom=True))
        assert le.overrides_unreadable()
        le._closed_read_at = 0.0                      # past the retry window
        pool = _HalfAlive()
        task = asyncio.create_task(le.refresh_whale_overrides(pool))
        for _ in range(500):                          # let the roster read land
            await asyncio.sleep(0)
            if le._CLIPS_DB_KEY in pool.reads:
                break
        assert le._ROSTER_DB_KEY in pool.reads and le._CLIPS_DB_KEY in pool.reads
        # MID-WINDOW: a concurrent event on the same loop
        assert le.overrides_unreadable(), "nothing adopted until both reads return"
        assert le._whale_set("LIVE_VERIFIED_WHALES") == set()
        ok, why = await le._mapping_admitted(_GatePool(), "swisstony", "premap", SLUG)
        assert ok is False and "CLOSED" in why
        assert await le.volume_normalized_clip(_GatePool(), "swisstony", SLUG) == 0.0
        assert le.per_fill_usd("swisstony") == 0.0
        assert le._roster_read_at == 0.0, "half a read does not start the TTL"
        pool.release.set()
        await task
        assert not le.overrides_unreadable()
        assert le._whale_set("LIVE_VERIFIED_WHALES") == set(STORED)
        assert le.per_fill_usd("swisstony") == 50.0
        assert le._roster_read_at > 0.0

    asyncio.run(scenario())


def test_a_concurrent_edge_is_logged_once(caplog):
    """Four refreshes in flight when the database dies (the copy
    semaphore's default) are one edge, not four ERROR lines and four
    restamps of `since`."""
    class _SlowBoom(_Pool):
        async def fetchval(self, sql, *a):
            await asyncio.sleep(0.01)
            raise ConnectionError("Name or service not known")

    async def four():
        await asyncio.gather(*(le.refresh_whale_overrides(_SlowBoom())
                               for _ in range(4)))

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        asyncio.run(four())
    assert len([r for r in caplog.records if "CLOSED" in r.message]) == 1
    assert le.overrides_unreadable()


def test_no_pool_is_a_failed_read_at_both_money_paths():
    """The callers wrapped `refresh(await get_pool())` in one try: a
    get_pool() raise (a worker booted while the host did not resolve)
    skipped the refresh and left the pre-incident fall-through in
    place with no closed state at all."""
    le.close_overrides("pool: RuntimeError: DB unreachable after 10 attempts")
    assert le.overrides_unreadable()
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set()
    assert le.per_fill_usd("swisstony") == 0.0
    assert "DB unreachable" in le.closed_state()["last_error"]
    assert le._closed_read_at > 0.0, "paced like any other failed read"
    for fn in (le.maybe_execute, le.mirror_exit):
        s = inspect.getsource(fn)
        i = s.index("close_overrides(")
        assert "await get_pool()" in s[max(0, i - 300):i], fn.__name__
        assert "refresh_whale_overrides(_pool_for_roster)" in s, fn.__name__


def test_mirror_live_refreshes_the_pair_every_tick():
    """mirror_live's readers refuse while closed, but with the copy
    probe off nothing else in the workers process refreshed the pair;
    a rebooted worker sat on the code default with the hardcoded clips
    and never reached the closed state (two reviewers, 2026-09-05)."""
    import sys
    import types

    sys.modules.setdefault("pywebpush", types.SimpleNamespace(
        webpush=None, WebPushException=Exception))
    from sportsassets.workers import mirror_live as ml

    s = inspect.getsource(ml._tick)
    i = s.index("await le.refresh_whale_overrides(t.pool)")
    assert i < s.index("_tick_book(t, book)"), "before any book is planned"
    assert i < s.index("_SQL_BOOKS_OPEN"), "before the books are read"


# ───────────────────────── one log line per transition ────────────────────────

def test_the_transitions_are_logged_once_each(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(3):
            _refresh(_Pool(boom=True))
        closed = [r for r in caplog.records if "CLOSED" in r.message]
        assert len(closed) == 1 and closed[0].levelno == logging.ERROR
        for _ in range(3):
            _refresh(_Pool(roster=STORED, clips=CLIPS))
        opened = [r for r in caplog.records if "REOPENED" in r.message]
        assert len(opened) == 1 and opened[0].levelno == logging.WARNING
        _refresh(_Pool(boom=True))
        assert len([r for r in caplog.records if "CLOSED" in r.message]) == 2, \
            "a second outage is a second edge"
    assert not [r for r in caplog.records
                if "keeping the last" in r.message], \
        "the old per-call 'keeping the last adopted' line is gone"


# ───────────────────────── the gates payload says what binds ──────────────────

def test_the_gates_payload_says_what_the_executor_will_do(monkeypatch):
    from sportsassets.api import app as app_mod

    class _Everything(_Pool):
        """api_gates reads several keys through the plain SELECT; the
        override reads through the bounded one. Down is down for all."""
        async def fetchval(self, sql, *a):
            if sql == le._OVERRIDE_READ_SQL:
                return await super().fetchval(sql, *a)
            if self.boom:
                raise ConnectionError("Name or service not known")
            return None

        async def fetch(self, *a, **k):
            if self.boom:
                raise ConnectionError("Name or service not known")
            return []

    async def _pool_of(p):
        return p

    async def _no_edge(_pool):
        return None

    monkeypatch.setattr(app_mod, "get_pool", lambda: _pool_of(down))
    from sportsassets import edge_gate
    monkeypatch.setattr(edge_gate, "refresh", _no_edge)
    down = _Everything(boom=True)
    _fresh()
    out = asyncio.run(app_mod.api_gates())
    assert out["verified_source"] == "unreadable_closed"
    assert out["verified_effective"] == []
    assert out["verified_stored"] == "unreadable"
    assert out["unreadable_closed"]["closed"] is True
    assert out["sizing"]["clip_overrides"] == "unreadable -> all clips 0.0"
    assert out["sizing"]["per_fill_effective"], "the hardcoded exit set"
    assert set(out["sizing"]["per_fill_effective"].values()) == {0.0}

    up = _Everything(roster=STORED, clips=CLIPS)
    monkeypatch.setattr(app_mod, "get_pool", lambda: _pool_of(up))
    le._closed_read_at = 0.0
    out = asyncio.run(app_mod.api_gates())
    assert out["verified_source"] == "db"
    assert out["verified_effective"] == sorted(STORED)
    assert out["unreadable_closed"]["closed"] is False
    assert out["sizing"]["clip_overrides"] == CLIPS
    assert out["sizing"]["per_fill_effective"]["swisstony"] == 50.0


# ───────────────────── source pin: no path bypasses the closed state ──────────

def _functions_referencing(module_src: str, names: set[str]) -> dict[str, set[str]]:
    """{name: {enclosing function names}} for every load or store of
    each module-level name, walking async and sync defs alike."""
    tree = ast.parse(module_src)
    out: dict[str, set[str]] = {n: set() for n in names}

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            here = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                here = child.name
            if isinstance(child, ast.Name) and child.id in names:
                out[child.id].add(fn or "<module>")
            if isinstance(child, ast.Global):
                continue
            walk(child, here)

    walk(tree, None)
    return out


def test_no_order_path_reads_the_roster_or_clips_around_the_closed_state():
    src = inspect.getsource(le)
    refs = _functions_referencing(src, {
        "_roster_override", "_clip_override", "_ROSTER_DB_KEY", "_CLIPS_DB_KEY",
        "_OVERRIDE_READ_SQL"})
    # the stored roster is WRITTEN only by the two synchronous adopters
    # (the readers return values) and READ only by the accessors that
    # ask overrides_unreadable() first
    assert refs["_roster_override"] <= {
        "<module>", "_adopt_closed", "_adopt_open",
        "overrides_unreadable", "_whale_set"}, refs["_roster_override"]
    assert refs["_clip_override"] <= {
        "<module>", "_adopt_open", "exitable_whales", "per_fill_usd"}, \
        refs["_clip_override"]
    for fn in (le._refresh_roster, le._refresh_clips, le._adopt_open,
               le._adopt_closed):
        awaits = [ast.unparse(n.value)
                  for n in ast.walk(ast.parse(inspect.getsource(fn)))
                  if isinstance(n, ast.Await)]
        assert all(a.startswith("_read_override(") for a in awaits), \
            f"{fn.__name__}: the only await is the read itself ({awaits})"
    # the keys are read in exactly one place each, through the bounded SELECT
    assert refs["_ROSTER_DB_KEY"] == {"<module>", "_refresh_roster"}
    assert refs["_CLIPS_DB_KEY"] == {"<module>", "_refresh_clips"}
    assert refs["_OVERRIDE_READ_SQL"] == {"<module>", "_read_override"}
    assert "wait_for" in inspect.getsource(le._read_override)
    # every reader asks the closed state BEFORE it touches the value
    for fn in (le._whale_set, le.exitable_whales, le.per_fill_usd):
        s = inspect.getsource(fn)
        var = "_roster_override" if fn is le._whale_set else "_clip_override"
        assert s.index("overrides_unreadable()") < s.index(var), fn.__name__
    # the entry gate refuses on the closed state BEFORE the empty-set
    # rule can read it as a resume
    s = inspect.getsource(le._mapping_admitted)
    assert s.index("overrides_unreadable()") < s.index(
        '_whale_set("LIVE_VERIFIED_WHALES")')
    # the copy path sizes through per_fill_usd, never the map
    assert "per_fill_usd(" in inspect.getsource(le.volume_normalized_clip)


def test_the_gates_payload_reads_nothing_around_the_closed_state():
    from sportsassets.api import app as app_mod

    s = inspect.getsource(app_mod.api_gates)
    assert "unreadable_closed" in s and "unreadable -> all clips 0.0" in s
    for var in ("_le._roster_override", "_le._clip_override"):
        i = s.index(var)
        assert "_le.overrides_unreadable()" in s[max(0, i - 200):i], var
        assert s.count(var) == 1, var
