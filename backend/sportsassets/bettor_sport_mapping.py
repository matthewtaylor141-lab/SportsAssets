"""§1. SPORT AND LEAGUE, FROM THE VENUE'S OWN PAYLOAD.

Owner directive, "STOP here" §1:

    "Trace exactly why sport is UNKNOWN on every row... Do not simply
    patch UNKNOWN with an inferred string. Build the canonical mapping
    from the actual venue payload into the observation schema, preserve
    the raw source field."

THE DEFECT, TRACED. `bettor_opportunities.sport` and `.league` are NULL
on all 9,702 rows, and the cause is not missing venue data:

    shadow_bettor.UNIVERSE_SQL selects
        p.identifier, p.market_slug, p.event_slug, p.event_title,
        p.side_norm, p.kind
    from us_premap -- a table that also carries `sports_type`
    (market.sportsMarketType, populated on 100% of 166,625 rows) and
    `team_league` (team.league, populated on 63%).

    universe() then builds its dicts WITHOUT a sport or league key at
    all, so `opportunity.get("sport")` is None on every row and the
    column is written NULL.

Measured 2026-09-20 against production: every one of the 9,702
observations joins to a us_premap row (no_premap_row = 0), 99.6% of
them to a row whose sports_type names a sport, and 77.3% to one that
also names a league. The data was there the whole time; the SELECT did
not ask for it.

────────────────────────────────────────────────────────────────────
TWO SOURCES, IN PRECEDENCE, AND NEITHER IS A GUESS.

1. VENUE_MARKET_TYPE_PREFIX -- the sport named inside
   market.sportsMarketType. 'football_team_full_game_spread' is the
   venue saying football on the market row itself. Present on 100% of
   premap rows, so this is the primary source.

   LONGEST MATCH, NOT split_part(x,'_',1). 'table_tennis_match_winner'
   reads 'table' under a naive first-token split, and 'table' is not a
   sport. The prefix set is ordered longest-first and every entry was
   observed in production, not imagined.

2. VENUE_LEAGUE_ATTESTED -- a LEAGUE -> SPORT entry is admitted ONLY
   where the venue's own rows attest it: that league appears with
   exactly one sport-naming market-type prefix, over at least
   ATTESTATION_MIN_ROWS rows. This is the fallback for `futures`
   markets, whose type names no sport.

   'cbb', 'nba', 'ucl' and 'dfb' are NOT in the table. They appear in
   production only on `futures` rows, so this venue has never stated
   their sport, and writing basketball beside 'nba' from general
   knowledge is exactly the inferred string the directive forbids --
   however obviously true it happens to be.

3. Otherwise SPORT is NOT_IDENTIFIED with a machine-readable reason.
────────────────────────────────────────────────────────────────────

THE RAW FIELDS ARE PRESERVED. SPORT_SOURCE_RAW carries the venue string
the sport was read from and LEAGUE_SOURCE_RAW the league string,
verbatim and unfolded. A normalised value whose source is gone cannot
be re-derived when the mapping changes, and the mapping WILL change:
the venue adds leagues.

NOT REUSED: `copy_sports.sport_of`. That function defaults every
unmapped league to "soccer", which is correct for the mirror lane it
serves and is exactly the silent-inference this module must not do. Its
SPORT_OF *dict* is not copied here either -- the table below is
generated from the venue's own attestations, and a copy of somebody
else's table would drift from both.

NOT USED: event.tags[].league.sportId. It is the venue's numeric sport
id and would be the best source of all, but the premap sweep does not
persist event tags, so no stored row carries it. It is declared below
as the preferred future source rather than left as a surprise.
"""

from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"

MAPPING_VERSION = "BETTOR_SPORT_MAPPING_V1"

# The production read the table below was generated from. A mapping
# without a date is a mapping nobody can re-derive.
ATTESTED_FROM = {
    "query": "research/bettor_sport_vocabulary.sql",
    "at": "2026-09-20T18:06Z",
    "database": "sportsassets-db",
    "premapRows": 166625,
    "distinctTeamLeagues": 111,
}

# ── source 1: the sport named inside market.sportsMarketType ─────────
#
# Ordered LONGEST FIRST so 'table_tennis_' wins over any shorter match.
# Every prefix was observed in production; the row counts are the
# evidence and are kept so a prefix nobody has seen since is visible.

