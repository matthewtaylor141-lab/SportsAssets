#!/usr/bin/env python3
"""Phase X: the READ-ONLY Kalshi market-data research path.

OFFLINE UNTIL EGRESS EXISTS. Importing this module contacts nothing. The
client refuses every write and every portfolio path by construction, not
by convention -- see ReadOnlyGuard.

WHY THIS EXISTS RATHER THAN REUSING THE TRADING ADAPTER
edge-engine's KalshiAdapter is a TRADING client. Three of its habits are
correct there and wrong here:

  1. It discards everything it does not trade on. `_discover_series`
     asks the venue for nested markets and keeps `ticker` and
     `yes_sub_title` -- exactly the two fields from which no settlement
     proposition can be built.
  2. On 429 it sleeps 1 s and ABANDONS the read. For a trader a skipped
     book is a skipped opportunity; for research it is a hole in the
     record that later reads as "no opportunity there".
  3. It can place and cancel orders.

So this is a separate read path. It changes nothing about production
trading behaviour, and nothing here can reach a write endpoint.

RAW BESIDE NORMALIZED. Every record carries the venue's own bytes
alongside the normalized view. A normalization defect must be repairable
from the sealed evidence without going back to the venue.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

# --------------------------------------------------------------- statuses
VERIFIED = "VERIFIED"
QUOTED = "QUOTED_VERBATIM"
ABSENT = "NOT_IDENTIFIED"
STATUSES = (VERIFIED, QUOTED, ABSENT)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
VENUE = "KALSHI"

# The rate limit this venue actually enforces is NOT ESTABLISHED. 1.0 s is
# a self-imposed research floor chosen for safety, never a measured
# ceiling, and no result may be described as rate-safe because of it.
MIN_INTERVAL_S = 1.0
KALSHI_RATE_LIMIT_NOT_ESTABLISHED = True
MAX_INTERVAL_S = 30.0
BACKOFF_MULT = 2.0
RECOVER_DECAY = 0.9
MAX_ATTEMPTS = 4


def field(value: Any, status: str, source: str) -> dict:
    """One record field. A VERIFIED slot the venue did not fill is
    NOT_IDENTIFIED -- never an empty VERIFIED."""
    if status == ABSENT or (status == VERIFIED and value in (None, "", [], {})):
        return {"value": None, "status": ABSENT, "source": source}
    return {"value": value, "status": status, "source": source}


def present(node: dict) -> bool:
    return bool(node) and node.get("status") != ABSENT


# Periods that do not end a sentence. "vs." is the one that matters most
# here and it is not a nicety: on "will settle to the winner of the Canelo
# Alvarez vs. Christian Mbilli boxing match", a naive split leaves the
# affirmative clause as "...winner of the Canelo Alvarez vs.", which names
# ONE fighter -- and the direction test then reads a moneyline that does
# not determine direction as though it did, on the wrong side. Caught by
# test_5b before any of this reached a venue.
NON_TERMINAL_ABBREVIATIONS = frozenset((
    "vs", "v", "no", "nos", "st", "mr", "mrs", "ms", "dr", "prof", "jr",
    "sr", "inc", "ltd", "co", "corp", "etc", "approx", "est", "al", "fig",
    "ca", "cf", "eg", "ie", "am", "pm", "u.s", "u.k",
))


def sentences(text: str) -> list[str]:
    """Split prose into sentences without altering one character of what
    survives; each result is a contiguous slice of `text`.

    A period only ends a sentence when the word before it is not a known
    abbreviation or a bare initial, and what follows opens like a new
    sentence. Over-splitting is not cosmetic: a truncated clause is a
    misquotation of the contract, and downstream this file treats the
    clause as the venue's own words.
    """
    if not text:
        return []
    out, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+", text):
        head = text[start:m.start()]
        nxt = text[m.end():m.end() + 1]
        if nxt and not (nxt.isupper() or nxt.isdigit() or nxt in "\"'("):
            continue
        if head.endswith("."):
            word = re.search(r"([A-Za-z0-9.]+)\.$", head)
            tok = (word.group(1).rstrip(".").lower() if word else "")
            if tok in NON_TERMINAL_ABBREVIATIONS or (
                    len(tok) == 1 and tok.isalpha()):
                continue
        piece = head.strip()
        if piece:
            out.append(piece)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


# ------------------------------------------------------------ read-only
class ReadOnlyViolation(RuntimeError):
    """Raised when anything asks this client to do something that is not a
    GET of a market-data path. It is an exception and not a log line
    because a research client that can be talked into a write is not a
    read-only client."""


# Market data only. /portfolio/* is deliberately absent: it is an account
# surface, it needs credentials this process must never load, and nothing
# in Phase X reads it.
ALLOWED_PATHS = (
    re.compile(r"^/events$"),
    re.compile(r"^/events/[A-Za-z0-9_.\-]+$"),
    re.compile(r"^/markets$"),
    re.compile(r"^/markets/[A-Za-z0-9_.\-]+$"),
    re.compile(r"^/markets/[A-Za-z0-9_.\-]+/orderbook$"),
    re.compile(r"^/series/[A-Za-z0-9_.\-]+$"),
    re.compile(r"^/exchange/status$"),
)
DENIED_SUBSTRINGS = ("portfolio", "order", "cancel", "balance", "position",
                     "fill", "settlement")


class ReadOnlyGuard:
    """Two independent gates, both of which must pass.

    An allowlist alone would admit /markets/{ticker} where ticker is
    'X/../portfolio/orders'; a denylist alone would admit a read path
    nobody vetted. The orderbook path is the one place 'order' appears as
    a legitimate substring, so it is matched structurally first and the
    denylist is applied to what remains.
    """

    @staticmethod
    def check(method: str, path: str) -> None:
        if method.upper() != "GET":
            raise ReadOnlyViolation(
                "method %r refused: this client performs GET only" % method)
        if not path.startswith("/") or ".." in path:
            raise ReadOnlyViolation("path %r refused: not a literal absolute "
                                    "venue path" % path)
        if not any(rx.match(path) for rx in ALLOWED_PATHS):
            raise ReadOnlyViolation(
                "path %r refused: not in the market-data allowlist" % path)
        # '/orderbook' is the sole legitimate 'order' substring; strip the
        # vetted suffix before the denylist sees it.
        probe = path[:-len("/orderbook")] if path.endswith("/orderbook") \
            else path
        low = probe.lower()
        for bad in DENIED_SUBSTRINGS:
            if bad in low:
                raise ReadOnlyViolation(
                    "path %r refused: contains %r" % (path, bad))


# ---------------------------------------------------------------- pacing
class ResearchPacer:
    """An explicit minimum interval, exact Retry-After, and backoff that
    does not snap back.

    The trading adapter's 429 handling (sleep 1 s, abandon the read) is
    not usable here: research cannot have holes it does not know about.
    This pacer retries, and when it finally gives up it SAYS SO in a
    sealed error record rather than returning nothing.
    """

    def __init__(self, base: float = MIN_INTERVAL_S,
                 clock=time, sleep=None) -> None:
        self.base = base
        self.interval = base
        self._last: float | None = None
        self.events: list[dict] = []
        self._clock = clock
        self._sleep = sleep or clock.sleep

    def wait(self) -> None:
        if self._last is not None:
            slack = (self._last + self.interval) - self._clock.monotonic()
            if slack > 0:
                self._sleep(slack)
        self._last = self._clock.monotonic()

    def last_request_monotonic(self) -> float | None:
        """Read-only. Lets a caller that paces a later stage itself carry
        the floor across the stage boundary -- the Track B-L lesson."""
        return self._last

    def on_429(self, retry_after: Any) -> float:
        wait_s = None
        if retry_after is not None:
            try:
                wait_s = max(0.0, float(retry_after))
            except (TypeError, ValueError):
                wait_s = None
        before = self.interval
        self.interval = min(MAX_INTERVAL_S, self.interval * BACKOFF_MULT)
        self.events.append({"event": "429", "retry_after": retry_after,
                            "slept_s": wait_s, "interval_before": before,
                            "interval_after": self.interval})
        if wait_s:
            self._sleep(wait_s)
        self._last = self._clock.monotonic()
        return self.interval

    def on_error(self, status: Any) -> float:
        before = self.interval
        self.interval = min(MAX_INTERVAL_S, self.interval * BACKOFF_MULT)
        self.events.append({"event": "error", "http_status": status,
                            "interval_before": before,
                            "interval_after": self.interval})
        return self.interval

    def on_success(self) -> None:
        if self.interval > self.base:
            self.interval = max(self.base, self.interval * RECOVER_DECAY)


# ---------------------------------------------------------------- client
class KalshiResearchClient:
    """GET-only market data, one receipt per requested observation.

    `transport` is any callable (method, url, params, timeout) -> object
    with .status_code, .headers, .text and .json(). It is injected so the
    offline suite exercises the real code path with fixtures, and so this
    module imports without a network library present.
    """

    def __init__(self, transport, base: str = KALSHI_BASE,
                 pacer: ResearchPacer | None = None, clock=time) -> None:
        self.base = base.rstrip("/")
        self.transport = transport
        self.pacer = pacer or ResearchPacer(clock=clock)
        self.clock = clock
        self.receipts: list[dict] = []

    def get(self, path: str, params: dict | None = None,
            timeout: float = 20.0) -> dict:
        """One requested observation -> exactly one receipt, always.

        Returns the receipt. `ok` says whether a body came back. A failed
        observation is a sealed record with its attempts, never a silent
        None: a hole that looks like an absence is how "no opportunity
        there" gets manufactured.
        """
        ReadOnlyGuard.check("GET", path)
        attempts: list[dict] = []
        body: Any = None
        ok = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.pacer.wait()
            wall = self.clock.time()
            mono = self.clock.monotonic()
            rec: dict = {"attempt": attempt, "request_wall": wall,
                         "request_monotonic": mono}
            try:
                resp = self.transport("GET", self.base + path, params,
                                      timeout)
            except Exception as exc:                       # noqa: BLE001
                rec.update({"outcome": "TRANSPORT_EXCEPTION",
                            "exception": "%s: %s" % (type(exc).__name__, exc)})
                attempts.append(rec)
                self.pacer.on_error(None)
                continue
            status = getattr(resp, "status_code", None)
            headers = dict(getattr(resp, "headers", {}) or {})
            rec["http_status"] = status
            rec["response_monotonic"] = self.clock.monotonic()
            if status == 429:
                rec["outcome"] = "RATE_LIMITED"
                rec["retry_after"] = headers.get("Retry-After") \
                    or headers.get("retry-after")
                attempts.append(rec)
                self.pacer.on_429(rec["retry_after"])
                continue
            if status != 200:
                rec["outcome"] = "HTTP_%s" % status
                rec["body_head"] = (getattr(resp, "text", "") or "")[:200]
                attempts.append(rec)
                self.pacer.on_error(status)
                continue
            raw = getattr(resp, "text", "") or ""
            try:
                body = resp.json()
            except Exception:                              # noqa: BLE001
                rec["outcome"] = "BAD_JSON"
                rec["body_head"] = raw[:200]
                attempts.append(rec)
                self.pacer.on_error(status)
                continue
            rec["outcome"] = "OK"
            rec["response_sha256"] = hashlib.sha256(
                (raw or json.dumps(body, sort_keys=True, default=str))
                .encode()).hexdigest()
            rec["response_bytes"] = len(raw)
            attempts.append(rec)
            self.pacer.on_success()
            ok = True
            break
        receipt = {
            "venue": VENUE, "path": path, "params": dict(params or {}),
            "ok": ok,
            "outcome": attempts[-1]["outcome"] if attempts else "NO_ATTEMPT",
            "attempts": attempts,
            "local_receipt_wall": self.clock.time(),
            "local_receipt_monotonic": self.clock.monotonic(),
            "body": body if ok else None,
            "rate_limit_established": not KALSHI_RATE_LIMIT_NOT_ESTABLISHED,
        }
        if not ok:
            receipt["OBSERVATION_FAILED"] = True
        self.receipts.append(receipt)
        return receipt

    # -- the three market-data reads Phase X needs, and no others --------
    def events(self, series_ticker: str, cursor: str = "",
               limit: int = 100) -> dict:
        p = {"series_ticker": series_ticker, "status": "open",
             "with_nested_markets": "true", "limit": limit}
        if cursor:
            p["cursor"] = cursor
        return self.get("/events", p)

    def market(self, ticker: str) -> dict:
        return self.get("/markets/%s" % ticker)

    def orderbook(self, ticker: str) -> dict:
        return self.get("/markets/%s/orderbook" % ticker)


# ------------------------------------------------------- contract record
# Every field the venue may supply that can move PAYS_1_IF, equivalence,
# cancellation, postponement, overtime, draw, void, settlement timing or
# settlement source. Retained whether or not this phase reads it, because
# a field discarded at ingestion is a field nobody can audit later.
RETAINED_MARKET_FIELDS = (
    "ticker", "event_ticker", "series_ticker", "market_type", "title",
    "subtitle", "yes_sub_title", "no_sub_title",
    "strike_type", "floor_strike", "cap_strike", "strike",
    "floor_strike_dollars", "cap_strike_dollars",
    "open_time", "close_time", "expiration_time",
    "expected_expiration_time", "latest_expiration_time",
    "settlement_timer_seconds", "settlement_value", "settlement_sources",
    "rules_primary", "rules_secondary", "status", "result",
    "can_close_early", "response_price_units", "tick_size",
    "notional_value", "risk_limit_cents", "category",
)
RETAINED_EVENT_FIELDS = (
    "event_ticker", "series_ticker", "title", "sub_title", "category",
    "strike_date", "strike_period", "mutually_exclusive", "collateral_return_type",
)

RULE_TOPICS: dict[str, tuple[str, ...]] = {
    "CANCELLATION_RULES": ("cancel", "canceled", "cancelled"),
    "POSTPONEMENT_RULES": ("postpon", "delay", "reschedul", "suspend",
                           "not held within", "not rescheduled"),
    "OVERTIME_RULES": ("overtime", "extra time", "extra innings",
                       "shootout", "penalty shoot"),
    "DRAW_RULES": ("draw", "tie ", "tied", "no contest", "push"),
    "VOID_RULES": ("void", "refund", "invalid", "nullif"),
}
OTHER_TOPICS = ("sourced from", "governing body", "settle", "settlement",
                "resolve", "expiration", "official")


def quote_topic(prose: list[tuple[str, str]],
                needles: tuple[str, ...]) -> list[dict]:
    hits: list[dict] = []
    for name, text in prose:
        for sent in sentences(text):
            if any(n in sent.lower() for n in needles):
                hits.append({"source": name, "text": sent})
    return hits


def rule_slots(prose: list[tuple[str, str]]) -> dict:
    out: dict[str, dict] = {}
    claimed: set[str] = set()
    names = "; ".join(n for n, _ in prose)
    for slot, needles in RULE_TOPICS.items():
        hits = quote_topic(prose, needles)
        for h in hits:
            claimed.add(h["text"])
        out[slot] = (field(hits, QUOTED, names) if hits else
                     field(None, ABSENT,
                           "no dedicated field; no matching sentence"))
    other = [h for h in quote_topic(prose, OTHER_TOPICS)
             if h["text"] not in claimed]
    out["OTHER_RESOLUTION_CONDITIONS"] = (
        field(other, QUOTED, names) if other else
        field(None, ABSENT, "no dedicated field; no unclaimed sentence"))
    return out


def kalshi_contract_record(market: dict, event: dict | None = None,
                           provenance: dict | None = None) -> dict:
    """One Kalshi contract, RAW beside normalized.

    Nothing is inferred. A field the venue did not send is NOT_IDENTIFIED,
    including the rules fields -- which the trading adapter never reads at
    all, and whose absence is the reason Phase X1 could not run.
    """
    ev = event or {}
    raw_m = {k: market.get(k) for k in RETAINED_MARKET_FIELDS
             if k in market}
    raw_e = {k: ev.get(k) for k in RETAINED_EVENT_FIELDS if k in ev}

    prose: list[tuple[str, str]] = []
    for key, label in (("rules_primary", "market.rules_primary"),
                       ("rules_secondary", "market.rules_secondary")):
        if market.get(key):
            prose.append((label, market[key]))

    rec: dict[str, Any] = {
        "RECORD_VERSION": "PX1-KALSHI-1",
        "VENUE": VENUE,
        "TICKER": field(market.get("ticker"), VERIFIED, "market.ticker"),
        "EVENT_TICKER": field(market.get("event_ticker")
                              or ev.get("event_ticker"), VERIFIED,
                              "market.event_ticker|event.event_ticker"),
        "SERIES_TICKER": field(market.get("series_ticker")
                               or ev.get("series_ticker"), VERIFIED,
                               "market.series_ticker|event.series_ticker"),
        "TITLE": field(market.get("title"), VERIFIED, "market.title"),
        "EVENT_TITLE": field(ev.get("title"), VERIFIED, "event.title"),
        "SUBTITLE": field(market.get("subtitle"), VERIFIED,
                          "market.subtitle"),
        "YES_SUB_TITLE": field(market.get("yes_sub_title"), VERIFIED,
                               "market.yes_sub_title"),
        "NO_SUB_TITLE": field(market.get("no_sub_title"), VERIFIED,
                              "market.no_sub_title"),
        "MARKET_TYPE": field(market.get("market_type"), VERIFIED,
                             "market.market_type"),
        "STRIKE": field({k: market[k] for k in
                         ("strike_type", "floor_strike", "cap_strike",
                          "strike", "floor_strike_dollars",
                          "cap_strike_dollars") if k in market} or None,
                        VERIFIED, "market.strike_*"),
        "CLOSE_TIME": field(market.get("close_time"), VERIFIED,
                            "market.close_time"),
        "EXPIRATION_TIME": field(market.get("expiration_time"), VERIFIED,
                                 "market.expiration_time"),
        "EXPECTED_EXPIRATION_TIME": field(
            market.get("expected_expiration_time"), VERIFIED,
            "market.expected_expiration_time"),
        "SETTLEMENT_TIMER_SECONDS": field(
            market.get("settlement_timer_seconds"), VERIFIED,
            "market.settlement_timer_seconds"),
        "SETTLEMENT_SOURCES": field(market.get("settlement_sources"),
                                    VERIFIED, "market.settlement_sources"),
        "RULES_PRIMARY": field(market.get("rules_primary"), VERIFIED,
                               "market.rules_primary"),
        "RULES_SECONDARY": field(market.get("rules_secondary"), VERIFIED,
                                 "market.rules_secondary"),
        "MARKET_STATUS": field(market.get("status"), VERIFIED,
                               "market.status"),
        "RESULT": field(market.get("result"), VERIFIED, "market.result"),
        "CAN_CLOSE_EARLY": field(market.get("can_close_early"), VERIFIED,
                                 "market.can_close_early"),
        "RISK_LIMIT_CENTS": field(market.get("risk_limit_cents"), VERIFIED,
                                  "market.risk_limit_cents"),
        "TICK_SIZE": field(market.get("tick_size"), VERIFIED,
                           "market.tick_size"),
    }
    rec.update(rule_slots(prose))
    rec["SOURCE_TEXTS"] = {
        "RULES_PRIMARY": market.get("rules_primary"),
        "RULES_SECONDARY": market.get("rules_secondary"),
        "TITLE": market.get("title"),
        "EVENT_TITLE": ev.get("title"),
    }
    rec["RAW_MARKET"] = raw_m
    rec["RAW_EVENT"] = raw_e
    rec["PROVENANCE"] = provenance or {}
    return rec


# ------------------------------------------------------------ book record
def _levels(pairs, scale_cents: bool) -> list[tuple[float, float]]:
    out = []
    for pq in pairs or []:
        try:
            p, q = float(pq[0]), float(pq[1])
        except (TypeError, ValueError, IndexError):
            continue
        out.append((p / 100.0 if scale_cents else p, q))
    return out


def kalshi_book_record(ticker: str, body: dict, wall: float,
                       monotonic: float, venue_ts: Any = None) -> dict:
    """EVERY returned level, both sides. No truncation.

    The production relay keeps 10 levels because a desk ticket shows 10.
    Research keeps all of them: a VWAP is only honest to the depth it was
    allowed to see, and a book silently cut at level 10 reports
    DEPTH_EXHAUSTED at a size the venue would actually have filled.

    Kalshi publishes resting BIDS on both sides, so executable YES ASKS
    are the NO bids mirrored through $1 -- the one transformation here,
    and the raw payload is kept beside it.
    """
    fp = body.get("orderbook_fp") or {}
    ob = body.get("orderbook") or {}
    if fp:
        yes_raw = _levels(fp.get("yes_dollars"), False)
        no_raw = _levels(fp.get("no_dollars"), False)
        fmt = "orderbook_fp"
    else:
        yes_raw = _levels(ob.get("yes_dollars") or ob.get("yes"),
                          not (ob.get("yes_dollars")))
        no_raw = _levels(ob.get("no_dollars") or ob.get("no"),
                         not (ob.get("no_dollars")))
        fmt = "orderbook"

    bids = [{"VENUE": VENUE, "TICKER": ticker, "SIDE": "BID",
             "PRICE": p, "QUANTITY": q, "LEVEL": i}
            for i, (p, q) in enumerate(
                sorted(yes_raw, key=lambda x: -x[0]), start=1)]
    asks = [{"VENUE": VENUE, "TICKER": ticker, "SIDE": "ASK",
             "PRICE": round(1.0 - p, 6), "QUANTITY": q, "LEVEL": i,
             "DERIVED_FROM": "no_bid@%s" % p}
            for i, (p, q) in enumerate(
                sorted(no_raw, key=lambda x: -x[0]), start=1)]
    asks.sort(key=lambda lv: lv["PRICE"])
    for i, lv in enumerate(asks, start=1):
        lv["LEVEL"] = i
    return {
        "VENUE": VENUE, "TICKER": ticker, "FORMAT": fmt,
        "BIDS": bids, "ASKS": asks,
        "BID_LEVELS": len(bids), "ASK_LEVELS": len(asks),
        "VENUE_TIMESTAMP": venue_ts,
        "LOCAL_RECEIPT_WALL": wall,
        "LOCAL_RECEIPT_MONOTONIC": monotonic,
        "RAW": body,
    }


def pmus_book_record(slug: str, market_data: dict, wall: float,
                     monotonic: float) -> dict:
    """The same shape from a PMUS /book payload, so both venues reach the
    economics through one structure. PMUS quotes both sides natively:
    `bids` and `offers`, px as {"value","currency"}."""
    def side(rows, name):
        out = []
        for r in rows or []:
            px = r.get("px") or {}
            try:
                p = float(px.get("value"))
                q = float(r.get("qty"))
            except (TypeError, ValueError):
                continue
            out.append({"VENUE": "PMUS", "TICKER": slug, "SIDE": name,
                        "PRICE": p, "QUANTITY": q, "LEVEL": 0})
        return out

    bids = sorted(side(market_data.get("bids"), "BID"),
                  key=lambda lv: -lv["PRICE"])
    asks = sorted(side(market_data.get("offers"), "ASK"),
                  key=lambda lv: lv["PRICE"])
    for seq in (bids, asks):
        for i, lv in enumerate(seq, start=1):
            lv["LEVEL"] = i
    return {
        "VENUE": "PMUS", "TICKER": slug, "FORMAT": "marketData",
        "BIDS": bids, "ASKS": asks,
        "BID_LEVELS": len(bids), "ASK_LEVELS": len(asks),
        "VENUE_TIMESTAMP": market_data.get("transactTime"),
        "LOCAL_RECEIPT_WALL": wall,
        "LOCAL_RECEIPT_MONOTONIC": monotonic,
        "RAW": market_data,
    }
