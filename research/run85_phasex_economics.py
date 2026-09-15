#!/usr/bin/env python3
"""Phase X economics: PAYS_1_IF, the equivalence gate, depth-aware VWAP,
and the fee state machine.

CONTACTS NOTHING. Pure functions over records.

THE ONE IDEA THIS FILE ENFORCES
A cross-venue arbitrage is a claim that two contracts pay the same dollar
on the same fact. Everything here exists to stop that claim being made on
anything weaker than the venues' own words:

  - PAYS_1_IF is assembled from explicit contract evidence with per-
    component provenance. A title is never evidence.
  - A side LABEL never sets economic direction. We have already seen a
    PMUS market where the venue's own orientation fields and its prose
    disagree, so labels are recorded and then not trusted.
  - Any unresolved material rule difference is EQUIVALENCE_NOT_IDENTIFIED,
    which is ineligible, not "probably fine".
  - A VWAP never walks past displayed depth, and a size the book cannot
    fill returns the fillable quantity, never an extrapolated price.
  - An unverified fee makes LOCKED_NET_PNL = NOT_IDENTIFIED_FEES. Gross
    is still reported, clearly labelled as gross.

The expected outcome of these rules is that MOST contracts fail the gate.
That is the rules working. A gate that passes everything is not a gate.
"""

from __future__ import annotations

import importlib.util
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

_s = importlib.util.spec_from_file_location(
    "px_kalshi", Path(__file__).with_name("run85_phasex_kalshi.py"))
K = importlib.util.module_from_spec(_s)
_s.loader.exec_module(K)

VERIFIED, QUOTED, ABSENT = K.VERIFIED, K.QUOTED, K.ABSENT
field, present, sentences = K.field, K.present, K.sentences

# A sentence that says which side the contract PAYS on. Without one, no
# amount of labelling establishes direction.
AFFIRMATIVE_MARKERS = (
    "resolves to yes", "resolve to yes", "settles to yes", "settle to yes",
    "settle to the winner", "settles to the winner", "pays out",
    "will settle to $1", "resolves yes", "market will settle to the winner",
)


# ===================================================== PAYS_1_IF =========
def _component(name: str, source_field: str, raw: Any,
               normalized: Any, status: str) -> dict:
    return {"FIELD": name, "SOURCE_FIELD": source_field, "RAW_VALUE": raw,
            "NORMALIZED_VALUE": normalized, "STATUS": status}


def pays_1_if(*, venue: str, contract_id: str, side_label: str | None,
              sibling_label: str | None, rules_prose: list[tuple[str, str]],
              subject: Any = None, line: Any = None, period: Any = None,
              settlement_source: Any = None) -> dict:
    """The explicit settlement proposition for ONE side of ONE contract.

    `rules_prose` is [(source_field, text)] -- the venue's own rules or
    description fields, verbatim.

    DIRECTION IS EARNED, NOT ASSUMED. PAYOFF_DIRECTION becomes VERIFIED
    only when the venue itself ties this side to the paying outcome: an
    affirmative settlement sentence exists, and EXACTLY ONE of the two
    side labels appears in it. If both appear -- which is what a
    moneyline's "settle to the winner of A vs B" does -- the prose does
    not say which token pays, and only the label would, so the answer is
    NOT_IDENTIFIED. If neither appears, likewise.

    That is deliberately strict, and it is the rule that would have
    caught asc-nfl-den-kc-2026-09-14-2h-pos-21pt5, whose `long` side is
    Denver while its description settles Yes on Kansas City.
    """
    comps: list[dict] = []
    blockers: list[str] = []

    comps.append(_component("SIDE_LABEL", "venue side label", side_label,
                            side_label,
                            VERIFIED if side_label else ABSENT))
    comps.append(_component("SIBLING_LABEL", "venue sibling side label",
                            sibling_label, sibling_label,
                            VERIFIED if sibling_label else ABSENT))
    for nm, src, val in (("SUBJECT", "venue subject field", subject),
                         ("LINE", "venue strike/line field", line),
                         ("PERIOD", "venue period/segment field", period),
                         ("SETTLEMENT_SOURCE", "venue settlement source",
                          settlement_source)):
        comps.append(_component(nm, src, val, val,
                                VERIFIED if val not in (None, "", [], {})
                                else ABSENT))

    if not rules_prose:
        blockers.append("NO_RULES_PROSE")
        prop = field(None, ABSENT, "no rules or description field supplied")
        affirm: list[dict] = []
    else:
        names = "; ".join(n for n, _ in rules_prose)
        prop = field([{"source": n, "text": t} for n, t in rules_prose],
                     QUOTED, names)
        affirm = [{"source": n, "text": s}
                  for n, t in rules_prose for s in sentences(t)
                  if any(m in s.lower() for m in AFFIRMATIVE_MARKERS)]
        if not affirm:
            blockers.append("NO_AFFIRMATIVE_SETTLEMENT_SENTENCE")

    direction = ABSENT
    anchor = None
    if affirm and side_label:
        joined = " ".join(a["text"].lower() for a in affirm)
        mine = side_label.lower() in joined
        theirs = bool(sibling_label) and sibling_label.lower() in joined
        if mine and not theirs:
            direction = VERIFIED
            anchor = [a for a in affirm
                      if side_label.lower() in a["text"].lower()]
        elif mine and theirs:
            blockers.append("BOTH_SIDE_LABELS_IN_AFFIRMATIVE_SENTENCE")
        else:
            blockers.append("SIDE_LABEL_ABSENT_FROM_AFFIRMATIVE_SENTENCE")
    elif affirm and not side_label:
        blockers.append("NO_SIDE_LABEL")

    comps.append(_component(
        "PAYOFF_DIRECTION", "affirmative settlement sentence vs side label",
        anchor, "PAYS_1_IF this side's proposition holds"
        if direction == VERIFIED else None, direction))

    return {
        "VENUE": venue,
        "CONTRACT_ID": contract_id,
        "SIDE_LABEL": side_label,
        "PAYOFF_DIRECTION": direction,
        "DIRECTION_ANCHOR": anchor,
        "DIRECTION_BLOCKERS": blockers,
        "PROPOSITION_TEXT": prop,
        "AFFIRMATIVE_SENTENCES": affirm,
        "COMPONENTS": comps,
        "ELIGIBLE_FOR_AUTOMATED_EQUIVALENCE": direction == VERIFIED,
    }


