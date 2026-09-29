"""THE DISCREPANCY REPORT: THE VENUE AND THE BOOK, COMPARED BOTH WAYS.

`bettor_account_onboarding.discrepancy_report` is the owner's evidence about
an account. These tests hold what it must do and what it must never do:

  * both directions -- a venue holding, order or execution the book does not
    know, AND a book position, working order or fill the venue does not show;
    a signed quantity that differs; a legacy live-beta holding classified as
    legacy rather than unexplained;
  * the strict readers decide `authoritative`: an absent `netPosition` is
    UNREADABLE, never flat;
  * the history is append-only and the latest-evidence key is still written;
  * it never unpauses an account, changes an accounting status, or calls an
    order-mutating method -- checked by running it against a client that
    refuses every such call, and by reading its source;
  * the paused shadow desk account's report says a venue reconciliation does
    not address its identifier integrity, and counts the desk's ids by scheme;
  * `GET /api/admin/funded-account-eligibility` reads the field
    `reconciliation_evidence` actually returns.

THE VENUE IS A STAND-IN AT THE SDK BOUNDARY: SYNTHETIC pages, no network, and
no order is sent -- the stand-in raises on any attempt.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_account_onboarding as ON
from sportsassets import bettor_funded_account as ACC

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-d5a-report-test"
VENUE = "PMUS"
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
MUTATING = ("create", "cancel", "modify", "cancel_all", "close_position",
            "preview", "retrieve")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _amt(v):
    return {"value": str(v), "currency": "USD"}


# ── A VENUE STAND-IN THAT REFUSES EVERY ORDER-MUTATING CALL ──────────

class _Resource:
    def __init__(self, name, answers, calls):
        self._name, self._answers, self._calls = name, answers, calls

    def __getattr__(self, meth):
        if meth.startswith("_"):
            raise AttributeError(meth)

        def call(*a, **k):
            self._calls.append("%s.%s" % (self._name, meth))
            if meth in MUTATING or meth not in self._answers:
                raise AssertionError("the report called %s.%s"
                                     % (self._name, meth))
            ans = self._answers[meth]
            if callable(ans):
                return ans(*a, **k)
            if isinstance(ans, list):
                if not ans:
                    raise AssertionError("read past the last page")
                page = ans.pop(0)
                if isinstance(page, Exception):
                    raise page
                return page
            return ans
        return call


class _Venue:
    """A stand-in for the ADAPTER MODULE: `_get_client()` and `balances()`,
    the two things the report reaches. SYNTHETIC answers."""
    __name__ = "fake_adapter_d5a"

    def __init__(self, *, positions=None, orders=None, activities=None,
                 reserved=0.0, balance_rows=None):
        self.calls: list = []
        self._pos = positions if positions is not None else [
            {"positions": {}, "eof": True}]
        self._orders = orders if orders is not None else {"orders": []}
        self._acts = activities if activities is not None else [
            {"activities": [], "eof": True}]
        self._bal = balance_rows if balance_rows is not None else [
            {"currency": "USD", "currentBalance": 1000.0,
             "buyingPower": 1000.0, "openOrders": reserved,
             "unsettledFunds": 0.0, "balanceReservation": 0.0,
             "absent_fields": [], "pending_withdrawals": 0}]

    def _get_client(self):
        outer = self

        class _C:
            portfolio = _Resource("portfolio", {
                "positions": outer._pos, "activities": outer._acts},
                outer.calls)
            orders = _Resource("orders", {"list": lambda p=None:
                                          outer._orders}, outer.calls)
            account = _Resource("account", {}, outer.calls)
        return _C()

    def balances(self):
        self.calls.append("account.balances")
        return {"endpoint": "/v1/account/balances", "currencies": 1,
                "balances": [dict(r) for r in self._bal]}


def _pos(net, cost=5.0):
    return {"netPosition": str(net), "cost": _amt(cost)}


def _order(oid, slug, *, leaves=10, price="0.50", intent=LONG):
    return {"id": oid, "marketSlug": slug, "intent": intent,
            "state": "ORDER_STATE_NEW", "price": _amt(price),
            "quantity": 10, "leavesQuantity": leaves, "cumQuantity": 0}


def _trade(tid, ts, slug, own, qty, exec_id=None):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"id": tid, "marketSlug": slug, "createTime": ts,
                      "qty": str(qty), "price": _amt("0.50"),
                      "isAggressor": True,
                      "aggressorExecution": {"id": exec_id or "x-" + tid,
                                             "order": {"id": own}}}}


# ── THE BOOK, WRITTEN DIRECTLY (one open group is the database's bound) ──

async def _clean(conn, account=ACCT):
    await conn.execute(
        "DELETE FROM bettor_funded_fills WHERE intent_id IN (SELECT "
        " intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        account)
    await conn.execute(
        "DELETE FROM bettor_funded_economics WHERE intent_id IN (SELECT "
        " intent_id FROM bettor_funded_intents WHERE account_id=$1)",
        account)
    await conn.execute(
        "DELETE FROM bettor_funded_intents WHERE account_id=$1 "
        "   AND kind='EXIT'", account)
    await conn.execute(
        "DELETE FROM bettor_funded_intents WHERE account_id=$1", account)
    await conn.execute(
        "DELETE FROM bettor_funded_portfolio_groups WHERE account_id=$1",
        account)
    await conn.execute("DELETE FROM live_orders WHERE asset LIKE 'd5a-%'")
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       account)
    await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                       ON.RECONCILIATION_KEY)
    async with conn.transaction():
        # The history is append-only by trigger; a test removes its own rows
        # with triggers suspended for ITS transaction only.
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM bettor_account_reconciliation_reports "
            " WHERE account_id=$1", account)


async def _group(conn, gid, event, account=ACCT):
    await conn.execute(
        "INSERT INTO bettor_funded_portfolio_groups (group_id, account_id, "
        " venue, event_key, structure, hedge_intent) VALUES "
        " ($1,$2,$3,$4,'INDIRECT_MIDDLE','ACQUIRED')",
        gid, account, VENUE, event)


async def _leg(conn, iid, *, slug, event, intent=LONG, qty=10, gid=None,
               role=None, state="FILLED", order_id=None, account=ACCT,
               fill_id=None, kind="ENTRY", parent=None):
    await conn.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue, "
        " venue_class, us_market_slug, event_key, order_intent, limit_price, "
        " quantity, collateral_usd, effective_digest, state, kind, "
        " parent_intent_id, residual_qty, portfolio_group_id, leg_role, "
        " venue_order_id) VALUES ($1,$2,$3,'FUNDED',$4,$5,$6,0.5,$7,$8,'d',"
        " $9,$10,$11,$12,$13,$14,$15)",
        iid, account, VENUE, slug, event, intent, qty,
        5.0 if kind == "ENTRY" else 0.0, state, kind, parent,
        qty if (kind == "ENTRY" and state == "FILLED") else 0, gid, role,
        order_id)
    if fill_id:
        await conn.execute(
            "INSERT INTO bettor_funded_fills (fill_id, intent_id, "
            " venue_order_id, venue_fill_id, at, qty, price, cash_usd, "
            " fee_usd, fee_basis, direction) VALUES "
            " ($1,$2,$3,$4,now() - interval '1 hour',$5,0.5,$6,0,'TEST',"
            "  'ENTRY')",
            "fvf:%s:%s" % (order_id, fill_id), iid, order_id, fill_id, qty,
            qty * 0.5)


async def _legacy(conn, *, asset, slug, status="filled", order_id=None,
                  filled=3):
    await conn.execute(
        "INSERT INTO live_orders (whale_username, asset, side, his_price, "
        " limit_price, requested_usd, requested_shares, status, "
        " filled_shares, us_market_slug, order_id, venue) VALUES "
        " ('d5a-test',$1,'BUY',0.5,0.5,1.5,$2,$3,$2,$4,$5,'polymarket-us')",
        asset, filled, status, slug, order_id)


def _names(rep):
    return [f["finding"] for f in rep["discrepancies"]]


def _one(rep, name, **match):
    hits = [f for f in rep["discrepancies"] if f["finding"] == name
            and all(f.get(k) == v for k, v in match.items())]
    assert len(hits) == 1, (name, match, rep["discrepancies"])
    return hits[0]


# ═════════════════════════════════════════════════════════════════════
# 1 · BOTH DIRECTIONS, STRICTLY
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_report_finds_what_each_side_holds_that_the_other_does_not():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    now = datetime.now(timezone.utc)
    try:
        await _clean(conn)
        await _group(conn, "grp:d5a-rpt", "ev-d5a-rpt")
        # BOOKED AND HELD: the venue agrees, and the execution pairs by id
        await _leg(conn, "fpi-d5a-held", slug="aec-d5a-held",
                   event="ev-d5a-rpt", gid="grp:d5a-rpt", role="PRIMARY",
                   order_id="vo-d5a-held", fill_id="vx-held-1")
        # BOOKED, NOT HELD AT THE VENUE: a position and a fill it lacks
        await _leg(conn, "fpi-d5a-gone", slug="aec-d5a-gone",
                   event="ev-d5a-rpt", gid="grp:d5a-rpt", role="HEDGE",
                   intent=SHORT, qty=4, order_id="vo-d5a-gone",
                   fill_id="vx-gone-1")
        # A WORKING EXIT the book records and the venue does not list
        await _leg(conn, "fpi-d5a-exit", slug="aec-d5a-held",
                   event="ev-d5a-rpt", kind="EXIT", parent="fpi-d5a-held",
                   state="ACKNOWLEDGED", order_id="vo-d5a-exit")
        # A LEGACY LIVE-BETA HOLDING and a legacy order
        await _legacy(conn, asset="d5a-legacy-asset", slug="aec-d5a-legacy",
                      order_id="vo-d5a-legacy")
        v = _Venue(
            positions=[{"positions": {
                "aec-d5a-held": _pos(10), "aec-d5a-unbooked": _pos(7),
                "aec-d5a-legacy": _pos(3)}, "eof": True}],
            orders={"orders": [_order("vo-d5a-stranger", "aec-d5a-unbooked"),
                               _order("vo-d5a-legacy", "aec-d5a-legacy")]},
            reserved=10.0,
            activities=[{"activities": [
                _trade("t-held", _iso(now - timedelta(hours=1)),
                       "aec-d5a-held", "vo-d5a-held", 10,
                       exec_id="vx-held-1"),
                _trade("t-unbooked", _iso(now - timedelta(hours=2)),
                       "aec-d5a-unbooked", "vo-d5a-nobody", 7),
                _trade("t-legacy", _iso(now - timedelta(hours=3)),
                       "aec-d5a-legacy", "vo-d5a-legacy", 3),
            ], "eof": True}])
        rep = await ON.discrepancy_report(conn, account_id=ACCT, venue=VENUE,
                                          adapter=v, now=now.timestamp())
        assert rep["ok"] is True
        # EVERY READ FINISHED BY ITS OWN TERMINATION
        assert rep["authoritative"] is True, rep["reads"]
        assert rep["completeness"]["complete"] is True

        # ── VENUE -> BOOK ──────────────────────────────────────────────
        u = _one(rep, ON.F_VENUE_POSITION_UNBOOKED,
                 us_market_slug="aec-d5a-unbooked")
        assert u["venue_net_position"] == 7.0 and u["blocking"] is True
        # A LEGACY HOLDING IS REPORTED AS LEGACY, NOT AS UNEXPLAINED
        lg = _one(rep, ON.F_LEGACY_POSITION, us_market_slug="aec-d5a-legacy")
        assert lg["legacy"]["live_orders"]["filled_shares"] == 3.0
        assert not [f for f in rep["discrepancies"]
                    if f["finding"] == ON.F_VENUE_POSITION_UNBOOKED
                    and f["us_market_slug"] == "aec-d5a-legacy"]
        _one(rep, ON.F_VENUE_ORDER_UNKNOWN, venue_order_id="vo-d5a-stranger")
        _one(rep, ON.F_LEGACY_ORDER, venue_order_id="vo-d5a-legacy")
        # AN UNBOOKED VENUE EXECUTION
        ex = _one(rep, ON.F_EXECUTION_UNBOOKED, venue_order_id="vo-d5a-nobody")
        assert ex["venue_qty"] == 7.0 and ex["blocking"] is True
        # a legacy order's executions are the legacy lane's, and do not block
        le = _one(rep, ON.F_LEGACY_EXECUTION, venue_order_id="vo-d5a-legacy")
        assert le["blocking"] is False
        # THE HELD, BOOKED POSITION AND ITS PAIRED EXECUTION ARE NOT FINDINGS
        assert not [f for f in rep["discrepancies"]
                    if f.get("us_market_slug") == "aec-d5a-held"
                    and f["check"] == "positions"]
        assert not [f for f in rep["discrepancies"]
                    if f.get("venue_order_id") == "vo-d5a-held"]

        # ── BOOK -> VENUE (map6: never checked before) ────────────────
        g = _one(rep, ON.F_BOOK_POSITION_NOT_AT_VENUE,
                 us_market_slug="aec-d5a-gone")
        assert g["book_net_position"] == -4.0      # a short is negative
        f = _one(rep, ON.F_FILL_NOT_AT_VENUE, venue_order_id="vo-d5a-gone")
        assert f["booked_unpaired_qty"] == 4.0
        o = _one(rep, ON.F_BOOK_ORDER_NOT_AT_VENUE,
                 venue_order_id="vo-d5a-exit")
        assert ON.GAP_TERMINAL_ORDER_HISTORY in o["why"]

        # ── THE VERDICT FIELDS ────────────────────────────────────────
        assert rep["would_be_eligible"] is False
        assert rep["pause_kept"] is True
        assert rep["blocking"] and all(b["blocking"] for b in rep["blocking"])
        assert {g["gap"] for g in rep["gaps"]} >= {
            ON.GAP_TERMINAL_ORDER_HISTORY, ON.GAP_ACCOUNT_IDENTITY,
            ON.GAP_CREDENTIAL_CAPABILITY}
        # THE STAND-IN WAS ASKED ONLY TO READ
        assert set(v.calls) <= {"account.balances", "orders.list",
                                "portfolio.positions",
                                "portfolio.activities"}, v.calls
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_signed_quantity_that_differs_is_a_finding():
    """A SHORT OF 5 IS -5 AT THE VENUE. The venue stating +5 is the wrong
    side, not a match on magnitude."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    now = time.time()
    try:
        await _clean(conn)
        await _group(conn, "grp:d5a-qty", "ev-d5a-qty")
        await _leg(conn, "fpi-d5a-short", slug="aec-d5a-short",
                   event="ev-d5a-qty", gid="grp:d5a-qty", role="PRIMARY",
                   intent=SHORT, qty=5, order_id="vo-d5a-short")
        right = await ON.discrepancy_report(
            conn, account_id=ACCT, venue=VENUE, now=now,
            adapter=_Venue(positions=[{"positions": {
                "aec-d5a-short": _pos(-5)}, "eof": True}]))
        assert ON.F_POSITION_QTY_DIFFERS not in _names(right), right[
            "discrepancies"]
        wrong = await ON.discrepancy_report(
            conn, account_id=ACCT, venue=VENUE, now=now,
            adapter=_Venue(positions=[{"positions": {
                "aec-d5a-short": _pos(5)}, "eof": True}]))
        d = _one(wrong, ON.F_POSITION_QTY_DIFFERS,
                 us_market_slug="aec-d5a-short")
        assert d["venue_net_position"] == 5.0
        assert d["book_net_position"] == -5.0
        assert wrong["would_be_eligible"] is False
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_absent_net_position_is_unreadable_never_flat():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        v = _Venue(positions=[{"positions": {
            "aec-d5a-unstated": {"cost": _amt(5)}}, "eof": True}])
        rep = await ON.discrepancy_report(conn, account_id=ACCT, venue=VENUE,
                                          adapter=v)
        assert rep["reads"]["positions"]["ok"] is False
        assert rep["reads"]["positions"]["refusal"] == \
            ACC.R_POSITION_FIELD_MISSING
        assert rep["authoritative"] is False
        assert rep["would_be_eligible"] is False
        nr = _one(rep, ON.F_READ_NOT_ESTABLISHED, check="positions")
        assert nr["blocking"] is True
        assert rep["verdicts"]["positions"] == ON.UNREADABLE
        # AND THE OLDER RECONCILIATION'S READER NO LONGER READS IT AS FLAT
        old = ON.read_positions(_Venue(positions=[{"positions": {
            "aec-d5a-unstated": {"cost": _amt(5)}}, "eof": True}]))
        assert old["verdict"] == ON.UNREADABLE, old
        assert "does not state its netPosition" in old["why"]
        # an explicit zero is still a measurement, and flat
        flat = ON.read_positions(_Venue(positions=[{"positions": {
            "aec-d5a-flat": {"netPosition": "0"}}, "eof": True}]))
        assert flat["verdict"] == ON.RECONCILED and flat["count"] == 0
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_unknown_activity_and_contradictory_balances_are_findings():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    now = datetime.now(timezone.utc)
    try:
        await _clean(conn)
        odd = {"type": "ACTIVITY_TYPE_SOMETHING_NEW",
               "timestamp": _iso(now - timedelta(hours=1)), "x": {}}
        rep = await ON.discrepancy_report(
            conn, account_id=ACCT, venue=VENUE, now=now.timestamp(),
            adapter=_Venue(reserved=25.0, activities=[
                {"activities": [odd], "eof": True}]))
        assert rep["authoritative"] is True
        uk = _one(rep, ON.F_UNKNOWN_ACTIVITY,
                  type="ACTIVITY_TYPE_SOMETHING_NEW")
        assert uk["blocking"] is True
        rs = _one(rep, ON.F_RESERVED_WITHOUT_ORDERS)
        assert rs["reserved_usd"] == 25.0
        # WORKING BUYS WITH NOTHING RESERVED contradict the other way
        rep = await ON.discrepancy_report(
            conn, account_id=ACCT, venue=VENUE, now=now.timestamp(),
            adapter=_Venue(reserved=0.0, orders={"orders": [
                _order("vo-d5a-x", "aec-d5a-x")]}))
        _one(rep, ON.F_ORDERS_WITHOUT_RESERVATION)
        # AND A ROW THAT DOES NOT STATE WHAT IT RESERVES, with orders open
        rep = await ON.discrepancy_report(
            conn, account_id=ACCT, venue=VENUE, now=now.timestamp(),
            adapter=_Venue(orders={"orders": [
                _order("vo-d5a-y", "aec-d5a-y")]}, balance_rows=[
                {"currency": "USD", "currentBalance": 10.0,
                 "absent_fields": ["openOrders"]}]))
        _one(rep, ON.F_RESERVED_NOT_STATED)
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · A CLEAN ACCOUNT, THE HISTORY, AND WHAT NEVER MOVES
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_clean_report_is_kept_writes_the_evidence_key_and_unpauses_nothing():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute(
            "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
            " opening_balance, opened_at, note, provenance, paused, "
            " pause_reason, accounting_status, accounting_detail) VALUES "
            " ($1,'desk-d5a','ACTIVE',0,now(),'d5a report test','{}'::jsonb,"
            "  TRUE,'awaiting an owner decision','UNCERTAIN','{}'::jsonb)",
            ACCT)
        before = dict(await conn.fetchrow(
            "SELECT status, paused, pause_reason, accounting_status, "
            " accounting_detail::text AS d, last_verified_at "
            " FROM bettor_desk_accounts WHERE account_id=$1", ACCT))
        rec = await ON.record_discrepancy_report(
            conn, account_id=ACCT, venue=VENUE, by="owner@test",
            adapter=_Venue())
        assert rec["recorded"] is True, rec
        assert rec["authoritative"] is True
        assert rec["discrepancies"] == [] and rec["blocking"] == []
        assert rec["would_be_eligible"] is True
        assert rec["pause_kept"] is True
        # THE SHA IS OF THE BODY AS KEPT
        body = {k: v for k, v in rec.items() if k not in (
            "recorded", "report_id", "report_sha", "latest_evidence_key")}
        assert rec["report_sha"] == ON._sha(body)

        # THE ACCOUNT ROW DID NOT MOVE -- not paused, not status, not
        # accounting, not last_verified_at
        after = dict(await conn.fetchrow(
            "SELECT status, paused, pause_reason, accounting_status, "
            " accounting_detail::text AS d, last_verified_at "
            " FROM bettor_desk_accounts WHERE account_id=$1", ACCT))
        assert after == before

        # THE LATEST-EVIDENCE KEY, for its existing consumers
        ev = await ON.reconciliation_evidence(conn, account_id=ACCT)
        assert ev["present"] is True and ev["usable"] is True, ev
        assert ev["passes"] is True
        assert ev["completeness"]["complete"] is True

        # THE HISTORY: a row per report, verdict fields, no bodies listed
        rec2 = await ON.record_discrepancy_report(
            conn, account_id=ACCT, venue=VENUE, by="owner@test",
            adapter=_Venue(reserved=5.0))
        assert rec2["would_be_eligible"] is False
        hist = await ON.report_history(conn, account_id=ACCT)
        assert hist["ok"] is True
        assert [r["report_id"] for r in hist["reports"]] == [
            rec2["report_id"], rec["report_id"]]
        assert [r["would_be_eligible"] for r in hist["reports"]] == [
            False, True]
        assert all("report" not in r for r in hist["reports"])
        # AND THE LATEST KEY NOW SAYS THE LATER, WORSE THING
        ev = await ON.reconciliation_evidence(conn, account_id=ACCT)
        assert ev["passes"] is False

        # APPEND-ONLY
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE bettor_account_reconciliation_reports "
                "   SET would_be_eligible=TRUE WHERE report_id=$1",
                rec2["report_id"])
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "DELETE FROM bettor_account_reconciliation_reports "
                " WHERE report_id=$1", rec["report_id"])
        # AND A REPORT CANNOT BE RECORDED ELIGIBLE WITHOUT AUTHORITY
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO bettor_account_reconciliation_reports "
                "(report_id, account_id, venue, recorded_at, recorded_by, "
                " authoritative, would_be_eligible, blocking_count, report, "
                " report_sha) VALUES ('rcr:forged',$1,'PMUS',now(),'x',"
                " FALSE,TRUE,0,'{}'::jsonb,$2)", ACCT, "0" * 64)
    finally:
        await _clean(conn)
        await conn.close()


