"""A cancelled exit stranded its row in 'exiting' forever.

mirror_exit claims a row with an atomic UPDATE to status='exiting', and
every path back out was an `except Exception`. asyncio.CancelledError
derives from BaseException, so a cancellation between the claim and the
terminal UPDATE sailed straight past and the claim was never released.

A canceller is live on this exact path: copy_sweep wraps maybe_execute
in asyncio.wait_for(ROW_TIMEOUT_S=60) and reaches mirror_exit through
classify_exit. Render's redeploy SIGTERM cancels every in-flight exit at
once, which means a deploy could strand several.

Nothing reaped that status. _reap_stale_submitting covers 'submitting'
only. A stranded row is invisible three ways over: the settlement sweep
selects status='filled' so it is never graded, mirror_exit's own row
query requires 'filled' so it can never be sold, and copy_sweep's
blocking list contains 'exiting' so the market can never be re-entered.
The shares sit at the venue and no number in the system moves.

Worse, asyncio.to_thread cannot be cancelled -- the thread runs to
completion -- so a cancellation can land AFTER the venue filled the
order. Releasing to 'filled' would then claim we hold shares that are
gone and the next cycle would sell them again.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from sportsassets import live_executor as le


class Pool:
    def __init__(self, rows):
        self.rows = rows
        self.execs: list[tuple] = []

    async def fetch(self, sql, *a):
        return self.rows if "status = 'exiting'" in sql else []

    async def execute(self, sql, *a):
        self.execs.append((sql, a))

    async def fetchval(self, sql, *a):
        return None


def _statuses(pool):
    out = []
    for sql, _a in pool.execs:
        for st in ("cashed_out", "filled", "error"):
            if f"status='{st}'" in sql or f"status = '{st}'" in sql:
                out.append(st)
                break
    return out


class TestTheVenueDecidesNotAClock:
    @pytest.mark.asyncio
    async def test_a_venue_holding_nothing_means_the_sale_HAPPENED(
            self, monkeypatch):
        async def _held(_slug):
            return 0, None

        monkeypatch.setattr(le, "_pm_held", _held)
        pool = Pool([{"id": 1, "us_market_slug": "slug-x"}])
        assert await le._reap_stale_exiting(pool) == 1
        assert "cashed_out" in _statuses(pool)

    @pytest.mark.asyncio
    async def test_a_venue_still_holding_means_it_never_landed(
            self, monkeypatch):
        async def _held(_slug):
            return 300, 0.4

        monkeypatch.setattr(le, "_pm_held", _held)
        monkeypatch.setattr(le, "_release_exit_claim", _release)
        pool = Pool([{"id": 1, "us_market_slug": "slug-x"}])
        assert await le._reap_stale_exiting(pool) == 1
        assert _RELEASED == [1]

    @pytest.mark.asyncio
    async def test_an_UNREADABLE_venue_decides_nothing(self, monkeypatch):
        """An outage must not be able to retire a live position or
        re-arm a sold one."""
        async def _held(_slug):
            raise RuntimeError("venue down")

        monkeypatch.setattr(le, "_pm_held", _held)
        pool = Pool([{"id": 1, "us_market_slug": "slug-x"}])
        assert await le._reap_stale_exiting(pool) == 0
        assert pool.execs == []

    @pytest.mark.asyncio
    async def test_a_row_with_no_slug_is_skipped(self, monkeypatch):
        pool = Pool([{"id": 1, "us_market_slug": None}])
        assert await le._reap_stale_exiting(pool) == 0

    @pytest.mark.asyncio
    async def test_an_unreadable_table_never_breaks_the_sweep(self):
        class Broken:
            async def fetch(self, *a):
                raise RuntimeError("db down")

        assert await le._reap_stale_exiting(Broken()) == 0

    @pytest.mark.asyncio
    async def test_the_retired_row_says_its_pnl_is_untrustworthy(
            self, monkeypatch):
        """The fill price was never captured, so the row's P&L is not
        evidence. Saying so is the difference between a gap and a lie."""
        async def _held(_slug):
            return 0, None

        monkeypatch.setattr(le, "_pm_held", _held)
        pool = Pool([{"id": 1, "us_market_slug": "slug-x"}])
        await le._reap_stale_exiting(pool)
        blob = " ".join(str(a) for _s, a in pool.execs)
        assert "never captured" in blob

    @pytest.mark.asyncio
    async def test_it_only_touches_rows_still_in_exiting(self,
                                                        monkeypatch):
        """A concurrent completion must win. The UPDATE carries its own
        status guard so the reaper cannot overwrite a row that finished
        between the SELECT and the write."""
        async def _held(_slug):
            return 0, None

        monkeypatch.setattr(le, "_pm_held", _held)
        pool = Pool([{"id": 1, "us_market_slug": "slug-x"}])
        await le._reap_stale_exiting(pool)
        sql = [s for s, _a in pool.execs if "cashed_out" in s][0]
        assert "AND status='exiting'" in sql


_RELEASED: list[int] = []


async def _release(_pool, row_id):
    _RELEASED.append(row_id)


class TestCancellationNoLongerSlipsPast:
    def test_the_pre_venue_handler_catches_BaseException(self):
        src = inspect.getsource(le.mirror_exit)
        i = src.index("held, _avg = await _pm_held(us_slug)")
        tail = src[i:]
        assert "except BaseException:" in tail[:tail.index("try:", 10)], \
            "CancelledError is a BaseException and was sailing past"

    def test_it_shields_the_release_from_its_own_cancellation(self):
        """Awaiting inside a cancelled task is itself cancelled, so an
        unshielded release never runs."""
        src = inspect.getsource(le.mirror_exit)
        assert "asyncio.shield(_release_exit_claim" in src

    def _venue_call_handlers(self):
        """The handlers wrapping the venue call, read from the TREE.

        Windowing on the literal "except Exception:" broke the moment
        the handler grew an `as` clause -- a landmark guessed against
        today's formatting, which is the same mistake in a new costume.
        The tree does not care how the line is spelled.
        """
        import ast

        fn = ast.parse(inspect.getsource(le.mirror_exit).lstrip()).body[0]
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            body = ast.unparse(node.body)
            if "submit_fok" in body and "close_position" in body:
                return {ast.unparse(h.type) if h.type else "":
                        ast.unparse(h.body) for h in node.handlers}
        raise AssertionError("the venue-call try block moved")

    def test_a_cancellation_AFTER_the_venue_call_does_not_release(self):
        """to_thread cannot be cancelled, so the sale may already have
        executed. Releasing to 'filled' would sell it twice."""
        h = self._venue_call_handlers()
        block = h["asyncio.CancelledError"]
        assert "_release_exit_claim" not in block
        assert "reconcile" in block

    def test_an_ordinary_exception_after_the_venue_call_still_releases(
            self):
        h = self._venue_call_handlers()
        assert "_release_exit_claim" in h["Exception"]

    def test_both_handlers_re_raise(self):
        for block in self._venue_call_handlers().values():
            assert block.rstrip().endswith("raise")

    def test_both_handlers_leave_a_census_trace(self):
        """Every other way out of mirror_exit records a reason. A
        re-raise that records nothing makes the census silently omit a
        whole class, and the reader believes the totals."""
        for block in self._venue_call_handlers().values():
            assert "_exit_stop(" in block, (
                "an exit that died here would leave no trace")
            assert "_exit_done(" not in block, (
                "_exit_done returns the reason; only _exit_stop is "
                "provably incapable of altering this path")


class TestItIsWiredIn:
    def test_the_sweep_calls_it_every_pass(self):
        from sportsassets.workers import copy_sweep

        src = inspect.getsource(copy_sweep.sweep_once)
        assert "_reap_stale_exiting(pool)" in src

    def test_the_count_reaches_the_heartbeat(self):
        from sportsassets.workers import copy_sweep

        src = inspect.getsource(copy_sweep.sweep_once)
        assert '"reaped_exiting"' in src

    def test_the_counter_uses_a_name_that_EXISTS(self):
        """The first version of this wrote stats["reaped_exiting"]
        before `stats` was ever assigned -- a NameError on the first
        pass that actually reaped something, which no test would hit
        against an empty table. Compile the function and check every
        name it loads is either assigned, a parameter, or a global."""
        import symtable

        from sportsassets.workers import copy_sweep

        src = inspect.getsource(copy_sweep)
        table = symtable.symtable(src, "copy_sweep.py", "exec")
        fn = next(c for c in table.get_children()
                  if c.get_name() == "sweep_once")
        for sym in fn.get_symbols():
            if sym.is_referenced() and not sym.is_assigned() \
                    and not sym.is_global() and not sym.is_free() \
                    and not sym.is_imported() and not sym.is_parameter():
                raise AssertionError(
                    f"{sym.get_name()!r} is read but never assigned")
