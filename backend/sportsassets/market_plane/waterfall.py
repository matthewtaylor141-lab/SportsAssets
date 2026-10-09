"""THE COVERAGE WATERFALL OVER A DECLARED TARGET UNIVERSE (RC6, lane D2).

MARKET DATA / RESEARCH ONLY. Nothing here admits, prices, sizes or orders
anything, and nothing here changes a contract's coverage state: every count
is read off the state `market_plane.coverage.terminal` already assigned, from
the same evidence (`populate.classify`).

WHY. The scorecard's coverage unit reads PRICEABLE over the ENTIRE active
registry (pm-acceptance 37836393458: 87 / 147,929), and nothing in the
packet said which of those contracts are the ones BETTOR is meant to price,
where along the chain each of them stops, or why. research-sql run
37871119335 (research/rc6_coverage_waterfall.sql) answered it once by hand;
this module answers it on every coverage pass, so the readback carries it.

THE TARGET UNIVERSE, AND WHERE IT COMES FROM. The only written statement of
the market scope in the repository is research/codex_continuous_coverage_
readiness.md, "Required outcome": "Derek should continuously evaluate the
complete available, entitled venue catalogue, pregame and in-play,
including moneyline, totals, spreads, alternate spreads and team totals."
It names families, no sport, league or horizon. So:

  TARGET_A   full-game MONEYLINE / SPREAD (every listed line: main and
             alternate) / TOTAL (the game total) / TEAM_TOTAL, every sport
             and league the venue lists, pregame and in play
  TARGET_B   the same four families on a PERIOD or segment (halves,
             quarters, periods, innings, first five, sets, maps, games of a
             series, regulation time) -- the statement neither names nor
             excludes them; they are reported apart so the owner can decide
  EXCLUDED   by NAMED class only: PLAYER_PROP, OTHER_PROP (exact score,
             first scorer, team statistic totals, corners, both teams to
             score, method of victory, ...), OUTRIGHT (futures), NON_SPORTS
             (a venue code NON_SPORTS_LEAGUES names on a row whose market
             type names no sport: ontology.excluded_as_non_sports), and the
             Kalshi series
             classes a series rule names (AWARD, POLL_RANKING, OFF_FIELD,
             MATCHUP_CONFIRMATION, MVE_PARLAY besides the above)
  UNCLASSIFIED  a contract whose venue states no market type (PMUS), or a
             Kalshi series no rule recognises: neither in nor out, counted on
             its own line, the Kalshi series by name

Nothing is excluded for failing a later stage, and no stage is skipped:
within a tier, catalogue = lost_external + lost_mapping + lost_settlement +
lost_fair_value + lost_fresh_book + priceable, checked on every pass
(`sums_exact`). The tier itself is the owner's to adopt (an evaluator
change); until then the scorecard's whole-registry unit is untouched.

KALSHI. A Kalshi row's family is read here from its SERIES -- the ticker
and the venue's series title, by the ordered rules of _K_RULES -- for the
tier only (basis KALSHI_SERIES_TICKER_AND_TITLE, named in the output, with
the rule that matched). A series no rule recognises is UNCLASSIFIED, never
EXCLUDED. The Kalshi mapping itself (sport, family, settlement) is the Kalshi
lane's; this never maps a Kalshi contract.
"""
from __future__ import annotations

import re

VERSION = "MARKET_PLANE_COVERAGE_WATERFALL_V1"

TARGET_A, TARGET_B = "TARGET_A", "TARGET_B"
EXCLUDED, UNCLASSIFIED = "EXCLUDED", "UNCLASSIFIED"
TIERS = (TARGET_A, TARGET_B, EXCLUDED, UNCLASSIFIED)

MONEYLINE, SPREAD, TOTAL, TEAM_TOTAL = ("MONEYLINE", "SPREAD", "TOTAL",
                                        "TEAM_TOTAL")
TARGET_FAMILIES = (MONEYLINE, SPREAD, TOTAL, TEAM_TOTAL)
X_PLAYER_PROP = "PLAYER_PROP"
X_OTHER_PROP = "OTHER_PROP"
X_OUTRIGHT = "OUTRIGHT"
X_NON_SPORTS = "NON_SPORTS"
#: (Kalshi) classes the venue's PMUS market types fold into `futures`, named
#: apart here because a Kalshi series names them apart
X_AWARD = "AWARD"
X_POLL_RANKING = "POLL_RANKING"
X_OFF_FIELD = "OFF_FIELD"
X_MATCHUP = "MATCHUP_CONFIRMATION"
X_MVE = "MVE_PARLAY"
NO_MARKET_TYPE = "NO_MARKET_TYPE"
#: a Kalshi series no rule below recognises: UNCLASSIFIED, never EXCLUDED
K_NOT_RECOGNISED = "KALSHI_SERIES_NOT_RECOGNISED"
FULL, PERIOD = "FULL", "PERIOD"

