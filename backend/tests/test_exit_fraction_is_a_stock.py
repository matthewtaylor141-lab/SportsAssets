"""The trade lane's exit fraction is CUMULATIVE, not per-trade.

Owner, 2026-08-30: "I need you to get the cash outs live and running
(specifically RN1)" — after the census showed exits_sold=0 with
mx_below_floor as the largest refusal that found a real position of ours.

THE DEFECT. classify_exit hands mirror_exit `qty / open_sh`: this one
complement buy over what the whale still held AT THAT MOMENT. The
denominator shrinks in lockstep with him. So a whale who trims 5% of
what is LEFT, over and over, reads exactly 0.05 on every single
observation, is refused by the 10% floor every single time, and walks
out of the whole position while we hold 100% of ours. The fraction can
never ratchet, because his remaining shrinks with his trims. Only a
constant-ABSOLUTE-size trimmer ever crosses the floor.

Measured on probe run 1407: mx_below_floor was 94 of the 191 exits that
found a filled position of ours — 49%, the largest single refusal — and
exits_sold was 0.

This is the same flow-vs-stock defect already documented and fixed for
the POSITION lane (live_executor EXIT_PENDING_REASONS, "THE SUB-FLOOR
TRIM"), left unfixed on the TRADE lane, which carries 98% of the volume
(mx_reached_position_lookup 1572 against the position lane's 23).

Driven, not source-read. The bug survived because the fraction was only
ever asserted as a number, never followed across a sequence of trims.
"""

from __future__ import annotations

import types

import pytest

from sportsassets import live_executor as le


class Row(dict):
    """asyncpg rows are mappings; live_orders rows are read by key."""


class Pool:
    """The same stub shape test_mirror_exit_quantity drives, restated
    here rather than imported — the tests dir is not a package, so a
    cross-module import fails collection."""

    def __init__(self, row: Row | None, bought=1000.0, sold=0.0):
        self.row = row
        self.bought, self.sold = bought, sold
        self.updates: list[tuple] = []
        self.claim_ok = True

    async def fetchval(self, sql, *args):
        if "UPDATE live_orders SET status='exiting'" in sql:
            return self.row["id"] if (self.row and self.claim_ok) else None
        if "copy_exit_applied" in sql:
            return None
        return None

    async def fetchrow(self, sql, *args):
        if "FROM live_orders" in sql:
            return self.row
        if "FROM trades t" in sql:
            return {"bought": self.bought, "sold": self.sold}
        return None

    async def execute(self, sql, *args):
        self.updates.append((sql, args))

    async def fetch(self, sql, *args):
        return []


async def _false(_pool):
    return False


@pytest.fixture
def bench(monkeypatch):
    """mirror_exit with the venue and the clock under our hand."""
    state = {"held": 1000, "close_calls": [], "fok_calls": [],
             "bid": 0.60}

    def _close_position(slug, slippage_bips=None):
        state["close_calls"].append((slug, slippage_bips))
        return {"ok": True, "filled_shares": state["held"],
                "fill_price": 0.58}

    def _submit_fok(slug, limit, qty, is_sell, tif):
        state["fok_calls"].append((slug, limit, qty, is_sell, tif))
        return {"ok": True, "filled_shares": qty, "fill_price": limit}

    fake_pmus = types.SimpleNamespace(
        close_position=_close_position, submit_fok=_submit_fok,
        slug_bid=lambda slug: state["bid"])

    async def _held(_slug):
        return state["held"], 0.40

    import sportsassets

    monkeypatch.setattr(sportsassets, "pmus", fake_pmus)
    monkeypatch.setitem(__import__("sys").modules,
                        "sportsassets.pmus", fake_pmus)
    monkeypatch.setattr(le, "_pm_held", _held)
    monkeypatch.setattr(le, "copy_halted", lambda: False)
    monkeypatch.setattr(le, "_whale_set", lambda _n: {"rn1"})
    monkeypatch.setattr(le, "_is_paused", _false)
    monkeypatch.setattr(le, "overspend_halt", _false)
    monkeypatch.setattr(le, "settings",
                        lambda: types.SimpleNamespace(
                            copy_probe_enabled=True))

    async def _pool():
        return le._TEST_POOL

    monkeypatch.setattr(le, "get_pool", _pool)
    return state


def _row(qty=200):
    return Row(id=7, us_market_slug="slug-x", qty=float(qty),
               entry=0.40, intent="BUY_LONG")


async def _trade_exit(pool, *, open_sh, exit_sh, trade_id=None):
    """One complement buy, exactly as classify_exit reports it."""
    le._TEST_POOL = pool
    return await le.mirror_exit({
        "side": "SELL", "whale_username": "rn1", "asset": "tokA",
        "id": trade_id,
        "closed_frac": min(1.0, exit_sh / open_sh),   # the FLOW
        "his_open_shares": open_sh, "his_exit_shares": exit_sh})


class TestTheProportionalScaleOutThatUsedToBeInvisible:
    @pytest.mark.asyncio
    async def test_a_5pct_trim_of_the_remainder_ratchets_past_the_floor(
            self, bench):
        """rn1's actual shape: trim a constant PERCENTAGE of what's left.

        Under the flow reading every one of these is 0.05 and is refused
        forever. Under a stock reading the cumulative fraction grows and
        crosses MIN_EXIT_FRAC on the third trim.
        """
        assert le.MIN_EXIT_FRAC == 0.10, "this test is calibrated to it"
        bench["held"] = 200
        remaining, reasons = 1000.0, []
        for _ in range(3):
            cut = remaining * 0.05
            pool = Pool(_row(qty=200), bought=1000.0)
            reasons.append(await _trade_exit(
                pool, open_sh=remaining, exit_sh=cut))
            remaining -= cut
        # 5%, then 9.75%, then 14.26% cumulative.
        assert reasons[0] == "mx_below_floor"
        assert reasons[1] == "mx_below_floor"
        assert reasons[2] == "mx_SOLD", (
            "the third trim puts him 14.3% out of his book and we must "
            f"follow; got {reasons[2]}")

    @pytest.mark.asyncio
    async def test_the_flow_reading_alone_would_refuse_all_three(self):
        """The control. Same trims, read as a flow, never cross 10%."""
        remaining = 1000.0
        for _ in range(3):
            cut = remaining * 0.05
            assert cut / remaining < le.MIN_EXIT_FRAC
            remaining -= cut


