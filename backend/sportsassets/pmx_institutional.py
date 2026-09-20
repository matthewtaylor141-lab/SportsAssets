"""INSTITUTIONAL PRODUCTION MARKET DATA, read from the worker itself.

Owner directive 2026-09-20 00:1xZ: a new production credential is
installed directly on sportsassets-workers. "DO NOT USE THE GITHUB
BRIDGE AS THE PRIMARY MARKET-DATA PATH."

WHAT THIS MODULE IS ALLOWED TO DO, and how that is enforced rather than
promised:

  * `READS` is an allow-list of exactly three names -- instruments,
    bbo, book. `request_for` refuses any other name BEFORE a socket is
    opened. There is no insert, cancel, replace, preview or funding
    path in this file, and a test asserts their absence from the
    source text.
  * `assert_production` refuses every host that is not production, so
    this module cannot be talked into reading preproduction and
    calling it production.
  * The credential slots are the unprefixed PMX_*. The preprod lane
    reads PMX_PREPROD_*. The prefix IS the attestation.

§4 OF THE DIRECTIVE, STATED AS CODE RATHER THAN AS A PROMISE: "Even if
the token contains write:orders, ORDER_SUBMISSION_IMPLEMENTATION =
NONE." A scope in a token is a permission the venue granted; it is not
a capability this process has. There is nothing here that could submit
one, and `verify()` reports the scopes it was granted precisely so that
a write scope is VISIBLE rather than quietly present.

WHAT IS NEVER RETURNED, LOGGED OR WRITTEN: the private key, the signed
assertion, the bearer token, or any value of a credential. `presence()`
answers which names arrived and nothing about what they contain --
not a length, not a prefix, not a hash. A hash of a secret is a
statement about the secret.

THE TWO AUDIENCES, as production evidence established on 2026-09-19:
the `aud` CLAIM inside the signed assertion is Auth0's token endpoint;
the `audience` FORM FIELD is the REST base. Different values, built
from different constants.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid

AUTH0_DOMAIN = "pmx-prod.us.auth0.com"
REST_BASE = "https://api.prod.polymarketexchange.com"

CLIENT_ASSERTION_AUD = "https://%s/oauth/token" % AUTH0_DOMAIN
TOKEN_REQUEST_AUDIENCE = REST_BASE
TOKEN_REQUEST_ENCODING = "application/x-www-form-urlencoded"

_PRODUCTION_HOSTS = frozenset({
    "api.prod.polymarketexchange.com",
    "pmx-prod.us.auth0.com",
})

CREDENTIALS_EXPECTED = ("PMX_CLIENT_ID", "PMX_PARTICIPANT_ID",
                        "PMX_KEY_ID", "PMX_PRIVATE_KEY_B64")

# OBSERVED_PRODUCTION 2026-09-19: /v1/users answered in 11.2 s and
# /v1/refdata/symbols had not answered at 30 s. A timeout shorter than
# the venue's own tail records a slow endpoint as a transport failure,
# which is a statement about us rather than about them.
TIMEOUT = (5.0, 30.0)

# THE MECHANISM, NAMED HONESTLY. The venue's documentation spells the
# gRPC market-data host four ways and none is confirmed for production,
# so this lane does not open a stream and does not claim to. REST
# bootstraps and REST maintains, at a tight cadence, into an in-memory
# book -- and the evidence says exactly that rather than implying a
# subscription nobody has verified.
MARKET_DATA_MECHANISM = "REST_POLL_MAINTAINED_IN_MEMORY"
STREAM_TARGET = "NOT_IDENTIFIED"

# §3: the evidence environment for anything this module returns.
EVIDENCE_ENVIRONMENT = "DIRECT_INSTITUTIONAL_WORKER"
EVIDENCE_CLASS = "OBSERVED_PRODUCTION"

# §4, as a value that travels with every receipt.
ORDER_SUBMISSION_IMPLEMENTATION = "NONE"

A_SECRET_MISSING = "CREDENTIAL_MISSING"
A_KEY_NOT_USABLE = "CREDENTIAL_PRESENT_BUT_NOT_USABLE"
A_REJECTED = "AUTHENTICATION_REJECTED"
A_PERMISSION_DENIED = "AUTHENTICATED_BUT_ENDPOINT_PERMISSION_DENIED"
A_OK = "AUTHENTICATED"
A_UNREACHABLE = "VENUE_UNREACHABLE"


class NotProduction(RuntimeError):
    pass


class NotAReadPath(RuntimeError):
    pass


class KeyNotUsable(RuntimeError):
    """The key is present and did not decode. NOT a venue problem.

    Saying "the venue is unreachable" when no socket was opened sends
    somebody to check the wrong system.
    """


def assert_production(url: str) -> str:
    host = str(url).split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if host.lower() not in _PRODUCTION_HOSTS:
        raise NotProduction("refused: %r is not a production host" % host)
    return str(url)


# ── the allow-list. THIS is what makes the lane read-only ────────────
#
# Three names, all market data. A path absent from this mapping cannot
# be requested: request_for raises rather than returning a URL. Adding
# anything else would take a deliberate edit, a failing test and a
# review, which is the point of the shape.

READ_ONLY_PATHS = {
    "instruments": ("POST", "/v1/refdata/instruments", "page_or_symbol"),
    "bbo":         ("GET",  "/v1/orderbook/{symbol}/bbo", "symbol"),
    "book":        ("GET",  "/v1/orderbook/{symbol}", "symbol"),
}
READS = tuple(READ_ONLY_PATHS)


def request_id() -> str:
    return str(uuid.uuid4())


def request_for(name: str, symbol: str = "") -> tuple:
    """(method, url, body) for one named read, or refuse by name."""
    if name not in READ_ONLY_PATHS:
        raise NotAReadPath(
            "refused: %r is not one of this lane's reads. This lane has "
            "no order, cancel, replace, preview or funding path, and one "
            "is not added by passing its name." % name)
    method, path, shape = READ_ONLY_PATHS[name]
    body = None
    symbol = str(symbol or "").strip()
    if shape == "page_or_symbol":
        body = ({"symbols": [symbol], "pageSize": 50} if symbol
                else {"pageSize": 50})
    elif shape == "symbol":
        if not symbol:
            raise NotAReadPath("refused: %r needs a symbol" % name)
        path = path.replace("{symbol}", symbol)
    return method, assert_production(REST_BASE + path), body


def presence(env=None) -> dict:
    """WHICH credential names arrived. Nothing about their contents.

    Not a length, not a prefix, not a hash. A hash of a secret is a
    statement about the secret, and the directive's words are
    "PMX_CLIENT_ID_PRESENT =", not "PMX_CLIENT_ID_LOOKS_LIKE =".
    """
    env = os.environ if env is None else env
    present = [n for n in CREDENTIALS_EXPECTED if (env.get(n) or "").strip()]
    missing = [n for n in CREDENTIALS_EXPECTED if n not in present]
    return {n + "_PRESENT": (n in present) for n in CREDENTIALS_EXPECTED} | {
        "missing": missing,
        "verdict": A_SECRET_MISSING if missing else None}


def _private_key_pem(env=None) -> str:
    env = os.environ if env is None else env
    raw = env.get("PMX_PRIVATE_KEY_B64") or ""
    if "-----BEGIN" in raw[:64]:
        return raw
    try:
        pem = base64.b64decode("".join(raw.split()), validate=True).decode()
    except Exception as exc:                                   # noqa: BLE001
        raise KeyNotUsable(
            "the key is set but is neither a PEM nor base64 of one "
            "(%s)" % type(exc).__name__) from None
    if "BEGIN" not in pem:
        raise KeyNotUsable(
            "the key decoded to %d bytes with no PEM header" % len(pem))
    return pem


def scopes_of(token: str) -> list:
    """The scope list from OUR OWN token's payload.

    The token is never returned, logged or written down; only the
    `scope` claim's words are, and they are reported precisely so a
    write scope is VISIBLE rather than quietly present. A granted scope
    is not a capability this process has -- see the module docstring.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:                                          # noqa: BLE001
        return []
    return str(claims.get("scope") or "").split()


