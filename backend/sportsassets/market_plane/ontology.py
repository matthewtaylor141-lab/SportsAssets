"""Schema-driven contract ontology.

The parser is deliberately broader than the legacy family allow-list but it
NEVER grants settlement or fair-value authority. Unknown tokens remain named
rather than guessed.
"""
from __future__ import annotations

import re
from .models import ContractMeaning

VERSION = "CONTRACT_ONTOLOGY_V1"

_PERIOD_PATTERNS = (
    (re.compile(r"(?:^|_)(full_game|full_time|match)(?:_|$)"), "FULL_EVENT"),
    (re.compile(r"(?:^|_)(first_half|1st_half|half_1)(?:_|$)"), "FIRST_HALF"),
    (re.compile(r"(?:^|_)(second_half|2nd_half|half_2)(?:_|$)"), "SECOND_HALF"),
    (re.compile(r"(?:^|_)(first_quarter|1st_quarter|q1)(?:_|$)"), "Q1"),
    (re.compile(r"(?:^|_)(second_quarter|2nd_quarter|q2)(?:_|$)"), "Q2"),
    (re.compile(r"(?:^|_)(third_quarter|3rd_quarter|q3)(?:_|$)"), "Q3"),
    (re.compile(r"(?:^|_)(fourth_quarter|4th_quarter|q4)(?:_|$)"), "Q4"),
    (re.compile(r"(?:^|_)(first_period|period_1|p1)(?:_|$)"), "P1"),
    (re.compile(r"(?:^|_)(second_period|period_2|p2)(?:_|$)"), "P2"),
    (re.compile(r"(?:^|_)(third_period|period_3|p3)(?:_|$)"), "P3"),
    (re.compile(r"(?:^|_)(first_inning|inning_1)(?:_|$)"), "INNING_1"),
    (re.compile(r"(?:^|_)(first_set|set_1)(?:_|$)"), "SET_1"),
    # ── (RC6.2, p-coverage) THE SEGMENTS THE VENUE NAMES THAT FELL THROUGH
    # TO FULL_EVENT. Every type below was read off production's active
    # registry (research-sql run 37945671144 F3, 2026-10-09) carrying
    # period FULL_EVENT although its own name states a part of the event:
    # hockey_team_regulation_winner 120 (the 60-minute result: overtime and
    # the shootout are not in it), table_tennis_set_2/3/4_winner 517,
    # tennis_set_2_winner 30, baseball_team_inning1..9_winner / _total 44,
    # baseball_team_first_five_winner / _total / _spread 10,
    # esports_map_winner_1/2 124, esports_map_total_rounds_1/2 91,
    # esports_map_rounds_handicap_1/2 89, esports_game_winner_1..4 51,
    # esports_game_total_kills_1/2 20, esports_game_first_blood_1..4 26,
    # esports_game_kills_odd_even_1..4 8. A FULL_EVENT WINNER is what
    # market_plane.settlement.h2h_family reads as the game's MONEY LINE, so
    # a regulation-time or inning winner was compared against the book's
    # full-game money-line terms (production: 120 regulation winners read
    # INCOMPATIBLE_NOT_PRICEABLE on the money line's postponement clause, 27
    # inning winners BOOK_TERMS_SCOPE on the baseball money line's scope).
    (re.compile(r"(?:^|_)regulation(?:_|$)"), "REGULATION_TIME"),
    (re.compile(r"(?:^|_)first_five(?:_|$)"), "FIRST_FIVE_INNINGS"),
    (re.compile(r"(?:^|_)inning_?([1-9][0-9]?)(?:_|$)"), "INNING_%s"),
    (re.compile(r"(?:^|_)set_([1-9])(?:_|$)"), "SET_%s"),
    (re.compile(r"(?:^|_)map_(?:[a-z]+_)*([1-9])$"), "MAP_%s"),
    (re.compile(r"^esports_game_(?:[a-z]+_)*([1-9])$"), "GAME_%s"),
)

