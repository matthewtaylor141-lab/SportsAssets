"""A REFUSED exit was erased from the snapshot forever.

whale_exits saves its position snapshot BEFORE acting on the diff, which
is right for crash safety: a crash mid-cycle must not replay the diff and
fire the same sells twice. But it advanced the asset the moment the exit
was HANDED to execute_copy, and execute_copy reported nothing back at
all — it returned None on success, on refusal, and on exception alike.

So an exit the sleeve REFUSED was indistinguishable from one it
COMPLETED, and the next cycle's `prev` already agreed the whale was out.
The position stayed ours, held to resolution against a whale who had
left it, with no counter anywhere saying so.

Not hypothetical. With the overspend breaker sitting tripped on a false
positive, the exit census read:

    EXITCENSUS mx_overspend_halt: 326

326 real exits destroyed by a refusal that cleared with a single POST.

These tests drive the real _cycle against an in-memory snapshot store,
so they fail if the retention is removed — the earlier deferred-exit
regression was guarded only by source-string assertions, which is how
this one reached production next to it.
"""

from __future__ import annotations

import json
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.workers import whale_exits as we


class FakePool:
    """ingestion_state as a dict, plus the SELECTs _cycle issues."""

    def __init__(self, resolved: set[str] | None = None,
                 unknown: set[str] | None = None,
                 closed: set[str] | None = None,
                 held=frozenset({"rn1"}),
                 live_rows: list[dict] | None = None,
                 roster: list[dict] | None = None):
        self.state: dict[str, str] = {}
        self.resolved = resolved or set()
        self.closed = closed or set()
        # Tokens with no market metadata at all. Everything else is
        # KNOWN and still trading, which is the only state on which a
        # disappearance may be read as an exit.
        self.unknown = unknown or set()
        self.saves: list[dict[str, float]] = []
        # THE HELD-WHALE READ (2026-09-05): a book is walked only while
        # we hold a live non-mirror row of his. `held` is what the read
        # answers -- rn1 by default, because every test in this file
        # predates the gate and a held whale is walked exactly as it
        # was -- or an exception instance to make the read raise.
        # `live_rows`, when given, answers instead from live_orders
        # rows filtered the way Postgres would, and ONLY by the clauses
        # that stand in the statement's text, PARSED from it rather
        # than matched as whole literals: a clause that goes missing
        # admits the rows it was excluding. A row carries an optional
        # `error` text and an optional `age_s` (seconds since
        # placed_at, default 0).
        self.held = held
        self.live_rows = live_rows
        self.roster = roster or [{"username": "rn1", "address": "0xrn1"}]
        self.deletes: list[str] = []
        self.sqls: list[str] = []
        # every statement handed to execute, in order: (sql, args)
        self.sqls_executed: list[tuple[str, tuple]] = []

    def _held_answer(self, sql: str) -> list[dict]:
        if self.live_rows is not None:
            rows = [r for r in self.live_rows if self._row_is_held(r, sql)]
            names = {(r.get("whale_username") or "").lower() or None
                     for r in rows}
            return [{"whale": n} for n in sorted(n for n in names if n)]
        if isinstance(self.held, BaseException):
            raise self.held
        return [{"whale": w} for w in sorted(self.held)]

    @staticmethod
    def _row_is_held(r: dict, sql: str) -> bool:
        """The statement's predicate, evaluated the way Postgres would,
        clause by clause and only for the clauses the text carries:
        (status-list OR named-error) AND lane."""
        # status IN ('a', 'b', ...): allowed = the quoted names
        m = re.search(r"status IN \(([^)]*)\)", sql)
        has_list = m is not None
        in_list = has_list and r.get("status") in set(
            re.findall(r"'([^']*)'", m.group(1)))
        # the reaper-named error rows, present iff status = 'error' is
        has_named = "status = 'error'" in sql
        named = False
        if has_named and r.get("status") == "error":
            prefixes = re.findall(r"error LIKE '([^%']*)%'", sql)
            named = (not prefixes) or any(
                (r.get("error") or "").startswith(p) for p in prefixes)
            if named and "placed_at > now() - interval '48 hours'" in sql:
                named = float(r.get("age_s", 0)) < 48 * 3600
        # no status clause of either kind in the text: every row passes
        status_ok = (in_list or named) if (has_list or has_named) else True
        lane_ok = True
        if "COALESCE(lane,'') <> 'mirror'" in sql:
            lane_ok = (r.get("lane") or "") != "mirror"
        return status_ok and lane_ok

    async def fetch(self, sql, *args):
        self.sqls.append(sql)
        if "FROM whales" in sql:
            return list(self.roster)
        if "lower(whale_username)" in sql and "FROM live_orders" in sql:
            return self._held_answer(sql)
        if "market_tokens" in sql:
            gone = args[0] if args else []
            return [{"token_id": a,
                     "resolved": a in self.resolved,
                     "closed": a in self.closed}
                    for a in gone if a not in self.unknown]
        return []

    async def fetchval(self, sql, *args):
        return self.state.get(args[0])

    async def execute(self, sql, *args):
        self.sqls_executed.append((sql, args))
        if sql.lstrip().upper().startswith("DELETE"):
            # the unheld whale's baseline retired; recorded so a test
            # can see WHICH row went. ONLY the one spelling: any other
            # DELETE (a `key <> $1`, a bare table) is a statement this
            # fake does not know how to answer, so it refuses.
            if sql.strip() != "DELETE FROM ingestion_state WHERE key = $1":
                raise AssertionError(sql)
            self.deletes.append(args[0])
            self.state.pop(args[0], None)
            return
        # STORED THE WAY jsonb STORES IT. PostgreSQL normalises object
        # keys into sorted order on write, so a stub that preserved
        # insertion order would hide any code that depended on it —
        # which the first version of the rotation did.
        val = json.loads(args[1])
        if isinstance(val, dict):
            val = {k: val[k] for k in sorted(val, key=lambda k: (len(k), k))}
        self.state[args[0]] = json.dumps(val)
        if args[0].startswith("whale_positions:"):
            self.saves.append(val)


