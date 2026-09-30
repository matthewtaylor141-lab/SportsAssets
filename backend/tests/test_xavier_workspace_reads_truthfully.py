"""XAVIER'S WORKSPACE (`/api/command/agents/xavier`) AND ITS SERVICING HOOK.

Pinned here:
  * both routes carry the COMMAND read guard (anonymous -> 401) and set
    Cache-Control: no-store; the module holds no mutating statement and no
    venue or order import;
  * with NO funded position -- the state today -- every required section is
    present and the empty ones are EMPTY with a named reason (no account
    bound; no owned inventory), never OK with an empty table;
  * after a real scheduled cycle on a held position with a four-line ladder,
    positions / reviews / ladder / alternatives / payout_tables / execution
    are OK and carry evidence refs to the persisted Xavier record, which the
    decision route returns (404 for an unknown id); the servicing cadence is
    read from the heartbeat the production writer writes;
  * `after_review` links every written decision into `agent_decisions`
    through core's registry when it is importable (a link with an evidence
    ref, never a copy of economics) and says UNAVAILABLE when it is not.

SYNTHETIC records only; removed after.
"""
from __future__ import annotations

import inspect
import re
import sys
import time
import types

import pytest

from sportsassets.api import agents_xavier as AX
from tests import test_xavier_ladder_compares_every_spread as T
from tests import test_xavier_manages_positions_through_the_scheduled_path as H

pg = H.pg
ADMIN = "admin-token-for-the-xavier-workspace-tests"


class _Cfg:
    admin_token = ADMIN
    desk_password = "desk-password-for-the-xavier-workspace-tests"


def _client(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from fastapi import FastAPI
    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    app = FastAPI()
    app.include_router(AX.router)
    return starlette.TestClient(app, raise_server_exceptions=False)


def _pool(monkeypatch):
    import asyncpg

    class _Acq:
        async def __aenter__(self):
            self.c = await asyncpg.connect(H.DSN)
            return self.c

        async def __aexit__(self, *a):
            await self.c.close()

    class _Pool:
        def acquire(self):
            return _Acq()

    async def _get():
        return _Pool()
    monkeypatch.setattr(AX, "get_pool", _get)


AUTH = {"X-Admin-Token": ADMIN}
WS = "/api/command/agents/xavier"


def test_the_workspace_module_is_read_only():
    src = inspect.getsource(AX)
    for bad in (r"\bINSERT\b", r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b",
                r"\bDROP\b", r"\bTRUNCATE\b", r"claim_dispatch",
                r"record_execution_event", r"dispatch_selection",
                r"submit_for_decision", r"_get_client", r"router\.post",
                r"router\.put", r"router\.delete"):
        assert not re.search(bad, src), bad
    assert set(AX.SECTIONS) == {
        "status", "versions", "positions", "reviews", "ladder",
        "alternatives", "payout_tables", "execution", "recovery",
        "performance", "servicing_cadence"}


def test_both_routes_refuse_an_anonymous_caller(monkeypatch):
    c = _client(monkeypatch)
    assert c.get(WS).status_code == 401
    assert c.get(WS + "/decisions/xav:nothing").status_code == 401


async def _no_heartbeat(conn):
    await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                       AX.SERVICING_KEY)


