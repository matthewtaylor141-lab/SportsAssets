"""SELECTION over the EXISTING exit engine. One thin layer, not a rival.

WHY THIS FILE IS SMALL, AND WHAT I GOT WRONG FIRST.

I wrote `bettor_mgmt_value` as a full management valuation -- HOLD,
EXIT_NOW, REDUCE, COMPLETE_PAIR and resting variants, each priced or
refused. `bettor_exit_engine` already did that, and had for some time:

    RESIDUAL_ACTIONS = (HOLD, POST_COMPLEMENT, TAKE_COMPLEMENT,
                        COMPLETE_PAIR, MERGE, DIRECT_EXIT,
                        WAIT_REQUOTE, HOLD_TO_SETTLEMENT)

It already separates POST_COMPLEMENT (rest a bid) from TAKE_COMPLEMENT
(cross the ask) -- the resting/taker distinction I "added". It already
carries `P_FILL=NOT_IDENTIFIED` on the resting branch with an explicit
no-fill branch, noting that an unfilled passive exit leaves us at full
exposure. It already reaches the conclusion I re-derived: it cannot RANK
anything, because ranking needs EV_HOLD and that needs an independent
fair value which does not exist. It even records the same trap --
"THE CHEAPEST ACTION IS NOT THE BEST ACTION" -- that my selection rule
was built to avoid.

So `bettor_mgmt_value` was a second exit engine. It is deleted. This
module is the one thing the existing engine deliberately does NOT do.

WHAT THE EXIT ENGINE DOES, PRECISELY -- the answer to "one action at a
time or simultaneous orders":

    NEITHER. `evaluate()` takes one residual position and returns an
    UNRANKED TABLE of all eight actions, every row carrying
    EV_VS_HOLD = NOT_IDENTIFIED and a `whyNotRanked`. It selects
    nothing, so it cannot select "one at a time". It holds no order
    state, no resting-order lifecycle, no cancellation and no partial
    fill, so it cannot maintain simultaneous orders either. It is a
    pricing and refusal surface, and the order lifecycle lives
    elsewhere -- in `bettor_desk.Order`.

    It also requires EXACTLY ONE LEG HELD: "holding both is a pair
    question for the pair engine". So a position that has been completed
    leaves its domain, which matters for the seeded path and is handled
    by returning the engine's own refusal rather than papering over it.

WHAT THIS ADDS, and it is two things only:

  1. A SELECTION RULE, declared. The engine refuses to rank and that
     refusal is correct; something still has to decide. The rule is
     stated here so it can be argued with, and its central provision is
     a refusal of its own: when HOLD is unpriced, NOTHING is selected.

     WHY THAT PROVISION EXISTS. Live, HOLD has no value and DIRECT_EXIT
     does. Ranking by whatever number is available selects the exit every
     time -- not because exiting is good but because it is the only
     action carrying a figure. That is an engine that liquidates the book
     for want of a settlement model, and it is the same failure the exit
     engine names as preferring inaction, pointed the other way.

  2. EXACT HOLD PRICING WHEN SETTLEMENT IS OBSERVED -- for SCORING a
     historical run, never for deciding one. On a resolved condition the
     payout is a fact, so hold-to-settlement is arithmetic. That is the
     only reason a historical management test can be scored at all, and
     supplying it at decision time would be look-ahead.
"""

from __future__ import annotations

from . import bettor_exit_engine as ee

VERSION = "BETTOR_MGMT_SELECT_V1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# Actions that need one of OUR orders to rest before they pay anything.
# Listed here rather than inferred from the name, because POST_COMPLEMENT
# and WAIT_REQUOTE do not share a prefix and a name test would miss one.
REQUIRES_OUR_FILL = ("POST_COMPLEMENT", "WAIT_REQUOTE")