#: (RC6.2, p-coverage) A MEASURE WHOSE NAME CONTAINS ANOTHER MEASURE'S
#: TOKEN. `_METRICS` is matched by substring, first hit wins, so these were
#: read as the shorter measure (production F3, 2026-10-09):
#: football_player_fantasy_points_ppr 134 -> POINTS, football_game_race_to_
#: points 435 -> POINTS, football_game_*_quarter_both_teams_score_points
#: 588 -> POINTS, baseball_player_hits_runs_rbis 60 -> RUNS,
#: baseball_player_home_runs 20 -> RUNS, football_player_most_passing_yards
#: 24 -> PASSING_YARDS. POINTS / RUNS are CORE_METRICS, so each of these
#: props also took the full-event core subscription priority. Matched on
#: whole tokens, before `_METRICS`.
_COMPOUND_METRICS = (
    ("fantasy_points", "FANTASY_POINTS", "TOTAL"),
    ("race_to_points", "RACE_TO_POINTS", "EQ"),
    ("both_teams_score_points", "BOTH_TEAMS_SCORE_POINTS", "YES_NO"),
    ("hits_runs_rbis", "HITS_RUNS_RBIS", "TOTAL"),
    ("home_runs", "HOME_RUNS", "TOTAL"),
    ("most_passing_yards", "MOST_PASSING_YARDS", "EQ"),
)
#: (RC6.2, p-coverage) A TEAM'S STATISTIC TOTAL IS NOT ITS SCORE. The
#: "team_total" token made every football_team_total_<statistic> a
#: TEAM_SCORE (production F3: field_goals_made 882, first_downs 708,
#: pass_touchdowns 708, pass_yards 708, receptions 708, rush_touchdowns
#: 708, touchdowns 624, offensive_yards 552, rush_yards 378, defensive_
#: special_teams_touchdowns 58 -- 6,034 contracts). The statistic the venue
#: names is the metric (subject TEAM, operator TOTAL, metric_source
#: VENUE_LABEL); a team total of the score itself keeps TEAM_SCORE.
_TEAM_STAT_TOTAL = re.compile(r"_team_total_(?P<stat>[a-z][a-z_]*)$")
_TEAM_SCORE_WORDS = frozenset({"points", "goals", "runs", "score"})

_METRICS = (
    ("winner", "WINNER", "EQ"),
    ("moneyline", "WINNER", "EQ"),
    ("spread", "MARGIN", "COVERS"),
    ("total_points", "POINTS", "TOTAL"),
    ("total_goals", "GOALS", "TOTAL"),
    ("total_runs", "RUNS", "TOTAL"),
    ("total_games", "GAMES", "TOTAL"),
    ("team_total", "TEAM_SCORE", "TOTAL"),
    ("points", "POINTS", "TOTAL"),
    ("goals", "GOALS", "TOTAL"),
    ("runs", "RUNS", "TOTAL"),
    ("passing_yards", "PASSING_YARDS", "TOTAL"),
    ("rushing_yards", "RUSHING_YARDS", "TOTAL"),
    ("receiving_yards", "RECEIVING_YARDS", "TOTAL"),
    ("receptions", "RECEPTIONS", "TOTAL"),
    ("strikeouts", "STRIKEOUTS", "TOTAL"),
    ("touchdowns", "TOUCHDOWNS", "TOTAL"),
    ("assists", "ASSISTS", "TOTAL"),
    ("rebounds", "REBOUNDS", "TOTAL"),
    ("three_pointers", "THREE_POINTERS", "TOTAL"),
    ("exact_score", "EXACT_SCORE", "EQ"),
    ("champion", "CHAMPION", "EQ"),
    ("award_winner", "AWARD_WINNER", "EQ"),
    ("season_wins", "SEASON_WINS", "TOTAL"),
)

SPORT_PREFIXES = {
    "americanfootball": "football", "football": "football", "nfl": "football",
    "basketball": "basketball", "nba": "basketball", "wnba": "basketball",
    "baseball": "baseball", "mlb": "baseball",
    "hockey": "hockey", "icehockey": "hockey", "nhl": "hockey",
    "soccer": "soccer", "tennis": "tennis",
    # (integration) the venue's own sportsMarketType heads seen in production
    # (research run 37529879382): every one is a sport the venue lists
    "table_tennis": "table_tennis", "efootball": "efootball",
    "esports": "esports", "ufc": "mma", "mma": "mma", "boxing": "boxing",
    "golf": "golf", "cricket": "cricket", "darts": "darts",
    "volleyball": "volleyball", "handball": "handball", "rugby": "rugby",
    "motorsport": "motorsport", "nascar": "motorsport", "f1": "motorsport",
    # (RC6.2) production F3: pickleball_match_winner (20 active rows)
    "pickleball": "pickleball",
}

