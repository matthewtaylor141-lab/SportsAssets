"""The live worker's ACTUAL ENTRY POINT, not a harness around it.

Every test here drives `bettor_live_loop.main()` or the loop object it
builds. The first activation review's point was precise: a harness
proving `ShadowLoop.restore` works says nothing about whether `main()`
calls it, and it did not.

The second review's points are pinned here too:

  * a store on ephemeral storage is REFUSED, and refusing stops the
    worker rather than degrading it;
  * records do not touch storage on the shared event loop;
  * a cancelled shutdown still writes its last batch;
  * settlement scheduling makes progress against a pending population;
  * discovery -> selection -> subscription -> decisions runs through
    `main()`, with an empty universe reported rather than bypassed.

Each class names the defect it pins.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import pytest

from sportsassets import bettor_live_store as store_mod
from sportsassets import bettor_market_stream as ms
from sportsassets import bettor_settlement_ingest as si
from sportsassets import bettor_live_control as ctl_mod
from sportsassets import bettor_universe_probe as probe_mod
from sportsassets.workers import bettor_live_loop as bl


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    """The enrichment pace is 0.25 req/s by default, which is the point
    of it. These tests are about what `main()` DECIDES, so they run
    unpaced; the pace itself is asserted in
    test_bettor_universe_probe.TestTheRateLimitIsEnforced."""
    monkeypatch.setenv(probe_mod.RPS_ENV, "100000")
    monkeypatch.setenv(probe_mod.CONC_ENV, "8")


def open_budget(*, max_distinct=40, seconds_left=1800.0, consumed=0):
    """A probe budget row with room and time left."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return json.dumps({
        "started_at": now.isoformat(),
        "deadline_at": (now + timedelta(seconds=seconds_left)).isoformat(),
        "max_distinct": max_distinct, "distinct_consumed": consumed})


class FakeControlPool:
    """`pool.fetchval` KEYED BY ingestion_state key, or a raise.

    Keyed, because `main()` now asks two different questions of the
    same table -- may we observe, and is there budget left -- and a
    double that answers both with one value cannot tell them apart.
    `execute` is recorded so a test can assert what was WRITTEN, which
    is how automatic shutdown is checked.
    """

    def __init__(self, value=None, *, raises=None, budget=None):
        self.value = value
        self.budget = open_budget() if budget is None else budget
        self.raises = raises
        self.calls = 0
        self.writes: list = []

    async def fetchval(self, _sql, *args):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        key = args[0] if args else None
        if key == ctl_mod.BUDGET_KEY:
            return self.budget
        return self.value

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        if "distinct_consumed" in sql:
            import json as _j
            b = _j.loads(self.budget) if isinstance(self.budget, str) \
                else dict(self.budget or {})
            b["distinct_consumed"] = b.get("distinct_consumed", 0) + args[1]
            self.budget = _j.dumps(b)
        elif args and args[0] == ctl_mod.CONTROL_KEY:
            self.value = "false"
        return "OK"


def RunningControl(**kw):
    """A control that says observation may run, with an open budget."""
    return FakeControlPool("true", **kw)


class SleepSpy:
    """An awaitable stand-in for `asyncio.sleep` that records the delays.

    The backoff SCHEDULE is the thing under test; spending it is not.
    """

    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


def md(slug="m1", *, source_ts, state="MARKET_STATE_OPEN"):
    return {"marketData": {
        "marketSlug": slug, "transactTime": source_ts, "state": state,
        "bids": [{"px": {"value": "0.4500"}, "qty": "40"},
                 {"px": {"value": "0.4400"}, "qty": "900"}],
        "offers": [{"px": {"value": "0.4700"}, "qty": "12"},
                   {"px": {"value": "0.4800"}, "qty": "3000"}],
        "stats": {"sharesTraded": "612.5"}}}


def fresh(d=0.0):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=d)).isoformat()


def filestore(tmp_path):
    return store_mod.FileStore(str(tmp_path), durable_across_redeploy=True)


def wired(tmp_path, slugs=("m1",), *, store=None, **kw):
    """The worker's own `build()`, wired to a durable store."""
    s = store if store is not None else filestore(tmp_path)
    asyncio.run(s.start())
    loop, stream = bl.build("k", "s", slugs=list(slugs),
                            leg_of={x: "yes" for x in slugs},
                            store=s, **kw)
    stream.epoch += 1
    stream.connected = True
    return loop, stream


def journal_lines(tmp_path):
    p = os.path.join(str(tmp_path), "decisions.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in open(p).read().strip().splitlines() if x]


# ── 1. durability ────────────────────────────────────────────────────

class TestEvidenceIsDurable:
    """`/var/tmp/bettor` passed every fsync and lost everything on the
    next redeploy. A default that cannot survive a deploy is not a
    durable default."""

    def test_the_worker_refuses_a_store_that_dies_on_redeploy(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("BETTOR_LIVE_STATE", "file")
        monkeypatch.setenv("BETTOR_LIVE_STATE_DIR", str(tmp_path))
        monkeypatch.delenv("BETTOR_LIVE_STATE_DISK", raising=False)
        monkeypatch.delenv(store_mod.ALLOW_EPHEMERAL_ENV, raising=False)
        assert store_mod.choose()["ok"] is False

    def test_no_ephemeral_path_is_a_default_any_more(self):
        """Checked over the CODE, not the prose: both modules describe
        the /var/tmp defect at length on purpose, and a grep over the
        whole source would make deleting the explanation the way to
        pass."""
        import ast
        import inspect
        for mod in (bl, store_mod):
            tree = ast.parse(inspect.getsource(mod))
            docs = set()
            for n in ast.walk(tree):
                if not isinstance(n, (ast.Module, ast.ClassDef,
                                      ast.FunctionDef,
                                      ast.AsyncFunctionDef)):
                    continue
                body = getattr(n, "body", None) or []
                if body and isinstance(body[0], ast.Expr) and isinstance(
                        getattr(body[0], "value", None), ast.Constant):
                    docs.add(id(body[0].value))
            for n in ast.walk(tree):
                if (isinstance(n, ast.Constant)
                        and isinstance(n.value, str)
                        and id(n) not in docs):
                    assert not n.value.startswith("/var/tmp"), (
                        "%s still carries an ephemeral path as a value"
                        % mod.__name__)
        assert not hasattr(bl, "DEFAULT_STATE_DIR")
        assert not hasattr(bl, "state_dir")

    def test_every_record_reaches_the_store_on_flush(self, tmp_path):
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        asyncio.run(loop.flush())
        rows = journal_lines(tmp_path)
        assert len(rows) == 1
        assert rows[0]["market_id"] == "m1"
        assert rows[0]["source_ts"] and rows[0]["received_at"]
        assert rows[0]["decided_at"]
        assert loop.records_written == 1
        assert loop.persist_failures == 0

    def test_deciding_does_no_io_at_all(self, tmp_path):
        """THE DEFECT: `_record` opened the journal and fsynced inline,
        inside `decide_slug`, on the event loop eighteen sibling worker
        loops share. The docstring claimed it ran in a thread."""
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        assert journal_lines(tmp_path) == [], "decide_slug wrote to disk"
        assert len(loop._outbox) == 1
        asyncio.run(loop.flush())
        assert len(journal_lines(tmp_path)) == 1

    def test_refusals_are_persisted_too(self, tmp_path):
        """An engine that stores only its trades cannot be evaluated."""
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-90)))   # stale
        loop.drain()
        asyncio.run(loop.flush())
        rec = journal_lines(tmp_path)[0]
        assert rec["status"] == "INELIGIBLE"
        assert rec["reasons"] == [ms.STALE_SOURCE]

    def test_a_persist_failure_is_counted_and_the_batch_is_kept(
            self, tmp_path):
        """A failed flush that dropped its batch would lose exactly the
        records the failure was about."""
        loop, stream = wired(tmp_path)
        loop.store.journal = str(tmp_path / "missing" / "d.jsonl")
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        asyncio.run(loop.flush())
        assert loop.persist_failures == 1
        assert loop.counters.get("persist_failed") == 1
        assert len(loop._outbox) == 1, "the failed batch was dropped"
        assert loop.report()["durability"]["persist_failures"] == 1

    def test_the_ledger_round_trips(self, tmp_path):
        loop, _ = wired(tmp_path, opening_cash=41.0)
        assert asyncio.run(loop.save_ledger()) is True
        loop2, _ = wired(tmp_path)
        out = asyncio.run(loop2.recover())
        assert out["ledger_restored"] is True
        assert loop2.shadow.ledger.cash == 41.0


