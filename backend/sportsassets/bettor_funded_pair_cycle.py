"""THE SCHEDULED PAIR CYCLE: the production importer the new modules lacked.

An independent review named the same thing twice, and it was the fair criticism:

    "There are still no production importers of the three new modules."
    "`reserved_collateral_usd()` is a helper, not an enforced account-wide limit
     until the risk consumer calls it."

`bettor_funded_reservations`, `bettor_funded_decision`, `bettor_funded_learning`
and `bettor_funded_indirect_pair` were reachable only from their own tests. A
module no scheduled path calls is a proposal about how the system might work.

THIS IS THE PATH THAT CALLS THEM, in the order one cycle has to do it:

    ext_pinnacle_loop.cycle
      -> _funded_service
        -> bettor_funded_management.manage        the existing servicing pass
        -> bettor_funded_pair_cycle.pass_once     THIS MODULE
             1 recover_reservations   every live reservation, from EVIDENCE
             2 discover               two distinct settlement-compatible
                                      contracts on ONE fixture
             3 decide                 HOLD / DIRECT_EXIT / REDUCE / the
                                      indirect acquisition, ranked on EXPECTED
                                      net value with the worst case as a
                                      CONSTRAINT
             4 acquire                reservation -> intent -> send -> ack,
                                      atomically, through
                                      bettor_funded_execution
             5 reconcile              group economics once, and the learning
                                      record attributed to the decision

──────────────────────────────────────────────────────────────────────
WHAT THIS MODULE DECIDES AND WHAT IT ONLY PASSES THROUGH.

It decides NOTHING about value. It does not price a contract, size an order,
compute a worst case or rank an action. `bettor_funded_decision.decide` ranks;
`bettor_indirect_structures.classify` establishes the structure;
`bettor_mgmt_select.rank_with_hold` supplies HOLD, DIRECT_EXIT and REDUCE;
`bettor_funded_execution.submit_for_decision` runs every rail, the authorization
gate and the submission switch. What this module does is SEQUENCE them and make
each step's failure a named refusal rather than an exception.

──────────────────────────────────────────────────────────────────────
ORDER 1 IS FIRST ON PURPOSE, AND IT IS THE RESTART GUARANTEE.

Recovery runs BEFORE discovery, before any decision and before any acquisition.
A reservation left AMBIGUOUS by a lost acknowledgement still CLAIMS its leg, so
until the venue's own answer is recorded and read, this cycle cannot decide to
acquire that leg again -- the claim refuses it, and the refusal is
`THAT_LEG_ALREADY_HAS_A_LIVE_RESERVATION`. That is what makes "restart without
duplicate submission" a property of the DATA rather than of whether the process
remembered what it was doing when it died.

AND RECOVERY NEVER RESENDS. It reads evidence and moves the reservation to
CONSUMED or RELEASED. Nothing in this module submits an order to resolve an
ambiguity; an ambiguous send is resolved by ASKING, never by sending again.

──────────────────────────────────────────────────────────────────────
THE VENUE IS ONE INSTRUMENT PER MARKET, WHICH IS WHY THIS IS INDIRECT.

On Polymarket US buying the opposite side of the SAME market is netting on the
same book -- it reduces the position, it does not create a second settling
holding. So a second leg has to be a DIFFERENT contract on the SAME fixture,
graded against the same outcome variable. `bettor_indirect_structures` is what
establishes that the two are graded against one variable at all, and it refuses
rather than guesses when the venue's settlement text was not captured.

NO FUNDED ORDER IS SENT FROM HERE. `bettor_funded_execution` keeps its own
switch, `FUNDED_SUBMISSION_ENABLED`, and it is off in the shipped code. This
module's acquisition step reaches that switch and stops there, with the adapter
uncalled. Turning it on is a code change with the owner's authority behind it.
"""

from __future__ import annotations

import time
from typing import Any

from . import bettor_funded_book as FB
from . import bettor_funded_decision as FD
from . import bettor_funded_execution as FX
from . import bettor_funded_hedge_supply as HS
from . import bettor_funded_indirect_pair as FIP
from . import bettor_funded_learning as FL
from . import bettor_funded_model as FMD
from . import bettor_funded_reservations as RSV
from . import bettor_indirect_structures as IS

VERSION = "FUNDED_PAIR_CYCLE_V1"

#: ── REFUSALS, EACH NAMING ONE MISSING THING ─────────────────────────
R_NO_SCHEMA = "THIS_DATABASE_HAS_NO_PAIR_SCHEMA"
R_NO_HELD_POSITION = "THE_LANE_HOLDS_NOTHING_TO_PAIR"
R_NO_SECOND_CONTRACT = "NO_SECOND_SETTLEMENT_COMPATIBLE_CONTRACT_WAS_DISCOVERED"
R_NOT_DISTINCT = "THE_TWO_CONTRACTS_ARE_THE_SAME_INSTRUMENT"
R_NOT_SETTLEMENT_COMPATIBLE = "THE_TWO_CONTRACTS_ARE_NOT_GRADED_BY_ONE_VARIABLE"
R_NO_GROUP = "THE_HELD_POSITION_BELONGS_TO_NO_PORTFOLIO_GROUP"
R_DECISION_IS_NOT_ACQUIRE = "THE_DECISION_WAS_NOT_TO_ACQUIRE_A_SECOND_LEG"
R_RESERVATION_REFUSED = "THE_LEG_COULD_NOT_BE_RESERVED"

#: The action names this module hands to the learning ledger. They are
#: `bettor_funded_learning.ACTIONS`, not a second vocabulary: two spellings of
#: one decision is how a ledger stops being able to group its own rows.
ACTION_ACQUIRE = "ACQUIRE_HEDGE"
ACTION_NOTHING_RANKABLE = "NO_ACTION_WAS_RANKABLE"


def describe() -> dict:
    return {
        "version": VERSION,
        "what_this_is": ("the scheduled pass that connects reservation, "
                         "decision, acquisition and learning. It is the "
                         "production importer those modules did not have"),
        "imports_in_production": [
            "bettor_funded_reservations", "bettor_funded_decision",
            "bettor_funded_learning", "bettor_funded_indirect_pair",
            "bettor_indirect_structures", "bettor_funded_execution",
            "bettor_funded_book"],
        "decides_nothing_itself": ("no price, no size, no worst case and no "
                                   "ranking is computed here"),
        "recovery_runs_first": ("an AMBIGUOUS reservation still claims its leg, "
                               "so the claim itself refuses a second "
                               "acquisition until the venue's answer is read"),
        "never_resends": ("an ambiguous send is resolved by asking the venue, "
                          "never by sending again"),
        "submission_switch": ("bettor_funded_execution.FUNDED_SUBMISSION_ENABLED"
                              " = %r" % FX.FUNDED_SUBMISSION_ENABLED),
    }


# ═════════════════════════════════════════════════════════════════════
# 1 · RECOVERY, FROM EVIDENCE, BEFORE ANYTHING ELSE
# ═════════════════════════════════════════════════════════════════════

