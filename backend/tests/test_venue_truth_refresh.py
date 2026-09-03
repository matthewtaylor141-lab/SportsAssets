"""Venue-truth refresh: a failed build is published, bounded and backed
off — never an endless 'building' (task #15, 2026-09-03).

The old _refresh reset `building` in a bare finally: any exception
inside _build left payload None, every later request re-kicked a build
and answered "building (first request since boot)" — 13/13 probes since
Sep 2, last built 2026-08-24. These tests pin the four properties that
make the failure visible instead: the error is named by stage in the
snapshot, a hanging read times out under its own constant, a failed
build backs off, and the PM side states the coverage it actually holds.
"""

import asyncio
import inspect
import logging
import re
import time

import pytest

from sportsassets.api import pmus_account
from sportsassets.api import venue_truth as vt


# ── fixtures ────────────────────────────────────────────────────────────

def _pm_trade(slug, qty, px, when, side="SIDE_BUY", rp=0.0):
    return {"type": "ACTIVITY_TYPE_TRADE", "createTime": when,
            "trade": {"marketSlug": slug, "qty": qty, "price": px,
                      "side": side, "realizedPnl": rp}}


def _pm_resolution(slug, realized, cost, when):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "createTime": when,
            "positionResolution": {
                "marketSlug": slug,
                "afterPosition": {"realized": {"value": realized}},
                "beforePosition": {"cost": {"value": cost}}}}


def _iso_ago(seconds: float) -> str:
    return vt._iso_utc(time.time() - seconds)


@pytest.fixture
def fresh(monkeypatch):
    """A cold snapshot, no DB, an instant Kalshi read and a two-row PM
    crawl in the rolling window; each test overrides what it breaks."""
    monkeypatch.setattr(vt, "_snap", vt._new_snap())
    monkeypatch.setattr(vt, "_bg_tasks", set())

    async def fake_kx():
        return ({"fills": [], "settlements": []}, None)

    async def fake_acts(since_day, timeout=240):
        return [_pm_trade("mlb-a", 100, 0.40, _iso_ago(2 * 86_400)),
                _pm_resolution("mlb-a", 60.0, 40.0, _iso_ago(86_400))]

    async def no_db(*a, **k):
        return []

    monkeypatch.setattr(vt, "_kalshi_raw_from_heartbeat", fake_kx)
    monkeypatch.setattr(pmus_account, "_week_activities", fake_acts)
    monkeypatch.setattr(vt, "_persist_days", no_db)
    monkeypatch.setattr(vt, "_frozen_days", no_db)
    return monkeypatch


async def _settle():
    """Let the background build finish (the task set drains)."""
    for _ in range(200):
        if not vt._bg_tasks and not vt._snap.get("building"):
            return
        await asyncio.sleep(0.005)
    raise AssertionError("background build never finished")


# ── 1. a raising build is published and backs off ───────────────────────

class TestFailurePublished:
    async def test_raising_build_publishes_named_error_and_stops_rekicking(
            self, fresh, caplog):
        calls = {"n": 0}

        async def boom():
            calls["n"] += 1
            raise RuntimeError("boom")

        fresh.setattr(vt, "_build", boom)
        with caplog.at_level(logging.ERROR, logger="sportsassets.api.venue_truth"):
            first = await vt.snapshot()
            assert first["building"] is True
            assert first["built_at"] is None
            await _settle()
            out = await vt.snapshot()
        assert out["building"] is False
        assert out["built_at"] is None
        assert out["error"] == {"stage": "build", "type": "RuntimeError",
                                "detail": "RuntimeError('boom')"}
        assert out["attempts"] == 1
        assert 0 < out["next_attempt_s"] <= vt._RETRY_MIN_S
        # The failure is logged with its traceback, not swallowed.
        assert any("stage build" in r.getMessage() and r.exc_info
                   for r in caplog.records)
        # Inside the backoff every further request reads the same
        # error and kicks nothing.
        for _ in range(5):
            again = await vt.snapshot()
            assert again["error"]["type"] == "RuntimeError"
            assert again["building"] is False
        await asyncio.sleep(0.01)
        assert calls["n"] == 1
        # Once the backoff has elapsed the next request re-kicks once.
        vt._snap["last_attempt"] -= vt._RETRY_MIN_S + 1
        kicked = await vt.snapshot()
        assert kicked["building"] is True
        await _settle()
        assert calls["n"] == 2
        out2 = await vt.snapshot()
        assert out2["attempts"] == 2
        # Doubling: the second failure waits longer than the first.
        assert out2["next_attempt_s"] > vt._RETRY_MIN_S

    async def test_stage_error_names_the_read_that_failed(self, fresh):
        async def bad_heartbeat():
            raise ConnectionResetError("pool gone")

        fresh.setattr(vt, "_kalshi_raw_from_heartbeat", bad_heartbeat)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert out["error"]["stage"] == "heartbeat"
        assert out["error"]["type"] == "ConnectionResetError"
        assert "pool gone" in out["error"]["detail"]
        assert out["attempts"] == 1

    async def test_detail_is_bounded_to_300_chars(self, fresh):
        async def bad_heartbeat():
            raise ValueError("x" * 2000)

        fresh.setattr(vt, "_kalshi_raw_from_heartbeat", bad_heartbeat)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert len(out["error"]["detail"]) == 300

    def test_backoff_doubles_and_caps(self):
        assert vt._backoff_s(0) == 0.0
        assert vt._backoff_s(1) == vt._RETRY_MIN_S
        assert vt._backoff_s(2) == 2 * vt._RETRY_MIN_S
        assert vt._backoff_s(50) == vt._RETRY_MAX_S


