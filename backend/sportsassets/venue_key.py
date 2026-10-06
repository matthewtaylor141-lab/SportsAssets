"""THE Ed25519 SECRET AS THE VENUE SDK NEEDS IT, FROM THE FORM IT WAS GIVEN IN.

WHY THIS EXISTS (2026-10-06, render-ops run 37477349299). Every
mirror_shadow tick on sportsassets-workers logged

    mirror_shadow: positions walk failed
        (ValueError: The seed must be exactly 32 bytes long)

`polymarket_us.auth.create_auth_headers` does exactly one thing with the
secret: `base64.b64decode(secret)`, keep the first 32 bytes when the result
is 64 long, and hand the rest to `nacl.signing.SigningKey`, which raises that
ValueError for ANY other length. So the configured secret is a valid Ed25519
key in a form the SDK does not decode -- the SDK silently accepts only
"standard base64 of a 32-byte seed or a 64-byte seed||public key". The
forms an operator actually pastes, and what b64decode makes of them:

    64 hex chars (a 32-byte seed in hex)     -> 48 bytes  -> ValueError
    128 hex chars (64-byte key in hex)       -> 96 bytes  -> ValueError
    PKCS#8 DER base64 / PEM body             -> 48 bytes  -> ValueError
    url-safe base64 ('-', '_')               -> binascii error / wrong bytes
    base64 missing its '=' padding           -> binascii.Error
    surrounding quotes, whitespace, newlines -> wrong bytes or an error

`normalize_secret_key` turns every one of those into the form the SDK does
decode -- standard, padded base64 of the 32-byte seed -- WITHOUT changing the
credential: the same key signs, only its encoding changes. A string it cannot
read as an Ed25519 key is returned UNCHANGED, so the failure (and the SDK's
own message) is exactly what it was, never a different key.

THE VALUE IS NEVER LOGGED. `describe_secret_key` reports only the detected
FORMAT and decoded length -- the same facts an operator needs to fix the
secret's encoding, and nothing that narrows the key.
"""
from __future__ import annotations

import base64
import binascii
import re

SEED_LEN = 32
# DER prefix of a PKCS#8 OneAsymmetricKey holding an Ed25519 private key
# (RFC 8410 s.7): SEQUENCE, version 0, AlgorithmIdentifier 1.3.101.112,
# OCTET STRING { OCTET STRING (32) }. The seed is the 32 bytes after it.
PKCS8_ED25519_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
_HEX = re.compile(r"\A[0-9a-fA-F]+\Z")
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]+-----(.*?)-----END [A-Z0-9 ]+-----",
                  re.S)


def _clean(raw: str) -> str:
    s = (raw or "").strip()
    # one layer of quotes, as a shell / dashboard paste leaves them
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    m = _PEM.search(s)
    if m:
        s = m.group(1)
    # literal "\n" sequences from a JSON-escaped paste, then any whitespace
    s = s.replace("\\n", "")
    return re.sub(r"\s+", "", s)


def _b64(s: str) -> bytes | None:
    t = s.replace("-", "+").replace("_", "/")
    t += "=" * (-len(t) % 4)
    try:
        return base64.b64decode(t, validate=True)
    except (binascii.Error, ValueError):
        return None


def _public_of(seed: bytes) -> bytes | None:
    try:
        from nacl.signing import SigningKey
        return bytes(SigningKey(seed).verify_key)
    except Exception:                                          # noqa: BLE001
        return None


def _seed_from_bytes(b: bytes) -> tuple[bytes, str] | None:
    if len(b) == SEED_LEN:
        return b, "seed32"
    if len(b) == 2 * SEED_LEN:
        # libsodium / nacl layout is seed || public key, and that is what the
        # SDK assumes (it keeps the first half). Verified where possible; a
        # public || seed paste is recognised by the same check.
        head, tail = b[:SEED_LEN], b[SEED_LEN:]
        if _public_of(head) == tail:
            return head, "seed_pub64"
        if _public_of(tail) == head:
            return tail, "pub_seed64"
        return head, "expanded64"
    if len(b) == len(PKCS8_ED25519_PREFIX) + SEED_LEN and b.startswith(
            PKCS8_ED25519_PREFIX):
        return b[len(PKCS8_ED25519_PREFIX):], "pkcs8_der"
    return None


def _decode(raw: str) -> tuple[bytes, str] | None:
    s = _clean(raw)
    if not s:
        return None
    b = _b64(s)
    if b is not None:
        got = _seed_from_bytes(b)
        if got is not None:
            # a 64-char hex string is ALSO valid base64 (48 bytes); that only
            # lands here when those 48 bytes are a PKCS#8 key, which a hex
            # seed's alphabet cannot spell
            return got[0], "base64:" + got[1]
    if _HEX.match(s) and len(s) % 2 == 0:
        got = _seed_from_bytes(bytes.fromhex(s))
        if got is not None:
            return got[0], "hex:" + got[1]
    return None


def normalize_secret_key(raw: str | None) -> str:
    """Standard padded base64 of the 32-byte Ed25519 seed held in `raw`, or
    `raw` unchanged when it is empty or not readable as an Ed25519 key."""
    if not raw:
        return raw or ""
    got = _decode(raw)
    if got is None:
        return raw
    return base64.b64encode(got[0]).decode("ascii")


def describe_secret_key(raw: str | None) -> dict:
    """The secret's FORMAT, never its value: present, the detected encoding,
    whether the SDK would have read it unchanged, and whether it now will."""
    if not raw:
        return {"present": False}
    got = _decode(raw)
    sdk_raw_ok = False
    try:
        n = len(base64.b64decode(raw))
        sdk_raw_ok = n in (SEED_LEN, 2 * SEED_LEN)
    except (binascii.Error, ValueError):
        pass
    return {"present": True,
            "format": got[1] if got else "unrecognised",
            "sdk_reads_as_given": sdk_raw_ok,
            "usable_after_normalisation": got is not None}
