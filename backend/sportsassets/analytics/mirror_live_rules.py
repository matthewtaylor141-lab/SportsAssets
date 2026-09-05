"""Live position mirroring, phase P1: the RULES, kept pure (owner order
2026-09-02, "maximum effort and certainty").

analytics/mirror is the arithmetic the shadow already logs: his net,
the ratio, the target, the one order plan() would place. This module
is everything the LIVE reconciler decides on top of that plan, written
so that every rule is a function over facts already read -- no venue,
no database, no clock of its own -- and every refusal is a name from
the census. The worker (workers/mirror_live) reads, calls these, and
writes; nothing here can place an order or touch a row.

What lives here, in the order the tick uses it:

  * the caps and timers, with their spec defaults: every cap
    overridable from the environment DOWNWARD only (capped_env, with a
    positive floor where a zero would mean UNBOUNDED downstream or a
    tick that cannot even cancel), the two WAITS before a more
    aggressive action UPWARD only (min_wait_env); and every argument
    that shadows one of them in a function below can only tighten it
    the same way (a caller's cap is min'd, a caller's wait max'd)
  * mirror_target()     the ratio anchored to the whale's per-fill
                        clip AT BOOK OPEN, capped at the mark, long-only:
                        a negative raw target is 0, never a short; an
                        unreadable position, clip or cap is NO PLAN,
                        never a target of 0 (which would be a flatten)
  * admission()         the first named refusal for a NEW book, and the
                        starred subset re-checked on every INCREASE
  * buy_price/sell_price the cent that goes on the wire, from the
                        UNROUNDED facts: floored for a BUY (never above
                        him, never above the bid), ceiled for a SELL
                        (never under his equivalent); buy_wire/sell_wire
                        are the cent arithmetic they share
  * room_scale()        the quantity the sleeve's room allows
  * keep_or_replace()   what to do with the order already resting
  * take_allowed()      the bounded take: only after the wait AND only
                        with the book at or through his level;
                        take_arms() the one rejection that arms it, in
                        the two shapes the venue refuses a post-only
                        rest (an HTTP 400, or a 200 carrying a
                        REJECTED order and a REJECTED execution)
  * select_flatten()    paired-out (rest at 1 - q, never marketed) vs
                        vanished (the one path that accepts slippage,
                        and only with every confirmation)
  * book_buy/book_sell  the ledger arithmetic per booked fill
  * drift_rule()        derived-vs-snapshot: increases refused on drift
                        or a stale read, reductions from the smaller;
                        drift_net_rule() the same disagreement read on
                        the NET of both tokens, so a pair he merged
                        on-chain is not a lifelong drift lock-out
  * episode_close()     cashed_out vs cancelled vs not yet, with
                        episode_close_reason() naming the "not yet"
  * p2_verdict()        the numbered P1 -> P2 gate, read from numbers,
                        never from memory; capture_short() the reported,
                        ungated clause (5)

Fail closed throughout, ON EVERY INPUT. A fact that was not read
(None), or that arrived as something that is not a number -- a bool, a
string (even a numeric one), NaN, an infinity -- is the named refusal:
never a guess, never an order, never an unbounded target, never a
corrupted ledger, and never a raise (the tick must go on to cancel).
One parser, _num, decides what a number is; everything on the money
path reads through it or through _count / _size on top of it. The
review that shaped these rules is the P1 panel synthesis (critics
C14-C16: never IOC-first, slippage only when he has LEFT the market,
the rest TTL separate from the copy lane's) and two adversarial
reviews of this module.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from . import mirror as mi
from .mirror import MARKET_NET_CAP_USD, MIN_MOVE_FRAC, RATIO_MAX, Plan
from .proof import MIN_PROOF_CLUSTERS, Z95
from .roster_rules import MIRROR_ANCHOR_CLIP_USD, MIN_N_DEMOTE, MIN_N_PROMOTE

# P1 is long-only. The standing row's raw.preview.intent and every
# submit_fok call carry this one constant; BUY_SHORT stays behind the
# executor's short gate until P2 (the spec's "P2's door").
ORDER_INTENT = "ORDER_INTENT_BUY_LONG"
BUY, SELL = "BUY_LONG", "SELL_LONG"
# The venue's own names for a refused order, spelled exactly as the
# SDK's Literal types spell them (polymarket_us/types/orders.py,
# OrderState and ExecutionType). Restated here because this module
# imports no venue adapter (the purity pin), and read by take_arms:
# the second shape of a post-only refusal is a 200 whose order comes
# back in this state with an execution of this type (to-a-tee program
# Phase 7, owner order 2026-09-02 "I want us to match everything ...
# mirror the whales to a tee"; the 1-share probe rungs read both
# shapes from the venue before any default rides on them). The
# adapter's contract is to carry them into raw verbatim; a spelling
# that differs by a character is not the venue's and never arms.
ORDER_STATE_REJECTED = "ORDER_STATE_REJECTED"
EXECUTION_TYPE_REJECTED = "EXECUTION_TYPE_REJECTED"


# --------------------------------------------------------------- readings

def _num(v: Any) -> float | None:
    """The one reading of a number on the money path: a finite int,
    float or Decimal, as a float. None, a bool, a string or bytes
    (even a numeric string -- a string is a reading nobody parsed
    upstream), NaN, an infinity and anything float() refuses are None:
    not a number, so a named refusal wherever they land, never a
    raise."""
    if v is None or isinstance(v, (bool, str, bytes)):
        return None
    try:
        f = float(v)
    except Exception:       # noqa: BLE001 -- an exotic __float__ may raise anything; not a number
        return None
    return f if math.isfinite(f) else None


def _count(v: Any) -> int | None:
    """A whole count at or above zero (books, shares to size), or
    None: a fraction, a negative, and everything _num refuses are not
    counts. A whole-valued float or Decimal (4.0) is a count."""
    f = _num(v)
    if f is None or f < 0 or f != math.floor(f):
        return None
    return int(f)


def _int(v: Any) -> int | None:
    """A count that ARRIVED as an int at or above zero, not a bool: the
    venue's open-order count is counted, never computed, so a float
    0.0, a Decimal('0') or a negative zero is a count nobody made."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _size(v: Any) -> float | None:
    """A share count from fills or a snapshot: a finite number at or
    above zero, or None when it was not read. A negative size is not
    a size."""
    f = _num(v)
    return f if f is not None and f >= 0 else None


def _env_float(name: str) -> float | None:
    """The environment value as a finite float, or None when it is
    absent, blank, unparseable or non-finite. The environment holds
    strings, so this is the one place a string is parsed. A name that
    is not a string is not a variable (os.environ raises on it), so it
    reads as absent rather than propagating (rules review addendum
    §11, owner order 2026-09-02 "go for it, let's get this working")."""
    if not isinstance(name, str):
        return None
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def capped_env(name: str, default: float, floor: float = 0.0) -> float:
    """A cap read from the environment that can only TIGHTEN.

    Every cap below is a bound on money or count. An operator can lower
    any of them without a deploy (the 1-share venue probe runs with
    MIRROR_MAX_LIVE_BOOKS=1 and a $25 net cap); nobody can raise one
    from a shell, because a raised cap is a code change that wants a
    review. The override is honoured within [floor, default]. The
    floor is 0 for a count or a room where zero means NONE (no books,
    no room) and is the most closed setting there is. It is POSITIVE
    where zero would not be closed at all: a bound whose zero means
    UNBOUNDED downstream (MIRROR_NET_CAP_USD, which mi.target_shares
    applies only while `cap_usd > 0`), the order-ops budget (a SAFE
    or exits-only tick must still be able to CANCEL), the rest TTL
    (a zero would re-place every tick). An override under the floor
    lands on the floor, never past it. Unreadable or non-finite values
    fall back to the default.
    """
    d, fl = _num(default), _num(floor)
    fl = 0.0 if fl is None else fl
    if d is None:
        return fl               # a cap with no readable default IS the floor: the most closed
    v = _env_float(name)
    if v is None:
        return d
    return min(d, max(fl, v))


def min_wait_env(name: str, default: float) -> float:
    """A WAIT read from the environment that can only LENGTHEN.

    The mirror image of capped_env, for the two timers that are
    patience before a MORE AGGRESSIVE action (the bounded take, the
    slippage flatten). A longer wait is more rest-first, more maker,
    so an operator may raise one from a shell; a shorter wait is the
    aggressive change, a code change that wants a review. Unreadable,
    non-finite or negative values fall back to the default: 'inf',
    'Infinity' and '1e400' (which parses to inf) are NOT "never" --
    they are the default. The only spelling of "never take / never
    slip" is a large FINITE wait, e.g. MIRROR_TAKE_AFTER_S=1e9, which
    take_allowed honours as max(wait_s, constant).
    """
    d = _num(default)
    if d is None or d < 0:
        return math.inf         # a wait with no readable default never elapses: no take, no slippage
    v = _env_float(name)
    if v is None or v < 0:
        return d
    return max(d, v)


