"""AGENT CITATION / CLAIM INTEGRITY -- THE PRE-PUBLICATION VERIFIER (LAB-B).

PURE: no database, no network, no clock, and nothing imported from an order,
venue, execution, funded or paper module (tests/test_lab_citation_integrity
_import_guard.py proves both directions).

THE DEFECT (owner, 2026-10-04). The agent chat (agents/persona_chat.py) numbers
every fact [F#], keeps a model reply only when every figure in it is held by
SOME fact (`ungrounded_numbers`), discards a reply that claims an action, and
falls back to a records-only answer. None of that proves that the [F#] written
on a sentence supports THAT sentence: the owner has read directionally correct
sentences citing the wrong fact id -- "149.3 seconds stale" citing the older
75.2-second review, the account's cash from F143 cited as F139, a review
called CURRENT whose cited fact is SUPERSEDED, a valid record of the wrong
position. A figure that exists somewhere in the fact list passes the existing
check wherever it is cited.

WHAT THIS CHECKS, sentence by sentence (`verify`)
  1. The answer is split into sentences. A verbatim quotation of a fact's text
     (what the records-only composer writes: "<fact text> [F#]") is one unit
     even when the fact's text has sentences of its own (`mask_quotations`).
  2. Each sentence's citation groups are parsed ("[F3] [F4]", "[F3, F4]").
     Material written before a group is BOUND to that group (a citation closes
     its clause); material after the last group binds to the last group. A
     citation written just BEFORE its clause is accepted too: an item is
     supported if its own group or the immediately preceding group in the
     same sentence supports it.
  3. The material claims are extracted: figures (with their written
     precision, $ / % / pp / contract units and the measure word beside them
     -- cash, reserved, available, equity, realised / unrealised P&L, fees,
     price, probability, edge, minimum, contracts, cost, age, limit), record
     ids (position / decision / review / challenge / estimate / session ids,
     policy versions), timestamps and clock times, upper-case state and
     refusal codes (HOLD, ENTER, REFUSE, FILLED, HOLD_ON_STALE_PROBABILITY,
     ...), CURRENT / SUPERSEDED assertions about a review, decision or state,
     and "<Agent>'s decision / review / ..." attributions. Each sentence is
     also labelled with the claim categories it touches (P&L, equity, cash,
     price, fill, quantity, probability, freshness, timestamp, management
     state, review status, order state, policy / version, settlement, another
     agent's decision, position ownership, audit state) -- labels for the
     scorecard, not a verdict.
  4. Every item is checked against the facts its citation names.

TOLERANCE (stated, and the same on both sides of the comparison)
  * A figure is supported when a cited fact holds the same number, or a
    number that ROUNDS to it at the precision the sentence wrote (half-up or
    half-even): "149.3" is supported by a fact's "149.300"; "$97,958" by
    "$97,958.35"; never "$98k" by "$97,958.35" (no rounding to significant
    figures, no k / M scaling). A figure written with a percent unit (%, pp,
    percent, cents) may also match the fact's number x100 or /100 (0.59 <->
    59%). Signs are ignored ("-$8.42" and "a loss of $8.42" are the same
    record figure).
  * The measure beside a figure is read the same way in the sentence and in
    the fact (the words just after it, else just before it, never across
    punctuation or another figure). A cited fact whose matching number is
    attributed to a DIFFERENT measure of the same family (cash vs available
    vs reserved ...; price vs probability vs edge vs minimum; contracts vs
    cost; age vs limit) does not support it: that is a coincidental number,
    the cross-record substitution the owner saw. Where either side names no
    measure, the number alone decides.
  * A bare 0, 1 or 2 (no $, decimal, or unit) is a counting word, not a
    figure (the existing checker admits them for every answer), and a figure
    the user's own question states is an echo of the question.
  * An id is supported when the cited fact's record id or text contains it; a
    timestamp when a cited fact holds the same date (and the same time to the
    precision written); a code when the cited fact holds it as a word or as
    a run of a longer code's underscore-separated parts (HOLD ->
    HOLD_ON_STALE_PROBABILITY, BUY_SHORT -> ORDER_INTENT_BUY_SHORT).
  * Status: a fact is STALE-marked when its text says SUPERSEDED or
    HISTORICAL (or its field is superseded_review), CURRENT-marked when it
    says CURRENT. A sentence asserting a current state ("current", "latest",
    "now", "still" beside review / decision / recommendation / management /
    state / status / call / position / order, or the word CURRENT) whose
    citation is STALE-marked and not CURRENT-marked cites stale state.

VERDICTS (one per material sentence; the most severe of its items)
  PASS                  every material item is supported by its citation
  NO_CITATION           a figure / id / timestamp / code with no [F#] at all
  INSUFFICIENT_SUPPORT  cited, and no fact in the answer's fact list holds it
  WRONG_FACT            cited, the cited fact does not hold it, ANOTHER fact
                        does (cross-record substitution) -- or the sentence
                        calls a CURRENT-marked record superseded
  STALE_STATE_CITATION  the citation is a SUPERSEDED / HISTORICAL record
                        while the sentence asserts the current state, or the
                        material sits in the current record of the same
                        entity while a superseded one is cited
  ENTITY_MISMATCH       the cited record is a valid record of a DIFFERENT
                        entity of the same kind (another position, review,
                        decision ...) than the one the sentence names, or the
                        sentence attributes the cited decision to an agent
                        whose record it is not
  Severity: ENTITY_MISMATCH > STALE_STATE_CITATION > WRONG_FACT >
  INSUFFICIENT_SUPPORT (NO_CITATION applies only to an uncited sentence).

ACTIONS (`gate`): never silently publish a known unsupported citation
  PUBLISHED_VERIFIED         the sentence passed
  REPAIRED_RECITED           deterministic repair: EXACTLY ONE fact in the
                             list supports the material (all of the clause's
                             items, else all of its failing items while the
                             original citation keeps supporting the rest) ->
                             that fact REPLACES a citation that holds
                             nothing the clause says (always for a stale
                             record cited for the present state), or is
                             cited BESIDE one that still holds part of it;
                             the repaired sentence is verified again and
                             must PASS. Never a model's guess.
  FELL_BACK_TO_RECORDS_ONLY  a MODEL reply with an unrepairable known
                             unsupported citation (WRONG_FACT,
                             STALE_STATE_CITATION, ENTITY_MISMATCH,
                             INSUFFICIENT_SUPPORT) is discarded; the
                             records-only answer is published with the
                             integrity reason named
  STATED_UNSUPPORTED         the sentence is kept and followed by an explicit
                             "(Integrity check: ...)" statement that the
                             evidence does not support it: a records-only
                             answer or an interrupted partial (nothing
                             further to fall back to), or a model reply whose
                             only remaining gap is an uncited sentence
                             (NO_CITATION) that no single fact could be cited
                             for

WHAT IT IS NOT. A judge of reasoning or tone; a check of spelled-out numbers
("seven refusals") or of lower-case action verbs ("we hold"), which stay with
the existing checks; a trading input. It has no authority over anything but
the chat prose it verifies.
"""

from __future__ import annotations

import hashlib
import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation

VERSION = "CITATION_INTEGRITY_V1"
AUTHORITY = "SHADOW_RESEARCH_ONLY"

PASS = "PASS"
WRONG_FACT = "WRONG_FACT"
INSUFFICIENT_SUPPORT = "INSUFFICIENT_SUPPORT"
STALE_STATE_CITATION = "STALE_STATE_CITATION"
ENTITY_MISMATCH = "ENTITY_MISMATCH"
NO_CITATION = "NO_CITATION"
VERDICTS = (PASS, WRONG_FACT, INSUFFICIENT_SUPPORT, STALE_STATE_CITATION,
            ENTITY_MISMATCH, NO_CITATION)