BASIS_PMUS = "VENUE_SPORTS_MARKET_TYPE"
BASIS_KALSHI = "KALSHI_SERIES_TICKER_AND_TITLE"

LOST_EXTERNAL = "LOST_EXTERNAL"
LOST_MAPPING = "LOST_MAPPING"
LOST_SETTLEMENT = "LOST_SETTLEMENT"
LOST_FAIR_VALUE = "LOST_FAIR_VALUE"
LOST_FRESH_BOOK = "LOST_FRESH_BOOK"
PRICEABLE = "PRICEABLE"
STAGES = (LOST_EXTERNAL, LOST_MAPPING, LOST_SETTLEMENT, LOST_FAIR_VALUE,
          LOST_FRESH_BOOK, PRICEABLE)
#: the cumulative stages a contract REACHES, in order (a contract lost at a
#: stage reached every stage before it)
REACHED = ("CATALOGUE", "MAPPED", "SETTLEMENT_SUPPORTED",
           "FAIR_VALUE_SUPPORTED", "FRESH_PRICEABLE")

SOURCE = {
    "file": "research/codex_continuous_coverage_readiness.md",
    "section": "Required outcome",
    "quote": ("Derek should continuously evaluate the complete available, "
              "entitled venue catalogue, pregame and in-play, including "
              "moneyline, totals, spreads, alternate spreads and team "
              "totals."),
    "status": ("PROPOSED: the only written market-scope statement found; "
               "no owner-approved sport / league / horizon scope exists in "
               "the repository, so none is applied"),
}

#: bounds on the keyed breakdowns (the snapshot is read whole)
MAX_SPORT_FAMILY_KEYS = 60
MAX_REASON_KEYS = 40
REASON_CHARS = 110

_ML = re.compile(r"(_winner(_[0-9]+)?$|^moneyline$)")
_SPREAD = re.compile(r"(_spread|_handicap(_[0-9]+)?)$")
_TOTAL = re.compile(r"(_total|_total_games|_total_sets|_total_maps|"
                    r"_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$")
#: a team total's own slug token (`-tt-<team>-16pt5`, `-tt1h-<team>-...`)
_TT_SLUG = re.compile(r"-tt(1h|2h|1q|2q|3q|4q)?-[a-z0-9]+-[0-9]+pt[0-9]+$")
_PMUS_PERIOD = re.compile(
    r"(first|second|third|fourth)_(half|quarter|period)|_regulation_|"
    r"_set_[0-9]|inning[0-9]|first_five|_map_|_game_winner_[0-9]|"
    r"_game_total_kills|_rounds_handicap_[0-9]")
#: a Kalshi period marker before a family suffix (1st half .. 4th quarter,
#: hockey periods, first five innings, a single inning)
_KP = r"(?:1H|2H|1Q|2Q|3Q|4Q|1P|2P|3P|F5|INNING)"