# ================================================== equivalence gate =====
MATERIAL_DIMENSIONS = (
    "SPORT", "LEAGUE", "UNDERLYING_EVENT", "PARTICIPANTS", "PROPOSITION",
    "LINE", "PERIOD", "START_TIME", "CLOSE_TIME", "SETTLEMENT_SOURCE",
    "SETTLEMENT_RULE", "OVERTIME", "EXTRA_TIME", "POSTPONEMENT",
    "CANCELLATION", "DRAW_TIE", "VOID", "OTHER_MATERIAL_CONDITIONS",
)

MATCH, MISMATCH, UNRESOLVED = "MATCH", "MISMATCH", "UNRESOLVED"

EQUIVALENCE_VERIFIED = "EQUIVALENCE_VERIFIED"
EQUIVALENCE_REJECTED = "EQUIVALENCE_REJECTED"
EQUIVALENCE_NOT_IDENTIFIED = "EQUIVALENCE_NOT_IDENTIFIED"


def compare_dimension(a: Any, b: Any) -> str:
    """Two dimension values -> MATCH / MISMATCH / UNRESOLVED.

    A dimension either side could not supply is UNRESOLVED, never MATCH.
    Two absences are not agreement: they are two silences, and a pair of
    silences is exactly how a rule difference hides.
    """
    a_missing = a in (None, "", [], {})
    b_missing = b in (None, "", [], {})
    if a_missing or b_missing:
        return UNRESOLVED
    if isinstance(a, str) and isinstance(b, str):
        return MATCH if a.strip().lower() == b.strip().lower() else MISMATCH
    try:
        if float(a) == float(b):
            return MATCH
        return MISMATCH
    except (TypeError, ValueError):
        return MATCH if a == b else MISMATCH


