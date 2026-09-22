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
import inspect
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
from sportsassets import bettor_universe as uni_mod
from sportsassets.workers import bettor_live_loop as bl


def _reject_phantom_columns(sql: str) -> None:
    """A double that accepts a column production does not have is not a
    double, it is a second schema. On 2026-09-22 the reservation wrote
    `updated_at = now()`, which `ingestion_state` has never had, and
    every local proof passed because every local proof had invented the
    column. These doubles now raise the way PostgreSQL would."""
    import re as _re
    for assign in _re.findall(r"([a-zA-Z_]+)\s*=\s*",
                              sql.split("WHERE")[0]):
        if assign.lower() in ("key", "value", "now", "jsonb_set",
                              "to_jsonb", "coalesce"):
            continue
        raise RuntimeError(
            "UndefinedColumnError: ingestion_state has no column %r "
            "(see backend/migrations/001_init.sql)" % assign)


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    """The enrichment pace is 0.25 req/s by default, which is the point
    of it. These tests are about what `main()` DECIDES, so they run
    unpaced; the pace itself is asserted in
    test_bettor_universe_probe.TestTheRateLimitIsEnforced."""
    monkeypatch.setenv(probe_mod.RPS_ENV, "100000")
    monkeypatch.setenv(probe_mod.CONC_ENV, "8")


PROBE_ID = "11111111-2222-3333-4444-555555555555"

# `workers/all.py:RESTART_DELAY_SECONDS` -- the supervisor's own
# pause between loop restarts. A hold must be meaningfully longer.
RESTART_DELAY_FLOOR = 5.0