#: THE KALSHI SERIES RULES (RC6, lane D2, review finding 1). A Kalshi row's
#: tier is read off its SERIES: the ticker without its `KX` prefix and the
#: series title (registry ontology.series.title, the venue's own words),
#: first matching rule wins. The ticker and title are the only venue facts
#: on a registry row that name the market; the Kalshi MAPPING (sport,
#: family, settlement) is the Kalshi lane's and nothing here maps a
#: contract or changes its state.
#:
#: Before, every series whose ticker did not end in GAME / SPREAD / TOTAL /
#: TEAMTOTAL was EXCLUDED as "not a game family": match, fight, doubles,
#: period, map and set winners among them (research-sql 37869672219 K4/K6:
#: KXATPMATCH "If Adolfo Daniel Vallejo wins the Paul vs Vallejo
#: professional tennis match", KXUFCFIGHT "wins the Pereira vs Zheleznyakova
#: professional MMA fight", KXNCAAF1H "College Football 1st Half Winner",
#: KXCS2MAP "Counter-Strike 2 Map Winner", KXATPSETWINNER "ATP Set Winner").
#: Now a series is EXCLUDED only when a rule positively names its class;
#: every other series is UNCLASSIFIED (K_NOT_RECOGNISED), counted on its own
#: line with its ticker (`kalshi_unrecognised`). Each rule was read against
#: the active catalogue's 1,351 series and one rules_primary text of each
#: (research-sql 37869672219-family corpus, 2026-10-09; the test fixture
#: tests/fixtures/kalshi_series_2026_10_09.json carries them): no series a
#: rule EXCLUDES has a rules text that settles on one game's, match's,
#: fight's, period's, map's or set's winner.
#:
#: (rule name, family, segment, ticker regex, title regex or None)
_K_RULES = (
    # ── the four target families, by the ticker's own family suffix ────
    ("PERIOD_TEAM_TOTAL", TEAM_TOTAL, PERIOD, r"%sTEAMTOTAL$" % _KP, None),
    ("TEAM_TOTAL", TEAM_TOTAL, FULL, r"TEAMTOTAL$", None),
    # a playoff series (several games): its winner, games, spread or exact
    # score -- before SPREAD / TOTAL so its spread is not a game's
    ("PLAYOFF_SERIES", X_OUTRIGHT, FULL,
     r"^(MLB|NBA|WNBA|NHL|NFL)SERIES(SPREAD|GAMES|SCORE)?$", None),
    ("PERIOD_SPREAD", SPREAD, PERIOD, r"%sSPREAD$" % _KP, None),
    ("SPREAD", SPREAD, FULL, r"SPREAD$", None),
    # fantasy points are a player's (KXNFLFFSEASONTOTAL "Pro Football
    # Player's Fantasy Season Total") -- before TOTAL
    ("FANTASY", X_PLAYER_PROP, FULL, r"^(NFLFF|RANKLISTFF)", None),
    ("PERIOD_TOTAL", TOTAL, PERIOD, r"%sTOTAL$" % _KP, None),
    ("TOTAL", TOTAL, FULL, r"TOTAL(MAPS|SETS)?$", None),
    # "...fight ... ends before round 2": the fight's total rounds
    ("FIGHT_TOTAL_ROUNDS", TOTAL, FULL, r"ROUNDS$", r"(?i)total rounds"),
    # "If either team scores a run in the first inning": the first
    # inning's total over 0.5
    ("FIRST_INNING_RUN", TOTAL, PERIOD, r"RFI$", r"(?i)first inning"),
    # a half / quarter winner (KXNCAAF1H, KXNFL2H "wins the 2nd Half
    # (including overtime)", KXWNBA1QWINNER); never a head-to-head (H2H)
    ("PERIOD_WINNER", MONEYLINE, PERIOD,
     r"(?<!H)(1H|2H|1Q|2Q|3Q|4Q)(WINNER)?$", r"(?i)\b(half|quarter)\b"),
    ("HOCKEY_PERIOD_WINNER", MONEYLINE, PERIOD, r"(1P|2P|3P)$",
     r"(?i)\bperiod\b"),
    ("FIRST_INNINGS_WINNER", MONEYLINE, PERIOD, r"F[3-7]$",
     r"(?i)\binnings\b"),
    ("INNING_WINNER", MONEYLINE, PERIOD, r"INNINGWIN$", None),
    ("MAP_WINNER", MONEYLINE, PERIOD, r"MAP$", r"(?i)\bmap winner\b"),
    ("SET_WINNER", MONEYLINE, PERIOD, r"SETWINNER$", None),
    # ── names that end like a game but are not one ────────────────────
    # "If Air Force is selected to play in a bowl game ... season"
    ("BOWL_SELECTION", X_OUTRIGHT, FULL, r"BOWLGAME$", None),
    ("EXACT_MATCH_SCORE", X_OTHER_PROP, FULL, r"EXACTMATCH$", None),
    # "If a boxing match between Mike Tyson and Floyd Mayweather occurs
    # before Jan 1, 2027"
    ("FIGHT_OCCURRENCE", X_OFF_FIELD, FULL, r"^FLOYDTYSONFIGHT$", None),
    # ── a game's, match's or fight's winner ───────────────────────────
    ("GAME_WINNER", MONEYLINE, FULL, r"(GAME|MATCH|FIGHT|DOUBLES)$", None),
    # KXBOXING "Boxing Match Champion": "If Thomas Chabot wins the Thomas
    # Chabot vs Jose Antonio Sanchez Romero boxing match"
    ("BOXING_MATCH", MONEYLINE, FULL, r"^BOXING$", None),
    # KXNFLTIE "If neither team wins and the game ends in a tie": the
    # game result's third outcome
    ("GAME_TIE", MONEYLINE, FULL, r"TIE$", r"(?i)\bgame to tie\b"),
    # ── EXCLUDED, each class positively named ─────────────────────────
    ("MVE_PARLAY", X_MVE, FULL, r"^MVE", None),
    # "...is confirmed to be the matchup in the AFC Championship"
    ("MATCHUP_CONFIRMATION", X_MATCHUP, FULL, r"(MATCHUP$|^TEAMSIN)", None),
    ("CORRECT_SCORE", X_OTHER_PROP, FULL, r"SCORE$",
     r"(?i)\b(correct|exact) score\b"),
    # one game's other outcomes: both teams to score, first to score,
    # overtime / extra innings, corners, a team's statistic, winning-margin
    # bands, method / round of victory, half-time / full-time, race to
    # N points, a safety, a weather delay
    ("GAME_PROP", X_OTHER_PROP, FULL,
     r"(BTTS|FTTS|FIRSTTDTEAM|EXTRAS|CORNERS|WINMARGIN|MOV|MOF|VICROUND|"
     r"DISTANCE|F10G|SFTY|DSTTD|DELAY)$|^(NFL|NCAAF)(BOTH|RACE|FG|TEAMSACK|"
     r"TEAMYDS|TEAMRECYDS|TEAMRECTD|1HFT)$|^(NFL|NCAAF|NHL|WNBA|NBA)OT$",
     None),
    # a named player's statistic: in a game, a week, a season or a career;
    # statistic leaders
    ("PLAYER_STAT", X_PLAYER_PROP, FULL,
     r"^(NFL|NCAAF|NHL|NBA|WNBA|MLB)(RECYDS|REC|RSHYDS|RSHATT|TD|TDPROT|"
     r"PASSYDS|PASSTDS|PASSATT|PASSCOMP|PASSINT|LONGREC|LONGRSH|RRYDS|GOAL|"
     r"FIRSTGOAL|AST|PTS|HIT|HR|HRR|RBI|TB|SB|KS|HA|ERA|WA|OUTS|SAVE|"
     r"FIRSTTD|TEAMFIRSTTD|RECYDSH2H|MOSTRECYDS|MOSTRSHYDS|STATAVG|TSPEC|"
     r"GAMESPECIALS|PLAYERHIGHSCORE|STAT|STATCOUNT|FASTPITCH)$|"
     r"^NFL(ESCALATOR|LADDER|CAREER|WEEKMOST|SEASON(REC|RECYDS|RECTD|RSHTD|"
     r"RSHYDS|PASSYDS|PASSTDS)$)|^NHL(SEASONPPTS|SEASONGOALS|PSEASONGOALS)$|"
     r"LEADER(PLAYOFF)?$|^LEADER|SEASONSTAT$|ROUNDSCORE$|CAREERGOALS$|"
     r"^SHAI20PTREC$", None),
    # personnel, franchise, venue choice, media, participation and
    # sanctions -- before AWARD ("Fighters to compete in MVP event" is a
    # participation market)
    ("OFF_FIELD", X_OFF_FIELD, FULL,
     r"(^NEXT|NEXT(TEAM|COACH|MANAGER|COMMISH|GOVERNOR)|COACHOUT|COACHLEAVE|"
     r"MANAGERSOUT|MANAGEROUTDATE|POCHETTINOOUT|MANCITY(TITLES|POINTS|"
     r"PREMTITLE)|(?<!PLAYOFF)HOST$|(TRADE|RETIRE|JOINCLUB|JOINCONF|"
     r"CONFLEAVE|LEAVE|DEBUT|FIRSTSTART|DRAFT|DRAFTPICK|DRAFTTOP|DRAFT1ST|"
     r"RELOCATION|RELOCATIONCHI|EXPAND|EXPANSION|FRANCHISECOST|"
     r"FRANCHISEOWNER|TEAMSALE|STAKESALE|STADIUM|GAMEDAY|GAMEDAYGUEST|"
     r"STARTERS|SELLOUT|WHATTEND|WHITEHOUSE|PLAYERDEAL|STEPHDEAL|COMPETE|"
     r"OCCUR|ROLE|SFPRACFIELD|RULE|TOJOINCBA|BBALLTEAMUSA|JOINRONALDO|"
     r"LAMINEYAMAL|CLUBCHANGEMBAPPE|SONICS|SEATTLE|NBATEAM|CHINA|HKANE|"
     r"DONATEMRBEAST|HONEYDEUCE|CAPTAIN|RETURN|SORONDO|COURSERECORDS|"
     r"SPORTSOWNERLBJ|OPENINGDAY|SEASONGAMES|WCTEAMS)$)",
     r"(?i)\bnext (team|coach|manager|club|governor|commissioner)\b|"
     r"coach(es)? (out|fired)|\bretire|\btraded?\b|\bjoin (club|"
     r"conference)\b|\bdebut\b|\bdraft\b|relocation|expan(d|sion)|"
     r"franchise|team sale|ownership|stadium|starting lineup|sellouts?|"
     r"white house|stripped|deducted|occurrence"),
    ("AWARD", X_AWARD, FULL,
     r"(AWARD|AWARDFIN|MVP|ALLPRO|ALLTEAM|ALLSTARS|ALLDEFENSE|ALLROOKIE|"
     r"HALLOFFAME|LINDSAY|HEISMAN(FINALIST)?|BALLONDOR(RANK|AWARD)?)$",
     r"(?i)\baward|\bmvps?\b|trophy|of the (year|month)\b|all-pro|all-nba|"
     r"all-stars?\b(?! break)|all-defensive|all-rookie|all-tournament|"
     r"hall of fame|heisman|ballon d.or|cy young|gold glove|"
     r"silver slugger|hank aaron|"
     r"sixth man|most improved|clutch player|hustle|sportsmanship|"
     r"teammate of|social justice|\b(1st|2nd|3rd) team\b"),
    ("POLL_RANKING", X_POLL_RANKING, FULL,
     r"(APRANK|APANY|POLL|APCHURN|RANK)$",
     r"(?i)\bpoll\b|\bap rank|\branked\b|\branking|kenpom|\bratings?\b"),
    # a competition's, season's or several games' outcome
    ("COMPETITION_OR_SEASON", X_OUTRIGHT, FULL,
     r"(WINS|WINSWEEK|SEED|1SEED|PLAYOFF|PLAYIN|ROUNDQUAL|QUAL|QUALIFIERS|"
     r"ROUND|TOP|TOP\d+|TOPX|REGTOP|TOP8|BOTTOM|LAST|RELEGATION|PROMO|"
     r"DIVISIONORDER|STAGEOFELIM|RECORD|RECORDWORST|STREAK|WSTREAK|"
     r"WINSTREAK|TEAMWINSTREAK|POSTASREC|UNDEFEATED|UNDEFEATEDHOME|"
     r"DIVUNDEFEATED|TEAMPTS|TEAMDPTS|TEAMPOINTS|SEASONPTS|H2HWINS|"
     r"H2HFINISH|POINTMARGIN|HIGHSCORE|WEEKHIGHSCORE|TIES|BLOWOUT|MOSTOT|"
     r"LONGESTPLAY|LONGESTFG|60YARDFGS|WALKOFFS|PLAYOFFHRS|PRIMETIME|"
     r"UNRANKEDUPSET|MOSTWINS|DIVMOSTWINS|DIVLEASTWINS|LASTTOWIN|"
     r"LASTTOLOSE|ENDSTREAK|SZNRECORD|CHAMP|"
     r"CHAMPS|TITLE|REGION|CONF|CFPCONF|FINALIST|QF|SF|TOPSEED|FINAL|"
     r"PLAYOFFHOST|GROUP|GROUPWIN|SEEDWIN|TREBLE|NEWCHAMPION|CONFSTREAK|"
     r"WORSTTOFIRST|TFUT|FUTURE|MAJOR|GRANDSLAM|TOURNWIN|NOVAK25|"
     r"SCOTTIESLAM|SINNERFINISH|WORLDRECORD|HOLEINONE|MAKECUT|R\dLEAD|"
     r"R\dTOP10|TOPTEAM|PODIUM|POLE|FASTLAP|BIGGESTMOVER|"
     r"TOPCONSTRUCTOR|CONSTRUCTORS|TEAMS|LSUTEX2X|BOTHQUAL|TEAMSQUAL|"
     r"LEAGUE|EXACTORDER)$",
     r"(?i)\bchampions?\b|\bchampionship\b|\bwinner\b|\btournament\b|"
     r"\bcup\b|qualif|\bseeds?\b|\bplayoffs?\b|\bfinishers?\b|\btop \d|"
     r"relegation|last place|\bleague phase\b|\bdivision\b.*\b(winner|"
     r"order)\b|world series|super bowl|stanley cup|win totals?|\bwins\b|"
     r"\brecord\b|\bstreak\b|undefeated|\bpoints\b|\bhighest\b|\blowest\b|"
     r"\bmost\b|\bleast\b|\bround\b|\bbowl\b|elimination|"
     r"exact order|\brace\b|grand slam|\bmajor\b|marathon|\btitle\b|"
     r"\bseries\b|\bfinals?\b|trophies|\btour\b|competition|primetime|"
     r"\bties\b|upsets|blowout|longest|winning margin|\bto win\b"),
)
_K_COMPILED = tuple((n, f, s, re.compile(rx), re.compile(trx) if trx
                     else None) for n, f, s, rx, trx in _K_RULES)


