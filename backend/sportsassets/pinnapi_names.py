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

import functools
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


#: (RC6) THE TOKENS OF A NAME ARE COMPUTED ONCE PER NAME, NOT PER COMPARISON.
#: `absence` compares every participant of every feed record of the sport
#: with both teams of a seed it could not match: ~10,400 `shares_a_token`
#: calls per miss on a 2,600-event cache, and each re-folded, re-canonicalised
#: and re-tokenised BOTH names. Production 2026-10-09 (the API loop watchdog
#: ring, research-sql rc6_api-responsive_loop_stalls.sql): an ext_pinnacle
#: cycle held the API loop 2.5 s inside absence -> shares_a_token -> _tokens.
#: Both helpers are pure functions of (name, family) over this module's
#: closed tables, so a bounded memo answers exactly what the computation
#: answers (an immutable frozenset: the callers only intersect and test
#: membership). The fold (`_fold`) and the canonical rendering (`canonical`)
#: under them are memoised the same way, by the name's text: the registration
#: batch's `pinnapi_primary.fixture_index` folds and canonicalises both names
#: of every cached fixture, on the loop, every batch. Bounded: at most
#: MEMO_NAMES names per helper.
MEMO_NAMES = 65_536


def _fold(value) -> str:
    """`pinnapi_primary.name`, restated so this module imports nothing.
    (RC6) Memoised by its text, as `pinnapi_primary.name` (see MEMO_NAMES)."""
    return _fold_text(str(value or ""))


@functools.lru_cache(maxsize=MEMO_NAMES)
def _fold_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
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
    target). Pure.

    (RC6) Memoised by (the folded text, family, sport_key, side) when the
    three are plain strings (or None) -- `pinnapi_primary.fixture_index`
    canonicalises both names of every cached fixture for every registration
    batch, on the API's event loop (see MEMO_NAMES). Every call still gets
    its own dict and its own rules list."""
    text = str(value or "")
    if all(v is None or type(v) is str for v in (family, sport_key, side)):
        nm, rules = _canonical_memo(text, family, sport_key, side)
        return {"name": nm, "rules": list(rules)}
    got = _canonical(text, family, sport_key, side)
    return {"name": got[0], "rules": list(got[1])}


@functools.lru_cache(maxsize=MEMO_NAMES)
def _canonical_memo(text, family, sport_key, side) -> tuple:
    return _canonical(text, family, sport_key, side)


def _canonical(value, family, sport_key, side) -> tuple:
    """`canonical`'s computation: (name, (rules...)). Pure."""
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
    return name, tuple(rules)


@functools.lru_cache(maxsize=MEMO_NAMES)
def _tokens(value, family) -> frozenset:
    c = canonical(value, family, side="feed")["name"]
    raw = set(_join_letters(_fold(value).split())) | set(c.split())
    return frozenset(t for t in raw if len(t) > 1 and t not in ABSENCE_STOP)


#: the grammatical words an acronym skips ("Clube de Regatas Brasil" is CRB)
_ACRONYM_SKIP = frozenset(("de", "do", "da", "the", "of", "y", "e"))


@functools.lru_cache(maxsize=MEMO_NAMES)
def _acronym(value) -> str:
    toks = [t for t in _fold(value).split()
            if len(t) > 1 and t not in _ACRONYM_SKIP]
    return "".join(t[0] for t in toks) if len(toks) >= 2 else ""


def shares_a_token(a, b, family, *, near_start: bool = False) -> bool:
    """Do two renderings share one non-trivial token, or is one the
    other's acronym ("CRB" / "Clube de Regatas Brasil")? Pure. With
    `near_start`, the NEAR_START_STOP words are not tokens either (RC6.3
    feed-retention: the question asked AT the start, see below)."""
    if near_start:
        ta, tb = _tokens_near(a, family), _tokens_near(b, family)
    else:
        ta, tb = _tokens(a, family), _tokens(b, family)
    if ta & tb:
        return True
    aa, ab = _acronym(a), _acronym(b)
    return bool((aa and aa in tb) or (ab and ab in ta))


