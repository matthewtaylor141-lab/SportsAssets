"""THE KALSHI SPORTS CATALOGUE -- credential-free, GET-ONLY, cursor-complete.

MARKET DATA / RESEARCH ONLY. No order, cancel, funding, sizing, capital or
promotion authority. This module imports neither `kalshi_venue` (the client
that holds submit / cancel) nor `kalshi_orders`, sends no credential and no
header of its own, and its transport REFUSES every method but GET before any
byte leaves the process.

WHY IT EXISTS (integration, settlement rule registry). The package asked for
`kalshi_public_rules.read` to be wired "into the existing candidate build
before kalshi_mapping.establish". There is no such build in production:
bettor_capital_eligibility.KALSHI_READ_PATH_MISSING names it -- no Kalshi
catalogue supplies the structured Kalshi records. This is the catalogue half
of that gap: it enumerates Kalshi's open SPORTS markets with each market's
own `rules_primary` / `rules_secondary` (parsed by
settlement_rule_registry.kalshi_rule_evidence) and hands them to the
Universal Market Plane registry (venue KALSHI, contract_id 'kalshi:'+ticker).
It maps nothing to a BETTOR contract and decides nothing: a Kalshi row stays a
named CODE_CONTROLLED_GAP in coverage until a structured mapping exists.

THE DOCUMENTED PUBLIC ENDPOINTS (docs.kalshi.com, API reference):
    GET {BASE}/series?category=Sports           the sports series list
    GET {BASE}/markets?series_ticker=<T>&status=open&limit=1000[&cursor=<c>]
BASE is the repo's documented Kalshi base (kalshi_public_rules.BASE ==
kalshi_venue.BASE_URLS["prod"], https://api.elections.kalshi.com/trade-api/v2;
the package's external-api.kalshi.com host is not used).

COMPLETENESS IS PROVEN, NEVER ASSUMED. A walk is COMPLETE only when the series
list and EVERY series' markets were paged until the venue returned no cursor.
A walk stopped by the request budget, a page cap, a failed request or a
repeated cursor is TRUNCATED, by name, with the series it did not finish --
never a quiet success. Requests are paced (>= MIN_GAP_S apart) and bounded
(max_requests per walk).
"""
from __future__ import annotations

import time

from . import kalshi_public_rules as KPR

VERSION = "KALSHI_SPORTS_CATALOGUE_V1"
BASE = KPR.BASE
SPORTS_CATEGORY = "Sports"
PAGE_LIMIT = 1000
MIN_GAP_S = 0.1
DEFAULT_PACING_S = 0.15
DEFAULT_MAX_REQUESTS = 800
MAX_PAGES_PER_SERIES = 40
TIMEOUT_S = 10.0

R_NON_GET_REFUSED = "KALSHI_CATALOGUE_NON_GET_METHOD_REFUSED"
R_TRUNCATED_BUDGET = "KALSHI_CATALOGUE_TRUNCATED_REQUEST_BUDGET"
R_TRUNCATED_PAGE_CAP = "KALSHI_CATALOGUE_TRUNCATED_PAGE_CAP"
R_TRUNCATED_HTTP = "KALSHI_CATALOGUE_TRUNCATED_REQUEST_FAILED"
R_TRUNCATED_CURSOR_REPEAT = "KALSHI_CATALOGUE_TRUNCATED_CURSOR_REPEATED"
R_SERIES_UNREADABLE = "KALSHI_CATALOGUE_SERIES_LIST_NOT_READ"


class NonGetRefused(RuntimeError):
    pass


class GetOnlyTransport:
    """The real transport. `request` refuses every method but GET before
    touching the network; `get` is the only path that sends."""

    def __init__(self, session=None):
        self._s = session

    def request(self, method, url, *, params=None, timeout=TIMEOUT_S):
        if str(method or "").upper() != "GET":
            raise NonGetRefused("%s: %s" % (R_NON_GET_REFUSED, method))
        if self._s is None:
            import requests
            self._s = requests.Session()
        return self._s.get(url, params=params, timeout=float(timeout))

    def get(self, url, *, params=None, timeout=TIMEOUT_S):
        return self.request("GET", url, params=params, timeout=timeout)


class _Pacer:
    def __init__(self, gap_s, sleep, clock):
        self.gap = max(float(gap_s), MIN_GAP_S)
        self.sleep, self.clock = sleep, clock
        self.last = None

    def wait(self):
        if self.last is not None:
            d = self.gap - (self.clock() - self.last)
            if d > 0:
                self.sleep(d)
        self.last = self.clock()


def _get_json(tx, pacer, url, params, timeout_s):
    pacer.wait()
    try:
        r = tx.get(url, params=params, timeout=float(timeout_s))
    except NonGetRefused:
        raise
    except Exception as exc:                                    # noqa: BLE001
        return None, type(exc).__name__
    status = getattr(r, "status_code", None)
    if status != 200:
        return None, "HTTP_%s" % status
    try:
        body = r.json()
    except Exception:                                           # noqa: BLE001
        return None, "JSON_UNREADABLE"
    if not isinstance(body, dict):
        return None, "BODY_NOT_AN_OBJECT"
    return body, None