def hold_to_settlement_usd(qty, basis_usd, payout) -> dict:
    """Score a hold against an OBSERVED payout.

    FOR SCORING, NOT DECIDING. `payout` must come from
    markets.resolved_prices -- an observed settlement. Passing a
    forecast here and calling the result an EV would be the
    substitution this stack refuses.
    """
    if payout is None:
        return {"status": NOT_IDENTIFIED,
                "blocker": "SETTLEMENT_NOT_OBSERVED",
                "why": ("hold is worth payout x qty. No observed payout "
                        "was supplied, and no validated settlement model "
                        "exists to estimate one: P_BETTOR_INDEPENDENT_V3 "
                        "measured the blend WORSE than the venue price by "
                        "0.00926 log loss, CI [-0.00222, +0.02036], "
                        "INCREMENTAL_SIGNAL_STATUS NOT_DETECTED"),
                "value_usd": None}
    return {"status": "IDENTIFIED", "blocker": None,
            "value_usd": float(payout) * float(qty) - float(basis_usd),
            "basis": "OBSERVED_SETTLEMENT_PAYOUT",
            "why": ("%.4f x %.4g against a basis of %.4f"
                    % (payout, qty, basis_usd))}


def select(inventory: dict, *, held_book=None, complement_book=None,
           observed_payout=None, qty=None, basis_usd=None,
           seconds_unpaired=None) -> dict:
    """Run the EXISTING engine, then apply the declared selection rule.

    `observed_payout` is accepted ONLY to score HOLD and is never passed
    into `ee.evaluate`. It must be absent at decision time.
    """
    table = ee.evaluate(inventory, held_book=held_book,
                        complement_book=complement_book,
                        seconds_unpaired=seconds_unpaired)
    rows = table.get("actions") or []
    out = {
        "version": VERSION,
        "engine": "bettor_exit_engine.evaluate",
        "engine_status": table.get("status"),
        "engine_why": table.get("why"),
        "actions": rows,
        "requires_our_fill": [r["action"] for r in rows
                              if r.get("action") in REQUIRES_OUR_FILL],
        "p_fill": NOT_IDENTIFIED,
    }
    if not rows:
        # The engine refused the whole comparison -- most often because
        # BOTH legs are held, which is the pair engine's domain. Its own
        # reason is surfaced rather than replaced.
        out.update(selected=None, selection_reason=(
            "the exit engine returned no actions: %s"
            % (table.get("why") or "no reason given")))
        return out

    # EVERY ROW IS UNRANKED BY CONSTRUCTION. The engine sets
    # EV_VS_HOLD = NOT_IDENTIFIED on all of them, so there is nothing to
    # maximise and selecting by "best EV" is not available. This is
    # asserted rather than assumed, so that if the engine ever starts
    # ranking, this layer fails loudly instead of silently ignoring it.
    ranked = [r for r in rows if r.get("EV_VS_HOLD") != NOT_IDENTIFIED]

    hold = hold_to_settlement_usd(qty, basis_usd, observed_payout) \
        if qty is not None and basis_usd is not None else \
        {"status": NOT_IDENTIFIED, "blocker": "NO_POSITION_SUPPLIED",
         "value_usd": None, "why": "qty and basis were not supplied"}
    out["hold_to_settlement"] = hold

    if not ranked:
        out.update(selected=None, selection_reason=(
            "NO ACTION SELECTED. Every action the engine priced is "
            "EV_VS_HOLD = NOT_IDENTIFIED, because ranking against HOLD "
            "needs an independent fair value that is not established. "
            "Selecting the cheapest available action instead would be a "
            "policy nobody chose -- and with HOLD unpriced it would "
            "liquidate the book for want of a settlement model."),
            unranked_count=len(rows))
        return out

    out.update(selected=None, selection_reason=(
        "the engine returned %d RANKED rows, which this layer was "
        "written when it could not. Refusing to select rather than "
        "guessing at a rule for a table whose shape has changed."
        % len(ranked)))
    return out


