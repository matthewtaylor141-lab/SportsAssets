"""Institutional PRE-PRODUCTION venue adapter (E35, 2026-09-10).

The mirror worker (workers/mirror_live) binds ONE venue module as `pmus`
and reaches everything through its attributes (inst/VENUE_CONTRACT.md).
This module is that second adapter: the institutional exchange's
PRE-PRODUCTION sandbox, spoken to over its REST API with a private-key
JWT, so the mirror's plumbing -- instrument lookup, scaling, post-only
placement, requote, cancel, positions, order state -- can be certified
against the institutional venue BEFORE any production switch. What the
owner asked for (2026-09-10 ~13:2xZ): "start using the preprod
environment for the institutional polymarket to test how this impacts
profitability". What preprod can and cannot prove was told to the owner
at 14:1xZ: its books are EMPTY (GET /v1/orderbook/<symbol> answers
`bids: [], offers: []` on a market the retail venue quotes live), dummy
funds, no counterparties observed, so it certifies MECHANICS, never
fills or profitability; the maker rebate question is production's.

THERE IS NO PRODUCTION HOST IN THIS MODULE. The auth domain and the API
base are constants (PMX_AUTH0_DOMAIN, PMX_BASE_URL); an environment
variable naming any other host makes the import refuse (`_host_guard`).
The production institutional venue is a separate lane and an explicit
owner order (real money).

THE CONTRACT THE MIRROR HOLDS THIS MODULE TO (inst/VENUE_CONTRACT.md):
  * every function is synchronous, blocking and thread-safe (the worker
    runs them under asyncio.to_thread, up to MIRROR_BOOK_CONCURRENCY=6
    books in flight);
  * `submit_fok` keeps its literal NAME and its positional order (slug,
    price, qty, sell, tif, intent, post_only, good_till) -- the worker's
    `_write_slots` reads `fn.__name__ == "submit_fok"` and `args[3]` as
    the sell flag -- and accepts the `paced_pair` keyword;
  * the resolvers (`resolve_market_exact`, `resolve_derivative_exact`,
    `resolve_team_yesno_exact`, `order_intent_for`, `_yn_name_match`)
    are the RETAIL module's own objects re-exported: the map lane
    compares `fn is pmus.resolve_market_exact` by identity, and the
    catalogue (slugs, sides, questions) is the retail venue's -- the
    institutional `symbol` IS the retail slug (aec-nfl-ne-sea-2026-09-09
    and four more, verified 13:45-14:06Z), though not every retail
    market exists there (aec-itfme-matesta-leoros-2026-09-10 -> 404
    "instrument does not exist");
  * `_get_client()` answers a shim with `.portfolio.positions(params)`
    and `.markets.retrieve_by_slug(slug)` in the retail SDK's wrapper
    shapes (`positions` / `nextCursor` / `eof`, `market`), so
    `mirror_shadow.account_positions_walk`, `_position_echo` and
    `_market_read` run unchanged;
  * refusals are RETURNED in the retail adapter's literal shapes and
    named; a lost response (5xx, timeout, dropped connection) RAISES;
    a 429 raises a RateLimitError-named exception carrying
    status_code 429 so `mirror_shadow.is_rate_limit` matches, and trips
    `venue_pace.penalize()` first.

WHAT IS DIFFERENT ON THIS VENUE, AND HOW IT IS READ:
  * ONE instrument per symbol, SIDE_BUY / SIDE_SELL, no intent field.
    The mirror's four intents map onto the long contract: BUY_LONG ->
    SIDE_BUY at L, SELL_LONG -> SIDE_SELL at L, BUY_SHORT -> SIDE_SELL
    at L, SELL_SHORT (the cover) -> SIDE_BUY at L, where L is the
    mirror's long-contract price (the retail adapter's BUY_SHORT
    expected cost is (1 - L) x qty, so L is the long price there too).
    A negative venue net position is the short.
  * Prices and quantities are INTEGER STRINGS scaled per instrument:
    priceScale 100 / tickSize 0.01 (esports, ATP, KBO/NPB, futures),
    1000 / 0.005 (MLB), 1000 / 0.001 (NFL); fractionalQtyScale 100 on
    every sports row seen ("Divide raw integer quantities by this
    value"). price_raw = round(L x priceScale) must land on the tick,
    else `tick_misaligned` -- NEVER silently rounded (the venue rejects
    "Price increment doesn't match tick size", and a rounded price is
    not his price). orderQty raw = contracts x fractionalQtyScale, and
    the preview's echo must equal that raw figure and the raw price
    (`preview_mismatch` otherwise, the retail path's own guard). The
    preview echoes orderQty VERBATIM ("1" -> "1", "100" -> "100";
    verified 14:15Z), so the echo cannot tell contracts from
    hundredths: the docs' rule is applied and the first sandbox fill's
    position read-back is the proof (open question in the notes).
  * There is NO GET-order-by-id. `order_status` reads the open-orders
    snapshot first (shared, refreshed at most every OPEN_SNAPSHOT_TTL_S)
    and then POST /v1/report/orders/search. There is NO documented
    executions / trades search body in inst/, so `recent_trades` is
    built from the report rows' `priceToQuantityFilled` and `cumQty`
    (see its docstring).
  * The venue's rate limits are per endpoint and LOW: ListInstruments
    6/min, GetBBO / GetOrderBook 12/min, SearchOrders 12/min, 429 body
    {"code": 8}. Each has a FAIL-CLOSED token bucket here (`BUCKETS`):
    an empty bucket answers the documented refusal / None / the stale
    cache and sends NOTHING, rather than earning a 429 that would trip
    the process-wide circuit for every lane. Beside them, every HTTP
    request passes `venue_pace.pace()` exactly once: the caller's own
    claim covers a call's FIRST request (`_paced`, `_venue_read`,
    `_paced_bbo`, the walk's per-page claim), and every further request
    a call makes claims its own gap inside (`_Claims`) -- the retail
    adapter's paced_pair rule, generalised, because on this venue a
    sell previews too and a placement reads the book back.
  * "Global Rate Limit Exceeded" on an insert is the venue's 5 s
    LATENCY STOPGAP, "not an actual rate limit ... do not throttle"
    (trader-guide/rate-limits): returned as the refusal
    `latency_reject`, no back-off, no circuit.
  * The mirror's maker wire must be priced against a REAL book, and
    preprod's is empty: `bbo_read` answers the RETAIL production book
    (pmus.bbo_read on the retail client) as `bid` / `ask` / `state`,
    and preprod's own top of book beside it as `pmx_bid` / `pmx_ask` /
    `pmx_state` when the 12/min budget allows (`pmx_state: "unread"`
    otherwise). THE QUOTE THE MIRROR PRICES FROM IS THE RETAIL VENUE'S.

Auth (the venue's own helper, .github/workflows/pmx-preprod.yml, which
is what worked from the runner at 13:45Z): an RS256 client-assertion
JWT {iss, sub, aud: "https://<domain>/", iat, exp: +60 s, jti} with the
key id in the header, exchanged form-encoded at /oauth/token with
client_id, client_assertion_type jwt-bearer, client_assertion, audience
= the API base, grant_type client_credentials. The token is cached and
re-minted after TOKEN_REFRESH_S = 150 s (the docs: "refreshed every
3 minutes"), under one lock. THE PRIVATE KEY AND THE TOKEN ARE NEVER
LOGGED and never appear in an exception's text.

Credentials are raw environment reads (PMX_CLIENT_ID, PMX_KEY_ID,
PMX_PRIVATE_KEY as PEM or base64 of a PEM, PMX_PARTICIPANT_ID,
PMX_ACCOUNT), read lazily at first use so the import never needs them;
a missing one raises by NAME at the call (a refusal on the money path,
never a silent default). No order path but GTC + post-only exists
here: any other tif is `ioc_refused`, a good_till is `gtd_refused`,
post_only False is `post_only_required`.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from . import pmus
from . import venue_pace
from .venue_pace import pace

log = logging.getLogger(__name__)

# ── THE HOSTS: constants, preprod only ──────────────────────────────
PMX_AUTH0_DOMAIN = "pmx-preprod.us.auth0.com"
PMX_BASE_URL = "https://api.preprod.polymarketexchange.com"
VENUE = "pmx_preprod"
# requests' (connect, read) timeout: the venue's docs put a 504 past 30 s
TIMEOUT = (10.0, 30.0)
TOKEN_REFRESH_S = 150.0
TOKEN_EXP_S = 60
# refdata is "static data - cache client-side" at 6/min; the docs' own
# example refreshes every 5 minutes
REF_TTL_S = 300.0
OPEN_SNAPSHOT_TTL_S = 2.0
REPORT_TTL_S = 10.0
# the report search refuses a body without pageSize (400 code 11,
# sandbox 14:2xZ); one page of this many rows per search
REPORT_PAGE_SIZE = 100
POS_TTL_S = 20.0
CATALOGUE_TTL_S = 600.0
_ORDER_MEMO_MAX = 4000

GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
ORDER_INTENT_BUY_LONG = "ORDER_INTENT_BUY_LONG"
ORDER_INTENT_BUY_SHORT = "ORDER_INTENT_BUY_SHORT"
ORDER_INTENT_SELL_LONG = "ORDER_INTENT_SELL_LONG"
ORDER_INTENT_SELL_SHORT = "ORDER_INTENT_SELL_SHORT"
# the four intents on the ONE long-contract instrument (module docstring)
_SIDE_FOR = {ORDER_INTENT_BUY_LONG: "SIDE_BUY", ORDER_INTENT_SELL_LONG: "SIDE_SELL",
             ORDER_INTENT_BUY_SHORT: "SIDE_SELL", ORDER_INTENT_SELL_SHORT: "SIDE_BUY"}
# the open-orders row's intent / venue_side, derived from the venue's
# side (the worker reads BUY / SELL off `side` and compares `venue_side`)
_INTENT_OF_SIDE = {"SIDE_BUY": ORDER_INTENT_BUY_LONG, "SIDE_SELL": ORDER_INTENT_SELL_LONG}
_VENUE_SIDE_OF = {"SIDE_BUY": "ORDER_SIDE_BUY", "SIDE_SELL": "ORDER_SIDE_SELL"}
INSTRUMENT_STATE_OPEN = "INSTRUMENT_STATE_OPEN"
# the latency stopgap's words (trader-guide/rate-limits): matched on the
# venue's message, the one case the docs themselves say to read by text
LATENCY_STOPGAP_TEXT = "Global Rate Limit Exceeded"
_CRED_NAMES = ("PMX_CLIENT_ID", "PMX_KEY_ID", "PMX_PRIVATE_KEY", "PMX_PARTICIPANT_ID",
               "PMX_ACCOUNT")


def _host_guard(env: Mapping[str, str] | None = None) -> None:
    """Refuse the import when the environment names any host but the
    preprod ones. The hosts are not inputs: the runner workflow fixes
    them the same way ("the base URLs are fixed to preprod below and
    are not inputs"), and a production host reached through this
    module by an environment edit would be real money through a lane
    built for dummy funds."""
    env = os.environ if env is None else env
    allowed = {"PMX_AUTH0_DOMAIN": {PMX_AUTH0_DOMAIN, f"https://{PMX_AUTH0_DOMAIN}"},
               "PMX_BASE_URL": {PMX_BASE_URL},
               "PMX_AUDIENCE": {PMX_BASE_URL}}
    for name, ok in allowed.items():
        v = str(env.get(name) or "").strip().rstrip("/").lower()
        if v and v not in ok:
            raise RuntimeError(f"pmx: {name}={v!r} is not the preprod host; this module has "
                               "no production host (E35)")


_host_guard()

# E35 review (D6): the two hosts as a LITERAL frozenset, checked on
# EVERY request -- the import-time guard reads the environment once,
# and a module constant rebound at runtime (a test fixture that leaks,
# a REPL) would otherwise send the next request wherever it points
_PREPROD_HOSTS = frozenset({"api.preprod.polymarketexchange.com", "pmx-preprod.us.auth0.com"})


def _assert_preprod(url: str) -> str:
    host = str(urlsplit(url).hostname or "").lower()
    if host not in _PREPROD_HOSTS:
        raise RuntimeError(f"pmx: refusing a request to {host!r}: not a preprod host (E35)")
    return url


# ── errors ───────────────────────────────────────────────────────────

class PmxError(RuntimeError):
    """This adapter's own raise: a lost or unreadable response."""


class APIStatusError(PmxError):
    """The venue answered an HTTP status >= 400. `status_code` is the
    int the worker's readers key on (`_raw_rate_limit`, `is_rate_limit`
    read an int status as authoritative); `body` the parsed JSON body
    (or the text head) for the refusal receipt."""

    def __init__(self, status_code: int, message: str, body: Any = None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = message
        self.body = body


class RateLimitError(APIStatusError):
    """A 429: named so `mirror_shadow.is_rate_limit` matches on the
    class name, as it does the retail SDK's."""


class RateBudget(PmxError):
    """A per-endpoint bucket is empty: NOTHING was sent. Not a venue
    429 (the class name carries no 'RateLimit' on purpose: the pacer's
    circuit is for the venue's refusals, not our own restraint)."""


# ── the per-endpoint fail-closed buckets ─────────────────────────────

class _Bucket:
    """A token bucket of `per_minute` requests: full at start, refilled
    at per_minute / 60 a second, never waited on -- `take` answers
    False and the caller answers its documented refusal."""

    def __init__(self, per_minute: int):
        self.cap = float(per_minute)
        self.rate = float(per_minute) / 60.0
        self.tokens = float(per_minute)
        self.at = time.monotonic()
        self.lock = threading.Lock()

    def take(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else float(now)
        with self.lock:
            self.tokens = min(self.cap, self.tokens + max(0.0, now - self.at) * self.rate)
            self.at = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False

    def reset(self) -> None:
        with self.lock:
            self.tokens = self.cap
            self.at = time.monotonic()


BUCKETS: dict[str, _Bucket] = {"refdata": _Bucket(6), "book": _Bucket(12), "report": _Bucket(12)}


class _Claims:
    """Which requests of one adapter call claim a gap on the process-wide
    pacer. The caller claimed one gap for the call's FIRST request
    (`_paced` / `_venue_read` / `_paced_bbo` / the walk's per-page
    claim), so `next()` answers False once and True for every request
    after -- one claim per HTTP request, never two for one, never none.
    `first_claimed=False` claims every request (the bbo read: the
    caller's gap went to the retail read that precedes it)."""

    def __init__(self, first_claimed: bool = True):
        self._pending = bool(first_claimed)

    def next(self) -> bool:
        if self._pending:
            self._pending = False
            return False
        return True


# ── credentials, token, session ──────────────────────────────────────

_session = None
_session_lock = threading.Lock()


def _session_get():
    """The requests.Session, built lazily; tests inject their own
    (`pmx._session = fake`) and never touch the network."""
    global _session
    with _session_lock:
        if _session is None:
            import requests
            _session = requests.Session()
        return _session


def _env(name: str) -> str:
    v = str(os.environ.get(name) or "").strip()
    if not v:
        raise RuntimeError(f"pmx: {name} unset")
    return v


def _private_key() -> str:
    """PMX_PRIVATE_KEY as PEM text: the PEM itself, or base64 of it (the
    runner's input shape). The value never appears in a message."""
    raw = _env("PMX_PRIVATE_KEY")
    if "BEGIN" in raw:
        return raw.replace("\\n", "\n")
    try:
        dec = base64.b64decode(raw).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 -- named, never the value
        raise RuntimeError("pmx: PMX_PRIVATE_KEY is neither a PEM nor base64 of one") from exc
    if "BEGIN" not in dec:
        raise RuntimeError("pmx: PMX_PRIVATE_KEY decodes to something that is not a PEM")
    return dec


_tok_lock = threading.Lock()
_tok: dict[str, Any] = {"value": None, "minted": 0.0}


def _mint_token() -> str:
    """One token request: the client-assertion JWT signed RS256 with the
    key id in the header, exchanged form-encoded (the venue's helper's
    shape; the docs' JSON body was not what worked from the runner).
    Paced like every request. The token is returned, never logged."""
    import jwt

    cid, kid = _env("PMX_CLIENT_ID"), _env("PMX_KEY_ID")
    key = _private_key()
    iat = int(time.time())
    assertion = jwt.encode({"iss": cid, "sub": cid, "aud": f"https://{PMX_AUTH0_DOMAIN}/",
                            "iat": iat, "exp": iat + TOKEN_EXP_S, "jti": str(uuid.uuid4())},
                           key, algorithm="RS256", headers={"kid": kid})
    pace()
    r = _session_get().request(
        "POST", _assert_preprod(f"https://{PMX_AUTH0_DOMAIN}/oauth/token"),
        data={"client_id": cid,
              "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
              "client_assertion": assertion, "audience": PMX_BASE_URL,
              "grant_type": "client_credentials"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT)
    body = _json_of(r)
    code = int(getattr(r, "status_code", 0) or 0)
    tok = body.get("access_token") if isinstance(body, dict) else None
    if code != 200 or not tok:
        # the error's WORD only: a token body is never echoed
        why = ((body or {}).get("error") or (body or {}).get("error_description") or ""
               if isinstance(body, dict) else "")
        raise APIStatusError(code, f"pmx: token request answered {code}: {str(why)[:120]}", None)
    return str(tok)


def _token(now: float | None = None) -> str:
    """The cached bearer, re-minted after TOKEN_REFRESH_S under one
    lock (so N threads waking together mint once)."""
    now = time.monotonic() if now is None else float(now)
    with _tok_lock:
        if _tok["value"] and now - float(_tok["minted"]) < TOKEN_REFRESH_S:
            return str(_tok["value"])
        tok = _mint_token()
        _tok["value"], _tok["minted"] = tok, now
        return tok


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}",
            "x-participant-id": _env("PMX_PARTICIPANT_ID"),
            "X-Client-Id": _env("PMX_CLIENT_ID"),
            "X-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json"}


def _json_of(r) -> Any:
    try:
        return r.json()
    except Exception:  # noqa: BLE001 -- not JSON: the caller reads the status
        return None


def _err_text(r, body: Any) -> str:
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])[:500]
    return str(getattr(r, "text", "") or "")[:500]