def classify_auth(token_status, probe_status, transport_error=None) -> str:
    if transport_error:
        return A_UNREACHABLE
    if token_status != 200:
        return A_REJECTED
    if probe_status in (401, 403):
        return A_PERMISSION_DENIED
    return A_OK


# ── the client ───────────────────────────────────────────────────────


class Institutional:
    """One authenticated read-only session, with a cached token.

    THE TOKEN IS MINTED ONCE AND REUSED until shortly before it
    expires. Minting per request would put an Auth0 round trip on every
    market-data read, which is the opposite of what §7 asks for -- and
    it would be a self-inflicted rate limit besides.
    """

    # Re-mint this long before the venue's own expiry, so a token never
    # expires mid-flight on a read.
    RENEW_MARGIN_S = 120

    def __init__(self, session=None, env=None):
        self._session = session
        self._env = os.environ if env is None else env
        self._token = None
        self._token_expires_at = 0.0
        self.last_auth_ms = None
        self.last_scopes = []

    @property
    def session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def _mint(self) -> tuple:
        import jwt

        cid = self._env["PMX_CLIENT_ID"]
        kid = self._env["PMX_KEY_ID"]
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": cid, "sub": cid, "aud": CLIENT_ASSERTION_AUD,
             "iat": now, "exp": now + 60, "jti": request_id()},
            _private_key_pem(self._env), algorithm="RS256",
            headers={"kid": kid})
        url = assert_production("https://%s/oauth/token" % AUTH0_DOMAIN)
        t0 = time.time()
        r = self.session.post(url, data={
            "client_id": cid,
            "client_assertion_type":
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
            "audience": TOKEN_REQUEST_AUDIENCE,
            "grant_type": "client_credentials"},
            headers={"Content-Type": TOKEN_REQUEST_ENCODING},
            timeout=TIMEOUT)
        self.last_auth_ms = round((time.time() - t0) * 1000, 1)
        body = {}
        try:
            body = r.json()
        except Exception:                                      # noqa: BLE001
            pass
        token = body.get("access_token") if isinstance(body, dict) else None
        expires_in = (body or {}).get("expires_in") or 0
        why = str((body or {}).get("error") or "")[:80]
        if r.status_code == 200 and token:
            self._token = token
            self._token_expires_at = time.time() + max(
                0, float(expires_in) - self.RENEW_MARGIN_S)
            self.last_scopes = scopes_of(token)
        return r.status_code, token, expires_in, why

    def token(self) -> str | None:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        status, token, _exp, _why = self._mint()
        return token if status == 200 else None

    def _headers(self, token, rid) -> dict:
        return {"Authorization": "Bearer %s" % token,
                "X-Client-Id": self._env["PMX_CLIENT_ID"],
                "x-participant-id": self._env["PMX_PARTICIPANT_ID"],
                "X-Request-Id": rid,
                "Content-Type": "application/json"}

    def read(self, name: str, symbol: str = "") -> dict:
        """One named read. Returns status, body and the REAL timings.

        Never raises for a venue problem: a transport failure comes
        back as a named row, because a market-data loop that died on a
        single slow read would take the lane down with it.
        """
        method, url, body = request_for(name, symbol)     # refuses by name
        token = self.token()
        if not token:
            return {"read": name, "status": None,
                    "verdict": A_REJECTED, "ms": None}
        rid = request_id()
        t0 = time.time()
        try:
            r = self.session.request(
                method, url, headers=self._headers(token, rid),
                json=body if method == "POST" else None, timeout=TIMEOUT)
            try:
                parsed = r.json()
            except Exception:                                  # noqa: BLE001
                parsed = None
            return {"read": name, "status": r.status_code, "body": parsed,
                    "requestId": rid,
                    "ms": round((time.time() - t0) * 1000, 1)}
        except Exception as exc:                               # noqa: BLE001
            return {"read": name, "status": None,
                    "transportError": type(exc).__name__,
                    "requestId": rid,
                    "ms": round((time.time() - t0) * 1000, 1)}