# ── the experimental rule ────────────────────────────────────────────
#
# WHAT IT IS AND WHAT IT IS NOT. It is a DECLARED RULE that can be
# tested. It is NOT an EV-optimal decision, and it must never be
# reported as one: the EV comparison that would justify calling it
# optimal is NOT_IDENTIFIED, which is exactly why a rule is needed.
#
# WHY IT CAN ACT WHEN THE RANKING CANNOT. Completing a pair has a value
# that contains no forecast -- a completed pair pays 1.00 per contract
# however the event resolves -- so the locked gain is arithmetic. What
# is NOT available is whether locking it beats holding, because holding
# is worth payout x qty and no payout estimate exists. So the rule
# expresses a PREFERENCE FOR CERTAINTY, declared as such, rather than a
# belief about the mean.
#
# IT WILL LOSE TO HOLDING on positions that settle in our favour. That
# is not a defect of the rule; it is what preferring certainty costs,
# and any report of this rule must carry it.
RULE_ID = "EXPERIMENTAL_COMPLETE_ON_LOCKED_GAIN_V1"
RULE_HURDLE_PER_CONTRACT = 0.01

RULE_DECLARATION = {
    "id": RULE_ID,
    "kind": "RULE",
    "is_not": ("an EV-optimal decision, an optimisation, or evidence "
               "that completing beats holding"),
    "objective": ("take a CERTAIN gain per contract when it clears a "
                  "declared hurdle, in preference to an uncertain "
                  "outcome of unknown mean"),
    "hurdle_per_contract_usd": RULE_HURDLE_PER_CONTRACT,
    "hurdle_is": "DECLARED, not fitted and not tuned on any result",
    "known_cost": ("it forgoes upside on positions that settle in our "
                   "favour, and will underperform HOLD on those"),
    "inputs": {
        "own_basis": "OBSERVED (average-cost convention)",
        "complement_ask": "OBSERVED (the venue's own book)",
        "fees": "OBSERVED schedule, or the action is refused",
        "settlement": "NOT USED -- the rule contains no forecast",
        "p_fill": "NOT USED -- it crosses the ask rather than resting",
    },
}


def experimental_rule(qty, own_basis_per_contract, complement_ask, *,
                      fee_fn=None) -> dict:
    """Would the declared rule act, and at what locked gain?

    Returns the decision AND the arithmetic, so the number can be
    checked rather than trusted.
    """
    base = {"rule": RULE_DECLARATION, "acts": False}
    if complement_ask is None:
        return {**base, "why": ("no complement ask was observed, so no "
                                "completion price exists"),
                "locked_gain_usd": None}
    q = float(qty)
    own = float(own_basis_per_contract) * q
    comp = float(complement_ask) * q
    fee = 0.0 if fee_fn is None else float(fee_fn(qty=q,
                                                 price=complement_ask))
    if fee_fn is None:
        return {**base, "why": ("no fee schedule was supplied. An "
                                "unknown cost is not a zero cost, so the "
                                "rule refuses rather than acting on a "
                                "gain it cannot verify"),
                "locked_gain_usd": None}
    locked = 1.00 * q - own - comp - fee
    hurdle = RULE_HURDLE_PER_CONTRACT * q
    return {
        **base,
        "acts": locked >= hurdle,
        "locked_gain_usd": locked,
        "hurdle_usd": hurdle,
        "arithmetic": ("1.00 x %.4g = %.4f, less own basis %.4f, less "
                       "completion %.4f, less fees %.4f => %.4f; hurdle "
                       "%.4f" % (q, q, own, comp, fee, locked, hurdle)),
        "why": ("locked gain %.4f %s the declared hurdle %.4f"
                % (locked, "clears" if locked >= hurdle else "is below",
                   hurdle)),
        "carries": ("this is a RULE. It is not an EV ranking against "
                    "HOLD, and HOLD remains NOT_IDENTIFIED at decision "
                    "time"),
    }
