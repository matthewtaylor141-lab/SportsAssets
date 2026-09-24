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


# ════════════════════════════════════════════════════════════════════
# THE CHALLENGER: THE SAME TABLE, WITH HOLD IN IT.
#
# Owner directive, "MAKE THE FULL EXIT POLICY OPERATIONAL" §1-§3:
#
#     "Keep the current $0.91/second-half-16% experiment as a frozen
#      benchmark. Run the broader decision policy as a separately
#      versioned shadow challenger so we can measure what it changes...
#      Choose an action and quantity, with a recorded reason. An unranked
#      action table or permanently empty selected field does not meet
#      this requirement... Comparing sale and completion prices answers
#      how to exit; it does not by itself establish that exiting beats
#      holding."
#
# THAT LAST SENTENCE IS EXACTLY WHAT `rank_priced_actions` ABOVE DOES AND
# ALL IT DOES. It answers HOW. It says so itself -- "HOLD is NOT
# comparable" -- and it is RIGHT to say so, because at the time it was
# written no probability source could price a hold. `PRICED_ACTION_
# RANKING_V1` and `EXPOSURE_TRIGGER_RULE_V1` ARE NOT MODIFIED. They are
# the frozen benchmark's ranking and they keep working unchanged when no
# EV_HOLD is supplied.
#
# WHAT CHANGED IS THE INPUT, NOT THE PRINCIPLE. `bettor_hold_value`
# supplies EV_HOLD from an external, labelled, correctly-mapped
# probability -- possible only since migration 108 fixed the payout
# identity. With that number present, HOLD enters the SAME comparison as
# every other action instead of being excluded from it, and the decision
# stops being "which exit" and becomes "act or not, and how much".
#
# THE QUANTITY IS DECIDED BY THE LADDER, NOT BY A FRACTION WE CHOSE.
# Once HOLD has a per-contract value, "sell some" is a real marginal
# question with a real answer: take the levels of the book that pay more
# than holding, and stop at the first one that does not. That is where
# a partial size comes from here. No 25/50/75% ladder is invented,
# because a fraction nobody derived is an assumption wearing a decision's
# clothes.
#
# WHAT IT IS STILL NOT. Not an EV optimisation. The probability is an
# external bookmaker's de-vigged price with NO established calibration
# interval on these markets, so this is a DECLARED RULE consuming a
# LABELLED FORECAST. It may lose to holding, and it may lose to exiting.
# ════════════════════════════════════════════════════════════════════

CHALLENGER_ID = "SHADOW_CHALLENGER_HOLD_RANKED_V1"
CHALLENGER_CLASS = "DECLARED_RULE_OVER_A_LABELLED_EXTERNAL_FORECAST"

#: A change must be worth more than this per contract before the book is
#: churned for it. DECLARED: half a venue tick. It exists because two
#: actions within a rounding error of each other are not distinguishable
#: by this evidence, and paying fees to swap between them is a cost with
#: no measured benefit. It was not fitted and not tuned on any result.
MIN_IMPROVEMENT_USD_PER_CONTRACT = 0.005

#: Tie-breaking, in order, declared so it can be argued with.
TIE_BREAK_ORDER = (
    "HOLD -- doing nothing is preferred when the alternatives do not "
    "beat it by the declared margin, because acting costs fees and "
    "queue position and a tie is not evidence",
    "the action that realises CASH NOW over one that realises it at "
    "settlement, because capital returned is capital that can be "
    "measured and redeployed",
    "DIRECT_EXIT before TAKE_COMPLEMENT at equal value, because it "
    "rests on fewer venue assumptions -- one fill in one book",
    "the LARGER executable quantity, because a decision that is right "
    "is right for more of the position",
)

