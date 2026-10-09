"""KALSHI SPORTS CONTRACT ONTOLOGY -- what each listed Kalshi binary pays on,
read from the venue's own series tag and the contract's own rules (RC6).

MARKET DATA / RESEARCH ONLY. Pure functions; no network, no credential, no
order, size, price or authority. This module imports nothing from
kalshi_venue / kalshi_orders (tests/test_kalshi_isolation.py).

THE GAP IT CLOSES (production RC5, pm-acceptance 37836393458): every active
Kalshi registry row -- 76,467 at RC5, 80,497 on 2026-10-09 (research
rc6_kalshi_ontology_explore_a) -- was CODE_CONTROLLED_GAP with the single
reason ONTOLOGY_GAPS:KALSHI_ONTOLOGY_NOT_MAPPED, because
market_plane.populate.kalshi_contract_row assigned no sport, family or
period ("no guessed mapping").

THE EVIDENCE, AND ONLY THAT:
  sport      the SERIES' own `tags` (GET /series: "Football", "Soccer",
             "Basketball", ...): exactly one sports tag, else refused. A
             sport word the contract's own rules state ("college football",
             "professional MLS soccer", "NHL") that names a different sport
             is a conflict, refused.
  event      the venue's event ticker; the market ticker must sit under it
             (`<event>-<code>`; a single-market event is its own ticker),
             and the event ticker under the series.
  family     the contract's own `rules_primary` -- the venue's terms, the
             text the market settles on (the same field kalshi_contract_terms
             and settlement_rule_registry read) -- matched ANCHORED, whole
             sentence, against the venue's templates (below). A sentence
             that matches no template is refused by name; nothing is matched
             on titles, sub-titles or the series title.
  subject    the team the contract names must be one of the two
             participants the same sentence names ("<A> vs <B>"), or the
             market's own ticker code must be one of the two codes of the
             event ticker (Kalshi's identifiers: KXNFLGAME-26OCT18ARILA-ARI
             -> ARI of ARI|LA). Neither -> refused (never a name match).
  line       the number the sentence states, with its operator: "more than"
             / "over" / "above" -> GT; "at least" -> GE.
  period     the period the sentence states: 1st / 2nd half, quarter,
             period, inning, first N innings, map, set; the full game
             otherwise. An overtime clause
             it states is recorded ("(including overtime)" /
             "(excluding overtime)"); soccer's "after 90 minutes plus
             stoppage time (does not include extra time or penalties)" is
             recorded as extra time EXCLUDED.

THE FAMILIES MAPPED: game winners (a team, or the tie / draw outcome, of the
full game or of a stated period), spreads (MARGIN), game totals (TOTAL) and
team totals (TEAM_SCORE). Everything else is recognised and REFUSED BY NAME,
never silently: player props (no agreed universe states them in scope),
team statistic props, other game props (exact score, first scorer, race to
N, double result, method of victory, overtime yes/no, ...), outrights /
season / award contracts, non-binary (ladder / escalator) payouts, and any
sentence no template recognises.

SETTLEMENT TERMS, PER CONTRACT (`terms`, bind_terms): bound by the
fingerprint of the contract's own rule block (rules_primary +
rules_secondary, settlement_rule_registry.kalshi_rules_sha256 -- the key of
its market_plane_rules row, where kalshi_rule_evidence parses that block
once per change), with the scope its own sentence states (overtime, extra
time, a half's 45 minutes). Nothing the text does not state is filled in;
the series rulebook (kalshi_contract_terms.bind) applies only on the
worker's verified live hash, never here. Mapping is not settlement proof:
market_plane.settlement decides that, from evidence, and a mapped Kalshi
full-event winner is compared there exactly as a Polymarket US one is.

COST. classify is a few string tests and at most a handful of anchored
regexes per contract (needles gate every template and game-prop pattern):
~25 us per market on the 80,659-market catalogue of 2026-10-09, so a walk's
persist adds ~2 s of CPU, paged (populate_kalshi awaits between pages).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

VERSION = "KALSHI_ONTOLOGY_V1"
VENUE = "KALSHI"
MAPPED = "MAPPED"
REFUSED = "REFUSED"

# ── refusal codes (each a CODE_CONTROLLED_GAP reason, by name) ──────────
R_NO_TICKER = "KALSHI_ONTOLOGY_TICKER_MISSING"
R_EVENT = "KALSHI_ONTOLOGY_TICKER_NOT_UNDER_ITS_EVENT"
R_RULES = "KALSHI_ONTOLOGY_RULES_PRIMARY_NOT_CAPTURED"
R_SPORT_TAG = "KALSHI_ONTOLOGY_SERIES_HAS_NO_SINGLE_SPORT_TAG"
R_SPORT_CONFLICT = "KALSHI_ONTOLOGY_SPORT_TAG_CONTRADICTS_RULES"
R_NON_BINARY = "KALSHI_ONTOLOGY_NON_BINARY_PAYOUT"
R_OUTRIGHT = "KALSHI_ONTOLOGY_OUTRIGHT_OR_SEASON_CONTRACT_NOT_IN_MAPPER"
R_PLAYER_PROP = "KALSHI_ONTOLOGY_PLAYER_PROP_NOT_IN_AGREED_UNIVERSE"
R_TEAM_STAT = "KALSHI_ONTOLOGY_TEAM_STAT_PROP_NOT_IN_MAPPER"
R_GAME_PROP = "KALSHI_ONTOLOGY_GAME_PROP_NOT_IN_MAPPER"
R_TEMPLATE = "KALSHI_ONTOLOGY_RULES_TEMPLATE_NOT_RECOGNISED"
R_SUBJECT = "KALSHI_ONTOLOGY_SUBJECT_NOT_A_PARTICIPANT"
R_PERIOD = "KALSHI_ONTOLOGY_PERIOD_NOT_RECOGNISED"
R_LINE = "KALSHI_ONTOLOGY_LINE_NOT_A_NUMBER"

#: the series tags that name a sport (GET /series `tags`), and the sport
#: vocabulary of market_plane.ontology (football = American football)
SPORT_TAGS = {
    "Football": "football", "Basketball": "basketball",
    "Baseball": "baseball", "Hockey": "hockey", "Soccer": "soccer",
    "Tennis": "tennis", "Golf": "golf", "Esports": "esports",
    "Motorsport": "motorsport", "MMA": "mma", "Boxing": "boxing",
    "Cricket": "cricket", "Volleyball": "volleyball",
    "Lacrosse": "lacrosse", "Rugby": "rugby", "Darts": "darts",
    "Chess": "chess", "Table Tennis": "table_tennis",
}

#: a sport the contract's own sentence names (word-bounded), checked
#: against the series tag; "NHL" is the venue's own hockey descriptor
_SPORT_WORDS_RX = re.compile(
    r"\b(soccer|football|basketball|baseball|hockey|tennis|volleyball|"
    r"cricket|darts|lacrosse|rugby)\b|\b(NHL)\b", re.I)
_SPORT_WORD = {"nhl": "hockey"}

# ── the sentence grammar ────────────────────────────────────────────────

_NUM = r"(?P<line>\d+(?:\.\d+)?)"
#: the period phrase the venue writes, its overtime clause kept
_PER = (r"(?P<period>(?:1st|2nd|3rd|4th|first|second|third|fourth) "
        r"(?:half|quarter|period)"
        r"(?: \((?:including|excluding|not including) overtime\))?"
        r"|full game(?: \((?:including|excluding|not including) overtime\))?"
        r"|map \d+|set \d+|\d+(?:st|nd|rd|th) inning|first \d+ innings)")
#: a phrase that never crosses a clause boundary (a tempered token): an
#: event or a subject never contains " in the ", " of the ", " wins ",
#: " win ", " tie " -- so a compound sentence ("wins the 1st half and ...
#: wins the full game in the A vs B game") cannot be read as a simple one
_NB = r"(?:(?! in the | of the | wins? | tie | and the | both | either ).)"
_EVENT = r"(?P<event>" + _NB + r"+? vs\.? " + _NB + r"+?)"
_SUBJ = r"(?P<subj>" + _NB + r"+?)"
_SUBJ2 = r"(?P<subj2>" + _NB + r"+?)"
#: after the game noun: the schedule, stage, scope clauses, then the end
_WHEN = (r"(?P<when> originally scheduled for [A-Z][a-z]{2,8}\.? \d{1,2},"
         r" \d{4}(?: at \d{1,2}:\d{2} ?[AP]M(?: [A-Z]{2,4})?)?)?")
_END = (r"(?P<stage> in the [^,]+?)?"
        r"(?P<reg> after 90 minutes plus stoppage time \(does not include"
        r" extra time or penalties\))?"
        r"(?P<half45> after 45 minutes plus stoppage time)?"
        r"(?P<played> after a ball has been played)?"
        r", then the market resolves to Yes\.?\s*$")
_TAIL = r" (?P<noun>game|match|fight)" + _WHEN + _END


def _rx(head: str, event: str = _EVENT, tail: str = _TAIL) -> re.Pattern:
    return re.compile("^If " + head + event + tail, re.I | re.S)


_IN_PER = r"(?: in the " + _PER + r" of the| in the) "
_UNIT = r" (?P<unit>points?|goals?|runs?)"

#: (template id, family, needles, compiled sentence). Each is anchored on
#: the whole sentence; the first that matches is the contract's template.
#: The needles are a cost bound only: a template is tried only when its
#: sentence contains one of them (lower-case), which every sentence the
#: template matches does (tests/test_rc6_kalshi_ontology.py pins it on the
#: real catalogue sample).
TEMPLATES = (
    # ── TOTALS ──
    ("TOTAL_TEAMS_COLLECTIVELY", "TOTAL", ('the teams collectively',), _rx(
        r"the teams collectively score more than " + _NUM + _UNIT + _IN_PER)),
    # (KXMLBTOTAL states "collectively score more 5.5 runs": "more" with
    # its "than" left out is still strictly more, and nothing else)
    ("TOTAL_TEAMS_IN_GAME_COLLECTIVELY", "TOTAL", ('the teams in the game collectively',), _rx(
        r"the teams in the game collectively score more than " + _NUM +
        _UNIT + r" in the " + _PER + r" of the ")),
    ("TOTAL_TEAMS_IN_EVENT_COLLECTIVELY", "TOTAL", ('if the teams in the ',), re.compile(
        r"^If the teams in the " + _EVENT + r" (?P<noun>game|match)" + _WHEN +
        r" collectively score more than " + _NUM + _UNIT + _END, re.I | re.S)),
    ("TOTAL_PAIR_COLLECTIVELY", "TOTAL", (' collectively score more',), _rx(
        _SUBJ + r" and " + _SUBJ2 + r" collectively score more(?: than)? " +
        _NUM + _UNIT + _IN_PER)),
    ("TOTAL_PAIR_COLLECTIVELY_AND_EVENT", "TOTAL", (' collectively score more',), _rx(
        _SUBJ + r" and " + _SUBJ2 + r" collectively score more than " +
        _NUM + _UNIT + r" in the ",
        event=r"(?P<event>" + _NB + r"+? and " + _NB + r"+?)")),
    ("TOTAL_OVER_SCORED", "TOTAL", ('if over ',), _rx(
        r"over " + _NUM + r" (?:total )?(?P<unit>points?|goals?|runs?|maps?)"
        r" (?:are|is) (?:scored|played) in the (?:" + _PER + r" of the )?")),
    ("TOTAL_GOALS_SCORED_BY_PAIR", "TOTAL", ('if the total ',), _rx(
        r"the total (?P<unit>goals|points|runs) scored by " + _SUBJ +
        r" and " + _SUBJ2 + r" is more than " + _NUM +
        r" (?:goals|points|runs)" + _IN_PER)),
    ("TOTAL_TENNIS_GAMES", "TOTAL", ('number of completed games',), _rx(
        r"the number of completed (?P<unit>games) in the full match is "
        r"above " + _NUM + r" in the ")),
    # ── SPREADS ──
    ("SPREAD_WINS_BY", "MARGIN", (' by more than ', ' by over '), _rx(
        r"(?:the )?" + _SUBJ + r" wins? by (?:more than|over) " + _NUM +
        _UNIT + _IN_PER)),
    ("SPREAD_POINT_DIFFERENTIAL", "MARGIN", ('point differential of at least',), _rx(
        _SUBJ + r" has a positive point differential of at least " + _NUM +
        r" (?P<unit>points?) against (?P<opp>" + _NB + r"+?) in the " +
        _PER + r" of the ")),
    ("SPREAD_TENNIS_GAMES", "MARGIN", ('game differential in favor of',), _rx(
        r"the game differential in favor of " + _SUBJ + r" across the full "
        r"match is above " + _NUM + r" (?P<unit>games) in the ")),
    # ── TEAM TOTALS ──
    ("TEAM_TOTAL_SCORES_OVER", "TEAM_SCORE", (' scores over ', ' scores more than ', ' records above '), _rx(
        _SUBJ + r" (?:scores over|scores more than|records above) " + _NUM +
        _UNIT + _IN_PER)),
    ("TEAM_TOTAL_SCORES_N_PLUS", "TEAM_SCORE", ('+ runs', '+ points', '+ goals'), _rx(
        _SUBJ + r" scores " + _NUM + r"\+" + _UNIT + _IN_PER)),
    # ── WINNERS ──
    ("WINNER_TIE_RESULT_OF_PERIOD_IN_REGULATION", "WINNER", ('of regulation time',), _rx(
        r"(?P<tie>Tie) is the result of the " + _PER +
        r" of (?P<regtime>regulation time) in the ")),
    ("WINNER_OF_PERIOD_IN_REGULATION", "WINNER", ('of regulation time',), _rx(
        _SUBJ + r" is the winner of the " + _PER +
        r" of (?P<regtime>regulation time) in the ")),
    ("WINNER_TIE_RESULT", "WINNER", ('is the result',), _rx(
        r"(?P<tie>Tie) is the result (?:of the (?:(?P<period>first half|"
        r"second half) in the )?|in the )")),
    ("WINNER_NEITHER_TEAM_WINS_TIE", "WINNER", ('neither team wins and',), _rx(
        r"(?P<tie>neither team) wins and the game ends in a tie in the ")),
    ("WINNER_NEITHER_TEAM_PERIOD", "WINNER", ('neither team wins the',), _rx(
        r"(?P<tie>neither team) wins the " + _PER + r" of the ")),
    ("WINNER_NEITHER_SCORES_MORE_IN_INNING", "WINNER", ('scores more runs',), _rx(
        r"(?P<tie>neither) " + _SUBJ + r" nor " + _SUBJ2 + r" scores more "
        r"runs in the " + _PER + r" of the ")),
    ("WINNER_PAIR_TIE", "WINNER", (' tie in the ', ' draw or tie in the '), _rx(
        _SUBJ + r" and " + _SUBJ2 + r" (?P<tie>tie|draw or tie)"
        r"(?: in the " + _PER + r" of the| in the) ")),
    ("WINNER_SCORES_MORE_IN_INNING", "WINNER", ('scores more runs',), _rx(
        _SUBJ + r" scores more runs in the " + _PER + r" of the ")),
    ("WINNER_OF_HALF", "WINNER", ('is the winner of the',), _rx(
        _SUBJ + r" is the winner of the (?P<period>first half|second half)"
        r" in the ")),
    ("WINNER_IS_THE_WINNER", "WINNER", ('is the winner in the',), _rx(_SUBJ + r" is the winner in the ")),
    ("WINNER_PERIOD", "WINNER", (' win',), _rx(
        _SUBJ + r" wins (?:the )?" + _PER + r" (?:of|in) the ")),
    ("WINNER_GAME", "WINNER", (' win',), _rx(r"(?:the )?" + _SUBJ + r" wins? the ")),
)

#: recognised families outside the mapper, refused by name; checked BEFORE
#: the templates (a compound or prop sentence is never read as a simple one)
_NON_BINARY_HEAD = "if each yes contract pays "
_SERIES_SCORE_HEAD = "if the series score is "
#: (name, needles, pattern) over the LOWER-CASED sentence; the pattern is
#: tried only when a needle is present (a cost bound only: every sentence
#: the pattern matches contains one)
_GAME_PROPS = tuple((n, nd, re.compile(rx)) for n, nd, rx in (
    ("EXACT_SCORE", ("if the result is a ", "if the score at the end of",
                     " wins "),
     r"^if .+? wins \d+-\d+ in the |^if the result is a \d+-\d+ draw\b|"
     r"^if the score at the end of\b"),
    ("DOUBLE_RESULT", ("half and",), r"\b(?:1st|first) half and\b"),
    ("RACE_TO", ("first to score",), r"\bis the first to score\b"),
    ("FIRST_SCORER", ("the first ", "the 1st "),
     r"\bscores? the (?:first|1st) (?:touchdown|goal|run|basket)\b|"
     r"\brecords the first goal\b"),
    ("METHOD_OF_VICTORY", ("wins by ", "no contest", "ends before round"),
     r"\bwins by (?:decision|ko|tko|submission|disqualification)\b|"
     r"\bdraw or no contest\b|\bdraw/no contest\b|\bends before round\b"),
    ("OVERTIME", ("overtime", "extra inning"),
     r"\bovertime period is played\b|\bgoes to overtime\b|"
     r"\bat least 1 overtime period\b|\bextra inning is played\b"),
    ("GOES_THE_DISTANCE", ("the distance",), r"\bgoes the distance\b"),
    ("WEATHER_DELAY", ("weather",), r"\bdelay due to weather\b"),
    ("SET_SCORE", ("set score",), r"\bby a set score of\b"),
    ("BOTH_TEAMS_TO_SCORE", ("both score", "each record"),
     r"\bboth score\b|\beach record at least\b"),
    ("CORNERS", ("corners",), r"\bcorners\b"),
    ("WINNING_MARGIN_BAND", ("differential of between",),
     r"\bpoint differential of between\b"),
    ("SCORING_EVENT", ("special teams", "safety", "if either team",
                       "scored by either"),
     r"\bdefensive or special teams touchdown\b|\brecord a safety\b|"
     r"^if either team scores\b|^if at least \d+ goals? is scored by "
     r"either\b"),
))
_STAT_HEAD = re.compile(
    r"^If (?:the )?(?P<subj>.+?) (?:records|scores|has|finishes|makes)\b",
    re.I)
_ANY_EVENT = re.compile(r" (?:of|in|for) (?:the )?(?P<event>[^,]+? vs\.? "
                        r"[^,]+?) (?:game|match|fight)\b", re.I)
#: a contract on a single fixture names one ("A vs B ... game|match|fight")
_HAS_EVENT = re.compile(r" vs\.? .+? (?:game|match|fight)\b", re.I)

_PERIODS = {
    "1st half": "FIRST_HALF", "first half": "FIRST_HALF",
    "2nd half": "SECOND_HALF", "second half": "SECOND_HALF",
    "1st quarter": "Q1", "first quarter": "Q1",
    "2nd quarter": "Q2", "second quarter": "Q2",
    "3rd quarter": "Q3", "third quarter": "Q3",
    "4th quarter": "Q4", "fourth quarter": "Q4",
    "1st period": "P1", "first period": "P1",
    "2nd period": "P2", "second period": "P2",
    "3rd period": "P3", "third period": "P3",
    "full game": "FULL_EVENT",
}
_UNITS = {"point": "POINTS", "goal": "GOALS", "run": "RUNS",
          "game": "GAMES", "map": "MAPS"}
FAMILY_CLASS = {"WINNER": "GAME_WINNER", "MARGIN": "SPREAD",
                "TOTAL": "TOTAL", "TEAM_SCORE": "TEAM_TOTAL"}


def _flat(s) -> str:
    return " ".join(str(s or "").split())


def _dec(s):
    try:
        return Decimal(str(s))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _period(raw):
    """(PERIOD, overtime clause) from the stated period phrase: the full
    game when nothing is stated; (None, None) if unrecognised."""
    if raw is None:
        return "FULL_EVENT", None
    s = raw.strip().lower()
    ot = None
    m = re.search(r"\((including|excluding|not including) overtime\)$", s)
    if m:
        ot = "INCLUDED" if m.group(1) == "including" else "EXCLUDED"
        s = s[:m.start()].strip()
    if s in _PERIODS:
        return _PERIODS[s], ot
    m = re.fullmatch(r"(map|set) (\d+)", s)
    if m:
        return "%s_%s" % (m.group(1).upper(), m.group(2)), ot
    m = re.fullmatch(r"(\d+)(?:st|nd|rd|th) inning", s)
    if m:
        return "INNING_%s" % m.group(1), ot
    m = re.fullmatch(r"first (\d+) innings", s)
    if m:
        return "INNINGS_1_TO_%s" % m.group(1), ot
    return None, None


def _unit(raw):
    s = str(raw or "").strip().lower()
    s = s[:-1] if s.endswith("s") else s
    return _UNITS.get(s)


def split_event(event: str):
    """("<A>", "<B> <descriptor>") from the sentence's event phrase
    ("A vs B ..." or, in the venue's KBO / NPB totals, "A and B ...")."""
    e = _flat(event)
    m = re.match(r"^(?P<a>.+?) vs\.? (?P<b>.+)$", e) or re.match(
        r"^(?P<a>.+?) and (?P<b>.+)$", e)
    return (m.group("a"), m.group("b")) if m else (None, None)


def _strip_the(s: str) -> str:
    """The venue's lower-case article ("the NC Dinos win"), never a name's
    own capitalised word ("THE UNIT", "The Strongest")."""
    s = _flat(s)
    return s[4:] if s.startswith("the ") else s


def named_participant(subject: str, event: str) -> bool:
    """The subject is one of the two sides the sentence names: the whole
    left side (or its last words after a competition prefix such as
    "MESA Pro Series 2026: 1337"), or the leading words of the right side
    (before the venue's descriptor: "Abilene Christian college football")."""
    a, b = split_event(event)
    s = _strip_the(subject)
    if not s or a is None:
        return False
    return (a == s or a.endswith(" " + s) or b == s
            or b.startswith(s + " "))


_DATE_HEAD = re.compile(r"^\d{2}[A-Z]{3}\d{2}(?:\d{4})?")


def ticker_code(ticker: str, event_ticker: str) -> str | None:
    t, e = str(ticker or ""), str(event_ticker or "")
    if not e or not t.startswith(e + "-"):
        return None
    return t[len(e) + 1:] or None


def coded_participant(ticker: str, event_ticker: str) -> str | None:
    """Kalshi's own identifiers: the event ticker's team block (after the
    series and the date) is the two teams' codes, and a team market's
    ticker ends in its team's code (a line may follow it). The code when
    the block splits EXACTLY into it and one other non-empty code, else
    None."""
    code = ticker_code(ticker, event_ticker)
    parts = str(event_ticker or "").split("-", 1)
    if code is None or len(parts) != 2:
        return None
    block = _DATE_HEAD.sub("", parts[1])
    letters = re.match(r"^[A-Z]+", code)
    if not block or letters is None:
        return None
    c = letters.group(0)
    if len(block) > len(c) and (block.startswith(c) or block.endswith(c)):
        return c
    return None


def sport_of(series: dict) -> tuple:
    """(sport, why) from the series' own tags: exactly one sports tag."""
    tags = [str(t) for t in (series or {}).get("tags") or []]
    sports = sorted({SPORT_TAGS[t] for t in tags if t in SPORT_TAGS})
    if len(sports) != 1:
        return None, "%s:%s" % (R_SPORT_TAG, ",".join(tags) or "NONE")
    return sports[0], None


def _sport_conflict(sport: str, event: str):
    """The sports the sentence names when it does not name the tag's own
    ("Czech National Football League soccer" names soccer: no conflict)."""
    named = {_SPORT_WORD.get(w.lower(), w.lower())
             for a, b in _SPORT_WORDS_RX.findall(event or "")
             for w in (a or (b if b == "NHL" else ""),) if w}
    if not named or sport in named:
        return None
    return sorted(named)


def _blank(m: dict, ser: dict) -> dict:
    return {"status": REFUSED, "refusal": None, "family": None,
            "family_class": None, "sport": None, "period": None,
            "operator": None, "line": None, "unit": None, "subject": None,
            "scope": None, "event_id": m.get("event_ticker"),
            "series": ser.get("ticker"), "template": None,
            "scheduled_as_stated": None, "version": VERSION}


def _subject(d: dict, fam: str, tid: str, t: str, ev: str):
    """The contract's subject from the matched sentence, or a refusal."""
    event = d.get("event")
    names = [x for x in (d.get("subj"), d.get("subj2"), d.get("opp")) if x]
    if d.get("tie"):
        out = {"type": "TIE", "as_stated": _flat(d["tie"])}
        if names:
            if not all(named_participant(x, event) for x in names):
                return None, R_SUBJECT
            out["between"] = [_strip_the(x) for x in names]
        return out, None
    if not names:
        return {"type": "EVENT"}, None
    if len(names) > 1 or tid.startswith("TOTAL_"):
        # a pair (totals) or a subject with an opponent: every name must be
        # a participant the sentence itself names
        if not all(named_participant(x, event) for x in names):
            return None, R_SUBJECT
        return ({"type": "EVENT", "as_stated": [_strip_the(x)
                                                for x in names]}
                if tid.startswith("TOTAL_") else
                {"type": "TEAM", "as_stated": _strip_the(names[0]),
                 "opponent": _strip_the(names[1]) if len(names) > 1
                 else None, "ticker_code": coded_participant(t, ev),
                 "basis": "RULES_NAMES_A_PARTICIPANT"}), None
    s = names[0]
    code = coded_participant(t, ev)
    if named_participant(s, event):
        return {"type": "TEAM", "as_stated": _strip_the(s),
                "ticker_code": code,
                "basis": "RULES_NAMES_A_PARTICIPANT"}, None
    if code is not None:
        return {"type": "TEAM", "as_stated": _strip_the(s),
                "ticker_code": code, "basis": "TICKER_CODE_IN_EVENT"}, None
    return None, R_SUBJECT


def classify(market: dict) -> dict:
    """PURE. One Kalshi market (ticker, event_ticker, rules_primary,
    _series {ticker, title, tags, category}) -> its ontology verdict:
    {status MAPPED | REFUSED, refusal, family, family_class, sport, period,
    operator, line, unit, subject, scope, event_id, series, template,
    scheduled_as_stated, version}. Titles are never read."""
    m = market or {}
    ser = dict(m.get("_series") or {})
    out = _blank(m, ser)

    def refuse(code, cls=None, tid=None):
        out.update(refusal=code, family_class=cls, template=tid)
        return out
    t, ev = m.get("ticker"), m.get("event_ticker")
    if not t:
        return refuse(R_NO_TICKER)
    # a market sits under its event (a single-market event IS its event),
    # and the event under the series
    if not ev or not (str(t) == str(ev) or str(t).startswith(
            str(ev) + "-")) or (ser.get("ticker") and not str(ev).startswith(
                str(ser["ticker"]) + "-")):
        return refuse(R_EVENT)
    sport, why = sport_of(ser)
    out["sport"] = sport
    rp = _flat(m.get("rules_primary"))
    if not rp:
        return refuse(R_RULES)
    low = rp.lower()
    if low.startswith(_NON_BINARY_HEAD):
        return refuse(R_NON_BINARY, "NON_BINARY")
    if low.startswith(_SERIES_SCORE_HEAD):
        return refuse(R_OUTRIGHT, "OUTRIGHT")
    if " vs" not in low and " collectively " not in low:
        # every single-fixture sentence names its fixture "<A> vs <B>" (the
        # KBO / NPB totals, "<A> and <B> collectively ..."): this is an
        # outright / season / award contract
        return refuse(R_OUTRIGHT, "OUTRIGHT")
    for name, nd, rx in _GAME_PROPS:
        if any(n in low for n in nd) and rx.search(low):
            return refuse("%s:%s" % (R_GAME_PROP, name), "GAME_PROP")
    for tid, fam, nd, rx in TEMPLATES:
        if not any(n in low for n in nd):
            continue
        g = rx.match(rp)
        if g is None:
            continue
        d = g.groupdict()
        cls = FAMILY_CLASS[fam]
        if sport is None:
            return refuse(why, cls, tid)
        bad = _sport_conflict(sport, d.get("event"))
        if bad:
            return refuse("%s:%s" % (R_SPORT_CONFLICT, ",".join(bad)), cls,
                          tid)
        period, ot = _period(d.get("period"))
        if period is None:
            return refuse(R_PERIOD, cls, tid)
        line = op = None
        if d.get("line") is not None:
            line = _dec(d["line"])
            if line is None:
                return refuse(R_LINE, cls, tid)
            op = "GE" if tid in ("SPREAD_POINT_DIFFERENTIAL",
                                 "TEAM_TOTAL_SCORES_N_PLUS") else "GT"
        unit = _unit(d.get("unit"))
        if fam != "WINNER" and unit is None:
            return refuse(R_TEMPLATE, cls, tid)
        subject, bad_subject = _subject(d, fam, tid, t, ev)
        if bad_subject:
            return refuse(bad_subject, cls, tid)
        out.update(
            status=MAPPED, family=fam, family_class=cls, period=period,
            operator=op, line=None if line is None else str(line),
            unit=unit, subject=subject, template=tid,
            scope={"overtime": ot or ("EXCLUDED" if d.get("regtime")
                                      else "NOT_STATED_IN_MARKET_TEXT"),
                   "extra_time": ("EXCLUDED" if d.get("reg") else
                                  "NOT_STATED_IN_MARKET_TEXT"),
                   "half_basis": ("45_MINUTES_PLUS_STOPPAGE"
                                  if d.get("half45") else None)},
            scheduled_as_stated=(
                _flat(d["when"])[len("originally scheduled for "):]
                if d.get("when") else None))
        return out
    if not _HAS_EVENT.search(rp):
        return refuse(R_OUTRIGHT, "OUTRIGHT")
    h = _STAT_HEAD.match(rp)
    if h is not None:
        g = _ANY_EVENT.search(rp)
        if g is not None and named_participant(h.group("subj"),
                                               g.group("event")):
            return refuse(R_TEAM_STAT, "TEAM_STAT_PROP")
        return refuse(R_PLAYER_PROP, "PLAYER_PROP")
    return refuse(R_TEMPLATE, "UNRECOGNISED")


def bind_terms(market: dict, verdict: dict) -> dict:
    """THIS contract's settlement terms, bound by fingerprint: the sha256 of
    its own rule block (rules_primary + rules_secondary,
    settlement_rule_registry.kalshi_rules_sha256 -- the key of its
    market_plane_rules row, where that block is parsed once by
    kalshi_rule_evidence), the scope its own sentence states (overtime,
    extra time, the half's 45 minutes) and the series rulebook's status.
    A change of either text is a new fingerprint, so the registry row's
    content changes with it. The rulebook (kalshi_contract_terms.bind)
    applies only on the worker's verified live hash, never here; nothing
    the text does not state is filled in."""
    from . import settlement_rule_registry as SRR
    v = verdict or {}
    return {"rules_sha256": SRR.kalshi_rules_sha256(market),
            "parsed_in": TERMS_PARSED_IN,
            "scope": {k: x for k, x in (v.get("scope") or {}).items()
                      if x is not None},
            "rulebook": RULEBOOK_NOT_APPLIED}


#: where a bound contract's rule block is parsed, and the rulebook status
#: (short codes: the registry row is rewritten on every walk)
TERMS_PARSED_IN = "MARKET_PLANE_RULES_SAME_SHA256"
RULEBOOK_NOT_APPLIED = "NOT_APPLIED_HERE_VERIFIED_HASH_ONLY"


def stored(verdict: dict) -> dict:
    """The verdict as the registry row keeps it: what the row's meaning
    does not already carry (sport, family, period, operator, line, event,
    series are there), without empty fields."""
    keep = ("status", "refusal", "family_class", "template", "unit",
            "subject", "scope", "scheduled_as_stated", "version")
    return {k: verdict[k] for k in keep
            if verdict.get(k) not in (None, {}, [])}
