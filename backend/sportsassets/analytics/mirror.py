"""Position mirroring, the pure part (owner order 2026-09-02, "go for it,
let's get this working").

The copy sleeve reacted to a whale's FILLS one at a time, each fill
judged by a rule (never-add, one-per-game, complement-buy-is-exit). A
whale who runs a two-sided book -- RN1 on Nakashima v Michelsen: 66
buys, 64 as a maker, 28,162 matched pairs at a combined 0.8915 and a
24,423-share residual -- is unreadable that way: the rules copied one
clip, sold it in six pieces, and threw away 55 of his 66 fills.

The mirror reads his POSITION instead. Per whale and market it keeps
one number, his net holding, and our job is to hold a fixed fraction of
it. Every entry, add, hedge and exit is the same operation: move toward
the target.

This module is the arithmetic, kept pure so every rule is testable
without a venue or a database:

  * net_positions(fills)  -> his running position per token from the
                            fills we ingest (BUY adds, SELL subtracts)
  * his_net(long, other)  -> the signed net on our netting venue, in
                            LONG-token shares: long minus the other side
  * opening_burst(...)    -> what his first move on a market amounts to
                            (all his buys inside BURST_S of the first),
                            the anchor the ratio is set against
  * mirror_ratio(...)     -> MIRROR_ANCHOR_CLIP_USD / median burst,
                            clamped to [RATIO_MIN, COPY_RATIO_MAX]
  * bankroll_ratio(...)   -> bankroll / his deployed dollars, the same
                            clamp; refused by name on an unreadable or
                            non-positive figure (reported by mirror_ratio
                            as ratio_bankroll, sized on nowhere yet)
  * target_shares(...)    -> ratio x his_net, capped by dollars at the
                            mark, whole shares, shorts optional
  * plan(...)             -> given target, ledger and venue positions:
                            the one order we WOULD place (side, qty,
                            price) or the reason for none, with the
                            dead band, the flatten exception, the
                            would-fill read against the live book

The shadow worker logs plan() per tick and places nothing (phase P0).
Phase P1 executes the same plan long-only under every existing breaker;
phase P2 admits negative targets through the short gate.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..ingestion.shadow_v2 import LATE_POLL_ROW_S
from .roster_rules import MIRROR_ANCHOR_CLIP_USD

# A whale's opening move is a BURST of fills, not one fill (RN1 loaded
# 10,654 Michelsen across 12 fills in three minutes; 7 of them under $6).
# Everything inside this window of his first BUY on a market is one
# decision, and that is what the $50 clip is set against.
BURST_S = 60.0
# The ratio is bounded: never more than one-for-one with him (the
# executor's COPY_RATIO_MAX), never so small that his ordinary move
# rounds to nothing for us.
RATIO_MAX = 1.0
RATIO_MIN = 1e-4
# Per-EVENT net exposure cap at the mark, in dollars. When it binds the
# target is SCALED, never refused, and never truncated on one side.
# $250 -> $1,000 (owner order 2026-09-06 13:36Z, "max trade on one side
# of an event to be $1000 between all fills") -> $2,500 (owner order
# ~14:00Z the same day, verbatim: "Just trade 10% of what he puts on
# everything he takes (with a hard cap of no single event having more
# than $2.5k on it) this limitation should never force us to decline
# any of the possible copies"). One mirror book is one side of one game
# (the one-per-game claim keys on game_key), so "a single event" IS the
# book, and the cap is on the book's net at the mark across every fill:
# his 100,000 sh @ 0.60 at 10% is 10,000 raw -> 4,166 sh = $2,500. The
# shadow sizes from the same constant, so its targets scale with it.
MARKET_NET_CAP_USD = 2500.0
# Dead band: a move under one whole share, or under this many dollars at
# the mark, is not worth an order -- unless it takes the book to zero.
# $5 -> $0 (owner order ~14:10Z 2026-09-06, verbatim: "Bets under $10,
# take the full position (exact copy)"): his small bets are copied
# whole (mirror_live_rules.open_ratio), so no dollar floor may drop
# them -- the ONLY floor is one whole share, and the `dead_band` name
# stays for that sub-share move. MIN_MOVE_FRAC (the 2% hysteresis on
# adjustments) is unchanged. The plan's dollar clause is inert at 0
# and is kept as the operator's handle should a floor ever be wanted.
MIN_MOVE_USD = 0.0
# Hysteresis: skip moves smaller than this fraction of the target.
MIN_MOVE_FRAC = 0.02
# Venue and ledger agree when they are within this many shares.
VENUE_LEDGER_TOL_SHARES = 1.0


@dataclass
class Fill:
    asset: str
    side: str           # BUY / SELL
    size: float
    price: float
    ts: float           # epoch seconds
    condition_id: str | None = None
    tx_hash: str | None = None


def net_positions(fills: Iterable[Fill | dict]) -> dict[str, float]:
    """His running position per token from fills: BUY adds, SELL
    subtracts, floored at zero (a SELL beyond what the ledger saw him
    buy is a fill we missed, not a short -- Polymarket global cannot
    short a token). Order-independent."""
    pos: dict[str, float] = {}
    for f in fills:
        d = f if isinstance(f, dict) else f.__dict__
        asset = str(d.get("asset") or "")
        if not asset:
            continue
        try:
            size = float(d.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        sign = 1.0 if str(d.get("side") or "").upper() == "BUY" else -1.0
        pos[asset] = pos.get(asset, 0.0) + sign * size
    return {a: max(0.0, round(v, 6)) for a, v in pos.items()}


def his_net(long_shares: float, other_shares: float) -> float:
    """The signed net on a netting venue, in long-token shares. His
    matched pairs cancel; what is left is the directional residual."""
    return round(float(long_shares or 0.0) - float(other_shares or 0.0), 6)


def opening_burst(fills: Iterable[Fill | dict], burst_s: float = BURST_S) -> float:
    """Dollars of his BUYS inside `burst_s` of his first BUY on the
    market (any token). Zero when he never bought."""
    buys = []
    for f in fills:
        d = f if isinstance(f, dict) else f.__dict__
        if str(d.get("side") or "").upper() != "BUY":
            continue
        try:
            ts = float(d.get("ts"))
            notional = float(d.get("size") or 0.0) * float(d.get("price") or 0.0)
        except (TypeError, ValueError):
            continue
        if notional > 0:
            buys.append((ts, notional))
    if not buys:
        return 0.0
    t0 = min(ts for ts, _ in buys)
    return round(sum(n for ts, n in buys if ts <= t0 + burst_s), 4)


def _usd_read(value: Any) -> float | None:
    """A dollar figure the ratio may be set against: an int or a float
    that is finite. Anything else -- None, a bool, a string (even a
    numeric one), NaN, an infinity -- is unreadable and yields None, so
    the caller names it rather than dividing by a guess."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def bankroll_ratio(deployed_usd: Any, bankroll_usd: Any) -> tuple[float | None, float | None, str | None]:
    """(deployed_usd as read, ratio_bankroll, why): the fraction of HIS
    deployed dollars our bankroll can hold, clamped like the burst
    ratio. Pure; the two dollar figures arrive from the caller (the
    shadow's 30-day open-cost query and the promoted bankroll constant
    -- this module reads no environment, test_mirror_live_rules.py:76
    pins that the shared constants are never restated here).

    The reading is refused by name, deployed first then bankroll:
    `deployed_unreadable` / `bankroll_unreadable` when the figure is not
    a finite number, `deployed_zero` / `bankroll_zero` when it is not
    positive (a zero or negative scale is no scale). The deployed figure
    is echoed as read whenever it IS a number, so a refused `_zero`
    still shows the value that refused it."""
    deployed = _usd_read(deployed_usd)
    if deployed is None:
        return None, None, "deployed_unreadable"
    if deployed <= 0:
        return deployed, None, "deployed_zero"
    bankroll = _usd_read(bankroll_usd)
    if bankroll is None:
        return deployed, None, "bankroll_unreadable"
    if bankroll <= 0:
        return deployed, None, "bankroll_zero"
    ratio = round(min(RATIO_MAX, max(RATIO_MIN, bankroll / deployed)), 6)
    return deployed, ratio, None


