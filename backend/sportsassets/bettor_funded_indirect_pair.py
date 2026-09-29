"""WHAT AN INDIRECT PAIR IS WORTH NET, AND WHICH ACTION TO RANK FIRST.

── A CORRECTION, RECORDED BECAUSE IT IS THE REASON THIS FILE IS SHORT ──

The first version of this module built its own outcome partition, its own per-leg
payout function and its own worst case. `bettor_indirect_structures` already does
all three, and does them properly: it enumerates the fixture's outcome regions
over MARGIN, TOTAL or the three-way categories, handles spreads and totals rather
than only moneylines, models regulation ties, pushes on integer lines, voids and
postponements as explicit states, refuses when a leg's overtime rule or
orientation is unstated, and reads the taxonomy off the joint payoff table instead
of matching names. It carries `min_payout_cents`, `cost_cents`,
`guaranteed_gross_result_cents`, `undetermined_regions` and `missing_facts`.

Writing a second, weaker model of the same thing is the defect this session spent
its morning removing from `bettor_funded_book`, where a duplicated block meant
edits landed on the copy that never ran. So the duplicate is gone and this module
CONSUMES that classifier.

── WHAT IS GENUINELY MISSING, AND IS WHAT REMAINS HERE ──────────────

`bettor_indirect_structures` says so itself, and `bettor_pair_engine` says it
again: a locked GROSS structure is not a profitable position. Three things stand
between the taxonomy and a decision, and none of them is a classification:

  1 FEES, WHICH DECIDE THE SIGN AT THESE MARGINS. A structure with a ten-cent
    gross surplus is loss-making at a twenty-five-cent round-trip fee. The
    structures module reports `locks_gross_surplus` and is careful to call it
    gross; turning it into a net worst case is arithmetic it deliberately leaves
    out. A verdict is WITHHELD until the fee is priced, because an unpriced fee is
    not a zero fee.

  2 INCREMENTAL CAPITAL. The primary leg is already funded. Comparing the pair's
    total cost against headroom that has already absorbed the first leg
    double-counts it; only the new money is a decision.

  3 DEPTH, AND THE PARTIAL FILL. A hedge sized against a depth never read fills
    partly and leaves the primary leg naked for the remainder while the
    accounting records a complete pair. An unknown depth is refused, not treated
    as unlimited, and the same quantity is flagged as the one book the exit path
    reads -- shared depth must not be counted as separately executable twice.

And then the ranking: HOLD, ACQUIRE_HEDGE, REDUCE and EXIT compared on WORST CASE,
which depends on no probability at all. An action whose inputs are not established
is listed as unrankable with its refusal -- never scored at zero and never dropped,
because a missing input that vanishes from a ranking becomes a decision made by
omission.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER, and ACQUIRE_HEDGE ranking first is a
recommendation, never an authorisation.
"""

from __future__ import annotations

VERSION = "FUNDED_INDIRECT_PAIR_V2"

NOT_ESTABLISHED = "NOT_ESTABLISHED"

ACTION_HOLD = "HOLD"
ACTION_ACQUIRE_HEDGE = "ACQUIRE_HEDGE"
ACTION_REDUCE = "REDUCE"
ACTION_EXIT = "EXIT"
ACTIONS = (ACTION_HOLD, ACTION_ACQUIRE_HEDGE, ACTION_REDUCE, ACTION_EXIT)

R_STRUCTURE_IS_UNESTABLISHABLE = "THE_STRUCTURE_ITSELF_IS_UNESTABLISHABLE"
R_MIN_PAYOUT_NOT_DETERMINED = "THE_MINIMUM_PAYOUT_IS_NOT_DETERMINED"
R_COST_NOT_STATED = "THE_STRUCTURES_COST_IS_NOT_STATED"
R_FEES_NOT_PRICED = "THE_FEE_SCHEDULE_WOULD_NOT_PRICE_THIS"
R_DEPTH_NOT_ESTABLISHED = "THE_BOOKS_DEPTH_AT_THAT_PRICE_IS_NOT_ESTABLISHED"
R_PRICE_NOT_ESTABLISHED = "THE_COMPLEMENTS_PRICE_IS_NOT_ESTABLISHED"

