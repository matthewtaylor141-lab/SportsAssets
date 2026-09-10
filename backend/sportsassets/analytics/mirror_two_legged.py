"""THE TWO-LEGGED READING (E38, 2026-09-10), MEASURED IN SHADOW FIRST.

NOTHING HERE CHANGES WHAT THE LIVE MIRROR SENDS. The live lane's order
paths are untouched by this lane; every target this module computes is
RECORDED by the shadow and read by an operator. The live switch is a
later lane, after the owner has read the shadow's numbers.

THE OBJECTIVE IS HIS MATCHED PAIRS WHERE THE PAIR CLEARS -- NOT HIS
NET, AND NOT HIS GROSS. Copying both of his legs at one ratio was the
first shape of this lane and it is provably the wrong objective: it
reproduces his LOSING pairs faithfully alongside his winning ones. Split
his matched book by sport over 7 days (hard2/hismatched_1452.txt:8-16;
pairs = matched_cost / pair_cost, return = pairs x $1.00, profit =
return - matched_cost) and the whole +$180,817 is four sports carrying
two:

    Soccer        $3,463,724 at 0.9325  ->  +$250,725
    NFL             $973,937 at 0.9730  ->   +$27,026
    Other-Sports  $1,807,006 at 0.9918  ->   +$14,940
    MMA               $9,071 at 0.7679  ->    +$2,742
    unclassified        $836 at 0.9975  ->        +$2
    MLB               $2,891 at 1.0014  ->        -$4
    Non-Sports      $193,473 at 1.0382  ->    -$7,119
    Tennis        $7,950,277 at 1.0136  ->  -$106,673

Soccer alone is 138.7% of the whole book's +$180,817 (all row, 0.9876),
and TENNIS -- his LARGEST matched book at 68.0% of his tennis cost --
loses 1.36c on every matched dollar. A ratio applied to both legs of
every market buys that tennis book too.

So the target is the MATCHED PAIR, admitted PER MARKET by its own pair
price: we form a pair only where the pair we could ACTUALLY form at the
current quotes clears 1.00 less the fee. His RESIDUAL -- the part of
his position that is not matched, which is what today's `his_net` is --
is RECORDED beside it and is NEVER a target here.

AND THE PAIR WE COULD ACTUALLY FORM IS THE POST-ONLY ONE. Both outcomes
share ONE book here, so crossing both touches costs ask_long +
(1 - bid_long) = 1 + THE SPREAD, exactly -- a taking pair can never
clear 1.00, on any book, ever. Resting on both bids costs 1 - the
spread, and that is also the only order this system sends (E31: the
mirror is a maker, every order a post-only rest). So the whole
economics reduce to HALF THE SPREAD PER CONTRACT against the fee,
gated by whether BOTH rests fill -- which is what the shadow measures
(`two_leg_both_obtainable`). The taking pair is carried beside it only
so that identity is visible in the data rather than assumed.

THE MANDATE, verbatim from the owner (2026-09-10 ~16:0xZ): "when he
wins, we need to win proportionally, when he loses, we need to lose
proportionately."

THE DIAGNOSIS THIS MODULE ANSWERS. The mirror's unit is HIS NET per
market -- `his_net` on mirror_books, mi.his_net(his_long, his_other)
over the collapsed fills workers/mirror_shadow.py:415 `his_fills`
returns (mirror_shadow.py:2301). A net cancels his MATCHED pairs, and
58.6% of his cost buys both outcomes of the same market
(hard2/hismatched_1452.txt:8: 7 days, cost $24,579,066, matched
$14,401,215, average pair cost 0.9876 against a $1.00 settlement, ZERO
sells in 77,230 fills). Our own staked over the same window was
$154,147 (hard2/byleague_1434.txt:33), 0.63% of his gross while the
configured ratio is 10% of his net. On 296 paired markets we staked
7.4% of his money and took 14.9% of his losses: our ROI -11.80%
against his -2.78%, a ratio of 4.24 where proportional is 1.00.

WHAT THIS MODULE IS. Two pure functions and their record types:

  his_legs(fills, long_asset, other_asset)
      His PER-TOKEN position on one market -- for EACH of the two
      outcome tokens, the shares he holds and his volume-weighted cost
      -- instead of the single net today's sizing reads.

  our_pair(legs, ratio, quotes, fee_per_contract=..., cap_usd=None)
      Our MATCHED-PAIR target at the ratio, admitted by the pair price
      we could actually pay, with his residual named beside it and
      never sized.

THE IDENTITY THAT MAKES THE REST SAFE. `his_legs` derives its shares
from mi.net_positions over the SAME collapsed fill list `his_fills`
returns (D1's key, docs/mirror-coverage.md section 43,
mirror_shadow.py:480's chain-first collapse) and forms its net by
calling mi.his_net on those two figures -- the same call
mirror_shadow.py:2301 makes. So

    legs.long_shares - legs.other_shares == legs.net == today's his_net

holds BY CONSTRUCTION, not by agreement of two arithmetics, and
test_e38_two_legged.py pins it on real fixtures. If the legs did not
reconcile to the net, everything downstream would be wrong.

PURE. This module reads no environment of its own, opens no
connection, sends nothing. The sizing constants are IMPORTED from the
modules that own them (mirror_live_rules, mirror) and never restated
here.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, NamedTuple

from . import mirror as mi
from .mirror_live_rules import (
    MIRROR_CLIP_USD,
    MIRROR_RATIO,
    MIRROR_SMALL_BET_USD,
    open_ratio,
)

__all__ = [
    "HisLegs", "OurPair", "Quotes",
    "his_legs", "both_sides_cost", "two_leg_open_ratio", "our_pair",
    "pair_cost", "leg_prices", "leg_quotes", "pair_admissible",
    "breakeven_fee", "FEE_PER_CONTRACT",
]

# THE FEE, PER CONTRACT, AS A PARAMETER -- NOT A KNOB AND NOT AN
# ENVIRONMENT READ. A pair is TWO contracts, so a pair pays 2 x this,
# and a pair is money only while
#
#     pair_cost + 2 x fee_per_contract  <  1.00
#
# The default is 0.0: the venue's real per-contract figure lands from a
# live probe, and until it does the shadow must not pretend to know it.
# Every caller may pass its own; `breakeven_fee` inverts the line so a
# reading says what fee the market could bear rather than assuming one.
FEE_PER_CONTRACT = 0.0


def _num(value: Any) -> float | None:
    """A figure this module may compute with: a finite int or float.
    None, a bool, a string (even a numeric one), NaN or an infinity is
    unreadable and answers None -- the caller names it, never a guess.
    The same rule mirror_live_rules._num applies; restated here only so
    this module imports no private name."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