def equivalence(pmus_payoff: dict, kalshi_payoff: dict,
                dimensions: dict[str, tuple[Any, Any]]) -> dict:
    """Classify one candidate PMUS/Kalshi pair.

    VERIFIED requires ALL of:
      - both sides' PAYOFF_DIRECTION VERIFIED (a label-only pair cannot
        reach the gate at all), and
      - every material dimension MATCH.
    Any MISMATCH is REJECTED. Any UNRESOLVED -- including a dimension no
    venue published -- is NOT_IDENTIFIED. There is no fourth outcome and
    no override.
    """
    results: dict[str, str] = {}
    for dim in MATERIAL_DIMENSIONS:
        a, b = dimensions.get(dim, (None, None))
        results[dim] = compare_dimension(a, b)

    blockers: list[str] = []
    if pmus_payoff.get("PAYOFF_DIRECTION") != VERIFIED:
        blockers.append("PMUS_PAYOFF_DIRECTION_NOT_IDENTIFIED")
    if kalshi_payoff.get("PAYOFF_DIRECTION") != VERIFIED:
        blockers.append("KALSHI_PAYOFF_DIRECTION_NOT_IDENTIFIED")

    mismatches = [d for d, r in results.items() if r == MISMATCH]
    unresolved = [d for d, r in results.items() if r == UNRESOLVED]

    if mismatches:
        status = EQUIVALENCE_REJECTED
    elif blockers or unresolved:
        status = EQUIVALENCE_NOT_IDENTIFIED
    else:
        status = EQUIVALENCE_VERIFIED

    return {
        "EQUIVALENCE_STATUS": status,
        "DIMENSION_RESULTS": results,
        "MISMATCHED_DIMENSIONS": mismatches,
        "UNRESOLVED_DIMENSIONS": unresolved,
        "DIRECTION_BLOCKERS": blockers,
        "PMUS_PAYS_1_IF": pmus_payoff,
        "KALSHI_PAYS_1_IF": kalshi_payoff,
        "ELIGIBLE_FOR_ECONOMICS": status == EQUIVALENCE_VERIFIED,
    }


# ========================================================= depth VWAP ====
def vwap(levels: list[dict], want_qty: float) -> dict:
    """Walk ACTUAL displayed depth. Never past it.

    Returns FILLED_QTY (what the shown book supports, which may be less
    than want_qty) and DEPTH_EXHAUSTED. The VWAP describes FILLED_QTY and
    nothing larger: pricing a size the book cannot show is the single
    easiest way to invent an arbitrage that does not exist.
    """
    want = float(want_qty)
    filled = 0.0
    cost = 0.0
    walked: list[dict] = []
    for lv in levels:
        if filled >= want:
            break
        take = min(float(lv["QUANTITY"]), want - filled)
        if take <= 0:
            continue
        cost += take * float(lv["PRICE"])
        filled += take
        walked.append({"LEVEL": lv.get("LEVEL"), "PRICE": float(lv["PRICE"]),
                       "TAKEN": take})
    displayed = sum(float(lv["QUANTITY"]) for lv in levels)
    return {
        "REQUESTED_QTY": want,
        "FILLED_QTY": filled,
        "DEPTH_EXHAUSTED": filled < want - 1e-12,
        "DISPLAYED_QTY": displayed,
        "VWAP": (cost / filled) if filled > 0 else None,
        "NOTIONAL": cost,
        "LEVELS_WALKED": walked,
        "PRICES_EXTRAPOLATED": False,
    }


# ============================================================== fees =====
# PMUS: externally supplied and verified. Kalshi: OUR OWN SOURCE ONLY --
# a constant in edge-engine/src/edge/venues/kalshi.py:304, never read back
# from the venue, with no maker path, no rounding rule and no tier data.
FEE_REGISTRY: dict[str, dict] = {
    "PMUS": {
        "TAKER_FEE_FORMULA": {
            "value": "Fee = 0.06 * C * p * (1-p)",
            "SOURCE": "externally supplied venue fee rules",
            "EFFECTIVE_DATE": "2026-07-01",
            "VERIFICATION_STATUS": VERIFIED},
        "MAKER_FEE_FORMULA": {
            "value": "Fee = -0.0125 * C * p * (1-p)  (rebate)",
            "SOURCE": "externally supplied venue fee rules",
            "EFFECTIVE_DATE": "2026-07-01",
            "VERIFICATION_STATUS": VERIFIED},
        "ROUNDING_RULE": {
            "value": "banker's rounding to nearest $0.01",
            "SOURCE": "externally supplied venue fee rules",
            "EFFECTIVE_DATE": "2026-07-01",
            "VERIFICATION_STATUS": VERIFIED},
        "PER_FILL_VS_AGGREGATE_ROUNDING": {
            "value": "PER_FILL",
            "SOURCE": "externally supplied venue fee rules",
            "EFFECTIVE_DATE": "2026-07-01",
            "VERIFICATION_STATUS": VERIFIED},
        "VOLUME_TIERS": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "ACCOUNT_SPECIFIC_TIERS": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "REBATES": {
            "value": "maker rebate is the negative taker coefficient above",
            "SOURCE": "externally supplied venue fee rules",
            "EFFECTIVE_DATE": "2026-07-01",
            "VERIFICATION_STATUS": VERIFIED},
        "OTHER_TRANSACTION_COSTS": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
    },
    "KALSHI": {
        "TAKER_FEE_FORMULA": {
            "value": "KALSHI_FEE_FORMULA_FROM_BETTOR_SOURCE_ONLY: "
                     "0.07 * p * (1-p) per contract",
            "SOURCE": "edge-engine/src/edge/venues/kalshi.py:304 "
                      "(our own constant, not a venue response)",
            "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "MAKER_FEE_FORMULA": {
            "value": None,
            "SOURCE": "no maker-fee path exists in this repo",
            "EFFECTIVE_DATE": None, "VERIFICATION_STATUS": ABSENT},
        "ROUNDING_RULE": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "PER_FILL_VS_AGGREGATE_ROUNDING": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "VOLUME_TIERS": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "ACCOUNT_SPECIFIC_TIERS": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "REBATES": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
        "OTHER_TRANSACTION_COSTS": {
            "value": None, "SOURCE": None, "EFFECTIVE_DATE": None,
            "VERIFICATION_STATUS": ABSENT},
    },
}

