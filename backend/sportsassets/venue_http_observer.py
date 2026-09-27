"""THE RESPONSE METADATA THE SDK THROWS AWAY, RECORDED.

WHY THIS FILE EXISTS. `polymarket_us.PolymarketUS._request` ends with
`return response.json()`. Everything the HTTP layer said about the payload --
when the origin generated it, whether a cache served it, how long it sat there,
whether it can be revalidated -- is discarded one line before we see it. That
missing information is the whole of the open question about book freshness:

    a book read that arrives in 20 ms may be a snapshot the edge cached
    four minutes ago, and nothing in the parsed body distinguishes the two.

So the loop had exactly two clocks to reason with -- our own receipt instant and
`marketData.transactTime` -- and neither of them can tell a quiet book from a
cached one. That is not a policy question. It is missing evidence, and this is
the file that collects it.

WHAT IT DOES. Installs one `httpx` response event hook on the SDK's own client
and records, per request path, the response's status and a FIXED WHITELIST of
metadata headers. It is a recorder:

  * it never changes a request, a response, a status or a body;
  * it never touches request headers, so no credential can pass through it;
  * it reads only `response.headers`, never the body, so it cannot consume the
    stream the SDK is about to parse;
  * an exception inside the hook is swallowed, because a bookkeeping failure
    must not take down a market-data read.

WHAT IT IS NOT. It is not a freshness verdict. Deciding what these headers
establish is `bettor_venue_currency`'s job, deliberately separated: this file
answers "what did the server say", that one answers "what does that prove", and
mixing them is how an assumption gets recorded as an observation.
"""

from __future__ import annotations

import threading
import time

#: The headers that bear on WHEN THIS PAYLOAD WAS GENERATED and whether it can
#: be revalidated. Every one is a standard response header with published
#: semantics (RFC 9110 / RFC 9111) or a widely documented cache-status header.
#: The list is closed on purpose: a whitelist cannot accidentally record an
#: authorization echo, a set-cookie, or a body.
OBSERVED_HEADERS = (
    # RFC 9111 §6.1 -- the origin's generation instant for THIS response.
    "date",
    # RFC 9111 §5.1 -- seconds this response has been in a cache. `Date` minus
    # `Age` is the origin generation instant of the stored representation.
    "age",
    # RFC 9111 §5.2 -- max-age / no-store / no-cache / must-revalidate.
    "cache-control",
    "expires",
    "pragma",
    # RFC 9110 §8.8 -- validators. The supported revalidation mechanism.
    "etag",
    "last-modified",
    "vary",
    # Cache-status headers. Not standardised as a set, but each is documented
    # by the intermediary that emits it and each states HIT or MISS plainly.
    "x-cache",
    "cf-cache-status",
    "x-cache-hits",
    "x-served-by",
    "age-source",
    "via",
    # Identity of the responder, which is what tells us an intermediary is in
    # the path at all.
    "server",
)

#: How many observations to keep per path. One is what a read needs; a handful
#: is what a revalidation probe needs.
KEEP_PER_PATH = 4

_LOCK = threading.Lock()
_SEEN: dict[str, list] = {}
_INSTALLED: set[int] = set()
#: Every path we have ever observed, whether or not its observations were taken.
#: A probe needs to know that a header set was NEVER seen, which is different
#: from seen-and-consumed.
_EVER: set[str] = set()


def _record(response) -> None:
    """The hook body. Never raises into httpx."""
    try:
        req = getattr(response, "request", None)
        url = getattr(req, "url", None)
        path = str(getattr(url, "path", "") or "")
        if not path:
            return
        hdrs = getattr(response, "headers", None)
        got = {}
        for name in OBSERVED_HEADERS:
            try:
                val = hdrs.get(name)
            except Exception:                                  # noqa: BLE001
                val = None
            if val is not None:
                # Bounded: a header is metadata, not a payload.
                got[name] = str(val)[:200]
        row = {
            "path": path,
            "method": str(getattr(req, "method", "") or ""),
            "status": getattr(response, "status_code", None),
            "headers": got,
            "headers_observed": list(OBSERVED_HEADERS),
            "headers_absent": [n for n in OBSERVED_HEADERS if n not in got],
            # OUR instants, labelled as ours. `received_at` is what a currency
            # verdict re-ages the origin instant against.
            "received_at": time.time(),
            "received_monotonic": time.monotonic(),
        }
        with _LOCK:
            _EVER.add(path)
            rows = _SEEN.setdefault(path, [])
            rows.append(row)
            del rows[:-KEEP_PER_PATH]
    except Exception:                                          # noqa: BLE001
        # A recorder that can break a read is worse than no recorder.
        return


def install(client) -> dict:
    """Install the hook on an SDK client's underlying httpx client.

    Idempotent per httpx client object: called on every book read without
    stacking hooks. Returns a small verdict so a caller can record that the
    observation channel exists (or say why it does not) rather than silently
    reporting "no headers" when the real answer is "never wired up".
    """
    http = getattr(client, "_http", None)
    if http is None:
        return {"installed": False,
                "why": "the SDK client exposes no underlying httpx client, so "
                       "response metadata cannot be observed without "
                       "reimplementing its request path"}
    key = id(http)
    with _LOCK:
        if key in _INSTALLED:
            return {"installed": True, "already": True}
    try:
        hooks = http.event_hooks
        existing = list(hooks.get("response") or [])
        if _record not in existing:
            existing.append(_record)
        hooks["response"] = existing
        http.event_hooks = hooks
    except Exception as exc:                                   # noqa: BLE001
        return {"installed": False, "why": "event hook rejected: %s"
                                          % type(exc).__name__}
    with _LOCK:
        _INSTALLED.add(key)
    return {"installed": True, "already": False}


def book_path(slug: str) -> str:
    """The documented book path for a slug, which is the key to look under."""
    return "/v1/markets/%s/book" % slug


def take(path: str):
    """The most recent observation for a path, REMOVED.

    Removed rather than read, because a stale header set silently reused on a
    later read is exactly the failure this file exists to detect. A caller that
    gets None learns that this particular request produced no observation.
    """
    with _LOCK:
        rows = _SEEN.get(path) or []
        return rows.pop() if rows else None


def peek(path: str):
    """The most recent observation for a path, left in place. For diagnostics."""
    with _LOCK:
        rows = _SEEN.get(path) or []
        return dict(rows[-1]) if rows else None


def ever_seen(path: str) -> bool:
    """Whether ANY response on this path was ever observed."""
    with _LOCK:
        return path in _EVER


def reset() -> None:
    """Forget every observation. Tests only."""
    with _LOCK:
        _SEEN.clear()
        _EVER.clear()


def describe() -> dict:
    return {
        "what_this_is": ("a recorder for the response metadata the venue SDK "
                         "discards, not a freshness verdict"),
        "headers_observed": list(OBSERVED_HEADERS),
        "never_recorded": ["any request header", "any response body",
                           "set-cookie", "authorization"],
        "verdict_lives_in": "bettor_venue_currency.evaluate",
        "keep_per_path": KEEP_PER_PATH,
    }
