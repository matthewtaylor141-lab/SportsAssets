"""PRONUNCIATION NORMALISER: the stored transcript -> the text a voice speaks.

PURE. The visible transcript is never changed; `normalise(body)` is the
spoken form of exactly that text, stored beside it (agent_chat_messages
.spoken_text) and the ONLY text sent to the speech provider.

    "$0.50"                      -> "fifty cents"
    "50¢"                        -> "fifty cents"
    "$1,000"                     -> "one thousand dollars"
    "$180"                       -> "one hundred eighty dollars"
    "$2,200.50"                  -> "two thousand two hundred dollars and
                                     fifty cents"
    "-$200" / "−$200"            -> "minus two hundred dollars"
    "18%"                        -> "eighteen percent"
    "9 pp" / "+9.00 pp" /
    "9 percentage points"        -> "nine percentage points"
    "probability 0.59"           -> "probability fifty-nine percent"
    "0.59" (no probability label)-> "zero point five nine"

A decimal between 0 and 1 is read as a percentage ONLY when the clause labels
it a probability ("probability", "chance", "likelihood", "implied", "p_...",
"p =") before it, or "probability" follows it directly. Record citations in
square brackets ("[derek_entry_decisions:drk-1]", "[F3]") and a trailing
"Sources:" line are for the reader; they are not spoken.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_ONES = ("zero one two three four five six seven eight nine ten eleven "
         "twelve thirteen fourteen fifteen sixteen seventeen eighteen "
         "nineteen").split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")
_SCALES = ((10 ** 12, "trillion"), (10 ** 9, "billion"),
           (10 ** 6, "million"), (1000, "thousand"))
_DIGIT = dict(enumerate(_ONES[:10]))
_MONTHS = ("January February March April May June July August September "
           "October November December").split()


def _under_thousand(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        t = _TENS[n // 10]
        parts.append(t + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n > 0:
        parts.append(_ONES[n])
    return " ".join(parts)


def integer_words(n: int) -> str:
    """0 -> 'zero', 180 -> 'one hundred eighty', 2200 -> 'two thousand two
    hundred', -3 -> 'minus three'."""
    n = int(n)
    if n < 0:
        return "minus " + integer_words(-n)
    if n == 0:
        return "zero"
    # BEYOND THE LARGEST SCALE THE NUMBER IS AN IDENTIFIER, NOT A QUANTITY
    # (a ledger sequence, an epoch in ms, a venue id). Read it digit by
    # digit, exactly as written -- n // 10**12 >= 1000 would otherwise index
    # past `_ONES` and crash the answer that contains it.
    if n >= 10 ** 15:
        return " ".join(_DIGIT[int(c)] for c in str(n))
    parts = []
    for size, name in _SCALES:
        if n >= size:
            parts.append(_under_thousand(n // size) + " " + name)
            n %= size
    if n:
        parts.append(_under_thousand(n))
    return " ".join(parts)


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(str(s).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def decimal_words(s: str) -> str:
    """'2.5' -> 'two point five', '0.59' -> 'zero point five nine',
    '9.00' -> 'nine', '1,000' -> 'one thousand'."""
    s = str(s).replace(",", "").strip()
    neg = s.startswith(("-", "−"))
    s = s.lstrip("+-−")
    if "." in s:
        whole, frac = s.split(".", 1)
        frac = frac.rstrip("0")
    else:
        whole, frac = s, ""
    out = integer_words(int(whole or "0"))
    if frac:
        out += " point " + " ".join(_DIGIT[int(c)] for c in frac)
    return ("minus " if neg else "") + out


def money_words(amount: str, *, negative: bool = False) -> str:
    """'0.50' -> 'fifty cents', '1,000' -> 'one thousand dollars',
    '2200.50' -> 'two thousand two hundred dollars and fifty cents',
    '0.0826' -> 'eight point two six cents'."""
    d = _dec(amount)
    if d is None:
        return amount
    if d < 0:
        negative, d = True, -d
    dollars = int(d)
    cents = (d - dollars) * 100
    pre = "minus " if negative else ""
    if cents != cents.to_integral_value():
        # a sub-cent price: say it in cents
        c = (d * 100).normalize()
        txt = decimal_words(format(c, "f"))
        if dollars == 0:
            return pre + txt + " cents"
        return pre + integer_words(dollars) + (" dollar" if dollars == 1
                                               else " dollars") + \
            " and " + decimal_words(format(cents.normalize(), "f")) + " cents"
    cents = int(cents)
    if dollars == 0 and cents == 0:
        return pre + "zero dollars"
    parts = []
    if dollars:
        parts.append(integer_words(dollars) + (" dollar" if dollars == 1
                                               else " dollars"))
    if cents:
        parts.append(integer_words(cents) + (" cent" if cents == 1
                                             else " cents"))
    return pre + " and ".join(parts)


def percent_words(num: str) -> str:
    return decimal_words(num) + " percent"


def probability_words(num: str) -> str:
    """'0.59' -> 'fifty-nine percent', '0.595' -> 'fifty-nine point five
    percent', '1.00' -> 'one hundred percent'."""
    d = _dec(num)
    if d is None:
        return num
    pct = (d * 100).normalize()
    return decimal_words(format(pct, "f")) + " percent"


# ── the patterns, applied in order ──────────────────────────────────
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+"
_CITATION = re.compile(r"\s?\[(?:F\d+|[A-Za-z_][\w.\-]*:[^\]\n]{1,200})\]"
                       r"(?:\s?\[(?:F\d+|[A-Za-z_][\w.\-]*:[^\]\n]{1,200})\])*")
_SOURCES_LINE = re.compile(r"(?im)^\s*(?:sources|facts cited)\s*:.*$")
_DISCLOSURE_LINE = re.compile(r"(?m)^\s*\[[^\]\n]{0,300}\]\s*$")
_MD = re.compile(r"(\*\*|__|`+|^#+\s*|^\s*[-*•]\s+)", re.M)
_NEG_MONEY = re.compile(r"(?<![\w$])[-−–]\s?\$\s?(" + _NUM + r")")
_MONEY_NEG_INSIDE = re.compile(r"\$\s?[-−](" + _NUM + r")")
_MONEY = re.compile(r"\$\s?(" + _NUM + r")(?:\s?(k|K|M|mm|million|thousand|"
                    r"billion)\b)?")
_CENTS = re.compile(r"(" + _NUM + r")\s?¢")
_PP = re.compile(r"([+\-−]?)(" + _NUM + r")\s?(?:pp\b|p\.p\.|percentage\s+"
                 r"points?\b|percentage-points?\b)", re.I)
_PCT = re.compile(r"([+\-−]?)(" + _NUM + r")\s?%")
_PROB_LABEL = re.compile(
    r"\b(?:probabilit(?:y|ies)|prob\.?|chance|likelihood|implied|"
    r"p_[a-z_]+|p\s*=)", re.I)
_PROB_AFTER = re.compile(r"(?<![\w.])(0?\.\d+|1\.0+|0|1)(\s+probability)\b",
                         re.I)
_UNIT_DEC = re.compile(r"(?<![\w.$\d])(0?\.\d+|1\.0+)"
                       r"(?!\w|\.\d|%|¢|\s?pp\b)")
_CLAUSE_SPLIT = re.compile(r"([.;!?](?!\d)|\n)")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SIGNED = re.compile(r"(?<![\w.])([+\-−])(" + _NUM + r")(?![\w])")
_PLAIN = re.compile(r"(?<![\w.])(" + _NUM + r")(?![\w])")
_SYMBOLS = [(re.compile(r"\s?×\s?"), " times "),
            (re.compile(r"(?<=\d)\s?x\s?(?=[\d$])"), " times "),
            (re.compile(r"\s?≥\s?"), " at least "),
            (re.compile(r"\s?≤\s?"), " at most "),
            (re.compile(r"\s?(?:→|->)\s?"), ", then "),
            (re.compile(r"\s=\s"), " equals "),
            (re.compile(r"(?<=[\d)])\s?/\s?(?=[\d(])"), " divided by "),
            (re.compile(r"\s\+\s"), " plus "),
            (re.compile(r"(?<=[\d)])\s[-−]\s(?=[\d($])"), " minus "),
            (re.compile(r"\s?≈\s?"), " about "),
            (re.compile(r"(?<=\s)~(?=\d|\$)"), "about "),
            (re.compile(r"\s@\s"), " at "),
            (re.compile(r"\s&\s"), " and "),
            (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"),
            (re.compile(r"\bML\b"), "moneyline"),
            (re.compile(r"\bEV\b"), "E V"),
            (re.compile(r"\bP&L\b", re.I), "P and L")]
_ALLCAPS_WORD = re.compile(r"\b([A-Z]{5,})\b")


def _money_repl(m) -> str:
    num, scale = m.group(1), (m.group(2) or "")
    if scale:
        mult = {"k": 1000, "thousand": 1000, "m": 10 ** 6, "mm": 10 ** 6,
                "million": 10 ** 6, "billion": 10 ** 9}[scale.lower()]
        d = _dec(num)
        if d is not None:
            num = format((d * mult).normalize(), "f")
    return money_words(num)


def _pp_repl(m) -> str:
    sign, num = m.group(1), m.group(2)
    d = _dec(num)
    unit = " percentage point" if d is not None and d == 1 else \
        " percentage points"
    return ("minus " if sign in ("-", "−") else "") + decimal_words(num) + \
        unit


def _pct_repl(m) -> str:
    sign, num = m.group(1), m.group(2)
    return ("minus " if sign in ("-", "−") else "") + percent_words(num)


def _probabilities(text: str) -> str:
    """Within each clause, a unit-interval decimal AFTER a probability label
    is a percentage; so is one directly followed by 'probability'."""
    text = _PROB_AFTER.sub(lambda m: probability_words(m.group(1))
                           + m.group(2), text)
    parts = _CLAUSE_SPLIT.split(text)
    out = []
    for part in parts:
        lab = _PROB_LABEL.search(part)
        if lab is None:
            out.append(part)
            continue
        head, tail = part[:lab.end()], part[lab.end():]
        tail = _UNIT_DEC.sub(lambda m: probability_words(m.group(1)), tail)
        out.append(head + tail)
    return "".join(out)


def _date_repl(m) -> str:
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)
    return "%s %d, %d" % (_MONTHS[mo - 1], d, y)


def normalise(text: str) -> str:
    """The spoken form of `text` (see the module docstring)."""
    s = str(text or "")
    s = _SOURCES_LINE.sub("", s)
    s = _DISCLOSURE_LINE.sub("", s)
    s = _CITATION.sub("", s)
    s = _MD.sub("", s)
    s = _ISO_DATE.sub(_date_repl, s)
    s = _NEG_MONEY.sub(lambda m: money_words(m.group(1), negative=True), s)
    s = _MONEY_NEG_INSIDE.sub(lambda m: money_words(m.group(1),
                                                    negative=True), s)
    s = _MONEY.sub(_money_repl, s)
    s = _CENTS.sub(lambda m: money_words(
        format((_dec(m.group(1)) or Decimal(0)) / 100, "f")), s)
    s = _PP.sub(_pp_repl, s)
    s = _PCT.sub(_pct_repl, s)
    s = _probabilities(s)
    for rx, rep in _SYMBOLS:
        s = rx.sub(rep, s)
    s = _SIGNED.sub(lambda m: ("minus " if m.group(1) in ("-", "−")
                               else "plus ") + decimal_words(m.group(2)), s)
    s = _PLAIN.sub(lambda m: decimal_words(m.group(1)), s)
    s = _ALLCAPS_WORD.sub(lambda m: m.group(1).lower(), s)
    s = re.sub(r"(?<=[A-Za-z])_(?=[A-Za-z])", " ", s)
    s = s.replace("(", "").replace(")", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" +([,.;:!?])", r"\1", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
