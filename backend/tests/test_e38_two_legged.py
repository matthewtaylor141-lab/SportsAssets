"""E38 -- THE TWO-LEGGED MIRROR, MEASURED IN SHADOW FIRST.

The pins this lane rests on, in the order the brief asks for them:

  1. THE IDENTITY. His two legs reconcile to today's `his_net` EXACTLY,
     on the REAL D1 fixture (tests/test_d1_fills_dedup.py's production
     slice of the Kostyuk/Noskova market) read through the REAL
     `his_fills` SQL on a real Postgres. If the legs did not reconcile
     to the net, everything downstream would be wrong.
  2. The target arithmetic, both directions, with the E1/U12 rules that
     govern sizing: the MIRROR_SMALL_BET_USD exact-copy threshold, the
     MIRROR_CLIP_USD per-order clip, and the whole-share floor.
  3. A market where he holds ONE side only: the two-legged reading must
     equal today's reading exactly.
  4. A market where his legs are EQUAL: pure matched, residual zero --
     and today's reading sees nothing there at all.
  5. The shadow's recording, with both quotes, with one quote missing,
     with a stale quote, and with the market closed.
  6. THE MONEY PATH IS UNTOUCHED: the shadow gains no order call, and
     mirror_live's order paths are byte-identical by hash.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
import pathlib
import re
import uuid

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.analytics import mirror_two_legged as t2
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms

PKG = pathlib.Path(ms.__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


def _fills(*legs):
    """(asset, shares, price) -> the fill dicts his_fills hands back."""
    return [{"asset": a, "side": "BUY", "size": float(sh), "price": float(px), "ts": float(i)}
            for i, (a, sh, px) in enumerate(legs)]


# ============================================================ 1. THE IDENTITY

def test_e38_the_two_legs_reconcile_to_todays_net_on_the_real_d1_fixture():
    """THE PIN THE WHOLE LANE RESTS ON, on production rows through the
    production SQL. `his_fills` (D1's chain-first collapse key,
    mirror_shadow.py:415) hands back the Kostyuk/Noskova market; the
    two-legged reading of those fills must give

        long_shares - other_shares == net == mi.his_net(long, other)

    which is the SAME call mirror_shadow.py:2301 makes to fill the row's
    `his_net`. Not two arithmetics that agree -- one arithmetic, read
    twice. The raw table reads 92,145 / 39,779; the collapse reads
    55,993.4 / 29,555.0 (test_d1_fills_dedup.py:175), and BOTH legs of
    the two-legged reading come off the collapsed list."""
    from tests.test_d1_fills_dedup import (D1_CID, K, NS, _d1_rows, _drop,
                                           _insert, _scratch)

    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, _d1_rows())
            fills = await ms.his_fills(c, "rn1", D1_CID)

            # today's reading, exactly as shadow_market forms it
            pos = mi.net_positions(fills)
            his_long, his_other = float(pos.get(K, 0.0)), float(pos.get(NS, 0.0))
            todays_net = mi.his_net(his_long, his_other)

            # the two-legged reading of the SAME list
            legs = t2.his_legs(fills, K, NS)

            assert legs.long_shares == his_long and legs.other_shares == his_other
            assert legs.long_shares - legs.other_shares == legs.net == todays_net
            assert legs.net == mi.his_net(legs.long_shares, legs.other_shares)
            assert abs(legs.long_shares - 55993.4) <= 1.0
            assert abs(legs.other_shares - 29555.0) <= 1.0

            # matched + residual is the larger leg, and matched is the smaller:
            # the two parts a net erases and a net keeps
            assert legs.matched_shares == min(his_long, his_other)
            assert legs.residual_shares == abs(todays_net)
            assert legs.matched_shares + legs.residual_shares == max(his_long, his_other)
            assert legs.residual_asset == K              # his directional side

            # the row's own his_paired_sh is the same matched figure
            assert round(legs.matched_shares, 4) == round(min(his_long, his_other), 4)

            # HIS pair cost on this market: his two vwaps, against $1.00
            assert legs.his_pair_cost == round(legs.long_vwap + legs.other_vwap, 6)

            # and the RAW table would have read a different book entirely
            raw = {r["asset"]: r["s"] for r in await c.fetch(
                "SELECT asset, sum(size)::float8 AS s FROM trades GROUP BY asset")}
            assert abs(raw[K] - 92145.2) < 0.01 and abs(raw[NS] - 39779.4) < 0.01
            assert legs.long_shares < raw[K] and legs.other_shares < raw[NS]
        finally:
            await _drop(admin, c, name)

    _run(run())


@pytest.mark.parametrize("a_sh, b_sh", [
    (0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0),
    (1000.0, 999.999999), (0.000001, 0.0), (55993.4, 29555.0), (29555.0, 55993.4),
])
def test_e38_the_legs_sum_to_the_net_on_every_shape(a_sh, b_sh):
    """The identity is not a property of one fixture."""
    legs = t2.his_legs(_fills(("A", a_sh, 0.5), ("B", b_sh, 0.5)), "A", "B")
    # THE identity: the same call the row's own his_net is filled by
    assert legs.net == mi.his_net(legs.long_shares, legs.other_shares)
    # and the raw subtraction, up to his_net's OWN 6-place rounding -- that
    # rounding is the system's, not this lane's (analytics/mirror.py:128)
    assert abs((legs.long_shares - legs.other_shares) - legs.net) <= 5e-7
    assert legs.matched_shares == round(min(a_sh, b_sh), 6)
    assert legs.residual_shares == round(abs(a_sh - b_sh), 6)
    assert legs.matched_shares + legs.residual_shares == round(max(a_sh, b_sh), 6)


def test_e38_the_reading_reuses_the_collapse_key_and_never_re_reads_the_table():
    """`his_legs` takes the list `his_fills` returns; it opens no
    connection and writes no SQL of its own, so the collapse key can
    only ever be D1's. The module is pure: no venue, no pool, no env."""
    src = inspect.getsource(t2)
    for banned in ("FROM trades", "pool", "asyncpg", "os.environ", "os.getenv",
                   "pmus", "submit_fok", "requests", "httpx"):
        assert banned not in src, banned
    assert "mi.net_positions(" in inspect.getsource(t2.his_legs)
    assert "mi.his_net(" in inspect.getsource(t2.his_legs)
    # and the shadow hands it the SAME object it derived his_long from
    s = inspect.getsource(ms.shadow_market)
    assert "fills = await his_fills(pool, whale, condition_id)" in s
    assert "two_leg_block(fills, la, oa" in s and "two_leg_block(fills, None, None)" in s