def kalshi_series_class(series: str | None, title: str | None = None
                        ) -> tuple:
    """PURE. (family, segment, rule) for one Kalshi series: its ticker
    (with or without `KX`) and its title. The first rule whose ticker regex
    matches -- and whose title regex, when it has one, matches too -- wins;
    the EXCLUDED classes also match on the title alone. None of them:
    (K_NOT_RECOGNISED, FULL, None)."""
    b = str(series or "").strip().upper()
    b = b[2:] if b.startswith("KX") else b
    t = str(title or "").strip()
    for name, fam, seg, rx, trx in _K_COMPILED:
        if fam in TARGET_FAMILIES or name in _K_TICKER_ONLY:
            if rx.search(b) and (trx is None or trx.search(t)):
                return fam, seg, name
        elif rx.search(b) or (trx is not None and trx.search(t)):
            return fam, seg, name
    return K_NOT_RECOGNISED, FULL, None


#: exclusion rules read off the ticker only (their ticker is exact; a title
#: word alone would over-reach)
_K_TICKER_ONLY = frozenset({"PLAYOFF_SERIES", "FANTASY", "BOWL_SELECTION",
                            "EXACT_MATCH_SCORE", "FIGHT_OCCURRENCE",
                            "MVE_PARLAY", "CORRECT_SCORE"})


