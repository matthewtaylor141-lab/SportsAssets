"""Authoritative settlement-rule evidence for Polymarket US and Kalshi.

READ-ONLY / EVIDENCE-ONLY / NO ORDER OR CAPITAL AUTHORITY.

This module converts the CURRENT CONTRACT'S published rule text into a small,
reviewable settlement vocabulary. It never infers an unstated rule, never
promotes a family example into contract authority, and never overwrites an
explicit structured rule silently.

The examples in SOURCE_CATALOG define parser vocabulary/provenance only. A
market is price-authorized only when its own current rules/API fields are read.

INTEGRATION NOTES (BETTOR_Settlement_Rule_Registry_v1, built against
7341c039; applied onto claude/p0-closeout):

  * THE PARSER IS SENTENCE-SCOPED. The package matched each clause anywhere
    in the text (a "last fair market price" in a RETIREMENT sentence would
    have set the game-level void rule). A payout now attaches only to the
    condition stated in the SAME sentence, the rule
    bettor_settlement_terms.compare already applies.
  * EXTENDED ONLY FOR EXPLICIT STATEMENTS PRESENT IN REAL VENUE TEXT
    (tests/fixtures/pmus_*listing*.json, every description, pinned by
    tests/test_settlement_rule_registry_integration.py):
      - CFB "If a tied final score is reported and no official winner is
        declared, this market will not resolve automatically and will be
        reviewed ..."  -> special condition TIE_MANUAL_REVIEW (no payout);
      - NHL line markets "If a shootout determines the winner, the shootout
        will count as one goal for the winning team" -> special condition
        SHOOTOUT_COUNTS_AS_ONE_GOAL (shootout_included is NOT set: the
        shootout is graded as one goal, not as its own score);
      - MLB "If the game is shortened but an official final result is
        declared, the market will settle based on that result" -> special
        condition SHORTENED_OFFICIAL_RESULT_STANDS;
      - team totals "... it will be flagged for manual last-fair-market-price
        settlement" -> special condition
        POSTPONEMENT_MANUAL_LAST_FAIR_PRICE_REVIEW (no automatic payout is
        recorded: the text says a reviewer settles it);
      - soccer "at the end of / during 90 minutes plus stoppage time" ->
        overtime_included False (the graded interval is stated explicitly);
      - tennis "does not begin due to a walkover, withdrawal, or
        cancellation ... last fair market price" -> void_rule LAST_FAIR_PRICE
        AND the named special condition WALKOVER_WITHDRAWAL_FAIR_PRICE;
        "If a player retires ... otherwise ... last fair market price" ->
        RETIREMENT_UNSETTLED_COMPONENTS_FAIR_PRICE only.
  * A WALKOVER / FORFEIT IS NEVER FLATTENED into the generic void rule on
    Kalshi either: it is its own special condition; only an explicit
    cancellation / not-started clause sets void_rule.
  * SCALAR_0_50 (a tie settling at 0.50) stays its own value, distinct from
    STAKE_BACK, LAST_FAIR_PRICE, YES and NO (kalshi_mapping.RULE_PAYOFF maps
    it to the payout "0.5").
  * `enrich_contract(..., text_fields=...)` reads only the named rules
    fields; kalshi_mapping passes `rules_text` only, so a listing's prose
    fields (title, description) stay non-evidence in that module.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy

VERSION = "SETTLEMENT_RULE_REGISTRY_V1"
#: the parser version recorded beside every parse (market_plane_rules)
PARSER_VERSION = "SETTLEMENT_RULE_REGISTRY_V1+SENTENCE_SCOPED_R1"
POLYMARKET_US = "POLYMARKET_US"
KALSHI = "KALSHI"
ESTABLISHED = "ESTABLISHED"
PARTIAL = "PARTIAL"
ABSENT = "ABSENT"
CONFLICT = "CONFLICT"
STATUSES = (ESTABLISHED, PARTIAL, ABSENT, CONFLICT)
LAST_FAIR_PRICE = "LAST_FAIR_PRICE"
STAKE_BACK = "STAKE_BACK"
SCALAR_0_50 = "SCALAR_0_50"

# named special conditions (never flattened into a generic payout)
SC_TIE_SCALAR = "TIE_SCALAR_0_50"
SC_TIE_MANUAL = "TIE_MANUAL_REVIEW"
SC_SHOOTOUT_ONE_GOAL = "SHOOTOUT_COUNTS_AS_ONE_GOAL"
SC_SHORTENED_OFFICIAL = "SHORTENED_OFFICIAL_RESULT_STANDS"
SC_MANUAL_LFMP = "POSTPONEMENT_MANUAL_LAST_FAIR_PRICE_REVIEW"
SC_WALKOVER = "WALKOVER_WITHDRAWAL_FAIR_PRICE"
SC_RETIREMENT = "RETIREMENT_UNSETTLED_COMPONENTS_FAIR_PRICE"
SC_DNP = "DNP_OR_NONSTARTER_FAIR_PRICE"
SC_SCRATCH = "SCRATCH_FAIR_PRICE"
SC_MULTI_WINNER = "MULTI_WINNER_OR_DEAD_HEAT_STATED"
SPECIAL_CONDITIONS = (SC_TIE_SCALAR, SC_TIE_MANUAL, SC_SHOOTOUT_ONE_GOAL,
                      SC_SHORTENED_OFFICIAL, SC_MANUAL_LFMP, SC_WALKOVER,
                      SC_RETIREMENT, SC_DNP, SC_SCRATCH, SC_MULTI_WINNER)

AUTHORITY = "EVIDENCE_ONLY_NO_ORDER_OR_CAPITAL_AUTHORITY"

SOURCE_CATALOG = {
    "kalshi_get_market_api": {
        "url": "https://docs.kalshi.com/api-reference/market/get-market",
        "facts": (
            "GET /trade-api/v2/markets/{ticker} is a public market read",
            "market object exposes rules_primary and rules_secondary",
        ),
    },
    "kalshi_market_rules_help": {
        "url": "https://help.kalshi.com/en/articles/13823822-market-rules",
        "facts": ("each market has its own rules",),
    },
    "kalshi_market_outcomes_help": {
        "url": "https://help.kalshi.com/en/articles/13823826-market-outcomes",
        "facts": ("contract terms define determination and information source",),
    },
    "pmus_nfl_example": {
        "url": "https://polymarket.us/sports/nfl/nfl-was-sf-2026-10-19",
        "facts": ("overtime included", "tie 0.50", "two-week reschedule", "last fair market price"),
    },
    "pmus_nhl_example": {
        "url": "https://polymarket.us/sports/nhl/nhl-fla-la-2026-10-06",
        "facts": ("overtime and shootout included", "two-calendar-day reschedule", "last fair market price"),
    },
    "pmus_mlb_example": {
        "url": "https://polymarket.us/sports/mlb/mlb-laa-sf-2026-07-25",
        "facts": ("extra innings included", "two-week reschedule", "last fair market price"),
    },
    "pmus_nba_example": {
        "url": "https://polymarket.us/sports/nba/nba-mia-tor-2026-10-03",
        "facts": ("overtime included", "two-calendar-day reschedule", "last fair market price"),
    },
    "kalshi_tennis_example": {
        "url": "https://kalshi.com/markets/kxatpexactmatch/atp-exact-match-score/kxatpexactmatch-26sep29etctsi",
        "facts": ("pre-start cancellation fair price", "retirement partial/fair-price handling", "two-week reschedule"),
    },
}


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def fingerprint(text) -> str | None:
    t = _flat(text)
    return hashlib.sha256(t.encode()).hexdigest() if t else None


def _sentences(text: str) -> list[str]:
    """Sentences, lower-cased. A boundary is a full stop followed by space
    (so '$0.50' and '2.5' stay inside their sentence). A semicolon does NOT
    end the rule: "If a player retires ...; otherwise ... last fair market
    price" is one condition and its payout."""
    t = _flat(text).lower()
    return [s.strip() for s in re.split(r"(?<=\.)\s+", t) if s.strip()]


