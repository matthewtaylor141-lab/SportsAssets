"""KALSHI VENUE ADAPTER -- credential-free, FAIL-CLOSED.

Management asked for the 1:1,000 execution mirror to be able to trade on
Kalshi as well as Polymarket US. No Kalshi credential exists yet, so this
module is built to be complete everywhere a credential is not needed and to
REFUSE, typed and before any byte leaves the process, everywhere it is.

THE CONTRACT IS PORTED, NOT GUESSED. Every endpoint, header, field and unit
below is taken from the edge-engine adapter that has traded Kalshi live
(edge-engine/src/edge/venues/kalshi.py). `CONTRACT` names the line each
item was read from; an item that edge-engine does NOT exercise is labelled
UNVERIFIED and is never on a money path without a refusal in front of it.
Nothing here imports edge-engine.

WHERE EDGE-ENGINE'S CODE AND ITS PROSE DISAGREE, THE CODE WINS:
  * The module docstring (kalshi.py:6) says books are "in cents"; the code
    (kalshi.py:260-285) reads the `orderbook_fp` dollar-string dialect first
    and treats cents as the legacy fallback. We do the same.
  * The task brief and older Kalshi docs say orders go to
    `/portfolio/orders` with `side: yes|no, action: buy|sell, yes_price`
    in cents. The code (kalshi.py:461-481) posts to
    `/portfolio/events/orders` because the legacy path answers HTTP 410,
    with `side: bid|ask` on a YES-only book and fixed-point STRING
    price/count. We build the V2 body. `/portfolio/orders` remains the
    verified path for LISTING orders and for DELETE (kalshi.py:606, 1001).
  * The fee prose (kalshi.py:8, 394-399) calls maker orders fee-free.
    We do NOT adopt that default (see kalshi_orders.fee_for).

CREDENTIALS (env names defined here, never logged, never returned):
    KALSHI_API_KEY_ID         the API key id (UUID-shaped)
    KALSHI_PRIVATE_KEY_PEM    the private key, PEM, unencrypted, of either
                              documented Kalshi key type: Ed25519 (PKCS#8,
                              Kalshi's default) or RSA (PKCS#1 or PKCS#8).
                              Read EXACTLY as configured, trailing newline
                              included; only a value that does not load as
                              given gets a paste repair (kalshi_key)
    KALSHI_PRIVATE_KEY_PATH   ... or a path to the same PEM file (its bytes,
                              unchanged)
    KALSHI_ENV                'prod' or 'demo' -- REQUIRED, no default
    KALSHI_SMALLLIVE_ENABLED  '1' / 'true' to allow submission; default OFF

`credential_state()` reports presence, shape and a fingerprint only.

KEY TYPES (RC5, 2026-10-08). This client signed with RSA-PSS only and
refused every other key as "not an RSA private key". Kalshi documents two
key types, Ed25519 (recommended) and RSA, and production's
KALSHI_PRIVATE_KEY_PEM is an Ed25519 PKCS#8 PEM (119 characters with its
trailing newline) that the dedicated plane already signs GET /account/limits
with (200). So credential_state called a documented, working key
KALSHI_CREDENTIAL_UNREADABLE. Loading, classifying and signing now go
through kalshi_key, the one implementation every Kalshi signer uses: the key
type is read from the PARSED key (Kalshi: "the PEM header does not identify
the key type"), Ed25519 signs the pre-sign text itself, RSA keeps RSA-PSS /
SHA-256 / MGF1-SHA256 / salt = digest length. The submission gate is
unchanged: a loadable key is one precondition of five, the env switch
defaults OFF, and no runner constructs this client
(tests/test_kalshi_isolation.py). KALSHI LIVE MONEY = NOT ACTIVATED.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from . import kalshi_key as KK

VERSION = "KALSHI_VENUE_V1"

KEY_ID_ENV = "KALSHI_API_KEY_ID"
PRIVATE_KEY_PEM_ENV = "KALSHI_PRIVATE_KEY_PEM"
PRIVATE_KEY_PATH_ENV = "KALSHI_PRIVATE_KEY_PATH"
ENV_ENV = "KALSHI_ENV"
ENABLED_ENV = "KALSHI_SMALLLIVE_ENABLED"
ENV_NAMES = (KEY_ID_ENV, PRIVATE_KEY_PEM_ENV, PRIVATE_KEY_PATH_ENV, ENV_ENV,
             ENABLED_ENV)

API_PREFIX = "/trade-api/v2"
BASE_URLS = {
    # kalshi.py:25 -- the base edge-engine trades against.
    "prod": "https://api.elections.kalshi.com" + API_PREFIX,
    # UNVERIFIED: edge-engine has never used a demo environment. Kalshi
    # documents this host for its demo exchange; it must be confirmed
    # before a demo key is used.
    "demo": "https://demo-api.kalshi.co" + API_PREFIX,
}

# Where each piece of the wire contract was read (edge-engine file:line).
CONTRACT = {
    "orderbook": ("GET /markets/{ticker}/orderbook", "kalshi.py:231,260-299"),
    "balance": ("GET /portfolio/balance -> balance (cents)", "kalshi.py:381-389"),
    "positions": ("GET /portfolio/positions -> market_positions[] "
                  "position_fp|position, market_exposure_dollars|market_exposure",
                  "kalshi.py:540-565,866-896"),
    "orders_list": ("GET /portfolio/orders?status=resting -> orders[] "
                    "order_id|id, ticker, action", "kalshi.py:606-613,897-911"),
    "fills": ("GET /portfolio/fills -> fills[] trade_id, order_id, ticker, side, "
              "action, count_fp|count, yes_price_dollars|yes_price, no_price*, "
              "is_taker, fee_dollars|fee, created_time",
              "kalshi.py:571-600,780-784"),
    "settlements": ("GET /portfolio/settlements -> settlements[] ticker, "
                    "market_result, *_total_cost[_dollars], revenue[_dollars], "
                    "settled_time", "kalshi.py:641-676,785-789"),
    "create_order": ("POST /portfolio/events/orders {ticker, client_order_id, "
                     "side bid|ask, count 'N.00', price '0.0000', "
                     "self_trade_prevention_type, time_in_force, expiration_time}",
                     "kalshi.py:458-481"),
    "create_order_ack": ("order|body: filled_count|fill_count|matched_count, "
                         "else remaining_count; order_id|id; status",
                         "kalshi.py:494-527"),
    "cancel": ("DELETE /portfolio/orders/{order_id}: 200/204 cancelled, "
               "404 gone, else error", "kalshi.py:995-1013"),
    "market_results": ("GET /markets?tickers=... status determined|finalized|"
                       "settled, result yes|no", "kalshi.py:1059-1075"),
    "auth": ("by the PARSED key's type: Ed25519 over the pre-sign text, or "
             "RSA-PSS(SHA256, MGF1-SHA256, salt=digest); pre-sign text "
             "'{ts_ms}{METHOD}{/trade-api/v2/path}', headers KALSHI-ACCESS-KEY/"
             "-TIMESTAMP/-SIGNATURE (base64)",
             "kalshi.py:361-379 (RSA); docs getting_started_api_keys "
             "(Ed25519, captured 2026-10-07)"),
    "pem_repair": ("one-line / escaped-newline PEM repair", "kalshi.py:319-346"),
    # Not exercised by edge-engine:
    "order_by_id": ("GET /portfolio/orders/{order_id}", "UNVERIFIED"),
    "post_only_flag": ("no venue post-only flag is sent; post-only is enforced "
                       "against the book before submission", "UNVERIFIED"),
    "fill_or_kill": ("FOK is not sent; edge-engine only uses IOC and GTC",
                     "UNVERIFIED"),
    "client_order_id_dedupe": ("venue de-duplication of a repeated "
                               "client_order_id is not relied on", "UNVERIFIED"),
}

# ── typed refusals ───────────────────────────────────────────────────
KALSHI_CREDENTIAL_ABSENT = "KALSHI_CREDENTIAL_ABSENT"
KALSHI_CREDENTIAL_UNREADABLE = "KALSHI_CREDENTIAL_UNREADABLE"
KALSHI_ENV_UNSET = "KALSHI_ENV_UNSET"
KALSHI_SMALLLIVE_DISABLED = "KALSHI_SMALLLIVE_DISABLED"
KALSHI_CONTROL_DISABLED = "KALSHI_CONTROL_DISABLED"
KALSHI_STOPPED = "KALSHI_STOPPED"
KALSHI_CONTROL_FINGERPRINT_MISMATCH = "KALSHI_CONTROL_FINGERPRINT_MISMATCH"
KALSHI_ENV_MISMATCH = "KALSHI_ENV_MISMATCH"
KALSHI_RECONCILIATION_ABSENT = "KALSHI_RECONCILIATION_ABSENT"
KALSHI_RECONCILIATION_FINGERPRINT_MISMATCH = \
    "KALSHI_RECONCILIATION_FINGERPRINT_MISMATCH"
KALSHI_RECONCILIATION_INCOMPLETE = "KALSHI_RECONCILIATION_INCOMPLETE"
KALSHI_RECONCILIATION_STALE = "KALSHI_RECONCILIATION_STALE"
KALSHI_RECONCILIATION_BASELINE_UNACCEPTED = \
    "KALSHI_RECONCILIATION_BASELINE_UNACCEPTED"
KALSHI_PLAN_NOT_SUBMITTABLE = "KALSHI_PLAN_NOT_SUBMITTABLE"
#: (RC6 red-team, credential isolation) the configured Kalshi key pair is
#: also configured in this process for another venue (credential_isolation)
KALSHI_KEY_REUSED_ACROSS_VENUES = "KALSHI_KEY_REUSED_ACROSS_VENUES"

RECONCILIATION_MAX_AGE_S = 15 * 60
TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Refusal:
    """Nothing was sent. `sent` exists so a caller can assert it."""
    code: str
    detail: dict = field(default_factory=dict)
    sent: bool = False
    ok: bool = False


@dataclass(frozen=True)
class Response:
    """A venue answer (or a transport failure, `error` set, status None)."""
    status: int | None
    body: Any = None
    error: str | None = None
    sent: bool = True

    @property
    def ok(self) -> bool:
        return self.status in (200, 201, 204)


class TransportError(Exception):
    """The request may or may not have reached the venue (timeout, reset)."""


class Transport(Protocol):
    def send(self, method: str, url: str, *, headers: dict, params: dict | None,
             json_body: dict | None, timeout: float) -> Response: ...


class RequestsTransport:
    """The real transport. Imported lazily; never constructed by tests."""

    def __init__(self):
        import requests
        self._s = requests.Session()
        self._exc = requests.RequestException

    def send(self, method, url, *, headers, params, json_body, timeout):
        try:
            r = self._s.request(method, url, headers=headers, params=params,
                                json=json_body, timeout=timeout)
        except self._exc as exc:
            raise TransportError(type(exc).__name__) from None
        try:
            body = r.json()
        except ValueError:
            body = None
        return Response(status=r.status_code, body=body,
                        error=None if r.status_code < 400 else (r.text or "")[:300])


# ── credentials ──────────────────────────────────────────────────────

def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def fingerprint(key_id: str) -> str | None:
    """sha256 prefix (12 hex) of the API key id: proves two reads used the
    same key, useless for authenticating. Same shape as execmirror_probe."""
    return hashlib.sha256(key_id.encode()).hexdigest()[:12] if key_id else None


def normalize_pem(pem: str | bytes) -> str:
    """The PEM text that loads: the value EXACTLY as configured when it
    loads as given (every byte, the trailing newline included), else the
    paste repair that does (kalshi.py:319-346's repairs: literal
    backslash-n escapes, a key squashed onto one line; plus base64 of the
    whole PEM). A value no form of which loads is returned as given, so the
    loader's own refusal is what the caller sees."""
    raw = pem.decode("utf-8") if isinstance(pem, (bytes, bytearray)) else str(pem)
    try:
        _key, form = KK.load(raw)
    except Exception:                                           # noqa: BLE001
        return raw
    for f, data in KK._candidates(raw):
        if f == form:
            return data.decode("utf-8")
    return raw


