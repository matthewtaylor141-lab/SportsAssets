"""Current held probability for Derek's existing two-model policy.

This helper acquires no socket, submits no orders, and changes no policy.
Its callbacks are the same scorer, blend and exact held-cache reader used by
BETTOR. Missing/stale data remains a refusal. Source clocks are not reset.
"""
from __future__ import annotations
import math
from datetime import datetime
from collections.abc import Mapping

VERSION = "XAVIER_CURRENT_BLEND_REFRESH_V1"


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def epoch(value):
    if isinstance(value, datetime):
        return value.timestamp() if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp() if dt.tzinfo is not None else None
        except ValueError:
            pass
    return number(value)


def probability(value):
    n = number(value)
    return n if n is not None and 0 <= n <= 1 else None


def source_fresh(source_at, *, now: float, limit_s: float) -> bool:
    s, n, lim = epoch(source_at), number(now), number(limit_s)
    return (s is not None and n is not None and lim is not None and lim > 0
            and 0 <= n - s <= lim)


async def refresh(conn, *, pos: dict, contract: dict | None,
                  stored: dict | None, model: dict, levels_buy: list,
                  at: float, max_age_s: float, score, blend, held_feed,
                  clock=None) -> dict:
    """Return a current blend or a named non-current result, never entry p.

    `clock` is evaluated AFTER the cache read. Fresh-at-start cannot become
    fresh-at-end through a captured/rounded age. The original max-age is kept.
    """
    now = lambda: float(clock()) if clock is not None else float(at)
    failed = {"ok": False, "stale": True, "p": None}
    c = dict(contract or {})
    if not c or c.get("us_market_slug") != pos.get("us_market_slug"):
        return dict(failed, why="ENTRY_CONTRACT_IDENTITY_NOT_ESTABLISHED")
    if not c.get("payout_event") or not isinstance(c.get("payout_is_complement"), bool):
        return dict(failed, why="ENTRY_PAYOUT_IDENTITY_NOT_ESTABLISHED")
    if not model.get("ok") or not levels_buy:
        return dict(failed, why="CURRENT_INTERNAL_MODEL_OR_BUY_BOOK_UNAVAILABLE")
    price = probability(levels_buy[0].get("price"))
    if price is None:
        return dict(failed, why="CURRENT_BUY_PRICE_INVALID")
    sc = score(model, price=price,
               payout_is_complement=bool(c["payout_is_complement"]))
    internal = probability(sc.get("p")) if isinstance(sc, Mapping) and sc.get("ok") else None
    if internal is None:
        return dict(failed, why="CURRENT_INTERNAL_SCORE_UNAVAILABLE")

    v = dict(stored or {})
    pin = probability(v.get("probability"))
    valid_stored = pin is not None and source_fresh(
        v.get("observed_at"), now=now(), limit_s=max_age_s)
    if valid_stored:
        # The calling query scopes all five contract dimensions, not slug only.
        source_at, received_at = epoch(v.get("observed_at")), epoch(v.get("received_at"))
        extra = {"valuation_id": v.get("id"), "valuation_store": "external_valuations"}
        label = "CURRENT_BLEND"
    else:
        try:
            cur = await held_feed(
                conn, pos=dict(pos, entry_event_key=c.get("event_key"),
                               entry_line=c.get("line") if c.get("market") in
                               ("spread", "total", "team_total") else None),
                payout_event=c["payout_event"],
                payout_is_complement=bool(c["payout_is_complement"]),
                at=now(), max_age_s=max_age_s)
        except Exception as exc:
            return dict(failed, why="HELD_CACHE_READ_FAILED", error=type(exc).__name__)
        if not isinstance(cur, Mapping) or not cur.get("ok"):
            return dict(failed, why="CURRENT_HELD_PROBABILITY_UNAVAILABLE",
                        feed_refusal=cur.get("reason") if isinstance(cur, Mapping) else "INVALID_HELD_READ")
        prov = dict(cur.get("provenance") or {})
        # change_ms is BETTOR's existing accepted change clock; never receipt.
        ms = prov.get("change_ms")
        if ms is None:
            ms = prov.get("source_change_ms")
        nms = number(ms)
        source_at = None if nms is None else nms / 1000.0
        pin = probability(cur.get("p"))
        if pin is None or not source_fresh(source_at, now=now(), limit_s=max_age_s):
            return dict(failed, why="HELD_PROBABILITY_EXPIRED_OR_CLOCK_INVALID",
                        feed_refusal="PROBABILITY_EVIDENCE_STALE")
        recv = number(prov.get("received_ms"))
        received_at = None if recv is None else recv / 1000.0
        extra = {"valuation_id": None, "valuation_store": None,
                 "feed": dict(prov, payout_event=c["payout_event"],
                              payout_is_complement=bool(c["payout_is_complement"]),
                              feed_event_id=cur.get("feed_event_id"),
                              market_key=cur.get("market_key"),
                              designation=cur.get("designation"),
                              identity_basis=cur.get("identity_basis")),
                 "entry_anchor_valuation_id": c.get("id")}
        label = "CURRENT_BLEND_HELD_CACHE"
    p = probability(blend(internal, pin))
    if p is None or not source_fresh(source_at, now=now(), limit_s=max_age_s):
        return dict(failed, why="BLEND_EXPIRED_OR_INVALID")
    return dict(extra, ok=True, p=p, source=label,
                p_internal=internal, p_pinnacle=pin, model_id=model.get("model_id"),
                pinnacle_at=source_at, pinnacle_received_at=received_at,
                pinnacle_age_s=now() - source_at, pinnacle_limit_s=max_age_s,
                stale=False, void_applied=False,
                policy="UNCHANGED_DEREK_TWO_MODEL_BLEND")


