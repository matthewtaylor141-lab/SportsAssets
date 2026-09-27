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
R_NO_EXIT_SIDE = "THE_SIDE_A_CLOSE_WOULD_CONSUME_PUBLISHES_NO_EXECUTABLE_LEVEL"
R_NO_PROBABILITY = "NO_ELIGIBLE_PROBABILITY_ROW_PRICES_THIS_CONTRACT"
R_HOLD_NOT_PRICED = "EV_HOLD_IS_NOT_IDENTIFIED_SO_NO_ACTION_CAN_BEAT_HOLDING"
R_NO_BASIS = "THE_POSITION_HAS_NO_PER_CONTRACT_BASIS_TO_RANK_AGAINST"
R_NO_ADAPTER = "THE_VENUE_ADAPTER_COULD_NOT_BE_RESOLVED"
R_SETTLEMENT_NOT_AUTHORITATIVE = "THE_VENUE_STATES_NO_AUTHORITATIVE_OUTCOME"
R_NO_EXIT_PRICE = "NO_EXIT_LIMIT_WAS_SUPPLIED_AND_THIS_LANE_INVENTS_NONE"
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


async def _decision_evidence(conn, probability_row) -> dict:
    """THE FIXTURE AND SETTLEMENT EVIDENCE THE ENTRY DECISION ALREADY HAD.

    `ev_hold` needs an event state (for its freshness bound) and a settlement
    attestation (for its terminal rule). Neither is a column on the funded
    position, and reading them off the position dict -- which is what this used
    to do -- yielded None for both on every call.

    They are persisted: `external_valuations.settlement_rule` is the attested
    rule the entry was decided under, and `settlement_comparison` carries the
    fixture evidence that scoped it, including `fixture_event_state`. Both are
    read from the row this probability came from, so the exit is valued under
    the same evidence the entry was.
    """
    out = {"read": False, "event_state": "UNKNOWN", "event_state_raw": None,
           "settlement_rule": None, "settlement_supplied": False,
           "valuation_row_id": (probability_row or {}).get("id")}
    rid = out["valuation_row_id"]
    if rid is None:
        return dict(out, why=("the probability row carries no id, so the "
                              "decision evidence beside it cannot be found"))
    try:
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
    return dict(out, read=True, settlement_rule=rule,
                settlement_supplied=bool(rule),
                event_state_raw=raw_state,
                event_state=event_state_of(raw_state),
                why=("the entry decision's own persisted evidence, so the "
                     "exit is valued under the rule the entry was"))


async def select_exit(conn, position, *, client=None, now=None,
                      fee_fn=None, book_reader=None) -> dict:
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
    try:
        got = reader(use, slug) or {}
    except Exception as exc:                               # noqa: BLE001
        return dict(out, ok=False, refusal=R_BOOK_UNREADABLE,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
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
    fresh = FA.venue_book_age(got.get("marketData"), now=at)
    out["venue_book_age"] = fresh
    if not fresh.get("ok"):
        return dict(out, ok=False, refusal=R_BOOK_NOT_FRESH,
                    book_refusal=fresh.get("refusal"), why=fresh.get("why"),
                    note=("probability freshness does not establish book "
                          "freshness. This is the entry lane's own admission "
                          "policy, applied to a funded exit"))

    lad = BS.exit_ladder(got["marketData"], held_intent=opened_with)
    out["exit_ladder"] = {k: lad.get(k) for k in
                          ("ok", "refusal", "best_exit_price",
                           "best_api_price", "size_at_best",
                           "displayed_depth", "levels_read", "parse_status")}
    if not lad.get("ok"):
        return dict(out, ok=False, refusal=R_NO_EXIT_SIDE,
                    ladder_refusal=lad.get("refusal"), why=lad.get("why"))

    # ── EV_HOLD, UNDER ITS OWN RULES, WITH THE EVIDENCE IT EXPECTS ───
    prob = await HV.latest_probability(conn, us_market_slug=slug)
    out["probability_read"] = {k: prob.get(k) for k in
                               ("found", "refusal", "eligibility", "why")}
    if not prob.get("found"):
        return dict(out, ok=False, refusal=R_NO_PROBABILITY,
                    probability_refusal=prob.get("refusal"),
                    why=prob.get("why"))
    # THE FIXTURE AND SETTLEMENT EVIDENCE, FROM THE ROW THAT PRICED IT.
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
    ev = await _decision_evidence(conn, prob["row"])
    out["decision_evidence"] = {k: ev.get(k) for k in
                                ("read", "event_state", "event_state_raw",
                                 "settlement_supplied", "why")}
    hv = HV.ev_hold(qty=residual, basis_per_contract=basis_per,
                    probability_row=prob["row"], now=at,
                    payout_event_held=str(payout_event),
                    event_state=ev.get("event_state"),
                    settlement=ev.get("settlement_rule"))
    out["ev_hold"] = {k: hv.get(k) for k in
                      ("status", "refusal", "why", "ev_hold_usd",
                       "probability", "age_bound_s", "event_state",
                       "probability_event", "payout_event_held")}
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
        sale_ladder=sale)
    out["ranking"] = {k: ranked.get(k) for k in
                      ("selected", "selected_qty", "selection_reason",
                       "operating_state", "governing_rule", "runner_up",
                       "improvement_over_hold_per_contract",
                       "is_a_deliberate_hold")}
    out["not_rankable"] = ranked.get("not_rankable")
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
    return dict(out, ok=True, refusal=None, selected=sel,
                selected_qty=(None if qty is None else float(qty)),
                is_an_evidenced_exit=False,
                why=ranked.get("selection_reason"),
                note=("this IS a decision. HOLD chosen by a named rule on "
                      "observed inputs is not the same as no decision, and "
                      "the reason above says which rule"))


