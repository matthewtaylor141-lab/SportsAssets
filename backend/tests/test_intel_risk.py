"""CAPITAL-CRITICAL (SHADOW): THE PORTFOLIO RISK ENGINE, PAPER AND ACTUAL
SEPARATELY, AND AUDREY'S INDEPENDENT RECOMPUTE.

  §1 positions, exposure by dimension, correlation clusters,
     liquidity-at-risk (depth walked, unabsorbed quantity reported), daily
     loss and drawdown -- hand-computed
  §2 unmeasurable -> null with a reason: no book, no equity snapshot,
     no exposure (HHI)
  §3 paper and actual are separate reports, never summed
  §4 Audrey's SQL path agrees with the Python path on synthetic books; a
     disagreement writes intel_audrey_risk_checks AND an Audrey finding;
     with no active session the reason the finding was not filed is kept
"""
from __future__ import annotations

import json
import time

import asyncpg
import pytest

from sportsassets.agents import audrey_intel_risk as AR
from sportsassets.intel import common as C
from sportsassets.intel import risk as RK

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
NOW = 2_000_000_000.0


def _fill(g, slug, direction, qty, price, fee=0.0, at=0.0, side="LONG",
          ek=None):
    return {"group_id": g, "us_market_slug": slug, "holding_side": side,
            "fixture": "fx-" + slug, "label": {"event_key": ek or slug},
            "strategy": "S", "direction": direction, "qty": qty,
            "gross_usd": qty * price, "fee_usd": fee, "filled_at": at}


def _pos(g, game, team, sport="baseball", exposure=10.0, slug=None):
    return {"group_id": g, "game": game, "team": team, "sport": sport,
            "exposure_usd": exposure, "us_market_slug": slug or g,
            "holding_side": "LONG", "open_qty": 10.0}


# ── §1 the arithmetic ────────────────────────────────────────────────

def test_positions_net_sales_and_settlements_at_average_cost():
    fills = [_fill("g1", "m1", "BUY", 100, 0.50, fee=1.0, at=1),
             _fill("g1", "m1", "SELL", 30, 0.60, at=2),
             _fill("g2", "m2", "BUY", 50, 0.40, at=3)]
    setts = {"paperpos:A:g2:m2:LONG": {"qty": 50}}
    pos = {p["group_id"]: p for p in RK.aggregate_paper(fills, setts,
                                                        account_id="A")}
    assert pos["g1"]["open_qty"] == pytest.approx(70)
    assert pos["g1"]["avg_cost"] == pytest.approx(0.51)
    assert pos["g2"]["open_qty"] == pytest.approx(0)
    p = RK.classify(pos["g1"], now=NOW, pm={"team_name": "Cubs",
                                            "sports_type": "baseball",
                                            "game_start": NOW - 60},
                    val={"market": "h2h"})
    assert p["exposure_usd"] == pytest.approx(35.7)
    assert p["live_state"] == "LIVE" and p["team"] == "Cubs"
    assert p["conference"] == "UNKNOWN" and p["settlement_class"] == "UNKNOWN"


def test_clusters_join_shared_games_and_shared_teams():
    ops = [_pos("a", "G1", "T1"), _pos("b", "G1", "T2"),
           _pos("c", "G2", "T1"), _pos("d", "G3", "T3", exposure=5.0)]
    cl = RK.clusters(ops)
    assert [c["positions"] for c in cl] == [3, 1]
    assert cl[0]["exposure_usd"] == pytest.approx(30.0)
    assert cl[0]["games"] == ["G1", "G2"]


def test_liquidity_at_risk_walks_the_book_for_each_side():
    book = C.book_view([{"px": {"value": "0.50"}, "qty": "60"},
                        {"px": {"value": "0.45"}, "qty": "100"}],
                       [{"px": {"value": "0.54"}, "qty": "60"}])
    long_ = dict(_pos("L", "G", "T", slug="m"), open_qty=100.0)
    short = dict(_pos("S", "G", "T", slug="m"), open_qty=100.0,
                 holding_side="SHORT")
    lar = RK.liquidity_at_risk([long_], {"m": book})
    assert lar["liquidity_at_risk_usd"] == pytest.approx(100 * 0.52 - 48.0)
    s = RK.liquidity_at_risk([short], {"m": book})
    assert s["liquidity_at_risk_usd"] == pytest.approx(48.0 - 60 * 0.46)
    assert s["unabsorbed_qty"] == pytest.approx(40.0)
    assert s["complete"] is True


def test_drawdown_and_daily_loss_from_the_equity_series():
    ds = C.day_start(NOW)
    eq = RK.equity_risk([(ds - 7200, 501000.0), (ds + 60, 499000.0),
                         (ds + 120, 500500.0)], now=ds + 200,
                        start_peak=500000.0)
    assert eq["peak_equity_usd"] == pytest.approx(501000.0)
    assert eq["max_drawdown_usd"] == pytest.approx(2000.0)
    assert eq["current_drawdown_usd"] == pytest.approx(500.0)
    assert eq["current_drawdown_pct"] == pytest.approx(500 / 501000 * 100,
                                                       abs=1e-6)
    assert eq["daily_pnl_usd"] == pytest.approx(-500.0)
    assert eq["daily_loss_usd"] == pytest.approx(500.0)


