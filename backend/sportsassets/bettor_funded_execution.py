"""THE FUNDED EXECUTION PATH THIS EV LANE WOULD ACTUALLY USE.

WHY THIS MODULE EXISTS, STATED PLAINLY. What was demonstrated before it was
`bettor_test_venue_executor`, whose `ALLOWED_VENUE_CLASSES` is `(VENUE_TEST,)`
-- it REFUSES a funded venue by construction, and it drives an internal
simulator. That is a fine proof of the order lifecycle and it is NOT a funded
execution path. Reading it as one was the gap in the last report.

Meanwhile the EV lane's scheduled worker (`workers/ext_pinnacle_loop`) has NO
ORDER PATH AT ALL: `api/app.py` says so at its arming site -- "it reaches
`pmus.book_read` and nothing else on that module". So between a qualifying
decision and a venue there was nothing. This module is that missing segment,
and it is deliberately thin: it CONNECTS things that already exist and adds no
second execution engine.

THE CONNECTION, END TO END, in the order it is evaluated:

    workers/ext_pinnacle_loop          the schedule
      -> bettor_external_shadow        a qualifying (admissible) decision
      -> plan_from_decision            slug, intent, limit, quantity
      -> bettor_funded_activation      the CANONICAL account row, off
                                       bettor_desk_accounts (status, paused,
                                       accounting_status)
      -> bettor_funded_activation      the OWNER-APPROVED limit set
      -> bettor_entry_execution        effective_limits = MIN(frozen, approved)
      -> bettor_entry_execution        authorize_submission: account, venue,
                                       revocation, a finite expiry, and a
                                       MATCHING approved-limit digest
      -> FUNDED_SUBMISSION_ENABLED     the one switch, off in code
      -> pmus.submit_fok               THE EXISTING VENUE ADAPTER, unchanged
           -> execution_gate.authorize a second, independent boundary
           -> _get_client()            the transport seam
           -> orders.preview           the venue's own cost, compared
           -> orders.create            the order

WHAT REMAINS DISABLED, PRECISELY. Four separate things, and no one of them is
a substitute for the others:

  1. `FUNDED_SUBMISSION_ENABLED = False` in THIS module. Every check above it
     runs; the adapter is never called. Flipping it is a code change.
  2. `bettor_entry_execution.REAL_ORDER_SUBMISSION_ENABLED = False`, which is
     what `authorize_submission` refuses on after consuming a valid
     authorization. Also a code change.
  3. `execution_gate`, inside `pmus.submit_fok`, which is bound per process
     and can pause the desk independently of anything here.
  4. `PMUS_KEY_ID` / `PMUS_SECRET_KEY`. Without them `pmus._get_client()`
     raises, so even with 1-3 cleared there is no credential in this
     deployment to sign an order with.

And two things that are NOT disablement and must not be reported as such: the
absence of an eligible account (there is none), and the absence of an approved
limit set (there is none). Those are owner inputs, not switches.
"""

from __future__ import annotations

import time
import uuid

from . import bettor_entry_execution as EX
from . import bettor_funded_activation as FA
from . import bettor_funded_book as FB

VERSION = "BETTOR_FUNDED_EXECUTION_CONNECTION_V1"

#: THE SWITCH. Off. Nothing in a database, a record or an approval moves it.
FUNDED_SUBMISSION_ENABLED = False

#: The venue classes this connection is for. The mirror image of the test
#: executor's: that one takes TEST and refuses FUNDED, this one takes FUNDED
#: and refuses TEST, so neither can ever stand in for the other.
ALLOWED_VENUE_CLASSES = (FA.VENUE_FUNDED,)

#: The EXISTING adapter, named rather than imported at module scope --
#: importing `pmus` is cheap but resolving a client is not, and a module that
#: cannot be imported without credentials cannot be tested.
ADAPTER_MODULE = "sportsassets.pmus"

#: The adapter entry points this connection uses, and nothing else. Listed so
#: a reader can see the whole surface it depends on.
ADAPTER_SURFACE = ("submit_fok", "order_status", "cancel_order", "open_orders")

#: The order shape the adapter is called with. FOK, LONG only, limit.
#: `post_only` stays off: this lane's plan is a marketable take at a measured
#: vwap, not a rest.
TIF = "TIME_IN_FORCE_FILL_OR_KILL"

