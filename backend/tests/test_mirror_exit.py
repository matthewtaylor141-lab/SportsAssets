"""Mirroring the whale's exits — the sell half of proportional copying.

Owner order 2026-08-25: "copy both buys and sells at a proportional
rate", with the worked example: he buys $5,000, sells for $7,500, then
re-enters at $60. Copying only the entry leaves us holding to
resolution while he banks the move — a different and worse strategy
than his, graded as if it were his.

Mirroring both legs makes our net position track his automatically.
His re-entry needs no house-money special case: after we mirror the
exit, our exposure on that market is proportionally what his is.

execute_copy previously returned early on anything that was not a BUY,
so his exits were discarded at the door. They now route to
mirror_exit, which carries its own gates rather than bypassing them.

SCOPE, DELIBERATELY NARROW. V1 mirrors FULL exits (he closes 95%+).
A partial scale-out is logged and skipped: the fraction has to be
computed against a position tracked across many fills, and getting it
wrong compounds silently. His cash-out behaviour is a full exit, which
is the case carrying the value.

It is INERT today — every copied whale shows zero sells all time. It
exists now so the sell leg is already correct when the ingestion gap
closes, rather than being written in a hurry against live money.
"""

import inspect

from sportsassets import live_executor as le


class TestSellsReachTheMirror:
    def test_execute_copy_routes_sells_instead_of_dropping_them(self):
        src = inspect.getsource(le.execute_copy)
        assert "mirror_exit(payload)" in src

    def test_it_no_longer_returns_early_on_every_non_buy(self):
        """The old line was a single `side != BUY -> return`, which
        silently discarded his exits."""
        src = inspect.getsource(le.execute_copy)
        assert src.index('payload.get("side") == "SELL"') < \
            src.index('payload.get("side") != "BUY"')


class TestTheMirrorGatesItself:
    """Routing a new event type into the money path must not create a
    gap around the controls the buy path crosses."""

    def test_it_checks_the_master_switch(self):
        assert "copy_probe_enabled" in inspect.getsource(le.mirror_exit)

    def test_it_checks_the_emergency_halt(self):
        assert "copy_halted()" in inspect.getsource(le.mirror_exit)

    def test_it_checks_the_overspend_breaker(self):
        assert "overspend_halt(pool)" in inspect.getsource(le.mirror_exit)

    def test_it_only_acts_for_verified_whales(self):
        assert '_whale_set("LIVE_VERIFIED_WHALES")' in \
            inspect.getsource(le.mirror_exit)

    def test_it_ignores_anything_that_is_not_a_sell(self):
        assert 'payload.get("side") != "SELL"' in \
            inspect.getsource(le.mirror_exit)


class TestItCannotSellWhatWeDoNotHold:
    def test_the_venue_is_the_referee_for_quantity(self):
        """Our row says what we bought; the account says what can be
        sold. Selling more than held is the error that turns a copy
        into a naked short."""
        src = inspect.getsource(le.mirror_exit)
        assert "_pm_held(us_slug)" in src
        assert "min(int(row[\"qty\"]), held)" in src

    def test_no_bid_means_no_sale(self):
        """Selling into an empty book at a guessed price is worse than
        holding."""
        src = inspect.getsource(le.mirror_exit)
        assert "slug_bid" in src
        assert "leaving the position" in src

    def test_the_limit_is_protective(self):
        assert "sell_limit_price(bid)" in inspect.getsource(le.mirror_exit)


class TestPartialExitsAreNowMirroredProportionally:
    """v1 skipped anything under 95% because partial accounting is where
    errors compound. Owner order 2026-08-25: "copy buys and 'sells' in
    the correct proportional relationship."

    Skipping a partial is not neutral — it leaves us holding a position
    he has already reduced, which is the exact divergence this path
    exists to close. So we now sell HIS fraction OF OUR holding, and the
    row bookkeeping keeps the remainder alive instead of retiring the
    whole position on a partial sale."""

    def test_there_is_a_floor_but_it_is_not_a_full_exit_gate(self):
        src = inspect.getsource(le.mirror_exit)
        assert "closed_frac < MIN_EXIT_FRAC" in src
        assert le.MIN_EXIT_FRAC < 0.5, (
            "a floor that only admits near-total exits is the v1 "
            "behaviour wearing a new name")

    def test_the_floor_exists_so_dust_trims_do_not_cross_a_spread(self):
        assert 0 < le.MIN_EXIT_FRAC <= 0.25

    def test_our_quantity_is_his_fraction_of_our_position(self):
        src = inspect.getsource(le.mirror_exit)
        assert "qty = int(ours * closed_frac)" in src

    def test_a_full_exit_closes_everything_to_the_share(self):
        """Rounding a 99% exit down would leave a permanent dust
        position that nothing ever revisits."""
        src = inspect.getsource(le.mirror_exit)
        assert "closed_frac >= FULL_EXIT_FRAC" in src
        assert "qty = ours" in src

    def test_a_partial_sale_does_not_retire_the_whole_row(self):
        """'cashed_out' after selling a fraction orphans the remainder:
        the settlement sweep targets status='filled', so the shares we
        still hold would never be graded."""
        src = inspect.getsource(le.mirror_exit)
        assert "remaining = max(0, int(row[\"qty\"]) - int(filled))" in src
        assert "SET status='filled', " in src

    def test_a_partial_is_logged_with_what_is_left(self):
        src = inspect.getsource(le.mirror_exit)
        assert "%d left" in src

    def test_the_fraction_comes_from_his_whole_ledger(self):
        """Not from the one sell row — a scale-out across several fills
        must read as one position, or the first partial looks total."""
        src = inspect.getsource(le.mirror_exit)
        assert "FILTER (WHERE t.side='BUY')" in src
        assert "FILTER (WHERE t.side='SELL')" in src