#: ── THE SCALE EVERY DOLLAR FIGURE IN THIS MODULE IS AT ──────────────
#:
#: NAMED, because an unnamed scale was a real defect here. The classifier's
#: `min_payout_cents` and `cost_cents` are PER UNIT; a fee is for the whole
#: acquisition. Reporting one against the other produced a figure at neither
#: scale, and it survived because every unit test in this module's suite holds
#: exactly one contract, where the two agree.
SCALE_WHOLE_POSITION = "WHOLE_POSITION"
SCALE_PER_UNIT = "PER_UNIT"


def describe() -> dict:
    return {
        "version": VERSION,
        "classification_comes_from": "sportsassets.bettor_indirect_structures",
        "why_not_here": (
            "that module already enumerates the fixture's outcome regions, "
            "handles spreads, totals, ties, pushes, voids and postponements, and "
            "refuses on unstated overtime or orientation. A second model of the "
            "same thing would drift from it"),
        "what_this_adds": [
            "the fee, which decides the sign at these margins",
            "incremental capital -- the new money only",
            "depth, and the refusal to treat a partial fill as the pair",
            "the ranking of HOLD / ACQUIRE_HEDGE / REDUCE / EXIT on worst case",
        ],
        "actions": list(ACTIONS),
        "leads_with": (
            "the NET worst case: the structure's minimum payout minus its cost "
            "minus fees. It depends on no probability at all"),
        "gross_is_not_net": (
            "`locks_gross_surplus` is a structural fact before fees, and this "
            "module never reports it as an outcome"),
        "acquire_hedge_is": (
            "a RECOMMENDATION. It is not an authorisation and this module cannot "
            "grant one"),
        "this_module_sends_nothing": True,
    }


