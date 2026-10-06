"""ONE FIXTURE, TWO RENDERINGS: THE PROVIDER'S NAMES AGAINST THE FEED'S.

THE LOSS (P1 first-loss census, production 2026-10-06). The metered
discovery provider and the PinnAPI feed name the same team differently, so
the primary selector's exact folded-name match (`pinnapi_primary.name`)
found no WS fixture for whole competitions and the event fell back to the
metered quote -- which arrives 15-27 s old and is refused
QUOTE_STALE_ON_ARRIVAL once our own processing adds the rest (1,445 NCAAF
rows / 52 events in 24 h; research-sql run 37477575060). Where the metered
payload carried no Pinnacle book at all, the event was refused
PINNAPI_PRIMARY_NO_EXACT_FIXTURE outright (33 in the 1 h census).

THE MEASURED PAIRS, both sources' own renderings of one fixture at one
start (research-sql runs 37477575060 and 37478502753, research/
p1_first_loss_evidence*.sql: every metered name of 14 days against every
PinnAPI-native seed name -- a native seed carries the FEED's own names):

  NCAAF   "Troy Trojans" / "Troy", "Southern Mississippi Golden Eagles" /
          "Southern Miss", "Arkansas State Red Wolves" / "Arkansas State",
          "UTSA Roadrunners" / "UTSA", "BYU Cougars" / "BYU", "San Jose
          State Spartans" / "San Jose State", ... -- the provider renders
          SCHOOL + MASCOT, the feed renders the SCHOOL (sometimes its own
          short form)
  MLS     "Vancouver Whitecaps FC" / "Vancouver Whitecaps"
  UNL     "Turkey" / "Turkiye", "Czech Republic" / "Czechia",
          "Bosnia & Herzegovina" / "Bosnia and Herzegovina" (already one
          spelling in `pinnapi_primary.name`)
  Serie B "Operario PR" / "Operario Ferroviario", "Athletic Club (MG)" /
          "Athletic Club", "Botafogo-SP" / "Botafogo SP", "Cuiabá" / "Cuiaba"

WHAT A CANONICAL NAME IS. A deterministic rewrite of ONE rendering, applied
to both sides, never a similarity score between two names:

  1  the folded name (`pinnapi_primary.name`: accents, case, punctuation,
     the "&"/"and" conjunction)
  2  runs of single letters joined ("D.C. United" -> "dc united",
     "Texas A&M" -> "texas am")
  3  a trailing "st" is "state" ("Ohio St" -> "ohio state")
  4  soccer only: the affiliation tokens fc / sc / cf / afc dropped (never
     a squad qualifier: "Arsenal Women" stays a different side)
  5  the closed tables below: the NCAAF provider's SCHOOL + MASCOT rendering
     -> the school; a school's or a country's own other renderings -> one
     spelling; and, scoped to ONE provider competition, a club's two
     renderings observed in production

Each table entry is ONE team's two renderings, never a similarity between
two teams, and the tables are applied to whole names only -- "Ohio State
Buckeyes" is "ohio state", never "ohio". A name no table knows keeps its
rules-1-4 form.

AND A CANONICAL MATCH IS STILL THE WHOLE IDENTITY. `pinnapi_primary` uses it
only when the exact match finds no fixture, and then exactly as the exact
match: BOTH participants, one-to-one, the same sport, inside the start
tolerance, and exactly ONE fixture -- two are AMBIGUOUS by name. The basis
(which rules and table entries fired) rides on the quote's provenance.

THE FEED HOLDS NO FIXTURE FOR EITHER TEAM (`absence`). When no fixture
matches even canonically, the census needs to know whether that is a naming
gap (ours) or the feed simply not carrying the fixture (Pinnacle has not
published it; the metered payload, in production, then carries no Pinnacle
book either). It is called absent ONLY when NO record of the sport in the
feed -- any record, prematch or live child, priced or not -- within a WIDE
window (ABSENCE_WINDOW_S, far wider than the match tolerance) shares a
single non-trivial token or an acronym with EITHER participant, the feed
holds fixtures of that sport at all, and it has evicted nothing. Any doubt
keeps the naming refusal. Pure.
"""
from __future__ import annotations

import re
import unicodedata

VERSION = "PINNAPI_CANONICAL_NAMES_V1"