# ================================================ 2. THE TARGET ARITHMETIC

def test_e38_the_matched_target_is_the_ratio_on_his_smaller_leg_both_directions():
    """matched_shares = min(his leg A, his leg B) x ratio, and the pair
    is whole pairs -- one share of each leg -- so the two leg targets
    are equal by construction and no directional position can appear by
    a rounding accident."""
    for a, b, want in ((1000.0, 400.0, 40), (400.0, 1000.0, 40), (777.0, 777.0, 77)):
        legs = t2.his_legs(_fills(("A", a, 0.40), ("B", b, 0.55)), "A", "B")
        p = t2.our_pair(legs, rules.MIRROR_RATIO, t2.leg_quotes(0.40, 0.42))
        assert p.pair_target == want, (a, b, p)
        assert p.long_target == p.other_target == p.pair_target
        assert p.his_matched_sh == min(a, b)
        # HIS RESIDUAL IS RECORDED AND NEVER SIZED
        assert p.his_residual_sh == abs(a - b)
        assert "residual" not in {k for k in p._fields if k.startswith("our")}


def test_e38_the_whole_share_floor_is_one_floor_on_the_pair():
    """Under one whole pair the target is 0 and the refusal is named; a
    fractional pair is not a pair. The floor is toward zero, mi's own."""
    q = t2.leg_quotes(0.40, 0.42)
    legs = t2.his_legs(_fills(("A", 9.0, 0.4), ("B", 9.0, 0.55)), "A", "B")
    assert t2.our_pair(legs, 0.10, q).refusal == "under_one_pair"
    assert t2.our_pair(legs, 0.10, q).pair_target == 0
    legs10 = t2.his_legs(_fills(("A", 10.0, 0.4), ("B", 10.0, 0.55)), "A", "B")
    assert t2.our_pair(legs10, 0.10, q).pair_target == 1
    # 19.9 x 0.10 = 1.99 pairs -> 1, never 2
    legs19 = t2.his_legs(_fills(("A", 19.9, 0.4), ("B", 25.0, 0.55)), "A", "B")
    assert t2.our_pair(legs19, 0.10, q).pair_target == 1


