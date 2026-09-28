"""WHAT AN INDIRECT PAIR IS WORTH, VALUED ON THE FIXTURE'S OUTCOMES.

An INDIRECT pair is two DISTINCT contracts on the SAME fixture -- long the home
moneyline and long the away moneyline, say. It is not the YES/NO pair
`bettor_pair_engine` values: on this venue buying the opposite side of one market
reduces the same book, so that is netting, and netting has no second settling
holding to value. Two distinct contracts DO settle independently, which is the
whole reason this structure exists and the whole reason it needs its own valuation.

── THE MISTAKE THIS MODULE IS BUILT AROUND ───────────────────────────
Two legs covering "both teams" feels like a locked position, and on a two-outcome
fixture with equal quantities it is: whichever side wins, one leg pays its
quantity. So the worst case is `min(q_primary, q_hedge)` against the total cost,
and if that is positive the structure cannot lose.

ON A THREE-OUTCOME FIXTURE IT IS NOT. A football match can be drawn, and two
moneyline legs pay NOTHING on a draw. The same two legs that are locked on an
NBA game are a total loss on a Premier League game. Nothing about the legs
themselves distinguishes those cases -- only the FIXTURE'S OUTCOME SPACE does.

So this module REFUSES TO VALUE A STRUCTURE WHOSE OUTCOME SPACE IS NOT
ESTABLISHED. Not "assumes two", not "defaults to the common case": refuses, by
name, with the reason. An unestablished outcome space is the difference between a
hedge and an unhedged double stake, and guessing it is how a strategy that
believes it is locked discovers otherwise at settlement.

── WORST CASE IS THE DECISION VARIABLE, NOT EXPECTED VALUE ───────────
The owner's standing rule is that historical or simulated results do not
establish a forward outcome. So the number this module leads with is the one that
does not depend on any probability at all: the payout in the WORST outcome the
fixture admits, minus what the structure cost including fees. That is arithmetic
on the venue's own settlement terms, not a forecast.

Probabilities appear only where a caller supplies them, only labelled as the
caller's own, and never blended into the worst case.

── BUCKETS ARE NEVER SUMMED INTO ONE NUMBER ──────────────────────────
Following `bettor_pair_engine` and for the reason the Ferrari result gave: its
merge economics were +$3.84M and its settled residual was -$4.44M. An engine
reporting only the total would have hidden both terms. Acquisition cost, fees,
per-outcome payouts, incremental capital and depth are reported separately.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER, and nothing here decides that a
pair may be acquired. It values and it ranks; the reservation machine and the
funded book are what would act, under authority this module does not grant.
"""

from __future__ import annotations

VERSION = "FUNDED_INDIRECT_PAIR_V1"

NOT_ESTABLISHED = "NOT_ESTABLISHED"

#: ── THE OUTCOME SPACE, WHICH MUST COME FROM EVIDENCE ─────────────────
#: A fixture's settling outcomes are a property of the competition's rules and
#: the venue's own settlement terms. They are supplied, with the source that
#: established them, or the valuation is refused.
OUTCOME_SPACE_ESTABLISHED_BY = (
    "VENUE_SETTLEMENT_TERMS",       # the venue states how the market resolves
    "COMPETITION_RULES_CAPTURED",   # the competition's own rules, captured
)

#: Every action the ranking may return. `ACQUIRE_HEDGE` is a RECOMMENDATION and
#: never an authorisation: acting on it requires a reservation, a funded grant and
#: the owner's approval, none of which this module can give.
ACTION_HOLD = "HOLD"
ACTION_ACQUIRE_HEDGE = "ACQUIRE_HEDGE"
ACTION_REDUCE = "REDUCE"
ACTION_EXIT = "EXIT"
ACTIONS = (ACTION_HOLD, ACTION_ACQUIRE_HEDGE, ACTION_REDUCE, ACTION_EXIT)