def _receipt_body(body: Any) -> Any:
    """The body as the refusal receipt carries it (pmus._post_only_refusal's
    rule: JSON scalars and containers as they are, anything else as text)."""
    if body is not None and not isinstance(body, (dict, list, str, int, float, bool)):
        return str(body)[:500]
    return body


def _request(method: str, path: str, *, json_body: Any = None, params: Any = None,
             claims: _Claims | None = None, auth: bool = True) -> Any:
    """One HTTP request to the venue: the headers on every call, one
    pacer claim when `claims` says so (None: always), the (10 s, 30 s)
    timeouts. A 429 trips the pacer's circuit and raises RateLimitError;
    any other >= 400 raises APIStatusError with the int status and the
    body; a 200 without a JSON body raises PmxError (unreadable is a
    raise, never {}). A 401 drops the cached token so the NEXT call
    re-mints; this one is reported, never retried blind."""
    headers = _headers() if auth else {"X-Request-Id": str(uuid.uuid4())}
    if claims is None or claims.next():
        pace()
    r = _session_get().request(method, _assert_preprod(PMX_BASE_URL + path), headers=headers,
                               params=params, json=json_body, timeout=TIMEOUT)
    code = int(getattr(r, "status_code", 0) or 0)
    body = _json_of(r)
    if code == 429:
        venue_pace.penalize()
        raise RateLimitError(code, _err_text(r, body), _receipt_body(body))
    if code == 401:
        with _tok_lock:
            _tok["value"] = None
    if code >= 400:
        raise APIStatusError(code, _err_text(r, body), _receipt_body(body))
    if body is None:
        raise PmxError(f"pmx: {method} {path} answered {code} without a JSON body")
    return body


