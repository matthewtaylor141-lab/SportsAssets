"""How much mirror_exit actually sells, driven for real.

Every existing test of this function reads its SOURCE. That is how a
branch decided by `closed_frac >= FULL_EXIT_FRAC` kept passing tests
written about remainders, and how a call that flattens the ACCOUNT
passed tests about our copy's quantity. Two money defects on one branch:

  * pmus.close_position takes ONLY a market slug -- no side, no
    quantity, no limit -- and flattens whatever the account holds on it.
    `held` comes from _pm_held, which reads the account-wide
    netPosition. Co-holding is designed in: the no-stack carve-out lets
    a copy proceed when the other holder is the underdog sleeve, and the
    manual desk is never blocked from a slug the copy sleeve holds. So a
    whale exit flattened the underdog sleeve's shares, left its row
    reading 'filled' with nothing behind it, and booked the P&L of
    shares this row never owned onto this row.

  * the remainder branch was gated on the WHALE'S FRACTION, not on
    whether shares remained. A FULL exit that only partially filled --
    an unlimited close exhausting its slippage bound in a thin book --
    fell through to 'cashed_out' while we still held shares. The
    settlement sweep targets status='filled', mirror_exit's row query
    requires status='filled', and copy_sweep's blocking list contains
    'cashed_out': the residual would be ungraded, unsellable and
    un-re-enterable, sitting at the venue, invisible everywhere.

So this file drives the function.
"""

from __future__ import annotations

import types

import pytest

from sportsassets import live_executor as le


class Row(dict):
    """asyncpg rows are mappings; live_orders rows are read by key."""


class Pool:
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


@pytest.fixture
def bench(monkeypatch):
    """A live mirror_exit with every collaborator under our hand."""
    state = {
        "held": 400,            # what the ACCOUNT holds on the slug
        "close_calls": [],      # pmus.close_position invocations
        "fok_calls": [],        # pmus.submit_fok invocations
        "close_fills": None,    # None -> fills everything held
        "fok_fills": None,      # None -> fills what was asked
        "bid": 0.60,
    }

    def _close_position(slug, slippage_bips=None):
        state["close_calls"].append((slug, slippage_bips))
        n = state["close_fills"]
        n = state["held"] if n is None else n
        return {"ok": True, "filled_shares": n, "fill_price": 0.58}

    def _submit_fok(slug, limit, qty, is_sell, tif):
        state["fok_calls"].append((slug, limit, qty, is_sell, tif))
        n = state["fok_fills"]
        n = qty if n is None else n
        return {"ok": True, "filled_shares": n, "fill_price": limit}

    fake_pmus = types.SimpleNamespace(
        close_position=_close_position,
        submit_fok=_submit_fok,
        slug_bid=lambda slug: state["bid"])

    async def _held(_slug):
        return state["held"], 0.40

    # `from . import pmus` reads the attribute off the PACKAGE, which
    # importlib set at first import — so replacing sys.modules alone
    # leaves the real venue client in place, and the test would have
    # quietly exercised it.
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

    # mirror_exit fetches its own pool. Without this the test dials a
    # real database and hangs, which is its own small lesson about
    # source-reading tests: nothing here was ever executed before.
    async def _pool():
        return le._TEST_POOL

    monkeypatch.setattr(le, "get_pool", _pool)
    return state


async def _false(_pool):
    return False


def _row(qty=200, entry=0.40):
    return Row(id=7, us_market_slug="slug-x", qty=float(qty),
               entry=entry, intent="BUY_LONG")


async def _exit(pool, closed_frac=1.0):
    le._TEST_POOL = pool
    return await le.mirror_exit({
        "side": "SELL", "whale_username": "rn1", "asset": "tokA",
        "closed_frac": closed_frac})


def _status(pool):
    """The live_orders status write, found by WHAT IT IS.

    This used to take pool.updates[-1] and trust that the row write was
    last. The exit-dedup ledger insert now follows it, so ordering was
    never the right thing to key on -- a helper that finds the wrong
    statement reports the wrong status and the test lies about the money
    path.
    """
    hits = [(sql, a) for sql, a in pool.updates
            if "UPDATE live_orders SET status=" in sql]
    assert hits, "the row status was never written"
    sql, args = hits[-1]
    return ("cashed_out" if "cashed_out" in sql else "filled"), args


