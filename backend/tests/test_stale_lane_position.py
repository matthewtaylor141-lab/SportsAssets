"""The sweep lane has no staleness gate at all.

execute_copy caps age at COPY_EXEC_MAX_AGE_S and maybe_execute at
_stale_cap_for(whale) — and BOTH are guarded on `reaction is not None`.
copy_sweep calls maybe_execute(payload, None) over a candidate window
of `t.ts > now() - interval '7 days'`. So a signal a week old reaches
the order path with no age check whatsoever.

On PRICE that is defensible: the FOK limit is his price or better, so a
market that ran away from him simply does not fill.

On POSITION it is not. A week-old buy is a buy he may since have
exited, and entering a position the whale has already left is exactly
the divergence today's exit work exists to close — arriving through the
other door, on the lane nobody was watching.

Found by the as-designed audit.
"""

import asyncio
import inspect

import pytest

from sportsassets import live_executor as le


class _Pool:
    def __init__(self, siblings=(("B",),), mine=0.0, sib=0.0,
                 raise_on=None):
        self._s = [{"token_id": t[0]} for t in siblings]
        self._mine, self._sib, self._raise = mine, sib, raise_on

    async def fetch(self, *_a):
        if self._raise == "fetch":
            raise RuntimeError("db down")
        return self._s

    async def fetchrow(self, *_a):
        if self._raise == "fetchrow":
            raise RuntimeError("db down")
        return {"mine": self._mine, "sib": self._sib}


def _h(pool, asset="A", whale="rn1"):
    return asyncio.run(le.whale_still_holds(pool, asset, whale))


class TestTheHoldingIsNetOfMerges:
    def test_an_open_position_reads_true(self):
        assert _h(_Pool(mine=100.0, sib=0.0)) is True

    def test_a_fully_exited_position_reads_false(self):
        """He bought A 100, exited by buying B 100. Both legs flat."""
        assert _h(_Pool(mine=100.0, sib=100.0)) is False

    def test_a_partially_exited_position_still_reads_true(self):
        assert _h(_Pool(mine=100.0, sib=40.0)) is True

    def test_an_over_exit_reads_false(self):
        assert _h(_Pool(mine=100.0, sib=140.0)) is False

    def test_it_uses_the_same_net_arithmetic_as_the_classifier(self):
        a = inspect.getsource(le.whale_still_holds)
        b = inspect.getsource(le.classify_exit)
        for frag in ("t.asset IN ($1, $2)",
                     "CASE WHEN t.side='BUY' THEN t.size"):
            assert frag in a and frag in b, frag


class TestUnknowableMeansPROCEED:
    """Refusing everything we cannot measure would close the sweep lane
    entirely, and the lane is not the problem."""

    @pytest.mark.parametrize("pool", [
        _Pool(siblings=()),                 # unenriched token
        _Pool(siblings=(("B",), ("C",))),   # not binary
        _Pool(raise_on="fetch"),
        _Pool(raise_on="fetchrow"),
    ])
    def test_it_returns_none_not_false(self, pool):
        assert _h(pool) is None

    def test_a_blank_asset_or_whale_is_none(self):
        assert _h(_Pool(), asset="") is None
        assert _h(_Pool(), whale="") is None

    def test_the_caller_only_refuses_on_an_explicit_False(self):
        src = inspect.getsource(le.maybe_execute)
        assert "if _held is False:" in src, \
            "`if not _held` would refuse on None and close the lane"


class TestItOnlyRunsOnTheStaleLane:
    def test_the_check_is_guarded_on_reaction_is_none(self):
        src = inspect.getsource(le.maybe_execute)
        i = src.index("whale_still_holds(")
        assert "if reaction is None:" in src[max(0, i - 900):i]

    def test_a_fresh_copy_does_not_pay_for_it(self):
        """Fresh signals are seconds old; asking would add a query to
        the hot path to answer a question that cannot have changed."""
        src = inspect.getsource(le.maybe_execute)
        assert src.count("whale_still_holds(") == 1

    def test_the_rejection_says_what_happened(self):
        src = inspect.getsource(le.maybe_execute)
        assert "he no longer holds this leg" in src

    def test_the_refusal_is_RECORDED_on_a_row(self):
        """It sits after the live_orders INSERT, beside the
        stale-signal cap, on purpose: a refusal that writes no row is a
        copy that vanishes without a trace, and at a 1.39% fill rate an
        unrecorded drop is indistinguishable from a market we chose not
        to trade."""
        src = inspect.getsource(le.maybe_execute)
        i = src.index("whale_still_holds(")
        assert src.index("INSERT INTO live_orders") < i
        assert "UPDATE live_orders SET status='rejected'" in src[i:i + 900]

    def test_it_sits_immediately_before_the_staleness_cap(self):
        """The two answer the same question — is this signal still
        worth acting on — and belong together."""
        src = inspect.getsource(le.maybe_execute)
        assert src.index("whale_still_holds(") < src.index("_stale_cap =")