R_VENUE_CLASS = "THIS_CONNECTION_ONLY_RUNS_A_FUNDED_CLASS_VENUE"
R_NOT_ADMISSIBLE = "THE_DECISION_WAS_NOT_ADMITTED_SO_THERE_IS_NOTHING_TO_SEND"
R_NO_SIZED_PLAN = "THE_DECISION_CARRIES_NO_SIZED_MARKETABLE_PLAN"
R_NO_VENUE_CONTRACT = "THE_DECISION_NAMES_NO_VENUE_CONTRACT_TO_SEND_TO"
R_NO_INTENT = "THE_DECISION_NAMES_NO_SIDE_AND_THE_VENUE_WOULD_PICK_ONE"
R_LIMITS_NOT_APPROVED = "NO_OWNER_APPROVED_LIMIT_SET_IS_RECORDED"
R_OVER_RAIL = "THE_ORDER_EXCEEDS_AN_EFFECTIVE_RAIL"
R_NOT_AUTHORIZED = "THE_AUTHORIZATION_GATE_REFUSED_THIS_SUBMISSION"
R_FUNDED_DISABLED = "FUNDED_SUBMISSION_IS_DISABLED_IN_CODE"
R_NO_ADAPTER = "THE_VENUE_ADAPTER_COULD_NOT_BE_RESOLVED"
R_NO_EVENT_KEY = "THE_DECISION_NAMES_NO_CANONICAL_EVENT_KEY"
R_GATE_NOT_AFFIRMATIVE = "THE_EXECUTION_GATE_DID_NOT_AFFIRMATIVELY_ALLOW_IT"
R_RAIL_NOT_MEASURED = "AN_EFFECTIVE_RAIL_HAS_NO_MEASUREMENT"
R_ANOTHER_LIVE = FB.R_ANOTHER_INTENT_IS_LIVE
R_LOST_ACKNOWLEDGEMENT = "THE_REQUEST_LEFT_AND_THE_ANSWER_WAS_LOST"
R_VENUE_GATE_DENIED = "THE_VENUE_BOUNDARY_GATE_DENIED_IT_BEFORE_SENDING"
R_PRICE_UNREPRESENTABLE = "THE_LIMIT_PRICE_CANNOT_BE_SENT_WITHOUT_LOOSENING_THE_BOUND"

#: The one intent this lane sends. The adapter refuses an unnamed side on any
#: market whose two sides share an identifier -- every `aec-` match -- so the
#: side is always named here rather than left to a default.
LONG = "ORDER_INTENT_BUY_LONG"


def _adapter(mod=None):
    """The existing venue module, or an injected stand-in for a test.

    Injection is at the MODULE boundary, not the order boundary: a test that
    wants the real `submit_fok` to run substitutes `pmus._get_client` instead,
    which is the transport seam and the only honest place to cut.
    """
    if mod is not None:
        return mod
    import importlib
    return importlib.import_module(ADAPTER_MODULE)


def safe_cent(price: float, intent: str) -> float | None:
    """ROUND THE LIMIT IN THE DIRECTION THAT CANNOT LOOSEN THE BOUND.

    THE DEFECT THIS CLOSES. The plan rounded with `round(limit, 2)`, which
    rounds to NEAREST -- so a sized limit of 0.6351 became 0.64 and the order
    committed MORE per contract than the plan was sized and admitted for. The
    rail was checked against one number and the venue was sent another.

    The venue takes an amount formatted `%.2f`, so two decimals is what it
    will see whatever we pass; the only question is which way the last cent
    goes, and the answer depends on the side:

      * BUY_LONG  -- the limit is the most we PAY per contract, so FLOOR.
        0.6399 -> 0.63. We may miss a fill; we cannot overpay.
      * BUY_SHORT -- the wire price is the CONTRACT price and the collateral
        is (1 - price) x qty, so a HIGHER wire price commits LESS cash.
        CEIL. 0.6301 -> 0.64.

    Returns None when the rounded price leaves the tradeable open interval,
    because a bound we cannot represent is not a bound we may quietly widen.
    """
    import math

    p = float(price)
    cents = math.floor(p * 100.0) if intent == LONG else math.ceil(p * 100.0)
    out = round(cents / 100.0, 2)
    if not (0.0 < out < 1.0):
        return None
    # AND IT MUST SURVIVE THE ADAPTER'S OWN FORMATTING UNCHANGED, so the
    # number the rails were checked against is the number the venue receives.
    if float("%.2f" % out) != out:
        return None
    return out


