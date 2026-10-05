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
               value the decision read off the valuation row -- or, when the
               decision DECLARES a venue conversion of that value (the NFL
               money line's tie conversion: `pin["venue_conversion"]` with
               the book's own number kept as `p_book_conditional_no_tie`),
               the book's number is the row's value and the converted p is
               re-derived below, never trusted.
  VENUE_CONVERSION  (only when one is declared) the converted p follows from
               the row's probability by the conversion's own stated formula
               p = (1 - t) p_book + payout t, at the end of its own declared
               tie-rate interval that LOWERS the held side's value, with t,
               the payout and the interval finite and inside [0, 1].
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
               short; the first level is that side's best TRADABLE price
               re-parsed from the raw book; the ladder is best first; and the
               book is not crossed (best bid above best offer). TRADABLE is
               the simulator's own rule: our adapter formats every price
               %.2f (bettor_paper_simulator ADAPTER_CENT_GRID), so a level
               whose wire price is not a whole cent -- the venue quotes MLB
               at a 0.005 tick and NFL at 0.001 -- cannot be taken by us and
               is not in the ladder. Such a level better than the first
               tradable one is NAMED on the receipt, never a refusal (review
               of 7bd084b: a 0.535 best offer had refused a correct 0.54
               ladder as SOFTWARE).
  FEE          the simulator's fee for one contract at the best level is a
               finite, non-negative amount no larger than the price.
  PINNACLE_AGE the Pinnacle reading was within its limit (30 s) at the
               decision instant -- the existing rule, unchanged: the age is
               known, not negative, not over the limit. The age when the
               edge was computed (after the book read) is RECORDED beside it
               for the record and gates nothing.
  BOOK_AGE     the observed book's receipt instant is known and the book is
               not older than the policy's book bound (a policy with no book
               bound -- Derek V2 reads its own book inside the decision --
               passes None: the age is recorded and only an unknown receipt
               instant fails); a stamp after the decision clock is recorded.

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
R_CONVERSION = "GROSS_EDGE_INPUT_VENUE_CONVERSION_NOT_REPRODUCIBLE"
REFUSALS = (R_PROBABILITY, R_ORIENTATION, R_ORIENTATION_UNVERIFIABLE,
            R_PRICE_SIDE, R_BOOK_CROSSED, R_FEE, R_PINNACLE_AGE, R_BOOK_AGE,
            R_CONVERSION)

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


def on_adapter_grid(wire) -> bool:
    """THE SIMULATOR'S CENT GRID (bettor_paper_simulator._cent, pinned equal
    by test): a wire price our adapter can send (it formats %.2f)."""
    return abs(round(float(wire) * 100) - float(wire) * 100) < 1e-9


def declared_conversion(pin: dict) -> dict | None:
    """The venue conversion the decision DECLARES it applied to the row's
    probability, or None: `pin["venue_conversion"]` with applies True and the
    book's own number kept on the pin (`p_book_conditional_no_tie`) -- the
    shape the NFL money line's tie conversion leaves (wt_r30a_nfl,
    paper_benchmark.apply_venue_conversion). Anything else declares none."""
    pin = pin or {}
    conv = pin.get("venue_conversion")
    if not isinstance(conv, dict) or conv.get("applies") is not True:
        return None
    if pin.get("p_book_conditional_no_tie") is None:
        return None
    return conv


#: THE ONE DECLARATION THE IDENTITY CHECK BELONGS TO (incident release,
#: verifier finding 6): bettor_ncaaf_settlement's VERSION and LEAGUE, pinned
#: equal by test (kept as constants so this module stays pure).
IDENTITY_CONVERSION_VERSION = "BETTOR_NCAAF_SETTLEMENT_V1"
IDENTITY_CONVERSION_LEAGUE = "cfb"


def league_token_of(row: dict) -> str | None:
    """The venue league token of the valuation row's contract: the second
    dash-separated part of its venue market slug (`aec-cfb-...` -> `cfb`),
    or None when the row carries no slug."""
    parts = str((row or {}).get("us_market_slug") or "").lower().split("-")
    return parts[1] if len(parts) > 2 and parts[1] else None


