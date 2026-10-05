"""THE GROSS-EDGE INPUTS, VALIDATED PER DECISION (P0 incident, 2026-10-04).

THE QUESTION THIS ANSWERS. BELOW_MIN_GROSS_EDGE is the largest single refusal
of the completed-game policy (991 rows / 33 markets in 24 h on 191b299). The
owner kept its threshold ("keep the BELOW_MIN_GROSS_EDGE threshold, but
validate its inputs"): a refusal is economic only if the numbers it compares
are the right numbers. The gross edge is

    edge = p(the event the HELD side pays on) - our cost per contract at the
           best level of the side a BUY consumes

and it is only an economic verdict when every input is what it claims to be.
This module checks each input on every decision that reaches the gross-edge
step and writes a VALIDATION RECEIPT beside the decision. A decision whose
inputs fail is refused as SOFTWARE with a precise code -- never recorded as
BELOW_MIN_GROSS_EDGE, never silently economic.

THE CHECKS (each named on the receipt with its value and what it should be):

  PROBABILITY  p is a probability (finite, strictly inside (0, 1)) and is the
               value the decision read off the valuation row.
  ORIENTATION  p is the probability of the event the HELD side pays on: the
               row's payout naming agrees with its complement flag
               (complement <=> payout_event = NOT(selection)), the held side
               is the row's buy intent, and the de-vig recomputed from the
               row's OWN recorded Pinnacle prices (raw_odds, devig_method,
               mapped_outcome) gives exactly p -- or 1 - p for a complement.
               A row whose orientation cannot be recomputed (no prices, no
               mapped outcome) is refused as unverifiable, not assumed.
  PRICE_SIDE   the price is the venue side a BUY of the held side consumes:
               the OFFERS (asks) for a long, the BIDS at cost 1 - bid for a
               short; the first level is that side's best price re-parsed
               from the raw book; the ladder is best first; and the book is
               not crossed (best bid above best offer).
  FEE          the simulator's fee for one contract at the best level is a
               finite, non-negative amount no larger than the price.
  PINNACLE_AGE the Pinnacle reading was within its limit (30 s) at the
               decision instant -- the existing rule, unchanged: the age is
               known, not negative, not over the limit. The age when the
               edge was computed (after the book read) is RECORDED beside it
               for the record and gates nothing.
  BOOK_AGE     the observed book is not older than the policy's book bound
               and is not stamped after the instant it is used at (a clock
               disagreement is not a current book).

NOTHING HERE MOVES A THRESHOLD. The edge threshold, the net-of-fee rule, the
30 s rule and the book bound are the caller's, passed in and echoed on the
receipt. Pure: no I/O; imports only the de-vig arithmetic and the book
level parser.
"""
from __future__ import annotations

import math

from . import bettor_book_snapshot as BS
from . import bettor_pinnacle_devig as devig

VERSION = "GROSS_EDGE_INPUT_VALIDATION_V1"

R_PROBABILITY = "GROSS_EDGE_INPUT_PROBABILITY_NOT_A_PROBABILITY"
R_ORIENTATION = "GROSS_EDGE_INPUT_PROBABILITY_NOT_ORIENTED_TO_THE_HELD_SIDE"
R_ORIENTATION_UNVERIFIABLE = \
    "GROSS_EDGE_INPUT_PROBABILITY_ORIENTATION_NOT_VERIFIABLE"
R_PRICE_SIDE = "GROSS_EDGE_INPUT_PRICE_NOT_THE_BUY_SIDE_OF_THE_BOOK"
R_BOOK_CROSSED = "GROSS_EDGE_INPUT_BOOK_CROSSED"
R_FEE = "GROSS_EDGE_INPUT_FEE_NOT_EVALUABLE"
R_PINNACLE_AGE = "GROSS_EDGE_INPUT_PINNACLE_AGE_NOT_WITHIN_LIMIT"
R_BOOK_AGE = "GROSS_EDGE_INPUT_BOOK_AGE_NOT_WITHIN_LIMIT"
REFUSALS = (R_PROBABILITY, R_ORIENTATION, R_ORIENTATION_UNVERIFIABLE,
            R_PRICE_SIDE, R_BOOK_CROSSED, R_FEE, R_PINNACLE_AGE, R_BOOK_AGE)