# ── 2. a hanging heartbeat read times out under its own constant ────────

class TestBoundedReads:
    async def test_hanging_heartbeat_times_out_with_stage_heartbeat(
            self, fresh):
        async def hang():
            await asyncio.Event().wait()

        fresh.setattr(vt, "_kalshi_raw_from_heartbeat", hang)
        fresh.setattr(vt, "_HEARTBEAT_TIMEOUT_S", 0.05)
        t0 = time.monotonic()
        await vt.snapshot()
        await _settle()
        assert time.monotonic() - t0 < 2.0
        out = await vt.snapshot()
        assert out["building"] is False
        assert out["error"]["stage"] == "heartbeat"
        assert out["error"]["type"] == "TimeoutError"
        assert "0.05s" in out["error"]["detail"]
        assert out["attempts"] == 1
        assert out["next_attempt_s"] > 0

    async def test_hanging_pm_crawl_is_partial_and_named(self, fresh):
        async def hang(since_day, timeout=240):
            await asyncio.Event().wait()

        fresh.setattr(pmus_account, "_week_activities", hang)
        fresh.setattr(vt, "_PM_CRAWL_TIMEOUT_S", 0.05)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        # The build landed: Kalshi served, the PM side names its stage.
        assert "error" not in out
        assert out["partial"] is True
        assert out["polymarket_us"]["error"].startswith("pm_crawl TimeoutError")
        assert out["pm_coverage"]["error"]["stage"] == "pm_crawl"
        assert out["pm_coverage"]["error"]["type"] == "TimeoutError"
        assert out["pm_coverage"]["rows"] == 0
        assert out["kalshi"]["settled"] == 0

    async def test_raising_fold_is_named_by_stage(self, fresh):
        def bad_fold(acts):
            raise KeyError("marketSlug")

        fresh.setattr(vt, "pm_positions", bad_fold)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert out["error"]["stage"] == "pm_positions"
        assert out["error"]["type"] == "KeyError"


# ── 3. a successful build after a failure clears the error ──────────────

class TestRecovery:
    async def test_success_after_failure_clears_error(self, fresh):
        state = {"fail": True}

        async def flaky_kx():
            if state["fail"]:
                raise OSError("db down")
            return ({"fills": [], "settlements": []}, None)

        fresh.setattr(vt, "_kalshi_raw_from_heartbeat", flaky_kx)
        await vt.snapshot()
        await _settle()
        failed = await vt.snapshot()
        assert failed["error"]["stage"] == "heartbeat"
        assert failed["attempts"] == 1

        state["fail"] = False
        vt._snap["last_attempt"] -= vt._RETRY_MAX_S + 1
        assert (await vt.snapshot())["building"] is True
        await _settle()
        out = await vt.snapshot()
        assert "error" not in out
        assert "next_attempt_s" not in out
        assert out.get("building") is None
        assert out["built_at"] == vt._snap["ts"]
        assert out["built_at"] > 0
        assert vt._snap["attempts"] == 0
        assert out["total"]["settled"] == 1
        assert out["total"]["realized"] == 60.0

    async def test_failed_refresh_serves_stale_payload_with_the_error(
            self, fresh):
        await vt.snapshot()
        await _settle()
        good = await vt.snapshot()
        assert good["total"]["settled"] == 1
        assert good["built_at"] == vt._snap["ts"]

        async def bad_kx():
            raise OSError("db down")

        fresh.setattr(vt, "_kalshi_raw_from_heartbeat", bad_kx)
        vt._snap["ts"] -= vt._TTL_S + 1        # past TTL: refresh due
        built_at = vt._snap["ts"]              # the aged build's stamp
        served = await vt.snapshot()
        assert served["total"]["settled"] == 1   # stale payload served
        await _settle()
        out = await vt.snapshot()
        assert out["total"]["settled"] == 1
        assert out["built_at"] == built_at
        assert out["error"]["stage"] == "heartbeat"
        assert out["attempts"] == 1
        assert out["next_attempt_s"] > 0


