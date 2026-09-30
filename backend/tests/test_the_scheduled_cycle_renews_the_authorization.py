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


async def _book_read_recorded(conn, *, at):
    await _put(conn, "ext_pinnacle_last_cycle", {
        "at": at, "cycle_label": "EVALUATED_1_CANDIDATE",
        "fixture": "A BOOK READ RECORDED BY THE TEST, not by a venue read",
        "mapped_candidate_ledger": [
            {"us_market_slug": "aec-x-2026-09-26",
             "venue_clock": {"basis": "M1_LIVE_MARKET_DATA_SUBSCRIPTION",
                             "age_at_read_s": 1.2}}]})


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


@pg
@pytest.mark.asyncio
async def test_hourly_cycles_on_a_controlled_clock_keep_it_live_until_the_owners_expiry(  # noqa: E501
        monkeypatch):
    """CONTROLLED TIME. The scheduled cycle runs once an hour for 80 hours
    under a 3-day owner authorization. At every hour before the owner's expiry
    the system authorization is live; it renews only inside its last
    RENEW_WINDOW_S; it never extends past the owner's expiry; and after that
    expiry nothing renews it and it lapses."""
    asyncpg = pytest.importorskip("asyncpg")
    real = time.time
    clock = [real()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    # THE VENUE'S ACCOUNT READS for the scheduled reconciliation: a flat,
    # clean account (SYNTHETIC answers; the report code is real).
    from sportsassets import bettor_account_onboarding as ON
    from tests.test_the_discrepancy_report_reads_both_directions import (  # noqa: E402,E501
        _Venue)
    monkeypatch.setattr(ON, "_adapter", lambda mod=None: _Venue())
    conn = await asyncpg.connect(DSN)
    try:
        t0 = clock[0]
        owner = await _ready(conn, owner_expires_in=3 * 86400)
        await _bind(conn)
        owner_exp = float(owner["expires_at"])
        await _book_read_recorded(conn, at=t0)
        seen = []
        for h in range(0, 81):
            clock[0] = t0 + h * 3600.0
            before = await _get(conn, FA.AUTHORIZATION_KEY)
            svc, dig = await _cycle(conn, monkeypatch)
            ren = dig["authorization_renewal"]
            rec = await _get(conn, FA.AUTHORIZATION_KEY)
            seen.append((h, ren["reason"], ren.get("refusal")))
            # THE ENTRY LANE'S BOOK READ FOR THIS HOUR, written as its cycle
            # writes it (FIXTURE EVIDENCE: the entry loop is stopped in this
            # harness and reads no book). The renewal's recheck reads it.
            await _book_read_recorded(conn, at=clock[0])
            # NEVER BEYOND THE OWNER
            assert float(rec["expires_at"]) <= owner_exp + 1e-6
            if ren["reason"] == "RENEWED":
                # a reconciliation was recorded for this renewal, or one
                # recorded minutes earlier in the window was still fresh
                assert ren["reconciliation"]["reason"] in (
                    FA.N_RECON_RECORDED, FA.N_RECON_FRESH), ren
                # only inside the window of the record it replaced
                assert float(before["expires_at"]) - clock[0] <= \
                    FA.RENEW_WINDOW_S + 1e-6
            if clock[0] < owner_exp:
                assert float(rec["expires_at"]) > clock[0], (h, ren, seen[-4:])
            else:
                assert ren["renewed"] is False
                assert float(rec["expires_at"]) <= clock[0]
        reasons = [r for _, r, _ in seen]
        assert reasons.count("RENEWED") >= 2, seen
        assert set(reasons) <= {"RENEWED", FA.N_NOT_DUE, FA.N_EXPIRED,
                                FA.N_OWNER_EXPIRED}, seen
    finally:
        monkeypatch.setattr(time, "time", real)
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_renewal_fails_closed_when_the_last_cycle_read_no_book(
        monkeypatch):
    """THE OPERATIONAL CONSEQUENCE, PINNED. The recheck needs the previous
    cycle's book read on an established basis. A cycle that read no book (an
    entry loop stopped, or nothing eligible) leaves the recheck unmet: renewal
    is refused by name and the authorization is left to lapse -- acquisitions
    stop at its expiry; exits do not depend on it."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _ready(conn, owner_expires_in=3 * 86400)
        await _bind(conn)
        old = await _age_system_record(conn, expires_in=3600)
        svc, dig = await _cycle(conn, monkeypatch)      # this cycle reads no book
        old = await _age_system_record(conn, expires_in=3600)
        svc, dig = await _cycle(conn, monkeypatch)
        assert dig["authorization_renewal"]["reason"] == FA.N_RECHECK_FAILED
        assert dig["authorization_renewal"]["refusal"] == \
            "FUNDED_ACTIVATION_PREREQUISITES_NOT_MET"
        new = await _get(conn, FA.AUTHORIZATION_KEY)
        assert new["expires_at"] == pytest.approx(old["expires_at"])
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS)
        await conn.close()