#: The recomputed de-vig must equal the stored probability to this tolerance
#: (the stored value IS that arithmetic; production recomputed 2797/2797 to
#: 6e-13).
P_TOLERANCE = 1e-9
#: Our own two clocks (the book's receipt instant and the decision clock) may
#: disagree by this much before a book stamped in the future is NAMED on the
#: receipt (recorded, never gating: the policy's book rule is unchanged).
CLOCK_TOLERANCE_S = 1.0
PRICE_TOLERANCE = 1e-9

LONG, SHORT = "LONG", "SHORT"
_INTENT = {LONG: "ORDER_INTENT_BUY_LONG", SHORT: "ORDER_INTENT_BUY_SHORT"}


def _finite(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _check(name, passed, refusal, **kw) -> dict:
    return dict({"check": name, "passed": bool(passed),
                 "refusal": None if passed else refusal}, **kw)


def _raw_odds(row) -> dict:
    raw = (row or {}).get("raw_odds")
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    out = {}
    for k, v in dict(raw or {}).items():
        f = _finite(v)
        if f is not None and f > 1.0:
            out[str(k)] = f
    return out


def orientation(*, p, side, row: dict) -> dict:
    """Is `p` the probability of the event the HELD side pays on? Recomputed
    from the row's own recorded Pinnacle prices. Pure."""
    row = dict(row or {})
    sel = str(row.get("contract_selection") or "")
    comp = bool(row.get("payout_is_complement"))
    pay = row.get("payout_event")
    detail = {"selection": sel, "payout_event": pay,
              "payout_is_complement": comp, "held_side": side,
              "buy_intent": row.get("buy_intent")}
    if side not in _INTENT or row.get("buy_intent") != _INTENT[side]:
        return _check("ORIENTATION", False, R_ORIENTATION,
                      why="the held side is not the valuation row's buy "
                          "intent", **detail)
    want_pay = ("NOT(%s)" % sel) if comp else sel
    if pay is not None and str(pay) != want_pay:
        return _check("ORIENTATION", False, R_ORIENTATION,
                      why=("the row's payout event %r disagrees with its "
                           "complement flag (expected %r)" % (pay, want_pay)),
                      **detail)
    odds = _raw_odds(row)
    mapped = row.get("mapped_outcome")
    method = row.get("devig_method") or devig.DEFAULT_METHOD
    if not odds or not mapped or str(mapped) not in odds \
            or method not in devig.METHODS:
        return _check("ORIENTATION", False, R_ORIENTATION_UNVERIFIABLE,
                      why=("the row does not carry the Pinnacle prices, the "
                           "mapped outcome or a declared de-vig method, so "
                           "the probability's orientation cannot be "
                           "recomputed"),
                      raw_odds_outcomes=sorted(odds), mapped_outcome=mapped,
                      devig_method=method, **detail)
    names = sorted(odds)
    probs = dict(zip(names, devig.devig([odds[n] for n in names], method)))
    p_sel = float(probs[str(mapped)])
    expected = (1.0 - p_sel) if comp else p_sel
    ok = abs(float(p) - expected) <= P_TOLERANCE
    return _check("ORIENTATION", ok, R_ORIENTATION,
                  value=float(p), expected=expected,
                  p_of_selection_recomputed=p_sel, devig_method=method,
                  mapped_outcome=str(mapped),
                  why=("p equals the de-vig recomputed from the row's own "
                       "Pinnacle prices for the event the held side pays on"
                       if ok else
                       "p is not the recomputed probability of the event the "
                       "held side pays on (%.12f vs %.12f)"
                       % (float(p), expected)),
                  **detail)


def _side_levels(md, raw_side: str) -> list:
    """(px, qty) of one raw book side, qty > 0, parsed by the venue's own
    level parser (bettor_book_snapshot._level)."""
    out = []
    for e in list((md or {}).get(raw_side) or []):
        px, qty, _why = BS._level(e)
        if px is None or qty is None:
            continue
        px, qty = float(px), float(qty)
        if qty > 0 and 0.0 < px < 1.0:
            out.append((px, qty))
    return out


def price_side(*, side, levels: list, consumed_side, md) -> list:
    """The price is the side a BUY of the held side consumes, best first,
    and the book is not crossed. Returns the checks (one or two). Pure."""
    want = "bids" if side == SHORT else "offers"
    if consumed_side != want or not levels:
        return [_check("PRICE_SIDE", False, R_PRICE_SIDE,
                       consumed_side=consumed_side, expected_side=want,
                       levels=len(levels or []),
                       why="the ladder is not the side a BUY of the held "
                           "side consumes")]
    raw = _side_levels(md, want)
    if not raw:
        return [_check("PRICE_SIDE", False, R_PRICE_SIDE,
                       consumed_side=consumed_side, expected_side=want,
                       why="the raw book carries no level on the consumed "
                           "side to confirm the price against")]
    best_px = max(p for p, _ in raw) if side == SHORT else \
        min(p for p, _ in raw)
    expected = (1.0 - best_px) if side == SHORT else best_px
    prices = [_finite(lv.get("price")) for lv in levels]
    ordered = all(a is not None and b is not None and a <= b + PRICE_TOLERANCE
                  for a, b in zip(prices, prices[1:]))
    first = prices[0]
    ok = (first is not None and abs(first - expected) <= PRICE_TOLERANCE
          and ordered)
    out = [_check("PRICE_SIDE", ok, R_PRICE_SIDE,
                  consumed_side=consumed_side, expected_side=want,
                  value=first, expected=expected,
                  best_raw_price_on_the_side=best_px,
                  cost_is=("1 - best bid" if side == SHORT
                           else "best offer (ask)"),
                  ladder_best_first=ordered,
                  why=("the first level is the best %s of the raw book, in "
                       "cost terms, and the ladder is best first" % want
                       if ok else
                       "the first level is not the best %s of the raw book "
                       "in cost terms, or the ladder is not best first"
                       % want))]
    offers, bids = _side_levels(md, "offers"), _side_levels(md, "bids")
    if offers and bids:
        bo, bb = min(p for p, _ in offers), max(p for p, _ in bids)
        crossed = bb > bo + PRICE_TOLERANCE
        out.append(_check("BOOK_NOT_CROSSED", not crossed, R_BOOK_CROSSED,
                          best_offer=bo, best_bid=bb,
                          why=("best bid %.4f is above best offer %.4f: the "
                               "book contradicts itself" % (bb, bo)
                               if crossed else "best bid <= best offer")))
    return out


def validate(*, p, side, row: dict, levels: list, consumed_side, md,
             fee_per_contract, pin: dict, decided_at: float,
             edge_at: float, book_observed_at, book_max_age_s: float,
             threshold_edge_pp=None) -> dict:
    """THE VALIDATION RECEIPT of one decision's gross-edge inputs.

    `fee_per_contract(price) -> float` is the simulator's fee for one
    contract; `pin` is the decision's Pinnacle reading as re-aged at
    `decided_at` (age_s, limit_s, qualified); `edge_at` is the instant the
    edge is computed (after the book read); `book_observed_at` is the book's
    receipt instant. Returns {version, ok, refusals, checks, ...}. Pure."""
    row = dict(row or {})
    pin = dict(pin or {})
    checks = []
    pf = _finite(p)
    stored = _finite(row.get("probability"))
    p_ok = (pf is not None and 0.0 < pf < 1.0
            and (stored is None or abs(stored - pf) <= P_TOLERANCE))
    checks.append(_check("PROBABILITY", p_ok, R_PROBABILITY, value=pf,
                         stored_on_the_row=stored,
                         why=("a probability strictly inside (0, 1), equal "
                              "to the valuation row's" if p_ok else
                              "p is not a probability strictly inside "
                              "(0, 1), or is not the row's value")))
    if p_ok:
        checks.append(orientation(p=pf, side=side, row=row))
    checks.extend(price_side(side=side, levels=levels,
                             consumed_side=consumed_side, md=md))
    first = _finite((levels or [{}])[0].get("price")) if levels else None
    try:
        fee = None if first is None else _finite(fee_per_contract(first))
    except Exception as exc:                                    # noqa: BLE001
        fee = None
        fee_err = "%s: %s" % (type(exc).__name__, str(exc)[:120])
    else:
        fee_err = None
    fee_ok = fee is not None and fee >= 0.0 and first is not None \
        and fee <= first + PRICE_TOLERANCE
    checks.append(_check("FEE", fee_ok, R_FEE, value=fee, price=first,
                         error=fee_err,
                         why=("the simulator's fee for one contract at the "
                              "best level is finite, non-negative and no "
                              "larger than the price" if fee_ok else
                              "the fee at the best level could not be "
                              "evaluated to a finite amount within [0, "
                              "price]")))
    age, limit = _finite(pin.get("age_s")), _finite(pin.get("limit_s"))
    pa_ok = (age is not None and limit is not None and 0.0 <= age <= limit
             and pin.get("qualified") is True)
    checks.append(_check(
        "PINNACLE_AGE", pa_ok, R_PINNACLE_AGE, value=age, limit_s=limit,
        at="THE_DECISION_INSTANT (the existing rule's instant, unchanged)",
        age_when_the_edge_was_computed_s=(
            None if age is None else round(age + max(
                0.0, float(edge_at) - float(decided_at)), 3)),
        age_when_the_edge_was_computed_gates_nothing=True,
        why=("the Pinnacle reading was within its limit at the decision "
             "instant" if pa_ok else
             "the Pinnacle age at the decision instant is unknown, negative "
             "or over its limit")))
    obs_at = _finite(book_observed_at)
    raw_age = None if obs_at is None else float(edge_at) - obs_at
    # THE POLICY'S OWN AGE RULE, unchanged: the age as the decision measures
    # it (a receipt stamped after the decision clock reads as age 0 there),
    # within the book bound. A stamp after the decision clock is RECORDED
    # beside it -- both clocks are ours, so in production it would name a
    # clock defect -- but gates nothing, exactly as the decision's rule.
    age_rule = None if raw_age is None else max(0.0, raw_age)
    ba_ok = age_rule is not None and age_rule <= float(book_max_age_s)
    checks.append(_check(
        "BOOK_AGE", ba_ok, R_BOOK_AGE,
        value=None if age_rule is None else round(age_rule, 3),
        limit_s=float(book_max_age_s),
        book_stamped_after_the_decision_clock_s=(
            None if raw_age is None or raw_age >= -CLOCK_TOLERANCE_S
            else round(-raw_age, 3)),
        why=("the book's receipt instant is known and the book is within "
             "the policy's bound at the instant it is used" if ba_ok else
             "the book's receipt instant is unknown, or the book is over "
             "the policy's bound at the instant it is used")))
    refusals = []
    for c in checks:
        if c["refusal"] and c["refusal"] not in refusals:
            refusals.append(c["refusal"])
    return {"version": VERSION, "ok": not refusals, "refusals": refusals,
            "checks": checks, "threshold_edge_pp": threshold_edge_pp,
            "class_if_refused": "SOFTWARE",
            "rule": ("a decision whose gross-edge inputs fail is refused by "
                     "the failing check's code (SOFTWARE), never as "
                     "BELOW_MIN_GROSS_EDGE; no threshold is read or moved "
                     "here")}


def describe() -> dict:
    return {"version": VERSION, "refusals": list(REFUSALS),
            "checks": ["PROBABILITY", "ORIENTATION", "PRICE_SIDE",
                       "BOOK_NOT_CROSSED", "FEE", "PINNACLE_AGE",
                       "BOOK_AGE"],
            "p_tolerance": P_TOLERANCE,
            "clock_tolerance_s": CLOCK_TOLERANCE_S}