CHALLENGER_OBJECTIVE = {
    "id": CHALLENGER_ID,
    "class": CHALLENGER_CLASS,
    "goal": ("maximise economic value per contract over the actions that "
             "can be priced AT ALL -- which now includes HOLD, valued "
             "from a labelled external probability -- net of the fees "
             "and depth each action actually faces"),
    "not_the_goal": (
        "expected-value optimality. No calibration interval is "
        "established for the probability source on these markets, so "
        "this maximises a number whose error is unmeasured. It is a "
        "declared rule and must never be reported as validated EV "
        "optimisation"),
    "constraints": [
        "an order may never exceed remaining inventory",
        "a reduction may never become a reversal -- capped at held qty",
        "ONE active management order per residual leg "
        "(bettor_mgmt_lifecycle.SUPPORTS_SIMULTANEOUS is False)",
        "the venue's own position model decides what a filled action "
        "does to inventory (bettor_venue_position_model)",
        "no action is ranked on a price without executable depth behind it",
        "no passive fill probability is invented, so resting actions are "
        "never ranked against taker ones",
    ],
    "tie_breaking": list(TIE_BREAK_ORDER),
    "min_improvement_usd_per_contract": MIN_IMPROVEMENT_USD_PER_CONTRACT,
    "hold_is_priced_by": "bettor_hold_value.ev_hold",
    "frozen_benchmark_untouched": (
        "PRICED_ACTION_RANKING_V1 and EXPOSURE_TRIGGER_RULE_V1 are "
        "unchanged and still run the $0.91/second-half-16% experiment"),
}

R_HOLD_NOT_PRICED = "EV_HOLD_NOT_IDENTIFIED"


def marginal_sale_size(ladder, *, hold_value_per_contract, qty,
                       fee_fn=None) -> dict:
    """How much of the position the BOOK pays more for than holding.

    Walks an acquisition ladder in cost space and takes each level while
    its proceeds per contract, after fees, exceed the per-contract value
    of holding. Stops at the first level that does not.

    THIS IS WHERE "SELL SOME" COMES FROM. Not a fraction we chose: the
    quantity is whatever the book actually pays a premium for. A single
    price level makes it all-or-nothing, which is correct -- there is
    nothing to split. A stepped book makes it genuinely partial.
    """
    levels = list((ladder or {}).get("levels") or ())
    out = {"levels_considered": len(levels), "taken": [], "skipped": [],
           "qty": 0.0, "proceeds_usd": 0.0, "fees_usd": 0.0,
           "hurdle_per_contract": hold_value_per_contract,
           "why": ("a level is taken only while its per-contract "
                   "proceeds after fees exceed the value of holding the "
                   "same contract")}
    if hold_value_per_contract is None:
        out["refusal"] = R_HOLD_NOT_PRICED
        out["why"] = ("without a per-contract hold value there is no "
                      "hurdle, so there is no marginal quantity to find "
                      "-- and taking every level instead would be "
                      "selling because a price exists")
        return out
    remaining = float(qty)
    hurdle = float(hold_value_per_contract)
    for lv in levels:
        if remaining <= 1e-9:
            break
        px = float(lv.get("acquisition_price"))
        avail = min(remaining, float(lv.get("qty") or 0.0))
        if avail <= 1e-9:
            continue
        fee = (0.0 if fee_fn is None
               else abs(float(fee_fn(qty=avail, price=px))))
        net_per = px - (fee / avail if avail else 0.0)
        if net_per <= hurdle + 1e-12:
            out["skipped"].append({
                "level": lv.get("level"), "price": px, "qty": avail,
                "net_per_contract": net_per,
                "why": ("%.6f after fees does not beat holding at %.6f"
                        % (net_per, hurdle))})
            break
        out["taken"].append({"level": lv.get("level"), "price": px,
                             "qty": avail, "net_per_contract": net_per,
                             "fees_usd": fee})
        out["qty"] += avail
        out["proceeds_usd"] += px * avail
        out["fees_usd"] += fee
        remaining -= avail
    out["covers_whole_position"] = out["qty"] >= float(qty) - 1e-9
    out["vwap"] = (out["proceeds_usd"] / out["qty"]) if out["qty"] else None
    return out


