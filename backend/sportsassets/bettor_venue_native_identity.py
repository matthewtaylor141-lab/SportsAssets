"""THE VENUE'S OWN CATALOGUE, ASKED DIRECTLY: provider fixture -> US contract.

THE GAP THIS CLOSES (production, 2026-09-29, 49 provider events). Twenty of
them stopped at NO_VENUE_CONTRACT_FOR_EVENT, raised by
`bettor_venue_mapping.map_event` -- which matches the provider's team names
against the GLOBAL catalogue (`markets`, written from Gamma), not against the
US venue. For at least eleven of those twenty the US venue's OWN catalogue
(`us_premap`) listed the fixture on the same date with a full-time or
full-game winner contract, while the global catalogue held no matching row at
all: UWCL AS Roma-Barcelona `uwcl-asr-fcb-2026-09-30`, UNL France-Italy
`unl-fra-ita-2026-10-02`, Serie B Sao Bernardo-CRB `brb-ber-crb-2026-10-02`.
The fixture was tradeable; the lane was looking for it in the wrong book.
Two more stopped at VENUE_MAPPING_AMBIGUOUS because `map_event` ignores dates,
so consecutive games of an MLB playoff series collide (map4 section 9 D6).

WHAT THIS DOES, AND WHAT IT REFUSES TO DO. One venue event is accepted when,
and only when:

  1 its `game_start` is within START_TOLERANCE_S of the provider's commence
    time (the number is justified where it is declared);
  2 it names EXACTLY two participants, from the venue's own per-side
    `team_name` -- never parsed out of a title, whose order can differ from
    the provider's home/away ("Game 1: CHI White Sox vs. HOU Astros" is the
    provider's Houston-at-home fixture);
  3 each provider team matches a DIFFERENT participant, and the assignment is
    unique: if the two could be swapped, which side is home is not
    established and nothing maps;
  4 it is the ONLY such event. Two candidates are an ambiguity, and an
    ambiguity is refused rather than resolved by preference.

Then the contract that pays on the priced outcome is read off the venue's own
rows. The priced outcome was the provider's HOME team only until the P0
incident repair (2026-10-04); it is now EVERY outcome of the de-vigged set,
asked one at a time: for soccer the per-side contract whose `team_name` is the
priced team, bought LONG, or for the draw the event's one team-less
`...-draw` contract whose own question asks about a draw, bought LONG; for a
two-way family the one row of the single two-way contract whose `team_name`
is the priced team, with THAT ROW'S intent.
Every row of the matched event must be REAL by `bettor_venue_realism`, and the
chosen contract's period must be FULL_MATCH by
`bettor_venue_mapping.period_of_venue_slug` -- the same two gates the global
path applies, reused rather than re-derived.

NO ALIAS IS INVENTED, and that is the same rule `bettor_venue_mapping` keeps.
"Turkey" does not become "Turkiye" and "Inter Milan" does not become
"Internazionale Milano": an unmatched name is refused by name and counted. The
only normalisation is folding (NFKD, the explicit Latin letters NFKD deletes),
case, punctuation, and a SHORT list of affiliation tokens -- legal-form
prefixes and suffixes one rendering carries and the other does not.

NO ALIAS OR CATALOGUE CAN REPAIR A MISSING PROVIDER PRICE. An event with no
Pinnacle h2h never reaches this module; `cycle()` refuses it first, by name,
exactly as before.

PURE except `resolve_venue_native`, which is one bounded read of `us_premap`
followed by the pure matcher. Nothing here reads the venue over the network,
and nothing on the decision path raises: every failure is
`{"ok": False, "refusal": ..., "why": ...}`.
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata

from . import bettor_venue_mapping as vmap
from . import bettor_venue_realism as vreal

VERSION = "BETTOR_VENUE_NATIVE_IDENTITY_V1"

#: How a venue-native identity says where it came from, on the identity row,
#: the contract and the candidate-outcome ledger.
RESOLVER = "VENUE_NATIVE"
MAPPED_BY_VENUE_NATIVE = "VENUE_NATIVE"
MAPPED_BY_GLOBAL = "GLOBAL_CATALOGUE"
MATCHED_BY = "VENUE_NATIVE_TEAM_NAMES_AND_START_TIME"
#: The basis `bettor_external_shadow.persist` already declares for a row with
#: a venue slug and no global condition (migration 110's CHECK lists it).
CONTRACT_IDENTITY_BASIS = "VENUE_NATIVE_US_SLUG"
PAYOUT_EVENT_BASIS = ("VENUE_NATIVE_CONTRACT_ROW_WHOSE_OWN_TEAM_NAME_IS_THE_"
                      "REQUESTED_OUTCOME")
#: The draw contract names no team; it is chosen by its own identifier and
#: question (`_draw_contract`), and says so on the row.
PAYOUT_EVENT_BASIS_DRAW = ("VENUE_NATIVE_TEAMLESS_DRAW_CONTRACT_WHOSE_OWN_"
                           "QUESTION_IS_THE_DRAW")

# ── THE NAMED REFUSALS ───────────────────────────────────────────────
R_NO_EVENT = "NO_VENUE_NATIVE_EVENT_FOR_FIXTURE"
R_AMBIGUOUS = "VENUE_NATIVE_EVENT_AMBIGUOUS"
#: Zero full candidates, but at least one event in the window names ONE of
#: the two teams. Named apart from R_NO_EVENT because the remedies differ: one
#: is a venue that does not list the fixture, the other is a naming gap that
#: this module deliberately does not bridge.
R_ONE_TEAM_ONLY = "VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM"
#: One event, and each provider team matches BOTH participants, so which one
#: is home is not established ("Paris FC" is contained in "Paris
#: Saint-Germain FC" as much as in "Paris FC"). Refused, never guessed.
R_ASSIGNMENT_AMBIGUOUS = "VENUE_NATIVE_TEAM_ASSIGNMENT_AMBIGUOUS"
R_NO_PRICED_CONTRACT = "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_NOT_FOUND"
R_PRICED_CONTRACT_AMBIGUOUS = (
    "VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_AMBIGUOUS")
#: The baseball contract must carry exactly one LONG row and one SHORT row,
#: one per participant. That pairing IS the side semantics a SHORT intent
#: relies on; a contract that does not show it cannot be priced from its SHORT
#: row without guessing which side the complement is.
R_CONTRACT_SIDES = "VENUE_NATIVE_CONTRACT_SIDES_NOT_ESTABLISHED"
R_FAMILY = "VENUE_NATIVE_FAMILY_NOT_SUPPORTED"
R_PROVIDER_EVENT = "VENUE_NATIVE_PROVIDER_EVENT_NOT_MATCHABLE"
R_READ_FAILED = "VENUE_NATIVE_CATALOGUE_READ_FAILED"
#: The bounded read came back full, so a second candidate may be beyond the
#: bound. An ambiguity that cannot be ruled out is not ruled out.
R_READ_TRUNCATED = "VENUE_NATIVE_CANDIDATE_READ_TRUNCATED"
#: The caller could not name the venue league this provider competition is
#: listed under, so a candidate in some OTHER competition -- the men's fixture
#: for a women's price, Serie A's Botafogo for Serie B's -- could not be ruled
#: out by competition.
R_COMPETITION = "VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED"
#: The matcher itself raised on the rows it was given. Nothing maps, and the
#: error is named on the event instead of ending the cycle it runs inside.
R_MATCH_RAISED = "VENUE_NATIVE_MATCH_RAISED"

#: A college school contain-matched but the venue's own nickname is not in
#: the provider's name: Ohio / Ohio State, Miami (FL) / Miami (OH).
R_NICKNAME = "VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM"

# ── EVERY OUTCOME OF THE EVENT, NOT ONLY HOME (P0 incident, 2026-10-04) ──
#
# THE DEFECT. This resolver answered for the provider's HOME team only
# (`priced = home`), so the away contract of every mapped event, and the
# soccer draw contract, were never resolved, never valued and never decided:
# 57 opportunities a day from the mapped events instead of ~140 (production
# funnel receipt, 2026-10-04). The team assignment was ALREADY established for
# both teams -- the one-to-one match below proves which participant is home
# and which is away -- so the away contract is read off the same evidence.
#
# THE DRAW HAS NO TEAM, so it cannot be found by team name. It is the
# event's ONE team-less contract whose identifier is `<prefix>-<event>-draw`,
# which carries exactly one LONG and one SHORT row and whose own question
# asks whether the match ends in a draw -- three facts from the venue's own
# catalogue, all required. A contract failing any of them refuses by name.
#: The requested outcome is not one of home / away / draw.
R_PRICED_DESIGNATION = "VENUE_NATIVE_PRICED_OUTCOME_NOT_A_DESIGNATION"
#: A draw was asked for on a family whose winner contract is two-way.
R_NO_DRAW_IN_FAMILY = "VENUE_NATIVE_FAMILY_HAS_NO_DRAW_OUTCOME"
#: The event lists no team-less contract at all.
R_NO_DRAW_CONTRACT = "VENUE_NATIVE_DRAW_CONTRACT_NOT_FOUND"
#: More than one team-less contract: which one is the draw is not shown.
R_DRAW_CONTRACT_AMBIGUOUS = "VENUE_NATIVE_DRAW_CONTRACT_AMBIGUOUS"
#: One team-less contract, but its identifier, its two sides or its own
#: question does not establish that it pays on the draw.
R_DRAW_CONTRACT_NOT_ESTABLISHED = "VENUE_NATIVE_DRAW_CONTRACT_NOT_ESTABLISHED"
#: The caller named the venue event the discovery matched (pinnapi_discovery,
#: exact structured names) and that event is not among the candidates here.
R_DISCOVERED_EVENT_ABSENT = "VENUE_NATIVE_DISCOVERED_EVENT_NOT_IN_THE_WINDOW"

REFUSALS = (R_NO_EVENT, R_AMBIGUOUS, R_ONE_TEAM_ONLY, R_ASSIGNMENT_AMBIGUOUS,
            R_NO_PRICED_CONTRACT, R_PRICED_CONTRACT_AMBIGUOUS,
            R_CONTRACT_SIDES, R_FAMILY, R_PROVIDER_EVENT, R_READ_FAILED,
            R_READ_TRUNCATED, R_COMPETITION, R_MATCH_RAISED, R_NICKNAME,
            R_PRICED_DESIGNATION, R_NO_DRAW_IN_FAMILY, R_NO_DRAW_CONTRACT,
            R_DRAW_CONTRACT_AMBIGUOUS, R_DRAW_CONTRACT_NOT_ESTABLISHED,
            R_DISCOVERED_EVENT_ABSENT)

#: ── WHICH REFUSALS ARE ABOUT THE EVENT, NOT ONE OF ITS OUTCOMES ──────
#:
#: The collector now asks once per outcome. A refusal in this set says the
#: FIXTURE could not be identified (no venue event, two candidates, a naming
#: gap, an unreadable catalogue), so asking again for the next outcome of the
#: same event would only count the same finding twice; the collector stops
#: the event there. Every other refusal -- no row for this team, an ambiguous
#: row, the draw contract's own evidence, a period or realism finding on THE
#: chosen contract -- is about one outcome, and the next outcome is still
#: asked. Unknown codes are treated as outcome-level (the next outcome is
#: asked), the direction that never hides an outcome.
EVENT_LEVEL_REFUSALS = frozenset((
    R_NO_EVENT, R_AMBIGUOUS, R_ONE_TEAM_ONLY, R_ASSIGNMENT_AMBIGUOUS,
    R_FAMILY, R_PROVIDER_EVENT, R_READ_FAILED, R_READ_TRUNCATED,
    R_COMPETITION, R_MATCH_RAISED, R_NICKNAME, R_DISCOVERED_EVENT_ABSENT))

#: The designations an outcome can carry: the provider's home team, its away
#: team, and the draw of a three-way (soccer) book.
DESIGNATIONS = ("home", "away", "draw")

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"

#: ── WHICH VENUE CONTRACTS ARE A FAMILY'S FULL-MATCH WINNER ────────────
#:
#: EXACT VALUES, NOT A PATTERN. These are the venue's own `sportsMarketType`
#: strings for the two families this lane prices, as the production fixture
#: carries them (1,746 soccer rows, 46 baseball rows, nothing else), and both
#: name their scope (`full_time`, `full_game`) in the token
#: `bettor_venue_mapping.scope_from_v1` reads as FULL. A new value -- a
#: `_first_half_winner`, an `efootball_` twin -- is absent until someone reads
#: it, which is the fail-closed direction.
FAMILY_WINNER_TYPES = {
    "soccer": ("soccer_team_full_time_winner",),
    "baseball": ("baseball_team_full_game_winner",),
    # cand22: the venue's college-football full-game winner, measured on the
    # 2026-10-03 board (107 `cfb` events; 106 carry exactly one LONG and one
    # SHORT row on one `aec-cfb-...` contract, the baseball shape). Its scope
    # token is `full_game`; segment winners (`_first_half_winner`,
    # `_first_quarter_winner` ...) are different values and stay absent.
    "football": ("football_team_full_game_winner",),
}

#: ── FOOTBALL PARTICIPANTS CARRY THE VENUE'S OWN NICKNAME ───────────────
#:
#: THE COLLISION THIS PREVENTS, on the measured board. The venue's college
#: `team_name` is the SCHOOL ("ohio", "ohio state", "michigan", "michigan
#: state", "miami  fl", "miami  oh") and `side_norm` on the same row is the
#: NICKNAME ("bobcats", "buckeyes", "wolverines", "spartans", "hurricanes",
#: "redhawks"). Containment on the school alone reads the provider's "Ohio
#: State Buckeyes" as the venue's "ohio" -- and on 2026-10-03 Ohio vs. Kent
#: State and Ohio State vs. Iowa both started at 15:30 ET, as did Michigan
#: (12:00) and Michigan State (12:30) inside one window. "State" is generic
#: everywhere else in this module and is the whole difference here.
#:
#: So a football participant is the venue's school AND nickname together
#: ("ohio bobcats", "ohio state buckeyes", "miami fl hurricanes") -- the same
#: rendering the provider uses ("Ohio Bobcats", "Miami Hurricanes"), compared
#: by the unchanged `same_team`. No alias is added: a school the two sources
#: spell differently ("UL Monroe" / "louisiana monroe") still refuses by name.
NICKNAME_QUALIFIED_FAMILIES = frozenset(("football",))


def participant_name(row, family) -> str:
    """The venue participant a row names: `team_name`, plus `side_norm` (the
    nickname) for NICKNAME_QUALIFIED_FAMILIES -- unless `team_name` already
    ends with it, as every venue `nfl` row does ("los angeles rams" / "rams").
    Pure."""
    r = _row(row)
    team = " ".join(str(r.get("team_name") or "").split())
    if family in NICKNAME_QUALIFIED_FAMILIES and team:
        nick = " ".join(str(r.get("side_norm") or "").split())
        if nick and nick not in ("yes", "no"):
            if team.lower().split()[-len(nick.split()):] == \
                    nick.lower().split():
                return team
            return "%s %s" % (team, nick)
    return str(r.get("team_name") or "")


#: ── THE NFL: CITY/REGION + NICKNAME, A CLOSED TABLE OF 32 (cand24) ─────
#:
#: The venue's `nfl` rows carry the full name in `team_name` ("los angeles
#: rams", "washington commanders") and the nickname in `side_norm`; its event
#: TITLES use the abbreviated region ("LA Rams vs. PHI Eagles", "NE Patriots
#: vs. BUF Bills", "JAC Jaguars vs. CIN Bengals"), and the provider renders the
#: full name ("Los Angeles Rams"). Two regions are shared -- LA (Rams,
#: Chargers) and NY (Giants, Jets) -- so the NICKNAME is what separates them and
#: the REGION must agree with it: "LA Rams" is the Rams, "NY Jets" the Jets,
#: and "Oakland Raiders" is nobody (the Raiders' region is Las Vegas).
#:
#: EACH ENTRY IS ONE TEAM'S OWN RENDERINGS (venue slug abbr, venue title abbr,
#: the common region abbreviation), never a similarity between two teams.
#: Nickname -> (region, renderings of the region). Every NFL nickname is
#: unique, so the nickname names at most one entry.
NFL_TEAMS = {
    "cardinals": ("arizona", ("ari", "arz")),
    "falcons": ("atlanta", ("atl",)),
    "ravens": ("baltimore", ("bal",)),
    "bills": ("buffalo", ("buf",)),
    "panthers": ("carolina", ("car",)),
    "bears": ("chicago", ("chi",)),
    "bengals": ("cincinnati", ("cin",)),
    "browns": ("cleveland", ("cle",)),
    "cowboys": ("dallas", ("dal",)),
    "broncos": ("denver", ("den",)),
    "lions": ("detroit", ("det",)),
    "packers": ("green bay", ("gb", "gnb")),
    "texans": ("houston", ("hou",)),
    "colts": ("indianapolis", ("ind",)),
    "jaguars": ("jacksonville", ("jax", "jac")),
    "chiefs": ("kansas city", ("kc", "kan")),
    "raiders": ("las vegas", ("lv", "lvr")),
    "chargers": ("los angeles", ("lac", "la")),
    "rams": ("los angeles", ("lar", "la")),
    "dolphins": ("miami", ("mia",)),
    "vikings": ("minnesota", ("min",)),
    "patriots": ("new england", ("ne", "nwe")),
    "saints": ("new orleans", ("no", "nor")),
    "giants": ("new york", ("nyg", "ny")),
    "jets": ("new york", ("nyj", "ny")),
    "eagles": ("philadelphia", ("phi",)),
    "steelers": ("pittsburgh", ("pit",)),
    "49ers": ("san francisco", ("sf", "sfo")),
    "seahawks": ("seattle", ("sea",)),
    "buccaneers": ("tampa bay", ("tb", "tam")),
    "titans": ("tennessee", ("ten",)),
    "commanders": ("washington", ("was", "wsh")),
}
NFL_PROVIDER_COMPETITION = "americanfootball_nfl"
NFL_LEAGUE_TOKEN = "nfl"


def nfl_team(name):
    """The NFL team a rendering names, as `(nickname, region)`, or None.

    Exactly one NFL nickname among the folded tokens, and every OTHER token
    must be that team's region -- in full ("tampa bay") or one of its own
    abbreviations ("tb") -- or nothing at all ("49ers"). A region that belongs
    to a different team ("louisville cardinals", "oakland raiders") is not
    this team, and the function answers None rather than guess. Pure."""
    toks = fold(name).split()
    nicks = [t for t in dict.fromkeys(toks) if t in NFL_TEAMS]
    if len(nicks) != 1:
        return None
    nick = nicks[0]
    region, abbrs = NFL_TEAMS[nick]
    rest = [t for t in toks if t != nick]
    if not rest or rest == region.split() or \
            (len(rest) == 1 and rest[0] in abbrs):
        return (nick, region)
    return None


def nfl_canonical_tokens(name):
    """`nfl_team`'s answer as the full rendering's tokens ("TB Buccaneers" ->
    ['tampa', 'bay', 'buccaneers']), or None. Pure."""
    got = nfl_team(name)
    if got is None:
        return None
    return got[1].split() + [got[0]]

#: ── THE START-TIME TOLERANCE, AND WHY IT IS 90 MINUTES ────────────────
#:
#: MEASURED ON THE FIXTURE, BOTH WAYS. Every provider event in the
#: 2026-09-29 capture whose fixture the venue lists sits within 10.4 minutes
#: of the venue's `game_start`: soccer to the minute, MLB 10 minutes early
#: (the venue lists the hour, the provider the first pitch -- 21:10:26 against
#: 21:00). The closest DIFFERENT meeting of the same two teams is the next
#: game of a series, 23.8 h away (NYY-BOS game 1 at 00:00 and game 2 at 00:00
#: next day, against the provider's 00:10).
#:
#: 90 minutes is ~9x the largest agreement observed, and it is below the
#: shortest separation of two games between the same teams on one day: an MLB
#: doubleheader's second game cannot start before the first has been played
#: (~3 h). Wider would start to reach a doubleheader's other game; narrower
#: would refuse a fixture whose two sources state the time a little
#: differently. Anything inside the window that also matches both teams is
#: counted, so two candidates refuse as ambiguous rather than one being picked.
START_TOLERANCE_S = 90 * 60.0

#: ── HOW RECENTLY THE VENUE MUST HAVE RE-LISTED A ROW ─────────────────
#:
#: The catalogue writer (`workers.premap`) sweeps the whole board every
#: REFRESH_SECONDS = 1800 s and prunes rows unseen for 26 h. A row not
#: re-seen in three sweep periods has missed at least two consecutive sweeps:
#: either the venue stopped listing it or the writer is not running, and in
#: neither case is it evidence of a current contract.
#:
#: IT IS ALSO THE CLOSED-MARKET FILTER. `us_premap` has no `closed` column;
#: the sweep asks the venue for active, unclosed events and drops closed
#: markets before writing, so a contract that closes simply stops being
#: re-seen and leaves this window.
RESEEN_WITHIN_S = 3 * 1800.0

#: Bound on the candidate read. A full result refuses (R_READ_TRUNCATED).
MAX_CANDIDATE_ROWS = 2000

#: ── NORMALISATION ────────────────────────────────────────────────────
#:
#: THE LATIN LETTERS NFKD DELETES, mapped to the base the venue writes. This
#: is `pmus._LATIN_FOLD`, restated here so this module stays pure and never
#: imports the venue client -- `tests/test_venue_native_identity_matches_the_
#: measured_fixture.py` pins the two tables equal. The venue's own `team_name`
#: was folded with that table ("Tromso" for "Tromso"), so the provider's name
#: must be folded the same way or "HB Koge" against "HB Køge" reads "kge".
LATIN_FOLD = str.maketrans({
    "ø": "o", "Ø": "O", "ǿ": "o", "Ǿ": "O",
    "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D",
    "þ": "th", "Þ": "Th", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe",
    "ß": "ss", "ı": "i", "ħ": "h", "Ħ": "H", "ŧ": "t", "Ŧ": "T",
    "ŋ": "ng", "Ŋ": "Ng",
    "'": "", "’": ""})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: AFFILIATION TOKENS: legal-form and society markers one rendering carries
#: and the other does not ("Goias" / "Goias EC", "Clube de Regatas Brasil" /
#: "CR Brasil", "FK Austria Wien" / "Austria Wien"). Deliberately short.
#: "United", "City" and "Real" are NOT here: they are the whole difference
#: between two clubs in one city (see `bettor_venue_mapping`'s docstring).
AFFILIATION_TOKENS = frozenset((
    "fc", "cf", "sc", "ac", "afc", "cd", "club", "clube", "ec", "cr",
    "fk", "bk", "ff",
))

#: GENERIC TOKENS NEVER COUNT ALONE. They stay in the comparison -- "city"
#: against "united" still refuses -- but a match must share at least one token
#: that is NOT generic. "Athletic Club (MG)" and "Athletic Club" share only
#: "athletic": two different clubs could, so that is not a match.
GENERIC_TOKENS = frozenset((
    "city", "united", "real", "sporting", "sport", "sports", "athletic",
    "atletico", "athletico", "club", "clube", "fc", "sc", "ac", "cf", "afc",
    "cd", "ec", "cr", "fk", "bk", "ff", "sao", "san", "santa", "santo",
    "saint", "st", "de", "da", "do", "dos", "das", "del", "la", "le", "el",
    "los", "las", "the", "of", "e", "y", "and", "new", "north", "south",
    "east", "west", "county", "town", "rovers", "wanderers", "albion",
    "borough", "dynamo", "dinamo", "olympic", "olympique", "racing",
    "deportivo", "deportes", "union", "inter", "internacional",
    "national", "nacional", "independiente", "universidad", "university",
    "state", "red", "white", "blue", "black", "green", "star", "stars",
    "royal", "sociedad", "esporte", "futebol", "football", "soccer",
    "calcio", "association", "associacao", "us", "as", "ss", "sv", "if",
))

#: SQUAD QUALIFIERS MAKE IT A DIFFERENT TEAM. The same lesson
#: `ext_pinnacle_loop.SQUAD_QUALIFIERS` records: "Arsenal" is contained in
#: "Arsenal Women" and they are not the same side. Kept by kind, because the
#: gender kind has one principled exemption (below) and the others have none.
GENDER_QUALIFIERS = frozenset((
    "women", "womens", "woman", "ladies", "feminin", "feminine", "femenino",
    "femenina", "feminino", "feminina", "frauen", "damen", "dames",
    "kvinner", "kobiet", "w", "wfc",
))
AGE_QUALIFIERS = frozenset((
    "u16", "u17", "u18", "u19", "u20", "u21", "u22", "u23", "youth",
    "junior", "juniors", "academy", "primavera", "jugend",
))
RESERVE_QUALIFIERS = frozenset((
    "ii", "iii", "b", "reserves", "reserve", "amateur", "amateure",
    "castilla", "atletic",
))
SQUAD_QUALIFIERS = GENDER_QUALIFIERS | AGE_QUALIFIERS | RESERVE_QUALIFIERS

#: ── WHEN A GENDER QUALIFIER IS THE COMPETITION'S OWN ─────────────────
#:
#: THE PROVIDER'S COMPETITION KEY ESTABLISHES THE SQUAD. The provider lists
#: the Women's Champions League under `soccer_uefa_champs_league_women` with
#: team names that carry no qualifier ("Arsenal", "Real Madrid"); the venue
#: lists the same fixtures as "Arsenal WFC" and "Real Madrid CF Femenino" at
#: the same instant. Inside a competition the provider itself declares to be a
#: women's competition, a women's marker on the venue side is the
#: competition's, not a different squad. Outside one, it is a different squad
#: and refuses. Age and reserve qualifiers have no such exemption.
#: An explicit list, taken from `ext_pinnacle_loop.VENUE_TOKEN_TO_PROVIDER_KEY`;
#: a key is never inferred to be a women's competition from its spelling.
WOMENS_PROVIDER_COMPETITIONS = frozenset((
    "soccer_uefa_champs_league_women",
    "soccer_usa_nwsl",
))


def fold(text) -> str:
    """Lowercase ASCII with punctuation turned to spaces. Order preserved."""
    s = str(text or "").translate(LATIN_FOLD)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode()
    return " ".join(_NON_ALNUM.sub(" ", s.lower()).split())


def _is_generic(tok: str) -> bool:
    return tok in GENERIC_TOKENS or len(tok) < 2 or tok.isdigit()


#: ── COLLEGE NAMES: THE ONLY REWRITES, AND THE GUARD ON THEM (cand22) ──
#:
#: Each is a RENDERING difference between two sources for ONE school, never
#: a similarity between two schools, and each is applied to BOTH sides:
#:
#:   ranked prefix   "#5 Alabama", "(5) Alabama", "No. 5 Alabama" -> alabama.
#:                   A poll rank is not part of the name.
#:   St.             "Mississippi St." -> mississippi state; a LEADING St.
#:                   is Saint ("St. Thomas (MN)" -> saint thomas mn).
#:   abbreviation    ABBREVIATIONS below, expanded to the school's full word
#:                   ("UL Monroe" -> louisiana monroe). An expansion can make
#:                   two renderings of different schools look alike, so a
#:                   match that used one stands ONLY when the venue's own
#:                   nickname is in the provider's name (warhawks).
#:
#: AND EVERY FOOTBALL MATCH NEEDS THE VENUE NICKNAME IN THE PROVIDER NAME.
#: The provider renders college teams as school + nickname ("Ohio Bobcats",
#: "Miami (OH) RedHawks"). A bare school would be CONTAINED in a longer
#: one -- "Ohio" in "ohio state buckeyes", "Miami" in "miami oh redhawks" --
#: and containment would cross-map them. Requiring the venue's nickname
#: (side_norm) closes that: "buckeyes" is not in "Ohio Bobcats". No fuzzy
#: score, no edit distance: an unmatched rendering refuses by name.
_RANK_PREFIX = re.compile(
    r"^\s*(?:#\s*\d{1,2}|\(\s*\d{1,2}\s*\)|no\.?\s*\d{1,2})\s+",
    re.IGNORECASE)

#: (folded token sequence) -> (expansion). Short on purpose; each entry is
#: one school's abbreviation as a source renders it.
ABBREVIATIONS = {
    ("ul",): ("louisiana",),                 # UL Monroe / UL Lafayette
    ("ole", "miss"): ("mississippi",),       # Ole Miss Rebels
    ("app",): ("appalachian",),              # App State Mountaineers
}

def _college_tokens(raw: list) -> tuple:
    """(tokens, rewrites) after the St. and abbreviation rewrites. Pure."""
    toks, rew = [], []
    i = 0
    while i < len(raw):
        hit = None
        for k, v in ABBREVIATIONS.items():
            if tuple(raw[i:i + len(k)]) == k:
                hit = (k, v)
                break
        if hit:
            toks.extend(hit[1])
            rew.append("%s->%s" % (" ".join(hit[0]), " ".join(hit[1])))
            i += len(hit[0])
            continue
        t = raw[i]
        if t == "st":
            t = "saint" if i == 0 else "state"
            rew.append("st->%s" % t)
        toks.append(t)
        i += 1
    return toks, rew


def team_profile(name, family=None, nickname=None) -> dict:
    """One team's name, as the comparison sees it, with what was dropped.

    `tokens` excludes affiliation markers and squad qualifiers (both reported);
    `distinctive` is the subset that may carry a match on its own. A name that
    is ONLY affiliation markers keeps them, because an empty token set would
    be contained in every other team. For NICKNAME_QUALIFIED_FAMILIES the
    college rewrites above apply (reported in `rewrites`), and `nickname`
    (the venue's side_norm) is carried as `nickname_tokens`.
    """
    college = family in NICKNAME_QUALIFIED_FAMILIES
    text = _RANK_PREFIX.sub("", str(name or "")) if college else name
    raw = fold(text).split()
    rewrites: list = []
    nfl = nfl_canonical_tokens(name) if college else None
    if nfl is not None:
        # AN NFL TEAM, rendered in full: "LA Rams" and "Los Angeles Rams" are
        # one team's two renderings (NFL_TEAMS); the nickname still has to
        # agree, so the Chargers are never the Rams.
        if raw != nfl:
            rewrites.append("nfl region %s -> %s" % (" ".join(raw),
                                                      " ".join(nfl)))
        raw = list(nfl)
    elif college:
        if text != name:
            rewrites.append("ranked prefix dropped")
        raw, rw = _college_tokens(raw)
        rewrites.extend(rw)
    quals = frozenset(t for t in raw if t in SQUAD_QUALIFIERS)
    body = [t for t in raw if t not in SQUAD_QUALIFIERS]
    kept = [t for t in body if t not in AFFILIATION_TOKENS]
    dropped = [t for t in body if t in AFFILIATION_TOKENS]
    if not kept:
        kept, dropped = body, []
    toks = frozenset(kept)
    out = {"name": name, "folded": fold(name), "tokens": toks,
           "distinctive": frozenset(t for t in toks if not _is_generic(t)),
           "qualifiers": quals, "dropped": sorted(set(dropped))}
    if college:
        nick = _college_tokens(fold(nickname).split())[0] if nickname else []
        out.update(rewrites=rewrites, nickname_tokens=frozenset(nick),
                   expanded=any("->" in r and not r.startswith("st->")
                                for r in rewrites))
    return out


def same_college_team(provider: dict, venue: dict, *,
                      exact_school_confirms: bool = False) -> dict:
    """`same_team`, plus the football guard: the venue's own nickname must be
    in the provider's name. Pure.

    `exact_school_confirms` (coverage census, 2026-10-05; the caller passes
    it only for a venue event PinnAPI-native discovery already matched to
    this provider fixture by EXACT structured identity): a provider name
    whose tokens EQUAL the venue's school tokens (its participant record
    without the nickname) -- Pinnacle's "Troy" for the venue's "troy" +
    "trojans" -- is the venue's own school record, token for
    token. The guard exists for CONTAINMENT ("Ohio" inside "Ohio State"),
    which equality cannot be: "Ohio" still never confirms "ohio state". The
    other conditions of `same_team` (equal squad qualifiers, a shared
    distinctive token) and the caller's one-to-one assignment of BOTH teams,
    start tolerance and league token all still apply.

    THE LOSS THIS CLOSES. Pinnacle names a college team by its school only
    (the discovery's receipts and its tests: "Troy", "Southern Miss",
    "Ohio"), so every PinnAPI-native NCAAF seed -- the one path that prices a
    college game from a current WS quote -- was refused here with
    VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM, on every game, while
    the metered path's own names never match a WS fixture: NCAAF reached no
    valuation."""
    got = same_team(provider, venue)
    nick = venue.get("nickname_tokens") or frozenset()
    got["venue_nickname"] = sorted(nick)
    school = frozenset(venue["tokens"]) - frozenset(nick)
    if got["same"] and exact_school_confirms and school and \
            provider["tokens"] == school and \
            not (nick and nick <= provider["tokens"]):
        got.update(confirmed_by="EXACT_SCHOOL_TOKENS_ON_THE_DISCOVERED_EVENT",
                   why=("the provider's name is the venue's school %s (its "
                        "record without the nickname %s) token for token, on "
                        "the venue event discovery matched by exact "
                        "structured identity" % (sorted(school),
                                                 sorted(nick))))
        return got
    if got["same"] and not (nick and nick <= provider["tokens"]):
        got.update(same=False, nickname_blocked=True,
                   why=("the venue's nickname %s is not in the provider's "
                        "name %r, so containment alone cannot say these are "
                        "the same school (Ohio is contained in Ohio State)"
                        % (sorted(nick) or "(none listed)",
                           provider.get("name"))))
    return got


def same_team(provider: dict, venue: dict, *, womens_competition=False) -> dict:
    """Are these two renderings the SAME team? A verdict with its reason.

    THREE CONDITIONS, ALL REQUIRED:

      1 CONTAINMENT of the non-qualifier tokens, one set inside the other --
        which covers "Goias" / "Goias EC" and "Sao Bernardo" / "Sao Bernardo
        FC", and does NOT cover "Manchester City" / "Manchester United",
        because "city" and "united" both survive and differ;
      2 A SHARED DISTINCTIVE TOKEN. Generic words never count alone, so
        "Athletic Club (MG)" and "Athletic Club" do not match on "athletic";
      3 THE SAME SQUAD QUALIFIERS, except that inside a competition the
        provider declares to be a women's competition a gender qualifier on
        the venue side is the competition's own (see
        WOMENS_PROVIDER_COMPETITIONS).
    """
    p_t, v_t = provider["tokens"], venue["tokens"]
    out = {"same": False, "provider": sorted(p_t), "venue": sorted(v_t)}
    if not p_t or not v_t:
        out["why"] = "a name with no tokens left after folding"
        return out
    pq, vq = set(provider["qualifiers"]), set(venue["qualifiers"])
    if womens_competition:
        # The provider's competition supplies the gender for its own teams;
        # only the gender kind is exempt, and only in this direction.
        vq -= GENDER_QUALIFIERS
        pq -= GENDER_QUALIFIERS
    if pq != vq:
        out["why"] = ("squad qualifiers differ (%s against %s): a different "
                      "side of the same club" % (sorted(pq), sorted(vq)))
        return out
    if not (p_t <= v_t or v_t <= p_t):
        out["why"] = ("neither name's tokens contain the other's, so a word "
                      "that distinguishes one club from another differs")
        return out
    shared = sorted(provider["distinctive"] & venue["distinctive"])
    if not shared:
        out["why"] = ("the names share only generic tokens (%s), which two "
                      "different clubs can share"
                      % sorted(p_t & v_t))
        return out
    out.update(same=True, shared_distinctive=shared,
               why="contained, sharing the distinctive token(s) %s" % shared)
    return out


def _epoch(value):
    """Epoch seconds from a datetime, a number or an ISO-8601 string; else
    None. Never raises: an unreadable instant is a refusal upstream."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return None
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    # A NAIVE time is no instant: a string without an offset would be read
    # in a guessed zone, so it is refused rather than assumed UTC.
    return d.timestamp() if d.tzinfo is not None else None


