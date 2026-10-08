"""KALSHI GAME CONTRACT TERMS -- the venue's own rulebook clauses bound to
the canonical claim layer (2026-10-07).

THE FINDING (production 088af82): every Kalshi game alias was refused
UNKNOWN_STATES, so no Kalshi claim could ever be fingerprinted. The market
text ("If Cleveland wins the ... game ...", "resolve based on the official
final result") never states whether overtime / extra innings / the shootout
count, nor (MLB, NBA, NHL) what a tie pays; the settlement parser was right
not to invent either. Kalshi DOES state both -- in the series' contract terms
(GET /series/{ticker} -> contract_terms_url, the rulebook the market is
listed under), e.g. BASKETBALLGAMEWIN: "Where not specified otherwise, <time
period> shall be understood to refer to the sum of regulation time and any
officially designated overtime periods."

WHAT THIS MODULE BINDS, AND ONLY WHEN ALL HOLD:
  * the rulebook named by the series' contract_terms_url is one recorded
    here (research/kalshi_canonical_venue/CONTRACT_TERMS_2026-10-07: the PDF
    bytes, their text and sha256), AND the live PDF fetched by the Kalshi
    market-data worker hashes to the recorded sha256 (a changed rulebook is
    RULEBOOK_CHANGED_SINCE_RECORDING: nothing is applied);
  * the market is an ENTIRE-GAME contract ("If <team> wins the <game> game
    originally scheduled for ...", no period qualifier) -- the rulebook's
    default time period;
  * then overtime_included = True (the rulebook clause), and, where no Tie
    strike is listed for the event, the rulebook's fixed tie payout
    (football / basketball / baseball: $0.50; hockey states none -- "may
    resolve to No" is not a fixed payout -- so its tie state stays unknown).
  * The market's OWN explicit text is read first and wins: "If the game ends
    in a tie, the market will resolve to $0.50 for each team" (NFL), "(within
    two days)" / "within 2 days" (MLB, WNBA postponement window) with "over
    / further than two days ... fair price".
Every application is listed (clause, source) and folded into the rules
fingerprint the settlement certificate is keyed on, so a rulebook or
version change re-certifies. Pure; the fetch is the worker's, public, and
restricted to assets.kalshi.com/contract_terms. No order path, no
credential.
"""
from __future__ import annotations

import hashlib
import json
import re

from .settlement_rule_registry import LAST_FAIR_PRICE, SCALAR_0_50

VERSION = "KALSHI_GAME_CONTRACT_TERMS_V1"
RECORDED_AT = "2026-10-07"
SOURCE_DIR = "research/kalshi_canonical_venue/CONTRACT_TERMS_2026-10-07"
URL_RX = re.compile(
    r"^https://assets\.kalshi\.com/contract_terms/([A-Z0-9_]+)\.pdf$")
STATE_KEY = "kalshi_contract_terms"

R_NOT_RECORDED = "RULEBOOK_NOT_RECORDED"
R_CHANGED = "RULEBOOK_CHANGED_SINCE_RECORDING"
R_UNVERIFIED = "RULEBOOK_NOT_VERIFIED_LIVE"
R_NO_RULEBOOK = "SERIES_RULEBOOK_UNKNOWN"
R_NOT_ENTIRE_GAME = "NOT_AN_ENTIRE_GAME_CONTRACT"

