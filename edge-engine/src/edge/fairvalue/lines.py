"""Derivative line handling (spreads / totals) — the kch123 extension.

Fair values come from de-vigging the sharp books' OWN two-sided quote for the
exact same line: "Over 8.5" is priced against "Under 8.5", "Chiefs -3.5"
against "Eagles +3.5". No model stands between the sharp line and the fair
value; Dixon-Coles remains a cross-check, not a source.

Hard rule encoded here: a line is only comparable at the EXACT same point.
A -7.5 quote never prices a -6.5 market; Over 8.5 never prices Over 9.5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from edge.venues.mapper import team_score

# A trailing unit word ("Over 23.5 games", "Munar +3.5 games") is part of
# the BET, not decoration. Tennis venues write game totals as
# "Over 23.5 games" and set totals as "Over 2.5 sets" — same shape, wildly
# different propositions — and the old anchored-at-the-number regex sent
# BOTH to the team matcher, where they died as no_side_match_moneyline
# (858 refusals in one measured cycle). The unit is captured so the caller
# can refuse a mismatched one instead of pricing sets off a games line.
_UNIT_WORDS = r"games?|sets?|points?|goals?|runs?|maps?|rounds?|corners?|cards?|aces?"
TOTAL_RE = re.compile(
    r"^\s*(over|under)\s*\(?([+-]?\d+(?:\.\d+)?)?\)?"
    r"(?:\s+(" + _UNIT_WORDS + r"))?\s*$", re.IGNORECASE)
# Team spread: text ending with a signed number, optionally unit-suffixed:
# "Chiefs -3.5", "Kansas City Chiefs (+3.5)", "Jaume Munar +3.5 games".
SPREAD_RE = re.compile(
    r"^(?P<team>.+?)\s*\(?(?P<point>[+-]\d+(?:\.\d+)?)\)?"
    r"(?:\s+(?P<unit>" + _UNIT_WORDS + r"))?\s*$")

# The unit a sport's PRIMARY totals/spreads market is denominated in — what
# the feed's unit-less "Over 22.5" means for that sport. A venue outcome
# stating any OTHER unit is a different bet and must refuse rather than
# pair: an unknown unit fails closed.
PRIMARY_TOTAL_UNIT = {
    "tennis": "game", "soccer": "goal", "basketball": "point",
    "americanfootball": "point", "icehockey": "goal", "baseball": "run",
}


def unit_conflicts(unit: str | None, sport_key: str | None) -> bool:
    """Does a stated unit disagree with what the sharp quote denominates?

    None (no unit stated) never conflicts — the overwhelmingly common venue
    form. A stated unit must equal the sport family's primary unit; a
    stated unit on an UNKNOWN family also conflicts, because we cannot say
    what the feed's number counts there."""
    if not unit:
        return False
    family = (sport_key or "").split("_", 1)[0]
    primary = PRIMARY_TOTAL_UNIT.get(family)
    return primary is None or unit.rstrip("s").lower() != primary


@dataclass(frozen=True)
class ParsedLine:
    kind: str            # 'moneyline' | 'total' | 'spread'
    team: str | None     # spread/moneyline: team text; total: None
    side: str | None     # total: 'over'|'under'
    point: float | None  # line value (spread signed, total unsigned)
    unit: str | None = None   # stated denomination ("games", "sets"), if any


def parse_outcome_line(name: str) -> ParsedLine:
    """Classify a feed or venue outcome string."""
    s = (name or "").strip()
    m = TOTAL_RE.match(s)
    if m:
        pt = float(m.group(2)) if m.group(2) is not None else None
        return ParsedLine("total", None, m.group(1).lower(), pt,
                          unit=(m.group(3) or None))
    m = SPREAD_RE.match(s)
    if m:
        return ParsedLine("spread", m.group("team").strip(), None,
                          float(m.group("point")),
                          unit=(m.group("unit") or None))
    return ParsedLine("moneyline", s or None, None, None)


@dataclass
class LinePair:
    """One two-sided sharp quote at a single point."""

    kind: str                    # 'total' | 'spread'
    point: float                 # totals: the O/U number; spreads: |line|
    a_name: str                  # totals: 'Over X' side; spreads: minus side team
    b_name: str
    a_parsed: ParsedLine
    b_parsed: ParsedLine
    a_odds: float
    b_odds: float


