"""KALSHI AS A MARKET-DATA VENUE: COMPLETE CATALOGUE, STRUCTURED FIXTURES, BOOKS,
AND ITS OWN HEALTH DOMAIN (Kalshi Canonical Venue V1).

MARKET DATA ONLY. GET-only, credential-free, through kalshi_catalogue's
GetOnlyTransport (which refuses every method but GET before a byte leaves
the process). This module imports neither kalshi_venue nor kalshi_orders:
no order, cancel, funding, sizing or capital path is reachable from it
(tests/test_kalshi_isolation.py and test_kalshi_canonical_market_data.py).

WHAT IT READS (documented public endpoints, no authentication):
  GET /series?category=Sports                     the sports series list
  GET /markets?status=open&limit=1000&cursor=..   EVERY open market, cursor
                                                  to exhaustion, kept only
                                                  when its series is a
                                                  sports series
  GET /milestones?related_event_ticker=<E>        the structured fixture:
                                                  league, start, home/away
                                                  structured team ids
  GET /markets/<T>/orderbook?depth=N              the book: YES and NO BIDS

COMPLETENESS IS PROVEN, NEVER ASSUMED. The catalogue is COMPLETE only when
the series list and the open-markets cursor were both exhausted; a request
budget, a failed request or a repeated cursor ends it TRUNCATED, by name.
(The registry's per-series walk, kalshi_catalogue.walk, stopped at its
800-request budget in production around series "KXE..." -- every later
series, MLB / NBA / NFL / NHL included, was never read. This walk reads
every open market once, so its cost does not grow with the series count.)

YES / NO ARE TWO EXECUTABLE PATHS, FROM THE VENUE'S OWN SEMANTICS. Kalshi's
orderbook returns only BIDS for YES and for NO, and its documentation states
the protocol (docs.kalshi.com/getting_started/orderbook_responses, fetched
2026-10-07, sha256 f1777255...): "A YES BID at price X is equivalent to a NO
ASK at price ($1.00 - X); a NO BID at price Y is equivalent to a YES ASK at
price ($1.00 - Y)". The YES ask ladder is therefore the NO bid ladder at
1 - price and the NO ask ladder the YES bid ladder at 1 - price -- the
venue's documented executable representation, not arithmetic inferred from a
YES quote. The market record's own yes_ask / no_ask are kept beside it and
must agree with the book's top (a disagreement is named, never smoothed).

STRUCTURED FIXTURE IDENTITY. A game event is mapped only from structured
facts: the milestone's league, start_date and home_team_id / away_team_id,
and each market's custom_strike team id. A team market whose structured id
is neither the home nor the away id, two markets for one team, a missing
milestone, or a league outside the declared table is NOT_ESTABLISHED.
Titles and sub-titles are never read for identity.

KALSHI_HEALTH is its own domain: a Kalshi 429 backs off Kalshi only, and
nothing here reads or writes Polymarket health.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import kalshi_catalogue as KC

VERSION = "KALSHI_MARKET_DATA_V1"
HEALTH_DOMAIN = "KALSHI_HEALTH"
BASE = KC.BASE
PAGE_LIMIT = 1000
DEFAULT_MAX_REQUESTS = 600
DEFAULT_PACING_S = 0.12
ORDERBOOK_DEPTH = 10
#: a Kalshi book is current for routing / arbitrage inside this bound
BOOK_SLA_S = 30.0

ORDERBOOK_PROTOCOL = {
    "basis": "KALSHI_DOCUMENTED_RECIPROCAL_BOOK",
    "url": "https://docs.kalshi.com/getting_started/orderbook_responses",
    "sha256": "f177725550bb04ea0e6f83d409f8a2baa39fd3b21cbd634e7b377ebd496765c6",
    "fetched": "2026-10-07T18:59:17Z (fetch-docs run 37671010542)",
    "rule": ("A YES BID at price X is equivalent to a NO ASK at price "
             "($1.00 - X); a NO BID at price Y is equivalent to a YES ASK at "
             "price ($1.00 - Y)")}

#: the leagues whose GAME series this venue maps, with the regular outcome
#: space the league's own rules admit. TWO_WAY: a winner or the game does
#: not count (no draw strike exists); THREE_WAY: a TIE strike is listed.
#: A series not resolved to a league here is counted UNMAPPED by name.
LEAGUE_SPORT = {
    "MLB": ("BASEBALL", "TWO_WAY"), "NBA": ("BASKETBALL", "TWO_WAY"),
    "WNBA": ("BASKETBALL", "TWO_WAY"), "NHL": ("HOCKEY", "TWO_WAY"),
    "NFL": ("FOOTBALL", "TWO_WAY_TIE_POSSIBLE"),
    "NCAAF": ("FOOTBALL", "TWO_WAY"), "CFB": ("FOOTBALL", "TWO_WAY"),
    "NCAAB": ("BASKETBALL", "TWO_WAY"), "CBB": ("BASKETBALL", "TWO_WAY"),
    "EPL": ("SOCCER", "THREE_WAY"), "MLS": ("SOCCER", "THREE_WAY"),
    "LALIGA": ("SOCCER", "THREE_WAY"), "BUNDESLIGA": ("SOCCER", "THREE_WAY"),
    "SERIEA": ("SOCCER", "THREE_WAY"), "LIGUE1": ("SOCCER", "THREE_WAY"),
    "UCL": ("SOCCER", "THREE_WAY"),
}
TIE_CODES = ("TIE", "DRAW")
STRIKE_TEAM_KEYS = ("baseball_team", "basketball_team", "football_team",
                    "hockey_team", "soccer_team", "team")

R_TRUNCATED_BUDGET = "KALSHI_MD_TRUNCATED_REQUEST_BUDGET"
R_TRUNCATED_HTTP = "KALSHI_MD_TRUNCATED_REQUEST_FAILED"
R_TRUNCATED_CURSOR = "KALSHI_MD_TRUNCATED_CURSOR_REPEATED"
R_RATE_LIMITED = "KALSHI_MD_RATE_LIMITED_429"
R_SERIES_UNREAD = "KALSHI_MD_SERIES_LIST_NOT_READ"


def _dec(x) -> Decimal | None:
    if x is None or x == "" or isinstance(x, bool):
        return None
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def _ts(v) -> float | None:
    if not v:
        return None
    import datetime as dt
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.timestamp() if d.tzinfo else None


# ── health: its own domain ─────────────────────────────────────────────

@dataclass
class KalshiHealth:
    """KALSHI_HEALTH. Backoff on a 429 is Kalshi's alone (the venue sends no
    Retry-After; there is no penalty, the bucket refills): 2, 4, 8 ... 60 s."""
    clock: object = time.time
    requests: int = 0
    ok: int = 0
    failures: int = 0
    http_429: int = 0
    consecutive_failures: int = 0
    backoff_until: float = 0.0
    last_ok_at: float | None = None
    last_failure: str | None = None
    domain: str = field(default=HEALTH_DOMAIN)

    def blocked(self) -> bool:
        return self.clock() < self.backoff_until

    def record(self, err: str | None) -> None:
        self.requests += 1
        if err is None:
            self.ok += 1
            self.consecutive_failures = 0
            self.last_ok_at = self.clock()
            return
        self.failures += 1
        self.consecutive_failures += 1
        self.last_failure = err
        if err == "HTTP_429":
            self.http_429 += 1
            step = min(60.0, 2.0 ** min(6, self.consecutive_failures))
            self.backoff_until = self.clock() + step

    def digest(self) -> dict:
        now = self.clock()
        return {"domain": self.domain, "requests": self.requests,
                "ok": self.ok, "failures": self.failures,
                "http_429": self.http_429,
                "consecutive_failures": self.consecutive_failures,
                "backing_off": now < self.backoff_until,
                "backoff_remaining_s": round(max(0.0, self.backoff_until
                                                 - now), 1),
                "last_ok_age_s": (None if self.last_ok_at is None
                                  else round(now - self.last_ok_at, 1)),
                "last_failure": self.last_failure}


def _get(tx, pacer, health, path, params, timeout_s=KC.TIMEOUT_S):
    if health is not None and health.blocked():
        return None, "BACKOFF"
    body, err = KC._get_json(tx, pacer, BASE + path, params, timeout_s)
    if health is not None:
        health.record(err)
    return body, err


#: a catalogue page refused 429 is retried after the health backoff, at most
#: this many times, before the walk stops (Kalshi: no penalty, the bucket
#: refills, "apply exponential backoff on 429"); every 429 is still counted
PAGE_429_RETRIES = 3
PAGE_429_MAX_WAIT_S = 16.0


def _get_patient(tx, pacer, health, path, params, spend):
    """_get, retrying the SAME request after the 429 backoff (each retry is
    a budgeted request). Returns (body, err, retries)."""
    tries = 0
    while True:
        body, err = _get(tx, pacer, health, path, params)
        if err not in ("HTTP_429", "BACKOFF") or tries >= PAGE_429_RETRIES \
                or health is None or not spend():
            return body, err, tries
        tries += 1
        wait = min(PAGE_429_MAX_WAIT_S,
                   max(1.0, health.backoff_until - health.clock()))
        pacer.sleep(wait)


# ── the complete catalogue ─────────────────────────────────────────────

COMPACT_KEYS = ("ticker", "event_ticker", "status", "market_type",
                "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars",
                "no_ask_dollars", "yes_bid_size_fp", "yes_ask_size_fp",
                "rules_primary", "rules_secondary", "custom_strike",
                "strike_type", "updated_time", "close_time",
                "occurrence_datetime", "expected_expiration_time",
                "price_level_structure", "can_close_early", "result",
                "title", "subtitle", "yes_sub_title", "no_sub_title")


def compact_market(m: dict, series: dict) -> dict:
    out = {k: m.get(k) for k in COMPACT_KEYS if k in m}
    out["_series"] = series
    return out


def walk_open_sports(transport=None, *, health: KalshiHealth | None = None,
                     max_requests: int = DEFAULT_MAX_REQUESTS,
                     pacing_s: float = DEFAULT_PACING_S,
                     sleep=time.sleep, clock=time.monotonic) -> dict:
    """The sports series list, then EVERY open market cursor-to-exhaustion,
    kept when its series is a sports series. Shape-compatible with
    kalshi_catalogue.walk (markets carry `_series`), so the registry
    populator takes it unchanged. Never raises."""
    tx = transport or KC.GetOnlyTransport()
    pacer = KC._Pacer(pacing_s, sleep, clock)
    out = {"version": VERSION, "base": BASE, "method": "GET",
           "credentials": "NONE", "complete": False, "stopped": None,
           "requests": 0, "series_total": 0, "series_list_complete": False,
           "markets_seen": 0, "markets": [], "pages": 0,
           "series_with_open_markets": 0,
           "authority": "MARKET_DATA_ONLY_NO_ORDER_AUTHORITY"}
    budget = max(2, int(max_requests))

    def spend():
        if out["requests"] >= budget:
            return False
        out["requests"] += 1
        return True
    series, cursor, seen = {}, None, set()
    while True:
        if not spend():
            out["stopped"] = R_TRUNCATED_BUDGET
            return out
        p = {"category": KC.SPORTS_CATEGORY}
        if cursor:
            p["cursor"] = cursor
        body, err = _get(tx, pacer, health, "/series", p)
        if err:
            out["stopped"] = (R_RATE_LIMITED if err == "HTTP_429"
                              else R_SERIES_UNREAD)
            out["error"] = err
            return out
        for s in body.get("series") or []:
            if isinstance(s, dict) and s.get("ticker"):
                series.setdefault(str(s["ticker"]), {
                    "ticker": str(s["ticker"]), "title": s.get("title"),
                    "tags": list(s.get("tags") or []),
                    "category": s.get("category"),
                    "fee_type": s.get("fee_type"),
                    "fee_multiplier": s.get("fee_multiplier"),
                    "contract_terms_url": s.get("contract_terms_url")})
        nxt = body.get("cursor") or None
        if not nxt:
            break
        if nxt in seen:
            out["stopped"] = R_TRUNCATED_CURSOR
            return out
        seen.add(nxt)
        cursor = nxt
    out["series_list_complete"] = True
    out["series_total"] = len(series)
    cursor, seen, with_open = None, set(), set()
    while True:
        if not spend():
            out["stopped"] = R_TRUNCATED_BUDGET
            break
        p = {"status": "open", "limit": PAGE_LIMIT, "mve_filter": "exclude"}
        if cursor:
            p["cursor"] = cursor
        body, err, tries = _get_patient(tx, pacer, health, "/markets", p,
                                        spend)
        out["page_429_retries"] = out.get("page_429_retries", 0) + tries
        if err:
            out["stopped"] = (R_RATE_LIMITED if err in ("HTTP_429",
                                                        "BACKOFF")
                              else "%s:%s" % (R_TRUNCATED_HTTP, err))
            break
        out["pages"] += 1
        for m in body.get("markets") or []:
            if not isinstance(m, dict) or not m.get("ticker"):
                continue
            out["markets_seen"] += 1
            st = str(m.get("event_ticker") or m["ticker"]).split("-")[0]
            s = series.get(st)
            if s is not None:
                out["markets"].append(compact_market(m, s))
                with_open.add(st)
        nxt = body.get("cursor") or None
        if not nxt:
            out["complete"] = True
            break
        if nxt in seen:
            out["stopped"] = R_TRUNCATED_CURSOR
            break
        seen.add(nxt)
        cursor = nxt
    out["series_with_open_markets"] = len(with_open)
    out["series"] = series
    return out


def census(result: dict) -> dict:
    """The catalogue census: complete / truncated by name, markets by
    series family class, without payloads."""
    ms = result.get("markets") or []
    by: dict = {}
    for m in ms:
        k = str((m.get("_series") or {}).get("ticker") or "?")
        by[k] = by.get(k, 0) + 1
    return {"version": VERSION, "complete": bool(result.get("complete")),
            "stopped": result.get("stopped"),
            "requests": result.get("requests"), "pages": result.get("pages"),
            "series_total": result.get("series_total"),
            "series_with_open_markets": result.get(
                "series_with_open_markets"),
            "markets_seen_all_categories": result.get("markets_seen"),
            "sports_markets": len(ms),
            "top_series": dict(sorted(by.items(), key=lambda kv: -kv[1])[:40])}


# ── structured fixtures ────────────────────────────────────────────────

def strike_team_id(m: dict) -> str | None:
    cs = m.get("custom_strike") if isinstance(m.get("custom_strike"),
                                              dict) else {}
    for k in STRIKE_TEAM_KEYS:
        v = cs.get(k)
        if v:
            return str(v)
    return None


def ticker_code(m: dict) -> str | None:
    t, e = str(m.get("ticker") or ""), str(m.get("event_ticker") or "")
    if not e or not t.startswith(e + "-"):
        return None
    c = t[len(e) + 1:]
    return c or None


@dataclass(frozen=True)
class KalshiFixture:
    event_ticker: str
    series_ticker: str
    sport: str | None
    league: str | None
    start_epoch: float | None
    home_id: str | None
    away_id: str | None
    home_code: str | None
    away_code: str | None
    tie_ticker: str | None
    team_tickers: tuple
    outcome_kind: str | None
    status: str
    reasons: tuple
    milestone_id: str | None


def fixture_from(event_ticker: str, markets: list, milestone: dict | None
                 ) -> KalshiFixture:
    """A Kalshi game event as a structured fixture, or NOT_ESTABLISHED with
    every missing fact named. Titles are never read."""
    r = []
    series = event_ticker.split("-")[0]
    det = (milestone or {}).get("details") or {}
    league = str(det.get("league") or "").upper() or None
    if milestone is None:
        r.append("MILESTONE_MISSING")
    elif det.get("main_game_event_ticker") and \
            det.get("main_game_event_ticker") != event_ticker and \
            event_ticker not in (milestone.get("primary_event_tickers") or []):
        r.append("MILESTONE_EVENT_MISMATCH")
    sport, kind = LEAGUE_SPORT.get(league or "", (None, None))
    if league is None:
        r.append("LEAGUE_MISSING")
    elif sport is None:
        r.append("LEAGUE_NOT_IN_TABLE:%s" % league)
    home, away = det.get("home_team_id"), det.get("away_team_id")
    if not home or not away:
        r.append("HOME_AWAY_IDS_MISSING")
    elif home == away:
        r.append("HOME_EQUALS_AWAY")
    start = _ts((milestone or {}).get("start_date"))
    if start is None:
        r.append("START_MISSING")
    by_id, tie, codes = {}, None, {}
    for m in markets:
        code = ticker_code(m)
        if code is None:
            r.append("TICKER_NOT_UNDER_EVENT:%s" % m.get("ticker"))
            continue
        if code.upper() in TIE_CODES:
            if tie is not None:
                r.append("TWO_TIE_STRIKES")
            tie = m["ticker"]
            continue
        sid = strike_team_id(m)
        if sid is None:
            r.append("STRIKE_TEAM_ID_MISSING:%s" % m["ticker"])
            continue
        if sid in by_id:
            r.append("TWO_MARKETS_FOR_ONE_TEAM:%s" % sid)
        by_id[sid] = m["ticker"]
        codes[sid] = code.upper()
    if home and away:
        extra = set(by_id) - {home, away}
        if extra:
            r.append("STRIKE_TEAM_NOT_IN_FIXTURE")
        for side, sid in (("HOME", home), ("AWAY", away)):
            if sid not in by_id:
                r.append("NO_MARKET_FOR_%s" % side)
    if kind == "THREE_WAY" and tie is None:
        r.append("THREE_WAY_WITHOUT_TIE_STRIKE")
    if kind and kind.startswith("TWO_WAY") and tie is not None:
        r.append("TIE_STRIKE_IN_A_TWO_WAY_LEAGUE")
    status = "ESTABLISHED" if not r else "NOT_ESTABLISHED"
    return KalshiFixture(
        event_ticker=event_ticker, series_ticker=series, sport=sport,
        league=league, start_epoch=start, home_id=home, away_id=away,
        home_code=codes.get(home), away_code=codes.get(away),
        tie_ticker=tie,
        team_tickers=tuple(sorted(by_id.values())),
        outcome_kind=kind, status=status, reasons=tuple(r),
        milestone_id=(milestone or {}).get("id"))


def group_events(markets: list) -> dict:
    ev: dict = {}
    for m in markets:
        e = m.get("event_ticker")
        if e:
            ev.setdefault(str(e), []).append(m)
    return ev


def game_candidates(markets: list) -> dict:
    """Events whose markets carry a structured team strike: the game
    events a fixture can be built for (futures / props are not)."""
    return {e: ms for e, ms in group_events(markets).items()
            if any(strike_team_id(m) for m in ms)}


# ── books ──────────────────────────────────────────────────────────────

def _levels(raw) -> list:
    out = []
    for x in raw or ():
        try:
            p, q = _dec(x[0]), _dec(x[1])
        except (TypeError, IndexError):
            continue
        if p is None or q is None or not (Decimal(0) < p < Decimal(1)) \
                or q <= 0:
            continue
        out.append((p, q))
    return out


def book_from_orderbook(body: dict, *, observed_at: float) -> dict:
    """The orderbook response -> bids and the documented ask ladders.
    YES asks = NO bids at 1 - price; NO asks = YES bids at 1 - price
    (ORDERBOOK_PROTOCOL). Best first. Quantities are whole contracts
    rounded DOWN (the venue may state fractional fixed-point counts)."""
    ob = (body or {}).get("orderbook_fp") or {}
    yb = _levels(ob.get("yes_dollars"))
    nb = _levels(ob.get("no_dollars"))

    def asks(bids):
        lv = [(Decimal(1) - p, int(q)) for p, q in bids if int(q) >= 1]
        return sorted(lv, key=lambda z: z[0])
    return {"yes_bids": sorted(((p, int(q)) for p, q in yb if int(q) >= 1),
                               key=lambda z: -z[0]),
            "no_bids": sorted(((p, int(q)) for p, q in nb if int(q) >= 1),
                              key=lambda z: -z[0]),
            "yes_asks": asks(nb), "no_asks": asks(yb),
            "observed_at": float(observed_at),
            "basis": ORDERBOOK_PROTOCOL["basis"],
            "readable": bool(ob)}


def quote_agreement(market: dict, book: dict) -> dict:
    """The market record's own yes_ask / no_ask against the book's top:
    agreement is evidence the reciprocal reading is the venue's; a
    disagreement is named (books move between the two reads)."""
    ya, na = _dec(market.get("yes_ask_dollars")), _dec(
        market.get("no_ask_dollars"))
    by = book["yes_asks"][0][0] if book.get("yes_asks") else None
    bn = book["no_asks"][0][0] if book.get("no_asks") else None
    return {"record_yes_ask": None if ya is None else str(ya),
            "book_yes_ask": None if by is None else str(by),
            "record_no_ask": None if na is None else str(na),
            "book_no_ask": None if bn is None else str(bn),
            "agree": (ya is not None and by is not None and ya == by
                      and na is not None and bn is not None and na == bn)}


def read_orderbook(ticker: str, transport=None, *, health=None, pacer=None,
                   depth: int = ORDERBOOK_DEPTH, now=time.time) -> dict:
    tx = transport or KC.GetOnlyTransport()
    pacer = pacer or KC._Pacer(DEFAULT_PACING_S, time.sleep, time.monotonic)
    body, err = _get(tx, pacer, health, "/markets/%s/orderbook" % ticker,
                     {"depth": int(depth)})
    if err:
        return {"ticker": ticker, "error": err, "readable": False}
    b = book_from_orderbook(body, observed_at=now())
    b["ticker"] = ticker
    return b


def read_series_fee_changes(series_ticker: str, transport=None, *,
                            health=None, pacer=None) -> tuple:
    """GET /series/fee_changes (public, effective-dated, the venue's own
    change ids): the published fee terms of one series, history included."""
    tx = transport or KC.GetOnlyTransport()
    pacer = pacer or KC._Pacer(DEFAULT_PACING_S, time.sleep, time.monotonic)
    body, err = _get(tx, pacer, health, "/series/fee_changes",
                     {"series_ticker": series_ticker,
                      "show_historical": "true"})
    if err:
        return None, err
    return list((body or {}).get("series_fee_change_arr") or []), None


def read_event_fee_changes(event_ticker: str, transport=None, *,
                           health=None, pacer=None) -> tuple:
    """GET /events/fee_changes: event-level overrides layered on the series
    terms (a null fee_type_override / fee_multiplier_override = cleared)."""
    tx = transport or KC.GetOnlyTransport()
    pacer = pacer or KC._Pacer(DEFAULT_PACING_S, time.sleep, time.monotonic)
    body, err = _get(tx, pacer, health, "/events/fee_changes",
                     {"event_ticker": event_ticker,
                      "show_historical": "true"})
    if err:
        return None, err
    return list((body or {}).get("event_fee_changes") or []), None


def read_milestone(event_ticker: str, transport=None, *, health=None,
                   pacer=None) -> tuple:
    tx = transport or KC.GetOnlyTransport()
    pacer = pacer or KC._Pacer(DEFAULT_PACING_S, time.sleep, time.monotonic)
    body, err = _get(tx, pacer, health, "/milestones",
                     {"related_event_ticker": event_ticker, "limit": 5})
    if err:
        return None, err
    ms = [m for m in (body.get("milestones") or []) if isinstance(m, dict)]
    exact = [m for m in ms if ((m.get("details") or {}).get(
        "main_game_event_ticker") == event_ticker or event_ticker in (
        m.get("primary_event_tickers") or []))]
    if len(exact) > 1:
        return None, "MILESTONE_AMBIGUOUS"
    return (exact[0] if exact else None), (None if exact else
                                           "MILESTONE_NOT_FOUND")


def freshness(books: dict, tickers, *, now: float,
              sla_s: float = BOOK_SLA_S) -> dict:
    """KALSHI freshness: tracked books current inside the SLA, over all
    tracked tickers (numerator / denominator, never blended with PMUS)."""
    tick = sorted(set(tickers))
    cur = [t for t in tick if (books.get(t) or {}).get("readable")
           and now - float((books.get(t) or {}).get("observed_at") or 0)
           <= sla_s]
    return {"domain": HEALTH_DOMAIN, "numerator": len(cur),
            "denominator": len(tick),
            "rate": round(len(cur) / len(tick), 4) if tick else None,
            "sla_s": sla_s}


def describe() -> dict:
    return {"version": VERSION, "domain": HEALTH_DOMAIN, "base": BASE,
            "method": "GET", "credentials": "NONE", "orders": False,
            "orderbook_protocol": ORDERBOOK_PROTOCOL,
            "leagues": sorted(LEAGUE_SPORT)}