def test_e38_the_exact_copy_threshold_reads_his_gross_and_never_his_net():
    """MIRROR_SMALL_BET_USD ($10) is E1/U12's exact-copy door. On ONE
    leg it is open_ratio, called, not copied. On TWO legs the net is the
    wrong measure of his money by construction -- 12,000 long against
    11,998 other nets 2 shares, about $1, while his stake is about
    $12,000 -- so the door is read against his GROSS. Fail closed: the
    two-legged door is never wider than today's."""
    mark = 0.50
    # one leg: identical to open_ratio, to the float
    for sh in (1.0, 19.0, 19.99, 20.0, 100.0, 100000.0):
        one = t2.his_legs(_fills(("A", sh, 0.5)), "A", "B")
        assert t2.two_leg_open_ratio(one, mark) == rules.open_ratio(one.net, mark), sh
    # the trap: a huge two-sided book with a tiny net
    big = t2.his_legs(_fills(("A", 12000.0, 0.5), ("B", 11998.0, 0.5)), "A", "B")
    assert big.net == 2.0
    assert rules.open_ratio(big.net, mark) == 1.0, "the NET reads this $12,000 book as a $1 bet"
    assert t2.two_leg_open_ratio(big, mark) == rules.MIRROR_RATIO, "the GROSS does not"
    # a genuinely small two-sided book still copies whole
    small = t2.his_legs(_fills(("A", 8.0, 0.5), ("B", 8.0, 0.5)), "A", "B")
    assert small.long_shares * 0.5 + small.other_shares * 0.5 < rules.MIRROR_SMALL_BET_USD
    assert t2.two_leg_open_ratio(small, mark) == 1.0
    # unreadable is NOT small
    assert t2.two_leg_open_ratio(big, None) == rules.MIRROR_RATIO
    assert t2.two_leg_open_ratio(big, "0.5") == rules.MIRROR_RATIO


def test_e38_the_clip_binds_the_pair_and_not_merely_each_leg():
    """MIRROR_CLIP_USD ($2,500) is per ORDER and a pair is TWO orders.
    Bounding each leg alone would let one market tie up $5,000 where
    today's single leg ties up $2,500, so the clip binds the PAIR:
    floor(clip / pair cost) pairs. Fail closed."""
    q = t2.leg_quotes(0.40, 0.42)                 # post-only pair 0.98
    legs = t2.his_legs(_fills(("A", 900000.0, 0.4), ("B", 900000.0, 0.55)), "A", "B")
    p = t2.our_pair(legs, rules.MIRROR_RATIO, q)
    assert p.clipped is True
    assert p.our_pair_usd <= float(rules.MIRROR_CLIP_USD)
    assert p.pair_target == int(float(rules.MIRROR_CLIP_USD) / p.our_pair_cost)
    # the ratio alone would have wanted 90,000 pairs
    assert rules.MIRROR_RATIO * legs.matched_shares == 90000.0
    # a caller may only TIGHTEN
    assert t2.our_pair(legs, rules.MIRROR_RATIO, q, cap_usd=100.0).our_pair_usd <= 100.0
    assert t2.our_pair(legs, rules.MIRROR_RATIO, q, cap_usd=0.0).refusal == "net_cap_zero"


def test_e38_the_pair_is_admitted_by_its_own_price_and_the_fee_is_a_parameter():
    """The objective is his matched pairs WHERE THE PAIR CLEARS. A pair
    is two contracts, so it pays the per-contract fee twice and clears
    only while `pair cost + 2 x fee < 1.00`; the break-even fee per
    contract is therefore the margin HALVED. The default fee is 0
    because the venue's real figure has not been probed."""
    assert t2.FEE_PER_CONTRACT == 0.0
    legs = t2.his_legs(_fills(("A", 1000.0, 0.4), ("B", 1000.0, 0.53)), "A", "B")
    good = t2.leg_quotes(0.40, 0.42)              # post-only pair 0.98, margin 2c
    assert good.rest_pair == 0.98
    p = t2.our_pair(legs, 0.10, good)
    assert p.admissible is True and p.pair_target == 100
    assert p.margin == 0.02 and p.breakeven_fee_pc == 0.01
    # a fee at the break-even point closes the door; just under it holds
    assert t2.our_pair(legs, 0.10, good, fee_per_contract=0.01).refusal == "pair_over_line"
    assert t2.our_pair(legs, 0.10, good, fee_per_contract=0.009).pair_target == 100
    # a wide book: post-only pair 0.90, break-even 5c a contract
    wide = t2.leg_quotes(0.40, 0.50)
    assert wide.rest_pair == 0.90 and t2.breakeven_fee(0.90) == 0.05
    # an unreadable or negative fee refuses; it is never read as zero
    assert t2.pair_admissible(0.98, None) is False
    assert t2.pair_admissible(0.98, -0.01) is False
    assert t2.pair_admissible(None, 0.0) is None
    # his own figures, for the record
    assert t2.breakeven_fee(0.9325) == 0.03375       # his soccer book
    assert t2.breakeven_fee(1.0136) == -0.0068       # his tennis book: no fee saves it


