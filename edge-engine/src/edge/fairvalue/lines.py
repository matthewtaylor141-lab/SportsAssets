"""Derivative line handling (spreads / totals) — the kch123 extension.

Fair values come from de-vigging the sharp books' OWN two-sided quote for the
exact same line: "Over 8.5" is priced against "Under 8.5", "Chiefs -3.5"
against "Eagles +3.5". No model stands between the sharp line and the fair
value; Dixon-Coles remains a cross-check, not a source.

Hard rule encoded here: a line is only comparable at the EXACT same point.
A -7.5 quote never prices a -6.5 market; Over 8.5 never prices Over 9.5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from edge.venues.mapper import team_score

TOTAL_RE = re.compile(r"^\s*(over|under)\s*\(?([+-]?\d+(?:\.\d+)?)?\)?\s*$", re.IGNORECASE)
# Team spread: any text ending with a signed number, e.g. "Chiefs -3.5",
# "Kansas City Chiefs (+3.5)".
SPREAD_RE = re.compile(r"^(?P<team>.+?)\s*\(?(?P<point>[+-]\d+(?:\.\d+)?)\)?\s*$")


@dataclass(frozen=True)
class ParsedLine:
    kind: str            # 'moneyline' | 'total' | 'spread'
    team: str | None     # spread/moneyline: team text; total: None
    side: str | None     # total: 'over'|'under'
    point: float | None  # line value (spread signed, total unsigned)


def parse_outcome_line(name: str) -> ParsedLine:
    """Classify a feed or venue outcome string."""
    s = (name or "").strip()
    m = TOTAL_RE.match(s)
    if m:
        pt = float(m.group(2)) if m.group(2) is not None else None
        return ParsedLine("total", None, m.group(1).lower(), pt)
    m = SPREAD_RE.match(s)
    if m:
        return ParsedLine("spread", m.group("team").strip(), None,
                          float(m.group("point")))
    return ParsedLine("moneyline", s or None, None, None)


@dataclass
class LinePair:
    """One two-sided sharp quote at a single point."""

    kind: str                    # 'total' | 'spread'
    point: float                 # totals: the O/U number; spreads: |line|
    a_name: str                  # totals: 'Over X' side; spreads: minus side team
    b_name: str
    a_parsed: ParsedLine
    b_parsed: ParsedLine
    a_odds: float
    b_odds: float


def pair_quotes(quotes: dict[str, float], kind: str) -> list[LinePair]:
    """Match a feed market dict {outcome_name: decimal_odds} into two-sided
    pairs at identical points. Unpaired quotes are dropped — a one-sided
    line cannot be de-vigged and is untradeable."""
    parsed = {name: parse_outcome_line(name) for name in quotes}
    pairs: list[LinePair] = []
    if kind == "total":
        overs = {p.point: n for n, p in parsed.items()
                 if p.kind == "total" and p.side == "over" and p.point is not None}
        unders = {p.point: n for n, p in parsed.items()
                  if p.kind == "total" and p.side == "under" and p.point is not None}
        for pt in sorted(set(overs) & set(unders)):
            a, b = overs[pt], unders[pt]
            pairs.append(LinePair("total", pt, a, b, parsed[a], parsed[b],
                                  quotes[a], quotes[b]))
    elif kind == "spread":
        entries = [(n, p) for n, p in parsed.items()
                   if p.kind == "spread" and p.point is not None]
        used: set[str] = set()
        for i, (na, pa) in enumerate(entries):
            if na in used or pa.point >= 0:
                continue  # anchor on the minus side
            for nb, pb in entries[i + 1:] + entries[:i]:
                if nb in used or nb == na:
                    continue
                if abs(pb.point + pa.point) < 1e-9 and pb.point > 0:
                    pairs.append(LinePair("spread", abs(pa.point), na, nb,
                                          pa, pb, quotes[na], quotes[nb]))
                    used.update((na, nb))
                    break
    return pairs


def outcome_matches(venue_outcome: str, pair_side: ParsedLine) -> bool:
    """Does a venue outcome string represent this exact side of this exact
    line? Point must match to the decimal; teams need >=0.95 similarity."""
    v = parse_outcome_line(venue_outcome)
    if v.kind != pair_side.kind:
        return False
    if pair_side.kind == "total":
        if v.side != pair_side.side:
            return False
        # Venue outcomes are often just "Over"/"Under" with the point in the
        # market title; a point-less venue outcome matches (title was already
        # line-matched upstream), a stated point must match exactly.
        return v.point is None or pair_side.point is None or \
            abs(v.point - pair_side.point) < 1e-9
    if pair_side.kind == "spread":
        if v.point is None or pair_side.point is None or \
                abs(v.point - pair_side.point) > 1e-9:
            return False
        return team_score(v.team or "", pair_side.team or "") >= 0.95
    return False


_TITLE_TOTAL = re.compile(r"(?:o/u|over/under|total)", re.IGNORECASE)
_TITLE_SPREAD_TEAM = re.compile(r"spread[:\s]+(.+?)\s*\(", re.IGNORECASE)


def canonical_outcome(market_title: str, outcome_name: str) -> str:
    """Normalize a venue outcome to a self-describing string using the
    market title's line context (venues often put the point in the title:
    'Mets vs. Tigers: O/U 8.5' outcome 'Over'; 'Spread: Eagles (-7.5)'
    outcome 'Cowboys' — the named team gets the title's signed point, the
    OTHER team gets the mirrored point).
    Returns 'Over 8.5' / 'Eagles -7.5' style, or the outcome unchanged."""
    p = parse_outcome_line(outcome_name)
    pt = title_point(market_title)
    if p.kind == "total":
        point = p.point if p.point is not None else (abs(pt) if pt is not None else None)
        return f"{p.side.capitalize()} {point:g}" if point is not None \
            else outcome_name.strip()
    if p.kind == "spread":
        return f"{p.team} {p.point:+g}"
    # Plain team name: a spread-titled market makes it a spread side.
    if pt is not None and (_TITLE_SPREAD_TEAM.search(market_title or "")
                           or not _TITLE_TOTAL.search(market_title or "")):
        m = _TITLE_SPREAD_TEAM.search(market_title or "")
        if m is not None:
            titled_team = m.group(1)
            signed = pt if team_score(outcome_name, titled_team) >= 0.95 else -pt
            return f"{outcome_name.strip()} {signed:+g}"
    return outcome_name.strip()


def title_point(title: str) -> float | None:
    """Extract a line from a market title, e.g. 'Mets vs. Tigers: O/U 8.5'
    -> 8.5, 'Spread: Eagles (-7.5)' -> -7.5. None when absent."""
    m = re.search(r"(?:o/u|over/under|total)[:\s]*([+-]?\d+(?:\.\d+)?)",
                  title or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\(([+-]\d+(?:\.\d+)?)\)", title or "")
    if m:
        return float(m.group(1))
    return None
