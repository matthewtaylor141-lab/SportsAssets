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
            R_NO_TEAMS, "VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED",
            "VENUE_CONTRACT_KIND_IS_NOT_A_CONFIRMED_MONEYLINE",
            "VENUE_SLUG_DOES_NOT_DECOMPOSE_INTO_EVENT_AND_SIDE",
            "VENUE_EVENT_IS_NOT_A_TWO_PARTICIPANT_MATCH",
            "VENUE_EVENT_TITLE_NOT_CAPTURED")

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


# ── WHICH PERIOD THE VENUE'S OWN CONTRACT PAYS ON ───────────────────
#
# THE DEFECT THIS CLOSES. Two separate places asserted the period instead
# of establishing it:
#
#   1 `is_segment()` above reads the TITLE only. `is_line_market()` reads
#     the title AND the slug, because "the slug is where the line usually
#     lives" -- and that is just as true of a period. The venue puts the
#     period in the slug: `atc-mlb-atl-mia-2026-09-25-i6-draw` is inning
#     six, and its title reads as a plain fixture name.
#
#   2 The entry lane then set `contract["period"] = "FULL_GAME"` as a
#     LITERAL, and passed the same literal to the valuation, so both
#     sides of the comparison agreed on a period neither had checked.
#
# Run 71 proved the exposure is real rather than theoretical: the venue's
# own board returned `atc-mlb-atl-mia-2026-09-25-i6-draw` and
# `atc-ebfcwc-bjo-paris-2026-09-26-dh1-bjo` inside the money-line family,
# because `copy_sports.market_type_of` classifies the venue grammar on
# the KIND PREFIX alone and never inspects the suffix. A full-match
# probability priced against an inning-six payout is a category error with
# a number on it -- the same failure as the totals market in run 23,
# arriving through the period instead of the line.
#
# THE RULE IS AFFIRMATIVE, AND IT NEEDS NO GRAMMAR VOCABULARY. A venue
# money-line slug is the event, dated, optionally followed by the side
# this contract pays on. So: take everything after the trailing
# YYYY-MM-DD; it must be EMPTY (the `aec` family, where both sides share
# one slug and the side is the intent) or EXACTLY the side token the
# resolver matched. Anything else -- an extra token before the side, a
# token that is not the side, no date at all -- is NOT ESTABLISHED, and
# not established is a refusal rather than a full match by default.
#
# Guessing a period vocabulary was the alternative and it is worse: a
# whitelist of `i6`/`dh1`/`h1`/`finalq` can only refuse the segments
# somebody already thought of, while this refuses every shape that is not
# demonstrably the whole fixture.

FULL_MATCH = "FULL_MATCH_ESTABLISHED_FROM_THE_VENUE_SLUG"
R_PERIOD_UNKNOWN = "VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED"

_TRAILING_DATE = re.compile(r"-(\d{4}-\d{2}-\d{2})(?:-(.*))?$")


def _norm_token(s) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


#: SLUG PREFIXES CONFIRMED to be the venue's money-line families, from its
#: own board (`aec-mlb-...`, `atc-mlb-...`) and from
#: `copy_sports.market_type_of`. `aqc` is deliberately absent: it was
#: observed on NHL conference futures
#: (`aqc-nhl-eastconf-2027-05-19-finalq-bos`), which is not a match money
#: line at all.
MONEYLINE_PREFIXES = ("aec", "atc")

#: THE CATALOGUE'S OWN `kind` VOCABULARY, AS OBSERVED. Every row read back
#: from production carries `kind = 'side'` -- it describes the market TYPE
#: (an outcome/side market, as against a line or a total), NOT the slug's
#: prefix. Gated on the observed value only; a value outside this set
#: refuses rather than being assumed benign.
SIDE_MARKET_KINDS = ("side",)

