"""Trader Mode: pure, position-keyed management projection. No authority.

All prices are held-side prices. A target touch is NOT a fill. A review that
was fresh when written can expire on screen. Unknown game state stays unknown.
This module does not rank actions, fit models, infer scores, or place orders.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone

SCHEMA = "bettor.trader.v1"
BOOK_LIMIT_S = 300.0
PROBABILITY_LIMIT_S = 30.0
GAME_LIMIT_S = 15.0                 # display freshness; not a trading gate
TERMINAL = {"EXPIRED", "CLOSED", "TERMINATED", "SETTLED", "RESOLVED"}
STANDING = {"RESTING", "PARTIALLY_FILLED", "CANCEL_PENDING", "PENDING_SIMULATION"}


def object_value(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v if isinstance(v, dict) else {}


def array_value(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return []
    return v if isinstance(v, list) else []


def num(v):
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def epoch(v):
    if isinstance(v, datetime):
        return v.timestamp() if v.tzinfo is not None else None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d.timestamp() if d.tzinfo is not None else None
        except ValueError:
            pass
    return num(v)


def iso(v):
    n = epoch(v)
    return None if n is None else datetime.fromtimestamp(n, timezone.utc).isoformat()


def fresh_at(at, now, limit):
    t = epoch(at)
    return t is not None and 0 <= float(now) - t <= limit


def price(v):
    n = num(v)
    return n if n is not None and 0 <= n <= 1 else None


def price_gap(direction, limit_price, quote, *, now):
    """Display distance only: no decision/trigger/execution authority."""
    direction = str(direction).upper()
    limit = price(limit_price)
    ref = price(quote.get("bid" if direction == "SELL" else "ask"))
    current = quote.get("current") is True and fresh_at(
        quote.get("at"), now, BOOK_LIMIT_S)
    if direction not in ("BUY", "SELL") or limit is None or ref is None:
        return {"distance_cents": None, "condition_met": None,
                "reason": "PRICE_OR_DIRECTION_UNAVAILABLE", "is_fill": False}
    delta = limit - ref if direction == "SELL" else ref - limit
    return {"distance_cents": round(max(0.0, delta) * 100, 6),
            "signed_distance_cents": round(delta * 100, 6),
            "condition_met": delta <= 1e-12 if current else None,
            "reference_price": ref, "limit_price": limit,
            "reference_side": "BID" if direction == "SELL" else "ASK",
            "condition": "BID_AT_LEAST_LIMIT" if direction == "SELL" else "ASK_AT_MOST_LIMIT",
            "reason": None if current else "BOOK_NOT_CURRENT",
            "is_fill": False,
            "disclosure": "Touch condition only. Depth, fees, queue and all gates still apply."}


def normalize_game(raw, *, expected_event_id, now):
    """A provider adapter must bind canonical event identity before this read.

    An event time alone never proves game completion or a running clock.
    Canonical normalizer accepts only explicit fields, not titles/odds-derived
    guesses. The UI may interpolate a confirmed running clock within 15s,
    labels it estimated, freezes at expiry/zero, and never rolls the period.
    """
    r = object_value(raw)
    absent = {"status": "UNAVAILABLE", "why": "LIVE_SCORE_SOURCE_NOT_CONNECTED",
              "event_id": expected_event_id, "clock_running": False}
    if not r:
        return absent
    if r.get("event_id") != expected_event_id or r.get("identity_verified") is not True:
        return dict(absent, why="SCORE_EVENT_IDENTITY_UNPROVEN")
    at = epoch(r.get("source_at"))
    if not r.get("source") or at is None:
        return dict(absent, why="SCORE_PROVENANCE_ABSENT")
    current = fresh_at(at, now, GAME_LIMIT_S)
    def bounded_int(k, low, high):
        n = num(r.get(k))
        return int(n) if n is not None and n.is_integer() and low <= n <= high else None
    bases = r.get("bases")
    valid_bases = (isinstance(bases, list) and len(bases) == 3
                   and all(isinstance(b, bool) for b in bases))
    running = r.get("clock_running") is True
    clock = num(r.get("clock_seconds"))
    if clock is None or clock < 0:
        clock, running = None, False
    return {"status": "CURRENT" if current else "STALE", "why": None if current else "SCORE_SOURCE_STALE",
            "event_id": expected_event_id, "source": str(r["source"]),
            "source_at": at, "source_age_s": float(now) - at,
            "game_status": str(r.get("game_status") or "UNKNOWN"),
            "home": str(r.get("home") or "Home"), "away": str(r.get("away") or "Away"),
            "home_score": bounded_int("home_score", 0, 1000),
            "away_score": bounded_int("away_score", 0, 1000),
            "period": str(r.get("period") or ""),
            "inning": bounded_int("inning", 1, 99),
            "inning_half": r.get("inning_half") if r.get("inning_half") in ("TOP", "BOTTOM") else None,
            "outs": bounded_int("outs", 0, 3), "balls": bounded_int("balls", 0, 4),
            "strikes": bounded_int("strikes", 0, 3),
            "bases": list(bases) if valid_bases else None,
            "clock_seconds": clock, "clock_running": running and current,
            "clock_direction": r.get("clock_direction") if r.get("clock_direction") in ("DOWN", "UP") else None,
            "clock_estimated": running and current,
            "down": bounded_int("down", 1, 4), "distance": bounded_int("distance", 0, 100),
            "possession": r.get("possession"),
            "last_play": str(r.get("last_play") or ""),
            "expires_at": at + GAME_LIMIT_S,
            "authority": "DISPLAY_ONLY_NOT_A_PRICE_OR_SETTLEMENT_SOURCE"}


def review_for_position(reviews, position_id, *, now):
    """Exactly one latest review for the exact position. No nested recursion."""
    rows = [r for r in reviews if r.get("position_id") == position_id
            and epoch(r.get("reviewed_at")) is not None
            and epoch(r.get("reviewed_at")) <= now]
    if not rows:
        return None
    return max(rows, key=lambda r: (epoch(r.get("reviewed_at")), str(r.get("review_id") or "")))


def packet_state(review, quote, *, now, orders=None, position_qty=None):
    r = object_value(review)
    sel = object_value(r.get("selection"))
    packet = object_value(sel.get("management_packet"))
    gate = object_value(packet.get("gate"))
    measure = object_value(r.get("measure"))
    missing = list(gate.get("missing") or [])
    p_at = measure.get("probability_source_at")
    if p_at is None:
        p_at = measure.get("pinnacle_at")
    p = price(measure.get("probability") if measure.get("probability") is not None else measure.get("p"))
    p_current = (p is not None and measure.get("evidence_state") == "FRESH_CURRENT_PROBABILITY"
                 and not measure.get("stale")
                 and fresh_at(p_at, now, PROBABILITY_LIMIT_S))
    if not p_current:
        missing.append("NO_FRESH_PROBABILITY")
    if quote.get("current") is not True or not fresh_at(quote.get("at"), now, BOOK_LIMIT_S):
        missing.append("NO_CURRENT_EXECUTABLE_BOOK")
    depth = num(quote.get("depth_at_bid"))
    if depth is None or depth <= 0:
        missing.append("NO_EXECUTABLE_EXIT_DEPTH")
    # A previously complete review cannot make a cancelled/expired/mismatched
    # protection order look current. This is display validity, not a new gate.
    if orders is not None:
        potential = [o for o in orders if o.get("role") == "STANDING_PROTECTION"
                     and o.get("state") in STANDING]
        valid = len(potential) == 1
        if valid:
            o = potential[0]
            expiry = epoch(o.get("expires_at"))
            remaining = num(o.get("remaining_qty"))
            q = num(position_qty)
            valid = (o.get("state") in ("RESTING", "PARTIALLY_FILLED")
                     and (expiry is None or now < expiry)
                     and remaining is not None and q is not None
                     and abs(remaining - q) <= 1e-6
                     and o.get("order_id") == object_value(packet.get("protection")).get("order_id"))
        if not valid:
            missing.append("NO_VALID_ACTIVE_PROTECTION")
    if not r:
        missing.append("NO_POSITION_REVIEW")
    complete = gate.get("complete") is True and not missing
    return {"complete": complete, "missing": sorted(set(missing)),
            "complete_at_recording": gate.get("complete"),
            "probability": p, "probability_at": epoch(p_at),
            "probability_current": p_current,
            "probability_age_s": None if epoch(p_at) is None else now - epoch(p_at),
            "probability_limit_s": PROBABILITY_LIMIT_S,
            "review_id": r.get("review_id"), "reviewed_at": epoch(r.get("reviewed_at")),
            "recorded_recommendation": r.get("recommendation"),
            "current_recommendation": r.get("recommendation") if complete else None,
            "state": "CURRENT" if complete else "WAITING_FOR_EVIDENCE",
            "scope": "DISPLAY_VALIDITY; EXISTING_XAVIER_GATE_REMAINS_AUTHORITY"}


def build_snapshot(rows, *, now, total_count=None, source_sha=None):
    """Build from canonical position rows prepared by the read-only adapter."""
    positions = []
    seen = set()
    for row in rows:
        r = dict(row)
        pid = str(r.get("position_id") or "")
        if not pid or pid in seen:
            raise ValueError("missing or duplicate canonical position identity")
        seen.add(pid)
        quote = dict(r.get("quote") or {})
        quote["current"] = (quote.get("current") is True
                             and fresh_at(quote.get("at"), now, BOOK_LIMIT_S))
        review = object_value(r.get("review"))
        orders = []
        for o in r.get("orders") or []:
            o = dict(o)
            # Adapter binds these to exact account/group/slug/holding side.
            if o.get("position_id") != pid:
                raise ValueError("order bound to another position")
            o["gap"] = price_gap(o.get("direction"), o.get("limit_price"), quote, now=now)
            o["is_standing"] = o.get("state") in STANDING
            o["execution_environment"] = "PAPER_SIMULATED"
            orders.append(o)
        pk = packet_state(review, quote, now=now, orders=orders, position_qty=r.get("qty"))
        state = "SETTLEMENT_PENDING" if str(quote.get("market_state") or "").upper() in TERMINAL else "ACTIVE"
        # Keep every open position in the response, including all unavailable rows.
        r.update(quote=quote, packet=pk, orders=orders, state=state,
                 game=normalize_game(r.get("game"), expected_event_id=r.get("event_id"), now=now))
        r["proposal"] = None
        prot = object_value(object_value(review.get("standing")).get("protective_price"))
        has_sale = any(o.get("direction") == "SELL" and o["is_standing"] for o in orders)
        if not has_sale and prot.get("ok") and price(prot.get("price")) is not None:
            r["proposal"] = {"kind": "RECORDED_PROPOSAL_NOT_AN_ORDER",
                             "limit_price": price(prot["price"]), "direction": "SELL",
                             "recorded_at": epoch(review.get("reviewed_at")),
                             "current": pk["complete"],
                             "gap": price_gap("SELL", prot["price"], quote, now=now)}
        q, basis = num(r.get("qty")), num(r.get("cost_basis_usd"))
        bid = price(quote.get("bid"))
        r["unrealized_usd"] = (round(q * bid - basis, 6)
                                if q is not None and q >= 0 and bid is not None and basis is not None
                                and quote["current"] else None)
        r["unrealized_basis"] = "HELD_SIDE_TOP_OF_BOOK_BEFORE_EXIT_FEES; NOT_FULL_SIZE_LIQUIDATION"
        r.pop("raw", None)
        positions.append(r)
    active = [p for p in positions if p["state"] == "ACTIVE"]
    n = len(active)
    complete = sum(p["packet"]["complete"] for p in active)
    current = sum(p["quote"]["current"] for p in active)
    count = len(positions) if total_count is None else int(total_count)
    if count < len(positions):
        raise ValueError("total count smaller than returned population")
    body = {"schema": SCHEMA, "mode": "PAPER", "source": "NATIVE_LEDGER",
            "authority": "READ_ONLY_SMALL_LIVE_SHADOW", "source_sha": source_sha,
            "snapshot_at": float(now), "positions": positions,
            "total_position_count": count, "returned_position_count": len(positions),
            "truncated": count > len(positions),
            "counts": {"all_open": count, "returned": len(positions),
                       "active": n, "settlement_pending": len(positions) - n,
                       "current_marks": current, "complete_packets": complete,
                       "incomplete_packets": n - complete,
                       "mark_rate": current / n if n else None,
                       "packet_rate": complete / n if n else None,
                       "standing_orders": sum(o["is_standing"] for p in positions for o in p["orders"]),
                       "rates_scope": "RETURNED_ACTIVE_POSITIONS; ALL_EXCLUSIONS_VISIBLE"},
            "freshness_limits": {"book_s": BOOK_LIMIT_S, "probability_s": PROBABILITY_LIMIT_S,
                                 "game_display_s": GAME_LIMIT_S},
            "execution_authority": False}
    content = dict(body, snapshot_at=None)
    body["snapshot_id"] = hashlib.sha256(json.dumps(content, sort_keys=True,
                                        default=str).encode()).hexdigest()[:24]
    return body
