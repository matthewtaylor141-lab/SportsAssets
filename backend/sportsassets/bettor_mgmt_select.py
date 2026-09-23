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
# WHY IT CAN ACT WHEN THE ENGINE'S RANKING CANNOT. Completing a pair and
# selling into the bid both have outcomes that contain NO forecast: a
# completed pair pays 1.00 per contract however the event resolves, and a
# sale realises the bid. So both are exact arithmetic and comparable to
# each other. What is NOT available is whether either beats HOLDING,
# because holding is worth payout x qty and no payout estimate exists.
# The rule therefore ranks within the priced subset and says plainly that
# HOLD is outside it.
#
# IT MAY LOSE TO HOLDING, on positions that settle in our favour. That is
# not a defect of the rule; it is the cost of acting on what can be
# priced, and any report of this rule must carry it.
RULE_ID = "PRICED_ACTION_RANKING_V1"

# THE BLANKET SUB-PAR RULE IS WITHDRAWN, AND IT WAS WRONG.
#
# My previous rule acted only when the complement could be bought below
# (1 - basis), i.e. when the completed pair locked a GAIN. The desk's own
# Ferrari policy carries the same rule at bettor_desk.py:808
# (`clears_below = 1.0 - avg - min_clear`) and states the reasoning:
# "complete when the complement can be bought below (1 - our entry) ...
# anything above that locks a loss."
#
# TRUE, AND NOT A REASON TO REFUSE. Completing above par locks a loss of
# (basis + ask - 1). Holding a leg that may settle at zero risks the
# WHOLE basis. Selling into a thin bid realises (bid - basis), which can
# be worse than the locked loss. A rule that refuses every completion
# above $1 cannot ever limit a loss by completing, which is a real and
# sometimes best action -- and Ferrari's residual book is the standing
# example of what refusing it costs.
#
# WHAT MAKES THIS RANKABLE WITHOUT A FORECAST, AND THE WORD I GOT WRONG.
# Two actions have outcomes that are EXACTLY PRICED at decision time:
#
#   DIRECT_EXIT       sell at the bid q   ->  q - basis
#   TAKE_COMPLEMENT   buy the ask a       ->  1.00 - a - basis
#
# I first called these CERTAIN. They are not. The ARITHMETIC is exact;
# the EXECUTION is not secured. Computing an attractive completion does
# not obtain it: an immediate order still needs executable price AND
# depth, and it may fill partially or not at all. So the figure is
# `outcome_if_filled_usd` and every row carries
# `execution_secured: False`. A locked profit is CONDITIONAL until the
# matching quantity actually fills, and a partial fill leaves residual
# exposure that is still under management.
#
# With that said, the two are comparable to each other exactly, and the
# comparison reduces to one clean test:
#
#       COMPLETE beats SELL  <=>  (1.00 - a) > q
#
# ... the complement is cheaper than one dollar minus the bid. No
# settlement model, no fill probability, no assumption. HOLD stays
# NOT_IDENTIFIED and is reported as not comparable, which is the honest
# shape: a RANKING OVER THE EXACTLY-PRICED SUBSET, with the uncertain
# action named and excluded rather than assigned a number.

RULE_DECLARATION = {
    "id": RULE_ID,
    "kind": "RULE over an EXACT sub-comparison",
    "what_is_ranked": ("DIRECT_EXIT and TAKE_COMPLEMENT only. Both have "
                       "outcomes EXACTLY PRICED at decision time, so the "
                       "comparison between them is arithmetic. Exactly "
                       "priced is NOT executed: see execution_secured"),
    "what_is_NOT_ranked": ("HOLD and HOLD_TO_SETTLEMENT. Their value is "
                           "payout x qty and no validated settlement "
                           "model exists. They are reported as NOT "
                           "COMPARABLE, never as zero"),
    "is_not": ("evidence that acting beats holding, or that the "
               "chosen action will execute. The best priced action may "
               "still be worse than holding -- that comparison is "
               "unavailable -- and it may not fill at all"),
    "supports_loss_limiting": ("YES. Completing above par is permitted "
                               "and is selected whenever (1 - ask) "
                               "exceeds the bid, even though the locked "
                               "result is a loss"),
    "withdrawn": ("the blanket 'combined cost must be below $1' gate. It "
                  "made loss-limiting completion structurally impossible"),
    "inputs": {
        "own_basis": "OBSERVED (average-cost convention)",
        "bid / complement_ask": "OBSERVED (the venue's own book)",
        "depth": "OBSERVED -- a price without size is not executable",
        "fees": "OBSERVED schedule, or the action is refused",
        "settlement": "NOT USED -- neither priced action needs a forecast",
        "p_fill": ("NOT USED -- both are TAKER actions against displayed "
                   "depth. The RESTING variants are a different question "
                   "and stay NOT_IDENTIFIED"),
    },
}


