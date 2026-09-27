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
                break
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


async def prior_taker_legs(conn, intent_id: str, direction: str) -> list:
    """THIS ORDER'S ALREADY-INGESTED FILLS, in fill order, as (qty, price).

    WHY THE EXPECTATION NEEDS THEM. The venue charges an order, not a fill:
    each fill pays its banker's-rounded fee ADJUSTED so the order's total never
    exceeds the banker's rounding of the cumulative exact fee. A fill's
    expected fee therefore depends on what the order has already been charged,
    which means it cannot be computed from the fill alone -- and `reconcile_fee`
    was computing it from the fill alone.

    READ FROM THE TABLE, NOT FROM MEMORY, AND THAT IS THE POINT. Three
    properties fall out of deriving the sequence from persisted rows rather
    than from a batch variable:

      RESTART        a worker that dies mid-order and comes back reads the
                     same prior fills, so the fourth fill is priced as the
                     fourth fill and not as the first.
      REDELIVERY     a duplicate delivery of fill 2 finds fill 2 already
                     present (it is excluded by id below), so it is priced
                     against the same prior sequence and yields the same
                     number rather than shifting the cap.
      PARTIAL FILL   an order that fills in pieces minutes apart is one order
                     to the venue and is now one order here too.

    Ordered by `at` then `fill_id` so the sequence is deterministic when two
    fills share a timestamp -- the venue's own order is not recoverable in that
    case, and an arbitrary but STABLE order is what keeps a re-run from
    producing different per-fill numbers for the same facts.
    """
    rows = await conn.fetch(
        "SELECT fill_id, qty::float8 AS q, price::float8 AS p "
        "  FROM bettor_funded_fills "
        " WHERE intent_id=$1 AND direction=$2 "
        " ORDER BY at, fill_id", intent_id, direction)
    return [(float(r["q"]), float(r["p"]), r["fill_id"]) for r in rows]


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


def reconcile_fee(qty: float, price: float, observed, *, at=None,
                  prior_legs=None) -> dict:
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
        legs = [(q, p) for q, p, *_ in prior_legs] + [(qty, price)]
        got = order_expected_fees(legs)
        if got["BLOCKER"] is None:
            cumulative = {
                "expected_fee_usd_single_fill": expected,
                "order_legs": len(legs),
                "this_leg_index": len(legs) - 1,
                "order_total_expected_usd": got["TOTAL"],
                "cumulative_cap_usd": got["cumulative_cap"],
                "cap_adjusted_this_leg": got["adjusted"][-1],
                "algorithm": got["algorithm"],
            }
            expected = got["per_fill"][-1]
            basis = ("calibration_fees.order_fees(leg %d of %d, %s)"
                     % (len(legs), len(legs), got["algorithm"]))
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
        priors = [t for t in await prior_taker_legs(conn, intent_id, direction)
                  if t[2] != fid]
        fee = reconcile_fee(qty, px, _observed_fee_of(f), at=now,
                            prior_legs=priors)
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
                " observed_fee_usd, fee_state, fee_reconciliation) "
                "VALUES ($1,$2,$3,$4,to_timestamp($5),$6,$7,$8,$9,$10,"
                "        $11::jsonb,$12,$13,$14,$15,$16::jsonb)"
                " ON CONFLICT (fill_id) DO NOTHING",
                fid, intent_id, str(row["venue_order_id"] or ""), str(vfid),
                now, qty, px, cash, fee["booked_fee_usd"], fee["fee_basis"],
                json.dumps(f, default=str), direction,
                fee["expected_fee_usd"], fee["observed_fee_usd"],
                fee["fee_state"],
                json.dumps(fee["reconciliation"], default=str))
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

    `closed` is one entry per CLOSED position -- `{"intent_id", "at", "net"}`
    -- ordered here by closure time. Each contributes its net once, when it
    closed, which is what a realised curve is: a position's entry cost is not a
    loss while the contracts are still owned, and a round trip that came back
    flat is not a drawdown just because cash left in between.

    THE DEFECT THIS CLOSES. `check_rails` hardcoded MAX_DRAWDOWN to 0.0 on the
    grounds that nothing had settled. That describes today. It cannot implement
    a loss stop tomorrow, because the moment something DOES settle the rail
    still reads zero and the stop never trips.
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
                      "net_usd": round(float(c.get("net") or 0.0), 6),
                      "cumulative_realised_usd": round(cum, 6),
                      "peak_usd": round(peak, 6),
                      "drawdown_usd": round(dd, 6)})
    return {"realised_pnl_usd": round(cum, 6),
            "peak_realised_usd": round(peak, 6),
            "max_drawdown_usd": round(worst, 6),
            "worst_at_intent": at_worst,
            "closed_positions": len(seq),
            "curve": curve,
            "basis": ("the realised equity curve: each CLOSED position's net "
                      "economic events, in closure order. An open position "
                      "contributes nothing -- its cost is an asset, not a "
                      "loss")}


async def realised(conn, *, account_id: str, venue: str) -> dict:
    """REALISED P&L AND DRAWDOWN, summed over recorded economic events."""
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
               "provisional_events": int(r["provisional"])} for r in rows]
    dd = drawdown_from(closed)
    prov = sum(c["provisional_events"] for c in closed)
    return dict(dd, closed=closed, provisional_events_in_realised=prov,
                realised_is_provisional=bool(prov),
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
        "open_position_net_cash_usd": round(float(openside["net"]), 6),
        "open_position_provisional_events": int(openside["provisional"]),
        # NAMED, NOT ZEROED.
        "unrealised_pnl_usd": None,
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
    pairs = await conn.fetch(
        "SELECT account_id, venue, count(*) AS intents "
        "  FROM bettor_funded_intents GROUP BY account_id, venue "
        " ORDER BY account_id, venue")
    books = []
    for p in pairs:
        books.append({
            "account_id": p["account_id"], "venue": p["venue"],
            "intents": int(p["intents"]),
            "exposure": await exposure(conn, account_id=p["account_id"],
                                      venue=p["venue"]),
            "pnl": await pnl(conn, account_id=p["account_id"],
                            venue=p["venue"])})
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
        discrepancies.append({
            "kind": "RESIDUAL_INVENTORY_STILL_HELD",
            "intent_id": r["intent_id"], "residual_qty": r["residual"],
            "us_market_slug": r["us_market_slug"],
            "settlement": r["settlement"],
            "what_it_means": ("contracts this lane owns. They count against "
                             "every rail until an evidenced exit or an "
                             "authoritative settlement removes them")})
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
        "counts_toward_strategy_performance": None,
        "counts_note": ("this is the only book that could, and it is empty "
                       "of realised results until a funded position closes. "
                       "It is reported apart from the shadow, acceptance, "
                       "demonstration and UNCLASSIFIED books and the totals "
                       "are not added"),
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
