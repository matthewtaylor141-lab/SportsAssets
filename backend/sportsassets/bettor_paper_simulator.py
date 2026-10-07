"""PAPER_SIM_V1 -- THE FILL SIMULATOR. SIMULATED, NOT VERIFIED EXECUTION.

Every acknowledgement and fill it writes carries event_source SIMULATOR and
the session's frozen simulator version. It reads only books the paper path
observed through the read-only market-data client
(`paper_book_observations`); it never touches the venue.

── MARKETABLE ORDERS (IOC / FOK) ──────────────────────────────────────────
  * DELAY. The order is evaluated on the FIRST READABLE book observed at or
    after decided_at + decision_to_execution_delay_s (`eligible_at`) and at
    or before `expires_at`. An observation row that carries an error (our
    request gate refused the read, our own deadline cut it, the venue
    answered an error) holds NO book: it is skipped, named on the order's
    event, and never fills or expires the order. No readable book before
    `expires_at` -> EXPIRED at `expires_at` with NO fill, reservation
    released, and the reason says which (nothing observed, or only
    unreadable reads -- with their counts by who refused them).
  * SIDE SEMANTICS. The ladder is the side the intent consumes
    (`bettor_book_snapshot.acquisition_ladder` / `exit_ladder`): BUY_LONG
    lifts offers, BUY_SHORT hits bids at cost 1 - bid, SELL of a long hits
    bids, SELL of a short lifts offers at proceeds 1 - offer.
  * DEPTH WALK WITHIN THE LIMIT, best level first, never beyond the limit.
    Levels whose wire price is not a whole cent are excluded (the adapter
    formats every price %.2f; ADAPTER_CENT_GRID).
  * CONSUMED LIQUIDITY. Each (market, side, wire price, book observation)
    level is consumed at most once across all paper orders
    (`paper_liquidity_consumed`), read and written under the account lock.
  * PARTIALS only when the order allows them (IOC); FOK fills all or none.
    An IOC remainder is released at once.
  * FEES per fill from the deployed schedule.

── RESTING ORDERS (GTD) ───────────────────────────────────────────────────
  * NEVER FILL ON A TOUCH. A resting BUY at limit L fills only on an observed
    book, AFTER placement, whose consumed side shows executable liquidity
    STRICTLY better than L (an offer below L); equal is a touch and is not a
    fill.
  * QUEUE. At placement the order joins the back of the queue: queue ahead =
    displayed size on OUR side at-or-better than L at the placement book,
    plus earlier open paper orders there (JOINS_THE_BACK_OF_THE_QUEUE,
    DISPLAYED_IS_ALL_THERE_IS). Crossing liquidity goes to the queue ahead
    first; only the excess can fill us, at our limit.
  * UNCERTAINTY IS KEPT. No book observed since placement, an unreadable
    book, or a book that does not cross: the order stays unfilled and the
    reason is recorded (NO_FILL_EVIDENCE). A missing update is never proof
    of a fill.

── THE OPTIMISTIC SENSITIVITY (reported separately, never primary) ─────────
  `optimistic_fill` prices the same order on the decision-time book with no
  delay, no consumption ledger and touch fills allowed. Its figures are a
  sensitivity bound for the report, never written as fills or cash.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

from . import bettor_book_snapshot as BS
from . import bettor_paper_ledger as L

VERSION = "PAPER_SIM_V1"
EVENT_SOURCE = "SIMULATOR"

BASIS_WALK = "DEPTH_WALK_WITHIN_LIMIT"
BASIS_CROSS = "CROSSING_LIQUIDITY_AFTER_QUEUE"

ASSUMPTIONS = {
    "marketable": ["NEXT_READABLE_BOOK_AT_OR_AFTER_DECISION_PLUS_DELAY",
                   "AN_UNREADABLE_OBSERVATION_IS_SKIPPED_NEVER_A_FILL",
                   "DISPLAYED_DEPTH_IS_EXECUTABLE_AT_THAT_BOOK",
                   "NO_HIDDEN_LIQUIDITY", "ADAPTER_CENT_GRID",
                   "CONSUMED_LEVELS_NEVER_REUSED"],
    "resting": ["JOINS_THE_BACK_OF_THE_QUEUE", "DISPLAYED_IS_ALL_THERE_IS",
                "TOUCH_IS_NOT_A_FILL", "STRICT_CROSS_AFTER_QUEUE_ONLY",
                "FILLED_AT_OUR_LIMIT_NO_PRICE_IMPROVEMENT",
                "MISSING_UPDATE_IS_NOT_A_FILL"],
}

R_NO_BOOK_YET = "NO_BOOK_OBSERVED_AT_OR_AFTER_DECISION_PLUS_DELAY_YET"
R_NO_BOOK_IN_WINDOW = "NO_BOOK_OBSERVED_BEFORE_THE_ORDER_EXPIRED"
R_NOTHING_WITHIN_LIMIT = "NO_DISPLAYED_LIQUIDITY_WITHIN_THE_LIMIT"
R_FOK_SHORT = "FOK_DEPTH_WITHIN_LIMIT_BELOW_THE_ORDER_QUANTITY"
R_IOC_REMAINDER = "IOC_REMAINDER_CANCELED"
R_NO_NEW_BOOK = "NO_BOOK_OBSERVED_SINCE_PLACEMENT"
R_TOUCH_ONLY = "TOUCH_IS_NOT_A_FILL"
R_NO_CROSS = "NO_LIQUIDITY_CROSSED_THE_LIMIT"
R_QUEUE_AHEAD = "CROSSING_LIQUIDITY_WENT_TO_THE_QUEUE_AHEAD"
R_GTD_EXPIRED = "GOOD_TILL_DATE_EXPIRED"
#: HISTORICAL (records written before R30A): a marketable order released on
#: the FIRST observation in its window because that observation was an
#: errored read. No longer written by `_marketable` -- see
#: R_NO_READABLE_BOOK_IN_WINDOW. A resting order still names it per book.
R_BOOK_UNREADABLE = "THE_OBSERVED_BOOK_WAS_UNREADABLE"
#: R30A: the marketable order's window closed with ONLY errored reads in it
#: (or none at all after an errored one): no book was ever observed, so there
#: is no fill evidence. The detail carries how many unreadable reads were seen.
R_NO_READABLE_BOOK_IN_WINDOW = "NO_READABLE_BOOK_OBSERVED_BEFORE_THE_ORDER_EXPIRED"

# ── AN UNREADABLE OBSERVATION IS EVIDENCE OF NOTHING, NOT A VERDICT ─────────
#
# THE DEFECT THIS CLOSES (P0 incident 2026-10-04, measured in production on
# 191b299). `_marketable` took the FIRST paper_book_observations row at or
# after `eligible_at` -- errored or not -- and an errored row expired the
# order AT ONCE with THE_OBSERVED_BOOK_WAS_UNREADABLE. Most of those rows
# record a read that never left our process: the venue request gate refused
# it because a 429 cooldown outlasted the pass deadline, or our own pass
# deadline cut it. 44 paper entry orders a day (61% of all of them) expired
# that way, while orders that met a readable in-window book filled 20 of 24.
# The order's own window (`expires_at`, 90 s) was never used.
#
# So an errored row is SKIPPED: the order is evaluated on the first READABLE
# observation inside [eligible_at, expires_at], with exactly the same walk,
# limit, depth and consumption rules; every skipped row is named on the
# order's terminal event; and an order that met no readable book expires
# only at `expires_at`, saying which reads failed and who refused them:
#
#   GATE_REFUSED   our venue request gate refused to dispatch (cooldown
#                  longer than the deadline, deadline already passed, hold
#                  longer than the undeadlined cap) -- venue_request_gate.R_*
#   DEADLINE_CUT   our own read deadline cut the wait
#                  (bettor_paper_guard.R_BOOK_READ_DEADLINE)
#   VENUE_ERROR    anything else: the venue answered an error, timed out,
#                  rate-limited, or returned no market data
#
# The codes are spelled here, not imported: this module stays importable
# without the venue modules, and a test pins them equal to their sources.
R_NO_READABLE_BOOK_YET = \
    "NO_READABLE_BOOK_OBSERVED_AT_OR_AFTER_DECISION_PLUS_DELAY_YET"
#: ONE TERMINAL CODE, TWO NAMES: the inc-sim and router streams repaired this
#: defect independently and named the same outcome differently; it is the
#: router's spelling (already classified SOFTWARE/DATA at stage FILL by
#: `refusal_taxonomy_table`) under both names, never two codes for one fact.
R_NO_READABLE_BOOK = R_NO_READABLE_BOOK_IN_WINDOW
U_GATE_REFUSED = "GATE_REFUSED"
U_DEADLINE_CUT = "DEADLINE_CUT"
U_VENUE_ERROR = "VENUE_ERROR"
UNREADABLE_CLASSES = (U_GATE_REFUSED, U_DEADLINE_CUT, U_VENUE_ERROR)
GATE_REFUSAL_CODES = frozenset({
    "VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE",
    "DECISION_DEADLINE_PASSED_BEFORE_DISPATCH",
    "VENUE_COOLDOWN_EXCEEDS_THE_UNDEADLINED_WAIT_CAP"})
DEADLINE_CUT_CODES = frozenset({"PAPER_BOOK_READ_DEADLINE_EXCEEDED"})
#: At most this many skipped rows are NAMED on an event (all are counted).
MAX_SKIPPED_NAMED = 20
#: At most this many unreadable rows are read per evaluation (one slug's
#: window is ~90 s of paced reads, so this bounds a pathological table only).
MAX_SKIPPED_SCANNED = 500


def unreadable_class(error) -> str:
    """WHO REFUSED AN UNREADABLE READ: our gate, our deadline, or the venue."""
    e = str(error or "")
    if e in GATE_REFUSAL_CODES:
        return U_GATE_REFUSED
    if e in DEADLINE_CUT_CODES:
        return U_DEADLINE_CUT
    return U_VENUE_ERROR


def unreadable_summary(rows) -> dict:
    """The skipped observations: counted by class and by error, and NAMED
    (obs id, receipt instant, error, class, source) up to MAX_SKIPPED_NAMED.
    Pure; `rows` are paper_book_observations rows (or dicts) with an error."""
    counts = {c: 0 for c in UNREADABLE_CLASSES}
    by_error: dict = {}
    named = []
    for r in rows or []:
        err = str(r["error"])
        cls = unreadable_class(err)
        counts[cls] += 1
        by_error[err] = by_error.get(err, 0) + 1
        if len(named) < MAX_SKIPPED_NAMED:
            at = r["observed_at"]
            named.append({"book_obs_id": r["obs_id"],
                          "observed_at": (L._epoch(at) if hasattr(
                              at, "timestamp") else at),
                          "error": err, "class": cls,
                          "source": dict(r).get("source"),
                          "read_basis": dict(r).get("read_basis")})
    total = sum(counts.values())
    return {"total": total, "gate_refused": counts[U_GATE_REFUSED],
            "deadline_cut": counts[U_DEADLINE_CUT],
            "venue_error": counts[U_VENUE_ERROR], "by_error": by_error,
            "named": named, "named_truncated": total > len(named),
            "skipped_is": ("an observation row with an error holds no book; "
                           "it was skipped, never used as a fill or an "
                           "expiry")}


def intent_of(direction: str, holding_side: str) -> str:
    if direction == "BUY":
        return ("ORDER_INTENT_BUY_SHORT" if holding_side == "SHORT"
                else "ORDER_INTENT_BUY_LONG")
    return ("ORDER_INTENT_SELL_SHORT" if holding_side == "SHORT"
            else "ORDER_INTENT_SELL_LONG")


def side_consumed(direction: str, holding_side: str) -> str:
    """Which side of the (long) book the order consumes."""
    if direction == "BUY":
        return "bids" if holding_side == "SHORT" else "offers"
    return "offers" if holding_side == "SHORT" else "bids"


def our_side(direction: str, holding_side: str) -> str:
    """The side a RESTING order of ours would sit on."""
    return "offers" if side_consumed(direction, holding_side) == "bids" \
        else "bids"


def _cent(wire: float) -> bool:
    return abs(round(float(wire) * 100) - float(wire) * 100) < 1e-9


def levels_for(market_data: dict | None, *, direction: str,
               holding_side: str) -> dict:
    """THE CONSUMED SIDE AS [{price (ours), wire, qty}], best first. `price`
    is our per-contract COST for a BUY and PROCEEDS for a SELL."""
    md = market_data if isinstance(market_data, dict) else None
    if direction == "BUY":
        lad = BS.acquisition_ladder(md, intent=intent_of("BUY",
                                                         holding_side))
        lv = [{"price": x["acquisition_price"], "wire": x["api_price"],
               "qty": x["qty"]} for x in (lad.get("levels") or [])]
    else:
        held = intent_of("BUY", holding_side)
        lad = BS.exit_ladder(md, held_intent=held)
        lv = [{"price": x["exit_price"], "wire": x["api_price"],
               "qty": x["qty"]} for x in (lad.get("levels") or [])]
    excluded = [x for x in lv if not _cent(x["wire"])]
    lv = [x for x in lv if _cent(x["wire"])]
    return {"ok": bool(lad.get("ok")), "levels": lv,
            "side": side_consumed(direction, holding_side),
            "excluded_off_cent_grid": len(excluded),
            "refusal": lad.get("refusal"),
            # a valid empty side vs a malformed / absent one
            # (paper_derek.no_book_refusal)
            "book_was": lad.get("book_was")}


def within(price: float, limit: float, direction: str, *,
           strict: bool = False) -> bool:
    if direction == "BUY":
        return price < limit - 1e-12 if strict else price <= limit + 1e-12
    return price > limit + 1e-12 if strict else price >= limit - 1e-12


def walk(levels: list, *, consumed: dict, limit: float, qty: float,
         direction: str, allow_partial: bool) -> dict:
    """THE MARKETABLE WALK. Pure. `consumed` maps wire price (str, 6dp) to
    quantity already consumed at this book instant."""
    takes, left = [], float(qty)
    for lv in levels:
        if left <= 1e-9:
            break
        if not within(lv["price"], limit, direction):
            break
        avail = float(lv["qty"]) - float(consumed.get(_wk(lv["wire"]), 0.0))
        if avail <= 1e-9:
            continue
        take = min(avail, left)
        takes.append(dict(lv, take=round(take, 6),
                          displayed=float(lv["qty"]),
                          already_consumed=float(consumed.get(
                              _wk(lv["wire"]), 0.0))))
        left -= take
    filled = round(float(qty) - left, 6)
    if not takes:
        return {"filled": 0.0, "takes": [], "refusal": R_NOTHING_WITHIN_LIMIT}
    if not allow_partial and left > 1e-9:
        return {"filled": 0.0, "takes": [], "refusal": R_FOK_SHORT,
                "would_fill": filled}
    return {"filled": filled, "takes": takes, "refusal": None,
            "complete": left <= 1e-9}


def _wk(wire) -> str:
    return "%.6f" % float(wire)


def resting_cross(levels: list, *, consumed: dict, limit: float,
                  direction: str, queue_ahead: float,
                  remaining: float) -> dict:
    """THE RESTING RULE ON ONE LATER BOOK. Pure. Liquidity STRICTLY better
    than the limit crosses; a level AT the limit is a touch and never fills.
    The crossing quantity serves the queue ahead first."""
    touch = any(abs(float(lv["price"]) - float(limit)) < 1e-12
                for lv in levels)
    crossing = []
    for lv in levels:
        if within(lv["price"], limit, direction, strict=True):
            avail = float(lv["qty"]) - float(consumed.get(_wk(lv["wire"]),
                                                          0.0))
            if avail > 1e-9:
                crossing.append(dict(lv, available=avail))
    total = sum(x["available"] for x in crossing)
    if total <= 1e-9:
        return {"filled": 0.0, "takes": [], "queue_ahead_after": queue_ahead,
                "refusal": R_TOUCH_ONLY if touch else R_NO_CROSS,
                "crossing_qty": 0.0, "touch_seen": touch}
    to_queue = min(queue_ahead, total)
    left_for_us = min(total - to_queue, remaining)
    q_after = round(queue_ahead - to_queue, 6)
    if left_for_us <= 1e-9:
        return {"filled": 0.0, "takes": [], "queue_ahead_after": q_after,
                "refusal": R_QUEUE_AHEAD, "crossing_qty": round(total, 6),
                "touch_seen": touch}
    # Consume the crossing levels in book order: the queue ahead took the
    # first `to_queue`, we take the next `left_for_us`.
    takes, skip, need = [], to_queue, left_for_us
    for x in crossing:
        a = x["available"]
        if skip >= a - 1e-12:
            skip -= a
            continue
        a -= skip
        skip = 0.0
        t = min(a, need)
        if t > 1e-9:
            takes.append(dict(x, take=round(t, 6)))
            need -= t
        if need <= 1e-9:
            break
    return {"filled": round(left_for_us - need, 6), "takes": takes,
            "queue_ahead_after": q_after, "refusal": None,
            "crossing_qty": round(total, 6), "touch_seen": touch}


#: A CROSSING LEVEL IS THE SAME LIQUIDITY UNTIL IT SHRINKS (the economic-
#: duplicate rule). A resting order's step reads every new observation of its
#: book; liquidity consumption is per observation, so a crossing level that
#: is simply still displayed on the next snapshot used to fill the SAME
#: order AGAIN at the same price and instant (production 2026-10-05: one
#: order filled 105.81 @ 0.52 twice at one instant; another 5 x 142). A level
#: that crossed us (or our queue ahead) would have traded and vanished; its
#: re-appearance at the same size is not new liquidity. So, per order: the
#: displayed size of every level that has already crossed is remembered;
#: on a later book only displayed size ABOVE the remembered size is
#: available; a level that shrinks (or disappears) lowers the memory to what
#: is still shown. Conservative: it can only remove fills, never add one.
SEEN_CROSSING_KEY = "seen_crossing"


def carry_seen(levels: list, *, consumed: dict, seen: dict) -> dict:
    """Pure. The consumption map for one later book with the order's own
    remembered crossing liquidity applied: consumed[wire] = max(the book's
    own consumption, what this order already saw cross at that wire, capped
    by what is displayed now)."""
    shown = {_wk(lv["wire"]): float(lv["qty"]) for lv in levels}
    out = dict(consumed)
    for wk, mem in (seen or {}).items():
        mem = min(float(mem), shown.get(wk, 0.0))
        if mem > 0:
            out[wk] = max(float(out.get(wk, 0.0)), mem)
    return out


def update_seen(levels: list, *, limit: float, direction: str,
                seen: dict) -> dict:
    """Pure. The memory after one book: every level shown lowers its memory
    to what it shows (a level gone is forgotten); every level that CROSSES
    the limit raises its memory to its displayed size."""
    shown = {_wk(lv["wire"]): float(lv["qty"]) for lv in levels}
    out = {wk: min(float(m), shown[wk]) for wk, m in (seen or {}).items()
           if shown.get(wk, 0.0) > 1e-9}
    for lv in levels:
        if within(lv["price"], limit, direction, strict=True):
            wk = _wk(lv["wire"])
            out[wk] = max(out.get(wk, 0.0), float(lv["qty"]))
    return {k: round(v, 6) for k, v in out.items()}


def optimistic_fill(market_data: dict | None, *, direction: str,
                    holding_side: str, qty: float, limit: float) -> dict:
    """THE OPTIMISTIC SENSITIVITY: the decision-time book, no delay, no
    consumption ledger, touch allowed, partials allowed. A bound for the
    report, NEVER a fill."""
    lv = levels_for(market_data, direction=direction,
                    holding_side=holding_side)
    got = walk(lv["levels"], consumed={}, limit=limit, qty=qty,
               direction=direction, allow_partial=True)
    cost = sum(t["take"] * t["price"] for t in got["takes"])
    return {"basis": "OPTIMISTIC_SENSITIVITY_NOT_A_FILL",
            "filled_qty": got["filled"], "gross_usd": round(cost, 6),
            "vwap": (round(cost / got["filled"], 6) if got["filled"] else
                     None)}


# ═════════════════════════════════════════════════════════════════════
# BOOK OBSERVATIONS
# ═════════════════════════════════════════════════════════════════════

async def record_book(conn, *, slug: str, read: dict, source: str,
                      read_basis: str) -> dict:
    """ONE OBSERVED BOOK, AS READ, with our receipt instant. An unreadable
    read is recorded as such (error) -- it is evidence of nothing."""
    md = read.get("marketData") if isinstance(read, dict) else None
    at = float((read or {}).get("observed_at") or time.time())
    err = (read or {}).get("error")
    if isinstance(read, dict) and read.get("shared_read"):
        # A READ THIS PROCESS ALREADY MADE seconds earlier, answered without a
        # second venue request; recorded with its ORIGINAL receipt instant.
        source = "%s:SHARED_READ" % source
    if not isinstance(md, dict):
        err = err or "NO_MARKET_DATA"
    bids = md.get("bids") if isinstance(md, dict) else None
    offers = md.get("offers") if isinstance(md, dict) else None
    state = (md.get("state") or md.get("marketState")) \
        if isinstance(md, dict) else None
    vts = (md.get("transactTime") or md.get("timestamp")) \
        if isinstance(md, dict) else None
    obs = await conn.fetchval(
        "INSERT INTO paper_book_observations (us_market_slug, observed_at, "
        " venue_ts, source, bids, offers, tick, market_state, error, "
        " read_basis) VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,"
        " $9,$10) RETURNING obs_id",
        slug, L._ts(at), None if vts is None else str(vts), source,
        json.dumps(bids, default=str) if bids is not None else None,
        json.dumps(offers, default=str) if offers is not None else None,
        None, None if state is None else str(state),
        None if err is None else str(err)[:200], read_basis)
    return {"obs_id": obs, "observed_at": at, "error": err,
            "market_data": md if isinstance(md, dict) else None}


def _md(row) -> dict | None:
    if row is None or row["error"]:
        return None
    return {"bids": L._j(row["bids"]) or [],
            "offers": L._j(row["offers"]) or []}


async def _consumed(conn, slug: str, side: str, obs_id: int) -> dict:
    rows = await conn.fetch(
        "SELECT wire_price, consumed_qty FROM paper_liquidity_consumed "
        " WHERE us_market_slug=$1 AND side_consumed=$2 AND book_obs_id=$3",
        slug, side, obs_id)
    return {_wk(r["wire_price"]): float(r["consumed_qty"]) for r in rows}


async def _consume(conn, *, slug: str, side: str, obs_id: int, wire,
                   displayed, take) -> None:
    await conn.execute(
        "INSERT INTO paper_liquidity_consumed (us_market_slug, side_consumed,"
        " wire_price, book_obs_id, displayed_qty, consumed_qty) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (us_market_slug, "
        " side_consumed, wire_price, book_obs_id) DO UPDATE SET "
        " consumed_qty = paper_liquidity_consumed.consumed_qty "
        "              + EXCLUDED.consumed_qty",
        slug, side, L.D(wire), obs_id, L.D(displayed), L.D(take))


# ═════════════════════════════════════════════════════════════════════
# SIMULATION OF OPEN ORDERS
# ═════════════════════════════════════════════════════════════════════

async def queue_ahead_at_placement(conn, *, slug: str, direction: str,
                                   holding_side: str, limit: float,
                                   market_data: dict | None,
                                   account_id: str) -> dict:
    """Displayed size on OUR side at-or-better than the limit at the
    placement book, plus earlier open paper resting orders there."""
    mine = our_side(direction, holding_side)
    # Our side as seen in OUR price space: a resting BUY_LONG sits in the
    # bids (price = bid); a resting SELL of a long sits in the offers.
    disp = 0.0
    md = market_data if isinstance(market_data, dict) else {}
    for e in (md.get(mine) or []):
        px, q, _ = BS._level(e)
        if px is None or q is None:
            continue
        wire = float(px)
        if direction == "BUY":
            ours = (1.0 - wire) if holding_side == "SHORT" else wire
            better = ours >= limit - 1e-12
        else:
            ours = (1.0 - wire) if holding_side == "SHORT" else wire
            better = ours <= limit + 1e-12
        if better:
            disp += float(q)
    earlier = await conn.fetchval(
        "SELECT coalesce(sum(qty - filled_qty), 0) FROM paper_orders "
        " WHERE account_id=$1 AND us_market_slug=$2 AND direction=$3 "
        "   AND holding_side=$4 AND order_type='RESTING' "
        "   AND state = ANY($5::text[]) AND "
        "   (CASE WHEN direction='BUY' THEN limit_price >= $6 "
        "         ELSE limit_price <= $6 END)",
        account_id, slug, direction, holding_side, list(L.OPEN_STATES),
        L.D(limit))
    return {"queue_ahead_qty": round(disp + float(earlier), 6),
            "displayed_at_or_better": round(disp, 6),
            "earlier_paper_orders": float(earlier),
            "assumptions": ["JOINS_THE_BACK_OF_THE_QUEUE",
                            "DISPLAYED_IS_ALL_THERE_IS"]}


async def simulate_order(conn, order_id: str, *, now: float,
                         fee_fn=None) -> dict:
    """ADVANCE ONE OPEN ORDER on the books observed so far. One transaction
    under the account lock; idempotent (fill keys are deterministic in the
    order, the book observation and the level)."""
    async with conn.transaction():
        acct = await conn.fetchval(
            "SELECT account_id FROM paper_orders WHERE order_id=$1", order_id)
        if acct is None:
            return {"order_id": order_id, "refusal": "NO_SUCH_ORDER"}
        await L._lock(conn, acct)
        o = await conn.fetchrow(
            "SELECT * FROM paper_orders WHERE order_id=$1 FOR UPDATE",
            order_id)
        if o["state"] not in L.OPEN_STATES:
            return {"order_id": order_id, "state": o["state"],
                    "skipped": "NOT_OPEN"}
        if o["state"] == "CANCEL_PENDING":
            got = await L.release_remainder_locked(
                conn, order_id=order_id, reason="CANCEL_CONFIRMED_BY_"
                "SIMULATOR", at=now, state="CANCELED")
            return {"order_id": order_id, "state": "CANCELED",
                    "released": got}
        if o["order_type"] == "MARKETABLE":
            return await _marketable(conn, o, now=now, fee_fn=fee_fn)
        return await _resting(conn, o, now=now, fee_fn=fee_fn)


async def _apply_takes(conn, o, *, takes, obs, basis, now, fee_fn,
                       evidence, event_detail=None) -> list:
    out = []
    side = side_consumed(o["direction"], o["holding_side"])
    for t in takes:
        await _consume(conn, slug=o["us_market_slug"], side=side,
                       obs_id=obs["obs_id"], wire=t["wire"],
                       displayed=t["qty"], take=t["take"])
        price = (float(o["limit_price"]) if basis == BASIS_CROSS
                 else float(t["price"]))
        fee = L._fee(fee_fn, t["take"], price, now)
        got = await L.apply_fill_locked(
            conn, order=dict(o), qty=t["take"], price=price,
            wire_price=t["wire"], fee=fee, filled_at=now, basis=basis,
            book_obs_id=obs["obs_id"],
            book_observed_at=L._epoch(obs["observed_at"]),
            evidence=dict(evidence, level={"wire": t["wire"],
                                           "displayed": t["qty"],
                                           "taken": t["take"]}),
            key="%s:obs%d:%s" % (o["order_id"], obs["obs_id"],
                                 _wk(t["wire"])),
            event_detail=event_detail)
        out.append(got)
    return out


async def window_observations(conn, o) -> dict:
    """THE ORDER'S WINDOW, READ ONCE: the FIRST READABLE observation in
    [eligible_at, expires_at] (receipt instant, then obs id) and every
    UNREADABLE observation in the window before it (bounded).

    Readable means the row carries no error (NULL or empty, exactly what
    `_md` reads as a book); every other row holds no book and is skipped."""
    slug, t0, t1 = o["us_market_slug"], o["eligible_at"], o["expires_at"]
    obs = await conn.fetchrow(
        "SELECT * FROM paper_book_observations WHERE us_market_slug=$1 "
        "   AND observed_at >= $2 AND observed_at <= $3 "
        "   AND coalesce(error, '') = '' "
        " ORDER BY observed_at, obs_id LIMIT 1", slug, t0, t1)
    if obs is None:
        bad = await conn.fetch(
            "SELECT obs_id, observed_at, source, read_basis, error "
            "  FROM paper_book_observations WHERE us_market_slug=$1 "
            "   AND observed_at >= $2 AND observed_at <= $3 "
            "   AND coalesce(error, '') <> '' "
            " ORDER BY observed_at, obs_id LIMIT $4",
            slug, t0, t1, MAX_SKIPPED_SCANNED)
    else:
        bad = await conn.fetch(
            "SELECT obs_id, observed_at, source, read_basis, error "
            "  FROM paper_book_observations WHERE us_market_slug=$1 "
            "   AND observed_at >= $2 AND coalesce(error, '') <> '' "
            "   AND (observed_at, obs_id) < ($3, $4) "
            " ORDER BY observed_at, obs_id LIMIT $5",
            slug, t0, obs["observed_at"], obs["obs_id"], MAX_SKIPPED_SCANNED)
    skipped = unreadable_summary(bad)
    skipped["scan_capped_at"] = (MAX_SKIPPED_SCANNED
                                 if len(bad) >= MAX_SKIPPED_SCANNED else None)
    return {"readable": obs, "skipped": skipped}


async def _marketable(conn, o, *, now: float, fee_fn) -> dict:
    """THE MARKETABLE RULE: the FIRST READABLE book observed at or after
    decision + delay, within the order's TTL.

    R30A INCIDENT REPAIR (the ENTER -> FILL collapse; merged from the inc-sim
    and router streams, which found the same defect independently). This
    used to take the first observation of ANY kind in [eligible_at,
    expires_at] and, when that observation was an errored read, release the
    order at once as THE_OBSERVED_BOOK_WAS_UNREADABLE. Those errored
    "observations" are almost all OUR OWN refusals to read -- the venue
    request gate declining to wait out a cooldown past the paper pass's
    remaining deadline (VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE), the
    pass's own deadline already spent (PAPER_BOOK_READ_DEADLINE_EXCEEDED), a
    429 -- recorded with our receipt instant, so they landed inside the
    window and killed the order before any book was ever seen. Production, 7
    days to 2026-10-04 20:54Z (research-sql run 37233864395, E1-E3): 100 of
    the 168 paper entry orders (28 of 45 completed-game, 72 of 123
    exploration) expired this way; 94 of them had nothing but errored reads
    in their window (44/day, 61% of entry orders, on the 24 h receipt).

    An errored read is evidence of nothing: no book was observed, so it can
    neither fill nor refuse a fill -- the SAME rule the resting path already
    applies (`_resting` skips an unreadable book) and the frozen config's own
    wording ("the FIRST book observed at or after decision time + delay; no
    such book before the TTL -> EXPIRED"). So errored reads are skipped
    (named and classed GATE_REFUSED / DEADLINE_CUT / VENUE_ERROR on the
    order's evidence and events); the order fills on the first READABLE book
    in its window (chronologically first, never chosen), or expires at its
    TTL with NO_READABLE_BOOK_OBSERVED_BEFORE_THE_ORDER_EXPIRED and the
    counts of unreadable reads it saw. Nothing else changes: the delay, the
    TTL, the limit, the depth walk, the consumption ledger and the fees are
    the session's frozen ones."""
    oid = o["order_id"]
    win = await window_observations(conn, o)
    obs, skipped = win["readable"], win["skipped"]
    if obs is not None and _md(obs) is None:
        # Unreachable by construction (the query reads exactly what `_md`
        # reads as a book); kept so a future change to either cannot turn an
        # unreadable row into a fill.
        obs = None
    if obs is None:
        unreadable = int(skipped["total"])
        if float(now) >= L._epoch(o["expires_at"]):
            # EXPIRED ONLY AT EXPIRES_AT, and the reason says which: nothing
            # was observed in the window, or only reads that held no book --
            # named, and counted by who refused them.
            reason = (R_NO_READABLE_BOOK if unreadable
                      else R_NO_BOOK_IN_WINDOW)
            rel = await L.release_remainder_locked(
                conn, order_id=oid, reason=reason, at=now, state="EXPIRED",
                detail=({"unreadable_books_skipped": skipped,
                         "unreadable_reads_in_window": unreadable}
                        if unreadable else
                        {"unreadable_reads_in_window": 0}))
            return {"order_id": oid, "state": "EXPIRED", "refusal": reason,
                    "released": rel, "unreadable_books_skipped": skipped,
                    "unreadable_reads_in_window": unreadable}
        return {"order_id": oid, "state": o["state"], "pending": True,
                "refusal": (R_NO_READABLE_BOOK_YET if unreadable
                            else R_NO_BOOK_YET),
                "unreadable_books_skipped": skipped,
                "unreadable_reads_in_window": unreadable}
    md = _md(obs)
    lv = levels_for(md, direction=o["direction"],
                    holding_side=o["holding_side"])
    consumed = await _consumed(conn, o["us_market_slug"], lv["side"],
                               obs["obs_id"])
    remaining = float(o["qty"]) - float(o["filled_qty"])
    got = walk(lv["levels"], consumed=consumed,
               limit=float(o["limit_price"]), qty=remaining,
               direction=o["direction"], allow_partial=o["allow_partial"])
    evidence = {"book_obs_id": obs["obs_id"],
                "book_observed_at": L._epoch(obs["observed_at"]),
                "eligible_at": L._epoch(o["eligible_at"]),
                "side_consumed": lv["side"],
                "levels_excluded_off_cent_grid": lv["excluded_off_cent_grid"],
                "assumptions": ASSUMPTIONS["marketable"],
                "simulator_version": o["simulator_version"]}
    # THE SKIPPED READS TRAVEL WITH THE OUTCOME: on the fill's evidence and
    # FILL event, and on the release event of an unfilled remainder.
    extra = ({"unreadable_books_skipped": skipped} if skipped["total"]
             else {})
    evidence.update(extra)
    fills = await _apply_takes(conn, o, takes=got["takes"], obs=obs,
                               basis=BASIS_WALK, now=now, fee_fn=fee_fn,
                               evidence=evidence, event_detail=extra)
    state = "FILLED" if got.get("complete") else None
    rel = None
    if not got.get("complete"):
        reason = got.get("refusal") or R_IOC_REMAINDER
        rel = await L.release_remainder_locked(
            conn, order_id=oid, reason=reason, at=now,
            state=("CANCELED" if fills else "EXPIRED"),
            detail=dict({"book_obs_id": obs["obs_id"],
                         "filled_qty": got["filled"]}, **extra))
        state = rel.get("state")
    return {"order_id": oid, "state": state, "filled_qty": got["filled"],
            "fills": fills, "refusal": got.get("refusal"),
            "released": rel, "book_obs_id": obs["obs_id"],
            "unreadable_books_skipped": skipped,
            "first_fill": any(x.get("first_fill") for x in fills
                              if x.get("ok") and not x.get("duplicate"))}


async def _resting(conn, o, *, now: float, fee_fn) -> dict:
    oid = o["order_id"]
    qb = dict(L._j(o["queue_basis"]) or {})
    last_obs = int(qb.get("last_obs_id") or qb.get("placement_obs_id") or 0)
    q_ahead = float(o["queue_ahead_qty"] or 0.0)
    seen = dict(qb.get(SEEN_CROSSING_KEY) or {})
    rows = await conn.fetch(
        "SELECT * FROM paper_book_observations WHERE us_market_slug=$1 "
        "   AND obs_id > $2 AND observed_at >= $3 AND observed_at <= $4 "
        " ORDER BY obs_id LIMIT 50", o["us_market_slug"], last_obs,
        o["eligible_at"], o["expires_at"])
    fills, reasons = [], []
    remaining = float(o["qty"]) - float(o["filled_qty"])
    for obs in rows:
        last_obs = obs["obs_id"]
        md = _md(obs)
        if md is None:
            reasons.append({"book_obs_id": obs["obs_id"],
                            "reason": R_BOOK_UNREADABLE})
            continue
        lv = levels_for(md, direction=o["direction"],
                        holding_side=o["holding_side"])
        consumed = await _consumed(conn, o["us_market_slug"], lv["side"],
                                   obs["obs_id"])
        consumed = carry_seen(lv["levels"], consumed=consumed, seen=seen)
        got = resting_cross(lv["levels"], consumed=consumed,
                            limit=float(o["limit_price"]),
                            direction=o["direction"], queue_ahead=q_ahead,
                            remaining=remaining)
        seen = update_seen(lv["levels"], limit=float(o["limit_price"]),
                           direction=o["direction"], seen=seen)
        q_before, q_ahead = q_ahead, got["queue_ahead_after"]
        if not got["takes"]:
            reasons.append({"book_obs_id": obs["obs_id"],
                            "reason": got["refusal"],
                            "crossing_qty": got["crossing_qty"],
                            "queue_ahead_after": q_ahead})
            continue
        evidence = {"book_obs_id": obs["obs_id"],
                    "book_observed_at": L._epoch(obs["observed_at"]),
                    "crossing_qty": got["crossing_qty"],
                    "queue_ahead_before": q_before,
                    "queue_ahead_after": q_ahead,
                    "assumptions": ASSUMPTIONS["resting"],
                    "simulator_version": o["simulator_version"]}
        o2 = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                 "order_id=$1", oid)
        got_fills = await _apply_takes(conn, o2, takes=got["takes"], obs=obs,
                                       basis=BASIS_CROSS, now=now,
                                       fee_fn=fee_fn, evidence=evidence)
        fills.extend(got_fills)
        remaining -= got["filled"]
        if remaining <= 1e-9:
            break
    qb.update(last_obs_id=last_obs, queue_ahead_remaining=q_ahead,
              **{SEEN_CROSSING_KEY: seen})
    cur = await conn.fetchrow("SELECT state FROM paper_orders WHERE "
                              "order_id=$1", oid)
    if cur["state"] in L.OPEN_STATES:
        await conn.execute(
            "UPDATE paper_orders SET queue_basis=$2::jsonb, "
            " queue_ahead_qty=$3, updated_at=now() WHERE order_id=$1",
            oid, json.dumps(qb, default=str), L.D(q_ahead))
    if not rows or (reasons and not fills):
        why = R_NO_NEW_BOOK if not rows else reasons[-1]["reason"]
        await L.event(conn, order_id=oid, kind="NO_FILL_EVIDENCE", at=now,
                      simulator_version=o["simulator_version"],
                      detail={"reason": why, "books_examined": len(rows),
                              "queue_ahead_remaining": q_ahead,
                              "missing_update_is_not_a_fill": True})
    state = cur["state"]
    rel = None
    if state in L.OPEN_STATES and float(now) >= L._epoch(o["expires_at"]):
        rel = await L.release_remainder_locked(
            conn, order_id=oid, reason=R_GTD_EXPIRED, at=now,
            state="EXPIRED")
        state = "EXPIRED"
    return {"order_id": oid, "state": state, "fills": fills,
            "no_fill_reasons": reasons[-5:], "books_examined": len(rows),
            "queue_ahead_remaining": q_ahead, "released": rel,
            "first_fill": any(x.get("first_fill") for x in fills
                              if x.get("ok") and not x.get("duplicate"))}


async def request_cancel(conn, order_id: str, *, now: float,
                         reason: str) -> dict:
    """CANCEL-PENDING: the order stops being fillable only when the simulator
    confirms the cancel on its next step (as a venue cancel is not terminal
    until confirmed). Fills already applied stay."""
    async with conn.transaction():
        acct = await conn.fetchval(
            "SELECT account_id FROM paper_orders WHERE order_id=$1", order_id)
        if acct is None:
            return {"ok": False, "refusal": "NO_SUCH_ORDER"}
        await L._lock(conn, acct)
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                "order_id=$1 FOR UPDATE", order_id)
        if o["state"] not in ("RESTING", "PARTIALLY_FILLED",
                              "PENDING_SIMULATION"):
            return {"ok": False, "refusal": L.R_ORDER_NOT_OPEN,
                    "state": o["state"]}
        await conn.execute(
            "UPDATE paper_orders SET state='CANCEL_PENDING', updated_at=now()"
            " WHERE order_id=$1", order_id)
        await L.event(conn, order_id=order_id, kind="CANCEL_REQUESTED",
                      at=now, simulator_version=o["simulator_version"],
                      detail={"reason": reason}, source="PAPER_AGENT")
    return {"ok": True, "state": "CANCEL_PENDING"}


