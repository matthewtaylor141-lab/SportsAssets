"""AN ACCOUNT BECOMES ELIGIBLE BY RECONCILIATION, NEVER BY INSERT.

THE CLAIM THIS FILE REFUSES. "Register a new account id as ACTIVE with CLEAN
accounting" was offered as a way forward. It is the system certifying itself:
the row is one this code wrote, and `accounting_status = 'CLEAN'` beside an
unreconciled id is a sentence, not evidence.

So: a fresh registration is PENDING_VERIFICATION / UNVERIFIED / paused; only a
clean four-way reconciliation against the VENUE moves it; an unreadable check
blocks exactly as hard as a discrepancy; and the paused account's only route
out is the same route in.
"""

from __future__ import annotations

import time

import pytest

from sportsassets import bettor_account_onboarding as ON
from sportsassets import bettor_funded_activation as FA

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-onboard-test"
VENUE = "PMUS"


# ── A VENUE THAT ANSWERS, AND ONE THAT DOES NOT ─────────────────────

class _Portfolio:
    def __init__(self, positions=None, fail=False, never_eof=False):
        self._positions = positions if positions is not None else {}
        self._fail = fail
        self._never_eof = never_eof

    def positions(self, params):
        if self._fail:
            raise RuntimeError("venue 503")
        if self._never_eof:
            return {"positions": self._positions, "nextCursor": "more",
                    "eof": False}
        return {"positions": self._positions, "nextCursor": "", "eof": True}


class _OrdersList:
    def __init__(self, rows=None):
        self._rows = rows or []

    def list(self, params):
        return {"orders": self._rows}


class _NoBalanceVenue:
    """An adapter WITHOUT a balance read -- the real `pmus` shape.

    It is a separate class rather than a flag, because the first version of
    this fixture did `del self.__class__.balance`, which removed the attribute
    from the CLASS and silently broke every later test in the file. A missing
    capability is a different type, not a mutated one.
    """
    __name__ = "fake_adapter_no_balance"

    def __init__(self, *, positions=None, orders=None, trades=None):
        self._portfolio = _Portfolio(positions)
        self._orders = _OrdersList(orders)
        self._trades = trades or {}

    def _get_client(self):
        outer = self

        class _C:
            portfolio = outer._portfolio
            orders = outer._orders
        return _C()

    def open_orders(self):
        return self._orders.list(None)["orders"]

    def market_trades(self, slug, since_ts=None):
        return list(self._trades.get(slug) or [])


class _Venue:
    """A stand-in for the ADAPTER MODULE, carrying only what onboarding reads.

    It is not a venue simulator: it answers the four reads and nothing else,
    which is the whole surface under test here.
    """
    __name__ = "fake_adapter"

    def __init__(self, *, positions=None, orders=None, balance=None,
                 trades=None, fail_positions=False, never_eof=False,
                 fail_trades=False):
        self._portfolio = _Portfolio(positions, fail_positions, never_eof)
        self._orders = _OrdersList(orders)
        self._balance = balance
        self._trades = trades or {}
        self._fail_trades = fail_trades

    # the transport seam, same name the real adapter uses
    def _get_client(self):
        outer = self

        class _C:
            portfolio = outer._portfolio
            orders = outer._orders
        return _C()

    def open_orders(self):
        return self._orders.list(None)["orders"]

    def balance(self):
        if self._balance is None:
            raise RuntimeError("balance read failed")
        return dict(self._balance)

    def market_trades(self, slug, since_ts=None):
        if self._fail_trades:
            raise RuntimeError("activities 500")
        return list(self._trades.get(slug) or [])


def _clean_venue(**kw):
    return _Venue(positions={}, orders=[], balance={"cash": 1000.0}, **kw)


async def _drop(conn):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)


# ── 1 · THE STATEMENT, AND THE SHAPE A NEW ROW GETS ─────────────────

def test_a_new_registry_id_is_not_evidence_of_clean_accounting():
    d = ON.describe()
    assert d["created_as"] == {"status": "PENDING_VERIFICATION",
                               "accounting_status": "UNVERIFIED",
                               "paused": True}
    # and the created accounting status is NOT one activation accepts
    assert ON.NEW_ACCOUNTING not in FA.ACCOUNTING_OK
    assert "PENDING_VERIFICATION" != "ACTIVE"
    # only RECONCILED passes; a non-discrepancy is not enough
    assert ON.PASSING == (ON.RECONCILED,)
    assert ON.UNREADABLE not in ON.PASSING
    assert ON.NOT_SUPPORTED not in ON.PASSING