@pg
@pytest.mark.asyncio
async def test_with_no_funded_position_every_section_says_why(monkeypatch):
    conn = await H._connect()
    try:
        await H.clean(conn)
        await _no_heartbeat(conn)
        got = await AX.workspace(conn, now=1_790_000_000.0)
        assert got["read_only"] is True
        assert got["read_at"] == 1_790_000_000.0
        secs = got["sections"]
        assert set(secs) == set(AX.SECTIONS)
        for name, s in secs.items():
            assert s["status"] in (AX.OK, AX.EMPTY, AX.UNAVAILABLE), name
            assert set(s) == {"status", "why", "data", "evidence"}, name
            if s["status"] != AX.OK:
                assert s["why"], name
        assert secs["positions"]["status"] == AX.EMPTY
        assert secs["positions"]["why"].startswith(
            "NO_FUNDED_ACCOUNT_IS_BOUND")
        for name in ("reviews", "ladder", "alternatives", "payout_tables",
                     "execution"):
            assert secs[name]["status"] == AX.EMPTY, (name, secs[name])
            assert "NO_FUNDED_ACCOUNT_IS_BOUND" in secs[name]["why"]
        assert secs["performance"]["status"] == AX.EMPTY
        assert secs["performance"]["why"].startswith("NO_OWNED_INVENTORY")
        assert secs["servicing_cadence"]["status"] == AX.EMPTY
        assert secs["status"]["status"] == AX.OK
        assert secs["versions"]["data"]["management_policy"]["source"] in (
            "CODE_DEFAULT", "AGENT_POLICY_VERSIONS")
        # the route serves the same shape, with no-store
        _pool(monkeypatch)
        c = _client(monkeypatch)
        r = c.get(WS, headers=AUTH)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        assert set(r.json()["sections"]) == set(AX.SECTIONS)
        assert c.get(WS + "/decisions/xav:none", headers=AUTH
                     ).status_code == 404
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_managed_position_fills_the_workspace_from_its_records(
        monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await H._connect()
    try:
        out, rec, _ = await T.run(
            conn, monkeypatch, p=0.55,
            hedge_bids={1: (0.55, 500), 2: (0.45, 500), 3: (0.35, 500),
                        4: (0.25, 500)})
        # THE SERVICING HEARTBEAT, through the production writer
        await L._servicing_heartbeat(conn, {
            "ran": True, "source": L.SOURCE_SERVICING_TASK,
            "at": time.time(), "elapsed_s": 0.5,
            "review_interval_s": 900.0})
        got = await AX.workspace(conn)
        secs = got["sections"]
        xid = rec["xavier_decision_id"]
        pos = secs["positions"]
        assert pos["status"] == AX.OK, pos
        grp = pos["data"]["groups"][0]
        assert grp["primary_qty"] == pytest.approx(10.0)
        assert grp["legs"][0]["intent_id"] == H.HELD_ID
        # the chosen +1.5 hedge was dispatched and filled in this cycle, so
        # the group's basis is both legs': 10 x 0.50 + hedge x 0.45
        assert grp["book"]["remaining_basis_usd"] == pytest.approx(
            5.0 + 0.45 * grp["hedge_qty"])
        assert grp["residual_unpaired_qty"] == pytest.approx(
            abs(grp["primary_qty"] - grp["hedge_qty"]))
        rv = secs["reviews"]
        assert rv["status"] == AX.OK
        row = next(r for r in rv["data"]["current"]
                   if r["xavier_decision_id"] == xid)
        assert row["evidence"][0] == {
            "kind": "bettor_xavier_decisions", "id": xid,
            "href": "/api/command/agents/xavier/decisions/%s" % xid}
        assert row["policy"]["identical_to_approved_ev_policy"] is True
        assert rv["data"]["next_review_at"] is not None
        lad = next(x for x in secs["ladder"]["data"]
                   if x["xavier_decision_id"] == xid)["ladder"]
        ids = [x["candidate_id"] for x in lad]
        for ln in (1, 2, 3, 4):
            assert T.nyy(ln) in ids
        alts = next(x for x in secs["alternatives"]["data"]
                    if x["xavier_decision_id"] == xid)["alternatives"]
        assert {"HOLD", "ACQUIRE_INDIRECT_HEDGE"} <= {a["action"]
                                                      for a in alts}
        assert secs["payout_tables"]["status"] == AX.OK
        assert secs["execution"]["status"] == AX.OK
        cad = secs["servicing_cadence"]
        assert cad["status"] == AX.OK and cad["data"]["state"] == "SERVICED"
        # THE DECISION ROUTE RETURNS THE PERSISTED RECORD
        _pool(monkeypatch)
        c = _client(monkeypatch)
        r = c.get(WS + "/decisions/" + xid, headers=AUTH)
        assert r.status_code == 200, r.text
        d = r.json()["decision"]
        assert d["xavier_decision_id"] == xid
        assert d["chosen_action"] == rec["chosen_action"]
        assert len(d["alternatives"]) == len(rec["alternatives"])
        assert r.headers["cache-control"] == "no-store"

        # ── after_review: THE REGISTRY ABSENT, THEN PRESENT ──────────
        from sportsassets.agents import xavier_ladder as XL
        review = {"funded_servicing": out.get("funded_servicing")}
        briefs = XL._briefs_of(review)
        assert any(b.get("xavier_decision_id") == xid for b in briefs)
        sys.modules.pop("sportsassets.agents.registry", None)
        import sportsassets.agents as AG
        had = getattr(AG, "registry", None)
        if had is None:
            got = await XL.after_review(conn, review=review, now=1.0)
            assert got["registry"].startswith("UNAVAILABLE")
            assert got["linked"] == 0 and got["decisions_seen"] >= 1
        calls = []

        async def link_decision(conn_, **kw):
            calls.append(kw)
            return {"ok": True, "decision_ref": kw["decision_ref"]}
        stub = types.ModuleType("sportsassets.agents.registry")
        stub.link_decision = link_decision
        monkeypatch.setitem(sys.modules, "sportsassets.agents.registry", stub)
        monkeypatch.setattr(AG, "registry", stub, raising=False)
        got = await XL.after_review(conn, review=review, now=2.0)
        assert got["registry"] == "AVAILABLE"
        assert got["linked"] == got["decisions_seen"] == len(calls) >= 1
        mine = next(k for k in calls if k["decision_ref"] == "xavier:" + xid)
        assert mine["agent_id"] == "XAVIER"
        assert mine["verdict"] == rec["chosen_action"]
        assert mine["evidence_refs"] == [{
            "kind": "bettor_xavier_decisions", "id": xid,
            "href": "/api/command/agents/xavier/decisions/%s" % xid}]
        # a link, never a copy of the economics
        assert "expected_net_usd" not in str(mine["summary"])
    finally:
        await _no_heartbeat(conn)
        await H.clean(conn)
        await conn.close()