async def _reserve_exit(conn, *, parent: str, row, venue: str,
                        opened_with: str, wire: float,
                        contracts: int) -> dict:
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
            '{"servicing": true, "reduces_exposure": true}',
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
                      expect_proceeds_per_contract=None,
                      now: float | None = None) -> dict:
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
           "what_remains_disabled": disablements()}
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
    auth = EX.authorize_submission(
        account_id=str(row["account_id"]), venue=ven,
        authorization=FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY)),
        approved_limits=approved, now=at)
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
    try:
        mod = _adapter(adapter)
    except Exception as exc:                               # noqa: BLE001
        return dict(out, ok=False, refusal=R_NO_ADAPTER, error=str(exc)[:200])

    # ── THE EXIT INTENT IS RESERVED AND COMMITTED, ATOMICALLY ───────
    res = await _reserve_exit(conn, parent=intent_id, row=row, venue=ven,
                             opened_with=opened_with, wire=wire,
                             contracts=contracts)
    out["reservation"] = res
    if not res.get("ok"):
        return dict(out, ok=False, refusal=res["refusal"],
                    why=res.get("why"))
    xid = res["exit_intent_id"]
    out["exit_intent_id"] = xid
    await FB.mark_send_attempted(conn, xid)
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
           "what_remains_disabled": disablements()}
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
        # THE PAYOUT IS IN THE POSITION'S OWN SPACE, through the same
        # side-aware function the entry cash used: a long is paid the long
        # price, a short is paid one minus it.
        amount = FB.cash_for(residual, payout_px, opened_with)
        basis = ("the venue's settlement endpoint reported a long-side price "
                 "of %s, corroborated against its own long side; the payout "
                 "is live_executor.fill_cash(%s, %s, %s)"
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


def _json(obj) -> str:
    import json
    return json.dumps(obj, default=str)


async def _approved(conn) -> dict:
    rec = FA._obj(await FA._state(conn, FA.LIMITS_KEY)) or {}
    return (dict(rec.get("proposed") or {}) if rec.get("approved") else {})


# ── 4 · THE RECURRING PASS ───────────────────────────────────────────

async def manage(conn, *, account_id: str, venue: str, adapter=None,
                 client=None, probe=None, fee_fn=None, book_reader=None,
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
           "what_remains_disabled": disablements()}
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
                                fee_fn=fee_fn, book_reader=book_reader)
        out["selection"].append(pick)
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
        # ── EXECUTE IT ─────────────────────────────────────────────
        ex = await submit_exit(conn, intent_id=p["intent_id"],
                              limit_price=pick["limit_price"],
                              quantity=pick["selected_qty"],
                              # THE WIRE AND THE PROCEEDS, BOTH, so the
                              # submission can refuse a wire that would
                              # receive less than the level it was chosen on.
                              expect_proceeds_per_contract=pick[
                                  "proceeds_per_contract"],
                              adapter=mod, venue=venue, now=at)
        out["exits"].append({"intent_id": p["intent_id"],
                             "selected": pick["selected"],
                             "selected_qty": pick["selected_qty"],
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
    if approved:
        eff = EX.effective_limits(approved)
        stop = float(eff["effective"].get("MAX_DRAWDOWN") or 0.0)
        dd = float(out["pnl"]["max_drawdown_usd"])
        out["loss_stop"] = {
            "rail": "MAX_DRAWDOWN", "limit_usd": stop,
            "measured_usd": dd,
            "tripped": bool(stop > 0 and dd > stop + 1e-9),
            "enforced_by": ("bettor_funded_execution.check_rails, on every "
                            "entry. A tripped stop refuses new exposure and "
                            "leaves servicing available"),
            "basis": out["pnl"]["realised_basis"]}
    else:
        out["loss_stop"] = {
            "rail": "MAX_DRAWDOWN", "limit_usd": None,
            "measured_usd": out["pnl"]["max_drawdown_usd"],
            "tripped": None,
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