def test_e38_crossing_both_touches_costs_one_plus_the_spread_by_identity():
    """THE FINDING THAT DECIDES HOW THE PRESET IS READ. The two outcomes
    share ONE book, so ask_other is the complement of bid_long and

        take pair = ask_long + (1 - bid_long) = 1 + the spread
        rest pair = bid_long + (1 - ask_long) = 1 - the spread

    exactly. A crossing pair can NEVER clear 1.00 -- not on a wide book,
    not on a tight one, not ever -- so `two_leg_pair_clears` is false by
    arithmetic and not by market conditions, and the only pair this
    system could form is the POST-ONLY one, which is also the only kind
    of order the mirror sends at all (E31: the mirror is a maker)."""
    for bid, ask in ((0.01, 0.02), (0.40, 0.42), (0.49, 0.51), (0.90, 0.99), (0.5, 0.5)):
        q = t2.leg_quotes(bid, ask)
        spread = round(ask - bid, 6)
        assert q.take_pair == round(1.0 + spread, 6), (bid, ask)
        assert q.rest_pair == round(1.0 - spread, 6), (bid, ask)
        assert q.take_pair >= 1.0
        assert t2.pair_admissible(q.take_pair) is False or spread == 0.0
    # and the target is priced at the rest, never at the touch
    legs = t2.his_legs(_fills(("A", 1000.0, 0.4), ("B", 1000.0, 0.53)), "A", "B")
    p = t2.our_pair(legs, 0.10, t2.leg_quotes(0.40, 0.42))
    assert p.our_pair_cost == 0.98 and p.take_pair_cost == 1.02
    assert p.admissible is True and p.take_admissible is False


def test_e38_the_pair_edge_is_ours_minus_his_on_the_same_market():
    """The single number that decides whether this works: 1.00 is where
    the trade is worthless and his figure is where he already is."""
    legs = t2.his_legs(_fills(("A", 1000.0, 0.40), ("B", 1000.0, 0.53)), "A", "B")
    assert legs.his_pair_cost == 0.93
    p = t2.our_pair(legs, 0.10, t2.leg_quotes(0.40, 0.42))
    assert p.his_pair_cost == 0.93 and p.our_pair_cost == 0.98
    assert p.pair_edge == 0.05, "five cents worse than him, on his own market"


# ===================================== 3. ONE SIDE ONLY == TODAY'S READING

@pytest.mark.parametrize("shares, mark", [(100.0, 0.60), (9.0, 0.50), (55993.4, 0.31),
                                          (12.0, 0.99), (12.0, 0.01)])
def test_e38_a_one_sided_market_reads_exactly_as_it_does_today(shares, mark):
    """He holds ONE outcome: there is no pair to form, his single leg IS
    a residual, and the two-legged reading must not move a number. The
    net, the ratio the rules choose and today's target are all
    unchanged, to the float."""
    legs = t2.his_legs(_fills(("A", shares, mark)), "A", "B")
    assert legs.both_sides is False
    assert legs.net == mi.his_net(shares, 0.0) == shares
    assert legs.matched_shares == 0.0 and legs.residual_shares == shares
    # the ratio the rules pick is open_ratio's, to the float
    assert t2.two_leg_open_ratio(legs, mark) == rules.open_ratio(legs.net, mark)
    # and there is no pair: named, never a silent zero
    p = t2.our_pair(legs, rules.MIRROR_RATIO, t2.leg_quotes(mark - 0.01, mark + 0.01))
    assert p.refusal == "not_both_sides" and p.pair_target == 0
    # today's single-leg target is untouched and still the whole reading
    today = mi.target_shares(rules.MIRROR_RATIO, legs.net, mark)
    assert today["target"] == int(rules.MIRROR_RATIO * shares)


# ====================================== 4. EQUAL LEGS: PURE MATCHED BOOK

