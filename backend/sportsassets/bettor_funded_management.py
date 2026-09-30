"""SERVICING THE FUNDED BOOK: exits, settlement, reconciliation, the stop.

WHY THIS IS A SEPARATE MODULE FROM THE ENTRY CONNECTOR, and it is the whole
design decision: SERVICING MUST OUTLIVE ENTRY. The moment the funded lane holds
one contract, the ability to reduce that exposure is more important than the
ability to add to it -- and the switch that stops new exposure
(`bettor_funded_execution.FUNDED_SUBMISSION_ENABLED`) must therefore not stop
an exit, a cancel, or a settlement reconciliation. Two lanes, two switches, and
the servicing one is the one that can be on while the entry one is off.

WHAT WAS MISSING AND WHY IT MATTERED. The funded path ended at entry-fill
ingestion. There was no exit, no settlement, no recurring management, and
consequently:

  * `pnl()` returned a hardcoded realised P&L of 0.0 "because nothing has
    settled". True on the day, and a constant cannot become nonzero -- so the
    FIRST settlement would still have read zero.
  * `check_rails()` measured MAX_DRAWDOWN as the same hardcoded 0.0, which is
    the rail a loss stop IS. A stop that reads a constant never trips.

So the loss stop is not a number in a config file here; it is the drawdown rail
in the owner's effective limit set, measured over
`bettor_funded_economics` by `bettor_funded_book.realised`, and enforced by the
same `check_rails` every entry already passes through. A realised loss past the
approved MAX_DRAWDOWN refuses the next entry, and it refuses it for the
measured reason rather than by a separate mechanism that could disagree.

WHAT THIS REUSES rather than reimplements:
  * `pmus.submit_fok(..., sell=True)` -- THE SAME adapter and the same
    `_exit_intent` derivation the desk's own exits use. There is no second
    order path.
  * `pmus.cancel_order` for an outstanding order that should stop working.
  * `bettor_venue_settlement_probe.probe` for the venue's own outcome, and
    only its REPORTED and VOID readings are treated as authoritative.
  * `bettor_funded_book` for every write: the exit is an intent with
    `kind='EXIT'`, its fills carry `direction='EXIT'`, and closure goes through
    `mark_position_closed` with one of the enumerated evidenced reasons.

WHAT REMAINS DISABLED. `FUNDED_EXIT_SUBMISSION_ENABLED` is False, exactly as
the entry switch is, so no order of any kind leaves this module in this
deployment. Settlement reconciliation and status reconciliation are READS and
stay available -- they move no money and submit nothing.
"""

from __future__ import annotations

import time

from . import bettor_entry_execution as EX
from . import bettor_funded_activation as FA
from . import bettor_funded_book as FB

VERSION = "BETTOR_FUNDED_MANAGEMENT_V1"

#: THE SERVICING SWITCH, and it is DELIBERATELY NOT the entry switch.
#:
#: Turning new exposure off must never strand inventory. When
#: `bettor_funded_execution.FUNDED_SUBMISSION_ENABLED` is False and this is
#: True, the lane can reduce and close what it holds and cannot open anything
#: new -- which is the configuration a pilot winds down in.
FUNDED_EXIT_SUBMISSION_ENABLED = False

#: THE ACTIONS THIS MODULE'S DISPATCH CAN ACTUALLY SEND.
#:
#: `select_exit` has exactly one dispatch branch --
#: `if sel in ("DIRECT_EXIT", "REDUCE")` -- which computes the wire price,
#: reserves inventory and reaches `submit_exit`. Every other selectable
#: action fell straight through it to a `return ok=True, refusal=None`
#: whose note reads "HOLD chosen by a named rule on observed inputs", so a
#: TAKE_COMPLEMENT selection was reported as a SUCCESSFUL servicing pass,
#: mislabelled as HOLD, with no order planned and nothing saying so.
#:
#: TAKE_COMPLEMENT, POST_COMPLEMENT, COMPLETE_PAIR and MERGE appear
#: nowhere else in this module. They are not implemented here, and until
#: their planner, reservation, wire instruction and reconciliation exist
#: they must not be selectable on the funded path. This tuple is passed to
#: `rank_with_hold` so they are ranked, shown and marked ineligible rather
#: than silently dropped, and `R_ACTION_NOT_EXECUTABLE` below catches any
#: that reach the dispatch anyway.
EXECUTABLE_ACTIONS = ("DIRECT_EXIT", "REDUCE")

#: Selected, priced, and not sendable by this module. A REFUSAL, because
#: ok=True on an action that produced no order is how the gap hid.
R_ACTION_NOT_EXECUTABLE = "SELECTED_ACTION_HAS_NO_DISPATCH_IN_THIS_MODULE"

#: Still open management-report requirements, named so the absence is a
#: tracked gap rather than an implicit capability.
UNIMPLEMENTED_ROUTES = {
    "TAKE_COMPLEMENT": ("cross to buy the complement. Needs a planner, an "
                        "inventory reservation, a wire instruction and "
                        "two-leg reconciliation; and on a netting venue "
                        "whether it is a second route at all is "
                        "NOT_ESTABLISHED"),
    "POST_COMPLEMENT": "rest a complement bid. Needs resting-order lifecycle",
    "COMPLETE_PAIR": "needs the complement route first",
    "MERGE": "PMUS_NATIVE_MERGE_AVAILABLE is NOT_IDENTIFIED",
    "FORM_INDIRECT_HEDGE": ("cross-market structure. Planner, reservations, "
                            "execution and reconciliation all absent"),
}

#: Reads. They submit nothing and they stay available whatever the switches say,
#: because a book that cannot be reconciled is worse than one that cannot trade.
SETTLEMENT_RECONCILIATION_IS_A_READ = True

ADAPTER_MODULE = "sportsassets.pmus"

#: Only these two readings of the venue's outcome may CLOSE a funded position.
#: `CONVERGED` is our inference from a price, not a payout the venue reported,
#: and inferring a settlement is how a position gets closed against the wrong
#: side.
AUTHORITATIVE_TERMINAL_READINGS = ("REPORTED_SETTLEMENT", "EXPLICIT_VOID")

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"

R_NO_SUCH_POSITION = "NO_SUCH_OPEN_FUNDED_POSITION"
R_NOTHING_HELD = "THIS_POSITION_HOLDS_NO_RESIDUAL_INVENTORY"
R_OVER_RESIDUAL = "THE_EXIT_IS_LARGER_THAN_THE_INVENTORY_HELD"
R_EXIT_DISABLED = "FUNDED_EXIT_SUBMISSION_IS_DISABLED_IN_CODE"
R_EXIT_PRICE_UNREPRESENTABLE = "THE_EXIT_LIMIT_CANNOT_BE_SENT_WITHOUT_ACCEPTING_LESS"
#: RETIRED. An exit no longer refuses on the ENTRY authorization gate --
#: `bettor_funded_book.check_servicing` is its boundary. Kept as names so a
#: reader grepping for them finds this note rather than nothing, and so a
#: future caller cannot resurrect the coupling by accident.
R_NOT_AUTHORIZED = "RETIRED_AN_EXIT_DOES_NOT_NEED_A_LIVE_SUBMISSION_GRANT"
R_GATE_NOT_AFFIRMATIVE = "RETIRED_SEE_R_NOT_AUTHORIZED"

#: Missing inputs the exit SELECTOR names, each pointing at one thing.
R_NO_PAYOUT_EVENT = "THE_POSITION_DOES_NOT_RECORD_THE_EVENT_IT_PAYS_ON"
R_BOOK_UNREADABLE = "THE_VENUES_EXECUTABLE_BOOK_COULD_NOT_BE_READ"
R_BOOK_NOT_FRESH = "THE_VENUE_BOOK_IS_STALE_OR_ITS_AGE_IS_UNMEASURED"
R_SETTLEMENT_NOT_ESTABLISHED = \
    "THE_SETTLEMENT_RULE_IS_NOT_ESTABLISHED_FOR_A_FUNDED_ACTION"
R_EXIT_WIRE_UNREPRESENTABLE = \
    "THE_SELECTED_LEVEL_CANNOT_BE_SENT_WITHOUT_ACCEPTING_LESS"
R_ASSESSMENT_EXPIRED = "THE_ASSESSMENT_THIS_EXIT_RESTS_ON_HAS_EXPIRED"

R_NO_INPUT_DEADLINE = "NO_INPUT_EXPIRY_WAS_CARRIED_TO_THIS_SUBMISSION"
R_INPUT_EXPIRY_UNMEASURED = "THE_INPUTS_OWN_EXPIRY_COULD_NOT_BE_ESTABLISHED"

#: WHEN THE INPUTS STOP BEING ADMISSIBLE -- computed, never granted.
#:
#: THE DEFECT THIS CLOSES, and it was a whole fresh window handed out for free.
#: The first version set `assessment_expires_at = decision_at + 30` and checked
#: the AGE OF THE ASSESSMENT at submission. Selecting therefore RESTARTED both
#: clocks: a book already 29 s old under a 30 s bound bought another 30 s the
#: moment it was looked at, and an order could go out against an observation
#: 59 s old while every field in the trace read "fresh".
#:
#: An assessment cannot be fresher than the observations it rests on, and
#: selection is not an observation. The deadline is therefore
#:
#:     min(book observation stamp + the book's own bound,
#:         probability observation stamp + the bound its event state permits)
#:
#: -- both taken from the SOURCES' own instants under the SAME policies that
#: admitted them, and neither restarted by anything this module does.
def input_deadline(book_age, hold_value) -> dict:
    """The earliest instant at which either input stops being admissible.

    `book_age` is `bettor_funded_activation.venue_book_age`'s answer, which
    carries the venue's parsed stamp and the bound it was admitted under.
    `hold_value` is `bettor_hold_value.ev_hold`'s, whose `freshness` block
    carries the bookmaker's own observation stamp and the bound the event state
    permits -- 30 s from `bettor_pinnacle_devig.MAX_QUOTE_AGE_S` in play, or the
    declared PRE_MATCH relaxation when one was applied.

    A MISSING PIECE IS NOT A LONG LIFETIME. If either stamp or either bound is
    absent the expiry is UNMEASURED and this says so, because the alternative is
    a deadline invented out of the instant somebody happened to ask.
    """
    b = dict(book_age or {})
    h = dict(hold_value or {})
    fr = dict(h.get("freshness") or {})
    b_at = b.get("parsed_epoch_s")
    b_bound = b.get("bound_s")
    p_at = fr.get("observed_at")
    p_bound = fr.get("bound_s", h.get("age_bound_s"))
    out = {
        "book": {"observed_at": b_at, "bound_s": b_bound,
                 "basis": b.get("basis"),
                 "policy": "bettor_funded_activation.venue_book_age"},
        "probability": {"observed_at": p_at, "bound_s": p_bound,
                        "bound_from_event_state":
                            fr.get("bound_from_event_state"),
                        "relaxation_applied": fr.get("relaxation_applied"),
                        "policy": "bettor_hold_value.bound_for"},
        "rule": ("the earliest expiry of the two observations under their own "
                 "policies. Selection restarts neither clock"),
        "selection_does_not_extend_it": True,
    }
    missing = [n for n, v in (("book.parsed_epoch_s", b_at),
                              ("book.bound_s", b_bound),
                              ("probability.observed_at", p_at),
                              ("probability.bound_s", p_bound))
               if v is None]
    if missing:
        return dict(out, ok=False, refusal=R_INPUT_EXPIRY_UNMEASURED,
                    missing=missing, expires_at=None,
                    why=("%s is absent, so the moment these inputs stop being "
                         "admissible cannot be established. An unknown expiry "
                         "is not a distant one" % ", ".join(missing)))
    book_expires = float(b_at) + float(b_bound)
    prob_expires = float(p_at) + float(p_bound)
    expires_at = min(book_expires, prob_expires)
    return dict(out, ok=True, refusal=None, expires_at=expires_at,
                book_expires_at=book_expires,
                probability_expires_at=prob_expires,
                governed_by=("VENUE_BOOK" if book_expires <= prob_expires
                             else "PROBABILITY"),
                why=("the %s observation expires first, at %.3f"
                     % ("venue book" if book_expires <= prob_expires
                        else "probability", expires_at)))
R_NO_EXIT_SIDE = "THE_SIDE_A_CLOSE_WOULD_CONSUME_PUBLISHES_NO_EXECUTABLE_LEVEL"
R_NO_PROBABILITY = "NO_ELIGIBLE_PROBABILITY_ROW_PRICES_THIS_CONTRACT"
R_HOLD_NOT_PRICED = "EV_HOLD_IS_NOT_IDENTIFIED_SO_NO_ACTION_CAN_BEAT_HOLDING"
R_NO_BASIS = "THE_POSITION_HAS_NO_PER_CONTRACT_BASIS_TO_RANK_AGAINST"
R_NO_ADAPTER = "THE_VENUE_ADAPTER_COULD_NOT_BE_RESOLVED"
R_SETTLEMENT_NOT_AUTHORITATIVE = "THE_VENUE_STATES_NO_AUTHORITATIVE_OUTCOME"
R_NO_EXIT_PRICE = "NO_EXIT_LIMIT_WAS_SUPPLIED_AND_THIS_LANE_INVENTS_NONE"

#: A multi-level REDUCE whose marginal level carries no wire price. The
#: quantity cannot be bounded without under-filling, so nothing is sent.
R_REDUCE_MARGINAL_WIRE_NOT_SUPPLIED = \
    "THE_SALE_LADDER_SUPPLIED_NO_WIRE_PRICE_FOR_THE_MARGINAL_LEVEL"
R_VENUE_GATE_DENIED = "THE_VENUE_BOUNDARY_GATE_DENIED_IT_BEFORE_SENDING"
R_LOST_ACKNOWLEDGEMENT = "THE_REQUEST_LEFT_AND_THE_ANSWER_WAS_LOST"


def _adapter(mod=None):
    if mod is not None:
        return mod
    import importlib
    return importlib.import_module(ADAPTER_MODULE)


def safe_exit_cent(price: float, opened_with: str) -> float | None:
    """ROUND AN EXIT LIMIT IN THE DIRECTION THAT CANNOT ACCEPT LESS.

    The mirror of `bettor_funded_execution.safe_cent`, and it rounds the OTHER
    way for the same reason: on an entry the limit bounds what we PAY, on an
    exit it bounds what we RECEIVE.

      * a long exit (SELL_LONG) receives `wire x qty`, so a higher wire is
        more cash -- CEIL. 0.6301 -> 0.64. We may miss the fill; we cannot be
        paid less than the plan assumed.
      * a short exit (SELL_SHORT) receives `(1 - wire) x qty`, because the
        wire is the CONTRACT price on both sides (the same fact that makes a
        short's collateral `(1 - price) x qty`), so a LOWER wire is more cash
        -- FLOOR. 0.6399 -> 0.63.

    None when the rounded price leaves the tradeable interval or does not
    survive the adapter's `%.2f` formatting unchanged.
    """
    import math

    p = float(price)
    cents = math.floor(p * 100.0) if opened_with == SHORT \
        else math.ceil(p * 100.0)
    out = round(cents / 100.0, 2)
    if not (0.0 < out < 1.0):
        return None
    if float("%.2f" % out) != out:
        return None
    return out