FAILURES = VERDICTS[1:]
#: the wrong-supporting-fact family PM question E asks about
WRONG_SUPPORT = (WRONG_FACT, STALE_STATE_CITATION, ENTITY_MISMATCH)
_SEVERITY = {ENTITY_MISMATCH: 5, STALE_STATE_CITATION: 4, WRONG_FACT: 3,
             INSUFFICIENT_SUPPORT: 2, NO_CITATION: 1, PASS: 0}

A_VERIFIED = "PUBLISHED_VERIFIED"
A_REPAIRED = "REPAIRED_RECITED"
A_FALLBACK = "FELL_BACK_TO_RECORDS_ONLY"
A_STATED = "STATED_UNSUPPORTED"
ACTIONS = (A_VERIFIED, A_REPAIRED, A_FALLBACK, A_STATED)

COMPOSER_MODEL = "MODEL"
COMPOSER_RECORDS = "RECORDS_ONLY"

ST_MODEL = "MODEL_REPLY"
ST_RECORDS = "RECORDS_ONLY_ANSWER"
ST_PARTIAL = "INTERRUPTED_PARTIAL"
STAGES = (ST_MODEL, ST_RECORDS, ST_PARTIAL)

#: the profile the production retrospective SQL ports (figures and status
#: only, measure-blind): `verify(profile=PROFILE_RETRO)` is its reference
PROFILE_FULL = "FULL"
PROFILE_RETRO = "RETRO_PORT"

K_NUMBER, K_ID, K_TIME, K_CODE, K_STATUS, K_AGENT = (
    "NUMBER", "ID", "TIMESTAMP", "CODE", "STATUS", "AGENT_ATTRIBUTION")

#: the statement that follows a sentence the evidence does not support
NOTE_PREFIX = "(Integrity check:"
NOTE_PHRASE = {
    WRONG_FACT: "the record it cites does not hold what this sentence "
                "states; another record does",
    INSUFFICIENT_SUPPORT: "no record in the evidence holds what this "
                          "sentence states",
    STALE_STATE_CITATION: "it cites a superseded record for the present "
                          "state",
    ENTITY_MISMATCH: "the record it cites belongs to a different entity "
                     "than the one it names",
    NO_CITATION: "it states a figure or record without citing the "
                 "evidence",
}
#: what the published records-only answer says when a model reply is
#: discarded by this gate
FALLBACK_WHY = "a citation did not support its sentence"

# ═════════════════════════════════════════════════════════════════════
# 1 · TOKENS (the same patterns are ported to the retrospective SQL)
# ═════════════════════════════════════════════════════════════════════

_WS = "[ \t\r\n\f\v]"
#: sentence boundary: terminal punctuation, whitespace, and a capital /
#: digit / quote / $ / opening parenthesis -- or a line break
BOUNDARY = re.compile(r"(?<=[.!?])" + _WS + r"+(?=[A-Z0-9\"\u201c$(])|\n+")
#: one citation group: [F3] [F4] / [F3][F4] / [F3, F4]
CITE_GROUP = re.compile(r"(?:\[F\d+(?:[ ]*[,;][ ]*F\d+)*\][ \t,]*)+")
_FID = re.compile(r"F(\d+)")
_ANY_CITE = re.compile(r"\[F\d+(?:[ ]*[,;][ ]*F\d+)*\]")
#: a figure: the persona chat's own pattern (persona_chat._NUMBER) with the
#: dollar sign and the unit beside it captured
UNIT = (r"(?:%|pp\b|percent(?:age)?|cents?\b|\u00a2|seconds?\b|secs?\b|"
        r"minutes?\b|mins?\b|contracts?\b|s\b|x\b)")
NUMBER = re.compile(r"(?<![\w.:])[-\u2212]?(\$?)(\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
                    r"\d+(?:\.\d+)?|\.\d+)(?!:\d)(" + _WS + r"?" + UNIT +
                    r")?")
_PERCENTISH = re.compile(r"%|pp|percent|cent|\u00a2")
_ISO = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?)"
                  r"(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2}|\s?UTC)?(?!\d)")
_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}:\d{2}(?::\d{2})?)(?:\.\d+)?(?![\d:])")
_IDTOK = re.compile(r"(?<![\w:.\-/#])[A-Za-z][A-Za-z0-9]*(?:[-_:.#/][A-Za-z0-9]+)+")
_MULTI_CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
STATE_WORDS = ("HOLD", "EXIT", "REDUCE", "HEDGE", "ENTER", "REFUSE",
               "REFUSED", "FILLED", "RESTING", "CANCELLED", "CANCELED",
               "SETTLED", "OPEN", "CLOSED", "UPHELD", "REJECTED",
               "OVERTURNED", "STALE", "FRESH", "PROPOSED", "ACTIVE",
               "INACTIVE", "ACKNOWLEDGED", "EXPIRED", "RECONCILES", "SKIP",
               "SPLIT", "WAIT", "PENDING", "APPROVED", "DISPUTE", "DISPUTED")
_STATE_CODE = re.compile(r"\b(?:%s)\b" % "|".join(STATE_WORDS))
#: a proposal, hypothesis or conditional, not an assertion about a record:
#: "That review should either reconfirm HOLD or select a different action",
#: "The measurable outcome is a stored review for <group>" (the production
#: retrospective, research runs 37234456833..37234967005: action words and
#: subject ids in such sentences are vocabulary, not claims)
_MODAL = re.compile(r"\b(?:should|would|could|might|may|if|once|whether|"
                    r"propose|proposed|proposal|suggest|suggests|suggested|"
                    r"hypothesis|hypothesi[sz]e|ought|let's|let\s+us|"
                    r"measurable\s+outcome|next\s+(?:action|step))\b|"
                    # a labelled proposal ("Repair: ...", "Action: ...") or
                    # an imperative opening ("Record the threshold on ...")
                    r"\b(?:action|repair|proposed\s+repair|recommendation|"
                    r"ask)\s*\**\s*:|^\W*(?:produce|record|re-?run|run|"
                    r"add|log|store|track|fix|attach|capture|persist)\b",
                    re.I)
_STATUS_NOUN = (r"\b(?:reviews?|decisions?|recommendations?|management|"
                r"states?|status|calls?|positions?|orders?)\b")
_CURRENT_WORD = re.compile(r"\b(?:current|currently|latest|newest|now|live|"
                           r"still)\b", re.I)
_SUPERSEDED_WORD = re.compile(r"\b(?:superseded|older|previous|earlier|"
                              r"prior|outdated|historical)\b", re.I)
_STATUS_NOUN_RX = re.compile(_STATUS_NOUN, re.I)
_UPPER_CURRENT = re.compile(r"\bCURRENT\b")
_UPPER_SUPERSEDED = re.compile(r"\bSUPERSEDED\b")
_UPPER_HISTORICAL = re.compile(r"\bHISTORICAL\b")
AGENT_NAMES = ("Derek", "Xavier", "Audrey", "Karen", "Eddie", "Scout",
               "Allie")
_ATTRIB = re.compile(
    r"\b(%s)['\u2019]s\s+(?:[A-Za-z\-]+\s+){0,2}?(decisions?|reviews?|calls?|"
    r"estimates?|challenges?|audits?|recommendations?|entry|entries|hedges?|"
    r"management|orders?|thesis)\b" % "|".join(AGENT_NAMES), re.I)
