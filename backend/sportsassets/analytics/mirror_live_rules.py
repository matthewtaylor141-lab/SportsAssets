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
from .mirror import MARKET_NET_CAP_USD, MIN_MOVE_FRAC, RATIO_MAX, RATIO_MIN, Plan
from .proof import MIN_PROOF_CLUSTERS, Z95
from .roster_rules import MIRROR_ANCHOR_CLIP_USD, MIN_N_DEMOTE, MIN_N_PROMOTE

# P1 is long-only: the standing row's raw.preview.intent and every
# submit_fok call carried this one constant. P2 (rung S0, owner order
# 2026-09-05 "we need to make sure we are mirroring shorts") keeps it as
# the LONG book's intent and the default everywhere, and adds the short
# book's beside it; a book's intent is fixed at open (mirror_books.intent)
# and every rule that needs the sign reads it from the book, never from
# a module constant. BUY / SELL stay the PLAN-side names in LONG space
# (mi.plan is sign-blind); the wire-side map below turns (book intent,
# plan side) into the intent the venue is sent.
ORDER_INTENT = "ORDER_INTENT_BUY_LONG"
ORDER_INTENT_SHORT = "ORDER_INTENT_BUY_SHORT"
BUY, SELL = "BUY_LONG", "SELL_LONG"
# The four WIRE intents, spelled as the SDK's Literal spells them
# (polymarket_us/types/orders.py OrderIntent): what mirror_orders.intent
# (migration 050) may carry.
WIRE_INTENTS = frozenset({"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_LONG",
                          "ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_SHORT"})


def env_switch(name: str, default: bool = False) -> bool:
    """A boolean switch read from the environment, the way the mirror's
    other on/off dials are read (PMUS_MIRROR_POST_ONLY, PMUS_MIRROR_GTD:
    the words on/1/true/yes turn it on, off/0/false/no turn it off,
    anything else -- absent, blank, a typo -- is the default). The
    environment holds strings, so this is the one place one is parsed."""
    if not isinstance(name, str):
        return bool(default)
    raw = str(os.environ.get(name) or "").strip().lower()
    if raw in ("on", "1", "true", "yes"):
        return True
    if raw in ("off", "0", "false", "no"):
        return False
    return bool(default)


# THE ONE KNOB OF P2 RUNG S0. Off, every rule in this module and every
# statement the live worker sends is what P1 shipped: a negative target
# is `short_side_refused`, a book is long-only, the shadow's target
# column is long-only. On, the live lane follows the whale's SHORT side
# (mirror_target admits a negative target as a BUY_SHORT book on the same
# slug) AND the shadow's tick_once computes its target column with the
# same allow_short, in the same deploy, so shadow_live_disagree compares
# like with like (brief E5). ON BY DEFAULT since 2026-09-06 (owner order
# ~14:10Z, verbatim: "I want shorts live as well"); MIRROR_SHORTS=off
# still turns it off from a shell. The default moved by a code change,
# as the discipline required. What did NOT move is the pre-S4 exit
# safety: a short book's partial reduce stays `short_reduce_unproven`
# (held, counted) and its flatten stays close_position when sole
# holder -- the only proven short exit -- until a 1-share resting
# SELL_SHORT has been read back at rung S4. Read through the module at
# call time (rules.MIRROR_SHORTS), like every cap.
MIRROR_SHORTS = env_switch("MIRROR_SHORTS", True)


def column_missing(exc: BaseException, column: str) -> bool:
    """The driver's UndefinedColumnError (by type name, so this module
    imports no driver), or any driver's text for it -- the one reading
    under which a column the workers reach production
    ahead of (mirror_orders.intent, migration 050) is ABSENT. Every
    other error on the same statement is the statement failing, not
    the column missing, and a caller that read it as absence would act
    on a database blip (P2 rung S0 review, the intent guard)."""
    if type(exc).__name__ == "UndefinedColumnError":
        return True
    msg = str(exc)
    return column in msg and "does not exist" in msg


def shorts_effective(column_present: bool | None) -> bool:
    """THE EFFECTIVE KNOB, the one reading both lanes make: the
    environment's MIRROR_SHORTS (this module at call time) AND the 050
    column present this tick. The live lane admits a short by it and
    the shadow computes its live-compared target column by it, so the
    two never part while the column is on its way (brief E5). Only the
    bool True of `column_present` is presence: an unreadable probe is
    not a column."""
    return bool(MIRROR_SHORTS) and column_present is True


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


def unbounded_env(name: str) -> float:
    """A cap -- a count of books or a day's dollars -- whose code
    default is NO CAP, spelled math.inf.

    capped_env's semantics with an unbounded default (owner orders
    2026-09-06: 13:36Z "I don't want to cap books opened at all";
    ~14:00Z "Let's remove those caps so we start copying his actual
    book"): the environment may only LOWER it, to a finite number --
    rung S3's 1-share probe runs with MIRROR_MAX_LIVE_BOOKS=1 -- and
    nobody can raise one from a shell, because there is nothing above
    no cap. An absent, blank, unparseable or non-finite value is the
    default (unbounded; 'inf' in the environment is not a lowering). A
    negative value lands on 0, the most closed setting there is (no
    books; no room). UNBOUNDED IS math.inf AND NOTHING ELSE: never a
    large number, which a reader would take for a count or a budget,
    and never 0, which capped_env's floor rule reads as NONE. The count
    caps are read through `_bounded`; the day cap through
    math.isfinite at the one site that applies it.
    """
    v = _env_float(name)
    if v is None:
        return math.inf
    return max(0.0, v)


def _bounded(cap: Any) -> int | None:
    """A count cap as admission reads it: None for UNBOUNDED (positive
    math.inf, the only spelling of "no cap"), else a whole count at or
    above zero (a fraction floors). Anything else -- None, a bool, NaN,
    a string, a negative -- is an UNREADABLE cap and reads as 0, the
    most closed: a cap nobody can read is a shut door, never an open
    one (fail closed; the cap with no readable default IS the floor,
    as capped_env has it)."""
    if isinstance(cap, float) and cap == math.inf:
        return None
    f = _num(cap)
    if f is None or f < 0:
        return 0
    return int(math.floor(f))


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


# THE CAP IS PER GAME, ACROSS EVERY MARKET OF THE GAME (E1, owner orders
# 2026-09-06 ~14:00Z "a hard cap of no single event having more than
# $2.5k on it ... this limitation should never force us to decline any
# of the possible copies" and ~22:3xZ "I just want to make sure the per
# game cap is at 2500 per game (never more)"). Each market of a game --
# the moneyline per team, the draw, every total line, btts, the spreads
# -- is its own book, and MIRROR_NET_CAP_USD applied per book let a
# game with six markets carry $15,000 (Espanyol/Sevilla ~$2,347 across
# five books on the night of the order, under the line by luck). The
# three pure readings below give the worker the per-game figure:
#   book_exposure  dollars at risk AT COST on one book -- ledger x
#                  avg_cost on a long, |ledger| x (1 - avg_cost) (the
#                  collateral) on a short, mirror-pnl's `open_cost` --
#                  plus the resting BUY-side notional of its open
#                  increase (money that can still fill). avg_cost NULL
#                  on a held book reads the MARK (more exposure counted,
#                  never less); nothing readable is None
#   game_room      MIRROR_NET_CAP_USD less the OTHER books' exposure of
#                  the game, clamped to [0, cap]; an unreadable sum is
#                  no room (fail closed)
#   game_capped    the target once the room is applied: an INCREASE is
#                  sized at the room -- SCALED, never refused -- and
#                  with no room it is at most what the book holds (an
#                  increase of 0, `game_cap_full`; `game_unreadable`
#                  when the game could not be read); a reduce or a
#                  flatten is never touched: the cap is not a reason to
#                  sell. The room is dollars AT COST: the worker hands
#                  mi.target_shares the held shares at the MARK plus
#                  what the room leaves after the book's own held cost,
#                  so the increase costs at most that (the per-market
#                  cap keeps its at-the-mark reading, unchanged)
# The game is the game_key ALONE -- every whale's books of one game
# share the one cap ("no single EVENT"). A book with no game_key is
# its own game (the per-market cap as before). The worker walks games
# oldest-touched first and a game's books in one fixed order (book id
# ascending), and counts a book sized this tick at its new target, so
# the room is consumed deterministically inside the tick.

def book_exposure(ledger_net: Any, avg_cost: Any, mark: Any, intent: Any = None,
                  resting_usd: Any = 0.0) -> float | None:
    """Dollars at risk at cost on one book (see the block above): the
    held leg at its average cost -- the mark when the cost is unreadable
    -- plus `resting_usd`, the open increase's unfilled notional. The
    short leg is the book whose ledger is negative or whose intent is
    BUY_SHORT; its cost a share is 1 - price. None when the ledger, the
    resting figure, or (on a held book) both the cost and the mark are
    unreadable, or the price is off the ladder: a figure nobody can read
    is no figure, and the caller treats None as no room."""
    n = _num(ledger_net)
    rest = _num(resting_usd)
    if n is None or rest is None or rest < 0:
        return None
    held = abs(n)
    if held < FLAT_TOL_SHARES:
        return round(rest, 4)
    px = _num(avg_cost)
    if px is None:
        px = _num(mark)
    if px is None or not (0.0 < px < 1.0):
        return None
    short = n < 0 or is_short(intent)
    return round(held * ((1.0 - px) if short else px) + rest, 4)


def game_room(other_exposure: Any, cap_usd: Any = None) -> float:
    """The dollars a game has left for one of its books: cap less the
    exposure of the game's OTHER books, clamped to [0, cap]. `cap_usd`
    None is MIRROR_NET_CAP_USD (read at call time); a cap at or under
    zero, or an unreadable exposure, is no room -- 0, fail closed."""
    cap = _num(MIRROR_NET_CAP_USD if cap_usd is None else cap_usd)
    if cap is None or cap <= 0:
        return 0.0
    e = _num(other_exposure)
    if e is None or e < 0:
        return 0.0
    return round(min(cap, max(0.0, cap - e)), 4)


def game_capped(full: int, room_target: int | None, ledger: Any,
                short: bool) -> tuple[int, str | None]:
    """(target, name): `full` is the per-market target as today,
    `room_target` the same arithmetic at cap min(room, per-market cap)
    -- None when the room is 0 -- and `ledger` the book's held shares.
    Only an INCREASE (away from zero on the book's own leg: above the
    ledger on a long book, below it on a short one) is touched; a
    reduce, a flatten or an on-target book keeps `full` (name None).
    An increase the room still admits past the ledger is the room
    target (`game_cap_scaled` when that is under `full`, None when the
    room did not bind); one the room does not admit is the ledger
    itself -- an increase of 0, `game_cap_full` -- and NEVER less."""
    led = int(_num(ledger) or 0)
    increasing = (full < led) if short else (full > led)
    if not increasing:
        return int(full), None
    if room_target is not None:
        rt = int(room_target)
        if (rt < led) if short else (rt > led):
            return rt, (None if rt == int(full) else "game_cap_scaled")
    return led, "game_cap_full"
