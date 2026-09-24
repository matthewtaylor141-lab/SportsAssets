"""PINNACLE EVENT -> VENUE CONTRACT, exactly or not at all.

This is the half that was missing. A de-vigged probability with no venue
contract beside it is not an opportunity, and a probability paired with
the WRONG venue contract is worse than no probability at all, because it
prices cleanly and looks right.

WHY NOT `edge/venues/mapper.py`. Its `norm_team` is the obvious thing to
reuse and it is the wrong tool here, for a reason worth recording. Its
`_NOISE` pattern strips the tokens `city`, `town`, `united` and `utd` as
noise, which is correct for its own job (scoring candidates before a
0.95-confidence gate) and catastrophic for ours. Measured, not supposed:

    norm_team("Manchester City")    -> 'manchester'
    norm_team("Manchester United")  -> 'manchester'      COLLIDE
    norm_team("Leeds United")       -> 'leeds'
    norm_team("Leeds City")         -> 'leeds'            COLLIDE

Both Manchester clubs were on the slate this source actually priced
(Liverpool v Manchester City and Manchester United v Tottenham, feed run
35935500538). Reusing that normaliser would have been free licence to
value one derby and buy the other. So normalisation here is deliberately
conservative -- deaccent, lowercase, drop punctuation, and NOTHING else --
and any pair that still collides is a refusal rather than a guess.

Its fuzzy `SequenceMatcher` scoring is not used either, for the reason
already recorded in `bettor_pinnacle_devig.map_selection`: the direction
is reversed. A near-match that scores 0.94 is not "probably the right
market", it is a DIFFERENT market that happens to read similarly, and
pricing it would be the exact failure the refusal list exists to prevent.

NO ALIAS TABLE IS INVENTED. `bettor_sport_mapping` sets the standard this
module follows: a mapping is admitted only where the venue's own rows
attest it. There is no attested alias data for these leagues yet, so
unmatched names are REFUSED BY NAME and counted. The miss rate is a
measurement to report, not a gap to paper over with general knowledge.
"""

from __future__ import annotations

import re
import unicodedata

VERSION = "BETTOR_VENUE_MAPPING_V1"

#: Deliberately NOT a noise list. See the module docstring.
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")

R_NO_CONTRACT = "NO_VENUE_CONTRACT_FOR_EVENT"
R_AMBIGUOUS = "VENUE_MAPPING_AMBIGUOUS"
R_CLOSED = "VENUE_MARKET_CLOSED_OR_RESOLVED"
R_COLLIDE = "TEAM_NAMES_COLLIDE_AFTER_NORMALISATION"
R_SEGMENT = "VENUE_CONTRACT_IS_A_SEGMENT_NOT_FULL_GAME"
R_NO_TEAMS = "EVENT_DOES_NOT_NAME_TWO_TEAMS"

REFUSALS = (R_NO_CONTRACT, R_AMBIGUOUS, R_CLOSED, R_COLLIDE, R_SEGMENT,
             "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE",
            R_NO_TEAMS)

#: Tokens that mark a venue title as covering only PART of a game. The
#: external valuation prices full-game h2h only, so a segment contract is
#: refused rather than paired with a full-game price -- the same rule
#: `edge/fairvalue/feed.py` states for its own segments ("a segment we
#: have no quotes for is REFUSED, never priced off the full game").
SEGMENT_MARKERS = (
    "1st half", "first half", "2nd half", "second half", "halftime",
    "half time", "1st quarter", "first quarter", "1st period",
    "first period", "1st inning", "first inning", "1st 5 innings",
    "first 5 innings", "f5", "1h", "2h", "q1", "p1",
)


#: A LINE MARKET IS NOT A MONEYLINE, and this is a correctness rule rather
#: than a preference. Run 23 mapped a Pinnacle h2h fixture to
#: `mlb-chc-mia-2026-09-05-total-9pt5` -- an over/under 9.5 runs contract --
#: because the title names both clubs and "total" was in neither the
#: segment list nor anything else. Had the book read succeeded, p(home win)
#: would have been compared against the ask on "over 9.5 runs" and the
#: resulting "edge" would have been a category error with a number on it.
#:
#: Checked against the TITLE and the SLUG, because the slug is where the
#: line usually lives (`...-total-9pt5`, `...-spread-neg-1pt5`) and the
#: title can read as a plain fixture name.
LINE_MARKERS = (
    "total", "totals", "over", "under", "spread", "spreads", "handicap",
    "handicaps", "run line", "puck line", "alternate", "alt", "margin",
    "asian", "btts", "both teams to score", "draw no bet",
)
#: A venue line token: `9pt5`, `neg-1pt5`, `o2pt5`. The slug form the venue
#: uses for a half-point line, which no moneyline slug carries.
_LINE_TOKEN = re.compile(r"(?:^|[-_])(?:o|u|pos|neg)?\d+pt\d(?:$|[-_])")

R_LINE = "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE"


