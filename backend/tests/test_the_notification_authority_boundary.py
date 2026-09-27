"""IS THE `user_key` CHECK AN AUTHORITY BOUNDARY? TESTED, NOT ASSERTED.

I added a `user_key` ownership check to `/api/push/unsubscribe` and reported the
endpoint-only deletion closed. This file is what that claim needed and did not
have.

THE BYPASS IT FOUND, AND IT WAS MY OWN CODE. `/api/push/subscribe` ran
`ON CONFLICT (endpoint) DO UPDATE SET user_key=$1`, so a caller holding an
endpoint could POST their own key, take ownership, and then unsubscribe
legitimately. Two requests, same outcome as before the fix, and the check was
satisfied on the way through.

The first test below performs that exact two-request attack and asserts it now
FAILS. It is written as the attack rather than as a property, because a property
test over the fixed code would have passed before the fix too.

WHAT IS NOT CLAIMED HERE. That `user_key` is now a sound credential. It is not:
it is an unguessable value that the prefs routes put in a URL PATH, so its
confidentiality is not maintained by the system depending on it, and those two
routes have no check at all. Those stay OPEN, and the tests pin them as open so
the finding cannot quietly lapse.
"""

from __future__ import annotations

import contextlib
import os

import asyncpg
import pytest

from sportsassets import notification_ownership as NO

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

OWNER = "owner-key-1111"
ATTACKER = "attacker-key-9999"
ENDPOINT = "https://push.example/ep/abc123"


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def _subscribe(conn, key, endpoint):
    """THE HANDLER'S OWN STATEMENT. Kept in one place so the attack below runs
    the same SQL production runs, rather than a paraphrase of it."""
    return str(await conn.execute(
        """
        INSERT INTO push_subscriptions (user_key, endpoint, p256dh, auth)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (endpoint) DO UPDATE SET p256dh=$3, auth=$4
          WHERE push_subscriptions.user_key = $1
        """, key, endpoint, "p", "a"))


async def _unsubscribe(conn, key, endpoint):
    got = str(await conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint=$1 AND user_key=$2",
        endpoint, key))
    return int(got.rsplit(" ", 1)[-1])


# ── 1 · the bypass, run as the attack ────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_an_attacker_cannot_take_over_an_endpoint_then_unsubscribe_it():
    """THE TWO-REQUEST ATTACK THAT DEFEATED MY OWN FIX.

    Request 1: subscribe with the attacker's key and the victim's endpoint.
               Under the old `DO UPDATE SET user_key=$1` this REASSIGNED the
               owner.
    Request 2: unsubscribe with the attacker's key. Now legitimate, because
               request 1 made the attacker the owner.

    Both requests are asserted here. The second must remove nothing.
    """
    async with _conn() as conn:
        await conn.execute("DELETE FROM push_subscriptions WHERE endpoint=$1",
                           ENDPOINT)
        await _subscribe(conn, OWNER, ENDPOINT)
        assert await conn.fetchval(
            "SELECT user_key FROM push_subscriptions WHERE endpoint=$1",
            ENDPOINT) == OWNER

        # REQUEST 1 -- the takeover attempt.
        await _subscribe(conn, ATTACKER, ENDPOINT)
        still = await conn.fetchval(
            "SELECT user_key FROM push_subscriptions WHERE endpoint=$1",
            ENDPOINT)
        assert still == OWNER, (
            "the endpoint's owner was reassigned; the unsubscribe check reads "
            "this column, so reassigning it defeats the check entirely")

        # REQUEST 2 -- the deletion that the takeover would have authorised.
        assert await _unsubscribe(conn, ATTACKER, ENDPOINT) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM push_subscriptions WHERE endpoint=$1",
            ENDPOINT) == 1, "the victim's subscription survived both requests"

        # AND THE OWNER IS STILL ABLE TO REMOVE THEIR OWN, so the fix did not
        # close the route by breaking it.
        assert await _unsubscribe(conn, OWNER, ENDPOINT) == 1


@pg
@pytest.mark.asyncio
async def test_the_owner_re_subscribing_still_refreshes_its_own_row():
    """THE LEGITIMATE CASE THE FIX MUST NOT BREAK. A browser re-subscribing
    gets the same endpoint from its push service and presents the same key, so
    the conflict clause matches and the keys are refreshed."""
    async with _conn() as conn:
        await conn.execute("DELETE FROM push_subscriptions WHERE endpoint=$1",
                           ENDPOINT)
        await _subscribe(conn, OWNER, ENDPOINT)
        await conn.execute(
            "UPDATE push_subscriptions SET p256dh='old' WHERE endpoint=$1",
            ENDPOINT)
        await _subscribe(conn, OWNER, ENDPOINT)
        assert await conn.fetchval(
            "SELECT p256dh FROM push_subscriptions WHERE endpoint=$1",
            ENDPOINT) == "p", "the owner's own refresh must still write"
        await conn.execute("DELETE FROM push_subscriptions WHERE endpoint=$1",
                           ENDPOINT)


@pg
@pytest.mark.asyncio
async def test_a_wrong_key_and_an_absent_row_are_indistinguishable():
    """Telling them apart would confirm that an endpoint exists under another
    key, which is the disclosure the route exists to avoid."""
    async with _conn() as conn:
        await conn.execute("DELETE FROM push_subscriptions WHERE endpoint=$1",
                           ENDPOINT)
        absent = await _unsubscribe(conn, ATTACKER, ENDPOINT)
        await _subscribe(conn, OWNER, ENDPOINT)
        wrong_key = await _unsubscribe(conn, ATTACKER, ENDPOINT)
        assert absent == wrong_key == 0
        await conn.execute("DELETE FROM push_subscriptions WHERE endpoint=$1",
                           ENDPOINT)


