"""THE MANAGEMENT EPOCH (bettor_paper_epoch): the PAPER book re-based to
$500,000 at 2026-10-05 00:00 America/New_York over the unchanged ledger.

Pure tests drive `management_book` with ledger-timed events; the DB tests
(RN1X_TEST_DSN) write real ledger entries through bettor_paper_ledger and
read them back through `read`, Audrey's checks and the equity/live payload.
"""
from __future__ import annotations

import os
import pathlib
import uuid

import pytest

from sportsassets import bettor_paper_epoch as EP
from sportsassets.agents import paper_audrey as AU

E = EP.EPOCH_START
H = 3600.0
PK1, PK2, PK3 = "pk:carried", "pk:today", "pk:old"


def book(*, fills=(), sets=(), sq=None, em=None, nm=None, cash_e=None,
         res_e=0, res_now=0, points=None):
    """A ledger consistent with the events: INITIAL_FUNDING $500k pre-epoch,
    then every fill / settlement entry's cash on its side of the epoch."""
    fills, sets = list(fills), list(sets)

    def cash(x):
        g, fe = float(x["gross_usd"]), float(x["fee_usd"])
        return -(g + fe) if x["direction"] == "BUY" else g - fe
    pre = 500000.0 + sum(cash(x) for x in fills if x["at"] < E) + sum(
        float(x["cash_usd"]) for x in sets if x["at"] < E)
    post = sum(cash(x) for x in fills if x["at"] >= E) + sum(
        float(x["cash_usd"]) for x in sets if x["at"] >= E)
    return EP.management_book(
        epoch_at=E, opening_equity=EP.OPENING_EQUITY_USD, fills=fills,
        settlement_entries=sets, settled_qty=sq or {}, epoch_marks=em or {},
        now_marks=nm or {},
        ledger_epoch={"cash_usd": pre if cash_e is None else cash_e,
                      "reserved_usd": res_e},
        ledger_now={"cash_usd": pre + post, "reserved_usd": res_now},
        hwm_points=points)


def fill(pk, d, qty, price, fee, at):
    return {"position_key": pk, "direction": d, "qty": qty,
            "gross_usd": qty * price, "fee_usd": fee, "at": at}


def settle(pk, payout, at, kind="SETTLEMENT"):
    return {"position_key": pk, "kind": kind, "cash_usd": payout, "at": at}


def reconciles(b):
    assert b["identity"]["holds"], b["identity"]
    assert b["opening"]["identity_holds"], b["opening"]
    assert b["ledger_reconciliation"]["reconciles"], b["ledger_reconciliation"]


# ── 1. OPENING EQUITY ────────────────────────────────────────────────────

def test_opening_equity_is_exactly_500k_with_no_carried_position():
    b = book(fills=[fill(PK3, "BUY", 100, 0.5, 1.0, E - 5 * H)],
             sets=[settle(PK3, 0.0, E - 2 * H)], sq={PK3: 100}, res_e=250.0,
             res_now=250.0)
    assert b["opening_equity_usd"] == 500000.00
    assert b["carried_positions"] == 0
    op = b["opening"]
    assert op["carried_position_mark_value_usd"] == 0.0
    assert op["available_cash_usd"] + op["reserved_usd"] == 500000.00
    assert b["equity_usd"] == 500000.00
    assert b["total_pnl_usd"] == 0.0 and b["return_pct"] == 0.0
    reconciles(b)


def test_opening_equity_is_exactly_500k_with_carried_positions():
    b = book(fills=[fill(PK1, "BUY", 1000, 0.40, 8.0, E - 6 * H)],
             em={PK1: {"price": 0.55, "observed_at": E - 60}},
             nm={PK1: {"price": 0.55}}, res_e=1234.5, res_now=1234.5)
    op = b["opening"]
    assert b["carried_positions"] == 1
    assert op["carried_position_mark_value_usd"] == 550.00
    assert op["reserved_usd"] == 1234.5
    assert op["available_cash_usd"] == round(500000 - 550 - 1234.5, 2)
    assert round(op["available_cash_usd"] + op["reserved_usd"]
                 + op["carried_position_mark_value_usd"], 2) == 500000.00
    assert b["equity_usd"] == 500000.00 and b["total_pnl_usd"] == 0.0
    reconciles(b)


# ── 2. WHAT COUNTS ───────────────────────────────────────────────────────