def conversion(*, p, p_book, conv: dict, league: str | None = None) -> dict:
    """Does the converted `p` follow from `p_book` by the declared
    conversion's own formula and worst-end rule? Re-derived. Pure.

    THE CHECK IS CHOSEN BY THE CONTRACT, NOT BY THE DECLARATION (verifier
    finding 6). The identity path was taken from the declaration's own
    fields (no interval, a tie probability present), so an NFL-shaped
    declaration carrying t = 0 and no interval would have skipped the NFL's
    worst-end re-derivation. It is now taken only for the NCAAF settlement's
    own declaration (`version`) on a row of the NCAAF venue league
    (`league`, from the row's venue slug); every other declaration takes the
    worst-end path, which refuses one with no interval."""
    if conv.get("version") == IDENTITY_CONVERSION_VERSION \
            and league == IDENTITY_CONVERSION_LEAGUE \
            and conv.get("tie_rate_interval") is None \
            and conv.get("tie_probability_completed") is not None:
        return _identity_conversion(p=p, p_book=p_book, conv=conv)
    t = _finite(conv.get("tie_rate_used"))
    pay = _finite(conv.get("tie_payout_per_contract"))
    iv = conv.get("tie_rate_interval")
    lo = hi = None
    if isinstance(iv, (list, tuple)) and len(iv) == 2:
        lo, hi = _finite(iv[0]), _finite(iv[1])
    detail = {"tie_rate_used": t, "tie_payout_per_contract": pay,
              "tie_rate_interval": [lo, hi], "p_book": p_book, "value": p,
              "formula": conv.get("formula")}
    sane = (all(x is not None and 0.0 <= x <= 1.0 for x in (t, pay, lo, hi))
            and lo <= hi + P_TOLERANCE
            and lo - P_TOLERANCE <= t <= hi + P_TOLERANCE)
    declared_book = _finite(conv.get("p_book_conditional_no_tie"))
    if not sane or declared_book is None \
            or abs(declared_book - float(p_book)) > P_TOLERANCE:
        return _check("VENUE_CONVERSION", False, R_CONVERSION,
                      why=("the declared conversion's tie rate, payout or "
                           "interval is not a valid [0, 1] quantity, the rate "
                           "is outside its own interval, or it names a book "
                           "probability that is not the row's"), **detail)

    def v(rate):
        return (1.0 - rate) * float(p_book) + pay * rate
    worst = min(v(lo), v(hi))
    expected = v(t)
    declared_p = _finite(conv.get("p"))
    ok = (abs(expected - worst) <= P_TOLERANCE
          and abs(float(p) - expected) <= P_TOLERANCE
          and (declared_p is None
               or abs(declared_p - float(p)) <= P_TOLERANCE))
    return _check("VENUE_CONVERSION", ok, R_CONVERSION, expected=expected,
                  worst_end_of_the_interval=worst,
                  why=("p is (1 - t) p_book + payout t at the end of the "
                       "declared interval that lowers the held side's value"
                       if ok else
                       "p does not follow from the row's probability by the "
                       "declared formula at the worst end of its interval"),
                  **detail)


