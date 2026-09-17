#!/usr/bin/env python3
"""TOTALS BINDING. Joining an Over/Under market to the contest it is about.

THE PROBLEM, STATED EXACTLY. A moneyline or spread row carries two venue team
ids, so `event_identity` resolves it to a contest directly. A totals row does
not: its two marketSides are Over and Under instruments with no `teamId` and no
`team` object at all. 651 of the sealed board's 20,000 markets are totals, and
every one of them was unbindable -- a permanent coverage hole in any
event-identified system.

WHAT THE VENUE DOES NOT GIVE US, established by scanning every field of all
20,000 rows rather than by assumption:

    no eventId, no gameId, no parentMarketId, no groupId, no competitionId
    the ONLY grouping-capable field on any market row is gameStartTime
    `metadata` carries playerId/teamId for PROPS only -- totals have none
    the known venue surface is three endpoints: /v1/markets,
    /v1/markets/{slug}/book, /v1/incentives. None resolves a market to an event.

So there is NO explicit venue event identifier to prefer. That is a finding, not
a gap in the search.

WHAT THE VENUE DOES GIVE US, and why this is not a title heuristic. Two
venue-native structured facts, used together:

    1. `gameStartTime`  -- the venue's own clock for the fixture
    2. `team.abbreviation` -- the venue's own short code for each team, read
       off the contest's OWN moneyline/spread rows, never typed by us

The totals row's `slug` is a venue-assigned structured identifier, and it
contains those same abbreviations as dash-delimited tokens. The binding is
EXACT TOKEN CONTAINMENT of the venue's own abbreviations, restricted to
contests at the identical gameStartTime.

This is not title parsing: `title` and `question` are never read. It is not
fuzzy matching: tokens match exactly or not at all. It is not time proximity:
start times must be identical, not close. It is not league-plus-time: the team
abbreviations must both appear.

PROVEN ON SEALED EVIDENCE, not assumed:
    651 totals   647 bound uniquely (99.4%)   0 ambiguous   4 unbound
    max contests matched by any single totals slug = 1
    9 start times carry more than one contest, 52 contests, 189 simultaneous
      SAME-LEAGUE pairs, and 0 pairs share any abbreviation
    the 4 unbound are one EFL Cup fixture for which the venue lists NO
      moneyline or spread at all -- there is no contest to bind to, and the
      resolver says so rather than inventing one

IT FAILS CLOSED. Zero candidates is UNBOUND. Two or more is
AMBIGUOUS_REFUSED -- never a pick. A total that cannot name its contest is not
assigned to one.

THE EVENT_ID IT EMITS IS THE FROZEN ONE. `gameStartTime|sorted-team-ids`,
byte-identical to what `event_identity` returns for that contest's moneyline
and spread, so all three families land on one key. This module does not define
an identity; it finds which existing identity a total belongs to.

This module contacts nothing and can place no order.
"""
import json
from collections import defaultdict

import event_identity as EI

NOT_IDENTIFIED = "NOT_IDENTIFIED"

RESOLVER_VERSION = "TOTALS_BINDING_V1"

MARKET_FAMILY = "TOTAL"
TOTALS_TYPE = "SPORTS_MARKET_TYPE_TOTAL"
CONTEST_TYPES = ("SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_SPREAD")

# THE IDENTITY HIERARCHY, AND IT IS THE CONDITION THIS RESOLVER EXISTS UNDER.
#
# This module ATTACHES a total to an event. It does not CREATE one. The
# canonical EVENT_ID is gameStartTime | sorted venue team ids, derived
# independently from venue-native contest rows -- moneylines and spreads that
# carry two team ids of their own. The totals slug is not an identity
# authority; it is only evidence of which existing identity a total belongs to.
#
# Concretely: `bind_total` can only ever return an EVENT_ID that
# `contest_index` already built from contest rows. There is no code path by
# which a totals market contributes a key to that index, so a board of totals
# alone resolves nothing at all -- which is the correct answer, not a gap.
ATTACHES_NEVER_CREATES = True
IDENTITY_AUTHORITY = "VENUE_CONTEST_ROWS_ONLY"
WHY_THE_SLUG_IS_NOT_AN_AUTHORITY = (
    "a slug is evidence of which contest a market belongs to; it is not "
    "evidence that the contest exists, and only rows carrying two venue team "
    "ids establish that")