async def recover_reservations(conn, *, account_id: str,
                               venue_reader=None, now: float | None = None
                               ) -> dict:
    """RESOLVE EVERY LIVE RESERVATION THAT AN INTERRUPTION LEFT UNRESOLVED.

    `venue_reader`, when supplied, is an async callable given the reservation and
    expected to RECORD its evidence -- it is how the venue's own answer reaches
    `bettor_funded_operation_evidence` on a restart, since the answer to the
    original send was lost with the process that sent it. When it is not
    supplied, this function still resolves whatever evidence is ALREADY recorded,
    and reports the rest as awaiting evidence.

    WHAT IT WILL NOT DO. It will not release a SEND_ATTEMPTED or AMBIGUOUS
    reservation on its own authority, and it will not resubmit. Those two
    refusals are the whole point: the first preserves exposure that may exist,
    the second stops the duplicate.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at,
                           "account_id": account_id,
                           "resolved": [], "awaiting_evidence": [],
                           "pre_send": [], "resubmitted_anything": False}
    try:
        live = await RSV.live(conn, account_id=account_id)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal=R_NO_SCHEMA,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    out["live_count"] = len(live)
    for res in live:
        op = res["operation_id"]
        if res["state"] in (RSV.HELD, RSV.COMMITTED):
            # NOTHING LEFT THIS PROCESS for these two, so there is nothing to
            # ask the venue about. They are reported, not touched: a HELD leg is
            # a live claim this cycle must respect, and releasing it here would
            # be this module inventing a decision.
            out["pre_send"].append(
                {"operation_id": op, "state": res["state"],
                 "why": ("pre-send states need no venue evidence and are not "
                         "resolved by recovery")})
            continue
        if venue_reader is not None:
            try:
                out.setdefault("reads", []).append(
                    {"operation_id": op,
                     "read": await venue_reader(conn, res, at=at)})
            except Exception as exc:                           # noqa: BLE001
                out.setdefault("read_errors", []).append(
                    {"operation_id": op,
                     "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
        if res["state"] == RSV.SEND_ATTEMPTED:
            # SEND_ATTEMPTED AFTER A RESTART IS AMBIGUOUS BY DEFINITION. The
            # process that sent it is gone, so its answer is gone. Saying so is
            # what makes the reservation eligible for evidence-based resolution.
            got = await RSV.mark_ambiguous(
                conn, operation_id=op,
                why=("this reservation was SEND_ATTEMPTED when the process that "
                     "sent it stopped, so whether the venue holds an order is "
                     "unknown to this process"))
            if got.get("ok"):
                res = got.get("reservation") or res
        got = await RSV.resolve_from_the_venue(conn, operation_id=op)
        if got.get("ok"):
            out["resolved"].append(dict(got, operation_id=op))
        else:
            out["awaiting_evidence"].append(
                {"operation_id": op, "state": res.get("state"),
                 "refusal": got.get("refusal"),
                 "exposure": got.get("exposure"),
                 "what_would_resolve_it": got.get("what_would_resolve_it")})
    return dict(out, ok=True,
                why=("every live reservation was either resolved from recorded "
                     "evidence or left claiming its leg. No order was sent"))


# ═════════════════════════════════════════════════════════════════════
# 2 · DISCOVERY: TWO DISTINCT SETTLEMENT-COMPATIBLE CONTRACTS
# ═════════════════════════════════════════════════════════════════════

def discover(*, held_leg, candidate_legs, sport_permits_tie: bool,
             fixture_can_void: bool = True,
             fixture_can_postpone: bool = True) -> dict:
    """CLASSIFY EVERY CANDIDATE AGAINST THE HELD LEG, AND SAY WHY EACH FAILED.

    TWO THINGS ARE CHECKED, and they are different:

      * DISTINCT -- a different condition on the venue. The same condition is
        not a second holding, it is the same holding; and on a one-instrument
        venue the OPPOSITE SIDE of the same market is netting on the same book,
        not a second settling position.
      * SETTLEMENT-COMPATIBLE -- graded against ONE outcome variable, which is
        `Leg.grading_key()`: fixture, period, variable, overtime treatment. Two
        legs differing on any of those are graded against different random
        variables however similar their titles look, and no joint payoff region
        can be proved.

    Then the classifier does the rest, and a structure it calls UNESTABLISHABLE
    is reported as rejected WITH its missing facts -- not silently dropped, and
    never admitted with a floor computed from the regions it could establish.
    """
    out: dict[str, Any] = {"version": VERSION,
                           "held": held_leg.condition_id,
                           "fixture": held_leg.fixture_id,
                           "examined": 0, "admitted": [], "rejected": []}
    want = held_leg.grading_key()
    for leg in candidate_legs:
        out["examined"] += 1
        row: dict[str, Any] = {"condition_id": leg.condition_id}
        # ── NETTING IS A QUESTION ABOUT THE INSTRUMENT, NOT THE SIDE ─
        #
        # `condition_id` is now `slug#SIDE`, so comparing identities alone
        # would let the OPPOSING SIDE of the held instrument through as a
        # distinct candidate -- precisely the error the owner named: "Direct
        # complements on the same netted PMUS instrument must not be presented
        # as an independent liquidity opportunity." The venue carries ONE book
        # per market (run 264: exactly 2.00 rows and 2 intents per slug across
        # all seventeen prefixes), so buying the other side of what you hold
        # nets the position rather than adding a second settling holding.
        #
        # So the identity is side-aware and this comparison deliberately is
        # NOT. `venue_slug_of` strips the side, and the two questions stay
        # separate instead of one key being bent to answer both.
        held_instrument = HS.venue_slug_of(held_leg.condition_id)
        if HS.venue_slug_of(leg.condition_id) == held_instrument:
            same_side = leg.condition_id == held_leg.condition_id
            out["rejected"].append(dict(
                row, refusal=R_NOT_DISTINCT,
                venue_instrument=held_instrument,
                is_the_same_side=same_side,
                why=("this is the contract already held"
                     if same_side else
                     ("this is the OPPOSING SIDE of the held instrument %r. It "
                      "has a different identity and it is the same book: "
                      "acquiring it nets the position rather than hedging it, "
                      "so it is not an independent liquidity opportunity"
                      % held_instrument))))
            continue
        if leg.grading_key() != want:
            out["rejected"].append(dict(
                row, refusal=R_NOT_SETTLEMENT_COMPATIBLE,
                held_grading_key=list(want),
                candidate_grading_key=list(leg.grading_key()),
                why=("fixture, period, outcome variable and overtime treatment "
                     "must all match before one variable grades both legs")))
            continue
        st = IS.classify(held_leg, leg, sport_permits_tie=sport_permits_tie,
                         fixture_can_void=fixture_can_void,
                         fixture_can_postpone=fixture_can_postpone)
        d = st.to_dict()
        if st.taxonomy == IS.UNESTABLISHABLE or st.missing_facts \
                or st.undetermined_regions:
            out["rejected"].append(dict(
                row, refusal=FD.R_STRUCTURE_IS_UNESTABLISHABLE,
                taxonomy=st.taxonomy,
                missing_facts=list(st.missing_facts),
                undetermined_regions=list(st.undetermined_regions),
                why=("the classifier did not establish this structure. A floor "
                     "computed over only the regions it COULD establish would "
                     "be a fabricated hedge")))
            continue
        out["admitted"].append({"condition_id": leg.condition_id,
                                "taxonomy": st.taxonomy,
                                "units": st.units,
                                "min_payout_cents": st.min_payout_cents,
                                "max_payout_cents": st.max_payout_cents,
                                "structure": d, "leg": leg})
    return dict(out, ok=bool(out["admitted"]),
                refusal=None if out["admitted"] else R_NO_SECOND_CONTRACT,
                distinct_settlement_compatible_contracts=(
                    1 + len(out["admitted"])),
                counting=("the held contract plus every admitted candidate. "
                          "Two means one held leg and one acquirable second "
                          "leg, graded by the same variable"))


# ═════════════════════════════════════════════════════════════════════
# 3 · THE DECISION, IN ONE COMPARISON
# ═════════════════════════════════════════════════════════════════════

async def decide_and_record(conn, *, decision_id: str, account_id: str,
                            venue: str, fixture: str, group_id: str | None,
                            hold_ranking: dict, admitted: dict | None,
                            region_probabilities: dict | None = None,
                            evidence_quality: str = FD.EVIDENCE_NOT_ESTABLISHED,
                            limits: dict | None = None,
                            fee_usd=None, depth=None, incremental=None,
                            capital_duration_h=None,
                            holding_policy: str = FL.POLICY_MAY_EXIT_EARLY,
                            filled_qty=None,
                            use_approved_model: bool = False,
                            model_inputs: dict | None = None,
                            now: float | None = None) -> dict:
    """ONE COMPARISON, THEN WRITE IT DOWN BEFORE THE OUTCOME EXISTS.

    `hold_ranking` is `bettor_mgmt_select.rank_with_hold`'s own output, UNCHANGED
    -- so HOLD, DIRECT_EXIT and REDUCE are the ones the deployed selector
    produced, priced by the deployed valuation, and the indirect acquisition
    joins THAT list rather than replacing it. `decide` ranks the whole set on
    expected net value and applies the downside and incremental-capital limits
    BEFORE choosing, so a breaching candidate is never selected.

    THE LEDGER ROW IS WRITTEN WITH THE SCOPE ITS BOUND IS CONDITIONAL ON: the
    action, the filled quantity and the holding policy. A floor computed for
    HOLD-to-settlement says nothing about a position sold on cycle three, and
    filing that divergence as a modelling error would be filing the wrong
    finding.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at,
                           "decision_id": decision_id, "group_id": group_id}
    # ── THE APPROVED MODEL, WHEN THIS LANE IS ASKED TO DECIDE FROM ONE ──
    #
    # `use_approved_model` is what makes the registry load-bearing instead of
    # decorative: the probability comes from the ONE approved version, and the
    # version, the exact feature vector and its sha go onto the decision row so
    # the estimate is falsifiable afterwards.
    #
    # WITH NO APPROVED MODEL THIS REFUSES RATHER THAN FALLS BACK. The lane then
    # has no estimate it is permitted to decide from, `region_probabilities` stays
    # absent, and `bettor_funded_decision` declines the indirect candidate by
    # name. A fallback to an unregistered number would mean the promotion gate
    # governed nothing.
    prediction = None
    if use_approved_model and admitted is not None:
        mi = dict(model_inputs or {})
        prediction = await FMD.predict_for(
            conn, structure=admitted["structure"],
            primary_cost_cents=mi.get("primary_cost_cents"),
            hedge_cost_cents=mi.get("hedge_cost_cents"),
            overtime_included=mi.get("overtime_included"),
            outside_split=mi.get("outside_split"))
        out["prediction"] = prediction
        if not prediction.get("ok"):
            # ── THE LABEL MUST NAME WHICH FAILURE THIS WAS ───────────
            #
            # This said "NOTHING_APPROVED" for every unsuccessful prediction,
            # which is false whenever a model IS approved and could not price
            # this structure. Measured: with a model promoted through the full
            # registry path, `predict_for` returned p_middle = 0.1344 and then
            # refused NO_PROBABILITY_WAS_STATED_FOR_THE_REGIONS_OUTSIDE_THE_
            # MIDDLE -- and the step reported NOTHING_APPROVED, sending a reader
            # to look at an empty registry that was not empty.
            #
            # The two have different owners. An empty registry is closed by
            # promoting a model; an unpriced outside region is closed by a
            # SECOND statement about the fixture that no source supplies, and
            # `predict_for` refuses to invent it by spreading the remainder
            # uniformly. Conflating them hides the harder of the two.
            out["region_probabilities_came_from"] = (
                "NOTHING_APPROVED"
                if prediction.get("refusal") == FMD.R_NO_APPROVED_MODEL
                else "APPROVED_MODEL_COULD_NOT_PRICE_THIS_STRUCTURE:%s"
                     % prediction.get("refusal"))
            out["prediction_refusal"] = prediction.get("refusal")
            out["prediction_why"] = prediction.get("why")
            region_probabilities = None
        else:
            region_probabilities = prediction["region_probabilities"]
            evidence_quality = FD.EVIDENCE_EXTERNAL_LABELLED
            out["region_probabilities_came_from"] = (
                "APPROVED_MODEL:%s" % prediction["model_version"])
    cand = None
    if admitted is not None:
        # ── THE WORST CASE IS COMPUTED AND PASSED IN, NOT LEFT NULL ──
        #
        # `indirect_candidate` takes `worst_case` and reports its
        # `worst_case_usd` as the candidate's `downside_usd`. Omitting it leaves
        # `downside_usd` None -- and a None downside is not a downside of zero,
        # but `decide`'s `max_downside_usd` constraint has nothing to compare, so
        # the approved downside limit would never bite on the one action it
        # exists to bound. The floor comes from `bettor_funded_indirect_pair`,
        # which refuses it outright for a structure the classifier did not
        # establish.
        out["worst_case"] = FIP.net_worst_case(
            admitted["structure"], fee_usd=fee_usd,
            fee_basis=(None if fee_usd is None else "SUPPLIED_BY_THE_CALLER"))
        cand = FD.indirect_candidate(
            structure=admitted["structure"],
            region_probabilities=region_probabilities,
            evidence_quality=evidence_quality, fee_usd=fee_usd, depth=depth,
            incremental=incremental, capital_duration_h=capital_duration_h,
            worst_case=out["worst_case"])
    out["indirect_candidate"] = cand
    verdict = FD.decide(hold_ranking=hold_ranking, indirect=cand,
                        limits=limits, capital_duration_h=capital_duration_h)
    out["decision"] = verdict
    # `selected` IS THE ACTION NAME; `selected_candidate` IS THE ROW. Reading
    # `selected` as a dict silently produced no action at all -- caught by the
    # lifecycle run, which raised `'str' object has no attribute 'get'`.
    sel = dict(verdict.get("selected_candidate") or {})
    action = verdict.get("selected") or ACTION_NOTHING_RANKABLE
    if action == FD.ACTION_ACQUIRE_INDIRECT_HEDGE:
        # ONE SPELLING PER DECISION. The ledger's vocabulary is
        # `bettor_funded_learning.ACTIONS`; the decision module names the same
        # action its own way, and the translation happens HERE, once, rather
        # than in every reader.
        action = ACTION_ACQUIRE
    elif action in LEDGER_ACTION_FOR_SELECTION:
        # AND THE EXIT NEEDED THE SAME TRANSLATION, WHICH IT DID NOT HAVE. The
        # selector says DIRECT_EXIT; `bettor_funded_learning.ACTIONS` says EXIT.
        # So `record_decision` refused every exit decision with
        # THAT_IS_NOT_AN_ACTION_THIS_LANE_TAKES -- harmless while `manage`
        # dispatched exits itself and the decision row was decoration, and NOT
        # harmless the moment dispatch required a persisted decision: the new
        # gate correctly refused to send an exit that could not be recorded.
        #
        # The gate found a real gap rather than creating one. One map, here,
        # beside the hedge's translation.
        action = LEDGER_ACTION_FOR_SELECTION[action]
    out["action"] = action
    rec = await FL.record_decision(
        conn, decision_id=decision_id, account_id=account_id, venue=venue,
        fixture=fixture, action=action, decided_at=at, group_id=group_id,
        worst_case_usd=sel.get("downside_usd"),
        ranking={"ranked": verdict.get("candidates") or [],
                 "unrankable": verdict.get("not_rankable") or []},
        inputs_present=tuple(verdict.get("inputs_present") or ()),
        inputs_missing=tuple(
            c.get("blocker") for c in (verdict.get("not_rankable") or [])
            if c.get("blocker")),
        bound_action=action, bound_filled_qty=filled_qty,
        bound_holding_policy=holding_policy,
        # ── WHAT THIS DECISION WAS PREDICTED FROM ────────────────────
        # All NULL when no model was used, and `bettor_funded_model.labelled`
        # then excludes the row from every evaluation: a decision with no
        # recorded vector cannot be scored, and scoring it against whatever the
        # model says today would be scoring the model on its own output.
        model_key=(prediction or {}).get("model_key"),
        model_version=(prediction or {}).get("model_version"),
        features=(prediction or {}).get("features"),
        feature_sha=(prediction or {}).get("feature_sha"),
        predicted=(prediction or {}).get("predicted"))
    out["ledger"] = rec
    return dict(out, ok=bool(rec.get("ok")),
                refusal=None if rec.get("ok") else rec.get("refusal"),
                selected=sel,
                policy=verdict.get("policy"),
                worst_case_is=verdict.get("worst_case_is"))


