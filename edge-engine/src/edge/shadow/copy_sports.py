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
  RN1           UNRESTRICTED (owner decision 2026-08-06 evening). The
                forensic verdict on his BOOK stands — >100% of profit is
                matched-pair spread capture (+3.64%), directional
                residual -1.07% — but neither that residual nor the
                all-fills paper cohort (-2.3%) measures the rule we
                actually run: first entry per market, $3 FOK at his
                price +2%. His first leg is the side the market tends
                to move toward while he completes the pair, and our own
                285-settled live record of exactly that rule is
                +$170.95 (163-122). Reinstated on that evidence, under
                watch: review trigger at -$60 from the sleeve's
                high-water mark.

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

# Whales copied WITHOUT a cell gate — the live sleeve's own record of
# the first-entry-per-market rule is the governing measurement (owner
# decision 2026-08-06; see the RN1 note above).
# 0x2c33…0563 promoted from vetting 2026-08-10 (owner approval): 1,712
# probes at our REAL latency graded +0.76% per-$1k residual ROI —
# strongest measured candidate on the board. Vetting measured his whole
# book, so he enters unrestricted like RN1 and earns cells (or removal)
# on his own settled record. Keyed by the roster's auto-generated
# username; the row is pinned (migration 019) so the weekly roster
# refresh can neither rename nor deactivate it out from under this key.
UNRESTRICTED = frozenset({
    "rn1",
    "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465",
})

# ALL soccer copying HALTED (owner order 2026-08-11 ~3:40pm ET: "halt
# all soccer trading until this issue is tested and resolved" — the
# PMUS same-market stacking incident). Checked before every other gate,
# both venues. NOTE: sport_of buckets every league outside the named US
# majors and tennis into 'soccer', so this also halts the misc bucket
# (table tennis, cricket, esports) — conservative and intended. NOTE
# ALSO: swisstony's cells are all soccer, so this halts him entirely
# until the owner lifts it.
HALTED_SPORTS = frozenset({"soccer"})

# Whales copied NOWHERE right now (owner directive 2026-08-11 during the
# profitability review): HomeRunHazard measured flat-negative on his own
# live record (33 fills, 10 settled, -$0.80) while his prop-heavy flow
# fed the losing prop cohort below. Pausing is reversible; his cells and
# band stay defined above so un-pausing is a one-line delete.
PAUSED = frozenset({"homerunhazard"})

# Market types copied for NOBODY — including UNRESTRICTED whales (owner
# directive 2026-08-11): live prop copies graded 3W/16L, -64% ROI.
# Blocked at the type level so the bleed cannot re-enter through any
# whale. btts/exact_score stay governed by the cell table.
BLOCKED_TYPES = frozenset({"prop"})

# whale -> allowed (sport, market_type) cells. Fail-closed.
CELLS: dict[str, frozenset] = {
    "kch123": frozenset({("basketball", "spread"), ("basketball", "total"),
                         ("football", "spread"), ("hockey", "moneyline")}),
    # Owner directive 2026-08-07 ("every whale trade copied as long as
    # edge metrics are met"): every EDGE-POSITIVE cell in his book is
    # on — the +1.5% bar relaxes to >0 donor ROI, with the 50-95c entry
    # band still guarding (his sub-50c entries lose account-wide) and
    # Kalshi fee floors covering the thin cells. Added: MLB moneyline
    # (+0.80%/$23.9M), tennis moneyline (ATP +1.07%/$24M, WTA
    # +1.12%/$19.4M — his scale sport), NBA spread (+2.76%) and total
    # (+1.27%), NHL total (+3.93%). Still OUT (edge not met): MLB
    # spread (+0.01% dead), ATP totals (-3.51%), CFL (unmeasured mix).
    "homerunhazard": frozenset({("baseball", "total"),
                                ("baseball", "moneyline"),
                                ("wnba", "total"), ("wnba", "moneyline"),
                                ("tennis", "moneyline"),
                                ("basketball", "spread"),
                                ("basketball", "total"),
                                ("hockey", "total")}),
    "swisstony": frozenset({("soccer", "moneyline"), ("soccer", "spread")}),
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
    # Tennis ~1.1% donor edge cannot pay the mid-price taker fee either.
    ("homerunhazard", "tennis"): 0.70,
}


import hashlib as _hashlib
import re as _re