# ── 4. the task set keeps a strong reference ────────────────────────────

class TestTaskReference:
    async def test_task_is_held_until_done_then_discarded(self, fresh):
        gate = asyncio.Event()

        async def gated():
            await gate.wait()
            return await vt._build_orig()

        fresh.setattr(vt, "_build_orig", vt._build, raising=False)
        fresh.setattr(vt, "_build", gated)
        first = await vt.snapshot()
        assert first["building"] is True
        assert len(vt._bg_tasks) == 1
        task = next(iter(vt._bg_tasks))
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        # A second request while building kicks nothing new.
        await vt.snapshot()
        assert len(vt._bg_tasks) == 1
        gate.set()
        await _settle()
        assert task.done() and task.exception() is None
        assert task not in vt._bg_tasks
        assert (await vt.snapshot())["total"]["settled"] == 1


# ── 5. coverage is computed from the rows actually held ─────────────────

class TestCoverage:
    NOW = 1_800_000_000.0     # 2027-01-15T08:00:00Z

    def test_uncapped_crawl_inside_window_states_its_span(self):
        since = "2027-01-02"
        rows = [_pm_trade("a", 1, 0.5, vt._iso_utc(self.NOW - 3 * 86_400)),
                _pm_trade("b", 1, 0.5, vt._iso_utc(self.NOW - 5 * 86_400)),
                _pm_resolution("a", 1.0, 0.5,
                               vt._iso_utc(self.NOW - 86_400))]
        cov = vt.pm_coverage(rows, since, now=self.NOW, row_cap=8000)
        assert cov["rows"] == 3
        assert cov["row_cap"] == 8000
        assert cov["rows_capped"] is False
        assert cov["window_reached"] is False
        assert cov["oldest"] == vt._iso_utc(self.NOW - 5 * 86_400)
        assert cov["oldest_ts"] == pytest.approx(self.NOW - 5 * 86_400)
        assert cov["newest"] == vt._iso_utc(self.NOW - 86_400)
        assert cov["coverage_days"] == 5.0
        assert cov["window_start"] == since
        assert cov["window_days"] == pytest.approx(
            (self.NOW - vt._et_midnight_ts(since)) / 86_400, abs=0.01)
        assert cov["undated_rows"] == 0

    def test_capped_crawl_short_of_the_window_is_flagged(self):
        since = "2027-01-02"
        rows = [_pm_trade(f"m{i}", 1, 0.5,
                          vt._iso_utc(self.NOW - i * 3600))
                for i in range(1, 4)]
        cov = vt.pm_coverage(rows, since, now=self.NOW, row_cap=3)
        assert cov["rows"] == 3
        assert cov["rows_capped"] is True
        assert cov["window_reached"] is False
        assert cov["oldest"] == vt._iso_utc(self.NOW - 3 * 3600)
        assert cov["coverage_days"] == pytest.approx(3 / 24, abs=0.01)
        assert cov["coverage_days"] < cov["window_days"]

    def test_crawl_that_reaches_the_window_start_is_full_coverage(self):
        since = "2027-01-02"
        start = vt._et_midnight_ts(since)
        rows = [_pm_trade("a", 1, 0.5, vt._iso_utc(start - 60)),
                _pm_trade("b", 1, 0.5, vt._iso_utc(self.NOW - 60))]
        cov = vt.pm_coverage(rows, since, now=self.NOW, row_cap=2)
        assert cov["window_reached"] is True
        # At the cap but past the window start: nothing is missing.
        assert cov["rows_capped"] is False
        assert cov["coverage_days"] == cov["window_days"]

    def test_no_rows_and_undated_rows_fail_closed(self):
        empty = vt.pm_coverage([], "2027-01-02", now=self.NOW, row_cap=10)
        assert empty["rows"] == 0
        assert empty["rows_capped"] is False
        assert empty["window_reached"] is False
        assert empty["oldest"] is None
        assert empty["coverage_days"] == 0.0
        undated = [{"type": "ACTIVITY_TYPE_TRADE",
                    "trade": {"marketSlug": "x", "qty": 1, "price": 0.5}}]
        cov = vt.pm_coverage(undated, "2027-01-02", now=self.NOW, row_cap=1)
        assert cov["undated_rows"] == 1
        assert cov["oldest"] is None
        assert cov["rows_capped"] is True     # at the cap, span unknown
        assert cov["coverage_days"] == 0.0

    def test_row_cap_matches_the_crawl_it_describes(self):
        # The cap is pmus_account._fetch_week_activities_sync's own
        # literals (80 pages x 100 rows); the constant here must track
        # them, or the coverage block would flag the wrong ceiling.
        # Whitespace-tolerant on purpose: pmus_account.py is another
        # builder's file and moves; a reformat must not flap this pin,
        # only a change of the literals themselves may.
        src = inspect.getsource(pmus_account._fetch_week_activities_sync)
        pages = int(re.search(r"for\s+_\s+in\s+range\(\s*(\d+)\s*\)", src)
                    .group(1))
        per_page = int(re.search(r'"limit"\s*:\s*(\d+)', src).group(1))
        assert vt._PM_CRAWL_PAGES == pages
        assert vt._PM_CRAWL_PAGE_ROWS == per_page
        assert vt._PM_CRAWL_ROW_CAP == pages * per_page == 8000

    async def test_built_payload_carries_coverage_and_partial_when_capped(
            self, fresh):
        fresh.setattr(vt, "_PM_CRAWL_ROW_CAP", 2)   # the fixture crawl is 2 rows
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        cov = out["pm_coverage"]
        assert cov["rows"] == 2
        assert cov["row_cap"] == 2
        assert cov["rows_capped"] is True
        assert cov["window_reached"] is False
        assert cov["oldest"] is not None
        assert cov["coverage_days"] == pytest.approx(2.0, abs=0.01)
        assert cov["window_days"] > cov["coverage_days"]
        assert out["partial"] is True
        # The figures themselves are still the rows' own truth.
        assert out["total"]["settled"] == 1
        assert out["total"]["realized"] == 60.0

    async def test_built_payload_uncapped_is_not_partial(self, fresh):
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert out["pm_coverage"]["rows_capped"] is False
        assert out["pm_coverage"]["rows"] == 2
        assert out["partial"] is False