class TestWeNeverOverSell:
    @pytest.mark.asyncio
    async def test_selling_is_sized_to_a_TARGET_not_a_slice(self, bench):
        """A cumulative fraction times a shrinking holding compounds.

        He is 20% out, then 40% out. Slice sizing takes 20% of 200 then
        40% of 160 and leaves 96 where he holds 120. Target sizing leaves
        exactly 120.
        """
        bench["held"] = 200
        pool = Pool(_row(qty=200), bought=1000.0)
        r1 = await _trade_exit(pool, open_sh=1000.0, exit_sh=200.0)
        assert r1 == "mx_SOLD"
        assert bench["fok_calls"][-1][2] == 40, \
            "20% of our 200 is 40 shares"

        # We now hold 160; he goes to 40% out in total.
        bench["held"] = 160
        pool2 = Pool(_row(qty=200), bought=1000.0)
        r2 = await _trade_exit(pool2, open_sh=800.0, exit_sh=200.0)
        assert r2 == "mx_SOLD"
        sold_2 = bench["fok_calls"][-1][2]
        assert sold_2 == 40, (
            "target 60% of 200 = 120 held, from 160 => sell 40; a slice "
            f"rule would have sold 40% of 160 = 64. got {sold_2}")

    @pytest.mark.asyncio
    async def test_it_never_sells_more_than_the_venue_holds(self, bench):
        """Our ledger row says 200; the venue holds 30. Sell 30, never
        the row's number — the clamp is what stops an exit becoming a
        short."""
        bench["held"] = 30
        pool = Pool(_row(qty=200), bought=1000.0)
        r = await _trade_exit(pool, open_sh=1000.0, exit_sh=900.0)
        assert r == "mx_SOLD"
        assert bench["close_calls"] or bench["fok_calls"]
        if bench["fok_calls"]:
            assert bench["fok_calls"][-1][2] <= 30

    @pytest.mark.asyncio
    async def test_already_at_the_target_sells_nothing_again(self, bench):
        """The self-correcting property, in the direction that matters.

        He is 20% out and we already hold exactly 80% — a re-observation
        of the same state must NOT sell another slice. It pins instead,
        which is what stops a staircase of repeated trims on one move.
        """
        bench["held"] = 160
        pool = Pool(_row(qty=200), bought=1000.0)
        r = await _trade_exit(pool, open_sh=1000.0, exit_sh=200.0)
        assert bench["fok_calls"] == [], f"sold again at target: {r}"
        assert r == "mx_exit_rounds_to_zero"
        assert r in le.EXIT_PENDING_REASONS


class TestTheOtherLaneIsUntouched:
    @pytest.mark.asyncio
    async def test_the_position_lane_keeps_its_supplied_flow(self, bench):
        """whale_exits supplies closed_frac with NO share terms, and it
        is already pinned and pre-filtered at MIN_SHRINK, so it must
        keep ratcheting the way it does today. Rewriting it here would
        have silently changed the one lane that was already correct."""
        bench["held"] = 200
        pool = Pool(_row(qty=200), bought=1000.0)
        le._TEST_POOL = pool
        r = await le.mirror_exit({
            "side": "SELL", "whale_username": "rn1", "asset": "tokA",
            "closed_frac": 0.25})           # no his_* terms
        assert r == "mx_SOLD"
        assert bench["fok_calls"][-1][2] == 50, \
            "25% of our 200, read exactly as supplied"

    @pytest.mark.asyncio
    async def test_a_missing_denominator_falls_back_to_the_flow(
            self, bench):
        """bought == 0 must not divide by zero or invent a fraction: it
        keeps the reading we already had."""
        bench["held"] = 200
        pool = Pool(_row(qty=200), bought=0.0)
        r = await _trade_exit(pool, open_sh=1000.0, exit_sh=250.0)
        assert r == "mx_SOLD"
        assert bench["fok_calls"][-1][2] == 50, "25% flow, unchanged"


class TestItStillRefusesWhatItShould:
    @pytest.mark.asyncio
    async def test_a_genuinely_tiny_cumulative_exit_is_still_refused(
            self, bench):
        """The floor is not removed, only measured on the right quantity.
        A whale 2% out of his book is still not worth a spread."""
        bench["held"] = 200
        pool = Pool(_row(qty=200), bought=1000.0)
        r = await _trade_exit(pool, open_sh=1000.0, exit_sh=20.0)
        assert r == "mx_below_floor"
        assert bench["fok_calls"] == [] and bench["close_calls"] == []

    @pytest.mark.asyncio
    async def test_a_full_exit_still_flattens(self, bench):
        """He is out; so are we. FULL_EXIT_FRAC is read off the same
        cumulative number, so a whale who leaves over many trims now
        reaches it — under the flow reading he never did unless he left
        in one trade."""
        bench["held"] = 200
        pool = Pool(_row(qty=200), bought=1000.0)
        r = await _trade_exit(pool, open_sh=1000.0, exit_sh=990.0)
        assert r == "mx_SOLD"
        assert len(bench["close_calls"]) == 1, \
            "we are the whole position, so the no-price flatten applies"
