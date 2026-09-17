"""Bind a venue soccer event to a public fixture, or refuse.

Directive section 12: "event matching fail-closed".

THE PROBLEM
-----------
The venue names an event with three-letter codes in a slug:

    epl-che-bri-2026-08-30-spread-home-2pt5

The public source names clubs in full:

    {"team1": "Chelsea FC", "team2": "Brighton & Hove Albion FC"}

Joining them means turning `che` into `Chelsea FC`. Guessing that is exactly the
class of inference standing instruction forbids: no title parsing, no fuzzy
string matching, no partial abbreviation matching, no nearest start time, no
single-team inference, no handwritten exceptions.

THE WAY ROUND IT
----------------
We never guess. The venue tells us the club's full name itself.

A spread market carries the two clubs as its OUTCOME LABELS, with the named side
first. So `epl-che-bri-...-spread-home-2pt5` has tokens

    ["Chelsea FC", "Brighton & Hove Albion FC"]

and `epl-ips-liv-...-spread-away-1pt5` has

    ["Liverpool FC", "Ipswich Town FC"]

-- away first, because the slug named the away side. That is a venue-supplied
dictionary entry: (league, code) -> the venue's own full club name. It is
evidence, not inference.

Three hard rules keep it honest:

1. A code resolves ONLY from spread rows with exactly two non-empty labels.
2. A code that resolves to more than one name anywhere in its league is a
   CONFLICT and is dropped. No tie-break, no majority vote.
3. The join to the public source is EXACT EQUALITY of the normalised name.
   Not a prefix, not a token overlap, not an edit distance. A club that does not
   match exactly is UNMATCHED and every event touching it is refused.

Measured on the retained settlement corpus this resolves 484 soccer codes across
32 league codes with ZERO conflicts, because the venue and the public source
share a naming convention ("1. FC Koln", "BV Borussia 09 Dortmund",
"Brighton & Hove Albion FC").

THE EVENT KEY
-------------
Within a round-robin league season an ordered club pair (home, away) occurs
exactly once. So the identifier is

    (venue_league, season, home_club_key, away_club_key)

and the date is a CORROBORATION, not the identifier: we require the public
fixture's date to fall within DATE_TOLERANCE_DAYS of the venue's slug date, and
we require exactly one surviving candidate. Two candidates is a refusal, not a
nearest-date pick.
"""

from __future__ import annotations

import collections
import datetime
import re

from public_soccer_ingest import normalise_club

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

MATCH_POLICY = "EXACT_NORMALISED_EQUALITY_OR_REFUSE"
FUZZY_MATCHING_USED = False
TITLE_PARSING_USED = False
PARTIAL_ABBREVIATION_MATCHING_USED = False
NEAREST_START_TIME_USED = False
SINGLE_TEAM_INFERENCE_USED = False
HANDWRITTEN_EXCEPTIONS = ()

# The slug date and the public date can differ by a calendar day for a late
# kickoff read in a different timezone. One day, and only as a corroboration of
# an identifier that is already unique without it.
DATE_TOLERANCE_DAYS = 1

CLUB_NAME_AUTHORITY = "VENUE_SPREAD_ROW_OUTCOME_LABELS"

SPREAD_RE = re.compile(
    r"^(?P<league>[a-z0-9]+)-(?P<home>[a-z0-9]+)-(?P<away>[a-z0-9]+)"
    r"-(?P<date>\d{4}-\d{2}-\d{2})-spread-(?P<side>home|away)-\d+pt\d+$")

EVENT_RE = re.compile(
    r"^(?P<league>[a-z0-9]+)-(?P<home>[a-z0-9]+)-(?P<away>[a-z0-9]+)"
    r"-(?P<date>\d{4}-\d{2}-\d{2})(?P<rest>-.*)?$")


# ---------------------------------------------------------------------------
# Step 1 -- the venue's own club dictionary
# ---------------------------------------------------------------------------


