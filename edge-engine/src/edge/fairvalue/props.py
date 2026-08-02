"""Player props: venue text -> the exact bet a sharp book is quoting.

This is the mapper problem again, and it is the one that loses money
quietly. A mis-mapped team market usually produces an absurd edge that the
implausibility guard catches. A mis-mapped PROP produces a confident,
plausible fair value for a different proposition — "5+ strikeouts" priced
off the 5.5 line, or a pitcher's hits-allowed priced off a batter's hits —
and that looks exactly like edge right up until it settles.

So every rule here refuses rather than guesses, and the two places where
refusing is easy to get wrong are called out below:

1. "N+" IS A HALF-POINT BET. "5+ strikeouts" means P(X >= 5), which is
   Over 4.5 — NOT Over 5.0. On a whole-number line, exactly 5 pushes, so
   Over 5.0 is a materially different (and worse) bet. We therefore pair
   "N+" only against point == N - 0.5 and refuse a whole-number line
   outright. Getting this wrong is worth several cents on every strikeout
   market, always in the direction that flatters us.

2. STAT NAMES OVERLAP ACROSS ROLES. "hits" is a batter market; "hits
   allowed" is a pitcher market. Substring matching maps one to the other
   silently. Phrases are matched longest-first and any residual ambiguity
   is a refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Venue phrase -> provider market key. Ordered longest-phrase-first at match
# time so "hits allowed" can never be swallowed by "hits".
PROP_STATS: dict[str, str] = {
    # COMBO STATS FIRST — and they must stay ahead of their own components.
    # "1+ hits + runs + RBIs" contains "hits", "runs" and "rbis" as
    # substrings, so any shorter match would silently price a three-way
    # combo off a single-component line. Longest-phrase-first ordering makes
    # that safe, but the ordering is load-bearing, not cosmetic. This one
    # market was ~495 refusals a cycle before it was mapped.
    "hits + runs + rbis": "batter_hits_runs_rbis",
    "hits + runs + rbi": "batter_hits_runs_rbis",
    "hits runs rbis": "batter_hits_runs_rbis",
    "strikeouts": "pitcher_strikeouts",
    "strike outs": "pitcher_strikeouts",
    "ks": "pitcher_strikeouts",
    "outs recorded": "pitcher_outs",
    "outs": "pitcher_outs",
    "earned runs allowed": "pitcher_earned_runs",
    "earned runs": "pitcher_earned_runs",
    "hits allowed": "pitcher_hits_allowed",
    "walks allowed": "pitcher_walks",
    "total bases": "batter_total_bases",
    "home runs": "batter_home_runs",
    "home run": "batter_home_runs",
    "rbis": "batter_rbis",
    "runs batted in": "batter_rbis",
    "stolen bases": "batter_stolen_bases",
    "hits": "batter_hits",
    "runs": "batter_runs_scored",
    "shots on target": "player_shots_on_target",
    "shots on goal": "player_shots_on_goal",
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
}

# Markets whose outcomes are a plain yes/no rather than a line. These need a
# different pairing (the complement is "does not"), so they are refused here
# rather than mis-handled as an over/under.
BINARY_MARKETS = {"player_goal_scorer_anytime"}

_N_PLUS = re.compile(r"\b(\d+)\s*\+")
_OVER = re.compile(r"\b(?:over|o)\s+(\d+(?:\.\d+)?)\b", re.I)
_UNDER = re.compile(r"\b(?:under|u)\s+(\d+(?:\.\d+)?)\b", re.I)


@dataclass(frozen=True)
class PropBet:
    """A venue prop resolved to a side of an exact provider line."""

    player: str
    market_key: str      # provider market, e.g. pitcher_strikeouts
    side: str            # "Over" | "Under"
    point: float         # the provider point this bet pairs against EXACTLY


def norm_player(name: str) -> str:
    """Fold accents, punctuation and case so 'Shota Imanaga' matches
    'Shota Imanaga.' and 'José Ramírez' matches 'Jose Ramirez'."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", folded.lower())
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def _match_stat(text: str) -> tuple[str | None, str | None]:
    """(market_key, reason). Longest phrase wins; ties refuse."""
    low = text.lower()
    hits = [(phrase, key) for phrase, key in PROP_STATS.items() if phrase in low]
    if not hits:
        return None, "unknown_stat"
    hits.sort(key=lambda kv: -len(kv[0]))
    best_len = len(hits[0][0])
    winners = {key for phrase, key in hits if len(phrase) == best_len}
    if len(winners) > 1:
        # Two different markets claim the same phrase length. That is a
        # coin flip on which bet we are pricing, so it is not a bet.
        return None, "ambiguous_stat"
    return hits[0][1], None


