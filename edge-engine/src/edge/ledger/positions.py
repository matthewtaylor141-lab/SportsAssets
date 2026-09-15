"""Average-cost position ledger — port of the audited pipeline (do not rewrite).

Semantics (identical to the Phase 1 study and the SportsAssets platform):
  BUY:  position grows at cost; average re-weights.
  SELL: realizes (price - avg_cost) * size on the sale date; oversells clamp.
  RESOLVE: remaining shares realize (payout - avg_cost) * size on settle date.
Open unresolved inventory is excluded from realized P&L, never zeroed.
"""

from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-9


@dataclass
class Fill:
    side: str  # BUY / SELL
    size: float
    price: float
    ts: float = 0.0


@dataclass
class Position:
    shares: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    cost_in: float = 0.0
    resolved: bool = False

    def apply(self, fill: Fill) -> float:
        """Apply one fill; returns the realization amount (0 for buys)."""
        if self.resolved:
            return 0.0
        if fill.side == "BUY":
            total = self.shares * self.avg_cost + fill.size * fill.price
            self.shares += fill.size
            self.avg_cost = total / self.shares if self.shares > EPS else 0.0
            self.cost_in += fill.size * fill.price
            return 0.0
        sold = min(fill.size, self.shares)
        if sold <= EPS:
            return 0.0
        pnl = sold * (fill.price - self.avg_cost)
        self.realized_pnl += pnl
        self.shares -= sold
        if self.shares <= EPS:
            self.shares, self.avg_cost = 0.0, 0.0
        return pnl

    def resolve(self, payout: float) -> float:
        """Settle remaining shares at payout (1.0 or 0.0); returns realization."""
        if self.resolved:
            return 0.0
        pnl = 0.0
        if self.shares > EPS:
            pnl = self.shares * (payout - self.avg_cost)
            self.realized_pnl += pnl
            self.shares, self.avg_cost = 0.0, 0.0
        self.resolved = True
        return pnl