# Blast radius. NO COUNT CAP on books, live or opened per day (owner
# order 2026-09-06 13:36Z, verbatim: "I don't want to cap books opened
# at all. I want max trade on one side of an event to be $1000 between
# all fills"; and ~14:00Z: "Let's remove those caps so we start copying
# his actual book"): both default to UNBOUNDED (math.inf; unbounded_env)
# and the environment may only LOWER them. admission reads them through
# `_bounded`: a finite cap bites by name (`max_books`), an unreadable
# COUNT of books refuses by its own name (`books_unreadable`), and an
# unbounded cap never refuses. THE WORST CASE, said plainly: with no
# count cap and no day cap (MIRROR_DAY_USD below) it is bounded by the
# loss stop (MIRROR_LOSS_STOP_USD, $1,000 realized in 24 h, then
# reduce-only until re-armed by hand) and by the venue balance -- not
# by a count of books and not by a day figure. Per event it is the
# $2,500 net cap (MIRROR_NET_CAP_USD), which scales and never declines.
MIRROR_MAX_LIVE_BOOKS = unbounded_env("MIRROR_MAX_LIVE_BOOKS")
MIRROR_MAX_BOOKS_PER_DAY = unbounded_env("MIRROR_MAX_BOOKS_PER_DAY")
# THE FIXED RATIO (owner order ~14:00Z 2026-09-06: "Just trade 10% of
# what he puts on everything he takes"). The ratio a book opens at for
# every allowlisted whale whose position is at or over
# MIRROR_SMALL_BET_USD (open_ratio; under it the copy is exact, ratio
# 1.0 -- owner order ~14:10Z, "Bets under $10, take the full position
# (exact copy)"). DECIDED AT OPEN AND STORED ON THE BOOK
# (mirror_books.ratio): an open book sizes on its stored ratio for its
# whole life and is never re-targeted at another -- a position that
# crosses $10 must not flip between 100% and 10% every tick -- so the
# books open today keep the 1.0 they opened at. ms.refresh_ratios keeps
# running and its anchor/bankroll readings stay reported as
# diagnostics; the shadow keeps sizing from its own readings (it
# measures). capped_env: the environment may only LOWER it, and the
# floor is mi.RATIO_MIN, so no shell can set it to 0 -- at open,
# `no_ratio` can fire only on a non-finite or non-positive constant.
MIRROR_RATIO = capped_env("MIRROR_RATIO", 0.10, floor=RATIO_MIN)
# THE SMALL-BET LINE (same order). His dollars at the mark at open --
# |his net shares| x px, px the mark for a long and 1 - mark for a
# short -- under this many dollars is copied whole. The environment may
# only LOWER it (0: nothing is small, every book opens at MIRROR_RATIO).
MIRROR_SMALL_BET_USD = capped_env("MIRROR_SMALL_BET_USD", 10.0)


def open_ratio(net: Any, mark: Any) -> float:
    """THE RATIO A BOOK OPENS AT (owner orders 2026-09-06 ~14:00Z and
    ~14:10Z): 1.0 -- an exact copy -- when his dollars at the mark are
    under MIRROR_SMALL_BET_USD, else MIRROR_RATIO. His dollars are |net|
    x px with px the mark for a long (net >= 0) and 1 - mark for a
    short (net < 0): the collateral his side commits, which is what
    "bets under $10" means on either side. Unreadable net or mark (by
    _num), or a mark off the ladder, is NOT small: the conservative
    MIRROR_RATIO -- and mirror_target still names the unreadable fact
    itself (`no_position`, `no_mark`), since it reads both again. Pure
    and side-effect free; the worker stores the result on the book at
    open and never calls this for an open book."""
    # the constant is handed on AS IS, never coerced: an unreadable one
    # is mirror_target's to name (`no_ratio`), not this reader's to mask
    usd = _his_dollars(net, mark)
    line = _num(MIRROR_SMALL_BET_USD)
    if usd is None or line is None or line <= 0:
        return MIRROR_RATIO
    return 1.0 if usd < line else MIRROR_RATIO


def _his_dollars(net: Any, mark: Any) -> float | None:
    """His dollars at the mark: |net| x mark on a long (net >= 0), |net|
    x (1 - mark) -- the collateral -- on a short. None when the net or
    the mark is unreadable (_num) or the mark is off the ladder."""
    n, m = _num(net), _num(mark)
    if n is None or m is None or not (0.01 <= m <= 0.99):
        return None
    return abs(n) * (m if n >= 0 else 1.0 - m)


def step_ratio(stored_ratio: Any, net: Any, mark: Any) -> float | None:
    """THE ONE-WAY STEP off an exact-copy book (review of U12c, FIX-1):
    the ratio is decided at open from ONE read of his position, so a
    book opened on the first $8 fill of a larger burst would follow him
    at 100% up to the $2,500 cap. When a book's STORED ratio is 1.0 (a
    small-bet book) and his dollars at the mark now EXCEED twice the
    small-bet line -- 2 x MIRROR_SMALL_BET_USD, $20, derived here and
    never a second knob -- the answer is MIRROR_RATIO: the worker
    writes it to the row for the book's life and names `ratio_stepped`.
    None otherwise: a 1.0 book at or under the step line, a book at any
    other ratio (a 0.10 book never steps, and NOTHING EVER STEPS UP),
    an unreadable stored ratio, net or mark (a step on a guess would
    sell). The hysteresis is the gap between $10 and $20: a position
    that crosses $10 does not flip; one that doubles past it does,
    once. The step changes the target from 100% to 10% and the normal
    reduce path sells the excess."""
    stored = _num(stored_ratio)
    if stored is None or abs(stored - 1.0) > 1e-9:
        return None
    usd = _his_dollars(net, mark)
    line = _num(MIRROR_SMALL_BET_USD)
    if usd is None or line is None or line <= 0:
        return None
    return MIRROR_RATIO if usd > 2.0 * line else None


# THE CATCH-UP TOLERANCE, IN CENTS (E12, 2026-09-08; docs/mirror-to-a-
# tee-program.md decision 13, option (A) "follow only his flow from
# first sight (Rule LE, pro-rata ratchet)" with option (b)'s small
# allowance). A block he built before we saw the market is bought --
# the book opens sized on his WHOLE net, as before E12 -- only when the
# mark at open is within this many cents of his cost over that block
# (mi.vwap_of: his size-weighted BUY price on the book's axis), so the
# premium is at most the tolerance and "10% of his shares" holds; else
# the book opens on his FLOW alone (mi.flow_net) and the block is never
# bought. capped_env: the environment may only LOWER it; 0 is NEVER
# catch up (not "within 0c": an exact-copy book under MIRROR_SMALL_BET_USD
# still copies whole, its premium being cents on under $10).
MIRROR_CATCHUP_TOL_CENTS = capped_env("MIRROR_CATCHUP_TOL_CENTS", 2.0, floor=0.0)
# the knob's own default, named once (the lane's review fold, 2026-09-08,
# MEDIUM-2): a tolerance LOWERED under it from the environment was, before
# D1, the operator's only handle on the worse side and meant that many
# cents -- it stays that many cents, bounding D1's band (below), so the
# rail an operator lowered is never widened by a deploy. Must agree with
# the literal above (pinned).
_CATCHUP_TOL_DEFAULT_CENTS = 2.0

# THE WORSE SIDE'S BAND (PNL lane 1 part (b); OWNER DECISION D1 = YES,
# 2026-09-08 ~13:3xZ, "make the changes, and get it live and running
# immediately"; hard2/PNL_program.md §2). With the book's axis known
# the worse side's allowance is no longer the flat 2c but
#   min(MIRROR_CATCHUP_MAX_CENTS, max(MIRROR_CATCHUP_TOL_CENTS,
#       MIRROR_CATCHUP_PCT x vwap))
# in cents on the contract price on the axis: 10% of his cost, never
# under the 2c floor, never over 5c (a 0.20 cost keeps 2c, a 0.50 cost
# gets 5c, a 0.80 cost is capped at 5c not 8c). The premium is bounded
# by 5c x shares (<= $125 on a $2,500 position) and the bought block
# earns his ROI on the day. capped_env, both: the environment may only
# LOWER the percentage (0 = the 2c floor alone) and the cap (0 = no
# worse side at all); nothing raises either, and MIRROR_CATCHUP_TOL_CENTS
# lowered under its 2c default IS the band (the fold, MEDIUM-2: the env
# may only lower, so the handle it had before D1 keeps its meaning). The
# five-argument call (no axis) keeps E12's flat 2c: the band needs the
# side of his cost.
MIRROR_CATCHUP_PCT = capped_env("MIRROR_CATCHUP_PCT", 0.10, floor=0.0)
MIRROR_CATCHUP_MAX_CENTS = capped_env("MIRROR_CATCHUP_MAX_CENTS", 5.0, floor=0.0)

# THE CANDIDATE'S SIDE BAND (FILL lane 0a, 2026-09-08). How far the ask
# may sit from his price at the OPEN before the candidate refuses
# `side_band` (the admission clause; Martinez 534 on 2026-09-08: his
# 0.61 against a market at 0.81 / 0.82, refused). The live worker read
# LIVE_SIDE_PRICE_BAND bare (`_env_float`, 0.15 when unset), so a value
# above 0.15 in the environment WIDENED the open's price band -- the one
# rail on the money path a shell could raise. capped_env: 0.15 is the
# ceiling, the environment may only LOWER it (0 = only an ask AT his
# price opens), an unreadable value lands on 0.15. The copy lane's
# executor reads the same variable at its own side check
# (live_executor.maybe_execute) and is not this rail's caller.
LIVE_SIDE_PRICE_BAND_MAX = capped_env("LIVE_SIDE_PRICE_BAND", 0.15, floor=0.0)

# THE AXIS NOT HANDED IN (PNL lane 1, 2026-09-08). open_catchup's E12
# callers and pins pass five arguments and no axis; they get E12's
# verdict exactly as pinned (the symmetric tolerance, no allowance --
# never wider than E12 was). A caller that WANTS the at-or-better
# allowance hands the book's axis (`short` True or False); an axis that
# is handed in but unreadable (None, a non-bool) is `axis_unread`.
_AXIS_NOT_GIVEN: Any = object()


def _mark_at_or_better(m: float, v: float, short: bool) -> bool:
    """Is the mark at or better than his cost on the book's axis, in
    contract space (the long token's price, the space mi.vwap_of reads
    both tokens onto)? A long is built by buying: at or UNDER his cost.
    A short is built by selling: at or OVER it. Equality is at-or-better
    on both (1e-9: the same float epsilon the tolerance reads with)."""
    return (m >= v - 1e-9) if short else (m <= v + 1e-9)


def _catchup_band_cents(tol: float, v: float) -> float:
    """The WORSE side's band in cents (owner decision D1 = YES,
    2026-09-08): min(MIRROR_CATCHUP_MAX_CENTS, max(tol, MIRROR_CATCHUP_PCT
    x vwap)), the vwap in contract space so 10% of 0.50 is 5c. Read at
    call time as the tolerance is. A knob that does not read (None, a
    string, NaN -- never from capped_env, which falls back to its
    default) grants NO widening: the band is the tolerance alone, E12's
    2c; a cap under the tolerance narrows the band under it (the env
    may only lower). A tolerance LOWERED under its 2c default (the
    environment's pre-D1 handle on the worse side; the lane's review
    fold, 2026-09-08, MEDIUM-2) is the band itself: TOL 1 admits 1c on
    every cost, never max(1c, 10%) -- the env may only lower, and a
    rail the operator lowered is not widened by a deploy."""
    if tol < _CATCHUP_TOL_DEFAULT_CENTS:
        return tol
    pct, mx = _num(MIRROR_CATCHUP_PCT), _num(MIRROR_CATCHUP_MAX_CENTS)
    if pct is None or mx is None:
        return tol
    return min(mx, max(tol, pct * v * 100.0))


