"""PRODUCTION, READ ONLY. There is no order path in this file.

Authorized by the owner on 2026-09-19, overriding the standing "no
production credential" line, after production credentials were issued to
BettorToken LLC for an account that holds no funds. What is authorized is
a VERIFICATION READ: what the account is, what it may do, what it holds.
Nothing else.

WHY THIS IS A SEPARATE FILE FROM pmx_preprod_ops.

Not for tidiness. `pmx_preprod_ops.assert_preprod` refuses every
non-preprod host, and that guard is not weakened, parameterized or made
conditional to accommodate production -- the moment an environment
becomes a variable, a mistake becomes possible. Preprod code talks to
preprod. This file talks to production. Neither can become the other.

HOW "NO ORDER" IS ENFORCED, rather than promised:

  * `READ_ONLY_PATHS` is an allow-list. `request_for` refuses any path
    that is not in it, by name, before a socket is opened.
  * Every entry in it is a read. No insert, cancel, replace, preview or
    funding path appears anywhere in this module, and a test asserts
    their absence from the source text.
  * The credential slots are PMX_*, which only this lane reads. The
    preprod lane reads PMX_PREPROD_*. The prefix IS the attestation:
    an unprefixed slot is production, a prefixed one is preproduction,
    and neither lane can pick up the other's keys.

THE TWO AUDIENCES, as corrected on 2026-09-19: the `aud` CLAIM inside the
signed assertion is Auth0's TOKEN ENDPOINT; the `audience` FORM FIELD of
the request is the REST base. They are different values and are built
from different constants here too.

WHAT IS NEVER PRINTED: the private key, the signed assertion, the bearer
token, and whole account payloads. Receipts carry statuses, counts,
resource names, request identifiers and the figures the owner asked for
-- never a raw body.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import uuid

# ── the production boundary ──────────────────────────────────────────

AUTH0_DOMAIN = "pmx-prod.us.auth0.com"
REST_BASE = "https://api.prod.polymarketexchange.com"

# Both values come from the current documentation, which management
# verified independently; they are not guesses and not asked of the venue.
CLIENT_ASSERTION_AUD = "https://%s/oauth/token" % AUTH0_DOMAIN
TOKEN_REQUEST_AUDIENCE = REST_BASE
TOKEN_REQUEST_ENCODING = "application/x-www-form-urlencoded"

# NOT_IDENTIFIED, and not probed from here. The documentation spells the
# gRPC host four ways and none is confirmed for production. A read-only
# verification does not need a stream, so this lane does not open one.
PRODUCTION_GRPC_TARGET = "NOT_IDENTIFIED"

_PRODUCTION_HOSTS = frozenset({
    "api.prod.polymarketexchange.com",
    "pmx-prod.us.auth0.com",
})

CREDENTIALS_EXPECTED = ("PMX_CLIENT_ID", "PMX_PARTICIPANT_ID",
                        "PMX_KEY_ID", "PMX_PRIVATE_KEY_B64")
# Needed only by the balance and positions reads, which say so by name
# rather than failing obscurely.
ACCOUNT_ENV = "PMX_ACCOUNT"


class NotProduction(RuntimeError):
    pass


class NotAReadPath(RuntimeError):
    pass


def assert_production(url: str) -> str:
    """Refuse anything that is not a production host.

    The mirror image of assert_preprod. A preprod host is refused here
    for the same reason a production host is refused there: an
    environment that can be talked into being the other one is not a
    boundary.
    """
    s = str(url)
    host = s.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if host not in _PRODUCTION_HOSTS:
        raise NotProduction("refused: %r is not a production host" % host)
    return s


# ── the allow-list. THIS is what makes the lane read-only ────────────
#
# A path absent from this mapping cannot be requested: `request_for`
# raises rather than returning a URL. Adding a mutating path here would
# take a deliberate edit, a test failure, and a review -- which is the
# point.

READ_ONLY_PATHS = {
    "whoami":       ("GET",  "/v1/whoami",                  None),
    "users":        ("GET",  "/v1/users",                   None),
    "accounts":     ("GET",  "/v1/accounts/accounts",       None),
    "positions":    ("GET",  "/v1/positions",               None),
    "balance":      ("POST", "/v1/positions/balance",       "account"),
    "balances":     ("POST", "/v1/positions/balances",      "account"),
    "instruments":  ("POST", "/v1/refdata/instruments",     "page"),
    "symbols":      ("POST", "/v1/refdata/symbols",         "page"),
    "orders":       ("POST", "/v1/report/orders/search",    "search"),
    "executions":   ("POST", "/v1/report/executions/search", "search"),
    # THE LIVE MARKET READS. These are the reason production beats
    # preproduction for observation: preprod's instrument universe is
    # disjoint from the one RN1 actually trades (OBSERVED_PREPROD
    # 2026-09-10: aec-itfme-matesta-leoros-2026-09-10 -> 404 instrument
    # does not exist), so no preprod book can measure real overlap,
    # real depth or real latency. Both are GETs and take a symbol.
    "bbo":          ("GET",  "/v1/orderbook/{symbol}/bbo",  "symbol"),
    "book":         ("GET",  "/v1/orderbook/{symbol}",      "symbol"),
}

READS = tuple(READ_ONLY_PATHS)
SYMBOL_READS = tuple(n for n, v in READ_ONLY_PATHS.items()
                     if v[2] == "symbol")


class SymbolRequired(RuntimeError):
    pass


def request_for(name: str, account: str = "", symbol: str = "") -> tuple:
    """(method, url, body) for one named read, or refuse.

    Refusal is by NAME and happens before any socket is opened.
    """
    if name not in READ_ONLY_PATHS:
        raise NotAReadPath(
            "refused: %r is not one of this lane's reads. This lane has no "
            "order, cancel, replace, preview or funding path, and one is "
            "not added by passing its name." % name)
    method, path, shape = READ_ONLY_PATHS[name]
    body = None
    if shape == "account":
        body = {"account": account, "currency": "USD"} if account else {}
        if name == "balances":
            body.pop("currency", None)
    elif shape == "page":
        body = {"pageSize": 20}
    elif shape == "search":
        body = {"pageSize": 50}
    elif shape == "symbol":
        symbol = str(symbol or "").strip()
        if not symbol:
            raise SymbolRequired(
                "refused: %r needs a symbol. Pass one rather than letting "
                "the read ask the venue an empty question." % name)
        if "/" in symbol or "?" in symbol or "#" in symbol:
            # a symbol is a slug; anything else could retarget the path
            raise SymbolRequired("refused: %r is not a symbol" % symbol)
        path = path.replace("{symbol}", symbol)
    return method, assert_production(REST_BASE + path), body


# ── verdicts, kept apart the way the preprod lane keeps them ─────────

A_SECRET_MISSING = "SECRET_MISSING"
A_KEY_NOT_USABLE = "CREDENTIAL_PRESENT_BUT_NOT_USABLE"
A_REJECTED = "AUTHENTICATION_REJECTED"
A_PERMISSION_DENIED = "AUTHENTICATED_BUT_ENDPOINT_PERMISSION_DENIED"
A_OK = "AUTHENTICATED"
A_UNREACHABLE = "VENUE_UNREACHABLE"


class KeyNotUsable(RuntimeError):
    """The key is present and did not decode. NOT a venue problem.

    Run 1 on 2026-09-19 reported VENUE_UNREACHABLE for exactly this: the
    private key failed base64 decoding, binascii.Error's type name is the
    bare word "Error", and the catch-all around the token mint labelled
    it a transport failure. No socket had been opened. Saying "the venue
    is unreachable" when the venue was never contacted is the kind of
    wrong answer that sends someone to check the wrong system, so a
    local credential fault now has its own verdict and never borrows
    that one.
    """


def key_shape(raw: str) -> dict:
    """WHAT SHAPE the key material is, revealing none of it.

    Everything here is a fact ABOUT the value -- its length, whether it
    parses, which PEM label it carries -- and none of it is the value.
    The point is to answer "what did I actually paste?" without anyone
    having to paste it anywhere else to find out.
    """
    import re

    raw = raw or ""
    stripped = "".join(raw.split())
    out = {"byteLength": len(raw),
           "strippedLength": len(stripped),
           "hasWhitespace": len(raw) != len(stripped),
           "lineCount": raw.count("\n") + 1 if raw else 0,
           "looksLikeJson": stripped[:1] in ("{", "["),
           "containsBEGIN": "BEGIN" in raw,
           "startsWithBEGIN": raw.lstrip()[:11] == "-----BEGIN ",
           "base64Decodes": False,
           "decodedLength": None,
           "decodedIsPem": False,
           "pemLabel": None}

    label = re.search(r"-----BEGIN ([A-Z0-9 ]+)-----", raw)
    if label:
        out["pemLabel"] = label.group(1)
    try:
        import base64 as _b64

        decoded = _b64.b64decode(stripped, validate=True)
        out["base64Decodes"] = True
        out["decodedLength"] = len(decoded)
        text = decoded.decode("utf-8", "replace")
        out["decodedIsPem"] = "-----BEGIN " in text
        inner = re.search(r"-----BEGIN ([A-Z0-9 ]+)-----", text)
        if inner:
            out["pemLabel"] = inner.group(1)
    except Exception as exc:                                   # noqa: BLE001
        out["base64Error"] = "%s: %s" % (type(exc).__name__, str(exc)[:60])

    # A PEM is not enough: it has to be the PRIVATE half. A public key
    # would stage cleanly and then fail at signing time with a message
    # about the key, which is a slow way to learn the wrong half of the
    # pair was installed.
    private = bool(out["pemLabel"]) and "PRIVATE" in (out["pemLabel"] or "")
    public = bool(out["pemLabel"]) and "PUBLIC" in (out["pemLabel"] or "")

    if not raw.strip():
        out["verdict"] = "EMPTY"
    elif public:
        out["verdict"] = "WRONG_HALF_OF_THE_PAIR"
    elif out["startsWithBEGIN"] and private:
        out["verdict"] = "USABLE_PEM"
    elif out["decodedIsPem"] and private:
        out["verdict"] = "USABLE_BASE64_OF_PEM"
    else:
        out["verdict"] = "NOT_A_PRIVATE_KEY_WE_CAN_USE"

    out["meaning"] = {
        "EMPTY": "the secret is not set, or is set to whitespace",
        "USABLE_PEM": "a private-key PEM pasted as-is; staged verbatim",
        "USABLE_BASE64_OF_PEM": "base64 of a private-key PEM; decoded",
        "WRONG_HALF_OF_THE_PAIR":
            "this is the PUBLIC key. The public half goes to Polymarket; "
            "the PRIVATE half goes in this secret and never leaves.",
        "NOT_A_PRIVATE_KEY_WE_CAN_USE":
            "the value is neither a private-key PEM nor base64 of one. "
            "Re-save it as `base64 -w0 <key>.pem`, or paste the PEM "
            "itself, including its BEGIN and END lines.",
    }[out["verdict"]]
    return out

# An OBSERVED_PRODUCTION reading is its own evidence class. It is not
# preprod evidence and preprod evidence is not it.
EVIDENCE_CLASS = "OBSERVED_PRODUCTION"


def credential_presence(env=None) -> dict:
    """Which credentials arrived, WITHOUT reading their values."""
    env = os.environ if env is None else env
    present = [n for n in CREDENTIALS_EXPECTED if (env.get(n) or "").strip()]
    missing = [n for n in CREDENTIALS_EXPECTED if n not in present]
    return {"present": present, "missing": missing,
            "accountSupplied": bool((env.get(ACCOUNT_ENV) or "").strip()),
            "verdict": A_SECRET_MISSING if missing else None}


def classify_auth(token_status, probe_status, transport_error=None) -> str:
    if transport_error:
        return A_UNREACHABLE
    if token_status != 200:
        return A_REJECTED
    if probe_status in (401, 403):
        return A_PERMISSION_DENIED
    return A_OK


def scopes_of(token: str) -> list:
    """The scope list from OUR OWN token's payload.

    The token is ours and this reads only its `scope` claim. The token
    itself is never returned, logged or written to a receipt.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:                                          # noqa: BLE001
        return []
    return str(claims.get("scope") or "").split()


