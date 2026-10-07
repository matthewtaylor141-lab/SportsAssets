from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Callable

PROVEN = "PROVEN"
NOT_PROVEN = "NOT_PROVEN"

@dataclass(frozen=True)
class CanonicalEvent:
    event_key: str
    sport: str
    league: str
    start_time: str
    outcomes: tuple[str, ...]
    exhaustive: bool
    basis: str

@dataclass(frozen=True)
class VenueInstrument:
    venue: str
    instrument_id: str
    market_id: str
    side: str
    event_key: str
    family: str
    period: str
    subject: str
    payoff: Mapping[str, Decimal]
    rules_status: str
    mapping_status: str
    settlement_status: str
    book_is_explicit: bool = True
    orderable_id: str | None = None
    sport: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str,str,str]:
        return (self.venue, self.instrument_id, self.side)

    @property
    def execution_key(self) -> tuple[str,str]:
        return (self.venue, self.orderable_id or self.instrument_id)

@dataclass(frozen=True)
class Book:
    venue: str
    instrument_id: str
    side: str
    asks: tuple[tuple[Decimal, int], ...]
    observed_at: float
    max_age_s: float
    explicit: bool = True

    @property
    def key(self) -> tuple[str,str,str]:
        return (self.venue, self.instrument_id, self.side)

FeeFn = Callable[[int, Decimal], Decimal]
