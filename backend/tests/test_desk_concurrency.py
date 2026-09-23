"""SINGLE WRITER AND IDEMPOTENT INTAKE, demonstrated.

WHY THIS FILE EXISTS. I previously offered as evidence of restart and
concurrency safety: one `bettor_desk_state` row, a monotonically
increasing cursor, unchanged earliest decision timestamps, and
`invariant_ok`. None of those four establishes either property.

  * ONE STATE ROW is what an UPSERT on a primary key produces whether
    one writer or five are doing the upserting.
  * A MONOTONE CURSOR is what `max()` over an append-only serial gives
    you regardless of how many readers advanced it.
  * UNCHANGED EARLIEST TIMESTAMPS show only that history was not
    re-read; they say nothing about concurrent writers now.
  * `invariant_ok` compares a desk against its OWN starting cash, so a
    freshly emptied book reconciles perfectly.

The actual guarantees are two specific mechanisms, and each is
exercised below against a fake connection that records what was asked.
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_desk as DK                    # noqa: E402
from sportsassets import bettor_desk_loop as DL               # noqa: E402


class FakeConn:
    """Records every statement; answers the lock however told to."""

    def __init__(self, lock_granted=True):
        self.lock_granted = lock_granted
        self.calls = []
        self.rows = {}

    async def fetchval(self, sql, *a):
        self.calls.append(("fetchval", sql, a))
        if "pg_try_advisory_lock" in sql:
            return self.lock_granted
        if "max(id)" in sql:
            return 4242
        return 0

    async def fetchrow(self, sql, *a):
        self.calls.append(("fetchrow", sql, a))
        # ROUTE BY QUERY. A single canned row for every fetchrow made
        # the cursor read return the state row and raise KeyError on
        # 'cursor_event_id' -- the fake was wrong, not the code.
        if "cursor_event_id" in sql:
            return self.rows.get("cursor")
        return self.rows.get("state")

    async def fetch(self, sql, *a):
        self.calls.append(("fetch", sql, a))
        if "bettor_desk_positions" in sql:
            return self.rows.get("legs", [])
        if "bettor_desk_orders" in sql:
            return self.rows.get("orders", [])
        return []

    async def execute(self, sql, *a):
        self.calls.append(("execute", sql, a))
        return "UPDATE 0"

    def transaction(self):
        conn = self

        class _T:
            async def __aenter__(self):
                conn.calls.append(("BEGIN", "", ()))
                return None

            async def __aexit__(self, *e):
                conn.calls.append(("COMMIT", "", ()))
                return False
        return _T()


# ── mechanism 1: the advisory lock is what decides who writes ────────

def test_the_lock_is_session_scoped_and_try_not_blocking():
    """`pg_try_advisory_lock` returns rather than queues.

    A BLOCKING `pg_advisory_lock` would make the second instance wait
    instead of standing by, and during a deploy the new instance would
    hang until the old one exited rather than reporting STANDBY.
    """
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_loop.py")).read()
    assert "pg_try_advisory_lock" in src
    assert "pg_advisory_lock(" not in src.replace("pg_try_advisory_lock(", "")
    # Session-scoped, NOT xact-scoped: an xact lock would be released at
    # the end of the first transaction and every later cycle would run
    # unguarded.
    assert "pg_try_advisory_xact_lock" not in src


@pytest.mark.anyio
async def test_the_lock_holder_proceeds_and_a_loser_never_writes():
    granted, denied = FakeConn(True), FakeConn(False)
    assert await DL._acquire(granted) is True
    assert await DL._acquire(denied) is False
    # The loser's only statement was the lock attempt itself.
    assert [c for c in denied.calls if c[0] == "execute"] == []


@pytest.mark.anyio
async def test_a_standby_instance_writes_nothing_at_all():
    """The overlap check. Two instances, one lock, one writer.

    `run()` is an infinite loop, so it is driven for a bounded moment
    and then cancelled; what matters is what reached the database.
    """
    os.environ["BETTOR_DESK_LOOP"] = "1"
    DL.CYCLE_S = 0.01
    loser = FakeConn(lock_granted=False)

    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(self_):
                    return loser

                async def __aexit__(self_, *e):
                    return False
            return _A()

    async def _pool():
        return _Pool()

    task = asyncio.get_running_loop().create_task(DL.run(_pool))
    await asyncio.sleep(0.08)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert DL.status()["state"] == DL.STATE_STANDBY
    writes = [c for c in loser.calls
              if c[0] == "execute" or "INSERT" in str(c[1]).upper()]
    assert writes == [], writes


# ── mechanism 2: the event id is the idempotency key ─────────────────

def test_every_write_carries_a_conflict_clause_on_a_natural_key():
    """A re-read event must not produce a second row anywhere."""
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_loop.py")).read()
    tree = ast.parse(src)
    persist = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == "_persist")
    inserts = [n.value for n in ast.walk(persist)
               if isinstance(n, ast.Constant)
               and isinstance(n.value, str)
               and "INSERT INTO" in n.value.upper()]
    assert inserts, "no INSERT found -- the check would pass vacuously"
    # `bettor_desk_ledger` is the one exemption and it is named rather
    # than tolerated. It is an append-only series of point-in-time
    # snapshots with a BIGSERIAL key, so a conflict clause would have
    # nothing to conflict on. It is safe under retry for a different
    # reason: the whole of `_persist` runs inside one transaction, so a
    # cycle that fails part-way rolls the snapshot back and the retry
    # writes exactly one row, not two.
    appended = 0
    for sql in inserts:
        if "BETTOR_DESK_LEDGER" in sql.upper():
            appended += 1
            continue
        assert "ON CONFLICT" in sql.upper(), sql[:90]
    assert appended == 1, "the ledger exemption must cover exactly one insert"

    # And the exemption only holds if the writes really are in one
    # transaction, so that is checked rather than assumed.
    assert any(isinstance(n, ast.AsyncWith)
               and any("transaction" in ast.dump(item.context_expr)
                       for item in n.items)
               for n in ast.walk(persist)), "_persist is not transactional"


def test_the_fill_key_is_derived_from_the_order_and_not_a_counter():
    """A counter would renumber after a restart and duplicate fills."""
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_loop.py")).read()
    i = src.index("INSERT INTO bettor_desk_fills")
    body = src[i:i + 1800]
    assert "o.order_id" in body
    assert "ON CONFLICT (fill_id) DO NOTHING" in body


def test_the_consumption_ledger_cannot_be_overdrawn():
    """The database enforces it, not the caller.

    One printed execution may not fill three hypothetical orders, and
    the CHECK is what makes that true even if the matcher were wrong.
    """
    sql = open(os.path.join(_ROOT, "migrations",
                            "094_bettor_desk.sql")).read()
    assert "bettor_desk_consumption_not_overdrawn" in sql
    assert "consumed_qty <= available_qty" in sql
    assert "evidence_id    TEXT PRIMARY KEY" in sql


def test_a_failed_cycle_does_not_advance_the_cursor():
    """Pinned on the while-loop's handler, via the AST.

    An earlier version of this test sliced source text and matched
    whichever `except` came first, so it broke the moment a guard was
    added above it.
    """
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_loop.py")).read()
    tree = ast.parse(src)
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
    loops = [n for n in ast.walk(run) if isinstance(n, ast.While)]
    tries = [t for lp in loops for t in ast.walk(lp)
             if isinstance(t, ast.Try)]
    assert tries, "the cycle is not guarded at all"
    # No handler may assign to `cursor`; the cursor only moves on the
    # success path.
    for t in tries:
        for h in t.handlers:
            for n in ast.walk(h):
                assert not (isinstance(n, ast.Name)
                            and isinstance(n.ctx, ast.Store)
                            and n.id == "cursor")


# ── the restore is checked, not trusted ──────────────────────────────

@pytest.mark.anyio
async def test_a_restore_reports_whether_the_rebuilt_book_reconciles():
    conn = FakeConn()
    conn.rows["state"] = {"cash": 97797.00, "start": 100000.0}
    conn.rows["legs"] = [{
        "condition_id": "c1", "outcome_index": 0, "qty": 4000.0,
        "cost": 2217.62, "realized": 14.62, "fees": -14.62,
        "opened": 1.0, "settled": False, "payout": None}]
    desk = DK.Desk(policy=DK.Policy(), limits=DK.Limits(),
                   fee_fn=DL.live_fee_fn)
    out = await DL._restore(conn, "live1", desk)
    assert out["restored"] is True
    assert desk.pf.cash == 97797.00
    assert out["reconciles"] is True
    assert abs(desk.pf.realized - 14.62) < 1e-9


@pytest.mark.anyio
async def test_a_restore_that_does_not_reconcile_says_so():
    """CONTROL. If `reconciles` were hard-coded True it would be noise."""
    conn = FakeConn()
    conn.rows["state"] = {"cash": 50000.0, "start": 100000.0}
    conn.rows["legs"] = []
    desk = DK.Desk(policy=DK.Policy(), limits=DK.Limits(),
                   fee_fn=DL.live_fee_fn)
    out = await DL._restore(conn, "live1", desk)
    assert out["restored"] is True
    assert out["reconciles"] is False


@pytest.mark.anyio
async def test_a_first_start_is_not_mistaken_for_a_restart():
    conn = FakeConn()
    desk = DK.Desk(policy=DK.Policy(), limits=DK.Limits())
    out = await DL._restore(conn, "live1", desk)
    assert out["restored"] is False
    assert "NO_PRIOR_STATE" in out["reason"]
    assert desk.pf.cash == desk.pf.starting_cash


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── fail closed: a book that does not reconcile must not trade ───────

@pytest.mark.anyio
async def test_a_non_reconciling_restore_halts_before_any_event_is_stepped():
    """FOUND IN PRODUCTION, by the check this test now pins.

    `_restore` took cash from the last epoch and legs from every epoch,
    because the rows carry no epoch to separate them. Open positions
    went 52 -> 96 and the identity drifted +$2,367.73. The desk must
    stop rather than continue carefully: no arithmetic recovers which
    legs belong to which book, and any rule that picked would be an
    invented accounting treatment.
    """
    os.environ["BETTOR_DESK_LOOP"] = "1"
    DL.CYCLE_S = 0.01
    conn = FakeConn(lock_granted=True)
    # cash from one era, a leg from another: the identity cannot hold.
    conn.rows["state"] = {"cash": 96405.37, "start": 100000.0}
    conn.rows["legs"] = [{
        "condition_id": "orphan", "outcome_index": 0, "qty": 9000.0,
        "cost": 5985.31, "realized": 22.95, "fees": 0.0,
        "opened": 1.0, "settled": False, "payout": None}]

    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(self_):
                    return conn

                async def __aexit__(self_, *e):
                    return False
            return _A()

    async def _pool():
        return _Pool()

    task = asyncio.get_running_loop().create_task(DL.run(_pool))
    await asyncio.sleep(0.08)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    st = DL.status()
    assert st["state"] == DL.STATE_ERROR, st
    assert "RESTORED_BOOK_DOES_NOT_RECONCILE" in (st["error"] or "")
    # AND IT WROTE NO SNAPSHOT. A halted desk that kept appending
    # non-reconciling ledger rows would be worse than one that stopped.
    ledger = [c for c in conn.calls
              if "bettor_desk_ledger" in str(c[1]).lower()]
    assert ledger == [], ledger


@pytest.mark.anyio
async def test_a_reconciling_restore_does_NOT_halt():
    """CONTROL. If the halt fired regardless, the desk could never run."""
    os.environ["BETTOR_DESK_LOOP"] = "1"
    DL.CYCLE_S = 0.01
    conn = FakeConn(lock_granted=True)
    conn.rows["state"] = {"cash": 97797.00, "start": 100000.0}
    conn.rows["legs"] = [{
        "condition_id": "c1", "outcome_index": 0, "qty": 4000.0,
        "cost": 2217.62, "realized": 14.62, "fees": 0.0,
        "opened": 1.0, "settled": False, "payout": None}]

    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(self_):
                    return conn

                async def __aexit__(self_, *e):
                    return False
            return _A()

    async def _pool():
        return _Pool()

    task = asyncio.get_running_loop().create_task(DL.run(_pool))
    await asyncio.sleep(0.08)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert DL.status()["state"] == DL.STATE_RUNNING, DL.status()
