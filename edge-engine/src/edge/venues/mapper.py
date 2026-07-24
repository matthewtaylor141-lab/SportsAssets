"""Feed event -> venue market mapping. The hardest unglamorous problem
(build spec step 3): fuzzy team-name matching between the odds feed and the
venue's market titles, seeded by the slug-prefix league map.

Contract: map only when confident (score >= MIN_SCORE and league matches);
report the match rate — the spec requires >95% on allowlist leagues before
shadow results count.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

MIN_SCORE = 0.85        # candidate floor: below this a market isn't even considered
TRADEABLE_SCORE = 0.95  # hard rule: <0.95 confidence is UNMAPPED and untradeable

_NOISE = re.compile(
    r"\b(fc|cf|sc|afc|cd|ac|as|ss|club|de|the|los|las|el|city|town|united|utd)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_VS = re.compile(r"\s+vs\.?\s+|\s+@\s+|\s+at\s+", re.IGNORECASE)


def norm_team(name: str) -> str:
    """Canonical team token string: lowercase, deaccent, strip suffix noise."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = _NON_ALNUM.sub(" ", s.lower())
    s = _NOISE.sub(" ", s)
    return " ".join(sorted(s.split()))


def team_score(a: str, b: str) -> float:
    """Similarity of two team names on normalized token strings."""
    na, nb = norm_team(a), norm_team(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Token containment (e.g. "arsenal" vs "arsenal london") scores high.
    ta, tb = set(na.split()), set(nb.split())
    if ta and (ta <= tb or tb <= ta):
        return 0.95
    return SequenceMatcher(None, na, nb).ratio()


@dataclass
class VenueMarket:
    market_id: str            # conditionId
    title: str                # e.g. "Arsenal vs. Chelsea"
    league_code: str | None   # slug prefix
    outcome_tokens: dict[str, str]  # outcome name -> token id


@dataclass
class MarketMatch:
    market: VenueMarket
    score: float
    home_outcome: str | None
    away_outcome: str | None

    @property
    def tradeable(self) -> bool:
        """Only >=0.95-confidence matches may carry orders (hard rule);
        0.85-0.95 matches are logged for the match-rate report but UNMAPPED
        for trading purposes."""
        return self.score >= TRADEABLE_SCORE


def parse_matchup(title: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in _VS.split(title or "") if p.strip()]
    if len(parts) != 2:
        return None
    # Trim trailing qualifiers ("Chelsea: winner" etc.)
    return parts[0], re.split(r"[:—]", parts[1])[0].strip()


def match_events_all(
    home: str, away: str, league_code: str | None, candidates: list[VenueMarket]
) -> list[MarketMatch]:
    """ALL venue markets for one real-world event (a game can have separate
    moneyline / spread / total listings, e.g. Kalshi series). Sorted best
    first; each match still carries its own tradeable flag."""
    out: list[MarketMatch] = []
    for mkt in candidates:
        if league_code and mkt.league_code and mkt.league_code != league_code:
            continue
        pair = parse_matchup(mkt.title)
        if not pair:
            continue
        a, b = pair
        straight = min(team_score(home, a), team_score(away, b))
        flipped = min(team_score(home, b), team_score(away, a))
        score = max(straight, flipped)
        if score < MIN_SCORE:
            continue
        home_oc = away_oc = None
        for oc_name in mkt.outcome_tokens:
            if team_score(home, oc_name) >= MIN_SCORE:
                home_oc = oc_name
            elif team_score(away, oc_name) >= MIN_SCORE:
                away_oc = oc_name
        out.append(MarketMatch(market=mkt, score=score,
                               home_outcome=home_oc, away_outcome=away_oc))
    return sorted(out, key=lambda m: -m.score)


def match_event(
    home: str, away: str, league_code: str | None, candidates: list[VenueMarket]
) -> MarketMatch | None:
    """Best venue market for a feed event; None below confidence floor."""
    best: MarketMatch | None = None
    for mkt in candidates:
        if league_code and mkt.league_code and mkt.league_code != league_code:
            continue
        pair = parse_matchup(mkt.title)
        if not pair:
            continue
        a, b = pair
        straight = min(team_score(home, a), team_score(away, b))
        flipped = min(team_score(home, b), team_score(away, a))
        score = max(straight, flipped)
        if score < MIN_SCORE:
            continue
        # Map outcome names to home/away by best per-outcome similarity.
        home_oc = away_oc = None
        for oc_name in mkt.outcome_tokens:
            if team_score(home, oc_name) >= MIN_SCORE:
                home_oc = oc_name
            elif team_score(away, oc_name) >= MIN_SCORE:
                away_oc = oc_name
        if best is None or score > best.score:
            best = MarketMatch(market=mkt, score=score, home_outcome=home_oc, away_outcome=away_oc)
    return best
