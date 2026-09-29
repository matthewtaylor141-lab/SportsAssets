"""XAVIER IN THE COMMAND CENTRE: `/api/command/xavier`, its history route,
its page, and the frontend that reads it.

Pinned here: the routes carry the same read guard as every COMMAND read and
refuse an anonymous caller with 401; the module holds no mutating statement;
an empty list is never shown as an all-clear -- the empty state names why
nothing is managed; a populated state carries the chosen action, every
alternative with value, change vs HOLD, worst case, capital and blocker, the
book's realised figures and the next review; the latest daily review's
alternative figures stay labelled HYPOTHETICAL_ESTIMATE with
could_have_filled UNPROVEN; and the frontend references the route and the
API-served page is exactly a fresh build of the static files.

Records inserted here are SYNTHETIC TEST EVIDENCE and are removed after.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import os
import pathlib
import re

import pytest

from sportsassets import bettor_xavier as X
from sportsassets import bettor_xavier_review as XR
from sportsassets.api import app as A
from sportsassets.api import command_xavier as CX

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "frontend" / "public" / "command"
ACCT = "acct-xcmd-test"
VENUE = "PMUS_TEST"
PFX = "xcmdt-"
ROUTES = ("/api/command/xavier", "/api/command/xavier/page",
          "/api/command/xavier/{intent_id}")


class _Cfg:
    """Known credentials for the ASGI app under test; not real secrets."""
    admin_token = "admin-token-for-the-xavier-tests"
    desk_password = "desk-password-for-the-xavier-tests"


@pytest.fixture()
def client(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    return starlette.TestClient(A.app, raise_server_exceptions=False)


def _pool(monkeypatch):
    """The app's pool, replaced by one that opens a connection on the
    test client's own event loop."""
    import asyncpg

    class _Acq:
        async def __aenter__(self):
            self.c = await asyncpg.connect(DSN)
            return self.c

        async def __aexit__(self, *a):
            await self.c.close()

    class _Pool:
        def acquire(self):
            return _Acq()

    async def _get():
        return _Pool()

    monkeypatch.setattr(A, "get_pool", _get)


AUTH = {"X-Admin-Token": _Cfg.admin_token}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE GUARD AND THE SHAPE OF THE MODULE
# ═════════════════════════════════════════════════════════════════════

def test_every_xavier_route_carries_the_command_read_guard():
    found = {}
    for r in A.app.routes:
        if getattr(r, "path", "") in ROUTES:
            names = {getattr(d.call, "__name__", "")
                     for d in r.dependant.dependencies}
            found[r.path] = (names, r.methods)
    assert set(found) == set(ROUTES)
    for path, (names, methods) in found.items():
        assert "require_command" in names, path
        assert methods <= {"GET", "HEAD"}, (path, methods)


def test_the_page_route_is_declared_before_the_per_position_route():
    """Starlette matches in order; `/page` must not be read as an intent."""
    order = [r.path for r in A.app.routes if getattr(r, "path", "") in ROUTES]
    assert order.index("/api/command/xavier/page") < order.index(
        "/api/command/xavier/{intent_id}")


def test_an_anonymous_caller_gets_401_on_every_xavier_route(client):
    for path in ("/api/command/xavier", "/api/command/xavier/page",
                 "/api/command/xavier/some-intent"):
        assert client.get(path).status_code == 401, path


def test_the_read_model_holds_no_mutating_statement_and_no_venue_client():
    src = inspect.getsource(CX)
    for word in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "ALTER "):
        assert word not in src.upper(), word
    assert "polymarket" not in src.lower()
    assert "submit" not in src.lower()