class TestDurabilityIsReportedHonestly:
    """THE DEFECT: "at most two seconds lost" assumes every flush
    succeeds. Under a database outage the uncommitted interval grows
    until the outbox overflows, and then records are DROPPED."""

    class Outage:
        backend = "outage"
        durable_across = ("redeploy",)

        def __init__(self):
            self.up = False
            self.rows = []

        async def start(self):
            return {"ok": True, "backend": self.backend}

        async def flush(self, records, cursors):
            if not self.up:
                return {"records": 0, "cursors": 0, "failures": 1,
                        "error": "ConnectionDoesNotExistError"}
            self.rows.extend(records)
            return {"records": len(records), "cursors": len(cursors),
                    "failures": 0}

        async def save_ledger(self, snapshot):
            return self.up

        async def load(self):
            return {"cursors": {}, "ledger": None, "backend": self.backend}

        async def due_for_settlement(self, *, now, limit):
            return {"slugs": [], "cursors": {}, "outstanding": 0}

        async def outcome_report(self):
            return {}

        async def prune(self, **_):
            return {}

        async def close(self):
            return None

    def test_the_guarantee_names_its_own_precondition(self, tmp_path):
        loop, _ = wired(tmp_path)
        g = loop.durability_report()["guarantee"]
        assert "WHILE FLUSHES SUCCEED" in g
        assert "DROPPED" in g

    def test_the_sigterm_case_is_stated_rather_than_claimed_away(
            self, tmp_path):
        """MEASURED: Python's default SIGTERM disposition terminates
        without running finally blocks, and `workers/all.py` installs
        no handler. The final flush therefore does NOT run on a Render
        restart, and the report says so instead of implying it does."""
        loop, _ = wired(tmp_path)
        note = loop.durability_report()["on_sigterm"]
        assert "does NOT run" in note
        assert "oldest_uncommitted_age_s" in note

        here = os.path.dirname(os.path.abspath(__file__))
        allpy = open(os.path.normpath(os.path.join(
            here, "..", "sportsassets", "workers", "all.py"))).read()
        assert "SIGTERM" not in allpy, (
            "workers/all.py now handles SIGTERM -- the durability note "
            "in bettor_live_loop is stale and must be re-measured")

    def test_an_outage_grows_the_uncommitted_interval_visibly(self,
                                                              tmp_path):
        store = self.Outage()
        loop, stream = wired(tmp_path, store=store)
        for i in range(5):
            loop._record({"loop": "L", "kind": "DECISION",
                          "market_id": "m", "decided_at": "D%d" % i})
        loop._outbox[0]["enqueued_at"] = time.time() - 90.0
        out = asyncio.run(loop.flush())
        assert out["failures"] == 1

        d = loop.durability_report()
        assert d["uncommitted_records"] == 5, "the batch was kept"
        assert d["oldest_uncommitted_age_s"] > 89
        assert d["flush_failures"] == 1
        assert d["last_flush_error"] == "ConnectionDoesNotExistError"
        assert d["records_written"] == 0
        assert d["records_dropped"] == 0
        assert d["windows_incomplete"] is False

    def test_overflow_drops_records_and_marks_windows_incomplete(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_mod, "OUTBOX_MAX", 10)
        store = self.Outage()
        loop, _ = wired(tmp_path, store=store)
        for i in range(25):
            loop._record({"loop": "L", "kind": "DECISION",
                          "market_id": "m", "decided_at": "D%d" % i})
        d = loop.durability_report()
        assert d["uncommitted_records"] == 10
        assert d["records_dropped"] == 15
        assert d["windows_incomplete"] is True
        assert d["incomplete_from"] and d["incomplete_to"]
        assert loop.counters["record_dropped_outbox_full"] == 15

    def test_recovery_from_the_outage_commits_the_kept_batch(self,
                                                             tmp_path):
        store = self.Outage()
        loop, _ = wired(tmp_path, store=store)
        for i in range(4):
            loop._record({"loop": "L", "kind": "DECISION",
                          "market_id": "m", "decided_at": "D%d" % i})
        asyncio.run(loop.flush())
        store.up = True
        asyncio.run(loop.flush())
        d = loop.durability_report()
        assert d["records_written"] == 4
        assert d["uncommitted_records"] == 0
        assert d["seconds_since_last_commit"] is not None
        assert [r["decided_at"] for r in store.rows] == [
            "D0", "D1", "D2", "D3"], "the batch was reordered"

    def test_a_lost_acknowledgment_cannot_inflate_the_count(self,
                                                            tmp_path):
        """A commit can succeed and its acknowledgment be lost. The
        retry must be a no-op, not a second copy of every record."""
        loop, stream = wired(tmp_path)
        for i in range(6):
            stream._on_market_data(md("m%d" % i, source_ts=fresh(-1)))
        loop.drain()
        first = list(loop._outbox)
        asyncio.run(loop.flush())

        # The commit landed; the acknowledgment did not. The loop
        # replays exactly what it kept.
        for r in reversed(first):
            loop._outbox.appendleft(r)
        asyncio.run(loop.flush())

        keys = [r["record_key"] for r in journal_lines(tmp_path)]
        assert len(keys) == 12, "the file journal appends both copies"
        assert len(set(keys)) == 6, (
            "the two copies must be the SAME six keys, so a store that "
            "enforces the key collapses them")

    def test_every_record_carries_a_stable_content_key(self, tmp_path):
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        rec = loop.records[-1]
        assert rec["record_key"] == store_mod.record_key(rec)
        assert rec["record_key"] == store_mod.record_key(dict(rec))


# ── 2. recovery IN THE ENTRY POINT ───────────────────────────────────