def _row(r) -> dict:
    return dict(r) if not isinstance(r, dict) else r


def league_token(event_slug) -> str:
    """The venue's league token: the first segment of its event slug
    (`uwcl-asr-fcb-2026-09-30` -> `uwcl`). The same position the lane's
    board reads (`split_part(market_slug, '-', 2)` on `atc-uwcl-...`)."""
    return str(event_slug or "").split("-", 1)[0].strip().lower()


def _events_in_window(rows, *, family, commence_epoch, league_tokens=None):
    """Group the family's winner rows by venue event, keeping only events
    whose start is inside the tolerance and, where the caller named them,
    whose league is the provider competition's own. Reports what it set
    aside."""
    types = FAMILY_WINNER_TYPES.get(family) or ()
    leagues = (None if league_tokens is None
               else {str(t).strip().lower() for t in league_tokens})
    by_event: dict = {}
    for r in rows or ():
        r = _row(r)
        if str(r.get("sports_type") or "") not in types:
            continue
        ev = str(r.get("event_slug") or "")
        if not ev:
            continue
        by_event.setdefault(ev, []).append(r)
    inside, set_aside = [], {"not_two_participants": [],
                             "start_not_one_instant": [],
                             "other_competition": []}
    for ev, rs in sorted(by_event.items()):
        starts = {_epoch(r.get("game_start")) for r in rs}
        if len(starts) != 1 or None in starts:
            set_aside["start_not_one_instant"].append(ev)
            continue
        start = next(iter(starts))
        offset = start - float(commence_epoch)
        if abs(offset) > START_TOLERANCE_S:
            continue
        # INSIDE THE WINDOW, BUT ANOTHER COMPETITION: reported, never a
        # candidate.
        if leagues is not None and league_token(ev) not in leagues:
            set_aside["other_competition"].append(ev)
            continue
        names = sorted({participant_name(r, family) for r in rs
                        if str(r.get("team_name") or "").strip()})
        if len(names) != 2:
            set_aside["not_two_participants"].append(ev)
            continue
        nicks = {}
        for r in rs:
            nicks.setdefault(participant_name(r, family),
                             str(r.get("side_norm") or ""))
        inside.append({"event_slug": ev, "rows": rs, "participants": names,
                       "nicknames": nicks,
                       "start_epoch": start, "offset_s": round(offset, 3)})
    return inside, set_aside