def test_the_real_adapter_cannot_read_balances_and_that_is_reported():
    """THE ENGINEERING GAP, NAMED. `pmus` exposes positions, activities and
    orders.list and nothing that states the account's cash, so check 1 cannot
    pass on the funded venue today. It is reported as NOT_SUPPORTED with the
    missing read named -- not skipped, and not assumed fine."""
    from sportsassets import pmus

    got = ON.read_balances(pmus)
    assert got["verdict"] == ON.NOT_SUPPORTED, got
    assert got["refusal"] == "ADAPTER_CANNOT_READ_BALANCES"
    assert got["engineering_gap"] is True
    assert "balance read" in got["missing"]
    # and it is listed in describe(), so a readback prints it
    assert any("balance read" in g
               for g in ON.describe()["known_adapter_gaps_on_pmus"])


# ── 2 · EACH READ'S THREE OUTCOMES ──────────────────────────────────

def test_an_unreadable_position_list_blocks_exactly_like_a_discrepancy():
    """'WE COULD NOT LOOK' MUST NOT RENDER AS 'WE LOOKED AND IT WAS EMPTY'."""
    hard = ON.read_positions(_Venue(fail_positions=True))
    assert hard["verdict"] == ON.UNREADABLE
    assert hard["verdict"] not in ON.PASSING

    # PAGING THAT NEVER REACHED eof IS ALSO UNREADABLE, not an empty account
    partial = ON.read_positions(_Venue(positions={"aec-a": {"netPosition": 5}},
                                       never_eof=True))
    assert partial["verdict"] == ON.UNREADABLE, partial
    assert "did not reach eof" in partial["why"]

    # A netPosition THAT DOES NOT PARSE IS NOT ZERO
    weird = ON.read_positions(_Venue(positions={"aec-a": {"netPosition": "?"}}))
    assert weird["verdict"] == ON.UNREADABLE, weird

    # AND A GENUINELY EMPTY, FULLY PAGED ACCOUNT RECONCILES
    empty = ON.read_positions(_Venue(positions={}))
    assert empty["verdict"] == ON.RECONCILED
    assert empty["count"] == 0


def test_unreadable_executions_block_and_a_window_is_stated():
    v = _Venue(positions={"aec-a": {"netPosition": 3}},
               trades={"aec-a": [{"id": 1}]})
    ok = ON.read_executions(v, since_ts=time.time() - 60, slugs=["aec-a"])
    assert ok["verdict"] == ON.RECONCILED
    assert ok["count"] == 1
    assert ok["window_s"] == ON.EXECUTION_WINDOW_S

    bad = ON.read_executions(_Venue(fail_trades=True),
                             since_ts=time.time() - 60, slugs=["aec-a"])
    assert bad["verdict"] == ON.UNREADABLE
    assert "aec-a" in bad["errors"]


# ── 3 · THE WHOLE RECONCILIATION, AND WHAT IT WILL NOT WAVE THROUGH ──