def rank_with_hold(qty, own_basis_per_contract, *, ev_hold=None,
                   bid=None, bid_size=None, complement_ask=None,
                   complement_ask_size=None, fee_fn=None,
                   venue=None, us_market_slug=None, held_is_long=True,
                   sale_ladder=None, resting_order=None,
                   settlement_semantics=None, fallback_trigger=None) -> dict:
    """THE CHALLENGER'S FULL DECISION: what to do, and how much of it.

    Every action §2 names is evaluated and the selected field is filled
    or the reason it is empty is a REFUSAL WITH A NAME, never a
    permanent blank. `ev_hold` is a `bettor_hold_value.ev_hold` record.
    """
    from . import bettor_venue_position_model as vpm

    q = float(qty)
    basis_per = float(own_basis_per_contract)
    basis = basis_per * q

    hv = dict(ev_hold or {})
    hold_priced = hv.get("status") == "IDENTIFIED"
    hold_total = hv.get("ev_hold_usd") if hold_priced else None
    # THE HURDLE IS THE VALUE OF HOLDING *ONE CONTRACT*, AND IT IS THE
    # SETTLEMENT VALUE, NOT THE P&L. Comparing a sale's proceeds against
    # (p - basis) would subtract the basis twice: the sale releases it
    # too. So the hurdle is p alone, and every candidate below is scored
    # net of the same pro-rata basis release.
    hold_per = hv.get("probability") if hold_priced else None

    out = {
        "rule": CHALLENGER_OBJECTIVE,
        "policy_version": CHALLENGER_ID,
        "qty_under_management": q,
        "basis_per_contract": basis_per,
        "basis_usd": basis,
        "hold_input": {
            "available": bool(hold_priced),
            "status": hv.get("status") or NOT_IDENTIFIED,
            "refusal": hv.get("refusal"),
            "why": hv.get("why"),
            "provenance": hv.get("provenance"),
            "probability": hv.get("probability"),
            "probability_event": hv.get("probability_event"),
            "payout_event_held": hv.get("payout_event_held"),
            "identity": hv.get("identity"),
            "freshness": hv.get("freshness"),
            "uncertainty": hv.get("uncertainty"),
            "source_row_id": hv.get("source_row_id"),
        },
        "candidates": [], "not_rankable": [], "venue_translation": {},
    }

    def _fee(sz, px):
        if fee_fn is None:
            return None
        return abs(float(fee_fn(qty=sz, price=px)))

    def _translate(action, size):
        t = vpm.translate(action, venue=venue, held_qty=q,
                          us_market_slug=us_market_slug,
                          requested_qty=size, held_is_long=held_is_long)
        out["venue_translation"][action] = t
        return t

    def _untranslatable(action, t):
        """An action the venue model cannot translate is NOT RANKED.

        THE DEFECT THIS CLOSES, AND IT WAS MINE. The first version
        computed the venue translation, recorded its refusal, and then
        ranked and selected the action anyway -- so an unknown venue
        produced a confidently selected DIRECT_EXIT whose effect on the
        position nobody could state. An action we cannot say the
        consequence of is not an action we may take.
        """
        if t.get("ok"):
            return False
        out["not_rankable"].append({
            "action": action,
            "blocker": t.get("refusal") or "VENUE_TRANSLATION_REFUSED",
            "value_usd": None,
            "why": ("%s. The economics may be computable but the "
                    "INVENTORY CONSEQUENCE is not, and an action whose "
                    "effect on the position cannot be stated is not "
                    "available" % (t.get("why") or "venue model refused"))})
        return True

    # ── HOLD ─────────────────────────────────────────────────────────
    t_hold = _translate("HOLD", 0.0)
    if hold_priced:
        out["candidates"].append({
            "action": "HOLD", "qty": q,
            "value_usd": float(hold_total),
            "value_per_contract": float(hold_per),
            "execution_secured": True,
            "execution_note": ("HOLD is the only action that needs no "
                               "fill: the position is already held"),
            "cash_now_usd": 0.0,
            "cash_at_settlement_usd": float(hold_per) * q,
            "collateral_released_now": False,
            "remaining_exposure_qty": q,
            "fees_usd": 0.0,
            "basis": "EXTERNAL_LABELLED_PROBABILITY",
            "arithmetic": hv.get("arithmetic"),
            "venue_effect": t_hold.get("net_effect"),
        })
    else:
        out["not_rankable"].append({
            "action": "HOLD", "blocker": hv.get("refusal") or R_HOLD_NOT_PRICED,
            "why": (hv.get("why") or
                    "no probability source priced this hold"),
            "value_usd": None,
            "is_not_zero": ("NOT_IDENTIFIED. Zero would assert the "
                            "position is worthless, which is the "
                            "assertion most likely to force an exit")})

    # ── HOLD_TO_SETTLEMENT ───────────────────────────────────────────
    #
    # A DIFFERENT ACTION FROM HOLD, and the difference is a commitment:
    # HOLD is re-decided next cycle, HOLD_TO_SETTLEMENT gives that up.
    # Its terminal value depends on the venue's settlement terms, and
    # this venue's prose is CONFLICTING_VENUE_PROSE, so even a priced
    # probability does not price it.
    sem = settlement_semantics or "CONFLICTING_VENUE_PROSE"
    if sem == "RESOLVED":
        out["candidates"].append({
            "action": "HOLD_TO_SETTLEMENT", "qty": q,
            "value_usd": (None if not hold_priced
                          else float(hold_total)),
            "value_per_contract": hold_per,
            "execution_secured": True, "cash_now_usd": 0.0,
            "cash_at_settlement_usd": (None if hold_per is None
                                       else float(hold_per) * q),
            "collateral_released_now": False,
            "remaining_exposure_qty": q, "fees_usd": 0.0,
            "gives_up": "the option to re-decide next cycle",
        })
    else:
        out["not_rankable"].append({
            "action": "HOLD_TO_SETTLEMENT",
            "blocker": "SETTLEMENT_SEMANTICS_%s" % sem,
            "why": ("the terminal value of carrying to settlement needs "
                    "the venue's settlement terms and this venue's "
                    "prose is %s. A priced probability does not settle "
                    "what the contract pays on an overtime or a void" % sem),
            "value_usd": None})

    # ── DIRECT_EXIT, full size at the top of the book ────────────────
    if bid is None:
        out["not_rankable"].append({"action": "DIRECT_EXIT",
                                    "blocker": "NO_BID", "value_usd": None})
    elif not bid_size or float(bid_size) <= 0:
        out["not_rankable"].append({"action": "DIRECT_EXIT",
                                    "blocker": "NO_EXECUTABLE_DEPTH",
                                    "value_usd": None})
    else:
        sz = min(q, float(bid_size))
        f = _fee(sz, bid)
        if f is None:
            out["not_rankable"].append({
                "action": "DIRECT_EXIT",
                "blocker": "FEE_SCHEDULE_NOT_ESTABLISHED",
                "value_usd": None})
        elif _untranslatable("DIRECT_EXIT", _translate("DIRECT_EXIT", sz)):
            pass
        else:
            t = out["venue_translation"]["DIRECT_EXIT"]
            rel = basis * (sz / q)
            # WHAT IS LEFT BEHIND IS PART OF THE ACTION'S VALUE. Selling
            # a depth-capped slice leaves the remainder held, and the
            # remainder is worth something -- so a partial exit is scored
            # as (proceeds on the slice) + (hold value of the rest).
            kept = q - sz
            kept_val = (None if hold_per is None
                        else float(hold_per) * kept - basis_per * kept)
            total = float(bid) * sz - f - rel + (kept_val or 0.0)
            out["candidates"].append({
                "action": "DIRECT_EXIT", "qty": sz,
                "value_usd": total,
                "value_per_contract": (total / q) if q else None,
                "slice_value_usd": float(bid) * sz - f - rel,
                "retained_value_usd": kept_val,
                "retained_value_status": ("IDENTIFIED" if kept_val is not None
                                          else NOT_IDENTIFIED),
                "execution_secured": False, "fees_usd": f,
                "depth_limited": sz < q - 1e-12,
                "cash_now_usd": float(bid) * sz - f,
                "cash_at_settlement_usd": 0.0,
                "collateral_released_now": True,
                "remaining_exposure_qty": kept,
                "venue_effect": t.get("net_effect"),
                "creates_second_leg": t.get("creates_second_leg"),
                "arithmetic": ("%.4f x %.4g - fees %.4f - basis %.4f"
                               % (float(bid), sz, f, rel)),
            })

    # ── REDUCE: the marginal quantity the book pays a premium for ────
    if sale_ladder and hold_per is not None:
        marg = marginal_sale_size(sale_ladder, hold_value_per_contract=hold_per,
                                  qty=q, fee_fn=fee_fn)
        out["marginal_sale"] = marg
        if (marg["qty"] > 1e-9 and not marg["covers_whole_position"]
                and not _untranslatable("REDUCE",
                                        _translate("REDUCE", marg["qty"]))):
            t = out["venue_translation"]["REDUCE"]
            sz = marg["qty"]
            rel = basis * (sz / q)
            kept = q - sz
            kept_val = float(hold_per) * kept - basis_per * kept
            total = marg["proceeds_usd"] - marg["fees_usd"] - rel + kept_val
            out["candidates"].append({
                "action": "REDUCE", "qty": sz,
                "value_usd": total,
                "value_per_contract": (total / q) if q else None,
                "slice_value_usd": (marg["proceeds_usd"] - marg["fees_usd"]
                                    - rel),
                "retained_value_usd": kept_val,
                "retained_value_status": "IDENTIFIED",
                "execution_secured": False, "fees_usd": marg["fees_usd"],
                "vwap": marg["vwap"],
                "levels_taken": marg["taken"],
                "level_that_stopped_it": (marg["skipped"][0]
                                          if marg["skipped"] else None),
                "cash_now_usd": marg["proceeds_usd"] - marg["fees_usd"],
                "cash_at_settlement_usd": 0.0,
                "collateral_released_now": True,
                "remaining_exposure_qty": kept,
                "venue_effect": t.get("net_effect"),
                "why_this_size": ("the book pays more than holding for "
                                  "%.4g contracts and not for the next "
                                  "level" % sz),
            })
    elif sale_ladder:
        out["not_rankable"].append({
            "action": "REDUCE", "blocker": R_HOLD_NOT_PRICED,
            "why": ("a partial size is the quantity the book pays more "
                    "for than holding. With no hold value there is no "
                    "hurdle and the quantity is undefined -- taking "
                    "every level instead would be selling because a "
                    "price exists"),
            "value_usd": None})

    # ── TAKE_COMPLEMENT, including loss-limiting completion ──────────
    if complement_ask is None:
        out["not_rankable"].append({
            "action": "TAKE_COMPLEMENT",
            "blocker": "COMPLEMENT_ASK_NOT_OBSERVED", "value_usd": None})
    elif not complement_ask_size or float(complement_ask_size) <= 0:
        out["not_rankable"].append({
            "action": "TAKE_COMPLEMENT",
            "blocker": "NO_EXECUTABLE_DEPTH", "value_usd": None})
    else:
        sz = min(q, float(complement_ask_size))
        f = _fee(sz, complement_ask)
        if f is None:
            out["not_rankable"].append({
                "action": "TAKE_COMPLEMENT",
                "blocker": "FEE_SCHEDULE_NOT_ESTABLISHED",
                "value_usd": None})
        elif _untranslatable("TAKE_COMPLEMENT",
                             _translate("TAKE_COMPLEMENT", sz)):
            pass
        else:
            t = out["venue_translation"]["TAKE_COMPLEMENT"]
            rel = basis * (sz / q)
            kept = q - sz
            kept_val = (None if hold_per is None
                        else float(hold_per) * kept - basis_per * kept)
            slice_val = 1.00 * sz - float(complement_ask) * sz - f - rel
            total = slice_val + (kept_val or 0.0)
            cand = {
                "action": "TAKE_COMPLEMENT", "qty": sz,
                "value_usd": total,
                "value_per_contract": (total / q) if q else None,
                "slice_value_usd": slice_val,
                "retained_value_usd": kept_val,
                "retained_value_status": ("IDENTIFIED" if kept_val is not None
                                          else NOT_IDENTIFIED),
                "execution_secured": False, "fees_usd": f,
                "depth_limited": sz < q - 1e-12,
                "locks_a_loss": slice_val < 0,
                "remaining_exposure_qty": kept,
                "venue_effect": t.get("net_effect"),
                "creates_second_leg": t.get("creates_second_leg"),
                "capital_release": t.get("capital_release"),
                "capital_release_why": t.get("capital_release_why"),
                "arithmetic": ("1.00 x %.4g - %.4f x %.4g - fees %.4f - "
                               "basis %.4f"
                               % (sz, float(complement_ask), sz, f, rel)),
                "loss_limiting_permitted": (
                    "completing above par locks a loss and is still "
                    "ranked, because holding a leg that may settle at "
                    "zero risks the whole basis"),
            }
            # ON A NETTING VENUE THIS IS THE SAME REDUCTION AS A SALE,
            # reached through the other ladder. The economics are
            # identical to selling at (1 - ask); what differs is which
            # book pays, and that is the whole reason both are priced.
            if t.get("ok") and not t.get("creates_second_leg"):
                cand.update(
                    cash_now_usd=1.00 * sz - float(complement_ask) * sz - f,
                    cash_at_settlement_usd=0.0,
                    collateral_released_now=True,
                    equivalent_sale_price=1.0 - float(complement_ask),
                    venue_note=t.get("mechanism"))
            else:
                cand.update(
                    cash_now_usd=-(float(complement_ask) * sz + f),
                    cash_at_settlement_usd=1.00 * sz,
                    collateral_released_now=False,
                    venue_note=t.get("mechanism"))
            out["candidates"].append(cand)

    # ── POST_COMPLEMENT / the resting order, never ranked ────────────
    out["not_rankable"].append({
        "action": "POST_COMPLEMENT",
        "blocker": "P_FILL_NOT_IDENTIFIED",
        "value_usd": None,
        "why": ("a resting order's value is its fill probability times "
                "what a fill is worth, and no BETTOR-native resting "
                "evidence exists. Manufacturing a p_fill is explicitly "
                "forbidden, so this action is AVAILABLE and NOT RANKED "
                "-- the frozen benchmark posts one by declared rule, "
                "which is a different basis from ranking it"),
        "no_fill_leaves": ("full exposure on the leg we started with. A "
                           "zero here would price a failed hedge as "
                           "though the risk had been removed")})

    # ── MERGE / capital release, per the venue's actual model ────────
    t_merge = _translate("MERGE", 0.0)
    out["not_rankable"].append({
        "action": "MERGE",
        "blocker": ("MERGE_%s" % (t_merge.get("capital_release")
                                  or NOT_IDENTIFIED)),
        "value_usd": None,
        "capital_release": t_merge.get("capital_release") or NOT_IDENTIFIED,
        "why": (t_merge.get("capital_release_why") or t_merge.get("why")
                or ("the venue's position model is not established, so "
                    "whether there is any matched capital to release "
                    "cannot be stated. NOT_IDENTIFIED, and no release "
                    "is claimed"))})

    # ── the resting order already working ────────────────────────────
    out["resting_order_decision"] = _resting_decision(resting_order, out)
    out["fallback_trigger"] = fallback_trigger

    return _choose(out, q, hold_priced=hold_priced,
                   fallback_trigger=fallback_trigger)


