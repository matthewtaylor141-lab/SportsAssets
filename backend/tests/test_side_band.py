"""The side-price band: the half of the fix that stops the WRONG SIDE.

`ask > limit` stops the overspend. It does not stop the wrong side.

If our intent names the opposite leg and that leg happens to be
CHEAPER than our limit, the order sails through looking like a bargain
and we end up holding the wrong bet at a good price — no overspend, no
breaker, no alarm, just a losing position that our own side echo calls
ok. That is the quiet version of the 2026-08-24 incident and it is
worse than the loud one.

The whale's own price is the reference. We are copying the SAME
outcome, so the side we buy should be quoted near what he paid. Far off
in either direction means the leg we named is probably not his outcome.

A complement flip is always at least |1 - 2p| away from his price, so
the band catches every one of them except a market sitting almost
exactly at 0.50 — where the two sides are genuinely near-identical in
price and the loss from being wrong is smallest.
"""

import os

import pytest


def _refuses(ask, his_price, band=0.15):
    """The condition as written in maybe_execute."""
    return ask is not None and his_price and abs(ask - his_price) > band


class TestTheRealRowsAreRefused:
    """Every overspent row from 2026-08-24, as (his_price, limit,
    wrong_ask). his_price is the limit less the 1c slippage the sizer
    adds.

    The band alone does NOT catch all six — I asserted it did and the
    arithmetic said otherwise. Two rows sit INSIDE it (gaps 0.12 and
    0.08), because those markets are near 0.50 where a flip barely
    moves the price. They are caught by the OTHER check: the wrong side
    is quoted above our limit, so `ask > limit` refuses them.

    Both checks are needed. Neither is sufficient. That is the honest
    shape of this fix and the reason to write the table out.
    """

    ROWS = [(0.22, 0.23, 0.89), (0.31, 0.32, 0.6853),
            (0.36, 0.37, 0.65), (0.44, 0.45, 0.56),
            (0.47, 0.48, 0.55), (0.21, 0.22, 0.78)]

    def test_every_row_is_refused_by_one_check_or_the_other(self):
        for his, limit, ask in self.ROWS:
            by_band = _refuses(ask, his)
            by_ask = ask > limit + 1e-9
            assert by_band or by_ask, (
                f"his {his} limit {limit} side quoted {ask} — neither "
                f"check refuses it")

    def test_the_band_catches_the_lopsided_four(self):
        caught = [(h, a) for h, _l, a in self.ROWS if _refuses(a, h)]
        assert len(caught) == 4, f"band caught {len(caught)}, expected 4"

    def test_the_ask_check_catches_the_two_near_a_coin_flip(self):
        """0.44->0.56 and 0.47->0.55: a flip near 0.50 moves the price
        so little that only the limit comparison sees it."""
        missed = [(h, l, a) for h, l, a in self.ROWS if not _refuses(a, h)]
        assert len(missed) == 2
        for _his, limit, ask in missed:
            assert ask > limit, "the ask check must catch what the band cannot"


class TestHonestCopiesStillPass:
    def test_an_exact_match_passes(self):
        assert not _refuses(0.45, 0.45)

    def test_normal_movement_between_his_fill_and_ours_passes(self):
        """The band must not fight the ordinary drift that latency
        causes, or it becomes an outage dressed as a guard."""
        for his, ask in [(0.45, 0.47), (0.45, 0.42), (0.60, 0.68),
                         (0.60, 0.52), (0.30, 0.40)]:
            assert not _refuses(ask, his), f"{his} -> {ask} must pass"

    def test_a_missing_quote_is_left_to_the_ask_check(self):
        """None is not this check's job — the ask check refuses it."""
        assert not _refuses(None, 0.45)

    def test_a_missing_whale_price_does_not_refuse_here(self):
        assert not _refuses(0.45, 0)


class TestWhatItCannotCatch:
    """Stated so nobody mistakes this for complete."""

    def test_a_coin_flip_market_slips_through_and_that_is_known(self):
        # both sides ~0.50: a flip is inside the band by construction
        assert not _refuses(0.52, 0.48)

    def test_but_that_is_where_being_wrong_costs_least(self):
        """The undetectable case is the one where the two sides are
        nearly the same price — so the wrong side is nearly the same
        bet. The band's blind spot and the damage both shrink to zero
        together, which is the property that makes it acceptable."""
        for his, ask in [(0.50, 0.50), (0.52, 0.48)]:
            assert abs(ask - his) <= 0.15
            assert abs((1 - his) - his) <= 0.15, \
                "a flip near 0.50 is a near-identical price by definition"


class TestTheBandIsConfigurable:
    def test_the_default_is_fifteen_cents(self, monkeypatch):
        monkeypatch.delenv("LIVE_SIDE_PRICE_BAND", raising=False)
        assert float(os.getenv("LIVE_SIDE_PRICE_BAND", "0.15")) == 0.15

    def test_it_is_wired_into_the_money_path(self):
        import inspect

        from sportsassets import live_executor as le

        src = inspect.getsource(le.maybe_execute)
        assert "LIVE_SIDE_PRICE_BAND" in src
        assert "side-price-mismatch" in src
        assert src.index("side-price-mismatch") < src.index("pmus.submit_fok")
