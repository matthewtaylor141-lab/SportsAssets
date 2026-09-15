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

    def test_a_quantity_sale_still_refuses_without_a_bid(self):
        """Selling a QUANTITY needs a price, so an unreadable bid defers
        it — into an empty book at a guessed price is worse than
        holding. This now covers a full exit on a CO-HELD slug as well
        as a partial: both sell a quantity rather than flattening, and
        "cannot price our own shares" must not escalate to "flatten
        somebody else's"."""
        src = inspect.getsource(le.mirror_exit)
        assert "slug_bid" in src
        assert "exit deferred " in src
        assert "mx_no_bid_for_partial" in src

    def test_a_full_exit_no_longer_needs_a_bid_at_all(self):
        """This was the blocker that made detection worthless: every
        unreadable bid was an exit we FOUND and then declined to take,
        leaving us holding what the whale had already left.
        close-position carries no limit price, so a missing bid cannot
        block it — and it works on either sign, which matters because a
        short reads negative."""
        src = inspect.getsource(le.mirror_exit)
        assert "pmus.close_position" in src
        assert "slippage_bips=EXIT_SLIPPAGE_BIPS" in src

    def test_the_flatten_is_never_sent_unbounded(self):
        """close-position has no limit and cannot be previewed, so a
        caller that has not chosen a slippage bound has not chosen to
        trade."""
        from sportsassets import pmus

        assert pmus.close_position("aec-x", slippage_bips=0)["status"] \
            == "no_slippage_bound"
        assert le.EXIT_SLIPPAGE_BIPS > 0

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
        # Pinned as ARITHMETIC, not as a literal: the truncating form
        # dropped a 20% trim on a 4-share remainder (int(0.8) == 0), so
        # the rule is now half-up. Asserting the old source string would
        # have blocked that fix while claiming to protect the
        # proportional relationship it broke.
        assert "ours * closed_frac" in src
        for ours, frac, want in ((100, 0.25, 25), (4, 0.20, 1),
                                 (10, 0.105, 1), (4, 0.10, 0)):
            assert int(ours * frac + 0.5) == want

    def test_a_full_exit_closes_everything_to_the_share(self):
        """Rounding a 99% exit down would leave a permanent dust
        position that nothing ever revisits."""
        src = inspect.getsource(le.mirror_exit)
        assert "closed_frac >= FULL_EXIT_FRAC" in src
        assert "qty = ours" in src

    def test_a_sale_that_leaves_shares_does_not_retire_the_row(self):
        """'cashed_out' while shares remain orphans them: the settlement
        sweep targets status='filled', so they would never be graded;
        mirror_exit's own row query requires 'filled', so they could
        never be sold again; and copy_sweep's blocking list contains
        'cashed_out', so we would never re-enter either.

        The condition must be about SHARES, not about the whale's
        fraction. Gating on closed_frac covered a partial exit and
        missed a FULL exit that only partially filled — the branch that
        runs an unlimited close against a book thin enough to exhaust
        its slippage bound."""
        src = inspect.getsource(le.mirror_exit)
        assert "remaining = max(0, int(row[\"qty\"]) - int(booked))" in src
        assert "SET status='filled', " in src
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert "if remaining > 0:" in code
        assert "remaining > 0 and closed_frac" not in code, \
            "the remainder test is gated on the whale's fraction again"

    def test_a_partial_is_logged_with_what_is_left(self):
        src = inspect.getsource(le.mirror_exit)
        assert "%d left" in src

    def test_the_fraction_comes_from_his_whole_ledger(self):
        """Not from the one sell row — a scale-out across several fills
        must read as one position, or the first partial looks total."""
        src = inspect.getsource(le.mirror_exit)
        assert "FILTER (WHERE t.side='BUY')" in src
        assert "FILTER (WHERE t.side='SELL')" in src