def open_catchup(net: Any, mark: Any, ratio: Any, block: Any, vwap: Any,
                 short: Any = _AXIS_NOT_GIVEN) -> dict[str, Any]:
    """THE OPEN'S VERDICT ON HIS PRE-EXISTING BLOCK (E12): the plan row's
    `catchup` = {vwap, mark, tol, allowed, flow_base, why}. `flow_base`
    is what the book stores: 0 when the block is bought (the book sizes
    on his whole net, as before), the block itself when it is not (the
    book sizes on his flow from first sight), None only when the block
    could not be read AND neither could his net. In order:

      `block_unread`  the block is unreadable: his whole net stands in
                      for it (nothing is bought); allowed False
      `no_block`      nothing pre-existing (the market is new to him
                      too): flow IS his net; allowed True, flow_base 0
      `small_bet`     the book's ratio is 1.0 -- the exact copy under
                      MIRROR_SMALL_BET_USD (owner ~14:10Z 2026-09-06):
                      copied whole, the premium is cents; allowed
      `axis_unread`   the axis was handed in and is not True / False:
                      the side of his cost cannot be read, not bought
                      (PNL lane 1; a verdict on a guessed axis would buy
                      a short's block at a premium)
      `at_or_better`  PNL LANE 1 (2026-09-08; the E12 allowance defect,
                      hard2/PNL_program.md lane 1): the mark is at or
                      better than his cost on the book's axis -- long
                      mark <= vwap, short mark >= vwap, contract space
                      (_mark_at_or_better) -- at ANY distance: allowed,
                      flow_base 0. Book 266 (his vwap 0.503-0.549, mark
                      0.50) was 4.9c UNDER his cost and E12's |mark -
                      vwap| <= tol refused it as it refuses 4.9c over;
                      a better mark carries all of his edge and no
                      premium, and proportionality is the objective.
                      THIS IS AN ALLOWANCE, NOT A TOLERANCE: it stands
                      with MIRROR_CATCHUP_TOL_CENTS lowered to 0 (the
                      env may only LOWER the worse side's tolerance;
                      it never touches the better side), so it is
                      judged BEFORE `tol_zero`. Only when the axis was
                      handed in; the mark and the vwap must both read
                      (else the unchanged chain below names which)
      `tol_zero`      MIRROR_CATCHUP_TOL_CENTS at or under 0: never
      `vwap_unread`   he bought nothing readable in the block: nothing
                      to read the premium against, not bought. Readable
                      is a price on the ladder, 0 < vwap < 1 (the
                      lane's review fold, 2026-09-08, MEDIUM-1): a
                      finite figure no fill could carry -- 1.5, 55.0, a
                      negative -- is not his cost, and the better side
                      above never reads it either (E12's symmetric
                      tolerance refused it; the allowance must too)
      `mark_unread`   no mark on the ladder (mirror_target has refused
                      `no_mark` before this is reached; belt and braces)
      `within_tol`    |mark - vwap| <= tol cents: allowed, flow_base 0
                      (with the axis handed in only the WORSE side
                      reaches here: the 2c floor of the band)
      `within_pct`    PNL LANE 1 PART (b), OWNER DECISION D1 = YES
                      (2026-09-08 ~13:3xZ; hard2/PNL_program.md §2):
                      with the axis handed in the worse side's band is
                      min(MIRROR_CATCHUP_MAX_CENTS, max(tol,
                      MIRROR_CATCHUP_PCT x vwap)) cents on the contract
                      price on the axis (_catchup_band_cents: 10% of
                      his cost, floor 2c, cap 5c -- a 0.50 cost gets
                      5c, a 0.80 cost 5c not 8c, a 0.20 cost the 2c
                      floor); a mark past the floor but within the
                      band is admitted, allowed, flow_base 0, and named
                      here (within_tol stays for the floor). Both knobs
                      capped_env: the env may only LOWER the band
                      (MIRROR_CATCHUP_PCT 0 = the floor alone,
                      MIRROR_CATCHUP_MAX_CENTS 0 = no worse side);
                      MIRROR_CATCHUP_TOL_CENTS at 0 is still `tol_zero`
                      ahead of the band -- "never catch up" closes the
                      band too -- and lowered under its 2c default it
                      IS the band (the fold, MEDIUM-2: TOL 1 admits 1c
                      on every cost; the env may only lower). The
                      row's `tol` stays the floor knob; the band is
                      min(5, max(tol, 10 x vwap)) of the row's `vwap`
      `flow_only`     outside it: the block is never bought

    Without `short` (E12's five-argument call) the verdict is E12's as
    pinned: the symmetric tolerance, no allowance, the flat 2c and no
    band -- never wider. Pure and side-effect free; the tolerance and
    the band's knobs are read at call time."""
    tol = _num(MIRROR_CATCHUP_TOL_CENTS)
    tol = 0.0 if tol is None or tol < 0 else tol
    b, n, m, v = _num(block), _num(net), _num(mark), _num(vwap)
    on_ladder = m is not None and 0.01 <= m <= 0.99
    # his cost must be a price a fill could carry (fold, MEDIUM-1): off the
    # ladder it is `vwap_unread` on both sides, never a cost to be better than
    v_ok = v is not None and 0.0 < v < 1.0
    out: dict[str, Any] = {"vwap": v, "mark": m, "tol": tol, "allowed": False,
                           "flow_base": b, "why": None}
    if b is None:
        out.update(flow_base=n, why="block_unread")
        return out
    if b == 0.0:
        out.update(allowed=True, flow_base=0.0, why="no_block")
        return out
    rt = _num(ratio)
    if rt is not None and abs(rt - 1.0) < 1e-9:
        out.update(allowed=True, flow_base=0.0, why="small_bet")
        return out
    if short is not _AXIS_NOT_GIVEN:
        if not isinstance(short, bool):
            out["why"] = "axis_unread"
            return out
        if on_ladder and v_ok and _mark_at_or_better(m, v, short):
            out.update(allowed=True, flow_base=0.0, why="at_or_better")
            return out
    if tol <= 0.0:
        out["why"] = "tol_zero"
        return out
    if not v_ok:
        out["why"] = "vwap_unread"
        return out
    if m is None or not (0.01 <= m <= 0.99):
        out["why"] = "mark_unread"
        return out
    # the band widens the floor only with the axis known (D1): the
    # five-argument call is judged on the flat tolerance, as E12 pinned
    band = _catchup_band_cents(tol, v) if short is not _AXIS_NOT_GIVEN else tol
    d = abs(m - v)
    if d <= band / 100.0 + 1e-9:
        why = "within_tol" if d <= tol / 100.0 + 1e-9 else "within_pct"
        out.update(allowed=True, flow_base=0.0, why=why)
        return out
    out["why"] = "flow_only"
    return out