# The components without which a NET number is a guess wearing a decimal
# point. Tiers and "other costs" are excluded deliberately: an absent
# tier table cannot make a fee LOWER than the base schedule, so it cannot
# turn a negative net positive.
FEE_COMPONENTS_REQUIRED_FOR_NET = (
    "TAKER_FEE_FORMULA", "ROUNDING_RULE", "PER_FILL_VS_AGGREGATE_ROUNDING",
)
NOT_IDENTIFIED_FEES = "NOT_IDENTIFIED_FEES"


def fee_readiness(venue: str, registry: dict | None = None) -> dict:
    reg = (registry or FEE_REGISTRY).get(venue.upper(), {})
    missing = [c for c in FEE_COMPONENTS_REQUIRED_FOR_NET
               if (reg.get(c) or {}).get("VERIFICATION_STATUS") != VERIFIED]
    return {"VENUE": venue.upper(), "MISSING_FOR_NET": missing,
            "NET_REPORTABLE": not missing, "REGISTRY": reg}


def _bankers_cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def pmus_taker_fee(contracts: float, price: float) -> Decimal:
    p = Decimal(str(price))
    return _bankers_cents(Decimal("0.06") * Decimal(str(contracts))
                          * p * (Decimal(1) - p))


# ============================================ cross-venue economics ======
def pair_economics(*, pmus_leg: dict, kalshi_leg: dict, qty: float,
                   registry: dict | None = None) -> dict:
    """Both legs at one size -> gross always, net only when fees allow.

    A leg is {"VWAP": ..., "FILLED_QTY": ..., "DEPTH_EXHAUSTED": ...} from
    vwap(). The hedgeable size is the SMALLER of the two fills: a hedge is
    only as big as its thinner leg, and sizing on the fatter one is how a
    two-leg trade becomes a one-leg position.
    """
    reg = registry or FEE_REGISTRY
    size = min(float(pmus_leg["FILLED_QTY"]), float(kalshi_leg["FILLED_QTY"]))
    out: dict[str, Any] = {
        "REQUESTED_QTY": qty,
        "MAX_HEDGEABLE_SIZE": size,
        "PMUS_DEPTH_EXHAUSTED": bool(pmus_leg["DEPTH_EXHAUSTED"]),
        "KALSHI_DEPTH_EXHAUSTED": bool(kalshi_leg["DEPTH_EXHAUSTED"]),
        "PMUS_VWAP": pmus_leg["VWAP"],
        "KALSHI_VWAP": kalshi_leg["VWAP"],
    }
    if size <= 0 or pmus_leg["VWAP"] is None or kalshi_leg["VWAP"] is None:
        out.update({"LOCKED_GROSS_PNL": None, "LOCKED_NET_PNL": None,
                    "REASON": "NO_COMMON_HEDGEABLE_SIZE"})
        return out

    pm_cost = size * float(pmus_leg["VWAP"])
    k_cost = size * float(kalshi_leg["VWAP"])
    out["PMUS_EXECUTION_COST"] = pm_cost
    out["KALSHI_EXECUTION_COST"] = k_cost
    out["TOTAL_ACQUISITION_COST_GROSS"] = pm_cost + k_cost
    # Complementary legs on equivalent contracts settle to exactly $1 per
    # pair, whichever way the event goes. That is the whole claim, and it
    # is valid ONLY because the equivalence gate ran first.
    out["GUARANTEED_SETTLEMENT_VALUE"] = size * 1.0
    out["LOCKED_GROSS_PNL"] = size * 1.0 - (pm_cost + k_cost)
    out["LOCKED_GROSS_BPS"] = (
        10_000.0 * out["LOCKED_GROSS_PNL"] / (pm_cost + k_cost)
        if (pm_cost + k_cost) > 0 else None)
    out["CAPITAL_REQUIRED"] = pm_cost + k_cost

    pm_ready = fee_readiness("PMUS", reg)
    k_ready = fee_readiness("KALSHI", reg)
    out["PMUS_FEE_READINESS"] = pm_ready["MISSING_FOR_NET"]
    out["KALSHI_FEE_READINESS"] = k_ready["MISSING_FOR_NET"]
    if not (pm_ready["NET_REPORTABLE"] and k_ready["NET_REPORTABLE"]):
        out["PMUS_FEES"] = None
        out["KALSHI_FEES"] = None
        out["LOCKED_NET_PNL"] = NOT_IDENTIFIED_FEES
        out["LOCKED_NET_BPS"] = NOT_IDENTIFIED_FEES
        out["NET_BLOCKED_BY"] = sorted(
            {"PMUS:%s" % m for m in pm_ready["MISSING_FOR_NET"]}
            | {"KALSHI:%s" % m for m in k_ready["MISSING_FOR_NET"]})
        return out

    pm_fee = float(pmus_taker_fee(size, float(pmus_leg["VWAP"])))
    k_fee = float(_kalshi_taker_fee(size, float(kalshi_leg["VWAP"]), reg))
    out["PMUS_FEES"] = pm_fee
    out["KALSHI_FEES"] = k_fee
    out["TOTAL_ACQUISITION_COST"] = pm_cost + k_cost + pm_fee + k_fee
    out["LOCKED_NET_PNL"] = (out["GUARANTEED_SETTLEMENT_VALUE"]
                             - out["TOTAL_ACQUISITION_COST"])
    out["LOCKED_NET_BPS"] = (10_000.0 * out["LOCKED_NET_PNL"]
                             / out["TOTAL_ACQUISITION_COST"]
                             if out["TOTAL_ACQUISITION_COST"] > 0 else None)
    return out