R_OUTCOME_SPACE_NOT_ESTABLISHED = "THE_FIXTURES_OUTCOME_SPACE_IS_NOT_ESTABLISHED"
R_OUTCOME_SOURCE_NOT_RECOGNISED = "THAT_IS_NOT_AN_ESTABLISHED_OUTCOME_SOURCE"
R_LEGS_ARE_THE_SAME_CONTRACT = "THE_TWO_LEGS_ARE_THE_SAME_CONTRACT"
R_NO_PRIMARY_LEG = "THERE_IS_NO_PRIMARY_LEG_TO_PAIR"
R_LEG_PAYS_ON_NO_OUTCOME = "A_LEG_PAYS_ON_NO_OUTCOME_THIS_FIXTURE_ADMITS"
R_DEPTH_NOT_ESTABLISHED = "THE_BOOKS_DEPTH_AT_THAT_PRICE_IS_NOT_ESTABLISHED"
R_PRICE_NOT_ESTABLISHED = "THE_COMPLEMENTS_PRICE_IS_NOT_ESTABLISHED"
R_FEES_NOT_PRICED = "THE_FEE_SCHEDULE_WOULD_NOT_PRICE_THIS"

#: WHY A STRUCTURE IS OR IS NOT LOCKED. Named values, because "hedged" is the
#: word that hides the three-outcome case.
COVER_ALL_OUTCOMES_PAY = "EVERY_SETTLING_OUTCOME_PAYS_AT_LEAST_ONE_LEG"
COVER_SOME_OUTCOMES_PAY_NOTHING = "AT_LEAST_ONE_SETTLING_OUTCOME_PAYS_NOTHING"


def describe() -> dict:
    return {
        "version": VERSION,
        "structure": ("two DISTINCT contracts on one fixture, settling "
                      "independently. NOT the YES/NO pair, which on this venue "
                      "is netting on a single book"),
        "actions": list(ACTIONS),
        "outcome_space_established_by": list(OUTCOME_SPACE_ESTABLISHED_BY),
        "leads_with": ("the payout in the WORST outcome the fixture admits, "
                       "minus total cost including fees -- arithmetic on the "
                       "venue's settlement terms, not a forecast"),
        "refuses_to": ("value a structure whose outcome space is not "
                       "established. Two moneyline legs are locked on a "
                       "two-outcome fixture and a total loss on a drawn one, "
                       "and only the outcome space tells them apart"),
        "never_blends": ("acquisition cost, fees, per-outcome payouts, "
                         "incremental capital and depth stay separate"),
        "acquire_hedge_is": ("a RECOMMENDATION. It is not an authorisation and "
                             "this module cannot grant one"),
        "this_module_sends_nothing": True,
    }


def outcome_space(*, fixture: str, outcomes, established_by: str,
                  evidence: dict | None = None) -> dict:
    """DECLARE THE SETTLING OUTCOMES OF A FIXTURE, with what established them.

    `outcomes` is the complete list of mutually exclusive settling results -- for
    a two-way fixture `["HOME", "AWAY"]`, for a three-way `["HOME","DRAW","AWAY"]`.
    A VOID is not listed here: it is not a settling outcome, it returns the
    collateral on what is still held, and `bettor_funded_management` already
    prices it from the remaining basis.

    Refused rather than defaulted, because a default is the bug.
    """
    out = {"version": VERSION, "fixture": str(fixture),
           "established_by": established_by}
    if established_by not in OUTCOME_SPACE_ESTABLISHED_BY:
        return dict(out, ok=False, refusal=R_OUTCOME_SOURCE_NOT_RECOGNISED,
                    recognised=list(OUTCOME_SPACE_ESTABLISHED_BY),
                    why=("the outcome space decides whether two legs are a "
                         "hedge or a double stake, so it is taken only from a "
                         "source that states how the market resolves"))
    named = [str(o) for o in (outcomes or []) if str(o).strip()]
    if len(named) < 2 or len(set(named)) != len(named):
        return dict(out, ok=False, refusal=R_OUTCOME_SPACE_NOT_ESTABLISHED,
                    outcomes=named,
                    why=("a settling outcome space needs at least two distinct "
                         "outcomes; %r is not one" % (named,)))
    return dict(out, ok=True, refusal=None, outcomes=named,
                count=len(named), evidence=dict(evidence or {}),
                void_is_not_listed=("a void is not a settling outcome; it "
                                    "returns collateral on what is still held"))