#: (integration) a futures/outright market's sportsMarketType carries no sport
#: head ("futures"): its sport is read off the venue's league code (the
#: event slug's second token / the catalogue's team league) -- the venue's
#: own key, never a name match. Unlisted leagues stay unknown (named gap).
LEAGUE_SPORT = {
    "nfl": "football", "cfb": "football", "ufl": "football",
    "nba": "basketball", "wnba": "basketball", "cbb": "basketball",
    # (RC6) NCAA men's / women's SOCCER: every typed venue row of these codes
    # is a soccer market (research-sql run 37871400151 F3: ncaaws 333 /
    # ncaams 132 rows, sport soccer from their own sportsMarketType; the
    # c28 receipt files both tokens under soccer too). They read basketball
    # here, which a futures market of either code would have inherited.
    "ncaams": "soccer", "ncaaws": "soccer", "euroleague": "basketball",
    "mlb": "baseball", "kbo": "baseball", "npb": "baseball",
    "nhl": "hockey", "khl": "hockey", "shl": "hockey",
    "epl": "soccer", "ucl": "soccer", "uel": "soccer", "uecl": "soccer",
    "unl": "soccer", "lal": "soccer", "sea": "soccer", "bun": "soccer",
    "bun2": "soccer", "lg1": "soccer", "mls": "soccer", "eflch": "soccer",
    "efl1": "soccer", "efl2": "soccer", "engnl": "soccer", "u21eq": "soccer",
    "uwwcq": "soccer", "intf": "soccer", "bra": "soccer", "arg2": "soccer",
    "acle": "soccer", "lmx": "soccer", "tff1": "soccer", "uslc": "soccer",
    "j1": "soccer", "j2": "soccer", "par1": "soccer", "par2": "soccer",
    "bel1": "soccer", "ligpor": "soccer", "ekst": "soccer", "idnsl": "soccer",
    "brb": "soccer", "cnl": "soccer", "afcq": "soccer", "lpa": "soccer",
    "sercc": "soccer", "serca": "soccer", "sercb": "soccer",
    "atp": "tennis", "wta": "tennis", "itfwo": "tennis", "itfme": "tennis",
    "utr": "tennis", "atpdb": "tennis",
    "setkameua": "table_tennis", "setkamecz": "table_tennis",
    "setkamemd": "table_tennis", "setkawoua": "table_tennis",
    "czechligapro": "table_tennis", "ttcup": "table_tennis",
    "cs2": "esports", "lol": "esports", "dota2": "esports",
    "valorant": "esports", "r6": "esports",
    "ebattlesefsa": "efootball", "ebattlesefpl": "efootball",
    "ebattlesefwca": "efootball", "ebattlesefwcb": "efootball",
    # (RC6.2, p-coverage) `vkl` / `btla` are SOCCER league codes on the
    # venue: every event listed under them in 45 days is a soccer game
    # (research-sql run 37946677103 H1 / 37945671144 F1: btla 12 events,
    # "Wydad AC vs. CODM Meknes"; vkl 8, "Vaasan Palloseura vs. IF
    # Gnistan"), and no efootball event carries either code. They read
    # efootball here, which an untyped or futures row would have inherited.
    "vkl": "soccer", "btla": "soccer",
    "ufc": "mma", "dwcs": "mma",
    "pga": "golf", "pgatc": "golf", "kft": "golf", "dpwt": "golf",
    "liv": "golf", "nascar": "motorsport", "f1": "motorsport",
    # (RC6.2, p-coverage) the residual venue codes left SPORT_NOT_NORMALIZED
    # after the slug grammar fix, each read with its own event title
    # (research-sql run 37945671144 F5, 2026-10-09): ppa "Who will win in
    # the upcoming pickleball event ..." (20), powerslap "William Shockey
    # vs Ace Samples" (8, type `moneyline`), dfb "DFB-Pokal Champion" (18),
    # uefa "Ballon d'Or 2026 Winner" (12), motogp "MotoGP World Champion"
    # (6), cdb "Copa do Brasil Champion" (4), lib "Copa Libertadores
    # Champion" (4), fide "FIDE World Chess Championship Winner 2026" (2),
    # boxing "Canelo Alvarez vs Christian Mbilli" (1, type `moneyline`),
    # football "Team from Texas wins the Pro or College Football
    # Championship" (1). (`pdc` -- 18 darts futures -- is named in
    # AMBIGUOUS_LEAGUE_CODES instead: the venue lists soccer under it too.)
    "ppa": "pickleball", "powerslap": "power_slap", "dfb": "soccer",
    "uefa": "soccer", "motogp": "motorsport",
    "cdb": "soccer", "lib": "soccer", "fide": "chess", "boxing": "boxing",
    "football": "football",
}
#: venue league codes that are NOT sports (the catalogue lists them beside
#: sports: crypto, entertainment, awards, weather, games): excluded from the
#: sports universe by name, never silently
NON_SPORTS_LEAGUES = {"btc", "eth", "sol", "ntflx", "nobel", "temp", "gtasc",
                      "oscars", "emmys", "grammys", "box", "pol",
                      # (RC6) the venue's macro-economic and central-bank
                      # markets, filed as `futures` beside the sports ones
                      # (research-sql run 37871400151 F4, their own event
                      # ids: uscpi-september-mom-2026-10-14, usfed-hike2-
                      # 2026-12-31, ecb-2026-10-29, boj-2026-10-30, ...).
                      # Unreachable before RC6: the league was read off the
                      # SECOND slug segment, so none of them was ever seen
                      # under its own code.
                      "us", "uscpi", "uscpicore", "usfed", "usgas",
                      "usunemp", "usnfp", "fed", "cut", "hike", "ecb", "boj",
                      "boe", "boc", "boi", "bcb", "cbr"}
