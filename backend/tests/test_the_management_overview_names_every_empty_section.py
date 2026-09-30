"""THE MANAGEMENT OVERVIEW (2026-10-01).

Every section answers OK, EMPTY or UNAVAILABLE, and an EMPTY or UNAVAILABLE
section always carries its named reason -- an empty book is never a row of
zeros. The funded-launch verdict is the serving process's own
`bettor_funded_activation.readiness` sorted by who can clear each check, and
both routes sit behind the COMMAND read credential.
"""
import contextlib
import os

import asyncpg
import pytest

from sportsassets.api import command_overview as CO

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


@pg
async def test_every_section_has_a_status_and_every_non_ok_one_a_reason():
    async with _conn() as conn:
        got = await CO.overview(conn)
    assert got["read_only"] is True
    for name, _ in CO.SECTIONS:
        s = got[name]
        assert s["status"] in (CO.OK, CO.EMPTY, CO.UNAVAILABLE), (name, s)
        if s["status"] != CO.OK:
            assert s["why"], (name, s)
    # submission is off in this build, and the page says so
    assert got["build_and_mode"]["data"]["funded_submission"] == "DISABLED"
    # the launch verdict is the readiness function's, never assumed
    fl = got["funded_launch"]
    if fl["status"] == CO.OK:
        assert fl["data"]["ready"] is False
        assert fl["verdict"].startswith("NOT_READY")
        assert fl["data"]["by_owner"]["categories"]


@pg
async def test_an_empty_book_is_explained_not_shown_as_zero():
    async with _conn() as conn:
        x = await CO.xavier(conn)
        e = await CO.execution(conn)
    for s in (x, e):
        if s["status"] == CO.EMPTY:
            assert "submission is disabled" in s["why"] or \
                "no funded" in s["why"]


async def test_a_failed_read_is_unavailable_by_name():
    async def boom(conn):
        raise RuntimeError("down")
    got = await CO._section(boom, None)
    assert got["status"] == CO.UNAVAILABLE and "RuntimeError" in got["why"]


class _Cfg:
    admin_token = "admin-secret-for-the-overview-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


def test_both_routes_need_the_command_credential(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    c = starlette.TestClient(A.app, raise_server_exceptions=False)
    assert c.get("/api/command/overview").status_code == 401
    assert c.get("/api/command/overview/page").status_code == 401
    r = c.get("/api/command/overview/page",
              headers={"X-Admin-Token": _Cfg.admin_token})
    assert r.status_code == 200 and "Operations Overview" in r.text
    assert "connect-src 'self'" in r.headers.get("content-security-policy", "")