@pytest.fixture
def harness(monkeypatch):
    """A whale whose book we control, and a scripted exit outcome."""
    book: dict[str, float] = {}
    calls: list[dict] = []
    outcome = {"reason": "mx_SOLD"}

    async def _positions(_http, _addr):
        return dict(book), {}, len(book)

    async def _copy(payload):
        calls.append(payload)
        r = outcome["reason"]
        return r(payload) if callable(r) else r

    monkeypatch.setattr(we, "_fetch_positions", _positions)
    monkeypatch.setattr(le, "execute_copy", _copy)
    monkeypatch.setattr("sportsassets.api.copies_record.COPY_WHALES",
                        {"rn1"}, raising=False)
    return book, calls, outcome


async def _run(pool, harness):
    return await we._cycle(object(), pool)


class TestARefusedExitIsFoundAgain:
    @pytest.mark.asyncio
    async def test_a_halted_sleeve_does_not_destroy_the_exit(self, harness):
        book, calls, outcome = harness
        pool = FakePool()
        book.update({"tokA": 500.0, "tokB": 100.0})
        # Cycle 1: first sight of the whale, snapshot only.
        await _run(pool, harness)
        assert calls == []
        # He exits tokA entirely. The sleeve is halted.
        book.pop("tokA")
        outcome["reason"] = "mx_overspend_halt"
        stats = await _run(pool, harness)
        assert len(calls) == 1 and calls[0]["asset"] == "tokA"
        assert stats["exits_pending"] == 1
        assert stats["exits_sold"] == 0
        assert stats["pend_mx_overspend_halt"] == 1
        # THE POINT: the snapshot still carries the position, so the
        # next cycle sees the same exit.
        outcome["reason"] = "mx_SOLD"
        stats = await _run(pool, harness)
        assert [c["asset"] for c in calls] == ["tokA", "tokA"]
        assert stats["exits_sold"] == 1
        # And now it is done, so it must not fire a third time.
        stats = await _run(pool, harness)
        assert len(calls) == 2
        assert stats["exit_attempts"] == 0

    @pytest.mark.asyncio
    async def test_the_bug_reproduces_when_the_reason_is_ignored(
            self, harness):
        """Pin the outcome to an unclassified reason and the old
        behaviour returns exactly — one attempt, then silence."""
        book, calls, outcome = harness
        pool = FakePool()
        book.update({"tokA": 500.0, "tokB": 100.0})
        await _run(pool, harness)
        book.pop("tokA")
        outcome["reason"] = "mx_no_position_of_ours"
        await _run(pool, harness)
        assert len(calls) == 1
        await _run(pool, harness)
        assert len(calls) == 1, \
            "a settled reason must not be retried forever"

    @pytest.mark.asyncio
    async def test_a_partial_exit_is_held_at_its_PRE_exit_size(
            self, harness):
        """A shrink must be re-pinned at the old size, not the new one:
        pinning at the new size would make the next diff read no change
        and lose the exit just as completely."""
        book, calls, outcome = harness
        pool = FakePool()
        book.update({"tokA": 500.0})
        await _run(pool, harness)
        book["tokA"] = 200.0
        outcome["reason"] = "mx_halted"
        await _run(pool, harness)
        assert calls[-1]["closed_frac"] == pytest.approx(0.6)
        outcome["reason"] = "mx_SOLD"
        await _run(pool, harness)
        assert len(calls) == 2
        assert calls[-1]["closed_frac"] == pytest.approx(0.6), \
            "the retry must carry the same fraction as the original"

    @pytest.mark.asyncio
    async def test_an_exception_is_NOT_retried(self, harness):
        """execute_copy swallows exceptions and returns None. This lane
        carries no trade id, so mirror_exit's copy_exit_applied ledger
        cannot deduplicate it — an order may already be in flight and
        selling the same position twice is worse than losing an exit."""
        book, calls, outcome = harness
        pool = FakePool()
        book.update({"tokA": 500.0, "keep": 10.0})
        await _run(pool, harness)
        book.pop("tokA")
        outcome["reason"] = None
        await _run(pool, harness)
        assert len(calls) == 1
        await _run(pool, harness)
        assert len(calls) == 1