def exit_proceeds(qty: float, wire: float, opened_with: str) -> float:
    """THE CASH AN EXIT RETURNS, in the space the position was taken in.

    Shares `live_executor.fill_cash` through `bettor_funded_book.cash_for`, so
    the exit's arithmetic and the entry's arithmetic are the same function and
    cannot drift into disagreement about a short.
    """
    return FB.cash_for(float(qty), float(wire), opened_with)


# ── 1 · WHAT THE LANE IS HOLDING ─────────────────────────────────────

async def open_positions(conn, *, account_id: str, venue: str) -> list[dict]:
    """EVERY POSITION THAT IS STILL OPEN, outstanding order or held inventory.

    Read through the database's own predicates, so this and the rails cannot
    answer different questions about the same row.
    """
    rows = await conn.fetch(
        "SELECT intent_id, account_id, venue, us_market_slug, event_key, "
        "       order_intent, state, venue_order_id, kind, "
        "       payout_event, held_is_long, "
        "       quantity::float8 AS quantity, "
        "       residual_qty::float8 AS residual, "
        "       collateral_usd::float8 AS collateral, "
        "       limit_price::float8 AS limit_price, "
        "       EXTRACT(EPOCH FROM sent_at)::float8 AS sent_epoch, "
        "       bettor_funded_order_is_outstanding(state) AS outstanding, "
        "       bettor_funded_holds_inventory(residual_qty, closed_at) "
        "           AS holding, settlement "
        "  FROM bettor_funded_intents "
        " WHERE kind='ENTRY' AND account_id=$1 AND upper(venue)=upper($2) "
        "   AND bettor_funded_position_is_open(state, residual_qty, closed_at)"
        " ORDER BY created_at", str(account_id), str(venue))
    return [dict(r) for r in rows]


# ── 2 · THE EXIT, THROUGH THE SAME ADAPTER ───────────────────────────

# ── THE EXIT DECISION, THROUGH THE COMPONENTS THAT ALREADY EXIST ────

def funded_fee_fn(*, qty, price):
    """The deployed fee schedule, in the shape the selectors call."""
    got, _ = FB.fee_for(float(qty), float(price), at=time.time())
    return float(got)


#: The fixture states the venue and provider record, mapped onto the
#: hold-value vocabulary. A token outside this table becomes UNKNOWN, which
#: takes the established bound -- never a wider one.
_EVENT_STATE_TOKENS = {
    "PRE": "PRE_MATCH", "PREGAME": "PRE_MATCH", "PRE_MATCH": "PRE_MATCH",
    "SCHEDULED": "PRE_MATCH", "NOT_STARTED": "PRE_MATCH",
    "IN": "IN_PLAY", "IN_PLAY": "IN_PLAY", "LIVE": "IN_PLAY",
    "INPROGRESS": "IN_PLAY", "IN_PROGRESS": "IN_PLAY",
    "BREAK": "BREAK", "HALFTIME": "BREAK", "HALF_TIME": "BREAK",
    "SUSPENDED": "SUSPENDED", "DELAYED": "SUSPENDED",
    "FINAL": "FINAL", "FINISHED": "FINAL", "COMPLETED": "FINAL",
    "ABANDONED": "ABANDONED", "CANCELLED": "ABANDONED",
    "POSTPONED": "ABANDONED",
}


def event_state_of(raw) -> str:
    """A fixture's own state token, in the hold-value vocabulary.

    An unrecognised token is UNKNOWN, which takes the ESTABLISHED age bound.
    That is the safe direction: every admissible state takes the same bound and
    only a named experiment widens it, so a token this table does not know
    cannot buy a staler quote.
    """
    tok = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    return _EVENT_STATE_TOKENS.get(tok, "UNKNOWN")


def _attestation_from_comparison(cmp_, *, book_rule=None) -> dict | None:
    """THE ENTRY'S CONDITION-TO-PAYOUT VERDICT, in the shape `ev_hold` reads.

    None when the row carries no such comparison -- the caller then falls back
    to an attestation dict if one exists, and otherwise to nothing, which reads
    UNKNOWN and restricts the funded action. Pure.

      COMPATIBLE    every applicable condition was stated on both sides and paid
                    the same -> established, nothing unmet.
      INCOMPATIBLE  the named mismatched conditions travel as conflicts, so
                    `_terminal_rule` reads INCOMPATIBLE and applies its own
                    transform-or-disqualify rule to them.
      UNKNOWN       the unstated conditions travel as unmet. Silence is not
                    agreement, and an UNKNOWN terminal rule restricts a funded
                    action exactly as it did before.
    """
    c = dict(cmp_ or {})
    if c.get("compared_on") != "CONDITION_TO_PAYOUT":
        return None
    verdict = str(c.get("compatibility") or "").upper()
    if verdict not in ("COMPATIBLE", "INCOMPATIBLE", "UNKNOWN"):
        return None
    applicable = list(c.get("applicable_conditions") or [])
    unstated = list(c.get("unstated_conditions") or [])
    mismatched = list(c.get("mismatched_conditions") or [])
    base = {"book_rule": book_rule, "attested": applicable,
            "venue_rules_text_read": c.get("venue_rules_read"),
            "venue_rules_field": c.get("venue_rules_source"),
            "derived_from": "settlement_comparison",
            "comparison_verdict": verdict}
    if verdict == "COMPATIBLE" and not unstated and not mismatched:
        return dict(base, overall_established=True, unmet=[])
    if verdict == "INCOMPATIBLE" or mismatched:
        return dict(base, overall_established=False,
                    unmet=["CONFLICT:%s" % m for m in mismatched]
                    or ["CONFLICT:UNNAMED"],
                    mismatched_conditions=mismatched)
    return dict(base, overall_established=False,
                unmet=unstated or ["SETTLEMENT_COMPARISON_UNKNOWN"])


async def _decision_evidence(conn, probability_row) -> dict:
    """THE FIXTURE AND SETTLEMENT EVIDENCE BESIDE THE PROBABILITY BEING USED.

    WHICH ROW THIS IS, PRECISELY. `latest_probability` returns the freshest
    ELIGIBLE valuation row for this market -- the CURRENT one, selected for this
    decision. It is NOT necessarily the historical row that priced the entry,
    and an earlier version of this docstring said it was. The distinction
    matters: the entry may have been taken minutes or hours ago against a
    different quote, a different fixture phase and possibly a different
    settlement attestation. What this function returns is the evidence attached
    to the row the EXIT is being valued on, which is the correct pairing for
    that valuation and is not a claim about the entry.

    WHY IT IS READ AT ALL. `ev_hold` needs an event state (for its freshness
    bound) and a settlement attestation (for its terminal rule). Neither is a
    column on the funded position, and reading them off the position dict --
    which is what this used to do -- yielded None for both on every call. They
    live on the valuation row: `settlement_rule` is the attested rule and
    `settlement_comparison` carries the fixture evidence that scoped it,
    including `fixture_event_state`.

    The row's own id and observation time are returned so a reader can see
    exactly which observation the valuation rests on rather than inferring it.
    """
    out = {"read": False, "event_state": "UNKNOWN", "event_state_raw": None,
           "settlement_rule": None, "settlement_supplied": False,
           "valuation_row_id": (probability_row or {}).get("id"),
           "valuation_observed_at": None,
           "which_row_this_is": (
               "the freshest ELIGIBLE valuation row for this market, selected "
               "for THIS decision. NOT necessarily the historical row that "
               "priced the entry")}
    rid = out["valuation_row_id"]
    if rid is None:
        return dict(out, why=("the probability row carries no id, so the "
                              "decision evidence beside it cannot be found"))
    try:
        # BY ID, AND ONLY THE ID `latest_probability` SELECTED -- which reads
        # ENTRY_DECISION rows only (migration 144), so a calibration-only row
        # never reaches this decision's evidence.
        row = await conn.fetchrow(
            "SELECT settlement_rule, settlement_comparison "
            "  FROM external_valuations WHERE id=$1", rid)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, why="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    if row is None:
        return dict(out, why="no valuation row %s exists any more" % rid)
    import json as _j

    rule = row["settlement_rule"]
    if isinstance(rule, str):
        try:
            rule = _j.loads(rule)
        except ValueError:
            rule = None
    cmp_ = row["settlement_comparison"]
    if isinstance(cmp_, str):
        try:
            cmp_ = _j.loads(cmp_)
        except ValueError:
            cmp_ = None
    raw_state = (cmp_ or {}).get("fixture_event_state")
    obs = (probability_row or {}).get("observed_at")
    # ── THE VERDICT THAT ADMITTED THE ENTRY, NOT A SUPERSEDED FIELD ──
    #
    # THE DEFECT THIS CLOSES, found by running the scheduled lifecycle end to
    # end. The entry lane admits on `settlement_comparison` -- the
    # condition-to-payout comparison, COMPATIBLE / INCOMPATIBLE / UNKNOWN. The
    # `settlement_rule` column on the same row holds the bookmaker rule's NAME,
    # e.g. "FULL_GAME_INCLUDING_EXTRA_INNINGS". This read `json.loads` of that
    # name, which raises, so `rule` became None, the terminal rule read
    # UNKNOWN, and every funded exit, reduce and hedge on a position the entry
    # had admitted as COMPATIBLE was refused
    # THE_SETTLEMENT_RULE_IS_NOT_ESTABLISHED_FOR_A_FUNDED_ACTION. One decision
    # row carried two settlement verdicts and management read the one that was
    # never a verdict.
    #
    # The funded lifecycle tests did not see it because they seed
    # `settlement_rule` as an attestation dict -- a shape the real entry lane
    # does not write.
    derived = _attestation_from_comparison(cmp_, book_rule=row[
        "settlement_rule"] if isinstance(row["settlement_rule"], str) else None)
    settlement_source = None
    if derived is not None:
        rule = derived
        settlement_source = (
            "settlement_comparison -- the condition-to-payout verdict the "
            "entry was admitted on")
    elif isinstance(rule, dict):
        settlement_source = "settlement_rule (an attestation dict)"
    else:
        rule = None
    return dict(out, read=True, settlement_rule=rule,
                settlement_source=settlement_source,
                settlement_supplied=bool(rule),
                event_state_raw=raw_state,
                event_state=event_state_of(raw_state),
                valuation_observed_at=(None if obs is None
                                       else str(obs)),
                why=("the persisted evidence attached to the valuation row "
                     "this decision is using. The exit is valued under the "
                     "rule attested for THAT observation"))


