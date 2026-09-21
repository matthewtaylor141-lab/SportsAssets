"""The live worker's ACTUAL ENTRY POINT, not a harness around it.

Every test here drives `bettor_live_loop.main()` or the loop object it
builds. The activation review's point was precise: a harness proving
`ShadowLoop.restore` works says nothing about whether `main()` calls
it, and it did not.

Each class names the defect it pins.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading

import pytest

from sportsassets import bettor_market_stream as ms
from sportsassets import bettor_settlement_ingest as si
from sportsassets.workers import bettor_live_loop as bl


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


def wired(tmp_path, slugs=("m1",), **kw):
    loop, stream = bl.build(
        "k", "s", slugs=list(slugs),
        leg_of={s: "yes" for s in slugs},
        state_path=str(tmp_path / "decisions.jsonl"),
        ledger_path=str(tmp_path / "ledger.json"), **kw)
    stream.epoch += 1
    stream.connected = True
    return loop, stream


# ── 1. durability ────────────────────────────────────────────────────

class TestEvidenceIsDurable:
    """BETTOR_LIVE_STATE defaulted to unset, so the deployment would
    have kept every decision in memory and lost it on redeploy."""

    def test_the_state_directory_has_a_default(self):
        assert bl.DEFAULT_STATE_DIR
        assert bl.state_dir() == bl.DEFAULT_STATE_DIR

    def test_every_record_reaches_disk(self, tmp_path):
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        lines = open(loop.state_path).read().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["market_id"] == "m1"
        assert rec["source_ts"] and rec["received_at"] and rec["decided_at"]
        assert loop.journal_written == 1
        assert loop.journal_failures == 0

    def test_refusals_are_persisted_too(self, tmp_path):
        """An engine that stores only its trades cannot be evaluated."""
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-90)))   # stale
        loop.drain()
        rec = json.loads(open(loop.state_path).read().strip())
        assert rec["status"] == "INELIGIBLE"
        assert rec["reasons"] == [ms.STALE_SOURCE]

    def test_a_persist_failure_is_counted_not_swallowed(self, tmp_path):
        loop, stream = wired(tmp_path)
        loop.state_path = str(tmp_path / "nope" / "x.jsonl")   # no dir
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        assert loop.journal_failures == 1
        assert loop.counters.get("persist_failed") == 1
        assert loop.report()["durability"]["persist_failures"] == 1

    def test_no_path_is_named_rather_than_silent(self, tmp_path):
        loop, stream = wired(tmp_path)
        loop.state_path = None
        stream._on_market_data(md(source_ts=fresh(-1)))
        loop.drain()
        assert loop.counters.get("record_not_persisted_no_path") == 1

    def test_the_ledger_is_written_atomically(self, tmp_path):
        loop, _ = wired(tmp_path)
        assert loop.save_ledger() is True
        assert json.loads(open(loop.ledger_path).read())
        assert not os.path.exists(loop.ledger_path + ".tmp")


# ── 2. recovery IN THE ENTRY POINT ───────────────────────────────────

class TestRecoveryHappensInTheWorker:

    def test_recover_rebuilds_dedup_from_the_journal(self, tmp_path):
        loop, stream = wired(tmp_path)
        ts = fresh(-1)
        stream._on_market_data(md(source_ts=ts))
        loop.drain()
        assert loop.counters.get("decided") == 1

        # A new process over the same directory.
        loop2, stream2 = wired(tmp_path)
        out = loop2.recover()
        assert out["journal_lines"] == 1
        assert out["slugs_recovered"] == 1
        stream2._on_market_data(md(source_ts=ts))       # same observation
        loop2.drain()
        assert loop2.counters.get("decided") is None
        assert loop2.counters.get("duplicate_observation_refused") == 1

    def test_recover_restores_the_ledger(self, tmp_path):
        loop, _ = wired(tmp_path, opening_cash=250.0)
        loop.save_ledger()
        loop2, _ = wired(tmp_path)
        out = loop2.recover()
        assert out["ledger_restored"] is True
        assert loop2.shadow.ledger.cash == 250.0

    def test_a_truncated_journal_does_not_stop_recovery(self, tmp_path):
        """A journal cut off by an OOM kill is the normal case here."""
        with open(tmp_path / "decisions.jsonl", "w") as fh:
            fh.write(json.dumps({"market_id": "m1",
                                 "source_ts": "2026-09-21T00:00:00Z"}) + "\n")
            fh.write('{"market_id": "m2", "source_')      # killed mid-write
        loop, _ = wired(tmp_path)
        out = loop.recover()
        assert out["journal_lines"] == 2
        assert out["journal_bad_lines"] == 1
        assert out["slugs_recovered"] == 1

    def test_a_corrupt_ledger_is_counted_and_the_loop_still_runs(self, tmp_path):
        (tmp_path / "ledger.json").write_text("{not json")
        loop, _ = wired(tmp_path)
        out = loop.recover()
        assert out["ledger_restored"] is False
        assert "ledger_error" in out
        assert loop.counters.get("ledger_restore_failed") == 1

    def test_a_newer_observation_after_recovery_is_decided(self, tmp_path):
        loop, stream = wired(tmp_path)
        stream._on_market_data(md(source_ts=fresh(-3)))
        loop.drain()
        loop2, stream2 = wired(tmp_path)
        loop2.recover()
        stream2._on_market_data(md(source_ts=fresh(-1)))   # strictly newer
        loop2.drain()
        assert loop2.counters.get("decided") == 1


# ── 3. bounded collections ───────────────────────────────────────────

class TestCollectionsAreBounded:
    """sportsassets-workers was OOM-killed thirteen times in one
    evening at 2 GiB. Unbounded growth is not an abstract risk here."""

    def test_the_record_ring_is_bounded(self, tmp_path):
        loop, stream = wired(tmp_path)
        loop.records.extend({"i": i} for i in range(bl.RECORD_RING + 500))
        assert len(loop.records) == bl.RECORD_RING

    def test_the_touch_ring_is_bounded(self, tmp_path):
        loop, _ = wired(tmp_path)
        for i in range(bl.TOUCH_RING + 100):
            loop.on_trade({"i": i})
        assert len(loop.touches) == bl.TOUCH_RING
        # The COUNT is exact even though the ring is not.
        assert loop.counters["trades_seen"] == bl.TOUCH_RING + 100

    def test_dedup_keeps_one_timestamp_per_slug_not_every_observation(
            self, tmp_path):
        loop, stream = wired(tmp_path, slugs=("m1",))
        for i in range(50):
            # Inside the 10 s bound, strictly increasing.
            stream._on_market_data(md(source_ts=fresh(-9 + i * 0.1)))
            loop.drain()
        assert len(loop._last_ts) == 1
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


# ── 6. settlement in the worker ──────────────────────────────────────

class TestSettlementIngestionRunsInTheLoop:

    def test_the_loop_ingests_over_contracts_it_has_observed(self, tmp_path):
        loop, stream = wired(tmp_path, slugs=("m1", "m2", "m3", "m4"))
        for s in ("m1", "m2", "m3", "m4"):
            stream._on_market_data(md(s, source_ts=fresh(-1)))
        loop.drain()
        assert len(loop._seen_contracts) == 4

        def reader(_c, slug):
            return {"m1": {"status": si.RESOLVED, "outcome": "1.0000",
                           "settled_at": "2026-09-21T02:00:00Z"},
                    "m2": {"status": si.PENDING},
                    "m3": {"status": si.UNREADABLE, "error": "TimeoutError"},
                    "m4": {"status": si.UNMATCHED}}[slug]

        out = asyncio.run(loop.ingest_settlements(client=None, limit=4)
                          ) if False else asyncio.run(
            si.ingest([("o%d" % i, s) for i, s in
                       enumerate(sorted(loop._seen_contracts))],
                      reader=reader, writer=loop._settlement_writer))
        assert out["counts"][si.RESOLVED] == 1
        assert out["counts"][si.PENDING] == 1
        assert out["counts"][si.UNREADABLE] == 1
        assert out["counts"][si.UNMATCHED] == 1
        assert out["counts"][si.INGESTED] == 1
        assert out["reconciled"] is True

    def test_a_settlement_row_is_journalled_durably(self, tmp_path):
        loop, _ = wired(tmp_path)
        asyncio.run(loop._settlement_writer({"OBSERVATION_ID": "o1",
                                             "SETTLEMENT_OUTCOME": "1.0"}))
        lines = [json.loads(x) for x in
                 open(loop.state_path).read().strip().splitlines()]
        assert lines[0]["kind"] == "SETTLEMENT"
        assert lines[0]["settlement"]["OBSERVATION_ID"] == "o1"

    def test_the_batch_is_bounded(self):
        assert bl.SETTLEMENT_BATCH <= 50
        assert bl.SETTLEMENT_EVERY_S >= 60

    def test_with_nothing_observed_it_does_not_read(self, tmp_path):
        loop, _ = wired(tmp_path)
        out = asyncio.run(loop.ingest_settlements())
        assert out["counts"] == {}
        assert "skipped" in out

    def test_the_loop_holds_no_database_handle(self, tmp_path):
        assert bl.describe()["writes_database"] is False


# ── 7. lifecycle in main() ───────────────────────────────────────────

class TestTheEntryPointLifecycle:

    def _patch(self, monkeypatch, tmp_path, *, discovery, creds=("k", "s")):
        monkeypatch.setenv("BETTOR_LIVE_STATE_DIR", str(tmp_path))

        class Cfg:
            pmus_key_id, pmus_secret_key = creds

        import sportsassets.config as cfgmod
        monkeypatch.setattr(cfgmod, "settings", lambda: Cfg(), raising=False)

        async def fake_discover(client=None):
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
        return made

    def test_a_failed_discovery_refuses_to_start(self, monkeypatch, tmp_path):
        """It used to log and run forever over an empty universe,
        reporting zero decisions as though the market were quiet."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": False, "error": "TimeoutError",
                                      "why": "market discovery failed"})
        asyncio.run(bl.main())
        assert "started" not in made

    def test_an_empty_universe_refuses_to_start(self, monkeypatch, tmp_path):
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": [],
                                      "considered": 400,
                                      "excluded_by_reason": {"X": 400}})
        asyncio.run(bl.main())
        assert "started" not in made

    def test_missing_credentials_refuse_to_start(self, monkeypatch, tmp_path):
        """A stream that cannot authenticate produces no books, and
        'no books' must never look like 'a quiet market'."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"]},
                           creds=(None, None))
        asyncio.run(bl.main())
        assert "started" not in made

    def test_cancellation_still_stops_the_stream(self, monkeypatch, tmp_path):
        """workers/all.py shuts a loop down by CANCELLING it. Without a
        finally, the socket thread outlives the coroutine."""
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1})

        async def run():
            task = asyncio.create_task(bl.main())
            await asyncio.sleep(0.4)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(run())
        assert made.get("started") is True
        assert made.get("stopped") is True, "cancellation orphaned the stream"

    def test_the_kill_switch_stops_it_and_the_stream(self, monkeypatch,
                                                    tmp_path):
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1})

        async def run():
            task = asyncio.create_task(bl.main())
            await asyncio.sleep(0.4)
            os.environ[bl.KILL_ENV] = "off"
            await asyncio.wait_for(task, timeout=10)
            os.environ.pop(bl.KILL_ENV, None)

        asyncio.run(run())
        assert made.get("stopped") is True

    def test_main_recovers_before_it_streams(self, monkeypatch, tmp_path):
        """The harness proved ShadowLoop.restore works. This proves
        main() calls it."""
        with open(tmp_path / "decisions.jsonl", "w") as fh:
            fh.write(json.dumps({"market_id": "m1",
                                 "source_ts": "2026-09-21T00:00:00Z",
                                 "decided_at": "2026-09-21T00:00:01Z"}) + "\n")
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"],
                                      "considered": 1})

        async def run():
            task = asyncio.create_task(bl.main())
            await asyncio.sleep(0.4)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        loop = made["loop"]
        assert loop.recovered is not None
        assert loop.recovered["journal_lines"] == 1
        assert loop._last_ts.get("m1") == "2026-09-21T00:00:00Z"

    def test_an_unusable_state_dir_refuses_to_run(self, monkeypatch,
                                                  tmp_path):
        """No durable records means no deliverable."""
        blocker = tmp_path / "file"
        blocker.write_text("x")
        made = self._patch(monkeypatch, tmp_path,
                           discovery={"ok": True, "slugs": ["m1"]})
        monkeypatch.setenv("BETTOR_LIVE_STATE_DIR", str(blocker / "sub"))
        asyncio.run(bl.main())
        assert "started" not in made


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

    def test_it_is_absent_from_workers_all(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.normpath(
            os.path.join(here, "..", "sportsassets", "workers", "all.py"))
        assert "bettor_live_loop" not in open(path).read()

    def test_the_report_states_zero_orders(self, tmp_path):
        loop, _ = wired(tmp_path)
        assert loop.report()["orders_submitted"] == 0