def is_line_market(title: str, slug: str = "") -> bool:
    """True when the contract prices a line, not the winner.

    Refuses on either channel: a moneyline never carries a half-point
    token, and a totals market whose title happens to read like a fixture
    name is exactly the case that got through.
    """
    t = " %s " % norm_name(title)
    if any((" %s " % m) in t for m in LINE_MARKERS):
        return True
    sl = str(slug or "").lower()
    if _LINE_TOKEN.search(sl):
        return True
    return any(("-%s-" % m.replace(" ", "-")) in "-%s-" % sl
               for m in LINE_MARKERS)


def norm_name(name: str) -> str:
    """Deaccent, lowercase, drop punctuation. Token ORDER is preserved.

    Order is preserved because sorting tokens is what let the reference
    mapper turn two different clubs into one string. "Cruz Azul" and
    "Azul Cruz" are not two readings of one name in any feed we consume.
    """
    s = unicodedata.normalize("NFKD", name or "")
    s = s.encode("ascii", "ignore").decode()
    s = _NON_ALNUM.sub(" ", s.lower())
    return " ".join(s.split())


def _tokens(name: str) -> set:
    return set(norm_name(name).split())


def is_segment(title: str) -> bool:
    t = " %s " % norm_name(title)
    return any((" %s " % m) in t for m in SEGMENT_MARKERS)


def map_event(*, home, away, markets):
    """Find THE ONE venue market for this event, or refuse.

    `markets` is an iterable of dicts carrying at least condition_id,
    title, event_title, closed and resolved -- the columns the `markets`
    table actually has, so this can be fed straight from a query.

    Returns {"mapped": bool, "condition_id": ..., "refusals": [...],
             "candidates": n, ...}. Never raises on ordinary bad input.
    """
    out = {"version": VERSION, "mapped": False, "condition_id": None,
           "home": home, "away": away,
           "home_norm": norm_name(home or ""), "away_norm": norm_name(away or ""),
           "candidates": 0, "candidate_ids": [], "refusals": []}

    h, a = _tokens(home or ""), _tokens(away or "")
    if not h or not a:
        out["refusals"].append(R_NO_TEAMS)
        return out
    if out["home_norm"] == out["away_norm"]:
        # Cannot happen from a real fixture; can happen from a
        # normalisation that eats the distinguishing token. Refusing here
        # is what stops the Manchester collision reaching a price.
        out["refusals"].append(R_COLLIDE)
        return out

    hits, blocked_closed, blocked_segment = [], 0, 0
    blocked_line = 0
    for m in markets:
        title = "%s %s" % (m.get("title") or "", m.get("event_title") or "")
        toks = _tokens(title)
        # BOTH sides must be fully named. A title carrying only one team is
        # not this fixture, however well it scores.
        if not (h <= toks and a <= toks):
            continue
        if m.get("closed") or m.get("resolved"):
            blocked_closed += 1
            continue
        if is_segment(title):
            blocked_segment += 1
            continue
        if is_line_market(title, m.get("slug") or ""):
            blocked_line += 1
            continue
        hits.append(m)

    out["candidates"] = len(hits)
    out["candidate_ids"] = [m.get("condition_id") for m in hits][:8]
    out["blocked_closed"] = blocked_closed
    out["blocked_line"] = blocked_line
    out["blocked_segment"] = blocked_segment

    if len(hits) == 1:
        out["mapped"] = True
        out["condition_id"] = hits[0].get("condition_id")
        out["matched_title"] = hits[0].get("title")
        # THE WHOLE ROW, because the caller's next step needs the fixture's
        # own fields -- title, event_title, the global slug -- to cross to
        # the venue's catalogue through `workers.premap.resolve`. Returning
        # only the condition id sent the caller back to the database for
        # the slug, and the slug it found was the GLOBAL one.
        out["market_row"] = dict(hits[0])
        out["match"] = "BOTH_TEAM_NAMES_FULLY_CONTAINED_EXACTLY_ONE_MARKET"
        return out

    if not hits:
        # Distinguish "no such fixture here" from "the fixture is here but
        # unusable", because they have different remedies.
        if blocked_closed:
            out["refusals"].append(R_CLOSED)
        if blocked_segment:
            out["refusals"].append(R_SEGMENT)
        if blocked_line:
            out["refusals"].append(R_LINE)
        if not blocked_closed and not blocked_segment and not blocked_line:
            out["refusals"].append(R_NO_CONTRACT)
        return out

    out["refusals"].append(R_AMBIGUOUS)
    return out


def describe() -> dict:
    return {
        "version": VERSION,
        "normalisation": ("deaccent, lowercase, drop punctuation. NO "
                          "noise-word stripping and NO token sorting"),
        "why_not_the_existing_mapper": (
            "its _NOISE list strips 'city' and 'united', which collapses "
            "Manchester City and Manchester United to the same token. Both "
            "were on the slate this source priced"),
        "fuzzy_matching": False,
        "alias_table": None,
        "alias_policy": ("none invented. An unmatched name is refused and "
                         "counted, following bettor_sport_mapping's rule "
                         "that a mapping needs venue attestation"),
        "refusals": list(REFUSALS),
        "prices_segments": False,
    }