# Per-market net exposure at the mark. mi.MARKET_NET_CAP_USD is the
# number; this is the operator's downward handle on it, passed to
# mi.target_shares as cap_usd so the target is SCALED at the mark.
# THE FLOOR IS POSITIVE (review finding): mi.target_shares caps only
# while `cap_usd > 0`, so a zero here would be no cap at all -- the
# one env value that would have RAISED exposure. One dollar at the
# mark is the smallest cap that is still a cap (at most 1/mark
# shares); an override of 0 or less lands on it. An operator who wants
# NO exposure has the switch (PMUS_MIRROR=off|exits), not a zero cap;
# and mirror_target refuses a cap at or under zero by the name
# `net_cap_zero` before mi.target_shares ever sees it, should one
# reach it by another road.
MIRROR_NET_CAP_FLOOR_USD = 1.0
MIRROR_NET_CAP_USD = capped_env("MIRROR_NET_CAP_USD", MARKET_NET_CAP_USD,
                                floor=MIRROR_NET_CAP_FLOOR_USD)
# Blast radius: at most this many books live, this many opened per day,
# so the worst case is books x net cap (5 x $250 = $1,250).
MIRROR_MAX_LIVE_BOOKS = int(capped_env("MIRROR_MAX_LIVE_BOOKS", 5))
MIRROR_MAX_BOOKS_PER_DAY = int(capped_env("MIRROR_MAX_BOOKS_PER_DAY", 5))
# Gross BUY dollars per rolling day on top of the sleeve's own caps.
MIRROR_DAY_USD = capped_env("MIRROR_DAY_USD", 1250.0)
# Mirror-own loss stop: 24 h realized including partial sales, which the
# global breaker cannot see (it reads terminal rows only). $250 -> $1,000
# by owner decision (2026-09-05 23:0xZ, asked as a multiple choice with
# the program's own numbers beside each option: $250 is 20% of a
# $1,250 day and trips on close to half of ordinary days at the copy
# lane's measured daily ROI spread; he chose the program document's
# figure). Still downward-only from the environment; still realized
# only; still re-armed by hand (render-ops sql mirror-rearm).
MIRROR_LOSS_STOP_USD = capped_env("MIRROR_LOSS_STOP_USD", 1000.0)
# Venue writes per tick, replaces per book per hour: the venue 429s a
# board walk above ~3 req/s and the copy lane shares the budget. The
# ops budget floors at ONE (review finding): a SAFE or exits-only tick
# is cancel-only, and a tick that may not write at all cannot cancel.
MIRROR_MAX_ORDER_OPS_PER_TICK = int(capped_env("MIRROR_MAX_ORDER_OPS_PER_TICK", 6, floor=1))
MIRROR_MAX_REPLACES_PER_HOUR = int(capped_env("MIRROR_MAX_REPLACES_PER_HOUR", 12))
# A resting order's life. NOT the copy lane's REST_BID_TTL_S (clamped to
# 15 s and doubling as mirror_exit's in-flight wait; critic C14): the
# mirror is a maker and waits. Floors at 30 s (review finding): an env
# of 0 would cancel and re-place every tick, a taker's churn on a
# maker's book and the replace budget gone in an hour.
MIRROR_REST_TTL_S = capped_env("MIRROR_REST_TTL_S", 600.0, floor=30.0)
# The bounded take: a rest must have stood unfilled this long (or a
# post-only rejection this old) before ONE IOC at the same wire is
# allowed, and even then only with the book at or through his level.
# A WAIT, NOT A CAP (review finding): the environment may only
# LENGTHEN it (min_wait_env, max(default, env)), and take_allowed
# itself never waits LESS than this constant whatever wait_s a caller
# passes. A longer wait is more rest-first; a shorter one is the
# aggressive change and wants a review. The price rule, not the timer,
# keeps the take at or under him (critic C15). The caps above stay
# downward-only.
MIRROR_TAKE_AFTER_S = min_wait_env("MIRROR_TAKE_AFTER_S", 120.0)
# A vanished whale's flatten rests at his equivalent this long before
# the slippage path (critic C16). A wait like the take's: the
# environment may only lengthen it.
MIRROR_FLATTEN_REST_S = min_wait_env("MIRROR_FLATTEN_REST_S", 300.0)
# A book flat at target 0 on a live market closes after this long.
MIRROR_FLAT_CLOSE_S = capped_env("MIRROR_FLAT_CLOSE_S", 3600.0)
# Derived-vs-snapshot disagreement above this refuses increases.
MIRROR_DRIFT_MAX = capped_env("MIRROR_DRIFT_MAX", 0.05)
# A frozen book degrades the heartbeat after this long and is named on
# the gates endpoint after this many ticks (never as an `error` row).
MIRROR_FROZEN_ALERT_S = capped_env("MIRROR_FROZEN_ALERT_S", 600.0)
MIRROR_FROZEN_NAME_TICKS = int(capped_env("MIRROR_FROZEN_NAME_TICKS", 3))
# Market families a book may open on (copy_sports.market_type_of).
# P1 opened on moneylines alone and refused derivatives at admission
# by the name `family` (program decision 19: totals, spreads and props
# were ~3% of his mapped dollars, to be admitted per family behind a
# shadow week). OWNER DECISION 2026-09-05 (asked as a multiple choice
# beside that recommendation): "everything he trades". So every sports
# family the type function names is admitted; `crypto` is another
# lane's venue and `unknown` (and blank) is the fail-closed reading of
# a slug the grammar could not type -- neither is a book. The per-side
# referee, the closed/resolved reads, the mapping quarantine and the
# side band still stand in front of every one of these; this set only
# stops refusing a derivative for BEING one. Its residuals (decision
# 17's resolution-rule mismatches, decision 19's unmeasured fill
# behaviour by family) are the owner's, accepted with the choice.
MIRROR_FAMILIES = frozenset({"moneyline", "spread", "total", "prop", "btts", "exact_score"})
# The flat tolerance in shares, ONE number for the ledger and the
# bookings (addendum section 9): a fractional venue fill can leave a
# ledger of 1e-8 that is not zero, and a book "held" by dust would never
# close; under this a ledger is flat, and a delta under it is dust that
# never books (a 1e-12 sale would otherwise book 1e-12 shares for $0).
# mirror_orders.qty is an integer, so real fills are never this small.
FLAT_TOL_SHARES = 1e-6

# The P2 gate's own thresholds (spec section 6). The 30-game floor and
# the 95% standard are imported above, never restated.
P2_MAKER_SHARE_MIN = 0.5
P2_TAKE_SLIP_MAX = 0.01
P2_FROZEN_TICK_FRAC_MAX = 0.01
P2_CAPTURE_MIN = 0.5           # reported beside the verdict (capture_short), not gated
P2_INTEGRITY_COUNTERS = ("frozen_unresolved", "wrong_sign_trip", "order_lost", "overfill",
                         "reaper_touched_mirror", "book_settle_disagree", "shadow_live_disagree")


# ---------------------------------------------------------------- target

def mirror_target(ratio: float | None, net: float | None, mark: float | None,
                  per_fill_usd: float | None,
                  cap_usd: float = MIRROR_NET_CAP_USD) -> dict[str, Any]:
    """Our target in long-token shares for this book, or the reason
    there is no plan.

    ratio_eff = shadow ratio x min(1, per_fill_usd / MIRROR_ANCHOR_CLIP_USD):
    the $50 anchor sets the ratio, a smaller per-fill clip scales it
    down, and nothing scales it UP in P1 (the anchor moves only by the
    P4 promotion). THE ANCHOR IS NOT THE PER-FILL LANE'S CLIP: that clip
    rose to $250 on 2026-09-04 and the mirror must not resize as a side
    effect of it -- the shadow's evidence was gathered at the anchor and
    the mirror moves when its own gate says so.

    THE CLIP SCALING APPLIES AT BOOK OPEN ONLY. A book's ratio is fixed
    at open (addendum section 7, mirror_books.ratio): the worker passes
    the live per-fill clip when it OPENS a book and stores the
    ratio_eff this returns; on every later tick it passes that stored
    ratio with per_fill_usd=MIRROR_ANCHOR_CLIP_USD (scale 1), never the
    live clip. A clip cut after open stops INCREASES -- admission(
    increase=True) names it `clip_zero` -- and never re-rates the book;
    a clip cut to 0 is not a flatten.

    Every refusal below is NO PLAN (target None), never a target of 0:
    a target of 0 is a FLATTEN to mi.plan (a SELL of the whole book),
    and none of these facts says "sell". The worker cancels what rests
    (keep_or_replace names a None plan) and HOLDS the book. Every
    number is read by the _num rule (finite, not a bool, not a
    string); mi.target_shares, which reads `net or 0.0` and caps only
    while `cap_usd > 0`, is never reached by anything else.

    A CALLER CAN ONLY TIGHTEN: cap_usd is min(cap_usd, the module's
    MIRROR_NET_CAP_USD) and the ratio is min(ratio, mi.RATIO_MAX), so
    no argument raises exposure past the constants the environment
    can only lower (review finding: cap_usd=1e9 read uncapped).

      `net_cap_zero`     cap_usd at or under 0, non-finite or
                         unreadable: NO exposure allowed. Refused HERE,
                         before mi.target_shares -- there a zero cap
                         is no cap
      `no_ratio`         no ratio, or one at or under 0
      `no_mark`          no mark ON THE LADDER, 0.01 <= mark <= 0.99: a
                         subnormal mark (1e-320) would loosen the cap
                         to cap/mark shares
      `no_position`      his net not read: None, a bool, a string,
                         NaN or an infinity (mi.target_shares would
                         read None/False/'' as 0 -> a flatten, and an
                         infinity as a cap-sized BUY)
      `clip_unreadable`  per_fill_usd None, unparseable or non-finite
      `clip_zero`        a clip at or under 0: our size for that whale
                         is zero, so the book neither opens nor grows

    A negative raw target is his short of our long token: target 0,
    named `short_side_refused`, P2's door -- the one refusal that IS a
    target, since a long book against his short holds nothing. A net
    of exactly 0 is a reading (he holds nothing): target 0, a flatten
    under select_flatten's confirmations.
    """
    out: dict[str, Any] = {"target": None, "raw": 0.0, "capped": False, "ratio_eff": None,
                           "refusal": None, "intent": ORDER_INTENT}
    cap = _num(cap_usd)
    if cap is None or cap <= 0:
        out["refusal"] = "net_cap_zero"
        return out
    cap = min(cap, float(MIRROR_NET_CAP_USD))
    rt = _num(ratio)
    if rt is None or rt <= 0:
        out["refusal"] = "no_ratio"
        return out
    rt = min(rt, float(RATIO_MAX))
    m = _num(mark)
    if m is None or not (0.01 <= m <= 0.99):
        out["refusal"] = "no_mark"
        return out
    n = _num(net)
    if n is None:
        out["refusal"] = "no_position"
        return out
    clip = _num(per_fill_usd)
    if clip is None:
        out["refusal"] = "clip_unreadable"
        return out
    scale = min(1.0, max(0.0, clip) / float(MIRROR_ANCHOR_CLIP_USD))
    ratio_eff = rt * scale
    out["ratio_eff"] = round(ratio_eff, 6)
    if ratio_eff <= 0:
        out["refusal"] = "clip_zero"
        return out
    t = mi.target_shares(ratio_eff, n, m, allow_short=False, cap_usd=cap)
    out.update({"target": int(t["target"]), "raw": t["raw"], "capped": bool(t["capped"])})
    if t["why"] == "short side not admitted":
        out["refusal"] = "short_side_refused"
    return out