def _read_pem(env: Mapping[str, str]) -> str | bytes:
    """THE CONFIGURED BYTES, UNCHANGED. The env value is handed on as set --
    never stripped, so a PEM's trailing newline survives (production's
    Ed25519 KALSHI_PRIVATE_KEY_PEM is 119 characters WITH it); a key file is
    read as bytes. Presence alone is judged on the stripped value."""
    pem = env.get(PRIVATE_KEY_PEM_ENV)
    if pem is not None and str(pem).strip():
        return pem
    path = _get(env, PRIVATE_KEY_PATH_ENV)
    if not path:
        return ""
    with open(path, "rb") as f:
        return f.read()


def load_private_key(pem: str | bytes):
    """Either documented Kalshi key type, Ed25519 or RSA (kalshi_key.load,
    the type read from the parsed key). Any other key, or a value that does
    not load, raises kalshi_key.KeyRefused -- a ValueError naming why."""
    return KK.load_private_key(pem)


def public_key_fingerprint(private_key) -> str:
    """sha256 prefix (16 hex) of the DER SubjectPublicKeyInfo: identifies
    WHICH key pair without revealing anything usable."""
    from cryptography.hazmat.primitives import serialization
    der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


UUID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def credential_state(env: Mapping[str, str] | None = None) -> dict:
    """Presence, shape and fingerprints ONLY. Never a value, length or
    prefix of a secret; never raises."""
    env = os.environ if env is None else env
    kid = _get(env, KEY_ID_ENV)
    has_pem = bool(_get(env, PRIVATE_KEY_PEM_ENV))
    has_path = bool(_get(env, PRIVATE_KEY_PATH_ENV))
    kenv = _get(env, ENV_ENV).lower()
    out = {"version": VERSION,
           "names": {KEY_ID_ENV: bool(kid), PRIVATE_KEY_PEM_ENV: has_pem,
                     PRIVATE_KEY_PATH_ENV: has_path, ENV_ENV: bool(kenv),
                     ENABLED_ENV: bool(_get(env, ENABLED_ENV))},
           "key_id_shape": ("UUID" if UUID_RX.match(kid) else
                            "NON_UUID" if kid else None),
           "key_fingerprint": fingerprint(kid),
           "environment": kenv if kenv in BASE_URLS else None,
           "environment_valid": kenv in BASE_URLS,
           "smalllive_enabled_env": smalllive_env_enabled(env),
           "private_key": None, "public_key_fingerprint": None}
    if has_pem or has_path:
        try:
            k, form = KK.load(_read_pem(env))
            if KK.key_type(k) == KK.KEY_RSA:
                out["private_key"] = {"type": "RSA", "bits": k.key_size,
                                      "loadable": True}
            else:
                out["private_key"] = {"type": KK.KEY_ED25519,
                                      "loadable": True}
            # AS_GIVEN unless a paste repair was needed: the bytes are used
            # as configured whenever they load
            out["private_key_form"] = form
            out["public_key_fingerprint"] = public_key_fingerprint(k)
        except Exception as exc:                              # noqa: BLE001
            out["private_key"] = {"loadable": False,
                                  "error": type(exc).__name__}
            if isinstance(exc, KK.KeyRefused):
                out["private_key"]["refusal"] = exc.code
    other = []
    if (out["private_key"] or {}).get("loadable"):
        try:
            other = _other_venue_slots(_read_pem(env), env)
        except Exception:                                     # noqa: BLE001
            other = []
    out["also_configured_as"] = other
    out["complete"] = bool(kid and (out["private_key"] or {}).get("loadable")
                           and out["environment_valid"] and not other)
    if not (kid and (has_pem or has_path)):
        out["state"] = KALSHI_CREDENTIAL_ABSENT
    elif not (out["private_key"] or {}).get("loadable"):
        out["state"] = KALSHI_CREDENTIAL_UNREADABLE
    elif other:
        out["state"] = KALSHI_KEY_REUSED_ACROSS_VENUES
    elif not out["environment_valid"]:
        out["state"] = KALSHI_ENV_UNSET
    else:
        out["state"] = "PRESENT"
    return out