def _series_title(c: dict) -> str | None:
    ont = c.get("ontology")
    if isinstance(ont, str):
        try:
            import json
            ont = json.loads(ont)
        except ValueError:
            ont = None
    ser = (ont or {}).get("series") if isinstance(ont, dict) else None
    return (ser or {}).get("title") if isinstance(ser, dict) else None


def classify(contract: dict) -> dict:
    """PURE. {tier, family, segment, basis[, rule]} for one registry
    contract."""
    c = dict(contract or {})
    venue = str(c.get("venue") or "")
    rule = None
    if venue == "KALSHI":
        fam, seg, rule = kalshi_series_class(c.get("competition"),
                                             _series_title(c))
        basis = BASIS_KALSHI
    else:
        from .ontology import excluded_as_non_sports
        from .populate import league_of
        mt = str(c.get("market_type") or "").strip().lower()
        lg = league_of(c.get("event_id"), c.get("competition"))
        basis = BASIS_PMUS
        seg = PERIOD if (mt and _PMUS_PERIOD.search(mt)) else FULL
        if excluded_as_non_sports(lg, mt):
            fam = X_NON_SPORTS
        elif not mt:
            fam = NO_MARKET_TYPE
        elif mt == "futures":
            fam = X_OUTRIGHT
        elif re.search(r"(^|_)player_", mt):
            fam = X_PLAYER_PROP
        elif _ML.search(mt):
            fam = MONEYLINE
        elif _SPREAD.search(mt):
            fam = SPREAD
        elif _TOTAL.search(mt):
            fam = (TEAM_TOTAL if _TT_SLUG.search(str(c.get("contract_id")
                                                     or "").lower())
                   else TOTAL)
        else:
            fam = X_OTHER_PROP
    if fam in TARGET_FAMILIES:
        tier = TARGET_A if seg == FULL else TARGET_B
    elif fam in (NO_MARKET_TYPE, K_NOT_RECOGNISED):
        tier = UNCLASSIFIED
    else:
        tier = EXCLUDED
    out = {"tier": tier, "family": fam, "segment": seg, "basis": basis}
    if venue == "KALSHI":
        out["rule"] = rule
    return out


