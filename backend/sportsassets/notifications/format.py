"""Notification text formatting. Pure functions.

Single-trade example:
  "beachboy4 BUY Chiefs ML @ 61¢ — $84,000 — Chiefs vs. Bills"
Collapsed-burst example:
  "beachboy4 placed 12 trades on Chiefs vs. Bills (net +$310K on Chiefs)"
"""

from __future__ import annotations


def fmt_usd(amount: float) -> str:
    a = abs(amount)
    sign = "-" if amount < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 1_000:
        return f"{sign}${a / 1_000:.0f}K"
    return f"{sign}${a:,.0f}"


def fmt_signed_usd(amount: float) -> str:
    s = fmt_usd(amount)
    return s if s.startswith("-") else f"+{s}"


def trade_title(trade: dict) -> str:
    whale = trade.get("whale_username") or f"whale#{trade.get('whale_id')}"
    side = trade.get("side", "?")
    outcome = trade.get("outcome") or "position"
    price = trade.get("price")
    cents = f" @ {round(float(price) * 100)}¢" if price is not None else ""
    return f"{whale} {side} {outcome}{cents}"


def trade_body(trade: dict) -> str:
    notional = float(trade.get("notional") or 0)
    market = trade.get("event_title") or trade.get("market_title") or "market pending…"
    return f"${notional:,.0f} — {market}"


def burst_summary(whale_username: str, trades: list[dict]) -> str:
    """Collapse a burst from one whale into a single line."""
    count = len(trades)
    # Dominant event by gross notional.
    events: dict[str, float] = {}
    net_by_outcome: dict[str, float] = {}
    for t in trades:
        ev = t.get("event_title") or t.get("market_title") or "multiple markets"
        notional = float(t.get("notional") or 0)
        events[ev] = events.get(ev, 0.0) + notional
        outcome = t.get("outcome") or "?"
        signed = notional if t.get("side") == "BUY" else -notional
        net_by_outcome[outcome] = net_by_outcome.get(outcome, 0.0) + signed
    top_event = max(events, key=events.get) if events else "multiple markets"
    scope = top_event if len(events) == 1 else f"{top_event} (+{len(events) - 1} more)"
    if net_by_outcome:
        top_outcome = max(net_by_outcome, key=lambda k: abs(net_by_outcome[k]))
        net = net_by_outcome[top_outcome]
        return (
            f"{whale_username} placed {count} trades on {scope} "
            f"(net {fmt_signed_usd(net)} on {top_outcome})"
        )
    return f"{whale_username} placed {count} trades on {scope}"
