"""THE SCHEDULED CYCLE CALLS THE BOUNDED RENEWAL, AND REPORTS IT.

`renew_system_authorization` existed and nothing scheduled called it, so a
multi-day owner authorization still stopped acquisitions after one day. The
funded service now calls it first on every cycle, before anything that can
raise, and the heartbeat's servicing digest carries the attempt.

Driven through `ext_pinnacle_loop.cycle()` with only the venue transport and
the entry loop's run control substituted.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets import bettor_funded_activation as FA
from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    _live, _stopped, _transport)
from tests.test_the_system_authorization_renews_only_under_the_owners import (  # noqa: E402,E501
    KEYS, _age_system_record, _get, _put, _ready)

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


async def _bind(conn):
    rec = await _get(conn, FA.AUTHORIZATION_KEY)
    await _put(conn, FA.ACCOUNT_KEY, {"account_id": rec["account_id"],
                                      "venue": rec["venue"],
                                      "approved": True})


async def _cycle(conn, monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L
    _transport(monkeypatch, bids=[])
    monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
    monkeypatch.setattr(L, "_running", lambda c: _stopped())
    monkeypatch.setattr(
        L, "book_currency_evidence",
        lambda slug=None: {"subscription": _live(), "revalidation": None})
    out = await L.cycle(conn)
    svc = out.get("funded_servicing")
    assert svc is not None, "the funded service did not run"
    return svc, L._servicing_digest(svc)


@pg
@pytest.mark.asyncio
async def test_the_cycle_renews_under_a_live_owner_authorization(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        owner = await _ready(conn, owner_expires_in=3 * 86400)
        await _bind(conn)
        old = await _age_system_record(conn, expires_in=3600)
        svc, dig = await _cycle(conn, monkeypatch)
        assert svc["authorization_renewal"]["renewed"] is True, \
            svc["authorization_renewal"]
        assert dig["authorization_renewal"]["reason"] == "RENEWED"
        new = await _get(conn, FA.AUTHORIZATION_KEY)
        assert new["expires_at"] > old["expires_at"]
        assert new["expires_at"] <= owner["expires_at"]
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_cycle_lets_it_expire_when_the_owner_revoked(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        owner = await _ready(conn, owner_expires_in=3 * 86400)
        await _bind(conn)
        old = await _age_system_record(conn, expires_in=3600)
        owner["revoked"] = True
        await _put(conn, FA.OWNER_AUTH_KEY, owner)
        svc, dig = await _cycle(conn, monkeypatch)
        assert svc["authorization_renewal"]["renewed"] is False
        assert dig["authorization_renewal"]["reason"] == FA.N_OWNER_REVOKED
        new = await _get(conn, FA.AUTHORIZATION_KEY)
        assert new["expires_at"] == pytest.approx(old["expires_at"])
        log = await _get(conn, FA.RENEWAL_LOG_KEY)
        assert log[-1]["reason"] == FA.N_OWNER_REVOKED
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS)
        await conn.close()