def test_e38_equal_legs_are_a_pure_matched_book_that_today_cannot_see():
    """His legs equal: residual zero, matched everything. Today's
    reading nets to ZERO -- and a target of 0 is a FLATTEN to mi.plan,
    which is to say today the mirror holds nothing at all on a market
    where he has a fully hedged book. This is the case the lane exists
    for."""
    legs = t2.his_legs(_fills(("A", 500.0, 0.50), ("B", 500.0, 0.48)), "A", "B")
    assert legs.matched_shares == 500.0
    assert legs.residual_shares == 0.0 and legs.residual_asset is None
    assert legs.net == 0.0 == mi.his_net(500.0, 500.0)
    assert legs.his_pair_cost == 0.98

    # TODAY: nothing
    assert mi.target_shares(rules.MIRROR_RATIO, legs.net, 0.50)["target"] == 0
    assert rules.mirror_target(rules.MIRROR_RATIO, legs.net, 0.50,
                               rules.MIRROR_CLIP_USD)["target"] == 0

    # E38: fifty pairs, if the pair clears
    p = t2.our_pair(legs, rules.MIRROR_RATIO, t2.leg_quotes(0.49, 0.51))
    assert p.our_pair_cost == 0.98 and p.pair_target == 50
    assert p.long_target == p.other_target == 50
    assert p.his_residual_sh == 0.0

    # AND IF IT DOES NOT CLEAR, NOTHING -- named. The post-only pair is
    # 1 - the spread, so the only ways it fails to clear are a LOCKED book
    # (spread 0: the pair is exactly 1.00 and 1.00 is not a margin) or a fee
    # at or over half the spread. That is the whole economics of this lane:
    # HALF THE SPREAD PER CONTRACT against the fee.
    locked = t2.leg_quotes(0.50, 0.50)
    assert locked.rest_pair == 1.0
    assert t2.our_pair(legs, rules.MIRROR_RATIO, locked).refusal == "pair_over_line"
    assert t2.our_pair(legs, rules.MIRROR_RATIO, t2.leg_quotes(0.49, 0.51),
                       fee_per_contract=0.01).refusal == "pair_over_line"
    assert t2.breakeven_fee(t2.leg_quotes(0.49, 0.51).rest_pair) == 0.01


# ================================================= 5. THE SHADOW'S RECORD

def _block(**kw):
    fl = kw.pop("fills", _fills(("A", 1000.0, 0.40), ("B", 900.0, 0.53)))
    la = kw.pop("la", "A")
    oa = kw.pop("oa", "B")
    return ms.two_leg_block(fl, la, oa, **kw)


def test_e38_the_shadow_records_both_legs_both_quotes_and_the_three_answers():
    d = _block(mark=0.41, bid=0.40, ask=0.42, state="MARKET_STATE_OPEN")
    # his two legs
    assert d["two_leg_his_long_sh"] == 1000.0 and d["two_leg_his_other_sh"] == 900.0
    assert d["two_leg_his_long_vwap"] == 0.40 and d["two_leg_his_other_vwap"] == 0.53
    assert d["two_leg_his_pair_cost"] == 0.93
    assert d["two_leg_matched_sh"] == 900.0
    assert d["two_leg_residual_sh"] == 100.0 and d["two_leg_residual_side"] == "long"
    # the venue's CURRENT best bid and ask on EACH leg
    assert d["two_leg_bid_long"] == 0.40 and d["two_leg_ask_long"] == 0.42
    assert d["two_leg_bid_other"] == 0.58 and d["two_leg_ask_other"] == 0.60
    assert d["two_leg_quote_src"] == "complement"
    # QUESTION 2: the pair cost, and whether it clears 1.00
    assert d["two_leg_pair_cost"] == 1.02 and d["two_leg_pair_clears"] is False
    assert d["two_leg_rest_pair_cost"] == 0.98 and d["two_leg_rest_admissible"] is True
    assert d["two_leg_pair_edge"] == 0.05          # ours 0.98 against his 0.93
    assert d["two_leg_breakeven_fee"] == 0.01
    assert d["two_leg_fee_pc"] == 0.0
    # QUESTION 3: his dollars, and whether we can see this market at all
    assert d["two_leg_his_cost_usd"] == round(1000 * 0.40 + 900 * 0.53, 2)
    assert d["two_leg_mapped"] is True and d["two_leg_both_sides"] is True
    # QUESTION 1: the two post-only rests, and the verdict the judge fills in
    assert d["two_leg_rest_long_px"] == 0.40 and d["two_leg_rest_other_px"] == 0.58
    assert d["two_leg_rest_other_wire"] == 0.42
    assert d["two_leg_both_obtainable"] is None, "unjudged until a later tick reads the book"
    # our target
    assert d["two_leg_pair_target"] == 90 and d["two_leg_ratio"] == rules.MIRROR_RATIO


@pytest.mark.parametrize("bid, ask", [(None, 0.42), (0.40, None), (None, None),
                                      (0.0, 0.42), (0.40, 1.0), (0.40, 0.0)])