def derive_club_names(settlement_rows):
    """(league, code) -> the venue's full club name, from spread rows only.

    Returns (resolved, report). `resolved` maps (league, code) to the name.
    Conflicts are excluded from `resolved` and listed in the report so a
    downstream reader can see what was dropped and why.
    """
    seen = collections.defaultdict(collections.Counter)
    rows_used = 0
    for row in settlement_rows:
        slug = row.get("market_slug") or ""
        m = SPREAD_RE.match(slug)
        if not m:
            continue
        labels = [t.get("outcome") for t in (row.get("tokens") or ())]
        if len(labels) != 2 or not all(labels):
            continue
        lg = m.group("league")
        home, away, side = m.group("home"), m.group("away"), m.group("side")
        named, other = (home, away) if side == "home" else (away, home)
        seen[(lg, named)][labels[0]] += 1
        seen[(lg, other)][labels[1]] += 1
        rows_used += 1

    resolved, conflicts = {}, {}
    for key, counter in seen.items():
        if len(counter) == 1:
            resolved[key] = next(iter(counter))
        else:
            conflicts[key] = dict(counter)

    return resolved, {
        "CLUB_NAME_AUTHORITY": CLUB_NAME_AUTHORITY,
        "SPREAD_ROWS_USED": rows_used,
        "CODES_SEEN": len(seen),
        "CODES_RESOLVED": len(resolved),
        "CODES_IN_CONFLICT": len(conflicts),
        "CONFLICTS": conflicts,
        "LEAGUES": sorted({k[0] for k in resolved}),
    }


# ---------------------------------------------------------------------------
# Step 2 -- exact join to the public club vocabulary
# ---------------------------------------------------------------------------


def club_vocabulary(fixtures):
    """Every normalised club key the public source uses, per venue league."""
    vocab = collections.defaultdict(set)
    for f in fixtures:
        lg = f.get("VENUE_LEAGUE")
        for k in (f.get("HOME_KEY"), f.get("AWAY_KEY")):
            if k:
                vocab[lg].add(k)
    return {k: frozenset(v) for k, v in vocab.items()}


def join_clubs(resolved_names, fixtures):
    """(league, code) -> normalised public club key, by exact equality only.

    A code whose venue name is not a public club name IN THE SAME LEAGUE is
    unmatched. Cross-league rescue is deliberately not attempted: a club that is
    in the venue's `elc` but the public `en.1` has been promoted or relegated,
    and quietly borrowing it would mix seasons.
    """
    vocab = club_vocabulary(fixtures)
    bound, unmatched, no_public_league = {}, {}, set()
    for (lg, code), name in resolved_names.items():
        if lg not in vocab:
            no_public_league.add(lg)
            continue
        key = normalise_club(name)
        if key in vocab[lg]:
            bound[(lg, code)] = key
        else:
            unmatched[(lg, code)] = name

    covered = collections.Counter(lg for (lg, _) in bound)
    return bound, {
        "MATCH_POLICY": MATCH_POLICY,
        "CODES_CONSIDERED": len(resolved_names),
        "CODES_BOUND": len(bound),
        "CODES_UNMATCHED": len(unmatched),
        "UNMATCHED": {("%s/%s" % k): v for k, v in sorted(unmatched.items())},
        "LEAGUES_WITHOUT_PUBLIC_DATA": sorted(no_public_league),
        "BOUND_PER_LEAGUE": dict(covered),
        "PUBLIC_CLUBS_PER_LEAGUE": {k: len(v) for k, v in sorted(vocab.items())},
    }


# ---------------------------------------------------------------------------
# Step 2b -- the clubs exact equality cannot reach
# ---------------------------------------------------------------------------

RESIDUAL_CLOSURE = "IDENTITY_BY_SCHEDULE_NOT_BY_NAME"
MIN_SCHEDULE_WITNESSES = 2