# THE MIRROR LANE'S OWN PER-ORDER CLIP (same order). The copy lane's
# LIVE_MAX_CLIP_USD ($250) and per_fill_usd are the COPY lane's
# numbers: sizing the mirror's rests from them made a $2,500 target ten
# sequential $250 rests under one-open-per-book. The mirror's rest is
# clipped here instead -- a $2,500 target is one rest -- and the copy
# lane's constants are untouched. le.per_fill_usd(whale) stays what it
# was for ADMISSION (a whale demoted to a $0 clip opens no book and
# adds to none: `clip_zero`); it no longer sizes. Floor $1: an env of
# 0 would be no rest at all, and "no exposure" is the switch's job.
MIRROR_CLIP_USD = capped_env("MIRROR_CLIP_USD", 2500.0, floor=1.0)
# THE SMALLEST ORDER THE MIRROR SENDS (review of U12c, FIX-2). With no
# dollar dead band and exact copies under $10, his 1 sh @ 0.05 became a
# $0.05 BUY on the wire; the venue's minimum notional is unknown, and a
# refused order would burn one of the six ops per tick, every tick,
# for as long as the book stood. An order whose notional -- wire x qty
# on a long, (1 - wire) x qty of collateral on a short -- is under this
# many dollars is NOT SENT, named `under_min_notional`, the book held.
# So his positions under $1 at the mark are not copied. A FLATTEN IS
# EXEMPT: a position that is leaving leaves at any size (a refused
# flatten rest would never write the row the vanish's rest clock keys
# on, and close_position would never be reached). capped_env: the
# environment may only LOWER it, to 0 (send everything).
MIRROR_MIN_ORDER_USD = capped_env("MIRROR_MIN_ORDER_USD", 1.0)
# THE SHORT SIDE'S SHARE CAP (P2 rung S0, S3 expressibility). The most
# shares a SHORT book may target, whatever his net says. UNBOUNDED by
# default since 2026-09-06 (owner order ~14:10Z, "I want shorts live as
# well"): a short book sizes by the same rule as a long -- the ratio
# decided at open (open_ratio) times his net, capped at $2,500 of
# COLLATERAL at the mark ((1 - mark) x shares, mi.target_shares'
# short-leg price), scaling and never refusing. Same representation as
# the count caps (math.inf, unbounded_env; read through `_bounded` at
# the two sites that admit a short target, the candidate's open and
# the open book's tick): the environment may only LOWER it -- the
# 1-share venue probe of rung S3/S4 runs with MIRROR_SHORT_MAX_SHARES=1
# -- and 0 refuses every short by the name `short_share_cap`, as before.
MIRROR_SHORT_MAX_SHARES = unbounded_env("MIRROR_SHORT_MAX_SHARES")
# Gross BUY dollars per rolling day. NO DAY CAP (owner order ~14:00Z
# 2026-09-06: "Let's remove those caps ... this limitation should never
# force us to decline any of the possible copies"; it answers the
# day-cap question that was saved as task 25): UNBOUNDED by default
# (math.inf, unbounded_env), the environment may only LOWER it, and a
# zero env means no room, as before. The worker applies it at one site
# (_global_guards): a FINITE cap bites `mirror_day_cap` on what filled,
# an UNREADABLE spend read bites `mirror_day_cap` whatever the cap
# (fail closed), and an unbounded cap publishes `mirror_day_room` as
# null and the mode line's `day=none`. The copy sleeve's DAILY room
# (config live_max_daily_usd) no longer binds the mirror either -- that
# was a day cap by another road; the sleeve's TOTAL room still does.
MIRROR_DAY_USD = unbounded_env("MIRROR_DAY_USD")
# Mirror-own loss stop: 24 h realized including partial sales, which the
# global breaker cannot see (it reads terminal rows only). $250 -> $1,000
# by owner decision (2026-09-05 23:0xZ, asked as a multiple choice with
# the program's own numbers beside each option: $250 is 20% of a
# $1,250 day and trips on close to half of ordinary days at the copy
# lane's measured daily ROI spread; he chose the program document's
# figure). Still downward-only from the environment; still realized
# only; still re-armed by hand (render-ops sql mirror-rearm).
# $1,000 -> $5,000 by owner decision (2026-09-06 ~22:30Z): the stop
# tripped at 20:47:34Z on a genuine night (his Juventus/Milan draw
# short and Espanyol/Sevilla under 1.5 settled against him; our 10%
# copies -$1,214 and -$975; the 24 h sum read -$1,553 at the trip and
# -$2,445 at 22:22Z). Asked "$3,000 / $5,000 / keep $1,000" with the
# figures beside each, Matt: "$5,000" -- roughly half of a day's peak
# stake ($10,666 that day) at 10% of his book.
MIRROR_LOSS_STOP_USD = capped_env("MIRROR_LOSS_STOP_USD", 5000.0)
# Venue writes per tick, replaces per book per hour: the venue 429s a
# board walk above ~3 req/s and the copy lane shares the budget. The
# ops budget floors at ONE (review finding): a SAFE or exits-only tick
# is cancel-only, and a tick that may not write at all cannot cancel.
# 6 -> 20 (E2, 2026-09-06, owner order "I want this firing as
# frequently as his"): with 41 books 16 waited a tick (ops_capped 16 at
# 20:35Z). THE RATE IS THE PACER'S, NOT THIS CAP'S (E2 review, HIGH-1):
# a BUY placement is two HTTP requests (preview, create), a cancel and
# a close one, and since E2 every one of this lane's writes claims its
# gaps on the process-wide 0.35 s pacer beside the reads (mirror_live
# _paced), so the venue sees at most one request per gap from this
# process whatever the walk's concurrency. This cap bounds how many
# writes a TICK spends -- 20 writes are ~40 requests, ~14 s of pacer
# time -- and MIRROR_VENUE_CALLS_PER_TICK below bounds the tick's
# writes and candidate reads together.
MIRROR_MAX_ORDER_OPS_PER_TICK = int(capped_env("MIRROR_MAX_ORDER_OPS_PER_TICK", 20, floor=1))
# THE BOOK WALK'S CONCURRENCY (E2). The open books are ticked under an
# asyncio.Semaphore of this many at once (the books of ONE GAME stay
# sequential, in id order, so the per-game cap's room is consumed in
# E1's one fixed order); env may only LOWER it, and 1 is the sequential
# walk. Not a venue-rate knob: every measurement read still queues on
# the process-wide 0.35 s pacer (venue_pace), which is what bounds the
# venue rate -- this bounds the in-flight database and data-API reads.
MIRROR_BOOK_CONCURRENCY = int(capped_env("MIRROR_BOOK_CONCURRENCY", 6, floor=1))
# THE TICK'S VENUE-CALL SOFT GUARD (E2). Every venue request the tick
# makes -- paced reads, cancels, placements (two for a BUY), closes,
# every positions page, the mapping lane's resolver calls -- is counted
# on the census (`venue_calls`). The GUARD counts the tick's WRITES and
# its CANDIDATE reads alone (review MEDIUM-5): the open books' reads are
# the tick's fixed cost -- exits must be managed -- and counting them
# left 12 candidate reads at 46 books. At this many the CANDIDATE walk
# stops for the tick (`venue_calls_capped`), and NOTHING ELSE does:
# every open book is still read and every exit still managed. 80 is
# the 40 candidate reads (mirror_live.MAX_MARKETS_PER_TICK) plus the
# 20 ops above AS REQUESTS -- a BUY placement is a preview and a
# create, so 20 BUYs are 40 (review round 2: at 60 they left 20
# candidate reads) -- ~28 s of pacer time on top of the books' reads.
# Env may only lower it; 0 is a tick that opens no book.
MIRROR_VENUE_CALLS_PER_TICK = int(capped_env("MIRROR_VENUE_CALLS_PER_TICK", 80, floor=0))
MIRROR_MAX_REPLACES_PER_HOUR = int(capped_env("MIRROR_MAX_REPLACES_PER_HOUR", 12))
# A resting order's life. NOT the copy lane's REST_BID_TTL_S (clamped to
# 15 s and doubling as mirror_exit's in-flight wait; critic C14): the
# mirror is a maker and waits. Floors at 30 s (review finding): an env
# of 0 would cancel and re-place every tick, a taker's churn on a
# maker's book and the replace budget gone in an hour.
MIRROR_REST_TTL_S = capped_env("MIRROR_REST_TTL_S", 600.0, floor=30.0)
# THE REST LIVES (E18, 2026-09-08; PNL program lane 6; owner "I want to
# know when he makes money we make money"). An ENTRY rest younger than
# this is not cancelled and re-placed over a cent move under
# REST_MIN_LIFE_CENT_MOVE or over a quantity move alone: 10 of the 24
# increase rows of the 11Z window (hourly_1129 rows 2697-2736) were
# cancelled `replace` after 14, 14, 15, 16, 19, 21, 27, 30, 64, 424 s
# (median 20 s) as his next fill moved the plan by a cent or a few
# shares, each re-quote to the back of the venue's queue -- book 533's
# 2734 @0.49 FILLED after 100 s once left alone, 2725/2729/2731 never
# stood 30 s; book 278's four rests at the SAME cent 0.62 (qty 86 ->
# 316 -> 493 -> 451) filled nothing. A WAIT, NOT A CAP: the environment
# may only LENGTHEN it (min_wait_env), a shorter floor is the churn
# and wants a review. Exits never read it (keep_or_replace's `stands`
# and every reduce rest are E4's rule, unchanged); a cent move of
# REST_MIN_LIFE_CENT_MOVE or more, a side or intent change, the TTL and
# an unreadable fact replace as before. The 12/h replace budget stands.
MIRROR_REST_MIN_LIFE_S = min_wait_env("MIRROR_REST_MIN_LIFE_S", 45.0)
# the cent move that ends the floor early: two cents, in contract space
# (a 1c move of his stands the young rest; 2c re-quotes it). Not a knob
REST_MIN_LIFE_CENT_MOVE = 0.02
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
# 120 s -> 20 s (E2, 2026-09-06, the reviewed aggressive change; owner
# order "I want this firing as frequently as his ... make the latency
# as low as possible"): the 120 s take placed 0 IOCs all night
# (placed_take 0 on every heartbeat read) and fills came only when the
# market came to us (9 of 26 live books had ever filled at 22:22Z). The
# PRICE RULE IS UNCHANGED: one IOC at the SAME wire, only with the book
# at or through his level (at_or_through), never chased. Env may still
# only lengthen it.
# 20 s -> 0 (E4 addendum, 2026-09-06 ~23:38Z, owner verbatim: "Remove
# the 20 second wait on entires too"): an ENTRY takes on the tick it is
# planned -- the ask at or through his level sends ONE IOC at the wire
# FIRST (`take_first`) and the remainder rests post-only at his level;
# the ask above his level rests as before and the take fires on the
# tick the ask arrives, with no age condition on the rest. THE PRICE
# RULE IS STILL UNCHANGED (never above his level; the 1c tolerance of
# E4 is the EXITS' rule, never an entry's). Zero here means "no wait":
# take_allowed reads `age >= 0`, so a rest of any age and a plan with
# no rest at all have waited. Env may only lengthen it, as before: a
# positive wait restores E2's rest-first take on the rest's own age
# and the arm's, and the arm's staleness bound (mirror_live
# TAKE_ARM_STALE_WAITS) is read only under a positive wait.
MIRROR_TAKE_AFTER_S = min_wait_env("MIRROR_TAKE_AFTER_S", 0.0)
# THE EXIT TOLERANCE (E4, 2026-09-06 ~23:24Z, owner verbatim: "we should
# exit when he exits at his price or within 1c variance (tolerance)").
# Every EXIT -- a reduce, the paired flatten, the vanish flatten, the
# sign-flip flatten, a short book's cover -- goes at HIS EXIT PRICE for
# the token, worse by at most this much: a long book's SELL has a
# FLOOR of his price less the tolerance (the rest at the cent ceil(his
# price), the take ONE IOC at the lowest cent at or above the floor
# whenever the bid is there, on the same tick, never chased past it); a
# short book's cover a CEILING of his price plus it (close_position
# only while the ask is at or under it). exit_terms below derives both
# from one figure; the worker never restates it. capped_env: the
# environment may only TIGHTEN it, to 0 (at his cent, nothing worse).
# Book 29 that night (tsc-cfb-washst-wash total 46.5, LONG 63 sh): his
# net flipped short at ~20:30Z, our flatten rest sat at max(his 0.4595,
# ask) while the bid fell to 0.01 / ask 0.02, was cancelled and
# re-quoted 14 times by TTL and never filled, the bounded take never
# fired (the bid never came back to the rest), and the position went
# to settlement. Under this rule it takes at 0.45+ while the bid is
# there and, once the market has fallen, HOLDS at his cent.
MIRROR_EXIT_TOL = capped_env("MIRROR_EXIT_TOL", 0.01, floor=0.0)
# THE EXIT'S TAKE BAND (FILL program lane 3, 2026-09-08; owner decision
# D2: "exits stay within 1c" -- the machinery lands INERT). How far past
# his exit price the exit's ONE IOC may be limited: on a long book's
# SELL the take_band cent is the lowest cent at or above his price less
# max(MIRROR_EXIT_TOL, this band); on a short book's cover the
# cover_band cent is the highest cent at or under his buy-back price
# plus the same. exit_terms reads it at call time like the tolerance,
# and the effective band is max(tol, band): a band at or under the
# tolerance changes NOTHING (take_band == take, cover_band == cover, and
# the tolerance branch of _act fires first), so at this default the
# rule is E4's byte for byte and the lane lands the names alone --
# decision 'exit_take_in_band' / 'cover_in_band' on a band IOC's row,
# census exit_take_in_band / cover_in_band, the band bounds on the plan
# -- so that the second cent, if the owner grants it, is ONE reviewed
# constant change here and not a new lane. The closed-in-24h rows
# (post_exits_1707 335-353, FILL_plan lane 3): 694 shares on eleven
# single-episode long books never exited, settled -340.80, worth 401.79
# at his price less 1c; a second cent's whole cost on them is 694 x
# 0.01 = 6.94. A CONTRACT PRICE, capped_env floor 0.0: the environment
# may only LOWER it (at or under the tolerance it is inert); the default
# IS the ceiling, so a wider band is a code change with a review, never
# a shell's. Read at call time by exit_terms (a test's monkeypatch and
# the operator's lowering apply without a restart of the rule).
MIRROR_EXIT_TAKE_BAND = capped_env("MIRROR_EXIT_TAKE_BAND", 0.01, floor=0.0)
# The band is INERT at or under the tolerance's own code default (0.01),
# whatever the tolerance reads at call time: an operator who LOWERS
# MIRROR_EXIT_TOL under it (E4 review LOW-6: a tolerance of 0 is "at his
# cent, nothing worse") tightens the exit exactly as before this band
# existed -- the plan's bare max(tol, band) would have let the band's
# default stand over a lowered tolerance, an environment change that
# did not lower. Only a band raised ABOVE this figure in code (owner
# decision D2 granted) widens the take, to max(tol, band); the operator
# turns that off with the band's own rail. Not a knob: the tolerance's
# default, restated so exit_terms can read it beside the band.
EXIT_BAND_INERT_AT = 0.01
# THE ENTRY'S TAKE BAND (E14, 2026-09-08; FILL program lane 2; owner
# order ~17:45Z: "We are being filled on his losers and missing his
# winners ... E14 (take at once when the ask is inside the band) is the
# lane for it"; decision D1 (a): one cent, live from the deploy, with
# the 24 h gate). On a LONG book, at FIRST SIGHT only (no order of ours
# standing: the open, the tick after a re-quote cancel, the fast tick's
# wake), an entry whose ask is ABOVE his cent but at or under
# band_cent(his) = buy_wire(his price + this band) -- floor-to-cent of
# his unrounded price plus the band, so no fill is ever more than the
# band over what HE paid -- sends ONE IOC limited at that cent
# (decision 'take_in_band', the word migration 059 reserved), re-read
# at the send as every IOC is (E18), the unfilled remainder resting at
# his cent as today. An ask at or under his cent is today's take
# ('take', unchanged); above the band cent the rest as today ('rest').
# A rest already standing is NEVER converted by this band (the keep
# branch is E18's queue position, docs section 36), a short book's add
# is never band-taken, a reduce never reads it. The 24 h rows before
# this (hourly_1737 1746-1747): 1,922 entry rests, 42.2% placed with
# the ask inside 1c of his cent and 16.0% at or through it, so the
# exactly-1c tranche is (42.2 - 16.0)% x 1,827 = 479 placements --
# the population this converts and nothing else; 557 IOC takes, 65.4%
# filled. A CONTRACT PRICE, capped_env floor 0.0: the environment may
# only LOWER it (0 = the band off, today's behaviour byte for byte);
# the default IS the ceiling, so a wider band is a code change with a
# review, never a shell's. Not the brief's max(2c, 10% of his cent)
# capped 5c: FILL_R5's table turns negative at 4c (-276) and 5c (-440).
# Read at call time by band_cent (tests and the operator's 0 apply
# without a restart of the rule).
MIRROR_TAKE_BAND = capped_env("MIRROR_TAKE_BAND", 0.01, floor=0.0)
# THE FLATTEN'S SLIPPAGE (S4, 2026-09-07): the one bound an UNPRICED
# flatten IOC may slip past the touch -- the long flatten's co-held IOC
# at le.sell_limit_price (the bid less 2c, floored at 0.01) and, mirrored
# for the short cover when he is gone (a vanish, the sign flip) with no
# price of his, le.buy_limit_price (the ask plus 2c, capped at 0.99).
# Documentary: the executor's two functions carry the figure; the tests
# pin them to this constant. Not a knob.
MIRROR_FLATTEN_SLIP = 0.02
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
# FROZEN BOOKS FOLLOW HIS EXITS (E5, 2026-09-07; owner question 16:5xZ
# "when he wins we win, when he loses we lose?"). A book frozen
# `placement_lost` or `venue_ledger_disagree` cancelled its orders and
# held to settlement while he sold. On, the live worker places REDUCING
# orders on such a book sized on the venue's own position, toward his
# net, at his price (mirror_live._frozen_exit); off, a frozen book does
# nothing but read, as before. The environment may only turn it OFF: a
# knob may lower a rail, never raise one. A module constant read at
# import, as every switch here (a change in the environment needs a
# restart); the worker reads it through the module at call time.
MIRROR_FROZEN_EXITS = env_switch("MIRROR_FROZEN_EXITS", True)
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
# E2 (2026-09-07): 'map_winner' joins the set -- the esports map winner
# copy_sports.mirror_family_of files a 'game<N>' slug under (docs §17);
# the family's mapping is the identity pick's alone, behind the same
# switch as C3, and everything in front of a book stands as before.
MIRROR_FAMILIES = frozenset({"moneyline", "spread", "total", "prop", "btts", "exact_score",
                             "map_winner"})