def test_a_pre_midnight_realized_loss_is_excluded():
    b = book(fills=[fill(PK3, "BUY", 2000, 0.6, 12.0, E - 9 * H)],
             sets=[settle(PK3, 0.0, E - 1 * H)], sq={PK3: 2000})
    assert b["realized_pnl_usd"] == 0.0
    assert b["equity_usd"] == 500000.00
    assert b["historical_positions"] == 1
    reconciles(b)


def test_a_post_epoch_correction_of_a_pre_epoch_settlement_stays_history():
    b = book(fills=[fill(PK3, "BUY", 100, 0.5, 0.0, E - 9 * H)],
             sets=[settle(PK3, 0.0, E - 1 * H),
                   settle(PK3, 100.0, E + 1 * H, kind="CORRECTION")],
             sq={PK3: 100})
    assert b["realized_pnl_usd"] == 0.0 and b["equity_usd"] == 500000.00
    assert b["ledger_reconciliation"]["post_epoch_cash_held_outside_usd"] \
        == 100.0
    reconciles(b)


def test_post_midnight_mark_movement_is_included():
    b = book(fills=[fill(PK1, "BUY", 1000, 0.40, 8.0, E - 6 * H)],
             em={PK1: {"price": 0.50}}, nm={PK1: {"price": 0.62}})
    assert b["unrealized_pnl_usd"] == 120.00          # (0.62 - 0.50) x 1000
    assert b["realized_pnl_usd"] == 0.0
    assert b["equity_usd"] == 500120.00
    assert b["rows"][0]["historical_cost_at_epoch_usd"] == 408.00
    reconciles(b)


def test_a_carried_position_settling_today_realizes_only_the_post_epoch_delta():
    b = book(fills=[fill(PK1, "BUY", 1000, 0.40, 8.0, E - 6 * H)],
             sets=[settle(PK1, 1000.0, E + 3 * H)], sq={PK1: 1000},
             em={PK1: {"price": 0.70}})
    # WON: $1,000 payout against the $700 epoch basis, not the $408 cost
    assert b["realized_pnl_usd"] == 300.00
    assert b["settled_after_epoch"] == 1
    assert b["equity_usd"] == 500300.00
    reconciles(b)


def test_a_trade_opened_today_uses_its_actual_entry_basis_and_fees():
    b = book(fills=[fill(PK2, "BUY", 500, 0.30, 3.0, E + 2 * H),
                    fill(PK2, "SELL", 200, 0.45, 1.0, E + 4 * H)],
             nm={PK2: {"price": 0.40}})
    avg = (150 + 3.0) / 500
    assert b["opened_after_epoch"] == 1
    assert b["realized_pnl_usd"] == round((90 - 1.0) - avg * 200, 2)
    assert b["unrealized_pnl_usd"] == round(300 * 0.40 - avg * 300, 2)
    assert b["rows"][0]["management_avg_basis_per_contract"] == round(avg, 6)
    reconciles(b)


def test_pre_epoch_fees_are_excluded_and_post_epoch_fees_included():
    b = book(fills=[fill(PK1, "BUY", 1000, 0.40, 50.0, E - 6 * H),
                    fill(PK1, "SELL", 1000, 0.50, 7.0, E + 1 * H)],
             em={PK1: {"price": 0.50}})
    # the $50 pre-epoch fee is history; the $7 post-epoch fee costs P&L
    assert b["realized_pnl_usd"] == -7.00
    assert b["fees_after_epoch_usd"] == 7.00
    assert b["equity_usd"] == 499993.00
    reconciles(b)


def test_the_equity_identity_always_reconciles():
    fills = [fill(PK1, "BUY", 1000, 0.40, 8.0, E - 6 * H),
             fill(PK1, "BUY", 300, 0.52, 2.0, E + 1 * H),
             fill(PK1, "SELL", 400, 0.58, 2.5, E + 2 * H),
             fill(PK2, "BUY", 800, 0.25, 4.0, E + 3 * H),
             fill(PK3, "BUY", 100, 0.5, 1.0, E - 8 * H)]
    sets = [settle(PK3, 100.0, E - 3 * H), settle(PK2, 0.0, E + 5 * H)]
    for now_px in (None, 0.01, 0.33, 0.99):
        b = book(fills=fills, sets=sets, sq={PK3: 100, PK2: 800},
                 em={PK1: {"price": 0.47}}, nm={PK1: {"price": now_px}},
                 res_e=100.0, res_now=40.0)
        reconciles(b)
        assert round(b["opening_equity_usd"] + b["total_pnl_usd"], 2) == \
            b["equity_usd"]