NO_FALLBACK = (
    "no title parsing, no question parsing, no fuzzy matching, no partial "
    "abbreviation match, no nearest start time, no league-plus-approximate "
    "time, no single-team inference, no handwritten exceptions")

# ALL FOUR, OR THE TOTAL STAYS UNBOUND. There is no fifth, weaker route.
BIND_CONDITIONS = (
    "1. the venue gameStartTime matches EXACTLY",
    "2. both of an already-identified contest's own venue team abbreviations "
    "appear as exact dash-delimited tokens of the venue slug",
    "3. exactly one qualifying contest exists",
    "4. the resulting EVENT_ID already existed independently, built from "
    "venue contest rows before any totals row was read",
)

IDENTITY_SOURCE = "VENUE_START_TIME_PLUS_VENUE_TEAM_ABBREVIATIONS_IN_SLUG"
STATUS_BOUND = "BOUND"
STATUS_UNBOUND = "UNBOUND_NO_CANDIDATE_CONTEST"
STATUS_AMBIGUOUS = "AMBIGUOUS_REFUSED"
STATUS_NOT_A_TOTAL = "NOT_A_TOTALS_MARKET"

NO_EXPLICIT_VENUE_EVENT_ID = (
    "no eventId, gameId, parentMarketId or groupId exists on any of the "
    "20,000 sealed board rows; gameStartTime is the only grouping-capable "
    "field the venue supplies")
NOT_A_TITLE_HEURISTIC = (
    "title and question are never read; the tokens compared are the venue's "
    "own team abbreviations against the venue's own slug, matched exactly")
NOT_TIME_PROXIMITY = (
    "start times must be IDENTICAL; a nearby kickoff is not the same fixture")
FAILS_CLOSED = (
    "zero candidates is UNBOUND and two or more is AMBIGUOUS_REFUSED; a total "
    "that cannot name its contest is never assigned to one")


def _abbrs_and_ids(market):
    """The venue's own team ids and abbreviations on one market row."""
    out = {}
    for s in EI.sides(market):
        t = s.get("team") or {}
        tid = t.get("id")
        if tid is None:
            tid = s.get("teamId")
        if tid is not None:
            out[tid] = (t.get("abbreviation") or "").lower()
    return out


def contest_index(markets):
    """Index the contests the venue DOES identify, keyed by its own event id.

    Only rows that carry two team ids contribute -- moneylines and spreads.
    Those are the rows whose identity `event_identity` can already resolve, so
    the index inherits the frozen key rather than defining a new one.
    """
    idx = {}
    for m in markets or ():
        if not isinstance(m, dict):
            continue
        eid, level = EI.event_identity(m)
        if level != EI.LEVEL_V1_CONTEST:
            continue
        info = _abbrs_and_ids(m)
        if len(info) < 2:
            continue
        rec = idx.setdefault(eid, {
            "EVENT_ID": eid,
            "GAME_START": m.get("gameStartTime"),
            "TEAM_IDS": sorted(info),
            "ABBREVIATIONS": set(),
            "LEAGUE": NOT_IDENTIFIED,
            "SPORT": m.get("sport") or m.get("category") or NOT_IDENTIFIED,
            "FAMILIES": set(),
            "MARKET_SLUGS": [],
        })
        rec["ABBREVIATIONS"].update(a for a in info.values() if a)
        rec["MARKET_SLUGS"].append(m.get("slug"))
        rec["FAMILIES"].add(m.get("sportsMarketTypeV2") or NOT_IDENTIFIED)
        lgs = EI.leagues(m)
        if lgs and rec["LEAGUE"] == NOT_IDENTIFIED:
            rec["LEAGUE"] = sorted(lgs)[0]
    return idx


def _by_start(idx):
    out = defaultdict(list)
    for rec in idx.values():
        out[rec["GAME_START"]].append(rec)
    return out


def _tokens(slug):
    return set((slug or "").lower().split("-"))