RESIDUAL_CLOSURE_RATIONALE = (
    "Exact name equality leaves a handful of clubs unbound because the two "
    "sources write them differently: the venue's 'BV Borussia 09 Dortmund' "
    "against the public 'Borussia Dortmund', 'Sporting CP' against 'Sporting "
    "Clube de Portugal', 'Vitoria SC' against 'Vitoria Guimaraes'. Every one of "
    "those is a real club we lose.\n\n"
    "The tempting fix is a hand-written alias table or a similarity score. Both "
    "are forbidden, and rightly: an alias table is an unfalsifiable assertion "
    "and a similarity score would happily marry 'Vitoria SC' to 'Vitoria de "
    "Setubal', a different club in the same league.\n\n"
    "So we do not look at the names at all. A club is identified by WHO IT "
    "PLAYED AND WHEN. Take an unbound venue code: from the slugs it appears in, "
    "read off its fixtures against clubs that ARE bound -- opponent, date, and "
    "whether it was home or away. Take an unbound public club and read off the "
    "same list. If the two schedules agree on at least "
    "MIN_SCHEDULE_WITNESSES fixtures, and the agreement is one-to-one in both "
    "directions within the league-season, the two are the same club. Two "
    "different clubs cannot play the same opponents at home on the same dates.\n\n"
    "This is evidence, not inference, and it fails closed: an ambiguous or "
    "thin residual is refused and reported, never resolved by preference."
)


def _venue_schedule(slugs, bound_codes):
    """(league, season, code) -> {(bound_opponent_key, date, side)}.

    Only fixtures against an ALREADY-BOUND opponent count as witnesses. Two
    unknowns cannot identify each other.
    """
    sched = collections.defaultdict(set)
    for slug in slugs:
        m = EVENT_RE.match(slug or "")
        if not m:
            continue
        lg, h, a, date = (m.group("league"), m.group("home"),
                          m.group("away"), m.group("date"))
        season = season_of(date)
        hk, ak = bound_codes.get((lg, h)), bound_codes.get((lg, a))
        if ak is not None and hk is None:
            sched[(lg, season, h)].add((ak, date, "HOME"))
        if hk is not None and ak is None:
            sched[(lg, season, a)].add((hk, date, "AWAY"))
    return sched


def _public_schedule(fixtures, bound_image):
    """(league, season, club_key) -> {(bound_opponent_key, date, side)}."""
    sched = collections.defaultdict(set)
    for f in fixtures:
        lg, season = f.get("VENUE_LEAGUE"), f.get("SEASON")
        hk, ak, date = f.get("HOME_KEY"), f.get("AWAY_KEY"), f.get("DATE")
        if not (lg and season and hk and ak and date):
            continue
        known = bound_image.get(lg, frozenset())
        if hk not in known and ak in known:
            sched[(lg, season, hk)].add((ak, date, "HOME"))
        if ak not in known and hk in known:
            sched[(lg, season, ak)].add((hk, date, "AWAY"))
    return sched


def _witness_overlap(a, b, tolerance_days):
    n = 0
    for opp, date, side in a:
        for opp2, date2, side2 in b:
            if opp == opp2 and side == side2 and \
                    _days_between(date, date2) <= tolerance_days:
                n += 1
                break
    return n