def _clean_binary_point(point: float | None) -> bool:
    """Only half-point lines are true binaries on a no-push venue.

    A book's two-way quote on a WHOLE line ("Over 8.0", "-2.0") is
    P(side | no push) — the push outcomes are refunded at the book but do
    not exist on the venue contract, so de-vigging it into a clean binary
    overstates our fair value, always in the flattering direction (the
    exact hazard props.py already documents for the N+ form). QUARTER
    lines (-0.25, -0.75) are split bets, not binaries at all. Audit
    2026-08-04: refuse both; .5 lines only.
    """
    if point is None:
        return False
    frac = abs(point) % 1
    return abs(frac - 0.5) < 1e-9


def pair_quotes(quotes: dict[str, float], kind: str) -> list[LinePair]:
    """Match a feed market dict {outcome_name: decimal_odds} into two-sided
    pairs at identical points. Unpaired quotes are dropped — a one-sided
    line cannot be de-vigged and is untradeable. Whole and quarter points
    are dropped too: see _clean_binary_point."""
    parsed = {name: parse_outcome_line(name) for name in quotes}
    pairs: list[LinePair] = []
    if kind == "total":
        overs = {p.point: n for n, p in parsed.items()
                 if p.kind == "total" and p.side == "over"
                 and _clean_binary_point(p.point)}
        unders = {p.point: n for n, p in parsed.items()
                  if p.kind == "total" and p.side == "under"
                  and _clean_binary_point(p.point)}
        for pt in sorted(set(overs) & set(unders)):
            a, b = overs[pt], unders[pt]
            pairs.append(LinePair("total", pt, a, b, parsed[a], parsed[b],
                                  quotes[a], quotes[b]))
    elif kind == "spread":
        entries = [(n, p) for n, p in parsed.items()
                   if p.kind == "spread" and _clean_binary_point(p.point)]
        used: set[str] = set()
        for i, (na, pa) in enumerate(entries):
            if na in used or pa.point >= 0:
                continue  # anchor on the minus side
            for nb, pb in entries[i + 1:] + entries[:i]:
                if nb in used or nb == na:
                    continue
                if abs(pb.point + pa.point) < 1e-9 and pb.point > 0:
                    pairs.append(LinePair("spread", abs(pa.point), na, nb,
                                          pa, pb, quotes[na], quotes[nb]))
                    used.update((na, nb))
                    break
    return pairs


def outcome_matches(venue_outcome: str, pair_side: ParsedLine) -> bool:
    """Does a venue outcome string represent this exact side of this exact
    line? Point must match to the decimal; teams need >=0.95 similarity."""
    v = parse_outcome_line(venue_outcome)
    if v.kind != pair_side.kind:
        return False
    if pair_side.kind == "total":
        if v.side != pair_side.side:
            return False
        # Venue outcomes are often just "Over"/"Under" with the point in the
        # market title; a point-less venue outcome matches (title was already
        # line-matched upstream), a stated point must match exactly.
        return v.point is None or pair_side.point is None or \
            abs(v.point - pair_side.point) < 1e-9
    if pair_side.kind == "spread":
        if v.point is None or pair_side.point is None or \
                abs(v.point - pair_side.point) > 1e-9:
            return False
        return team_score(v.team or "", pair_side.team or "") >= 0.95
    return False


_TITLE_TOTAL = re.compile(r"(?:o/u|over/under|total)", re.IGNORECASE)
_TITLE_SPREAD_TEAM = re.compile(r"spread[:\s]+(.+?)\s*\(", re.IGNORECASE)


def canonical_outcome(market_title: str, outcome_name: str) -> str:
    """Normalize a venue outcome to a self-describing string using the
    market title's line context (venues often put the point in the title:
    'Mets vs. Tigers: O/U 8.5' outcome 'Over'; 'Spread: Eagles (-7.5)'
    outcome 'Cowboys' — the named team gets the title's signed point, the
    OTHER team gets the mirrored point).
    Returns 'Over 8.5' / 'Eagles -7.5' style, or the outcome unchanged."""
    p = parse_outcome_line(outcome_name)
    pt = title_point(market_title)
    if p.kind == "total":
        point = p.point if p.point is not None else (abs(pt) if pt is not None else None)
        return f"{p.side.capitalize()} {point:g}" if point is not None \
            else outcome_name.strip()
    if p.kind == "spread":
        return f"{p.team} {p.point:+g}"
    # Plain team name: a spread-titled market makes it a spread side.
    if pt is not None and (_TITLE_SPREAD_TEAM.search(market_title or "")
                           or not _TITLE_TOTAL.search(market_title or "")):
        m = _TITLE_SPREAD_TEAM.search(market_title or "")
        if m is not None:
            titled_team = m.group(1)
            signed = pt if team_score(outcome_name, titled_team) >= 0.95 else -pt
            return f"{outcome_name.strip()} {signed:+g}"
    return outcome_name.strip()


