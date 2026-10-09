"""WHICH FEE ARITHMETIC EACH PRODUCTION PATH ACTUALLY RUNS.

I reported the fee finding closed because `calibration_fees` now implements
the published page: banker's rounding, the running cumulative taker cap in
`order_fees()`, per-sport theta with effective dates, a combo refusal and the
execution-report units. All of that is true of the module.

IT IS NOT TRUE OF THE SYSTEM, and this module is the audit that says so.

THE FINDING, AS IT STOOD WHEN THIS MODULE WAS WRITTEN. `order_fees()` -- the
function carrying the cumulative cap -- had ZERO production callers. Every
production path computed fees one fill at a time, and two of them did not use
`calibration_fees` at all.

WHAT HAS BEEN REPAIRED SINCE, AND IT IS THE TWO PATHS WHERE MONEY IS AT STAKE.
Both places that can actually SEE a fill sequence now price it as an order:

  * `bettor_entry_execution.walk_fee` replaced the inline
    `sum(fee_fn(level) for level in levels_taken)` -- a depth walk across three
    ladder levels IS a three-fill order -- and calls `order_fees`. It first
    CHECKS the caller's fee_fn against the published single-fill taker curve at
    every level taken, because the cap is a taker rule and one caller passes a
    maker rebate function; a mismatch keeps the caller's own arithmetic and
    reports `schedule_reaches: False` rather than imposing the cap on a curve
    it does not belong to.

  * `bettor_funded_book.reconcile_fee` now takes `prior_legs` and
    `_ingest_locked` supplies this order's already-persisted fills, read back
    inside the position lock and excluding the fill being ingested by its own
    id. The expectation for a fill is the increment the published algorithm
    attributes to THAT leg.

`order_fees` was also generalised to per-fill prices, because a marketable walk
takes each level at its own price and a single-price signature could not
express one -- which is the concrete reason the entry planner had been summing
per level instead of calling it.

WHAT IS STILL NOT REPAIRED is listed in STILL_OPEN and it is not small: the
entry planner and shadow loops still resolve theta through
`bettor_fee_schedule.LATEST` rather than the fill's own date, no path passes a
sport, and nothing here has been compared against a real observed charge.

TWO FEE MODULES EXIST AND THEY ARE NOT THE SAME CODE.

    calibration_fees        corrected today. `expected_fee` (one fill),
                            `order_fees` (a fill SEQUENCE, with the cap),
                            `maker_rebates`, per-sport theta, combo refusal.

    bettor_fee_schedule     dated Schedule objects. `fill_fee` (one fill).
                            NO cumulative cap in `fill_fee`; the cap lives in
                            `TakerAccrual`, which no production caller uses.
                            NO per-sport theta at all.

They agree on the exchange-wide taker coefficient (0.0695) and on banker's
rounding, which is why this did not show up as a wrong number in a test. They
disagree on two things that DO change money:

  * THE EFFECTIVE DATE. `bettor_fee_schedule.PMUS_2026_09_17.effective_from`
    is "2026-09-17". The retrieved page says the schedule is effective
    "12 AM ET, Friday September 25, 2026". Eight days of fills would be
    priced on a schedule the venue had not yet put in force.

  * PER-SPORT THETA. The page schedules Table Tennis to 0.10 at 11:59 PM ET
    2026-09-30. `bettor_fee_schedule` has one theta for every sport, so from
    that instant it would understate a Table Tennis taker fee by 31%. Table
    Tennis is outside the proposed operating scope -- which limits the blast
    radius and does not make the schedule correct.

AND THE SHAPE OF THE ERROR IS THE ONE THAT WAS PREDICTED. A cap applied to an
order cannot be recovered by charging each fill separately: three fills of 40
at $0.50 are $0.70 each = $2.10 independently, against $2.08 under the cap.
Two cents on $60 of notional is small; the per-fill sequence being WRONG is
not, because reconciliation compares our number to the venue's fill by fill,
and a disagreement on every multi-fill order is indistinguishable from a fee
we do not understand.

WHAT THIS MODULE IS. A register of every production path that prices a fee,
what it calls, and whether the corrected arithmetic reaches it. It is data,
checked by tests against the real call sites, so a path cannot be added
without appearing here and a claim of closure cannot outrun the code again.

WHAT IT IS NOT. It is not the repair. `A2` stays IMPLEMENTED, not VERIFIED:
the corrected schedule is implemented, its arrival at every consumer is
partially repaired here, and deployed behaviour has not been observed.
"""

