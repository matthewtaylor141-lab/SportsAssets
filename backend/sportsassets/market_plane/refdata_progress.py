"""Reference-data recovery policy. Pure; no network, order or capital access.

A transport failure is not evidence that an instrument is unlisted. Pending
retry IDs are excluded BEFORE the database LIMIT so they cannot starve the
remaining universe. Existing stream and per-pass request limits are unchanged.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

VERSION = "REFDATA_PROGRESS_V1"


def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def cooling_ids(attempted: Mapping[str, float], *, now: float,
                retry_s: float) -> list[str]:
    """Exclude cooling (and future-clock) attempts before selecting a page."""
    return sorted(s for s, at in attempted.items()
                  if finite_number(at) is not None
                  and now - float(at) < retry_s)


def classify_bootstrap(symbol: str, result: Mapping | None) -> dict:
    """Validate a parsed bootstrap response without creating mapping authority.

    Only an HTTP 200, structured empty instruments response can be classified
    UNLISTED. A 404 can also be a bad path/entitlement, so remains a retryable
    failure. Valid refdata still passes existing exact-identity/same-book gates.
    """
    r = dict(result or {})
    status = r.get("status")
    if r.get("transportError") or status != 200:
        return {"state": "RETRY", "why": "REFDATA_HTTP_OR_TRANSPORT_FAILURE",
                "http_status": status, "record": None}
    rec = r.get("record")
    if rec is None:
        if r.get("authoritative_empty") is True:
            return {"state": "UNLISTED", "why": "EXACT_QUERY_RETURNED_EMPTY",
                    "record": None}
        return {"state": "RETRY", "why": "REFDATA_RESPONSE_NOT_ESTABLISHED",
                "record": None}
    if not isinstance(rec, Mapping) or rec.get("symbol") != symbol:
        return {"state": "RETRY", "why": "REFDATA_SYMBOL_MISMATCH",
                "record": None}
    ps = finite_number(r.get("priceScale", rec.get("priceScale")))
    qs = finite_number(r.get("qtyScale", rec.get("fractionalQtyScale")))
    if ps is None or qs is None or ps <= 0 or qs <= 0:
        return {"state": "RETRY", "why": "REFDATA_SCALES_UNPROVEN",
                "record": None}
    return {"state": "VALID", "why": None, "record": dict(rec)}


def instrument_response(symbol: str, row: Mapping | None) -> dict:
    """Select the exact symbol, not blindly instruments[0]."""
    r = dict(row or {})
    body = r.get("body")
    rows = body.get("instruments") if isinstance(body, Mapping) else None
    exact = [dict(x) for x in rows if isinstance(x, Mapping)
             and x.get("symbol") == symbol] if isinstance(rows, list) else []
    # Multiple conflicting records for the same symbol are not certifiable.
    unique = exact and all(x == exact[0] for x in exact)
    return {"symbol": symbol, "status": r.get("status"),
            "record": exact[0] if unique else None,
            "authoritative_empty": (r.get("status") == 200
                                    and isinstance(rows, list) and not rows
                                    and not r.get("transportError")),
            "transportError": r.get("transportError"), "ms": r.get("ms")}


def capacity_view(*, active: int, pending: int, subscribable: int,
                  subscribed: int, max_per_stream: int, max_streams: int) -> dict:
    """Capacity and ingestion backlog are different; neither is hidden."""
    vals = (active, pending, subscribable, subscribed, max_per_stream, max_streams)
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in vals):
        raise ValueError("capacity counts must be nonnegative integers")
    if max_per_stream == 0 or max_streams == 0:
        raise ValueError("stream limits must be positive")
    capacity = max_per_stream * max_streams
    return {
        "configured_capacity": capacity, "active_required": active,
        "refdata_pending": pending, "known_subscribable": subscribable,
        "subscribed": subscribed,
        "unused_configured_slots": max(0, capacity - subscribed),
        "known_capacity_overflow": max(0, subscribable - capacity),
        "unresolved_coverage_backlog": pending,
        "streams_for_known_subscribable": math.ceil(subscribable / max_per_stream),
        "streams_if_all_active_are_pmx_listed": math.ceil(active / max_per_stream),
        "full_capacity_requirement_proven": pending == 0 and subscribable == active,
        "streams_required_for_full_coverage": (
            math.ceil(active / max_per_stream)
            if pending == 0 and subscribable == active else None),
        "full_coverage_requirement_why": (
            None if pending == 0 and subscribable == active
            else "REFERENCE_DATA_OR_FULL_UNIVERSE_STREAM_AVAILABILITY_NOT_PROVEN"),
        "capacity_is_not_an_explanation_for":
            "unresolved reference data or unused configured slots",
    }


def _pct(xs: list, q: float):
    if not xs:
        return None
    ys = sorted(xs)
    return round(ys[min(len(ys) - 1, int(q * (len(ys) - 1) + 0.5))], 1)


def refdata_metrics(totals: dict, boot: Mapping, *, lat_ms: list, n429: int,
                    budget: int, now: float) -> dict:
    """THE REFDATA THROUGHPUT, MEASURED (owner closeout item 4). `totals` is
    the worker's running record since its boot (mutated); returns this
    pass's latency and the running rates: requests/min, success rate, 429
    rate, median / p95 latency, listed / unlisted / retryable counts and the
    burn-down per hour. Transient failures are RETRY, never UNLISTED."""
    t = totals
    t.setdefault("since", float(now))
    for k in ("requests", "listed", "unlisted", "retryable", "http_429"):
        t.setdefault(k, 0)
    t["requests"] += int(boot.get("attempted") or 0)
    t["listed"] += int(boot.get("stored") or 0)
    t["unlisted"] += int(boot.get("unlisted") or 0)
    t["retryable"] += int(boot.get("failed") or 0)
    t["http_429"] += int(n429)
    ring = t.setdefault("lat_ms", [])
    ring.extend(round(x, 1) for x in lat_ms)
    del ring[:-2000]
    mins = max((float(now) - t["since"]) / 60.0, 1e-9)
    req = t["requests"]
    return {"budget": budget,
            "latency_ms": {"p50": _pct(lat_ms, 0.5), "p95": _pct(lat_ms, 0.95)},
            "totals_since_boot": {
                "since": t["since"], "requests": req,
                "listed": t["listed"], "unlisted": t["unlisted"],
                "retryable": t["retryable"], "http_429": t["http_429"],
                "requests_per_min": round(req / mins, 2),
                "success_rate": (round((t["listed"] + t["unlisted"]) / req, 4)
                                 if req else None),
                "rate_429": round(t["http_429"] / req, 4) if req else None,
                "latency_ms_p50": _pct(ring, 0.5),
                "latency_ms_p95": _pct(ring, 0.95),
                "burn_down_per_hour": round(
                    (t["listed"] + t["unlisted"]) / (mins / 60.0), 1)}}
