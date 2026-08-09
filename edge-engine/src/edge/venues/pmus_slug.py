"""Polymarket US slug grammar — the side of the bet, read structurally.

Measured from live slugs (2026-07-29 probe):

    atc-lmx-pue-gua-2026-07-31-gua        per-team market, side = 'gua'
    atc-irlp-dun-slr-2026-07-31-slr       per-team market, side = 'slr'
    asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5  spread, first-5-innings, +1.5
    aec-mlb-sea-lad-2026-07-30            single market, NO side suffix

The venue often gives the market a `title` that is just the slug again, so
the old path fuzzy-matched a slug STRING against team names. That produced
two failures at once:

  * 208 no_side_match_moneyline per cycle — nothing in "aec-mlb-sea-lad-
    2026-07-30" resembles "Seattle Mariners", so the outcome was dropped;
  * far worse, the occasional ACCIDENTAL match on the wrong side, e.g.
    "aec-wta-xinwan-liusam-2026-07-29" priced at 1c against a 96c fair
    value — a 95-cent "edge" that is really an inverted bet.

The fix is to stop guessing from prose. A slug names its teams as short
codes in away-home order, and the trailing code (when present) says which
side the market is. We only ever need to tell TWO candidates apart — the
feed's home and away — which is a far easier problem than identifying a team
from scratch, and it fails closed when the codes do not clearly discriminate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Trailing decorations that are never a team code.
# Period markers are never a team code — 'i9' is inning nine, not a club.
_PERIOD = re.compile(r"^(i|inn|q|p|h|s)\d{1,2}$", re.IGNORECASE)
_NOT_A_CODE = {"neg", "pos", "f5", "f3", "f7", "1h", "2h", "h1", "h2",
               "1q", "2q", "3q", "4q", "q1", "q2", "q3", "q4",
               "1p", "2p", "3p", "p1", "p2", "p3", "over", "under",
               "draw", "tie", "yes", "no"}

# Marker emitted at discovery when the side is known only as a slug code.
# The runner resolves it against the feed event's actual team names.
CODE_PREFIX = "@code:"


@dataclass(frozen=True)
class PmusSlug:
    kind: str                # 'atc' | 'aec' | 'asc' | other
    league: str | None
    codes: tuple[str, ...]   # team codes in slug order (away, home)
    side: str | None         # trailing team code, when the slug names one
    date: str | None
    tail: tuple[str, ...] = ()   # everything after the date, verbatim


def parse_slug(slug: str) -> PmusSlug:
    parts = [p for p in (slug or "").lower().split("-") if p]
    date_i = next((i for i, p in enumerate(parts) if _DATE.match(p)), None)
    if date_i is None:
        # Dates are written yyyy-mm-dd, so the split above breaks them into
        # three components; rejoin and look again.
        for i in range(len(parts) - 2):
            joined = "-".join(parts[i:i + 3])
            if _DATE.match(joined):
                parts = parts[:i] + [joined] + parts[i + 3:]
                date_i = i
                break
    if date_i is None:
        return PmusSlug(parts[0] if parts else "", None, (), None, None)

    head, tail = parts[:date_i], parts[date_i + 1:]
    kind = head[0] if head else ""
    league = head[1] if len(head) > 1 else None
    codes = tuple(head[2:])
    side = next((p for p in reversed(tail)
                 if p not in _NOT_A_CODE and not p.isdigit()
                 and "pt" not in p and not _PERIOD.match(p)), None)
    return PmusSlug(kind, league, codes, side, parts[date_i], tuple(tail))


def code_score(code: str, team: str) -> float:
    """How well a short slug code identifies a team name, 0..1.

    Codes are abbreviations, not names: 'gua' for Guadalajara, 'slr' for
    Sligo Rovers, 'nyy' for New York Yankees. Ordinary string similarity
    scores all of these near zero, which is exactly why the text matcher was
    dropping them."""
    code = (code or "").strip().lower()
    name = (team or "").strip().lower()
    if not code or not name:
        return 0.0
    words = [w for w in re.split(r"[^a-z0-9]+", name) if w]
    if not words:
        return 0.0
    if code in words:                                   # exact word
        return 1.0
    if any(w.startswith(code) for w in words):          # 'gua' / CD Guadalajara
        return 0.9                                      # (club prefixes ignored)
    joined = "".join(words)
    if joined.startswith(code):
        return 0.9
    initials = "".join(w[0] for w in words)
    if code == initials or initials.startswith(code):   # 'nyy' / New York Yankees
        return 0.9
    # Abbreviations skip letters — 'slr' for SLigo Rovers, 'lgo' for LG
    # twins. An in-order subsequence catches the whole family; it is
    # permissive on its own, which is safe here only because the caller is
    # choosing between exactly two teams and enforces a margin.
    if _is_subsequence(code, joined):
        return 0.8
    return 0.0


def _is_subsequence(code: str, text: str) -> bool:
    it = iter(text)
    return all(ch in it for ch in code)


MIN_SIDE_SCORE = 0.8      # below this a code has not identified anything
MIN_MARGIN = 0.1          # and it must clearly beat the OTHER team


def resolve_side(code: str, home: str, away: str) -> str | None:
    """Which of these two teams does this slug code mean? None = unclear.

    Two candidates only, so this is discrimination rather than
    identification. Refusing on an ambiguous margin is the whole point: an
    inverted side does not produce a small error, it produces a bet on the
    opposite outcome priced with the wrong number."""
    h, a = code_score(code, home), code_score(code, away)
    best, other, winner = (h, a, home) if h >= a else (a, h, away)
    if best < MIN_SIDE_SCORE or best - other < MIN_MARGIN:
        return None
    return winner


def is_bare_team_market(slug: str) -> bool:
    """True only for a FULL-GAME team moneyline slug: 'atc' kind whose
    post-date tail is exactly the side code and nothing else.

    The slug-code outcome fallback used this parser's `side` while
    DISCARDING the qualifiers around it — so 'atc-mlb-nym-pit-…-f5-nym'
    (first five innings, CAN TIE) entered the cross-venue arbitrage pool
    wearing a bare team name. Two of those "guaranteed" legs lose the
    whole stake on a tie (owner report 2026-08-09: Mets/Pirates F5 pair,
    $51 exposed). Anything with a segment, line, or extra qualifier is
    a different proposition and never a dutchable moneyline side."""
    p = parse_slug(slug)
    return bool(p.kind == "atc" and p.side and p.tail == (p.side,))


def looks_like_a_slug(text: str) -> bool:
    """Is this 'outcome name' actually just the slug again? The venue does
    this often, and a slug fuzzy-matched against team names is how a market
    ends up priced as its own opposite."""
    t = (text or "").strip()
    return bool(t) and " " not in t and t.count("-") >= 3