#: NON_SPORTS_EXCLUDED_FROM_REGISTRY: what a full populate pass kept out
OUTSIDE_LINE = "NON_SPORTS_EXCLUDED_FROM_REGISTRY"
OUTSIDE_NOT_RECORDED = "NO_FULL_POPULATE_PASS_RECORDED"


def _outside(rec: dict | None) -> dict:
    """PURE. The waterfall's line for the markets kept out of the registry
    (never summed into the tiers)."""
    if not rec:
        return {"line": OUTSIDE_LINE, "status": OUTSIDE_NOT_RECORDED,
                "in_sums": False}
    return {"line": OUTSIDE_LINE, "status": "RECORDED", "in_sums": False,
            "full_pass_at": rec.get("at"), "rule": rec.get("rule"),
            "listed_active_by_code": rec.get("excluded_listed_active"),
            "listed_active_total": rec.get("excluded_listed_active_total"),
            "catalogue_by_code": rec.get("excluded"),
            "catalogue_total": rec.get("excluded_total")}


#: coverage.terminal's states, one stage each -- except CODE_CONTROLLED_GAP,
#: which terminal() assigns at TWO points of its order (not mapped; mapped
#: and valued but no fresh canonical book), told apart by the same evidence
_STATE_STAGE = {"PRICEABLE": PRICEABLE,
                "EXTERNAL_DATA_UNAVAILABLE": LOST_EXTERNAL,
                "MAPPED_BUT_SETTLEMENT_NOT_PROVEN": LOST_SETTLEMENT,
                "MAPPED_BUT_NO_FAIR_VALUE_SOURCE": LOST_FAIR_VALUE}