# ── 6. review round 1: the history stage, the whole-build ceiling, the
#      partial edge day, and the minors folded with them ────────────────

async def _settle_building():
    """Wait for `building` to reset only — the task set may still hold
    an abandoned build (by design) so _settle() would never return."""
    for _ in range(400):
        if not vt._snap.get("building"):
            return
        await asyncio.sleep(0.005)
    raise AssertionError("building never reset")


class TestHistoryStageBounded:
    """Blocking (x2): _persist_days / _frozen_days ran with no ceiling
    after the two bounded stages; asyncpg 0.31 acquires with
    timeout=None, so a saturated pool reproduced 'building (first
    request since boot)' forever with error None and attempts 0."""

    async def test_hanging_persist_days_is_bounded_and_served_partial(
            self, fresh):
        async def hang(*a, **k):
            await asyncio.Event().wait()

        fresh.setattr(vt, "_persist_days", hang)
        fresh.setattr(vt, "_HISTORY_TIMEOUT_S", 0.05)
        t0 = time.monotonic()
        first = await vt.snapshot()
        assert first["building"] is True
        await _settle()
        assert time.monotonic() - t0 < 2.0
        out = await vt.snapshot()
        # The build LANDED: the live payload serves, history is named.
        assert out.get("building") is None
        assert out["built_at"] > 0
        assert "error" not in out
        assert out["history_error"].startswith("history TimeoutError")
        assert "history persist exceeded 0.05s" in out["history_error"]
        assert "frozen_days" not in out
        assert out["total"]["settled"] == 1
        assert vt._snap["attempts"] == 0
        # And the mechanism is not stuck: past TTL the next request
        # kicks a fresh build.
        vt._snap["ts"] -= vt._TTL_S + 1
        await vt.snapshot()
        assert vt._snap["building"] is True
        await _settle()
        assert vt._snap["building"] is False

    async def test_hanging_frozen_days_is_bounded_with_its_own_label(
            self, fresh):
        async def hang(*a, **k):
            await asyncio.Event().wait()

        fresh.setattr(vt, "_frozen_days", hang)
        fresh.setattr(vt, "_HISTORY_TIMEOUT_S", 0.05)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert "history read exceeded 0.05s" in out["history_error"]
        assert out["total"]["settled"] == 1

    async def test_raising_history_read_is_partial_and_named(self, fresh):
        async def boom(*a, **k):
            raise ConnectionResetError("pool gone")

        fresh.setattr(vt, "_frozen_days", boom)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert out["history_error"] == (
            "history ConnectionResetError: pool gone")
        assert "error" not in out

    def test_every_db_await_in_build_is_under_bounded(self):
        # Source pin: the four DB reads inside _build all go through
        # _bounded(...). A bare `await _persist_days(` / `await
        # _frozen_days(` / `await _kalshi_raw_from_heartbeat(` is the
        # defect this class exists for.
        src = inspect.getsource(vt._build)
        for name in ("_kalshi_raw_from_heartbeat(", "_week_activities(",
                     "_persist_days(", "_frozen_days("):
            assert f"await {name}" not in src, name
            assert name in src, name
        assert src.count("_bounded(") == 4