def parse_prop(text: str) -> tuple[PropBet | None, str | None]:
    """Venue outcome text -> PropBet, or (None, reason).

    Understood forms:
        "Shota Imanaga 5+ strikeouts"      -> Over 4.5
        "Shota Imanaga over 4.5 strikeouts"-> Over 4.5
        "Shota Imanaga under 4.5 strikeouts"-> Under 4.5
    Anything else is refused by name.
    """
    if not text or not text.strip():
        return None, "empty"
    # Team margin markets ("TEAM wins by over 1.5 runs") contain stat words
    # ("runs") and an N+ shape, so the prop parser would happily read them
    # as batter_runs_scored for a player named "atlanta braves wins by".
    # They are run-line spreads and are handled by the line matcher.
    if re.search(r"\bwins?\s+by\b", text, re.IGNORECASE):
        return None, "team_margin_not_prop"
    market_key, why = _match_stat(text)
    if market_key is None:
        return None, why
    if market_key in BINARY_MARKETS:
        return None, "binary_market"

    side: str | None = None
    point: float | None = None
    m = _N_PLUS.search(text)
    if m:
        n = int(m.group(1))
        # See the module header: "N+" is P(X >= N) == Over (N - 0.5).
        side, point = "Over", n - 0.5
    else:
        mo, mu = _OVER.search(text), _UNDER.search(text)
        if mo and mu:
            return None, "over_and_under_in_one_outcome"
        if mo:
            side, point = "Over", float(mo.group(1))
        elif mu:
            side, point = "Under", float(mu.group(1))
    if side is None or point is None:
        return None, "no_threshold"

    # The player is whatever precedes the number. Everything after it is
    # the stat phrase, which we have already consumed.
    head = text[:m.start()] if m else text[:(mo or mu).start()]
    player = norm_player(re.sub(r"\b(to record|records?|total)\b", " ", head, flags=re.I))
    if not player:
        return None, "no_player"
    return PropBet(player=player, market_key=market_key, side=side, point=point), None


def fair_for_prop(bet: PropBet, quotes: dict) -> tuple[float | None, dict | None]:
    """De-vig the Over/Under pair at EXACTLY this point.

    `quotes` is {(market_key, normalised player, point): {"Over": odds,
    "Under": odds}} as assembled from the provider payload.

    A missing side means we cannot de-vig — a one-sided quote still carries
    the book's margin, and treating it as a probability hands us a free
    couple of cents of imaginary edge on every single prop.
    """
    from edge.fairvalue.devig import fair_value

    key = (bet.market_key, bet.player, bet.point)
    pair = quotes.get(key)
    if not pair:
        near = sorted({p for (mk, pl, p) in quotes
                       if mk == bet.market_key and pl == bet.player})
        return None, {"reason": "no_prop_quote_at_point",
                      "market": bet.market_key, "point": bet.point,
                      "player": bet.player[:24], "points_offered": near[:6]}
    if "Over" not in pair or "Under" not in pair:
        return None, {"reason": "one_sided_prop_quote",
                      "market": bet.market_key, "point": bet.point,
                      "player": bet.player[:24], "have": sorted(pair)}
    fair_over, fair_under = fair_value([pair["Over"], pair["Under"]])
    return (fair_over if bet.side == "Over" else fair_under), None
