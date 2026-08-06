"""Cell-level copy policy: whale x sport x market-type x entry band.

Derived 2026-08-06 from four fill-level forensic reconstructions under
strict statistical rules — a cell is copyable only if the donor's ROI in
it is >= +1.5% (survives our ~86s latency decay), carries >= $1M donor
volume, and the MECHANISM is directional forecasting (market-making
hedge legs are not signals):

  kch123        basketball spreads (+22.2% / $21.8M), basketball totals
                (+10.7% / $16.0M), football spreads (+31.8% / $5.7M),
                hockey moneyline (+6.6% / $45.3M)
  HomeRunHazard baseball totals (+1.89% / $20.3M), WNBA totals (+6.50%),
                WNBA moneyline (+3.62%); entry band 50-95c ONLY — every
                sub-50c band in his directional book loses (-6.8% to
                -100%), and >95c fails the $1M sample floor
  swisstony     soccer moneyline (+3.3% / $360M), soccer spreads
                (+3.5% / $99.2M); exact-score (+0.7%) fails the bar
  RN1           NOTHING. His own forensic verdict: >100% of profit is
                matched-pair spread capture (+3.64%); the copyable
                directional residual is -1.07%. "Any attempt to copy
                RN1's picks would copy the losing half of the account."
                He stays in the probe/paper cohort so our filtered
                variant keeps being measured for free.

Kalshi carve-out: at 30-70c the taker fee (7%*p*(1-p), peak 1.75%)
consumes a thin donor edge, so thin-edge cells (swisstony soccer, HRH
baseball) only route to Kalshi at >= 70c; Polymarket (zero fee) takes
everything else.

Exclusive assignment is deliberate: it prevents two whales walking us
onto opposite sides of one game through different assets. Everything
fails closed: unknown whale, league, market type, or price copies
nothing.
"""

from __future__ import annotations

_KINDS = {"atc", "aec", "asc", "tsc", "astatc", "cpc"}

SPORT_OF = {"nba": "basketball", "cbb": "basketball",
            "nfl": "football", "cfb": "football",
            "nhl": "hockey", "mlb": "baseball", "wnba": "wnba",
            "atp": "tennis", "wta": "tennis", "itf": "tennis"}

# whale -> allowed (sport, market_type) cells. Fail-closed.
CELLS: dict[str, frozenset] = {
    "kch123": frozenset({("basketball", "spread"), ("basketball", "total"),
                         ("football", "spread"), ("hockey", "moneyline")}),
    "homerunhazard": frozenset({("baseball", "total"), ("wnba", "total"),
                                ("wnba", "moneyline")}),
    "swisstony": frozenset({("soccer", "moneyline"), ("soccer", "spread")}),
    "rn1": frozenset(),
}

# whale -> (entry floor, entry ceiling) on HIS price, dollars.
ENTRY_BAND: dict[str, tuple[float, float]] = {
    "homerunhazard": (0.50, 0.95),
}

# (whale, sport) cells whose donor edge is too thin to pay Kalshi's
# mid-price taker fee: Kalshi entries only at/above this ask.
KALSHI_MIN_ASK: dict[tuple[str, str], float] = {
    ("swisstony", "soccer"): 0.70,
    ("homerunhazard", "baseball"): 0.70,
}


def market_type_of(slug: str) -> str:
    """Market type from the slug's kind prefix (and derivative tokens):
    moneyline (atc/aec), spread (asc), total (tsc), and astatc derivatives
    split into btts ('ftts' token), exact_score ('es' segment) and prop.
    Kindless slugs are venue moneyline events. Fails to 'unknown', never
    silently to a tradeable type."""
    s = (slug or "").lower()
    parts = [p for p in s.split("-") if p]
    if not parts:
        return "unknown"
    kind = parts[0]
    if kind in ("atc", "aec"):
        return "moneyline"
    if kind == "asc":
        return "spread"
    if kind == "tsc":
        return "total"
    if kind == "cpc":
        return "crypto"
    if kind == "astatc":
        if "ftts" in parts:
            return "btts"
        if "es" in parts:
            return "exact_score"
        return "prop"
    if kind in _KINDS:
        return "unknown"
    # Kindless feed slugs ({league}-...) are event moneylines.
    return "moneyline"


def league_of(slug: str) -> str:
    parts = [p for p in (slug or "").lower().split("-") if p]
    if not parts:
        return ""
    if parts[0] in _KINDS and len(parts) > 1:
        return parts[1]
    return parts[0]


def sport_of(slug: str) -> str:
    """Sport bucket for a slug's league. Leagues outside the named US
    majors and tennis are the soccer/other bucket (the donor data cannot
    distinguish them further)."""
    lg = league_of(slug)
    if not lg:
        return ""
    return SPORT_OF.get(lg, "soccer")


def copy_allowed(whale: str, slug: str, price: float | None = None) -> bool:
    """May `whale`'s position in `slug` be copied at his `price`?
    Cell-level gate; fails closed on anything unrecognized."""
    w = (whale or "").strip().lower()
    if not w:
        return False
    cells = CELLS.get(w)
    if not cells:
        return False
    sport = sport_of(slug)
    if not sport:
        return False
    if (sport, market_type_of(slug)) not in cells:
        return False
    if price is not None:
        band = ENTRY_BAND.get(w)
        if band is not None and not (band[0] <= float(price) <= band[1]):
            return False
    return True


def kalshi_min_ask(whale: str, slug: str) -> float:
    """Minimum Kalshi ask for this cell (fee-viability carve-out);
    0.0 = no extra constraint."""
    return KALSHI_MIN_ASK.get(((whale or "").lower(), sport_of(slug)), 0.0)