def test_the_report_path_calls_nothing_that_places_modifies_or_cancels():
    """STRUCTURAL. The report's own code, and the readers it uses, contain no
    order-mutating call and no write to the account row; the runtime test
    above ran it against a stand-in that raises on any such call."""
    import re

    from sportsassets import bettor_account_onboarding as M
    fns = [M.discrepancy_report, M.record_discrepancy_report,
           M.report_history, M._our_book_for_report, M._compare_positions,
           M._compare_orders, M._compare_executions, M._compare_balances,
           M._registry_row, M.desk_identifier_integrity, M.read_balances,
           ACC.read_open_orders_sync, ACC.read_positions_sync,
           ACC.read_account_activity_sync]
    src = "\n".join(inspect.getsource(f) for f in fns)
    for token in (r"\.create\(", r"\.cancel\(", r"cancel_all", r"\.modify\(",
                  r"close_position\(", r"submit_\w*\(", r"cancel_order\(",
                  r"place_order", r"\.retrieve\(", r"mark_eligible\(",
                  r"resolve_existing\(", r"UPDATE\s+bettor_desk_accounts",
                  r"paused\s*=\s*FALSE", r"accounting_status\s*="):
        assert not re.search(token, src), token
    # the venue calls it does make are the four reads
    assert "client.portfolio.activities(" in src
    assert "client.portfolio.positions(" in src
    assert "client.orders.list(" in src