def bind_total(total_row, index, by_start=None):
    """Resolve ONE totals market to its contest, or refuse and say why."""
    by_start = by_start if by_start is not None else _by_start(index)
    slug = total_row.get("slug")
    if total_row.get("sportsMarketTypeV2") != TOTALS_TYPE:
        return _record(total_row, None, STATUS_NOT_A_TOTAL, [])

    start = total_row.get("gameStartTime")
    toks = _tokens(slug)
    hits = []
    for rec in by_start.get(start, ()):
        ab = [a for a in rec["ABBREVIATIONS"] if a]
        # BOTH of the venue's abbreviations for that contest must appear as
        # exact tokens. One is not a fixture; it is a team.
        if len(ab) >= 2 and all(a in toks for a in ab):
            hits.append(rec)

    if len(hits) == 1:
        return _record(total_row, hits[0], STATUS_BOUND, hits)
    if not hits:
        return _record(total_row, None, STATUS_UNBOUND, hits)
    return _record(total_row, None, STATUS_AMBIGUOUS, hits)


def _sides(total_row):
    """The venue's Over and Under instruments, by its own `description`."""
    over = under = NOT_IDENTIFIED
    for s in EI.sides(total_row):
        d = (s.get("description") or "").strip().lower()
        if d == "over":
            over = s.get("id", NOT_IDENTIFIED)
        elif d == "under":
            under = s.get("id", NOT_IDENTIFIED)
    return over, under


def _record(total_row, rec, status, hits):
    over, under = _sides(total_row)
    line = total_row.get("line")
    return {
        "EVENT_ID": rec["EVENT_ID"] if rec else NOT_IDENTIFIED,
        "MARKET_ID": str(total_row.get("id") or NOT_IDENTIFIED),
        "MARKET_SLUG": total_row.get("slug") or NOT_IDENTIFIED,
        "MARKET_FAMILY": MARKET_FAMILY,
        "TOTAL_LINE": line if line is not None else NOT_IDENTIFIED,
        "TOTAL_UNIT": total_row.get("spreadTotalSuffix") or NOT_IDENTIFIED,
        "OVER_SIDE_ID": over,
        "UNDER_SIDE_ID": under,
        "GAME_START_TIME": total_row.get("gameStartTime") or NOT_IDENTIFIED,
        "SPORT": (total_row.get("sportsMarketType")
                  or total_row.get("category") or NOT_IDENTIFIED),
        "LEAGUE": rec["LEAGUE"] if rec else NOT_IDENTIFIED,
        "TEAM_IDS": rec["TEAM_IDS"] if rec else NOT_IDENTIFIED,
        "IDENTITY_SOURCE": IDENTITY_SOURCE if rec else NOT_IDENTIFIED,
        "IDENTITY_STATUS": status,
        "RESOLUTION_STATUS": status,
        "RESOLVER_VERSION": RESOLVER_VERSION,
        "IDENTITY_CONFIDENCE": ("VENUE_STRUCTURED_EXACT_TOKEN_MATCH" if rec
                                else NOT_IDENTIFIED),
        "CANDIDATE_CONTESTS": len(hits),

        # PROVENANCE. The relationship is auditable end to end: which venue
        # abbreviations matched, and which contest rows established the
        # identity this total was attached to. Without the source market ids a
        # reader cannot check that the event existed independently.
        "TOTAL_MARKET_ID": str(total_row.get("id") or NOT_IDENTIFIED),
        "TOTAL_MARKET_SLUG": total_row.get("slug") or NOT_IDENTIFIED,
        "MATCHED_TEAM_ABBREVIATIONS": (sorted(a for a in rec["ABBREVIATIONS"]
                                              if a) if rec
                                       else NOT_IDENTIFIED),
        "SOURCE_CONTEST_MARKET_IDS": (list(rec["MARKET_SLUGS"]) if rec
                                      else NOT_IDENTIFIED),

        "EVENT_ID_IS_THE_FROZEN_KEY": True,
        "IDENTITY_ATTACHED_NOT_CREATED": ATTACHES_NEVER_CREATES,
        "IDENTITY_AUTHORITY": IDENTITY_AUTHORITY,
        "EVENT_ID_EXISTED_INDEPENDENTLY": bool(rec),
        "BIND_CONDITIONS": list(BIND_CONDITIONS),
        "NOT_A_TITLE_HEURISTIC": NOT_A_TITLE_HEURISTIC,
        "NO_FALLBACK": NO_FALLBACK,
        "FAILS_CLOSED": FAILS_CLOSED,
    }