from __future__ import annotations

#: Whether the corrected schedule reaches this consumer.
REACHED = "CORRECTED_SCHEDULE_REACHES_THIS_PATH"
PARTIAL = "PARTIALLY_REACHED"
NOT_REACHED = "CORRECTED_SCHEDULE_DOES_NOT_REACH_THIS_PATH"

#: Whether the path can see a fill SEQUENCE at all. A path that prices one
#: hypothetical fill cannot apply a cumulative cap and is not wrong not to;
#: a path that books real fills one at a time IS wrong not to.
SEQUENCE_VISIBLE = "SEES_THE_ORDER_S_FILL_SEQUENCE"
SINGLE_FILL_ONLY = "PRICES_ONE_HYPOTHETICAL_FILL"

#: Every production path that prices a fee. `symbol` is the call site, and
#: the tests below read the actual source to confirm it is still there.
CONSUMERS = (
    {
        "path": "ENTRY_PLANNER",
        "component": "workers/ext_pinnacle_loop.py",
        "symbol": "fee_fn -> bettor_fee_schedule.LATEST.fill_fee",
        "module": "bettor_fee_schedule",
        "reaches": NOT_REACHED,
        "sequence": SINGLE_FILL_ONLY,
        "what_it_prices": (
            "the break-even limit (fair_value - fee_per_contract) and the "
            "realised per-contract fee across a depth walk"),
        "defects": (
            "uses LATEST, so `for_date` -- the whole point of a dated "
            "registry -- is bypassed and a fill is priced on today's "
            "schedule whatever its own date",
            "no per-sport theta, so Table Tennis is understated from "
            "2026-10-07T04:00Z",
            "effective_from 2026-09-17 against the published 2026-09-25",
        ),
        "money_consequence": (
            "the break-even limit is the price above which we will not buy. "
            "An understated fee RAISES that limit, so this defect makes the "
            "lane willing to pay MORE, not less"),
    },
    {
        "path": "ENTRY_DEPTH_WALK",
        "component": "bettor_entry_execution.estimate",
        "symbol": "bettor_entry_execution.walk_fee -> order_fees",
        "module": "calibration_fees",
        "reaches": REACHED,
        "sequence": SEQUENCE_VISIBLE,
        "what_it_prices": (
            "fee_per_contract_realised, fee_total_usd, and the per-leg "
            "collected amounts under the running cap"),
        "defects": (
            "REPAIRED. It previously summed each level's own independently "
            "rounded fee -- a depth walk across three ladder levels IS a "
            "three-fill order, and summing independent roundings is the "
            "calculation the published cap replaces. It now calls order_fees "
            "with the per-level (qty, price) legs",
            "STILL OPEN: it passes no sport, so per-sport theta is inert here",
        ),
        "money_consequence": (
            "the old sum overstated a multi-level entry's cost, which was "
            "conservative on the entry decision and wrong in the preview the "
            "reconciliation is later compared against. Both numbers are now "
            "reported -- `fee_if_levels_were_priced_independently` keeps the "
            "old figure visible instead of making the difference something "
            "that has to be reconstructed"),
    },
    {
        "path": "FUNDED_FILL_BOOKING",
        "component": "bettor_funded_book.fee_for / reconcile_fee",
        "symbol": ("reconcile_fee(prior_legs=prior_taker_legs(...)) -> "
                   "order_fees"),
        "module": "calibration_fees",
        "reaches": REACHED,
        "sequence": SEQUENCE_VISIBLE,
        "what_it_prices": (
            "each ingested fill's expected fee as the increment the published "
            "algorithm attributes to that leg, stored per fill in "
            "bettor_funded_fills.expected_fee_usd"),
        "defects": (
            "REPAIRED. It called `expected_fee` (one fill) and not "
            "`order_fees` (the sequence), so the cap never applied to a "
            "funded order that filled more than once",
            "STILL OPEN: passes no sport, so per-sport theta cannot take "
            "effect",
        ),
        "money_consequence": (
            "the EXPECTED column disagreed with the venue on every "
            "multi-fill order. The OBSERVED charge stays authoritative and is "
            "what gets booked, so this was misstating the expectation and the "
            "reconciliation verdict rather than the cash -- which is why it "
            "would have shown up as a fee we appear not to understand"),
        "restart_and_redelivery": (
            "the sequence is read from bettor_funded_fills inside the "
            "position lock, not held in a batch variable, so a restart "
            "mid-order prices the fourth fill as the fourth fill; and the "
            "fill being ingested is excluded from its own priors by id, so a "
            "duplicate delivery cannot promote it to a later leg"),
    },
    {
        "path": "EXIT_PLANNER",
        "component": "bettor_funded_management",
        "symbol": ("funded_fee_fn -> bettor_funded_book.fee_for(qty, price, "
                   "at=time.time()), passed as `fee_fn` to the exit selector"),
        "module": "calibration_fees",
        "reaches": PARTIAL,
        "sequence": SINGLE_FILL_ONLY,
        "what_it_prices": ("the expected fee on a proposed exit, one "
                           "hypothetical fill at the exit price"),
        "defects": (
            "one hypothetical fill, so the cap is not APPLICABLE at decision "
            "time -- nothing has filled yet and there is no sequence to cap",
            "but an exit that then fills in three pieces is BOOKED through "
            "FUNDED_FILL_BOOKING, which does apply the cap. So the decision "
            "and the booking use different arithmetic, and the difference is "
            "the cap's adjustment",
            "no sport passed",
        ),
        "money_consequence": (
            "the exit DECISION is priced slightly high -- the uncapped "
            "per-fill sum exceeds the capped total -- which is conservative "
            "on whether to exit and wrong in the preview that the booking is "
            "later compared against"),
        "why_this_is_not_the_same_defect_as_the_entry_walk": (
            "the entry walk KNOWS its ladder levels, so it knows the fill "
            "sequence before it acts and can price the order. An exit "
            "selector is choosing a quantity against a single price and does "
            "not know how the venue will break it up. Pricing it as one fill "
            "is the best available estimate, not an omission"),
    },
    {
        "path": "PREVIEW_COMPARISON",
        "component": "bettor_funded_execution / pmus.submit_fok",
        "symbol": "collateral_for(limit_price, quantity, intent)",
        "module": "(none -- this is COLLATERAL, not fees)",
        "reaches": "NOT_APPLICABLE",
        "sequence": SINGLE_FILL_ONLY,
        "what_it_prices": (
            "the COLLATERAL the venue takes, compared against the venue's own "
            "orders.preview cost. `(1 - p) x q` on a short, `p x q` on a long"),
        "defects": (
            "NONE OF THE FEE KIND, and tracing this was worth it to establish "
            "that. The preview comparison is about collateral and does not "
            "include a fee term at all -- the venue's preview states its own "
            "cost and ours is compared to it",
        ),
        "money_consequence": (
            "none from fees. The collateral formula had its own defect -- the "
            "connector used the LONG formula on both sides, refusing correctly "
            "sized shorts below 0.50 and overspending above it -- and that is "
            "already fixed and shared with the adapter"),
        "and_this_is_why_it_is_listed": (
            "a census that omitted it would leave open whether the preview "
            "path carries an uncorrected fee. It does not carry one at all"),
    },
    {
        "path": "FEE_ADJUSTMENTS_AND_LATE_CORRECTIONS",
        "component": "bettor_funded_book._repair_one_fill / reconcile_observed",
        "symbol": "reconcile_fee(..., observed=...) on redelivery",
        "module": "calibration_fees",
        "reaches": REACHED,
        "sequence": SEQUENCE_VISIBLE,
        "what_it_prices": (
            "a fill whose OBSERVED charge arrives after the fill was first "
            "ingested -- the common case, because the venue often states a "
            "commission only on a later delivery"),
        "defects": (
            "REPAIRED. The expectation is recomputed from the persisted "
            "sequence, so a late correction lands on the right leg",
        ),
        "money_consequence": (
            "the OBSERVED charge is what the account paid and is what gets "
            "booked. A late correction changes `booked_fee_usd` and the "
            "reconciliation verdict; it does NOT change the expectations of "
            "other fills, because those are derived from (qty, price) and not "
            "from anybody's observed charge. That is what keeps a late "
            "arrival from cascading"),
        "and_the_cumulative_expectations_stay_consistent": (
            "an observed charge is an input to the reconciliation, never to "
            "the expectation. So a correction on fill 2 cannot make fills 3 "
            "and 4 inconsistent -- only inserting a fill can, and that is "
            "handled by `restates_following_fills`"),
    },
    {
        "path": "DISPLAY",
        "component": "api/desk_page.py",
        "symbol": "fees_usd, fee_per, realised_net_of_fees_usd -- READ ONLY",
        "module": "(none -- it renders stored values)",
        "reaches": "NOT_APPLICABLE",
        "sequence": SINGLE_FILL_ONLY,
        "what_it_prices": "nothing. It renders what the books already hold",
        "defects": (
            "NONE OF THE FEE KIND. The desk page computes no fee; every figure "
            "it shows was computed by one of the paths above and stored",
        ),
        "money_consequence": (
            "none directly -- and a display that recomputed a fee would be a "
            "second implementation, which is the defect this census exists to "
            "find. It does not"),
    },
    {
        "path": "SHADOW_LOOP",
        "component": "bettor_shadow_loop / bettor_desk_loop / rn1x",
        "symbol": "bettor_fee_schedule.LATEST.fill_fee",
        "module": "bettor_fee_schedule",
        "reaches": NOT_REACHED,
        "sequence": SINGLE_FILL_ONLY,
        "what_it_prices": "modelled fees in the shadow books",
        "defects": ("LATEST rather than the fill's own date",
                    "no per-sport theta"),
        "money_consequence": (
            "MODELLED money only. It never reaches an account, but it is "
            "what the strategy evidence is measured in, so a fee error here "
            "biases the recorded edge"),
    },
    {
        "path": "DESK_CORRECTION",
        "component": "bettor_desk_correction",
        "symbol": "bettor_fee_schedule.for_date(iso).fill_fee",
        "module": "bettor_fee_schedule",
        "reaches": PARTIAL,
        "sequence": SINGLE_FILL_ONLY,
        "what_it_prices": "a restated historical fee",
        "defects": ("dates correctly -- this is the ONE path that does -- "
                    "but still on the 2026-09-17 effective date and with no "
                    "per-sport theta",),
        "money_consequence": "restates history on a schedule off by 8 days",
    },
    {
        "path": "TEST_VENUE_EXECUTOR",
        "component": "bettor_test_venue_executor",
        "symbol": "bettor_fee_schedule.LATEST.fill_fee, summed per fill",
        "module": "bettor_fee_schedule",
        "reaches": NOT_REACHED,
        "sequence": SEQUENCE_VISIBLE,
        "what_it_prices": (
            "expected_fees = sum(fee_for(f) for f in fills), then asserts "
            "fees_reconcile against its own sum"),
        "defects": (
            "IT RECONCILES AGAINST ITSELF. Both sides of `fees_reconcile` "
            "come from the same per-fill sum, so the check passes however "
            "wrong the arithmetic is. This is the substituted transport, so "
            "no real money is involved -- but it is why the lifecycle proofs "
            "never surfaced the missing cap",
        ),
        "money_consequence": "none directly; it is why this went unseen",
    },
)