def test_an_unverified_epoch_mark_is_never_invented_and_held_outside():
    b = book(fills=[fill(PK1, "BUY", 1000, 0.40, 8.0, E - 6 * H),
                    fill(PK1, "SELL", 500, 0.60, 1.0, E + 1 * H)],
             em={PK1: {"why": EP.R_NO_EPOCH_BOOK}}, nm={PK1: {"price": 0.6}})
    assert b["carried_unverified"] == 1 and b["carried_positions"] == 0
    u = b["unverified_positions"][0]
    assert u["state"] == EP.R_UNVERIFIED and u["why"] == EP.R_NO_EPOCH_BOOK
    assert u["open_qty_now"] == 500.0 and u["current_marked_value_usd"] == 300.0
    # nothing about it enters the management figures
    assert b["realized_pnl_usd"] == 0.0 and b["equity_usd"] == 500000.00
    assert b["ledger_reconciliation"]["post_epoch_cash_held_outside_usd"] \
        == 299.0
    assert "only" in b["high_water_mark_rule"]
    reconciles(b)


def test_drawdown_is_measured_from_the_management_high_water_mark():
    fills = [fill(PK2, "BUY", 1000, 0.40, 0.0, E + 1 * H)]
    pre = 500000.0
    b = book(fills=fills, nm={PK2: {"price": 0.35}},
             points=[(E + 2 * H, pre - 400 + 1000 * 0.48)])
    assert b["high_water_mark_usd"] == 500080.00
    assert b["equity_usd"] == 499950.00
    assert b["drawdown_usd"] == 130.00


# ── 3. AUDREY AND THE COMMAND CENTER ─────────────────────────────────────

def test_audrey_reconciliation_exactly_matches_the_management_view():
    fills = [fill(PK1, "BUY", 1000, 0.40, 8.0, E - 6 * H),
             fill(PK1, "SELL", 400, 0.58, 2.5, E + 2 * H),
             fill(PK2, "BUY", 800, 0.25, 4.0, E + 3 * H)]
    b = book(fills=fills, em={PK1: {"price": 0.47}},
             nm={PK1: {"price": 0.5}, PK2: {"price": 0.3}}, res_e=10.0)
    led_e = 500000.0 - 408.0
    led_now = led_e + (232.0 - 2.5) - 204.0
    checks = AU.management_checks(b, ledger_cash_now=led_now,
                                  ledger_cash_at_epoch=led_e)
    assert [c["check"] for c in checks] == [
        "MANAGEMENT_OPENING_EQUALS_OPENING_EQUITY",
        "MANAGEMENT_CASH_EQUALS_THE_REBASED_LEDGER",
        "MANAGEMENT_EQUITY_EQUALS_OPENING_PLUS_PNL"]
    assert all(c["passed"] for c in checks), checks
    # a ledger that disagrees by a cent is caught
    bad = AU.management_checks(b, ledger_cash_now=led_now + 0.02,
                               ledger_cash_at_epoch=led_e)
    assert not bad[1]["passed"] and not bad[2]["passed"]


def test_the_management_view_never_feeds_a_risk_control():
    b = book()
    assert b["feeds_risk_controls"] is False
    src = (pathlib.Path(EP.__file__).read_text())
    for forbidden in ("submit_order", "_check_caps", "INSERT", "UPDATE ",
                      "DELETE"):
        assert forbidden not in src, forbidden


def test_the_epoch_is_midnight_new_york_on_october_5_2026():
    import datetime as dt
    assert dt.datetime.fromtimestamp(E, dt.timezone.utc).isoformat() == \
        "2026-10-05T04:00:00+00:00"
    assert str(EP.OPENING_EQUITY_USD) == "500000.00"
    assert EP.LABEL == ("MANAGEMENT START: OCT 5, 2026 · OPENING EQUITY "
                        "$500,000")


FRONT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" \
    / "command"


@pytest.mark.skipif(not (FRONT / "hq.js").exists(),
                    reason="the 3D Command frontend is not in this tree")
def test_command_defaults_to_the_management_epoch():
    js = (FRONT / "hq.js").read_text()
    assert "p.management" in js or "paper.management" in js
    assert "MANAGEMENT START" in js or "mg.label" in js
    assert "PRE-MANAGEMENT HISTORY" in js


