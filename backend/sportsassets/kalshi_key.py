"""KALSHI API KEYS: THE TWO DOCUMENTED CLASSES, LOADED FROM THE BYTES AS
GIVEN, SIGNED WITH THE ALGORITHM THE PARSED KEY CALLS FOR.

WHY THIS EXISTS (RC5, owner directive 2026-10-08: "Correctly support the
actual approved key classes and preserve credential bytes, including
trailing newlines"). Kalshi documents exactly two API key types
(docs.kalshi.com/getting_started/api_keys, captured at research/
kalshi_canonical_venue/REP_PRODUCTION_CONTRACT_2026-10-07/docs/
getting_started_api_keys.md):

    Ed25519 (recommended; the default)  Ed25519 (RFC 8032) signs the
                                        pre-sign text itself; Kalshi hands
                                        out PKCS#8 "BEGIN PRIVATE KEY"
    RSA (2048-bit)                      RSA-PSS, SHA-256, MGF1-SHA256, salt
                                        = digest length; PKCS#1 "BEGIN RSA
                                        PRIVATE KEY"

and warns: "The PEM header does not identify the key type: PKCS#8 RSA keys
... also begin with -----BEGIN PRIVATE KEY-----. Determine the type from the
parsed key." The pre-sign text, headers and permissions are the same for
both types.

PRODUCTION (pm-acceptance run 37738089957, RC4 7fd4574e): KALSHI_PRIVATE_KEY_
PEM on sportsassets-api and sportsassets-workers is an Ed25519 PKCS#8 PEM,
119 characters with its trailing newline (red-team CREDENTIAL_CLASSES
by_process KALSHI = ED25519_PEM), and the dedicated plane's signed GET
/trade-api/v2/account/limits with that key answers 200 (KALSHI_HEALTH
account_limits_status OK). Before this module three places disagreed about
that one key:
  * kalshi_ws (WebSocket handshake, account limits): Ed25519 or RSA-PSS --
    the one correct signer, which is why the plane authenticates;
  * kalshi_venue (the REST client, its account reads and its order signer):
    RSA only -- load_private_key raised "not an RSA private key", so
    credential_state called a Kalshi-recommended key UNREADABLE;
  * redteam/controls: classified by PEM SHAPE and expected RSA only, so
    CREDENTIAL_CLASSES was RED (CREDENTIAL_CLASS_MISMATCH:KALSHI:ED25519_PEM).
Every one of them now loads, classifies and signs here.

BYTES AS GIVEN. A configured value is parsed EXACTLY as configured first --
every byte, the trailing newline included. Only a value that does not load
as given is repaired, and only by the mechanical repairs an env-var paste
needs (literal "\\n" escapes; a PEM squashed onto one line; base64 of the
whole PEM). A repair changes how the text is laid out, never the key, and
`describe` names the form that loaded. Nothing here strips, re-wraps or
re-encodes a value that already loads.

WHAT THIS MODULE IS NOT. It holds no transport and imports no client: it
cannot send anything. The order client is kalshi_venue (blocked from import
in the market plane, gated by its own submission_gate; Kalshi live money is
NOT ACTIVATED). And it claims no key SCOPE: Kalshi documents no read-only key
class, so every Kalshi key is account-wide. A process that only reads is
read-only by its own code (market_plane_guard, the import-closure tests),
never by the key.

THE VALUE IS NEVER RETURNED, LOGGED OR DESCRIBED: refusals are names,
`describe` reports the class, the form that loaded and (RSA) the modulus
size -- nothing that narrows the key.
"""
from __future__ import annotations

import base64
import binascii
import re

VERSION = "KALSHI_KEY_V1"

KEY_ED25519 = "ED25519"
KEY_RSA = "RSA"
#: the documented key types, recommended first (getting_started_api_keys)
APPROVED_KEY_TYPES = (KEY_ED25519, KEY_RSA)
DOCS = ("research/kalshi_canonical_venue/REP_PRODUCTION_CONTRACT_2026-10-07/"
        "docs/getting_started_api_keys.md")

# ── refusals (the message of KeyRefused; never a value) ───────────────
R_KEY_ABSENT = "KALSHI_PRIVATE_KEY_ABSENT"
#: nothing in the value (as given, or in any repaired form) is a PEM block
R_KEY_NOT_PEM = "KALSHI_KEY_NOT_PEM"
#: a PEM block is there but no form of it parses as an unencrypted key
R_KEY_UNLOADABLE = "KALSHI_PRIVATE_KEY_UNLOADABLE"
#: a key parsed, but it is neither of the two documented Kalshi key types
R_KEY_TYPE_NOT_DOCUMENTED = "KALSHI_KEY_TYPE_NOT_ED25519_OR_RSA"

# ── the form a value loaded in ────────────────────────────────────────
FORM_AS_GIVEN = "AS_GIVEN"
FORM_ESCAPED_NEWLINES = "ESCAPED_NEWLINES_REPAIRED"
FORM_ONE_LINE_REWRAPPED = "ONE_LINE_PEM_REWRAPPED"
FORM_BASE64_OF_PEM = "BASE64_OF_PEM_DECODED"

_ONE_LINE = re.compile(
    r"^\s*['\"]?(-----BEGIN [A-Z0-9 ]+-----)(.*?)(-----END [A-Z0-9 ]+-----)"
    r"['\"]?\s*$", re.S)


