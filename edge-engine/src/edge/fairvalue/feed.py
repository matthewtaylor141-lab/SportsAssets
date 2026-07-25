"""Odds feed client. Licensed feed only (never scraped) — The Odds API tier
by default; the FeedClient ABC keeps OpticOdds/Sportradar swappable.

Normalized output per event:
  FeedEvent(sport_key, league_code, home, away, commence_ts,
            h2h={outcome_name: decimal_odds}, totals=..., spreads=...)
Only sharp books (config) contribute; fair value comes from de-vig downstream.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

SHARP_BOOKS = {"pinnacle", "betfair_ex_eu", "betfair_ex_uk", "smarkets"}

# The Odds API sport keys -> our league slug codes (subset; extend as mapped).
SPORT_KEY_LEAGUE = {
    "soccer_epl": "epl", "soccer_spain_la_liga": "lal", "soccer_france_ligue_one": "fl1",
    "soccer_uefa_europa_league": "uel", "soccer_brazil_campeonato": "bra",
    "soccer_fifa_world_cup": "fifwc", "soccer_germany_bundesliga2": "bl2",
    "soccer_spl": "spl", "soccer_italy_serie_a": "sea", "soccer_argentina_primera_division": "arg",
    "soccer_mexico_ligamx": "mex", "soccer_japan_j_league": "j1100",
    "basketball_nba": "nba", "basketball_wnba": "wnba", "basketball_ncaab": "cbb",
    "tennis_wta": "wta",
    # US majors for the derivative (spread/total) edge — kch123 evidence.
    "icehockey_nhl": "nhl", "americanfootball_nfl": "nfl",
}


@dataclass
class FeedEvent:
    sport_key: str
    league_code: str
    home: str
    away: str
    commence_ts: float
    h2h: dict[str, float] = field(default_factory=dict)      # name -> decimal odds
    totals: dict[str, float] = field(default_factory=dict)   # "Over 2.5" -> odds
    spreads: dict[str, float] = field(default_factory=dict)  # "Home -1.5" -> odds
    fetched_at: float = 0.0   # staleness stamp — set at fetch, checked pre-order

    def is_fresh(self, max_age_s: float = 30.0, now: float | None = None) -> bool:
        """Hard rule: no order without a quote fresher than max_age_s."""
        return (now or time.time()) - self.fetched_at <= max_age_s

    def event_key(self) -> str:
        """Venue-agnostic identity of the real-world event (one-per-event cap)."""
        import hashlib

        from edge.venues.mapper import norm_team

        day = int(self.commence_ts // 86400) if self.commence_ts else 0
        raw = f"{self.league_code}|{norm_team(self.home)}|{norm_team(self.away)}|{day}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


class OddsFeed(ABC):
    """Abstract odds feed (build step 3). Licensed API access only — no
    scraping. Implementations: TheOddsAPIClient (live), OpticOddsFeed (stub)."""

    @abstractmethod
    def fetch_events(self, sport_key: str) -> list[FeedEvent]: ...

    def quota(self) -> dict:
        """Remaining request budget, if the provider reports one."""
        return {}


FeedClient = OddsFeed  # back-compat alias for existing imports


class TheOddsAPIClient(OddsFeed):
    def resolve_sport_keys(self) -> list[str]:
        """Live sport keys from the feed intersected with our league map.
        The Odds API uses per-tournament tennis keys (tennis_wta_*), so a
        hardcoded list goes stale — resolve from /sports each run."""
        try:
            resp = self._sess.get(f"{self.BASE}/sports", params={"apiKey": self._key}, timeout=15)
            resp.raise_for_status()
            keys = []
            for s in resp.json():
                k = s.get("key", "")
                if not s.get("active"):
                    continue
                if k in SPORT_KEY_LEAGUE:
                    keys.append(k)
                elif k.startswith("tennis_wta"):
                    keys.append(k)  # maps to league 'wta' (allowlisted)
                # tennis_atp_* deliberately skipped: blocklisted (measured flat)
            if keys:
                return keys
        except (requests.RequestException, ValueError) as exc:
            log.warning("sport-key discovery failed (%s); using static map", exc)
        return list(SPORT_KEY_LEAGUE)

    @staticmethod
    def league_of(sport_key: str) -> str:
        if sport_key in SPORT_KEY_LEAGUE:
            return SPORT_KEY_LEAGUE[sport_key]
        if sport_key.startswith("tennis_wta"):
            return "wta"
        return sport_key

    BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str | None = None, cache_ttl_s: float = 25.0,
                 quota_reserve: int = 50) -> None:
        self._key = api_key or os.environ.get("EDGE_ODDS_API_KEY", "")
        if not self._key:
            log.warning("EDGE_ODDS_API_KEY not set — feed calls will fail")
        self._sess = requests.Session()
        # Rate budgeting: short TTL cache absorbs intra-cycle re-fetches, and
        # a quota reserve floor stops us from burning the last N credits —
        # stale-but-served beats exhausted-and-blind.
        self._cache: dict[str, tuple[float, list[FeedEvent]]] = {}
        self._cache_ttl = cache_ttl_s
        self._quota_reserve = quota_reserve
        self._quota: dict[str, float] = {}
        self._server_skew_s: float | None = None  # local - server clock, from Date header
        # sport_key -> has live/imminent games (drives per-sport TTL)
        self._sport_active: dict[str, bool] = {}
        # sports whose alternate-line request the provider rejects (422)
        self._no_alt_lines: set[str] = set()

    def quota(self) -> dict:
        return dict(self._quota)

    def server_clock_skew_s(self) -> float | None:
        """|local - feed server| seconds, from the last response's Date header
        (watchdog + check-live input)."""
        return self._server_skew_s

    def _track_response(self, resp) -> None:
        for header, key in (("x-requests-remaining", "remaining"),
                            ("x-requests-used", "used")):
            val = resp.headers.get(header)
            if val is not None:
                try:
                    self._quota[key] = float(val)
                except ValueError:
                    pass
        date_hdr = resp.headers.get("date")
        if date_hdr:
            try:
                from email.utils import parsedate_to_datetime

                self._server_skew_s = time.time() - parsedate_to_datetime(date_hdr).timestamp()
            except (TypeError, ValueError):
                pass

    # Alternate spread/total lines are requested for EVERY sport: venues list
    # handicaps (soccer -2.5, etc.) that a book's standard line never quotes,
    # and without the alternate ladder those markets can never be priced at
    # all. Sports that reject the request are remembered and fall back.
    # EDGE_ALT_LINES=0 disables entirely.
    ALT_LINE_SPORTS: set[str] | None = None  # None = all sports

    # Below this many remaining credits, start conserving; above it, keep
    # every sport fresh (a slow TTL makes quotes fail the 30s freshness
    # rule, which silently disables trading on that sport).
    QUOTA_CONSERVE_BELOW = 50_000

    def _ttl_for(self, sport_key: str) -> float:
        """Fast TTL for everything while quota is plentiful. Only when the
        budget runs low do idle sports (no game within -6h..+4h) coast."""
        if self._quota.get("remaining", float("inf")) > self.QUOTA_CONSERVE_BELOW:
            return self._cache_ttl
        return self._cache_ttl if self._sport_active.get(sport_key, True) \
            else max(self._cache_ttl, 300.0)

    def fetch_events(self, sport_key: str) -> list[FeedEvent]:
        now = time.time()
        cached = self._cache.get(sport_key)
        if cached and now - cached[0] < self._ttl_for(sport_key):
            return cached[1]
        if self._quota.get("remaining", float("inf")) <= self._quota_reserve:
            log.warning("odds quota at reserve floor (%s left) — serving stale cache",
                        self._quota.get("remaining"))
            return cached[1] if cached else []
        markets = "h2h,totals,spreads"
        if (os.environ.get("EDGE_ALT_LINES", "1") != "0"
                and (self.ALT_LINE_SPORTS is None
                     or sport_key in self.ALT_LINE_SPORTS)
                and sport_key not in self._no_alt_lines):
            markets += ",alternate_spreads,alternate_totals"

        def _get(mkts: str):
            r = self._sess.get(
                f"{self.BASE}/sports/{sport_key}/odds",
                params={"apiKey": self._key, "regions": "eu",
                        "markets": mkts, "oddsFormat": "decimal"},
                timeout=20,
            )
            self._track_response(r)
            return r

        resp = _get(markets)
        if resp.status_code == 422 and "alternate" in markets:
            # Provider rejects alternate markets for this sport (commonly
            # out-of-season or plan-scoped). Remember and fall back to the
            # base markets rather than losing the sport entirely.
            log.info("%s: alternate lines unavailable (422) — base markets only",
                     sport_key)
            self._no_alt_lines.add(sport_key)
            resp = _get("h2h,totals,spreads")
        resp.raise_for_status()
        out: list[FeedEvent] = []
        for raw in resp.json():
            ev = FeedEvent(
                sport_key=sport_key,
                league_code=self.league_of(sport_key),
                home=raw.get("home_team", ""),
                away=raw.get("away_team", ""),
                commence_ts=_iso_ts(raw.get("commence_time")),
                fetched_at=now,
            )
            # Consensus across ALL sharp books: median decimal odds per outcome.
            samples: dict[str, dict[str, list[float]]] = {"h2h": {}, "totals": {}, "spreads": {}}
            # Alternate lines fold into the same buckets — downstream pairing
            # is point-exact, so extra lines just mean more pairable points.
            bucket_of = {"h2h": "h2h", "totals": "totals", "spreads": "spreads",
                         "alternate_totals": "totals", "alternate_spreads": "spreads"}
            for book in raw.get("bookmakers", []):
                if book.get("key") not in SHARP_BOOKS:
                    continue
                for mkt in book.get("markets", []):
                    bucket = bucket_of.get(mkt["key"])
                    if bucket is None:
                        continue
                    for oc in mkt.get("outcomes", []):
                        name, price = oc.get("name", ""), float(oc.get("price", 0) or 0)
                        if price <= 1.0:
                            continue
                        key = name if bucket == "h2h" else f"{name} {oc.get('point')}"
                        samples[bucket].setdefault(key, []).append(price)
            ev.h2h = {k: _median(v) for k, v in samples["h2h"].items()}
            ev.totals = {k: _median(v) for k, v in samples["totals"].items()}
            ev.spreads = {k: _median(v) for k, v in samples["spreads"].items()}
            if ev.h2h:
                out.append(ev)
        self._cache[sport_key] = (now, out)
        # Live/imminent window: any game from 6h ago (in-play) to 4h ahead.
        self._sport_active[sport_key] = any(
            now - 6 * 3600 < e.commence_ts < now + 4 * 3600 for e in out)
        return out


class OpticOddsFeed(OddsFeed):
    """Second-provider stub (build step 3). Kept as a compile-time interface
    check and a clear seam for a feed migration — not implemented in v1."""

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ.get("EDGE_OPTICODDS_API_KEY", "")

    def fetch_events(self, sport_key: str) -> list[FeedEvent]:
        raise NotImplementedError(
            "OpticOdds adapter is a v1 stub — TheOddsAPIClient is the live feed. "
            "Implement fetch_events against the OpticOdds REST API to activate."
        )


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _iso_ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()