# Venue split (owner directive 2026-08-07: "trades firing on both venues
# almost evenly, when it makes sense from a pricing standpoint"). The
# fast PMUS leg reacts in ~86s and wins the race for every fillable
# copy; the cross-venue one-copy rule then locks Kalshi out — Kalshi
# was structurally the leftover venue. A deterministic per-asset split
# gives Kalshi FIRST CLAIM on half the flow in the sports it actually
# lists; the engine's price gates (his+2% fee-loaded, fee floors,
# collapse guard) remain the "when it makes sense" bar, and anything
# Kalshi cannot price is reclaimed by the hourly PMUS sweep. Soccer
# stays PMUS-first: Kalshi's soccer coverage is thin and the 70c fee
# floor already routes most of it to the fee-free venue.
KALSHI_FIRST_SPORTS = frozenset({"baseball", "wnba", "basketball",
                                 "football", "hockey", "tennis"})


def kalshi_first(asset: str) -> bool:
    """Kalshi's first-claim share of fresh copy flow, deterministic by
    asset id — same answer on every service, so the two venues never
    race for one position.

    Default 50 (owner directive 2026-08-10 night: "both firing correct
    trades and copied edges immediately"). PMUS fires instantly on its
    half; the fresh-fill wake fires Kalshi in ~10-40s on the other; and
    the reclaim sweep (COPY_SWEEP_EVERY_S, now 2 minutes) cross-covers
    whatever the first venue refused, so neither leg ever waits long on
    the other. One copy per position stays enforced by the claims and
    the venue-side never-add veto. History: 100 on 2026-08-09, 50 on
    2026-08-10 morning, 100 that evening for Kalshi volume, 50 again
    the same night for both-immediate; KALSHI_FIRST_PCT overrides in
    one env change."""
    if not asset:
        return False
    import os as _os
    pct = int(_os.environ.get("KALSHI_FIRST_PCT", "50"))
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    return int(_hashlib.sha1(str(asset).encode()).hexdigest()[-2:],
               16) % 100 < pct


_DATE_RE = _re.compile(r"^\d{4}$")
_TOTAL_RE = _re.compile(r"^[ou]\d+(pt\d)?$")
_LINE_RE = _re.compile(r"^(pos|neg)?\d+(pt\d)?$")


def _post_date_tokens(parts: list[str]) -> list[str] | None:
    """Tokens after the YYYY-MM-DD triple, or None if no date found."""
    for i in range(len(parts) - 2):
        if (_DATE_RE.match(parts[i]) and parts[i + 1].isdigit()
                and parts[i + 2].isdigit()):
            return parts[i + 3:]
    return None


def market_type_of(slug: str) -> str:
    """Market type from either slug grammar.

    PMUS venue grammar keys on the kind prefix: atc/aec moneyline, asc
    spread, tsc total, astatc derivatives (ftts=btts, es=exact score).

    The WHALE FEED's slugs are kindless and league-led (audit
    2026-08-06: 'mlb-nyy-bos-2026-07-22...') — there the market type
    lives in the post-date suffix: empty or a single team code is a
    moneyline; 'o8pt5'/'u10' totals; a bare line ('3pt5', 'pos-2pt5',
    optionally after a team code) is a spread; 'es-2-0' exact score;
    'ftts'/'btts' both-teams-to-score. Anything unrecognized returns
    'unknown' — NEVER silently a tradeable type."""
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
    # Kindless feed grammar: type from the post-date suffix.
    suffix = _post_date_tokens(parts)
    if suffix is None:
        return "unknown"          # undatable slug: fail closed
    if not suffix:
        return "moneyline"        # bare event slug
    if "ftts" in suffix or "btts" in suffix:
        return "btts"
    if suffix[0] == "es":
        return "exact_score"
    if any(_TOTAL_RE.match(t) for t in suffix):
        return "total"
    if any(_LINE_RE.match(t) for t in suffix):
        return "spread"
    if len(suffix) == 1 and suffix[0].isalpha():
        return "moneyline"        # team-code pick side
    return "unknown"


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
    if w in PAUSED:
        return False
    if sport_of(slug) in HALTED_SPORTS:
        return False
    if market_type_of(slug) in BLOCKED_TYPES:
        return False
    if w in UNRESTRICTED:
        return True
    cells = CELLS.get(w)
    if not cells:
        return False
    sport = sport_of(slug)
    if not sport:
        return False
    if (sport, market_type_of(slug)) not in cells:
        return False
    band = ENTRY_BAND.get(w)
    if band is not None:
        # A banded whale with no readable price is REFUSED — the band is
        # the protection, and an absent price must not disable it.
        if price is None:
            return False
        try:
            px = float(price)
        except (TypeError, ValueError):
            return False
        if not (band[0] <= px <= band[1]):
            return False
    return True


def kalshi_min_ask(whale: str, slug: str) -> float:
    """Minimum Kalshi ask for this cell (fee-viability carve-out);
    0.0 = no extra constraint."""
    return KALSHI_MIN_ASK.get(((whale or "").strip().lower(),
                               sport_of(slug)), 0.0)
