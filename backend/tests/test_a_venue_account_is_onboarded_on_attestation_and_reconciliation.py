"""A REAL VENUE ACCOUNT ENTERS THE REGISTRY ON THE OWNER'S ATTESTATION AND
BECOMES ELIGIBLE ONLY BY THE FOUR RECONCILIATIONS.

The venue's balances payload names no account, so which account the deployed
key signs for is the OWNER's statement, recorded as an attestation. The
attestation establishes identity and nothing about accounting: the account is
eligible only when balances, positions, open orders and executions all
reconcile. A failed reconciliation writes nothing and the row stays paused.
The shadow desk book is refused by name.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_account_onboarding as ON
from tests.test_account_onboarding_is_earned import (  # noqa: E402
    _clean_venue, _Venue, _bal)

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "pmus-attested-test"
VENUE = "PMUS"
OPERATOR = "Owner Name"
STATEMENT = ("pmus-attested-test is my Polymarket US (PMUS) account and the "
             "deployed key belongs to it")


async def _drop(conn):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                       ON.ATTESTATION_KEY_PREFIX + ACCT)


def _kw(**over):
    base = dict(account_id=ACCT, venue=VENUE, statement=STATEMENT,
                confirm=ACCT, operator=OPERATOR)
    base.update(over)
    return base


@pg
@pytest.mark.asyncio
async def test_a_failed_reconciliation_registers_paused_and_writes_nothing():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop(conn)
        got = await ON.onboard_attested(
            conn, **_kw(), adapter=_Venue(fail_positions=True,
                                          balance=_bal(), orders=[]))
        assert got["ok"] is False and got["still_paused"] is True
        assert got["refusal"] == ON.R_NOT_RECONCILED
        assert got["wrote_eligibility"] is False
        row = await conn.fetchrow(
            "SELECT status, paused, accounting_status FROM "
            " bettor_desk_accounts WHERE account_id=$1", ACCT)
        assert dict(row) == {"status": "PENDING_VERIFICATION", "paused": True,
                             "accounting_status": "UNVERIFIED"}
        att = await ON.attestation_for(conn, ACCT)
        assert att["identity_evidence"] == "OWNER_ATTESTATION"
        assert att["attested_by"] == OPERATOR
        assert att["establishes_accounting"] is False
    finally:
        await _drop(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_four_clean_reconciliations_make_the_attested_account_eligible():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop(conn)
        got = await ON.onboard_attested(conn, **_kw(), adapter=_clean_venue())
        assert got["ok"] is True, got
        assert got["wrote_eligibility"] is True
        row = await conn.fetchrow(
            "SELECT status, paused, accounting_status FROM "
            " bettor_desk_accounts WHERE account_id=$1", ACCT)
        assert dict(row) == {"status": "ACTIVE", "paused": False,
                             "accounting_status": "RECONCILED"}
    finally:
        await _drop(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("over,refusal", [
    ({"account_id": ON.SHADOW_DESK_ACCOUNT,
      "confirm": ON.SHADOW_DESK_ACCOUNT}, ON.R_SHADOW_DESK),
    ({"confirm": "someone-else"}, ON.R_CONFIRM),
    ({"venue": "NOWHERE"}, ON.R_VENUE),
    ({"statement": "yes"}, ON.R_ATTESTATION),
    ({"statement": "this is my account on the venue, trust me"},
     ON.R_ATTESTATION),
])
async def test_the_onboarding_refuses_what_it_cannot_stand_behind(over,
                                                                  refusal):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _drop(conn)
        got = await ON.onboard_attested(conn, **_kw(**over),
                                        adapter=_clean_venue())
        assert got["ok"] is False and got["refusal"] == refusal, got
        n = await conn.fetchval(
            "SELECT count(*) FROM bettor_desk_accounts WHERE account_id=$1",
            over.get("account_id", ACCT))
        if refusal == ON.R_SHADOW_DESK:
            pass            # the shadow row may exist; it was not touched
        else:
            assert n == 0
    finally:
        await _drop(conn)
        await conn.close()


class _Cfg:
    admin_token = "admin-secret-for-the-onboarding-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


def test_the_route_needs_both_factors_and_takes_the_operator_from_settings(
        monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from sportsassets.api import app as A
    cfg = _Cfg()
    monkeypatch.setattr(A, "settings", lambda: cfg, raising=False)
    c = starlette.TestClient(A.app, raise_server_exceptions=False)
    route = "/api/admin/funded-account-onboard"
    body = {"account_id": ACCT, "venue": VENUE, "statement": STATEMENT,
            "confirm": ACCT, "operator": "someone typed this"}
    assert c.post(route, json=body).status_code == 401
    admin = {"X-Admin-Token": cfg.admin_token}
    r = c.post(route, json=body, headers=admin)
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == \
        "FUNDED_RESOLUTION_KEY_NOT_CONFIGURED"
    cfg.funded_resolution_key = "the-owners-resolution-key"
    cfg.funded_resolution_operator = OPERATOR
    r = c.post(route, json=body, headers=dict(admin, **{
        "X-Resolution-Key": "a-guess"}))
    assert r.status_code == 401
    import inspect
    src = inspect.getsource(A.admin_funded_account_onboard)
    assert '"funded_resolution_operator"' in src
    assert 'b.get("operator")' not in src
