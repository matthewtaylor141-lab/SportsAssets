"""ROUTING A POSITION TO THE ENGINES THAT ALREADY EXIST.

THIS MODULE COMPUTES ALMOST NOTHING. Every figure below is produced by a
module that was already here, and the only thing that was missing is the
ROUTING between them. That absence is why both engines appeared to have a
hole in them:

    bettor_exit_engine   "holding both is a pair question for the pair
                          engine"
    bettor_pair_engine   "holding both is an inventory question, not a
                          completion one"

Each deferred the both-legs state to the other and neither handled it, so
a completed position fell between them. Neither is wrong. The state they
were both declining is not a single position at all -- it is TWO, and
splitting it is an inventory job that `bettor_inventory` already does.

    100 YES and 40 NO is 40 MATCHED PAIRS and 60 RESIDUAL YES.

Once split, each part goes to the engine whose domain it actually is:

    60 residual YES   -> bettor_exit_engine.evaluate()   exactly one leg
                         held, which is precisely its contract
    40 matched pairs  -> matched accounting below, where the only
                         question is whether capital can be released

WHAT EACH EXISTING MODULE CONTRIBUTES, so nothing here is mistaken for
new work:

    research/.../inventory_state.py   MATCHED_QTY, PAIR_BASIS,
                                      LOCKED_PNL, CAPITAL_OCCUPIED_BY_*
                                      in exact decimal
    bettor_inventory.inventory()      that state built from the ledger,
                                      plus the PAIR CLAIM
    bettor_identity_bindings          whether min(YES,NO) is really a
                                      complement pair
    bettor_exit_engine.evaluate()     the residual's action table
    bettor_pair_engine.pair_view()    what COMPLETING would take
    bettor_mgmt_select.select()       the declared selection rule
    bettor_desk.Order / Portfolio     the order lifecycle and accounting

HOLDING BOTH LEGS DOES NOT REMOVE THE RESIDUAL FROM MANAGEMENT, and that
is the failure this routing exists to prevent. A book of 100 YES and 40
NO is not "hedged"; it is 40 locked pairs plus a 60-lot directional
position that still needs an exit decision every cycle. An engine that
saw "both legs held" and stopped would have silently dropped that 60
from management -- which is the Ferrari residual failure in miniature,
where the matched machinery looked excellent and the residual changed
the sign of the strategy.

NO CAPITAL RELEASE IS MANUFACTURED. A matched pair returns capital only
if the venue lets the matched quantity be merged or netted, and
`bettor_pair_engine` is explicit that the institutional MERGE_MECHANISM
is NOT_IDENTIFIED -- which is NOT zero, because zero would assert that
completing frees nothing. So matched inventory is RETAINED and accounted
for at its basis, its capital shown as occupied, and the release left
NOT_IDENTIFIED with the reason attached.
"""

from __future__ import annotations

from . import bettor_exit_engine as ee
from . import bettor_inventory as binv
from . import bettor_mgmt_select as sel
from . import bettor_pair_engine as pe

VERSION = "BETTOR_MGMT_ROUTER_V1"
NOT_IDENTIFIED = "NOT_IDENTIFIED"

# What the selection is FOR. Stated because a selection rule without a
# declared objective cannot be argued with, and because this one is NOT
# "maximise expected value" -- that is unavailable, and saying so is the
# point.
OBJECTIVE = {
    "goal": ("decide, for each part of the position, whether to act or "
             "to retain -- and to refuse rather than guess when the "
             "comparison that would justify acting is unavailable"),
    "not_the_goal": ("maximising expected value. EV_VS_HOLD is "
                     "NOT_IDENTIFIED on every action the exit engine "
                     "prices, because ranking against HOLD needs an "
                     "independent fair value that is not established. A "
                     "rule that acted anyway would be optimising a "
                     "quantity nobody can compute"),
    "experimental_rules_are_allowed": (
        "a labelled experimental rule may be TESTED without being called "
        "an EV-optimal decision. What it may not do is borrow the "
        "authority of an optimisation it did not perform"),
}