def scales_of(instrument_record) -> tuple:
    """(priceScale, fractionalQtyScale) as INTEGERS, or (None, None).

    NEVER a default. The scale is per instrument, and assuming one
    misprices every instrument that does not use it -- MLB and NFL are
    1000 where the sports rows seen so far are 100.
    """
    rec = instrument_record if isinstance(instrument_record, dict) else {}
    try:
        ps = int(str(rec.get("priceScale")).strip())
        qs = int(str(rec.get("fractionalQtyScale")).strip())
    except (TypeError, ValueError, AttributeError):
        return None, None
    return (ps, qs) if ps > 0 and qs > 0 else (None, None)


def verify(client=None, symbol: str = "") -> dict:
    """§1 and §3: prove the worker's own credential, without exposing it.

    Reports the verdict, the scopes granted, whether L2 is readable and
    -- when a symbol is given -- the instrument identity and the book
    shape the directive asked to see.
    """
    seen = presence()
    if seen["verdict"]:
        return {"AUTH_STATUS": seen["verdict"],
                "AUTH_ENVIRONMENT": "PRODUCTION",
                "presence": seen,
                "ORDER_SUBMISSION_IMPLEMENTATION":
                    ORDER_SUBMISSION_IMPLEMENTATION}

    client = client or Institutional()
    try:
        status, token, expires_in, why = client._mint()
    except KeyNotUsable as exc:
        return {"AUTH_STATUS": A_KEY_NOT_USABLE, "why": str(exc),
                "venueContacted": False, "presence": seen,
                "ORDER_SUBMISSION_IMPLEMENTATION":
                    ORDER_SUBMISSION_IMPLEMENTATION}
    except Exception as exc:                                   # noqa: BLE001
        return {"AUTH_STATUS": A_UNREACHABLE,
                "transportError": type(exc).__name__,
                "presence": seen,
                "ORDER_SUBMISSION_IMPLEMENTATION":
                    ORDER_SUBMISSION_IMPLEMENTATION}
    if status != 200 or not token:
        return {"AUTH_STATUS": A_REJECTED, "tokenStatus": status,
                "error": why, "presence": seen,
                "ORDER_SUBMISSION_IMPLEMENTATION":
                    ORDER_SUBMISSION_IMPLEMENTATION}

    scopes = client.last_scopes
    out = {
        "AUTH_STATUS": A_OK,
        "AUTH_ENVIRONMENT": "PRODUCTION",
        "TOKEN_SCOPES": scopes,
        "READ_L2_PERMISSION": "read:l2marketdata" in scopes,
        # THE HONEST ANSWER, and its limit stated with it. The worker
        # authenticated with the PMX_KEY_ID installed in ITS OWN
        # environment. Whether that value differs from the GitHub
        # secret's cannot be checked from here: GitHub will not reveal
        # it, and the directive forbids trying.
        "NEW_KEY_ID_USED": "YES",
        "NEW_KEY_ID_USED_MEANS": (
            "the worker signed with the PMX_KEY_ID present in its own "
            "environment; the GitHub secret's value was not read, "
            "compared or recovered"),
        "authMs": client.last_auth_ms,
        "tokenLifetimeSeconds": expires_in,
        "MARKET_DATA_MECHANISM": MARKET_DATA_MECHANISM,
        "STREAM_TARGET": STREAM_TARGET,
        "EVIDENCE_ENVIRONMENT": EVIDENCE_ENVIRONMENT,
        "ORDER_SUBMISSION_IMPLEMENTATION": ORDER_SUBMISSION_IMPLEMENTATION,
        "presence": seen,
        "reads": [],
    }

    if not symbol:
        return out

    for name in ("instruments", "bbo", "book"):
        row = client.read(name, symbol)
        body = row.pop("body", None)
        if name == "instruments":
            rows = (body or {}).get("instruments") or []
            rec = rows[0] if rows and isinstance(rows[0], dict) else None
            ps, qs = scales_of(rec)
            ev = (rec or {}).get("eventAttributes") or {}
            row["instrumentIdentity"] = {
                "symbol": (rec or {}).get("symbol"),
                "productId": (rec or {}).get("productId"),
                "priceScale": ps, "fractionalQtyScale": qs,
                "payoutValue": ev.get("payoutValue"),
                "eventOutcome": ev.get("eventOutcome"),
            }
        elif name == "book":
            bids = (body or {}).get("bids") or []
            offers = (body or {}).get("offers") or []
            row["bookShape"] = {
                "bidLevels": len(bids), "offerLevels": len(offers),
                "transactTime": (body or {}).get("transactTime"),
                "state": (body or {}).get("state"),
                # VERBATIM, one level each. Field names are the thing
                # being verified, so they are shown rather than
                # described -- the failure this prevents is adapting to
                # guessed names, which cost us a whole run once.
                "bidSample": bids[:1], "offerSample": offers[:1],
            }
        else:
            row["bboShape"] = {"keys": sorted((body or {}).keys())
                               if isinstance(body, dict) else None,
                               "bestBid": (body or {}).get("bestBid"),
                               "bestOffer": (body or {}).get("bestOffer")}
        out["reads"].append(row)

    denied = [r["read"] for r in out["reads"] if r.get("status") in (401, 403)]
    out["AUTH_STATUS"] = classify_auth(
        status, out["reads"][0].get("status") if out["reads"] else None)
    out["permissionDenied"] = denied
    out["DIRECT_INSTITUTIONAL_L2_STATUS"] = (
        "VERIFIED" if any(r["read"] == "book" and r.get("status") == 200
                          for r in out["reads"]) else "NOT_VERIFIED")
    return out