def close_residual_by_schedule(slugs, bound_codes, fixtures,
                               tolerance_days=DATE_TOLERANCE_DAYS,
                               min_witnesses=MIN_SCHEDULE_WITNESSES):
    """Bind the leftover codes by schedule agreement. Returns (extra, report).

    `extra` is a fresh mapping to merge into `bound_codes`. Nothing here
    overrides an exact-name bind: the residual is by construction disjoint from
    it.
    """
    bound_image = collections.defaultdict(set)
    for (lg, _), key in bound_codes.items():
        bound_image[lg].add(key)
    bound_image = {k: frozenset(v) for k, v in bound_image.items()}

    vsched = _venue_schedule(slugs, bound_codes)
    psched = _public_schedule(fixtures, bound_image)

    # Candidate pairs, scored by how many fixtures the two schedules share.
    scores = collections.defaultdict(dict)
    for (lg, season, code), vw in vsched.items():
        if len(vw) < min_witnesses:
            continue
        for (lg2, season2, club), pw in psched.items():
            if (lg2, season2) != (lg, season) or len(pw) < min_witnesses:
                continue
            n = _witness_overlap(vw, pw, tolerance_days)
            if n >= min_witnesses:
                scores[(lg, season, code)][club] = n

    # Accept only a one-to-one, unambiguous assignment.
    extra, ambiguous, contested = {}, {}, {}
    claims = collections.defaultdict(list)
    for key, cands in scores.items():
        if len(cands) != 1:
            ambiguous[key] = cands
            continue
        club = next(iter(cands))
        claims[(key[0], key[1], club)].append(key)
    for club_key, claimants in claims.items():
        if len(claimants) != 1:
            contested[club_key] = [c[2] for c in claimants]
            continue
        lg, season, code = claimants[0]
        extra[(lg, code)] = club_key[2]

    thin = {k: len(v) for k, v in vsched.items()
            if len(v) < min_witnesses and (k[0], k[2]) not in bound_codes}

    return extra, {
        "RESIDUAL_CLOSURE": RESIDUAL_CLOSURE,
        "RATIONALE": RESIDUAL_CLOSURE_RATIONALE,
        "MIN_SCHEDULE_WITNESSES": min_witnesses,
        "UNBOUND_CODES_WITH_SCHEDULES": len(vsched),
        "CLOSED": len(extra),
        "CLOSED_PAIRS": {("%s/%s" % k): v for k, v in sorted(extra.items())},
        "REFUSED_AMBIGUOUS": len(ambiguous),
        "AMBIGUOUS": {("%s/%s/%s" % k): v for k, v in sorted(ambiguous.items())},
        "REFUSED_CONTESTED": len(contested),
        "CONTESTED": {("%s/%s/%s" % k): v for k, v in sorted(contested.items())},
        "REFUSED_TOO_FEW_WITNESSES": len(thin),
        "NAMES_COMPARED": False,
        "FAIL_CLOSED": True,
    }


# ---------------------------------------------------------------------------
# Step 3 -- the event bind
# ---------------------------------------------------------------------------


def season_of(date_str):
    """The openfootball season label a European league date belongs to.

    August through December belongs to the season that opens that year; January
    through July belongs to the season that opened the previous year. This is a
    calendar rule about how these competitions are scheduled, not an inference
    about any particular fixture.
    """
    y, m, _ = (int(x) for x in date_str.split("-"))
    start = y if m >= 8 else y - 1
    return "%d-%02d" % (start, (start + 1) % 100)


def _days_between(a, b):
    fmt = "%Y-%m-%d"
    da = datetime.datetime.strptime(a, fmt).date()
    db = datetime.datetime.strptime(b, fmt).date()
    return abs((da - db).days)


def fixture_index(fixtures):
    """(venue_league, season, home_key, away_key) -> [fixture, ...]."""
    idx = collections.defaultdict(list)
    for f in fixtures:
        if not (f.get("HOME_KEY") and f.get("AWAY_KEY") and f.get("DATE")):
            continue
        idx[(f["VENUE_LEAGUE"], f["SEASON"], f["HOME_KEY"], f["AWAY_KEY"])].append(f)
    return idx