class HisLegs(NamedTuple):
    """HIS position on one market, BOTH legs kept.

    `long_asset` / `other_asset` are the pair as the mapping named them
    (mirror_shadow.map_market): the venue's LONG side and its
    complement. `long_shares` / `other_shares` are mi.net_positions'
    figures for those two tokens -- BUY adds, SELL subtracts, floored at
    zero -- so they are the same numbers the row's his_long / his_other
    already carry.

    `long_cost_usd` / `other_cost_usd` are his BUY dollars on each leg
    (size x price, BUYS only: he has no sells -- 0 in 77,230 fills,
    hard2/hismatched_1452.txt:20) and `long_bought_sh` / `other_bought_sh`
    the shares those dollars bought, so the vwaps are cost / bought --
    the same arithmetic the read-only `his-matched` preset computes
    (.github/workflows/render-ops.yml:379, `cost / NULLIF(bought, 0)`).

    `net` is mi.his_net(long_shares, other_shares) -- THE CALL, not a
    copy of it. `matched_shares` is min(long, other), the part a net
    erases; `residual_shares` is abs(net), the part a net keeps;
    `residual_asset` is the token the residual sits on, None when the
    legs are equal (a pure matched book, whose net is 0 and whose
    target today is a flatten).

    `his_pair_cost` is long_vwap + other_vwap -- HIS achieved cost for
    one matched pair against a $1.00 settlement, the per-market figure
    the 0.9876 average is made of. None when either leg bought nothing.
    """
    long_asset: str | None
    other_asset: str | None
    long_shares: float
    other_shares: float
    long_bought_sh: float
    other_bought_sh: float
    long_cost_usd: float
    other_cost_usd: float
    long_vwap: float | None
    other_vwap: float | None
    net: float
    matched_shares: float
    residual_shares: float
    residual_asset: str | None
    his_pair_cost: float | None

    @property
    def both_sides(self) -> bool:
        """He holds BOTH outcomes: the case a net cannot see."""
        return self.long_shares > 0.0 and self.other_shares > 0.0

    @property
    def gross_shares(self) -> float:
        """His shares across both legs (matched counted twice: it is two
        contracts, and it costs two contracts' money)."""
        return round(self.long_shares + self.other_shares, 6)

    @property
    def his_cost_usd(self) -> float:
        """His dollars on this market, at HIS OWN fill prices -- readable
        with no venue quote at all, so an UNMAPPED market has this figure
        too and the coverage question has a denominator."""
        return round(self.long_cost_usd + self.other_cost_usd, 4)


