"""THE MANAGEMENT EPOCH, RECONCILED EXACTLY (bettor_paper_epoch rule 7 and
the epoch mark from recorded evidence).

PRODUCTION (Command UI on RC4 7fd4574e, 2026-10-08): "management equity
curve not reconstructable ... while 4 carried position(s) are
EPOCH_OPEN_MARK_UNVERIFIED or $141.80 of post-epoch ledger cash is held
outside"; equity $458,852 = $500,000 - $41,053 realized - $94 unrealized; the
4 carried positions' current marked value $0 (all closed). The owner: the
epoch reconciles exactly to its $500,000 opening baseline plus subsequent
ledger-supported changes, every difference itemised; today's equity is
never forced to $500,000 and historical losses are never hidden.

Pure tests drive `management_book` / `classify_epoch_mark`; the DB test
(RN1X_TEST_DSN) writes real ledger entries, book observations and
settlements and reads them back through `read`.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from sportsassets import bettor_paper_epoch as EP

E = EP.EPOCH_START
H = 3600.0
PK_HIST, PK_CAR, PK_UNV, PK_NEW = ("pk:hist", "pk:carried", "pk:unverified",
                                   "pk:new")


def fill(pk, d, qty, price, fee, at, seq=None):
    return {"position_key": pk, "direction": d, "qty": qty,
            "gross_usd": qty * price, "fee_usd": fee, "at": at, "seq": seq}


def settle(pk, cash, at, kind="SETTLEMENT", seq=None):
    return {"position_key": pk, "kind": kind, "cash_usd": cash, "at": at,
            "seq": seq}


def _cash(x):
    if "direction" in x:
        g, fe = float(x["gross_usd"]), float(x["fee_usd"])
        return -(g + fe) if x["direction"] == "BUY" else g - fe
    return float(x["cash_usd"])


def book(*, fills=(), sets=(), sq=None, em=None, nm=None, res_e=0.0,
         res_now=0.0, points=None, extra_pre=0.0, extra_post=0.0):
    """A ledger consistent with the events (INITIAL_FUNDING $500k before the
    epoch); `extra_pre` / `extra_post` add ledger cash no event explains."""
    fills, sets = list(fills), list(sets)
    ev = fills + sets
    pre = 500000.0 + sum(_cash(x) for x in ev if x["at"] < E) + extra_pre
    post = sum(_cash(x) for x in ev if x["at"] >= E) + extra_post
    return EP.management_book(
        epoch_at=E, opening_equity=EP.OPENING_EQUITY_USD, fills=fills,
        settlement_entries=sets, settled_qty=sq or {}, epoch_marks=em or {},
        now_marks=nm or {},
        ledger_epoch={"cash_usd": pre, "reserved_usd": res_e,
                      "funding_usd": 500000.0},
        ledger_now={"cash_usd": pre + post, "reserved_usd": res_now},
        hwm_points=points)


def items_of(b, kind):
    return [i for i in b["reconciliation"]["differences_to_ledger"]
            if i["item"] == kind]


# ── 1. THE EXACT RECONCILIATION ──────────────────────────────────────────

def _the_mixed_ledger():
    """History with a loss, a verified carried position, an unverified
    carried position that settled after the epoch (the production shape:
    held outside, now closed), a post-epoch correction of a pre-epoch
    settlement and a position opened after the epoch."""
    fills = [
        # history: bought 2,000 at 0.60 (+$12 fee), settled LOST before E
        fill(PK_HIST, "BUY", 2000, 0.60, 12.0, E - 9 * H, seq=2),
        # carried, verified at 0.50; part sold after the epoch
        fill(PK_CAR, "BUY", 1000, 0.40, 8.0, E - 6 * H, seq=3),
        fill(PK_CAR, "SELL", 400, 0.58, 2.5, E + 2 * H, seq=10),
        # carried, UNVERIFIED: 300 bought at 0.70, settled 0.47 after E
        fill(PK_UNV, "BUY", 300, 0.70, 3.0, E - 5 * H, seq=4),
        # opened after the epoch
        fill(PK_NEW, "BUY", 800, 0.25, 4.0, E + 3 * H, seq=11)]
    sets = [settle(PK_HIST, 0.0, E - 1 * H, seq=5),
            settle(PK_UNV, 141.0, E + 4 * H, seq=12),
            settle(PK_HIST, 0.80, E + 6 * H, kind="CORRECTION", seq=13)]
    return dict(fills=fills, sets=sets, sq={PK_HIST: 2000, PK_UNV: 300},
                em={PK_CAR: {"price": 0.50, "basis": EP.B_BOOK},
                    PK_UNV: {"why": EP.R_LAST_BOOK_TOO_OLD,
                             "evidence": {"last_error_free_book_age_at_"
                                          "epoch_s": 1712.0}}},
                nm={PK_CAR: {"price": 0.55}, PK_NEW: {"price": 0.20}})


def test_opening_plus_itemised_changes_equals_management_equity_exactly():
    b = book(**_the_mixed_ledger())
    rc = b["reconciliation"]
    # management: opening + realized + unrealized, per position
    assert rc["opening_equity_usd"] == 500000.00
    assert rc["management_gap_usd"] == 0.0
    assert rc["changes"]["itemised_in"] == "rows"
    rows = {r["position_key"]: r for r in b[rc["changes"]["itemised_in"]]}
    assert set(rows) == {PK_CAR, PK_NEW}
    assert rc["changes"]["positions"] == 2
    # carried: 400 sold at 0.58 less $2.50 against the 0.50 epoch basis
    assert rows[PK_CAR]["realized_pnl_usd"] == round(232 - 2.5 - 200, 2)
    assert rows[PK_CAR]["unrealized_pnl_usd"] == round(600 * (0.55 - 0.50),
                                                       2)
    avg = (200 + 4.0) / 800
    assert rows[PK_NEW]["unrealized_pnl_usd"] == round(
        800 * 0.20 - 800 * avg, 2)
    total = sum(r["realized_pnl_usd"] + (r["unrealized_pnl_usd"] or 0)
                for r in rows.values())
    assert round(500000 + total, 2) == b["equity_usd"]
    assert abs(rc["changes"]["realized_usd"] + rc["changes"]["unrealized_usd"]
               - total) < 1e-6
    # TODAY'S EQUITY IS NOT FORCED TO THE OPENING: it moved by the changes
    assert b["equity_usd"] != 500000.00
    assert rc["forces_equity_to_opening"] is False


def test_management_plus_every_itemised_difference_equals_the_ledger():
    b = book(**_the_mixed_ledger())
    rc = b["reconciliation"]
    assert rc["exact"] is True and rc["fully_attributed"] is True
    assert abs(rc["ledger_gap_usd"]) < 1e-6
    # the ledger side, stated independently: ledger cash now + open values
    pre = 500000 - (1200 + 12) - (400 + 8) - (210 + 3) + 0.0
    post = (232 - 2.5) - (200 + 4) + 141.0 + 0.80
    ledger_eq = (pre + post) + 600 * 0.55 + 800 * 0.20
    assert abs(rc["ledger_equity_usd"] - ledger_eq) < 1e-6
    assert abs(b["equity_usd"] + rc["differences_total_usd"]
               - ledger_eq) < 0.01
    # EVERY difference is a line, with what it is
    (hist,) = items_of(b, EP.I_PRE_REALIZED)
    assert hist["amount_usd"] == -1212.0          # the historical LOSS, shown
    (car,) = items_of(b, EP.I_PRE_CARRIED)
    assert car["amount_usd"] == round(-408.0 + 1000 * 0.50, 6)
    (unv,) = items_of(b, EP.I_UNVERIFIED)
    assert unv["position_key"] == PK_UNV
    assert unv["pre_epoch_ledger_cash_usd"] == -213.0
    assert unv["post_epoch_ledger_cash_usd"] == 141.0
    assert unv["amount_usd"] == -72.0             # its whole ledger result
    assert EP.R_LAST_BOOK_TOO_OLD in unv["why"]
    (cor,) = items_of(b, EP.I_HIST_CORRECTION)
    assert cor["amount_usd"] == 0.80 and cor["ledger_seq"] == 13
    (una,) = items_of(b, EP.I_UNATTRIBUTED)
    assert una["amount_usd"] == 0.0
    (fund,) = items_of(b, EP.I_FUNDING)
    assert fund["amount_usd"] == 0.0
    # the pre-management result: ledger equity at the epoch - $500,000
    assert rc["pre_management_result_usd"] == round(-1212 + 92 - 213, 6)


def test_unattributed_ledger_cash_is_a_named_line_and_not_exact_silence():
    b = book(**_the_mixed_ledger(), extra_pre=10.0, extra_post=-2.5)
    rc = b["reconciliation"]
    (una,) = items_of(b, EP.I_UNATTRIBUTED)
    assert una["before_epoch_usd"] == 10.0
    assert una["after_epoch_usd"] == -2.5
    assert rc["fully_attributed"] is False
    # the bridge still states every dollar
    assert abs(rc["ledger_gap_usd"]) < 1e-6


def test_the_held_outside_position_is_listed_with_its_whole_result():
    b = book(**_the_mixed_ledger())
    (u,) = b["unverified_positions"]
    assert u["state"] == EP.R_UNVERIFIED
    assert u["why"] == EP.R_LAST_BOOK_TOO_OLD
    assert u["why_family"] == EP.R_NO_EPOCH_BOOK
    assert u["epoch_mark_evidence"]["last_error_free_book_age_at_epoch_s"] \
        == 1712.0
    assert u["closed"] is True and u["closed_at"] == E + 4 * H
    assert u["whole_ledger_result_usd"] == -72.0
    assert u["value_now_basis"] == "CLOSED"


# ── 2. THE HIGH-WATER MARK ONCE THE UNVERIFIED POSITIONS HAVE CLOSED ─────

def test_snapshots_after_the_last_unverified_position_closed_are_rebased():
    lg = _the_mixed_ledger()
    b0 = book(**lg)
    offset = b0["opening"]["cash_including_reserved_usd"] - \
        b0["opening"]["ledger_cash_at_epoch_usd"]
    # one snapshot while the unverified position was open (seq 11, before
    # its settlement at seq 12) and one after it settled (seq 12)
    t_open, t_after = E + 3.5 * H, E + 5 * H
    eq_after = 501000.0
    b = book(**lg, points=[(t_open, 502000.0, 11), (t_after, eq_after, 12)])
    assert b["high_water_mark_exact_from"] == E + 4 * H
    assert b["high_water_mark_points_used"] == 1
    assert b["high_water_mark_points_before_exact_from"] == 1
    # re-based: snapshot equity + offset - the held-outside cash it held
    # (the unverified settlement, $141; the correction at seq 13 not yet)
    expect = round(eq_after + offset - 141.0, 2)
    assert b["high_water_mark_usd"] == max(expect, b["equity_usd"], 500000.0)
    assert "only" not in b["high_water_mark_rule"]
    assert EP.R_UNVERIFIED in b["high_water_mark_rule"]


def test_held_outside_corrections_alone_no_longer_block_the_snapshots():
    fills = [fill(PK_HIST, "BUY", 100, 0.5, 0.0, E - 9 * H, seq=2),
             fill(PK_NEW, "BUY", 1000, 0.40, 0.0, E + 1 * H, seq=6)]
    sets = [settle(PK_HIST, 0.0, E - 1 * H, seq=3),
            settle(PK_HIST, 100.0, E + 2 * H, kind="CORRECTION", seq=7)]
    b = book(fills=fills, sets=sets, sq={PK_HIST: 100},
             nm={PK_NEW: {"price": 0.35}},
             points=[(E + 1.5 * H, 500000 - 50 - 400 + 1000 * 0.48, 6),
                     (E + 3 * H, 500000 - 50 - 400 + 100 + 1000 * 0.47, 7)])
    assert b["rebase_exact"] is False      # $100 is held outside...
    assert b["high_water_mark_points_used"] == 2   # ...but subtracted by seq
    assert b["high_water_mark_usd"] == 500080.00
    assert b["high_water_mark_basis"] == "EQUITY_SNAPSHOT"
    assert b["equity_usd"] == 499950.00
    assert b["drawdown_usd"] == 130.00


def test_an_open_unverified_position_still_refuses_the_snapshots():
    b = book(fills=[fill(PK_UNV, "BUY", 1000, 0.40, 8.0, E - 6 * H, seq=2),
                    fill(PK_UNV, "SELL", 500, 0.60, 1.0, E + 1 * H, seq=5)],
             em={PK_UNV: {"why": EP.R_NEVER_OBSERVED}},
             nm={PK_UNV: {"price": 0.6}},
             points=[(E + 2 * H, 600000.0, 6)])
    assert b["high_water_mark_points_used"] == 0
    assert b["high_water_mark_exact_from"] is None
    assert "only" in b["high_water_mark_rule"]
    assert "still open" in b["high_water_mark_rule"]
    # its current value is on the ledger side of the bridge, named
    (u,) = items_of(b, EP.I_UNVERIFIED)
    assert u["value_now_basis"] == "CURRENT_LEDGER_MARK"
    assert u["value_now_usd"] == 300.0
    assert b["reconciliation"]["exact"] is True


# ── 3. THE EPOCH MARK, FROM RECORDED EVIDENCE AT THE EPOCH INSTANT ───────

def _md(bids=(), offers=()):
    return {"bids": json.dumps([{"px": {"value": "%.2f" % p}, "qty": str(q)}
                                for p, q in bids]),
            "offers": json.dumps([{"px": {"value": "%.2f" % p},
                                   "qty": str(q)} for p, q in offers])}


def mark(**kw):
    base = dict(epoch_at=E, holding_side="LONG", window_book=None,
                last_book=None, window_reads=None, first_book_after=None,
                settlements=None)
    base.update(kw)
    return EP.classify_epoch_mark(**base)


def test_a_book_inside_the_window_is_the_mark_unchanged():
    wb = dict(_md(bids=[(0.52, 10)], offers=[(0.55, 10)]), obs_id=7,
              observed_at=E - 60)
    m = mark(window_book=wb, last_book=wb)
    assert m["price"] == 0.52 and m["basis"] == EP.B_BOOK
    assert m["source"] == "paper_book_observations:7"
    assert m["age_at_epoch_s"] == 60.0


def test_a_settlement_whose_every_outcome_read_was_recorded_by_the_epoch():
    s = [{"settlement_id": "paperset:a", "version": 1, "outcome": "WON",
          "payout_per_contract": 1.0, "settled_at": E + 40,
          "evidence": json.dumps({"rows": [
              {"valuation_id": 1, "outcome_at": E - 90},
              {"valuation_id": 2, "outcome_at": E - 30}]})}]
    m = mark(settlements=s, last_book={"obs_id": 3, "observed_at": E - 4000,
                                       "market_state": "MARKET_STATE_OPEN"})
    assert m["price"] == 1.0 and m["basis"] == EP.B_SETTLEMENT
    assert m["source"] == "paper_settlements:paperset:a"
    assert m["observed_at"] == E - 30


def test_a_settlement_read_after_the_epoch_is_shown_but_never_the_mark():
    s = [{"settlement_id": "paperset:b", "version": 1, "outcome": "LOST",
          "payout_per_contract": 0.0, "settled_at": E + 900,
          "evidence": {"rows": [{"outcome_at": E - 30},
                                {"outcome_at": E + 600}]}}]
    m = mark(settlements=s, last_book={"obs_id": 3, "observed_at": E - 1712,
                                       "market_state": "MARKET_STATE_OPEN"},
             first_book_after={"observed_at": E + 45})
    assert m.get("price") is None
    assert m["why"] == EP.R_LAST_BOOK_TOO_OLD
    ev = m["evidence"]
    assert ev["last_error_free_book_age_at_epoch_s"] == 1712.0
    assert ev["first_error_free_book_after_epoch_delay_s"] == 45.0
    assert ev["settlement"]["payout_per_contract"] == 0.0
    assert ev["settlement_not_used_because"] == \
        "THE_OUTCOME_WAS_FIRST_FULLY_RECORDED_AFTER_THE_EPOCH"


def test_a_venue_price_settlement_read_by_the_epoch_is_the_mark():
    s = [{"settlement_id": "paperset:c", "version": 1,
          "outcome": "SETTLED_AT_VENUE_PRICE", "payout_per_contract": 0.485,
          "settled_at": E + 30,
          "evidence": {"price": 0.485, "evidence": [
              {"valuation_id": 9, "settlement_read": "0.485",
               "settlement_read_at": E - 5}]}}]
    m = mark(settlements=s)
    assert m["price"] == 0.485 and m["basis"] == EP.B_SETTLEMENT


def test_evidence_without_an_instant_never_proves_by_the_epoch():
    s = [{"settlement_id": "paperset:d", "version": 1, "outcome": "WON",
          "payout_per_contract": 1.0, "settled_at": E + 30,
          "evidence": {"test": "synthetic"}}]
    m = mark(settlements=s)
    assert m.get("price") is None and m["why"] == EP.R_NEVER_OBSERVED
    assert m["evidence"]["settlement_not_used_because"] == \
        "THE_SETTLEMENT_EVIDENCE_NAMES_NO_OUTCOME_READ_INSTANT"
    s[0]["evidence"] = {"rows": [{"outcome_at": E - 5}, {"outcome_at": None}]}
    assert mark(settlements=s).get("price") is None


def test_a_market_that_ended_before_the_epoch_is_named_not_marked():
    s = [{"settlement_id": "paperset:e", "version": 1, "outcome": "WON",
          "payout_per_contract": 1.0, "settled_at": E + 7200,
          "evidence": {"rows": [{"outcome_at": E + 7000}]}}]
    m = mark(last_book={"obs_id": 4, "observed_at": E - 5400,
                        "market_state": "MARKET_STATE_EXPIRED"},
             settlements=s)
    assert m.get("price") is None and m["why"] == EP.R_MARKET_ENDED
    assert m["evidence"]["last_error_free_book_market_state"] == \
        "MARKET_STATE_EXPIRED"
    # the later settlement is shown beside it, never used as the mark
    assert m["evidence"]["settlement"]["payout_per_contract"] == 1.0


def test_a_market_never_observed_and_failed_reads_are_named():
    assert mark()["why"] == EP.R_NEVER_OBSERVED
    m = mark(window_reads={"total": 3, "failed": 3,
                           "failed_example": "HTTP_429"},
             last_book={"obs_id": 1, "observed_at": E - 9000,
                        "market_state": None})
    assert m["why"] == EP.R_WINDOW_READS_FAILED
    assert m["evidence"]["failed_read_example"] == "HTTP_429"


def test_a_window_book_with_no_exit_side_keeps_its_named_reason():
    wb = dict(_md(offers=[(0.55, 10)]), obs_id=8, observed_at=E - 10)
    m = mark(window_book=wb, last_book=wb)
    assert m.get("price") is None
    assert m["why"].startswith(EP.R_NO_EXIT_SIDE + ": ")


def test_a_settlement_verified_carried_position_realizes_only_after_epoch():
    """Verified from its settlement: the payout IS the epoch value, so its
    settlement after the epoch realizes nothing; a later correction is
    post-epoch management P&L."""
    b = book(fills=[fill(PK_CAR, "BUY", 100, 0.30, 0.0, E - 3 * H, seq=2)],
             sets=[settle(PK_CAR, 100.0, E + 60, seq=4),
                   settle(PK_CAR, -100.0, E + 2 * H, kind="CORRECTION",
                          seq=5)],
             sq={PK_CAR: 100},
             em={PK_CAR: {"price": 1.0, "basis": EP.B_SETTLEMENT,
                          "source": "paper_settlements:x"}})
    (row,) = b["rows"]
    assert row["epoch_mark_basis"] == EP.B_SETTLEMENT
    assert b["opening"]["carried_position_mark_value_usd"] == 100.0
    assert b["realized_pnl_usd"] == -100.0
    assert b["reconciliation"]["exact"] is True


def test_the_terminal_states_are_the_freshness_classifiers():
    from sportsassets import bettor_paper_freshness as PMF
    assert EP.TERMINAL_MARKET_STATES == PMF.TERMINAL_MARKET_STATES


def test_the_new_reasons_are_classified_accounting():
    from sportsassets import refusal_taxonomy_table as TT
    for c in (EP.R_NEVER_OBSERVED, EP.R_LAST_BOOK_TOO_OLD,
              EP.R_MARKET_ENDED, EP.R_WINDOW_READS_FAILED,
              EP.R_NO_EXIT_SIDE):
        assert TT.TABLE[c][2] == "ACCOUNTING", c


# ── 4. AGAINST THE REAL LEDGER (RN1X_TEST_DSN) ──────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
def test_the_read_names_each_carried_mark_and_reconciles_exactly():
    """Two positions open at an epoch placed between their fills and their
    settlements: one whose only books are older than the bound and whose
    outcome was read after the epoch (UNVERIFIED, LAST_BOOK_TOO_OLD), one
    whose outcome was read before the epoch (verified from its settlement).
    Both settle after the epoch; the read reconciles to the ledger exactly
    and re-bases the snapshot taken after both closed."""
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    from tests import paper_harness as HA

    async def go():
        conn = await HA.connect()
        try:
            if await conn.fetchval(
                    "SELECT to_regclass('paper_ledger') IS NULL"):
                pytest.skip("migration 171 is not applied")
            acct = await HA.new_account(conn, "epochrc", now=HA.T0)
            a = acct["account_id"]
            tail = a[-10:]
            slugs = {"old": "epochrc-%s-old" % tail,
                     "set": "epochrc-%s-set" % tail}
            groups = {}
            for i, (k, slug) in enumerate(sorted(slugs.items())):
                o = HA.order(acct, key="e%d" % i, slug=slug, qty=100.0,
                             limit=0.5, at=HA.T0 + i)
                got = await L.submit_order(conn, o, fee_fn=HA.zero_fee,
                                           now=HA.T0 + i)
                assert got["ok"], got
                await HA.observe(conn, slug, HA.T0 + i + 3.0,
                                 offers=[(0.5, 100)], bids=[(0.47, 100)])
                await SIM.simulate_order(conn, got["order"]["order_id"],
                                         now=HA.T0 + i + 4.0,
                                         fee_fn=HA.zero_fee)
                groups[k] = o["group_id"]
            await asyncio.sleep(0.05)
            epoch = time.time()
            await asyncio.sleep(0.05)
            # the 'old' market's last error-free book is 1,712 s before E
            await HA.observe(conn, slugs["old"], epoch - 1712.0,
                             bids=[(0.44, 100)], offers=[(0.46, 100)])
            for k, payout_outcome, read_at in (
                    ("old", "LOST", epoch + 600.0),
                    ("set", "WON", epoch - 30.0)):
                r = await L.settle(
                    conn, account_id=a, group_id=groups[k], slug=slugs[k],
                    holding_side="LONG",
                    settlement_event_key="venue-final:%s" % slugs[k],
                    outcome=payout_outcome,
                    evidence={"rows": [{"valuation_id": 1,
                                        "outcome_at": read_at}]},
                    evidence_source="TEST_FIXTURE_SYNTHETIC",
                    at=epoch + 1.0, session_id=acct["session_id"])
                assert r["ok"], r
            seq = await conn.fetchval(
                "SELECT max(seq) FROM paper_ledger WHERE account_id=$1", a)
            cash_now = float(await conn.fetchval(
                "SELECT sum(cash_delta_usd) FROM paper_ledger "
                " WHERE account_id=$1", a))
            # an equity snapshot after both closed (no open position)
            await conn.execute(
                "INSERT INTO paper_equity_snapshots (session_id, account_id, "
                " at, cash_usd, reserved_usd, marked_value_usd, equity_usd, "
                " equity_excluding_unmarked_usd, unmarked_positions, "
                " realized_pnl_usd, unrealized_pnl_usd, last_sequence) "
                " VALUES ($1,$2,to_timestamp($3),$4,0,0,$4,$4,0,0,0,$5)",
                acct["session_id"], a, time.time() + 1.0, cash_now, seq)
            now = time.time() + 5.0
            bal = await L.balances(conn, a, now=now)
            b = await EP.read(conn, a, bal=bal, now=now, epoch_at=epoch)
            assert b["status"] == "OK", b["why"]
            assert b["carried_positions"] == 1
            assert b["carried_unverified"] == 1
            (u,) = b["unverified_positions"]
            assert u["market"] == slugs["old"]
            assert u["why"] == EP.R_LAST_BOOK_TOO_OLD
            ev = u["epoch_mark_evidence"]
            assert abs(ev["last_error_free_book_age_at_epoch_s"]
                       - 1712.0) < 0.01
            assert ev["settlement_not_used_because"] == \
                "THE_OUTCOME_WAS_FIRST_FULLY_RECORDED_AFTER_THE_EPOCH"
            (row,) = b["rows"]
            assert row["market"] == slugs["set"]
            assert row["epoch_mark_basis"] == EP.B_SETTLEMENT
            assert row["epoch_mark_price"] == 1.0
            assert row["realized_pnl_usd"] == 0.0
            rc = b["reconciliation"]
            assert rc["exact"] and rc["fully_attributed"], rc
            # the bridge to the ledger: cash now + no open value
            assert abs(rc["ledger_equity_usd"] - cash_now) < 1e-6
            (unv,) = [i for i in rc["differences_to_ledger"]
                      if i["item"] == EP.I_UNVERIFIED]
            assert unv["amount_usd"] == -50.0      # bought 50, settled 0
            assert b["high_water_mark_points_used"] == 1
            assert "only" not in b["high_water_mark_rule"]
            assert b["pre_management_history"]["pre_management_result_usd"] \
                == rc["pre_management_result_usd"]
        finally:
            await conn.close()
    asyncio.run(go())