# The flat tolerance in shares, ONE number for the ledger and the
# bookings (addendum section 9): a fractional venue fill can leave a
# ledger of 1e-8 that is not zero, and a book "held" by dust would never
# close; under this a ledger is flat, and a delta under it is dust that
# never books (a 1e-12 sale would otherwise book 1e-12 shares for $0).
# mirror_orders.qty is an integer, so real fills are never this small.
FLAT_TOL_SHARES = 1e-6
# A SELL the venue filled within ONE VENUE LOT past the ledger is DUST,
# not an overfill (2026-09-06 01:11:57Z, deploy dd1ed77: the standing
# row held 413.76 shares of fractional fills, mirror_books.ledger_net is
# an INTEGER column and read 414, the flatten sized its SELL off the
# integer, the venue sold 414.0, and the 0.24-share gap between a
# fractional fill and a whole-share ledger tripped the lane off as an
# overfill -- exits-only from 01:13Z on rounding). The gap between a
# fractional row and its rounded ledger is bounded by one lot, so a
# sale up to one lot past the ledger books the ledger, is counted
# (census `ledger_dust`) and never trips; a sale MORE than a lot past
# the ledger is still the overfill -- the short a signed-net venue
# would then hold -- and still freezes the book and trips the switch.
# NOT read from the environment, in either direction: a wider
# tolerance is exactly the room a real short could hide under, and
# widening it is a code change that wants a review.
SELL_DUST_SHARES = 1.0

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
                  cap_usd: float = MIRROR_NET_CAP_USD,
                  allow_short: bool = False) -> dict[str, Any]:
    """Our target in long-token shares for this book, or the reason
    there is no plan.

    `allow_short` (P2 rung S0, default False: byte-identical to P1) is
    the MIRROR_SHORTS knob as the worker read it. Only the bool True
    admits: a negative raw target is then a SHORT of the long token,
    returned SIGNED (negative), capped at $cap on the SHORT leg's price
    1 - mark by mi.target_shares, and `intent` names the book that
    holds it, ORDER_INTENT_BUY_SHORT. Anything else keeps the P1 door:
    target 0, `short_side_refused` -- the reversal path the rung plan
    keeps (knob off restores it alone).

    ratio_eff = ratio x min(1, per_fill_usd / MIRROR_ANCHOR_CLIP_USD):
    the $50 anchor sets the scale, a smaller clip scales it down, and
    nothing scales it UP. SINCE 2026-09-06 (U12b/U12c) the `ratio` the
    worker passes at OPEN is open_ratio's -- 1.0 under the small-bet
    line, else MIRROR_RATIO -- with MIRROR_CLIP_USD as the clip (at or
    over the anchor: scale 1, the ratio is stored unscaled), and on an
    open book's tick the book's STORED ratio with the anchor clip; the
    shadow's measured ratio sizes nothing live any more. THE ANCHOR IS
    NOT THE PER-FILL LANE'S CLIP: that clip rose to $250 on 2026-09-04
    and the mirror must not resize as a side effect of it.

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
    t = mi.target_shares(ratio_eff, n, m, allow_short=allow_short is True, cap_usd=cap)
    out.update({"target": int(t["target"]), "raw": t["raw"], "capped": bool(t["capped"])})
    if t["why"] == "short side not admitted":
        out["refusal"] = "short_side_refused"
    elif out["target"] < 0:
        out["intent"] = ORDER_INTENT_SHORT
    return out


# --------------------------------------------------------- the wire map
#
# P2 rung S0: the plan frame and the wire-side map (brief section 3.3).
# mi.plan stays sign-blind in LONG space -- a short book's ledger is
# NEGATIVE (long-token shares by our booking, signed), so a plan to hold
# MORE of a short is a SELL_LONG plan and a plan to cover it is a
# BUY_LONG plan. The lane maps (book intent, plan side) to what the venue
# is sent:
#
#   book   plan side  meaning                  wire intent  sell  leg
#   long   BUY_LONG   increase                 BUY_LONG     no    add
#   long   SELL_LONG  reduce / flatten         SELL_LONG    yes   reduce
#   short  SELL_LONG  increase (add to short)  BUY_SHORT    no    add
#   short  BUY_LONG   reduce / flatten         SELL_SHORT   yes   reduce
#
# The `sell` flag is what submit_fok is passed beside the book's BUY
# intent (the adapter maps a sell to the exit intent itself); the wire
# intent is what mirror_orders.intent records. A short REDUCE is
# `short_reduce_unproven` before rung S4 -- the venue has never read a
# resting SELL_SHORT back in a stated denomination -- except the whole
# book flattened by close_position when we are the slug's sole holder;
# the worker holds that rule, this map only names the wire.

def is_short(intent: Any) -> bool:
    """Is this the short book's intent? Only the exact SDK spelling of
    BUY_SHORT; anything else (None, a bool, SELL_SHORT, a typo) is not
    a short book -- and never a long one by default either: the callers
    below refuse an intent they cannot name."""
    return isinstance(intent, str) and intent == ORDER_INTENT_SHORT


def wire_side(intent: Any, side: Any) -> tuple[str, bool, str] | None:
    """(wire intent, sell flag, leg action 'add' | 'reduce') for a plan
    side on a book of this intent, per the table above; None when the
    intent is not one of the two book intents or the side is not one
    of the two plan sides."""
    if not isinstance(side, str) or side not in (BUY, SELL):
        return None
    if intent is None or intent == ORDER_INTENT:
        return (ORDER_INTENT, False, "add") if side == BUY else ("ORDER_INTENT_SELL_LONG", True, "reduce")
    if is_short(intent):
        return (ORDER_INTENT_SHORT, False, "add") if side == SELL else ("ORDER_INTENT_SELL_SHORT", True, "reduce")
    return None


def leg_action(intent: Any, side: Any) -> str | None:
    """'add' when this plan side GROWS the leg the book holds, 'reduce'
    when it shrinks it, None when either cannot be read. The booking,
    the day cap, the increase refusals and the give-back all key on the
    LEG, never on the long-space plan side alone."""
    w = wire_side(intent, side)
    return None if w is None else w[2]


def sign_flip(intent: Any, target: Any) -> bool:
    """Has his net crossed zero against this book? True when a NONZERO
    whole target carries the opposite sign to the book's leg: a positive
    target on a short book, a negative one on a long book (brief B8,
    owner default Q5 (a)). The worker then flattens the book (target 0)
    under the name `sign_flip` and opens the opposite side as a NEW
    episode once this one has closed -- and since 2026-09-06 the flip
    IS that close once the book is flat with no order open and the
    venue reads 0 (episode_close_reason; no flat wait). A target that
    is not an int, or 0, or a book whose intent cannot be read, never
    flips."""
    if isinstance(target, bool) or not isinstance(target, int) or target == 0:
        return False
    if intent is None or intent == ORDER_INTENT:
        return target < 0
    if is_short(intent):
        return target > 0
    return False


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
    # E17 (2026-09-08, PNL lane 5): the ledger of the newest CLOSED
    # mirror book on this whale and condition that still holds shares
    # (book 204: closed "standing row settled" at 18:48Z on a live
    # market with our 13 sh on the venue; he added 6,350 sh after and
    # every candidate was refused `venue_already_holds` on our own
    # shares). None -- no such book, or unread -- is the fail-closed
    # default: the venue's shares then refuse as before. Appended LAST.
    prior_episode_ledger: float | None = None
    # E17 fold (2026-09-08, review HIGH-1; Martinez 12:10Z: the flip
    # close's IOC bought 371.2 against 371 sold, the venue +0.2, the
    # INTEGER ledger 0, every later fill of his refused
    # `venue_already_holds`). The IDENTITY read by the worker: a CLOSED
    # mirror book on this whale and condition whose standing row is lane
    # 'mirror' on the market's asset exists -- the venue's sub-share
    # residual beside it is the mirror's own dust. Honoured only as the
    # bool True, and only with |venue| under mi.VENUE_LEDGER_TOL_SHARES
    # (venue_dust_is_ours); False (not read) refuses as before. Appended
    # LAST, after prior_episode_ledger.
    venue_dust_ours: bool | None = False
    # E19 (2026-09-08, PNL lane 8; Martinez, books 529/534): the worker
    # sized this candidate's target on the SMALLER of two readings of
    # his net that agree on the side -- his fills' net and this tick's
    # per-market venue read (smaller_reading) -- so the drift clause may
    # admit past MIRROR_DRIFT_MAX, and only beside snap_market_fresh
    # True; the drift number itself is never touched. False, the
    # fail-closed default: `drift` as before. Appended LAST.
    drift_sized_smaller: bool = False


def venue_dust_is_ours(venue_net: Any, ours: Any) -> bool:
    """E17 fold (HIGH-1): the venue's figure on the slug is a sub-share
    residual -- non-zero, |venue| < mi.VENUE_LEDGER_TOL_SHARES (the D1
    dust the freeze reads as agreement with a ledger of 0) -- AND the
    worker read the identity (`ours` is the bool True: a closed mirror
    book on the market whose standing row is lane 'mirror' on the
    asset). Any residual at or over the tolerance, an unreadable
    figure, the identity not read: False -- `venue_already_holds`
    stands. Never a magnitude match against a ledger; never loosens
    admission's `vn != 0.0`. Pure."""
    vn = _num(venue_net)
    if vn is None or ours is not True:
        return False
    return vn != 0.0 and abs(vn) < float(mi.VENUE_LEDGER_TOL_SHARES)


def smaller_reading(fills_net: Any, venue_net: Any) -> float | None:
    """E19: the SIGNED smaller magnitude of two readings of his net on
    one market, or None -- the reading a candidate may be sized on when
    the two disagree past MIRROR_DRIFT_MAX (owner 2026-09-08: "Why did
    we only have 12c on Martinez. I see RN1 had 25000 on Martinez": his
    fills read 11,974.6, the venue's own per-market snapshot 25,104,
    drift 0.523, and every window for two hours was refused `drift`
    while both readings said LONG).

    Both readings must be numbers (_num: None, a bool, a string, NaN,
    an infinity are no reading), both non-zero, and of ONE sign: two
    readings that disagree on which side he is on justify holding
    neither (None -- the worker keeps the `drift` refusal). The smaller
    magnitude never buys more than either reading says he holds; on a
    short (both negative) it is the smaller short, the same rule."""
    a, b = _num(fills_net), _num(venue_net)
    if a is None or b is None or a == 0.0 or b == 0.0:
        return None
    if (a < 0.0) != (b < 0.0):
        return None
    m = min(abs(a), abs(b))
    return -m if a < 0.0 else m


def prior_episode_adoption(venue_net: Any, prior_ledger: Any) -> bool:
    """E17: the venue's shares on the slug ARE a closed mirror book's
    ledger -- both numbers, the prior a held position (|prior| >=
    mi.VENUE_LEDGER_TOL_SHARES), the same sign, and |venue - prior| <=
    mi.VENUE_LEDGER_TOL_SHARES (the D1 dust, the tolerance the freeze
    reads the same pair at). Any other magnitude, a sign against, a
    flat prior, an unreadable figure: False -- `venue_already_holds`
    stands. Pure."""
    vn, pr = _num(venue_net), _num(prior_ledger)
    if vn is None or pr is None:
        return False
    tol = float(mi.VENUE_LEDGER_TOL_SHARES)
    if abs(pr) < tol or (pr > 0) != (vn > 0):
        return False
    return abs(vn - pr) <= tol