@pg
@pytest.mark.asyncio
async def test_a_fresh_registration_is_not_eligible_and_activation_refuses_it():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop(conn)
        reg = await ON.register(conn, account_id=ACCT, venue=VENUE,
                               desk_id="desk-onboard-test",
                               note="onboarding test", by="pytest")
        assert reg["ok"] is True
        assert reg["account"]["status"] == "PENDING_VERIFICATION"
        assert reg["account"]["accounting_status"] == "UNVERIFIED"
        assert reg["account"]["paused"] is True

        # AND THE ACTIVATION PATH REFUSES IT, on its own row
        sel = await FA.account_selection(conn, ACCT)
        assert sel["ok"] is False
        assert sel["refusal"] in (FA.R_ACCOUNT_NOT_ACTIVE,
                                 FA.R_ACCOUNT_PAUSED,
                                 FA.R_ACCOUNTING_UNCERTAIN)
    finally:
        await _drop(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_mark_eligible_writes_nothing_when_a_check_does_not_pass():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop(conn)
        await ON.register(conn, account_id=ACCT, venue=VENUE,
                          desk_id="desk-onboard-test", note="t", by="pytest")
        # THE REAL ADAPTER'S GAP: no balance read -> NOT_SUPPORTED -> blocked
        got = await ON.mark_eligible(conn, account_id=ACCT, venue=VENUE,
                                     by="pytest",
                                     adapter=_NoBalanceVenue(positions={},
                                                             orders=[]))
        assert got["ok"] is False
        assert got["refusal"] == ON.R_NOT_RECONCILED
        assert got["wrote"] is False
        blocked = {b["check"] for b in got["reconciliation"]["blocking"]}
        assert "balances" in blocked, got["reconciliation"]["blocking"]
        # NOTHING MOVED
        row = await conn.fetchrow(
            "SELECT status, paused, accounting_status "
            "  FROM bettor_desk_accounts WHERE account_id=$1", ACCT)
        assert row["status"] == "PENDING_VERIFICATION"
        assert row["paused"] is True
        assert row["accounting_status"] == "UNVERIFIED"
    finally:
        await _drop(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_venue_position_this_book_never_booked_is_a_discrepancy():
    """THE FINDING THAT MATTERS MOST ON ONBOARDING. The venue holds exposure
    with no row here. Marking the account clean would adopt it silently, and
    its cost basis would be a guess from then on."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop(conn)
        await ON.register(conn, account_id=ACCT, venue=VENUE,
                          desk_id="desk-onboard-test", note="t", by="pytest")
        v = _clean_venue()
        v._portfolio = _Portfolio({"aec-nobody-booked-this": {
            "netPosition": 7}})
        v._trades = {"aec-nobody-booked-this": [{"id": 1}]}
        got = await ON.mark_eligible(conn, account_id=ACCT, venue=VENUE,
                                     by="pytest", adapter=v)
        assert got["ok"] is False, got
        found = [c for c in got["reconciliation"]["checks"]
                 if c["verdict"] == ON.DISCREPANCY]
        assert found, got["reconciliation"]["checks"]
        assert "aec-nobody-booked-this" in found[0][
            "venue_holds_positions_this_book_does_not_know_about"]
        assert got["wrote"] is False
    finally:
        await _drop(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_clean_four_way_reconciliation_is_what_makes_it_eligible():
    """AND WHEN ALL FOUR ANSWER, the row moves -- with the evidence stamped on
    it, so a later reader can see what it was cleared against."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop(conn)
        await ON.register(conn, account_id=ACCT, venue=VENUE,
                          desk_id="desk-onboard-test", note="t", by="pytest")
        got = await ON.mark_eligible(conn, account_id=ACCT, venue=VENUE,
                                     by="pytest", adapter=_clean_venue())
        assert got["ok"] is True, got
        assert got["wrote"] is True
        assert got["reconciliation"]["verdicts"] == {
            "balances": ON.RECONCILED, "positions": ON.RECONCILED,
            "open_orders": ON.RECONCILED, "executions": ON.RECONCILED}
        row = await conn.fetchrow(
            "SELECT status, paused, accounting_status, last_verified_at, "
            "       last_verified_detail FROM bettor_desk_accounts "
            " WHERE account_id=$1", ACCT)
        assert row["status"] == "ACTIVE"
        assert row["paused"] is False
        assert row["accounting_status"] == "RECONCILED"
        assert row["accounting_status"] in FA.ACCOUNTING_OK
        assert row["last_verified_at"] is not None
        assert row["last_verified_detail"]          # the evidence is stamped
        # AND NOW ACTIVATION ACCEPTS IT
        sel = await FA.account_selection(conn, ACCT)
        assert sel["ok"] is True, sel
        assert sel["accounting_status"] == "RECONCILED"
    finally:
        await _drop(conn)
        await conn.close()


# ── 4 · THE PAUSED ACCOUNT STAYS PAUSED ─────────────────────────────

@pg
@pytest.mark.asyncio
async def test_the_paused_account_is_not_unpaused_by_a_decision_to_unpause_it():
    """acct_fc2d773a2afa4851's shape: paused, accounting unresolved. The only
    route out is the same four reconciliations, and an unreadable venue leaves
    it exactly where it was."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    paused = "acct-paused-onboard-test"
    try:
        await conn.execute(
            "DELETE FROM bettor_desk_accounts WHERE account_id=$1", paused)
        await conn.execute(
            "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
            " paused, pause_reason, accounting_status, opening_balance, note) "
            "VALUES ($1,'desk-paused-test','ACTIVE',TRUE,'accounting "
            "unresolved','UNCERTAIN',0,'stands in for the real one')", paused)

        got = await ON.resolve_existing(conn, account_id=paused, venue=VENUE,
                                        by="pytest",
                                        adapter=_Venue(fail_positions=True,
                                                       balance={"cash": 1.0},
                                                       orders=[]))
        assert got["ok"] is False
        assert got["still_paused"] is True
        assert "did not resolve it" in got["why_it_stays_paused"]
        row = await conn.fetchrow(
            "SELECT paused, accounting_status FROM bettor_desk_accounts "
            " WHERE account_id=$1", paused)
        assert row["paused"] is True
        assert row["accounting_status"] == "UNCERTAIN"
        # and activation still refuses it by canonical id
        sel = await FA.account_selection(conn, paused)
        assert sel["ok"] is False
        assert sel["refusal"] == FA.R_ACCOUNT_PAUSED
    finally:
        await conn.execute(
            "DELETE FROM bettor_desk_accounts WHERE account_id=$1", paused)
        await conn.close()


def test_nothing_in_the_module_unpauses_without_reconciling():
    """A STRUCTURAL CHECK. Every UPDATE that clears `paused` must sit behind
    the reconciliation verdict, so a future edit cannot add a second route."""
    import pathlib
    import re

    src = pathlib.Path(ON.__file__).read_text()
    updates = [m.start() for m in re.finditer(r"paused=FALSE", src)]
    assert updates, "the module must be able to unpause at all"
    for at in updates:
        # the guard `if not rec.get("eligible")` must appear before it
        assert 'if not rec.get("eligible")' in src[:at], (
            "an unpause that is not behind the reconciliation verdict")