async def select_exit(conn, position, *, client=None, now=None,
                      fee_fn=None, book_reader=None,
                      subscription=None, revalidation=None) -> dict:
    """WHAT TO DO WITH A FUNDED HOLDING, AND HOW MUCH OF IT.

    THE DEFECT THIS CLOSES. `manage()` reconciled, asked about settlement, and
    then emitted `needs_a_decision`. That is servicing infrastructure reporting
    that a decision is owed -- it is not exit management, and describing it as
    such was the overstatement. Nothing selected an action.

    THIS SELECTS ONE, THROUGH THE DEPLOYED DECISION PATH and not a second copy
    of it:

      pmus.book_read                 the venue's own executable book
      bettor_book_snapshot.exit_ladder    what CLOSING pays, level by level,
                                     with the complement price beside it
      bettor_hold_value.latest_probability   the freshest ELIGIBLE row; a held
                                     row is never used for a decision
      bettor_hold_value.ev_hold      EV_HOLD under its own freshness bound,
                                     its payout-event agreement check and its
                                     terminal-rule gate
      bettor_mgmt_select.rank_with_hold   every action scored, the marginal
                                     quantity the book pays a premium for, the
                                     MIN_IMPROVEMENT gate, and `selected`

    EVERY EVIDENCE REQUIREMENT THOSE COMPONENTS CARRY IS RETAINED. The freshness
    bound is theirs, the eligible-row rule is theirs, the payout-event agreement
    is theirs, and the settlement-compatibility gate is theirs. This function
    supplies funded inputs and reports what is missing; it relaxes nothing.

    AND A MISSING INPUT IS NAMED, ONE AT A TIME. "Needs a decision" was the old
    answer to five different problems. Each now has its own refusal, so the
    next engineering step is identified rather than guessed.
    """
    from . import bettor_book_snapshot as BS
    from . import bettor_hold_value as HV
    from . import bettor_mgmt_select as MS

    at = float(now if now is not None else time.time())
    slug = str(position.get("us_market_slug"))
    opened_with = str(position.get("order_intent"))
    residual = float(position.get("residual") or position.get("residual_qty")
                     or 0)
    out = {"version": VERSION, "at": at, "intent_id": position.get("intent_id"),
           "us_market_slug": slug, "residual_qty": residual,
           "selected": None, "selected_qty": None, "limit_price": None,
           "components": {
               "book": "pmus.book_read",
               "ladder": "bettor_book_snapshot.exit_ladder",
               "probability": "bettor_hold_value.latest_probability",
               "hold_value": "bettor_hold_value.ev_hold",
               "ranking": "bettor_mgmt_select.rank_with_hold"},
           "evidence_requirements_are_the_components_own": True}
    if residual <= 0:
        return dict(out, ok=False, refusal=R_NOTHING_HELD)

    # ── THE EVENT THIS CONTRACT PAYS ON, never derived ──────────────
    payout_event = position.get("payout_event")
    if not payout_event:
        return dict(out, ok=False, refusal=R_NO_PAYOUT_EVENT,
                    missing_input="bettor_funded_intents.payout_event",
                    why=("`ev_hold` requires the event OUR contract pays on, "
                         "and it is NOT the order intent: a BUY_SHORT pays on "
                         "the complement, and on a three-way book the "
                         "complement is not the opposing team. This position "
                         "was recorded before the column existed, or by a "
                         "caller that did not supply it. Deriving it here "
                         "would value the holding against the wrong outcome"))

    # ── THE BASIS, FROM THE ENTRY FILLS ────────────────────────────
    rb = await FB.remaining_basis(conn, str(position.get("intent_id")))
    out["basis"] = rb
    if rb["basis_per_contract"] is None:
        return dict(out, ok=False, refusal=R_NO_BASIS,
                    why=("no entry fill carries a cost for this position, so "
                         "there is nothing to rank an exit against"))
    basis_per = float(rb["basis_per_contract"])

    # ── THE EXECUTABLE BOOK ────────────────────────────────────────
    from . import pmus as _p

    reader = book_reader or _p.book_read
    # RESOLVE A CLIENT WHEN THE CALLER SUPPLIED NONE. `pmus.book_read` takes the
    # client explicitly and does NOT fall back, so a scheduled caller that
    # passes nothing gets `NO_BOOK_FEED_ON_CLIENT` -- an answer about our
    # argument, dressed as an answer about the venue. The resolution goes
    # through `_get_client`, which is the transport seam, so the substituted
    # transport in a test and the real credential in production take the same
    # path. No credential raises, and that is reported as the credential it is.
    use = client
    if use is None:
        try:
            use = _p._get_client()
        except Exception as exc:                           # noqa: BLE001
            return dict(out, ok=False, refusal=R_BOOK_UNREADABLE,
                        error="%s: %s" % (type(exc).__name__,
                                          str(exc)[:200]),
                        why=("no venue client could be resolved, so the "
                             "executable book was never read. In this "
                             "deployment that is the absent credential, not "
                             "an empty market"))
    # ── THE THREE INSTANTS, TAKEN SEPARATELY ────────────────────────
    #
    # `at` is when this pass STARTED. It is not the decision instant and it is
    # not when the book arrived, and using it for either understates the age by
    # however long acquisition took.
    requested_at = time.time()
    try:
        got = reader(use, slug) or {}
    except Exception as exc:                               # noqa: BLE001
        return dict(out, ok=False, refusal=R_BOOK_UNREADABLE,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    received_at = time.time()
    if got.get("error") or not got.get("marketData"):
        return dict(out, ok=False, refusal=R_BOOK_UNREADABLE,
                    book_error=got.get("error"),
                    why=("an unreadable book and an empty book are different "
                         "facts and only one of them is about the market. "
                         "Neither is a reason to sell"))
    # ── THE VENUE BOOK'S OWN AGE, UNDER THE DECLARED ADMISSION POLICY ─
    #
    # THE DEFECT THIS CLOSES. `book_read` retrieves the payload and nothing
    # more, and this applied no freshness check at all -- so a ten-minute-old
    # ladder was as good as a current one. `ev_hold` checks the PROBABILITY's
    # age, which is a different claim about a different source: a fresh
    # probability against a stale book is precisely the pair that produces a
    # confident decision on a price that no longer exists.
    # ── THE LADDER, PARSED FIRST. It needs no clock, and a side that
    #    publishes no executable level has nothing to sell into whatever any
    #    probability says -- so it is refused before a database read.
    lad = BS.exit_ladder(got["marketData"], held_intent=opened_with)
    out["exit_ladder"] = {k: lad.get(k) for k in
                          ("ok", "refusal", "best_exit_price",
                           "best_api_price", "size_at_best",
                           "displayed_depth", "levels_read", "parse_status")}
    if not lad.get("ok"):
        return dict(out, ok=False, refusal=R_NO_EXIT_SIDE,
                    ladder_refusal=lad.get("refusal"), why=lad.get("why"))

    # THE DECISION INSTANT, TAKEN AFTER EVERY ACQUISITION -- the book above and
    # the probability and evidence reads below. Both clocks are aged against
    # THIS instant, so a decision cannot be stamped fresher than the work that
    # produced it, and a slow read counts against the bound rather than
    # disappearing into it.
    prob = await HV.latest_probability(conn, us_market_slug=slug)
    out["probability_read"] = {k: prob.get(k) for k in
                               ("found", "refusal", "eligibility", "why")}
    if not prob.get("found"):
        return dict(out, ok=False, refusal=R_NO_PROBABILITY,
                    probability_refusal=prob.get("refusal"),
                    why=prob.get("why"))
    evidence = await _decision_evidence(conn, prob["row"])
    decision_at = time.time()
    out["instants"] = {
        "pass_started_at": at, "book_requested_at": requested_at,
        "book_received_at": received_at, "decision_at": decision_at,
        "acquisition_s": round(received_at - requested_at, 3),
        "reads_after_the_book_s": round(decision_at - received_at, 3),
        "why": ("the age of every input is evaluated at `decision_at`, which "
                "is after all of them. `pass_started_at` is when this pass "
                "began and is NOT the decision instant -- aging against it "
                "was the defect: it understated every age by the whole "
                "acquisition")}
    # THE SHARED RULE, AND THE EVIDENCE IT NEEDS. `http_observation` is the
    # response contract recorded by `venue_http_observer` on this very read;
    # `subscription` is a live market-data subscription for this market when one
    # exists. Passing neither is not a loophole -- the verdict is then the named
    # unresolved one and the exit refuses.
    fresh = FA.venue_book_age(got.get("marketData"),
                             decision_at=decision_at,
                             received_at=received_at,
                             requested_at=requested_at,
                             observation=got.get("http_observation"),
                             subscription=subscription
                                          or got.get("subscription"),
                             revalidation=revalidation
                                          or got.get("revalidation"))
    out["venue_book_age"] = fresh
    if not fresh.get("ok"):
        return dict(out, ok=False, refusal=R_BOOK_NOT_FRESH,
                    book_refusal=fresh.get("refusal"), why=fresh.get("why"),
                    note=("probability freshness does not establish book "
                          "freshness. This is the entry lane's own admission "
                          "policy, applied to a funded exit"))

    # ── EV_HOLD, UNDER ITS OWN RULES, WITH THE EVIDENCE IT EXPECTS ───
    #
    # THE FIXTURE AND SETTLEMENT EVIDENCE, read above beside the probability.
    #
    # THE DEFECT THIS CLOSES. `open_positions()` supplies neither
    # `event_state` nor `settlement_rule` -- they are not columns on the
    # position at all -- and this read them off the position dict, so both
    # were always None. `ev_hold` then took its default UNKNOWN state and an
    # unsupplied settlement, and `_terminal_rule` labelled the result
    # CONDITIONAL and ranked it anyway. Checking `status == IDENTIFIED` passed
    # on a valuation whose terminal rule was never established.
    #
    # The evidence is the ENTRY DECISION'S OWN, persisted on the
    # `external_valuations` row this probability came from: the attested
    # settlement rule, and the fixture's event state inside the settlement
    # comparison that scoped it.
    ev = evidence
    out["decision_evidence"] = {k: ev.get(k) for k in
                                ("read", "event_state", "event_state_raw",
                                 "settlement_supplied", "valuation_row_id",
                                 "valuation_observed_at",
                                 "which_row_this_is", "why")}
    # AGED AT THE DECISION INSTANT, the same one the book was aged at. Passing
    # the pass-start instant here would have let a slow acquisition carry an
    # expired probability into a live comparison.
    hv = HV.ev_hold(qty=residual, basis_per_contract=basis_per,
                    probability_row=prob["row"], now=decision_at,
                    payout_event_held=str(payout_event),
                    event_state=ev.get("event_state"),
                    settlement=ev.get("settlement_rule"))
    out["ev_hold"] = {k: hv.get(k) for k in
                      ("status", "refusal", "why", "ev_hold_usd",
                       "probability", "age_bound_s", "event_state",
                       "probability_event", "payout_event_held",
                       # WHICH ROW THE PROBABILITY CAME FROM, so a consumer
                       # that must price on the SAME probability (the pair
                       # cycle's payout-state distribution) can name it.
                       "source_row_id", "source")}
    term = dict(hv.get("terminal_rule") or {})
    out["terminal_rule"] = {k: term.get(k) for k in
                            ("compatibility", "established", "asked", "unmet",
                             "conflicts", "disqualifies_selection",
                             "unknown_is_retained_as_conditional")}
    if hv.get("status") != "IDENTIFIED":
        return dict(out, ok=False, refusal=R_HOLD_NOT_PRICED,
                    hold_refusal=hv.get("refusal"), why=hv.get("why"),
                    what_is_missing=(
                        "EV_HOLD is the reference every action is measured "
                        "against. Without it nothing can be shown to beat "
                        "holding, and selling because a price exists is not a "
                        "decision. THIS IS AN ENGINEERING INPUT, not "
                        "calibration evidence: the two are not "
                        "interchangeable, and accumulating outcome history "
                        "would not supply it"))
    # ── AND `IDENTIFIED` IS NOT ENOUGH FOR A FUNDED ACTION ──────────
    #
    # The hold-value component deliberately RETAINS an UNKNOWN terminal rule as
    # a conditional valuation and ranks it, because for a SHADOW decision the
    # probability is still the best available estimate. A funded exit is not a
    # shadow decision: an unestablished terminal rule means we do not know what
    # the contract pays on an overtime, a push or a void, and that is the
    # number the whole comparison rests on. UNKNOWN therefore restricts the
    # funded action instead of being labelled and acted upon.
    if term.get("compatibility") != "ESTABLISHED":
        return dict(out, ok=False, refusal=R_SETTLEMENT_NOT_ESTABLISHED,
                    compatibility=term.get("compatibility"),
                    unmet=term.get("unmet"),
                    why=("the terminal rule is %s. The hold-value component "
                         "retains that as a CONDITIONAL shadow valuation and "
                         "ranks it, which is right for a shadow decision and "
                         "not for a funded one: %s"
                         % (term.get("compatibility"),
                            term.get("why_not") or "no attestation supplied")),
                    funded_restriction=(
                        "a funded exit requires an ESTABLISHED settlement "
                        "attestation. Conditional valuation is admissible for "
                        "the shadow book and is not admissible here"))

    # ── WHAT REMAINS OF THE INPUTS' OWN LIFETIME ────────────────────
    #
    # Computed from the two OBSERVATION stamps under the policies that admitted
    # them, so selecting cannot extend either. It is carried on the answer and
    # re-checked immediately before anything is sent.
    deadline = input_deadline(fresh, hv)
    out["inputs_expiry"] = deadline
    if not deadline.get("ok"):
        return dict(out, ok=False, refusal=R_INPUT_EXPIRY_UNMEASURED,
                    missing=deadline.get("missing"), why=deadline.get("why"),
                    note=("both inputs passed their own freshness checks, so "
                         "this is a reporting gap in one of them rather than a "
                         "stale input -- and it is refused anyway, because a "
                         "submission with no established deadline is the hole "
                         "this replaced"))
    out["remaining_lifetime_s"] = round(
        float(deadline["expires_at"]) - decision_at, 3)

    # ── THE RANKING, AND ITS OWN GATES ─────────────────────────────
    sale = BS.as_sale_ladder(lad)
    ranked = MS.rank_with_hold(
        residual, basis_per, ev_hold=hv,
        bid=lad.get("best_exit_price"), bid_size=lad.get("size_at_best"),
        complement_ask=lad.get("best_complement_price"),
        complement_ask_size=lad.get("size_at_best"),
        fee_fn=(fee_fn or funded_fee_fn),
        venue=position.get("venue"), us_market_slug=slug,
        held_is_long=(opened_with != SHORT),
        sale_ladder=sale,
        # WHAT THIS FUNCTION CAN ACTUALLY SEND. The dispatch below has one
        # branch, for DIRECT_EXIT and REDUCE. Declaring it here stops an
        # action becoming selectable before its execution semantics are
        # supported -- see EXECUTABLE_ACTIONS.
        executable_actions=EXECUTABLE_ACTIONS)
    out["ranking"] = {k: ranked.get(k) for k in
                      ("selected", "selected_qty", "selection_reason",
                       "operating_state", "governing_rule", "runner_up",
                       "improvement_over_hold_per_contract",
                       "is_a_deliberate_hold",
                       # ── THE TABLE ITSELF, NOT JUST ITS WINNER ──────
                       #
                       # The projection listed the outcome and dropped the
                       # reasoning, so a servicing pass could exit at a
                       # LOSS -- correctly, because holding was worth less
                       # -- and no operator-visible field said so. The
                       # candidate carries `locks_a_loss`, `depth_limited`
                       # and `fees_usd`; the ineligible ones carry the code
                       # that disqualified them. Both are what "the exact
                       # reason an action could not proceed" means, so both
                       # travel with the decision.
                       "candidates", "unqualified")}
    # AND THE CHOSEN ROW, RESOLVED, so a reader does not rescan the table
    # to learn whether the action it is looking at realises a loss.
    _sel_name = ranked.get("selected")
    _chosen = next((c for c in (ranked.get("candidates") or [])
                    if c.get("action") == _sel_name), None)
    out["ranking"]["selected_candidate"] = _chosen
    out["ranking"]["selected_locks_a_loss"] = (
        None if _chosen is None else bool(_chosen.get("locks_a_loss")))
    out["ranking"]["selected_is_depth_limited"] = (
        None if _chosen is None else bool(_chosen.get("depth_limited")))
    out["not_rankable"] = ranked.get("not_rankable")
    # ── EVERY EVIDENCED EXIT ACTION GETS ITS OWN EXECUTABLE TERMS ────
    #
    # THE GAP THIS CLOSES (Xavier map, decision-flow Q2). Only the SELECTED
    # action got a wire limit and a proceeds bound, so the final ranking could
    # build an ExecutionPlan for that one action alone: a DIRECT_EXIT that lost
    # to HOLD here (or to REDUCE) was dropped silently before the one ranking
    # that also holds the hedge ever saw it. The terms below are the SAME
    # arithmetic the selected branch applies -- `_exit_terms` is that branch's
    # rule, factored out -- computed for every DIRECT_EXIT and REDUCE
    # candidate. An action whose terms cannot be established carries its
    # refusal instead, so the supplier can name it rather than drop it.
    out["executable_exit_terms"] = {}
    for _cand in (ranked.get("candidates") or []):
        _act = _cand.get("action")
        if _act not in EXECUTABLE_ACTIONS or not _cand.get("qty"):
            continue
        _t = _exit_terms(_act, float(_cand["qty"]), lad, ranked, opened_with)
        _t.update(assessed_at=decision_at,
                  inputs_expire_at=float(deadline["expires_at"]),
                  inputs_expiry_governed_by=deadline.get("governed_by"),
                  value_usd=_cand.get("value_usd"),
                  selection_eligible=_cand.get("selection_eligible"))
        out["executable_exit_terms"][_act] = _t
    sel = ranked.get("selected")
    qty = ranked.get("selected_qty")
    if sel in ("DIRECT_EXIT", "REDUCE") and qty and float(qty) > 0:
        # ── PROCEEDS AND WIRE PRICE ARE DIFFERENT NUMBERS ────────────
        #
        # THE DEFECT THIS CLOSES, and it inverted the economic bound on every
        # short. `exit_ladder.best_exit_price` is CASH RECEIVED per held
        # contract. `submit_exit`'s `limit_price` is the WIRE price -- the
        # contract price the venue puts on the order. On a LONG they happen to
        # coincide. On a SHORT they are complements: a long-side ask of 0.20
        # means exit proceeds of 0.80, and passing 0.80 as the wire limit told
        # the venue to accept anything down to 0.20 in proceeds. The selector
        # chose the action on 0.80 and would have submitted a bound of 0.20.
        #
        # THE LADDER ALREADY CARRIES BOTH, by name and for this reason:
        # `exit_price` is the proceeds and `api_price` is the venue's own price
        # at that level. `api_price` IS the wire on both sides -- long:
        # proceeds = wire; short: proceeds = 1 - wire -- so it is what is sent,
        # and the proceeds ride beside it as what the decision was made on.
        proceeds_per = float(lad["best_exit_price"])
        wire = float(lad["best_api_price"])
        # ── A MULTI-LEVEL REDUCE IS BOUNDED AT ITS MARGINAL LEVEL ──────
        #
        # THE DEFECT THIS CLOSES. Both actions took the BEST level's price.
        # For a DIRECT_EXIT that is right: its quantity is `size_at_best`,
        # so the best level's price clears all of it. For a REDUCE whose
        # quantity spans several levels it is wrong -- a sell limit at the
        # best price matches only the best level's depth, so a REDUCE
        # selected for 10 contracts on a vwap of 0.578 was submitted
        # bounded at 0.62 and could fill 4.
        #
        # It lost no money (the bound is never crossed downward), but the
        # ACTION'S WHOLE ADVANTAGE over DIRECT_EXIT was unreachable by the
        # order sent -- and REDUCE is in EXECUTABLE_ACTIONS, so it was
        # allowed to win on it. An advertised action whose differentiating
        # case the dispatch cannot execute is not an executable action.
        #
        # The marginal level is the worst price the chosen quantity
        # accepts; a limit there clears every better level too, so one
        # order fills the whole quantity at the vwap the decision used.
        _marg = ranked.get("marginal_sale") or {}
        if sel == "REDUCE" and _marg.get("needs_a_marginal_wire_price"):
            _mw = _marg.get("marginal_api_price")
            if _mw is None:
                # NO GUESS. Without the marginal level's wire price this
                # quantity cannot be bounded correctly, and bounding it at
                # the best level would under-fill silently.
                return dict(out, ok=False,
                            refusal=R_REDUCE_MARGINAL_WIRE_NOT_SUPPLIED,
                            selected=sel, selected_qty=float(qty),
                            levels_spanned=_marg.get("levels_spanned"),
                            vwap=_marg.get("vwap"),
                            why=("this REDUCE spans %s levels and the sale "
                                 "ladder supplied no wire price for the "
                                 "marginal one. Bounding at the best level "
                                 "would fill only its depth, so the chosen "
                                 "quantity is not submittable and nothing "
                                 "is sent"
                                 % _marg.get("levels_spanned")))
            wire = float(_mw)
            # ── WHICH NUMBER THE ROUNDING GUARD BELOW COMPARES ─────────
            #
            # The MARGINAL level's proceeds, not the vwap. The guard asks
            # "does the wire we round to receive less than the price this
            # order accepts?", and what this order accepts at its worst IS
            # the marginal level. My first version set this to the vwap and
            # the guard refused every multi-level REDUCE: 0.55 at the margin
            # is CORRECTLY below a 0.578 vwap, because the better levels
            # make up the difference. Comparing a margin against an average
            # compares two different quantities.
            proceeds_per = float(_marg["marginal_proceeds_per_contract"])
            out["reduce_spans_levels"] = _marg.get("levels_spanned")
            out["reduce_bounded_at"] = "THE_MARGINAL_LEVEL"
            # AND THE AGGREGATE THE DECISION RESTS ON, beside it: the vwap
            # over every level taken. That is what the ranking scored, and
            # it is NOT the per-contract bound.
            out["reduce_expected_vwap"] = float(_marg["vwap"])
            out["reduce_vwap_is_not_the_bound"] = (
                "the order is bounded at the marginal level (%s) so the "
                "whole quantity can fill; the vwap (%s) is what the "
                "selection scored across every level taken. Better levels "
                "fill at their own better prices"
                % (_mw, round(float(_marg["vwap"]), 6)))
        rounded = safe_exit_cent(wire, opened_with)
        if rounded is None:
            return dict(out, ok=False, refusal=R_EXIT_WIRE_UNREPRESENTABLE,
                        wire_asked=wire, proceeds_per_contract=proceeds_per,
                        why=("%s cannot be expressed in the venue's two "
                             "decimals on this side without accepting less "
                             "than the level the action was chosen on"
                             % wire))
        # AND THE ROUNDING IS CHECKED, not trusted. `safe_exit_cent` ceils a
        # long wire and floors a short one, which is the direction that cannot
        # reduce proceeds -- so this assertion should never fire, and it is
        # here because "should never" is how the inversion above survived.
        got_per = exit_proceeds(1, rounded, opened_with)
        if got_per < proceeds_per - 1e-9:
            return dict(out, ok=False, refusal=R_EXIT_WIRE_UNREPRESENTABLE,
                        wire_asked=wire, wire_rounded=rounded,
                        proceeds_per_contract=proceeds_per,
                        proceeds_after_rounding=got_per,
                        why=("rounding the wire to %s would receive %.6f per "
                             "contract against the %.6f the action was "
                             "selected on" % (rounded, got_per, proceeds_per)))
        return dict(out, ok=True, refusal=None, selected=sel,
                    selected_qty=float(qty),
                    # WHEN THIS WAS ASSESSED, so a caller that sits on it can
                    # be refused rather than sending a stale bound.
                    assessed_at=decision_at,
                    # THE INPUTS' OWN DEADLINE, not this instant plus a bound.
                    inputs_expire_at=float(deadline["expires_at"]),
                    inputs_expiry_governed_by=deadline.get("governed_by"),
                    inputs_expiry=deadline,
                    remaining_lifetime_s=out["remaining_lifetime_s"],
                    # WHAT IS SENT.
                    limit_price=rounded,
                    price_space="VENUE_WIRE_CONTRACT_PRICE",
                    # WHAT THE DECISION WAS MADE ON.
                    proceeds_per_contract=proceeds_per,
                    proceeds_after_rounding=got_per,
                    proceeds_space=("cash received per held contract: wire on "
                                    "a long, (1 - wire) on a short"),
                    rounding=("CEIL" if opened_with != SHORT else "FLOOR"),
                    is_an_evidenced_exit=True,
                    price_source=("the best level of the venue's own exit "
                                  "ladder at the moment of the decision"),
                    why=ranked.get("selection_reason"))
    # ── THE DISPATCH IS TOTAL, and it was not ────────────────────────
    #
    # Anything selected that is not HOLD and not in EXECUTABLE_ACTIONS
    # reached this return and was reported ok=True with a note claiming
    # HOLD had been chosen. That is an unexecuted action recorded as a
    # successful servicing pass. It is now a named refusal.
    if sel is not None and sel != "HOLD" and sel not in EXECUTABLE_ACTIONS:
        return dict(out, ok=False, refusal=R_ACTION_NOT_EXECUTABLE,
                    selected=sel,
                    selected_qty=(None if qty is None else float(qty)),
                    assessed_at=decision_at,
                    is_an_evidenced_exit=False,
                    executable_actions=list(EXECUTABLE_ACTIONS),
                    unimplemented_route=UNIMPLEMENTED_ROUTES.get(sel),
                    why=("the ranking selected %s and this module dispatches "
                         "only %s. No order was planned, no inventory was "
                         "reserved and nothing was sent. Reporting this as a "
                         "completed servicing pass is what hid the gap, so "
                         "it is a refusal. The position is unchanged and is "
                         "still held"
                         % (sel, ", ".join(EXECUTABLE_ACTIONS))),
                    inventory_untouched=True)

    return dict(out, ok=True, refusal=None, selected=sel,
                selected_qty=(None if qty is None else float(qty)),
                assessed_at=decision_at,
                inputs_expire_at=float(deadline["expires_at"]),
                inputs_expiry_governed_by=deadline.get("governed_by"),
                is_an_evidenced_exit=False,
                why=ranked.get("selection_reason"),
                note=("this IS a decision. HOLD chosen by a named rule on "
                      "observed inputs is not the same as no decision, and "
                      "the reason above says which rule"))


def _exit_terms(action, qty, lad, ranked, opened_with) -> dict:
    """THE WIRE LIMIT AND PROCEEDS BOUND ONE EXIT ACTION WOULD BE SENT AT.

    The same rules `select_exit` applies to the SELECTED action -- the best
    level for a DIRECT_EXIT, the MARGINAL level for a multi-level REDUCE,
    rounding in the direction that cannot receive less, and that rounding
    checked rather than trusted -- so a plan built for an action that was not
    selected here is bounded exactly as it would have been had it been. Pure.
    """
    out = {"ok": False, "action": action, "selected_qty": float(qty),
           "price_space": "VENUE_WIRE_CONTRACT_PRICE"}
    proceeds_per = float(lad["best_exit_price"])
    wire = float(lad["best_api_price"])
    marg = ranked.get("marginal_sale") or {}
    if action == "REDUCE" and marg.get("needs_a_marginal_wire_price"):
        mw = marg.get("marginal_api_price")
        if mw is None:
            return dict(out, refusal=R_REDUCE_MARGINAL_WIRE_NOT_SUPPLIED,
                        levels_spanned=marg.get("levels_spanned"),
                        vwap=marg.get("vwap"))
        wire = float(mw)
        proceeds_per = float(marg["marginal_proceeds_per_contract"])
        out.update(reduce_spans_levels=marg.get("levels_spanned"),
                   reduce_bounded_at="THE_MARGINAL_LEVEL",
                   reduce_expected_vwap=float(marg["vwap"]))
    rounded = safe_exit_cent(wire, opened_with)
    if rounded is None:
        return dict(out, refusal=R_EXIT_WIRE_UNREPRESENTABLE, wire_asked=wire,
                    proceeds_per_contract=proceeds_per)
    got_per = exit_proceeds(1, rounded, opened_with)
    if got_per < proceeds_per - 1e-9:
        return dict(out, refusal=R_EXIT_WIRE_UNREPRESENTABLE, wire_asked=wire,
                    wire_rounded=rounded, proceeds_per_contract=proceeds_per,
                    proceeds_after_rounding=got_per)
    return dict(out, ok=True, refusal=None, limit_price=rounded,
                proceeds_per_contract=proceeds_per,
                proceeds_after_rounding=got_per,
                rounding=("CEIL" if opened_with != SHORT else "FLOOR"))


#: The decision-reference keys an exit intent may carry beyond the servicing
#: flags: which persisted decision it executes, the digest of the plan that
#: decision bound, and the Xavier review that recorded it.
EXIT_DECISION_REF_KEYS = ("decision_id", "plan_digest", "xavier_decision_id",
                          "action")


def _exit_decision_ref(decision_ref: dict | None) -> str:
    import json as _j
    ref = {"servicing": True, "reduces_exposure": True}
    for k in EXIT_DECISION_REF_KEYS:
        if (decision_ref or {}).get(k) is not None:
            ref[k] = str(decision_ref[k])
    return _j.dumps(ref)


async def _reserve_exit(conn, *, parent: str, row, venue: str,
                        opened_with: str, wire: float,
                        contracts: int, decision_ref: dict | None = None
                        ) -> dict:
    """TAKE THE INVENTORY BEFORE SENDING ANYTHING, under a row lock.

    THE DEFECT THIS CLOSES, and it is the one the one-open-position index
    cannot: `submit_exit` read `residual_qty`, then inserted an EXIT. EXIT rows
    are deliberately OUTSIDE that unique index, because an exit must never be
    refused for reducing an open position -- so there was nothing at all between
    the read and the insert. Two concurrent full exits both read 10 held and
    both sold 10. A retry after an ambiguous answer did the same thing, because
    the first attempt's UNRESOLVED exit consumed no fills and so did not appear
    in the residual.

    THE RESERVATION IS THE INSERT. `SELECT ... FOR UPDATE` on the PARENT row
    serialises callers on the position itself, and
    `bettor_funded_available_to_exit` -- evaluated inside that lock -- subtracts
    the unfilled remainder of every exit already outstanding or unresolved. The
    second caller therefore reads an availability the first has already
    committed against, and is refused rather than queued into an oversell.

    A SELECT-then-INSERT outside a transaction could not do this, which is the
    same reason the entry path's guarantee is a unique index rather than a
    check.
    """
    out = {"parent_intent_id": parent, "asked_qty": contracts,
           "serialised_by": "SELECT ... FOR UPDATE on the parent position",
           "availability_is": ("residual minus the unfilled remainder of "
                               "every outstanding or unresolved exit")}
    async with conn.transaction():
        locked = await conn.fetchrow(
            "SELECT residual_qty::float8 AS residual, closed_at "
            "  FROM bettor_funded_intents "
            " WHERE intent_id=$1 AND kind='ENTRY' FOR UPDATE", parent)
        if locked is None:
            return dict(out, ok=False, refusal=R_NO_SUCH_POSITION)
        if locked["closed_at"] is not None:
            return dict(out, ok=False, refusal=R_NOTHING_HELD,
                        why="the position closed while this exit was priced")
        avail = float(await conn.fetchval(
            "SELECT bettor_funded_available_to_exit($1)::float8", parent)
            or 0.0)
        out["available_to_exit"] = avail
        out["residual_qty"] = float(locked["residual"] or 0)
        if contracts > avail + 1e-9:
            return dict(out, ok=False, refusal=R_OVER_RESIDUAL,
                        why=("%d contracts were requested and %s are "
                             "available: the residual is %s and the rest is "
                             "already reserved by an exit that is outstanding "
                             "or unresolved. Refused INSIDE the lock, so two "
                             "callers cannot both pass here"
                             % (contracts, avail,
                                float(locked["residual"] or 0))))
        xid = FB.new_intent_id()
        await conn.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent,"
            " limit_price, quantity, collateral_usd, effective_digest,"
            " decision_ref, state, provenance, kind, parent_intent_id,"
            " payout_event, held_is_long, client_identity_supported) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,"
            "        'INTENT_RECORDED',$13,'EXIT',$14,$15,$16,$17)",
            xid, str(row["account_id"]), venue, str(row["venue_class"]),
            str(row["us_market_slug"]), str(row["event_key"]), opened_with,
            wire, contracts,
            # AN EXIT COMMITS NO COLLATERAL. It releases it.
            0.0, str(row["effective_digest"] or ""),
            # ── THE ORDER NAMES THE DECISION IT EXECUTES ────────────
            # The link used to be a naming convention (`op:` vs `dec:`). The
            # persisted decision id, the bound plan's digest and the Xavier
            # review are written onto the order itself when the caller has
            # them; the servicing flags stay exactly as they were.
            _exit_decision_ref(decision_ref),
            FB.PROVENANCE, parent,
            row["payout_event"] if "payout_event" in row.keys() else None,
            row["held_is_long"] if "held_is_long" in row.keys() else None,
            FB.CLIENT_ORDER_IDENTITY_SUPPORTED)
        # READ THE AVAILABILITY BACK inside the same transaction, so the
        # reservation this insert took is a measured fact and not an assumption.
        after = float(await conn.fetchval(
            "SELECT bettor_funded_available_to_exit($1)::float8", parent)
            or 0.0)
    return dict(out, ok=True, refusal=None, exit_intent_id=xid,
                available_after_reserving=after,
                reserved_qty=round(avail - after, 6))