def net_worst_case(structure, *, fee_usd=None, fee_basis: str | None = None
                   ) -> dict:
    """THE NET WORST CASE, from a `bettor_indirect_structures.Structure`.

    `structure` is that module's result -- its dataclass or its `to_dict()`. The
    minimum payout and the cost are ITS arithmetic over the fixture's own outcome
    regions, in cents; this function subtracts the fee and reports the result in
    dollars with everything it relied on named.

    THE REFUSALS COME FROM THE STRUCTURE FIRST. A structure with missing facts, an
    undetermined region or no determined minimum has no worst case to net, and a
    number produced anyway would be a fabricated hedge with a decimal point.
    """
    d = structure if isinstance(structure, dict) else structure.to_dict()
    out: dict = {"version": VERSION,
                 "taxonomy": d.get("taxonomy"),
                 "units": d.get("units"),
                 "legs": list(d.get("legs") or ()),
                 "classified_by": "bettor_indirect_structures"}
    missing = list(d.get("missing_facts") or ())
    undetermined = list(d.get("undetermined_regions") or ())
    # ── THE TAXONOMY IS CHECKED FIRST, AND THIS WAS A REAL DEFECT ────
    #
    # An earlier version refused only on `missing_facts` and on a NULL
    # `min_payout_cents`. Measured against the real classifier, an UNESTABLISHABLE
    # structure can have NEITHER: a spread whose void rule was never captured
    # gives `missing_facts=()`, `undetermined_regions=('fixture cancelled or
    # abandoned',)` and `min_payout_cents=100` -- a minimum over the DETERMINED
    # regions only. This module then reported `worst_case_usd = +0.01` and
    # `cannot_lose = True` for a structure the classifier had explicitly refused.
    #
    # That is exactly the fabricated hedge with a decimal point on it. The floor
    # would have been computed from a partition with a reachable cell nobody can
    # price, and the one thing a worst case must not do is omit an outcome.
    if str(d.get("taxonomy")) == "UNESTABLISHABLE" or missing or undetermined:
        return dict(out, ok=False, refusal=R_STRUCTURE_IS_UNESTABLISHABLE,
                    missing_facts=missing,
                    undetermined_regions=undetermined,
                    why=("the classifier did not establish this structure, so "
                         "there is no minimum payout to net a fee against. A "
                         "minimum taken over only the DETERMINED regions is not "
                         "a floor: it omits an outcome that can actually happen"))
    if d.get("min_payout_cents") is None:
        return dict(out, ok=False, refusal=R_MIN_PAYOUT_NOT_DETERMINED,
                    undetermined_regions=undetermined,
                    why=("the minimum payout is not determined. An undetermined "
                         "cell must not contribute zero to a minimum, which "
                         "would report a floor the position does not have"))
    if d.get("cost_cents") is None:
        return dict(out, ok=False, refusal=R_COST_NOT_STATED,
                    why="without the cost there is no result to report")

    # ── UNITS. THE SCALE WAS THE DEFECT, AND IT WAS MEASURED ─────────
    #
    # `bettor_indirect_structures` reports `min_payout_cents` and `cost_cents`
    # PER UNIT -- `cost_cents` is literally `leg_a.cost_cents_per_unit +
    # leg_b.cost_cents_per_unit`. This function divided by 100 and stopped, so a
    # ten-contract middle reported a gross worst case of -$0.03 where the
    # position's was -$0.30. Every existing test in this module's suite holds ONE
    # unit, which is exactly why the mismatch survived: at units=1 the two scales
    # agree, and every assertion still passes after this repair.
    #
    # THE FEE WAS ALREADY WHOLE-POSITION, which is what made the result
    # incoherent rather than merely small: a per-unit payout minus a
    # whole-position fee is a quantity at neither scale. Both halves are now at
    # the POSITION scale and the scale is NAMED in the output, because a number
    # whose scale is not stated is how this got through.
    units = int(d.get("units") or 0)
    per_unit_min = round(int(d["min_payout_cents"]) / 100.0, 6)
    per_unit_cost = round(int(d["cost_cents"]) / 100.0, 6)
    min_payout_usd = round(per_unit_min * units, 6)
    cost_usd = round(per_unit_cost * units, 6)
    gross = round(min_payout_usd - cost_usd, 6)
    res = dict(out, ok=True, refusal=None,
               scale=SCALE_WHOLE_POSITION,
               units_valued=units,
               min_payout_usd_per_unit=per_unit_min,
               cost_usd_per_unit=per_unit_cost,
               min_payout_usd=min_payout_usd,
               cost_usd=cost_usd,
               gross_worst_case_usd=gross,
               scale_note=("every dollar figure here is for the WHOLE matched "
                           "position of %d unit(s). The classifier's cents are "
                           "per unit; the fee is for the whole acquisition"
                           % units),
               locks_gross_surplus=d.get("locks_gross_surplus"),
               both_lose_regions=list(d.get("both_lose_regions") or ()),
               unresolved_states=list(d.get("unresolved_states") or ()),
               fee_basis=fee_basis or NOT_ESTABLISHED)
    # UNRESOLVED STATES ARE CARRIED, NOT SWALLOWED. A void or a postponement is
    # not a payout, and the structures module lists them precisely so a consumer
    # cannot quietly treat "conditional on resolution" as unconditional.
    res["worst_case_is_conditional_on_resolution"] = bool(
        res["unresolved_states"])
    if fee_usd is None:
        return dict(res, fees_usd=None, worst_case_usd=None,
                    verdict_is_withheld=R_FEES_NOT_PRICED,
                    why=("the net worst case is withheld until the fee is "
                         "priced. A structure with a ten-cent gross surplus is "
                         "loss-making at a twenty-five-cent round trip, so the "
                         "fee is what decides the sign -- and an unpriced fee is "
                         "not a zero fee"))
    fees = round(float(fee_usd), 6)
    net = round(gross - fees, 6)
    return dict(res, fees_usd=fees, worst_case_usd=net,
                cannot_lose=bool(net > 0 and not res["unresolved_states"]),
                why_the_worst_case_is_the_headline=(
                    "it depends on no probability at all -- only on the "
                    "fixture's own outcome regions, what was paid and the fee"))


