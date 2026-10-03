"""Shared helpers for the SHADOW intelligence layer. Pure; no I/O.

THE TWO RULES EVERY MODULE HERE FOLLOWS:

  * every output carries LABEL = 'SHADOW' and the disclosure below;
  * an unmeasured quantity is None with a NAMED REASON in the output's
    `unmeasured` map -- never 0. A measured zero (an empty book that really
    is empty, a position that really was held without management) is a
    number and carries its basis instead.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from zoneinfo import ZoneInfo

LABEL = "SHADOW"
AUTHORITY = "SHADOW_NO_AUTHORITY"
DISCLOSURE = (
    "SHADOW: computed, persisted and displayed only. No venue authority; "
    "places and cancels nothing; changes no live sizing, limit or threshold; "
    "never modifies a production probability. Unmeasured values are null "
    "with a named reason, never zero.")
PAPER_ACCOUNT = "paper_acct_main"
REPORTING_TZ = "America/New_York"

#: The existing $1,000 notional sleeve the shadow allocator and shadow sizing
#: distribute. A SHADOW basis only: nothing reads it to size an order.
SLEEVE_NOTIONAL_USD = 1000.0

BOOKS = ("PAPER", "ACTUAL")


def envelope(**extra) -> dict:
    out = {"label": LABEL, "authority": AUTHORITY, "disclosure": DISCLOSURE}
    out.update(extra)
    return out


class Out(dict):
    """A dict that records a reason whenever a value is set to None."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setdefault("unmeasured", {})

    def put(self, key, value, reason=None):
        self[key] = value
        if value is None:
            self["unmeasured"][key] = reason or "NOT_MEASURED"
        else:
            self["unmeasured"].pop(key, None)
        return value


def num(v):
    """float or None (NaN / inf / unparsable -> None)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def jload(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, _dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.timestamp()
    return num(v)


def ts(e):
    return _dt.datetime.fromtimestamp(float(e), _dt.timezone.utc)


def rnd(v, k=6):
    return None if v is None else round(float(v), k)


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()


def day_start(at: float, tz: str = REPORTING_TZ) -> float:
    z = ZoneInfo(tz)
    local = _dt.datetime.fromtimestamp(float(at), z)
    return local.replace(hour=0, minute=0, second=0,
                         microsecond=0).timestamp()


def median(xs):
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def mean(xs):
    s = [x for x in xs if x is not None]
    return (sum(s) / len(s)) if s else None


def stdev(xs):
    s = [x for x in xs if x is not None]
    if len(s) < 2:
        return None
    m = sum(s) / len(s)
    return math.sqrt(sum((x - m) ** 2 for x in s) / (len(s) - 1))


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def levels(side) -> list:
    """Venue book levels -> [(price, qty)] with positive qty, as stored in
    paper_book_observations ({"px": {"value": "0.52"}, "qty": "25"}).
    Unparsable levels are dropped."""
    out = []
    for lv in (jload(side) or []):
        if not isinstance(lv, dict):
            continue
        px = lv.get("px")
        px = px.get("value") if isinstance(px, dict) else px
        p, q = num(px), num(lv.get("qty"))
        if p is None or q is None or q <= 0 or not 0.0 < p < 1.0:
            continue
        out.append((p, q))
    return out


def book_view(bids, offers) -> dict:
    """Best bid / offer (LONG-side wire prices), spread, top-of-book depth."""
    b = sorted(levels(bids), key=lambda x: -x[0])
    o = sorted(levels(offers), key=lambda x: x[0])
    best_bid = b[0][0] if b else None
    best_offer = o[0][0] if o else None
    spread = (best_offer - best_bid) if (best_bid is not None
                                         and best_offer is not None) else None
    # displayed notional at the displayed wire price, both sides, top five
    # levels each. An empty book is a measured 0.0 only when a book WAS read.
    depth = sum(p * q for p, q in b[:5]) + sum(p * q for p, q in o[:5])
    return {"bids": b, "offers": o, "best_bid": best_bid,
            "best_offer": best_offer, "spread": spread,
            "mid": (None if spread is None else (best_bid + best_offer) / 2.0),
            "top5_depth_usd": depth}


def exit_walk(book: dict, holding_side: str, qty: float) -> dict:
    """What selling `qty` held contracts into the observed book returns, in
    OUR cost space. A LONG sells into the bids; a SHORT (holding the
    complement) exits against the LONG offers at (1 - offer). Quantity the
    displayed depth cannot absorb is reported, not valued."""
    if str(holding_side).upper() == "SHORT":
        ladder = [(1.0 - p, q) for p, q in book.get("offers") or []]
    else:
        ladder = list(book.get("bids") or [])
    left, value = float(qty), 0.0
    for p, q in ladder:
        take = min(left, q)
        value += take * p
        left -= take
        if left <= 1e-12:
            break
    return {"value_usd": value, "unabsorbed_qty": max(0.0, left)}


def cost_space(price, holding_side: str):
    """A LONG-side wire price -> our cost per contract for `holding_side`."""
    p = num(price)
    if p is None:
        return None
    return (1.0 - p) if str(holding_side).upper() == "SHORT" else p


def side_of_intent(intent) -> str:
    s = str(intent or "").upper()
    return "SHORT" if "SHORT" in s else "LONG"


def is_buy_intent(intent) -> bool:
    return "BUY" in str(intent or "").upper()
