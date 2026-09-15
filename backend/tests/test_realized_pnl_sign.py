"""Every short exit reported its P&L with the sign inverted.

`(exit - entry) * shares` is the LONG formula and it was applied to
every exit regardless of intent — in four separate places. That is the
same mistake, in the same shape, as `spent = filled * fill_price`: the
line that produced

    BYINTENT BUY_LONG   n=25  over=0  clean=25
    BYINTENT BUY_SHORT  n=6   over=6  clean=0

and cost a whole class of copy before fill_cash was written. The
venue's own receipts settled the denomination that day —

    SHORTTRUTH n=6 with_venue_side=6 sides={"ORDER_SIDE_SELL":6}
               within_auth short_model=6/6 long_model=0/6

— and fill_cash and wire_limit were converted. The exit arithmetic was
not. One decision, several encodings, some of them updated: the
failure mode this codebase produces most often.

On a short it does not merely misreport the magnitude, IT FLIPS THE
SIGN, so a winning short is published as a loss of exactly equal size.
Six shorts filled on 2026-08-24 before the branch was banned, so this
is reachable on rows held right now, and it becomes reachable on every
new short the moment LIVE_ALLOW_SHORT is turned back on.

The arithmetic, from what the receipts established — on a short the
venue's `price` field names the LONG leg:

    short entry at price e    cost per share  = 1 - e
    closed  at    price x     leg then worth  = 1 - x
    realized per share        = (1-x) - (1-e) = e - x
"""

import inspect

import pytest

from sportsassets import live_executor as le


@pytest.fixture(autouse=True)
def _short_model_armed(monkeypatch):
    """The short model is armed by the venue receipt; pin it so these
    assertions describe the shipped configuration."""
    monkeypatch.setattr(le, "short_model_confirmed", lambda: True)


SHORT = "ORDER_INTENT_BUY_SHORT"
LONG = "ORDER_INTENT_BUY_LONG"


class TestTheInversion:
    def test_a_short_that_won_was_being_published_as_a_loss(self):
        """Entered short at a long-leg price of 0.89 (cost 0.11/share),
        the long leg falls to 0.60 (short leg now worth 0.40). That is
        +0.29 a share on 100 shares."""
        assert le.realized_pnl(0.89, 0.60, 100, SHORT) == 29.0
        # what shipped before:
        assert round((0.60 - 0.89) * 100, 4) == -29.0

    def test_a_short_that_lost_was_being_published_as_a_gain(self):
        assert le.realized_pnl(0.60, 0.89, 100, SHORT) == -29.0

    def test_the_magnitude_was_always_right_only_the_sign_was_not(self):
        for e, x in ((0.89, 0.60), (0.12, 0.44), (0.5, 0.5001)):
            short = le.realized_pnl(e, x, 100, SHORT)
            long_ = le.realized_pnl(e, x, 100, LONG)
            assert short == pytest.approx(-long_)

    def test_a_long_is_untouched(self):
        assert le.realized_pnl(0.40, 0.55, 200, LONG) == 30.0
        assert le.realized_pnl(0.40, 0.55, 200, None) == 30.0


class TestItDerivesFromTheCASHNotTheQuote:
    """The check that the formula is right rather than merely negated:
    compute both legs in dollars and compare."""

    @pytest.mark.parametrize("entry,exit_px,n", [
        (0.89, 0.60, 100), (0.23, 0.77, 50), (0.05, 0.95, 1000),
    ])
    def test_short_pnl_equals_proceeds_minus_cost(self, entry, exit_px, n):
        cost = (1.0 - entry) * n           # what fill_cash charges us
        proceeds = (1.0 - exit_px) * n     # what the leg is worth now
        assert le.realized_pnl(entry, exit_px, n, SHORT) == \
            pytest.approx(round(proceeds - cost, 4))

    @pytest.mark.parametrize("entry,exit_px,n", [
        (0.40, 0.55, 200), (0.05, 0.02, 900),
    ])
    def test_long_pnl_equals_proceeds_minus_cost(self, entry, exit_px, n):
        assert le.realized_pnl(entry, exit_px, n, LONG) == \
            pytest.approx(round(exit_px * n - entry * n, 4))

    def test_it_agrees_with_fill_cash_on_what_a_short_cost(self):
        """Two functions, one denomination — if they disagree, one of
        them is the next incident."""
        assert le.fill_cash(100, 0.89, SHORT) == pytest.approx(11.0)
        # closing at the same price realizes nothing
        assert le.realized_pnl(0.89, 0.89, 100, SHORT) == 0.0


class TestItIsDisarmedWithTheRestOfTheShortModel:
    def test_an_unconfirmed_short_model_uses_the_long_formula(self,
                                                              monkeypatch):
        """The short cost model does not arm itself, and the P&L must
        not arm itself either — one switch, not two."""
        monkeypatch.setattr(le, "short_model_confirmed", lambda: False)
        assert le.realized_pnl(0.89, 0.60, 100, SHORT) == -29.0

    def test_it_routes_through_the_same_predicate(self):
        src = inspect.getsource(le.realized_pnl)
        assert "_use_short_math(intent)" in src


class TestUnknownStaysUnknown:
    def test_a_missing_entry_is_none_not_zero(self):
        assert le.realized_pnl(None, 0.5, 10, LONG) is None

    def test_a_missing_exit_is_none_not_zero(self):
        assert le.realized_pnl(0.5, None, 10, LONG) is None

    def test_zero_shares_is_none(self):
        assert le.realized_pnl(0.5, 0.6, 0, LONG) is None

    def test_unparseable_inputs_do_not_raise(self):
        assert le.realized_pnl("x", 0.5, 10, LONG) is None


class TestEveryExitSiteUsesIt:
    """Four places encoded this one decision. That is how the bug got
    in, so the test is that there is now ONE."""

    def test_mirror_exit_uses_the_helper_and_carries_the_intent(self):
        src = inspect.getsource(le.mirror_exit)
        assert "realized_pnl(" in src
        assert 'row["intent"]' in src
        assert "ORDER_INTENT_SQL" in src

    def test_no_exit_site_still_open_codes_the_long_formula(self):
        from pathlib import Path

        root = Path(le.__file__).resolve().parent
        bad = []
        for f in (root / "live_executor.py",
                  root / "workers" / "underdog.py"):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                t = line.strip()
                if t.startswith("#"):
                    continue
                if ("pnl" in t and "* filled" in t) or \
                   ("pnl" in t and '* r["qty"]' in t):
                    if "realized_pnl" not in t:
                        bad.append(f"{f.name}:{i} {t}")
        assert bad == [], f"open-coded exit P&L survives: {bad}"

    def test_the_copy_exit_sweep_selects_the_intent_it_now_uses(self):
        from sportsassets.workers import underdog as ud

        src = inspect.getsource(ud._copy_exit_sweep)
        assert "ORDER_INTENT_SQL" in src, \
            "it sells COPY-sleeve rows, which can be shorts"
        assert 'r["intent"]' in src

    def test_the_intent_expression_has_one_definition(self):
        from pathlib import Path

        root = Path(le.__file__).resolve().parent
        hits = 0
        for f in root.rglob("*.py"):
            hits += f.read_text().count(
                "raw #>> '{response,executions,0,order,intent}'")
        # live_executor's constant, plus the two admin read-only
        # endpoints that predate it — never a fourth in a worker
        assert hits <= 4, "the intent path is being copied around again"