def _refuse(out: dict, code: str, why: str) -> dict:
    out.update(ok=False, refusal=code, why=why)
    return out


def match_event(*, home, away, commence_epoch, family, rows,
                competition=None, league_tokens=None, priced="home",
                event_slug=None) -> dict:
    """THE ONE venue event for this provider fixture and the contract that
    pays on the PRICED outcome (`priced`: 'home' -- the default, the only
    outcome before the P0 incident repair -- 'away', or 'draw' on a soccer
    event), or a named refusal saying what failed. Pure; never raises.

    `event_slug`, when given, is the venue event pinnapi_discovery already
    matched to this provider event by exact structured names and start time
    (pinnapi_census.event_identity). Only that event is a candidate here; every
    check below still runs on it, and if it is not inside the window the
    match refuses (R_DISCOVERED_EVENT_ABSENT) rather than looking elsewhere.

    `rows` are `us_premap` rows (dicts or records) carrying market_slug,
    intent, event_slug, event_title, question, kind, sports_type, team_abbr,
    team_name, side_norm and game_start. `competition` is the provider's
    sport key; it is read for one thing only, the women's-competition
    exemption on gender qualifiers.

    `league_tokens` RESTRICTS THE SEARCH TO THE PROVIDER COMPETITION'S OWN
    VENUE LEAGUE(S) -- the league the cycle confirmed this provider key
    against. Given, a venue event in any other league is not a candidate, so a
    women's price cannot land on a men's fixture of the same two clubs, nor a
    Serie B price on a Serie A "Botafogo". Given EMPTY, the competition is not
    established and nothing maps. None (a direct caller that has no mapping)
    applies no league restriction; the scheduled path always supplies it.
    """
    out: dict = {"version": VERSION, "ok": False, "refusal": None,
                 "why": None, "family": family, "home": home, "away": away,
                 "competition": competition,
                 "commence_epoch": commence_epoch,
                 "tolerance_s": START_TOLERANCE_S, "candidates": 0,
                 "partial_matches": [], "matched_by": MATCHED_BY,
                 "priced": priced, "discovered_event_slug": event_slug}
    if priced not in DESIGNATIONS:
        return _refuse(out, R_PRICED_DESIGNATION, (
            "the priced outcome %r is not one of %s" % (priced, DESIGNATIONS)))
    if family not in FAMILY_WINNER_TYPES:
        return _refuse(out, R_FAMILY, (
            "family %r has no established full-match winner type on the "
            "venue; only %s are read" % (family, sorted(FAMILY_WINNER_TYPES))))
    if priced == "draw" and family != "soccer":
        return _refuse(out, R_NO_DRAW_IN_FAMILY, (
            "the %s winner contract is two-way; only soccer's full-time "
            "result has a draw" % (family,)))
    try:
        commence = float(commence_epoch)
    except (TypeError, ValueError):
        return _refuse(out, R_PROVIDER_EVENT,
                       "the provider's commence time is not readable")
    hp, ap = team_profile(home, family), team_profile(away, family)
    if not hp["tokens"] or not ap["tokens"] or hp["tokens"] == ap["tokens"]:
        return _refuse(out, R_PROVIDER_EVENT, (
            "the provider event does not name two distinct teams after "
            "folding (%r vs %r)" % (home, away)))
    womens = str(competition or "") in WOMENS_PROVIDER_COMPETITIONS
    out["womens_competition"] = womens
    out["league_tokens"] = (None if league_tokens is None
                            else sorted(str(t) for t in league_tokens))
    if league_tokens is not None and not list(league_tokens):
        return _refuse(out, R_COMPETITION, (
            "no venue league is named for the provider competition %r, so a "
            "fixture of the same two teams in another competition cannot be "
            "ruled out" % (competition,)))

    events, set_aside = _events_in_window(rows, family=family,
                                          commence_epoch=commence,
                                          league_tokens=league_tokens)
    if event_slug is not None:
        # THE DISCOVERED EVENT ONLY. Not a looser match: the same checks run
        # on it; an event the discovery did not name is simply not asked.
        named = [e for e in events if e["event_slug"] == str(event_slug)]
        out["events_in_window_before_discovery_restriction"] = len(events)
        if not named:
            out["events_in_window"] = len(events)
            out["set_aside"] = {k: v[:6] for k, v in set_aside.items() if v}
            return _refuse(out, R_DISCOVERED_EVENT_ABSENT, (
                "the discovered venue event %s is not among the %d %s winner "
                "event(s) within %.0f min of the provider's start in the "
                "named league(s) %s" % (event_slug, len(events), family,
                                       START_TOLERANCE_S / 60.0,
                                       out["league_tokens"])))
        events = named
    out["events_in_window"] = len(events)
    out["set_aside"] = {k: v[:6] for k, v in set_aside.items() if v}
    full = []
    nickname_blocked = []
    college = family in NICKNAME_QUALIFIED_FAMILIES
    for ev in events:
        a, b = ev["participants"]
        if college:
            nk = ev.get("nicknames") or {}
            pa = team_profile(a, family, nickname=nk.get(a))
            pb = team_profile(b, family, nickname=nk.get(b))
            # ONLY ON THE DISCOVERED EVENT may an exact school stand in for
            # the nickname (same_college_team): two independent exact
            # identities then agree; a metered name still needs its nickname
            _xs = event_slug is not None
            h_a = same_college_team(hp, pa, exact_school_confirms=_xs)
            h_b = same_college_team(hp, pb, exact_school_confirms=_xs)
            a_a = same_college_team(ap, pa, exact_school_confirms=_xs)
            a_b = same_college_team(ap, pb, exact_school_confirms=_xs)
            for v in (h_a, h_b, a_a, a_b):
                if v.get("nickname_blocked") and \
                        ev["event_slug"] not in nickname_blocked:
                    nickname_blocked.append(ev["event_slug"])
        else:
            pa, pb = team_profile(a), team_profile(b)
            h_a = same_team(hp, pa, womens_competition=womens)
            h_b = same_team(hp, pb, womens_competition=womens)
            a_a = same_team(ap, pa, womens_competition=womens)
            a_b = same_team(ap, pb, womens_competition=womens)
        # THE ONE-TO-ONE ASSIGNMENT, BOTH ORIENTATIONS. Home to one
        # participant and away to THE OTHER; one participant never serves
        # twice.
        straight = h_a["same"] and a_b["same"]
        swapped = h_b["same"] and a_a["same"]
        if straight or swapped:
            full.append(dict(ev, straight=straight, swapped=swapped,
                             evidence={"home_vs_" + a: h_a,
                                       "home_vs_" + b: h_b,
                                       "away_vs_" + a: a_a,
                                       "away_vs_" + b: a_b}))
        elif h_a["same"] or h_b["same"] or a_a["same"] or a_b["same"]:
            if len(out["partial_matches"]) < 6:
                out["partial_matches"].append({
                    "event_slug": ev["event_slug"],
                    "participants": ev["participants"],
                    "home_matched": h_a["same"] or h_b["same"],
                    "away_matched": a_a["same"] or a_b["same"],
                    "offset_s": ev["offset_s"]})
    out["candidates"] = len(full)
    out["candidate_events"] = [e["event_slug"] for e in full][:6]
    out["nickname_blocked_events"] = nickname_blocked[:6]
    if not full:
        if nickname_blocked:
            return _refuse(out, R_NICKNAME, (
                "%r / %r contain-match a venue school in %s, but the venue's "
                "own nickname is not in the provider's name, so the two may "
                "be different schools (Ohio / Ohio State, Miami FL / Miami "
                "OH). Refused rather than cross-mapped"
                % (home, away, ", ".join(nickname_blocked[:6]))))
        if out["partial_matches"]:
            return _refuse(out, R_ONE_TEAM_ONLY, (
                "%d venue event(s) within %.0f min name one of %r / %r and "
                "not the other (%s). A fixture with one team matched is a "
                "different fixture, and no alias is invented to close the gap"
                % (len(out["partial_matches"]), START_TOLERANCE_S / 60.0,
                   home, away, ", ".join(p["event_slug"]
                                         for p in out["partial_matches"]))))
        return _refuse(out, R_NO_EVENT, (
            "no venue %s winner event within %.0f min of the provider's start "
            "names both %r and %r (%d event(s) in the window)"
            % (family, START_TOLERANCE_S / 60.0, home, away, len(events))))
    if len(full) > 1:
        return _refuse(out, R_AMBIGUOUS, (
            "%d venue events within %.0f min match both teams (%s). Two "
            "candidates are an ambiguity, not a choice"
            % (len(full), START_TOLERANCE_S / 60.0,
               ", ".join(e["event_slug"] for e in full))))
    ev = full[0]
    out["event_slug"] = ev["event_slug"]
    out["participants"] = ev["participants"]
    out["offset_s"] = ev["offset_s"]
    out["name_evidence"] = ev["evidence"]
    if ev["straight"] and ev["swapped"]:
        return _refuse(out, R_ASSIGNMENT_AMBIGUOUS, (
            "each provider team matches both participants of %s (%s), so "
            "which one is HOME is not established"
            % (ev["event_slug"], " / ".join(ev["participants"]))))
    a, b = ev["participants"]
    home_p, away_p = (a, b) if ev["straight"] else (b, a)
    out["assignment"] = {"home": home_p, "away": away_p}
    out["orientation"] = ("PROVIDER_HOME_IS_THE_FIRST_PARTICIPANT"
                          if ev["straight"] else
                          "PROVIDER_HOME_IS_THE_SECOND_PARTICIPANT")

    # ── EVERY ROW OF THE EVENT IS THE REAL FIXTURE, OR NOTHING MAPS ──
    #
    # The venue's own classification and prose, through the same classifier
    # the global path uses. One simulated or unreadable row on the event is
    # enough: a real-looking contract beside a simulated sibling is a
    # contradiction about what the event is.
    for r in ev["rows"]:
        verdict = vreal.classify({k: r.get(k) for k in vreal.CATALOGUE_FIELDS})
        if verdict.get("verdict") != vreal.REAL:
            out["realism"] = {k: verdict.get(k)
                              for k in ("verdict", "evidence", "why")}
            return _refuse(out, verdict.get("refusal")
                           or vreal.R_REALISM_NOT_ESTABLISHED,
                           "%s: %s" % (r.get("market_slug"), verdict.get("why")))
    if priced == "draw":
        return _draw_contract(out, ev, family=family)
    # ── THE CONTRACT THAT PAYS ON THE PRICED TEAM ─────────────────────
    # Home or away, read off the SAME one-to-one assignment: the participant
    # the provider's home matched, or the OTHER one.
    priced_p = home_p if priced == "home" else away_p
    out["priced_participant"] = priced_p
    mine = [r for r in ev["rows"] if participant_name(r, family) == priced_p]
    if family == "soccer":
        # The per-side contract on the priced team, bought LONG. Its SHORT
        # row pays on NOT(team) -- the draw AND the other team's win -- and
        # is never this outcome. The draw contract carries no team and is
        # only ever selected for the draw (`_draw_contract`).
        picked = [r for r in mine if str(r.get("intent") or "") == LONG]
    else:
        # The single two-way contract: the row whose own team is the priced
        # team, with THAT row's intent. SHORT is the venue saying that team
        # is its short side of the contract; the contract still pays on that
        # team, so the payout event is that team and only the ladder changes.
        picked = [r for r in mine
                  if str(r.get("intent") or "") in (LONG, SHORT)]
    if not picked:
        return _refuse(out, R_NO_PRICED_CONTRACT, (
            "event %s lists no %s contract row for the priced team %r"
            % (ev["event_slug"], "LONG per-side" if family == "soccer"
               else "LONG or SHORT", priced_p)))
    if len(picked) > 1:
        return _refuse(out, R_PRICED_CONTRACT_AMBIGUOUS, (
            "event %s lists %d candidate rows for the priced team %r (%s)"
            % (ev["event_slug"], len(picked), priced_p,
               ", ".join(sorted({str(r.get('market_slug')) for r in picked})))))
    row = picked[0]
    slug = str(row.get("market_slug") or "")
    if family != "soccer":
        # THE SIDE SEMANTICS, SHOWN RATHER THAN ASSUMED: exactly two rows on
        # this contract, one LONG and one SHORT, one per participant. Every
        # two-way family (baseball, football) -- soccer alone is per-side.
        sides = [r for r in ev["rows"]
                 if str(r.get("market_slug") or "") == slug]
        pairs = sorted((str(r.get("intent") or ""),
                        participant_name(r, family)) for r in sides)
        if (len(sides) != 2
                or [p[0] for p in pairs] != sorted([LONG, SHORT])
                or {p[1] for p in pairs} != set(ev["participants"])):
            out["contract_sides"] = pairs
            return _refuse(out, R_CONTRACT_SIDES, (
                "contract %s carries %s; a two-way contract must carry one "
                "LONG and one SHORT row, one per participant, before a row's "
                "intent can say which side it buys" % (slug, pairs)))
    # ── THE PERIOD, BY THE SAME RULE THE GLOBAL PATH APPLIES ────────
    #
    # `side` is the venue's own team code for the per-side soccer contract
    # (`atc-<event>-<abbr>`); the two-way baseball contract has no side
    # token and decomposes to `aec-<event>`. `sports_type` IS the venue's
    # `sportsMarketType`, where scope lives.
    period = vmap.period_of_venue_slug(
        slug, side=row.get("team_abbr"), event_slug=row.get("event_slug"),
        kind=row.get("kind"), event_title=row.get("event_title"),
        sports_market_type_v2=None,
        sports_market_type=row.get("sports_type"),
        type_metadata_available=bool(row.get("sports_type")))
    out["period_evidence"] = period
    if period.get("period") != vmap.FULL_MATCH:
        return _refuse(out, (period.get("refusals") or [None])[0]
                       or vmap.R_PERIOD_UNKNOWN,
                       period.get("why") or "period not established")
    out.update(ok=True, us_market_slug=slug, intent=str(row.get("intent")),
               row={k: (v.isoformat() if isinstance(v, _dt.datetime) else v)
                    for k, v in row.items()},
               why=("venue event %s (%s) starts %+.0f s from the provider; "
                    "%s %r is the participant %r, whose %s row on %s is "
                    "bought %s"
                    % (ev["event_slug"], " vs ".join(ev["participants"]),
                       ev["offset_s"], priced,
                       home if priced == "home" else away, priced_p,
                       "per-side" if family == "soccer" else "two-way",
                       slug, row.get("intent"))))
    return out