# Venue slugs encode the handicap when the outcome field does not, e.g.
# "asc-lmx-san-atl-2026-07-25-neg-2pt5" (-2.5), "...-f5-pos-1pt5" (+1.5).
# Measured from live Polymarket US slugs, 2026-07-24.
_SLUG_LINE = re.compile(r"-(neg|pos)-(\d+)pt(\d+)(?:-|$)", re.IGNORECASE)


def slug_point(slug: str) -> float | None:
    """Signed handicap parsed from a venue market slug, or None."""
    m = _SLUG_LINE.search(slug or "")
    if not m:
        return None
    value = float(f"{m.group(2)}.{m.group(3)}")
    return -value if m.group(1).lower() == "neg" else value


# ── game segments (partial-game markets) ────────────────────────────────
#
# "asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5" is a FIRST FIVE INNINGS run line,
# not a full-game one. Read only for its handicap it looks identical to the
# full-game +1.5, and pricing it against the full-game fair value compares
# two different propositions — the same failure bet_identity was built to
# stop, one level up. Segment markets are a large share of what this venue
# lists, so they have to be identified, not ignored.
#
# Tokens are matched as whole dash-delimited slug components, and only ones
# that cannot plausibly be a team code are listed.
_SEGMENT_TOKENS = {
    "f5": "f5", "f3": "f3", "f7": "f7",          # baseball innings
    "1h": "h1", "h1": "h1", "fh": "h1",          # halves
    "2h": "h2", "h2": "h2",
    "1q": "q1", "q1": "q1", "2q": "q2", "q2": "q2",
    "3q": "q3", "q3": "q3", "4q": "q4", "q4": "q4",
    "1p": "p1", "p1": "p1", "2p": "p2", "p2": "p2", "3p": "p3", "p3": "p3",
}
# Per-inning / per-period markets: "…-i9-tex" is INNING 9, not the game.
# Found live 2026-07-30 producing a -96c "edge" (ask 0.98 against a 0.02
# full-game fair value) and, far more dangerously, several in the 2-8c range
# that the implausibility guard would have passed straight through to a real
# order.
_NUMBERED_SEGMENT = re.compile(r"^(i|inn|q|p|h|s)(\d{1,2})$", re.IGNORECASE)


def _numbered(part: str) -> str | None:
    m = _NUMBERED_SEGMENT.match(part or "")
    return f"{m.group(1).lower()}{int(m.group(2))}" if m else None


_TITLE_SEGMENT = (
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s*inning\b", re.I), None),
    (re.compile(r"first\s*(5|five)\s*innings?", re.I), "f5"),
    (re.compile(r"\bin(?:ning)?\s*(\d{1,2})\b", re.I), None),
    (re.compile(r"first\s*(3|three)\s*innings?", re.I), "f3"),
    (re.compile(r"first\s*(7|seven)\s*innings?", re.I), "f7"),
    (re.compile(r"\b(1st|first)\s*half\b", re.I), "h1"),
    (re.compile(r"\b(2nd|second)\s*half\b", re.I), "h2"),
    (re.compile(r"\b(1st|first)\s*quarter\b", re.I), "q1"),
    (re.compile(r"\b(1st|first)\s*period\b", re.I), "p1"),
)


def slug_segment(slug: str) -> str | None:
    """Game segment encoded in a venue slug ('f5', 'i9', 'h1', ...), or None
    for a full-game market."""
    for part in (slug or "").lower().split("-"):
        seg = _SEGMENT_TOKENS.get(part) or _numbered(part)
        if seg:
            return seg
    return None


def title_segment(title: str) -> str | None:
    for pattern, seg in _TITLE_SEGMENT:
        m = pattern.search(title or "")
        if m:
            # seg None => the pattern captured the period NUMBER itself.
            return seg if seg else f"i{int(m.group(1))}"
    return None


def tag_segment(outcome_key: str, segment: str | None) -> str:
    """Prefix a segment marker so a partial-game outcome can never collide
    with — or be matched against — its full-game namesake."""
    return f"[{segment}] {outcome_key}" if segment else outcome_key