#: The one function that implements the published cumulative algorithm, and
#: the count of production paths that call it.
CUMULATIVE_IMPLEMENTATION = "calibration_fees.order_fees"
CUMULATIVE_PRODUCTION_CALLERS = 2
CUMULATIVE_PRODUCTION_CALLERS_WERE = 0
CUMULATIVE_PRODUCTION_CALLER_NAMES = (
    "bettor_entry_execution.walk_fee",
    "bettor_funded_book.reconcile_fee (via _ingest_locked's prior_legs)",
)

#: The two schedules' disagreements, as facts rather than prose.
SCHEDULE_DISAGREEMENTS = (
    {
        "field": "effective_from",
        "calibration_fees": "2026-09-25T04:00:00Z",
        "bettor_fee_schedule": "2026-09-17",
        "published": "12 AM ET, Friday September 25, 2026",
        "authority": "calibration_fees",
        "why": ("the page was retrieved on 2026-09-27 and quoted verbatim in "
                "research/evidence/VENUE_FEE_POLICY_2026-09-27.md. The "
                "2026-09-17 date predates that retrieval and has no quoted "
                "source"),
    },
    {
        "field": "theta_taker_is_per_sport",
        "calibration_fees": "yes, with TABLE_TENNIS -> 0.10 at 2026-10-07T04:00Z",
        "bettor_fee_schedule": "no, one theta for every sport",
        "published": ("The Table Tennis taker fee coefficient becomes 0.10, "
                      "effective 11:59 PM ET, Wednesday September 30, 2026"),
        "authority": "calibration_fees",
        "why": "the page states it and one constant cannot represent it",
    },
    {
        "field": "multi_fill_taker_algorithm",
        "calibration_fees": ("order_fees: per-fill banker's rounding with a "
                             "running cap at banker's(cumulative exact)"),
        "bettor_fee_schedule": ("fill_fee: per-fill only. TakerAccrual "
                                "implements a cumulative rule and has no "
                                "production caller"),
        "published": ("each fill is charged its banker's-rounded fee, "
                      "adjusted so that the total commission collected "
                      "across the order's fills never exceeds the banker's "
                      "rounding of the cumulative exact fee"),
        "authority": "calibration_fees",
        "why": ("TakerAccrual's rule -- recompute on total filled and deduct "
                "what was charged -- gives the same TOTAL as the published "
                "cap on a monotonic fill sequence, and differs from it when "
                "banker's(cumulative exact) falls below what is already "
                "charged: the published wording caps, it does not claw back"),
    },
)