def identity_of(body) -> dict:
    """Resource names only -- never the whole payload."""
    if not isinstance(body, dict):
        return {}
    out = {}
    for key in ("name", "firm", "firmType", "participant", "user",
                "account", "clearingMember", "displayName"):
        if body.get(key):
            out[key] = body[key]
    return out


def summarize(name: str, status: int, body) -> dict:
    """What a read SAW, without reproducing the payload.

    "Do not print unrestricted private account payloads" is a rule about
    what leaves the runner, so the receipt carries shapes and counts and
    the few figures the verification is for.
    """
    out = {"read": name, "status": status}
    if not isinstance(body, dict):
        out["shape"] = type(body).__name__
        return out
    if name in ("whoami", "users"):
        out["identity"] = identity_of(body)
    elif name in ("balance", "balances"):
        for key in ("buyingPower", "cash", "available", "currency",
                    "netLiquidatingValue", "balances"):
            if key in body:
                value = body[key]
                out[key] = len(value) if isinstance(value, list) else value
    elif name == "positions":
        rows = body.get("positions") or body.get("position") or []
        out["positionCount"] = len(rows) if isinstance(rows, list) else None
    elif name in ("instruments", "symbols"):
        rows = body.get("instruments") or body.get("symbols") or []
        out["count"] = len(rows) if isinstance(rows, list) else None
        if isinstance(rows, list) and rows:
            first = rows[0] if isinstance(rows[0], dict) else {}
            out["firstSymbol"] = first.get("symbol")
            out["firstState"] = first.get("state")
        out["nextPageToken"] = bool(body.get("nextPageToken"))
    elif name in ("orders", "executions"):
        rows = body.get("order") or body.get("execution") or []
        out["rowCount"] = len(rows) if isinstance(rows, list) else None
    elif name in ("bbo", "book"):
        # depth and touch, which are facts about the market rather than
        # about our account, so the actual numbers travel
        for key in ("symbol", "bidPrice", "bidQty", "askPrice", "askQty",
                    "lastPrice", "timestamp"):
            if key in body:
                out[key] = body[key]
        for side in ("bids", "asks"):
            rows = body.get(side)
            if isinstance(rows, list):
                out[side + "Levels"] = len(rows)
                if rows and isinstance(rows[0], dict):
                    out[side + "Top"] = rows[0]
    elif name == "accounts":
        rows = body.get("accounts") or []
        out["accountCount"] = len(rows) if isinstance(rows, list) else None
        if isinstance(rows, list):
            out["accountNames"] = [r.get("name") for r in rows
                                   if isinstance(r, dict)][:10]
    else:
        out["keys"] = sorted(body)[:12]
    return out