def test_the_projection_derives_the_increment_only_from_the_same_record():
    alts = CX.project_alternatives([
        {"action": "HOLD", "value_usd": 1.0, "downside_usd": -5.0},
        {"action": "DIRECT_EXIT", "expected_net_usd": 0.9,
         "worst_case_remaining_loss_usd": -0.1, "capital_released_usd": 5.5},
        {"action": "ACQUIRE_HEDGE", "value_usd": 1.2,
         "increment_vs_hold_usd": 0.25, "incremental_capital_usd": 9.8},
        {"action": "REDUCE", "blocker": "NO_EXECUTABLE_DEPTH"}],
        chosen_index=0)
    by = {a["action"]: a for a in alts}
    assert by["HOLD"]["chosen"] is True
    assert by["DIRECT_EXIT"]["increment_vs_hold_usd"] == pytest.approx(-0.1)
    assert by["DIRECT_EXIT"]["increment_basis"].startswith("DERIVED")
    assert by["ACQUIRE_HEDGE"]["increment_vs_hold_usd"] == 0.25
    assert by["ACQUIRE_HEDGE"]["increment_basis"] == "RECORDED"
    assert by["ACQUIRE_HEDGE"]["capital_usd"] == 9.8
    assert by["HOLD"]["worst_case_usd"] == -5.0
    assert by["REDUCE"]["value_usd"] is None
    assert by["REDUCE"]["blocker"] == "NO_EXECUTABLE_DEPTH"


def test_the_review_digest_keeps_every_estimate_labelled():
    got = CX.review_digest({
        "review_id": "xrev:2031-03-10", "review_date": "2031-03-10",
        "alternatives": {"label": XR.KIND_HYPOTHETICAL,
                         "could_have_filled": "UNPROVEN",
                         "rows": [{"action": "DIRECT_EXIT",
                                   "kind": XR.KIND_HYPOTHETICAL,
                                   "could_have_filled": "UNPROVEN",
                                   "estimate_usd": 0.9}] * 40},
        "per_action": {}, "errors": {}, "models": {"promoted_anything":
                                                   False},
        "invalidated": []})
    assert "HYPOTHETICAL ESTIMATE" in got["alternatives"]["label"]
    assert "UNPROVEN" in got["alternatives"]["label"]
    assert got["alternatives"]["could_have_filled"] == "UNPROVEN"
    assert len(got["alternatives"]["rows"]) == CX.REVIEW_ALTERNATIVE_ROWS
    assert all(r["kind"] == XR.KIND_HYPOTHETICAL
               for r in got["alternatives"]["rows"])
    assert CX.review_digest(None)["available"] is False


# ═════════════════════════════════════════════════════════════════════
# 2 · THE FRONTEND
# ═════════════════════════════════════════════════════════════════════

def test_the_frontend_reads_the_route_and_labels_the_estimates():
    js = (BUNDLE / "xavier.js").read_text()
    assert "'/api/command/xavier'" in js
    assert "credentials: 'same-origin'" in js and "no-store" in js
    assert "HYPOTHETICAL ESTIMATES" in js
    assert "UNKNOWN" in js
    for never in ("localStorage", "sessionStorage", "document.cookie"):
        assert never not in js, never
    center = (BUNDLE / "center.js").read_text()
    assert "['xavier', 'Xavier']" in center
    assert "BTXavier.panel(render)" in center
    html = (BUNDLE / "center.html").read_text()
    assert html.index('src="xavier.js"') < html.index('src="center.js"')
    assert 'href="xavier.css"' in html


def test_the_api_served_page_is_a_fresh_build_of_the_static_files():
    from sportsassets.api.xavier_page import XAVIER_PAGE_HTML
    from tools import build_xavier_page as B

    assert XAVIER_PAGE_HTML == B.build(), (
        "api/xavier_page.py is stale: run python3 -m tools.build_xavier_page")
    assert "script src=" not in XAVIER_PAGE_HTML
    assert "/api/command/xavier" in XAVIER_PAGE_HTML


# ═════════════════════════════════════════════════════════════════════
# 3 · THE ROUTE AGAINST A DATABASE
# ═════════════════════════════════════════════════════════════════════