MARKET_TYPE_PREFIXES = (
    ("table_tennis_", "table_tennis", 5504),
    ("basketball_", "basketball", 1110),
    ("baseball_", "baseball", 26572),
    ("cricket_", "cricket", 18),
    ("esports_", "esports", 1804),
    ("football_", "football", 60760),
    ("hockey_", "hockey", 130),
    ("soccer_", "soccer", 18268),
    ("tennis_", "tennis", 3160),
    ("boxing_", "boxing", 36),
    ("darts_", "darts", 66),
    ("ufc_", "ufc", 1104),
)

# Prefixes that name a MARKET TYPE and no sport. Listed by name so a row
# carrying one is a KNOWN absence rather than an unmatched string.
TYPE_PREFIXES_NAMING_NO_SPORT = {
    "futures": ("a futures market names no sport in its type. The "
                "league is the only venue-stated source, and many "
                "futures rows carry no league either"),
    "moneyline": ("a bare moneyline type names no sport. Observed on "
                  "the 'boxing' and 'pll' leagues"),
}

# A VENUE-STATED CATEGORY THAT IS NOT A SPORT. 'election_' markets
# (production: `paccc-usho-midterms-2026-11-03-dem`) carry a real,
# venue-stated category and no sport. That is a DIFFERENT fact from
# "we could not tell", and collapsing the two would put political
# markets into whichever sport cohort happened to absorb the unknowns.
NON_SPORT_CATEGORIES = {
    "election_": "election",
}

R_NOT_A_SPORT = "MARKET_CATEGORY_IS_NOT_A_SPORT"

WHY_A_NON_SPORT_IS_NOT_UNKNOWN = (
    "an election market's category is stated by the venue and is not a "
    "sport. Recording it as NOT_IDENTIFIED would put it in the same "
    "bucket as markets whose sport we simply failed to read, and the "
    "cohort split would then be over a bucket containing both")

WHY_LONGEST_MATCH = (
    "split_part(sports_type, '_', 1) reads 'table_tennis_match_winner' "
    "as 'table', which is not a sport. The prefix set is ordered "
    "longest-first so the venue's two-word sports resolve correctly")

# ── source 2: LEAGUE -> SPORT, only where the venue attests it ───────
#
# Generated from the production cross-tab: a league admitted here
# appeared with exactly ONE sport-naming market-type prefix. The count
# is the number of us_premap rows attesting it.

ATTESTATION_MIN_ROWS = 20

WHY_A_THRESHOLD = (
    "a league attested by a handful of rows can be attested by a "
    "handful of BAD rows. Production shows league 'pdc' -- the darts "
    "tour -- on four soccer_* market types, which is either a venue "
    "data error or a league code collision. Either way four rows is "
    "not an attestation, so entries below the threshold are recorded "
    "as LOW_EVIDENCE and not admitted")