def _draw_contract(out: dict, ev: dict, *, family) -> dict:
    """THE EVENT'S DRAW CONTRACT, bought LONG, or a named refusal. Pure.

    Three facts, every one from the venue's own catalogue rows, all required:

      1 it is the event's ONE contract whose rows name no team (the per-side
        contracts all carry their team in `team_name`);
      2 its identifier is `<prefix>-<event_slug>-draw` -- the event's own slug
        followed by the side token `draw`, which is also the decomposition the
        period rule below reads (`bettor_venue_mapping.period_of_venue_slug`
        with side 'draw');
      3 it carries exactly one LONG and one SHORT row, and its own question
        asks whether the match ends in a draw -- so LONG is YES on the draw.

    The LONG row pays on the draw and nothing else, so the payout event is
    the draw itself: no complement is involved.
    """
    slug_ev = ev["event_slug"]
    teamless = [r for r in ev["rows"]
                if not str(r.get("team_name") or "").strip()]
    slugs = sorted({str(r.get("market_slug") or "") for r in teamless})
    out["draw_candidates"] = slugs[:6]
    if not slugs:
        return _refuse(out, R_NO_DRAW_CONTRACT, (
            "event %s lists no contract without a team, so no draw contract "
            "exists to price" % (slug_ev,)))
    if len(slugs) > 1:
        return _refuse(out, R_DRAW_CONTRACT_AMBIGUOUS, (
            "event %s lists %d contracts without a team (%s); which one pays "
            "on the draw is not shown" % (slug_ev, len(slugs),
                                          ", ".join(slugs[:6]))))
    slug = slugs[0]
    rows = [r for r in teamless if str(r.get("market_slug") or "") == slug]
    intents = sorted(str(r.get("intent") or "") for r in rows)
    questions = {" ".join(str(r.get("question") or "").lower().split())
                 for r in rows}
    why_not = []
    if not slug.lower().endswith("-%s-draw" % slug_ev.lower()):
        why_not.append("identifier %r is not <prefix>-%s-draw" % (slug,
                                                                  slug_ev))
    if intents != sorted([LONG, SHORT]):
        why_not.append("sides %s are not one LONG and one SHORT" % intents)
    if not questions or not all(re.search(r"\bdraw\b", q) for q in questions):
        why_not.append("its own question does not ask about a draw (%s)"
                       % sorted(questions)[:2])
    if why_not:
        out["draw_contract_evidence"] = {"market_slug": slug,
                                         "intents": intents,
                                         "questions": sorted(questions)[:2]}
        return _refuse(out, R_DRAW_CONTRACT_NOT_ESTABLISHED,
                       "event %s: %s" % (slug_ev, "; ".join(why_not)))
    row = next(r for r in rows if str(r.get("intent") or "") == LONG)
    period = vmap.period_of_venue_slug(
        slug, side="draw", event_slug=row.get("event_slug"),
        kind=row.get("kind"), event_title=row.get("event_title"),
        sports_market_type_v2=None,
        sports_market_type=row.get("sports_type"),
        type_metadata_available=bool(row.get("sports_type")))
    out["period_evidence"] = period
    if period.get("period") != vmap.FULL_MATCH:
        return _refuse(out, (period.get("refusals") or [None])[0]
                       or vmap.R_PERIOD_UNKNOWN,
                       period.get("why") or "period not established")
    out["priced_participant"] = None
    out.update(ok=True, us_market_slug=slug, intent=LONG,
               row={k: (v.isoformat() if isinstance(v, _dt.datetime) else v)
                    for k, v in row.items()},
               why=("venue event %s (%s) starts %+.0f s from the provider; "
                    "the draw is the team-less contract %s (its own question "
                    "asks about a draw), bought LONG"
                    % (slug_ev, " vs ".join(ev["participants"]),
                       ev["offset_s"], slug)))
    return out


