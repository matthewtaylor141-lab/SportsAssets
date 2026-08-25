"""The overspend assumed a cost formula nobody checked.

Every "BUY_SHORT is 6-for-6 wrong" conclusion rests on one unexamined
step: that the cash cost of a filled order is fill_price x qty. That
holds for a long. The venue's own SDK says this is not a long/short
pair of tokens at all:

    polymarket_us/types/orders.py
      CreateOrderParams: marketSlug + intent, and NO token or asset id
        — you cannot name a "short token" because there is one market
        and one price ladder.
      Order: carries BOTH `side` (ORDER_SIDE_BUY/SELL) and `intent`.
        `side` is not an input. The venue DERIVES it from the intent.

That is a futures-style contract: going short is selling the contract,
`price` denominates the contract, and a short ties up (1 - price) x qty.

Tested against the six real rows, the short model puts every one of
them AT OR UNDER the authorised amount — worst overage $0.00 across all
six, one landing exactly on $249.92. The long model has them at 1.15x
to 3.87x.

That fit is why this file is careful rather than triumphant. A model
that fits perfectly is the most persuasive kind of wrong, and several
confident readings on this system in the last day have been exactly
that. So the endpoint reports the venue's own `order.side` — which we
have stored on every row since the beginning and never read — and it
refuses to emit a verdict when that field is absent.
"""

import inspect

from sportsassets.api import app as app_mod

# (slug, requested_usd, qty, limit, fill_px) — 2026-08-24, 0x076daa87
ROWS = [
    ("harwen-stetra", 249.78, 1086, 0.23, 0.89),
    ("danalt-fracom", 249.92, 1136, 0.22, 0.78),
    ("colwon-elmmoe", 249.92, 781, 0.32, 0.6853),
    ("domsal-akaurh", 249.75, 675, 0.37, 0.65),
    ("jancho-meerot", 249.75, 555, 0.45, 0.56),
    ("ekaovc-kaique", 249.60, 520, 0.48, 0.55),
]


class TestTheTwoCostModels:
    def test_short_model_is_within_authorization_on_every_row(self):
        for slug, req, qty, _lim, f in ROWS:
            cost = (1.0 - f) * qty
            assert cost <= req + 0.01, (
                f"{slug}: short-model cost {cost:.2f} exceeds the "
                f"authorised {req:.2f}")

    def test_long_model_breaches_authorization_on_every_row(self):
        """The same six rows, the other formula. This is what produced
        'over=6 clean=0'."""
        for slug, req, qty, _lim, f in ROWS:
            assert f * qty > req + 0.01, slug

    def test_one_row_lands_on_the_authorised_figure_exactly(self):
        """249.92 requested, 1136 shares, filled 0.78:
        (1 - 0.78) * 1136 = 249.92. To the cent."""
        _slug, req, qty, _lim, f = ROWS[1]
        assert abs((1.0 - f) * qty - req) < 0.005

    def test_the_worst_overage_across_all_six_is_zero(self):
        worst = max((1.0 - f) * qty - req for _s, req, qty, _l, f in ROWS)
        assert worst <= 0.01, f"worst short-model overage {worst:.2f}"

    def test_the_legs_sum_to_one_plus_spread(self):
        """limit + fill_px ~ 1 is the signature of two legs of ONE book,
        which is what makes the short model structurally plausible
        rather than merely arithmetically convenient."""
        sums = sorted(lim + f for _s, _r, _q, lim, f in ROWS)
        assert sums[0] >= 0.999
        assert sums[-2] <= 1.031, "five of six sit within 3 cents of 1.00"


class TestTheSDKShapeIsWhatMakesThisPlausible:
    def test_a_create_order_names_no_token(self):
        """If you could name a short TOKEN, the long model would be the
        natural reading. You cannot — there is only marketSlug."""
        from polymarket_us.types import orders as o

        params = getattr(o, "CreateOrderParams").__annotations__
        assert "marketSlug" in params and "intent" in params
        for token_field in ("assetId", "tokenId", "instrumentId", "side"):
            assert token_field not in params, (
                f"{token_field} is settable — the one-book reading is "
                f"wrong and this whole model needs revisiting")

    def test_side_is_returned_not_sent(self):
        from polymarket_us.types import orders as o

        assert "side" in getattr(o, "Order").__annotations__
        assert "side" not in getattr(o, "CreateOrderParams").__annotations__

    def test_the_intent_enum_has_all_four_directions(self):
        from polymarket_us.types import orders as o

        for intent in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_LONG",
                       "ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_SHORT"):
            assert intent in str(o.OrderIntent)


class TestTheEndpointRefusesToGuess:
    def test_no_venue_side_means_no_verdict(self):
        src = inspect.getsource(app_mod.api_short_truth)
        assert "VENUE SIDE ABSENT" in src
        assert "NOT proof" in src

    def test_a_mixed_result_blocks_action(self):
        src = inspect.getsource(app_mod.api_short_truth)
        assert "MIXED venue sides" in src
        assert "do not act until resolved" in src

    def test_both_outcomes_are_spelled_out_in_advance(self):
        """Naming what each answer would mean BEFORE reading it is what
        stops the reading being fitted to the hope."""
        src = inspect.getsource(app_mod.api_short_truth)
        assert "ORDER_SIDE_SELL" in src and "ORDER_SIDE_BUY" in src
        assert "the ban stands" in src

    def test_it_reads_the_three_fields_the_old_diagnostic_skipped(self):
        src = inspect.getsource(app_mod.api_short_truth)
        for field in ("side", "cashOrderQty", "avgPx"):
            assert f'o.get("{field}")' in src

    def test_it_reports_both_models_per_row_not_just_the_favoured_one(self):
        src = inspect.getsource(app_mod.api_short_truth)
        assert "cost_if_long_model" in src
        assert "cost_if_short_model" in src

    def test_it_is_admin_only(self):
        route = [r for r in app_mod.app.routes
                 if getattr(r, "path", "") == "/api/admin/short-truth"]
        assert route and route[0].dependencies
