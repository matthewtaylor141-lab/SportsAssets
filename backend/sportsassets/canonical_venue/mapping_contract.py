from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class FixtureIdentity:
    sport: str
    league: str
    home_key: str
    away_key: str
    start_epoch: float

@dataclass(frozen=True)
class VenueFixture:
    venue: str
    sport: str | None
    league: str | None
    home_key: str | None
    away_key: str | None
    start_epoch: float | None
    structured_basis: str | None

@dataclass(frozen=True)
class MappingDecision:
    status: str
    reasons: tuple[str,...]

def map_fixture_exact(
    canonical: FixtureIdentity,
    candidate: VenueFixture,
    *,
    start_tolerance_s: float=15*60,
) -> MappingDecision:
    """Highest-standard fixture mapping.

    Titles are deliberately absent. Team keys are expected to be league-
    namespaced upstream (e.g. MLB:NYY, WNBA:NYL), preventing city-name
    collisions across leagues.
    """
    r=[]
    if not candidate.structured_basis:
        r.append("STRUCTURED_BASIS_MISSING")
    for k in ("sport","league","home_key","away_key","start_epoch"):
        if getattr(candidate,k) is None:
            r.append("MISSING_"+k.upper())
    if r:
        return MappingDecision("NOT_ESTABLISHED",tuple(r))
    if candidate.sport != canonical.sport:
        r.append("SPORT_MISMATCH")
    if candidate.league != canonical.league:
        r.append("LEAGUE_MISMATCH")
    if candidate.home_key != canonical.home_key:
        r.append("HOME_TEAM_MISMATCH")
    if candidate.away_key != canonical.away_key:
        r.append("AWAY_TEAM_MISMATCH")
    if abs(float(candidate.start_epoch)-float(canonical.start_epoch)) > float(start_tolerance_s):
        r.append("START_TIME_MISMATCH")
    return MappingDecision("ESTABLISHED" if not r else "NOT_ESTABLISHED",tuple(r))

@dataclass(frozen=True)
class MarketIdentity:
    family: str
    period: str
    line: Decimal | None
    subject_key: str | None

def map_market_exact(canonical: MarketIdentity, candidate: MarketIdentity) -> MappingDecision:
    r=[]
    if candidate.family != canonical.family: r.append("FAMILY_MISMATCH")
    if candidate.period != canonical.period: r.append("PERIOD_MISMATCH")
    if candidate.line != canonical.line: r.append("LINE_MISMATCH")
    if candidate.subject_key != canonical.subject_key: r.append("SUBJECT_MISMATCH")
    return MappingDecision("ESTABLISHED" if not r else "NOT_ESTABLISHED",tuple(r))