def _window_hours(text: str):
    low = _flat(text).lower()
    if re.search(r"\bwithin\s+two\s+calendar\s+days\b", low) or re.search(r"\bwithin\s+48\s+hours\b", low):
        return 48.0
    if re.search(r"\bwithin\s+two\s+weeks\b", low) or re.search(r"\bwithin\s+14\s+days\b", low):
        return 336.0
    return None


def _verification_sources(text: str) -> list[str]:
    out = []
    for pat in (
        r"outcome\s+(?:is\s+)?verified\s+from\s+([^.;]+)",
        r"outcome\s+sourced\s+from\s+([^.;]+)",
    ):
        m = re.search(pat, _flat(text), re.I)
        if m:
            v = m.group(1).strip()
            if v and v not in out:
                out.append(v)
    return out


_POSTPONE = r"\bpostpon\w*|\bdelay\w*|\bsuspend\w*|\breschedul\w*"
_LFMP = r"\blast\s+fair\s+market\s+price\b"
_LFMP_MANUAL = r"\bmanual\s+last[-\s]fair[-\s]market[-\s]price\b"


def polymarket_us_rule_evidence(rules_text, *, sport_family=None,
                                sports_market_type=None, period=None) -> dict:
    """Parse explicit statements in this Polymarket US contract's own rules."""
    text = _flat(rules_text)
    low = text.lower()
    sents = _sentences(text)
    settlement, special, matched = {}, [], []

    # Specific exclusion first; a period warning must not be overwritten by
    # the event-level sentence that full-game OT is included.
    if re.search(r"\bovertime\s+does\s+not\s+count\b", low):
        settlement["overtime_included"] = False
        matched.append("OVERTIME_EXCLUDED_EXPLICIT")
    elif (re.search(r"\bovertime\s+and\s+(?:any\s+)?(?:penalty\s+)?shootouts?\s+(?:is|are|will\s+be)\s+included\b", low)):
        settlement["overtime_included"] = True
        settlement["shootout_included"] = True
        matched.append("OVERTIME_AND_SHOOTOUT_INCLUDED")
    elif re.search(r"\bovertime\s+(?:is|are|will\s+be)\s+included\b", low):
        settlement["overtime_included"] = True
        matched.append("OVERTIME_INCLUDED")
    elif re.search(r"\bextra\s+innings\s+(?:are|is|will\s+be)\s+included\b", low):
        settlement["overtime_included"] = True
        settlement["extra_innings_included"] = True
        matched.append("EXTRA_INNINGS_INCLUDED")
    elif re.search(r"\b(?:at\s+the\s+end\s+of|during)\s+90\s+minutes\s+plus\s+stoppage\s+time\b", low):
        # the graded interval is stated: regulation plus stoppage time only
        settlement["overtime_included"] = False
        matched.append("REGULATION_90_MINUTES_PLUS_STOPPAGE_ONLY")

    for s in sents:
        if re.search(r"\bshootout\s+determines\s+the\s+winner\b", s) and \
                re.search(r"\bcount\s+as\s+one\s+goal\b", s):
            special.append(SC_SHOOTOUT_ONE_GOAL)
            matched.append("SHOOTOUT_COUNTS_AS_ONE_GOAL")
        if re.search(r"\bgame\s+ends?\s+in\s+a\s+tie\b|\bends?\s+in\s+a\s+tie\b", s) and \
                re.search(r"\bsettle\w*\s+(?:to|at)\s+\$?0?\.50?\b", s):
            settlement["draw_rule"] = SCALAR_0_50
            special.append(SC_TIE_SCALAR)
            matched.append("TIE_SETTLES_0_50")
        if re.search(r"\btied\s+final\s+score\b", s) and \
                re.search(r"\bnot\s+resolve\s+automatically\b|\bwill\s+be\s+reviewed\b", s):
            special.append(SC_TIE_MANUAL)
            matched.append("TIE_NO_OFFICIAL_WINNER_MANUAL_REVIEW")
        if re.search(r"\bshortened\b", s) and \
                re.search(r"\bofficial\s+final\s+result\b", s) and \
                re.search(r"\bsettle\s+based\s+on\s+that\s+result\b", s):
            special.append(SC_SHORTENED_OFFICIAL)
            matched.append("SHORTENED_OFFICIAL_RESULT_STANDS")
        postpone = bool(re.search(_POSTPONE, s))
        if postpone:
            wh = _window_hours(s)
            if wh is not None and "postponement_window_hours" not in settlement:
                settlement["postponement_window_hours"] = wh
                matched.append("POSTPONEMENT_WINDOW")
        if re.search(_LFMP_MANUAL, s):
            special.append(SC_MANUAL_LFMP)
            matched.append("MANUAL_LAST_FAIR_MARKET_PRICE_REVIEW")
            continue
        if not re.search(_LFMP, s):
            continue
        if re.search(r"\bretire\w*", s):
            special.append(SC_RETIREMENT)
            matched.append("RETIREMENT_FAIR_PRICE")
        elif re.search(r"\bwalkover\b|\bwithdraw\w*|\bcancel\w*|\bdoes\s+not\s+begin\b", s):
            settlement["void_rule"] = LAST_FAIR_PRICE
            if re.search(r"\bwalkover\b|\bwithdraw\w*", s):
                special.append(SC_WALKOVER)
            matched.append("NOT_BEGUN_OR_CANCELLED_FAIR_PRICE")
        elif postpone:
            settlement["postponement_payout"] = LAST_FAIR_PRICE
            settlement["void_rule"] = LAST_FAIR_PRICE
            matched.append("LAST_FAIR_MARKET_PRICE")

    special = sorted(set(special))
    status = ESTABLISHED if (settlement or special) else (PARTIAL if text else ABSENT)
    return {
        "version": VERSION, "parser_version": PARSER_VERSION,
        "venue": POLYMARKET_US, "status": status,
        "sport_family": sport_family, "sports_market_type": sports_market_type,
        "period": period, "rules_sha256": fingerprint(text),
        "settlement": settlement, "special_conditions": special,
        "verification_sources": _verification_sources(text), "matched": matched,
        "authority": AUTHORITY,
    }


