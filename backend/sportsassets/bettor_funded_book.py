"""THE FUNDED PILOT'S DURABLE BOOK: intent, acknowledgement, fills, recovery.

WHAT THIS IS FOR. A funded order has to survive the three things that make
real execution hard, and none of them is the happy path:

  * A LOST ACKNOWLEDGEMENT. We sent a request and never learned the answer.
    The order may be resting, may have filled, may never have arrived. The one
    thing that must not happen is a blind resubmission, so intent is COMMITTED
    BEFORE the request leaves, and recovery reconciles against the venue.

  * A RESTART. The process dies between the send and the answer. The committed
    intent is what a new process finds, and it finds it in a state that says
    "this may exist at the venue" rather than "nothing happened".

  * REPEATED DELIVERY. The venue hands us the same execution again. Fills are
    keyed by the VENUE'S own identity, so the tenth delivery writes what the
    first did.

WHAT IT REUSES rather than reimplements:
  * `live_executor.fill_cash` -- the side-aware cash a fill consumed. On a
    short the cash is (1 - price) x qty, and getting this wrong once already
    produced a phantom overspend that took a correct class of trade off the
    board for a day.
  * `calibration_fees.expected_fee` -- the DEPLOYED fee schedule, with its own
    rounding, rather than a second copy of the coefficients.
  * `bettor_desk`'s state vocabulary shape and the reconcile-don't-guess rule
    from `bettor_test_venue_executor`.

AND A SHADOW FILL CANNOT STAND IN FOR A FUNDED ONE. Funded fills live in
`bettor_funded_fills` and nowhere else; the shadow lanes' `rn1x_orders` carries
`CHECK (is_modelled)` and could not hold one of these rows if it tried. Every
read here is scoped to the funded tables, so no funded number is ever assembled
from a modelled row.
"""

from __future__ import annotations

import json
import time
import uuid

from . import calibration_fees as FEES

#: STATES IN WHICH THE ORDER IS STILL OUTSTANDING AT THE VENUE.
#:
#: FILLED IS DELIBERATELY NOT HERE, and that was never the bug. A filled order
#: is finished. The bug was reading this set as "where the exposure is", so the
#: moment an entry filled its holding vanished from every rail and the
#: one-position index released its slot -- at the exact point exposure is at its
#: MAXIMUM. Order terminality and inventory closure are two different questions
#: and they now have two different predicates, here and in migration 126.
OUTSTANDING_ORDER_STATES = ("INTENT_RECORDED", "SEND_ATTEMPTED",
                            "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNRESOLVED")

#: The old name, kept so nothing silently changes meaning. It answers the
#: OUTSTANDING-ORDER question, which is what it always computed.
LIVE_STATES = OUTSTANDING_ORDER_STATES

#: An order in one of these is no longer working. It says NOTHING about whether
#: we still hold contracts -- `holds_inventory` answers that.
TERMINAL_ORDER_STATES = ("FILLED", "CANCELLED", "REJECTED", "ABANDONED")
TERMINAL_STATES = TERMINAL_ORDER_STATES

#: The only reasons a holding may leave exposure. Both require evidence.
CLOSURE_REASONS = ("EXITED_IN_THE_MARKET", "SETTLED_BY_THE_VENUE",
                   "VOIDED_BY_THE_VENUE", "NEVER_HELD_ANY_INVENTORY")

#: Fee states. PROVISIONAL means the number is the SCHEDULE'S EXPECTATION and
#: the venue has not said what it charged.
FEE_PROVISIONAL = "PROVISIONAL"
FEE_RECONCILED = "RECONCILED"
FEE_DISAGREES = "DISAGREES"

PROVENANCE = "FUNDED_PILOT_EXECUTION"

#: What a figure reads when it cannot be established. Spelled here rather
#: than imported from `bettor_mgmt_select`, so this module's readers do not
#: pull the ranker in, and stated as a constant so "not identified" and 0
#: can never be confused by a caller matching on a string literal.
NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ─────────────────────────────────────────────────────────────────────
# A CONTROLLED DEMONSTRATION IN THE FUNDED SCHEMA IS NOT A FUNDED RESULT
# ─────────────────────────────────────────────────────────────────────
#
# `bettor_demonstration` already runs a controlled scenario through the
# SHADOW components, and it is kept out of performance by its experiment
# id at the consuming queries. The funded tables have no experiment
# column -- a funded book is keyed by (account_id, venue) -- so the same
# discipline needs the same enforcement point HERE, on the account.
#
# WHY THIS IS NOT A LABEL ON A SCREEN. `command_center` classifies every
# book it returns, so the number itself is tagged at the reader every
# consumer uses, not annotated once in a panel a later panel forgets. A
# demonstration book's `counts_toward_strategy_performance` is False --
# not None, which means "unknown" and is what a real funded book with no
# closed position reports.
#
# AND IT IS A CONVENTION, NOT A PERMISSION, exactly like the rest of the
# lane separation: nothing stops a caller writing a demonstration row
# under a production account id. What it does is make an unlabelled
# demonstration VISIBLE -- see the UNCLASSIFIED case below.
DEMONSTRATION_ACCOUNT_MARK = "DEMONSTRATION"

BOOK_CLASS_FUNDED = "FUNDED_REAL_MONEY"
BOOK_CLASS_DEMONSTRATION = "CONTROLLED_DEMONSTRATION"

WHY_A_DEMONSTRATION_BOOK_IS_SEPARATE = (
    "its prices, depth, fills and probability are CHOSEN inputs supplied "
    "to the deployed functions. It establishes that the software carries a "
    "position through its lifecycle and that the ledger adds up. It "
    "establishes NOTHING about opportunity: not that such a contract "
    "existed, not that it was priced this way, and not that it would have "
    "filled. So it is never summed into a result and never read as "
    "performance")


def is_demonstration_account(account_id) -> bool:
    """Whether a funded book is a controlled demonstration, BY ITS ID.

    The mark must appear in the account id itself, so the classification
    travels with every row rather than living in a side table that a
    later reader might not join.
    """
    return DEMONSTRATION_ACCOUNT_MARK in str(account_id or "").upper()


def classify_book(account_id) -> dict:
    """One book's class and whether it may count as performance."""
    demo = is_demonstration_account(account_id)
    return {
        "book_class": (BOOK_CLASS_DEMONSTRATION if demo
                       else BOOK_CLASS_FUNDED),
        "counts_toward_strategy_performance": False if demo else None,
        "why": (WHY_A_DEMONSTRATION_BOOK_IS_SEPARATE if demo else
                "a funded book's realised results COULD count. None means "
                "not established here -- this reader does not know whether "
                "a closed funded position exists yet, and reporting False "
                "would claim a finding it has not made"),
    }


R_ANOTHER_INTENT_IS_LIVE = "ANOTHER_FUNDED_INTENT_IS_ALREADY_LIVE"
R_NO_EXECUTION_IDENTITY = "THE_VENUE_SUPPLIED_NO_DURABLE_EXECUTION_IDENTITY"
R_NO_SUCH_INTENT = "NO_SUCH_FUNDED_INTENT"

#: RECOVERY REFUSALS. Each one leaves the intent UNRESOLVED with its exposure
#: preserved; none of them adopts anything.
R_NO_CORRELATED_ORDER = "NO_VENUE_ORDER_CORRELATES_WITH_THIS_INTENT"
R_AMBIGUOUS_CORRELATION = "SEVERAL_VENUE_ORDERS_CORRELATE_AND_NONE_IS_OURS"
R_CANDIDATE_PREDATES_US = "THE_ONLY_CORRELATED_ORDER_PREDATES_OUR_REQUEST"

#: DOES THIS VENUE LET US PUT OUR OWN IDENTITY ON A REQUEST? No.
#:
#: THE WHOLE `CreateOrderParams` SURFACE of the deployed SDK (polymarket_us
#: 0.1.2) is: marketSlug, intent, type, price, quantity, tif,
#: participateDontInitiate, goodTillTime, cashOrderQty, manualOrderIndicator,
#: synchronousExecution, maxBlockTime, slippageTolerance. There is no
#: clientOrderId, no clOrdID, no external reference, no idempotency key -- the
#: string does not appear anywhere in the package.
#:
#: WHY THAT SETTLES THE RECOVERY QUESTION. A durable request-to-venue identity
#: is the only thing that can prove an order the venue holds is the one WE
#: sent. Matching slug, side, limit and clip identifies an order with matching
#: TERMS, and a single manual order can satisfy all four -- which is precisely
#: the case the earlier `candidates[0]` rule adopted and the four-term rule
#: still adopted. So on this venue a lost acknowledgement has NOTHING to
#: reconcile against, and the correct behaviour is to stay UNRESOLVED and say
#: what would be needed. If the venue later accepts a client identity, set this
#: True, send it, and adoption becomes provable rather than inferred.
CLIENT_ORDER_IDENTITY_SUPPORTED = False

#: What we would send if it were supported, named so the change is one line.
CLIENT_ORDER_IDENTITY_FIELD = "clientOrderId"
CLIENT_ORDER_IDENTITY_WOULD_BE = "the funded intent_id, which is already "\
                                 "committed before the request leaves"

R_NO_DURABLE_IDENTITY = "THE_VENUE_ACCEPTS_NO_CLIENT_ORDER_IDENTITY_SO_OWNERSHIP_IS_NOT_PROVABLE"

#: THE FIELDS A TERM MATCH AGREES ON. They are NOT ownership -- see
#: `CLIENT_ORDER_IDENTITY_SUPPORTED`. They are computed and reported because an
#: operator reconciling by hand needs to know which orders to look at, and
#: because "nothing matched" and "one thing matched and we still cannot claim
#: it" are different facts about the same UNRESOLVED row.
#:
#: WHY THIS LIST AND NOT THE SLUG ALONE. Recovery used to take
#: `candidates[0]` off a match on `us_market_slug`, so a manual desk order, a
#: copy-lane order or another strategy's order on the same market became OURS
#: -- and with it, its fills, its cash and its place in the one-open-position
#: index. Market coincidence is not ownership. An order is ours only if every
#: term of the request we sent is present in it AND no other order matches,
#: and anything short of that stays UNRESOLVED.
CORRELATION_TERMS = ("us_market_slug", "order_intent", "limit_price",
                     "quantity")

#: How far BEFORE our send a candidate's create time may sit and still be ours.
#: Clock skew between this process and the venue is real; a manual order placed
#: an hour earlier is not.
CORRELATION_CLOCK_TOLERANCE_S = 120.0

#: EXECUTION TYPES THAT ARE NOT A FILL. Everything else carrying a positive
#: quantity and price IS one.
#:
#: WHY A DENYLIST. The submit path used an allowlist of FILL/PARTIAL_FILL, so
#: an execution the venue typed anything else -- or did not type at all -- was
#: dropped silently, and `pmus`'s own reader says the opposite: shares that
#: executed ARE the fill. A type this list does not name is reported in
#: `skipped` rather than vanishing.
NON_FILL_EXECUTION_TYPES = (
    "EXECUTION_TYPE_NEW", "EXECUTION_TYPE_REJECTED",
    "EXECUTION_TYPE_CANCELED", "EXECUTION_TYPE_CANCELLED",
    "EXECUTION_TYPE_EXPIRED", "EXECUTION_TYPE_REPLACED",
    "EXECUTION_TYPE_PENDING_CANCEL", "EXECUTION_TYPE_PENDING_REPLACE",
    "EXECUTION_TYPE_ORDER_STATUS", "EXECUTION_TYPE_UNSPECIFIED")


def _px(v):
    """A price from either shape: the venue's `{"value": ...}` money object or
    an already-parsed float."""
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


#: The venue's own commission keys on one execution, in the order the
#: adapter reads them: the per-execution amount first, the per-order total
#: second. Named here so a test can assert the reader covers both rather
#: than restating the strings.
VENUE_COMMISSION_KEYS = ("commissionNotionalCollected",
                         "commissionNotionalTotalCollected")


def _venue_commission(ex: dict):
    """`(usd, which_key)` when the venue stated a commission on this raw
    execution, else None -- NEVER a guessed zero.

    A stated 0.00 and an absent commission are different facts. The first
    is an observation the fee reconciler can compare against the schedule;
    the second leaves the booked fee provisional. Returning 0.0 for the
    absent case would silently turn "unknown" into "free".

    The amount is parsed by `pmus._commission_fields`, the adapter's own
    reader, so the bool and non-finite refusals it already enforces apply
    here too and cannot drift. If the adapter is unavailable -- which no
    production path is, but an import-light caller might be -- the
    commission is reported ABSENT rather than parsed by a second
    implementation.
    """
    if not isinstance(ex, dict):
        return None
    which = None
    for k in VENUE_COMMISSION_KEYS:
        if ex.get(k) is not None:
            which = k
            break
    if which is None:
        return None
    try:
        from . import pmus as _pmus
    except Exception:  # noqa: BLE001 — absent adapter, not a bad amount
        return None
    try:
        usd, _spread = _pmus._commission_fields(ex)
    except Exception:  # noqa: BLE001
        return None
    if usd is None:
        # The key was present and the adapter REFUSED its value (a bool, a
        # non-finite). That is not an observation, so it stays absent.
        return None
    return float(usd), which


def executions_of(payload) -> dict:
    """THE VENUE'S EXECUTIONS, in the one shape the funded book ingests.

    ONE READER FOR BOTH SHAPES, and that is the point. `pmus.submit_fok`
    answers with the venue's own response under `raw.response.executions`
    (camelCase: `lastPx` as a money object, `lastShares`, `id`), while
    `pmus.order_status` answers with `_execution_record`s under a top-level
    `executions` key (snake_case: `last_px`, `last_shares`, `commission_usd`).

    THE DEFECT THIS CLOSES. Recovery read `st.get("fills")` -- a key
    `order_status` has never returned. So the production adapter could report
    executions on a recovered order and recovery would ingest NONE of them,
    which is the fill-during-downtime case: the money moved and the book did
    not know. Two readers for two shapes is how that happened, so there is now
    one, and both call sites use it.

    A SECOND DEFECT OF THE SAME KIND, found while separating partial-exit
    P&L. This reader took the commission from `commission_usd` /
    `commissionUsd` only -- keys that exist in the ALREADY-PARSED
    `order_status` shape. The venue's own executions name it
    `commissionNotionalCollected` (per execution) or
    `commissionNotionalTotalCollected` (per order), and `submit_fok`
    attaches parsed records ONLY on the post-only mirror path; the funded
    exit path gets `raw.response.executions` untouched. So on every funded
    submit the venue's stated commission was DROPPED, `observed_fee_usd`
    came back None, and the fee was booked as the schedule's EXPECTATION in
    state PROVISIONAL -- with no discrepancy raised, because from the
    book's side the venue had simply said nothing. A venue charge that
    disagreed with the schedule could therefore never be detected on a
    funded exit. That is what `FEE_DISAGREES` exists to catch.

    THE VALUE IS PARSED BY THE ADAPTER'S OWN READER, not re-implemented
    here. `pmus._commission_fields` already refuses a bool (True is not one
    dollar) and a non-finite reading (which would fail the jsonb write),
    and a second parser would drift from it. `commission_read_from` records
    which key answered, so a caller can tell "the venue stated 0.00" from
    "the venue stated nothing" -- they are different facts and only the
    first can reconcile.
    """
    if isinstance(payload, list):
        raw = list(payload)
    else:
        p = payload or {}
        raw = p.get("executions")
        if raw is None:
            raw = ((p.get("raw") or {}).get("response") or {}).get(
                "executions")
        if raw is None:
            resp = p.get("response")
            if isinstance(resp, dict):
                raw = resp.get("executions")
        # `fills` is read LAST and only if the venue actually sent one, so a
        # caller that has such a shape is not broken by this change.
        if raw is None:
            raw = p.get("fills")
        if not isinstance(raw, list):
            raw = []
    out, skipped = [], []
    for ex in raw:
        if not isinstance(ex, dict):
            skipped.append({"why": "not an object", "value": repr(ex)[:120]})
            continue
        kind = str(ex.get("type") or "")
        if kind in NON_FILL_EXECUTION_TYPES:
            skipped.append({"why": "not a fill", "type": kind,
                            "id": ex.get("id")})
            continue
        qty = ex.get("lastShares")
        if qty is None:
            qty = ex.get("last_shares")
        price = ex.get("lastPx")
        if price is None:
            price = ex.get("last_px")
        try:
            q = float(qty or 0)
        except (TypeError, ValueError):
            q = 0.0
        p = _px(price)
        if q <= 0 or p <= 0:
            # NOT DROPPED SILENTLY: an execution the venue sent that carries
            # no usable quantity or price is a discrepancy, and `ingest_fills`
            # is what turns one into UNRESOLVED.
            skipped.append({"why": "no usable quantity or price",
                            "type": kind, "id": ex.get("id"),
                            "qty": qty, "price": price})
            continue
        rec = {"qty": q, "price": p,
               "venue_fill_id": (ex.get("id") or ex.get("executionId")
                                 or ex.get("execution_id")
                                 or ex.get("tradeId") or ex.get("trade_id")),
               "raw": ex}
        # THE VENUE'S OWN COMMISSION rides through, so the fee is reconciled
        # against what it charged rather than only what the schedule expected.
        for k in ("commission_usd", "commissionUsd"):
            if ex.get(k) is not None:
                rec["commission_usd"] = ex[k]
                rec["commission_read_from"] = k
                break
        else:
            # THE RAW VENUE SHAPE, through the adapter's own reader.
            v = _venue_commission(ex)
            if v is not None:
                rec["commission_usd"] = v[0]
                rec["commission_read_from"] = v[1]
        out.append(rec)
    return {"executions": out, "skipped": skipped,
            "read_from": ("the venue's executions list, in either the "
                          "submit-answer or the order_status shape")}


def _venue_order_id_of(o: dict):
    return str(o.get("order_id") or o.get("id") or "").strip() or None