R_PERIOD_KIND = "VENUE_CONTRACT_KIND_IS_NOT_A_CONFIRMED_MONEYLINE"
R_PERIOD_SHAPE = "VENUE_SLUG_DOES_NOT_DECOMPOSE_INTO_EVENT_AND_SIDE"
R_PERIOD_NOT_A_MATCH = "VENUE_EVENT_IS_NOT_A_TWO_PARTICIPANT_MATCH"
#: The participant count was not MEASURABLE, which is a different problem
#: from its being measured and wrong. v2's `kind` mistake was diagnosed in
#: one cycle because the three ways it could fail had three names.
R_PERIOD_TITLE = "VENUE_EVENT_TITLE_NOT_CAPTURED"


def participants_in_event_title(event_title) -> list:
    """The sides an event title names.

    THE SPLIT IS NEITHER NEW NOR MINE. `api.app`'s `shadow-mapgap` already
    reads a fixture's two sides exactly this way off `markets.event_title`,
    and names its own refusal `event_title_does_not_name_two_sides` when the
    shape does not yield two. This applies the SAME interpretation to the
    VENUE catalogue's `event_title`, so one reading of "names two sides"
    serves both sides of the crossing.

    It is a participant test and nothing more. "Alexis de la Cerda vs Cain
    Lewis" names two participants and passes; whether this lane holds a
    probability for boxing is a different gate and stays one.
    """
    ev = str(event_title or "")
    return [s.strip() for s in ev.replace(" vs. ", " vs ").split(" vs ")
            if s.strip()]