def kalshi_rules_text(market: dict) -> tuple[str, str]:
    m = dict(market or {})
    return _flat(m.get("rules_primary")), _flat(m.get("rules_secondary"))


def kalshi_rules_sha256(market: dict) -> str | None:
    """ONE fingerprint over the whole rule block (primary + secondary)."""
    p, s = kalshi_rules_text(market)
    if not p and not s:
        return None
    return hashlib.sha256(("%s\x1f%s" % (p, s)).encode()).hexdigest()


def kalshi_rule_evidence(market: dict) -> dict:
    """Parse Kalshi's authoritative per-market rules_primary/secondary."""
    market = dict(market or {})
    primary, secondary = kalshi_rules_text(market)
    text = _flat(primary + " " + secondary)
    settlement, special, matched = {}, [], []
    for s in _sentences(text):
        if re.search(r"\bin\s+the\s+event\s+of\s+a\s+tie\b", s) and \
                re.search(r"\b(?:resolve|settle)\w*\s+(?:at|to)\s+(?:50\s*c|50\s*cents|\$?0?\.50?)\b", s):
            settlement["draw_rule"] = SCALAR_0_50
            special.append(SC_TIE_SCALAR)
            matched.append("TIE_SETTLES_0_50")
        if re.search(r"\bdead\s+heat\b|\bmultiple\s+winners\b|\bmore\s+than\s+one\s+winner\b", s):
            special.append(SC_MULTI_WINNER)
            matched.append("MULTI_WINNER_STATED")
        wh = _window_hours(s)
        if wh is not None and "postponement_window_hours" not in settlement and \
                re.search(_POSTPONE + r"|\bnot\s+started\b|\bbegins?\b", s):
            settlement["postponement_window_hours"] = wh
            matched.append("POSTPONEMENT_WINDOW")
        if not re.search(r"\bfair\s+(?:market\s+)?price\b", s):
            continue
        if re.search(r"\bscratch\w*", s):
            special.append(SC_SCRATCH)
            matched.append("SCRATCH_FAIR_PRICE")
        if re.search(r"\bnever\s+takes?\s+a\s+snap\b|\bnot\s+(?:a\s+)?starting\s+pitcher\b|"
                     r"\bnot\s+(?:in|included\s+in)\s+the\s+starting\s+lineup\b|"
                     r"\bdoes\s+not\s+play\b|\bdid\s+not\s+play\b|\bdnp\b", s):
            special.append(SC_DNP)
            matched.append("PARTICIPATION_FAIR_PRICE")
        if re.search(r"\bretire\w*", s):
            special.append(SC_RETIREMENT)
            matched.append("RETIREMENT_FAIR_PRICE")
            continue
        if re.search(r"\bwalkover\b|\bforfeit\w*", s):
            special.append(SC_WALKOVER)
            matched.append("WALKOVER_OR_FORFEIT_FAIR_PRICE")
        if re.search(r"\bcancel\w*|\bnot\s+started\b|\bdoes\s+not\s+occur\b", s):
            settlement["void_rule"] = LAST_FAIR_PRICE
            matched.append("CANCELLED_OR_NOT_STARTED_FAIR_PRICE")
        if re.search(_POSTPONE + r"|\bnot\s+started\s+within\b", s) and \
                (wh is not None or settlement.get("postponement_window_hours") is not None):
            settlement["postponement_payout"] = LAST_FAIR_PRICE
            matched.append("POSTPONEMENT_FAIR_PRICE")

    sources = _verification_sources(text)
    special = sorted(set(special))
    status = ESTABLISHED if settlement or special else (PARTIAL if text else ABSENT)
    return {
        "version": VERSION, "parser_version": PARSER_VERSION,
        "venue": KALSHI, "status": status,
        "ticker": market.get("ticker"), "event_ticker": market.get("event_ticker"),
        "rules_primary_sha256": fingerprint(primary),
        "rules_secondary_sha256": fingerprint(secondary),
        "rules_sha256": kalshi_rules_sha256(market),
        "rules_primary_present": bool(primary),
        "rules_secondary_present": bool(secondary),
        "settlement": settlement, "special_conditions": special,
        "verification_sources": sources, "matched": matched,
        "settlement_timer_seconds": market.get("settlement_timer_seconds"),
        "settlement_value_dollars": market.get("settlement_value_dollars"),
        "settlement_ts": market.get("settlement_ts"),
        "authority": AUTHORITY,
        "api_contract": SOURCE_CATALOG["kalshi_get_market_api"]["url"],
    }