@pytest.mark.parametrize("ident,kind,scheme", [
    ("acct_fc2d773a2afa4851-O-000001", "O", ON.ID_LEGACY_COUNTER),
    ("acct_fc2d773a2afa4851-D-000583", "D", ON.ID_LEGACY_COUNTER),
    ("live1-O-000063", "O", ON.ID_LEGACY_COUNTER),
    ("acct_fc2d773a2afa4851-O-trade:221480822~a1b2c3-01", "O",
     ON.ID_EVIDENCE_DERIVED),
    ("acct_fc2d773a2afa4851-D-at:1790000000.000~0f0f0f-12", "D",
     ON.ID_EVIDENCE_DERIVED),
    ("acct_fc2d773a2afa4851-D-boot:deadbeef-01", "D", ON.ID_BOOT_DERIVED),
    # CANNOT BE TOLD, and said so by name
    ("acct_fc2d773a2afa4851-O-12345", "O", ON.ID_SCHEME_UNREADABLE),
    ("acct_fc2d773a2afa4851-O-trade:1-01", "O", ON.ID_SCHEME_UNREADABLE),
    ("someone-else-O-000001", "O", ON.ID_SCHEME_UNREADABLE),
    ("acct_fc2d773a2afa4851-D-000001", "O", ON.ID_SCHEME_UNREADABLE),
])
def test_the_desk_id_scheme_is_read_from_the_format_strings(ident, kind,
                                                            scheme):
    got = ON.classify_desk_id(ident, prefixes=["acct_fc2d773a2afa4851",
                                               "live1"], kind=kind)
    assert got["scheme"] == scheme, got