def _other_venue_slots(pem, env) -> list:
    """Other venues' slots in `env` holding the same key pair as the Kalshi
    key about to be used (credential_isolation; names only)."""
    from . import credential_isolation as CI
    return CI.other_venue_slots_holding(pem, venue="KALSHI", env=env)


def smalllive_env_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return _get(env, ENABLED_ENV).lower() in ("1", "true", "yes", "on")


# ── signing (kalshi.py:361-379) ──────────────────────────────────────

def signing_message(ts_ms: str, method: str, path: str) -> bytes:
    """`path` is the full API path WITHOUT the query string, e.g.
    '/trade-api/v2/portfolio/balance' (kalshi.py:383-385 signs the path and
    passes params separately)."""
    if not path.startswith(API_PREFIX + "/"):
        raise ValueError("sign the full API path (%s/...)" % API_PREFIX)
    if "?" in path:
        raise ValueError("the signed path carries no query string")
    return f"{ts_ms}{method.upper()}{path}".encode()


def sign(private_key, ts_ms: str, method: str, path: str) -> str:
    """base64 signature over the pre-sign text, by the key's own type:
    Ed25519 signs the text itself, RSA is RSA-PSS / SHA-256 / MGF1-SHA256
    / salt = digest length (kalshi_key.sign)."""
    return KK.sign(private_key, signing_message(ts_ms, method, path))


