"""Phase-1 zero-mismatch audit of the named-tennis lane — PINNED.

The lane's certification bar (design record, 2026-08-27): before any
executor code consumes a named_ml bridge hit, the production census's
own audit records must survive a zero-mismatch hand audit. This file
IS that audit, executed 2026-08-27 over the 40 audit records the
first post-1.3 census emitted (would_resolve=62; the census stores
40), and pinned so the bar cannot silently rot.

The verifier is INDEPENDENT BY CONSTRUCTION: it imports nothing from
premap and re-derives every witness from the raw strings — a bug
shared with the lane's implementation cannot vouch for itself. The
one thing it hardened DURING the audit: the first pass wrongly
assumed his pick always lands on a BUY_LONG row and flagged 15
records; the lane's actual contract is that the pick reaches his OWN
name's row carrying whatever polarity the venue stored, with the
sibling complementary. The corrected invariant is what this pins.
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata

_DATA = pathlib.Path(__file__).parent / "data" / \
    "named_phase1_audit_2026-08-27.json"

_MONTHS = {"January": 1, "February": 2, "March": 3, "April": 4,
           "May": 5, "June": 6, "July": 7, "August": 8,
           "September": 9, "October": 10, "November": 11,
           "December": 12}

_Q_RE = re.compile(
    r"Who will win in the upcoming tennis event (.+?) vs (.+?) "
    r"scheduled for ([A-Z][a-z]+) (\d{1,2}), (\d{4}) at "
    r"(\d{1,2}):(\d{2}) (AM|PM) UTC\?")


def _fold(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s).encode(
        "ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def _name(s: str) -> str:
    return " ".join(_fold(s))


def _prefix_build(code: str, toks: list[str]) -> bool:
    # code splits as p1+p2: p1 a >=2-char prefix of the FIRST token,
    # p2 a >=2-char prefix of a LATER token — order-sensitive.
    for i in range(2, len(code) - 1):
        p1, p2 = code[:i], code[i:]
        if len(p2) < 2:
            break
        if toks and toks[0].startswith(p1) and any(
                t.startswith(p2) for t in toks[1:]):
            return True
    return False


def _audit_one(a: dict) -> list[str]:
    errs: list[str] = []
    m = _Q_RE.fullmatch(a["question"])
    if not m:
        return ["question shape"]
    qa, qb, mon, day, yr, hh, mm, _ap = m.groups()
    ident = a["identifier"].split("-")
    if (int(yr), _MONTHS[mon], int(day)) != (
            int(ident[4]), int(ident[5]), int(ident[6])):
        errs.append("date: question vs identifier")
    if a["his_slug"].split("-")[-3:] != ident[4:]:
        errs.append("date: his_slug vs identifier")
    # the clock quarantine's legitimacy: the poisoned line IS the
    # question clock's minutes, never a betting line
    if a["line_before"] != mm or a["clock"]["mm"] != mm \
            or a["clock"]["hh"] != hh.lstrip("0"):
        errs.append("clock/line mismatch")
    qpair = {_name(qa), _name(qb)}
    vpair = {_name(a["side_norm"]), _name(a["sibling_side"])}
    if qpair != vpair:
        errs.append("pair: question vs venue rows")
    if _name(a["outcome"]) != _name(a["side_norm"]):
        errs.append("selected row is not his pick")
    half = a["his_title"].rsplit(":", 1)[-1]
    hm = re.fullmatch(r"\s*(.+?) vs (.+?)\s*", half)
    if not hm or {_name(hm.group(1)), _name(hm.group(2))} != qpair:
        errs.append("his title half != pair")
    if {a["intent"], a["sibling_intent"]} != {
            "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"}:
        errs.append("intent pair not complementary")
    ca, cb = a["venue_codes"]
    if ident[2] != ca or ident[3] != cb:
        errs.append("identifier codes != venue_codes")
    if not _prefix_build(ca, _fold(qa)):
        errs.append(f"code {ca} !~ {qa}")
    if not _prefix_build(cb, _fold(qb)):
        errs.append(f"code {cb} !~ {qb}")
    all_toks = [t for n in (qa, qb) for t in _fold(n)]
    hc = a["his_slug"].split("-")[1:-3]
    if not all(any(s.startswith(c) or c.startswith(s)
                   for s in all_toks) for c in hc):
        errs.append(f"his codes {hc} do not corroborate names")
    if a["market_slug"] != a["identifier"] \
            or a["event_slug"] != a["identifier"][4:]:
        errs.append("slug family mismatch")
    if ident[1] not in ("itfme", "itfwo"):
        errs.append(f"family {ident[1]} unattested")
    return errs


def test_phase1_audit_is_zero_mismatch():
    snap = json.loads(_DATA.read_text())
    audits = snap["audits"]
    assert len(audits) == 40
    failures = {a["identifier"]: errs
                for a in audits if (errs := _audit_one(a))}
    assert failures == {}, failures


def test_audit_covers_both_polarities_and_many_markets():
    """The zero-mismatch verdict would be weak over a homogeneous
    sample; the census sample it certifies is not one."""
    snap = json.loads(_DATA.read_text())
    audits = snap["audits"]
    intents = {a["intent"] for a in audits}
    assert intents == {"ORDER_INTENT_BUY_LONG",
                       "ORDER_INTENT_BUY_SHORT"}
    assert len({a["identifier"] for a in audits}) >= 10


def test_census_facts_backing_the_itfwo_admission():
    """9b839de admitted itfwo citing these exact counts — the
    citation stays checkable against the stored census."""
    snap = json.loads(_DATA.read_text())
    assert snap["named_ml_reasons"]["ok"] == 62
    assert snap["lg_pairs"]["itf->itfwo"] == 112