def test_the_classifier_matches_what_the_desk_actually_generates():
    """The two schemes, produced by `bettor_desk.Desk` itself."""
    from sportsassets import bettor_desk as BD
    d = BD.Desk.__new__(BD.Desk)
    d.id_prefix, d._n, d._evt_key = "acct_x", 0, None
    d._boot_key = "0123abcd"
    legacy = d._legacy_next_id("O")
    boot = d._next_id("D")
    d._evt_key, d._n = "trade:99~abcdef", 0
    evidence = d._next_id("O")
    kw = {"prefixes": ["acct_x"]}
    assert ON.classify_desk_id(legacy, kind="O", **kw)["scheme"] == \
        ON.ID_LEGACY_COUNTER
    assert ON.classify_desk_id(boot, kind="D", **kw)["scheme"] == \
        ON.ID_BOOT_DERIVED
    assert ON.classify_desk_id(evidence, kind="O", **kw)["scheme"] == \
        ON.ID_EVIDENCE_DERIVED


# ═════════════════════════════════════════════════════════════════════
# 3 · THE PAUSED SHADOW DESK ACCOUNT
# ═════════════════════════════════════════════════════════════════════

DESK_ACCT = ON.PAUSED_DESK_ACCOUNT
DETAIL = {"cause": "per-process counter ids collided across restarts",
          "window_from": "2026-09-23T16:06:51Z",
          "fix": "ids are now derived from the source event id",
          "what_the_fix_does_not_do": ("it prevents future collisions and "
                                       "repairs nothing already overwritten"),
          "NOT_reconstructable": "whether a specific value was overwritten",
          "performance_qualification": "SUPPRESSED"}