#: whose records a source holds (None: no single agent)
SOURCE_OWNER = (("derek_", "DEREK"), ("bettor_xavier_", "XAVIER"),
                ("paper_xavier_", "XAVIER"),
                ("bettor_standing_order_plans", "XAVIER"),
                ("audrey_", "AUDREY"), ("karen_", "KAREN"),
                ("eddie_", "EDDIE"), ("scout_", "SCOUT"),
                ("allie_", "ALLIE"))

#: measure words -> canonical measure; a measure belongs to one family
MEASURE_WORDS = {
    "cash": "CASH", "reserved": "RESERVED", "available": "AVAILABLE",
    "equity": "EQUITY", "realised": "REALIZED_PNL",
    "realized": "REALIZED_PNL", "unrealised": "UNREALIZED_PNL",
    "unrealized": "UNREALIZED_PNL", "fee": "FEES", "fees": "FEES",
    "profit": "PROFIT", "ev": "PROFIT",
    "price": "PRICE", "prices": "PRICE", "priced": "PRICE", "vwap": "PRICE",
    "limit": ("PRICE", "LIMIT"),
    "probability": "PROBABILITY", "probabilities": "PROBABILITY",
    "implied": "PROBABILITY", "edge": "EDGE", "minimum": "THRESHOLD",
    "threshold": "THRESHOLD",
    "contracts": "QUANTITY", "contract": "QUANTITY", "qty": "QUANTITY",
    "quantity": "QUANTITY", "shares": "QUANTITY",
    "cost": "COST", "costs": "COST", "stake": "COST", "collateral": "COST",
    "spent": "COST",
    "old": "AGE", "age": "AGE", "aged": "AGE", "stale": "AGE",
}
_FIELD_ONLY_WORDS = {"p": "PROBABILITY", "pp": "EDGE"}
FAMILY = {"CASH": "ACCOUNT", "RESERVED": "ACCOUNT", "AVAILABLE": "ACCOUNT",
          "EQUITY": "ACCOUNT", "REALIZED_PNL": "ACCOUNT",
          "UNREALIZED_PNL": "ACCOUNT", "FEES": "ACCOUNT",
          "PROFIT": "ACCOUNT", "PRICE": "MARKET", "PROBABILITY": "MARKET",
          "EDGE": "MARKET", "THRESHOLD": "MARKET", "QUANTITY": "SIZE",
          "COST": "SIZE", "AGE": "TIME", "LIMIT": "TIME"}
_SKIP_WORDS = frozenset(("in", "of", "at", "the", "a", "an", "is", "was",
                         "are", "were", "to", "for", "its", "his", "her",
                         "our", "their", "this", "that", "on", "per", "s"))
#: a clause joint: a measure word beyond it belongs to the next figure
#: ("cash of $499,404.86 and reserved deltas of $98.92": production
#: research run 37234456833, where "reserved" was read as the first figure's)
_JOINT_WORDS = frozenset(("and", "or", "but", "while", "whereas", "versus",
                          "vs", "against", "plus", "then", "with", "than",
                          # a qualifier, not the figure's name: "$180 before
                          # fees" is a profit, not a fee
                          "before", "after", "excluding", "including",
                          "less", "minus", "net"))
_STOP_PUNCT = frozenset("(),;:[]{}\u2014\u2013=")
_WORDTOK = re.compile(r"[A-Za-z&]+|\d[\d,.]*|\S")

CATEGORIES = (
    ("pnl", re.compile(r"\b(?:p&l|pnl|reali[sz]ed|unreali[sz]ed|profit|"
                       r"loss|booked)\b", re.I)),
    ("equity", re.compile(r"\bequity\b", re.I)),
    ("cash", re.compile(r"\b(?:cash|reserved|available|balances?)\b", re.I)),
    ("price", re.compile(r"\b(?:price[sd]?|vwap|limit)\b", re.I)),
    ("fill", re.compile(r"\b(?:fill|fills|filled|unfilled)\b", re.I)),
    ("quantity", re.compile(r"\b(?:contracts?|qty|quantity|shares)\b", re.I)),
    ("probability", re.compile(r"\b(?:probabilit\w*|implied|blended)\b",
                               re.I)),
    ("freshness", re.compile(r"\b(?:fresh\w*|stale|old|age|seconds?)\b",
                             re.I)),
    ("management_state", re.compile(r"\b(?:hold|exit|reduce|hedge|"
                                    r"manag\w*|recommend\w*)\b", re.I)),
    ("review_status", re.compile(r"\b(?:current|superseded|latest|older|"
                                 r"previous)\b", re.I)),
    ("order_state", re.compile(r"\b(?:orders?|resting|cancel\w*|"
                               r"acknowledged)\b", re.I)),
    ("policy_version", re.compile(r"\b(?:polic\w*|version|threshold|"
                                  r"minimum)\b|_v\d", re.I)),
    ("settlement", re.compile(r"\bsettle\w*\b", re.I)),
    ("agent_decision", re.compile(r"\b(?:%s)\b" % "|".join(AGENT_NAMES),
                                  re.I)),
    ("position_ownership", re.compile(r"\b(?:owns?|manages?|holds?|held|"
                                      r"positions?)\b", re.I)),
    ("audit_state", re.compile(r"\b(?:audit\w*|reconcil\w*)\b", re.I)),
)


def sha256(text: str) -> str:
    return hashlib.sha256(" ".join(str(text or "").split()).encode(
        "utf-8")).hexdigest()


def _dec(s) -> Decimal | None:
    try:
        return Decimal(str(s).replace(",", "").replace("\u2212", "-"))
    except (InvalidOperation, ValueError):
        return None


def _decimals(s: str) -> int:
    s = str(s)
    return len(s.split(".")[1]) if "." in s else 0


# ═════════════════════════════════════════════════════════════════════
# 2 · MEASURES (read identically in the sentence and in the fact)
# ═════════════════════════════════════════════════════════════════════

def _measure_of_word(w: str, field: bool = False) -> tuple:
    m = MEASURE_WORDS.get(w.lower())
    if m is None and field:
        m = _FIELD_ONLY_WORDS.get(w.lower())
    if m is None:
        return ()
    return m if isinstance(m, tuple) else (m,)


def _is_num_tok(t: str) -> bool:
    return bool(t) and t[0].isdigit()


def measures_near(text: str, start: int, end: int) -> frozenset:
    """The measure of the figure at text[start:end]: the NEAREST measure word
    (the unit the figure carries counts as nearest; then up to three words
    after it and three before it, small words not counted, never across
    punctuation, a clause joint or another figure, and never a word that
    belongs to the figure before -- "2,000 contracts for $1,000": contracts
    is 2,000's). A tie unites both sides. "Available of $499,305.94 equals
    cash minus reserved" is AVAILABLE (production research run 37234456833
    read it as cash when the words after always won)."""
    unit: set = set()
    for w in re.findall(r"[A-Za-z]+", text[start:end]):
        unit.update(_measure_of_word(w))     # the unit the figure carries
    if unit:
        return frozenset(unit)
    after_d, after_m = None, set()
    n = 0
    for t in _WORDTOK.findall(text[end:end + 80]):
        if t in _STOP_PUNCT or _is_num_tok(t) or t == "$":
            break
        if not t[0].isalpha():
            continue
        if t.lower() in _JOINT_WORDS:
            break
        if t.lower() in _SKIP_WORDS:
            continue
        n += 1
        m = _measure_of_word(t)
        if m:
            after_d, after_m = n, set(m)
            break
        if n >= 3:
            break
    before_d, before_m = None, set()
    before = _WORDTOK.findall(text[max(0, start - 80):start])
    n = 0
    for i in range(len(before) - 1, -1, -1):
        t = before[i]
        if t in _STOP_PUNCT or _is_num_tok(t):
            break
        if t == "$" or not t[0].isalpha():
            continue
        if i > 0 and _is_num_tok(before[i - 1]):
            break                       # the previous figure's own word
        if t.lower() in _JOINT_WORDS:
            break
        if t.lower() in _SKIP_WORDS:
            continue
        n += 1
        m = _measure_of_word(t)
        if m:
            before_d, before_m = n, set(m)
            break
        if n >= 3:
            break
    if after_d is None and before_d is None:
        return frozenset()
    if before_d is None or (after_d is not None and after_d < before_d):
        return frozenset(after_m)
    if after_d is None or before_d < after_d:
        return frozenset(before_m)
    return frozenset(after_m | before_m)