#: ── (RC6.3 feed-retention) THE NEAR-START QUESTION, NARROWED TO WHAT CAN
#: HIDE A FIXTURE ──
#:
#: PRODUCTION (software-reds audit, RC6.3b packet hour 2026-10-10 09:54Z: 35
#: events PINNAPI_PRIMARY_NO_EXACT_FIXTURE; 24 h readback research-sql run
#: 38071612807: NO_EXACT rows every hour for MLS, Liga MX, NCAAF and the NFL,
#: NOT_YET_POSTED named in ONE hour of 24). Two things kept the doubt for
#: fixtures Pinnacle had simply not posted:
#:
#:   1 ONE COUNTER FOR EVERY EVICTION. `no_candidate_near_start` required
#:     the cache's `events_evicted` to be zero -- for the whole process life
#:     (281 evictions on the live heartbeat at 17:24Z). The cache now leaves
#:     a TOMBSTONE per eviction (pinnapi_feed.Tombstones: sport, start, the
#:     two names, when), so the question is asked of the evicted records
#:     THEMSELVES: a tombstone of the sport inside the match tolerance that
#:     shares a token with either team keeps the doubt (`tombstones_near`);
#:     the bare counter is telemetry (`evicted_total`). Two things about the
#:     ring itself still keep every doubt: it dropped tombstones for
#:     CAPACITY inside its retention window (`tombstone_overflow`), or the
#:     counter reports evictions the ring never recorded (`unaccounted`).
#:   2 AFFILIATIVE TOKENS AT THE SAME START. A slate of MLS kick-offs at one
#:     start shares "United" and "City"; a Saturday of 40 NCAAF games at one
#:     start shares "State". Every such record "named" a team of every other
#:     game at that start, so no game of the slate could be called unposted.
#:     The near-start test (and ONLY it: the 36 h `sharing` scan is
#:     unchanged, as is every match rule) ignores NEAR_START_STOP -- the
#:     affiliative words State / St, United / Utd, City and the soccer
#:     affiliations -- and ignores a record already CLAIMED one-to-one by a
#:     DIFFERENT metered event (pinnapi_feed.FixtureClaims, written by
#:     pinnapi_primary.match_event on every exact match): one fixture is one
#:     game. A claim covers a record only while the record's names are the
#:     ones matched, and never a claim by an event of the same two names.
#:
#: `blocked_by` names every condition that kept `no_candidate_near_start`
#: False, so a NO_EXACT ledger row says what stood in the way
#: (ext_pinnacle_loop.absence_evidence_codes). NOT_YET_POSTED itself still
#: also requires the metered payload to carry no Pinnacle book.
NEAR_START_STOP = ABSENCE_STOP | frozenset(("state", "st", "united", "utd",
                                            "city"))
#: why `no_candidate_near_start` is False, by name (ledger evidence)
B_NAMED_NEAR_START = "A_RECORD_NEAR_THE_START_NAMES_A_TEAM"
B_TOMBSTONE_NEAR_START = "AN_EVICTED_RECORD_NEAR_THE_START_NAMED_A_TEAM"
B_TOMBSTONE_OVERFLOW = "THE_TOMBSTONE_RING_OVERFLOWED_INSIDE_ITS_WINDOW"
B_EVICTIONS_UNACCOUNTED = "EVICTIONS_THE_TOMBSTONE_RING_DOES_NOT_ACCOUNT_FOR"
B_NO_RECORD_OF_SPORT = "THE_FEED_HOLDS_NO_RECORD_OF_THE_SPORT"


@functools.lru_cache(maxsize=MEMO_NAMES)
def _tokens_near(value, family) -> frozenset:
    return _tokens(value, family) - NEAR_START_STOP


def _claimed_by_another(claims, ev, asking: frozenset, now) -> bool:
    """Is this record covered by a claim (pinnapi_feed.FixtureClaims) of a
    metered event OTHER than the one asking -- so it cannot be the asking
    fixture under other names? The claim must still describe the record's
    current names, and a claim by an event of the asking event's own two
    names (the same game listed twice) covers nothing."""
    if claims is None or not isinstance(ev, dict):
        return False
    getter = getattr(claims, "get", None)
    if not callable(getter):
        return False
    names = frozenset(_fold(p.get("name"))
                      for p in (ev.get("participants") or [])
                      if isinstance(p, dict) and p.get("name"))
    for fid in (ev.get("id"), ev.get("parentId")):
        if fid is None:
            continue
        try:
            c = getter(fid, None, now=now) if now is not None else getter(fid)
        except TypeError:
            c = getter(fid)
        if not isinstance(c, dict):
            continue
        if frozenset(c.get("fixture_names") or ()) != names:
            continue
        if frozenset(c.get("names") or ()) == asking:
            continue
        return True
    return False


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
#:   tombstones_near   evicted records of the sport (pinnapi_feed.Tombstones)
#:                     starting inside the tolerance that shared a token or
#:                     an acronym with either participant (RC6.3)
#:   no_candidate_near_start  the feed holds records of the sport,
#:                     named_near_start and tombstones_near are empty, and
#:                     the tombstone ring accounts for every eviction without
#:                     an overflow inside its window (RC6.3: before, ANY
#:                     eviction in the process's life kept this False)
#:   blocked_by        every condition that kept it False, by name
#:
#: `no_candidate_near_start` alone decides nothing: the collector calls the
#: fixture NOT YET POSTED (an EXTERNAL absence) only when the metered
#: provider's payload ALSO carries no Pinnacle book for the event -- two
#: independent sources agreeing that Pinnacle has not published it
#: (ext_pinnacle_loop.no_pinnacle_codes). Any record near the start that
#: shares a token keeps PINNAPI_PRIMARY_NO_EXACT_FIXTURE, ours.
NEAR_START_SAMPLE = 4


