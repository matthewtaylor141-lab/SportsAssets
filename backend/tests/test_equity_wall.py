"""THE LIVE EQUITY WALL: /api/command/equity/live and /equity/curve.

  §1 FIXTURES (pure): a paper book with marked + UNMARKED positions; the
     Polymarket US small-live account STOPPED with $1,000 and no position;
     Kalshi UNAVAILABLE with the exact reason; marks that stopped -> STALE;
     the two books (and the two venues) are never summed -- no key combines
     them; the ETag ignores the clock and changes with the content.
  §2 THE CURVE (pure): only changes are kept; thinning keeps the ends.
  §3 DATABASE: the endpoint over a real paper ledger, a stopped mirror
     snapshot and an empty Kalshi lane; If-None-Match -> 304; the curve
     keeps only changed snapshots with their causes.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import json
import time

import pytest
from starlette.requests import Request
from starlette.responses import Response

from sportsassets import bettor_paper_ledger as L
from sportsassets.api import command_equity as E

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
NOW = 1_791_100_000.0          # a fixed clock for the pure fixtures


# ── fixtures ─────────────────────────────────────────────────────────

def _pos(key, *, qty, cost, price=None, observed_at=None, stale=False,
         why=None):
    mark = ({"status": "STALE" if stale else "OK", "price": price,
             "source": "paper_book_observations:%s" % key,
             "observed_at": observed_at, "stale": stale}
            if price is not None else
            {"status": "UNAVAILABLE", "price": None, "why": why or "NO_EXIT_SIDE"})
    mv = None if price is None else round(qty * price, 6)
    return {"position_key": "paperpos:t:%s" % key, "us_market_slug": key,
            "holding_side": "LONG", "strategy": "S", "open_qty": qty,
            "cost_basis_usd": cost, "mark": mark,
            "marked_value_usd": mv,
            "unrealized_pnl_usd": None if mv is None else round(mv - cost, 6)}


def paper_balances(positions, *, cash=499000.0, realized=-12.5):
    return {"ok": True, "cash_usd": cash, "available_usd": cash,
            "reserved_usd": 0.0, "starting_cash_usd": 500000.0,
            "realized_pnl_usd": realized, "fees_paid_usd": 1.25,
            "open_positions": positions, "last_updated_at": NOW - 40,
            "last_sequence": 77, "data_label": L.DATA_LABEL,
            "mark_method": L.MARK_METHOD}


RUNNING = {"active": True, "session_id": "paper_session_x",
           "started_at": NOW - 86400, "heartbeat_at": NOW - 30}


def stopped_ctl(**kw):
    ctl = {"enabled": True, "stopped": True, "scale": 1000,
           "max_order_usd": 25, "stop_done_at": NOW - 26 * 3600,
           "cutover_at": NOW - 27 * 3600, "updated_at": NOW - 26 * 3600,
           "account_fingerprint": True,
           "baseline": {"at": NOW - 27 * 3600, "positions_net": {},
                        "balances": [{"currentBalance": 1000,
                                      "buyingPower": 1000}]}}
    ctl.update(kw)
    return ctl


def snap(at, *, balance=1000, positions=None, open_orders=0):
    return {"at": at,
            "balances": json.dumps([{"currency": "USD",
                                     "currentBalance": balance,
                                     "buyingPower": balance,
                                     "assetNotional": 0,
                                     "balanceReservation": balance}]),
            "positions": json.dumps(positions or []),
            "open_orders": open_orders,
            "reconciliation": json.dumps({"reconciled": True,
                                          "differences": {}})}


# ── §1 fixtures ──────────────────────────────────────────────────────

def test_paper_marked_and_unmarked_positions_are_explicit():
    bal = paper_balances([
        _pos("m1", qty=1000, cost=500.0, price=0.55, observed_at=NOW - 20),
        _pos("u1", qty=400, cost=160.0)])
    got = E.paper_account(bal, now=NOW, session=RUNNING,
                          day_basis={"equity_usd": 499600.0, "at": NOW - 9000,
                                     "basis": "t"})
    assert got["status"] == "OK" and got["book"] == "PAPER"
    # equity = cash + marked value + UNMARKED at cost, said explicitly
    assert got["equity_usd"] == 499000.0 + 550.0 + 160.0
    t = got["equity_treatment"]
    assert t["rule"] == "UNMARKED_CARRIED_AT_COST"
    assert t["unmarked_positions"] == 1
    assert t["unmarked_carried_at_cost_usd"] == 160.0
    assert got["equity_marked_only_usd"] == 499550.0
    # unrealized ONLY from the real mark
    assert got["unrealized_pnl_usd"] == 50.0
    assert got["realized_pnl_usd"] == -12.5
    op = got["open_positions"]
    assert (op["count"], op["marked"], op["unmarked"]) == (2, 1, 1)
    states = {r["market"]: r for r in op["rows"]}
    assert states["u1"]["mark_state"] == "UNMARKED"
    assert states["u1"]["unrealized_pnl_usd"] is None
    assert states["u1"]["marked_value_usd"] is None
    assert states["m1"]["mark_state"] == "MARKED"
    assert got["exposure"] == {"cost_basis_usd": 660.0,
                               "marked_value_usd": 550.0,
                               "unmarked_cost_basis_usd": 160.0}
    assert got["day_change"]["usd"] == round(499710.0 - 499600.0, 2)
    assert got["since_inception"]["usd"] == -290.0
    assert got["marks_as_of"]["newest_age_s"] == 20.0


def test_paper_marks_that_stopped_refreshing_are_stale():
    bal = paper_balances([
        _pos("m1", qty=1000, cost=500.0, price=0.55, observed_at=NOW - 900,
             stale=True),
        _pos("m2", qty=100, cost=40.0, price=0.41, observed_at=NOW - 1200,
             stale=True)])
    got = E.paper_account(bal, now=NOW, session=RUNNING)
    assert got["status"] == "STALE"
    assert "no position mark has refreshed for 15m" in got["why"]
    assert got["open_positions"]["stale_marks"] == 2
    assert {r["mark_state"] for r in got["open_positions"]["rows"]} == {
        "STALE_MARK"}
    assert got["marks_as_of"]["oldest_age_s"] == 1200.0
    # a stopped runtime is STALE even with no position at all
    dead = E.paper_account(paper_balances([]), now=NOW,
                           session=dict(RUNNING, heartbeat_at=NOW - 3600))
    assert dead["status"] == "STALE" and "heartbeat" in dead["why"]
    assert dead["lane"]["state"] == "NO_HEARTBEAT"
    assert got["lane"]["state"] == "RUNNING"
    assert dead["equity_usd"] == 499000.0


def test_paper_unreadable_is_unavailable_never_zero():
    got = E.paper_account(None, now=NOW, error="PAPER_READ_FAILED: X")
    assert got["status"] == "UNAVAILABLE"
    for k in ("equity_usd", "cash_usd", "realized_pnl_usd",
              "unrealized_pnl_usd"):
        assert got[k] is None, k


def test_polymarket_stopped_with_1000_and_no_position():
    got = E.pm_account(stopped_ctl(), snap(NOW - 26 * 3600), now=NOW,
                       fills=0)
    assert got["venue"] == "polymarket_us" and got["book"] == "ACTUAL"
    assert got["lane"]["state"] == "STOPPED"
    assert got["lane"]["scale"] == 1000 and got["lane"]["cap_usd_per_order"] == 25
    assert got["status"] == "STALE"
    assert "STOPPED" in got["why"] and "26.0h" in got["why"]
    assert got["equity_usd"] == 1000.0 and got["cash_usd"] == 1000.0
    assert got["available_usd"] == 1000.0
    assert got["open_positions"]["count"] == 0
    assert got["realized_pnl_usd"] == 0.0
    assert "execmirror_fills is empty" in got["realized_basis"]
    assert got["unrealized_pnl_usd"] == 0.0
    assert got["session_change"]["usd"] == 0.0
    assert got["day_change"]["usd"] is None and got["day_change"]["why"]
    assert got["resting_orders"] == 0


def test_polymarket_running_marks_positions_from_the_venue_snapshot():
    pos = [{"slug": "a", "netPosition": 3, "cost": 1.5, "cashValue": 1.8,
            "realized": 0.25},
           {"slug": "b", "netPosition": 2, "cost": {"value": "0.90"}},
           {"slug": "c", "netPosition": 0, "cost": 0, "realized": -0.1,
            "expired": True}]
    ctl = stopped_ctl(stopped=False)
    got = E.pm_account(ctl, snap(NOW - 30, balance=997.6, positions=pos),
                       now=NOW, fills=4,
                       day_snap=snap(NOW - 50000, balance=1000))
    assert got["status"] == "OK" and got["lane"]["state"] == "ENABLED"
    assert got["equity_usd"] == round(997.6 + 1.8 + 0.9, 2)
    assert got["unrealized_pnl_usd"] == 0.3
    assert got["realized_pnl_usd"] == 0.15
    assert got["equity_treatment"]["rule"] == "UNMARKED_CARRIED_AT_COST"
    assert got["open_positions"]["unmarked"] == 1
    assert got["day_change"]["usd"] == round(got["equity_usd"] - 1000.0, 2)
    fresh_stale = E.pm_account(ctl, snap(NOW - 600), now=NOW, fills=0)
    assert fresh_stale["status"] == "STALE"
    assert "ENABLED" in fresh_stale["why"]


def test_kalshi_without_an_account_is_unavailable_with_the_reason():
    got = E.kalshi_account({"enabled": False, "stopped": False,
                            "key_fingerprint": False, "scale": 1000,
                            "max_order_usd": 25}, None, now=NOW,
                           credential_present=False, fills=0)
    assert got["status"] == "UNAVAILABLE" and got["venue"] == "kalshi"
    assert got["equity_usd"] is None and got["cash_usd"] is None
    assert got["why"].startswith("NO_KALSHI_ACCOUNT")
    assert "holds no key" in got["why"]
    assert "KALSHI_API_KEY_ID is not configured" in got["why"]
    assert got["lane"]["state"] == "NOT_CONNECTED"
    nomig = E.kalshi_account(None, None, now=NOW, schema=False)
    assert nomig["status"] == "UNAVAILABLE"
    assert "MIGRATION_196" in nomig["why"]
    assert E.kalshi_credential_in_this_service({}) is False
    assert E.kalshi_credential_in_this_service(
        {"KALSHI_API_KEY_ID": "k", "KALSHI_PRIVATE_KEY_PATH": "/p"}) is True


def test_kalshi_with_a_reconciliation_is_shown_unmarked_at_cost():
    recon = {"at": NOW - 60, "verdict": "NOT_EMPTY", "complete": True,
             "balance_usd": 40.0, "kalshi_env": "prod",
             "positions": json.dumps([{"ticker": "KX", "position": "2",
                                       "exposure_usd": "1.10"}]),
             "resting_orders": "[]"}
    got = E.kalshi_account({"enabled": False, "key_fingerprint": True},
                           recon, now=NOW)
    assert got["status"] == "OK" and got["equity_usd"] == 41.1
    assert got["unrealized_pnl_usd"] is None
    assert got["equity_treatment"]["rule"] == "UNMARKED_CARRIED_AT_COST"
    bad = E.kalshi_account({"key_fingerprint": True},
                           dict(recon, verdict="UNREADABLE", complete=False,
                                errors='{"balance": "http_401"}'), now=NOW)
    assert bad["status"] == "UNAVAILABLE" and "http_401" in bad["why"]


def _all_keys(v, out=None):
    out = set() if out is None else out
    if isinstance(v, dict):
        for k, x in v.items():
            out.add(k)
            _all_keys(x, out)
    elif isinstance(v, list):
        for x in v:
            _all_keys(x, out)
    return out


def _payload():
    paper = E.paper_account(paper_balances([
        _pos("m1", qty=1000, cost=500.0, price=0.55, observed_at=NOW - 20)]),
        now=NOW, session=RUNNING)
    pm = E.pm_account(stopped_ctl(), snap(NOW - 26 * 3600), now=NOW, fills=0)
    k = E.kalshi_account({"key_fingerprint": False}, None, now=NOW)
    return E.envelope(paper, pm, k)


def test_paper_and_actual_are_never_summed_and_venues_never_combined():
    p = _payload()
    assert p["books_summed"] is False and p["venues_summed"] is False
    assert p["actual"]["venues_summed"] is False
    assert set(p) >= {"paper", "actual"}
    assert set(p["actual"]["venues"]) == {"polymarket_us", "kalshi"}
    # nothing at the top or at the actual level carries money
    for level in (p, p["actual"]):
        for k, v in level.items():
            assert not (k.endswith("_usd") or k in ("equity", "cash")), k
    keys = _all_keys(p)
    for bad in ("total", "combined", "aggregate", "sum_", "grand",
                "paper_plus", "all_books", "net_worth"):
        assert not any(bad in k.lower() for k in keys), bad
    assert p["paper"]["book"] == "PAPER"
    assert {v["book"] for v in p["actual"]["venues"].values()} == {"ACTUAL"}
    # the actual block never holds the paper equity and vice versa
    assert p["paper"]["equity_usd"] != p["actual"]["venues"][
        "polymarket_us"]["equity_usd"]


def test_the_etag_ignores_the_clock_and_follows_the_content():
    a = E.stamp(_payload(), now=NOW)
    b = E.stamp(_payload(), now=NOW + 1)
    assert a["etag"] == b["etag"] and a["seq"] == b["seq"]
    later = _payload()
    later["paper"]["marks_as_of"]["newest_age_s"] = 999.0
    assert E.stamp(later, now=NOW + 2)["etag"] == a["etag"]
    moved = _payload()
    moved["paper"]["equity_usd"] = 499551.0
    c = E.stamp(moved, now=NOW + 3)
    assert c["etag"] != a["etag"] and c["seq"] == a["seq"] + 1
    flipped = _payload()
    flipped["paper"]["status"] = "STALE"
    assert E.content_etag(flipped) != E.content_etag(_payload())


def test_the_ny_session_day_handles_daylight_saving():
    # 2026-10-04 05:00Z is 01:00 EDT -> day starts 04:00Z
    assert E.iso(E.ny_day_start(1791090000.0)) == "2026-10-04T04:00:00Z"
    # 2026-12-01 12:00Z is 07:00 EST -> day starts 05:00Z
    assert E.iso(E.ny_day_start(1796126400.0)) == "2026-12-01T05:00:00Z"


# ── §2 the curve ─────────────────────────────────────────────────────

def test_the_curve_keeps_only_genuine_changes():
    pts = E.keep_changes([(1, 100.0, "A"), (2, 100.0, "MARK"),
                          (3, 100.004, "MARK"), (4, 101.0, "LEDGER"),
                          (5, None, "MARK"), (6, None, "MARK"),
                          (7, 101.0, "MARK")])
    assert [(p["t"], p["v"], p["cause"]) for p in pts] == [
        (1, 100.0, "A"), (4, 101.0, "LEDGER"), (5, None, "GAP"),
        (7, 101.0, "MARK")]
    many = [{"t": float(i), "v": float(i % 7), "cause": "MARK"}
            for i in range(5000)]
    thin, did = E.thin(many, 100)
    assert did and len(thin) <= 100
    assert thin[0] == many[0] and thin[-1] == many[-1]
    same, did2 = E.thin(many[:10], 100)
    assert same == many[:10] and did2 is False


# ── §3 database ──────────────────────────────────────────────────────

def _request(etag=None):
    headers = [(b"if-none-match", etag.encode())] if etag else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers":
                    headers, "query_string": b""})


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


async def _seed_paper(conn, now):
    from sportsassets import bettor_paper_session as S
    from sportsassets import bettor_paper_simulator as SIM
    a = await H.new_account(conn, "eqw", now=now - 7200)
    acct = a["account_id"]

    async def buy(key, slug, qty, limit, bids):
        o = H.order(a, key=key, qty=qty, limit=limit, slug=slug,
                    at=now - 600, group_id="paper_g_%s" % key)
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=now - 600)
        assert got["ok"], got
        await H.observe(conn, slug, now - 597, offers=[(limit, qty)],
                        bids=bids)
        await SIM.simulate_order(conn, got["order"]["order_id"],
                                 now=now - 596, fee_fn=H.zero_fee)

    await buy("mk", acct + ":marked", 1000, 0.50, [(0.48, 500)])
    await buy("um", acct + ":unmarked", 200, 0.30, [])
    # the marked market moves; a genuine new observation
    await H.observe(conn, acct + ":marked", now - 20, bids=[(0.56, 500)],
                    offers=[(0.58, 500)])
    await S.record_pass(conn, a["session_id"], result={"ok": True},
                        now=now - 15)
    return a


@pg
async def test_the_live_endpoint_over_real_records(monkeypatch):
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        a = await _seed_paper(conn, now)
        acct = a["account_id"]
        # snapshots: two identical, one changed (a mark), one ledger move
        for i, (eq, seq) in enumerate(((499960.0, 3), (499960.0, 3),
                                       (499980.0, 3), (499990.0, 4))):
            await conn.execute(
                "INSERT INTO paper_equity_snapshots (session_id, account_id,"
                " at, cash_usd, reserved_usd, marked_value_usd, equity_usd,"
                " equity_excluding_unmarked_usd, unmarked_positions,"
                " realized_pnl_usd, unrealized_pnl_usd, last_sequence)"
                " VALUES ($1,$2,to_timestamp($3),0,0,0,$4,$4,0,0,0,$5)",
                a["session_id"], acct, now - 3000 + i * 60, eq, seq)
        # the Polymarket US mirror: STOPPED, $1,000, no position, 26 h ago
        await conn.execute(
            "UPDATE execmirror_control SET enabled = true, stopped = true, "
            " stop_done_at = to_timestamp($1), cutover_at = to_timestamp($2),"
            " baseline = $3::jsonb WHERE id = 1", now - 26 * 3600,
            now - 27 * 3600, json.dumps({"positions_net": {}, "balances": [
                {"currentBalance": 1000, "buyingPower": 1000}]}))
        # the scenario has no venue fill since the cutover; rows another
        # test left behind would (correctly) make realized P&L unknown
        await conn.execute("DELETE FROM execmirror_fills")
        await conn.execute("DELETE FROM execmirror_snapshots")
        s = snap(now - 26 * 3600)
        await conn.execute(
            "INSERT INTO execmirror_snapshots (at, balances, positions, "
            " open_orders, reconciliation) VALUES (to_timestamp($1), "
            " $2::jsonb, $3::jsonb, $4, $5::jsonb)", s["at"], s["balances"],
            s["positions"], s["open_orders"], s["reconciliation"])
        # Kalshi: no account read at all
        await conn.execute("UPDATE kalshi_smalllive_control SET enabled = "
                           "false, reconciliation_id = NULL, key_fingerprint"
                           " = NULL WHERE id = 1")
        await conn.execute("DELETE FROM kalshi_account_reconciliations")

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(E, "_pool", pool)
        async def selected_account(conn):
            return acct
        monkeypatch.setattr(L, "selected_account", selected_account)
        E._LIVE.update(at=0.0, payload=None)
        E._CURVES.clear()

        resp = Response()
        got = await E.equity_live(_request(), resp)
        assert resp.headers["ETag"] == got["etag"]
        assert resp.headers["Cache-Control"] == "private, no-cache"
        paper = got["paper"]
        assert paper["status"] == "OK", paper["why"]
        assert paper["open_positions"]["count"] == 2
        assert paper["open_positions"]["unmarked"] == 1
        assert paper["equity_treatment"]["rule"] == "UNMARKED_CARRIED_AT_COST"
        assert paper["equity_treatment"]["unmarked_carried_at_cost_usd"] == 60.0
        assert paper["cash_usd"] == 500000.0 - 500.0 - 60.0
        assert paper["equity_usd"] == paper["cash_usd"] + 560.0 + 60.0
        assert paper["unrealized_pnl_usd"] == 60.0
        assert paper["session_change"]["basis_equity_usd"] == 499960.0
        pm = got["actual"]["venues"]["polymarket_us"]
        assert pm["status"] == "STALE" and pm["lane"]["state"] == "STOPPED"
        assert pm["equity_usd"] == 1000.0
        assert pm["realized_pnl_usd"] == 0.0
        k = got["actual"]["venues"]["kalshi"]
        assert k["status"] == "UNAVAILABLE" and k["equity_usd"] is None
        assert k["why"].startswith("NO_KALSHI_ACCOUNT")
        assert got["books_summed"] is False

        # the cheap poll: same content -> 304, no body
        E._LIVE.update(at=0.0, payload=None)
        again = await E.equity_live(_request(got["etag"]), Response())
        assert again.status_code == 304
        assert again.headers["ETag"] == got["etag"]

        # the curve: only changed snapshots, each with its cause
        cv = await E.equity_curve(_request(), Response(), book="PAPER",
                                  venue=None, window="1d")
        assert cv["status"] == "OK" and cv["interpolated"] is False
        assert [(p["v"], p["cause"]) for p in cv["points"]] == [
            (499960.0, "FIRST_RECORD"), (499980.0, "MARK"),
            (499990.0, "LEDGER")]
        assert cv["scanned"] == 4
        pmc = await E.equity_curve(_request(), Response(), book="ACTUAL",
                                   venue="polymarket_us", window="30d")
        assert [p["v"] for p in pmc["points"]] == [1000.0]
        kc = await E.equity_curve(_request(), Response(), book="ACTUAL",
                                  venue="kalshi", window="7d")
        assert kc["status"] == "UNAVAILABLE" and kc["points"] == []
    finally:
        await tr.rollback()
        await conn.close()
        E._LIVE.update(at=0.0, payload=None)
        E._CURVES.clear()