def field_measures(field: str) -> frozenset:
    out: set = set()
    for w in re.split(r"[^A-Za-z]+", str(field or "")):
        if w:
            out.update(_measure_of_word(w, field=True))
    return frozenset(out)


def measures_compatible(a: frozenset, b: frozenset) -> bool:
    """Either side unnamed, a common measure, or no common family."""
    if not a or not b or a & b:
        return True
    return not ({FAMILY[x] for x in a} & {FAMILY[x] for x in b})


# ═════════════════════════════════════════════════════════════════════
# 3 · THE FACT INDEX
# ═════════════════════════════════════════════════════════════════════

def _id_kind(tok: str) -> str:
    head = re.split(r"[-_:.#/]", tok, maxsplit=1)[0]
    return re.sub(r"\d+", "", head.lower()) or head.lower()


def _looks_like_id(tok: str, known: set) -> bool:
    if not re.search(r"\d", tok):
        return False
    if tok.lower() in known:
        return True
    if ":" in tok or len(re.findall(r"[-_:.#/]", tok)) >= 2:
        return True
    return any(len(p) >= 6 and re.search(r"[A-Za-z]", p) and
               re.search(r"\d", p) for p in re.split(r"[-_:.#/]", tok))


def _timestamps(text: str) -> list:
    out = []
    for m in _ISO.finditer(text):
        out.append((m.group(1), m.group(2)))
    return out


class Fact:
    __slots__ = ("fid", "source", "record_id", "field", "value", "text",
                 "tokens", "raw", "ids", "times", "clocks", "codes",
                 "stale", "current", "owner", "blob")

    def __init__(self, f: dict):
        self.fid = str(f.get("fact_id"))
        self.source = str(f.get("source") or "")
        self.record_id = str(f.get("record_id") or "")
        self.field = str(f.get("field") or "")
        self.value = f.get("value")
        self.text = str(f.get("text") or "")
        self.blob = " ".join((self.text, self.record_id))
        #: figures in the fact's text with their measures, plus its numeric
        #: value with the measure its field names
        self.tokens: list = []
        for m in NUMBER.finditer(self.text):
            d = _dec(m.group(2))
            if d is not None:
                self.tokens.append((d, measures_near(
                    self.text, m.start(), m.end())))
        v = self.value
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            d = _dec(repr(v) if isinstance(v, float) else v)
            if d is not None:
                self.tokens.append((d, field_measures(self.field)))
        #: every figure the existing checker admits from this fact (its
        #: text, its numeric value, its record id) -- measure-blind
        self.raw: list = [d for d, _m in self.tokens]
        for m in NUMBER.finditer(self.record_id):
            d = _dec(m.group(2))
            if d is not None:
                self.raw.append(d)
        self.ids = {t.lower() for t in _IDTOK.findall(self.blob)}
        if self.record_id:
            self.ids.add(self.record_id.lower())
        self.times = _timestamps(self.blob)
        self.clocks = set(_CLOCK.findall(self.text))
        up = " ".join((self.text, str(v) if isinstance(v, str) else "",
                       self.field))
        self.codes = up
        self.stale = bool(_UPPER_SUPERSEDED.search(self.text)
                          or _UPPER_HISTORICAL.search(self.text)
                          or self.field == "superseded_review")
        self.current = bool(_UPPER_CURRENT.search(self.text)) and \
            not self.stale
        self.owner = next((o for p, o in SOURCE_OWNER
                           if self.source.startswith(p)), None)


class FactIndex:
    def __init__(self, facts):
        self.raw_facts = [f for f in facts or [] if isinstance(f, dict)]
        self.facts: dict = {}
        for f in facts or []:
            if isinstance(f, dict) and f.get("fact_id"):
                fx = Fact(f)
                self.facts.setdefault(fx.fid, fx)
        self.order = list(self.facts)
        self.known_ids: set = set()
        for fx in self.facts.values():
            self.known_ids |= {i for i in fx.ids if re.search(r"\d", i)}

    def get(self, fid):
        return self.facts.get(fid)


# ═════════════════════════════════════════════════════════════════════
# 4 · SENTENCES, SEGMENTS, ITEMS
# ═════════════════════════════════════════════════════════════════════

def mask_quotations(text: str, facts) -> str:
    """A same-length copy of `text` in which every verbatim quotation of a
    fact's text (12+ characters, in fact order) has its internal sentence
    breaks neutralised (the whitespace after . ! ? and every line break
    becomes \\x03), so a quoted fact stays one sentence, and its own "[F"
    tokens become "\\x04F": a stored lesson that quotes an earlier answer's
    "[F72]" is the RECORD's text, never this answer's citation (production
    retrospective, research run 37234402124). The SQL port does the same
    replacements in the same order."""
    t = str(text or "")
    for f in facts or []:
        ft = str((f or {}).get("text") or "")
        if len(ft) < 12 or ft not in t:
            continue
        masked = re.sub(r"([.!?])[ \t\r\n\f\v]", "\\1\x03", ft).replace(
            "\n", "\x03").replace("[F", "\x04F")
        if masked != ft:
            t = t.replace(ft, masked)
    return t


def cited_fact_ids(text: str, facts=()) -> list:
    """The fact ids `text` cites, in order: every [F#] / [F#, F#] citation
    group outside a quoted fact's own text."""
    out = []
    for g in CITE_GROUP.finditer(mask_quotations(text, facts)):
        for fid in _group_ids(g.group(0)):
            if fid not in out:
                out.append(fid)
    return out


def split_sentences(text: str, facts=(), *, partial: bool = False,
                    masked: str | None = None) -> list:
    """[(start, end)] spans of the sentences of `text` (offsets into the
    original text). With `partial`, a trailing fragment with no terminal
    punctuation or citation (an interrupted stream) is left out."""
    t = str(text or "")
    if masked is None:
        masked = mask_quotations(t, facts)
    spans, start = [], 0
    for m in BOUNDARY.finditer(masked):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(t)))
    out = []
    for a, b in spans:
        seg = t[a:b]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg) - len(seg.rstrip())
        if seg.strip():
            out.append((a + lead, b - trail))
    if partial and out:
        a, b = out[-1]
        last = t[a:b].rstrip()
        if not (last.endswith((".", "!", "?", "]", ")")) or
                _ANY_CITE.search(last[-12:])):
            out = out[:-1]
    return out


def _group_ids(g: str) -> list:
    out = []
    for n in _FID.findall(g):
        fid = "F%d" % int(n)
        if fid not in out:
            out.append(fid)
    return out