_SEG_PREFIX = re.compile(r"^\[([a-z0-9]+)\]\s*")


# "TEAM wins by over 1.5 runs" is a RUN-LINE SPREAD wearing prose: margin
# > 1.5 is exactly TEAM -1.5, a market the sharp books quote directly
# (including per-segment: f5 spreads are requested). Before this it was
# parsed as a player prop with player "atlanta braves wins by" (and refused)
# or fell into the team matcher (and refused) — hundreds of mappable
# markets a cycle thrown away. "N+ runs" means margin >= N, which is the
# -(N - 0.5) line; "over X" means the -X line.
_MARGIN_RE = re.compile(
    r"^(?P<team>.+?)\s+wins?\s+by\s+"
    r"(?:(?:over\s+)(?P<over>\d+(?:\.\d+)?)|(?P<plus>\d+(?:\.\d+)?)\+)"
    r"\s*(?:runs?|goals?|points?)?(?:\s+in\s+.*)?$",
    re.IGNORECASE)


def margin_to_spread(text: str) -> str | None:
    """'Baltimore Orioles wins by over 1.5 runs in first…' -> 'Baltimore
    Orioles -1.5', or None when the text is not a margin market. Any
    segment context was already split off by split_segment; the textual
    tail ('in first…') describes that same segment and is dropped."""
    m = _MARGIN_RE.match((text or "").strip())
    if not m:
        return None
    line = (float(m.group("over")) if m.group("over")
            else float(m.group("plus")) - 0.5)
    if line <= 0:
        return None
    return f"{m.group('team').strip()} -{line:g}"


_SET_WINNER = re.compile(
    r"^\s*set\s*(\d)\s*winner\s*$|^\s*(\d)(?:st|nd|rd|th)\s+set\s+winner\s*$",
    re.IGNORECASE)


def split_segment(outcome_key: str) -> tuple[str | None, str]:
    """(segment, bare outcome). Inverse of tag_segment.

    Tennis set winners ("Set 1 Winner") are PARTIAL-match bets — the same
    relationship a first-half line has to a football game. Left
    unrecognized they fell through to the team matcher and died as
    no_side_match_moneyline; as segments they refuse cleanly as
    no_sharp_quote_segment_s1 until a sharp per-set quote exists to pair
    them against. Never priced off the full-match line."""
    m = _SET_WINNER.match(outcome_key or "")
    if m:
        n = m.group(1) or m.group(2)
        return f"s{n}", (outcome_key or "")
    m = _SEG_PREFIX.match(outcome_key or "")
    if not m:
        return None, (outcome_key or "")
    return m.group(1), outcome_key[m.end():]


def apply_slug_line(outcome_key: str, slug: str) -> str:
    """Attach the slug's handicap to an outcome that doesn't carry one.

    Without this a spread market whose outcome field is just the team name
    gets priced against the MONEYLINE fair value — comparing 'wins by 3+'
    against 'wins', which manufactures enormous phantom edges. With it, the
    outcome is line-explicit and only pairs with the sharp book's quote at
    the same point (or matches nothing, which is the correct outcome)."""
    point = slug_point(slug)
    if point is None:
        return outcome_key
    parsed = parse_outcome_line(outcome_key)
    if parsed.point is not None:
        return outcome_key  # already line-explicit
    if parsed.kind == "total" and parsed.side:
        return f"{parsed.side.capitalize()} {abs(point):g}"
    return f"{outcome_key.strip()} {point:+g}"


@dataclass(frozen=True)
class BetIdentity:
    """What bet a venue market actually represents, derived from EVERY
    available signal with cross-agreement required.

    The failure mode this exists to prevent: one signal is missing or
    misread, we silently fall back to a different bet (a spread priced as a
    moneyline), and the resulting 'edge' is a comparison of two unrelated
    propositions. Any disagreement between signals makes the market
    untradeable rather than guessed."""

    category: str            # moneyline | spread | total
    point: float | None
    side: str | None         # team text, or over/under
    sources: tuple[str, ...]  # signals that contributed the point
    conflict: str | None      # populated when signals disagree
    segment: str | None = None  # 'f5', 'h1', ... ; None = full game

    @property
    def tradeable(self) -> bool:
        if self.conflict:
            return False
        if self.category in ("spread", "total"):
            return self.point is not None
        return bool(self.side)


