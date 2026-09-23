"""RESTART RECOVERY, PROVEN. A real Postgres-shaped fake, four scenarios.

WHAT HAS TO BE TRUE, and each is a test below rather than a claim:

  1. A restart restores IDENTICAL cash, positions, cost basis, realized
     P&L and resting orders.
  2. A PARTIAL FILL survives: the order comes back PARTIALLY_FILLED with
     its fills, so its average fill price is derived and not lost.
  3. An INTERRUPTED WRITE leaves no half-state: the cursor cannot
     advance without the cash it belongs to, because they are the same
     UPDATE inside the same transaction.
  4. OVERLAPPING PROCESSES: the second holds no lock, writes nothing,
     and does not create a second account.
  5. HISTORICAL UNASSIGNED ROWS (account_id IS NULL) cannot enter the
     new book.
  6. A restart does NOT replenish capital or reset performance.

The fake below models the two things that actually matter about
Postgres here: statements are routed by their text, and a transaction
that raises discards everything written inside it.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_desk as DK                    # noqa: E402
from sportsassets import bettor_desk_accounts as ACC          # noqa: E402
from sportsassets import bettor_desk_loop as DL               # noqa: E402

OPENING = 100000.0


class Store:
    """A tiny row store with transaction semantics and account scoping."""

    def __init__(self):
        self.accounts = {}
        self.account_state = {}
        self.positions = {}          # (account, cond, oi) -> dict
        self.orders = {}             # order_id -> dict
        self.fills = {}              # fill_id -> dict
        self.consumption = {}        # (eid, account) -> dict
        self.ledger = []
        self.incidents = {}
        self.decisions = {}
        self.epochs = {}
        self.fail_after = None       # statements until an induced crash
        self.n = 0


class FakeConn:
    def __init__(self, store, lock_granted=True):
        self.s = store
        self.lock_granted = lock_granted
        self._depth = 0
        self._journal = []

    # ── transaction with rollback ────────────────────────────────────
    def transaction(self):
        conn = self

        class _T:
            async def __aenter__(self_):
                conn._depth += 1
                conn._journal.append([])
                return None

            async def __aexit__(self_, exc_type, *e):
                undo = conn._journal.pop()
                conn._depth -= 1
                if exc_type is not None:
                    for fn in reversed(undo):
                        fn()
                elif conn._journal:
                    conn._journal[-1].extend(undo)
                return False
        return _T()

    def _undo(self, fn):
        if self._journal:
            self._journal[-1].append(fn)

    def _tick(self):
        self.s.n += 1
        if (self.s.fail_after is not None
                and self.s.n > self.s.fail_after):
            raise RuntimeError("INDUCED_CRASH_MID_TRANSACTION")

    # ── reads ────────────────────────────────────────────────────────
    async def fetchval(self, sql, *a):
        if "pg_try_advisory_lock" in sql:
            return self.lock_granted
        if "max(id)" in sql:
            return 9000
        if "count(*)" in sql and "bettor_desk_ledger" in sql:
            return len(self.s.ledger)
        if "count(*)" in sql:
            return 0
        return 0

    async def fetchrow(self, sql, *a):
        if "FROM bettor_desk_accounts" in sql:
            for v in self.s.accounts.values():
                if v["desk_id"] == a[0] and v["status"] == a[1]:
                    return dict(v)
            return None
        if "bettor_desk_account_state" in sql:
            return self.s.account_state.get(a[0])
        if "FROM bettor_desk_ledger" in sql:
            rows = [r for r in self.s.ledger if r.get("account_id") is None]
            return rows[-1] if rows else None
        if "FROM bettor_desk_positions" in sql and "count(*)" in sql:
            return {"n": 0, "cost": 0.0}
        return None

    async def fetch(self, sql, *a):
        if "FROM bettor_desk_positions" in sql:
            return [v for k, v in self.s.positions.items()
                    if k[0] == a[0]]
        if "FROM bettor_desk_orders" in sql:
            return [v for v in self.s.orders.values()
                    if v.get("account_id") == a[0]
                    and v["state"] in ("RESTING", "PARTIALLY_FILLED")]
        if "bettor_desk_fills" in sql:
            ids = set(a[1])
            return sorted(
                (v for v in self.s.fills.values()
                 if v.get("account_id") == a[0] and v["order_id"] in ids),
                key=lambda r: r["at"])
        if "bettor_desk_consumption" in sql:
            return [v for k, v in self.s.consumption.items() if k[1] == a[0]]
        return []

    # ── writes ───────────────────────────────────────────────────────
    async def execute(self, sql, *a):
        self._tick()
        u = sql.upper()
        if "INSERT INTO BETTOR_DESK_ACCOUNTS" in u:
            aid = a[0]
            # the partial unique index: one ACTIVE per desk
            for v in self.s.accounts.values():
                if v["desk_id"] == a[1] and v["status"] == ACC.ACTIVE:
                    raise RuntimeError("unique_violation: one active account")
            self.s.accounts[aid] = {
                "account_id": aid, "desk_id": a[1], "status": a[2],
                "opening_balance": float(a[3]), "note": a[4],
                "opened_at": "2026-09-23T16:00:00Z"}
            self._undo(lambda: self.s.accounts.pop(aid, None))
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_INCIDENTS" in u:
            self.s.incidents[a[0]] = {"incident_id": a[0]}
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_ACCOUNT_STATE" in u:
            prev = self.s.account_state.get(a[0])
            self.s.account_state[a[0]] = {
                "account_id": a[0], "desk_id": a[1],
                "cursor_event_id": a[2], "cash": float(a[3]),
                "start": float(a[4]), "realized": float(a[5]),
                "fees": float(a[6])}
            self._undo(lambda: self.s.account_state.__setitem__(a[0], prev)
                       if prev else self.s.account_state.pop(a[0], None))
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_POSITIONS" in u:
            k = (a[10], a[1], a[2])
            prev = self.s.positions.get(k)
            self.s.positions[k] = {
                "condition_id": a[1], "outcome_index": a[2],
                "qty": float(a[3]), "cost": float(a[4]),
                "realized": float(a[5]), "fees": float(a[6]),
                "opened": 1.0, "settled": bool(a[8]),
                "payout": a[9], "account_id": a[10]}
            self._undo(lambda: self.s.positions.__setitem__(k, prev)
                       if prev else self.s.positions.pop(k, None))
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_ORDERS" in u:
            oid = a[0]
            prev = self.s.orders.get(oid)
            self.s.orders[oid] = {
                "order_id": oid, "desk_decision_id": a[3],
                "condition_id": a[4], "outcome_index": a[5],
                "side": a[6], "intent": a[7],
                "limit_price": float(a[8]), "qty": float(a[9]),
                "filled_qty": float(a[10]), "avg_fill_price": a[12],
                "fees_usd": float(a[13]), "state": a[14],
                "placed": a[16], "expires": a[17],
                "account_id": a[19]}
            self._undo(lambda: self.s.orders.__setitem__(oid, prev)
                       if prev else self.s.orders.pop(oid, None))
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_FILLS" in u:
            fid = a[0]
            self.s.fills.setdefault(fid, {
                "fill_id": fid, "order_id": a[1], "at": float(a[2]),
                "qty": float(a[3]), "price": float(a[4]),
                "fee_usd": float(a[5]), "liquidity": a[6],
                "evidence_kind": a[7], "evidence_id": a[8],
                "exec_model": a[10], "account_id": a[11]})
            self._undo(lambda: self.s.fills.pop(fid, None))
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_CONSUMPTION" in u:
            k = (a[0], a[1])
            self.s.consumption[k] = {
                "evidence_id": a[0], "account_id": a[1],
                "available": float(a[2]), "consumed": float(a[3])}
            self._undo(lambda: self.s.consumption.pop(k, None))
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_LEDGER" in u:
            row = {"cash_usd": float(a[2]), "inventory_cost": float(a[4]),
                   "realized_pnl_usd": float(a[6]),
                   "account_id": a[13] if len(a) > 13 else None}
            self.s.ledger.append(row)
            self._undo(lambda: self.s.ledger.pop())
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_DECISIONS" in u:
            self.s.decisions.setdefault(a[0], {"account_id": a[17]})
            return "INSERT 0 1"
        if "INSERT INTO BETTOR_DESK_EPOCHS" in u:
            self.s.epochs.setdefault(a[0], {})
            return "INSERT 0 1"
        return "UPDATE 0"


def _pool_for(conn):
    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(self_):
                    return conn

                async def __aexit__(self_, *e):
                    return False
            return _A()

    async def _p():
        return _Pool()
    return _p


def _evt(i, price=0.50, size=800.0, cond="cA"):
    return {"kind": "PRINT", "at": float(1000 + i), "condition_id": cond,
            "outcome_index": 0, "price": price, "size": size,
            "evidence_id": "trade:%d" % i}


async def _run_briefly(conn, seconds=0.09):
    os.environ["BETTOR_DESK_LOOP"] = "1"
    DL.CYCLE_S = 0.01
    task = asyncio.get_running_loop().create_task(DL.run(_pool_for(conn)))
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _fresh_desk(account_id=None):
    d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=OPENING),
                fee_fn=DL.live_fee_fn, desk_id="live1")
    return d


# ── 1. the account is created once and resumed ───────────────────────

@pytest.mark.anyio
async def test_the_account_is_created_once_and_resumed_by_a_restart():
    st = Store()
    conn = FakeConn(st)
    a1 = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    assert a1["created"] is True
    a2 = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    assert a2["created"] is False
    assert a2["account_id"] == a1["account_id"]
    assert len(st.accounts) == 1
    # THE INCIDENT IS RECORDED, once.
    assert ACC.INCIDENT_ID in st.incidents


@pytest.mark.anyio
async def test_a_restart_does_not_replenish_capital_or_reset_performance():
    st = Store()
    conn = FakeConn(st)
    a = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    st.account_state[a["account_id"]] = {
        "cash": 91234.56, "start": OPENING, "realized": -55.44,
        "fees": -3.21, "cursor_event_id": 7777}
    again = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    assert again["created"] is False
    # capital untouched, performance untouched
    s = st.account_state[a["account_id"]]
    assert s["cash"] == 91234.56 and s["realized"] == -55.44


# ── 2. identical restore, including a partial fill ───────────────────

@pytest.mark.anyio
async def test_a_restart_restores_cash_positions_basis_realized_and_orders():
    st = Store()
    conn = FakeConn(st)
    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    aid = acct["account_id"]

    # Build a book: several buys, then a resting order left partly filled.
    live = _fresh_desk()
    live.account_id = aid
    for i in range(14):
        live.step(_evt(i, price=0.48 + 0.004 * (i % 5)))
    await DL._persist(conn, "live1", aid, live, 5014)

    before = {
        "cash": round(live.pf.cash, 6),
        "realized": round(live.pf.realized, 6),
        "fees": round(live.pf.fees, 6),
        "legs": {k: (round(v["qty"], 6), round(v["cost"], 6),
                     round(v["realized"], 6))
                 for k, v in live.pf.legs.items() if v["qty"] > 0},
        "resting": sorted(o.order_id for o in live.orders.values()
                          if o.state in ("RESTING", "PARTIALLY_FILLED")),
        "partials": {o.order_id: (round(o.filled_qty, 6),
                                  o.avg_fill_price)
                     for o in live.orders.values()
                     if o.state == "PARTIALLY_FILLED"},
    }
    assert before["legs"], "the fixture produced no position to restore"

    # RESTART: a brand-new process, brand-new Desk.
    after_desk = _fresh_desk()
    out = await DL._restore(conn, "live1", aid, after_desk)
    assert out["restored"] is True
    assert out["reconciles"] is True, out

    after = {
        "cash": round(after_desk.pf.cash, 6),
        "realized": round(after_desk.pf.realized, 6),
        "fees": round(after_desk.pf.fees, 6),
        "legs": {k: (round(v["qty"], 6), round(v["cost"], 6),
                     round(v["realized"], 6))
                 for k, v in after_desk.pf.legs.items() if v["qty"] > 0},
        "resting": sorted(o.order_id for o in after_desk.orders.values()
                          if o.state in ("RESTING", "PARTIALLY_FILLED")),
        "partials": {o.order_id: (round(o.filled_qty, 6),
                                  o.avg_fill_price)
                     for o in after_desk.orders.values()
                     if o.state == "PARTIALLY_FILLED"},
    }
    assert after["cash"] == before["cash"]
    assert after["realized"] == before["realized"]
    assert after["fees"] == before["fees"]
    assert after["legs"] == before["legs"]
    assert after["resting"] == before["resting"]
    # THE PARTIAL FILL: same filled quantity AND same average price,
    # the latter derived from the reloaded fills rather than copied.
    assert after["partials"] == before["partials"]


@pytest.mark.anyio
async def test_a_partially_filled_order_comes_back_with_its_fills():
    st = Store()
    conn = FakeConn(st)
    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    aid = acct["account_id"]
    live = _fresh_desk()
    live.account_id = aid
    # small prints against a larger order produce partial fills
    for i in range(10):
        live.step(_evt(i, price=0.50, size=60.0))
    await DL._persist(conn, "live1", aid, live, 6010)
    partial = [o for o in live.orders.values()
               if o.state == "PARTIALLY_FILLED"]
    if not partial:
        pytest.skip("this fixture produced no partial fill")
    fresh = _fresh_desk()
    await DL._restore(conn, "live1", aid, fresh)
    for o in partial:
        r = fresh.orders[o.order_id]
        assert r.state == "PARTIALLY_FILLED"
        assert round(r.filled_qty, 6) == round(o.filled_qty, 6)
        assert len(r.fills) == len(o.fills)
        assert r.avg_fill_price == o.avg_fill_price


# ── 3. an interrupted write leaves nothing behind ────────────────────

@pytest.mark.anyio
async def test_an_interrupted_persist_advances_neither_cursor_nor_cash():
    st = Store()
    conn = FakeConn(st)
    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    aid = acct["account_id"]
    live = _fresh_desk()
    live.account_id = aid
    for i in range(12):
        live.step(_evt(i))
    await DL._persist(conn, "live1", aid, live, 7012)
    settled = dict(st.account_state[aid])
    ledger_rows = len(st.ledger)

    # A second batch, interrupted part-way through the transaction.
    for i in range(12, 20):
        live.step(_evt(i))
    st.n = 0
    st.fail_after = 3
    with pytest.raises(RuntimeError):
        await DL._persist(conn, "live1", aid, live, 7020)
    st.fail_after = None

    # NOTHING MOVED. Cursor, cash, realized and the ledger are exactly
    # as the last committed cycle left them.
    assert st.account_state[aid] == settled
    assert st.account_state[aid]["cursor_event_id"] == 7012
    assert len(st.ledger) == ledger_rows


@pytest.mark.anyio
async def test_replaying_the_same_events_does_not_duplicate_fills():
    """Duplicate-event protection: the evidence id is the key."""
    st = Store()
    conn = FakeConn(st)
    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    aid = acct["account_id"]
    live = _fresh_desk()
    live.account_id = aid
    for i in range(10):
        live.step(_evt(i))
    await DL._persist(conn, "live1", aid, live, 8010)
    fills_once = len(st.fills)
    # The same cycle persisted again -- which is what a retry after a
    # failed cycle does.
    await DL._persist(conn, "live1", aid, live, 8010)
    assert len(st.fills) == fills_once

    # And a restored desk re-offered the same evidence takes nothing
    # more, because the consumption ledger came back with it.
    fresh = _fresh_desk()
    await DL._restore(conn, "live1", aid, fresh)
    consumed_before = dict(fresh.cons.consumed)
    assert consumed_before, "the consumption ledger did not come back"
    assert any(v > 0 for v in consumed_before.values())

    # THE GUARANTEE IS THAT ALLOCATION IS NOT RESET, not that nothing
    # remains. My first version asserted take() == 0, which was wrong:
    # a print of 800 whose orders took 600 has 200 legitimately left,
    # and demanding zero would have been testing that the restore
    # DISCARDED available liquidity.
    #
    # What must hold is that `offer` does not reset the consumed figure
    # and that the running total can never exceed what was offered --
    # which is exactly what stops one print filling the same order
    # twice across a restart.
    for eid, prior in consumed_before.items():
        # AVAILABLE IS THE QUEUE-SHARE-LIMITED AMOUNT, not the print's
        # size. My first version wrote `800.0 - prior` and compared
        # against the restored figure, which asserted my own guess
        # about the execution model rather than reading it back.
        avail = fresh.cons.available[eid]
        fresh.cons.offer(eid, 800.0)          # a re-offer must not reset
        assert fresh.cons.available[eid] == avail, (
            "offer() overwrote a restored availability")
        assert fresh.cons.consumed[eid] == prior, (
            "offer() reset a consumed figure that had been restored")
        room = fresh.cons.remaining(eid)
        assert room == pytest.approx(avail - prior)
        took = fresh.cons.take(eid, 10_000.0)
        assert took == pytest.approx(room)
        assert fresh.cons.consumed[eid] <= fresh.cons.available[eid] + 1e-9
        # And now it is spent: a second demand gets nothing.
        assert fresh.cons.take(eid, 10_000.0) == 0.0


# ── 4. overlapping processes ─────────────────────────────────────────

@pytest.mark.anyio
async def test_an_overlapping_process_writes_nothing_and_adds_no_account():
    st = Store()
    holder = FakeConn(st, lock_granted=True)
    await ACC.ensure_account(holder, "live1", opening_balance=OPENING)
    assert len(st.accounts) == 1

    loser = FakeConn(st, lock_granted=False)
    await _run_briefly(loser)
    assert DL.status()["state"] == DL.STATE_STANDBY
    assert len(st.accounts) == 1
    assert st.ledger == []


@pytest.mark.anyio
async def test_a_second_account_creation_is_refused_by_the_database():
    """Even if two starts raced past the check, only one can insert."""
    st = Store()
    c1, c2 = FakeConn(st), FakeConn(st)
    a = await ACC.ensure_account(c1, "live1", opening_balance=OPENING)
    # Simulate the race: c2 already read "no active account" and tries.
    with pytest.raises(RuntimeError):
        await c2.execute(
            "INSERT INTO bettor_desk_accounts (account_id, desk_id, "
            "status, opening_balance, note, provenance) "
            "VALUES ($1,$2,$3,$4,$5,$6::jsonb)",
            "acct_other", "live1", ACC.ACTIVE, OPENING, "x", "{}")
    assert len(st.accounts) == 1
    assert a["account_id"] in st.accounts


# ── 5. unassigned historical rows cannot enter the new book ──────────

@pytest.mark.anyio
async def test_rows_with_no_account_cannot_enter_the_new_book():
    """THE DEFECT THAT CAUSED THIS RESET, pinned.

    96 legs where the running book had 52, because the restore read
    every position row regardless of which book wrote it.
    """
    st = Store()
    conn = FakeConn(st)
    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    aid = acct["account_id"]

    # Historical, unattributable rows: account_id None.
    for i in range(44):
        st.positions[(None, "orphan%d" % i, 0)] = {
            "condition_id": "orphan%d" % i, "outcome_index": 0,
            "qty": 100.0, "cost": 53.81, "realized": 0.0, "fees": 0.0,
            "opened": 1.0, "settled": False, "payout": None,
            "account_id": None}
    st.account_state[aid] = {
        "cash": OPENING, "start": OPENING, "realized": 0.0, "fees": 0.0,
        "cursor_event_id": 9000}

    desk = _fresh_desk()
    out = await DL._restore(conn, "live1", aid, desk)
    assert out["legs"] == 0, "an unassigned row entered the new book"
    assert desk.pf.inventory_cost() == 0.0
    assert desk.pf.cash == OPENING
    assert out["reconciles"] is True


@pytest.mark.anyio
async def test_the_new_book_opens_flat_and_reconciles():
    st = Store()
    conn = FakeConn(st)
    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    desk = _fresh_desk()
    desk.pf.starting_cash = float(acct["opening_balance"])
    out = await DL._restore(conn, "live1", acct["account_id"], desk)
    assert out["restored"] is False          # nothing to restore yet
    assert desk.pf.cash == OPENING
    assert desk.pf.realized == 0.0
    assert desk.pf.invariant()["ok"]


# ── 6. the account is not automatically a FINAL evaluation set ───────

def test_the_new_account_is_not_declared_a_final_evaluation_set():
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_accounts.py")).read()
    assert "NOT a FINAL evaluation set" in src
    assert "frozen policy" in src
    assert ACC.NEW_ACCOUNT_NOTE == (
        "New shadow account following an accounting-recovery defect.")


def test_the_closed_period_is_preserved_and_marked_unreliable():
    assert ACC.PNL_UNRELIABLE == "UNRELIABLE_DO_NOT_QUOTE"
    assert "UNATTRIBUTABLE" in ACC.CLOSED_NOTE
    assert "not closed" in ACC.CLOSED_NOTE
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_accounts.py")).read()
    # No deletion, no compensating entry.
    assert "DELETE FROM" not in src.upper()
    assert "records_deleted" in src and "compensating_cash_posted" in src


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── ids must not collide between books ───────────────────────────────

def test_ids_do_not_collide_across_restarts_of_the_SAME_account():
    """THE DEFECT THE FIRST FIX DID NOT CLOSE.

    Moving the prefix from desk_id to account_id separated the two
    BOOKS. It did nothing about restarts: `self._n` still restarted at
    zero in every process, so two successive processes on the SAME
    account produced byte-identical ids and the upserts did the same
    damage inside one account that they had done between two.
    """
    def proc():
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(),
                    desk_id="live1")
        d.id_prefix = "acct_same"
        return d

    def ids_of(d):
        for i in range(1, 8):
            d.step(_evt(i))
        return sorted(d.orders) + sorted(
            x["desk_decision_id"] for x in d.decisions)

    p1, p2 = proc(), proc()
    a, b = ids_of(p1), ids_of(p2)

    # THE SAME EVENTS REGENERATE THE SAME IDS -- that is idempotency,
    # and it is why a replayed event is absorbed by ON CONFLICT rather
    # than duplicating.
    assert a == b

    # DIFFERENT EVENTS MUST NOT COLLIDE. This is the property the
    # counter lost on restart.
    p3 = proc()
    for i in range(100, 108):
        p3.step(_evt(i))
    later = set(p3.orders) | {x["desk_decision_id"] for x in p3.decisions}
    assert not (set(a) & later), sorted(set(a) & later)[:3]

    # And the id carries the EVIDENCE ID, so it is traceable to input.
    assert any("trade:1" in x for x in a), a[:3]


def test_the_superseded_counter_id_really_did_collide():
    """CONTROL. Without this, the test above could be asserting a
    property the old code also had."""
    def proc():
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(),
                    desk_id="live1")
        d.id_prefix = "acct_same"
        return d
    p1, p2 = proc(), proc()
    old1 = [p1._legacy_next_id("O") for _ in range(6)]
    old2 = [p2._legacy_next_id("O") for _ in range(6)]
    assert old1 == old2, "the superseded form did not collide"
    # and it carries no trace of the event that produced it
    assert not any("trade:" in x for x in old1)


@pytest.mark.anyio
async def test_a_restart_of_the_same_account_overwrites_none_of_its_own_rows():
    """THE REQUIREMENT: tested against EXISTING rows in the SAME
    account, not only against UNASSIGNED ones."""
    st = Store()
    conn = FakeConn(st)
    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    aid = acct["account_id"]

    # PROCESS 1 trades events 1..8 and commits.
    p1 = _fresh_desk()
    p1.account_id = aid
    p1.id_prefix = aid
    for i in range(1, 9):
        p1.step(_evt(i))
    await DL._persist(conn, "live1", aid, p1, 8)
    orders_after_1 = {k: dict(v) for k, v in st.orders.items()}
    fills_after_1 = {k: dict(v) for k, v in st.fills.items()}
    decisions_after_1 = set(st.decisions)
    assert orders_after_1, "the fixture wrote no orders"

    # PROCESS 2: restart, restore, then trade NEW events 9..16.
    p2 = _fresh_desk()
    p2.account_id = aid
    p2.id_prefix = aid
    await DL._restore(conn, "live1", aid, p2)
    for i in range(9, 17):
        p2.step(_evt(i))
    await DL._persist(conn, "live1", aid, p2, 16)

    # NOT ONE of process 1's rows changed.
    for oid, before in orders_after_1.items():
        assert st.orders[oid] == before, oid
    for fid, before in fills_after_1.items():
        assert st.fills[fid] == before, fid
    # and process 1's decisions are all still present
    assert decisions_after_1 <= set(st.decisions)
    # and process 2 actually wrote something, or this proves nothing
    assert len(st.decisions) > len(decisions_after_1)


def test_generated_ids_are_namespaced_by_the_book_not_the_desk():
    """CAUGHT IN PRODUCTION minutes after the reset.

    `_next_id` prefixed with `desk_id` -- a constant -- and the counter
    restarts at zero in every process. So the new book's first order was
    `live1-O-000001`, which the PREVIOUS book had already written, and
    the upserts did what they were told with a colliding key: orders
    OVERWROTE a preserved record, decisions were SILENTLY DROPPED, and
    fills collided because fill_id is order_id:index.
    """
    a = DK.Desk(policy=DK.Policy(), limits=DK.Limits(), desk_id="live1")
    b = DK.Desk(policy=DK.Policy(), limits=DK.Limits(), desk_id="live1")
    a.id_prefix, b.id_prefix = "acct_aaa", "acct_bbb"
    ids_a = {a._next_id("O") for _ in range(50)}
    ids_b = {b._next_id("O") for _ in range(50)}
    assert not (ids_a & ids_b), sorted(ids_a & ids_b)[:3]

    # CONTROL: with the OLD behaviour the two books collide completely,
    # so the test above is detecting a real property and not a tautology.
    c = DK.Desk(policy=DK.Policy(), limits=DK.Limits(), desk_id="live1")
    d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(), desk_id="live1")
    assert {c._next_id("O") for _ in range(50)} == \
           {d._next_id("O") for _ in range(50)}


@pytest.mark.anyio
async def test_a_new_book_does_not_overwrite_the_closed_books_rows():
    """The whole point of the account id, verified end to end."""
    st = Store()
    conn = FakeConn(st)
    # A preserved row from the closed period.
    st.positions[(None, "shared", 0)] = {
        "condition_id": "shared", "outcome_index": 0, "qty": 500.0,
        "cost": 240.0, "realized": 0.0, "fees": 0.0, "opened": 1.0,
        "settled": False, "payout": None, "account_id": None}
    st.orders["live1-O-000001"] = {
        "order_id": "live1-O-000001", "state": "FILLED",
        "account_id": None, "condition_id": "shared",
        "outcome_index": 0, "side": "BUY", "intent": "ENTER",
        "limit_price": 0.48, "qty": 500.0, "filled_qty": 500.0,
        "avg_fill_price": 0.48, "fees_usd": 0.0,
        "desk_decision_id": "live1-D-000001", "placed": 1.0,
        "expires": 2.0}

    acct = await ACC.ensure_account(conn, "live1", opening_balance=OPENING)
    aid = acct["account_id"]
    live = _fresh_desk()
    live.account_id = aid
    live.id_prefix = aid
    for i in range(12):
        live.step(_evt(i, cond="shared"))
    await DL._persist(conn, "live1", aid, live, 9012)

    # The preserved row is untouched, and still unassigned.
    old = st.positions[(None, "shared", 0)]
    assert old["qty"] == 500.0 and old["cost"] == 240.0
    assert old["account_id"] is None
    assert st.orders["live1-O-000001"]["account_id"] is None
    assert st.orders["live1-O-000001"]["state"] == "FILLED"
    # and the new book wrote its own rows under its own key
    assert any(k[0] == aid for k in st.positions)
    assert all(o["account_id"] == aid for oid, o in st.orders.items()
               if oid.startswith(aid))


# ── containment: a paused desk performs no work and writes nothing ────

class PausedConn(FakeConn):
    def __init__(self, store, paused=True, readable=True):
        super().__init__(store, lock_granted=True)
        self.paused = paused
        self.readable = readable

    async def fetchrow(self, sql, *a):
        if "paused" in sql and "bettor_desk_accounts" in sql:
            if not self.readable:
                raise RuntimeError("flag read failed")
            return {"paused": self.paused,
                    "pause_reason": "ACCOUNTING_RECOVERY",
                    "accounting_status": "ACCOUNTING_UNCERTAIN"}
        return await super().fetchrow(sql, *a)


async def _drive(conn, seconds=0.09):
    os.environ["BETTOR_DESK_LOOP"] = "1"
    DL.CYCLE_S = 0.01
    task = asyncio.get_running_loop().create_task(DL.run(_pool_for(conn)))
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.anyio
async def test_a_paused_account_processes_nothing_and_writes_nothing():
    st = Store()
    conn = PausedConn(st, paused=True)
    await _drive(conn)
    assert DL.status()["state"] == DL.STATE_PAUSED, DL.status()
    assert DL.status()["paused"] is True
    # NOT EVEN A LEDGER SNAPSHOT. A paused desk that kept appending
    # snapshots would look like a running one on the page.
    assert st.ledger == []
    assert st.orders == {}
    assert st.fills == {}
    # AND NOT AN EPOCH ROW EITHER, which is the assertion this test was
    # missing. `Store` tracked epochs all along and nothing looked at
    # them, so "writes nothing" passed while the desk was in fact
    # ending one epoch and opening another on every paused boot --
    # visible in production as epoch 4 opened at 16:51:40Z against an
    # account flagged paused at 16:51:38Z. The pause is now read before
    # any startup write, and this is what holds it there.
    assert st.epochs == {}, st.epochs
    # The book is deliberately NOT restored while paused: restoring and
    # then idling would leave a live portfolio in memory that a cleared
    # flag could resume from without re-reading it.
    assert (DL.status().get("restore") or {}).get("restored") is False


@pytest.mark.anyio
async def test_an_unreadable_pause_flag_is_treated_as_paused():
    """FAIL CLOSED. A desk that trades while unsure whether it was
    stopped is worse than one that idles."""
    st = Store()
    conn = PausedConn(st, readable=False)
    await _drive(conn)
    s = DL.status()
    assert s["state"] == DL.STATE_PAUSED, s
    assert "UNREADABLE" in (s["pause_reason"] or "")
    assert st.ledger == []


@pytest.mark.anyio
async def test_an_unpaused_account_is_NOT_held(monkeypatch):
    """CONTROL. If the pause fired regardless, the desk could never run
    and the two tests above would prove nothing."""
    st = Store()
    conn = PausedConn(st, paused=False)
    await _drive(conn)
    assert DL.status()["state"] == DL.STATE_RUNNING, DL.status()
    assert DL.status()["paused"] is False


def test_the_pause_is_read_every_cycle_not_once_at_startup():
    """So pausing and resuming need no deploy."""
    import ast
    src = open(os.path.join(_ROOT, "sportsassets",
                            "bettor_desk_loop.py")).read()
    run = next(n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
    loops = [n for n in ast.walk(run) if isinstance(n, ast.While)]
    cyclic = [lp for lp in loops if "_pause" in ast.unparse(lp)]
    assert cyclic, "the pause is not checked inside the cycle loop"
