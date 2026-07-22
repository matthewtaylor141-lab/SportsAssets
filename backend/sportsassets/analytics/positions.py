"""Position lifecycle math — pure, deterministic, average-cost.

Accounting method: AVERAGE COST (documented product decision).
  - BUY:  new_avg = (shares*avg + size*price) / (shares + size)
  - SELL: realized += min(size, shares) * (price - avg); shares -= sold
  - Resolution: remaining shares realize at payout (1.0 win / 0.0 loss);
    the position is then closed.

Sells beyond tracked inventory (history predates tracking, or transfers)
are clamped and counted in `untracked_sells` rather than fabricating
negative inventory — P&L is only claimed on shares we saw bought.

Win/Loss rule (per spec §5): a MARKET is a Win if the whale's total realized
P&L across all outcome tokens in that market is > 0, a Loss if < 0, a scratch
at 0. Never per-leg — hedged packages contain deliberately losing legs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EPS = 1e-9


@dataclass
class Fill:
    side: str  # BUY / SELL
    size: float
    price: float
    ts: int = 0


@dataclass
class Position:
    """One whale × one outcome token."""

    shares: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    notional_in: float = 0.0  # total USDC deployed into buys (ROI denominator input)
    untracked_sells: float = 0.0
    resolved: bool = False
    fills: int = 0

    def apply(self, fill: Fill) -> None:
        if self.resolved:
            return  # post-resolution fills shouldn't exist; ignore defensively
        self.fills += 1
        if fill.side == "BUY":
            total_cost = self.shares * self.avg_cost + fill.size * fill.price
            self.shares += fill.size
            self.avg_cost = total_cost / self.shares if self.shares > EPS else 0.0
            self.notional_in += fill.size * fill.price
        elif fill.side == "SELL":
            sold = min(fill.size, self.shares)
            if fill.size - sold > EPS:
                self.untracked_sells += fill.size - sold
            if sold > EPS:
                self.realized_pnl += sold * (fill.price - self.avg_cost)
                self.shares -= sold
                if self.shares <= EPS:
                    self.shares = 0.0
                    self.avg_cost = 0.0

    def resolve(self, payout: float) -> None:
        """Realize remaining shares at the resolution payout (1.0 or 0.0)."""
        if self.resolved:
            return
        if self.shares > EPS:
            self.realized_pnl += self.shares * (payout - self.avg_cost)
            self.shares = 0.0
            self.avg_cost = 0.0
        self.resolved = True

    @property
    def open_exposure(self) -> float:
        """Current capital at risk: remaining shares at average cost."""
        return self.shares * self.avg_cost


def build_position(fills: list[Fill], payout: float | None = None) -> Position:
    """Replay a fill sequence (chronological) into a Position; resolve if payout given."""
    pos = Position()
    for f in sorted(fills, key=lambda f: f.ts):
        pos.apply(f)
    if payout is not None:
        pos.resolve(payout)
    return pos


def market_result(realized_pnl_total: float, tolerance: float = 0.01) -> str:
    """Per-market W/L/scratch from summed realized P&L across the market's legs.

    tolerance (USDC) absorbs float dust so a perfectly-hedged package scores
    scratch instead of flapping between win/loss on 1e-12 residue.
    """
    if realized_pnl_total > tolerance:
        return "win"
    if realized_pnl_total < -tolerance:
        return "loss"
    return "scratch"