def walk(transport=None, *, max_requests: int = DEFAULT_MAX_REQUESTS,
         pacing_s: float = DEFAULT_PACING_S, timeout_s: float = TIMEOUT_S,
         max_pages_per_series: int = MAX_PAGES_PER_SERIES,
         sleep=time.sleep, clock=time.monotonic) -> dict:
    """One full catalogue walk. Never raises (except a NonGetRefused, which
    no code path here can produce). Returns {complete, stopped, requests,
    series_total, series_complete, series_truncated, series_unread,
    markets, ...}; every market carries `_series` (ticker, title, tags,
    category) as the venue listed it."""
    tx = transport or GetOnlyTransport()
    pacer = _Pacer(pacing_s, sleep, clock)
    out = {"version": VERSION, "base": BASE, "method": "GET",
           "credentials": "NONE", "complete": False, "stopped": None,
           "requests": 0, "series_total": 0, "series_complete": 0,
           "series_truncated": {}, "series_unread": [], "markets": [],
           "series_list_complete": False,
           "authority": "MARKET_DATA_ONLY_NO_ORDER_AUTHORITY"}
    budget = max(1, int(max_requests))

    def spend():
        if out["requests"] >= budget:
            return False
        out["requests"] += 1
        return True

    # 1. the sports series list (cursor-paged if the venue pages it)
    series, cursor, seen = [], None, set()
    while True:
        if not spend():
            out["stopped"] = R_TRUNCATED_BUDGET
            return out
        params = {"category": SPORTS_CATEGORY}
        if cursor:
            params["cursor"] = cursor
        body, err = _get_json(tx, pacer, BASE + "/series", params, timeout_s)
        if err:
            out["stopped"] = R_SERIES_UNREADABLE
            out["series_error"] = err
            return out
        series.extend(s for s in (body.get("series") or [])
                      if isinstance(s, dict) and s.get("ticker"))
        nxt = body.get("cursor") or None
        if not nxt:
            break
        if nxt in seen:
            out["stopped"] = R_TRUNCATED_CURSOR_REPEAT
            return out
        seen.add(nxt)
        cursor = nxt
    out["series_list_complete"] = True
    by_ticker = {}
    for s in series:
        by_ticker.setdefault(str(s["ticker"]), s)
    tickers = sorted(by_ticker)
    out["series_total"] = len(tickers)

    # 2. every series' OPEN markets, cursor to exhaustion
    for i, t in enumerate(tickers):
        s = by_ticker[t]
        meta = {"ticker": t, "title": s.get("title"),
                "tags": list(s.get("tags") or []),
                "category": s.get("category")}
        cursor, seen, pages, why = None, set(), 0, None
        got = []
        while True:
            if pages >= int(max_pages_per_series):
                why = R_TRUNCATED_PAGE_CAP
                break
            if not spend():
                why = R_TRUNCATED_BUDGET
                break
            params = {"series_ticker": t, "status": "open",
                      "limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            body, err = _get_json(tx, pacer, BASE + "/markets", params,
                                  timeout_s)
            pages += 1
            if err:
                why = "%s:%s" % (R_TRUNCATED_HTTP, err)
                break
            for m in body.get("markets") or []:
                if isinstance(m, dict) and m.get("ticker"):
                    got.append(dict(m, _series=meta))
            nxt = body.get("cursor") or None
            if not nxt:
                break
            if nxt in seen:
                why = R_TRUNCATED_CURSOR_REPEAT
                break
            seen.add(nxt)
            cursor = nxt
        out["markets"].extend(got)       # a partial series' markets are kept
        if why is None:
            out["series_complete"] += 1
            continue
        out["series_truncated"][t] = why
        if why == R_TRUNCATED_BUDGET:
            out["series_unread"] = tickers[i + 1:]
            out["stopped"] = R_TRUNCATED_BUDGET
            break
    if out["series_truncated"] and out["stopped"] is None:
        out["stopped"] = sorted(set(
            v.split(":")[0] for v in out["series_truncated"].values()))[0]
    out["complete"] = (out["series_list_complete"] and not
                       out["series_truncated"] and not out["series_unread"])
    return out


def summary(result: dict, *, sample: int = 25) -> dict:
    """The walk without its market payloads (bounded, for heartbeats and
    snapshots)."""
    r = dict(result or {})
    tr = dict(r.get("series_truncated") or {})
    un = list(r.get("series_unread") or [])
    ms = r.get("markets") or []
    return {k: r.get(k) for k in ("version", "complete", "stopped",
                                  "requests", "series_total",
                                  "series_complete", "series_list_complete")
            } | {"markets": len(ms),
                 "with_rules_primary": sum(
                     1 for m in ms if str(m.get("rules_primary") or "").strip()),
                 "with_rules_secondary": sum(
                     1 for m in ms
                     if str(m.get("rules_secondary") or "").strip()),
                 "series_truncated_count": len(tr),
                 "series_truncated": dict(sorted(tr.items())[:sample]),
                 "series_unread_count": len(un),
                 "series_unread": un[:sample]}


def describe() -> dict:
    return {"version": VERSION, "base": BASE, "method": "GET",
            "credentials": "NONE", "orders": False, "mutations": False,
            "endpoints": ["GET /series?category=%s" % SPORTS_CATEGORY,
                          "GET /markets?series_ticker=<T>&status=open"
                          "&limit=%d&cursor=<c>" % PAGE_LIMIT],
            "completeness": "cursor exhausted for the series list and every "
                            "series, else TRUNCATED by name",
            "pacing_min_gap_s": MIN_GAP_S}