#: THE ONE READ. Bounded by family, by the start window, by recency and by a
#: row count whose saturation refuses.
VENUE_NATIVE_SQL = """
    SELECT market_slug, intent, event_slug, event_title, question, kind,
           sports_type, team_abbr, team_name, side_norm, line, signed,
           game_start, updated_at
      FROM us_premap
     WHERE sports_type = ANY($1::text[])
       AND game_start >= to_timestamp($2::float8)
       AND game_start <= to_timestamp($3::float8)
       AND updated_at >= to_timestamp($4::float8)
     ORDER BY event_slug, market_slug, intent
     LIMIT $5
"""


def _identity_shell(*, priced_outcome) -> dict:
    """The keys `ext_pinnacle_loop.resolve_venue_identity` returns, so every
    consumer downstream reads a venue-native identity the same way."""
    return {"ok": False, "us_market_slug": None, "intent": None,
            "refusal": None, "resolver": RESOLVER, "global_slug": None,
            "condition_id": None, "priced_outcome": priced_outcome,
            "contract_identity_basis": CONTRACT_IDENTITY_BASIS}


def identity_from_match(match: dict, *, priced_outcome) -> dict:
    """A `match_event` result, in `resolve_venue_identity`'s shape. Pure.

    THE SIDE, STATED THE SAME WAY THE GLOBAL PATH STATES IT. The intent
    selects the LADDER (short consumes the bids at 1 - bid) and nothing else;
    the payout event is the requested outcome, because the row was chosen
    for having that outcome as its own team; and the probability is the same
    event, so no complement is involved.
    """
    out = _identity_shell(priced_outcome=priced_outcome)
    out["venue_native"] = {k: match.get(k) for k in (
        "version", "event_slug", "participants", "assignment", "orientation",
        "offset_s", "tolerance_s", "candidates", "candidate_events",
        "partial_matches", "events_in_window", "womens_competition",
        "competition", "league_tokens", "set_aside", "why", "priced",
        "priced_participant", "discovered_event_slug", "draw_candidates")}
    out["venue_native"]["name_evidence"] = match.get("name_evidence")
    if not match.get("ok"):
        out["refusal"] = match.get("refusal") or R_NO_EVENT
        out["why"] = match.get("why")
        if match.get("realism"):
            out["realism"] = dict(match["realism"], read_error=None)
        if match.get("period_evidence"):
            out["period_evidence"] = match["period_evidence"]
        return out
    row = match.get("row") or {}
    intent = str(match.get("intent") or "")
    out["us_market_slug"] = match.get("us_market_slug")
    out["venue_event_key"] = row.get("event_slug")
    out["venue_event_key_is"] = (
        "us_premap.event_slug for this contract -- the VENUE's event, which "
        "two contracts on one fixture share. Not the odds provider's event "
        "id, which is a different namespace")
    real = vreal.classify({k: row.get(k) for k in vreal.CATALOGUE_FIELDS})
    out["realism"] = {k: real.get(k) for k in ("verdict", "evidence", "why")}
    out["realism"]["read_error"] = None
    out["intent"] = intent
    out["matched_side_norm"] = row.get("side_norm")
    out["matched_identifier"] = row.get("market_slug")
    out["matched_by"] = MATCHED_BY
    out["matched_question"] = row.get("question")
    out["matched_league_alias"] = None
    out["resolver_asked_for"] = str(priced_outcome)
    out["venue_market_type"] = {
        "source": "us_premap.sports_type (the venue's sportsMarketType)",
        "available": bool(row.get("sports_type")),
        "sports_market_type_v2": None,
        "sports_market_type": row.get("sports_type")}
    period = dict(match.get("period_evidence") or {})
    period["catalogue"] = {k: row.get(k) for k in (
        "kind", "event_slug", "side_norm", "event_title", "sports_type",
        "team_abbr", "team_name")}
    out["period_evidence"] = period
    out["period"] = "FULL_GAME"
    out["period_basis"] = period.get("basis")
    out["ladder_side"] = "BID" if intent == SHORT else "ASK"
    out["intent_selects"] = (
        "the ladder that supplies acquisition cost, and nothing else. It "
        "does NOT name the payout event")
    out["payout_event"] = str(priced_outcome)
    draw = match.get("priced") == "draw"
    out["priced_designation"] = match.get("priced") or "home"
    out["payout_event_basis"] = (PAYOUT_EVENT_BASIS_DRAW if draw
                                 else PAYOUT_EVENT_BASIS)
    out["probability_event"] = str(priced_outcome)
    out["payout_is_complement"] = False
    out["complement_note"] = (
        ("payout_is_complement is false because the contract chosen is the "
         "event's draw contract bought LONG (YES on its own question, 'end in "
         "a draw?'): the probability's event and the payout event are the "
         "SAME event, the draw") if draw else
        "payout_is_complement is false because the contract row was chosen "
        "for carrying the requested outcome as its own team: the "
        "probability's event and the payout event are the SAME event. A "
        "SHORT intent means the cost comes off the bid ladder, not that the "
        "payout inverted")
    out["ok"] = True
    return out