def collateral_for(limit_price: float, quantity: int, intent: str) -> float:
    """WHAT THE VENUE TAKES, in the space the venue takes it.

    THE DEFECT THIS CLOSES. The connector computed `price x quantity` for
    every side. That is the LONG formula. `pmus.submit_fok` computes
    `expected_cost = (1 - limit_price) x quantity` on a BUY_SHORT -- the
    collateral -- and compares the venue's preview against THAT. So on a short
    the connector was checking the rails against a number the adapter does not
    use and the venue does not take: on a longshot short at 0.78 the connector
    read $0.78/contract where the venue takes $0.22, refusing a correctly
    sized order; above 0.50 it read LESS than the venue takes, which is the
    direction that lets an order through the rails and overspends.

    One formula, shared with the adapter, named here so the two cannot drift.
    """
    q = int(quantity)
    p = float(limit_price)
    return round(((1.0 - p) * q) if intent == "ORDER_INTENT_BUY_SHORT"
                 else (p * q), 6)


def plan_from_decision(rec: dict | None) -> dict:
    """WHAT WOULD BE SENT, read off a qualifying decision. Pure.

    `rec` is a `bettor_external_shadow.evaluate` record -- the same object
    `bettor_entry_inventory.plan_entry` consumes, so the funded path and the
    shadow path read ONE decision and cannot disagree about what it said.
    """
    out = {"version": VERSION, "ok": False}
    rec = dict(rec or {})
    if not rec.get("admissible"):
        return dict(out, refusal=R_NOT_ADMISSIBLE,
                    refusals=list(rec.get("refusals") or []),
                    why=("only an admitted decision reaches a venue; this "
                         "one was refused"))
    est = ((rec.get("execution_plan") or {}).get("execution")) or {}
    qty, vwap = est.get("size"), est.get("vwap")
    limit = est.get("limit_price", vwap)
    if not qty or vwap is None or limit is None:
        return dict(out, refusal=R_NO_SIZED_PLAN, execution=est,
                    why=("the admitted decision carries no sized marketable "
                         "fill, so there is no order to construct"))
    slug = (rec.get("us_market_slug") or rec.get("venue_slug")
            or (rec.get("venue") or {}).get("us_market_slug"))
    if not slug:
        return dict(out, refusal=R_NO_VENUE_CONTRACT,
                    why=("the decision names no venue contract. The adapter "
                         "is addressed by us_market_slug and nothing else"))
    # THE CANONICAL EVENT KEY, REQUIRED AND NEVER DERIVED.
    #
    # MAX_EVENT_EXPOSURE is a real rail and it can only be enforced against a
    # key. Guessing one from the slug would make the rail pass on a guess,
    # which is worse than not having it, so an intent that cannot name its
    # event is refused here.
    event_key = (rec.get("event_key") or rec.get("event_slug")
                 or (rec.get("venue") or {}).get("event_slug"))
    if not event_key:
        return dict(out, refusal=R_NO_EVENT_KEY,
                    why=("the event rail is enforced per event, so an order "
                         "that cannot name its event cannot be checked "
                         "against it. A key inferred from the slug would let "
                         "the rail pass on a guess"))
    intent = (rec.get("order_intent") or rec.get("buy_intent")
              or (rec.get("identity") or {}).get("buy_intent"))
    if intent not in (LONG, "ORDER_INTENT_BUY_SHORT"):
        return dict(out, refusal=R_NO_INTENT, intent=intent,
                    why=("every two-sided market on this venue shares one "
                         "identifier between its sides, so an unnamed side "
                         "is the venue choosing for us"))
    # THE CONTRACT COUNT IS AN INTEGER AT THE VENUE. Rounding DOWN is the only
    # safe direction: it can never commit more collateral than the plan sized.
    contracts = int(float(qty))
    if contracts < 1:
        return dict(out, refusal=R_NO_SIZED_PLAN, execution=est,
                    why=("%s contracts rounds down to nothing at this "
                         "venue's integer quantity" % qty))
    wire = safe_cent(float(limit), intent)
    if wire is None:
        return dict(out, refusal=R_PRICE_UNREPRESENTABLE,
                    asked=float(limit), intent=intent,
                    why=("%s cannot be expressed in the venue's two decimals "
                         "on this side without loosening the bound the plan "
                         "was sized against" % limit))
    # THE EVENT THIS CONTRACT WOULD PAY ON, carried from the decision and
    # never derived. `bettor_hold_value.ev_hold` refuses without it, so exit
    # management is impossible for a position whose entry did not record it --
    # and it is NOT the order intent: a BUY_SHORT pays on the complement, and
    # on a three-way book the complement is not the opposing team. The shadow
    # record computes it (`payout_is_complement`), so it is read, not guessed.
    payout_event = (rec.get("payout_event")
                    or (rec.get("identity") or {}).get("payout_event"))
    return {"version": VERSION, "ok": True, "refusal": None,
            "us_market_slug": str(slug), "event_key": str(event_key),
            "intent": intent,
            "payout_event": (None if not payout_event else str(payout_event)),
            "held_is_long": intent == LONG,
            "payout_event_note": (
                "absent here means the decision did not state it. The entry is "
                "not refused for that -- the rails and the authorization do not "
                "depend on it -- but the resulting position CANNOT be valued "
                "for an exit, and select_exit says so by name rather than "
                "deriving one"),
            "limit_price": wire, "quantity": contracts,
            "sell": False, "tif": TIF, "post_only": False,
            "collateral_usd": collateral_for(wire, contracts, intent),
            "collateral_space": ("(1 - price) x qty" if intent ==
                                 "ORDER_INTENT_BUY_SHORT" else "price x qty"),
            "sized_from": {"size": qty, "vwap": vwap,
                           "limit_price": est.get("limit_price")},
            "rounded": {"asked": float(limit), "sent": wire,
                        "direction": ("FLOOR" if intent == LONG else "CEIL"),
                        "why": ("the direction that cannot commit more than "
                                "the plan was sized for")},
            "rounded_down_because": ("the venue's quantity is an integer "
                                     "count of contracts")}