_ATTESTED = {
    # league: (sport, attesting us_premap rows)
    "cfb": ("football", 36847), "mlb": ("baseball", 25428),
    "nfl": ("football", 20170), "setkameua": ("table_tennis", 3050),
    "mls": ("soccer", 2392), "lal": ("soccer", 1608),
    "epl": ("soccer", 1428), "sea": ("soccer", 1418),
    "lg1": ("soccer", 1404), "cs2": ("esports", 990),
    "lmx": ("soccer", 934), "bun": ("soccer", 818),
    "itfme": ("tennis", 788), "wnba": ("basketball", 770),
    "setkamecz": ("table_tennis", 630), "utr": ("tennis", 580),
    "setkamemd": ("table_tennis", 566), "wta": ("tennis", 534),
    "ncaaws": ("soccer", 424), "itfwo": ("tennis", 392),
    "lol": ("esports", 314), "ncaams": ("soccer", 312),
    "dota2": ("esports", 266), "atp": ("tennis", 190),
    "atpcq": ("tennis", 162), "tdp": ("soccer", 136),
    "setkawoua": ("table_tennis", 120), "ere": ("soccer", 108),
    "nhl": ("hockey", 90), "r6": ("esports", 86),
    "arg2": ("soccer", 84), "bra": ("soccer", 80),
    "rl": ("esports", 80), "npb": ("baseball", 70),
    "eflch": ("soccer", 60), "lpa": ("soccer", 52),
    "uslc": ("soccer", 52), "ufc": ("ufc", 46),
    "modus": ("darts", 46), "lco": ("soccer", 44),
    "khl": ("hockey", 42), "wtadb": ("tennis", 40),
    "boxing": ("boxing", 38), "kbo": ("baseball", 38),
    "lal2": ("soccer", 36), "ligpor": ("soccer", 36),
    "uru1": ("soccer", 32), "valorant": ("esports", 32),
    "intf": ("soccer", 32), "uwcl": ("soccer", 32),
    "nor1": ("soccer", 32), "lnbp": ("basketball", 28),
    "nwsl": ("soccer", 28), "brb": ("soccer", 28),
    "uzb1": ("soccer", 28), "ecu1": ("soccer", 28),
    "pl1": ("soccer", 28), "ekst": ("soccer", 24),
    "swsl": ("soccer", 24), "srb": ("soccer", 24),
    "atpdb": ("tennis", 24), "slr": ("soccer", 24),
    "isl1": ("soccer", 24), "alsv": ("soccer", 24),
    "tsl": ("soccer", 24), "els": ("soccer", 24),
    "nls": ("soccer", 24), "sld": ("soccer", 20),
    "lng": ("soccer", 20), "nb1": ("soccer", 20),
    "flc": ("soccer", 20), "pdcdarts": ("darts", 20),
    "btla": ("soccer", 20), "svnp": ("soccer", 20),
    "atbl": ("soccer", 20),
    # Below the threshold. Kept with their counts so the gap is
    # visible and so a later read can see them cross it.
    "bbl": ("basketball", 16), "par2": ("soccer", 16),
    "pvl": ("soccer", 16), "wsl": ("soccer", 16),
    "kl1": ("soccer", 16), "lexp": ("soccer", 16),
    "lpc": ("soccer", 16), "nbl": ("basketball", 14),
    "wtt": ("table_tennis", 14), "den1": ("soccer", 12),
    "swe2": ("soccer", 12), "lkl": ("basketball", 12),
    "dwcs": ("ufc", 10), "bcl": ("basketball", 8),
    "scp": ("soccer", 8), "ykk": ("soccer", 8),
    "t20icr": ("cricket", 6), "acb": ("basketball", 6),
    "swcl": ("soccer", 4), "irlp": ("soccer", 4),
    "pdc": ("soccer", 4), "lig2": ("soccer", 4),
    "eurolg": ("basketball", 4), "etpl": ("cricket", 4),
    "odicr": ("cricket", 4), "vkl": ("soccer", 4),
    "odcup": ("cricket", 2), "cplcr": ("cricket", 2),
    "uecl": ("soccer", 2),
}

LEAGUE_TO_SPORT = {lg: sport for lg, (sport, n) in _ATTESTED.items()
                   if n >= ATTESTATION_MIN_ROWS}
LEAGUE_ATTESTATION = {lg: n for lg, (sport, n) in _ATTESTED.items()}
LOW_EVIDENCE_LEAGUES = {lg: {"sportSeen": sport, "rows": n}
                        for lg, (sport, n) in _ATTESTED.items()
                        if n < ATTESTATION_MIN_ROWS}

# Leagues production carries that this venue has NEVER stated a sport
# for: they appear only on `futures` rows. Named so their absence is a
# finding rather than an oversight.
LEAGUES_WITH_NO_VENUE_STATED_SPORT = {
    "cbb": 142, "nba": 120, "ucl": 72, "dfb": 36, "cdb": 32,
    "lib": 30, "pll": 2,
}

WHY_NOT_INFERRED = (
    "'nba' is obviously basketball and it is not in the table, because "
    "this venue has never said so on any stored row -- every nba row "
    "production carries is a `futures` market. Writing basketball "
    "beside it from general knowledge is the inferred string the "
    "directive forbids, and the fact that it happens to be true is "
    "what makes the habit dangerous rather than safe")

# ── the preferred source we do not yet persist ───────────────────────

PREFERRED_FUTURE_SOURCE = {
    "field": "event.tags[].league.sportId",
    "status": "NOT_PERSISTED",
    "why": ("the venue's own numeric sport id, verified present on "
            "20/20 events in the beta48 payload audit. It would remove "
            "the league table entirely. The premap sweep does not "
            "store event tags, so no row carries it today"),
    "readerAlreadyExists": "research/run85_trackbl_block.py primary_tag",
    "wouldRequire": "persisting event tags on the premap sweep",
}

# ── machine-readable reasons a row stays unmapped ────────────────────

R_NO_PREMAP_ROW = "NO_PREMAP_ROW_FOR_THIS_MARKET_SLUG"
R_NO_SPORTS_TYPE = "PREMAP_ROW_CARRIES_NO_SPORTS_MARKET_TYPE"
R_TYPE_NAMES_NO_SPORT = "MARKET_TYPE_NAMES_NO_SPORT"
R_TYPE_PREFIX_UNKNOWN = "MARKET_TYPE_PREFIX_NOT_IN_DECLARED_SET"
R_LEAGUE_ABSENT = "NO_LEAGUE_AND_MARKET_TYPE_NAMES_NO_SPORT"
R_LEAGUE_NOT_ATTESTED = "LEAGUE_HAS_NO_VENUE_ATTESTED_SPORT"
R_LEAGUE_LOW_EVIDENCE = "LEAGUE_ATTESTATION_BELOW_THRESHOLD"

