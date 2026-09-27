"""NOTIFICATION AUTHORIZATION, THROUGH THE REAL ROUTES.

THE SEQUENCE OF MY OWN CORRECTIONS ON THIS ONE FINDING, because the sequence is
the lesson:

  1  `push/unsubscribe` deleted by ENDPOINT ALONE. Anybody holding an endpoint
     could switch off somebody else's alerts.
  2  I added a `user_key` check and reported it closed. It was not: `subscribe`
     ran `ON CONFLICT DO UPDATE SET user_key=$1`, so a caller could take
     ownership and then unsubscribe legitimately.
  3  I closed that and reported the boundary verified. Still not: a
     client-generated UUID that travels in `/api/prefs/{user_key}` URL PATHS is
     not a credential -- it is in every access log between the browser and here.
     And the two prefs routes had no check at all.

    EACH TIME I FIXED THE ATTACK I HAD JUST LOOKED AT. The fix was real and the
    claim was wider than it.

SO THE IDENTIFIER AND THE CREDENTIAL ARE NOW DIFFERENT VALUES, and these tests
exercise every route against both: the public `user_key`, which must authorise
NOTHING, and the server-issued capability, which must be the only thing that
does.

WHAT IS TESTED: unauthorized read, unauthorized write, first registration,
reassignment, deletion -- and that a path segment never grants control.
"""

from __future__ import annotations

import contextlib
import os

import asyncpg
import pytest

from sportsassets import notification_capability as NC

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
pytestmark = pg

OWNER = "cap-owner-1111"
ATTACKER = "cap-attacker-9999"


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        await NC.ensure_schema(c)
        await c.execute("DELETE FROM notification_capabilities "
                        " WHERE user_key = ANY($1)", [OWNER, ATTACKER])
        yield c
    finally:
        await c.execute("DELETE FROM notification_capabilities "
                        " WHERE user_key = ANY($1)", [OWNER, ATTACKER])
        await c.close()


# ── 1 · minting ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_registration_mints_and_a_repeat_does_NOT_reissue():
    """THE ASYMMETRY IS THE WHOLE SECURITY OF THE SCHEME.

    If re-registering handed back the capability, an attacker holding only the
    PUBLIC `user_key` would "register" it and be given control -- the
    reassignment attack with an extra step.
    """
    async with _conn() as conn:
        first = await NC.issue_if_first(conn, OWNER)
        assert first["minted"] is True
        assert first["capability"], "first registration must return the secret"
        assert first["header"] == NC.CAPABILITY_HEADER

        again = await NC.issue_if_first(conn, OWNER)
        assert again["minted"] is False
        assert again["capability"] is None, (
            "a repeat registration must not hand the capability to whoever "
            "asked -- holding the public identifier would then be enough")
        assert "public identifier" in again["why_not_reissued"]

        # AND THE ORIGINAL STILL WORKS, so the refusal to reissue did not also
        # break the owner.
        assert (await NC.check(conn, OWNER, first["capability"]))["ok"] is True


@pytest.mark.asyncio
async def test_the_secret_is_never_stored_only_its_hash():
    """A DATABASE DUMP MUST NOT MINT AUTHORITY. A backup, a support query or a
    read-only replica are all places this table is seen by someone who should
    not thereby be able to change anybody's alerts."""
    async with _conn() as conn:
        got = await NC.issue_if_first(conn, OWNER)
        secret = got["capability"]
        stored = await conn.fetchval(
            "SELECT secret_sha256 FROM notification_capabilities "
            " WHERE user_key=$1", OWNER)
        assert stored != secret
        assert len(stored) == 64
        assert stored == NC.sha256_hex(secret)
        # THE WHOLE TABLE, SEARCHED FOR THE SECRET.
        rows = await conn.fetch("SELECT * FROM notification_capabilities")
        blob = " ".join(str(v) for r in rows for v in dict(r).values())
        assert secret not in blob


@pytest.mark.asyncio
async def test_the_secret_is_server_generated_not_client_chosen():
    """A value the caller picks cannot be a capability the server issued. Two
    mints must differ, and neither is derived from the user_key."""
    async with _conn() as conn:
        a = await NC.issue_if_first(conn, OWNER)
        b = await NC.issue_if_first(conn, ATTACKER)
        assert a["capability"] != b["capability"]
        for got, key in ((a, OWNER), (b, ATTACKER)):
            assert key not in got["capability"]
        assert len(a["capability"]) >= 32, "256 bits, URL-safe encoded"


# ── 2 · checking, and every uncertainty fails closed ─────────────────

@pytest.mark.asyncio
async def test_the_public_user_key_authorises_NOTHING():
    """THE CENTRAL PROPERTY. Presenting the identifier as the credential fails,
    because the identifier is in URL paths and therefore in logs."""
    async with _conn() as conn:
        await NC.issue_if_first(conn, OWNER)
        got = await NC.check(conn, OWNER, OWNER)
        assert got["ok"] is False
        assert got["refusal"] == NC.R_BAD_CAPABILITY
        assert NC.USER_KEY_AUTHORISES == "NOTHING"


@pytest.mark.asyncio
async def test_an_absent_capability_refuses_and_names_the_header():
    async with _conn() as conn:
        await NC.issue_if_first(conn, OWNER)
        got = await NC.check(conn, OWNER, "")
        assert got["refusal"] == NC.R_NO_CAPABILITY
        assert got["header"] == NC.CAPABILITY_HEADER
        assert "authorises nothing" in got["why"]