def segments(sentence: str) -> list:
    """[{text, start, end, group: [ids], gstart, gend, tail}] -- the clause
    before each citation group, then the tail after the last group (bound to
    the last group)."""
    groups = list(CITE_GROUP.finditer(sentence))
    out, start = [], 0
    for i, g in enumerate(groups):
        out.append({"i": i, "text": sentence[start:g.start()],
                    "start": start, "end": g.start(),
                    "group": _group_ids(g.group(0)),
                    "gstart": g.start(), "gend": g.end(), "tail": False})
        start = g.end()
    tail = sentence[start:]
    if groups:
        if tail.strip():
            last = out[-1]
            out.append({"i": len(groups) - 1, "text": tail, "start": start,
                        "end": len(sentence), "group": last["group"],
                        "gstart": last["gstart"], "gend": last["gend"],
                        "tail": True})
    else:
        out.append({"i": None, "text": sentence, "start": 0,
                    "end": len(sentence), "group": [], "gstart": None,
                    "gend": None, "tail": False})
    return out


def _question_numbers(question: str) -> set:
    out = set()
    for m in NUMBER.finditer(str(question or "")):
        d = _dec(m.group(2))
        if d is not None:
            out.add(d)
    return out


def _number_items(text: str, qnums: set, *, measure: bool) -> list:
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group(2)
        d = _dec(raw)
        if d is None:
            continue
        dollar, unit = bool(m.group(1)), (m.group(3) or "").strip()
        bare = not dollar and "." not in raw and not unit
        if bare and d in (Decimal(0), Decimal(1), Decimal(2)):
            continue                    # a counting word
        if d in qnums:
            continue                    # an echo of the question
        out.append({"kind": K_NUMBER, "token": m.group(1) + raw,
                    "value": d, "decimals": _decimals(raw),
                    "percentish": bool(unit and _PERCENTISH.search(unit)),
                    "measures": measures_near(text, m.start(), m.end())
                    if measure else frozenset(),
                    "unit": unit})
    return out


def _blank(text: str, spans: list) -> str:
    chars = list(text)
    for a, b in spans:
        for k in range(a, b):
            chars[k] = " "
    return "".join(chars)


def extract_items(text: str, idx: FactIndex, qnums: set, *,
                  profile: str = PROFILE_FULL) -> list:
    """The material items of one clause. Order of extraction: ids, then
    timestamps, then figures (an id's or a timestamp's digits are never read
    as figures), then codes, then status assertions and attributions."""
    items: list = []
    work = text
    if profile == PROFILE_FULL:
        spans = []
        for m in _IDTOK.finditer(work):
            tok = m.group(0)
            if _looks_like_id(tok, idx.known_ids):
                items.append({"kind": K_ID, "token": tok,
                              "kind_of_id": _id_kind(tok)})
                spans.append(m.span())
        work = _blank(work, spans)
        spans = []
        for m in _ISO.finditer(work):
            items.append({"kind": K_TIME, "token": m.group(0).strip(),
                          "date": m.group(1), "time": m.group(2)})
            spans.append(m.span())
        work = _blank(work, spans)
        spans = []
        for m in _CLOCK.finditer(work):
            items.append({"kind": K_TIME, "token": m.group(1), "date": None,
                          "time": m.group(1)})
            spans.append(m.span())
        work = _blank(work, spans)
    items += _number_items(work, qnums, measure=profile == PROFILE_FULL)
    if profile == PROFILE_FULL:
        seen = set()
        for rx, single in ((_MULTI_CODE, False), (_STATE_CODE, True)):
            for m in rx.finditer(work):
                c = m.group(0)
                if c in seen or c in ("CURRENT", "SUPERSEDED", "HISTORICAL"):
                    continue
                seen.add(c)
                items.append({"kind": K_CODE, "token": c, "single": single})
    cur, sup = status_assertion(text)
    if cur != sup:
        items.append({"kind": K_STATUS, "token": "CURRENT" if cur else
                      "SUPERSEDED", "asserts": "CURRENT" if cur else
                      "SUPERSEDED"})
    if profile == PROFILE_FULL:
        for m in _ATTRIB.finditer(text):
            items.append({"kind": K_AGENT, "token": m.group(0),
                          "agent": m.group(1).upper()})
    return items


def status_assertion(text: str) -> tuple:
    """(asserts current, asserts superseded) for one clause."""
    t = str(text or "")
    noun = bool(_STATUS_NOUN_RX.search(t))
    cur = bool(_UPPER_CURRENT.search(t)) or (
        noun and bool(_CURRENT_WORD.search(t)))
    sup = bool(_UPPER_SUPERSEDED.search(t)) or (
        noun and bool(_SUPERSEDED_WORD.search(t)))
    return cur, sup


def categories_of(text: str, items: list) -> list:
    out = [name for name, rx in CATEGORIES if rx.search(text)]
    if any(i["kind"] == K_TIME for i in items) and "timestamp" not in out:
        out.append("timestamp")
    if any(i["kind"] == K_AGENT for i in items) and \
            "agent_decision" not in out:
        out.append("agent_decision")
    return out


#: what makes a sentence material for NO_CITATION: a figure, a record id, a
#: timestamp or a code. A status word or an attribution with no citation is
#: not checkable against any record ("my current assessment rests on the
#: latest records") and is verified only when cited.
_MATERIAL_KINDS = (K_NUMBER, K_ID, K_TIME, K_CODE)


def _material(item: dict, modal: bool, cited: bool) -> bool:
    """Whether an item makes its sentence a material claim. Figures and
    timestamps always do. Without a citation, a lone action word (HOLD,
    REDUCE ...) is vocabulary, and an id or a refusal / policy code inside a
    proposal or conditional names a subject rather than asserting a record
    -- neither demands a citation. With a citation every item is checked
    (a modal sentence's lone action words were already set aside)."""
    k = item["kind"]
    if k in (K_NUMBER, K_TIME):
        return True
    if k not in (K_ID, K_CODE):
        return False
    if cited:
        return True
    if item.get("single"):
        return False
    return not modal


# ═════════════════════════════════════════════════════════════════════
# 5 · SUPPORT
# ═════════════════════════════════════════════════════════════════════

def _sig_digits(x: Decimal) -> int:
    digits = str(x).replace(".", "").replace("-", "").lstrip("0")
    return len(digits)


def _num_match(a: Decimal, item: dict) -> bool:
    """Exact, or rounded to the precision written (half-up or half-even) --
    a rounding is admitted only when the written figure has two or more
    significant digits or the rounding moves the value by at most 1% of it
    ("$97,958" for 97,958.35 and "149.3" for 149.300 are the record; "$1"
    for 0.60 is not)."""
    x, d = item["value"], item["decimals"]
    cands = [abs(a)]
    if item.get("percentish"):
        cands += [abs(a) * 100, abs(a) / 100]
    q = Decimal(1).scaleb(-d)
    for c in cands:
        if c == x:
            return True
        try:
            r = (c.quantize(q, rounding=ROUND_HALF_UP) == x or
                 c.quantize(q, rounding=ROUND_HALF_EVEN) == x)
        except InvalidOperation:
            continue
        if r and (_sig_digits(x) >= 2 or abs(c - x) <= abs(x) / 100):
            return True
    return False


def supports(fx: Fact, item: dict, *, profile: str = PROFILE_FULL) -> bool:
    k = item["kind"]
    if k == K_NUMBER:
        if profile == PROFILE_RETRO:
            return any(_num_match(a, item) for a in fx.raw)
        for a, ms in fx.tokens:
            if _num_match(a, item) and measures_compatible(
                    item["measures"], ms):
                return True
        # a number only inside the fact's ids / timestamps: measure-blind
        token_vals = [a for a, _m in fx.tokens]
        return any(_num_match(a, item) for a in fx.raw
                   if a not in token_vals)
    if k == K_ID:
        tok = item["token"].lower()
        return tok in fx.ids or tok in fx.blob.lower()
    if k == K_TIME:
        if item.get("date"):
            return any(dt == item["date"] and (
                not item.get("time") or (tm or "").startswith(item["time"]))
                for dt, tm in fx.times)
        t = item["time"]
        return t in fx.clocks or any((tm or "").startswith(t)
                                     for _d, tm in fx.times)
    if k == K_CODE:
        # a whole code or a run of its underscore-separated parts: HOLD in
        # HOLD_ON_STALE_PROBABILITY, BUY_SHORT in ORDER_INTENT_BUY_SHORT
        return bool(re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])"
                              % re.escape(item["token"]), fx.codes))
    if k == K_STATUS:
        if item["asserts"] == "CURRENT":
            return not (fx.stale and not fx.current)
        return not (fx.current and not fx.stale)
    if k == K_AGENT:
        return fx.owner is None or fx.owner == item["agent"] or \
            item["agent"].lower() in fx.text.lower()
    return False