def mirror_ratio(bursts: Iterable[float], clip_usd: float = MIRROR_ANCHOR_CLIP_USD, *,
                 deployed_usd: float | None = None,
                 bankroll_usd: float | None = None) -> dict:
    """ratio = clip / median opening burst over his recent markets, so
    his typical first move maps to the measuring clip. Needs at least
    MIN_MARKETS markets; otherwise the ratio is None and the mirror does
    nothing (fail closed on an unknown scale).

    `deployed_usd` / `bankroll_usd` (keyword-only, default None) add a
    THIRD reading, `ratio_bankroll`, beside the median and the weighted
    anchor; see `bankroll_ratio` for its refusals. Without them the
    reading is `deployed_unreadable`, and every key an existing caller
    reads keeps HEAD's value."""
    xs = sorted(float(b) for b in bursts if b and float(b) > 0)
    # THE BANKROLL RATIO (owner order 2026-09-02, "I want us to match
    # everything ... mirror the whales to a tee"; the sizing lens and its
    # engineering refutation, 2026-09-02, F1-F2): both burst anchors map
    # his typical FIRST MOVE to the $50 measuring clip, so on the markets
    # that carry his money the median ratio clamps to 1.0 and the
    # per-market cap does all the sizing (at the $250 it was when this
    # was measured it bound on 68.7-85.1% of his long dollars at
    # r=1.9%; $1,000 since 2026-09-06), which inverts his shape. The fraction that keeps his
    # shape is bankroll / his deployed dollars: the 30-day open cost
    # OVERSTATES him (resolved balances stay in the denominator), so this
    # r errs small, never large. It is reported here and measured in the
    # shadow before any live rule sizes on it.
    deployed, ratio_bankroll, why_bankroll = bankroll_ratio(deployed_usd, bankroll_usd)
    out: dict[str, Any] = {"n": len(xs), "anchor_usd": None, "ratio": None,
                           "clip_usd": float(clip_usd), "anchor_usd_weighted": None,
                           "ratio_weighted": None, "deployed_usd": deployed,
                           "ratio_bankroll": None}
    if why_bankroll is not None:
        out["why_bankroll"] = why_bankroll
    if len(xs) < MIN_MARKETS:
        out["why"] = f"fewer than {MIN_MARKETS} markets with an opening burst"
        # The bankroll ratio needs no burst, but a whale with too few
        # markets to anchor on is a scale we do not know yet; the third
        # reading fails closed with the other two, and says so.
        if why_bankroll is None:
            out["why_bankroll"] = out["why"]
        return out
    out["ratio_bankroll"] = ratio_bankroll
    anchor = statistics.median(xs)
    out["anchor_usd"] = round(anchor, 2)
    out["ratio"] = round(min(RATIO_MAX, max(RATIO_MIN, float(clip_usd) / anchor)), 6)
    # THE DOLLAR-WEIGHTED ANCHOR, reported beside the median (first shadow
    # hour, 2026-09-02): RN1 opened 19,742 markets in 30 days with a
    # median burst of $25.60, so the median-anchored ratio clamps to 1.0
    # and the per-market cap (MARKET_NET_CAP_USD) does all the sizing on
    # the markets that carry his money. This is the burst size at which half of his opening dollars
    # sit in LARGER bursts -- where the money is, not where the count is.
    total = sum(xs)
    acc = 0.0
    weighted = xs[-1]
    for b in reversed(xs):
        acc += b
        if acc >= total / 2.0:
            weighted = b
            break
    out["anchor_usd_weighted"] = round(weighted, 2)
    out["ratio_weighted"] = round(min(RATIO_MAX, max(RATIO_MIN, float(clip_usd) / weighted)), 6)
    return out


MIN_MARKETS = 10


def target_shares(ratio: float | None, net: float, mark: float | None,
                  allow_short: bool = False,
                  cap_usd: float = MARKET_NET_CAP_USD) -> dict:
    """Our target in long-token shares. Whole shares. A negative net is
    a short of the long token: refused (target 0) unless shorts are
    allowed. The dollar cap at the mark scales the target down, never
    truncates one side."""
    if ratio is None or ratio <= 0:
        return {"target": 0, "raw": 0.0, "capped": False, "why": "no ratio"}
    raw = float(ratio) * float(net or 0.0)
    if raw < 0 and not allow_short:
        return {"target": 0, "raw": round(raw, 4), "capped": False,
                "why": "short side not admitted"}
    capped = False
    if mark is not None and 0.0 < float(mark) < 1.0 and cap_usd > 0:
        px = float(mark) if raw >= 0 else 1.0 - float(mark)
        max_sh = cap_usd / px
        if abs(raw) > max_sh:
            raw = max_sh if raw > 0 else -max_sh
            capped = True
    tgt = int(raw) if raw >= 0 else -int(-raw)      # toward zero, whole shares
    return {"target": tgt, "raw": round(raw, 4), "capped": capped, "why": None}


