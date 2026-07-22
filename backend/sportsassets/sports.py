"""Deterministic sport classification.

Maps a market's Gamma tags / slugs / title to one canonical sport bucket.
Classification is pure and re-runnable: same inputs always give the same
sport, so a reclassification sweep after a rule change is safe.
"""

from __future__ import annotations

SPORTS = [
    "NBA",
    "NFL",
    "MLB",
    "NHL",
    "Soccer",
    "Tennis",
    "MMA",
    "Golf",
    "Other-Sports",
    "Non-Sports",
]

UNCLASSIFIED = "unclassified"

# Keyword → sport, checked in order (first match wins). Keywords are matched
# against lowercase tag labels/slugs first (strong signal), then title tokens.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("NBA", ("nba", "basketball", "wnba", "ncaab", "college-basketball", "march madness")),
    ("NFL", ("nfl", "super bowl", "superbowl", "american football", "ncaaf", "college-football")),
    ("MLB", ("mlb", "baseball", "world series")),
    ("NHL", ("nhl", "hockey", "stanley cup")),
    (
        "Soccer",
        (
            "soccer",
            "epl",
            "premier league",
            "ucl",
            "champions league",
            "la liga",
            "laliga",
            "serie a",
            "bundesliga",
            "ligue 1",
            "world cup",
            "mls",
            "europa league",
            "copa",
            "fifa",
            "uefa",
        ),
    ),
    ("Tennis", ("tennis", "wimbledon", "us open tennis", "atp", "wta", "roland garros")),
    ("MMA", ("ufc", "mma", "boxing", "fight night", "bellator")),
    ("Golf", ("golf", "pga", "masters tournament", "ryder cup", "liv golf")),
    (
        "Other-Sports",
        (
            "sports",
            "cricket",
            "rugby",
            "f1",
            "formula 1",
            "nascar",
            "olympics",
            "esports",
            "chess",
            "darts",
            "cycling",
            "athletics",
        ),
    ),
]


def classify(tags: list[str] | None, slug: str = "", title: str = "") -> str:
    """Classify a market into a sport bucket.

    `tags` are Gamma tag labels/slugs; `slug` and `title` are fallbacks.
    Returns a value from SPORTS. Markets with no sports signal at all are
    'Non-Sports' (we may still ingest them if a whale trades them — they are
    excluded from sport rollups but kept in totals for leaderboard drift checks).
    """
    haystacks: list[str] = []
    for t in tags or []:
        haystacks.append(t.lower())
    if slug:
        haystacks.append(slug.replace("-", " ").lower())
    if title:
        haystacks.append(title.lower())

    for sport, keywords in _RULES:
        for hay in haystacks:
            for kw in keywords:
                if kw in hay:
                    return sport
    return "Non-Sports" if haystacks else UNCLASSIFIED


def is_sport(sport: str) -> bool:
    return sport not in ("Non-Sports", UNCLASSIFIED)
