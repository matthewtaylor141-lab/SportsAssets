"""THE 24-HOUR SYSTEM AUTHORIZATION RENEWS ONLY UNDER THE OWNER'S, BOUNDED.

Without renewal, a multi-day owner authorization still stopped acquisitions
after one day. Renewal never creates the first authorization, renews only in
the last RENEW_WINDOW_S of a live, unrevoked record, only while the owner's
authorization is unrevoked, uninvalidated, dated and unexpired for exactly the
same account/venue/limit digest, rechecks every prerequisite by re-running
`authorize`, and never extends past the owner's expiry.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets import bettor_account_onboarding as _ON
from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_funded_activation as FA
from tests.test_funded_activation_is_a_real_path import (  # noqa: E402
    CLEAN, _LIMITS, _record_and_approve_limits, _seed_accounts,
    _seed_evidence)

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
KEYS = [_ON.RECONCILIATION_KEY, FA.LIMITS_KEY, FA.ACCOUNT_KEY,
        FA.AUTHORIZATION_KEY, FA.OWNER_AUTH_KEY, FA.RENEWAL_LOG_KEY]


async def _put(conn, key, val):
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb", key,
        json.dumps(val))


async def _get(conn, key):
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", key)
    return json.loads(raw) if isinstance(raw, str) else raw


async def _ready(conn, *, owner_expires_in):
    await _seed_accounts(conn)
    await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                       KEYS)
    await _record_and_approve_limits(conn, approved=True)
    await conn.execute("DELETE FROM external_valuations "
                       "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
    await _seed_evidence(conn, waived=False)
    eff = EX.effective_limits(_LIMITS)
    owner = {"account_id": CLEAN, "venue": "PMUS",
             "effective_digest": eff["effective_digest"], "by": "owner",
             "at": time.time(), "expires_at": time.time() + owner_expires_in,
             "revoked": False, "invalidated": False,
             "statement": "a test fixture, not a real authorisation"}
    await _put(conn, FA.OWNER_AUTH_KEY, owner)
    first = await FA.authorize(conn, account_id=CLEAN, venue="PMUS",
                               by="test")
    assert first["ok"] is True, first.get("refusal")
    return owner


async def _age_system_record(conn, *, expires_in):
    rec = await _get(conn, FA.AUTHORIZATION_KEY)
    rec["at"] = time.time() - 23 * 3600
    rec["expires_at"] = time.time() + expires_in
    await _put(conn, FA.AUTHORIZATION_KEY, rec)
    return rec


@pg
@pytest.mark.asyncio
async def test_renewal_rechecks_everything_and_never_outlives_the_owner():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        # the owner's authorization lasts 3 days: renewal gives a full day
        owner = await _ready(conn, owner_expires_in=3 * 86400)
        await _age_system_record(conn, expires_in=3600)
        got = await FA.renew_system_authorization(conn)
        assert got["renewed"] is True, got
        new = await _get(conn, FA.AUTHORIZATION_KEY)
        assert new["expires_at"] <= time.time() + EX.AUTHORIZATION_TTL_S + 5
        assert new["expires_at"] <= owner["expires_at"]
        # the owner's lasts only 5 more hours: renewal stops at it
        owner["expires_at"] = time.time() + 5 * 3600
        await _put(conn, FA.OWNER_AUTH_KEY, owner)
        await _age_system_record(conn, expires_in=1800)
        got2 = await FA.renew_system_authorization(conn)
        assert got2["renewed"] is True, got2
        new2 = await _get(conn, FA.AUTHORIZATION_KEY)
        assert new2["expires_at"] == pytest.approx(owner["expires_at"],
                                                   abs=1e-3)
        assert new2["capped_by_owner_expiry"] is True
        log = await _get(conn, FA.RENEWAL_LOG_KEY)
        assert [e["reason"] for e in log][-2:] == ["RENEWED", "RENEWED"]
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS)
        await conn.close()


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("case,reason", [
    ("not_due", FA.N_NOT_DUE),
    ("system_revoked", FA.N_REVOKED),
    ("system_expired", FA.N_EXPIRED),
    ("owner_revoked", FA.N_OWNER_REVOKED),
    ("owner_invalidated", FA.N_OWNER_REVOKED),
    ("owner_expired", FA.N_OWNER_EXPIRED),
    ("owner_no_expiry", FA.N_OWNER_NO_EXPIRY),
    ("owner_other_scope", FA.N_SCOPE),
    ("owner_absent", FA.N_NO_OWNER),
    ("prerequisite_fails", FA.N_RECHECK_FAILED),
])
async def test_renewal_refuses_by_name_and_leaves_the_record_to_expire(
        case, reason):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        owner = await _ready(conn, owner_expires_in=3 * 86400)
        rec = await _age_system_record(
            conn, expires_in=(10 * 3600 if case == "not_due" else
                              -60 if case == "system_expired" else 3600))
        if case == "system_revoked":
            rec["revoked"] = True
            await _put(conn, FA.AUTHORIZATION_KEY, rec)
        elif case == "owner_revoked":
            await _put(conn, FA.OWNER_AUTH_KEY, dict(owner, revoked=True))
        elif case == "owner_invalidated":
            await _put(conn, FA.OWNER_AUTH_KEY, dict(owner, invalidated=True))
        elif case == "owner_expired":
            await _put(conn, FA.OWNER_AUTH_KEY,
                       dict(owner, expires_at=time.time() - 1))
        elif case == "owner_no_expiry":
            o = dict(owner)
            o.pop("expires_at")
            await _put(conn, FA.OWNER_AUTH_KEY, o)
        elif case == "owner_other_scope":
            await _put(conn, FA.OWNER_AUTH_KEY,
                       dict(owner, effective_digest="0" * 64))
        elif case == "owner_absent":
            await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                               FA.OWNER_AUTH_KEY)
        elif case == "prerequisite_fails":
            # the limits lose their approval: authorize refuses on recheck
            lim = await _get(conn, FA.LIMITS_KEY)
            lim["approved"] = False
            await _put(conn, FA.LIMITS_KEY, lim)
        before = await _get(conn, FA.AUTHORIZATION_KEY)
        got = await FA.renew_system_authorization(conn)
        assert got["renewed"] is False
        assert got["reason"] == reason, got
        after = await _get(conn, FA.AUTHORIZATION_KEY)
        assert after["expires_at"] == before["expires_at"]
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_renewal_never_creates_the_first_authorization():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key = ANY($1)",
                           KEYS)
        got = await FA.renew_system_authorization(conn)
        assert got["renewed"] is False
        assert got["reason"] == FA.N_NO_SYSTEM_AUTH
        assert await _get(conn, FA.AUTHORIZATION_KEY) is None
    finally:
        await conn.close()