class TestWholeBuildCeiling:
    """The backstop in _refresh: no stage may leave `building` stuck,
    and — because asyncpg's cancel handshake can overrun wait_for's own
    ceiling — the inner task is abandoned, not awaited."""

    def test_build_ceiling_sits_above_the_stage_ceilings(self):
        stages = (vt._HEARTBEAT_TIMEOUT_S + vt._PM_CRAWL_TIMEOUT_S
                  + 2 * vt._HISTORY_TIMEOUT_S)
        assert vt._BUILD_TIMEOUT_S > stages
        assert vt._BUILD_TIMEOUT_S < vt._RETRY_MAX_S

    async def test_a_build_that_ignores_cancellation_is_abandoned_and_published(
            self, fresh, caplog):
        gate = asyncio.Event()
        state = {"cancelled": 0}

        async def stuck_build():
            # A read whose cancel path itself hangs (the asyncpg
            # release/cancel round trip): swallow the cancel and keep
            # waiting on something nobody will set until the test does.
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                state["cancelled"] += 1
            await gate.wait()
            raise RuntimeError("late failure after the ceiling")

        fresh.setattr(vt, "_build", stuck_build)
        fresh.setattr(vt, "_BUILD_TIMEOUT_S", 0.05)
        t0 = time.monotonic()
        with caplog.at_level(logging.WARNING,
                             logger="sportsassets.api.venue_truth"):
            first = await vt.snapshot()
            assert first["building"] is True
            await _settle_building()
            assert time.monotonic() - t0 < 2.0
            out = await vt.snapshot()
            assert out["building"] is False
            assert out["built_at"] is None
            assert out["error"]["stage"] == "build"
            assert out["error"]["type"] == "TimeoutError"
            assert "build exceeded 0.05s" in out["error"]["detail"]
            assert out["attempts"] == 1
            assert out["next_attempt_s"] > 0
            # Published from outside any except block, still with a
            # traceback slot filled (exc_info=exc, not logger.exception).
            rec = next(r for r in caplog.records
                       if "stage build" in r.getMessage())
            assert rec.exc_info and rec.exc_info[0] is TimeoutError
            # The abandoned build was cancelled once, is still alive,
            # and is held by a strong reference while it winds down.
            await asyncio.sleep(0.01)
            assert state["cancelled"] == 1
            abandoned = [t for t in vt._bg_tasks if not t.done()]
            assert len(abandoned) == 1
            # Inside the backoff nothing re-kicks; after it, one build.
            assert (await vt.snapshot())["building"] is False
            vt._snap["last_attempt"] -= vt._RETRY_MIN_S + 1
            fresh.setattr(vt, "_build", vt._build_orig, raising=False) \
                if hasattr(vt, "_build_orig") else None
            kicked = await vt.snapshot()
            assert kicked["building"] is True
            # Release the stuck build: its late exception is reaped
            # (logged, never 'Task exception was never retrieved') and
            # the strong ref dropped.
            gate.set()
            await asyncio.sleep(0.02)
            assert abandoned[0].done()
            assert abandoned[0] not in vt._bg_tasks
            assert any("ended late with RuntimeError" in r.getMessage()
                       for r in caplog.records)

    async def test_cancelled_refresh_resets_building_and_abandons_inner(
            self, fresh):
        gate = asyncio.Event()

        async def gated():
            await gate.wait()
            return await vt._build_orig()

        fresh.setattr(vt, "_build_orig", vt._build, raising=False)
        fresh.setattr(vt, "_build", gated)
        await vt.snapshot()
        task = next(iter(vt._bg_tasks))
        await asyncio.sleep(0)            # let _refresh take its first step
        task.cancel()
        await asyncio.sleep(0.01)
        assert task.cancelled()
        assert vt._snap["building"] is False
        gate.set()
        await asyncio.sleep(0.02)
        assert not vt._bg_tasks

    async def test_refresh_cancelled_before_its_first_step_resets_building(
            self, fresh):
        # A task cancelled before the loop ever enters it runs no
        # try/finally at all; the kick's done-callback covers that.
        await vt.snapshot()
        assert vt._snap["building"] is True
        task = next(iter(vt._bg_tasks))
        task.cancel()                     # no yield in between
        await asyncio.sleep(0.01)
        assert task.cancelled()
        assert vt._snap["building"] is False
        assert not vt._bg_tasks
        assert vt._snap["last_attempt"] > 0
        # The next request kicks normally.
        assert (await vt.snapshot())["building"] is True
        await _settle()
        assert (await vt.snapshot())["total"]["settled"] == 1