# Every input's provenance. MODEL means a fitted estimator; RULE means a
# declared decision procedure; ASSUMPTION means a number we chose and
# cannot measure; OBSERVED means it came off the venue or the chain.
LABELS = {
    "MATCHED_QTY": "OBSERVED (arithmetic over observed fills)",
    "PAIR_BASIS": "OBSERVED (average-cost convention, named on the row)",
    "LOCKED_PNL": "OBSERVED, CONDITIONAL on the pair claim being confirmed",
    "pairStatus": "OBSERVED (bettor_identity_bindings verdict)",
    "residual_exit_prices": "OBSERVED (the venue's own book)",
    "EV_VS_HOLD": "NOT_IDENTIFIED -- needs an independent fair value",
    "capital_release": "NOT_IDENTIFIED -- institutional MERGE_MECHANISM",
    "P_FILL": "NOT_IDENTIFIED -- no BETTOR-native resting evidence",
    "P_PAIR_COMPLETION": ("NOT_IDENTIFIED. The whale hazard table is a "
                          "PRIOR about their completions and is marked "
                          "WHALE_COMPLETION_AS_P_FILL: FORBIDDEN"),
    "queue_share": "ASSUMPTION",
    "selection_hurdle": "RULE (declared, not fitted)",
}


def _residual_only(state: dict, leg: str) -> dict:
    """A one-leg inventory view of the residual, for the exit engine.

    WHY THIS IS NOT A FAKE INVENTORY. The exit engine's contract is
    "exactly one leg held", and the residual genuinely is exactly one
    leg: the matched quantity is a different position with a different
    question. Passing the FULL book instead would make the engine refuse
    the whole comparison and the residual would go unmanaged.

    The basis passed is the residual basis under the AVERAGE-COST
    convention `bettor_inventory` names, not a recomputed one.
    """
    qty = state.get("RESIDUAL_%s_QTY" % leg)
    basis = state.get("RESIDUAL_%s_BASIS" % leg)
    other = binv.LEG_NO if leg == binv.LEG_YES else binv.LEG_YES
    return {
        "%s_QTY" % leg: qty,
        "%s_AVG_BASIS" % leg: basis,
        "%s_QTY" % other: "0",
        "%s_AVG_BASIS" % other: None,
        "_routed_as": "RESIDUAL_ONLY",
        "_why": ("the matched quantity is excluded because it is a "
                 "different position with a different question, not "
                 "because it stopped existing"),
    }