#: (RC6) venue codes the venue uses for MORE THAN ONE sport: never mapped
#: from the code alone (a named LEAGUE_CODE_AMBIGUOUS gap instead). `wbc`
#: was read as the World Baseball Classic; every `wbc` market listed today is
#: a World Boxing Council title future (research-sql run 37871400151 F4:
#: 145 rows, wbc-bantamw-2026-12-31-champ .. wbc-welterw-2026-12-31-champ).
#: (RC6.2, p-coverage) `pdc` too: the darts body's futures ("PDC World Darts
#: Championship Winner", 18 active rows) and Chilean soccer games ("Everton
#: de Vina del Mar vs. CD La Serena", 8 events) share it (research-sql run
#: 37946677103 H1: the only venue code listed under two sports in 45 days).
#: A typed soccer row takes its sport from its market type; an untyped one
#: stays LEAGUE_CODE_AMBIGUOUS -- no slug grammar tells the two apart.
AMBIGUOUS_LEAGUE_CODES = {"wbc": ("baseball", "boxing"),
                          "pdc": ("darts", "soccer")}

#: (RC6.2, p-coverage) THE AMBIGUITY RESOLVED BY THE VENUE'S OWN EVENT SLUG,
#: never by the code alone. A boxing sanctioning body's title future names
#: its WEIGHT CLASS as the event slug's second segment -- every one of the
#: 145 active `wbc` rows (research-sql run 37945671144 F6, 2026-10-09:
#: wbc-bantamw / flyw / middlew / cruiserw / welterw / featherw / heavyw /
#: lightw-2026-12-31-champ, titled "WBC <Weight>weight Champion on Dec 31,
#: 2026"), and the UFC's own title futures use the same grammar
#: (ufc-welterw-2026-12-31-champ). A slug whose second segment is not a
#: weight class (a World Baseball Classic game would carry its teams) stays
#: LEAGUE_CODE_AMBIGUOUS.
_WEIGHT_CLASS = re.compile(
    r"^(?:super|light|junior|minimum)?"
    r"(?:straw|minimum|fly|bantam|feather|light|welter|middle|cruiser|heavy)"
    r"w(?:eight)?$")
_DISAMBIGUATE = {"wbc": (("boxing", _WEIGHT_CLASS),)}


