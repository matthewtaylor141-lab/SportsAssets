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

#: Intent states in which this lane may already have exposure at the venue.
#: Mirrors `bettor_funded_intent_is_live` in migration 125 -- both read one
#: definition, and a test asserts they agree.
LIVE_STATES = ("INTENT_RECORDED", "SEND_ATTEMPTED", "ACKNOWLEDGED",
               "PARTIALLY_FILLED", "UNRESOLVED")
TERMINAL_STATES = ("FILLED", "CANCELLED", "REJECTED", "ABANDONED")

PROVENANCE = "FUNDED_PILOT_EXECUTION"

R_ANOTHER_INTENT_IS_LIVE = "ANOTHER_FUNDED_INTENT_IS_ALREADY_LIVE"
R_NO_EXECUTION_IDENTITY = "THE_VENUE_SUPPLIED_NO_DURABLE_EXECUTION_IDENTITY"
R_NO_SUCH_INTENT = "NO_SUCH_FUNDED_INTENT"


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
                        decision_ref: dict | None = None) -> dict:
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
            " decision_ref, state, provenance) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,"
            "        'INTENT_RECORDED',$13)",
            intent_id, str(account_id), str(venue), str(venue_class),
            str(us_market_slug), str(event_key), str(order_intent),
            float(limit_price), int(quantity), float(collateral_usd),
            str(effective_digest), json.dumps(decision_ref or {}),
            PROVENANCE)
    except Exception as exc:                                # noqa: BLE001
        msg = str(exc)
        if "bettor_funded_one_live_intent" in msg:
            live = await live_intents(conn)
            return dict(out, ok=False, refusal=R_ANOTHER_INTENT_IS_LIVE,
                        live=[r["intent_id"] for r in live],
                        why=("this lane holds one open funded intent at a "
                             "time and the database enforces it, so a second "
                             "concurrent submission is refused here rather "
                             "than at the venue"))
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

async def ingest_fills(conn, intent_id: str, fills, *,
                       at: float | None = None) -> dict:
    """RECORD EXECUTIONS IDEMPOTENTLY, and refuse to invent an identity.

    Returns what was written, what was already held, and what could not be
    identified. An unidentified execution is NOT written and NOT called
    already-held: the intent goes UNRESOLVED, because quantity the venue
    reported and we cannot place is a discrepancy, not a rounding error.
    """
    now = float(at if at is not None else time.time())
    row = await conn.fetchrow(
        "SELECT intent_id, venue_order_id, order_intent, quantity "
        "  FROM bettor_funded_intents WHERE intent_id=$1", intent_id)
    if row is None:
        return {"ok": False, "refusal": R_NO_SUCH_INTENT}
    written, already, unresolved = [], [], []
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
        cash = cash_for(qty, px, row["order_intent"])
        fee, basis = fee_for(qty, px, at=now)
        res = await conn.execute(
            "INSERT INTO bettor_funded_fills (fill_id, intent_id,"
            " venue_order_id, venue_fill_id, at, qty, price, cash_usd,"
            " fee_usd, fee_basis, raw) "
            "VALUES ($1,$2,$3,$4,to_timestamp($5),$6,$7,$8,$9,$10,$11::jsonb)"
            " ON CONFLICT (fill_id) DO NOTHING",
            fid, intent_id, str(row["venue_order_id"] or ""), str(vfid),
            now, qty, px, cash, fee, basis, json.dumps(f, default=str))
        (written if res.endswith("1") else already).append(
            {"fill_id": fid, "qty": qty, "price": px, "cash_usd": cash,
             "fee_usd": fee})
    # THE FILLED QUANTITY COMES FROM THE LEDGER, never from a running total
    # the venue happened to send, so the two can never disagree.
    tot = await conn.fetchrow(
        "SELECT coalesce(sum(qty),0)::float8 AS q, "
        "       coalesce(sum(cash_usd),0)::float8 AS cash, "
        "       coalesce(sum(fee_usd),0)::float8 AS fee "
        "  FROM bettor_funded_fills WHERE intent_id=$1", intent_id)
    filled = float(tot["q"])
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
    return {"ok": True, "written": written, "already_held": already,
            "unresolved": unresolved, "unresolved_count": len(unresolved),
            "filled_qty_from_the_ledger": round(filled, 6),
            "cash_usd_from_the_ledger": round(float(tot["cash"]), 6),
            "fees_usd_from_the_ledger": round(float(tot["fee"]), 6),
            "idempotent_on": "the venue's own fill id"}