def _identity_conversion(*, p, p_book, conv: dict) -> dict:
    """THE NCAAF MONEY LINE'S DECLARED CONVERSION (integration of inc-edge
    with the P0 incident NCAAF stream). bettor_ncaaf_settlement.convert
    declares a venue conversion with the SAME shape as the NFL's
    (`applies`, `p`, `p_book_conditional_no_tie`) but with no tie-rate
    interval: its formula is p_venue = (1 - t) p_book + 0 * t with
    t = `tie_probability_completed` = 0 by the cited rule that a completed
    college game cannot end tied, so p_venue = p_book. Re-derived here, never
    trusted: t must be exactly 0 (any other declared t has no interval to
    take a worst end from and is refused), the declared book number must be
    the row's, and the decision's p (and the declaration's own p) must equal
    the book's number. Pure."""
    t = _finite(conv.get("tie_probability_completed"))
    declared_book = _finite(conv.get("p_book_conditional_no_tie"))
    declared_p = _finite(conv.get("p"))
    detail = {"tie_probability_completed": t, "p_book": p_book, "value": p,
              "formula": conv.get("formula"), "p_is": conv.get("p_is")}
    ok = (t == 0.0 and declared_book is not None and p_book is not None
          and abs(declared_book - float(p_book)) <= P_TOLERANCE
          and abs(float(p) - float(p_book)) <= P_TOLERANCE
          and (declared_p is None
               or abs(declared_p - float(p)) <= P_TOLERANCE))
    return _check("VENUE_CONVERSION", ok, R_CONVERSION,
                  expected=None if p_book is None else float(p_book),
                  why=("a zero tie probability makes the conversion the "
                       "identity: p is the row's own probability"
                       if ok else
                       "the declared identity conversion does not hold: t is "
                       "not 0, or p / the declared book number is not the "
                       "row's probability"), **detail)


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
    raw_all = _side_levels(md, want)
    raw = [(p, q) for p, q in raw_all if on_adapter_grid(p)]
    if not raw:
        return [_check("PRICE_SIDE", False, R_PRICE_SIDE,
                       consumed_side=consumed_side, expected_side=want,
                       off_cent_levels=len(raw_all),
                       why="the raw book carries no tradable (whole-cent) "
                           "level on the consumed side to confirm the price "
                           "against")]
    best_px = max(p for p, _ in raw) if side == SHORT else \
        min(p for p, _ in raw)
    better = (lambda px: px > best_px + PRICE_TOLERANCE) if side == SHORT \
        else (lambda px: px < best_px - PRICE_TOLERANCE)
    off_ahead = sorted(({"price": round(p, 6), "qty": round(q, 6)}
                        for p, q in raw_all
                        if not on_adapter_grid(p) and better(p)),
                       key=lambda x: x["price"], reverse=(side == SHORT))
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
                  best_price_is="THE_BEST_TRADABLE_WHOLE_CENT_LEVEL",
                  off_cent_levels_better_than_the_first_tradable=off_ahead,
                  off_cent_levels_are=(
                      "levels whose wire price is not a whole cent: the "
                      "adapter cannot send that price (ADAPTER_CENT_GRID), "
                      "so they are named here and are not a defect"),
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
             edge_at: float, book_observed_at, book_max_age_s: float | None,
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
    # A DECLARED CONVERSION (review of 7bd084b): the NFL stream's tie
    # conversion replaces pin["p"] before the edge. The ROW's value is then
    # the book's number it kept, and the converted p is re-derived below.
    conv = declared_conversion(pin)
    p_row = _finite(pin.get("p_book_conditional_no_tie")) if conv else pf
    p_ok = (pf is not None and 0.0 < pf < 1.0
            and p_row is not None and 0.0 < p_row < 1.0
            and (stored is None or abs(stored - p_row) <= P_TOLERANCE))
    checks.append(_check("PROBABILITY", p_ok, R_PROBABILITY, value=pf,
                         stored_on_the_row=stored,
                         row_value_used=p_row,
                         converted=conv is not None,
                         why=("a probability strictly inside (0, 1), equal "
                              "to the valuation row's" + (
                                  " before its declared venue conversion"
                                  if conv else "") if p_ok else
                              "p is not a probability strictly inside "
                              "(0, 1), or is not the row's value (nor a "
                              "declared conversion of it)")))
    if p_ok and conv is not None:
        checks.append(conversion(p=pf, p_book=p_row, conv=conv,
                                 league=league_token_of(row)))
    if p_ok:
        checks.append(orientation(p=p_row, side=side, row=row))
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
    bound = None if book_max_age_s is None else float(book_max_age_s)
    ba_ok = age_rule is not None and (bound is None or age_rule <= bound)
    checks.append(_check(
        "BOOK_AGE", ba_ok, R_BOOK_AGE,
        value=None if age_rule is None else round(age_rule, 3),
        limit_s=bound,
        **({"no_book_age_rule_in_this_policy": (
            "recorded only: this policy reads its own book inside the "
            "decision and has no book-age bound; none is added here")}
           if bound is None else {}),
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


def summary(receipt: dict | None) -> dict | None:
    """The receipt as the policy decision carries it: version, verdict,
    refusals and each check's pass/fail."""
    if receipt is None:
        return None
    return {"version": receipt["version"], "ok": receipt["ok"],
            "refusals": list(receipt["refusals"]),
            "checks": {c["check"]: c["passed"] for c in receipt["checks"]}}


def describe() -> dict:
    return {"version": VERSION, "refusals": list(REFUSALS),
            "checks": ["PROBABILITY", "VENUE_CONVERSION", "ORIENTATION",
                       "PRICE_SIDE", "BOOK_NOT_CROSSED", "FEE",
                       "PINNACLE_AGE", "BOOK_AGE"],
            "p_tolerance": P_TOLERANCE,
            "clock_tolerance_s": CLOCK_TOLERANCE_S}