def _kalshi_taker_fee(contracts: float, price: float,
                      registry: dict) -> Decimal:
    """Only reachable once the Kalshi schedule is VERIFIED in the
    registry -- pair_economics returns NOT_IDENTIFIED_FEES before this is
    ever called otherwise. It stays a hard refusal rather than a default
    so that verifying the schedule is a deliberate act."""
    reg = registry.get("KALSHI", {})
    if (reg.get("TAKER_FEE_FORMULA") or {}).get(
            "VERIFICATION_STATUS") != VERIFIED:
        raise RuntimeError("Kalshi taker fee is not venue-verified; "
                           "LOCKED_NET_PNL must be NOT_IDENTIFIED_FEES")
    coeff = Decimal(str(reg["TAKER_FEE_FORMULA"].get("COEFFICIENT", "0.07")))
    p = Decimal(str(price))
    raw = coeff * Decimal(str(contracts)) * p * (Decimal(1) - p)
    return (_bankers_cents(raw)
            if reg.get("ROUNDING_RULE", {}).get("value", "").startswith(
                "banker") else raw)


# ====================================================== capture sync =====
def capture_sync(pmus_book: dict, kalshi_book: dict) -> dict:
    """How far apart the two observations actually are. Reported on every
    pair, never assumed to be zero."""
    pw = pmus_book.get("LOCAL_RECEIPT_WALL")
    kw = kalshi_book.get("LOCAL_RECEIPT_WALL")
    diff = abs(pw - kw) * 1000.0 if (pw is not None and kw is not None) \
        else None
    return {"PMUS_RECEIPT_TIME": pw, "KALSHI_RECEIPT_TIME": kw,
            "PMUS_VENUE_TIMESTAMP": pmus_book.get("VENUE_TIMESTAMP"),
            "KALSHI_VENUE_TIMESTAMP": kalshi_book.get("VENUE_TIMESTAMP"),
            "ABS_CAPTURE_DIFFERENCE_MS": diff,
            "SIMULTANEOUS": False,
            "NOTE": "asynchronous observations; never executable as one "
                    "instant"}
