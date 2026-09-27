"""THE NOTIFICATION CAPABILITY: minting, checking, and what it authorises.

WHAT THIS REPLACES. `user_key` -- a client-generated UUID -- was being used as
three things at once: the row's identifier, the credential that authorises
changing it, and a URL path segment. Those three roles cannot be held by one
value. As an identifier it must be shareable; as a credential it must be secret;
as a path segment it is logged by every intermediary.

    AND THE FAILURE WAS NOT ENTROPY. 122 random bits is plenty. "A UUID is weak"
    would have been the wrong finding and would have led to a longer UUID.

SO IDENTIFIER AND CREDENTIAL ARE NOW DIFFERENT VALUES.

    user_key            PUBLIC. Identifies the row. May appear in a path, a log,
                        a screenshot. AUTHORISES NOTHING.
    capability secret   SERVER-GENERATED, returned exactly once at first
                        registration, sent in the X-Notify-Capability HEADER,
                        stored only as a SHA-256 hash.

FOUR PROPERTIES, AND EACH ONE ANSWERS A SPECIFIC WAY THE OLD SCHEME FAILED:

  SERVER-GENERATED   the client cannot choose it, so it cannot claim another's
                     and cannot pick a weak one.
  HEADER-ONLY        it is never a path segment, so it is not written to access
                     logs, proxy logs, referrers or history by construction --
                     not by a rule someone has to remember.
  HASHED AT REST     reading this table yields no control. A database dump, a
                     backup, or a support query cannot mint authority.
  ISSUED ONCE        a second registration for the same key does NOT reissue.
                     Otherwise anyone could "re-register" and be handed the
                     capability, which is the reassignment attack with an extra
                     step.

WHAT IT DOES NOT CLAIM TO BE. It is a bearer capability, so possession is
authority: a copied secret works from anywhere, and there is no binding to a
device. That is inherent to the design and is the honest limit -- the thing it
fixes is that authority is now a secret the server issued and keeps hashed,
rather than an identifier the client invented and published in its own URLs.

BLAST RADIUS, UNCHANGED AND STILL SMALL. No capital, no order, no position, no
money, no market data. The worst outcome is somebody's alerts switched off or
their preferences read and rewritten. That is a real authorization defect at a
small size, and it is being fixed at that size.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: The header the capability travels in. NEVER a path segment, and named so a
#: reviewer can grep for it.
CAPABILITY_HEADER = "X-Notify-Capability"

#: Bytes of entropy in a minted secret. 32 bytes = 256 bits.
SECRET_BYTES = 32

R_NO_CAPABILITY = "NOTIFICATION_CAPABILITY_REQUIRED"
R_BAD_CAPABILITY = "NOTIFICATION_CAPABILITY_DOES_NOT_MATCH"
R_REVOKED = "NOTIFICATION_CAPABILITY_REVOKED"

#: What the identifier authorises, stated so it cannot drift back.
USER_KEY_AUTHORISES = "NOTHING"
USER_KEY_IS = "A_PUBLIC_IDENTIFIER"


def sha256_hex(secret: str) -> str:
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()


def mint() -> tuple[str, str]:
    """A new capability secret and its hash. The secret is returned ONCE.

    `secrets.token_urlsafe` rather than `uuid4`: the value is a credential, so it
    comes from the CSPRNG interface meant for credentials, and it is URL-safe
    only so it survives being pasted -- it is never put IN a URL.
    """
    s = secrets.token_urlsafe(SECRET_BYTES)
    return s, sha256_hex(s)


async def ensure_schema(conn) -> None:
    """Create the table if migration 130 has not been applied here.

    Additive and idempotent, so a caller on an older schema still gets the
    guarded behaviour rather than a 500 -- the guard must not be the thing that
    breaks when the schema is behind.
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS notification_capabilities ("
        " user_key text PRIMARY KEY,"
        " secret_sha256 text NOT NULL,"
        " issued_at timestamptz NOT NULL DEFAULT now(),"
        " last_used_at timestamptz,"
        " uses bigint NOT NULL DEFAULT 0,"
        " revoked_at timestamptz)")


