"""Per-whale sport assignment for the copy book.

Owner directive 2026-08-06: the copy portfolio is four accounts, each
copied ONLY in the sport(s) where its fill-level forensic reconstruction
showed the strongest copyable (directional) edge:

  kch123        basketball (NBA/CBB), football (NFL/CFB), hockey (NHL)
                — 13.5% / 3.6% / 5.7% ROI on $136M combined volume
  HomeRunHazard baseball (MLB), WNBA — MLB totals 1.89%, WNBA 3.29%
  RN1           tennis (ATP/WTA) — the one slice where OUR filtered live
                copy cohort is measured positive; his blended book is
                market-making and not copyable elsewhere
  swisstony     soccer and everything outside the US majors — 2.5% on
                $776M, the volume engine

Exclusive assignment is deliberate: it prevents two whales walking us
onto opposite sides of the same game through different accounts, which
the per-asset dedupe cannot see (assets differ; games collide).

League codes are the venue slug's league token. Slug grammar is
{kind}-{league}-... for market/event slugs (atc/aec/asc/tsc/astatc/cpc),
but feed rows occasionally carry kindless slugs ({league}-...), so the
parser accepts both.
"""

from __future__ import annotations

_KINDS = {"atc", "aec", "asc", "tsc", "astatc", "cpc"}

BASKETBALL = {"nba", "cbb"}
FOOTBALL = {"nfl", "cfb"}
HOCKEY = {"nhl"}
BASEBALL = {"mlb"}
WNBA = {"wnba"}
TENNIS = {"atp", "wta"}
US_MAJORS = BASKETBALL | FOOTBALL | HOCKEY | BASEBALL | WNBA | TENNIS

ASSIGNMENTS: dict[str, set[str]] = {
    "kch123": BASKETBALL | FOOTBALL | HOCKEY,
    "homerunhazard": BASEBALL | WNBA,
    "rn1": TENNIS,
    # swisstony: complement of the US majors (soccer + other), handled
    # in copy_allowed — a set literal cannot express "everything else".
}


def league_of(slug: str) -> str:
    parts = [p for p in (slug or "").lower().split("-") if p]
    if not parts:
        return ""
    if parts[0] in _KINDS and len(parts) > 1:
        return parts[1]
    return parts[0]


def copy_allowed(whale: str, slug: str) -> bool:
    """May `whale`'s position in `slug`'s league be copied? Fails closed:
    an unassigned whale or unparseable slug copies nothing."""
    w = (whale or "").strip().lower()
    lg = league_of(slug)
    if not w or not lg:
        return False
    if w == "swisstony":
        return lg not in US_MAJORS
    allowed = ASSIGNMENTS.get(w)
    return allowed is not None and lg in allowed