def his_legs(fills: Iterable[dict], long_asset: str | None,
             other_asset: str | None) -> HisLegs:
    """HIS PER-TOKEN POSITION on one market from the fills
    `mirror_shadow.his_fills` returns.

    THE COLLAPSE IS NOT REDONE HERE. `fills` is already D1's list: the
    dedup/collapse key lives in the SQL (mirror_shadow.py:415-505, the
    chain-first `has_net_leg` window) and this function reads what it
    kept, exactly as mirror_shadow.py:2245 does. Passing a raw trades
    list here would read a different book; that is the caller's
    contract, and the shadow honours it by handing over the same
    `fills` object it derived his_long / his_other from.

    Shares come from mi.net_positions -- the reader today's row uses --
    and the net from mi.his_net over those two figures, so
    `long_shares - other_shares == net` is the SAME arithmetic the row
    already carries, not a second one that could drift.

    A missing asset name reads that leg at zero: a market whose long
    token the catalogue could not name (the row's `long_token_unknown`)
    is one-legged here, never a guess.
    """
    fl = list(fills or ())
    pos = mi.net_positions(fl)
    la = str(long_asset) if long_asset else None
    oa = str(other_asset) if other_asset else None
    long_sh = float(pos.get(la, 0.0)) if la else 0.0
    other_sh = float(pos.get(oa, 0.0)) if oa else 0.0

    bought: dict[str, float] = {}
    cost: dict[str, float] = {}
    for f in fl:
        d = f if isinstance(f, dict) else getattr(f, "__dict__", {})
        asset = str(d.get("asset") or "")
        if not asset or str(d.get("side") or "").upper() != "BUY":
            continue
        size, px = _num(d.get("size")), _num(d.get("price"))
        if size is None or px is None or size <= 0 or not (0.0 < px < 1.0):
            continue
        bought[asset] = bought.get(asset, 0.0) + size
        cost[asset] = cost.get(asset, 0.0) + size * px

    def _leg(a: str | None) -> tuple[float, float, float | None]:
        if not a:
            return 0.0, 0.0, None
        sh, usd = round(bought.get(a, 0.0), 6), round(cost.get(a, 0.0), 6)
        return sh, usd, (round(usd / sh, 6) if sh > 0 else None)

    lb, lc, lv = _leg(la)
    ob, oc, ov = _leg(oa)
    net = mi.his_net(long_sh, other_sh)          # THE call, never a copy
    matched = round(min(long_sh, other_sh), 6)
    residual = round(abs(net), 6)
    if long_sh > other_sh:
        res_asset = la
    elif other_sh > long_sh:
        res_asset = oa
    else:
        res_asset = None                         # equal legs: pure matched
    return HisLegs(long_asset=la, other_asset=oa,
                   long_shares=long_sh, other_shares=other_sh,
                   long_bought_sh=lb, other_bought_sh=ob,
                   long_cost_usd=lc, other_cost_usd=oc,
                   long_vwap=lv, other_vwap=ov,
                   net=net, matched_shares=matched, residual_shares=residual,
                   residual_asset=res_asset,
                   his_pair_cost=(round(lv + ov, 6) if (lv is not None and ov is not None)
                                  else None))