def open_budget(*, max_distinct=40, seconds_left=1800.0, consumed=0,
                max_attempts=160, attempts=0, max_listing=18, listing=0,
                probe_id=PROBE_ID, slugs=None):
    """An armed allowance with room and time left.

    The shape is the one `obs-arm` writes: a probe identity, three caps,
    three RESERVED counters and the set of distinct slugs already paid
    for. `consumed` names the distinct counter for the tests that
    predate the rename.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    row = {
        "probe_id": probe_id,
        "started_at": now.isoformat(),
        "deadline_at": (now + timedelta(seconds=seconds_left)).isoformat(),
        "max_distinct": max_distinct,
        "max_bbo_attempts": max_attempts,
        "max_listing_attempts": max_listing,
        "distinct_reserved": consumed,
        "bbo_attempts_reserved": attempts,
        "listing_attempts_reserved": listing,
        "slugs": list(slugs or [])}
    return json.dumps(row)


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
        _reject_phantom_columns(sql)
        if args and args[0] == ctl_mod.BUDGET_KEY and "jsonb_set" in sql:
            # The reservation's UPDATE, applied to the double's row so a
            # sequence of reservations behaves like the real one:
            # ($1 key, $2 counter name, $3 new value, [$4 slug]).
            import json as _j
            b = _j.loads(self.budget) if isinstance(self.budget, str) \
                else dict(self.budget or {})
            b[args[1]] = args[2]
            if len(args) > 3:
                b.setdefault("slugs", []).append(args[3])
            self.budget = _j.dumps(b)
        elif args and args[0] == ctl_mod.CONTROL_KEY:
            self.value = "false"
        return "OK"

    # ── the transaction surface `ctl.reserve` needs ──────────────────
    #
    # `reserve()` takes a connection and a transaction because a single
    # statement cannot hold the cap under concurrency. The double
    # therefore has to offer the same shape; it is the SAME object, so
    # `FOR UPDATE` is a no-op here and real serialisation is proved
    # against real PostgreSQL instead
    # (scripts/bettor_budget_reservation_probe.py).
    def acquire(self):
        pool = self

        class _Held:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *a):
                return False

        return _Held()

    def transaction(self):
        class _Txn:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *a):
                return False

        return _Txn()


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
            # THE DOUBLE CARRIES THE PRODUCTION SIGNATURE. `stop()`
            # now takes `wait_s` and RETURNS the closure record; a
            # double that took neither would have hidden the change
            # and passed while production raised TypeError.
            stream.stop = lambda *, wait_s=0.0: (
                made.setdefault("stopped", True),
                made.setdefault("stop_wait_s", wait_s),
                {"closed": True, "thread_alive": False,
                 "close_latency_s": 0.0, "waited_s": wait_s})[-1]
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

    def test_nothing_observable_is_reported_not_bypassed(self, monkeypatch,
                                                         tmp_path):
        """The rule is not relaxed to find something to watch.

        THE REFUSAL MOVED, AND NARROWED. It used to fire whenever the
        ECONOMIC rule admitted nothing, which made the transport
        untestable for as long as the strategy kept saying no. It now
        fires only when there is nothing VALID AND OPEN to watch at
        all -- a strictly weaker precondition. The strategy's verdict
        is still reported verbatim and is still not relaxed; see
        `TestObservationIsNotTradingAdmission` for the case where the
        rule refuses everything and the run proceeds anyway.
        """
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": [],
                                      "considered": 400, "pages_read": 6,
                                      "observation": {"slugs": [],
                                                      "eligible_for_"
                                                      "observation": 0},
                                      "excluded_by_reason":
                                          {"TRADED_VOLUME_BELOW_MIN": 400}})
        out = self.run_main(made)
        assert out["why"] == "NOTHING_OBSERVABLE"
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
            ("NOTHING_OBSERVABLE", {"discovery": {"ok": True, "slugs": [],
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

    def test_a_closed_control_polls_on_a_bounded_interval(
            self, monkeypatch, tmp_path):
        """A STOPPED LOOP IS HELD FROM BOTH SIDES.

        It must not re-poll every five seconds -- that was the original
        point of this test -- and it must not disappear for an hour
        either, which is what the shared escalating backoff did on
        2026-09-22 when it slept past the deadline of the probe it had
        just been armed for.
        """
        bl._reset_backoff()
        made, _ = self._spying(monkeypatch, tmp_path)
        spy = SleepSpy()
        asyncio.run(bl.main(store=made["store"],
                            control_pool=FakeControlPool("false"),
                            sleep=spy))
        assert spy.delays == [bl.IDLE_POLL_S]
        held = spy.delays[0]
        assert held > RESTART_DELAY_FLOOR, \
            "a stopped loop must not re-poll every five seconds"
        assert held < ctl_mod.PROBE_DEADLINE_S, \
            "a stopped loop must not sleep past the probe it is armed for"

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

    def test_exhaustion_no_longer_disarms_the_whole_authorization(
            self, monkeypatch, tmp_path):
        """THE REPAIR. Exhausting the HTTP allowance is not the end of
        the observation window.

        This test previously asserted `pool.value == "false"` -- that a
        spent allowance disarmed the probe. Probe 2 showed what that
        costs: enrichment consumed all 40 distinct slots BEFORE the
        stream opened, the next budget check stopped the loop, and an
        1,800-second authorization produced 30 seconds of streaming and
        14 frames of 25, leaving roughly 1,187 seconds unused.

        A fresh process with a spent allowance still cannot acquire, so
        it still does not start -- but it must NOT write the control to
        false, because the authorization runs to the DEADLINE and a
        crash at minute three would otherwise end a thirty-minute
        window. The deadline disarms; exhaustion waits.
        """
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl(budget=open_budget(max_distinct=40,
                                                 consumed=40))
        out = asyncio.run(bl.main(store=made["store"], control_pool=pool,
                                  sleep=SleepSpy()))
        assert out["started"] is False
        assert out["why"] == ctl_mod.B_EXHAUSTED
        assert pool.value == "true", \
            "a spent allowance must not disarm a live authorization"
        assert not any(ctl_mod.CONTROL_KEY in str(a)
                       for _s, a in pool.writes), \
            "nothing may be written to the control on exhaustion"
        assert "started" not in made, "and nothing was acquired"

    def test_a_spent_allowance_closes_ACQUISITION_not_the_OBSERVATION(
            self, monkeypatch, tmp_path):
        """The in-flight half of the repair, and the important half.

        A running loop whose allowance runs out mid-stream must:
          * keep the socket it already paid for,
          * stop dispatching HTTP entirely -- discovery AND settlement,
          * not disarm,
          * and say so in the receipt, separately from `stopped_by`.

        This is the case probe 2 could not distinguish, because
        acquisition ending and the run ending were the same event.
        """
        made = self._spying(monkeypatch, tmp_path)

        class ExhaustsMidRun(FakeControlPool):
            """Open for the first budget read, spent for every one
            after -- which is exactly what a probe that finishes
            enrichment looks like."""

            def __init__(self):
                super().__init__("true", budget=open_budget())
                self.budget_reads = 0

            async def fetchval(self, sql, *args):
                key = args[0] if args else None
                if key == ctl_mod.BUDGET_KEY:
                    self.budget_reads += 1
                    if self.budget_reads > 1:
                        self.budget = open_budget(max_distinct=40,
                                                  consumed=40)
                return await super().fetchval(sql, *args)

        pool = ExhaustsMidRun()
        discoveries = []
        real = bl._discover

        async def counting_discover(client=None, **kw):
            discoveries.append(kw)
            return {"ok": True, "slugs": ["m1"], "considered": 1,
                    "detail": [{"slug": "m1", "outcome_leg": "yes"}],
                    "coverage": {"candidates": 6, "distinct_enriched": 3}}

        monkeypatch.setattr(bl, "_discover", counting_discover)
        monkeypatch.setattr(bl, "CONTROL_EVERY_S", 0.0, raising=False)
        monkeypatch.setattr(ctl_mod, "CONTROL_EVERY_S", 0.0)
        monkeypatch.setattr(bl, "DISCOVERY_EVERY_S", 0.0)
        monkeypatch.setattr(bl, "SETTLEMENT_EVERY_S", 0.0)

        settlements = []

        out = asyncio.run(bl.main(store=made["store"], control_pool=pool,
                                  run_for_s=0.25, sleep=SleepSpy()))
        del real, settlements

        assert out["started"] is True, "the run must not refuse to start"
        # THE OBSERVATION SURVIVED the exhaustion.
        assert out["stopped_by"] != ctl_mod.B_EXHAUSTED, (
            "a spent allowance ended the observation; that is the defect "
            "this repair exists to remove")
        # THE ALLOWANCE DID NOT GROW. Exactly one discovery -- the
        # startup one -- and none after acquisition closed.
        assert len(discoveries) == 1, (
            "discovery ran %d times after the allowance was spent"
            % (len(discoveries) - 1))
        # AND THE AUTHORIZATION IS STILL LIVE.
        assert pool.value == "true"

        r = out.get("stop_receipt") or {}
        assert r.get("acquisition_closed") is True
        assert r.get("acquisition_closed_at"), \
            "the receipt must timestamp acquisition closing"
        assert r.get("observed_after_acquisition_closed_s") is not None, \
            "the receipt must report how long observation outlived it"

    def test_nothing_in_the_codebase_ever_writes_true(self):
        """Arming is a human action. `disarm` is the only writer of the
        control and it writes one literal."""
        import inspect
        src = inspect.getsource(ctl_mod.disarm)
        assert "'false'::jsonb" in src
        assert "'true'" not in src and '"true"' not in src

    def test_nothing_is_charged_after_the_fact_any_more(
            self, monkeypatch, tmp_path):
        """THE DEFECT THESE TWO TESTS USED TO ENCODE.

        They asserted that `main()` wrote `distinct_consumed` after
        discovery returned. That write WAS the bug: it sat below the
        EMPTY_UNIVERSE early return, so the ordinary path spent the
        allowance and recorded nothing, and a crash between dispatch and
        write left the row reusable. Post-hoc charging is now gone
        entirely and this test fails if it comes back.
        """
        made = self._spying(monkeypatch, tmp_path)
        pool = RunningControl()
        asyncio.run(bl.main(store=made["store"], run_for_s=0.0,
                            control_pool=pool, sleep=SleepSpy()))
        assert not hasattr(ctl_mod, "consume_budget"), \
            "post-hoc charging must not exist"
        row = json.loads(pool.budget)
        assert "distinct_consumed" not in row
        for sql, _a in pool.writes:
            assert "distinct_consumed" not in sql

    def test_no_counter_is_ever_decremented(self):
        """NO REFUND PATH. Failures, rejected candidates and empty
        universes all keep what they spent, so a counter only ever goes
        up. Asserted over a real reservation sequence AND over the
        source, because a refund added later would pass the first check
        by simply never being exercised."""
        pool = FakeControlPool("true", budget=open_budget())
        seen: dict = {}

        async def drive():
            for i in range(4):
                await ctl_mod.reserve(pool, ctl_mod.R_DISTINCT,
                                      slug="m%d" % i, probe_id=PROBE_ID)
                await ctl_mod.reserve(pool, ctl_mod.R_ATTEMPT,
                                      slug="m%d" % i, probe_id=PROBE_ID)
            await ctl_mod.reserve(pool, ctl_mod.R_LISTING,
                                  probe_id=PROBE_ID)

        asyncio.run(drive())
        for sql, args in pool.writes:
            if args and args[0] == ctl_mod.BUDGET_KEY \
                    and "jsonb_set" in sql:
                name, val = args[1], args[2]
                assert val > seen.get(name, -1), \
                    f"{name} went from {seen.get(name)} to {val}"
                seen[name] = val
        assert seen == {"distinct_reserved": 4,
                        "bbo_attempts_reserved": 4,
                        "listing_attempts_reserved": 1}, seen

        # And over the source, because a refund added later would pass
        # the check above simply by never being exercised. Code tokens
        # only -- the module's prose says "never refunded" and that is
        # the opposite of a defect.
        import ast
        tree = ast.parse(inspect.getsource(ctl_mod))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body \
                    and isinstance(body[0], ast.Expr) \
                    and isinstance(getattr(body[0], "value", None),
                                   ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
        code = ast.unparse(tree)
        for banned in ("- 1)", "- $2", "def refund", "def unreserve",
                       "distinct_reserved') - ", "used - 1"):
            assert banned not in code, \
                f"{banned!r} in the control module looks like a refund"

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


class TestTheProductionSeamHasNoInjectedPool:
    """THE DEFECT THIS CLASS PINS, found on production 2026-09-22.

    `workers/all.py` registers this loop as `bettor_live_loop.main` and
    the supervisor calls it WITH NO ARGUMENTS, so `control_pool` is
    None. `read_control`, `read_budget` and `reserve` all resolve None
    through `_resolve()` -> `get_pool()`; an earlier `_reserve` closure
    did not, and refused every reservation with NO_CONTROL_POOL.

    Measured at 2026-09-22T07:39:05.938Z: the live worker read the
    control as `true`, read an open allowance, then refused its first
    listing reservation and never issued a request. It failed CLOSED --
    zero venue requests, all three counters still 0 -- but the probe
    could not run.

    Every proof before it injected a pool, so the seam production
    actually uses was never exercised. These tests use that seam.
    """

    def _patched_pool(self, monkeypatch, pool):
        """Make `get_pool()` answer, exactly as it does in production."""
        import sportsassets.db as dbmod

        async def get_pool():
            return pool

        monkeypatch.setattr(dbmod, "get_pool", get_pool, raising=False)

    def _venue(self):
        """A client that counts, so the test can see that a request was
        actually issued rather than merely permitted."""
        class Markets:
            def __init__(self):
                self.list_calls, self.bbo_calls = 0, []

            def list(self, params):
                self.list_calls += 1
                if params.get("offset", 0) > 0:
                    return {"markets": []}
                return {"markets": [
                    {"id": "i%d" % i, "slug": "s%03d" % i,
                     "title": "M", "outcome": "yes", "active": True,
                     "closed": False, "liquidity": 1.0, "volume": 1.0,
                     "eventSlug": "e"} for i in range(3)]}

            def bbo(self, slug):
                self.bbo_calls.append(slug)
                return {"marketData": {
                    "marketSlug": slug, "state": "MARKET_STATE_OPEN",
                    "bestBid": {"value": "0.2150", "currency": "USD"},
                    "bestAsk": {"value": "0.3850", "currency": "USD"},
                    "askDepth": 16, "bidDepth": 10,
                    "sharesTraded": "138.77"}}

            def settlement(self, slug):
                return {"marketData": {}}

        class Client:
            def __init__(self):
                self.markets = Markets()

        return Client()

    def test_main_with_no_pool_still_reserves_and_dispatches(
            self, monkeypatch, tmp_path):
        """The whole defect, in one assertion: called the way the
        supervisor calls it -- no `control_pool` -- the loop must still
        take allowance and reach the venue."""
        pool = FakeControlPool("true", budget=open_budget())
        self._patched_pool(monkeypatch, pool)
        monkeypatch.setenv(probe_mod.RPS_ENV, "100000")

        class Cfg:
            pmus_key_id, pmus_secret_key = "k", "s"

        import sportsassets.config as cfgmod
        monkeypatch.setattr(cfgmod, "settings", lambda: Cfg(),
                            raising=False)
        client = self._venue()
        asyncio.run(bl.main(client=client, store=filestore(tmp_path),
                            run_for_s=0.0, sleep=SleepSpy()))
        row = json.loads(pool.budget)
        assert row["listing_attempts_reserved"] >= 1, \
            "no listing attempt was reserved through the production seam"
        assert client.markets.list_calls >= 1, "the listing never went out"
        assert row["distinct_reserved"] == len(set(client.markets.bbo_calls))

    def test_no_reservation_answers_NO_CONTROL_POOL(self):
        """The specific refusal that fired on production at
        07:39:05.938Z must not be reachable at all any more.

        Over CODE only -- the comment above the fix names the refusal
        deliberately, and recording why it existed is the opposite of
        reintroducing it."""
        import ast
        tree = ast.parse(inspect.getsource(bl))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body \
                    and isinstance(body[0], ast.Expr) \
                    and isinstance(getattr(body[0], "value", None),
                                   ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
        assert "NO_CONTROL_POOL" not in ast.unparse(tree)

    def test_disarm_reaches_the_database_without_an_injected_pool(
            self, monkeypatch):
        """THE SECOND, QUIETER HALF. `disarm` took a pool directly and
        `main()` guarded it with `control_pool is not None`, so on
        production the deadline's automatic shutdown would have written
        nothing at all -- on the one deployment it exists for."""
        pool = FakeControlPool("true", budget=open_budget())
        self._patched_pool(monkeypatch, pool)
        out = asyncio.run(ctl_mod.disarm(None, ctl_mod.B_EXPIRED))
        assert out["disarmed"] is True
        assert pool.value == "false", "the control was not written"
        assert "control_pool is not None" not in inspect.getsource(bl), \
            "a None pool must never mean 'skip the safety write'"

    def test_every_control_entry_point_accepts_none(self, monkeypatch):
        """One rule, applied everywhere: None means RESOLVE THE PROCESS
        POOL, never 'there is no pool'."""
        pool = FakeControlPool("true", budget=open_budget())
        self._patched_pool(monkeypatch, pool)

        async def drive():
            assert (await ctl_mod.read_control(None))["run"] is True
            assert (await ctl_mod.read_budget(None))["open"] is True
            r = await ctl_mod.reserve(None, ctl_mod.R_LISTING,
                                      probe_id=PROBE_ID)
            assert ctl_mod.granted(r), r
            assert (await ctl_mod.disarm(None, "T"))["disarmed"] is True

        asyncio.run(drive())


class TestTheStoppedWorkerPollsInsteadOfSleepingThroughTheProbe:
    """THE DEFECT THIS CLASS PINS, measured 2026-09-22.

    A deliberately stopped worker shared the ESCALATING acquisition
    backoff. Hours of idling had climbed the rung to 3600 s, so when an
    allowance was armed at 07:38:21Z with a 1800 s deadline, the next
    wake was not due until ~08:34 -- after the deadline it was meant to
    run inside. The probe could only be started by restarting the
    service, and a stop you have to restart to undo is not a control.

    A refusal that never touched the venue now polls on a short fixed
    interval; one that did still backs off.
    """

    def setup_method(self):
        bl._reset_backoff()

    def _hold(self, why, n=1):
        spy = SleepSpy()

        async def drive():
            for _ in range(n):
                await bl._backoff(why, sleep=spy)

        asyncio.run(drive())
        return spy.delays

    def test_a_stopped_worker_polls_and_never_escalates(self):
        """Ten cycles of being switched off is ten short polls."""
        delays = self._hold(ctl_mod.W_STOPPED, n=10)
        assert delays == [bl.IDLE_POLL_S] * 10, delays

    def test_the_poll_is_short_enough_to_run_inside_a_probe(self):
        """The arithmetic the incident turned on: a stopped worker must
        notice `obs-run` well inside the 1800 s a probe is given."""
        assert bl.IDLE_POLL_S + 5 < ctl_mod.PROBE_DEADLINE_S / 10, (
            "a stopped worker must pick up an arming many times over "
            "within one probe's deadline")

    @pytest.mark.parametrize("why", [
        "KILL_SWITCH", "STOPPED_BY_CONTROL", "CONTROL_ROW_ABSENT",
        "CONTROL_MALFORMED", "CONTROL_UNREADABLE",
        "DEADLINE_PASSED", "BUDGET_EXHAUSTED", "BUDGET_UNREADABLE",
    ])
    def test_every_zero_cost_refusal_polls(self, why):
        """Each of these is decided before a client is constructed, so
        each costs one database read and no venue request."""
        assert bl.is_control_reason(why), why
        assert self._hold(why) == [bl.IDLE_POLL_S]

    @pytest.mark.parametrize("why", [
        "DISCOVERY_FAILED", "EMPTY_UNIVERSE", "NO_CREDENTIALS",
        "STORE_START_FAILED", "STORE_START_REFUSED", "NO_DURABLE_STORE",
    ])
    def test_acquisition_errors_still_back_off(self, why):
        assert not bl.is_control_reason(why), why
        assert self._hold(why) == [bl.NOSTART_BACKOFF_S[0]]

    def test_the_acquisition_ladder_is_unchanged(self):
        assert self._hold("DISCOVERY_FAILED", n=5) == [
            300.0, 900.0, 1800.0, 3600.0, 3600.0]

    def test_a_stop_does_not_advance_the_acquisition_rung(self):
        """The two are separate. Being switched off between two venue
        failures must not push the venue ladder along."""
        assert self._hold("DISCOVERY_FAILED") == [300.0]
        assert self._hold(ctl_mod.W_STOPPED, n=20) == [bl.IDLE_POLL_S] * 20
        assert self._hold("DISCOVERY_FAILED") == [900.0], \
            "the stop moved the acquisition ladder"

    def test_a_stop_does_not_reset_it_either(self):
        self._hold("DISCOVERY_FAILED", n=3)          # 300, 900, 1800
        self._hold(ctl_mod.W_STOPPED, n=3)
        assert self._hold("DISCOVERY_FAILED") == [3600.0], \
            "the stop reset the acquisition ladder"

    def test_a_venue_retry_after_still_wins_over_both(self):
        spy = SleepSpy()
        asyncio.run(bl._backoff("EMPTY_UNIVERSE", sleep=spy,
                                at_least=4000.0))
        assert spy.delays == [4000.0]

    def test_polling_costs_no_venue_request(self, monkeypatch, tmp_path):
        """The point of the short interval: it is cheap. A stopped
        `main()` must construct no client at all."""
        import sportsassets.pmus as pmus

        def boom():
            raise AssertionError("a stopped loop built a venue client")

        monkeypatch.setattr(pmus, "_get_client", boom, raising=False)
        spy = SleepSpy()
        out = asyncio.run(bl.main(control_pool=FakeControlPool("false"),
                                  store=filestore(tmp_path), sleep=spy))
        assert out["why"] == ctl_mod.W_STOPPED
        assert spy.delays == [bl.IDLE_POLL_S]


# ── the two repairs of 2026-09-22 (turn D) ───────────────────────────

def _bbo(slug, *, bid="0.4500", ask="0.4800", vol="900",
         state="MARKET_STATE_OPEN"):
    """One enriched row in the spelling `bettor_universe.assess` reads."""
    row = {"slug": slug, "market_id": slug, "venue_state": state,
           "sharesTraded": vol, "outcome_leg": "yes",
           "enriched_from": "markets.bbo"}
    if bid is not None:
        row["bestBid"] = {"value": bid, "currency": "USD"}
    if ask is not None:
        row["bestAsk"] = {"value": ask, "currency": "USD"}
    return row


class TestObservationIsNotTradingAdmission:
    """THE CIRCULAR DEPENDENCY.

    Probe 3c413436 subscribed only to markets the frozen rule ADMITTED.
    The rule admitted none, so nothing was subscribed, so no WebSocket
    frame was ever received, so the transport stayed unverified -- and
    would have stayed unverified for as long as the strategy kept
    saying no. The two outcomes are now independent.

    Every test here asserts an EXTERNAL CONSEQUENCE: what got
    subscribed, what got journalled, what the rule still says.
    """

    def test_a_universe_the_rule_refuses_entirely_is_still_watched(self):
        # Every one of these is REFUSED by the frozen rule: one tick of
        # spread is below MIN_SPREAD_TICKS = 2.
        rows = [_bbo("m%d" % i, bid="0.4500", ask="0.4600")
                for i in range(6)]
        cands = [{"slug": r["slug"]} for r in rows]
        sel = uni_mod.select(rows)
        assert sel["slugs"] == [], "fixture no longer exercises a refusal"

        obs = bl.observation_subset(rows, cands, limit=4,
                                    admitted=sel["slugs"])
        assert len(obs["slugs"]) == 4, "nothing was watched"
        assert obs["observed_despite_refusal"] == 4
        assert obs["refusal_reasons_observed"] == {
            uni_mod.R_SPREAD: 4}

    def test_the_frozen_rule_is_applied_unchanged(self):
        """Watching a market does not admit it. The thresholds are the
        same objects the rule publishes."""
        rows = [_bbo("m1", bid="0.4500", ask="0.4600")]
        obs = bl.observation_subset(rows, [{"slug": "m1"}], limit=4,
                                    admitted=[])
        d = obs["detail"][0]
        assert d["strategy_admitted"] is False
        assert d["universe_rule_reason"] == uni_mod.R_SPREAD
        assert obs["not_trading_admission"] is True
        # UNCHANGED, asserted against the rule itself rather than a
        # copy of its numbers.
        assert uni_mod.MIN_SPREAD_TICKS == 2
        assert uni_mod.MAX_PRICE == 0.50
        assert uni_mod.MIN_SHARES_TRADED == 100.0

    def test_the_union_of_admitted_and_observed_respects_the_total_cap(self):
        """THE DECLARED LIMIT IS A TOTAL.

        This test used to assert that admitted markets did NOT consume
        the observation limit -- so one admitted plus a limit of three
        subscribed four, and the declared bound described neither
        number. `watch_max` now bounds the union.
        """
        good = _bbo("good", bid="0.4000", ask="0.4500")   # 5 ticks, admitted
        bad = [_bbo("b%d" % i, bid="0.4500", ask="0.4600") for i in range(5)]
        rows = [good] + bad
        cands = [{"slug": r["slug"]} for r in rows]
        sel = uni_mod.select(rows)
        assert sel["slugs"] == ["good"]
        obs = bl.observation_subset(rows, cands, limit=8, watch_max=3,
                                    admitted=sel["slugs"])
        assert obs["total_subscribed"] == 3
        assert len(obs["slugs"]) == 3
        assert "good" in obs["slugs"]
        assert len(obs["observation_only_slugs"]) == 2
        assert obs["watch_max"] == 3

    def test_the_cap_truncates_the_rules_own_choices_and_says_so(self):
        """A run that watched fewer markets than the rule chose is a
        DIFFERENT RUN, and the truncation is named rather than silent."""
        rows = [_bbo("a%d" % i, bid="0.4000", ask="0.4500")
                for i in range(6)]
        cands = [{"slug": r["slug"]} for r in rows]
        sel = uni_mod.select(rows)
        assert len(sel["slugs"]) == 6
        obs = bl.observation_subset(rows, cands, limit=8, watch_max=2,
                                    admitted=sel["slugs"])
        assert obs["total_subscribed"] == 2
        assert len(obs["strategy_admitted_dropped_by_cap"]) == 4
        assert obs["observation_only_slugs"] == []

    def test_the_default_total_cap_is_the_observation_limit(self):
        rows = [_bbo("a%d" % i, bid="0.4500", ask="0.4600")
                for i in range(9)]
        cands = [{"slug": r["slug"]} for r in rows]
        obs = bl.observation_subset(rows, cands, limit=4, admitted=[])
        assert obs["watch_max"] == 4 and obs["total_subscribed"] == 4

    def test_a_malformed_body_is_never_subscribed(self):
        """`assess` reports ONE_SIDED_BOOK both for an absent side and
        for one that is present and unreadable. Only the first is a
        market state; the second is a body we could not read, and
        subscribing on it would mean subscribing on nothing."""
        good = _bbo("real_one_sided", ask=None)
        junk = _bbo("junk", ask="not-a-number")
        rows = [good, junk]
        obs = bl.observation_subset(rows, [{"slug": "real_one_sided"},
                                           {"slug": "junk"}],
                                    limit=8, admitted=[])
        assert obs["slugs"] == ["real_one_sided"]
        assert obs["excluded_malformed_body"] == ["junk"]

    def test_closed_and_unparsable_markets_are_never_watched(self):
        """Valid and OPEN. A market the venue does not call open cannot
        produce a meaningful frame -- that is a transport fact, and it
        is the ONLY non-economic filter applied."""
        rows = [_bbo("closed", state="MARKET_STATE_CLOSED"),
                _bbo("crossed", bid="0.6000", ask="0.5000"),
                _bbo("thin", bid="0.4500", ask="0.4600")]
        obs = bl.observation_subset(rows, [{"slug": r["slug"]}
                                           for r in rows],
                                    limit=9, admitted=[])
        assert obs["slugs"] == ["thin"]

    def test_one_sided_books_are_watched_but_ranked_last(self):
        rows = [_bbo("one_sided", ask=None),
                _bbo("two_sided", bid="0.4500", ask="0.4600")]
        obs = bl.observation_subset(rows, [{"slug": "one_sided"},
                                           {"slug": "two_sided"}],
                                    limit=9, admitted=[])
        assert obs["slugs"] == ["two_sided", "one_sided"]

    def test_the_subset_is_deterministic_and_order_is_the_seeded_one(self):
        """DETERMINISTIC, AND CHOSEN BEFORE THE ECONOMICS ARE SEEN. The
        candidate order decides, and the candidate order is the seeded
        permutation fixed at listing time -- not the arrival order of
        the enriched rows and not anything about their prices."""
        rows = [_bbo("z", bid="0.4500", ask="0.4600"),
                _bbo("a", bid="0.4500", ask="0.4600"),
                _bbo("m", bid="0.4500", ask="0.4600")]
        cands = [{"slug": "m"}, {"slug": "z"}, {"slug": "a"}]
        first = bl.observation_subset(rows, cands, limit=2, admitted=[])
        again = bl.observation_subset(list(reversed(rows)), cands,
                                      limit=2, admitted=[])
        assert first["slugs"] == again["slugs"] == ["m", "z"]

    def test_every_record_carries_why_the_market_is_watched(self, tmp_path):
        """DURABLE, PER RECORD. Not a startup log line a restart
        overwrites."""
        loop, stream = wired(tmp_path, slugs=("m1",), admission={
            "m1": {"observation_basis": "OBSERVATION_ONLY",
                   "universe_rule_reason": uni_mod.R_SPREAD,
                   "universe": uni_mod.UNIVERSE_VERSION}})
        stream._on_market_data(md("m1", source_ts=fresh(-1)))
        loop.drain()
        asyncio.run(loop.flush())
        lines = journal_lines(tmp_path)
        assert lines, "no record was written"
        assert all(x["observation_basis"] == "OBSERVATION_ONLY"
                   for x in lines)
        assert all(x["universe_rule_reason"] == uni_mod.R_SPREAD
                   for x in lines)
        assert loop.report()["watching"]["refusals_observed"] == \
            {uni_mod.R_SPREAD: len(lines)}

    def test_watching_never_produces_an_order(self, tmp_path):
        loop, stream = wired(tmp_path, slugs=("m1",), admission={
            "m1": {"observation_basis": "OBSERVATION_ONLY",
                   "universe_rule_reason": uni_mod.R_SPREAD}})
        stream._on_market_data(md("m1", source_ts=fresh(-1)))
        loop.drain()
        rep = loop.report()
        assert rep["orders_submitted"] == 0
        assert rep["watching"]["observation_is_not_trading_admission"]
        # THE STRUCTURAL GUARANTEE, not the configured one: the worker
        # holds no order path at all.
        src = inspect.getsource(bl)
        assert "orders." not in src and "place_order" not in src


class TestTheListingBudgetCountsOutboundRequests:
    """ONE RESERVED UNIT PER OUTBOUND REQUEST.

    THE DEFECT, measured on probe 3c413436: the reservation was taken
    once around a loop that issued up to six `markets.list` calls, so
    `listing_attempts_reserved: 1` was written while the venue received
    six requests -- and those six went through no pacer, so a run
    reported as 0.25 req/s opened with a six-request burst.
    """

    class _Client:
        """A venue whose LISTING is counted, page by page."""

        def __init__(self, pages, *, fail_on=(), status=None):
            self.pages = pages
            self.fail_on = set(fail_on)
            self.status = status
            self.retry_after = None
            self.calls: list = []
            self.markets = self

        def list(self, params):
            self.calls.append(dict(params))
            n = len(self.calls)
            if n in self.fail_on:
                raise _HTTPish(self.status or 500, self.retry_after)
            off = int(params.get("offset", 0))
            size = int(params.get("limit", 500))
            idx = off // size
            rows = self.pages[idx] if idx < len(self.pages) else []
            return {"markets": rows}

    @staticmethod
    def _rows(n, *, base=0):
        return [{"slug": "s%05d" % (base + i), "active": True,
                 "closed": False, "outcome": "yes"} for i in range(n)]

    def _run(self, client, **kw):
        seen = {"n": 0}

        async def reserve(kind, slug):
            assert kind == "listing"
            seen["n"] += 1
            return {"ok": True, "why": "RESERVED", "new": True}

        spy = SleepSpy()
        out = asyncio.run(bl._list_candidates(client=client,
                                              reserve=reserve, sleep=spy,
                                              **kw))
        return out, seen["n"], spy

    def test_every_page_costs_a_reserved_unit(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGES", "6")
        pages = [self._rows(10, base=i * 10) for i in range(4)] + \
                [self._rows(3, base=40)]
        out, reserved, _ = self._run(self._Client(pages))
        assert len(out["candidates"]) == 43
        assert len(pages[0]) == 10
        # FIVE PAGES READ, FIVE RESERVATIONS, FIVE OUTBOUND REQUESTS.
        # The old build reserved ONE and issued five.
        assert reserved == 5
        assert out["listing_attempts"] == 5
        assert out["listing_pages_requested"] == 5
        assert len(out["request_telemetry"]) and \
            out["request_telemetry"]["requests_started"] == 5

    def test_an_unfunded_page_is_never_issued(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        client = self._Client([self._rows(10, base=i * 10)
                               for i in range(6)])
        n = {"i": 0}

        async def reserve(kind, slug):
            n["i"] += 1
            if n["i"] > 3:
                return {"ok": False, "why": "RESERVATION_EXHAUSTED"}
            return {"ok": True, "why": "RESERVED", "new": True}

        out = asyncio.run(bl._list_candidates(client=client,
                                              reserve=reserve,
                                              sleep=SleepSpy()))
        # THE REQUEST COUNT IS THE THING. Three funded, three issued.
        assert len(client.calls) == 3
        assert out["ok"] is True and out["pages_read"] == 3
        assert out["listing_incomplete"]

    def test_a_retry_costs_its_own_unit(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        client = self._Client([self._rows(3)], fail_on=(1,))
        out, reserved, spy = self._run(client)
        assert len(client.calls) == 2 and reserved == 2
        assert out["listing_attempts"] == 2
        assert spy.delays == [2.0], "the retry did not back off"

    def test_page_one_failing_is_a_failed_listing(self, monkeypatch):
        client = self._Client([self._rows(3)], fail_on=(1, 2, 3))
        out, reserved, _ = self._run(client)
        assert out["ok"] is False
        assert reserved == 3 and len(client.calls) == 3

    def test_a_later_page_failing_keeps_the_rows_already_paid_for(
            self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        client = self._Client([self._rows(10, base=0),
                               self._rows(10, base=10)],
                              fail_on=(2, 3, 4))
        out, _, _ = self._run(client)
        assert out["ok"] is True
        assert len(out["candidates"]) == 10
        assert out["pages_read"] == 1
        assert out["listing_incomplete"]

    def test_the_listing_is_paced(self, monkeypatch):
        """Six unpaced requests at the start of a run is why '0.25
        req/s' was never a description of the process."""
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        monkeypatch.setenv(probe_mod.RPS_ENV, "0.5")
        client = self._Client([self._rows(10, base=0),
                               self._rows(10, base=10),
                               self._rows(3, base=20)])
        out, _, spy = self._run(client)
        assert out["pages_read"] == 3
        # THE PACER SLEPT BETWEEN PAGES: one wait per gap, each at
        # least the configured interval. (The exact figures grow under
        # a spy sleep, which does not advance the clock it measures
        # against -- the property under test is that the listing is
        # paced at all, which it previously was not.)
        assert len(spy.delays) == 2
        assert all(d > 1.9 for d in spy.delays)
        # The densest-window figures are NOT asserted here: a spy
        # sleep does not advance the clock they are measured against,
        # so all three starts land in the same millisecond and the
        # window reads 3. That is a property of the double, not of the
        # code, and asserting it either way would be asserting the
        # double. What IS asserted is the request count.
        assert out["request_telemetry"]["requests_started"] == 3

    def test_repeated_throttling_stops_the_listing(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        client = self._Client([self._rows(10, base=0),
                               self._rows(10, base=10)],
                              fail_on=(2, 3, 4, 5, 6), status=429)
        out, reserved, _ = self._run(client)
        assert out["ok"] is True and out["pages_read"] == 1
        assert out["rate_limited"] >= 2
        assert "throttled" in (out["listing_incomplete"] or "")
        # IT STOPPED rather than walking the remaining pages to collect
        # identical refusals.
        assert len(client.calls) == 3

    def test_a_429_records_what_this_process_was_doing(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        client = self._Client([self._rows(3)], fail_on=(1, 2, 3),
                              status=429)
        out, _, _ = self._run(client)
        ev = out["rate_limit_events"]
        # TWO, not three: the second consecutive refusal STOPS the
        # listing rather than spending the third retry on the same
        # answer. Both are recorded.
        assert len(ev) == probe_mod.LISTING_MAX_CONSECUTIVE_429 == 2
        assert len(client.calls) == 2
        assert all(e["where"] == "listing" for e in ev)
        assert all(e["observed_starts_last_10s"] >= 1 for e in ev)
        # AND IT REFUSES TO ATTRIBUTE THE CAUSE.
        assert all("NOT ESTABLISHED" in e["attribution"] for e in ev)

    def test_retry_after_is_honoured_verbatim(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        client = self._Client([self._rows(3)], fail_on=(1,), status=429)
        client.retry_after = "47"
        out, _, spy = self._run(client)
        assert 47.0 in spy.delays, "the server's own backoff was ignored"

    def test_a_retry_after_above_the_threshold_abandons_the_listing(
            self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        client = self._Client([self._rows(10, base=0),
                               self._rows(3, base=10)],
                              fail_on=(2,), status=429)
        client.retry_after = str(int(probe_mod.PROBE_SUSPEND_ABOVE_S) + 60)
        out, _, spy = self._run(client)
        assert out["suspended_for_s"] == \
            probe_mod.PROBE_SUSPEND_ABOVE_S + 60
        assert len(client.calls) == 2, "it retried sooner than asked"
        assert probe_mod.PROBE_SUSPEND_ABOVE_S + 60 not in spy.delays

    def test_a_listing_suspension_reaches_the_callers_backoff(
            self, monkeypatch, tmp_path):
        """The venue asked for a delay. A discovery failure that
        dropped it would let the loop come back sooner than the server
        said -- the one thing a limiter tells us not to do."""
        monkeypatch.setenv("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "10")
        asked = probe_mod.PROBE_SUSPEND_ABOVE_S + 300

        class _C(TestTheListingBudgetCountsOutboundRequests._Client):
            pass

        client = _C([[{"slug": "s1", "active": True, "closed": False}]],
                    fail_on=(1, 2, 3), status=429)
        client.retry_after = str(int(asked))

        async def reserve(kind, slug):
            return {"ok": True, "why": "RESERVED", "new": True}

        out = asyncio.run(bl._discover(client=client, reserve=reserve,
                                       sleep=SleepSpy()))
        assert out["ok"] is False
        assert out["suspended_for_s"] == asked

        spy = SleepSpy()
        asyncio.run(bl._backoff("DISCOVERY_FAILED", sleep=spy,
                                at_least=out["suspended_for_s"]))
        assert spy.delays == [asked]


class _HTTPish(Exception):
    """An SDK error in the shape `probe_mod._status_of` reads."""

    def __init__(self, status, retry_after=None):
        super().__init__("HTTP %d" % status)
        self.status_code = status
        self.response = _Resp(status, retry_after)


class _Resp:
    def __init__(self, status, retry_after):
        self.status_code = status
        self.headers = ({} if retry_after is None
                        else {"retry-after": str(retry_after)})


class TestTheSampleIsReproducible:
    """The 40 markets of probe 3c413436 were the 40 ALPHABETICALLY
    FIRST slugs, and came back as one market family. A seeded
    permutation spreads the sample across the bounded listing -- and
    establishes nothing about the venue beyond it."""

    def _cands(self, n=500):
        return probe_mod.candidates_from_listing(
            [{"slug": "fam%d-league-bavg-mkt%04d" % (i % 7, i),
              "active": True,
              "closed": False, "outcome": "yes"} for i in range(n)])

    def test_the_same_seed_gives_the_same_order(self):
        c = self._cands()
        a = probe_mod.order_candidates(c, seed="S1")["ordered"]
        b = probe_mod.order_candidates(list(reversed(c)), seed="S1")
        assert [x["slug"] for x in a] == [x["slug"] for x in b["ordered"]]

    def test_a_different_seed_gives_a_different_order(self):
        c = self._cands()
        a = probe_mod.order_candidates(c, seed="S1")["ordered"]
        b = probe_mod.order_candidates(c, seed="S2")["ordered"]
        assert [x["slug"] for x in a] != [x["slug"] for x in b]

    def test_it_is_a_permutation_and_loses_nothing(self):
        c = self._cands()
        o = probe_mod.order_candidates(c, seed="S1")
        assert sorted(x["slug"] for x in o["ordered"]) == \
            sorted(x["slug"] for x in c)
        assert o["population"] == len(c)

    def test_a_prefix_spreads_across_families_where_the_sort_did_not(self):
        c = self._cands()
        alphabetical = {probe_mod.family_of(x) for x in c[:40]}
        o = probe_mod.order_candidates(c, seed="S1")
        seeded = {probe_mod.family_of(x) for x in o["ordered"][:40]}
        assert len(alphabetical) == 1, "fixture no longer shows the defect"
        assert len(seeded) > 1

    def test_the_report_records_the_seed_and_refuses_the_claim(self):
        c = self._cands()
        o = probe_mod.order_candidates(c, seed="S1")
        rep = probe_mod.sample_report(o, [x["slug"] for x in
                                          o["ordered"][:40]])
        assert rep["seed"] == "S1"
        assert rep["sampled"] == 40 and rep["population"] == 500
        assert rep["coverage_of_listing"] == 0.08
        assert "representativeness" in rep["does_not_establish"]
        assert rep["establishes"] == "coverage within the bounded listing"


class TestTheSDKAddsNoHiddenRequests:
    """THE BUDGET MUST COVER OUTBOUND REQUESTS, NOT WRAPPER CALLS.

    One `markets.list` call is asserted here to be exactly one outbound
    HTTP request. If a future SDK adds pagination, retry or redirect
    following, that equality breaks and the listing budget silently
    undercounts again -- so it is checked against the INSTALLED
    package rather than remembered from a reading of it.
    """

    def _sdk(self):
        return pytest.importorskip("polymarket_us")

    def test_markets_list_has_no_internal_pagination(self):
        sdk = self._sdk()
        src = inspect.getsource(sdk.resources.Markets.list)
        assert "while" not in src and "for " not in src
        assert src.count("self._client.get") == 1

    def test_the_http_client_adds_no_retry_transport(self):
        sdk = self._sdk()
        src = inspect.getsource(sdk.client.PolymarketUS.__init__)
        assert "httpx.Client(" in src
        # httpx defaults to retries=0 and follow_redirects=False; what
        # matters is that the SDK does not override either.
        assert "retries" not in src and "transport" not in src

    def test_the_client_follows_no_redirects(self):
        sdk = self._sdk()
        src = inspect.getsource(sdk.client)
        assert "follow_redirects" not in src

    def test_a_429_is_raised_not_retried(self):
        sdk = self._sdk()
        src = inspect.getsource(sdk.client.PolymarketUS._request)
        assert "sleep" not in src
        assert "_handle_error_response" in src


class TestTheShutdownIsMeasuredNotInferred:
    """"The stream closed within 90 seconds" was argued from the
    ABSENCE of new journal rows. A quiet market produces exactly the
    same absence. These are positive observations, made by the process
    that did the closing and surviving its exit."""

    def test_a_stream_that_never_ran_does_not_report_a_close(self):
        """A THREAD THAT NEVER EXISTED IS NOT A CLOSED SOCKET.

        This test previously asserted `closed is True` here, which is
        the whole error: there was no connection, so there was nothing
        to close, and `close_latency_s` was a number measuring nothing.
        """
        s = ms.MarketStream("k", "s", autostart=False)
        rec = s.stop(wait_s=0.0)
        assert rec["shutdown"] == ms.MarketStream.SHUT_NEVER_STARTED
        assert rec["closed"] is False
        assert rec["thread_started"] is False
        assert rec["socket_close_returned"] is False
        # UNMEASURED is None, never zero.
        assert rec["close_latency_s"] is None
        assert rec["stop_requested_at_iso"]

    def test_a_join_that_times_out_reports_failure(self):
        """An unverified shutdown is a FAILURE, not a slow success."""
        import threading as _th
        s = ms.MarketStream("k", "s", autostart=False)
        hold = _th.Event()
        t = _th.Thread(target=hold.wait, daemon=True)
        t.start()
        s._thread = t
        try:
            rec = s.stop(wait_s=0.05)
            assert rec["shutdown"] == ms.MarketStream.SHUT_JOIN_TIMEOUT
            assert rec["closed"] is False and rec["thread_alive"] is True
            assert rec["socket_closed_at_iso"] is None
        finally:
            hold.set()
            t.join(timeout=2)

    def test_a_thread_that_exited_without_closing_is_not_CLOSED(self):
        """A thread can end without its socket having shut -- an
        exception on the way out, a close() that raised, a run that
        never connected. Thread exit is not proof."""
        s = ms.MarketStream("k", "s", autostart=False)
        s._thread = type("Dead", (), {"is_alive": lambda self: False})()
        s.thread_exited_at, s.thread_exited_at_iso = time.time(), "t"
        rec = s.stop(wait_s=0.0)
        assert rec["shutdown"] == ms.MarketStream.SHUT_NO_CLOSE
        assert rec["closed"] is False

    def test_a_close_that_raised_is_reported_as_incomplete(self):
        s = ms.MarketStream("k", "s", autostart=False)
        s._thread = type("Dead", (), {"is_alive": lambda self: False})()
        s.socket_closed_at = time.time()
        s.socket_closed_at_iso = "t"
        s.socket_close_ok = False
        s.socket_close_error = "ConnectionResetError"
        rec = s.stop(wait_s=0.0)
        assert rec["shutdown"] == ms.MarketStream.SHUT_CLOSE_FAILED
        assert rec["closed"] is False
        assert rec["socket_close_error"] == "ConnectionResetError"

    def test_only_a_returned_close_counts_as_CLOSED(self):
        s = ms.MarketStream("k", "s", autostart=False)
        s._thread = type("Dead", (), {"is_alive": lambda self: False})()
        s.stop_requested_at = time.time() - 1.5
        s.stop_requested_at_iso = "t0"
        s.socket_closed_at = time.time()
        s.socket_closed_at_iso = "t1"
        s.socket_close_ok = True
        rec = s.stop(wait_s=0.0)
        assert rec["shutdown"] == ms.MarketStream.SHUT_CLOSED
        assert rec["closed"] is True
        assert 1.0 < rec["close_latency_s"] < 3.0

    def test_a_frame_stamps_when_it_arrived(self):
        s = ms.MarketStream("k", "s", autostart=False)
        s.epoch += 1
        s.connected = True
        assert s.stats()["last_frame_at_iso"] is None
        s._on_market_data(md("m1", source_ts=fresh(-1)))
        assert s.stats()["last_frame_at_iso"] is not None

    def test_a_close_after_a_prior_disconnect_is_NOT_an_active_close(self):
        """THE CORRECTION.

        `ws.close()` returns cleanly on a socket that had ALREADY
        dropped -- the reconnect loop holds the `ws` object across a
        disconnect -- so a CLOSED verdict can follow a disconnect that
        happened minutes earlier. An unchanged epoch does not rule it
        out either: the epoch advances on a successful RECONNECT, so a
        socket that dropped and never came back leaves the epoch
        exactly where it was.
        """
        s = ms.MarketStream("k", "s", autostart=False)
        s._thread = type("Dead", (), {"is_alive": lambda self: False})()
        s.connected = False                  # already down
        s.epoch = 1                          # and the epoch never moved
        s.stop_requested_at = time.time() - 1.0
        s.stop_requested_at_iso = "t0"
        s.socket_closed_at = time.time()
        s.socket_closed_at_iso = "t1"
        s.socket_close_ok = True
        rec = s.stop(wait_s=0.0)
        # The close itself is recorded honestly...
        assert rec["shutdown"] == ms.MarketStream.SHUT_CLOSED
        assert rec["closed"] is True
        # ...and licenses NO active-close claim.
        assert rec["connected_at_stop"] is False
        assert rec["active_close_exercised"] is False
        assert "NOT EXERCISED" in rec["active_close_note"]

    def test_an_active_close_requires_a_live_connection(self):
        s = ms.MarketStream("k", "s", autostart=False)
        s._thread = type("Dead", (), {"is_alive": lambda self: False})()
        s.connected = True
        s.connected_since = "2026-09-22T12:00:00+00:00"
        s.epoch = 3
        s.reconnects = 2
        s.stop_requested_at = time.time() - 1.0
        s.stop_requested_at_iso = "t0"
        s.socket_closed_at = time.time()
        s.socket_closed_at_iso = "t1"
        s.socket_close_ok = True
        rec = s.stop(wait_s=0.0)
        assert rec["active_close_exercised"] is True
        assert rec["active_close_note"] is None
        assert rec["connected_at_stop"] is True
        assert rec["epoch_at_stop"] == 3
        assert rec["reconnects_at_stop"] == 2
        assert rec["connected_since"] == "2026-09-22T12:00:00+00:00"

    def test_connection_state_is_read_BEFORE_the_stop_flag_is_set(self):
        """Once `_stop` is true the thread tears the connection down, so
        a read taken after it would describe the shutdown rather than
        the state the shutdown found."""
        s = ms.MarketStream("k", "s", autostart=False)
        s.connected = True
        seen = {}

        real = ms.MarketStream.__dict__["stop"]

        class Spy(ms.MarketStream):
            pass

        # Flip `connected` to False the instant `_stop` is written, the
        # way the socket thread would.
        class _Flag:
            def __set__(self, obj, value):
                obj.__dict__["_stop"] = value
                if value:
                    obj.__dict__["connected"] = False

            def __get__(self, obj, owner=None):
                return obj.__dict__.get("_stop", False)

        Spy._stop = _Flag()
        s.__class__ = Spy
        rec = real(s, wait_s=0.0)
        seen["connected_at_stop"] = rec["connected_at_stop"]
        # If the read happened after the flag, this would be False.
        assert seen["connected_at_stop"] is True
        assert s.connected is False, "the flag did not take effect"

    def test_a_never_started_stream_exercises_no_active_close(self):
        s = ms.MarketStream("k", "s", autostart=False)
        rec = s.stop(wait_s=0.0)
        assert rec["shutdown"] == ms.MarketStream.SHUT_NEVER_STARTED
        assert rec["active_close_exercised"] is False
        assert "NOT EXERCISED" in rec["active_close_note"]

    def test_stats_keeps_socket_close_and_thread_exit_apart(self):
        s = ms.MarketStream("k", "s", autostart=False)
        s.stop(wait_s=0.0)
        st = s.stats()
        assert st["stop_requested_at_iso"]
        # NOTHING CLOSED, so nothing is claimed.
        assert st["socket_closed_at_iso"] is None
        assert st["close_latency_s"] is None
        assert "thread_exited_at_iso" in st

    def test_main_writes_a_durable_stop_receipt(self, monkeypatch,
                                                tmp_path):
        made = TestTheEntryPointLifecycle()._patch(
            monkeypatch, tmp_path,
            discovery={"ok": True, "slugs": ["m1"], "considered": 1,
                       "detail": [{"slug": "m1", "outcome_leg": "yes"}],
                       "observation": {
                           "slugs": ["m1"],
                           "detail": [{"slug": "m1",
                                       "strategy_admitted": True,
                                       "universe_rule_reason": None,
                                       "outcome_leg": "yes"}]},
                       "coverage": {"candidates": 1, "probed": 1}})
        pool = RunningControl()
        bl._reset_backoff()
        out = asyncio.run(bl.main(store=made["store"], control_pool=pool,
                                  run_for_s=0.05, sleep=SleepSpy()))
        # THE EXTERNAL CONSEQUENCE: a row in ingestion_state, written
        # by the process that shut the socket.
        written = [a for sql, a in pool.writes
                   if a and a[0] == ctl_mod.RECEIPT_KEY]
        assert written, "no stop receipt reached the database"
        body = json.loads(written[-1][1])
        assert body["boot_id"] == out["report"]["boot_id"]
        assert body["orders_submitted"] == 0
        assert body["transport"]["closed"] is True
        assert body["transport"]["waited_s"] == bl.STREAM_CLOSE_WAIT_S
        assert "acquisition" in body and "reserved" in body["acquisition"]

    def test_an_unwritable_receipt_does_not_change_the_shutdown(self):
        """The shutdown has already happened by the time this is
        called. A receipt that cannot be written is a warning, not a
        failure mode."""
        class _Dead:
            async def execute(self, *a):
                raise RuntimeError("no database")

        r = asyncio.run(ctl_mod.write_stop_receipt(_Dead(), {"a": 1}))
        assert r["written"] is False and r["error"] == "RuntimeError"