async def _approved(conn) -> dict:
    """THE OWNER-APPROVED LIMIT SET, or an empty dict when none is approved.

    An empty dict is NOT "no opinion": `authorize_submission` treats it as a
    real set whose digest differs, and `submit_for_decision` refuses on
    R_LIMITS_NOT_APPROVED before it gets that far.
    """
    rec = FA._obj(await FA._state(conn, FA.LIMITS_KEY)) or {}
    return (dict(rec.get("proposed") or {}) if rec.get("approved") else {})


async def check_rails(conn, plan: dict, effective: dict, *,
                      account_id: str, venue: str) -> dict:
    """EVERY EFFECTIVE RAIL, MEASURED AGAINST THE FUNDED BOOK.

    THE DEFECT THIS CLOSES. The connector checked MAX_MARKET_EXPOSURE and
    nothing else, so the owner's capital, event, correlated, drawdown,
    residual-inventory and capital-hours caps were all quietly unchecked at the
    one moment they matter. And the measurement counted nothing that had not
    filled -- blind to precisely the window a second order does damage in.

    So: every rail in the effective set gets a number from `bettor_funded_book`
    (which counts PENDING and IN-FLIGHT collateral at full size), and a rail
    with no measurement BLOCKS rather than passing quietly.
    """
    exp = await FB.exposure(conn, account_id=account_id, venue=venue)
    # REALISED RESULTS AND THE WORST PEAK-TO-TROUGH, from the economics
    # ledger. It was a hardcoded 0.0 with a note saying nothing had settled --
    # true on the day and useless as a loss stop, because the constant stays
    # zero after the first settlement too.
    real = await FB.realised(conn, account_id=account_id, venue=venue)
    add = float(plan["collateral_usd"])
    ev = plan["event_key"]
    mk = plan["us_market_slug"]
    per_event = dict(exp["per_event_collateral_usd"])
    per_market = dict(exp["per_market_collateral_usd"])
    measured = {
        # the per-order rail bounds THIS order plus anything already committed
        # on the same market
        "MAX_MARKET_EXPOSURE": (per_market.get(mk, 0.0) + add,
                                "this order's collateral plus live collateral "
                                "on the same market"),
        "MAX_EVENT_EXPOSURE": (per_event.get(ev, 0.0) + add,
                               "live collateral on event %r plus this order"
                               % ev),
        "MAX_CORRELATED_EXPOSURE": (
            exp["pending_and_in_flight_collateral_usd"] + add,
            "every live intent in this lane plus this order, under the "
            "worst-case correlation assumption"),
        "MAX_CAPITAL_DEPLOYED": (
            exp["filled_cash_usd"] + exp["filled_fees_usd"]
            + exp["pending_and_in_flight_collateral_usd"] + add,
            "cash and fees already out plus live collateral plus this order"),
        "MAX_RESIDUAL_INVENTORY": (
            exp["contracts_held"] + float(plan["quantity"]),
            "contracts the funded book holds plus this order's contracts"),
        "MAX_CAPITAL_HOURS": (
            exp["capital_hours_usd_h"],
            "cash x hours held, integrated over the funded fills. A new "
            "order contributes nothing until it fills"),
        "MAX_DRAWDOWN": (
            real["max_drawdown_usd"],
            "the worst peak-to-trough of the realised equity curve over %d "
            "closed funded position(s), summed from bettor_funded_economics"
            % real["closed_positions"]),
    }
    rails, over, unmeasured = [], [], []
    for rail, limit in sorted(effective.items()):
        got = measured.get(rail)
        if got is None:
            unmeasured.append(rail)
            rails.append({"rail": rail, "limit": float(limit),
                          "measured": None, "verdict": "NOT_MEASURED",
                          "why": ("this rail is in the effective set and this "
                                  "lane has no measurement for it, so it "
                                  "blocks rather than passing quietly")})
            continue
        value, basis = got
        ok = float(value) <= float(limit) + 1e-9
        rails.append({"rail": rail, "limit": float(limit),
                      "measured": round(float(value), 6),
                      "basis": basis,
                      "verdict": "WITHIN" if ok else "EXCEEDED"})
        if not ok:
            over.append(rails[-1])
    return {"rails": rails, "over": over, "unmeasured": unmeasured,
            "exposure": exp, "realised": real,
            "counted_pending_and_in_flight": True,
            "counted_residual_holdings": True,
            "realised_is_provisional": real["realised_is_provisional"],
            "every_effective_rail_was_checked": not unmeasured}