async def _purge():
    import asyncpg

    c = await asyncpg.connect(DSN)
    try:
        if not await X.has_schema(c):
            pytest.skip("migration 148 is not in this database")
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            if await X.has_event_schema(c):
                await c.execute("DELETE FROM bettor_xavier_execution_events "
                                " WHERE account_id=$1", ACCT)
            await c.execute("DELETE FROM bettor_xavier_decisions "
                            " WHERE account_id=$1", ACCT)
            if await XR.has_schema(c):
                await c.execute("DELETE FROM bettor_xavier_reviews "
                                " WHERE review_id LIKE 'xrev:2032-%'")
    finally:
        await c.close()


@pytest.fixture()
async def clean():
    if not DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    await _purge()
    yield
    await _purge()


@pg
async def test_the_empty_state_says_why_nothing_is_managed(monkeypatch,
                                                           clean):
    import asyncpg

    async def _none(conn, **kw):
        return {"ok": True, "refusal": None, "positions": []}

    monkeypatch.setattr(X, "latest_decisions", _none)
    c = await asyncpg.connect(DSN)
    try:
        got = await CX.overview(c)
    finally:
        await c.close()
    assert got["empty"] is True and got["positions"] == []
    why = got["why_nothing_is_managed"]
    assert why["reasons"], why
    assert "account_bound" in why and "last_cycle" in why
    assert why["decision_table"] == "PRESENT"
    if not why["account_bound"]:
        assert "NO_FUNDED_ACCOUNT_IS_BOUND" in why["reasons"]
    assert got["read_only"] is True
    assert got["submission_switches"]["FUNDED_SUBMISSION_ENABLED"] is False