def _epoch_of(v):
    """A venue timestamp as an epoch, or None when it cannot be read."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        import datetime as _dt
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError):
        return None


def correlate_venue_order(intent: dict, venue_orders, *, claimed=(),
                          sent_at=None) -> dict:
    """WHICH VENUE ORDERS MATCH OUR TERMS -- WHICH IS NOT WHICH ONE IS OURS.

    Answers `{"adopt": <order|None>, "refusal": <str|None>, ...}` and is pure,
    so the rule can be tested without a database or an adapter.

    `adopt` IS None ON THIS VENUE, ALWAYS, and that is the correction. This
    function used to return the single four-term match as an adoption, on the
    reasoning that slug AND side AND exact limit AND exact clip together
    identify our request. They do not. They identify an order with our TERMS,
    and one manual order placed at the same price and size satisfies every one
    of them -- so the rule adopted somebody else's order in exactly the case it
    was written to prevent, just less often than `candidates[0]` did. The
    timestamp check did not rescue it either: it falls back to the four terms
    whenever either timestamp is unreadable, which is the common case for a
    venue that did not acknowledge.

    OWNERSHIP NEEDS A DURABLE REQUEST-TO-VENUE IDENTITY, and this venue accepts
    none (`CLIENT_ORDER_IDENTITY_SUPPORTED`). So the term match is computed,
    reported, and never acted on: an operator reconciling a lost
    acknowledgement by hand needs to know which orders to look at, and "nothing
    matched" and "one thing matched and we still cannot claim it" are different
    facts about the same UNRESOLVED row.

    THE TERM ASSESSMENT, in the order it is applied:

      1. An order another funded intent has already claimed is THAT intent's,
         never a candidate here.
      2. Every term of `CORRELATION_TERMS` must agree.
      3. How many survived: none, one, or several. One is not enough.
      4. If both times are readable, whether the survivor predates our send.
         An order that already existed cannot be ours -- but the converse does
         not follow, and an unreadable timestamp establishes nothing.
    """
    claimed = {str(c) for c in (claimed or ()) if c}
    want_slug = str(intent.get("us_market_slug") or "").strip().lower()
    want_intent = str(intent.get("order_intent") or "").strip().upper()
    try:
        want_px = round(float(intent.get("limit_price") or 0), 6)
    except (TypeError, ValueError):
        want_px = None
    try:
        want_qty = round(float(intent.get("quantity") or 0), 6)
    except (TypeError, ValueError):
        want_qty = None
    report = {"terms": list(CORRELATION_TERMS), "examined": 0,
              "already_claimed_by_another_intent": [], "rejected": [],
              "matched": []}
    if not want_slug or not want_intent or not want_px or not want_qty:
        return dict(report, adopt=None, refusal=R_NO_CORRELATED_ORDER,
                    why=("this intent does not itself state every "
                         "correlation term, so nothing can be correlated "
                         "with it"))
    for o in list(venue_orders or ()):
        if not isinstance(o, dict):
            continue
        report["examined"] += 1
        oid = _venue_order_id_of(o)
        if oid and oid in claimed:
            report["already_claimed_by_another_intent"].append(oid)
            continue
        got_slug = str(o.get("us_market_slug") or o.get("marketSlug")
                       or "").strip().lower()
        got_intent = str(o.get("intent") or "").strip().upper()
        got_px = round(_px(o.get("price")), 6)
        try:
            got_qty = round(float(o.get("quantity") or 0), 6)
        except (TypeError, ValueError):
            got_qty = 0.0
        disagrees = []
        if got_slug != want_slug:
            disagrees.append("us_market_slug")
        if got_intent != want_intent:
            disagrees.append("order_intent")
        if abs(got_px - want_px) > 1e-9:
            disagrees.append("limit_price")
        if abs(got_qty - want_qty) > 1e-9:
            disagrees.append("quantity")
        if disagrees:
            report["rejected"].append({"venue_order_id": oid,
                                       "disagrees_on": disagrees})
            continue
        report["matched"].append(oid)
        report.setdefault("_candidates", []).append(o)
    cands = report.pop("_candidates", [])
    if not cands:
        return dict(report, adopt=None, refusal=R_NO_CORRELATED_ORDER,
                    why=("no order the venue holds agrees with every term of "
                         "the request this intent recorded"))
    if len(cands) > 1:
        return dict(report, adopt=None, refusal=R_AMBIGUOUS_CORRELATION,
                    why=("%d orders agree with this request and nothing "
                         "distinguishes them, so none of them is established "
                         "as ours" % len(cands)))
    only = cands[0]
    report["single_term_match"] = _venue_order_id_of(only)
    made = _epoch_of(only.get("created_at") or only.get("createTime")
                     or only.get("insertTime"))
    ours = _epoch_of(sent_at)
    if made is not None and ours is not None:
        if made < ours - CORRELATION_CLOCK_TOLERANCE_S:
            return dict(report, adopt=None,
                        refusal=R_CANDIDATE_PREDATES_US,
                        venue_created_at=made, we_sent_at=ours,
                        why=("this order existed %.0f s before our request "
                             "left, so it is somebody else's order that "
                             "happens to match" % (ours - made)))
        report["created_after_our_send"] = True
    else:
        # AND AN UNREADABLE TIMESTAMP ESTABLISHES NOTHING. It used to fall back
        # to "the four terms alone", which was the adoption path; now it simply
        # records that the one discriminator we had was unavailable.
        report["created_after_our_send"] = None
        report["time_check"] = ("one of the two timestamps was unreadable, so "
                               "not even the weak time discriminator applies")
    # ── ONE MATCH IS STILL NOT OURS ─────────────────────────────────
    #
    # Everything above narrowed the field. Nothing above proved ownership, and
    # on this venue nothing can: see CLIENT_ORDER_IDENTITY_SUPPORTED.
    if not CLIENT_ORDER_IDENTITY_SUPPORTED:
        return dict(report, adopt=None, refusal=R_NO_DURABLE_IDENTITY,
                    term_match_only=_venue_order_id_of(only),
                    would_need={
                        "field": CLIENT_ORDER_IDENTITY_FIELD,
                        "value": CLIENT_ORDER_IDENTITY_WOULD_BE,
                        "venue_accepts_it": False},
                    why=("exactly one order the venue holds agrees with every "
                         "term of our request, and a term match is not "
                         "ownership: one manual order at the same price and "
                         "size satisfies all of them. This venue's order "
                         "parameters carry no client identity, so ownership "
                         "cannot be established and this order is NOT "
                         "adopted. It is reported for manual reconciliation"))
    # Reachable only if a future venue accepts a client identity AND the sent
    # value came back on the order.
    sent_id = str(intent.get("client_identity") or "").strip()
    got_id = str(only.get("client_identity")
                 or only.get(CLIENT_ORDER_IDENTITY_FIELD) or "").strip()
    if not sent_id or sent_id != got_id:
        return dict(report, adopt=None, refusal=R_NO_DURABLE_IDENTITY,
                    term_match_only=_venue_order_id_of(only),
                    why=("the venue supports a client identity and this order "
                         "does not carry ours, so it is not ours"))
    return dict(report, adopt=only, refusal=None,
                proved_by="the client identity we sent, echoed by the venue",
                venue_order_id=_venue_order_id_of(only))


def new_intent_id() -> str:
    return "fpi-%s" % uuid.uuid4().hex[:16]


def fill_id_for(venue_order_id, venue_fill_id) -> str | None:
    """The durable funded fill identity, or None when the venue named none.

    Same rule as the test-venue executor, for the same reason: one id shared
    by every unnamed execution would let `ON CONFLICT DO NOTHING` drop distinct
    fills, and a price/quantity hash collides because two executions on a
    resting order can share both.
    """
    fid = str(venue_fill_id or "").strip()
    if not fid:
        return None
    return "fvf:%s:%s" % (str(venue_order_id or ""), fid)


def cash_for(qty: float, price: float, intent: str) -> float:
    """The cash a fill consumed, from the deployed side-aware function."""
    from .live_executor import fill_cash
    return float(fill_cash(float(qty), float(price), intent))


def fee_for(qty: float, price: float, *, at=None) -> tuple[float, str]:
    """The fee from the DEPLOYED schedule, with its basis and version named.

    `expected_fee` answers a RECORD, not a number: the charge, the schedule
    date, the coefficient, the formula and a `BLOCKER` when it will not price
    something. Taking `float()` of the record would have raised, and catching
    that as "the schedule could not price it" would have been a lie about
    whose fault it was -- so the record is read properly and a BLOCKER is
    surfaced as a blocker.
    """
    # `at` IS A DATE STRING TO THE SCHEDULE, NOT AN EPOCH.
    #
    # `expected_fee` compares `str(at) < SCHEDULE["EFFECTIVE_DATE"]`, so the
    # value has to be a `YYYY-MM-DD` the comparison means something against.
    # Passing epoch seconds made "1790467984.4" < "2026-09-17" true and the
    # schedule answered FEE_SCHEDULE_NOT_EFFECTIVE_AT_THIS_TIME -- it refused
    # to price rather than pricing wrongly, which is the schedule behaving
    # correctly and my units being wrong.
    when = at
    if when is not None and not isinstance(when, str):
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(
            float(when), _dt.timezone.utc).date().isoformat()
    got = FEES.expected_fee(price, qty, at=when) or {}
    if got.get("BLOCKER"):
        # A FEE THE SCHEDULE REFUSES TO STATE IS NOT A ZERO FEE.
        raise RuntimeError("the deployed fee schedule refuses to price %s @ "
                           "%s: %s" % (qty, price, got["BLOCKER"]))
    charge = got.get("FEE")
    if charge is None:
        raise RuntimeError("the deployed fee schedule stated no charge for "
                           "%s @ %s" % (qty, price))
    return (float(charge),
            "calibration_fees.expected_fee(%s, schedule %s, %s)"
            % (got.get("role"), got.get("schedule"), got.get("rounding")))


# ── 1 · INTENT, COMMITTED BEFORE ANYTHING IS SENT ───────────────────

async def record_intent(conn, *, intent_id: str, account_id: str, venue: str,
                        venue_class: str, us_market_slug: str,
                        event_key: str, order_intent: str,
                        limit_price: float, quantity: int,
                        collateral_usd: float, effective_digest: str,
                        decision_ref: dict | None = None,
                        payout_event: str | None = None,
                        held_is_long: bool | None = None) -> dict:
    """WRITE THE INTENT AND COMMIT IT. Nothing has been sent yet.

    The one-live-intent unique index is what makes the concurrency guarantee
    real: two callers racing here both attempt the insert and the SECOND one
    fails at the database, whatever the interleaving. A SELECT-then-INSERT
    check could not do that.
    """
    out = {"intent_id": intent_id, "state": "INTENT_RECORDED"}
    try:
        await conn.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent,"
            " limit_price, quantity, collateral_usd, effective_digest,"
            " decision_ref, state, provenance, payout_event, held_is_long,"
            " client_identity_supported) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,"
            "        'INTENT_RECORDED',$13,$14,$15,$16)",
            intent_id, str(account_id), str(venue), str(venue_class),
            str(us_market_slug), str(event_key), str(order_intent),
            float(limit_price), int(quantity), float(collateral_usd),
            str(effective_digest), json.dumps(decision_ref or {}),
            PROVENANCE,
            # THE EVENT THIS CONTRACT PAYS ON, carried from the decision. An
            # intent written without it cannot be valued later and says so
            # rather than being valued against a derived guess.
            (None if payout_event is None else str(payout_event)),
            (None if held_is_long is None else bool(held_is_long)),
            CLIENT_ORDER_IDENTITY_SUPPORTED)
    except Exception as exc:                                # noqa: BLE001
        msg = str(exc)
        # BOTH INDEX NAMES ARE MATCHED, and that is not belt-and-braces.
        # Migration 126 replaced `bettor_funded_one_live_intent` with
        # `bettor_funded_one_open_position`, and this refusal keyed off the old
        # name alone -- so after the migration a second concurrent submission
        # RAISED out of here instead of returning a clean refusal. The database
        # still refused it, which is the guarantee, but the caller saw an
        # unhandled UniqueViolationError rather than
        # ANOTHER_FUNDED_INTENT_IS_ALREADY_LIVE. The existing concurrency test
        # is what caught it.
        if ("bettor_funded_one_open_position" in msg
                or "bettor_funded_one_live_intent" in msg):
            open_now = await open_entry_positions(conn)
            return dict(out, ok=False, refusal=R_ANOTHER_INTENT_IS_LIVE,
                        live=[r["intent_id"] for r in open_now],
                        open_positions=[
                            {"intent_id": r["intent_id"], "state": r["state"],
                             "residual_qty": float(r["residual_qty"] or 0)}
                            for r in open_now],
                        why=("this lane holds one OPEN POSITION at a time and "
                             "the database enforces it. A position is open "
                             "while its order is outstanding OR it still "
                             "holds contracts, so a filled entry keeps the "
                             "slot until an evidenced exit or settlement "
                             "closes it"))
        raise
    return dict(out, ok=True, refusal=None,
                committed_before_any_request_left=True)


async def mark_send_attempted(conn, intent_id: str) -> None:
    """THE REQUEST IS ABOUT TO LEAVE. From here on a lost answer means the
    order MAY exist, and recovery must ask the venue rather than resend."""
    await conn.execute(
        "UPDATE bettor_funded_intents SET state='SEND_ATTEMPTED', "
        "  sent_at=now(), updated_at=now() "
        " WHERE intent_id=$1 AND state='INTENT_RECORDED'", intent_id)


async def abandon_before_send(conn, intent_id: str, reason: str) -> None:
    """NOTHING LEFT THIS PROCESS. Only reachable while the row is still
    INTENT_RECORDED, so it can never close a row that may hold exposure."""
    await conn.execute(
        "UPDATE bettor_funded_intents SET state='ABANDONED', "
        "  unresolved_reason=$2, resolved_at=now(), updated_at=now() "
        " WHERE intent_id=$1 AND state='INTENT_RECORDED'",
        intent_id, str(reason)[:500])


async def abandon_proven_not_sent(conn, intent_id: str, reason: str) -> dict:
    """CLOSE A ROW THAT IS `SEND_ATTEMPTED`, and only on PROOF nothing left.

    WHY THIS IS SEPARATE FROM `abandon_before_send`. That one refuses to touch
    anything past INTENT_RECORDED, which is the right default: a row that says
    "the request may have left" must not be closed on a guess. But there is one
    case where we KNOW nothing left -- `execution_gate.Denied`, raised by
    `pmus.submit_fok` at its first statement, before the client is built and
    before any socket. Leaving that row UNRESOLVED would preserve exposure that
    never existed and hold the one-live slot against nothing.

    The name says what the caller is asserting, so a future caller that does
    NOT have that proof has to notice it is claiming something.
    """
    await conn.execute(
        "UPDATE bettor_funded_intents SET state='ABANDONED', "
        "  unresolved_reason=$2, resolved_at=now(), updated_at=now() "
        " WHERE intent_id=$1 AND state IN ('INTENT_RECORDED',"
        "                                  'SEND_ATTEMPTED')",
        intent_id, str(reason)[:500])
    st = await conn.fetchval(
        "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
        intent_id)
    return {"intent_id": intent_id, "state": st, "exposure": "NONE",
            "asserted": "nothing left this process"}


async def record_acknowledgement(conn, intent_id: str, *,
                                 venue_order_id, status: str,
                                 raw: dict | None = None) -> dict:
    """THE VENUE NAMED AN ORDER. Store its id; that is the handle every later
    poll, cancel and recovery needs."""
    vid = str(venue_order_id or "").strip() or None
    state = "ACKNOWLEDGED"
    if str(status or "").lower() in ("rejected", "post_only_rejected",
                                     "bad_intent", "ambiguous_side",
                                     "preview_mismatch", "preview_unreadable",
                                     "side_unverifiable"):
        # THE VENUE REFUSED IT, and a refusal is not exposure.
        state = "REJECTED"
    await conn.execute(
        "UPDATE bettor_funded_intents SET state=$3, venue_order_id=$2, "
        "  raw=$4::jsonb, updated_at=now(), "
        "  resolved_at=CASE WHEN $3='REJECTED' THEN now() ELSE resolved_at END"
        " WHERE intent_id=$1", intent_id, vid, state,
        json.dumps(raw or {}, default=str))
    return {"intent_id": intent_id, "state": state, "venue_order_id": vid}


async def mark_unresolved(conn, intent_id: str, reason: str) -> dict:
    """WE DO NOT KNOW WHAT HAPPENED, and the exposure stands until we do.

    Deliberately NOT a terminal state: it keeps the intent live, so the
    one-live guard keeps refusing new exposure and the headroom check keeps
    counting it.
    """
    await conn.execute(
        "UPDATE bettor_funded_intents SET state='UNRESOLVED', "
        "  unresolved_reason=$2, updated_at=now() WHERE intent_id=$1",
        intent_id, str(reason)[:500])
    return {"intent_id": intent_id, "state": "UNRESOLVED",
            "exposure": "PRESERVED", "reason": reason}


# ── 2 · FILLS, KEYED BY THE VENUE'S OWN IDENTITY ────────────────────

#: DISCREPANCY KINDS. Each is a thing that cannot be true at once.
D_OVERSOLD = "AN_EXIT_SOLD_MORE_THAN_WAS_HELD"
D_ECONOMICS_MISSING = "A_FILL_HAD_NO_ECONOMIC_EVENTS"
D_FEE_DISAGREES = "THE_VENUE_CHARGED_WHAT_THE_SCHEDULE_DID_NOT_PREDICT"
D_ORPHAN_ORDER = "THE_VENUE_HOLDS_AN_ORDER_NO_INTENT_OWNS"
D_TERM_MATCH_NOT_OWNERSHIP = "A_VENUE_ORDER_MATCHES_OUR_TERMS_BUT_IS_NOT_OURS"
D_FILLED_NO_EXECUTIONS = "THE_VENUE_REPORTS_FILLED_SHARES_AND_NO_EXECUTIONS"


async def record_discrepancy(conn, *, kind: str, intent_id=None,
                             detail: dict | None = None,
                             discrepancy_id: str | None = None) -> str:
    """WRITE DOWN SOMETHING THAT CANNOT BE TRUE. Idempotent on the id.

    WHY THIS EXISTS. The alternative to a discrepancy row is a number that
    quietly becomes plausible -- `max(0, ...)` on a negative residual, a
    missing ledger entry read as "already held", an unowned venue order
    dropped from a report. Each of those is a real state the book can be in,
    and each of them reads as normal once it has been smoothed. A row an
    operator has to close cannot be smoothed.
    """
    did = discrepancy_id or "fd-%s" % uuid.uuid4().hex[:16]
    await conn.execute(
        "INSERT INTO bettor_funded_discrepancies (discrepancy_id, intent_id,"
        " kind, detail) VALUES ($1,$2,$3,$4::jsonb) "
        "ON CONFLICT (discrepancy_id) DO UPDATE SET detail=$4::jsonb",
        did, intent_id, str(kind), json.dumps(detail or {}, default=str))
    return did


async def _recompute_residual(conn, intent_id: str) -> float:
    """RESIDUAL = ENTRY FILLS - EXIT FILLS, FROM THE LEDGER, ALWAYS.

    Never incremented. A derived counter that drifts is worse than no counter,
    which is the same reason the filled quantity is recomputed rather than
    tracked. It is denormalised onto the intent only because a partial unique
    index cannot aggregate over another table.
    """
    # THE WHOLE POSITION, NOT ONE ROW OF IT. An exit is its own intent
    # (`kind='EXIT'`, `parent_intent_id` naming the position it closes) and its
    # fills are written against ITS id, so summing `intent_id=$1` alone counted
    # the entry's fills and none of the exits -- the residual never moved off
    # the full clip and the position could never close. The position is the
    # parent plus every child that points at it.
    row = await conn.fetchrow(
        "SELECT coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.qty "
        "                        ELSE 0 END),0)::float8 AS in_qty, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.qty "
        "                        ELSE 0 END),0)::float8 AS out_qty "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.intent_id=$1 OR i.parent_intent_id=$1", intent_id)
    raw = round(float(row["in_qty"]) - float(row["out_qty"]), 6)
    residual = max(0.0, raw)
    if raw < -1e-9:
        # AN OVERSELL IS NOT A ZERO RESIDUAL. `max(0, ...)` is here because the
        # column has a `>= 0` CHECK and a flat position is the common case --
        # but clamping a NEGATIVE residual to zero produces the one number that
        # reads as "flat and finished", when what actually happened is that we
        # sold contracts we could not have held. Either an entry fill was
        # missed, an exit fill was double-counted, or we are SHORT at the
        # venue. The clamp still protects the column; the discrepancy is what
        # stops it from being the whole story.
        await record_discrepancy(
            conn, kind=D_OVERSOLD, intent_id=intent_id,
            discrepancy_id="fd:%s:OVERSOLD" % intent_id,
            detail={"entry_qty": float(row["in_qty"]),
                    "exit_qty": float(row["out_qty"]),
                    "unclamped_residual": raw,
                    "stored_residual": residual,
                    "what_it_means": (
                        "exits exceed entries by %s contracts on this "
                        "position. The stored residual is clamped to 0 "
                        "because the column forbids a negative, and this row "
                        "exists so the clamp is not mistaken for flat"
                        % abs(raw)),
                    "possible_causes": [
                        "an entry fill the venue reported and we never "
                        "ingested",
                        "an exit fill counted twice under two identities",
                        "an exit that oversold because a reservation was "
                        "bypassed -- which is what "
                        "bettor_funded_available_to_exit exists to prevent"]})
    await conn.execute(
        "UPDATE bettor_funded_intents SET residual_qty=$2, updated_at=now() "
        " WHERE intent_id=$1", intent_id, residual)
    return residual


async def record_economic_event(conn, *, intent_id: str, kind: str,
                                amount_usd: float, basis: str,
                                qty=None, at: float | None = None,
                                provisional: bool = False,
                                evidence: dict | None = None,
                                event_id: str | None = None) -> str:
    """ONE CASH MOVEMENT, SIGNED. Idempotent on `event_id`.

    Realised P&L is the SUM of these rows -- it is not a constant and it is not
    derived twice in two places.
    """
    eid = event_id or "fev-%s" % uuid.uuid4().hex[:16]
    await conn.execute(
        "INSERT INTO bettor_funded_economics (event_id, intent_id, at, kind,"
        " amount_usd, qty, basis, provisional, evidence) "
        "VALUES ($1,$2,to_timestamp($3),$4,$5,$6,$7,$8,$9::jsonb) "
        "ON CONFLICT (event_id) DO NOTHING",
        eid, intent_id, float(at if at is not None else time.time()),
        str(kind), float(amount_usd),
        (None if qty is None else float(qty)), str(basis), bool(provisional),
        json.dumps(evidence or {}, default=str))
    return eid


def _observed_fee_of(f: dict):
    """THE VENUE'S OWN COMMISSION on an execution, or None.

    `pmus._execution_record` carries `commission_usd` -- the venue stating what
    it charged. None means it did not state one, which is NOT zero.
    """
    for key in ("commission_usd", "observed_fee_usd", "fee_usd"):
        if key in f and f[key] is not None:
            try:
                return abs(float(f[key]))
            except (TypeError, ValueError):
                return None
    return None


#: WHICH ORDER THE CUMULATIVE FEE WAS COMPUTED ON. Recorded per fill, because a
#: fee computed on arrival order must not be presented as one computed on the
#: venue's execution order.
ORDER_BY_VENUE_SEQUENCE = "VENUE_SEQUENCE"
ORDER_BY_VENUE_TIME = "VENUE_EXECUTED_AT"
ORDER_BY_ARRIVAL = "ARRIVAL_ORDER"

#: What each basis does and does not support.
FEE_ORDER_BASES = {
    ORDER_BY_VENUE_SEQUENCE: {
        "is_the_venues_own_order": True,
        "why_preferred": ("a sequence cannot tie, so it is a total order and "
                          "two fills can never be ambiguous"),
        "per_fill_attribution_matches_the_venue": True,
    },
    ORDER_BY_VENUE_TIME: {
        "is_the_venues_own_order": True,
        "caveat": ("two executions can share a timestamp. Where they do, "
                   "fill_id breaks the tie -- deterministic, and not the "
                   "venue's own tie-break, so the attribution between those "
                   "two fills specifically is not established"),
        "per_fill_attribution_matches_the_venue": "EXCEPT_ON_TIES",
    },
    ORDER_BY_ARRIVAL: {
        "is_the_venues_own_order": False,
        "why": ("`at` is written from OUR clock at ingest. Arrival order is "
                "not execution order: executions can be delivered out of "
                "order, redelivered, or arrive together after a reconnect"),
        "consequence": ("the order TOTAL is unaffected -- the cap is a "
                        "function of the multiset of (qty, price) -- but the "
                        "PER-FILL amounts may differ from the venue's, so a "
                        "fill-by-fill reconciliation can disagree while the "
                        "total agrees"),
        "per_fill_attribution_matches_the_venue": False,
        "and_this_is_what_I_had": (
            "I ordered by `at, fill_id` and called it deterministic. It is -- "
            "and deterministic is not correct. A stable wrong order is still a "
            "wrong order; stability only stops the number changing between "
            "runs"),
    },
}


def venue_execution_order(row) -> tuple:
    """The venue's own stated position for one fill, and on what basis.

    Returns `(basis, sort_key)`. The sort key always ends with `fill_id` so the
    order is TOTAL -- two fills with no venue ordering at all still sort
    reproducibly, which is what keeps a re-run from producing different per-fill
    numbers for the same facts.
    """
    seq = row.get("venue_sequence")
    vat = row.get("venue_executed_at")
    fid = str(row.get("fill_id") or "")
    if seq is not None:
        return (ORDER_BY_VENUE_SEQUENCE, (0, float(seq), 0.0, fid))
    if vat is not None:
        ts = vat.timestamp() if hasattr(vat, "timestamp") else float(vat)
        return (ORDER_BY_VENUE_TIME, (1, 0.0, ts, fid))
    at = row.get("at")
    ts = at.timestamp() if hasattr(at, "timestamp") else float(at or 0.0)
    return (ORDER_BY_ARRIVAL, (2, 0.0, ts, fid))


async def prior_taker_legs(conn, intent_id: str, direction: str) -> list:
    """THIS ORDER'S ALREADY-INGESTED FILLS, IN THE VENUE'S OWN ORDER.

    WHY THE EXPECTATION NEEDS THEM. The venue charges an order, not a fill: each
    fill pays its banker's-rounded fee ADJUSTED so the order's total never
    exceeds the banker's rounding of the cumulative exact fee. A fill's expected
    fee therefore depends on what the order has already been charged, which means
    it cannot be computed from the fill alone -- and `reconcile_fee` was computing
    it from the fill alone.

    WHOSE ORDER, AND THIS IS THE PART I HAD WRONG. The cap is applied by the
    VENUE, in the venue's execution order. `bettor_funded_fills.at` is OUR clock,
    written at ingest, so ordering by it gives ARRIVAL order -- and arrival order
    is not execution order. So the sequence is built from, in preference:

        venue_sequence      the venue's own sequence number. A total order.
        venue_executed_at   the venue's own instant, with fill_id breaking ties.
        at, fill_id         ARRIVAL ORDER, used only when the venue stated
                            neither -- and REPORTED as such, so a fee computed
                            this way is never presented as the venue's.

    READ FROM THE TABLE, NOT FROM MEMORY, AND THAT IS THE POINT. Three
    properties fall out of deriving the sequence from persisted rows:

      RESTART        a worker that dies mid-order and comes back reads the same
                     prior fills, so the fourth fill is priced as the fourth.
      REDELIVERY     a duplicate delivery of fill 2 finds fill 2 already present
                     (excluded by id at the call site), so it is priced against
                     the same prior sequence and yields the same number.
      LATE ARRIVAL   a fill that arrives out of order takes its VENUE position in
                     the sequence, not its arrival position -- which is the whole
                     reason the basis matters.

    Each element is `(qty, price, fill_id)`, ordered. The mixed basis for the
    order as a whole is reported by `order_fee_basis` below.
    """
    rows = await conn.fetch(
        "SELECT fill_id, qty::float8 AS q, price::float8 AS p, at, "
        "       venue_sequence, venue_executed_at "
        "  FROM bettor_funded_fills "
        " WHERE intent_id=$1 AND direction=$2", intent_id, direction)
    decorated = []
    for r in rows:
        row = dict(r)
        basis, key = venue_execution_order(row)
        decorated.append((key, basis, float(r["q"]), float(r["p"]),
                          r["fill_id"]))
    decorated.sort(key=lambda t: t[0])
    return [(q, p, fid) for _k, _b, q, p, fid in decorated]


async def prior_taker_legs_keyed(conn, intent_id: str, direction: str) -> list:
    """The same sequence, CARRYING ITS SORT KEYS, for placement.

    `prior_taker_legs` returns the caller-facing triple and is what every reader
    wants. Placing a NEW fill among them needs the keys as well, and recomputing
    them in the caller would be a second copy of the ordering rule -- which is
    exactly how two mechanisms drift apart. So both come from here.
    """
    rows = await conn.fetch(
        "SELECT fill_id, qty::float8 AS q, price::float8 AS p, at, "
        "       venue_sequence, venue_executed_at "
        "  FROM bettor_funded_fills "
        " WHERE intent_id=$1 AND direction=$2", intent_id, direction)
    out = []
    for r in rows:
        _basis, key = venue_execution_order(dict(r))
        out.append((key, float(r["q"]), float(r["p"]), r["fill_id"]))
    out.sort(key=lambda t: t[0])
    return out


async def order_fee_basis(conn, intent_id: str, direction: str) -> dict:
    """WHICH ORDER THE WHOLE ORDER'S FEES REST ON, and whether it is the venue's.

    THE WEAKEST LINK GOVERNS. An order whose fills are mostly sequenced by the
    venue but which contains ONE fill with no venue ordering is an order whose
    per-fill attribution is not established -- inserting that fill anywhere
    changes its neighbours' amounts. So the basis reported for the order is the
    weakest of its fills', not the most common.
    """
    rows = await conn.fetch(
        "SELECT fill_id, at, venue_sequence, venue_executed_at "
        "  FROM bettor_funded_fills "
        " WHERE intent_id=$1 AND direction=$2", intent_id, direction)
    bases = [venue_execution_order(dict(r))[0] for r in rows]
    if not bases:
        return {"basis": None, "fills": 0,
                "is_the_venues_own_order": None,
                "why": "no fills have been ingested for this order"}
    rank = {ORDER_BY_VENUE_SEQUENCE: 0, ORDER_BY_VENUE_TIME: 1,
            ORDER_BY_ARRIVAL: 2}
    weakest = max(bases, key=lambda b: rank[b])
    info = FEE_ORDER_BASES[weakest]
    return {
        "basis": weakest,
        "fills": len(bases),
        "per_fill_basis_counts": {b: bases.count(b) for b in set(bases)},
        "is_the_venues_own_order": info["is_the_venues_own_order"],
        "per_fill_attribution_matches_the_venue":
            info["per_fill_attribution_matches_the_venue"],
        "the_weakest_link_governs": (
            "one fill with no venue ordering makes the whole order's per-fill "
            "attribution unestablished, because where that fill sits changes "
            "its neighbours' amounts"),
        "the_total_is_unaffected": (
            "the cap is a function of the multiset of (qty, price), so the "
            "order TOTAL is the same under any ordering. Only the per-fill "
            "split moves"),
    }


def order_expected_fees(legs) -> dict:
    """THE PUBLISHED PER-FILL TAKER EXPECTATION for a whole order's legs.

    `legs` is [(qty, price), ...] in fill order. Returns the schedule's own
    per-fill collected amounts under the running cumulative cap, so the caller
    can attribute the LAST leg's amount to the fill it is ingesting.

    A BLOCKER is returned rather than a number the schedule would not state.
    """
    from . import calibration_fees as CF
    got = CF.order_fees(None, [(q, p) for q, p in legs])
    if got.get("BLOCKER") or got.get("TOTAL") is None:
        return {"BLOCKER": got.get("BLOCKER") or "NO_TOTAL", "per_fill": None}
    return {
        "BLOCKER": None,
        "per_fill": [float(x["collected"]) for x in got["per_fill"]],
        "TOTAL": float(got["TOTAL"]),
        "cumulative_cap": float(got["cumulative_cap"]),
        "adjusted": [bool(x["adjusted"]) for x in got["per_fill"]],
        "algorithm": got["algorithm"],
    }


def _insert_by_venue_order(keyed, key) -> dict:
    """WHERE THIS FILL SITS AMONG THE ONES ALREADY STORED, by the venue's order.

    THE DEFECT THIS CLOSES, AND IT IS THE ONE I ALMOST SHIPPED. My first version
    appended the new fill to the priors and priced it as the LAST leg. That is
    correct only when fills arrive in execution order. A fill delivered late --
    whose venue sequence puts it second of four -- would have been priced as the
    fourth, and the cap's adjustment attributed to the wrong fill.

    "Arrival order is not automatically venue execution order" cuts both ways: it
    is not enough to SORT the stored legs correctly, the new fill has to be
    PLACED correctly among them.

    `keyed` is `[(sort_key, qty, price, fill_id), ...]` already in venue order.
    Returns `before` -- what the order had been charged when this fill executed --
    and `after`, the fills that execute AFTER it and whose own amounts therefore
    change.
    """
    idx = 0
    for k, _q, _p, _f in keyed:
        if k > key:
            break
        idx += 1
    trio = [(q, p, f) for _k, q, p, f in keyed]
    return {"before": trio[:idx], "after": trio[idx:], "index": idx,
            "placed_last": idx == len(trio),
            "and_a_late_fill_is_not_appended": (
                "placement is by the venue's own key, so a fill delivered out "
                "of order is priced at its execution position")}


def venue_order_fields(f: dict) -> dict:
    """THE VENUE'S OWN SEQUENCE AND EXECUTION INSTANT, from its execution report.

    Both are optional and BOTH ABSENT IS A REAL ANSWER, not a parse failure: it
    means the venue told us neither, and the cumulative fee for that order then
    rests on arrival order and says so.

    Names are tried in the order the venue's own documentation and the pinned
    SDK use. An unparseable value is discarded rather than coerced -- guessing a
    sequence would put a fill in the wrong place in the cap.
    """
    out = {"venue_sequence": None, "venue_executed_at": None}
    for key in ("sequence", "seq", "sequenceNumber", "execSeq",
                "executionSequence"):
        v = f.get(key)
        if v is None:
            continue
        try:
            out["venue_sequence"] = int(v)
            break
        except (TypeError, ValueError):
            continue
    for key in ("transactTime", "executedAt", "execTime", "executed_at",
                "matchTime"):
        v = f.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            out["venue_executed_at"] = float(v)
            break
        try:
            import datetime as _dt
            txt = str(v).replace("Z", "+00:00")
            if "." in txt:
                head, _, tail = txt.partition(".")
                digits = "".join(c for c in tail if c.isdigit())[:6]
                off = tail[len("".join(c for c in tail if c.isdigit())):]
                txt = "%s.%s%s" % (head, digits.ljust(6, "0"),
                                   off or "+00:00")
            out["venue_executed_at"] = _dt.datetime.fromisoformat(
                txt).timestamp()
            break
        except (TypeError, ValueError):
            continue
    return out


def reconcile_fee(qty: float, price: float, observed, *, at=None,
                  prior_legs=None, following_legs=None) -> dict:
    """EXPECTED AGAINST OBSERVED, through the deployed reconciler.

    THE DEFECT THIS CLOSES. `ingest_fills` called `expected_fee` and stored the
    result in `fee_usd`, and everything downstream -- cash, P&L, the
    reconciliation -- read that as what the venue charged. It is an ESTIMATE.
    When the venue charges something else, the difference has to be visible,
    not absorbed.
    """
    expected, basis = fee_for(qty, price, at=at)
    # THE ORDER'S CUMULATIVE CAP, WHERE THE ORDER'S SEQUENCE IS KNOWN.
    #
    # `fee_for` prices this fill as if it were the only one, which is right for
    # a single-fill order and wrong for every other. When the caller can supply
    # the fills already ingested against this order, the expectation becomes the
    # increment the published algorithm attributes to THIS fill: the whole
    # sequence is priced under the running cap and the last leg's collected
    # amount is taken. That is not the same as capping the total afterwards --
    # the adjustment lands on the fill that would breach the cap, which is what
    # a per-fill reconciliation has to compare against.
    #
    # `prior_legs` omitted keeps the old single-fill behaviour, because a caller
    # that cannot see the order's history must not have a cap invented for it.
    cumulative = None
    if prior_legs is not None:
        before = [(q, p) for q, p, *_ in prior_legs]
        after = [(q, p) for q, p, *_ in (following_legs or [])]
        # THE WHOLE ORDER, IN EXECUTION ORDER, WITH THIS FILL IN ITS PLACE.
        # `after` is not cosmetic: the cap is running, so a fill inserted before
        # existing ones changes THEIR amounts too. Pricing only `before + this`
        # would give this fill the right number and leave its successors holding
        # figures computed as if it had never executed.
        idx = len(before)
        legs = before + [(qty, price)] + after
        got = order_expected_fees(legs)
        if got["BLOCKER"] is None:
            cumulative = {
                "expected_fee_usd_single_fill": expected,
                "order_legs": len(legs),
                "this_leg_index": idx,
                "legs_before": len(before),
                "legs_after": len(after),
                "order_total_expected_usd": got["TOTAL"],
                "cumulative_cap_usd": got["cumulative_cap"],
                "cap_adjusted_this_leg": got["adjusted"][idx],
                "algorithm": got["algorithm"],
            }
            expected = got["per_fill"][idx]
            basis = ("calibration_fees.order_fees(leg %d of %d, %s)"
                     % (idx + 1, len(legs), got["algorithm"]))
            if after:
                # THE SUCCESSORS WHOSE AMOUNTS THIS FILL CHANGED, named with
                # their recomputed figures so the caller can restate them rather
                # than discover the inconsistency later.
                cumulative["restates_following_fills"] = [
                    {"fill_id": fl[2] if len(fl) > 2 else None,
                     "expected_fee_usd": got["per_fill"][idx + 1 + i],
                     "cap_adjusted": got["adjusted"][idx + 1 + i]}
                    for i, fl in enumerate(following_legs or [])]
                cumulative["why_they_need_restating"] = (
                    "the cap is RUNNING, so a fill inserted before existing "
                    "ones changes their amounts. Leaving them as they were "
                    "would make the order's per-fill figures inconsistent with "
                    "its own total")
        else:
            # A SCHEDULE THAT WILL NOT PRICE THE SEQUENCE FALLS BACK TO THE
            # SINGLE-FILL NUMBER AND SAYS SO, rather than silently reporting a
            # capped figure it did not compute.
            cumulative = {"BLOCKER": got["BLOCKER"],
                          "fell_back_to": "single-fill expected_fee"}
    if observed is None:
        return {"expected_fee_usd": expected, "observed_fee_usd": None,
                "booked_fee_usd": expected, "fee_state": FEE_PROVISIONAL,
                "fee_basis": basis, "cumulative": cumulative,
                "reconciliation": {
                    "AGREED": None,
                    "why": ("the venue stated no commission on this "
                            "execution, so the booked fee is the schedule's "
                            "EXPECTATION and the cash is not final")}}
    # the deployed reconciler compares expectation against expectation at the
    # role the venue says it priced; the venue's number is handed to it in the
    # shape it reads.
    when = at
    if when is not None and not isinstance(when, str):
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(
            float(when), _dt.timezone.utc).date().isoformat()
    # THE DEPLOYED RECONCILER WORKS IN `Decimal`, AND SO MUST ITS INPUT.
    # `expected_fee` answers a `Decimal` charge and `reconcile` subtracts the
    # observed one from it directly, so handing it a float raises
    # `unsupported operand type(s) for -: 'Decimal' and 'float'` -- the module
    # is right to keep money out of binary floating point and the caller has to
    # meet it there. `str()` first, never `Decimal(float)`, which would carry
    # the float's representation error into the comparison.
    import decimal

    got = FEES.reconcile(price, qty,
                         {"role": FEES.ROLE_TAKER,
                          "FEE": decimal.Decimal(str(observed))}, at=when)
    agreed = bool(got.get("AGREED"))
    return {"expected_fee_usd": expected,
            "cumulative": cumulative,
            "observed_fee_usd": float(observed),
            # THE OBSERVED CHARGE IS WHAT THE ACCOUNT PAID, so it is what the
            # accounting books once it exists.
            "booked_fee_usd": float(observed),
            "fee_state": FEE_RECONCILED if agreed else FEE_DISAGREES,
            "fee_basis": basis,
            "reconciliation": got}


async def _write_fill_economics(conn, *, fill_id: str, econ_intent: str,
                                direction: str, qty: float, cash: float,
                                fee: dict, buy_intent: str,
                                at: float) -> None:
    """THE TWO EVENTS A FILL MOVES, and the flag that says they are there.

    Called only inside a transaction that also inserts or has already inserted
    the fill. Both events are keyed off the fill's own id, so writing them
    twice writes them once.
    """
    await record_economic_event(
        conn, intent_id=econ_intent,
        kind=("EXIT_PROCEEDS" if direction == "EXIT" else "ENTRY_COST"),
        amount_usd=(cash if direction == "EXIT" else -cash),
        qty=qty, at=at,
        basis=("live_executor.fill_cash(%s) at the venue's own fill price"
               % buy_intent),
        evidence={"fill_id": fill_id, "direction": direction},
        event_id="fev:%s:CASH" % fill_id)
    await record_economic_event(
        conn, intent_id=econ_intent, kind="FEE",
        amount_usd=-float(fee["booked_fee_usd"]), qty=qty, at=at,
        basis=fee["fee_basis"],
        provisional=(fee["fee_state"] == FEE_PROVISIONAL),
        evidence={"fill_id": fill_id, "fee_state": fee["fee_state"],
                  "expected": fee["expected_fee_usd"],
                  "observed": fee["observed_fee_usd"],
                  "reconciliation": fee["reconciliation"]},
        event_id="fev:%s:FEE" % fill_id)
    await conn.execute(
        "UPDATE bettor_funded_fills SET economics_written=TRUE "
        " WHERE fill_id=$1", fill_id)


async def _repair_one_fill(conn, *, fill_id: str, econ_intent: str,
                           direction: str, buy_intent: str, observed,
                           at: float) -> dict:
    """FINISH WHAT AN INTERRUPTION LEFT, AND BOOK A FEE THAT ARRIVED LATE.

    TWO REPAIRS, BOTH IDEMPOTENT.

    THE FIRST: a fill whose economic events were never written. An
    interruption between the fill insert and the events used to be permanent,
    because the redelivery saw the fill already present and stopped. Now the
    redelivery writes the missing events from the STORED fill -- never from the
    incoming payload, which may differ -- so the repair reproduces what the
    first attempt would have written.

    THE SECOND: a PROVISIONAL fee replaced by the venue's actual charge. The
    booked fee was the schedule's estimate because the venue had stated none.
    When a later delivery carries `commission_usd`, the fill's observed fee,
    its state and its booked number are updated, and the difference is written
    as a FEE_ADJUSTMENT event -- so the cash moves by exactly the gap rather
    than the original FEE event being rewritten behind the reader's back. The
    original event's `provisional` flag is cleared in the same transaction,
    because it is no longer an estimate.
    """
    out = {"repaired_economics": False, "fee_reconciled_late": False}
    async with conn.transaction():
        cur = await conn.fetchrow(
            "SELECT fill_id, qty::float8 AS qty, price::float8 AS price, "
            "       cash_usd::float8 AS cash, fee_usd::float8 AS fee, "
            "       expected_fee_usd::float8 AS expected, "
            "       observed_fee_usd::float8 AS observed, fee_state, "
            "       fee_basis, economics_written, direction "
            "  FROM bettor_funded_fills WHERE fill_id=$1 FOR UPDATE", fill_id)
        if cur is None:
            return out
        have = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_economics "
            " WHERE event_id = ANY($1::text[])",
            ["fev:%s:CASH" % fill_id, "fev:%s:FEE" % fill_id])
        # ── BOTH REPAIRS, IN THIS ONE TRANSACTION ───────────────────
        #
        # THE DEFECT THIS CLOSES. The fee update was an `elif` on the missing-
        # events branch. A redelivery that BOTH finished an interrupted write
        # AND carried the venue's actual commission therefore wrote the events
        # and threw the fee away -- and since the venue states a commission
        # once, that was the only copy. The two repairs are independent facts
        # about the same fill and both are done here.
        if int(have or 0) < 2:
            # THE INTERRUPTION REPAIR, from the stored row.
            await _write_fill_economics(
                conn, fill_id=fill_id, econ_intent=econ_intent,
                direction=cur["direction"] or direction,
                qty=float(cur["qty"]), cash=float(cur["cash"]),
                fee={"booked_fee_usd": float(cur["fee"]),
                     "expected_fee_usd": cur["expected"],
                     "observed_fee_usd": cur["observed"],
                     "fee_state": cur["fee_state"],
                     "fee_basis": cur["fee_basis"],
                     "reconciliation": {"written_by": "REPAIR_PASS"}},
                buy_intent=buy_intent, at=at)
            out["repaired_economics"] = True
            out["events_found_before_repair"] = int(have or 0)
            await record_discrepancy(
                conn, kind=D_ECONOMICS_MISSING, intent_id=econ_intent,
                discrepancy_id="fd:%s:ECONOMICS" % fill_id,
                detail={"fill_id": fill_id,
                        "events_found": int(have or 0),
                        "repaired_at": at,
                        "what_it_means": (
                            "this fill existed with fewer than its two "
                            "economic events, which means a previous write "
                            "was interrupted between the fill and the "
                            "ledger. The events have been reconstructed from "
                            "the stored fill and the realised total was "
                            "understated until now")})
        if cur["fee_state"] == FEE_PROVISIONAL and observed is not None:
            # THE LATE FEE -- checked INDEPENDENTLY of the repair above, not as
            # its alternative. Only from PROVISIONAL: a RECONCILED or DISAGREES
            # fill already has the venue's number and a second delivery of it
            # must not move the cash again.
            got = reconcile_fee(float(cur["qty"]), float(cur["price"]),
                                observed, at=at)
            delta = float(got["booked_fee_usd"]) - float(cur["fee"])
            await conn.execute(
                "UPDATE bettor_funded_fills SET observed_fee_usd=$2, "
                "  fee_usd=$3, fee_state=$4, fee_reconciliation=$5::jsonb "
                " WHERE fill_id=$1",
                fill_id, got["observed_fee_usd"], got["booked_fee_usd"],
                got["fee_state"],
                json.dumps(got["reconciliation"], default=str))
            # THE ORIGINAL FEE EVENT IS NO LONGER PROVISIONAL.
            await conn.execute(
                "UPDATE bettor_funded_economics SET provisional=FALSE "
                " WHERE event_id=$1", "fev:%s:FEE" % fill_id)
            if abs(delta) > 1e-9:
                await record_economic_event(
                    conn, intent_id=econ_intent, kind="FEE_ADJUSTMENT",
                    amount_usd=-delta, qty=float(cur["qty"]), at=at,
                    basis=("the venue's stated commission minus the "
                           "schedule's expectation, booked as the difference "
                           "so the original FEE event is not rewritten"),
                    evidence={"fill_id": fill_id,
                              "expected": cur["expected"],
                              "provisionally_booked": float(cur["fee"]),
                              "observed": got["observed_fee_usd"],
                              "fee_state": got["fee_state"]},
                    event_id="fev:%s:FEE_ADJ" % fill_id)
            out.update(fee_reconciled_late=True,
                       fee_state=got["fee_state"],
                       adjustment_usd=round(-delta, 6),
                       was_provisional=True)
            if got["fee_state"] == FEE_DISAGREES:
                await record_discrepancy(
                    conn, kind=D_FEE_DISAGREES, intent_id=econ_intent,
                    discrepancy_id="fd:%s:FEE" % fill_id,
                    detail={"fill_id": fill_id, "expected": cur["expected"],
                            "observed": got["observed_fee_usd"],
                            "arrived": "on a later delivery"})
    return out


async def repair_missing_economics(conn, *, account_id=None,
                                   venue=None, at: float | None = None
                                   ) -> dict:
    """SWEEP FOR FILLS WHOSE ECONOMIC EVENTS ARE MISSING, and write them.

    THE REPLAY-REPAIR PROOF. The transaction in `ingest_fills` makes an
    interruption between the fill and its events impossible going forward; this
    makes one RECOVERABLE if it ever happened -- including on rows written by
    the earlier non-atomic path, which is every fill that predates migration
    128. It is a read for any book that is already consistent.

    Safe to run on a schedule. It writes only what is absent.
    """
    now = float(at if at is not None else time.time())
    sql = ("SELECT f.fill_id, f.intent_id, i.parent_intent_id, "
           "       i.order_intent, p.order_intent AS parent_intent, "
           "       f.direction "
           "  FROM bettor_funded_fills f "
           "  JOIN bettor_funded_intents i ON i.intent_id=f.intent_id "
           "  LEFT JOIN bettor_funded_intents p "
           "    ON p.intent_id=i.parent_intent_id "
           " WHERE NOT f.economics_written")
    args: list = []
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND i.account_id=$%d" % len(args)
    if venue is not None:
        args.append(str(venue))
        sql += " AND upper(i.venue)=upper($%d)" % len(args)
    rows = await conn.fetch(sql, *args)
    repaired, already = [], []
    for r in rows:
        econ = r["parent_intent_id"] or r["intent_id"]
        got = await _repair_one_fill(
            conn, fill_id=r["fill_id"], econ_intent=econ,
            direction=r["direction"],
            buy_intent=(r["parent_intent"] or r["order_intent"]),
            observed=None, at=now)
        if got.get("repaired_economics"):
            repaired.append(r["fill_id"])
        else:
            # THE EVENTS WERE THERE ALL ALONG -- an older row whose flag was
            # never set. Setting it is what stops this sweep re-examining it.
            await conn.execute(
                "UPDATE bettor_funded_fills SET economics_written=TRUE "
                " WHERE fill_id=$1", r["fill_id"])
            already.append(r["fill_id"])
    for iid in {r["parent_intent_id"] or r["intent_id"] for r in rows}:
        await _recompute_residual(conn, iid)
    return {"examined": len(rows), "repaired": repaired,
            "already_written_flag_set": already,
            "is_a_read_when_the_book_is_consistent": not rows,
            "why": ("a fill without its economic events is inventory whose "
                    "cost is in no ledger. This finds any and writes them "
                    "from the stored fill")}


async def ingest_fills(conn, intent_id: str, fills, *,
                       direction: str = "ENTRY",
                       at: float | None = None) -> dict:
    """RECORD EXECUTIONS IDEMPOTENTLY, and refuse to invent an identity.

    An unidentified execution is NOT written and NOT called already-held: the
    intent goes UNRESOLVED, because quantity the venue reported and we cannot
    place is a discrepancy, not a rounding error.

    Every ingested fill also writes its ECONOMIC EVENTS -- the cash and the fee
    -- so realised P&L and drawdown are sums over rows rather than constants.
    """
    now = float(at if at is not None else time.time())
    row = await conn.fetchrow(
        "SELECT intent_id, venue_order_id, order_intent, quantity, kind, "
        "       parent_intent_id "
        "  FROM bettor_funded_intents WHERE intent_id=$1", intent_id)
    if row is None:
        return {"ok": False, "refusal": R_NO_SUCH_INTENT}
    # AN EXIT'S ECONOMICS BELONG TO THE POSITION IT CLOSES.
    econ_intent = row["parent_intent_id"] or intent_id
    # and the side is the ENTRY's side: buying back a short releases
    # collateral in the same space the short committed it.
    buy_intent = row["order_intent"]
    if row["parent_intent_id"]:
        parent = await conn.fetchrow(
            "SELECT order_intent FROM bettor_funded_intents WHERE intent_id=$1",
            row["parent_intent_id"])
        if parent:
            buy_intent = parent["order_intent"]
    written, already, unresolved = [], [], []
    # ── ONE TRANSACTION, UNDER THE POSITION'S OWN LOCK ──────────────
    #
    # THE DEFECT THIS CLOSES. The previous version made the FILL and its
    # ECONOMIC EVENTS atomic and then updated the ORDER STATE and the RESIDUAL
    # afterwards, outside. That leaves a window with a specific and expensive
    # shape: an entry set to FILLED, whose `residual_qty` is still the 0 it was
    # inserted with. `bettor_funded_position_is_open` reads FILLED as not
    # outstanding and residual 0 as nothing held -- so the one-open-position
    # slot is RELEASED while the account actually holds the contracts, and the
    # next entry is admitted against inventory no rail can see.
    #
    # The ledger, the order state and the residual/closure transition are
    # therefore one commit, taken under `FOR UPDATE` on the POSITION row -- the
    # same lock `_reserve_exit` takes, in the same order, so an exit
    # reservation and a fill ingest serialise against each other instead of
    # interleaving.
    async with conn.transaction():
        await conn.execute(
            "SELECT 1 FROM bettor_funded_intents WHERE intent_id=$1 "
            " FOR UPDATE", econ_intent)
        return await _ingest_locked(
            conn, intent_id=intent_id, fills=fills, direction=direction,
            now=now, row=row, econ_intent=econ_intent,
            buy_intent=buy_intent, written=written, already=already,
            unresolved=unresolved)


async def _ingest_locked(conn, *, intent_id, fills, direction, now, row,
                         econ_intent, buy_intent, written, already,
                         unresolved):
    """THE BODY OF `ingest_fills`, running inside its transaction and lock.

    Split out only so the lock and the commit boundary are visible in one
    place at the call site rather than as an indentation level here.
    """
    for f in list(fills or ()):
        qty = float(f.get("qty") or 0)
        px = float(f.get("price") or 0)
        vfid = f.get("venue_fill_id")
        fid = fill_id_for(row["venue_order_id"], vfid)
        if fid is None or qty <= 0 or px <= 0:
            unresolved.append({"qty": qty, "price": px,
                               "venue_fill_id": vfid,
                               "refusal": R_NO_EXECUTION_IDENTITY})
            continue
        cash = cash_for(qty, px, buy_intent)
        # THIS ORDER'S EARLIER FILLS, READ BACK INSIDE THE LOCK.
        #
        # Read per fill rather than once per batch, because the batch itself
        # writes rows: fill 2 of a three-fill delivery must see fill 1, which
        # was inserted a few lines below on the previous iteration. Reading
        # once up front would price all three as firsts.
        #
        # A REDELIVERY IS EXCLUDED BY ITS OWN ID. Without that, re-ingesting
        # fill 2 would see fill 2 among the priors and price it as a third
        # leg -- a duplicate delivery would change the expectation, which is
        # the opposite of idempotent.
        keyed = [t for t in await prior_taker_legs_keyed(
            conn, intent_id, direction) if t[3] != fid]
        # THE VENUE'S OWN ORDERING, CAPTURED BEFORE THE FEE IS COMPUTED, so this
        # fill takes its VENUE position in the sequence rather than its arrival
        # position. Both absent is a real answer and produces ARRIVAL_ORDER.
        vo = venue_order_fields(f)
        _b, _k = venue_execution_order(
            {"fill_id": fid, "at": now,
             "venue_sequence": vo["venue_sequence"],
             "venue_executed_at": vo["venue_executed_at"]})
        # THIS FILL IS PLACED BY ITS OWN KEY, NOT APPENDED. A late-arriving fill
        # whose venue sequence puts it SECOND of four must be priced as the
        # second -- appending it would price it as the fourth and attribute the
        # cap's adjustment to the wrong fill.
        placed = _insert_by_venue_order(keyed, _k)
        fee = reconcile_fee(qty, px, _observed_fee_of(f), at=now,
                            prior_legs=placed["before"],
                            following_legs=placed["after"])
        fee["fee_order_basis"] = _b
        fee["venue_sequence"] = vo["venue_sequence"]
        fee["venue_executed_at"] = vo["venue_executed_at"]
        # ── ONE TRANSACTION: THE FILL, ITS EVENTS, AND THE FLAG ─────
        #
        # THE DEFECT THIS CLOSES. The fill went in, then the cash event, then
        # the fee event, as three statements. Stop between the first and the
        # second -- a restart, a killed worker, a dropped connection -- and the
        # redelivery hits `ON CONFLICT DO NOTHING` on the fill, concludes
        # "already held", and NEVER writes the events. The contracts sit in
        # inventory with their cost in no ledger, so realised P&L is wrong by
        # exactly that fill and nothing reports it.
        #
        # `economics_written` is set in the SAME transaction as the events, so
        # it can only be true if they exist -- and `repair_missing_economics`
        # below finds any fill where it is not.
        #
        # THIS IS NOW A SAVEPOINT inside the caller's transaction, which holds
        # the position lock and also covers the state and residual transition.
        # It is kept because it scopes ONE fill: a failure here rolls that fill
        # back without discarding fills already ingested in this batch.
        async with conn.transaction():
            res = await conn.execute(
                "INSERT INTO bettor_funded_fills (fill_id, intent_id,"
                " venue_order_id, venue_fill_id, at, qty, price, cash_usd,"
                " fee_usd, fee_basis, raw, direction, expected_fee_usd,"
                " observed_fee_usd, fee_state, fee_reconciliation,"
                " venue_sequence, venue_executed_at, fee_order_basis) "
                "VALUES ($1,$2,$3,$4,to_timestamp($5),$6,$7,$8,$9,$10,"
                "        $11::jsonb,$12,$13,$14,$15,$16::jsonb,$17,"
                "        CASE WHEN $18::float8 IS NULL THEN NULL"
                "             ELSE to_timestamp($18::float8) END,$19)"
                " ON CONFLICT (fill_id) DO NOTHING",
                fid, intent_id, str(row["venue_order_id"] or ""), str(vfid),
                now, qty, px, cash, fee["booked_fee_usd"], fee["fee_basis"],
                json.dumps(f, default=str), direction,
                fee["expected_fee_usd"], fee["observed_fee_usd"],
                fee["fee_state"],
                json.dumps(fee["reconciliation"], default=str),
                # THE VENUE'S OWN ORDERING, PERSISTED. Without these columns the
                # next fill of this order would read the sequence back and find
                # only our arrival clock, so the ordering would be correct for
                # one fill and lost for every fill after it.
                fee.get("venue_sequence"),
                fee.get("venue_executed_at"),
                fee.get("fee_order_basis"))
            fresh = res.endswith("1")
            if fresh:
                await _write_fill_economics(
                    conn, fill_id=fid, econ_intent=econ_intent,
                    direction=direction, qty=qty, cash=cash, fee=fee,
                    buy_intent=buy_intent, at=now)
        if fresh:
            written.append({"fill_id": fid, "qty": qty, "price": px,
                            "cash_usd": cash, **fee})
        else:
            # ── A REDELIVERY IS NOT NOTHING ─────────────────────────
            #
            # It is the second chance to finish an interrupted write, and the
            # occasion on which the venue's ACTUAL commission often arrives
            # for the first time. Both are handled here, idempotently, so a
            # replay repairs rather than merely declining to duplicate.
            rep = await _repair_one_fill(
                conn, fill_id=fid, econ_intent=econ_intent,
                direction=direction, buy_intent=buy_intent,
                observed=_observed_fee_of(f), at=now)
            already.append({"fill_id": fid, "qty": qty, "price": px, **rep})
    # THIS ORDER'S OWN FILLED QUANTITY, in its own direction. An exit's fills
    # carry direction='EXIT', so summing only the ENTRY column would read every
    # exit as unfilled and leave its state wrong.
    tot = await conn.fetchrow(
        "SELECT coalesce(sum(CASE WHEN direction=$2 THEN qty ELSE 0 END)"
        "       ,0)::float8 AS own_qty, "
        "       coalesce(sum(CASE WHEN direction='ENTRY' THEN qty ELSE 0 END)"
        "       ,0)::float8 AS in_qty, "
        "       coalesce(sum(cash_usd),0)::float8 AS cash, "
        "       coalesce(sum(fee_usd),0)::float8 AS fee "
        "  FROM bettor_funded_fills WHERE intent_id=$1", intent_id, direction)
    filled = float(tot["own_qty"])
    if unresolved:
        await mark_unresolved(
            conn, intent_id,
            "%d execution(s) the venue did not name; quantity unreconciled"
            % len(unresolved))
    elif filled >= float(row["quantity"]) - 1e-9:
        await conn.execute(
            "UPDATE bettor_funded_intents SET state='FILLED', "
            "  resolved_at=now(), updated_at=now() "
            " WHERE intent_id=$1 AND state NOT IN ('UNRESOLVED','CANCELLED')",
            intent_id)
    elif filled > 0:
        await conn.execute(
            "UPDATE bettor_funded_intents SET state='PARTIALLY_FILLED', "
            "  updated_at=now() "
            " WHERE intent_id=$1 AND state NOT IN ('UNRESOLVED','CANCELLED')",
            intent_id)
    # ... AND AN ORDER THAT FILLED NOTHING IS NOT FINISHED. This used to mark
    # every exit FILLED the moment it was ingested, whatever the venue had
    # actually done -- so an exit that filled nothing read as complete and the
    # inventory it was supposed to reduce looked serviced.
    # THE RESIDUAL IS RECOMPUTED ON THE POSITION, which is what exposure reads.
    residual = await _recompute_residual(conn, econ_intent)
    if direction == "EXIT" and residual <= 0:
        await mark_position_closed(conn, econ_intent,
                                   "EXITED_IN_THE_MARKET")
    return {"ok": True, "written": written, "already_held": already,
            "unresolved": unresolved, "unresolved_count": len(unresolved),
            "direction": direction,
            "filled_qty_from_the_ledger": round(filled, 6),
            "residual_qty": residual,
            "cash_usd_from_the_ledger": round(float(tot["cash"]), 6),
            "fees_usd_from_the_ledger": round(float(tot["fee"]), 6),
            "idempotent_on": "the venue's own fill id"}


async def remaining_basis(conn, intent_id: str) -> dict:
    """WHAT THE CONTRACTS WE STILL HOLD COST US, pro rata.

    THE DEFECT THIS CLOSES. The void refund summed the PARENT's entry fills and
    handed that back -- `SELECT sum(cash) ... WHERE intent_id = parent`, which
    reads the entry's own rows and none of the exit children's. After a partial
    exit that is the ORIGINAL acquisition cost of the whole clip, not the cost
    of what is left, so a void on a position already half sold refunded roughly
    twice what the venue was still holding. The exit's proceeds were booked
    too, so the position came out ahead on a void -- which is a fabricated
    profit in the one case where the correct answer is close to zero.

    The basis is per-contract from the ENTRY fills, times the residual.
    """
    row = await conn.fetchrow(
        "SELECT coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.cash_usd "
        "                       ELSE 0 END),0)::float8 AS entry_cash, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.qty "
        "                       ELSE 0 END),0)::float8 AS entry_qty, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.cash_usd "
        "                       ELSE 0 END),0)::float8 AS exit_cash, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.qty "
        "                       ELSE 0 END),0)::float8 AS exit_qty "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.intent_id=$1 OR i.parent_intent_id=$1", intent_id)
    eq = float(row["entry_qty"])
    ec = float(row["entry_cash"])
    per = (ec / eq) if eq > 0 else None
    residual = await conn.fetchval(
        "SELECT residual_qty::float8 FROM bettor_funded_intents "
        " WHERE intent_id=$1", intent_id)
    res = float(residual or 0.0)
    return {"entry_cash_usd": round(ec, 6),
            "entry_qty": round(eq, 6),
            "exit_cash_usd": round(float(row["exit_cash"]), 6),
            "exit_qty": round(float(row["exit_qty"]), 6),
            "basis_per_contract": (None if per is None else round(per, 8)),
            "residual_qty": round(res, 6),
            "remaining_basis_usd": (0.0 if per is None
                                    else round(per * res, 6)),
            "why": ("the cost of the contracts STILL HELD -- per-contract "
                    "entry cost times the residual -- not the original "
                    "acquisition cost of a clip that has been partly sold")}


R_NOT_OUR_ROW = "THIS_POSITION_BELONGS_TO_ANOTHER_ACCOUNT_OR_VENUE"
R_NO_ROW = "NO_SUCH_FUNDED_POSITION"


def partial_realisation(*, entry_qty, entry_cash, entry_fees,
                        exit_qty, exit_cash, exit_fees, residual) -> dict:
    """THE ARITHMETIC OF A PARTIAL EXIT, PURE AND IN ONE PLACE.

    ── WHY THIS IS FACTORED OUT ──────────────────────────────────────
    Two callers need it now: `realised_on_sold`, which answers it for one
    position on the operator surface, and `realised`, which must fold the
    SAME result into the realised equity curve the loss stop reads. Two
    copies of this would be two conventions on one position, and the
    docstring below already warns that a second convention makes the two
    disagree. So there is exactly one, and it is pure -- testable without
    a database, and identical wherever it is called.

        allocated_basis   = (entry_cash / entry_qty) * sold_qty
        realised_on_sold  = exit_proceeds - allocated_basis - fees_on_sold

    Returns None for every derived figure when `entry_qty` is 0 -- there
    is then no per-contract basis, and inventing one would be the
    favourable assumption this system refuses.
    """
    eq, ec = float(entry_qty or 0.0), float(entry_cash or 0.0)
    sold, proceeds = float(exit_qty or 0.0), float(exit_cash or 0.0)
    e_fee, x_fee = float(entry_fees or 0.0), float(exit_fees or 0.0)
    resid = float(residual or 0.0)
    per = (ec / eq) if eq > 0 else None
    # FEES ARE ALLOCATED THE SAME WAY THE BASIS IS. An entry fee was paid
    # on the whole clip, so only the sold fraction of it belongs to the
    # sold result; every exit fee was paid to sell, so all of it does.
    entry_fee_on_sold = (0.0 if eq <= 0 else e_fee * (sold / eq))
    entry_fee_on_residual = (0.0 if eq <= 0 else e_fee * (resid / eq))
    fees_on_sold = entry_fee_on_sold + x_fee
    allocated = (None if per is None else per * sold)
    realised = (None if allocated is None
                else proceeds - allocated - fees_on_sold)
    remaining_basis_usd = (None if per is None else per * resid)
    # WHAT OPEN-POSITION NET CASH CARRIES THAT THIS DOES NOT, to the cent.
    # Net cash is short of the sold result by the residual's basis AND by
    # the residual's share of the entry fee -- two terms, not one.
    excess = (None if remaining_basis_usd is None
              else remaining_basis_usd + entry_fee_on_residual)
    return {
        "sold_qty": round(sold, 6),
        "exit_proceeds_usd": round(proceeds, 6),
        "basis_per_contract": (None if per is None else round(per, 8)),
        "allocated_basis_usd": (None if allocated is None
                                else round(allocated, 6)),
        "entry_fees_total_usd": round(e_fee, 6),
        "entry_fees_allocated_to_sold_usd": round(entry_fee_on_sold, 6),
        "entry_fees_allocated_to_residual_usd": round(entry_fee_on_residual,
                                                      6),
        "exit_fees_usd": round(x_fee, 6),
        "fees_on_sold_usd": round(fees_on_sold, 6),
        "realised_on_sold_usd": (None if realised is None
                                 else round(realised, 6)),
        "residual_qty": round(resid, 6),
        "remaining_basis_usd": (None if remaining_basis_usd is None
                                else round(remaining_basis_usd, 6)),
        "net_cash_exceeds_this_by_usd": (None if excess is None
                                         else round(excess, 6)),
    }


async def realised_on_sold(conn, intent_id: str) -> dict:
    """WHAT THE QUANTITY ALREADY SOLD ACTUALLY MADE OR LOST.

    THE GAP THIS CLOSES. `realised()` books a result when the POSITION
    closes -- it filters `closed_at IS NOT NULL` -- which is the right
    basis for drawdown and the loss stop, because an open position's
    outcome is not yet determined. But it means an open position that has
    already sold part of its inventory reports `realised_pnl_usd` 0.00
    while a real gain or loss sits inside it. The controlled demonstration
    made that visible: 9 of 15 contracts sold at 0.41 on a 0.60 basis, a
    -$1.71 result on the sold portion, and the lane reported 0.00 realised
    with -$5.71 of open-position net cash.

    Open-position net cash is NOT that number. It is
    (basis out - proceeds back + fees) over the WHOLE clip, so it mixes
    the realised result on what was sold with the cost still tied up in
    what is held. Reporting it as the partial result overstates the loss
    by the remaining basis.

    THE ATTRIBUTION CONVENTION IS THE ONE THE LANE ALREADY DECLARES:
    average entry cost per contract, exactly as `remaining_basis` uses it
    for a void refund. It is not invented here, and using a second
    convention for the same position would make the two disagree.

        allocated_basis = (entry_cash / entry_qty) * sold_qty
        realised_on_sold = exit_proceeds - allocated_basis - fees_on_both

    EVERY COMPONENT IS RETURNED SEPARATELY, because a single net figure
    cannot be checked. Proceeds, allocated basis, fees and the remaining
    inventory each stand on their own line.
    """
    f = await conn.fetchrow(
        "SELECT coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.cash_usd "
        "                       ELSE 0 END),0)::float8 AS entry_cash, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.qty "
        "                       ELSE 0 END),0)::float8 AS entry_qty, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.cash_usd "
        "                       ELSE 0 END),0)::float8 AS exit_cash, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.qty "
        "                       ELSE 0 END),0)::float8 AS exit_qty, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.fee_usd "
        "                       ELSE 0 END),0)::float8 AS entry_fees, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.fee_usd "
        "                       ELSE 0 END),0)::float8 AS exit_fees, "
        "       count(*) FILTER (WHERE f.fee_state=$2) AS provisional_fills "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.intent_id=$1 OR i.parent_intent_id=$1",
        intent_id, FEE_PROVISIONAL)
    pos = await conn.fetchrow(
        "SELECT residual_qty::float8 AS residual, "
        "       closed_at IS NOT NULL AS closed, closed_reason "
        "  FROM bettor_funded_intents WHERE intent_id=$1 AND kind='ENTRY'",
        intent_id)
    if pos is None:
        return {"ok": False, "refusal": R_NO_SUCH_INTENT,
                "intent_id": intent_id}

    # THE ARITHMETIC IS `partial_realisation`'s, not repeated here. See its
    # docstring: `realised` folds the same result into the equity curve the
    # loss stop reads, and two copies would be two conventions.
    m = partial_realisation(
        entry_qty=f["entry_qty"], entry_cash=f["entry_cash"],
        entry_fees=f["entry_fees"], exit_qty=f["exit_qty"],
        exit_cash=f["exit_cash"], exit_fees=f["exit_fees"],
        residual=(pos["residual"] or 0.0))

    return {
        "ok": True, "intent_id": intent_id,
        # ── THE SOLD SIDE, COMPONENT BY COMPONENT ──────────────────
        **m,
        "position_closed": bool(pos["closed"]),
        "closed_reason": pos["closed_reason"],
        # ── WHAT THIS IS AND IS NOT ────────────────────────────────
        "attribution": "AVERAGE_ENTRY_COST_PER_CONTRACT",
        "attribution_is_the_lanes_own": (
            "the same per-contract basis `remaining_basis` uses for a void "
            "refund. Not invented here; a second convention on one position "
            "would make the two disagree"),
        "identity": ("realised_on_sold = exit_proceeds - allocated_basis - "
                     "fees_on_sold"),
        "this_is_now_inside_realised_pnl": (
            "CORRECTED 2026-09-28. This field previously read '`realised()` "
            "books on POSITION CLOSURE and is what the drawdown and the "
            "loss stop read ... both are correct for their own question'. "
            "The first half was true and the second was wrong. A loss stop "
            "whose measurement omits a loss that has ALREADY BEEN TAKEN is "
            "not correct for its own question -- it is a stop that does not "
            "stop. `realised()` now folds this result into the realised "
            "equity curve at the instant the last exit filled, so the same "
            "number the operator reads here is the number MAX_DRAWDOWN is "
            "measured against"),
        "this_is_not_open_position_net_cash": (
            "net cash is (basis out - proceeds back + fees) over the WHOLE "
            "clip, so it mixes the realised result on what was sold with "
            "the cost still tied up in what is held. It overstates a "
            "partial loss by the remaining basis AND by the residual's "
            "share of the entry fee -- TWO terms. A correction: I first "
            "wrote that it differed by the remaining basis alone, and on "
            "the demonstration's own figures (fees 0.25 entry, 0.15 exit) "
            "that identity misses by exactly the 0.10 of entry fee sitting "
            "on the 6 contracts still held"),
        "net_cash_exceeds_this_by_usd": (None if excess is None
                                         else round(excess, 6)),
        "net_cash_identity": (
            "open_position_net_cash = realised_on_sold - remaining_basis - "
            "entry_fees_allocated_to_residual"),
        "unrealised_on_the_residual": NOT_IDENTIFIED,
        "why_unrealised_is_not_identified": (
            "marking the residual needs a funded mark, and this lane has no "
            "funded mark source. Reported NOT_IDENTIFIED rather than 0"),
        "fees_are_provisional_on": int(f["provisional_fills"] or 0),
        "realised_on_sold_is_provisional": bool(f["provisional_fills"]),
    }


async def check_servicing(conn, *, intent_id: str, account_id: str,
                          venue: str) -> dict:
    """MAY WE SERVICE A POSITION WE ALREADY HOLD? OWNERSHIP, NOT PERMISSION.

    THE DEFECT THIS CLOSES. `submit_exit` required an AFFIRMATIVE
    `authorize_submission` result -- the same gate an entry passes. So an
    expired grant, a revoked one, a replaced approved-limit set, or
    `REAL_ORDER_SUBMISSION_ENABLED` being off blocked the EXIT as well as the
    entry. That is the wrong failure in the most expensive direction: the thing
    that lapsed was permission to take NEW exposure, and the consequence was
    that existing exposure could no longer be reduced. A lapse must shrink what
    we may do; it must never strand what we already did.

    This is the same boundary `bettor_test_venue_executor.check_servicing`
    established for the shadow lane, applied to the funded book: ownership is
    read from THE POSITION'S OWN ROW (`account_id`, `venue`), never from the
    authorization record -- which is a single replaceable row, so reading
    ownership from it would let a new account's grant service the previous
    account's positions. Submission authority is reported for the record and
    gates nothing here.

    WHAT STILL GATES AN EXIT, and it is not nothing: the venue class, this
    row's ownership, `FUNDED_EXIT_SUBMISSION_ENABLED`, the adapter's own
    `execution_gate.authorize('submit')`, and the absence of credentials.
    """
    from . import bettor_funded_activation as FA

    klass = FA.venue_class(venue)
    out = {"gate": "FUNDED_SERVICING", "intent_id": intent_id,
           "account_id": account_id, "venue": venue, "venue_class": klass,
           "this_gate_authorises_no_new_exposure": True,
           "ownership_is_read_from": ("the position's own row (account_id, "
                                     "venue), never the authorization record"),
           "needs_a_live_submission_grant": False,
           "why_not": ("an exit REDUCES exposure. Refusing it because the "
                       "grant to ADD exposure lapsed is how a lapse becomes "
                       "an unmanaged position")}
    if klass != FA.VENUE_FUNDED:
        return dict(out, ok=False, refusal="NOT_A_FUNDED_CLASS_VENUE")
    row = await conn.fetchrow(
        "SELECT intent_id, account_id, venue, kind FROM bettor_funded_intents "
        " WHERE intent_id=$1", intent_id)
    if row is None:
        return dict(out, ok=False, refusal=R_NO_ROW)
    if (str(row["account_id"]) != str(account_id)
            or str(row["venue"]).upper() != str(venue).upper()):
        return dict(out, ok=False, refusal=R_NOT_OUR_ROW,
                    row_belongs_to={"account_id": row["account_id"],
                                    "venue": row["venue"]},
                    why=("a lapsed or replaced grant is not a licence to "
                         "service somebody else's positions"))
    return dict(out, ok=True, refusal=None, owns_the_row=True)


async def mark_position_closed(conn, intent_id: str,
                               reason: str) -> dict:
    """REMOVE A HOLDING FROM EXPOSURE, and only for an evidenced reason.

    NOT NAMED `close_position`, DELIBERATELY. `pmus.close_position` PLACES
    AN ORDER -- it is one of the two submitting entry points the order-route
    census watches for by name. This function submits nothing; it writes
    `closed_at` and a reason. Sharing the name made the census flag this
    module as order-capable, and a census that cannot tell a venue
    submission from a bookkeeping UPDATE is a census that will one day miss
    a real one.

    The database's CHECK constraint enumerates the reasons, so a closure with
    no reason -- or an invented one -- cannot be written at all.
    """
    if reason not in CLOSURE_REASONS:
        raise ValueError("%r is not an evidenced closure reason" % reason)
    await conn.execute(
        "UPDATE bettor_funded_intents SET closed_at=now(), closed_reason=$2, "
        "  updated_at=now() WHERE intent_id=$1 AND closed_at IS NULL",
        intent_id, reason)
    return {"intent_id": intent_id, "closed_reason": reason,
            "exposure": "REMOVED"}


# ── 3 · RECOVERY: ASK THE VENUE, NEVER RESEND ───────────────────────

async def open_entry_positions(conn, *, account_id=None,
                               venue=None) -> list[dict]:
    """EVERY ENTRY WHOSE POSITION IS OPEN -- outstanding order or held stock.

    This is what the one-position index is over, so a refusal that names the
    occupant names the same rows the database refused on.
    """
    sql = ("SELECT * FROM bettor_funded_intents "
           " WHERE kind='ENTRY' AND bettor_funded_position_is_open("
           "         state, residual_qty, closed_at)")
    args: list = []
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND account_id=$%d" % len(args)
    if venue is not None:
        args.append(str(venue))
        sql += " AND upper(venue)=upper($%d)" % len(args)
    return [dict(r) for r in await conn.fetch(sql + " ORDER BY created_at",
                                             *args)]


async def live_intents(conn, *, account_id=None, venue=None) -> list[dict]:
    """EVERY INTENT WHOSE ORDER IS STILL OUTSTANDING AT THE VENUE.

    Deliberately NOT "everything with exposure" -- that is
    `open_entry_positions`. This is what recovery iterates, because an order
    that is finished has nothing left to ask the venue about.
    """
    sql = ("SELECT * FROM bettor_funded_intents "
           " WHERE bettor_funded_order_is_outstanding(state)")
    args: list = []
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND account_id=$%d" % len(args)
    if venue is not None:
        args.append(str(venue))
        sql += " AND upper(venue)=upper($%d)" % len(args)
    return [dict(r) for r in await conn.fetch(sql + " ORDER BY created_at",
                                             *args)]


async def _executions_we_cannot_claim(adapter, intent: dict, *,
                                     at: float) -> dict:
    """WHAT THE VENUE TRADED ON THIS MARKET while we were not looking.

    Read and REPORTED, never adopted. A trade on our market during our
    downtime is exactly as much evidence of ownership as a resting order on
    our market is -- none -- so this cannot resolve an intent. It exists
    because the alternative is a silent UNRESOLVED row with no indication of
    whether anything actually happened, and an operator reconciling by hand
    needs to know which it is.
    """
    fn = getattr(adapter, "recent_trades", None)
    if fn is None:
        return {"venue_activity_on_this_market": None,
                "why": "this adapter states no trade history"}
    since = intent.get("sent_at")
    since_ts = _epoch_of(since)
    if since_ts is None:
        since_ts = at - 86400.0
    try:
        rows = fn(str(intent.get("us_market_slug")), since_ts) or []
    except Exception as exc:                                # noqa: BLE001
        return {"venue_activity_on_this_market": "UNREADABLE",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return {"venue_activity_on_this_market": len(rows),
            "requires_manual_reconciliation": bool(rows),
            "why": ("the venue traded on this market since our request left. "
                    "That is NOT evidence the trade was ours and nothing is "
                    "adopted from it; it is reported so the discrepancy is "
                    "visible rather than silent")}


async def recover(conn, adapter, *, account_id: str, venue: str,
                  now: float | None = None) -> dict:
    """AFTER A RESTART OR A LOST ANSWER: reconcile every live intent.

    THE RULE. A row in SEND_ATTEMPTED with no venue order id is the dangerous
    one -- we may or may not have an order. It is resolved by ASKING THE VENUE
    (its open orders, then its record of the order) and NEVER by sending
    another. If the venue cannot establish what happened, the intent is left
    UNRESOLVED with its exposure preserved.
    """
    at = float(now if now is not None else time.time())
    out = {"at": at, "account_id": account_id, "venue": venue,
           "resubmitted_anything": False,   # and there is no code path that could
           "reconciled": [], "unresolved": [], "orphans": []}
    rows = await live_intents(conn, account_id=account_id, venue=venue)
    out["live_intents"] = len(rows)
    if not rows:
        return dict(out, ok=True)

    # WHAT THE VENUE HOLDS OPEN, read once.
    try:
        theirs = {}
        for o in (adapter.open_orders() or []):
            oid = _venue_order_id_of(o)
            if oid:
                theirs[oid] = o
        readable = True
    except Exception as exc:                                # noqa: BLE001
        theirs, readable = {}, False
        out["open_orders_error"] = "%s: %s" % (type(exc).__name__,
                                               str(exc)[:200])

    # EVERY VENUE ORDER SOME FUNDED INTENT ALREADY OWNS. An order in here is
    # that intent's and can never be adopted by another one, which is the
    # first thing that stopped two intents from claiming one order.
    claimed = {str(rr["venue_order_id"]) for rr in await conn.fetch(
        "SELECT venue_order_id FROM bettor_funded_intents "
        " WHERE venue_order_id IS NOT NULL")}
    out["venue_orders_already_claimed"] = sorted(claimed)

    for r in rows:
        iid, vid = r["intent_id"], r["venue_order_id"]
        rec = {"intent_id": iid, "state_was": r["state"],
               "venue_order_id": vid}
        if not readable:
            # WE COULD NOT LOOK. That is not "no order exists".
            got = await mark_unresolved(
                conn, iid, "the venue's open-order list could not be read, "
                           "so whether this order exists is unknown")
            out["unresolved"].append(dict(rec, **got))
            continue
        if vid is None:
            # THE LOST-ACKNOWLEDGEMENT CASE. We have no venue id, so we
            # cannot ask about a specific order, and the ONLY way an order the
            # venue holds becomes ours is an ESTABLISHED CORRELATION.
            corr = correlate_venue_order(dict(r), list(theirs.values()),
                                         claimed=claimed,
                                         sent_at=r.get("sent_at"))
            rec["correlation"] = corr
            if corr["adopt"] is None:
                extra = await _executions_we_cannot_claim(adapter, r, at=at)
                got = await mark_unresolved(
                    conn, iid,
                    "sent with no acknowledgement and %s; the exposure stands "
                    "until the venue establishes it" % corr["refusal"])
                if corr.get("term_match_only"):
                    # A TERM MATCH IS A LEAD, NOT A CLAIM. It is recorded so an
                    # operator knows which order to look at -- and recorded as
                    # a DISCREPANCY rather than a resolution, because the one
                    # thing that must not happen is this row quietly becoming
                    # ours on the strength of a price and a size.
                    await record_discrepancy(
                        conn, kind=D_TERM_MATCH_NOT_OWNERSHIP, intent_id=iid,
                        discrepancy_id="fd:%s:TERM_MATCH" % iid,
                        detail={"venue_order_id": corr["term_match_only"],
                                "terms": list(CORRELATION_TERMS),
                                "client_identity_supported":
                                    CLIENT_ORDER_IDENTITY_SUPPORTED,
                                "what_it_means": (
                                    "one order the venue holds agrees with "
                                    "every term of a request we sent and "
                                    "never got an answer to. It may be ours. "
                                    "It may be a manual order at the same "
                                    "price and size. This venue accepts no "
                                    "client order identity, so the difference "
                                    "cannot be established from here and a "
                                    "human must look")})
                    rec["term_match_recorded_as_a_discrepancy"] = True
                out["unresolved"].append(dict(rec, **got, **extra))
                continue
            aid = corr["venue_order_id"]
            await record_acknowledgement(
                conn, iid, venue_order_id=aid, status="open",
                raw={"adopted_by_recovery": True,
                     "correlation": {k: v for k, v in corr.items()
                                     if k != "adopt"},
                     "venue_order": corr["adopt"]})
            claimed.add(str(aid))
            vid = aid
            rec["venue_order_id"] = aid
            rec["case_pre"] = "ADOPTED_ON_AN_ESTABLISHED_CORRELATION"
            # AND THEN FALL THROUGH AND ASK ABOUT IT. Adopting the id without
            # reading the order back was the other half of the downtime bug:
            # the order may have filled since, and the book would carry an
            # ACKNOWLEDGED row with no fills against a position that exists.
        # WE HAVE A VENUE ID: ask about that order specifically.
        st = None
        try:
            st = adapter.order_status(vid)
        except Exception as exc:                            # noqa: BLE001
            rec["status_error"] = "%s: %s" % (type(exc).__name__,
                                              str(exc)[:200])
        if not isinstance(st, dict) or not st:
            got = await mark_unresolved(
                conn, iid, "the venue has no readable record of order %s, so "
                           "its outcome is not established" % vid)
            out["unresolved"].append(dict(rec, **got))
            continue
        # THE EXECUTIONS, THROUGH THE ONE READER. `order_status` returns them
        # under `executions`, never `fills`, and reading the wrong key is what
        # made a fill during downtime invisible.
        read = executions_of(st)
        # THE DIRECTION COMES FROM THE INTENT, NOT FROM A DEFAULT.
        #
        # THE DEFECT THIS CLOSES. `ingest_fills` defaults to ENTRY, and since
        # exits became intents in their own right, recovery iterates them too --
        # so an exit whose fill arrived during downtime was booked as an ENTRY.
        # The residual then GREW by the quantity we had just sold: a 10-contract
        # position that fully exited read 20 held. Found by the scheduled-path
        # lifecycle test, which is the only place the two halves meet.
        direction = "EXIT" if str(r["kind"]) == "EXIT" else "ENTRY"
        rec["direction"] = direction
        ing = await ingest_fills(conn, iid, read["executions"], at=at,
                                direction=direction)
        after = await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1", iid)
        if st.get("executions") is None:
            # "THE VENUE SENT NO LIST" IS NOT "THE VENUE SENT AN EMPTY ONE" --
            # `pmus.order_status` is explicit about the difference, and a
            # partially or fully filled order whose executions we cannot see
            # is a quantity we cannot place.
            seen = float(st.get("filled_shares") or 0)
            if seen > float(ing.get("filled_qty_from_the_ledger") or 0) + 1e-9:
                got = await mark_unresolved(
                    conn, iid,
                    "the venue reports %s filled shares on order %s and sent "
                    "no executions list, so %s shares are unplaceable"
                    % (seen, vid,
                       round(seen - float(ing.get(
                           "filled_qty_from_the_ledger") or 0), 6)))
                out["unresolved"].append(dict(
                    rec, case="FILLED_SHARES_WITH_NO_EXECUTIONS", **got))
                continue
        out["reconciled"].append(dict(
            rec, case=rec.pop("case_pre", "READ_FROM_THE_VENUE"),
            venue_state=st.get("state") or st.get("status"),
            state_now=after,
            executions_read=len(read["executions"]),
            executions_skipped=read["skipped"],
            venue_filled_shares=st.get("filled_shares"),
            fills_written=len(ing.get("written") or []),
            fills_already_held=len(ing.get("already_held") or []),
            unresolved_executions=ing.get("unresolved_count"),
            residual_qty=ing.get("residual_qty"),
            filled_qty_from_the_ledger=ing.get(
                "filled_qty_from_the_ledger")))
    for vid, o in theirs.items():
        known = await conn.fetchval(
            "SELECT 1 FROM bettor_funded_intents WHERE venue_order_id=$1",
            vid)
        if not known:
            out["orphans"].append({
                "venue_order_id": vid,
                "what": ("the venue holds an order this book has no intent "
                         "for. It is reported and NEVER adopted as ours")})
    return dict(out, ok=True)


# ── 4 · THE FUNDED BOOK'S OWN NUMBERS ───────────────────────────────

async def exposure(conn, *, account_id: str, venue: str) -> dict:
    """WHAT THIS LANE HAS AT RISK: outstanding orders AND residual holdings.

    TWO DEFECTS, ONE MEASUREMENT.

    The first: a headroom check that counts only FILLED positions is blind to
    exactly the window in which a second order does damage -- the request is in
    flight, nothing has filled, and the rail says there is room. Pending
    intents and in-flight reservations are collateral the venue may take at any
    moment, so they count at full size.

    The second, and it was the worse one: this read the OUTSTANDING-ORDER
    predicate and called it exposure. The moment an entry FILLED, its holding
    left every rail -- per-event, per-market, correlated -- at the exact point
    the exposure is at its MAXIMUM, because the contracts are now actually
    owned. A filled order is finished; the position it created is not.

    So a position contributes here while EITHER is true, and how much it
    contributes depends on which:

      * an OUTSTANDING order reserves its whole clip, because the resting
        remainder can fill at any moment;
      * a TERMINAL order with residual inventory contributes the collateral of
        the contracts still held, pro rata.

    Only an evidenced exit or an authoritative settlement -- `closed_at` with
    one of `CLOSURE_REASONS` -- takes a holding out of these numbers.
    """
    open_rows = await conn.fetch(
        "SELECT intent_id, event_key, us_market_slug, kind, state, "
        "       collateral_usd::float8 AS collateral, "
        "       quantity::float8 AS quantity, "
        "       residual_qty::float8 AS residual, closed_at, "
        "       bettor_funded_order_is_outstanding(state) AS outstanding, "
        "       bettor_funded_holds_inventory(residual_qty, closed_at) "
        "           AS holding "
        "  FROM bettor_funded_intents "
        " WHERE kind='ENTRY' "
        "   AND bettor_funded_position_is_open(state, residual_qty, closed_at)"
        "   AND account_id=$1 AND upper(venue)=upper($2)",
        str(account_id), str(venue))
    cash = await conn.fetchrow(
        "SELECT coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.cash_usd "
        "                        ELSE 0 END),0)::float8 AS entry_cash, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.cash_usd "
        "                        ELSE 0 END),0)::float8 AS exit_cash, "
        "       coalesce(sum(f.fee_usd),0)::float8 AS fee, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.qty "
        "                        ELSE -f.qty END),0)::float8 AS net_qty "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.account_id=$1 AND upper(i.venue)=upper($2)",
        str(account_id), str(venue))
    per_event: dict = {}
    per_market: dict = {}
    at_risk = 0.0
    outstanding_only = 0.0
    holding_only = 0.0
    rows = []
    for r in open_rows:
        c = float(r["collateral"])
        q = float(r["quantity"] or 0)
        res = float(r["residual"] or 0)
        if r["outstanding"]:
            # THE WHOLE CLIP. The resting remainder can fill at any moment and
            # the filled part is already owned, so the reservation is the
            # order's full collateral either way.
            mine, why = c, "the order is outstanding, so its whole clip is reserved"
        else:
            # TERMINAL ORDER, CONTRACTS STILL HELD: the residual, pro rata.
            mine = (c * res / q) if q > 0 else c
            why = ("the order is terminal and %s of %s contracts are still "
                   "held" % (res, q))
        at_risk += mine
        if r["outstanding"]:
            outstanding_only += mine
        else:
            holding_only += mine
        per_event[r["event_key"]] = per_event.get(r["event_key"], 0.0) + mine
        per_market[r["us_market_slug"]] = per_market.get(
            r["us_market_slug"], 0.0) + mine
        rows.append(dict(r, at_risk_usd=round(mine, 6), why=why))
    # CAPITAL-HOURS, integrated over the entry fills this lane actually holds.
    ch = await conn.fetchval(
        "SELECT coalesce(sum(f.cash_usd * GREATEST(0, EXTRACT(EPOCH FROM "
        "       (now() - f.at)) / 3600.0)),0)::float8 "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE f.direction='ENTRY' AND i.account_id=$1 "
        "   AND upper(i.venue)=upper($2)", str(account_id), str(venue))
    held = await conn.fetchval(
        "SELECT coalesce(sum(residual_qty),0)::float8 "
        "  FROM bettor_funded_intents "
        " WHERE kind='ENTRY' AND closed_at IS NULL AND account_id=$1 "
        "   AND upper(venue)=upper($2)", str(account_id), str(venue))
    return {
        "account_id": account_id, "venue": venue,
        "open_positions": rows,
        # the historical key name, and it now means "every open position",
        # outstanding order or residual holding alike.
        "live_intents": rows,
        "outstanding_orders": [dict(r) for r in rows if r["outstanding"]],
        "residual_holdings": [dict(r) for r in rows if r["holding"]],
        "pending_and_in_flight_collateral_usd": round(at_risk, 6),
        "collateral_of_outstanding_orders_usd": round(outstanding_only, 6),
        "collateral_of_residual_holdings_usd": round(holding_only, 6),
        "filled_cash_usd": round(float(cash["entry_cash"]), 6),
        "exit_proceeds_usd": round(float(cash["exit_cash"]), 6),
        "filled_fees_usd": round(float(cash["fee"]), 6),
        # NET, so an exited position stops consuming the inventory rail.
        "contracts_held": round(float(held or 0.0), 6),
        "contracts_net_from_the_fill_ledger": round(float(cash["net_qty"]), 6),
        "capital_hours_usd_h": round(float(ch or 0.0), 6),
        "per_event_collateral_usd": {k: round(v, 6)
                                     for k, v in per_event.items()},
        "per_market_collateral_usd": {k: round(v, 6)
                                      for k, v in per_market.items()},
        "counts_pending_at_full_size": True,
        "counts_residual_holdings_after_the_order_is_terminal": True,
        "leaves_exposure_only_on": list(CLOSURE_REASONS),
        "scope": ("bettor_funded_* only. No modelled row and no shadow lane "
                  "contributes to any number here"),
    }


def drawdown_from(closed: list[dict]) -> dict:
    """THE REALISED EQUITY CURVE AND ITS WORST PEAK-TO-TROUGH.

    Pure, so the loss stop can be tested without a database.

    `closed` is one entry per BOOKED RESULT -- `{"intent_id", "at", "net"}` --
    ordered here by the instant the result was taken. A position's entry cost
    is not a loss while the contracts are still owned, and a round trip that
    came back flat is not a drawdown just because cash left in between.

    THE ARGUMENT NAME IS HISTORICAL AND NARROWER THAN THE INPUT (2026-09-28).
    It is no longer only closures. `realised` now also passes the
    `realised_on_sold` result of every OPEN position that has sold some of its
    inventory, booked at its last exit fill. This function needed no change --
    it was always "a sequence of booked results" -- but the previous wording
    ("one entry per CLOSED position", "an open position contributes nothing")
    described the CALLER'S filter as though it were this function's rule, and
    that is how a caller's defect ends up looking like a design decision.

    WHAT THIS FUNCTION DOES NOT DECIDE. Which results are bookable. That is
    `realised`'s job and it is where the defect was: it booked only closures,
    so a loss already taken on a partial exit was absent from the number the
    loss stop compares against.

    THE ORIGINAL DEFECT THIS CLOSES. `check_rails` hardcoded MAX_DRAWDOWN to
    0.0 on the grounds that nothing had settled. That describes today. It
    cannot implement a loss stop tomorrow, because the moment something DOES
    settle the rail still reads zero and the stop never trips.
    """
    seq = sorted(list(closed or ()), key=lambda c: (c.get("at") or 0))
    cum = 0.0
    peak = 0.0
    worst = 0.0
    at_worst = None
    curve = []
    for c in seq:
        cum += float(c.get("net") or 0.0)
        peak = max(peak, cum)
        dd = peak - cum
        if dd > worst:
            worst, at_worst = dd, c.get("intent_id")
        curve.append({"intent_id": c.get("intent_id"), "at": c.get("at"),
                      # WHICH KIND OF RESULT THIS POINT IS. Without it a
                      # reader cannot tell a closure from a partial exit,
                      # and the whole correction becomes invisible again.
                      "component": c.get("component"),
                      "net_usd": round(float(c.get("net") or 0.0), 6),
                      "cumulative_realised_usd": round(cum, 6),
                      "peak_usd": round(peak, 6),
                      "drawdown_usd": round(dd, 6)})
    n_closed = sum(1 for c in seq
                   if c.get("component") != "REALISED_ON_SOLD_WHILE_OPEN")
    return {"realised_pnl_usd": round(cum, 6),
            "peak_realised_usd": round(peak, 6),
            "max_drawdown_usd": round(worst, 6),
            "worst_at_intent": at_worst,
            # `closed_positions` KEPT AT ITS TRUE MEANING. `check_rails`
            # prints it as "over %d closed funded position(s)", so letting
            # it become a count of all booked results would have made that
            # basis line assert a closure that did not happen.
            "closed_positions": n_closed,
            "booked_results": len(seq),
            "partially_realised_results": len(seq) - n_closed,
            "curve": curve,
            "basis": ("the realised equity curve over every result already "
                      "TAKEN, in the order it was taken: each closed "
                      "position's net economic events, AND the "
                      "realised-on-sold result of each open position that "
                      "has sold inventory, booked at its last exit fill. "
                      "The cost of inventory still HELD is not in here -- "
                      "that is an asset at cost. A loss already realised on "
                      "a partial exit IS, because the contracts are gone "
                      "and the cash came back short")}


async def realised(conn, *, account_id: str, venue: str) -> dict:
    """REALISED P&L AND DRAWDOWN, over EVERY result that has been taken.

    ── THE DEFECT THIS CLOSES, AND IT DISARMED THE LOSS STOP ─────────
    This function filtered `closed_at IS NOT NULL`. Every consumer of
    MAX_DRAWDOWN reads its `max_drawdown_usd`:
    `bettor_funded_execution.check_rails` (which refuses new exposure),
    `bettor_funded_management`'s `loss_stop`, and `pnl`. So a position
    that had sold part of its inventory at a loss -- cash gone, contracts
    gone, result determined -- contributed NOTHING to the measurement, and
    the stop could be breached without ever tripping.

    The controlled demonstration showed it exactly: 9 of 15 contracts sold
    at 0.41 on a 0.60 basis, a -$2.01 realised result including fees, and
    `max_drawdown_usd` read 0.00. Under a $40 stop that is a rounding
    error; under a stop the position could actually reach it is the
    difference between a control that works and a control that reports.

    ── WHY "ONLY AT CLOSURE" WAS EVER DEFENSIBLE, AND WHY IT IS NOT ──
    The original reasoning is on `drawdown_from`: "a position's entry cost
    is not a loss while the contracts are still owned". That is TRUE and it
    is still respected -- the remaining basis is an asset at cost and is
    NOT booked here. What was wrong is the inference that therefore nothing
    about an open position is realised. Selling 9 contracts below cost
    realises a loss on those 9. The contracts are gone; the cash came back
    short. No later event can undo it. It is realised in the only sense the
    word has.

    ── THE TWO COMPONENTS, AND THEY ARE LABELLED ─────────────────────
      A · CLOSED positions   the whole net, booked at `closed_at`.
      B · OPEN positions that have sold some inventory: their
          `partial_realisation` result, booked at the LAST EXIT FILL --
          the instant the result was actually taken, which is what an
          equity curve orders by. Not `now`, which would make the curve
          move when nothing happened.

    Both go into ONE time-ordered curve, because a drawdown is peak-to-
    trough over the sequence of results and two separate curves cannot be
    combined afterwards: the worst trough may lie between a closure and a
    partial exit, and summing two maxima would miss it.

    ── WHAT IS STILL NOT IN HERE, DELIBERATELY ───────────────────────
    UNREALISED movement on the residual. That needs a funded mark and this
    lane has no funded mark source, so it is reported as unknown by name
    (`unrealised_on_open_inventory`) and the declared policy is applied by
    the caller -- `loss_controls` below. It is NEVER read as zero, and
    this realised figure is NEVER presented as a maximum possible loss.
    """
    rows = await conn.fetch(
        "SELECT i.intent_id, "
        "       EXTRACT(EPOCH FROM i.closed_at)::float8 AS closed_epoch, "
        "       i.closed_reason, "
        "       coalesce(sum(e.amount_usd),0)::float8 AS net, "
        "       count(e.event_id) AS events, "
        "       count(e.event_id) FILTER (WHERE e.provisional) AS provisional "
        "  FROM bettor_funded_intents i "
        "  LEFT JOIN bettor_funded_economics e ON e.intent_id=i.intent_id "
        " WHERE i.kind='ENTRY' AND i.closed_at IS NOT NULL "
        "   AND i.account_id=$1 AND upper(i.venue)=upper($2) "
        " GROUP BY i.intent_id, i.closed_at, i.closed_reason",
        str(account_id), str(venue))
    closed = [{"intent_id": r["intent_id"], "at": float(r["closed_epoch"]),
               "net": float(r["net"]), "closed_reason": r["closed_reason"],
               "events": int(r["events"]),
               "provisional_events": int(r["provisional"]),
               "component": "CLOSED_POSITION_NET"} for r in rows]

    # ── B · THE OPEN POSITIONS THAT HAVE ALREADY SOLD SOMETHING ─────
    #
    # ONE QUERY, aggregated per parent intent, rather than N calls to
    # `realised_on_sold`. The arithmetic is still that function's --
    # `partial_realisation` is applied below -- so the two cannot diverge.
    #
    # THE JOIN IS `f.intent_id=i.intent_id OR i.parent_intent_id`. An exit
    # is its own intent with the entry as parent, so fills must be gathered
    # across the family exactly as `realised_on_sold` gathers them. Reading
    # only the entry's own fills would find no exits at all and this whole
    # component would silently be zero -- the same failure in a new place.
    prows = await conn.fetch(
        "SELECT p.intent_id, "
        "       p.residual_qty::float8 AS residual, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.cash_usd "
        "                       ELSE 0 END),0)::float8 AS entry_cash, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.qty "
        "                       ELSE 0 END),0)::float8 AS entry_qty, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.cash_usd "
        "                       ELSE 0 END),0)::float8 AS exit_cash, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.qty "
        "                       ELSE 0 END),0)::float8 AS exit_qty, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.fee_usd "
        "                       ELSE 0 END),0)::float8 AS entry_fees, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.fee_usd "
        "                       ELSE 0 END),0)::float8 AS exit_fees, "
        "       EXTRACT(EPOCH FROM max(f.at) FILTER "
        "               (WHERE f.direction='EXIT'))::float8 AS last_exit, "
        "       count(*) FILTER (WHERE f.fee_state=$3) AS provisional "
        "  FROM bettor_funded_intents p "
        "  JOIN bettor_funded_intents k "
        "    ON k.intent_id=p.intent_id OR k.parent_intent_id=p.intent_id "
        "  JOIN bettor_funded_fills f ON f.intent_id=k.intent_id "
        " WHERE p.kind='ENTRY' AND p.closed_at IS NULL "
        "   AND p.account_id=$1 AND upper(p.venue)=upper($2) "
        " GROUP BY p.intent_id, p.residual_qty "
        "HAVING coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.qty "
        "                        ELSE 0 END),0) > 0",
        str(account_id), str(venue), FEE_PROVISIONAL)
    partial = []
    for r in prows:
        m = partial_realisation(
            entry_qty=r["entry_qty"], entry_cash=r["entry_cash"],
            entry_fees=r["entry_fees"], exit_qty=r["exit_qty"],
            exit_cash=r["exit_cash"], exit_fees=r["exit_fees"],
            residual=r["residual"])
        net = m["realised_on_sold_usd"]
        # NO PER-CONTRACT BASIS MEANS NO RESULT TO BOOK, and a position with
        # exit fills but no entry fills is a reconciliation problem, not a
        # zero. It is carried with `net: None` so it appears in the ledger
        # and is excluded from the curve rather than counted as flat.
        partial.append({
            "intent_id": r["intent_id"],
            "at": (None if r["last_exit"] is None
                   else float(r["last_exit"])),
            "net": net,
            "closed_reason": None,
            "events": None,
            "provisional_events": int(r["provisional"] or 0),
            "component": "REALISED_ON_SOLD_WHILE_OPEN",
            "residual_qty": m["residual_qty"],
            "remaining_basis_usd": m["remaining_basis_usd"],
            "sold_qty": m["sold_qty"],
            "components": m,
        })

    # THE CURVE IS OVER BOTH, and rows with no bookable result or no
    # instant to book it at are excluded from the ORDERING rather than
    # given a default -- `drawdown_from` sorts on `at` and a None there
    # would place a real result at the start of time.
    bookable = [c for c in (closed + partial)
                if c.get("net") is not None and c.get("at") is not None]
    unbookable = [c for c in (closed + partial)
                  if c.get("net") is None or c.get("at") is None]
    dd = drawdown_from(bookable)
    prov = sum(int(c.get("provisional_events") or 0)
               for c in (closed + partial))
    partial_sum = sum(float(c["net"]) for c in partial
                      if c.get("net") is not None)
    return dict(
        dd, closed=closed,
        # ── THE SECOND COMPONENT, NAMED AND SEPARABLE ───────────────
        partially_realised_open=partial,
        partially_realised_open_positions=len(partial),
        partially_realised_usd=round(partial_sum, 6),
        realised_components=("CLOSED_POSITION_NET + "
                             "REALISED_ON_SOLD_WHILE_OPEN"),
        includes_open_position_partial_results=True,
        # WHAT COULD NOT BE BOOKED AND WHY, rather than a quiet omission.
        not_bookable=[{"intent_id": c.get("intent_id"),
                       "component": c.get("component"),
                       "why": ("no per-contract basis (no entry fills)"
                               if c.get("net") is None
                               else "no exit fill instant to book it at")}
                      for c in unbookable],
        provisional_events_in_realised=prov,
        realised_is_provisional=bool(prov),
        # ── AND WHAT THIS NUMBER IS NOT ─────────────────────────────
        unrealised_on_open_inventory=NOT_IDENTIFIED,
        why_unrealised_is_not_identified=(
            "marking open inventory needs a funded mark and this lane has "
            "no funded mark source. Reported NOT_IDENTIFIED, never 0, and "
            "this realised figure is NOT a maximum possible loss -- see "
            "`loss_controls`, which applies the declared policy for an "
            "unavailable mark"),
        provisional_note=(
            "%d economic event(s) inside the realised total are "
            "PROVISIONAL -- the venue has not stated a fee it was "
            "charged -- so this number is explicitly incomplete "
            "rather than quietly exact" % prov) if prov else None)


async def pnl(conn, *, account_id: str, venue: str) -> dict:
    """THE FUNDED LANE'S P&L, from the funded economics ledger.

    THE DEFECT THIS CLOSES. `realised_pnl_usd` was the literal `0.0` with a
    note saying nothing had settled. That note was true and the number was
    still wrong as an implementation: a constant cannot become nonzero, so the
    first settlement would have gone unrecorded in the very field a loss stop
    reads. Realised P&L is now the SUM over `bettor_funded_economics`, and what
    genuinely cannot be measured -- unrealised P&L, which needs a funded mark
    this lane has no source for -- says so by name instead of reading zero.
    """
    row = await conn.fetchrow(
        "SELECT count(*) AS fills, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.qty "
        "                        ELSE -f.qty END),0)::float8 AS net_qty, "
        "       coalesce(sum(CASE WHEN f.direction='ENTRY' THEN f.cash_usd "
        "                        ELSE 0 END),0)::float8 AS entry_cash, "
        "       coalesce(sum(CASE WHEN f.direction='EXIT' THEN f.cash_usd "
        "                        ELSE 0 END),0)::float8 AS exit_cash, "
        "       coalesce(sum(f.fee_usd),0)::float8 AS fee, "
        "       coalesce(sum(f.expected_fee_usd),0)::float8 AS exp_fee, "
        "       count(*) FILTER (WHERE f.fee_state='PROVISIONAL') AS prov, "
        "       count(*) FILTER (WHERE f.fee_state='DISAGREES') AS disagree "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.account_id=$1 AND upper(i.venue)=upper($2)",
        str(account_id), str(venue))
    real = await realised(conn, account_id=account_id, venue=venue)
    # THE OPEN SIDE, kept apart. Its cost is an asset at cost, not a result.
    openside = await conn.fetchrow(
        "SELECT coalesce(sum(e.amount_usd),0)::float8 AS net, "
        "       count(e.event_id) FILTER (WHERE e.provisional) AS provisional "
        "  FROM bettor_funded_economics e JOIN bettor_funded_intents i "
        "    ON i.intent_id=e.intent_id "
        " WHERE i.closed_at IS NULL AND i.account_id=$1 "
        "   AND upper(i.venue)=upper($2)", str(account_id), str(venue))
    byk = await conn.fetch(
        "SELECT e.kind, coalesce(sum(e.amount_usd),0)::float8 AS amt, "
        "       count(*) AS n "
        "  FROM bettor_funded_economics e JOIN bettor_funded_intents i "
        "    ON i.intent_id=e.intent_id "
        " WHERE i.account_id=$1 AND upper(i.venue)=upper($2) "
        " GROUP BY e.kind ORDER BY e.kind", str(account_id), str(venue))
    unres = await conn.fetch(
        "SELECT intent_id, unresolved_reason FROM bettor_funded_intents "
        " WHERE state='UNRESOLVED' AND account_id=$1 "
        "   AND upper(venue)=upper($2)", str(account_id), str(venue))
    feediff = await conn.fetch(
        "SELECT f.fill_id, f.intent_id, f.expected_fee_usd::float8 AS exp, "
        "       f.observed_fee_usd::float8 AS obs, f.fee_state, "
        "       f.fee_reconciliation "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE f.fee_state <> 'RECONCILED' AND i.account_id=$1 "
        "   AND upper(i.venue)=upper($2) ORDER BY f.at",
        str(account_id), str(venue))
    entry, exitc = float(row["entry_cash"]), float(row["exit_cash"])
    fee, expfee = float(row["fee"]), float(row["exp_fee"])
    return {
        "account_id": account_id, "venue": venue,
        "fills": int(row["fills"]),
        "contracts": round(float(row["net_qty"]), 6),
        "cost_basis_usd": round(entry, 6),
        "exit_proceeds_usd": round(exitc, 6),
        # WHAT WAS BOOKED, WHAT WAS EXPECTED, AND THE DIFFERENCE. Booking the
        # estimate as the charge is what hid this; the gap is now a number.
        "fees_usd": round(fee, 6),
        "expected_fees_usd": round(expfee, 6),
        "fee_variance_usd": round(fee - expfee, 6),
        "fills_with_provisional_fees": int(row["prov"]),
        "fills_where_the_venue_charged_something_else": int(row["disagree"]),
        "fee_discrepancies": [dict(r) for r in feediff],
        "cash_out_the_door_usd": round(entry + fee - exitc, 6),
        "realised_pnl_usd": real["realised_pnl_usd"],
        "realised_basis": real["basis"],
        "realised_is_provisional": real["realised_is_provisional"],
        "realised_provisional_note": real["provisional_note"],
        "max_drawdown_usd": real["max_drawdown_usd"],
        "peak_realised_usd": real["peak_realised_usd"],
        "realised_curve": real["curve"],
        "closed_positions": real["closed_positions"],
        # THE PARTIAL COMPONENT, SURFACED HERE TOO. `max_drawdown_usd` above
        # now includes it, so a reader who cannot see the component would be
        # unable to reconcile the drawdown against the closed positions.
        "booked_results": real["booked_results"],
        "partially_realised_usd": real["partially_realised_usd"],
        "partially_realised_open_positions":
            real["partially_realised_open_positions"],
        "realised_not_bookable": real["not_bookable"],
        "open_position_net_cash_usd": round(float(openside["net"]), 6),
        "open_position_provisional_events": int(openside["provisional"]),
        "open_position_net_cash_is_not_a_result": (
            "it is (basis out - proceeds back + fees) over the whole clip, "
            "so it mixes the realised result on what was sold with the cost "
            "still tied up in what is held. `partially_realised_usd` is the "
            "result; this is cash"),
        # NAMED, NOT ZEROED.
        "unrealised_pnl_usd": NOT_IDENTIFIED,
        "unrealised_basis": (
            "UNMEASURED. Marking residual funded inventory needs a funded "
            "mark, and this lane has no mark source it would stand behind, so "
            "this is reported as unmeasured rather than as zero"),
        "economics_by_kind": [dict(r) for r in byk],
        "unresolved_intents": [dict(r) for r in unres],
        "is_not_summed_with": ("the shadow, acceptance, demonstration or "
                              "UNCLASSIFIED books. This is the funded lane "
                              "alone"),
    }


#: WHAT WE DO WHEN AN OPEN POSITION CANNOT BE MARKED. Declared as a named
#: policy rather than decided inside a formula, so it can be read, tested
#: and argued with. The two candidates were:
#:
#:   TREAT_AS_ZERO     the unmarked position is assumed to be worth what
#:                     it cost. Never. This is the favourable assumption,
#:                     and it is the one that makes a blown position look
#:                     like a flat one.
#:   WORST_CASE_TOTAL_LOSS  the whole remaining basis is treated as
#:                     potentially lost for the purpose of stating
#:                     WORST-CASE OUTSTANDING EXPOSURE -- and NOT booked
#:                     into realised drawdown, because it has not happened.
#:
#: The second is the policy. It is already what `exposure_from_rows` does
#: for the shadow rails (`DRAWDOWN_IS_WORST_CASE`); this states it for the
#: funded book and, critically, keeps it in a DIFFERENT FIELD from the
#: realised number, so a worst case can never be reported as a result.
UNMARKED_POLICY = "WORST_CASE_TOTAL_LOSS_FOR_EXPOSURE_NOT_FOR_REALISED"


async def loss_controls(conn, *, account_id: str, venue: str,
                        approved_limits: dict | None = None) -> dict:
    """THE FOUR QUANTITIES, KEPT APART, AND WHAT EACH CONTROL DOES WITH THEM.

    ── WHY THIS FUNCTION EXISTS ──────────────────────────────────────
    Because four different numbers were being reached for through one
    another, and each substitution is a specific wrong answer:

      1 · REALISED DRAWDOWN            worst peak-to-trough of results
                                       already TAKEN. Governs the loss
                                       stop. Was blind to partial exits.
      2 · UNREALISED P&L               movement on inventory still HELD.
                                       Needs a mark. UNKNOWN here, and
                                       unknown is not zero.
      3 · CASH USAGE                   money actually out of the account.
                                       Not a loss: most of it is an asset
                                       at cost.
      4 · WORST-CASE OUTSTANDING       what could still be lost if every
          EXPOSURE                     unmarked holding settled worthless.
                                       A bound, not an expectation, and
                                       never a result.

    Reporting 1 as 4 understates risk. Reporting 4 as 1 fabricates a loss.
    Reporting 3 as 1 overstates the loss by the remaining basis -- which is
    exactly the -$5.71-versus--$2.01 error the demonstration surfaced.
    Reporting 2 as 0 is the one that lets a blown position look flat.

    ── AND WHAT THIS IS NOT ──────────────────────────────────────────
    `loss_stop_limit_usd` is a TRIGGER, not a maximum possible loss. Once
    tripped it refuses new exposure; it does not and cannot liquidate
    inventory at a price, so the loss on what is already held can exceed
    it. `worst_case_total_loss_usd` is the figure that bounds that, and the
    two are reported side by side so the trigger is never read as the bound.
    """
    real = await realised(conn, account_id=account_id, venue=venue)
    exp = await exposure(conn, account_id=account_id, venue=venue)

    # ── 3 · CASH USAGE. Out the door, net of what came back. Stated as
    # cash, never as a result: the remaining basis inside it is inventory.
    cash_out = round(float(exp["filled_cash_usd"])
                     + float(exp["filled_fees_usd"])
                     - float(exp["exit_proceeds_usd"]), 6)

    # ── 4 · WORST-CASE OUTSTANDING EXPOSURE. The remaining basis of every
    # open position that has no mark, plus collateral the venue may take on
    # anything still outstanding. Under UNMARKED_POLICY the holdings are
    # counted as a total loss; this lane has no funded mark source, so that
    # is every holding, and `unmarked_holdings` says how many.
    remaining_basis = 0.0
    unmarked = 0
    for p in (real.get("partially_realised_open") or []):
        rb = p.get("remaining_basis_usd")
        if rb is not None:
            remaining_basis += float(rb)
            unmarked += 1
    # Positions that have sold NOTHING are not in `partially_realised_open`,
    # so their basis has to come from the exposure read -- otherwise the
    # worst case would cover only the positions that happened to be
    # partially exited, which is the narrower set and the wrong bound.
    holdings_collateral = float(exp["collateral_of_residual_holdings_usd"])
    outstanding_collateral = float(exp["collateral_of_outstanding_orders_usd"])
    worst_case = round(max(remaining_basis, holdings_collateral)
                       + outstanding_collateral, 6)

    dd = float(real["max_drawdown_usd"])
    limit = None
    if approved_limits:
        try:
            limit = float(approved_limits.get("MAX_DRAWDOWN"))
        except (TypeError, ValueError):
            limit = None
    tripped = (None if not limit or limit <= 0 else bool(dd > limit + 1e-9))

    return {
        "account_id": account_id, "venue": venue,
        # ── 1 · REALISED DRAWDOWN ───────────────────────────────────
        "realised_drawdown_usd": round(dd, 6),
        "realised_pnl_usd": real["realised_pnl_usd"],
        "realised_basis": real["basis"],
        "realised_includes_partial_exits": True,
        "realised_components": real["realised_components"],
        "partially_realised_usd": real["partially_realised_usd"],
        "partially_realised_open_positions":
            real["partially_realised_open_positions"],
        "realised_is_provisional": real["realised_is_provisional"],
        # ── 2 · UNREALISED P&L ──────────────────────────────────────
        "unrealised_pnl_usd": NOT_IDENTIFIED,
        "unrealised_why": (
            "marking open funded inventory needs a funded mark source and "
            "this lane has none it would stand behind. UNKNOWN, not 0 -- "
            "a zero here would assert that inventory is worth its cost"),
        "unmarked_policy": UNMARKED_POLICY,
        "unmarked_holdings": unmarked,
        # ── 3 · CASH USAGE ──────────────────────────────────────────
        "cash_used_usd": cash_out,
        "cash_used_is_not_a_loss": (
            "cash out minus proceeds back. The remaining basis inside it is "
            "INVENTORY, an asset at cost. Reading this as the loss "
            "overstates it by exactly that basis plus the residual's share "
            "of the entry fee"),
        "entry_cash_usd": exp["filled_cash_usd"],
        "entry_fees_usd": exp["filled_fees_usd"],
        "exit_proceeds_usd": exp["exit_proceeds_usd"],
        # ── 4 · WORST-CASE OUTSTANDING EXPOSURE ─────────────────────
        "worst_case_total_loss_usd": worst_case,
        "worst_case_basis": (
            "every unmarked holding settles worthless (%s), plus collateral "
            "the venue may take on orders still outstanding. A BOUND on "
            "what is still at risk, not a forecast and not a result"
            % UNMARKED_POLICY),
        "residual_holdings_collateral_usd": round(holdings_collateral, 6),
        "outstanding_orders_collateral_usd": round(outstanding_collateral, 6),
        "remaining_basis_of_partially_exited_usd": round(remaining_basis, 6),
        "contracts_held": exp["contracts_held"],
        # ── THE CONTROLS: SCOPE, TRIGGER, RESPONSE ──────────────────
        "controls": [
            {
                "control": "MAX_DRAWDOWN",
                "scope": ("the funded lane's realised equity curve for this "
                          "account and venue. Not the shadow, acceptance or "
                          "demonstration books"),
                "measures": "realised_drawdown_usd",
                "trigger": ("measured realised drawdown exceeds the "
                            "owner-approved MAX_DRAWDOWN"),
                "response": ("REFUSES NEW EXPOSURE. Every entry goes through "
                             "bettor_funded_execution.check_rails, which "
                             "blocks on an EXCEEDED rail. Servicing an "
                             "existing position is NOT blocked -- that is "
                             "ownership-bound and gated by check_servicing, "
                             "not by submission authority -- and neither is "
                             "reconciliation"),
                "limit_usd": limit,
                "measured_usd": round(dd, 6),
                "tripped": tripped,
                "why_no_verdict": (None if tripped is not None else
                                   "no owner-approved MAX_DRAWDOWN exists, "
                                   "so there is no threshold to compare "
                                   "against. The measurement is real; the "
                                   "threshold is an owner input"),
                "is_not_a_maximum_loss": (
                    "this is a TRIGGER on realised results. It refuses new "
                    "exposure; it cannot liquidate inventory at a price, so "
                    "the loss on what is already held can exceed it. "
                    "worst_case_total_loss_usd is the bound"),
            },
            {
                "control": "MAX_RESIDUAL_INVENTORY",
                "scope": "contracts the funded book holds, net of exits",
                "measures": "contracts_held",
                "trigger": "held contracts plus the proposed order exceed it",
                "response": "refuses the new order; holdings untouched",
            },
            {
                "control": "MAX_CAPITAL_DEPLOYED",
                "scope": "cash and fees out plus live collateral",
                "measures": "cash_used_usd and live collateral",
                "trigger": "the sum plus this order exceeds the limit",
                "response": "refuses the new order",
            },
            {
                "control": "FUNDED_EXIT_SUBMISSION_ENABLED",
                "scope": "whether a funded exit may actually be sent",
                "measures": "not a measurement -- a shipped constant",
                "trigger": "n/a",
                "response": ("ships False, so an exit is decided, priced and "
                             "recorded but never submitted. This is "
                             "CONTAINMENT and it is not one of the four "
                             "quantities"),
            },
        ],
        "four_quantities_are_distinct": (
            "realised_drawdown_usd, unrealised_pnl_usd, cash_used_usd and "
            "worst_case_total_loss_usd are four different measurements of "
            "four different things. None is defined as another"),
    }


#: The scheduled cycle's heartbeat row. Named here rather than imported
#: so this reader does not pull a worker module into the API's request
#: path; a test cross-checks it against the loop's own constant.
SCHEDULED_CYCLE_KEY = "ext_pinnacle_last_cycle"


async def last_scheduled_decision(conn) -> dict:
    """WHAT THE LAST SCHEDULED SERVICING PASS DECIDED, as it recorded it.

    THE GAP THIS CLOSES. The command centre could show the funded BOOK --
    what is held, what filled, what is unresolved -- and not the
    DECISION. So an operator looking at 6 held contracts had no way to
    learn whether the lane had chosen to hold them, tried to sell them
    and been refused, or never considered them. The three are different
    situations with the same row.

    IT IS THE LAST PASS, NOT A HISTORY, and says so. One row per position,
    overwritten every cycle. A decision log is a table and this is a
    heartbeat; promising more than one pass here would be inventing
    durability the write does not have.

    NEVER RAISES ON A MISSING OR MALFORMED ROW. "No cycle has recorded a
    decision" is a legitimate state -- a fresh database, a loop that has
    not run -- and it is reported as that rather than as an error or, worse,
    as an empty decision set that reads like "nothing to do".
    """
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            SCHEDULED_CYCLE_KEY)
    except Exception as exc:                                   # noqa: BLE001
        return {"available": False,
                "why": "the cycle heartbeat could not be read: %s"
                       % type(exc).__name__}
    if not raw:
        return {"available": False,
                "why": ("no scheduled cycle has recorded a decision in this "
                        "database. This is not an empty decision set -- "
                        "nothing has run"),
                "key": SCHEDULED_CYCLE_KEY}
    try:
        beat = json.loads(raw)
    except (TypeError, ValueError):
        return {"available": False,
                "why": "the cycle heartbeat is not readable JSON",
                "key": SCHEDULED_CYCLE_KEY}
    if not isinstance(beat, dict):
        # VALID jsonb IS NOT NECESSARILY AN OBJECT. A scalar string or a
        # list parses cleanly and then raises AttributeError on `.get`,
        # inside a reader whose contract is not to raise.
        return {"available": False,
                "why": ("the cycle heartbeat holds a %s rather than an "
                        "object" % type(beat).__name__),
                "key": SCHEDULED_CYCLE_KEY}
    svc = beat.get("funded_servicing")
    if svc is None:
        return {"available": False,
                "at": beat.get("at"), "cycle_state": beat.get("state"),
                "writer": beat.get("writer"),
                "why": ("the last cycle recorded no servicing decision. A "
                        "build older than the one that persists it writes "
                        "this row without the field, so an absent decision "
                        "here means the SERVING BUILD, not an idle lane"),
                "key": SCHEDULED_CYCLE_KEY}
    return {
        "available": True,
        "at": beat.get("at"),
        "cycle_state": beat.get("state"),
        "cycle_label": beat.get("cycle_label"),
        "writer": beat.get("writer"),
        "servicing": svc,
        "is_the_last_pass_not_a_history": (
            "one row per position, overwritten each cycle. For the sequence "
            "of what happened, read the fills and the discrepancies"),
        "key": SCHEDULED_CYCLE_KEY,
    }


async def command_center(conn) -> dict:
    """THE WHOLE FUNDED BOOK AND EVERY UNRESOLVED DISCREPANCY IN IT.

    FOR THE COMMAND CENTRE, and shaped for it: one section, one book per
    account and venue, and the discrepancies as a FIRST-CLASS list rather than
    a footnote -- an unresolved intent, a fee the venue charged differently, a
    provisional economic event and an inventory holding with no supported exit
    are each things an operator must act on, and a panel that only totals
    numbers hides all four.

    IT IS NEVER SUMMED WITH ANYTHING. The shadow, acceptance, demonstration and
    UNCLASSIFIED books are modelled; this one is money. The two live in
    separate sections and the totals are not added, which is why this returns
    its own section rather than a row inside Positions.
    """
    # ── CAPABILITY BEFORE CONTENT ───────────────────────────────────
    #
    # On 2026-09-27 this section served `UndefinedColumnError: column
    # "residual_qty" does not exist` -- accurate, and only because the API
    # happens to catch exceptions around this call. A reader could not tell from
    # that whether the funded lane was BLOCKED or merely broken, and nothing in
    # it said that submission was stopped. The schema is therefore asked FIRST
    # and the answer is a capability statement, with the missing objects named.
    from . import bettor_funded_schema as FS

    schema = await FS.readiness(conn)
    if not schema.get("ok"):
        return {
            "section": "Funded book",
            "label": "REAL MONEY -- NEVER SUMMED WITH ANY MODELLED BOOK",
            "funded_capability": FS.CAPABILITY_BLOCKED,
            "refusal": schema.get("refusal"),
            "schema_readiness": schema,
            "books": [], "book_count": None,
            "unresolved_discrepancies": [],
            "unresolved_discrepancy_count": None,
            "counts_toward_strategy_performance": None,
            "why": schema.get("why"),
            "what_this_is_not": (
                "this is NOT an empty funded book. The book cannot be read at "
                "all in this database, and the numbers are withheld rather "
                "than shown as zeros -- a panel that reports 0 positions and "
                "$0.00 over a schema it cannot query is the failure mode this "
                "replaces"),
            "submission_is_blocked_too": (
                "bettor_funded_execution.submit_for_decision and "
                "bettor_funded_management.submit_exit refuse with the same "
                "refusal, so no order can be sent while this reads BLOCKED"),
            "the_rest_of_the_service_is_unaffected": True,
        }
    # ── THE LAST SCHEDULED DECISION, BESIDE THE BOOK IT ACTED ON ────
    #
    # Read from the cycle's own heartbeat row rather than recomputed, so
    # the panel shows WHAT THE LANE ACTUALLY DECIDED on its last pass --
    # not what a fresh evaluation would decide now against a different
    # book. The two answers differ, and only the first one explains the
    # state the book is in.
    decision = await last_scheduled_decision(conn)
    pairs = await conn.fetch(
        "SELECT account_id, venue, count(*) AS intents "
        "  FROM bettor_funded_intents GROUP BY account_id, venue "
        " ORDER BY account_id, venue")
    books = []
    for p in pairs:
        cls = classify_book(p["account_id"])
        books.append({
            "account_id": p["account_id"], "venue": p["venue"],
            "intents": int(p["intents"]),
            # THE CLASSIFICATION TRAVELS WITH THE NUMBERS, so a consumer
            # cannot read this book's P&L without also reading whether it
            # is allowed to be performance.
            "book_class": cls["book_class"],
            "counts_toward_strategy_performance": cls[
                "counts_toward_strategy_performance"],
            "classification_why": cls["why"],
            "exposure": await exposure(conn, account_id=p["account_id"],
                                      venue=p["venue"]),
            "pnl": await pnl(conn, account_id=p["account_id"],
                            venue=p["venue"])})
    demo_books = [b for b in books
                  if b["book_class"] == BOOK_CLASS_DEMONSTRATION]
    real_books = [b for b in books if b["book_class"] == BOOK_CLASS_FUNDED]
    unres = await conn.fetch(
        "SELECT intent_id, account_id, venue, us_market_slug, state, "
        "       unresolved_reason, venue_order_id, "
        "       residual_qty::float8 AS residual "
        "  FROM bettor_funded_intents WHERE state='UNRESOLVED' "
        " ORDER BY updated_at DESC")
    fees = await conn.fetch(
        "SELECT fill_id, intent_id, fee_state, "
        "       expected_fee_usd::float8 AS expected, "
        "       observed_fee_usd::float8 AS observed, fee_reconciliation "
        "  FROM bettor_funded_fills WHERE fee_state <> 'RECONCILED' "
        " ORDER BY at DESC")
    prov = await conn.fetch(
        "SELECT event_id, intent_id, kind, amount_usd::float8 AS amount, "
        "       basis FROM bettor_funded_economics WHERE provisional "
        " ORDER BY at DESC")
    stranded = await conn.fetch(
        "SELECT intent_id, account_id, venue, us_market_slug, state, "
        "       residual_qty::float8 AS residual, settlement "
        "  FROM bettor_funded_intents "
        " WHERE kind='ENTRY' "
        "   AND bettor_funded_holds_inventory(residual_qty, closed_at) "
        " ORDER BY created_at")
    discrepancies = []
    for r in unres:
        discrepancies.append({
            "kind": "AN_UNRESOLVED_INTENT", "intent_id": r["intent_id"],
            "detail": r["unresolved_reason"],
            "exposure": "PRESERVED",
            "what_it_means": ("the venue could not establish this order's "
                             "outcome. Its exposure still counts against "
                             "every rail and no order is resent")})
    for r in fees:
        discrepancies.append({
            "kind": ("A_FEE_THE_VENUE_CHARGED_DIFFERENTLY"
                     if r["fee_state"] == FEE_DISAGREES
                     else "A_FEE_THE_VENUE_HAS_NOT_STATED"),
            "intent_id": r["intent_id"], "fill_id": r["fill_id"],
            "expected_fee_usd": r["expected"],
            "observed_fee_usd": r["observed"],
            "what_it_means": ("the booked fee is the schedule's ESTIMATE, so "
                             "the cash and the P&L on this fill are not final"
                             if r["fee_state"] == FEE_PROVISIONAL else
                             "the venue charged something the deployed "
                             "schedule did not predict, and the difference "
                             "is carried rather than absorbed")})
    for r in prov:
        discrepancies.append({
            "kind": "A_PROVISIONAL_ECONOMIC_EVENT",
            "intent_id": r["intent_id"], "event_id": r["event_id"],
            "amount_usd": r["amount"], "basis": r["basis"],
            "what_it_means": ("this amount is in the realised total and is "
                             "explicitly incomplete")})
    # ── AND THE RECORDED ONES, which are the ones nothing smoothed over ──
    #
    # Everything above is DERIVED from the current state of the book. These are
    # events that happened: an oversell whose residual was clamped, a fill whose
    # economic events an interruption lost, a venue order matching our terms
    # that we cannot claim. A derived view cannot show them, because the whole
    # problem with each is that the book now looks consistent.
    recorded = await conn.fetch(
        "SELECT discrepancy_id, intent_id, kind, "
        "       EXTRACT(EPOCH FROM at)::float8 AS at, detail "
        "  FROM bettor_funded_discrepancies WHERE resolved_at IS NULL "
        " ORDER BY at DESC")
    for r in recorded:
        det = r["detail"]
        if isinstance(det, str):
            det = json.loads(det)
        discrepancies.append({
            "kind": r["kind"], "intent_id": r["intent_id"],
            "discrepancy_id": r["discrepancy_id"], "at": r["at"],
            "recorded": True, "detail": det,
            "what_it_means": (det or {}).get(
                "what_it_means",
                "a recorded discrepancy an operator must close")})
    for r in stranded:
        # ── THE PARTIAL RESULT ALREADY INSIDE THIS OPEN POSITION ──────
        #
        # An open position can already contain a realised gain or loss on
        # quantity it has sold. Showing only "residual 6" told an operator
        # nothing about the -$1.71 sitting inside it, and `realised()`
        # reports 0.00 here because it books on closure. So each held
        # position carries its own partial breakdown, component by
        # component, beside the residual.
        _sold = await realised_on_sold(conn, r["intent_id"])
        discrepancies.append({
            "kind": "RESIDUAL_INVENTORY_STILL_HELD",
            "intent_id": r["intent_id"], "residual_qty": r["residual"],
            "us_market_slug": r["us_market_slug"],
            "settlement": r["settlement"],
            "partial_exit_result": (None if not _sold.get("ok") else {
                k: _sold[k] for k in (
                    "sold_qty", "exit_proceeds_usd", "basis_per_contract",
                    "allocated_basis_usd", "fees_on_sold_usd",
                    "realised_on_sold_usd", "remaining_basis_usd",
                    "attribution", "realised_on_sold_is_provisional")}),
            "what_it_means": ("contracts this lane owns. They count against "
                             "every rail until an evidenced exit or an "
                             "authoritative settlement removes them"),
            "and_what_is_already_realised": (
                "`partial_exit_result` is the result on the quantity ALREADY "
                "SOLD out of this position, on the lane's own average-entry-"
                "cost basis. It is NOT `realised_pnl_usd` (which books on "
                "closure) and NOT open-position net cash (which mixes the "
                "sold result with the basis still held)")})
    return {
        "section": "Funded book",
        "label": "REAL MONEY — NEVER SUMMED WITH ANY MODELLED BOOK",
        "funded_capability": FS.CAPABILITY_AVAILABLE,
        "schema_readiness": {k: schema.get(k) for k in
                             ("ok", "capability", "missing_migrations",
                              "missing_tables", "missing_columns",
                              "missing_functions")},
        "books": books,
        "book_count": len(books),
        # ── PER-CLASS, because this section can now hold both ──────
        #
        # It reported one value for the whole section, which was right
        # while every book in it was real money. A controlled
        # demonstration written into the funded SCHEMA is not a funded
        # RESULT, and a single section-wide None would let its P&L be
        # read as an unclassified funded number.
        "funded_books": real_books,
        "funded_book_count": len(real_books),
        "demonstration_books": demo_books,
        "demonstration_book_count": len(demo_books),
        # ── THE DECISION, BESIDE THE BOOK ─────────────────────────
        "last_scheduled_decision": decision,
        "decision_note": (
            "the selected action, its execution eligibility, the order and "
            "fill outcome and the NAMED reason each other candidate could "
            "not proceed, as the last scheduled servicing pass recorded "
            "them. A book without its decision cannot answer 'why is this "
            "still held?'"),
        "counts_toward_strategy_performance": None,
        "counts_note": ("PER BOOK, in each book's own "
                       "`counts_toward_strategy_performance`. A funded book "
                       "reads None -- not established here, because this "
                       "reader does not know whether a closed funded "
                       "position exists. A demonstration book reads False. "
                       "The section-wide value stays None because the "
                       "section now holds both kinds and one answer for "
                       "both would be wrong for one of them"),
        "demonstration_note": (
            "%d of %d books in this section are controlled demonstrations, "
            "classified by `%s` in the account id. %s"
            % (len(demo_books), len(books), DEMONSTRATION_ACCOUNT_MARK,
               WHY_A_DEMONSTRATION_BOOK_IS_SEPARATE)),
        "demonstration_realised_usd": round(sum(
            float((b["pnl"] or {}).get("realised_pnl_usd") or 0.0)
            for b in demo_books), 6),
        "demonstration_is_never_added_to_funded": (
            "the two lists are returned separately and no total spans them. "
            "`demonstration_realised_usd` is reported so it can be SEEN, "
            "and it is not performance"),
        "classification_is_a_convention_not_a_permission": (
            "nothing stops a caller writing a demonstration row under a "
            "production account id. What this does is make an unlabelled "
            "demonstration visible rather than silently countable"),
        "unresolved_discrepancies": discrepancies,
        "unresolved_discrepancy_count": len(discrepancies),
        "discrepancies_note": ("each row is something an operator must act "
                              "on. A panel that shows only totals hides "
                              "every one of them"),
        "realised_pnl_is_measured_not_assumed": (
            "summed over bettor_funded_economics. Unrealised P&L is reported "
            "as UNMEASURED, not as zero, because marking funded inventory "
            "needs a funded mark this lane has no source for"),
        "order_terminality_is_not_inventory_closure": True,
        "audit": {"reader": "%s.command_center" % __name__,
                  "tables": ["bettor_funded_intents", "bettor_funded_fills",
                             "bettor_funded_economics"]},
    }


def describe() -> dict:
    return {
        "version": "BETTOR_FUNDED_BOOK_V2",
        "tables": ["bettor_funded_intents", "bettor_funded_fills",
                   "bettor_funded_economics"],
        "outstanding_order_states": list(OUTSTANDING_ORDER_STATES),
        "live_states": list(LIVE_STATES),
        "terminal_order_states": list(TERMINAL_ORDER_STATES),
        "terminal_states": list(TERMINAL_STATES),
        "order_terminality_is_not_inventory_closure": {
            "outstanding": "bettor_funded_order_is_outstanding(state)",
            "still_held": "bettor_funded_holds_inventory(residual, closed)",
            "position_open": "either of the two",
            "why": ("FILLED is terminal for the ORDER and is the point of "
                    "MAXIMUM exposure for the POSITION. Reading one predicate "
                    "as both released the one-position slot and erased the "
                    "holding from every rail at that exact moment")},
        "closure_reasons": list(CLOSURE_REASONS),
        "fee_states": [FEE_PROVISIONAL, FEE_RECONCILED, FEE_DISAGREES],
        "expected_and_observed_fees_are_separate_columns": True,
        "intent_is_committed_before_sending": True,
        "one_open_position_enforced_by": "bettor_funded_one_open_position "
                                        "(unique index, migration 126)",
        "one_live_intent_enforced_by": "bettor_funded_one_live_intent "
                                       "(unique index, migration 125) -- "
                                       "replaced by the above",
        "fill_identity": "fvf:<venue_order_id>:<venue_fill_id>, PRIMARY KEY",
        "reuses": {
            "cash": "live_executor.fill_cash (side-aware)",
            "fees": "calibration_fees.expected_fee + .reconcile (deployed)",
            "settlement": "bettor_venue_settlement_probe.probe"},
        "recovery_never_resubmits": True,
        "recovery_requires": {
            "terms": list(CORRELATION_TERMS),
            "uniqueness": "exactly one candidate, or UNRESOLVED",
            "not_already_claimed": "an order another intent owns is never a "
                                   "candidate",
            "clock_tolerance_s": CORRELATION_CLOCK_TOLERANCE_S,
            "executions_read_from": "executions (both adapter shapes), never "
                                    "a `fills` key order_status never sends",
            # AND THE ONE THAT MATTERS MOST: a term match is not ownership.
            "a_durable_identity": CLIENT_ORDER_IDENTITY_SUPPORTED,
            "adoption_is_possible_on_this_venue": (
                CLIENT_ORDER_IDENTITY_SUPPORTED),
            "why_not": ("polymarket_us CreateOrderParams accepts no client "
                        "order identifier, so an order the venue holds cannot "
                        "be proved to be the one we sent. A lost "
                        "acknowledgement stays UNRESOLVED and the term match "
                        "is recorded for manual reconciliation")},
        "unresolved_preserves_exposure": True,
        "shadow_cannot_stand_in": ("rn1x_orders carries CHECK (is_modelled); "
                                   "every read here is scoped to "
                                   "bettor_funded_*"),
    }