#: What is still open after this audit, stated so it cannot be read as closed.
STILL_OPEN = (
    "the entry planner and shadow loops price on bettor_fee_schedule.LATEST "
    "rather than the fill's own date",
    "bettor_fee_schedule's effective_from is 2026-09-17 against the "
    "published 2026-09-25, and it has no per-sport theta at all",
    "no production path passes a sport, so per-sport theta is inert",
    "the EXIT planner prices one hypothetical fill, so the exit DECISION and "
    "the exit BOOKING use different arithmetic. That is inherent -- a selector "
    "choosing a quantity against one price cannot know how the venue will "
    "break it up -- and the gap is the cap's adjustment, which is small and "
    "conservative in the decision's direction",
    "observed charges have never been compared against this schedule for a "
    "real account, so nothing here is VERIFIED_APPLIED",
)

A2_STATUS = "IMPLEMENTED"
A2_STATUS_IS_NOT = "VERIFIED"
A2_WHY_NOT_VERIFIED = (
    "verification needs the corrected schedule observed in the DEPLOYED "
    "entry planner, exit planner, preview comparison and reconciliation, "
    "against a real multi-fill order with the venue's own observed charge. "
    "None of that has happened: this repair has not been deployed, and this "
    "lane has never had a real fill to reconcile against")


