"""The exits were never missing from the feed. They were wearing a BUY.

Owner, 2026-08-25, after reading Polymarket's global microdata:
"shorts, in the context of this data, means selling."

The venue holds ONE SIGNED net position per market. You cannot be long
and short the same market at once, so buying the complementary leg of
something you hold does not open a second bet — it retires the first,
share for share. That is why these whales show 860,669 buys and zero
sells across every ingestion source.

Until this shipped, the system did something worse than miss them.
execute_copy tested `side == "BUY"` and handed a complement buy to
maybe_execute, which sized a fresh clip and bought the leg he was
ABANDONING. Our exposure to the original outcome did not fall to zero;
it went to 2x — his leg we never closed, plus the opposite leg at a
price that necessarily sums to ~1.00 with his entry. Every exit he made
cost us twice, and the loss was active, not passive.

Misreading an ENTRY as an EXIT sells a position we meant to hold, so
every refusal here fails closed.
"""

import asyncio
import inspect

from sportsassets import live_executor as le


class _Pool:
    """Minimal stub: canned sibling rows and a canned holding."""

    def __init__(self, siblings=(("TOKEN_B",),), open_sh=1000.0,
                 raise_on=None):
        self._siblings = [{"token_id": s[0]} for s in siblings]
        self._open = open_sh
        self._raise_on = raise_on

    async def fetch(self, _sql, *_a):
        if self._raise_on == "fetch":
            raise RuntimeError("db down")
        return self._siblings

    async def fetchrow(self, _sql, *_a):
        if self._raise_on == "fetchrow":
            raise RuntimeError("db down")
        return {"open_sh": self._open}


def _c(pool, asset="TOKEN_A", whale="swisstony", size=500.0):
    return asyncio.run(le.classify_exit(pool, asset, whale, size))


class TestItRecognisesTheExit:
    def test_a_complement_buy_against_a_holding_is_an_exit(self):
        out = _c(_Pool(open_sh=1000.0), size=800.0)
        assert out is not None
        assert out["asset"] == "TOKEN_B", (
            "the position to sell is the SIBLING — the leg we hold and "
            "he is leaving, not the one he just bought")
        assert out["exit_via_asset"] == "TOKEN_A"

    def test_the_fraction_is_a_pure_share_ratio(self):
        """A complement share retires a held share 1:1, so no price
        conversion enters the fraction. That is what makes it safe to
        compute on the fast path."""
        out = _c(_Pool(open_sh=1000.0), size=250.0)
        assert out["closed_frac"] == 0.25

    def test_a_full_close_reads_as_one(self):
        assert _c(_Pool(open_sh=1000.0), size=1000.0)["closed_frac"] == 1.0

    def test_an_oversized_buy_is_capped_at_one(self):
        """Buying more complement than he holds is an exit of everything
        PLUS a new entry. We mirror the exit part and cap there rather
        than selling more of ours than he closed of his."""
        assert _c(_Pool(open_sh=1000.0), size=4000.0)["closed_frac"] == 1.0


class TestEveryRefusalFailsClosed:
    def test_no_sibling_is_not_an_exit(self):
        assert _c(_Pool(siblings=())) is None

    def test_two_siblings_refuses_rather_than_guessing(self):
        """Multi-outcome markets and negative-risk baskets have no
        single complement. Picking one would sell a real position on a
        coin flip."""
        assert _c(_Pool(siblings=(("B",), ("C",)))) is None

    def test_he_must_actually_hold_the_sibling(self):
        """No holding means this is a genuine new bet on the other side.
        Treating it as an exit would sell a position on a fresh entry
        signal — the failure this whole path exists to stop, inverted."""
        assert _c(_Pool(open_sh=0.0)) is None

    def test_a_net_flat_or_short_holding_is_not_an_exit(self):
        assert _c(_Pool(open_sh=-500.0)) is None

    def test_a_database_failure_is_not_an_exit(self):
        assert _c(_Pool(raise_on="fetch")) is None
        assert _c(_Pool(raise_on="fetchrow")) is None

    def test_missing_identifiers_are_not_an_exit(self):
        assert _c(_Pool(), asset="") is None
        assert _c(_Pool(), whale="") is None

    def test_a_zero_or_negative_size_is_not_an_exit(self):
        assert _c(_Pool(), size=0) is None
        assert _c(_Pool(), size=-10) is None

    def test_an_unparseable_size_is_not_an_exit(self):
        assert _c(_Pool(), size="banana") is None


class TestItIsWiredWhereThePoolAlreadyExists:
    """The first version called get_pool() inside execute_copy — a path
    that had never needed a pool — and added 105 seconds of connect
    retry to the copy path. The reaction-stamp test caught it reading
    135s where it expected 30s. Latency is the thing we are trying to
    REDUCE; a classifier that costs a connection is a regression even
    when its logic is right."""

    def test_it_runs_inside_maybe_execute_after_the_pool_exists(self):
        src = inspect.getsource(le.maybe_execute)
        assert "classify_exit(" in src
        assert src.index("pool = await get_pool()") < src.index(
            "classify_exit(")

    def test_execute_copy_does_not_acquire_a_pool_to_classify(self):
        src = inspect.getsource(le.execute_copy)
        assert "classify_exit" not in src
        assert "get_pool" not in src

    def test_it_classifies_before_any_order_is_sized(self):
        src = inspect.getsource(le.maybe_execute)
        assert src.index("classify_exit(") < src.index("pmus.submit_fok")

    def test_a_classified_exit_never_falls_through_to_the_buy(self):
        src = inspect.getsource(le.maybe_execute)
        i = src.index("await mirror_exit(")
        assert "return" in src[i:i + 200]


class TestTheMoneyGates:
    def test_the_exit_claims_its_row_atomically(self):
        """Five complement fills in one second spawn five execute_copy
        tasks. Without an atomic claim all five read the same 'filled'
        row and all five sell."""
        src = inspect.getsource(le.mirror_exit)
        assert "SET status='exiting'" in src
        assert "AND status='filled' RETURNING id" in src

    def test_a_lost_claim_stops_the_second_caller(self):
        src = inspect.getsource(le.mirror_exit)
        assert "already claimed by another task" in src

    def test_every_refusal_after_the_claim_releases_it(self):
        """A row stranded in 'exiting' is invisible to the settlement
        sweep and blocked from re-entry — a guard doing its job must not
        leave the position frozen."""
        src = inspect.getsource(le.mirror_exit)
        after = src[src.index("SET status='exiting'"):]
        assert after.count("_release_exit_claim") >= 4

    def test_the_sweep_cannot_re_buy_what_we_exited(self):
        from sportsassets.workers import copy_sweep

        src = inspect.getsource(copy_sweep)
        assert "'settled','cashed_out'" in src.replace("\n", "").replace(
            "                                              ", "")
        assert "exiting" in src

    def test_a_short_position_is_sellable(self):
        """netPosition is SIGNED; a short is negative. `qty <= 0` read
        every short as 'nothing here', which made shorts unsellable and
        turned a pricing bug into stranded inventory."""
        src = inspect.getsource(le._pm_held)
        assert "abs(_amt(p.get(\"netPosition\")))" in src

    def test_an_expired_market_still_returns_nothing(self):
        src = inspect.getsource(le._pm_held)
        assert 'p.get("expired")' in src
