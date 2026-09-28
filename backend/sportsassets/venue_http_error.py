"""A BOUNDED, SANITIZED DIAGNOSTIC FROM A VENUE HTTP EXCEPTION.

── WHY THIS EXISTS ──────────────────────────────────────────────────
`pmus.book_read()` ended its failure path with

    out["error"] = type(exc).__name__

which is why a reproduced triple-429 reached the operator as
`RateLimitError` with `status`, `exception` and `detail` all null. The
SDK's `APIStatusError` carries the whole `httpx.Response` -- status,
headers, `Retry-After`, the request URL and a body -- and every one of
those was discarded before `_venue_diagnostic()` could look.

The consequence was not only a thin log line. Without the status nobody
could tell a 429 from a 500, and without `Retry-After` the cooldown had
no duration to honour, so the lane re-read on its ordinary gap and
collected another 429.

── WHAT IS DELIBERATELY NOT INCLUDED ────────────────────────────────
Credentials and signed URLs. A venue exception string can contain a
presigned query, an `Authorization` header or a session token, and a
diagnostic that cannot be shown to anyone is worth nothing. So:

  * headers are allow-listed, never copied wholesale;
  * the URL keeps scheme, host and path and DROPS the query entirely;
  * every free-text field runs through the same redaction the loop
    already applies to venue messages;
  * everything is length-bounded, because a diagnostic is metadata and
    not a payload.
"""

import re

#: Response headers worth keeping. `Retry-After` is the operational one --
#: it is the venue telling us how long to wait, and honouring it is the
#: difference between a cooldown and a guess. The rest identify the
#: response for a support conversation.
KEEP_HEADERS = (
    "retry-after",
    "x-request-id", "x-amzn-requestid", "x-amz-request-id",
    "cf-ray", "x-ratelimit-limit", "x-ratelimit-remaining",
    "x-ratelimit-reset", "date", "content-type",
)

#: Anything token-shaped, matching the loop's own rule so one redaction
#: standard applies to venue text wherever it is captured.
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{20,}")
_SECRETISH = re.compile(r"(?i)(key|secret|token|signature|sig|auth|bearer)"
                        r"\s*[=:]\s*\S+")

MAX_TEXT = 300


def sanitize(text, limit: int = MAX_TEXT) -> str | None:
    """Redact token-shaped and secret-labelled spans, then bound."""
    if text is None:
        return None
    s = str(text)
    s = _SECRETISH.sub(lambda m: "%s=<redacted>" % m.group(1), s)
    s = _TOKENISH.sub("<redacted>", s)
    return s[:limit]


def _retry_after_seconds(value):
    """`Retry-After` as seconds, or None.

    RFC 9110 allows EITHER a delta-seconds integer OR an HTTP-date. Only
    handling the integer form is how a date-valued header becomes "no
    Retry-After" and the cooldown silently falls back to a guess.
    """
    if value is None:
        return None
    raw = str(value).strip()
    try:
        return max(0.0, float(int(raw)))
    except (TypeError, ValueError):
        pass
    try:
        import email.utils
        import time as _t
        when = email.utils.parsedate_to_datetime(raw)
        if when is None:
            return None
        return max(0.0, when.timestamp() - _t.time())
    except Exception:                                          # noqa: BLE001
        return None


def _safe_url(request) -> dict:
    """Scheme, host and path. THE QUERY IS DROPPED, not redacted.

    A presigned URL carries its credential in the query, and redacting
    span by span invites a miss. Dropping it entirely loses nothing a
    diagnostic needs -- the endpoint is the path.
    """
    out = {"url_host": None, "url_path": None, "method": None,
           "query_dropped": True}
    if request is None:
        return out
    try:
        out["method"] = str(getattr(request, "method", "") or "") or None
        u = getattr(request, "url", None)
        if u is not None:
            out["url_host"] = str(getattr(u, "host", "") or "") or None
            out["url_path"] = str(getattr(u, "path", "") or "") or None
    except Exception:                                          # noqa: BLE001
        pass
    return out


def describe(exc, *, endpoint: str = None, attempts: int = None,
             elapsed_s: float = None) -> dict:
    """Everything a reader needs about a failed venue call, and no secrets.

    NEVER RAISES. A diagnostic that can break the path it describes is
    worse than none, so every field is read defensively and an
    unreadable one is reported as unreadable.
    """
    out = {
        "error_type": type(exc).__name__,
        "endpoint": endpoint,
        "attempts": attempts,
        "elapsed_s": (None if elapsed_s is None else round(float(elapsed_s), 3)),
        "http_status": None,
        "retry_after_s": None,
        "retry_after_raw": None,
        "request_id": None,
        "headers": {},
        "message": None,
        "is_rate_limited": False,
        "diagnostic_is_sanitized": True,
        "query_string_dropped": True,
    }
    try:
        out["message"] = sanitize(getattr(exc, "message", None) or str(exc))
    except Exception:                                          # noqa: BLE001
        out["message"] = "<unreadable>"

    resp = getattr(exc, "response", None)
    req = getattr(exc, "request", None) or getattr(resp, "request", None)
    out.update(_safe_url(req))

    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(resp, "status_code", None)
    try:
        out["http_status"] = None if status is None else int(status)
    except (TypeError, ValueError):
        out["http_status"] = None

    try:
        hdrs = getattr(resp, "headers", None)
        if hdrs is not None:
            for name in KEEP_HEADERS:
                try:
                    v = hdrs.get(name)
                except Exception:                              # noqa: BLE001
                    v = None
                if v is not None:
                    out["headers"][name] = sanitize(v, 120)
            out["retry_after_raw"] = out["headers"].get("retry-after")
            out["retry_after_s"] = _retry_after_seconds(out["retry_after_raw"])
            out["request_id"] = (out["headers"].get("x-request-id")
                                 or out["headers"].get("x-amzn-requestid")
                                 or out["headers"].get("cf-ray"))
    except Exception:                                          # noqa: BLE001
        pass

    # RATE LIMITING IS RECOGNISED BY STATUS *OR* CLASS, not one alone. The
    # status is the authority; the class name is the fallback for a
    # transport that raised before a response existed. Requiring both
    # would miss the cases the cooldown most needs to catch.
    out["is_rate_limited"] = bool(
        out["http_status"] == 429
        or "ratelimit" in out["error_type"].lower().replace("_", ""))
    return out


__all__ = ["describe", "sanitize", "KEEP_HEADERS", "MAX_TEXT"]