class TestTheRotationDoesNotRelyOnDictOrder:
    """The first version of this rotation leaned on the pinned assets
    landing at the end of the snapshot's insertion order. That is true
    of a Python dict and FALSE of the column it is stored in: jsonb
    normalises object keys into sorted order on write. A stub pool that
    keeps the JSON string round-trips insertion order perfectly, so the
    behavioural tests below would have passed against a production
    system that starved the backlog — the exact shape of "a probe
    reading something production does not write".

    So the cursor is explicit, and these tests pin the pure functions
    that implement it.
    """

    def test_jsonb_key_order_is_not_something_to_rely_on(self):
        """Documenting the trap, and proving the code does not depend on
        it: the rotation must survive its input being re-sorted."""
        found = [("t05", 1.0), ("t01", 1.0), ("t09", 1.0)]
        tried = ["t01"]
        a = we.rotate_for_fairness(found, tried)
        b = we.rotate_for_fairness(sorted(found), tried)
        assert a[-1][0] == b[-1][0] == "t01", \
            "the least-recently-tried exit must sort last either way"

    def test_untried_exits_come_first(self):
        found = [("a", 1.0), ("b", 1.0), ("c", 1.0)]
        assert [x[0] for x in we.rotate_for_fairness(found, ["a", "b"])] \
            == ["c", "a", "b"]

    def test_the_oldest_attempt_is_retried_before_a_newer_one(self):
        found = [("b", 1.0), ("a", 1.0)]
        assert [x[0] for x in we.rotate_for_fairness(found, ["a", "b"])] \
            == ["a", "b"]

    def test_an_empty_cursor_changes_nothing(self):
        found = [("a", 1.0), ("b", 1.0)]
        assert we.rotate_for_fairness(found, []) == found

    def test_a_settled_attempt_leaves_the_cursor(self):
        assert we.next_cursor(["a", "b"], ["a"], set()) == ["b"]

    def test_a_pending_attempt_moves_to_the_back(self):
        assert we.next_cursor(["a", "b"], ["a"], {"a"}) == ["b", "a"]

    def test_a_first_attempt_joins_the_back(self):
        assert we.next_cursor([], ["x"], {"x"}) == ["x"]

    def test_the_cursor_is_bounded(self):
        big = [f"a{i}" for i in range(we.MAX_RETRY_CURSOR * 2)]
        out = we.next_cursor(big, [], set())
        assert len(out) == we.MAX_RETRY_CURSOR
        assert out[-1] == big[-1], "the newest must survive the trim"