class TestRecoveryHappensInTheWorker:

    def test_recovery_rebuilds_dedup_from_the_cursor_not_the_journal(
            self, tmp_path):
        loop, stream = wired(tmp_path)
        ts = fresh(-1)
        stream._on_market_data(md(source_ts=ts))
        loop.drain()
        asyncio.run(loop.flush())
        assert loop.counters.get("decided") == 1

        # A NEW PROCESS over the same store.
        loop2, stream2 = wired(tmp_path)
        out = asyncio.run(loop2.recover())
        assert out["cursors_recovered"] == 1
        stream2._on_market_data(md(source_ts=ts))       # same observation
        loop2.drain()
        assert loop2.counters.get("decided") is None
        assert loop2.counters.get("duplicate_observation_refused") == 1

    def test_recovery_cost_does_not_grow_with_the_record_count(
            self, tmp_path):
        """O(cursors), not O(journal). A month of evidence must cost
        nothing to restart against."""
        loop, stream = wired(tmp_path)
        loop._record({"market_id": "m1", "kind": "DECISION"})
        for i in range(4000):
            loop._record({"market_id": "m1", "kind": "DECISION", "i": i})
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        asyncio.run(loop.flush())
        assert len(journal_lines(tmp_path)) > 4000

        loop2, _ = wired(tmp_path)
        out = asyncio.run(loop2.recover())
        assert out["cursors_recovered"] == 1
        assert out["recover_seconds"] < 1.0

    def test_a_corrupt_ledger_is_counted_and_the_loop_still_runs(
            self, tmp_path):
        s = filestore(tmp_path)
        asyncio.run(s.start())
        open(s.ledger_path, "w").write("{not json")
        loop, _ = wired(tmp_path, store=s)
        out = asyncio.run(loop.recover())
        assert out["ledger_restored"] is False
        assert "ledger_error" in out
        assert loop.counters.get("ledger_restore_failed") == 1

    def test_a_newer_observation_after_recovery_is_decided(self, tmp_path):
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-3)))
        loop.drain()
        asyncio.run(loop.flush())
        loop2, stream2 = wired(tmp_path)
        asyncio.run(loop2.recover())
        stream2._on_market_data(md(source_ts=fresh(-1)))   # strictly newer
        loop2.drain()
        assert loop2.counters.get("decided") == 1

    def test_the_settlement_schedule_survives_the_restart(self, tmp_path):
        """A restart that forgot which contracts had resolved would read
        every settled market again on every deploy."""
        loop, stream = wired(tmp_path, slugs=("m1",))
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        loop.cursors["m1"].update(
            settle_status=store_mod.SETTLE_RESOLVED, settle_attempts=1,
            settle_next_at=None, settle_last_at=time.time())
        loop._cursor_dirty.add("m1")
        asyncio.run(loop.flush())

        loop2, _ = wired(tmp_path)
        out = asyncio.run(loop2.recover())
        assert out["settlements_completed_at_recovery"] == 1
        assert asyncio.run(loop2.settlement_due(limit=25))["slugs"] == []

    def test_an_unresolved_obligation_survives_the_restart_too(self,
                                                               tmp_path):
        """The case the age-based prune used to delete: a market that
        has been open a long time is the one retention exists for."""
        loop, stream = wired(tmp_path, slugs=("m1",))
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        loop.cursors["m1"].update(
            settle_status=store_mod.SETTLE_RESOLVED_DERIVED,
            settle_derived_outcome="1.0000", settle_derived_at=1.0,
            settle_attempts=9, settle_next_at=1.0)
        loop._cursor_dirty.add("m1")
        asyncio.run(loop.flush())
        asyncio.run(loop.prune())

        loop2, _ = wired(tmp_path)
        asyncio.run(loop2.recover())
        due = asyncio.run(loop2.settlement_due(limit=25))
        assert due["slugs"] == ["m1"], (
            "a derived outcome must stay queued for the authoritative "
            "read that can confirm it")
        assert loop2.cursors["m1"]["settle_derived_outcome"] == "1.0000"


# ── 3. bounded collections ───────────────────────────────────────────