PMUS_TEXT_FIELDS = ("rules_text", "description", "assetPriceTerms")


def enrich_contract(contract: dict, *, venue: str,
                    text_fields=PMUS_TEXT_FIELDS) -> dict:
    """Fill only missing structured fields; explicit contradictions fail closed."""
    out = deepcopy(contract or {})
    existing = dict(out.get("settlement") or {})
    if venue == POLYMARKET_US:
        text = next((out.get(f) for f in text_fields if out.get(f)), None)
        evidence = polymarket_us_rule_evidence(
            text, sport_family=out.get("sport_family"),
            sports_market_type=out.get("sports_market_type"), period=out.get("period"))
    elif venue == KALSHI:
        evidence = kalshi_rule_evidence(out)
    else:
        out["settlement_rule_evidence"] = {"version": VERSION, "venue": venue,
                                           "status": ABSENT, "why": "UNKNOWN_VENUE"}
        return out
    conflicts = {}
    for k, v in evidence["settlement"].items():
        if k in existing and existing[k] is not None and existing[k] != v:
            conflicts[k] = {"explicit": existing[k], "parsed": v}
        elif existing.get(k) is None:
            existing[k] = v
    out["settlement"] = existing
    if conflicts:
        evidence = dict(evidence, status=CONFLICT, conflicts=conflicts)
    out["settlement_rule_evidence"] = evidence
    return out


def describe() -> dict:
    return {"version": VERSION, "parser_version": PARSER_VERSION,
            "venues": [POLYMARKET_US, KALSHI], "source_catalog": SOURCE_CATALOG,
            "special_conditions": list(SPECIAL_CONDITIONS),
            "default": "ABSENT_OR_PARTIAL_NEVER_GUESSED",
            "authority": AUTHORITY}