def both_sides_cost(fills: Iterable[dict]) -> tuple[bool, float, int]:
    """(he holds both outcomes, his BUY dollars on the market, tokens
    held) WITHOUT a mapping: read off the fills' own tokens, so an
    UNMAPPED market -- where no long/other assignment exists -- still
    contributes its dollars to the coverage denominator.

    A condition with more than two tokens held is not a binary pair;
    it reports its dollars and its token count, and `both_sides` is
    True only for exactly two held tokens. Pure."""
    fl = list(fills or ())
    pos = mi.net_positions(fl)
    held = [a for a, v in pos.items() if v > 0.0]
    usd = 0.0
    for f in fl:
        d = f if isinstance(f, dict) else getattr(f, "__dict__", {})
        if str(d.get("side") or "").upper() != "BUY":
            continue
        size, px = _num(d.get("size")), _num(d.get("price"))
        if size is None or px is None or size <= 0 or not (0.0 < px < 1.0):
            continue
        usd += size * px
    return len(held) == 2, round(usd, 4), len(held)


def leg_prices(mark: Any) -> tuple[float, float] | None:
    """(long-leg price, other-leg price) from the row's mark: the mark
    and one minus it. None when the mark is unreadable or off the
    ladder (0.01 to 0.99), the SAME window mirror_live_rules.mirror_target
    admits a mark on -- a subnormal mark would divide a dollar cap into
    an infinity of shares. Pure."""
    m = _num(mark)
    if m is None or not (0.01 <= m <= 0.99):
        return None
    return m, round(1.0 - m, 6)


def pair_cost(px_long: Any, px_other: Any) -> float | None:
    """The cost of ONE matched pair at two leg prices: long + other,
    against a $1.00 settlement. None when either price is unreadable.
    Under 1.00 the pair is money; at or over it, it is not. Pure."""
    a, b = _num(px_long), _num(px_other)
    if a is None or b is None:
        return None
    return round(a + b, 6)


def two_leg_open_ratio(legs: HisLegs, mark: Any) -> float:
    """THE RATIO A TWO-LEGGED BOOK WOULD OPEN AT -- open_ratio's rule
    read against his GROSS, never his net.

    E1/U12's exact-copy door (mirror_live_rules.open_ratio,
    MIRROR_SMALL_BET_USD $10) asks whether HIS DOLLARS are under the
    small-bet line, and today it measures those dollars as |net| x px.
    That question was written for one leg. On a two-legged book the net
    is the WRONG measure of his money by construction: a market where
    he holds 12,000 long and 11,998 other has a net of 2 -- $1 at a
    0.50 mark, comfortably "a bet under $10" -- while his actual stake
    is about $12,000. Reading the net there would open an EXACT-COPY
    book on a five-figure position.

    So the gross is the reading: long_shares x mark + other_shares x
    (1 - mark), the collateral both legs commit -- and the door is
    then at most as open as today's, never wider. FAIL CLOSED: an
    unreadable gross is not small (MIRROR_RATIO), exactly as
    open_ratio treats an unreadable net.

    ON A ONE-LEGGED MARKET THIS IS open_ratio EXACTLY. With one leg at
    zero the gross IS |net| x px, so the two readings agree to the
    float -- pinned in test_e38_two_legged.py. Pure."""
    prices = leg_prices(mark)
    if prices is None:
        return MIRROR_RATIO
    px_l, px_o = prices
    if not legs.both_sides:
        # one leg (or none): his dollars are the net's, and the rule
        # that governs the live lane is the rule -- called, not copied
        return open_ratio(legs.net, mark)
    gross = legs.long_shares * px_l + legs.other_shares * px_o
    line = _num(MIRROR_SMALL_BET_USD)
    g = _num(gross)
    if g is None or line is None or line <= 0:
        return MIRROR_RATIO
    return 1.0 if g < line else MIRROR_RATIO