def test_e38_a_missing_or_off_ladder_quote_records_no_pair_price_at_all(bid, ask):
    """FAIL CLOSED. A leg we cannot price is not a leg we guess: no pair
    cost, no admissibility, no target, no rest to judge -- and the row
    still carries his legs and his dollars, because those need no quote
    and they are the coverage denominator."""
    d = _block(mark=0.41, bid=bid, ask=ask, state="MARKET_STATE_OPEN")
    assert d["two_leg_pair_cost"] is None
    assert d["two_leg_rest_pair_cost"] is None
    assert d["two_leg_rest_admissible"] is None
    assert d["two_leg_pair_target"] == 0 and d["two_leg_refusal"] == "no_quote"
    assert "two_leg_rest_long_px" not in d, "half a pair is not a pair"
    assert "two_leg_both_obtainable" not in d
    # but his side of the reading survives
    assert d["two_leg_his_long_sh"] == 1000.0 and d["two_leg_his_pair_cost"] == 0.93
    assert d["two_leg_his_cost_usd"] == 877.0
    assert d["two_leg_both_sides"] is True


def test_e38_a_stale_quote_is_the_state_the_row_carries_and_never_a_silent_price():
    """The shadow already names a stale or halted book by its venue
    state (`no mark: venue state ...`, mirror_shadow.no_mark_reason).
    The two-legged block records that state verbatim beside its own
    figures, so a pair cost can always be read against the book it came
    from -- and a market with no mark records no pair at all."""
    d = _block(mark=None, bid=None, ask=None, state="MARKET_STATE_HALTED")
    assert d["two_leg_state"] == "MARKET_STATE_HALTED"
    assert d["two_leg_pair_cost"] is None and d["two_leg_refusal"] == "no_quote"
    assert d["two_leg_his_cost_usd"] == 877.0, "his dollars still count toward coverage"
    # a quote that IS present is recorded with its state, whatever the state says
    d2 = _block(mark=0.41, bid=0.40, ask=0.42, state="MARKET_STATE_HALTED")
    assert d2["two_leg_state"] == "MARKET_STATE_HALTED" and d2["two_leg_rest_pair_cost"] == 0.98


def test_e38_a_closed_market_is_counted_and_never_priced():
    d = _block(mark=None, bid=None, ask=None, state="MARKET_STATE_RESOLVED")
    assert d["two_leg_state"] == "MARKET_STATE_RESOLVED"
    assert d["two_leg_both_sides"] is True and d["two_leg_mapped"] is True
    assert d["two_leg_pair_target"] == 0 and d["two_leg_refusal"] == "no_quote"
    assert "two_leg_both_obtainable" not in d


def test_e38_a_per_side_market_records_no_pair_price_because_we_never_read_its_book():
    """A per-side market resolves each token BUY_LONG on its OWN slug
    (mirror_shadow._choose_long), the row keeps only the larger side's
    slug, and the complement identity does NOT hold there. Fail closed,
    and say which reading it was."""
    d = _block(mark=0.46, bid=0.45, ask=0.47, state="MARKET_STATE_OPEN", per_side=True)
    assert d["two_leg_quote_src"] == "per_side_unread"
    assert d["two_leg_ask_other"] is None and d["two_leg_bid_other"] is None
    assert d["two_leg_pair_cost"] is None and d["two_leg_refusal"] == "no_quote"
    assert d["two_leg_bid_long"] == 0.45, "our own leg's book was read and is recorded"


def test_e38_an_unmapped_market_carries_the_coverage_denominator_and_nothing_else():
    """QUESTION 3 needs a denominator, and it lives on the rows with no
    mapping at all. His dollars are read at HIS OWN fill prices, so they
    exist without any venue quote."""
    d = ms.two_leg_block(_fills(("A", 400.0, 0.35), ("B", 400.0, 0.60)), None, None)
    assert set(d) == set(ms.TWO_LEG_KEYS_ALWAYS)
    assert d["two_leg_mapped"] is False and d["two_leg_both_sides"] is True
    assert d["two_leg_his_cost_usd"] == 380.0
    assert d["two_leg_tokens_held"] == 2
    # a market with more than two tokens held is not a binary pair
    d3 = ms.two_leg_block(_fills(("A", 1.0, 0.3), ("B", 1.0, 0.3), ("C", 1.0, 0.3)), None, None)
    assert d3["two_leg_both_sides"] is False and d3["two_leg_tokens_held"] == 3