# ── §2 nulls ─────────────────────────────────────────────────────────

def test_unmeasurable_risk_is_null_with_a_reason():
    lar = RK.liquidity_at_risk([_pos("a", "G", "T")], {})
    assert lar["liquidity_at_risk_usd"] is None
    assert lar["unmeasured"]["liquidity_at_risk_usd"] == (
        "NO_OPEN_POSITION_HAS_A_READABLE_BOOK")
    none = RK.liquidity_at_risk([], {})
    assert none["liquidity_at_risk_usd"] == 0.0
    assert none["basis"] == "NO_OPEN_POSITIONS"
    eq = RK.equity_risk([], now=NOW)
    for k in ("current_drawdown_usd", "daily_loss_usd", "max_drawdown_usd"):
        assert eq[k] is None and eq["unmeasured"][k]
    eq = RK.equity_risk([(NOW - 10 * 86400, 1.0)], now=NOW)
    assert eq["daily_loss_usd"] is None
    assert eq["unmeasured"]["daily_loss_usd"] == "NO_EQUITY_SNAPSHOT_TODAY"
    rep = RK.report([], book="PAPER", now=NOW, books={}, equity={})
    assert rep["game_concentration_hhi"] is None
    assert rep["unmeasured"]["game_concentration_hhi"] == "NO_OPEN_EXPOSURE"


# ── §3 separate books ────────────────────────────────────────────────

def test_each_book_is_its_own_report_and_never_summed():
    fills = [_fill("g1", "m1", "BUY", 100, 0.5, at=1)]
    pos = [RK.classify(p, now=NOW, pm=None, val=None)
           for p in RK.aggregate_paper(fills, {}, account_id="A")]
    paper = RK.report(pos, book="PAPER", now=NOW, books={}, equity={})
    act = RK.aggregate_actual([{"venue": "POLYMARKET_US", "group_id": "g9",
                                "us_market_slug": "m9",
                                "intent": "ORDER_INTENT_BUY_SHORT", "qty": 2,
                                "price": 0.7, "fee_usd": 0.0}], set())
    act = [RK.classify(p, now=NOW, pm=None, val=None) for p in act]
    actual = RK.report(act, book="ACTUAL", now=NOW, books={}, equity={})
    assert paper["book"] == "PAPER" and actual["book"] == "ACTUAL"
    assert paper["summed_with_other_book"] is False
    assert actual["summed_with_other_book"] is False
    assert paper["gross_exposure_usd"] == pytest.approx(50.0)
    # a BUY_SHORT at LONG-side wire 0.70 costs 0.30 per contract
    assert actual["gross_exposure_usd"] == pytest.approx(0.6)
    assert actual["label"] == paper["label"] == "SHADOW"
    settled = RK.aggregate_actual([{"venue": "POLYMARKET_US",
                                    "group_id": "g9", "us_market_slug": "m9",
                                    "intent": "ORDER_INTENT_BUY_LONG",
                                    "qty": 2, "price": 0.7}], {"m9"})
    assert settled[0]["open_qty"] == 0


def test_the_comparison_tolerances():
    rows = AR.compare({"open_positions": 2, "distinct_games": 1,
                       "gross_exposure_usd": 100.0,
                       "max_game_exposure_usd": 100.0},
                      {"open_positions": 2, "distinct_games": 1,
                       "gross_exposure_usd": 100.005,
                       "max_game_exposure_usd": 100.5})
    by = {r["metric"]: r for r in rows}
    assert by["gross_exposure_usd"]["agrees"] is True
    assert by["max_game_exposure_usd"]["agrees"] is False
    assert by["open_positions"]["tolerance"] == 0.0
    un = AR.compare({"open_positions": None}, {"open_positions": 1})
    assert un[0]["agrees"] is None and un[0]["why"] == "PRIMARY_UNMEASURED"


# ── §4 the reads and Audrey ──────────────────────────────────────────

async def _book(conn, now):
    acct = await H.new_account(conn, "intelrisk", now=now - 86400)
    slug_a, slug_b = F.uid("intel-risk-a-"), F.uid("intel-risk-b-")
    g1 = await F.position(conn, acct, slug=slug_a, qty=100, price=0.50,
                          fee=1.0, at=now - 3600, event_key="GAME1")
    g2 = await F.position(conn, acct, slug=slug_b, qty=200, price=0.40,
                          at=now - 3500, event_key="GAME1")
    g3 = await F.position(conn, acct, slug=F.uid("intel-risk-c-"), qty=10,
                          price=0.30, at=now - 3400, event_key="GAME2")
    xo = await F.order(conn, acct, group_id=g2, slug=slug_b, role="EXIT",
                       direction="SELL", qty=50, price=0.45, at=now - 1000)
    await F.fill(conn, acct, order_id=xo, group_id=g2, slug=slug_b,
                 role="EXIT", direction="SELL", qty=50, price=0.45,
                 at=now - 998, event_key="GAME1")
    await F.book(conn, slug_a, now - 30,
                 bids=((0.48, 50), (0.40, 100)), offers=((0.52, 100),))
    await F.equity(conn, acct, at=now - 600, equity_usd=500100.0)
    await F.equity(conn, acct, at=now - 60, equity_usd=499900.0)
    return acct, (g1, g2, g3)