class Quotes(NamedTuple):
    """The venue's CURRENT touch on EACH leg, in each leg's own price.

    The mirror reads ONE book per market (mirror_shadow._paced_bbo at
    :1334, quoted in LONG-token terms), and on a binary market the other
    outcome is that book's complement: a BUY of the other leg at q is a
    SELL of the long leg at 1 - q. So

        ask_other = 1 - bid_long        bid_other = 1 - ask_long

    and no second venue read exists or is made -- the shadow's venue
    budget is unchanged by this lane (its four paced call sites are
    pinned at test_e11_venue_gate.py:456).

    THE COMPLEMENT IS SOUND ON THIS VENUE because every condition here
    is BINARY: market_tokens must carry exactly two tokens for a
    condition (mirror_shadow.py:1011) at complementary indices
    (map_lane.pair_agrees, map_lane.py:596 and :603), and a three-way
    game is three separate binary markets -- a soccer 1X2 is a home
    slug, an away slug and a `-draw` slug (render-ops.yml:599), and even
    an exact score is one market per score line.

    `src` names where the other leg's quote came from and is the ONLY
    honest way to read the figures: 'complement' when the identity
    above holds, and 'per_side_unread' when it does NOT -- a per-side
    market resolves each token BUY_LONG on its OWN slug
    (mirror_shadow._choose_long at :747), the row keeps only the larger
    side's slug, and the other leg's book is one this worker never
    read. Those rows carry no pair cost at all: FAIL CLOSED, a pair
    price we cannot see is not a pair price we guess. (The live lane
    refuses per-side markets anyway -- `per_side_unsupported`.)"""
    bid_long: float | None
    ask_long: float | None
    bid_other: float | None
    ask_other: float | None
    src: str

    @property
    def take_pair(self) -> float | None:
        """WHAT ONE PAIR COSTS IF WE CROSS BOTH TOUCHES NOW: ask on leg
        A + ask on leg B. THE achievable pair cost -- the figure his own
        pair cost is compared against, and the figure admissibility is
        decided on."""
        return pair_cost(self.ask_long, self.ask_other)

    @property
    def rest_pair(self) -> float | None:
        """What one pair costs if BOTH legs are POST-ONLY rests at their
        own touch: bid on leg A + bid on leg B. On one complementary
        book that is bid_long + (1 - ask_long) = 1 - spread, so it
        always clears 1.00 by the spread -- and is obtainable only if
        BOTH rests fill, which is what the shadow judges. That joint
        fill, not this price, is the real question."""
        return pair_cost(self.bid_long, self.bid_other)


def leg_quotes(bid: Any, ask: Any, per_side: bool = False) -> Quotes:
    """The two legs' touch from the ONE book the shadow already read.

    `bid` / `ask` are the row's own long-side quote. A quote off the
    ladder (at or outside 0 and 1) is not a quote: it reads None, and
    its complement reads None with it -- never a 1.0 or a 0.0 that
    would make a pair look free. `per_side` True refuses both derived
    legs by name. Pure."""
    def _px(v: Any) -> float | None:
        f = _num(v)
        return f if (f is not None and 0.0 < f < 1.0) else None

    b, a = _px(bid), _px(ask)
    if per_side:
        return Quotes(bid_long=b, ask_long=a, bid_other=None, ask_other=None,
                      src="per_side_unread")
    return Quotes(bid_long=b, ask_long=a,
                  bid_other=(round(1.0 - a, 6) if a is not None else None),
                  ask_other=(round(1.0 - b, 6) if b is not None else None),
                  src="complement")


def pair_admissible(cost: Any, fee_per_contract: Any = FEE_PER_CONTRACT) -> bool | None:
    """Does this pair clear? `cost` + 2 x fee < 1.00.

    A pair is TWO contracts, so it pays the per-contract fee twice --
    which is exactly why the break-even fee below is the margin HALVED.
    None when the pair cost is unreadable (no quote, a per-side market):
    UNKNOWN, never admissible. An unreadable or negative fee is not zero
    either -- it refuses. Pure, and the money direction is closed."""
    c, f = _num(cost), _num(fee_per_contract)
    if c is None:
        return None
    if f is None or f < 0:
        return False
    return bool(c + 2.0 * f < 1.0)


def breakeven_fee(cost: Any) -> float | None:
    """THE FEE PER CONTRACT THIS PAIR COULD BEAR: (1.00 - pair cost) / 2.

    The margin is 1.00 - cost over the pair; a pair pays two per-contract
    fees, so the break-even per contract is half the margin. Negative
    when the pair does not clear at any fee -- his tennis book, at
    1.0136, is -0.0068 a contract before we pay anyone anything. None
    when the cost is unreadable. This is the reading that lets an
    operator ask "what fee can we afford?" instead of assuming one the
    venue has not told us. Pure."""
    c = _num(cost)
    return None if c is None else round((1.0 - c) / 2.0, 6)