#: tokens dropped from a soccer club's name (rule 4). Squad qualifiers are
#: never here: "Arsenal Women" is not "Arsenal".
SOCCER_AFFILIATION = frozenset(("fc", "sc", "cf", "afc"))

#: THE NCAAF PROVIDER'S RENDERING: (school, mascot), every name the metered
#: provider sent in 14 days (research-sql run 37478502753 section 1a, 130
#: names) plus the remaining FBS programmes in the same "School Mascot" form.
#: The canonical school is the provider's own school words, folded.
NCAAF_SCHOOL_MASCOT = (
    ("air force", "falcons"), ("akron", "zips"), ("alabama", "crimson tide"),
    ("appalachian state", "mountaineers"), ("arizona state", "sun devils"),
    ("arizona", "wildcats"), ("arkansas", "razorbacks"),
    ("arkansas state", "red wolves"), ("army", "black knights"),
    ("auburn", "tigers"), ("ball state", "cardinals"), ("baylor", "bears"),
    ("boise state", "broncos"), ("boston college", "eagles"),
    ("bowling green", "falcons"), ("buffalo", "bulls"), ("byu", "cougars"),
    ("california", "golden bears"), ("central michigan", "chippewas"),
    ("charlotte", "49ers"), ("cincinnati", "bearcats"), ("clemson", "tigers"),
    ("coastal carolina", "chanticleers"), ("colorado", "buffaloes"),
    ("colorado state", "rams"), ("delaware", "blue hens"),
    ("duke", "blue devils"), ("east carolina", "pirates"),
    ("eastern michigan", "eagles"), ("florida atlantic", "owls"),
    ("florida", "gators"), ("florida international", "panthers"),
    ("florida state", "seminoles"), ("fresno state", "bulldogs"),
    ("georgia", "bulldogs"), ("georgia southern", "eagles"),
    ("georgia state", "panthers"), ("georgia tech", "yellow jackets"),
    ("hawaii", "rainbow warriors"), ("houston", "cougars"),
    ("illinois", "fighting illini"), ("indiana", "hoosiers"),
    ("iowa", "hawkeyes"), ("iowa state", "cyclones"),
    ("jacksonville state", "gamecocks"), ("james madison", "dukes"),
    ("kansas", "jayhawks"), ("kansas state", "wildcats"),
    ("kennesaw state", "owls"), ("kent state", "golden flashes"),
    ("kentucky", "wildcats"), ("liberty", "flames"),
    ("louisiana", "ragin cajuns"), ("louisiana tech", "bulldogs"),
    ("louisville", "cardinals"), ("lsu", "tigers"),
    ("marshall", "thundering herd"), ("maryland", "terrapins"),
    ("mcneese state", "cowboys"), ("memphis", "tigers"),
    ("miami", "hurricanes"), ("miami oh", "redhawks"),
    ("michigan", "wolverines"), ("michigan state", "spartans"),
    ("middle tennessee", "blue raiders"),
    ("minnesota", "golden gophers"), ("mississippi state", "bulldogs"),
    ("missouri state", "bears"), ("missouri", "tigers"),
    ("navy", "midshipmen"), ("nc state", "wolfpack"),
    ("nebraska", "cornhuskers"), ("nevada", "wolf pack"),
    ("new mexico", "lobos"), ("new mexico state", "aggies"),
    ("north carolina", "tar heels"), ("north dakota state", "bison"),
    ("northern illinois", "huskies"), ("north texas", "mean green"),
    ("northwestern", "wildcats"), ("notre dame", "fighting irish"),
    ("ohio", "bobcats"), ("ohio state", "buckeyes"),
    ("oklahoma", "sooners"), ("oklahoma state", "cowboys"),
    ("old dominion", "monarchs"), ("ole miss", "rebels"),
    ("oregon", "ducks"), ("oregon state", "beavers"),
    ("penn state", "nittany lions"), ("pittsburgh", "panthers"),
    ("purdue", "boilermakers"), ("rice", "owls"),
    ("rutgers", "scarlet knights"), ("sacramento state", "hornets"),
    ("sam houston state", "bearkats"), ("san diego state", "aztecs"),
    ("san jose state", "spartans"), ("smu", "mustangs"),
    ("south alabama", "jaguars"), ("south carolina", "gamecocks"),
    ("southern mississippi", "golden eagles"), ("south florida", "bulls"),
    ("stanford", "cardinal"), ("syracuse", "orange"),
    ("tcu", "horned frogs"), ("temple", "owls"),
    ("tennessee", "volunteers"), ("texas am", "aggies"),
    ("texas", "longhorns"), ("texas southern", "tigers"),
    ("texas state", "bobcats"), ("texas tech", "red raiders"),
    ("toledo", "rockets"), ("troy", "trojans"), ("tulane", "green wave"),
    ("tulsa", "golden hurricane"), ("uab", "blazers"), ("ucf", "knights"),
    ("ucla", "bruins"), ("uconn", "huskies"), ("ul monroe", "warhawks"),
    ("umass", "minutemen"), ("unlv", "rebels"), ("usc", "trojans"),
    ("utah state", "aggies"), ("utah", "utes"), ("utep", "miners"),
    ("utsa", "roadrunners"), ("vanderbilt", "commodores"),
    ("virginia", "cavaliers"), ("virginia tech", "hokies"),
    ("wake forest", "demon deacons"), ("washington", "huskies"),
    ("washington state", "cougars"), ("western kentucky", "hilltoppers"),
    ("western michigan", "broncos"), ("west virginia", "mountaineers"),
    ("wisconsin", "badgers"), ("wyoming", "cowboys"),
)

