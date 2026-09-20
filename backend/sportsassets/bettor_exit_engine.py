"""THE AI INVENTORY / EXIT ENGINE. THE FERRARI IMPROVEMENT.

Owner directive, "CONTINUE THE BUILD" §7:

    "Ferrari showed excellent pair-generation economics AND destructive
    residual inventory economics. BETTOR should retain the pair
    machinery and improve the inventory decision... Do NOT use a fixed
    60-second exit as the final BETTOR methodology... The real engine
    should exit when another action has superior conservative economic
    value."

WHAT THIS REPLACES, AND WHAT IT DOES NOT. X1's 60-second horizon is
research infrastructure: a fixed clock makes a cohort comparable and
that is exactly what an experiment needs. It is not a strategy. A
position does not become worth exiting because sixty seconds elapsed;
it becomes worth exiting when some other action is worth more. This
module makes that comparison explicit for every residual, on every
legitimate new state.

§10, THE SWISSTONY LESSON. Owning YES does not mean exiting is selling
YES. The same exposure can be removed by BUYING NO, and sometimes that
is cheaper -- a thin bid on the leg you hold and a tight ask on its
complement is a common shape, and selling into the thin side pays for
the privilege. So TAKE_COMPLEMENT and DIRECT_EXIT are kept as DISTINCT
ACTIONS with their own prices, never collapsed into "exit", however
similarly the venue happens to net them.

────────────────────────────────────────────────────────────────────
WHAT THIS ENGINE CAN AND CANNOT RANK TODAY, STATED PLAINLY.

It can price EXECUTION COST for every action that touches the book,
because the book is observable. DIRECT_EXIT's proceeds, the
complement's cost and the resulting hedge tax against par are all real
numbers right now.

It CANNOT rank the actions, because ranking needs EV_HOLD -- the value
of doing nothing -- and that requires an independent fair value that
does not exist. Every comparison against HOLD therefore returns
NOT_IDENTIFIED, and the engine says which comparison it could not make
rather than defaulting to the action whose cost happens to be lowest.

    THE CHEAPEST ACTION IS NOT THE BEST ACTION. DIRECT_EXIT at a wide
    spread is expensive and may still beat holding a leg that is about
    to lose. HOLD is free and may be the worst thing available. An
    engine that ranked by execution cost alone would systematically
    prefer inaction, which is a policy nobody chose and which Ferrari's
    residual book is the standing example of.
────────────────────────────────────────────────────────────────────

CONSERVATIVE ECONOMIC VALUE, WHEN IT ARRIVES (§9). The comparison is
specified to run on a DISTRIBUTION per action -- P10, mean, P(EV>0),
tail loss and capital duration -- not a point estimate, so that an
action with a good mean and an intolerable tail loses to one with a
worse mean and a survivable one. The fields are declared here and are
NOT_IDENTIFIED until the prospective dataset supports them. Nothing is
trained and nothing is promoted.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_ev_actions as acts
from . import bettor_ev_bridge as evb
from . import bettor_hedge_tax as htax
from . import bettor_inventory as binv
from . import bettor_pair_engine as pe

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── §7: the actions a residual position may take ─────────────────────
#
# Named from the canonical table so this engine and the entry engine
# cannot drift into two vocabularies.

RESIDUAL_ACTIONS = (
    "HOLD",
    "POST_COMPLEMENT",
    "TAKE_COMPLEMENT",
    "COMPLETE_PAIR",
    "MERGE",
    "DIRECT_EXIT",
    "WAIT_REQUOTE",
    "HOLD_TO_SETTLEMENT",
)

NOT_A_FIXED_HORIZON = (
    "there is no 60-second rule here. The X-series fixed horizon is "
    "research infrastructure -- a fixed clock is what makes a cohort "
    "comparable -- and it is not a strategy. A position becomes worth "
    "exiting when another action is worth more, not when a timer "
    "elapses")

COMPLEMENT_IS_AN_EXIT = (
    "owning YES does not mean exiting is selling YES. Buying NO removes "
    "the same exposure and is sometimes cheaper, because the bid on the "
    "leg held and the ask on its complement are different prices in "
    "different books. TAKE_COMPLEMENT and DIRECT_EXIT stay distinct "
    "actions with their own prices")

CHEAPEST_IS_NOT_BEST = (
    "execution cost alone must never pick the action. HOLD costs "
    "nothing and can be the worst choice available; a wide-spread exit "
    "is expensive and can still beat holding a losing leg. Ranking by "
    "cost would systematically prefer inaction, which is the policy "
    "Ferrari's residual book already demonstrates the cost of")

# ── §9: the distribution each action is eventually judged on ─────────

CONSERVATIVE_FIELDS = (
    "EV_P10",
    "EV_MEAN",
    "P_EV_POSITIVE",
    "TAIL_LOSS",
    "CAPITAL_DURATION_S",
)

WHY_A_DISTRIBUTION = (
    "a point estimate cannot express that one action has a better mean "
    "and an intolerable tail. The comparison runs on P10, mean, "
    "P(EV>0), tail loss and capital duration so a survivable action "
    "can beat a higher-mean one that occasionally does not survive")


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _unranked(action, why, **extra):
    row = {
        "action": action,
        "leg": acts.leg_of(action),
        "EV_VS_HOLD": NOT_IDENTIFIED,
        "whyNotRanked": why,
    }
    for f in CONSERVATIVE_FIELDS:
        row[f] = NOT_IDENTIFIED
    row.update(extra)
    return row


def evaluate(inventory: dict, *, held_book=None, complement_book=None,
             seconds_unpaired=None, root=None) -> dict:
    """Compare every action available to this residual position.

    `held_book` prices the leg we own (we would SELL into its bid).
    `complement_book` prices the other leg (we would BUY at its ask).
    """
    yq = _d(inventory.get("YES_QTY")) or Decimal("0")
    nq = _d(inventory.get("NO_QTY")) or Decimal("0")
    if yq > 0 and nq == 0:
        held = binv.LEG_YES
    elif nq > 0 and yq == 0:
        held = binv.LEG_NO
    else:
        return {
            "status": NOT_IDENTIFIED,
            "actions": [],
            "why": ("an exit comparison needs exactly one leg held. "
                    "Holding neither is flat; holding both is a pair "
                    "question for the pair engine"),
        }

    qty = yq if held == binv.LEG_YES else nq
    own_basis = _d(inventory.get("%s_AVG_BASIS" % held))
    bid = _d((held_book or {}).get("bid"))
    ask = _d((complement_book or {}).get("ask"))

    tax = htax.hedge_tax(own_basis, ask)

    rows = []
    for action in RESIDUAL_ACTIONS:
        if action == "HOLD":
            rows.append(_unranked(
                action,
                ("HOLD is the reference every other action is measured "
                 "against, and its own value needs an independent fair "
                 "value. Its execution cost is zero and that is not the "
                 "same as its EV being zero"),
                EXECUTION_COST="0",
                isTheReference=True))
            continue

        if action == "DIRECT_EXIT":
            proceeds = (str(bid * qty) if bid is not None else
                        NOT_IDENTIFIED)
            cost = (str((bid - own_basis) * qty)
                    if bid is not None and own_basis is not None
                    else NOT_IDENTIFIED)
            rows.append(_unranked(
                action,
                ("the realised loss against basis is identified, but "
                 "whether taking it beats holding is not"),
                SELL_PRICE=str(bid) if bid is not None else NOT_IDENTIFIED,
                PROCEEDS=proceeds,
                REALISED_VS_BASIS=cost,
                sellsInto="the BID on the leg we hold"))
            continue

        if action in ("TAKE_COMPLEMENT", "COMPLETE_PAIR"):
            rows.append(_unranked(
                action,
                ("the cost of completing is identified against par; "
                 "whether completing beats holding is not"),
                BUY_PRICE=str(ask) if ask is not None else NOT_IDENTIFIED,
                PAIR_BASIS=tax["PAIR_BASIS"],
                HEDGE_TAX_VS_PAR=tax["HEDGE_TAX_VS_PAR"],
                HEDGE_TAX_VS_HOLD=tax["HEDGE_TAX_VS_HOLD"],
                buysFrom="the ASK on the complement leg",
                isAnExitMechanism=COMPLEMENT_IS_AN_EXIT))
            continue

        if action == "POST_COMPLEMENT":
            # §14. THE NO-FILL BRANCH HERE IS NOT NOTHING. Resting a bid
            # on the complement is a PASSIVE EXIT: if it does not fill we
            # still own the leg we started with, at full exposure. A
            # zero here would price a failed hedge as though the risk had
            # been removed.
            rows.append(_unranked(
                action,
                ("a resting complement bid has no identified fill "
                 "probability -- BETTOR has never rested an order -- so "
                 "neither its cost nor its value is identified"),
                P_FILL=NOT_IDENTIFIED,
                fillOutcomes=list(evb.FILL_OUTCOMES),
                # `why` is renamed on the way in: the branch's own
                # reason is about NOT FILLING, while _unranked's `why`
                # is about not being RANKED. Two different sentences.
                **{("whyNoFill" if k == "why" else k): v
                   for k, v in evb.no_fill_branch(evb.PASSIVE_EXIT).items()}))
            continue

        if action == "MERGE":
            rows.append(_unranked(
                action,
                ("no venue merge mechanism is confirmed for this "
                 "instrument, so the action cannot be priced at all"),
                MERGE_MECHANISM="NOT_IDENTIFIED"))
            continue

        if action == "HOLD_TO_SETTLEMENT":
            rows.append(_unranked(
                action,
                ("this venue's settlement prose is "
                 "CONFLICTING_VENUE_PROSE, so the terminal value of "
                 "carrying to settlement is not identified"),
                SETTLEMENT_SEMANTICS="CONFLICTING_VENUE_PROSE"))
            continue

        rows.append(_unranked(
            action,
            "no identified value; the action is recorded as available"))

    # §10 SIDE BY SIDE, because the whole point is that they differ.
    exit_comparison = {
        "DIRECT_EXIT_SELL_PRICE": (str(bid) if bid is not None
                                   else NOT_IDENTIFIED),
        "TAKE_COMPLEMENT_BUY_PRICE": (str(ask) if ask is not None
                                      else NOT_IDENTIFIED),
        "theyAreDifferentPrices": COMPLEMENT_IS_AN_EXIT,
        "whichIsCheaper": NOT_IDENTIFIED,
        "whyNotAnswered": (
            "comparing them needs the value of what each leaves behind: "
            "DIRECT_EXIT leaves nothing, COMPLETE_PAIR leaves a matched "
            "pair that still occupies capital until settlement or "
            "merge. Those are not the same object and cannot be "
            "compared on price alone"),
    }

    return {
        "status": "EXECUTION_COSTS_IDENTIFIED_RANKING_NOT_IDENTIFIED",
        "heldLeg": held,
        "heldQty": str(qty),
        "actions": rows,
        "bestAction": NOT_IDENTIFIED,
        "whyNoBestAction": (
            "ranking requires EV_HOLD, the value of doing nothing, "
            "which requires an independent fair value. "
            "FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED, so no action can "
            "be shown to beat holding and none is recommended"),
        "cheapestIsNotBest": CHEAPEST_IS_NOT_BEST,
        "notAFixedHorizon": NOT_A_FIXED_HORIZON,
        "exitComparison": exit_comparison,
        "hedgeTax": tax,
        "conservativeFields": list(CONSERVATIVE_FIELDS),
        "whyADistribution": WHY_A_DISTRIBUTION,
        "pairView": pe.pair_view(inventory, complement_book,
                                 seconds_unpaired=seconds_unpaired,
                                 root=root),
    }


def describe() -> dict:
    return {
        "purpose": "compare every action a residual position may take",
        "residualActions": list(RESIDUAL_ACTIONS),
        "notAFixedHorizon": NOT_A_FIXED_HORIZON,
        "complementIsAnExit": COMPLEMENT_IS_AN_EXIT,
        "cheapestIsNotBest": CHEAPEST_IS_NOT_BEST,
        "conservativeFields": list(CONSERVATIVE_FIELDS),
        "whyADistribution": WHY_A_DISTRIBUTION,
        "machineryAvailable": evb.available(),
    }