UNRESOLVED_REASONS = (
    R_NO_PREMAP_ROW, R_NO_SPORTS_TYPE, R_TYPE_NAMES_NO_SPORT,
    R_TYPE_PREFIX_UNKNOWN, R_LEAGUE_ABSENT, R_LEAGUE_NOT_ATTESTED,
    R_LEAGUE_LOW_EVIDENCE, R_NOT_A_SPORT,
)

SOURCE_MARKET_TYPE = "VENUE_MARKET_TYPE_PREFIX"
SOURCE_LEAGUE = "VENUE_LEAGUE_ATTESTED"

NO_FUZZY_MATCHING = (
    "sport and league come from venue-native fields only. No title "
    "parsing, no team-name matching, no price matching, and no slug "
    "guessing -- the slug's league token is the venue's own string but "
    "it is read from the stored column, not scraped back out of the "
    "identifier")


def sport_from_market_type(sports_type):
    """The sport the venue named inside sportsMarketType, or a reason."""
    raw = (sports_type or "").strip().lower()
    if not raw:
        return None, R_NO_SPORTS_TYPE
    for prefix, sport, _n in MARKET_TYPE_PREFIXES:
        if raw.startswith(prefix):
            return sport, None
    for prefix in NON_SPORT_CATEGORIES:
        if raw.startswith(prefix):
            # Stated by the venue, and not a sport. Distinct from
            # "we could not tell".
            return None, R_NOT_A_SPORT
    head = raw.split("_", 1)[0]
    if head in TYPE_PREFIXES_NAMING_NO_SPORT:
        return None, R_TYPE_NAMES_NO_SPORT
    return None, R_TYPE_PREFIX_UNKNOWN


def sport_from_league(team_league):
    """The sport the venue attests for this league, or a reason."""
    lg = (team_league or "").strip().lower()
    if not lg:
        return None, R_LEAGUE_ABSENT
    if lg in LEAGUE_TO_SPORT:
        return LEAGUE_TO_SPORT[lg], None
    if lg in LOW_EVIDENCE_LEAGUES:
        return None, R_LEAGUE_LOW_EVIDENCE
    return None, R_LEAGUE_NOT_ATTESTED


def classify(*, sports_type=None, team_league=None,
             has_premap_row=True) -> dict:
    """SPORT and LEAGUE for one market, with the raw source preserved.

    Returns NOT_IDENTIFIED and a machine-readable reason rather than a
    guess. The raw strings travel so the value can be re-derived when
    the mapping changes.
    """
    out = {
        "mappingVersion": MAPPING_VERSION,
        # The raw venue strings, verbatim. Never folded, never dropped.
        "SPORT_SOURCE_RAW": sports_type if sports_type else NOT_IDENTIFIED,
        "LEAGUE_SOURCE_RAW": team_league if team_league else NOT_IDENTIFIED,
        "noFuzzyMatching": NO_FUZZY_MATCHING,
    }
    if not has_premap_row:
        out.update({"SPORT": NOT_IDENTIFIED, "LEAGUE": NOT_IDENTIFIED,
                    "SPORT_SOURCE": NOT_IDENTIFIED,
                    "UNRESOLVED_MAPPING_REASON": R_NO_PREMAP_ROW})
        return out

    # LEAGUE is the venue's own string, lower-cased and nothing else.
    lg = (team_league or "").strip().lower()
    out["LEAGUE"] = lg or NOT_IDENTIFIED

    sport, why = sport_from_market_type(sports_type)
    if sport:
        out.update({"SPORT": sport, "SPORT_SOURCE": SOURCE_MARKET_TYPE,
                    "UNRESOLVED_MAPPING_REASON": None})
        return out

    if why == R_NOT_A_SPORT:
        # The venue stated a category that is not a sport. The league
        # fallback is not consulted -- it would answer a question the
        # row does not pose.
        out.update({"SPORT": NOT_IDENTIFIED,
                    "SPORT_SOURCE": SOURCE_MARKET_TYPE,
                    "MARKET_CATEGORY": NON_SPORT_CATEGORIES[
                        next(p for p in NON_SPORT_CATEGORIES
                             if (sports_type or "").strip().lower()
                             .startswith(p))],
                    "UNRESOLVED_MAPPING_REASON": R_NOT_A_SPORT,
                    "whyNotUnknown": WHY_A_NON_SPORT_IS_NOT_UNKNOWN})
        return out

    # The market type named no sport. Fall back to the league, but only
    # where the venue itself attests it.
    sport2, why2 = sport_from_league(team_league)
    if sport2:
        out.update({
            "SPORT": sport2, "SPORT_SOURCE": SOURCE_LEAGUE,
            "leagueAttestingRows": LEAGUE_ATTESTATION.get(lg),
            "whyFallback": why,
            "UNRESOLVED_MAPPING_REASON": None,
        })
        return out

    out.update({"SPORT": NOT_IDENTIFIED, "SPORT_SOURCE": NOT_IDENTIFIED,
                "UNRESOLVED_MAPPING_REASON": why2 or why,
                "marketTypeReason": why})
    if lg and lg in LEAGUES_WITH_NO_VENUE_STATED_SPORT:
        out["whyNotInferred"] = WHY_NOT_INFERRED
    return out