def incremental_capital_usd(*, hedge_qty, hedge_price, hedge_fee_usd=None
                            ) -> dict:
    """WHAT ACQUIRING THE HEDGE WOULD COST, which is not the pair's total. The
    primary leg is already paid for; comparing the total against headroom that has
    already absorbed it double-counts the first leg."""
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
    primary leg naked for the unfilled part while the accounting believes the pair
    is complete -- which is the specific way this structure fails. An unknown
    depth is refused; a partial reports its shortfall.
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


# ═════════════════════════════════════════════════════════════════════
# FOUR QUANTITIES, AND THE UNCOVERED REMAINDER IS WORTH SOMETHING
# ═════════════════════════════════════════════════════════════════════
#
# THE OWNER'S ARITHMETIC CONTROL, which this exists to reproduce:
#
#     10 held at $0.55, 6 opposing at $0.30. In the ordinary outcome
#
#         6 - (10 x 0.55) - (6 x 0.30) = -$1.30
#
#     while the matched six pairs alone show +$0.90 and the four uncovered
#     contracts lose $2.20.
#
# `net_worst_case` values the MATCHED SLICE. `classify` sets `units` to
# min(held, hedge) and builds the payout table on one unit of each, so the number
# that comes back is +$0.90 -- correct for what it measures, and NOT the
# position's result. The four uncovered contracts are unhedged directional
# inventory that nothing in that figure values.
#
# `rank_admitted` then multiplied that +$0.90 by `supportable / wanted`, which is
# the DOUBLE PRORATION: `classify` had already scaled to the matched units, so
# scaling again by 6/10 reports $0.54 for a slice worth $0.90 -- and still says
# nothing about the four.
#
# So the three are separated and all three are reported. `matched_slice` is what
# the structure establishes; `uncovered` is the remainder at its own basis;
# `whole_position` is their sum, and is the only one of the three that answers
# "what happens to this account".
#
# FOUR QUANTITIES, NEVER ONE. Each has a different source and they diverge in the
# ordinary case, which is why a single `qty` was wrong:
#
#     REQUESTED    what the position wants hedged -- its residual quantity
#     SUPPORTABLE  what the book's DISPLAYED depth would cover
#     PROPOSED     what the order actually asks for
#     FILLED       what came back, knowable only after the fact
#
# Displayed depth is not a queue position, so SUPPORTABLE bounds PROPOSED and
# PROPOSED bounds nothing at all.

QTY_REQUESTED = "REQUESTED_BY_THE_POSITION"
QTY_SUPPORTABLE = "SUPPORTABLE_BY_DISPLAYED_DEPTH"
QTY_PROPOSED = "PROPOSED_ON_THE_ORDER"
QTY_FILLED = "ACTUALLY_FILLED"

R_UNCOVERED_BASIS_NOT_STATED = (
    "THE_UNCOVERED_INVENTORYS_OWN_BASIS_IS_NOT_STATED")