class OurPair(NamedTuple):
    """OUR MATCHED-PAIR target on one market, and his residual beside it.

    `pair_target` is WHOLE PAIRS -- one share of each leg -- so
    `long_target == other_target == pair_target` by construction. There
    is no directional leg here at all: the residual is his and stays
    his.

    THE TWO PAIR PRICES, AND WHY ONLY ONE OF THEM CAN EVER CLEAR.
    `take_pair_cost` is what a pair costs if we CROSS both touches now
    (ask A + ask B). On this venue the two outcomes share ONE book, so
    ask_other is the complement of bid_long and

        take_pair_cost = ask_long + (1 - bid_long) = 1 + the spread

    EXACTLY -- it is 1.00 or worse on every book that has ever existed,
    and `take_admissible` is therefore False by arithmetic and not by
    market conditions. It is carried so that identity is visible in the
    data rather than assumed. `our_pair_cost` is the POST-ONLY pair
    (bid A + bid B = 1 - the spread), which is the only pair that can
    clear, and it is the price the target is sized and admitted on --
    which is also the only price this system could pay: E31 made the
    mirror a MAKER, every order it sends is a post-only rest that never
    crosses the touch. A post-only pair is obtainable only if BOTH
    rests fill, and that is what the shadow measures.

    `his_residual_sh` / `residual_asset` record the part of his position
    a pair cannot hold. IT IS NEVER A TARGET IN THIS MODULE. It is the
    same figure today's `his_net` is (the absolute value of it),
    recorded so an operator sees exactly what the matched-only objective
    declines to copy.

    `his_pair_cost` is his own achieved cost on the same market and
    `pair_edge` is ours minus his -- the single number that decides
    whether this works, since 1.00 is where the trade is worthless and
    his figure is where he already is. `breakeven_fee_pc` is the fee per
    contract the pair could bear: the margin HALVED, because a pair is
    two contracts."""
    pair_target: int
    long_target: int
    other_target: int
    his_matched_sh: float
    his_residual_sh: float
    residual_asset: str | None
    admissible: bool | None
    take_admissible: bool | None
    our_pair_cost: float | None
    take_pair_cost: float | None
    his_pair_cost: float | None
    pair_edge: float | None
    take_edge: float | None
    margin: float | None
    breakeven_fee_pc: float | None
    fee_per_contract: float | None
    ratio: float | None
    our_pair_usd: float
    clipped: bool
    refusal: str | None