def disambiguate_league(code, event_id) -> str | None:
    """PURE. The sport an AMBIGUOUS venue league code means for THIS event,
    read off the event slug's second segment by a declared grammar, or None
    (the ambiguity stays a named gap)."""
    parts = [p for p in str(event_id or "").strip().lower().split("-") if p]
    if len(parts) < 2 or parts[0] != str(code or "").strip().lower():
        return None
    for sport, rx in _DISAMBIGUATE.get(parts[0], ()):
        if rx.match(parts[1]):
            return sport
    return None

#: (integration) THE VENUE'S OWN LABEL WHEN NO TOKEN ABOVE MATCHES. The
#: residual of sportsMarketType after the sport head, the subject word and
#: the period words is the venue's name for what the contract measures
#: (`soccer_game_total_corners` -> TOTAL_CORNERS). It is recorded as the
#: metric with metric_source VENUE_LABEL -- a structural fact, never a guess at
#: settlement -- and the operator from its own suffix.
_OPERATOR_SUFFIX = (
    ("exact_score", "EQ"), ("exact_margin", "EQ"), ("winner", "EQ"),
    ("spread", "COVERS"), ("total", "TOTAL"), ("btts", "YES_NO"),
    ("both_teams_score_points", "YES_NO"), ("overtime", "YES_NO"),
    ("double_result", "EQ"), ("method_of_victory", "EQ"),
    ("first_touchdown", "EQ"), ("first_goal", "EQ"), ("first_score", "EQ"),
    ("first_team_to_score", "EQ"), ("race_to_points", "EQ"),
)
_STRUCTURE_WORDS = {"team", "game", "match", "player", "full", "time",
                    "first", "second", "third", "fourth", "half", "quarter",
                    "period", "inning", "set", "map", "regulation"}


def _line_from(*values):
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return None


def _venue_label_metric(raw: str, sport_head: str | None):
    toks = [t for t in raw.split("_") if t]
    if sport_head:
        n = len(sport_head.split("_"))
        if "_".join(toks[:n]) == sport_head:
            toks = toks[n:]
    body = [t for t in toks if t not in _STRUCTURE_WORDS
            and not re.fullmatch(r"\d+", t)]
    if not body:
        return None, None
    op = "PROPOSITION"
    for suffix, o in _OPERATOR_SUFFIX:
        if raw.endswith(suffix) or ("_" + suffix + "_") in ("_" + raw + "_"):
            op = o
            break
    return "_".join(body).upper(), op