def _status_positive(fx: Fact, item: dict) -> bool:
    """For a REPAIR, status must be held positively: a CURRENT assertion is
    re-cited only to a CURRENT-marked fact, a superseded one only to a
    STALE-marked fact."""
    return fx.current if item["asserts"] == "CURRENT" else fx.stale


# ═════════════════════════════════════════════════════════════════════
# 6 · ONE SENTENCE
# ═════════════════════════════════════════════════════════════════════

def _classify(item: dict, bound: list, alt: list, sent_ids: list,
              idx: FactIndex) -> str:
    k = item["kind"]
    if k == K_STATUS:
        if item["asserts"] == "CURRENT":
            return STALE_STATE_CITATION
        return WRONG_FACT
    if k == K_AGENT:
        return ENTITY_MISMATCH
    if k == K_ID:
        same_kind = any(i != item["token"].lower() and
                        _id_kind(i) == item["kind_of_id"]
                        for fx in bound for i in fx.ids)
        if same_kind:
            return ENTITY_MISMATCH
        return WRONG_FACT if alt else INSUFFICIENT_SUPPORT
    if not alt:
        return INSUFFICIENT_SUPPORT
    if any(fx.stale and not fx.current for fx in bound) and \
            any(not fx.stale for fx in alt):
        return STALE_STATE_CITATION
    for sid in sent_ids:
        named = sid["token"].lower()
        if any(named in fx.ids or named in fx.blob.lower() for fx in bound):
            continue
        if any(i != named and _id_kind(i) == sid["kind_of_id"]
               for fx in bound for i in fx.ids) and \
                any(named in fx.blob.lower() for fx in alt):
            return ENTITY_MISMATCH
    return WRONG_FACT


def verify_sentence(sentence: str, idx: FactIndex, qnums: set, *,
                    profile: str = PROFILE_FULL) -> dict:
    segs = segments(sentence)
    cited = []
    for s in segs:
        for fid in s["group"]:
            if fid not in cited:
                cited.append(fid)
    group_list = []
    for s in segs:
        if s["i"] is not None and not s["tail"]:
            group_list.append(s["group"])
    seg_items = []
    all_items = []
    modal = bool(_MODAL.search(sentence))
    for s in segs:
        its = extract_items(s["text"], idx, qnums, profile=profile)
        if modal:
            # a single action word in a proposal or conditional is
            # vocabulary ("should either reconfirm HOLD or ..."), never
            # checked as a claim about a record
            its = [i for i in its if not i.get("single")]
        seg_items.append(its)
        all_items += its
    sent_ids = [i for i in all_items if i["kind"] == K_ID]
    material = any(_material(i, modal, bool(cited)) for i in all_items)
    out = {"text": sentence, "sha256": sha256(sentence),
           "cited_ids": cited,
           "unknown_ids": [f for f in cited if idx.get(f) is None],
           "material": bool(material or (cited and all_items)),
           "categories": categories_of(sentence, all_items),
           "items": len(all_items), "failing": [], "verdict": PASS,
           "segments": []}
    if not out["material"]:
        out["verdict"] = None
        return out
    if not cited:
        need = [i for i in all_items if _material(i, modal, False)]
        alt_all = [fx.fid for fx in idx.facts.values()
                   if all(supports(fx, it, profile=profile) for it in need)]
        out["verdict"] = NO_CITATION
        out["failing"] = [{"kind": i["kind"], "token": i["token"],
                           "verdict": NO_CITATION} for i in need]
        out["supported_by_uncited"] = alt_all[:10]
        out["segments"].append({"segment": 0, "items": len(all_items),
                                "failing": len(out["failing"])})
        return out
    worst = PASS
    for n, (s, its) in enumerate(zip(segs, seg_items)):
        gi = s["i"]
        primary = [idx.get(f) for f in s["group"] if idx.get(f)]
        secondary = []
        if gi is not None and gi >= 1 and not s["tail"]:
            secondary = [idx.get(f) for f in group_list[gi - 1]
                         if idx.get(f)]
        bound = primary + secondary
        fails = []
        for it in its:
            pool = primary if it["kind"] == K_STATUS else bound
            if it["kind"] in (K_STATUS, K_AGENT) and not pool:
                continue                # nothing cited that could contradict
            if it["kind"] == K_AGENT:
                owned = [fx for fx in pool if fx.owner is not None]
                ok = (not owned) or any(supports(fx, it, profile=profile)
                                        for fx in pool)
            else:
                ok = any(supports(fx, it, profile=profile) for fx in pool)
            if ok:
                continue
            alt = [fx for fx in idx.facts.values() if fx not in pool and
                   supports(fx, it, profile=profile)] \
                if it["kind"] not in (K_STATUS, K_AGENT) else []
            v = _classify(it, pool, alt, sent_ids, idx)
            fails.append({"kind": it["kind"], "token": it["token"],
                          "verdict": v, "alt": [fx.fid for fx in alt][:10],
                          "segment": n})
            if _SEVERITY[v] > _SEVERITY[worst]:
                worst = v
        out["failing"] += fails
        out["segments"].append({"segment": n, "group": s["group"],
                                "items": len(its), "failing": len(fails)})
    out["verdict"] = worst
    return out


# ═════════════════════════════════════════════════════════════════════
# 7 · A WHOLE TEXT
# ═════════════════════════════════════════════════════════════════════

def _skip(sentence: str, skip_prefixes) -> bool:
    s = sentence.lstrip()
    return s.startswith(NOTE_PREFIX) or any(
        p and s.startswith(p) for p in (skip_prefixes or ()))


def verify(text: str, facts, *, question: str = "", skip_prefixes=(),
           partial: bool = False, profile: str = PROFILE_FULL,
           index: FactIndex | None = None) -> dict:
    """Every sentence of `text` checked against `facts` (see the module
    docstring). Returns {version, profile, sentences, counts, material,
    cited_material, passed}."""
    idx = index or FactIndex(facts)
    qn = _question_numbers(question)
    t = str(text or "")
    masked = mask_quotations(t, facts)
    sents = []
    for n, (a, b) in enumerate(split_sentences(t, facts, partial=partial,
                                               masked=masked)):
        # checked on the masked form (a quotation's own [F tokens are not
        # citations); reported and repaired on the original, same offsets
        ev = masked[a:b].replace("\x03", " ")
        if _skip(ev, skip_prefixes):
            continue
        r = verify_sentence(ev, idx, qn, profile=profile)
        r.update({"index": n, "start": a, "end": b, "text": t[a:b],
                  "sha256": sha256(t[a:b]), "eval_text": ev})
        sents.append(r)
    counts = {v: 0 for v in VERDICTS}
    for r in sents:
        if r["verdict"] is not None:
            counts[r["verdict"]] += 1
    material = sum(counts.values())
    return {"version": VERSION, "profile": profile, "sentences": sents,
            "sentence_count": len(sents), "counts": counts,
            "material": material,
            "cited_material": material - counts[NO_CITATION],
            "passed": material == counts[PASS]}