def bet_identity(slug: str, title: str, outcome: str) -> BetIdentity:
    """Cross-check the handicap across slug, market title, and outcome text."""
    from_outcome = parse_outcome_line(outcome)
    t_point = title_point(title)
    s_point = slug_point(slug)
    # Segment first: a partial-game market is a different bet from its
    # full-game namesake even when every handicap signal agrees.
    seg_slug, seg_title = slug_segment(slug), title_segment(title)
    segment = seg_slug or seg_title
    seg_conflict = ("segment mismatch slug=%s title=%s" % (seg_slug, seg_title)
                    if seg_slug and seg_title and seg_slug != seg_title else None)

    points: dict[str, float] = {}
    if from_outcome.point is not None:
        points["outcome"] = from_outcome.point
    if t_point is not None:
        points["title"] = t_point
    if s_point is not None:
        points["slug"] = s_point

    # Totals carry an unsigned number; spreads a signed one. Compare on the
    # appropriate footing so "+2.5 in the title, 2.5 in the slug" isn't a
    # false conflict.
    is_total = (from_outcome.kind == "total"
                or bool(_TITLE_TOTAL.search(title or "")))
    def norm(v: float) -> float:
        return abs(v) if is_total else v

    conflict = None
    if len(points) >= 2:
        vals = {round(norm(v), 3) for v in points.values()}
        if len(vals) > 1:
            conflict = "point mismatch " + ", ".join(
                f"{k}={v:+g}" for k, v in sorted(points.items()))

    if is_total:
        point = abs(next(iter(points.values()))) if points else None
        side = from_outcome.side
        return BetIdentity("total", point, side, tuple(sorted(points)),
                           conflict or seg_conflict, segment)

    if points:
        point = next(iter(points.values()))
        team = from_outcome.team or (outcome or "").strip() or None
        return BetIdentity("spread", point, team, tuple(sorted(points)),
                           conflict or seg_conflict, segment)

    # No handicap anywhere: a plain moneyline — but only if nothing hints
    # otherwise. A spread-flavoured title with no parsable number is a
    # signal we failed to read, not a moneyline.
    if _TITLE_SPREAD_TEAM.search(title or "") or "spread" in (title or "").lower():
        return BetIdentity("spread", None, (outcome or "").strip() or None, (),
                           "spread market with unreadable line", segment)
    return BetIdentity("moneyline", None, (outcome or "").strip() or None, (),
                       seg_conflict, segment)


def title_point(title: str) -> float | None:
    """Extract a line from a market title, e.g. 'Mets vs. Tigers: O/U 8.5'
    -> 8.5, 'Spread: Eagles (-7.5)' -> -7.5. None when absent."""
    m = re.search(r"(?:o/u|over/under|total)[:\s]*([+-]?\d+(?:\.\d+)?)",
                  title or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\(([+-]\d+(?:\.\d+)?)\)", title or "")
    if m:
        return float(m.group(1))
    return None


# The venue writes the soccer draw as "Tie (Reg. Time)"; the feed writes
# "Draw". Ordinary similarity scores that pair at 0.24, so EVERY three-way
# soccer market lost its third outcome — measured live at 2,989
# no_side_match_moneyline rejections in a single cycle, the largest single
# loss in the funnel.
_DRAW_WORDS = {"draw", "tie", "empate", "nul", "x"}
# Qualifiers the venue hangs off the draw, all of which carry no team.
_DRAW_FILLER = {"the", "reg", "regular", "time", "match", "result", "90",
                "90min", "ft", "fulltime", "full", "in"}


def is_draw(name: str) -> bool:
    """Does this outcome mean 'neither side wins'?

    "Tie (Reg. Time)", "The Draw" and "Draw" all agree; a club merely
    CONTAINING the word does not. Substring or word-search matching would
    classify a side called "Tie Break FC United" as the draw and then price
    a team against the draw's fair value, so the test is that EVERY token is
    either a draw word or a known qualifier — nothing left over to be a team
    name. An outcome carrying a handicap or an over/under is a different bet
    and never a draw.
    """
    t = (name or "").strip()
    if not t or parse_outcome_line(t).kind != "moneyline":
        return False
    tokens = [w for w in re.split(r"[^a-z0-9]+", t.lower()) if w]
    if not tokens or not any(w in _DRAW_WORDS for w in tokens):
        return False
    return all(w in _DRAW_WORDS or w in _DRAW_FILLER for w in tokens)