#: the recorded rulebooks: PDF sha256 and the clauses applied, verbatim
RULEBOOKS = {
    "BASKETBALLGAMEWIN": {
        "pdf_sha256": "7db0904a1d578a2bca0f6a1f55d0d7962d32b76ce5fafb0e23"
                      "823e5444381556",
        "overtime_included": True,
        "tie_without_tie_strike": SCALAR_0_50,
        "clauses": {
            "overtime_included": (
                "Where not specified otherwise, <time period> shall be "
                "understood to refer to the sum of regulation time and any "
                "officially designated overtime periods."),
            "tie_without_tie_strike": (
                "If no “Tie” strike is listed, the Contract shall "
                "resolve such that \"Yes\" holders are paid $0.50, and \"No\" "
                "holders are paid $0.50.")}},
    "FOOTBALLGAMEWIN": {
        "pdf_sha256": "57b57e1ad96466e413e9274c0d2598225997e35d71262eb63c"
                      "4ae13858b81d38",
        "overtime_included": True,
        "tie_without_tie_strike": SCALAR_0_50,
        "clauses": {
            "overtime_included": (
                "Unless otherwise specified, all overtime periods are "
                "included in determining the winner."),
            "tie_without_tie_strike": (
                "If no “Tie” strike is listed, the Contract shall "
                "resolve such that \"Yes\" holders are paid $0.50, and \"No\" "
                "holders are paid $0.50.")}},
    "BASEBALLGAMEWIN": {
        "pdf_sha256": "46b02443153f4692acb3bac3d3aedabe93e837b08c80323013"
                      "c8dce117ebb6e7",
        "overtime_included": True,
        "tie_without_tie_strike": SCALAR_0_50,
        "clauses": {
            "overtime_included": (
                "Unless otherwise specified, all extra innings (or any "
                "equivalent tie-breaking procedure used by the governing "
                "body, such as the international tiebreaker rule) are "
                "included in determining the winner."),
            "tie_without_tie_strike": (
                "If a “Tie” strike is not listed for a given <time "
                "period> and <time period> of <baseball game> ends in a tie "
                "after all regulation and extra innings (unless specifically "
                "excluded), the market will resolve to $1/(the number of tied "
                "teams), rounded down. For example, if two teams tie, then "
                "each team strike will resolve to $0.50.")}},
    "HOCKEYWINNINGINPERIOD": {
        "pdf_sha256": "adf31742fa165efa8d0fd0fb6126520b07b283a26b4375dde1"
                      "25a5b06e641da5",
        "overtime_included": True,
        # "may resolve to No" is not a fixed payout: the tie state stays
        # unknown (fail closed)
        "tie_without_tie_strike": None,
        "clauses": {
            "overtime_included": (
                "Where not specified otherwise, <time period> shall be "
                "understood to refer to the sum of regulation time, overtime, "
                "and the shootout."),
            "tie_not_fixed": (
                "If <team> are drawn with their opponent (i.e. the goal "
                "differential is zero), the Contract for <team> may resolve "
                "to “No”, and a “Tie” strike, if present, "
                "may resolve “Yes”.")}},
}

_ENTIRE = re.compile(r"^\s*If\s+(?P<team>.+?)\s+wins\s+the\s+(?P<game>.+?)"
                     r"\s+game\s+originally\s+scheduled\s+for\s", re.I)
_PERIOD = re.compile(r"\b(?:half|halves|quarter|period|inning|innings|"
                     r"regulation|overtime|shootout|first|second|third|1st|"
                     r"2nd|3rd|4th|5th|set|map|round|series)\b", re.I)
_TIE = re.compile(r"\bif\s+the\s+game\s+ends\s+in\s+a\s+tie\b[^.]*?\bresolve"
                  r"\w*\s+to\s+\$?0?\.50\b", re.I)
_WINDOW = re.compile(r"\bwithin\s+(?:two|2)\s+days\b", re.I)
_BEYOND = re.compile(r"\b(?:over|further\s+than)\s+(?:two|2)\s+days\b[^.]*?"
                     r"\bfair\s+(?:market\s+)?price\b", re.I)


def rulebook_of(url) -> str | None:
    m = URL_RX.match(str(url or ""))
    return m.group(1) if m else None


def entire_game(rules_primary) -> bool:
    m = _ENTIRE.match(str(rules_primary or ""))
    return bool(m) and not _PERIOD.search(m.group("game"))


