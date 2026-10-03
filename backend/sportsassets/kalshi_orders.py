"""KALSHI ORDERS: translation, fees, the order-state machine and parsers.

Pure functions only; nothing here sends anything (kalshi_venue does, behind
its gate). The shapes follow the execution mirror (execmirror.py) so a
Kalshi row reads like a Polymarket US row:

  translation  a BETTOR mirror intent (BUY_LONG / BUY_SHORT / SELL_LONG /
               SELL_SHORT, wire price, qty, tif, post-only) -> the Kalshi V2
               order body (kalshi.py:470-481): YES ticker, side bid|ask,
               count 'N.00', price '0.XX00', time_in_force, expiration_time,
               self_trade_prevention_type, and a client_order_id derived
               DETERMINISTICALLY from the mirror id (idempotency key).
  quantity     execmirror.scale_qty, unchanged: nearest whole contract at
               scale, half-to-even; a sub-minimum is EXCLUDED as
               BELOW_VENUE_MINIMUM, never rounded up. Exits sell the same
               fraction of LIVE inventory (execmirror.plan_sell, unchanged).
  price        Kalshi's 1-cent tick (kalshi.py:448-452: tick 0.01, prices in
               [0.01, 0.99]). A wire price off the tick is rounded AGAINST
               us -- a buy limit DOWN, a sell limit UP -- so the live order
               is never more aggressive than the paper order.
  shorts       Kalshi V2 is a single YES-denominated book and edge-engine
               only ever sells contracts it holds (kalshi.py:461-469). A
               BETTOR short is therefore held as YES on the complementary
               ticker, which kalshi_mapping must ESTABLISH (explicitly
               declared complement outcome, complement payoff vector);
               price = 1 - wire price.
  post-only    No venue post-only flag is exercised by edge-engine, so none
               is sent. A RESTING (post-only) paper order is checked against
               the current book and EXCLUDED if it would cross.
  tif          IOC -> immediate_or_cancel; GTD -> good_till_canceled with
               expiration_time = the paper expiry (kalshi.py:477-481). FOK is
               EXCLUDED (unverified at the venue; mapping it to IOC would
               change its meaning).
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from . import execmirror as EM
from . import kalshi_account as KA
from . import kalshi_venue as KV

VERSION = "KALSHI_ORDERS_V1"
TICK = Decimal("0.01")
MIN_PRICE, MAX_PRICE = Decimal("0.01"), Decimal("0.99")
VENUE_MINIMUM = 1                     # whole contracts (kalshi.py:473 'N.00')
STP = "taker_at_cross"                # kalshi.py:475
TIF_KALSHI = {"IOC": "immediate_or_cancel", "GTD": "good_till_canceled"}
CLIENT_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "bettor:kalshi-smalllive")

# exclusion codes (execmirror's, plus Kalshi-specific ones)
BELOW_VENUE_MINIMUM = EM.BELOW_VENUE_MINIMUM
ABOVE_ORDER_CAP = EM.ABOVE_ORDER_CAP
INSUFFICIENT_CASH = EM.INSUFFICIENT_CASH
UNSUPPORTED_ORDER = EM.UNSUPPORTED_ORDER
MAPPING_NOT_ESTABLISHED = "MAPPING_NOT_ESTABLISHED"
PRICE_OUT_OF_RANGE = "PRICE_OUT_OF_RANGE"
POST_ONLY_WOULD_CROSS = "POST_ONLY_WOULD_CROSS"
POST_ONLY_BOOK_UNKNOWN = "POST_ONLY_BOOK_UNKNOWN"

LEGS = {  # intent -> (holding, action, V2 side)
    "BUY_LONG": ("LONG", "buy", "bid"), "SELL_LONG": ("LONG", "sell", "ask"),
    "BUY_SHORT": ("SHORT", "buy", "bid"), "SELL_SHORT": ("SHORT", "sell", "ask"),
}


def norm_intent(intent: str) -> str:
    s = str(intent or "").upper()
    return s[len("ORDER_INTENT_"):] if s.startswith("ORDER_INTENT_") else s


# ── fees ─────────────────────────────────────────────────────────────
#
# edge-engine charges Kalshi's taker fee as 0.07 * p * (1 - p) per contract
# (kalshi.py:304-305; kalshi_crypto.py:112-114) and rounds the ORDER's fee UP
# to the cent: ceil(fee * filled * 100) / 100 (kalshi_crypto.py:512). That
# is Kalshi's published general schedule, ceil(0.07 * C * P * (1 - P)).
#
# MAKER FEES NEED CONFIRMATION. edge-engine's prose and base adapter treat
# resting orders as fee-free (kalshi.py:394-399; base.py:57-60); Kalshi's
# schedule has charged maker fees on some series. Until the ACCOUNT's fee
# schedule is confirmed the maker coefficient defaults to the TAKER one --
# conservative: economics can only be understated, never flattered.
TAKER_COEFFICIENT = Decimal("0.07")
MAKER_COEFFICIENT_DEFAULT = TAKER_COEFFICIENT
MAKER_FEE_CONFIRMED = False


def fee_for(count, price, side: str = "yes", maker: bool = False, *,
            maker_coefficient=None) -> Decimal:
    """Kalshi trading fee in dollars for ONE order of `count` contracts at
    `price` (dollars, the price of the leg traded), rounded UP to the cent.
    Size-dependent: the rounding is applied to the order, not per contract.
    Symmetric in p <-> 1-p, so `side` (yes / no) does not change it."""
    c, p = Decimal(str(count)), Decimal(str(price))
    if c <= 0 or c != c.to_integral_value():
        raise ValueError("count must be a positive whole number of contracts")
    if not (Decimal(0) < p < Decimal(1)):
        raise ValueError("price must be strictly between 0 and 1")
    if str(side).lower() not in ("yes", "no"):
        raise ValueError("side is yes or no")
    coef = TAKER_COEFFICIENT if not maker else Decimal(str(
        MAKER_COEFFICIENT_DEFAULT if maker_coefficient is None else maker_coefficient))
    raw = coef * c * p * (Decimal(1) - p)
    return (raw * 100).to_integral_value(rounding=ROUND_CEILING) / 100


# ── translation ──────────────────────────────────────────────────────

def client_order_id(mirror_id: str) -> str:
    """Deterministic per mirror row: a re-plan or a retry of the same row
    carries the same id, so reconciliation can find the first attempt."""
    if not mirror_id:
        raise ValueError("mirror_id required")
    return str(uuid.uuid5(CLIENT_ID_NAMESPACE, str(mirror_id)))


def round_price(p: Decimal, action: str) -> Decimal:
    return p.quantize(TICK, rounding=ROUND_FLOOR if action == "buy" else ROUND_CEILING)


def _epoch(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return int(v.timestamp()) if v.tzinfo else None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(d.timestamp()) if d.tzinfo else None


def _excluded(code, **kw) -> dict:
    out = {"state": "EXCLUDED", "exclusion": code, "live_qty": 0,
           "payload": None, "venue_minimum": VENUE_MINIMUM}
    out.update(kw)
    return out


def plan(order: dict, target, *, scale=1000, max_order_usd=25,
         buying_power=None, book: dict | None = None,
         live_held: int = 0, live_committed: int = 0,
         paper_open_qty=None, opened_intent: str | None = None) -> dict:
    """Translate one BETTOR mirror intent into a Kalshi plan.

    order: execmirror's paper-order shape (mirror_id, intent, wire_price,
      qty, time_in_force, order_type, expires_at, role).
    target: the kalshi_mapping.MappingVerdict for this order's holding
      (LONG for *_LONG intents, SHORT for *_SHORT) -- or None.
    book: {"best_bid": Decimal|None, "best_ask": Decimal|None} on the YES
      ticker, required only for post-only orders."""
    intent = norm_intent(order.get("intent"))
    if intent not in LEGS:
        return _excluded(UNSUPPORTED_ORDER, detail={"intent": order.get("intent")})
    holding, action, side = LEGS[intent]
    if target is None or getattr(target, "verdict", None) != "ESTABLISHED" \
            or getattr(target, "holding", None) != holding:
        return _excluded(MAPPING_NOT_ESTABLISHED, detail={
            "holding": holding,
            "missing": list(getattr(target, "missing", []) or []),
            "mismatched": list(getattr(target, "mismatched", []) or [])})
    ticker = target.kalshi_ticker
    tif = str(order.get("time_in_force") or "")
    if tif not in TIF_KALSHI:
        return _excluded(UNSUPPORTED_ORDER, ticker=ticker,
                         detail={"time_in_force": tif,
                                 "why": "only IOC and GTD are verified at Kalshi"})
    expiration = None
    if tif == "GTD":
        expiration = _epoch(order.get("expires_at"))
        if expiration is None:
            return _excluded(UNSUPPORTED_ORDER, ticker=ticker,
                             detail={"why": "GTD without a timezone-aware expiry"})
    wire = Decimal(str(order["wire_price"]))
    leg_px = wire if holding == "LONG" else Decimal(1) - wire
    px = round_price(leg_px, action)
    base = {"ticker": ticker, "holding": holding, "action": action, "side": side,
            "contract_side": "yes", "intent": intent, "paper_wire_price": wire,
            "leg_price_exact": leg_px, "price": px,
            "price_rounding_delta": px - leg_px, "venue_minimum": VENUE_MINIMUM}
    if not (MIN_PRICE <= px <= MAX_PRICE):
        return _excluded(PRICE_OUT_OF_RANGE, **base)
    post_only = str(order.get("order_type")) == "RESTING"
    if post_only:
        ref = (book or {}).get("best_ask" if action == "buy" else "best_bid")
        if ref is None:
            return _excluded(POST_ONLY_BOOK_UNKNOWN, **base)
        ref = Decimal(str(ref))
        if (action == "buy" and px >= ref) or (action == "sell" and px <= ref):
            return _excluded(POST_ONLY_WOULD_CROSS, **base,
                             detail={"reference": str(ref)})
    if action == "buy":
        exact, live, delta = EM.scale_qty(order["qty"], scale)
        base.update(paper_qty=Decimal(str(order["qty"])), scale=Decimal(str(scale)),
                    raw_scaled_qty=exact, rounding_delta=delta)
        if live < VENUE_MINIMUM:
            return _excluded(BELOW_VENUE_MINIMUM, **base,
                             detail={"why": "paper qty / %s rounds to 0 whole "
                                            "contracts" % scale})
        notional = px * live
        fee = fee_for(live, px, maker=post_only)
        if notional > Decimal(str(max_order_usd)):
            return _excluded(ABOVE_ORDER_CAP, **base, live_qty=live,
                             detail={"cost_usd": str(notional),
                                     "cap_usd": str(max_order_usd)})
        if buying_power is not None and notional + fee > Decimal(str(buying_power)):
            return _excluded(INSUFFICIENT_CASH, **base, live_qty=live,
                             detail={"cost_usd": str(notional + fee),
                                     "buying_power_usd": str(buying_power)})
    else:
        em_order = dict(order, intent="ORDER_INTENT_" + intent,
                        us_market_slug=order.get("us_market_slug") or ticker,
                        time_in_force=tif)
        sp = EM.plan_sell(em_order, scale=scale, paper_open_qty=paper_open_qty,
                          live_held=live_held, live_committed=live_committed,
                          opened_intent=opened_intent)
        base.update(paper_qty=Decimal(str(order["qty"])), scale=Decimal(str(scale)),
                    raw_scaled_qty=sp.scaled_qty, rounding_delta=sp.rounding_delta)
        if sp.state != "PLANNED":
            return _excluded(sp.exclusion, **base, detail=sp.detail)
        live = sp.live_qty
        notional = px * live
        fee = fee_for(live, px, maker=post_only)
    payload = {"ticker": ticker,
               "client_order_id": client_order_id(order["mirror_id"]),
               "side": side, "count": "%d.00" % live, "price": "%.4f" % px,
               "self_trade_prevention_type": STP,
               "time_in_force": TIF_KALSHI[tif]}
    if expiration is not None:
        payload["expiration_time"] = expiration
    return dict(base, state="PLANNED", exclusion=None, live_qty=live,
                live_notional=notional, fee_estimate=fee, post_only=post_only,
                payload=payload, client_order_id=payload["client_order_id"])


# ── the order-state machine ──────────────────────────────────────────

TERMINAL = ("FILLED", "CANCELLED", "EXPIRED", "REJECTED", "EXCLUDED")
TRANSITIONS = {
    "PLANNED": {"SUBMITTING", "EXCLUDED"},
    "SUBMITTING": {"OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED",
                   "UNKNOWN"},
    "UNKNOWN": {"OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED",
                "REJECTED"},
    "OPEN": {"PARTIALLY_FILLED", "FILLED", "CANCEL_REQUESTED", "CANCELLED",
             "EXPIRED"},
    "PARTIALLY_FILLED": {"FILLED", "CANCEL_REQUESTED", "CANCELLED", "EXPIRED"},
    "CANCEL_REQUESTED": {"CANCELLED", "FILLED", "EXPIRED"},
}


class IllegalTransition(ValueError):
    pass


def transition(current: str, new: str) -> str:
    if current == new and current not in TERMINAL:
        return new
    if new not in TRANSITIONS.get(current, set()):
        raise IllegalTransition("%s -> %s" % (current, new))
    return new


def state_from_venue(status, fill_count, count, prev_state: str | None = None) -> str:
    """Kalshi order status (resting / executed / canceled) + cumulative fill
    -> lane state. An unrecognised status is UNKNOWN, never assumed."""
    s = str(status or "").lower()
    f = Decimal(str(fill_count or 0))
    c = Decimal(str(count or 0))
    if s == "resting":
        if prev_state == "CANCEL_REQUESTED":
            return "CANCEL_REQUESTED"
        return "PARTIALLY_FILLED" if f > 0 else "OPEN"
    if s == "executed":
        return "FILLED"
    if s in ("canceled", "cancelled"):
        return "FILLED" if c > 0 and f >= c else "CANCELLED"
    if s == "expired":
        return "FILLED" if c > 0 and f >= c else "EXPIRED"
    return "UNKNOWN"


# ── acknowledgement (kalshi.py:494-527) ──────────────────────────────

# 409 is deliberately NOT here: a conflict on a repeated client_order_id can
# mean the order already EXISTS, so it is reconciled, never read as "nothing
# placed".
REJECT_STATUSES = (400, 401, 403, 404, 410, 422)


def _fill_from_order(order: dict, count) -> Decimal | None:
    for key in ("filled_count", "fill_count", "matched_count"):
        if order.get(key) is not None:
            d = KA._dec(order[key])
            return d if d is not None else Decimal(0)
    if order.get("remaining_count") is not None:
        r = KA._dec(order["remaining_count"])
        return Decimal(0) if r is None else max(Decimal(0), Decimal(str(count)) - r)
    return None


def parse_ack(resp, count) -> dict:
    """ACCEPTED (with order id, venue status, state), REJECTED (the venue
    answered and refused: nothing placed), AMBIGUOUS (we cannot know whether
    an order exists: reconcile by client_order_id before ANY retry), or
    REFUSED (the gate refused: nothing sent).

    The ack's fill count drives the order STATE only. It is not a fill:
    positions are built from /portfolio/fills rows alone. A missing fill
    field reads as zero filled (fail closed, kalshi.py:500-522)."""
    if isinstance(resp, KV.Refusal):
        return {"outcome": "REFUSED", "code": resp.code, "state": "EXCLUDED",
                "sent": False}
    if resp.status is None:
        return {"outcome": "AMBIGUOUS", "state": "UNKNOWN", "error": resp.error}
    if resp.status in (200, 201):
        body = resp.body if isinstance(resp.body, dict) else {}
        order = body.get("order") or body
        oid = order.get("order_id") or order.get("id")
        filled = _fill_from_order(order, count)
        if not oid:
            return {"outcome": "AMBIGUOUS", "state": "UNKNOWN",
                    "error": "ACCEPTED_WITHOUT_ORDER_ID"}
        f = filled if filled is not None else Decimal(0)
        status = order.get("status")
        st = state_from_venue(status, f, count)
        return {"outcome": "ACCEPTED", "order_id": str(oid), "venue_status": status,
                "ack_fill_count": f, "fill_field_present": filled is not None,
                "client_order_id": order.get("client_order_id"),
                "state": st}
    if resp.status in REJECT_STATUSES:
        return {"outcome": "REJECTED", "state": "REJECTED", "status": resp.status,
                "error": (resp.error or "")[:300]}
    return {"outcome": "AMBIGUOUS", "state": "UNKNOWN", "status": resp.status,
            "error": (resp.error or "")[:300]}


def poll_translate(row: dict, order_record: dict) -> dict:
    """One poll of a venue order record -> new lane state (transition-checked)."""
    o = KA.parse_order(order_record)
    cum = o["fill_count"]
    if cum is None and o["remaining_count"] is not None:
        cum = max(Decimal(0), Decimal(str(row["live_qty"])) - o["remaining_count"])
    new = state_from_venue(o["status"], cum or 0, row["live_qty"], row.get("state"))
    if new == "UNKNOWN":
        return {"state": row.get("state"), "venue_status": o["status"],
                "flag": "UNRECOGNISED_VENUE_STATUS", "cum": cum}
    return {"state": transition(row["state"], new), "venue_status": o["status"],
            "cum": cum}


# ── fills ────────────────────────────────────────────────────────────

def new_fills(fill_rows: list, *, known_trade_ids: set, order_ids: set) -> list:
    """Venue fills for our orders not seen before, de-duplicated by trade_id
    (the dedupe truth, kalshi.py:798-801). Rows without a trade id or with
    an unreadable count/price are returned flagged, never silently used."""
    out = []
    for r in fill_rows:
        f = KA.parse_fill(r)
        if f["order_id"] not in order_ids or not f["trade_id"] \
                or f["trade_id"] in known_trade_ids:
            continue
        f["usable"] = f["count"] is not None and f["count"] > 0 and f["price"] is not None
        out.append(f)
        known_trade_ids = known_trade_ids | {f["trade_id"]}
    return out


# ── cancel (kalshi.py:995-1013) ──────────────────────────────────────

def cancel_request(order_id: str) -> dict:
    if not order_id:
        raise ValueError("order_id required")
    return {"method": "DELETE",
            "path": KV.API_PREFIX + "/portfolio/orders/%s" % order_id}


def parse_cancel(resp) -> dict:
    """CANCELLED (confirmed dead, safe to re-plan), GONE (404: already
    filled or expired -- do NOT repost until the fills read has spoken),
    ERROR (ambiguous: treat as possibly live, never repost on top), REFUSED
    (nothing sent)."""
    if isinstance(resp, KV.Refusal):
        return {"outcome": "REFUSED", "code": resp.code, "repost_safe": False}
    if resp.status in (200, 204):
        return {"outcome": "CANCELLED", "repost_safe": True}
    if resp.status == 404:
        return {"outcome": "GONE", "repost_safe": False}
    return {"outcome": "ERROR", "repost_safe": False, "status": resp.status}


# ── ambiguous submission ─────────────────────────────────────────────

AMBIGUOUS_GRACE_S = 45.0


def epoch_s(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dt.datetime):
        return v.timestamp()
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def reconcile_ambiguous(row: dict, orders_read, fills_read, *, now: float,
                        mapped_order_ids: set = frozenset(),
                        grace_s: float = AMBIGUOUS_GRACE_S) -> dict:
    """An UNKNOWN submission, resolved from the venue BEFORE anything is
    retried. orders_read / fills_read are list responses (Response, page
    list or body) from /portfolio/orders (ALL statuses) and /portfolio/fills.

    ADOPT          an order with our client_order_id exists: take its id/state
    UNATTRIBUTED   no such order, but an unmapped fill on this ticker and
                   action appeared after the attempt began: never retry; the
                   account reconciliation must explain it
    WAIT           inside the grace window, or a read failed
    NOT_FOUND      after the grace window, both reads complete and empty of
                   it: a retry is permitted and MUST reuse the same
                   client_order_id"""
    orders, oerr, ocomplete = KA._pages(orders_read, "orders")
    fills, ferr, fcomplete = KA._pages(fills_read, "fills")
    if oerr or ferr:
        return {"outcome": "WAIT", "retry_permitted": False,
                "why": "READ_FAILED", "errors": {"orders": oerr, "fills": ferr}}
    coid = row["client_order_id"]
    for raw in orders:
        o = KA.parse_order(raw)
        if o["client_order_id"] == coid:
            cum = o["fill_count"] or Decimal(0)
            return {"outcome": "ADOPT", "order_id": o["order_id"],
                    "venue_status": o["status"], "cum": cum,
                    "state": state_from_venue(o["status"], cum, row["live_qty"]),
                    "retry_permitted": False}
    started = epoch_s(row.get("submit_started_at")) or 0.0
    for raw in fills:
        f = KA.parse_fill(raw)
        t = epoch_s(f["created_time"])
        if f["ticker"] == row["ticker"] and f["action"] == row["action"] \
                and f["order_id"] not in mapped_order_ids \
                and (t is None or t >= started):
            return {"outcome": "UNATTRIBUTED", "retry_permitted": False,
                    "trade_id": f["trade_id"], "order_id": f["order_id"]}
    if now - started < grace_s:
        return {"outcome": "WAIT", "retry_permitted": False, "why": "GRACE"}
    if not (ocomplete and fcomplete):
        return {"outcome": "WAIT", "retry_permitted": False, "why": "READ_INCOMPLETE"}
    return {"outcome": "NOT_FOUND", "retry_permitted": True,
            "retry_client_order_id": coid}