def by_path(name: str) -> dict | None:
    for c in CONSUMERS:
        if c["path"] == name:
            return c
    return None


def paths_the_correction_misses() -> tuple:
    """The consumers the corrected schedule does NOT reach."""
    return tuple(c["path"] for c in CONSUMERS if c["reaches"] == NOT_REACHED)


def paths_that_see_a_fill_sequence() -> tuple:
    """Where a cumulative cap is applicable at all.

    A path that prices one hypothetical fill is not wrong to omit the cap.
    These are the paths where omitting it IS the defect.
    """
    return tuple(c["path"] for c in CONSUMERS
                 if c["sequence"] == SEQUENCE_VISIBLE)


def describe() -> dict:
    return {
        "consumers": len(CONSUMERS),
        "cumulative_implementation": CUMULATIVE_IMPLEMENTATION,
        "cumulative_production_callers": CUMULATIVE_PRODUCTION_CALLERS,
        "not_reached": list(paths_the_correction_misses()),
        "cap_is_applicable_on": list(paths_that_see_a_fill_sequence()),
        "schedule_disagreements": len(SCHEDULE_DISAGREEMENTS),
        "still_open": list(STILL_OPEN),
        "A2": {"status": A2_STATUS, "is_not": A2_STATUS_IS_NOT,
               "why": A2_WHY_NOT_VERIFIED},
        "the_finding": (
            "a corrected fee module is not corrected fees. order_fees() "
            "carries the published cumulative algorithm and has %d "
            "production callers" % CUMULATIVE_PRODUCTION_CALLERS),
    }
