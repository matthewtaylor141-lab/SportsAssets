"""MANAGEMENT_PAIR_091_STOP_16_V1 — management's explicit exit policy.

MANAGEMENT-DEFINED AND EXPERIMENTAL. Not learned, not fitted, not shown
to be profitable. Every threshold was supplied by management and is
frozen here before any forward evaluation; changing one produces a NEW
policy id and this one's verdict survives the change.

It replaces the provisional adverse-move/time trigger I had written for
this experiment. That rule is withdrawn.

WHAT IT DECIDES, IN TWO INDEPENDENT PARTS.

  1 PAIRING, allowed throughout the event. Rest a complementary buy
    whose limit keeps the COMBINED pair cost at or below $0.91
    INCLUDING known entry and completion fees, so a fully completed $1
    pair nets at least $0.09.

  2 LOSS EXIT, second half only. Sell unpaired inventory when estimated
    net executable proceeds fall to 84% or less of that inventory's
    ALLOCATED acquisition cost, fees included.

════════════════════════════════════════════════════════════════════
THE BLOCKER ON PART 2, ESTABLISHED BY INSPECTION BEFORE IMPLEMENTING.

§3 requires the second half to come from OBSERVED, TIMESTAMPED EVENT
PROGRESS under a documented sport-specific mapping, and forbids
estimating it from elapsed wall-clock time or an assumed duration.

WE HAVE NO SUCH FEED. Searched: no `period`, `quarter`, `inning`,
`half`, `game_clock` or score column exists anywhere in the migrations.
The only event-state we hold is `live_status`, and
`bettor_state_capture._live` derives it as

    secs = game_start - observed_at
    PREGAME if secs > 0 else LIVE

-- a SCHEDULED-START WALL-CLOCK derivation, which is precisely the
estimate §3 rules out. Using it to place a halfway point would be the
forbidden inference wearing a different name.

SO EVERY MARKET IS CURRENTLY SECOND_HALF_UNDEFINED. Part 2 is
implemented in full and reports LOSS_EXIT_UNAVAILABLE with the exposure
retained visibly, exactly as §3 directs. No event type is admitted to
the loss-exit experiment yet, and none will be until a progress feed
with a documented mapping exists. Part 1 needs no event state and runs.

    THE SMALLEST MISSING INPUT is a timestamped period/clock feed for
    ONE sport, plus its documented mapping to a halfway point. Nothing
    else in this policy is blocked.
════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

POLICY_ID = "MANAGEMENT_PAIR_091_STOP_16_V1"
POLICY_CLASS = "MANAGEMENT_DEFINED_EXPERIMENTAL"
FROZEN_AT = "2026-09-23"

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── §1 the pairing target ────────────────────────────────────────────
PAIR_TARGET_COST = 0.91        # combined, INCLUDING fees
PAIR_MIN_NET_PER_PAIR = 0.09   # 1.00 - 0.91, stated so both move together
TICK = 0.01                    # venue price tick

# ── §2 the loss trigger ──────────────────────────────────────────────
LOSS_TRIGGER_FRACTION = 0.84   # net proceeds / allocated cost
SECOND_HALF_ONLY = True

# ── BOTH THRESHOLDS WERE QUERIED AND CONFIRMED, 2026-09-23 ───────────
#
# Management read the policy back as "buy the opposite side at $0.34 or
# better, or sell the $0.57 position at $0.41". Both are the fee-FREE
# arithmetic, and on a 961.58-contract position the difference is
# material, so each was put back rather than assumed.
#
# PAIRING, on a 0.57 basis. CONFIRMED: the 0.91 is ALL-IN, fees INSIDE.
#     limit 0.32 -> purchase 0.8900, fee 0.0151, combined 0.9051,
#                   NET per completed pair 0.0949   <-- meets ">= $0.09 net"
#     limit 0.33 -> combined 0.9154, net 0.0846     (over the target)
#     limit 0.34 -> purchase EXACTLY 0.9100, but combined 0.9256 once
#                   the completion fee lands, net only 0.0744
#   0.34 and ">= $0.09 net" are not simultaneously satisfiable at this
#   basis. The net floor was kept.
#
# LOSS EXIT, on a 0.57 basis. CONFIRMED: 84% OF ALLOCATED COST.
#     0.57 x 0.84 = 0.4788, so the trigger bid is ~0.48
#     0.41 is 71.9% of cost -- a 28.1% loss, not 16%. It equals
#     0.57 - 0.16, i.e. 16 POINTS of the $1 payout rather than 16% of
#     cost. That reading was declined.
#   The two differ by 0.0688 per contract = $66.16 on this position.
#
# No code changed: the implementation already matched both confirmations.
THRESHOLD_CONFIRMATIONS = {
    "asked_on": "2026-09-23",
    "pair_target_basis": "ALL_IN_INCLUDING_FEES",
    "pair_limit_at_057_basis": 0.32,
    "pair_net_at_057_basis": 0.0949,
    "pair_reading_declined": ("0.34, which is the purchase cost alone and "
                             "nets 0.0744 -- below the $0.09 floor"),
    "loss_trigger_basis": "84_PERCENT_OF_ALLOCATED_COST",
    "loss_trigger_bid_at_057_basis": 0.4788,
    "loss_reading_declined": ("0.41, which is 16 POINTS of the $1 payout "
                             "and 71.9% of cost -- a 28.1% loss, not 16%"),
}

# ── §3 event phase ───────────────────────────────────────────────────
SECOND_HALF_UNDEFINED = "SECOND_HALF_UNDEFINED"
PROGRESS_UNAVAILABLE = "EVENT_PROGRESS_UNAVAILABLE"
FIRST_HALF = "FIRST_HALF"
SECOND_HALF = "SECOND_HALF"

# Sports admitted to the loss-exit experiment, with their mapping. EMPTY
# BY CONSTRUCTION: a sport is added here only when a timestamped
# progress feed for it exists AND its halfway mapping is written down.
# An empty registry is the honest state, not an oversight.
SECOND_HALF_MAPPING: dict = {}

MAPPING_REQUIREMENTS = (
    "a timestamped progress field observed from the venue or a feed "
    "(period/quarter/inning/clock), NOT derived from game_start",
    "a documented rule mapping that field to a halfway point for THAT "
    "sport",
    "the field present on the rows this policy reads, at decision time",
)

WHY_NO_SPORT_IS_ADMITTED = (
    "no period, quarter, inning, half, clock or score column exists in "
    "the schema. `live_status` is PREGAME/LIVE derived from "
    "game_start - observed_at, a scheduled-start wall-clock estimate, "
    "which section 3 forbids as a basis for the halfway point")

# ── provenance of every readable input ───────────────────────────────
SOURCE_CLASS = {
    "held_basis": "OBSERVED_INPUT (the recorded cost basis of held inventory)",
    "entry_fee": "OBSERVED_INPUT (published schedule; zero on an assigned seed)",
    "completion_fee": "OBSERVED_INPUT (published schedule, TAKER side)",
    "executable_bid": "OBSERVED_INPUT (the venue's own book)",
    "bid_depth": "OBSERVED_INPUT",
    "event_progress": "OBSERVED_INPUT -- ABSENT, see WHY_NO_SPORT_IS_ADMITTED",
    "pair_target_cost": "MANAGEMENT_DEFINED (0.91)",
    "loss_trigger_fraction": "MANAGEMENT_DEFINED (0.84)",
    "tick_rounding": "VENUE_MECHANIC, rounded conservatively",
    "our_fill": "EXECUTION_ASSUMPTION (print-through; P_FILL NOT_IDENTIFIED)",
    "settlement_payout": "OBSERVED_INPUT, available only for SCORING",
}

BENCHMARKS = {
    "HOLD_TO_SETTLEMENT": ("the same assigned inventory carried to the "
                           "observed payout; no action, no fees"),
    "RN1_MANAGEMENT": ("what the source account did next on the same "
                       "condition. OBSERVED, at their size and their "
                       "order policy -- WHALE_ORDER_POLICY is "
                       "NOT_IDENTIFIED, so a benchmark and not an "
                       "achievable return"),
}

DOES_NOT_ESTABLISH = (
    "profitability -- this is management-defined and experimental",
    "that it is learned: nothing here is fitted",
    "our fill probability: P_FILL is NOT_IDENTIFIED, and the resting "
    "complementary buy depends on it",
    "a 16% trigger is not a 16% realised loss: the trigger price and "
    "the execution price are reported separately",
    "that RN1's prices were available to us: assigned-entry testing",
)


def _fee_taker(qty, price) -> float:
    """The published TAKER fee. Used even for a RESTING order's limit.

    WHY THE TAKER SIDE. A resting buy that fills is a MAKER fill and
    earns a rebate (theta_maker -0.0125). Section 1 says not to rely on
    an uncertain rebate to meet the target, so the limit is solved
    against the POSITIVE taker fee. If the rebate arrives it is upside
    the target never counted on.
    """
    from . import bettor_fee_schedule as FEES
    return float(FEES.LATEST.fill_fee(Decimal(str(round(float(qty), 6))),
                                      Decimal(str(round(float(price), 6))),
                                      maker=False))


def pair_limit(held_basis_per_contract, qty, *, entry_fee_usd=0.0) -> dict:
    """§1. The highest complementary limit that still meets the target.

    Solved on the TICK GRID, descending, and rounded CONSERVATIVELY: the
    limit is the largest tick at which combined cost including fees is
    still <= 0.91, so the policy never bids a price that would miss the
    target by a rounding step.
    """
    b = float(held_basis_per_contract)
    q = float(qty)
    entry_per = float(entry_fee_usd) / q if q > 0 else 0.0
    budget = PAIR_TARGET_COST - b - entry_per
    out = {"policy_id": POLICY_ID, "held_basis_per_contract": b,
           "entry_fee_per_contract": entry_per,
           "target_combined_cost": PAIR_TARGET_COST,
           "min_net_per_pair": PAIR_MIN_NET_PER_PAIR,
           "tick": TICK, "qty": q,
           "fee_side_used": "TAKER (the rebate is not relied on)",
           "source_class": SOURCE_CLASS["pair_target_cost"]}
    if budget <= 0:
        out.update(limit=None, feasible=False,
                   why=("the held basis %.4f already leaves no room under "
                        "a %.2f combined target, so no complementary "
                        "limit can meet it" % (b, PAIR_TARGET_COST)))
        return out
    # Descend the tick grid from the budget. The fee depends on the
    # price, so each candidate is checked rather than solved in closed
    # form -- p(1-p) makes the closed form a quadratic and the grid is
    # exact and obvious.
    ticks = int(budget / TICK) + 2
    for i in range(ticks, 0, -1):
        a = round(i * TICK, 2)
        if a <= 0:
            continue
        fee_per = _fee_taker(q, a) / q if q > 0 else 0.0
        combined = b + entry_per + a + fee_per
        if combined <= PAIR_TARGET_COST + 1e-12:
            out.update(limit=a, feasible=True,
                       completion_fee_per_contract=fee_per,
                       combined_cost_per_pair=combined,
                       net_per_completed_pair=1.00 - combined,
                       why=("bid %.2f: %.4f basis + %.4f entry fee + "
                            "%.2f completion + %.4f completion fee = "
                            "%.4f combined, netting %.4f per completed "
                            "pair against a %.2f minimum"
                            % (a, b, entry_per, a, fee_per, combined,
                               1.00 - combined, PAIR_MIN_NET_PER_PAIR)))
            return out
    out.update(limit=None, feasible=False,
               why=("no tick at or below %.4f meets the target once the "
                    "completion fee is included" % budget))
    return out


def event_phase(*, progress=None, sport=None) -> dict:
    """§3. The event phase, from OBSERVED progress or not at all.

    `progress` must be a timestamped observation of event state. There
    is no fallback: absent a feed and a mapping this returns
    SECOND_HALF_UNDEFINED, which excludes the market from the loss-exit
    experiment rather than guessing.
    """
    out = {"source_class": SOURCE_CLASS["event_progress"],
           "sport": sport, "mapping_requirements": list(MAPPING_REQUIREMENTS)}
    if sport not in SECOND_HALF_MAPPING:
        out.update(phase=SECOND_HALF_UNDEFINED, loss_exit_available=False,
                   admitted_to_experiment=False,
                   why=WHY_NO_SPORT_IS_ADMITTED)
        return out
    if progress is None:
        # Admitted sport, but progress went missing after entry.
        out.update(phase=PROGRESS_UNAVAILABLE, loss_exit_available=False,
                   admitted_to_experiment=True,
                   why=("this sport has a mapping but no progress "
                        "observation is available now. The loss exit is "
                        "UNAVAILABLE and the exposure is retained "
                        "visibly rather than exited on a guess"))
        return out
    rule = SECOND_HALF_MAPPING[sport]
    phase = rule(progress)
    out.update(phase=phase, loss_exit_available=(phase == SECOND_HALF),
               admitted_to_experiment=True, observed_progress=progress)
    return out


def loss_trigger(*, allocated_cost_usd, qty, bid=None, bid_size=None) -> dict:
    """§2. Are net executable proceeds <= 84% of allocated cost?

    USES EXECUTABLE BIDS AND DEPTH. Not the last trade, not the midpoint
    -- a last trade is where someone else transacted and a midpoint is
    where nobody did.

    THE TRIGGER PRICE IS NOT THE EXECUTION PRICE. This returns the level
    at which the rule fires; what the sale actually achieves is a
    separate number reported beside it. A 16% trigger does not guarantee
    a 16% realised loss, and a gap through the level can make it worse.
    """
    q = float(qty)
    cost = float(allocated_cost_usd)
    out = {"policy_id": POLICY_ID, "qty": q,
           "allocated_cost_usd": cost,
           "trigger_fraction": LOSS_TRIGGER_FRACTION,
           "source_class": SOURCE_CLASS["loss_trigger_fraction"],
           "price_input": "EXECUTABLE_BID_AND_DEPTH",
           "not_used": ["last trade price", "midpoint"]}
    if bid is None:
        out.update(fired=False, status=NOT_IDENTIFIED,
                   blocker="NO_EXECUTABLE_BID",
                   why=("no bid was readable, so net proceeds are unknown "
                        "-- not zero, and not a reason to sell"))
        return out
    depth = float(bid_size or 0.0)
    if depth <= 0:
        out.update(fired=False, status=NOT_IDENTIFIED,
                   blocker="NO_EXECUTABLE_DEPTH",
                   why="a bid with no size behind it is not an exit")
        return out
    sellable = min(q, depth)
    fee = _fee_taker(sellable, bid)
    proceeds = float(bid) * sellable - fee
    # The cost allocated to the quantity actually sellable, so a
    # depth-limited exit is not compared against the whole position's
    # cost.
    cost_alloc = cost * (sellable / q) if q > 0 else 0.0
    ratio = (proceeds / cost_alloc) if cost_alloc > 0 else None
    fired = ratio is not None and ratio <= LOSS_TRIGGER_FRACTION
    out.update(
        fired=fired, status="EVALUATED",
        sellable_qty=sellable, depth_limited=sellable < q - 1e-12,
        trigger_bid=float(bid),
        net_proceeds_usd=proceeds, fees_usd=fee,
        allocated_cost_for_sellable_usd=cost_alloc,
        proceeds_over_cost=ratio,
        trigger_level_bid=(LOSS_TRIGGER_FRACTION * cost_alloc / sellable
                           if sellable > 0 else None),
        trigger_price_is_not_execution_price=(
            "this is the level the rule fires at. What a sale achieves is "
            "reported separately; a gap through the level can be worse"),
        why=("net proceeds %.4f on %.4g sellable against allocated cost "
             "%.4f is %.4f of cost; the trigger is %.2f"
             % (proceeds, sellable, cost_alloc, ratio or 0.0,
                LOSS_TRIGGER_FRACTION)))
    return out


def describe() -> dict:
    return {
        "policy_id": POLICY_ID, "policy_class": POLICY_CLASS,
        "frozen_at": FROZEN_AT,
        "label": ("MANAGEMENT-DEFINED and EXPERIMENTAL. Not learned, not "
                  "fitted, not shown to be profitable"),
        "replaces": ("the provisional adverse-move / time-open trigger, "
                     "which is withdrawn for this experiment"),
        "pairing": {
            "target_combined_cost": PAIR_TARGET_COST,
            "min_net_per_completed_pair": PAIR_MIN_NET_PER_PAIR,
            "allowed_throughout_event": True,
            "fee_side": "TAKER -- the maker rebate is not relied on",
            "tick": TICK, "rounding": "CONSERVATIVE",
            "requires": ("a CONFIRMED complementary contract with the "
                         "applicable $1 combined payout"),
        },
        "loss_exit": {
            "trigger_fraction": LOSS_TRIGGER_FRACTION,
            "second_half_only": SECOND_HALF_ONLY,
            "price_input": "EXECUTABLE_BID_AND_DEPTH",
            "availability": "UNAVAILABLE -- no admitted sport",
            "why_unavailable": WHY_NO_SPORT_IS_ADMITTED,
            "admitted_sports": sorted(SECOND_HALF_MAPPING),
            "mapping_requirements": list(MAPPING_REQUIREMENTS),
            "smallest_missing_input": (
                "a timestamped period/clock feed for ONE sport plus its "
                "documented halfway mapping. Nothing else is blocked"),
        },
        "source_class": dict(SOURCE_CLASS),
        "benchmarks": dict(BENCHMARKS),
        "does_not_establish": list(DOES_NOT_ESTABLISH),
        "authorizes": "SHADOW BEHAVIOUR ONLY. Not funded trading.",
    }