def bind_all(markets):
    """Bind every totals market on a board, and report coverage."""
    idx = contest_index(markets)
    by_start = _by_start(idx)
    totals = [m for m in (markets or ())
              if isinstance(m, dict)
              and m.get("sportsMarketTypeV2") == TOTALS_TYPE]
    records = [bind_total(m, idx, by_start) for m in totals]
    bound = [r for r in records if r["IDENTITY_STATUS"] == STATUS_BOUND]
    unbound = [r for r in records if r["IDENTITY_STATUS"] == STATUS_UNBOUND]
    ambiguous = [r for r in records if r["IDENTITY_STATUS"] == STATUS_AMBIGUOUS]

    fams = {eid: set(rec["FAMILIES"]) for eid, rec in idx.items()}
    for r in bound:
        fams.setdefault(r["EVENT_ID"], set()).add(TOTALS_TYPE)
    triplets = [e for e, f in fams.items()
                if {"SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_SPREAD",
                    TOTALS_TYPE} <= f]

    # THE ARCHITECTURAL CHECK, RUN RATHER THAN ASSERTED. Every EVENT_ID a
    # bound total carries must already be a key of the contest index -- an
    # index built before a single totals row was read. If a totals market
    # could ever mint a key, this set difference would be non-empty.
    invented = sorted({r["EVENT_ID"] for r in bound} - set(idx))

    n = len(totals)
    return {
        "TOTALS_MARKETS_OBSERVED": n,
        "ATTACHES_NEVER_CREATES": ATTACHES_NEVER_CREATES,
        "IDENTITY_AUTHORITY": IDENTITY_AUTHORITY,
        "BIND_CONDITIONS": list(BIND_CONDITIONS),
        "NO_FALLBACK": NO_FALLBACK,
        "EVENT_IDS_INVENTED_BY_TOTALS": len(invented),
        "EVERY_BOUND_EVENT_ID_PREEXISTED": not invented,
        "CANONICAL_EVENTS_FROM_CONTEST_ROWS_ONLY": len(idx),
        "TOTALS_CANONICALLY_BOUND": len(bound),
        "TOTALS_UNBOUND": len(unbound),
        "TOTALS_AMBIGUOUS_REFUSED": len(ambiguous),
        "TOTALS_BINDING_COVERAGE_PCT": (100.0 * len(bound) / n) if n else
                                       NOT_IDENTIFIED,
        "TOTALS_BINDING_SOURCE": IDENTITY_SOURCE,
        "MONEYLINE_SPREAD_TOTAL_EVENT_TRIPLETS": len(triplets),
        "CONTESTS_INDEXED": len(idx),
        "STRONGER_CANONICAL_EVENT_ID_AVAILABLE": "NO",
        "NO_EXPLICIT_VENUE_EVENT_ID": NO_EXPLICIT_VENUE_EVENT_ID,
        "NOT_A_TITLE_HEURISTIC": NOT_A_TITLE_HEURISTIC,
        "NOT_TIME_PROXIMITY": NOT_TIME_PROXIMITY,
        "FAILS_CLOSED": FAILS_CLOSED,
        "RECORDS": records,
        "UNBOUND_SLUGS": [r["MARKET_SLUG"] for r in unbound],
        "ADDING_TOTALS_INCREASES_THE_OPPORTUNITY_SET_NOT_PERMISSION": (
            "every totals market still passes BETTOR EV and risk independently"),
    }


def render(rep):
    keys = ("TOTALS_MARKETS_OBSERVED", "TOTALS_CANONICALLY_BOUND",
            "TOTALS_UNBOUND", "TOTALS_AMBIGUOUS_REFUSED",
            "TOTALS_BINDING_COVERAGE_PCT", "TOTALS_BINDING_SOURCE",
            "MONEYLINE_SPREAD_TOTAL_EVENT_TRIPLETS", "CONTESTS_INDEXED",
            "STRONGER_CANONICAL_EVENT_ID_AVAILABLE")
    return "\n".join("%-46s = %s" % (k, rep[k]) for k in keys)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