# ------------------------------------------ E12: his FLOW from first sight
#
# docs/mirror-to-a-tee-program.md decision 13 (2026-09-08): "(A) follow
# only his flow from first sight (Rule LE, pro-rata ratchet); (B) build
# r x his_net into his open book at his newest level (today's code);
# ... RECOMMENDATION: (A) now ...; (B) never." And D25's amendment of
# the ratchet, verbatim: "Amended to PRO-RATA on reductions: `block_t =
# block_{t-1} x net_t/net_{t-1}` when net falls, unchanged on
# increases, 0 on a crossing to <= 0." THE BLOCK is his net on the
# book's long-token axis that stood before we saw the market: never
# bought (the 03:22Z paired day: ours 0.71 vs his 0.287 on the losers
# opened late). What the target is sized on is his net LESS the block.
# Pure; the live worker stores the block (`mirror_books.flow_base`) and
# the net it was last read against (`flow_last_net`) and hands both
# back every tick. Every number is read by _num_r: a bool, a string,
# NaN or an infinity is unreadable, and unreadable is answered None --
# the caller names it (no_position), never a guess.
#
# THE FOLD (2026-09-08; the review of E12). HIGH-1: the ingest clock
# alone read a backfilled or reconciled OLD fill as the flow that woke
# the market (ingestion/history.py stamps `detected_at` at the backfill,
# an S1 reconciliation row at its insertion), and the book opened on it
# at his NEWEST cent -- the block, bought, by the back door. A fill is
# the FLOW only when BOTH clocks say so (is_flow): its ingest clock
# inside the window AND its own stamp not older than the window's start
# by more than LATE_FILL_S. HIGH-2: the ratchet moves ONLY on a fall his
# FILLS witness -- the fills' net (his_net over net_positions) moves
# only when a fill of his is ingested and falls only on a SELL of the
# long token or a BUY of the other -- so the block, its reference
# (`flow_last_net`) and the ratchet live on the FILLS' axis, and the
# tick's two-source reading never moves the block and never sizes a
# flow book: it is compared with the fills' arithmetic and named on the
# plan when it disagrees (flow_reading). THE MEDIUMS, said, not built:
# MEDIUM-1, first sight is the FIRST_SIGHT_S window before the OPEN,
# not before our first look -- a market we map late (an `unmapped` or
# no-mark memo, a read the walk's cap deferred, a loss stop) reads his
# fills in the meantime as the block, by design under (A): the mirror
# buys the flow it could have answered, never his history at the
# current price, and the 2c allowance admits the rest (the 03:22Z
# paired day: his fill reached our first order after more than 60 s on
# 9 of 29 books -- 105 to 1,315 s -- and each of those opens is a
# flow-only open unless the allowance admits it). MEDIUM-2: a sign-flip
# reopen is flow-only -- the flip's close needs the venue read at 0 and
# the reopen the tick after, so his crossing fills are at least two
# ticks old at the reopen and are the new side's block; target 0 unless
# the mark is within the allowance of his cost on the new side (the
# short axis reads his other-token BUY at 1 - p). MEDIUM-3, a
# FOLLOW-UP at the fold, built by E12b: vwap_of read BUYs alone (the
# long token at p; a short's other token at 1 - p), so a long he built
# by SELLING the other token read `vwap_unread` and the book opened
# flow-only at the exact mark -- fail-closed, a missed follow, not
# money; it now reads every fill that ADDS on the book's axis (below).
#
# E12b (2026-09-08; the fold re-review's MEDIUM-1). The fold witnessed
# the fills' NET, not a reducing FILL: the chain-first collapse in
# workers/mirror_shadow.his_fills (a per-match row whose legs do not sum
# to the chain row still collapses) lowers the fills' net with no sale
# of his -- two poll legs of 6,000 + 5,640 replaced by their chain row
# of 11,000 -- and D25's ratchet moved on it (a 100,000 block to
# 99,426.7; 57 shares of the block held as flow). So the ratchet moves
# ONLY when the tick's fills hold a REDUCING fill of his on the block's
# axis -- a SELL of the long token or a BUY of the other on a long
# block, the mirror image on a short -- whose ingest clock is after the
# reference's clock (reducing_since), and by no more than those fills'
# share of the fall (witnessed_ratchet: net_t' = net_{t-1} less the
# witnessed reduction, block x net_t' / net_{t-1}); a fall none of them
# explains is named `flow_fills_shrank` = {from, to, unexplained,
# witnessed} for the plan and the block stands. The reference's clock
# (mirror_books.flow_last_at, migration 058) is the newest ingest clock
# among the fills the reference counted (fills_clock): a counted fill
# never witnesses again, a fill ingested after the read always can,
# whatever the worker's clock says against the ingestion's (pipeline.py
# stamps detected_at on the ingestion host).
#
# THE E12b FOLD (2026-09-08; the review's CRITICAL-1 and HIGH-1).
# HIGH-1: the witness was one-sided -- a RISE of the fills' net was
# D25's "unchanged on increases" whatever explained it, so a poll SELL
# leg overstated at 2,750 witnessed a 25 % ratchet and its chain row at
# the true 2,000 raised the fills' net by 750 with no adding fill of
# his; the flow read it and the book BOUGHT 75 of the block at his cent.
# Now the mirror image of the witness reads the rise (adding_since: a
# BUY of the long token or a SELL of the other on a long axis, the
# mirror image on a short, clocked after the reference) and a rise the
# adds do not explain goes to the BLOCK (restored_block: block_t =
# block_{t-1} + unexplained, never past the net), named
# `flow_fills_grew` -- never bought; a rise with no adding fill at all
# is block whole. The landed rule (a row with no clock) shares the
# defect and stays as it is. CRITICAL-1 (the worker): the hold
# suppresses NEW reduces only; his witnessed exit -- resting, cancelled
# by its TTL, or never placed -- keeps its whole E4 life through _act,
# capped at the witnessed share still outstanding (our ledger past the
# target the reference sizes). MEDIUM-1, said: a sale
# and a re-add in one tick apply the FALL (witnessed_ratchet), so the
# re-add is under-mirrored (sell 3,000 + re-buy 2,000: block 9,090.9,
# target 90; over two ticks 7,272.7 and 272 -- 182 fewer held), D25's
# path dependence, fail-closed toward not buying. LOW-1, said:
# reducing_since compares a stamp-only fill's VENUE time to a reference
# clock that is an INGEST stamp, so a SELL made inside the lag with no
# detected_at is missed until a later fill moves the clock -- the
# legacy-NULL case only (pipeline.py and history.py stamp every insert).
# LOW-2, said: a part-witnessed crossing leaves a residual block (the
# collapse's 640 and his full exit in one tick: 573.3 at net 0) that
# under-mirrors his re-buy (442 where a clean block gives 500),
# fail-closed; accepted.
#
# THE E12b FOLD RE-REVIEW (2026-09-08; MEDIUM-1): adding_since read the
# ingest clock alone, so a backfilled or S1-reconciled BUY of his
# history (stamped 2 h 13 min ago, inserted now) explained a rise and
# was BOUGHT at his current cent -- the road is_flow closes at the open.
# Now an adding fill counts only when its own stamp is also no more
# than LATE_FILL_S before the reference's clock (the one constant);
# an older one is unexplained, restored to the block, never bought.
# reducing_since keeps one clock: an old-stamped SELL of his is a sale
# that happened, late-known -- a witness, never missed. LOW-1, said: a
# legacy-NULL add (no detected_at, an old stamp) is absorbed into the
# block and never followed -- fail-closed. LOW-2, said: on a book at
# the per-event cap the reference's target is read at this tick's mark
# (3,000 at 0.80, 2,777 at 0.90), so a mark that rose since the
# reference lowers the hold's cap -- the cap's own rule on any tick
# (E1's at-the-mark cap), not the hold's; below the cap it is the flow's.

