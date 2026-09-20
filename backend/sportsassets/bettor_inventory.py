"""PER-LEG INVENTORY IN PRODUCTION. WHAT WE ACTUALLY OWN, ON BOTH LEGS.

Owner directive, "CONTINUE THE BUILD" §3:

    "Pairing, residual management, complement acquisition, merge, hedge
    tax, exit optimization and capital allocation all require the system
    to know what it actually owns on BOTH legs... Never net YES and NO
    into one position. Never let complement acquisition look like a
    direct sell merely because the venue's implementation nets
    economically."

THE ARITHMETIC IS NOT HERE. `research/beta48/shadow/inventory_state.py`
already computes MATCHED_QTY, PAIR_BASIS, LOCKED_PNL,
CAPITAL_OCCUPIED_BY_PAIR and CAPITAL_OCCUPIED_BY_RESIDUAL in exact
decimal, and refuses to net the legs. It is imported, never copied.
This module does the two things that module cannot:

    1. build the per-leg state FROM THE PRODUCTION LEDGER, and
    2. refuse to claim a pair the identity layer has not confirmed.

WHY A PAIR IS A CLAIM AND NOT AN ARITHMETIC FACT. MATCHED_QTY =
min(YES_QTY, NO_QTY) is only economically true if the two legs really
are complements of ONE condition -- if exactly one of them pays $1.
`bettor_identity_bindings` already carries that verdict, and it is not
always YES: EXACT_ONE_TO_COMPLEMENT_BASKET is confirmed, while
STRUCTURALLY_IDENTIFIED_COMPLEMENT_PENDING_INSTITUTIONAL_CONFIRMATION
is a structural guess awaiting the venue. Computing a locked P&L across
an unconfirmed pair would book profit on an assumption, so when the
binding is not confirmed the legs are held SEPARATELY and the pair view
is PAIR_UNCONFIRMED rather than a number.

    A MATCHED PAIR AND A FLAT BOOK BOTH NET TO ZERO. 100 YES with 100
    NO carries locked P&L, occupies capital and needs managing; 0 and 0
    occupies nothing. An engine that nets reports them identically and
    loses all three facts. That is why nothing here ever sums the legs.

RESIDUAL BASIS IS AVERAGE COST, AND SAYS SO. §3 asks for
RESIDUAL_YES_BASIS and RESIDUAL_NO_BASIS. Under the average-cost
convention `inventory_state` already uses for YES_AVG_COST, the residual
carries that same average -- matching does not consume specific lots.
The alternative (FIFO) would give a different number, so the convention
is named on every row rather than left for a reader to assume.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER. It reads the ledger.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_ev_bridge as evb

NOT_IDENTIFIED = "NOT_IDENTIFIED"

LEG_YES = "YES"
LEG_NO = "NO"
LEGS = (LEG_YES, LEG_NO)

# ── the pair claim, which identity owns and inventory only reads ─────

PAIR_CONFIRMED = "PAIR_CONFIRMED"
PAIR_UNCONFIRMED = "PAIR_UNCONFIRMED"
PAIR_REFUSED = "PAIR_REFUSED"

# The identity verdicts under which min(YES, NO) is an economically
# real pair. Listed positively: a verdict that is not here does not
# become a pair by resembling one.
CONFIRMED_COMPLEMENT_STATUSES = (
    "EXACT_SAME_CONTRACT",
    "EXACT_ONE_TO_COMPLEMENT_BASKET",
)

UNCONFIRMED_COMPLEMENT_STATUSES = (
    "STRUCTURALLY_IDENTIFIED_COMPLEMENT_PENDING_INSTITUTIONAL_CONFIRMATION",
    "AMBIGUOUS",
    "NOT_IDENTIFIED",
)

REFUSED_COMPLEMENT_STATUSES = ("DIFFERENT_CONTRACT",)

BASIS_CONVENTION = "AVERAGE_COST"

BASIS_CONVENTION_RULE = (
    "the residual carries the LEG'S AVERAGE COST, because matching does "
    "not consume specific lots under average-cost accounting. FIFO "
    "would give a different residual basis and a different realised "
    "P&L on the matched portion, so the convention is stated on every "
    "row rather than inferred from the number")

DO_NOT_NET = (
    "YES and NO are tracked separately and the pair view is DERIVED. A "
    "matched pair and a flat book both net to zero while one carries "
    "locked P&L, occupies capital and needs managing. Netting reports "
    "them identically and loses all three facts")

COMPLEMENT_IS_NOT_A_SELL = (
    "acquiring NO while holding YES is a NO ACQUISITION, recorded on "
    "the NO leg. It is never recorded as a reduction of the YES leg, "
    "however the venue happens to net it economically: the two have "
    "different fills, different fees, different exit options and "
    "different residual risk, and a ledger that conflates them cannot "
    "tell a hedge from a sale afterwards")


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _norm_leg(leg):
    """YES/NO, or None. An unrecognised leg is never bucketed."""
    if leg is None:
        return None
    t = str(leg).strip().upper()
    return t if t in LEGS else None


# ── step 1: the ledger rows -> a per-leg state ───────────────────────

def legs_from_rows(rows) -> dict:
    """Aggregate per-leg fills into quantity and average cost per leg.

    Rows are the production position/fill rows. Each carries ONE leg
    and is counted on that leg only. A row whose leg is unrecognised is
    REFUSED into `unrecognisedLegs` rather than guessed into YES.
    """
    acc = {LEG_YES: {"qty": Decimal("0"), "cost": Decimal("0"), "n": 0},
           LEG_NO: {"qty": Decimal("0"), "cost": Decimal("0"), "n": 0}}
    unrecognised, unpriced = [], []

    for r in rows or ():
        leg = _norm_leg(r.get("leg") or r.get("side") or r.get("outcome_leg"))
        if leg is None:
            unrecognised.append(r.get("position_id") or r.get("id"))
            continue
        qty = _d(r.get("qty") if r.get("qty") is not None
                 else r.get("entry_qty"))
        price = _d(r.get("price") if r.get("price") is not None
                   else r.get("entry_vwap"))
        if qty is None or price is None:
            # A fill without a quantity or a price cannot enter an
            # average cost. It is named, not silently dropped and not
            # counted at zero.
            unpriced.append(r.get("position_id") or r.get("id"))
            continue
        acc[leg]["qty"] += qty
        acc[leg]["cost"] += qty * price
        acc[leg]["n"] += 1

    out = {"unrecognisedLegs": unrecognised, "unpricedRows": unpriced,
           "complementIsNotASell": COMPLEMENT_IS_NOT_A_SELL}
    for leg in LEGS:
        q, c = acc[leg]["qty"], acc[leg]["cost"]
        out["%s_QTY" % leg] = str(q)
        out["%s_AVG_BASIS" % leg] = (str(c / q) if q > 0 else NOT_IDENTIFIED)
        out["%s_FILL_COUNT" % leg] = acc[leg]["n"]
    return out


# ── step 2: the derived view, computed by the research module ────────

def pair_status(identity_status) -> dict:
    """May min(YES, NO) be read as an economically real pair?"""
    s = identity_status or NOT_IDENTIFIED
    if s in CONFIRMED_COMPLEMENT_STATUSES:
        return {"pairStatus": PAIR_CONFIRMED, "identityStatus": s,
                "why": "the identity layer confirms the two legs are "
                       "complements of one condition"}
    if s in REFUSED_COMPLEMENT_STATUSES:
        return {"pairStatus": PAIR_REFUSED, "identityStatus": s,
                "why": ("the identity layer says these are DIFFERENT "
                        "contracts, so holding both is two directional "
                        "positions and not a pair at all")}
    return {"pairStatus": PAIR_UNCONFIRMED, "identityStatus": s,
            "why": ("the complement is not confirmed, so a matched "
                    "quantity would book locked P&L on an assumption. "
                    "The legs are held separately until the venue "
                    "confirms")}


def inventory(rows, *, identity_status=None, time_in_inventory_s=None,
              root=None) -> dict:
    """The per-leg inventory state for ONE condition.

    Delegates every figure the research module already computes and
    adds only what production needs on top: the residual bases under a
    named convention, and the pair claim.
    """
    legs = legs_from_rows(rows)
    claim = pair_status(identity_status)

    state = {
        "YES_QTY": legs["YES_QTY"], "YES_AVG_BASIS": legs["YES_AVG_BASIS"],
        "NO_QTY": legs["NO_QTY"], "NO_AVG_BASIS": legs["NO_AVG_BASIS"],
        "basisConvention": BASIS_CONVENTION,
        "basisConventionRule": BASIS_CONVENTION_RULE,
        "doNotNet": DO_NOT_NET,
        "fillCounts": {leg: legs["%s_FILL_COUNT" % leg] for leg in LEGS},
        "unrecognisedLegs": legs["unrecognisedLegs"],
        "unpricedRows": legs["unpricedRows"],
        "complementIsNotASell": COMPLEMENT_IS_NOT_A_SELL,
    }
    state.update(claim)

    yq, nq = _d(legs["YES_QTY"]) or Decimal("0"), \
        _d(legs["NO_QTY"]) or Decimal("0")

    # THE PAIR VIEW EXISTS ONLY WHEN THE PAIR DOES. Both legs held and
    # the complement confirmed. Otherwise the quantities stand alone.
    if claim["pairStatus"] != PAIR_CONFIRMED and yq > 0 and nq > 0:
        state.update({
            "MATCHED_QTY": NOT_IDENTIFIED,
            "MATCHED_PAIR_BASIS": NOT_IDENTIFIED,
            "MATCHED_CAPITAL": NOT_IDENTIFIED,
            "LOCKED_PNL": NOT_IDENTIFIED,
            "RESIDUAL_YES_QTY": legs["YES_QTY"],
            "RESIDUAL_YES_BASIS": legs["YES_AVG_BASIS"],
            "RESIDUAL_NO_QTY": legs["NO_QTY"],
            "RESIDUAL_NO_BASIS": legs["NO_AVG_BASIS"],
            "whyNoPairView": (
                "both legs are held but the complement is %s. Until it "
                "is confirmed each leg is carried as its own "
                "directional position and no locked P&L is claimed"
                % claim["identityStatus"]),
        })
        return state

    try:
        INV = evb.machinery(root)["inventory_state"]
    except evb.MachineryUnavailable as exc:
        state.update({"MATCHED_QTY": NOT_IDENTIFIED,
                      "derivedView": evb.MACHINERY_UNAVAILABLE,
                      "why": str(exc)})
        return state

    derived = INV.inventory(
        yes_qty=legs["YES_QTY"],
        yes_avg_cost=(None if legs["YES_AVG_BASIS"] == NOT_IDENTIFIED
                      else legs["YES_AVG_BASIS"]),
        no_qty=legs["NO_QTY"],
        no_avg_cost=(None if legs["NO_AVG_BASIS"] == NOT_IDENTIFIED
                     else legs["NO_AVG_BASIS"]),
        time_in_inventory_s=time_in_inventory_s)

    state.update({
        "MATCHED_QTY": derived["MATCHED_QTY"],
        "MATCHED_PAIR_BASIS": derived["PAIR_BASIS"],
        "MATCHED_CAPITAL": derived["CAPITAL_OCCUPIED_BY_PAIR"],
        "RESIDUAL_CAPITAL": derived["CAPITAL_OCCUPIED_BY_RESIDUAL"],
        "CAPITAL_OCCUPIED": derived["CAPITAL_OCCUPIED"],
        "LOCKED_PNL": derived["LOCKED_PNL"],
        "UNLOCKED_EXPOSURE": derived["UNLOCKED_EXPOSURE"],
        "RESIDUAL_YES_QTY": derived["RESIDUAL_YES"],
        "RESIDUAL_NO_QTY": derived["RESIDUAL_NO"],
        # Not emitted by the research module; supplied here under the
        # convention named above rather than left for a reader to guess.
        "RESIDUAL_YES_BASIS": (legs["YES_AVG_BASIS"]
                               if _d(derived["RESIDUAL_YES"]) else
                               NOT_IDENTIFIED),
        "RESIDUAL_NO_BASIS": (legs["NO_AVG_BASIS"]
                              if _d(derived["RESIDUAL_NO"]) else
                              NOT_IDENTIFIED),
        "IS_GENUINELY_FLAT": derived["IS_GENUINELY_FLAT"],
        "IS_MATCHED_NOT_FLAT": derived["IS_MATCHED_NOT_FLAT"],
        "TIME_IN_INVENTORY": derived["TIME_IN_INVENTORY"],
        "derivedBy": "research/beta48/shadow/inventory_state.py",
    })
    return state


# ── reading the production ledger ────────────────────────────────────
#
# THE SOURCE IS shadow_positions, SCOPED TO THE LANE, and the choice is
# load bearing in two ways.
#
# WHY NOT bettor_experimental_positions. That table belongs to the
# X-series, and §22 is explicit that the experiments' results must not
# be attributed to BETTOR EV. Folding X1's positions into BETTOR's
# inventory would do exactly that -- silently, and in the one place
# every downstream engine reads. Its `side` column also carries the
# ACTION (`BUY`), not an outcome leg, so the two tables do not even
# describe the same thing.
#
# WHY THE LANE IS A PARAMETER AND NOT A DEFAULT. shadow_positions is
# shared with RN1. A query that forgot the predicate would quietly mix
# a frozen copy-trading strategy's inventory into BETTOR's, so the lane
# is required and the store refuses an unknown one.
#
# The condition key is the venue-native market_id and the leg is the
# venue's own outcome leg: no fuzzy title matching, no team-name
# matching, no price matching.

BETTOR_LANE = "BETTOR_EV_SHADOW"

OPEN_LEGS_SQL = """
    SELECT shadow_position_id AS position_id,
           market_id,
           leg,
           entry_qty          AS qty,
           entry_price        AS price,
           entry_time
      FROM shadow_positions
     WHERE lane = $2
       AND market_id = $1
     ORDER BY entry_time