class TestCollectionsAreBounded:
    """sportsassets-workers was OOM-killed thirteen times in one
    evening at 2 GiB. Unbounded growth is not an abstract risk here."""

    def test_the_record_ring_is_bounded(self, tmp_path):
        loop, _ = wired(tmp_path)
        loop.records.extend({"i": i} for i in range(bl.RECORD_RING + 500))
        assert len(loop.records) == bl.RECORD_RING

    def test_the_touch_ring_is_bounded(self, tmp_path):
        loop, _ = wired(tmp_path)
        for i in range(bl.TOUCH_RING + 100):
            loop.on_trade({"i": i})
        assert len(loop.touches) == bl.TOUCH_RING
        # The COUNT is exact even though the ring is not.
        assert loop.counters["trades_seen"] == bl.TOUCH_RING + 100

    def test_the_outbox_is_bounded_and_overflow_is_named(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.setattr(store_mod, "OUTBOX_MAX", 50)
        loop, _ = wired(tmp_path)
        for i in range(120):
            loop._record({"i": i, "market_id": "m"})
        assert len(loop._outbox) == 50
        assert loop.counters["record_dropped_outbox_full"] == 70

    def test_the_cursor_cache_is_bounded_and_a_miss_is_named(
            self, tmp_path, monkeypatch):
        """The cache is bounded; the SCHEDULE is not, because it lives
        in the store. An eviction is a cache miss, not lost work."""
        monkeypatch.setattr(store_mod, "CURSOR_MAX_IN_MEMORY", 10)
        loop, _ = wired(tmp_path)
        for i in range(25):
            loop._cursor("m%03d" % i)
            loop._cursor_dirty.discard("m%03d" % i)
        assert len(loop.cursors) == 10
        assert loop.counters["cursor_evicted"] == 15
        assert loop.counters["cursor_cache_miss_unresolved"] == 15

    def test_an_unflushed_cursor_is_never_evicted(self, tmp_path,
                                                  monkeypatch):
        """Dropping a cached row loses nothing. Dropping an UNWRITTEN
        update loses the update."""
        monkeypatch.setattr(store_mod, "CURSOR_MAX_IN_MEMORY", 5)
        loop, _ = wired(tmp_path)
        for i in range(20):
            loop._cursor("m%03d" % i)          # every one left dirty
        assert len(loop.cursors) == 20, "an unflushed cursor was evicted"
        assert loop.counters["cursor_kept_unflushed"] > 0

    def test_dedup_keeps_one_cursor_per_slug_not_every_observation(
            self, tmp_path):
        loop, stream = wired(tmp_path, slugs=("m1",))
        for i in range(50):
            # Inside the 10 s bound, strictly increasing.
            stream._on_market_data(md(source_ts=fresh(-9 + i * 0.1)))
            loop.drain()
        assert len(loop.cursors) == 1
        assert loop.counters.get("decided") == 50

    def test_an_out_of_order_redelivery_is_refused(self, tmp_path):
        """Stricter than a seen-set: an OLD observation arriving after
        a newer one is refused, not decided."""
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        stream._on_market_data(md(source_ts=fresh(-5)))
        loop.drain()
        assert loop.counters.get("duplicate_observation_refused") == 1


# ── 4. exactly-once trades ───────────────────────────────────────────

class TestTradesAreCountedExactlyOnce:
    """build() installed on_trade AND main() drained into the same
    handler. One trade, trades_seen=2. Reproduced before the fix."""

    def test_build_does_not_install_a_trade_callback(self, tmp_path):
        loop, stream = wired(tmp_path)
        assert stream._trade_cb is None
        assert stream._book_cb == loop.on_book

    def test_one_delivered_trade_is_counted_once(self, tmp_path):
        loop, stream = wired(tmp_path)
        stream._on_trade({"trade": {"marketSlug": "m1",
                                    "price": {"value": "0.45"},
                                    "quantity": {"value": "10"},
                                    "tradeTime": "t",
                                    "maker": {}, "taker": {}}})
        for t in stream.drain_trades():
            loop.on_trade(t)
        assert loop.counters["trades_seen"] == 1
        assert len(loop.touches) == 1
        assert loop.report()["touches_not_fills"]["trades_observed"] == 1

    def test_draining_twice_does_not_recount(self, tmp_path):
        loop, stream = wired(tmp_path)
        stream._on_trade({"trade": {"marketSlug": "m1",
                                    "price": {"value": "0.45"},
                                    "quantity": {"value": "10"},
                                    "tradeTime": "t",
                                    "maker": {}, "taker": {}}})
        for _ in range(3):
            for t in stream.drain_trades():
                loop.on_trade(t)
        assert loop.counters["trades_seen"] == 1


# ── 5. thread safety ─────────────────────────────────────────────────

class TestTheDirtySetIsSynchronized:

    def test_concurrent_marking_and_taking_loses_nothing(self, tmp_path):
        loop, _ = wired(tmp_path)
        n, taken = 4000, []
        stop = threading.Event()

        def producer():
            for i in range(n):
                loop.on_book("m%d" % i, {})
            stop.set()

        def consumer():
            while not stop.is_set() or loop._dirty:
                taken.extend(loop.take_dirty())

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start(); t2.start(); t1.join(20); t2.join(20)
        assert len(set(taken)) == n, "a marked slug was dropped"

    def test_take_dirty_clears_under_the_lock(self, tmp_path):
        loop, _ = wired(tmp_path)
        loop.on_book("a", {})
        assert loop.take_dirty() == ["a"]
        assert loop.take_dirty() == []


# ── 6. settlement scheduling in the worker ───────────────────────────

class TestSettlementSchedulingInTheLoop:
    """THE DEFECTS: "the 25 oldest observed" cannot make progress; a
    derived outcome was retired as though authoritative; and a healthy
    open market was abandoned after twelve checks."""

    def observed(self, tmp_path, n):
        loop, stream = wired(tmp_path, slugs=tuple("m%03d" % i
                                                   for i in range(n)))
        for i in range(n):
            stream._on_market_data(md("m%03d" % i, source_ts=fresh(-1)))
        loop.drain()
        asyncio.run(loop.flush())
        assert len(loop.cursors) == n
        return loop

    def reader_all(self, status):
        def reader(_c, slug):
            return {"status": status, "error": "TimeoutError"}
        return reader

    async def run_pass(self, loop, status, now):
        q = await loop.settlement_due(now=now)
        slugs = q["slugs"]
        out = await si.ingest([("o:%s" % s, s) for s in slugs],
                              reader=self.reader_all(status),
                              writer=loop._settlement_writer)
        for rec in out["detail"]:
            c = loop.cursors[rec["slug"]]
            nxt = store_mod.settle_transition(
                rec["status"], c["settle_attempts"], now=now,
                failures=c["settle_failures"] or 0)
            nxt.pop("retired")
            c.update(nxt)
            loop._cursor_dirty.add(rec["slug"])
        await loop.flush()
        return {"contracts_read": len(slugs), "slugs": slugs}

    def test_a_pending_population_does_not_hold_the_batch(self, tmp_path):
        loop = self.observed(tmp_path, 60)
        t = time.time()
        first = asyncio.run(self.run_pass(loop, si.PENDING, t))
        second = asyncio.run(self.run_pass(loop, si.PENDING, t + 1))
        assert first["contracts_read"] == bl.SETTLEMENT_BATCH
        assert second["contracts_read"] == bl.SETTLEMENT_BATCH
        assert not (set(first["slugs"]) & set(second["slugs"]))

    def test_a_newly_observed_contract_is_read_next_pass(self, tmp_path):
        loop = self.observed(tmp_path, bl.SETTLEMENT_BATCH)
        t = time.time()
        asyncio.run(self.run_pass(loop, si.PENDING, t))
        loop._cursor("brand-new")
        asyncio.run(loop.flush())
        due = asyncio.run(loop.settlement_due(now=t + 1))
        assert due["slugs"][0] == "brand-new"

    def test_the_queue_is_read_from_the_store_not_the_cache(self, tmp_path):
        """A market must not stop being checked because the process was
        busy enough to evict it from memory."""
        loop = self.observed(tmp_path, 8)
        loop.cursors.clear()                       # the cache, emptied
        due = asyncio.run(loop.settlement_due(now=time.time()))
        assert len(due["slugs"]) == 8
        assert due["outstanding"] == 8
        assert len(loop.cursors) == 8, "the rows come back from the store"

    def test_the_loop_reads_and_reschedules_through_its_own_method(
            self, tmp_path):
        loop = self.observed(tmp_path, 4)
        statuses = {"m000": si.RESOLVED, "m001": si.PENDING,
                    "m002": si.UNREADABLE, "m003": si.UNMATCHED}
        payload = {si.RESOLVED: {"status": si.RESOLVED, "outcome": "1.0000",
                                 "settled_at": "2026-09-21T02:00:00Z"},
                   si.PENDING: {"status": si.PENDING},
                   si.UNREADABLE: {"status": si.UNREADABLE,
                                   "error": "TimeoutError"},
                   si.UNMATCHED: {"status": si.UNMATCHED}}
        import sportsassets.bettor_live_read as live
        orig = live.read_resolution
        live.read_resolution = lambda _c, slug: payload[statuses[slug]]
        try:
            out = asyncio.run(loop.ingest_settlements(now=1000.0))
        finally:
            live.read_resolution = orig

        assert out["counts"][si.RESOLVED] == 1
        assert out["counts"][si.INGESTED] == 1
        assert out["reconciled"] is True
        assert out["completed_authoritatively"] == 1
        assert loop.cursors["m000"]["settle_next_at"] is None
        # A PENDING answer is a successful read: it schedules forward
        # and NEVER advances the failure count.
        assert loop.cursors["m001"]["settle_next_at"] > 1000.0
        assert loop.cursors["m001"]["settle_failures"] == 0
        # Failed reads do.
        assert loop.cursors["m002"]["settle_failures"] == 1
        assert loop.cursors["m003"]["settle_failures"] == 1
        assert loop._cursor_dirty >= {"m000", "m001", "m002", "m003"}

    def test_a_derived_outcome_does_not_complete_collection(self, tmp_path):
        """THE DEFECT: RESOLVED_DERIVED was retired alongside RESOLVED,
        so an inference from a price closed the contract and the
        venue's own endpoint could never confirm it."""
        loop = self.observed(tmp_path, 1)
        import sportsassets.bettor_live_read as live
        orig = live.read_resolution
        live.read_resolution = lambda _c, slug: {
            "status": si.RESOLVED_DERIVED, "outcome": "1.0000"}
        try:
            out = asyncio.run(loop.ingest_settlements(now=1000.0))
        finally:
            live.read_resolution = orig

        assert out["completed_authoritatively"] == 0
        assert out["awaiting_authoritative"] == 1
        c = loop.cursors["m000"]
        assert c["settle_status"] == store_mod.SETTLE_RESOLVED_DERIVED
        assert c["settle_derived_outcome"] == "1.0000"
        assert c["settle_outcome"] is None, (
            "a derived outcome must never be written to the "
            "authoritative field")
        assert c["settle_next_at"] > 1000.0
        assert store_mod.has_outstanding_obligation(c) is True

    def test_a_long_open_market_is_never_abandoned(self, tmp_path):
        """A market may legitimately stay open far longer than any
        attempt count. The first fix abandoned it at twelve."""
        loop = self.observed(tmp_path, 1)
        import sportsassets.bettor_live_read as live
        orig = live.read_resolution
        live.read_resolution = lambda _c, slug: {"status": si.PENDING}
        t = 1000.0
        try:
            for _ in range(30):
                asyncio.run(loop.ingest_settlements(now=t))
                t = max(t + 1.0, loop.cursors["m000"]["settle_next_at"])
        finally:
            live.read_resolution = orig
        c = loop.cursors["m000"]
        assert c["settle_status"] == store_mod.SETTLE_PENDING
        assert c["settle_attempts"] == 30
        assert c["settle_failures"] == 0
        assert store_mod.has_outstanding_obligation(c) is True

    def test_a_run_of_failed_reads_escalates_and_stays_queued(self,
                                                              tmp_path):
        loop = self.observed(tmp_path, 1)
        import sportsassets.bettor_live_read as live
        orig = live.read_resolution
        live.read_resolution = lambda _c, slug: {
            "status": si.UNREADABLE, "error": "TimeoutError"}
        t = 1000.0
        try:
            for _ in range(store_mod.READ_FAILURE_ESCALATE_AT):
                out = asyncio.run(loop.ingest_settlements(now=t))
                t = max(t + 1.0, loop.cursors["m000"]["settle_next_at"])
        finally:
            live.read_resolution = orig
        c = loop.cursors["m000"]
        assert c["settle_status"] == store_mod.SETTLE_READ_ESCALATED
        assert out["read_escalated"] == 1
        assert loop.counters["settlement_read_escalated"] >= 1
        # STILL QUEUED. Dropping it would lose an obligation quietly.
        due = asyncio.run(loop.settlement_due(now=c["settle_next_at"] + 1))
        assert due["slugs"] == ["m000"]

    def test_an_authoritative_resolution_is_never_read_again(self,
                                                             tmp_path):
        loop = self.observed(tmp_path, 1)
        c = loop.cursors["m000"]
        t = store_mod.settle_transition(si.RESOLVED, 0, now=0.0)
        t.pop("retired")
        c.update(t)
        loop._cursor_dirty.add("m000")
        asyncio.run(loop.flush())
        due = asyncio.run(loop.settlement_due(now=10 ** 12))
        assert due["slugs"] == []

    def test_a_settlement_row_is_journalled_not_written_to_accounting(
            self, tmp_path):
        loop, _ = wired(tmp_path)
        asyncio.run(loop._settlement_writer({"OBSERVATION_ID": "o1",
                                             "SETTLEMENT_OUTCOME": "1.0"}))
        asyncio.run(loop.flush())
        rows = journal_lines(tmp_path)
        assert rows[0]["kind"] == "SETTLEMENT"
        assert rows[0]["settlement"]["OBSERVATION_ID"] == "o1"

    def test_the_batch_is_bounded(self):
        assert bl.SETTLEMENT_BATCH <= 50
        assert bl.SETTLEMENT_EVERY_S >= 60

    def test_with_nothing_observed_it_does_not_read(self, tmp_path):
        loop, _ = wired(tmp_path)
        out = asyncio.run(loop.ingest_settlements())
        assert out["counts"] == {}
        assert "skipped" in out

    def test_the_loop_writes_only_its_own_tables(self):
        assert store_mod.OWN_TABLES == ("bettor_live_journal",
                                        "bettor_live_cursor",
                                        "bettor_live_ledger")
        assert bl.describe()["writes_accounting"] is False
        assert "bettor_state_settlements" not in bl.describe()[
            "writes_database"]

    def test_the_schedule_is_reported_with_its_scope(self, tmp_path):
        loop = self.observed(tmp_path, 3)
        rep = loop.report()["settlement"]["schedule"]
        assert "cache" in rep["scope"]
        assert rep["contracts_cached"] == 3
        assert rep["by_status"]["NEVER_ATTEMPTED"] == 3
        assert rep["outstanding"] == 3
        assert rep["completed_authoritatively"] == 0
        assert rep["awaiting_authoritative"] == 0


# ── 7. lifecycle in main() ───────────────────────────────────────────

class TestTheEntryPointLifecycle:

    def _patch(self, monkeypatch, tmp_path, *, discovery, creds=("k", "s")):
        class Cfg:
            pmus_key_id, pmus_secret_key = creds

        import sportsassets.config as cfgmod
        monkeypatch.setattr(cfgmod, "settings", lambda: Cfg(), raising=False)

        async def fake_discover(client=None, **kw):
            return discovery

        monkeypatch.setattr(bl, "_discover", fake_discover)
        made = {}
        real_build = bl.build          # captured BEFORE the patch, or
                                       # fake_build calls itself

        def fake_build(*a, **kw):
            loop, stream = real_build(*a, **kw)
            stream.start = lambda: made.setdefault("started", True)
            stream.stop = lambda: made.setdefault("stopped", True)
            made["loop"], made["stream"] = loop, stream
            return loop, stream

        monkeypatch.setattr(bl, "build", fake_build)
        made["store"] = filestore(tmp_path)
        return made

    def run_main(self, made, **kw):
        # THE CONTROL IS NOW PART OF STARTING. `main()` reads the
        # database stop control before anything else and fails closed,
        # so a test that wants to reach discovery has to supply a
        # control that says run -- exactly as production will.
        # `sleep` is a spy so the non-start backoff is asserted rather
        # than served: these cases return in milliseconds and the
        # schedule is checked in TestTheNoStartBackoff.
        kw.setdefault("control_pool", RunningControl())
        kw.setdefault("sleep", made.setdefault("sleep", SleepSpy()))
        bl._reset_backoff()
        return asyncio.run(bl.main(store=made["store"], **kw))

    def test_a_failed_discovery_refuses_to_start(self, monkeypatch, tmp_path):
        """It used to log and run forever over an empty universe,
        reporting zero decisions as though the market were quiet."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": False, "error": "TimeoutError",
                                      "why": "market discovery failed"})
        out = self.run_main(made)
        assert out["started"] is False and out["why"] == "DISCOVERY_FAILED"
        assert "started" not in made

    def test_an_empty_universe_is_reported_not_bypassed(self, monkeypatch,
                                                        tmp_path):
        """The rule is not relaxed to find something to watch."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": [],
                                      "considered": 400, "pages_read": 6,
                                      "excluded_by_reason":
                                          {"TRADED_VOLUME_BELOW_MIN": 400}})
        out = self.run_main(made)
        assert out["why"] == "EMPTY_UNIVERSE"
        assert out["considered"] == 400
        assert out["excluded_by_reason"]["TRADED_VOLUME_BELOW_MIN"] == 400
        assert out["pages_read"] == 6
        assert "started" not in made

    def test_missing_credentials_refuse_to_start(self, monkeypatch, tmp_path):
        """A stream that cannot authenticate produces no books, and
        'no books' must never look like 'a quiet market'."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"]},
                           creds=(None, None))
        out = self.run_main(made)
        assert out["why"] == "NO_CREDENTIALS"
        assert "started" not in made

    def test_no_durable_store_refuses_to_start(self, monkeypatch, tmp_path):
        """A run whose evidence dies on the next deploy produced
        nothing, so it does not begin."""
        self._patch(monkeypatch, tmp_path,
                    discovery={"ok": True, "slugs": ["m1"]})
        monkeypatch.setenv("BETTOR_LIVE_STATE", "file")
        monkeypatch.setenv("BETTOR_LIVE_STATE_DIR", str(tmp_path))
        monkeypatch.delenv("BETTOR_LIVE_STATE_DISK", raising=False)
        monkeypatch.delenv(store_mod.ALLOW_EPHEMERAL_ENV, raising=False)
        out = asyncio.run(bl.main(control_pool=RunningControl(),
                                  sleep=SleepSpy()))   # no store
        assert out["why"] == "NO_DURABLE_STORE"

    def test_a_store_that_will_not_start_refuses_the_run(self, monkeypatch,
                                                         tmp_path):
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"]})

        class Broken:
            backend = "broken"

            async def start(self):
                raise OSError("read-only file system")

        out = asyncio.run(bl.main(store=Broken(),
                                  control_pool=RunningControl(),
                                  sleep=SleepSpy()))
        assert out["why"] == "STORE_START_FAILED"
        assert out["detail"] == "OSError"
        assert "started" not in made

    def test_cancellation_still_stops_the_stream(self, monkeypatch, tmp_path):
        """workers/all.py shuts a loop down by CANCELLING it. Without a
        finally, the socket thread outlives the coroutine."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1})

        async def run():
            task = asyncio.create_task(bl.main(
                store=made["store"], control_pool=RunningControl(),
                sleep=SleepSpy()))
            await asyncio.sleep(0.4)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(run())
        assert made.get("started") is True
        assert made.get("stopped") is True, "cancellation orphaned the stream"

    def test_cancellation_still_writes_the_last_batch(self, monkeypatch,
                                                      tmp_path):
        """A `finally` that awaits inside an already-cancelled task has
        its first await re-raise, so the batch the shutdown existed to
        save is exactly the batch that is lost."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1})

        async def run():
            task = asyncio.create_task(bl.main(
                store=made["store"], control_pool=RunningControl(),
                sleep=SleepSpy()))
            await asyncio.sleep(0.3)
            loop = made["loop"]
            loop._record({"loop": "L", "kind": "DECISION",
                          "market_id": "last-batch"})
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(run())
        rows = journal_lines(tmp_path)
        assert any(r.get("market_id") == "last-batch" for r in rows), (
            "the shutdown flush was cancelled with the batch still in "
            "the outbox")

    def test_the_kill_switch_stops_it_and_the_stream(self, monkeypatch,
                                                    tmp_path):
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1})

        async def run():
            task = asyncio.create_task(bl.main(
                store=made["store"], control_pool=RunningControl(),
                sleep=SleepSpy()))
            await asyncio.sleep(0.4)
            os.environ[bl.KILL_ENV] = "off"
            await asyncio.wait_for(task, timeout=10)
            os.environ.pop(bl.KILL_ENV, None)

        asyncio.run(run())
        assert made.get("stopped") is True

    def test_main_recovers_before_it_streams(self, monkeypatch, tmp_path):
        """The harness proved ShadowLoop.restore works. This proves
        main() calls it."""
        s = filestore(tmp_path)
        asyncio.run(s.start())
        asyncio.run(s.flush([], {"m1": {"first_seen_at": 1.0,
                                        "last_seen_at": 1.0,
                                        "last_source_ts":
                                            "2026-09-21T00:00:00Z",
                                        "last_decided_at": None,
                                        "settle_status": None,
                                        "settle_attempts": 0,
                                        "settle_next_at": None,
                                        "settle_last_at": None}}))
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1})
        made["store"] = filestore(tmp_path)

        async def run():
            task = asyncio.create_task(bl.main(
                store=made["store"], control_pool=RunningControl(),
                sleep=SleepSpy()))
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        loop = made["loop"]
        assert loop.recovered is not None
        assert loop.recovered["cursors_recovered"] == 1
        assert loop.cursors["m1"]["last_source_ts"] == "2026-09-21T00:00:00Z"

    def test_run_for_s_returns_a_report_rather_than_running_forever(
            self, monkeypatch, tmp_path):
        """The seam the startup-path runner uses. Production passes
        nothing and the loop runs until cancelled."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1, "pages_read": 3})
        out = self.run_main(made, run_for_s=0.1)
        assert out["started"] is True
        assert out["report"]["orders_submitted"] == 0
        assert out["report"]["discovery"]["pages_read"] == 3

    def test_production_passes_no_seams(self):
        """`workers/all.py` calls `main()` with no arguments, so every
        seam defaults to the production path."""
        import inspect
        sig = inspect.signature(bl.main)
        assert all(p.default is None for p in sig.parameters.values())
        here = os.path.dirname(os.path.abspath(__file__))
        allpy = open(os.path.normpath(os.path.join(
            here, "..", "sportsassets", "workers", "all.py"))).read()
        # ON THE DEREGISTRATION BRANCH the entry is commented out. The
        # seams must still default to the production path either way,
        # which is what this test is actually about.
        assert ("bettor_live_loop.main)" in allpy
                or "# (\"bettor_live\", bettor_live_loop.main)," in allpy)


