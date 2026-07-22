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


class FeedClient(ABC):
    @abstractmethod
    def fetch_events(self, sport_key: str) -> list[FeedEvent]: ...


class TheOddsAPIClient(FeedClient):
    BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ.get("EDGE_ODDS_API_KEY", "")
        if not self._key:
            log.warning("EDGE_ODDS_API_KEY not set — feed calls will fail")
        self._sess = requests.Session()

    def fetch_events(self, sport_key: str) -> list[FeedEvent]:
        resp = self._sess.get(
            f"{self.BASE}/sports/{sport_key}/odds",
            params={"apiKey": self._key, "regions": "eu",
                    "markets": "h2h,totals,spreads", "oddsFormat": "decimal"},
            timeout=20,
        )
        resp.raise_for_status()
        out: list[FeedEvent] = []
        for raw in resp.json():
            ev = FeedEvent(
                sport_key=sport_key,
                league_code=SPORT_KEY_LEAGUE.get(sport_key, sport_key),
                home=raw.get("home_team", ""),
                away=raw.get("away_team", ""),
                commence_ts=_iso_ts(raw.get("commence_time")),
            )
            # Consensus across ALL sharp books: median decimal odds per outcome.
            samples: dict[str, dict[str, list[float]]] = {"h2h": {}, "totals": {}, "spreads": {}}
            for book in raw.get("bookmakers", []):
                if book.get("key") not in SHARP_BOOKS:
                    continue
                for mkt in book.get("markets", []):
                    if mkt["key"] not in samples:
                        continue
                    for oc in mkt.get("outcomes", []):
                        name, price = oc.get("name", ""), float(oc.get("price", 0) or 0)
                        if price <= 1.0:
                            continue
                        key = name if mkt["key"] == "h2h" else f"{name} {oc.get('point')}"
                        samples[mkt["key"]].setdefault(key, []).append(price)
            ev.h2h = {k: _median(v) for k, v in samples["h2h"].items()}
            ev.totals = {k: _median(v) for k, v in samples["totals"].items()}
            ev.spreads = {k: _median(v) for k, v in samples["spreads"].items()}
            if ev.h2h:
                out.append(ev)
        return out


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