async def _desk_rows(conn):
    """SYNTHETIC desk rows standing in for the real account's: two legacy-
    counter decisions and orders inside the window, one evidence-derived
    decision, one id that fits neither form, and one legacy row BEFORE the
    window that must not be counted."""
    when = datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc)
    early = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
    rows = [("%s-D-000001" % DESK_ACCT, when),
            ("%s-D-000002" % DESK_ACCT, when),
            ("%s-D-trade:5~abcdef-01" % DESK_ACCT, when),
            ("%s-D-garbled" % DESK_ACCT, when),
            ("%s-D-000009" % DESK_ACCT, early)]
    for did, at in rows:
        await conn.execute(
            "INSERT INTO bettor_desk_decisions (desk_decision_id, desk_id, "
            " boot_id, decided_at, feature_cutoff_at, action, reason, "
            " policy_version, model_version, inputs_sha, account_id, "
            " created_at) VALUES ($1,'live1','b',$2,$2,'HOLD','t','p','m',"
            " 'sha',$3,$2)", did, at, DESK_ACCT)
    for oid, did, at in (("%s-O-000001" % DESK_ACCT,
                          "%s-D-000001" % DESK_ACCT, when),
                         ("%s-O-000002" % DESK_ACCT,
                          "%s-D-000002" % DESK_ACCT, when)):
        await conn.execute(
            "INSERT INTO bettor_desk_orders (order_id, desk_id, boot_id, "
            " desk_decision_id, condition_id, outcome_index, side, intent, "
            " limit_price, qty, notional_committed, state, account_id, "
            " created_at, updated_at) VALUES ($1,'live1','b',$2,'c',0,'BUY',"
            " 'ENTER',0.5,1,0.5,'RESTING',$3,$4,$4)", oid, did, DESK_ACCT, at)