# ------------------------------------------------------------- admission

@dataclass
class AdmissionFacts:
    """Facts the worker has already READ for one candidate book. Every
    default is the fail-closed value, so a fact never read refuses by
    its name rather than admitting by omission."""
    increases_ok: bool | None = None
    increases_refusal: str = "mode_env_off"     # mode_env_off|mode_db_off|mode_db_unreadable|whales_unreadable
    per_fill_usd: float | None = None
    family: str | None = None
    per_side: bool | None = None                # the market keys its sides by a per-side identifier
    market_closed: bool | None = None
    market_resolved: bool | None = None
    game_too_far_out: bool | None = None
    mapping_ok: bool | None = None
    mapping_why: str | None = None
    edge_ok: bool | None = None
    edge_why: str | None = None
    cell_ok: bool | None = None
    cell_clause: str | None = None
    legacy_row: bool | None = None
    slug_recent_copy: bool | None = None
    underdog_coholds: bool | None = None
    venue_net: float | None = None              # signed net on the slug; absent from a full walk = 0
    kalshi_claimed: bool | None = None
    side_band_hit: bool | None = None
    snap_fresh: bool | None = None
    drift: float | None = None
    books_live: int | None = None
    opened_today: int | None = None
    first_fill_ok: bool | None = None
    # A per-MARKET venue read of BOTH tokens of this condition, stamped
    # fresh AND complete for that market (Phase 1 of the to-a-tee
    # program, owner order 2026-09-02 "I want us to match everything
    # ... mirror the whales to a tee"). It stands beside snap_fresh,
    # the whole-book walk's flag, because RN1's walk is truncated on
    # every probe (tee/lifecycle.refute.market.md: a mapped row with
    # one token `n/a` every time), so `snap_fresh` is never True for
    # him and P1 as specified opened no RN1 book. Appended LAST so a
    # positional construction anywhere keeps its meaning; None (not
    # read) is the fail-closed default, and nothing but the bool True
    # admits.
    snap_market_fresh: bool | None = None


def _why(v: Any) -> str:
    return str(v) if isinstance(v, str) and v else "unreadable"


def admission(f: AdmissionFacts, increase: bool = False) -> str | None:
    """The first named refusal, or None when the book may open.

    `increase=True` is the re-check on every INCREASE of a live book:
    the starred clauses of the spec (mode, clip, mapping, edge, cell),
    so a whale who loses his edge or his mapping admission mid-book
    stops adding but keeps reducing. Order is the spec's, so a
    candidate that fails twice is named by the earlier gate.

    Every flag admits only when it `is` the admitting value (True or
    False by clause), every number reads by the _num / _count rule:
    a fact that is None, a bool where a number belongs, a string, NaN
    or an infinity refuses by the clause's name and never raises.
    `per_side_unsupported`: P1 does not trade markets that key their
    sides by a per-side identifier (addendum section 7); a new book
    needs `per_side is False`, read, not assumed. Something that is
    not an AdmissionFacts at all is `facts_unreadable`.
    """
    if not isinstance(f, AdmissionFacts):
        return "facts_unreadable"
    if f.increases_ok is not True:
        return f.increases_refusal if isinstance(f.increases_refusal, str) and f.increases_refusal else "mode_env_off"
    clip = _num(f.per_fill_usd)
    if clip is None:
        return "clip_unreadable"
    if clip <= 0:
        return "clip_zero"
    if not increase:
        if not isinstance(f.family, str) or f.family not in MIRROR_FAMILIES:
            return "family"
        if f.per_side is not False:
            return "per_side_unsupported"
        if f.market_closed is not False or f.market_resolved is not False:
            return "market_closed"
        if f.game_too_far_out is not False:
            return "game_too_far_out"
    if f.mapping_ok is not True:
        return "mapping:" + _why(f.mapping_why)
    if f.edge_ok is not True:
        return "edge_gate:" + _why(f.edge_why)
    if f.cell_ok is not True:
        return "cell_gate_" + _why(f.cell_clause)
    if increase:
        return None
    if f.legacy_row is not False:
        return "legacy_row"
    if f.slug_recent_copy is not False:
        return "slug_recent_copy"
    if f.underdog_coholds is not False:
        return "underdog_coholds"
    vn = _num(f.venue_net)
    if vn is None:
        return "positions_unreadable"
    if vn != 0.0:
        return "venue_already_holds"
    if f.kalshi_claimed is not False:
        return "kalshi_claimed"
    if f.side_band_hit is not False:
        return "side_band"
    # either sight of him is a sight: the whole-book walk read fresh
    # and complete, OR the per-market read of both tokens of this
    # condition read fresh and complete (Phase 1). The name stays
    # `snapshot_stale` so the census keeps its history; with neither
    # flag the bool True the candidate is refused as before
    if f.snap_fresh is not True and f.snap_market_fresh is not True:
        return "snapshot_stale"
    d = _num(f.drift)
    if d is None or d < 0.0 or d > float(MIRROR_DRIFT_MAX):
        return "drift"
    live, today = _count(f.books_live), _count(f.opened_today)
    if live is None or today is None:
        return "max_books"
    if live >= MIRROR_MAX_LIVE_BOOKS or today >= MIRROR_MAX_BOOKS_PER_DAY:
        return "max_books"
    if f.first_fill_ok is not True:
        return "first_fill_gate"
    return None


# ---------------------------------------------------------------- prices

def buy_wire(px: float | None) -> float | None:
    """The cent a BUY rests at: a price FLOORED to the tick. The
    executor's rest_tick(wire_limit(px, BUY_LONG), BUY_LONG) does
    exactly this for a long, and the test suite pins the two against
    each other; the arithmetic is repeated here because the rules
    module imports no executor. Floored so the rest can never pay
    above the price and never sits above the bid, so it never crosses
    at placement -- IN A BOOK WITH bid < ask. In a locked or inverted
    book (bid >= ask) a cent at or under the bid can be at or through
    the ask: the post-only rest is refused with the 400 that arms the
    take (take_arms), and a fill, were one to happen, would still be
    at or under him -- the level bound holds in every book, the
    no-cross bound only in a normal one.

    THE PRICE MUST BE EXACT -- the unrounded min(his level, bid) that
    buy_price computes -- because a level rounded first can round UP
    across a cent: mi.plan rounds plan.price to 4 places, so his
    0.47996 arrives as 0.48, and 0.48 floors to 0.48, a cent above
    him. This floor alone is exact to 6 decimals (the round(..., 6)
    that absorbs float noise also absorbs a seventh decimal:
    0.479999999 reads as 48.0); buy_price adds the post-condition
    that holds at any precision. None when there is no usable cent:
    under 0.01, at or over 1, or not a number (a bool, a string,
    NaN, an infinity)."""
    p = _num(px)
    if p is None or not (0.0 < p < 1.0):
        return None
    w = math.floor(round(p * 100.0, 6)) / 100.0
    return w if w >= 0.01 else None