def _leg(role, *, slug, qty, price_paid, pays_on):
    return {"leg_role": role, "us_market_slug": str(slug),
            "qty": float(qty), "price_paid": float(price_paid),
            "pays_on": [str(o) for o in (pays_on or [])]}


def leg(role, *, slug, qty, price_paid, pays_on):
    """A LEG, AND THE OUTCOMES IT PAYS ON.

    `pays_on` is the subset of the fixture's outcomes in which this contract
    settles at 1. It is stated per leg rather than inferred from the slug,
    because inferring it means parsing a market name -- and a mis-parse here
    turns an unhedged position into one the system believes is covered.
    """
    return _leg(role, slug=slug, qty=qty, price_paid=price_paid,
                pays_on=pays_on)


def value_the_structure(*, space: dict, legs, fee_usd=None,
                        fee_basis: str | None = None) -> dict:
    """WHAT THE LEGS PAY IN EVERY OUTCOME, AND WHAT THE WORST ONE IS.

    No probabilities. The payout in each settling outcome is the sum of the
    quantities of the legs that pay in it; the cost is what was paid plus fees.
    The worst case is the minimum over outcomes. That is arithmetic, and it is
    the number a bounded real-money pilot can actually be sized against.
    """
    out: dict = {"version": VERSION}
    if not space.get("ok"):
        return dict(out, ok=False,
                    refusal=space.get("refusal",
                                      R_OUTCOME_SPACE_NOT_ESTABLISHED),
                    why=space.get("why"))
    outcomes = list(space["outcomes"])
    legs = [dict(x) for x in (legs or [])]
    if not legs:
        return dict(out, ok=False, refusal=R_NO_PRIMARY_LEG,
                    why="there are no legs to value")
    slugs = [x["us_market_slug"] for x in legs]
    if len(set(slugs)) != len(slugs):
        return dict(out, ok=False, refusal=R_LEGS_ARE_THE_SAME_CONTRACT,
                    slugs=slugs,
                    why=("two legs on one contract is netting on a single book, "
                         "not an indirect pair with two settling holdings"))
    for x in legs:
        unknown = [o for o in x["pays_on"] if o not in outcomes]
        if unknown:
            return dict(out, ok=False, refusal=R_LEG_PAYS_ON_NO_OUTCOME,
                        leg=x["us_market_slug"], unknown_outcomes=unknown,
                        fixture_outcomes=outcomes,
                        why=("a leg that pays on an outcome the fixture does "
                             "not admit is not described correctly, and its "
                             "payout cannot be computed"))
        if not x["pays_on"]:
            return dict(out, ok=False, refusal=R_LEG_PAYS_ON_NO_OUTCOME,
                        leg=x["us_market_slug"],
                        why=("this leg pays in no outcome, so it is not a "
                             "settling holding"))

    acquisition_usd = round(sum(x["qty"] * x["price_paid"] for x in legs), 6)
    fees = None if fee_usd is None else round(float(fee_usd), 6)
    total_cost = (None if fees is None
                  else round(acquisition_usd + fees, 6))

    payouts = {}
    for o in outcomes:
        payouts[o] = round(sum(x["qty"] for x in legs if o in x["pays_on"]), 6)
    worst_outcome = min(payouts, key=lambda o: payouts[o])
    best_outcome = max(payouts, key=lambda o: payouts[o])

    uncovered = [o for o in outcomes if payouts[o] <= 0]
    coverage = (COVER_ALL_OUTCOMES_PAY if not uncovered
                else COVER_SOME_OUTCOMES_PAY_NOTHING)

    res: dict = dict(
        out, ok=True, refusal=None,
        fixture=space["fixture"],
        outcomes=outcomes,
        outcome_space_established_by=space["established_by"],
        legs=[{"leg_role": x["leg_role"], "us_market_slug": x["us_market_slug"],
               "qty": x["qty"], "price_paid": x["price_paid"],
               "pays_on": x["pays_on"]} for x in legs],
        acquisition_usd=acquisition_usd,
        fees_usd=fees,
        fee_basis=fee_basis or NOT_ESTABLISHED,
        total_cost_usd=total_cost,
        payout_by_outcome=payouts,
        worst_outcome=worst_outcome,
        worst_payout_usd=payouts[worst_outcome],
        best_outcome=best_outcome,
        best_payout_usd=payouts[best_outcome],
        outcomes_paying_nothing=uncovered,
        coverage=coverage,
        buckets_are_not_summed=("acquisition, fees and per-outcome payouts are "
                               "separate; no single figure stands for the "
                               "structure"),
    )
    if total_cost is None:
        # NO FEE, NO VERDICT. A worst case computed without fees is optimistic by
        # exactly the fees, and this structure's whole claim is a small positive
        # margin -- which is the size at which fees decide the sign.
        return dict(res, worst_case_usd=None,
                    verdict_is_withheld=R_FEES_NOT_PRICED,
                    why=("the worst case is withheld until fees are priced: at "
                         "this structure's margins the fee is what decides the "
                         "sign"))
    res["worst_case_usd"] = round(payouts[worst_outcome] - total_cost, 6)
    res["best_case_usd"] = round(payouts[best_outcome] - total_cost, 6)
    res["cannot_lose"] = bool(res["worst_case_usd"] > 0
                              and coverage == COVER_ALL_OUTCOMES_PAY)
    res["why_the_worst_case_is_the_headline"] = (
        "it depends on no probability at all -- only on the venue's settlement "
        "terms and what was paid")
    if uncovered:
        res["warning"] = (
            "outcome(s) %r pay NOTHING. Two legs that look like a hedge are an "
            "unhedged double stake on a fixture that admits an outcome neither "
            "covers" % (uncovered,))
    return res