def parse_market_type(*, venue: str, contract_id: str, sports_market_type: str | None,
                      competition: str | None = None, event_id: str | None = None,
                      line=None, side=None, subject_type=None, subject_id=None,
                      metadata: dict | None = None) -> dict:
    raw = str(sports_market_type or "").strip().lower().replace("-", "_")
    md = metadata or {}
    sport = None
    sport_head = None
    head = raw.split("_", 1)[0] if raw else ""
    # longest head first, so "table_tennis" is never read as "tennis"
    for p, canonical in sorted(SPORT_PREFIXES.items(),
                               key=lambda kv: -len(kv[0])):
        if raw == p or raw.startswith(p + "_") or (
                "_" not in p and head.startswith(p)):
            sport, sport_head = canonical, p
            break
    sport_basis = "VENUE_MARKET_TYPE" if sport else None
    ambiguous = False
    if sport is None and competition:
        lg = str(competition).strip().lower()
        if lg in AMBIGUOUS_LEAGUE_CODES:
            sp = disambiguate_league(lg, event_id)
            if sp is None:
                ambiguous = True
            else:
                sport, sport_basis = sp, "VENUE_LEAGUE_CODE_AND_EVENT_SLUG"
        elif lg in LEAGUE_SPORT:
            sport, sport_basis = LEAGUE_SPORT[lg], "VENUE_LEAGUE_CODE"
    period = "FULL_EVENT"
    for rx, value in _PERIOD_PATTERNS:
        pm = rx.search(raw)
        if pm:
            period = value % pm.group(1) if "%s" in value else value
            break
    metric = operator = None
    metric_source = None
    normalized = raw
    padded = "_%s_" % raw
    ts = _TEAM_STAT_TOTAL.search(raw)
    if ts and ts.group("stat") not in _TEAM_SCORE_WORDS:
        metric, operator = ts.group("stat").upper(), "TOTAL"
        metric_source = "VENUE_LABEL"
    if metric is None:
        for token, met, op in _COMPOUND_METRICS:
            if "_%s_" % token in padded:
                metric, operator, metric_source = met, op, "TOKEN"
                break
    if metric is None:
        for token, met, op in _METRICS:
            if token in normalized:
                metric, operator = met, op
                break
        metric_source = "TOKEN" if metric else None
    if metric is None and raw.endswith("_total"):
        metric, operator, metric_source = "TOTAL", "TOTAL", "TOKEN"
    if metric is None and raw == "futures":
        # an outright: what it pays on is the question text (the venue's)
        metric, operator, metric_source = "OUTRIGHT", "EQ", "VENUE_FUTURES"
    if metric is None and raw and sport_head:
        # only under a recognised sport head: an unrecognised type stays a
        # named METRIC_NOT_NORMALIZED gap, never relabelled
        metric, operator = _venue_label_metric(raw, sport_head)
        metric_source = "VENUE_LABEL" if metric else None
    subj_type = subject_type or md.get("subject_type")
    if subj_type is None:
        if any(t in raw for t in ("player_", "pitcher_", "quarterback_")):
            subj_type = "PLAYER"
        elif "team_" in raw:
            subj_type = "TEAM"
        elif metric in ("CHAMPION", "AWARD_WINNER", "OUTRIGHT"):
            subj_type = "OUTRIGHT"
        else:
            subj_type = "EVENT"
    meaning = ContractMeaning(
        venue=venue, venue_contract_id=contract_id, sport=sport,
        competition=competition, event_id=event_id,
        subject_type=subj_type, subject_id=subject_id or md.get("subject_id"),
        period=period, metric=metric, operator=operator,
        line=_line_from(line, md.get("line"), md.get("points")), side=side,
        settlement_schema=md.get("settlement_schema"), raw_market_type=raw,
    )
    gaps = []
    if not sport:
        gaps.append("LEAGUE_CODE_AMBIGUOUS" if ambiguous
                    else "SPORT_NOT_NORMALIZED")
    if not metric:
        gaps.append("METRIC_NOT_NORMALIZED")
    return {"ok": not gaps, "meaning": meaning.to_dict(), "gaps": gaps,
            "sport_basis": sport_basis, "metric_source": metric_source,
            "version": VERSION}


def binary_sides(*, long_intent=None, long_side=None, short_intent=None,
                 short_side=None) -> dict:
    """BOTH ECONOMIC SIDES OF ONE BINARY CONTRACT, first-class. The venue
    publishes ONE book (the long instrument); the short side is its
    complement -- never a separate, manufactured NO book. A short position's
    executable price is 1 - (the long side's opposite touch) and it pays
    1 - (the long payout)."""
    return {
        "LONG": {"economic_side": "YES", "intent": long_intent,
                 "side_norm": long_side, "price_transform": "IDENTITY",
                 "buy_price_from": "LONG_BEST_OFFER",
                 "sell_price_from": "LONG_BEST_BID",
                 "payout": "1 IF YES ELSE 0"},
        "SHORT": {"economic_side": "NO", "intent": short_intent,
                  "side_norm": short_side, "price_transform": "COMPLEMENT",
                  "buy_price_from": "1 - LONG_BEST_BID",
                  "sell_price_from": "1 - LONG_BEST_OFFER",
                  "payout": "1 IF NO ELSE 0"},
        "independent_short_book": False,
        "version": VERSION}


def side_price(book: dict, side: str, action: str):
    """PURE. The executable price of `action` (BUY/SELL) on `side`
    (LONG/SHORT) from the ONE long book {"bids": [[p, q]], "offers": [...]};
    None when the needed level is absent. SHORT is the complement."""
    def top(levels, best):
        ps = [float(l[0]) for l in levels or () if l and l[0] is not None]
        return (max(ps) if best == "max" else min(ps)) if ps else None
    bid = top((book or {}).get("bids"), "max")
    offer = top((book or {}).get("offers"), "min")
    if side == "LONG":
        return offer if action == "BUY" else bid
    if side == "SHORT":
        if action == "BUY":
            return None if bid is None else round(1.0 - bid, 10)
        return None if offer is None else round(1.0 - offer, 10)
    return None
