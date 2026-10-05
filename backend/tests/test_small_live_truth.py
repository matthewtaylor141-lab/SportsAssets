"""CAPITAL-CRITICAL: SMALL LIVE MEANS BETTOR-ORIGINATED.

  §1 THE STATUS (pure, bettor_originated_status.derive_status): exactly one
     of NOT_CONFIGURED / SHADOW / READY_NOT_ACTIVATED / ACTIVE / DEGRADED /
     STOPPED; NEVER ACTIVE without a BETTOR-originated, venue-acknowledged
     order carrying the whole chain (Derek decision -> allocation -> Archer
     execution decision -> venue order + ack); RN1, mirror and copy orders
     never count whatever they carry; this build has no BETTOR-originated
     path, so the status is SHADOW / NOT_CONFIGURED.
  §2 RECOMMENDATIONS ARE NOT ORDERS: Archer's EXECUTE_NOW counts never
     appear as orders submitted, venue-acknowledged or filled.
  §3 THE LEGACY MIRROR LABEL comes from the real control row: STOPPED only
     when it says so, RUNNING when it runs; the equity wall, the mirror
     report, Slack and Archer's page carry it.
  §4 AUTHORITY: the module makes no venue call, imports no order / venue /
     execution / funded module, holds no SQL write and changes no control
     row; the path constant stays False.
  §5 DATABASE: over real records (a STOPPED mirror with excluded copies, a
     live-eligible intent refused, Archer EXECUTE_NOW estimates, no Kalshi
     key) the status is SHADOW, Kalshi and Polymarket US are NOT_CONNECTED,
     orders are 0 and the reads leave every control row unchanged.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import ast
import pathlib
import re
import time

import pytest

from sportsassets import bettor_originated_status as B

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_791_100_000.0


def _order(**kw):
    o = {"origin": "BETTOR", "derek_decision_id": "paperdec:1",
         "allocation_id": "alloc:1", "archer_estimate_id": "archer:1",
         "archer_recommendation": "EXECUTE_NOW", "venue": "polymarket_us",
         "venue_order_id": "V-1", "venue_ack_at": NOW - 60, "fills": 1}
    o.update(kw)
    return o


def _ev(**kw):
    ev = {"path_configured": True, "control": {"enabled": True,
                                               "stopped": False},
          "orders": [], "shadow": {"archer_estimates": 3},
          "heartbeat_at": NOW - 10,
          "venues": {"polymarket_us": {"state": "CONNECTED"},
                     "kalshi": {"state": "NOT_CONNECTED"}}}
    ev.update(kw)
    return ev


# ── §1 the status ────────────────────────────────────────────────────

def test_the_statuses_are_exactly_the_six():
    assert B.STATUSES == ("NOT_CONFIGURED", "SHADOW", "READY_NOT_ACTIVATED",
                          "ACTIVE", "DEGRADED", "STOPPED")


def test_active_needs_a_bettor_originated_venue_acknowledged_order():
    assert B.derive_status(_ev(orders=[_order()]), now=NOW)["status"] == \
        "ACTIVE"
    # no order -> never ACTIVE, even enabled, connected and heartbeating
    assert B.derive_status(_ev(), now=NOW)["status"] == \
        "READY_NOT_ACTIVATED"
    # every missing link of the chain refuses the order
    for k in B.CHAIN_FIELDS:
        got = B.derive_status(_ev(orders=[_order(**{k: None})]), now=NOW)
        assert got["status"] != "ACTIVE", k
        assert got["refused_orders"][0]["refused"].startswith(
            "CHAIN_INCOMPLETE"), k
    for rec in ("SKIP_EXECUTION", "WAIT", None):
        got = B.derive_status(_ev(orders=[_order(archer_recommendation=rec)]),
                              now=NOW)
        assert got["status"] != "ACTIVE", rec
    got = B.derive_status(_ev(orders=[_order(venue="paper")]), now=NOW)
    assert got["status"] != "ACTIVE"


@pytest.mark.parametrize("origin", ["RN1", "MIRROR", "LEGACY_MIRROR",
                                    "EXECMIRROR", "COPY", "KALSHI_MIRROR",
                                    "EXECUTION_INTENT_MIRROR_LANE",
                                    "PAPER_ORDER_COPY", "", None, "bettor2"])
def test_mirror_rn1_and_copy_orders_never_count(origin):
    full = _order(origin=origin)
    ok, why = B.is_bettor_originated(full)
    assert ok is False and why
    got = B.derive_status(_ev(orders=[full] * 5), now=NOW)
    assert got["status"] != "ACTIVE"
    assert got["verified_orders"] == []


def test_stale_heartbeat_or_no_venue_is_degraded_not_active():
    assert B.derive_status(_ev(orders=[_order()], heartbeat_at=NOW - 3600),
                           now=NOW)["status"] == "DEGRADED"
    assert B.derive_status(_ev(orders=[_order()], heartbeat_at=None),
                           now=NOW)["status"] == "DEGRADED"
    no_venue = {"polymarket_us": {"state": "NOT_CONNECTED"},
                "kalshi": {"state": "NOT_CONNECTED"}}
    assert B.derive_status(_ev(orders=[_order()], venues=no_venue),
                           now=NOW)["status"] == "DEGRADED"
    assert B.derive_status(_ev(control={"enabled": True, "stopped": True},
                               orders=[_order()]),
                           now=NOW)["status"] == "STOPPED"


def test_this_build_has_no_bettor_originated_path():
    assert B.BETTOR_ORIGINATED_PATH_CONFIGURED is False
    shadow = B.derive_status(_ev(path_configured=False, control=None),
                             now=NOW)
    assert shadow["status"] == "SHADOW"
    none = B.derive_status(_ev(path_configured=False, control=None,
                               shadow={"archer_estimates": 0}), now=NOW)
    assert none["status"] == "NOT_CONFIGURED"
    # even a fully formed order cannot make an unconfigured path ACTIVE
    odd = B.derive_status(_ev(path_configured=False, orders=[_order()]),
                          now=NOW)
    assert odd["status"] == "DEGRADED"


# ── §2 recommendations are not orders ────────────────────────────────

def test_archer_execute_now_counts_never_appear_as_orders():
    counts = {"status": "OK", "why": None,
              "recommendations": {"EXECUTE_NOW": 41, "REST_LIMIT": 2,
                                  "SPLIT": 1, "WAIT": 3,
                                  "SKIP_EXECUTION": 17},
              "estimates": 64}
    legacy = {"label": "LEGACY MIRROR VALIDATION — STOPPED",
              "orders": {"sent_or_planned": 0, "venue_acknowledged": 0},
              "fills": {"count": 0}}
    f = B.archer_funnel(counts, verified_orders=[], legacy=legacy)
    assert f["recommendations_are_not_orders"] is True
    assert f["execute_now_recommendations"] == 41
    assert f["skip_execution_recommendations"] == 17
    assert f["bettor_orders_submitted"] == 0
    assert f["bettor_orders_venue_acknowledged"] == 0
    assert f["bettor_fills"] == 0
    assert f["legacy_mirror_not_archer"]["label"].startswith(
        "LEGACY MIRROR VALIDATION")
    from sportsassets.agents import archer as E
    assert tuple(B.EXECUTING_RECOMMENDATIONS) == tuple(E.EXECUTING)
    assert set(B.RECOMMENDATIONS) == set(E.RECOMMENDATIONS)
    # unmeasured is not zero
    u = B.archer_funnel({"status": "UNAVAILABLE", "why": "X",
                        "recommendations": None}, verified_orders=[],
                       legacy=None)
    assert u["execute_now_recommendations"] is None


# ── §3 the legacy mirror label ───────────────────────────────────────

def test_the_legacy_label_follows_the_real_control_row():
    assert B.legacy_label({"enabled": True, "stopped": True}) == \
        "LEGACY MIRROR VALIDATION — STOPPED"
    assert B.legacy_label({"enabled": True, "stopped": False}) == \
        "LEGACY MIRROR VALIDATION — RUNNING"
    assert B.legacy_label({"enabled": False, "stopped": False}) == \
        "LEGACY MIRROR VALIDATION — DISABLED"
    assert B.legacy_label(None).endswith("UNAVAILABLE")


def test_the_surfaces_carry_the_legacy_label():
    from sportsassets import slack_updates as U
    from sportsassets.api import command_equity as E
    pm = E.pm_account({"enabled": True, "stopped": True, "scale": 1000,
                       "account_fingerprint": True}, None, now=NOW)
    assert pm["label"] == "Polymarket US · LEGACY MIRROR VALIDATION — STOPPED"
    pm_on = E.pm_account({"enabled": True, "stopped": False, "scale": 1000,
                          "account_fingerprint": True}, None, now=NOW)
    assert pm_on["label"].endswith("LEGACY MIRROR VALIDATION — RUNNING")
    assert E.ACTUAL_LABEL.startswith("LEGACY MIRROR VALIDATION")
    env = E.envelope({}, {}, {}, {"title": B.TITLE, "status": "SHADOW"})
    assert env["actual"]["is_target_small_live"] is False
    assert env["small_live_bettor"]["title"] == \
        "SMALL LIVE — BETTOR ORIGINATED"
    m = {"enabled": True, "stopped": True, "pnl": {}, "coverage": {},
         "account": {}, "small_live": {"status": "SHADOW", "why": "w"}}
    text = U.mirror_block(m)
    assert text.startswith("LEGACY MIRROR VALIDATION — STOPPED")
    assert "SMALL LIVE — BETTOR ORIGINATED: SHADOW" in text
    assert U.mirror_block(None).startswith("LEGACY MIRROR VALIDATION")
    pages = (PKG / "api" / "agent_pages.py").read_text()
    assert "execution_funnel" in pages and "Recommendations are not orders" \
        in pages
    view = (PKG / "execmirror_view.py").read_text()
    assert '"title": "LEGACY MIRROR VALIDATION · execution mirror' in view


# ── §4 authority ─────────────────────────────────────────────────────

FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "order", "submit", "venue", "httpx", "requests",
             "aiohttp", "urllib")


def test_the_module_has_no_authority():
    path = PKG / "bettor_originated_status.py"
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom):
            mods = [node.module or ""] + [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            assert not any(f in (m or "") for f in FORBIDDEN), m
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|"
                                 r"DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE)",
                                 node.value, re.I), node.value[:60]


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_small_live_truth.py" in listed.splitlines()


# ── §5 the database ──────────────────────────────────────────────────

async def _controls(conn):
    a = await conn.fetchrow("SELECT enabled, stopped, scale, max_order_usd, "
                            "revision FROM execmirror_control WHERE id = 1")
    k = await conn.fetchrow("SELECT enabled, stopped, scale, max_order_usd, "
                            "revision FROM kalshi_smalllive_control "
                            "WHERE id = 1")
    return dict(a), dict(k)


@pg
async def test_over_real_records_the_status_is_shadow_and_orders_are_zero():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        await conn.execute(
            "UPDATE execmirror_control SET enabled = true, stopped = true, "
            " stop_done_at = to_timestamp($1) WHERE id = 1", now - 3600)
        await conn.execute("UPDATE kalshi_smalllive_control SET enabled = "
                           "false, reconciliation_id = NULL, key_fingerprint"
                           " = NULL WHERE id = 1")
        # an EXCLUDED mirror copy (never placed)
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, paper_order_id, role, "
            " us_market_slug, intent, order_type, tif, state, exclusion) "
            "VALUES ('slt:m1', 'paperord:slt1', 'ENTRY', 'slt-mkt', "
            " 'ORDER_INTENT_BUY_LONG', 'MARKETABLE', 'IOC', 'EXCLUDED', "
            " 'BELOW_VENUE_MINIMUM')")
        has_archer = await conn.fetchval(
            "SELECT to_regclass('eddie_execution_estimates') IS NOT NULL")
        before = await _controls(conn)
        got = await B.read_isolated(conn, now=now)
        after = await _controls(conn)
        assert before == after                  # nothing was changed
        small = got["small_live"]
        assert small["status"] in ("SHADOW", "NOT_CONFIGURED")
        assert small["status"] != "ACTIVE"
        assert small["orders"]["submitted"] == 0
        assert small["orders"]["venue_acknowledged"] == 0
        assert small["fills"]["count"] == 0
        assert small["mirror_orders_counted"] == 0
        assert small["venues"]["kalshi"]["state"] == "NOT_CONNECTED"
        assert small["venues"]["polymarket_us"]["state"] == "NOT_CONNECTED"
        assert small["equity"]["usd"] is None and small["equity"]["why"]
        lm = got["legacy_mirror"]
        assert lm["label"] == "LEGACY MIRROR VALIDATION — STOPPED"
        assert lm["is_target_small_live"] is False
        assert lm["orders"]["excluded"] >= 1
        f = got["archer_funnel"]
        assert f["bettor_orders_submitted"] == 0
        if has_archer:
            assert f["status"] == "OK"
            assert isinstance(f["execute_now_recommendations"], int)
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_equity_payload_reports_small_live_apart():
    from sportsassets.api import command_equity as E
    conn = await H.connect()
    try:
        got = await E.live_payload(conn)
    finally:
        await conn.close()
    sl = got["small_live_bettor"]
    assert sl["title"] == "SMALL LIVE — BETTOR ORIGINATED"
    assert sl["status"] in B.STATUSES and sl["status"] != "ACTIVE"
    assert sl["archer_funnel"]["recommendations_are_not_orders"] is True
    assert got["actual"]["legacy_mirror"] is True
    assert got["actual"]["venues"]["polymarket_us"]["label"].startswith(
        "Polymarket US · LEGACY MIRROR VALIDATION")