def _num_r(v: Any) -> float | None:
    if isinstance(v, (bool, str)) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def fill_clock(f: Any) -> float | None:
    """When WE learned of the fill: the ingest's `detected_at`, else the
    fill's own stamp `ts`. None when neither reads -- an unclocked fill
    is an OLD one, the block's side (not bought), never the flow's."""
    d = f if isinstance(f, dict) else getattr(f, "__dict__", None)
    if not isinstance(d, dict):
        return None
    for k in ("detected_at", "ts"):
        v = _num_r(d.get(k))
        if v is not None:
            return v
    return None


# THE LATE ROW (the fold, HIGH-1): the poll lane's own late-row allowance,
# ingestion/shadow_v2.LATE_POLL_ROW_S (900 s: "detected_at - ts beyond
# this = reconciler artifact"), reused rather than a second number for
# the same fact -- a row whose ingest clock trails its stamp by more
# than this is the reconciler's or the backfill's, not the poll's (whose
# lag is minutes), and a fill of his that old was not answered by
# anything: the block's side, whatever its ingest clock says. The poll
# lane's ~281 s lag stays flow.
LATE_FILL_S = float(LATE_POLL_ROW_S)


def is_flow(f: Any, since: float | None, late_s: float = LATE_FILL_S) -> bool:
    """The fill is FLOW from first sight -- answered as any fill of his
    is -- only when BOTH clocks say so: its ingest clock (fill_clock) at
    or after `since` AND its own stamp `ts` not older than `since` by
    more than `late_s`. `since` None reads every fill as old; an
    unclocked or unstamped fill is old (the block's side, not bought);
    an unreadable `late_s` admits nothing past the window's start."""
    if since is None:
        return False
    at = fill_clock(f)
    if at is None or at < float(since):
        return False
    d = f if isinstance(f, dict) else getattr(f, "__dict__", None)
    ts = _num_r(d.get("ts")) if isinstance(d, dict) else None
    late = _num_r(late_s)
    late = 0.0 if late is None or late < 0.0 else late
    return ts is not None and ts >= float(since) - late


def _signed_size(f: dict, long_asset: str | None, other_asset: str | None) -> float | None:
    """The fill's move of his net in long-token shares: BUY of the long
    token +, SELL of it -, BUY of the other token -, SELL of it +. None
    for a token that is neither, or an unreadable size."""
    a, side = str(f.get("asset") or ""), str(f.get("side") or "").upper()
    size = _num_r(f.get("size"))
    if size is None or size <= 0 or side not in ("BUY", "SELL"):
        return None
    if long_asset and a == long_asset:
        return size if side == "BUY" else -size
    if other_asset and a == other_asset:
        return -size if side == "BUY" else size
    return None


def pre_existing_block(net: Any, fills: Iterable[dict], long_asset: str | None,
                       other_asset: str | None, since: float | None,
                       late_s: float = LATE_FILL_S) -> float | None:
    """THE BLOCK AT FIRST SIGHT: his net on the axis less the fills that
    are flow (is_flow: ingested at or after `since` AND stamped no more
    than `late_s` before it) -- the flow that brought the market to us,
    answered as any fill of his is -- clamped to the axis: never past
    his net, never across zero (he built 12,000 while nobody watched
    and added 1,000 as we looked: the block is 12,000 of his 13,000; he
    bought 1,000 and sold 500 inside the window on a net of 400: the
    block is 0, not -100). `net` is HIS FILLS' net (the axis the block
    and its ratchet live on). `since` None reads every fill as old: the
    whole net is the block. None on an unreadable net."""
    n = _num_r(net)
    if n is None:
        return None
    recent = 0.0
    if since is not None:
        for f in fills or ():
            if not isinstance(f, dict) or not is_flow(f, since, late_s):
                continue
            s = _signed_size(f, long_asset, other_asset)
            if s is not None:
                recent += s
    b = n - recent
    if n >= 0:
        return round(min(max(0.0, b), n), 6)
    return round(max(min(0.0, b), n), 6)


def flow_net(net: Any, block: Any) -> float | None:
    """The net the target is sized on under Rule LE: his net less the
    block, on the block's side of zero and never under it (a 12,000
    block on a net of 13,000 is a flow of 1,000; on a net of 11,500 --
    a fall the ratchet has not yet read -- a flow of 0, never -500).
    His WHOLE net when the block is 0 (nothing pre-existing, or the
    ratchet zeroed it) or his net has crossed to the other sign (the
    sign flip keeps its rule). None when either is unreadable."""
    n, b = _num_r(net), _num_r(block)
    if n is None or b is None:
        return None
    if b == 0.0 or n == 0.0 or (n > 0) != (b > 0):
        return round(n, 6)
    if b > 0:
        return round(max(0.0, n - b), 6)
    return round(min(0.0, n - b), 6)


