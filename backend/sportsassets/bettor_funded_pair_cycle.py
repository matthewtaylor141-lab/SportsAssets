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
from . import bettor_funded_indirect_pair as FIP
from . import bettor_funded_learning as FL
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
        if leg.condition_id == held_leg.condition_id:
            out["rejected"].append(dict(
                row, refusal=R_NOT_DISTINCT,
                why=("this is the contract already held. On a venue with one "
                     "instrument per market the opposite side of the same book "
                     "is netting, not a second settling holding")))
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
                            region_probabilities: dict | None,
                            evidence_quality: str,
                            limits: dict | None = None,
                            fee_usd=None, depth=None, incremental=None,
                            capital_duration_h=None,
                            holding_policy: str = FL.POLICY_MAY_EXIT_EARLY,
                            filled_qty=None,
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
        bound_holding_policy=holding_policy)
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
    # ── GROUPS HOLDING ONE LEG ──────────────────────────────────────
    try:
        unpaired = await conn.fetch(
            "SELECT g.group_id, g.event_key, g.structure, g.hedge_intent, "
            "       count(i.intent_id) AS legs "
            "  FROM bettor_funded_portfolio_groups g "
            "  JOIN bettor_funded_intents i "
            "    ON i.portfolio_group_id = g.group_id "
            " WHERE g.closed_at IS NULL "
            + ("  AND g.account_id = $1 " if account_id else "")
            + " GROUP BY g.group_id, g.event_key, g.structure, g.hedge_intent "
              " HAVING count(i.intent_id) = 1",
            *([account_id] if account_id else []))
    except Exception:                                          # noqa: BLE001
        unpaired = []
    for row in unpaired:
        out["risks"].append({
            "risk": RISK_UNPAIRED, "group_id": row["group_id"],
            "fixture": row["event_key"], "legs": int(row["legs"]),
            "structure": row["structure"], "hedge_intent": row["hedge_intent"],
            "what_it_means": (
                "the group is open with a single leg. That is the ordinary "
                "state of an unpaired holding and it is listed rather than "
                "flagged as an error -- but it is the capital the one-open-"
                "group bound is being spent on")})
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

async def pass_once(conn, *, account_id: str, venue: str,
                    pair_inputs=None, adapter=None,
                    venue_positions: dict | None = None,
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
        found = discover(
            held_leg=facts["held_leg"], candidate_legs=facts["candidate_legs"],
            sport_permits_tie=bool(facts.get("sport_permits_tie")),
            fixture_can_void=bool(facts.get("fixture_can_void", True)),
            fixture_can_postpone=bool(facts.get("fixture_can_postpone", True)))
        step["discovery"] = {k: found[k] for k in
                             ("ok", "refusal", "examined", "rejected",
                              "distinct_settlement_compatible_contracts")}
        best = (found["admitted"] or [None])[0]
        step["admitted_contract"] = None if best is None else best["condition_id"]
        gid = pos.get("portfolio_group_id")
        if gid is None:
            step["refusal"] = R_NO_GROUP
            continue
        dec = await decide_and_record(
            conn, decision_id=facts["decision_id"], account_id=account_id,
            venue=venue, fixture=facts["held_leg"].fixture_id, group_id=gid,
            hold_ranking=facts["hold_ranking"], admitted=best,
            region_probabilities=facts.get("region_probabilities"),
            evidence_quality=facts.get("evidence_quality",
                                       FD.EVIDENCE_NOT_ESTABLISHED),
            limits=facts.get("limits"), fee_usd=facts.get("fee_usd"),
            depth=facts.get("depth"), incremental=facts.get("incremental"),
            capital_duration_h=facts.get("capital_duration_h"),
            holding_policy=facts.get("holding_policy",
                                     FL.POLICY_MAY_EXIT_EARLY),
            filled_qty=pos.get("filled_qty"), now=at)
        step["decision"] = {k: dec.get(k) for k in
                            ("ok", "action", "refusal", "policy", "selected")}
        if dec.get("action") != ACTION_ACQUIRE:
            step["refusal"] = R_DECISION_IS_NOT_ACQUIRE
            step["what_was_selected_instead"] = dec.get("action")
            continue
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