async def submit_for_decision(conn, rec: dict, *, account_id: str,
                              venue: str, adapter=None,
                              now: float | None = None) -> dict:
    """THE WHOLE PATH, refusing at the first thing that is not established.

    ORDER OF OPERATIONS, and it matters: every check that can refuse runs
    BEFORE any intent is written, so a refusal leaves no row; the intent is
    then committed BEFORE the request leaves; and a lost answer leaves that
    row for recovery rather than being retried here.
    """
    at = float(now if now is not None else time.time())
    klass = FA.venue_class(venue)
    out = {"version": VERSION, "at": at, "account_id": account_id,
           "venue": venue, "venue_class": klass,
           "adapter": ADAPTER_MODULE, "submitted": False,
           "order": None, "intent_id": None,
           "what_remains_disabled": disablements()}
    # ── THE SCHEMA THIS RESULT WOULD BE RECORDED IN ─────────────────
    #
    # FIRST, before the venue class, the plan or any rail. `start.sh` serves
    # after a failed migration on purpose, so this process can be running
    # against a database that has none of the funded columns -- which is what
    # happened on 2026-09-27. An order sent from a process that cannot record
    # the fill is the one failure with no safe recovery, so the funded
    # CAPABILITY is blocked while API availability is untouched.
    from . import bettor_funded_schema as FS

    blocked = await FS.require(conn)
    if blocked is not None:
        return dict(out, **blocked)
    if klass not in ALLOWED_VENUE_CLASSES:
        return dict(out, ok=False, refusal=R_VENUE_CLASS,
                    allowed=list(ALLOWED_VENUE_CLASSES),
                    why=("this connection is the FUNDED path; a test-class "
                         "venue belongs to bettor_test_venue_executor, and "
                         "the two are deliberately disjoint"))
    plan = plan_from_decision(rec)
    out["plan"] = plan
    if not plan.get("ok"):
        return dict(out, ok=False, refusal=plan["refusal"],
                    why=plan.get("why"))
    sel = await FA.account_selection(conn, account_id)
    out["account_selection"] = sel
    if not sel.get("ok"):
        return dict(out, ok=False, refusal=sel["refusal"],
                    why=sel.get("why"))
    approved = await _approved(conn)
    if not approved:
        return dict(out, ok=False, refusal=R_LIMITS_NOT_APPROVED,
                    why=("the effective rails are MIN(frozen, approved) and "
                         "no approved set exists, so there is nothing to "
                         "tighten the frozen rails to a pilot size"))
    eff = EX.effective_limits(approved)
    out["effective_limits"] = eff
    rails = await check_rails(conn, plan, eff["effective"],
                              account_id=sel["account_id"], venue=venue)
    out["rails"] = rails
    if rails["unmeasured"]:
        return dict(out, ok=False, refusal=R_RAIL_NOT_MEASURED,
                    unmeasured=rails["unmeasured"],
                    why=("a predeclared rail without a measurement is not a "
                         "cleared rail"))
    if rails["over"]:
        return dict(out, ok=False, refusal=R_OVER_RAIL, over=rails["over"],
                    why="; ".join(
                        "%s: $%.2f against a $%.2f rail"
                        % (o["rail"], o["measured"], o["limit"])
                        for o in rails["over"]))
    auth = EX.authorize_submission(
        account_id=sel["account_id"], venue=venue,
        authorization=FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY)),
        approved_limits=approved, now=at)
    out["authorization"] = auth
    out["owner_side_satisfied"] = bool(auth.get("authorization_consumed"))
    if not auth.get("authorization_consumed"):
        return dict(out, ok=False, refusal=R_NOT_AUTHORIZED,
                    gate_refusal=auth.get("refusal"),
                    why=("the authorization gate did not consume a record "
                         "for this account, venue and limit set"))
    # AN AFFIRMATIVE RESULT IS REQUIRED, NOT MERELY A CONSUMED RECORD.
    #
    # THE DEFECT THIS CLOSES. This asked only for `authorization_consumed`,
    # which is TRUE on the refusal `REAL_ORDER_SUBMISSION_IS_DISABLED_IN_CODE`
    # -- that flag means "the record was read and matched", and it is set
    # immediately BEFORE the constant is consulted. So with this connector
    # enabled and `REAL_ORDER_SUBMISSION_ENABLED` False the connector walked
    # straight past the execution module's own switch and called the adapter.
    # Two switches, and only one of them was gating. `ok` is the gate's
    # affirmative answer and nothing else will do.
    out["execution_gate_affirmative"] = bool(auth.get("ok"))
    if not auth.get("ok"):
        return dict(out, ok=False, refusal=R_GATE_NOT_AFFIRMATIVE,
                    gate_refusal=auth.get("refusal"),
                    why=("the authorization record was consumed and the "
                         "execution gate still did not allow the submission: "
                         "%s. A consumed record is not permission"
                         % auth.get("refusal")))
    out["would_send"] = {
        "callable": "%s.submit_fok" % ADAPTER_MODULE,
        "args": [plan["us_market_slug"], plan["limit_price"],
                 plan["quantity"], plan["sell"]],
        "kwargs": {"tif": plan["tif"], "intent": plan["intent"],
                   "post_only": plan["post_only"]},
        "and_then": ("the adapter's own execution_gate.authorize('submit'), "
                     "its orders.preview cost comparison, and orders.create")}
    if not FUNDED_SUBMISSION_ENABLED:
        return dict(out, ok=False, refusal=R_FUNDED_DISABLED,
                    why=("every check this connection makes has passed and "
                         "the adapter was NOT called. Turning this on is a "
                         "code change, and three further boundaries remain "
                         "after it"))
    try:
        mod = _adapter(adapter)
    except Exception as exc:                               # noqa: BLE001
        return dict(out, ok=False, refusal=R_NO_ADAPTER,
                    error=str(exc)[:200])
    missing = [n for n in ADAPTER_SURFACE if not hasattr(mod, n)]
    if missing:
        return dict(out, ok=False, refusal=R_NO_ADAPTER, missing=missing,
                    why="the adapter does not carry the surface this needs")

    # ── INTENT IS COMMITTED BEFORE THE REQUEST LEAVES ───────────────
    intent_id = FB.new_intent_id()
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=sel["account_id"], venue=venue,
        venue_class=klass, us_market_slug=plan["us_market_slug"],
        event_key=plan["event_key"], order_intent=plan["intent"],
        limit_price=plan["limit_price"], quantity=plan["quantity"],
        collateral_usd=plan["collateral_usd"],
        effective_digest=eff["effective_digest"],
        payout_event=plan.get("payout_event"),
        held_is_long=plan.get("held_is_long"),
        decision_ref={"admissible": True,
                      "sized_from": plan["sized_from"],
                      "payout_event": plan.get("payout_event"),
                      "authorization_at": auth.get("authorization",
                                                   {}).get("at")})
    out["intent"] = got
    if not got.get("ok"):
        # ANOTHER LIVE INTENT. The database refused the second one, which is
        # the one-position rule being ENFORCED rather than proposed.
        return dict(out, ok=False, refusal=got["refusal"], why=got.get("why"))
    out["intent_id"] = intent_id
    await FB.mark_send_attempted(conn, intent_id)

    # ── THE REQUEST ─────────────────────────────────────────────────
    try:
        answer = mod.submit_fok(
            plan["us_market_slug"], plan["limit_price"], plan["quantity"],
            plan["sell"], tif=plan["tif"], intent=plan["intent"],
            post_only=plan["post_only"])
    except Exception as exc:                               # noqa: BLE001
        # DID THE REQUEST ACTUALLY LEAVE? The two answers need opposite
        # handling, and getting this wrong in either direction is a real cost:
        # calling a pre-send refusal "unresolved" preserves exposure that
        # never existed and blocks the lane behind the one-live guard for
        # nothing; calling a post-send failure "abandoned" loses a real order.
        #
        # ONE CASE IS PROVABLY PRE-SEND: `execution_gate.Denied`, which
        # `pmus.submit_fok` raises from its FIRST statement -- before
        # `_get_client()`, before the preview, before any socket. That is our
        # own code refusing, so nothing was sent and the intent is ABANDONED.
        #
        # EVERYTHING ELSE DEFAULTS TO UNRESOLVED, which is the safe direction:
        # a timeout, a dropped connection or an unparseable answer all mean the
        # venue MAY hold an order, so the exposure stands and recovery asks.
        pre_send = False
        try:
            from . import execution_gate as _eg
            pre_send = isinstance(exc, _eg.Denied)
        except Exception:                                  # noqa: BLE001
            pre_send = False
        detail = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        if pre_send:
            await FB.abandon_proven_not_sent(
                conn, intent_id,
                "the venue-boundary gate denied the submission before any "
                "request left this process: %s" % detail)
            return dict(out, ok=False, submitted=False,
                        refusal=R_VENUE_GATE_DENIED, error=detail,
                        exposure="NONE",
                        resubmitted_anything=False,
                        why=("the adapter's own execution gate refused at its "
                             "first statement, so nothing was sent and the "
                             "intent is abandoned rather than left standing"))
        await FB.mark_unresolved(
            conn, intent_id,
            "the request left this process and raised %s -- whether the "
            "venue holds an order is unknown" % detail)
        return dict(out, ok=False, submitted=True,
                    refusal=R_LOST_ACKNOWLEDGEMENT, error=detail,
                    exposure="PRESERVED",
                    resubmitted_anything=False,
                    why=("the intent is committed and UNRESOLVED. Recovery "
                         "reconciles it against the venue; it is never "
                         "resent from here"))
    out["venue_answer"] = answer
    ack = await FB.record_acknowledgement(
        conn, intent_id, venue_order_id=(answer or {}).get("order_id"),
        status=(answer or {}).get("status"), raw=answer or {})
    out["acknowledgement"] = ack
    # THE VENUE'S OWN EXECUTIONS, INGESTED IDEMPOTENTLY.
    fills = _executions_of(answer)
    ing = await FB.ingest_fills(conn, intent_id, fills, at=at)
    out["fills"] = ing
    state = await conn.fetchval(
        "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
        intent_id)
    ok = bool((answer or {}).get("ok"))
    return dict(out, ok=ok, submitted=True,
                refusal=None if ok else (answer or {}).get("status"),
                state=state,
                order={"intent_id": intent_id,
                       "venue_order_id": ack.get("venue_order_id"),
                       "status": (answer or {}).get("status"),
                       "filled_qty_from_the_ledger": ing.get(
                           "filled_qty_from_the_ledger"),
                       "cash_usd_from_the_ledger": ing.get(
                           "cash_usd_from_the_ledger"),
                       "fees_usd_from_the_ledger": ing.get(
                           "fees_usd_from_the_ledger"),
                       "unresolved_executions": ing.get("unresolved_count")})