# ═════════════════════════════════════════════════════════════════════
# 8 · DETERMINISTIC REPAIR
# ═════════════════════════════════════════════════════════════════════

def _supports_all(fx: Fact, items: list) -> bool:
    for it in items:
        if it["kind"] == K_STATUS:
            if not _status_positive(fx, it):
                return False
        elif not supports(fx, it):
            return False
    return True


def _evaluable(sentence: str, idx: FactIndex) -> str:
    return mask_quotations(sentence, idx.raw_facts).replace("\x03", " ")


_STATE_LOWER = re.compile(r"\b(?:%s)\b" % "|".join(
    w.lower() for w in STATE_WORDS if len(w) >= 4), re.I)


def _still_relevant(orig: list, items: list, clause: str) -> bool:
    """Whether a clause's original citation still holds part of what the
    clause says: one of its checked items, or a state word the clause uses
    in prose ("expired" against a fact whose state is EXPIRED). Decides
    only whether a repair cites beside the original or replaces it."""
    for fx in orig:
        if any(supports(fx, i) for i in items if i["kind"] != K_STATUS):
            return True
        for w in set(m.group(0).upper() for m in _STATE_LOWER.finditer(
                clause)):
            if re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % w,
                         fx.codes.upper()):
                return True
    return False


def _repair_sentence(sentence: str, res: dict, idx: FactIndex,
                     qnums: set) -> tuple:
    """(new sentence, [{from, to, segment}]) or (None, reason). Segments are
    read on the evaluable (masked) form; the edit is applied to the original
    at the same offsets."""
    segs = segments(res.get("eval_text") or _evaluable(sentence, idx))
    if res["verdict"] == NO_CITATION:
        modal = bool(_MODAL.search(res.get("eval_text") or sentence))
        items = []
        for s in segs:
            items += [i for i in extract_items(s["text"], idx, qnums)
                      if _material(i, modal, False)]
        cands = [fx for fx in idx.facts.values() if _supports_all(fx, items)]
        if len(cands) != 1:
            return None, "%d_FACTS_SUPPORT_THE_UNCITED_MATERIAL" % len(cands)
        body = sentence.rstrip()
        end = len(body)
        while end and body[end - 1] in ".!?":
            end -= 1
        new = "%s [%s]%s" % (body[:end], cands[0].fid, body[end:])
        return new, [{"from": [], "to": cands[0].fid, "segment": 0}]
    failing_segments = sorted({f["segment"] for f in res["failing"]})
    edits = []
    for n in failing_segments:
        s = segs[n]
        if s["gstart"] is None:
            return None, "UNCITED_CLAUSE"
        its = extract_items(s["text"], idx, qnums)
        failing_tokens = {(f["kind"], f["token"]) for f in res["failing"]
                          if f["segment"] == n}
        fail_items = [i for i in its
                      if (i["kind"], i["token"]) in failing_tokens]
        if any(i["kind"] == K_AGENT for i in fail_items):
            return None, "AGENT_ATTRIBUTION_NOT_REPAIRABLE"
        whole = [fx for fx in idx.facts.values()
                 if _supports_all(fx, its)]
        if len(whole) == 1:
            orig = [idx.get(f) for f in s["group"] if idx.get(f)]
            if _still_relevant(orig, its, s["text"]) and not any(
                    i["kind"] == K_STATUS for i in fail_items):
                # the clause's citation still holds part of what it says
                # ("566 units expired [state = EXPIRED, ...]"): the one
                # fact that holds the rest is cited BESIDE it
                ids = list(s["group"]) + [whole[0].fid]
            else:
                # a citation that holds nothing the clause says (or a
                # stale record cited for the present): REPLACED
                ids = [whole[0].fid]
            edits.append((s["gstart"], s["gend"], ids, s["group"], n))
            continue
        if any(i["kind"] == K_STATUS for i in fail_items):
            # a stale citation for a current state is REPLACED by the one
            # current record that holds the whole clause, never joined by
            # one beside it (that would let "review rv-8790 is CURRENT"
            # pass by citing someone else's current review)
            return None, "%d_CURRENT_FACTS_HOLD_THE_CLAUSE" % len(whole)
        rest = [i for i in its if (i["kind"], i["token"])
                not in failing_tokens]
        orig = [idx.get(f) for f in s["group"] if idx.get(f)]
        keeps = all(any(supports(fx, i) for fx in orig) for i in rest)
        part = [fx for fx in idx.facts.values()
                if fx not in orig and _supports_all(fx, fail_items)]
        if keeps and len(part) == 1 and rest:
            edits.append((s["gstart"], s["gend"],
                          list(s["group"]) + [part[0].fid], s["group"], n))
            continue
        return None, "%d_FACTS_SUPPORT_THE_CLAUSE" % len(whole)
    new = sentence
    done = []
    seen_groups = set()
    for gs, ge, ids, old, n in sorted(edits, key=lambda e: -e[0]):
        if (gs, ge) in seen_groups:
            continue
        seen_groups.add((gs, ge))
        trail = re.search(r"[ \t,]*$", new[gs:ge]).group(0)
        new = new[:gs] + " ".join("[%s]" % i for i in ids) + trail + new[ge:]
        done.append({"from": old, "to": ids, "segment": n})
    return new, done


def repair(text: str, report: dict, facts, *, question: str = "",
           index: FactIndex | None = None) -> tuple:
    """(new text, repairs) -- every failing sentence for which EXACTLY ONE
    fact supports the material is re-cited; the repaired sentence must then
    PASS on its own, or it is left as it was."""
    idx = index or FactIndex(facts)
    qn = _question_numbers(question)
    t = str(text or "")
    edits, repairs = [], []
    for r in report["sentences"]:
        if r["verdict"] in (None, PASS):
            continue
        new, how = _repair_sentence(r["text"], r, idx, qn)
        if new is None:
            repairs.append({"index": r["index"], "repaired": False,
                            "why": how, "verdict": r["verdict"]})
            continue
        again = verify_sentence(_evaluable(new, idx), idx, qn)
        if again["verdict"] != PASS:
            repairs.append({"index": r["index"], "repaired": False,
                            "why": "REPAIR_DID_NOT_VERIFY",
                            "verdict": r["verdict"]})
            continue
        edits.append((r["start"], r["end"], new))
        repairs.append({"index": r["index"], "repaired": True,
                        "verdict": r["verdict"], "edits": how,
                        "sha256_after": sha256(new)})
    for a, b, new in sorted(edits, key=lambda e: -e[0]):
        t = t[:a] + new + t[b:]
    return t, repairs


def annotate(text: str, report: dict) -> str:
    """Each still-failing sentence followed by the explicit statement that
    the evidence does not support it."""
    t = str(text or "")
    for r in sorted(report["sentences"], key=lambda r: -r["end"]):
        if r["verdict"] in (None, PASS):
            continue
        note = " %s %s.)" % (NOTE_PREFIX, NOTE_PHRASE[r["verdict"]])
        t = t[:r["end"]] + note + t[r["end"]:]
    return t


# ═════════════════════════════════════════════════════════════════════
# 9 · THE GATE (what the answer pipeline calls before publishing)
# ═════════════════════════════════════════════════════════════════════

def _worst(report: dict) -> str | None:
    worst = None
    for r in report["sentences"]:
        v = r["verdict"]
        if v not in (None, PASS) and (worst is None or
                                      _SEVERITY[v] > _SEVERITY[worst]):
            worst = v
    return worst