@pg
def test_a_populated_state_through_the_route(client, monkeypatch, clean):
    import asyncio

    import asyncpg

    decided = dt.datetime(2032, 1, 5, 12, tzinfo=dt.timezone.utc).timestamp()

    async def _seed():
        c = await asyncpg.connect(DSN)
        try:
            got = await X.record_decision(
                c, account_id=ACCT, venue=VENUE, intent_id=PFX + "i1",
                decided_at=decided, responsibility_state=X.HELD,
                execution_eligibility=X.E_SUBMISSION_DISABLED,
                alternatives=[
                    {"action": "HOLD", "qty": 10, "value_usd": 1.0,
                     "worst_case_remaining_loss_usd": -5.0},
                    {"action": "DIRECT_EXIT", "qty": 10, "value_usd": 1.4,
                     "worst_case_remaining_loss_usd": 0.4,
                     "capital_released_usd": 5.4, "plan_digest": "pd-x1"},
                    {"action": "REDUCE", "blocker": "NO_EXECUTABLE_DEPTH"}],
                reasoning={"why": "the exit beats HOLD by 0.40"},
                expected_economics={"expected_net_usd": 1.4},
                residual_exposure={"matched_qty": 0, "unpaired_qty": 10},
                evidence={"probability_source": "SYNTHETIC_TEST"},
                obligations=[{"name": "RESIDUAL_INVENTORY", "qty": 10}],
                chosen_action="DIRECT_EXIT", chosen_plan_digest="pd-x1",
                us_market_slug=PFX + "slug1",
                next_review_at=decided + 900)
            assert got["ok"], got
            await c.execute(
                "INSERT INTO bettor_xavier_reviews (review_id, review_date, "
                " computed_at, review_version, decisions_reviewed, "
                " decisions_with_outcomes, alternatives, summary) VALUES "
                " ('xrev:2032-01-06', '2032-01-06', now(), 'TEST', 1, 1, "
                " $1::jsonb, 'synthetic test review')",
                json.dumps({"label": XR.KIND_HYPOTHETICAL,
                            "could_have_filled": "UNPROVEN",
                            "rows": [{"action": "HOLD",
                                      "kind": XR.KIND_HYPOTHETICAL,
                                      "could_have_filled": "UNPROVEN",
                                      "fill_basis": XR.FILL_BASIS_NO_ORDER,
                                      "estimate_usd": 5.0}]}))
            return got["xavier_decision_id"]
        finally:
            await c.close()

    xid = asyncio.run(_seed())
    _pool(monkeypatch)
    r = client.get("/api/command/xavier", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    body = r.json()
    mine = [p for p in body["positions"] if p["xavier_decision_id"] == xid]
    assert mine, body
    p = mine[0]
    assert p["chosen_action"] == "DIRECT_EXIT"
    assert p["execution_eligibility"] == X.E_SUBMISSION_DISABLED
    assert p["responsibility_state"] == X.HELD
    by = {a["action"]: a for a in p["alternatives"]}
    assert by["DIRECT_EXIT"]["chosen"] is True
    assert by["DIRECT_EXIT"]["increment_vs_hold_usd"] == pytest.approx(0.4)
    assert by["DIRECT_EXIT"]["worst_case_usd"] == pytest.approx(0.4)
    assert by["REDUCE"]["blocker"] == "NO_EXECUTABLE_DEPTH"
    assert p["residual_exposure"]["unpaired_qty"] == 10
    assert p["next_review_at"].startswith("2032-01-05T12:15")
    names = {b["name"] for b in p["blockers"]}
    assert X.E_SUBMISSION_DISABLED in names
    assert "NO_EXECUTABLE_DEPTH" in names and "RESIDUAL_INVENTORY" in names
    # THE BOOK HAS NO ECONOMICS FOR THIS SYNTHETIC POSITION: realised is the
    # ledger's empty sum and unrealised is unmeasured, never zero
    assert p["realised"]["realised_usd"] == 0.0
    assert p["realised"]["unrealised_usd"] is None
    assert "NOT_MEASURED" in p["realised"]["unrealised_status"]
    rv = body["daily_review"]
    assert rv["available"] is True and rv["review_id"] == "xrev:2032-01-06"
    assert all(x["kind"] == XR.KIND_HYPOTHETICAL
               and x["could_have_filled"] == "UNPROVEN"
               for x in rv["alternatives"]["rows"])
    assert "UNPROVEN" in body["hypothetical_label"]
    text = json.dumps(body)
    assert _Cfg.admin_token not in text and "password" not in text.lower()

    h = client.get("/api/command/xavier/" + PFX + "i1", headers=AUTH)
    assert h.status_code == 200, h.text
    assert h.json()["count"] == 1
    assert h.json()["decisions"][0]["chosen_action"] == "DIRECT_EXIT"
    missing = client.get("/api/command/xavier/" + PFX + "none", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["detail"]["reason"] == CX.R_NO_RECORD
    page = client.get("/api/command/xavier/page", headers=AUTH)
    assert page.status_code == 200
    assert "text/html" in page.headers.get("content-type", "")
    assert "default-src 'none'" in page.headers.get(
        "content-security-policy", "")
    assert re.search(r"/api/command/xavier'", page.text)


def test_no_database_pool_is_a_named_503(client, monkeypatch):
    async def _no_pool():
        raise RuntimeError("synthetic: no pool")

    monkeypatch.setattr(A, "get_pool", _no_pool)
    for path in ("/api/command/xavier", "/api/command/xavier/some-intent"):
        r = client.get(path, headers=AUTH)
        assert r.status_code == 503, path
        assert r.json()["detail"]["reason"] == "NO_DATABASE_POOL"


@pg
def test_a_failed_read_is_503_not_an_empty_book(client, monkeypatch):
    async def _broken(conn, **kw):
        raise CX.XavierUnavailable("XAVIER_RECORDS_UNREADABLE", "synthetic")

    _pool(monkeypatch)
    monkeypatch.setattr(CX, "overview", _broken)
    r = client.get("/api/command/xavier", headers=AUTH)
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "XAVIER_RECORDS_UNREADABLE"