def sell_wire(px: float | None) -> float | None:
    """The cent a SELL rests at: a price CEILED to the tick, capped at
    the venue's top tick 0.99. Ceiled so the sale is never under the
    price; the cap is the ladder's edge, not a concession. Exact like
    buy_wire: sell_price passes the unrounded max(his equivalent,
    ask), because a 4-place rounding of 0.52004 is 0.52, under him.
    None when the price is not a positive number."""
    p = _num(px)
    if p is None or p <= 0.0:
        return None
    if p >= 0.99:
        return 0.99
    w = math.ceil(round(p * 100.0, 6)) / 100.0
    return w if w >= 0.01 else None


def buy_price(his_level: float | None, bid: float | None) -> float | None:
    """The cent a BUY rests at, from the UNROUNDED facts: floor-to-cent
    of min(his level, bid). This is the worker's wire for a BUY.

    THE WORKER PASSES HIS UNROUNDED LEVEL -- the fill price as
    ingested (6 decimals), the same figure it passes mi.plan as
    his_last_px -- and the raw bid, NEVER plan.price. mi.plan rounds
    min(his, bid) to 4 places BEFORE any floor (mirror.py plan(),
    `round(min(cands), 4)`), and 0.47996 rounds to 0.48: a wire from
    plan.price would rest a cent ABOVE him. This floor of the exact
    minimum is at or under his level and at or under the bid for every
    level, which the property tests sweep over 6-decimal prices AND
    full-precision floats.

    THE POST-CONDITION IS UNCONDITIONAL: wire <= his level and
    wire <= bid, at ANY precision. Ingestion rounds prices to at most
    8 decimals, but nothing here assumes it: buy_wire's floor reads a
    level a hair under a cent (0.479999999) as the cent, so the cent
    is checked against the exact level and stepped DOWN one cent when
    it is above him or the bid; a cent that would still be above, or
    under 0.01, is no price.

    None when either fact is missing or not a price in (0, 1), or when
    there is no cent at or under both: no price to rest at
    (`no_price`), never a guess. His level unreadable is not "rest at
    the bid": with no level there is nothing to join.
    """
    h, b = _num(his_level), _num(bid)
    if h is None or b is None or not (0.0 < h < 1.0) or not (0.0 < b < 1.0):
        return None
    w = buy_wire(min(h, b))
    if w is None:
        return None
    if w > h or w > b:
        w = round(w - 0.01, 2)
    if w < 0.01 or w > h or w > b:
        return None
    return w


def sell_price(his_equiv: float | None, ask: float | None) -> float | None:
    """The cent a SELL rests at, from the UNROUNDED facts: ceil-to-cent
    of max(his equivalent, ask), capped at 0.99. This is the worker's
    wire for a SELL. The worker passes his UNROUNDED equivalent
    (1 - his price on the other token, from the ingested 6-decimal
    fill) and the raw ask, never plan.price: mi.plan's 4-place
    rounding turns 0.52004 into 0.52, a cent UNDER him. The
    post-condition is unconditional like buy_price's: wire >=
    min(0.99, his equivalent) and wire >= min(0.99, ask) at any
    precision (sell_wire's ceiling reads 0.520000001 as 52.0; the
    cent is stepped UP once when it is under either), or None. None
    when either fact is missing or not a price in (0, 1)."""
    h, a = _num(his_equiv), _num(ask)
    if h is None or a is None or not (0.0 < h < 1.0) or not (0.0 < a < 1.0):
        return None
    w = sell_wire(max(h, a))
    if w is None:
        return None
    if w < 0.99 and (w < h or w < a):
        w = min(0.99, round(w + 0.01, 2))
    if w < 0.99 and (w < h or w < a):
        return None
    return w


def plan_wire(p: Plan | None) -> float | None:
    """The cent of a shadow plan's price, by side. plan.price is
    ROUNDED to 4 places by mi.plan, so this is the shadow's figure for
    comparison and logging, not the worker's wire: the worker rests at
    buy_price / sell_price and passes that wire to keep_or_replace.
    None for anything that is not a Plan with a str side of BUY or
    SELL."""
    if not isinstance(p, Plan) or not isinstance(p.side, str) or p.side not in (BUY, SELL):
        return None
    return buy_wire(p.price) if p.side == BUY else sell_wire(p.price)


def room_scale(qty: int, wire: float | None, clip_usd: float | None,
               day_room: float | None, total_room: float | None,
               mirror_day: float | None) -> int:
    """The BUY quantity the room allows: min(qty, floor(min(per-order
    clip, sleeve day room, sleeve total room, mirror day room) / wire)).
    Under one share is 0, which the worker names `over_room`. Any room,
    quantity or wire that could not be read as a finite number (a
    bool, a string, NaN, an infinity) is no room, the wire must be a
    cent on the ladder (0.01 to 0.99: a sub-cent wire divides a room
    into an infinity of shares), and a quotient that is not finite is
    no room: 0, never a raise."""
    q = _count(qty)
    if q is None or q < 1:
        return 0
    rooms: list[float] = []
    for r in (clip_usd, day_room, total_room, mirror_day):
        v = _num(r)
        if v is None:
            return 0
        rooms.append(v)
    w = _num(wire)
    if w is None or not (0.01 <= w <= 0.99):
        return 0
    cash = min(rooms)
    if cash <= 0:
        return 0
    shares = cash / w
    if not math.isfinite(shares):
        return 0
    allowed = min(q, int(math.floor(round(shares, 6))))
    return allowed if allowed >= 1 else 0


# ---------------------------------------------------------- open orders

@dataclass
class OpenOrder:
    side: str                    # BUY_LONG / SELL_LONG
    wire: float | None
    qty: int
    leaves: float | None         # qty - filled, by the last order_status read
    placed_at: float | None      # epoch seconds


_PLAN_REASON_KEYS = {
    "on target": "on_target",
    "under one share": "under_one_share",
    "under the dollar dead band": "dead_band",
    "inside hysteresis": "hysteresis",
    "no price to rest at": "no_price",
    "venue unreadable": "positions_unreadable",
    "frozen: venue and ledger disagree": "venue_ledger_disagree",
}

_FROM_PLAN = object()      # keep_or_replace's "no wire given: take the plan's"


def _cent(v: Any) -> float | None:
    """A cent on the ladder: a number in [0.01, 0.99] within 1e-6 of a
    whole cent, or None. Both the plan's wire and the RESTING order's
    wire are read through this -- an order resting off a cent is not
    an order this book placed."""
    f = _num(v)
    if f is None or not (0.01 <= f <= 0.99) or abs(f * 100.0 - round(f * 100.0)) > 1e-6:
        return None
    return f


def plan_reason_key(reason: str | None) -> str:
    """mi.plan's reason text as its census name; a reason that cannot
    even be rendered as text is `no_plan`."""
    try:
        r = str(reason or "").strip()
    except Exception:       # noqa: BLE001 -- a reason object whose __str__/__bool__ raises is no reason
        return "no_plan"
    if r in _PLAN_REASON_KEYS:
        return _PLAN_REASON_KEYS[r]
    return "".join(ch if ch.isalnum() else "_" for ch in r.lower()).strip("_") or "no_plan"


def keep_or_replace(order: OpenOrder, p: Plan | None, now: float,
                    ttl_s: float = MIRROR_REST_TTL_S,
                    cancel_reason: str | None = None,
                    wire: float | None | object = _FROM_PLAN) -> str:
    """What to do with the order already resting on this book.

      'keep'      same side, same cent, leaves within a share (or within
                  MIN_MOVE_FRAC of the plan's quantity), younger than
                  the TTL -- the worker names it `open_order_pending`
      'replace'   the plan moved (side, cent, quantity), the order aged
                  past the TTL, or a fact about the order could not be
                  read (its wire, its age, its leaves, the TTL, the
                  clock; an order placed in the FUTURE is unreadable
                  too): cancel, read, book, re-quote at his newest
                  equivalent. Unreadable is never 'keep'.
      <reason>    cancel only: the book is frozen / closing / a guard
                  tripped (`cancel_reason`, passed through; a reason
                  given but blank is `cancel_unnamed`, still a cancel),
                  or the plan is no order at all (its census name)
      'no_price'  the plan has a side but no cent to rest at

    `wire` is the cent the worker WOULD rest at now -- buy_price /
    sell_price from his unrounded level -- and is what the resting
    order's cent is compared to. Without it the plan's own rounded
    price is used (the shadow's figure), which can differ by a cent
    from the exact wire at a 5- or 6-decimal level and would replace
    forever; the worker passes its wire. A wire passed as None, off
    the ladder or off a cent is `no_price`; a RESTING order whose own
    wire is off a cent is not one this book placed: 'replace'. `ttl_s`
    can only SHORTEN the order's life: the effective TTL is
    min(ttl_s, MIRROR_REST_TTL_S). A plan under one share is not a
    plan an order can match: 'replace'. An `order` that is not an
    OpenOrder, or a `p` that is neither None nor a Plan, is 'replace'
    (after a cancel_reason, which always wins); sides are compared
    only as the two strings BUY / SELL.
    """
    if cancel_reason is not None:
        return cancel_reason if isinstance(cancel_reason, str) and cancel_reason.strip() else "cancel_unnamed"
    if not isinstance(order, OpenOrder) or (p is not None and not isinstance(p, Plan)):
        return "replace"
    if p is None or p.side is None:
        return plan_reason_key(p.reason if p is not None else "no_plan")
    placed, t, ttl = _num(order.placed_at), _num(now), _num(ttl_s)
    if placed is None or t is None or ttl is None:
        return "replace"
    ttl = min(ttl, float(MIRROR_REST_TTL_S))
    age = t - placed
    if age < 0 or age >= ttl:
        return "replace"
    if (not isinstance(p.side, str) or not isinstance(order.side, str)
            or p.side not in (BUY, SELL) or p.side != order.side):
        return "replace"
    pw = _cent(plan_wire(p) if wire is _FROM_PLAN else wire)
    if pw is None:
        return "no_price"
    ow = _cent(order.wire)
    if ow is None or round(abs(ow - pw), 6) >= 0.01:
        return "replace"
    leaves, q = _num(order.leaves), _num(p.qty)
    if leaves is None or q is None or q < 1:
        return "replace"
    diff = abs(leaves - q)
    if diff < 1.0 or diff <= MIN_MOVE_FRAC * abs(q):
        return "keep"
    return "replace"