@pg
async def test_the_paper_report_and_audreys_recompute_agree():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, (g1, g2, g3) = await _book(conn, now)
        rep = await RK.paper_report(conn, now=now,
                                    account_id=acct["account_id"])
        assert rep["open_positions"] == 3
        expect = 100 * 0.51 + 150 * 0.40 + 10 * 0.30
        assert rep["gross_exposure_usd"] == pytest.approx(expect)
        assert rep["max_game_exposure_usd"] == pytest.approx(51.0 + 60.0)
        assert rep["distinct_games"] == 2
        assert rep["liquidity"]["measured_positions"] == 1
        assert rep["liquidity"]["unmeasured_positions"] == 2
        assert rep["liquidity"]["complete"] is False
        # 100 LONG at mid 0.50: 50 x 0.48 + 50 x 0.40 = 44 -> LaR 6
        assert rep["liquidity"]["liquidity_at_risk_usd"] == pytest.approx(6.0)
        assert rep["equity"]["max_drawdown_usd"] == pytest.approx(200.0)
        assert rep["clusters"][0]["positions"] == 2
        got = await AR.check(conn, run_id="intelrun:test", book="PAPER",
                             primary=rep, now=now,
                             account_id=acct["account_id"])
        assert got["agrees"] is True, got
        assert got["finding_id"] is None
        n = await conn.fetchval(
            "SELECT count(*) FROM intel_audrey_risk_checks WHERE run_id="
            "'intelrun:test' AND book='PAPER' AND agrees")
        assert n == len(AR.METRICS)
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_a_disagreement_files_an_audrey_finding():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, _ = await _book(conn, now)
        rep = await RK.paper_report(conn, now=now,
                                    account_id=acct["account_id"])
        wrong = dict(rep, gross_exposure_usd=rep["gross_exposure_usd"] + 5)
        got = await AR.check(conn, run_id="intelrun:bad", book="PAPER",
                             primary=wrong, now=now,
                             account_id=acct["account_id"])
        assert got["agrees"] is False and got["finding_id"]
        f = await conn.fetchrow(
            "SELECT kind, severity, detail FROM paper_audrey_findings "
            " WHERE finding_id=$1", got["finding_id"])
        assert f["kind"] == AR.FINDING_KIND and f["severity"] == "WARNING"
        detail = json.loads(f["detail"])
        assert detail["label"] == "SHADOW"
        assert detail["disagreements"][0]["metric"] == "gross_exposure_usd"
        row = await conn.fetchrow(
            "SELECT agrees, finding_id FROM intel_audrey_risk_checks WHERE "
            " run_id='intelrun:bad' AND metric='gross_exposure_usd'")
        assert row["agrees"] is False
        assert row["finding_id"] == got["finding_id"]
        # no active session: the check is kept, with why no finding
        got = await AR.check(conn, run_id="intelrun:nosess", book="PAPER",
                             primary={"open_positions": 9}, now=now,
                             account_id="paper_test_no_session_x")
        assert got["agrees"] is False and got["finding_id"] is None
        assert got["finding_unwritten_reason"] == (
            "NO_ACTIVE_PAPER_SESSION_TO_FILE_THE_FINDING_UNDER")
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_actual_report_and_recompute_agree_on_venue_fills():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        g, slug, mid = F.uid("grp-"), F.uid("intel-act-"), F.uid("exm-")
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, group_id, role, "
            " us_market_slug, intent, order_type, tif, live_qty, state, "
            " cum_qty) VALUES ($1,$2,'ENTRY',$3,'ORDER_INTENT_BUY_LONG',"
            " 'LIMIT','IOC',3,'FILLED',3)", mid, g, slug)
        await conn.execute(
            "INSERT INTO execmirror_fills (fill_key, mirror_id, "
            " venue_order_id, group_id, us_market_slug, intent, qty, price, "
            " fee_usd, observed_at) VALUES ($1,$2,'v',$3,$4,"
            " 'ORDER_INTENT_BUY_LONG',3,0.5,0.03,to_timestamp($5))",
            F.uid("fk-"), mid, g, slug, now - 100)
        rep = await RK.actual_report(conn, now=now)
        mine = [p for p in rep["positions"] if p["group_id"] == g]
        assert len(mine) == 1
        assert mine[0]["exposure_usd"] == pytest.approx(1.53)
        assert mine[0]["venue"] == "POLYMARKET_US"
        got = await AR.check(conn, run_id="intelrun:act", book="ACTUAL",
                             primary=rep, now=now)
        assert got["agrees"] is True, got
    finally:
        await tr.rollback()
        await conn.close()