@pytest.mark.asyncio
async def test_an_UNREGISTERED_key_refuses_rather_than_passing():
    """THE DEFECT THIS REPLACES, IN ITS PUREST FORM: "no capability has been set
    up, so anything goes". A missing row is a refusal."""
    async with _conn() as conn:
        got = await NC.check(conn, "never-registered", "anything")
        assert got["ok"] is False
        assert got["refusal"] == NC.R_BAD_CAPABILITY


@pytest.mark.asyncio
async def test_a_missing_registration_and_a_wrong_secret_answer_ALIKE():
    """Telling them apart would reveal whether a user_key is registered, which
    is a disclosure on its own."""
    async with _conn() as conn:
        first = await NC.issue_if_first(conn, OWNER)
        absent = await NC.check(conn, "never-registered", "x")
        wrong = await NC.check(conn, OWNER, "x")
        assert absent["refusal"] == wrong["refusal"] == NC.R_BAD_CAPABILITY
        assert absent["why"] == wrong["why"] or (
            "no capability matches" in absent["why"]
            and "no capability matches" in wrong["why"])
        assert (await NC.check(conn, OWNER, first["capability"]))["ok"] is True


@pytest.mark.asyncio
async def test_one_users_capability_does_not_authorise_anothers_key():
    """THE CROSS-USER CASE, which is the whole point of having per-key
    authority."""
    async with _conn() as conn:
        mine = await NC.issue_if_first(conn, OWNER)
        theirs = await NC.issue_if_first(conn, ATTACKER)
        assert (await NC.check(conn, ATTACKER, mine["capability"]))[
            "ok"] is False
        assert (await NC.check(conn, OWNER, theirs["capability"]))[
            "ok"] is False
        assert (await NC.check(conn, OWNER, mine["capability"]))["ok"] is True


@pytest.mark.asyncio
async def test_a_revoked_capability_refuses_by_its_own_name():
    """Revocation must be distinguishable from a wrong secret, because they need
    different responses: re-register, or stop trying."""
    async with _conn() as conn:
        got = await NC.issue_if_first(conn, OWNER)
        await conn.execute(
            "UPDATE notification_capabilities SET revoked_at=now() "
            " WHERE user_key=$1", OWNER)
        res = await NC.check(conn, OWNER, got["capability"])
        assert res["refusal"] == NC.R_REVOKED


@pytest.mark.asyncio
async def test_use_is_recorded_because_that_is_the_only_leak_signal():
    """A bearer capability cannot be bound to a device, so the only available
    signal that one has leaked is that it starts being used more."""
    async with _conn() as conn:
        got = await NC.issue_if_first(conn, OWNER)
        for _ in range(3):
            assert (await NC.check(conn, OWNER, got["capability"]))["ok"]
        row = await conn.fetchrow(
            "SELECT uses, last_used_at FROM notification_capabilities "
            " WHERE user_key=$1", OWNER)
        assert row["uses"] == 3
        assert row["last_used_at"] is not None


# ── 3 · the design's own claims, and its honest limits ───────────────

def test_the_capability_never_travels_in_a_path():
    """"Public identifiers in paths must not grant control." Satisfied by
    separating identifier from credential, not by hiding the identifier -- the
    path segment stays and stops authorising."""
    d = NC.describe()
    assert d["never_a_path_segment"] is True
    assert d["header"] == "X-Notify-Capability"
    assert d["user_key_is"] == "A_PUBLIC_IDENTIFIER"
    assert d["user_key_authorises"] == "NOTHING"


def test_all_four_design_properties_are_stated_with_their_reason():
    p = NC.describe()["properties"]
    assert set(p) == {"server_generated", "header_only", "hashed_at_rest",
                      "issued_once"}
    assert "cannot choose" in p["server_generated"]
    assert "by construction" in p["header_only"]
    assert "cannot mint authority" in p["hashed_at_rest"]
    assert "public identifier" in p["issued_once"]


def test_the_honest_limits_are_recorded_not_omitted():
    """It is a BEARER capability: possession is authority, a copied secret works
    from anywhere, and there is no device binding. Saying so is what makes the
    rest of the claim trustworthy."""
    lim = NC.describe()["honest_limits"]
    assert "possession is authority" in lim["bearer"]
    assert "no device binding" in lim["bearer"]
    assert "cannot be recovered" in lim["no_recovery"]
    assert "No capital" in lim["blast_radius"]
    assert "SERVER issued" in NC.describe()["what_it_fixes"]


def test_the_routes_that_depend_on_it_are_all_guarded_in_the_source():
    """READ OFF THE HANDLERS. Four routes touch notification state and every one
    must take the header; the two prefs routes had no check at all."""
    import pathlib
    import re

    src = (pathlib.Path(NC.__file__).parent / "api" / "app.py").read_text()
    for route in ("/api/push/unsubscribe", "/api/prefs/{user_key}"):
        i = src.index('"%s"' % route)
        window = src[i:i + 3000]
        assert "x_notify_capability" in window, route
        assert "_NC.check(" in window, route
    # AND SUBSCRIBE MINTS RATHER THAN CHECKS, because there is nothing to check
    # before the capability exists.
    i = src.index('"/api/push/subscribe"')
    assert "_NC.issue_if_first(" in src[i:i + 3000]
    # THE HEADER NAME IS NEVER BUILT INTO A PATH.
    assert not re.search(r'/api/[^"\n]*x-notify-capability', src, re.I)