def quantities(*, requested, supportable=None, proposed=None, filled=None
               ) -> dict:
    """The four quantities, kept apart, with the relations that must hold.

    Returns them named, plus `uncovered_qty` -- requested minus whichever later
    quantity is known. `filled` decides it once it exists, because what is
    covered is what actually filled, not what was proposed.
    """
    req = None if requested is None else float(requested)
    sup = None if supportable is None else float(supportable)
    pro = None if proposed is None else float(proposed)
    fil = None if filled is None else float(filled)
    # WHAT IS COVERED IS WHAT FILLED. Before a fill the best available estimate
    # is the proposal, then the supportable quantity -- and WHICH ONE was used
    # is recorded, because "6 will fill" and "6 did fill" are different
    # statements and only one of them is a fact.
    covered, basis_of_covered = None, None
    for value, label in ((fil, QTY_FILLED), (pro, QTY_PROPOSED),
                         (sup, QTY_SUPPORTABLE)):
        if value is not None:
            covered, basis_of_covered = value, label
            break
    out = {"version": VERSION,
           "requested_qty": req, "supportable_qty": sup,
           "proposed_qty": pro, "filled_qty": fil,
           "covered_qty": covered,
           "covered_qty_is": basis_of_covered,
           "uncovered_qty": (None if (req is None or covered is None)
                             else round(max(0.0, req - covered), 6)),
           "why_four": ("requested, supportable, proposed and filled diverge in "
                        "the ordinary case. Displayed depth is not a queue "
                        "position, so supportable bounds proposed and proposed "
                        "bounds nothing")}
    problems = []
    if req is not None and sup is not None and sup > req + 1e-9:
        problems.append("supportable %s exceeds requested %s" % (sup, req))
    if sup is not None and pro is not None and pro > sup + 1e-9:
        problems.append("proposed %s exceeds the supportable %s" % (pro, sup))
    if pro is not None and fil is not None and fil > pro + 1e-9:
        problems.append("filled %s exceeds the proposed %s" % (fil, pro))
    out["inconsistent"] = problems
    return out


R_POSITION_HAS_AN_UNDETERMINED_REGION = (
    "SOME_OUTCOME_REGION_OF_THE_WHOLE_POSITION_HAS_NO_DETERMINED_PAYOUT")