def _executions_of(answer: dict | None) -> list[dict]:
    """THE VENUE'S EXECUTIONS, through the funded book's ONE reader.

    This used to be a second reader with its own key names and its own
    allowlist of execution types, and recovery had a third that read a `fills`
    key `pmus.order_status` has never returned. Three readers for two venue
    shapes is how a fill during downtime became invisible, so there is now one
    -- `bettor_funded_book.executions_of` -- and this delegates to it.
    """
    return FB.executions_of(answer)["executions"]


def disablements() -> list[dict]:
    """THE FOUR THINGS THAT REMAIN OFF, each named with what clears it.

    Written as data rather than prose so a readback can print it and a test
    can assert on it, and so "disabled" is never a single vague word.
    """
    return [
        {"n": 1, "what": "FUNDED_SUBMISSION_ENABLED",
         "where": "%s" % __name__,
         "value": FUNDED_SUBMISSION_ENABLED,
         "cleared_by": "a code change",
         "effect": ("every check in this connection runs and the adapter is "
                    "never called")},
        {"n": 2, "what": "REAL_ORDER_SUBMISSION_ENABLED",
         "where": "sportsassets.bettor_entry_execution",
         "value": EX.REAL_ORDER_SUBMISSION_ENABLED,
         "cleared_by": "a code change",
         "effect": ("authorize_submission consumes a valid authorization and "
                    "then refuses on this constant")},
        {"n": 3, "what": "execution_gate", "where": "sportsassets.pmus",
         "value": "bound per process; denial RAISES inside submit_fok",
         "cleared_by": "a bound, unpaused gate at submission time",
         "effect": ("an independent boundary the venue adapter reads at the "
                    "moment of submission, never carried in by a caller")},
        {"n": 4, "what": "PMUS_KEY_ID / PMUS_SECRET_KEY",
         "where": "the service environment",
         "value": "absent in this deployment",
         "cleared_by": "provisioning a venue credential",
         "effect": "pmus._get_client() raises, so nothing can be signed"},
    ]