def evidence(measure: dict, *, at: float, limit_s: float, qty=None) -> dict:
    """Re-evaluate currency from source time, never a previously cached age.

    No source stamp -> not current. No finite [0,1] p -> unavailable. A
    claimed age of 1s cannot override a source timestamp 90s in the past.
    """
    m = measure or {}
    p = probability(m.get("p"))
    src = epoch(m.get("pinnacle_at"))
    recv = epoch(m.get("pinnacle_received_at"))
    if src is None:
        src = epoch(m.get("entry_pinnacle_at"))
        recv = epoch(m.get("entry_pinnacle_received_at"))
    supplied = number(m.get("pinnacle_limit_s"))
    limit = min(float(limit_s), supplied) if supplied is not None and supplied > 0 else float(limit_s)
    age = None if src is None else float(at) - src
    current = (p is not None and not m.get("stale")
               and source_fresh(src, now=at, limit_s=limit))
    state = ("PROBABILITY_UNAVAILABLE" if p is None else
             "FRESH_CURRENT_PROBABILITY" if current else
             "STALE_ENTRY_TIME_PROBABILITY")
    nqty = number(qty)
    value = p * nqty if p is not None and nqty is not None and nqty >= 0 else None
    # The operator-facing wording of the previous reader is kept (readers and
    # tests depend on it); the source-clock rule is stated beside it.
    if current:
        limitation = None
    elif p is None:
        limitation = (
            "no probability for this contract (neither a current reading "
            "nor the entry decision's); none is invented, nothing is ranked "
            "on one, and its absence alone never liquidates the position; "
            "its cost-recovery protection is unaffected")
    else:
        limitation = (
            "no fresh PinnAPI/Pinnacle probability for this contract within "
            "the %.0fs freshness limit at review time; the probability used "
            "is %s (source %s, age %s s), so the hold value derived from it "
            "is entry-time/stale and NOT a current expected value. No "
            "discretionary sale is ranked on it; the position is held with "
            "this limitation stated and its cost-recovery protection is "
            "unaffected. Currency is measured from the source timestamp at "
            "use; polling does not refresh the probability clock"
            % (limit, "the entry decision's" if m.get("source")
               == "ENTRY_TIME_MEASURE" else "an older reading",
               m.get("source"), None if age is None else round(age, 3)))
    if limitation and m.get("feed_refusal"):
        limitation += "; PinnAPI feed: %s" % m["feed_refusal"]
    return {"evidence_state": state, "probability": p,
            "probability_source": m.get("source"),
            "probability_source_at": src, "probability_received_at": recv,
            "probability_age_s": age, "probability_limit_s": limit,
            "probability_limitation": limitation,
            "current_hold_value_usd": value if current else None,
            "entry_time_hold_value_usd": value if p is not None and not current else None}