class TestPartialEdgeDayNeverFrozen:
    """Major: a capped crawl froze the ET day holding its oldest row —
    partial by construction — into venue_truth_days as a complete row.
    Only days strictly after that day and strictly before today are
    provably complete when the window was not reached."""

    NOW = 1_800_000_000.0     # 2027-01-15T08:00:00Z = 03:00 ET, Jan 15

    def test_pm_persist_bounds(self):
        since = "2027-01-02"
        # Window reached: the whole window, re-upserted every build.
        assert vt.pm_persist_bounds(
            {"window_reached": True, "oldest_ts": 1.0}, since,
            now=self.NOW) == (since, None)
        # No dated row: nothing is provable, nothing may write.
        assert vt.pm_persist_bounds({"window_reached": False,
                                     "oldest_ts": None}, since,
                                    now=self.NOW) is None
        assert vt.pm_persist_bounds({"rows": 0}, since, now=self.NOW) is None
        # Capped inside the window: the oldest row's ET day is partial;
        # the range is the day after it up to (excluding) today.
        oldest = vt._et_midnight_ts("2027-01-10") + 18 * 3600  # 18:00 ET
        assert vt.pm_persist_bounds(
            {"window_reached": False, "oldest_ts": oldest},
            since, now=self.NOW) == ("2027-01-11", "2027-01-15")
        # Oldest row on the window start day, window not reached: the
        # first day is the next one, never below `since`.
        oldest = vt._et_midnight_ts(since) + 60
        assert vt.pm_persist_bounds(
            {"window_reached": False, "oldest_ts": oldest},
            since, now=self.NOW) == ("2027-01-03", "2027-01-15")
        # Oldest row yesterday: only today is fully held, and today is
        # still in progress — nothing provable. Same for today.
        for day in ("2027-01-14", "2027-01-15"):
            oldest = vt._et_midnight_ts(day) + 60
            assert vt.pm_persist_bounds(
                {"window_reached": False, "oldest_ts": oldest},
                since, now=self.NOW) is None

    def test_persistable_day_rows_honours_before_day(self):
        pm = [{"market_slug": f"m{i}", "cost": 1.0, "realized": 0.5,
               "settled": True, "open": False,
               "settled_at": vt._et_midnight_ts(f"2027-01-1{i}") + 3600}
              for i in range(0, 4)]          # Jan 10..13
        days = [t[0] for t in vt.persistable_day_rows(
            pm, "polymarket-us", "2027-01-11", before_day="2027-01-13")]
        assert days == ["2027-01-12", "2027-01-11"]
        # No upper bound by default (the Kalshi/original semantics).
        assert len(vt.persistable_day_rows(pm, "polymarket-us",
                                           "2027-01-02")) == 4

    async def test_persist_days_honours_the_pm_bounds(self, monkeypatch):
        captured = {}

        class _Pool:
            async def executemany(self, sql, tuples):
                captured["tuples"] = list(tuples)

        async def fake_pool():
            return _Pool()

        from sportsassets import db
        monkeypatch.setattr(db, "get_pool", fake_pool)
        pm = [{"market_slug": f"m{i}", "cost": 1.0, "realized": 0.5,
               "settled": True, "open": False,
               "settled_at": vt._et_midnight_ts(f"2027-01-1{i}") + 3600}
              for i in range(0, 4)]          # Jan 10..13
        kx = [{"ticker": "K", "cost": 1.0, "realized": 0.5, "settled": True,
               "open": False, "settled_at": vt._et_midnight_ts("2027-01-10")
               + 3600, "window_complete": True}]
        await vt._persist_days(pm, kx, "2027-01-02", pm_ok=True, kx_ok=True,
                               pm_days=("2027-01-11", "2027-01-13"))
        days = sorted((t[0], t[1]) for t in captured["tuples"])
        # Kalshi keeps the whole window; PM only its provable range.
        assert days == [("2027-01-10", "kalshi"),
                        ("2027-01-11", "polymarket-us"),
                        ("2027-01-12", "polymarket-us")]

    async def test_capped_crawl_never_hands_the_oldest_rows_day_to_the_upsert(
            self, fresh):
        # The reviewer's repro, one day deeper: the crawl's reach holds
        # day-2 from 18:00 ET onward — three resolutions that evening,
        # two on day-1, one at 01:00 ET today; cap = rows held.
        now = time.time()
        today = vt._et_day(now)
        mid_today = vt._et_midnight_ts(today)
        acts = []
        for i, off in enumerate((30 * 3600, 28 * 3600, 26 * 3600)):
            ts = mid_today - off                           # day-2 evening
            acts.append(_pm_trade(f"y{i}", 10, 0.5, vt._iso_utc(ts - 60)))
            acts.append(_pm_resolution(f"y{i}", 3.0, 5.0, vt._iso_utc(ts)))
        for i, off in enumerate((20 * 3600, 6 * 3600)):
            ts = mid_today - off                           # day-1
            acts.append(_pm_resolution(f"d{i}", 1.0, 5.0, vt._iso_utc(ts)))
        acts.append(_pm_resolution("t0", -2.0, 5.0,
                                   vt._iso_utc(mid_today + 60)))
        day_2 = vt._et_day(mid_today - 28 * 3600)
        day_1 = vt._et_day(mid_today - 6 * 3600)
        if len({day_2, day_1, today}) != 3:
            pytest.skip("DST edge")

        async def fake_acts(since_day, timeout=240):
            return acts

        captured = {}

        async def spy_persist(pm_rows, kx_rows, since, pm_ok, kx_ok,
                              pm_days=None):
            first, before = pm_days if pm_days else (since, None)
            captured.update(pm_ok=pm_ok, pm_days=pm_days, tuples=(
                vt.persistable_day_rows(pm_rows, "polymarket-us", first,
                                        before_day=before) if pm_ok else []))

        fresh.setattr(pmus_account, "_week_activities", fake_acts)
        fresh.setattr(vt, "_persist_days", spy_persist)
        fresh.setattr(vt, "_PM_CRAWL_ROW_CAP", len(acts))
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        cov = out["pm_coverage"]
        assert cov["rows_capped"] is True and cov["window_reached"] is False
        assert vt._et_day(cov["oldest_ts"]) == day_2
        assert out["partial"] is True
        # day-2 — the tail the crawl held — is NOT handed to the upsert,
        # nor is today (in progress); day-1, fully held, is.
        assert captured["pm_ok"] is True
        assert captured["pm_days"] == (day_1, today)
        assert cov["persist_from"] == day_1
        assert cov["persist_before"] == today
        assert [t[0] for t in captured["tuples"]] == [day_1]
        assert captured["tuples"][0][2] == 2          # both day-1 rows
        assert day_2 in out["history_note"]
        assert "partial and not written" in out["history_note"]
        # The live figures are still the rows' own truth.
        assert out["total"]["settled"] == 6

    async def test_a_crawl_whose_oldest_row_is_yesterday_writes_no_pm_day(
            self, fresh):
        # Reach under a day: the only day fully held is today, which is
        # still in progress and might never be re-written; nothing may
        # be frozen.
        now = time.time()
        today = vt._et_day(now)
        mid_today = vt._et_midnight_ts(today)
        acts = [_pm_trade("a", 1, 0.5, vt._iso_utc(mid_today - 3600)),
                _pm_resolution("a", 1.0, 0.5, vt._iso_utc(mid_today + 60))]

        async def fake_acts(since_day, timeout=240):
            return acts

        captured = {}

        async def spy_persist(pm_rows, kx_rows, since, pm_ok, kx_ok,
                              pm_days=None):
            captured.update(pm_ok=pm_ok, pm_days=pm_days)

        fresh.setattr(pmus_account, "_week_activities", fake_acts)
        fresh.setattr(vt, "_persist_days", spy_persist)
        fresh.setattr(vt, "_PM_CRAWL_ROW_CAP", 2)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert captured["pm_ok"] is False
        assert captured["pm_days"] is None
        assert out["pm_coverage"]["persist_from"] is None
        assert "for no day only" in out["history_note"]

    async def test_a_crawl_that_reaches_the_window_writes_the_whole_window(
            self, fresh):
        # The fixture crawl (2 rows, no cap) does not reach the window
        # start; make it: one row before the window opens.
        since = vt._since_day()
        start = vt._et_midnight_ts(since)

        async def fake_acts(since_day, timeout=240):
            return [_pm_trade("old", 1, 0.5, vt._iso_utc(start - 60)),
                    _pm_trade("mlb-a", 100, 0.40, _iso_ago(2 * 86_400)),
                    _pm_resolution("mlb-a", 60.0, 40.0, _iso_ago(86_400))]

        captured = {}

        async def spy_persist(pm_rows, kx_rows, since, pm_ok, kx_ok,
                              pm_days=None):
            captured.update(pm_ok=pm_ok, pm_days=pm_days, since=since)

        fresh.setattr(pmus_account, "_week_activities", fake_acts)
        fresh.setattr(vt, "_persist_days", spy_persist)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert out["pm_coverage"]["window_reached"] is True
        assert captured["pm_ok"] is True
        assert captured["pm_days"] == (since, None)
        assert captured["since"] == since
        assert out["pm_coverage"]["persist_from"] == since
        assert out["pm_coverage"]["persist_before"] is None
        assert "history_note" not in out

    async def test_a_failed_crawl_persists_no_pm_day_and_no_note(self, fresh):
        async def boom(since_day, timeout=240):
            raise OSError("venue down")

        captured = {}

        async def spy_persist(pm_rows, kx_rows, since, pm_ok, kx_ok,
                              pm_days=None):
            captured.update(pm_ok=pm_ok, pm_days=pm_days)

        fresh.setattr(pmus_account, "_week_activities", boom)
        fresh.setattr(vt, "_persist_days", spy_persist)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert captured["pm_ok"] is False
        assert out["pm_coverage"]["persist_from"] is None
        assert "history_note" not in out