def describe() -> dict:
    return {
        "version": VERSION,
        "what_this_is": ("the missing segment between a qualifying EV "
                         "decision and the EXISTING venue adapter"),
        "what_this_is_not": ("a second execution engine, and not "
                             "bettor_test_venue_executor, which refuses a "
                             "funded venue and drives an internal simulator"),
        "chain": ["workers.ext_pinnacle_loop (schedule)",
                  "bettor_external_shadow.evaluate (qualifying decision)",
                  "%s.plan_from_decision" % __name__,
                  "bettor_funded_activation.account_selection (canonical row)",
                  "bettor_funded_activation.LIMITS (owner-approved set)",
                  "bettor_entry_execution.effective_limits (MIN, never raises)",
                  "bettor_entry_execution.authorize_submission",
                  "%s.FUNDED_SUBMISSION_ENABLED" % __name__,
                  "pmus.submit_fok -> execution_gate -> preview -> create"],
        "allowed_venue_classes": list(ALLOWED_VENUE_CLASSES),
        "adapter": ADAPTER_MODULE,
        "adapter_surface": list(ADAPTER_SURFACE),
        "transport_seam_for_tests": "sportsassets.pmus._get_client",
        "refusals": [R_VENUE_CLASS, R_NOT_ADMISSIBLE, R_NO_SIZED_PLAN,
                     R_NO_VENUE_CONTRACT, R_NO_INTENT,
                     R_LIMITS_NOT_APPROVED, R_OVER_RAIL, R_NOT_AUTHORIZED,
                     R_FUNDED_DISABLED, R_NO_ADAPTER],
        "what_remains_disabled": disablements(),
        "writes_no_rows": ("the inventory rows belong to "
                           "bettor_entry_inventory; this segment only "
                           "decides and calls"),
    }
