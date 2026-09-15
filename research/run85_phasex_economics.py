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

# A sentence that states the settlement condition. Without one, no amount
# of labelling establishes anything.
AFFIRMATIVE_MARKERS = (
    "resolves to yes", "resolve to yes", "settles to yes", "settle to yes",
    "settle to the winner", "settles to the winner", "pays out",
    "will settle to $1", "resolves yes", "market will settle to the winner",
    "win the game", "wins the game", "win the match", "wins the match",
    "settle according to", "settles according to",
)

# Connectors that join two participants into a CONTEST rather than making
# one of them the subject of a clause.
MATCHUP_CONNECTORS = (" vs. ", " vs ", " v. ", " v ", " versus ", " at ",
                      " against ")

# PAYOFF_STATUS vocabulary. Every non-VERIFIED value names WHY, because
# "not identified" without a reason cannot be acted on.
PAYOFF_VERIFIED = "VERIFIED"
NOT_IDENTIFIED_SIDE_BINDING = "NOT_IDENTIFIED_SIDE_BINDING"
NOT_IDENTIFIED_CONTRADICTION = "NOT_IDENTIFIED_CONTRADICTION"
NOT_IDENTIFIED_SETTLEMENT_CONDITION = "NOT_IDENTIFIED_SETTLEMENT_CONDITION"
NOT_IDENTIFIED_AMBIGUOUS_SUBJECT = "NOT_IDENTIFIED_AMBIGUOUS_SUBJECT"
NOT_IDENTIFIED_EVENT = "NOT_IDENTIFIED_EVENT"

PROOF_COMPONENTS = ("UNDERLYING_EVENT", "OUTCOME_PARTICIPANT_BINDING",
                    "SIDE_TO_PAYOUT_BINDING", "SETTLEMENT_CONDITION",
                    "MATERIAL_SPECIAL_RULES")


def _matchup_spans(text: str, a: str, b: str) -> list[tuple[int, int]]:
    """Where `a` and `b` sit adjacent across a matchup connector.

    "the winner of the Canelo Alvarez vs. Christian Mbilli boxing match"
    names a CONTEST. Neither fighter is the subject of that clause, so the
    sentence states the settlement condition without stating orientation,
    and the venue's side field is what supplies it.
    """
    low, spans = text.lower(), []
    for x, y in ((a, b), (b, a)):
        for conn in MATCHUP_CONNECTORS:
            probe = x.lower() + conn + y.lower()
            start = low.find(probe)
            while start != -1:
                spans.append((start, start + len(probe)))
                start = low.find(probe, start + 1)
    return spans


def _outside_matchup(text: str, label: str,
                     spans: list[tuple[int, int]]) -> int | None:
    """First position where `label` occurs NOT inside a matchup phrase, or
    None. An occurrence outside a matchup is the label acting as a SUBJECT.
    """
    low, needle = text.lower(), label.lower()
    i = low.find(needle)
    while i != -1:
        if not any(s <= i < e for s, e in spans):
            return i
        i = low.find(needle, i + 1)
    return None


# ===================================================== PAYS_1_IF =========
def _component(name: str, source_field: str, raw: Any,
               normalized: Any, status: str) -> dict:
    return {"FIELD": name, "SOURCE_FIELD": source_field, "RAW_VALUE": raw,
            "NORMALIZED_VALUE": normalized, "STATUS": status}