def position_worst_case(*, held_leg, hedge_leg, hedge_qty,
                        sport_permits_tie, fee_usd=None, fee_basis=None,
                        fixture_can_void=True, fixture_can_postpone=True
                        ) -> dict:
    """The WHOLE POSITION's floor, over ONE joint partition of the fixture.

    `held_leg` carries the inventory at its real quantity; `hedge_qty` is how
    many contracts of `hedge_leg` would actually be acquired -- the PROPOSED or
    FILLED quantity, never the requested one.

    WHY THIS IS NOT "THE MATCHED FLOOR PLUS THE UNCOVERED FLOOR", which is what
    I wrote first and which is wrong. Those two minima occur in DIFFERENT
    REGIONS. On the owner's control -- 10 held at $0.55, 6 opposing at $0.30,
    cancellation refunding basis -- the matched slice's worst region is the VOID
    (both legs refunded, exactly breakeven, $0.00) while the uncovered
    inventory's worst region is the one where the held side loses (-$2.20).
    Summing them gives -$2.20, an outcome that cannot occur: in the void the
    uncovered contracts are refunded too.

    Built as one table over the real quantities, the same control gives

        margin < 0 (the hedge wins) ...  $6.00 - $7.30 = -$1.30   <- the floor
        margin > 0 (the held wins) ....  $10.00 - $7.30 = +$2.70
        cancelled (both refunded) .....  $7.30 - $7.30 =  $0.00

    which is the owner's -$1.30, derived rather than asserted. A floor is the
    minimum over single joint outcomes, and that is the only thing it can be.

    NO SECOND PRORATION either: the table is built at the real quantities, so
    there is nothing left to scale. `rank_admitted` used to multiply an
    already-whole-position figure by covered/requested.
    """
    from . import bettor_indirect_structures as IS

    out = {"version": VERSION, "ok": False, "refusal": None,
           "scale": SCALE_WHOLE_POSITION,
           "a_floor_is_a_minimum_over_joint_outcomes": (
               "not the sum of two separately minimised pieces -- those minima "
               "can sit in different regions and their sum can be an outcome "
               "that cannot happen"),
           "no_second_proration": (
               "the table is built at the real quantities, so there is nothing "
               "left to scale by covered/requested")}
    hq = float(hedge_qty or 0.0)
    held_q = float(getattr(held_leg, "quantity", 0) or 0.0)
    out.update(held_qty=held_q, covered_qty=min(hq, held_q),
               hedge_qty=hq,
               uncovered_qty=round(max(0.0, held_q - hq), 6))
    if held_leg is None or hedge_leg is None:
        return dict(out, refusal=R_STRUCTURE_IS_UNESTABLISHABLE,
                    why="both legs are needed to partition one outcome space")
    gaps = list(held_leg.missing_facts()) + list(hedge_leg.missing_facts())
    # THE SAME REFUSAL `classify` MAKES: an integer total line's push is not
    # isolated by the partition this table is built on, so the floor would be
    # a minimum over cells that misgrade the push.
    gaps += IS.integer_total_push_gaps((held_leg, hedge_leg))
    if gaps:
        return dict(out, refusal=R_STRUCTURE_IS_UNESTABLISHABLE,
                    missing_facts=gaps,
                    why=("a leg missing a fact grading needs has no payout "
                         "function: %s" % "; ".join(gaps[:3])))
    if held_leg.cost_cents_per_unit is None or \
            hedge_leg.cost_cents_per_unit is None:
        return dict(out, refusal=R_COST_NOT_STATED,
                    why="without both bases there is no result to report")
    if fee_usd is None:
        return dict(out, refusal=R_FEES_NOT_PRICED,
                    why=("a fee-adjusted floor computed without a fee is not "
                         "fee-adjusted"))

    # THE REAL QUANTITIES, in one table. `_with_quantity` preserves the
    # settlement reading, which is why it uses dataclasses.replace.
    legs = (held_leg, IS._with_quantity(hedge_leg, int(hq)))
    table = IS.payoff_table(legs, sport_permits_tie=sport_permits_tie,
                            fixture_can_void=fixture_can_void,
                            fixture_can_postpone=fixture_can_postpone)
    cost_cents = (int(held_leg.cost_cents_per_unit) * int(held_q)
                  + int(hedge_leg.cost_cents_per_unit) * int(hq))
    cost_usd = round(cost_cents / 100.0, 6)
    out.update(cost_usd=cost_usd,
               held_basis_usd_per_unit=round(
                   int(held_leg.cost_cents_per_unit) / 100.0, 6),
               hedge_basis_usd_per_unit=round(
                   int(hedge_leg.cost_cents_per_unit) / 100.0, 6))

    rows, undetermined = [], []
    for r in table:
        # A POSTPONEMENT IS NOT A PAYOUT. The market stays open, so the cell
        # has no money rather than an unknown amount, and it is reported apart
        # instead of making every position undeterminable.
        if r["state"] == IS.STATE_POSTPONED:
            continue
        if not r["determined"]:
            undetermined.append(r["region"])
            continue
        gross = round(r["joint_cents"] / 100.0 - cost_usd, 6)
        rows.append({"region": r["region"], "state": r["state"],
                     "payout_usd": round(r["joint_cents"] / 100.0, 6),
                     "net_usd": gross,
                     "per_leg_cents": r["per_leg_cents"]})
    out["regions"] = rows
    out["unresolved_states"] = [r["region"] for r in table
                               if r["state"] == IS.STATE_POSTPONED]
    if undetermined:
        return dict(out, refusal=R_POSITION_HAS_AN_UNDETERMINED_REGION,
                    undetermined_regions=undetermined,
                    why=("%d region(s) of the whole position have no "
                         "determined payout, and a minimum taken over the rest "
                         "omits an outcome that can actually happen: %s"
                         % (len(undetermined), undetermined[:3])))
    if not rows:
        return dict(out, refusal=R_MIN_PAYOUT_NOT_DETERMINED,
                    why="no region of the position has a determined payout")

    binding = min(rows, key=lambda r: r["net_usd"])
    gross_floor = binding["net_usd"]
    net_floor = round(gross_floor - float(fee_usd), 6)
    # ── THE DECOMPOSITION, INSIDE THE BINDING REGION ─────────────────
    #
    # Reported so the number can be read, not so the number can be built: both
    # halves are taken from the SAME region as the floor, which is what makes
    # them add up to it.
    per_leg = binding["per_leg_cents"]
    held_pays = round((per_leg[0] or 0) / 100.0, 6)
    hedge_pays = round((per_leg[1] or 0) / 100.0, 6)
    cov = min(hq, held_q)
    unc = round(max(0.0, held_q - hq), 6)
    held_basis = out["held_basis_usd_per_unit"]
    hedge_basis = out["hedge_basis_usd_per_unit"]
    matched_usd = round(cov * (held_pays - held_basis)
                        + min(hq, held_q) * (hedge_pays - hedge_basis), 6)
    extra_hedge = round(max(0.0, hq - held_q), 6)
    uncovered_usd = round(unc * (held_pays - held_basis)
                          + extra_hedge * (hedge_pays - hedge_basis), 6)
    return dict(out, ok=True,
                binding_region=binding["region"],
                binding_state=binding["state"],
                gross_worst_case_usd=gross_floor,
                fees_usd=float(fee_usd), fee_basis=fee_basis,
                worst_case_usd=net_floor,
                whole_position_usd=net_floor,
                matched_slice_usd=matched_usd,
                uncovered_usd=uncovered_usd,
                uncovered_is=("held inventory no hedge covers"
                              if unc else
                              ("hedge contracts beyond the inventory"
                               if extra_hedge else "nothing")),
                fully_covered=unc <= 1e-9 and extra_hedge <= 1e-9,
                cannot_lose=net_floor > 0,
                worst_case_is_conditional_on_resolution=True,
                why=("the position's floor is $%+.4f in %r, gross $%+.4f less "
                     "$%.4f of fees. Inside that region the %s matched "
                     "contract(s) contribute $%+.4f and the %s uncovered "
                     "contribute $%+.4f"
                     % (net_floor, binding["region"], gross_floor,
                        float(fee_usd), cov, matched_usd,
                        unc or extra_hedge, uncovered_usd)))