def pre_existing_ratchet(block: Any, last_net: Any, net: Any) -> float | None:
    """D25, in its words: `block_t = block_{t-1} x net_t/net_{t-1}` when
    net falls, unchanged on increases, 0 on a crossing to <= 0 -- read
    on the block's axis (a short book's block is negative and "falls"
    toward zero). So a 25% sale by him is a 25% reduce by us, and his
    full exit is our full exit; an increase after a reduction leaves
    the ratcheted block where it is (never restored). `last_net` is
    net_{t-1}, his net the block was last read against; unreadable, the
    block itself stands in (the state at open). None on an unreadable
    block or net (the caller sizes nothing on it).

    BOTH NETS ARE HIS FILLS' (the fold, HIGH-2): net_t and net_{t-1}
    are his_net over net_positions of the fills the tick holds, which
    move only when a fill of his is ingested, and a reading of the
    venue that wobbles or reads zero (D1's merged pair) never reaches
    this function. The reading's own verdict is flow_reading's. But the
    fills' net does NOT fall only on a SELL of the long token or a BUY
    of the other (the re-review's MEDIUM-1): the chain-first collapse
    of the poll lane's legs lowers it with no sale of his, so the live
    worker runs this arithmetic through witnessed_ratchet -- on the fall
    a reducing fill of his explains, never on the rest -- once the
    reference carries its clock (migration 058); this is the LANDED
    rule, kept for a row with no clock and for the clock column absent."""
    b, n = _num_r(block), _num_r(net)
    if b is None or n is None:
        return None
    if b == 0.0:
        return 0.0
    s = 1.0 if b > 0 else -1.0
    ln = _num_r(last_net)
    ln = b if ln is None else ln
    bn, lnn, nn = b * s, ln * s, n * s
    if nn <= 0.0:
        return 0.0                              # a crossing to <= 0
    if lnn > 0.0 and nn < lnn:
        return round(min(bn * nn / lnn, nn) * s, 6)      # falls: pro rata (never past the net)
    return round(min(bn, nn) * s, 6)                     # unchanged on increases


FLOW_READING_ZERO = "flow_reading_zero"
FLOW_READING_DISAGREE = "flow_reading_disagree"


def flow_reading(reading: Any, fills_net: Any, dust: float = VENUE_LEDGER_TOL_SHARES) -> str | None:
    """THE TICK'S READING AGAINST HIS FILLS' ARITHMETIC (the fold,
    HIGH-2), for the plan's `flow_reading`. A flow book is sized on his
    fills' net (flow_net over the block, which pre_existing_ratchet
    moves by the fills alone); the two-source reading the tick made
    (the per-market read, the walk, the smaller of two disagreeing
    readings) is compared with it and NAMED when it disagrees, never
    obeyed: None when it agrees within `dust` (one share, D1's "to the
    share" -- VENUE_LEDGER_TOL_SHARES, the tolerance the worker's
    _fresh_agreed reads the same pair at); `flow_reading_zero` when it
    reads zero against fills that say he holds (D1's merged pair, a
    blip: the old rule's zero-read flatten and, under E12, the re-buy
    of his whole block next tick -- neither now); `flow_reading_disagree`
    otherwise (a 0.9% wobble inside MIRROR_DRIFT_MAX, which ratcheted
    the block and bought part of it next tick; a venue reading past the
    fills, which sized a buy his fills never made). Either unreadable:
    `flow_reading_disagree` -- a reading that was not made agrees with
    nothing. Pure."""
    r, n = _num_r(reading), _num_r(fills_net)
    if r is None or n is None:
        return FLOW_READING_DISAGREE
    d = _num_r(dust)
    d = 0.0 if d is None or d < 0.0 else d
    if abs(r - n) <= d:
        return None
    if r == 0.0:
        return FLOW_READING_ZERO
    return FLOW_READING_DISAGREE


FLOW_FILLS_SHRANK = "flow_fills_shrank"


def fills_clock(fills: Iterable[dict], fallback: Any = None) -> float | None:
    """THE REFERENCE'S CLOCK (E12b; mirror_books.flow_last_at): the
    newest ingest clock (fill_clock: `detected_at`, else the stamp)
    among the fills a reference write counted. Written beside the
    reference net, and what reducing_since reads a witness against:
    a fill the reference counted is at or before it and never witnesses
    again; a fill ingested after that read is stamped after it by the
    ingestion's own clock (pipeline.py stamps `detected_at` on the
    ingestion host, in insertion order), whatever the worker's clock
    reads -- a clock skew between the two hosts, or a fill landing
    between the tick's read and its write, can neither hide a sale nor
    re-witness one. `fallback` (the tick's own clock) when no fill has
    a clock; None when neither reads."""
    best = None
    for f in fills or ():
        at = fill_clock(f) if isinstance(f, dict) else None
        if at is not None and (best is None or at > best):
            best = at
    return _num_r(fallback) if best is None else best


def reducing_since(fills: Iterable[dict], long_asset: str | None, other_asset: str | None,
                   block: Any, since: Any) -> float:
    """THE WITNESS (E12b; the re-review's MEDIUM-1): the reduction his
    fills carry on the block's axis since the reference's clock, in
    long-token shares -- a SELL of the long token or a BUY of the other
    on a long block; a BUY of the long token or a SELL of the other on
    a short one (the mirror image); nothing else is a witness (a SELL
    of the other token on a long book is an ADD) -- summed over the
    fills whose ingest clock (fill_clock) is strictly AFTER `since`.
    An old row re-read (clocked at or before the reference) is no
    witness; the poll lane's late row (stamped minutes ago, ingested
    now) is one. NO SECOND CLOCK HERE (the E12b fold re-review): a
    reducing row of his stamped hours ago and inserted now -- a
    backfilled or S1-reconciled SELL -- is still a witness, because
    his sale HAPPENED and we learn of it late: the block ratchets by
    it and the exit follows at his price, as it must (adding_since
    reads the stamp too, because an old add is history that must not
    be bought; an old sale is an exit that must not be missed). 0
    with no block, no clock, or nothing reducing."""
    b, at0 = _num_r(block), _num_r(since)
    if b is None or b == 0.0 or at0 is None:
        return 0.0
    s = 1.0 if b > 0 else -1.0
    total = 0.0
    for f in fills or ():
        if not isinstance(f, dict):
            continue
        at = fill_clock(f)
        if at is None or at <= at0:
            continue
        sz = _signed_size(f, long_asset, other_asset)
        if sz is None:
            continue
        red = -sz * s
        if red > 0.0:
            total += red
    return round(total, 6)