async def issue_if_first(conn, user_key: str) -> dict:
    """FIRST REGISTRATION MINTS. A LATER ONE DOES NOT REISSUE.

    That asymmetry is the whole security of the scheme. If re-registering handed
    back the capability, an attacker holding only the public `user_key` would
    "register" it and be given control -- the reassignment attack with an extra
    step. So the insert is `ON CONFLICT DO NOTHING` and the secret is returned
    only when this call actually created the row.

    A caller that has lost its secret cannot recover it here, and that is
    correct: recovery needs an out-of-band identity this system does not have.
    """
    await ensure_schema(conn)
    secret, digest = mint()
    res = await conn.execute(
        "INSERT INTO notification_capabilities (user_key, secret_sha256) "
        "VALUES ($1,$2) ON CONFLICT (user_key) DO NOTHING",
        str(user_key), digest)
    minted = str(res).rsplit(" ", 1)[-1] == "1"
    return {
        "user_key": str(user_key),
        "minted": minted,
        # THE SECRET, AND ONLY ON THE CALL THAT CREATED IT.
        "capability": secret if minted else None,
        "header": CAPABILITY_HEADER,
        "why_not_reissued": None if minted else (
            "a capability already exists for this user_key. Reissuing on a "
            "repeat registration would hand control to anyone holding the "
            "public identifier"),
        "store_it": ("returned exactly once. It is not recoverable, because "
                     "recovery needs an identity this system does not have"),
    }


async def check(conn, user_key: str, supplied: str | None) -> dict:
    """DOES THIS CAPABILITY AUTHORISE THIS user_key?

    Compared with `hmac.compare_digest` on the HASHES, so the comparison is
    constant-time and the stored value is never the secret.

    FAIL CLOSED ON EVERY UNCERTAINTY. No row, no secret, a wrong secret and a
    revoked one all refuse. A missing row in particular must not be read as "no
    capability has been set up, so anything goes" -- that is the defect this
    replaces, in its purest form.
    """
    key = str(user_key or "")
    got = (supplied or "").strip()
    if not key:
        return {"ok": False, "refusal": R_NO_CAPABILITY,
                "why": "no user_key was supplied"}
    if not got:
        return {"ok": False, "refusal": R_NO_CAPABILITY,
                "header": CAPABILITY_HEADER,
                "why": ("this route requires the notification capability in "
                        "the %s header. The user_key is a public identifier "
                        "and authorises nothing" % CAPABILITY_HEADER)}
    await ensure_schema(conn)
    row = await conn.fetchrow(
        "SELECT secret_sha256, revoked_at FROM notification_capabilities "
        " WHERE user_key=$1", key)
    if row is None:
        # NO ROW IS A REFUSAL, NOT A PASS. And the answer is deliberately the
        # same as a wrong secret: distinguishing them would say whether a
        # user_key is registered, which is a disclosure on its own.
        return {"ok": False, "refusal": R_BAD_CAPABILITY,
                "why": ("no capability matches. A missing registration and a "
                        "wrong secret answer alike, because telling them apart "
                        "would reveal whether a user_key exists")}
    if row["revoked_at"] is not None:
        return {"ok": False, "refusal": R_REVOKED,
                "why": "this capability has been revoked"}
    if not hmac.compare_digest(str(row["secret_sha256"]), sha256_hex(got)):
        return {"ok": False, "refusal": R_BAD_CAPABILITY,
                "why": "no capability matches"}
    # USE IS RECORDED. A capability that starts being used from two places at
    # once is the only signal available that one has leaked.
    await conn.execute(
        "UPDATE notification_capabilities SET last_used_at=now(), "
        "       uses = uses + 1 WHERE user_key=$1", key)
    return {"ok": True, "refusal": None, "user_key": key}


def describe() -> dict:
    return {
        "header": CAPABILITY_HEADER,
        "never_a_path_segment": True,
        "secret_bytes": SECRET_BYTES,
        "stored_as": "SHA-256 hash; the secret is never stored",
        "issued": "exactly once, at first registration",
        "user_key_is": USER_KEY_IS,
        "user_key_authorises": USER_KEY_AUTHORISES,
        "refusals": [R_NO_CAPABILITY, R_BAD_CAPABILITY, R_REVOKED],
        "properties": {
            "server_generated": "the client cannot choose or claim another's",
            "header_only": ("not written to access logs, proxy logs, referrers "
                            "or history by construction"),
            "hashed_at_rest": ("a database dump cannot mint authority"),
            "issued_once": ("re-registration does not reissue, or holding the "
                            "public identifier would be enough"),
        },
        "honest_limits": {
            "bearer": ("possession is authority. A copied secret works from "
                       "anywhere and there is no device binding"),
            "no_recovery": ("a lost secret cannot be recovered, because "
                            "recovery needs an identity this system lacks"),
            "blast_radius": ("notification preferences only. No capital, "
                             "order, position, money or market data"),
        },
        "what_it_fixes": (
            "authority is now a secret the SERVER issued and keeps hashed, "
            "rather than an identifier the client invented and published in "
            "its own URLs"),
    }