async def open_orders(conn, account_id: str) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT order_id, order_type, us_market_slug, state FROM paper_orders"
        " WHERE account_id=$1 AND state = ANY($2::text[]) ORDER BY created_at",
        account_id, list(L.OPEN_STATES))]


async def run(conn, *, account_id: str, now: float, fee_fn=None,
              deadline: float | None = None) -> dict:
    """ADVANCE EVERY OPEN ORDER, bounded by `deadline` (monotonic)."""
    out: dict[str, Any] = {"examined": 0, "fills": 0, "first_fills": [],
                           "terminal": 0, "results": []}
    for r in await open_orders(conn, account_id):
        if deadline is not None and time.monotonic() > deadline:
            out["budget_exhausted"] = True
            break
        got = await simulate_order(conn, r["order_id"], now=now,
                                   fee_fn=fee_fn)
        out["examined"] += 1
        nf = sum(1 for x in got.get("fills") or []
                 if x.get("ok") and not x.get("duplicate"))
        out["fills"] += nf
        if got.get("first_fill"):
            out["first_fills"].append(r["order_id"])
        if got.get("state") in L.TERMINAL_STATES:
            out["terminal"] += 1
        out["results"].append({k: got.get(k) for k in (
            "order_id", "state", "refusal", "filled_qty", "books_examined",
            "pending")})
    return out


def describe() -> dict:
    return {"version": VERSION, "event_source": EVENT_SOURCE,
            "assumptions": ASSUMPTIONS,
            "bases": [BASIS_WALK, BASIS_CROSS]}