def witnessed_ratchet(block: Any, last_net: Any, net: Any,
                      witnessed: Any) -> tuple[float | None, dict | None]:
    """D25's ratchet on the fall his fills WITNESS (E12b): `(block_t,
    shrank)`. No fall (net_t at or above net_{t-1} on the block's
    axis): pre_existing_ratchet's answer -- unchanged on increases --
    and None. A fall explained by the witnessed reduction (the reducing
    fills reducing_since summed; a sale and a re-add in one tick
    explain more than they fell, and the fall is what is applied):
    `block_{t-1} x net_t / net_{t-1}`, D25 as landed, and None. A fall
    LARGER than the witnessed reduction -- a chain-first collapse and a
    sale in one tick: the witnessed part only, `net_t' = net_{t-1} -
    witnessed`, `block_{t-1} x net_t' / net_{t-1}` (0 when net_t'
    crosses to <= 0), and the remainder named: `shrank = {from: net_{t-1},
    to: net_t, unexplained, witnessed}`. A fall with NOTHING witnessed
    (the collapse alone, or a crossing to <= 0 no fill of his made):
    the block as it was and the same dict with `witnessed` 0 -- the
    caller neither writes nor sells on it. None, None on an unreadable
    block or net; an unreadable witness is 0 (nothing seen, nothing
    moved: fail closed toward holding, never toward selling)."""
    b, n = _num_r(block), _num_r(net)
    if b is None or n is None:
        return None, None
    if b == 0.0:
        return 0.0, None
    s = 1.0 if b > 0 else -1.0
    ln = _num_r(last_net)
    ln = b if ln is None else ln
    fall = round((ln - n) * s, 6)
    if fall <= 0.0:
        return pre_existing_ratchet(b, ln, n), None
    w = _num_r(witnessed)
    w = 0.0 if w is None or w < 0.0 else w
    applied = min(fall, w)
    unexplained = round(fall - applied, 6)
    if applied <= 0.0:
        return b, {"from": ln, "to": n, "unexplained": unexplained, "witnessed": 0.0}
    fb = pre_existing_ratchet(b, ln, (ln * s - applied) * s)
    if unexplained <= 0.0:
        return fb, None
    return fb, {"from": ln, "to": n, "unexplained": unexplained, "witnessed": round(applied, 6)}


FLOW_FILLS_GREW = "flow_fills_grew"


def adding_since(fills: Iterable[dict], long_asset: str | None, other_asset: str | None,
                 axis: Any, since: Any, late_s: float = LATE_FILL_S) -> float:
    """THE MIRROR IMAGE OF THE WITNESS (the E12b fold, HIGH-1): the ADDS
    his fills carry on the axis since the reference's clock, in
    long-token shares -- a BUY of the long token or a SELL of the other
    on a long axis; a SELL of the long token or a BUY of the other on a
    short one -- over the fills whose ingest clock (fill_clock) is
    strictly AFTER `since` AND whose own stamp `ts` is not older than
    `since` by more than `late_s` (the E12b fold re-review's MEDIUM-1:
    is_flow's second clock, the one constant LATE_FILL_S). A backfilled
    or S1-reconciled BUY of his HISTORY -- stamped hours ago, inserted
    now -- is clocked after the reference but is not an add he made
    since it: not counted, so the rise it makes is unexplained and
    restored to the block (never bought), the road is_flow closes at
    the open; the poll lane's late add (minutes) stays flow. An
    unstamped adding row is old, as at the open. `axis` is the block,
    or his net when the block is 0: its sign is the side. 0 with no
    axis, no clock, or nothing adding (fail closed: an unexplained rise
    is block); an unreadable allowance admits nothing stamped before
    the clock."""
    a, at0 = _num_r(axis), _num_r(since)
    if a is None or a == 0.0 or at0 is None:
        return 0.0
    late = _num_r(late_s)
    late = 0.0 if late is None or late < 0.0 else late
    s = 1.0 if a > 0 else -1.0
    total = 0.0
    for f in fills or ():
        if not isinstance(f, dict):
            continue
        at = fill_clock(f)
        if at is None or at <= at0:
            continue
        ts = _num_r(f.get("ts"))
        if ts is None or ts < at0 - late:
            continue                                    # his history, late-known: the block's
        sz = _signed_size(f, long_asset, other_asset)
        if sz is None:
            continue
        add = sz * s
        if add > 0.0:
            total += add
    return round(total, 6)


def restored_block(block: Any, last_net: Any, net: Any, added: Any) -> tuple[float | None, dict | None]:
    """AN UNEXPLAINED RISE OF THE FILLS' NET GOES TO THE BLOCK (the E12b
    fold, HIGH-1): `(block_t, grew)`. No rise on the axis, or a rise
    the adding fills (adding_since) explain: the block as handed and
    None -- D25's "unchanged on increases", the rise is his flow. A
    rise LARGER than the adds -- a reducing leg the poll lane
    overstated, corrected by its chain row; a legs' mismatch the other
    way -- puts the unexplained part in the BLOCK: `block_t =
    block_{t-1} + unexplained`, never past the net, and `grew = {from:
    net_{t-1}, to: net_t, unexplained, witnessed: the explained adds}`
    names it; a rise with no adding fill at all is block whole. So the
    flow -- and the target -- rise by the explained adds alone: never
    a buy on a rise he did not make. Additive, not pro rata: undoing
    the phantom ratchet pro rata (7,500 x 9,000 / 8,250 = 8,181.8 on
    the review's harness, the true path's block) would BUY 6 shares on
    the correction; the additive restore (8,250) holds 75 where the true
    path holds 81 -- fail-closed toward not buying. The block's sign is
    the axis; a block of 0 takes the net's. None, None on an unreadable
    block or net; an unreadable `added` is nothing seen."""
    b, n = _num_r(block), _num_r(net)
    if b is None or n is None:
        return None, None
    s = (1.0 if b > 0 else -1.0) if b != 0.0 else ((1.0 if n > 0 else -1.0) if n != 0.0 else 0.0)
    if s == 0.0:
        return b, None
    ln = _num_r(last_net)
    ln = b if ln is None else ln
    rise = round((n - ln) * s, 6)
    if rise <= 0.0:
        return b, None
    a = _num_r(added)
    a = 0.0 if a is None or a < 0.0 else a
    explained = min(rise, a)
    unexplained = round(rise - explained, 6)
    if unexplained <= 0.0:
        return b, None
    fb = round(min(b * s + unexplained, n * s) * s, 6)
    return fb, {"from": ln, "to": n, "unexplained": unexplained, "witnessed": round(explained, 6)}


