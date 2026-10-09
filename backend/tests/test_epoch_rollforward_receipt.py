"""THE $500,000 EPOCH AS AN EXACT DECIMAL ROLL-FORWARD RECEIPT (RC6 identity lane).

The owner's manifest row: "500000 epoch reconciliation:
IMPLEMENTATION_CLAIM_UNVERIFIED -- no exact signed ledger proof". The epoch
reconciliation (bettor_paper_epoch, 80019a8d) reconciled the management book
to the ledger in floats rounded for display, with no roll-forward of the form

    OPENING 500,000.00 + NET CASH FLOWS + REALIZED P&L + UNREALIZED P&L on
    verified marks - ALL-IN FEES = ENDING EQUITY

every input exact, the open marks verified and the append-only ledger
replayed. management_book now carries that roll-forward (`rollforward`, every
leg a Decimal at the ledger's 6 places, ENDING taken from the ledger cash
independently of the legs, and the bridge to ledger equity itemised), and
backend/tools/epoch_rollforward_receipt.py (stdlib only) replays a production
export (research/rc6_identity_epoch_rollforward.sql) through it.

CAPITAL-CRITICAL: the Postgres test builds a real paper ledger -- fees with
sub-cent remainders, a position flat before the epoch, one carried on a
verified epoch book and partly sold after, one held outside (no epoch book)
and settled after, one opened and settled after, one opened after and still
open on a fresh book -- runs the research SQL FILE itself against it, and
requires the tool's receipt to equal bettor_paper_epoch.read's own
roll-forward to the micro-dollar.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time
from decimal import Decimal

import pytest

from sportsassets import bettor_paper_epoch as EP

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "backend" / "tools" / "epoch_rollforward_receipt.py"
SQL = ROOT / "research" / "rc6_identity_epoch_rollforward.sql"
DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

sys.path.insert(0, str(TOOL.parent))
import epoch_rollforward_receipt as T  # noqa: E402

E = EP.EPOCH_START
D = Decimal


def _book(**kw):
    """management_book over a ledger consistent with the events."""
    fills = kw.pop("fills", [])
    sets = kw.pop("sets", [])
    extra_post = D(kw.pop("extra_post", "0"))

    def cash(x):
        if "direction" in x:
            g, f = D(x["gross_usd"]), D(x["fee_usd"])
            return -(g + f) if x["direction"] == "BUY" else g - f
        return D(x["cash_usd"])
    pre = D("500000") + sum((cash(x) for x in fills + sets if x["at"] < E),
                            D(0))
    post = sum((cash(x) for x in fills + sets if x["at"] >= E), D(0))
    return EP.management_book(
        epoch_at=E, opening_equity=EP.OPENING_EQUITY_USD, fills=fills,
        settlement_entries=sets, settled_qty=kw.pop("sq", {}),
        epoch_marks=kw.pop("em", {}), now_marks=kw.pop("nm", {}),
        ledger_epoch={"cash_usd": pre, "reserved_usd": D(0),
                      "funding_usd": D("500000")},
        ledger_now={"cash_usd": pre + post + extra_post,
                    "reserved_usd": D(0)}, meta={}, hwm_points=[])


def F(pk, d, qty, px, fee, at):
    return {"position_key": pk, "direction": d, "qty": D(qty),
            "gross_usd": D(qty) * D(px), "fee_usd": D(fee), "at": at}


# ── the roll-forward, pure ────────────────────────────────────────────

def test_a_closed_and_an_open_position_roll_forward_exactly_with_fees_apart():
    """BUY 100 @ 0.40 fee 1.75 and SELL 40 @ 0.55 fee 0.70 after the epoch;
    60 open, marked at 0.52 on a fresh book; a second position bought 30 @
    0.30 fee 0.525 and settled WON. Every leg exact; fees apart."""
    fills = [F("a", "BUY", "100", "0.40", "1.75", E + 10),
             F("a", "SELL", "40", "0.55", "0.70", E + 20),
             F("b", "BUY", "30", "0.30", "0.525", E + 30)]
    sets = [{"position_key": "b", "kind": "SETTLEMENT",
             "cash_usd": D("30.000000"), "at": E + 40}]
    b = _book(fills=fills, sets=sets, sq={"b": D("30")},
              nm={"a": {"price": 0.52, "stale": False}})
    rf = b["rollforward"]
    # a: closed 40 of 100 at gross cost 40 x 0.40 = 16.00 -> 22.00 - 16.00
    #    open 60 at gross 24.00, marked 60 x 0.52 = 31.20 -> +7.20
    # b: settled 30.00 - 9.00 = 21.00; fees 1.75 + 0.70 + 0.525 = 2.975
    assert rf["realized_pnl"] == "27.000000"
    assert rf["unrealized_pnl"] == "7.200000"
    assert rf["all_in_fees"] == "2.975000"
    assert rf["net_cash_flows"] == "0"
    assert D(rf["ending_capital"]) == D("500031.225")
    assert rf["gap"] in ("0", "0.000000") and rf["balanced"] is True
    assert rf["verdict"] == EP.RF_EXACT
    assert rf["open_position_marks_verified"] is True
    # the cents view states its own rounding residual (0.005 of fees)
    assert rf["cents"]["all_in_fees"] == "2.98"
    assert rf["cents"]["ending_capital"] == "500031.23"
    assert D(rf["cents"]["rounding_residual"]) == D("-0.01")
    # WHY THE RECEIPT NEVER READS THE DISPLAY FIGURES: the book's float
    # display rounds the exact 500,031.225000 to 500,031.22 (binary float),
    # the receipt's Decimal half-up to 500,031.23 -- the exact value, not
    # either rounding, is what reconciles
    assert b["equity_usd"] == 500031.22
    assert D(rf["ending_capital"]) == D("500031.225000")
    assert rf["bridge_to_ledger"]["gap"] in ("0", "0.000000")


def test_a_stale_or_missing_open_mark_is_never_a_verified_receipt():
    fills = [F("a", "BUY", "100", "0.40", "0", E + 10)]
    stale = _book(fills=fills, nm={"a": {"price": 0.50, "stale": True}})
    assert stale["rollforward"]["verdict"] == EP.RF_UNVERIFIED_MARKS
    assert stale["rollforward"]["open_position_marks_verified"] is False
    unmarked = _book(fills=fills, nm={})
    rf = unmarked["rollforward"]
    assert rf["verdict"] == EP.RF_UNVERIFIED_MARKS
    assert rf["unrealized_pnl"] == "0" and rf["balanced"] is True
    assert rf["open_marks"][0]["mark_state"] == "UNMARKED"


def test_unexplained_post_epoch_ledger_cash_does_not_balance_by_its_amount():
    fills = [F("a", "BUY", "10", "0.50", "0", E + 10)]
    b = _book(fills=fills, nm={"a": {"price": 0.5, "stale": False}},
              extra_post="12.345678")
    rf = b["rollforward"]
    assert rf["balanced"] is False and rf["verdict"] == EP.RF_NOT_BALANCED
    assert D(rf["gap"]) == D("-12.345678")


def test_a_held_outside_position_is_itemised_never_forced():
    fills = [F("h", "BUY", "50", "0.40", "0", E - 100)]
    sets = [{"position_key": "h", "kind": "SETTLEMENT",
             "cash_usd": D("0"), "at": E + 50}]
    b = _book(fills=fills, sets=sets, sq={"h": D("50")},
              em={"h": {"why": EP.R_LAST_BOOK_TOO_OLD}})
    rf = b["rollforward"]
    assert rf["verdict"] == EP.RF_EXACT_HELD_OUTSIDE
    assert rf["held_outside_positions"] == 1
    items = {i["item"]: i for i in rf["bridge_to_ledger"]["items"]}
    assert D(items[EP.I_UNVERIFIED]["amount"]) == D("-20")
    assert rf["bridge_to_ledger"]["gap"] in ("0", "0.000000")


# ── the tool: refusals ────────────────────────────────────────────────

def _manifest(**kw):
    m = {"version": "X", "account_id": "acct", "epoch_at": str(E),
         "mark_max_age_s": "300", "db_now": str(E + 3600),
         "ledger_rows": 1, "fill_rows": 0, "settlement_rows": 0,
         "epoch_book_slugs": 0, "now_book_slugs": 0}
    m.update(kw)
    return "EPOCHRF|manifest|0|" + json.dumps(m)


FUND = ('EPOCHRF|ledger|0|[[1, "INITIAL_FUNDING", "500000.000000", '
        '"0.000000", "500000.000000", "0.000000", "%s", null, null, null, '
        'null]]' % (E - 86400))


def test_a_truncated_export_is_refused_never_read_as_a_smaller_ledger():
    with pytest.raises(T.ExportError, match=T.R_INCOMPLETE):
        T.parse(_manifest(ledger_rows=2) + "\n" + FUND)
    with pytest.raises(T.ExportError, match=T.R_INCOMPLETE):
        T.parse(FUND)                                   # no manifest


def test_a_float_amount_never_reaches_the_receipt():
    with pytest.raises(T.ExportError, match="float"):
        T.dec(0.1)
    assert T.dec("0.100000") == D("0.1")


def test_the_parse_reads_a_research_sql_log_with_its_prefixes():
    log = "\n".join("research\tRun\t2026-10-09T12:00:00.0Z " + x
                    for x in (" line ", "------", " " + _manifest(),
                              " " + FUND + "   ", "(2 rows)"))
    exp = T.parse(log)
    r = T.receipt(exp)
    assert r["verified"] is True and r["verdict"] == EP.RF_EXACT
    assert r["contract"]["ending_capital"] == "500000.00"
    assert r["contract"]["reconciled_to_append_only_ledger"] is True


def test_a_broken_chain_and_a_mismatched_fill_are_named():
    bad = FUND.replace('"500000.000000", "0.000000", "%s"' % (E - 86400),
                       '"499999.990000", "0.000000", "%s"' % (E - 86400))
    r = T.receipt(T.parse(_manifest() + "\n" + bad))
    assert r["verified"] is False and "LEDGER_REPLAY" in r["verdict"]
    assert r["ledger_replay"]["problems"][0]["why"] == \
        "CASH_AFTER_DOES_NOT_CHAIN"
    fill_row = ('[2, "FILL", "-50.000000", "-50.000000", "499950.000000", '
                '"0.000000", "%s", "f1", null, null, null]' % (E + 10))
    led = FUND[:-1] + ", " + fill_row + "]"
    fills = ('EPOCHRF|fills|0|[["f1", "g", "s", "LONG", "BUY", "100", '
             '"49.000000", "0.000000", "%s", 2, null]]' % (E + 10))
    r = T.receipt(T.parse("\n".join([
        _manifest(ledger_rows=2, fill_rows=1), led, fills])))
    assert "COSTS" in r["verdict"]
    assert r["costs"]["problems"][0]["why"] == "FILL_CASH_MISMATCH"


def test_a_short_holding_marks_at_one_minus_the_best_offer():
    book = {"obs_id": 1, "observed_at": str(E),
            "bids": [{"px": {"value": "0.40"}, "qty": "10"}],
            "offers": [{"px": {"value": "0.45"}, "qty": "10"}]}
    s = T.exit_mark(book, "SHORT", now=D(str(E + 10)), max_age=D("300"))
    lg = T.exit_mark(book, "LONG", now=D(str(E + 10)), max_age=D("300"))
    assert D(str(s["price"])) == D("0.55") and s["stale"] is False
    assert D(str(lg["price"])) == D("0.40")
    old = T.exit_mark(book, "LONG", now=D(str(E + 301)), max_age=D("300"))
    assert old["stale"] is True


def test_the_tool_and_the_modules_it_loads_import_only_the_standard_library():
    """The judge runs it with a bare python: the tool and the three pure
    modules it imports at load time name no third-party package."""
    import ast
    files = [TOOL] + [ROOT / "backend" / "sportsassets" / f for f in (
        "bettor_paper_epoch.py", "bettor_paper_ledger.py",
        "bettor_book_snapshot.py", "__init__.py")]
    for f in files:
        tree = ast.parse(f.read_text())
        for node in tree.body:                     # module-level imports
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level:                     # relative: the package
                    continue
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            bad = {r for r in roots if r not in sys.stdlib_module_names
                   and r not in ("sportsassets", "__future__")}
            assert not bad, (f.name, bad)


def test_the_research_export_is_one_read_only_statement():
    import re
    body = re.sub(r"--[^\n]*", "", SQL.read_text())
    kw = re.compile(r"\b(insert|update|delete|drop|alter|truncate|grant|"
                    r"revoke|create|copy|vacuum|reindex|refresh|call|do|"
                    r"merge|lock)\b", re.I)
    assert not kw.search(body), kw.search(body)
    assert body.count(";") == 1


# ── CAPITAL-CRITICAL: the research SQL file on a real ledger ──────────

#: the five strategies migration 309's check admits, one per fixture market
STRATEGY = {"flat": "DEREK_ENTRY_POLICY_V2",
            "carry": "PINNACLE_ONLY_PAPER_BENCHMARK",
            "held": "PINNACLE_COMPLETED_GAME_PAPER",
            "won": "PINNACLE_COMPLETED_GAME_MAKER_PAPER",
            "open": "PINNACLE_EXPLORATION_PAPER"}


def _sql_for(acct: str, epoch: float) -> str:
    """The research file itself, its two constants replaced."""
    import datetime as dt
    src = SQL.read_text()
    iso = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()
    a = "timestamptz '2026-10-05 04:00:00+00' AS epoch"
    b = "'paper_acct_main'::text AS acct"
    assert src.count(a) == 1 and src.count(b) == 1
    return src.replace(a, "timestamptz '%s' AS epoch" % iso).replace(
        b, "'%s'::text AS acct" % acct)


@pg
def test_the_research_export_replays_to_the_epoch_reads_own_rollforward(
        tmp_path):
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    from tests import paper_harness as HA

    fee = HA.flat_fee(0.0175)                  # sub-cent remainders

    async def buy_or_sell(conn, acct, key, slug, *, d, qty, px, at,
                          group=None, book=None):
        o = HA.order(acct, key=key, slug=slug, direction=d, qty=qty,
                     limit=px, at=at, group_id=group,
                     role="ENTRY" if d == "BUY" else "EXIT")
        # one strategy per market: an entry is never refused by ANOTHER
        # fixture position's (synthetic, stale) management freshness
        o["strategy"] = STRATEGY[slug.rsplit("-", 1)[-1]]
        got = await L.submit_order(conn, o, fee_fn=fee, now=at)
        assert got["ok"], got
        await HA.observe(conn, slug, at + 3.0, **book)
        sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                       now=at + 4.0, fee_fn=fee)
        st = await conn.fetchval("SELECT state FROM paper_orders WHERE "
                                 "order_id=$1", got["order"]["order_id"])
        assert st == "FILLED", (st, sim)
        return o["group_id"]

    async def go():
        conn = await HA.connect()
        try:
            acct = await HA.new_account(conn, "rfwd", now=HA.T0)
            a = acct["account_id"]
            t = a[-8:]
            s = {k: "rfwd-%s-%s" % (t, k)
                 for k in ("flat", "carry", "held", "won", "open")}
            g = {}
            # BEFORE THE EPOCH (synthetic venue instants; the ledger's
            # commit instants are the real clock, as in production)
            g["flat"] = await buy_or_sell(
                conn, acct, "f1", s["flat"], d="BUY", qty=100.0, px=0.50,
                at=HA.T0, book={"offers": [(0.50, 100)], "bids": [(0.48, 100)]})
            await buy_or_sell(
                conn, acct, "f2", s["flat"], d="SELL", qty=100.0, px=0.55,
                at=HA.T0 + 10, group=g["flat"],
                book={"offers": [(0.57, 100)], "bids": [(0.55, 100)]})
            g["carry"] = await buy_or_sell(
                conn, acct, "c1", s["carry"], d="BUY", qty=100.0, px=0.40,
                at=HA.T0 + 20,
                book={"offers": [(0.40, 100)], "bids": [(0.38, 100)]})
            g["held"] = await buy_or_sell(
                conn, acct, "h1", s["held"], d="BUY", qty=50.0, px=0.30,
                at=HA.T0 + 30,
                book={"offers": [(0.30, 50)], "bids": [(0.28, 50)]})
            await asyncio.sleep(0.05)
            epoch = time.time()
            await asyncio.sleep(0.05)
            # the carried market's book 10 s before the epoch: verified
            await HA.observe(conn, s["carry"], epoch - 10.0,
                             bids=[(0.45, 100)], offers=[(0.47, 100)])
            # AFTER THE EPOCH (each trade's book is observed at at + 3 s:
            # after the epoch, before the fresh marks below)
            now0 = time.time()
            await buy_or_sell(
                conn, acct, "c2", s["carry"], d="SELL", qty=40.0, px=0.52,
                at=now0 - 3.0, group=g["carry"],
                book={"offers": [(0.54, 100)], "bids": [(0.52, 100)]})
            g["won"] = await buy_or_sell(
                conn, acct, "w1", s["won"], d="BUY", qty=30.0, px=0.33,
                at=now0 - 2.9,
                book={"offers": [(0.33, 30)], "bids": [(0.31, 30)]})
            g["open"] = await buy_or_sell(
                conn, acct, "o1", s["open"], d="BUY", qty=70.0, px=0.61,
                at=now0 - 2.8,
                book={"offers": [(0.61, 70)], "bids": [(0.59, 70)]})
            for k, outcome in (("held", "LOST"), ("won", "WON")):
                r = await L.settle(
                    conn, account_id=a, group_id=g[k], slug=s[k],
                    holding_side="LONG",
                    settlement_event_key="venue-final:%s" % s[k],
                    outcome=outcome,
                    evidence={"rows": [{"valuation_id": 1,
                                        "outcome_at": time.time()}]},
                    evidence_source="TEST_FIXTURE_SYNTHETIC",
                    at=time.time(), session_id=acct["session_id"])
                assert r["ok"], r
            # fresh books for the two positions still open, observed after
            # every trade's own book (each observed at its order's at + 3 s)
            await asyncio.sleep(0.5)
            now1 = time.time()
            assert now1 > now0 + 0.2
            await HA.observe(conn, s["carry"], now1,
                             bids=[(0.56, 100)], offers=[(0.58, 100)])
            await HA.observe(conn, s["open"], now1,
                             bids=[(0.64, 70)], offers=[(0.66, 70)])
            # THE RESEARCH FILE ITSELF, read only, on this ledger
            async with conn.transaction(readonly=True):
                lines = [r["line"] for r in await conn.fetch(
                    _sql_for(a, epoch))]
            now = time.time()
            bal = await L.balances(conn, a, now=now)
            book = await EP.read(conn, a, bal=bal, now=now, epoch_at=epoch,
                                 itemise=True)
            live = await EP.read(conn, a, bal=bal, now=now, epoch_at=epoch)
            return lines, book, a, epoch, live
        finally:
            await conn.close()

    lines, book, acct, epoch, live = asyncio.run(go())
    # the live read (the API payload) carries the same legs, not the per-
    # position itemisation (3 in scope: carried, won, open; the held-outside
    # one is a bridge line, the flat one history)
    assert live["rollforward"]["positions"] == {"count": 3,
                                                "itemised": False}
    for k in ("realized_pnl", "unrealized_pnl", "all_in_fees",
              "ending_capital", "verdict"):
        assert live["rollforward"][k] == book["rollforward"][k]
    export = tmp_path / "export.txt"
    export.write_text("\n".join(lines) + "\n")
    rec = T.receipt(T.parse(export.read_text()))
    rf_app = book["rollforward"]
    # the read model itself balances exactly, marks verified
    assert book["status"] == "OK", book["why"]
    assert rf_app["balanced"] is True
    assert rf_app["verdict"] == EP.RF_EXACT_HELD_OUTSIDE, rf_app
    # THE RECEIPT FROM THE RESEARCH EXPORT == THE READ'S OWN ROLL-FORWARD,
    # to the micro-dollar, leg by leg
    rf = rec["rollforward"]
    for k in ("opening_capital", "net_cash_flows", "realized_pnl",
              "unrealized_pnl", "all_in_fees", "ending_capital", "gap"):
        assert D(rf[k]) == D(rf_app[k]), (k, rf[k], rf_app[k])
    assert rf["bridge_to_ledger"] == rf_app["bridge_to_ledger"]
    assert rec["positions"] == rf_app["positions"]
    assert rec["verified"] is True, rec["verdict"]
    assert rec["verdict"] == EP.RF_EXACT_HELD_OUTSIDE
    assert rec["ledger_replay"]["ok"] and rec["costs"]["ok"]
    assert rec["ledger_replay"]["funding_after_the_epoch"] == 0
    # the fixture's own arithmetic, by hand (fees 0.0175 per contract):
    #   carried 100 @ epoch mark 0.45 = 45.00; sold 40 @ 0.52 = 20.80 vs
    #   cost 18.00 -> +2.80; open 60 at 0.56 = 33.60 vs 27.00 -> +6.60
    #   won: bought 30 @ 0.33 = 9.90, settled 30.00 -> +20.10
    #   open: bought 70 @ 0.61 = 42.70, marked 0.64 = 44.80 -> +2.10
    #   fees after the epoch: (40 + 30 + 70) x 0.0175 = 2.45
    assert D(rf["realized_pnl"]) == D("22.90")
    assert D(rf["unrealized_pnl"]) == D("8.70")
    assert D(rf["all_in_fees"]) == D("2.45")
    assert D(rf["ending_capital"]) == D("500029.15")
    c = rec["contract"]
    assert c == {"opening_capital": "500000.00", "net_cash_flows": "0.00",
                 "realized_pnl": "22.90", "unrealized_pnl": "8.70",
                 "all_in_fees": "2.45", "ending_capital": "500029.15",
                 "cents_rounding_residual": "0.00",
                 "reconciled_to_append_only_ledger": True,
                 "open_position_marks_verified": True,
                 "all_costs_reconciled": True}
    # the held-outside position is itemised in the bridge, never forced
    items = {i["item"]: i for i in rf["bridge_to_ledger"]["items"]}
    assert D(items[EP.I_UNVERIFIED]["amount"]) == D("-15.875")
    # AND THE TOOL RUNS AS THE JUDGE RUNS IT: stdlib python, isolated mode
    out = subprocess.run([sys.executable, "-I", str(TOOL), str(export)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    cli = json.loads(out.stdout)
    assert cli["contract"] == c and cli["export_sha256"] == rec[
        "export_sha256"]