def verify(public_key, signature_b64: str, ts_ms: str, method: str,
           path: str) -> bool:
    """For tests and self-checks: does this signature verify (under the
    public key's own algorithm)?"""
    return KK.verify(public_key, signature_b64,
                     signing_message(ts_ms, method, path))


def auth_headers(key_id: str, private_key, method: str, path: str,
                 now_ms: int) -> dict:
    ts = str(int(now_ms))
    return {"KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sign(private_key, ts, method, path)}


# ── the submission gate (pure) ───────────────────────────────────────

def submission_gate(cred: dict, control: dict | None,
                    reconciliation: dict | None, *, env_enabled: bool,
                    now: float) -> Refusal | None:
    """None when a submission may be sent; otherwise the FIRST reason not.

    Order matters and is tested: a missing credential is reported before
    anything else, so "no key" is never disguised as "disabled".
    `control` is the durable control row (kalshi_smalllive_control);
    `reconciliation` the read-only account reconciliation it names
    (kalshi_account_reconciliations)."""
    if cred.get("state") != "PRESENT":
        return Refusal(cred.get("state") or KALSHI_CREDENTIAL_ABSENT)
    fp = cred.get("key_fingerprint")
    if not env_enabled:
        return Refusal(KALSHI_SMALLLIVE_DISABLED, {"env": ENABLED_ENV})
    control = control or {}
    if not control.get("enabled"):
        return Refusal(KALSHI_CONTROL_DISABLED)
    if control.get("stopped"):
        return Refusal(KALSHI_STOPPED)
    if control.get("key_fingerprint") != fp:
        return Refusal(KALSHI_CONTROL_FINGERPRINT_MISMATCH,
                       {"expected": control.get("key_fingerprint"), "found": fp})
    if control.get("kalshi_env") != cred.get("environment"):
        return Refusal(KALSHI_ENV_MISMATCH,
                       {"control": control.get("kalshi_env"),
                        "credential": cred.get("environment")})
    if not reconciliation:
        return Refusal(KALSHI_RECONCILIATION_ABSENT)
    if reconciliation.get("key_fingerprint") != fp:
        return Refusal(KALSHI_RECONCILIATION_FINGERPRINT_MISMATCH,
                       {"expected": fp,
                        "found": reconciliation.get("key_fingerprint")})
    if not reconciliation.get("complete") or \
            reconciliation.get("verdict") not in ("EMPTY", "NOT_EMPTY"):
        return Refusal(KALSHI_RECONCILIATION_INCOMPLETE,
                       {"verdict": reconciliation.get("verdict")})
    if reconciliation.get("verdict") == "NOT_EMPTY" and \
            not reconciliation.get("baseline_accepted"):
        return Refusal(KALSHI_RECONCILIATION_BASELINE_UNACCEPTED)
    at = reconciliation.get("at")
    at = at.timestamp() if hasattr(at, "timestamp") else at
    if at is None or now - float(at) > RECONCILIATION_MAX_AGE_S:
        return Refusal(KALSHI_RECONCILIATION_STALE,
                       {"max_age_s": RECONCILIATION_MAX_AGE_S})
    return None


# ── the client ───────────────────────────────────────────────────────

class KalshiClient:
    """Every authenticated method checks the credential FIRST and returns a
    Refusal without touching the transport when it is absent. The
    transport is injected; the default (requests) is built only on the
    first call that actually sends."""

    def __init__(self, transport: Transport | None = None, *,
                 env: Mapping[str, str] | None = None,
                 clock: Callable[[], float] = time.time,
                 timeout: float = TIMEOUT_S):
        self._env = os.environ if env is None else env
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._key = None

    # -- plumbing --
    def _tx(self) -> Transport:
        if self._transport is None:
            self._transport = RequestsTransport()
        return self._transport

    def _base(self) -> str | None:
        return BASE_URLS.get(_get(self._env, ENV_ENV).lower())

    def credential_state(self) -> dict:
        return credential_state(self._env)

    def _auth(self, method: str, path: str) -> dict | Refusal:
        kid = _get(self._env, KEY_ID_ENV)
        if not kid or not (_get(self._env, PRIVATE_KEY_PEM_ENV)
                           or _get(self._env, PRIVATE_KEY_PATH_ENV)):
            return Refusal(KALSHI_CREDENTIAL_ABSENT)
        if self._base() is None:
            return Refusal(KALSHI_ENV_UNSET)
        if self._key is None:
            try:
                pem = _read_pem(self._env)
                other = _other_venue_slots(pem, self._env)
                if other:
                    return Refusal(KALSHI_KEY_REUSED_ACROSS_VENUES,
                                   {"also_configured_as": other})
                self._key = load_private_key(pem)
            except Exception as exc:                          # noqa: BLE001
                return Refusal(KALSHI_CREDENTIAL_UNREADABLE,
                               {"error": type(exc).__name__})
        return auth_headers(kid, self._key, method, API_PREFIX + path,
                            int(self._clock() * 1000))

    def _send(self, method: str, path: str, *, params=None, json_body=None,
              auth: bool) -> Response | Refusal:
        if auth:
            headers = self._auth(method, path)
            if isinstance(headers, Refusal):
                return headers
        else:
            headers = {}
            if self._base() is None:
                return Refusal(KALSHI_ENV_UNSET)
        try:
            return self._tx().send(method, self._base() + path, headers=headers,
                                   params=params, json_body=json_body,
                                   timeout=self._timeout)
        except TransportError as exc:
            return Response(status=None, error="transport:%s" % exc)

    # -- public market data (no credential) --
    def orderbook(self, ticker: str) -> Response | Refusal:
        return self._send("GET", "/markets/%s/orderbook" % ticker, auth=False)

    # -- authenticated reads --
    def balance(self):
        return self._send("GET", "/portfolio/balance", auth=True)

    def positions(self, cursor: str | None = None, limit: int = 200):
        p = {"limit": limit, **({"cursor": cursor} if cursor else {})}
        return self._send("GET", "/portfolio/positions", params=p, auth=True)

    def orders(self, status: str | None = None, cursor: str | None = None,
               limit: int = 100):
        p = {"limit": limit, **({"status": status} if status else {}),
             **({"cursor": cursor} if cursor else {})}
        return self._send("GET", "/portfolio/orders", params=p, auth=True)

    def fills(self, cursor: str | None = None, limit: int = 200):
        p = {"limit": limit, **({"cursor": cursor} if cursor else {})}
        return self._send("GET", "/portfolio/fills", params=p, auth=True)

    def settlements(self, cursor: str | None = None, limit: int = 200):
        p = {"limit": limit, **({"cursor": cursor} if cursor else {})}
        return self._send("GET", "/portfolio/settlements", params=p, auth=True)

    def order(self, order_id: str):
        """UNVERIFIED endpoint (edge-engine never reads one order by id);
        the poller falls back to the verified list read when it fails."""
        return self._send("GET", "/portfolio/orders/%s" % order_id, auth=True)

    # -- mutations --
    def submit(self, plan: dict, *, control: dict | None,
               reconciliation: dict | None) -> Response | Refusal:
        """POST a planned order. Refused unless the credential is present,
        the env switch AND the durable control are on, and a complete
        read-only reconciliation for THIS key is fresh."""
        cred = credential_state(self._env)
        refusal = submission_gate(cred, control, reconciliation,
                                  env_enabled=smalllive_env_enabled(self._env),
                                  now=self._clock())
        if refusal:
            return refusal
        if not plan or plan.get("state") != "PLANNED" or not plan.get("payload"):
            return Refusal(KALSHI_PLAN_NOT_SUBMITTABLE,
                           {"state": (plan or {}).get("state")})
        return self._send("POST", "/portfolio/events/orders",
                          json_body=dict(plan["payload"]), auth=True)

    def cancel(self, order_id: str) -> Response | Refusal:
        """Cancel needs the credential but NOT the enable switch: an
        emergency stop must be able to cancel with submission off."""
        return self._send("DELETE", "/portfolio/orders/%s" % order_id, auth=True)
