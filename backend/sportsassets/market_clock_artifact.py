"""Pure copy of the existing side-aware premap clock proof.

Copied from verified 53c4767 premap.py; AST parity is tested against that
worker so this cannot silently diverge. Kept dependency-free for read models.
"""
import re

_LINE_CTX = re.compile(
    r"(?:[+-]|\bover\b|\bunder\b|\bo\b|\bu\b|o/u|:)\s*(\d+(?:\.\d+)?)",
    re.I)

def _canon_line(n: str) -> str:
    """One spelling per line value. The venue writes '1.50' where the
    whale's slug says 1pt5 -> '1.5'; every comparison in this module is
    string equality, so numerically identical lines failed line_ok and
    the row was refused as no_side_match (live census 2026-08-29: the
    printed candidates carried line '1.50' against his_lines ['1.5']).
    Trailing zeros after the point (and a then-bare point) are dropped;
    whole numbers pass through untouched."""
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n

def _lines_of(text: str | None) -> set[str]:
    """Every line a text states — half-point lines anywhere, plus WHOLE
    numbers when they sit in a handicap or total context.

    Only \d+\.5 was matched before, so a whole-number line ('-3', 'O/U
    47') produced NO line at all on both the venue row and the whale's
    pick — and an empty line skipped the comparison entirely (leak-hunt
    round 3, 2026-08-24)."""
    t = text or ""
    out = {n for n in re.findall(r"\d+\.\d+", t)}
    out |= {m.group(1) for m in _LINE_CTX.finditer(t)}
    return {_canon_line(n) for n in out if n}

def _clock_artifact(rl: str, r: dict) -> bool:
    """True when a row's stamped line is nothing but the question's
    CLOCK minutes — '7:00 PM' matched the ':' line context and
    stamped line='00' on BOTH sides of a moneyline market, and the
    unlined pick then failed the line-presence comparison against a
    phantom (live census 2026-08-29, the WNBA example: 'Washington
    Mystics' refused against sides mystics/sparks). Authentication
    mirrors the named-tennis bridge's clock quarantine: the line must
    be the question's SOLE parsed line AND verbatim-equal to a strict
    clock parse's minutes. A global time-strip is NOT an option — the
    bridge lane depends on the clock stamping (its quarantine
    authenticates the same artifact instead of erasing it).

    Fleet round 37 (major): the question-only check had a
    coincidence hole — a REAL line stamped from the SIDE DESCRIPTION
    ('Alabama -30') collided with a ':30' kickoff clock in the
    question, authenticated as an artifact, and the erased real line
    both refused the correct lined pick AND let an unlined moneyline
    pick wrongly match the spread side (a money-direction wrong-
    market copy). A line the SIDE ITSELF states is real whatever the
    clock says: the row's signed magnitude and its side_norm tokens
    both veto the artifact."""
    q = r.get("question")
    if not rl or not q:
        return False
    m = re.search(r"\b\d{1,2}:(\d{2})\b", q)
    if not (m and m.group(1) == rl and _lines_of(q) == {rl}):
        return False
    mag = (r.get("signed") or "").lstrip("+-")
    if mag and _canon_line(mag) == rl:
        return False               # the side states this line, signed
    sn = r.get("side_norm") or ""
    if rl in _lines_of(sn) or re.search(
            r"(?:^|\s)" + re.escape(rl) + r"(?:\s|$)", sn):
        return False               # the side states this line, plain
    return True