class AbsenceIndex:
    """(RC6) ONE PASS OVER THE FEED'S RECORDS FOR A BATCH OF MISSES.

    `absence` asks, for every record of the sport, whether a participant
    shares a token or an acronym with either team -- a scan of the whole
    feed per seed the matcher could not place. For a batch of registrations
    (pinnapi_primary.fixture_index, built with no await between it and the
    last registration) this inverts that scan once: token -> the records
    whose participants carry it, and acronym -> the records whose
    participants abbreviate to it. A seed's candidates are the records that
    carry one of its tokens, whose acronym is one of its tokens, or that
    carry its acronym -- exactly the records `shares_a_token` can answer True
    for -- and `absence` then runs its own unchanged test on those records,
    in their order. Every other record of the sport names neither team, so
    it only counts toward `sport_records`, which is counted here. The answer
    is the scan's, verbatim (test_rc6_api_responsive_offloop pins it).
    Built for ONE (records, sport, family): the tokens depend on the family."""

    def __init__(self, records, sport_id, family):
        self.records, self.sport_id, self.family = records, sport_id, family
        self.rows: list = []
        self.postings: dict = {}
        self.acronyms: dict = {}
        for pos, ev in enumerate(records or ()):
            if not isinstance(ev, dict) or ev.get("sport_id") != sport_id:
                continue
            self.rows.append(pos)
            for p in ev.get("participants") or []:
                if not (isinstance(p, dict) and p.get("name")):
                    continue
                n = str(p.get("name"))
                for t in _tokens(n, family):
                    self.postings.setdefault(t, set()).add(pos)
                a = _acronym(n)
                if a:
                    self.acronyms.setdefault(a, set()).add(pos)

    def candidates(self, home, away) -> list:
        """The records (in their order) `shares_a_token` might name a team
        in -- a superset of those it does."""
        keys = _tokens(home, self.family) | _tokens(away, self.family)
        got: set = set()
        for t in keys:
            got |= self.postings.get(t, set())
            got |= self.acronyms.get(t, set())
        for a in (_acronym(home), _acronym(away)):
            if a:
                got |= self.postings.get(a, set())
        return [self.records[pos] for pos in sorted(got)]