def incremental_capital_usd(*, hedge_qty, hedge_price, hedge_fee_usd=None
                            ) -> dict:
    """WHAT ACQUIRING THE HEDGE WOULD ACTUALLY COST, which is not the pair's
    total. The primary leg is already paid for; the decision in front of the
    system is the NEW money, and comparing a total against a headroom that has
    already absorbed the first leg double-counts it."""
    cash = round(float(hedge_qty) * float(hedge_price), 6)
    fee = None if hedge_fee_usd is None else round(float(hedge_fee_usd), 6)
    return {
        "version": VERSION,
        "cash_usd": cash,
        "fee_usd": fee,
        "incremental_capital_usd": None if fee is None else round(cash + fee, 6),
        "why": ("the primary leg is already funded; only the new money is a "
                "decision, and counting the pair's total against headroom that "
                "already holds the first leg double-counts it"),
    }


def depth_supports(*, wanted_qty, depth_qty_at_price=None) -> dict:
    """CAN THE BOOK ACTUALLY SUPPLY THE HEDGE.

    ABSENT DEPTH IS NOT INFINITE DEPTH. A hedge that only half fills leaves the
    primary leg naked for the unfilled part while the accounting believes the
    pair is complete -- which is the specific way this structure fails. So an
    unknown depth is refused rather than assumed, and a partial is reported as a
    partial with the shortfall named.
    """
    if depth_qty_at_price is None:
        return {"version": VERSION, "ok": False,
                "refusal": R_DEPTH_NOT_ESTABLISHED,
                "wanted_qty": float(wanted_qty),
                "why": ("an unknown depth is not an unlimited one. Sizing a "
                        "hedge against a depth we never read leaves the primary "
                        "leg partly naked while the books say it is paired")}
    have = float(depth_qty_at_price)
    want = float(wanted_qty)
    return {"version": VERSION, "ok": True, "refusal": None,
            "wanted_qty": want, "available_qty": have,
            "fully_supported": have + 1e-9 >= want,
            "supportable_qty": min(have, want),
            "shortfall_qty": round(max(0.0, want - have), 6),
            "shared_depth_is_not_multiple_quantities": (
                "this quantity is the same book the exit path reads. It must "
                "not be counted as separately executable in two places")}