def test_e38_the_judge_calls_a_pair_obtainable_only_when_BOTH_rests_filled():
    """QUESTION 1, on a real Postgres and through the REAL judge. The
    long leg's rest fills when the ask comes DOWN to it; the other
    leg's rest is a sale of the long token on the same complementary
    book, so it fills when the bid comes UP to `two_leg_rest_other_wire`.
    A pair is obtainable only when BOTH have happened inside the same
    JUDGE_TTL_S the shadow judges every other plan over. A book that
    moved only one way fills ONE leg -- which is a naked directional
    position, the exact thing this objective exists to avoid."""
    asyncpg = pytest.importorskip("asyncpg")
    from tests.test_e12_flow_only import DSN_BASE

    async def run():
        try:
            admin = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception:  # noqa: BLE001 — no local PG: skip, never fake
            pytest.skip("no local postgres for the E38 judge")
        name = "e38_" + uuid.uuid4().hex[:10]
        await admin.execute(f'CREATE DATABASE "{name}"')
        c = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
        try:
            mig = pathlib.Path(ms.__file__).resolve().parents[2] / "migrations"
            await c.execute((mig / "046_mirror_shadow.sql").read_text())
            d = _block(mark=0.41, bid=0.40, ask=0.42, state="MARKET_STATE_OPEN")
            for cid in ("c_both", "c_long", "c_other", "c_neither"):
                await c.execute(
                    "INSERT INTO mirror_shadow (whale, condition_id, reason, detail) "
                    "VALUES ('rn1', $1, 'e38', $2::jsonb)", cid, json.dumps(d))

            async def judge(cid, bid, ask):
                await ms._resolve_previous(c, {"whale": "rn1", "condition_id": cid,
                                               "bid": bid, "ask": ask})
                got = await c.fetchval(
                    "SELECT detail FROM mirror_shadow WHERE condition_id = $1", cid)
                return json.loads(got) if isinstance(got, str) else got

            # the book reached BOTH sides: ask fell to 0.40, bid rose to 0.42
            both = await judge("c_both", 0.42, 0.40)
            assert both["two_leg_fill_long"] is True and both["two_leg_fill_other"] is True
            assert both["two_leg_both_obtainable"] is True

            # the book only fell: the long rest filled, the pair did not
            lo = await judge("c_long", 0.30, 0.35)
            assert lo["two_leg_fill_long"] is True
            assert "two_leg_fill_other" not in lo
            assert lo["two_leg_both_obtainable"] is None, "still open inside the TTL"

            # the book only rose: the other leg alone
            hi = await judge("c_other", 0.50, 0.55)
            assert hi.get("two_leg_fill_long") is None and hi["two_leg_fill_other"] is True
            assert hi["two_leg_both_obtainable"] is None

            # a book that never reached either rest
            ne = await judge("c_neither", 0.405, 0.415)
            assert "two_leg_fill_long" not in ne and "two_leg_fill_other" not in ne

            # past the TTL with neither leg: NOT obtainable, and it says so
            await c.execute("UPDATE mirror_shadow SET at = now() - ($1::float8 * interval "
                            "'1 second') WHERE condition_id = 'c_neither'",
                            float(ms.JUDGE_TTL_S) + 60.0)
            ex = await judge("c_neither", 0.405, 0.415)
            assert ex["two_leg_both_obtainable"] is False and "two_leg_expired_s" in ex

            # a row we stopped reading stays NULL: unobserved is not unfilled
            await ms._resolve_previous(c, {"whale": "rn1", "condition_id": "c_long",
                                           "bid": None, "ask": None})
            still = await c.fetchval(
                "SELECT detail FROM mirror_shadow WHERE condition_id = 'c_long'")
            still = json.loads(still) if isinstance(still, str) else still
            assert still["two_leg_both_obtainable"] is None
        finally:
            await c.close()
            await admin.execute(f'DROP DATABASE "{name}"')
            await admin.close()

    _run(run())


# ============================== 6. THE MONEY PATH IS UNTOUCHED, BY HASH