_BOOK_WHYS = ("NO_CURRENT_CANONICAL_BOOK", "NO_FRESH_CANONICAL_BOOK")


def stage_of(state: str | None, why: str | None = None,
             evidence: dict | None = None) -> str:
    """The stage at which the contract stopped: its terminal state, and for
    CODE_CONTROLLED_GAP the same evidence terminal() read (mapped or not),
    else its own why. An unknown state is a mapping loss, never a pass."""
    if state in _STATE_STAGE:
        return _STATE_STAGE[state]
    e = evidence or {}
    if "mapped" in e:
        return LOST_FRESH_BOOK if e.get("mapped") else LOST_MAPPING
    return (LOST_FRESH_BOOK if str(why or "").startswith(_BOOK_WHYS)
            else LOST_MAPPING)


#: THE DECISION HORIZON, READ (collector_coverage.HORIZON_AHEAD_S /
#: _BEHIND_S: -6 h .. +24 h), never set here. A contract outside it cannot
#: carry a decision valuation yet BY DESIGN -- the collector values only
#: competitions with an event inside it -- so it is REPORTED apart
#: (in_decision_horizon), never removed from any denominator. research-sql
#: run 37873014699 M3: every major-league money line starting 24-48 h out
#: and never valued was NOT_A_COLLECTOR_CANDIDATE_IN_24H.
def _horizon() -> tuple:
    from .. import collector_coverage as CC
    return float(CC.HORIZON_BEHIND_S), float(CC.HORIZON_AHEAD_S)


def _blank() -> dict:
    return {"catalogue": 0, **{s: 0 for s in STAGES}, "valued_24h": 0,
            "within_48h": 0, "within_48h_priceable": 0,
            "in_decision_horizon": 0, "in_decision_horizon_priceable": 0}