# ── 3 · RECOVERY: ASK THE VENUE, NEVER RESEND ───────────────────────

async def live_intents(conn, *, account_id=None, venue=None) -> list[dict]:
    sql = ("SELECT * FROM bettor_funded_intents "
           " WHERE bettor_funded_intent_is_live(state)")
    args: list = []
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND account_id=$%d" % len(args)
    if venue is not None:
        args.append(str(venue))
        sql += " AND upper(venue)=upper($%d)" % len(args)
    return [dict(r) for r in await conn.fetch(sql + " ORDER BY created_at",
                                             *args)]


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
        theirs = {str(o.get("order_id") or o.get("id") or ""): o
                  for o in (adapter.open_orders() or [])}
        readable = True
    except Exception as exc:                                # noqa: BLE001
        theirs, readable = {}, False
        out["open_orders_error"] = "%s: %s" % (type(exc).__name__,
                                               str(exc)[:200])

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
            # cannot ask about a specific order. The only honest answers are
            # "the venue holds something on this market that we have not
            # booked" or "we do not know".
            candidates = [o for o in theirs.values()
                          if str(o.get("us_market_slug")
                                 or o.get("marketSlug") or "").lower()
                          == str(r["us_market_slug"]).lower()]
            if candidates:
                adopt = candidates[0]
                aid = str(adopt.get("order_id") or adopt.get("id") or "")
                await record_acknowledgement(
                    conn, iid, venue_order_id=aid, status="open",
                    raw={"adopted_by_recovery": True, "venue_order": adopt})
                out["reconciled"].append(dict(
                    rec, case="ADOPTED_AN_ORDER_THE_VENUE_HOLDS",
                    venue_order_id=aid,
                    why=("this intent was sent and never acknowledged; the "
                         "venue holds an order on its market, so it is "
                         "adopted rather than duplicated")))
                continue
            got = await mark_unresolved(
                conn, iid,
                "sent with no acknowledgement and the venue lists no order "
                "on this market; it may never have arrived, or it may have "
                "filled and closed")
            out["unresolved"].append(dict(rec, **got))
            continue
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
        ing = await ingest_fills(conn, iid, st.get("fills") or [], at=at)
        after = await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1", iid)
        out["reconciled"].append(dict(
            rec, case="READ_FROM_THE_VENUE", venue_state=st.get("status"),
            state_now=after,
            fills_written=len(ing.get("written") or []),
            fills_already_held=len(ing.get("already_held") or []),
            unresolved_executions=ing.get("unresolved_count"),
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
    """WHAT THIS LANE HAS COMMITTED, counting things that have not filled.

    THE DEFECT THIS CLOSES. A headroom check that counts only FILLED positions
    is blind to exactly the window in which a second order does damage: the
    request is in flight, nothing has filled, and the rail says there is room.
    Pending intents and in-flight reservations are collateral the venue may
    take at any moment, so they count at full size.
    """
    live = await conn.fetch(
        "SELECT intent_id, event_key, us_market_slug, collateral_usd::float8 "
        "       AS collateral, state "
        "  FROM bettor_funded_intents "
        " WHERE bettor_funded_intent_is_live(state) AND account_id=$1 "
        "   AND upper(venue)=upper($2)", str(account_id), str(venue))
    filled = await conn.fetchrow(
        "SELECT coalesce(sum(f.cash_usd),0)::float8 AS cash, "
        "       coalesce(sum(f.fee_usd),0)::float8 AS fee, "
        "       coalesce(sum(f.qty),0)::float8 AS qty "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.account_id=$1 AND upper(i.venue)=upper($2)",
        str(account_id), str(venue))
    per_event: dict = {}
    per_market: dict = {}
    pending = 0.0
    for r in live:
        c = float(r["collateral"])
        pending += c
        per_event[r["event_key"]] = per_event.get(r["event_key"], 0.0) + c
        per_market[r["us_market_slug"]] = per_market.get(
            r["us_market_slug"], 0.0) + c
    # CAPITAL-HOURS, integrated over the fills this lane actually holds.
    ch = await conn.fetchval(
        "SELECT coalesce(sum(f.cash_usd * GREATEST(0, EXTRACT(EPOCH FROM "
        "       (now() - f.at)) / 3600.0)),0)::float8 "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.account_id=$1 AND upper(i.venue)=upper($2)",
        str(account_id), str(venue))
    return {
        "account_id": account_id, "venue": venue,
        "live_intents": [dict(r) for r in live],
        "pending_and_in_flight_collateral_usd": round(pending, 6),
        "filled_cash_usd": round(float(filled["cash"]), 6),
        "filled_fees_usd": round(float(filled["fee"]), 6),
        "contracts_held": round(float(filled["qty"]), 6),
        "capital_hours_usd_h": round(float(ch or 0.0), 6),
        "per_event_collateral_usd": {k: round(v, 6)
                                     for k, v in per_event.items()},
        "per_market_collateral_usd": {k: round(v, 6)
                                      for k, v in per_market.items()},
        "counts_pending_at_full_size": True,
        "scope": ("bettor_funded_* only. No modelled row and no shadow lane "
                  "contributes to any number here"),
    }


async def pnl(conn, *, account_id: str, venue: str) -> dict:
    """THE FUNDED LANE'S P&L, from funded fills alone.

    Realised P&L needs settlement, and this lane has no settled position yet,
    so realised is 0 WITH THAT BASIS STATED -- it is a measurement of an empty
    set, not an unmeasured quantity. Cost basis and fees are real numbers now.
    """
    row = await conn.fetchrow(
        "SELECT count(*) AS fills, "
        "       coalesce(sum(f.qty),0)::float8 AS qty, "
        "       coalesce(sum(f.cash_usd),0)::float8 AS cash, "
        "       coalesce(sum(f.fee_usd),0)::float8 AS fee "
        "  FROM bettor_funded_fills f JOIN bettor_funded_intents i "
        "    ON i.intent_id=f.intent_id "
        " WHERE i.account_id=$1 AND upper(i.venue)=upper($2)",
        str(account_id), str(venue))
    settled = await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_intents "
        " WHERE account_id=$1 AND upper(venue)=upper($2) "
        "   AND resolved_at IS NOT NULL AND state='FILLED'",
        str(account_id), str(venue))
    unres = await conn.fetch(
        "SELECT intent_id, unresolved_reason FROM bettor_funded_intents "
        " WHERE state='UNRESOLVED' AND account_id=$1 "
        "   AND upper(venue)=upper($2)", str(account_id), str(venue))
    cash, fee = float(row["cash"]), float(row["fee"])
    return {
        "account_id": account_id, "venue": venue,
        "fills": int(row["fills"]),
        "contracts": round(float(row["qty"]), 6),
        "cost_basis_usd": round(cash, 6),
        "fees_usd": round(fee, 6),
        "cash_out_the_door_usd": round(cash + fee, 6),
        "realised_pnl_usd": 0.0,
        "realised_basis": ("no funded position has settled, so realised P&L "
                           "is the sum over an empty set -- measured, not "
                           "assumed"),
        "filled_intents": int(settled or 0),
        "unresolved_intents": [dict(r) for r in unres],
        "is_not_summed_with": ("the shadow, acceptance, demonstration or "
                              "UNCLASSIFIED books. This is the funded lane "
                              "alone"),
    }


def describe() -> dict:
    return {
        "version": "BETTOR_FUNDED_BOOK_V1",
        "tables": ["bettor_funded_intents", "bettor_funded_fills"],
        "live_states": list(LIVE_STATES),
        "terminal_states": list(TERMINAL_STATES),
        "intent_is_committed_before_sending": True,
        "one_live_intent_enforced_by": "bettor_funded_one_live_intent "
                                       "(unique index, migration 125)",
        "fill_identity": "fvf:<venue_order_id>:<venue_fill_id>, PRIMARY KEY",
        "reuses": {
            "cash": "live_executor.fill_cash (side-aware)",
            "fees": "calibration_fees.expected_fee (deployed schedule)"},
        "recovery_never_resubmits": True,
        "unresolved_preserves_exposure": True,
        "shadow_cannot_stand_in": ("rn1x_orders carries CHECK (is_modelled); "
                                   "every read here is scoped to "
                                   "bettor_funded_*"),
    }