async def submit_exit(conn, *, intent_id: str, limit_price=None,
                      quantity=None, adapter=None, venue: str | None = None,
                      expect_proceeds_per_contract=None, assessed_at=None,
                      inputs_expire_at=None,
                      now: float | None = None,
                      decision_ref: dict | None = None) -> dict:
    """SELL BACK SOME OR ALL OF A HELD FUNDED POSITION.

    `limit_price` IS THE VENUE'S WIRE PRICE -- the contract price the order
    carries -- and NOT the cash we receive. On a long those coincide; on a
    short the proceeds are `1 - wire`. `expect_proceeds_per_contract`, when
    supplied, is the per-contract CASH the caller selected the action on, and
    the submission refuses if the wire it is about to send would receive less
    than that. Passing proceeds into `limit_price` is the inversion that made
    an 0.80 short exit accept 0.20, so the two spaces are named here and
    checked against each other before anything is sent.

    THE ORDER OF OPERATIONS IS THE ENTRY PATH'S, for the same reasons: every
    refusal happens before anything is written, the exit intent is COMMITTED
    before the request leaves, and a lost answer leaves that row UNRESOLVED for
    recovery rather than being retried here.

    WHAT AN EXIT IS *NOT* CHECKED AGAINST. The entry rails. An exit REDUCES
    exposure, and refusing one because a position is open -- or because the
    lane is at its capital cap -- is exactly the failure that strands
    inventory. That is also why the exit is `kind='EXIT'` in the database: the
    one-open-position unique index applies to ENTRY alone.
    """
    at = float(now if now is not None else time.time())
    out = {"version": VERSION, "at": at, "parent_intent_id": intent_id,
           "submitted": False, "exit_intent_id": None,
           # COUNTED, so "no venue call happened" is a readback and not a
           # reassurance. It becomes 1 at the one line that can make one.
           "venue_calls": 0,
           "what_remains_disabled": disablements()}
    # ── THE SCHEMA THIS EXIT WOULD BE RECORDED IN, FIRST ────────────
    #
    # The read below already names `residual_qty` and `kind`, so on an
    # unmigrated database this function raises instead of refusing -- and an
    # exception is not a refusal a caller can act on. Asked first, it becomes
    # one, and the servicing lane reports its capability rather than its
    # traceback.
    from . import bettor_funded_schema as FS

    blocked = await FS.require(conn)
    if blocked is not None:
        return dict(out, venue_calls=0, **blocked)
    row = await conn.fetchrow(
        "SELECT intent_id, account_id, venue, venue_class, us_market_slug, "
        "       event_key, order_intent, effective_digest, closed_at, "
        "       payout_event, held_is_long, "
        "       residual_qty::float8 AS residual, "
        "       quantity::float8 AS quantity "
        "  FROM bettor_funded_intents WHERE intent_id=$1 AND kind='ENTRY'",
        intent_id)
    if row is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_POSITION)
    out["position"] = dict(row)
    residual = float(row["residual"] or 0)
    if residual <= 0 or row["closed_at"] is not None:
        return dict(out, ok=False, refusal=R_NOTHING_HELD,
                    residual_qty=residual,
                    why=("there is nothing to sell back: the position holds "
                         "no residual inventory or has already been closed"))
    # A FIRST, UNLOCKED SIZING READ -- deliberately not the authority. The
    # binding check is `_reserve_exit` below, which takes FOR UPDATE on the
    # parent and computes availability inside the transaction that inserts the
    # exit. This one exists only so an obviously-oversized request is refused
    # before a price is rounded and a gate is consulted.
    avail_hint = await conn.fetchval(
        "SELECT bettor_funded_available_to_exit($1)::float8", intent_id)
    avail_hint = float(avail_hint or 0.0)
    out["available_to_exit_hint"] = avail_hint
    want = avail_hint if quantity is None else float(quantity)
    if want <= 0 or want > avail_hint + 1e-9:
        return dict(out, ok=False, refusal=R_OVER_RESIDUAL,
                    asked=want, residual_qty=residual,
                    available_to_exit=avail_hint,
                    why=("an exit larger than the inventory AVAILABLE would "
                         "oversell: %s contracts are held and %s of them are "
                         "already reserved by an exit that is outstanding or "
                         "unresolved" % (residual, residual - avail_hint)))
    contracts = int(want)
    if contracts < 1:
        return dict(out, ok=False, refusal=R_OVER_RESIDUAL, asked=want,
                    why="%s contracts rounds down to nothing at this venue's "
                        "integer quantity" % want)
    if limit_price is None:
        # NO PRICE IS INVENTED HERE. This lane has no funded mark it would
        # stand behind, and a made-up exit limit is a made-up P&L.
        return dict(out, ok=False, refusal=R_NO_EXIT_PRICE,
                    why=("an exit limit must be supplied by the caller. This "
                         "module has no funded mark source, and inventing one "
                         "would manufacture the very number the exit is "
                         "supposed to measure"))
    opened_with = str(row["order_intent"])
    wire = safe_exit_cent(float(limit_price), opened_with)
    if wire is None:
        return dict(out, ok=False, refusal=R_EXIT_PRICE_UNREPRESENTABLE,
                    asked=float(limit_price), opened_with=opened_with,
                    why=("%s cannot be expressed in the venue's two decimals "
                         "on this side without accepting less than the plan "
                         "assumed" % limit_price))
    ven = str(venue or row["venue"])
    proceeds = exit_proceeds(contracts, wire, opened_with)
    per_contract = exit_proceeds(1, wire, opened_with)
    # ── THE INPUTS' REMAINING LIFETIME, CHECKED TWICE ───────────────
    #
    # THE DEFECT THIS CLOSES. This used to measure the AGE OF THE ASSESSMENT
    # against a 30 s bound, and the selector handed out that bound fresh at the
    # decision instant. So a book observed 29 s ago bought another 30 s by being
    # looked at, and an order could leave against a 59-second-old observation
    # with every field reading "within bound". What expires is the OBSERVATION,
    # not the act of assessing it.
    #
    # `inputs_expire_at` is the earliest expiry of the book stamp and the
    # probability stamp under their own policies, computed by `input_deadline`
    # and carried here unchanged. It is checked HERE, before anything is
    # written, and AGAIN immediately before the send -- after the reservation's
    # row lock, which is the wait that can consume what is left of it.
    out["inputs_expiry"] = {
        "expires_at": (None if inputs_expire_at is None
                       else float(inputs_expire_at)),
        "checked_at": at,
        "remaining_s": (None if inputs_expire_at is None
                        else round(float(inputs_expire_at) - at, 3)),
        "assessed_at_for_the_record": (None if assessed_at is None
                                       else float(assessed_at)),
        "rechecked_immediately_before_the_send": True,
        "what_expires": ("the venue book observation and the probability "
                         "observation, under the bounds that admitted them"),
        "not": "an allowance granted at the moment of selection"}
    if inputs_expire_at is not None and at > float(inputs_expire_at):
        return dict(out, ok=False, refusal=R_ASSESSMENT_EXPIRED,
                    venue_calls=0,
                    why=("the observations behind this exit expired %.1f s ago "
                         "under their own freshness policies. Nothing was "
                         "reserved and nothing was sent: the correct action is "
                         "to re-assess"
                         % (at - float(inputs_expire_at))))

    # ── THE BOUND THE CALLER SELECTED ON, CHECKED AGAINST THE WIRE ───
    if expect_proceeds_per_contract is not None:
        want = float(expect_proceeds_per_contract)
        if per_contract < want - 1e-9:
            return dict(out, ok=False,
                        refusal=R_EXIT_WIRE_UNREPRESENTABLE,
                        wire=wire, proceeds_per_contract=per_contract,
                        selected_on_proceeds_per_contract=want,
                        opened_with=opened_with,
                        why=("this wire receives %.6f per contract and the "
                             "action was selected on %.6f. A wire that "
                             "accepts less than the level the decision was "
                             "made on is not the same order -- on a short the "
                             "two spaces are complements and confusing them "
                             "inverts the bound" % (per_contract, want)))
    out["plan"] = {
        "us_market_slug": row["us_market_slug"], "quantity": contracts,
        "limit_price": wire, "sell": True,
        "opened_with": opened_with,
        "adapter_will_send": ("pmus._exit_intent derives SELL_SHORT from "
                              "BUY_SHORT and SELL_LONG from BUY_LONG; the "
                              "BUY intent is passed so the adapter never "
                              "sells a side we do not hold"),
        "expected_proceeds_usd": proceeds,
        "proceeds_per_contract": per_contract,
        "price_space": "VENUE_WIRE_CONTRACT_PRICE",
        "proceeds_space": ("cash received per held contract: wire on a long, "
                           "(1 - wire) on a short"),
        "selected_on_proceeds_per_contract": (
            None if expect_proceeds_per_contract is None
            else float(expect_proceeds_per_contract)),
        "rounded": {"asked": float(limit_price), "sent": wire,
                    "direction": ("FLOOR" if opened_with == SHORT
                                  else "CEIL"),
                    "why": ("the direction that cannot receive less per "
                            "contract than the plan assumed")}}
    # ── THE SERVICING BOUNDARY: OWNERSHIP, NOT ENTRY PERMISSION ─────
    #
    # THE DEFECT THIS CLOSES. This required an AFFIRMATIVE
    # `authorize_submission` -- the entry gate. So an expired grant, a revoked
    # one, a replaced approved-limit set, or `REAL_ORDER_SUBMISSION_ENABLED`
    # being off blocked the EXIT too, and the separate servicing switch bought
    # nothing: the lapse that removed permission to ADD exposure also removed
    # the ability to REDUCE it. Now the gate is the established ownership one --
    # this position's own row must belong to this account at this venue -- and
    # submission authority is reported without gating.
    gate = await FB.check_servicing(conn, intent_id=intent_id,
                                   account_id=str(row["account_id"]),
                                   venue=ven)
    out["servicing_gate"] = gate
    if not gate.get("ok"):
        return dict(out, ok=False, refusal=gate["refusal"],
                    why=gate.get("why"))
    # FOR THE RECORD ONLY. A lapsed grant is reported and does not refuse.
    approved = await _approved(conn)
    # `adds_exposure=False`: THIS IS AN EXIT. The account-wide exposure check
    # bounds exposure GROWING, and a sale cannot breach it -- so gating an exit
    # on an account-wide measurement would strand inventory exactly when the
    # venue is unreadable, which is the same failure the comment above describes
    # for an expired grant.
    auth = EX.authorize_submission(
        account_id=str(row["account_id"]), venue=ven,
        authorization=FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY)),
        approved_limits=approved, adds_exposure=False, now=at)
    out["submission_authority_for_the_record"] = {
        "valid_for_new_exposure": bool(auth.get("authorization_consumed")),
        "affirmative": bool(auth.get("ok")),
        "refusal_if_any": auth.get("refusal"),
        "does_not_gate_this_exit": True,
        "why": ("an exit reduces exposure. Gating it on the grant to ADD "
                "exposure is how an expiry strands inventory")}
    # THE ACCOUNT'S OWN STATE IS REPORTED, NOT ENFORCED, for the same reason:
    # a paused account must still be able to sell what it holds.
    sel = await FA.account_selection(conn, str(row["account_id"]))
    out["account_selection_for_the_record"] = {
        "eligible_for_new_exposure": bool(sel.get("ok")),
        "refusal_if_any": sel.get("refusal"),
        "does_not_gate_this_exit": True}
    out["would_send"] = {
        "callable": "%s.submit_fok" % ADAPTER_MODULE,
        "args": [row["us_market_slug"], wire, contracts, True],
        "kwargs": {"intent": opened_with},
        "and_then": ("the adapter's own execution_gate.authorize('submit') "
                     "and orders.create. A sell skips the preview cost "
                     "comparison, which is buy-shaped")}
    if not FUNDED_EXIT_SUBMISSION_ENABLED:
        return dict(out, ok=False, refusal=R_EXIT_DISABLED,
                    why=("every check this servicing path makes has passed "
                         "and the adapter was NOT called. This switch is "
                         "separate from the entry switch precisely so that "
                         "stopping new exposure never strands inventory"))
    # ── A SUBMISSION WITH NO ESTABLISHED DEADLINE IS REFUSED ────────
    #
    # Placed on the path that can actually reach the venue. Every refusal above
    # happens whether or not a deadline was supplied; from here on a request
    # could leave, and "no deadline" is not permission to send -- it is the
    # absence of the only thing that says these prices still exist.
    if inputs_expire_at is None:
        return dict(out, ok=False, refusal=R_NO_INPUT_DEADLINE, venue_calls=0,
                    why=("this submission carries no `inputs_expire_at`, so "
                         "there is nothing to check the book and probability "
                         "stamps against. The scheduled path computes it in "
                         "`select_exit` and passes it through; a caller that "
                         "cannot supply one has not established that its "
                         "prices are current"))
    try:
        mod = _adapter(adapter)
    except Exception as exc:                               # noqa: BLE001
        return dict(out, ok=False, refusal=R_NO_ADAPTER, error=str(exc)[:200])

    # ── THE EXIT INTENT IS RESERVED AND COMMITTED, ATOMICALLY ───────
    res = await _reserve_exit(conn, parent=intent_id, row=row, venue=ven,
                             opened_with=opened_with, wire=wire,
                             contracts=contracts, decision_ref=decision_ref)
    out["reservation"] = res
    if not res.get("ok"):
        return dict(out, ok=False, refusal=res["refusal"],
                    why=res.get("why"))
    xid = res["exit_intent_id"]
    out["exit_intent_id"] = xid

    # ── AND AGAIN, AFTER THE LOCK, IMMEDIATELY BEFORE THE SEND ──────
    #
    # THE WINDOW THIS CLOSES. `_reserve_exit` takes `SELECT ... FOR UPDATE` on
    # the position. Under contention that wait is unbounded, and the check above
    # happened before it -- so a caller could pass a live deadline, block on the
    # lock past it, and then send. The clock is read again HERE, at the last
    # instant before the one line that can reach the venue.
    #
    # AN EXPIRED RESERVATION IS RELEASED, NOT SENT. `abandon_proven_not_sent` is
    # the same path the venue-boundary denial uses: the exit row is closed as
    # never held, so the inventory it reserved returns to
    # `bettor_funded_available_to_exit` instead of sitting there blocking the
    # re-assessment that should replace it.
    send_at = time.time()
    left = float(inputs_expire_at) - send_at
    out["inputs_expiry"]["rechecked_at"] = send_at
    out["inputs_expiry"]["remaining_at_send_s"] = round(left, 3)
    out["inputs_expiry"]["waited_for_the_lock_s"] = round(send_at - at, 3)
    if left < 0:
        await FB.abandon_proven_not_sent(
            conn, xid,
            "the venue book and probability observations behind this exit "
            "expired while it waited for the position lock: %.1f s past their "
            "own deadline at the moment of sending. No request was made."
            % (-left))
        avail = await conn.fetchval(
            "SELECT bettor_funded_available_to_exit($1)::float8", intent_id)
        return dict(out, ok=False, submitted=False, venue_calls=0,
                    refusal=R_ASSESSMENT_EXPIRED,
                    reservation_released=True,
                    available_to_exit_after_release=float(avail or 0.0),
                    why=("the reservation was taken and then released without "
                         "any venue call, because the prices it rests on "
                         "expired during the wait. Sending here would have put "
                         "an order on the venue at a level that no longer "
                         "existed"))
    await FB.mark_send_attempted(conn, xid)
    out["venue_calls"] = 1
    try:
        answer = mod.submit_fok(str(row["us_market_slug"]), wire, contracts,
                                True, intent=opened_with)
    except Exception as exc:                               # noqa: BLE001
        pre_send = False
        try:
            from . import execution_gate as _eg
            pre_send = isinstance(exc, _eg.Denied)
        except Exception:                                  # noqa: BLE001
            pre_send = False
        detail = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        if pre_send:
            await FB.abandon_proven_not_sent(
                conn, xid, "the venue-boundary gate denied the exit before "
                           "any request left this process: %s" % detail)
            return dict(out, ok=False, submitted=False,
                        refusal=R_VENUE_GATE_DENIED, error=detail,
                        why=("nothing was sent, so the exit intent is "
                             "abandoned and the position is unchanged"))
        await FB.mark_unresolved(
            conn, xid,
            "the exit request left this process and raised %s -- whether the "
            "venue holds an exit order is unknown" % detail)
        return dict(out, ok=False, submitted=True,
                    refusal=R_LOST_ACKNOWLEDGEMENT, error=detail,
                    why=("the exit intent stands UNRESOLVED. Recovery asks "
                         "the venue; it is never resent from here. The "
                         "position's inventory is NOT reduced on a guess"))
    out["venue_answer"] = answer
    ack = await FB.record_acknowledgement(
        conn, xid, venue_order_id=(answer or {}).get("order_id"),
        status=(answer or {}).get("status"), raw=answer or {})
    out["acknowledgement"] = ack
    ing = await FB.ingest_fills(conn, xid, FB.executions_of(
        answer)["executions"], direction="EXIT", at=at)
    out["fills"] = ing
    after = await conn.fetchrow(
        "SELECT residual_qty::float8 AS residual, closed_at, closed_reason "
        "  FROM bettor_funded_intents WHERE intent_id=$1", intent_id)
    return dict(out, ok=bool((answer or {}).get("ok")), submitted=True,
                position_after={"residual_qty": float(after["residual"]),
                                "closed_at": after["closed_at"],
                                "closed_reason": after["closed_reason"]},
                exit_proceeds_usd=ing.get("cash_usd_from_the_ledger"))