# ── 8. the worker sizes nothing ──────────────────────────────────────

class TestNoOrderCapability:

    def test_max_contracts_defaults_to_zero(self):
        import inspect
        assert inspect.signature(
            bl.LiveLoop.__init__).parameters["max_contracts"].default == 0.0
        assert bl.describe()["default_max_contracts"] == 0.0

    def test_no_order_call_is_reachable(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(bl))
        called = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
        for forbidden in ("submit_fok", "close_position", "submit",
                          "place_order", "cancel_order", "authorize"):
            assert forbidden not in called

    def test_its_import_closure_reaches_no_order_path(self):
        """The precise dependency question the release asks: the worker
        needs `pmus._get_client` for the market listing and nothing
        else, so the execution gate is NOT a dependency of it."""
        import ast
        import os as _os
        root = _os.path.normpath(_os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "..",
            "sportsassets"))

        def path_of(mod):
            p = _os.path.join(root, *mod.split(".")) + ".py"
            return p if _os.path.exists(p) else None

        seen, queue = set(), ["workers.bettor_live_loop"]
        while queue:
            m = queue.pop()
            if m in seen:
                continue
            p = path_of(m)
            if p is None:
                continue
            seen.add(m)
            tree = ast.parse(open(p).read())
            for n in tree.body:            # TOP-LEVEL imports only
                if isinstance(n, ast.ImportFrom) and n.level:
                    base = m.split(".")[:-1]
                    base = base[:len(base) - (n.level - 1)] if n.level > 1 \
                        else base
                    pre = ".".join(base)
                    for a in n.names:
                        queue.append((pre + "." + a.name).lstrip("."))
        assert "execution_gate" not in seen
        assert "pmus" not in seen
        assert "live_executor" not in seen

    def test_it_is_registered_in_workers_all_with_the_kill_switch(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.normpath(
            os.path.join(here, "..", "sportsassets", "workers", "all.py"))
        text = open(path).read()
        assert ('("bettor_live", bettor_live_loop.main)' in text
                or '# ("bettor_live", bettor_live_loop.main),' in text)
        assert "BETTOR_LIVE_LOOP=off" in text

    def test_the_report_states_zero_orders(self, tmp_path):
        loop, _ = wired(tmp_path)
        assert loop.report()["orders_submitted"] == 0


# ── 9. the no-start backoff ──────────────────────────────────────────

class TestTheNoStartBackoff:
    """THE DEFECT: `workers/all.py:run_forever` is a `while True`, so a
    `main()` that returned immediately became a five-second cycle. On
    2026-09-21 that was about 73 rounds of six listing pages and one
    advisory-locked DDL transaction each -- roughly 440 venue requests
    -- in the sixteen minutes before the loop was deregistered.

    That supervisor is shared by twenty-five workers and is not this
    release's to change, so the bound lives in `main()`."""

    def test_the_schedule_escalates_and_then_holds(self):
        bl._reset_backoff()
        spy = SleepSpy()

        async def drive():
            for _ in range(6):
                await bl._backoff("EMPTY_UNIVERSE", sleep=spy)

        asyncio.run(drive())
        assert spy.delays == [300.0, 900.0, 1800.0, 3600.0, 3600.0,
                              3600.0]
        assert spy.delays[-1] == max(bl.NOSTART_BACKOFF_S), "capped"

    def test_the_first_hold_covers_the_cost_of_the_refusal_itself(self):
        """A refusal is not free: an EMPTY_UNIVERSE return has already
        spent six listing pages and up to PROBE_BATCH * rounds BBO
        reads. The hold has to be long enough that retrying is not a
        hot loop wearing a backoff."""
        from sportsassets import bettor_universe_probe as probe
        worst_reads = 6 + probe.PROBE_BATCH * probe.PROBE_ROUNDS_AT_START
        hold = min(bl.NOSTART_BACKOFF_S)
        assert hold >= 300.0
        assert worst_reads / hold < 6.0, (
            "%d reads per %.0fs is %.1f req/s"
            % (worst_reads, hold, worst_reads / hold))

    def test_a_run_that_started_clears_the_backoff(self, monkeypatch,
                                                   tmp_path):
        """Otherwise one bad morning leaves every later restart on the
        fifteen-minute rung."""
        bl._reset_backoff()
        asyncio.run(bl._backoff("x", sleep=SleepSpy()))
        asyncio.run(bl._backoff("x", sleep=SleepSpy()))
        assert bl._nostart_rung > 0

        made = self._started(monkeypatch, tmp_path)
        spy = SleepSpy()
        asyncio.run(bl.main(store=made["store"], run_for_s=0.0,
                            control_pool=RunningControl(), sleep=spy))
        assert bl._nostart_rung == 0
        assert spy.delays == [], "a run that started served no backoff"

    def test_every_refusal_holds_before_it_returns(self, monkeypatch,
                                                   tmp_path):
        """Each of these used to return in microseconds, straight back
        into the supervisor's five-second restart."""
        for why, kw in (
            ("DISCOVERY_FAILED", {"discovery": {"ok": False,
                                                "error": "TimeoutError"}}),
            ("EMPTY_UNIVERSE", {"discovery": {"ok": True, "slugs": [],
                                              "considered": 900,
                                              "coverage": {}}}),
            ("NO_CREDENTIALS", {"discovery": {"ok": True, "slugs": ["m1"]},
                                "creds": (None, None)}),
        ):
            bl._reset_backoff()
            made = self._patch_for(monkeypatch, tmp_path, **kw)
            spy = SleepSpy()
            out = asyncio.run(bl.main(store=made["store"],
                                      control_pool=RunningControl(),
                                      sleep=spy))
            assert out["why"] == why
            assert spy.delays == [300.0], (
                "%s returned without holding" % why)

    # -- helpers ------------------------------------------------------

    def _patch_for(self, monkeypatch, tmp_path, *, discovery,
                   creds=("k", "s")):
        return TestTheEntryPointLifecycle()._patch(
            monkeypatch, tmp_path, discovery=discovery, creds=creds)

    def _started(self, monkeypatch, tmp_path):
        return self._patch_for(
            monkeypatch, tmp_path,
            discovery={"ok": True, "slugs": ["m1"], "considered": 1,
                       "detail": [{"slug": "m1", "outcome_leg": "yes"}],
                       "coverage": {"candidates": 1, "probed": 1,
                                    "enriched": 1}})


# ── 10. the database stop control, through main() ────────────────────

class TestTheStopControlGovernsTheLoop:
    """THE DEFECT: BETTOR_LIVE_LOOP=off was set and acknowledged, the
    service was demonstrably restarted (`server_restarted`
    2026-09-21T22:43:40.131647Z), and the restarted process still ran
    the discovery path. The stop had to stop depending on the
    environment."""

    def _spying(self, monkeypatch, tmp_path):
        made = TestTheEntryPointLifecycle()._patch(
            monkeypatch, tmp_path,
            discovery={"ok": True, "slugs": ["m1"], "considered": 1,
                       "detail": [{"slug": "m1", "outcome_leg": "yes"}],
                       "coverage": {}})
        calls = {"discover": 0, "store_start": 0}
        real_discover = bl._discover

        async def counting(*a, **kw):
            calls["discover"] += 1
            return await real_discover(*a, **kw)

        monkeypatch.setattr(bl, "_discover", counting)
        store = made["store"]
        real_start = store.start

        async def counting_start():
            calls["store_start"] += 1
            return await real_start()

        store.start = counting_start
        return made, calls

    def test_a_closed_control_costs_nothing_at_all(self, monkeypatch,
                                                   tmp_path):
        """No venue request, no schema initialization, no credential
        read. A loop that is stopped must be free."""
        made, calls = self._spying(monkeypatch, tmp_path)
        out = asyncio.run(bl.main(store=made["store"],
                                  control_pool=FakeControlPool("false"),
                                  sleep=SleepSpy()))
        assert out["started"] is False
        assert out["why"] == "STOPPED_BY_CONTROL"
        assert calls == {"discover": 0, "store_start": 0}
        assert "started" not in made, "the stream never opened"

    @pytest.mark.parametrize("pool,why", [
        (FakeControlPool(None), "CONTROL_ROW_ABSENT"),
        (FakeControlPool("maybe"), "CONTROL_MALFORMED"),
        (FakeControlPool(raises=ConnectionError("down")),
         "CONTROL_UNREADABLE"),
    ])
    def test_it_fails_closed_through_main(self, monkeypatch, tmp_path,
                                          pool, why):
        made, calls = self._spying(monkeypatch, tmp_path)
        out = asyncio.run(bl.main(store=made["store"], control_pool=pool,
                                  sleep=SleepSpy()))
        assert out["started"] is False and out["why"] == why
        assert calls["discover"] == 0 and calls["store_start"] == 0

    def test_a_closed_control_also_serves_the_backoff(self, monkeypatch,
                                                      tmp_path):
        bl._reset_backoff()
        made, _ = self._spying(monkeypatch, tmp_path)
        spy = SleepSpy()
        asyncio.run(bl.main(store=made["store"],
                            control_pool=FakeControlPool("false"),
                            sleep=spy))
        assert spy.delays == [300.0], (
            "a stopped loop must not re-poll every five seconds either")

    def test_a_running_control_lets_the_loop_start(self, monkeypatch,
                                                  tmp_path):
        made, calls = self._spying(monkeypatch, tmp_path)
        out = asyncio.run(bl.main(store=made["store"], run_for_s=0.0,
                                  control_pool=RunningControl(),
                                  sleep=SleepSpy()))
        assert out["started"] is True
        assert calls["store_start"] == 1 and calls["discover"] == 1
        assert made.get("started") is True

    def test_flipping_the_row_stops_a_running_loop_with_no_deploy(
            self, monkeypatch, tmp_path):
        """THE DEMONSTRATION. One row changes; the running process
        notices on its own timer and stops. No restart, no redeploy, no
        environment change -- which is exactly what the environment
        variable failed to deliver."""
        monkeypatch.setattr(bl.ctl, "CONTROL_EVERY_S", 0.0)
        made, _ = self._spying(monkeypatch, tmp_path)
        pool = FakeControlPool("true")

        async def drive():
            task = asyncio.create_task(bl.main(
                store=made["store"], control_pool=pool, sleep=SleepSpy()))
            for _ in range(200):                # let it get running
                await asyncio.sleep(0.01)
                if made.get("started"):
                    break
            assert made.get("started") is True, "never started"
            pool.value = "false"                # <- the whole change
            return await asyncio.wait_for(task, timeout=5)

        out = asyncio.run(drive())
        assert out["started"] is True
        assert out["stopped_by"] == "STOPPED_BY_CONTROL"
        assert made.get("stopped") is True, "the stream was stopped too"

    def test_an_unreadable_control_stops_a_running_loop_too(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(bl.ctl, "CONTROL_EVERY_S", 0.0)
        made, _ = self._spying(monkeypatch, tmp_path)
        pool = FakeControlPool("true")

        async def drive():
            task = asyncio.create_task(bl.main(
                store=made["store"], control_pool=pool, sleep=SleepSpy()))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if made.get("started"):
                    break
            pool.raises = ConnectionError("db went away")
            return await asyncio.wait_for(task, timeout=5)

        out = asyncio.run(drive())
        # A database that has gone away fails BOTH reads. Whichever is
        # asked first, the loop stops -- that is the whole property.
        assert out["stopped_by"] in ("CONTROL_UNREADABLE",
                                     "BUDGET_UNREADABLE")
        assert made.get("stopped") is True

    def test_the_report_names_the_control_it_is_running_on(
            self, monkeypatch, tmp_path):
        made, _ = self._spying(monkeypatch, tmp_path)
        out = asyncio.run(bl.main(store=made["store"], run_for_s=0.0,
                                  control_pool=RunningControl(),
                                  sleep=SleepSpy()))
        ctl_state = out["report"]["control"]
        assert ctl_state["why"] == "RUNNING"
        assert ctl_state["key"] == "bettor_live_observation"


# ── 11. the probe budget and its deadline ────────────────────────────

class TestTheProbeBudgetSurvivesRestarts:
    """A BUDGET HELD IN MEMORY IS NOT A BUDGET. `workers/all.py`
    restarts a returning loop forever -- about 73 times in sixteen
    minutes on 2026-09-21 -- so an in-process '40 markets' buys 40 more
    on every cycle. Both the cap and the deadline live in the database
    and are absolute."""

    def _spying(self, monkeypatch, tmp_path, candidates=6):
        made = TestTheEntryPointLifecycle()._patch(
            monkeypatch, tmp_path,
            discovery={"ok": True, "slugs": ["m1"], "considered": 1,
                       "detail": [{"slug": "m1", "outcome_leg": "yes"}],
                       "coverage": {"candidates": candidates,
                                    "distinct_enriched": 3}})
        return made

    def test_an_absent_budget_row_refuses(self, monkeypatch, tmp_path):
        """A probe without a declared budget does not run."""
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl(budget=None)
        pool.budget = None
        out = asyncio.run(bl.main(store=made["store"], control_pool=pool,
                                  sleep=SleepSpy()))
        assert out["started"] is False
        assert out["why"] == ctl_mod.B_UNREADABLE
        assert "started" not in made

    @pytest.mark.parametrize("budget,why", [
        (open_budget(max_distinct=40, consumed=40), ctl_mod.B_EXHAUSTED),
        (open_budget(seconds_left=-1), ctl_mod.B_EXPIRED),
        ("not json", ctl_mod.B_UNREADABLE),
        (json.dumps({"max_distinct": 40}), ctl_mod.B_UNREADABLE),
    ])
    def test_it_fails_closed_on_every_bad_budget(self, monkeypatch,
                                                 tmp_path, budget, why):
        made = self._spying(monkeypatch, tmp_path)
        out = asyncio.run(bl.main(store=made["store"],
                                  control_pool=RunningControl(budget=budget),
                                  sleep=SleepSpy()))
        assert out["started"] is False and out["why"] == why
        assert "started" not in made, "nothing was acquired"

    def test_expiry_DISARMS_so_the_restart_does_not_resume(
            self, monkeypatch, tmp_path):
        """Returning is not enough: the supervisor starts a fresh
        process five seconds later. The control is written to false so
        the NEXT process stops too."""
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl(budget=open_budget(seconds_left=-1))
        out = asyncio.run(bl.main(store=made["store"], control_pool=pool,
                                  sleep=SleepSpy()))
        assert out["why"] == ctl_mod.B_EXPIRED
        assert pool.value == "false", "the control was not disarmed"
        assert any(ctl_mod.CONTROL_KEY in str(a) for _s, a in pool.writes)

    def test_exhaustion_disarms_too(self, monkeypatch, tmp_path):
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl(budget=open_budget(max_distinct=40,
                                                 consumed=40))
        asyncio.run(bl.main(store=made["store"], control_pool=pool,
                            sleep=SleepSpy()))
        assert pool.value == "false"

    def test_nothing_in_the_codebase_ever_writes_true(self):
        """Arming is a human action. `disarm` is the only writer of the
        control and it writes one literal."""
        import inspect
        src = inspect.getsource(ctl_mod.disarm)
        assert "'false'::jsonb" in src
        assert "'true'" not in src and '"true"' not in src

    def test_consumption_is_recorded_before_the_universe_is_used(
            self, monkeypatch, tmp_path):
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl()
        asyncio.run(bl.main(store=made["store"], run_for_s=0.0,
                            control_pool=pool, sleep=SleepSpy()))
        assert json.loads(pool.budget)["distinct_consumed"] == 3
        assert any("distinct_consumed" in s for s, _a in pool.writes)

    def test_consumption_accumulates_across_runs(self, monkeypatch,
                                                 tmp_path):
        """The point of holding it in the database: a restart resumes
        against what has already been spent."""
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl()
        for _ in range(3):
            asyncio.run(bl.main(store=made["store"], run_for_s=0.0,
                                control_pool=pool, sleep=SleepSpy()))
        assert json.loads(pool.budget)["distinct_consumed"] == 9

    def test_the_remaining_budget_caps_what_discovery_may_read(
            self, monkeypatch, tmp_path):
        seen = {}
        made = TestTheEntryPointLifecycle()._patch(
            monkeypatch, tmp_path,
            discovery={"ok": True, "slugs": ["m1"], "considered": 1,
                       "detail": [{"slug": "m1", "outcome_leg": "yes"}],
                       "coverage": {}})

        async def capture(client=None, **kw):
            seen.update(kw)
            return {"ok": True, "slugs": ["m1"], "considered": 1,
                    "detail": [{"slug": "m1", "outcome_leg": "yes"}],
                    "coverage": {}}

        monkeypatch.setattr(bl, "_discover", capture)
        asyncio.run(bl.main(store=made["store"], run_for_s=0.0,
                            control_pool=RunningControl(
                                budget=open_budget(max_distinct=40,
                                                   consumed=37)),
                            sleep=SleepSpy()))
        assert seen["max_distinct"] == 3, (
            "discovery was handed the LIFETIME remainder, not a batch")

    def test_the_deadline_stops_a_running_loop_and_disarms(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(bl.ctl, "CONTROL_EVERY_S", 0.0)
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl()

        async def drive():
            task = asyncio.create_task(bl.main(
                store=made["store"], control_pool=pool, sleep=SleepSpy()))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if made.get("started"):
                    break
            assert made.get("started") is True
            pool.budget = open_budget(seconds_left=-1)   # deadline passes
            return await asyncio.wait_for(task, timeout=5)

        out = asyncio.run(drive())
        assert out["stopped_by"] == ctl_mod.B_EXPIRED
        assert made.get("stopped") is True, "the stream was stopped"
        assert pool.value == "false", "and the next process is stopped too"


class TestTheIdleDeploymentEvidencesTheApprovedLimits:
    """THE CHECK THIS STAGE RESTS ON.

    Stage 2 is authorized against eight specific limits, and the first
    deployment's whole job is to prove -- from a loop that refused to
    observe -- that the running process actually holds them. That proof
    is a log line carrying `effective_config()`, so the line has to be
    complete: a limit the line omits is a limit the deployment cannot
    evidence, and the September 21 incident is exactly what happens
    when a configuration is believed rather than read back.

    These tests fail if a future edit drops one of the eight, or
    changes a default out from under the approved figure.
    """

    APPROVED = {
        "max_rps": 0.25,
        "concurrency": 1,
        "budget_max_distinct_default": 40,
        "budget_deadline_s_default": 1800.0,
        "frame_capture_n": 25,
        "max_contracts": "0",
        "suspend_above_s": 120.0,
        "listing_max_retries": 2,
    }

    def _clean(self, monkeypatch):
        """The approved environment, and nothing inherited."""
        for name in (probe_mod.RPS_ENV, probe_mod.CONC_ENV,
                     probe_mod.BATCH_ENV, "BETTOR_LIVE_MAX_CONTRACTS",
                     "BETTOR_LIVE_DISCOVERY_PAGES", bl.KILL_ENV):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(ms.FRAMES_ENV, "25")

    def test_it_reports_every_approved_limit(self, monkeypatch):
        self._clean(monkeypatch)
        cfg = bl.effective_config()
        missing = [k for k in self.APPROVED if k not in cfg]
        assert not missing, f"effective_config omits {missing}"

    def test_the_reported_values_are_the_approved_values(self, monkeypatch):
        self._clean(monkeypatch)
        cfg = bl.effective_config()
        wrong = {k: (cfg[k], v) for k, v in self.APPROVED.items()
                 if cfg[k] != v}
        assert not wrong, f"reported != approved (got, approved): {wrong}"

    def test_the_line_is_json_serialisable(self, monkeypatch):
        """It is logged with json.dumps. A value that cannot serialise
        turns the evidence line into a traceback."""
        self._clean(monkeypatch)
        json.dumps(bl.effective_config())

    def test_it_does_not_claim_to_have_read_the_budget_row(self,
                                                          monkeypatch):
        """A refusal at the control returns BEFORE the budget is read.
        The line must name the row as authoritative rather than imply
        these defaults are what was armed."""
        self._clean(monkeypatch)
        cfg = bl.effective_config()
        assert cfg["budget_key"] == ctl_mod.BUDGET_KEY
        assert "row" in cfg["budget_authority"]
        assert all(k.endswith("_default") for k in cfg
                   if k.startswith("budget_") and k not in
                   ("budget_key", "budget_authority")), \
            "a budget figure not marked _default reads as one that was read"

    def test_an_override_above_the_envelope_is_flagged(self, monkeypatch):
        """Raising the pace past the demonstrated envelope must show up
        in the same line, so an override cannot be quiet."""
        self._clean(monkeypatch)
        assert bl.effective_config()["above_demonstrated_envelope"] is False
        monkeypatch.setenv(probe_mod.RPS_ENV, "5")
        flagged = bl.effective_config()
        assert flagged["max_rps"] == 5.0
        assert flagged["above_demonstrated_envelope"] is True

    def test_the_refusal_actually_carries_the_line(self, monkeypatch,
                                                  tmp_path, caplog):
        """Not that the function exists -- that a stopped `main()` logs
        it. The deployment has nothing else to show."""
        self._clean(monkeypatch)
        with caplog.at_level("INFO"):
            out = asyncio.run(bl.main(control_pool=FakeControlPool("false"),
                                      sleep=SleepSpy()))
        assert out["started"] is False
        assert out["config"]["max_rps"] == 0.25
        said = "\n".join(r.getMessage() for r in caplog.records)
        assert "not observing" in said
        assert "effective config" in said
        assert '"max_rps": 0.25' in said
        assert '"budget_max_distinct_default": 40' in said