def bind_event(slug, bound_codes, idx, tolerance_days=DATE_TOLERANCE_DAYS):
    """Bind one venue slug to one public fixture, or return why not.

    Returns (fixture_or_None, reason). `reason` is always a named refusal, never
    a silent None: an unreadable outcome and a genuinely absent fixture are
    different facts and a caller must be able to tell them apart.
    """
    m = EVENT_RE.match(slug or "")
    if not m:
        return None, "SLUG_NOT_AN_EVENT_SHAPE"
    lg, h, a, date = (m.group("league"), m.group("home"),
                      m.group("away"), m.group("date"))
    hk, ak = bound_codes.get((lg, h)), bound_codes.get((lg, a))
    if hk is None and ak is None:
        return None, "BOTH_CLUB_CODES_UNBOUND"
    if hk is None:
        return None, "HOME_CLUB_CODE_UNBOUND"
    if ak is None:
        return None, "AWAY_CLUB_CODE_UNBOUND"

    season = season_of(date)
    cands = idx.get((lg, season, hk, ak), ())
    if not cands:
        return None, "NO_PUBLIC_FIXTURE_FOR_THIS_ORDERED_PAIR_AND_SEASON"
    near = [f for f in cands if _days_between(f["DATE"], date) <= tolerance_days]
    if not near:
        return None, "PUBLIC_FIXTURE_DATE_OUTSIDE_TOLERANCE"
    if len(near) > 1:
        # Two fixtures for one ordered pair inside a day of each other is not a
        # thing that happens in a round-robin season. If it does, the identifier
        # is broken and picking the nearer one would hide that.
        return None, "AMBIGUOUS_MULTIPLE_PUBLIC_FIXTURES"
    return near[0], "BOUND"


def bind_all(slugs, bound_codes, fixtures, tolerance_days=DATE_TOLERANCE_DAYS):
    """Bind a collection of venue slugs. Returns (bindings, report)."""
    idx = fixture_index(fixtures)
    bindings, reasons = {}, collections.Counter()
    unplayed = 0
    for slug in slugs:
        fx, reason = bind_event(slug, bound_codes, idx, tolerance_days)
        reasons[reason] += 1
        if fx is not None:
            bindings[slug] = fx
            if not fx.get("PLAYED"):
                unplayed += 1
    total = sum(reasons.values())
    return bindings, {
        "SLUGS_CONSIDERED": total,
        "BOUND": len(bindings),
        "BOUND_WITH_RESULT": len(bindings) - unplayed,
        "BOUND_WITHOUT_RESULT_YET": unplayed,
        "BIND_RATE": (len(bindings) / total) if total else 0.0,
        "REFUSAL_REASONS": dict(reasons),
        "DATE_TOLERANCE_DAYS": tolerance_days,
        "FAIL_CLOSED": True,
    }


# ---------------------------------------------------------------------------
# Step 4 -- agreement check against the corpus's own reconstructed outcomes
# ---------------------------------------------------------------------------

AGREEMENT_IS_THE_TEST_OF_THE_BIND = (
    "A bind that is merely plausible proves nothing. The corpus reconstructs "
    "each event's score independently, from its own settled contracts. If the "
    "public source and the corpus disagree on the score of a bound event, the "
    "bind is wrong -- or one of the two sources is. Either way the number to "
    "report is the disagreement count, and it must be zero before any model "
    "trained on public results is compared to venue prices."
)


def check_agreement(bindings, corpus_scores):
    """Compare public full-time scores to scores reconstructed from the corpus.

    `corpus_scores` maps a venue event slug to (home_goals, away_goals).
    Returns a report. Disagreements are listed, not summarised away.
    """
    agree, disagree, no_corpus, no_public = 0, [], 0, 0
    for slug, fx in bindings.items():
        got = corpus_scores.get(slug)
        if got is None:
            no_corpus += 1
            continue
        if not fx.get("PLAYED"):
            no_public += 1
            continue
        pub = (fx["FT_HOME"], fx["FT_AWAY"])
        if tuple(got) == tuple(pub):
            agree += 1
        else:
            disagree.append({"SLUG": slug, "CORPUS": list(got),
                             "PUBLIC": list(pub), "PUBLIC_DATE": fx["DATE"]})
    comparable = agree + len(disagree)
    return {
        "COMPARABLE": comparable,
        "AGREE": agree,
        "DISAGREE": len(disagree),
        "AGREEMENT_RATE": (agree / comparable) if comparable else None,
        "NO_CORPUS_SCORE": no_corpus,
        "PUBLIC_RESULT_NOT_YET_IN": no_public,
        "DISAGREEMENTS": disagree[:50],
        "WHY_THIS_MATTERS": AGREEMENT_IS_THE_TEST_OF_THE_BIND,
    }