async def cancel_outstanding(conn, *, intent_id: str, adapter=None) -> dict:
    """STOP AN OUTSTANDING FUNDED ORDER WORKING, and never infer the result.

    A cancel that the venue does not confirm leaves the intent exactly as it
    was. The state machine has no "probably cancelled", because a cancel that
    raced a fill and was read as a cancellation is how a filled position
    disappears from the book.
    """
    out = {"version": VERSION, "intent_id": intent_id, "cancelled": False,
           "venue_calls": 0,
           "what_remains_disabled": disablements()}
    # THE SAME CONDITION AS THE SUBMISSION. A cancel writes a state transition
    # whose columns may not exist, and a cancel that cannot be recorded is
    # indistinguishable afterwards from one that never happened.
    from . import bettor_funded_schema as FS

    blocked = await FS.require(conn)
    if blocked is not None:
        return dict(out, **blocked)
    row = await conn.fetchrow(
        "SELECT intent_id, us_market_slug, venue_order_id, state "
        "  FROM bettor_funded_intents WHERE intent_id=$1", intent_id)
    if row is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_POSITION)
    if not row["venue_order_id"]:
        return dict(out, ok=False,
                    refusal="THIS_INTENT_NAMES_NO_VENUE_ORDER_TO_CANCEL",
                    why=("without the venue's own order id there is nothing "
                         "to address a cancel to, and recovery is what "
                         "establishes that id"))
    if not FUNDED_EXIT_SUBMISSION_ENABLED:
        return dict(out, ok=False, refusal=R_EXIT_DISABLED,
                    would_send={"callable": "%s.cancel_order" % ADAPTER_MODULE,
                                "args": [row["venue_order_id"],
                                         row["us_market_slug"]]})
    mod = _adapter(adapter)
    got = mod.cancel_order(str(row["venue_order_id"]),
                           str(row["us_market_slug"]))
    out["venue_answer"] = got
    if not (got or {}).get("ok"):
        # UNCONFIRMED IS NOT CANCELLED. The row is untouched.
        return dict(out, ok=False,
                    refusal="THE_VENUE_DID_NOT_CONFIRM_THE_CANCEL",
                    state=row["state"],
                    why=("the intent is left exactly as it was. A cancel that "
                         "raced a fill and was recorded as a cancellation is "
                         "how filled inventory vanishes from a book"))
    await conn.execute(
        "UPDATE bettor_funded_intents SET state='CANCELLED', "
        "  resolved_at=now(), updated_at=now() "
        " WHERE intent_id=$1 AND bettor_funded_order_is_outstanding(state)",
        intent_id)
    # AND THE RESIDUAL IS RECOMPUTED, because a cancel after a partial fill
    # leaves inventory the position must keep carrying.
    residual = await FB._recompute_residual(conn, intent_id)
    if residual <= 0:
        await FB.mark_position_closed(conn, intent_id, "NEVER_HELD_ANY_INVENTORY")
    return dict(out, ok=True, cancelled=True, residual_qty=residual,
                note=("a cancel after a partial fill leaves the filled "
                      "contracts in exposure; only a zero residual closes "
                      "the position"))


