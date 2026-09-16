"""Is the desk actually live, and can anyone tell?

Owner, 2026-08-26: "I need to make sure the over the counter desk is
live and working so when a team member places a manual trade on the desk
it's being executed instantly by the ai trader."

Tracing the path to answer that turned up three things that made the
question unanswerable:

  * The admin kill switch applied to ONE venue. _execute_manual checks
    _is_paused before a Polymarket ticket; the Kalshi branch never did.
    Flipping live_trading_paused stopped half the desk and left the
    other half placing at full size -- one decision written in two
    places with one of them updated.
  * Nothing ever retired a queued Kalshi ticket. The relay thread
    returns SILENTLY when EDGE_PLATFORM_API or EDGE_INGEST_TOKEN is
    unset, so every ticket queues into nothing; 'pending' rows count
    against the 24h cap, so the desk then locks itself out with an
    exhausted budget that was never spent.
  * The live-status block built for the owner's 2026-08-21 report
    ("trades aren't being processed") reads live_orders, which holds the
    Polymarket leg ONLY. The Kalshi half of the desk -- the half that
    goes through a relay in another process, i.e. the half that can
    silently stop -- was invisible in the one instrument built to see
    it.

An instrument that cannot see its subject is the failure mode that has
cost the most here, and this was three of them in one path.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets.api import app as api_app


class FakePool:
    def __init__(self, *, rows=None, vals=None):
        self.rows = rows if rows is not None else []
        self.vals = dict(vals or {})
        self.sql: list[str] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return self.rows

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        return self.vals.get(args[0] if args else None)

    async def execute(self, sql, *args):
        self.sql.append(sql)


class TestTheKillSwitchCoversBothVenues:
    def test_the_kalshi_branch_reads_the_pause(self):
        src = inspect.getsource(api_app.api_manual_trade)
        assert "_is_paused" in src
        # ...and it is read BEFORE anything is queued
        assert src.index("_is_paused(") < src.index("manual_kalshi_queue")

    def test_it_refuses_rather_than_queueing(self):
        src = inspect.getsource(api_app.api_manual_trade)
        blk = src[src.index("if await _is_paused("):]
        blk = blk[:blk.index("day_spent")]
        assert "return" in blk
        assert "paused" in blk

    @pytest.mark.asyncio
    async def test_the_pause_itself_still_fails_closed(self):
        """An unreadable flag must read as paused. This test guards the
        property the desk branch now depends on."""
        from sportsassets import live_executor as le

        class Broken:
            async def fetchval(self, *a):
                raise RuntimeError("db down")

        assert await le._is_paused(Broken()) is True


class TestTheQueueIsReaped:
    def test_only_pending_is_retired(self):
        """A 'placed' row may have money behind it and only the venue
        knows. Failing it would be a claim we cannot support."""
        src = inspect.getsource(api_app.reap_stale_desk_queue)
        assert "status='pending'" in src
        assert "status='placed'" not in src

    def test_the_error_says_nothing_was_sent(self):
        src = inspect.getsource(api_app.reap_stale_desk_queue)
        assert "nothing was sent to the venue" in src

    def test_it_runs_before_the_budget_is_computed(self):
        """Otherwise a wedged queue locks the desk out with an
        'exhausted' budget that was never spent."""
        src = inspect.getsource(api_app.api_manual_trade)
        assert "reap_stale_desk_queue(pool)" in src
        assert (src.index("reap_stale_desk_queue(pool)")
                < src.index("day_spent = float("))

    @pytest.mark.asyncio
    async def test_a_reaper_failure_never_blocks_a_ticket(self):
        class Broken:
            async def fetch(self, *a):
                raise RuntimeError("db down")

        assert await api_app.reap_stale_desk_queue(Broken()) == 0

    @pytest.mark.asyncio
    async def test_it_returns_what_it_retired(self):
        pool = FakePool(rows=[{"id": 1}, {"id": 2}])
        assert await api_app.reap_stale_desk_queue(pool) == 2

    def test_the_window_is_far_past_the_relay_startup_sleep(self):
        """The relay sleeps 30s once at thread start. A window shorter
        than that would fail healthy tickets on every deploy."""
        assert api_app.DESK_QUEUE_STALE_S >= 120


class TestTheRelayHasAHeartbeat:
    def test_the_queue_pull_records_that_it_happened(self):
        """This pull is the only proof the relay process is alive."""
        src = inspect.getsource(api_app.api_manual_kalshi_queue)
        assert "DESK_RELAY_SEEN_KEY" in src
        assert "ingestion_state" in src

    def test_the_heartbeat_never_blocks_the_relay(self):
        src = inspect.getsource(api_app.api_manual_kalshi_queue)
        blk = src[src.index("DESK_RELAY_SEEN_KEY"):]
        assert "except Exception" in blk[:400]

    def test_it_is_written_before_the_rows_are_returned(self):
        src = inspect.getsource(api_app.api_manual_kalshi_queue)
        assert src.index("DESK_RELAY_SEEN_KEY") < src.index("LIMIT 20")


class TestLiveStatusCanSeeTheKalshiHalf:
    def _src(self) -> str:
        return inspect.getsource(api_app)

    def test_the_queue_table_is_read(self):
        src = self._src()
        i = src.index('manual_desk = {')
        blk = src[i:i + 4000]
        assert "manual_kalshi_queue" in blk, (
            "the desk diagnostic still reads only the Polymarket leg")

    def test_relay_liveness_is_reported(self):
        src = self._src()
        blk = src[src.index('manual_desk = {'):][:4000]
        for key in ("relay_last_seen", "relay_age_s", "relay_alive"):
            assert f'"{key}"' in blk

    def test_stuck_tickets_are_counted_in_both_states(self):
        """'placed' is not terminal and nothing retires it, so a relay
        that died mid-ticket leaves a row the desk UI polls forever."""
        blk = self._src()[self._src().index('manual_desk = {'):][:4000]
        assert '"stuck_pending"' in blk
        assert '"stuck_placed"' in blk

    def test_liveness_is_derived_from_the_same_window_as_the_reaper(self):
        """Two windows for one decision is how the reaper and the
        display end up disagreeing about whether the desk is up."""
        blk = self._src()[self._src().index('manual_desk = {'):][:4000]
        assert "DESK_QUEUE_STALE_S" in blk
        assert blk.count("DESK_QUEUE_STALE_S") >= 3

    def test_relay_alive_is_false_when_nothing_was_ever_recorded(self):
        """A relay that never started must not read as alive. The guard
        is `_age is not None`, not a bare comparison -- None < N would
        raise, and a truthiness test on 0 would flip the answer."""
        blk = self._src()[self._src().index('manual_desk = {'):][:4000]
        assert "_age is not None and _age <" in blk