#: A SCHOOL'S OTHER RENDERINGS -> the provider's school words (football).
#: Each is one school's own short or long form ("Southern Miss" is the feed's
#: rendering of "Southern Mississippi", run 37478502753 section 1c).
NCAAF_SCHOOL_RENDERINGS = {
    "southern miss": "southern mississippi",
    "miami ohio": "miami oh",
    "miami florida": "miami", "miami fl": "miami",
    "louisiana monroe": "ul monroe", "ulm": "ul monroe",
    "louisiana lafayette": "louisiana", "ul lafayette": "louisiana",
    "connecticut": "uconn", "massachusetts": "umass",
    "central florida": "ucf", "southern california": "usc",
    "louisiana state": "lsu", "brigham young": "byu",
    "north carolina state": "nc state", "mississippi": "ole miss",
    "texas el paso": "utep", "texas san antonio": "utsa",
    "nevada las vegas": "unlv", "alabama birmingham": "uab",
    "fiu": "florida international", "fau": "florida atlantic",
    "app state": "appalachian state", "sam houston": "sam houston state",
    "texas christian": "tcu", "southern methodist": "smu",
    "pitt": "pittsburgh", "wku": "western kentucky",
    "niu": "northern illinois", "army west point": "army",
    "middle tennessee state": "middle tennessee",
    "hawai i": "hawaii",
}

#: A COUNTRY'S OR A CLUB'S OTHER RENDERING (soccer), both sides. Each is one
#: team: "Turkey" / "Turkiye" and "Czech Republic" / "Czechia" are the
#: production pairs (run 37478502753 section 1c); the rest are the same
#: country's own official short / long forms.
SOCCER_RENDERINGS = {
    "turkey": "turkiye", "czech republic": "czechia",
    "korea republic": "south korea", "republic of korea": "south korea",
    "korea dpr": "north korea",
    "united states": "usa", "united states of america": "usa",
    "macedonia": "north macedonia", "fyr macedonia": "north macedonia",
    "republic of ireland": "ireland", "cabo verde": "cape verde",
    "los angeles galaxy": "la galaxy", "lafc": "los angeles",
    "nycfc": "new york city",
}

#: ONE CLUB'S TWO RENDERINGS INSIDE ONE PROVIDER COMPETITION, applied to the
#: provider's name only (the feed's is the target, as observed). Scoped
#: because a rendering can name a different club elsewhere: the venue's own
#: catalogue lists "athletic club" with no state, so only inside the
#: provider's Serie B key is "Athletic Club (MG)" the feed's "Athletic Club"
#: (production pair, run 37478502753 section 1c: Goias v Athletic Club,
#: 2026-10-06T23:30Z; and Operario Ferroviario v Botafogo SP, 2026-10-07).
COMPETITION_RENDERINGS = {
    "soccer_brazil_serie_b": {
        "athletic club mg": "athletic club",
        "operario pr": "operario ferroviario",
    },
}