def gate(text: str, facts, *, question: str = "",
         composer: str = COMPOSER_MODEL, skip_prefixes=(),
         partial: bool = False) -> dict:
    """{action, text (what may be published; None when a model reply is to
    be discarded), reason, verdict, before, after, repairs, sentences} --
    `sentences` is the per-sentence ledger: index, sha256, cited ids,
    verdict (as first verified), categories, failing items, action and the
    ids a repair cited."""
    idx = FactIndex(facts)
    before = verify(text, facts, question=question,
                    skip_prefixes=skip_prefixes, partial=partial, index=idx)
    out = {"version": VERSION, "composer": composer, "partial": partial,
           "before": before, "repairs": [], "reason": None,
           "verdict": None}
    if before["passed"]:
        out.update(action=A_VERIFIED, text=text, after=before)
        out["sentences"] = _ledger(before, [], A_VERIFIED)
        return out
    new, repairs = repair(text, before, facts, question=question, index=idx)
    after = verify(new, facts, question=question,
                   skip_prefixes=skip_prefixes, partial=partial, index=idx)
    out.update(repairs=repairs, after=after)
    if after["passed"]:
        out.update(action=A_REPAIRED, text=new)
        out["sentences"] = _ledger(before, repairs, A_REPAIRED)
        return out
    worst = _worst(after)
    out["verdict"] = worst
    out["reason"] = "CITATION_INTEGRITY:%s" % worst
    if composer == COMPOSER_MODEL and not partial and worst != NO_CITATION:
        # a KNOWN unsupported citation (wrong record, stale record, other
        # entity, nothing holds it) is never published: the whole reply is
        # discarded. A sentence that merely cites nothing (and no single
        # fact could be cited for it) is kept with the explicit integrity
        # statement instead -- the production retrospective showed 87 of
        # 302 stored model replies whose only gap was an uncited sentence.
        out.update(action=A_FALLBACK, text=None)
        out["sentences"] = _ledger(before, repairs, A_FALLBACK)
        return out
    out.update(action=A_STATED, text=annotate(new, after))
    out["sentences"] = _ledger(before, repairs, A_STATED)
    return out


def _ledger(before: dict, repairs: list, mode: str) -> list:
    """One row per material sentence. In a discarded model reply every row
    is FELL_BACK_TO_RECORDS_ONLY (nothing of it was published; a repair that
    was possible is still listed); otherwise PASS -> PUBLISHED_VERIFIED, a
    repaired sentence -> REPAIRED_RECITED, an unrepaired one ->
    STATED_UNSUPPORTED."""
    fixed = {r["index"]: r for r in repairs if r.get("repaired")}
    rows = []
    for r in before["sentences"]:
        if r["verdict"] is None:
            continue
        to = []
        for e in (fixed.get(r["index"]) or {}).get("edits") or []:
            to += e["to"] if isinstance(e["to"], list) else [e["to"]]
        if mode == A_FALLBACK:
            act = A_FALLBACK
        elif r["verdict"] == PASS:
            act = A_VERIFIED
        elif r["index"] in fixed:
            act = A_REPAIRED
        else:
            act = A_STATED
        rows.append({"index": r["index"], "sha256": r["sha256"],
                     "cited_ids": r["cited_ids"], "verdict": r["verdict"],
                     "categories": r["categories"],
                     "failing": [{k: f.get(k) for k in ("kind", "token",
                                                        "verdict", "alt")}
                                 for f in r["failing"]][:12],
                     "action": act, "repaired_ids": to})
    return rows


def summary(g: dict) -> dict:
    """The compact form kept in the stored message's provider record."""
    b = g["before"]
    return {"version": g["version"], "action": g["action"],
            "reason": g.get("reason"), "verdict": g.get("verdict"),
            "sentences": b["sentence_count"], "material": b["material"],
            "cited_material": b["cited_material"],
            "counts": {k: v for k, v in b["counts"].items() if v},
            "repaired": sum(1 for r in g["repairs"] if r.get("repaired"))}


# ═════════════════════════════════════════════════════════════════════
# 10 · THE RETROSPECTIVE TALLY (stored answers; PM question E)
# ═════════════════════════════════════════════════════════════════════

#: an LLM-mode stored answer's figure that no CITED fact holds: the answer
#: passed the full-list figure check when it was published
#: (persona_chat.ungrounded_numbers), so an UNCITED fact held it -- the
#: sentence cited the wrong or too few facts. Counted apart from the
#: observed classes: the full fact list was not persisted, so which fact it
#: was cannot be shown.
INFERRED_UNCITED_SOURCE = "INFERRED_UNCITED_SOURCE"


#: a stored answer's failing sentence that cites a fact id the stored record
#: does not hold: before the comma-list fix (persona_chat.cited_facts),
#: "[F212, F213]" was never stored, so such a sentence cannot be judged
#: from the record -- counted apart, out of every rate's denominator
UNVERIFIABLE_NOT_STORED = "UNVERIFIABLE_CITED_FACT_NOT_STORED"


def _tally_row() -> dict:
    return {"answers": 0, "answers_with_material": 0,
            "answers_with_cited_material": 0, "sentences": 0,
            "material": 0, "cited_material": 0,
            "verdicts": {v: 0 for v in VERDICTS},
            INFERRED_UNCITED_SOURCE: 0, UNVERIFIABLE_NOT_STORED: 0,
            "answers_with_wrong_support": 0,
            "answers_with_wrong_or_inferred": 0}


def is_unverifiable(sentence: dict) -> bool:
    return sentence.get("verdict") not in (None, PASS) and \
        bool(sentence.get("unknown_ids"))


def is_inferred_uncited(sentence: dict, mode: str) -> bool:
    if mode != "LLM" or sentence.get("verdict") != INSUFFICIENT_SUPPORT:
        return False
    ins = [f for f in sentence["failing"]
           if f["verdict"] == INSUFFICIENT_SUPPORT]
    return bool(ins) and all(f["kind"] == K_NUMBER for f in ins)


def retro_tally(reports) -> dict:
    """[(agent, provider mode, `verify` report)] -> {"by_agent": {agent:
    {mode: counts, "ALL": counts}}, "all": counts}. Pure; the production
    SQL port emits the same counts."""
    by: dict = {}
    total = {"ALL": _tally_row()}
    for agent, mode, rep in reports:
        mode = mode or "UNKNOWN"
        rows = by.setdefault(agent, {})
        targets = [rows.setdefault(mode, _tally_row()),
                   rows.setdefault("ALL", _tally_row()),
                   total.setdefault(mode, _tally_row()), total["ALL"]]
        sents = [s for s in rep["sentences"] if s["verdict"] is not None]
        judged = [s for s in sents if not is_unverifiable(s)]
        strict = sum(1 for s in judged if s["verdict"] in WRONG_SUPPORT)
        inferred = sum(1 for s in judged if is_inferred_uncited(s, mode))
        cited = sum(1 for s in sents if s["verdict"] != NO_CITATION)
        for t in targets:
            t["answers"] += 1
            t["sentences"] += rep["sentence_count"]
            t["material"] += len(sents)
            t["cited_material"] += cited
            t["answers_with_material"] += 1 if sents else 0
            t["answers_with_cited_material"] += 1 if cited else 0
            for s in judged:
                t["verdicts"][s["verdict"]] += 1
            t[UNVERIFIABLE_NOT_STORED] += len(sents) - len(judged)
            t[INFERRED_UNCITED_SOURCE] += inferred
            t["answers_with_wrong_support"] += 1 if strict else 0
            t["answers_with_wrong_or_inferred"] += 1 if (
                strict or inferred) else 0
    return {"by_agent": by, "all": total}