# -------------------------------------------------------------- the take

def at_or_through(side: str, bid: float | None, ask: float | None,
                  wire: float | None) -> bool:
    """Is the book at or through OUR resting level right now? A BUY is
    marketable when the ask is at or under our cent, a SELL when the
    bid is at or over it. The wire must be a cent on the ladder
    (0.01 to 0.99, a number, not a bool) and so must the quote: a
    missing, unreadable or impossible quote (0.0, -0.0, 1e-12, 1.0,
    1.5, 1e308) is not at anything, so a take never fires on a
    non-quote."""
    w = _num(wire)
    if w is None or not (0.01 <= w <= 0.99):
        return False
    # a side that is not a string is not a side; comparing it could
    # run a foreign __eq__ that raises, and a take must never fire or
    # crash on that (rules review addendum §11, owner order 2026-09-02
    # "go for it, let's get this working")
    if not isinstance(side, str):
        return False
    if side == BUY:
        a = _num(ask)
        return a is not None and 0.01 <= a <= 0.99 and a <= w + 1e-9
    if side == SELL:
        b = _num(bid)
        return b is not None and 0.01 <= b <= 0.99 and b >= w - 1e-9
    return False


def take_allowed(rest_age_s: float | None, take_armed_at: float | None, now: float,
                 bid: float | None, ask: float | None, wire: float | None, side: str,
                 wait_s: float = MIRROR_TAKE_AFTER_S) -> bool:
    """The bounded take (never IOC-first, critic C15). Allowed only when
    (a) a rest for this (book, level) has stood unfilled `wait_s` --
    `rest_age_s` is the age AT THIS LEVEL, reset by every re-quote --
    or a post-only rejection armed the take that long ago, AND (b) the
    book is at or through his level NOW, so the one IOC at the SAME
    wire pays his price or better. A market that never comes to him is
    held under target (`resting_above_level`), never chased.

    `wait_s` can only LENGTHEN the wait: the effective wait is
    max(wait_s, MIRROR_TAKE_AFTER_S), so no caller shortens it (an
    unreadable wait_s is the constant). An age or clock that is not a
    finite number has not waited, and an arm time before the epoch or
    after `now` is not an arm time."""
    ws = _num(wait_s)
    floor = float(MIRROR_TAKE_AFTER_S)
    ws = floor if ws is None else max(ws, floor)
    waited = False
    age = _num(rest_age_s)
    if age is not None and age >= ws:
        waited = True
    armed, t = _num(take_armed_at), _num(now)
    if armed is not None and t is not None and 0.0 <= armed <= t and t - armed >= ws:
        waited = True
    return waited and at_or_through(side, bid, ask, wire)


def _int_code(v: Any) -> int | None:
    """An HTTP status as the int it arrived as, or None: a bool, a
    float 400.0, a string '400' or anything else is not a status the
    adapter read from the venue."""
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def take_arms(refusal: Any) -> bool:
    """Does a post-only rejection ARM the take? Only the venue's
    crossing refusal (addendum section 7), which the venue voices in
    TWO shapes; the adapter (pmus.submit_fok under post_only=True)
    returns `post_only_rejected` for both and carries the facts in
    `raw`, and this reads either the bare status code or that raw dict:

      (a) an HTTP 400 from the SDK -- the int 400 (today's path, the
          worker passes raw["status_code"]), or a dict whose
          "status_code" is the int 400;
      (b) a 200 whose order comes back REJECTED with an execution of
          type REJECTED -- a dict whose "status_code" is the int 200
          AND whose "post_only_cross" is the bool True AND whose
          "execution_type" is exactly EXECUTION_TYPE_REJECTED, the
          SDK's own spelling (polymarket_us/types/orders.py).

    Nothing else arms. A 401/403/429/5xx is the adapter's
    `post_only_rejected` too but says nothing about the book; a string
    '400', a float, a bool, None, a list, a dict missing any of the
    keys its shape needs, a "status_code" that is not an int, a 200
    without post_only_cross, a post_only_cross that is not the bool
    True, an execution type spelled any other way -- none of them is
    the venue's crossing refusal, and arming is the aggressive path.
    The truth table is pinned in the tests (to-a-tee program Phase 7,
    owner order 2026-09-02 "I want us to match everything ... mirror
    the whales to a tee": no default rides before the 1-share rungs
    read both shapes from the venue; the market `state` read that
    Phase 4 adds so a PREOPEN/SUSPENDED 400 never arms is not here yet
    and is not assumed).
    """
    if isinstance(refusal, dict):
        code = _int_code(refusal.get("status_code"))
        if code == 400:
            return True
        if code != 200:
            return False
        return (refusal.get("post_only_cross") is True
                and isinstance(refusal.get("execution_type"), str)
                and refusal.get("execution_type") == EXECUTION_TYPE_REJECTED)
    return _int_code(refusal) == 400


# --------------------------------------------------------------- flatten

def select_flatten(target: int, his_long: float | None, his_other: float | None,
                   snap_fresh: bool | None, snap_long: float | None,
                   snap_other: float | None, market_live: bool | None,
                   confirm_gone: bool | None,
                   snap_partial: bool | None = None) -> str | None:
    """Which flatten a target of 0 is. None when the target is not 0
    (that is a reduce, not a flatten).

      'flatten_paired'     he still holds a token by fills, or a FRESH
                           snapshot (complete or partial: a truncated
                           walk that lists the token is still a sight
                           of him holding it) shows him holding one:
                           the paired-out book. Rests at max(1 - q,
                           ask), re-quoted on TTL, NEVER marketed (the
                           spec removed the 300 s target-0 slippage
                           flatten; critic C16)
      'flatten_vanished'   he has LEFT the market: no fills on either
                           token, the market live, the mirror's OWN
                           whale_exits._confirm_gone True, AND the
                           snapshot either shows him flat on a FRESH,
                           COMPLETE walk (both sizes read as 0) or is
                           ABSENT OR PARTIAL (snap_fresh is not True,
                           or snap_partial is True). Addendum section
                           8: RN1's positions walk is always truncated,
                           so a fresh complete snapshot never exists
                           for him and _confirm_gone off fills-derived
                           zero is the positive confirmation. The only
                           path that accepts slippage.
      'vanish_unconfirmed' gone by fills but not by every confirmation
                           (the market not live, _confirm_gone not
                           True, or a fresh complete snapshot whose
                           sizes could not be read): treated as
                           flatten_paired this tick

    THE TRUTH TABLE, which the tests sweep: nothing but (market_live
    is True, confirm_gone is True, both fills read as 0, no fresh
    snapshot reading above 0) reaches flatten_vanished.

    ONLY AN INT IS A TARGET: a bool, a float (0.0, -0.0), a string or
    None is no flatten (None). Every size reading is REQUIRED as a
    number at or above zero (review finding): a None, bool, string,
    non-finite or NEGATIVE his_long / his_other is a reading that was
    not made, and on the one path that accepts slippage an unmade
    reading is `vanish_unconfirmed`, never zero. The worker passes
    pos.get(token, 0.0) and, for a completed walk, snap.get(token,
    0.0); a snapshot that is absent or partial carries no reading of
    its own and says so by snap_fresh / snap_partial. EVERY FLAG IS A
    bool OR None: a 1, a 0, a 'True' in snap_fresh, snap_partial,
    market_live or confirm_gone is `vanish_unconfirmed` (review
    finding: snap_fresh=1 read as "snapshot absent" and walked past a
    fresh sighting of him into the slippage path). Fills that show him
    holding name the paired book before any flag is read.
    """
    if isinstance(target, bool) or not isinstance(target, int) or target != 0:
        return None
    hl, ho = _size(his_long), _size(his_other)
    if hl is None or ho is None:
        return "vanish_unconfirmed"
    if hl > 0 or ho > 0:
        return "flatten_paired"
    for flag in (snap_fresh, snap_partial, market_live, confirm_gone):
        if not (flag is None or isinstance(flag, bool)):
            return "vanish_unconfirmed"
    sl, so = _size(snap_long), _size(snap_other)
    if snap_fresh is True and ((sl is not None and sl > 0) or (so is not None and so > 0)):
        return "flatten_paired"
    if not (market_live is True and confirm_gone is True):
        return "vanish_unconfirmed"
    absent_or_partial = snap_fresh is not True or snap_partial is True
    fresh_flat = (snap_fresh is True and snap_partial is not True
                  and sl is not None and so is not None and sl == 0 and so == 0)
    if fresh_flat or absent_or_partial:
        return "flatten_vanished"
    return "vanish_unconfirmed"


