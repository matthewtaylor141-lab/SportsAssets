"""THE PAPER BOOK'S ECONOMIC SLEEVES (migration 223).

  §1 THE CLASSIFIER (pure): every known paper strategy has a sleeve; the
     exploration strategy is TRAINING; an unknown or missing strategy is
     UNCLASSIFIED -- never INVESTMENT; the live-eligible versions are the
     execution allowlist's.
  §2 THE SPLIT (pure): INVESTMENT economics exclude TRAINING; starting cash
     + every sleeve's contribution == the account equity, exactly; a group
     with no durable classification is UNCLASSIFIED; training losses are
     RESEARCH COST and training wins are not production alpha; a mark
     re-read at the same price is not a genuine mark update.
  §3 THE COCKPIT (pure): the sleeve rows the profitability metrics are
     recomputed over never include another sleeve's position.
  §4 THE AUTHORITY: /api/command/profitability/sleeves is GET only, 401
     without a command session, holds no SQL write and imports nothing with
     authority.
  §5 THE DATABASE: the SQL classifier equals the Python one; the table is
     append-only with RESEARCH / SHADOW_NO_AUTHORITY; every new ENTRY is
     classified at entry; the backstop records a group the trigger missed;
     over a real multi-strategy paper book EVERY position is classified, the
     sleeves reconcile to the equity wall's account equity, and the endpoint
     defaults to INVESTMENT inside a READ ONLY transaction.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import ast
import pathlib
import re
import time

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_sleeves as SL
from sportsassets.api import command_sleeves as CS

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_791_100_000.0

KNOWN = ("DEREK_ENTRY_POLICY_V2", "PINNACLE_ONLY_PAPER_BENCHMARK",
         "PINNACLE_COMPLETED_GAME_PAPER",
         "PINNACLE_COMPLETED_GAME_MAKER_PAPER", "PINNACLE_EXPLORATION_PAPER")


# ── §1 the classifier ────────────────────────────────────────────────

def test_every_known_strategy_has_a_sleeve_and_exploration_is_training():
    from sportsassets import bettor_paper_ops as OPS
    assert set(OPS.STRATEGY_ORDER) == set(KNOWN)
    for s in KNOWN:
        assert SL.classify(s, None)[0] in SL.SLEEVES
        assert SL.classify(s, None)[0] != SL.UNCLASSIFIED, s
    assert SL.classify("PINNACLE_EXPLORATION_PAPER",
                       "PINNACLE_EXPLORATION_PAPER_V3")[0] == SL.TRAINING
    assert SL.classify("PINNACLE_COMPLETED_GAME_PAPER",
                       "PINNACLE_COMPLETED_GAME_PAPER_V3") == (
        SL.INVESTMENT, "INVESTMENT_POLICY_LIVE_ELIGIBLE_VERSION")
    assert SL.classify("PINNACLE_COMPLETED_GAME_PAPER",
                       "PINNACLE_COMPLETED_GAME_PAPER_V1")[1] == \
        "INVESTMENT_POLICY_VERSION_NOT_LIVE_ELIGIBLE"
    assert SL.classify("DEREK_ENTRY_POLICY_V2", None)[0] == SL.INVESTMENT
    assert SL.classify("PINNACLE_ONLY_PAPER_BENCHMARK", None)[0] == \
        SL.BENCHMARK
    assert SL.classify("PINNACLE_COMPLETED_GAME_MAKER_PAPER", None)[0] == \
        SL.BENCHMARK


def test_unknown_or_missing_strategy_is_unclassified_never_investment():
    assert SL.classify("SOMETHING_NEW_PAPER", "V9") == (
        SL.UNCLASSIFIED, "UNKNOWN_STRATEGY")
    assert SL.classify(None, None) == (SL.UNCLASSIFIED,
                                       "NO_STRATEGY_RECORDED")


def test_live_eligible_versions_are_the_execution_allowlist():
    from sportsassets import execmirror as M
    assert set(SL.INVESTMENT_LIVE_ELIGIBLE) == set(
        M.LIVE_ELIGIBLE["PINNACLE_COMPLETED_GAME_PAPER"])
    # the exploration strategy is never live eligible and is TRAINING
    assert "PINNACLE_EXPLORATION_PAPER" not in M.LIVE_ELIGIBLE


# ── §2 the split ─────────────────────────────────────────────────────

def _p(gid, *, realized=0.0, open_qty=0.0, cost=0.0, fees=0.0,
       strategy="S"):
    return {"position_key": "paperpos:t:%s:m:LONG" % gid, "group_id": gid,
            "us_market_slug": "m-" + gid, "holding_side": "LONG",
            "strategy": strategy, "open_qty": open_qty,
            "cost_basis_usd": cost, "realized_pnl_usd": realized,
            "buy_fees_usd": fees, "sale_fees_usd": 0.0}


def _view(p, *, price=None, observed_at=None):
    mark = ({"status": "OK", "price": price, "observed_at": observed_at,
             "stale": False} if price is not None else
            {"status": "UNAVAILABLE", "price": None, "why": "NO_EXIT_SIDE"})
    mv = None if price is None else round(p["open_qty"] * price, 6)
    return dict(p, mark=mark, marked_value_usd=mv,
                unrealized_pnl_usd=None if mv is None else round(
                    mv - p["cost_basis_usd"], 6))


CLASSES = {
    "g_inv": {"sleeve": "INVESTMENT", "basis": "B", "strategy":
              "PINNACLE_COMPLETED_GAME_PAPER"},
    "g_inv2": {"sleeve": "INVESTMENT", "basis": "B", "strategy":
               "PINNACLE_COMPLETED_GAME_PAPER"},
    "g_trn": {"sleeve": "TRAINING", "basis": "B", "strategy":
              "PINNACLE_EXPLORATION_PAPER"},
    "g_trn2": {"sleeve": "TRAINING", "basis": "B", "strategy":
               "PINNACLE_EXPLORATION_PAPER"},
    "g_bm": {"sleeve": "BENCHMARK", "basis": "B", "strategy":
             "PINNACLE_ONLY_PAPER_BENCHMARK"},
}


def _book():
    """cash = 500,000 - open costs + realized of closed positions."""
    inv = _p("g_inv", open_qty=1000, cost=500.0)            # marked 0.55
    inv2 = _p("g_inv2", realized=37.25)                      # closed, won
    trn = _p("g_trn", realized=-80.0)                        # closed, lost
    trn2 = _p("g_trn2", open_qty=200, cost=90.0, realized=12.5)  # marked
    bm = _p("g_bm", open_qty=100, cost=40.0)                 # UNMARKED
    orphan = _p("g_orphan", realized=-3.0)                   # no class
    allp = [inv, inv2, trn, trn2, bm, orphan]
    realized = sum(p["realized_pnl_usd"] for p in allp)
    cost = sum(p["cost_basis_usd"] for p in allp)
    cash = 500000.0 - cost + realized
    views = [_view(inv, price=0.55, observed_at=NOW - 30),
             _view(trn2, price=0.40, observed_at=NOW - 30), _view(bm)]
    bal = {"ok": True, "cash_usd": cash, "starting_cash_usd": 500000.0,
           "open_positions": views}
    return bal, allp


def test_investment_economics_exclude_training():
    bal, allp = _book()
    got = SL.split(bal, allp, CLASSES, now=NOW)
    inv, trn = got["sleeves"]["INVESTMENT"], got["sleeves"]["TRAINING"]
    assert inv["realized_pnl_usd"] == 37.25
    assert inv["unrealized_pnl_usd"] == 50.0                 # 550 - 500
    assert inv["net_pnl_usd"] == 87.25
    assert inv["positions_open"] == 1 and inv["positions_closed"] == 1
    assert inv["exposure"]["cost_basis_usd"] == 500.0
    assert set(inv["strategies"]) == {"PINNACLE_COMPLETED_GAME_PAPER"}
    # the training loss never touches the investment sleeve
    assert trn["realized_pnl_usd"] == -67.5
    assert trn["unrealized_pnl_usd"] == -10.0                # 80 - 90
    assert trn["exposure"]["cost_basis_usd"] == 90.0
    assert set(trn["strategies"]) == {"PINNACLE_EXPLORATION_PAPER"}


def test_combined_equals_the_sum_of_the_sleeves_exactly():
    bal, allp = _book()
    got = SL.split(bal, allp, CLASSES, now=NOW)
    s = got["sleeves"]
    contrib = sum(s[k]["net_pnl_usd"] for k in SL.SLEEVES)
    acc = got["accounting"]
    assert round(contrib, 2) == acc["sleeve_contributions_usd"]
    assert acc["accounted_equity_usd"] == round(500000.0 + contrib, 2)
    # the account equity the equity wall shows: cash + marked + unmarked at
    # cost
    cash = bal["cash_usd"]
    assert acc["account_equity_usd"] == round(cash + 550.0 + 80.0 + 40.0, 2)
    assert acc["difference_usd"] == 0.0 and acc["reconciled"] is True
    assert acc["title"] == "COMBINED ACCOUNTING TOTAL"
    # exposure sums too
    assert sum(s[k]["exposure"]["cost_basis_usd"] for k in SL.SLEEVES) == \
        630.0


def test_a_group_without_a_durable_classification_is_unclassified():
    bal, allp = _book()
    got = SL.split(bal, allp, CLASSES, now=NOW)
    un = got["sleeves"]["UNCLASSIFIED"]
    assert un["positions_closed"] == 1 and un["realized_pnl_usd"] == -3.0
    assert un["classification_bases"] == {SL.R_NO_DURABLE: 1}
    assert got["unclassified_positions"] == 1
    # nothing of it reached INVESTMENT
    assert got["sleeves"]["INVESTMENT"]["realized_pnl_usd"] == 37.25


def test_training_losses_are_research_cost_and_wins_are_not_alpha():
    bal, allp = _book()
    got = SL.split(bal, allp, CLASSES, now=NOW)
    trn = got["sleeves"]["TRAINING"]
    assert trn["realized_label"] == "RESEARCH COST"
    assert trn["realized_losses_usd"] == -80.0
    assert trn["realized_wins_usd"] == 12.5
    assert trn["economics_label"] == \
        "EXPLORATION_RESEARCH_COST_NOT_INVESTMENT_PERFORMANCE"
    win = SL.split({"ok": True, "cash_usd": 500050.0,
                    "starting_cash_usd": 500000.0, "open_positions": []},
                   [_p("g_trn", realized=50.0)], CLASSES, now=NOW)
    assert win["sleeves"]["TRAINING"]["realized_label"] == \
        "TRAINING WIN -- NOT PRODUCTION ALPHA"
    assert win["sleeves"]["INVESTMENT"]["realized_pnl_usd"] == 0.0


def test_unmarked_positions_add_nothing_to_unrealized():
    bal, allp = _book()
    bm = SL.split(bal, allp, CLASSES, now=NOW)["sleeves"]["BENCHMARK"]
    assert bm["unrealized_pnl_usd"] == 0.0
    assert bm["exposure"]["unmarked_positions"] == 1
    assert bm["exposure"]["unmarked_cost_basis_usd"] == 40.0
    assert bm["marks"]["state"] == "UNMARKED"


def _md(bid):
    return {"bids": [{"px": {"value": "%.2f" % bid}, "qty": "500"}],
            "offers": [{"px": {"value": "%.2f" % (bid + 0.02)},
                        "qty": "500"}]}


def test_a_re_read_at_the_same_price_is_not_a_genuine_mark_update():
    same = [(NOW - 10, _md(0.50)), (NOW - 70, _md(0.50)),
            (NOW - 130, _md(0.50))]
    got = SL.mark_change_of(same, "LONG")
    assert got["changed_at"] is None
    assert got["unchanged_since_at_least"] == NOW - 130
    moved = [(NOW - 10, _md(0.52)), (NOW - 70, _md(0.52)),
             (NOW - 130, _md(0.50))]
    got = SL.mark_change_of(moved, "LONG")
    assert got["changed_at"] == NOW - 70 and got["price"] == 0.52
    # a sleeve whose marks only re-read is NO_NEW_MARK, never FRESH
    bal, allp = _book()
    changes = {("m-g_inv", "LONG"): {"changed_at": None,
                                     "unchanged_since_at_least": NOW - 4000},
               ("m-g_trn2", "LONG"): {"changed_at": NOW - 60}}
    s = SL.split(bal, allp, CLASSES, now=NOW, changes=changes)["sleeves"]
    assert s["INVESTMENT"]["marks"]["state"] == "NO_NEW_MARK"
    assert s["INVESTMENT"]["marks"]["last_genuine_mark_update_at"] is None
    assert s["TRAINING"]["marks"]["state"] == "FRESH"
    assert s["TRAINING"]["marks"]["last_genuine_mark_update_at"] == NOW - 60


def test_the_equity_wall_freezes_without_a_genuine_mark_change():
    from sportsassets.api import command_equity as E
    bal, allp = _book()
    bal.update(realized_pnl_usd=0.0, last_updated_at=NOW - 7200,
               available_usd=1.0, reserved_usd=0.0)
    sess = {"active": True, "heartbeat_at": NOW - 20}
    still = E.paper_account(bal, now=NOW, session=sess, genuine_mark_at=None)
    # the newest book RE-READ was 30 s ago, but nothing changed: the last
    # change is the ledger's, and the account says NO NEW MARK
    assert still["last_change_at"] == E.iso(NOW - 7200)
    assert still["no_new_mark"] is True
    moved = E.paper_account(bal, now=NOW, session=sess,
                            genuine_mark_at=NOW - 45)
    assert moved["last_change_at"] == E.iso(NOW - 45)
    assert moved["no_new_mark"] is False
    assert moved["last_genuine_mark_update_at"] == E.iso(NOW - 45)


# ── §3 the cockpit rows ──────────────────────────────────────────────

def test_the_cockpit_recomputes_investment_without_training_rows():
    from sportsassets.profitability import metrics as MT
    econs = []
    for i in range(6):
        econs.append({"book": "PAPER", "position_key": "pi%d" % i,
                      "group_id": "g_inv", "state": "CLOSED",
                      "net_profit_usd": 10.0, "capital_committed_usd": 100.0,
                      "capital_hours": 50.0, "released_at": NOW - 3600 * i,
                      "last_event_at": NOW - 3600 * i,
                      "expected_net_profit_usd": 8.0})
        econs.append({"book": "PAPER", "position_key": "pt%d" % i,
                      "group_id": "g_trn", "state": "CLOSED",
                      "net_profit_usd": -40.0, "capital_committed_usd": 100.0,
                      "capital_hours": 50.0, "released_at": NOW - 3600 * i,
                      "last_event_at": NOW - 3600 * i,
                      "expected_net_profit_usd": -5.0})
    econs.append({"book": "PAPER", "position_key": "px", "group_id":
                  "g_unknown", "state": "CLOSED", "net_profit_usd": 999.0,
                  "capital_committed_usd": 1.0, "capital_hours": 1.0,
                  "released_at": NOW, "last_event_at": NOW})
    groups = {"g_inv": "INVESTMENT", "g_trn": "TRAINING"}
    inv = CS.select_rows(econs, groups, "INVESTMENT")
    assert {e["group_id"] for e in inv} == {"g_inv"} and len(inv) == 6
    un = CS.select_rows(econs, groups, "UNCLASSIFIED")
    assert [e["position_key"] for e in un] == ["px"]
    assert len(CS.select_rows(econs, groups, "COMBINED")) == 13
    m = {x["metric"]: x for x in MT.compute(inv, book="PAPER", now=NOW,
                                           lookback_days=60)}
    assert m["REALIZED_NET_EDGE"]["value"] == pytest.approx(0.10)
    cap = CS.capital_summary(inv, now=NOW)
    assert cap["realized_net_profit_usd"] == 60.0
    assert cap["realized_sample"] == 6
    assert cap["idle_capital_usd"] is None
    assert "SHARED_CASH" in cap["unmeasured"]["idle_capital_usd"]


# ── §4 authority ─────────────────────────────────────────────────────

FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "order", "submit", "venue", "live_")


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_the_sleeve_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == CS.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(CS.PATH).status_code == 401
    assert client.post(CS.PATH).status_code in (401, 405)


def test_the_sleeve_modules_hold_no_write_and_no_authority_import():
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                       r"FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE)", re.I)
    for rel in ("api/command_sleeves.py",):
        tree = ast.parse((PKG / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not write.search(node.value), (rel, node.value[:60])
    for rel in ("api/command_sleeves.py", "bettor_paper_sleeves.py"):
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in FORBIDDEN), (rel, imp)
    # the sleeves reader's ONE write is the append-only backstop INSERT into
    # its own table
    src = (PKG / "bettor_paper_sleeves.py").read_text()
    tables = set(re.findall(r"INSERT INTO\s+([a-z_]+)", src))
    assert tables == {"paper_sleeve_classifications"}, tables
    assert not re.search(r"\bUPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM", src)


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_paper_sleeves.py" in listed.splitlines()


# ── §5 the database ──────────────────────────────────────────────────

@pg
async def test_the_sql_classifier_equals_the_python_one():
    conn = await H.connect()
    try:
        for s in KNOWN + ("SOMETHING_ELSE", None):
            for v in (None, "PINNACLE_COMPLETED_GAME_PAPER_V1",
                      "PINNACLE_COMPLETED_GAME_PAPER_V2",
                      "PINNACLE_COMPLETED_GAME_PAPER_V3"):
                row = await conn.fetchrow(
                    "SELECT paper_sleeve_of($1, $2) AS s, "
                    "       paper_sleeve_basis($1, $2) AS b", s, v)
                assert (row["s"], row["b"]) == SL.classify(s, v), (s, v)
    finally:
        await conn.close()


@pg
async def test_the_table_is_append_only_research_with_no_authority():
    import asyncpg
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            "INSERT INTO paper_sleeve_classifications (classification_id, "
            " account_id, group_id, sleeve, basis, classifier_version, "
            " classified_by) VALUES ('psc:t1','paper_t','g_t1','TRAINING',"
            " 'B','PAPER_SLEEVE_V1','BACKSTOP')")
        for sql in ("UPDATE paper_sleeve_classifications SET sleeve = "
                    "'INVESTMENT' WHERE classification_id = 'psc:t1'",
                    "DELETE FROM paper_sleeve_classifications WHERE "
                    "classification_id = 'psc:t1'"):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(sql)
        for bad in ("label", "authority"):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO paper_sleeve_classifications ("
                        " classification_id, account_id, group_id, sleeve, "
                        " basis, classifier_version, classified_by, %s) "
                        "VALUES ('psc:t2','paper_t','g_t2','TRAINING','B',"
                        " 'PAPER_SLEEVE_V1','BACKSTOP','X')" % bad)
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO paper_sleeve_classifications ("
                    " classification_id, account_id, group_id, sleeve, basis,"
                    " classifier_version, classified_by) VALUES ('psc:t3',"
                    " 'paper_t','g_t3','PRODUCTION','B','PAPER_SLEEVE_V1',"
                    " 'BACKSTOP')")
    finally:
        await tr.rollback()
        await conn.close()


async def _buy(conn, a, *, key, slug, strategy, qty, limit, at, bids,
               fixture):
    from sportsassets import bettor_paper_simulator as SIM
    o = H.order(a, key=key, qty=qty, limit=limit, slug=slug, at=at,
                group_id="paper_g_%s_%s" % (a["account_id"][-6:], key),
                fixture=fixture)
    o["strategy"] = strategy
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3, offers=[(limit, qty)], bids=bids)
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                             fee_fn=H.zero_fee)
    return o["group_id"]


async def _seed(conn, now):
    a = await H.new_account(conn, "slv", now=now - 7200)
    acct = a["account_id"]
    g_inv = await _buy(conn, a, key="inv", slug=acct + ":inv",
                       strategy="PINNACLE_COMPLETED_GAME_PAPER", qty=1000,
                       limit=0.50, at=now - 900, bids=[(0.48, 1000)],
                       fixture="fx-inv")
    g_trn = await _buy(conn, a, key="trn", slug=acct + ":trn",
                       strategy="PINNACLE_EXPLORATION_PAPER", qty=200,
                       limit=0.40, at=now - 880, bids=[(0.38, 200)],
                       fixture="fx-trn")
    g_trn2 = await _buy(conn, a, key="trn2", slug=acct + ":trn2",
                        strategy="PINNACLE_EXPLORATION_PAPER", qty=100,
                        limit=0.30, at=now - 860, bids=[(0.29, 100)],
                        fixture="fx-trn2")
    # the training position that lost: settled LOST -> a research cost
    await L.settle(conn, account_id=acct, group_id=g_trn2,
                   slug=acct + ":trn2", holding_side="LONG",
                   settlement_event_key="ev-trn2", outcome="LOST",
                   evidence={"t": 1}, evidence_source="TEST", at=now - 600)
    # the investment market genuinely moves
    await H.observe(conn, acct + ":inv", now - 30, bids=[(0.56, 1000)],
                    offers=[(0.58, 1000)])
    return a, {"inv": g_inv, "trn": g_trn, "trn2": g_trn2}


@pg
async def test_every_paper_position_is_classified_and_the_sleeves_reconcile(
        monkeypatch):
    from sportsassets.api import command_equity as E
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        a, g = await _seed(conn, now)
        acct = a["account_id"]
        # CLASSIFIED AT ENTRY by the trigger, in the order's transaction
        rows = {r["group_id"]: r for r in await conn.fetch(
            "SELECT group_id, sleeve, classified_by, strategy "
            "  FROM paper_sleeve_classifications WHERE account_id = $1",
            acct)}
        assert set(rows) == set(g.values())
        assert {r["classified_by"] for r in rows.values()} == {
            "ENTRY_TRIGGER"}
        assert rows[g["inv"]]["sleeve"] == "INVESTMENT"
        assert rows[g["trn"]]["sleeve"] == rows[g["trn2"]]["sleeve"] == \
            "TRAINING"
        SL._MC_CACHE.update(at=0.0, key=None, val=None)
        sb = await SL.sleeve_book(conn, acct, now=now)
        assert sb["status"] == "OK"
        assert sb["unclassified_positions"] == 0
        s = sb["sleeves"]
        positions = await L.positions(conn, acct, include_closed=True)
        assert sum(s[k]["positions_open"] + s[k]["positions_closed"]
                   for k in SL.SLEEVES) == len(positions) == 3
        # INVESTMENT excludes the training loss
        assert s["INVESTMENT"]["realized_pnl_usd"] == 0.0
        assert s["INVESTMENT"]["unrealized_pnl_usd"] == round(
            1000 * 0.56 - 500.0, 2)
        assert s["TRAINING"]["realized_pnl_usd"] == -30.0
        assert s["TRAINING"]["realized_label"] == "RESEARCH COST"
        # the investment mark genuinely changed (0.48 -> 0.56)
        assert s["INVESTMENT"]["marks"]["last_genuine_mark_update_at"] == \
            pytest.approx(now - 30, abs=1)
        # combined == sum of the sleeves == the equity wall's account equity
        bal = await L.balances(conn, acct, now=now)
        wall = E.paper_account(bal, now=now)
        acc = sb["accounting"]
        assert acc["reconciled"] is True and acc["difference_usd"] == 0.0
        assert acc["account_equity_usd"] == wall["equity_usd"]
        assert acc["accounted_equity_usd"] == wall["equity_usd"]
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_backstop_classifies_a_group_the_trigger_missed():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute("ALTER TABLE paper_orders DISABLE TRIGGER "
                           "paper_orders_sleeve_at_entry_trg")
        now = time.time()
        a = await H.new_account(conn, "bks", now=now - 3600)
        acct = a["account_id"]
        gid = await _buy(conn, a, key="b1", slug=acct + ":b1",
                         strategy="PINNACLE_EXPLORATION_PAPER", qty=100,
                         limit=0.40, at=now - 600, bids=[(0.39, 100)],
                         fixture="fx-b1")
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_sleeve_classifications "
            " WHERE group_id = $1", gid) == 0
        sb = await SL.sleeve_book(conn, acct, now=now)
        # no durable row yet: UNCLASSIFIED, explicitly -- never INVESTMENT
        assert sb["sleeves"]["UNCLASSIFIED"]["positions_open"] == 1
        assert sb["sleeves"]["INVESTMENT"]["positions_open"] == 0
        assert await SL.classify_missing(conn, acct) == 1
        assert await SL.classify_missing(conn, acct) == 0      # idempotent
        r = await conn.fetchrow(
            "SELECT sleeve, classified_by, basis FROM "
            " paper_sleeve_classifications WHERE group_id = $1", gid)
        assert (r["sleeve"], r["classified_by"]) == ("TRAINING", "BACKSTOP")
        sb = await SL.sleeve_book(conn, acct, now=now)
        assert sb["unclassified_positions"] == 0
    finally:
        await tr.rollback()
        await conn.close()


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


@pg
async def test_the_cockpit_endpoint_defaults_to_investment(monkeypatch):
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        a, g = await _seed(conn, now)
        monkeypatch.setattr(L, "ACCOUNT_ID", a["account_id"])

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(CS, "_pool", pool)
        CS._CACHE.clear()
        SL._MC_CACHE.update(at=0.0, key=None, val=None)
        import inspect
        default = inspect.signature(CS.profitability_sleeves).parameters[
            "sleeve"].default
        assert getattr(default, "default", default) == "INVESTMENT"
        got = await CS.profitability_sleeves(sleeve="INVESTMENT")
        assert got["label"] == "RESEARCH"
        assert got["authority"] == "SHADOW_NO_AUTHORITY"
        assert got["status"] == "OK", got["why"]
        d = got["data"]
        assert d["sleeve"] == "INVESTMENT" == d["default_sleeve"]
        assert d["live"]["sleeve"] == "INVESTMENT"
        assert d["live"]["realized_pnl_usd"] == 0.0
        assert set(d["live"]["strategies"]) == {
            "PINNACLE_COMPLETED_GAME_PAPER"}
        assert d["equity_method"]["rule"] == "PNL_ONLY_ON_SHARED_CASH"
        CS._CACHE.clear()
        t = await CS.profitability_sleeves(sleeve="TRAINING")
        assert t["data"]["live"]["realized_pnl_usd"] == -30.0
        assert t["data"]["labels"]["TRAINING"]["loss"] == "RESEARCH COST"
    finally:
        await tr.rollback()
        await conn.close()
        CS._CACHE.clear()
