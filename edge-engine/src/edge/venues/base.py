"""Venue adapter contract. Both Polymarket and Kalshi implement this so the
execution engine and shadow runner are venue-agnostic. Fees and order
semantics live in the adapter, never in strategy code."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class BookLevel:
    price: float
    size: float


@dataclass
class MarketBook:
    venue: str
    market_id: str          # conditionId (PM) / ticker (Kalshi)
    outcome_id: str         # token id (PM) / yes|no side (Kalshi)
    bids: list[BookLevel]
    asks: list[BookLevel]
    ts: float


@dataclass
class FillIntent:
    """What the engine wants; the adapter decides maker/taker mechanics."""
    market_id: str
    outcome_id: str
    limit_price: float
    size_usd: float
    fair_value: float
    edge: float             # fair_value - limit_price, pre-fee
    league: str
    band: str


class VenueAdapter(ABC):
    name: str

    @abstractmethod
    async def subscribe_books(self, market_ids: list[str]):
        """Yield MarketBook updates."""

    @abstractmethod
    def taker_fee(self, price: float) -> float:
        """Fee in probability units per $1 of contracts at this price.
        Polymarket: 0.0. Kalshi: 0.07 * p * (1-p)."""

    @abstractmethod
    async def place(self, intent: FillIntent):
        """Live mode only. Shadow mode never calls this."""

    @abstractmethod
    async def settlements(self):
        """Yield (market_id, winning_outcome_ids) at resolution."""

    def maker_fee(self, price: float) -> float:
        """Fee for a RESTING order. Both venues price maker liquidity at or
        below taker; adapters that charge differently override this."""
        return 0.0

    def plan_entry(self, book: MarketBook) -> tuple[float, bool]:
        """(entry_price, taker) — the price this venue would actually enter
        at, given the book. The default is to cross the spread and pay the
        ask; adapters that can rest an order override this to quote a better
        price. The engine evaluates its threshold at whatever comes back, so
        a venue that prices better also qualifies more opportunities."""
        return (book.asks[0].price, True) if book.asks else (0.0, True)

    def net_edge(self, intent: FillIntent, taker: bool) -> float:
        return intent.edge - (self.taker_fee(intent.limit_price) if taker
                              else self.maker_fee(intent.limit_price))