#: tokens too common to count as a shared participant token in `absence`
#: (purely affiliative words). Deliberately short: a LONGER list would call
#: more fixtures absent, which is the direction this guard must not err in.
ABSENCE_STOP = frozenset(("fc", "sc", "cf", "afc", "club", "clube", "de",
                          "do", "da", "the", "of"))
#: the wide window the absence scan reads, either side of the provider's
#: start (the match tolerance is 90 min): a fixture the feed lists a day
#: away still names the team, and keeps the refusal ours
ABSENCE_WINDOW_S = 36 * 3600

R_NOT_IN_FEED = "PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM"


def _fold(value) -> str:
    """`pinnapi_primary.name`, restated so this module imports nothing."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(t for t in re.findall(r"[^\W_]+", text.replace("&", " "))
                    if t != "and")


def _join_letters(toks: list) -> list:
    out, run = [], []
    for t in toks:
        if len(t) == 1 and t.isalpha():
            run.append(t)
            continue
        if run:
            out.append("".join(run))
            run = []
        out.append(t)
    if run:
        out.append("".join(run))
    return out


_NCAAF_FULL = {("%s %s" % (s, m)): s for s, m in NCAAF_SCHOOL_MASCOT}


def canonical(value, family=None, sport_key=None, *, side="provider") -> dict:
    """{"name": canonical string, "rules": [rules that fired]} for one
    rendering. `side` "provider" also applies the competition-scoped
    renderings of `sport_key`; "feed" does not (the feed's name is their
    target). Pure."""
    rules = []
    toks = _fold(value).split()
    j = _join_letters(toks)
    if j != toks:
        rules.append("SINGLE_LETTERS_JOINED")
    toks = j
    if len(toks) > 1 and toks[-1] == "st":
        toks = toks[:-1] + ["state"]
        rules.append("TRAILING_ST_IS_STATE")
    if family == "soccer":
        kept = [t for t in toks if t not in SOCCER_AFFILIATION]
        if kept and kept != toks:
            toks = kept
            rules.append("SOCCER_AFFILIATION_DROPPED")
    name = " ".join(toks)
    if family == "soccer":
        scoped = COMPETITION_RENDERINGS.get(str(sport_key or "")) \
            if side == "provider" else None
        if scoped and name in scoped:
            rules.append("COMPETITION_RENDERING:%s:%s" % (sport_key, name))
            name = scoped[name]
        if name in SOCCER_RENDERINGS:
            rules.append("SOCCER_RENDERING:%s" % name)
            name = SOCCER_RENDERINGS[name]
    elif family == "football":
        if name in _NCAAF_FULL:
            rules.append("NCAAF_SCHOOL_MASCOT:%s" % name)
            name = _NCAAF_FULL[name]
        if name in NCAAF_SCHOOL_RENDERINGS:
            rules.append("NCAAF_SCHOOL_RENDERING:%s" % name)
            name = NCAAF_SCHOOL_RENDERINGS[name]
    return {"name": name, "rules": rules}


def _tokens(value, family) -> set:
    c = canonical(value, family, side="feed")["name"]
    raw = set(_join_letters(_fold(value).split())) | set(c.split())
    return {t for t in raw if len(t) > 1 and t not in ABSENCE_STOP}


#: the grammatical words an acronym skips ("Clube de Regatas Brasil" is CRB)
_ACRONYM_SKIP = frozenset(("de", "do", "da", "the", "of", "y", "e"))


def _acronym(value) -> str:
    toks = [t for t in _fold(value).split()
            if len(t) > 1 and t not in _ACRONYM_SKIP]
    return "".join(t[0] for t in toks) if len(toks) >= 2 else ""


def shares_a_token(a, b, family) -> bool:
    """Do two renderings share one non-trivial token, or is one the
    other's acronym ("CRB" / "Clube de Regatas Brasil")? Pure."""
    ta, tb = _tokens(a, family), _tokens(b, family)
    if ta & tb:
        return True
    aa, ab = _acronym(a), _acronym(b)
    return bool((aa and aa in tb) or (ab and ab in ta))


