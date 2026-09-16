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
            "end in a draw",
            "both teams to score",
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


# Event/market slug prefixes — Polymarket sports slugs lead with the league
# ("mlb-nyy-bos-2026-07-10", "atp-hamburg-…"). Deterministic, checked first
# after tags. Keys are the first dash-separated token, lowercased.
_SOCCER_CODES = [
    # Recovered from the audited full-history study (146K markets) — league/
    # competition slug codes observed on real Polymarket sports events.
    "epl", "ucl", "uel", "uef", "mls", "laliga", "lal", "seriea", "sea",
    "bundesliga", "bun", "bl2", "ligue1", "fl1", "fr2", "fifa", "fif", "fifwc",
    "uefa", "copa", "concacaf", "libertadores", "lib", "ligamx", "mex", "elc",
    "es2", "bra", "bra2", "brco", "arg", "j2100", "j1100", "chi", "chi1",
    "itsb", "ere", "spl", "tur", "por", "nor", "col", "col1", "kor", "mar1",
    "egy1", "sud", "rou1", "scop", "aus", "per1", "swe", "rus", "cze1", "den",
    "efa", "el1", "el2", "nwsl", "enl", "ukr1", "hr1", "cde", "gtm", "isp",
    "acn", "svk1", "cdr", "sga",
]

_SLUG_PREFIX: dict[str, str] = {
    "mlb": "MLB", "kbo": "MLB",
    "nba": "NBA", "wnba": "NBA", "ncaab": "NBA", "cbb": "NBA", "bkcba": "NBA",
    "nfl": "NFL", "cfb": "NFL", "ncaaf": "NFL",
    "nhl": "NHL", "khl": "NHL",
    "atp": "Tennis", "wta": "Tennis", "itf": "Tennis", "tennis": "Tennis",
    "challenger": "Tennis",
    "ufc": "MMA", "mma": "MMA", "boxing": "MMA", "pfl": "MMA",
    "pga": "Golf", "golf": "Golf", "lpga": "Golf", "liv": "Golf",
    "f1": "Other-Sports", "nascar": "Other-Sports", "cricket": "Other-Sports",
    "darts": "Other-Sports", "rugby": "Other-Sports", "cs2": "Other-Sports",
    "lol": "Other-Sports", "dota2": "Other-Sports", "valorant": "Other-Sports",
    **{code: "Soccer" for code in _SOCCER_CODES},
}


def _slug_sport(slug: str) -> str | None:
    if not slug:
        return None
    head = slug.strip().lower().split("-", 1)[0]
    return _SLUG_PREFIX.get(head)


def classify(
    tags: list[str] | None, slug: str = "", title: str = "", event_slug: str = ""
) -> str:
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

    # League slug prefix — decisive when tags/keywords are silent.
    for s in (event_slug, slug):
        found = _slug_sport(s)
        if found:
            return found

    return "Non-Sports" if haystacks or event_slug else UNCLASSIFIED


def is_sport(sport: str) -> bool:
    return sport not in ("Non-Sports", UNCLASSIFIED)