def adopted_block(block: Any, adopted: Any, ratio: Any) -> float | None:
    """E17: the block the adopted shares already cover. The prior
    episode bought `adopted` shares of his pre-close net; on the new
    episode that net is the block (E12: fills older than the close
    clock), and the part the adopted shares stand for -- adopted /
    ratio on the block's axis -- is already ours, so it is taken off
    the block, clamped between 0 and the block (never past the block,
    never across zero). An unreadable adopted figure or ratio leaves
    the block whole (fail closed: a larger block is less flow, nothing
    more bought); a block of None stays None."""
    b = _num(block)
    if b is None:
        return None
    a, r = _num(adopted), _num(ratio)
    if a is None or r is None or r <= 0.0:
        return b
    covered = a / r
    if b >= 0.0:
        return max(0.0, min(b, b - max(0.0, covered)))
    return min(0.0, max(b, b - min(0.0, covered)))


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
    # E17: the venue's shares that are exactly a closed mirror book's
    # ledger on this market are the prior episode's, adopted by the new
    # one (prior_episode_adoption; the worker names `adopted_prior_episode`
    # and carries them as the opening ledger); any other magnitude refuses.
    # E17 fold (HIGH-1): a sub-share residual beside a closed mirror book
    # on the market (venue_dust_is_ours: the identity read True AND
    # |venue| under the tolerance) is the mirror's own dust and admits;
    # the worker names `venue_dust_ours` and opens the book at ledger 0
    if (vn != 0.0 and not prior_episode_adoption(vn, f.prior_episode_ledger)
            and not venue_dust_is_ours(vn, f.venue_dust_ours)):
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
        # E19 (PNL lane 8): past the max the candidate is admitted ONLY
        # when the worker sized its target on the smaller of the two
        # readings (drift_sized_smaller, smaller_reading) AND the reading
        # that disagreed was this tick's per-market read (snap_market_fresh
        # True): the drift number is the real one, never a stand-in, and
        # an unreadable or negative drift refuses whatever the flag says
        if not (d is not None and d >= 0.0 and f.drift_sized_smaller is True
                and f.snap_market_fresh is True):
            return "drift"
    # the book counts: an UNREADABLE count refuses under its own name
    # (fail closed, never "no cap"); a FINITE cap bites as max_books;
    # an unbounded cap (the default since 2026-09-06) never does
    live, today = _count(f.books_live), _count(f.opened_today)
    if live is None or today is None:
        return "books_unreadable"
    live_cap, day_cap = _bounded(MIRROR_MAX_LIVE_BOOKS), _bounded(MIRROR_MAX_BOOKS_PER_DAY)
    if (live_cap is not None and live >= live_cap) or (day_cap is not None and today >= day_cap):
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


def band_cent(his_px: float | None, band: float | None = None) -> float | None:
    """THE ENTRY BAND'S CENT (E14, FILL lane 2): buy_wire(his price +
    band) -- floor-to-cent of his UNROUNDED price plus the band, so the
    IOC limited here can never fill more than the band over what he
    paid (his 0.471 + 0.01 = 0.481 -> 0.48; his 0.479 -> 0.489 -> 0.48:
    the same cent, at most 0.9c over him). `band` None reads
    MIRROR_TAKE_BAND at call time. None -- no band, the rest as today --
    when his price is not a price in (0, 1) or a bool, when the band is
    unreadable, negative or zero, when the sum has no cent on the
    ladder, and when the cent is not STRICTLY above his own cent
    (buy_wire(his)): a band under a cent is no band (his 0.472 + 0.005
    = 0.477 floors to his own 0.47), and at 0 the lane is off."""
    h = _num(his_px)
    if h is None or not (0.0 < h < 1.0):
        return None
    b = _num(MIRROR_TAKE_BAND if band is None else band)
    if b is None or b <= 0.0:
        return None
    base = buy_wire(h)
    if base is None:
        return None
    w = buy_wire(h + b)
    if w is None or w <= base + 1e-9:
        return None
    return w


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
               mirror_day: float | None, intent: str | None = None) -> int:
    """The BUY quantity the room allows: min(qty, floor(min(per-order
    clip, sleeve day room, sleeve total room, mirror day room) / cost
    per share)). Under one share is 0, which the worker names
    `over_room`. Any room, quantity or wire that could not be read as a
    finite number (a bool, a string, NaN, an infinity) is no room, the
    wire must be a cent on the ladder (0.01 to 0.99: a sub-cent wire
    divides a room into an infinity of shares), and a quotient that is
    not finite is no room: 0, never a raise.

    THE COST PER SHARE IS THE WIRE ON A LONG AND ONE MINUS IT ON A
    SHORT (P2 rung S0, brief D3): a BUY_SHORT's wire is the CONTRACT
    price the venue is sent (le.wire_limit: "sell at >= 0.78 means pay
    <= 0.22"), and the collateral it ties up is (1 - wire) a share.
    Dividing a short's room by the wire under-sized it ~30x against
    its collateral on a longshot (60 shares for $50 at a 0.83 wire,
    where the true 0.17 cost buys 294). `intent` is the BOOK's intent;
    anything but the exact BUY_SHORT spelling is the long formula, so
    every existing caller is byte-identical. Pure arithmetic: the
    executor's short model being disarmed is refused upstream by name
    (`short_model_disarmed`), never inherited here."""
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
    if is_short(intent):
        w = round(1.0 - w, 6)
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
    side: str                    # BUY_LONG / SELL_LONG (the PLAN side, long space)
    wire: float | None
    qty: int
    leaves: float | None         # qty - filled, by the last order_status read
    placed_at: float | None      # epoch seconds
    # the WIRE intent the order was sent with (mirror_orders.intent, P2
    # rung S0). Appended LAST so a positional construction keeps its
    # meaning; None is "not read" and compares as nothing (doc:491)
    intent: str | None = None


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


def rest_decision(order: OpenOrder, p: Plan | None, now: float,
                  ttl_s: float = MIRROR_REST_TTL_S,
                  cancel_reason: str | None = None,
                  wire: float | None | object = _FROM_PLAN,
                  intent: str | None = None,
                  stands: bool = False,
                  min_life_s: float = MIRROR_REST_MIN_LIFE_S,
                  entry: bool | None = None) -> tuple[str, dict]:
    """keep_or_replace's verdict WITH ITS CAUSE: (verdict, detail).
    `detail["cause"]` names the clause that decided -- 'cancel_reason',
    'unreadable', 'no_plan', 'future', 'ttl', 'side', 'intent',
    'no_price', 'cent', 'under_one_share', 'qty', 'same' -- and, under
    THE REST-LIFE FLOOR (E18), 'min_life' with `kept_min_life` True,
    `rest_age_s`, `floor_s`, `cent_moved` and, when the plan's quantity
    GREW past the rest's leaves, `add_pending` = {qty, wire, since}:
    the growth the worker re-plans once the floor has passed (`since`
    is the tick's clock that first held it). A quantity that FELL is
    his exit and never waits: 'replace' with cause 'qty' at any age
    (the review's CRITICAL-1, folded 2026-09-08). The floor applies to
    an ENTRY rest (`stands` is not True) younger than `min_life_s`
    whose side and intent are unchanged and whose cent moved under
    REST_MIN_LIFE_CENT_MOVE in ONE direction only -- the rest's wire at
    or UNDER his new cent on a BUY, at or OVER it on a SELL (the
    review's MEDIUM-2, folded 2026-09-08 as the mandate's reading,
    "entries at his cent": a rest is never left a cent past him for
    the floor's life; his cent moving past the rest replaces at once,
    as before the floor) -- and never when `entry` is False (the worker
    passes the leg action: an UNPRICED reduce rest -- he gave no exit
    price, the rest sits at the ask -- is an exit, not an entry, and
    keeps E4's rule whole); `min_life_s` can only LENGTHEN the floor
    (max(min_life_s, MIRROR_REST_MIN_LIFE_S); unreadable is the
    constant), the mirror of take_allowed's wait. Everything else is
    keep_or_replace's docstring, byte for byte."""
    if cancel_reason is not None:
        name = cancel_reason if isinstance(cancel_reason, str) and cancel_reason.strip() else "cancel_unnamed"
        return name, {"cause": "cancel_reason"}
    if not isinstance(order, OpenOrder) or (p is not None and not isinstance(p, Plan)):
        return "replace", {"cause": "unreadable"}
    if p is None or p.side is None:
        return plan_reason_key(p.reason if p is not None else "no_plan"), {"cause": "no_plan"}
    placed, t, ttl = _num(order.placed_at), _num(now), _num(ttl_s)
    if placed is None or t is None or ttl is None:
        return "replace", {"cause": "unreadable"}
    ttl = min(ttl, float(MIRROR_REST_TTL_S))
    age = t - placed
    if age < 0:
        return "replace", {"cause": "future"}
    if age >= ttl and stands is not True:
        return "replace", {"cause": "ttl", "rest_age_s": age}
    if (not isinstance(p.side, str) or not isinstance(order.side, str)
            or p.side not in (BUY, SELL) or p.side != order.side):
        return "replace", {"cause": "side"}
    oi = getattr(order, "intent", None)
    if isinstance(intent, str) and isinstance(oi, str) and intent != oi:
        return "replace", {"cause": "intent"}
    pw = _cent(plan_wire(p) if wire is _FROM_PLAN else wire)
    if pw is None:
        return "no_price", {"cause": "no_price"}
    ow = _cent(order.wire)
    if ow is None:
        return "replace", {"cause": "cent"}
    moved = round(abs(ow - pw), 6)
    # THE FLOOR (E18): the caller's floor can only lengthen the constant
    floor = _num(min_life_s)
    floor = float(MIRROR_REST_MIN_LIFE_S) if floor is None else max(floor, float(MIRROR_REST_MIN_LIFE_S))
    young = stands is not True and entry is not False and age < floor
    # ONE DIRECTION ONLY (the E18 review's MEDIUM-2, folded 2026-09-08 --
    # the mandate's reading, "entries at his cent"): a young rest stands
    # over a sub-2c move only while its wire is at or UNDER his new cent
    # on a BUY (at or OVER it on a SELL) -- the queue kept when his cent
    # rose (533: 0.43 -> 0.48 -> 0.49). His cent moving the other way
    # would leave the rest a cent PAST him for the floor's life, so it
    # replaces at once, as before the floor
    not_past_him = ow <= pw if p.side == BUY else ow >= pw
    if moved >= 0.01 and not (young and moved < REST_MIN_LIFE_CENT_MOVE and not_past_him):
        return "replace", {"cause": "cent", "cent_moved": moved}
    leaves, q = _num(order.leaves), _num(p.qty)
    if leaves is None or q is None:
        return "replace", {"cause": "unreadable"}
    if q < 1:
        return "replace", {"cause": "under_one_share"}
    diff = abs(leaves - q)
    if diff < 1.0 or diff <= MIN_MOVE_FRAC * abs(q):
        if moved >= 0.01:
            # the cent moved under two cents on a young rest: kept by the floor
            return "keep", {"cause": "min_life", "kept_min_life": True, "rest_age_s": age,
                            "floor_s": floor, "cent_moved": moved}
        return "keep", {"cause": "same"}
    if young and q > leaves:
        # the quantity GREW (alone, or with a sub-2c cent move) on a young
        # rest: kept, the growth carried for the tick past the floor. A
        # FALL never waits (the E18 review's CRITICAL-1, folded
        # 2026-09-08): a target under the rest's leaves is his exit, and
        # a rest kept over it would stand past his proportion for the
        # floor's life -- it replaces at once, as before the floor
        return "keep", {"cause": "min_life", "kept_min_life": True, "rest_age_s": age,
                        "floor_s": floor, "cent_moved": moved,
                        "add_pending": {"qty": int(q), "wire": pw, "since": float(t)}}
    return "replace", {"cause": "qty"}