class KeyRefused(ValueError):
    """The value is not a Kalshi API private key this code can sign with.
    A ValueError (callers that caught the old loaders' ValueError still
    do); the message and `.code` are the refusal name, never a value."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _present(raw) -> bool:
    if raw is None:
        return False
    if isinstance(raw, (bytes, bytearray)):
        return bool(bytes(raw).strip())
    return bool(str(raw).strip())


def _rewrap(text: str) -> str | None:
    """A PEM whose newlines were collapsed (to spaces or to nothing),
    re-wrapped at 64 columns between its armour lines; None when the text
    is not one such block."""
    m = _ONE_LINE.match(text)
    if not m:
        return None
    head, body, tail = m.groups()
    body = re.sub(r"\s+", "", body)
    if not body:
        return None
    wrapped = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"{head}\n{wrapped}\n{tail}\n"


def _candidates(raw):
    """(form, bytes) in the order they are tried: the value EXACTLY as
    configured first, then each mechanical repair of it. A repair is
    yielded only when it differs from what came before."""
    if isinstance(raw, (bytes, bytearray)):
        given = bytes(raw)
        try:
            text = given.decode("utf-8")
        except UnicodeDecodeError:
            yield FORM_AS_GIVEN, given
            return
    else:
        text = str(raw)
        given = text.encode("utf-8")
    yield FORM_AS_GIVEN, given
    seen = {given}
    t = text
    if "\\n" in t:
        t = t.replace("\\r\\n", "\n").replace("\\n", "\n")
        b = t.encode("utf-8")
        if b not in seen:
            seen.add(b)
            yield FORM_ESCAPED_NEWLINES, b
    if "\n" not in t.strip():
        w = _rewrap(t)
        if w is not None and w.encode("utf-8") not in seen:
            seen.add(w.encode("utf-8"))
            yield FORM_ONE_LINE_REWRAPPED, w.encode("utf-8")
    if "-----BEGIN" not in t:
        squashed = "".join(t.split()).strip("'\"")
        try:
            dec = base64.b64decode(squashed, validate=True)
        except (binascii.Error, ValueError):
            dec = b""
        if b"-----BEGIN" in dec and dec not in seen:
            yield FORM_BASE64_OF_PEM, dec


def key_type(key) -> str | None:
    """KEY_ED25519 / KEY_RSA for a parsed private OR public key; None for
    any other algorithm."""
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
    if isinstance(key, (ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey)):
        return KEY_ED25519
    if isinstance(key, (rsa.RSAPrivateKey, rsa.RSAPublicKey)):
        return KEY_RSA
    return None


def load(raw) -> tuple:
    """(private key, form). RAISES KeyRefused (a ValueError) naming why
    when the value is absent, holds no PEM, does not parse, or parses as a
    key type Kalshi does not document."""
    if not _present(raw):
        raise KeyRefused(R_KEY_ABSENT)
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key)
    saw_pem = False
    for form, data in _candidates(raw):
        if b"-----BEGIN" not in data:
            continue
        saw_pem = True
        try:
            key = load_pem_private_key(data, password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            continue
        if key_type(key) is None:
            # parsed, and not a documented Kalshi key: a repair cannot
            # change the algorithm, so no further form is tried
            raise KeyRefused(R_KEY_TYPE_NOT_DOCUMENTED)
        return key, form
    raise KeyRefused(R_KEY_UNLOADABLE if saw_pem else R_KEY_NOT_PEM)


def load_private_key(raw):
    """The parsed key (Ed25519 or RSA); KeyRefused otherwise."""
    return load(raw)[0]


def sign(private_key, message: bytes) -> str:
    """base64 signature over `message` with the algorithm the KEY calls
    for: Ed25519 signs the message itself; RSA is RSA-PSS with SHA-256,
    MGF1-SHA256 and salt = digest length. Any other key is refused by name
    before anything is signed."""
    kt = key_type(private_key)
    if kt == KEY_ED25519:
        sig = private_key.sign(message)
    elif kt == KEY_RSA:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        sig = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
    else:
        raise KeyRefused(R_KEY_TYPE_NOT_DOCUMENTED)
    return base64.b64encode(sig).decode("ascii")


def verify(public_key, signature_b64: str, message: bytes) -> bool:
    """Does this signature verify under this public key's own algorithm?
    For tests and self-checks; False (never a raise) on any mismatch."""
    from cryptography.exceptions import InvalidSignature
    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return False
    kt = key_type(public_key)
    try:
        if kt == KEY_ED25519:
            public_key.verify(sig, message)
        elif kt == KEY_RSA:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            public_key.verify(
                sig, message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256())
        else:
            return False
        return True
    except InvalidSignature:
        return False


def describe(raw) -> dict:
    """The key's CLASS, never its value: present, loadable, the documented
    type, the form it loaded in (AS_GIVEN unless a paste repair was
    needed), the RSA modulus size, or the refusal. Never raises."""
    if not _present(raw):
        return {"present": False, "loadable": False, "type": None,
                "form": None, "refusal": R_KEY_ABSENT}
    try:
        key, form = load(raw)
    except KeyRefused as exc:
        return {"present": True, "loadable": False, "type": None,
                "form": None, "refusal": exc.code}
    except Exception as exc:                                    # noqa: BLE001
        return {"present": True, "loadable": False, "type": None,
                "form": None, "refusal": R_KEY_UNLOADABLE,
                "error": type(exc).__name__}
    kt = key_type(key)
    out = {"present": True, "loadable": True, "type": kt, "form": form,
           "refusal": None}
    if kt == KEY_RSA:
        out["bits"] = key.key_size
    return out
