"""/api/command/coverage, /api/command/postmortems, /api/command/quality:
read-only, behind the command read guard, and shaped as documented. A
failed read is 503 with a reason, never a page of zeros."""
from __future__ import annotations

import pytest

from sportsassets import db as DB
from sportsassets.api import app as A
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
PATHS = ("/api/command/coverage", "/api/command/postmortems",
         "/api/command/quality")


class _Cfg:
    """Known credentials for the ASGI app under test; not real secrets."""
    admin_token = "admin-token-for-the-quality-route-tests"
    desk_password = "desk-password-for-the-quality-route-tests"


@pytest.fixture()
def client(monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    return TestClient(A.app, raise_server_exceptions=False)


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
    monkeypatch.setattr(DB, "get_pool", _get)


def test_an_anonymous_caller_gets_401(client):
    for p in PATHS:
        assert client.get(p).status_code == 401, p


def test_no_route_accepts_a_write(client):
    auth = {"X-Admin-Token": _Cfg.admin_token}
    for p in PATHS:
        assert client.post(p, headers=auth).status_code == 405, p


def test_a_dead_database_is_503_with_a_reason(client, monkeypatch):
    async def _dead():
        raise ConnectionRefusedError("synthetic")
    monkeypatch.setattr(DB, "get_pool", _dead)
    auth = {"X-Admin-Token": _Cfg.admin_token}
    for p in PATHS:
        r = client.get(p, headers=auth)
        assert r.status_code == 503, (p, r.status_code)
        assert r.json()["detail"]["reason"] == "NO_DATABASE_POOL"


@pg
def test_an_authorised_reader_gets_each_read(client, monkeypatch):
    _pool(monkeypatch)
    auth = {"X-Admin-Token": _Cfg.admin_token}
    r = client.get("/api/command/coverage?tz=UTC&days=3", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tz"] == "UTC" and body["unit"] == "provider events"
    assert body["stages"][0] == "provider" and body["stages"][-1] == "filled"
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/command/coverage?tz=Europe/London",
                      headers=auth).status_code == 400
    r = client.get("/api/command/postmortems?book=paper&limit=5",
                   headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["books_never_summed"] is True and "actual" not in body
    assert body["paper"]["status"] in ("OK", "EMPTY")
    assert client.get("/api/command/postmortems?book=both",
                      headers=auth).status_code == 400
    r = client.get("/api/command/quality", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["domains"]) == {"ENGINEERING", "AGENTS",
                                    "INVESTMENT_INTELLIGENCE", "EXECUTION",
                                    "PROFITABILITY_EVIDENCE"}
    assert body["profitability"]["paper"] in (
        "UNPROVEN", "SUPPORTED_BY_FORWARD_SAMPLE",
        "NOT_SUPPORTED_BY_FORWARD_SAMPLE")