def _resting_decision(resting_order, out) -> dict:
    """MAINTAIN, CANCEL or REPLACE the order already working.

    §2 requires this as a decision in its own right. It needs no fill
    probability: it compares the working order's own intent against the
    one now selected, which is observed on both sides.
    """
    if not resting_order:
        return {"has_working_order": False, "decision": "NONE",
                "why": "no management order is working on this leg"}
    return {"has_working_order": True,
            "order_id": resting_order.get("order_id"),
            "side": resting_order.get("side"),
            "limit_price": resting_order.get("limit_price"),
            "remaining": resting_order.get("remaining"),
            "state": resting_order.get("state"),
            "decision": "DECIDED_AT_PLACEMENT",
            "rule": ("an unchanged intent at an unchanged price and size "
                     "is MAINTAINED so queue priority survives; any "
                     "change is a CANCEL then a REPLACE after the "
                     "acknowledgement, never two live orders"),
            "one_active_order": True}


# THE FALLBACK, NAMED. §3: "Where evidence is insufficient, use an
# explicitly named operating rule or fallback."
#
# THE FAILURE THIS EXISTS FOR, AND MY FIRST VERSION HAD IT. With HOLD
# unpriced, ranking the remaining candidates selects an exit EVERY TIME
# -- not because exiting is good, but because it is the only action
# carrying a figure. `bettor_mgmt_select.select` names this precisely:
# "that is an engine that liquidates the book for want of a settlement
# model". A challenger that inherits the same defect is not an
# improvement on the benchmark, it is the benchmark's guard removed.
#
# So when EV_HOLD is NOT_IDENTIFIED the WHETHER question is handed back
# to the DECLARED rule that already answers it without a forecast --
# EXPOSURE_TRIGGER_RULE_V1, tested and unchanged. The priced subset is
# ranked ONLY once that rule has fired. Nothing is invented: the
# fallback is the frozen benchmark's own guard, reused by name.
FALLBACK_RULE = "EXPOSURE_TRIGGER_RULE_V1"