class TestMinorsFolded:
    async def test_undated_rows_below_the_cap_are_partial_with_a_note(
            self, fresh):
        async def undated(since_day, timeout=240):
            return [{"type": "ACTIVITY_TYPE_TRADE",
                     "trade": {"marketSlug": "x", "qty": 1, "price": 0.5}},
                    {"type": "ACTIVITY_TYPE_TRADE",
                     "trade": {"marketSlug": "y", "qty": 1, "price": 0.5}}]

        fresh.setattr(pmus_account, "_week_activities", undated)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        cov = out["pm_coverage"]
        assert cov["rows"] == 2 and cov["undated_rows"] == 2
        assert cov["rows_capped"] is False      # below the cap...
        assert cov["oldest"] is None
        assert "span unknown" in cov["note"]
        assert out["partial"] is True           # ...but not full coverage
        assert cov["persist_from"] is None

    async def test_inner_timeout_carries_a_named_detail(self, fresh):
        async def inner_bound(since_day, timeout=240):
            raise asyncio.TimeoutError()       # wait_for's bare TimeoutError

        fresh.setattr(pmus_account, "_week_activities", inner_bound)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        err = out["polymarket_us"]["error"]
        assert err.startswith("pm_crawl TimeoutError: pm_crawl timed out "
                              "inside the read after ")
        assert "ceiling 300s not reached" in err
        detail = out["pm_coverage"]["error"]["detail"]
        assert detail != "TimeoutError()"
        assert "inside the read" in detail

    async def test_ceiling_timeout_detail_carries_elapsed_and_ceiling(
            self, fresh):
        async def hang():
            await asyncio.Event().wait()

        fresh.setattr(vt, "_kalshi_raw_from_heartbeat", hang)
        fresh.setattr(vt, "_HEARTBEAT_TIMEOUT_S", 0.05)
        await vt.snapshot()
        await _settle()
        out = await vt.snapshot()
        assert re.search(r"heartbeat exceeded 0\.05s \(after \d+\.\ds\)",
                         out["error"]["detail"])

    async def test_a_raise_in_kick_is_published_not_wedged(self, fresh):
        def bad_kick():
            raise RuntimeError("no loop")

        fresh.setattr(vt, "_kick_refresh", bad_kick)
        out = await vt.snapshot()
        assert out["building"] is False
        assert out["error"] == {"stage": "kick", "type": "RuntimeError",
                                "detail": "RuntimeError('no loop')"}
        assert out["attempts"] == 1
        assert out["next_attempt_s"] > 0
        assert vt._snap["building"] is False
        assert not vt._bg_tasks
        # Inside the backoff nothing is retried; the flag never wedged.
        again = await vt.snapshot()
        assert again["building"] is False and again["attempts"] == 1