# ── 3 · SETTLEMENT, FROM THE VENUE'S OWN ANSWER ──────────────────────

async def reconcile_settlement(conn, *, intent_id: str, client=None,
                               probe=None, now: float | None = None) -> dict:
    """ASK THE VENUE HOW THE MARKET RESOLVED, and close only on its answer.

    A READ. It submits nothing and stays available with every submission
    switch off, because a position that settled while the lane was paused must
    still leave the book by evidence.

    ONLY TWO READINGS ARE AUTHORITATIVE. `REPORTED_SETTLEMENT` is the venue's
    settlement endpoint stating a price, corroborated against the venue's own
    long-side price by `bettor_live_read.read_resolution` -- a contradiction
    there is UNREADABLE, not a winner. `EXPLICIT_VOID` is the venue declaring
    a void. `CONVERGED_PRICE_INFERENCE` is OUR inference from prices at 1 and
    0 and is deliberately NOT enough to close a funded position.
    """
    at = float(now if now is not None else time.time())
    out = {"version": VERSION, "at": at, "intent_id": intent_id,
           "closed": False, "is_a_read": True,
           "authoritative_readings": list(AUTHORITATIVE_TERMINAL_READINGS)}
    row = await conn.fetchrow(
        "SELECT intent_id, us_market_slug, order_intent, closed_at, "
        "       residual_qty::float8 AS residual, "
        "       quantity::float8 AS quantity, "
        "       collateral_usd::float8 AS collateral "
        "  FROM bettor_funded_intents WHERE intent_id=$1 AND kind='ENTRY'",
        intent_id)
    if row is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_POSITION)
    residual = float(row["residual"] or 0)
    if residual <= 0 or row["closed_at"] is not None:
        return dict(out, ok=False, refusal=R_NOTHING_HELD,
                    residual_qty=residual)
    fn = probe
    if fn is None:
        from . import bettor_venue_settlement_probe as SP
        fn = SP.probe
    try:
        got = fn(client, str(row["us_market_slug"])) or {}
    except Exception as exc:                               # noqa: BLE001
        return dict(out, ok=False, refusal=R_SETTLEMENT_NOT_AUTHORITATIVE,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    why="the settlement probe raised, so nothing is closed")
    reading = str(got.get("terminal_reading") or "")
    out["terminal_reading"] = reading
    out["authoritative_payout_present"] = bool(
        got.get("authoritative_payout_present"))
    out["probe_why"] = got.get("why")
    if reading not in AUTHORITATIVE_TERMINAL_READINGS:
        await conn.execute(
            "UPDATE bettor_funded_intents SET settlement=$2::jsonb, "
            "  updated_at=now() WHERE intent_id=$1", intent_id,
            _json({"terminal_reading": reading, "at": at,
                   "why": got.get("why"), "closed_anything": False}))
        return dict(out, ok=False, refusal=R_SETTLEMENT_NOT_AUTHORITATIVE,
                    why=("the venue's reading is %r, which is not one this "
                         "lane closes a funded position on. The exposure "
                         "stands" % reading))
    opened_with = str(row["order_intent"])
    if reading == "EXPLICIT_VOID":
        # A VOID RETURNS THE COLLATERAL ON WHAT IS STILL HELD -- NOT THE
        # ORIGINAL ACQUISITION COST.
        #
        # THE DEFECT THIS CLOSES. This summed the PARENT's own fills:
        # `WHERE intent_id = $1`, which reads the ENTRY's rows and none of the
        # exit children's, because an exit is its own intent. After a partial
        # exit that total is the cost of the WHOLE original clip. The exit's
        # proceeds had already been booked as EXIT_PROCEEDS, so refunding the
        # full acquisition cost on top of them made a void look PROFITABLE --
        # a fabricated gain in the one case whose correct answer is close to
        # minus the fees.
        rb = await FB.remaining_basis(conn, intent_id)
        amount = float(rb["remaining_basis_usd"])
        basis = ("the venue declared a void, so the collateral on the %s "
                 "contracts STILL HELD is returned: %s per contract from the "
                 "entry fills. Contracts already exited are not refunded -- "
                 "their proceeds are booked. Fees are not returned by this "
                 "and stay in the realised total"
                 % (rb["residual_qty"], rb["basis_per_contract"]))
        out["remaining_basis"] = rb
        reason = "VOIDED_BY_THE_VENUE"
        payout_px = None
    else:
        v = got.get("reader_verdict") or {}
        try:
            payout_px = float(v.get("settlement_price"))
        except (TypeError, ValueError):
            payout_px = None
        if payout_px is None:
            return dict(out, ok=False,
                        refusal=R_SETTLEMENT_NOT_AUTHORITATIVE,
                        why=("the reading is REPORTED and the reader states "
                             "no settlement price, so the payout is not "
                             "established"))
        if not (0.0 <= payout_px <= 1.0):
            return dict(out, ok=False,
                        refusal=R_SETTLEMENT_NOT_AUTHORITATIVE,
                        why=("the reader's settlement price %r is outside "
                             "[0, 1], so the payout is not established"
                             % payout_px))
        # THE PAYOUT IS IN THE POSITION'S OWN SPACE, through the same
        # side-aware function the entry cash used: a long is paid the long
        # price, a short is paid one minus it.
        # `settlement_cash`, not `cash_for`: at a long-side price of 0 a
        # fill's cost is 0 whatever the side, but a winning SHORT is paid in
        # full.
        amount = FB.settlement_cash(residual, payout_px, opened_with)
        basis = ("the venue's settlement endpoint reported a long-side price "
                 "of %s, corroborated against its own long side; the payout "
                 "is bettor_funded_book.settlement_cash(%s, %s, %s)"
                 % (payout_px, residual, payout_px, opened_with))
        reason = "SETTLED_BY_THE_VENUE"
    await FB.record_economic_event(
        conn, intent_id=intent_id, kind="SETTLEMENT",
        amount_usd=amount, qty=residual, at=at, basis=basis,
        evidence={"terminal_reading": reading, "payout_price": payout_px,
                  "probe_why": got.get("why")},
        event_id="fev:%s:SETTLEMENT:%s" % (intent_id, reading))
    await conn.execute(
        "UPDATE bettor_funded_intents SET settlement=$2::jsonb, "
        "  updated_at=now() WHERE intent_id=$1", intent_id,
        _json({"terminal_reading": reading, "at": at,
               "payout_price": payout_px, "payout_usd": amount,
               "why": got.get("why")}))
    closed = await FB.mark_position_closed(conn, intent_id, reason)
    return dict(out, ok=True, closed=True, closure=closed,
                settlement_usd=round(float(amount), 6),
                payout_price=payout_px, basis=basis)


# ── 3b · RE-READING WHAT WAS ALREADY SETTLED (migration 141) ─────────
#
# A LEG IS CLOSED ON THE VENUE'S READING AT THAT INSTANT, and a venue can
# correct a settlement afterwards. Nothing re-read a closed leg, so a
# correction could never reach the booked record or the pairing model's
# label built on it -- an approved model trained on a label the venue had
# since reversed stayed approved. This re-reads recently settled legs through
# the SAME probe and the SAME authoritative bar the close used, and records
# every re-read. It is a read: it rewrites no accounting and closes nothing.

#: How long after settling a leg is re-read, and how many per pass (least
#: recently re-read first, so every leg in the window is reached in turn).
RECHECK_WINDOW_S = 7 * 86400.0
RECHECKS_PER_PASS = 10
VERDICT_AGREES = "AGREES"
VERDICT_DISAGREES = "DISAGREES"
VERDICT_NOT_ESTABLISHED = "NOT_ESTABLISHED"
R_RECHECKS_UNAVAILABLE = "THE_SETTLEMENT_RECHECK_RECORD_IS_UNAVAILABLE"
_EPOCH_RE = r"^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$"


def _settlement_probe(client, slug: str) -> dict:
    """The production reader for a re-read: the probe the close used."""
    from . import bettor_venue_settlement_probe as SP
    return SP.probe(client, slug)