# ═════════════════════════════════════════════════════════════════════
# 4 · THE ACQUISITION: RESERVE, THEN SUBMIT THROUGH THE RAILS
# ═════════════════════════════════════════════════════════════════════

async def acquire_second_leg(conn, *, operation_id: str, group_id: str,
                             us_market_slug: str, quantity: float,
                             limit_price: float, collateral_usd: float,
                             decision_record: dict, account_id: str,
                             venue: str, adapter=None,
                             venue_positions: dict | None = None,
                             leg_role: str = "HEDGE",
                             now: float | None = None) -> dict:
    """RESERVE THE LEG FIRST, THEN SEND -- and never the other way round.

    THE ORDER IS THE POINT. The reservation is taken BEFORE any intent exists,
    so from the first instant the leg is claimed and the collateral is counted
    as committed capital by `reserved_collateral_usd`, which
    `bettor_funded_execution.check_rails` now reads. A second cycle -- or a
    second process -- reaching for the same leg is refused at the database, not
    at a check that races.

    `submit_for_decision` then does everything else: the schema gate, the venue
    class, the plan, the account, the approved limits, every rail INCLUDING the
    reserved collateral this hold just added, the account-wide exposure, the
    authorization gate, the execution gate and the submission switch. It binds
    the reservation to the intent it creates, moves both rows to send-eligible
    together, and resolves both together whatever the send does.

    IF THE SUBMISSION NEVER REACHES THE VENUE, the reservation is released as
    NEVER SENT -- and only from a pre-send state. A refusal at the switch is
    pre-send by construction: the adapter was not called.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at,
                           "operation_id": operation_id, "group_id": group_id,
                           "resubmitted_anything": False}
    res = await RSV.hold(conn, operation_id=operation_id, group_id=group_id,
                         leg_role=leg_role, us_market_slug=us_market_slug,
                         quantity=quantity, limit_price=limit_price,
                         collateral_usd=collateral_usd)
    out["reservation"] = res
    if not res.get("ok"):
        return dict(out, ok=False, refusal=R_RESERVATION_REFUSED,
                    reservation_refusal=res.get("refusal"),
                    why=res.get("why"), submitted=False,
                    nothing_was_sent=True)
    out["reserved_collateral_usd"] = collateral_usd
    sub = await FX.submit_for_decision(
        conn, decision_record, account_id=account_id, venue=venue,
        adapter=adapter, venue_positions=venue_positions,
        operation_id=operation_id, portfolio_group_id=group_id,
        leg_role=leg_role, now=at)
    out["submission"] = sub
    if sub.get("intent_id") is None and not sub.get("submitted"):
        # NOTHING WAS SENT AND NO INTENT EXISTS, so the reservation is a claim
        # on a leg nobody is acquiring. Releasing it here is the correct
        # direction and it is safe: `release` refuses any state from which the
        # request may have left, so if this were wrong the machine would say so
        # rather than quietly freeing exposure.
        out["reservation_released"] = await RSV.release(
            conn, operation_id=operation_id, why=RSV.WHY_NEVER_SENT)
    return dict(out, ok=bool(sub.get("ok")),
                refusal=sub.get("refusal"), submitted=sub.get("submitted"),
                intent_id=sub.get("intent_id"),
                exposure=sub.get("exposure"))


# ═════════════════════════════════════════════════════════════════════
# 5 · RECONCILIATION: THE GROUP'S ECONOMICS ONCE, THE LEARNING ATTRIBUTED
# ═════════════════════════════════════════════════════════════════════

async def reconcile_and_learn(conn, *, group_id: str,
                              decision_ids=(), correction_reason=None,
                              require_final: bool = True) -> dict:
    """ATTACH THE GROUP'S RESULT TO EVERY DECISION, AND COUNT IT ONCE.

    Both halves matter and they are different questions. "Did this decision's
    claimed floor hold?" is asked PER DECISION, so every decision on the group
    gets the group's realised net attached. "What did the account make?" is asked
    PER GROUP, once -- and summing the decision rows answered it by multiplying
    the same economic result by the number of times the system thought about the
    position.

    `join_realised` writes both: a versioned outcome per decision and ONE row in
    `bettor_funded_group_results`. This function drives it for each decision and
    reports the group's result from the single-row side.
    """
    out: dict[str, Any] = {"version": VERSION, "group_id": group_id,
                           "joined": [], "not_joined": []}
    for did in decision_ids:
        got = await FL.join_realised(conn, decision_id=did,
                                     correction_reason=correction_reason)
        (out["joined"] if got.get("ok") else out["not_joined"]).append(
            dict(got, decision_id=did))
    out["group_result"] = await FL.realised_from_the_book(conn,
                                                         group_id=group_id)
    row = await conn.fetchrow(
        "SELECT realised_net_usd, realised_basis, is_final, "
        "       provisional_events, version "
        "  FROM bettor_funded_group_results WHERE group_id = $1", group_id)
    out["group_results_row"] = dict(row) if row else None
    out["portfolio_pnl_is_summed_from"] = "bettor_funded_group_results"
    out["why_not_the_decision_ledger"] = (
        "a position evaluated on five cycles has five decisions and one "
        "economic result. Summing the decisions multiplied it by five")
    return dict(out, ok=not out["not_joined"])


# ═════════════════════════════════════════════════════════════════════
# 6 · WHAT AN OPERATOR HAS TO SEE
# ═════════════════════════════════════════════════════════════════════

#: Every entry here is something a person must act on, and none of them appears
#: in a total. The panel this feeds already learned once that showing only
#: aggregates hid four different actionable states.
RISK_AMBIGUOUS_SEND = "AN_ACQUISITION_MAY_EXIST_AT_THE_VENUE_AND_WE_CANNOT_TELL"
RISK_CAPITAL_CLAIMED = "CAPITAL_IS_CLAIMED_BY_A_RESERVATION_THAT_HAS_NOT_SENT"
RISK_UNPAIRED = "A_GROUP_HOLDS_ONE_LEG_AND_NO_SECOND_LEG"
RISK_BOUND_NOT_HELD = "A_DECISIONS_CLAIMED_WORST_CASE_DID_NOT_HOLD"
RISK_BOUND_INVALIDATED = "A_CLAIMED_BOUNDS_SCOPE_WAS_INVALIDATED_LATER"


async def operator_view(conn, *, account_id: str | None = None,
                        limit: int = 50) -> dict:
    """THE ACTUAL DECISION AND THE OUTSTANDING RISKS, for the command centre.

    TWO THINGS, AND THEY ARE NOT THE SAME THING. The DECISION is what the lane
    chose and why -- read from the ledger row it wrote before the outcome
    existed, so it cannot be a re-derivation that a changed book would answer
    differently. The RISKS are states a person must act on, listed individually:
    an acquisition that may be live at the venue, capital claimed by a
    reservation that has not sent, a group holding one leg of a pair, and a
    claimed worst case that did not hold or whose scope was later invalidated.

    NONE OF THEM IS SUMMED, and that is deliberate. Each is a different action.
    """
    out: dict[str, Any] = {"version": VERSION, "account_id": account_id,
                           "risks": [], "last_decision": None,
                           "recent_decisions": [], "reserved": None}
    try:
        present = bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            " WHERE table_schema='public' "
            "   AND table_name='bettor_funded_leg_reservations'"))
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal="THE_PAIR_SCHEMA_COULD_NOT_BE_READ",
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    why=("unreadable is not empty. A panel that shows no "
                         "reservations over a schema it could not query would "
                         "report safety it has not established"))
    out["pair_schema"] = "PRESENT" if present else "ABSENT"
    if not present:
        return dict(out, ok=True, refusal=R_NO_SCHEMA,
                    why=("this database carries no reservation or decision "
                         "tables, so there is nothing of this lane to show. "
                         "That is a complete answer, not an outage"))
    # ── RESERVED CAPITAL, THE SAME READING THE RAILS ENFORCE ─────────
    out["reserved"] = await RSV.reserved_collateral_usd(conn,
                                                        account_id=account_id)
    # ── AND WHICH MODEL IS DECIDING ──────────────────────────────────
    #
    # AN OPERATOR CANNOT READ A DECISION WITHOUT IT. "Acquire the hedge" means
    # something different under a model approved this morning than under the one
    # it replaced, and a panel that shows the action without the version leaves
    # a reader unable to tell which. `NO_APPROVED_MODEL` is reported as what it
    # is -- the state in which this lane declines every indirect acquisition --
    # rather than omitted.
    try:
        appr = await FMD.approved(conn)
        out["deciding_model"] = (
            {"model_key": appr["model"]["model_key"],
             "model_version": appr["model"]["model_version"],
             "estimator": appr["model"]["estimator"],
             "approved_by": appr["model"]["approved_by"],
             "approved_at": appr["model"]["approved_at"]}
            if appr.get("ok") else
            {"refusal": appr.get("refusal"),
             "consequence": ("with no approved model this lane has no estimate "
                             "it may decide from, so every indirect "
                             "acquisition is declined for want of region "
                             "probabilities")})
    except Exception as exc:                                   # noqa: BLE001
        out["deciding_model"] = {
            "refusal": "THE_MODEL_REGISTRY_COULD_NOT_BE_READ",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    for res in await RSV.live(conn, account_id=account_id):
        if res["state"] in (RSV.SEND_ATTEMPTED, RSV.AMBIGUOUS):
            out["risks"].append({
                "risk": RISK_AMBIGUOUS_SEND, "operation_id": res["operation_id"],
                "state": res["state"], "group_id": res["group_id"],
                "us_market_slug": res["us_market_slug"],
                "collateral_usd": res["collateral_usd"],
                "intent_id": res.get("intent_id"),
                "what_resolves_it": (
                    "a recorded read of the venue for this account and "
                    "instrument that covers TERMINAL orders. Until then the "
                    "leg stays claimed and is not acquired again")})
        else:
            out["risks"].append({
                "risk": RISK_CAPITAL_CLAIMED, "operation_id": res["operation_id"],
                "state": res["state"], "group_id": res["group_id"],
                "collateral_usd": res["collateral_usd"],
                "counted_as_committed_capital": (
                    res["state"] in RSV.STATES_THAT_COUNT_AS_COMMITTED_CAPITAL)})
    # ── UNPAIRED INVENTORY, BY QUANTITY AND NOT BY LEG COUNT ────────
    #
    # THE DEFECT THIS REPLACES, FOUND BY READING THE LIFECYCLE'S OWN TRACE. This
    # asked `HAVING count(intent_id) = 1`, so a group whose hedge leg filled SIX
    # of the TEN units the structure was valued on counted as paired and produced
    # no risk at all -- while four contracts sat naked and the claimed floor
    # described a position that no longer existed. The lifecycle run printed
    # `risk_count: 0` at exactly that moment, which is how this was caught.
    #
    # THE MEASURE IS THE MATCHED QUANTITY: filled PRIMARY minus filled HEDGE. A
    # single-leg group is the same risk with the hedge at zero, so the two
    # collapse into one entry rather than two spellings of one problem.
    try:
        unpaired = await conn.fetch(
            "SELECT g.group_id, g.event_key, g.structure, g.hedge_intent, "
            "       count(DISTINCT i.intent_id) AS legs, "
            "       coalesce(sum(q.filled) FILTER "
            "         (WHERE i.leg_role = 'PRIMARY'), 0)::float8 AS primary_qty,"
            "       coalesce(sum(q.filled) FILTER "
            "         (WHERE i.leg_role = 'HEDGE'), 0)::float8 AS hedge_qty "
            "  FROM bettor_funded_portfolio_groups g "
            "  JOIN bettor_funded_intents i "
            "    ON i.portfolio_group_id = g.group_id AND i.kind = 'ENTRY' "
            "  LEFT JOIN LATERAL ("
            "    SELECT coalesce(sum(f.qty) FILTER "
            "             (WHERE f.direction = 'ENTRY'), 0) AS filled "
            "      FROM bettor_funded_fills f WHERE f.intent_id = i.intent_id"
            "  ) q ON TRUE "
            " WHERE g.closed_at IS NULL "
            + ("  AND g.account_id = $1 " if account_id else "")
            + " GROUP BY g.group_id, g.event_key, g.structure, g.hedge_intent",
            *([account_id] if account_id else []))
    except Exception as exc:                                   # noqa: BLE001
        unpaired = []
        out["unpaired_read_error"] = "%s: %s" % (type(exc).__name__,
                                                 str(exc)[:200])
    for row in unpaired:
        naked = round(float(row["primary_qty"]) - float(row["hedge_qty"]), 6)
        if naked <= 0:
            continue
        out["risks"].append({
            "risk": RISK_UNPAIRED, "group_id": row["group_id"],
            "fixture": row["event_key"], "legs": int(row["legs"]),
            "structure": row["structure"], "hedge_intent": row["hedge_intent"],
            "primary_filled_qty": float(row["primary_qty"]),
            "hedge_filled_qty": float(row["hedge_qty"]),
            "unpaired_qty": naked,
            "what_it_means": (
                "%s of %s primary contract(s) carry no second leg. The claimed "
                "floor was computed over the MATCHED units, so it does not "
                "describe the naked part at all"
                % (naked, row["primary_qty"]))})
    # ── THE DECISIONS, AND WHETHER THEIR CLAIMED BOUND HELD ─────────
    try:
        rows = await conn.fetch(
            "SELECT * FROM bettor_funded_decisions "
            + (" WHERE account_id = $1 " if account_id else "")
            + " ORDER BY decided_at DESC LIMIT %d" % int(limit),
            *([account_id] if account_id else []))
    except Exception:                                          # noqa: BLE001
        rows = []
    scopes: dict = {}
    for r in rows:
        dec = FL._row(r)
        # ── THE OBSERVED SCOPE, OR NO SCOPE CLAIM AT ALL ─────────────
        #
        # `check_the_worst_case` guards every scope comparison on the observed
        # field being present, so calling it without one can never report a
        # divergence -- the panel would show a bound as HELD on a position whose
        # quantity or holding policy had changed. Read once per group.
        gid = dec.get("group_id")
        if gid and gid not in scopes:
            try:
                scopes[gid] = await FL.observed_scope(conn, group_id=gid)
            except Exception as exc:                            # noqa: BLE001
                scopes[gid] = {"ok": False, "error": "%s: %s" % (
                    type(exc).__name__, str(exc)[:200])}
        obs = scopes.get(gid) or {}
        chk = FL.check_the_worst_case(
            dec, observed=({k: obs[k] for k in ("filled_qty", "exited_early")}
                           if obs.get("ok") else None))
        item = {"decision_id": dec["decision_id"], "action": dec["action"],
                "group_id": dec.get("group_id"),
                "fixture": dec.get("fixture"),
                "worst_case_usd": dec.get("worst_case_usd"),
                "realised_net_usd": dec.get("realised_net_usd"),
                "bound_check": chk.get("worst_case_check"),
                "observed_scope": {k: obs.get(k) for k in
                                   ("ok", "filled_qty", "exited_early")},
                "bound_scope": {"action": dec.get("bound_action"),
                                "filled_qty": dec.get("bound_filled_qty"),
                                "holding_policy": dec.get(
                                    "bound_holding_policy")},
                "inputs_missing": list(dec.get("inputs_missing") or ())}
        out["recent_decisions"].append(item)
        if chk.get("worst_case_check") == FL.CHECK_VIOLATED:
            out["risks"].append(dict(item, risk=RISK_BOUND_NOT_HELD,
                                     why=chk.get("why")))
        elif chk.get("worst_case_check") == FL.CHECK_INVALIDATED:
            out["risks"].append(dict(item, risk=RISK_BOUND_INVALIDATED,
                                     why=chk.get("why"),
                                     and_it_is_not_a_modelling_error=(
                                         "the position changed after the bound "
                                         "was claimed, so the comparison is "
                                         "void -- not evidence the settlement "
                                         "model was wrong")))
    out["last_decision"] = (out["recent_decisions"] or [None])[0]
    return dict(out, ok=True, risk_count=len(out["risks"]),
                risks_are_not_summed=(
                    "each entry is a different action for a person to take. A "
                    "count is given so none is missed, and no total spans them"),
                decision_is_read_not_recomputed=(
                    "from the ledger row written before the outcome existed. A "
                    "fresh evaluation against today's book answers a different "
                    "question and would not explain the state the book is in"))


# ═════════════════════════════════════════════════════════════════════
# THE PASS
# ═════════════════════════════════════════════════════════════════════

ACTION_DIRECT_EXIT = "DIRECT_EXIT"
ACTION_REDUCE = "REDUCE"
ACTION_HOLD = "HOLD"

#: ── TWO VOCABULARIES, ONE TRANSLATION, DECLARED ONCE ─────────────────
#:
#: `bettor_mgmt_select` and `bettor_funded_decision` say DIRECT_EXIT. The
#: decision ledger (`bettor_funded_learning.ACTIONS`) says EXIT. The hedge
#: already had its translation in `decide_and_record`; the exit did not, so
#: every exit decision was refused by the ledger as an unrecognised action.
#: That was invisible while exits were dispatched by `manage` and the decision
#: row was decoration, and it became a refusal the instant dispatch required a
#: persisted decision.
LEDGER_ACTION_FOR_SELECTION = {ACTION_DIRECT_EXIT: "EXIT"}
SELECTION_ACTION_FOR_LEDGER = {v: k for k, v
                               in LEDGER_ACTION_FOR_SELECTION.items()}
#: The ledger spelling of every action this pass can dispatch as an exit-type
#: order. `REDUCE` is spelled the same in both vocabularies.
LEDGER_EXIT_ACTIONS = ("EXIT", ACTION_REDUCE)

#: Actions this pass can DISPATCH, and what dispatching each one means.
#:
#: IT USED TO BE ONE. `pass_once` dispatched only the acquisition; a selected
#: DIRECT_EXIT or REDUCE was recorded and left for `manage` to act on
#: independently -- which is how two managers came to choose separately. Now the
#: exit and the reduction are dispatched from HERE, through the selection
#: `manage` deferred, so exactly one component sends exactly one action.
#:
#: HOLD DISPATCHES NOTHING, deliberately and not as an omission: holding is the
#: absence of an order, and a HOLD that sent something would be a different
#: action.
DISPATCHABLE = {
    "ACQUIRE_HEDGE": "submit the second leg through acquire_second_leg",
    ACTION_DIRECT_EXIT: ("send the deferred exit through "
                         "bettor_funded_management.dispatch_selection"),
    ACTION_REDUCE: ("send the deferred exit at the reduced quantity, through "
                    "the same dispatcher"),
    ACTION_HOLD: "send NOTHING; holding is the absence of an order",
}

R_NO_DEFERRED_EXIT_TO_DISPATCH = (
    "THE_RANKING_SELECTED_AN_EXIT_AND_NO_DEFERRED_SELECTION_WAS_SUPPLIED")
R_DECISION_NOT_PERSISTED = "THE_DECISION_DID_NOT_PERSIST_SO_NOTHING_IS_SENT"

#: Every field that must agree between the ranking's winning candidate and the
#: order about to be sent. Named rather than compared ad hoc, so adding a field
#: to the plan cannot silently escape the binding.
BOUND_FIELDS = ("action", "quantity", "limit_price", "proceeds_per_contract",
                "us_market_slug", "inputs_expire_at")
R_ORDER_DOES_NOT_MATCH_THE_DECISION = (
    "THE_ORDER_DOES_NOT_MATCH_THE_CANDIDATE_THE_RANKING_SELECTED")


#: ── THE EXECUTABLE PLAN, COMPLETE AND IMMUTABLE BEFORE RANKING ───────
#:
#: WHAT `bind_selection_to_candidate` COULD NOT DO, AND WHY IT IS REPLACED.
#: Codex, on 05fa15f:
#:
#:   * it returned ok=True when the candidate lacked `limit_price`,
#:     `proceeds_per_contract` or `inputs_expire_at` -- recording them "unbound"
#:     and proceeding, so an order with no wire limit passed the binding;
#:   * a malformed numeric became None through `_num` and then compared equal to
#:     another None, so garbage passed too;
#:   * it IGNORED the candidate's own `us_market_slug`, comparing the position's
#:     against the selection's with a fallback to the position itself -- so a
#:     candidate for market B passed against an order for market A;
#:   * it never checked position or intent identity at all.
#:
#: Every one of those is the same root error: comparing two partial records
#: FIELD BY FIELD and treating an absence as agreement. A comparison cannot
#: establish completeness.
#:
#: SO THE PLAN IS BUILT COMPLETE, ONCE, BEFORE RANKING. `ExecutionPlan` is
#: frozen: an action cannot be ranked unless a plan for it validated, and the
#: plan the winner is bound to is the same object that was ranked -- not a
#: lookup by position id reconciled afterwards. A missing or malformed required
#: field refuses at CONSTRUCTION, which is before the candidate exists, which is
#: before anything can be dispatched.
PLAN_REQUIRED = ("account_id", "venue", "intent_id", "us_market_slug",
                 "action", "quantity", "limit_price",
                 "proceeds_per_contract", "inputs_expire_at")

R_PLAN_INCOMPLETE = "THE_EXECUTION_PLAN_IS_MISSING_A_REQUIRED_FIELD"
R_PLAN_MALFORMED = "AN_EXECUTION_PLAN_FIELD_IS_NOT_A_USABLE_VALUE"
R_PLAN_EVIDENCE_EXPIRED = "THE_EXECUTION_PLAN_EVIDENCE_HAS_ALREADY_EXPIRED"
R_PLAN_NOT_THE_RANKED_ONE = "THE_PLAN_IS_NOT_THE_ONE_THAT_WAS_RANKED"
R_PLAN_IDENTITY = "THE_EXECUTION_PLAN_NAMES_A_DIFFERENT_POSITION_OR_INSTRUMENT"


class PlanRefused(Exception):
    """Raised by `plan_for` with a named refusal. Never escapes `pass_once`."""

    def __init__(self, refusal, why, field=None, value=None):
        super().__init__(why)
        self.refusal = refusal
        self.why = why
        self.field = field
        self.value = value

    def as_dict(self) -> dict:
        return {"ok": False, "refusal": self.refusal, "why": self.why,
                "field": self.field, "value": self.value}


def _positive(field, value):
    """A number that is present, finite and greater than zero, or refuse.

    `_num` returned None for a malformed value and None compared equal to
    another None, so "0.4x" and a missing field agreed with each other. A
    quantity, a price and a proceeds figure are each meaningless at zero or
    below, and an order carrying one is not an order.
    """
    import math

    if value is None:
        raise PlanRefused(R_PLAN_INCOMPLETE,
                          "%s is required and was not supplied" % field, field)
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise PlanRefused(R_PLAN_MALFORMED,
                          "%s is %r, which is not a number" % (field, value),
                          field, value)
    if not math.isfinite(v) or v <= 0:
        raise PlanRefused(R_PLAN_MALFORMED,
                          "%s is %r; a quantity, price or proceeds figure must "
                          "be finite and above zero" % (field, value),
                          field, value)
    return round(v, 6)


def _text(field, value):
    if value is None or not str(value).strip():
        raise PlanRefused(R_PLAN_INCOMPLETE,
                          "%s is required and was not supplied" % field, field)
    return str(value).strip()


class ExecutionPlan:
    """ONE ACTION'S COMPLETE ORDER. Immutable once built.

    It carries the whole identity of the thing to be done -- account, venue,
    position/intent, instrument, action, quantity, wire limit, valuation basis
    and evidence validity -- because a dispatcher that has to look any of them up
    is a dispatcher that can look up the wrong one.

    `digest` is what binds it to the decision: the persisted winner records the
    digest of the plan it was ranked with, and the dispatcher sends the plan
    whose digest matches. A substituted plan changes the digest and refuses.
    """

    __slots__ = ("account_id", "venue", "intent_id", "us_market_slug", "action",
                 "quantity", "limit_price", "proceeds_per_contract",
                 "inputs_expire_at", "assessed_at", "source", "_digest")

    def __init__(self, **kw):
        object.__setattr__ if False else None
        self.account_id = _text("account_id", kw.get("account_id"))
        self.venue = _text("venue", kw.get("venue"))
        self.intent_id = _text("intent_id", kw.get("intent_id"))
        self.us_market_slug = _text("us_market_slug", kw.get("us_market_slug"))
        self.action = _text("action", kw.get("action"))
        self.quantity = _positive("quantity", kw.get("quantity"))
        self.limit_price = _positive("limit_price", kw.get("limit_price"))
        self.proceeds_per_contract = _positive(
            "proceeds_per_contract", kw.get("proceeds_per_contract"))
        self.inputs_expire_at = _positive("inputs_expire_at",
                                          kw.get("inputs_expire_at"))
        self.assessed_at = kw.get("assessed_at")
        self.source = str(kw.get("source") or "")
        self._digest = self._compute_digest()

    def __setattr__(self, name, value):
        # IMMUTABLE AFTER CONSTRUCTION. A plan that can be edited between the
        # ranking and the send is not a binding, and the digest would no longer
        # describe what is about to be dispatched.
        if getattr(self, "_digest", None) is not None:
            raise AttributeError(
                "an ExecutionPlan is immutable once built; %s cannot be set "
                "after the plan was ranked" % name)
        object.__setattr__(self, name, value)

    def _compute_digest(self) -> str:
        import hashlib

        payload = "|".join(str(getattr(self, f)) for f in PLAN_REQUIRED)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    @property
    def digest(self) -> str:
        return self._digest

    def as_dict(self) -> dict:
        out = {f: getattr(self, f) for f in PLAN_REQUIRED}
        out["digest"] = self._digest
        out["assessed_at"] = self.assessed_at
        out["source"] = self.source
        return out

    def check_not_expired(self, now) -> dict:
        """Has this plan's evidence already expired? An independent check.

        COMPARING two expiry values only establishes that two records agree
        about when the evidence dies -- which they can do while it is already
        dead. The clock is asked separately.
        """
        at = float(now)
        remaining = round(self.inputs_expire_at - at, 3)
        return {"expired": remaining <= 0, "now": at,
                "inputs_expire_at": self.inputs_expire_at,
                "remaining_s": remaining,
                "why": ("agreement about an expiry instant is not evidence that "
                        "the instant has not passed; the clock is asked")}


def plan_for(*, action, selection, account_id, venue, position, now=None):
    """Build one action's plan, or raise `PlanRefused` naming the field.

    `action` is the SELECTOR's spelling (DIRECT_EXIT / REDUCE), because that is
    what the plan executes; the ledger's spelling is applied to the decision,
    not to the order.
    """
    sel = dict(selection or {})
    pos = dict(position or {})
    # THE INSTRUMENT COMES FROM THE POSITION, which is the authority on what is
    # held -- and the selection's own slug, where it carries one, must AGREE
    # rather than override. A selection naming another market is a different
    # order, not a correction.
    pos_slug = _text("us_market_slug", pos.get("us_market_slug"))
    sel_slug = sel.get("us_market_slug")
    if sel_slug is not None and str(sel_slug).strip() != pos_slug:
        raise PlanRefused(
            R_PLAN_IDENTITY,
            "the selection names instrument %r and the position holds %r"
            % (str(sel_slug).strip(), pos_slug), "us_market_slug", sel_slug)
    sel_intent = sel.get("intent_id")
    pos_intent = _text("intent_id", pos.get("intent_id"))
    if sel_intent is not None and str(sel_intent).strip() != pos_intent:
        raise PlanRefused(
            R_PLAN_IDENTITY,
            "the selection names position %r and this position is %r"
            % (str(sel_intent).strip(), pos_intent), "intent_id", sel_intent)
    return ExecutionPlan(
        account_id=account_id, venue=venue, intent_id=pos_intent,
        us_market_slug=pos_slug, action=action,
        quantity=sel.get("selected_qty"),
        limit_price=sel.get("limit_price"),
        proceeds_per_contract=sel.get("proceeds_per_contract"),
        inputs_expire_at=sel.get("inputs_expire_at"),
        assessed_at=sel.get("assessed_at"),
        source="bettor_funded_management.select_exit (deferred)")


def bind_plan_to_decision(*, plan, candidate, action, now,
                          expected_digest=None) -> dict:
    """Is THIS plan the one the ranking selected, and is it still valid?

    Replaces the field-by-field comparison. The candidate carries the digest of
    the plan it was built from, so binding is an identity check rather than a
    reconciliation of two partial records -- and a substituted plan, however
    well-formed, does not match.
    """
    out: dict = {"ok": False, "refusal": R_PLAN_NOT_THE_RANKED_ONE,
                 "plan": None, "compared": {}}
    if plan is None:
        out["why"] = ("no executable plan exists for the selected action, so "
                      "there is nothing to bind and nothing to send")
        out["refusal"] = R_PLAN_INCOMPLETE
        return out
    out["plan"] = plan.as_dict()
    cand = dict(candidate or {})
    want = str(expected_digest or cand.get("plan_digest") or "")
    out["compared"] = {"plan_digest": plan.digest, "candidate_digest": want,
                       "candidate_action": cand.get("action"),
                       "plan_action": plan.action}
    if not want:
        out["why"] = ("the selected candidate carries no plan digest, so it was "
                      "not built from an executable plan and nothing can be "
                      "bound to it")
        return out
    if want != plan.digest:
        out["why"] = ("the selected candidate was ranked with plan %s and the "
                      "plan about to be sent is %s: a different order"
                      % (want[:12], plan.digest[:12]))
        return out
    # THE ACTION, TOO: the digest covers it, and a mismatch here means the
    # decision's own action disagrees with the plan it carried, which is a
    # defect in the caller rather than a substitution.
    if str(cand.get("action") or "") != plan.action:
        out["why"] = ("the candidate's action %r and its plan's action %r "
                      "disagree" % (cand.get("action"), plan.action))
        return out
    expiry = plan.check_not_expired(now)
    out["expiry"] = expiry
    if expiry["expired"]:
        out.update(refusal=R_PLAN_EVIDENCE_EXPIRED,
                   why=("the plan's evidence expired %.3fs ago. Two records "
                        "agreeing about an expiry instant does not establish "
                        "that the instant has not passed"
                        % -expiry["remaining_s"]))
        return out
    out.update(ok=True, refusal=None,
               why=("the plan about to be sent is the one the ranking selected "
                    "(digest %s) and its evidence is valid for another %.3fs"
                    % (plan.digest[:12], expiry["remaining_s"])))
    return out


def _num(v):
    try:
        return None if v is None else round(float(v), 6)
    except (TypeError, ValueError):
        return None


def bind_selection_to_candidate(*, selection, candidate, action,
                                position=None) -> dict:
    """Is this order the one the ranking selected? Pure; never raises.

    THE DEFECT THIS CLOSES. The exit branch fetched a deferred selection by
    POSITION ID and sent it. A position id says which position an order concerns;
    it says nothing about which ACTION or SIZE was decided. Codex supplied a
    ranking selecting REDUCE for 2 contracts against a deferred selection
    holding DIRECT_EXIT for 10, and the ten-contract exit was sent while the
    pass reported REDUCE.

    Every field in `BOUND_FIELDS` is compared. A quantity that differs is a
    different order; an action that differs is a different decision; an expiry
    that differs means the order would be sent on an assessment other than the
    one that was ranked. Any mismatch refuses BEFORE the adapter, naming the
    field, both values and which side each came from -- so the report cannot say
    "dispatched REDUCE" while an exit went out.
    """
    sel = dict(selection or {})
    cand = dict(candidate or {})
    out: dict = {"ok": False, "fields": BOUND_FIELDS, "compared": {},
                 "refusal": R_ORDER_DOES_NOT_MATCH_THE_DECISION}
    if not cand:
        out["why"] = ("the ranking reported no selected candidate, so there is "
                      "nothing for the order to be bound to. An order sent now "
                      "would be bound to nothing")
        return out
    pos = dict(position or {})
    # THE TWO VOCABULARIES ARE RECONCILED BEFORE COMPARISON, not papered over:
    # the ledger's EXIT and the selector's DIRECT_EXIT are the same action, and
    # comparing the raw strings would refuse every legitimate exit while a
    # genuine action mismatch -- REDUCE decided, DIRECT_EXIT in the order --
    # still has to fail.
    want_action = SELECTION_ACTION_FOR_LEDGER.get(str(action or ""),
                                                  str(action or ""))
    got_action = str(sel.get("selected") or "")
    pairs = {
        "action": (want_action, got_action),
        "quantity": (_num(cand.get("qty")), _num(sel.get("selected_qty"))),
        "limit_price": (_num(cand.get("limit_price")),
                        _num(sel.get("limit_price"))),
        "proceeds_per_contract": (_num(cand.get("proceeds_per_contract")),
                                  _num(sel.get("proceeds_per_contract"))),
        "us_market_slug": (str(pos.get("us_market_slug") or ""),
                           str(sel.get("us_market_slug")
                               or pos.get("us_market_slug") or "")),
        "inputs_expire_at": (_num(cand.get("inputs_expire_at")),
                             _num(sel.get("inputs_expire_at"))),
    }
    mismatched = []
    for field in BOUND_FIELDS:
        want, got = pairs[field]
        out["compared"][field] = {"ranking": want, "order": got}
        if want is None and field in ("limit_price", "proceeds_per_contract",
                                      "inputs_expire_at"):
            # THE CANDIDATE DOES NOT CARRY IT, so there is nothing to disagree
            # with. Recorded as unbound rather than silently treated as equal:
            # the operator can see which fields the binding actually covered.
            out["compared"][field]["bound"] = False
            continue
        out["compared"][field]["bound"] = True
        if want != got:
            mismatched.append(field)
    if mismatched:
        out["mismatched"] = mismatched
        out["why"] = ("the order disagrees with the selected candidate on %s. "
                      "%s. A position id is not a binding: it says which "
                      "position an order concerns and nothing about which "
                      "action or size was decided"
                      % (", ".join(mismatched),
                         "; ".join("%s ranking=%r order=%r"
                                   % (f, out["compared"][f]["ranking"],
                                      out["compared"][f]["order"])
                                   for f in mismatched)))
        return out
    out.update(ok=True, refusal=None,
               why=("every bound field agrees between the candidate the "
                    "ranking selected and the order to be sent"))
    return out


async def _decide_or_refuse(fn, conn, **kw):
    """Call `decide_and_record` with its keywords. A seam, not a wrapper.

    It exists so the `try` around the call has a single statement to guard and
    the long keyword list does not have to be re-indented -- which is how a
    keyword gets dropped during a mechanical edit.
    """
    return await fn(conn, **kw)


# ═════════════════════════════════════════════════════════════════════
# RANKING THE ADMITTED CANDIDATES, ON EACH ONE'S OWN NUMBERS
# ═════════════════════════════════════════════════════════════════════
#
# WHAT THIS REPLACES, AND WHY IT COULD NOT BE DONE BEFORE. `discover` admitted
# every settlement-compatible contract and then took `admitted_all[0]` -- the
# first -- recording `hedge_candidates_not_ranked` with the honest reason:
#
#     "ranking them needs a region probability, a fee and a depth reading per
#      contract, none of which is wired on this lane"
#
# That was true while `candidate_legs` was empty and the lane had no per-contract
# reads at all. It is no longer true: `bettor_funded_hedge_supply.
# candidate_legs_for` prices EACH candidate on its OWN ladder and returns that
# contract's own price and displayed depth, and the fee schedule prices each
# acquisition. So the inputs exist and the first-admitted shortcut is now a
# defect rather than a limitation.
#
# ── WHAT IS RANKED ON, AND WHAT IS REFUSED RATHER THAN SCORED ────────
#
# Each candidate is scored on its FEE-ADJUSTED NET WORST CASE over the units the
# structure actually establishes, using `bettor_funded_indirect_pair.
# net_worst_case` -- the same function the decision module uses, not a second
# arithmetic. A candidate is NOT scored, and is reported `not_rankable`, when:
#
#   * its own price was never established (it never reached here: the supplier
#     refuses an unpriced candidate rather than pricing it off another book),
#   * its displayed depth will not support the quantity wanted, or
#   * the fee schedule will not price it.
#
# AN UNREADABLE INPUT IS NOT A ZERO. A candidate missing depth is not "a
# candidate worth nothing"; it is a candidate nobody measured, and scoring it as
# zero would let a measured loser beat it. Both lists travel on the step.
#
# ── WHAT THIS IS NOT ────────────────────────────────────────────────
#
# It is not a claim that the winner is profitable, and it does not choose
# between the hedge and HOLD or the exit -- `bettor_funded_decision.decide`
# still owns that comparison and still requires a region probability for the
# indirect action. This only stops the lane from picking arbitrarily among
# several hedges. A middle is not preferred here for being a middle: the
# taxonomy is carried for the record and the ORDER comes from the numbers.

R_CANDIDATE_DEPTH_NOT_ESTABLISHED = (
    "THIS_CANDIDATES_DISPLAYED_DEPTH_WAS_NOT_ESTABLISHED")
R_CANDIDATE_DEPTH_TOO_THIN = (
    "THIS_CANDIDATES_DISPLAYED_DEPTH_WILL_NOT_SUPPORT_THE_QUANTITY")
R_CANDIDATE_FEE_NOT_PRICED = "THE_FEE_SCHEDULE_WOULD_NOT_PRICE_THIS_CANDIDATE"
R_CANDIDATE_VALUE_NOT_DETERMINED = (
    "THE_CANDIDATES_NET_WORST_CASE_WAS_NOT_DETERMINED")


def rank_admitted(admitted, *, details=None, wanted_qty=None, fee_usd=None,
                  fee_basis=None, shared_depth=None):
    """Order the admitted candidates by fee-adjusted net worst case.

    `admitted`  `discover`'s own admitted list, each entry carrying the
                classified `structure` and the `leg` it was built from.
    `details`   the supplier's per-candidate readings keyed by market slug --
                its own price and its own displayed depth.
    `wanted_qty` how many contracts the acquisition would want.

    Returns {"ranked": [...], "not_rankable": [...], "best": entry|None, ...}.
    Pure and never raises: a ranking that throws on the decision path would take
    down the exit beside it.
    """
    from . import bettor_funded_indirect_pair as IP

    # ── DETAILS ARE KEYED BY THE SIDE-AWARE IDENTITY ─────────────────
    #
    # THE DEFECT THIS FIXES, MEASURED. This was keyed by `market_slug`, and one
    # slug carries two sides with independent books. So both sides of one
    # instrument read the SAME price and the SAME depth, and two ranked rows
    # came out sharing one `condition_id` with different scores -- after which
    # `best_admitted`'s lookup by that id resolved to whichever entry was last
    # in the dict. Run 268 measured the size of the collapse: 16,545 candidates
    # across 1,558 future fixtures, which is every future fixture and exactly
    # half the candidate set.
    #
    # A detail row that carries only a slug is still accepted -- a supplier
    # that predates the identity model, and a persisted decision read back --
    # but it is recorded as SLUG-ONLY so a price that cannot distinguish the
    # sides is not read as this side's own.
    by_id, by_slug_only = {}, {}
    for d in (details or ()):
        if not isinstance(d, dict):
            continue
        cid = d.get("candidate_id")
        if cid:
            by_id[str(cid)] = d
        elif d.get("market_slug"):
            by_slug_only[str(d["market_slug"])] = d
    # ── A SUPPLIER MAY READ DEPTH ONCE FOR THE POSITION ──────────────
    #
    # THE REGRESSION THIS FIXES, AND THE GATE FOUND IT. `pair_inputs`'
    # contract has always allowed a single `depth` reading -- a
    # `depth_supports(...)` result for the position -- rather than one per
    # candidate. Reading only `candidate_leg_details` made every candidate
    # DEPTH_NOT_ESTABLISHED for such a supplier, nothing was rankable, and the
    # acquisition could no longer win a ranking it used to win. One existing
    # test caught exactly that.
    #
    # So a shared reading is accepted and USED, and the fact that it is shared
    # is recorded: it cannot discriminate between candidates, so it bounds what
    # the ranking means rather than being quietly treated as per-contract.
    shared = dict(shared_depth or {}) if isinstance(shared_depth, dict) else {}
    shared_qty = shared.get("available_qty")
    if shared_qty is None and shared.get("ok") and "supportable_qty" in shared:
        shared_qty = shared.get("supportable_qty")
    out = {"ranked": [], "not_rankable": [], "best": None,
           "scored_on": ("fee-adjusted net worst case per candidate, over the "
                         "units the structure establishes"),
           "an_unreadable_input_is_not_a_zero": (
               "a candidate whose depth or fee could not be read is reported "
               "not_rankable, never scored as worthless -- scoring it zero "
               "would let a measured loser beat something nobody measured"),
           "the_taxonomy_does_not_set_the_order": (
               "a middle is not preferred for being a middle; the order comes "
               "from the numbers and the taxonomy is carried for the record"),
           "depth_source": ("per candidate, from each contract's own ladder"
                            if (by_id or by_slug_only) else
                            ("one shared reading for the position, which "
                             "cannot discriminate between candidates"
                             if shared_qty is not None else "none")),
           "identity": ("condition_id is slug#SIDE. The two sides of one venue "
                        "instrument have independent books, so a reading keyed "
                        "by slug alone cannot price either of them")}
    for cand in (admitted or ()):
        cand = dict(cand or {})
        leg = cand.get("leg")
        cid = str(cand.get("condition_id")
                  or getattr(leg, "condition_id", "") or "")
        venue_slug, side = HS.split_identity(cid)
        # THE SIDE-AWARE LOOKUP FIRST, then the slug-only one -- and when the
        # slug-only one answers, say so on the row.
        det = dict(by_id.get(cid) or {})
        price_is_side_aware = bool(det)
        if not det:
            det = dict(by_slug_only.get(venue_slug) or {})
            if det:
                det["price_is_shared_across_both_sides"] = True
        if det.get("depth_qty") is None and shared_qty is not None:
            det["depth_qty"] = shared_qty
            det["depth_is_shared_not_per_contract"] = True
        row = {"condition_id": cid,
               "venue_slug": venue_slug, "side": side,
               "price_is_this_sides_own": price_is_side_aware,
               "taxonomy": cand.get("taxonomy"),
               "units": cand.get("units"),
               "price": det.get("price"), "depth_qty": det.get("depth_qty"),
               "evidence_age_s": det.get("evidence_age_s")}
        # ── DEPTH, WHICH IS DISPLAYED DEPTH AND NOT A QUEUE POSITION ──
        want = wanted_qty if wanted_qty is not None else cand.get("units")
        dep = IP.depth_supports(wanted_qty=want,
                                depth_qty_at_price=det.get("depth_qty"))
        row["depth"] = dep
        if det.get("depth_qty") is None:
            out["not_rankable"].append(
                dict(row, refusal=R_CANDIDATE_DEPTH_NOT_ESTABLISHED,
                     why=("this contract's displayed depth was not read, so "
                          "whether the quantity could be acquired at all is "
                          "unknown")))
            continue
        # ── A PARTIAL DEPTH IS RANKED AT WHAT IT COVERS, NOT REFUSED ──
        #
        # MY OWN BUG FIRST: this read `dep.get("supported")`, which
        # `depth_supports` does not return -- the field is `fully_supported` --
        # so every candidate was refused THIS_CANDIDATES_DISPLAYED_DEPTH_WILL_
        # NOT_SUPPORT_THE_QUANTITY however deep the book was. A missing key read
        # as False, which is the same class of error as treating an absence as
        # agreement.
        #
        # AND THE PARTIAL CASE IS A REAL ANSWER. A book that supports 6 of 10
        # contracts is not "no hedge": it is a hedge over 6, leaving 4 UNCOVERED,
        # and the two quantities have to travel separately because the way this
        # structure fails is a half-filled second leg whose accounting believes
        # the pair is complete. `depth_supports` says so in its own words. So the
        # candidate is ranked at its SUPPORTABLE quantity with the shortfall
        # carried, and only a book supporting nothing is not rankable.
        supportable = float(dep.get("supportable_qty") or 0.0)
        row["covered_qty"] = supportable
        row["uncovered_qty"] = float(dep.get("shortfall_qty") or 0.0)
        row["fully_supported"] = bool(dep.get("fully_supported"))
        if supportable <= 0:
            out["not_rankable"].append(
                dict(row, refusal=R_CANDIDATE_DEPTH_TOO_THIN,
                     why=("the displayed depth %r supports no contracts at all, "
                          "so there is no hedge to rank"
                          % (det.get("depth_qty"),))))
            continue
        # ── THE FEE, WHICH MUST BE PRICED AND NOT ASSUMED ────────────
        if fee_usd is None:
            out["not_rankable"].append(
                dict(row, refusal=R_CANDIDATE_FEE_NOT_PRICED,
                     why=("no fee reading was supplied, and a fee-adjusted "
                          "value computed without a fee is not fee-adjusted")))
            continue
        val = IP.net_worst_case(cand.get("structure"), fee_usd=fee_usd,
                                fee_basis=fee_basis)
        row["net_worst_case"] = val
        # THE KEY IS `worst_case_usd`. I read `net_worst_case_usd`, which the
        # function does not return, so every candidate was refused
        # THE_CANDIDATES_NET_WORST_CASE_WAS_NOT_DETERMINED while the floor was
        # sitting in the payload at -15.7975. Same shape of error as the depth
        # key above: a missing key read as an absent value.
        floor = (val or {}).get("worst_case_usd")
        row["taxonomy_from_valuation"] = (val or {}).get("taxonomy")
        # AND THE TAXONOMY IS RECORDED, NOT REWARDED. On the fixture this was
        # built against -- a moneyline plus an opposing spread, the very
        # combination it would be easiest to assume cannot lose -- the
        # classifier returns INDEPENDENT_OVERLAP and a floor of -$15.80 net of
        # $7.30 in fees. Both-win AND both-lose are reachable. That is the
        # measured answer to "is a moneyline/opposing-spread pair a middle",
        # and it is no.
        row["both_win_and_both_lose_reachable"] = (
            (val or {}).get("taxonomy") == "INDEPENDENT_OVERLAP")
        if floor is None:
            out["not_rankable"].append(
                dict(row, refusal=R_CANDIDATE_VALUE_NOT_DETERMINED,
                     why=((val or {}).get("why")
                          or "the structure's floor was not determined")))
            continue
        # THE FLOOR IS OVER WHAT CAN ACTUALLY BE ACQUIRED. A structure priced
        # over ten contracts when six are available overstates the hedge by the
        # four that would not fill, so the floor is pro-rated to the covered
        # quantity and the fact is recorded rather than folded in silently.
        row["score_usd"] = float(floor)
        if want and supportable < float(want):
            row["score_usd"] = float(floor) * (supportable / float(want))
            row["score_is_prorated"] = (
                "the structure's floor covers %s contracts and only %s can be "
                "acquired, so the score is pro-rated and %s stay UNCOVERED"
                % (want, supportable, row["uncovered_qty"]))
        out["ranked"].append(row)
    # HIGHEST FLOOR FIRST; ties broken by the contract id so the order is
    # deterministic rather than dependent on the catalogue's row order.
    out["ranked"].sort(key=lambda r: (-r["score_usd"], r["condition_id"]))
    if out["ranked"]:
        winner = out["ranked"][0]
        out["best"] = winner
        by_id = {str(c.get("condition_id")): c for c in (admitted or ())}
        out["best_admitted"] = by_id.get(winner["condition_id"])
        out["why"] = ("%d candidate(s) ranked on their own fee-adjusted floor, "
                      "%d not rankable; %s wins at %.4f"
                      % (len(out["ranked"]), len(out["not_rankable"]),
                         winner["condition_id"][:40], winner["score_usd"]))
    else:
        out["why"] = ("no admitted candidate could be ranked: %d had an input "
                      "nobody read" % len(out["not_rankable"]))
    return out


async def pass_once(conn, *, account_id: str, venue: str,
                    pair_inputs=None, adapter=None,
                    venue_positions: dict | None = None,
                    deferred_exits=None,
                    exit_dispatcher=None,
                    venue_reader=None, now: float | None = None) -> dict:
    """ONE SCHEDULED PAIR PASS. Never raises; reports what it did not do.

    `pair_inputs` is a callable the scheduled caller supplies, returning the
    facts a pairing decision needs for one held position: the held leg, the
    candidate legs, the HOLD ranking, the region probabilities and their
    evidence quality, the fee and depth readings and the approved limits.

    IT IS NOT OPTIONAL AND IT IS NOT DEFAULTED. Every one of those is a reading
    of a venue, a fee schedule or an odds source, and this module inventing any
    of them would be manufacturing the inputs of a capital decision. With no
    supplier the pass RECOVERS -- which needs no inputs and is the half that
    protects money -- and then reports that pairing had nothing to work from.

    `deferred_exits` is `manage(defer_dispatch=True)`'s own selections, keyed by
    intent id. THIS IS THE ORDERING REPAIR: without them the exit was chosen and
    SENT by `manage` before this ranking ran, so the comparison this module
    performs could not change what happened. With them, the exit arrives as a
    candidate and the winner of the one ranking is the only thing dispatched.

    `exit_dispatcher` is the callable that sends a selected exit. It defaults to
    `bettor_funded_management.dispatch_selection`, and is injectable so a test
    can count sends without substituting a venue.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at,
                           "account_id": account_id, "venue": venue,
                           "opened_anything": False,
                           "resubmitted_anything": False,
                           "considered": [], "acquisitions": []}
    out["recovery"] = await recover_reservations(
        conn, account_id=account_id, venue_reader=venue_reader, now=at)
    if pair_inputs is None:
        return dict(out, ok=True, paired_anything=False,
                    refusal=R_NO_HELD_POSITION,
                    why=("no pairing-input supplier was configured, so there "
                         "are no read facts to decide from. Recovery ran, "
                         "because it reads only our own rows"))
    try:
        held = await FB.open_entry_positions(conn, account_id=account_id,
                                            venue=venue)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal=R_NO_SCHEMA,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    out["open_entry_positions"] = len(held)
    if not held:
        return dict(out, ok=True, paired_anything=False,
                    refusal=R_NO_HELD_POSITION,
                    why="nothing is held, so there is nothing to pair")
    for pos in held:
        step: dict[str, Any] = {"intent_id": pos.get("intent_id"),
                                "group_id": pos.get("portfolio_group_id")}
        out["considered"].append(step)
        facts = await pair_inputs(conn, pos, at=at)
        if not facts or not facts.get("ok"):
            step["refusal"] = (facts or {}).get("refusal", R_NO_SECOND_CONTRACT)
            step["missing"] = (facts or {}).get("missing")
            continue
        # ── DISCOVERY IS SKIPPED WHEN THERE IS NOTHING TO DISCOVER ──
        #
        # A supplier that cannot read the venue's complementary contracts gives
        # `held_leg=None` and `candidate_legs=[]`. `discover` then crashed on
        # `None.condition_id` and the whole pass returned
        # FUNDED_PAIR_CYCLE_RAISED -- which took the EXIT dispatch down with it,
        # so a lane with no hedge reader could not act on its own held position
        # at all. That is strictly worse than the behaviour being replaced.
        #
        # No hedge candidate is a legitimate state, not an error: the ranking
        # still holds HOLD and the deferred exit, and one of those is still the
        # right action. So discovery runs only when there is a held leg to
        # discover against, and its absence is NAMED.
        if facts.get("held_leg") is None or not facts.get("candidate_legs"):
            found = {"ok": False, "refusal": R_NO_SECOND_CONTRACT,
                     "examined": 0, "rejected": [],
                     "distinct_settlement_compatible_contracts": 0,
                     "admitted": [],
                     "why": ("no second-leg candidate reader supplied a held "
                             "leg or any candidate contracts, so there is no "
                             "hedge to rank. HOLD and the deferred exit are "
                             "still ranked and one of them is still dispatched")}
        else:
            found = discover(
                held_leg=facts["held_leg"],
                candidate_legs=facts["candidate_legs"],
                sport_permits_tie=bool(facts.get("sport_permits_tie")),
                fixture_can_void=bool(facts.get("fixture_can_void", True)),
                fixture_can_postpone=bool(
                    facts.get("fixture_can_postpone", True)))
        step["discovery"] = {k: found.get(k) for k in
                             ("ok", "refusal", "examined", "rejected",
                              "distinct_settlement_compatible_contracts")}
        # ── FIRST ADMITTED, AND THAT IS A STATED LIMITATION ──────────
        #
        # Codex: "Rank eligible candidates rather than selecting the first
        # admitted contract." Correct, and it is not fixed here, because ranking
        # them requires what ranking anything requires -- a region-probability
        # source, a fee reading and a depth reading PER CONTRACT -- and none of
        # those is wired on this lane (`PAIR_INPUT_READINESS` names them).
        # Scoring several contracts on inputs nobody read would be worse than
        # taking one deterministically.
        #
        # So the limitation is recorded on the step rather than left to be
        # inferred from the absence of a ranking, and `discover`'s full admitted
        # list travels with it so a reader can see what was not compared.
        admitted_all = list(found.get("admitted") or [])
        # ── EVERY ELIGIBLE CANDIDATE IS SCORED, NOT THE FIRST ADMITTED ──
        #
        # This used to be `admitted_all[0]`. The supplier now prices EACH
        # candidate on its OWN ladder and returns that contract's own depth, so
        # the inputs the old comment said were missing exist, and taking the
        # first became a defect rather than a stated limitation.
        ranking = rank_admitted(
            admitted_all,
            details=facts.get("candidate_leg_details"),
            wanted_qty=pos.get("residual_qty") or pos.get("filled_qty"),
            fee_usd=facts.get("fee_usd"),
            fee_basis=facts.get("fee_basis"),
            shared_depth=facts.get("depth"))
        step["hedge_candidate_ranking"] = {
            k: ranking[k] for k in ("ranked", "not_rankable", "why",
                                    "scored_on",
                                    "an_unreadable_input_is_not_a_zero",
                                    "the_taxonomy_does_not_set_the_order")}
        best = ranking.get("best_admitted")
        if best is None and admitted_all and not ranking.get("ranked"):
            # NOTHING COULD BE SCORED. Not "no candidate exists" -- candidates
            # were admitted and each lacked an input nobody read. Naming that
            # separately is the difference between a thin board and an
            # unfinished reader.
            step["hedge_candidates_not_ranked"] = {
                "admitted": len(admitted_all),
                "taken": "none",
                "why": ("every admitted candidate was missing a reading it "
                        "would have to be scored on -- see "
                        "hedge_candidate_ranking.not_rankable. Scoring on "
                        "inputs nobody read would be worse than sending "
                        "nothing"),
                "contracts": [c.get("condition_id") for c in admitted_all][:8]}
        step["admitted_contract"] = None if best is None else best["condition_id"]
        gid = pos.get("portfolio_group_id")
        if gid is None and best is not None:
            # A GROUP IS NEEDED TO ACQUIRE A SECOND LEG, not to sell what is
            # already held. This used to refuse before the decision, so a
            # position with no group could not be exited through this pass --
            # which after the ordering change means it could not be exited at
            # all. It refuses only the acquisition now.
            step["refusal"] = R_NO_GROUP
            continue
        # ── A DECISION WRITE THAT RAISES IS THIS POSITION'S PROBLEM ──
        #
        # `pass_once` says "never raises" and did not honour it here: an
        # exception out of `decide_and_record` propagated to `_funded_service`,
        # which turned it into FUNDED_PAIR_CYCLE_RAISED -- taking the whole pass
        # down, including the recovery and every OTHER position's decision,
        # because one position's ledger write failed. An exception is the same
        # ANSWER as a persistence refusal (no durable record, so nothing is
        # sent) and it is contained to the position it happened on.
        try:
            dec = await _decide_or_refuse(decide_and_record,
            conn, decision_id=facts["decision_id"], account_id=account_id,
            venue=venue,
            # THE FIXTURE, FROM WHICHEVER SOURCE HAS IT. The held leg is the
            # richer record and is absent when no second-leg reader is wired,
            # so the position's own event key stands in. Same fixture, different
            # reader -- and without this the pass raised AttributeError and took
            # the EXIT dispatch down with it, leaving a lane with no hedge
            # reader unable to act on its own held position at all.
            fixture=(facts["held_leg"].fixture_id
                     if facts.get("held_leg") is not None
                     else (pos.get("event_key") or pos.get("us_market_slug"))),
            group_id=gid,
            hold_ranking=facts["hold_ranking"], admitted=best,
            region_probabilities=facts.get("region_probabilities"),
            evidence_quality=facts.get("evidence_quality",
                                       FD.EVIDENCE_NOT_ESTABLISHED),
            limits=facts.get("limits"), fee_usd=facts.get("fee_usd"),
            depth=facts.get("depth"), incremental=facts.get("incremental"),
            capital_duration_h=facts.get("capital_duration_h"),
            holding_policy=facts.get("holding_policy",
                                     FL.POLICY_MAY_EXIT_EARLY),
            filled_qty=pos.get("filled_qty"),
            # ── THE APPROVED MODEL, WHEN THE SUPPLIER ASKS FOR IT ────
            #
            # The supplier decides, not this function: a lane with no approved
            # model must be able to run the rest of the pass, and a lane with
            # one must not have its estimate silently replaced by whatever the
            # supplier computed itself. Both are visible in the step's own
            # `region_probabilities_came_from`.
            use_approved_model=bool(facts.get("use_approved_model")),
            model_inputs=facts.get("model_inputs"), now=at)
        except Exception as exc:                               # noqa: BLE001
            step["refusal"] = R_DECISION_NOT_PERSISTED
            step["decision_refusal"] = "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160])
            step["dispatched"] = None
            step["why_nothing_was_sent"] = (
                "the decision write raised (%s), so there is no durable record "
                "for an order to refer to. Contained to this position: the "
                "recovery and every other position's decision still ran"
                % type(exc).__name__)
            continue
        step["decision"] = {k: dec.get(k) for k in
                            ("ok", "action", "refusal", "policy", "selected",
                             "region_probabilities_came_from")}
        # ── DISPATCH THE ONE SELECTED ACTION, WHICHEVER IT IS ───────
        #
        # `pass_once` used to dispatch only the acquisition and record anything
        # else as R_DECISION_IS_NOT_ACQUIRE -- leaving `manage` to act on the
        # exit independently, which is exactly the two-managers defect. All four
        # selectable actions are handled here now, and HOLD sending nothing is
        # one of the four rather than a gap.
        action = dec.get("action")
        step["dispatchable"] = sorted(DISPATCHABLE)
        # ── NO DURABLE DECISION, NO ORDINARY DISPATCH ────────────────
        #
        # THE DEFECT, REPRODUCED BY CODEX. `decide_and_record` can return
        # ok=False while still carrying an action -- a persistence refusal, or a
        # write that raised -- and this branch read `action` and dispatched
        # without ever looking at `ok`. So an order could go to the venue with
        # NO durable record of the decision that authorised it: the one state in
        # which nobody can afterwards say why the position was taken, and the
        # one the decision ledger exists to prevent.
        #
        # An ordinary action now requires a decision that was recorded. The
        # refusal is separate from "the decision was not to acquire", because a
        # failed write and a deliberate hold are opposite situations and only
        # one of them needs an operator.
        #
        # EMERGENCY AND MANDATORY CONTROLS ARE NOT ROUTED THROUGH HERE and are
        # unaffected: `bettor_funded_execution`'s own risk gates and the
        # reconciliation path in `manage` act on their own authority, and this
        # gate is on the ORDINARY ranked-action path only. That separation is
        # stated rather than assumed, so a future emergency route has to be
        # explicit about being one.
        if not dec.get("ok"):
            step["refusal"] = R_DECISION_NOT_PERSISTED
            step["what_was_selected_instead"] = action
            step["decision_refusal"] = dec.get("refusal") or dec.get("error")
            step["dispatched"] = None
            step["why_nothing_was_sent"] = (
                "the decision did not persist (%s), so there is no durable "
                "record for an order to refer to. An action dispatched now "
                "would exist at the venue and nowhere in our own ledger"
                % (dec.get("refusal") or dec.get("error") or "no reason given"))
            continue
        if action in (ACTION_HOLD, None, ACTION_NOTHING_RANKABLE):
            step["dispatched"] = None
            step["refusal"] = R_DECISION_IS_NOT_ACQUIRE
            step["what_was_selected_instead"] = action
            step["why_nothing_was_sent"] = (
                "holding is the absence of an order. The decision is recorded "
                "and no venue call is made, which is the action being taken")
            continue
        if action in LEDGER_EXIT_ACTIONS:
            # THE EXIT `manage` SELECTED AND DID NOT SEND. It is dispatched
            # exactly as selected -- nothing here recomputes a price, a
            # quantity or a proceeds figure, because a dispatcher with its own
            # opinion of the number is the binding defect in another place.
            sel = dict(deferred_exits or {}).get(pos.get("intent_id"))
            if not sel:
                step["refusal"] = R_NO_DEFERRED_EXIT_TO_DISPATCH
                step["what_was_selected_instead"] = action
                step["why_nothing_was_sent"] = (
                    "the ranking selected %s and no deferred selection was "
                    "supplied for this position, so there is no priced, "
                    "bounded order to send. Reconstructing one here would be "
                    "inventing the order the decision was not made on"
                    % (action,))
                continue
            # ── THE SELECTION MUST BE THE WINNING CANDIDATE ──────────
            #
            # THE DEFECT, REPRODUCED BY CODEX. This looked the selection up by
            # POSITION ID and sent it. Given a ranking that selected REDUCE for
            # 2 contracts and a deferred selection holding DIRECT_EXIT for 10,
            # the dispatcher received the ten-contract exit while the pass
            # reported REDUCE -- an order labelled with one action carrying
            # another action's payload, at five times the decided size.
            #
            # A position id is not a binding. Action, instrument, quantity,
            # limit, proceeds basis and evidence expiry are all compared against
            # the candidate the ranking actually selected, BEFORE the adapter is
            # reached, and a mismatch on any of them refuses.
            # ── BIND THE WINNER TO THE PLAN IT WAS RANKED WITH ──────
            #
            # An IDENTITY check on the plan object, not a field-by-field
            # reconciliation of two partial records. The supplier built one
            # complete, immutable plan per rankable action and the candidate
            # carries its digest, so a substituted plan -- however well-formed --
            # does not match, and expiry is checked against the clock rather than
            # by two records agreeing about an instant.
            plans = dict((facts.get("executable_plans_by_action") or {}))
            plan = plans.get(SELECTION_ACTION_FOR_LEDGER.get(action, action))
            bound = bind_plan_to_decision(
                plan=plan, candidate=dec.get("selected"), action=action, now=at)
            step["order_binding"] = bound
            if not bound["ok"]:
                step["refusal"] = bound["refusal"]
                step["what_was_selected_instead"] = action
                step["dispatched"] = None
                step["why_nothing_was_sent"] = bound["why"]
                continue
            # THE ORDER IS THE PLAN. Nothing downstream re-reads the selection.
            sel = dict(sel, selected_qty=plan.quantity,
                       limit_price=plan.limit_price,
                       proceeds_per_contract=plan.proceeds_per_contract,
                       inputs_expire_at=plan.inputs_expire_at,
                       us_market_slug=plan.us_market_slug,
                       intent_id=plan.intent_id,
                       plan_digest=plan.digest)
            dispatcher = exit_dispatcher
            if dispatcher is None:
                from . import bettor_funded_management as _FM
                dispatcher = _FM.dispatch_selection
            sent = await dispatcher(conn, selection=sel, adapter=adapter,
                                    venue=venue, now=at)
            step["dispatched"] = action
            step["exit_dispatch"] = {
                k: sent.get(k) for k in
                ("ok", "submitted", "refusal", "exit_intent_id", "quantity",
                 "limit_price", "why")}
            # BOTH SPELLINGS ON THE RECORD. `action` is the LEDGER's (EXIT),
            # `selection_action` the selector's (DIRECT_EXIT). A reader
            # comparing this row against either module should not have to know
            # the translation exists.
            out.setdefault("exits", []).append(dict(
                sent, action=action,
                selection_action=SELECTION_ACTION_FOR_LEDGER.get(action,
                                                                 action),
                order_binding=bound))
            out["resubmitted_anything"] = bool(
                out["resubmitted_anything"] or sent.get("submitted"))
            continue
        if action != ACTION_ACQUIRE:
            step["refusal"] = R_DECISION_IS_NOT_ACQUIRE
            step["what_was_selected_instead"] = action
            continue
        step["dispatched"] = ACTION_ACQUIRE
        got = await acquire_second_leg(
            conn, operation_id=facts["operation_id"], group_id=gid,
            us_market_slug=facts["hedge_us_market_slug"],
            quantity=facts["hedge_quantity"],
            limit_price=facts["hedge_limit_price"],
            collateral_usd=facts["hedge_collateral_usd"],
            decision_record=facts["hedge_decision_record"],
            account_id=account_id, venue=venue, adapter=adapter,
            venue_positions=venue_positions, now=at)
        step["acquisition"] = {k: got.get(k) for k in
                               ("ok", "refusal", "submitted", "intent_id",
                                "exposure")}
        out["acquisitions"].append(got)
        out["opened_anything"] = bool(out["opened_anything"]
                                      or got.get("submitted"))
    return dict(out, ok=True,
                paired_anything=bool(out["acquisitions"]),
                what_remains_disabled=FX.disablements(),
                downside_view_is_not_the_policy=FIP.VERSION)