# --------------------------------------------------------------- booking

@dataclass
class BookState:
    """mirror_books' arithmetic columns. ledger_net is long-token shares
    BY OUR BOOKING; avg_cost the weighted average of the buys behind
    it; peak_exposure_usd the STAKE the record grades against (max over
    life of ledger_net x avg_cost); realized_pnl every sale, partials
    included, which the global breaker cannot see."""
    ledger_net: float = 0.0
    avg_cost: float | None = None
    gross_buy_usd: float = 0.0
    gross_sell_usd: float = 0.0
    peak_exposure_usd: float = 0.0
    realized_pnl: float = 0.0


class Booking(NamedTuple):
    state: BookState
    booked: float            # shares actually booked
    usd: float               # cash of the booked shares
    realized: float | None   # SELL only; None when nothing was booked
    overfill: bool           # a SELL past what the ledger held
    refusal: str | None      # 'bad_delta' | 'nothing_to_book' | 'bad_price' | 'bad_usd'
    #                          | 'bad_state' | 'avg_cost_unknown'


def _px(px: Any) -> float | None:
    p = _num(px)
    return p if p is not None and 0.0 < p < 1.0 else None


def _state_nums(state: BookState) -> BookState | None:
    """The state's columns as finite numbers (None as 0 for the sums,
    None kept for avg_cost), or None when any column is not a number
    or the ledger is negative: a long-only book cannot be short, and
    a NaN ledger is not a ledger. avg_cost that is not a PRICE in
    (0, 1) -- a long token never cost 5.0 or -0.5 -- reads as unknown
    (None), which the booking names `avg_cost_unknown` wherever
    shares are held against it. Something that is not a BookState at
    all (None, a dict, a number) is no book -- never an EMPTY one
    (review finding: book_buy(None, 5, 0.5) booked 5)."""
    if not isinstance(state, BookState):
        return None
    vals: dict[str, float] = {}
    for k in ("ledger_net", "gross_buy_usd", "gross_sell_usd", "peak_exposure_usd", "realized_pnl"):
        v = getattr(state, k, None)
        n = 0.0 if v is None else _num(v)
        if n is None:
            return None
        vals[k] = n
    if vals["ledger_net"] < 0:
        return None
    ac = getattr(state, "avg_cost", None)
    return BookState(avg_cost=None if ac is None else _px(ac), **vals)


def book_buy(state: BookState, delta_shares: float, px: float | None,
             usd: float | None = None) -> Booking:
    """Book a BUY fill of `delta_shares` at `px` into the state.

    IDEMPOTENCY IS THE CALLER'S. `delta_shares` is the venue's filled
    quantity MINUS mirror_orders.booked_filled, read and advanced under
    `WHERE booked_filled = $expected` in the same transaction as the
    standing-row UPDATE (which itself refuses a repeated (order_id,
    seq) in raw.adds). This function is plain arithmetic: applied twice
    to the same fill it books it twice, so it is never called outside
    that cursor. It is PURE: the same inputs give the same Booking and
    the input state is never mutated (the caller commits or rolls back
    the returned one).

    Weighted average cost, the standing row's fill_price formula: a
    buy onto a flat book (ledger 0) starts the average afresh at the
    fill price, so a flat-then-rebuy episode does not inherit the old
    cost. `usd` is fill_cash for BUY_LONG (shares x price) when the
    caller has the venue's figure; the identity otherwise.

    Nothing that is not a number reaches the ledger (review finding):
      'bad_delta'        delta_shares None, a bool, a string, NaN or
                         an infinity
      'nothing_to_book'  a delta read as under FLAT_TOL_SHARES: zero,
                         negative, or dust
      'bad_price'        px not a finite price in (0, 1)
      'bad_usd'          usd given but not a finite number >= 0
      'bad_state'        a state column that is not a number, or a
                         negative ledger -- BEFORE the arithmetic, and
                         AFTER it: a result that overflowed to an
                         infinity or an average off the ladder is
                         refused, never written (review finding: a
                         1e308 ledger plus 1e308 shares read as inf)
      'avg_cost_unknown' shares already held without a known cost, or
                         against a cost that is not a price in (0, 1):
                         no average can be carried
    A refusal returns the input state unchanged.

    IT DOES NOT KNOW WHAT WE ASKED TO PAY, and that is deliberate: it
    is handed a price, never the order's wire, so ANY finite price in
    (0, 1) books. A rest that fills above its own cent therefore books
    silently here, inflating avg_cost, gross_buy_usd and the day's
    spend. THE WIRE COMPARISON IS THE CALLER'S and it exists: the
    worker's `_book_delta` holds `o["wire"]`, compares the venue's
    average against it for every BUY (a CLOSE row exempt by name, its
    wire being 0.0 by construction), and trips the lane off under
    `mirror_overspend`. Do not add the comparison here: this function is
    pure arithmetic over one state, it has no wire, no census and no
    trip, and an instrument that can refuse a booking would strand
    shares the venue has already given us. Its refusal list above is
    exhaustive and unchanged."""
    d = _num(delta_shares)
    if d is None:
        return Booking(state, 0.0, 0.0, None, False, "bad_delta")
    if d < FLAT_TOL_SHARES:
        return Booking(state, 0.0, 0.0, None, False, "nothing_to_book")
    p = _px(px)
    if p is None:
        return Booking(state, 0.0, 0.0, None, False, "bad_price")
    if usd is None:
        cash = d * p
    else:
        c = _num(usd)
        if c is None or c < 0:
            return Booking(state, 0.0, 0.0, None, False, "bad_usd")
        cash = c
    st = _state_nums(state)
    if st is None:
        return Booking(state, 0.0, 0.0, None, False, "bad_state")
    net0 = st.ledger_net
    net1 = net0 + d
    if net0 > 0:
        if st.avg_cost is None:
            return Booking(state, 0.0, 0.0, None, False, "avg_cost_unknown")
        prior = st.avg_cost * net0
    else:
        prior = 0.0
    avg = round((prior + p * d) / net1, 6)
    peak = max(st.peak_exposure_usd, round(net1 * avg, 4))
    new = replace(st, ledger_net=net1, avg_cost=avg,
                  gross_buy_usd=round(st.gross_buy_usd + cash, 4),
                  peak_exposure_usd=peak)
    if _state_nums(new) is None or _px(avg) is None or not math.isfinite(cash):
        return Booking(state, 0.0, 0.0, None, False, "bad_state")
    return Booking(new, d, round(cash, 4), None, False, None)


def book_sell(state: BookState, delta_shares: float, px: float | None) -> Booking:
    """Book a SELL fill of `delta_shares` at `px`. Same cursor contract
    and the same purity as book_buy.

    booked = min(delta, ledger_net); realized = (px - avg_cost) x
    booked, the long formula (le.realized_pnl for BUY_LONG). An
    OVERFILL -- the venue sold more than the ledger held, by more than
    FLAT_TOL_SHARES -- books the ledger and flags it: a sale past zero
    on a signed-net venue is a SHORT, and the worker freezes the book
    and trips mirror_live off with the receipt. A ledger under
    FLAT_TOL_SHARES is flat: nothing books onto it (booked 0, no
    refusal, the overfill flag still set when the sale was real). The
    average cost is untouched by a sale; a book sold to zero keeps it
    until the next buy resets it.

    Refusals as book_buy's ('bad_delta', 'nothing_to_book' for a
    delta under FLAT_TOL_SHARES, 'bad_price', 'bad_state'), and
    'avg_cost_unknown' when shares would be booked against a cost that
    is None or not a price: nothing is realized from an unknown cost.
    """
    d = _num(delta_shares)
    if d is None:
        return Booking(state, 0.0, 0.0, None, False, "bad_delta")
    if d < FLAT_TOL_SHARES:
        return Booking(state, 0.0, 0.0, None, False, "nothing_to_book")
    p = _px(px)
    if p is None:
        return Booking(state, 0.0, 0.0, None, False, "bad_price")
    st = _state_nums(state)
    if st is None:
        return Booking(state, 0.0, 0.0, None, False, "bad_state")
    net0 = st.ledger_net
    booked = min(d, net0)
    overfill = d > net0 + FLAT_TOL_SHARES
    if booked < FLAT_TOL_SHARES:
        return Booking(state, 0.0, 0.0, None, overfill, None)
    if st.avg_cost is None:
        return Booking(state, 0.0, 0.0, None, overfill, "avg_cost_unknown")
    realized = round((p - st.avg_cost) * booked, 4)
    cash = round(booked * p, 4)
    new = replace(st, ledger_net=net0 - booked,
                  gross_sell_usd=round(st.gross_sell_usd + cash, 4),
                  realized_pnl=round(st.realized_pnl + realized, 4))
    if _state_nums(new) is None or not math.isfinite(realized) or not math.isfinite(cash):
        return Booking(state, 0.0, 0.0, None, overfill, "bad_state")
    return Booking(new, booked, cash, realized, overfill, None)