def route(rows, *, identity_status=None, held_book=None,
          complement_book=None, seconds_unpaired=None,
          observed_payout=None, root=None) -> dict:
    """Split the position, then send each part to its own engine."""
    state = binv.inventory(rows, identity_status=identity_status,
                           time_in_inventory_s=seconds_unpaired, root=root)

    def _n(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    yq, nq = _n(state.get("YES_QTY")), _n(state.get("NO_QTY"))
    matched = state.get("MATCHED_QTY")
    matched_n = _n(matched) if matched != NOT_IDENTIFIED else 0.0
    res_y, res_n = (_n(state.get("RESIDUAL_YES_QTY")),
                    _n(state.get("RESIDUAL_NO_QTY")))

    out = {
        "version": VERSION,
        "objective": OBJECTIVE,
        "labels": LABELS,
        "inventory": state,
        "split": {
            "YES_QTY": yq, "NO_QTY": nq,
            "MATCHED_QTY": matched,
            "RESIDUAL_YES_QTY": state.get("RESIDUAL_YES_QTY"),
            "RESIDUAL_NO_QTY": state.get("RESIDUAL_NO_QTY"),
            "pairStatus": state.get("pairStatus"),
            "rule": ("min(YES, NO) is matched ONLY when the identity "
                     "layer confirms the complement. Otherwise the legs "
                     "are held separately and there is no pair to "
                     "manage -- a locked P&L across an unconfirmed pair "
                     "would book profit on an assumption"),
        },
        "engines_used": [],
    }

    # ── the MATCHED part ────────────────────────────────────────────
    if matched_n > 0:
        out["matched"] = {
            "qty": matched,
            "pair_basis": state.get("MATCHED_PAIR_BASIS"),
            "capital_occupied": state.get("MATCHED_CAPITAL"),
            "locked_pnl": state.get("LOCKED_PNL"),
            "action_available": "RETAIN_MATCHED",
            "capital_release": NOT_IDENTIFIED,
            "capital_release_why": pe.WHY_CAPITAL_RELEASE_NOT_IDENTIFIED,
            "merge": NOT_IDENTIFIED,
            "why_only_retain": (
                "the only thing that could be DONE with a matched pair "
                "today is release its capital by merging or netting it, "
                "and the institutional merge mechanism is "
                "NOT_IDENTIFIED. So the pair is RETAINED and accounted "
                "for at its basis. Its capital stays shown as occupied "
                "rather than freed, because freeing it would be a claim "
                "about the venue we have not established"),
        }
        out["engines_used"].append("bettor_pair_engine (capital-release "
                                   "refusal)")
    elif matched == NOT_IDENTIFIED and yq > 0 and nq > 0:
        out["matched"] = {
            "qty": NOT_IDENTIFIED,
            "why": ("both legs are held but the pair is %s, so no "
                    "matched quantity is claimed and the legs are "
                    "managed separately"
                    % state.get("pairStatus")),
            "capital_release": NOT_IDENTIFIED,
        }

    # ── the RESIDUAL part, which is where management happens ────────
    #
    # A residual on BOTH legs cannot happen when a pair is confirmed --
    # matching consumes min(YES, NO) -- so at most one of these runs.
    # If the pair is UNCONFIRMED both legs stand alone and neither is a
    # residual of anything; each is evaluated as its own one-leg book.
    residuals = []
    for leg, q in ((binv.LEG_YES, res_y), (binv.LEG_NO, res_n)):
        if q <= 0:
            continue
        inv = _residual_only(state, leg)
        table = ee.evaluate(inv, held_book=held_book,
                            complement_book=complement_book,
                            seconds_unpaired=seconds_unpaired)
        chosen = sel.select(inv, held_book=held_book,
                            complement_book=complement_book,
                            observed_payout=observed_payout,
                            qty=q,
                            basis_usd=_n(state.get("RESIDUAL_%s_BASIS"
                                                   % leg)) * q,
                            seconds_unpaired=seconds_unpaired)
        residuals.append({
            "leg": leg, "qty": q,
            "basis_per_contract": state.get("RESIDUAL_%s_BASIS" % leg),
            "basis_convention": state.get("basisConvention"),
            "actions": table.get("actions"),
            "engine_status": table.get("status"),
            "selected": chosen.get("selected"),
            "selection_reason": chosen.get("selection_reason"),
            "requires_our_fill": chosen.get("requires_our_fill"),
            "hold_to_settlement": chosen.get("hold_to_settlement"),
            "completion_view": pe.pair_view(
                inv, complement_book, seconds_unpaired=seconds_unpaired,
                root=root),
        })
        out["engines_used"] += ["bettor_exit_engine.evaluate",
                                "bettor_mgmt_select.select",
                                "bettor_pair_engine.pair_view"]
    out["residuals"] = residuals

    # THE CHECK THAT THE SPLIT DID NOT LOSE ANYTHING. Matched twice plus
    # each residual must equal the legs we started with. Without this a
    # routing bug would silently drop inventory from management, which is
    # the exact failure this module exists to prevent.
    reconstructed_y = matched_n + res_y
    reconstructed_n = matched_n + res_n
    out["split_reconciles"] = (abs(reconstructed_y - yq) < 1e-9
                               and abs(reconstructed_n - nq) < 1e-9)
    out["split_check"] = {
        "matched_plus_residual_yes": reconstructed_y, "yes_qty": yq,
        "matched_plus_residual_no": reconstructed_n, "no_qty": nq,
        "why": ("if this is false the router has dropped inventory and "
                "some part of the position is going unmanaged"),
    }
    out["under_management"] = {
        "matched_qty": matched,
        "residual_legs": [r["leg"] for r in residuals],
        "nothing_is_exempt": ("holding both legs does NOT remove the "
                              "residual from management. 100 YES and 40 "
                              "NO is 40 locked pairs PLUS a 60-lot "
                              "directional position that still needs an "
                              "exit decision every cycle"),
    }
    out["engines_used"] = sorted(set(out["engines_used"]))
    return out