# ------------------------------ E15 (2026-09-08): the witness on EVERY book
#
# Book 451 (task 69): an exact copy of 16 @0.39; he added to $46, the
# one-way ratio step (rules.step_ratio) took the target to 10 and the
# reduce path SOLD 6 at 0.33 while he was BUYING. Book 278: nine
# increase rests at 0.62 -> 0.12 -> 0.07 sized on a reading that lagged
# his 4,415 Yes buys; 24 @0.12 and 292 @0.07 filled -- $446 of our $539
# loss on a sale he did not make. E12b's rule (the ratchet moves only on
# a REDUCING fill of his clocked after the reference) governed flow
# books alone; a book with no block (opened before 057, or with the
# block admitted) still sold on any fall of the target: the ratio step,
# the $10 line crossed, a cap scaled down, a snapshot smaller than the
# fills' net, the chain-first collapse. Now EVERY book's reduce needs
# the same witness: a reducing fill of his on the book's axis, clocked
# after the last plan's reference (`reduce_ref` on the plan row: the
# target the last un-held plan sized and the newest ingest clock among
# the fills it counted, mi.fills_clock). Pure helpers here; the live
# worker names `reduce_unwitnessed` = {from, to, cause} and HOLDS at the
# ledger (his witnessed exit's rest keeps its E4 life, capped at the
# reference's target, exactly E12b's construction); a witnessed fall
# reduces to the target at his price within MIRROR_EXIT_TOL; a target
# of 0 (his full exit, the confirmed vanish, the sign flip) keeps its
# own readers -- none of them is this rule's.

REDUCE_UNWITNESSED = "reduce_unwitnessed"
FLIP_BURST_NET = "flip_burst_net"
DRIFT_FILLS_EXPLAIN = "drift_fills_explain"


def reducing_on(fills: Iterable[dict], long_asset: str | None, other_asset: str | None,
                short: bool, since: Any) -> float:
    """THE WITNESS ON THE BOOK'S OWN LEG (E15): reducing_since read on
    the leg's axis rather than a block's -- a SELL of the long token or
    a BUY of the other on a long book, the mirror image on a short --
    over the fills clocked strictly after `since`. 0 with no clock or
    nothing reducing (fail closed: no witness, no sale)."""
    return reducing_since(fills, long_asset, other_asset, -1.0 if short else 1.0, since)


def burst_since(fills: Iterable[dict], long_asset: str | None, other_asset: str | None,
                short: bool, since: Any) -> dict | None:
    """THE TWO-SIDED BURST (E15; book 278): both tokens bought inside
    one tick -- his fills clocked after `since` carry an ADD and a
    REDUCTION on the book's axis at once (adding_since and reducing_on,
    each > 0). `{adds, reductions, net}` in long-token shares on the
    leg's axis, else None (one-sided or nothing). The worker sizes an
    increase on the NET of the burst (his fills' net), never on a
    reading that carries the last leg alone."""
    adds = adding_since(fills, long_asset, other_asset, -1.0 if short else 1.0, since)
    reds = reducing_on(fills, long_asset, other_asset, short, since)
    if adds <= 0.0 or reds <= 0.0:
        return None
    return {"adds": adds, "reductions": reds, "net": round(adds - reds, 6)}


def drift_explained(fills: Iterable[dict], long_asset: str | None, other_asset: str | None,
                    short: bool, fills_net: Any, snap_net: Any, since: Any,
                    dust: float = VENUE_LEDGER_TOL_SHARES) -> dict | None:
    """THE DRIFT HIS FILLS EXPLAIN (lane 4, 2026-09-08): the snapshot is
    older than one tick and his fills' net stands ABOVE it on the
    book's leg by exactly the adds ingested after the snapshot's clock
    (adding_since over the chain / s1-sourced fills alone -- the poll
    lane's rows never explain a reading, they are what the chain
    corrects) within `dust`. `{fills_after, delta}` when explained --
    the worker then sizes the INCREASE on the fills' net -- else None:
    a fills' net under the snapshot (a sale the reading has not seen,
    or a collapse) is never this rule's, an unexplained rise refuses as
    today, an unreadable figure explains nothing."""
    n, s0, at0 = _num_r(fills_net), _num_r(snap_net), _num_r(since)
    if n is None or s0 is None or at0 is None:
        return None
    sgn = -1.0 if short else 1.0
    delta = round((n - s0) * sgn, 6)
    if delta <= 0.0:
        return None
    sourced = [f for f in fills or () if isinstance(f, dict)
               and str(f.get("source") or "") in ("chain", "s1")]
    added = adding_since(sourced, long_asset, other_asset, sgn, at0)
    d = _num_r(dust)
    d = 0.0 if d is None or d < 0.0 else d
    if added <= 0.0 or abs(delta - added) > d:
        return None
    return {"fills_after": added, "delta": delta}


def vwap_of(fills: Iterable[dict], long_asset: str | None, other_asset: str | None,
            short: bool = False, before: float | None = None,
            late_s: float = LATE_FILL_S) -> float | None:
    """His size-weighted cost on the book's axis over the BLOCK's
    fills: those that are not flow from `before` (is_flow, the same cut
    pre_existing_block makes: a fill ingested inside the window but
    stamped more than `late_s` before it is the block's, and its price
    enters here); every fill when `before` is None. The cost of the
    block, which the catch-up tolerance is read against.

    EVERY FILL THAT ADDS ON THE AXIS (E12b; the first review's
    MEDIUM-3): on a long book a BUY of the long token at p and a SELL
    of the other token at 1 - p -- the way his level is read on the
    long axis -- and on a SHORT book the mirror image, a BUY of the
    other token at 1 - p and a SELL of the long token at p; a reducing
    fill on the axis is excluded as before (his cost is what he paid
    to build, not what he took to trim). So a long he built by SELLING
    the other token alone reads his cost and catches up within the
    allowance of 1 - his price, where it read `vwap_unread` and opened
    flow-only at the exact mark. None when he built nothing readable."""
    num = den = 0.0
    for f in fills or ():
        if not isinstance(f, dict):
            continue
        if before is not None and is_flow(f, before, late_s):
            continue
        sz = _signed_size(f, long_asset, other_asset)
        if sz is None or ((sz < 0.0) if not short else (sz > 0.0)):
            continue                                    # not on the axis, or a reducing fill
        p = _num_r(f.get("price"))
        if p is None or not (0.0 < p < 1.0):
            continue
        px = p if str(f.get("asset") or "") == long_asset else 1.0 - p
        num += px * abs(sz)
        den += abs(sz)
    return round(num / den, 6) if den > 0 else None