"""

LATEST_BINDING_SQL = """
    SELECT identity_status
      FROM bettor_identity_bindings
     WHERE market_id = $1
     ORDER BY resolved_at DESC
     LIMIT 1
"""


async def load(pool, market_id: str, *, lane=BETTOR_LANE, root=None) -> dict:
    """Per-leg inventory for one market ON ONE LANE, read from the ledger."""
    from . import shadow_lanes as lanes
    if lane not in lanes.LANES:
        raise ValueError(
            "refused: %r is not a lane. Inventory is never read across "
            "lanes -- RN1's frozen strategy and BETTOR's EV lane hold "
            "different books for different reasons" % lane)
    rows = await pool.fetch(OPEN_LEGS_SQL, market_id, lane)
    binding = await pool.fetchrow(LATEST_BINDING_SQL, market_id)
    state = inventory(
        [dict(r) for r in rows],
        identity_status=(binding["identity_status"] if binding else None),
        root=root)
    state["marketId"] = market_id
    state["lane"] = lane
    return state


def describe() -> dict:
    return {
        "purpose": "per-leg inventory, never netted",
        "legs": list(LEGS),
        "pairStatuses": [PAIR_CONFIRMED, PAIR_UNCONFIRMED, PAIR_REFUSED],
        "confirmedComplementStatuses": list(CONFIRMED_COMPLEMENT_STATUSES),
        "basisConvention": BASIS_CONVENTION,
        "doNotNet": DO_NOT_NET,
        "complementIsNotASell": COMPLEMENT_IS_NOT_A_SELL,
        "arithmeticDelegatedTo": (
            "research/beta48/shadow/inventory_state.py, imported not "
            "copied"),
    }