def keep_or_replace(order: OpenOrder, p: Plan | None, now: float,
                    ttl_s: float = MIRROR_REST_TTL_S,
                    cancel_reason: str | None = None,
                    wire: float | None | object = _FROM_PLAN,
                    intent: str | None = None,
                    stands: bool = False,
                    min_life_s: float = MIRROR_REST_MIN_LIFE_S,
                    entry: bool | None = None) -> str:
    """What to do with the order already resting on this book.

    `intent` (P2 rung S0, brief B6) is the WIRE intent the plan would
    be sent with now -- wire_side(book intent, plan side)[0] -- and is
    compared to the resting order's own (OpenOrder.intent) when BOTH
    were read: a rest carrying another intent is not this plan's rest,
    'replace'. Either side None is not compared (every P1 caller).

    `stands` (E4: an EXIT rest at his cent) turns the TTL off: a rest
    past MIRROR_REST_TTL_S at the same side, cent and quantity is
    'keep', because cancelling it to re-place the same cent is a
    wasted replace (the worker names it `requote_same_wire`); every
    other clause -- the side, the intent, the cent, the quantity, an
    unreadable fact, a placement in the future -- decides exactly as
    before. Entries never pass it.

    THE REST-LIFE FLOOR (E18, 2026-09-08; `min_life_s`, the constant
    MIRROR_REST_MIN_LIFE_S, env may only lengthen): an ENTRY rest
    (`stands` is not True) younger than the floor is 'keep' when the
    side and the intent are unchanged and the cent moved under
    REST_MIN_LIFE_CENT_MOVE (2c) with the rest still at or UNDER his
    new cent on a BUY (at or OVER it on a SELL; the review's MEDIUM-2,
    folded 2026-09-08 -- the mandate's "entries at his cent": his cent
    moving past the rest replaces at once, as before) -- a quantity
    GROWTH is 'keep' too, and rest_decision carries it as `add_pending`
    for the worker to re-plan once the floor has passed; a quantity
    that FELL is his exit and replaces at once (the review's
    CRITICAL-1, folded 2026-09-08). A cent move of 2c or more, a side
    or intent change, the TTL and an unreadable fact are 'replace' as
    before; a rest at or past the floor decides exactly as before. The
    floor is a WAIT (a caller's `min_life_s` can only
    lengthen it); exits never read it -- `stands` (a priced exit) and
    `entry` False (the worker's word for any reduce rest) both keep
    the floor off.

      'keep'      same side, same cent, leaves within a share (or within
                  MIN_MOVE_FRAC of the plan's quantity), younger than
                  the TTL -- the worker names it `open_order_pending`;
                  or an entry rest under the floor (above)
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
    return rest_decision(order, p, now, ttl_s=ttl_s, cancel_reason=cancel_reason, wire=wire,
                         intent=intent, stands=stands, min_life_s=min_life_s, entry=entry)[0]


_REPLACE_DECISIONS = {"ttl": "ttl", "cent": "replace_cent", "qty": "replace_qty",
                      "side": "replace_side", "intent": "replace_side"}


def replace_decision(detail: dict | None) -> str:
    """The `decision` the cancelled row records (mirror_orders.decision,
    migration 059) for a replace: 'ttl', 'replace_cent', 'replace_qty',
    'replace_side' (a side or intent change), else 'replace_unread' (an
    unreadable fact, a placement in the future, a plan under a share) --
    never a guess from a detail that names no cause."""
    cause = detail.get("cause") if isinstance(detail, dict) else None
    return _REPLACE_DECISIONS.get(str(cause), "replace_unread")


def order_decision(action: str | None, is_take: bool, short_cover: bool,
                   in_band: bool = False) -> str:
    """The `decision` an order row records at its INSERT (E18; migration
    059): 'cover' for a short book's buy-back (its rest or its IOC),
    'take_in_band' for an ENTRY's IOC sent at the band cent (E14, FILL
    lane 2: `in_band` True on an add's IOC -- the word 059 reserved,
    written here and nowhere else; a cover wins over it), 'take' for any
    other IOC, 'exit_rest' for a rest that shrinks the leg, 'rest' for an
    entry's. FILL lane 3 (the exit band, inert at its default): a
    reduce's IOC with `in_band` True is 'exit_take_in_band' and a short
    cover's IOC with it 'cover_in_band' -- the two words a band IOC's
    row carries so its cents past his price are readable per row; the
    band words are written on an IOC alone (`is_take`), on a readable
    leg action alone, and only for `in_band` exactly True -- anything
    else lands on the word the row carried before the band."""
    if short_cover:
        return "cover_in_band" if (in_band is True and is_take) else "cover"
    if is_take:
        if in_band is True and action == "add":
            return "take_in_band"
        if in_band is True and action == "reduce":
            return "exit_take_in_band"
        return "take"
    return "exit_rest" if action == "reduce" else "rest"


# -------------------------------------------------------------- the take

def at_or_through(side: str, bid: float | None, ask: float | None,
                  wire: float | None) -> bool:
    """Is the book at or through OUR resting level right now? A BUY is
    marketable when the ask is at or under our cent, a SELL when the
    bid is at or over it. The wire must be a cent on the ladder
    (0.01 to 0.99, a number, not a bool) and so must the quote: a
    missing, unreadable or impossible quote (0.0, -0.0, 1e-12, 1.0,
    1.5, 1e308) is not at anything, so a take never fires on a
    non-quote.

    ON A SHORT BOOK THE PLAN SIDE IS THE WIRE'S SIDE IN THE WIRE'S OWN
    SPACE (P2 rung S0, brief B6 / 3.3): a BUY_SHORT rest is the SELL_LONG
    plan's cent in contract space -- the contract sold at or above it --
    so it is marketable when the bid is at or over it, which in the
    short leg's own terms is `short_ask = 1 - bid <= 1 - wire`, the
    shadow's judge-short-sell rule (mirror_shadow, `/* judge-short-sell
    */`); a SELL_SHORT (a cover, the BUY_LONG plan) when the ask is at
    or under it. No conversion, no second rule: `side` is the plan side."""
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


def take_in_band(bid: float | None, ask: float | None, his_cent: float | None,
                 band_cent: float | None) -> bool:
    """Is the ask STRICTLY above his cent and at or under the band cent
    (E14, FILL lane 2)? `not at_or_through(BUY, his_cent) and
    at_or_through(BUY, band_cent)`: an ask at or under his cent is
    today's take, which fires first and is never this; an ask above
    the band cent is the rest. Both cents must be readable numbers on
    the ladder with the band cent above his (else False: no band on a
    cent nobody read), and at_or_through's own quote rules hold -- a
    missing, unreadable or impossible ask is never in band. The bid is
    not read."""
    hc, bc = _num(his_cent), _num(band_cent)
    if hc is None or bc is None or not (0.01 <= hc <= 0.99) or not (bc > hc + 1e-9):
        return False
    return (not at_or_through(BUY, bid, ask, hc)) and at_or_through(BUY, bid, ask, bc)


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


def exit_terms(side: str, his_px: float | None,
               tol: float | None = None, band: float | None = None) -> dict[str, float] | None:
    """THE EXIT'S PRICES FROM HIS EXIT PRICE (E4, owner order 2026-09-06
    "we should exit when he exits at his price or within 1c variance
    (tolerance)"), for the one figure the worker already hands the exit
    plan as his level (`his_px`: his reduce fill on the token, or for a
    short cover his buy-back in the long token's space, as _his_level
    reads it), unrounded.

      SELL (a long book's reduce or flatten):
        px      his price
        floor   his price - tol: the worst price the exit may fill at
        rest    the cent the rest goes at, ceil(his price) capped at
                0.99 (sell_wire) -- HIS cent, never max(his, ask): the
                ask no longer lifts the rest above him
        take    the lowest cent AT OR ABOVE the floor (sell_wire of the
                floor, never under 0.01): the IOC's limit. The take
                fires when the bid is at or through it
                (at_or_through(SELL, bid, ask, take)); a SELL IOC at
                that limit fills at the bid, at or above the floor.
                The cent is taken ABOVE the floor, not under it: a
                floor off a cent (his 0.4595 - 0.01 = 0.4495) floored
                to the cent (0.44) would admit a fill 1.95c under him,
                past the tolerance; ceiled (0.45) every fill is inside
                it, at any precision. On a cent floor the two agree.
      BUY (a short book's cover, in long space; S4 makes it a priced
      order through the same machinery as the SELL):
        px      his buy-back price
        ceiling his price + tol
        cover   the highest cent AT OR UNDER the ceiling (buy_wire of
                the ceiling, capped at 0.99): the IOC's limit, sent
                only while the ask is at or under it
                (at_or_through(BUY, bid, ask, cover)); `take` is the
                same cent, the SELL's name for it
        rest    the cent the cover RESTS at, floor(his price) (buy_wire,
                never under 0.01): never above him, and under the ask
                whenever the take did not fire.

      THE BAND (FILL lane 3, 2026-09-08; owner decision D2 keeps the
      exit within 1c, so the band lands INERT): `band` is read at call
      time like `tol` (MIRROR_EXIT_TAKE_BAND with none given) and the
      effective band is b = max(tol, band) when the band is above the
      tolerance's own code default (EXIT_BAND_INERT_AT, 0.01) and b = tol
      otherwise, so a band at or under that figure is no band at all --
      and a tolerance the environment lowered tightens the take exactly
      as before (E4 review LOW-6). SELL adds
        band_floor  his price - b
        take_band   the lowest cent AT OR ABOVE band_floor (the same
                    ceiled reading as `take`, so every fill is inside b
                    of him at any precision); equal to `take` whenever
                    the band is inert
      and BUY adds
        band_ceiling  his price + b
        cover_band    the highest cent AT OR UNDER band_ceiling (capped
                      at 0.99); equal to `cover` whenever inert.
      `take` / `rest` / `cover` are byte for byte E4's whatever the
      band reads. An unreadable, negative or bool band is the
      tolerance (inert), never a wider cent.

    None when he gave no exit price -- `his_px` missing, not a number,
    a bool, or off (0, 1) -- when the side is not BUY or SELL, or when
    the tolerance is unreadable or negative: the rule cannot apply and
    the worker keeps the plan's old behaviour under `exit_px_src:
    'none'`, never a guessed level. Pure; the tolerance is a parameter
    so the property tests sweep it, and with none given the module
    constant is read AT CALL TIME (E4 review, LOW-6: a default bound at
    import would neither see the environment's tightening nor a test's
    monkeypatch through the worker)."""
    h, tl = _num(his_px), _num(MIRROR_EXIT_TOL if tol is None else tol)
    if h is None or not (0.0 < h < 1.0) or tl is None or tl < 0.0 or isinstance(his_px, bool):
        return None
    if not isinstance(side, str):
        return None
    bd = _num(MIRROR_EXIT_TAKE_BAND if band is None else band)
    if bd is None or bd < 0.0 or isinstance(band, bool) or bd <= float(EXIT_BAND_INERT_AT) + 1e-12:
        bd = tl                         # unreadable, or at / under the tolerance's default: inert
    b = max(tl, bd)
    if side == SELL:
        floor = h - tl
        take = sell_wire(max(floor, 0.01))
        rest = sell_wire(h)
        if take is None or rest is None:
            return None
        band_floor = h - b
        take_band = sell_wire(max(band_floor, 0.01))
        if take_band is None or take_band > take + 1e-9:
            band_floor, take_band = floor, take       # never a cent above the take: inert
        return {"px": h, "floor": floor, "rest": rest, "take": take,
                "band_floor": band_floor, "take_band": take_band}
    if side == BUY:
        ceiling = h + tl
        cover = buy_wire(min(ceiling, 0.99))
        rest = buy_wire(max(h, 0.01))
        if cover is None or rest is None:
            return None
        band_ceiling = h + b
        cover_band = buy_wire(min(band_ceiling, 0.99))
        if cover_band is None or cover_band < cover - 1e-9:
            band_ceiling, cover_band = ceiling, cover   # never a cent under the cover: inert
        return {"px": h, "ceiling": ceiling, "cover": cover, "rest": rest, "take": cover,
                "band_ceiling": band_ceiling, "cover_band": cover_band}
    return None


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
    BY OUR BOOKING, SIGNED (P2 rung S0, brief 3.1): positive on a long
    book, negative on a short one -- the same sign the shadow's
    ledger_net and the venue's netPosition carry, so venue - ledger
    stays one signed subtraction. avg_cost the weighted average of the
    buys behind it, in CONTRACT price on either sign (what the venue
    returns; le:3212); peak_exposure_usd the STAKE the record grades
    against (max over life of the LEG x its cost a share: avg on a
    long, 1 - avg on a short); realized_pnl every sale, partials
    included, which the global breaker cannot see. `intent` is the
    book's own (mirror_books.intent), fixed at open: None or BUY_LONG
    is a long book, BUY_SHORT a short one. Leg space is a VIEW --
    leg = |ledger_net|, sign = -1 on a short -- produced by _state_nums;
    everything that clamps a quantity clamps on the leg."""
    ledger_net: float = 0.0
    avg_cost: float | None = None
    gross_buy_usd: float = 0.0
    gross_sell_usd: float = 0.0
    peak_exposure_usd: float = 0.0
    realized_pnl: float = 0.0
    intent: str | None = None


class Booking(NamedTuple):
    state: BookState
    booked: float            # shares actually booked
    usd: float               # cash of the booked shares
    realized: float | None   # SELL only; None when nothing was booked
    overfill: bool           # a SELL MORE than SELL_DUST_SHARES past what the ledger held
    refusal: str | None      # 'bad_delta' | 'nothing_to_book' | 'bad_price' | 'bad_usd'
    #                          | 'bad_state' | 'avg_cost_unknown'
    dust: float = 0.0        # SELL only: the shares sold past the ledger when that gap is
    #                          within SELL_DUST_SHARES (rounding between a fractional fill
    #                          and a whole-share ledger); 0.0 otherwise, and 0.0 on an overfill


def _px(px: Any) -> float | None:
    p = _num(px)
    return p if p is not None and 0.0 < p < 1.0 else None


def _state_nums(state: BookState) -> BookState | None:
    """The state's columns as finite numbers (None as 0 for the sums,
    None kept for avg_cost), or None when any column is not a number
    or the ledger's sign is not the book's: a long book cannot be
    short, a short book (intent BUY_SHORT, P2 rung S0, brief B1) cannot
    be long, and a NaN ledger is not a ledger. avg_cost that is not a
    PRICE in (0, 1) -- a token never cost 5.0 or -0.5 -- reads as
    unknown (None), which the booking names `avg_cost_unknown`
    wherever shares are held against it. Something that is not a
    BookState at all (None, a dict, a number) is no book -- never an
    EMPTY one (review finding: book_buy(None, 5, 0.5) booked 5). An
    intent that is neither None, BUY_LONG nor BUY_SHORT is no book."""
    if not isinstance(state, BookState):
        return None
    intent = getattr(state, "intent", None)
    if intent is not None and intent != ORDER_INTENT and not is_short(intent):
        return None
    vals: dict[str, float] = {}
    for k in ("ledger_net", "gross_buy_usd", "gross_sell_usd", "peak_exposure_usd", "realized_pnl"):
        v = getattr(state, k, None)
        n = 0.0 if v is None else _num(v)
        if n is None:
            return None
        vals[k] = n
    if is_short(intent):
        if vals["ledger_net"] > 0:
            return None
    elif vals["ledger_net"] < 0:
        return None
    ac = getattr(state, "avg_cost", None)
    return BookState(avg_cost=None if ac is None else _px(ac), intent=intent, **vals)


def _leg(st: BookState) -> tuple[float, float]:
    """(sign, leg) of a _state_nums state: the leg is |ledger_net|, the
    sign -1 on a short book and +1 on a long one, so ledger = sign x
    leg. The one place leg space is produced from the signed ledger."""
    sign = -1.0 if is_short(getattr(st, "intent", None)) else 1.0
    return sign, abs(st.ledger_net)


def _cost_px(px: float, intent: Any) -> float:
    """The cost of ONE share at contract price `px` for a book of this
    intent: px on a long, 1 - px on a short (le.cost_per_share, restated
    as arithmetic because this module imports no executor; the
    executor's disarm switch is refused upstream by name, never
    inherited here)."""
    return round(1.0 - px, 6) if is_short(intent) else px


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
    cash: float | None = None
    if usd is not None:
        c = _num(usd)
        if c is None or c < 0:
            return Booking(state, 0.0, 0.0, None, False, "bad_usd")
        cash = c
    st = _state_nums(state)
    if st is None:
        return Booking(state, 0.0, 0.0, None, False, "bad_state")
    if cash is None:
        # the identity: the leg's cost a share (the contract price on
        # a long, one minus it on a short -- P2 rung S0, brief A5)
        cash = d * _cost_px(p, st.intent)
    # LEG SPACE (P2 rung S0, brief B1-B2): a buy GROWS the leg the book
    # holds -- more long-token shares on a long book, more short-leg
    # shares on a short one, whose ledger goes further NEGATIVE. The
    # average is the contract price on either sign; the peak is the
    # leg's stake, leg x its cost a share
    sign, leg0 = _leg(st)
    leg1 = leg0 + d
    if leg0 > 0:
        if st.avg_cost is None:
            return Booking(state, 0.0, 0.0, None, False, "avg_cost_unknown")
        prior = st.avg_cost * leg0
    else:
        prior = 0.0
    avg = round((prior + p * d) / leg1, 6)
    peak = max(st.peak_exposure_usd, round(leg1 * _cost_px(avg, st.intent), 4))
    new = replace(st, ledger_net=sign * leg1, avg_cost=avg,
                  gross_buy_usd=round(st.gross_buy_usd + cash, 4),
                  peak_exposure_usd=peak)
    if _state_nums(new) is None or _px(avg) is None or not math.isfinite(cash):
        return Booking(state, 0.0, 0.0, None, False, "bad_state")
    return Booking(new, d, round(cash, 4), None, False, None)


def book_sell(state: BookState, delta_shares: float, px: float | None) -> Booking:
    """Book a SELL fill of `delta_shares` at `px`. Same cursor contract
    and the same purity as book_buy.

    booked = min(delta, ledger_net); realized = (px - avg_cost) x
    booked, the long formula (le.realized_pnl for BUY_LONG). On a SHORT
    book (P2 rung S0) the same in LEG space: the ledger is signed, the
    leg is its magnitude, realized is (avg_cost - px) x booked and the
    cash is the leg's proceeds a share (_cost_px). An
    OVERFILL -- the venue sold more than the ledger held, by MORE THAN
    SELL_DUST_SHARES -- books the ledger and flags it: a sale past zero
    on a signed-net venue is a SHORT, and the worker freezes the book
    and trips mirror_live off with the receipt. A sale past the ledger
    by up to SELL_DUST_SHARES is DUST (2026-09-06): the ledger column
    is whole shares and the standing row is fractional, so a flatten
    sized off the ledger can sell a fraction of a lot more than the
    ledger holds; `dust` carries that gap, booked stays min(delta,
    ledger_net), overfill stays False, and the worker counts it under
    `ledger_dust` with no freeze and no trip. A ledger under
    FLAT_TOL_SHARES is flat: nothing books onto it (booked 0, no
    refusal, the overfill or dust reading still made when the sale
    was real). The average cost is untouched by a sale; a book sold
    to zero keeps it until the next buy resets it.

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
    # LEG SPACE (P2 rung S0, brief B2, E1): a sale SHRINKS the leg the
    # book holds toward zero -- from above on a long book, from below
    # on a short one -- and an overfill is a sale past zero OF THE LEG
    # (|booked| > |ledger|) by more than SELL_DUST_SHARES; a gap of up
    # to SELL_DUST_SHARES is dust (U11), on either leg. Realized on a
    # short is the long formula with the sign flipped, (avg - px) x
    # booked (le.realized_pnl: "e - x"); the cash is the leg's proceeds
    # a share
    sign, leg0 = _leg(st)
    booked = min(d, leg0)
    over = d - leg0
    overfill = over > SELL_DUST_SHARES
    dust = round(over, 6) if 0.0 < over <= SELL_DUST_SHARES else 0.0
    if booked < FLAT_TOL_SHARES:
        return Booking(state, 0.0, 0.0, None, overfill, None, dust)
    if st.avg_cost is None:
        return Booking(state, 0.0, 0.0, None, overfill, "avg_cost_unknown", dust)
    per = (st.avg_cost - p) if is_short(st.intent) else (p - st.avg_cost)
    realized = round(per * booked, 4)
    cash = round(booked * _cost_px(p, st.intent), 4)
    new = replace(st, ledger_net=sign * (leg0 - booked),
                  gross_sell_usd=round(st.gross_sell_usd + cash, 4),
                  realized_pnl=round(st.realized_pnl + realized, 4))
    if _state_nums(new) is None or not math.isfinite(realized) or not math.isfinite(cash):
        return Booking(state, 0.0, 0.0, None, overfill, "bad_state", dust)
    return Booking(new, booked, cash, realized, overfill, None, dust)


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
                         flat_close_s: float = MIRROR_FLAT_CLOSE_S,
                         sign_flipped: bool | None = None) -> str:
    """Whether this episode closes now and how -- or, by name, why not.

    `sign_flipped` (P2 rung S0, brief B8; owner default Q5 (a)): the
    bool True says his net has crossed zero against this book, so the
    book is flattening to open the opposite side as a NEW episode. It
    names the 'held' reading `sign_flip` while shares are still held;
    once flat -- and with no order open -- the flip IS the close: the
    episode closes now, without the flat wait, so the opposite side's
    book can open on the next tick (2026-09-06, owner 19:33Z "I need
    more trades firing in the mirror sleeve": his +$18.7k Medvedev
    flip sat behind a flat short book for the hour the wait would
    have cost). The flat wait exists so a book he may re-buy is not
    closed and reopened for nothing; a flip is the opposite reading --
    his net is materially the other sign at the ratio (sign_flip()
    reads a NONZERO whole target), so the next episode is the other
    side, never this one. Anything but the bool True is not a flip.

      'sign_flip'        NOT YET: shares still held on a book his net
                         has crossed against; the flatten is in flight

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
                         resolved; he has LEFT (vanish confirmed); his
                         net has FLIPPED against this book (sign_flipped
                         True); the book sat flat at target 0 for
                         `flat_close_s`

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
        return "sign_flip" if sign_flipped is True else "held"
    due = (market_closed_or_resolved is True or vanished_confirmed is True
           or sign_flipped is True)
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
    "ORDER_INTENT", "ORDER_INTENT_SHORT", "WIRE_INTENTS", "BUY", "SELL",
    "ORDER_STATE_REJECTED", "EXECUTION_TYPE_REJECTED",
    "capped_env", "min_wait_env", "unbounded_env", "env_switch", "MIRROR_SHORTS",
    "column_missing", "shorts_effective", "MIRROR_SHORT_MAX_SHARES",
    "is_short", "wire_side", "leg_action", "sign_flip",
    "MIRROR_NET_CAP_FLOOR_USD", "MIRROR_RATIO", "MIRROR_CLIP_USD",
    "MIRROR_SMALL_BET_USD", "open_ratio", "step_ratio", "MIRROR_MIN_ORDER_USD",
    "MIRROR_CATCHUP_TOL_CENTS", "MIRROR_CATCHUP_PCT", "MIRROR_CATCHUP_MAX_CENTS", "open_catchup",
    "LIVE_SIDE_PRICE_BAND_MAX",
    "MIRROR_NET_CAP_USD", "MIRROR_MAX_LIVE_BOOKS", "MIRROR_MAX_BOOKS_PER_DAY",
    "MIRROR_DAY_USD", "MIRROR_LOSS_STOP_USD", "MIRROR_MAX_ORDER_OPS_PER_TICK",
    "MIRROR_BOOK_CONCURRENCY", "MIRROR_VENUE_CALLS_PER_TICK",
    "MIRROR_MAX_REPLACES_PER_HOUR", "MIRROR_REST_TTL_S", "MIRROR_TAKE_AFTER_S",
    "MIRROR_EXIT_TOL", "exit_terms", "MIRROR_EXIT_TAKE_BAND", "EXIT_BAND_INERT_AT",
    "MIRROR_TAKE_BAND", "band_cent", "take_in_band",
    "MIRROR_FLATTEN_SLIP",
    "MIRROR_FLATTEN_REST_S", "MIRROR_FLAT_CLOSE_S", "MIRROR_DRIFT_MAX",
    "MIRROR_FROZEN_ALERT_S", "MIRROR_FROZEN_NAME_TICKS", "MIRROR_FROZEN_EXITS", "MIRROR_FAMILIES",
    "FLAT_TOL_SHARES", "SELL_DUST_SHARES",
    "P2_MAKER_SHARE_MIN", "P2_TAKE_SLIP_MAX", "P2_FROZEN_TICK_FRAC_MAX", "P2_CAPTURE_MIN",
    "P2_INTEGRITY_COUNTERS",
    "mirror_target", "AdmissionFacts", "admission", "prior_episode_adoption", "adopted_block",
    "venue_dust_is_ours",
    "buy_wire", "sell_wire", "buy_price", "sell_price", "plan_wire", "room_scale",
    "OpenOrder", "plan_reason_key", "keep_or_replace", "rest_decision", "replace_decision",
    "order_decision", "MIRROR_REST_MIN_LIFE_S", "REST_MIN_LIFE_CENT_MOVE",
    "at_or_through", "take_allowed", "take_arms", "select_flatten",
    "BookState", "Booking", "book_buy", "book_sell",
    "DriftRule", "drift_of", "drift_rule", "drift_net_rule",
    "episode_close", "episode_close_reason",
    "demotion_due", "capture_short", "p2_verdict",
]