# ── 4. AGAINST THE REAL LEDGER (RN1X_TEST_DSN) ──────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
def test_the_read_against_a_real_ledger_and_history_stays_queryable():
    import asyncio

    import asyncpg

    from sportsassets import bettor_paper_ledger as L
    from sportsassets.api import command_equity as CE

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            if await conn.fetchval(
                    "SELECT to_regclass('paper_ledger') IS NULL"):
                pytest.skip("migration 171 is not applied")
            acct = "paper_acct_epoch_%s" % uuid.uuid4().hex[:8]
            await L.ensure_account(conn, account_id=acct,
                                   account_key="EPOCH_TEST_" + acct)
            n_before = await conn.fetchval(
                "SELECT count(*) FROM paper_ledger WHERE account_id=$1", acct)
            bal = await L.balances(conn, acct, now=E + 5 * H)
            b = await EP.read(conn, acct, bal=bal, now=E + 5 * H)
            assert b["status"] == "OK", b.get("why")
            assert b["opening_equity_usd"] == 500000.00
            assert b["equity_usd"] == 500000.00
            assert b["pre_management_history"]["ledger_cash_usd"] == 500000.0
            # the read wrote nothing: history is the unchanged ledger
            assert await conn.fetchval(
                "SELECT count(*) FROM paper_ledger WHERE account_id=$1",
                acct) == n_before
            # before the epoch the view says so instead of a figure
            nb = await EP.read(conn, acct, bal=bal, now=E - 1)
            assert nb["status"] == "NOT_STARTED"
            # equity/live carries it beside the unchanged ledger figures
            p = await CE.read_paper(conn, now=E + 5 * H, account_id=acct)
            assert p["management"]["status"] == "OK"
            assert p["management"]["pre_management_history"][
                "ledger_equity_usd"] == p["equity_usd"]
        finally:
            await tr.rollback()
            await conn.close()
    asyncio.run(go())


@pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
def test_a_seeded_session_reconciles_on_either_side_of_the_epoch():
    """The synthetic session (real ledger, simulator and settlements) read
    with the epoch AFTER all of it (everything carried or history; the
    seeded books are too old to be epoch marks, so open positions are
    EPOCH_OPEN_MARK_UNVERIFIED) and BEFORE all of it (everything opened
    after the epoch). Both reconcile to the ledger and to Audrey."""
    import asyncio
    import time

    from sportsassets import bettor_paper_ledger as L
    from tests import paper_harness as HA
    from tests import paper_ops_seed as SEED

    async def go():
        conn = await HA.connect()
        try:
            acct = await HA.new_account(conn, "epoch%s" % uuid.uuid4().hex[:6],
                                        now=HA.T0)
            before = time.time() - 1.0
            await SEED.seed(conn, acct, t0=HA.T0)
            after = time.time() + 1.0
            a = acct["account_id"]
            bal = await L.balances(conn, a, now=after)
            cash = float(await conn.fetchval(
                "SELECT sum(cash_delta_usd) FROM paper_ledger "
                " WHERE account_id=$1", a))
            for ep_at in (after, before):
                b = await EP.read(conn, a, bal=bal, now=after + 5,
                                  epoch_at=ep_at)
                assert b["status"] == "OK", b["why"]
                assert b["opening_equity_usd"] == 500000.00
                op = b["opening"]
                assert round(op["available_cash_usd"] + op["reserved_usd"]
                             + op["carried_position_mark_value_usd"], 2) \
                    == 500000.00
                e0 = float(await conn.fetchval(
                    "SELECT coalesce(sum(cash_delta_usd),0) FROM paper_ledger"
                    " WHERE account_id=$1 AND (committed_at < to_timestamp($2)"
                    "   OR kind = 'INITIAL_FUNDING')", a, ep_at))
                checks = AU.management_checks(b, ledger_cash_now=cash,
                                              ledger_cash_at_epoch=e0)
                assert all(c["passed"] for c in checks), checks
            late = await EP.read(conn, a, bal=bal, now=after + 5,
                                 epoch_at=after)
            assert late["realized_pnl_usd"] == 0.0
            assert late["opened_after_epoch"] == 0
            assert late["carried_unverified"] == len(
                [p for p in bal["open_positions"]])
            early = await EP.read(conn, a, bal=bal, now=after + 5,
                                  epoch_at=before)
            assert early["carried_positions"] == 0
            assert early["opened_after_epoch"] >= 1
            # everything was opened after this epoch: its realized P&L is
            # the ledger's own realized P&L
            assert abs(early["realized_pnl_usd"]
                       - bal["realized_pnl_usd"]) < 0.01
        finally:
            await conn.close()
    asyncio.run(go())