# ── WHEN to close exposure, which is a different question from HOW ───
#
# THE GAP THIS CLOSES. Ranking DIRECT_EXIT against TAKE_COMPLEMENT picks
# an exit METHOD. It says nothing about whether exposure should be closed
# at all. I had been treating the method comparison as though it were the
# decision, which quietly made "act" the default whenever any action was
# priceable.
#
# UNTIL A TRIGGER FIRES, HOLD IS A VALID OPERATING STATE -- not a
# fallback, not an absence of decision, and not something to be escaped
# as soon as a number exists.
#
# WHAT A TRIGGER MAY USE. Only information available at that instant: the
# last observed price on our own leg, our entry basis, and how long the
# exposure has been open. No settlement forecast, no future print, no
# knowledge of what the cohort does next.
#
# IT IS A RULE, NOT A MODEL. No validated estimator says when closing
# beats holding -- that comparison needs EV_HOLD. These thresholds are
# DECLARED, they are not fitted, and they were not tuned on any result.
# A rule that fires on a 20% adverse move is a stated risk preference;
# calling it optimal would be the claim this stack refuses.
TRIGGER_ID = "EXPOSURE_TRIGGER_RULE_V1"
TRIGGER_ADVERSE_FRACTION = 0.20      # of basis, on the last observed price
TRIGGER_MAX_SECONDS_OPEN = 86_400.0  # one day unpaired

TRIGGER_DECLARATION = {
    "id": TRIGGER_ID,
    "kind": "RULE",
    "question_it_answers": "WHETHER to close exposure, not HOW",
    "inputs_available_now": ["last observed price on our leg",
                            "entry basis", "seconds exposure has been open"],
    "inputs_refused": ["settlement forecast", "future prints",
                       "the cohort's later actions"],
    "thresholds_are": "DECLARED, not fitted and not tuned on any result",
    "is_not": ("evidence that closing beats holding. That comparison "
               "needs EV_HOLD, which is NOT_IDENTIFIED"),
    "hold_is_valid": ("until a trigger fires, HOLD is the operating "
                      "state. It is not a fallback and not an absence of "
                      "a decision"),
    "adverse_fraction": TRIGGER_ADVERSE_FRACTION,
    "max_seconds_open": TRIGGER_MAX_SECONDS_OPEN,
}