# ── receipts ─────────────────────────────────────────────────────────

_FORBIDDEN = ("BEGIN RSA", "BEGIN PRIVATE", "BEGIN EC", "access_token",
              "Bearer ey", "client_assertion")


def _refuse_secrets(obj) -> None:
    blob = json.dumps(obj, default=str)
    for marker in _FORBIDDEN:
        if marker in blob:
            raise RuntimeError(
                "receipt refused: it contains %r, which is a credential or "
                "a signed assertion and must never be written down" % marker)


def receipt(kind: str, **fields) -> dict:
    r = {"kind": kind,
         "runId": os.environ.get("GITHUB_RUN_ID"),
         "executedRevision": os.environ.get("GITHUB_SHA"),
         "environment": "PRODUCTION",
         "evidenceClass": EVIDENCE_CLASS,
         "restBase": REST_BASE,
         "grpcTarget": PRODUCTION_GRPC_TARGET,
         "orderPathPresent": False,
         "at": time.time()}
    r.update(fields)
    _refuse_secrets(r)
    return r


def write_receipt(path, payload) -> str:
    _refuse_secrets(payload)
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=1, default=str))
    return str(p)


def request_id() -> str:
    """A DISTINCT identifier per request, so a receipt can be matched to
    the call it describes and venue support can be pointed at one."""
    return str(uuid.uuid4())


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("op", choices=("presence", "keyshape", "verify"))
    ap.add_argument("--reads", default="", help="comma-separated read names")
    ap.add_argument("--symbol", default="", help="symbol for bbo / book")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    if a.op == "presence":
        out = receipt("presence", **credential_presence())
        print(json.dumps(out, indent=1))
        if a.out:
            write_receipt(a.out, out)
        return 0 if out["verdict"] is None else 1

    if a.op == "keyshape":
        # No network. Facts ABOUT the value, never the value.
        out = receipt("keyshape",
                      **key_shape(os.environ.get("PMX_PRIVATE_KEY_B64", "")))
        print(json.dumps(out, indent=1))
        if a.out:
            write_receipt(a.out, out)
        return 0 if out["verdict"] != "NOT_A_PRIVATE_KEY_WE_CAN_USE" else 1

    from pmx_production_net import run_verify                  # noqa: E402
    return run_verify(a)


if __name__ == "__main__":
    sys.exit(_cli())