def pays_1_if(*, venue: str, contract_id: str, side_label: str | None,
              sibling_label: str | None, rules_prose: list[tuple[str, str]],
              subject: Any = None, line: Any = None, period: Any = None,
              settlement_source: Any = None, event: Any = None,
              side_binding_fields: list[tuple[str, Any]] | None = None
              ) -> dict:
    """The explicit settlement proposition for ONE side of ONE contract,
    with the proof chain that establishes it.

    THE RULE. A label alone is never a payoff proof. But a label is not
    worthless either: when several AUTHORITATIVE venue fields agree, the
    chain can carry what no single field does. So direction comes from:

        UNDERLYING EVENT
      + OUTCOME / PARTICIPANT BINDING
      + SIDE-TO-PAYOUT BINDING          (what the venue says YES/LONG means)
      + SETTLEMENT CONDITION            (the venue's own settlement prose)
      + MATERIAL SPECIAL RULES
      + NO CONTRADICTION among them
      = VERIFIED

    THE DISTINCTION THAT MAKES THIS SAFE. A settlement sentence can name
    participants two ways, and they mean opposite things:

      CONTEST   "...settle to the winner of the Canelo Alvarez vs.
                 Christian Mbilli boxing match."
                Both names sit inside a matchup phrase. The sentence
                states WHAT settles, not WHICH side pays, so the side
                field supplies orientation and nothing contradicts it.
                -> VERIFIED through the chain.

      SUBJECT   "...settle to Yes if Kansas City Chiefs outscores Denver
                 Broncos by more than 21.5 points..."
                A name sits OUTSIDE the matchup, as the subject of the
                paying clause. Now the prose does speak to orientation --
                and on the real PMUS row this text belongs to, the venue's
                own `long` flag sits on DENVER.
                -> NOT_IDENTIFIED_CONTRADICTION. The contradiction is
                reported; neither field is elected correct.

    Word order is used only to find DISAGREEMENT, never agreement: when
    both labels act as subjects and the earliest is this side's, the
    result is NOT_IDENTIFIED_AMBIGUOUS_SUBJECT, because "A outscores B"
    and "B is outscored by A" are the same fact in opposite order and
    position cannot tell them apart.
    """
    proof: list[dict] = []
    blockers: list[str] = []

    def add(src: str, raw: Any, component: str, status: str) -> None:
        proof.append({"source_field": src, "raw_value": raw,
                      "proposition_component": component, "status": status})

    # -- 1. UNDERLYING EVENT -------------------------------------------
    add("venue event field", event, "UNDERLYING_EVENT",
        VERIFIED if event not in (None, "", [], {}) else ABSENT)

    # -- 2. OUTCOME / PARTICIPANT BINDING ------------------------------
    add("venue side label", side_label, "OUTCOME_PARTICIPANT_BINDING",
        VERIFIED if side_label else ABSENT)
    add("venue sibling side label", sibling_label,
        "OUTCOME_PARTICIPANT_BINDING",
        VERIFIED if sibling_label else ABSENT)
    for nm, val in (("venue subject field", subject),
                    ("venue strike/line field", line),
                    ("venue period/segment field", period),
                    ("venue settlement source", settlement_source)):
        add(nm, val, "MATERIAL_SPECIAL_RULES",
            VERIFIED if val not in (None, "", [], {}) else ABSENT)

    # -- 3. SIDE-TO-PAYOUT BINDING -------------------------------------
    # Authoritative fields whose declared meaning is "this is what the
    # paying side IS" -- yes_sub_title on Kalshi, the long marketSide on
    # PMUS. Several of them must agree with each other.
    bindings = list(side_binding_fields or [])
    if not bindings and side_label:
        bindings = [("venue side label", side_label)]
    for src, val in bindings:
        add(src, val, "SIDE_TO_PAYOUT_BINDING",
            VERIFIED if val not in (None, "", [], {}) else ABSENT)
    named = {str(v).strip().lower() for _s, v in bindings
             if v not in (None, "", [], {})}
    if not named:
        blockers.append("NO_SIDE_BINDING_FIELD")
    internal_conflict = len(named) > 1

    # -- 4. SETTLEMENT CONDITION ---------------------------------------
    names = "; ".join(n for n, _ in rules_prose)
    if not rules_prose:
        prop = field(None, ABSENT, "no rules or description field supplied")
        affirm: list[dict] = []
        blockers.append("NO_RULES_PROSE")
    else:
        prop = field([{"source": n, "text": t} for n, t in rules_prose],
                     QUOTED, names)
        affirm = [{"source": n, "text": s}
                  for n, t in rules_prose for s in sentences(t)
                  if any(m in s.lower() for m in AFFIRMATIVE_MARKERS)]
        if not affirm:
            blockers.append("NO_AFFIRMATIVE_SETTLEMENT_SENTENCE")
    for a in affirm:
        add(a["source"], a["text"], "SETTLEMENT_CONDITION", QUOTED)

    # -- 5. MATERIAL SPECIAL RULES -------------------------------------
    add(names or "none", bool(rules_prose), "MATERIAL_SPECIAL_RULES",
        VERIFIED if rules_prose else ABSENT)

    # -- resolve orientation against the settlement condition ----------
    status = ABSENT
    anchor = None
    if internal_conflict:
        status = NOT_IDENTIFIED_CONTRADICTION
        blockers.append("SIDE_BINDING_FIELDS_DISAGREE:%s"
                        % sorted(named))
    elif not named:
        status = NOT_IDENTIFIED_SIDE_BINDING
    elif not affirm:
        status = NOT_IDENTIFIED_SETTLEMENT_CONDITION
    elif event in (None, "", [], {}):
        status = NOT_IDENTIFIED_EVENT
        blockers.append("NO_UNDERLYING_EVENT_FIELD")
    else:
        bound = next(iter(named))
        joined = " ".join(a["text"] for a in affirm)
        spans = (_matchup_spans(joined, side_label or "", sibling_label)
                 if (side_label and sibling_label) else [])
        mine = _outside_matchup(joined, bound, spans)
        theirs = (_outside_matchup(joined, sibling_label, spans)
                  if sibling_label else None)
        in_text = bound in joined.lower() or (
            sibling_label or "").lower() in joined.lower()
        if mine is None and theirs is None:
            # A CONTEST: the sentence settles the matchup and says nothing
            # about orientation, so the side field is uncontradicted.
            if in_text:
                status = PAYOFF_VERIFIED
                anchor = affirm
            else:
                status = NOT_IDENTIFIED_SIDE_BINDING
                blockers.append("SIDE_LABEL_ABSENT_FROM_SETTLEMENT_SENTENCE")
        elif mine is not None and theirs is None:
            status = PAYOFF_VERIFIED           # subject IS this side
            anchor = affirm
        elif mine is None and theirs is not None:
            status = NOT_IDENTIFIED_CONTRADICTION
            blockers.append("SETTLEMENT_SUBJECT_IS_THE_SIBLING_SIDE")
        elif mine < theirs:
            status = NOT_IDENTIFIED_AMBIGUOUS_SUBJECT
            blockers.append("BOTH_LABELS_ACT_AS_SUBJECTS")
        else:
            status = NOT_IDENTIFIED_CONTRADICTION
            blockers.append("SETTLEMENT_SUBJECT_IS_THE_SIBLING_SIDE")

    add("settlement condition vs side-to-payout binding", anchor,
        "SIDE_TO_PAYOUT_BINDING",
        VERIFIED if status == PAYOFF_VERIFIED else status)

    missing = [c for c in PROOF_COMPONENTS
               if not any(p["proposition_component"] == c
                          and p["status"] in (VERIFIED, QUOTED)
                          for p in proof)]
    if missing and status == PAYOFF_VERIFIED:
        status = ABSENT
        blockers.append("PROOF_COMPONENTS_MISSING:%s" % missing)

    return {
        "VENUE": venue,
        "CONTRACT_ID": contract_id,
        "SIDE_LABEL": side_label,
        "PAYOFF_PROPOSITION": ("PAYS $1 IF: %s" % (side_label or "?"))
                              if status == PAYOFF_VERIFIED else None,
        "PAYOFF_STATUS": status,
        "PAYOFF_PROOF": proof,
        "PROOF_COMPONENTS_MISSING": missing,
        # kept so the equivalence gate's contract is unchanged
        "PAYOFF_DIRECTION": VERIFIED if status == PAYOFF_VERIFIED else ABSENT,
        "DIRECTION_ANCHOR": anchor,
        "DIRECTION_BLOCKERS": blockers,
        "PROPOSITION_TEXT": prop,
        "AFFIRMATIVE_SENTENCES": affirm,
        "COMPONENTS": proof,
        "ELIGIBLE_FOR_AUTOMATED_EQUIVALENCE": status == PAYOFF_VERIFIED,
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
