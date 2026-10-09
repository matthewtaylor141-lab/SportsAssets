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
)

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
    "vkl": "efootball", "btla": "efootball",
    "ufc": "mma", "dwcs": "mma",
    "pga": "golf", "pgatc": "golf", "kft": "golf", "dpwt": "golf",
    "liv": "golf", "nascar": "motorsport", "f1": "motorsport",
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
AMBIGUOUS_LEAGUE_CODES = {"wbc": ("baseball", "boxing")}

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
            ambiguous = True
        elif lg in LEAGUE_SPORT:
            sport, sport_basis = LEAGUE_SPORT[lg], "VENUE_LEAGUE_CODE"
    period = "FULL_EVENT"
    for rx, value in _PERIOD_PATTERNS:
        if rx.search(raw):
            period = value
            break
    metric = operator = None
    normalized = raw
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
