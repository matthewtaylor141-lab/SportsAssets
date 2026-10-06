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
}


def _line_from(*values):
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return None


def parse_market_type(*, venue: str, contract_id: str, sports_market_type: str | None,
                      competition: str | None = None, event_id: str | None = None,
                      line=None, side=None, subject_type=None, subject_id=None,
                      metadata: dict | None = None) -> dict:
    raw = str(sports_market_type or "").strip().lower().replace("-", "_")
    md = metadata or {}
    sport = None
    head = raw.split("_", 1)[0] if raw else ""
    for p, canonical in SPORT_PREFIXES.items():
        if head.startswith(p) or raw.startswith(p + "_"):
            sport = canonical
            break
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
    if metric is None and raw.endswith("_total"):
        metric, operator = "TOTAL", "TOTAL"
    subj_type = subject_type or md.get("subject_type")
    if subj_type is None:
        if any(t in raw for t in ("player_", "pitcher_", "quarterback_")):
            subj_type = "PLAYER"
        elif "team_" in raw:
            subj_type = "TEAM"
        elif metric in ("CHAMPION", "AWARD_WINNER"):
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
        gaps.append("SPORT_NOT_NORMALIZED")
    if not metric:
        gaps.append("METRIC_NOT_NORMALIZED")
    return {"ok": not gaps, "meaning": meaning.to_dict(), "gaps": gaps,
            "version": VERSION}