def recheck_verdict(booked: dict, got: dict) -> dict:
    """Does the venue's reading now match what was booked? Pure."""
    b_reading = str((booked or {}).get("terminal_reading") or "") or None
    b_px = (booked or {}).get("payout_price")
    reading = str((got or {}).get("terminal_reading") or "")
    base = {"booked_reading": b_reading, "booked_payout_price": b_px,
            "venue_reading": reading or None, "venue_payout_price": None}
    if reading not in AUTHORITATIVE_TERMINAL_READINGS:
        return dict(base, verdict=VERDICT_NOT_ESTABLISHED,
                    why=("the re-read is %r, which is not authoritative, so "
                         "it says nothing about the booked reading"
                         % (reading or None)))
    if reading == "EXPLICIT_VOID":
        agrees = b_reading == "EXPLICIT_VOID"
        return dict(base, verdict=VERDICT_AGREES if agrees
                    else VERDICT_DISAGREES,
                    why=("the venue now declares a void; %s was booked"
                         % b_reading))
    try:
        v_px = float(((got or {}).get("reader_verdict") or {})
                     .get("settlement_price"))
    except (TypeError, ValueError):
        v_px = None
    if v_px is None or v_px != v_px:
        return dict(base, verdict=VERDICT_NOT_ESTABLISHED,
                    why="the reading is REPORTED and states no price")
    base["venue_payout_price"] = v_px
    try:
        b_num = float(b_px) if b_px is not None else None
    except (TypeError, ValueError):
        b_num = None
    agrees = (b_reading == "REPORTED_SETTLEMENT" and b_num is not None
              and abs(b_num - v_px) <= 1e-9)
    return dict(base, verdict=VERDICT_AGREES if agrees else VERDICT_DISAGREES,
                why=("the venue's settlement price is now %s; %s at %s was "
                     "booked" % (v_px, b_reading, b_px)))