# ----------------------------------------------------------------- drift

class DriftRule(NamedTuple):
    increase_ok: bool
    reduce_from: str             # 'derived' | 'smaller'
    refusal: str | None          # 'snapshot_stale' | 'drift' | None
    drift: float | None


def drift_of(his_long: float | None, snap_long: float | None) -> float | None:
    """|derived - snapshot| / max(derived, snapshot, 1), or None when
    either reading is not a size (None, a bool, a string, NaN, an
    infinity, a negative): no number is made from a reading that was
    not made."""
    a, b = _size(his_long), _size(snap_long)
    if a is None or b is None:
        return None
    return round(abs(a - b) / max(a, b, 1.0), 6)


def drift_rule(his_long: float | None, snap_long: float | None, fresh: bool | None,
               partial: bool | None, last_fresh_agreed: bool = False,
               drift_max: float = MIRROR_DRIFT_MAX) -> DriftRule:
    """Derived (from his fills) against the exit worker's raw snapshot.

    Fresh and within `drift_max`: increases allowed, reductions sized
    from the derived reading. Fresh and drifted: increases refused
    (`drift`), reductions sized from the SMALLER of the two readings.
    The smaller reading gives the smaller target, so it sells MORE of
    what we hold than the larger one would: when the readings
    disagree we keep no more than the reading he is most likely to
    have left justifies, so we never keep holding a position he may
    already have exited -- and we never BUY on a disagreement at all.
    Stale or partial: increases refused (`snapshot_stale`); reductions
    proceed on derived data only while the last fresh read agreed
    (`last_fresh_agreed is True`, which the WORKER must assert -- the
    default is False, the smaller), else from the smaller. Either
    reading not a size (None, a bool, a string, NaN, an infinity, a
    negative): stale, from the smaller, drift None.

    Fresh means READ fresh and READ complete: `fresh is True and
    partial is False`. A partial flag that was not read (None) is not
    "not partial" (review finding); it is a stale read. The leading
    two fields are the (increase_ok, reduce_from) pair; the refusal
    and the number ride beside them.
    """
    d = drift_of(his_long, snap_long)
    if d is None:
        return DriftRule(False, "smaller", "snapshot_stale", None)
    fresh_eff = fresh is True and partial is False
    if not fresh_eff:
        return DriftRule(False, "derived" if last_fresh_agreed is True else "smaller",
                         "snapshot_stale", None)
    dm = _num(drift_max)
    if dm is None or d > min(dm, float(MIRROR_DRIFT_MAX)):    # a caller can only tighten the bound
        return DriftRule(False, "smaller", "drift", d)
    return DriftRule(True, "derived", None, d)


def drift_net_rule(his_long: float | None, his_other: float | None,
                   snap_long: float | None, snap_other: float | None) -> float | None:
    """The derived-vs-snapshot disagreement read on the NET of both
    tokens: |(his_long - his_other) - (snap_long - snap_other)| /
    max(|net_fills|, |net_snap|), rounded to 6 places; 0.0 when both
    nets are zero (nothing to disagree about); None when any of the
    four readings is not a size (None, a bool, a string, NaN, an
    infinity, a NEGATIVE) -- no number is made from a reading that
    was not made, never a guess.

    WHY THE NET AND NOT THE TOKEN (to-a-tee program Phase 1, owner
    order 2026-09-02 "I want us to match everything ... mirror the
    whales to a tee"): 42.8% of his shares since 08-01 are merged pair
    legs (probe:1843). His fills say +5,000 Yes and +5,000 No; he
    merges the pair on-chain and the venue then shows 0 and 0. The
    per-token rule drift_of reads |5,000 - 0| / 5,000 = 1.0 on each
    token and refuses every increase on that market for the life of
    the book -- a lifelong drift lock-out on a position that is, in
    truth, flat on both sides. On the net the same market reads
    |0 - 0| = 0: drift 0. A one-sided add reads the same number under
    both rules (his_long 1,000 vs snap 990 with nothing on the other
    token is 0.01 here and in drift_of), so the net rule loosens
    nothing where the per-token rule was right. Its inputs are still
    held to the per-token rule's standard: the per-token rule refuses
    negatives (_size), and so does this one, on all four -- a size
    under zero is a reading nobody made, whatever it would net to.
    The denominator is exactly the larger |net| (no share floor): a
    sub-share net against a zero net reads as full disagreement, the
    closed reading, and the worker's FLAT_TOL_SHARES dust never
    reaches a target anyway. Beside drift_rule, which stays as
    pinned; the worker that reads Phase 1's per-market read decides
    which drift number rides in AdmissionFacts.drift.
    """
    hl, ho, sl, so = _size(his_long), _size(his_other), _size(snap_long), _size(snap_other)
    if hl is None or ho is None or sl is None or so is None:
        return None
    net_fills = hl - ho
    net_snap = sl - so
    denom = max(abs(net_fills), abs(net_snap))
    if denom == 0.0:
        return 0.0
    d = abs(net_fills - net_snap) / denom
    return round(d, 6) if math.isfinite(d) else None


# --------------------------------------------------------- episode close

def episode_close_reason(state: BookState, market_closed_or_resolved: bool | None,
                         vanished_confirmed: bool | None, flat_for_s: float | None,
                         open_orders: int | None,
                         flat_close_s: float = MIRROR_FLAT_CLOSE_S) -> str:
    """Whether this episode closes now and how -- or, by name, why not.

      'cashed_out'       CLOSES: gross_buy_usd > 0, the row closes with
                         its realized sales as pnl (the mirror_exit
                         shape, $0 added)
      'cancelled'        CLOSES: never filled, the row is released and
                         the asset claim with it, without waiting on a
                         venue verdict
      'bad_open_orders'  the open-order count did not arrive as an int
                         at or above zero (None, a bool, '0', 0.0,
                         -0.0, Decimal('0'), NaN, a negative): a count
                         is counted, never computed, and nothing
                         closes on one that was not
      'orders_open'      an order is still non-terminal
      'bad_state'        a ledger column that is not a number, or a
                         state that is not a BookState
      'held'             shares still held: |ledger_net| at or over
                         FLAT_TOL_SHARES (addendum section 9: a
                         fractional venue fill can leave 1e-8 shares,
                         and a book "held" by dust would never close).
                         A market that resolves with shares HELD is
                         not closed by us -- settlement from the venue
                         closes that row
      'not_due'          flat, but none of: the market closed or
                         resolved; he has LEFT (vanish confirmed); the
                         book sat flat at target 0 for `flat_close_s`

    Only the first two are a close; episode_close() is this reduced
    to the verdict, for the tick that asks nothing more. `flat_close_s`
    can only LENGTHEN the flat wait: the effective limit is
    max(flat_close_s, MIRROR_FLAT_CLOSE_S), so no caller closes a
    flat book early (review finding: flat_close_s=0 cashed out at
    once).
    """
    n = _int(open_orders)
    if n is None:
        return "bad_open_orders"
    if n != 0:
        return "orders_open"
    st = _state_nums(state)
    if st is None:
        return "bad_state"
    if abs(st.ledger_net) >= FLAT_TOL_SHARES:
        return "held"
    due = market_closed_or_resolved is True or vanished_confirmed is True
    if not due:
        flat, limit = _num(flat_for_s), _num(flat_close_s)
        if limit is not None:
            limit = max(limit, float(MIRROR_FLAT_CLOSE_S))
        due = flat is not None and limit is not None and flat >= limit
    if not due:
        return "not_due"
    return "cashed_out" if st.gross_buy_usd > 0 else "cancelled"


def episode_close(state: BookState, market_closed_or_resolved: bool | None,
                  vanished_confirmed: bool | None, flat_for_s: float | None,
                  open_orders: int | None,
                  flat_close_s: float = MIRROR_FLAT_CLOSE_S) -> str | None:
    """'cashed_out' | 'cancelled' when the episode closes now, else
    None. The reason it does not is episode_close_reason()'s; nothing
    that is not a read count of zero open orders over a numeric, flat
    ledger ever closes, and garbage is never 'cancelled'."""
    why = episode_close_reason(state, market_closed_or_resolved, vanished_confirmed,
                               flat_for_s, open_orders, flat_close_s)
    return why if why in ("cashed_out", "cancelled") else None


# --------------------------------------------------------------- P2 gate

def _read(numbers: dict, key: str, failures: list[str], lo: float | None = None,
          hi: float | None = None, whole: bool = False) -> float | None:
    """One number of the payload by the _num rule (finite, not a bool,
    not a string), held to its plausible range: under `lo`, over `hi`
    or not a whole number when `whole` is `unreadable:<key>` like a
    missing one -- an implausible figure is a figure that was not
    computed."""
    v = numbers.get(key) if isinstance(numbers, dict) else None
    f = _num(v)
    if (f is None or (lo is not None and f < lo) or (hi is not None and f > hi)
            or (whole and f != math.floor(f))):
        failures.append(f"unreadable:{key}")
        return None
    return f


