"""THE PAPER ACCOUNT ROUTE AND THE LIVE STREAM (api.command_paper).

  * GET /api/command/paper/account returns the ONE derived-figures function's
    output plus the latest ledger entries, labelled LIVE MARKET DATA /
    SIMULATED EXECUTION, with last_updated_at; every read route refuses
    without the COMMAND credential.
  * The stream publishes ONLY COMMITTED ledger entries, in sequence, each
    with its sequence number, the entry, the recomputed balances and
    committed_at; an uncommitted reservation is never published; a reconnect
    with Last-Event-ID replays from that sequence.
Synthetic data in a scratch database; the stream is driven on a test account.
"""
from __future__ import annotations

import contextlib
import json

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.api import command_paper as CP

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def _frames(chunks):
    out = []
    for c in chunks:
        ev = {"id": None}
        for line in c.strip().splitlines():
            k, _, v = line.partition(": ")
            ev[k] = v
        ev["data"] = json.loads(ev["data"])
        out.append(ev)
    return out


async def _collect(conn, **kw):
    @contextlib.asynccontextmanager
    async def acquire():
        yield conn

    async def nosleep(_):
        return None
    return _frames([c async for c in CP.stream_events(
        acquire, sleep=nosleep, **kw)])


@pg
async def test_the_stream_publishes_committed_entries_in_sequence_and_replays_on_reconnect():
    conn = await H.connect()
    writer = await H.connect()
    try:
        a = await H.new_account(conn, "stream")
        acct = a["account_id"]
        first = await _collect(conn, last_event_id=None, max_polls=1,
                               account_id=acct)
        assert first[0]["event"] == "snapshot"
        snap = first[0]["data"]
        assert snap["balances"]["cash_usd"] == 500000.0
        assert snap["data_label"] == "LIVE MARKET DATA / SIMULATED EXECUTION"
        assert snap["last_updated_at"] is not None
        start = snap["sequence"]
        # AN UNCOMMITTED RESERVATION IS NEVER PUBLISHED
        tx = writer.transaction()
        await tx.start()
        o = H.order(a, key="st-1", qty=1000, limit=0.5, slug=acct + ":m",
                    at=H.T0)
        got = await L.submit_order(writer, o, fee_fn=H.zero_fee, now=H.T0)
        assert got["ok"], got
        mid = await _collect(conn, last_event_id=start, max_polls=1,
                             account_id=acct)
        assert mid == []
        await tx.commit()
        after = await _collect(conn, last_event_id=start, max_polls=1,
                               account_id=acct)
        assert [f["event"] for f in after] == ["ledger"]
        e = after[0]["data"]
        assert e["entry"]["kind"] == "ORDER_SUBMITTED"
        assert e["running_balances"] == {"cash_usd": 500000.0,
                                         "reserved_usd": 500.0,
                                         "available_usd": 499500.0}
        assert e["balances"]["available_usd"] == 499500.0
        assert e["committed_at"] and int(after[0]["id"]) == e["sequence"]
        # two more committed entries, then a reconnect replays from `start`
        await H.observe(writer, acct + ":m", H.T0 + 3, offers=[(0.5, 400)])
        from sportsassets import bettor_paper_simulator as SIM
        await SIM.simulate_order(writer, got["order"]["order_id"],
                                 now=H.T0 + 4, fee_fn=H.zero_fee)
        replay = await _collect(conn, last_event_id=start, max_polls=1,
                                account_id=acct)
        seqs = [f["data"]["sequence"] for f in replay]
        assert seqs == sorted(seqs) and len(seqs) == 3
        assert [f["data"]["entry"]["kind"] for f in replay] == [
            "ORDER_SUBMITTED", "FILL", "RESERVATION_RELEASED"]
        tail = await _collect(conn, last_event_id=seqs[1], max_polls=1,
                              account_id=acct)
        assert [f["data"]["sequence"] for f in tail] == [seqs[2]]
        assert tail[0]["data"]["balances"]["cash_usd"] == 499800.0
    finally:
        await writer.close()
        await conn.close()


# ── the routes ──────────────────────────────────────────────────────
class _Cfg:
    admin_token = "admin-secret-for-the-paper-routes-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


def _client(monkeypatch):
    import asyncpg
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    pools = {}

    async def _pool():
        import asyncio
        loop = asyncio.get_running_loop()
        if loop not in pools:
            pools[loop] = await asyncpg.create_pool(H.DSN, min_size=1,
                                                    max_size=2)
        return pools[loop]
    monkeypatch.setattr(CP, "_pool", _pool)
    app = FastAPI()
    app.include_router(CP.router)
    return TestClient(app, raise_server_exceptions=False)


ROUTES = ("/api/command/paper/account", "/api/command/paper/session",
          "/api/command/paper/derek", "/api/command/paper/xavier",
          "/api/command/paper/audrey", "/api/command/paper/stream")


@pg
def test_every_paper_route_needs_the_command_credential_and_reads_sections(monkeypatch):
    c = _client(monkeypatch)
    for path in ROUTES:
        assert c.get(path).status_code == 401, path
    hdr = {"X-Admin-Token": _Cfg.admin_token}
    r = c.get("/api/command/paper/account", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data_label"] == "LIVE MARKET DATA / SIMULATED EXECUTION"
    assert body["account"]["status"] == "OK"
    acct = body["account"]["data"]
    assert acct["account_id"] == L.ACCOUNT_ID
    assert acct["starting_cash_usd"] == 500000.0
    for k in ("cash_usd", "reserved_usd", "available_usd", "total_equity_usd",
              "realized_pnl_usd", "unrealized_pnl_usd", "open_positions",
              "last_updated_at", "labels"):
        assert k in acct, k
    assert body["ledger"]["status"] == "OK"
    assert body["ledger"]["data"][-1]["kind"] == "INITIAL_FUNDING"
    assert body["last_updated_at"] is not None
    for path in ROUTES[1:-1]:
        r = c.get(path, headers=hdr)
        assert r.status_code == 200, (path, r.text)
        body = r.json()
        assert body["data_label"] == L.DATA_LABEL
        for k, v in body.items():
            if isinstance(v, dict) and "status" in v:
                assert v["status"] in ("OK", "EMPTY", "UNAVAILABLE"), (path, k)
                if v["status"] != "OK":
                    assert v["why"], (path, k)