async def resolve_venue_native(conn, *, home, away, commence_time, family,
                               now, competition=None,
                               league_tokens=None, priced="home",
                               draw_label="Draw", event_slug=None) -> dict:
    """Provider fixture -> the venue's own contract for the PRICED outcome
    ('home' by default, 'away', or 'draw' on soccer), or a refusal.

    One bounded read of `us_premap`, then `match_event`. Returns EXACTLY the
    keys `ext_pinnacle_loop.resolve_venue_identity` returns (plus
    `venue_native`, the match evidence, and `contract_identity_basis`), so the
    admission path downstream is the same path. The identity's
    `priced_outcome` / `payout_event` is the provider's own name for that
    outcome: its home or away team, or `draw_label` (the de-vigged set's draw
    outcome name). `event_slug` restricts the match to the venue event the
    discovery named (see `match_event`). Never raises.
    """
    priced_name = {"home": home, "away": away,
                   "draw": draw_label}.get(priced, priced)
    out = _identity_shell(priced_outcome=priced_name)
    types = FAMILY_WINNER_TYPES.get(family)
    if not types:
        out.update(refusal=R_FAMILY,
                   why="family %r is not read by this resolver" % (family,))
        return out
    at = _epoch(commence_time)
    if at is None:
        out.update(refusal=R_PROVIDER_EVENT,
                   why="the provider's commence time %r is not readable"
                   % (commence_time,))
        return out
    try:
        rows = await conn.fetch(
            VENUE_NATIVE_SQL, list(types), at - START_TOLERANCE_S,
            at + START_TOLERANCE_S, float(now) - RESEEN_WITHIN_S,
            MAX_CANDIDATE_ROWS)
    except Exception as exc:                                   # noqa: BLE001
        out.update(refusal=R_READ_FAILED,
                   why=("the venue catalogue read failed (%s), which is not "
                        "evidence that the venue lists nothing"
                        % type(exc).__name__),
                   read_error=type(exc).__name__)
        return out
    rows = [dict(r) for r in rows]
    if len(rows) >= MAX_CANDIDATE_ROWS:
        out.update(refusal=R_READ_TRUNCATED,
                   why=("the candidate read returned its bound of %d rows, so "
                        "a second matching event may lie beyond it"
                        % MAX_CANDIDATE_ROWS))
        return out
    try:
        match = match_event(home=home, away=away, commence_epoch=at,
                            family=family, rows=rows, competition=competition,
                            league_tokens=league_tokens, priced=priced,
                            event_slug=event_slug)
        got = identity_from_match(match, priced_outcome=priced_name)
    except Exception as exc:                                   # noqa: BLE001
        # FAIL CLOSED AND SAY SO. `cycle()` never raises, and a defect here
        # must not take down every other event's decision -- nor may it map
        # anything. The refusal carries the exception's type.
        out.update(refusal=R_MATCH_RAISED, match_error=type(exc).__name__,
                   why=("the venue-native matcher raised %s on %d catalogue "
                        "rows; nothing is mapped"
                        % (type(exc).__name__, len(rows))))
        return out
    got["venue_native"]["rows_read"] = len(rows)
    got["venue_native"]["reseen_within_s"] = RESEEN_WITHIN_S
    return got


def describe() -> dict:
    return {
        "version": VERSION,
        "resolver": RESOLVER,
        "reads": "us_premap, the US venue's own catalogue",
        "family_winner_types": {k: list(v)
                                for k, v in FAMILY_WINNER_TYPES.items()},
        "start_tolerance_s": START_TOLERANCE_S,
        "reseen_within_s": RESEEN_WITHIN_S,
        "affiliation_tokens_dropped": sorted(AFFILIATION_TOKENS),
        "generic_tokens_never_count_alone": True,
        "alias_table": None,
        # cand24: one closed table of the 32 NFL teams' OWN renderings
        # (region in full or its abbreviation + the nickname) -- not an alias
        # between two names, and the nickname must always agree
        "nfl_teams": len(NFL_TEAMS),
        "womens_provider_competitions": sorted(WOMENS_PROVIDER_COMPETITIONS),
        "refusals": list(REFUSALS),
        "priced_outcome": ("EVERY outcome of the de-vigged set: the "
                           "provider's home team, its away team, and the "
                           "draw on a soccer event (P0 incident repair)"),
        "event_level_refusals": sorted(EVENT_LEVEL_REFUSALS),
        "never_repairs": "a missing provider price (NO_PINNACLE_ON_EVENT)",
    }
