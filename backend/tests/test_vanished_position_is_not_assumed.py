"""A vanished position we know NOTHING about fired a real market sell.

whale_exits reads a position that disappears from a whale's book as a
100% exit unless it can see the market resolved. The resolved set came
from an INNER JOIN of market_tokens to markets returning only rows with
resolved=true, so a token absent from market_tokens — or whose market row
does not exist — produced no row, and absence was read as positive proof
the market had NOT resolved.

The full-exit branch of mirror_exit calls pmus.close_position: an
unlimited market sell bounded only by EXIT_SLIPPAGE_BIPS (300). So a
position we simply had no metadata for got flattened at the bid, at up
to 3% plus fees, against a book whose entire measured edge is 94-205bp.

The module's designed protection is that unknown-ness forfeits the cycle
— the except branch sets the skip set to None for exactly that reason.
It only ever fired when the query RAISED, never when the query simply
could not see, and those come back the same shape.

The population is already measured in this codebase: EXITCENSUS
cls_token_unenriched: 56 in one window.

Second hazard, same branch: markets.resolved is fed by an unordered
LIMIT 500 sweep on a 300s cycle, so a just-finished game is not
guaranteed to be flagged. In that window a redemption reads as an exit
and we sell a near-certain $1.00 payout at the bid. markets.closed is
set when the market stops trading, ahead of resolution, and a whale
cannot trade out of a closed market — so a disappearance there is a
redemption by construction.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import live_executor as le
from sportsassets.workers import whale_exits as we

from tests.test_refused_exit_is_held import FakePool, harness  # noqa: F401


async def _cycle(pool):
    return await we._cycle(object(), pool)


class TestOnlyALiveMarketCanProduceAnExit:
    @pytest.mark.asyncio
    async def test_a_token_with_no_metadata_is_NOT_sold(self, harness):
        book, calls, _outcome = harness
        pool = FakePool(unknown={"ghost"})
        book.update({"ghost": 500.0, "keep": 10.0})
        await _cycle(pool)
        book.pop("ghost")
        stats = await _cycle(pool)
        assert calls == [], \
            "an unlimited close fired on a market we cannot even name"
        assert stats["vanished_unknown"] == 1

    @pytest.mark.asyncio
    async def test_a_RESOLVED_market_is_still_skipped(self, harness):
        book, calls, _outcome = harness
        pool = FakePool(resolved={"tokA"})
        book.update({"tokA": 500.0, "keep": 10.0})
        await _cycle(pool)
        book.pop("tokA")
        stats = await _cycle(pool)
        assert calls == []
        assert stats["vanished_settled"] == 1

    @pytest.mark.asyncio
    async def test_a_CLOSED_but_not_yet_resolved_market_is_skipped(
            self, harness):
        """The resolution lag window. He cannot have traded out of a
        market that stopped trading, so this is a redemption."""
        book, calls, _outcome = harness
        pool = FakePool(closed={"tokA"})
        book.update({"tokA": 500.0, "keep": 10.0})
        await _cycle(pool)
        book.pop("tokA")
        stats = await _cycle(pool)
        assert calls == [], \
            "sold a near-certain $1.00 payout at the bid during the " \
            "resolution-flag lag"
        assert stats["vanished_settled"] == 1

    @pytest.mark.asyncio
    async def test_a_KNOWN_LIVE_market_still_produces_the_exit(self, harness):
        """The guard must not eat the case the worker exists for."""
        book, calls, _outcome = harness
        pool = FakePool()
        book.update({"tokA": 500.0, "keep": 10.0})
        await _cycle(pool)
        book.pop("tokA")
        stats = await _cycle(pool)
        assert [c["asset"] for c in calls] == ["tokA"]
        assert calls[0]["closed_frac"] == 1.0
        assert stats["vanished_live"] == 1
        assert stats["vanished_unknown"] == 0

    @pytest.mark.asyncio
    async def test_a_partial_shrink_is_unaffected_by_any_of_this(
            self, harness):
        """A position that shrank is still THERE. Metadata cannot make
        a measured trim ambiguous, and the guard must not touch it."""
        book, calls, _outcome = harness
        pool = FakePool(unknown={"tokA"})
        book.update({"tokA": 500.0})
        await _cycle(pool)
        book["tokA"] = 100.0
        await _cycle(pool)
        assert [c["asset"] for c in calls] == ["tokA"]
        assert calls[0]["closed_frac"] == pytest.approx(0.8)


class TestTheBugReproduces:
    def test_the_old_query_could_not_distinguish_the_two_cases(self):
        """Absence of a row meant 'not resolved' and 'never heard of
        it' alike. The rule under test is the caller's, so it is stated
        here against diff_exits directly."""
        prev, now = {"ghost": 100.0}, {}
        # What the old caller passed: only positively-resolved tokens.
        assert we.diff_exits(prev, now, set()) == [("ghost", 1.0)]
        # What the new caller passes: everything it cannot vouch for.
        assert we.diff_exits(prev, now, {"ghost"}) == []

    def test_the_third_argument_is_no_longer_named_resolved(self):
        """The name was the defect. `resolved` invited a caller to pass
        only what it had confirmed, and everything else fell through to
        a market sell."""
        sig = inspect.signature(we.diff_exits)
        assert "resolved" not in sig.parameters
        assert "not_an_exit" in sig.parameters

    def test_the_guard_can_only_ever_refuse_more(self):
        """There must be no input on which the new skip set is smaller
        than the old one — a widened skip set cannot sell something the
        old code would not have."""
        prev = {f"t{i}": 100.0 for i in range(6)}
        now: dict[str, float] = {}
        old_skip = {"t0"}                    # resolved only
        new_skip = {"t0", "t1", "t2"}        # resolved | closed | unknown
        old = {a for a, _ in we.diff_exits(prev, now, old_skip)}
        new = {a for a, _ in we.diff_exits(prev, now, new_skip)}
        assert new <= old


class TestTheAdminPauseReachesTheExitPath:
    """POST /api/admin/live/pause is documented as "no further orders".
    _is_paused was read in exactly two places — the manual desk and
    maybe_execute — and mirror_exit is neither, so the newest
    order-placing path in the system grew outside the kill switch. The
    operator believed the account was stopped while the whale_exits
    worker went on sending unpriced close_position flattens every 120
    seconds."""

    def test_mirror_exit_reads_the_pause(self):
        src = inspect.getsource(le.mirror_exit)
        assert "_is_paused" in src

    def test_it_refuses_before_anything_looks_up_a_position(self):
        src = inspect.getsource(le.mirror_exit)
        assert src.index("_is_paused") < \
            src.index("mx_reached_position_lookup")

    def test_the_pause_is_a_pending_reason_not_a_settled_one(self):
        """A pause is cleared by the operator and the exit is still real
        when it clears — so it must be held, exactly like the breaker."""
        assert "mx_paused" in le.EXIT_PENDING_REASONS

    @pytest.mark.asyncio
    async def test_an_unreadable_kill_switch_counts_as_ENGAGED(self):
        class Broken:
            async def fetchval(self, *a):
                raise RuntimeError("db down")

        assert await le._is_paused(Broken()) is True

    @pytest.mark.asyncio
    async def test_an_unparseable_value_counts_as_ENGAGED(self):
        class Junk:
            async def fetchval(self, *a):
                return "{not json"

        assert await le._is_paused(Junk()) is True

    @pytest.mark.asyncio
    async def test_an_absent_row_is_still_NOT_paused(self):
        class Empty:
            async def fetchval(self, *a):
                return None

        assert await le._is_paused(Empty()) is False

    @pytest.mark.asyncio
    async def test_a_normal_value_still_reads_normally(self):
        class Yes:
            async def fetchval(self, *a):
                return "true"

        class No:
            async def fetchval(self, *a):
                return "false"

        assert await le._is_paused(Yes()) is True
        assert await le._is_paused(No()) is False

    def test_the_manual_sell_is_deliberately_NOT_gated(self):
        """Left open ON PURPOSE, and recorded here so it reads as a
        decision rather than the same omission twice. The pause exists
        so the operator can stop the machine; taking away his own
        ability to reduce a position while it is engaged would trap him
        in whatever prompted the pause. Automated paths are gated; a
        human pressing a button is not."""
        src = inspect.getsource(le._execute_manual_sell)
        assert "_is_paused" not in src