class TestTheRetryQueueRotates:
    @pytest.mark.asyncio
    async def test_a_backlog_larger_than_the_cap_does_not_starve(
            self, harness):
        """With every exit refused and a backlog above
        MAX_EXITS_PER_CYCLE, retrying the same ten forever would leave
        the rest untouched."""
        book, calls, outcome = harness
        pool = FakePool()
        cap = we.MAX_EXITS_PER_CYCLE
        assets = [f"t{i:02d}" for i in range(cap * 2)]
        book.update({a: 100.0 for a in assets})
        book["keep"] = 10.0
        await _run(pool, harness)
        for a in assets:
            book.pop(a)
        outcome["reason"] = "mx_halted"
        await _run(pool, harness)
        first = [c["asset"] for c in calls]
        assert len(first) == cap
        calls.clear()
        await _run(pool, harness)
        second = [c["asset"] for c in calls]
        assert len(second) == cap
        assert not (set(first) & set(second)), \
            f"the same exits were retried while others waited: {second}"

    @pytest.mark.asyncio
    async def test_every_exit_eventually_gets_a_turn(self, harness):
        book, calls, outcome = harness
        pool = FakePool()
        cap = we.MAX_EXITS_PER_CYCLE
        assets = [f"t{i:02d}" for i in range(cap * 3)]
        book.update({a: 100.0 for a in assets})
        book["keep"] = 10.0
        await _run(pool, harness)
        for a in assets:
            book.pop(a)
        outcome["reason"] = "mx_halted"
        seen: set[str] = set()
        for _ in range(6):
            calls.clear()
            await _run(pool, harness)
            seen.update(c["asset"] for c in calls)
        assert seen == set(assets), f"never tried: {set(assets) - seen}"


class TestTheCounterStopsLying:
    def test_exits_no_longer_counts_attempts(self):
        import inspect

        src = inspect.getsource(we._cycle)
        body = src[src.index("for asset, frac in acting:"):]
        head = body[:body.index("reason = await execute_copy")]
        assert 'stats["exits"] += 1' not in head, \
            "counting before the call reports a halted sleeve as working"
        assert 'stats["exit_attempts"] += 1' in head

    def test_the_new_counters_are_always_present(self):
        """Absent and zero look identical to a reader, and this codebase
        has shipped that confusion before."""
        import inspect

        src = inspect.getsource(we._cycle)
        head = src[:src.index("all_sibs")]
        for k in ("exit_attempts", "exits_sold", "exits_pending",
                  "exits_no_action"):
            assert f'"{k}"' in head, f"{k} is only added conditionally"

    @pytest.mark.asyncio
    async def test_a_fully_halted_cycle_reports_zero_sold(self, harness):
        book, calls, outcome = harness
        pool = FakePool()
        book.update({"tokA": 500.0, "keep": 10.0})
        await _run(pool, harness)
        book.pop("tokA")
        outcome["reason"] = "mx_overspend_halt"
        stats = await _run(pool, harness)
        assert stats["exits"] == 0, \
            "the headline counter must not report a refusal as an exit"
        assert stats["exits_sold"] == 0
        assert stats["exit_attempts"] == 1