# ── numbers ──────────────────────────────────────────────────────────

def _int_of(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f != int(f):
        return None
    return int(f)


def _float_of(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _raw_div(v: Any, scale: int) -> float:
    """A raw integer string over its scale; 0.0 when absent or
    unreadable (the retail rows' own literal for an unreadable price)."""
    f = _float_of(v)
    if f is None or not scale:
        return 0.0
    return f / float(scale)


def _epoch(v: Any) -> float | None:
    """RFC3339 text -> epoch seconds; None when absent or unreadable
    (nanosecond fractions are cut to microseconds first)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        head, tail = s.split(".", 1)
        frac = "".join(ch for ch in tail if ch.isdigit())
        rest = tail[len(frac):]
        s = f"{head}.{frac[:6]}{rest}" if frac else head + rest
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _order_of(resp: Any, keys: tuple[str, ...]) -> dict:
    if not isinstance(resp, dict):
        return {}
    for k in keys:
        o = resp.get(k)
        if isinstance(o, dict):
            return o
    return {}


def _field(o: dict, *keys: str) -> Any:
    """The first present key: the schema's camelCase or the quickstart's
    snake_case (the docs disagree; both are tolerated)."""
    for k in keys:
        if k in o and o[k] is not None:
            return o[k]
    return None


# ── reference data (6/min, cached) ───────────────────────────────────

_ref_lock = threading.Lock()
_ref: dict[str, tuple[dict | None, float]] = {}


def _instruments(symbols: list[str], claims: _Claims | None = None) -> dict[str, dict | None]:
    """{symbol: instrument row | None (the venue has none)} for the
    symbols, from the 5-minute cache, one batched POST /v1/refdata/
    instruments for the misses. Refdata by symbols answers only the
    ones that exist (verified 14:15Z), so an absent symbol is a None
    remembered for the TTL -- a `no_instrument` refusal never burns the
    6/min budget twice. Empty bucket: a cached entry of ANY age answers
    (stale reference data beats no read); an uncached symbol raises
    RateBudget, which the callers turn into their `rate_budget` refusal.
    A 404 on the call is read as "none of them exist" (the venue's
    "instrument does not exist" is a 404)."""
    now = time.monotonic()
    want = sorted({str(s or "").strip().lower() for s in symbols if s})
    out: dict[str, dict | None] = {}
    miss: list[str] = []
    with _ref_lock:
        for s in want:
            ent = _ref.get(s)
            if ent is not None and now - ent[1] < REF_TTL_S:
                out[s] = ent[0]
            else:
                miss.append(s)
    if not miss:
        return out
    if not BUCKETS["refdata"].take(now):
        with _ref_lock:
            for s in miss:
                ent = _ref.get(s)
                if ent is None:
                    raise RateBudget(f"pmx: refdata budget empty and {s} uncached")
                out[s] = ent[0]
        return out
    try:
        resp = _request("POST", "/v1/refdata/instruments",
                        json_body={"symbols": miss, "pageSize": min(1000, max(50, len(miss)))},
                        claims=claims)
    except APIStatusError as exc:
        if exc.status_code != 404:
            raise
        rows: list = []
    else:
        rows = resp.get("instruments") or [] if isinstance(resp, dict) else []
    found: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("symbol"):
            found[str(row["symbol"]).strip().lower()] = row
    with _ref_lock:
        for s in miss:
            _ref[s] = (found.get(s), now)
            out[s] = found.get(s)
    return out


class Scaling:
    """One instrument's two scales, and the conversions in BOTH
    directions: `price_scale` (priceScale: 100 or 1000), `tick_raw`
    (the tick in raw price units: 1 / 5 / 1), `qty_scale`
    (fractionalQtyScale: 100). Built from the instrument row
    (`Scaling.of`) or by hand; the sandbox probe of 14:19-14:30Z showed
    the venue itself never answers the quantity question (it echoed
    orderQty "100" verbatim, then dropped the order), so the scale is
    an explicit constructor argument read off the instrument, the docs'
    rule ("Divide raw integer quantities by this value") applied in
    both directions and pinned by the unit tests.

    tickSize is read as a decimal price when under 1 (0.01 / 0.005 /
    0.001, as the sandbox prints it) and as raw units otherwise (the
    OpenAPI types it integer); either way it must be a whole number of
    raw units."""

    __slots__ = ("price_scale", "tick_raw", "qty_scale")

    def __init__(self, price_scale: int, tick_raw: int, qty_scale: int):
        if price_scale < 1 or tick_raw < 1 or qty_scale < 1:
            raise ValueError("pmx: a scale under 1")
        self.price_scale = int(price_scale)
        self.tick_raw = int(tick_raw)
        self.qty_scale = int(qty_scale)

    @classmethod
    def of(cls, row: dict | None) -> "Scaling | str":
        """The instrument row's scaling, or the refusal name
        `scale_unreadable`."""
        if not isinstance(row, dict):
            return "scale_unreadable"
        ps = _int_of(row.get("priceScale"))
        fq = _int_of(row.get("fractionalQtyScale"))
        tick = _float_of(row.get("tickSize"))
        if not ps or ps <= 0 or not fq or fq <= 0 or tick is None or tick <= 0:
            return "scale_unreadable"
        tick_raw = tick if tick >= 1 else tick * ps
        if abs(tick_raw - round(tick_raw)) > 1e-6 or round(tick_raw) < 1:
            return "scale_unreadable"
        return cls(ps, int(round(tick_raw)), fq)

    def price_raw(self, limit_price: float) -> int | None:
        """round(L x priceScale) when L sits exactly on the scale AND on
        the tick and is strictly inside (0, 1); None otherwise -- the
        caller refuses `tick_misaligned`, nothing is rounded toward the
        venue's ladder on our behalf."""
        x = float(limit_price) * self.price_scale
        raw = int(round(x))
        if abs(x - raw) > 1e-6 or raw <= 0 or raw >= self.price_scale or raw % self.tick_raw:
            return None
        return raw

    def price(self, raw: Any) -> float:
        """A raw price string over priceScale; 0.0 unreadable (the
        retail row's literal)."""
        return _raw_div(raw, self.price_scale)

    def qty_raw(self, contracts: int) -> int:
        """Whole contracts -> the raw orderQty integer (x qty_scale)."""
        return int(contracts) * self.qty_scale

    def contracts(self, raw: Any) -> float:
        """A raw quantity string over qty_scale; 0.0 unreadable."""
        return _raw_div(raw, self.qty_scale)

    def as_tuple(self) -> tuple[int, int, int]:
        return self.price_scale, self.tick_raw, self.qty_scale


def _scale(row: dict | None) -> tuple[int, int, int] | str:
    """(priceScale, tick_raw, fractionalQtyScale) or `scale_unreadable`:
    Scaling.of as a tuple, for the readers that unpack it."""
    sc = Scaling.of(row)
    return sc if isinstance(sc, str) else sc.as_tuple()


def _price_raw(limit_price: float, ps: int, tick_raw: int) -> int | None:
    return Scaling(ps, tick_raw, 1).price_raw(limit_price)


def _refuse(status: str, **raw: Any) -> dict:
    """The retail adapter's refusal literal: the same five top-level
    keys, `ok` False, no id, nothing filled."""
    return {"ok": False, "order_id": None, "status": status, "fill_price": None,
            "filled_shares": 0.0, "raw": raw}


# ── the order memo (id -> symbol) and the open-orders snapshot ────────

_memo_lock = threading.Lock()
_order_symbol: dict[str, str] = {}
# E35 review (D1): the wire intent this process sent under a clordId,
# written BEFORE the insert goes out. The venue has no intent field, so
# a listing's row can only carry the intent for orders this process
# placed -- and the worker reads it: the S4 read-back proof demands
# `intent == ORDER_INTENT_SELL_SHORT` on the cover's row
# (mirror_live._s4_probe) and the lost-placement search matches intent
# to intent when both name one (_on_book_matches). Derived from the
# side (BUY_LONG / SELL_LONG) for every other row, as the design said.
_intent_by_clord: dict[str, str] = {}
# E35 review (D2): the ids this process asked the venue to cancel, with
# the monotonic time of the request -- an order_status inside
# CANCEL_FRESH_S of a cancel never answers from the shared snapshot
_cancelled_at: dict[str, float] = {}
CANCEL_FRESH_S = 60.0
_open_lock = threading.Lock()
_open_snap: dict[str, Any] = {"ts": 0.0, "rows": {}}


def _memo_put(memo: dict, key: Any, value: Any) -> None:
    if not key:
        return
    with _memo_lock:
        if len(memo) >= _ORDER_MEMO_MAX:
            for k in list(memo)[: _ORDER_MEMO_MAX // 4]:
                memo.pop(k, None)
        memo[str(key)] = value


def _remember(order_id: Any, symbol: str) -> None:
    """The symbol an order id belongs to, learned at placement and on
    every listing: the report search is by symbols, never by id."""
    if not order_id or not symbol:
        return
    _memo_put(_order_symbol, order_id, symbol)


def _norm_order(o: dict, inst: dict | None) -> dict:
    """One venue order -> the desk's 13-key open-order row, the retail
    `_norm_order` literal key for key: `intent` derived from the venue's
    side (SIDE_BUY -> BUY_LONG, SIDE_SELL -> SELL_LONG), `side` the
    desk's BUY / SELL off it, `venue_side` ORDER_SIDE_BUY / _SELL,
    price = raw / priceScale, quantities = raw / fractionalQuantityScale
    (the order's own, else the instrument's), state lowercase with the
    prefix stripped, tif with its prefix stripped. Raises when the
    instrument's scale cannot be read: a row whose price cannot be
    scaled is not a row (the retail literal's 0.0 would read as a
    price to requote against)."""
    sym = str(_field(o, "symbol") or "")
    ps, fq = _order_scales(o, inst)
    if ps is None or fq is None:
        raise PmxError(f"pmx: open order {_field(o, 'id')} on {sym}: scale_unreadable")
    side = str(_field(o, "side") or "")
    with _memo_lock:
        memo_intent = _intent_by_clord.get(str(_field(o, "clordId", "clord_id") or ""))
    intent = memo_intent or _INTENT_OF_SIDE.get(side)
    state = str(_field(o, "state") or "")
    ea = inst.get("eventAttributes") if isinstance(inst, dict) else None
    title_src = (inst.get("description") if isinstance(inst, dict) else None) or (
        (ea or {}).get("question") if isinstance(ea, dict) else None)
    return {
        "order_id": _field(o, "id"),
        "us_market_slug": sym or None,
        "intent": intent,
        "side": ("SELL" if "SELL" in str(intent or "") else "BUY"),
        "venue_side": _VENUE_SIDE_OF.get(side),
        "price": _raw_div(_field(o, "price"), ps),
        "quantity": _raw_div(_field(o, "orderQty", "order_qty"), fq),
        "filled_shares": _raw_div(_field(o, "cumQty", "cum_qty"), fq),
        "leaves": _raw_div(_field(o, "leavesQty", "leaves_qty"), fq),
        "avg_px": (_raw_div(_field(o, "avgPx", "avg_px"), ps) or None),
        "state": (state.replace("ORDER_STATE_", "").lower() or "unknown"),
        "title": pmus._clean_title(title_src),
        "created_at": _field(o, "createTime", "insertTime", "create_time", "insert_time"),
        "tif": str(_field(o, "timeInForce", "time_in_force") or "").replace("TIME_IN_FORCE_", ""),
    }


def _order_scales(o: dict, inst: dict | None) -> tuple[int | None, int | None]:
    """(priceScale, fractionalQuantityScale) for one venue order: THE
    ORDER'S OWN FIGURES FIRST (the Order prints both, "copied from the
    instrument at order creation time" -- the schema's words; the
    preview printed priceScale "100" / fractionalQuantityScale "100"),
    the instrument row's when the order carries none, None when
    neither states one. E35 review (D4): refdata by symbols answers
    only the instruments that exist, so an open order on a symbol the
    catalogue has dropped (expired, delisted) used to make EVERY
    whole-account listing raise -- the worker's `open_orders_unreadable`
    for every tick until that order was gone -- when the order itself
    states the scales it was created with."""
    ps = _int_of(_field(o, "priceScale", "price_scale"))
    fq = _int_of(_field(o, "fractionalQuantityScale", "fractional_quantity_scale"))
    if (ps is None or fq is None) and isinstance(inst, dict):
        sc = _scale(inst)
        if not isinstance(sc, str):
            ps = ps if ps is not None else sc[0]
            fq = fq if fq is not None else sc[2]
    if ps is not None and ps < 1:
        ps = None
    if fq is not None and fq < 1:
        fq = None
    return ps, fq


def _rows_of(raw_rows: list, claims: _Claims | None) -> list[tuple[dict, dict]]:
    """[(row, raw order)] for a listing, the instruments batched in one
    refdata read; every id remembered with its symbol."""
    rows = [o for o in raw_rows if isinstance(o, dict)]
    symbols = sorted({str(_field(o, "symbol") or "").lower() for o in rows if _field(o, "symbol")})
    inst = _instruments(symbols, claims) if symbols else {}
    out: list[tuple[dict, dict]] = []
    for o in rows:
        sym = str(_field(o, "symbol") or "").lower()
        row = _norm_order(o, inst.get(sym))
        _remember(row["order_id"], sym)
        out.append((row, o))
    return out


def _open_read(slugs: list[str] | None, claims: _Claims) -> list[tuple[dict, dict]]:
    """GET /v1/trading/orders/open (symbols filter when given); the
    whole-account read replaces the shared snapshot, a filtered read
    updates the rows it saw."""
    params = {"symbols": [str(s) for s in slugs]} if slugs else None
    resp = _request("GET", "/v1/trading/orders/open", params=params, claims=claims)
    pairs = _rows_of(resp.get("orders") or [] if isinstance(resp, dict) else [], claims)
    with _open_lock:
        if not slugs:
            _open_snap["rows"] = {str(r["order_id"]): (r, o) for r, o in pairs if r["order_id"]}
            _open_snap["ts"] = time.monotonic()
        else:
            for r, o in pairs:
                if r["order_id"]:
                    _open_snap["rows"][str(r["order_id"])] = (r, o)
    return pairs


def _open_snapshot(claims: _Claims, force: bool = False) -> dict[str, tuple[dict, dict]]:
    """The whole-account open orders, refreshed when older than
    OPEN_SNAPSHOT_TTL_S (or when `force` says so: a read right after a
    cancel must be the venue's, never the snapshot that still lists
    the order -- E35 review, D2); a refresh that fails raises (never a
    stale answer dressed as fresh)."""
    with _open_lock:
        fresh = time.monotonic() - float(_open_snap["ts"]) < OPEN_SNAPSHOT_TTL_S
        rows = dict(_open_snap["rows"])
    if fresh and not force:
        return rows
    _open_read(None, claims)
    with _open_lock:
        return dict(_open_snap["rows"])


# ── the report search (12/min, cached 10 s) ──────────────────────────

_rep_lock = threading.Lock()
_rep: dict[str, tuple[Any, float]] = {}


def _report_search(body: dict, claims: _Claims | None) -> tuple[Any, bool]:
    """POST /v1/report/orders/search with `body`: (response, fresh).
    The body the sandbox accepted (14:19-14:30Z): `symbols` or
    `orderIds` (+ a stateFilter the callers do not send: every state is
    wanted), and ALWAYS `pageSize` -- without it the venue refuses 400
    code 11 "Maximum requested records is 5000". The answer's rows are
    under `order` (the sandbox's key; `orders`, the quickstart's, is
    read too), beside `nextPageToken` and `lastExecutions`. A fresh
    cached answer (10 s) is returned without a request; an empty bucket
    answers the stale cached one (fresh False) or raises RateBudget
    when there is none."""
    body = {**body, "pageSize": int(body.get("pageSize") or REPORT_PAGE_SIZE)}
    key = json.dumps(body, sort_keys=True)
    now = time.monotonic()
    with _rep_lock:
        ent = _rep.get(key)
    if ent is not None and now - ent[1] < REPORT_TTL_S:
        return ent[0], True
    if not BUCKETS["report"].take(now):
        if ent is None:
            raise RateBudget("pmx: report budget empty and no cached search")
        return ent[0], False
    resp = _request("POST", "/v1/report/orders/search", json_body=body, claims=claims)
    with _rep_lock:
        _rep[key] = (resp, now)
        if len(_rep) > 256:
            for k in [k for k, v in _rep.items() if now - v[1] >= REPORT_TTL_S]:
                _rep.pop(k, None)
    return resp, True


def _report_rows(resp: Any) -> list[dict]:
    """The orders on a report page: `order` (the sandbox's key) or
    `orders` (the quickstart's)."""
    if not isinstance(resp, dict):
        return []
    rows = resp.get("order")
    if not isinstance(rows, list):
        rows = resp.get("orders")
    return [o for o in (rows or []) if isinstance(o, dict)]


def _report_executions(resp: Any, order_id: str, ps: int | None = None,
                       fq: int | None = None) -> list[dict] | None:
    """The page's `lastExecutions` that name `order_id` as the retail
    execution records (the Execution schema is documented: id, order,
    lastShares, lastPx, type, tradeId, aggressor, ...); None when the
    page carries no list ("the venue sent no list" is not an empty
    one). The list's relation to the searched orders is not documented
    beyond its name, so only records whose nested order is this one
    are kept. E35 review (D7): `last_px` / `last_shares` /
    `commission_usd` are the venue's raw integers, scaled here onto the
    retail record's units (price 0-1, contracts, USD) when the scales
    are known; None when a figure cannot be scaled -- never a raw 55
    dressed as a price."""
    if not isinstance(resp, dict) or not isinstance(resp.get("lastExecutions"), list):
        return None
    out = []
    for ex in resp["lastExecutions"]:
        if not isinstance(ex, dict):
            continue
        o = ex.get("order") if isinstance(ex.get("order"), dict) else {}
        if str(_field(o, "id") or "") != order_id:
            continue
        rec = pmus._execution_record(ex)
        px, sh, usd = rec.get("last_px"), rec.get("last_shares"), rec.get("commission_usd")
        rec["last_px"] = (px / float(ps)) if (px is not None and ps) else None
        rec["last_shares"] = (sh / float(fq)) if (sh is not None and fq) else None
        rec["commission_usd"] = (usd / float(ps * fq)) if (usd is not None and ps and fq) else None
        out.append(rec)
    return out


def _page_truncated(resp: Any) -> bool:
    tok, eof = _report_page_token(resp)
    return bool(tok) and not eof


def _report_page_token(resp: Any) -> tuple[str, bool]:
    """(next page token, eof) in whichever spelling the page carries."""
    if not isinstance(resp, dict):
        return "", True
    tok = _field(resp, "nextPageToken", "next_page_token", "nextCursor", "next_cursor")
    return (str(tok) if tok else ""), bool(resp.get("eof"))


def _commission_usd(o: dict, ps: int, fq: int) -> float | None:
    """commissionNotionalTotalCollected on the order, scaled by
    priceScale x fractionalQuantityScale (fees.md: "scaled by
    price_scale and fractional_quantity_scale"); None absent or
    unreadable, never a guess."""
    v = _float_of(_field(o, "commissionNotionalTotalCollected", "commission_notional_total_collected"))
    if v is None or not ps or not fq:
        return None
    return v / float(ps * fq)


# ── positions (the shim) and the retail catalogue ────────────────────

_cat_lock = threading.Lock()
_catalogue: dict[str, tuple[dict, float]] = {}
_pos_lock = threading.Lock()
_pos_cache: dict[str, Any] = {"ts": 0.0, "net": {}}


def _catalogue_meta(slug: str, claims: _Claims | None) -> dict:
    """The retail catalogue's outcome / title for a slug (10-minute
    cache), the marketMetadata `_position_echo` reads. Unreadable is an
    empty dict: an echo with no side named is `unverified`, never a
    guessed side. One retail request per miss, claimed."""
    now = time.monotonic()
    with _cat_lock:
        ent = _catalogue.get(slug)
    if ent is not None and now - ent[1] < CATALOGUE_TTL_S:
        return dict(ent[0])
    meta: dict = {}
    try:
        if claims is None or claims.next():
            pace()
        m = (pmus._get_client().markets.retrieve_by_slug(slug) or {}).get("market") or {}
        if isinstance(m, dict) and m:
            meta = {"outcome": m.get("outcome"),
                    "title": m.get("title") or m.get("question") or m.get("name")}
    except Exception as exc:  # noqa: BLE001 -- no metadata is named, never invented
        meta = {"error": type(exc).__name__}
    with _cat_lock:
        _catalogue[slug] = (meta, now)
    return dict(meta)


class _Portfolio:
    """`.positions(params)` in the retail SDK's wrapper shape:
    `{"positions": {slug: {netPosition, cost, expired, marketMetadata}},
    "nextCursor": None, "eof": True}` -- one page, the venue's GET
    /v1/positions?name=<account> is not cursor-paged (14:15Z: `name`
    is required, 400 without it). netPosition is the venue's signed
    raw quantity over the instrument's fractionalQtyScale, i.e. signed
    CONTRACTS (a negative net is the short); cost over priceScale x
    fractionalQtyScale. A row whose symbol or scale cannot be read
    RAISES: the walk is then unreadable by name
    (account_positions_walk's own rule), never a walk missing a slug."""

    def positions(self, params: dict | None = None, _claims: _Claims | None = None) -> dict:
        # the caller paces per page (the walk, _position_echo, _pm_held);
        # an adapter call that reads positions mid-call hands its own
        # accounting in
        c = _Claims() if _claims is None else _claims
        resp = _request("GET", "/v1/positions", params={"name": _env("PMX_ACCOUNT")}, claims=c)
        rows = [p for p in ((resp.get("positions") if isinstance(resp, dict) else None) or [])
                if isinstance(p, dict)]
        symbols = sorted({str(p.get("symbol") or "").lower() for p in rows if p.get("symbol")})
        inst = _instruments(symbols, c) if symbols else {}
        out: dict[str, dict] = {}
        for p in rows:
            sym = str(p.get("symbol") or "").strip().lower()
            if not sym:
                raise PmxError("pmx: a position row carries no symbol")
            sc = _scale(inst.get(sym))
            if isinstance(sc, str):
                raise PmxError(f"pmx: position on {sym}: {sc}")
            ps, _tick, fq = sc
            net = _float_of(p.get("netPosition"))
            if net is None:
                raise PmxError(f"pmx: position on {sym} carries an unreadable netPosition")
            cost = _float_of(p.get("cost"))
            out[sym] = {"netPosition": net / float(fq),
                        "cost": (cost / float(ps * fq)) if cost is not None else None,
                        "expired": bool(p.get("expired")),
                        "marketMetadata": _catalogue_meta(sym, c)}
        return {"positions": out, "nextCursor": None, "eof": True}


class _Markets:
    """`.retrieve_by_slug(slug)` delegated to the retail PUBLIC client:
    the catalogue identity the map lane and `_market_read` rely on."""

    def retrieve_by_slug(self, slug: str):
        return pmus._get_client().markets.retrieve_by_slug(slug)


class _Shim:
    def __init__(self):
        self.portfolio = _Portfolio()
        self.markets = _Markets()


_shim: _Shim | None = None
_shim_lock = threading.Lock()


def _get_client() -> _Shim:
    """The process-global shim (the retail `_get_client` memo shape)."""
    global _shim
    with _shim_lock:
        if _shim is None:
            _shim = _Shim()
        return _shim


def _net_position(symbol: str, claims: _Claims | None, fresh: bool = False) -> float | None:
    """Signed net contracts on `symbol` from the venue (20 s cache;
    `fresh` reads the venue now -- the sell guard's read, E35 review
    D5); None when the read fails -- the caller REFUSES on None."""
    now = time.monotonic()
    with _pos_lock:
        if not fresh and now - float(_pos_cache["ts"]) < POS_TTL_S:
            return float(_pos_cache["net"].get(symbol, 0.0))
    try:
        pos = _Portfolio().positions(_claims=claims)
    except Exception as exc:  # noqa: BLE001 -- unreadable is None, the caller refuses
        log.warning("pmx: position read failed (%s: %s)", type(exc).__name__, str(exc)[:160])
        return None
    net = {k: float(v.get("netPosition") or 0.0) for k, v in (pos.get("positions") or {}).items()}
    with _pos_lock:
        _pos_cache["ts"], _pos_cache["net"] = now, net
    return float(net.get(symbol, 0.0))


# ── the contract's reads ─────────────────────────────────────────────

def open_orders(slugs: list[str] | None = None) -> list[dict]:
    """The account's resting orders as `_norm_order` rows (GET
    /v1/trading/orders/open, symbols filter when given). Raises to the
    caller: unreadable is never []."""
    return [r for r, _o in _open_read(slugs, _Claims())]


_rep_rows: dict[str, tuple[dict, dict, list[dict] | None, float]] = {}
# E35 review (D8): how many ids ride one report search beside the one
# asked for -- every id this process knows that the open snapshot no
# longer lists (terminal, or vanished), most recent first
REPORT_BATCH_IDS = 20


def _gone_ids(oid: str) -> list[str]:
    with _open_lock:
        open_ids = set(_open_snap["rows"])
    with _memo_lock:
        known = list(_order_symbol)
    out: list[str] = []
    for i in reversed(known):
        if i != oid and i not in open_ids:
            out.append(i)
            if len(out) >= REPORT_BATCH_IDS - 1:
                break
    return out


def _report_lookup(oid: str, claims: _Claims) -> tuple[dict, dict, list[dict] | None] | None:
    """The report search by `orderIds` (the sandbox accepted it, 14:2xZ):
    (row, raw order, the page's execution records for it | None), or
    None when the venue has no record of the id. RateBudget propagates.

    E35 review (D8): the 12/min report budget is spent per TERMINAL
    TRANSITION -- every cancel's re-read and every order that left the
    open list costs one search -- so a tick that finishes more than
    twelve orders in a minute used to freeze the thirteenth
    `order_state_unknown`. One search now asks for the id AND the ids
    this process knows that the open snapshot no longer lists (at most
    REPORT_BATCH_IDS), and every row on the page is cached BY ID for
    REPORT_TTL_S; a status read of any of them inside that window costs
    nothing. A page with a next-page token that does not carry the id
    RAISES (the id may be on a page never read: not "no record")."""
    now = time.monotonic()
    with _rep_lock:
        ent = _rep_rows.get(oid)
    if ent is not None and now - ent[3] < REPORT_TTL_S:
        return ent[0], ent[1], ent[2]
    ids = sorted({oid, *_gone_ids(oid)})
    resp, fresh = _report_search({"orderIds": ids}, claims)
    found = None
    for row, o in _rows_of(_report_rows(resp), claims):
        rid = str(row["order_id"])
        sym = str(row["us_market_slug"] or "").lower()
        ps, fq = _order_scales(o, _instruments([sym], claims).get(sym) if sym else None)
        execs = _report_executions(resp, rid, ps, fq)
        if fresh:
            with _rep_lock:
                _rep_rows[rid] = (row, o, execs, now)
                if len(_rep_rows) > 512:
                    for k in [k for k, v in _rep_rows.items() if now - v[3] >= REPORT_TTL_S]:
                        _rep_rows.pop(k, None)
        if rid == oid:
            found = (row, o, execs)
    if found is None and _page_truncated(resp):
        raise PmxError(f"pmx: report page for {oid} truncated (a next page token, the id not on this one)")
    return found


def order_status(order_id: str) -> dict | None:
    """One order by id: the open-orders snapshot first (shared,
    refreshed at most every OPEN_SNAPSHOT_TTL_S), else the report
    search by `orderIds`; None when the venue has no record of it ON
    EITHER SURFACE -- the sandbox's own behaviour on 14:19Z's probe
    order (200 {orderId}, then nothing on open orders, positions or
    any report search): "not found anywhere" is a terminal-unknown
    answer, and the worker's `_reconcile_open` names it
    `order_state_unknown` and freezes the book with the id kept. Adds
    `commission_usd` (the order's commissionNotionalTotalCollected,
    scaled), `commission_spread_px` None (the venue states no spread on
    an order) and `executions` (the report page's `lastExecutions` for
    this order as retail execution records when the page carried the
    list, None from the open-orders snapshot: no list there). An empty
    report budget with nothing cached is None too: the worker collapses
    a raise and a None alike, and nothing is sent."""
    c = _Claims()
    oid = str(order_id)
    now = time.monotonic()
    with _memo_lock:
        cancelled = _cancelled_at.get(oid)
    # E35 review (D2): after a cancel this process sent, the snapshot
    # (refreshed at most every 2 s) can still list the order for a read
    # made 0.3 s later -- CANCEL_READS x CANCEL_READ_GAP_S of the
    # worker's re-reads all answered "still open" and the book froze
    # `cancel_pending`; so the read is the venue's, forced
    force = cancelled is not None and now - float(cancelled) < CANCEL_FRESH_S
    snap = _open_snapshot(c, force=force)
    hit = snap.get(oid)
    execs: list[dict] | None = None
    if hit is None:
        try:
            found = _report_lookup(oid, c)
        except RateBudget:
            return None
        if found is None:
            return None
        row, o, execs = found
    else:
        row, o = hit
    out = dict(row)
    inst = _instruments([str(row["us_market_slug"] or "")], c).get(
        str(row["us_market_slug"] or "").lower())
    ps, fq = _order_scales(o, inst)
    out["commission_usd"] = _commission_usd(o, ps or 0, fq or 0)
    out["commission_spread_px"] = None
    out["executions"] = execs
    return out


def recent_trades(us_market_slug: str, since_ts: float, max_pages: int = 3) -> list[dict]:
    """The account's own fills on one symbol since `since_ts`, newest
    first, in the retail adapter's 18-key row shape.

    THE DOCUMENTED FALLBACK (design A, `recent_trades`): neither the
    executions search nor the trades search has a documented request
    body in inst/ (auth.md names POST /v1/report/trades/search and its
    scope; llms.txt names the pages; no page was fetched), and this
    lane invents no shape. The rows are built from the ORDERS the
    report search returns for the symbol (the documented body:
    `symbols`): every order with cumQty > 0 yields one row per price
    level of its `priceToQuantityFilled` ("Quantity filled at each
    price point over the life of the order"), or one row at avgPx when
    the venue sent no levels; `ts` is the order's lastTransactTime (the
    venue prints no per-fill time on an order), `side` the order's side
    with the retail prefix (ORDER_SIDE_BUY / _SELL), `own_*` the same
    order (every row here is our own order: the report is account-
    scoped), `aggressor` None (unknown on an order row; a post-only
    rest that filled was the maker, but None is what the venue said),
    `realized_pnl` 0.0 (not stated), `self` False.

    Raises: RuntimeError("trade log truncated ...") when max_pages ran
    out with a next page token still present; PmxError on a filled
    order with no lastTransactTime (a fill that cannot be placed against
    since_ts is unreadable, not 'no fills'); RateBudget when the report
    bucket is empty and no page is cached. Unreadable and truncated are
    raises, never an empty list."""
    c = _Claims()
    symbol = str(us_market_slug or "").strip().lower()
    body: dict = {"symbols": [symbol]}
    orders: list[dict] = []
    reached = False
    for _ in range(int(max_pages)):
        resp, fresh = _report_search(body, c)
        if not fresh:
            # E35 review (D3): a page older than REPORT_TTL_S served under
            # an empty budget cannot name the fills since it was read --
            # "no fills" off it is the lost-fill class E22 exists for
            raise RateBudget("pmx: report budget empty; the cached page is stale, never 'no fills'")
        orders.extend(_report_rows(resp))
        tok, eof = _report_page_token(resp)
        if eof or not tok:
            reached = True
            break
        body = {**body, "pageToken": tok}
    if not reached:
        raise RuntimeError(f"trade log truncated after {max_pages} pages "
                           f"before reaching {int(since_ts)}")
    inst = _instruments([symbol], c).get(symbol)
    out: list[dict] = []
    for o in orders:
        if str(_field(o, "symbol") or "").lower() != symbol:
            continue
        cum_raw = _float_of(_field(o, "cumQty", "cum_qty"))
        if not cum_raw or cum_raw <= 0:
            continue
        oid = _field(o, "id")
        ps, fq = _order_scales(o, inst)
        if ps is None or fq is None:
            raise PmxError(f"pmx: recent_trades on {symbol}: filled order {oid} scale_unreadable")
        ts = _epoch(_field(o, "lastTransactTime", "last_transact_time"))
        if ts is None:
            raise PmxError(f"pmx: filled order {oid} carries no lastTransactTime")
        if ts < float(since_ts):
            continue
        _remember(oid, symbol)
        side = str(_field(o, "side") or "")
        venue_side = _VENUE_SIDE_OF.get(side, "")
        intent = _INTENT_OF_SIDE.get(side)
        tif = str(_field(o, "timeInForce", "time_in_force") or "").replace("TIME_IN_FORCE_", "") or None
        order_qty = _raw_div(_field(o, "orderQty", "order_qty"), fq) or None
        order_price = _raw_div(_field(o, "price"), ps) or None
        levels: list[tuple[float, float]] = []
        p2q = _field(o, "priceToQuantityFilled", "price_to_quantity_filled")
        if isinstance(p2q, dict):
            for p_raw, q_raw in p2q.items():
                p, q = _float_of(p_raw), _float_of(q_raw)
                if p is None or q is None or q <= 0:
                    continue
                levels.append((p / ps, q / fq))
        if not levels:
            levels.append((_raw_div(_field(o, "avgPx", "avg_px"), ps), cum_raw / fq))
        for px, qty in levels:
            out.append({
                "qty": qty, "price": px, "side": venue_side, "ts": ts,
                "realized_pnl": 0.0,
                "order_id": (str(oid) if oid else None),
                "order_qty": order_qty, "order_price": order_price, "order_tif": tif,
                "aggressor": None,
                "manual": pmus._manual_flag({"manualOrderIndicator":
                                             _field(o, "manualOrderIndicator", "manual_order_indicator")}),
                "own_order_id": (str(oid) if oid else None),
                "own_side": pmus._bare_word(venue_side, "ORDER_SIDE_"),
                "own_intent": pmus._bare_word(intent, "ORDER_INTENT_"),
                "own_qty": order_qty, "own_price": order_price, "own_tif": tif,
                "self": False,
            })
    out.sort(key=lambda r: -float(r["ts"]))
    return out


def bbo_read(client, us_slug: str) -> dict:
    """The mirror's quote read. THE QUOTES ARE THE RETAIL PRODUCTION
    BOOK'S: `bid` / `ask` / `state` / `error` are `pmus.bbo_read` on the
    retail client, byte for byte, because the maker wire must be priced
    against a real book and preprod's is empty (module docstring). The
    `client` argument (this module's shim) is not the retail client and
    is not used for it. Beside them, preprod's own top of book when the
    12/min GetBBO budget allows: `pmx_bid` / `pmx_ask` (px over
    priceScale, None when the side is empty) and `pmx_state` (the
    venue's INSTRUMENT_STATE_* word; "unread" when the budget is empty
    or the read failed, `pmx_error` naming why; "no_instrument" when
    the symbol is not listed there). Every pmx request here claims its
    own gap: the caller's went to the retail read."""
    out = dict(pmus.bbo_read(pmus._get_client(), us_slug))
    out.update({"pmx_bid": None, "pmx_ask": None, "pmx_state": "unread", "pmx_error": None})
    symbol = str(us_slug or "").strip().lower()
    c = _Claims(first_claimed=False)
    try:
        inst = _instruments([symbol], c).get(symbol)
        if inst is None:
            out["pmx_state"] = "no_instrument"
            return out
        sc = _scale(inst)
        if isinstance(sc, str):
            out["pmx_error"] = sc
            return out
        ps = sc[0]
        if not BUCKETS["book"].take():
            return out
        # the symbol is a slug off the desk's tables: quoted, so it can
        # only ever name a path segment (E35 review, D6)
        d = _request("GET", f"/v1/orderbook/{quote(symbol, safe='')}/bbo", claims=c)
        out["pmx_bid"] = _book_px((d or {}).get("bestBid"), ps)
        out["pmx_ask"] = _book_px((d or {}).get("bestOffer"), ps)
        st = (d or {}).get("state")
        out["pmx_state"] = str(st) if st is not None else None
    except Exception as exc:  # noqa: BLE001 -- the retail quote stands; preprod's read is named
        out["pmx_state"] = "unread"
        out["pmx_error"] = type(exc).__name__
    return out


def _book_px(entry: Any, ps: int) -> float | None:
    if not isinstance(entry, dict):
        return None
    px = _float_of(entry.get("px"))
    if px is None or ps <= 0:
        return None
    v = px / float(ps)
    return v if 0.0 < v < 1.0 else None


# ── the contract's writes ────────────────────────────────────────────

def cancel_order(order_id: str, us_market_slug: str) -> dict:
    """POST /v1/trading/orders/cancel {orderId, symbol} -> {"ok": True},
    or {"ok": False, "error": "<Class>: <text>"}. Never raises; a 429's
    string leads with RateLimitError so `is_rate_limit` reads it."""
    # E35 review (D2): recorded BEFORE the request (a cancel whose
    # response is lost may still have landed), and the shared snapshot
    # dropped after it: every status read of this id inside
    # CANCEL_FRESH_S is the venue's own
    _memo_put(_cancelled_at, str(order_id), time.monotonic())
    try:
        _request("POST", "/v1/trading/orders/cancel",
                 json_body={"orderId": str(order_id), "symbol": str(us_market_slug)},
                 claims=_Claims())
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 -- the desk reports, never 500s
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    finally:
        with _open_lock:
            _open_snap["ts"] = 0.0


def _post_only_result(exc: APIStatusError, prev_order: dict) -> dict:
    """A 4xx on the insert as the retail `_post_only_refusal` literal;
    the latency stopgap's text names `latency_reject` instead (no
    back-off: the docs' own instruction)."""
    text = f"{exc.message} {json.dumps(exc.body, default=str) if exc.body is not None else ''}"
    status = "latency_reject" if LATENCY_STOPGAP_TEXT.lower() in text.lower() else "post_only_rejected"
    return {"ok": False, "order_id": None, "status": status,
            "fill_price": None, "filled_shares": 0.0,
            "raw": {"preview": prev_order, "status_code": int(exc.status_code),
                    "error": str(exc.message or exc)[:500],
                    "error_type": type(exc).__name__, "body": exc.body}}


def _preview_echo(prev: dict, req: dict, ps: int, fq: int, qty: int, price_raw: int) -> str | None:
    """Why the previewed order is not ours, or None when it is: the raw
    price and the raw quantity must equal what was sent, the symbol and
    the side too when echoed, and a priceScale / fractionalQuantityScale
    the venue prints on the previewed order must be the instrument's."""
    p = _int_of(_field(prev, "price"))
    q = _int_of(_field(prev, "orderQty", "order_qty"))
    if p is None or q is None:
        return "unreadable"
    if p != price_raw:
        return f"price echoed {p} for {price_raw}"
    if q != qty * fq:
        return f"orderQty echoed {q} for {qty * fq}"
    sym = _field(prev, "symbol")
    if sym is not None and str(sym).lower() != str(req["symbol"]).lower():
        return f"symbol echoed {sym}"
    side = _field(prev, "side")
    if side is not None and str(side) != req["side"]:
        return f"side echoed {side} for {req['side']}"
    eps = _int_of(_field(prev, "priceScale", "price_scale"))
    if eps is not None and eps != ps:
        return f"priceScale echoed {eps} for {ps}"
    efq = _int_of(_field(prev, "fractionalQuantityScale", "fractional_quantity_scale"))
    if efq is not None and efq != fq:
        return f"fractionalQuantityScale echoed {efq} for {fq}"
    return None


def submit_fok(us_market_slug: str, limit_price: float, quantity: int,
               sell: bool = False,
               tif: str = "TIME_IN_FORCE_FILL_OR_KILL",
               intent: str | None = None,
               post_only: bool = False,
               good_till: str | None = None,
               paced_pair: bool = False) -> dict:
    """Preview, then place ONE GTC post-only limit order on the
    institutional preprod venue, then read its state back once. The
    name and the positional order are the retail adapter's (the
    worker's `_write_slots` reads both); the return is the retail
    literal {ok, order_id, status, fill_price, filled_shares, raw}.

    THE GATES, in order, each a named refusal (ok False, no id):
      `ioc_refused` (tif is not GTC: E31, the mirror is a maker),
      `gtd_refused` (a good_till: GTC always here), `post_only_required`
      (the flag off), `bad_intent`, `bad_quantity` (not a whole
      contract >= 1), `side_unverifiable` (a sell with no intent whose
      venue position cannot be read), `rate_budget` (refdata bucket
      empty and the instrument uncached), `no_instrument`,
      `instrument_closed` (state not INSTRUMENT_STATE_OPEN),
      `scale_unreadable`, `tick_misaligned`, `preview_unreadable`,
      `preview_mismatch`; then the venue's own 4xx on the insert as
      `post_only_rejected` (raw.status_code / error / error_type / body)
      or `latency_reject` on its stopgap text. A 5xx, a timeout, a lost
      connection RAISE (the response is lost, the order may stand: the
      worker's lost-placement search is the only safe reading); a 429
      raises RateLimitError from the preview and is the 4xx refusal on
      the create (status_code 429 on raw), the retail path's shape.

    `paced_pair` is accepted for the contract and is redundant here:
    every request of this call after the first claims its own gap
    (`_Claims`), the preview being the caller's claimed one -- a sell
    previews too on this venue (the scaling echo is the guard), so the
    sell's pair is spaced as the buy's is.

    A 200 {orderId} FROM THE INSERT IS AN ACKNOWLEDGEMENT, NOT A RESTING
    ORDER (the sandbox probe of 14:19-14:30Z: 200 {"orderId":
    "CDHM9PJV16R7"} and then NOTHING -- open orders [], positions [],
    every report search empty; the docs: "A returned order ID with HTTP
    200 does not guarantee the order was successfully processed").
    So after the insert answers an id (`orderId`, or `order.id` in the
    quickstart's shape) the order is READ BACK: one paced read of the
    open orders for the symbol, then, when that does not carry it, the
    report search by `orderIds` (12/min budget; skipped when empty).
    A row found gives the state lowercase prefix-stripped, its cumQty
    as filled_shares, its avgPx as fill_price. NEITHER SURFACE showing
    it is `status: ""` with the id KEPT and `raw.read_back:
    "not_found"`: the empty status is the worker's own non-terminal
    unknown at placement (`_place`: `_rest_terminal({"state": status}
    if status else None)`), so the row stays open with its id, the next
    tick's `_reconcile_open` asks `order_status`, and a venue that still
    has no record answers None there -- the worker's
    `order_state_unknown` freeze, the id on the row. "unknown" is NOT
    returned here: `_rest_terminal` reads that word as terminal and
    would close the row on an order that may yet rest. A read-back that
    raises keeps the id too (raw.open_read_error names it). raw carries
    the previewed order, the insert's request (`request`: the exact
    body sent, also logged at INFO -- the ladder's next probe reads
    it) and response, `executions` [] (no execution list on a REST
    insert), `open_order` (the row read back, or None) and `read_back`
    ("open" | "report" | "not_found" | "unread")."""
    symbol = str(us_market_slug or "").strip().lower()
    if tif != GTC:
        return _refuse("ioc_refused", tif=tif, why="only GTC post-only rests exist on this lane")
    if good_till is not None:
        return _refuse("gtd_refused", good_till=good_till, why="GTC always on this lane")
    if not post_only:
        return _refuse("post_only_required", why="every order on this lane is a post-only rest")
    if intent is not None and intent not in (ORDER_INTENT_BUY_LONG, ORDER_INTENT_BUY_SHORT):
        return _refuse("bad_intent", intent=intent)
    qty = _int_of(quantity)
    if qty is None or qty < 1:
        return _refuse("bad_quantity", quantity=quantity, why="whole contracts >= 1 only")
    c = _Claims()
    facts: dict[str, Any] = {}
    if sell:
        # E35 review (D5): EVERY SELL READS THE VENUE'S POSITION FIRST,
        # fresh. On the retail venue a SELL_LONG past the position is
        # the venue's own rejection (its tokens are long-only); here a
        # SIDE_SELL past the long is a SHORT OPENED, and a cover
        # (SIDE_BUY) past the short is a long opened -- an order the
        # plan never asked for. So an exit whose quantity exceeds the
        # position it exits, or whose sign the venue does not hold, is
        # refused `position_mismatch` with both figures on raw; an
        # unreadable position is `side_unverifiable`
        net = _net_position(symbol, c, fresh=True)
        if net is None:
            return _refuse("side_unverifiable", symbol=symbol,
                           why="the venue position could not be read before a sell")
        if intent == ORDER_INTENT_BUY_SHORT:
            wire_intent = ORDER_INTENT_SELL_SHORT
        elif intent == ORDER_INTENT_BUY_LONG:
            wire_intent = ORDER_INTENT_SELL_LONG
        else:
            # the exit side from the venue's own position sign: a short
            # (negative net) is covered with SIDE_BUY
            wire_intent = ORDER_INTENT_SELL_SHORT if net < 0 else ORDER_INTENT_SELL_LONG
        held = -net if wire_intent == ORDER_INTENT_SELL_SHORT else net
        facts["venue_net"] = net
        if held <= 0 or float(qty) > held + 1e-9:
            return _refuse("position_mismatch", symbol=symbol, intent=wire_intent, quantity=qty,
                           venue_net=net,
                           why="the sell exceeds (or contradicts) the position the venue holds; "
                               "on this venue it would open the opposite side")
    else:
        wire_intent = intent or ORDER_INTENT_BUY_LONG
    side = _SIDE_FOR[wire_intent]
    try:
        inst = _instruments([symbol], c).get(symbol)
    except RateBudget as exc:
        return _refuse("rate_budget", symbol=symbol, why=str(exc)[:200])
    if inst is None:
        return _refuse("no_instrument", symbol=symbol)
    state = str(inst.get("state") or "")
    if state != INSTRUMENT_STATE_OPEN:
        return _refuse("instrument_closed", symbol=symbol, state=state)
    sc = Scaling.of(inst)
    if isinstance(sc, str):
        return _refuse(sc, symbol=symbol, priceScale=inst.get("priceScale"),
                       tickSize=inst.get("tickSize"), fractionalQtyScale=inst.get("fractionalQtyScale"))
    ps, tick_raw, fq = sc.as_tuple()
    price_raw = sc.price_raw(limit_price)
    if price_raw is None:
        return _refuse("tick_misaligned", symbol=symbol, price=limit_price, price_scale=ps,
                       tick_raw=tick_raw, why="the price is not on the instrument's tick; "
                                              "never rounded on our behalf")
    req = {"type": "ORDER_TYPE_LIMIT", "side": side, "orderQty": str(sc.qty_raw(qty)),
           "symbol": symbol, "price": str(price_raw), "timeInForce": GTC,
           "clordId": str(uuid.uuid4()), "account": _env("PMX_ACCOUNT"),
           "participateDontInitiate": True,
           "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATED",
           "selfMatchPreventionInstruction": "SELF_MATCH_PREVENTION_INSTRUCTION_REJECT_AGGRESSOR"}
    facts.update({"intent": wire_intent, "side": side, "price_raw": price_raw,
                  "order_qty_raw": qty * fq, "price_scale": ps, "fq_scale": fq})
    # E35 review (D1): the intent this clordId carries, remembered
    # BEFORE anything goes out, so a listing's row for this order --
    # after a lost response included -- names the intent the desk sent
    _memo_put(_intent_by_clord, req["clordId"], wire_intent)
    preview = _request("POST", "/v1/trading/orders/preview", json_body={"request": req}, claims=c)
    prev_order = _order_of(preview, ("previewOrder", "preview_order", "order"))
    if not prev_order:
        return _refuse("preview_unreadable", preview=preview, **facts,
                       why="venue preview echoed no order; refusing rather than assuming it agrees")
    why = _preview_echo(prev_order, req, ps, fq, qty, price_raw)
    if why == "unreadable":
        return _refuse("preview_unreadable", preview=preview, **facts,
                       why="venue preview stated no price or quantity")
    if why is not None:
        return _refuse("preview_mismatch", preview=preview, **facts,
                       venue_price_raw=_field(prev_order, "price"),
                       venue_order_qty_raw=_field(prev_order, "orderQty", "order_qty"),
                       why=f"venue preview did not echo our order ({why})")
    # the exact body, logged: the verification ladder's next sandbox
    # probe is run against what this line prints (nothing secret is in
    # it: the account resource name, a uuid, the symbol, integers)
    log.info("pmx: insert %s", json.dumps(req, sort_keys=True))
    try:
        resp = _request("POST", "/v1/trading/orders", json_body=req, claims=c)
    except APIStatusError as exc:
        if 400 <= exc.status_code < 500:
            out = _post_only_result(exc, prev_order)
            out["raw"].update(facts)
            out["raw"]["request"] = req
            return out
        raise
    order_id = _field(resp, "orderId", "order_id") if isinstance(resp, dict) else None
    if not order_id:
        order_id = _field(_order_of(resp, ("order",)), "id")
    if not order_id:
        raise PmxError("pmx: the insert answered without an order id (a lost response, not a refusal)")
    order_id = str(order_id)
    _remember(order_id, symbol)
    row, read_back, read_error, execs = _read_back(order_id, symbol, c)
    filled = float(row["filled_shares"]) if row else 0.0
    # E35 review (D7): the execution records the report page carried for
    # this order ride `raw.executions` (their `aggressor` bool is what
    # the worker's _aggressor_maker reads on a fill at create); [] when
    # the venue sent none -- the worker reads [] as "unread", the block
    return {"ok": filled > 0, "order_id": order_id,
            "status": (str(row["state"]) if row else ""),
            "fill_price": (row["avg_px"] if row and filled > 0 else None),
            "filled_shares": filled,
            "raw": {"preview": prev_order, "request": req, "response": resp,
                    "executions": list(execs or []),
                    "open_order": row, "read_back": read_back, "open_read_error": read_error,
                    **facts}}


def _read_back(order_id: str, symbol: str,
               claims: _Claims) -> tuple[dict | None, str, str | None, list[dict] | None]:
    """The order after its insert: (row | None, where it was found --
    "open" | "report" | "not_found" | "unread" -- the read's error when
    one raised, and the report page's execution records for it). Open
    orders for the symbol first; the report by orderIds when the
    listing does not carry it and the 12/min budget allows; a raise on
    either surface keeps the id and names itself."""
    try:
        for r, _o in _open_read([symbol], claims):
            if str(r["order_id"]) == order_id:
                return r, "open", None, None
    except Exception as exc:  # noqa: BLE001 -- the id is kept; the state is unread by name
        return None, "unread", f"{type(exc).__name__}: {str(exc)[:160]}", None
    try:
        found = _report_lookup(order_id, claims)
    except RateBudget as exc:
        return None, "unread", f"{type(exc).__name__}: {str(exc)[:160]}", None
    except Exception as exc:  # noqa: BLE001
        return None, "unread", f"{type(exc).__name__}: {str(exc)[:160]}", None
    if found is None:
        return None, "not_found", None, None
    return found[0], "report", None, found[2]


def close_position(us_slug: str, *, slippage_bips: int) -> dict:
    """The retail venue's one-call flatten has no counterpart here."""
    return {"ok": False, "status": "not_supported", "slug": us_slug,
            "why": "no close-position endpoint on the institutional venue; the mirror never calls it"}


def balance() -> dict:
    """POST /v1/positions/balance {account, currency "USD"} -> the
    venue's own figures verbatim (balance, excessCapital, buyingPower,
    openOrders, updateTime -- the sandbox's dummy 1000000; the body
    needs BOTH the account resource name and the currency, 14:2xZ).
    A read for the probe and the ladder; the mirror never sizes on it.
    Raises on failure."""
    return _request("POST", "/v1/positions/balance",
                    json_body={"account": _env("PMX_ACCOUNT"), "currency": "USD"},
                    claims=_Claims(first_claimed=False))


def probe() -> dict:
    """Connectivity / auth probe: /v1/health (no auth), /v1/whoami and
    the account balance. Never orders; every failure is a named string."""
    out: dict[str, Any] = {"venue": VENUE, "base_url": PMX_BASE_URL,
                           "creds_configured": all(os.environ.get(n) for n in _CRED_NAMES)}
    try:
        out["health"] = _request("GET", "/v1/health", auth=False, claims=_Claims(first_claimed=False))
        out["health_ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["health_ok"] = False
        out["health_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    if out["creds_configured"]:
        try:
            who = _request("GET", "/v1/whoami", claims=_Claims(first_claimed=False))
            out["auth_ok"] = True
            out["whoami"] = {k: (who or {}).get(k) for k in ("user", "firm", "firmType")}
        except Exception as exc:  # noqa: BLE001
            out["auth_ok"] = False
            out["auth_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        try:
            bal = balance()
            out["balance"] = {k: (bal or {}).get(k) for k in ("balance", "excessCapital",
                                                             "buyingPower", "openOrders")}
        except Exception as exc:  # noqa: BLE001
            out["balance_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out


# ── the retail module's own objects, re-exported (identity-compared) ──
resolve_market_exact = pmus.resolve_market_exact
resolve_derivative_exact = pmus.resolve_derivative_exact
resolve_team_yesno_exact = pmus.resolve_team_yesno_exact
order_intent_for = pmus.order_intent_for
_yn_name_match = pmus._yn_name_match
OPEN_ORDER_STATES = pmus.OPEN_ORDER_STATES

__all__ = ["PMX_AUTH0_DOMAIN", "PMX_BASE_URL", "VENUE", "BUCKETS", "Scaling", "submit_fok",
           "cancel_order", "open_orders", "order_status", "recent_trades", "bbo_read",
           "_get_client", "resolve_market_exact", "resolve_derivative_exact",
           "resolve_team_yesno_exact", "order_intent_for", "_yn_name_match", "close_position",
           "balance", "probe", "OPEN_ORDER_STATES", "PmxError", "APIStatusError",
           "RateLimitError", "RateBudget"]