def _interval(numbers: dict, failures: list[str]) -> tuple[float, float] | None:
    """The book cohort's 95% interval: proof.roi_with_ci's `ci95`
    pair, or ci_lo/ci_hi, or roi +- Z95 x se -- one standard. ONE FORM
    DECIDES: the first of the three that is present. Both bounds read
    by the _num rule (finite, not a bool, not a string), lo <= hi,
    se >= 0; anything else is `unreadable:ci95`, and a malformed form
    never falls through to a second one (review finding: [inf, inf],
    [True, True], [0.5, 0.1] and a negative se all used to pass)."""
    n = numbers if isinstance(numbers, dict) else {}
    lo = hi = None
    if n.get("ci95") is not None:
        ci = n.get("ci95")
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            lo, hi = _num(ci[0]), _num(ci[1])
    elif n.get("ci_lo") is not None or n.get("ci_hi") is not None:
        lo, hi = _num(n.get("ci_lo")), _num(n.get("ci_hi"))
    elif n.get("roi") is not None or n.get("se") is not None:
        roi, se = _num(n.get("roi")), _num(n.get("se"))
        if roi is not None and se is not None and se >= 0:
            lo, hi = roi - Z95 * se, roi + Z95 * se
    if lo is None or hi is None or lo > hi:
        failures.append("unreadable:ci95")
        return None
    return lo, hi


def demotion_due(numbers: dict) -> bool:
    """Spec 6 (2): the cohort's upper bound below zero at MIN_N_DEMOTE
    or more closed books turns mirror_live off (`demoted`); exits and
    flattens continue. Unreadable numbers demote nothing -- the DB
    switch's own unreadable state is already exits-only."""
    scratch: list[str] = []
    books = _read(numbers, "closed_books", scratch, lo=0, whole=True)
    iv = _interval(numbers, scratch)
    if books is None or iv is None:
        return False
    return books >= MIN_N_DEMOTE and iv[1] < 0.0


def capture_short(numbers: dict) -> bool | None:
    """Spec 6 (5), REPORTED beside the verdict, never gated: True when
    the pooled capture of his scaled long-only book P&L is under
    P2_CAPTURE_MIN, False at or above it, None when it cannot be read
    (missing, not a number, negative). The interval decides the gate;
    this rides on the MIRRORGRADE line."""
    scratch: list[str] = []
    c = _read(numbers, "capture", scratch, lo=0.0)
    return None if c is None else c < P2_CAPTURE_MIN


def p2_verdict(numbers: dict) -> tuple[bool, list[str]]:
    """The P1 -> P2 gate, every clause numbered as in the spec, ALL
    read from `numbers` (the /api/admin/mirror payload), none from
    memory. Returns (pass, failures); a number that cannot be read is
    a failure named `unreadable:<key>`, and so is one outside its
    plausible range (a share over 1, a negative count, a slip under
    -1): a figure that cannot be is a figure that was not computed.

    keys: closed_books, games (whole, >= 0), ci95|ci_lo+ci_hi|roi+se
    (see _interval), at_or_better, maker_share (in [0, 1]),
    take_slip_median (>= -1), frozen_ticks, live_ticks (whole, >= 0),
    the integrity counters (P2_INTEGRITY_COUNTERS; whole, >= 0),
    drift_p90 (>= 0), capture (>= 0; read for plausibility, reported
    by capture_short, never gated by its size), census_missing (list),
    why_overflow (bool).
    """
    failures: list[str] = []
    n = numbers if isinstance(numbers, dict) else {}
    # (1) sample: closed books over distinct games
    books = _read(n, "closed_books", failures, lo=0, whole=True)
    games = _read(n, "games", failures, lo=0, whole=True)
    if books is not None and books < MIN_N_PROMOTE:
        failures.append(f"books<{MIN_N_PROMOTE}")
    if books is not None and games is not None and games > books:
        failures.append("unreadable:games")       # a game is counted through a closed book: more games than books is no count
        games = None
    if games is not None and games < MIN_PROOF_CLUSTERS:
        failures.append(f"games<{MIN_PROOF_CLUSTERS}")
    # (2) the interval
    iv = _interval(n, failures)
    if iv is not None:
        lo, hi = iv
        if not (lo > 0.0):
            failures.append("ci_lo<=0")
        if hi < 0.0 and books is not None and books >= MIN_N_DEMOTE:
            failures.append("demoted")
    # (3) execution
    aob = _read(n, "at_or_better", failures, lo=0.0, hi=1.0)
    if aob is not None and aob < 1.0:
        failures.append("at_or_better<1")
    maker = _read(n, "maker_share", failures, lo=0.0, hi=1.0)
    if maker is not None and maker < P2_MAKER_SHARE_MIN:
        failures.append(f"maker_share<{P2_MAKER_SHARE_MIN}")
    slip = _read(n, "take_slip_median", failures, lo=-1.0)
    if slip is not None and slip > P2_TAKE_SLIP_MAX:
        failures.append("take_slip>1c")
    # (4) integrity
    ft = _read(n, "frozen_ticks", failures, lo=0, whole=True)
    lt = _read(n, "live_ticks", failures, lo=0, whole=True)
    if ft is not None and lt is not None:
        if lt <= 0:
            failures.append("unreadable:live_ticks")
        elif not (ft / lt < P2_FROZEN_TICK_FRAC_MAX):
            failures.append("frozen_ticks>=1%")
    for key in P2_INTEGRITY_COUNTERS:
        v = _read(n, key, failures, lo=0, whole=True)
        if v is not None and v != 0:
            failures.append(key)
    dp = _read(n, "drift_p90", failures, lo=0.0)
    if dp is not None and dp > MIRROR_DRIFT_MAX:
        failures.append(f"drift_p90>{MIRROR_DRIFT_MAX}")
    # (5) capture is reported beside the verdict (capture_short); the
    # interval decides. Read here for plausibility only: a capture
    # that is not a number, or negative, is a figure not computed
    _read(n, "capture", failures, lo=0.0)
    # (6) the census
    missing = n.get("census_missing")
    if not isinstance(missing, (list, tuple, set)):
        failures.append("unreadable:census_missing")
    elif missing:
        # an item whose __str__ raises is a census nobody can read:
        # refuse by name rather than let the verdict crash (rules
        # review addendum §11, owner order 2026-09-02 "go for it,
        # let's get this working")
        try:
            failures.append("census_missing:" + ",".join(sorted(str(m) for m in missing)))
        except Exception:
            failures.append("unreadable:census_missing")
    overflow = n.get("why_overflow")
    if not isinstance(overflow, bool):
        failures.append("unreadable:why_overflow")
    elif overflow:
        failures.append("why_overflow")
    return (not failures), failures


# Deliberately NOT here: the shared constants imported above
# (MARKET_NET_CAP_USD, MIN_MOVE_FRAC, RATIO_MAX, MIN_PROOF_CLUSTERS,
# Z95, MIRROR_ANCHOR_CLIP_USD, MIN_N_DEMOTE, MIN_N_PROMOTE) and mi.Plan. Each
# is USED here and belongs to its own module; this module re-exports
# none of them, and the test suite reads them through this module only
# to pin that they are the same objects, never restated.
__all__ = [
    "ORDER_INTENT", "BUY", "SELL", "ORDER_STATE_REJECTED", "EXECUTION_TYPE_REJECTED",
    "capped_env", "min_wait_env",
    "MIRROR_NET_CAP_FLOOR_USD",
    "MIRROR_NET_CAP_USD", "MIRROR_MAX_LIVE_BOOKS", "MIRROR_MAX_BOOKS_PER_DAY",
    "MIRROR_DAY_USD", "MIRROR_LOSS_STOP_USD", "MIRROR_MAX_ORDER_OPS_PER_TICK",
    "MIRROR_MAX_REPLACES_PER_HOUR", "MIRROR_REST_TTL_S", "MIRROR_TAKE_AFTER_S",
    "MIRROR_FLATTEN_REST_S", "MIRROR_FLAT_CLOSE_S", "MIRROR_DRIFT_MAX",
    "MIRROR_FROZEN_ALERT_S", "MIRROR_FROZEN_NAME_TICKS", "MIRROR_FAMILIES", "FLAT_TOL_SHARES",
    "P2_MAKER_SHARE_MIN", "P2_TAKE_SLIP_MAX", "P2_FROZEN_TICK_FRAC_MAX", "P2_CAPTURE_MIN",
    "P2_INTEGRITY_COUNTERS",
    "mirror_target", "AdmissionFacts", "admission",
    "buy_wire", "sell_wire", "buy_price", "sell_price", "plan_wire", "room_scale",
    "OpenOrder", "plan_reason_key", "keep_or_replace",
    "at_or_through", "take_allowed", "take_arms", "select_flatten",
    "BookState", "Booking", "book_buy", "book_sell",
    "DriftRule", "drift_of", "drift_rule", "drift_net_rule",
    "episode_close", "episode_close_reason",
    "demotion_due", "capture_short", "p2_verdict",
]