#: ── NOT YET POSTED: NO CANDIDATE AT THE START (software census closure) ──
#:
#: THE NAMING-GAP QUESTION IS ASKED AT THE START, NOT A DAY AWAY. A feed
#: record can only ever be THIS fixture under other names if it starts inside
#: the match tolerance (`pinnapi_primary.START_TOLERANCE_S`): the identity
#: refuses any other start whatever the names say, so a record naming a team
#: at another start time (its previous or next game) can never be matched by
#: any normalisation and is not a naming gap. `absence` therefore also
#: reports, over EVERY record of the sport:
#:
#:   named_near_start  records starting inside the tolerance (or with no
#:                     readable start) that share a token or an acronym with
#:                     either participant -- a candidate the names might hide
#:   named_elsewhere   records naming a participant token, all starting
#:                     OUTSIDE the tolerance (name + start, up to 4)
#:   no_candidate_near_start  the feed holds records of the sport, has
#:                     evicted nothing, and named_near_start is empty
#:
#: `no_candidate_near_start` alone decides nothing: the collector calls the
#: fixture NOT YET POSTED (an EXTERNAL absence) only when the metered
#: provider's payload ALSO carries no Pinnacle book for the event -- two
#: independent sources agreeing that Pinnacle has not published it
#: (ext_pinnacle_loop.no_pinnacle_codes). Any record near the start that
#: shares a token keeps PINNAPI_PRIMARY_NO_EXACT_FIXTURE, ours.
NEAR_START_SAMPLE = 4


def absence(records, *, sport_id, start, home, away, family,
            evicted=0, tolerance_s=None) -> dict:
    """Is the fixture ABSENT from the feed (see the module docstring)? Over
    the feed's raw records (`cache.events` values). {"absent": bool,
    "why": ..., "sport_records": n, "sharing": [up to 4 names]}, plus, when
    `tolerance_s` is given, the near-start scan (NEAR_START_SAMPLE above).
    Pure."""
    sport_records, sharing = 0, []
    near, elsewhere = [], []
    tol = None if tolerance_s is None else float(tolerance_s)
    for ev in records or ():
        if not isinstance(ev, dict) or ev.get("sport_id") != sport_id:
            continue
        sport_records += 1
        st = _epoch(ev.get("startTime"))
        names = [str(p.get("name")) for p in (ev.get("participants") or [])
                 if isinstance(p, dict) and p.get("name")]
        named = [n for n in names if shares_a_token(n, home, family)
                 or shares_a_token(n, away, family)]
        if tol is not None and named:
            if st is None or abs(st - float(start)) <= tol:
                for n in named:
                    if len(near) < NEAR_START_SAMPLE and n not in near:
                        near.append(n)
            elif len(elsewhere) < NEAR_START_SAMPLE:
                elsewhere.append({"names": names, "start_epoch_s": st,
                                  "offset_s": round(st - float(start), 1)})
        if st is not None and abs(st - float(start)) > ABSENCE_WINDOW_S:
            continue
        for n in named:
            if len(sharing) < 4 and n not in sharing:
                sharing.append(n)
    out = {"sport_records": sport_records, "sharing": sharing,
           "window_s": ABSENCE_WINDOW_S, "evicted": int(evicted or 0)}
    if tol is not None:
        out.update(near_start_window_s=tol, named_near_start=near,
                   named_elsewhere=elsewhere,
                   no_candidate_near_start=bool(
                       sport_records > 0 and not evicted and not near))
    if sharing:
        return dict(out, absent=False,
                    why=("feed records within %.0f h name a participant "
                         "token (%s): a naming gap, ours"
                         % (ABSENCE_WINDOW_S / 3600, ", ".join(sharing))))
    if sport_records == 0:
        return dict(out, absent=False,
                    why=("the feed holds no record of sport %s at all: its "
                         "scope, not the fixture, is in question" % sport_id))
    if evicted:
        return dict(out, absent=False,
                    why=("the cache has evicted %d event(s): the fixture may "
                         "have been ours to keep" % int(evicted)))
    return dict(out, absent=True,
                why=("none of the feed's %d record(s) of sport %s within "
                     "%.0f h of the start shares a token or an acronym with "
                     "either participant" % (sport_records, sport_id,
                                             ABSENCE_WINDOW_S / 3600)))


def _epoch(value):
    from datetime import datetime
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