# mirror_live's ORDER PATHS on the tip this lane was built from
# (43a7863), by the source hash of each function that can reach the
# venue with a write. THE LIVE MIRROR SENDS EXACTLY WHAT IT SENT: this
# lane computes and RECORDS a two-legged target and changes nothing
# about the live lane, and the live switch is a later lane the owner
# decides after reading the shadow's numbers. If one of these moves,
# either this lane touched the money path -- which it must not -- or
# another lane did and this pin needs re-cutting deliberately.
LIVE_ORDER_PATHS = {
    "_place": "f559a52bfb610ef3",
    "_place_reserved": "a83a3e9473eb7112",
    "_cancel_and_settle": "953ba5587d2ad423",
    "_cancel_open_for": "639e841a3d2109eb",
    "_cancel_frozen_open": "ec97c5bcb577363c",
    "_guarded": "961a29171c0d9168",
    "_post_only_held": "b2ab4a5719d67785",
}
MIRROR_LIVE_ON_TIP = "5f3ea048054117f0"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def test_e38_the_live_lanes_order_paths_are_byte_identical():
    for name, want in LIVE_ORDER_PATHS.items():
        fn = getattr(ml, name)
        assert _sha(inspect.getsource(fn)) == want, f"{name} moved: the money path is not this lane's"
    # and, on this tree, the whole worker file is untouched
    path = PKG / "workers" / "mirror_live.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest()[:16] == MIRROR_LIVE_ON_TIP


def test_e38_the_live_lane_never_imports_or_calls_the_two_legged_reading():
    """The two-legged target is RECORDED, never sent. Nothing in the
    live worker may read it, and the pure module may not reach back."""
    live = inspect.getsource(ml)
    for banned in ("mirror_two_legged", "two_leg_block", "our_pair", "two_leg_"):
        assert banned not in live, f"the live lane names {banned}"
    # it imports the sizing constants from mirror_live_RULES, which is the
    # pure rules module; what it must never reach is the live WORKER
    assert re.search(r"\bmirror_live\b(?!_rules)", inspect.getsource(t2)) is None
    assert "from ..workers" not in inspect.getsource(t2)


def test_e38_the_shadow_gains_no_order_call():
    """THE ASSERTION THAT FAILS IF THE MODULE GAINS AN ORDER CALL. Three
    ways at once: the named write verbs by substring; every attribute
    call on the venue handle by AST, against the read-only allowlist the
    module already uses; and the venue-write helper the live lane routes
    every order through, which this module must never name."""
    src = inspect.getsource(ms)
    for banned in ("submit_fok", "cancel_order", "close_position", "execute_manual",
                   "maybe_execute", "post_order", "place_order", "create_order",
                   "cancel_all", "submit_order", ".submit(", ".cancel("):
        assert banned not in src, f"the shadow must not reference {banned}"

    # by AST: every pmus.<attr> the module touches
    tree = ast.parse((PKG / "workers" / "mirror_shadow.py").read_text())
    touched = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id in ("pmus", "_pmus"):
            touched.add(node.attr)
    allowed = {"bbo_read", "_get_client", "account_positions_walk", "book_read",
               "market_read", "markets", "positions"}
    assert touched <= allowed, f"the shadow reached a new venue method: {sorted(touched - allowed)}"

    # the two-legged block itself calls nothing at all but the pure module
    blk = inspect.getsource(ms.two_leg_block)
    assert "await " not in blk and "pmus" not in blk and "pool" not in blk
    assert "rules_2l." in blk

    # and the venue budget is exactly what it was: four paced reads, no lane
    text = (PKG / "workers" / "mirror_shadow.py").read_text()
    assert len(re.findall(r"^\s*pace\(READ_PACING_S\)\s*$", text, re.M)) == 4
    assert text.count("FROM trades t") == 4, "no new statement reads his fills"


def test_e38_the_two_legged_judge_never_touches_the_live_compared_columns():
    """The joint-fill verdict rides the JSONB detail alone. `would_fill`
    -- the column P1's gate and `shadow_live_disagree` are read against
    -- is untouched by this lane, so the long-only rate is byte-identical
    to before it."""
    src = inspect.getsource(ms._resolve_previous)
    for tag in ("/* judge-two-leg-long */", "/* judge-two-leg-other */",
                "/* judge-two-leg-both */", "/* judge-two-leg-expire */"):
        assert tag in src, tag
    # every two-legged statement writes `detail` and only `detail`
    for chunk in src.split("judge-two-leg")[1:]:
        stmt = src[:src.index(chunk)]
        head = stmt[stmt.rindex("UPDATE mirror_shadow"):]
        assert "SET detail = " in head, head[:120]
        assert "would_fill = " not in head, "a two-legged judge moved the live-compared column"


def test_e38_the_docs_section_says_what_is_not_changed():
    doc = (PKG.parents[1] / "docs" / "mirror-coverage.md").read_text()
    i = doc.index("## 77. E38")
    section = doc[i:]
    assert "DOES NOT FIX" in section
    for phrase in ("when he wins, we need to win proportionally",
                   "WHAT IS **NOT** CHANGED, SAID FIRST", "MATCHED PAIRS WHERE THE PAIR CLEARS"):
        assert phrase in section, phrase