async def _drop_desk(conn):
    await conn.execute("DELETE FROM bettor_desk_orders WHERE account_id=$1",
                       DESK_ACCT)
    await conn.execute(
        "DELETE FROM bettor_desk_decisions WHERE account_id=$1", DESK_ACCT)
    await _clean(conn, DESK_ACCT)


@pg
@pytest.mark.asyncio
async def test_the_paused_desk_report_names_what_a_venue_reconciliation_cannot():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop_desk(conn)
        await conn.execute(
            "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
            " opening_balance, opened_at, note, provenance, paused, "
            " pause_reason, paused_at, accounting_status, accounting_detail)"
            " VALUES ($1,'live1','ACTIVE',100000,now(),'stand-in for the "
            " paused shadow account','{}'::jsonb,TRUE,$2,now(),"
            " 'ACCOUNTING_UNCERTAIN',$3::jsonb)", DESK_ACCT,
            "ACCOUNTING_RECOVERY: identifier collision across restarts of "
            "this account. Paused pending verification of the corrected "
            "build.", json.dumps(DETAIL))
        await _desk_rows(conn)
        before = dict(await conn.fetchrow(
            "SELECT status, paused, pause_reason, accounting_status, "
            " last_verified_at FROM bettor_desk_accounts "
            " WHERE account_id=$1", DESK_ACCT))
        # A PERFECTLY CLEAN VENUE -- and still not a basis to unpause
        rec = await ON.record_discrepancy_report(
            conn, account_id=DESK_ACCT, venue=VENUE, by="owner@test",
            adapter=_Venue())
        assert rec["recorded"] is True
        assert rec["authoritative"] is True
        assert rec["venue_reconciliation_addresses_desk_identifier_"
                   "integrity"] is False
        assert ON.GAP_DESK_NOT_ADDRESSED in {g["gap"] for g in rec["gaps"]}
        nv = _one(rec, ON.F_DESK_IDS_NOT_VERIFIED)
        assert nv["blocking"] is True
        assert rec["would_be_eligible"] is False
        integ = rec["desk_identifier_integrity"]
        assert integ["window_from"] == "2026-09-23T16:06:51Z"
        assert integ["verified"] is False
        dec = integ["counts"]["bettor_desk_decisions"]
        assert dec["rows"] == 4          # the pre-window row is not counted
        assert dec["by_scheme"] == {ON.ID_LEGACY_COUNTER: 2,
                                    ON.ID_EVIDENCE_DERIVED: 1,
                                    ON.ID_BOOT_DERIVED: 0,
                                    ON.ID_SCHEME_UNREADABLE: 1}
        assert dec["unclassifiable_examples"][0]["id"] == \
            "%s-D-garbled" % DESK_ACCT
        orders = integ["counts"]["bettor_desk_orders"]
        assert orders["by_scheme"][ON.ID_LEGACY_COUNTER] == 2
        crit = integ["what_the_pause_reason_says_would_verify_the_corrected_"
                     "build"]
        assert len(crit) == 4
        assert "mark_eligible" in crit[2]["observed"]
        assert "repairs nothing already overwritten" in crit[3]["observed"]
        # AND THE ROW DID NOT MOVE
        after = dict(await conn.fetchrow(
            "SELECT status, paused, pause_reason, accounting_status, "
            " last_verified_at FROM bettor_desk_accounts "
            " WHERE account_id=$1", DESK_ACCT))
        assert after == before
        assert after["paused"] is True
        assert after["accounting_status"] == "ACCOUNTING_UNCERTAIN"
    finally:
        await _drop_desk(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · THE ROUTES
# ═════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-for-the-d5a-report-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


def test_the_report_routes_need_the_admin_token(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    c = starlette.TestClient(A.app, raise_server_exceptions=False)
    assert c.post("/api/admin/funded-account-discrepancy-report"
                  "?account_id=a&venue=PMUS").status_code == 401
    assert c.get("/api/admin/funded-account-discrepancy-reports"
                 ).status_code == 401
    assert c.post("/api/admin/funded-account-discrepancy-report"
                  "?account_id=a&venue=PMUS",
                  headers={"X-Admin-Token": "wrong"}).status_code == 401
    methods = {r.path: r.methods for r in A.app.routes
               if getattr(r, "path", "").startswith(
                   "/api/admin/funded-account-discrepancy")}
    assert "POST" in methods["/api/admin/funded-account-discrepancy-report"]
    assert "GET" in methods["/api/admin/funded-account-discrepancy-reports"]


# ── THE ELIGIBILITY ROUTE, AGAINST THE REAL EVIDENCE READER ──────────

class _StubConn:
    """Answers the route's reads: the registry rows, and the one
    `ingestion_state` value (a reconciliation record) for every fetchval."""

    def __init__(self, registry, state):
        self._registry, self._state = registry, state

    async def fetch(self, sql, *args):
        return [dict(r) for r in self._registry]

    async def fetchrow(self, sql, *args):
        return None

    async def fetchval(self, sql, *args):
        return self._state


def _record(*, age_s=60.0, eligible=True, complete=True, account="acct-e"):
    return {"version": ON.REPORT_VERSION, "recorded_at": time.time() - age_s,
            "account_id": account, "venue": VENUE, "eligible": eligible,
            "completeness": {"complete": complete}, "verdicts": {},
            "discrepancies": []}


@pytest.mark.parametrize("row,rec,eligible,reason", [
    # THE CASE THE BUG MADE IMPOSSIBLE: fresh, complete, passing evidence on
    # an active, unpaused row -- eligible.
    ({"paused": False}, _record(), True, None),
    ({"paused": False}, _record(age_s=ON.EVIDENCE_MAX_AGE_S + 60), False,
     ON.R_EVIDENCE_STALE),
    ({"paused": False}, _record(eligible=False), False, ON.R_NOT_RECONCILED),
    ({"paused": False}, _record(complete=False), False, ON.R_NOT_RECONCILED),
    ({"paused": False}, _record(account="acct-other"), False,
     ON.R_EVIDENCE_OTHER_ACCOUNT),
    # PASSING EVIDENCE DOES NOT MAKE A PAUSED ROW ELIGIBLE -- and the route
    # only reports either way
    ({"paused": True}, _record(), False, "PAUSED"),
])
def test_the_eligibility_route_reads_the_field_the_evidence_returns(
        monkeypatch, row, rec, eligible, reason):
    from sportsassets import bettor_account_exposure as AE
    from sportsassets.api import app as A

    async def _expo(conn, *, account_id=None, venue_positions=None, now=None):
        return {"total": None, "refusal": "TOTAL_UNREADABLE"}

    monkeypatch.setattr(AE, "account_exposure", _expo)
    registry = [dict({"account_id": "acct-e", "desk_id": "d",
                      "status": "ACTIVE", "accounting_status": "RECONCILED"},
                     **row)]
    conn = _StubConn(registry, rec)

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(_s):
                    return conn

                async def __aexit__(_s, *a):
                    return False
            return _Ctx()

    async def _pool():
        return _Pool()

    monkeypatch.setattr("sportsassets.db.get_pool", _pool)

    class _Resp:
        headers: dict = {}

    got = asyncio.run(A.admin_funded_account_eligibility(
        _Resp(), account_id="acct-e"))
    assert got["eligible"] is eligible, got
    if reason is None:
        assert got["why_not"] is None
        assert got["reconciliation"]["passes"] is True
    else:
        assert any(reason in r for r in got["why_not"]), got["why_not"]
    assert got["this_route_is_read_only"]["writes"] == 0
    # the route reads the evidence's own fields, not a key it never returns
    src = inspect.getsource(A.admin_funded_account_eligibility)
    assert 'evidence.get("ok")' not in src
    assert 'evidence.get("passes") is True' in src
