"""Worker: THE EXTERNAL-VALUATION SHADOW, ACTUALLY RUNNING IN PRODUCTION.

WHAT WAS WRONG BEFORE THIS FILE EXISTED. `bettor_external_shadow.evaluate`
was called by a test and by a research runner, and by nothing else. The
entry inputs it forwards into `bettor_entry_gate.admit` were therefore
supplied only by test fixtures. A function that accepts the new inputs in
a test is not a connected path -- the runtime caller has to supply them,
and until this loop there was no runtime caller at all.

ONE CYCLE, IN ORDER, AND IT FAILS CLOSED AT EVERY STEP:

    1. CONTROL ROW. Absent, false, malformed or unreadable all mean
       STOPPED. Absence is not permission.
    2. TABLES, against the live catalog rather than the migrations
       directory -- migration 100 sat committed at HEAD for hours while
       production had none of its tables.
    3. CREDENTIAL, by presence only. Never read into a log, a return
       value or an error message.
    4. FRESH ODDS from the provider, for the supported sports only.
    5. VENUE CONTRACT, exactly, via `bettor_venue_mapping`.
    6. CONTEMPORANEOUS VENUE QUOTE -- ask and depth, read in the same
       cycle as the odds, because comparing a fresh probability against a
       stale price is the error that makes everything look profitable.
    7. FEES from the production schedule. Never defaulted.
    8. THE REAL GATE, with the real execution and risk checks.
    9. PERSIST every evaluation, cleared or refused, exactly once.

IT PLACES NO ORDER. There is no venue write in this module's import
closure: `bettor_live_read` is a reader, and the only writer reached is
`bettor_external_shadow.persist`, whose table CHECKs order_submitted is
FALSE. That CHECK is a backstop, not the control -- the control is that no
submit function is imported here or reachable from what is.

IT DOES NOT TOUCH RN1-SEEDED MANAGEMENT. Different experiment id,
different table, different cursor. The frozen pairing policy and the 16%
second-half trigger are not consulted, imported or read.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import os
import re
import time

from .. import bettor_entry_execution as entryx
from .. import bettor_entry_inventory as inv
from .. import bettor_research_shadow as rsh
from .. import bettor_external_shadow as ext
from .. import bettor_fixture_metadata as fmeta_mod
from .. import bettor_fixture_store as fstore
# THE SUPPORTED PARSER FOR THIS VENUE'S CLOCK, imported rather than
# rewritten. `_parse_ts` handles the bare `Z` and the venue's variable
# fractional precision (up to nanoseconds), and refuses a naive stamp.
# A second implementation here is exactly how the first one came to be
# `float()`, which refused every real value the venue sends.
from ..bettor_market_stream import _parse_ts as _stream_parse_ts
from .. import bettor_pinnacle_devig as devig
from .. import bettor_venue_currency as vc
from .. import bettor_venue_mapping as vmap
from .. import bettor_venue_native_identity as vnat
from .. import bettor_venue_realism as vreal
from .. import bettor_venue_settlement as vset

log = logging.getLogger(__name__)

CONTROL_KEY = ext.CONTROL_KEY
ENV_FLAG = "EXT_PINNACLE_SHADOW"

#: ── WHICH COMPETITIONS THIS LOOP ASKS THE PROVIDER FOR ───────────────
#:
#: Basketball and hockey are absent BECAUSE THE BOOK DOES NOT QUOTE THEM on our
#: plan (NBA 0/41, NHL 0/33, measured run 35933793563) -- asking anyway would
#: spend credits to be refused.
#:
#: AND `soccer_epl` IS NOW ABSENT FOR THE SAME KIND OF REASON, MEASURED ON THE
#: OTHER SIDE (2026-09-28, research-sql run 258). The cycle on build c3d0cfc
#: reported `soccer_epl: provider_events 20, with_pinnacle_h2h 20,
#: mapped_to_a_venue_contract 0`, and I spent a session treating that as a
#: resolver defect. It is not. Our leading token against the US venue's own
#: catalogue:
#:
#:     our token   our open markets   us_premap rows carrying it
#:     lal                      468                            0
#:     epl                      387                            0
#:     sea                      321                         1812
#:     mls                      316                          460
#:     col                      268                         1256
#:     por                      166                          726
#:     tur                      161                          570
#:     bra                      225                          412
#:     arg                      210                          390
#:
#: The US venue does not list the English Premier League. What it lists is 420
#: rows across 70 events titled "eBattles: Arsenal vs. Chelsea", classified
#: `efootball_team_full_time_winner` -- simulations bearing real club names,
#: which `bettor_venue_realism` now refuses at the resolver precisely because
#: every other check the lane makes would pass on one.
#:
#: So the twenty EPL events were never reachable, and the ~20 credits a cycle
#: spent fetching them bought a refusal. The competitions with real US coverage
#: are in the right-hand column, and they are what this set now names.
#:
#: THE PROVIDER'S KEY NAMES ARE CANDIDATES, NOT FACTS. I have not read the
#: provider's catalogue from this container -- it has no egress and no key -- so
#: every soccer key below is a CANDIDATE confirmed at cycle start against
#: `/v4/sports`, which is UNMETERED and therefore free to check. A candidate the
#: provider does not list is reported as PROVIDER_DOES_NOT_LIST_THIS_COMPETITION
#: and never fetched. Writing a guessed key straight into a metered fetch is how
#: the EPL spend happened; confirming first costs nothing.
#:
#: `baseball_mlb` is CONFIRMED: it is the only key that has ever produced a
#: valuation row here (all 1,126 of them, sport_family `baseball`).
SPORTS_CONFIRMED = (("baseball_mlb", "baseball"),)

#: ── WHY THIS IS A TOKEN MAP AND NOT A LIST OF COMPETITIONS ───────────
#:
#: MY FIRST ATTEMPT AT THIS LIST WAS WRONG AND THE MEASUREMENT CAUGHT IT. I
#: ordered seven soccer competitions by `us_premap` rows matching
#: `'%-<token>-%'`: Serie A 1812, Conference League 1256, Primeira Liga 726,
#: Turkey 570. Run 259 asked for the same counts with the token in the slug's
#: LEAGUE POSITION instead of anywhere in it:
#:
#:     token   anywhere   in the league position   what it matched
#:     col         1244                        0   aec-mlb-col-cws  (Colorado)
#:     sea         1080                        0   aec-mlb-laa-sea  (Seattle)
#:     por          712                        0   asc-unl-den-por  (Portugal, UNL)
#:     tur          570                        0   asc-unl-bel-tur  (Turkey, UNL)
#:     arg          378                        0   atc-ebfwca-arg-bra (eBattles)
#:     lmx          576                      576   real Liga MX
#:     mls          460                      460   real MLS
#:
#: Six of the seven had ZERO coverage. A team abbreviation and a league token
#: share a slug and the pattern could not tell them apart, so I nearly pointed a
#: metered credit budget at Serie A on the strength of Seattle Mariners rows.
#:
#: AND THE REAL BOARD IS SEASONAL, WHICH IS THE ARCHITECTURAL POINT. What the
#: venue actually lists, by league position and event count, is almost entirely
#: NATIONAL-TEAM football: unl 39 events, intf 15, cnl 10, uwcl 9, then club
#: competitions in single figures (arg2 8, lco 7, brb 6, uslc 5, irl1 5, par2 4,
#: mls 3, lmx 3). Late September is an international window. A hard-coded list
#: of club competitions would have been wrong in a fortnight even if I had
#: measured it correctly, so the set is DERIVED from the venue's own board at
#: cycle time (`venue_soccer_competitions`) and this map only says which
#: provider key corresponds to a venue league token.
#:
#: The provider's key names are still MINE TO GUESS -- this container has no
#: provider egress -- so each is confirmed against the provider's own unmetered
#: catalogue before a metered call is made on it.
#: EVERY ENTRY CARRIES THE VENUE FIXTURES THAT SUPPORT IT (run 260, statement 4).
#: A key that exists in the provider's catalogue is not the competition; the
#: fixtures are what say which competition it is, and two of my thirteen guesses
#: were refuted by reading them.
VENUE_TOKEN_TO_PROVIDER_KEY = {
    # national teams: Belgium vs France, Germany vs Greece, Spain vs Croatia
    "unl": "soccer_uefa_nations_league",
    # Columbus Crew vs Inter Miami, Seattle Sounders vs Sporting KC
    "mls": "soccer_usa_mls",
    # Club Leon vs FC Juarez, Club Necaxa vs CF America
    "lmx": "soccer_mexico_ligamx",
    # AS Roma vs FC Barcelona, Man City WFC vs Real Madrid Femenino
    "uwcl": "soccer_uefa_champs_league_women",
    # Curacao vs Nicaragua, Jamaica vs Honduras -- CONCACAF national teams
    "cnl": "soccer_concacaf_nations_league",
    # Brooklyn FC vs Detroit City, FC Tulsa vs New Mexico United
    "uslc": "soccer_usa_usl_championship",
    # CA Atlanta vs Agropecuario, Club Almagro vs Nueva Chicago -- second tier
    "arg2": "soccer_argentina_primera_nacional",
    # America FC vs EC Juventude, Botafogo vs Ponte Preta -- Serie B
    "brb": "soccer_brazil_serie_b",
    # Atletico Nacional vs Junior, Independiente Medellin vs Millonarios
    "lco": "soccer_colombia_primera_a",
    # Defensor Sporting vs Danubio, Montevideo City Torque vs Penarol
    "uru1": "soccer_uruguay_primera_division",
    # Bay FC vs Orlando Pride, Utah Royals vs North Carolina Courage
    "nwsl": "soccer_usa_nwsl",
}

#: ── THE TWO MAPPINGS THE FIXTURES REFUTED, AND WHY THEY ARE OUT ──────
#:
#: I mapped these from the token alone and both were wrong by one league tier.
#: Codex singled out the first; reading the venue's fixtures refuted the second
#: as well, which is the argument for validating ALL of them the same way rather
#: than only the one that was challenged.
VENUE_TOKENS_WITH_A_REFUTED_MAPPING = {
    "engnl": {
        "i_mapped_it_to": "soccer_england_league2",
        "the_venue_fixtures_are": ("AFC Fylde vs Carlisle United, Barrow AFC vs "
                                   "Scunthorpe United, Boreham Wood vs "
                                   "Kidderminster Harriers, Gateshead vs "
                                   "Altrincham, Hornchurch vs Aldershot Town"),
        "which_competition_that_is": ("the English NATIONAL LEAGUE -- the fifth "
                                      "tier, and Hornchurch and Worthing are "
                                      "National League South"),
        "why_it_is_out": ("`soccer_england_league2` is the EFL League Two, the "
                          "fourth tier: a different competition with different "
                          "clubs. The provider may well list that key, which is "
                          "exactly why key existence cannot confirm a mapping"),
    },
    "irl1": {
        "i_mapped_it_to": "soccer_league_of_ireland",
        "the_venue_fixtures_are": ("Athlone Town vs Cork City, Bray Wanderers "
                                   "vs Cobh Ramblers, Finn Harps vs Kerry FC, "
                                   "Treaty United vs University College Dublin"),
        "which_competition_that_is": ("the League of Ireland FIRST Division, "
                                      "the second tier"),
        "why_it_is_out": ("`soccer_league_of_ireland` is the Premier Division. "
                          "Not challenged by anyone -- found by applying the "
                          "same fixture check to every entry instead of only "
                          "the one that was questioned"),
    },
}

R_MAPPING_FIXTURES_DO_NOT_MATCH = (
    "THE_PROVIDER_COMPETITION_FIXTURES_DO_NOT_MATCH_THE_VENUE_COMPETITION")
R_MAPPING_UNCONFIRMABLE = (
    "THE_MAPPING_COULD_NOT_BE_CONFIRMED_AGAINST_ANY_FIXTURE")


#: ── AFFILIATION MARKERS, THE ONLY SAFE THING TO DROP ─────────────────
#:
#: THE DEFECT THAT TAUGHT THIS, AND IT WAS MINE. The first version dropped
#: "United", "City", "Town", "Athletic" and "Rovers" as club-form noise, on the
#: reasoning that they appear across every division and so discriminate nothing.
#: Codex's counterexample:
#:
#:     provider  Manchester United vs Manchester City
#:     venue     Manchester United vs Liverpool
#:     -> ok=True, strong_matches=1, shared=["manchester"]
#:
#: Both provider teams normalised to {manchester}, and ONE venue token satisfied
#: BOTH sides: a two-sided match that is one shared token counted twice,
#: confirming a mapping between two DIFFERENT fixtures on a shared city name --
#: the exact inference this module forbids elsewhere.
#:
#: "United" and "City" are not noise; they are the entire difference between two
#: clubs in one city. What IS safe to drop is an AFFILIATION MARKER: the legal
#: form and society suffixes that appear on one source's rendering and not the
#: other's (Inter Miami CF / Inter Miami). Dropping those is a rendering
#: difference. Dropping a discriminating word is a different team.
#:
#: Every drop is REPORTED on the result, so a match can be checked against what
#: was normalised away rather than trusted.
AFFILIATION_MARKERS = frozenset((
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "sv", "vfb", "vfl",
    "bsc", "tsg", "fsv", "cd", "ca", "cr", "ec", "sd", "ud", "ad", "aa",
    "csd", "club", "clube", "futbol", "football", "soccer", "calcio",
    "the", "de", "del", "da", "do", "of",
))

#: One calendar day of tolerance: the venue dates some fixtures a day either
#: side of the provider -- the C9 board carries the bookmaker's 2026-09-10
#: against the venue's 2026-09-09. The same allowance `premap.resolve` makes.
FIXTURE_DATE_TOLERANCE_DAYS = 1

R_SIDES_NOT_TWO = "AN_EVENT_TITLE_DID_NOT_NAME_TWO_DISTINCT_SIDES"


def _fold(text) -> str:
    """Lowercase, strip accents and punctuation. Nothing else."""
    import re as _re
    import unicodedata

    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return _re.sub(r"[^a-z0-9 ]+", " ", t.lower())


def _team_tokens(name, family=None):
    """One team's DISTINGUISHING tokens, and what was dropped.

    Affiliation markers are dropped only when something remains: a team whose
    whole rendered name is a marker keeps it, because an empty token set would
    match every other team. For a college family (football) the venue-native
    resolver's own rendering rewrites apply first -- a poll-rank prefix
    ("#5 Alabama") is dropped and "St." reads as State / leading Saint -- so a
    ranked venue title still confirms the competition (cand22).
    """
    if family is not None:
        # imported here so this helper stays self-contained (it is lifted on
        # its own by tests/test_held_feed_boundary.py)
        from .. import bettor_venue_native_identity as _vn
    if family is not None and family in _vn.NICKNAME_QUALIFIED_FAMILIES:
        # AN NFL TEAM reads as its full rendering (cand24): the venue's `nfl`
        # titles say "LA Rams vs. PHI Eagles", the provider "Los Angeles
        # Rams" -- one team, by NFL_TEAMS, whose nickname must agree.
        nfl = _vn.nfl_canonical_tokens(name)
        if nfl is not None:
            return frozenset(nfl), []
        name = _vn._RANK_PREFIX.sub("", str(name or ""))
        raw = _vn._college_tokens([w for w in _fold(name).split() if w])[0]
    else:
        raw = [w for w in _fold(name).split() if w]
    kept = [w for w in raw if w not in AFFILIATION_MARKERS and len(w) > 1]
    dropped = [w for w in raw if w not in kept]
    if not kept:
        kept, dropped = raw, []
    return frozenset(kept), dropped


def _sides_of(title):
    """The two sides of an event title, or None when it does not name two.

    A title yielding anything but exactly two non-empty sides cannot support a
    one-to-one match, and guessing which half is which is how a shared city
    name became a confirmation.
    """
    t = _fold(title)
    for sep in (" vs ", " v ", " at ", " versus "):
        if sep in t:
            parts = [x.strip() for x in t.split(sep) if x.strip()]
            if len(parts) == 2:
                return parts
    return None


#: ── QUALIFIERS THAT MAKE IT A DIFFERENT TEAM ─────────────────────────
#:
#: THE SECOND ROUND OF THE SAME DEFECT. Fixing the Manchester case with a
#: one-to-one assignment left unrestricted token CONTAINMENT in place, and Codex
#: showed what containment still accepts:
#:
#:     Arsenal vs Chelsea  ->  Arsenal Women vs Chelsea Women
#:     Arsenal vs Chelsea  ->  Arsenal U21 vs Chelsea U21
#:
#: {arsenal} is contained in {arsenal, women}, so each side "matched" and the
#: assignment was one-to-one -- a correct assignment between the WRONG teams.
#: A men's first team and a women's team are different competitions entirely,
#: and a Pinnacle price for one against a venue contract on the other is the
#: same class of error as the eBattles simulation.
#:
#: So containment is no longer unrestricted: a qualifier present on one side and
#: absent on the other makes them different teams. Containment still covers the
#: case it was for -- an affiliation marker dropped from one rendering -- because
#: markers are removed before comparison and are not qualifiers.
SQUAD_QUALIFIERS = frozenset((
    # gender
    "women", "womens", "ladies", "feminin", "feminine", "femenino", "femenina",
    "frauen", "damen", "dames", "kvinner", "kobiet", "w",
    # age group
    "u16", "u17", "u18", "u19", "u20", "u21", "u22", "u23", "youth", "junior",
    "juniors", "academy", "primavera", "jugend",
    # reserve and secondary sides
    "ii", "iii", "b", "reserves", "reserve", "amateur", "amateure",
    "castilla", "atletic",
))

R_QUALIFIER_MISMATCH = "ONE_SIDE_CARRIES_A_SQUAD_QUALIFIER_THE_OTHER_DOES_NOT"


def _qualifiers(tokens) -> frozenset:
    return frozenset(t for t in tokens if t in SQUAD_QUALIFIERS)


def _same_team(a_tokens, b_tokens) -> bool:
    """Are these two renderings the SAME team?

    Two conditions, and the second is the repair:

      1. equality after normalisation, or one token set contained in the other
         -- which covers "Inter Miami" against "Inter Miami CF", and does NOT
         cover "Manchester United" against "Manchester City" because `united`
         and `city` both survive;
      2. THE SAME SQUAD QUALIFIERS. "Arsenal" is contained in "Arsenal Women"
         and they are not the same team, so a qualifier on one side and not the
         other refuses however well the rest matches.
    """
    if not a_tokens or not b_tokens:
        return False
    if _qualifiers(a_tokens) != _qualifiers(b_tokens):
        return False
    return (a_tokens == b_tokens or a_tokens <= b_tokens
            or b_tokens <= a_tokens)


def _same_fixture_day(provider_at, venue_at) -> dict:
    """Do these datings refer to the same meeting?

    A verdict rather than a bool: "no dating supplied" is not the same answer as
    "the datings disagree", and only the second may refuse a return leg while
    the first must be reported as a limit of the confirmation.
    """
    import datetime as _dt

    def _day(v):
        if v is None:
            return None
        if isinstance(v, _dt.datetime):
            return v.date()
        if isinstance(v, _dt.date):
            return v
        try:
            return _dt.date.fromisoformat(str(v)[:10])
        except ValueError:
            return None

    a, b = _day(provider_at), _day(venue_at)
    if a is None or b is None:
        return {"comparable": False, "same": None,
                "why": ("no dating on one side, so a repeated meeting between "
                        "these teams is not distinguished from this one")}
    gap = abs((a - b).days)
    return {"comparable": True, "same": gap <= FIXTURE_DATE_TOLERANCE_DAYS,
            "provider_day": a.isoformat(), "venue_day": b.isoformat(),
            "gap_days": gap, "tolerance_days": FIXTURE_DATE_TOLERANCE_DAYS}


#: ── WHAT ACTUALLY CONFIRMS A COMPETITION MAPPING ─────────────────────
#:
#: ONE STRONG MATCH: a single provider fixture whose BOTH SIDES are recognized
#: in the same venue event title. Two named teams playing each other in both
#: sources is not a coincidence, and it is the thing a wrong mapping cannot
#: produce -- `soccer_england_league2` against the venue's National League
#: shares no fixture at all, let alone both sides of one.
#:
#: I FIRST REQUIRED TWO MATCHES AND THAT WAS THE WRONG MEASUREMENT, not merely a
#: strict one. The provider's competition routinely carries MORE fixtures than
#: the venue lists -- the venue's board is a subset, three MLS events against a
#: full matchday -- so counting absolute matches measures VENUE COVERAGE, not
#: whether the two are the same competition. A correctly mapped competition with
#: one listed fixture would have been refused forever.
#:
#: A WEAK match (one shared word) is counted and reported but cannot confirm on
#: its own: "United" and "City" appear in every English division, and a rule
#: that accepted them would confirm every tier against every other.
MIN_STRONG_FIXTURE_MATCHES = 1

#: Kept because it is reported, and because the weak count is still evidence an
#: operator reads next to the strong one.
MIN_FIXTURE_MATCHES = 1


def confirm_mapping_by_fixtures(*, provider_events, venue_event_titles,
                                venue_event_days=None, family=None) -> dict:
    """Does the provider's competition name the SAME FIXTURES as the venue's?

    THE HOLE THIS CLOSES. `select_sports` established that a provider key EXISTS
    and is ACTIVE and reported it as confirmed. Existence is not identity:
    `soccer_england_league2` exists, is active, and is not the competition the
    venue lists under `engnl`.

    A ONE-TO-ONE MATCH OF BOTH TEAMS, AND THAT WORDING IS THE REPAIR. The first
    version matched sets of words and counted a side matched on ANY overlap, so:

        provider  Manchester United vs Manchester City
        venue     Manchester United vs Liverpool

    both provider teams reduced to {manchester}, the SAME venue token satisfied
    both sides, and a shared city name confirmed a mapping between two different
    fixtures.

    Now the provider's home must match ONE venue side and the provider's away
    THE OTHER, as distinct positions. Reversed sides count -- the two sources do
    not agree on home/away ordering -- but one venue side cannot serve twice.

    AND THE DATE, WHERE BOTH SIDES SUPPLY ONE. Two teams meet more than once a
    season, so names alone cannot separate a fixture from its return leg.
    `venue_event_days` maps a venue title to its `game_start`; where both
    datings exist they must agree within a calendar day, and where one is
    absent that is REPORTED as a limit of the confirmation rather than passed
    over.

    CANDIDATE DISCOVERY IS NOT CONFIRMED IDENTITY. This function answers only
    "are these the same competition". It selects no contract and admits nothing.

    Pure. Never raises. An empty provider list is UNCONFIRMABLE, not confirmed.
    """
    out: dict = {"ok": False, "matches": 0, "strong_matches": 0, "examined": 0,
                 "min_required": MIN_STRONG_FIXTURE_MATCHES,
                 "matched_fixtures": [], "normalisation": [],
                 "rejected_fixtures": [],
                 "requires": ("a one-to-one match of BOTH teams as distinct "
                              "sides; a shared token is not a match"),
                 "this_confirms_identity_not_a_candidate": True,
                 "refusal": R_MAPPING_UNCONFIRMABLE}
    days = dict(venue_event_days or {})
    venue = []
    for title in (venue_event_titles or ()):
        sides = _sides_of(title)
        if sides is None:
            out["rejected_fixtures"].append(
                {"venue": str(title)[:60], "refusal": R_SIDES_NOT_TWO})
            continue
        a_tok, a_drop = _team_tokens(sides[0], family)
        b_tok, b_drop = _team_tokens(sides[1], family)
        if a_tok == b_tok or not a_tok or not b_tok:
            # Two sides that normalise identically cannot support a one-to-one
            # match either, and it is the shape the old defect produced.
            out["rejected_fixtures"].append(
                {"venue": str(title)[:60], "refusal": R_SIDES_NOT_TWO,
                 "why": "both sides normalise to the same team"})
            continue
        # A title that names fixtures on several days (a return leg under the
        # same title) is one venue fixture PER DAY (R30A): each is matched on
        # its own date, so neither meeting erases the other.
        tdays = days.get(title)
        for tday in (tdays if isinstance(tdays, (list, tuple)) else [tdays]):
            venue.append({"title": str(title)[:70], "a": a_tok, "b": b_tok,
                          "day": tday})
        if a_drop or b_drop:
            out["normalisation"].append(
                {"venue": str(title)[:60],
                 "dropped": sorted(set(a_drop + b_drop)),
                 "why": ("affiliation markers only; a discriminating word is "
                         "never dropped")})
    if not venue:
        out["why"] = ("the venue lists no event title naming two distinct "
                      "sides for this competition, so there is nothing a "
                      "one-to-one match could be made against")
        return out
    events = list(provider_events or ())
    out["examined"] = len(events)
    if not events:
        out["why"] = ("the provider returned no events for this key. That is "
                      "consistent with a competition between rounds AND with a "
                      "key that is not this competition; it confirms neither")
        return out

    for ev in events:
        ev = ev or {}
        home, h_drop = _team_tokens(ev.get("home_team"), family)
        away, a_drop = _team_tokens(ev.get("away_team"), family)
        if not home or not away or home == away:
            out["rejected_fixtures"].append(
                {"provider": "%s vs %s" % (ev.get("home_team"),
                                           ev.get("away_team")),
                 "refusal": R_SIDES_NOT_TWO,
                 "why": "the provider event does not name two distinct sides"})
            continue
        if h_drop or a_drop:
            out["normalisation"].append(
                {"provider": "%s vs %s" % (ev.get("home_team"),
                                           ev.get("away_team")),
                 "dropped": sorted(set(h_drop + a_drop)),
                 "why": "affiliation markers only"})
        for v in venue:
            # ── THE ONE-TO-ONE ASSIGNMENT, BOTH ORIENTATIONS ─────────
            same_order = _same_team(home, v["a"]) and _same_team(away, v["b"])
            swapped = _same_team(home, v["b"]) and _same_team(away, v["a"])
            if not (same_order or swapped):
                continue
            day = _same_fixture_day(ev.get("commence_time"), v["day"])
            if day["comparable"] and not day["same"]:
                out["rejected_fixtures"].append(
                    {"provider": "%s vs %s" % (ev.get("home_team"),
                                               ev.get("away_team")),
                     "venue": v["title"],
                     "refusal": R_MAPPING_FIXTURES_DO_NOT_MATCH,
                     "why": ("the same two teams on different days (%s against "
                             "%s): a different meeting, not this one"
                             % (day["provider_day"], day["venue_day"]))})
                continue
            out["matches"] += 1
            out["strong_matches"] += 1
            if len(out["matched_fixtures"]) < 4:
                out["matched_fixtures"].append(
                    {"provider": "%s vs %s" % (ev.get("home_team"),
                                               ev.get("away_team")),
                     "venue": v["title"],
                     "orientation": "SAME" if same_order else "SIDES_SWAPPED",
                     "strong": True,
                     "home_matched": sorted(home),
                     "away_matched": sorted(away),
                     "date_check": day})
            break

    if out["strong_matches"] >= MIN_STRONG_FIXTURE_MATCHES:
        undated = [m for m in out["matched_fixtures"]
                   if not (m["date_check"] or {}).get("comparable")]
        out.update(ok=True, refusal=None,
                   dating_incomplete=bool(undated),
                   why=("%d of the provider's %d fixtures match a venue "
                        "fixture one-to-one on both sides%s"
                        % (out["strong_matches"], out["examined"],
                           (" -- but with no dating on one side, so a repeated "
                            "meeting between the same teams is not excluded"
                            if undated else " and on the date"))))
        return out
    out.update(refusal=R_MAPPING_FIXTURES_DO_NOT_MATCH,
               why=("none of the provider's %d fixtures matches any of the "
                    "venue's %d one-to-one on both sides. A shared city name "
                    "is not a match: `Manchester United vs Manchester City` "
                    "against `Manchester United vs Liverpool` shares only "
                    "'manchester' and is a different fixture"
                    % (out["examined"], len(venue))))
    return out

#: `intf` (international friendlies, 15 events) is deliberately ABSENT. The
#: provider does list friendlies, but a friendly's settlement and team-selection
#: conventions are not the ones `bettor_venue_settlement` has established, and
#: this lane already refuses on VOID_ABANDONMENT_RULE_NOT_ESTABLISHED and
#: OVERTIME_RULE_NOT_ESTABLISHED for exactly that class of gap. Spending credits
#: to reach a refusal is the mistake this whole change is undoing.
VENUE_TOKENS_DELIBERATELY_EXCLUDED = {
    "intf": ("international friendlies: settlement and team-selection "
             "conventions are not established for this lane, so a candidate "
             "would reach a rule refusal, not a trade"),
}

#: ── THE FALLBACK BOARD, AND ITS EXPIRY ──────────────────────────────
#:
#: WHAT WAS WRONG WITH IT. `venue_soccer_competitions` fell back to this
#: snapshot whenever the live read failed, with no time limit -- so a database
#: read that broke in October would have kept the cycle spending metered credits
#: on September's board indefinitely, and the heartbeat would have shown a
#: competition set that no longer existed. A snapshot is evidence of what was
#: listed ONCE; it is not evidence of current coverage, and the longer it is
#: used the less it is evidence of anything.
#:
#: So the fallback has a stated validity window. Inside it the snapshot is used
#: and marked STALE_SNAPSHOT. Outside it the board is EXPIRED and the cycle
#: falls back to the confirmed set alone -- the same place a failed provider
#: catalogue read leaves it. An old board must not become permanent evidence.
#: 2026-09-28T00:00:00Z. I first wrote 1759017600, which is 2025-09-28 --
#: a year early, so the snapshot was born 365 days old and the fallback
#: reported EXPIRED_SNAPSHOT on its first use. A hand-typed epoch is a
#: guess like any other; this one is computed and the date is in the
#: comment so the next reader can check it without a converter.
VENUE_BOARD_SNAPSHOT_AT = 1790553600.0
VENUE_BOARD_SNAPSHOT_VALID_S = 7 * 24 * 3600.0  # one week
R_BOARD_SNAPSHOT_EXPIRED = "THE_FALLBACK_VENUE_BOARD_SNAPSHOT_HAS_EXPIRED"

#: The measured board, 2026-09-28 (research-sql run 259), as
#: `venue_soccer_competitions` would return it. Kept as the FALLBACK ordering
#: when the live read fails, and as the record of what the numbers above mean.
VENUE_BOARD_SNAPSHOT_MEASURED = (
    ("unl", 39), ("intf", 15), ("engnl", 12), ("cnl", 10), ("uwcl", 9),
    ("arg2", 8), ("lco", 7), ("brb", 6), ("uslc", 5), ("irl1", 5),
    ("par2", 4), ("mls", 3), ("lmx", 3), ("uru1", 3), ("nwsl", 2),
)

#: ── THE BOARD'S BOUNDS (R30A P0 incident, inc-catalogue) ───────────────
#:
#: THE BOARD KEPT ITS FIRST THIRTY TOKENS AND TWELVE TITLES, AND BOTH CUTS
#: REACHED MAPPED COMPETITIONS. research-sql run 37233672878 (K5a, 2026-10-04
#: 20:51Z): the venue's real soccer board carried 47 league tokens; `LIMIT 30`
#: dropped 17 of them -- among them `mls` (row 38) and `uslc` (row 44), both
#: mapped in VENUE_TOKEN_TO_PROVIDER_KEY -- so on a quiet day those two
#: competitions could never become a candidate, and nothing named why (ties at
#: one event broke on the planner's whim). And the fixture titles that travel
#: with each candidate were the first TWELVE in alphabetical order: `unl` listed
#: 25 fixtures, `ncaaws` 74, `u21eq` 24, so `confirm_mapping_by_fixtures` saw
#: under half of the Nations League board and could refuse a competition whose
#: provider fixtures all sat in the alphabet's second half.
#:
#: The token bound is now a size the board cannot reach in practice (it holds
#: one row per competition), and the titles carried are every fixture up to a
#: bound far above any measured slate. Both bounds are still bounds: a board
#: that FILLS one is reported (`board_truncated`, `titles_truncated`), never
#: read as complete.
BOARD_TOKEN_LIMIT = 500
BOARD_TITLES_CARRIED = 1000
#: The board ranks the slate inside the catalogue sweep's own forward window
#: (premap fwd_h = 96 h) -- the horizon it always ranked, now stated.
BOARD_HORIZON_H = 96

#: The venue's REAL soccer board, by its own league token, newest first. The
#: simulated exclusion is `bettor_venue_realism`'s, applied in SQL so a
#: competition of 168 eBattles events cannot outrank a real one.
def _board_sql(family_prefix: str = "soccer",
               title_limit: int = BOARD_TITLES_CARRIED,
               token_limit: int = BOARD_TOKEN_LIMIT) -> str:
    """The board query, with its exclusions GENERATED from the classifier.

    `family_prefix` is the venue `sports_type` family the board ranks
    (`soccer`, or `football` for the college-football board below), and
    `title_limit` bounds the fixture titles carried per token for the mapping
    confirmation. Both are fixed identifiers in source, asserted before
    interpolation.

    THE INCONSISTENCY THIS REMOVES. This query was hand-written to exclude
    `%ebattles%` and `%esoccer%` while claiming to share
    `bettor_venue_realism`'s rule -- which knows sixteen markers and seven
    simulated `sports_type` prefixes. Two of sixteen is not the same rule, and
    a new simulated family (`ecricket`, say) would have counted as real soccer
    here while the per-contract guard refused every one of its contracts. The
    board would then have ranked a competition the lane cannot trade.

    So the SQL is built from `SIMULATED_MARKERS` and
    `SIMULATED_SPORTS_TYPE_PREFIXES` themselves. Adding a marker to the module
    changes this query with it. The markers are fixed identifiers in source, not
    input, and are asserted to be plain lowercase words before interpolation.
    """
    assert family_prefix.isalpha() and family_prefix.islower(), family_prefix
    assert isinstance(title_limit, int) and 0 < title_limit <= 5000, title_limit
    assert isinstance(token_limit, int) and 0 < token_limit <= 5000, token_limit
    for m in vreal.SIMULATED_MARKERS:
        assert m.replace("-", "").isalpha() and m.islower(), m
    for pfx in vreal.SIMULATED_SPORTS_TYPE_PREFIXES:
        assert pfx.rstrip("_").isalpha() and pfx.islower(), pfx
    prose = "\n       ".join(
        "AND lower(coalesce(event_title, '') || ' ' "
        "|| coalesce(question, '')) NOT LIKE '%%%s%%'" % m
        for m in vreal.SIMULATED_MARKERS)
    types = "\n       ".join(
        "AND coalesce(sports_type, '') NOT LIKE '%s%%'" % pfx
        for pfx in vreal.SIMULATED_SPORTS_TYPE_PREFIXES)
    # `sports_type LIKE 'soccer%'` is the POSITIVE half and is the classifier's
    # affirmative rule too: only a recognized real family counts, so an
    # unrecognized one is absent from the board rather than ranked.
    return ("""
    SELECT split_part(market_slug, '-', 2)  AS token,
           count(DISTINCT event_slug)        AS events,
           -- every distinct fixture title, so a carried list shorter than this
           -- is a truncation the reader can name (R30A)
           count(DISTINCT left(event_title, 80)) AS title_count,
           (array_agg(DISTINCT left(event_title, 80)))[1:%(n)d] AS titles,
           -- THE FIXTURE DATES, PER TITLE. Codex: the scheduled caller never
           -- supplied `venue_event_days`, so the date comparison in
           -- `confirm_mapping_by_fixtures` was exercised only by tests. Two
           -- teams meet more than once a season and names alone cannot separate
           -- a fixture from its return leg, so the venue's own game_start
           -- travels with each title.
           (array_agg(DISTINCT left(event_title, 80) || '\u0001'
                      || coalesce(to_char(game_start, 'YYYY-MM-DD'), '')))[1:%(n)d]
             AS title_days
      FROM us_premap
     WHERE sports_type LIKE '%(fam)s%%'
       """ % {"n": title_limit, "fam": family_prefix} + types + """
       """ + prose + """
       AND game_start > now() - interval '6 hours'
       -- the slate the metered budget is spent on: the sweep's own window.
       -- The catalogue now also holds futures and next week's fixtures
       -- (premap AHEAD); ranking competitions by those would hand the
       -- provider budget to fixtures it does not price yet (R30A)
       AND game_start <= now() + interval '%(horizon)d hours'
     GROUP BY 1
     ORDER BY 2 DESC, 1
     LIMIT %(tokens)d
""" % {"tokens": token_limit, "horizon": BOARD_HORIZON_H})


VENUE_SOCCER_BOARD_SQL = _board_sql()


async def venue_soccer_competitions(conn, *, now: float | None = None) -> dict:
    """What the VENUE lists right now, by its own league token.

    Read-only, one bounded query, no venue network call -- `us_premap` is the
    catalogue the collector already maintains.

    A READ FAILURE FALLS BACK WITH AN EXPIRY, NOT FOREVER. An unread board is
    not an empty board, so the snapshot stands in for it -- but only inside
    `VENUE_BOARD_SNAPSHOT_VALID_S`. Past that the board is EXPIRED, the returned
    board is empty, and the cycle runs the confirmed set alone. Without the
    expiry a database read that broke in October would have kept this lane
    spending metered credits on September's competitions and reporting them as
    current coverage.

    `evidence_age_s` and `evidence` are on every result, including the healthy
    one, so a reader never has to infer which of the three states produced it.
    """
    at = float(now if now is not None else time.time())
    out: dict = {"read": False, "board": [], "source": "us_premap",
                 "evidence": "LIVE_READ", "evidence_age_s": 0.0,
                 "snapshot_at": VENUE_BOARD_SNAPSHOT_AT,
                 "snapshot_valid_s": VENUE_BOARD_SNAPSHOT_VALID_S}
    try:
        rows = await conn.fetch(VENUE_SOCCER_BOARD_SQL)
    except Exception as exc:                                   # noqa: BLE001
        age = at - VENUE_BOARD_SNAPSHOT_AT
        out["error"] = type(exc).__name__
        out["evidence_age_s"] = round(age, 1)
        if age > VENUE_BOARD_SNAPSHOT_VALID_S:
            out.update(evidence="EXPIRED_SNAPSHOT", board=[],
                       refusal=R_BOARD_SNAPSHOT_EXPIRED,
                       why=("the venue board could not be read (%s) and the "
                            "2026-09-28 snapshot is %.1f days old, past its "
                            "%.1f-day validity. A snapshot that old is not "
                            "evidence of current coverage, so no soccer "
                            "competition is requested and the confirmed set "
                            "runs alone"
                            % (type(exc).__name__, age / 86400.0,
                               VENUE_BOARD_SNAPSHOT_VALID_S / 86400.0)))
            return out
        out.update(evidence="STALE_SNAPSHOT",
                   board=list(VENUE_BOARD_SNAPSHOT_MEASURED),
                   why=("the venue board could not be read (%s), so the "
                        "2026-09-28 snapshot is used -- %.1f days old, inside "
                        "its validity window -- and marked as a snapshot "
                        "rather than a current reading"
                        % (type(exc).__name__, age / 86400.0)))
        return out
    out["read"] = True
    out["board"] = [(str(r["token"]), int(r["events"])) for r in rows]
    # THE VENUE'S OWN FIXTURE TITLES PER TOKEN, so the mapping can be confirmed
    # against fixtures without a second query for every sport in the cycle.
    out["titles"] = {str(r["token"]): [str(t) for t in (r["titles"] or [])]
                     for r in rows}
    # {token: {title: 'YYYY-MM-DD'}}, from the venue's own game_start.
    days: dict = {}
    for r in rows:
        days[str(r["token"])] = title_days_of(r["title_days"])
    out["title_days"] = days
    out.update(board_bounds(rows))
    out["why"] = ("the venue's own league tokens for REAL soccer events "
                  "starting within the last 6 hours or later, simulated "
                  "competitions excluded by the venue's own words")
    return out


def title_days_of(pairs) -> dict:
    """{title: 'YYYY-MM-DD'} from the board's `title<U+0001>day` pairs -- and
    {title: ['YYYY-MM-DD', 'YYYY-MM-DD']} when ONE title names fixtures on
    several days. Pure.

    TWO FIXTURES, ONE TITLE, TWO DATES (R30A, adversarial review): this was
    `per[title] = day`, so the last pair won and a return leg -- the same two
    clubs, the same title, another date -- erased the first fixture's date
    from the confirmation (a provider fixture on the erased date then read as
    "the same two teams on different days"). The SQL already returns every
    distinct (title, day) pair; every date is kept. A single date stays a
    string, exactly as every reader and pin has it."""
    per: dict = {}
    for pair in (pairs or []):
        title, _, day = str(pair).partition("\u0001")
        if not (title and day):
            continue
        cur = per.get(title)
        if cur is None:
            per[title] = day
        elif isinstance(cur, list):
            if day not in cur:
                cur.append(day)
                cur.sort()
        elif cur != day:
            per[title] = sorted({cur, day})
    return per


def board_bounds(rows, *, token_limit: int = BOARD_TOKEN_LIMIT,
                 title_limit: int = BOARD_TITLES_CARRIED) -> dict:
    """The board's own completeness, stated beside it (R30A). Pure.

    `board_truncated` when the read returned its token bound (a competition may
    lie beyond it); `titles_truncated` names every token whose carried titles
    are fewer than its distinct fixture titles. A row without `title_count`
    (an older reader, a test fake) is reported as not measured, never as
    complete."""
    rows = list(rows or [])
    cut, unmeasured, same_title, pairs_cut = [], [], [], []
    for r in rows:
        get = r.get if hasattr(r, "get") else (lambda k, _r=r: _r[k])

        def _opt(k):
            try:
                return get(k)
            except (KeyError, IndexError):
                return None

        n = _opt("title_count")
        pairs = list(_opt("title_days") or [])
        # every title naming fixtures on more than one day, by name (R30A)
        for title, days in title_days_of(pairs).items():
            if isinstance(days, list) and len(same_title) < 50:
                same_title.append({"token": str(get("token")),
                                   "title": title, "days": list(days)})
        if len(pairs) >= title_limit:
            pairs_cut.append(str(get("token")))
        if n is None:
            unmeasured.append(str(get("token")))
            continue
        carried = len(get("titles") or [])
        if int(n) > carried:
            cut.append({"token": str(get("token")), "titles": int(n),
                        "carried": carried})
    return {"board_bound": {"tokens": token_limit, "titles": title_limit},
            "board_truncated": len(rows) >= token_limit,
            "titles_truncated": cut,
            "title_days_truncated": pairs_cut,
            "same_title_fixtures": same_title,
            "titles_count_unmeasured": unmeasured}


def candidates_from_board(board, titles=None, title_days=None, *,
                          family="soccer", token_map=None) -> list:
    """Venue board -> provider-key candidates, in the board's own order.

    `family` / `token_map` default to the soccer board and
    VENUE_TOKEN_TO_PROVIDER_KEY; the football board passes `"football"` and
    VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY, so a token is only ever looked up in
    its own family's map (`cfb` is not a soccer token and `unl` is not a
    football one).

    `titles` is the venue's own fixture titles per token. They travel WITH the
    candidate because the mapping is confirmed against fixtures, and a candidate
    that arrives at the confirmation step without the venue's fixtures cannot be
    confirmed -- it would be admitted on key existence alone, which is the hole
    being closed.
    """
    got = []
    by_token = dict(titles or {})
    days_by_token = dict(title_days or {})
    for token, events in (board or ()):
        if token in VENUE_TOKENS_DELIBERATELY_EXCLUDED:
            continue
        if token in VENUE_TOKENS_WITH_A_REFUTED_MAPPING:
            # The fixtures refute the key I mapped it to. Leaving it in to fail
            # the runtime fixture check would spend a metered fetch to learn
            # what reading the fixtures already established.
            continue
        key = (VENUE_TOKEN_TO_PROVIDER_KEY if token_map is None
               else token_map).get(token)
        if not key:
            continue
        got.append({"key": key, "family": family, "our_token": token,
                    "venue_events": int(events),
                    "venue_titles": list(by_token.get(token) or ()),
                    "venue_title_days": dict(days_by_token.get(token) or {})})
    return got

#: The board as measured, for the tests and for a caller with no connection.
#: THE SNAPSHOT CARRIES NO FIXTURE TITLES, and that is a real consequence: a
#: candidate built from it cannot pass the fixture confirmation, so a cycle
#: running on the stale snapshot requests the CONFIRMED set only. Stated here
#: rather than discovered as an empty funnel.
SPORTS_CANDIDATES = tuple(
    candidates_from_board(VENUE_BOARD_SNAPSHOT_MEASURED))

#: The old name, kept so an import of it cannot silently resolve to nothing.
VENUE_SOCCER_BOARD_MEASURED_2026_09_28 = VENUE_BOARD_SNAPSHOT_MEASURED

#: ── COLLEGE FOOTBALL: THE VENUE'S `cfb` BOARD AND THE PROVIDER KEY FOR IT ──
#:
#: THE LOSS THIS CLOSES (cand22, production 2026-10-03, a college-football
#: Saturday; research-sql cand22_ncaaf_stages / cand22_ncaaf_names). The venue
#: listed 107 `cfb` events with a `football_team_full_game_winner` contract on
#: the America/New_York day (Alabama vs. Mississippi State
#: `aec-cfb-ala-mspst-2026-10-03`, Michigan vs. Minnesota, Notre Dame vs. North
#: Carolina ...), and the scheduled cycle requested `baseball_mlb`,
#: `soccer_uefa_nations_league` and `soccer_brazil_serie_b` -- one metered
#: slot of the four unused. No `cfb` event reached a provider fetch, an
#: identity, a valuation or a paper decision, and nothing named why: the
#: competition set was derived from the SOCCER board alone, so the football
#: board was never read and no refusal existed to record. That is a silent
#: disappearance, not a refusal.
#:
#: The fix reads the venue's FOOTBALL board the same way (the same generated
#: SQL, `sports_type LIKE 'football%'`, the same simulated-family exclusions)
#: and maps its token through this table. The candidate then goes through
#: EXACTLY the soccer path: confirmed against the provider's unmetered
#: catalogue, capped by MAX_METERED_SPORTS_PER_CYCLE (unchanged), fixture-
#: confirmed against the venue's own titles before any identity is resolved.
#: `nfl` WAS deliberately absent here (cand22) and that left the NFL invisible:
#: on Sunday 2026-10-04 the venue listed 14 `nfl` games with a
#: `football_team_full_game_winner` contract on the America/New_York day
#: (research-sql cand24_nfl_discover: `aec-nfl-ind-was-2026-10-04`, the 09:30 ET
#: London game, through `aec-nfl-det-car-2026-10-04` at 20:20 ET) and no
#: provider request, identity, valuation or refusal existed for any of them.
#: cand24 maps it through THE SAME PATH as `cfb`: provider-catalogue
#: confirmation, the unchanged MAX_METERED_SPORTS_PER_CYCLE ranked by venue
#: coverage, fixture confirmation against the venue's own titles, then the
#: venue-native identity (city/region + nickname, see
#: bettor_venue_native_identity.NFL_TEAMS). The league tokens keep the two
#: competitions apart: an NFL price never searches `cfb` rows and vice versa.
VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY = {
    # Alabama vs. Mississippi State, Michigan vs. Minnesota, Notre Dame vs.
    # North Carolina, Ohio State vs. Iowa (venue `cfb`, 2026-10-03)
    "cfb": "americanfootball_ncaaf",
    # IND Colts vs. WAS Commanders (London, 13:30Z), LA Rams vs. PHI Eagles,
    # NY Jets vs. CHI Bears, DET Lions vs. CAR Panthers (venue `nfl`,
    # 2026-10-04; team_name "indianapolis colts", side_norm "colts")
    "nfl": "americanfootball_nfl",
}

#: More titles than soccer's old 12: one Saturday's `cfb` slate is ~100
#: events, listed alphabetically, and the confirmation needs a title the
#: provider still lists (finished games drop out of the provider's feed).
#: R30A: both boards now carry every fixture up to BOARD_TITLES_CARRIED (a
#: Saturday with FBS and FCS listed would pass 120), with the cut named by
#: `board_bounds` if a slate ever reaches it.
VENUE_FOOTBALL_TITLES_CARRIED = BOARD_TITLES_CARRIED
VENUE_FOOTBALL_BOARD_SQL = _board_sql("football",
                                      VENUE_FOOTBALL_TITLES_CARRIED)


async def venue_football_competitions(conn) -> dict:
    """The venue's FOOTBALL board, by its own league token. Never raises.

    Same shape as `venue_soccer_competitions`. There is NO snapshot fallback:
    a failed read returns an empty board with the error named, and the cycle
    requests no football competition -- an unread board is never a licence to
    spend a metered credit.
    """
    out: dict = {"read": False, "board": [], "titles": {}, "title_days": {},
                 "source": "us_premap", "family": "football",
                 "evidence": "LIVE_READ"}
    try:
        rows = await conn.fetch(VENUE_FOOTBALL_BOARD_SQL)
    except Exception as exc:                                   # noqa: BLE001
        out.update(evidence="READ_FAILED", error=type(exc).__name__,
                   why=("the venue football board could not be read (%s); no "
                        "football competition is requested this cycle"
                        % type(exc).__name__))
        return out
    try:
        out["board"] = [(str(r["token"]), int(r["events"])) for r in rows]
        out["titles"] = {str(r["token"]): [str(t) for t in (r["titles"] or [])]
                         for r in rows}
        days: dict = {}
        for r in rows:
            days[str(r["token"])] = title_days_of(r["title_days"])
        out["title_days"] = days
        out.update(board_bounds(rows))
    except Exception as exc:                                   # noqa: BLE001
        out.update(board=[], titles={}, title_days={},
                   evidence="READ_UNPARSEABLE", error=type(exc).__name__)
        return out
    out["read"] = True
    return out


def football_candidates(board: dict) -> list:
    """The football board's provider-key candidates (pure)."""
    b = dict(board or {})
    return candidates_from_board(b.get("board"), b.get("titles"),
                                 b.get("title_days"), family="football",
                                 token_map=VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY)


def merge_candidates(*lists) -> list:
    """One candidate list ordered by MEASURED VENUE COVERAGE (events,
    descending), stable within ties -- the order `select_sports` spends its
    unchanged metered budget in. A key appearing twice keeps its first."""
    seen, got = set(), []
    for c in sorted((c for lst in lists for c in (lst or ())),
                    key=lambda c: -int(c.get("venue_events") or 0)):
        if c["key"] in seen:
            continue
        seen.add(c["key"])
        got.append(c)
    return got

R_PROVIDER_DOES_NOT_LIST = "PROVIDER_DOES_NOT_LIST_THIS_COMPETITION"
R_PROVIDER_LISTS_IT_INACTIVE = "PROVIDER_LISTS_THIS_COMPETITION_AS_INACTIVE"

#: ── THE METERED REQUEST BUDGET, AND THE CHANGE THIS MADE TO IT ───────
#:
#: The provider bills per request x market x region, so this number IS the
#: budget. It is declared as a number rather than left to the length of a tuple
#: so that adding a candidate cannot silently raise the spend -- which is how
#: the EPL fetch persisted unnoticed.
#:
#: WHAT CHANGED, EXPLICITLY. Before: THREE fetches per cycle (soccer_epl,
#: soccer_mexico_ligamx, baseball_mlb) at roughly 18-21 credits each -- about 60
#: a cycle, ~5.8k/day at the 900 s cadence. After: FOUR -- about 80 a cycle,
#: ~7.7k/day. That is an increase of roughly 1,900 credits a day, and it is a
#: resource decision rather than a neutral refactor, so it is written here as a
#: change with its arithmetic instead of appearing as a different tuple length.
#:
#: The offsetting fact, and it does not cancel the increase: one of the three
#: former fetches (soccer_epl, ~20 credits a cycle, ~2k/day) could never reach a
#: venue contract, so the SPEND THAT CAN REACH A CONTRACT rises from roughly
#: 40 credits a cycle to 80.
MAX_METERED_SPORTS_PER_CYCLE = 4
#: UNCHANGED BY cand24 (the NFL). The NFL competes for the same four keys by
#: the same rule -- the confirmed MLB key first, then measured venue coverage.
#: On the 2026-10-04 board (cfb 34, unl 26, nfl 15, brb 8 events) that requests
#: MLB, NCAAF, UNL and NFL, and Brazil Serie B -- with no event in the next 24 h
#: -- is the one `budget_dropped`, by name. MLB (the postseason) is never
#: ranked out. Raising the cap to 5 would cost ~20 credits a cycle, ~1.9k a day
#: at the 900 s cadence; it is not raised.
METERED_BUDGET_CHANGE = {
    "before": {"keys": 3, "credits_per_cycle": "~60", "per_day": "~5.8k",
               "keys_named": ["soccer_epl", "soccer_mexico_ligamx",
                              "baseball_mlb"],
               "of_which_unreachable": ["soccer_epl"]},
    "after": {"keys": 4, "credits_per_cycle": "~80", "per_day": "~7.7k"},
    "net": ("about +1,900 credits a day; reachable spend rises from ~40 to ~80 "
            "credits a cycle once the unreachable EPL fetch is removed"),
    "cadence_s": 900.0,
}

#: The set used when the unmetered catalogue read FAILS. It carries only what is
#: already confirmed: a failed confirmation must not be a licence to spend on
#: candidates, and it must never reinstate `soccer_epl`.
SPORTS = SPORTS_CONFIRMED


def provider_keys_for_family(family: str) -> list:
    """Every provider key this lane might use for a sport family.

    THE DEFECT THIS EXISTS FOR, AND IT REACHED PRODUCTION BEHAVIOUR, NOT JUST A
    TEST. `rn1x_shadow.ManagedOdds.for_family` read the static `SPORTS` tuple.
    When that tuple narrowed to `baseball_mlb` alone, a held SOCCER position's
    managed-odds fetch began refusing with
    THE_HELD_SPORT_IS_NOT_IN_THE_PROVIDER_SET -- so an existing soccer position
    lost its fair-value input entirely, and the refusal named the wrong reason.
    The full suite caught it; my own targeted selection did not, because its
    `-k` filter covered neither `held_position` nor `budget`.

    A HELD POSITION'S FAMILY IS NOT A BUDGET QUESTION. `SPORTS`/`select_sports`
    answer "which competitions may this cycle SPEND ON for new entries", which is
    bounded by a credit budget and by the venue's current board. Servicing
    inventory already owned is a different question with a different answer: the
    key is needed regardless of this cycle's budget, so this returns the
    CONFIRMED keys plus every mapped candidate key, unfiltered by budget or by
    today's board.
    """
    fam = str(family or "")
    keys = [k for k, f in SPORTS_CONFIRMED if f == fam]
    if fam == "soccer":
        # Every key in the token map, whether or not the board carries the token
        # today: a position held over a competition's off-day still needs its
        # probability source.
        for key in VENUE_TOKEN_TO_PROVIDER_KEY.values():
            if key not in keys:
                keys.append(key)
    if fam == "football":
        for key in VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY.values():
            if key not in keys:
                keys.append(key)
    for cand in SPORTS_CANDIDATES:
        if cand["family"] == fam and cand["key"] not in keys:
            keys.append(cand["key"])
    return keys


def select_sports(catalogue, *, budget=MAX_METERED_SPORTS_PER_CYCLE,
                  candidates=None) -> dict:
    """Confirmed keys, plus candidates the PROVIDER ITSELF lists as active.

    Pure. `catalogue` is `fetch_sport_catalogue`'s result -- the unmetered
    `/v4/sports` read -- and a failed or empty read yields the confirmed set
    alone, with the reason. Never raises: a cycle that cannot confirm still
    trades the sport it has always traded.

    `candidates` is the venue board's own candidate list, from
    `candidates_from_board(await venue_soccer_competitions(conn))`. It defaults
    to the 2026-09-28 measurement so a caller with no connection -- a test, a
    describe -- still gets a defensible set, but the CYCLE passes the live board
    because the venue's soccer board is seasonal.

    Returns `sports` in the shape the cycle iterates ((key, family) pairs),
    `rejected` naming each candidate and why, and `budget_*` so an operator can
    see what the cap dropped rather than inferring it from a short list.
    """
    out: dict = {"sports": list(SPORTS_CONFIRMED),
                 "rejected": [], "confirmed_by_provider": [],
                 "budget": int(budget),
                 "budget_dropped": [],
                 "catalogue_read": bool((catalogue or {}).get("ok")),
                 "never_requested": ["soccer_epl"],
                 "why_epl_is_never_requested": (
                     "ON 2026-09-28 the US venue listed zero contracts "
                     "carrying the token `epl` (research-sql runs 258 and "
                     "259), and its English top-flight rows were eBattles "
                     "simulations. That is a SNAPSHOT of one board, not a "
                     "standing fact about the venue: it may list the "
                     "competition later, and this exclusion would then be "
                     "wrong. What makes the exclusion safe regardless is that "
                     "the soccer set is DERIVED from the board each cycle -- "
                     "if `epl` appears on it with a confirmable provider "
                     "mapping, it is requested like any other token. The "
                     "entry here only keeps it out of the hand-written "
                     "candidate path"),
                 "this_is_a_snapshot_not_a_standing_claim": True}
    cands = list(SPORTS_CANDIDATES if candidates is None else candidates)
    out["candidates_considered"] = [c["our_token"] for c in cands]
    if not (catalogue or {}).get("ok"):
        out["why"] = ("the unmetered sport catalogue could not be read, so no "
                      "candidate is confirmed and none is fetched. The "
                      "confirmed set still runs")
        out["rejected"] = [{"key": c["key"], "our_token": c["our_token"],
                            "refusal": R_PROVIDER_DOES_NOT_LIST,
                            "why": "catalogue unread; not confirmable"}
                           for c in cands]
        return out
    listed = {}
    for row in (catalogue.get("sports") or []):
        key = str((row or {}).get("key") or "")
        if key:
            listed[key] = row
    for cand in cands:
        row = listed.get(cand["key"])
        if row is None:
            out["rejected"].append(
                {"key": cand["key"], "our_token": cand["our_token"],
                 "refusal": R_PROVIDER_DOES_NOT_LIST,
                 "why": ("the provider's own catalogue does not carry this "
                         "key, so the name was a guess of mine and no "
                         "metered call is made on it")})
            continue
        if row.get("active") is False:
            out["rejected"].append(
                {"key": cand["key"], "our_token": cand["our_token"],
                 "refusal": R_PROVIDER_LISTS_IT_INACTIVE,
                 "why": "the provider lists it with active=false"})
            continue
        out["confirmed_by_provider"].append(
            {"key": cand["key"], "our_token": cand["our_token"],
             "venue_events": cand["venue_events"],
             # THE VENUE'S FIXTURES COME WITH IT, and this was the omission that
             # made every mapping UNCONFIRMABLE in the cycle: the titles reached
             # the candidate and stopped there, so the confirmation step had the
             # provider's fixtures and none of the venue's to compare them to.
             # It refused everything, which is the safe direction and still the
             # wrong reason.
             "venue_titles": list(cand.get("venue_titles") or ()),
             "venue_title_days": dict(cand.get("venue_title_days") or {}),
             "provider_title": row.get("title")})
    # ORDER IS MEASURED COVERAGE, and the cap is applied after confirmation so
    # a candidate the provider does not list cannot consume a budget slot.
    room = max(0, int(budget) - len(out["sports"]))
    for i, ok in enumerate(out["confirmed_by_provider"]):
        fam = next(c["family"] for c in cands
                   if c["key"] == ok["key"])
        if i < room:
            out["sports"].append((ok["key"], fam))
        else:
            out["budget_dropped"].append(
                {"key": ok["key"], "venue_events": ok["venue_events"],
                 "why": ("confirmed and active, but the cycle's metered "
                         "budget of %d keys is already spent" % int(budget))})
    out["why"] = ("the confirmed set, plus every candidate the provider's own "
                  "unmetered catalogue lists as active, ordered by measured US "
                  "venue coverage and capped at the metered budget")
    return out

#: One cycle per this many seconds. The provider bills per request x
#: market x region, so the cadence IS the budget: three sports at roughly
#: 18-21 credits each is ~60 credits a cycle, ~7.2k/day at 15 minutes.
CYCLE_S = 900.0

#: Hard ceiling on contracts evaluated per cycle. A loop that can grow
#: without bound is the thing the execution-discipline rule forbids.
MAX_PER_CYCLE = 40

#: ── HOW MANY EVENTS MAY SHARE ONE ODDS FETCH ────────────────────────
#:
#: THE MEASURED PROBLEM THIS EXISTS FOR (2026-09-28, all 1,126 evaluation
#: rows). 399 rows produced no fair value, and every one of them failed at
#: the SAME link: QUOTE_STALE. Zero failed at provider coverage, mapping,
#: the de-vig, calibration, persistence or consumer lookup. Decomposing
#: `pinnacle_age_s` the way `_entry_freshness` already splits it:
#:
#:     provider lag (old on arrival)  median 14.6 s, p95 352 s, max 823 s
#:     our processing delay           median 28.7 s, p95 64.7 s, max 94.8 s
#:     already stale on arrival       103 rows  (26%)  -- coverage fact
#:     WE made it stale               296 rows  (74%)  -- ours to fix
#:
#: So on the median row the provider handed us a quote with ~15 s of the
#: 30 s budget left and we spent ~29 s before deciding. This module's own
#: freshness note already said which of those is ours: "a slow provider is
#: a coverage fact, our own delay is ours to fix."
#:
#: WHY THE DELAY IS NOT A DEFECT AND CONCURRENCY CANNOT REMOVE IT.
#: `venue_pace.pace` is a deliberate PROCESS-WIDE SERIAL gate, one request
#: per MIN_GAP_S, shared with the protected collector because the venue
#: 429s above ~3 req/s. Each event needs several paced venue reads before
#: its decision instant, and `received_at` is stamped ONCE per sport, so
#: event N carries a quote aged by every preceding event's reads too.
#: Running them concurrently would not help: the gate would serialise them
#: anyway, and widening it would starve the money path.
#:
#: SO THE ONLY LEVER IS HOW MANY EVENTS SHARE ONE FETCH, AND IT COSTS
#: CREDITS. The provider bills per request x market x region -- one fetch
#: is ~18-21 credits, ~60 a cycle today. Halving the events per fetch
#: roughly halves our accumulated delay and roughly doubles the odds
#: credits. That is a resource decision with a number attached, not a bug,
#: so the knob EXISTS and DEFAULTS TO TODAY'S BEHAVIOUR: at MAX_PER_CYCLE
#: no extra fetch is ever issued and the credit spend is unchanged. Lower
#: it deliberately, and `odds_refetches` on the cycle row says what it
#: bought.
EVENTS_PER_ODDS_FETCH = MAX_PER_CYCLE

#: What a re-fetch is allowed to do, so it can never become a retry. It
#: refreshes the quote for events NOT YET evaluated in this sport; it
#: never re-evaluates an event already decided, and a failed re-fetch
#: keeps the quote we have rather than abandoning the sport -- the old
#: quote then ages normally and QUOTE_STALE refuses it by name, which is
#: the correct outcome and not a silent downgrade.
ODDS_REFETCH_IS_NOT_A_RETRY = (
    "a re-fetch refreshes the provider quote for events still to be "
    "evaluated in this sport. It never re-decides an evaluated event, and "
    "when it fails the existing quote is kept and ages normally, so the "
    "freshness rule refuses it by name instead of the sport being dropped")

#: The venue read is the slow part; bound it so one hanging book cannot
#: hold the cycle open.
VENUE_TIMEOUT_S = 10.0

#: The sharp books whose agreement counts toward per-outcome depth. Taken
#: from `edge/fairvalue/feed.py`'s ANCHOR_BOOKS/SHARP_BOOKS set, which was
#: built from observed production payloads. Pinnacle is the anchor and is
#: counted too -- the floor is "the anchor plus at least one more".
SHARP_BOOKS = ("pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au",
               "smarkets", "matchbook", "betanysports", "lowvig")

#: WHAT PINNACLE'S h2h ACTUALLY SETTLES ON, per sport. These differ, and
#: collapsing them into one string would be the settlement mismatch the
#: valuation source refuses by name.
PINNACLE_SETTLEMENT = {
    "soccer": "REGULATION_90_PLUS_STOPPAGE_NO_EXTRA_TIME",
    "baseball": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
    # cand22: the publisher's American Football section, "Bets on the Game
    # and 2nd Half-periods include points scored in overtime" (captured with
    # its page hash in bettor_settlement_terms.CAPTURE_RUN_FOOTBALL).
    "football": "FULL_GAME_INCLUDING_OVERTIME",
}

#: family -> the labels `markets.sport` actually carries. Read out of
#: `sports._RULES`, not guessed: that table is the writer's vocabulary.
VENUE_SPORT_LABELS = {
    "soccer": ("Soccer",),
    "baseball": ("MLB",),
}

R_NO_CANDIDATE_MARKETS = "NO_OPEN_VENUE_MARKETS_IN_SUPPORTED_SPORTS"
R_NO_TABLE = "EXTERNAL_VALUATIONS_TABLE_ABSENT"
R_VENUE_RULE_UNKNOWN = "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED"
R_NO_VENUE_QUOTE = "NO_CONTEMPORANEOUS_VENUE_QUOTE"
R_VENUE_QUOTE_STALE = "VENUE_QUOTE_STALE"

def _evidence_key(obj):
    """A stable, hashable digest of a currency-evidence object.

    WHY THIS IS NOT `str(obj)`. Dict ordering would make the key depend on
    construction order, so the same evidence could produce two keys and the
    deduplication would silently stop deduplicating. `sort_keys=True` makes
    it canonical.

    AND WHY NOT `hash(obj)`. Dicts are unhashable, and Python's string hash
    is salted per process -- a key that changes between restarts is not a
    key. This is content-addressed and reproducible.

    None maps to None rather than to a digest of "null", so "no evidence"
    stays visibly distinct from "some evidence" when a key is read back.
    """
    if obj is None:
        return None
    import hashlib
    import json as _json
    try:
        blob = _json.dumps(obj, sort_keys=True, default=str)
    except Exception:                                          # noqa: BLE001
        # UNDIGESTIBLE EVIDENCE MEANS DO NOT DEDUPLICATE AT ALL.
        #
        # TWO WRONG ANSWERS I WROTE AND REJECTED. A shared constant would
        # make every undigestible candidate look equivalent to every other
        # -- the worst outcome, since it deduplicates precisely the cases
        # we understand least. And `id(obj)` is unsound: CPython reuses the
        # address of a freed object, so two different evidence objects
        # built in sequence can share an id and COLLIDE. That was
        # demonstrated, not theorised -- two distinct dicts returned the
        # same fallback key.
        #
        # A fresh `object()` is unique and never equal to anything else, so
        # the tuple containing it can never hit the cache. The candidate
        # gets its own real read, which is the conservative direction: we
        # spend a request rather than risk answering one instrument with
        # another's book.
        return object()
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _median(xs):
    """The middle value, or None on an empty sample.

    None RATHER THAN 0.0, and the distinction is the whole point: a cycle
    that evaluated nothing has NO measured delay, and 0.0 would read as
    instant -- the best-looking figure in the table, produced by measuring
    nothing at all.
    """
    v = sorted(float(x) for x in (xs or ()) if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return round(v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0, 3)


def _maxof(xs):
    """The worst sample, or None if there are none.

    Carried beside the median so a single large outlier stays VISIBLE. A
    median alone would let one 400-second stall disappear, and that stall
    is exactly the kind of event an operator needs to see.
    """
    v = [float(x) for x in (xs or ()) if x is not None]
    return round(max(v), 3) if v else None


#: LEVER A's refusal. Its own code, deliberately NOT folded into
#: `QUOTE_STALE`: the two say different things. `QUOTE_STALE` means the
#: quote aged out somewhere between arrival and the decision -- which is
#: the case our own delay can cause and the case the latency work has to
#: reduce. This one means it was ALREADY outside the limit before we spent
#: a single venue read on it, so no amount of speed on our side would have
#: saved it. Counting them together would make the levers look effective
#: by moving cases from one bucket into an indistinguishable one.
R_QUOTE_STALE_ON_ARRIVAL = "QUOTE_STALE_ON_ARRIVAL"

#: How many deferred candidates are named on the cycle. Bounded because a
#: heartbeat is overwritten every cycle and must not grow without bound;
#: `deferred_total` carries the full count beside the sample so the bound
#: cannot understate the coverage that was not examined.
MAX_DEFERRED_REPORTED = 40

WHY_DEFERRED = (
    "MAX_PER_CYCLE was reached before this candidate came up. It was NOT "
    "judged and nothing about it was decided -- deferred is not refused. "
    "It is named because lever B reorders the queue, so which candidates "
    "fall past the cap is now decided by the freshest-first sort rather "
    "than by the provider's sequence: a change that consistently pushed "
    "the same markets past the cutoff would otherwise read as a better "
    "stale rate with no trace of the coverage it cost")

#: LEVER C's refusal. The duplicate is REFUSED rather than served a cached
#: book, because a shared `acquisition_ladder` becomes two independent
#: executable quantities at sizing -- see the comment at the cache.
R_INSTRUMENT_ALREADY_EVALUATED = "INSTRUMENT_ALREADY_EVALUATED_THIS_FETCH"

WHY_DUPLICATE_INSTRUMENT = (
    "another provider event in this same fetch already resolved to this "
    "venue contract and side, and its book read succeeded. Two provider "
    "events on one instrument are ONE opportunity, so the duplicate is "
    "refused rather than priced again. It is refused rather than served "
    "the cached book because a shared acquisition ladder sized twice "
    "becomes two independent executable quantities against one book's "
    "depth -- and because a book that is never reused can never be stale")

WHY_SKIPPED_ON_ARRIVAL = (
    "the provider's quote was already older than the 30 s rule at the "
    "moment this candidate came up, before any venue read. The venue read "
    "and its pacing gap were not spent, because the decision gate could "
    "not have admitted the candidate and the read would have added its "
    "delay to every candidate after this one. The LIMIT IS UNCHANGED -- "
    "this is the same PINNACLE_MAX_AGE_S the decision gate applies, "
    "checked earlier, not a tighter bound")


def arrival_split(provider_epoch, received_at, arrival) -> dict:
    """HOW OLD THE PRICE WAS WHEN WE REACHED IT, split by whose time it was.

    `provider_lag_s`   received_at - the provider's own last_update: THEIRS
    `our_processing_s` arrival - received_at: OURS (every earlier event's
                       paced reads in this fetch, plus this event's work)
    `quote_age_s`      arrival - last_update: the sum the 30 s rule governs

    Pure. A missing clock leaves its figures None, never 0 -- an unmeasured
    lag reported as zero would be the best-looking number in the table.
    """
    out = {"provider_lag_s": None, "our_processing_s": None,
           "quote_age_s": None}
    try:
        pe = None if provider_epoch is None else float(provider_epoch)
        rx = None if received_at is None else float(received_at)
        ar = None if arrival is None else float(arrival)
    except (TypeError, ValueError):
        return out
    if pe is not None and rx is not None:
        out["provider_lag_s"] = round(rx - pe, 3)
    if rx is not None and ar is not None:
        out["our_processing_s"] = round(ar - rx, 3)
    if pe is not None and ar is not None:
        out["quote_age_s"] = round(ar - pe, 3)
    return out


#: WHAT `marketData.transactTime` MEANS -- AND THIS IS NOT YET ESTABLISHED.
#:
#: The gate below computes `decision_instant - transactTime` and refuses
#: above MAX_VENUE_QUOTE_AGE_S. That arithmetic measures OUR staleness only
#: if the venue stamps the RESPONSE. If it stamps the LAST BOOK CHANGE, an
#: old value means the book has not moved -- the ordinary condition of a
#: quiet pre-game money line -- and refusing on it would refuse every quiet
#: book and call the result freshness.
#:
#: TWO OTHER MODULES HERE TOOK THE OTHER SIDE, and they are the established
#: execution path: `institutional_book.current()` computes FRESHNESS_STATUS
#: from BETTOR_RECEIVED_TIMESTAMP -- our own clock -- and uses transactTime
#: only for MARKET_DATA_LAG_MS as provenance; `obs/streamstate` states the
#: prohibition outright, that a venue timestamp belongs to the venue's clock
#: domain and nothing subtracts it from a local reading.
#:
#: TWO DIFFERENT AGES, AND ONLY ONE OF THEM IS MEASURABLE HERE.
#:
#:   LOCAL OBSERVATION AGE = decision_instant - our receipt instant. Both
#:   readings are OURS, the subtraction is a real interval, and it answers
#:   a real question: how long ago did we last see a copy of this book. In
#:   this lane it is small by construction, because the read happens at the
#:   decision -- and SMALL IS NOT INVALID. A bounded observation age is a
#:   genuine property of a synchronous read, not a defect in it.
#:
#:   UPSTREAM DATA AGE = how long ago the venue's own book was that shape.
#:   THIS IS UNKNOWN. `transactTime` is the only candidate and what it
#:   denotes is unestablished: a response stamp and a last-book-change
#:   stamp produce the same field and opposite readings of the same number.
#:
#: NEITHER TOPOLOGY ESTABLISHES UPSTREAM FRESHNESS, and this is a
#: correction to an earlier version of this comment that implied a poll
#: cache did. `institutional_book.FRESHNESS_LIMIT_S` (5.0 s, measured from
#: BETTOR_RECEIVED_TIMESTAMP) bounds how stale OUR COPY is; that is a local
#: observation age too. A persistent poll refreshing that cache
#: (`workers/institutional_md`, its own words REST_POLL_MAINTAINED_IN_MEMORY
#: with the stream target NOT_IDENTIFIED) makes the bound frequent -- it
#: does not make the venue's answer current, because a poll can be served a
#: stale snapshot exactly as a one-off read can. The established path is
#: honest about this: it records the transactTime lag as MARKET_DATA_LAG_MS
#: and gates on neither.
#:
#: SO: RECEIPT TIME ALONE DOES NOT PROVE THE UNDERLYING BOOK IS CURRENT --
#: in either topology -- AND THE EXPLICIT REFUSAL IS RETAINED. Both limits
#: stay where they are. Relaxing a gate on an unestablished reading would be
#: loosening a freshness requirement to manufacture activity.
#:
#: WHAT THE PROBE ESTABLISHES, AND WHAT IT DOES NOT.
#:   establishes  the field's presence, its type, its raw text, whether the
#:                supported parser accepts it, our own request and receipt
#:                instants, and whether the book bytes changed between two
#:                reads.
#:   unknown      what the stamp denotes; the venue's clock offset from
#:                ours; whether an unchanged book means a quiet market or a
#:                stalled upstream; and therefore the upstream data age.
#: It classifies nothing and changes no admission.
VENUE_CLOCK_SEMANTICS = "UNESTABLISHED__LAST_BOOK_UPDATE_OR_RESPONSE_STAMP"

#: THE LABEL FOR A CYCLE THAT EVALUATED NOTHING. Not "zero positive edge":
#: that is a claim about the market, and a cycle whose candidates never
#: reached the economics did not measure the market at all.
ZERO_EVALUATED_INPUT_PATH_BLOCKED = "ZERO_EVALUATED__INPUT_PATH_BLOCKED"
R_NO_DEPTH = "VENUE_ASK_HAS_NO_DEPTH"
R_PROVIDER_ERROR = "PROVIDER_REQUEST_FAILED"
# THREE REASONS THAT WERE ONE COUNTER. The 02:08:18Z production cycle
# mapped two events exactly and then reported
# `NO_CONTEMPORANEOUS_VENUE_QUOTE 2` -- which told management the stage
# refused but not which of three unrelated things was missing: a slug on
# the market row, a book read that raised, or a venue error response.
# Depth and staleness already had their own names and did not fire, so
# these three are the whole remainder.
#: How many DISTINCT venue error messages one cycle keeps. Enough to tell
#: "every contract says the same thing" from "they say different things",
#: and few enough that a heartbeat stays a heartbeat.
MAX_VENUE_ERRORS = 5

#: THE CROSSING THIS LOOP WAS MISSING. `pmus.book_read` takes the VENUE's
#: own market slug; `markets.slug` is the global catalogue's slug for the
#: same fixture, and the venue answers NotFoundError for it -- which is
#: what every read in runs 22-24 did. The repository already crosses that
#: boundary in `workers/premap.resolve`: exact keys out of `us_premap`,
#: date agreement, the venue's own side expansion, and named refusals. It
#: is reused here rather than re-implemented, because a sixth resolver
#: with its own idea of side matching is how a wrong-side trade happens.
R_NO_PREMAP = "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"
R_INTENT_NOT_LONG = "VENUE_CONTRACT_IS_NOT_LONG_ON_THE_PRICED_OUTCOME"
R_NO_SLUG = "VENUE_MARKET_ROW_HAS_NO_SLUG"
R_VENUE_READ_FAILED = "VENUE_BOOK_READ_FAILED"
R_VENUE_READ_ERROR = "VENUE_BOOK_READ_RETURNED_ERROR"

#: ── WHEN THE VENUE'S OWN CATALOGUE MAY STAND IN FOR THE GLOBAL ONE ───
#:
#: EXACTLY THE REFUSALS THAT MEAN "THE GLOBAL CATALOGUE COULD NOT NAME ONE
#: MONEYLINE ROW". No row (NO_VENUE_CONTRACT_FOR_EVENT), two rows because the
#: global match ignores dates (VENUE_MAPPING_AMBIGUOUS, the MLB series case),
#: or only line markets (VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE). Each
#: is a statement about the GLOBAL catalogue, and `bettor_venue_native_identity`
#: asks the venue's own. Every code must be one of these: a CLOSED or SEGMENT
#: code beside them says the fixture was found and was unusable, which the
#: venue-native path has no business overriding. And identity, only when the
#: US catalogue crossing found NOTHING (NO_VENUE_NATIVE_CONTRACT_IN_PREMAP) --
#: never a realism, intent or period refusal, which are findings about a
#: contract that was found.
#:
#: NO_PINNACLE_ON_EVENT IS NOT HERE AND CANNOT BE: it is refused before the
#: mapping is attempted. No catalogue can repair a missing provider price.
VENUE_NATIVE_MAY_REPLACE = (vmap.R_NO_CONTRACT, vmap.R_AMBIGUOUS, vmap.R_LINE)
VENUE_NATIVE_MAY_REPLACE_IDENTITY = (R_NO_PREMAP,)


#: THE VENUE LEAGUE EACH PROVIDER COMPETITION IS LISTED UNDER, so the
#: venue-native search stays inside the competition the cycle confirmed. The
#: soccer keys come from VENUE_TOKEN_TO_PROVIDER_KEY (each entry supported by
#: the venue's own fixtures); `mlb` is the league token of every MLB contract
#: this lane has ever valued (`aec-mlb-...`) and of the 7 MLB events in the
#: 2026-09-29 capture -- whose 16 NPB and KBO events, on the SAME winner type,
#: carry `npb` and `kbo`. A key with no entry maps nothing
#: (VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED).
VENUE_LEAGUE_TOKENS_CONFIRMED = {"baseball_mlb": ("mlb",)}


def venue_league_tokens(sport_key) -> tuple:
    """The venue league token(s) for a provider key; () when none is named.
    `americanfootball_ncaaf` -> ('cfb',) and `americanfootball_nfl` ->
    ('nfl',) through the football token map, so a college price never lands
    on an `nfl` fixture, nor an NFL price on a `cfb` one."""
    got = set(VENUE_LEAGUE_TOKENS_CONFIRMED.get(str(sport_key), ()))
    got |= {t for t, k in VENUE_TOKEN_TO_PROVIDER_KEY.items()
            if k == str(sport_key)}
    got |= {t for t, k in VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY.items()
            if k == str(sport_key)}
    return tuple(sorted(got))


def provider_key_for_venue_token(token):
    """THE ONE LEAGUE IDENTITY: venue league token -> provider competition key
    (`cfb` -> `americanfootball_ncaaf`), from the same maps the cycle selects
    and resolves with; None when this lane maps no competition to it. The
    coverage census and coverage_integrity read league identity HERE, so the
    three can never disagree on what `cfb` is."""
    t = str(token or "").strip().lower()
    if t in VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY:
        return VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY[t]
    if t in VENUE_TOKEN_TO_PROVIDER_KEY:
        return VENUE_TOKEN_TO_PROVIDER_KEY[t]
    for key, toks in VENUE_LEAGUE_TOKENS_CONFIRMED.items():
        if t in toks:
            return key
    return None


def family_for_provider_key(key):
    """The sport family this lane prices a provider key under, or None."""
    k = str(key or "")
    for kk, fam in SPORTS_CONFIRMED:
        if kk == k:
            return fam
    if k in VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY.values():
        return "football"
    if k in VENUE_TOKEN_TO_PROVIDER_KEY.values():
        return "soccer"
    return None


def venue_native_may_replace(codes) -> bool:
    """True only when EVERY global mapping refusal is one the venue's own
    catalogue can answer. Pure."""
    codes = [str(c) for c in (codes or ())]
    return bool(codes) and all(c in VENUE_NATIVE_MAY_REPLACE for c in codes)


def venue_native_mapping(vn, *, replaced, global_mapped=None) -> dict:
    """The `mapped` record a venue-native identity travels under.

    NO GLOBAL ID IS CARRIED, even where the global match found a row: that row
    was matched on names alone -- for Wales v Norway it was the global DRAW
    market -- and a venue-native contract borrowing its condition would key
    the market rail, the fixture scope and the payout binding to a different
    instrument. The global evidence is kept, labelled as what was replaced.
    """
    g = dict(global_mapped or {})
    row = dict(g.get("market_row") or {})
    return {"version": vnat.VERSION, "mapped": True,
            "mapped_by": vnat.MAPPED_BY_VENUE_NATIVE,
            "condition_id": None, "global_slug": None, "market_row": None,
            "match": vnat.MATCHED_BY, "refusals": [],
            "matched_title": (vn.get("venue_native") or {}).get("event_slug"),
            "global_refusal_replaced": ",".join(str(c) for c in replaced),
            "global_refusals_replaced": list(replaced),
            "global_mapping_replaced": ({
                "condition_id": g.get("condition_id"),
                "slug": row.get("slug"), "title": row.get("title")}
                if g.get("mapped") else {
                "refusals": list(g.get("refusals") or []),
                "candidates": g.get("candidates")}),
            "venue_native": vn.get("venue_native")}

#: A venue quote older than this is not contemporaneous with a 30 s odds
#: quote. Same order of magnitude as the feed's own freshness rule,
#: deliberately: the comparison is only as fresh as its stalest side.
MAX_VENUE_QUOTE_AGE_S = 30.0

#: ── WHAT `marketData.transactTime` TRACKS: STILL UNRESOLVED. ────────
#:
#: A WITHDRAWN CONCLUSION, RECORDED AS WITHDRAWN. An earlier version of this
#: block asserted `VENUE_STAMP_SEMANTICS = "LAST_BOOK_CHANGE"` on the strength of
#: the 2026-09-27 probe: six contracts read twice, twenty seconds apart,
#: `transactTime` and the best ask identical on all six. That inference does not
#: hold. A cache serving the same representation to both reads produces the
#: identical observation -- the same bytes, therefore the same stamp and the same
#: ask -- and no response header was captured on either read, so caching was
#: neither shown nor excluded. Two hypotheses, one prediction; the sample cannot
#: choose between them, and more samples cannot either.
#:
#: The observation is kept, as an observation, in `bettor_venue_currency`
#: (`PROBE_2026_09_27`) together with the list of what it does not establish.
#: The stamp is carried as provenance, reported as an age, and DECIDES NOTHING:
#: an old value is not read as proof the book is stale, and a recent value is not
#: read as proof it is current.
VENUE_STAMP_SEMANTICS = vc.VENUE_STAMP_SEMANTICS            # "UNRESOLVED"
VENUE_STAMP_SEMANTICS_MEANS = vc.VENUE_STAMP_SEMANTICS_MEANS
STAMP_OBSERVATION = vc.PROBE_2026_09_27

#: ── WHAT ESTABLISHES THAT THE BOOK IS CURRENT ────────────────────────
#:
#: Not a sample and not one of our own clocks: a mechanism with a published
#: contract. `bettor_venue_currency` names three -- a live market-data
#: subscription (M1), a conditional revalidation answered 304 (M2), and the
#: origin's own generation instant from `Date` minus `Age` (M3, which bounds the
#: RESPONSE and not the book state, so it is a partial). Absent all three the
#: verdict is NOT_ESTABLISHED, which refuses -- and which is NOT the claim that
#: the book is stale, only that we cannot show it is current.
R_BOOK_CURRENCY_NOT_ESTABLISHED = "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"
R_BOOK_CURRENCY_CONTRADICTED = "VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT"

#: ── A CURRENCY REFUSAL STILL VALUES THE ODDS SOURCE ────────────────────
#:
#: THE GAP THIS CLOSES. The valuation was written only after an ok venue read,
#: and every read has refused on currency since d66e89e -- so no valuation
#: was written and the calibration cohort stopped at 53 fixtures, contrary to
#: `bettor_entry_execution`'s own claim that the calibration gate does not
#: block recording the valuation. Calibration scores the ODDS SOURCE against
#: the venue's settlement and needs no tradable venue price.
#:
#: So after exactly these refusals -- the book WAS read and displayed a
#: price; only its currency is unestablished or contradicted -- the SAME
#: evaluate/persist path runs with the refusal carried in, and the record is
#: sealed CALIBRATION_ONLY (see `bettor_valuation_purpose`). A read that
#: FAILED, errored or showed no depth is not here: there is no book to
#: describe, and the candidate stops where it always did.
CALIBRATION_ONLY_AFTER = (R_BOOK_CURRENCY_NOT_ESTABLISHED,
                          R_BOOK_CURRENCY_CONTRADICTED)
#: The named counter the spec asks for: valuations recorded inadmissible, for
#: calibration only. Reported beside the tally, never inside it -- the event's
#: own outcome stays REFUSED under the currency code, so "written" cannot
#: masquerade as admitted.
#: It is the key on the cycle result and on the heartbeat.
C_CALIBRATION_ONLY_RECORDED = "valuations_recorded_inadmissible_for_calibration"
#: Bounded like the entry lane: a calibration-only record costs the same
#: settlement-rules and fixture-scope reads an evaluation does. One is
#: attempted only while `evaluated + attempted < MAX_CALIBRATION_ONLY_PER_CYCLE`,
#: so a cycle makes at most this many of them. They NEVER take an entry
#: evaluation's slot: the entry lane is still bounded by `evaluated` alone, as
#: it was, so a cycle with both kinds can spend up to twice MAX_PER_CYCLE of
#: those reads -- the price of not letting calibration crowd out an entry.
MAX_CALIBRATION_ONLY_PER_CYCLE = MAX_PER_CYCLE

#: ── AND, SEPARATELY, OUR OWN PROCESSING DELAY ────────────────────────
#:
#: How long our own read may sit between arriving and being decided upon. Both
#: instants are ours and the interval is real, so this is a genuine check and it
#: is kept. It is ALSO, on its own, worth nothing as a freshness statement: a
#: cached snapshot generated four minutes ago, returned in 20 ms and received one
#: second before the decision passes this check comfortably and is not current.
#: It is therefore an ADDITIONAL requirement, never a substitute for one, and it
#: can never produce `fresh: true` by itself.
MAX_OUR_PROCESSING_DELAY_S = 10.0
R_OUR_PROCESSING_DELAY = "OUR_OWN_PROCESSING_DELAY_EXCEEDED_BEFORE_THE_DECISION"
PROCESSING_DELAY_IS_NOT_FRESHNESS = (
    "the interval between our receipt of a payload and the decision taken on "
    "it. It bounds OUR contribution to staleness and nothing upstream: a "
    "minutes-old cached snapshot received one second ago satisfies it. It is an "
    "additional requirement and never evidence of currency")


# ── control, fail closed ────────────────────────────────────────────

async def _running(conn) -> tuple:
    if str(os.environ.get(ENV_FLAG, "")).strip().lower() in ("off", "0",
                                                             "false"):
        return False, "%s is off in the environment" % ENV_FLAG
    # `ingestion_state`, the SAME table every other loop's stop lives in.
    # A second control table would mean a second place to look during an
    # incident, and the row is the authoritative stop because it is the
    # PROMPT one -- stopping this loop must never require a deploy.
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            CONTROL_KEY)
    except Exception as exc:                                   # noqa: BLE001
        return False, "CONTROL_UNREADABLE_%s" % type(exc).__name__
    if raw is None:
        return False, "CONTROL_ROW_ABSENT"
    if raw.strip().lower() != "true":
        return False, "CONTROL_ROW_NOT_TRUE"
    return True, "RUNNING"


async def _table_ready(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('public.external_valuations') IS NOT NULL"))
    except Exception:                                          # noqa: BLE001
        return False


# ── the provider read, from THIS process ────────────────────────────

async def fetch_odds(sport_key: str, *, api_key: str, timeout=20.0) -> dict:
    """One bulk h2h request. Returns the payload plus the quota headers.

    The key is passed in rather than read here so that the only place it
    is looked up is `credential_present`, and it is never interpolated
    into a log line or an exception message.
    """
    import httpx

    url = ("https://api.the-odds-api.com/v4/sports/%s/odds/" % sport_key)
    params = {"apiKey": api_key, "regions": "eu,uk,us",
              "markets": "h2h", "oddsFormat": "decimal"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        used = r.headers.get("x-requests-used")
        remaining = r.headers.get("x-requests-remaining")
        if r.status_code != 200:
            # The body can echo the query string, so it is NOT included.
            return {"ok": False, "status": r.status_code,
                    "refusal": R_PROVIDER_ERROR,
                    "credits_used": used, "credits_remaining": remaining,
                    "events": []}
        return {"ok": True, "status": 200, "events": r.json(),
                "credits_used": used, "credits_remaining": remaining,
                "received_at": time.time()}


def _read_resolution_blocking(slug: str) -> dict:
    """THE VENUE'S OWN ANSWER about whether this market has resolved.

    A slug dated yesterday and a start time in the past establish neither
    completion nor settlement -- the fixture may have been postponed,
    suspended, or simply not written back yet. `bettor_live_read` asks the
    venue's settlement endpoint first and the listing second, and returns
    five distinct statuses so PENDING, UNREADABLE and UNMATCHED can never
    be read as resolution.

    Paced like every other venue read, and never raises.
    """
    from .. import bettor_live_read as lr
    from .. import pmus
    from ..venue_pace import pace

    pace()
    try:
        client = pmus._get_client()
    except Exception as exc:                                    # noqa: BLE001
        return {"status": lr.UNREADABLE, "error": type(exc).__name__,
                "stage": "CLIENT_CONSTRUCTION", "market_slug": slug}
    try:
        return lr.read_resolution(client, slug)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": lr.UNREADABLE, "error": type(exc).__name__,
                "stage": "RESOLUTION_READ", "market_slug": slug}


#: THE VENUE'S OWN RULES PROSE, CACHED. A contract's settlement text does
#: not change minute to minute, and re-reading it every management cycle
#: would spend a paced venue call on a static string. Keyed by slug.
_RULES_CACHE: dict = {}
RULES_CACHE_TTL_S = 3600.0


def rules_cache_reset() -> None:
    """Forget the cached prose. For tests; never called in the loop."""
    _RULES_CACHE.clear()


def _read_venue_rules_blocking(slug: str, *, now=None) -> dict:
    """THE VENUE'S PUBLISHED SETTLEMENT PROSE for one contract.

    `bettor_venue_settlement.attest` could not establish the overtime rule
    and said so with the detail "no rules text exists on either side".
    That was true of what we HELD: the venue's listing carries
    `description` and `assetPriceTerms` and nobody read them. This reads
    them, paced like every other venue call and cached for an hour, and
    never raises -- an unreadable prose is a named error, not an exception
    on the decision path.
    """
    import time as _t

    from .. import bettor_live_read as lr
    from .. import pmus
    from ..venue_pace import pace

    at = float(now if now is not None else _t.time())
    hit = _RULES_CACHE.get(slug)
    if hit is not None and (at - float(hit.get("read_at") or 0.0)
                            ) <= RULES_CACHE_TTL_S:
        return dict(hit, from_cache=True)
    try:
        pace()
        client = pmus._get_client()
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "slug": slug, "error": type(exc).__name__,
                "stage": "CLIENT_CONSTRUCTION", "read_at": at,
                "from_cache": False}
    try:
        got = lr.read_rules_text(client, slug)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "slug": slug, "error": type(exc).__name__,
                "stage": "RULES_TEXT_READ", "read_at": at,
                "from_cache": False}
    got["read_at"] = at
    got["from_cache"] = False
    # Cache the ANSWER, including a named failure: a contract that
    # publishes no prose should not be re-asked every cycle either.
    _RULES_CACHE[slug] = dict(got)
    return got


def market_grid_blocking(slug: str, *, now=None) -> dict:
    """THE PRICES AN ORDER FOR THIS MARKET CAN CARRY, or a named refusal.

    The market's own `orderPriceMinTickSize` comes off the SAME listing row
    the rules prose is read from, through the SAME hourly cache -- so it costs
    no request the cycle was not already making for this contract -- and
    `bettor_book_snapshot.executable_grid` combines it with the adapter's
    whole cent. A listing that could not be read, or that names no tick, is
    NOT a cent tick: the grid refuses and every ladder on this market with it.
    """
    from .. import bettor_book_snapshot as bs

    got = dict(_read_venue_rules_blocking(slug, now=now) or {})
    grid = bs.executable_grid(
        got.get("tick_size"), field=got.get("tick_field"),
        read_at=got.get("read_at"),
        source=("pmus:/markets?slug=%s (the rules listing, cached %ss)"
                % (slug, int(RULES_CACHE_TTL_S))))
    grid["tick_from_cache"] = bool(got.get("from_cache"))
    if not grid.get("ok") and got.get("error"):
        grid["listing_error"] = got.get("error")
    return grid


def funded_tick_reader(client, slug):
    """The tick reader `bettor_funded_management.select_exit` is handed by
    the scheduled servicing pass: the cached listing above, not a second
    uncached read per position. `client` is accepted for the reader's shape
    and not used -- the cache resolves the transport the same way."""
    del client
    return market_grid_blocking(slug)


async def fetch_sport_catalogue(*, api_key: str, timeout=20.0) -> dict:
    """WHICH SPORTS THE PROVIDER OFFERS AT ALL. Costs no credits.

    `/v4/sports` is not metered, and it is the difference between "this
    sport is not in OUR set" (a decision of ours) and "the provider does
    not carry it" (a fact about them). A held exposure in an uncovered
    sport needs that distinction before anyone argues about extending the
    set: the first is a choice we can revisit, the second is not.
    """
    # IT MUST NOT RAISE, because `pass_once` now calls it on every cycle to
    # confirm a competition before spending a metered credit on it. This
    # function previously had no caller inside the cycle, so a transport error
    # propagating out of it was harmless; the moment it moved onto the cycle's
    # path, an unwrapped httpx error became "a provider outage kills the whole
    # cycle, including the funded servicing that does not need this provider at
    # all". The first run against a proxied test environment failed exactly
    # that way (httpx.ProxyError, three cycle tests), which is how it was found.
    #
    # A transport failure is the same ANSWER as a non-200: the catalogue is
    # unread, so nothing is confirmed and only the confirmed set runs.
    import httpx

    url = "https://api.the-odds-api.com/v4/sports/"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params={"apiKey": api_key,
                                              "all": "true"})
            used = r.headers.get("x-requests-used")
            remaining = r.headers.get("x-requests-remaining")
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code,
                        "refusal": R_PROVIDER_ERROR, "sports": [],
                        "credits_used": used, "credits_remaining": remaining}
            rows = r.json() or []
            return {"ok": True, "status": 200,
                    "sports": [{"key": x.get("key"), "group": x.get("group"),
                                "title": x.get("title"),
                                "active": x.get("active")} for x in rows],
                    "metered": False,
                    "credits_used": used, "credits_remaining": remaining,
                    "received_at": time.time()}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "status": None, "refusal": R_PROVIDER_ERROR,
                "sports": [], "error": type(exc).__name__,
                "why": ("the unmetered catalogue read failed at the transport "
                        "(%s). Unread is not empty: no candidate is confirmed "
                        "and none is fetched" % type(exc).__name__),
                "credits_used": None, "credits_remaining": None,
                "received_at": time.time()}


async def fetch_scores(sport_key: str, *, api_key: str, timeout=20.0) -> dict:
    """DOES THIS PROVIDER GIVE OBSERVED EVENT PROGRESS?

    The second-half loss exit is blocked on exactly one missing
    capability: a timestamped progress observation (period / quarter /
    inning / clock) that is NOT derived from a scheduled start time.
    `bettor_rn1x_policy.PROGRESS_FEED_CONNECTED` is empty by construction
    and that is what keeps the exit unavailable.

    This asks the provider's own scores endpoint what it actually returns,
    so the answer is measured rather than assumed. It reports the KEYS
    present on a live event, because the question is not "is there a
    score" but "is there a PERIOD", and those are different fields with
    different consequences: a score with no period cannot locate the
    halfway point, and substituting elapsed wall-clock time for it is
    precisely what the policy forbids.

    `daysFrom=1` is required for the endpoint to include completed and
    in-play events. It costs credits like any other request.
    """
    import httpx

    url = "https://api.the-odds-api.com/v4/sports/%s/scores/" % sport_key
    params = {"apiKey": api_key, "daysFrom": "1"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        out = {"status": r.status_code, "sport": sport_key,
               "credits_used": r.headers.get("x-requests-used"),
               "credits_remaining": r.headers.get("x-requests-remaining")}
        if r.status_code != 200:
            out["ok"] = False
            return out
        events = r.json() or []
        live = [e for e in events
                if not e.get("completed") and e.get("scores")]
        # THE KEY SETS, which is the whole point. Named, not summarised.
        keys = sorted({k for e in events for k in e})
        score_keys = sorted({k for e in events
                             for s in (e.get("scores") or []) for k in s})
        progress_fields = sorted(
            k for k in keys
            if k.lower() in ("period", "quarter", "inning", "half", "clock",
                             "time_remaining", "game_clock", "status",
                             "progress", "elapsed"))
        out.update({
            "ok": True, "events": len(events),
            "in_play_with_scores": len(live),
            "event_keys": keys, "score_keys": score_keys,
            "progress_fields_present": progress_fields,
            "carries_observed_period": bool(progress_fields),
            "verdict": (
                "a progress field is present -- inspect it before "
                "connecting" if progress_fields else
                "NO period/quarter/inning/clock field. Scores alone cannot "
                "locate the halfway point, and elapsed wall-clock time is "
                "forbidden as a substitute, so this endpoint does NOT "
                "close the second-half exit blocker"),
            "sample": (dict(live[0]) if live else
                       (dict(events[0]) if events else None)),
        })
        return out


def pinnacle_h2h(event: dict, *, received_at: float) -> dict | None:
    """Pinnacle's COMPLETE h2h outcome set for one event, or None.

    Per-OUTCOME sharp depth is counted here rather than per event, which
    is the distinction `edge/fairvalue/feed.py` made after its winner's
    curse audit: a 1c edge agreed by six books is a signal, the same 1c
    from one book is a rounding error.
    """
    books = event.get("bookmakers") or []
    pin = None
    for b in books:
        if b.get("key") == devig.BOOK:
            pin = b
            break
    if pin is None:
        return None
    mkt = None
    for m in (pin.get("markets") or []):
        if m.get("key") == "h2h":
            mkt = m
            break
    if mkt is None:
        return None

    prices, depth = {}, {}
    for o in (mkt.get("outcomes") or []):
        name = o.get("name")
        if name is None or o.get("price") in (None, 0):
            continue
        prices[str(name)] = float(o["price"])
    for b in books:
        if b.get("key") not in SHARP_BOOKS:
            continue
        for m in (b.get("markets") or []):
            if m.get("key") != "h2h":
                continue
            for o in (m.get("outcomes") or []):
                n = str(o.get("name"))
                if n in prices:
                    depth[n] = depth.get(n, 0) + 1
    return {"prices": prices, "depth": depth,
            "observed_at": mkt.get("last_update") or pin.get("last_update"),
            "received_at": received_at,
            "home": event.get("home_team"), "away": event.get("away_team"),
            "event_id": event.get("id"),
            "commence_time": event.get("commence_time")}


def primary_pinnacle_h2h(event: dict, *, received_at: float,
                         family: str, at: float) -> dict | None:
    """Preferred live WS source, with the original collector as fallback.

    Reads the existing owner in this process; never opens another socket.
    Discovery/independent-book evidence stays with the original event.
    """
    from .. import pinnapi_feed_runtime as feed
    from .. import pinnapi_primary as primary
    owner = feed._STATE.get("owner")
    return primary.select(
        owner.cache if owner else None, event,
        pinnacle_h2h(event, received_at=received_at), family=family,
        sharp_books=SHARP_BOOKS, at=at, max_age_s=PINNACLE_MAX_AGE_S,
        runtime_id=feed._STATE.get("runtime_id"))


def validate_primary_pinnacle(quote: dict, *, at: float) -> dict:
    from .. import pinnapi_feed_runtime as feed
    from .. import pinnapi_primary as primary
    owner = feed._STATE.get("owner")
    return primary.validate(
        owner.cache if owner else None, quote, at=at,
        max_age_s=PINNACLE_MAX_AGE_S,
        runtime_id=feed._STATE.get("runtime_id"))


# ── the venue side, read in the same cycle ──────────────────────────

#: A market our table still calls open, that the venue has forgotten. Run
#: 23's three probe reads all returned NotFoundError, on slugs like
#: `mlb-chc-mia-2026-09-05-total-9pt5` -- a fixture 19 days past. `closed`
#: and `resolved` are OUR flags, written by the refresher; a row it stopped
#: updating weeks ago is stale whatever those flags say. `updated_at` is
#: the honest recency signal, so the candidate set is bounded by it.
#:
#: This NARROWS the candidate set. It does not weaken any refusal and it
#: does not raise the request budget: the same LIMIT, the same
#: MAX_PER_CYCLE, fewer reads wasted on markets that cannot answer.
MARKET_STALE_AFTER_S = 2 * 24 * 3600

MARKETS_SQL = """
    SELECT condition_id, title, event_title, slug, closed, resolved,
           sport, updated_at
      FROM markets
     WHERE NOT closed AND NOT resolved
       AND sport = ANY($1::text[])
       AND updated_at >= now() - make_interval(secs => $2::float8)
     ORDER BY updated_at DESC
     LIMIT 4000
"""


#: Anything token-shaped is redacted before a venue message is stored. A
#: diagnostic is worth nothing if it cannot be shown, and an exception
#: string from an HTTP client can carry a signed URL.
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{20,}")
_SECRETISH = re.compile(r"(?i)(key|secret|token|signature|sig|auth)"
                        r"\s*[=:]\s*\S+")


def _sanitize(text: str, *, limit: int = 240) -> str:
    """Strip query strings, secrets and token-shaped runs from a message."""
    s = str(text or "")
    s = re.sub(r"\?[^\s'\"]+", "?<query-removed>", s)
    s = _SECRETISH.sub(r"\1=<redacted>", s)
    s = _TOKENISH.sub("<redacted>", s)
    return s[:limit]


def _venue_diagnostic(slug, exc, *, stage, code=None, feed=None) -> dict:
    """What a person needs to act on a venue refusal.

    The contract identifier, the stage, the venue's own code, and an HTTP
    status when the client exposes one -- `type(exc).__name__` alone was
    the reason `VENUE_BOOK_READ_RETURNED_ERROR 2` could not be acted on.
    Everything free-text goes through `_sanitize` first.
    """
    out = {"slug": slug, "stage": stage, "endpoint": "markets.book",
           "code": code, "feed": feed, "status": None, "detail": None,
           "exception": type(exc).__name__ if exc is not None else None}
    if exc is not None:
        for attr in ("status", "status_code", "code"):
            val = getattr(exc, attr, None)
            if isinstance(val, int):
                out["status"] = val
                break
        else:
            resp = getattr(exc, "response", None)
            val = getattr(resp, "status_code", None)
            if isinstance(val, int):
                out["status"] = val
        out["detail"] = _sanitize(exc)
    return out


# ── ONE VENUE READ, SHARED WITHIN SECONDS (2026-10-01) ────────────────
#
# The cycle reads a contract's book to value it and, seconds later, the
# paper decision on that same valuation read the SAME book again through the
# same paced client -- two requests where one answers both. After a 429 the
# second request met the venue cooldown (>= 5 s) and the decision was
# refused BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE: 6 of 17 and
# 8 of 19 completed-game book reads in the 18:00 and 17:00 UTC hours.
#
# So every SUCCESSFUL read is remembered here, briefly, with OUR receipt
# instant, and `recent_book` hands a copy to a reader that can use a read
# that young. Nothing is ever made fresher than it is: the copy keeps its
# original receipt instant, every decision still applies its own age limit
# to it, an error or a gate refusal is never remembered, and the cache is
# per process, bounded, and consulted only by the paper market-data client.
_RECENT_BOOKS: dict = {}
_RECENT_BOOKS_LOCK = __import__("threading").Lock()
RECENT_BOOKS_MAX = 256


def _remember_book(slug: str, out: dict) -> None:
    md = out.get("marketData") if isinstance(out, dict) else None
    if not slug or not isinstance(md, dict):
        return
    rec = {"marketData": md, "observed_at": time.time(),
           "feed": out.get("feed")}
    with _RECENT_BOOKS_LOCK:
        _RECENT_BOOKS[str(slug)] = rec
        if len(_RECENT_BOOKS) > RECENT_BOOKS_MAX:
            for k in sorted(_RECENT_BOOKS,
                            key=lambda k: _RECENT_BOOKS[k]["observed_at"]
                            )[:RECENT_BOOKS_MAX // 2]:
                _RECENT_BOOKS.pop(k, None)


def recent_book(slug: str, *, max_age_s: float, now: float | None = None
                ) -> dict | None:
    """A copy of the last SUCCESSFUL book read of `slug` in this process if
    our receipt of it is at most `max_age_s` old, else None. The copy keeps
    its original receipt instant (`observed_at`) and says it was shared."""
    at = time.time() if now is None else float(now)
    with _RECENT_BOOKS_LOCK:
        rec = _RECENT_BOOKS.get(str(slug or ""))
    if rec is None:
        return None
    age = at - float(rec["observed_at"])
    if age < 0 or age > float(max_age_s):
        return None
    return {"marketData": rec["marketData"],
            "observed_at": rec["observed_at"], "feed": rec.get("feed"),
            "shared_read": True, "shared_read_age_s": round(age, 3)}


def _read_book_blocking(slug: str, *,
                        deadline_epoch_s: float | None = None) -> dict:
    """One PACED public book read, off the event loop. Never raises.

    `pmus.book_read` rather than `bbo_read`, and that choice is load
    bearing: `bbo_read` returns four keys and discards the ladders, which
    is why `shadow_market_states.available_depth` was NULL on every row
    BETTOR ever wrote and INSUFFICIENT_DEPTH fired on half its decisions.
    This comparison needs depth, so it reads the whole `marketData` and
    puts it through `bettor_book_snapshot`, which exists for exactly this.

    `venue_pace.pace` is called because this loop shares the venue budget
    with the collector, which must not be starved by it.

    AND THE RESPONSE'S OWN METADATA IS COLLECTED HERE. The SDK ends its request
    with `return response.json()`, discarding every header -- including the three
    that bear on when the payload was generated and whether a cache served it
    (`Date`, `Age`, `Cache-Control`) and the two that permit revalidation
    (`ETag`, `Last-Modified`). Without them the loop has only its own receipt
    instant and a stamp of unresolved meaning, and neither can tell a quiet book
    from a cached one. `venue_http_observer` installs one httpx response hook on
    the SDK's own client and records that whitelist. It changes no request and no
    response; a failure to install is reported, not raised, because a missing
    observation must refuse rather than crash a market-data read.
    """
    from .. import pmus
    from .. import venue_http_observer as vho
    from ..venue_pace import pace

    # ── ONE LOGICAL READ, ONE ID, ONE SET OF COUNTERS ────────────────
    #
    # `pace()` is NOT called here any more. It is called inside the
    # transport, per actual request -- because the SDK can retry inside one
    # logical call and a gap applied out here paces the call rather than
    # the requests. Pacing here as well would double-charge the first
    # request's gap.
    from .. import venue_request_gate as grt

    read_id = grt.begin_read(slug=slug, deadline_epoch_s=deadline_epoch_s)
    grt.bind_read(read_id)
    try:
        try:
            client = pmus._get_client()
        except Exception as exc:                                # noqa: BLE001
            return {"marketData": None, "error": type(exc).__name__,
                    "diagnostic": _venue_diagnostic(
                        slug, exc, stage="CLIENT_CONSTRUCTION")}
        observer = vho.install(client)
        path = vho.book_path(slug)
        try:
            out = pmus.book_read(client, slug)
        except grt.VenueGateRefusal as ref:
            # OUR OWN GATE, NAMED AS OURS. Reporting this as a venue error
            # would send an operator to the venue for a decision we made.
            return {"marketData": None, "error": ref.refusal,
                    "refused_by": "OUR_REQUEST_GATE",
                    "gate_detail": ref.detail,
                    "attempts": grt.attempts_for_read(read_id),
                    "diagnostic": dict(ref.detail,
                                       slug=slug, stage="REQUEST_GATE",
                                       refusal=ref.refusal)}
        except Exception as exc:                                # noqa: BLE001
            return {"marketData": None, "error": type(exc).__name__,
                    "diagnostic": _venue_diagnostic(
                        slug, exc, stage="BOOK_READ")}
    # TAKEN, not peeked: a header set left behind and silently reused on a later
    # read is precisely the failure this evidence exists to detect.
        out["http_observation"] = vho.take(path)
        out["http_observer"] = observer
        # ── THE PER-READ ACCOUNTING, CARRIED OUT ─────────────────────
        out["attempts"] = grt.attempts_for_read(read_id)
        out["request_accounting"] = grt.read_state(read_id)
        if out.get("error"):
            # ── THE REPAIRED DIAGNOSTIC IS CARRIED, NOT REBUILT ──────
            #
            # THIS WAS THE THIRD DEFECT. `book_read` now returns
            # `error_detail` with the real HTTP status, Retry-After and
            # request id -- and this branch built a FRESH `_venue_diagnostic`
            # with a null exception and overwrote it. The final scheduled
            # result still read `status: null, exception: null, detail:
            # null`, so the repair never reached a reader. Extracting
            # metadata nobody projects is not a repair.
            #
            # The request context `_venue_diagnostic` adds is still wanted,
            # so the two are MERGED with the preserved fields winning: a
            # measured status must not be overwritten by an absent one.
            base = _venue_diagnostic(
                slug, None, stage="BOOK_READ", code=out.get("error"),
                feed=out.get("feed")) or {}
            detail = out.get("error_detail") or {}
            merged = dict(base)
            for k, v in detail.items():
                if v is not None or k not in merged:
                    merged[k] = v
            # AND THE PER-READ ACCOUNTING TRAVELS WITH IT.
            merged["attempts"] = out.get("attempts")
            merged["cooldown"] = out.get("cooldown")
            merged["request_accounting"] = out.get("request_accounting")
            out["diagnostic"] = merged
        else:
            _remember_book(slug, out)
        return out
    finally:
        # THE READ ID IS ALWAYS CLOSED AND UNBOUND. A leaked binding would
        # attribute the NEXT read's requests to this one, which is exactly
        # the cross-read contamination the per-read id exists to prevent.
        grt.bind_read(None)
        grt.end_read(read_id)


#: Fields a live-progress observation would have to arrive in. Matched
#: case-insensitively against the KEYS of a payload, never against values:
#: the question is whether the field exists at all.
PROGRESS_FIELD_NAMES = (
    "period", "periods", "half", "halves", "quarter", "inning", "innings",
    "clock", "gameclock", "timeremaining", "elapsed", "minute", "phase",
    "gamestate", "livestate", "eventstate", "status", "state",
)


def _progress_like_keys(obj, prefix="", out=None, depth=0):
    """Every key in a payload whose NAME could carry event progress."""
    out = [] if out is None else out
    if depth > 3:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = "%s.%s" % (prefix, k) if prefix else str(k)
            if str(k).lower().replace("_", "") in PROGRESS_FIELD_NAMES:
                out.append({"path": path,
                            "value_type": type(v).__name__,
                            # The VALUE is reported only as a type and a
                            # short repr: a status string is evidence, and
                            # a full payload dump in a diagnostic is not.
                            "sample": _sanitize(repr(v), limit=60)})
            _progress_like_keys(v, path, out, depth + 1)
    elif isinstance(obj, list) and obj:
        _progress_like_keys(obj[0], "%s[0]" % prefix, out, depth + 1)
    return out


def venue_event_progress_probe(limit=1) -> dict:
    """Does the VENUE's own event payload carry event progress?

    "No code reads a period field" is an inspection. This is the
    measurement: it fetches one page of the venue's events and reports
    which of its KEYS could carry progress. The distinction the exit
    blocker turns on -- "our integrations lack the field" versus "no
    accessible source exists" -- can only be settled on the payload.
    """
    from .. import pmus
    from ..venue_pace import pace

    out = {"source": "venue", "endpoint": "events.list",
           "reader": "pmus._get_client().events.list",
           "events_seen": 0, "top_level_keys": [],
           "progress_like_keys": [], "carries_observed_period": False}
    try:
        pace()
        client = pmus._get_client()
        resp = client.events.list({"limit": int(limit)}) or {}
    except Exception as exc:                                   # noqa: BLE001
        out.update(error=type(exc).__name__,
                   detail=_sanitize(exc, limit=160),
                   verdict="the venue event list could not be read, so this "
                           "says nothing either way")
        return out
    evs = resp.get("events") or []
    out["events_seen"] = len(evs)
    if not evs:
        out["verdict"] = "the venue returned no events; nothing to inspect"
        return out
    ev = evs[0] if isinstance(evs[0], dict) else {}
    out["top_level_keys"] = sorted(str(k) for k in ev.keys())
    hits = _progress_like_keys(ev)
    out["progress_like_keys"] = hits[:12]
    # A `status`/`state` key is not a period. Only a period/half/quarter/
    # inning/clock field can locate the halfway point, so the verdict is
    # narrow on purpose.
    period_names = ("period", "periods", "half", "halves", "quarter",
                    "inning", "innings", "clock", "gameclock",
                    "timeremaining", "elapsed", "minute")
    out["carries_observed_period"] = any(
        h["path"].split(".")[-1].lower().replace("_", "") in period_names
        for h in hits)
    out["verdict"] = (
        "the venue's event payload DOES carry a period-like field -- "
        "%s -- and the exit blocker should be reconsidered against it"
        % ", ".join(h["path"] for h in hits)
        if out["carries_observed_period"] else
        ("the venue's event payload carries no period, half, quarter, "
         "inning or clock field. Combined with the odds provider's scores "
         "endpoint (measured three times, progress_fields []), NEITHER "
         "integration we hold carries the observation -- which is a "
         "different statement from 'no accessible source exists'"))
    return out


async def resolve_venue_identity(conn, *, market_row, priced_outcome):
    """The GLOBAL fixture -> the VENUE's own contract, or a named refusal.

    Reuses `workers/premap.resolve`, which is the lane the copy path
    already trusts: deterministic keys out of `us_premap`, game agreement
    on the date (including the venue's calendar-adjacent dating, which a
    naive date equality gets wrong -- the C9 board has the bookmaker's
    2026-09-10 against the venue's 2026-09-09 for one fixture), and the
    venue's own side expansion so a side cannot be inferred.

    TWO THINGS ARE CHECKED, NOT ONE. The slug, and the INTENT. `resolve`
    returns the intent for buying that contract: LONG means it pays on the
    outcome we priced, SHORT means it pays on the complement. Comparing
    p(home win) against the ask on a SHORT contract is a sign error that
    would look like a large edge, so a non-LONG intent refuses by name.
    """
    from . import premap as _premap

    out = {"ok": False, "us_market_slug": None, "intent": None,
           "refusal": None, "resolver": "workers.premap.resolve",
           "global_slug": market_row.get("slug"),
           "condition_id": market_row.get("condition_id"),
           "priced_outcome": priced_outcome}
    try:
        got = await _premap.resolve(
            conn, market_row.get("title"), market_row.get("event_title"),
            priced_outcome, market_row.get("slug"),
            condition_id=market_row.get("condition_id"))
    except Exception as exc:                                   # noqa: BLE001
        out["refusal"] = R_NO_PREMAP
        out["why"] = "the resolver raised %s" % type(exc).__name__
        return out
    if not got or not got.get("market_slug"):
        out["refusal"] = R_NO_PREMAP
        out["why"] = ("the venue's own catalogue carries no contract for "
                      "this fixture and outcome. `markets.slug` is the "
                      "GLOBAL id and the venue does not accept it")
        return out
    out["us_market_slug"] = str(got["market_slug"])
    # ── THE VENUE'S OWN EVENT IDENTITY (audit finding A9) ────────────
    #
    # The event rail compares a candidate's event key against the open book's.
    # The book's now comes from `us_premap.event_slug` (see OPEN_BOOK_SQL), so
    # the candidate's must come from the SAME column or the two are in different
    # namespaces and never compare equal -- which is how passing the odds
    # provider's `event_id` here would silently keep the rail at zero even after
    # the query was repaired. One table, one namespace, read by its slug.
    #
    # ── AND WHETHER THAT CONTRACT IS THE FIXTURE AT ALL (2026-09-28) ──
    #
    # ONE ROUND TRIP, TWO QUESTIONS. This read used to select `event_slug`
    # alone. It now takes the whole catalogue row, because the row answers a
    # second question that was not being asked and is the more dangerous of the
    # two: does this contract settle on the real fixture, or on a SIMULATION of
    # it bearing the same club names?
    #
    # THE MEASURED REASON (research-sql run 258). The US venue lists ZERO
    # contracts carrying the token `epl` -- the English Premier League is not on
    # its board. What it does list is 420 rows across 70 events titled
    # "eBattles: Arsenal vs. Chelsea" and the like, classified
    # `efootball_team_full_time_winner`: video game matches between real club
    # names. Every check this lane makes would pass on one of those. The clubs
    # match, the date matches, the market type matches, the money line exists.
    # The event is not the one occurring.
    #
    # So realism is established from the VENUE'S OWN WORDS before the intent is
    # even looked at, and an unreadable or silent row refuses rather than
    # proceeds. `bettor_venue_realism` carries the full finding and the reason a
    # league-token pattern is never used to decide it.
    row = None
    try:
        row = await conn.fetchrow(vreal.CATALOGUE_SQL, out["us_market_slug"])
        out["venue_event_key"] = (row or {}).get("event_slug")
    except Exception as exc:                                   # noqa: BLE001
        out["venue_event_key"] = None
        out["venue_event_key_error"] = type(exc).__name__
        out["venue_catalogue_read_error"] = type(exc).__name__
    out["venue_event_key_is"] = (
        "us_premap.event_slug for this contract -- the VENUE's event, which two "
        "contracts on one fixture share. Not the odds provider's event id, "
        "which is a different namespace")
    realism = vreal.classify(row)
    if out.get("venue_catalogue_read_error"):
        realism = vreal.classify(None)
        realism["read_error"] = out["venue_catalogue_read_error"]
    out["realism"] = {k: realism.get(k) for k in
                      ("verdict", "evidence", "why", "read_error")}
    if realism.get("verdict") != vreal.REAL:
        out["refusal"] = realism["refusal"]
        out["why"] = realism.get("why") or "venue contract realism not REAL"
        return out
    intent = str(got.get("intent") or "")
    out["intent"] = got.get("intent")
    # BOTH INTENTS ARE VALID RESOLVED EXPOSURES.
    #
    # This used to refuse anything that was not BUY_LONG, and the refusal
    # was CORRECT while `venue_quote` could only read the offer ladder:
    # pricing p(outcome) against the long book's ask on a short leg is a
    # sign error. Run 28 refused eleven of forty-one candidates there.
    #
    # The resolver was never wrong. On the `aec-` family both sides carry
    # the same identifier and the side is the INTENT, so BUY_SHORT is the
    # resolver correctly saying "the exposure to this outcome is the short
    # leg of that market". Now that the reader consumes the ladder the
    # intent names, the refusal narrows to intents nothing can price.
    if intent not in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
        out["refusal"] = R_INTENT_NOT_LONG
        out["why"] = ("the resolved contract's buy intent is %r, which "
                      "names no side this reader can consume"
                      % (got.get("intent"),))
        return out
    # ── THREE SEPARATE RELATIONSHIPS, NOT ONE INFERENCE ──────────────
    #
    # THE DEFECT THIS REPLACES, and it was mine. This wrapper did:
    #
    #     short = intent == "ORDER_INTENT_BUY_SHORT"
    #     pays_on_priced_outcome = not short
    #     payout_event = "NOT(%s)" % priced_outcome if short else ...
    #
    # which reads the payout event off the INTENT. That is wrong, and it
    # fabricates a positive edge. `premap.resolve` is asked for a specific
    # OUTCOME and returns the row matching it together with the intent
    # that BUYS it. A BUY_SHORT result for "Chicago Cubs" means Cubs is
    # the venue's SHORT side; the contract still PAYS ON CUBS. It does not
    # become NOT(Cubs).
    #
    # With p(Cubs)=0.30 and an acquisition cost of 0.40 the true edge is
    # -0.10. The old inference complemented a probability that already
    # described the selected exposure and produced +0.30.
    #
    # So the three relationships are now stated apart:
    #
    #   1 INTENT -> WHICH LADDER pays for it (short consumes the bids at
    #     1 - bid). That is all the intent decides.
    #   2 REQUESTED OUTCOME + MATCHED VENUE SIDE + settlement terms ->
    #     WHICH EVENT PAYS. The resolver matched a side FOR the requested
    #     outcome, so the payout event is that outcome.
    #   3 The probability is complemented ONLY when its source event is
    #     demonstrably the complement of the payout event.
    #
    # The resolver's own evidence is PRESERVED rather than discarded and
    # reconstructed: side_norm, identifier, matched_by and question travel
    # onto the row, so a reader can audit which venue side was chosen.
    # THE KEY NAMES THE RESOLVER ACTUALLY RETURNS.
    #
    # THE DEFECT THIS FIXES, and it was silent. This block read
    # `side_norm`, `identifier` and `question` -- none of which
    # `premap.resolve` puts in its result. It returns the side under
    # `outcome`, the venue identifier under `market_slug`, and the
    # question under `title`. So every candidate row in production carried
    #
    #     matched_side_norm  null
    #     matched_identifier null
    #     matched_question   null
    #
    # while the comment above promised the resolver's evidence was
    # "PRESERVED rather than discarded and reconstructed". It was
    # discarded. Nothing downstream read the nulls, so nothing broke and
    # nothing complained -- which is exactly why payout identity could not
    # be audited from the row it was supposed to be auditable from.
    out["matched_side_norm"] = got.get("outcome")
    out["matched_identifier"] = got.get("market_slug")
    out["matched_by"] = got.get("matched_by")
    out["matched_question"] = got.get("title")
    out["matched_league_alias"] = got.get("league_alias")
    out["resolver_asked_for"] = str(priced_outcome)

    # ── WHICH PERIOD THE VENUE CONTRACT PAYS ON, ESTABLISHED ─────────
    #
    # Before this, the lane set `contract["period"] = "FULL_GAME"` as a
    # literal and passed the same literal to the valuation, so both sides
    # of the comparison agreed on a period neither had read. The venue's
    # board carries `...-2026-09-25-i6-draw` (inning six) inside the
    # money-line family, so the exposure is real.
    # THE CATALOGUE'S OWN FIELDS, read for this contract. `premap.resolve`
    # does not return the kind or the sibling count, so they are read here
    # from the same table the resolver matched in -- one bounded query, no
    # second resolver.
    pmeta = await period_metadata(conn, out["us_market_slug"])
    vtype = venue_market_type(out["us_market_slug"])
    out["venue_market_type"] = vtype
    period = vmap.period_of_venue_slug(
        out["us_market_slug"],
        # The catalogue's `side_norm` is preferred over the resolver's
        # returned outcome: the decomposition has to be checked against
        # the venue's own spelling, not ours.
        side=pmeta.get("side_norm") or got.get("outcome"),
        event_slug=pmeta.get("event_slug"),
        kind=pmeta.get("kind"),
        sibling_markets=pmeta.get("sibling_markets"),
        # v4: the participant test that closes the futures/trophy hole v3
        # named. The catalogue's OWN event title, read with the same " vs "
        # split `shadow-mapgap` uses on our side of the crossing.
        event_title=pmeta.get("event_title"),
        participant_witness=pmeta.get("side_norms_on_slug"),
        # THE AUTHORITY. The venue's own market type, from its board rather
        # than from our slug reading. Absent -> the rule refuses by name.
        # v2 is not persisted anywhere this lane may read, so it is None
        # and v1 carries both structure and scope -- it is strictly more
        # specific, and the copy lane already trusts it that way.
        sports_market_type_v2=None,
        sports_market_type=pmeta.get("sports_type"),
        type_metadata_available=bool(pmeta.get("sports_type")))
    period["catalogue"] = pmeta
    period["venue_market_type_read"] = vtype
    out["period_evidence"] = period
    if period.get("period") != vmap.FULL_MATCH:
        # THE SPECIFIC REASON, not one blanket unknown. An unsupported kind,
        # a slug that will not decompose, and a field of entrants have three
        # different remedies and the census has to be able to tell them
        # apart.
        out["refusal"] = ((period.get("refusals") or [None])[0]
                          or vmap.R_PERIOD_UNKNOWN)
        out["why"] = period.get("why")
        out["period_evidence"] = period
        return out
    out["period"] = "FULL_GAME"
    out["period_basis"] = period["basis"]
    out["ladder_side"] = ("BID" if intent == "ORDER_INTENT_BUY_SHORT"
                          else "ASK")
    out["intent_selects"] = (
        "the ladder that supplies acquisition cost, and nothing else. It "
        "does NOT name the payout event")

    # THE PAYOUT EVENT. The resolver was asked for `priced_outcome` and
    # returned the side that buys it, so that outcome is what pays.
    out["payout_event"] = str(priced_outcome)
    out["payout_event_basis"] = (
        "RESOLVER_MATCHED_A_SIDE_FOR_THE_REQUESTED_OUTCOME")
    # THE PROBABILITY'S SOURCE EVENT is the same outcome the de-vig
    # prices, so no complement is involved and none is applied. The flag
    # stays in the vocabulary -- a genuinely complementary payout is a
    # real case -- but it is only ever set when the two events are
    # demonstrably complementary, which this path never asserts from the
    # intent.
    out["probability_event"] = str(priced_outcome)
    out["payout_is_complement"] = False
    out["complement_note"] = (
        "payout_is_complement is false here because the resolver matched "
        "a side FOR the requested outcome: the probability's event and "
        "the payout event are the SAME event. A short intent means the "
        "cost comes off the bid ladder, not that the payout inverted. "
        "Where a complement IS involved, remember it is every other "
        "outcome together -- on a three-way book NOT(home) covers away "
        "AND draw, and is not p(the other team)")
    out["ok"] = True
    return out


#: The venue's own catalogue, asked whether it lists a SEPARATE draw
#: contract for this event. Queried by `event_slug` rather than parsed out
#: of the slug's grammar: the grammar is premap's to own, and a data
#: question with a data answer cannot be wrong about a naming convention.
DRAW_SIBLING_SQL = """
    SELECT p2.market_slug
      FROM us_premap p1
      JOIN us_premap p2 ON p2.event_slug = p1.event_slug
     WHERE p1.market_slug = $1
       AND p2.market_slug <> p1.market_slug
       AND lower(p2.market_slug) LIKE '%-draw'
     LIMIT 1
"""

EVENT_ROW_SQL = """
    SELECT event_slug, team_league, sports_type, game_start, kind, side_norm
      FROM us_premap WHERE market_slug = $1 LIMIT 1
"""

#: THE STRUCTURED METADATA THE PERIOD CHECK NEEDS, in one query: the
#: catalogue's own kind, event and side for this contract, plus HOW MANY
#: contracts it publishes for that event. The count is what separates a
#: two-participant match (2 or 3) from a trophy or a conference winner
#: (one per entrant) -- by counting, never by reading a name.
#: `sports_type` IS `sportsMarketType`. The catalogue writer stamps it from
#: `m.get("sportsMarketType")` (workers/premap.py) and the copy lane already
#: trusts it as a scope discriminator -- its `_C7_FULL_TIME` is the literal
#: `soccer_team_full_time_winner`. So the venue's own market type reaches
#: this lane through the CATALOGUE, like every other catalogue field, and
#: the funding guard that permits only `book_read` and `_get_client` off
#: `pmus` stays exactly as it is. No schema change and no new reader.
#:
#: THE COLUMN MAY BE ABSENT. It arrives with the C6 column set (migration
#: 055 / the bootstrap's ALTER), so the read is written to tolerate its
#: absence and report it as VENUE_MARKET_TYPE_METADATA_NOT_RETAINED rather
#: than to fail the query.
PERIOD_METADATA_SQL = """
    SELECT p1.kind, p1.event_slug, p1.side_norm, p1.event_title,
           p1.sports_type,
           (SELECT count(DISTINCT p2.market_slug)
              FROM us_premap p2
             WHERE p2.event_slug = p1.event_slug) AS sibling_markets,
           (SELECT count(DISTINCT p3.side_norm)
              FROM us_premap p3
             WHERE p3.market_slug = p1.market_slug) AS side_norms_on_slug
      FROM us_premap p1
     WHERE p1.market_slug = $1
     LIMIT 1
"""


def venue_market_type(us_market_slug: str) -> dict:
    """THE VENUE'S OWN MARKET TYPE -- WHICH THIS LANE CANNOT YET REACH.

    THE FIELD EXISTS AND IS RETAINED. `pmus.list_desk_events` now carries
    `sports_market_type_v2`, `sports_market_type`, `team` and `team_id` on
    every desk market row, and `/api/admin/venue-competitions?token=` shows
    them raw.

    THE GUARD THAT STOPS THIS LANE READING IT, and it is deliberate.
    `test_ext_shadow_cannot_fund` asserts that exactly two names are taken
    off `pmus` here -- `book_read` and `_get_client` -- so the lane cannot
    acquire a venue capability by accident. An earlier version of this
    function called `pmus.list_desk_events()`, which is a cache read rather
    than a venue call, and the guard refused it anyway. THE GUARD IS RIGHT
    AND IT IS NOT BEING WIDENED: a control that only holds when the thing
    it blocks looks dangerous is not a control.

    SO THE TYPE IS NOT AVAILABLE TO THIS LANE, AND THAT IS REPORTED RATHER
    THAN WORKED AROUND. The period rule refuses with
    VENUE_MARKET_TYPE_METADATA_NOT_RETAINED, which names precisely what is
    missing and where. The authorized path is for the catalogue writer --
    the component that already reads the venue's board -- to persist these
    two fields beside the rest of `us_premap`, which this lane then reads
    like every other catalogue field. That is a schema change to a shared
    table and it is NOT bundled into this release.
    """
    return {"source": "NOT_AVAILABLE_TO_THIS_LANE",
            "available": False,
            "sports_market_type_v2": None, "sports_market_type": None,
            "team": None, "team_id": None,
            "why": ("the venue's own market type is retained on the desk "
                    "board but this lane may not read it: the funding guard "
                    "permits only `book_read` and `_get_client` off `pmus`, "
                    "and widening that control to fetch metadata would be "
                    "the wrong trade. The catalogue writer must persist "
                    "`sportsMarketTypeV2` and `sportsMarketType` into "
                    "`us_premap` for this lane to reach them"),
            "remedy": ("persist the two fields into `us_premap` from the "
                       "catalogue writer (workers.premap.ensure_schema and "
                       "its writer), then read them in `period_metadata` "
                       "beside the rest of the catalogue"),
            "inspectable_at": ("/api/admin/venue-competitions?token=<league>"
                               " -> token_events[].market_types")}


PERIOD_METADATA_SQL_PRE_C6 = PERIOD_METADATA_SQL.replace(
    "p1.sports_type,", "NULL::text AS sports_type,")


async def period_metadata(conn, us_market_slug: str) -> dict:
    """The catalogue's own kind, event, side and sibling count, or empties.

    A read that FAILS leaves every field None, and the period check then
    refuses by name. Returning partial defaults would be the silent
    fallback this lane exists to avoid.
    """
    out = {"source": "us_premap", "read": False, "kind": None,
           "event_slug": None, "side_norm": None, "sibling_markets": None,
           "event_title": None, "side_norms_on_slug": None,
           "sports_type": None, "sports_type_column_present": None}
    try:
        row = await conn.fetchrow(PERIOD_METADATA_SQL, us_market_slug)
        out["sports_type_column_present"] = True
    except Exception as exc:                                   # noqa: BLE001
        # THE C6 COLUMN MAY NOT BE THERE. A database without migration 055
        # has no `sports_type`, and that is a different fact from the read
        # failing -- so the pre-C6 query is tried and the absence recorded.
        out["error"] = type(exc).__name__
        try:
            row = await conn.fetchrow(PERIOD_METADATA_SQL_PRE_C6,
                                      us_market_slug)
            out["sports_type_column_present"] = False
            out.pop("error", None)
            out["why_no_type"] = (
                "this database has no `sports_type` column (the C6 set, "
                "migration 055), so the venue's own market type is not "
                "retained here and scope cannot be established")
        except Exception:                                      # noqa: BLE001
            return out
    if row is None:
        out["why"] = ("the venue's catalogue has no row for this market "
                      "slug, so none of its structured fields exist")
        return out
    out.update(read=True, kind=row["kind"], event_slug=row["event_slug"],
               side_norm=row["side_norm"],
               sibling_markets=row["sibling_markets"],
               event_title=row["event_title"],
               side_norms_on_slug=row["side_norms_on_slug"])
    try:
        out["sports_type"] = row["sports_type"]
    except (KeyError, IndexError):
        out["sports_type"] = None
    return out


async def venue_settlement_evidence(conn, us_market_slug: str) -> dict:
    """What the VENUE's catalogue shows about this event's settlement.

    One fact does real work here: whether the venue lists a separate DRAW
    contract. If it does, its "will A beat B" binary cannot be paying on a
    draw, because the draw is a different contract -- which is the same
    treatment the bookmaker's 3-outcome h2h gives it. That is an
    attestation from two independent catalogues, not an assumption.
    """
    out = {"source": "us_premap", "market_slug": us_market_slug,
           "draw_contract_present": False, "draw_slug": None,
           "event_slug": None, "team_league": None, "sports_type": None,
           "game_start": None, "kind": None, "side_norm": None,
           "readable": False}
    try:
        row = await conn.fetchrow(EVENT_ROW_SQL, us_market_slug)
        if row is not None:
            out.update(readable=True, event_slug=row["event_slug"],
                       team_league=row["team_league"],
                       sports_type=row["sports_type"],
                       kind=row["kind"], side_norm=row["side_norm"],
                       game_start=(row["game_start"].isoformat()
                                   if row["game_start"] is not None else None))
        draw = await conn.fetchval(DRAW_SIBLING_SQL, us_market_slug)
        if draw:
            out.update(draw_contract_present=True, draw_slug=str(draw))
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = type(exc).__name__

    # THE VENUE'S OWN PUBLISHED PROSE, FETCHED RATHER THAN ASSUMED ABSENT.
    #
    # `attest` compares the bookmaker's captured terms against
    # `rules_text`, condition by condition. This function never supplied
    # it, so every entry candidate's settlement comparison was UNKNOWN
    # for want of a read -- which is not the same as the venue publishing
    # nothing, and `bettor_live_read.read_rules_text` exists precisely
    # because that distinction was got wrong once already.
    #
    # One listing read per candidate, paced and bounded by MAX_PER_CYCLE,
    # and it never raises: a failed read leaves the prose absent and the
    # comparison UNKNOWN, which is the same conservative answer as before
    # -- it just now says which of the two happened.
    # THE READER THAT ALREADY EXISTED. I had added a second, uncached one
    # beside it -- a parallel implementation of the same venue call that
    # would spend a paced request per candidate per cycle on a string that
    # does not change. `_read_venue_rules_blocking` caches for an hour,
    # keyed by slug, and reports a named failure rather than raising.
    rules = await asyncio.to_thread(_read_venue_rules_blocking,
                                    us_market_slug)
    out["rules_text"] = rules.get("rules_text")
    out["rules_source"] = rules.get("source")
    out["rules_read"] = rules
    return out


def _displayed_not_for_orders(book, *, intent, slug, read_at,
                              currency, venue_ts=None,
                              venue_clock_basis=None) -> dict:
    """WHAT A REFUSED BOOK DISPLAYED ON THE SIDE THIS INTENT CONSUMES.

    Built only for a read `venue_quote` then REFUSES, so it is never usable
    for orders -- the flag is a constant, not a computation -- and it names
    the currency verdict that made it so. Pure: it parses the payload the
    read already returned and makes no request.
    """
    from .. import bettor_book_snapshot as bs
    from .. import bettor_valuation_purpose as _vp

    try:
        lad = bs.acquisition_ladder((book or {}).get("marketData"),
                                    intent=intent)
    except Exception as exc:                                   # noqa: BLE001
        lad = {"ok": False, "refusal": "DISPLAYED_LADDER_UNPARSEABLE:%s"
               % type(exc).__name__}
    ok = bool(lad.get("ok")) and lad.get("best_acquisition_price") is not None
    return {"ok": ok,
            "usable_for_orders": False,
            "what_this_is": _vp.DISPLAYED_NOT_AN_ORDER_PRICE,
            "acquisition_price": (lad.get("best_acquisition_price")
                                  if ok else None),
            "api_price": lad.get("best_api_price") if ok else None,
            "side_consumed": lad.get("side_consumed"),
            "pays_on": lad.get("pays_on"),
            "depth": lad.get("displayed_depth") if ok else None,
            "levels_read": lad.get("levels_read"),
            "refusal": None if ok else (lad.get("refusal") or R_NO_DEPTH),
            "intent": intent, "slug": slug, "read_at": read_at,
            # THE VENUE'S OWN STAMP (marketData.transactTime, parsed), or
            # None with the clock's basis when it supplied none. Provenance
            # for research observations; its meaning is unresolved and it
            # decides nothing (VENUE_STAMP_SEMANTICS).
            "venue_ts": venue_ts, "venue_clock_basis": venue_clock_basis,
            "book_currency_verdict": (currency or {}).get("verdict"),
            "book_currency_mechanism": (currency or {}).get("mechanism")}


def _calibration_only_basis(vq) -> dict | None:
    """The refused venue read a CALIBRATION_ONLY record may be built on.

    None unless the read refused with one of CALIBRATION_ONLY_AFTER AND
    carries the displayed block `venue_quote` attaches to exactly those
    refusals -- so a stubbed or older quote shape, or any other refusal,
    stops the candidate where it always stopped.
    """
    vq = vq if isinstance(vq, dict) else {}
    if vq.get("ok") or vq.get("refusal") not in CALIBRATION_ONLY_AFTER:
        return None
    shown = vq.get("displayed_not_for_orders")
    if not isinstance(shown, dict):
        return None
    cur = vq.get("book_currency") or {}
    return {"refusal": vq["refusal"],
            # FLAGGED AGAIN HERE, not trusted from the quote: whatever built
            # the block, a record built on a refused read is never orderable.
            "displayed": dict(shown, usable_for_orders=False),
            "book_currency": {k: cur.get(k) for k in (
                "verdict", "mechanism", "mechanisms_unavailable", "partial",
                "why")},
            "venue_read_why": vq.get("why")}


def _displayed_market_state(basis: dict) -> dict:
    """The market state a calibration-only valuation is compared against.

    The DISPLAYED price, labelled as such in `ask_basis`. `evaluate` then
    seals the record, moving this price out of `executable_price`; it is
    passed at all only so the gate's economics are traced at the price the
    venue showed, which is what "every reachable admission check" means.
    """
    d = dict((basis or {}).get("displayed") or {})
    return {"ask": d.get("acquisition_price"),
            "api_price": d.get("api_price"),
            "side_consumed": d.get("side_consumed"),
            "depth": d.get("depth"),
            "readable": bool(d.get("ok")),
            "ask_basis": ("DISPLAYED_ON_A_BOOK_WHOSE_CURRENCY_IS_NOT_"
                          "ESTABLISHED__NOT_USABLE_FOR_ORDERS")}


async def venue_quote(conn, *, us_slug, intent, now=None, size=None,
                      subscription=None, revalidation=None):
    """Contemporaneous ACQUISITION ladder for one venue contract.

    `intent` IS THE SIDE, AND IT IS REQUIRED. This used to take an
    `outcome_index` that it accepted and never read: it returned BEST_ASK
    for the market however the caller asked. On the `aec-` family both
    sides carry the SAME identifier -- equal to the slug -- and the side
    is carried only by the order intent, so half of all candidates were
    being priced off the wrong book. Run 28 refused eleven of forty-one
    candidates for exactly that reason, correctly. A parameter that looks
    like it selects a side and does not is how that hid, so it is gone
    rather than kept and ignored.

    LONG consumes the OFFER ladder at its published price. SHORT consumes
    the BID ladder at (1 - bid), which is the conversion `pmus.slug_bid`
    settled against five live markets exact to the cent. The per-share
    arithmetic lives in `bettor_book_snapshot.cost_per_share`, extracted
    rather than imported from the execution module on purpose: this loop
    is guarded by a test asserting no order-submission path is even
    nameable from it, and a shadow loop should not reach a submit
    function transitively for one line of arithmetic. A test pins the two
    definitions equal so they cannot drift.

    A quote whose CURRENCY IS NOT ESTABLISHED within MAX_VENUE_QUOTE_AGE_S
    is REFUSED rather than used: the comparison is only as fresh as its
    stalest side, and pairing a 3 s Pinnacle price against a venue ask of
    unknown age manufactures an edge out of the gap between them. What may
    establish it is a mechanism with a published contract, and the two
    optional arguments are how a caller supplies one:

      `subscription`  {"alive_at", "last_update_at"} from a live market-data
                      subscription for this market (mechanism M1);
      `revalidation`  the result of a conditional re-request on the book
                      path, 304 meaning the origin affirms it (M2).

    Supplying neither is not an error and not a loophole: the verdict is
    then NOT_ESTABLISHED and the candidate refuses, with the mechanisms it
    lacked named on the refusal.

    The depth returned is DISPLAYED depth, which is an observation and
    explicitly not a queue position -- the snapshot module says so in the
    payload itself and `P_FILL` stays NOT_IDENTIFIED regardless.

    ── THE VERDICT INSTANT IS NEVER BEFORE THE READ (map4 §9 D8) ──────
    `now` is the CALLER's instant, and the scheduled cycle takes it BEFORE
    the read. Currency used to be evaluated at that instant -- so a book was
    judged as of a moment before we had it -- and `now - read_at` was
    negative, so OUR_OWN_PROCESSING_DELAY could never fire here. The verdict
    is now taken at `max(now, our post-read receipt)`: never earlier than
    the payload existed on our side, and later only when a caller asks to
    judge the read at a later decision. Re-ageing only ever makes the
    verdict stricter.

    ── A CURRENCY REFUSAL CARRIES WHAT THE BOOK DISPLAYED ─────────────
    On VENUE_BOOK_CURRENCY_NOT_ESTABLISHED, _CONTRADICTED_BY_CONTRACT and
    OUR_OWN_PROCESSING_DELAY the book WAS read. The refusal now carries the
    ladder this intent would consume under `displayed_not_for_orders`,
    flagged `usable_for_orders: False` -- the same labelling
    `observation_quote` uses. It is there so the calibration-only record can
    say what the venue displayed; no reader may size, reserve or send
    against it, and it is deliberately NOT under `ask`, `acquisition_price`
    or `acquisition_ladder`, the keys an order path reads.
    """
    from .. import bettor_book_snapshot as bs

    # THE VENUE'S OWN SLUG, SUPPLIED BY THE CALLER. This used to read
    # `markets.slug` -- the GLOBAL catalogue's id -- and hand it to a US
    # endpoint, which is why every read answered NotFoundError. The
    # crossing now happens once, in `resolve_venue_identity`, through the
    # resolver the copy lane uses.
    slug = str(us_slug or "")
    if not slug:
        return {"ok": False, "refusal": R_NO_SLUG,
                "why": ("no venue-native slug was supplied. A global "
                        "condition id is not a US market slug and must "
                        "never be passed as one")}
    # THE INSTANT THE REQUEST LEFT. Recorded because it is real and because
    # our own latency is worth knowing -- NOT because it bounds the age of
    # the book. See the note on our own clocks below.
    request_sent_at = time.time()
    try:
        book = await asyncio.wait_for(
            asyncio.to_thread(_read_book_blocking, slug),
            timeout=VENUE_TIMEOUT_S)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": R_VENUE_READ_FAILED,
                "why": "book read failed: %s" % type(exc).__name__,
                "exception": type(exc).__name__,
                "diagnostic": _venue_diagnostic(slug, exc,
                                                stage="BOOK_READ_AWAIT")}
    if book.get("error"):
        diag = book.get("diagnostic") or {}
        return {"ok": False, "refusal": R_VENUE_READ_ERROR,
                "why": "venue read error: %s" % book["error"],
                "venue_error": _sanitize(book["error"], limit=80),
                "diagnostic": diag}

    read_at = time.time()
    # THE INSTANT THE VERDICT IS TAKEN AT: never before our own receipt of
    # the payload (see the docstring, D8).
    verdict_at = (read_at if now is None
                  else max(float(now), float(read_at)))
    snap = bs.snapshot(book.get("marketData"), symbol=slug,
                       captured_at=read_at)
    ask = snap.get("BEST_ASK")
    if ask in (None, bs.NOT_IDENTIFIED):
        return {"ok": False, "refusal": R_NO_DEPTH,
                "why": "the book has no ask side",
                "parse_status": snap.get("PARSE_STATUS")}
    depth_raw = (snap.get("DISPLAYED_DEPTH_AT_T0") or {}).get("ask")
    try:
        depth = float(depth_raw)
    except (TypeError, ValueError):
        depth = 0.0
    if depth <= 0:
        return {"ok": False, "refusal": R_NO_DEPTH,
                "why": "the ask side shows no displayed quantity"}

    # THE VENUE'S OWN CLOCK, when it gives one. WHAT IT MEANS IS NOT
    # ESTABLISHED -- see VENUE_CLOCK_SEMANTICS above. An earlier version of
    # this comment asserted it is "the venue stating when this book was
    # true"; that is one of two live readings and it was never measured.
    # Falling back to our read time would make every quote fresh by
    # construction in a read-then-decide loop, so the fallback is NAMED and
    # the gate is left refusing until the probe answers.
    # THE DEFECT THIS FIXES, measured in production on build 51f20e7.
    #
    # `float(venue_ts)` was the whole parse. The venue does not send a
    # number: `marketData.transactTime` is an ISO-8601 UTC string with a
    # bare `Z` and VARIABLE fractional precision, up to nanoseconds --
    # "2026-09-21T18:20:25.743291447Z". `float()` raises on it, so every
    # book read recorded VENUE_CLOCK_UNPARSEABLE, `venue_age_s` stayed
    # unmeasured, and `_entry_freshness` returned `fresh: null`. That is
    # UNKNOWN, and UNKNOWN blocks -- so nine positive-edge candidates were
    # refused for a clock we never read rather than a book that was stale.
    #
    # CORRECTION TO AN EARLIER VERSION OF THIS COMMENT: it claimed 3.11's
    # `datetime.fromisoformat` rejects a bare Z and nine fractional digits.
    # Measured, 3.11 accepts every captured form; that was true of <=3.10.
    # The reason to use the repo's parser is the NAIVE case, where
    # fromisoformat returns a naive datetime and `.timestamp()` silently
    # reads it in the host's local zone: `bettor_market_stream._parse_ts` strips the Z, truncates the
    # fraction to six digits and refuses a naive stamp. That is the
    # supported parser for this venue's clock and it is reused here rather
    # than reimplemented -- the second implementation is how the first one
    # came to be `float()`.
    #
    # THE RAW VALUE TRAVELS WITH THE VERDICT. Its type and its repr are
    # carried out so a future mismatch names itself instead of collapsing
    # into "unparseable" again.
    # ── WHAT OUR OWN CLOCKS DO AND DO NOT ESTABLISH ──────────────────
    #
    # THE CLAIM THAT WAS WRONG, AND IT WAS MINE. An earlier version of this
    # read treated the request-to-response round trip as an upper bound on
    # the age of the book: "the venue answered after we asked, so what we
    # received cannot be older than the round trip". That is FALSE. The
    # round trip measures TRANSPORT LATENCY. A server can answer in 20 ms
    # with a snapshot it cached minutes ago, or with a book it never
    # revalidated against its own matching engine -- and a latency-derived
    # bound would then CERTIFY that stale snapshot as fresh. The faster the
    # answer, the stronger the false certificate. That is precisely the
    # substitution of an assumption for evidence that the freshness gate
    # exists to refuse, and it was written into the gate itself.
    #
    # WHAT IS STILL TRUE AND STILL RECORDED. Three instants, each labelled
    # for what it is: when WE sent the request, when WE received the
    # response, and when the decision was taken. They bound OUR contribution
    # to the delay, they are what a latency investigation needs, and not one
    # of them says anything about how old the venue's book was.
    #
    # SO ADMISSION IS A STATED POLICY, NOT A DERIVED BOUND. The upstream age
    # is established only by the VENUE's own clock. When `transactTime` is
    # absent or unparseable the age is UNMEASURED and the candidate is
    # refused -- a policy with a reason attached, not a claim that the book
    # was stale. What would change it: the venue publishing what the field
    # denotes, or an endpoint returning a revalidation instant.
    round_trip_s = max(0.0, float(read_at) - float(request_sent_at))
    venue_ts = snap.get("TRANSACT_TIME")
    age, age_basis, vt = None, "VENUE_CLOCK_NOT_PROVIDED", None
    clock = {"field_path": "marketData.transactTime",
             # OUR OWN OBSERVATIONS, labelled as ours and as latency.
             "our_request_sent_at": request_sent_at,
             "our_response_received_at": read_at,
             "our_transport_latency_s": round(round_trip_s, 6),
             "our_transport_latency_is_not_an_upstream_age": (
                 "a server can answer quickly with a cached or unrevalidated "
                 "snapshot, so this number cannot bound how old the book "
                 "was. It bounds OUR contribution to the delay and nothing "
                 "else"),
             "upstream_age_established_only_by": (
                 "the venue's own clock. Absent or unparseable, the upstream "
                 "age is UNMEASURED and the candidate is refused by policy"),
             "raw": (None if venue_ts is None else str(venue_ts)[:64]),
             "raw_type": type(venue_ts).__name__,
             "parser": "bettor_market_stream._parse_ts",
             "parser_accepts": ("ISO_8601_UTC_WITH_A_BARE_Z_AND_UP_TO_"
                                "NANOSECOND_FRACTIONAL_SECONDS")}
    if venue_ts not in (None, bs.NOT_IDENTIFIED):
        dt = _stream_parse_ts(venue_ts)
        if dt is None:
            age, age_basis, vt = None, "VENUE_CLOCK_UNPARSEABLE", None
            clock["why"] = ("the venue sent a value the supported parser "
                            "refuses. It is NOT absent, and it is NOT an "
                            "observed stale age: the age is UNMEASURED")
        else:
            vt = dt.timestamp()
            age = float(verdict_at) - vt
            age_basis = "VENUE_TRANSACT_TIME"
    else:
        clock["why"] = ("the venue supplied no transactTime, so our read "
                        "clock is the only one -- and using it would make "
                        "every book fresh by construction")
    # NO FALLBACK. When the venue's clock gives nothing the age stays None
    # and the basis stays VENUE_CLOCK_NOT_PROVIDED / _UNPARSEABLE, which is
    # UNMEASURED and refuses. Substituting our latency here is the defect
    # described above.
    clock["venue_clock_contributed"] = age is not None
    clock["parsed_epoch_s"] = vt
    clock["age_at_read_s"] = (None if age is None else round(age, 3))
    clock["basis"] = age_basis
    # ── ADMISSION: TWO SEPARATE REQUIREMENTS, NEITHER SUFFICIENT ALONE ──
    #
    # (1) THE BOOK'S CURRENCY MUST BE ESTABLISHED BY A MECHANISM WITH A
    #     PUBLISHED CONTRACT. `bettor_venue_currency` decides, from the
    #     response's own headers, from a live subscription's liveness, or from a
    #     conditional revalidation. Absent all three the verdict is
    #     NOT_ESTABLISHED and this refuses -- which is not a claim the book is
    #     stale, and is recorded as such.
    #
    # (2) OUR OWN PROCESSING DELAY MUST BE INSIDE ITS BOUND. A real interval
    #     between two of our own instants, and a real check.
    #
    # WHY BOTH, AND WHY NEITHER ALONE. A previous version of this block made (2)
    # the whole of admission, on the strength of a probe that was read as
    # establishing that `transactTime` is a last-change stamp. It does not
    # establish that -- a cache serving both reads the same bytes predicts the
    # same observation -- and with (2) alone a snapshot generated four minutes
    # ago, returned in 20 ms and decided on one second later passes the gate.
    # That is the exact substitution this gate exists to refuse, so (1) is
    # restored as its own requirement and (2) is kept as what it actually is.
    clock["stamp_semantics"] = VENUE_STAMP_SEMANTICS
    clock["stamp_semantics_means"] = VENUE_STAMP_SEMANTICS_MEANS
    clock["stamp_observation"] = STAMP_OBSERVATION
    clock["venue_stamp_age_s"] = (None if age is None else round(age, 3))
    clock["venue_stamp_age_decides_nothing"] = (
        "reported as provenance. An old value is not proof the book is stale "
        "and a recent one is not proof it is current, because what the field "
        "denotes is unresolved")
    currency = vc.evaluate(now=verdict_at,
                           observation=book.get("http_observation"),
                           subscription=subscription,
                           revalidation=revalidation,
                           venue_ts=vt, our_receipt_at=read_at,
                           bound_s=MAX_VENUE_QUOTE_AGE_S)
    clock["book_currency"] = currency
    clock["http_observer"] = book.get("http_observer")
    # BOTH INSTANTS ON THE RECORD, so a reader can see which one judged it.
    clock["caller_instant_epoch_s"] = (None if now is None else float(now))
    clock["verdict_instant_epoch_s"] = float(verdict_at)
    clock["verdict_instant_basis"] = (
        "MAX_OF_THE_CALLERS_INSTANT_AND_OUR_POST_READ_RECEIPT: a book is never "
        "judged as of a moment before we held it")
    our_delay = float(verdict_at) - float(read_at)
    clock["our_processing_delay_s"] = round(our_delay, 3)
    clock["our_processing_delay_limit_s"] = MAX_OUR_PROCESSING_DELAY_S
    clock["our_processing_delay_is_not_freshness"] = \
        PROCESSING_DELAY_IS_NOT_FRESHNESS
    # WHAT THE BOOK DISPLAYED, for the refusals below only. Carried so a
    # calibration-only record can state the venue price beside the
    # probability -- labelled, and under a key no order path reads.
    refused_read = {
        "displayed_not_for_orders": _displayed_not_for_orders(
            book, intent=intent, slug=slug, read_at=read_at,
            currency=currency, venue_ts=vt, venue_clock_basis=age_basis),
        "venue_ts": vt, "read_at": read_at, "slug": slug, "intent": intent,
        "http_observation": book.get("http_observation")}
    # THE CONTRADICTED CASE FIRST: it is the one backed by evidence.
    if currency["verdict"] == vc.CONTRADICTED:
        return {"ok": False, "refusal": R_BOOK_CURRENCY_CONTRADICTED,
                "age_s": currency.get("origin_generation_age_s"),
                "limit_s": MAX_VENUE_QUOTE_AGE_S,
                "age_basis": currency["mechanism"],
                "why": currency["why"],
                "book_currency": currency, "venue_clock": clock,
                **refused_read}
    if currency["verdict"] != vc.ESTABLISHED:
        # THE SAME REFUSAL, BY THE SAME NAME -- plus WHICH PART OF M1 WAS
        # MISSING for this market: the subscription's readiness and its named
        # reason (M1_SUBSCRIPTION_REFUSED_BY_VENUE, M1_SNAPSHOT_PENDING, ...,
        # M1_FEED_TIMING_NOT_DOCUMENTED_P5). Diagnostic only; nothing reads it
        # to admit, and it never raises.
        from .. import bettor_market_subscription as _msub
        return {"ok": False, "refusal": R_BOOK_CURRENCY_NOT_ESTABLISHED,
                "m1_subscription": _msub.m1_refusal_detail(
                    slug, now=verdict_at),
                "age_s": None,
                "limit_s": MAX_VENUE_QUOTE_AGE_S,
                "age_basis": vc.NO_MECHANISM,
                "unmeasured": True,
                "this_is_not_a_stale_book": (
                    "no mechanism established that this book is current. That "
                    "is missing evidence, not an observation that it is old"),
                "mechanisms_unavailable": currency["mechanisms_unavailable"],
                "partial": currency.get("partial"),
                "why": currency["why"],
                "book_currency": currency, "venue_clock": clock,
                **refused_read}
    if our_delay > MAX_OUR_PROCESSING_DELAY_S:
        return {"ok": False, "refusal": R_OUR_PROCESSING_DELAY,
                "age_s": round(our_delay, 3),
                "limit_s": MAX_OUR_PROCESSING_DELAY_S,
                "age_basis": "OUR_OWN_RECEIPT_INSTANT",
                "why": ("this book was received %.1f s before the decision "
                        "instant, past the %.0f s this lane allows a read to "
                        "sit. Both instants are ours and the interval is real. "
                        "Its currency WAS established (%s); this refusal is "
                        "about our own delay and nothing upstream"
                        % (our_delay, MAX_OUR_PROCESSING_DELAY_S,
                           currency["mechanism"])),
                "book_currency": currency, "venue_clock": clock,
                **refused_read}

    # THE SIDE THAT ACTUALLY PAYS ON OUR OUTCOME, in cost space.
    lad = bs.acquisition_ladder(book.get("marketData"), intent=intent)
    if not lad.get("ok"):
        return {"ok": False,
                "refusal": lad.get("refusal") or R_NO_DEPTH,
                "why": ("the ladder this intent must consume is not "
                        "readable: %s" % (lad.get("book_was")
                                          or lad.get("parse_status"))),
                "acquisition": lad, "slug": slug}
    # ── ONLY DEPTH AN ORDER WE CAN SEND IS ABLE TO REACH ─────────────
    #
    # BEFORE anything values, sizes or ranks this ladder: a level whose wire
    # price is off the market's executable grid (its own tick AND the
    # adapter's whole cent) is excluded here, by name, with its quantity --
    # the entry's budget walk, the hedge candidate's price and depth and the
    # plan built from either all read the ladder returned below. An unread
    # tick refuses the whole ladder; it is never assumed to be a cent.
    grid = await asyncio.to_thread(market_grid_blocking, slug)
    lad = bs.restrict_to_executable(lad, grid)
    if not lad.get("ok"):
        return {"ok": False, "refusal": lad.get("refusal"),
                "why": lad.get("why"),
                "executable_grid": grid,
                "levels_excluded_unrepresentable": lad.get(
                    "levels_excluded_unrepresentable"),
                "excluded_unrepresentable_qty": lad.get(
                    "excluded_unrepresentable_qty"),
                "acquisition": lad, "slug": slug, "intent": intent,
                "book_currency": currency, "read_at": read_at}
    sized = (bs.fill_across_levels(lad, float(size))
             if size else None)

    return {"ok": True,
            # `ask` is kept for every existing reader and is now
            # explicitly the YES-denominated API price of the best level
            # ON THE SIDE THIS INTENT CONSUMES.
            "ask": lad["best_api_price"],
            "acquisition_price": lad["best_acquisition_price"],
            "api_price": lad["best_api_price"],
            "price_spaces": bs.PRICE_SPACES,
            "intent": intent,
            "side_consumed": lad["side_consumed"],
            "pays_on": lad["pays_on"],
            "depth": lad["displayed_depth"],
            "levels_published": lad.get("levels_published"),
            "levels_read": lad.get("levels_read"),
            "sized": sized,
            # THE WHOLE LADDER, so a caller can walk it rather than
            # guess from the best level. The entry lane's marketable
            # execution estimate needs every level's price and quantity;
            # returning only `depth` forced the loop to treat the book as
            # one price with a number beside it.
            "acquisition_ladder": lad,
            # WHAT WAS LEFT OUT, AND WHY: the executable grid this ladder was
            # restricted to and every displayed level it excluded, priced.
            "executable_grid": lad.get("executable_grid"),
            "levels_excluded_unrepresentable": lad.get(
                "levels_excluded_unrepresentable"),
            "excluded_unrepresentable_qty": lad.get(
                "excluded_unrepresentable_qty"),
            "age_s": age, "age_basis": age_basis,
            # THE CURRENCY VERDICT, CARRIED. `_entry_freshness` re-ages it at
            # the decision instant rather than re-deriving it, so one module
            # decides what establishes currency and one number is applied.
            "book_currency": currency,
            "http_observation": book.get("http_observation"),
            # THE CLOCK'S OWN EVIDENCE: field path, raw value, its type,
            # which parser was applied and what that parser accepts.
            "venue_clock": clock,
            # THE VENUE'S OWN INSTANT, CARRIED OUT. `age_s` is the age at
            # READ time, and a decision taken after two more network reads
            # is not that fresh. Returning the timestamp lets the freshness
            # gate re-age the book against the DECISION instant instead of
            # inheriting a number measured earlier.
            "venue_ts": vt,
            "read_at": read_at, "slug": slug,
            "displayed_depth_is_not_a_queue": True,
            # The bid is deliberately reported as None. The comparison
            # crosses the ladder this intent must cross; a resting price
            # would invent a queue position we never held.
            "bid": None}


# ── THE OUTCOME JOIN, WHICH IS WHAT MAKES CALIBRATION POSSIBLE ───────
#
# `external_valuations` records what the source SAID. Nothing was reading
# back what then HAPPENED, so `outcome_known` was false on every row ever
# written and the calibration measurement had an empty input by
# construction. `JOIN_OUTCOME` has existed since migration 103 and had no
# caller.
#
# ── TWO CONVERSIONS, AND THEY ARE NOT THE SAME CONVERSION ───────────
#
# THE DEFECT THIS REPLACES, AND IT WAS MINE. This join mapped the venue's
# settlement price to our outcome through `payout_is_complement`. That
# flag answers a question about the PROBABILITY: is the de-vig's source
# event the complement of the event the contract pays on? On this lane it
# is always FALSE, because `resolve_venue_identity` asks the resolver for
# a specific outcome and the resolver returns the side that BUYS it -- so
# the probability and the payout describe the same event, INCLUDING when
# that side is the venue's SHORT side.
#
# The settlement price is about the venue's OWN LONG (YES) side. A
# fixture whose YES settles at 1, held SHORT, pays 0. With the flag FALSE
# the old code recorded 1. That is not an approximation, it is the exact
# opposite answer, on every short leg.
#
# So the two conversions are now stated apart and applied once each:
#
#   PROBABILITY CONVERSION  payout_is_complement, applied inside
#                           `bettor_external_shadow.evaluate` at decision
#                           time. By the time a row is written, the
#                           `probability` column already describes the
#                           payout event. This join must not touch it.
#   SETTLEMENT CONVERSION   buy_intent / ladder_side, applied HERE. It
#                           says whether the exposure we hold is the
#                           venue's LONG side (pays the settlement price)
#                           or its SHORT side (pays one minus it).
#
# `buy_intent` and `ladder_side` are the VERIFIED venue-side identity:
# both are written by `resolve_venue_identity` from the resolver's own
# answer, and they agree by construction (BUY_SHORT <-> BID). The mapper
# below requires them to agree and refuses rather than guessing when they
# do not, because a disagreement means the identity is not established.
#
# ── FOUR OUTCOMES OF A READ, KEPT APART ─────────────────────────────
#
# A single `None` return once meant all four of these, and the caller
# recorded all four as VOID -- permanently, since a joined row is never
# re-read:
#
#   CONFIRMED_VOID   the venue DECLARED a void or refund in a field this
#                    reader has identified. A price of neither 0 nor 1 is
#                    NOT this: it establishes only that neither side was
#                    paid in full, which is equally consistent with a
#                    partial settlement, a scaled payout and a unit
#                    convention we have misread. That case is
#                    NEITHER_SIDE_PAID_AND_NO_REFUND_IS_ESTABLISHED and
#                    nothing is written for it.
#   NAMED_WINNER     a label, e.g. "Houston Astros". `float()` raises,
#                    which is how a resolved fixture became a void.
#   INFERRED         RESOLVED_DERIVED: closed with prices converged. The
#                    venue reported no winner; this is our inference from
#                    a price and is not settlement evidence.
#   UNPARSEABLE      a value present that is neither.
#
# VOID IS NEVER INFERRED FROM A NONBINARY VALUE. "Stakes returned" is a
# claim about what the venue DID, and a number that is not 0 or 1 is
# consistent with a void, with a partially-settled market, with a scaled
# payout, with a different unit convention and with a payload we have
# misread. A void therefore requires the venue to DECLARE one, in a field
# `_declared_void` has identified; as of 2026-09-25 the PMUS settlement
# payload carries no such field, so in practice every nonbinary price is
# an unresolved accounting state. Anything that is not a settled 0/1 or a
# declared void is reported by its own name and left unjoined.

# `outcome_basis IS NULL` is the queue condition, and it is the SAME
# condition the calibration scope uses. A row leaves this queue exactly
# when a basis is recorded for it -- a settled 0/1 or a confirmed void --
# and rows reopened by migration 118's audit re-enter it, because the
# audit cleared their basis along with their outcome.
#
# ORDERED BY WHEN THE VENUE WAS LAST ASKED, never-asked first. A fixture
# the venue reports only as a named winner is never written, so ordering
# by `decided_at` alone would let a handful of unresolvable rows occupy
# the whole per-run budget forever and starve every later fixture.
#
# EVERY RECORD PURPOSE, deliberately (migration 144): a CALIBRATION_ONLY row
# is joined exactly like an entry decision -- labelling it with the venue's
# settlement is the reason it is recorded. The join writes outcome columns
# only; it cannot change a row's purpose (the 144 trigger refuses that).
UNJOINED_SQL = """
    SELECT id, us_market_slug, buy_intent, ladder_side,
           payout_is_complement
      FROM external_valuations
     WHERE experiment_id = $1
       AND outcome_known = FALSE
       AND outcome_basis IS NULL
       AND us_market_slug IS NOT NULL
       AND decided_at < now() - interval '2 hours'
     ORDER BY settlement_read_at ASC NULLS FIRST, decided_at ASC
     LIMIT $2
"""

#: Bounded per run: each row costs one paced venue read, and the join
#: shares the venue budget with the collector and the entry loop.
MAX_JOINS_PER_RUN = 60

#: A settlement price this far from 0 or 1 is not a side paying out.
_SETTLED_EPS = 1e-6

#: How a stored outcome was established. NULL in the column means "not
#: established by this mapper", which is what keeps a row out of scope.
B_SETTLEMENT_PRICE = "VENUE_SETTLEMENT_PRICE"
B_REPORTED_OUTCOME = "VENUE_REPORTED_OUTCOME"
B_CONFIRMED_VOID = "CONFIRMED_VOID"

#: Classes that are NOT an outcome, each its own answer.
C_NAMED_WINNER = "VENUE_NAMED_A_WINNER_NOT_A_PRICE"
C_INFERRED = "INFERRED_FROM_CONVERGED_PRICES_NOT_VENUE_SETTLEMENT"
C_UNPARSEABLE = "SETTLEMENT_VALUE_UNPARSEABLE"
C_SIDE_UNKNOWN = "VENUE_SIDE_IDENTITY_NOT_ESTABLISHED"
#: A parseable price that paid neither side in full, with NO declared void
#: or refund. Not an outcome, and NOT a refund: see the long note in
#: `outcome_from_settlement`.
C_NEITHER_SIDE_PAID = "NEITHER_SIDE_PAID_AND_NO_REFUND_IS_ESTABLISHED"

#: Fields a venue response would have to carry for a refund to be
#: ESTABLISHED rather than inferred. Named rather than guessed at, so that
#: when the venue does publish one the change is a one-line addition here
#: and not a re-derivation somewhere downstream. As of 2026-09-25 the PMUS
#: settlement payload carries none of them, which is why every nonbinary
#: price is currently an unresolved accounting state.
VOID_FLAG_FIELDS = ("void", "voided", "isVoid", "is_void",
                    "refunded", "isRefunded", "is_refunded",
                    "stakeReturned", "stake_returned", "cancelled",
                    "canceled", "isCancelled")
#: Values of a `status`-like field that DECLARE a void. Matched exactly
#: after casefolding; a substring match would read "not_voided" as a void.
VOID_STATUS_FIELDS = ("settlementStatus", "settlement_status", "status",
                      "resolution", "resolutionStatus")
VOID_STATUS_VALUES = ("void", "voided", "refunded", "cancelled",
                      "canceled", "no_action", "noaction", "push")


def _declared_void(resolution) -> dict:
    """Did the venue DECLARE a void or refund, in a field we identified?

    Returns the evidence, never a bare boolean, because "no void was
    declared" and "we could not tell" must not read the same. A missing
    field is not a false: it is an absent statement, and that is exactly
    why the caller leaves the position unresolved rather than settling it.
    """
    out = {"declared": False, "field": None, "value": None,
           "fields_present": [],
           "basis": "VENUE_MUST_STATE_A_VOID_IT_IS_NEVER_INFERRED"}
    r = resolution or {}
    if not isinstance(r, dict):
        return out
    for f in VOID_FLAG_FIELDS:
        if f in r and r[f] is not None:
            out["fields_present"].append(f)
            if r[f] is True or str(r[f]).strip().casefold() in (
                    "true", "1", "yes"):
                out.update(declared=True, field=f, value=str(r[f]))
                return out
    for f in VOID_STATUS_FIELDS:
        v = r.get(f)
        if v is None:
            continue
        out["fields_present"].append(f)
        if str(v).strip().casefold() in VOID_STATUS_VALUES:
            out.update(declared=True, field=f, value=str(v))
            return out
    return out

#: The two venue sides, and which settlement value each of them pays.
SIDE_LONG = "VENUE_LONG_PAYS_THE_SETTLEMENT_PRICE"
SIDE_SHORT = "VENUE_SHORT_PAYS_ONE_MINUS_THE_SETTLEMENT_PRICE"


def venue_side_of_our_exposure(*, buy_intent, ladder_side):
    """Which side of the venue's market the held exposure IS.

    Returns `SIDE_LONG`, `SIDE_SHORT`, or None when the two independent
    statements of the identity do not agree -- which is a refusal, not a
    tie to be broken. Both fields are written together by
    `resolve_venue_identity` from one resolver answer, so disagreement
    means a row was written by something else or the writer changed.
    """
    intent = str(buy_intent or "")
    side = str(ladder_side or "").upper()
    if intent == "ORDER_INTENT_BUY_LONG" and side in ("ASK", ""):
        return SIDE_LONG
    if intent == "ORDER_INTENT_BUY_SHORT" and side in ("BID", ""):
        return SIDE_SHORT
    return None


def outcome_from_settlement(resolution, *, buy_intent, ladder_side):
    """The held exposure's 0/1 truth, or the exact reason there is none.

    `resolution` is a `bettor_live_read.read_resolution` result. Returns
    a dict -- never a bare value -- because the CALLER has to be able to
    tell a void from a label from an unreadable payload, and a single
    `None` cannot.

        {"outcome": 0|1|None, "basis": str|None, "side_map": str|None,
         "settlement_read": str|None, "class": str}
    """
    out = {"outcome": None, "basis": None, "side_map": None,
           "settlement_read": None, "class": None,
           "status": str((resolution or {}).get("status") or "")}
    side = venue_side_of_our_exposure(buy_intent=buy_intent,
                                     ladder_side=ladder_side)
    out["side_map"] = side
    if side is None:
        out["class"] = C_SIDE_UNKNOWN
        return out

    from .. import bettor_live_read as lr

    st = out["status"]
    # AN INFERENCE IS NOT A SETTLEMENT. RESOLVED_DERIVED means the market
    # closed and its prices converged; the venue named nothing. Scoring a
    # probability against our own inference would make the calibration
    # partly a measurement of our inference.
    if st == getattr(lr, "RESOLVED_DERIVED", "RESOLVED_DERIVED"):
        out["class"] = C_INFERRED
        out["settlement_read"] = (
            None if resolution.get("outcome") is None
            else str(resolution["outcome"]))
        return out
    if st != lr.RESOLVED:
        out["class"] = "NOT_RESOLVED:%s" % (st or "UNKNOWN")
        return out

    # A RESOLVED READ CARRIES EITHER A PRICE OR A NAME. The settlement
    # endpoint gives a price; the listing's reported-outcome field, if it
    # ever appears, gives whatever the venue put there.
    raw = resolution.get("settlement_price")
    if raw is None:
        raw = resolution.get("outcome")
    # THE VENUE'S OWN STRING, UNPARSED, when it gave one. `settlement_price`
    # is our float reading of it; recording the reading instead of the
    # value would lose the unit convention the raw string carries and make
    # a later recheck check our arithmetic rather than the venue's answer.
    shown = resolution.get("settlement_price_raw")
    out["settlement_read"] = (str(shown) if shown is not None
                              else (None if raw is None else str(raw)))
    try:
        sp = float(raw)
    except (TypeError, ValueError):
        # A NAME IS NOT A NUMBER, and it is not a void either. Mapping a
        # label to a side needs the venue's own outcome labels beside the
        # side we hold, which this reader does not carry -- so it is
        # reported and left for a reader that does.
        out["class"] = (C_NAMED_WINNER
                        if isinstance(raw, str) and raw.strip()
                        else C_UNPARSEABLE)
        return out

    if abs(sp - 1.0) <= _SETTLED_EPS:
        yes = 1
    elif abs(sp) <= _SETTLED_EPS:
        yes = 0
    else:
        # ── A NONBINARY PRICE IS NOT A REFUND ────────────────────────
        #
        # THE DEFECT THIS CORRECTS, AND IT WAS MINE, TWICE OVER. The
        # previous version read any parseable value other than 0 or 1 as
        # CONFIRMED_VOID, and the settlement consumer then assumed the
        # original stake came back, called the result authoritative and
        # closed the inventory. A 0.5 response with no void or refund
        # evidence anywhere produced a manufactured cash entitlement.
        #
        # 0.5 is consistent with a void, with a partially settled market,
        # with a unit convention we have misread, with a scaled payout,
        # and with a contract that does not settle in {0, 1} at all. The
        # only thing it establishes is that NEITHER SIDE was paid in
        # full, and "neither side paid in full" is not "stakes returned".
        # Calling the fee treatment conservative did not establish the
        # cash entitlement either; a conservative guess is still a guess.
        #
        # VERIFIED REFUND SEMANTICS ARE REQUIRED. Until the venue states
        # a void or a refund in a field this reader has actually
        # identified, the answer is an explicit UNRESOLVED accounting
        # state: nothing is written, no exposure is released, and the
        # position stays open and stays in the queue.
        got_void = _declared_void(resolution)
        if got_void.get("declared"):
            out["class"] = B_CONFIRMED_VOID
            out["basis"] = B_CONFIRMED_VOID
            out["void_evidence"] = got_void
            return out
        out["class"] = C_NEITHER_SIDE_PAID
        out["void_evidence"] = got_void
        out["why"] = (
            "the venue settled at %s, which paid neither side in full. "
            "That is consistent with a void, with a partial settlement, "
            "with a scaled payout and with a unit convention we have "
            "misread, and it establishes none of them. No refund is "
            "inferred and no exposure is released" % out["settlement_read"])
        return out

    out["outcome"] = (1 - yes) if side == SIDE_SHORT else yes
    out["basis"] = (B_SETTLEMENT_PRICE
                    if resolution.get("settlement_price") is not None
                    else B_REPORTED_OUTCOME)
    out["class"] = out["basis"]
    return out


#: A settled 0/1, with the provenance that makes it scorable.
JOIN_RESOLVED_SQL = """
    UPDATE external_valuations
       SET outcome_known = TRUE, outcome = $2,
           outcome_at = to_timestamp($3),
           outcome_basis = $4, outcome_side_map = $5,
           settlement_read = $6, settlement_read_at = to_timestamp($3)
     WHERE id = $1 AND outcome_known = FALSE
"""

#: A CONFIRMED void. `outcome_known` stays FALSE -- migration 103 is right
#: that a known outcome must be 0 or 1 -- and the BASIS is what takes the
#: row out of the unjoined queue and tells the evaluator to class it VOID.
JOIN_VOID_SQL = """
    UPDATE external_valuations
       SET outcome_basis = $2, outcome_side_map = $3,
           settlement_read = $4, settlement_read_at = to_timestamp($5)
     WHERE id = $1 AND outcome_known = FALSE AND outcome_basis IS NULL
"""

#: A READ THAT ESTABLISHED NOTHING. It records that the venue was asked
#: and what came back, and deliberately leaves `outcome_basis` NULL so the
#: row is neither scorable nor forgotten.
JOIN_ATTEMPT_SQL = """
    UPDATE external_valuations
       SET outcome_side_map = $2, settlement_read = $3,
           settlement_read_at = to_timestamp($4)
     WHERE id = $1 AND outcome_known = FALSE AND outcome_basis IS NULL
"""


async def join_outcomes(conn, *, limit=MAX_JOINS_PER_RUN) -> dict:
    """Read back what happened, for valuations that do not know yet.

    Never raises. Reports every class separately -- resolved, void, still
    pending, unreadable, unmatched, named-winner, inferred -- because a
    calibration that could not say how much of its record it failed to
    resolve would be reporting a sample it had not characterised, and
    because three of those classes were previously all recorded as VOID.

    ONLY TWO CLASSES ARE WRITTEN: a settled 0/1 and a confirmed void.
    Everything else is LEFT UNJOINED on purpose -- a named winner and an
    inferred resolution are readable later by a reader that carries the
    venue's outcome labels, and permanently stamping them now is exactly
    the mistake migration 118 had to reopen rows to undo.
    """
    from .. import bettor_live_read as lr

    out = {"ran": True, "examined": 0, "resolved": 0, "void": 0,
           "pending": 0, "unreadable": 0, "unmatched": 0, "errors": 0,
           "named_winner": 0, "inferred": 0, "side_unknown": 0,
           "unparseable": 0, "neither_side_paid": 0,
           "limit": int(limit), "by_status": {}, "by_class": {}}
    try:
        rows = await conn.fetch(UNJOINED_SQL, ext.EXPERIMENT_ID, int(limit))
    except Exception as exc:                                   # noqa: BLE001
        return {"ran": False, "error": "%s" % type(exc).__name__,
                "why": "the unjoined-valuation read failed"}
    out["examined"] = len(rows)
    for r in rows:
        try:
            res = await asyncio.to_thread(_read_resolution_blocking,
                                          r["us_market_slug"])
        except Exception as exc:                               # noqa: BLE001
            out["errors"] += 1
            out["by_status"]["EXC:" + type(exc).__name__] = \
                out["by_status"].get("EXC:" + type(exc).__name__, 0) + 1
            continue
        st = str(res.get("status") or "")
        out["by_status"][st] = out["by_status"].get(st, 0) + 1

        got = outcome_from_settlement(res, buy_intent=r["buy_intent"],
                                      ladder_side=r["ladder_side"])
        cls = str(got.get("class") or "UNCLASSIFIED")
        out["by_class"][cls] = out["by_class"].get(cls, 0) + 1
        at = time.time()

        if got["outcome"] is not None:
            try:
                await conn.execute(JOIN_RESOLVED_SQL, r["id"],
                                   int(got["outcome"]), at, got["basis"],
                                   got["side_map"], got["settlement_read"])
                out["resolved"] += 1
            except Exception:                                  # noqa: BLE001
                out["errors"] += 1
            continue
        if cls == B_CONFIRMED_VOID:
            try:
                await conn.execute(JOIN_VOID_SQL, r["id"], B_CONFIRMED_VOID,
                                   got["side_map"], got["settlement_read"],
                                   at)
                out["void"] += 1
            except Exception:                                  # noqa: BLE001
                out["errors"] += 1
            continue

        # NO BASIS IS WRITTEN FOR THE REST -- so they stay in scope for a
        # later read and out of scope for calibration -- but the ATTEMPT is
        # stamped, which is what stops a handful of unresolvable fixtures
        # from consuming the whole per-run budget every run.
        try:
            await conn.execute(JOIN_ATTEMPT_SQL, r["id"], got["side_map"],
                               got["settlement_read"], at)
        except Exception:                                      # noqa: BLE001
            out["errors"] += 1
        if cls == C_NEITHER_SIDE_PAID:
            out["neither_side_paid"] += 1
        elif cls == C_NAMED_WINNER:
            out["named_winner"] += 1
        elif cls == C_INFERRED:
            out["inferred"] += 1
        elif cls == C_SIDE_UNKNOWN:
            out["side_unknown"] += 1
        elif cls == C_UNPARSEABLE:
            out["unparseable"] += 1
        elif st == lr.PENDING:
            out["pending"] += 1
        elif st == lr.UNMATCHED:
            out["unmatched"] += 1
        else:
            out["unreadable"] += 1
    return out


# ── THE ENTRY LANE'S EXECUTION, SIZING AND RISK ─────────────────────
#
# Everything below composes existing engines and computes no economics of
# its own. The arithmetic lives in `bettor_entry_execution`; these are the
# adapters that fetch what it needs from this loop's own data.

FIXTURE_META_SQL = """
    SELECT phase, phase_uncovered, game_format, scheduled_innings,
           play_has_begun, event_state_raw, abstract_state, start_evidence,
           terminal_hint, game_pk, home_team, away_team, source, source_url,
           reader_version,
           official_date::text AS official_date,
           extract(epoch FROM actual_start_at)::float8 AS actual_start_at,
           to_char(retrieved_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
               AS retrieved_at
      FROM fixture_metadata WHERE condition_id = $1
"""


async def fixture_metadata_for(conn, condition_id) -> dict:
    """The persisted authoritative fixture row, or why there is none.

    THROUGH THE SHARED STORE, not a second copy of the query. The manager
    and the admin acquisition route read and write the same row through
    `bettor_fixture_store`; a private SELECT here is how the two lanes
    would drift apart.

    A FAILED READ IS NOT AN ABSENT ROW, and the store reports them
    separately: an absent row is an acquisition task, a failed read is a
    database fault.
    """
    return await fstore.read(conn, condition_id)


#: One HTTP GET per official date per cycle, at most this many dates. The
#: league's schedule endpoint is asked for a DATE, and a cycle's candidates
#: cluster on one or two of them.
FIXTURE_MAX_DATES_PER_CYCLE = 3
FIXTURE_FETCH_TIMEOUT_S = 15.0


def _official_date_candidates(commence_iso) -> list:
    """The official dates a fixture at this instant could belong to.

    A 01:40Z first pitch is a US evening game on the PREVIOUS calendar
    date, and `officialDate` is what the league calls it. So the UTC date
    and the day before are both tried, in that order, and a match is
    required to be unique within the date that produced it -- never
    stitched across the two.
    """
    at = fmeta_mod._epoch(commence_iso)
    if at is None:
        return []
    import datetime as _dt

    d = _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc).date()
    return [d.isoformat(), (d - _dt.timedelta(days=1)).isoformat()]


def _fetch_schedule_blocking(date_str):
    """The league's own schedule for one date. Blocking; called in a thread.

    NO CREDENTIAL, no write, one GET. The failure is returned, not raised,
    because an unreachable league host must leave the scope UNESTABLISHED
    rather than end a cycle.
    """
    import json as _json
    import urllib.request as _ur

    url = fmeta_mod.SOURCE_URL % date_str
    try:
        req = _ur.Request(url, headers={"Accept": "application/json"})
        with _ur.urlopen(req,                               # noqa: S310
                         timeout=FIXTURE_FETCH_TIMEOUT_S) as r:
            return {"ok": True, "url": url,
                    "payload": _json.loads(r.read().decode("utf-8"))}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "url": url,
                "error": "%s: %s" % (type(exc).__name__, exc)}


R_FIXTURE_KEY_ABSENT = "FIXTURE_METADATA_HAS_NO_CONDITION_KEY"


async def acquire_venue_fixture_scope(conn, *, event_slug, sport_key, home,
                                      away, commence_iso, now, cache,
                                      fetcher=None) -> dict:
    """THE SCOPE EVIDENCE FOR A VENUE-NATIVE SOCCER CONTRACT.

    Keyed by the venue's OWN namespaced event identity (migration 183),
    never by a global condition id. Acquired from the competition
    organiser's published schedule when `bettor_soccer_fixture.SOURCES`
    declares one; refused by name otherwise. The returned dict has the same
    reading keys as `fstore.read` so the settlement path consumes it
    unchanged; every failure is a named refusal and NO row.
    """
    from .. import bettor_soccer_fixture as SF
    key = SF.venue_fixture_key(event_slug)
    base = {"read": False, "venue_fixture_key": key, "key_kind":
            "VENUE_NATIVE_EVENT", "error": None}
    if key is None:
        acq = {"attempted": False, "refusal": SF.R_NO_EVENT_KEY,
               "why": ("the venue catalogue gave no event slug for this "
                       "contract, so there is no venue-native key to bind "
                       "fixture evidence to")}
        return dict(base, refusal=SF.R_NO_EVENT_KEY, acquisition=acq)
    src = SF.source_for(sport_key)
    if src is None:
        r = "%s:%s" % (SF.R_NO_SOURCE, sport_key)
        acq = {"attempted": False, "refusal": r,
               "why": ("no organiser-published schedule source is declared "
                       "for %r, so the competition phase, the match format "
                       "and the event state cannot be established from an "
                       "authoritative source" % (sport_key,))}
        return dict(base, refusal=r, acquisition=acq)
    row = await SF.read(conn, key)
    need = fstore.needs_acquisition(row, now=now)
    acq = {"attempted": False, "decided": need, "source": src["source"]}
    if not need.get("acquire"):
        return dict(row, venue_fixture_key=key, key_kind="VENUE_NATIVE_EVENT",
                    acquisition=acq)
    if not (str(home or "").strip() and str(away or "").strip()):
        acq.update(refusal=SF.R_BINDING,
                   why="the odds provider gave no pair of team names")
        return dict(row, venue_fixture_key=key, acquisition=acq)
    at = fmeta_mod._epoch(commence_iso)
    if at is None:
        acq.update(refusal="COMMENCE_TIME_NOT_READABLE",
                   why="the event's commence time could not be read")
        return dict(row, venue_fixture_key=key, acquisition=acq)
    import datetime as _dt
    d = _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc).date()
    # The organiser files a match under its LOCAL date: the UTC date first,
    # then the dates either side, each required to give a unique match.
    dates = [d.isoformat(), (d + _dt.timedelta(days=1)).isoformat(),
             (d - _dt.timedelta(days=1)).isoformat()]
    fetch = fetcher or SF.fetch_blocking
    acq.update(attempted=True, dates_tried=[])
    for date_str in dates:
        ck = "%s:%s:%s" % (src["kind"], src["competition_id"], date_str)
        if ck not in cache:
            if len([k for k in cache if cache[k] is not None]) >= \
                    FIXTURE_MAX_DATES_PER_CYCLE:
                acq.update(refusal="FIXTURE_FETCH_BUDGET_SPENT",
                           why="this cycle's schedule-read bound is spent")
                return dict(row, venue_fixture_key=key, acquisition=acq)
            got = await asyncio.to_thread(fetch, src, date_str)
            cache[ck] = got if got.get("ok") else None
            if not got.get("ok"):
                acq.setdefault("fetch_errors", []).append(
                    {"date": date_str, "error": got.get("error"),
                     "url": got.get("url")})
        got = cache.get(ck)
        if got is None:
            continue
        ev = SF.parse(got["payload"], home=home, away=away,
                      date_str=date_str, retrieved_at=got["retrieved_at"],
                      url=got["url"], src=src)
        acq["dates_tried"].append({"date": date_str, "matched": ev["ok"],
                                   "refusals": ev.get("refusals")})
        if not ev["ok"]:
            if SF.R_AMBIGUOUS in (ev.get("refusals") or []):
                acq.update(refusal=SF.R_AMBIGUOUS, why=ev.get("why"))
                return dict(row, venue_fixture_key=key, acquisition=acq)
            continue
        acq["write"] = await SF.upsert(conn, key, ev)
        acq["evidence_refusals"] = list(ev.get("refusals") or [])
        fresh = await SF.read(conn, key)
        return dict(fresh, venue_fixture_key=key,
                    key_kind="VENUE_NATIVE_EVENT", acquisition=acq)
    acq["refusal"] = acq.get("refusal") or (
        SF.R_FETCH if acq.get("fetch_errors") and not any(
            t.get("matched") is False for t in acq["dates_tried"])
        else SF.R_NO_MATCH)
    acq["why"] = acq.get("why") or (
        "the organiser schedule gave no unique match for %r vs %r on %s"
        % (home, away, dates))
    return dict(row, venue_fixture_key=key, acquisition=acq)


async def acquire_fixture_scope(conn, *, condition_id, home, away,
                                commence_iso, now, cache, fetcher=None,
                                sport_family=None, sport_key=None,
                                venue_event_slug=None, venue_fetcher=None):
    """The scope evidence for ONE candidate's fixture, acquired if needed.

    ── THE DEFECT THIS CLOSES ───────────────────────────────────────

    `fixture_metadata` was written by an admin route invoked by hand for
    one acceptance position. Ordinary candidates had no row, so the quote
    context stayed unproven, `book_terms()` withheld the bookmaker side and
    every settlement verdict read UNKNOWN. Measured 2026-09-25: 15 of 15
    candidate rows carried `ctx - phase - fmt -` with `rules_read true`.

    ── WHAT IT DOES ─────────────────────────────────────────────────

    Reads the row; if absent, stale past `bettor_fixture_store`'s bound, or
    carrying no reported event state, fetches the league's schedule for the
    fixture's own official date, matches the game INDEPENDENTLY on the two
    team names the ODDS PROVIDER gave for this event plus that date, and
    persists the parsed evidence with its source, URL, binding and
    retrieval time. Then it RE-READS the row, so what the lane uses is what
    is persisted rather than what was computed in memory.

    ── WHAT IT WILL NOT DO ──────────────────────────────────────────

    It never infers a context from a scheduled start: the context comes
    from `fmeta_mod.context_for`, which refuses unless the league reported
    a state. It never selects a fixture without both team names and a date.
    It never widens the scope guard. Every failure is a named refusal on
    the returned row, and the caller's gate reads that refusal.

    ── NO KEY, NO READ AND NO WRITE ────────────────────────────────
    The row is keyed by the GLOBAL condition id. A candidate whose identity
    came from the venue's own catalogue has none, and `str(None)` is the
    string "None": the old path would have read -- and on a match WRITTEN --
    a row under that shared key, so a second venue-native candidate would
    have been handed the first one's game state. Refused by name instead;
    the scope stays unestablished and the settlement comparison says so.
    """
    if not str(condition_id or "").strip() and \
            str(sport_family or "") == "soccer":
        # A VENUE-NATIVE SOCCER CONTRACT: its own namespaced key and the
        # organiser's schedule, never a borrowed or invented condition id.
        return await acquire_venue_fixture_scope(
            conn, event_slug=venue_event_slug, sport_key=sport_key,
            home=home, away=away, commence_iso=commence_iso, now=now,
            cache=cache, fetcher=venue_fetcher)
    if not str(condition_id or "").strip():
        acq = {"attempted": False, "refusal": R_FIXTURE_KEY_ABSENT,
               "why": ("no global condition id to key the fixture row by "
                       "(a venue-native identity), so nothing is read or "
                       "written and the scope stays unestablished")}
        return {"read": False, "error": None,
                "refusal": R_FIXTURE_KEY_ABSENT, "why": acq["why"],
                "acquisition": acq}
    row = await fstore.read(conn, condition_id)
    need = fstore.needs_acquisition(row, now=now)
    acq = {"attempted": False, "decided": need}
    if not need.get("acquire"):
        return dict(row, acquisition=acq)
    if not (str(home or "").strip() and str(away or "").strip()):
        acq["refusal"] = "FIXTURE_BINDING_INCOMPLETE"
        acq["why"] = ("the odds provider gave no pair of team names for "
                      "this event, and a fixture is matched on the two "
                      "names and the official date")
        return dict(row, acquisition=acq)
    dates = _official_date_candidates(commence_iso)
    if not dates:
        acq["refusal"] = "COMMENCE_TIME_NOT_READABLE"
        acq["why"] = ("the event's commence time could not be read, so no "
                      "official date could be derived to ask for")
        return dict(row, acquisition=acq)

    acq["attempted"] = True
    acq["dates_tried"] = []
    fetch = fetcher or _fetch_schedule_blocking
    for date_str in dates:
        if date_str not in cache:
            if len([k for k in cache if cache[k] is not None]) >= \
                    FIXTURE_MAX_DATES_PER_CYCLE:
                acq["refusal"] = "FIXTURE_FETCH_BUDGET_SPENT"
                acq["why"] = ("this cycle has already fetched %d schedule "
                              "dates, which is the bound"
                              % FIXTURE_MAX_DATES_PER_CYCLE)
                return dict(row, acquisition=acq)
            got = await asyncio.to_thread(fetch, date_str)
            cache[date_str] = got if got.get("ok") else None
            if not got.get("ok"):
                acq.setdefault("fetch_errors", []).append(
                    {"date": date_str, "error": got.get("error"),
                     "url": got.get("url")})
        got = cache.get(date_str)
        if got is None:
            continue
        parsed = fmeta_mod.parse_games(got["payload"])
        if not parsed.get("ok"):
            acq.setdefault("parse_refusals", []).append(
                {"date": date_str, "refusal": parsed.get("refusal")})
            continue
        m = fmeta_mod.match_fixture(parsed["games"], home=home, away=away,
                                   official_date=date_str)
        acq["dates_tried"].append({"date": date_str,
                                   "games": parsed.get("total"),
                                   "matched": bool(m.get("ok")),
                                   "refusal": m.get("refusal")})
        if not m.get("ok"):
            continue
        # THE RETRIEVAL TIME IS OURS, STAMPED AT THE READ, because the row
        # is evidence and an undated observation is not evidence.
        import datetime as _dt

        at = _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        ev = fmeta_mod.evidence_from(m["game"], retrieved_at=at,
                                     source_url=got["url"],
                                     condition_id=str(condition_id))
        wrote = await fstore.upsert(conn, ev, raw_game=m["game"])
        acq["write"] = wrote
        acq["evidence_refusals"] = list(ev.get("refusals") or [])
        fresh = await fstore.read(conn, condition_id)
        return dict(fresh, acquisition=acq)
    acq["refusal"] = acq.get("refusal") or fmeta_mod.R_NO_MATCH
    acq["why"] = acq.get("why") or (
        "no schedule date returned exactly one game for %r vs %r, so the "
        "fixture is not bound and the scope stays unestablished"
        % (away, home))
    return dict(row, acquisition=acq)


def _risk_action(intent) -> str:
    """The EV action vocabulary's name for acquiring this leg.

    Not the ladder side: `BID`/`ASK` names a side of someone else's book,
    and the risk engine asks what WE are doing. Both are marketable --
    this lane crosses -- so both are TAKE_*, never MAKE_*.
    """
    return ("TAKE_NO" if str(intent) == "ORDER_INTENT_BUY_SHORT"
            else "TAKE_YES")


def _quote_epoch(quote):
    """The provider's `last_update` as epoch seconds, or None.

    THE PROVIDER STATES ISO-8601. `quote["observed_at"]` is that string;
    the de-vig parses it internally, so callers that read the field raw
    got a string. `float()` on it made the freshness gate report "one of
    the two clocks is not measured" for EVERY candidate -- a wrong refusal
    that looked like a venue problem -- and made the quote-context rule
    raise. Parsed once, here, through the odds source's own reader.
    """
    at = (quote or {}).get("observed_at")
    if at is None:
        return None
    if isinstance(at, (int, float)):
        return float(at)
    try:
        return float(devig._epoch(at))
    except (TypeError, ValueError):
        return None


TOKENS_SQL = """
    SELECT token_id, outcome, outcome_index
      FROM market_tokens WHERE condition_id = $1
     ORDER BY outcome_index
"""

R_OUTCOME_NOT_BOUND = "PAYOUT_OUTCOME_INDEX_NOT_BOUND_TO_A_TOKEN"
R_OUTCOME_DISAGREES = "PAYOUT_OUTCOME_DISAGREES_WITH_THE_VENUE_INTENT"


async def bind_payout_outcome(conn, *, condition_id, payout_event, intent,
                              record_purpose=None):
    """Which GLOBAL token and index the held payout event actually is.

    A CALIBRATION-ONLY RECORD IS REFUSED FIRST, by name and before any read:
    binding an outcome index is the first step of writing inventory, and such
    a record may never have any. `record_purpose` is the valuation's own
    purpose; None is the pre-144 caller, whose records are entry decisions.

    ── WHY THIS IS NOT DERIVED FROM THE INTENT ──────────────────────
    It was: `1 if intent == BUY_SHORT else 0`. That treats the US venue's
    order intent as though it determined the GLOBAL catalogue's outcome
    ordering, and those are two different systems. `market_tokens` lists
    the outcomes with their indices as the global market defines them,
    and nothing guarantees index 0 is the side a BUY_LONG acquires --
    the ordering is the catalogue's, not the venue's.

    Under the old rule a market whose token order happened to be reversed
    would file the position under the opposite leg: the one-position index
    would collide with the other side, the settlement payout would be read
    off the wrong outcome, and every number downstream would be about a
    contract we do not hold.

    So the binding is READ, by matching the payout event's name against
    the token outcomes, and then CROSS-CHECKED against the intent: the two
    are expected to agree, and a disagreement is reported rather than
    resolved in favour of either. An unbindable outcome refuses the entry
    -- there is no default.

    A VENUE-NATIVE IDENTITY HAS NO GLOBAL CONDITION TO BIND AGAINST, and
    querying `str(None)` would ask the catalogue about the literal "None".
    It refuses by the same name, saying why, before any read.
    """
    from .. import bettor_valuation_purpose as _vp

    refused = _vp.refuse_unless_entry(
        purpose=(_vp.ENTRY_DECISION if record_purpose is None
                 else record_purpose))
    if refused is not None:
        return refused
    if not str(condition_id or "").strip():
        return {"ok": False, "refusal": R_OUTCOME_NOT_BOUND, "tokens": [],
                "basis": "NO_GLOBAL_CONDITION_TO_BIND_AGAINST",
                "why": ("this contract's identity came from the venue's own "
                        "catalogue and the global catalogue holds no "
                        "condition for it, so the payout outcome cannot be "
                        "bound to a global token index. It is NOT assumed")}
    try:
        rows = await conn.fetch(TOKENS_SQL, str(condition_id))
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": R_OUTCOME_NOT_BOUND,
                "error": type(exc).__name__,
                "why": ("the token read failed, so which outcome this "
                        "contract pays on is unknown")}
    toks = [dict(r) for r in rows]
    if not toks:
        return {"ok": False, "refusal": R_OUTCOME_NOT_BOUND, "tokens": [],
                "why": ("the global catalogue lists no tokens for this "
                        "condition, so the payout outcome cannot be bound "
                        "to an index. It is NOT assumed to be 0")}

    want = _norm_outcome(payout_event)
    hits = [t for t in toks if _norm_outcome(t["outcome"]) == want]
    if len(hits) != 1:
        return {"ok": False, "refusal": R_OUTCOME_NOT_BOUND,
                "tokens": [t["outcome"] for t in toks],
                "payout_event": payout_event,
                "matches": len(hits),
                "why": ("the payout event matches %d of %d listed outcomes. "
                        "One and only one is a binding" % (len(hits),
                                                           len(toks)))}
    tok = hits[0]
    idx = int(tok["outcome_index"])
    # THE CROSS-CHECK. The intent implies a side under the venue's own
    # two-leg convention; if that disagrees with the catalogue's index the
    # two systems are not describing the same leg and the entry stops.
    implied = 1 if str(intent) == "ORDER_INTENT_BUY_SHORT" else 0
    out = {"ok": True, "outcome_index": idx, "token_id": tok["token_id"],
           "outcome": tok["outcome"], "outcomes_listed": len(toks),
           "intent": intent, "intent_implied_index": implied,
           "basis": "MATCHED_THE_PAYOUT_EVENT_AGAINST_market_tokens",
           "cross_check": ("AGREES" if idx == implied else "DISAGREES")}
    if idx != implied and len(toks) == 2:
        # ON A BINARY MARKET the two conventions should coincide. When they
        # do not, one of them is wrong about which leg is held, and picking
        # either would be a guess about where the money is.
        out.update(ok=False, refusal=R_OUTCOME_DISAGREES,
                   why=("the catalogue puts %r at index %d while the venue "
                        "intent %s implies index %d. On a two-outcome "
                        "market these must agree; they do not, so which "
                        "leg is held is unestablished"
                        % (tok["outcome"], idx, intent, implied)))
    return out


def _norm_outcome(name) -> str:
    """Compare outcome names without punctuation or case.

    Deliberately NOT the team-name normaliser: that one strips words like
    "city" and "united" as noise, which is right for matching a fixture
    and wrong here, where "Manchester City" and "Manchester United" are
    two outcomes of two different markets and must never collapse.
    """
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


#: The odds source's own hard rule, restated where the gate can see it.
#: `bettor_pinnacle_devig` refuses a quote older than this; the entry
#: lane's freshness gate must not be laxer than the valuation's.
PINNACLE_MAX_AGE_S = 30.0


def _entry_freshness(quote, vq, now) -> dict:
    """Both clocks, and the STALEST one governs.

    An edge computed from a 3-second Pinnacle price and a five-minute-old
    venue ask is an artefact of the gap between them. Either age being
    unmeasurable leaves the verdict UNKNOWN, which blocks -- in
    particular when the venue supplies no `transactTime`, because then
    our read time is the only clock and using it would make every quote
    look fresh by construction.
    """
    p_age = None
    at = _quote_epoch(quote)
    if at is not None:
        p_age = float(now) - at
    # BOTH CLOCKS RE-AGED AT THE DECISION INSTANT. `vq["age_s"]` is the
    # age the book had when it was READ, and the decision that follows is
    # taken after the rules read and the fixture-metadata read as well --
    # so inheriting that number would claim a freshness the decision never
    # had. The venue's own transact time is carried out of the read for
    # exactly this, and when it did not provide one there is nothing to
    # re-age from, which stays unmeasured rather than falling back to our
    # read clock.
    #
    # THE VENUE ARM, AND WHAT IT IS ALLOWED TO CONCLUDE. This arm has now been
    # wrong twice in opposite directions, so both errors are named here.
    #
    #   The FIRST version re-aged `transactTime` and required it under 30 s.
    #   That treats an old stamp as proof the book is stale, which it is not:
    #   what the field denotes is unresolved.
    #
    #   The SECOND version re-aged OUR RECEIPT INSTANT and required that under
    #   10 s -- and returned `fresh: true` on it alone. That treats a fast
    #   response as proof the book is current, which is worse: a snapshot
    #   generated four minutes ago and delivered in 20 ms passes it, and the
    #   faster the answer the stronger the false certificate.
    #
    # SO THE TWO QUANTITIES ARE NOW SEPARATE AND BOTH ARE REQUIRED. Currency is
    # established by `bettor_venue_currency` from a published contract, or it is
    # NOT ESTABLISHED and this verdict cannot be `fresh: true`. Our processing
    # delay is checked as well, as OUR delay -- an additional requirement that
    # on its own establishes nothing upstream.
    vt = vq.get("venue_ts")
    stamp_age = (None if vt is None else float(now) - float(vt))
    # THE CURRENCY VERDICT, RE-AGED AT THE DECISION INSTANT. The quote carries
    # the verdict from the read; re-evaluating it here against `now` is what
    # stops a book whose currency was established at the read from inheriting
    # that establishment through two further network reads.
    cur_at_read = vq.get("book_currency") or {}
    currency = vc.evaluate(
        now=now,
        observation=vq.get("http_observation"),
        subscription=cur_at_read.get("subscription_input")
                     or vq.get("subscription"),
        revalidation=cur_at_read.get("revalidation_input")
                     or vq.get("revalidation"),
        venue_ts=vt, our_receipt_at=vq.get("read_at"),
        bound_s=MAX_VENUE_QUOTE_AGE_S)
    # OUR RECEIPT INSTANT, from either place the quote records it: the clock
    # block's `our_response_received_at` or the top-level `read_at`. The real
    # `venue_quote` sets both, and reading only one of them would make this gate
    # depend on which field a caller happened to carry.
    recvd = ((vq.get("venue_clock") or {}).get("our_response_received_at")
             if (vq.get("venue_clock") or {}).get("our_response_received_at")
             is not None else vq.get("read_at"))
    if recvd is not None:
        v_delay = float(now) - float(recvd)
        v_basis = "OUR_OWN_PROCESSING_DELAY_REAGED_AT_THE_DECISION"
    else:
        v_delay = None
        v_basis = "OUR_RECEIPT_INSTANT_NOT_RECORDED"
    # The number the venue arm is JUDGED on is the established book-state age.
    # None when nothing established it, which is UNKNOWN and blocks.
    v_age = currency.get("book_state_age_s") if vc.admits(currency) else None
    # THE PROVIDER'S OWN STALENESS AND OUR PROCESSING DELAY, SEPARATED.
    #
    # `pinnacle_age_s` is the number the 30-second rule governs and it is
    # UNCHANGED. But it is a sum: how old the price already was when the
    # provider gave it to us, plus how long we then took to decide. A
    # refusal reading 58 s says nothing about which, and the remedy differs
    # -- a slow provider is a coverage fact, our own delay is ours to fix.
    # Both are computed from clocks already on the record: the provider's
    # `last_update` and the receipt stamp taken at the fetch.
    recv = (quote or {}).get("received_at")
    prov_lag = (None if (at is None or recv is None)
                else round(float(recv) - at, 3))
    our_delay = (None if recv is None else round(float(now) - float(recv), 3))
    out = {"pinnacle_age_s": (None if p_age is None else round(p_age, 3)),
           "pinnacle_limit_s": PINNACLE_MAX_AGE_S,
           "pinnacle_provider_lag_s": prov_lag,
           "pinnacle_our_processing_s": our_delay,
           "age_decomposition": ("pinnacle_age_s = provider_lag + "
                                 "our_processing, both from clocks on the "
                                 "record. The 30 s rule governs the SUM "
                                 "and is unchanged"),
           # THE VENUE AGE IS THE ESTABLISHED BOOK-STATE AGE, or None.
           "venue_age_s": (None if v_age is None else round(float(v_age), 3)),
           "venue_age_at_read_s": vq.get("age_s"),
           "venue_limit_s": MAX_VENUE_QUOTE_AGE_S,
           "venue_age_basis": (currency["mechanism"] if vc.admits(currency)
                               else vc.NO_MECHANISM),
           "venue_currency_verdict": currency["verdict"],
           "venue_currency": currency,
           "venue_currency_is_required": (
               "`fresh` cannot be true unless a mechanism with a published "
               "contract established this book's state inside the bound. "
               "NOT_ESTABLISHED is UNKNOWN, and UNKNOWN blocks"),
           # OUR OWN DELAY, SEPARATE, AND NEVER SUFFICIENT.
           "our_processing_delay_s": (None if v_delay is None
                                      else round(float(v_delay), 3)),
           "our_processing_delay_limit_s": MAX_OUR_PROCESSING_DELAY_S,
           "our_processing_delay_basis": v_basis,
           "our_processing_delay_is_not_freshness":
               PROCESSING_DELAY_IS_NOT_FRESHNESS,
           # THE STAMP, AS PROVENANCE ONLY.
           "venue_stamp_age_s": (None if stamp_age is None
                                 else round(float(stamp_age), 3)),
           "venue_stamp_semantics": VENUE_STAMP_SEMANTICS,
           "venue_stamp_decides_nothing": (
               "an old stamp is not proof the book is stale and a recent one "
               "is not proof it is current, because what the field denotes is "
               "unresolved. It is carried as provenance"),
           "venue_clock": vq.get("venue_clock"),
           "both_reaged_at_the_decision": True,
           "stalest_governs": True}
    # UNKNOWN BLOCKS, AND SAYS WHICH SIDE IS UNKNOWN. The venue side is unknown
    # whenever no mechanism established currency -- which is the state the lane
    # is in today, and it is reported as missing evidence rather than as a
    # stale book.
    if p_age is None or v_age is None:
        out["fresh"] = None
        if v_age is None:
            out["why"] = (
                "the venue book's currency is %s, so whether this pair is "
                "contemporaneous is UNKNOWN. %s"
                % (currency["verdict"], currency.get("why") or ""))
            out["unknown_side"] = "venue"
            out["mechanisms_unavailable"] = currency["mechanisms_unavailable"]
        else:
            out["why"] = ("the bookmaker's own observation instant is not "
                          "measured, so whether this pair is contemporaneous "
                          "is unknown")
            out["unknown_side"] = "pinnacle"
        return out
    # A DELAY WE CANNOT MEASURE IS NOT A DELAY WE MAY IGNORE.
    if v_delay is None:
        out["fresh"] = None
        out["why"] = ("our own receipt instant was not recorded, so our "
                      "processing delay is unmeasured and cannot be checked")
        out["unknown_side"] = "our_processing_delay"
        return out
    if p_age < 0:
        out["fresh"] = False
        out["why"] = ("the bookmaker's observation is stamped %.2fs AFTER "
                      "the decision instant; a clock disagreement is not "
                      "freshness" % (-p_age,))
        out["unknown_side"] = "pinnacle_clock"
        return out
    ok = (p_age <= PINNACLE_MAX_AGE_S
          and float(v_age) <= MAX_VENUE_QUOTE_AGE_S
          and float(v_delay) <= MAX_OUR_PROCESSING_DELAY_S)
    out["fresh"] = bool(ok)
    out["why"] = ("pinnacle %.2fs/%.0fs (the bookmaker's own observation "
                  "instant); venue book state %.2fs/%.0fs ESTABLISHED by %s; "
                  "our own processing delay %.2fs/%.0fs. The venue stamp reads "
                  "%s and decides nothing"
                  % (p_age, PINNACLE_MAX_AGE_S, float(v_age),
                     MAX_VENUE_QUOTE_AGE_S, currency["mechanism"],
                     float(v_delay), MAX_OUR_PROCESSING_DELAY_S,
                     ("absent" if stamp_age is None
                      else "%.0f s old" % float(stamp_age))))
    return out


#: HOW OLD A CALIBRATION MEASUREMENT MAY BE AND STILL OPEN THE GATE. The
#: evaluator scores a 90-day window; a measurement not repeated for two weeks
#: describes a source that may have moved since. Stale is NOT MEASURED.
CALIBRATION_MAX_AGE_S = 14 * 86400.0

CALIBRATION_SQL = """
    SELECT source_version, sample_size, metric, score, tolerance,
           within_tolerance, measured_by, provenance,
           extract(epoch FROM measured_at) AS measured_at_epoch_s,
           to_char(measured_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS measured_at,
           to_char(window_start, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
               AS window_start,
           to_char(window_end, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS window_end
      FROM external_source_calibration
     WHERE source_version = $1
     ORDER BY measured_at DESC LIMIT 1
"""


async def source_calibration(conn, source_version) -> dict:
    """The latest calibration MEASUREMENT for the external source.

    `measured` is False when there is no row, when the table does not
    exist, or when the read fails -- and in every one of those cases the
    MODEL_TRUST_DRIFT gate stays NOT_EVALUABLE and blocks inventory. The
    three cases are distinguished in `why` because they call for different
    actions, but none of them is a pass.

    THE GATE READS A ROW, NOT A CONSTANT, and that is the point: the
    blocker is cleared by producing evidence about the source, not by
    editing a boolean in a worker.
    """
    try:
        row = await conn.fetchrow(CALIBRATION_SQL, str(source_version))
    except Exception as exc:                                   # noqa: BLE001
        return {"measured": False, "error": type(exc).__name__,
                "source_version": source_version,
                "why": ("the calibration read failed, which is not "
                        "evidence that the source is calibrated")}
    if row is None:
        return {"measured": False, "error": None,
                "source_version": source_version,
                "why": ("no calibration has been measured for %s. Its own "
                        "module says to validate it in shadow, and that "
                        "measurement has not been made" % source_version)}
    d = dict(row)
    # ── A ROW IS A MEASUREMENT ONLY IF THE CURRENT EVALUATOR MADE IT ──
    #
    # THE HOLE (production-prerequisite investigation, 2026-09-29). This read
    # accepted ANY newest row: a hand-inserted one, one written by an older
    # evaluator whose acceptance conditions no longer apply, one measured
    # months ago, or one labelled by a test. `bettor_source_calibration.measure`
    # is the only production writer and stamps `provenance.evaluator`; a row
    # without the CURRENT evaluator, below the evaluator's own minimum sample,
    # or older than CALIBRATION_MAX_AGE_S is reported and NOT counted.
    from .. import bettor_source_calibration as _CAL
    prov = d.get("provenance")
    if isinstance(prov, str):
        try:
            prov = __import__("json").loads(prov)
        except ValueError:
            prov = {}
    prov = prov if isinstance(prov, dict) else {}
    d["provenance"] = prov
    age = (time.time() - float(d["measured_at_epoch_s"])
           if d.get("measured_at_epoch_s") is not None else None)
    not_a_measurement = None
    if prov.get("evaluator") != _CAL.VERSION:
        not_a_measurement = ("CALIBRATION_ROW_NOT_FROM_THE_CURRENT_EVALUATOR",
                             "the newest row names evaluator %r; only %s "
                             "measurements open this gate"
                             % (prov.get("evaluator"), _CAL.VERSION))
    elif int(d.get("sample_size") or 0) < _CAL.MIN_RESOLVED_EVENTS:
        not_a_measurement = ("CALIBRATION_ROW_BELOW_THE_EVALUATORS_MINIMUM",
                             "%s resolved events, below the evaluator's own "
                             "minimum of %d" % (d.get("sample_size"),
                                                _CAL.MIN_RESOLVED_EVENTS))
    elif age is None or age > CALIBRATION_MAX_AGE_S:
        not_a_measurement = ("CALIBRATION_ROW_IS_STALE",
                             "measured %s ago, past the %.0f-day limit"
                             % ("an unknown time" if age is None
                                else "%.1f days" % (age / 86400.0),
                                CALIBRATION_MAX_AGE_S / 86400.0))
    if not_a_measurement is not None:
        return {"measured": False, "error": not_a_measurement[0],
                "source_version": source_version,
                "newest_row": {k: d.get(k) for k in
                               ("measured_at", "measured_by", "sample_size",
                                "within_tolerance")},
                "why": not_a_measurement[1]}
    d["measured"] = True
    d["age_s"] = round(age, 1)
    d["why"] = ("%s = %.6f against a tolerance of %.6f over %d resolved "
                "observations (%s to %s), measured by %s"
                % (d["metric"], float(d["score"]), float(d["tolerance"]),
                   int(d["sample_size"]), d["window_start"], d["window_end"],
                   d["measured_by"]))
    return d


#: ── THE CALIBRATION MEASUREMENT, SCHEDULED ───────────────────────────
#:
#: THE GAP THIS CLOSES. `bettor_source_calibration.measure` had one caller,
#: the admin route. Nothing ran it on a schedule, so the cohort could grow
#: and never be measured, and a PASSED row would lapse at
#: CALIBRATION_MAX_AGE_S (14 days) unless someone remembered the route.
#:
#: WHAT RUNS. `measure(write=True)` over the default 90-day window, at most
#: once per CALIBRATION_MEASURE_EVERY_S, right after the outcome join. The
#: evaluator writes ONLY a completed PASSED/FAILED verdict; an INSUFFICIENT
#: result is returned and reported and never written, so running it changes
#: no gate until the evidence exists. It does NOT change the entry gate's
#: requirement (current evaluator, >= 300 scored fixtures, <= 14 days,
#: within tolerance) -- `source_calibration` above is untouched.
#:
#: HOW RESTARTS ARE KEPT FROM HAMMERING IT. Three instants, the latest wins:
#: this process's own last run; the newest measured row for the source (any
#: measurer -- an admin run an hour ago is a measurement an hour old); and
#: the last run recorded in this loop's own heartbeat, which covers the
#: INSUFFICIENT case that writes no row.
CALIBRATION_MEASURE_EVERY_S = 6 * 3600.0
CALIBRATION_MEASURED_BY = "SCHEDULED_CALIBRATION_RUN"
CALIBRATION_MEASURE_WINDOW_DAYS = 90
_LAST_CALIBRATION_MEASURE = [0.0]

NEWEST_MEASUREMENT_SQL = """
    SELECT extract(epoch FROM max(measured_at))::float8
      FROM external_source_calibration
     WHERE source_version = $1
"""

#: The previous cycle's measurement digest, from this loop's own heartbeat:
#: when it last ran, and what that run found -- so a cycle on which the
#: measurement is not due still reports the latest result, including after a
#: restart.
LAST_SCHEDULED_RUN_SQL = """
    SELECT (value::jsonb -> 'source_calibration_measurement')::text
      FROM ingestion_state WHERE key = $1
"""


async def _scheduled_calibration_measurement(conn, *, now: float) -> dict:
    """RUN THE CALIBRATION MEASUREMENT IF IT IS DUE. Never raises.

    Returns the digest's input: whether it ran, when it last ran and is next
    due, and either `measure`'s own result (it ran) or the previous run's
    compact result carried from the heartbeat (it did not). A read that could
    not say when the last run was is reported by name and does not stop the
    run: the process-local guard still bounds it, and an unknown is not a
    reason to skip evidence collection.
    """
    import json as _json

    from .. import bettor_source_calibration as _CAL

    last_local = float(_LAST_CALIBRATION_MEASURE[0] or 0.0)
    guard = {"process_last_ran_at": last_local or None,
             "newest_measured_row_at": None, "heartbeat_last_ran_at": None,
             "read_errors": []}
    prev: dict = {}
    try:
        guard["newest_measured_row_at"] = await conn.fetchval(
            NEWEST_MEASUREMENT_SQL, devig.VERSION)
    except Exception as exc:                                   # noqa: BLE001
        guard["read_errors"].append("NEWEST_MEASUREMENT:%s"
                                    % type(exc).__name__)
    try:
        raw = await conn.fetchval(LAST_SCHEDULED_RUN_SQL, HEARTBEAT_KEY)
        prev = _json.loads(raw) if raw else {}
        prev = prev if isinstance(prev, dict) else {}
        if prev.get("last_ran_at") is not None:
            guard["heartbeat_last_ran_at"] = float(prev["last_ran_at"])
    except Exception as exc:                                   # noqa: BLE001
        guard["read_errors"].append("HEARTBEAT_LAST_RUN:%s"
                                    % type(exc).__name__)
    last = max([float(x) for x in (last_local,
                                   guard["newest_measured_row_at"],
                                   guard["heartbeat_last_ran_at"])
                if x is not None] or [0.0])
    base = {"every_s": CALIBRATION_MEASURE_EVERY_S,
            "measured_by": CALIBRATION_MEASURED_BY,
            "window_days": CALIBRATION_MEASURE_WINDOW_DAYS,
            "evaluator": _CAL.VERSION, "guard": guard}
    if last and float(now) - last < CALIBRATION_MEASURE_EVERY_S:
        return dict(base, ran=False, why="NOT_DUE",
                    last_ran_at=last,
                    next_due_at=last + CALIBRATION_MEASURE_EVERY_S,
                    carried_last_run=prev.get("last_run"))
    _LAST_CALIBRATION_MEASURE[0] = float(now)
    try:
        got = await _CAL.measure(conn, experiment_id=ext.EXPERIMENT_ID,
                                 days=CALIBRATION_MEASURE_WINDOW_DAYS,
                                 now=float(now),
                                 measured_by=CALIBRATION_MEASURED_BY,
                                 write=True)
    except Exception as exc:                                   # noqa: BLE001
        # `measure` catches its own read and write failures; anything else
        # is a defect, reported as one rather than as "insufficient".
        got = {"ran": False, "error": "MEASURE_RAISED:%s: %s"
               % (type(exc).__name__, str(exc)[:160])}
    return dict(base, ran=bool(got.get("ran")), attempted=True,
                last_ran_at=float(now),
                next_due_at=float(now) + CALIBRATION_MEASURE_EVERY_S,
                result=got)


#: ── THE SHADOW BOOK'S EVENT IDENTITY (audit finding A9) ─────────────
#:
#: THE DEFECT. This query selected `condition_id`, cost, quantity, the decision
#: instant and the realised net -- and NO EVENT IDENTITY. `exposure_from_rows`
#: sums a row into MAX_EVENT_EXPOSURE only when `r["event_key"]` equals the
#: proposed position's event key; with the column absent, `.get("event_key")` is
#: None on every row, the condition never holds, and the event rail measured
#: ZERO however many positions this lane already held on the same fixture. It
#: was not an unenforced rail; it was a rail reading a quantity that was never
#: selected. The funded lane carries its own event key and was never affected,
#: which is exactly why the funded repair did not fix this one.
#:
#: WHERE THE IDENTITY COMES FROM. `rn1x_positions.venue_market_slug` is the
#: venue's own market slug, and `us_premap.market_slug -> event_slug` is the
#: venue's own event. That is the join the copy path already trusts, and using
#: the VENUE's event rather than the odds provider's matters: two contracts on
#: one fixture share a venue event slug, and the provider's event id is a
#: different namespace that would never compare equal.
#:
#: AND A ROW WHOSE EVENT CANNOT BE RESOLVED IS NOT A ROW ON A DIFFERENT EVENT.
#: `event_key` stays NULL there and `event_key_resolved` says so, so the caller
#: can refuse rather than quietly measure a smaller number. That distinction is
#: the whole finding: the old behaviour was silently-None on every row.
OPEN_BOOK_SQL = """
    SELECT p.condition_id,
           p.seed_basis_usd::float8 AS cost_usd,
           p.seed_qty::float8       AS qty,
           extract(epoch FROM p.decision_ts)::float8 AS opened_at,
           o.net_usd::float8        AS realized_net_usd,
           p.venue_market_slug,
           m.event_slug             AS event_key,
           (m.event_slug IS NOT NULL) AS event_key_resolved
      FROM rn1x_positions p
      LEFT JOIN rn1x_outcomes o ON o.position_id = p.position_id
      LEFT JOIN us_premap m      ON m.market_slug = p.venue_market_slug
     WHERE p.experiment_id = $1
"""

R_EVENT_IDENTITY_UNRESOLVED = "OPEN_BOOK_ROW_HAS_NO_RESOLVABLE_EVENT_IDENTITY"


async def open_shadow_book(conn, experiment_id) -> list | None:
    """The lane's own open inventory, or None when it could not be read.

    None IS NOT AN EMPTY BOOK. An unread book leaves every rail
    NOT_EVALUABLE and blocks the entry; an empty book is a measurement
    that happens to be zero. Collapsing the two would permit a trade
    because the database was down.

    EACH ROW NOW CARRIES ITS VENUE EVENT SLUG. A row whose slug does not resolve
    keeps `event_key: None` and `event_key_resolved: False`, which the caller
    must treat as an unmeasurable event rail rather than as a row belonging to
    some other event.
    """
    try:
        rows = await conn.fetch(OPEN_BOOK_SQL, str(experiment_id))
    except Exception:                                          # noqa: BLE001
        return None
    return [dict(r) for r in rows]


def event_exposure_is_measurable(open_book) -> dict:
    """Can MAX_EVENT_EXPOSURE be measured over this book at all?

    A9's real consequence was a rail that read zero and looked satisfied. So the
    answer is explicit: every OPEN row must carry a resolved event identity, and
    where one does not, the rail is NOT_EVALUABLE and the caller refuses. A
    settled row is excluded because it occupies no exposure.
    """
    if open_book is None:
        return {"measurable": None, "why": "the open book could not be read",
                "refusal": entryx.R_BOOK_NOT_READ}
    unresolved = [r for r in open_book
                  if r.get("realized_net_usd") is None
                  and not r.get("event_key_resolved")]
    if unresolved:
        return {"measurable": False,
                "refusal": R_EVENT_IDENTITY_UNRESOLVED,
                "rows_without_an_event_identity": len(unresolved),
                "open_rows": len([r for r in open_book
                                  if r.get("realized_net_usd") is None]),
                "slugs": [r.get("venue_market_slug") for r in unresolved][:10],
                "why": ("each of these open rows has no resolvable venue event "
                        "slug, so exposure on its fixture cannot be summed. "
                        "Measuring the rail without them would report a "
                        "smaller number and call the rail satisfied")}
    return {"measurable": True,
            "open_rows": len([r for r in open_book
                              if r.get("realized_net_usd") is None]),
            "why": "every open row carries a resolved venue event identity"}


def _settlement_compatibility(srule) -> dict:
    """The per-condition comparison verdict, from where `attest` puts it.

    The condition-to-payout comparison lives under the `void` rule --
    that is the rule whose prose the captured terms speak to -- and the
    engine's verdict is COMPATIBLE, INCOMPATIBLE or UNKNOWN. Anything
    else, including a rule dict that predates the comparison, reads as
    NOT_ESTABLISHED, which leaves the gate NOT_EVALUABLE rather than
    quietly clear.
    """
    void = ((srule or {}).get("rules") or {}).get("void") or {}
    cmp_ = void.get("terms_comparison") or {}
    # THE ESTABLISHMENT IS POSITIVE EVIDENCE OR NOTHING. `overall_established`
    # is attest's own verdict AND a COMPATIBLE comparison with no named
    # blocker; a projection that lacked a recognised refusal used to read as
    # established beside compatibility=UNKNOWN.
    sb = vset.settlement_blockers(srule, fixture=(srule or {}).get(
        "fixture_metadata"))
    return {"compatibility": cmp_.get("verdict"),
            "mismatched_conditions": cmp_.get("mismatched_conditions"),
            "applicable_conditions": cmp_.get("applicable_conditions"),
            "unstated_conditions": cmp_.get("unstated_conditions"),
            "compared_on": void.get("compared_on"),
            "overall_established": sb["established"],
            "attest_unmet": list((srule or {}).get("unmet") or []),
            "blockers": sb["blockers"],
            "per_condition": sb["per_condition"],
            "book_ambiguous_conditions": sb["book_ambiguous_conditions"],
            "book_capture": sb["book_capture"]}


def _entry_plan(*, ladder, fee_fn, observation_age_s, action, condition_id,
                event_key, open_book, settlement, freshness, calibration,
                now, research_authorised=False, provider_event_id=None,
                event_exposure_measurable=None, venue_market_slug=None):
    """A callable `bettor_external_shadow.evaluate` invokes once.

    It receives the fair value that function computed -- so there is
    exactly one valuation and exactly one complement inversion -- and
    returns the size, the marketable execution estimate, the price of the
    quantity actually claimed, and the risk verdict.
    """
    def plan(*, fair_value, contract):
        # ── WHAT THE RAILS STILL ALLOW, MEASURED BEFORE SIZING ───────
        #
        # Production run 71 showed every positive-edge candidate failing
        # all five exposure rails at once, because a STANDARD-dollar
        # budget reserved at the break-even limit always exceeds a
        # STANDARD-dollar rail by exactly the edge. Sizing now asks the
        # rails what is left FIRST and spends at most that. No limit
        # moves; the standard trade remains the ceiling.
        # THE VENUE CONTRACT TOO, so the market rail counts an open row on
        # the same venue contract whichever catalogue its identity came from
        # (a venue-native candidate carries no global condition id).
        head = entryx.headroom_from_rows(
            open_book, condition_id=condition_id, event_key=event_key,
            now=now, venue_market_slug=venue_market_slug)
        # ── THE EVENT RAIL MUST BE MEASURABLE, NOT MERELY SMALL (A9) ──
        #
        # Until the open-book query carried an event identity, this rail summed
        # nothing and reported zero exposure on every fixture -- a rail that
        # looks satisfied because the quantity it reads was never selected. Both
        # facts now travel with the headroom, and an unmeasurable rail refuses
        # by name instead of passing quietly.
        head["event_key_used"] = event_key
        head["event_key_namespace"] = "us_premap.event_slug (the VENUE's event)"
        head["provider_event_id_is_not_the_event_key"] = provider_event_id
        head["event_exposure_measurable"] = event_exposure_measurable
        if event_key in (None, ""):
            refusals_pre = [R_EVENT_IDENTITY_UNRESOLVED]
        elif (event_exposure_measurable or {}).get("measurable") is not True:
            refusals_pre = [(event_exposure_measurable or {}).get("refusal")
                            or R_EVENT_IDENTITY_UNRESOLVED]
        else:
            refusals_pre = []
        if refusals_pre:
            return {"ok": False, "refusals": refusals_pre,
                    "detail": {"rail_headroom": head},
                    "why": ("the event exposure rail cannot be measured over "
                            "this book, so the rail cannot be shown to hold. "
                            "It is refused rather than read as zero")}
        est = entryx.estimate(ladder=ladder, fair_value=fair_value,
                              fee_fn=fee_fn,
                              observation_age_s=observation_age_s,
                              headroom=head)
        detail = {"execution": est, "rail_headroom": head}
        refusals = list(est.get("refusals") or [])
        if not est.get("ok"):
            # NO SIZE MEANS NO RISK QUESTION. Evaluating rails against a
            # position that was never sized would produce a verdict about
            # nothing, and a `permitted: True` from it would be the
            # placeholder returning by another door.
            detail["risk"] = {"evaluated": False,
                              "why": ("no execution estimate, so there is "
                                      "no proposed position to measure "
                                      "exposure for")}
            return {"execution_estimate": None, "size": None,
                    "ask": None, "refusals": refusals,
                    "risk": {"permitted": False,
                             "reason": "NO_SIZED_POSITION_TO_ASSESS"},
                    "detail": detail}

        # THE RESERVATION IS DELIBERATELY THE WORST CASE. Nothing fills
        # above the submitted limit, so sizing the exposure at the limit
        # reserves the most the order could possibly consume -- which is
        # the conservative thing to do against a rail. It is NOT the
        # expected acquisition cost, and the two must not be swapped:
        # reserving at the modelled walk would under-reserve, and
        # comparing edge at the limit destroys the edge (see below).
        cost = float(est["size"]) * float(est["worst_case_cost_per_contract"])
        exposure = entryx.exposure_from_rows(
            open_book, condition_id=condition_id, event_key=event_key,
            proposed_cost_usd=cost, proposed_qty=est["size"], now=now,
            proposed_cost_basis="SIZE_TIMES_WORST_CASE_COST_PER_CONTRACT",
            venue_market_slug=venue_market_slug)
        gates = entryx.state_from_evidence(
            freshness=freshness,
            settlement=_settlement_compatibility(settlement),
            probability=fair_value,
            # THE MEASUREMENT AS READ, not an assertion made here.
            # Production finds no row -- PINNACLE_DEVIG_V1's own note on
            # its default method is "validate in shadow" and that
            # validation has not been done -- so the gate stays
            # NOT_EVALUABLE.
            calibration=calibration)

        # ── THE UNFUNDED RESEARCH WAIVER, APPLIED ABOVE THE GATE ─────
        #
        # `state_from_evidence` IS NOT TOUCHED and MODEL_TRUST_DRIFT still
        # reads None in `gates["state"]`. The waiver returns a SEPARATE map
        # for the risk engine, keeps the original under
        # `gate_state_as_read`, and both are recorded -- so the row always
        # shows what the gate said as well as what the engine was given.
        #
        # Unauthorised, it waives nothing and the engine sees the gate map
        # unchanged, which is the production default.
        waiver = rsh.waive(gates["state"],
                           authorised_flag=research_authorised,
                           calibration=calibration)
        verdict = entryx.verdict(action, observed=exposure["observed"],
                                 state=waiver["state"])
        # THE WAIVER, CARRIED ON THE PERSISTED ROW. `detail` holds the
        # complete record, but only `execution`, `risk` and `exposure` are
        # stored as columns on the valuation -- so a later reader of the
        # database could not tell whether a decision used the waiver, which
        # is precisely the question the research lane has to answer. The
        # risk verdict is the section that CONSUMED the waived map, so the
        # record travels with it. It is a copy and an input to nothing.
        verdict = dict(
            verdict,
            research_waiver={
                "version": waiver.get("version"),
                "authorised": waiver.get("authorised"),
                "waived": list(waiver.get("waived") or []),
                "refusals": list(waiver.get("refusals") or []),
                "gate_state_as_read": waiver.get("gate_state_as_read"),
            },
            # ── THE FRESHNESS EVIDENCE, FOR THE SAME REASON ──────────
            #
            # STALE_DATA is a gate this verdict CONSUMED, and production
            # reported it failing on candidates whose Pinnacle quote was
            # 16 and 22 seconds old against a 30-second rule -- which
            # looks like a contradiction and is not one. `_entry_freshness`
            # returns `fresh: None` whenever EITHER clock is unmeasured,
            # and the venue supplies no `transactTime` on some reads, so
            # the verdict is UNKNOWN rather than STALE. Those two have
            # completely different remedies: one is our latency, the other
            # is a field the venue did not send.
            #
            # None of that was on the row, so the question could only be
            # answered by re-deriving it. It is on the row now: both ages,
            # both limits, the venue's clock basis, and the provider-lag /
            # our-processing split of the Pinnacle age.
            freshness_evidence={
                "fresh": (freshness or {}).get("fresh"),
                "why": (freshness or {}).get("why"),
                "pinnacle_age_s": (freshness or {}).get("pinnacle_age_s"),
                "pinnacle_limit_s": (freshness or {}).get("pinnacle_limit_s"),
                "pinnacle_provider_lag_s": (
                    (freshness or {}).get("pinnacle_provider_lag_s")),
                "pinnacle_our_processing_s": (
                    (freshness or {}).get("pinnacle_our_processing_s")),
                "venue_age_s": (freshness or {}).get("venue_age_s"),
                "venue_limit_s": (freshness or {}).get("venue_limit_s"),
                "venue_age_basis": (freshness or {}).get("venue_age_basis"),
                # THE RAW CLOCK VALUE AND ITS TYPE, on the row. A refusal
                # that says only "unparseable" cannot be acted on; one that
                # carries the field path, the value and the parser can.
                "venue_clock": (freshness or {}).get("venue_clock"),
                "venue_age_at_read_s": (
                    (freshness or {}).get("venue_age_at_read_s")),
                "unknown_is_not_stale": (
                    "fresh=null means a clock was not measured, so whether "
                    "the pair is contemporaneous is UNKNOWN. fresh=false "
                    "means both were measured and one was too old"),
            },
            # WHAT THE RAILS LEFT, AND WHICH ONE BOUND THE SIZE. Without
            # this a reader cannot tell a rail that refused the candidate
            # from a rail that merely sized it.
            rail_headroom=(est.get("rail_headroom") or {}).get("headroom"),
            qty_cap=est.get("qty_cap"),
            notional_reduced_by_rails=est.get("notional_reduced_by_rails"),
            policy_notional_usd=est.get("policy_notional_usd"))
        detail["research_waiver"] = waiver
        detail.update({"exposure": exposure, "gates": gates,
                       "gates_seen_by_the_engine": waiver["state"],
                       "risk": verdict, "proposed_cost_usd": round(cost, 6),
                       "proposed_cost_basis":
                           "SIZE_TIMES_WORST_CASE_COST_PER_CONTRACT",
                       "risk_action": action})
        refusals.extend(exposure.get("refusals") or [])
        # ── THE THREE PRICES, EACH TO ITS OWN CONSUMER ───────────────
        # `acquisition_cost_per_contract` is what the quantity is
        # modelled to actually cost across the levels walked: that is
        # the number the economic comparison must use, because the edge
        # is (probability - what we pay - fees).
        #
        # `submitted_limit` is the break-even price the belief implies.
        # It is the ORDER's price, and it is by construction equal to
        # fair_value - fee, so an edge computed against it is zero minus
        # rounding, always. Handing it to the gate as the "ask" is the
        # defect that produced 159 NO_ACTION_HAS_POSITIVE_NET_EDGE
        # refusals in run 48: arithmetic, not a market fact.
        #
        # `worst_case_cost_per_contract` is the reservation price above.
        return {"execution_estimate": est,
                "size": est["size"],
                "ask": est["acquisition_cost_per_contract"],
                "ask_basis": est["acquisition_cost_is"],
                "fee_per_contract": est["fee_per_contract_realised"],
                "submitted_limit": est["submitted_limit"],
                "worst_case_cost_per_contract":
                    est["worst_case_cost_per_contract"],
                "risk": verdict,
                "refusals": refusals,
                "detail": detail}
    return plan


# ── one cycle ───────────────────────────────────────────────────────

async def venue_account_exposure() -> dict:
    """WHAT THE ACCOUNT HOLDS AND HAS WORKING AT THE VENUE, OR WHY NOT.

    Delegates to `bettor_funded_execution.read_venue_account`. The read lives
    there, beside the connector whose gate needs it, because THIS module is
    held to `book_read` and `_get_client` off `pmus`
    (`test_the_lane_takes_only_the_two_permitted_names_off_pmus`) so that the
    shadow lane cannot acquire a venue capability by accident. An account read
    is read-only, but the rule is about which module holds venue access, and
    the funded modules already do.
    """
    from .. import bettor_funded_execution as _FX

    return await _FX.read_venue_account()


def _venue_positions_for_gate(read: dict) -> dict | None:
    """The shape `bettor_account_exposure.account_exposure` reads, or None."""
    from .. import bettor_funded_execution as _FX

    return _FX.venue_positions_for_gate(read)


#: `agents.runtime.R_DEREK_GATE_UNAVAILABLE`, repeated here so the refusal is
#: named even when the agents package itself cannot be imported.
R_DEREK_GATE_UNAVAILABLE = "DEREK_ENTRY_POLICY_UNAVAILABLE_SO_NOTHING_IS_SENT"


def _agents_runtime():
    """THE AGENTS' GUARDED RUNTIME (core, migration 152). Imported lazily;
    every call site contains a failure, so an agent can never break the
    cycle, the servicing pass or recovery."""
    from ..agents import runtime as _AR

    return _AR


async def _funded_attempt(conn, rec, *, now):
    """OFFER ONE ADMITTED DECISION TO THE FUNDED CONNECTOR.

    Returns None when the funded lane is not configured at all, so an
    unconfigured deployment adds nothing to the cycle report. Otherwise it
    returns the connector's whole answer, refusal included, because "what
    stopped it" is the useful line in a cycle log.

    NOTHING HERE DECIDES ANYTHING. Every gate lives in the connector: the
    account row, the owner-approved limits, the complete rails against the
    funded book, the authorization gate's affirmative answer, and the code
    switch. This function only carries the decision across.

    EXCEPT ONE REFUSAL, TAKEN HERE AND FIRST: a calibration-only record is
    never offered to the connector at all -- not even to be refused there --
    and nothing is read for it. The connector refuses it too.
    """
    from .. import bettor_funded_activation as _FA
    from .. import bettor_funded_execution as _FX
    from .. import bettor_valuation_purpose as _vp

    refused = _vp.refuse_unless_entry(rec)
    if refused is not None:
        return refused
    try:
        bound = _FA._obj(await _FA._state(conn, _FA.ACCOUNT_KEY)) or {}
    except Exception:                                          # noqa: BLE001
        return None
    account_id = str(bound.get("account_id") or "").strip()
    venue = str(bound.get("venue") or "").strip()
    if not account_id or not venue:
        # NOT CONFIGURED. Not a refusal -- there is nothing to refuse yet.
        return None
    # ── THE ONE EXECUTION AUTHORITY ─────────────────────────────────
    #
    # This is a submit path, so it takes the same lock a servicing pass holds
    # (see `_execution_lock`), BEFORE the account read, so the exposure the
    # gate measures cannot move under a concurrent servicing pass's dispatch.
    # A bounded wait, then a named refusal: a decision that aged past the
    # freshness bound while it waited must not be sent.
    lock = _execution_lock()
    try:
        await asyncio.wait_for(lock.acquire(), ENTRY_WAITS_FOR_EXECUTION_S)
    except (asyncio.TimeoutError, TimeoutError):
        return {"ok": False, "refusal": R_EXECUTION_AUTHORITY_BUSY,
                "waited_s": ENTRY_WAITS_FOR_EXECUTION_S}
    try:
        # ── AGENTS (core, migration 152): DEREK'S ENTRY POLICY, BINDING ──
        # The owner's entry policy (`agents.derek_policy.
        # gate_for_funded_entry`) must answer 'ENTER' before anything is read
        # or sent. Another verdict, a raise, an overrun or a missing module
        # REFUSES by name and nothing is sent. Entry only: exits, management
        # and recovery never pass through here.
        try:
            _gate = await _agents_runtime().gate_for_funded_entry(
                conn, rec, now=now)
        except Exception as exc:                               # noqa: BLE001
            _gate = {"enter": False, "refusal": R_DEREK_GATE_UNAVAILABLE,
                     "gate": {"error_type": type(exc).__name__}}
        if not _gate.get("enter"):
            return {"ok": False,
                    "refusal": _gate.get("refusal") or R_DEREK_GATE_UNAVAILABLE,
                    "derek_gate": _gate.get("gate"), "nothing_was_sent": True}
        # THE ORDER NAMES THE DEREK DECISION THAT AUTHORISED IT. The gate
        # records its decision (derek_entry_decisions) before answering; its
        # id was dropped here, so the intent -- and every fill, handoff and
        # Xavier review keyed on it -- could be tied back to Derek only by
        # matching slug, side and time. Carried on a copy of the record; the
        # connector writes it onto the intent's decision_ref.
        _derek_id = (_gate.get("gate") or {}).get("decision_id")
        if _derek_id:
            rec = dict(rec, derek_decision_id=str(_derek_id))
        # THE ACCOUNT, READ AT THE VENUE, so the execution gate can measure
        # it. A failed read passes None and the gate refuses by name, as
        # before.
        read = await venue_account_exposure()
        try:
            # THE SCHEDULED ENTRY IS FITTED TO THE APPROVED RAILS. The count
            # on `rec` is the shadow cohort's; the connector reduces it to the
            # largest count every approved rail clears at the same limit,
            # records that on the intent, and refuses when not one contract
            # fits.
            got = await _FX.submit_for_decision(
                conn, rec, account_id=account_id, venue=venue, now=now,
                venue_positions=_venue_positions_for_gate(read),
                size_to_approved_rails=True)
            return dict(got, venue_account_read={
                k: read.get(k) for k in ("ok", "refusal", "held_usd",
                                         "working_usd", "pages",
                                         "open_orders", "read_at_epoch_s",
                                         "why")})
        except Exception as exc:                               # noqa: BLE001
            # A CONNECTOR THAT RAISES MUST NOT TAKE THE CYCLE DOWN, and it
            # must not be reported as a clean refusal either.
            return {"ok": False, "refusal": "FUNDED_CONNECTOR_RAISED",
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    finally:
        lock.release()


def book_currency_evidence(slug=None) -> dict:
    """THE ONE SEAM THROUGH WHICH A FRESHNESS MECHANISM REACHES EITHER LANE.

    Both lanes admit a venue book only when a mechanism with a published
    contract establishes that it is current. This function is where that
    mechanism is supplied, and TODAY IT SUPPLIES NONE:

      * M1 needs a live market-data subscription for the market, with per-slug
        last-update and connection-liveness instants. `bettor_market_stream`
        speaks the protocol and the EV lane does not subscribe.
      * M2 needs a conditional re-request on the book path. Whether the endpoint
        emits a validator is reported per read by the admin clock probe and is
        not yet known.

    So this returns `{"subscription": None, "revalidation": None}` with the
    reason attached, every lane refuses on an unestablished currency, and that
    refusal is the named blocker the operating view reports. IT IS A SEAM AND
    NOT A DEFAULT: nothing here invents an allowance, and wiring M1 means
    returning real instants from here rather than changing any gate.
    """
    from .. import bettor_stream_currency as _sc
    from .. import bettor_market_subscription as _msub

    # THE DECISION PROCESS ASKS FOR THIS MARKET. Every market whose currency is
    # asked about here is one the subscription must hold, so asking IS the
    # subscribe request. A no-op (never raises) when no subscription runs in
    # this process -- and the answer below is then today's refusal.
    if slug:
        _msub.want([slug])
    m1 = _sc.evidence_for(slug)
    return {
        "subscription": m1.get("subscription"),
        "revalidation": None,
        "m1": {"status": _sc.M1_STATUS,
               "missing_from_the_feed": list(_sc.MISSING_PRECONDITIONS),
               "refusal": m1.get("refusal"),
               "why": m1.get("why"),
               "state": m1.get("state"),
               # THE SUBSCRIPTION'S PER-MARKET READINESS, and the name of the
               # part of M1 that was missing (a venue refusal lands here).
               "readiness": m1.get("readiness"),
               "subscription_refusal": m1.get("subscription_refusal")},
        # ── WHY, FROM THE MODULE THAT OWNS THE QUESTION ────────────────
        #
        # THE DEFECT THIS REPLACES (2026-09-28). This key used to carry a
        # HAND-WRITTEN PARALLEL ACCOUNT of why M1 is unavailable, and that
        # account had gone stale: it said the payload "carries no sequence
        # number (so a dropped message is undetectable) and nothing
        # distinguishes a snapshot from an increment". `bettor_stream_currency`
        # had already EXAMINED AND WITHDRAWN both of those as preconditions --
        # P1 replacement authority is ESTABLISHED from the venue's published
        # subscription table (MARKET_DATA is documented as the full order
        # book), and the delta-continuity requirement was withdrawn because
        # this feed has no deltas for a sequence to order.
        #
        # So two modules disagreed and THE ONE AN OPERATOR READS WAS THE
        # STALE ONE. It is the immediate reason a report asserted the feed
        # could never support M1, which is a stronger claim than the evidence
        # carries and not the one the owning module makes.
        #
        # The fix is structural rather than a better paragraph: this hands
        # back the OWNING MODULE'S OWN `why` plus its machine-readable
        # missing-precondition list, so the explanation cannot drift from the
        # verdict again. A test asserts the two agree.
        "why_none": (m1.get("why") if _sc.MISSING_PRECONDITIONS
                     else m1.get("why")),
        "missing_preconditions": list(_sc.MISSING_PRECONDITIONS),
        "why_source": ("bettor_stream_currency.evidence_for(), which owns this "
                       "question. Not restated here, because a parallel "
                       "account is what went stale"),
        "and_what_the_gap_is_NOT": (
            "not missing replacement authority. P1 IS established from the "
            "venue's published subscription-types table, which documents "
            "SUBSCRIPTION_TYPE_MARKET_DATA as the full order book, so the "
            "feed is fit to hold a displayed book. And not delta sequencing: "
            "that requirement was withdrawn because a full-replacement feed "
            "has no increments for a sequence to order"),
        "what_would_change_it": (
            # ALSO CORRECTED. This used to ask the venue to document "the
            # market-data message as a full replacement" -- WHICH IT ALREADY
            # DOES. Asking for something already supplied made the blocker
            # look permanent when the real gap is narrower.
            "a published TIMING contract -- an as-of instant, a latency bound, "
            "or a documented meaning for transactTime -- which closes P5, the "
            "one unmet feed-level precondition; or an ETag / Last-Modified on "
            "the book path, which makes M2 available. Replacement authority is "
            "already published and does not need to change. And CONNECTION "
            "CONTINUITY is OURS to fix: the client emits 'close' and returns "
            "rather than reconnecting, resubscribing and resynchronising"),
        "consequence_today": ("every venue read reaches "
                              "BOOK_CURRENCY_NOT_ESTABLISHED and refuses. That "
                              "is missing evidence, not a stale book"),
        "slug": slug,
    }


# ═════════════════════════════════════════════════════════════════════
# THE PAIRING INPUT SUPPLIER
# ═════════════════════════════════════════════════════════════════════
#
# `pass_once` refuses to invent any of these, and it is right to: every one is a
# reading of a venue, a fee schedule or an odds source, and a module that
# defaulted one would be manufacturing the input of a capital decision. So the
# supplier lives here, in the scheduled caller, where the deployed readers are.
#
# ITS CONTRACT WITH ITSELF: where a reading is unavailable it returns
# `ok: False` and NAMES the missing input. It never substitutes a value, and it
# never returns a partial set as though it were complete -- `pass_once` would
# then decide on inputs nobody read.
#
# THE DEFERRED EXIT IS THE POINT. `hold_ranking` carries a DIRECT_EXIT candidate
# built from the selection `manage` deferred, at the price and quantity `manage`
# chose and on `select_exit`'s own expected-net figure. That is what makes the
# comparison real: without it the ranking would compare the hedge against a HOLD
# alone and the exit would already have been sent.

R_NO_DEFERRED_SELECTION = "NO_MANAGEMENT_SELECTION_FOR_THIS_POSITION"
R_NO_HEDGE_CANDIDATE_READER = "NO_SECOND_LEG_CANDIDATE_READER_IS_WIRED"
R_NO_REGION_PROBABILITY_SOURCE = "NO_REGION_PROBABILITY_SOURCE_IS_WIRED"
R_ACTION_NOT_DISPATCHABLE_HERE = "THIS_LANE_HAS_NO_ORDER_FOR_THAT_ACTION"
R_NO_EXECUTABLE_PLAN = "NO_EXECUTABLE_PLAN_WAS_DEFERRED_FOR_THAT_ACTION"
#: TAKE_COMPLEMENT on a one-signed-net venue is a REDUCE through the other
#: ladder, not a separate holding (`bettor_venue_position_model`).
R_TAKE_COMPLEMENT_NETS = "ON_PMUS_BUYING_THE_OTHER_SIDE_NETS_THE_POSITION_SEE_REDUCE"
#: The selector's own churn rule, applied to exits it did not choose.
R_BELOW_MIN_IMPROVEMENT = "IMPROVES_ON_HOLD_BY_LESS_THAN_THE_DECLARED_MINIMUM"


def _MIN_IMPROVEMENT_PER_CONTRACT() -> float:
    """`bettor_mgmt_select`'s declared minimum, read from its source of truth."""
    from .. import bettor_mgmt_select as _MS
    return float(_MS.MIN_IMPROVEMENT_USD_PER_CONTRACT)

#: The inputs a pairing decision needs, and which of them this lane can read
#: today. Declared rather than discovered so the gap is a list, not a surprise.
PAIR_INPUT_READINESS = {
    "held_leg": "READ -- bettor_funded_book.open_entry_positions",
    "hold_ranking.HOLD": "READ -- bettor_funded_management.select_exit",
    "hold_ranking.DIRECT_EXIT": ("READ -- the selection manage deferred, at its "
                                 "own price, quantity and expected net"),
    "hold_ranking.REDUCE": ("READ where select_exit ranked one; absent "
                            "otherwise, and absent is not zero"),
    "fee_usd": "READ -- bettor_fee_schedule.LATEST",
    "candidate_legs": ("NOT WIRED -- needs a venue catalogue read for the "
                       "complementary contracts on the same fixture"),
    "region_probabilities": ("NOT WIRED -- needs the region probability source "
                            "with its own stated evidence quality"),
    "depth": ("NOT WIRED -- needs the venue ladder read at the hedge's own "
              "limit price"),
}


def _exit_value_from(sel: dict):
    """The exit's value, from the selector's OWN candidate for that action.

    THE DEFECT THIS FIXES, AND IT IS A LOSS-CONTAINMENT ONE. I read
    `expected_net_usd` off the SELECTION, which `select_exit` does not always
    populate there -- it lives on the matching candidate inside the selection's
    `ranking`. Absent, the exit joined the combined ranking UNSCORED, and an
    unscored candidate cannot win: the lane chose HOLD over an exit it had
    already decided was right, silently, on every position.

    An unscored exit losing to HOLD looks exactly like a considered decision to
    hold. That is the worst shape a defect of this kind can take, so the value
    is taken from the candidate that carries it and the fallbacks are explicit.
    """
    action = str(sel.get("selected") or "DIRECT_EXIT")
    for cand in ((sel.get("ranking") or {}).get("candidates") or []):
        if str(cand.get("action")) == action:
            for field in ("value_usd", "expected_net_usd", "slice_value_usd"):
                if cand.get(field) is not None:
                    return float(cand[field]), "selector_candidate.%s" % field
    if sel.get("expected_net_usd") is not None:
        return float(sel["expected_net_usd"]), "selection.expected_net_usd"
    return None, "NOT_IDENTIFIED"


def _exit_candidate_from(sel: dict, *, residual) -> dict | None:
    """The deferred exit, in `rank_with_hold`'s candidate shape.

    NOTHING IS RECOMPUTED. The price, the quantity and the expected net are
    `select_exit`'s own; re-deriving any of them here would put a second opinion
    of the same number into the ranking, and the decision would then be made on
    a figure the order was not priced at.

    `expected_net_usd` absent means the selector did not produce one, and the
    candidate is UNSCORED rather than assumed zero -- `bettor_funded_decision`
    already keeps unscored candidates apart from scored ones for exactly this.
    """
    if not sel:
        return None
    qty = sel.get("selected_qty")
    if qty is None:
        return None
    net, net_source = _exit_value_from(sel)
    action = str(sel.get("selected") or "DIRECT_EXIT")
    return {
        "action": action,
        "qty": float(qty),
        "value_usd": None if net is None else float(net),
        "expected_net_usd": None if net is None else float(net),
        # THE EXIT'S DOWNSIDE IS ITS PROCEEDS: the position is gone once it
        # fills, so the worst case and the expected case coincide.
        "downside_usd": None if net is None else float(net),
        "incremental_capital_usd": 0.0,
        "capital_duration_h": 0.0,
        "evidence_quality": FD_EVIDENCE_VENUE_IMPLIED,
        "execution_secured": False,
        "limit_price": sel.get("limit_price"),
        "proceeds_per_contract": sel.get("proceeds_per_contract"),
        "from_deferred_selection": True,
        "value_source": net_source,
        "residual_at_selection": residual,
    }


FD_EVIDENCE_VENUE_IMPLIED = "VENUE_IMPLIED"


R_NO_HOLD_PROBABILITY = "HOLD_STATES_NO_PROBABILITY_TO_PRICE_THE_ACQUISITION_ON"
R_HOLD_PROBABILITY_RECORD_DISAGREES = (
    "HOLDS_PROBABILITY_DISAGREES_WITH_THE_RECORD_MANAGEMENT_KEPT")


def _primary_probability_for(hold_ranking, record) -> dict:
    """THE PROBABILITY THE HOLD CANDIDATE IS VALUED ON, with its source, or a
    named refusal. Pure.

    Read from the HOLD candidate's `value_per_contract` -- the number
    `rank_with_hold` valued HOLD on -- and, where `manage` kept the
    `ev_hold` record it came from, checked against that record's probability.
    A HOLD with no stated probability, or one whose record disagrees, supplies
    nothing: the acquisition is then not priced at all rather than priced on a
    different marginal from HOLD's.
    """
    hold = next((c for c in (hold_ranking or {}).get("candidates") or ()
                 if str((c or {}).get("action")) == "HOLD"), None)
    p = None if hold is None else hold.get("value_per_contract")
    try:
        p = None if p is None or isinstance(p, bool) else float(p)
    except (TypeError, ValueError):
        p = None
    if p is None or not (0.0 <= p <= 1.0):
        return {"ok": False, "refusal": R_NO_HOLD_PROBABILITY,
                "why": ("no priced HOLD candidate states the probability it "
                        "was valued on (value_per_contract)")}
    rec = dict(record or {})
    rp = rec.get("probability")
    try:
        rp_f = None if rp is None or isinstance(rp, bool) else float(rp)
    except (TypeError, ValueError):
        rp_f = float("nan")
    if rp is not None and not (rp_f is not None and abs(rp_f - p) <= 1e-12):
        return {"ok": False, "refusal": R_HOLD_PROBABILITY_RECORD_DISAGREES,
                "hold_candidate_probability": p, "record_probability": rp,
                "why": ("the HOLD candidate is valued on %r and the ev_hold "
                        "record management kept says %r" % (p, rp))}
    src = dict(rec.get("source") or {})
    return {"ok": True, "refusal": None, "probability": p,
            "source": {
                "from": "hold_ranking.HOLD.value_per_contract",
                "is": ("the probability HOLD, DIRECT_EXIT and REDUCE are "
                       "valued on"),
                "record_checked": rp is not None,
                "valuation_row_id": rec.get("source_row_id"),
                "provider": src.get("provider"),
                "book": src.get("book"),
                "devig_method": src.get("devig_method"),
                "version": src.get("version"),
                "probability_event": rec.get("probability_event"),
                "payout_event_held": rec.get("payout_event_held")}}


def _model_inputs_for(*, held, candidates) -> dict:
    """The feature inputs `predict_for` needs, from the built legs.

    `features_of` is a function of the STRUCTURE plus each side's cost and the
    overtime treatment, so the two costs come from the legs themselves -- the
    held leg's recorded basis and the candidate's own quoted price -- rather
    than from one shared number. `overtime_included` is the treatment the legs
    AGREE on, which they must, since a differing one would have kept them out
    of the same grading key and out of the same structure.
    """
    from .. import bettor_indirect_structures as _IS

    hl = (held or {}).get("leg")
    if hl is None or not candidates:
        return {}
    best = candidates[0]
    cl = best.get("leg")
    return {"primary_cost_cents": getattr(hl, "cost_cents_per_unit", None),
            "hedge_cost_cents": getattr(cl, "cost_cents_per_unit", None),
            "overtime_included": (
                getattr(hl, "overtime", None) == _IS.OT_INCLUDED),
            "from_the_built_legs": True}


async def _venue_prose(slug):
    """The venue's own settlement prose for one contract, paced and cached.

    A thin async wrapper over the reader that has been on the entry path since
    the rules-text work, so the funded lane reads the SAME text the entry lane
    does rather than a second, divergent copy of the same call.

    It carries the requests the read DISPATCHED, counted at the request gate
    under the read's own id (0 for a cached answer), so a caller that bounds
    its venue usage can report what it spent rather than estimate it.
    """
    from .. import bettor_pair_observations as _PO

    got, sent = await asyncio.to_thread(_PO.metered,
                                        _read_venue_rules_blocking, slug)
    got = dict(got or {})
    got["dispatches"] = 0 if got.get("from_cache") else sent
    return got


def _clean_family(sports_type):
    """The sport family from a venue sports_type, for the fee coefficient."""
    return str(sports_type or "").split("_")[0].strip().lower() or None


async def _fee_for_candidates(legs, *, at, sport=None,
                              wanted_qty=None) -> dict:
    """ONE TAKER FEE READING for an acquisition of this size, or None.

    Priced on the schedule in force at the CYCLE'S date and with the SPORT'S
    own coefficient, because the fee module says in its own source that doing
    otherwise is a defect. It never raises: a fee that cannot be priced is a
    None the ranking refuses on, not an exception on the decision path.
    """
    import datetime as _dt

    out = {"fee_usd": None, "fee_basis": None, "sport": sport,
           "why": None}
    rows = [dict(c) for c in (legs or []) if (c or {}).get("price") is not None]
    if not rows:
        out["why"] = ("no priced candidate to price a fee for")
        return out
    try:
        from .. import bettor_fee_schedule as FS
        from .. import calibration_fees as CF

        when = _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc)
        sched = FS.for_date(when.date().isoformat())
        theta = CF.taker_coefficient(sport, at=when.isoformat())
        # THE LARGEST CANDIDATE'S OWN PRICE AND SIZE. A single reading is used
        # for the comparison, so it must not flatter the cheapest candidate:
        # taking the most expensive one keeps the shared reading conservative.
        candidate_fees = {}
        for row in rows:
            available = row.get("depth_qty")
            qty_for_row = min(float(wanted_qty or 0), float(available or 0))
            if qty_for_row <= 0 or not qty_for_row.is_integer():
                continue
            candidate_fees[row.get("candidate_id") or row["market_slug"]] = {
                "fee_usd": float(sched.exact(theta, int(qty_for_row), float(row["price"]))),
                "fee_basis": "DATED_SCHEDULE_AT_THIS_CANDIDATES_PRICE_AND_PROPOSED_QUANTITY",
                "fee_quantity": qty_for_row}
        out["candidate_fees"] = candidate_fees
        worst = max(rows, key=lambda r: float(r.get("price") or 0.0))
        # THE QUANTITY WE WOULD ACQUIRE, NOT THE WHOLE DISPLAYED DEPTH. Sizing
        # the fee on `depth_qty` priced 500 contracts when 10 were wanted and
        # produced a $7.30 fee on a $3 hedge -- which would have made every
        # candidate look unaffordable. Displayed depth is what is AVAILABLE; the
        # fee is charged on what is bought.
        qty = int(float(wanted_qty or 0) or 1)
        fee = sched.exact(theta, qty, float(worst["price"]))
        out.update(fee_usd=float(fee),
                   fee_basis=("%s taker fee at theta %s for %d contracts at "
                              "%s, schedule in force on %s"
                              % (sched.describe().get("name", "PMUS"), theta,
                                 qty, worst["price"],
                                 when.date().isoformat())),
                   theta=str(theta), schedule_date=when.date().isoformat(),
                   priced_on=worst.get("market_slug"))
    except Exception as exc:                                    # noqa: BLE001
        out["why"] = ("the fee could not be priced (%s), so no candidate is "
                      "scored on an assumed fee" % type(exc).__name__)
    return out


R_NO_ADMISSIBLE_HEDGE_CANDIDATE = (
    "NO_SIBLING_CONTRACT_ON_THIS_FIXTURE_COULD_BE_BUILT_INTO_A_LEG")


async def _candidate_quote(conn, slug, side, *, now=None) -> dict:
    """ONE CANDIDATE CONTRACT'S OWN PRICE AND DEPTH.

    It goes through `venue_quote`, which is the same paced acquisition-ladder
    read the entry lane prices with, so a hedge candidate is costed exactly the
    way an entry is. A candidate whose currency is not established REFUSES
    there and arrives here without a price, and `candidate_legs_for` then
    refuses the candidate rather than pricing it off the held contract's book.

    LONG consumes the ask; SHORT consumes the bid at the complementary cost.
    Depth is restricted to levels at the quoted cost, not the entire ladder --
    and the ladder itself to the levels an order we can send is able to reach
    (`bettor_book_snapshot.restrict_to_executable`, applied in `venue_quote`).
    """
    from .. import bettor_book_snapshot as bs

    try:
        if side not in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
            return {"ok": False, "refusal": "HEDGE_SIDE_NOT_IDENTIFIED"}
        # THROUGH THE ONE FRESHNESS SEAM, per contract, as the entry lane
        # reads its quote (integration, XC). This called `venue_quote` with no
        # mechanism at all, so a hedge book could never be admitted even when
        # `book_currency_evidence` established the market's currency -- the
        # hedge bypassed the seam both lanes are documented to share. Today
        # the seam supplies no mechanism and the refusal is unchanged.
        cev = book_currency_evidence(slug)
        got = await venue_quote(conn, us_slug=slug, intent=side, now=now,
                                subscription=cev.get("subscription"),
                                revalidation=cev.get("revalidation"))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__,
                "why": ("the candidate's own ladder read failed; the candidate "
                        "is refused rather than priced from another book")}
    got = dict(got or {})
    if not got.get("ok"):
        return got
    lad = got.get("acquisition_ladder") or {}
    levels = lad.get("levels") or []
    if not levels:
        return dict(got, ok=False, refusal="HEDGE_PRICE_LEVEL_NOT_IDENTIFIED")
    # THE LADDER IS ALREADY RESTRICTED TO THE EXECUTABLE GRID (`venue_quote`).
    # Asserted, not trusted: a quote whose ladder did not pass through the
    # grid, or whose best level is off it, is refused by name rather than
    # priced -- this price becomes the leg's cost and the plan's wire limit.
    grid = lad.get("executable_grid") or got.get("executable_grid")
    if not lad.get("executable_grid_applied") or not (grid or {}).get("ok") \
            or not bs.on_executable_grid(levels[0].get("api_price"), grid):
        return dict(got, ok=False,
                    refusal=bs.R_LIMIT_OFF_THE_EXECUTABLE_GRID,
                    why=("the candidate's best level %r is not established "
                         "on the executable grid %r"
                         % (levels[0].get("api_price"),
                            (grid or {}).get("step"))))
    price = levels[0].get("acquisition_price")
    depth = sum(float(r["qty"]) for r in levels if r.get("acquisition_price") == price)
    currency = got.get("book_currency") or {}
    established = currency.get("book_state_established_at_epoch_s")
    bound = currency.get("bound_s")
    received = got.get("read_at")
    if None in (established, bound, received):
        return dict(got, ok=False, refusal="HEDGE_EVIDENCE_EXPIRY_NOT_ESTABLISHED")
    expiry = min(float(established) + float(bound),
                 float(received) + MAX_OUR_PROCESSING_DELAY_S)
    return dict(got, price=price, cost_per_share=price,
                api_price=levels[0].get("api_price"), depth_qty=depth,
                # THE LEVELS THIS PRICE AND DEPTH COUNT, so the plan built on
                # them can prove its limit reaches every one.
                counted_levels=[{"api_price": r.get("api_price"),
                                 "acquisition_price": r.get(
                                     "acquisition_price"),
                                 "qty": r.get("qty")} for r in levels
                                if r.get("acquisition_price") == price],
                inputs_expire_at=expiry)


async def observation_quote(slug, side, *, now=None) -> dict:
    """ONE CONTRACT'S DISPLAYED ACQUISITION PRICE, FOR THE OBSERVATION LEDGER.

    NOT AN ORDER PRICE. `venue_quote` refuses a book whose currency is not
    established, which is right for anything that could become an order. An
    observation records what the book displayed and HOW CURRENT that was --
    the verdict travels on the result as `book_currency`, and
    `usable_for_orders` is True only when it is ESTABLISHED. Nothing reads
    this for a trade: it prices features for a record labelled later from
    the venue's own settlement.
    """
    from .. import bettor_book_snapshot as bs

    if side not in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
        return {"ok": False, "refusal": "OBSERVED_SIDE_NOT_IDENTIFIED",
                "dispatches": 0}
    # ONE BOOK PER CONTRACT PER PASS. Both sides of an instrument are read
    # off ONE book -- LONG consumes the offers, SHORT the bids -- so within an
    # observation pass the second side reuses the payload the first side's
    # read returned, with that read's own receipt instant and HTTP metadata:
    # the currency verdict below is evaluated at the time it is used, so the
    # reused book is judged older, never fresher.
    cache = _OBS_BOOK_CACHE.get()
    hit = cache.get(str(slug)) if cache is not None else None
    if hit is not None:
        book, read_at, sent, reused = hit["book"], hit["read_at"], 0, True
    else:
        try:
            book = await asyncio.wait_for(
                asyncio.to_thread(_read_book_blocking, str(slug)),
                timeout=VENUE_TIMEOUT_S)
        except Exception as exc:                                # noqa: BLE001
            return {"ok": False, "refusal": R_VENUE_READ_FAILED,
                    "error": type(exc).__name__, "dispatches": None}
        sent, reused = book.get("attempts"), False
        if book.get("error"):
            return {"ok": False, "refusal": R_VENUE_READ_ERROR,
                    "dispatches": sent}
        read_at = time.time()
        if cache is not None:
            cache[str(slug)] = {"book": book, "read_at": read_at}
    lad = bs.acquisition_ladder(book.get("marketData"), intent=side)
    if not lad.get("ok") or not (lad.get("levels") or []):
        return {"ok": False, "refusal": lad.get("refusal") or R_NO_DEPTH,
                "dispatches": sent, "book_from_pass_cache": reused}
    snap = bs.snapshot(book.get("marketData"), symbol=str(slug),
                       captured_at=read_at)
    vt = None
    ts = snap.get("TRANSACT_TIME")
    if ts not in (None, bs.NOT_IDENTIFIED):
        dt = _stream_parse_ts(ts)
        vt = None if dt is None else dt.timestamp()
    # THE VERDICT INSTANT IS NEVER BEFORE THE READ. A pass hands every read
    # the instant it began; judging a book received minutes later at that
    # instant understates its age (review of 599076c). A book reused within
    # the pass is judged at the instant it is used.
    currency = vc.evaluate(now=max(float(now if now is not None else read_at),
                                   read_at, time.time() if reused else read_at),
                           observation=book.get("http_observation"),
                           subscription=None, revalidation=None, venue_ts=vt,
                           our_receipt_at=read_at,
                           bound_s=MAX_VENUE_QUOTE_AGE_S)
    levels = lad["levels"]
    price = levels[0].get("acquisition_price")
    return {"ok": price is not None, "price": price, "cost_per_share": price,
            "depth_qty": sum(float(r["qty"]) for r in levels
                             if r.get("acquisition_price") == price),
            "read_at": read_at, "book_currency": currency,
            "usable_for_orders": currency.get("verdict") == vc.ESTABLISHED,
            "dispatches": sent, "book_from_pass_cache": reused,
            "what_this_is": "A DISPLAYED PRICE FOR AN OBSERVATION, NOT AN "
                            "ORDER PRICE"}


#: The observer's per-pass book cache. Set by `_pair_observation_pass` for the
#: duration of one pass and read only by `observation_quote`; unset (None)
#: everywhere else, so no other caller ever sees a reused book.
_OBS_BOOK_CACHE: contextvars.ContextVar = contextvars.ContextVar(
    "ext_pinnacle_observation_book_cache", default=None)
#: When the observer last ran in this process, and the least interval between
#: passes: a BLOCKED cycle repeats every IDLE_POLL_S, and the observer's venue
#: reads must not repeat with it.
_LAST_OBSERVATION_PASS = [0.0]
OBSERVATION_MIN_INTERVAL_S = 840.0
R_OBSERVER_STOPPED_WITH_THE_LANE = (
    "THE_LANE_IS_STOPPED_SO_THE_OBSERVER_MAKES_NO_VENUE_READS")
R_OBSERVATION_NOT_DUE = "THE_LAST_OBSERVATION_PASS_WAS_TOO_RECENT"


#: THE COLLECTION PASS LIMIT'S HARD BOUNDS. The registry's ACTIVE version
#: (agent DEREK, key collection.pass_limit -- the pre-authorized
#: COLLECTION_PASS_LIMIT change class, released and rolled back only by
#: agents.improvement under its acceptance and canary rules) chooses a value
#: INSIDE these bounds; outside them, or unreadable, the code constant applies.
COLLECTION_PASS_LIMIT_BOUNDS = (1, 10)


async def _collection_pass_limit(conn) -> dict:
    """How many candidates this pass attempts, and where that number came
    from. Never raises; the code default is labelled as such."""
    from .. import bettor_pair_observations as _PO
    default = int(_PO.CANDIDATES_PER_PASS)
    out = {"candidates_per_pass": default, "source": "CODE_DEFAULT",
           "version": None, "bounds": list(COLLECTION_PASS_LIMIT_BOUNDS)}
    try:
        from ..agents import registry as _REG
        got = await _REG.active_policy(
            conn, "DEREK", "collection.pass_limit",
            default={"candidates_per_pass": default})
        v = (got.get("params") or {}).get("candidates_per_pass")
        lo, hi = COLLECTION_PASS_LIMIT_BOUNDS
        if got.get("source") != "CODE_DEFAULT" and isinstance(v, int) \
                and not isinstance(v, bool) and lo <= v <= hi:
            out.update(candidates_per_pass=v, source=got.get("source"),
                       version=got.get("version"))
        elif got.get("source") != "CODE_DEFAULT":
            out["refused_value"] = v
            out["why"] = "the ACTIVE value is outside the hard bounds"
    except Exception as exc:                                    # noqa: BLE001
        out["why"] = "policy read failed: %s" % type(exc).__name__
    return out


async def _pair_observation_pass(conn, observable, *, now) -> dict:
    """THE NON-FUNDED PAIR OBSERVER, once per LIVE cycle, never fatal.

    See `bettor_pair_observations`: it records the pairing structures on a few
    contracts -- this cycle's mapped identities AND fixtures taken straight
    from the venue's own catalogue, so the observer does not starve when the
    entry lane admits nothing -- labels older observations from the venue's
    own settlements, offers the registry a CANDIDATE fit on them, and scores
    the observation-sourced candidates. It holds, reserves and sends nothing,
    and promotes nothing.
    """
    _LAST_OBSERVATION_PASS[0] = float(now)
    try:
        from .. import bettor_pair_observations as _PO

        catalogue = await _PO.catalogue_candidates(conn, now=now)
        pass_limit = await _collection_pass_limit(conn)
        token = _OBS_BOOK_CACHE.set({})
        try:
            got = await _PO.observation_pass(
                conn, candidates=observable,
                catalogue=catalogue.get("candidates") or [],
                quoter=lambda slug, side: observation_quote(slug, side,
                                                            now=now),
                prose_reader=_venue_prose, now=now,
                per_pass=pass_limit["candidates_per_pass"])
        finally:
            _OBS_BOOK_CACHE.reset(token)
        return dict(got, catalogue={k: v for k, v in catalogue.items()
                                    if k != "candidates"},
                    pass_limit=pass_limit)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": "PAIR_OBSERVATION_PASS_RAISED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


async def _observation_when_entry_is_blocked(conn, *, now: float,
                                             blocked_by: str) -> dict:
    """THE OBSERVER, WHEN THE ENTRY LANE CANNOT RUN.

    A missing odds credential or an unmigrated shadow table stops the ENTRY
    lane, and neither has anything to do with observing two venue contracts
    and reading their settlements. So the observer runs here too -- from the
    catalogue alone -- at most once per OBSERVATION_MIN_INTERVAL_S, because
    a blocked cycle repeats every IDLE_POLL_S. It does NOT run when the lane
    is STOPPED: that switch stops the lane's venue reads, observations
    included."""
    last = _LAST_OBSERVATION_PASS[0]
    if last and float(now) - last < OBSERVATION_MIN_INTERVAL_S:
        return {"ran": False, "why": R_OBSERVATION_NOT_DUE,
                "lane_state": "BLOCKED:%s" % blocked_by,
                "last_pass_age_s": round(float(now) - last, 1),
                "next_due_in_s": round(OBSERVATION_MIN_INTERVAL_S
                                       - (float(now) - last), 1)}
    got = await _pair_observation_pass(conn, [], now=now)
    return dict(got, ran=True, lane_state="BLOCKED:%s" % blocked_by)


async def _registry_state(conn, model_key=None) -> dict:
    """IS A MODEL APPROVED FOR A PAIRING KEY. A read, not a substitute.

    Recorded on the payload so a cycle that produced no hedge says WHICH
    dependency was absent. An empty registry is a missing evidence dependency
    to work through, and naming it on every cycle is how it stays visible
    instead of becoming the permanent shape of the lane.
    """
    from .. import bettor_funded_model as FMD

    key = model_key or FMD.KEY_MIDDLE
    out = {"model_key": key, "approved": False, "refusal": None,
           "promotion_bar": {"min_rows": FMD.MIN_EVALUATION_ROWS,
                             "min_margin_vs_incumbent": FMD.MIN_SKILL_MARGIN},
           "asked_by": ("the pair cycle, per structure, via "
                        "bettor_funded_model.predict_distribution (a primary "
                        "probability supplied) or predict_for (legacy) -- not "
                        "here")}
    try:
        got = await FMD.approved(conn, model_key=key)
    except Exception as exc:                                    # noqa: BLE001
        out["refusal"] = "REGISTRY_READ_RAISED_" + type(exc).__name__
        return out
    if got.get("ok"):
        mdl = dict(got.get("model") or {})
        out.update(approved=True,
                   model_version=mdl.get("model_version"),
                   model_id=mdl.get("model_id"))
        return out
    out["refusal"] = got.get("refusal") or FMD.R_NO_APPROVED_MODEL
    out["why"] = got.get("why")
    return out


async def funded_pair_inputs(conn, pos, *, at, deferred=None,
                             account_id=None, venue=None,
                             management_rankings=None,
                             prose_reader=None, quoter=None):
    """The pairing facts for one held position, or a named missing input.

    Signature matches what `pass_once` calls: `(conn, pos, at=)`. `deferred` is
    bound by `_funded_service` with `functools.partial`, so the supplier is a
    plain callable to the pass and carries no hidden state.

    `prose_reader` and `quoter` default to the PRODUCTION venue reads and are
    overridable so a test can substitute the TRANSPORT while the supplier, the
    builder and every reader under test still run. That is the only thing they
    are for: a test that passed a finished `Leg` or a finished ranking would be
    testing nothing.
    """
    from .. import bettor_funded_decision as FD
    from .. import bettor_funded_hedge_supply as HSUP
    from .. import bettor_funded_model as FMD

    out: dict = {"ok": False, "readiness": dict(PAIR_INPUT_READINESS)}
    intent_id = str(pos.get("intent_id") or "")
    sel = dict(deferred or {}).get(intent_id)
    # ── POSITION EVIDENCE DOES NOT DEPEND ON THE OLD SELECTOR'S CHOICE ──
    #
    # THE DEFECT, AND IT DEFEATED THE WHOLE POINT OF THE REORDER. This returned
    # R_NO_DEFERRED_SELECTION whenever `manage` had no deferred exit -- which is
    # exactly what happens when the original selector chose HOLD. So the case
    # that matters most for pairing (HOLD beats selling, and acquiring a hedge
    # beats HOLD) could never be considered: the supplier refused before the
    # hedge was looked at.
    #
    # An absent exit plan means ONE candidate is missing, not that the position
    # has no evidence. The HOLD the selector priced is still a candidate, the
    # hedge can still be ranked against it, and a hedge that wins can still be
    # dispatched -- its plan comes from the acquisition path, not from `manage`.
    # So the pass proceeds with what exists and NAMES the absent exit plan.
    #
    # A HOLD-ONLY RANKING IS STILL A RANKING. `bettor_funded_decision` requires
    # a priced HOLD and refuses otherwise, so the guard that matters is still
    # there and is not weakened here.
    hold_ranking_source = {}
    if sel:
        # The deferred selection carries the same ranking `manage` recorded; the
        # production record is preferred so both branches read one source.
        _mr = dict(management_rankings or {}).get(intent_id) or {}
        hold_ranking_source = dict(_mr.get("ranking")
                                   or sel.get("ranking") or {})
        out["management_ranking_source"] = (
            "manage.management_rankings" if _mr.get("ranking")
            else "the deferred selection's own ranking")
    else:
        # The selector's own ranking, taken from the position's recorded
        # management selection where one exists. Without it there is no priced
        # HOLD, and the decision module refuses on its own account.
        # THE PRODUCTION RANKING, from `manage`'s own per-position record --
        # not `pos["management_ranking"]`, which nothing ever wrote and which
        # only a test supplied.
        _mr = dict(management_rankings or {}).get(intent_id) or {}
        hold_ranking_source = dict(_mr.get("ranking") or {})
        out["management_ranking_source"] = (
            "bettor_funded_management.manage.management_rankings[%s]"
            % intent_id if _mr else "NONE_RECORDED_FOR_THIS_POSITION")
        out["exit_plan_absent"] = R_NO_DEFERRED_SELECTION
        out["what_that_costs"] = (
            "no exit candidate and no exit order. HOLD and any hedge are still "
            "ranked, and a hedge that wins is still dispatched through the "
            "acquisition path")
    residual = pos.get("residual_qty") or pos.get("filled_qty")
    exit_cand = None
    hold_from_selector = hold_ranking_source
    candidates = []
    # THE SELECTOR'S OWN HOLD, unchanged. Where it did not price one, the
    # ranking's HOLD_NOT_PRICED refusal fires and nothing is selected -- which
    # is the existing guard and is not worked around here.
    # ── ONLY DISPATCHABLE ACTIONS MAY WIN THE RANKING ────────────────
    #
    # THE DEFECT THIS AVOIDS, AND IT FIRED ON THE FIRST RUN. `select_exit`'s
    # ranking carries actions this lane cannot send -- TAKE_COMPLEMENT,
    # POST_COMPLEMENT, MERGE. Passing them through unfiltered, the combined
    # ranking selected TAKE_COMPLEMENT at +1.37, `pass_once` refused it with
    # THAT_IS_NOT_AN_ACTION_THIS_LANE_TAKES, and the EXIT that WAS dispatchable
    # was blocked by an action nothing could execute. A better-scoring action
    # nobody can take is not a reason to take nothing.
    #
    # So a non-dispatchable candidate is carried as NOT RANKABLE with a named
    # blocker: visible in the decision record, unable to win. That is the same
    # rule `bettor_funded_decision` already applies to a limit breach -- removed
    # before the choice rather than out-ranked after it -- and it is NOT a claim
    # that the action is worthless. It is a claim that this lane has no order
    # for it, which is a scope fact and is reported as one.
    from .. import bettor_funded_pair_cycle as _PCD

    # ── AND ONLY AN ACTION WITH AN EXECUTABLE PLAN MAY WIN ───────────
    #
    # THE SECOND HALF OF THE SAME RULE, and the order-binding check found it.
    # `select_exit` VALUES several actions -- DIRECT_EXIT, REDUCE, and the
    # complement family -- but `manage` defers an executable plan for exactly
    # ONE of them: the action it selected, with its price, quantity, proceeds
    # basis and evidence expiry.
    #
    # On the loss-containment fixture the combined ranking selected a REDUCE
    # candidate at 9 contracts while the only plan in hand was a DIRECT_EXIT at
    # 9. The binding refused -- correctly, an order labelled REDUCE carrying an
    # exit payload is the defect -- but the refusal came at the venue boundary,
    # after the decision had been persisted as REDUCE. A candidate that cannot
    # be executed must not win the ranking in the first place.
    #
    # So an action with no executable plan is NOT RANKABLE with its own blocker,
    # exactly like a non-dispatchable one. When a plan exists for REDUCE as well
    # as EXIT, both are rankable and each is bound to its own plan; that is what
    # `plans_by_action` is for and why it is keyed rather than a single slot.
    # ── A COMPLETE PLAN PER ACTION, BUILT BEFORE RANKING ─────────────
    #
    # THE ROOT ERROR THIS REPLACES. The old path ranked a candidate and then
    # reconciled it against a selection fetched by position id, field by field,
    # treating an absent field as agreement -- so a candidate with no wire limit,
    # a malformed price, or another market's slug all passed the "binding".
    #
    # An action is now rankable ONLY IF a complete, immutable ExecutionPlan for
    # it validated first: account, venue, position/intent, instrument, action,
    # quantity, wire limit, valuation basis and evidence validity, each present
    # and usable or the plan refuses at construction. The candidate carries that
    # plan's DIGEST, so binding the winner is an identity check on the object
    # that was ranked rather than a comparison of two partial records.
    # ── A COMPLETE PLAN FOR EVERY EXECUTABLE EXIT, NOT ONLY THE SELECTED ONE ──
    #
    # THE DEFECT THIS CLOSES (Xavier map, decision-flow Q2). A plan was built
    # for the ONE action `manage` selected. So the final ranking held at most one
    # exit: a DIRECT_EXIT the selector ranked below HOLD or REDUCE vanished with
    # no blocker, and when the selector chose REDUCE its own digest-less REDUCE
    # was appended BESIDE the plan-built one and won the stable sort -- binding
    # then refused the winner and a REDUCE could never be dispatched.
    #
    # `select_exit` now prices every evidenced DIRECT_EXIT and REDUCE with the
    # SAME function it prices its choice with (`executable_exit_terms`), so a
    # plan is built for each. The selection `manage` deferred is still the
    # source for its own action (same numbers, one record); the others come from
    # the terms. A candidate whose terms refused, or whose plan refused, is NOT
    # RANKABLE with that exact refusal -- never dropped, never ranked.
    plans_by_action = {}
    plan_refusals = []
    exit_sources = {}
    if sel and sel.get("selected") is not None:
        try:
            _plan = _PCD.plan_for(
                action=str(sel.get("selected")), selection=sel,
                account_id=account_id, venue=venue, position=pos)
            plans_by_action[_plan.action] = _plan
            exit_sources[_plan.action] = dict(sel)
        except _PCD.PlanRefused as exc:
            plan_refusals.append(dict(exc.as_dict(),
                                      action=str(sel.get("selected"))))
    _terms_by_action = dict((_mr or {}).get("executable_exit_terms") or {})
    for _act, _t in sorted(_terms_by_action.items()):
        if _act in plans_by_action or _act not in ("DIRECT_EXIT", "REDUCE"):
            continue
        if not (_t or {}).get("ok"):
            plan_refusals.append({"ok": False, "action": _act,
                                  "refusal": (_t or {}).get("refusal"),
                                  "why": "the selector could not bound this "
                                         "action's order: %s"
                                         % ((_t or {}).get("refusal"),)})
            continue
        _src = dict(_t, intent_id=intent_id, selected=_act,
                    us_market_slug=pos.get("us_market_slug"))
        try:
            _plan = _PCD.plan_for(action=_act, selection=_src,
                                  account_id=account_id, venue=venue,
                                  position=pos)
            plans_by_action[_plan.action] = _plan
            exit_sources[_plan.action] = _src
        except _PCD.PlanRefused as exc:
            plan_refusals.append(dict(exc.as_dict(), action=_act))
    out["executable_plans"] = {a: pl.as_dict()
                               for a, pl in plans_by_action.items()}
    # THE PLAN OBJECTS THEMSELVES, for the binding. The dicts above are for the
    # report; the binding needs the immutable object that was ranked, because a
    # dict can be edited between the ranking and the send and an ExecutionPlan
    # cannot.
    out["executable_plans_by_action"] = dict(plans_by_action)
    # AND BY DIGEST: a group decision can hold the same action for two legs,
    # and the digest -- not the action name -- is what names one order.
    out["executable_plans_by_digest"] = {pl.digest: pl for pl in
                                         plans_by_action.values()}
    out["plan_refusals"] = plan_refusals
    _refusal_by_action = {r.get("action"): r for r in plan_refusals}

    # THE POSITION'S BASIS AND HOLD'S PER-CONTRACT VALUE, for the worst cases.
    _basis = (_mr or {}).get("basis") or {}
    _basis_per = _basis.get("basis_per_contract")
    _hold_cand = next((c for c in (hold_from_selector.get("candidates") or [])
                       if str(c.get("action")) == "HOLD"), None)
    _hold_value = (None if _hold_cand is None else _hold_cand.get("value_usd"))

    dispatchable = set(_PCD.DISPATCHABLE) | {"HOLD"}
    # ── MANAGEMENT'S OWN BLOCKERS REACH THE RANKING ─────────────────────
    #
    # THE DEFECT THIS CLOSES (Xavier map Q4). `select_exit` puts its
    # `not_rankable` at the TOP level, outside the `ranking` projection, and this
    # read only the projection -- so HOLD_TO_SETTLEMENT, POST_COMPLEMENT, MERGE,
    # NO_BID and the rest never reached the persisted decision. Both sources are
    # read now, without duplicates.
    not_rankable = []
    _seen_blocks = set()
    for _b in (list(hold_from_selector.get("not_rankable") or [])
               + list((_mr or {}).get("not_rankable") or [])):
        _key = (str(_b.get("action")), str(_b.get("blocker")))
        if _key in _seen_blocks:
            continue
        _seen_blocks.add(_key)
        not_rankable.append(dict(_b))
    # A SELECTOR THAT REFUSED BEFORE RANKING still names why for every action
    # the position could have taken, so the record shows a blocker per action
    # rather than an empty list.
    if (_mr or {}).get("refusal") and not (
            hold_from_selector.get("candidates") or []):
        for _act in ("HOLD", "DIRECT_EXIT", "REDUCE"):
            if (_act, str(_mr.get("refusal"))) in _seen_blocks:
                continue
            not_rankable.append({
                "action": _act, "blocker": _mr.get("refusal"),
                "value_usd": None,
                "why": ("bettor_funded_management.select_exit refused before "
                        "any action was ranked: %s" % _mr.get("refusal"))})
    for cand in (hold_from_selector.get("candidates") or []):
        action = str(cand.get("action"))
        if action in ("DIRECT_EXIT", "REDUCE"):
            if action in plans_by_action:
                continue          # replaced by the plan-built candidate below
            _r = _refusal_by_action.get(action) or {}
            not_rankable.append(dict(
                cand, value_usd=None,
                blocker=_r.get("refusal") or R_NO_EXECUTABLE_PLAN,
                why=("scored %s by the selector and no executable plan could "
                     "be built for it (%s). Winning without a plan would "
                     "persist a decision nothing can carry out"
                     % (cand.get("value_usd"),
                        _r.get("why") or "no terms were supplied"))))
            continue
        if action == "TAKE_COMPLEMENT" and not cand.get("creates_second_leg"):
            # ON PMUS BUYING THE OTHER SIDE IS NETTING, which is a REDUCE of the
            # held position through the other ladder -- not a separate holding
            # and not a second order route (`bettor_venue_position_model`).
            not_rankable.append(dict(
                cand, value_usd=None, blocker=R_TAKE_COMPLEMENT_NETS,
                why=("scored %s by the selector. On a one-signed-net venue "
                     "buying the other side of the held instrument nets the "
                     "position: its economics are DIRECT_EXIT/REDUCE's on the "
                     "other ladder, and it is not a separate holding"
                     % (cand.get("value_usd"),))))
            continue
        if action != "HOLD" and action not in plans_by_action:
            not_rankable.append(dict(
                cand, value_usd=None, blocker=R_NO_EXECUTABLE_PLAN,
                why=("scored %s by the selector and no executable plan was "
                     "deferred for it: management produced a priced, bounded "
                     "order for %r only. Winning without a plan would persist "
                     "a decision nothing can carry out, which is worse than "
                     "not ranking it"
                     % (cand.get("value_usd"),
                        (sel or {}).get("selected")))))
            continue
        if action not in dispatchable:
            not_rankable.append(dict(
                cand, value_usd=None, blocker=R_ACTION_NOT_DISPATCHABLE_HERE,
                why=("scored %s by the selector and this lane has no order for "
                     "it: %s. It is removed before the choice rather than "
                     "out-ranked after it, so it cannot block an action that "
                     "can be sent" % (cand.get("value_usd"),
                                      sorted(dispatchable)))))
            continue
        _c = dict(cand)
        if action == "HOLD" and _basis_per is not None and residual:
            # HOLD'S TRUE WORST CASE: the position loses and the remaining
            # basis is gone.
            _c.setdefault("worst_case_net_usd",
                          round(-float(_basis_per) * float(residual), 6))
        _c.setdefault("intent_id", intent_id)
        candidates.append(_c)
    # THE EXIT CANDIDATE IS BUILT FROM ITS PLAN, not from the raw selection, so
    # a candidate cannot exist for an action whose plan did not validate.
    _sel_cands = {str(c.get("action")): c
                  for c in (hold_from_selector.get("candidates") or [])}
    for _action, _pl in plans_by_action.items():
        _src = exit_sources.get(_action) or {}
        _c = _exit_candidate_from(dict(_src, ranking=hold_from_selector)
                                  if not _src.get("ranking") else _src,
                                  residual=residual)
        if _c is None:
            continue
        _c["action"] = _action
        _c["qty"] = _pl.quantity
        _c["limit_price"] = _pl.limit_price
        _c["proceeds_per_contract"] = _pl.proceeds_per_contract
        _c["inputs_expire_at"] = _pl.inputs_expire_at
        _c["plan_digest"] = _pl.digest
        _c["intent_id"] = intent_id
        _c["from_deferred_selection"] = bool(sel) and \
            str(sel.get("selected")) == _action
        # THE SELECTOR'S OWN FIGURES FOR THIS ACTION, carried (never
        # recomputed): proceeds net of fees, fees, what stays held.
        _sc = _sel_cands.get(_action) or {}
        for _k in ("slice_value_usd", "retained_value_usd", "fees_usd",
                   "cash_now_usd", "remaining_exposure_qty", "locks_a_loss",
                   "depth_limited"):
            if _sc.get(_k) is not None:
                _c[_k] = _sc[_k]
        # ── THE TRUE WORST CASE, not the expectation ────────────────────
        # A depth-limited exit keeps inventory, and that inventory can LOSE:
        # worst = the realised slice + the retained contracts losing their
        # basis. Where the selector's slice is not available the previous
        # figure stands, which is never less conservative than this one.
        _kept = _sc.get("remaining_exposure_qty")
        if _sc.get("slice_value_usd") is not None and _kept is not None \
                and _basis_per is not None:
            _wc = round(float(_sc["slice_value_usd"])
                        - float(_basis_per) * float(_kept), 6)
            _c["worst_case_net_usd"] = _wc
            _c["downside_usd"] = _wc
        # ── THE CHURN GATE THE SELECTOR APPLIES, APPLIED HERE TOO ───────
        #
        # `rank_with_hold` refuses to act for less than
        # MIN_IMPROVEMENT_USD_PER_CONTRACT over HOLD. Offering an exit the
        # selector declined on that rule to a ranking WITHOUT it would loosen
        # the selection policy; it is carried NOT RANKABLE with the rule named.
        if _hold_value is not None and _c.get("value_usd") is not None \
                and residual:
            _gain = (float(_c["value_usd"]) - float(_hold_value)) \
                / float(residual)
            if _gain < _MIN_IMPROVEMENT_PER_CONTRACT():
                not_rankable.append(dict(
                    _c, value_usd=None, blocker=R_BELOW_MIN_IMPROVEMENT,
                    improvement_over_hold_per_contract=round(_gain, 6),
                    why=("improves on HOLD by %.6f per contract, below the "
                         "declared %.4f minimum the selector applies before "
                         "the book is churned" % (
                             _gain, _MIN_IMPROVEMENT_PER_CONTRACT()))))
                continue
        exit_cand = _c
        candidates.append(_c)
    hold_ranking = {
        "version": hold_from_selector.get("version") or "MGMT_SELECT",
        "candidates": candidates,
        "not_rankable": not_rankable,
    }
    out["hold_ranking"] = hold_ranking
    # ── THE PRIMARY PROBABILITY HOLD IS VALUED ON, FOR THE ACQUISITION ──
    #
    # `rank_with_hold` values HOLD at `value_per_contract` = the held
    # position's external probability, and DIRECT_EXIT and REDUCE keep the
    # unsold part on the same number. The indirect acquisition is priced on
    # THAT probability too (the payout-state distribution's P(primary wins)),
    # so all four actions rest on one primary marginal. It is read from the
    # HOLD candidate that actually enters the ranking -- not re-derived -- and
    # cross-checked against the probability record `manage` kept beside it; a
    # disagreement supplies nothing rather than choosing one.
    primary = _primary_probability_for(hold_ranking,
                                       (_mr or {}).get("hold_probability"))
    # THE SOURCE'S CALIBRATION, by the entry lane's own reader. An acquisition
    # priced on this probability commits new money on it, so the same
    # MODEL_TRUST_DRIFT evidence the entry lane requires is read here and
    # carried with it; `predict_distribution` refuses without a current
    # passing measurement. Read, never assumed.
    primary_calibration = None
    if primary.get("ok"):
        ver = (primary.get("source") or {}).get("version")
        primary_calibration = (
            await source_calibration(conn, ver) if ver else
            {"measured": False, "error": "PRIMARY_SOURCE_VERSION_NOT_STATED",
             "source_version": None,
             "why": ("the HOLD probability's record names no source version, "
                     "so no calibration can be matched to it")})
        primary["source"]["calibration"] = {
            k: (primary_calibration or {}).get(k) for k in (
                "measured", "within_tolerance", "error", "source_version",
                "measured_at", "sample_size", "metric", "score", "tolerance",
                "why")}
    out["primary_probability_read"] = primary
    out["deferred_selection"] = ({k: sel.get(k) for k in
                                  ("selected", "selected_qty", "limit_price",
                                   "proceeds_per_contract",
                                   "expected_net_usd")} if sel else None)
    # ══════════════════════════════════════════════════════════════════
    # THE HEDGE SUPPLIERS, RUN. Previously this returned held_leg=None and
    # candidate_legs=[] with the reads named as NOT WIRED -- so `discover` was
    # skipped on every cycle and no hedge could ever be a candidate.
    #
    # THREE THINGS ARE PRESERVED WHATEVER THESE READS DO.
    #
    #   1. EVERY failure is a named refusal on the payload, never an exception:
    #      a catalogue outage or an unread settlement rule must not take out
    #      the cycle.
    #   2. The exit and the HOLD are ALREADY in `candidates` above and are not
    #      touched here. An independently executable exit survives a total
    #      failure of the hedge path, which is the property Codex asked for and
    #      the one a "return early on missing hedge input" shape destroys.
    #   3. `region_probabilities` stays None while no model is APPROVED. A
    #      venue-implied price is not a qualified probability and is not
    #      substituted for one.
    # ══════════════════════════════════════════════════════════════════
    _prose = prose_reader if prose_reader is not None else _venue_prose
    held = {"ok": False, "refusal": None}
    cands = {"legs": [], "refused": [], "examined": 0}
    hedge_unavailable = []
    try:
        held = await HSUP.held_leg_for(conn, position=pos, prose_reader=_prose,
                                      now=at)
    except Exception as exc:                                    # noqa: BLE001
        held = {"ok": False, "refusal": "HELD_LEG_SUPPLIER_RAISED_"
                + type(exc).__name__,
                "why": ("the held-leg supplier raised. The exit and HOLD "
                        "candidates are unaffected and still rankable")}
    if held.get("ok"):
        try:
            cands = await HSUP.candidate_legs_for(
                conn, held_row=dict(held.get("row") or {},
                                    market_slug=held.get("us_market_slug"),
                                    event_slug=(held.get("row") or {}).get(
                                        "event_slug"),
                                    residual_qty=residual),
                quoter=quoter, prose_reader=_prose, now=at)
        except Exception as exc:                                # noqa: BLE001
            cands = {"legs": [], "refused": [], "examined": 0,
                     "refusal": "CANDIDATE_SUPPLIER_RAISED_"
                     + type(exc).__name__}
    else:
        hedge_unavailable.append(held.get("refusal")
                                 or R_NO_HEDGE_CANDIDATE_READER)
    if cands.get("refusal"):
        hedge_unavailable.append(cands["refusal"])
    if held.get("ok") and not cands.get("legs"):
        hedge_unavailable.append(R_NO_ADMISSIBLE_HEDGE_CANDIDATE)
    # ── THE FEE, PRICED PER CANDIDATE AT ITS OWN DATE AND SPORT ──────
    #
    # `fee_usd` was None, which made every candidate `not_rankable` for want of
    # a fee reading. Two traps the fee module names in its own source and both
    # are avoided here:
    #
    #   * `LATEST_CALLERS_ARE_A_DEFECT` -- "a fee is a fact about WHEN it was
    #     charged. Callers that use LATEST price every fill on today's
    #     schedule." So the schedule is taken with `for_date` at the cycle's own
    #     date.
    #   * `PER_SPORT_THETA_IS_NOT_HERE` -- the exchange-wide coefficient
    #     understates Table Tennis from 2026-10-01 by 31%. So the coefficient
    #     comes from `calibration_fees.taker_coefficient(sport, at)`.
    #
    # A fee that cannot be priced stays None and the candidates report
    # THE_FEE_SCHEDULE_WOULD_NOT_PRICE_THIS_CANDIDATE rather than being scored
    # on an assumed one.
    fee_read = await _fee_for_candidates(
        cands.get("legs") or [], at=at, wanted_qty=residual,
        sport=_clean_family((held.get("row") or {}).get("sports_type")))
    out["fee_read"] = fee_read

    # ── THE PROBABILITY SUPPLIER IS NOT RE-IMPLEMENTED HERE ──────────
    #
    # `discover` already calls `bettor_funded_model.predict_for` itself, per
    # STRUCTURE, once the two legs have been classified -- which is the only
    # place it can be called, because the feature vector is a function of the
    # structure and the structure does not exist until the legs are paired.
    # Computing a probability here would either duplicate that or invent one
    # before the thing it is about exists.
    #
    # So what this reads is the REGISTRY STATE, as evidence on the payload: is
    # a model approved for this key at all. `region_probabilities` stays None
    # and `discover` asks the model itself.
    regions = await _registry_state(conn)
    if primary.get("ok"):
        # THE DISTRIBUTION PATH PRICES THE HEDGE, so ITS model is the
        # dependency to name; KEY_MIDDLE's state is still recorded.
        cond = await _registry_state(conn, model_key=FMD.KEY_HEDGE_GIVEN_PRIMARY)
        out["conditional_registry_read"] = cond
        if not cond.get("approved"):
            hedge_unavailable.append(cond.get("refusal")
                                     or R_NO_REGION_PROBABILITY_SOURCE)
    elif not regions.get("approved"):
        hedge_unavailable.append(regions.get("refusal")
                                 or R_NO_REGION_PROBABILITY_SOURCE)

    out["held_leg_read"] = {k: held.get(k) for k in
                            ("ok", "refusal", "field", "why", "built_from",
                             "grading_key", "prose_read", "missing_facts")}
    out["candidate_legs_read"] = {
        "examined": cands.get("examined"), "built": len(cands.get("legs") or []),
        "refused": cands.get("refused"), "why": cands.get("why"),
        "netting_exclusion": cands.get("netting_exclusion"),
        "every_candidate_was_attempted": cands.get(
            "every_candidate_was_attempted"),
        # ── HOW THE SEARCH ENDED, CARRIED TO THE DECISION (XAVIER) ───────
        # The supplier reports its read budget (the per-pass quote cap), the
        # catalogue's own count of sibling pairs, whether it stopped at the
        # cap, and its eligibility block when one was applied. These were
        # dropped here, so the decision could not say "best among the
        # examined, N of M" and a limited search read as a complete one.
        # Carried verbatim; nothing is recomputed and no read is added.
        "truncated_at_limit": cands.get("truncated_at_limit"),
        "limit": cands.get("limit"),
        "fixture_candidate_pairs": cands.get("fixture_candidate_pairs"),
        "fixture_candidate_slugs": cands.get("fixture_candidate_slugs"),
        "truncation_note": cands.get("truncation_note"),
        "eligibility": cands.get("eligibility"),
        "search_order": {k: (cands.get("search_order") or {}).get(k)
                         for k in ("catalogue_rows_read", "quoted_at_most",
                                   "by_rank", "rule")},
        "refusal": cands.get("refusal")}
    # THE TIE PARTITION, from the held leg's own family and overtime treatment.
    # Both come from reads already done: the family from `sports_type` and the
    # overtime treatment from the venue's captured prose.
    _held_leg_obj = held.get("leg")
    tie_read = vset.tie_is_reachable(
        sport_family=_clean_family((held.get("row") or {}).get("sports_type")),
        overtime=(getattr(_held_leg_obj, "overtime", None)
                  if _held_leg_obj is not None else None))
    if tie_read.get("refusal"):
        hedge_unavailable.append(tie_read["refusal"])
    out["region_probability_read"] = regions
    # ── THE EVIDENCE THE MANAGEMENT VALUATION RESTED ON, for Xavier's record:
    # the basis, HOLD's probability and its source row, the book and
    # probability expiry. Read from `manage`'s own per-position record.
    out["management_evidence"] = {
        k: (_mr or {}).get(k) for k in (
            "selected", "selection_ok", "refusal", "basis", "ev_hold",
            "probability_read", "decision_evidence", "inputs_expire_at",
            "assessed_at", "residual_qty")}
    # ── WHAT THE EXECUTABLE GRID EXCLUDED BEFORE ANYTHING WAS VALUED ─────
    #
    # For the held position's exit ladder and for every hedge candidate the
    # search quoted: the grid (tick, step), the levels counted and the levels
    # excluded as unrepresentable, with prices and quantities -- so the
    # decision record can show that no action was valued, sized or ranked on
    # depth its order could not reach.
    from .. import bettor_book_snapshot as _bsg

    def _grid_view(src):
        src = dict(src or {})
        return {"executable_grid": src.get("executable_grid"),
                "levels_excluded_unrepresentable": src.get(
                    "levels_excluded_unrepresentable"),
                "excluded_unrepresentable_qty": src.get(
                    "excluded_unrepresentable_qty"),
                "counted_levels": src.get("counted_levels"),
                "refusal": src.get("refusal")}
    _hedge_grid = {}
    for c in cands.get("legs") or []:
        _hedge_grid[str(c.get("candidate_id"))] = dict(
            _grid_view(c.get("quote")), price=c.get("price"),
            depth_qty=c.get("depth_qty"), rankable_input=True)
    for r in cands.get("refused") or []:
        if r.get("executable_grid") is not None \
                or r.get("levels_excluded_unrepresentable") is not None:
            _hedge_grid[str(r.get("candidate_id"))] = dict(
                _grid_view(r), rankable_input=False)
    out["executable_grid"] = {
        "exit_ladder": _grid_view((_mr or {}).get("exit_ladder")),
        "hedge_candidates": _hedge_grid,
        "rule": _bsg.EXECUTABLE_GRID_RULE}
    # ── HOW LONG CAPITAL STAYS COMMITTED: the catalogue's scheduled start is
    # the known lower bound; its end is not stated, so no duration is invented.
    from .. import bettor_xavier as _XV
    out["capital"] = _XV.capital_duration(
        game_start=(held.get("row") or {}).get("game_start"), now=at)
    out["readiness"] = dict(PAIR_INPUT_READINESS, **{
        "held_leg": ("BUILT via bettor_funded_hedge_supply.held_leg_for"
                     if held.get("ok") else
                     "REFUSED -- %s" % held.get("refusal")),
        "candidate_legs": ("BUILT %d of %d siblings"
                           % (len(cands.get("legs") or []),
                              cands.get("examined") or 0)),
        "region_probabilities": (
            "ASKED PER STRUCTURE by discover via "
            "bettor_funded_model.predict_for; registry %s"
            % ("has an approved model" if regions.get("approved")
               else "is empty (%s)" % regions.get("refusal")))})
    return dict(out, ok=True,
                held_leg=(held.get("leg") if held.get("ok") else None),
                candidate_legs=[c["leg"] for c in (cands.get("legs") or [])],
                candidate_leg_details=[dict(c, **(fee_read.get("candidate_fees") or {}).get(
                    c.get("candidate_id") or c["market_slug"], {"fee_usd": None}))
                    for c in (cands.get("legs") or [])],
                decision_id="dec:%s:%d" % (intent_id[-24:], int(at)),
                operation_id="op:%s:%d" % (intent_id[-24:], int(at)),
                # None BY DESIGN: `discover` asks `predict_for` per structure.
                region_probabilities=None,
                evidence_quality=FD.EVIDENCE_NOT_ESTABLISHED,
                # ── THE SWITCH THAT MAKES THE REGISTRY LOAD-BEARING ──
                #
                # `decide_and_record` calls `predict_for` only when
                # `use_approved_model` is true, and this supplier never set it --
                # so a promoted model changed nothing on the scheduled path and
                # the indirect candidate was declined for want of a probability
                # even with a model approved. The registry was decorative here.
                #
                # It is set from the REGISTRY'S OWN STATE, not from a
                # preference: with nothing approved it stays false and the lane
                # behaves exactly as before, and with a model approved the
                # probability comes from that one version, whose id, feature
                # vector and sha go onto the decision row.
                # ── CAN THE GRADED INTERVAL END LEVEL ────────────────
                #
                # NEVER SUPPLIED AT ALL BEFORE, so `pass_once` coerced the
                # absence to False with `bool(facts.get(...))` and every
                # structure was classified over a partition with the level cell
                # REMOVED. On a fixture that can end level that omits the
                # outcome which happens.
                #
                # Read from the declared table of game rules, keyed by the sport
                # family and the overtime treatment the venue's own prose
                # established -- so an undeclared pair arrives as None and
                # `discover` refuses rather than removing a cell.
                sport_permits_tie=tie_read.get("permits_tie"),
                sport_permits_tie_read=tie_read,
                use_approved_model=bool(regions.get("approved")),
                # THE PRIMARY PROBABILITY TRAVELS WITH THE MODEL INPUTS: with
                # it the pair cycle prices through the payout-state
                # distribution (`predict_distribution`); without it only the
                # legacy KEY_MIDDLE path remains, and that refuses.
                model_inputs=dict(
                    _model_inputs_for(held=held,
                                      candidates=cands.get("legs") or []),
                    **({"primary_probability": primary["probability"],
                        "primary_source": primary["source"],
                        "primary_calibration": primary_calibration}
                       if primary.get("ok") else {})),
                limits=None,
                fee_usd=fee_read.get("fee_usd"),
                fee_basis=fee_read.get("fee_basis"),
                depth=({c["market_slug"]: c.get("depth_qty")
                        for c in (cands.get("legs") or [])} or None),
                incremental=None, capital_duration_h=None,
                hedge_us_market_slug=None, hedge_quantity=None,
                hedge_limit_price=None, hedge_collateral_usd=None,
                hedge_decision_record=None,
                unavailable=sorted(set(hedge_unavailable)),
                why=("the exit and the hold are read and ranked, and the "
                     "hedge suppliers ran: held leg %s, %d of %d sibling "
                     "contracts built into candidate legs, region "
                     "probabilities %s. Anything unavailable is named on "
                     "`unavailable` and leaves the exit independently "
                     "executable"
                     % ("built" if held.get("ok")
                        else "refused (%s)" % held.get("refusal"),
                        len(cands.get("legs") or []),
                        cands.get("examined") or 0,
                        "available from an approved model"
                        if regions.get("approved")
                        else "unavailable (%s)" % regions.get("refusal"))))


async def _funded_service(conn, *, now, review_interval_s: float = CYCLE_S,
                          run_learning: bool = True,
                          trigger_source: str = "SCHEDULED_SERVICING"):
    """SERVICE WHAT THE FUNDED LANE HOLDS, once per servicing pass.

    `review_interval_s` is when Xavier's next review of each position is
    due, stated on its record: CYCLE_S when the collection cycle is the
    caller (its fallback, see `_service_once`), SERVICING_INTERVAL_S when
    the servicing task is. `run_learning=False` skips ONLY the learning pass
    at the end -- it is the slow half, and it runs on its own cadence (see
    `LEARNING_INTERVAL_S`); nothing that can submit, cancel or recover is
    ever skipped by it.

    Returns None when the funded lane is not configured, like
    `_funded_attempt`. Otherwise it returns one servicing pass:
    reconciliation against the venue, settlement for anything still held,
    the re-measured exposure and realised P&L, and the list of things that
    need a decision.

    IT RUNS WHETHER OR NOT AN ENTRY WAS OFFERED, and that is the point. Entry
    is opportunistic -- no admitted candidate, no attempt. Servicing is not
    optional: inventory the lane already owns has to be reconciled, settled and
    measured on every cycle, or the loss stop is enforced against a stale
    number and a position that settled while the lane was paused never leaves
    the book.

    IT SUBMITS NOTHING. `manage` opens no position at all, and its exits and
    cancels sit behind their own switch, which is off. The reconciliation and
    the settlement read are reads.
    """
    from .. import bettor_funded_activation as _FA
    from .. import bettor_funded_management as _FM

    try:
        bound = _FA._obj(await _FA._state(conn, _FA.ACCOUNT_KEY)) or {}
    except Exception:                                          # noqa: BLE001
        return None
    account_id = str(bound.get("account_id") or "").strip()
    venue = str(bound.get("venue") or "").strip()
    if not account_id or not venue:
        return None
    # ── BOUNDED RENEWAL OF THE 24-HOUR SYSTEM AUTHORIZATION ──────────
    #
    # FIRST, so a later step that raises cannot skip it. It renews only in the
    # record's last RENEW_WINDOW_S, only under an unrevoked, uninvalidated,
    # unexpired owner authorization for the same account, venue and limit
    # digest, only if a full re-run of `authorize` passes, and never past the
    # owner's expiry. Anything else leaves the record to expire, named. It
    # sends nothing and grants nothing the owner has not.
    # The recheck needs a venue reconciliation no older than its bound; when a
    # renewal is due, one is read and recorded first (reads only).
    renewal_reconciliation = await _FA.reconcile_before_renewal(
        conn, account_id=account_id, venue=venue, now=now)
    try:
        renewal = await _FA.renew_system_authorization(conn, now=now)
    except Exception as exc:                                   # noqa: BLE001
        renewal = {"renewed": False, "reason": "RENEWAL_RAISED",
                   "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    renewal["reconciliation"] = renewal_reconciliation
    ev = book_currency_evidence()
    # ── ONE MANAGEMENT DECISION, AND `manage` NO LONGER ACTS ALONE ───
    #
    # THE DEFECT, AND IT WAS THE CENTRAL ONE. This called `manage()` and then
    # `pass_once()`. `manage` step 4 selects an exit AND SUBMITS IT, so an exit
    # could be chosen and sent before the indirect hedge was considered at all --
    # two independent managers, the first acting first. The ranking in
    # `bettor_funded_decision` was real and could not change the outcome,
    # because by the time it ran the sale had happened.
    #
    # `defer_dispatch=True` makes step 4 select, record and STOP. The selections
    # travel into `pass_once` as candidates, join the one ranking that also holds
    # the indirect hedge, and the winner -- exit, reduction, acquisition or
    # nothing for a hold -- is the only thing dispatched.
    #
    # WHAT IS DELIBERATELY NOT DEFERRED: reconciliation, the economics repair and
    # the settlement close. They are reads and a settlement, not the action being
    # ranked, and deferring them would leave the loss stop enforced on a stale
    # number and a settled position on the book.
    try:
        got = await _FM.manage(conn, account_id=account_id, venue=venue,
                               subscription=ev.get("subscription"),
                               revalidation=ev.get("revalidation"),
                               defer_dispatch=True,
                               tick_reader=funded_tick_reader,
                               now=now)
    except Exception as exc:                                   # noqa: BLE001
        # SERVICING THAT RAISED IS NOT SERVICING THAT FOUND NOTHING.
        return {"ok": False, "refusal": "FUNDED_SERVICING_RAISED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "authorization_renewal": renewal}
    got["authorization_renewal"] = renewal
    # ── AND THE PAIR PASS, WHOSE FIRST STEP IS RESERVATION RECOVERY ──
    #
    # WHY IT RUNS ON EVERY CYCLE THAT REACHES HERE, held position or not: its
    # first step resolves reservations left live by a lost acknowledgement. A
    # reservation nobody resolves keeps claiming its leg forever; a reservation
    # resolved WITHOUT the venue's own recorded evidence is how the same leg gets
    # acquired twice. Neither is a state to leave until the next deploy.
    #
    # `pair_inputs` IS THE PAIRING SUPPLIER. The pairing decision needs a venue
    # catalogue read, a fee reading, a depth reading and a region-probability
    # source with stated evidence quality. `funded_pair_inputs` supplies them
    # from the deployed readers; where one is absent it returns `ok: False` with
    # the name of the missing input rather than a manufactured value, and the
    # pass then recovers and acquires nothing.
    #
    # THE DEFERRED EXITS GO WITH IT. Without them a selected exit has no priced,
    # bounded order to send and the pass refuses by name -- which is the correct
    # failure, but the whole point is that they ARE supplied, so the ranking's
    # choice is the one that acts.
    # ── EVERY LOST ACKNOWLEDGEMENT, INVESTIGATED ─────────────────────
    #
    # `manage` has just run recovery, which leaves a send with no answer
    # UNRESOLVED and never adopts a term-matched order. This opens (or
    # refreshes) one durable investigation per such intent with what the venue
    # shows on its market. It reads, records and changes no intent; only the
    # audited operator route closes one. Its reader is also handed to the pair
    # pass, whose recovery step reaches the same investigation from the
    # reservation side.
    from .. import bettor_funded_investigation as _FI
    try:
        got["investigations"] = await _FI.investigate_all(
            conn, account_id=account_id, venue=venue, now=now)
    except Exception as exc:                                   # noqa: BLE001
        got["investigations"] = {
            "ok": False, "refusal": "FUNDED_INVESTIGATION_RAISED",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    # ── AND WHAT WAS ALREADY SETTLED, RE-READ (migration 141) ────────
    #
    # BEFORE the pair pass and the learning pass, so a correction found here
    # is what both of them see this cycle: the contested group is no longer a
    # label (the learning pass withdraws a model trained on it) and the
    # account takes no new exposure. A read; it rewrites no accounting.
    try:
        got["settlement_rechecks"] = await _FM.recheck_settlements(
            conn, account_id=account_id, venue=venue, now=now)
    except Exception as exc:                                   # noqa: BLE001
        got["settlement_rechecks"] = {
            "ok": False, "refusal": "FUNDED_SETTLEMENT_RECHECK_RAISED",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    try:
        from .. import bettor_funded_pair_cycle as _PC

        deferred = {str(d.get("intent_id")): d
                    for d in (got.get("deferred_exits") or [])
                    if d.get("intent_id")}
        got["deferred_exit_count"] = len(deferred)
        import functools

        # THE ACCOUNT, READ AT THE VENUE, for the same gate the entry path
        # consults. Only a dispatched acquisition reaches it; HOLD and the
        # recovery step never do, so a failed read withholds orders and
        # nothing else.
        account_read = await venue_account_exposure()
        got["venue_account_read"] = {
            k: account_read.get(k) for k in (
                "ok", "refusal", "held_usd", "working_usd", "pages",
                "open_orders", "read_at_epoch_s", "why")}
        got["pair_cycle"] = await _PC.pass_once(
            conn, account_id=account_id, venue=venue,
            pair_inputs=functools.partial(
                funded_pair_inputs, deferred=deferred,
                account_id=account_id, venue=venue,
                # THE PRODUCTION RANKING FOR EVERY POSITION, from `manage`.
                management_rankings=got.get("management_rankings") or {},
                # THE PRODUCTION VENUE READS, bound here so the SCHEDULED
                # caller is what supplies them. `_venue_prose` is the same
                # paced, hour-cached reader the entry lane uses;
                # `_candidate_quote` prices each candidate on ITS OWN ladder.
                prose_reader=_venue_prose,
                quoter=functools.partial(_candidate_quote, conn, now=now)),
            deferred_exits=deferred,
            venue_positions=_venue_positions_for_gate(account_read),
            venue_reader=_FI.reservation_reader(),
            # XAVIER'S NEXT REVIEW is no later than the next servicing pass:
            # SERVICING_INTERVAL_S under the servicing task, CYCLE_S when the
            # collection cycle services in its place.
            review_interval_s=review_interval_s,
            # WHAT STARTED THIS PASS: named only for a venue order / market
            # event routed into the same group authority (the scheduled
            # call is unchanged).
            **({} if trigger_source == "SCHEDULED_SERVICING"
               else {"trigger_source": trigger_source}),
            now=now)
        # THE ORDERING, ASSERTED IN THE RESULT rather than left to a reader to
        # infer from two sibling keys. `manage` sent nothing; whatever was sent
        # was sent by the ranking.
        got["decision_ordering"] = {
            "reconciled_first": True,
            "manage_dispatched": False,
            "manage_deferred_exits": len(deferred),
            "ranked_together": True,
            "dispatched_by": "bettor_funded_pair_cycle.pass_once",
            "order": ["reconcile", "shared_decision_evidence",
                      "rank_all_eligible_actions", "persist_the_decision",
                      "dispatch_that_action"],
        }
    except Exception as exc:                                   # noqa: BLE001
        got["pair_cycle"] = {
            "ok": False, "refusal": "FUNDED_PAIR_CYCLE_RAISED",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    # ── AND THE LEARNING LOOP: JOIN FINISHED POSITIONS, SCORE CANDIDATES ──
    #
    # After the pass, so a position the pass just closed is joined this cycle.
    # It promotes nothing: promotion needs a named approver.
    if not run_learning:
        return got
    try:
        from .. import bettor_funded_pair_cycle as _PC

        got["learning"] = await _PC.scheduled_learning_pass(
            conn, account_id=account_id, now=now)
    except Exception as exc:                                   # noqa: BLE001
        got["learning"] = {
            "ok": False, "refusal": "FUNDED_LEARNING_PASS_RAISED",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return got


# ═════════════════════════════════════════════════════════════════════
# ONE EXECUTION AUTHORITY, ON ITS OWN CADENCE
# ═════════════════════════════════════════════════════════════════════
#
# THE DEPENDENCY THIS REMOVES. `_funded_service` -- the reconciliation,
# `manage`'s fill recovery, the lost-acknowledgement investigations, the
# settlement re-reads, the pair pass whose first steps are reservation and
# claim recovery, and Xavier's review of every held position -- ran ONCE PER
# CYCLE, at the top of `cycle()`. First WITHIN its cycle, but the NEXT pass
# waited behind everything else that cycle did (the entry lane's odds fetches
# and paced venue reads, the candidate-outcome rows, the outcome join, the
# calibration measurement, the pair observation pass) and then behind `run`'s
# CYCLE_S sleep, which starts only when the cycle has ended. A held position
# was therefore reviewed, and a lost acknowledgement recovered, once every
# CYCLE_S + elapsed_s -- the ~17-18 minutes between production heartbeats,
# and longer whenever collection ran long. Xavier's own record promised the
# next review at `at + CYCLE_S`, which the schedule never met.
#
# WHAT REPLACES IT. `_servicing_loop` is its own task in the same process,
# started by `run` only once the writer lock is held (a standby services
# nothing), on its own pool connection, one pass every SERVICING_INTERVAL_S
# from the start of the previous one. It awaits nothing the collection cycle
# does. While it is alive the cycle does not service; it reports the task's
# latest pass. `cycle()` called on its own -- no task in this process --
# services exactly as it always did, under the same lock.
#
# ONE EXECUTION AUTHORITY, IN THREE LAYERS:
#   1 PROCESS. Only the process holding LOCK_KEY starts the task.
#   2 IN PROCESS. `_execution_lock()` is the ONE serialization point for every
#     path in this module that can submit or cancel an order: a servicing pass
#     (`manage` and the pair pass it feeds) and the funded entry attempt. A
#     servicing pass that finds it held does NOT queue behind it -- it sends
#     nothing and reports R_EXECUTION_AUTHORITY_BUSY -- so two passes never
#     overlap and the task's pass and the cycle's servicing cannot both
#     dispatch. The funded entry attempt waits at most
#     ENTRY_WAITS_FOR_EXECUTION_S for it and then refuses by name, rather than
#     sending a decision that aged while it waited.
#   3 PER GROUP, ACROSS CONNECTIONS AND PROCESSES. Xavier's group advisory lock
#     and dispatch claims (`bettor_xavier.try_group_lock`, `record_execution`)
#     are unchanged and still govern every dispatch.
#
# WHAT IS NOT BOUNDED BY A TIMEOUT, deliberately: a pass in progress is never
# cancelled. Cancelling one between an intent's commit and the venue's answer
# would manufacture exactly the ambiguous submission recovery exists for, and
# no ambiguous submission is ever retried. Every venue call carries its own
# timeout, and every pass's duration is on the servicing heartbeat, so an
# overrun is visible rather than assumed away.
#
# THE BOUND. A pass starts no later than
#     max(SERVICING_INTERVAL_S, previous pass's duration + SERVICING_MIN_GAP_S)
# after the previous pass started, whatever the collection cycle is doing.
# The only thing the two tasks share is the process-wide venue pacer
# (`venue_pace`, FIFO, one request per MIN_GAP_S), so each servicing venue read
# queues behind at most the collection reads already queued -- seconds, not a
# cycle. The collection cycle's own cadence is unchanged.

#: How often held positions are reviewed and fills / reservations recovered,
#: start to start. The same interval a STOPPED lane has always serviced at
#: (IDLE_POLL_S, every poll), so servicing at this rate is venue load
#: production has already carried.
SERVICING_INTERVAL_S = 60.0

#: The least pause after a pass that overran its interval, so an overrunning
#: pass cannot turn the task into a tight loop against the venue.
SERVICING_MIN_GAP_S = 5.0

#: THE SLOW HALF KEEPS THE COLLECTION CADENCE. The learning pass (it fits and
#: scores candidate models) and Xavier's daily review hold no order path and
#: are not what a held position waits for; running a model fit sixty times an
#: hour would be a change of learning policy, not of management latency. They
#: run inside a servicing pass, under the same lock and after the pair pass as
#: before, at most once per this interval.
LEARNING_INTERVAL_S = CYCLE_S

#: How long a servicing pass waits for a pool connection before the pass is
#: reported as not run -- never an indefinite wait.
SERVICING_ACQUIRE_TIMEOUT_S = 30.0

#: How long the funded ENTRY attempt waits for the execution lock: the age an
#: entry's price may reach at all (PINNACLE_MAX_AGE_S). Longer and the decision
#: it would carry is one the freshness rule would already refuse.
ENTRY_WAITS_FOR_EXECUTION_S = PINNACLE_MAX_AGE_S

#: The servicing task's own heartbeat key, beside the cycle's.
SERVICING_KEY = "ext_pinnacle_last_servicing"

SOURCE_SERVICING_TASK = "SERVICING_TASK"
SOURCE_COLLECTION_CYCLE = "COLLECTION_CYCLE"
R_EXECUTION_AUTHORITY_BUSY = (
    "THE_EXECUTION_LOCK_IS_HELD_BY_ANOTHER_SERVICING_PASS_OR_FUNDED_ACTION"
    "_SO_THIS_ONE_SENDS_NOTHING")
R_SERVICING_PASS_RAISED = "SERVICING_PASS_RAISED"
R_NO_SERVICING_PASS_YET = "THE_SERVICING_TASK_HAS_NOT_COMPLETED_A_PASS_YET"

#: How many recent pass starts are kept to report the measured interval.
SERVICING_STARTS_KEPT = 16

#: The lock is built lazily PER EVENT LOOP (see `db._connect_lock`): an
#: asyncio.Lock binds to the first loop that contends on it.
_EXEC_LOCK: dict = {"lock": None, "loop": None}


def _execution_lock() -> asyncio.Lock:
    """THE ONE IN-PROCESS SERIALIZATION POINT for submit/cancel paths."""
    loop = asyncio.get_running_loop()
    if _EXEC_LOCK["lock"] is None or _EXEC_LOCK["loop"] is not loop:
        _EXEC_LOCK["lock"] = asyncio.Lock()
        _EXEC_LOCK["loop"] = loop
    return _EXEC_LOCK["lock"]


def _servicing_state() -> dict:
    return {"task_active": False, "task_started_at": None,
            "passes": 0, "skipped_busy": 0, "errors": 0, "last_error": None,
            "last": None, "last_started_at": None, "last_finished_at": None,
            "last_elapsed_s": None, "max_elapsed_s": None,
            "last_source": None, "starts": [],
            "slow_half_at": 0.0, "last_learning": None,
            "last_xavier_review": None}


#: What this process's servicing did, for the heartbeats. Process memory only:
#: the durable facts are the funded book, Xavier's decision rows and the
#: SERVICING_KEY heartbeat.
_SERVICING: dict = _servicing_state()


async def _service_once(conn, *, now: float, source: str,
                        review_interval_s: float = SERVICING_INTERVAL_S,
                        slow: bool | None = None, service=None,
                        trigger_source: str = "SCHEDULED_SERVICING") -> dict:
    """ONE SERVICING PASS UNDER THE EXECUTION LOCK. Never raises.

    `slow` runs the slow half (learning, Xavier's daily review): None means
    "when LEARNING_INTERVAL_S has passed since it last ran". `service` is the
    servicing call itself, when the caller binds its own (the collection
    cycle's fallback does, with `_funded_service`'s defaults); by default it
    is `_funded_service` at `review_interval_s`.

    A HELD LOCK IS NOT WAITED ON. Another pass, or a funded entry attempt, is
    acting; this one sends nothing and says so, and the next pass decides on
    what is then held.
    """
    st = _SERVICING
    try:
        lock = _execution_lock()
    except Exception as exc:                                   # noqa: BLE001
        return {"ran": False, "source": source, "at": float(now),
                "refusal": R_SERVICING_PASS_RAISED,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    if lock.locked():
        st["skipped_busy"] += 1
        if trigger_source != "SCHEDULED_SERVICING":
            # A VENUE EVENT ARRIVED WHILE A PASS WAS ACTING: it is not
            # dropped -- the running pass is re-run once when it finishes.
            _VENUE_EVENTS["pending"] += 1
        return {"ran": False, "source": source, "at": float(now),
                "refusal": R_EXECUTION_AUTHORITY_BUSY,
                "funded_service": {"ok": False,
                                   "refusal": R_EXECUTION_AUTHORITY_BUSY},
                "xavier_review": st["last_xavier_review"]}
    async with lock:
        t0 = time.monotonic()
        if slow is None:
            slow = (float(now) - float(st["slow_half_at"] or 0.0)
                    >= LEARNING_INTERVAL_S)
        slow = bool(slow)
        st["starts"] = (st["starts"] + [float(now)])[-SERVICING_STARTS_KEPT:]
        try:
            if service is not None:
                svc = await service()
            else:
                # The scheduled call is exactly the call it always was; only
                # a venue-event pass names its trigger.
                svc = await _funded_service(
                    conn, now=now, review_interval_s=review_interval_s,
                    run_learning=slow,
                    **({} if trigger_source == "SCHEDULED_SERVICING"
                       else {"trigger_source": trigger_source}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:                               # noqa: BLE001
            svc = {"ok": False, "refusal": "FUNDED_SERVICING_RAISED",
                   "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        if slow:
            if isinstance(svc, dict) and "learning" in svc:
                st["last_learning"] = svc.get("learning")
            # XAVIER'S DAILY REVIEW, after the learning pass, as before.
            try:
                xr = await _xavier_daily_review(conn, now=time.time())
            except asyncio.CancelledError:
                raise
            except Exception as exc:                           # noqa: BLE001
                xr = {"ok": False, "ran": False,
                      "refusal": R_SERVICING_PASS_RAISED,
                      "error": "%s: %s" % (type(exc).__name__,
                                           str(exc)[:200])}
            st["last_xavier_review"] = xr
            st["slow_half_at"] = float(now)
        else:
            # THE LAST LEARNING PASS, CARRIED -- it has its own `at`, so the
            # digest shows how old it is rather than an empty learning row.
            if isinstance(svc, dict) and st["last_learning"] is not None:
                svc["learning"] = st["last_learning"]
            xr = st["last_xavier_review"]
        elapsed = round(time.monotonic() - t0, 3)
        out = {"ran": True, "source": source, "at": float(now),
               "elapsed_s": elapsed, "slow_half_ran": slow,
               "review_interval_s": float(review_interval_s),
               "funded_service": svc, "xavier_review": xr}
        st.update(passes=st["passes"] + 1, last=out,
                  last_started_at=float(now), last_finished_at=time.time(),
                  last_elapsed_s=elapsed, last_source=source,
                  max_elapsed_s=max(elapsed, st["max_elapsed_s"] or 0.0))
    # ── AGENTS (core, migration 152): AFTER THE EXECUTION LOCK IS RELEASED ──
    # Handoff repair, Xavier's truthful heartbeat and, on the slow half,
    # Audrey / improvement -- all guarded and bounded, none holding the lock,
    # none able to undo or delay what this pass already did.
    out["agents"] = await _agents_after_service(conn, out, slow=slow)
    # ── A VENUE EVENT THAT ARRIVED DURING THIS PASS ─────────────────────
    # It found the execution lock held and was recorded as pending; the same
    # group authority runs once more now, so the event is acted on without
    # waiting for the next servicing interval. Bounded: one re-run, and a
    # re-run does not chain another.
    if _VENUE_EVENTS["pending"] and not _VENUE_EVENTS["rerunning"]:
        _VENUE_EVENTS["pending"] = 0
        _VENUE_EVENTS["rerunning"] = True
        try:
            out["coalesced_venue_event_pass"] = await _service_once(
                conn, now=time.time(), source=SOURCE_VENUE_EVENT,
                review_interval_s=review_interval_s, slow=False,
                trigger_source=VENUE_EVENT_COALESCED)
        finally:
            _VENUE_EVENTS["rerunning"] = False
    return out


# ═════════════════════════════════════════════════════════════════════
# VENUE EVENTS: A TRIGGER INTO THE SAME GROUP AUTHORITY, NOT A SECOND ONE
# ═════════════════════════════════════════════════════════════════════
#
# An authenticated order update or snapshot (the private websocket's
# `order_update` / `order_snapshot`) and a market update (book, trade,
# market state) on a watched slug are TRIGGERS. Each is recorded (append-
# only, `bettor_standing_order_events`); an order update carrying an
# execution is ingested into the ONE book through the one reader and the
# idempotent writer; and then the SAME servicing pass runs -- `_service_once`
# under the execution lock, `manage`'s reconciliation reads, the pair pass
# under each group's lock -- so the standing order is re-evaluated against
# HOLD on the event rather than on the next interval. The 60-second
# servicing task remains the reconciliation backstop: a lost event costs
# latency, never correctness.
#
# TOUCHING THE PRICE IS NOT A FILL. A market update never writes a fill;
# only the venue's own executions on our order do.
#
# NOT WIRED TO A LIVE CONNECTION IN THIS DEPLOYMENT: the private websocket
# needs the venue credential this deployment does not hold (the same one
# `pmus._get_client` needs). `attach_private_feed` registers these handlers
# on an SDK `PrivateWebSocket` when one is connected.
SOURCE_VENUE_EVENT = "VENUE_EVENT"
VENUE_EVENT_COALESCED = "VENUE_ORDER_EVENT"
#: A market update re-runs the pass at most this often per slug.
MARKET_EVENT_MIN_GAP_S = 1.0
_VENUE_EVENTS: dict = {"pending": 0, "rerunning": False,
                       "last_market_pass": {}}


async def on_private_order_message(conn, message: dict, *,
                                   now: float | None = None,
                                   run_pass: bool = True) -> dict:
    """ONE AUTHENTICATED ORDER UPDATE / SNAPSHOT. Records it, ingests any
    execution it carries (idempotently, into the one book), then runs the
    group authority's pass. Never raises."""
    from .. import bettor_xavier_standing_orders as _SPO
    at = float(now if now is not None else time.time())
    try:
        got = await _SPO.ingest_private_order_message(conn, message, now=at)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}
    if run_pass and got.get("relevant"):
        got["pass"] = await _service_once(
            conn, now=at, source=SOURCE_VENUE_EVENT, slow=False,
            trigger_source="VENUE_ORDER_EVENT")
    return dict(got, ok=True)


async def on_market_message(conn, message: dict, *, now: float | None = None,
                            run_pass: bool = True) -> dict:
    """ONE MARKET UPDATE. Never a fill. On a watched slug it is recorded and
    re-runs the group authority's pass (at most every
    MARKET_EVENT_MIN_GAP_S per slug). Never raises."""
    from .. import bettor_xavier_standing_orders as _SPO
    at = float(now if now is not None else time.time())
    try:
        got = await _SPO.note_market_message(conn, message, now=at)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}
    slug = ((message or {}).get("marketData") or (message or {}).get(
        "marketDataLite") or (message or {}).get("trade") or {}).get(
        "marketSlug")
    last = _VENUE_EVENTS["last_market_pass"].get(slug)
    if run_pass and got.get("relevant") and (
            last is None or at - last >= MARKET_EVENT_MIN_GAP_S):
        _VENUE_EVENTS["last_market_pass"][slug] = at
        got["pass"] = await _service_once(
            conn, now=at, source=SOURCE_VENUE_EVENT, slow=False,
            trigger_source="VENUE_MARKET_EVENT")
    return dict(got, ok=True)


def attach_private_feed(ws, *, get_conn) -> dict:
    """REGISTER THE HANDLERS ON AN SDK `PrivateWebSocket` (its `order_update`
    / `order_snapshot` events). `get_conn` is an async context manager
    factory for a database connection. Each message is handled on its own
    task; the handler itself serialises on the execution lock."""
    import asyncio as _aio

    def _cb(message):
        async def _run():
            async with get_conn() as conn:
                await on_private_order_message(conn, message)
        try:
            _aio.get_running_loop().create_task(_run())
        except RuntimeError:
            pass
    ws.on("order_update", _cb)
    ws.on("order_snapshot", _cb)
    return {"attached": ["order_update", "order_snapshot"]}


async def _agents_after_service(conn, out: dict, *, slow: bool) -> dict:
    """`agents.runtime.xavier_pass_finished` every pass and
    `agents.runtime.slow_half` when the slow half ran. Never raises."""
    got: dict = {}
    try:
        got["xavier"] = await _agents_runtime().xavier_pass_finished(
            conn, res=out, now=time.time())
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        got["xavier"] = {"error": "%s: %s" % (type(exc).__name__,
                                              str(exc)[:200])}
    # PAPER TRADING (migration 171+): one background pass per servicing
    # pass, on its own pool connection, gated by PAPER_SESSION=on AND the
    # paper control row; it returns at once and never raises here.
    try:
        got["paper"] = _agents_runtime().paper_pass_hook(
            trigger="SERVICING_TASK")
    except Exception as exc:                                   # noqa: BLE001
        got["paper"] = {"error": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:200])}
    if slow:
        try:
            got["audrey"] = await _agents_runtime().slow_half(
                conn, now=time.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:                               # noqa: BLE001
            got["audrey"] = {"error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}
    return got


def _serviced_by_the_task() -> tuple:
    """(funded_service, xavier_review) of the servicing task's latest pass,
    for the collection cycle's heartbeat. The cycle services nothing itself
    while the task is alive."""
    last = _SERVICING.get("last")
    if not isinstance(last, dict):
        return ({"ok": None, "refusal": R_NO_SERVICING_PASS_YET},
                _SERVICING.get("last_xavier_review"))
    return last.get("funded_service"), last.get("xavier_review")


def _servicing_cadence_digest(now: float | None = None) -> dict:
    """WHO SERVICES, HOW OFTEN, MEASURED IN THIS PROCESS. Never raises."""
    try:
        st = _SERVICING
        now = time.time() if now is None else float(now)
        starts = list(st.get("starts") or [])
        gaps = [round(b - a, 3) for a, b in zip(starts, starts[1:])]
        last = st.get("last_started_at")
        return {
            "servicer": (SOURCE_SERVICING_TASK if st.get("task_active")
                         else SOURCE_COLLECTION_CYCLE),
            "task_active": bool(st.get("task_active")),
            "task_started_at": st.get("task_started_at"),
            "interval_s": SERVICING_INTERVAL_S,
            "min_gap_s": SERVICING_MIN_GAP_S,
            "learning_interval_s": LEARNING_INTERVAL_S,
            "passes": st.get("passes"),
            "skipped_busy": st.get("skipped_busy"),
            "errors": st.get("errors"), "last_error": st.get("last_error"),
            "last_pass_at": last,
            "last_pass_age_s": (None if last is None
                                else round(now - float(last), 3)),
            "last_pass_source": st.get("last_source"),
            "last_pass_elapsed_s": st.get("last_elapsed_s"),
            "max_pass_elapsed_s": st.get("max_elapsed_s"),
            "slow_half_at": st.get("slow_half_at") or None,
            "recent_start_gaps_s": {
                "n": len(gaps),
                "min": min(gaps) if gaps else None,
                "max": max(gaps) if gaps else None,
                "last": gaps[-1] if gaps else None},
            "bound": ("with the task alive, a servicing pass starts no later "
                      "than max(interval_s, previous pass duration + "
                      "min_gap_s) after the previous one started, whatever "
                      "the collection cycle is doing"),
        }
    except Exception as exc:                                   # noqa: BLE001
        return {"digest_failed": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:160])}


async def _servicing_heartbeat(conn, res: dict) -> None:
    """THE SERVICING TASK'S OWN ROW (SERVICING_KEY). Never raises.

    A pass that did not run (the lock was held) keeps the last completed
    pass's digests, so a skip never erases what was last decided."""
    import json

    try:
        res = res if isinstance(res, dict) else {}
        shown = res if res.get("ran") else (_SERVICING.get("last") or {})
        payload = {
            "at": time.time(),
            "writer": _code_identity(),
            "state": "SERVICED" if res.get("ran") else "NOT_RUN",
            "refusal": res.get("refusal"),
            "source": res.get("source"),
            "pass_at": shown.get("at"),
            "elapsed_s": shown.get("elapsed_s"),
            "slow_half_ran": shown.get("slow_half_ran"),
            "review_interval_s": shown.get("review_interval_s"),
            "servicing_cadence": _servicing_cadence_digest(),
            "funded_servicing": _servicing_digest(shown.get("funded_service")),
            "xavier_review": _xavier_review_digest(shown.get("xavier_review")),
        }
        blob = json.dumps(payload, default=str)
        if len(blob) > HEARTBEAT_MAX_BYTES:
            payload["funded_servicing"] = {"trimmed_over_bytes": len(blob)}
            blob = json.dumps(payload, default=str)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            SERVICING_KEY, blob)
    except Exception:                                          # noqa: BLE001
        log.warning("ext_pinnacle: servicing heartbeat failed", exc_info=True)


async def _servicing_loop(pool, *, interval_s: float = SERVICING_INTERVAL_S,
                          sleep=None, clock=None,
                          max_passes: int | None = None) -> None:
    """THE SERVICING TASK: management and recovery on their own cadence.

    Started by `run` after the writer lock is held; cancelled with it. Each
    pass takes a pool connection (bounded wait), services under the execution
    lock and writes SERVICING_KEY. NOTHING RAISES OUT OF A PASS: a failed pass
    is counted, logged and followed by the next one on schedule. `sleep`,
    `clock` and `max_passes` exist so a test can drive it on controlled time.
    """
    sleep = sleep or asyncio.sleep
    clock = clock or time.monotonic
    st = _SERVICING
    st["task_active"] = True
    st["task_started_at"] = time.time()
    n = 0
    try:
        while max_passes is None or n < max_passes:
            n += 1
            t0 = clock()
            try:
                async with pool.acquire(
                        timeout=SERVICING_ACQUIRE_TIMEOUT_S) as sconn:
                    res = await _service_once(
                        sconn, now=time.time(), source=SOURCE_SERVICING_TASK,
                        review_interval_s=interval_s)
                    await _servicing_heartbeat(sconn, res)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                           # noqa: BLE001
                st["errors"] += 1
                st["last_error"] = "%s: %s" % (type(exc).__name__,
                                               str(exc)[:200])
                log.warning("ext_pinnacle: servicing pass failed",
                            exc_info=True)
            elapsed = clock() - t0
            await sleep(max(SERVICING_MIN_GAP_S, float(interval_s) - elapsed))
    finally:
        st["task_active"] = False


class _EventTally(dict):
    """THE CYCLE'S REFUSAL TALLY, which also says WHICH EVENT each count
    belongs to.

    Every per-event outcome in `cycle` is already written as
    `tally[code] = tally.get(code, 0) + 1`. Recording the increment where it
    happens attributes each code to the open event in the order it fired, so
    no refusal site has to be edited -- and a site added later is attributed
    by construction rather than by remembering to.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.log = None

    def __setitem__(self, key, value):
        if self.log is not None and value > self.get(key, 0):
            self.log.append(key)
        super().__setitem__(key, value)


#: Codes that record what happened AFTER admission rather than why an event was
#: refused. An ADMITTED event keeps them in its code list; none of them is a
#: first refusal.
EVENT_NOT_A_REFUSAL = ("ADMITTED", "ENTRY_INVENTORY_WRITTEN",
                       "EXPOSURE_RESERVED", "DUPLICATE_OBSERVATION_SKIPPED")

#: Every outcome a provider event can have in one cycle. UNCLASSIFIED means the
#: cycle moved past an event without attributing anything to it: a defect in
#: this accounting, reported rather than hidden.
EVENT_OUTCOMES = ("ADMITTED", "REFUSED", "ALREADY_RECORDED", "DEFERRED",
                  "UNCLASSIFIED")


def _event_outcome(codes, *, deferred=False) -> dict:
    """What happened to one provider event, from the codes attributed to it in
    the order they fired."""
    if deferred:
        return {"outcome": "DEFERRED", "first_refusal": None,
                "codes": [], "why": WHY_DEFERRED}
    seen: list = []
    for c in codes:
        if c not in seen:
            seen.append(c)
    refusals = [c for c in seen if c not in EVENT_NOT_A_REFUSAL
                and not str(c).startswith("FUNDED:")]
    if "ADMITTED" in seen:
        outcome = "ADMITTED"
    elif refusals:
        outcome = "REFUSED"
    elif "DUPLICATE_OBSERVATION_SKIPPED" in seen:
        outcome = "ALREADY_RECORDED"
    else:
        outcome = "UNCLASSIFIED"
    return {"outcome": outcome,
            "first_refusal": refusals[0] if refusals else None,
            "codes": seen}


def _reconcile_event_ledger(rows, funnel) -> dict:
    """DO THE ROWS ADD UP TO THE FUNNEL? Per provider sport, the events the
    fetch returned must equal the rows written for that sport, and no row may
    be UNCLASSIFIED. Either failing is reported by name."""
    by_outcome: dict = {}
    per_sport: dict = {}
    for r in rows:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
        per_sport[r["sport_key"]] = per_sport.get(r["sport_key"], 0) + 1
    sports: dict = {}
    for sk, step in (funnel or {}).items():
        want = int((step or {}).get("provider_events") or 0)
        got = per_sport.get(sk, 0)
        sports[sk] = {"provider_events": want, "rows": got,
                      "reconciles": want == got}
    for sk, got in per_sport.items():
        sports.setdefault(sk, {"provider_events": 0, "rows": got,
                               "reconciles": False})
    unclassified = by_outcome.get("UNCLASSIFIED", 0)
    return {"rows": len(rows), "by_outcome": by_outcome,
            "per_sport": sports, "unclassified": unclassified,
            "reconciles": (unclassified == 0
                           and all(v["reconciles"] for v in sports.values()))}


#: The columns migration 143 adds, written when all of them are present.
CANDIDATE_OUTCOME_143_COLUMNS = ("provider_lag_s", "our_processing_s",
                                 "quote_age_s", "mapped_by",
                                 "global_refusal_replaced")


async def _persist_candidate_outcomes(conn, *, cycle_at: float, rows) -> dict:
    """ONE ROW PER PROVIDER EVENT PER CYCLE, appended, never updated.

    Never raises: an unwritable ledger is reported on the cycle and the cycle
    goes on, because refusing to trade is not made safer by also refusing to
    record why. An absent table is named separately from a failed write, since
    one is a migration not yet applied and the other is a fault.
    """
    import json
    import uuid

    cycle_id = uuid.uuid4().hex
    if not rows:
        return {"ok": True, "cycle_id": cycle_id, "rows": 0}
    try:
        exists = await conn.fetchval(
            "SELECT to_regclass('ext_candidate_outcomes') IS NOT NULL")
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "cycle_id": cycle_id, "rows": 0,
                "refusal": "CANDIDATE_OUTCOMES_READ_FAILED",
                "error": type(exc).__name__}
    if not exists:
        return {"ok": False, "cycle_id": cycle_id, "rows": 0,
                "refusal": "CANDIDATE_OUTCOMES_TABLE_ABSENT",
                "why": "migration 137 is not applied here"}
    _ident = _code_identity() or {}
    writer = _ident.get("build") or _ident.get("source_sha256_12")
    # MIGRATION 143'S COLUMNS, WHERE THE DATABASE HAS THEM. The workers can
    # boot before the API has migrated, so an absent column set falls back to
    # the 137 row -- and SAYS SO on the result -- rather than losing every
    # row to a failed INSERT.
    try:
        have = int(await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_name = 'ext_candidate_outcomes' "
            "   AND column_name = ANY($1::text[])",
            list(CANDIDATE_OUTCOME_143_COLUMNS)) or 0)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "cycle_id": cycle_id, "rows": 0,
                "refusal": "CANDIDATE_OUTCOMES_READ_FAILED",
                "error": type(exc).__name__}
    with_143 = have == len(CANDIDATE_OUTCOME_143_COLUMNS)

    def _num(v):
        return None if v is None else float(v)

    base = [(cycle_id, float(cycle_at), writer, r["sport_key"],
             r.get("family"), int(r["queue_position"]),
             r.get("provider_event_id"), r.get("home"), r.get("away"),
             None if r.get("commence_time") is None
             else str(r.get("commence_time")),
             r.get("global_slug"), r.get("us_market_slug"), r.get("stage"),
             r["outcome"], r.get("first_refusal"),
             json.dumps(list(r.get("codes") or [])))
            for r in rows]
    try:
        if with_143:
            await conn.executemany(
                "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, "
                " writer, sport_key, family, queue_position, "
                " provider_event_id, home, away, commence_time, global_slug, "
                " us_market_slug, stage, outcome, first_refusal, codes, "
                " provider_lag_s, our_processing_s, quote_age_s, mapped_by, "
                " global_refusal_replaced) "
                "VALUES ($1, to_timestamp($2), $3, $4, $5, $6, $7, $8, $9, "
                " $10, $11, $12, $13, $14, $15, $16::jsonb, $17, $18, $19, "
                " $20, $21)",
                [b + (_num(r.get("provider_lag_s")),
                      _num(r.get("our_processing_s")),
                      _num(r.get("quote_age_s")),
                      r.get("mapped_by"), r.get("global_refusal_replaced"))
                 for b, r in zip(base, rows)])
        else:
            await conn.executemany(
                "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, "
                " writer, sport_key, family, queue_position, "
                " provider_event_id, home, away, commence_time, global_slug, "
                " us_market_slug, stage, outcome, first_refusal, codes) "
                "VALUES ($1, to_timestamp($2), $3, $4, $5, $6, $7, $8, $9, "
                " $10, $11, $12, $13, $14, $15, $16::jsonb)", base)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "cycle_id": cycle_id, "rows": 0,
                "refusal": "CANDIDATE_OUTCOMES_WRITE_FAILED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    out = {"ok": True, "cycle_id": cycle_id, "rows": len(rows),
           "columns_143": with_143}
    if not with_143:
        out["why_no_arrival_columns"] = (
            "migration 143 is not applied here, so the lag split and the "
            "mapping path were not written; the rows are otherwise complete")
    return out


async def cycle(conn, *, stream_seed=None) -> dict:
    """Never raises. Returns what it did and, mostly, why it did not."""
    started = time.time()
    # ── AGENTS (core, migration 152): DEREK IS EVALUATING ──────────────
    if stream_seed is None:
        await _derek_heartbeat_start(conn, now=started)
    # ONE SCHEDULE FETCH PER OFFICIAL DATE PER CYCLE. The candidates in a
    # cycle cluster on one or two dates; asking per candidate would be the
    # same answer many times over.
    fixture_cache: dict = {}

    # ── SERVICING FIRST, BEFORE ANY ENTRY-SIDE GATE ─────────────────
    #
    # THE DEFECT THIS CLOSES. `_funded_service` used to run at the BOTTOM of
    # this function, after three early returns: the observation stop
    # (`_running`), the shadow table check, and the odds-provider credential.
    # So a paused entry loop, an unmigrated shadow table, or a missing
    # EDGE_ODDS_API_KEY all silently stopped the funded book from being
    # reconciled, settled and re-measured -- while the position stayed open at
    # the venue. Every one of those three is a reason to stop ADDING exposure
    # and none of them is a reason to stop managing what is already held.
    #
    # It runs here, unconditionally, and its own result says what it did. It
    # submits nothing that the servicing switch does not permit.
    #
    # UNLESS THE SERVICING TASK IS ALIVE, which is the production schedule
    # (see "ONE EXECUTION AUTHORITY, ON ITS OWN CADENCE" above `_EventTally`):
    # then held positions are reviewed and fills recovered every
    # SERVICING_INTERVAL_S by that task, never behind this cycle's collection
    # work, and this cycle reports its latest pass instead of running a second
    # one. Without the task (a cycle called on its own) it services here,
    # exactly as before, under the same execution lock.
    async def _service_here():
        return await _funded_service(conn, now=time.time())
    if stream_seed is not None:
        funded_service, xavier_review = {"ran": False, "paper_stream": True}, None
    elif _SERVICING["task_active"]:
        funded_service, xavier_review = _serviced_by_the_task()
    else:
        # XAVIER'S DAILY REVIEW runs inside, after the learning pass inside
        # servicing and above every entry gate, so a stopped lane is still
        # reviewed. Once per UTC day; never fatal. Every heartbeat below
        # carries its digest.
        _svc = await _service_once(conn, now=time.time(),
                                   source=SOURCE_COLLECTION_CYCLE,
                                   review_interval_s=CYCLE_S, slow=True,
                                   service=_service_here)
        funded_service = _svc.get("funded_service")
        xavier_review = _svc.get("xavier_review")
    _t_serviced = time.time()

    # ── A CYCLE THAT ENTERS NOTHING STILL SERVICED, SO IT STILL BEATS ──
    #
    # THE GAP THIS CLOSES. Servicing runs above the entry gates on
    # purpose: a stopped entry loop is a reason to add nothing, not a
    # reason to stop managing an open position. But each of these three
    # early returns went straight back to the caller WITHOUT writing the
    # heartbeat, so the decision the pass had just made about every held
    # position was discarded -- and the command centre's tile went stale
    # in exactly the states an operator most needs it: the lane stopped,
    # the table missing, the credential absent. `_beat` writes what was
    # decided and then returns the same dict.
    async def _beat(payload: dict) -> dict:
        if stream_seed is not None:
            return payload
        payload.setdefault("xavier_review", xavier_review)
        payload.setdefault("step_timing_s", {
            "servicing_in_cycle": round(_t_serviced - started, 3)})
        # AGENTS (core): Derek's after_cycle and truthful end state (BLOCKED,
        # naming why), guarded -- the entry lane did not run.
        payload["agents"] = await _derek_after_cycle(conn, payload)
        await _heartbeat(conn, payload)
        return payload

    running, why = await _running(conn)
    if not running:
        return await _beat({
            "ran": False, "state": "STOPPED", "why": why,
            "funded_servicing": funded_service,
            # THE OBSERVER STOPS WITH THE LANE, and says so: the stop switch
            # stops this lane's venue reads, and observations are venue reads.
            "pair_observation": {"ran": False,
                                 "why": R_OBSERVER_STOPPED_WITH_THE_LANE,
                                 "lane_state": "STOPPED"},
            "servicing_ran_anyway": ("a stopped entry loop is a reason to "
                                     "add nothing, not a reason to stop "
                                     "managing an open position")})
    if not await _table_ready(conn):
        return await _beat({
            "ran": False, "state": "BLOCKED", "why": R_NO_TABLE,
            "funded_servicing": funded_service,
            "pair_observation": ({"ran": False} if stream_seed is not None else
                await _observation_when_entry_is_blocked(
                    conn, now=time.time(), blocked_by=R_NO_TABLE)),
            "servicing_ran_anyway": True})

    cred = ext.credential_present()
    if not cred["present"]:
        return await _beat({
            "ran": False, "state": "BLOCKED",
            "why": cred["refusal"], "credential": cred,
            "funded_servicing": funded_service,
            # NOT AN ODDS-PROVIDER READ: the observer prices nothing from
            # the provider, so a missing provider credential does not stop it.
            "pair_observation": ({"ran": False} if stream_seed is not None else
                await _observation_when_entry_is_blocked(
                    conn, now=time.time(), blocked_by=str(cred["refusal"]))),
            "servicing_ran_anyway": ("the odds provider prices NEW "
                                     "candidates. An open funded position "
                                     "is managed from the VENUE's book "
                                     "and the venue's settlement, neither "
                                     "of which needs this credential")})
    api_key = os.environ["EDGE_ODDS_API_KEY"]

    from .. import bettor_fee_schedule as FEES
    from decimal import Decimal

    def fee_fn(qty, price, maker=False):
        return float(FEES.LATEST.fill_fee(
            Decimal(str(round(float(qty), 6))),
            Decimal(str(round(float(price), 6))), maker=bool(maker)))

    # TWO SPORT VOCABULARIES EXIST AND THEY DO NOT OVERLAP.
    #
    # `markets.sport` is written by `workers/metadata_refresher` via
    # `sports.classify`, whose labels are capitalised leagues:
    # 'Soccer', 'MLB', 'NBA', 'NHL', 'Tennis', ... . The family names this
    # loop and `bettor_pinnacle_devig` use ('soccer', 'baseball') come from
    # `bettor_sport_mapping`, which reads the venue's own market-type
    # prefixes. Filtering the markets table on the SECOND vocabulary
    # matched nothing at all.
    #
    # Measured, not deduced: the first armed cycle reported
    # `markets 0` with `NO_VENUE_CONTRACT_FOR_EVENT 44` -- every event
    # refused for want of a venue contract when the truth was that the
    # candidate query returned an empty set. A mapping refusal and an empty
    # candidate list are different facts, and only one of them is about
    # the mapping.
    tally = _EventTally()
    # ── EVERY PROVIDER EVENT, WITH ITS IDENTITY AND WHAT HAPPENED TO IT ──
    #
    # The tally counts codes and `mapped_candidate_ledger` names only the
    # candidates that REACHED the mapping. An event refused before that --
    # no Pinnacle price, no venue contract -- was a count and nothing else, and
    # the heartbeat holding even that is overwritten every cycle. So "33 events,
    # 33 refusals" could be reconciled once, by whoever read it in time, and
    # never again. One row per provider event now carries its identity and every
    # code the cycle attributed to it, IN ORDER, and is written durably below.
    event_ledger: list = []
    _open_ev: dict = {"row": None}

    def _close_event() -> None:
        row = _open_ev["row"]
        tally.log = None
        _open_ev["row"] = None
        if row is None:
            return
        row.update(_event_outcome(row.pop("_codes")))
        event_ledger.append(row)

    def _open_event(sport_key, family, position, ev) -> None:
        _close_event()
        ev = ev if isinstance(ev, dict) else {}
        row = {"sport_key": sport_key, "family": family,
               "queue_position": int(position),
               "provider_event_id": ev.get("id"),
               "home": ev.get("home_team"), "away": ev.get("away_team"),
               "commence_time": ev.get("commence_time"),
               "global_slug": None, "us_market_slug": None, "stage": None,
               # migration 143: the price's age on arrival, split, and which
               # catalogue mapped the contract. None until measured.
               "provider_lag_s": None, "our_processing_s": None,
               "quote_age_s": None, "mapped_by": None,
               "global_refusal_replaced": None,
               "_codes": []}
        _open_ev["row"] = row
        tally.log = row["_codes"]

    def _event_fields(fields: dict) -> None:
        """Facts about the OPEN event that are not refusal codes."""
        if _open_ev["row"] is not None:
            _open_ev["row"].update(fields)
    # THE VENUE'S OWN ERROR TEXT, bounded. A counter says how often the
    # venue refused; only the message says whether that is an entitlement,
    # a closed market or a rate limit -- and those need different actions
    # from different people.
    venue_errors: list = []
    seen_venue_errors: set = set()
    # ── EVERY MAPPED CANDIDATE, RECONCILED TO ITS FIRST REFUSAL ──────
    #
    # `funnel_by_provider_sport` says `mapped 3 -> evaluated 0` and the
    # refusal tally says which codes fired, but neither says WHICH mapped
    # candidate stopped WHERE. On run 75 the per-candidate census lived
    # only in a separate route whose step died, so `mapped 3` had no
    # reconciliation at all and the difference between three candidates
    # failing one gate and one candidate failing three was unreadable.
    #
    # One row per candidate that REACHED the mapping, carrying the venue
    # slug, the stage, the first refusal and -- where the stage is the
    # book read -- the clock evidence. Bounded: MAX_PER_CYCLE candidates
    # are evaluated at most, so this list cannot outgrow that.
    ledger: list = []
    observable: list = []

    def _ledger(entry: dict) -> None:
        if _open_ev["row"] is not None:
            for _k in ("global_slug", "us_market_slug", "stage"):
                if entry.get(_k) is not None:
                    _open_ev["row"][_k] = entry.get(_k)
        if len(ledger) < MAX_PER_CYCLE + 8:
            ledger.append(entry)
    # ── WHICH COMPETITIONS THIS CYCLE MAY SPEND ON ───────────────────
    #
    # CONFIRM BEFORE SPENDING. `/v4/sports` is unmetered, so every candidate key
    # is checked against the provider's own catalogue before a metered fetch is
    # made on it. A key I guessed wrong is then a named refusal in the report
    # rather than ~20 credits and a 422. The confirmed set runs either way, so a
    # catalogue outage cannot stop the sport that has always worked.
    # AND WHICH COMPETITIONS THE VENUE IS LISTING TODAY. The board is seasonal:
    # on 2026-09-28 it was almost entirely national-team football, because late
    # September is an international window. A candidate list frozen in source
    # would be wrong within a fortnight, so it is read.
    if stream_seed is not None:
        # Discovery/competition was confirmed by the scheduled collector.
        # Re-resolve the venue-native identity below; never scan its full
        # catalogue or make another metered REST discovery request per tick.
        sports_selection = {"sports": [(stream_seed["sport_key"],
                                         stream_seed["family"])],
                            "confirmed_by_provider": []}
        sports_for_cycle = tuple(sports_selection["sports"])
        markets = []
    else:
        _cat = await fetch_sport_catalogue(api_key=api_key)
        _board = await venue_soccer_competitions(conn)
        # THE FIXTURE DATES TRAVEL WITH THE TITLES (map4 section 9 D1). The board
        # read has carried `title_days` since the return-leg defect was fixed, and
        # this call dropped it, so every candidate reached
        # `confirm_mapping_by_fixtures` with `venue_title_days == {}` and the date
        # comparison never ran on the scheduled path -- only in a test that called
        # the helper directly.
        # THE FOOTBALL BOARD BESIDE THE SOCCER ONE (cand22): without it the
        # venue's `cfb` events were never a candidate and disappeared with no
        # refusal. Merged by venue coverage; the budget is unchanged.
        _fboard = await venue_football_competitions(conn)
        sports_selection = select_sports(
            _cat, candidates=merge_candidates(
                candidates_from_board(_board["board"],
                                      _board.get("titles"),
                                      _board.get("title_days")),
                football_candidates(_fboard)))
        sports_selection["venue_board"] = _board
        sports_selection["venue_football_board"] = _fboard
        # FOOTBALL IS ITERATED LAST. Its budget slot is earned by coverage
        # above, but the sports this lane already valued keep their place in
        # the loop -- and with it their share of the per-cycle bounds
        # (MAX_PER_CYCLE, MAX_CALIBRATION_ONLY_PER_CYCLE) and the paced venue
        # reads -- exactly as before this change. Stable: no other order moves.
        sports_for_cycle = tuple(sorted(sports_selection["sports"],
                                        key=lambda kf: kf[1] == "football"))
        labels = sorted({lbl for _, fam in sports_for_cycle
                         for lbl in VENUE_SPORT_LABELS.get(fam, ())})
        markets = [dict(r) for r in await conn.fetch(MARKETS_SQL, labels, MARKET_STALE_AFTER_S)]
    # ── THE OBSERVED UNIVERSE, AND WHERE IT NARROWS ──────────────────
    #
    # `markets_considered` was ONE number over BOTH supported sports, and
    # the refusal tally was one dictionary over all of them. Neither could
    # answer the question that was actually asked: does the OTHER supported
    # sport contribute any candidate at all, and if not, at which step does
    # it stop? So the universe is counted per venue label and the early
    # gates are counted per provider sport. Reporting only -- no gate reads
    # any of this.
    universe: dict = {}
    for _m in markets:
        _lbl = str(_m.get("sport") or "UNLABELLED")
        universe[_lbl] = universe.get(_lbl, 0) + 1
    funnel: dict = {}
    if not markets and stream_seed is None:
        # SAY SO BY NAME rather than letting 44 mapping refusals imply the
        # mapper is at fault.
        tally[R_NO_CANDIDATE_MARKETS] = 1

    written = 0
    evaluated = 0
    credits = {"used": None, "remaining": None}
    # WHAT THE FRESHNESS KNOB ACTUALLY DID THIS CYCLE, reported rather than
    # inferred from the credit count. Both are 0 at the default setting.
    odds_refetches = 0
    odds_refetch_failures = 0

    # ── THE LATENCY MEASUREMENT, ACCUMULATED OVER THE WHOLE CYCLE ──────
    #
    # Owner requirement: "Measure provider age, our added delay, request
    # counts, valid evaluations and refusals on the deployed path. Show
    # whether the previously self-inflicted stale cases decrease."
    #
    # PROVIDER LAG AND OUR OWN DELAY STAY APART. `pinnacle_age_s` is their
    # SUM and the 30 s rule governs the sum -- unchanged by any of this.
    # But the remedies differ and only the second is ours, so a single
    # aggregate age would hide whether the levers below did anything.
    #
    # `self_inflicted_stale` is the number that has to move: a refusal
    # where the provider handed us a quote that was INSIDE the limit and
    # our own accumulated processing pushed it outside. Counted at the
    # decision, from clocks on the record, never estimated.
    # WHAT PRIORITISATION PUSHED PAST THE CAP. Bounded, because a heartbeat
    # is not a log; `deferred_total` counts every one so the bound cannot
    # hide the scale.
    deferred: list = []
    deferred_total = 0
    lat = {"provider_lag_samples": [], "our_delay_samples": [],
           "age_samples": [], "valid_evaluations": 0, "stale_refusals": 0,
           "self_inflicted_stale": 0, "provider_stale_on_arrival": 0,
           "stale_on_arrival_due_to_our_processing": 0,
           "skipped_stale_on_arrival": 0, "arrival_skip_samples": 0,
           "deduplicated_requests": 0, "venue_requests": 0,
           # every event WITH A PINNACLE PRICE, measured when it came up --
           # mapped or not, evaluated or not
           "arrival_lag_every_priced": [], "arrival_ours_every_priced": []}

    # THE OPEN BOOK, READ ONCE PER CYCLE. The risk rails are measured
    # against it plus the position being proposed, so it has to be read
    # before any candidate is evaluated. None means the read FAILED, and
    # None blocks every entry this cycle rather than reading as a flat
    # book -- a database outage must not look like available headroom.
    open_book = await open_shadow_book(conn, ext.EXPERIMENT_ID)
    if open_book is None:
        tally[entryx.R_BOOK_NOT_READ] = 1
    # WHETHER THE EVENT RAIL CAN BE MEASURED OVER THIS BOOK AT ALL (A9). Read
    # once per cycle, carried onto every candidate's plan, and reported on the
    # cycle so an operator sees a rail that is UNMEASURABLE rather than a rail
    # that silently read zero.
    _ev_measurable = event_exposure_is_measurable(open_book)
    #: What this cycle actually created, reported per entry so a reader
    #: never has to infer inventory from a refusal count.
    entries: list = []
    # THE CALIBRATION MEASUREMENT, read once per cycle. Absent, the
    # MODEL_TRUST_DRIFT gate blocks every entry -- which is the current
    # production state and is reported rather than worked around.
    calibration = await source_calibration(conn, devig.VERSION)
    # THE UNFUNDED RESEARCH LANE'S AUTHORISATION, read once per cycle from
    # its own control row. Absent or unreadable is OFF. It waives exactly
    # MODEL_TRUST_DRIFT and nothing else; see bettor_research_shadow.
    research = await rsh.authorised(conn)
    # ── VALUATIONS RECORDED FOR CALIBRATION ONLY, counted apart ─────────
    # Never in `evaluated` or `written`: a calibration-only record reached no
    # execution estimate and is not an entry decision, so counting it there
    # would let "written" read as progress of the entry lane. The event it
    # belongs to stays REFUSED under its venue-read code.
    cal_only: dict = {"attempted": 0, "recorded": 0, "already_recorded": 0,
                      "not_recorded_cycle_bound": 0, "persist_errors": {},
                      "refusals": {}, "rows": []}

    for sport_key, family in sports_for_cycle:
        _close_event()
        if evaluated >= MAX_PER_CYCLE:
            break
        step = funnel.setdefault(sport_key, {
            "family": family,
            "venue_labels": list(VENUE_SPORT_LABELS.get(family, ())),
            "venue_markets_open_and_fresh": sum(
                universe.get(lbl, 0)
                for lbl in VENUE_SPORT_LABELS.get(family, ())),
            "provider_events": 0, "with_pinnacle_h2h": 0,
            "mapped_to_a_venue_contract": 0,
            # WHICH CATALOGUE MAPPED IT: `mapped_to_a_venue_contract` counts
            # both paths, this counts the venue-native one, and
            # `venue_native_replaced` names the global refusal each replaced.
            "mapped_by_venue_native": 0, "venue_native_replaced": {},
            # THE STAGE AFTER MAPPING, counted (map4 section 9 D4): an
            # identity refusal used to reach the cycle tally and not this
            # sport's own refusals, so `mapped 3 -> evaluated 0` did not say
            # how many stopped at identity.
            "identity_resolved": 0,
            "evaluated": 0, "written": 0,
            "refusals": {}})

        def _step_refuse(code, _s=step):
            _s["refusals"][code] = _s["refusals"].get(code, 0) + 1

        def _venue_native_took_it(replaced, _s=step):
            """A venue-native identity replaced the global refusal(s)."""
            _s["mapped_by_venue_native"] += 1
            for c in replaced:
                _s["venue_native_replaced"][c] = \
                    _s["venue_native_replaced"].get(c, 0) + 1
            _event_fields({"mapped_by": vnat.MAPPED_BY_VENUE_NATIVE,
                           "global_refusal_replaced":
                               ",".join(str(c) for c in replaced)})

        got = ({"ok": True, "events": [stream_seed["event"]],
                "received_at": stream_seed["received_at"]}
               if stream_seed is not None else
               await fetch_odds(sport_key, api_key=api_key))
        credits["used"] = got.get("credits_used") or credits["used"]
        credits["remaining"] = (got.get("credits_remaining")
                                or credits["remaining"])
        if not got.get("ok"):
            tally[R_PROVIDER_ERROR] = tally.get(R_PROVIDER_ERROR, 0) + 1
            _step_refuse(R_PROVIDER_ERROR)
            continue
        # ── IS THIS PROVIDER COMPETITION THE VENUE'S COMPETITION? ────
        #
        # THE HOLE THIS CLOSES. `select_sports` established that the key EXISTS
        # and is ACTIVE, which is not the same as its being the competition
        # mapped to it. `soccer_england_league2` exists, is active, and is the
        # fourth tier -- the venue's `engnl` is the fifth. A mismatch would have
        # been fetched, failed to resolve every event, and reported as a
        # mapping defect in the funnel.
        #
        # THIS IS THE FIRST MOMENT THE CHECK IS POSSIBLE: the provider's
        # fixtures only exist after the fetch. So the metered call is already
        # spent, and what the check protects is everything after it -- no
        # resolution attempt, no venue read, no candidate, and the competition
        # is refused BY NAME rather than appearing as twenty unmappable events.
        # The credit is reported as spent either way.
        # THE PAID FETCH'S EVENTS ARE COUNTED BEFORE ANYTHING CAN REFUSE THEM
        # (map4 section 9 D3). This was set after the confirmation below, so a
        # competition refused there showed `provider_events 0` and no event
        # rows at all, although the metered call had returned events.
        step["provider_events"] = len(got.get("events") or [])
        _cand = next((c for c in sports_selection.get("confirmed_by_provider")
                      or [] if c.get("key") == sport_key), None)
        if _cand is not None:
            conf = confirm_mapping_by_fixtures(
                provider_events=got.get("events") or [],
                venue_event_titles=_cand.get("venue_titles") or [],
                # THE VENUE'S OWN FIXTURE DATES, so the date check runs on the
                # scheduled path rather than only in a test.
                venue_event_days=_cand.get("venue_title_days") or {},
                family=_cand.get("family") or family)
            step["mapping_confirmation"] = {
                k: conf[k] for k in ("ok", "refusal", "matches", "examined",
                                     "min_required", "matched_fixtures", "why")}
            _cand["fixture_confirmation"] = step["mapping_confirmation"]
            if not conf["ok"]:
                # COUNTED ONCE, AS A COMPETITION (the tally and this sport's
                # refusals are unchanged), AND RECORDED ONCE PER EVENT: every
                # event the fetch returned gets its row, refused with the
                # confirmation's reason, so the rows still add up to
                # `provider_events` and an event is never silently absent.
                tally[conf["refusal"]] = tally.get(conf["refusal"], 0) + 1
                _step_refuse(conf["refusal"])
                _close_event()
                for _k, _e in enumerate(got.get("events") or []):
                    _e = _e if isinstance(_e, dict) else {}
                    event_ledger.append({
                        "sport_key": sport_key, "family": family,
                        "queue_position": _k,
                        "provider_event_id": _e.get("id"),
                        "home": _e.get("home_team"),
                        "away": _e.get("away_team"),
                        "commence_time": _e.get("commence_time"),
                        "global_slug": None, "us_market_slug": None,
                        "stage": "3_IDENTITY",
                        **_event_outcome([conf["refusal"]])})
                continue
        received_at = got["received_at"]

        # HOW MANY EVENTS THIS FETCH HAS ALREADY SERVED. Counted per sport,
        # because `received_at` is stamped per fetch and the accumulated
        # processing delay is what it governs.
        served_by_this_fetch = 0
        # BY INDEX OVER A LOCAL LIST, so a re-fetch can actually replace the
        # events still to come. `for event in got["events"]` binds the list
        # once, so rebinding `got` inside it would have changed nothing --
        # a fresh payload would have been fetched, paid for, and then
        # ignored for every event but the current one.
        events = list(got["events"] or [])

        # ── LEVER B · FRESHEST FIRST ────────────────────────────────
        #
        # The accumulated delay falls on whoever is evaluated LAST, so the
        # order decides which candidates survive it. Sorting by the
        # PROVIDER'S own `last_update` -- newest first -- spends the early,
        # low-delay slots on the quotes with the most headroom.
        #
        # THIS MOVES NO LIMIT AND DROPS NOTHING. The same events are
        # considered; only the order changes. An event with no readable
        # timestamp sorts LAST rather than first, because an unknown age
        # must not be handed the freshest slot -- that would be the
        # favourable assumption.
        #
        # AND IT IS NOT A SELECTION RULE. It does not prefer better
        # candidates, richer markets or anything economic. It prefers
        # FRESHER DATA, which is a property of the observation and not of
        # the opportunity.
        def _prov_epoch(e):
            q = pinnacle_h2h(e, received_at=received_at)
            return _quote_epoch(q) if q is not None else None

        # A TOTAL ORDER, so the same input always yields the same order.
        # The event id is the final tiebreak: without it two events sharing
        # a `last_update` would be ordered by whatever sequence the
        # provider happened to send, and a rerun on the same payload could
        # evaluate a different subset once MAX_PER_CYCLE bites. A
        # measurement that moves when the data did not is not a
        # measurement, and this is the same clock-dependence that made four
        # test identities appear to vanish between two gate runs.
        _ages = {id(e): _prov_epoch(e) for e in events}
        events.sort(key=lambda e: (_ages[id(e)] is None,
                                   -(_ages[id(e)] or 0.0),
                                   str((e or {}).get("id") or "")))

        # ── LEVER C · WITHIN-CYCLE DEDUPLICATION ────────────────────
        #
        # WHAT COUNTS AS EQUIVALENT, and it is narrow on purpose: the same
        # `(us_market_slug, intent)`. That pair IS the instrument -- one
        # venue contract, one side of it. Two provider events that resolve
        # to it would issue two identical paced venue reads for one book.
        #
        # WHAT IS *NOT* DEDUPLICATED, because the owner's instruction is
        # exact ("Deduplicate only equivalent requests; retain correct
        # instrument identity and observation times"):
        #   * NOT by condition_id alone -- that is the market, and the two
        #     sides of it are two different ladders.
        #   * NOT by event -- one event carries several markets.
        #   * NOT the PROVIDER quote. Each event keeps its own
        #     `observed_at`, and the reused venue book is re-aged against
        #     the SECOND candidate's own decision instant, so a shared read
        #     cannot lend its freshness to a later decision.
        #
        # The cache is per SPORT FETCH, not per cycle, because
        # `received_at` is stamped per fetch.
        vq_cache: dict = {}
        for _i in range(len(events)):
            if evaluated >= MAX_PER_CYCLE:
                # ── WHAT PRIORITISATION DEFERRED, REPORTED BY IDENTITY ──
                #
                # Owner requirement: "report which candidates were
                # deferred, so improved throughput is not confused with
                # quietly dropping difficult cases."
                #
                # THE FAILURE THIS MAKES IMPOSSIBLE. Lever B reorders, so
                # the candidates that fall past MAX_PER_CYCLE are now
                # chosen BY THE SORT rather than by the provider's
                # sequence. If the deferred set were invisible, a change
                # that consistently pushed the same hard markets past the
                # cutoff would show up as a better stale rate and a better
                # evaluation count with no trace of the coverage it lost.
                # Naming them turns that into something a reader can see.
                #
                # DEFERRED, NOT REFUSED, and the distinction is kept: these
                # were not judged and nothing about them was decided. The
                # cap is the reason and it is stated.
                for _k in range(_i, len(events)):
                    _e = events[_k] or {}
                    if len(deferred) >= MAX_DEFERRED_REPORTED:
                        break
                    deferred.append({
                        "event_id": _e.get("id"),
                        "sport_key": sport_key,
                        "home": _e.get("home_team"),
                        "away": _e.get("away_team"),
                        "provider_observed_at_epoch_s": (
                            None if _ages.get(id(events[_k])) is None
                            else round(float(_ages[id(events[_k])]), 6)),
                        "queue_position": _k,
                        "why": WHY_DEFERRED})
                deferred_total += max(0, len(events) - _i)
                _close_event()
                for _k in range(_i, len(events)):
                    _e = events[_k] if isinstance(events[_k], dict) else {}
                    event_ledger.append({
                        "sport_key": sport_key, "family": family,
                        "queue_position": _k,
                        "provider_event_id": _e.get("id"),
                        "home": _e.get("home_team"),
                        "away": _e.get("away_team"),
                        "commence_time": _e.get("commence_time"),
                        "global_slug": None, "us_market_slug": None,
                        "stage": None,
                        **_event_outcome([], deferred=True)})
                break
            # ── REFRESH THE QUOTE BEFORE IT GOES STALE ON OUR CLOCK ────
            #
            # At the default (EVENTS_PER_ODDS_FETCH == MAX_PER_CYCLE) this
            # can never fire, so the credit spend is exactly what it was.
            # Lowered deliberately, it bounds our OWN contribution to
            # `pinnacle_age_s` at roughly that many events' worth of paced
            # venue reads instead of the whole cycle's.
            if stream_seed is None and served_by_this_fetch >= EVENTS_PER_ODDS_FETCH:
                again = await fetch_odds(sport_key, api_key=api_key)
                credits["used"] = again.get("credits_used") or credits["used"]
                credits["remaining"] = (again.get("credits_remaining")
                                        or credits["remaining"])
                odds_refetches += 1
                served_by_this_fetch = 0
                if again.get("ok") and again.get("received_at") is not None:
                    # THE NEW RECEIPT INSTANT AND THE NEW PAYLOAD'S OWN
                    # PRICES, together. Taking the stamp without the prices
                    # would be the worst of both: a fresh-looking age on an
                    # old quote, which is the exact false certificate this
                    # module's freshness note warns about twice.
                    received_at = again["received_at"]
                    fresh = {(e or {}).get("id"): e
                             for e in (again["events"] or [])
                             if isinstance(e, dict) and (e or {}).get("id")}
                    for _j in range(_i, len(events)):
                        _r = fresh.get((events[_j] or {}).get("id"))
                        if _r is not None:
                            events[_j] = _r
                else:
                    # KEPT, NOT DISCARDED. The old quote ages normally and
                    # QUOTE_STALE names it. Abandoning the sport would turn
                    # a provider hiccup into missing coverage.
                    odds_refetch_failures += 1
            event = events[_i]
            _open_event(sport_key, family, _i, event)
            served_by_this_fetch += 1
            quote = primary_pinnacle_h2h(
                event, received_at=received_at, family=family, at=time.time())
            if stream_seed is None:
                from .. import pinnapi_reactive as reactive
                reactive.register(event, sport_key=sport_key, family=family,
                                  received_at=received_at)
            elif (quote or {}).get("reference_input", {}).get("provider") != "pinnapi.com/raw-websocket":
                tally["WS_REFERENCE_NOT_USABLE"] = tally.get("WS_REFERENCE_NOT_USABLE", 0) + 1
                continue
            if quote is None:
                tally["NO_PINNACLE_ON_EVENT"] = \
                    tally.get("NO_PINNACLE_ON_EVENT", 0) + 1
                _step_refuse("NO_PINNACLE_ON_EVENT")
                continue
            if stream_seed is not None:
                source = quote["reference_input"]
                version = (source["epoch"], source["source_change_ms"],
                           tuple(sorted(source["raw_odds"].items())))
                if version != stream_seed["trigger"]["version"]:
                    tally["WS_TRIGGER_SUPERSEDED"] = 1
                    continue
                source["evaluation_trigger"] = {
                    k: stream_seed["trigger"][k] for k in
                    ("attempt_id", "received_at", "queued_at", "evaluation_started_at")}
            step["with_pinnacle_h2h"] += 1
            # ── HOW OLD THE PRICE WAS WHEN WE REACHED IT, FOR EVERY EVENT ──
            #
            # THE GAP THIS CLOSES. The lag split was taken only for events that
            # reached a decision (and, at lever A, for those skipped there), so
            # an event refused at mapping or identity left no measurement: the
            # cycle could not say whether its unmapped events were also stale
            # ones, or whose time made them so. Every event with a Pinnacle
            # price is measured here, on the row; lever A re-measures at its
            # own instant for the events that get that far.
            _pe = _quote_epoch(quote)
            _first = arrival_split(_pe, quote["received_at"], time.time())
            _event_fields(_first)
            if _first["provider_lag_s"] is not None:
                lat["arrival_lag_every_priced"].append(_first["provider_lag_s"])
            if _first["our_processing_s"] is not None:
                lat["arrival_ours_every_priced"].append(
                    _first["our_processing_s"])

            # ── the venue contract, exactly or not at all ───────────
            mapped = vmap.map_event(home=quote["home"], away=quote["away"],
                                    markets=markets)
            ident = None
            if not mapped["mapped"]:
                # ── THE VENUE'S OWN CATALOGUE, WHEN THE GLOBAL ONE HAS NO ROW ──
                #
                # `map_event` searches the GLOBAL catalogue. On 2026-09-29 at
                # least eleven of twenty NO_VENUE_CONTRACT events were listed
                # by the US venue itself on the same date, and the two MLB
                # series games collided because the global match ignores
                # dates. `bettor_venue_native_identity` asks the venue's own
                # catalogue -- start time within its tolerance, both teams to
                # distinct participants, exactly one event -- and on a match
                # the candidate continues down THIS admission path with that
                # identity. On a refusal the global refusal stays first and
                # the venue-native refusal is counted beside it.
                vn = None
                if venue_native_may_replace(mapped["refusals"]):
                    vn = await vnat.resolve_venue_native(
                        conn, home=quote["home"], away=quote["away"],
                        commence_time=quote.get("commence_time"),
                        family=family, now=time.time(),
                        competition=sport_key,
                        league_tokens=venue_league_tokens(sport_key))
                if vn is not None and vn.get("ok"):
                    replaced = list(mapped["refusals"])
                    mapped = venue_native_mapping(vn, replaced=replaced,
                                                  global_mapped=mapped)
                    ident = vn
                    _venue_native_took_it(replaced)
                else:
                    for code in mapped["refusals"]:
                        tally[code] = tally.get(code, 0) + 1
                        _step_refuse(code)
                    if vn is not None:
                        code = vn.get("refusal") or vnat.R_NO_EVENT
                        tally[code] = tally.get(code, 0) + 1
                        _step_refuse(code)
                    continue
            else:
                _event_fields({"mapped_by": vnat.MAPPED_BY_GLOBAL})
            step["mapped_to_a_venue_contract"] += 1

            # ── the venue's OWN contract, before any venue read ─────
            # Refusing here costs nothing: a fixture the venue's catalogue
            # does not carry cannot answer a book read, and asking anyway
            # spends a read the protected collector shares.
            _vn_refusal = None
            if ident is None:
                ident = await resolve_venue_identity(
                    conn, market_row=mapped["market_row"],
                    priced_outcome=quote["home"])
                if (not ident["ok"]
                        and ident.get("refusal")
                        in VENUE_NATIVE_MAY_REPLACE_IDENTITY):
                    # THE GLOBAL ROW WAS FOUND AND THE US CROSSING FOUND
                    # NOTHING. Only that refusal is offered to the venue's own
                    # catalogue; a realism, intent or period refusal is a
                    # finding about a contract that WAS found, and stands.
                    vn = await vnat.resolve_venue_native(
                        conn, home=quote["home"], away=quote["away"],
                        commence_time=quote.get("commence_time"),
                        family=family, now=time.time(),
                        competition=sport_key,
                        league_tokens=venue_league_tokens(sport_key))
                    if vn.get("ok"):
                        vn["global_identity_replaced"] = {
                            k: ident.get(k) for k in (
                                "refusal", "why", "global_slug",
                                "condition_id", "resolver")}
                        mapped = venue_native_mapping(
                            vn, replaced=[R_NO_PREMAP], global_mapped=mapped)
                        ident = vn
                        _venue_native_took_it([R_NO_PREMAP])
                    else:
                        _vn_refusal = vn.get("refusal") or vnat.R_NO_EVENT
            if ident["ok"]:
                # A CONTRACT WITH A RESOLVED VENUE IDENTITY is something the
                # non-funded pair observer can start from, whatever this
                # candidate's own fate below.
                observable.append((ident.get("us_market_slug"),
                                   ident.get("intent")))
                step["identity_resolved"] += 1
            if not ident["ok"]:
                code = ident["refusal"] or R_NO_PREMAP
                tally[code] = tally.get(code, 0) + 1
                _step_refuse(code)
                if _vn_refusal:
                    # BOTH COUNTED, the crossing's refusal first.
                    tally[_vn_refusal] = tally.get(_vn_refusal, 0) + 1
                    _step_refuse(_vn_refusal)
                _ledger({"global_slug": mapped.get("global_slug")
                         or (mapped.get("market_row") or {}).get("slug"),
                         "us_market_slug": ident.get("us_market_slug"),
                         "priced_outcome": quote.get("home"),
                         "stage": ext.STAGE_OF.get(code, "3_IDENTITY"),
                         "first_refusal": code,
                         "venue_native_refusal": _vn_refusal,
                         "period_evidence": ident.get("period_evidence")})
                key = (ident.get("global_slug"), code, ident.get("intent"))
                if (key not in seen_venue_errors
                        and len(venue_errors) < MAX_VENUE_ERRORS):
                    seen_venue_errors.add(key)
                    venue_errors.append({
                        "stage": "IDENTITY_CROSSING",
                        "resolver": ident["resolver"],
                        "global_slug": ident.get("global_slug"),
                        "condition_id": ident.get("condition_id"),
                        "priced_outcome": ident.get("priced_outcome"),
                        "intent": ident.get("intent"),
                        "refusal": code,
                        "why": _sanitize(ident.get("why") or "", limit=200)})
                continue

            # ── LEVER A · SKIP WHAT IS ALREADY PAST THE LIMIT ───────
            #
            # Checked HERE: after the free work (parse, map, identity) and
            # BEFORE the paced venue read, which is the expensive step and
            # the one that adds most of our own delay.
            #
            # THE LIMIT IS UNCHANGED. This is `PINNACLE_MAX_AGE_S`, the
            # odds source's own 30 s rule, compared against the same
            # provider epoch the decision gate will compare. No tighter
            # bound, no margin, no forecast of how long the read will take
            # -- a candidate is skipped only when it is ALREADY outside the
            # limit at this instant, so the decision gate could not
            # possibly admit it.
            #
            # WHY THIS IS NOT A REFUSAL BEING HIDDEN. The refusal still
            # happens and is still counted: `QUOTE_STALE_ON_ARRIVAL` is
            # tallied by name and the candidate is written to the ledger
            # exactly as a late-stage staleness refusal would be. What is
            # saved is the venue read, the pacing gap it costs, and -- the
            # point -- the delay that read would have added to every
            # candidate AFTER this one. A doomed candidate currently makes
            # its successors stale too.
            #
            # AND IT IS PROVIDER STALENESS, NOT OURS. At this instant our
            # own contribution is whatever the cycle has accumulated so
            # far, so `provider_stale_on_arrival` is only incremented when
            # the PROVIDER's lag alone already exceeded the limit; the
            # combined case is `skipped_stale_on_arrival`.
            _pe = _quote_epoch(quote)
            reference_received_at = quote["received_at"]
            _arr = time.time()
            _event_fields(arrival_split(_pe, reference_received_at, _arr))
            if _pe is not None and (_arr - _pe) > PINNACLE_MAX_AGE_S:
                lat["skipped_stale_on_arrival"] += 1
                if (reference_received_at is not None
                        and (float(reference_received_at) - _pe) > PINNACLE_MAX_AGE_S):
                    lat["provider_stale_on_arrival"] += 1
                elif reference_received_at is not None:
                    # OURS, BY NAME. The provider handed this price over
                    # INSIDE the limit and our own accumulated processing
                    # took it past. It used to be derivable only as
                    # skipped - provider_stale, which also swept in the
                    # events whose receipt stamp was missing.
                    lat["stale_on_arrival_due_to_our_processing"] += 1
                # THE SKIPPED EVENT'S CLOCKS JOIN THE CYCLE'S FIGURES. The
                # medians sampled evaluated candidates only, so the events
                # most likely to be stale were the ones left out of the
                # measurement of staleness.
                if reference_received_at is not None:
                    lat["provider_lag_samples"].append(
                        float(reference_received_at) - _pe)
                    lat["our_delay_samples"].append(_arr - float(reference_received_at))
                lat["age_samples"].append(_arr - _pe)
                lat["arrival_skip_samples"] += 1
                code = R_QUOTE_STALE_ON_ARRIVAL
                tally[code] = tally.get(code, 0) + 1
                _step_refuse(code)
                _ledger({"global_slug": mapped.get("global_slug")
                         or (mapped.get("market_row") or {}).get("slug"),
                         "us_market_slug": ident.get("us_market_slug"),
                         "priced_outcome": quote.get("home"),
                         "stage": "2_FRESHNESS",
                         "first_refusal": code,
                         "observed_at_epoch_s": round(float(_pe), 6),
                         "decision_instant_epoch_s": round(_arr, 6),
                         "age_s": round(_arr - _pe, 3),
                         "limit_s": PINNACLE_MAX_AGE_S,
                         "provider_lag_s": (
                             None if reference_received_at is None
                             else round(float(reference_received_at) - _pe, 3)),
                         "our_processing_s": (
                             None if reference_received_at is None
                             else round(_arr - float(reference_received_at), 3)),
                         "age_basis": "PROVIDER_LAST_UPDATE_AT_ARRIVAL",
                         "why": WHY_SKIPPED_ON_ARRIVAL})
                continue

            # THE READ CLOCK, WHICH IS NOT THE DECISION CLOCK. This
            # instant ages the book at the moment it was read. The
            # DECISION instant is taken after the venue read, the rules
            # read and the fixture-metadata read have all completed, and
            # both inputs are re-aged against THAT -- because a decision
            # stamped before several network reads claims a freshness it
            # did not have, which is the defect already repaired once in
            # the management lane.
            read_at = time.time()
            # THE INTENT IS THE SIDE. Passing it is what makes this read
            # the ladder the contract actually trades on.
            # THE SAME SEAM THE FUNDED LANE USES. It supplies no mechanism
            # today, so this read refuses on an unestablished currency -- which
            # is the named blocker, not a hidden one.
            _cev = book_currency_evidence(ident["us_market_slug"])
            # ── LEVER C, APPLIED ────────────────────────────────────
            #
            # THE KEY IS THE INSTRUMENT: this venue contract, this side.
            # A second provider event resolving to the same pair would
            # otherwise issue an identical paced read for the same book.
            #
            # WHAT IS REUSED IS THE VENUE'S BOOK PAYLOAD, NOTHING ELSE.
            # `read_at` for THIS candidate is its own instant (taken above),
            # the provider quote is its own, and `_entry_freshness` re-ages
            # BOTH against this candidate's own decision instant further
            # down. So a reused read cannot lend its freshness to a later
            # decision: the second candidate is judged on how old the book
            # actually is when IT decides, which is strictly older. That is
            # the whole reason this is safe, and it is the reason the
            # freshness recheck was left exactly where it was.
            # THE KEY IS THE COMPLETE REQUEST IDENTITY, not a subset of it.
            # `venue_quote` is a function of exactly these arguments, so two
            # calls agreeing on all of them are the same request and two
            # differing anywhere are not. Naming them individually rather
            # than keying on (slug, intent) alone: the currency evidence
            # decides whether the read can be admitted at all, so two
            # candidates with different subscription or revalidation
            # evidence must NOT share an answer even on one instrument.
            #
            # THE EVIDENCE IS DIGESTED, NOT NAMED BY A FIELD I ASSUMED
            # EXISTS. My first version keyed on `_cev["subscription_key"]`
            # and `_cev["revalidation_key"]` -- neither of which
            # `book_currency_evidence` returns. Both read None for every
            # candidate, so the key silently degraded to (slug, intent) and
            # two candidates with DIFFERENT currency evidence would have
            # collided. `_evidence_key` digests the real objects, so the key
            # cannot quietly lose a component the way a wrong field name
            # does.
            _ck = (ident["us_market_slug"], ident["intent"],
                   _evidence_key(_cev.get("subscription")),
                   _evidence_key(_cev.get("revalidation")))
            if _ck in vq_cache:
                # ── REFUSED, NOT REUSED, AND THIS IS THE SAFE DIRECTION ──
                #
                # THE BUG THIS AVOIDS, which my first version had. Handing
                # the cached `vq` to a second candidate gives it the same
                # `acquisition_ladder` and `depth`. Each candidate then
                # sizes independently against that ladder as though it
                # owned all of it, so two candidates on ONE instrument
                # could together claim twice the quantity the book can
                # actually fill. Shared depth silently becoming two
                # independent executable quantities is a sizing error that
                # reaches the wire, and it is strictly worse than the
                # duplicate read it was meant to save.
                #
                # AND IT ALSO DISPOSES OF THE STALE-CACHE QUESTION. A
                # cached book is never re-aged, re-admitted or consulted
                # again, so there is no entry that can expire during
                # processing and no path by which an old read lends its
                # freshness to a later decision. The saving -- the paced
                # venue read -- is kept in full.
                #
                # TWO PROVIDER EVENTS ON ONE INSTRUMENT ARE ONE
                # OPPORTUNITY. We would act at most once on it in any
                # case, so refusing the duplicate loses no reachable
                # trade; it only stops us pricing the same book twice.
                lat["deduplicated_requests"] += 1
                code = R_INSTRUMENT_ALREADY_EVALUATED
                tally[code] = tally.get(code, 0) + 1
                _step_refuse(code)
                _ledger({"global_slug": mapped.get("global_slug")
                         or (mapped.get("market_row") or {}).get("slug"),
                         "us_market_slug": ident.get("us_market_slug"),
                         "priced_outcome": quote.get("home"),
                         "stage": "3_IDENTITY",
                         "first_refusal": code,
                         "already_evaluated_from_event":
                             vq_cache[_ck].get("first_event_id"),
                         "this_event": quote.get("event_id"),
                         "observed_at_epoch_s": (
                             None if _pe is None else round(float(_pe), 6)),
                         "why": WHY_DUPLICATE_INSTRUMENT})
                continue
            vq = await venue_quote(
                conn, us_slug=ident["us_market_slug"],
                intent=ident["intent"], now=read_at,
                subscription=_cev.get("subscription"),
                revalidation=_cev.get("revalidation"))
            lat["venue_requests"] += 1
            # ONLY A SUCCESSFUL READ CLAIMS THE INSTRUMENT. Recording a
            # refusal would suppress every retry on that instrument for the
            # rest of the cycle, so one transient venue error would refuse
            # every candidate on that contract with an optimisation as the
            # cause rather than the venue.
            #
            # WHAT IS STORED IS A CLAIM, NOT A BOOK TO REUSE. Only the
            # identity of the event that claimed it is ever read back, for
            # the ledger line above -- the payload is never handed to a
            # second candidate.
            if vq.get("ok"):
                vq_cache[_ck] = {"first_event_id": quote.get("event_id"),
                                 "claimed_at": read_at}
            # SET ONLY BY A CURRENCY REFUSAL ON A BOOK THAT WAS READ; see
            # CALIBRATION_ONLY_AFTER. None everywhere else, and then every
            # line below runs exactly as it did before.
            calibration_only = None
            if not vq.get("ok"):
                code = vq.get("refusal") or R_NO_VENUE_QUOTE
                tally[code] = tally.get(code, 0) + 1
                _vq_entry = {
                         "global_slug": mapped.get("global_slug")
                         or (mapped.get("market_row") or {}).get("slug"),
                         "us_market_slug": ident.get("us_market_slug"),
                         "priced_outcome": quote.get("home"),
                         "stage": ext.STAGE_OF.get(code, "2_FRESHNESS"),
                         "first_refusal": code,
                         # THE FIVE NUMBERS A CLOCK REFUSAL OWES: the raw
                         # value, what it parsed to, the instant it was
                         # measured against, the age and the limit.
                         "raw_transact_time": (
                             (vq.get("venue_clock") or {}).get("raw")),
                         "parsed_epoch_s": (
                             (vq.get("venue_clock") or {})
                             .get("parsed_epoch_s")),
                         # THE INSTANT THE VERDICT WAS ACTUALLY TAKEN AT,
                         # which is no longer the pre-read `read_at` (D8).
                         "decision_instant_epoch_s": round(float(
                             (vq.get("venue_clock") or {}).get(
                                 "verdict_instant_epoch_s") or read_at), 6),
                         "age_s": vq.get("age_s"),
                         "limit_s": vq.get("limit_s"),
                         "age_basis": vq.get("age_basis"),
                         "age_semantics": VENUE_CLOCK_SEMANTICS}
                _ledger(_vq_entry)
                # THE VENUE'S OWN WORDS, kept. Run 22 named this refusal
                # `VENUE_BOOK_READ_RETURNED_ERROR 2` -- which is the right
                # counter and still not an answer: whether that is an
                # entitlement, a closed market or a rate limit decides
                # whether anyone can act on it, and the message says which.
                # Bounded, deduplicated, and slug-tagged so it identifies a
                # contract without becoming a log dump.
                diag = dict(vq.get("diagnostic") or {})
                diag["condition_id"] = mapped["condition_id"]
                diag["us_market_slug"] = ident["us_market_slug"]
                diag["refusal"] = code
                # THE CLOCK TRAVELS WITH THE REFUSAL. Run 75 refused
                # `aec-mlb-nym-wsh-2026-09-26` with VENUE_QUOTE_STALE --
                # which is only reachable once the age is MEASURED, so it
                # was itself the proof that transactTime now parses. But
                # the projection carried no age, no limit and no raw
                # value, so the one line that established the repair could
                # not say by how much the book was stale or what string it
                # read. A refusal that names a number must show it.
                for k in ("age_s", "limit_s", "age_basis"):
                    if vq.get(k) is not None:
                        diag[k] = vq.get(k)
                vclock = vq.get("venue_clock")
                if isinstance(vclock, dict):
                    diag["venue_clock"] = {
                        kk: vclock.get(kk) for kk in
                        ("field_path", "raw", "raw_type", "parser",
                         "parsed_epoch_s", "age_at_read_s", "basis", "why")
                        if vclock.get(kk) is not None}
                diag.setdefault("why", _sanitize(vq.get("why") or "",
                                                 limit=120))
                key = (diag.get("slug"), diag.get("code"),
                       diag.get("status"), diag.get("exception"))
                if (key not in seen_venue_errors
                        and len(venue_errors) < MAX_VENUE_ERRORS):
                    seen_venue_errors.add(key)
                    venue_errors.append(diag)
                # ── THE CALIBRATION-ONLY RECORD, OR NOTHING ─────────────
                #
                # The refusal above is counted, ledgered and attributed to
                # this event exactly as before; nothing here undoes it. What
                # continues is only the VALUATION -- the settlement evidence,
                # the fixture scope, the attestation and the gate, through the
                # same `ext.evaluate` / `ext.persist` -- so the odds source can
                # be scored against the venue's settlement. It is sealed
                # CALIBRATION_ONLY and returns to the loop before the
                # admission block below is reachable.
                calibration_only = _calibration_only_basis(vq)
                if calibration_only is None:
                    continue
                if (evaluated + cal_only["attempted"]
                        >= MAX_CALIBRATION_ONLY_PER_CYCLE):
                    cal_only["not_recorded_cycle_bound"] += 1
                    continue
                cal_only["attempted"] += 1
                _vq_entry["calibration_only_record"] = "ATTEMPTED"

            # THE SETTLEMENT RULE, AND WHY THIS USUALLY STOPS HERE.
            # Pinnacle's rule is known per sport. The VENUE contract's
            # rule is not in our data at all, so `agrees` returns None and
            # this refuses by name. An unknown is not a match, and
            # asserting one would make every edge below unfalsifiable.
            # THE RULES, ATTESTED PER FIXTURE FROM EACH SIDE'S CATALOGUE.
            # `agrees()` answers from the module-level ATTESTED table,
            # which is empty and stays empty; `attest()` asks what THIS
            # event's evidence shows and says which class established each
            # rule. Only ATTESTING_CLASSES count -- an inference is
            # recorded and does not unblock.
            vevid = await venue_settlement_evidence(
                conn, ident["us_market_slug"])
            # THE SAME AUTHORITATIVE EVIDENCE MANAGEMENT ALREADY USES.
            #
            # The entry lane used to attest settlement with no context,
            # no scope and no event state, which means the comparison ran
            # against whichever rule set happened to be keyed by
            # ("baseball", "h2h") alone. Management does not do that: it
            # reads the persisted `fixture_metadata` row, takes the
            # competition phase and the game format from it, and derives
            # the quote's PRE_GAME/LIVE context from the ACTUAL reported
            # event state rather than a catalogue start time.
            #
            # Entry now reads the same row, through the same parser, for
            # the same reasons -- Pinnacle grades a called game one way
            # before the first pitch and the opposite way in play, and an
            # entry priced under the wrong one of those is priced against
            # a contract that does not exist. The provenance travels with
            # it: source, URL, fixture binding and retrieval time.
            # ACQUIRED IF ABSENT, not merely read. The row used to be
            # written only by a hand-invoked admin route, so ordinary
            # candidates had none and the comparison could never see our
            # own side. The lane now acquires the same evidence, through
            # the same parser, for the candidate it is evaluating.
            fmeta = await acquire_fixture_scope(
                conn, condition_id=mapped["condition_id"],
                home=quote.get("home"), away=quote.get("away"),
                commence_iso=quote.get("commence_time"),
                now=time.time(), cache=fixture_cache,
                sport_family=family, sport_key=sport_key,
                venue_event_slug=(vevid or {}).get("event_slug"))
            ctx_ev = None
            if fmeta.get("read") and fmeta.get("play_has_begun") is not None:
                ctx_ev = fmeta_mod.context_for(
                    {"play_has_begun": fmeta.get("play_has_begun"),
                     "actual_start_at": fmeta.get("actual_start_at"),
                     "retrieved_at": fmeta.get("retrieved_at")},
                    observed_at=_quote_epoch(quote))
            srule = vset.attest(
                sport_family=family, market="h2h",
                venue_evidence=vevid,
                book_evidence={"outcome_names": list(quote["prices"].keys()),
                               "source": ("pinnapi:ws:h2h:%s" % devig.BOOK
                                          if (quote.get("reference_input") or {}).get("provider")
                                          == "pinnapi.com/raw-websocket" else
                                          "theoddsapi:h2h:%s" % devig.BOOK)},
                observed_at=_quote_epoch(quote),
                book_context=(ctx_ev or {}).get("context"),
                phase=fmeta.get("phase"),
                game_format=fmeta.get("game_format"))
            srule["book_rule"] = vset.BOOK_SETTLEMENT.get(family)
            srule["fixture_metadata"] = fmeta
            srule["quote_context_evidence"] = ctx_ev

            # ── THE DECISION INSTANT, TAKEN HERE ────────────────────
            # After the venue book read, the venue rules read and the
            # fixture-metadata read. Both clocks are re-aged against it
            # below, so a decision cannot be stamped fresher than the work
            # that produced it. `decision_lag_s` is the gap those reads
            # actually took, reported rather than absorbed.
            now = time.time()
            decision_lag_s = round(now - read_at, 3)
            # THE SPECIFIC UNMET RULES, not one blanket unknown. "The
            # settlement rules do not match" is four questions -- draw,
            # overtime, push, void -- and a census that collapses them
            # cannot tell management which one to go and establish.
            extra = ([] if srule.get("overall_established")
                     else (list(srule.get("unmet") or [])
                           or [srule.get("refusal") or R_VENUE_RULE_UNKNOWN]))
            if calibration_only is not None:
                # THE VENUE READ'S OWN REFUSAL LEADS the record's refusals: it
                # is where this candidate stopped as an entry, and every later
                # code is what the trace met after it.
                extra = [calibration_only["refusal"]] + [
                    c for c in extra if c != calibration_only["refusal"]]

            # Independent-book depth is re-aged too; a cached discovery
            # payload must not supply stale corroboration for a WS price.
            if ((quote.get("reference_input") or {}).get("provider")
                    == "pinnapi.com/raw-websocket"):
                from .. import pinnapi_primary as primary
                quote["depth"], _independent = primary.book_depth(
                    event, quote["prices"], SHARP_BOOKS,
                    at=now, max_age_s=PINNACLE_MAX_AGE_S)
                quote["reference_input"]["independent_books"] = _independent
            reference_check = validate_primary_pinnacle(quote, at=now)
            if not reference_check["ok"]:
                extra = [reference_check["reason"]] + extra

            contract = {
                "venue": "PMUS",
                # BOTH IDENTITIES, EACH FROM ITS OWN SOURCE. The global id
                # comes from the `markets` row the fixture was found in;
                # the venue-native slug comes from the venue's own
                # catalogue via premap.resolve. Neither is derived from
                # the other, which is why the basis can say BOTH.
                "condition_id": mapped["condition_id"],
                "us_market_slug": ident["us_market_slug"],
                # A VENUE-NATIVE IDENTITY HAS NO GLOBAL ID and says so
                # (VENUE_NATIVE_US_SLUG); it never borrows the global row's.
                "contract_identity_basis":
                    ident.get("contract_identity_basis")
                    or "BOTH_PRESENT_AND_INDEPENDENTLY_SOURCED",
                "identity_resolver": ident["resolver"],
                "buy_intent": ident["intent"],
                "selection": quote["home"],
                # WHAT THIS CONTRACT PAYS ON, carried onto the row so a
                # reader never has to infer it from the intent.
                # THE IDENTITY EVIDENCE, PRESERVED. Requested outcome,
                # the venue side actually matched, the intent, the ladder
                # it selects and the event that pays -- all on the row, so
                # payout identity is never reconstructed from the intent.
                "payout_event": ident["payout_event"],
                "payout_event_basis": ident["payout_event_basis"],
                "probability_event": ident["probability_event"],
                "resolver_asked_for": ident["resolver_asked_for"],
                "matched_side_norm": ident.get("matched_side_norm"),
                "matched_identifier": ident.get("matched_identifier"),
                "matched_by": ident.get("matched_by"),
                "ladder_side": ident["ladder_side"],
                "sport_family": family,
                "market": "h2h",
                # ESTABLISHED, NOT ASSERTED. `resolve_venue_identity`
                # refuses unless the venue slug demonstrably names the
                # whole dated fixture, so reaching here means FULL_GAME
                # was read off the venue's own identifier rather than
                # typed in. The evidence travels with it.
                "period": ident["period"],
                "period_basis": ident["period_basis"],
                "period_evidence": ident["period_evidence"],
                "line": None,
                "settlement_rule": srule["book_rule"],
                "event_key": quote["event_id"],
            }
            rec = ext.evaluate(
                contract=contract,
                quote={# THE BOOK IS DECLARED, and the source checks it.
                       # Omitting it made the valuation refuse every event
                       # with PINNACLE_NOT_IN_THIS_PAYLOAD -- correctly,
                       # since an undeclared book is exactly the silent
                       # substitution the refusal exists to catch.
                       "book": devig.BOOK,
                       # `outcomes`, which is the key the source reads.
                       # Passing `prices` produced "0 of 3 outcomes priced"
                       # -- the refusal was right and the caller was wrong.
                       "outcomes": quote["prices"],
                       "observed_at": quote["observed_at"],
                       "received_at": quote["received_at"],
                       "event_key": quote["event_id"],
                       # THE SAME ESTABLISHED PERIOD ON BOTH SIDES. When
                       # this was a literal on both, the comparison could
                       # not fail: two assertions of FULL_GAME always
                       # agree. Now the contract's period comes from the
                       # venue slug and the quote is declared to be the
                       # feed's full-match h2h, so a mismatch is visible.
                       "period": ident["period"],
                       "period_basis": "THE_FEED_H2H_MARKET_IS_FULL_MATCH",
                       "line": None,
                       "settlement_rule": srule["book_rule"]},
                # THE ACQUISITION PRICE, NOT THE API PRICE. For a LONG
                # these are the same number; for a SHORT the API price is
                # YES-denominated and the cost is its complement, and
                # passing the wrong one here is the same
                # mis-denomination the execution lane's wire-price
                # conversion exists to prevent (that module is
                # deliberately not nameable from this loop).
                #
                # A CALIBRATION-ONLY RECORD has no acquisition price: its book
                # was refused. It is compared at the DISPLAYED price, labelled
                # as such, and `evaluate` moves that price out of the
                # executable columns when it seals the record.
                market_state=(_displayed_market_state(calibration_only)
                              if calibration_only is not None else
                              {"ask": vq["acquisition_price"],
                               "api_price": vq["api_price"],
                               "side_consumed": vq["side_consumed"],
                               "depth": vq["depth"],
                               "readable": True}),
                # THE THREE PLACEHOLDERS ARE GONE. What stood here was
                #
                #     execution_estimate p_fill None  -> refused, honestly
                #     size               1.0          -> chosen by nobody
                #     risk               permitted    -> the risk layer
                #                                       told its answer
                #
                # and the second and third are the ones that mattered: a
                # BUY admitted on them would have been sized by a literal
                # and cleared by an assertion. All three now come from
                # `bettor_entry_execution`, which composes the frozen
                # sizing policy, the marketable-fill reconstructor and the
                # risk engine with this lane's predeclared limits. The
                # plan runs INSIDE evaluate, after the one valuation, so
                # the limit, the size and the price all describe the same
                # payout event as the probability.
                #
                # NO PLAN FOR A CALIBRATION-ONLY RECORD: nothing may be sized
                # against a displayed price, so no size, execution estimate,
                # exposure or risk verdict is built. The gate then refuses it
                # on those by name, which is the trace we want.
                execution_plan=None if calibration_only is not None else
                _entry_plan(
                    ladder=vq.get("acquisition_ladder"),
                    fee_fn=fee_fn,
                    observation_age_s=vq.get("age_s"),
                    action=_risk_action(ident["intent"]),
                    condition_id=mapped["condition_id"],
                    # THE VENUE'S EVENT, NOT THE PROVIDER'S (A9). The open book
                    # now carries `us_premap.event_slug` per row; comparing the
                    # provider's `event_id` against it would never match, so the
                    # event rail would still read zero on a repaired query.
                    event_key=ident.get("venue_event_key"),
                    venue_market_slug=ident.get("us_market_slug"),
                    provider_event_id=quote["event_id"],
                    open_book=open_book,
                    event_exposure_measurable=_ev_measurable,
                    settlement=srule,
                    freshness=_entry_freshness(quote, vq, now),
                    calibration=calibration,
                    research_authorised=research.get("authorised") is True,
                    now=now),
                # Still passed, and still what the gate sees if no plan
                # is built: a missing estimate refuses by name.
                execution_estimate={"p_fill": None,
                                    "basis": "P_FILL_NOT_IDENTIFIED",
                                    "crossing": True},
                size=None,
                risk={"permitted": False,
                      "reason": "NO_EXECUTION_PLAN_WAS_BUILT"},
                fee_fn=fee_fn,
                now=now,
                outcome_books=quote["depth"].get(str(quote["home"])),
                armed=True,
                # INVERTED ONCE, INSIDE evaluate. The loop does not
                # pre-invert the probability; it states which event the
                # contract pays on and lets the one place that owns the
                # comparison do the arithmetic.
                payout_is_complement=bool(ident["payout_is_complement"]),
                extra_refusals=extra,
                record_purpose=(ext.PURPOSE_ENTRY_DECISION
                                if calibration_only is None
                                else ext.PURPOSE_CALIBRATION_ONLY),
                calibration_only_evidence=(
                    None if calibration_only is None else {
                        "venue_read_refusal": calibration_only["refusal"],
                        "venue_read_why": _sanitize(
                            calibration_only.get("venue_read_why") or "",
                            limit=240),
                        "book_currency": calibration_only["book_currency"],
                        "displayed_quote": calibration_only["displayed"],
                        "decision_instant_epoch_s": now,
                        "decision_lag_s": decision_lag_s}))
            if calibration_only is None:
                evaluated += 1
                step["evaluated"] += 1
            else:
                # EVERY GATE STATE THE RISK VERDICT WOULD HAVE READ, evaluated
                # from the same evidence -- freshness re-aged at this instant
                # (the venue side stays NOT_ESTABLISHED, so STALE_DATA is
                # unknown and blocks), the settlement comparison, the declared
                # support and the calibration read. No rail is evaluated: a
                # rail needs a proposed position and there is none.
                _cfr = _entry_freshness(quote, vq, now)
                rec["calibration_only_evidence"]["state_gates"] = \
                    entryx.state_from_evidence(
                        freshness=_cfr,
                        settlement=_settlement_compatibility(srule),
                        probability=rec.get("probability"),
                        calibration=calibration)
                rec["calibration_only_evidence"]["freshness"] = {
                    k: _cfr.get(k) for k in (
                        "fresh", "why", "unknown_side", "pinnacle_age_s",
                        "pinnacle_limit_s", "pinnacle_provider_lag_s",
                        "pinnacle_our_processing_s",
                        "venue_currency_verdict", "venue_age_s",
                        "our_processing_delay_s")}
                rec["calibration_only_evidence"]["rails"] = (
                    "NOT_EVALUATED: a rail is measured against a proposed "
                    "position, and a calibration-only record proposes none")

            # ── THE MEASUREMENT, TAKEN AT THE DECISION ──────────────
            #
            # HERE, not earlier, because `now` is the decision instant and
            # the whole question is how old the inputs were WHEN THE
            # DECISION WAS MADE. Every figure comes from `_entry_freshness`,
            # which computed them from clocks already on the record -- the
            # provider's `last_update` and the receipt stamp taken at the
            # fetch. Nothing here is estimated or re-derived.
            #
            # `self_inflicted_stale` IS THE NUMBER THE LEVERS HAVE TO MOVE:
            # the provider handed us a quote INSIDE the limit and our own
            # accumulated processing pushed it outside. The 296-of-399
            # finding was exactly this population. It is computed as a
            # conjunction of two measured quantities, not inferred from the
            # refusal code, so a change in refusal naming cannot move it.
            #
            # EVALUATIONS ONLY. A calibration-only record reached no decision,
            # so it contributes no latency sample and no valid evaluation.
            _fr = _entry_freshness(quote, vq, now) \
                if calibration_only is None else {}
            _pl = _fr.get("pinnacle_provider_lag_s")
            _od = _fr.get("pinnacle_our_processing_s")
            _ag = _fr.get("pinnacle_age_s")
            if _pl is not None:
                lat["provider_lag_samples"].append(float(_pl))
            if _od is not None:
                lat["our_delay_samples"].append(float(_od))
            if _ag is not None:
                lat["age_samples"].append(float(_ag))
                if _ag > PINNACLE_MAX_AGE_S:
                    lat["stale_refusals"] += 1
                    if _pl is not None and _pl <= PINNACLE_MAX_AGE_S:
                        lat["self_inflicted_stale"] += 1
                else:
                    # A VALID EVALUATION IS ONE WHOSE PROBABILITY INPUT WAS
                    # INSIDE THE RULE. Not one that produced a BUY -- that
                    # is an economic outcome and belongs to a different
                    # question. Conflating them would let a cycle with no
                    # edge look like a cycle with no data.
                    lat["valid_evaluations"] += 1

            rec["venue_quote"] = vq
            rec["mapping"] = mapped
            rec["settlement"] = srule
            # THE COMPARISON AND WHAT SCOPED IT, on the row. A verdict
            # without the fixture evidence behind it cannot be rechecked:
            # the same prose reads differently under a different phase or
            # game format, and the provenance says which one applied.
            rec["settlement_comparison"] = dict(
                _settlement_compatibility(srule),
                quote_context=(ctx_ev or {}).get("context"),
                quote_context_why=(ctx_ev or {}).get("why"),
                scope_phase=fmeta.get("phase"),
                scope_game_format=fmeta.get("game_format"),
                fixture_source=fmeta.get("source"),
                fixture_source_url=fmeta.get("source_url"),
                fixture_retrieved_at=fmeta.get("retrieved_at"),
                fixture_game_pk=fmeta.get("game_pk"),
                fixture_venue_key=fmeta.get("venue_fixture_key"),
                fixture_source_match_id=fmeta.get("source_match_id"),
                fixture_read=fmeta.get("read"),
                # WHETHER THE LANE ACQUIRED IT THIS CYCLE, AND WHY NOT.
                # An absent scope used to be indistinguishable from an
                # unattempted one on the row.
                fixture_acquisition=fmeta.get("acquisition"),
                fixture_event_state=fmeta.get("event_state_raw"),
                fixture_play_has_begun=fmeta.get("play_has_begun"),
                venue_rules_read=bool((vevid or {}).get("rules_text")),
                venue_rules_source=(vevid or {}).get("rules_source"),
                # THE VENUE'S OWN WORDS, kept with the verdict they produced,
                # so the comparison can be re-run and audited from the row.
                venue_rules_text=(str((vevid or {}).get("rules_text"))[:4000]
                                  if (vevid or {}).get("rules_text")
                                  else None),
                venue_rules_sha256=(
                    hashlib.sha256(str((vevid or {}).get("rules_text"))
                                   .encode("utf-8")).hexdigest()
                    if (vevid or {}).get("rules_text") else None),
                # WHERE AND WHEN THE WORDS WERE READ: the listing field they
                # came from, the reader, the instant of the venue read (an
                # hourly-cached answer keeps its ORIGINAL read instant and
                # says it was cached), and a named failure when none came.
                venue_rules_field=((vevid or {}).get("rules_read")
                                   or {}).get("rules_field"),
                venue_rules_reader=((vevid or {}).get("rules_read")
                                    or {}).get("reader"),
                venue_rules_retrieved_at=((vevid or {}).get("rules_read")
                                          or {}).get("read_at"),
                venue_rules_from_cache=((vevid or {}).get("rules_read")
                                        or {}).get("from_cache"),
                venue_rules_error=((vevid or {}).get("rules_read")
                                   or {}).get("error"))
            # Persist actual source + epoch/clocks beside settlement evidence.
            # Invalid authority/input removes probability as well as admission:
            # paper policies may intentionally disregard other lane refusals.
            from .. import pinnapi_primary as primary
            primary.stamp_record(rec, quote, reference_check)
            if calibration_only is not None:
                # ── A CALIBRATION-ONLY RECORD IS WRITTEN AND GOES NO FURTHER ──
                #
                # THE BOUNDARY, IN CODE AS WELL AS IN THE SCHEMA. This branch
                # returns to the loop in every outcome, so no calibration-only
                # record reaches the admission block below: no payout binding,
                # no inventory plan or write, no funded attempt, no in-cycle
                # reservation. Each of those refuses such a record by name as
                # well, and migration 144 refuses it as a row -- three layers,
                # none relying on `admissible`.
                try:
                    cal_id = await ext.persist(conn, rec)
                except Exception as exc:                       # noqa: BLE001
                    # A FAILED WRITE IS COUNTED BY NAME, in the tally and on
                    # the ledger line, never absorbed: on a database without
                    # migration 144 this is where the missing column shows.
                    name = "CALIBRATION_ONLY_PERSIST:" + type(exc).__name__
                    cal_only["persist_errors"][name] = \
                        cal_only["persist_errors"].get(name, 0) + 1
                    tally[name] = tally.get(name, 0) + 1
                    _vq_entry["calibration_only_record"] = name
                    continue
                if cal_id is None:
                    # THE SAME OBSERVATION, ALREADY RECORDED (migration 105/106).
                    cal_only["already_recorded"] += 1
                    _vq_entry["calibration_only_record"] = "ALREADY_RECORDED"
                    continue
                cal_only["recorded"] += 1
                step["recorded_for_calibration_only"] = \
                    step.get("recorded_for_calibration_only", 0) + 1
                for _c in rec.get("refusals") or []:
                    cal_only["refusals"][_c] = \
                        cal_only["refusals"].get(_c, 0) + 1
                if len(cal_only["rows"]) < 10:
                    cal_only["rows"].append({
                        "valuation_id": cal_id,
                        "us_market_slug": ident.get("us_market_slug"),
                        "event_key": quote.get("event_id"),
                        "probability": rec.get("probability"),
                        "venue_read_refusal": calibration_only["refusal"],
                        "displayed_price_not_for_orders": (
                            calibration_only["displayed"]
                            .get("acquisition_price"))})
                _vq_entry["calibration_only_record"] = "RECORDED"
                _vq_entry["calibration_only_valuation_id"] = cal_id
                if stream_seed is not None:
                    stream_seed["valuation_ids"].append(cal_id)
                await _paper_valuation(conn, cal_id)
                continue
            try:
                row_id = await ext.persist(conn, rec)
                if row_id is None:
                    # ALREADY RECORDED. The provider's quote has not moved
                    # since a previous cycle, so this is the same
                    # observation, not a new one. Counting it again is the
                    # duplicate accounting migration 105 exists to stop.
                    tally["DUPLICATE_OBSERVATION_SKIPPED"] = \
                        tally.get("DUPLICATE_OBSERVATION_SKIPPED", 0) + 1
                    continue
                written += 1
                step["written"] += 1
            except Exception as exc:                           # noqa: BLE001
                tally["PERSIST:" + type(exc).__name__] = \
                    tally.get("PERSIST:" + type(exc).__name__, 0) + 1
                continue
            if stream_seed is not None:
                stream_seed["valuation_ids"].append(row_id)
            await _paper_valuation(conn, row_id)
            if stream_seed is not None:
                # Never reach inventory or funded execution on a WS wakeup.
                continue
            key = "ADMITTED" if rec.get("admissible") else None
            if key:
                tally[key] = tally.get(key, 0) + 1
                _ledger({"global_slug": mapped.get("global_slug")
                         or (mapped.get("market_row") or {}).get("slug"),
                         "us_market_slug": ident.get("us_market_slug"),
                         "priced_outcome": quote.get("home"),
                         "stage": "ADMITTED", "first_refusal": None,
                         "edge": rec.get("edge")})
                # AN ADMITTED DECISION BECOMES INVENTORY. Writing the
                # valuation row alone is a decision record, not an entry:
                # nothing would hold a position, owe a fee, carry
                # residual quantity or have to reconcile. The rows land
                # in the same four tables the managed position uses, so
                # the existing management cycle picks this up as an
                # ordinary open position on its next pass.
                try:
                    # THE OUTCOME INDEX IS READ AND CROSS-CHECKED, never
                    # derived from the venue intent. An unbindable or
                    # disagreeing outcome refuses the inventory write: the
                    # valuation row stands, but nothing is held under an
                    # index nobody established.
                    bound = await bind_payout_outcome(
                        conn, condition_id=mapped["condition_id"],
                        payout_event=rec.get("payout_event"),
                        intent=ident["intent"],
                        # THE RECORD'S OWN PURPOSE, so the binder refuses a
                        # calibration-only record even if one got here.
                        record_purpose=rec.get("record_purpose"))
                    rec["payout_binding"] = bound
                    if not bound.get("ok"):
                        code = bound.get("refusal") or R_OUTCOME_NOT_BOUND
                        tally[code] = tally.get(code, 0) + 1
                        entries.append({"written": False,
                                        "refusals": [code],
                                        "condition_id":
                                            mapped["condition_id"],
                                        "payout_binding": bound})
                        continue
                    # THE IDENTITY GOES ON THE POSITION, not left to be
                    # rediscovered later by matching on condition alone.
                    # `buy_intent` and `ladder_side` live on the CONTRACT
                    # (that is where `persist` reads them from), so they
                    # are lifted onto the record the writer sees, and the
                    # valuation row id is the link back to the decision
                    # that admitted this. See migration 119.
                    rec["valuation_row_id"] = row_id
                    rec["buy_intent"] = contract.get("buy_intent")
                    rec["ladder_side"] = contract.get("ladder_side")
                    rec["us_market_slug"] = contract.get("us_market_slug")
                    plan = inv.plan_entry(
                        rec, now=now,
                        outcome_index=bound["outcome_index"],
                        fee_fn=fee_fn)
                    wrote = await inv.persist_entry(conn, plan)
                    rec["inventory"] = wrote
                    # ── THE FUNDED PATH, CALLED FROM THE SCHEDULE ───
                    #
                    # THE GAP THIS CLOSES. This worker had NO ORDER PATH at
                    # all: `api/app.py` recorded that it "reaches
                    # pmus.book_read and nothing else on that module", so a
                    # qualifying decision could never reach a venue however
                    # complete the connector was. It is called here, on the
                    # SAME admitted record the shadow inventory was written
                    # from, so the two can never disagree about what the
                    # decision said.
                    #
                    # AND IT SENDS NOTHING TODAY. `FUNDED_SUBMISSION_ENABLED`
                    # is False, so the connector runs every gate it has and
                    # returns before the adapter is reached. The call is here
                    # rather than absent because an unwired path cannot be
                    # tested, and because what it refuses on is the thing
                    # worth reading in a cycle report.
                    rec["event_key"] = quote.get("event_id")
                    rec["order_intent"] = contract.get("buy_intent")
                    funded = await _funded_attempt(conn, rec, now=now)
                    if funded is not None:
                        rec["funded"] = funded
                        tally["FUNDED:" + str(funded.get("refusal")
                                              or "SUBMITTED")] = tally.get(
                            "FUNDED:" + str(funded.get("refusal")
                                            or "SUBMITTED"), 0) + 1
                    entries.append({
                        "position_id": wrote.get("position_id"),
                        "decision_id": wrote.get("decision_id"),
                        "order_id": wrote.get("order_id"),
                        "written": wrote.get("written"),
                        "refusals": wrote.get("refusals"),
                        "accounting": wrote.get("accounting")})
                    if wrote.get("written"):
                        tally["ENTRY_INVENTORY_WRITTEN"] = \
                            tally.get("ENTRY_INVENTORY_WRITTEN", 0) + 1
                        # ── THE BOOK IS RESERVED AGAINST, IMMEDIATELY ──
                        #
                        # THE DEFECT THIS CLOSES. `open_book` is read once
                        # per cycle, so every candidate after the first
                        # measured its exposure against a book that did
                        # not contain the positions this cycle had just
                        # created. Two $600 entries, each admissible alone
                        # against an empty book, both cleared a $1,000
                        # combined-exposure rail -- and the rail is the
                        # one thing standing between a capped pilot and an
                        # uncapped one.
                        #
                        # A RESERVATION, NOT A RE-READ. The row was
                        # written inside a transaction that has committed,
                        # so appending it here is the same fact the next
                        # read would return, without a second round trip
                        # per candidate. It carries the COST BASIS
                        # (including fees) and the filled quantity, which
                        # are exactly what the rails are measured in.
                        acct = wrote.get("accounting") or {}
                        if open_book is not None:
                            open_book.append({
                                "condition_id": mapped["condition_id"],
                                "venue_market_slug":
                                    ident.get("us_market_slug"),
                                "event_key": quote["event_id"],
                                "cost_usd": acct.get("cost_basis_usd"),
                                "qty": acct.get("filled_qty"),
                                "opened_at": now,
                                "reserved_in_this_cycle": True})
                            tally["EXPOSURE_RESERVED"] = \
                                tally.get("EXPOSURE_RESERVED", 0) + 1
                    else:
                        for code in wrote.get("refusals") or []:
                            tally[code] = tally.get(code, 0) + 1
                except Exception as exc:                       # noqa: BLE001
                    # THE VALUATION ROW IS ALREADY DOWN. An inventory
                    # write that fails must not erase the decision that
                    # preceded it, so this is counted and named rather
                    # than raised.
                    tally["ENTRY_INVENTORY:" + type(exc).__name__] = \
                        tally.get("ENTRY_INVENTORY:"
                                  + type(exc).__name__, 0) + 1
            else:
                for code in rec.get("refusals", []):
                    tally[code] = tally.get(code, 0) + 1
                first = (rec.get("refusals") or [None])[0]
                _ledger({"global_slug": mapped.get("global_slug")
                         or (mapped.get("market_row") or {}).get("slug"),
                         "us_market_slug": ident.get("us_market_slug"),
                         "priced_outcome": quote.get("home"),
                         "stage": ext.STAGE_OF.get(first, "UNMAPPED_STAGE"),
                         "first_refusal": first,
                         "all_refusals": list(rec.get("refusals") or [])[:6],
                         "edge": rec.get("edge"),
                         "estimated_edge_is_positive": (
                             None if rec.get("edge") is None
                             else float(rec["edge"]) > 0.0)})

    _close_event()
    _t_entry = time.time()
    candidate_outcomes = _reconcile_event_ledger(event_ledger, funnel)
    candidate_outcomes["persisted"] = await _persist_candidate_outcomes(
        conn, cycle_at=started, rows=event_ledger)
    _t_persisted = time.time()
    if stream_seed is not None:
        return {"ran": True, "state": "WS_PAPER_EVALUATED", "written": written,
                "refusals": dict(tally), "candidate_outcomes": candidate_outcomes,
                "valuation_ids": list(stream_seed["valuation_ids"])}

    # THE OUTCOME JOIN RUNS EVERY CYCLE, bounded. Collection has to
    # progress on its own: a calibration that waits for someone to
    # remember to run a backfill is a calibration that never happens.
    joined = await join_outcomes(conn)
    _t_joined = time.time()
    # THE CALIBRATION MEASUREMENT, SCHEDULED, right after the join that
    # feeds it. At most once per CALIBRATION_MEASURE_EVERY_S; see
    # `_scheduled_calibration_measurement`. It writes only a completed
    # verdict and changes nothing the entry gate requires.
    calibration_measurement = await _scheduled_calibration_measurement(
        conn, now=time.time())
    _t_calibrated = time.time()
    pair_observation = await _pair_observation_pass(conn, observable,
                                                    now=time.time())
    _t_observed = time.time()
    # WHERE THE CYCLE'S TIME WENT, per step, so the cadence can be read from
    # the heartbeat rather than inferred from `elapsed_s` alone. `servicing`
    # is ~0 while the servicing task is alive: the cycle then services nothing.
    step_timing_s = {
        "servicing_in_cycle": round(_t_serviced - started, 3),
        "entry_lane": round(_t_entry - _t_serviced, 3),
        "candidate_outcomes": round(_t_persisted - _t_entry, 3),
        "outcome_join": round(_t_joined - _t_persisted, 3),
        "calibration_measurement": round(_t_calibrated - _t_joined, 3),
        "pair_observation": round(_t_observed - _t_calibrated, 3)}

    out = {"ran": True, "state": "LIVE",
           "step_timing_s": step_timing_s,
           "pair_observation": pair_observation,
           "experiment_id": ext.EXPERIMENT_ID,
           "outcome_join": joined,
           "source_calibration_measurement": calibration_measurement,
           "funded_servicing": funded_service,
           "xavier_review": xavier_review,
           "evaluated": evaluated, "written": written,
           # THE NAMED COUNTER, beside `written` and never inside it: rows
           # recorded for calibration only, whose events stay counted under
           # their venue-read refusal in `refusals`.
           C_CALIBRATION_ONLY_RECORDED: cal_only["recorded"],
           "calibration_only": cal_only,
           "refusals": tally, "credits": credits,
           # ── THE FRESHNESS KNOB, AND WHAT IT COST ──────────────────
           # Both counters are 0 at the default setting, where no extra
           # fetch is ever issued. Reported unconditionally so that a
           # change in credit spend can be attributed rather than guessed.
           # ── THE MEASURED LATENCY PATH ────────────────────────────
           #
           # MEDIANS, NOT MEANS. One 400-second outlier -- a provider
           # hiccup, a venue timeout -- drags a mean far enough to make a
           # real improvement invisible, and the question here is what a
           # TYPICAL candidate experienced. The max is carried alongside so
           # the outlier is still visible rather than smoothed away.
           #
           # AN EMPTY SAMPLE IS None, NEVER 0. A cycle that evaluated
           # nothing has no measured delay; reporting 0.0 would read as
           # "instant" and would be the best-looking number in the table.
           "latency": {
               "provider_lag_s": _median(lat["provider_lag_samples"]),
               "provider_lag_max_s": _maxof(lat["provider_lag_samples"]),
               "our_processing_s": _median(lat["our_delay_samples"]),
               "our_processing_max_s": _maxof(lat["our_delay_samples"]),
               "pinnacle_age_s": _median(lat["age_samples"]),
               "pinnacle_age_max_s": _maxof(lat["age_samples"]),
               "limit_s": PINNACLE_MAX_AGE_S,
               "samples": len(lat["age_samples"]),
               "valid_evaluations": lat["valid_evaluations"],
               "stale_refusals": lat["stale_refusals"],
               "self_inflicted_stale": lat["self_inflicted_stale"],
               "provider_stale_on_arrival": lat["provider_stale_on_arrival"],
               # THE OTHER HALF OF THE SKIPS, BY NAME: the provider was inside
               # the limit and our own processing took the price past it.
               "stale_on_arrival_due_to_our_processing":
                   lat["stale_on_arrival_due_to_our_processing"],
               "skipped_stale_on_arrival": lat["skipped_stale_on_arrival"],
               # `samples` now includes the events lever A skipped (their
               # clocks at the skip instant); this many of them.
               "samples_from_arrival_skips": lat["arrival_skip_samples"],
               # AND FOR EVERY EVENT THAT HAD A PINNACLE PRICE, measured when
               # the cycle reached it -- including the events refused at
               # mapping or identity, which the figures above never saw.
               "on_arrival_every_priced_event": {
                   "events": len(lat["arrival_lag_every_priced"]),
                   "provider_lag_s": _median(lat["arrival_lag_every_priced"]),
                   "provider_lag_max_s": _maxof(
                       lat["arrival_lag_every_priced"]),
                   "our_processing_s": _median(
                       lat["arrival_ours_every_priced"]),
                   "our_processing_max_s": _maxof(
                       lat["arrival_ours_every_priced"])},
               "deduplicated_requests": lat["deduplicated_requests"],
               "venue_requests": lat["venue_requests"],
               # WHAT WAS NOT EXAMINED, so throughput cannot be confused
               # with quietly dropping the difficult cases.
               "deferred_candidates": deferred_total,
               "deferred_sample": deferred,
               "deferred_sample_bounded_at": MAX_DEFERRED_REPORTED,
               "deferred_is_not_refused": (
                   "a deferred candidate was never judged. MAX_PER_CYCLE "
                   "was reached first, and the reordering means the sort "
                   "chooses who is deferred -- so they are named"),
               "what_self_inflicted_means": (
                   "the provider handed us a quote INSIDE the 30 s rule and "
                   "our own accumulated processing pushed it outside. 296 of "
                   "399 fair-value failures were this population. It is the "
                   "number the levers have to move"),
               "the_limit_did_not_move": (
                   "every figure here is measured against the same "
                   "PINNACLE_MAX_AGE_S the decision gate applies. Levers A, "
                   "B and C change which candidates get the freshest slots "
                   "and stop spending reads on candidates already outside "
                   "the limit. None of them relaxes it"),
               "levers": {
                   "A_skip_stale_on_arrival": (
                       "a candidate already past the limit before any venue "
                       "read is refused by name (QUOTE_STALE_ON_ARRIVAL) "
                       "and its read is not spent -- so it stops making its "
                       "successors stale too"),
                   "B_freshest_first": (
                       "events are ordered by the provider's own "
                       "last_update, newest first, so the low-delay slots "
                       "go to the quotes with the most headroom. Nothing is "
                       "dropped and no limit moves"),
                   "C_dedupe_equivalent_only": (
                       "two provider events resolving to the same "
                       "(us_market_slug, intent) share one venue read. Each "
                       "keeps its own observation time and is re-aged "
                       "against its OWN decision instant, so a shared read "
                       "cannot lend freshness to a later decision"),
               },
           },
           "odds_freshness": {
               "events_per_odds_fetch": EVENTS_PER_ODDS_FETCH,
               "max_per_cycle": MAX_PER_CYCLE,
               "is_the_default": EVENTS_PER_ODDS_FETCH >= MAX_PER_CYCLE,
               "odds_refetches": odds_refetches,
               "odds_refetch_failures": odds_refetch_failures,
               "what_it_is_for": (
                   "399 of 1,126 evaluation rows produced no fair value and "
                   "every one failed at QUOTE_STALE. 296 of those 399 were "
                   "stale because of OUR accumulated processing delay, not "
                   "the provider's lag. This bounds how many events share "
                   "one provider quote"),
               "why_it_is_not_on_by_default": (
                   "lowering it buys freshness with provider credits, "
                   "roughly in proportion. That is a resource decision, so "
                   "the default changes nothing"),
               "refetch_is_not_a_retry": ODDS_REFETCH_IS_NOT_A_RETRY,
           },
           "venue_errors": venue_errors,
           "markets_considered": len(markets),
           # WHAT WAS AVAILABLE, AND WHERE EACH SPORT STOPPED. See the
           # comment at `universe` above: one aggregate count could not say
           # whether a supported sport contributed anything.
           "venue_universe_by_label": universe,
           # WHICH COMPETITIONS THIS CYCLE WAS ALLOWED TO SPEND ON, and every
           # candidate it refused with the reason. A short funnel used to be
           # indistinguishable from a narrow sport set.
           "sports_selection": sports_selection,
           "funnel_by_provider_sport": funnel,
           # EVERY MAPPED CANDIDATE, RECONCILED TO ITS FIRST REFUSAL.
           "mapped_candidate_ledger": ledger,
           # EVERY PROVIDER EVENT, mapped or not, with its identity and its
           # outcome -- and whether the rows add up to the funnel.
           "candidate_outcomes": candidate_outcomes,
           "event_ledger": event_ledger,
           # THE CYCLE'S OWN LABEL, and the distinction it protects.
           # "zero positive edge" is a claim ABOUT THE MARKET. It can only
           # be made when candidates actually reached the economics. When
           # nothing reached execution estimation, the truthful label is
           # that the INPUT PATH was blocked -- the market was never
           # measured and nothing here says anything about it.
           "cycle_label": (
               ZERO_EVALUATED_INPUT_PATH_BLOCKED if evaluated == 0
               else "EVALUATED_%d_CANDIDATES" % evaluated),
           "cycle_label_note": (
               "with evaluated == 0 no candidate reached execution "
               "estimation, so this cycle establishes NOTHING about "
               "available edge. It is an input-path fact"),
           "entries": entries,
           "source_calibration": calibration,
           "research_shadow": research,
           "open_book_rows": (None if open_book is None else len(open_book)),
           "elapsed_s": round(time.time() - started, 2),
           "order_submitted": False}
    # ── AGENTS (core): DEREK'S after_cycle ON THIS CYCLE'S RESULT ─────
    # Guarded: a missing or failing Derek records its own state and the
    # cycle's result and heartbeat are unchanged.
    out["agents"] = await _derek_after_cycle(conn, out)
    await _heartbeat(conn, out)
    return out


async def _paper_valuation(conn, valuation_id) -> None:
    """PAPER TRADING: Derek's paper decision on this valuation at the instant
    it was written (`agents.runtime.paper_valuation_hook`). Returns at once
    when PAPER_SESSION is unset; bounded; never raises into the cycle; sends
    nothing to the venue (the paper path reads market data only)."""
    if str(os.environ.get("PAPER_SESSION", "")).strip().lower() not in (
            "on", "1", "true", "yes"):
        return
    try:
        await _agents_runtime().paper_valuation_hook(
            conn, valuation_id=valuation_id)
    except asyncio.CancelledError:
        raise
    except Exception:                                          # noqa: BLE001
        log.warning("ext_pinnacle: paper valuation hook failed",
                    exc_info=True)


async def _derek_heartbeat_start(conn, *, now: float) -> None:
    """Derek EVALUATING at the top of the cycle. Never raises."""
    try:
        await _agents_runtime().derek_cycle_started(conn, now=now)
    except asyncio.CancelledError:
        raise
    except Exception:                                          # noqa: BLE001
        log.warning("ext_pinnacle: derek start heartbeat failed",
                    exc_info=True)


async def _derek_after_cycle(conn, result: dict) -> dict:
    """`agents.derek.after_cycle(conn, cycle=result, now=now)` through the
    guarded runtime, then Derek's truthful end state. Never raises."""
    try:
        return await _agents_runtime().derek_cycle_finished(
            conn, cycle=result, now=time.time())
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


HEARTBEAT_KEY = "ext_pinnacle_last_cycle"

#: THE STANDBY'S OWN KEY. A process that holds no lock writes no
#: valuations, so it has no cycle to report -- but it must still be
#: visible, because "a second process is up and waiting" is worth knowing.
#: It gets its own row. Sharing HEARTBEAT_KEY made the standby's empty
#: cycle the thing every read returned.
STANDBY_KEY = "ext_pinnacle_last_cycle_standby"

#: ITS OWN ROW, WRITTEN ONCE PER BOOT. What the process found in storage at
#: startup and which controls it re-armed. On the cycle row it would be
#: overwritten within a cycle and the restart boundary -- the only thing this
#: record is evidence of -- would be unobservable a minute later.
COOLDOWN_RESUME_KEY = "ext_pinnacle_cooldown_resume"


def _venue_sdk_digest() -> dict:
    """The dependency position, small enough for a heartbeat row.

    A SUBSET, not the whole report: the heartbeat is read on every cycle
    and the full retry-facts block belongs in a diagnostic, not in a row
    written every sixty seconds. The fields kept are the ones an operator
    would act on -- which build is running, whether it is the chosen one,
    and whether its own retries are off.

    NEVER RAISES. A heartbeat that can fail on a version lookup would
    lose the whole refusal census with it.
    """
    try:
        from .. import venue_sdk
        rep = venue_sdk.report()
        return {
            "pinned": rep["pinned"],
            "installed": rep["installed"],
            "pinned_matches_installed": rep["pinned_matches_installed"],
            "sdk_retries_disabled": rep["sdk_retries_disabled"],
            "our_max_dispatches_per_read": _max_dispatches(),
            "post_is_retried_by_the_sdk":
                (rep.get("sdk_retry_facts") or {}).get("post_is_retried"),
            "refusals": rep["refusals"],
        }
    except Exception as exc:                                   # noqa: BLE001
        return {"unavailable": type(exc).__name__}


def _max_dispatches():
    """The per-read dispatch budget, READ FROM `venue_sdk`, NOT FROM `pmus`.

    THE CONTAINMENT THIS RESPECTS. Two tests walk this module's AST and
    assert that the only attributes taken off `pmus` are `book_read` and
    `_get_client` -- because `pmus` also defines the funded order submitter,
    and an entry lane that can reach it is one careless refactor from
    submitting. Reading a constant off it broke that, and the failing tests
    were right: the rule is about the reachable surface, not about intent.

    AND THE SUBMITTER IS NOT NAMED IN THIS COMMENT, deliberately. A sibling
    test asserts by plain string search that this module's source contains
    none of the funded call names, precisely because an AST check can be
    fooled by a call built from a string. Writing the name in prose to
    explain the rule set off the rule -- correctly. The reasoning is the
    useful part; the token is not.
    """
    try:
        from .. import venue_sdk
        return venue_sdk.BOOK_READ_MAX_DISPATCHES
    except Exception:                                          # noqa: BLE001
        return None


def _rate_control_digest() -> dict:
    """The prohibition and the reduced rate, SEPARATELY.

    Reported as two fields with two names because they are two controls.
    The previous single "cooldown" number was a reduced rate being read as
    a prohibition, and an operator could not have told from the row.

    `queued_but_not_yet_durable` is included deliberately: a cooldown
    observed on the read path is durable only after the next drain, and
    that window is a real gap rather than something to leave implicit.
    """
    out = {}
    try:
        from .. import venue_request_gate as grt
        g = grt.gate_state()
        out["not_before"] = {
            "blocking_right_now": g["blocking"],
            "seconds_left": round(g["seconds_left"], 3),
            "reason": g["reason"],
            "what_this_is": "A PROHIBITION: no request dispatches before it",
        }
        out["process_request_totals"] = grt.totals()
    except Exception as exc:                                   # noqa: BLE001
        out["not_before"] = {"unavailable": type(exc).__name__}
    # ── WHETHER RESEARCH TRAFFIC IS ACTUALLY STARVING SERVICING ──────
    #
    # THE QUESTION THIS MAKES ANSWERABLE, AND THE CHANGE IT REPLACES.
    # The instruction is to throttle lower-priority research traffic first
    # and preserve capacity for reconciliation and servicing. The obvious
    # implementation is to promote the servicing reads into the pacer's
    # existing PRIORITY lane -- and that lane currently has exactly one
    # claimant, the frozen mirror tick. Adding claimants to it changes the
    # scheduling that frozen lane sees.
    #
    # There is no evidence yet that it should be changed. The measured
    # blocker was ONE 429 on ONE candidate's book read, not a servicing read
    # starved behind research. Re-scheduling a frozen lane on the strength
    # of a plausible story is the same error as reporting a control that
    # does not control.
    #
    # So the contention is MEASURED instead. `lane_stats()` already counts
    # the seconds each lane's claims spent waiting and how many there were;
    # it simply had no reader. With it on the heartbeat, "research is
    # starving servicing" becomes a claim production can settle, and the
    # scheduling change can follow evidence instead of preceding it.
    try:
        from .. import venue_pace as vp2
        out["pacer_lanes"] = dict(
            vp2.lane_stats(),
            scope="PROCESS_SINCE_IMPORT",
            read_as="deltas between heartbeats, not absolutes",
            what_is_in_each_lane={
                "priority": "the frozen mirror tick only",
                "normal": ("this lane's candidate book reads AND its "
                           "reconciliation and servicing reads -- they "
                           "currently compete equally, which is the thing "
                           "these numbers exist to test"),
            })
    except Exception as exc:                                   # noqa: BLE001
        out["pacer_lanes"] = {"unavailable": type(exc).__name__}
    try:
        from .. import venue_pace as vp
        c = vp.cooldown_state()
        out["reduced_rate"] = {
            "active": c["active"],
            "seconds_left": round(c["seconds_left"], 3),
            "multiplier_while_active": c["multiplier_while_active"],
            "retry_after_s": c["retry_after_s"],
            "what_this_is": ("A SLOWER RATE, not a prohibition: it "
                            "multiplies the inter-request gap"),
        }
    except Exception as exc:                                   # noqa: BLE001
        out["reduced_rate"] = {"unavailable": type(exc).__name__}
    try:
        from .. import venue_cooldown_store as vcs
        out["queued_but_not_yet_durable"] = bool(vcs.pending() is not None)
        out["persistence"] = vcs.drain_stats()
    except Exception as exc:                                   # noqa: BLE001
        out["persistence"] = {"unavailable": type(exc).__name__}
    return out


def _code_identity() -> dict:
    """WHAT CODE THE ACTIVE WRITER IS RUNNING, from the writer itself.

    A deploy id says what the service was asked to run. This says what the
    process that wrote this heartbeat is actually executing: a digest of
    this module's own source, plus the build marker when the platform sets
    one. Verifying a fix by reading the deploy id assumes the restart
    happened and the import succeeded; this does not.
    """
    import hashlib
    import inspect
    import os
    import sys

    try:
        src = inspect.getsource(sys.modules[__name__]).encode()
        digest = hashlib.sha256(src).hexdigest()[:12]
    except Exception:                                          # noqa: BLE001
        digest = None
    return {"module": __name__, "source_sha256_12": digest,
            "build": (os.getenv("RENDER_GIT_COMMIT")
                      or os.getenv("GIT_COMMIT") or None),
            "pid": os.getpid()}


#: How many positions' decisions the heartbeat carries. The funded lane
#: holds one open ENTRY at a time by unique index, so this is headroom,
#: not a cap anyone should hit -- and it is bounded because a heartbeat
#: row that grows with the book is a heartbeat that eventually fails to
#: write, silently, taking the whole tile with it.
SERVICING_DIGEST_LIMIT = 8


def _learning_digest(lp) -> dict | None:
    """The scheduled learning pass, bounded for the heartbeat."""
    if not isinstance(lp, dict):
        return None
    j = lp.get("join") or {}
    e = lp.get("evaluate") or {}
    g = lp.get("generate") or {}
    w = lp.get("withdraw") or {}
    return {"ok": lp.get("ok"), "refusal": lp.get("refusal"),
            "generate": {k: g.get(k) for k in (
                "ok", "refusal", "generated", "reason", "model_id",
                "n_events", "training_events_available", "error")},
            "join": {k: j.get(k) for k in ("ok", "refusal", "examined",
                                           "without_a_group", "error")},
            "joined": len(j.get("joined") or []),
            "waiting_for_the_position": len(
                j.get("waiting_for_the_position") or []),
            "join_refused": len(j.get("refused") or []),
            "evaluate": {k: e.get(k) for k in ("ok", "refusal", "error")},
            "candidates_not_scored_this_pass": lp.get(
                "candidates_not_scored_this_pass"),
            "candidates_scored": [
                {k: c.get(k) for k in (
                    "model_id", "ok", "refusal", "prospective_events",
                    "prospective_log_loss",
                    "retrospective_out_of_sample_events",
                    "retrospective_out_of_sample_log_loss", "weighting",
                    "awaiting", "error")}
                for c in (e.get("scored") or [])[:SERVICING_DIGEST_LIMIT]],
            "withdraw": _withdraw_digest(w),
            "promoted_anything": lp.get("promoted_anything"),
            "promotion": lp.get("promotion")}


def _withdraw_digest(w) -> dict | None:
    """`withdraw_invalidated`'s answer: whether an approved model lost its
    pricing authority this pass, and why -- or why it did not."""
    if not isinstance(w, dict) or not w:
        return None
    return {k: w.get(k) for k in ("ok", "refusal", "approved_model",
                                  "withdrawn", "reason",
                                  "not_withdrawn_because",
                                  "provenance_verified", "error")}


async def _xavier_daily_review(conn, *, now):
    """XAVIER'S DAILY REVIEW, after the learning pass, once per UTC day.

    RUNS WHETHER OR NOT A FUNDED ACCOUNT IS BOUND: with none bound there are
    still models to summarise, a calibration to read and past decisions to
    score. EVERY ACCOUNT'S decisions are reviewed, not only the bound one's:
    a re-bound account must not leave the previous account's decisions
    unscored, and the review has no authority, so reading more costs nothing.
    It writes only its own review row (and retires an approval whose records
    no longer reproduce, which removes authority and grants none).
    NEVER FATAL: a failure is returned for the heartbeat and retried next
    cycle, because the review row for the day was not written."""
    from .. import bettor_xavier_review as _XR

    try:
        return await _XR.daily_review(conn, now=now)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "ran": False, "refusal": _XR.R_RAISED,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def _xavier_review_digest(xr) -> dict | None:
    """The daily review, bounded for the heartbeat: the last review date,
    how many decisions it read, how many had authoritative outcomes, how many
    were invalidated, and when the next is due -- or why it did not run."""
    if not isinstance(xr, dict):
        return None
    d = xr.get("digest") if isinstance(xr.get("digest"), dict) else {}
    return {"ok": xr.get("ok"), "ran": xr.get("ran"),
            "refusal": xr.get("refusal"), "error": xr.get("error"),
            "review_date": xr.get("review_date"),
            "last_review_date": d.get("last_review_date"),
            "decisions_reviewed": d.get("decisions_reviewed"),
            "decisions_with_outcomes": d.get("decisions_with_outcomes"),
            "invalidated": d.get("invalidated"),
            "next_due_at": d.get("next_due_at")}


def _settlement_digest(reads) -> dict | None:
    """The per-position settlement reads `manage` made, counted by result."""
    if reads is None:
        return None
    if not isinstance(reads, list):
        return {"unreadable_shape": type(reads).__name__}
    by: dict = {}
    for r in reads:
        r = r if isinstance(r, dict) else {}
        k = ("CLOSED" if r.get("closed") else
             str(r.get("refusal") or r.get("status") or r.get("why")
                 or "OPEN"))[:120]
        by[k] = by.get(k, 0) + 1
    return {"positions_read": len(reads), "by_result": by}


def _recheck_digest(rc) -> dict | None:
    """The re-reads of recently settled funded legs (migration 141)."""
    if not isinstance(rc, dict):
        return None
    out = {k: rc.get(k) for k in ("ok", "refusal", "error",
                                  "disagreements")}
    by: dict = {}
    for r in rc.get("rechecked") or []:
        v = str((r or {}).get("verdict") or "UNNAMED")
        by[v] = by.get(v, 0) + 1
    out["rechecked"] = len(rc.get("rechecked") or [])
    out["by_verdict"] = by
    return out


#: The heartbeat row's ceiling. Nothing in the database bounds
#: `ingestion_state.value`; past this the per-row samples are emptied (and
#: the row says so) while every count and named refusal is kept.
HEARTBEAT_MAX_BYTES = 262144


def _observation_digest(po) -> dict | None:
    """THE NON-FUNDED OBSERVER'S LAST PASS, for the heartbeat.

    Every count and every named refusal; a bounded sample of the attempts
    (the full list of each attempt is in `bettor_pair_observation_attempts`
    when migration 142 is present); what the labeller, generation and
    evaluation did; and the venue reads the pass spent. Never raises."""
    if po is None:
        return None
    try:
        if not isinstance(po, dict):
            return {"digest_failed": "not a dict: %s" % type(po).__name__}
        lab = po.get("labels") or {}
        gen = po.get("generate") or {}
        genc = po.get("generate_conditional") or {}
        ev = po.get("evaluate") or {}
        cat = po.get("catalogue") or {}
        attempts = []
        for a in (po.get("observed") or [])[:SERVICING_DIGEST_LIMIT]:
            a = a if isinstance(a, dict) else {}
            attempts.append(dict(
                {k: a.get(k) for k in (
                    "us_market_slug", "side", "source", "fixture", "outcome",
                    "refusal", "quote_refusal", "held_refusal",
                    "discovery_refusal", "examined", "admitted",
                    "second_legs_refused", "skipped_unpriced_second_leg",
                    "budget_limited", "elapsed_s", "reads", "error",
                    "conclusion", "sibling_categories", "siblings_total",
                    "siblings_truncated_at_limit", "siblings_eligible",
                    "siblings_examined", "siblings_deferred",
                    "siblings_exhausted", "observable_without_admission")},
                written=sum(1 for r in a.get("recorded") or []
                            if (r or {}).get("written"))))
        return {
            "ran": po.get("ran", "ok" in po),
            "why": po.get("why"),
            "ok": po.get("ok"), "refusal": po.get("refusal"),
            "error": po.get("error"),
            "pass_id": po.get("pass_id"), "at": po.get("at"),
            "elapsed_s": po.get("elapsed_s"), "budget_s": po.get("budget_s"),
            "stopped_for_deadline": po.get("stopped_for_deadline"),
            "lane_state": po.get("lane_state"),
            "candidates": {
                "offered": po.get("candidates_offered"),
                "by_source": po.get("candidates_offered_by_source"),
                "attempted": po.get("attempted"),
                "not_attempted": po.get("not_attempted")},
            "outcomes": po.get("outcomes"),
            "refusals": po.get("refusals"),
            "conclusions": po.get("conclusions"),
            "attempt_memory": po.get("attempt_memory"),
            "observations_written": po.get("observations_written"),
            # OF WHICH RECORDED WITHOUT ADMISSION (cancellation unresolved):
            # evidence, never an approval.
            "observations_written_not_admitted": po.get(
                "observations_written_not_admitted"),
            "attempts": attempts,
            "attempts_truncated_at": (
                SERVICING_DIGEST_LIMIT
                if len(po.get("observed") or []) > SERVICING_DIGEST_LIMIT
                else None),
            "attempt_ledger": po.get("attempt_ledger"),
            "catalogue": ({k: cat.get(k) for k in (
                "ok", "refusal", "why", "error", "sweep_age_s", "rows_read",
                "truncated_at_row_limit", "fixtures_seen",
                "eligible_fixtures", "excluded_rows", "excluded_fixtures",
                "not_offered_for_limit", "never_attempted_fixtures",
                "attempt_memory", "structural_census")} if cat else None),
            "labels": {k: lab.get(k) for k in (
                "ok", "refusal", "error", "labelled", "not_a_label",
                "awaiting", "corrected", "unreadable", "row_errors",
                "pairs_read", "pairs_selected", "stopped_for_deadline")},
            "generate": {k: gen.get(k) for k in (
                "ok", "refusal", "error", "reason", "generated", "model_id",
                "n_events", "training_events_available", "why")},
            # THE SECOND KEY OBSERVATIONS TRAIN: P(hedge wins | primary
            # outcome), which the payout-state distribution prices from.
            "generate_conditional": {k: genc.get(k) for k in (
                "ok", "refusal", "error", "model_key", "reason", "generated",
                "model_id", "n_events", "training_events_available", "why")},
            "evaluate": {
                "ok": ev.get("ok"), "refusal": ev.get("refusal"),
                "error": ev.get("error"),
                "candidates_waiting": ev.get("candidates_waiting"),
                "candidates_waiting_by_key": ev.get(
                    "candidates_waiting_by_key"),
                "scored": list((ev.get("scored") or [])
                               [:SERVICING_DIGEST_LIMIT]),
                "withdraw": _withdraw_digest(ev.get("withdraw")),
                "withdraw_by_key": {
                    k: _withdraw_digest(w) for k, w in
                    (ev.get("withdraw_by_key") or {}).items()},
                "promoted_anything": ev.get("promoted_anything")},
            "venue_usage": po.get("venue_usage"),
            "sent_anything": po.get("sent_anything"),
            "promoted_anything": po.get("promoted_anything"),
        }
    except Exception as exc:                                   # noqa: BLE001
        return {"digest_failed": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:160])}


def _outcome_join_digest(oj) -> dict | None:
    """`join_outcomes`: valuations joined to venue settlements this cycle,
    by result. It feeds the source-calibration cohort."""
    if not isinstance(oj, dict):
        return None
    return {k: oj.get(k) for k in (
        "ran", "error", "why", "examined", "resolved", "void", "pending",
        "unreadable", "unmatched", "errors", "named_winner", "inferred",
        "side_unknown", "unparseable", "neither_side_paid", "limit",
        "by_status", "by_class")}


def _last_run_of(result) -> dict | None:
    """The compact result of one `measure` run, as the heartbeat carries it."""
    from .. import bettor_source_calibration as _CAL

    if not isinstance(result, dict):
        return None
    resolved = result.get("resolved_fixtures")
    cohort = result.get("cohort_shortfall")
    if cohort is None and resolved is not None:
        cohort = _CAL.cohort_shortfall(resolved)
    return {"at": result.get("measured_at"),
            "ran": result.get("ran"),
            "status": result.get("status"),
            "resolved_fixtures": resolved,
            "scored_events": result.get("scored_events"),
            "shortfall": cohort,
            "would_write": result.get("would_write"),
            "written": result.get("written"),
            # A WRITTEN VERDICT SAYS WHICH WAY IT WENT; an unwritten one
            # has no verdict to report.
            "within_tolerance": (result.get("within_tolerance")
                                 if result.get("written") else None),
            "write_refused_why": result.get("write_refused_why"),
            "error": (result.get("error") or result.get("write_error"))}


def _calibration_measurement_digest(m) -> dict | None:
    """THE SCHEDULED CALIBRATION MEASUREMENT, for the heartbeat.

    Status, resolved fixtures, scored events, the exact shortfall
    (`bettor_source_calibration.cohort_shortfall`), would-write, written and
    when it is next due. On a cycle where it was not due, `last_run` is the
    previous run's result carried from the heartbeat, and `from_this_cycle`
    says so. `last_ran_at` is what the next process reads, so a restart does
    not re-run it early. NEVER RAISES.
    """
    if not isinstance(m, dict):
        return None
    try:
        this = _last_run_of(m.get("result")) if m.get("result") else None
        last = this or m.get("carried_last_run")
        return {"ran_this_cycle": bool(m.get("ran")),
                "attempted_this_cycle": bool(m.get("attempted")),
                "why_not": m.get("why"),
                "from_this_cycle": this is not None,
                "last_run": last,
                "status": (last or {}).get("status"),
                "resolved_fixtures": (last or {}).get("resolved_fixtures"),
                "scored_events": (last or {}).get("scored_events"),
                "shortfall": (last or {}).get("shortfall"),
                "would_write": (last or {}).get("would_write"),
                "written": (last or {}).get("written"),
                "last_ran_at": m.get("last_ran_at"),
                "next_due_at": m.get("next_due_at"),
                "every_s": m.get("every_s"),
                "measured_by": m.get("measured_by"),
                "window_days": m.get("window_days"),
                "evaluator": m.get("evaluator"),
                "guard_read_errors": (m.get("guard") or {}).get(
                    "read_errors") or [],
                "what_this_does_not_change": (
                    "the entry gate's requirement: a current-evaluator row, "
                    ">= 300 scored fixtures, <= 14 days old, within "
                    "tolerance. INSUFFICIENT is never written")}
    except Exception as exc:                                   # noqa: BLE001
        return {"digest_failed": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:160])}


def _calibration_only_digest(c) -> dict | None:
    """Valuations recorded for calibration only this cycle. NEVER RAISES."""
    if not isinstance(c, dict):
        return None
    try:
        return {"recorded": c.get("recorded"),
                "attempted": c.get("attempted"),
                "already_recorded": c.get("already_recorded"),
                "not_recorded_cycle_bound": c.get("not_recorded_cycle_bound"),
                "persist_errors": c.get("persist_errors") or {},
                "refusals": c.get("refusals") or {},
                "rows": list(c.get("rows") or [])[:10],
                "what_these_are": (
                    "valuations recorded while the venue read refused for book "
                    "currency, so the odds source can be scored against the "
                    "venue's settlement. record_purpose CALIBRATION_ONLY: never "
                    "admissible, no executable price, no plan. Their events are "
                    "counted under the venue-read refusal in `refusals`")}
    except Exception as exc:                                   # noqa: BLE001
        return {"digest_failed": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:160])}


def _servicing_digest(svc) -> dict | None:
    """ONE ROW PER POSITION: the decision, and why it could not proceed.

    Reads what `bettor_funded_management.manage` returns and keeps the
    fields an operator needs to answer "what did the scheduled lane decide
    about this holding, and did anything happen?" -- selected action,
    execution eligibility, order status, and the named blocker.

    NEVER RAISES. A digest that threw would take down a heartbeat whose
    whole job is to survive, so an unexpected shape is reported as such.
    """
    if svc is None:
        return None
    try:
        rows = []
        for pick in (svc.get("selection") or [])[:SERVICING_DIGEST_LIMIT]:
            rank = pick.get("ranking") or {}
            rows.append({
                "intent_id": pick.get("intent_id"),
                "us_market_slug": pick.get("us_market_slug"),
                "residual_qty": pick.get("residual_qty"),
                # ── THE DECISION ──────────────────────────────────
                "ok": pick.get("ok"),
                "selected": rank.get("selected"),
                "selected_qty": rank.get("selected_qty"),
                "limit_price": pick.get("limit_price"),
                "proceeds_per_contract": pick.get("proceeds_per_contract"),
                "is_a_deliberate_hold": rank.get("is_a_deliberate_hold"),
                "locks_a_loss": rank.get("selected_locks_a_loss"),
                "depth_limited": rank.get("selected_is_depth_limited"),
                "operating_state": rank.get("operating_state"),
                # ── WHY SOMETHING ELSE COULD NOT PROCEED ──────────
                #
                # Both halves, because they are different facts: an action
                # ruled INELIGIBLE (no execution path in this caller, or
                # an advantage resting on unestablished liquidity) is not
                # the same as one that could not be PRICED at all.
                "refusal": pick.get("refusal"),
                "ineligible": [
                    {"action": c.get("action"),
                     "code": (c.get("selection_ineligible_because") or {})
                     .get("code")}
                    for c in (rank.get("candidates") or [])
                    if c.get("selection_eligible") is False],
                "not_rankable": [
                    {"action": r.get("action"), "blocker": r.get("blocker")}
                    for r in (pick.get("not_rankable") or [])],
            })
        # `manage` keys these by the PARENT's `intent_id` and carries the
        # wire price under `wire_limit_price` and the post-exit holding
        # under `position_after`. My first version read
        # `parent_intent_id`/`limit_price`/`residual_qty` and persisted
        # three nulls -- a digest that reads keys the producer does not
        # emit is worse than no digest, because it looks populated.
        exits = [{"parent_intent_id": x.get("intent_id"),
                  "exit_intent_id": x.get("exit_intent_id"),
                  "selected": x.get("selected"),
                  "selected_qty": x.get("selected_qty"),
                  "wire_limit_price": x.get("wire_limit_price"),
                  "submitted": x.get("submitted"),
                  "refusal": x.get("refusal"),
                  "venue_calls": x.get("venue_calls"),
                  "position_after": x.get("position_after")}
                 for x in (svc.get("exits") or [])[:SERVICING_DIGEST_LIMIT]]
        rec = svc.get("recovered") or {}
        ren = svc.get("authorization_renewal") or {}
        return {
            "ok": svc.get("ok"),
            "refusal": svc.get("refusal"),
            # THE RENEWAL ATTEMPT, every cycle: renewed or the named reason,
            # and the expiry it leaves in force.
            "authorization_renewal": ({
                k: ren.get(k) for k in (
                    "renewed", "reason", "refusal", "expires_at",
                    "previous_expires_at", "owner_expires_at", "error",
                    "reconciliation")}
                if ren else None),
            "positions_serviced": len(svc.get("selection") or []),
            "decisions": rows,
            "decisions_truncated_at": (
                SERVICING_DIGEST_LIMIT
                if len(svc.get("selection") or []) > SERVICING_DIGEST_LIMIT
                else None),
            "exits": exits,
            "recovered": {
                "ok": rec.get("ok"),
                "reconciled": len(rec.get("reconciled") or []),
                "unresolved": len(rec.get("unresolved") or []),
            } if rec else None,
            # `manage` returns ONE SETTLEMENT READ PER HELD POSITION, a list.
            # This used to read `.get("ok")` off it only when it was a dict,
            # so it was None on every cycle whatever the reads said.
            "settlement": _settlement_digest(svc.get("settlement")),
            "settlement_rechecks": _recheck_digest(
                svc.get("settlement_rechecks")),
            # THE LEARNING LOOP'S LAST PASS: how many decisions got their
            # outcome, how many still wait on an open position, and what each
            # candidate model measured. Promotion is never scheduled.
            "learning": _learning_digest(svc.get("learning")),
            # ── XAVIER: WHAT WAS DECIDED AND WHETHER IT COULD BE SENT ──
            #
            # THE GAP THIS CLOSES (Xavier map Q4). The rows above are
            # `manage`'s choice, which is a CANDIDATE, not the decision: the
            # one ranking in the pair pass decides and dispatches. Its per-
            # position brief -- responsibility state, the chosen action, the
            # execution eligibility, the top blockers and the next review --
            # is what an operator reads to know what Xavier did.
            "xavier": [dict(b) for b in (
                (svc.get("pair_cycle") or {}).get("xavier")
                or [])][:SERVICING_DIGEST_LIMIT],
            "what_this_is": (
                "the LAST scheduled servicing decision, not a history. One "
                "row per held position, overwritten each cycle"),
        }
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": None, "digest_failed": "%s: %s"
                % (type(exc).__name__, str(exc)[:160]),
                "why_this_matters": (
                    "the servicing pass may well have succeeded; only this "
                    "projection of it failed. Read the funded book itself")}


def _freshness_digest(out: dict) -> dict | None:
    """THE MEASURED LATENCY NUMBERS, projected onto the heartbeat.

    ── THE DEFECT THIS FIXES, WHICH WAS MINE ─────────────────────────
    `_cycle` returned an `odds_freshness` block carrying the knob's
    configuration and its two counters. `_heartbeat` persists an EXPLICIT
    SUBSET of the cycle return, and `odds_freshness` was not in it. So the
    telemetry for the latency work existed for the duration of one
    function call and was never readable afterwards -- exactly the fault
    `_servicing_digest` was written to fix, reintroduced by me in the very
    change that was supposed to make the latency behaviour observable.
    A readback asking whether the knob was in force got `field_present f`
    and I nearly read that as "the serving build predates it".

    ── WHY A DIGEST AND NOT THE BLOCK ────────────────────────────────
    The cycle block carries four paragraphs of prose explaining what the
    knob is for. A heartbeat is overwritten every cycle and read by an
    operator surface; it carries the NUMBERS. The prose stays on the cycle
    return, where the explanation belongs.

    ── WHAT IS MEASURED HERE, AND WHAT EACH ONE MEANS ────────────────
    `provider_lag_s` is how old the price already was when the provider
    handed it to us. `our_processing_s` is how long we then took to reach
    a decision. Their sum is `pinnacle_age_s`, the quantity the 30-second
    rule governs -- and the rule is UNCHANGED by any of this. The split
    matters because the remedies differ and only one of them is ours.

    `self_inflicted_stale` counts the refusals where our own delay ALONE
    exceeded the limit: cases that would have been valid had we been
    faster. That is the number the latency work has to move, and it is
    counted rather than inferred.

    NEVER RAISES, for the same reason `_servicing_digest` does not.
    """
    fr = out.get("odds_freshness")
    if not isinstance(fr, dict):
        return None
    try:
        lat = out.get("latency") or {}
        return {
            # ── THE KNOB'S STATE ──────────────────────────────────
            "events_per_odds_fetch": fr.get("events_per_odds_fetch"),
            "max_per_cycle": fr.get("max_per_cycle"),
            "is_the_default": fr.get("is_the_default"),
            "odds_refetches": fr.get("odds_refetches"),
            "odds_refetch_failures": fr.get("odds_refetch_failures"),
            # ── THE MEASURED PATH ─────────────────────────────────
            "provider_lag_s": lat.get("provider_lag_s"),
            "our_processing_s": lat.get("our_processing_s"),
            "pinnacle_age_s": lat.get("pinnacle_age_s"),
            "limit_s": PINNACLE_MAX_AGE_S,
            "valid_evaluations": lat.get("valid_evaluations"),
            "stale_refusals": lat.get("stale_refusals"),
            "self_inflicted_stale": lat.get("self_inflicted_stale"),
            "provider_stale_on_arrival": lat.get("provider_stale_on_arrival"),
            "stale_on_arrival_due_to_our_processing": lat.get(
                "stale_on_arrival_due_to_our_processing"),
            "skipped_stale_on_arrival": lat.get("skipped_stale_on_arrival"),
            "samples_from_arrival_skips": lat.get(
                "samples_from_arrival_skips"),
            "on_arrival_every_priced_event": lat.get(
                "on_arrival_every_priced_event"),
            "deduplicated_requests": lat.get("deduplicated_requests"),
            "venue_requests": lat.get("venue_requests"),
            # DEFERRALS REACH THE OPERATOR SURFACE TOO. A count and a
            # bounded sample: without them a rising evaluation count and a
            # falling stale rate could both be produced by examining fewer,
            # easier candidates.
            "deferred_candidates": lat.get("deferred_candidates"),
            "deferred_sample": (lat.get("deferred_sample") or [])[:10],
            "measured_on": "THE_DEPLOYED_PATH_NOT_A_FIXTURE",
        }
    except Exception as exc:                                    # noqa: BLE001
        return {"digest_failed": "%s: %s"
                % (type(exc).__name__, str(exc)[:160])}


def _selection_digest(out: dict) -> dict:
    """The competition selection, bounded for a heartbeat row.

    Bounded deliberately: `confirmed_by_provider` carries up to twelve venue
    fixture titles per candidate for the mapping check, and a heartbeat is read
    by an operator, not archived. The titles are dropped and the CONFIRMATION is
    kept, because the confirmation is the answer and the titles are its input.
    """
    sel = dict(out.get("sports_selection") or {})
    board = dict(sel.get("venue_board") or {})
    fboard = dict(sel.get("venue_football_board") or {})
    return {
        "requested": [k for k, _ in (sel.get("sports") or [])],
        "metered_budget": sel.get("budget"),
        "rejected": [{"key": r.get("key"), "our_token": r.get("our_token"),
                      "refusal": r.get("refusal")}
                     for r in (sel.get("rejected") or [])][:12],
        "confirmed_by_provider": [
            {"key": c.get("key"), "our_token": c.get("our_token"),
             "venue_events": c.get("venue_events"),
             "provider_title": c.get("provider_title"),
             "fixture_confirmation": {
                 k: (c.get("fixture_confirmation") or {}).get(k)
                 for k in ("ok", "refusal", "matches", "examined")}}
            for c in (sel.get("confirmed_by_provider") or [])][:12],
        "budget_dropped": [{"key": d.get("key"),
                            "venue_events": d.get("venue_events")}
                           for d in (sel.get("budget_dropped") or [])][:12],
        "never_requested": sel.get("never_requested") or [],
        "catalogue_read": sel.get("catalogue_read"),
        "venue_board": {"read": board.get("read"),
                        "evidence": board.get("evidence"),
                        "evidence_age_s": board.get("evidence_age_s"),
                        "refusal": board.get("refusal"),
                        "tokens": [t for t, _ in (board.get("board") or [])][:20]},
        "venue_football_board": {
            "read": fboard.get("read"), "evidence": fboard.get("evidence"),
            "error": fboard.get("error"),
            "tokens": [list(x) for x in (fboard.get("board") or [])][:10]},
    }


def _market_subscription_digest() -> dict:
    """`bettor_market_subscription.heartbeat_digest`, guarded twice: the import
    too, so a heartbeat can never be lost to it."""
    try:
        from .. import bettor_market_subscription as _msub
        return _msub.heartbeat_digest()
    except Exception as exc:                                   # noqa: BLE001
        return {"digest_failed": type(exc).__name__}


async def _heartbeat(conn, out: dict, *, key: str = None) -> None:
    """PERSIST THE CYCLE SUMMARY, because most refusals never reach a row.

    `key` EXISTS SO A STANDBY CANNOT ERASE THE WRITER'S CYCLE. Run 26 read
    `markets 0, evaluated 0, venue_errors []` and
    `ANOTHER_PROCESS_HOLDS_THE_WRITER_LOCK 1` from this row -- not because
    the writer refused everything, but because the STANDBY in the API
    process rewrote this key every 60 s with an empty cycle while the real
    writer (a different pid) cycled more slowly. The measurement was of the
    process that does nothing. Standbys now write STANDBY_KEY and the
    writer's row is left alone.

    MOST of this loop's refusal counters fire BEFORE anything is
    evaluated -- no candidate markets at all, no Pinnacle on the event, no
    venue contract, an ambiguous mapping, a closed market, a segment
    contract, no slug on the market row, a failed venue read, a venue
    error, no ask depth, a stale venue quote. (A count was written here
    instead and went stale the moment one counter was split into three;
    the list is the claim, not the number.) Those candidates never reach
    `ext.evaluate`, so they never
    produce an `external_valuations` row, so the refusal census cannot see
    them. The first live run showed exactly that: `evaluated 0` with an
    empty refusal list, which reads as "nothing happened" when in fact
    every candidate was refused for a nameable reason.

    "If no candidate qualifies, report the actual refusal counts" cannot be
    satisfied by a table that only holds the candidates that got far
    enough to be scored. So the whole tally goes here, into the same
    `ingestion_state` table the other loops heartbeat into, and the
    command-centre tile reads it.
    """
    import json

    try:
        payload = {
                "at": time.time(),
                "writer": _code_identity(),
                # ── WHICH VENUE CLIENT THIS PROCESS IS ACTUALLY RUNNING ──
                #
                # The image resolved `polymarket-us>=0.1.2` at build time,
                # so the deployed retry behaviour was decided by whatever
                # PyPI had latest that minute -- and nothing in the running
                # system could be asked which one it got. This container had
                # 0.1.2 (no retries) while the deployed image had 1.0.2
                # (three attempts per call), which is why the local tests
                # could not have seen the amplification.
                #
                # The version is now pinned, and it is REPORTED, because a
                # build log is not available to an operator reading a
                # heartbeat. `pinned_matches_installed: false` is the alarm.
                "venue_sdk": _venue_sdk_digest(),
                # AND THE TWO RATE CONTROLS AS THEY STAND RIGHT NOW. They
                # are separate quantities (a prohibition and a slower rate)
                # and were previously reported as one.
                "venue_rate_controls": _rate_control_digest(),
                # THE MARKET-DATA SUBSCRIPTION: its own state, readiness per
                # market counted by state and by named reason, the reconnect
                # bound it has spent, any venue refusal, and which M1 reasons
                # decisions were refused under. Bounded; never raises.
                "market_subscription": _market_subscription_digest(),
                "state": out.get("state"),
                "evaluated": out.get("evaluated"),
                "written": out.get("written"),
                "markets_considered": out.get("markets_considered"),
                "elapsed_s": out.get("elapsed_s"),
                "credits": out.get("credits"),
                # THE POINT OF THE WHOLE FUNCTION.
                "refusals": out.get("refusals") or {},
                # Three sports and two labels, so this stays a heartbeat.
                "venue_universe_by_label":
                    out.get("venue_universe_by_label") or {},
                "funnel_by_provider_sport":
                    out.get("funnel_by_provider_sport") or {},
                # ── WHICH COMPETITIONS WERE REQUESTED, AND WHICH REFUSED ──
                #
                # `cycle` computed this and the heartbeat dropped it, so
                # production could not answer "which competitions did this
                # build ask for" -- and a cycle running one sport looked
                # identical to a cycle running four. It carries the venue
                # board's own evidence state (LIVE_READ / STALE_SNAPSHOT /
                # EXPIRED_SNAPSHOT) with it, so a set derived from an expired
                # snapshot cannot be read as current coverage.
                "sports_selection": _selection_digest(out),
                # and, for the refusals that have a message, the message.
                "venue_errors": out.get("venue_errors") or [],
                # EVERY MAPPED CANDIDATE AGAINST ITS FIRST REFUSAL, so a
                # `mapped 3 -> evaluated 0` funnel reconciles without a
                # second route (whose step died on run 75, taking the only
                # per-candidate census with it).
                "mapped_candidate_ledger":
                    out.get("mapped_candidate_ledger") or [],
                # THE PER-EVENT RECONCILIATION, summarised. The rows
                # themselves are in `ext_candidate_outcomes`, one per event
                # per cycle, so this heartbeat stays bounded.
                "candidate_outcomes": out.get("candidate_outcomes"),
                "cycle_label": out.get("cycle_label"),
                "cycle_label_note": out.get("cycle_label_note"),
                # THE LATENCY MEASUREMENT, PERSISTED. See
                # `_freshness_digest`: the cycle computed these and the
                # heartbeat dropped them, so the one change made to reduce
                # self-inflicted staleness was unobservable in production.
                "odds_freshness": _freshness_digest(out),
                # THE SERVICING DECISION, PERSISTED.
                #
                # THE GAP THIS CLOSES. `_funded_service` ran on every
                # cycle, decided an action for every held position, and its
                # answer was returned to a caller that dropped it. So the
                # selected action, its execution eligibility and the exact
                # reason a candidate could not proceed existed for the
                # duration of one function call and were never readable
                # afterwards -- the command centre could show the BOOK but
                # not the DECISION, and an operator asking "why is this
                # still held?" had nothing to read.
                #
                # PROJECTED, NOT DUMPED. The full ranking carries candidate
                # tables and evidence rows; a heartbeat is not a decision
                # log and must not grow without bound. This keeps one row
                # per position: what was chosen, at what size and price,
                # whether it was sent, and the named blocker otherwise.
                "funded_servicing": _servicing_digest(
                    out.get("funded_servicing")),
                # XAVIER'S DAILY REVIEW: last date, decisions reviewed, with
                # outcomes, invalidated, next due -- or why it did not run.
                "xavier_review": _xavier_review_digest(
                    out.get("xavier_review")),
                # WHO SERVICES AND HOW OFTEN, measured: the servicing task
                # (its own cadence) or this cycle (its fallback). The
                # servicing task also writes SERVICING_KEY on every pass.
                "servicing_cadence": _servicing_cadence_digest(),
                # WHERE THIS CYCLE'S TIME WENT, per step.
                "step_timing_s": out.get("step_timing_s"),
                # WHY A CYCLE STOPPED OR WAS BLOCKED. The early returns
                # carry it and the heartbeat used to drop it, so a STOPPED
                # row read `refusals: {}` and did not say why.
                "why": out.get("why"),
                # ── THE LEARNING PATH, PERSISTED ─────────────────────────
                #
                # THE GAP THIS CLOSES. The non-funded observer, its labeller,
                # candidate generation and evaluation ran every LIVE cycle
                # and their result was returned to a caller that dropped it,
                # so "zero observations" could not be told from "never ran"
                # or "ran and every attempt was refused for a stated reason".
                # Projected like servicing: counts, every named refusal, a
                # bounded sample of attempts, and the venue reads it spent.
                "pair_observation": _observation_digest(
                    out.get("pair_observation")),
                # And the entry lane's outcome join (valuations -> venue
                # settlements), which feeds the calibration cohort.
                "outcome_join": _outcome_join_digest(out.get("outcome_join")),
                # THE STARTUP ROW'S OWN SUBJECT. The cooldown-resume row is
                # written at startup with what was resumed from storage, and
                # this field was dropped -- so the row said only its state.
                "cooldown_resume": out.get("cooldown_resume"),
                # THE SCHEDULED CALIBRATION MEASUREMENT: status, resolved
                # fixtures, scored events, the exact shortfall, would-write,
                # written, next due. Its `last_ran_at` is also the restart
                # guard the next process reads.
                "source_calibration_measurement":
                    _calibration_measurement_digest(
                        out.get("source_calibration_measurement")),
                # VALUATIONS RECORDED FOR CALIBRATION ONLY, counted apart from
                # `written` so they never read as entry-lane progress.
                C_CALIBRATION_ONLY_RECORDED:
                    out.get(C_CALIBRATION_ONLY_RECORDED),
                "calibration_only": _calibration_only_digest(
                    out.get("calibration_only")),
        }
        blob = json.dumps(payload, default=str)
        if len(blob) > HEARTBEAT_MAX_BYTES:
            # BOUNDED, AND SAYS SO. The per-row samples go first; every
            # count and named refusal stays.
            trimmed = []
            for k in ("mapped_candidate_ledger", "venue_errors"):
                if payload.get(k):
                    payload[k] = []
                    trimmed.append(k)
            po = payload.get("pair_observation")
            if isinstance(po, dict) and po.get("attempts"):
                po["attempts"] = []
                trimmed.append("pair_observation.attempts")
            payload["heartbeat_trimmed"] = {"over_bytes": len(blob),
                                            "limit_bytes": HEARTBEAT_MAX_BYTES,
                                            "emptied": trimmed}
            blob = json.dumps(payload, default=str)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            key or HEARTBEAT_KEY, blob)
    except Exception:                                          # noqa: BLE001
        # A heartbeat that cannot be written must not take the cycle down.
        log.warning("ext_pinnacle: heartbeat failed", exc_info=True)


#: How long to wait before looking at the control row again while the loop
#: is NOT running. A stopped cycle spends no provider credits and no venue
#: requests -- it reads one column -- so the full cadence would only mean
#: that arming the experiment took up to CYCLE_S to be noticed, and that a
#: STOP issued during a sleep would look like it had not worked.
IDLE_POLL_S = 60.0


#: ONE WRITER, AND ITS OWN KEY. This loop writes `external_valuations`
#: and its own heartbeat. Two instances -- two API instances, or an
#: overlapping deploy -- would each spend provider credits and each write
#: the same observation, and the one-per-observation index would turn the
#: second one's work into silent DUPLICATE_OBSERVATION_SKIPPED rows rather
#: than into an error anyone would see. The key is this loop's alone: a
#: key shared with the shadow loop would make one of them a standby of the
#: other, which is a different bug wearing the same lock.
#: rn1x_shadow holds ...032 and rn1x_learn_loop ...033.
LOCK_KEY = 7723901544120034


def next_cycle_delay(*, ran: bool, elapsed_s: float) -> float:
    """Seconds to wait before the next cycle: START TO START at CYCLE_S for a
    cycle that ran (the provider budget is per cycle, so this spends exactly
    what the budget states), never less than IDLE_POLL_S; IDLE_POLL_S for a
    cycle that was stopped or blocked."""
    if not ran:
        return IDLE_POLL_S
    return max(IDLE_POLL_S, CYCLE_S - max(0.0, float(elapsed_s)))


async def run(get_pool) -> None:
    """The long-running task. Armed from the API's startup.

    THE CADENCE DEPENDS ON WHETHER IT RAN. A cycle that actually valued
    anything waits CYCLE_S, because that interval IS the provider budget.
    A cycle that was stopped or blocked waits IDLE_POLL_S, because it
    consumed nothing and the control row is the thing it is waiting for.

    THE WRITER LOCK IS SESSION-SCOPED, so it is held on ONE connection for
    the loop's whole life. Acquiring it per cycle and returning that
    connection to the pool would release it, which is a lock that reads as
    present and enforces nothing. The contention is re-asked every
    IDLE_POLL_S so a standby can actually take over -- a standby that
    never asks again cannot.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        while not await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            log.info("ext_pinnacle STANDBY: another process holds the "
                     "writer lock; writing nothing, retrying in %ss",
                     IDLE_POLL_S)
            # ITS OWN KEY. This process writes no valuations, so it has no
            # cycle; writing HEARTBEAT_KEY here erased the real writer's.
            await _heartbeat(conn, {
                "state": "STANDBY_NOT_THE_WRITER",
                "refusals": {"ANOTHER_PROCESS_HOLDS_THE_WRITER_LOCK": 1}},
                key=STANDBY_KEY)
            await asyncio.sleep(IDLE_POLL_S)
        log.info("ext_pinnacle: writer lock held (key %s)", LOCK_KEY)
        # ── AGENTS (core, migration 152): THE THREE IDENTITIES, ONCE ─────
        # By the writer only (a standby never reaches here), with this
        # process's code identity. Never fatal.
        try:
            await _agents_runtime().ensure_identities(
                conn, code_version=_code_identity())
        except Exception:                                      # noqa: BLE001
            log.warning("ext_pinnacle: agent identities not ensured",
                        exc_info=True)
        # ── THE STORED VENUE COOLDOWN, READ BACK BEFORE THE FIRST READ ───
        #
        # THE FAIL-OPEN THIS CLOSES. Both rate controls lived in module
        # globals and died with the process, so a crash-looping worker
        # resumed at full rate immediately after the venue rate-limited us
        # -- the worst possible moment, and the one most likely to follow a
        # 429 storm. `resume_cooldown` and `cooldown_state` existed and a
        # repository search found no production caller of either: a
        # capability nothing invokes is not a capability, which is the third
        # time that shape has been found on this path.
        #
        # PLACED AFTER THE LOCK AND BEFORE THE LOOP, deliberately. A standby
        # process must not re-arm anything (it sends nothing), and the first
        # cycle must not send before the stored prohibition is in force.
        #
        # A FAILED READ IS NOT "NO COOLDOWN". `load_and_resume` reports
        # `read_failed` and this logs it as a warning rather than proceeding
        # as though the venue were clear.
        try:
            from .. import venue_cooldown_store as _vcs
            resumed = await _vcs.load_and_resume(conn)
            if resumed.get("read_failed"):
                log.warning("ext_pinnacle: the stored venue cooldown could "
                            "NOT be read (%s) — proceeding without it, and "
                            "this is unknown rather than clear",
                            resumed.get("why"))
            elif resumed.get("resumed"):
                log.warning("ext_pinnacle: venue cooldown RESUMED from "
                            "storage: %s", resumed)
            else:
                log.info("ext_pinnacle: no venue cooldown to resume (%s)",
                         resumed.get("why"))
            await _heartbeat(conn, {"state": "COOLDOWN_RESUME_AT_STARTUP",
                                    "cooldown_resume": resumed},
                             key=COOLDOWN_RESUME_KEY)
        except Exception:                                      # noqa: BLE001
            log.warning("ext_pinnacle: cooldown resume failed", exc_info=True)
        # ── THE MARKET-DATA SUBSCRIPTION, IN THE PROCESS THAT DECIDES ────
        #
        # After the writer lock, so a standby never opens a socket. Armed only
        # by BETTOR_MARKET_SUBSCRIPTION=on and a configured venue key; in every
        # other case it records why and does nothing, and every decision
        # refuses exactly as before. `start_default` never raises.
        from .. import bettor_market_subscription as _msub
        log.info("ext_pinnacle: market-data subscription %s",
                 _msub.start_default().get("state"))
        # ── THE PINNAPI FEED (C1, OBSERVE ONLY), BESIDE THE DECIDER ──────
        # After the writer lock and with THIS connection's backend pid: the
        # feed owner re-checks every liveness pass that this pid still holds
        # LOCK_KEY and stops for good if not, so the one provider socket only
        # ever lives beside the process that decides. Disarmed (no lease, no
        # socket) unless the 'pinnapi_feed' control row reads true; env
        # PINNAPI_FEED=off keeps it from starting at all. Never raises.
        from .. import pinnapi_feed_runtime as _feed
        try:
            _writer_pid = await conn.fetchval("SELECT pg_backend_pid()")
            log.info("ext_pinnacle: pinnapi feed %s", (await
                     _feed.start_default(pool, writer_pid=_writer_pid,
                                         writer_lock_key=LOCK_KEY)
                     ).get("state"))
        except Exception:                                      # noqa: BLE001
            log.warning("ext_pinnacle: pinnapi feed start failed",
                        exc_info=True)
        from .. import pinnapi_reactive as _reactive
        reactive_task = _reactive.start(pool, cycle=cycle)
        # ── MANAGEMENT AND RECOVERY, ON THEIR OWN CADENCE ────────────────
        #
        # AFTER THE LOCK, so only the writer services (a standby never
        # reaches here), and AFTER the cooldown resume, so its first venue
        # read already obeys a stored prohibition. It lives exactly as long
        # as this loop. See "ONE EXECUTION AUTHORITY, ON ITS OWN CADENCE".
        servicing = asyncio.get_running_loop().create_task(
            _servicing_loop(pool, interval_s=SERVICING_INTERVAL_S))
        try:
            while True:
                delay = IDLE_POLL_S
                try:
                    # ── THE OTHER HALF OF DURABILITY ─────────────────────
                    # A 429 is observed on a synchronous market-data path
                    # with no connection, so it QUEUES its cooldown. This is
                    # where the connection exists. Drained BEFORE the cycle,
                    # so an observation from the previous cycle is durable
                    # before this one sends anything.
                    try:
                        from .. import venue_cooldown_store as _vcs2
                        if _vcs2.pending() is not None:
                            drained = await _vcs2.drain_pending(conn)
                            log.warning("ext_pinnacle: venue cooldown "
                                        "persisted %s", drained)
                    except Exception:                          # noqa: BLE001
                        log.warning("ext_pinnacle: cooldown drain failed",
                                    exc_info=True)
                    t_cycle = time.monotonic()
                    out = await cycle(conn)
                    log.info("ext_pinnacle: %s", out)
                    if out.get("ran"):
                        # START TO START, as the budget above is written
                        # ("~7.2k/day at 15 minutes"). Sleeping the full
                        # CYCLE_S AFTER a 4-5 minute cycle made the real
                        # period ~19.5 minutes (measured 13:02, 13:21, 13:41,
                        # 13:58 UTC on 2026-10-01): a quarter fewer scans than
                        # the budget pays for. Never shorter than IDLE_POLL_S.
                        delay = next_cycle_delay(
                            ran=True, elapsed_s=time.monotonic() - t_cycle)
                except asyncio.CancelledError:
                    raise
                except Exception:                              # noqa: BLE001
                    log.warning("ext_pinnacle: cycle failed", exc_info=True)
                await asyncio.sleep(delay)
        finally:
            await _reactive.stop(reactive_task)
            # THE FEED FIRST, BOUNDED: its socket closes and its lease is
            # released before this connection (and LOCK_KEY) is returned.
            try:
                await _feed.shutdown_default(wait_s=8.0)
            except Exception:                                  # noqa: BLE001
                pass
            # CLEAN SHUTDOWN of the subscription's socket. Never raises, and
            # does not block the event loop: the socket thread sees the stop
            # and closes on its own next pass.
            _msub.shutdown_default(wait_s=0.0)
            servicing.cancel()
            try:
                await servicing
            except (asyncio.CancelledError, Exception):        # noqa: BLE001
                pass
            _SERVICING["task_active"] = False