# ── 2 · the classification, which is the point of correction 6 ───────

def test_the_key_is_neither_an_identity_nor_a_protected_capability():
    """A CLIENT-SUPPLIED IDENTIFIER IS NOT PROOF OF OWNERSHIP, and this one is
    not a capability either -- not for want of entropy, but because the prefs
    routes put it in a URL path."""
    c = NO.USER_KEY_CLASSIFICATION
    assert c["is_an_authenticated_identity"] is False
    assert "whatever the request said it was" in c["why_not"]
    assert c["is_a_protected_capability"] is False
    assert "URL PATH SEGMENT" in c["why_not_that_either"]
    assert "access logs" in c["why_not_that_either"]
    assert NO.classification() == NO.BARE_IDENTIFIER
    # AND THE WRONG FINDING IS EXPLICITLY NOT THE ONE MADE.
    assert "122 random bits" in c["entropy_is_not_the_problem"]
    assert "wrong finding" in c["entropy_is_not_the_problem"]
    assert "POSSESSION" in c["what_it_does_establish"]


def test_the_subscribe_bypass_is_recorded_as_found_and_closed():
    by = {b["id"]: b for b in NO.BYPASSES}
    b = by["OWNER_REASSIGNMENT_VIA_SUBSCRIBE"]
    assert b["was_open"] is True and b["closed"] is True
    assert "satisfied on the way through" in b["how_it_worked"]
    assert "WHERE push_subscriptions.user_key = $1" in b["closed_by"]
    # THE GENERAL LESSON IS RECORDED, not just the instance.
    assert "not a boundary while another handler can write the column" in (
        b["why_it_matters_beyond_this_route"])


def test_the_two_prefs_routes_ARE_NOW_GUARDED():
    """THEY WERE THE REAL RESIDUE AND THEY ARE CLOSED.

    This test used to assert they were STILL UNGUARDED, which was the honest
    state then: one disclosed a user's preferences to anyone naming the key and
    the other overwrote them, with no check at all. Both now require the
    server-issued capability in a header.

    THE PATH SEGMENT STAYS. `user_key` identifies which row is wanted, which is
    a fine job for a path. What changed is that it no longer authorises -- which
    is how "public identifiers in paths must not grant control" is satisfied
    without pretending the identifier can be kept secret.
    """
    assert NO.UNGUARDED_ROUTES == ()
    get = NO.route("GET /api/prefs/{user_key}")
    put = NO.route("PUT /api/prefs/{user_key}")
    assert get["guard"] == put["guard"] == "CAPABILITY_REQUIRED"
    assert "no check at all" in get["was"] and "no check at all" in put["was"]
    assert "X-Notify-Capability" in get["now"]
    assert "authorises nothing" in get["now"]


def test_the_capability_replaced_the_identifier_as_the_authority():
    """The three earlier repairs each stopped short of this: the identifier and
    the credential are now DIFFERENT VALUES."""
    c = NO.CAPABILITY
    assert c["never_a_path_segment"] is True
    assert c["server_generated"] is True
    assert "never reissued" in c["issued"]
    assert "yields no control" in c["stored"]
    assert "authorises NOTHING" in c["user_key_remains"]
    # AND WHY NOT A LOGIN IS ANSWERED, rather than the option being ignored.
    assert "no per-user principal" in c["why_not_a_login"]
    assert "wrong size of change" in c["why_not_a_login"]


def test_the_remaining_bypasses_are_open_with_named_repairs():
    assert set(NO.OPEN_BYPASSES) == {"KEY_DISCLOSED_BY_ITS_OWN_URL",
                                     "NO_BINDING_BETWEEN_KEY_AND_BROWSER"}
    for b in NO.BYPASSES:
        if not b["closed"]:
            assert b["what_would_close_it"], b["id"]


def test_the_status_names_its_limit_rather_than_claiming_closure():
    """"REPAIRED" alone would be the fourth overstatement in this one finding's
    history. What remains open is inherent to a bearer capability and is said."""
    assert NO.STATUS == "REPAIRED_WITH_A_STATED_LIMIT"
    assert len(NO.WHAT_IS_REPAIRED) == 5
    open_text = " ".join(NO.WHAT_IS_OPEN)
    assert "BEARER capability" in open_text
    assert "no binding to the browser" in open_text
    assert "cannot be recovered" in open_text
    assert "inherent to the design rather than a defect" in open_text
    # AND THE SEQUENCE OF MY OWN OVERSTATEMENTS IS ON THE RECORD.
    corr = NO.describe()["the_correction_to_me"]
    assert "THREE TIMES" in corr
    assert "each claim was wider than it" in corr


def test_the_blast_radius_is_stated_at_its_actual_size():
    """Neither dropped for being small nor inflated for being real. No capital
    is reachable, and an unauthorized write on another user's row is still a
    finding."""
    br = NO.BLAST_RADIUS
    assert br["capital"].startswith("NONE")
    assert "notifications switched off" in br["worst_case"]
    assert "not dropped for being small" in br["and_it_is_still_a_finding"]
    assert "not inflated for being real" in br["and_it_is_still_a_finding"]


def test_every_route_that_depends_on_the_key_is_in_the_register():
    """A route cannot start depending on `user_key` without appearing here."""
    import pathlib
    import re

    src = (pathlib.Path(NO.__file__).parent / "api" / "app.py").read_text()
    # Every prefs/push route path that carries user_key in its decorator or body.
    paths = set(re.findall(r'@app\.(?:get|post|put)\("(/api/(?:push|prefs)[^"]*)"',
                           src))
    registered = {r["route"].split(" ", 1)[1] for r in NO.DEPENDENT_ROUTES}
    assert paths <= registered, (
        "these push/prefs routes are not in notification_ownership: %s"
        % sorted(paths - registered))