class TestItSellsOnlyOurShares:
    @pytest.mark.asyncio
    async def test_a_co_held_slug_is_NOT_flattened(self, bench):
        """The underdog sleeve holds 200 of the venue's 400; our copy is
        the other 200. A full whale exit must sell 200, not 400."""
        bench["held"] = 400
        pool = Pool(_row(qty=200))
        r = await _exit(pool)
        assert bench["close_calls"] == [], \
            "flattened a market another sleeve is holding"
        assert len(bench["fok_calls"]) == 1
        assert bench["fok_calls"][0][2] == 200
        assert bench["fok_calls"][0][3] is True
        assert r == "mx_SOLD"

    @pytest.mark.asyncio
    async def test_when_we_ARE_the_position_it_still_flattens(self, bench):
        """The flatten keeps its no-price property in the ordinary case,
        which is the whole reason it exists: an unreadable bid must not
        block an exit we already detected."""
        bench["held"] = 200
        pool = Pool(_row(qty=200))
        r = await _exit(pool)
        assert len(bench["close_calls"]) == 1
        assert bench["close_calls"][0] == ("slug-x", le.EXIT_SLIPPAGE_BIPS)
        assert bench["fok_calls"] == []
        assert r == "mx_SOLD"

    @pytest.mark.asyncio
    async def test_a_ledger_larger_than_the_venue_still_flattens(
            self, bench):
        """Our row says 200, the venue says 150. We are still the whole
        position; there is nobody else to protect."""
        bench["held"] = 150
        pool = Pool(_row(qty=200))
        await _exit(pool)
        assert len(bench["close_calls"]) == 1

    @pytest.mark.asyncio
    async def test_a_co_held_slug_with_no_bid_REFUSES(self, bench):
        """"Cannot price our own shares" must not escalate to "flatten
        somebody else's"."""
        bench["held"], bench["bid"] = 400, None
        pool = Pool(_row(qty=200))
        r = await _exit(pool)
        assert r == "mx_no_bid_for_partial"
        assert bench["close_calls"] == [] and bench["fok_calls"] == []
        assert r in le.EXIT_PENDING_REASONS

    @pytest.mark.asyncio
    async def test_a_SOLE_holding_with_no_bid_still_exits(self, bench):
        bench["held"], bench["bid"] = 200, None
        pool = Pool(_row(qty=200))
        assert await _exit(pool) == "mx_SOLD"
        assert len(bench["close_calls"]) == 1


class TestPnLIsBookedOnOurSharesOnly:
    @pytest.mark.asyncio
    async def test_a_venue_wide_fill_cannot_inflate_the_row(self, bench):
        """The clamp is the belt: even if some path reintroduces the
        flatten on a co-held slug, the row may only book what it owned.
        400 shares come back; 200 were ours."""
        bench["held"] = 400
        bench["fok_fills"] = 400          # the venue over-delivers
        pool = Pool(_row(qty=200, entry=0.40))
        await _exit(pool)
        _st, args = _status(pool)
        # Sold at sell_limit_price(0.60) = 0.58, entry 0.40.
        # Ours:      200 * 0.18 = 36.00
        # Venue-wide: 400 * 0.18 = 72.00  <- the number the row used to
        #                                    claim, on 200 shares that
        #                                    belonged to another sleeve.
        assert args[-1] == pytest.approx(36.0), \
            f"booked P&L on shares the row never owned: {args[-1]}"
        assert args[-1] != pytest.approx(72.0)

    @pytest.mark.asyncio
    async def test_the_ordinary_case_books_the_full_amount(self, bench):
        bench["held"] = 200
        pool = Pool(_row(qty=200, entry=0.40))
        await _exit(pool)
        _st, args = _status(pool)
        # close_position fills 200 @ 0.58 -> 200 * 0.18 = 36
        assert args[-1] == pytest.approx(36.0)

    @pytest.mark.asyncio
    async def test_pnl_ACCUMULATES_onto_an_earlier_partial(self, bench):
        bench["held"] = 200
        pool = Pool(_row(qty=200))
        await _exit(pool)
        # Found by content, not by position — see _status.
        sql, _a = [(q, a) for q, a in pool.updates
                   if "UPDATE live_orders SET status=" in q][-1]
        assert "pnl=COALESCE(pnl,0)+" in sql, \
            "assigning would erase what earlier legs realised"