class TestTheAllowlistIsOwnedByTheProducer:
    def test_every_pending_reason_is_actually_produced_somewhere(self):
        import inspect
        import re

        # BOTH producers: mirror_exit for the in-path refusals, and
        # execute_copy for the dispatcher's own exception path
        # (mx_exception_pending, 2026-08-26). A pending reason nothing
        # can produce is a dead allowlist entry that reads like
        # coverage.
        #
        # _exit_done ONLY (round three, 2026-09-01). _exit_stop's
        # contract is to return None, so a reason it "produces" never
        # leaves mirror_exit and can never be pending: mx_entry_in_flight
        # was added to the allowlist and returned through _exit_stop,
        # and this test -- matching either helper -- called it produced.
        src = (inspect.getsource(le.mirror_exit)
               + inspect.getsource(le.execute_copy))
        real = set(re.findall(r'_exit_done\(\s*"(mx_[a-z_]+)"', src))
        unknown = le.EXIT_PENDING_REASONS - real
        assert not unknown, f"pending reasons that cannot occur: {unknown}"

    def test_the_settled_reasons_are_excluded_deliberately(self):
        """These mean there is nothing left to do. Retrying any of them
        would re-pin the asset and re-diff it every cycle forever."""
        for r in ("mx_SOLD", "mx_no_position_of_ours",
                  "mx_venue_holds_nothing", "mx_exit_already_mirrored",
                  "mx_whale_not_verified", "mx_no_ledger_position"):
            assert r not in le.EXIT_PENDING_REASONS

    def test_mx_below_floor_MOVED_to_pending_on_evidence(self):
        """This assertion used to include mx_below_floor, and it was
        wrong -- recorded here rather than quietly deleted.

        The reasoning was that a trim under the floor means "nothing
        worth doing". That holds for ONE observation and fails across
        many, because closed_frac is a flow measured against the
        previous snapshot and the snapshot advances every 120 seconds.
        A whale trimming 7% repeatedly has every observation correctly
        refused while the deficit is discarded each time. Simulated on
        the production constants: 7%/cycle for 20 cycles leaves him at
        23.4% and us at 100.0%, with 20 refusals and no observation
        ever crossing the floor.

        Pinning does not admit a smaller trim -- the floor is
        unchanged. It stops the baseline running away from one.
        """
        assert "mx_below_floor" in le.EXIT_PENDING_REASONS
        # And the floor itself did NOT move.
        assert le.MIN_EXIT_FRAC == 0.10

    def test_the_halt_reasons_ARE_included(self):
        """The 326 destroyed exits were all one of these."""
        assert "mx_overspend_halt" in le.EXIT_PENDING_REASONS
        assert "mx_halted" in le.EXIT_PENDING_REASONS

    def test_an_unknown_reason_is_treated_as_settled(self):
        assert "mx_something_nobody_classified" not in \
            le.EXIT_PENDING_REASONS
        assert None not in le.EXIT_PENDING_REASONS


class TestTheHaltedSleeveStillRefuses:
    def test_the_sell_dispatch_did_not_bypass_the_master_switch(self):
        """execute_copy now dispatches SELL above the copy_probe_enabled
        return so the refusal has a readable name. mirror_exit's own
        first check must remain a strict superset of the one it moved
        ahead of, or that reordering is a loosened money gate."""
        import inspect

        src = inspect.getsource(le.mirror_exit)
        gate = src[:src.index("mx_halted")]
        assert "copy_probe_enabled" in gate
        assert "copy_halted()" in gate

    def test_the_dispatch_precedes_nothing_that_places_an_order(self):
        import inspect

        src = inspect.getsource(le.execute_copy)
        assert src.index("return await mirror_exit(payload)") < \
            src.index("maybe_execute")