def our_pair(legs: HisLegs, ratio: Any, quotes: Quotes,
             fee_per_contract: Any = FEE_PER_CONTRACT,
             cap_usd: Any = None) -> OurPair:
    """OUR MATCHED-PAIR TARGET from HIS two legs, at the ratio, admitted
    by the pair price we could ACTUALLY pay right now.

        matched_shares = min(his leg A, his leg B) x ratio
        our pair       = that, floored to whole pairs, under the clip,
                         and ZERO unless the pair clears

    PRICED AT THE POST-ONLY PAIR, NOT AT THE TOUCH. See OurPair: the
    crossing pair is 1 + the spread by identity and can never clear, and
    the mirror is a maker by code default anyway (E31), so the pair this
    system could actually form is two post-only rests at 1 - the spread.
    `take_pair_cost` and `take_admissible` are recorded beside it.

    HIS RESIDUAL IS RECORDED AND NEVER SIZED. `residual = (the larger
    leg - the smaller) x ratio` was this lane\'s first shape; it is not a
    target now, because the residual is precisely the directional bet
    whose ROI ratio of 4.24 is the diagnosis. `his_residual_sh` carries
    it so the operator sees what is declined.

    HOW THE E1 / U12 SIZING RULES APPLY TO A MATCHED-PAIR TARGET. Every
    one of them was written for ONE directional leg; each is placed
    here deliberately, and the notes say so in the same words:

    MIRROR_RATIO (0.10) and the MIRROR_SMALL_BET_USD ($10) EXACT COPY.
        The ratio is the caller\'s; `two_leg_open_ratio` chooses it and
        measures his dollars as the GROSS of both legs, never the net
        (see that function). Gross, not his matched dollars: gross is
        the LARGER denominator, so the exact-copy door is at most as
        open as it would be on either other reading -- fail closed.

    MIRROR_CLIP_USD ($2,500) -- PER ORDER. A pair is TWO orders, so
        the clip binds each leg; and because two legs on one market
        would otherwise tie up $5,000 where today\'s single leg ties up
        $2,500, it binds THE PAIR as well. The pair cost is what a pair
        actually costs, so the bound is floor(clip / pair cost) pairs,
        which is at or under the per-leg bound for every pair price
        under 1.00. The tighter bound is the one that binds; a later
        live lane cannot inherit a widened one.

    THE WHOLE-SHARE FLOOR. One floor, on the PAIR: a fractional pair is
        not a pair, and flooring each leg separately could leave the
        legs unequal, which is a naked directional position by
        arithmetic accident. Under one whole pair the target is 0.

    THE SHORT DOOR IS NOT OPENED. Both legs are BUYS of their own
        outcome token: the target is non-negative by construction, so
        `allow_short` never applies and P2\'s door is untouched.

    Refusals (target 0, `refusal` named): `no_ratio`; `no_quote` (a leg
    we cannot price -- a per-side market, an empty book, a stale or
    off-ladder quote); `not_both_sides` (he holds one outcome only:
    there is no pair to form, and his single leg is a residual);
    `pair_over_line` (the post-only pair does not clear 1.00 less two
    fees); `net_cap_zero`; `under_one_pair`. Pure."""
    matched = _num(legs.matched_shares) or 0.0
    residual = _num(legs.residual_shares) or 0.0
    our_cost = quotes.rest_pair                 # the post-only pair: the one that can clear
    take_cost = quotes.take_pair                # 1 + the spread, recorded for the identity
    his_cost = _num(legs.his_pair_cost)
    fee = _num(fee_per_contract)
    adm = pair_admissible(our_cost, fee_per_contract)
    base = {
        "pair_target": 0, "long_target": 0, "other_target": 0,
        "his_matched_sh": round(matched, 6), "his_residual_sh": round(residual, 6),
        "residual_asset": legs.residual_asset,
        "admissible": adm, "take_admissible": pair_admissible(take_cost, fee_per_contract),
        "our_pair_cost": our_cost, "take_pair_cost": take_cost, "his_pair_cost": his_cost,
        "pair_edge": (round(our_cost - his_cost, 6)
                      if (our_cost is not None and his_cost is not None) else None),
        "take_edge": (round(take_cost - his_cost, 6)
                      if (take_cost is not None and his_cost is not None) else None),
        "margin": (round(1.0 - our_cost, 6) if our_cost is not None else None),
        "breakeven_fee_pc": breakeven_fee(our_cost),
        "fee_per_contract": fee,
        "ratio": None, "our_pair_usd": 0.0, "clipped": False, "refusal": None,
    }
    r = _num(ratio)
    cap = _num(MIRROR_CLIP_USD if cap_usd is None else cap_usd)
    if r is None or r <= 0:
        return OurPair(**{**base, "refusal": "no_ratio"})
    base["ratio"] = r
    if not legs.both_sides:
        return OurPair(**{**base, "refusal": "not_both_sides"})
    if our_cost is None:
        return OurPair(**{**base, "refusal": "no_quote"})
    if cap is None or cap <= 0:
        return OurPair(**{**base, "refusal": "net_cap_zero"})
    if adm is not True:
        return OurPair(**{**base, "refusal": "pair_over_line"})
    want = r * matched
    room = cap / our_cost                       # the clip, in whole pairs
    n = int(min(want, room))                    # ONE floor, toward zero
    if n < 1:
        return OurPair(**{**base, "refusal": "under_one_pair"})
    return OurPair(**{**base, "pair_target": n, "long_target": n, "other_target": n,
                      "our_pair_usd": round(n * our_cost, 4),
                      "clipped": bool(room < want)})