def exposure_trigger(*, basis_per_contract, last_price=None,
                     seconds_open=None) -> dict:
    """Should exposure be closed at all? A declared rule.

    Returns fired=False with HOLD as the operating state when no
    condition is met. Returns fired=True and the condition that fired,
    which the METHOD comparison then answers separately.
    """
    out = {"trigger": TRIGGER_DECLARATION, "fired": False,
           "conditions": [], "operating_state": "HOLD"}
    b = float(basis_per_contract)

    if last_price is None:
        out["conditions"].append({
            "name": "ADVERSE_MOVE", "status": NOT_IDENTIFIED,
            "why": ("no price has been observed on our leg since entry, "
                    "so the move is unknown -- not zero")})
    else:
        move = (float(last_price) - b) / b if b > 0 else 0.0
        hit = move <= -TRIGGER_ADVERSE_FRACTION
        out["conditions"].append({
            "name": "ADVERSE_MOVE", "status": "EVALUATED", "fired": hit,
            "observed_move_fraction": move,
            "threshold": -TRIGGER_ADVERSE_FRACTION,
            "why": ("last observed %.4f against basis %.4f is %.2f%%; "
                    "the declared threshold is %.0f%%"
                    % (float(last_price), b, move * 100.0,
                       -TRIGGER_ADVERSE_FRACTION * 100.0))})
        if hit:
            out["fired"] = True

    if seconds_open is None:
        out["conditions"].append({
            "name": "TIME_OPEN", "status": NOT_IDENTIFIED,
            "why": "how long the exposure has been open was not supplied"})
    else:
        hit = float(seconds_open) >= TRIGGER_MAX_SECONDS_OPEN
        out["conditions"].append({
            "name": "TIME_OPEN", "status": "EVALUATED", "fired": hit,
            "seconds_open": float(seconds_open),
            "threshold": TRIGGER_MAX_SECONDS_OPEN,
            "why": ("%.0fs open against a declared maximum of %.0fs"
                    % (float(seconds_open), TRIGGER_MAX_SECONDS_OPEN))})
        if hit:
            out["fired"] = True

    if out["fired"]:
        out["operating_state"] = "CLOSING"
        out["reason"] = ("a declared trigger fired: %s. The METHOD is a "
                         "separate comparison and does not justify "
                         "closing by itself"
                         % ", ".join(c["name"] for c in out["conditions"]
                                     if c.get("fired")))
    else:
        out["reason"] = ("no declared trigger fired, so HOLD remains the "
                         "operating state. A priceable exit existing is "
                         "NOT a reason to take it")
    return out