class Waterfall:
    """Counters only: one contract in, nothing kept but the counts."""

    def __init__(self, *, now: float):
        self.now = float(now)
        self.behind_s, self.ahead_s = _horizon()
        self.tiers = {t: _blank() for t in TIERS}
        self.venue_tier: dict = {}
        self.fam_seg: dict = {}
        self.sport_fam: dict = {}
        self.excluded: dict = {}
        self.reasons: dict = {}
        self.k_rules: dict = {}
        self.k_unrecognised: dict = {}

    def add(self, contract: dict, t: dict, *, valued: bool = False) -> dict:
        k = classify(contract)
        stage = stage_of((t or {}).get("state"), (t or {}).get("why"),
                         (t or {}).get("evidence"))
        st = contract.get("event_start")
        try:
            st = float(st.timestamp()) if hasattr(st, "timestamp") else (
                None if st is None else float(st))
        except (TypeError, ValueError):
            st = None
        w48 = st is not None and -6 * 3600.0 <= st - self.now <= 48 * 3600.0
        hz = st is not None and -self.behind_s <= st - self.now <= self.ahead_s
        venue = str(contract.get("venue") or "UNKNOWN")
        books = [(k["tier"], self.tiers),
                 ("%s|%s" % (venue, k["tier"]), self.venue_tier),
                 ("%s|%s|%s" % (k["tier"], k["family"], k["segment"]),
                  self.fam_seg)]
        if k["tier"] in (TARGET_A, TARGET_B):
            books.append(("%s|%s|%s|%s" % (
                k["tier"], venue, contract.get("sport") or "UNKNOWN",
                k["family"]), self.sport_fam))
        for key, book in books:
            row = book.setdefault(key, _blank())
            row["catalogue"] += 1
            row[stage] += 1
            row["valued_24h"] += int(bool(valued))
            row["within_48h"] += int(w48)
            row["within_48h_priceable"] += int(w48 and stage == PRICEABLE)
            row["in_decision_horizon"] += int(hz)
            row["in_decision_horizon_priceable"] += int(
                hz and stage == PRICEABLE)
        if k["tier"] in (TARGET_A, TARGET_B):
            if stage != PRICEABLE:
                rk = "%s|%s|%s" % (k["tier"], stage,
                                   str((t or {}).get("why") or "")
                                   [:REASON_CHARS])
                self.reasons[rk] = self.reasons.get(rk, 0) + 1
        elif k["tier"] == EXCLUDED:
            xk = "%s|%s|%s" % (venue, k["family"], k["segment"])
            self.excluded[xk] = self.excluded.get(xk, 0) + 1
        if "rule" in k:
            rn = k["rule"] or K_NOT_RECOGNISED
            self.k_rules[rn] = self.k_rules.get(rn, 0) + 1
            if k["family"] == K_NOT_RECOGNISED:
                sk = str(contract.get("competition") or "")[:40]
                self.k_unrecognised[sk] = self.k_unrecognised.get(sk, 0) + 1
        return dict(k, stage=stage)

    @staticmethod
    def _reached(row: dict) -> dict:
        n = row["catalogue"]
        mapped = n - row[LOST_EXTERNAL] - row[LOST_MAPPING]
        settled = mapped - row[LOST_SETTLEMENT]
        valued = settled - row[LOST_FAIR_VALUE]
        return dict(zip(REACHED, (n, mapped, settled, valued,
                                  valued - row[LOST_FRESH_BOOK])))

    def result(self, *, outside_registry: dict | None = None) -> dict:
        """The counters. `outside_registry` is the plane's last FULL
        populate pass's record (populate.full_pass_record): the catalogue
        markets kept out of the registry by name. They are in no tier and no
        sum here -- the registry is the catalogue this waterfall counts --
        and they are shown beside it so the exclusion stays visible; absent
        (no full pass recorded yet) it says so."""
        tiers = {}
        exact = True
        for t, row in self.tiers.items():
            ok = row["catalogue"] == sum(row[s] for s in STAGES)
            exact = exact and ok
            tiers[t] = dict(row, reached=self._reached(row),
                            rate=(round(row[PRICEABLE] / row["catalogue"], 6)
                                  if row["catalogue"] else None))
        total = sum(r["catalogue"] for r in self.tiers.values())
        top_sf = sorted(self.sport_fam.items(),
                        key=lambda kv: -kv[1]["catalogue"])
        top_r = sorted(self.reasons.items(), key=lambda kv: -kv[1])
        top_k = sorted(self.k_unrecognised.items(), key=lambda kv: -kv[1])
        return {"version": VERSION, "computed_at": self.now,
                "decision_horizon_s": {"behind": self.behind_s,
                                       "ahead": self.ahead_s,
                                       "source": "collector_coverage."
                                                 "HORIZON_BEHIND_S / "
                                                 "HORIZON_AHEAD_S (read)"},
                "universe": {"source": dict(SOURCE),
                             "tiers": {TARGET_A: "full-game moneyline, "
                                       "spread (every line), game total, "
                                       "team total; every sport and league",
                                       TARGET_B: "the same families on a "
                                       "period or segment",
                                       EXCLUDED: "named classes only",
                                       UNCLASSIFIED: "the venue states no "
                                       "market type (PMUS); no series rule "
                                       "recognises the series (Kalshi)"},
                             "basis": {"POLYMARKET_US": BASIS_PMUS,
                                       "KALSHI": BASIS_KALSHI}},
                "catalogue": total,
                "stages": list(STAGES), "reached_order": list(REACHED),
                "tiers": tiers,
                "by_venue_tier": dict(sorted(self.venue_tier.items())),
                "by_tier_family_segment": dict(sorted(self.fam_seg.items())),
                "by_target_sport_family": dict(
                    top_sf[:MAX_SPORT_FAMILY_KEYS]),
                "by_target_sport_family_keys_total": len(top_sf),
                "excluded": dict(sorted(self.excluded.items())),
                "reasons": dict(top_r[:MAX_REASON_KEYS]),
                "reasons_keys_total": len(top_r),
                # (Kalshi) contracts by the series rule that tiered them, and
                # the series no rule recognises, by name (UNCLASSIFIED, never
                # EXCLUDED): the MAX_REASON_KEYS largest, with the count of
                # all of them (30 series on 2026-10-09)
                "by_kalshi_rule": dict(sorted(self.k_rules.items())),
                "kalshi_unrecognised": dict(top_k[:MAX_REASON_KEYS]),
                "kalshi_unrecognised_keys_total": len(top_k),
                "outside_registry": _outside(outside_registry),
                "sums_exact": bool(exact and total == sum(
                    self.tiers[t]["catalogue"] for t in TIERS))}