def absence(records, *, sport_id, start, home, away, family,
            evicted=0, tolerance_s=None, prepared=None, tombstones=None,
            claims=None, overflow=False, unaccounted=None,
            now=None) -> dict:
    """Is the fixture ABSENT from the feed (see the module docstring)? Over
    the feed's raw records (`cache.events` values). {"absent": bool,
    "why": ..., "sport_records": n, "sharing": [up to 4 names]}, plus, when
    `tolerance_s` is given, the near-start scan (NEAR_START_SAMPLE above).
    Pure. `prepared` (an AbsenceIndex of these same records, this sport and
    family) answers the same, scanning only the records that can name a
    team.

    (RC6.3 feed-retention, see NEAR_START_STOP) `tombstones`: the cache's
    evicted records inside their retention window (dicts with sport_id,
    start_s, home, away, protected); `claims`: pinnapi_feed.FixtureClaims
    (or any mapping of fixture id -> claim); `overflow`: the ring dropped
    tombstones for capacity inside its window; `unaccounted`: evictions the
    counter reports beyond the ring's (None: computed as `evicted` minus
    the tombstones given -- a caller without a ring passes nothing and a
    bare counter then keeps the doubt, exactly as before)."""
    sport_records, sharing = 0, []
    near, elsewhere = [], []
    tol = None if tolerance_s is None else float(tolerance_s)
    asking = frozenset((_fold(home), _fold(away)))
    if (prepared is not None and prepared.records is records
            and prepared.sport_id == sport_id and prepared.family == family):
        scan, sport_records = prepared.candidates(home, away), \
            len(prepared.rows)
        counted = True
    else:
        scan, counted = records or (), False
    claimed_away = 0
    for ev in scan:
        if not isinstance(ev, dict) or ev.get("sport_id") != sport_id:
            continue
        if not counted:
            sport_records += 1
        st = _epoch(ev.get("startTime"))
        names = [str(p.get("name")) for p in (ev.get("participants") or [])
                 if isinstance(p, dict) and p.get("name")]
        named = [n for n in names if shares_a_token(n, home, family)
                 or shares_a_token(n, away, family)]
        if tol is not None and named:
            if st is None or abs(st - float(start)) <= tol:
                # AT THE START: the narrowed test (affiliative words are not
                # a shared name), and never a record another metered event
                # already is
                narrow = [n for n in named
                          if shares_a_token(n, home, family, near_start=True)
                          or shares_a_token(n, away, family, near_start=True)]
                if narrow and _claimed_by_another(claims, ev, asking, now):
                    claimed_away += 1
                    narrow = []
                for n in narrow:
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
    # THE EVICTED RECORDS, ASKED THE SAME QUESTIONS (RC6.3)
    tomb_near, tomb_sharing, tomb_total, tomb_protected = [], [], 0, 0
    for t in (tombstones or ()):
        if not isinstance(t, dict) or t.get("sport_id") != sport_id:
            continue
        tomb_total += 1
        if t.get("protected"):
            tomb_protected += 1
        st = t.get("start_s")
        if st is None:
            st = _epoch(t.get("startTime"))
        tnames = [str(t.get(k)) for k in ("home", "away") if t.get(k)]
        named = [n for n in tnames if shares_a_token(n, home, family)
                 or shares_a_token(n, away, family)]
        if not named:
            continue
        if tol is not None and (st is None or abs(float(st) - float(start))
                                <= tol):
            narrow = [n for n in named
                      if shares_a_token(n, home, family, near_start=True)
                      or shares_a_token(n, away, family, near_start=True)]
            for n in narrow:
                if len(tomb_near) < NEAR_START_SAMPLE and n not in tomb_near:
                    tomb_near.append(n)
        if st is None or abs(float(st) - float(start)) <= ABSENCE_WINDOW_S:
            for n in named:
                if len(tomb_sharing) < 4 and n not in tomb_sharing:
                    tomb_sharing.append(n)
    evicted_total = int(evicted or 0)
    if unaccounted is None:
        unaccounted = max(0, evicted_total - tomb_total) \
            if tombstones is not None else evicted_total
    unaccounted = int(unaccounted or 0)
    overflow = bool(overflow)
    out = {"sport_records": sport_records, "sharing": sharing,
           "window_s": ABSENCE_WINDOW_S, "evicted": evicted_total,
           "evicted_total": evicted_total,
           "tombstones_of_sport": tomb_total,
           "tombstones_protected": tomb_protected,
           "tombstones_sharing": tomb_sharing,
           "tombstone_overflow": overflow,
           "evictions_unaccounted": unaccounted,
           "records_claimed_by_another_event": claimed_away}
    if tol is not None:
        blocked = []
        if sport_records == 0:
            blocked.append(B_NO_RECORD_OF_SPORT)
        if near:
            blocked.append(B_NAMED_NEAR_START)
        if tomb_near:
            blocked.append(B_TOMBSTONE_NEAR_START)
        if overflow:
            blocked.append(B_TOMBSTONE_OVERFLOW)
        if unaccounted:
            blocked.append(B_EVICTIONS_UNACCOUNTED)
        out.update(near_start_window_s=tol, named_near_start=near,
                   named_elsewhere=elsewhere, tombstones_near=tomb_near,
                   no_candidate_near_start=not blocked,
                   blocked_by=blocked)
    if sharing:
        return dict(out, absent=False,
                    why=("feed records within %.0f h name a participant "
                         "token (%s): a naming gap, ours"
                         % (ABSENCE_WINDOW_S / 3600, ", ".join(sharing))))
    if sport_records == 0:
        return dict(out, absent=False,
                    why=("the feed holds no record of sport %s at all: its "
                         "scope, not the fixture, is in question" % sport_id))
    if tomb_sharing:
        return dict(out, absent=False,
                    why=("evicted feed records within %.0f h named a "
                         "participant token (%s): the fixture may have been "
                         "ours to keep" % (ABSENCE_WINDOW_S / 3600,
                                           ", ".join(tomb_sharing))))
    if overflow:
        return dict(out, absent=False,
                    why=("the tombstone ring dropped evicted records inside "
                         "its window: what the cache evicted is not fully "
                         "known"))
    if unaccounted:
        return dict(out, absent=False,
                    why=("the cache counts %d eviction(s) its tombstone ring "
                         "does not account for: the fixture may have been "
                         "ours to keep" % unaccounted))
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
