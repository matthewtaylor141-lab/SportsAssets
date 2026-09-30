"""MANAGEMENT DIRECTIVES: an instruction, translated into a durable record.

Management tells Audrey what to prioritise ("Prioritize reducing drawdown
without increasing capital limits."). This module turns that sentence into a
structured directive (migration 156 `management_directives`) and follows it:

  requester            the role the ROUTE authenticated (`operator`/`admin`),
                       passed in by the caller -- never a name typed in the
                       message. A read credential cannot create a directive.
  objective / kind     what should improve, classified by keyword
  scope                accounts, agents and markets named (or the stated
                       default, labelled as a default)
  constraints          every numerical constraint supplied, verbatim and
                       parsed, plus the STANDING RULES that no directive can
                       lift (capital limits do not increase, risk does not
                       expand, submission switches and approval controls do not
                       change)
  acceptance criteria  how the outcome will be judged, from the authoritative
                       book
  review / expiry      from the text, or the labelled seven-day default
  change class         PRIORITY_ONLY (an investigation) or POLICY_CANDIDATE
                       (work that may produce a candidate policy, which is then
                       evaluated and approved through the existing release
                       process) -- AUTHORITY_CHANGE is only ever REFUSED
  required approval    what any resulting change needs before it can act
  assigned agent       DEREK (entries), XAVIER (positions) or both
  status / evidence    DRAFT_NEEDS_CLARIFICATION with the exact question when
                       an essential is missing; ACTIVE once complete (Derek's /
                       Xavier's improvement tasks are created through
                       `registry.create_task` with deterministic ids and
                       linked); IN_PROGRESS / COMPLETED from the linked tasks'
                       outcomes (`monitor`); EXPIRED; CANCELLED.

A DIRECTIVE HAS NO AUTHORITY OVER RISK. A profit target is an objective, not a
guarantee and not a permission to expand risk. A directive can NEVER grant
credentials, change approved limits, authorise a live release, flip a
submission switch or change approval controls: such content is refused by name
(PROHIBITED_SELF_AUTHORIZATION). When the request came from an operator
credential it is recorded as a REQUEST for the existing owner approval process
(the admin funded-limits / owner-authorization endpoints) -- it is not
executed, and nothing here can execute it.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import re

log = logging.getLogger(__name__)

VERSION = "directives-v1"

# ── statuses (migration 156 CHECK) ──────────────────────────────────
DRAFT = "DRAFT_NEEDS_CLARIFICATION"
ACTIVE = "ACTIVE"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
REFUSED = "REFUSED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
STATUSES = (DRAFT, ACTIVE, IN_PROGRESS, COMPLETED, REFUSED, EXPIRED,
            CANCELLED)
OPEN_STATUSES = (DRAFT, ACTIVE, IN_PROGRESS)
TERMINAL_STATUSES = (COMPLETED, REFUSED, EXPIRED, CANCELLED)

# ── change classes ──────────────────────────────────────────────────
PRIORITY_ONLY = "PRIORITY_ONLY"
POLICY_CANDIDATE = "POLICY_CANDIDATE"
AUTHORITY_CHANGE = "AUTHORITY_CHANGE"

APPROVAL_FOR = {
    PRIORITY_ONLY: "NONE_AN_INVESTIGATION_CHANGES_NO_BEHAVIOUR",
    POLICY_CANDIDATE: ("OWNER_RELEASE_APPROVAL_BEFORE_ANY_RESULTING_POLICY_"
                       "CHANGE_ACTS"),
    AUTHORITY_CHANGE: "OWNER_APPROVAL_PROCESS_NOT_THIS_CHANNEL",
}

# ── agents ──────────────────────────────────────────────────────────
DEREK = "DEREK"
XAVIER = "XAVIER"
BOTH = "DEREK_AND_XAVIER"
AGENTS_OF = {DEREK: [DEREK], XAVIER: [XAVIER], BOTH: [DEREK, XAVIER]}

# ── who may do what ─────────────────────────────────────────────────
#: roles `require_command_control` returns: the scoped operator session and
#: the service credential. Everything `require_command` alone returns is READ.
CONTROL_ROLES = frozenset({"operator", "admin"})
READ_ROLES = frozenset({"command", "desk"})

# ── refusals, by name ───────────────────────────────────────────────
R_PROHIBITED = "PROHIBITED_SELF_AUTHORIZATION"
R_REQUIRES_OPERATOR = "REQUIRES_OPERATOR_CREDENTIAL"
R_NO_SCHEMA = "MIGRATION_156_NOT_APPLIED"
R_NOT_FOUND = "NO_DIRECTIVE_WITH_THAT_ID"
R_NOT_OPEN = "THE_DIRECTIVE_IS_NOT_OPEN"
R_EMPTY = "THE_INSTRUCTION_IS_EMPTY"
R_BAD_AGENT = "TASKS_ARE_ASSIGNED_TO_DEREK_OR_XAVIER_ONLY"

TASK_KIND = "DIRECTIVE_IMPROVEMENT"
TASK_CREATED_BY = "AUDREY"

#: where a legitimate owner request is routed. These are the EXISTING owner
#: approval endpoints; this module never calls them.
OWNER_APPROVAL_ROUTES = {
    "RAISE_LIMITS": "POST /api/admin/funded-limits/approve",
    "EXPAND_RISK": ("POST /api/admin/funded-limits/approve (risk is bounded "
                    "by the owner-approved limits)"),
    "CHANGE_APPROVAL_CONTROLS": "POST /api/admin/funded-owner-authorization",
    "AUTHORIZE_RELEASE": ("the improvement release process (candidate "
                          "evaluation, then owner approval)"),
    "ENABLE_SUBMISSION": ("a reviewed code change: the submission switches "
                          "are module constants, not settings"),
    "GRANT_CREDENTIALS": ("the service's secret store, provisioned by the "
                          "owner outside the application"),
    "GRANT_AUTHORITY": ("the owner: roles come from credentials, never from a "
                        "message"),
    "RUN_SHELL": "not available through any agent channel",
}

STANDING_RULES = {
    "capital_limits": "NO_INCREASE",
    "risk_expansion": "NOT_PERMITTED",
    "submission_switches": "UNCHANGED",
    "approval_controls": "UNCHANGED",
    "credentials": "UNCHANGED",
    "execution_authority": ("UNCHANGED: orders only through the existing "
                            "deterministic path"),
}

DEFAULT_REVIEW_S = 7 * 86400.0


# ═════════════════════════════════════════════════════════════════════
# 1 · THE AUTHORITY SCREEN
# ═════════════════════════════════════════════════════════════════════

_GAP = r"(?P<gap>(?:\W+[\w'&.-]+){0,6}?)\W+"
_NEGATION = re.compile(
    r"\b(without|not|never|no|don'?t|do\s+not|avoid|avoiding|nor)\b", re.I)
_NEGATED_BEFORE = re.compile(
    r"\b(without|not|never|no|don'?t|do\s+not|avoid|avoiding|nor)\s+"
    r"(?:[\w'-]+\s+){0,2}$", re.I)

_SWITCHES = (r"FUNDED_SUBMISSION_ENABLED|REAL_ORDER_SUBMISSION_ENABLED|"
             r"FUNDED_EXIT_SUBMISSION_ENABLED|\w+_SUBMISSION_ENABLED")

_PROHIBITED: list[tuple[str, re.Pattern]] = [
    ("RAISE_LIMITS", re.compile(
        r"\b(raise|raising|increase|increasing|lift|lifting|double|doubling|"
        r"expand|expanding|loosen|loosening|remove|removing|bump|widen|"
        r"widening|relax|relaxing|exceed|exceeding|override|overriding|"
        r"change|changing|set|setting|adjust|adjusting|modify|modifying|"
        r"up)\b" + _GAP +
        r"(limits?|caps?|max(?:imum)?\s+(?:stake|exposure|position|size)s?|"
        r"allocation|leverage|bankroll|budget)\b", re.I)),
    # A profit target is not permission to expand risk: asking for more risk
    # is a limit question for the owner, whatever the wording.
    ("EXPAND_RISK", re.compile(
        r"\b(increase|increasing|raise|raising|double|doubling|triple|expand|"
        r"expanding|take\s+(?:on\s+)?more|bigger|larger|grow|scale\s+up|"
        r"max\s+out)\b" + _GAP +
        r"(risk|exposure|stakes?|sizing|position\s+sizes?|bet\s+sizes?|"
        r"order\s+sizes?|capital\s+at\s+risk|leverage)\b", re.I)),
    ("ENABLE_SUBMISSION", re.compile(
        r"\b(enable|enabling|turn\s+on|switch\s+on|flip|flipping|activate|"
        r"activating|allow|allowing|start|starting|unblock|unlock|set|"
        r"setting)\b" + _GAP +
        r"(submissions?|live\s+orders?|real\s+orders?|order\s+sending|"
        r"live\s+trading|real[- ]money|funded\s+(?:orders?|trading|lane|"
        r"exits?)|" + _SWITCHES + r")\b", re.I)),
    ("ENABLE_SUBMISSION", re.compile(r"\b(" + _SWITCHES + r")\b", re.I)),
    ("GRANT_CREDENTIALS", re.compile(
        r"\b(add|adding|grant|granting|give|giving|create|creating|issue|"
        r"store|set|rotate|share|reveal|show|print|send|provision|generate|"
        r"mint|paste|use)\b" + _GAP +
        r"(credentials?|api[ _-]?keys?|tokens?|passwords?|secrets?|"
        r"private[ _-]keys?|ANTHROPIC_API_KEY|ADMIN_TOKEN)\b", re.I)),
    ("GRANT_AUTHORITY", re.compile(
        r"\b(grant|granting|give|giving|provision|elevate|escalate|promote)\b"
        + _GAP +
        r"(access|permissions?|authority|privileges?|admin|operator|"
        r"control|superuser|root)\b", re.I)),
    ("AUTHORIZE_RELEASE", re.compile(
        r"\b(approve|approving|deploy|deploying|release|releasing|ship|"
        r"shipping|promote|promoting|authori[sz]e|authori[sz]ing|push|"
        r"roll\s*out|activate|activating|merge)\b" + _GAP +
        r"(release|deploy(?:ment)?|candidates?|polic(?:y|ies)|models?|"
        r"versions?|build|production|prod|live|changes?)\b", re.I)),
    ("AUTHORIZE_RELEASE", re.compile(
        r"\b(go(?:ing)?\s+live|deploy\s+(?:it|now|this)|approve\s+(?:it|this|"
        r"that))\b", re.I)),
    ("CHANGE_APPROVAL_CONTROLS", re.compile(
        r"\b(disable|disabling|bypass|bypassing|skip|skipping|remove|"
        r"removing|override|overriding|change|changing|turn\s+off|"
        r"switch\s+off|waive|waiving|weaken|ignore|ignoring|lift|lifting|"
        r"clear|clearing|unhalt|drop)\b" + _GAP +
        r"(approvals?|owner\s+authori[sz]ations?|authori[sz]ations?|halts?|"
        r"gates?|controls?|kill[- ]switch|review\s+requirements?|"
        r"sign[- ]offs?|safeguards?|rails?|checks?)\b", re.I)),
    ("RUN_SHELL", re.compile(
        r"\b(run|execute|exec|eval|invoke|spawn)\b" + _GAP +
        r"(shell|commands?|bash|sh|scripts?|python|sql|query|queries|psql|"
        r"terminal|subprocess)\b", re.I)),
    ("RUN_SHELL", re.compile(
        r"(`[^`]{2,}`|\brm\s+-rf\b|\bsudo\b|\bcurl\s+https?:|"
        r"\bDROP\s+TABLE\b|\bDELETE\s+FROM\b|\bTRUNCATE\b|\bINSERT\s+INTO\b|"
        r"\bUPDATE\s+\w+\s+SET\b|\bALTER\s+TABLE\b|\bos\.system\b|"
        r"\bsubprocess\b)", re.I)),
]

_PURE_QUESTION = re.compile(
    r"^\s*(what|why|how|which|who|when|where|did|does|is|are|was|were|has|"
    r"have)\b[^?]*\?\s*$", re.I | re.S)


def is_pure_question(text: str) -> bool:
    """An interrogative that ends with '?'. It asks about a record; it asks
    nothing to be done. ('Can you raise the limits?' is NOT one: it is a
    request phrased as a question, and is screened.)"""
    return bool(_PURE_QUESTION.match(text or ""))


def screen_authority(text: str, *, questions_exempt: bool = True) -> dict:
    """Does this text ask for authority no agent channel can give?

    Returns {"refused": bool, "refusal": R_PROHIBITED|None, "categories":
    [...], "matched": [snippets]}. A clause negated right before its verb
    ("without increasing capital limits") asks for the opposite and is not a
    request. A pure interrogative about records is exempt -- it can change
    nothing -- unless `questions_exempt=False`."""
    text = str(text or "")[:8000]
    out = {"refused": False, "refusal": None, "categories": [],
           "matched": []}
    if questions_exempt and is_pure_question(text):
        return out
    for name, rx in _PROHIBITED:
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 60):m.start()]
            if _NEGATED_BEFORE.search(before):
                continue
            gap = m.groupdict().get("gap") or ""
            if _NEGATION.search(gap):
                continue
            if name not in out["categories"]:
                out["categories"].append(name)
            out["matched"].append(m.group(0).strip()[:120])
            break
    if out["categories"]:
        out["refused"] = True
        out["refusal"] = R_PROHIBITED
    return out


def approval_request(screen: dict) -> dict:
    """What a legitimate owner request becomes: a pointer to the existing
    owner approval process. It is recorded; it is not executed."""
    return {
        "kind": "OWNER_APPROVAL_REQUEST",
        "executed": False,
        "categories": list(screen.get("categories") or []),
        "routes": {c: OWNER_APPROVAL_ROUTES.get(c, "owner approval process")
                   for c in screen.get("categories") or []},
        "note": ("recorded for the owner's existing approval process; this "
                 "channel cannot grant credentials, change approved limits, "
                 "authorise a release, flip a submission switch or change "
                 "approval controls"),
    }


# ═════════════════════════════════════════════════════════════════════
# 2 · TRANSLATION (pure)
# ═════════════════════════════════════════════════════════════════════

#: (kind, pattern, default agent, change class) -- first match wins
_KINDS: list[tuple[str, re.Pattern, str, str]] = [
    ("DRAWDOWN_REDUCTION", re.compile(
        r"\bdraw\s*-?\s*downs?\b|\bpeak[- ]to[- ]trough\b", re.I),
     BOTH, POLICY_CANDIDATE),
    ("PROFIT_TARGET", re.compile(
        r"\b(profit|p&l|pnl|net|returns?|earn|make)\b[^.;]*?"
        r"(\$\s?\d|\d[\d,.]*\s?(?:%|percent|usd|dollars|k\b))"
        r"|\bprofit\s+target\b", re.I),
     BOTH, POLICY_CANDIDATE),
    ("LOSS_REDUCTION", re.compile(
        r"\b(reduc\w*|cut\w*|limit\w*|minimi[sz]\w*|lower\w*|stop\w*)\b"
        r"(?:\W+\w+){0,4}?\W+(loss(?:es)?|bleed\w*|losing)\b", re.I),
     BOTH, POLICY_CANDIDATE),
    ("PAIRING_AND_HEDGING", re.compile(
        r"\b(pair|pairs|pairing|paired|hedge|hedges|hedging|unpaired|"
        r"residual)\b", re.I), XAVIER, POLICY_CANDIDATE),
    ("EXIT_MANAGEMENT", re.compile(
        r"\b(exit|exits|exiting|close\s+out|take\s+profit|stop[- ]loss|"
        r"holding\s+period)\b", re.I), XAVIER, POLICY_CANDIDATE),
    ("ENTRY_SELECTION", re.compile(
        r"\b(entry|entries|selection|selecting|select|edge|coverage|"
        r"opportunit\w*|markets?\s+covered|refus\w*\s+trades?)\b", re.I),
     DEREK, POLICY_CANDIDATE),
    ("EXECUTION_QUALITY", re.compile(
        r"\b(slippage|fill\s+rate|fills?|latency|execution\s+quality)\b",
        re.I), BOTH, POLICY_CANDIDATE),
    ("INVESTIGATION", re.compile(
        r"\b(investigate|look\s+into|analy[sz]e|diagnose|explain|report\s+on|"
        r"find\s+out)\b", re.I), BOTH, PRIORITY_ONLY),
]

_ACCOUNT_ID = re.compile(
    r"\b(acct[-_:][\w\-:.]*\w)|\baccount\s+(?:id\s+)?"
    r"([A-Za-z0-9]*[\d_\-][\w\-:.]*\w)", re.I)
_ALL_ACCOUNTS = re.compile(r"\b(all|every|each)\s+(?:the\s+)?accounts?\b",
                           re.I)
_VAGUE_ACCOUNT = re.compile(
    r"\b(that|this|the|my|one)\s+account\b(?!\s+(?:id\s+)?[A-Za-z0-9]*[\d_\-])",
    re.I)
_MARKETS = re.compile(
    r"\b(nba|wnba|nfl|mlb|nhl|ncaaf|ncaab|cfb|cbb|mls|epl|soccer|football|"
    r"basketball|baseball|hockey|tennis|ufc|mma|golf|boxing|cricket|"
    r"moneylines?|spreads?|totals?|over/under|player\s+props?|props?)\b",
    re.I)
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")
_NUMWORD = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "fourteen": 14, "thirty": 30}
_UNIT_S = {"day": 86400.0, "week": 7 * 86400.0, "month": 30 * 86400.0}

_QTY_WORDS = ("drawdown", "draw down", "loss", "exposure", "profit", "p&l",
              "pnl", "net", "return", "stake", "position", "fill rate",
              "slippage", "coverage", "hedge", "pair", "edge", "win rate")
_NUMBER = re.compile(
    r"(?P<cur>\$)\s?(?P<val1>\d[\d,]*(?:\.\d+)?)\s*(?P<k1>k\b)?"
    r"|(?P<val2>\d[\d,]*(?:\.\d+)?)\s*(?P<k2>k\b)?\s*"
    r"(?P<unit>%|percent\b|usd\b|dollars\b|bps\b|basis\s+points\b)", re.I)
_CMP = [
    ("<=", re.compile(r"(below|under|less\s+than|at\s+most|no\s+more\s+than|"
                      r"max(?:imum)?(?:\s+of)?|cap(?:ped)?\s+at|within|<=?)"
                      r"\s*$", re.I)),
    (">=", re.compile(r"(above|over|at\s+least|more\s+than|minimum(?:\s+of)?|"
                      r"min|>=?)\s*$", re.I)),
    ("REDUCE_BY", re.compile(r"(reduc\w*|cut\w*|lower\w*|decreas\w*)"
                             r"(?:\W+\w+){0,3}?\W+by\s*$|\bby\s*$", re.I)),
    ("TARGET", re.compile(r"(to|of|target(?:ing)?|make|earn|reach)\s*$",
                          re.I)),
]

_EXPLICIT_NO_LIMIT_INCREASE = re.compile(
    r"\b(without|not|never|no)\s+(?:\w+\s+){0,2}"
    r"(?:increas\w*|rais\w*|expand\w*|chang\w*|touch\w*)\s+(?:\w+\s+){0,2}"
    r"(?:limits?|caps?|capital)\b|\bwithin\s+(?:the\s+)?(?:current|existing|"
    r"approved)\s+(?:capital\s+|risk\s+)?limits?\b|\bno\s+(?:new|additional|"
    r"more)\s+capital\b", re.I)

Q_OBJECTIVE = ("What outcome should this directive improve -- for example "
               "drawdown, realised net, losses, entry selection (Derek), "
               "pairing/hedging or exits (Xavier), or execution quality -- "
               "and is there a number to aim for?")
Q_ACCOUNT = ("Which account should this apply to? Name the account id, or "
             "say 'all accounts'.")


def _q_horizon(target: dict | None) -> str:
    amt = ""
    if target:
        amt = " %s%s" % ("$" if target.get("unit") == "USD" else "",
                         _fmt_num(target.get("value")))
        if target.get("unit") == "%":
            amt += "%"
    return ("By what date should progress on the%s target be reviewed? (A "
            "profit target is an objective, not a guarantee, and it does not "
            "permit any increase in risk or limits.)" % amt)


def _fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return ("%d" % f) if f == int(f) else ("%g" % f)


def _utc(epoch: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)


def _day_start(epoch: float) -> float:
    d = _utc(epoch)
    return _dt.datetime(d.year, d.month, d.day,
                        tzinfo=_dt.timezone.utc).timestamp()


def _iso(epoch: float | None) -> str | None:
    return None if epoch is None else _utc(epoch).isoformat()


def parse_horizon(text: str, *, now: float) -> dict:
    """The review date / expiry the text states, or nothing. Never guessed:
    the caller labels any default it applies."""
    t = text or ""
    review = expires = None
    basis = None
    for m in _DATE.finditer(t):
        try:
            d = _dt.datetime.strptime(m.group(1), "%Y-%m-%d").replace(
                tzinfo=_dt.timezone.utc).timestamp()
        except ValueError:
            continue
        pre = t[max(0, m.start() - 20):m.start()].lower()
        if re.search(r"\b(until|through|thru|expir\w*|ends?)\s*(?:on\s+)?$",
                     pre):
            expires = d + 86400.0
        else:
            review = d
        basis = "DATE_IN_TEXT"
    m = re.search(r"\b(within|in|over|for|after|during)\s+(?:the\s+)?"
                  r"(?:next\s+)?(\d+|a|an|one|two|three|four|five|six|seven|"
                  r"eight|nine|ten|fourteen|thirty)\s+(day|week|month)s?\b",
                  t, re.I)
    if m:
        n = m.group(2).lower()
        n = int(n) if n.isdigit() else _NUMWORD.get(n, 1)
        delta = n * _UNIT_S[m.group(3).lower()]
        if review is None:
            review = now + delta
        if m.group(1).lower() == "for" and expires is None:
            expires = now + delta
        basis = basis or "RELATIVE_PERIOD_IN_TEXT"
    m = re.search(r"\b(?:by\s+)?(?:the\s+)?end\s+of\s+(?:the\s+)?"
                  r"(day|week|month)\b", t, re.I)
    if m and review is None:
        start = _day_start(now)
        unit = m.group(1).lower()
        if unit == "day":
            review = start + 86400.0
        elif unit == "week":
            wd = _utc(now).weekday()
            review = start + (7 - wd) * 86400.0
        else:
            d = _utc(now)
            y, mo = (d.year + 1, 1) if d.month == 12 else (d.year,
                                                          d.month + 1)
            review = _dt.datetime(y, mo, 1,
                                  tzinfo=_dt.timezone.utc).timestamp()
        basis = basis or "END_OF_PERIOD_IN_TEXT"
    m = re.search(r"\b(?:by|on|next|this|until|before)\s+(" +
                  "|".join(_WEEKDAYS) + r")\b", t, re.I)
    if m and review is None:
        target = _WEEKDAYS.index(m.group(1).lower())
        wd = _utc(now).weekday()
        ahead = (target - wd) % 7 or 7
        review = _day_start(now) + (ahead + 1) * 86400.0
        basis = basis or "WEEKDAY_IN_TEXT"
    if review is None:
        if re.search(r"\btomorrow\b", t, re.I):
            review, basis = _day_start(now) + 2 * 86400.0, "TOMORROW_IN_TEXT"
        elif re.search(r"\bnext\s+week\b", t, re.I):
            review, basis = now + 7 * 86400.0, "NEXT_WEEK_IN_TEXT"
        elif re.search(r"\bnext\s+month\b", t, re.I):
            review, basis = now + 30 * 86400.0, "NEXT_MONTH_IN_TEXT"
        elif re.search(r"\b(today|tonight|eod)\b", t, re.I):
            review, basis = _day_start(now) + 86400.0, "TODAY_IN_TEXT"
    return {"review_at": review, "expires_at": expires, "basis": basis}


def parse_numbers(text: str) -> list:
    """Every numerical constraint WITH A UNIT ($, %, usd, bps): the quantity
    it constrains (the nearest named quantity), the comparator and the value.
    Dates and account ids are removed first so they are never read as
    numbers."""
    t = _DATE.sub(" ", text or "")
    t = _ACCOUNT_ID.sub(" ", t)
    out = []
    for m in _NUMBER.finditer(t):
        raw = m.group("val1") or m.group("val2")
        try:
            val = float(raw.replace(",", ""))
        except (TypeError, ValueError):
            continue
        if m.group("k1") or m.group("k2"):
            val *= 1000.0
        unit = "USD" if m.group("cur") else (m.group("unit") or "").lower()
        unit = {"percent": "%", "usd": "USD", "dollars": "USD",
                "basis points": "bps"}.get(unit, unit)
        before = t[max(0, m.start() - 60):m.start()]
        after = t[m.end():m.end() + 40]
        qty = None
        best = -1
        low = before.lower()
        for w in _QTY_WORDS:
            i = low.rfind(w)
            if i > best:
                best, qty = i, w
        if qty is None:
            for w in _QTY_WORDS:
                if w in after.lower():
                    qty = w
                    break
        cmp_ = "="
        for name, rx in _CMP:
            if rx.search(before):
                cmp_ = name
                break
        out.append({"quantity": (qty or "unspecified").replace(
                        "draw down", "drawdown"),
                    "comparator": cmp_, "value": val, "unit": unit,
                    "text": m.group(0).strip()})
    return out


def translate(instruction: str, *, now: float,
              fields: dict | None = None) -> dict:
    """The structured directive a management instruction describes.

    PURE: no database, no clock beyond `now`. `fields` (the structured form)
    overrides what the text says, field by field. Returns the directive's
    content plus `missing` (the essentials not supplied) and
    `clarifying_question` (the exact question to ask, or None)."""
    fields = dict(fields or {})
    text = " ".join(str(instruction or "").split())
    objective = re.sub(r"^(?:please\s+|directive\s*:\s*|audrey\s*[,:]\s*)+",
                       "", text, flags=re.I).strip()
    kind = default_agent = cclass = None
    for k, rx, agent, cc in _KINDS:
        if rx.search(objective):
            kind, default_agent, cclass = k, agent, cc
            break

    low = objective.lower()
    named_d, named_x = "derek" in low, "xavier" in low
    agent = (BOTH if named_d and named_x else DEREK if named_d
             else XAVIER if named_x else default_agent)
    if fields.get("assigned_agent"):
        agent = str(fields["assigned_agent"]).upper()
    if kind is None and (named_d or named_x) and re.search(
            r"\b(prioriti[sz]e|focus|improve|reduce|increase)\b", low):
        kind, cclass = "AGENT_PRIORITY", POLICY_CANDIDATE

    # scope
    accounts = []
    for m in _ACCOUNT_ID.finditer(objective):
        a = m.group(1) or m.group(2)
        if a and a not in accounts:
            accounts.append(a)
    accounts_basis = "NAMED_IN_INSTRUCTION" if accounts else None
    vague_account = bool(_VAGUE_ACCOUNT.search(objective)) and not accounts
    if not accounts and _ALL_ACCOUNTS.search(objective):
        accounts, accounts_basis = ["ALL_ACCOUNTS"], "ALL_NAMED"
        vague_account = False
    if fields.get("accounts"):
        accounts = [str(a) for a in fields["accounts"]]
        accounts_basis, vague_account = "STRUCTURED_FORM", False
    if not accounts and not vague_account:
        accounts = ["ALL_ACCOUNTS_UNDER_MANAGEMENT"]
        accounts_basis = "DEFAULT_NONE_NAMED"
    markets = []
    for m in _MARKETS.finditer(objective):
        v = m.group(1).upper()
        if v not in markets:
            markets.append(v)
    markets_basis = "NAMED_IN_INSTRUCTION" if markets else \
        "DEFAULT_ALL_MARKETS_IN_MANDATE"
    if fields.get("markets"):
        markets = [str(x) for x in fields["markets"]]
        markets_basis = "STRUCTURED_FORM"
    agents = AGENTS_OF.get(agent or "", [])
    if fields.get("agents"):
        agents = [str(a).upper() for a in fields["agents"]]

    # constraints
    numbers = parse_numbers(objective)
    if isinstance(fields.get("constraints"), dict):
        for q, v in fields["constraints"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numbers.append({"quantity": str(q), "comparator": "<=",
                                "value": float(v), "unit": "",
                                "text": "structured form"})
            elif isinstance(v, dict) and isinstance(v.get("value"),
                                                    (int, float)):
                numbers.append({"quantity": str(q),
                                "comparator": str(v.get("comparator", "<=")),
                                "value": float(v["value"]),
                                "unit": str(v.get("unit", "")),
                                "text": "structured form"})
    explicit_no_increase = bool(_EXPLICIT_NO_LIMIT_INCREASE.search(objective))
    constraints = {
        "numerical": numbers,
        "standing_rules": dict(STANDING_RULES),
        "capital_limits": {
            "rule": "NO_INCREASE",
            "source": ("MANAGEMENT_INSTRUCTION" if explicit_no_increase
                       else "STANDING_RULE")},
    }

    target = None
    if kind == "PROFIT_TARGET":
        for n in numbers:
            if n["quantity"] in ("profit", "p&l", "pnl", "net", "return",
                                 "unspecified") and n["unit"] in ("USD", "%"):
                target = n
                break
        constraints["profit_target"] = {
            "target": target,
            "nature": "OBJECTIVE_NOT_A_GUARANTEE",
            "risk_expansion": "NOT_PERMITTED"}

    # horizon
    hz = parse_horizon(objective, now=now)
    review_at, expires_at = hz["review_at"], hz["expires_at"]
    horizon_basis = hz["basis"]
    for key in ("review_at", "expires_at"):
        v = fields.get(key)
        if v not in (None, ""):
            ep = _to_epoch(v)
            if ep is not None:
                if key == "review_at":
                    review_at = ep
                else:
                    expires_at = ep
                horizon_basis = "STRUCTURED_FORM"
    horizon_supplied = review_at is not None or expires_at is not None

    missing: list[str] = []
    question = None
    if not objective:
        missing.append("objective")
        question = Q_OBJECTIVE
    elif kind is None:
        missing.append("objective")
        question = Q_OBJECTIVE
    elif vague_account:
        missing.append("accounts")
        question = Q_ACCOUNT
    elif kind == "PROFIT_TARGET" and not horizon_supplied:
        missing.append("review_at")
        question = _q_horizon(target)
    if agent not in AGENTS_OF and not missing:
        missing.append("assigned_agent")
        question = ("Which agent should own this -- Derek (entry selection) "
                    "or Xavier (position management)?")

    if review_at is None and not missing:
        review_at = (expires_at if expires_at is not None
                     else now + DEFAULT_REVIEW_S)
        horizon_basis = horizon_basis or "DEFAULT_SEVEN_DAY_REVIEW"

    criteria = _criteria(kind, numbers, target, fields)
    cclass = cclass or POLICY_CANDIDATE
    return {
        "instruction": text,
        "objective": objective or None,
        "objective_kind": kind,
        "scope": {"accounts": accounts, "accounts_basis": accounts_basis,
                  "agents": agents, "markets": markets,
                  "markets_basis": markets_basis},
        "constraints": constraints,
        "acceptance_criteria": criteria,
        "review_at": review_at,
        "expires_at": expires_at,
        "horizon_basis": horizon_basis,
        "change_class": cclass,
        "required_approval": APPROVAL_FOR[cclass],
        "assigned_agent": agent if agent in AGENTS_OF else None,
        "missing": missing,
        "clarifying_question": question,
        "complete": not missing,
    }


def _to_epoch(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, _dt.datetime):
        return (v if v.tzinfo else v.replace(
            tzinfo=_dt.timezone.utc)).timestamp()
    try:
        s = str(v).strip().replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError):
        return None


def _criteria(kind, numbers, target, fields) -> list:
    if isinstance(fields.get("acceptance_criteria"), list) and \
            fields["acceptance_criteria"]:
        base = [str(c) for c in fields["acceptance_criteria"]]
    elif kind == "DRAWDOWN_REDUCTION":
        base = ["Peak-to-trough drawdown of the authoritative book "
                "(bettor_funded_economics) over the review window is lower "
                "than over the prior window of equal length, or a candidate "
                "policy shows lower drawdown in replay evaluation"]
    elif kind == "PROFIT_TARGET":
        amt = ("the target" if not target else "%s%s%s" % (
            "$" if target["unit"] == "USD" else "", _fmt_num(target["value"]),
            "%" if target["unit"] == "%" else ""))
        base = ["Realised net in bettor_funded_economics reaches %s by the "
                "review date -- measured, not promised" % amt]
    elif kind == "LOSS_REDUCTION":
        base = ["Realised losses in bettor_funded_economics over the review "
                "window are lower than over the prior equal window"]
    elif kind == "INVESTIGATION":
        base = ["A written finding citing the decision / position / audit "
                "identifiers it rests on"]
    else:
        base = ["The assigned agent's improvement task reaches a recorded "
                "outcome (candidate evaluated and approved, or closed with no "
                "change) with evidence"]
    for n in numbers:
        base.append("%s %s %s%s%s" % (
            n["quantity"], n["comparator"],
            "$" if n["unit"] == "USD" else "", _fmt_num(n["value"]),
            n["unit"] if n["unit"] not in ("USD",) else ""))
    base.append("No approved capital or risk limit is increased and no "
                "submission switch, credential or approval control changes")
    base.append("Any resulting policy change passes evaluation and the "
                "existing owner approval before it acts")
    return base


# ═════════════════════════════════════════════════════════════════════
# 3 · PERSISTENCE
# ═════════════════════════════════════════════════════════════════════

async def has_schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('management_directives') IS NOT NULL "
            "   AND to_regclass('management_directive_events') IS NOT NULL")
    except Exception:                                           # noqa: BLE001
        return False


def directive_id_for(*, requester_role: str, instruction: str, now: float,
                     conversation_id: str | None = None,
                     message_id: str | None = None) -> str:
    """Deterministic: the same instruction on the same message (or at the
    same instant) reaches the same directive, so a replay creates nothing."""
    blob = "|".join([str(requester_role), str(conversation_id or ""),
                     str(message_id or ""), "%.3f" % float(now),
                     " ".join(str(instruction or "").split())])
    return "dir-" + hashlib.sha256(blob.encode()).hexdigest()[:20]


def task_id_for(directive_id: str, agent: str, title: str | None = None) -> str:
    tid = "task-%s-%s" % (directive_id, agent.lower())
    if title:
        tid += "-" + hashlib.sha256(title.encode()).hexdigest()[:8]
    return tid


def _j(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


def _obj(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k, dflt in (("scope", {}), ("constraints", {}),
                    ("acceptance_criteria", []), ("evidence", {})):
        d[k] = _obj(d.get(k), dflt)
    d["task_ids"] = list(d.get("task_ids") or [])
    for k in ("review_at", "expires_at", "created_at", "updated_at"):
        if isinstance(d.get(k), _dt.datetime):
            d[k] = d[k].isoformat()
    return d


async def _event(conn, directive_id, *, kind, actor_role, actor_label, now,
                 from_status=None, to_status=None, detail=None) -> None:
    await conn.execute(
        "INSERT INTO management_directive_events (directive_id, at, kind, "
        " actor_role, actor_label, from_status, to_status, detail) VALUES "
        " ($1, to_timestamp($2), $3, $4, $5, $6, $7, $8::jsonb)",
        directive_id, float(now), kind, actor_role, actor_label, from_status,
        to_status, _j(detail or {}))


async def get(conn, directive_id: str) -> dict | None:
    if not await has_schema(conn):
        return None
    return _row(await conn.fetchrow(
        "SELECT * FROM management_directives WHERE directive_id=$1",
        str(directive_id)))


async def events(conn, directive_id: str, limit: int = 100) -> list:
    rows = await conn.fetch(
        "SELECT event_id, at, kind, actor_role, actor_label, from_status, "
        " to_status, detail FROM management_directive_events "
        " WHERE directive_id=$1 ORDER BY event_id LIMIT $2",
        str(directive_id), int(limit))
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = _obj(d.get("detail"), {})
        d["at"] = d["at"].isoformat() if d.get("at") else None
        out.append(d)
    return out


async def list_directives(conn, *, status: str | None = None,
                          limit: int = 50) -> list:
    if not await has_schema(conn):
        return []
    if status:
        rows = await conn.fetch(
            "SELECT * FROM management_directives WHERE status=$1 "
            " ORDER BY created_at DESC LIMIT $2", status, int(limit))
    else:
        rows = await conn.fetch(
            "SELECT * FROM management_directives ORDER BY created_at DESC "
            " LIMIT $1", int(limit))
    return [_row(r) for r in rows]


async def created_between(conn, *, start: float, end: float,
                          limit: int = 20) -> list:
    if not await has_schema(conn):
        return []
    rows = await conn.fetch(
        "SELECT * FROM management_directives WHERE created_at >= "
        " to_timestamp($1) AND created_at < to_timestamp($2) "
        " ORDER BY created_at DESC LIMIT $3", float(start), float(end),
        int(limit))
    return [_row(r) for r in rows]


async def open_draft_in(conn, conversation_id: str) -> dict | None:
    if not conversation_id or not await has_schema(conn):
        return None
    return _row(await conn.fetchrow(
        "SELECT * FROM management_directives WHERE conversation_id=$1 "
        " AND status=$2 ORDER BY created_at DESC LIMIT 1",
        conversation_id, DRAFT))


async def tasks_of(conn, directive: dict) -> list:
    """The linked tasks as the task table has them now (read-only)."""
    ids = list(directive.get("task_ids") or [])
    if not ids:
        return []
    try:
        if not await conn.fetchval(
                "SELECT to_regclass('agent_tasks') IS NOT NULL"):
            return [{"task_id": t, "status": "UNKNOWN",
                     "why": "AGENT_TASKS_TABLE_ABSENT"} for t in ids]
        rows = await conn.fetch(
            "SELECT task_id, assignee, kind, title, status, updated_at "
            "  FROM agent_tasks WHERE task_id = ANY($1::text[])", ids)
    except Exception as exc:                                    # noqa: BLE001
        return [{"task_id": t, "status": "UNKNOWN",
                 "why": "READ_FAILED:%s" % type(exc).__name__} for t in ids]
    found = {r["task_id"]: dict(r) for r in rows}
    out = []
    for t in ids:
        d = found.get(t) or {"task_id": t, "status": "UNKNOWN",
                             "why": "TASK_ROW_NOT_FOUND"}
        if isinstance(d.get("updated_at"), _dt.datetime):
            d["updated_at"] = d["updated_at"].isoformat()
        out.append(d)
    return out


def _refuse(refusal: str, **kw) -> dict:
    return dict({"ok": False, "refusal": refusal, "directive": None}, **kw)


async def create(conn, *, instruction: str, requester_role: str,
                 requester_label: str | None, now: float,
                 conversation_id: str | None = None,
                 message_id: str | None = None,
                 fields: dict | None = None,
                 directive_id: str | None = None) -> dict:
    """Record a directive from management's instruction (or the structured
    form). The requester is the AUTHENTICATED role handed in by the route.

    Returns {"ok", "refusal", "directive", "created", "tasks"}. Never raises
    on a refusal; a database failure propagates to the caller."""
    if requester_role not in CONTROL_ROLES:
        return _refuse(R_REQUIRES_OPERATOR, requester_role=requester_role)
    instruction = " ".join(str(instruction or "").split())[:4000]
    if not instruction:
        return _refuse(R_EMPTY)
    if not await has_schema(conn):
        return _refuse(R_NO_SCHEMA)
    screen = screen_authority(
        instruction + " " + (_j(fields) if fields else ""),
        questions_exempt=False)
    did = directive_id or directive_id_for(
        requester_role=requester_role, instruction=instruction, now=now,
        conversation_id=conversation_id, message_id=message_id)
    existing = await get(conn, did)
    if existing is not None:
        return {"ok": existing["status"] != REFUSED,
                "refusal": existing.get("refusal"), "directive": existing,
                "created": False, "tasks": await tasks_of(conn, existing)}

    if screen["refused"]:
        req = approval_request(screen)
        log.warning("directive refused %s: %s (role=%s)", R_PROHIBITED,
                    ",".join(screen["categories"]), requester_role)
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO management_directives (directive_id, "
                " requested_by_role, requested_by_label, conversation_id, "
                " message_id, instruction, objective, objective_kind, scope, "
                " constraints, acceptance_criteria, change_class, "
                " required_approval, assigned_agent, status, refusal, "
                " evidence, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,"
                " $6,$7,'AUTHORITY_REQUEST','{}'::jsonb,$8::jsonb,'[]'::jsonb,"
                " $9,$10,NULL,$11,$12,$13::jsonb,to_timestamp($14),"
                " to_timestamp($14))",
                did, requester_role, requester_label, conversation_id,
                message_id, instruction, instruction,
                _j({"standing_rules": STANDING_RULES}), AUTHORITY_CHANGE,
                APPROVAL_FOR[AUTHORITY_CHANGE], REFUSED, R_PROHIBITED,
                _j({"screen": screen, "approval_request": req,
                    "executed": False}), float(now))
            await _event(conn, did, kind="REFUSED_" + R_PROHIBITED,
                         actor_role=requester_role,
                         actor_label=requester_label, now=now,
                         to_status=REFUSED,
                         detail={"categories": screen["categories"],
                                 "approval_request": req})
        d = await get(conn, did)
        return {"ok": False, "refusal": R_PROHIBITED, "directive": d,
                "created": True, "tasks": [], "approval_request": req}

    t = translate(instruction, now=now, fields=fields)
    status = ACTIVE if t["complete"] else DRAFT
    evidence = {"translator": VERSION, "missing": t["missing"],
                "horizon_basis": t["horizon_basis"],
                "requester_source": "AUTHENTICATED_ROUTE_ROLE"}
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO management_directives (directive_id, "
            " requested_by_role, requested_by_label, conversation_id, "
            " message_id, instruction, objective, objective_kind, scope, "
            " constraints, acceptance_criteria, review_at, expires_at, "
            " change_class, required_approval, assigned_agent, status, "
            " clarifying_question, evidence, created_at, updated_at) VALUES "
            " ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,"
            " CASE WHEN $12::float8 IS NULL THEN NULL ELSE to_timestamp($12) "
            " END, CASE WHEN $13::float8 IS NULL THEN NULL ELSE "
            " to_timestamp($13) END, $14,$15,$16,$17,$18,$19::jsonb,"
            " to_timestamp($20), to_timestamp($20))",
            did, requester_role, requester_label, conversation_id, message_id,
            instruction, t["objective"], t["objective_kind"], _j(t["scope"]),
            _j(t["constraints"]), _j(t["acceptance_criteria"]),
            t["review_at"], t["expires_at"], t["change_class"],
            t["required_approval"], t["assigned_agent"], status,
            t["clarifying_question"], _j(evidence), float(now))
        await _event(conn, did, kind="CREATED", actor_role=requester_role,
                     actor_label=requester_label, now=now, to_status=status,
                     detail={"missing": t["missing"],
                             "clarifying_question": t["clarifying_question"]})
    tasks = []
    if status == ACTIVE:
        tasks = await _ensure_tasks(conn, did, actor_role=requester_role,
                                    actor_label=requester_label, now=now)
    d = await get(conn, did)
    return {"ok": True, "refusal": None, "directive": d, "created": True,
            "tasks": tasks}


async def _set(conn, directive: dict, *, to_status: str | None, kind: str,
               actor_role: str, actor_label: str | None, now: float,
               detail: dict | None = None, cols: dict | None = None) -> dict:
    """Move a directive (and/or rewrite its content columns) with an event,
    in one transaction."""
    cols = dict(cols or {})
    if to_status:
        cols["status"] = to_status
    sets, args = [], [directive["directive_id"]]
    for k, v in cols.items():
        args.append(v)
        if k in ("scope", "constraints", "acceptance_criteria", "evidence"):
            sets.append("%s = $%d::jsonb" % (k, len(args)))
            args[-1] = _j(v)
        elif k in ("review_at", "expires_at"):
            sets.append("%s = CASE WHEN $%d::float8 IS NULL THEN NULL ELSE "
                        "to_timestamp($%d) END" % (k, len(args), len(args)))
        elif k == "task_ids":
            sets.append("task_ids = $%d::text[]" % len(args))
        else:
            sets.append("%s = $%d" % (k, len(args)))
    args.append(float(now))
    sets.append("updated_at = to_timestamp($%d)" % len(args))
    async with conn.transaction():
        await conn.execute(
            "UPDATE management_directives SET %s WHERE directive_id = $1"
            % ", ".join(sets), *args)
        await _event(conn, directive["directive_id"], kind=kind,
                     actor_role=actor_role, actor_label=actor_label, now=now,
                     from_status=directive.get("status"),
                     to_status=to_status or directive.get("status"),
                     detail=detail)
    return await get(conn, directive["directive_id"])


async def answer_clarification(conn, *, directive_id: str, answer: str,
                               requester_role: str,
                               requester_label: str | None, now: float,
                               fields: dict | None = None) -> dict:
    """A follow-up completes a draft: the instruction and the answer are
    translated together; the draft becomes ACTIVE (tasks created) or asks the
    next question."""
    if requester_role not in CONTROL_ROLES:
        return _refuse(R_REQUIRES_OPERATOR, requester_role=requester_role)
    d = await get(conn, directive_id)
    if d is None:
        return _refuse(R_NOT_FOUND, directive_id=directive_id)
    if d["status"] != DRAFT:
        if d["status"] == ACTIVE and not d["task_ids"]:
            tasks = await _ensure_tasks(conn, directive_id,
                                        actor_role=requester_role,
                                        actor_label=requester_label, now=now)
            return {"ok": True, "refusal": None,
                    "directive": await get(conn, directive_id),
                    "created": False, "tasks": tasks}
        return _refuse(R_NOT_OPEN, directive=d)
    answer = " ".join(str(answer or "").split())[:2000]
    screen = screen_authority(answer + " " + (_j(fields) if fields else ""),
                              questions_exempt=False)
    if screen["refused"]:
        return _refuse(R_PROHIBITED, directive=d, screen=screen,
                       approval_request=approval_request(screen))
    answers = list((d.get("evidence") or {}).get("clarifications") or [])
    if answer:
        answers.append({"at": _iso(now), "answer": answer,
                        "question": d.get("clarifying_question")})
    combined = " ".join([d["instruction"]] + [a["answer"] for a in answers])
    prior_fields = (d.get("evidence") or {}).get("form_fields") or {}
    merged_fields = dict(prior_fields, **(fields or {}))
    # "all accounts" in reply to the account question
    t = translate(combined, now=now, fields=merged_fields or None)
    status = ACTIVE if t["complete"] else DRAFT
    ev = dict(d.get("evidence") or {})
    ev.update({"clarifications": answers, "missing": t["missing"],
               "horizon_basis": t["horizon_basis"]})
    if merged_fields:
        ev["form_fields"] = merged_fields
    d2 = await _set(
        conn, d, to_status=status,
        kind="CLARIFIED" if status == ACTIVE else "STILL_NEEDS_CLARIFICATION",
        actor_role=requester_role, actor_label=requester_label, now=now,
        detail={"answer": answer, "missing": t["missing"]},
        cols={"objective": t["objective"],
              "objective_kind": t["objective_kind"], "scope": t["scope"],
              "constraints": t["constraints"],
              "acceptance_criteria": t["acceptance_criteria"],
              "review_at": t["review_at"], "expires_at": t["expires_at"],
              "change_class": t["change_class"],
              "required_approval": t["required_approval"],
              "assigned_agent": t["assigned_agent"],
              "clarifying_question": t["clarifying_question"],
              "evidence": ev})
    tasks = []
    if status == ACTIVE:
        tasks = await _ensure_tasks(conn, directive_id,
                                    actor_role=requester_role,
                                    actor_label=requester_label, now=now)
        d2 = await get(conn, directive_id)
    return {"ok": True, "refusal": None, "directive": d2, "created": False,
            "tasks": tasks}


async def confirm(conn, *, directive_id: str, requester_role: str,
                  requester_label: str | None, now: float,
                  answer: str | None = None,
                  fields: dict | None = None) -> dict:
    """Operator confirmation: completes a draft with the answer / fields
    supplied, or (re)links the tasks of an ACTIVE directive that has none."""
    return await answer_clarification(
        conn, directive_id=directive_id, answer=answer or "",
        requester_role=requester_role, requester_label=requester_label,
        now=now, fields=fields)


async def cancel(conn, *, directive_id: str, reason: str,
                 requester_role: str, requester_label: str | None,
                 now: float) -> dict:
    if requester_role not in CONTROL_ROLES:
        return _refuse(R_REQUIRES_OPERATOR, requester_role=requester_role)
    d = await get(conn, directive_id)
    if d is None:
        return _refuse(R_NOT_FOUND, directive_id=directive_id)
    if d["status"] not in OPEN_STATUSES:
        return _refuse(R_NOT_OPEN, directive=d)
    task_results = []
    for tid in d["task_ids"]:
        task_results.append(await _task_event(
            conn, tid, kind="CANCELLED_WITH_DIRECTIVE",
            actor="MANAGEMENT:%s" % requester_role,
            detail={"directive_id": directive_id, "reason": reason},
            status="CANCELLED", now=now, only_if_open=True))
    d2 = await _set(conn, d, to_status=CANCELLED, kind="CANCELLED",
                    actor_role=requester_role, actor_label=requester_label,
                    now=now, detail={"reason": str(reason)[:500],
                                     "tasks": task_results})
    return {"ok": True, "refusal": None, "directive": d2, "created": False,
            "tasks": await tasks_of(conn, d2)}


async def assign(conn, *, directive_id: str, agent: str, title: str,
                 requester_role: str, requester_label: str | None,
                 now: float) -> dict:
    """An additional improvement task for Derek or Xavier under an open
    directive."""
    if requester_role not in CONTROL_ROLES:
        return _refuse(R_REQUIRES_OPERATOR, requester_role=requester_role)
    agent = str(agent or "").upper()
    if agent not in (DEREK, XAVIER):
        return _refuse(R_BAD_AGENT, agent=agent)
    title = " ".join(str(title or "").split())[:300]
    screen = screen_authority(title, questions_exempt=False)
    if screen["refused"]:
        return _refuse(R_PROHIBITED, screen=screen,
                       approval_request=approval_request(screen))
    d = await get(conn, directive_id)
    if d is None:
        return _refuse(R_NOT_FOUND, directive_id=directive_id)
    if d["status"] not in (ACTIVE, IN_PROGRESS):
        return _refuse(R_NOT_OPEN, directive=d)
    tid = task_id_for(directive_id, agent, title or "assigned")
    made = await _create_task(conn, d, agent=agent, task_id=tid,
                              title=title or None, now=now)
    if made.get("ok") and tid not in d["task_ids"]:
        d = await _set(conn, d, to_status=None, kind="TASK_ASSIGNED",
                       actor_role=requester_role, actor_label=requester_label,
                       now=now, detail={"task_id": tid, "agent": agent,
                                        "title": title},
                       cols={"task_ids": d["task_ids"] + [tid]})
    return {"ok": bool(made.get("ok")), "refusal": made.get("refusal"),
            "directive": d, "created": False, "task": made,
            "tasks": await tasks_of(conn, d)}


# ── tasks: through the core registry, guarded ───────────────────────

def _registry():
    try:
        from . import registry as R                              # noqa: F401
        return R
    except Exception:                                           # noqa: BLE001
        return None


def _task_spec(d: dict, agent: str) -> dict:
    return {"directive_id": d["directive_id"],
            "objective": d.get("objective"),
            "objective_kind": d.get("objective_kind"),
            "scope": d.get("scope"), "constraints": d.get("constraints"),
            "acceptance_criteria": d.get("acceptance_criteria"),
            "review_at": d.get("review_at"),
            "expires_at": d.get("expires_at"),
            "change_class": d.get("change_class"),
            "required_approval": d.get("required_approval"),
            "standing_rules": STANDING_RULES,
            "agent_focus": ("entry selection and sizing within the approved "
                            "limits" if agent == DEREK else
                            "position management (hold / pair / exit) within "
                            "the approved limits"),
            "requested_by_role": d.get("requested_by_role")}


async def _create_task(conn, d: dict, *, agent: str, task_id: str,
                       title: str | None, now: float) -> dict:
    title = title or ("Directive %s: %s" % (d["directive_id"],
                                            (d.get("objective") or "")[:160]))
    spec = _task_spec(d, agent)
    evidence = [{"kind": "management_directives", "id": d["directive_id"],
                 "href": "/api/command/agents/audrey/directives/%s"
                         % d["directive_id"]}]
    R = _registry()
    via = "REGISTRY"
    try:
        if R is not None and hasattr(R, "create_task"):
            await R.create_task(conn, assignee=agent,
                                created_by=TASK_CREATED_BY, kind=TASK_KIND,
                                title=title, spec=spec,
                                directive_id=d["directive_id"],
                                evidence=evidence, task_id=task_id, now=now)
        else:
            via = "DIRECT_SQL_REGISTRY_ABSENT"
            if not await conn.fetchval(
                    "SELECT to_regclass('agent_tasks') IS NOT NULL"):
                return {"ok": False, "task_id": task_id, "agent": agent,
                        "refusal": "AGENT_TASKS_TABLE_ABSENT", "via": via}
            async with conn.transaction():
                st = await conn.execute(
                    "INSERT INTO agent_tasks (task_id, assignee, created_by, "
                    " kind, title, spec, status, directive_id, evidence, "
                    " outcome, created_at, updated_at) VALUES ($1,$2,$3,$4,"
                    " $5,$6::jsonb,'OPEN',$7,$8::jsonb,'{}'::jsonb,"
                    " to_timestamp($9),to_timestamp($9)) "
                    " ON CONFLICT (task_id) DO NOTHING",
                    task_id, agent, TASK_CREATED_BY, TASK_KIND, title,
                    _j(spec), d["directive_id"], _j(evidence), float(now))
                if str(st).endswith(" 1"):
                    await conn.execute(
                        "INSERT INTO agent_task_events (task_id, at, kind, "
                        " actor, detail) VALUES ($1, to_timestamp($2), "
                        " 'CREATED', $3, $4::jsonb)",
                        task_id, float(now), TASK_CREATED_BY,
                        _j({"directive_id": d["directive_id"]}))
    except Exception as exc:                                    # noqa: BLE001
        log.warning("directive task %s not created: %s", task_id,
                    type(exc).__name__)
        return {"ok": False, "task_id": task_id, "agent": agent,
                "refusal": "TASK_CREATE_FAILED:%s" % type(exc).__name__,
                "via": via}
    try:
        status = await conn.fetchval(
            "SELECT status FROM agent_tasks WHERE task_id=$1", task_id)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "task_id": task_id, "agent": agent,
                "refusal": "TASK_READBACK_FAILED:%s" % type(exc).__name__,
                "via": via}
    if status is None:
        return {"ok": False, "task_id": task_id, "agent": agent,
                "refusal": "TASK_NOT_FOUND_AFTER_CREATE", "via": via}
    return {"ok": True, "task_id": task_id, "agent": agent,
            "status": status, "via": via, "refusal": None}


async def _task_event(conn, task_id: str, *, kind: str, actor: str,
                      detail: dict, status: str | None, now: float,
                      only_if_open: bool = False) -> dict:
    try:
        if only_if_open:
            cur = await conn.fetchval(
                "SELECT status FROM agent_tasks WHERE task_id=$1", task_id)
            if cur is None or cur in ("REJECTED", "RELEASED", "ROLLED_BACK",
                                      "CLOSED_NO_CHANGE", "CANCELLED"):
                return {"task_id": task_id, "ok": False,
                        "why": "TASK_ALREADY_TERMINAL_OR_ABSENT",
                        "status": cur}
        R = _registry()
        if R is not None and hasattr(R, "task_event"):
            await R.task_event(conn, task_id, kind=kind, actor=actor,
                               detail=detail, status=status, now=now)
        else:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agent_task_events (task_id, at, kind, actor, "
                    " detail) VALUES ($1, to_timestamp($2), $3, $4, $5::jsonb)",
                    task_id, float(now), kind, actor, _j(detail))
                if status:
                    await conn.execute(
                        "UPDATE agent_tasks SET status=$2, "
                        " updated_at=to_timestamp($3) WHERE task_id=$1",
                        task_id, status, float(now))
        return {"task_id": task_id, "ok": True, "status": status}
    except Exception as exc:                                    # noqa: BLE001
        return {"task_id": task_id, "ok": False,
                "why": "TASK_EVENT_FAILED:%s" % type(exc).__name__}


async def _ensure_tasks(conn, directive_id: str, *, actor_role: str,
                        actor_label: str | None, now: float) -> list:
    """One improvement task per assigned agent, deterministic ids, linked.
    A task that cannot be created is recorded on the directive by name and
    retried by `monitor`; the directive never claims a task it lacks."""
    d = await get(conn, directive_id)
    if d is None or d["status"] not in (ACTIVE, IN_PROGRESS):
        return []
    results = []
    linked = list(d["task_ids"])
    for agent in AGENTS_OF.get(d.get("assigned_agent") or "", []):
        tid = task_id_for(directive_id, agent)
        r = await _create_task(conn, d, agent=agent, task_id=tid, title=None,
                               now=now)
        results.append(r)
        if r.get("ok") and tid not in linked:
            linked.append(tid)
    failures = [r for r in results if not r.get("ok")]
    if linked != d["task_ids"] or failures:
        ev = dict(d.get("evidence") or {})
        ev["task_creation"] = results
        await _set(conn, d, to_status=None,
                   kind="TASKS_LINKED" if not failures else
                   "TASK_CREATION_INCOMPLETE",
                   actor_role=actor_role, actor_label=actor_label, now=now,
                   detail={"results": results},
                   cols={"task_ids": linked, "evidence": ev})
    return results


# ═════════════════════════════════════════════════════════════════════
# 4 · MONITORING
# ═════════════════════════════════════════════════════════════════════

TASK_WORKING = ("IN_PROGRESS", "WAITING", "CANDIDATE_READY", "EVALUATING",
                "APPROVAL_READY", "APPROVED")
TASK_CHANGE_ADOPTED = ("RELEASED",)
TASK_NO_CHANGE = ("REJECTED", "CLOSED_NO_CHANGE", "ROLLED_BACK", "CANCELLED")
TASK_TERMINAL = TASK_CHANGE_ADOPTED + TASK_NO_CHANGE


def derived_status(directive: dict, tasks: list, *, now: float) -> tuple:
    """(status, reason, outcome) the directive should have given its linked
    tasks and the clock. Pure."""
    st = directive.get("status")
    if st not in OPEN_STATUSES:
        return st, None, None
    exp = _to_epoch(directive.get("expires_at"))
    if exp is not None and exp <= now:
        return EXPIRED, "EXPIRES_AT_PASSED", None
    if st == DRAFT or not tasks:
        return st, None, None
    statuses = [t.get("status") for t in tasks]
    if any(s == "UNKNOWN" for s in statuses):
        return st, None, None
    if all(s in TASK_TERMINAL for s in statuses):
        adopted = [t["task_id"] for t in tasks
                   if t.get("status") in TASK_CHANGE_ADOPTED]
        return COMPLETED, "ALL_LINKED_TASKS_REACHED_AN_OUTCOME", {
            "result": ("CHANGE_RELEASED_THROUGH_APPROVAL" if adopted
                       else "NO_CHANGE_ADOPTED"),
            "released_tasks": adopted,
            "task_outcomes": {t["task_id"]: t.get("status") for t in tasks}}
    if st == ACTIVE and any(s in TASK_WORKING + TASK_TERMINAL
                            for s in statuses):
        return IN_PROGRESS, "A_LINKED_TASK_IS_UNDER_WAY", None
    return st, None, None


async def monitor(conn, *, now: float, actor_role: str = "system",
                  limit: int = 200) -> dict:
    """Bring every open directive's status in line with its linked tasks'
    outcomes and the clock; retry task creation for an ACTIVE directive that
    has none; flag a review that has come due (once). Idempotent."""
    out = {"checked": 0, "transitions": [], "review_due": [],
           "tasks_retried": []}
    if not await has_schema(conn):
        out["refusal"] = R_NO_SCHEMA
        return out
    rows = await conn.fetch(
        "SELECT * FROM management_directives WHERE status = ANY($1::text[]) "
        " ORDER BY created_at LIMIT $2", list(OPEN_STATUSES), int(limit))
    for r in rows:
        d = _row(r)
        out["checked"] += 1
        if d["status"] == ACTIVE and not d["task_ids"]:
            got = await _ensure_tasks(conn, d["directive_id"],
                                      actor_role=actor_role,
                                      actor_label="directive monitor",
                                      now=now)
            out["tasks_retried"].append({"directive_id": d["directive_id"],
                                         "results": got})
            d = await get(conn, d["directive_id"])
        tasks = await tasks_of(conn, d)
        to, reason, outcome = derived_status(d, tasks, now=now)
        if to != d["status"]:
            ev = dict(d.get("evidence") or {})
            if outcome:
                ev["outcome"] = outcome
            ev["last_task_read"] = {t["task_id"]: t.get("status")
                                    for t in tasks}
            await _set(conn, d, to_status=to, kind="STATUS_FROM_TASKS"
                       if to != EXPIRED else "EXPIRED",
                       actor_role=actor_role, actor_label="directive monitor",
                       now=now, detail={"reason": reason, "tasks": {
                           t["task_id"]: t.get("status") for t in tasks},
                           "outcome": outcome},
                       cols={"evidence": ev})
            out["transitions"].append({"directive_id": d["directive_id"],
                                       "from": d["status"], "to": to,
                                       "reason": reason})
            continue
        rev = _to_epoch(d.get("review_at"))
        if rev is not None and rev <= now and d["status"] != DRAFT \
                and not (d.get("evidence") or {}).get("review_due_at"):
            ev = dict(d.get("evidence") or {})
            ev["review_due_at"] = _iso(now)
            await _set(conn, d, to_status=None, kind="REVIEW_DUE",
                       actor_role=actor_role, actor_label="directive monitor",
                       now=now, detail={"review_at": d.get("review_at")},
                       cols={"evidence": ev})
            out["review_due"].append(d["directive_id"])
    return out


def describe() -> dict:
    return {"version": VERSION, "statuses": list(STATUSES),
            "change_classes": [PRIORITY_ONLY, POLICY_CANDIDATE,
                               AUTHORITY_CHANGE],
            "control_roles": sorted(CONTROL_ROLES),
            "standing_rules": STANDING_RULES,
            "prohibited_categories": sorted({n for n, _ in _PROHIBITED}),
            "refusal": R_PROHIBITED,
            "grants_authority": False, "submits_orders": False}