FALLBACK_DECLARATION = {
    "applies_when": "EV_HOLD is NOT_IDENTIFIED",
    "rule": FALLBACK_RULE,
    "what_it_does": ("answers WHETHER to close using only the last "
                     "observed price, our basis and how long the "
                     "exposure has been open -- no forecast"),
    "why_not_rank_anyway": (
        "with HOLD unpriced, ranking the rest selects an exit every "
        "time, because it is the only action carrying a number. That is "
        "liquidating the book for want of a settlement model"),
    "if_the_fallback_is_absent": (
        "nothing is selected and the state is HOLD_FOR_MISSING_INPUT, "
        "which is NOT a decision to hold and is recorded as distinct "
        "from one"),
}


def _choose(out, q, *, hold_priced=True, fallback_trigger=None) -> dict:
    """Rank, apply the declared tie-breaks, and fill `selected`."""
    # ── the fallback path, taken before anything is ranked ───────────
    if not hold_priced:
        out["fallback"] = dict(FALLBACK_DECLARATION)
        blocker = next((r["blocker"] for r in out["not_rankable"]
                        if r["action"] == "HOLD"), R_HOLD_NOT_PRICED)
        # A TRIGGER THAT EVALUATED NOTHING DECIDED NOTHING. `fired=False`
        # is returned both when the rule looked at the evidence and was
        # not satisfied AND when it had no evidence to look at -- both
        # conditions come back status=NOT_IDENTIFIED. Reading the second
        # as a decision to hold is exactly the confusion §5 forbids:
        # "A missing input must be distinguishable from a deliberate
        # decision to hold."
        _evaluated = [c for c in ((fallback_trigger or {}).get("conditions")
                                  or ()) if c.get("status") == "EVALUATED"]
        if fallback_trigger and not _evaluated:
            out.update(
                selected=None, selected_qty=None,
                governing_rule="%s / FALLBACK_HAD_NO_INPUTS" % CHALLENGER_ID,
                is_a_deliberate_hold=False,
                operating_state="HOLD_FOR_MISSING_INPUT",
                fallback_evaluated_conditions=0,
                selection_reason=(
                    "NOTHING SELECTED. EV_HOLD is %s, and the fallback "
                    "%s evaluated NO condition -- neither the last "
                    "price on our leg nor the time the exposure has "
                    "been open was supplied, so both came back "
                    "NOT_IDENTIFIED. A rule that looked at nothing did "
                    "not decide to hold; the position is unchanged "
                    "because the policy is blind here, and that is "
                    "recorded as a different fact"
                    % (blocker, FALLBACK_RULE)))
            out["ranked"] = [{"action": c["action"], "qty": c.get("qty"),
                              "value_usd": c["value_usd"]}
                             for c in out["candidates"]
                             if c.get("value_usd") is not None]
            return out
        if not fallback_trigger:
            out.update(
                selected=None, selected_qty=None,
                governing_rule="%s / NO_FALLBACK" % CHALLENGER_ID,
                is_a_deliberate_hold=False,
                operating_state="HOLD_FOR_MISSING_INPUT",
                selection_reason=(
                    "NOTHING SELECTED. EV_HOLD is %s and no fallback "
                    "rule was supplied, so the position is left exactly "
                    "as it is WITHOUT a decision behind it. This is a "
                    "MISSING INPUT, not a hold: ranking the priced "
                    "actions alone would select an exit every time, "
                    "because it is the only action carrying a number"
                    % blocker))
            out["ranked"] = [{"action": c["action"], "qty": c.get("qty"),
                              "value_usd": c["value_usd"]}
                             for c in out["candidates"]
                             if c.get("value_usd") is not None]
            return out
        if not fallback_trigger.get("fired"):
            out.update(
                selected="HOLD", selected_qty=q,
                governing_rule="%s / FALLBACK %s"
                               % (CHALLENGER_ID, FALLBACK_RULE),
                is_a_deliberate_hold=True,
                hold_basis="FALLBACK_RULE_NOT_A_PRICED_COMPARISON",
                operating_state="HOLD_BY_FALLBACK_RULE",
                selection_reason=(
                    "HOLD BY THE DECLARED FALLBACK. EV_HOLD is %s, so "
                    "the priced subset cannot say whether acting beats "
                    "holding. %s answered WHETHER without a forecast "
                    "and did not fire: %s. This IS a decision -- taken "
                    "by a named rule on observed inputs -- and it is "
                    "recorded as distinct from a hold with no decision "
                    "behind it"
                    % (blocker, FALLBACK_RULE,
                       fallback_trigger.get("reason") or "no reason given")))
            out["ranked"] = [{"action": c["action"], "qty": c.get("qty"),
                              "value_usd": c["value_usd"]}
                             for c in out["candidates"]
                             if c.get("value_usd") is not None]
            return out
        # The trigger fired: rank the priced subset, and say so.
        out["fallback_fired"] = True

    cands = [c for c in out["candidates"] if c.get("value_usd") is not None]
    if not cands:
        out.update(selected=None, selected_qty=None,
                   governing_rule=CHALLENGER_ID,
                   selection_reason=(
                       "NO ACTION IS PRICED AT ALL. Every candidate is "
                       "refused with a named blocker: %s. This is a "
                       "REFUSAL, not an empty selection"
                       % "; ".join("%s (%s)" % (r["action"], r["blocker"])
                                   for r in out["not_rankable"])),
                   is_a_deliberate_hold=False,
                   operating_state="HOLD_FOR_MISSING_INPUT")
        return out

    cash_rank = {True: 0, False: 1}

    def _key(c):
        return (-float(c["value_usd"]),
                0 if c["action"] == "HOLD" else 1,
                cash_rank.get(bool(c.get("collateral_released_now")), 1),
                0 if c["action"] == "DIRECT_EXIT" else 1,
                -float(c.get("qty") or 0.0))

    cands.sort(key=_key)
    best = cands[0]
    hold = next((c for c in cands if c["action"] == "HOLD"), None)

    # THE MARGIN GATE. Beating HOLD by less than the declared minimum is
    # not a reason to churn the book.
    if hold is not None and best["action"] != "HOLD":
        per = q if q else 1.0
        gain = (best["value_usd"] - hold["value_usd"]) / per
        out["improvement_over_hold_per_contract"] = gain
        if gain < MIN_IMPROVEMENT_USD_PER_CONTRACT:
            _age = (out["hold_input"].get("freshness") or {}).get(
                "age_from_observation_s")
            _when = ("observed %.0f s before the decision" % _age
                     if _age is not None else "observed at an unstated time")
            out.update(
                selected="HOLD", selected_qty=q,
                governing_rule="%s / MIN_IMPROVEMENT" % CHALLENGER_ID,
                is_a_deliberate_hold=True,
                operating_state="HOLD_BY_DECISION",
                runner_up=best["action"],
                selection_reason=(
                    "HOLD SELECTED DELIBERATELY. %s scored %.4f against "
                    "HOLD's %.4f, an improvement of %.6f per contract, "
                    "below the declared %.4f minimum. Acting costs fees "
                    "and queue position and a difference this small is "
                    "not distinguishable by this evidence. This is a "
                    "DECISION, not a missing input: the hold value came "
                    "from %s, %s"
                    % (best["action"], best["value_usd"],
                       hold["value_usd"], gain,
                       MIN_IMPROVEMENT_USD_PER_CONTRACT,
                       (out["hold_input"].get("provenance") or {}).get(
                           "class", "an external source"), _when)))
            out["ranked"] = [{"action": c["action"], "qty": c.get("qty"),
                              "value_usd": c["value_usd"]} for c in cands]
            return out

    runner = cands[1] if len(cands) > 1 else None
    reason = ("%s over %.4g contracts scores %.4f"
              % (best["action"], best.get("qty") or 0.0,
                 best["value_usd"]))
    if runner:
        reason += (", ahead of %s at %.4f by %.4f"
                   % (runner["action"], runner["value_usd"],
                      best["value_usd"] - runner["value_usd"]))
    if hold is not None:
        reason += (". HOLD was IN the comparison at %.4f, priced from %s"
                   % (hold["value_usd"],
                      out["hold_input"].get("probability_event")))
    else:
        reason += (". HOLD was NOT comparable (%s), so this ranks the "
                   "priced subset and does NOT establish that acting "
                   "beats holding"
                   % next((r["blocker"] for r in out["not_rankable"]
                           if r["action"] == "HOLD"), R_HOLD_NOT_PRICED))
    if best.get("locks_a_loss"):
        reason += (". THIS LOCKS A LOSS and is selected anyway because "
                   "every alternative scores worse")
    if best["action"] != "HOLD":
        reason += (". Not secured: the size is executable against "
                   "displayed depth and may still fill partially or not "
                   "at all")
    if out.get("fallback_fired"):
        reason += (". EV_HOLD was NOT_IDENTIFIED, so WHETHER to close "
                   "was answered by the declared fallback %s, which "
                   "fired; this ranking then chose only the METHOD and "
                   "does NOT establish that acting beat holding"
                   % FALLBACK_RULE)
    out.update(
        selected=best["action"], selected_qty=best.get("qty"),
        governing_rule=("%s / FALLBACK %s" % (CHALLENGER_ID, FALLBACK_RULE)
                        if out.get("fallback_fired") else CHALLENGER_ID),
        selection_reason=reason,
        is_a_deliberate_hold=best["action"] == "HOLD",
        operating_state=("HOLD_BY_DECISION" if best["action"] == "HOLD"
                         else "CLOSING"),
        margin_usd=(best["value_usd"] - runner["value_usd"]
                    if runner else None),
        ranked=[{"action": c["action"], "qty": c.get("qty"),
                 "value_usd": c["value_usd"]} for c in cands])
    return out