def rank_priced_actions(qty, own_basis_per_contract, *, bid=None,
                         bid_size=None, complement_ask=None,
                         complement_ask_size=None, fee_fn=None) -> dict:
    """Rank the EXACTLY-PRICED actions. Publish the arithmetic.

    Returns each action's exactly-priced outcome over the SAME quantity,
    winner, the margin, and HOLD named as not comparable. Sizes matter:
    a price without depth behind it is not an executable action and is
    refused rather than ranked.
    """
    q = float(qty)
    basis = float(own_basis_per_contract) * q
    cands, refused = [], []

    def _fee(sz, px):
        if fee_fn is None:
            return None
        return float(fee_fn(qty=sz, price=px))

    # DIRECT_EXIT -- sell the held leg into the bid.
    if bid is None:
        refused.append({"action": "DIRECT_EXIT", "blocker": "NO_BID"})
    elif not bid_size or float(bid_size) <= 0:
        refused.append({"action": "DIRECT_EXIT",
                        "blocker": "NO_EXECUTABLE_DEPTH"})
    else:
        sz = min(q, float(bid_size))
        f = _fee(sz, bid)
        if f is None:
            refused.append({"action": "DIRECT_EXIT",
                            "blocker": "FEE_SCHEDULE_NOT_ESTABLISHED"})
        else:
            # Basis is released pro-rata on what the bid could actually
            # take, not on what we wanted to sell.
            rel = basis * (sz / q)
            cands.append({
                "action": "DIRECT_EXIT", "qty": sz,
                "outcome_if_filled_usd": float(bid) * sz - f - rel,
                "execution_secured": False, "fees_usd": f,
                "depth_limited": sz < q - 1e-12,
                # CASH NOW vs CASH LATER. A sale returns cash at
                # execution. A completion does not. Comparing the two on
                # net outcome alone hides that difference entirely.
                "cash_now_usd": float(bid) * sz - f,
                "cash_at_settlement_usd": 0.0,
                "collateral_released_now": True,
                "remaining_exposure_qty": q - sz,
                "arithmetic": ("%.4f x %.4g - fees %.4f - basis %.4f"
                               % (float(bid), sz, f, rel))})

    # TAKE_COMPLEMENT -- buy the other leg; the pair then pays 1.00.
    if complement_ask is None:
        refused.append({"action": "TAKE_COMPLEMENT",
                        "blocker": "COMPLEMENT_ASK_NOT_OBSERVED"})
    elif not complement_ask_size or float(complement_ask_size) <= 0:
        refused.append({"action": "TAKE_COMPLEMENT",
                        "blocker": "NO_EXECUTABLE_DEPTH"})
    else:
        sz = min(q, float(complement_ask_size))
        f = _fee(sz, complement_ask)
        if f is None:
            refused.append({"action": "TAKE_COMPLEMENT",
                            "blocker": "FEE_SCHEDULE_NOT_ESTABLISHED"})
        else:
            rel = basis * (sz / q)
            val = 1.00 * sz - float(complement_ask) * sz - f - rel
            cands.append({
                "action": "TAKE_COMPLEMENT", "qty": sz,
                "outcome_if_filled_usd": val, "execution_secured": False,
                "fees_usd": f,
                "depth_limited": sz < q - 1e-12,
                "locks_a_loss": val < 0,
                "unpaired_after": q - sz,
                # COMPLETING SPENDS CASH AND RETURNS NONE UNTIL
                # SETTLEMENT. A HELD PAIR IS NOT AVAILABLE CASH: the
                # matched quantity only frees collateral if the venue
                # lets it be merged or netted, and the institutional
                # MERGE_MECHANISM is NOT_IDENTIFIED. So this action
                # INCREASES capital committed at the moment it fills.
                "cash_now_usd": -(float(complement_ask) * sz + f),
                "cash_at_settlement_usd": 1.00 * sz,
                "collateral_released_now": False,
                "collateral_release": NOT_IDENTIFIED,
                "collateral_release_why": (
                    "a matched pair is not cash. Releasing it needs a "
                    "merge or net the venue has not established for this "
                    "account, so the capital stays committed until "
                    "settlement"),
                "remaining_exposure_qty": q - sz,
                "arithmetic": ("1.00 x %.4g - %.4f x %.4g - fees %.4f - "
                               "basis %.4f" % (sz, float(complement_ask),
                                               sz, f, rel))})

    out = {
        "rule": RULE_DECLARATION,
        "qty_compared": q,
        "priced_actions": cands,
        "refused": refused,
        "hold": {"action": "HOLD", "value_usd": None,
                 "status": NOT_IDENTIFIED,
                 "why": ("worth payout x qty, and no validated "
                         "settlement model exists. NOT zero, and NOT "
                         "comparable to the actions above")},
    }
    if not cands:
        out.update(selected=None, selection_reason=(
            "no action has an exactly priced outcome: %s"
            % ", ".join("%s (%s)" % (r["action"], r["blocker"])
                        for r in refused)))
        return out

    cands.sort(key=lambda c: c["outcome_if_filled_usd"], reverse=True)
    best = cands[0]
    runner = cands[1] if len(cands) > 1 else None
    reason = ("%s at %.4f is the best PRICED action" %
              (best["action"], best["outcome_if_filled_usd"]))
    if runner:
        reason += (" , beating %s at %.4f by %.4f"
                   % (runner["action"], runner["outcome_if_filled_usd"],
                      best["outcome_if_filled_usd"] - runner["outcome_if_filled_usd"]))
        # The clean test, stated so it can be checked independently.
        if (complement_ask is not None and bid is not None):
            reason += ("; the SIMPLIFIED pre-cost test is 1 - ask = "
                       "%.4f vs bid = %.4f, and the figures above "
                       "are the NET ones after action-specific fees "
                       "and depth caps"
                       % (1.0 - float(complement_ask), float(bid)))
    if best["outcome_if_filled_usd"] < 0:
        reason += (". THIS LOCKS A LOSS and is selected anyway because "
                   "every other priced action is worse")
    reason += (". HOLD is NOT comparable -- it needs a settlement model "
               "that does not exist, so this is a ranking over the "
               "exactly-priced subset, not a claim that acting beats "
               "holding, and not a guarantee that it fills")
    out.update(selected=best["action"], selection_reason=reason,
               margin_usd=(best["outcome_if_filled_usd"] - runner["outcome_if_filled_usd"]
                           if runner else None))
    return out
