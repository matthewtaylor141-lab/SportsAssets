"""THE PAPER ACCOUNT AND ITS ONE LEDGER (migration 171, bettor_paper_ledger).

Owner rules, each proven here on a scratch database with SYNTHETIC books:
  * exactly $500,000 ONCE: re-running the initializer and the migration's own
    insert never re-funds; no reset, no replenishment, no DEPOSIT kind;
  * the ledger is append-only with unique idempotency keys: a duplicate
    submit, fill or settlement never debits or credits twice;
  * ORDER_SUBMITTED reserves limit x qty + max fees (available falls, cash
    does not); FILL debits the filled cost + fees only and releases the
    filled share; CANCEL / EXPIRE releases the remainder with no cash credit;
    SALE credits proceeds - fees; SETTLEMENT credits exactly once, a loser
    credits 0; a correction is a separate entry that keeps history; a price
    movement changes marks and equity, never cash;
  * concurrent submissions cannot spend the same available cash;
  * the owner's worked example, fees 0.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
def SL(acct, name):
    """Market slugs are unique per test account: observed books and the
    consumed-liquidity ledger are shared by every paper order, by design."""
    return "%s:%s" % (acct["account_id"], name)


MIGRATION = (pathlib.Path(__file__).resolve().parents[1] / "migrations"
             / "171_paper_account_and_ledger.sql")


async def _buy_and_fill(conn, acct, *, key, qty, limit, slug, at,
                        fee_fn=H.zero_fee, offers=None, group_id=None,
                        holding_side="LONG", role="ENTRY", bids=None):
    o = H.order(acct, key=key, qty=qty, limit=limit, slug=slug, at=at,
                group_id=group_id, holding_side=holding_side, role=role)
    got = await L.submit_order(conn, o, fee_fn=fee_fn, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3.0, offers=offers or [(limit, qty)],
                    bids=bids or [])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                   now=at + 4.0, fee_fn=fee_fn)
    return got, sim


@pg
async def test_the_live_account_is_funded_exactly_once_and_never_again():
    conn = await H.connect()
    try:
        first = await L.ensure_account(conn)
        again = await L.ensure_account(conn)
        # THE MIGRATION'S OWN INSERT, RE-RUN VERBATIM (a redeploy that replays
        # the file): still one funding entry.
        await conn.execute(MIGRATION.read_text())
        for _ in range(3):
            await L.ensure_account(conn)
        n = await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 "
            "   AND kind='INITIAL_FUNDING'", L.ACCOUNT_ID)
        amt = await conn.fetchval(
            "SELECT cash_delta_usd FROM paper_ledger WHERE account_id=$1 "
            "   AND kind='INITIAL_FUNDING'", L.ACCOUNT_ID)
        assert n == 1 and float(amt) == 500000.0
        assert first["ok"] and again["ok"] and again["funded_now"] is False
        b = await L.balances(conn, L.ACCOUNT_ID, now=H.T0)
        assert b["starting_cash_usd"] == 500000.0
        assert b["data_label"] == "LIVE MARKET DATA / SIMULATED EXECUTION"
        assert b["real_money_submission"] == "DISABLED"
        # NO SECOND FUNDING, EVEN WRITTEN DIRECTLY, AND NO DEPOSIT KIND.
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO paper_ledger (seq, account_id, idempotency_key, "
                " kind, cash_delta_usd, reserved_delta_usd, cash_after_usd, "
                " reserved_after_usd, event_source) VALUES (0,$1,'again',"
                " 'INITIAL_FUNDING',500000,0,0,0,'PAPER_LEDGER')",
                L.ACCOUNT_ID)
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO paper_ledger (seq, account_id, idempotency_key, "
                " kind, cash_delta_usd, reserved_delta_usd, cash_after_usd, "
                " reserved_after_usd, event_source) VALUES (0,$1,'topup',"
                " 'DEPOSIT',100000,0,0,0,'PAPER_LEDGER')", L.ACCOUNT_ID)
        # NO RESET: the ledger and the account cannot be rewritten.
        with pytest.raises(Exception):
            await conn.execute("UPDATE paper_ledger SET cash_delta_usd = 1 "
                               "WHERE account_id=$1", L.ACCOUNT_ID)
        with pytest.raises(Exception):
            await conn.execute("DELETE FROM paper_ledger WHERE account_id=$1",
                               L.ACCOUNT_ID)
        with pytest.raises(Exception):
            await conn.execute("UPDATE paper_accounts SET starting_cash_usd="
                               "600000 WHERE account_id=$1", L.ACCOUNT_ID)
    finally:
        await conn.close()


@pg
async def test_the_owners_worked_example_fees_zero():
    """Start $500,000. Buy $1,000 of Yankees ML at $0.50 -> $499,000. Settle
    as winner (2,000 x $1) -> $501,000; a loser leaves $499,000. With an $800
    hedge added before settlement -> $498,200, then each contract settles by
    its own rule."""
    conn = await H.connect()
    try:
        # WINNER
        a = await H.new_account(conn, "we_win")
        _, sim = await _buy_and_fill(conn, a, key="yankees-win", qty=2000,
                                     limit=0.50, slug=SL(a, "yankees-ml"),
                                     at=H.T0, group_id="paper_g_yank")
        assert sim["state"] == "FILLED", sim
        b = await L.balances(conn, a["account_id"], now=H.T0 + 5)
        assert b["cash_usd"] == 499000.0 and b["reserved_usd"] == 0.0
        s = await L.settle(conn, account_id=a["account_id"],
                           group_id="paper_g_yank", slug=SL(a, "yankees-ml"),
                           holding_side="LONG",
                           settlement_event_key="venue-final",
                           outcome="WON", evidence={"test": "synthetic"},
                           evidence_source="TEST_FIXTURE", at=H.T0 + 100)
        assert s["ok"] and s["payout_usd"] == 2000.0
        b = await L.balances(conn, a["account_id"], now=H.T0 + 101)
        assert b["cash_usd"] == 501000.0
        assert b["realized_pnl_usd"] == 1000.0 and not b["open_positions"]
        # LOSER
        a2 = await H.new_account(conn, "we_lose")
        await _buy_and_fill(conn, a2, key="yankees-lose", qty=2000,
                            limit=0.50, slug=SL(a2, "yankees-ml-2"), at=H.T0,
                            group_id="paper_g_yank2")
        await L.settle(conn, account_id=a2["account_id"],
                       group_id="paper_g_yank2", slug=SL(a2, "yankees-ml-2"),
                       holding_side="LONG",
                       settlement_event_key="venue-final", outcome="LOST",
                       evidence={"test": "synthetic"},
                       evidence_source="TEST_FIXTURE", at=H.T0 + 100)
        b2 = await L.balances(conn, a2["account_id"], now=H.T0 + 101)
        assert b2["cash_usd"] == 499000.0
        assert b2["realized_pnl_usd"] == -1000.0
        # WITH AN $800 HEDGE BEFORE SETTLEMENT
        a3 = await H.new_account(conn, "hedged")
        await _buy_and_fill(conn, a3, key="yankees-h", qty=2000, limit=0.50,
                            slug=SL(a3, "yankees-ml-3"), at=H.T0,
                            group_id="paper_g_h")
        # the hedge: 2,000 of the other side's contract at $0.40 = $800
        await _buy_and_fill(conn, a3, key="redsox-h", qty=2000, limit=0.40,
                            slug=SL(a3, "redsox-ml-3"), at=H.T0 + 10,
                            group_id="paper_g_h", role="HEDGE")
        b3 = await L.balances(conn, a3["account_id"], now=H.T0 + 20)
        assert b3["cash_usd"] == 498200.0
        for slug, outcome in ((SL(a3, "yankees-ml-3"), "WON"),
                              (SL(a3, "redsox-ml-3"), "LOST")):
            got = await L.settle(conn, account_id=a3["account_id"],
                                 group_id="paper_g_h", slug=slug,
                                 holding_side="LONG",
                                 settlement_event_key="venue-final",
                                 outcome=outcome,
                                 evidence={"test": "synthetic"},
                                 evidence_source="TEST_FIXTURE",
                                 at=H.T0 + 100)
            assert got["ok"], got
        b3 = await L.balances(conn, a3["account_id"], now=H.T0 + 101)
        assert b3["cash_usd"] == 500200.0
    finally:
        await conn.close()


@pg
async def test_a_reservation_is_not_a_purchase_and_a_partial_fill_debits_only_the_filled_share():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "resv")
        o = H.order(a, key="resv-1", qty=1000, limit=0.60, slug=SL(a, "m-resv"),
                    at=H.T0)
        got = await L.submit_order(conn, o, fee_fn=H.flat_fee(0.01),
                                   now=H.T0)
        assert got["ok"], got
        b = await L.balances(conn, a["account_id"], now=H.T0)
        # limit x qty + max fees = 600 + 10
        assert b["cash_usd"] == 500000.0
        assert b["reserved_usd"] == 610.0
        assert b["available_usd"] == 499390.0
        assert await H.ledger_kinds(conn, a["account_id"]) == [
            "INITIAL_FUNDING", "ORDER_SUBMITTED"]
        # a 400-contract partial at 0.55 (IOC: the remainder is released)
        await H.observe(conn, SL(a, "m-resv"), H.T0 + 3, offers=[(0.55, 400),
                                                          (0.70, 5000)])
        sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                       now=H.T0 + 4,
                                       fee_fn=H.flat_fee(0.01))
        assert sim["filled_qty"] == 400.0 and sim["state"] == "CANCELED"
        b = await L.balances(conn, a["account_id"], now=H.T0 + 5)
        assert b["cash_usd"] == 500000.0 - 220.0 - 4.0
        assert b["reserved_usd"] == 0.0
        rows = await conn.fetch(
            "SELECT kind, cash_delta_usd, reserved_delta_usd FROM paper_ledger"
            " WHERE account_id=$1 ORDER BY seq", a["account_id"])
        kinds = [(r["kind"], float(r["cash_delta_usd"]),
                  float(r["reserved_delta_usd"])) for r in rows]
        assert kinds[2] == ("FILL", -224.0, -244.0)     # 610 x 400/1000
        assert kinds[3] == ("RESERVATION_RELEASED", 0.0, -366.0)
        # SALE: credit proceeds - fees, never an acquisition
        so = H.order(a, key="resv-sell", direction="SELL", qty=400,
                     limit=0.50, slug=SL(a, "m-resv"), role="EXIT",
                     group_id=o["group_id"], at=H.T0 + 10)
        sg = await L.submit_order(conn, so, now=H.T0 + 10)
        assert sg["ok"], sg
        # a second sale of the same inventory is refused (committed)
        so2 = dict(so, idempotency_key="resv-sell-2")
        sg2 = await L.submit_order(conn, so2, now=H.T0 + 10)
        assert not sg2["ok"] and sg2["refusal"] == L.R_NOT_HELD
        await H.observe(conn, SL(a, "m-resv"), H.T0 + 13, bids=[(0.52, 1000)])
        sim = await SIM.simulate_order(conn, sg["order"]["order_id"],
                                       now=H.T0 + 14,
                                       fee_fn=H.flat_fee(0.01))
        assert sim["state"] == "FILLED", sim
        b = await L.balances(conn, a["account_id"], now=H.T0 + 15)
        assert b["cash_usd"] == 500000.0 - 224.0 + 208.0 - 4.0
        assert not b["open_positions"]
    finally:
        await conn.close()


@pg
async def test_duplicates_retries_and_restarts_never_debit_or_credit_twice():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "dup")
        o = H.order(a, key="dup-1", qty=100, limit=0.50, slug=SL(a, "m-dup"),
                    at=H.T0)
        g1 = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        g2 = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0 + 1)
        assert g1["ok"] and g2["duplicate"]
        await H.observe(conn, SL(a, "m-dup"), H.T0 + 3, offers=[(0.50, 100)])
        oid = g1["order"]["order_id"]
        s1 = await SIM.simulate_order(conn, oid, now=H.T0 + 4,
                                      fee_fn=H.zero_fee)
        s2 = await SIM.simulate_order(conn, oid, now=H.T0 + 5,
                                      fee_fn=H.zero_fee)
        assert s1["state"] == "FILLED" and s2.get("skipped") == "NOT_OPEN"
        # A redelivered fill (same key) is a no-op.
        async with conn.transaction():
            await L._lock(conn, a["account_id"])
            again = await L.apply_fill_locked(
                conn, order={"order_id": oid}, qty=100, price=0.5,
                wire_price=0.5, fee=0, filled_at=H.T0 + 4,
                basis=SIM.BASIS_WALK,
                key="%s:obs%d:%s" % (oid, (await conn.fetchval(
                    "SELECT max(book_obs_id) FROM paper_fills WHERE "
                    "order_id=$1", oid)), SIM._wk(0.5)))
        assert again["duplicate"] is True
        for _ in range(3):
            await L.settle(conn, account_id=a["account_id"],
                           group_id=o["group_id"], slug=SL(a, "m-dup"),
                           holding_side="LONG", settlement_event_key="final",
                           outcome="WON", evidence={}, evidence_source="T",
                           at=H.T0 + 50)
        assert await H.ledger_kinds(conn, a["account_id"]) == [
            "INITIAL_FUNDING", "ORDER_SUBMITTED", "FILL", "SETTLEMENT"]
        b = await L.balances(conn, a["account_id"], now=H.T0 + 60)
        assert b["cash_usd"] == 500050.0 and b["ledger_consistent"]
    finally:
        await conn.close()


@pg
async def test_concurrent_submissions_cannot_spend_the_same_available_cash():
    """Two connections race orders each needing more than half the book's
    available cash. The account lock serialises them: exactly one reserves."""
    c0 = await H.connect()
    try:
        a = await H.new_account(c0, "race")
        # 300,000 available after the 20% reserve is irrelevant here: role
        # HEDGE may use the reserve; each order reserves 300,000.
        conns = [await H.connect() for _ in range(4)]
        orders = [H.order(a, key="race-%d" % i, qty=600000, limit=0.50,
                          slug=SL(a, "m-race-%d" % i), role="HEDGE", at=H.T0)
                  for i in range(4)]
        got = await asyncio.gather(*[
            L.submit_order(c, o, fee_fn=H.zero_fee, now=H.T0)
            for c, o in zip(conns, orders)])
        ok = [g for g in got if g["ok"]]
        refused = [g for g in got if not g["ok"]]
        assert len(ok) == 1, got
        assert all(g["refusal"] == L.R_INSUFFICIENT for g in refused)
        b = await L.balances(c0, a["account_id"], now=H.T0)
        assert b["reserved_usd"] == 300000.0
        assert b["available_usd"] == 200000.0
        for c in conns:
            await c.close()
    finally:
        await c0.close()


@pg
async def test_caps_and_the_hedge_reserve_bind_entries():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "caps")
        caps = a["config"]["risk"]
        big = H.order(a, key="cap-1", qty=12000, limit=0.50, slug=SL(a, "m-cap"),
                      at=H.T0)
        got = await L.submit_order(conn, big, caps=caps, fee_fn=H.zero_fee,
                                   now=H.T0)
        assert not got["ok"] and got["refusal"] == L.R_PER_ORDER
        n_ok = 0
        for i in range(4):
            o = H.order(a, key="cap-f%d" % i, qty=9000, limit=0.50,
                        slug=SL(a, "m-fx-%d" % i), fixture="fx-cap", at=H.T0)
            g = await L.submit_order(conn, o, caps=caps, fee_fn=H.zero_fee,
                                     now=H.T0)
            n_ok += 1 if g["ok"] else 0
            if not g["ok"]:
                assert g["refusal"] == L.R_PER_FIXTURE
        assert n_ok == 3        # 3 x 4,500 = 13,500 <= 15,000 < 18,000
    finally:
        await conn.close()


@pg
async def test_a_settlement_correction_is_a_separate_entry_and_history_stays():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "corr")
        o, _ = await _buy_and_fill(conn, a, key="corr-1", qty=1000,
                                   limit=0.40, slug=SL(a, "m-corr"), at=H.T0,
                                   group_id="paper_g_corr")
        await L.settle(conn, account_id=a["account_id"],
                       group_id="paper_g_corr", slug=SL(a, "m-corr"),
                       holding_side="LONG", settlement_event_key="final",
                       outcome="WON", evidence={"v": 1},
                       evidence_source="T", at=H.T0 + 50)
        c = await L.correct_settlement(
            conn, account_id=a["account_id"], group_id="paper_g_corr",
            slug=SL(a, "m-corr"), holding_side="LONG", settlement_event_key="final",
            outcome="LOST", evidence={"v": 2, "why": "venue corrected"},
            evidence_source="T", at=H.T0 + 60)
        assert c["ok"] and c["payout_difference_usd"] == -1000.0
        rows = await conn.fetch(
            "SELECT kind, cash_delta_usd, corrects_seq FROM paper_ledger "
            " WHERE account_id=$1 ORDER BY seq", a["account_id"])
        assert [r["kind"] for r in rows][-2:] == ["SETTLEMENT", "CORRECTION"]
        assert rows[-1]["corrects_seq"] is not None
        hist = await conn.fetch(
            "SELECT version, outcome FROM paper_settlements WHERE "
            " account_id=$1 ORDER BY version", a["account_id"])
        assert [(r["version"], r["outcome"]) for r in hist] == [
            (1, "WON"), (2, "LOST")]
        b = await L.balances(conn, a["account_id"], now=H.T0 + 70)
        assert b["cash_usd"] == 499600.0
    finally:
        await conn.close()


@pg
async def test_a_price_movement_changes_marks_and_equity_never_cash_and_a_missing_mark_is_not_zero():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "marks")
        await _buy_and_fill(conn, a, key="mk-1", qty=1000, limit=0.50,
                            slug=SL(a, "m-mark"), at=H.T0, bids=[(0.48, 500)])
        b1 = await L.balances(conn, a["account_id"], now=H.T0 + 10)
        await H.observe(conn, SL(a, "m-mark"), H.T0 + 20, bids=[(0.60, 500)],
                        offers=[(0.62, 500)])
        b2 = await L.balances(conn, a["account_id"], now=H.T0 + 21)
        assert b1["cash_usd"] == b2["cash_usd"] == 499500.0
        assert b1["total_equity_usd"] == 499500.0 + 480.0
        assert b2["total_equity_usd"] == 499500.0 + 600.0
        assert b2["unrealized_pnl_usd"] == 100.0
        pos = b2["open_positions"][0]
        assert pos["mark"]["source"].startswith("paper_book_observations:")
        assert pos["mark"]["status"] == "OK"
        # STALE: flagged, still shown
        b3 = await L.balances(conn, a["account_id"],
                              now=H.T0 + 21 + L.MARK_STALE_AFTER_S + 5)
        assert b3["stale_marks"] == [pos["position_key"]]
        # UNAVAILABLE: a market whose bids vanish has no exit mark
        await H.observe(conn, SL(a, "m-mark"), H.T0 + 30, bids=[],
                        offers=[(0.62, 500)])
        b4 = await L.balances(conn, a["account_id"], now=H.T0 + 31)
        assert b4["total_equity_usd"] is None
        assert b4["marks_complete"] is False
        assert b4["unmarked_positions"] == [pos["position_key"]]
        assert b4["equity_excluding_unmarked_usd"] == 499500.0
        assert b4["cash_usd"] == 499500.0
    finally:
        await conn.close()