@dataclass
class Book:
    bid: float | None = None
    ask: float | None = None


@dataclass
class Plan:
    side: str | None            # BUY_LONG / SELL_LONG / None
    qty: int
    price: float | None         # the price we would rest at (his level)
    reason: str                 # why (or why not)
    would_fill: bool | None = None
    detail: dict = field(default_factory=dict)


def plan(target: int, ledger: float, venue: float | None, book: Book,
         his_last_px: float | None, mark: float | None) -> Plan:
    """The one order we WOULD place to move from what we hold toward the
    target. Fail closed on any disagreement between ledger and venue.

      * venue None (unreadable) -> nothing
      * venue != ledger         -> frozen: a position the book cannot
                                   explain is reconciled first, never
                                   traded against
      * |delta| < 1 share       -> nothing
      * |delta x mark| < MIN_MOVE_USD and not a flatten -> nothing
      * |delta| < MIN_MOVE_FRAC x |target| and not a flatten -> nothing
      * buy: rest at min(his last price, best bid) -- join his level,
             never above him; would_fill if the ask is at/under it
      * sell: rest at max(his equivalent price, best ask); would_fill
              if the bid is at/over it
    """
    if venue is None:
        return Plan(None, 0, None, "venue unreadable")
    # WITHIN ONE SHARE IS AGREEMENT (shadow hours 2-3, 2026-09-02): the
    # ledger holds fractional fills (322.51 shares) and the venue reports
    # whole ones (-323); comparing truncated integers froze 176 readings
    # on a rounding edge. A real disagreement is more than a share.
    if abs(float(venue) - float(ledger)) > VENUE_LEDGER_TOL_SHARES:
        return Plan(None, 0, None, "frozen: venue and ledger disagree",
                    detail={"venue": venue, "ledger": ledger})
    delta = int(target) - int(ledger)
    if delta == 0:
        return Plan(None, 0, None, "on target")
    flatten = int(target) == 0
    if abs(delta) < 1:
        return Plan(None, 0, None, "under one share")
    # THE BAND IS PRICED AT THE LEG'S OWN PRICE (P2 rung S0, brief C6):
    # a move on a SHORT ledger commits collateral at 1 - mark a share,
    # so 12 shares of a 0.70 leg ($8.40) are not "under $5" because the
    # long token trades at 0.30, and 40 shares of a 0.10 leg ($4) are,
    # whatever 40 x 0.90 says. A long book's band is unchanged
    if mark is not None and not flatten:
        leg_px = float(mark) if (int(target) >= 0 and ledger >= 0) else 1.0 - float(mark)
        if abs(delta) * leg_px < MIN_MOVE_USD:
            return Plan(None, 0, None, "under the dollar dead band",
                        detail={"delta": delta})
    if target != 0 and abs(delta) < MIN_MOVE_FRAC * abs(int(target)) and not flatten:
        return Plan(None, 0, None, "inside hysteresis", detail={"delta": delta})
    # THE BOOK SIDE WE JOIN IS REQUIRED (review round one): a buy rests
    # at or under the bid, a sell at or over the ask; without that side
    # of the book there is no price to rest at, and a price off a stale
    # fill alone would be counted a fill against a book it never saw.
    # `would_fill` here is the IMMEDIATE read -- the book already at or
    # through his level at this tick ("marketable now"); the shadow
    # worker resolves the real question, whether the book CAME to the
    # resting price, against the next reading of the market.
    if delta > 0:
        if book.bid is None or not (0.0 < book.bid < 1.0):
            return Plan("BUY_LONG", delta, None, "no price to rest at")
        cands = [p for p in (his_last_px, book.bid) if p is not None and 0.0 < p < 1.0]
        px = round(min(cands), 4)
        wf = (book.ask is not None and book.ask <= px)
        # a BUY that takes a SHORT ledger to zero is the flatten of that
        # leg (P2 rung S0): named as the SELL side names its own, so the
        # live lane's flatten rules read one word on either sign. A long
        # book never plans a BUY toward zero, so its reasons are unchanged
        return Plan("BUY_LONG", delta, px, "flatten" if flatten else "increase toward target", wf,
                    {"delta": delta})
    # decrease: sell the long leg at his equivalent price or better
    if book.ask is None or not (0.0 < book.ask < 1.0):
        return Plan("SELL_LONG", -delta, None, "no price to rest at")
    cands = [p for p in (his_last_px, book.ask) if p is not None and 0.0 < p < 1.0]
    px = round(max(cands), 4)
    wf = (book.bid is not None and book.bid >= px)
    return Plan("SELL_LONG", -delta, px, "flatten" if flatten else "reduce toward target",
                wf, {"delta": delta})


__all__ = ["Fill", "Book", "Plan", "net_positions", "his_net", "opening_burst",
           "mirror_ratio", "bankroll_ratio", "target_shares", "plan", "BURST_S",
           "MARKET_NET_CAP_USD",
           "MIN_MOVE_USD", "MIN_MOVE_FRAC", "MIN_MARKETS", "VENUE_LEDGER_TOL_SHARES",
           # E12: Rule LE's arithmetic; the fold's late row and the reading's verdict
           "fill_clock", "pre_existing_block", "flow_net", "pre_existing_ratchet", "vwap_of",
           "LATE_FILL_S", "is_flow", "flow_reading", "FLOW_READING_ZERO", "FLOW_READING_DISAGREE",
           # E12b: the witness, the reference's clock, the ratchet on the witnessed fall
           "fills_clock", "reducing_since", "witnessed_ratchet", "FLOW_FILLS_SHRANK",
           # the E12b fold: the mirror image of the witness, the unexplained rise to the block
           "adding_since", "restored_block", "FLOW_FILLS_GREW",
           # E15: the witness on every book, the two-sided burst, the drift his fills explain
           "reducing_on", "burst_since", "drift_explained", "REDUCE_UNWITNESSED",
           "FLIP_BURST_NET", "DRIFT_FILLS_EXPLAIN"]
