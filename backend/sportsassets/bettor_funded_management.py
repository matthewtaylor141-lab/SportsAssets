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
R_NOT_AUTHORIZED = "THE_AUTHORIZATION_GATE_REFUSED_THIS_SUBMISSION"
R_GATE_NOT_AFFIRMATIVE = "THE_EXECUTION_GATE_DID_NOT_AFFIRMATIVELY_ALLOW_IT"
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

async def submit_exit(conn, *, intent_id: str, limit_price=None,
                      quantity=None, adapter=None, venue: str | None = None,
                      now: float | None = None) -> dict:
    """SELL BACK SOME OR ALL OF A HELD FUNDED POSITION.

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
    want = residual if quantity is None else float(quantity)
    if want <= 0 or want > residual + 1e-9:
        return dict(out, ok=False, refusal=R_OVER_RESIDUAL,
                    asked=want, residual_qty=residual,
                    why=("an exit larger than the inventory held would open a "
                         "position on the other side, which this lane does "
                         "not do"))
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
    out["plan"] = {
        "us_market_slug": row["us_market_slug"], "quantity": contracts,
        "limit_price": wire, "sell": True,
        "opened_with": opened_with,
        "adapter_will_send": ("pmus._exit_intent derives SELL_SHORT from "
                              "BUY_SHORT and SELL_LONG from BUY_LONG; the "
                              "BUY intent is passed so the adapter never "
                              "sells a side we do not hold"),
        "expected_proceeds_usd": proceeds,
        "rounded": {"asked": float(limit_price), "sent": wire,
                    "direction": ("FLOOR" if opened_with == SHORT
                                  else "CEIL"),
                    "why": ("the direction that cannot receive less per "
                            "contract than the plan assumed")}}
    sel = await FA.account_selection(conn, str(row["account_id"]))
    out["account_selection"] = sel
    if not sel.get("ok"):
        return dict(out, ok=False, refusal=sel["refusal"],
                    why=sel.get("why"))
    approved = await _approved(conn)
    auth = EX.authorize_submission(
        account_id=str(row["account_id"]), venue=ven,
        authorization=FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY)),
        approved_limits=approved, now=at)
    out["authorization"] = auth
    if not auth.get("authorization_consumed"):
        return dict(out, ok=False, refusal=R_NOT_AUTHORIZED,
                    gate_refusal=auth.get("refusal"),
                    why=("an exit is still a real order at a real venue, so "
                         "it passes the same authorization boundary an entry "
                         "does"))
    if not auth.get("ok"):
        return dict(out, ok=False, refusal=R_GATE_NOT_AFFIRMATIVE,
                    gate_refusal=auth.get("refusal"),
                    why=("the record was consumed and the execution gate "
                         "still did not allow it: %s" % auth.get("refusal")))
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

    # ── THE EXIT INTENT IS COMMITTED BEFORE THE REQUEST LEAVES ──────
    xid = FB.new_intent_id()
    await conn.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
        " venue_class, us_market_slug, event_key, order_intent, limit_price,"
        " quantity, collateral_usd, effective_digest, decision_ref, state,"
        " provenance, kind, parent_intent_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,"
        "        'INTENT_RECORDED',$13,'EXIT',$14)",
        xid, str(row["account_id"]), ven, str(row["venue_class"]),
        str(row["us_market_slug"]), str(row["event_key"]), opened_with,
        wire, contracts,
        # AN EXIT COMMITS NO COLLATERAL. It releases it.
        0.0, str(row["effective_digest"] or ""),
        '{"servicing": true, "reduces_exposure": true}',
        FB.PROVENANCE, intent_id)
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
        # A VOID RETURNS THE COLLATERAL, so the position nets to its fees.
        paid = await conn.fetchval(
            "SELECT coalesce(sum(CASE WHEN direction='ENTRY' THEN cash_usd "
            "                       ELSE -cash_usd END),0)::float8 "
            "  FROM bettor_funded_fills WHERE intent_id=$1", intent_id)
        amount = float(paid or 0.0)
        basis = ("the venue declared a void, so the collateral this position "
                 "paid is returned. Fees already booked are NOT returned by "
                 "this and stay in the realised total")
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
                 client=None, probe=None, now: float | None = None) -> dict:
    """ONE SERVICING CYCLE over every open funded position.

    WHAT IT DOES, all of it reads unless a servicing switch is on:

      1. RECONCILE THE ORDER against the venue -- `bettor_funded_book.recover`,
         which requires an established correlation before adopting anything
         and reads the adapter's `executions`, so a fill that happened while
         this lane was not running lands in the book.
      2. ASK ABOUT SETTLEMENT for anything still held, and close only on the
         venue's own authoritative answer.
      3. RE-MEASURE the book: exposure, realised P&L and the drawdown the loss
         stop is enforced on.
      4. REPORT what needs a decision -- a held position with no supported
         exit price, an unresolved intent, a fee the venue charged differently
         -- rather than acting on a guess.

    It never opens a position. It is safe to run on a schedule with every
    submission switch off, which is the configuration this deployment is in.
    """
    at = float(now if now is not None else time.time())
    out = {"version": VERSION, "at": at, "account_id": account_id,
           "venue": venue, "opened_anything": False,
           "recovered": None, "settlement": [], "needs_a_decision": [],
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
    held = await open_positions(conn, account_id=account_id, venue=venue)
    out["open_positions"] = held
    for p in held:
        if not p["holding"]:
            continue
        got = await reconcile_settlement(conn, intent_id=p["intent_id"],
                                        client=client, probe=probe, now=at)
        out["settlement"].append(got)
        if not got.get("closed"):
            out["needs_a_decision"].append({
                "intent_id": p["intent_id"],
                "us_market_slug": p["us_market_slug"],
                "residual_qty": p["residual"],
                "what": "AN_EXIT_LIMIT_OR_AN_AUTHORITATIVE_SETTLEMENT",
                "why": ("this position holds inventory, the venue states no "
                        "authoritative outcome (%s), and this lane invents no "
                        "exit price" % got.get("terminal_reading"))})
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
