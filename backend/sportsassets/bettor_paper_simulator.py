"""PAPER_SIM_V1 -- THE FILL SIMULATOR. SIMULATED, NOT VERIFIED EXECUTION.

Every acknowledgement and fill it writes carries event_source SIMULATOR and
the session's frozen simulator version. It reads only books the paper path
observed through the read-only market-data client
(`paper_book_observations`); it never touches the venue.

── MARKETABLE ORDERS (IOC / FOK) ──────────────────────────────────────────
  * DELAY. The order is evaluated on the FIRST book observed at or after
    decided_at + decision_to_execution_delay_s (`eligible_at`). No such book
    before `expires_at` -> EXPIRED with NO fill, reservation released.
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
    "marketable": ["NEXT_BOOK_AT_OR_AFTER_DECISION_PLUS_DELAY",
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
            "refusal": lad.get("refusal")}


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
                       evidence) -> list:
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
                                 _wk(t["wire"])))
        out.append(got)
    return out


async def _marketable(conn, o, *, now: float, fee_fn) -> dict:
    """THE MARKETABLE RULE: the FIRST READABLE book observed at or after
    decision + delay, within the order's TTL.

    R30A INCIDENT REPAIR (the ENTER -> FILL collapse). This used to take the
    first observation of ANY kind in [eligible_at, expires_at] and, when that
    observation was an errored read, release the order at once as
    THE_OBSERVED_BOOK_WAS_UNREADABLE. Those errored "observations" are almost
    all OUR OWN refusals to read -- the venue request gate declining to wait
    out a cooldown past the paper pass's remaining deadline
    (VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE), the pass's own deadline
    already spent (PAPER_BOOK_READ_DEADLINE_EXCEEDED), a 429 -- recorded with
    our receipt instant, so they landed inside the window and killed the
    order before any book was ever seen. Production, 7 days to 2026-10-04
    20:54Z (research-sql run 37233864395, E1-E3): 100 of the 168 paper entry
    orders (28 of 45 completed-game, 72 of 123 exploration) expired this way;
    94 of them had nothing but errored reads in their window.

    An errored read is evidence of nothing: no book was observed, so it can
    neither fill nor refuse a fill -- the SAME rule the resting path already
    applies (`_resting` skips an unreadable book) and the frozen config's own
    wording ("the FIRST book observed at or after decision time + delay; no
    such book before the TTL -> EXPIRED"). So errored reads are skipped; the
    order fills on the first READABLE book in its window (chronologically
    first, never chosen), or expires at its TTL with
    NO_READABLE_BOOK_OBSERVED_BEFORE_THE_ORDER_EXPIRED and the count of
    unreadable reads it saw. Nothing else changes: the delay, the TTL, the
    limit, the depth walk, the consumption ledger and the fees are the
    session's frozen ones."""
    oid = o["order_id"]
    obs = await conn.fetchrow(
        "SELECT * FROM paper_book_observations WHERE us_market_slug=$1 "
        "   AND observed_at >= $2 AND observed_at <= $3 AND error IS NULL "
        " ORDER BY observed_at, obs_id LIMIT 1", o["us_market_slug"],
        o["eligible_at"], o["expires_at"])
    if obs is not None and _md(obs) is None:
        obs = None
    if obs is None:
        unreadable = int(await conn.fetchval(
            "SELECT count(*) FROM paper_book_observations "
            " WHERE us_market_slug=$1 AND observed_at >= $2 "
            "   AND observed_at <= $3 AND error IS NOT NULL",
            o["us_market_slug"], o["eligible_at"], o["expires_at"]) or 0)
        if float(now) >= L._epoch(o["expires_at"]):
            reason = (R_NO_READABLE_BOOK_IN_WINDOW if unreadable
                      else R_NO_BOOK_IN_WINDOW)
            rel = await L.release_remainder_locked(
                conn, order_id=oid, reason=reason, at=now,
                state="EXPIRED",
                detail={"unreadable_reads_in_window": unreadable})
            return {"order_id": oid, "state": "EXPIRED",
                    "refusal": reason, "released": rel,
                    "unreadable_reads_in_window": unreadable}
        return {"order_id": oid, "state": o["state"], "pending": True,
                "refusal": R_NO_BOOK_YET,
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
    fills = await _apply_takes(conn, o, takes=got["takes"], obs=obs,
                               basis=BASIS_WALK, now=now, fee_fn=fee_fn,
                               evidence=evidence)
    state = "FILLED" if got.get("complete") else None
    rel = None
    if not got.get("complete"):
        reason = got.get("refusal") or R_IOC_REMAINDER
        rel = await L.release_remainder_locked(
            conn, order_id=oid, reason=reason, at=now,
            state=("CANCELED" if fills else "EXPIRED"),
            detail={"book_obs_id": obs["obs_id"],
                    "filled_qty": got["filled"]})
        state = rel.get("state")
    return {"order_id": oid, "state": state, "filled_qty": got["filled"],
            "fills": fills, "refusal": got.get("refusal"),
            "released": rel, "book_obs_id": obs["obs_id"],
            "first_fill": any(x.get("first_fill") for x in fills
                              if x.get("ok") and not x.get("duplicate"))}


async def _resting(conn, o, *, now: float, fee_fn) -> dict:
    oid = o["order_id"]
    qb = dict(L._j(o["queue_basis"]) or {})
    last_obs = int(qb.get("last_obs_id") or qb.get("placement_obs_id") or 0)
    q_ahead = float(o["queue_ahead_qty"] or 0.0)
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
        got = resting_cross(lv["levels"], consumed=consumed,
                            limit=float(o["limit_price"]),
                            direction=o["direction"], queue_ahead=q_ahead,
                            remaining=remaining)
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
    qb.update(last_obs_id=last_obs, queue_ahead_remaining=q_ahead)
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