def market_clauses(primary, secondary) -> dict:
    """The market's own explicit statements this module reads (the shared
    parser does not): {term: (value, clause)}."""
    text = re.sub(r"\s+", " ", "%s %s" % (primary or "", secondary or ""))
    out = {}
    m = _TIE.search(text)
    if m:
        out["draw_rule"] = (SCALAR_0_50, m.group(0))
    w = _WINDOW.search(text)
    if w:
        out["postponement_window_hours"] = (48.0, w.group(0))
        b = _BEYOND.search(text)
        if b:
            out["postponement_payout"] = (LAST_FAIR_PRICE, b.group(0))
    return out


def bind(evidence: dict | None, *, rules_primary, rules_secondary,
         rulebook: str | None, observed_sha256: str | None,
         has_tie_strike: bool) -> dict:
    """The rules evidence with the venue's explicit market clauses and, when
    verified, its rulebook clauses applied; never removes or overrides a
    term the market text already states."""
    ev = dict(evidence or {})
    s = dict(ev.get("settlement") or {})
    applied, refused = [], []
    for k, (v, clause) in market_clauses(rules_primary,
                                         rules_secondary).items():
        if s.get(k) is None:
            s[k] = v
            applied.append({"term": k, "value": v, "source": "MARKET_TEXT",
                            "clause": clause})
    rb = RULEBOOKS.get(rulebook or "")
    if not rulebook:
        refused.append(R_NO_RULEBOOK)
    elif rb is None:
        refused.append("%s:%s" % (R_NOT_RECORDED, rulebook))
    elif not observed_sha256:
        refused.append(R_UNVERIFIED)
    elif observed_sha256 != rb["pdf_sha256"]:
        refused.append(R_CHANGED)
    elif not entire_game(rules_primary):
        refused.append(R_NOT_ENTIRE_GAME)
    else:
        if s.get("overtime_included") is None and rb["overtime_included"]:
            s["overtime_included"] = True
            applied.append({"term": "overtime_included", "value": True,
                            "source": "RULEBOOK:%s" % rulebook,
                            "clause": rb["clauses"]["overtime_included"]})
        tie = rb.get("tie_without_tie_strike")
        if tie and not has_tie_strike and s.get("draw_rule") is None:
            s["draw_rule"] = tie
            applied.append({"term": "draw_rule", "value": tie,
                            "source": "RULEBOOK:%s" % rulebook,
                            "clause": rb["clauses"]["tie_without_tie_strike"]})
    ev["settlement"] = s
    ev["contract_terms"] = {
        "version": VERSION, "rulebook": rulebook,
        "recorded_sha256": (rb or {}).get("pdf_sha256"),
        "observed_sha256": observed_sha256, "recorded_at": RECORDED_AT,
        "source_records": SOURCE_DIR, "applied": applied,
        "refused": refused}
    if applied:
        # the certificate fingerprint covers what was applied and from which
        # rulebook bytes: a change in either re-certifies
        ev["rules_sha256"] = hashlib.sha256(json.dumps(
            [ev.get("rules_sha256"), VERSION, rulebook, observed_sha256,
             [(a["term"], a["value"], a["source"]) for a in applied]],
            sort_keys=True, default=str).encode()).hexdigest()
    return ev


def fetch_rulebook(url: str, *, client=None, timeout_s: float = 20.0) -> dict:
    """GET one public rulebook PDF (assets.kalshi.com/contract_terms only)
    and hash it. Never raises."""
    name = rulebook_of(url)
    if name is None:
        return {"url": url, "status": "REFUSED_URL", "sha256": None}
    try:
        import httpx
        own = client is None
        c = client or httpx.Client(timeout=timeout_s, follow_redirects=False)
        try:
            r = c.get(url)
        finally:
            if own:
                c.close()
        if r.status_code != 200:
            return {"url": url, "rulebook": name, "status": "HTTP_%d"
                    % r.status_code, "sha256": None}
        body = r.content
        return {"url": url, "rulebook": name, "status": "OK",
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body)}
    except Exception as exc:                                    # noqa: BLE001
        return {"url": url, "rulebook": name, "status": type(exc).__name__,
                "sha256": None}
