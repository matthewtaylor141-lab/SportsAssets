"""Translate prediction-market positions into sportsbook language.

The execution team reads bets, not token trades:
  - BUY "Yankees" in "Yankees vs. Red Sox"  → "Yankees ML"
  - BUY "No" on the Yankees side of a game   → "Red Sox ML" (the equivalent bet)
  - BUY "Over" in a totals market            → "Over 8.5"
  - outcome "Chiefs -3.5"                    → "Chiefs -3.5" (spread, as-is)
  - "Will the Yankees win the World Series?" → "Yankees win the World Series"
Prices become American odds: 0.61 → -156, 0.30 → +233.

Pure functions; unit-tested.
"""

from __future__ import annotations

import re

_VS = re.compile(r"\s+vs\.?\s+|\s+@\s+", re.IGNORECASE)
_SIGNED_NUM = re.compile(r"[+-]\d+(?:\.\d+)?")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def american_odds(price: float | None) -> str:
    """Implied-probability price (0..1) → American odds string."""
    if price is None or price <= 0 or price >= 1:
        return "—"
    if price >= 0.5:
        return f"-{round(price / (1 - price) * 100)}"
    return f"+{round((1 - price) / price * 100)}"


def _clean_prop(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"^will\s+(the\s+)?", "", t, flags=re.IGNORECASE)
    return t.rstrip("?").strip()


def opponent_of(team: str, event_title: str | None) -> str | None:
    """'Yankees' in 'Yankees vs. Red Sox' → 'Red Sox' (either direction)."""
    if not team or not event_title:
        return None
    parts = [p.strip() for p in _VS.split(event_title) if p.strip()]
    if len(parts) != 2:
        return None
    a, b = parts
    tl = team.lower()
    if tl in a.lower() or a.lower() in tl:
        return b
    if tl in b.lower() or b.lower() in tl:
        return a
    return None


def _total_line(title: str) -> str | None:
    """Extract the O/U line number from a totals-style market title."""
    m = list(_NUM.finditer(title or ""))
    return m[-1].group() if m else None


def bet_label(outcome: str | None, market_title: str | None, event_title: str | None = None) -> str:
    """Sportsbook-style description of holding this outcome token."""
    o = (outcome or "").strip()
    low = o.lower()
    title = (market_title or "").strip()

    if low in ("over", "under"):
        line = _total_line(title)
        return f"{o} {line}" if line else o

    if low == "yes":
        return _clean_prop(title) or "Yes"

    if low == "no":
        # NO on a team's game market = the equivalent bet on the opponent.
        # The proposition's SUBJECT is the team mentioned earliest in it
        # ("Will the Yankees beat the Red Sox?" → subject Yankees → bet Red Sox).
        prop = _clean_prop(title)
        pl = prop.lower()
        for source in (event_title, title):
            parts = [p.strip() for p in _VS.split(source or "") if p.strip()]
            if len(parts) == 2:
                # Strip prefixes like "Chiefs vs. Bills: Chiefs to win" from side b.
                a, b = parts[0], re.split(r"[:—-]", parts[1])[0].strip()
                ia = pl.find(a.lower())
                ib = pl.find(b.lower())
                if ia >= 0 and (ib < 0 or ia < ib):
                    return f"{b} ML"
                if ib >= 0 and (ia < 0 or ib < ia):
                    return f"{a} ML"
        return f"Against: {prop}" if prop else "No"

    # Outcome is a team/player/side name.
    if _SIGNED_NUM.search(o):
        return o  # spread baked into the outcome, e.g. "Chiefs -3.5"
    if opponent_of(o, event_title):
        return f"{o} ML"
    if re.search(r"\bwin\b|\bbeat\b|champion|series|cup|title|mvp", title, re.IGNORECASE):
        return f"{o} — {_clean_prop(title)}"
    return o or _clean_prop(title)


def bet_type(outcome: str | None, market_title: str | None, event_title: str | None = None) -> str:
    """Bet-type bucket for per-type P&L analysis. Categories and patterns
    follow the audited reference methodology: Exact Score / Spread / Total /
    Futures / Moneyline / Prop."""
    o = (outcome or "").strip()
    low = o.lower()
    title = (market_title or "").strip()
    t = title.lower()
    if "exact score" in t:
        return "Exact Score"
    if low in ("over", "under") or re.search(
        r"\bo/u\b|over/under|total (goals|points|runs)|\btotal\b", t
    ):
        return "Total"
    if _SIGNED_NUM.search(o) or re.search(r"\bspread\b|\bhandicap\b|\(\s*[-+]\d+(\.\d+)?\s*\)", t):
        return "Spread"
    if re.search(
        r"win the|champion|winner of the|to win .*(cup|league|title|series|tournament)|\bmvp\b", t
    ) and not opponent_of(o, event_title):
        return "Futures"
    if low in ("yes", "no"):
        # Yes/No on a specific game reads as ML-equivalent; otherwise a prop.
        parts = [p for p in _VS.split(event_title or title) if p.strip()]
        if len(parts) == 2:
            return "Moneyline"
        return "Prop"
    if opponent_of(o, event_title):
        return "Moneyline"
    if re.search(
        r"both teams|first (goal|touchdown|basket)|scorer|cards|corners|assists", t
    ):
        return "Prop"
    if re.search(r"\bwin\b|\bbeat\b|series|cup|title", t):
        return "Futures"
    return "Moneyline" if len([p for p in _VS.split(title) if p.strip()]) == 2 else "Prop"


def result_word(pnl: float, resolved: bool, tolerance: float = 0.01) -> str:
    if pnl > tolerance:
        return "Win" if resolved else "Cash-out (profit)"
    if pnl < -tolerance:
        return "Loss" if resolved else "Cash-out (loss)"
    return "Push"