def rank_actions(*, held: dict, hedge_candidate: dict | None = None,
                 exit_proceeds_usd=None, evidence: dict | None = None) -> dict:
    """ORDER THE AVAILABLE ACTIONS BY THEIR WORST CASE, and say what is missing.

    THE COMPARISON IS LIKE FOR LIKE. Every action is scored by the worst thing
    that can happen to the account if it is taken: for HOLD, the primary leg
    paying nothing; for ACQUIRE_HEDGE, the paired structure's worst outcome; for
    EXIT, the proceeds actually available now. An action whose inputs are not
    established is NOT scored as zero and is NOT silently dropped -- it is listed
    as unrankable with the refusal that made it so, because a missing input that
    disappears from a ranking becomes a decision made by omission.

    `held` is a `value_the_structure` result for the legs currently held.
    `hedge_candidate` carries the candidate hedge leg, its priced fee, its depth
    and the outcome space, so the paired structure can be valued on the same
    terms.
    """
    out: dict = {"version": VERSION, "actions": [], "unrankable": [],
                 "evidence": dict(evidence or {})}
    if not held.get("ok"):
        return dict(out, ok=False, refusal=held.get("refusal"),
                    why=held.get("why"))
    out["fixture"] = held["fixture"]
    out["held_worst_case_usd"] = held.get("worst_case_usd")

    def _add(action, worst, detail):
        out["actions"].append(dict(detail, action=action,
                                   worst_case_usd=worst))

    def _cannot(action, refusal, why, detail=None):
        out["unrankable"].append(dict(detail or {}, action=action,
                                      refusal=refusal, why=why))

    # ── HOLD ─────────────────────────────────────────────────────────
    if held.get("worst_case_usd") is None:
        _cannot(ACTION_HOLD, held.get("verdict_is_withheld", R_FEES_NOT_PRICED),
                held.get("why"))
    else:
        _add(ACTION_HOLD, held["worst_case_usd"], {
            "coverage": held["coverage"],
            "outcomes_paying_nothing": held["outcomes_paying_nothing"],
            "incremental_capital_usd": 0.0,
            "why": "keep exactly what is held; no new money, no new depth used"})

    # ── EXIT ─────────────────────────────────────────────────────────
    if exit_proceeds_usd is None:
        _cannot(ACTION_EXIT, R_PRICE_NOT_ESTABLISHED,
                ("an exit is scored on proceeds actually available now. "
                 "Without a read price there is no number, and using the "
                 "entry price would score a sale at what we paid"))
    else:
        _add(ACTION_EXIT, round(float(exit_proceeds_usd)
                                - (held.get("total_cost_usd") or 0.0), 6), {
            "proceeds_usd": round(float(exit_proceeds_usd), 6),
            "incremental_capital_usd": 0.0,
            "releases_the_capacity_slot": True,
            "why": ("sell what is held. This is the only action that needs no "
                    "forecast and no further capital")})

    # ── REDUCE ───────────────────────────────────────────────────────
    # DELIBERATELY UNRANKABLE UNTIL A PRICE IS READ, for the same reason as EXIT
    # and not as a placeholder: a partial sale is scored on the same read price,
    # and a REDUCE ranked without one would be an exit priced at nothing.
    if exit_proceeds_usd is None:
        _cannot(ACTION_REDUCE, R_PRICE_NOT_ESTABLISHED,
                "a partial sale is scored on the same read price an exit needs")
    else:
        _add(ACTION_REDUCE, None, {
            "incremental_capital_usd": 0.0,
            "worst_case_is_between": [
                round(float(exit_proceeds_usd)
                      - (held.get("total_cost_usd") or 0.0), 6),
                held.get("worst_case_usd")],
            "why": ("a partial sale lies between EXIT and HOLD by "
                    "construction, so it is ranked only when a quantity is "
                    "chosen -- and choosing it is a sizing decision this "
                    "function does not make")})

    # ── ACQUIRE_HEDGE ────────────────────────────────────────────────
    if not hedge_candidate:
        _cannot(ACTION_ACQUIRE_HEDGE, R_PRICE_NOT_ESTABLISHED,
                "no candidate hedge was supplied, so there is nothing to value")
    else:
        paired = hedge_candidate.get("paired_structure") or {}
        depth = hedge_candidate.get("depth") or {}
        incr = hedge_candidate.get("incremental") or {}
        if not paired.get("ok"):
            _cannot(ACTION_ACQUIRE_HEDGE,
                    paired.get("refusal", R_PRICE_NOT_ESTABLISHED),
                    paired.get("why"))
        elif paired.get("worst_case_usd") is None:
            _cannot(ACTION_ACQUIRE_HEDGE,
                    paired.get("verdict_is_withheld", R_FEES_NOT_PRICED),
                    paired.get("why"))
        elif not depth.get("ok"):
            _cannot(ACTION_ACQUIRE_HEDGE,
                    depth.get("refusal", R_DEPTH_NOT_ESTABLISHED),
                    depth.get("why"))
        elif not depth.get("fully_supported"):
            # A PARTIAL HEDGE IS NOT THE STRUCTURE THAT WAS VALUED. Ranking it
            # on the full pair's worst case would score a position the book
            # cannot supply.
            _cannot(ACTION_ACQUIRE_HEDGE, R_DEPTH_NOT_ESTABLISHED,
                    ("the book supplies %s of the %s the hedge needs. A partial "
                     "hedge leaves the primary leg naked for the shortfall, and "
                     "the full pair's worst case does not describe it"
                     % (depth.get("supportable_qty"), depth.get("wanted_qty"))),
                    {"shortfall_qty": depth.get("shortfall_qty")})
        elif incr.get("incremental_capital_usd") is None:
            _cannot(ACTION_ACQUIRE_HEDGE, R_FEES_NOT_PRICED,
                    "the hedge's own fee is not priced, so the new money it "
                    "would take is not known")
        else:
            _add(ACTION_ACQUIRE_HEDGE, paired["worst_case_usd"], {
                "coverage": paired["coverage"],
                "outcomes_paying_nothing": paired["outcomes_paying_nothing"],
                "cannot_lose": paired.get("cannot_lose"),
                "incremental_capital_usd": incr["incremental_capital_usd"],
                "depth": {k: depth.get(k) for k in
                          ("wanted_qty", "available_qty", "fully_supported")},
                "why": ("buy the complement. This is the only action that spends "
                        "NEW money, so its worst case must clear HOLD's by more "
                        "than the capital it consumes is worth elsewhere -- a "
                        "judgement this function reports the inputs for and "
                        "does not make")})

    ranked = [a for a in out["actions"] if a["worst_case_usd"] is not None]
    ranked.sort(key=lambda a: (-a["worst_case_usd"],
                               a.get("incremental_capital_usd") or 0.0))
    out["ranked"] = ranked
    out["best"] = ranked[0] if ranked else None
    out["ok"] = True
    out["refusal"] = None
    out["ranking_rule"] = (
        "highest worst case first; ties broken by LESS new capital. No "
        "probability enters the ranking")
    out["what_this_is_not"] = (
        "an authorisation. ACQUIRE_HEDGE appearing first means the structure's "
        "worst case is the best available on these inputs -- not that capital "
        "may be committed, which needs a funded grant and the owner's approval")
    if out["unrankable"]:
        out["unrankable_are_not_zero"] = (
            "an action whose inputs are not established is listed here rather "
            "than scored at zero or dropped. A missing input that disappears "
            "from a ranking becomes a decision made by omission")
    return out