def downside_only_view(*, held: dict, hedge_candidate: dict | None = None,
                       exit_proceeds_usd=None, evidence: dict | None = None
                       ) -> dict:
    """THE DOWNSIDE ORDERING. **THIS IS NOT THE DECISION POLICY.**

    ── RENAMED, BECAUSE THE OLD NAME WAS THE DEFECT ─────────────────
    This was `rank_actions`, and an independent review was right that a function
    with that name, sorting by worst case, silently stood in for a policy this
    system already has. `bettor_mgmt_select.rank_with_hold` decides on EXPECTED
    value; `bettor_funded_decision.decide` is where an indirect acquisition joins
    that comparison. A holding can have positive expected value while its worst
    case is losing the stake, so ordering by worst case makes immediate
    liquidation win for the wrong reason -- the mirror image of the failure
    `bettor_mgmt_select` names as liquidating the book for want of a settlement
    model.

    WHAT IT IS FOR. Reading the downside of each action side by side, and
    supplying `downside_usd` to `bettor_funded_decision.decide`, which applies it
    as a CONSTRAINT. No probability enters it, which is exactly why it cannot be
    the ranking.

    `held` and `hedge_candidate["paired_structure"]` are `net_worst_case` results.
    """
    out: dict = {"version": VERSION, "actions": [], "unrankable": [],
                 "evidence": dict(evidence or {})}
    if not held.get("ok"):
        return dict(out, ok=False, refusal=held.get("refusal"),
                    why=held.get("why"))
    out["held_taxonomy"] = held.get("taxonomy")
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
            "taxonomy": held.get("taxonomy"),
            "both_lose_regions": held.get("both_lose_regions"),
            "unresolved_states": held.get("unresolved_states"),
            "incremental_capital_usd": 0.0,
            "why": "keep exactly what is held; no new money, no new depth used"})

    # ── EXIT ─────────────────────────────────────────────────────────
    if exit_proceeds_usd is None:
        _cannot(ACTION_EXIT, R_PRICE_NOT_ESTABLISHED,
                ("an exit is scored on proceeds actually available now. Without "
                 "a read price there is no number, and using the entry price "
                 "would score a sale at what we paid"))
    else:
        _add(ACTION_EXIT,
             round(float(exit_proceeds_usd) - (held.get("cost_usd") or 0.0)
                   - (held.get("fees_usd") or 0.0), 6), {
            "proceeds_usd": round(float(exit_proceeds_usd), 6),
            "incremental_capital_usd": 0.0,
            "releases_the_capacity_slot": True,
            "why": ("sell what is held. This is the only action that needs no "
                    "forecast and no further capital")})

    # ── REDUCE ───────────────────────────────────────────────────────
    if exit_proceeds_usd is None:
        _cannot(ACTION_REDUCE, R_PRICE_NOT_ESTABLISHED,
                "a partial sale is scored on the same read price an exit needs")
    else:
        _add(ACTION_REDUCE, None, {
            "incremental_capital_usd": 0.0,
            "worst_case_is_between": [
                round(float(exit_proceeds_usd) - (held.get("cost_usd") or 0.0)
                      - (held.get("fees_usd") or 0.0), 6),
                held.get("worst_case_usd")],
            "why": ("a partial sale lies between EXIT and HOLD by construction, "
                    "so it is ranked only when a quantity is chosen -- and "
                    "choosing it is a sizing decision this function does not "
                    "make")})

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
                    paired.get("why"),
                    {"missing_facts": paired.get("missing_facts")})
        elif paired.get("worst_case_usd") is None:
            _cannot(ACTION_ACQUIRE_HEDGE,
                    paired.get("verdict_is_withheld", R_FEES_NOT_PRICED),
                    paired.get("why"))
        elif not depth.get("ok"):
            _cannot(ACTION_ACQUIRE_HEDGE,
                    depth.get("refusal", R_DEPTH_NOT_ESTABLISHED),
                    depth.get("why"))
        elif not depth.get("fully_supported"):
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
                "taxonomy": paired.get("taxonomy"),
                "both_lose_regions": paired.get("both_lose_regions"),
                "unresolved_states": paired.get("unresolved_states"),
                "cannot_lose": paired.get("cannot_lose"),
                "incremental_capital_usd": incr["incremental_capital_usd"],
                "depth": {k: depth.get(k) for k in
                          ("wanted_qty", "available_qty", "fully_supported")},
                "why": ("buy the complement. This is the only action that spends "
                        "NEW money, so its worst case must clear HOLD's by more "
                        "than the capital it consumes is worth elsewhere -- a "
                        "judgement this function reports the inputs for and does "
                        "not make")})

    ranked = [a for a in out["actions"] if a["worst_case_usd"] is not None]
    ranked.sort(key=lambda a: (-a["worst_case_usd"],
                               a.get("incremental_capital_usd") or 0.0))
    out["ranked"] = ranked
    out["best"] = ranked[0] if ranked else None
    out["ok"] = True
    out["refusal"] = None
    out["ordering_rule"] = (
        "highest NET worst case first; ties broken by LESS new capital. No "
        "probability enters it, which is why this is a downside VIEW and not a "
        "decision")
    out["this_is_not_the_decision_policy"] = (
        "the policy is EXPECTED net value -- see bettor_funded_decision.decide. "
        "Selecting on worst case would make liquidation win for the wrong "
        "reason")
    out["what_this_is_not"] = (
        "a decision, and not an authorisation. ACQUIRE_HEDGE appearing first here "
        "means only that its worst case is the least bad on these inputs")
    if out["unrankable"]:
        out["unrankable_are_not_zero"] = (
            "an action whose inputs are not established is listed here rather "
            "than scored at zero or dropped. A missing input that disappears "
            "from a ranking becomes a decision made by omission")
    return out