def period_of_venue_slug(market_slug, *, side=None, event_slug=None,
                         kind=None, sibling_markets=None,
                         event_title=None, participant_witness=None) -> dict:
    """FULL_MATCH, or a named refusal saying what was not established.

    TWO EARLIER VERSIONS OF THIS RULE WERE WRONG, and production caught
    both. The record is here because the third has to be read against it.

    v1 -- "the residual after the trailing date is empty or exactly the
    matched side". Necessary, nowhere near sufficient: it admitted NHL
    conference futures, a Stanley Cup trophy market and an unsupported
    prefix, all of which have an empty residual.

    v2 -- "a CONFIRMED money-line `kind` from `us_premap`, plus a sibling
    count of 2 or 3". Both halves rested on a vocabulary I had not read.
    The readback on build 1a97513 showed why: every catalogue row carries
    `kind = 'side'` -- a market TYPE, not the slug's prefix -- so gating on
    ("aec","atc") refused EVERYTHING, and `mapped 3` became `evaluated 0`
    in one cycle. And the sibling count came back 79 and 249, because
    counting distinct market slugs under an event counts the event's
    MARKETS (money line, spreads, totals, props), not its participants.

    v3 gates only on what has now been observed, and each fact is
    independent of the others:

      1 THE MARKET TYPE, from the catalogue: `kind` must be a side market
        (observed vocabulary: 'side'). That is what separates an outcome
        market from a line or a total, and it is the catalogue's own word.
      2 THE FAMILY, from the slug prefix: `aec` or `atc`, confirmed on the
        venue's own board and by `copy_sports.market_type_of`. `aqc` was
        observed on conference futures and is excluded.
      3 NO PERIOD TOKEN, by EXACT decomposition against the catalogue's own
        `event_slug` and `side_norm`. This is the structural test and it
        carries no vocabulary at all: an inning-six or second-quarter
        contract has a token between the event and the side and fails
        outright, whatever its title says.

      4 TWO PARTICIPANTS, from the catalogue's own `event_title`, split on
        " vs "/" vs. " -- the interpretation `shadow-mapgap` already uses on
        our side of the crossing. THIS IS v4, AND IT CLOSES THE HOLE v3
        NAMED. IT IS A PARTICIPANT TEST AND NOT A SCOPE TEST: a
        two-participant event sells first-half, inning and quarter
        contracts too, and step 3 is what refuses those. Step 4 without
        step 3 would admit every segment of a real fixture; step 3 without
        step 4 admits every trophy. Both are required and neither is
        redundant. A trophy or futures market decomposes just as cleanly as a
        fixture ("Stanley Cup Winner", "Qatar Airways Azerbaijan Grand Prix
        Winning Constructor", "Presidents Cup Round 3 Winner", "Eastern
        Conference Winner"), so the decomposition cannot tell a match from a
        field of entrants. A title naming exactly two sides can, and it is
        structured metadata the catalogue already carries rather than a
        fourth guess.

    THE SIBLING COUNT IS GONE, because it was measuring the wrong thing.
    `sibling_markets` is still accepted and REPORTED so the readback keeps
    showing it, and it gates nothing. `participant_witness` -- a count of
    distinct `side_norm` values on this market slug, which for an `aec`
    two-way money line IS the participant list -- is likewise reported and
    gates nothing: it is carried so a later read can promote it if
    `event_title` turns out sparse, WITHOUT a fifth version of this rule
    being guessed at first.

    MISSING METADATA FAILS CLOSED AND SAYS WHICH FIELD IS MISSING. An
    absent `event_title` is refused as VENUE_EVENT_TITLE_NOT_CAPTURED, not
    waved through and not collapsed into the generic unknown -- that
    distinction is what let v2's `kind` mistake be diagnosed in one cycle.
    """
    slug = str(market_slug or "").lower()
    out = {"market_slug": slug, "side": side, "event_slug": event_slug,
           "kind": kind, "sibling_markets": sibling_markets,
           "period": None, "residual": None, "refusals": [],
           "basis": ("CATALOGUE_SIDE_MARKET_KIND_PLUS_A_CONFIRMED_"
                     "MONEYLINE_SLUG_PREFIX_PLUS_AN_EXACT_EVENT_AND_SIDE_"
                     "DECOMPOSITION_PLUS_A_TWO_PARTICIPANT_EVENT_TITLE"),
           "what_each_check_establishes": {
               "catalogue_kind": "an outcome market, not a line or a total",
               "slug_prefix": "a confirmed money-line family",
               "exact_decomposition": (
                   "THE SCOPE. Nothing sits between the event and the "
                   "side, so no half, inning, quarter or leg token is "
                   "present. This is the only check that speaks to period"),
               "two_participant_title": (
                   "A FIXTURE RATHER THAN A FIELD OF ENTRANTS. It does "
                   "NOT independently prove full-match scope -- a "
                   "two-participant event can still sell a first-half or "
                   "an inning contract, and the decomposition is what "
                   "refuses those. The two checks are not "
                   "interchangeable and neither is redundant"),
           },
           "moneyline_prefixes": list(MONEYLINE_PREFIXES),
           "side_market_kinds": list(SIDE_MARKET_KINDS),
           "sibling_markets_gates_nothing": (
               "counting distinct market slugs under an event counts the "
               "event's MARKETS, not its participants -- observed 79 and "
               "249. Reported, never gated on"),
           "event_title": event_title,
           "participant_witness": participant_witness,
           "participant_witness_gates_nothing": (
               "a distinct-side_norm count is carried so a later read can "
               "promote it if event_title is sparse. It is not gated on "
               "before it has been observed"),
           "does_not_establish": (
               "full-match SCOPE from the title alone -- a two-participant "
               "event also sells halves, innings and quarters, and only "
               "the exact decomposition refuses those; nor that the two "
               "participants named are the two this lane holds a "
               "probability for, which is the resolver's job; nor that the "
               "competition is real rather than simulated, which the "
               "venue's own labels answer separately")}

    m = _TRAILING_DATE.search(slug)
    if not m:
        out["refusals"].append(R_PERIOD_UNKNOWN)
        out["why"] = ("the venue slug %r carries no trailing YYYY-MM-DD, so "
                      "it does not even have the shape of a dated fixture"
                      % slug)
        return out
    out["slug_date"] = m.group(1)
    out["residual"] = (m.group(2) or "").strip("-")

    # ── 1 · the market TYPE, in the catalogue's own word ─────────────
    k = str(kind or "").strip().lower()
    out["kind"] = k or None
    if not k:
        out["refusals"].append(R_PERIOD_UNKNOWN)
        out["why"] = ("the catalogue supplied no `kind`, so whether this is "
                      "a side market or a line is not established")
        return out
    if k not in SIDE_MARKET_KINDS:
        out["refusals"].append(R_PERIOD_KIND)
        out["why"] = ("the catalogue's own kind for this contract is %r; "
                      "the observed side-market vocabulary is %s. A value "
                      "outside it is not assumed benign"
                      % (k, ", ".join(repr(x) for x in SIDE_MARKET_KINDS)))
        return out

    # ── 2 · the FAMILY, from the slug's own prefix ───────────────────
    prefix = slug.split("-", 1)[0]
    out["slug_prefix"] = prefix
    if prefix not in MONEYLINE_PREFIXES:
        out["refusals"].append(R_PERIOD_KIND)
        out["why"] = ("the slug prefix is %r, which is not a confirmed "
                      "money-line family (%s). `aqc` was observed on "
                      "conference futures"
                      % (prefix, ", ".join(MONEYLINE_PREFIXES)))
        return out

    # ── 3 · NO PERIOD TOKEN, by exact decomposition ──────────────────
    ev = str(event_slug or "").strip().lower()
    if not ev:
        out["refusals"].append(R_PERIOD_UNKNOWN)
        out["why"] = ("the catalogue supplied no `event_slug`, so the slug "
                      "cannot be decomposed and anything between the event "
                      "and the side would go unseen")
        return out
    accepted = ["%s-%s" % (prefix, ev)]
    if side:
        accepted.append("%s-%s-%s" % (prefix, ev, str(side).strip().lower()))
    out["accepted_decompositions"] = accepted
    if slug not in accepted:
        out["refusals"].append(R_PERIOD_SHAPE)
        out["why"] = (
            "the market slug is %r, and the catalogue's own event and side "
            "compose to %s. Whatever sits between them -- a half, an "
            "inning, a quarter, a leg -- is a market this lane has not "
            "established, and a full-match probability may not be priced "
            "against it" % (slug, " or ".join(repr(a) for a in accepted)))
        return out

    # ── 4 · TWO PARTICIPANTS, from the catalogue's own event title ───
    # v3 named this as an open hole and it is now closed. A trophy or a
    # futures market decomposes exactly as cleanly as a fixture, so steps
    # 1-3 cannot separate them; a title naming two sides can.
    title = str(event_title or "").strip()
    parts = participants_in_event_title(title)
    out["participants_parsed"] = parts[:2]
    out["participants_named"] = len(parts)
    if not title:
        out["refusals"].append(R_PERIOD_TITLE)
        out["why"] = (
            "the catalogue supplied no `event_title` for this contract, so "
            "whether the event is a two-participant match or a field of "
            "entrants is UNMEASURED. A futures or trophy market decomposes "
            "into an event and a side exactly as a fixture does, so steps "
            "1-3 cannot tell them apart and this fails closed")
        return out
    if len(parts) != 2:
        out["refusals"].append(R_PERIOD_NOT_A_MATCH)
        out["why"] = (
            "the catalogue's own event title %r names %d side(s), not two. "
            "A full-match money line is priced between two participants; a "
            "trophy, a conference, an outright or a round winner is a field "
            "of entrants and a full-match probability may not be priced "
            "against it" % (title[:70], len(parts)))
        return out

    out["period"] = FULL_MATCH
    out["why"] = (
        "the catalogue calls this a %r market, the slug prefix %r is a "
        "confirmed money-line family, the slug decomposes exactly into its "
        "event %r%s with nothing between them, and the catalogue's own "
        "event title names exactly two participants (%s)"
        % (k, prefix, ev, (" and side %r" % side) if side else "",
           " vs ".join(repr(p) for p in parts[:2])))
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