async def recheck_settlements(conn, *, account_id: str, venue: str,
                              now: float | None = None, probe=None,
                              client=None, window_s: float = RECHECK_WINDOW_S,
                              per_pass: int = RECHECKS_PER_PASS) -> dict:
    """RE-READ RECENTLY SETTLED FUNDED LEGS AND RECORD WHAT THE VENUE SAYS.

    A DISAGREEMENT is recorded and acted on elsewhere, by reading this record:
    the leg's group stops being a pairing-model label
    (`bettor_funded_model.LABEL_SQL`), so an approved model trained on it no
    longer reproduces and stops pricing; and the account is refused new
    exposure (`bettor_funded_activation.account_selection`) while the leg's
    newest established re-read disagrees. Exits are never gated on it.
    """
    import json

    at = float(now if now is not None else time.time())
    out = {"version": VERSION, "at": at, "is_a_read": True,
           "account_id": account_id, "rechecked": [], "disagreements": 0,
           "rewrote_accounting": False}
    if await conn.fetchval(
            "SELECT to_regclass('bettor_funded_settlement_rechecks')") is None:
        return dict(out, ok=False, refusal=R_RECHECKS_UNAVAILABLE,
                    why="migration 141 is not applied here")
    rows = await conn.fetch(
        "SELECT i.intent_id, i.us_market_slug, i.settlement, "
        "       (SELECT max(r.read_at) FROM bettor_funded_settlement_rechecks r"
        "         WHERE r.intent_id = i.intent_id) AS last_read "
        "  FROM bettor_funded_intents i "
        " WHERE i.account_id=$1 AND i.venue=$2 AND i.kind='ENTRY' "
        "   AND i.closed_at IS NOT NULL "
        "   AND (i.settlement ->> 'terminal_reading') = ANY($3::text[]) "
        "   AND (CASE WHEN (i.settlement ->> 'at') ~ $6 "
        "             THEN (i.settlement ->> 'at')::float8 END) >= $4 "
        " ORDER BY last_read NULLS FIRST, i.intent_id "
        " LIMIT $5",
        account_id, venue, list(AUTHORITATIVE_TERMINAL_READINGS),
        at - float(window_s), int(per_pass), _EPOCH_RE)
    fn = probe or _settlement_probe
    for r in rows:
        booked = r["settlement"]
        if isinstance(booked, str):
            booked = json.loads(booked)
        try:
            got = fn(client, str(r["us_market_slug"])) or {}
        except Exception as exc:                               # noqa: BLE001
            got = {"terminal_reading": None,
                   "why": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        v = recheck_verdict(booked or {}, got)
        rv = got.get("reader_verdict") or {}
        kept = {"terminal_reading": got.get("terminal_reading"),
                "why": got.get("why"),
                "reader_verdict": {k: rv.get(k) for k in (
                    "status", "corroboration", "settlement_price",
                    "settlement_price_raw", "settled_at", "error")}}
        await conn.execute(
            "INSERT INTO bettor_funded_settlement_rechecks (intent_id, "
            " read_at, booked_reading, booked_payout_price, venue_reading, "
            " venue_payout_price, verdict, why, probe) VALUES "
            " ($1, to_timestamp($2), $3, $4, $5, $6, $7, $8, $9::jsonb)",
            r["intent_id"], at, v["booked_reading"],
            None if v["booked_payout_price"] is None
            else float(v["booked_payout_price"]),
            v["venue_reading"], v["venue_payout_price"], v["verdict"],
            v["why"], _json(kept))
        out["rechecked"].append({"intent_id": r["intent_id"],
                                 "verdict": v["verdict"], "why": v["why"]})
        if v["verdict"] == VERDICT_DISAGREES:
            out["disagreements"] += 1
    return dict(out, ok=True, refusal=None)


def _json(obj) -> str:
    import json
    return json.dumps(obj, default=str)


async def _approved(conn) -> dict:
    rec = FA._obj(await FA._state(conn, FA.LIMITS_KEY)) or {}
    return (dict(rec.get("proposed") or {}) if rec.get("approved") else {})


# ── 4 · THE RECURRING PASS ───────────────────────────────────────────

#: ── WHY `manage` CAN NOW BE ASKED NOT TO DISPATCH ────────────────────
#:
#: THE DEFECT THIS EXISTS FOR, and it is the central one. `_funded_service` ran
#: `manage()` and then `pass_once()`. `manage` step 4 SELECTS AN EXIT AND
#: SUBMITS IT, so an exit could be chosen and sent before the indirect hedge was
#: ever considered -- two independent managers, the first acting first. Proving
#: "one ranking precedes the action" by calling `pass_once` directly with a
#: supplied fixture did not test that, because production never calls it that
#: way.
#:
#: With `defer_dispatch=True`, step 4 selects, records, and stops. The selection
#: travels to the one ranking that also holds the indirect candidate, and
#: whatever wins there is dispatched -- by `dispatch_selection` below, which is
#: the same `submit_exit` call, moved rather than duplicated.
#:
#: The default is False so every existing caller and test keeps its behaviour;
#: the scheduled path passes True. Reconciliation, economics repair and
#: settlement are unaffected: they are reads and a settlement close, they are
#: not the action being ranked, and deferring them would leave the loss stop
#: enforced on a stale number.
DEFER_DISPATCH_MEANS = (
    "select and record the exit, do not send it. The selection is ranked "
    "against the indirect hedge by bettor_funded_pair_cycle, and the winner is "
    "dispatched once")
R_DISPATCH_DEFERRED = "THE_EXIT_WAS_SELECTED_AND_ITS_DISPATCH_DEFERRED_TO_THE_RANKING"


async def manage(conn, *, account_id: str, venue: str, adapter=None,
                 client=None, probe=None, fee_fn=None, book_reader=None,
                 subscription=None, revalidation=None,
                 defer_dispatch: bool = False,
                 now: float | None = None) -> dict:
    """ONE MANAGEMENT CYCLE over every open funded position.

    IT MANAGES; IT DOES NOT ONLY REPORT. The earlier version reconciled, asked
    about settlement, and then emitted `needs_a_decision` -- which is servicing
    infrastructure saying a decision is owed. Nothing chose an action. Now:

      1. RECONCILE THE ORDER against the venue -- `bettor_funded_book.recover`,
         which never adopts an order it cannot prove is ours and reads the
         adapter's `executions`, so a fill that happened while this lane was
         not running lands in the book.
      2. REPAIR any fill whose economic events an interruption left unwritten,
         so realised P&L is not silently understated.
      3. ASK ABOUT SETTLEMENT for anything still held, and close only on the
         venue's own authoritative answer.
      4. SELECT AN ACTION for anything still held, through `select_exit` --
         the venue's executable book, EV_HOLD under its own freshness and
         payout-event rules, and the deployed ranking with its MIN_IMPROVEMENT
         gate. An evidenced exit is EXECUTED through `submit_exit`.
      5. RE-MEASURE the book: exposure, realised P&L and the drawdown the loss
         stop is enforced on.
      6. NAME WHAT IS MISSING, one input at a time, for anything that could not
         be decided.

    IT NEVER OPENS A POSITION. Its exits and cancels sit behind
    `FUNDED_EXIT_SUBMISSION_ENABLED`, which is off, so in this deployment step
    4 selects and then stops at that switch with the adapter uncalled. The
    reconciliation, repair and settlement reads are not gated at all.
    """
    at = float(now if now is not None else time.time())
    out = {"version": VERSION, "at": at, "account_id": account_id,
           "venue": venue, "opened_anything": False,
           "recovered": None, "settlement": [], "needs_a_decision": [],
           "selection": [], "decisions": [], "exits": [],
           # EVERY POSITION'S RANKING, keyed by intent id. Consumed by the
           # pairing supplier, which previously read a field nothing wrote.
           "management_rankings": {},
           "funded_capability": None,
           "defer_dispatch": bool(defer_dispatch),
           "defer_dispatch_means": (DEFER_DISPATCH_MEANS if defer_dispatch
                                    else None),
           # THE EXITS SELECTED BUT NOT SENT, in the shape the ranking needs.
           "deferred_exits": [],
           "what_remains_disabled": disablements()}
    # ── CAPABILITY FIRST, so a scheduled pass REPORTS a blocked schema ──
    #
    # Every read in this pass names a column migration 126 adds. On an
    # unmigrated database this function used to raise, and the scheduled caller
    # turned that into FUNDED_SERVICING_RAISED -- a traceback where the honest
    # answer is "this database cannot carry the funded book yet". It is asked
    # once, here, and reported.
    from . import bettor_funded_schema as FS

    schema = await FS.readiness(conn)
    out["funded_capability"] = schema.get("capability")
    out["schema_readiness"] = {k: schema.get(k) for k in
                               ("ok", "capability", "refusal",
                                "missing_migrations", "missing_tables",
                                "missing_columns", "missing_functions", "why")}
    if not schema.get("ok"):
        return dict(out, ok=False, refusal=schema.get("refusal"),
                    why=schema.get("why"),
                    nothing_was_reconciled_settled_or_sent=True,
                    note=("servicing reads are normally never gated -- this is "
                         "not a policy gate but an absent schema: the rows this "
                         "pass would read and write do not exist here"))
    try:
        mod = _adapter(adapter)
    except Exception as exc:                               # noqa: BLE001
        mod = None
        out["adapter_error"] = "%s: %s" % (type(exc).__name__,
                                           str(exc)[:200])
    if mod is not None:
        try:
            out["recovered"] = await FB.recover(
                conn, mod, account_id=account_id, venue=venue, now=at)
        except Exception as exc:                           # noqa: BLE001
            out["recovery_error"] = "%s: %s" % (type(exc).__name__,
                                                str(exc)[:200])
    # ── EVERY FILL'S ECONOMICS, REPAIRED IF AN EARLIER WRITE STOPPED ─
    try:
        out["economics_repair"] = await FB.repair_missing_economics(
            conn, account_id=account_id, venue=venue, at=at)
    except Exception as exc:                               # noqa: BLE001
        out["economics_repair_error"] = "%s: %s" % (type(exc).__name__,
                                                    str(exc)[:200])
    held = await open_positions(conn, account_id=account_id, venue=venue)
    out["open_positions"] = held
    for p in held:
        if not p["holding"]:
            continue
        got = await reconcile_settlement(conn, intent_id=p["intent_id"],
                                        client=client, probe=probe, now=at)
        out["settlement"].append(got)
        if got.get("closed"):
            continue
        # ── THE EXIT DECISION, TAKEN RATHER THAN DEFERRED ───────────
        pick = await select_exit(conn, p, client=client, now=at,
                                fee_fn=fee_fn, book_reader=book_reader,
                                subscription=subscription,
                                revalidation=revalidation)
        out["selection"].append(pick)
        # ── THE RANKING, FOR EVERY POSITION, WHATEVER WAS SELECTED ───
        #
        # THE DEFECT THIS CLOSES. The pairing supplier read
        # `pos["management_ranking"]` -- a key NOTHING in production ever wrote.
        # `bettor_funded_intents` has no such column, so it was always empty and
        # only a test manufactured it. When the selector chose HOLD the supplier
        # therefore had no priced HOLD to rank a hedge against, and the case that
        # matters most for pairing was unreachable through an invented field.
        #
        # `select_exit` computes the ranking for every position it examines,
        # including the ones it decides to hold. It is recorded here, keyed by
        # intent id, and handed to the supplier by the scheduled caller -- so the
        # supplier consumes a production value whatever the selector chose.
        _hv = pick.get("ev_hold") or {}
        out.setdefault("management_rankings", {})[str(p["intent_id"])] = {
            "ranking": pick.get("ranking") or {},
            "selected": pick.get("selected"),
            "selection_ok": bool(pick.get("ok")),
            "refusal": pick.get("refusal"),
            "us_market_slug": p.get("us_market_slug"),
            "residual_qty": p.get("residual"),
            # THE PROBABILITY HOLD, DIRECT_EXIT AND REDUCE WERE VALUED ON, with
            # the row it came from. The pair cycle prices the indirect
            # acquisition on this same number, so all four actions rest on one
            # primary marginal.
            "hold_probability": ({
                "probability": _hv.get("probability"),
                "source_row_id": _hv.get("source_row_id"),
                "source": _hv.get("source"),
                "probability_event": _hv.get("probability_event"),
                "payout_event_held": _hv.get("payout_event_held"),
                "status": _hv.get("status")} if _hv else None),
            # ── WHAT THE RANKING COULD NOT RANK, AND WHAT IT COULD SEND ──
            # `select_exit` puts its `not_rankable` at the TOP level, outside
            # the `ranking` projection, so HOLD_TO_SETTLEMENT, POST_COMPLEMENT,
            # MERGE, NO_BID and the rest never reached the decision record
            # (Xavier map Q4). And only the selected action had terms a plan
            # could be built from. Both travel now.
            "not_rankable": list(pick.get("not_rankable") or []),
            "executable_exit_terms": dict(
                pick.get("executable_exit_terms") or {}),
            # THE EVIDENCE THE VALUATION RESTED ON, for the decision record.
            "basis": {k: (pick.get("basis") or {}).get(k) for k in (
                "basis_per_contract", "remaining_basis_usd", "residual_qty",
                "entry_qty")},
            "ev_hold": {k: (pick.get("ev_hold") or {}).get(k) for k in (
                "status", "refusal", "probability", "probability_event",
                "payout_event_held", "age_bound_s")},
            "probability_read": pick.get("probability_read"),
            "decision_evidence": {k: (pick.get("decision_evidence")
                                      or {}).get(k) for k in (
                "valuation_row_id", "valuation_observed_at", "event_state",
                # THE SETTLEMENT RULE THE VALUATION READ, so the position's
                # settlement identity is on the record beside its payout.
                "settlement_rule", "settlement_source")},
            "inputs_expire_at": pick.get("inputs_expire_at"),
            "assessed_at": pick.get("assessed_at"),
        }
        if not pick.get("ok"):
            # A NAMED MISSING INPUT, not "needs a decision". Each of these
            # points at one thing to build or one row to supply.
            out["needs_a_decision"].append({
                "intent_id": p["intent_id"],
                "us_market_slug": p["us_market_slug"],
                "residual_qty": p["residual"],
                "what": pick["refusal"],
                "missing_input": pick.get("missing_input"),
                "why": pick.get("why")})
            continue
        if not pick.get("is_an_evidenced_exit"):
            # HOLD, BY A NAMED RULE. Recorded as a decision taken.
            out["decisions"].append({
                "intent_id": p["intent_id"], "selected": pick["selected"],
                "governing_rule": (pick.get("ranking") or {}).get(
                    "governing_rule"),
                "why": pick.get("why")})
            continue
        # ── OR HAND IT TO THE RANKING INSTEAD OF SENDING IT ────────
        #
        # This is the whole point of `defer_dispatch`. An evidenced exit is a
        # CANDIDATE, not yet an action: it has to be compared against the
        # indirect hedge on the same scale before anything is sent. The
        # selection is carried out whole -- price, quantity, proceeds, the
        # inputs' own deadline -- because `dispatch_selection` must be able to
        # send exactly this and nothing reconstructed.
        if defer_dispatch:
            out["deferred_exits"].append({
                "intent_id": p["intent_id"],
                "us_market_slug": p.get("us_market_slug"),
                "selected": pick["selected"],
                "selected_qty": pick["selected_qty"],
                "limit_price": pick["limit_price"],
                "proceeds_per_contract": pick["proceeds_per_contract"],
                "assessed_at": pick.get("assessed_at"),
                "inputs_expire_at": pick.get("inputs_expire_at"),
                "inputs_expiry_governed_by": pick.get(
                    "inputs_expiry_governed_by"),
                # THE EXIT'S OWN VALUE, in the unit the ranking compares on.
                # `select_exit` computes it; re-deriving it here would be a
                # second opinion about the same number.
                "expected_net_usd": pick.get("expected_net_usd"),
                "ranking": pick.get("ranking"),
                "why": pick.get("why")})
            out["decisions"].append({
                "intent_id": p["intent_id"], "selected": pick["selected"],
                "executed": False, "stopped_at": R_DISPATCH_DEFERRED,
                "why": ("selected and deferred to the one ranking that also "
                        "holds the indirect candidate; nothing is sent from "
                        "here")})
            continue

        # ── EXECUTE IT ─────────────────────────────────────────────
        ex = await submit_exit(conn, intent_id=p["intent_id"],
                              limit_price=pick["limit_price"],
                              quantity=pick["selected_qty"],
                              # THE WIRE AND THE PROCEEDS, BOTH, so the
                              # submission can refuse a wire that would
                              # receive less than the level it was chosen on.
                              expect_proceeds_per_contract=pick[
                                  "proceeds_per_contract"],
                              assessed_at=pick.get("assessed_at"),
                              # THE INPUTS' OWN DEADLINE, carried from the
                              # selector unchanged. This is the automated path:
                              # if it did not carry it, `submit_exit` would
                              # refuse rather than send on an unbounded
                              # assessment.
                              inputs_expire_at=pick.get("inputs_expire_at"),
                              adapter=mod, venue=venue, now=time.time())
        out["exits"].append({"intent_id": p["intent_id"],
                             "selected": pick["selected"],
                             "selected_qty": pick["selected_qty"],
                             "inputs_expire_at": pick.get("inputs_expire_at"),
                             "inputs_expiry_governed_by":
                                 pick.get("inputs_expiry_governed_by"),
                             "venue_calls": ex.get("venue_calls"),
                             "wire_limit_price": pick["limit_price"],
                             "proceeds_per_contract": pick[
                                 "proceeds_per_contract"],
                             "submitted": ex.get("submitted"),
                             "refusal": ex.get("refusal"),
                             "exit_intent_id": ex.get("exit_intent_id"),
                             "position_after": ex.get("position_after"),
                             "why": ex.get("why")})
        out["decisions"].append({
            "intent_id": p["intent_id"], "selected": pick["selected"],
            "executed": bool(ex.get("submitted")),
            "stopped_at": ex.get("refusal"),
            "why": pick.get("why")})
    out["exposure"] = await FB.exposure(conn, account_id=account_id,
                                       venue=venue)
    out["pnl"] = await FB.pnl(conn, account_id=account_id, venue=venue)
    approved = await _approved(conn)
    # THE FOUR QUANTITIES, FROM THE ONE PLACE THAT KEEPS THEM APART.
    # `loss_controls` reports realised drawdown, unrealised P&L (unknown
    # here, never zero), cash usage and worst-case outstanding exposure as
    # four separate measurements, with each control's scope, trigger and
    # response. It is read BEFORE the loss stop below so the stop is stated
    # beside its bound rather than alone -- a trigger read on its own gets
    # mistaken for a maximum loss, which is exactly what happened when the
    # $40 stop was presented as the worst case on a $100 position.
    eff_limits = (EX.effective_limits(approved)["effective"]
                  if approved else None)
    out["loss_controls"] = await FB.loss_controls(
        conn, account_id=account_id, venue=venue,
        approved_limits=eff_limits)
    if approved:
        stop = float(eff_limits.get("MAX_DRAWDOWN") or 0.0)
        dd = float(out["pnl"]["max_drawdown_usd"])
        out["loss_stop"] = {
            "rail": "MAX_DRAWDOWN", "limit_usd": stop,
            "measured_usd": dd,
            "tripped": bool(stop > 0 and dd > stop + 1e-9),
            "enforced_by": ("bettor_funded_execution.check_rails, on every "
                            "entry. A tripped stop refuses new exposure and "
                            "leaves servicing available"),
            # THE MEASUREMENT NOW INCLUDES PARTIAL EXITS, and the counts say
            # so, because "over N closed positions" was the wording that made
            # a blind measurement look complete.
            "includes_partial_exits": True,
            "partially_realised_usd":
                out["pnl"].get("partially_realised_usd"),
            "partially_realised_open_positions":
                out["pnl"].get("partially_realised_open_positions"),
            # AND IT IS NOT THE MAXIMUM LOSS.
            "is_not_a_maximum_loss": (
                "a trigger on realised results. It refuses new exposure; it "
                "cannot liquidate inventory at a price, so the loss on what "
                "is already held can exceed it"),
            "worst_case_total_loss_usd":
                out["loss_controls"]["worst_case_total_loss_usd"],
            "basis": out["pnl"]["realised_basis"]}
    else:
        out["loss_stop"] = {
            "rail": "MAX_DRAWDOWN", "limit_usd": None,
            "measured_usd": out["pnl"]["max_drawdown_usd"],
            "tripped": None,
            "includes_partial_exits": True,
            "worst_case_total_loss_usd":
                out["loss_controls"]["worst_case_total_loss_usd"],
            "why": ("no owner-approved limit set exists, so there is no "
                    "configured stop to compare the measured drawdown "
                    "against. The measurement is real; the threshold is an "
                    "owner input")}
    unres = out["pnl"].get("unresolved_intents") or []
    fees = out["pnl"].get("fee_discrepancies") or []
    for u in unres:
        out["needs_a_decision"].append({
            "intent_id": u.get("intent_id"), "what": "AN_UNRESOLVED_INTENT",
            "why": u.get("unresolved_reason")})
    for f in fees:
        out["needs_a_decision"].append({
            "intent_id": f.get("intent_id"), "fill_id": f.get("fill_id"),
            "what": "A_FEE_THAT_IS_NOT_RECONCILED",
            "fee_state": f.get("fee_state"),
            "expected_fee_usd": f.get("exp"),
            "observed_fee_usd": f.get("obs")})
    return dict(out, ok=True)


async def dispatch_selection(conn, *, selection, adapter=None, venue: str,
                             now: float | None = None) -> dict:
    """SEND ONE DEFERRED EXIT, exactly as selected. Never raises.

    THE SAME `submit_exit` CALL AS STEP 4, moved rather than duplicated -- so
    the deferred path cannot drift from the immediate one, and in particular
    cannot lose `inputs_expire_at`, without which `submit_exit` refuses to send
    on an unbounded assessment.

    `selection` is one entry from `manage(defer_dispatch=True)`'s
    `deferred_exits`. Nothing here re-derives a price, a quantity or a proceeds
    figure: a dispatcher that recomputed its own would be a second opinion about
    the number the decision was made on, which is the candidate-to-order binding
    defect in another place.
    """
    sel = dict(selection or {})
    if not sel.get("intent_id"):
        return {"ok": False, "submitted": False,
                "refusal": "NO_DEFERRED_SELECTION_TO_DISPATCH",
                "why": "dispatch was asked for with no selection"}
    try:
        mod = _adapter(adapter)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "submitted": False,
                "refusal": "ADAPTER_UNAVAILABLE",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    try:
        ex = await submit_exit(
            conn, intent_id=sel["intent_id"],
            limit_price=sel["limit_price"],
            quantity=sel["selected_qty"],
            expect_proceeds_per_contract=sel["proceeds_per_contract"],
            assessed_at=sel.get("assessed_at"),
            inputs_expire_at=sel.get("inputs_expire_at"),
            adapter=mod, venue=venue,
            now=float(now if now is not None else time.time()),
            # WHICH DECISION THIS ORDER EXECUTES, written onto the exit
            # intent. Carried, never computed here.
            decision_ref=sel.get("decision_ref"))
    except Exception as exc:                                   # noqa: BLE001
        # THE SEND'S OUTCOME IS NOT KNOWN FROM HERE. `submit_exit` handles
        # its own send exceptions; one reaching this point may have been
        # raised after the request left, so it is reported as an unknown
        # outcome (exposure preserved) rather than as a refusal.
        return {"ok": False, "submitted": None, "outcome_unknown": True,
                "refusal": "EXIT_DISPATCH_RAISED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    ack = ex.get("acknowledgement") or {}
    return {"ok": True, "submitted": bool(ex.get("submitted")),
            "refusal": ex.get("refusal"),
            "exit_intent_id": ex.get("exit_intent_id"),
            "venue_calls": ex.get("venue_calls"),
            "position_after": ex.get("position_after"),
            "dispatched": sel["selected"],
            "quantity": sel["selected_qty"],
            "limit_price": sel["limit_price"],
            # WHAT THE VENUE SAID, for the execution record: its order id,
            # the state our book recorded from its answer, and the filled
            # quantity the fills ledger now holds for this exit.
            "venue_order_id": ack.get("venue_order_id"),
            "acknowledged_state": ack.get("state"),
            "filled_qty": (ex.get("fills") or {}).get(
                "filled_qty_from_the_ledger"),
            "why": ex.get("why")}


def disablements() -> list[dict]:
    """WHAT IS OFF IN THE SERVICING LANE, and what is deliberately ON."""
    return [
        {"n": 1, "what": "FUNDED_EXIT_SUBMISSION_ENABLED",
         "where": __name__, "value": FUNDED_EXIT_SUBMISSION_ENABLED,
         "stops": "every exit and every cancel this module can send",
         "cleared_by": "a code change, separately from the entry switch"},
        {"n": 2, "what": "execution_gate inside pmus.submit_fok",
         "where": "sportsassets.pmus", "value": "process-bound",
         "stops": "any submission at the venue boundary",
         "cleared_by": "the desk's own gate state"},
        {"n": 3, "what": "PMUS_KEY_ID / PMUS_SECRET_KEY",
         "where": "the environment", "value": "absent in this deployment",
         "stops": "resolving a venue client at all",
         "cleared_by": "credentials the owner supplies"},
        {"n": 4, "what": "settlement and status reconciliation",
         "where": __name__, "value": "DELIBERATELY AVAILABLE",
         "stops": "nothing -- these are reads",
         "cleared_by": ("n/a. A book that cannot be reconciled while the "
                        "lane is paused is worse than one that cannot "
                        "trade, so servicing reads are never gated")},
        {"n": 5, "what": "the funded schema must be present",
         "where": "sportsassets.bettor_funded_schema",
         "value": "ENFORCED per call, read from the catalogue",
         "stops": ("every funded submission when a required migration, column "
                   "or function is missing -- the state a failed migration "
                   "leaves behind while the API keeps serving"),
         "cleared_by": "applying the funded migrations to that database"},
        {"n": 6, "what": "the inputs' own expiry",
         "where": "%s.input_deadline" % __name__,
         "value": ("ENFORCED twice: before anything is written and again "
                   "immediately before the send, after the position lock"),
         "stops": ("a submission whose book or probability observation has "
                   "passed the bound that admitted it. An expired reservation "
                   "is released without a venue call"),
         "cleared_by": ("re-assessment. It is not clearable by waiting: the "
                        "deadline comes from the observations, and selecting "
                        "restarts neither clock")},
    ]


def describe() -> dict:
    return {
        "version": VERSION,
        "servicing_switch_is_separate_from_the_entry_switch": True,
        "entry_switch": ("bettor_funded_execution."
                         "FUNDED_SUBMISSION_ENABLED"),
        "servicing_switch": "FUNDED_EXIT_SUBMISSION_ENABLED",
        "why_separate": ("stopping new exposure must never strand inventory. "
                         "With the entry switch off and this one on, the lane "
                         "can only reduce what it holds"),
        "exit_goes_through": "pmus.submit_fok(..., sell=True) -- the same "
                             "adapter and the same _exit_intent derivation "
                             "the desk's exits use",
        "exit_rounding": {"SELL_LONG": "CEIL", "SELL_SHORT": "FLOOR",
                          "why": "the direction that cannot receive less"},
        "exit_is_not_checked_against_the_entry_rails": (
            "an exit reduces exposure; refusing one on a capital or "
            "one-position rail is what strands inventory"),
        "settlement_authoritative_readings":
            list(AUTHORITATIVE_TERMINAL_READINGS),
        "settlement_inference_is_not_enough": (
            "CONVERGED_PRICE_INFERENCE is our reading of prices at 1 and 0, "
            "not a payout the venue reported, and it does not close a funded "
            "position"),
        "loss_stop": ("MAX_DRAWDOWN in the owner's effective limit set, "
                      "measured over bettor_funded_economics and enforced by "
                      "bettor_funded_execution.check_rails"),
        "reads_stay_available_when_submission_is_off": True,
        "what_remains_disabled": disablements(),
    }
