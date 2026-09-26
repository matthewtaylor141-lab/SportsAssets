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
    limit = round(float(limit), 2)
    notional = round(limit * contracts, 6)
    return {"version": VERSION, "ok": True, "refusal": None,
            "us_market_slug": str(slug), "intent": intent,
            "limit_price": limit, "quantity": contracts,
            "sell": False, "tif": TIF, "post_only": False,
            "notional_usd": notional,
            "sized_from": {"size": qty, "vwap": vwap,
                           "limit_price": est.get("limit_price")},
            "rounded_down_because": ("the venue's quantity is an integer "
                                     "count of contracts")}


def _rail_check(plan: dict, effective: dict) -> dict | None:
    """The order against the rails the owner's approval produced.

    Only the two rails a SINGLE order can be judged against on its own are
    checked here -- per-order notional and the correlated-exposure cap it sits
    under. Capital deployed and drawdown are book-level and are the risk
    lane's, which is why they are NOT silently treated as cleared.
    """
    per_order = effective.get("MAX_MARKET_EXPOSURE")
    if per_order is not None and plan["notional_usd"] > float(per_order):
        return {"rail": "MAX_MARKET_EXPOSURE", "limit": float(per_order),
                "order_notional_usd": plan["notional_usd"]}
    return None


async def _approved(conn) -> dict:
    rec = FA._obj(await FA._state(conn, FA.LIMITS_KEY)) or {}
    return (dict(rec.get("proposed") or {}) if rec.get("approved") else {})


async def submit_for_decision(conn, rec: dict, *, account_id: str,
                              venue: str, adapter=None,
                              now: float | None = None) -> dict:
    """THE WHOLE PATH, in one call, refusing at the first thing that is not
    established. Nothing is written to any table here: this is the segment
    between a decision and the venue, and the rows are the inventory lane's.

    The return always names, for the record, which of the four disablements
    would stop a submission even if everything else were satisfied.
    """
    at = float(now if now is not None else time.time())
    klass = FA.venue_class(venue)
    out = {"version": VERSION, "at": at, "account_id": account_id,
           "venue": venue, "venue_class": klass,
           "adapter": ADAPTER_MODULE, "submitted": False,
           "order": None,
           "what_remains_disabled": disablements()}
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
    over = _rail_check(plan, eff["effective"])
    if over is not None:
        return dict(out, ok=False, refusal=R_OVER_RAIL, over=over,
                    why=("$%.2f of notional against a $%.2f rail"
                         % (over["order_notional_usd"], over["limit"])))
    auth = EX.authorize_submission(
        account_id=sel["account_id"], venue=venue,
        authorization=FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY)),
        approved_limits=approved, now=at)
    out["authorization"] = auth
    if not auth.get("authorization_consumed"):
        return dict(out, ok=False, refusal=R_NOT_AUTHORIZED,
                    gate_refusal=auth.get("refusal"),
                    why=("the authorization gate did not consume a record "
                         "for this account, venue and limit set"))
    # THE AUTHORIZATION IS VALID. Everything the owner controls is satisfied,
    # and what stops the order from here is code.
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
    try:                                               # pragma: no cover
        mod = _adapter(adapter)
    except Exception as exc:                           # pragma: no cover
        return dict(out, ok=False, refusal=R_NO_ADAPTER,
                    error=str(exc)[:200])
    missing = [n for n in ADAPTER_SURFACE if not hasattr(mod, n)]
    if missing:                                        # pragma: no cover
        return dict(out, ok=False, refusal=R_NO_ADAPTER, missing=missing,
                    why="the adapter does not carry the surface this needs")
    client_order_id = "fex-%s" % uuid.uuid4().hex[:14]
    out["client_order_id"] = client_order_id
    answer = mod.submit_fok(                           # pragma: no cover
        plan["us_market_slug"], plan["limit_price"], plan["quantity"],
        plan["sell"], tif=plan["tif"], intent=plan["intent"],
        post_only=plan["post_only"])
    out["venue_answer"] = answer                       # pragma: no cover
    ok = bool((answer or {}).get("ok"))                # pragma: no cover
    return dict(out, ok=ok, submitted=True,            # pragma: no cover
                refusal=None if ok else (answer or {}).get("status"),
                order={"client_order_id": client_order_id,
                       "venue_order_id": (answer or {}).get("order_id"),
                       "status": (answer or {}).get("status"),
                       "filled_shares": (answer or {}).get("filled_shares"),
                       "fill_price": (answer or {}).get("fill_price")})


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