def report(rows) -> dict:
    """§1's requested figures over a set of classified rows."""
    rows = list(rows)
    identified = [r for r in rows if r.get("SPORT") not in
                  (None, NOT_IDENTIFIED)]
    sports, leagues, reasons = {}, {}, {}
    for r in rows:
        s = r.get("SPORT")
        if s and s != NOT_IDENTIFIED:
            sports[s] = sports.get(s, 0) + 1
        lg = r.get("LEAGUE")
        if lg and lg != NOT_IDENTIFIED:
            leagues[lg] = leagues.get(lg, 0) + 1
        why = r.get("UNRESOLVED_MAPPING_REASON")
        if why:
            reasons[why] = reasons.get(why, 0) + 1
    n = len(rows)
    return {
        "SPORT_MAPPING_STATUS": (
            "BUILT_NOT_YET_WIRED_TO_PRODUCTION" if not n
            else "APPLIED"),
        "mappingVersion": MAPPING_VERSION,
        "attestedFrom": dict(ATTESTED_FROM),
        "TOTAL_ROWS_ELIGIBLE_FOR_MAPPING": n,
        "SPORT_IDENTIFIED_ROWS": len(identified),
        "SPORT_UNKNOWN_ROWS": n - len(identified),
        "SPORT_IDENTIFICATION_RATE": (
            NOT_IDENTIFIED if not n else round(len(identified) / n, 4)),
        "SPORTS_OBSERVED": dict(sorted(sports.items(),
                                       key=lambda kv: -kv[1])),
        "LEAGUES_OBSERVED": dict(sorted(leagues.items(),
                                        key=lambda kv: -kv[1])),
        "UNRESOLVED_MAPPING_REASONS": dict(sorted(reasons.items(),
                                                  key=lambda kv: -kv[1])),
        "declaredReasons": list(UNRESOLVED_REASONS),
    }


def describe() -> dict:
    return {
        "mappingVersion": MAPPING_VERSION,
        "attestedFrom": dict(ATTESTED_FROM),
        "sources": [SOURCE_MARKET_TYPE, SOURCE_LEAGUE],
        "marketTypePrefixes": [p for p, _s, _n in MARKET_TYPE_PREFIXES],
        "typePrefixesNamingNoSport": dict(TYPE_PREFIXES_NAMING_NO_SPORT),
        "nonSportCategories": dict(NON_SPORT_CATEGORIES),
        "whyANonSportIsNotUnknown": WHY_A_NON_SPORT_IS_NOT_UNKNOWN,
        "whyLongestMatch": WHY_LONGEST_MATCH,
        "attestationMinRows": ATTESTATION_MIN_ROWS,
        "whyAThreshold": WHY_A_THRESHOLD,
        "leaguesAdmitted": len(LEAGUE_TO_SPORT),
        "leaguesLowEvidence": dict(LOW_EVIDENCE_LEAGUES),
        "leaguesWithNoVenueStatedSport": dict(
            LEAGUES_WITH_NO_VENUE_STATED_SPORT),
        "whyNotInferred": WHY_NOT_INFERRED,
        "preferredFutureSource": dict(PREFERRED_FUTURE_SOURCE),
        "noFuzzyMatching": NO_FUZZY_MATCHING,
        "unresolvedReasons": list(UNRESOLVED_REASONS),
        "notReused": (
            "copy_sports.sport_of defaults every unmapped league to "
            "'soccer'. That is correct for the mirror lane it serves "
            "and is exactly the silent inference this module refuses"),
    }