class TestAPartiallyFilledFullExitKeepsItsShares:
    @pytest.mark.asyncio
    async def test_the_row_is_NOT_marked_cashed_out(self, bench):
        """The thin-book case: an unlimited close exhausts its slippage
        bound after 130 of 200 shares."""
        bench["held"] = 200
        bench["close_fills"] = 130
        pool = Pool(_row(qty=200))
        await _exit(pool)
        status, args = _status(pool)
        assert status == "filled", \
            "70 shares orphaned at the venue: ungraded, unsellable, " \
            "and blocking re-entry"
        assert args[1] == 70

    @pytest.mark.asyncio
    async def test_it_comes_back_as_PENDING_so_the_residual_retries(
            self, bench):
        bench["held"] = 200
        bench["close_fills"] = 130
        pool = Pool(_row(qty=200))
        r = await _exit(pool)
        assert r == "mx_partial_full_exit"
        assert r in le.EXIT_PENDING_REASONS

    @pytest.mark.asyncio
    async def test_a_FULL_fill_still_retires_the_row(self, bench):
        bench["held"] = 200
        pool = Pool(_row(qty=200))
        r = await _exit(pool)
        assert _status(pool)[0] == "cashed_out"
        assert r == "mx_SOLD"

    @pytest.mark.asyncio
    async def test_the_old_gate_reproduces_the_orphan(self, bench):
        """The defect stated as arithmetic: on a full exit the old
        condition `remaining > 0 and closed_frac < FULL_EXIT_FRAC` is
        False no matter how many shares are left."""
        remaining, closed_frac = 70, 1.0
        assert not (remaining > 0 and closed_frac < le.FULL_EXIT_FRAC)
        assert remaining > 0

    @pytest.mark.asyncio
    async def test_a_partial_exit_that_underfills_also_keeps_its_shares(
            self, bench):
        """The case the old gate DID cover, which must not regress."""
        bench["held"] = 200
        bench["fok_fills"] = 20
        pool = Pool(_row(qty=200))
        await _exit(pool, closed_frac=0.5)
        status, args = _status(pool)
        assert status == "filled"
        assert args[1] == 180


class TestTheGatesStillRefuse:
    @pytest.mark.asyncio
    async def test_a_paused_account_places_no_order(self, bench,
                                                   monkeypatch):
        async def _true(_pool):
            return True

        monkeypatch.setattr(le, "_is_paused", _true)
        pool = Pool(_row())
        assert await _exit(pool) == "mx_paused"
        assert bench["close_calls"] == [] and bench["fok_calls"] == []

    @pytest.mark.asyncio
    async def test_a_tripped_breaker_places_no_order(self, bench,
                                                    monkeypatch):
        async def _true(_pool):
            return True

        monkeypatch.setattr(le, "overspend_halt", _true)
        pool = Pool(_row())
        assert await _exit(pool) == "mx_overspend_halt"
        assert bench["close_calls"] == [] and bench["fok_calls"] == []

    @pytest.mark.asyncio
    async def test_no_position_of_ours_places_no_order(self, bench):
        pool = Pool(None)
        assert await _exit(pool) == "mx_no_position_of_ours"
        assert bench["close_calls"] == [] and bench["fok_calls"] == []

    @pytest.mark.asyncio
    async def test_a_venue_holding_nothing_places_no_order(self, bench):
        bench["held"] = 0
        pool = Pool(_row(qty=200))
        assert await _exit(pool) == "mx_venue_holds_nothing"
        assert bench["close_calls"] == [] and bench["fok_calls"] == []

    @pytest.mark.asyncio
    async def test_a_lost_claim_places_no_order(self, bench):
        pool = Pool(_row())
        pool.claim_ok = False
        assert await _exit(pool) == "mx_already_claimed"
        assert bench["close_calls"] == [] and bench["fok_calls"] == []

    @pytest.mark.asyncio
    async def test_a_dust_trim_is_below_the_floor(self, bench):
        pool = Pool(_row(qty=200))
        assert await _exit(pool, closed_frac=0.01) == "mx_below_floor"
        assert bench["close_calls"] == [] and bench["fok_calls"] == []
