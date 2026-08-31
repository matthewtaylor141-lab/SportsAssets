"""slug_bid must price the leg we actually hold.

`exits_sold: 0` had a single mechanical cause. On this venue's tennis
family BOTH sides carry the SAME identifier, so slug_bid's sibling
fallback (`identifier != us_slug`) matched neither side and returned
None on every one of them — the exit census's `no_bid`. A second bug
sat in front of it: the venue publishes quotes as
`{"value": "0.7800", "currency": "USD"}`, and `float(dict)` raises, so
the explicit-bid loop swallowed the exception and read a market with a
published bid as having none.

The fixtures below are the five live markets from run 33395797987,
verbatim. They are what settled the quote shape:

    long.price  == bestAsk       on all five
    short.price == 1 - bestBid   on all five

so both `side.price` fields are asks, and:

    sell a LONG  leg -> bestBid
    sell a SHORT leg -> 1 - bestAsk

The direction matters asymmetrically and that is why the None-means-
refuse tests are here. On aec-wta-emmnav-loiboi the long bid is 0.95
and the short leg is worth 0.05. A short priced off bestBid sets a sell
floor nineteen times the asset's value — it simply never fills, which
is survivable. The reverse, a long priced off the short book, sells a
95c asset with a 5c floor. Neither is allowed to happen by accident.
"""
import asyncio

import pytest

from sportsassets import pmus


# The five markets exactly as run 33395797987 read them.
LIVE = {
    "aec-wta-kimbir-petmar-2026-08-30": (0.25, 0.26, 0.26, 0.75),
    "aec-atp-marlan-jacfea-2026-08-30": (0.71, 0.72, 0.72, 0.29),
    "aec-atp-sebgor-rapcol-2026-08-30": (0.20, 0.22, 0.22, 0.80),
    "aec-wta-emmnav-loiboi-2026-08-30": (0.95, 0.97, 0.97, 0.05),
    "aec-wta-liltag-tamkor-2026-08-30": (0.78, 0.79, 0.79, 0.22),
}


def _market(slug, bid, ask, long_px, short_px, shared=True):
    """The retrieve_by_slug record, in the shape it ACTUALLY has.

    It does NOT carry `bestBid`/`bestAsk`. The unmapped funnel's own
    `keys:` diagnostics list `bestBidQuote`/`bestAskQuote`, and an
    earlier version of these fixtures invented the bbo field names on
    this object — so every test passed while slug_bid read fields that
    were never there.
    """
    return {
        "slug": slug,
        "bestBidQuote": {"value": f"{bid:.4f}", "currency": "USD"},
        "bestAskQuote": {"value": f"{ask:.4f}", "currency": "USD"},
        "marketSides": [
            {"long": True, "price": f"{long_px}",
             "identifier": slug, "tradable": True},
            {"long": False, "price": f"{short_px}",
             "identifier": slug if shared else slug + "-b",
             "tradable": True},
        ],
    }


def _client(market, bbo=None):
    """`bbo` is a SEPARATE call from retrieve_by_slug — the one the
    side attribution was actually proven against."""
    class _M:
        def retrieve_by_slug(self, _s):
            return {"market": market}

        def bbo(self, _s):
            if bbo is None:
                raise RuntimeError("no bbo feed")
            return {"marketData": bbo}

    class _C:
        markets = _M()

    return _C()


def _bbo(bid, ask):
    return {"bestBid": {"value": f"{bid:.4f}", "currency": "USD"},
            "bestAsk": {"value": f"{ask:.4f}", "currency": "USD"}}


def _install(monkeypatch, market, bbo=None):
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _client(market, bbo))


# ------------------------------------------------ the shape is what it is

@pytest.mark.parametrize("slug", list(LIVE))
def test_the_recorded_shape_is_internally_consistent(slug):
    """Not a code test — a check that the fixtures still encode the
    relationship the live run measured. If this fails, the venue
    changed and every conclusion below needs re-measuring."""
    bid, ask, long_px, short_px = LIVE[slug]
    assert long_px == ask, "long.price is the ASK"
    assert abs(short_px - (1 - bid)) < 1e-9, "short.price is 1 - bestBid"


# ------------------------------------------------------------- long legs

@pytest.mark.parametrize("slug", list(LIVE))
def test_a_long_leg_prices_off_the_best_bid(monkeypatch, slug):
    bid, ask, long_px, short_px = LIVE[slug]
    _install(monkeypatch, _market(slug, bid, ask, long_px, short_px))
    assert pmus.slug_bid(slug, True) == pytest.approx(bid)


# ------------------------------------------------------------ short legs

@pytest.mark.parametrize("slug", list(LIVE))
def test_a_short_leg_prices_off_one_minus_the_best_ask(monkeypatch, slug):
    bid, ask, long_px, short_px = LIVE[slug]
    _install(monkeypatch, _market(slug, bid, ask, long_px, short_px))
    got = pmus.slug_bid(slug, False)
    assert got == pytest.approx(round(1 - ask, 4))


def test_a_short_leg_is_never_priced_off_the_long_book(monkeypatch):
    """The giveaway direction, on the widest market in the sample:
    long bid 0.95, short leg worth 0.05."""
    slug = "aec-wta-emmnav-loiboi-2026-08-30"
    bid, ask, long_px, short_px = LIVE[slug]
    _install(monkeypatch, _market(slug, bid, ask, long_px, short_px))
    got = pmus.slug_bid(slug, False)
    assert got != pytest.approx(bid)
    assert got < 0.1, (
        "a short leg priced near the LONG bid — this sets a sell floor "
        "nineteen times the asset's value")


def test_the_two_legs_never_return_the_same_price(monkeypatch):
    """A single spread means the two legs cannot both be right at one
    number; if they ever match, the leg selector is being ignored."""
    for slug, (bid, ask, lp, sp) in LIVE.items():
        _install(monkeypatch, _market(slug, bid, ask, lp, sp))
        assert pmus.slug_bid(slug, True) != pmus.slug_bid(slug, False)


# ------------------------------------------------- refusing beats guessing

@pytest.mark.parametrize("slug", list(LIVE))
def test_a_shared_identifier_market_refuses_when_the_leg_is_unknown(
        monkeypatch, slug):
    """The whole hazard. Both sides carry one identifier, so the slug
    cannot select a leg. Now that the quote parses, returning bestBid
    here would start pricing shorts off the long book on exactly the
    markets that were broken."""
    bid, ask, long_px, short_px = LIVE[slug]
    _install(monkeypatch, _market(slug, bid, ask, long_px, short_px))
    assert pmus.slug_bid(slug) is None
    assert pmus.slug_bid(slug, None) is None


def test_a_missing_ask_refuses_rather_than_falling_back_to_the_bid(
        monkeypatch):
    """Fail closed: no ask means no short price, not the long price."""
    slug = "aec-wta-liltag-tamkor-2026-08-30"
    m = _market(slug, 0.78, 0.79, 0.79, 0.22)
    m.pop("bestAskQuote")
    _install(monkeypatch, m)
    assert pmus.slug_bid(slug, False) is None


def test_an_unreadable_market_is_still_none(monkeypatch):
    class _M:
        def retrieve_by_slug(self, _s):
            raise RuntimeError("404")

    class _C:
        markets = _M()

    monkeypatch.setattr(pmus, "_get_client", lambda: _C())
    assert pmus.slug_bid("x", True) is None
    assert pmus.slug_bid("x", False) is None
    assert pmus.slug_bid("x") is None


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.2, 1.4])
def test_prices_outside_the_unit_interval_are_refused(monkeypatch, bad):
    slug = "aec-wta-liltag-tamkor-2026-08-30"
    m = _market(slug, 0.78, 0.79, 0.79, 0.22)
    m["bestBidQuote"] = {"value": str(bad)}
    _install(monkeypatch, m)
    assert pmus.slug_bid(slug, True) is None


# ------------------------------------------- the quote shape itself parses

def test_the_dict_quote_shape_is_read(monkeypatch):
    """`float({"value": "0.78"})` raises, and the old loop swallowed it
    — a published bid read as no bid at all."""
    slug = "aec-wta-liltag-tamkor-2026-08-30"
    _install(monkeypatch, _market(slug, 0.78, 0.79, 0.79, 0.22))
    assert pmus.slug_bid(slug, True) == pytest.approx(0.78)


def test_a_bare_float_quote_still_works(monkeypatch):
    """Other venues/markets publish a plain number; the fix must not
    trade one shape for the other."""
    slug = "plain-market"
    m = _market(slug, 0.61, 0.62, 0.62, 0.39)
    m["bestBidQuote"] = 0.61
    m["bestAskQuote"] = 0.62
    _install(monkeypatch, m)
    assert pmus.slug_bid(slug, True) == pytest.approx(0.61)
    assert pmus.slug_bid(slug, False) == pytest.approx(0.38)


def test_the_bbo_feed_is_preferred_over_the_market_record():
    """The BBO feed is the ONLY source whose side is known — the
    five-market attribution was measured on it. If the record and the
    feed disagree, the feed wins, because the record's side was never
    established."""
    slug = "aec-wta-liltag-tamkor-2026-08-30"

    class _M:
        def retrieve_by_slug(self, _s):
            return {"market": _market(slug, 0.10, 0.11, 0.11, 0.90)}

        def bbo(self, _s):
            return {"marketData": _bbo(0.78, 0.79)}

    class _C:
        markets = _M()

    import pytest as _pt
    orig = pmus._get_client
    pmus._get_client = lambda: _C()
    try:
        assert pmus.slug_bid(slug, True) == _pt.approx(0.78)
        assert pmus.slug_bid(slug, False) == _pt.approx(0.21)
    finally:
        pmus._get_client = orig


def test_the_market_record_is_the_fallback_when_the_feed_is_absent(
        monkeypatch):
    """bbo raising must not lose the quote entirely — but the field
    names on the record are bestBidQuote/bestAskQuote, which is what
    the original implementation never looked for."""
    slug = "aec-wta-liltag-tamkor-2026-08-30"
    _install(monkeypatch, _market(slug, 0.78, 0.79, 0.79, 0.22),
             bbo=None)
    assert pmus.slug_bid(slug, True) == pytest.approx(0.78)
    assert pmus.slug_bid(slug, False) == pytest.approx(0.21)


def test_the_record_does_not_carry_the_bbo_field_names():
    """The bug this whole block exists for. An earlier fixture invented
    `bestBid` on the record; every test passed while slug_bid read a
    field that is not on that object."""
    m = _market("s", 0.5, 0.6, 0.6, 0.5)
    assert "bestBid" not in m and "bestAsk" not in m
    assert "bestBidQuote" in m and "bestAskQuote" in m


def test_both_quotes_come_from_one_snapshot(monkeypatch):
    """Bid and ask must not be read from two separate calls — a move
    between them can invert the spread."""
    import inspect
    src = inspect.getsource(pmus._bbo_quotes)
    assert src.count("fn(us_slug)") == 1, (
        "bid and ask are fetched separately; they can straddle a move")


# ------------------------------- the distinct-identifier path is unchanged

def test_a_two_identifier_market_still_works_without_a_leg(monkeypatch):
    """Markets whose sides carry DIFFERENT identifiers were never
    broken, and the existing callers pass no leg. That path must behave
    exactly as it did."""
    slug = "distinct-market"
    m = _market(slug, 0.30, 0.31, 0.31, 0.70, shared=False)
    _install(monkeypatch, m)
    assert pmus.slug_bid(slug) == pytest.approx(0.30)


def test_the_sibling_mirror_still_answers_when_no_bid_is_published(
        monkeypatch):
    """The pre-existing fallback: a resting YES bid IS a NO ask."""
    slug = "distinct-market"
    m = _market(slug, 0.30, 0.31, 0.31, 0.70, shared=False)
    m.pop("bestBidQuote")
    _install(monkeypatch, m)
    assert pmus.slug_bid(slug) == pytest.approx(0.30)


def test_the_signature_keeps_the_leg_optional():
    """Every existing caller passes one positional argument."""
    import inspect
    sig = inspect.signature(pmus.slug_bid)
    assert sig.parameters["long_leg"].default is None


# ----------------------------------------------------------- the wiring

def test_the_exit_path_actually_supplies_the_leg():
    """The resolver being able to price a short is worth nothing if
    mirror_exit still calls it without a leg — it would refuse exactly
    as before, and every test above would still pass."""
    import inspect

    from sportsassets import live_executor as le

    src = inspect.getsource(le.mirror_exit)
    assert "pmus.slug_bid" in src
    i = src.index("pmus.slug_bid")
    call = src[i:i + 120]
    assert "_long_leg" in call, (
        "mirror_exit calls slug_bid without saying which leg it holds")
    # And the leg must come from the recorded entry intent, not a guess.
    head = src[:i]
    assert 'ORDER_INTENT_BUY_SHORT' in head
    assert 'ORDER_INTENT_BUY_LONG' in head
    assert 'row["intent"]' in head


def test_the_desk_path_READS_the_leg_and_never_assumes_long():
    """The bug this test exists for was mine, and it shipped.

    I passed `slug_bid(us_slug, True)` on the desk path arguing that a
    short reads negative through _pm_held, so a positive `held` proves
    long. _pm_held returns abs(netPosition) — the magnitude — so
    `held >= 1` is equally true of a short.

    That is the GIVEAWAY direction. With the long book at 0.05/0.06 the
    short leg is worth 0.94, and a bestBid floor sells it for 0.05."""
    import inspect

    from sportsassets import live_executor as le

    # The public entry point is a thin try/except wrapper; the
    # body lives in _execute_manual_sell.
    src = inspect.getsource(le._execute_manual_sell)
    assert "pmus.slug_bid, us_slug, True" not in src, (
        "the desk path hardcodes long — a held SHORT would be priced "
        "off the long bid")
    assert "_pm_long_leg" in src, "the desk path never reads the side"
    i, j = src.index("_pm_long_leg"), src.index("pmus.slug_bid")
    assert i < j, "the side must be read before the bid is priced"


def test_pm_held_still_returns_a_magnitude():
    """The premise of the bug above. If _pm_held ever became signed,
    the reasoning changes and these call sites need re-reading."""
    import inspect

    from sportsassets import live_executor as le

    src = inspect.getsource(le._pm_held)
    assert "abs(_amt(p.get(\"netPosition\")))" in src


def test_pm_long_leg_reads_the_sign(monkeypatch):
    from sportsassets import live_executor as le
    from sportsassets.api import pmus_account

    for net, want in ((25, True), (-25, False), (0, None)):
        monkeypatch.setattr(pmus_account, "_fetch_all_positions_sync",
                            lambda n=net: {"s": {"netPosition": n}})
        assert asyncio.run(le._pm_long_leg("s")) is want


def test_pm_long_leg_is_none_when_the_venue_is_unreadable(monkeypatch):
    """Unreadable must not read as long."""
    from sportsassets import live_executor as le
    from sportsassets.api import pmus_account

    def _boom():
        raise RuntimeError("venue 503")

    monkeypatch.setattr(pmus_account, "_fetch_all_positions_sync", _boom)
    assert asyncio.run(le._pm_long_leg("s")) is None


def test_mirror_exit_falls_back_to_the_venue_when_intent_is_missing():
    """Otherwise the fix silently reproduces `no_bid` on exactly the
    rows whose recorded history is thinnest."""
    import inspect

    from sportsassets import live_executor as le

    src = inspect.getsource(le.mirror_exit)
    i = src.index("pmus.slug_bid")
    assert "_pm_long_leg" in src[:i], (
        "mirror_exit gives up when intent is absent instead of asking "
        "the account which side it holds")


def test_the_price_and_the_order_side_come_from_one_source():
    """The limit is priced for a specific leg. submit_fok derives the
    SELL side itself, and with intent=None that is a SECOND, independent
    derivation (the venue's position sign) of the same fact. Two
    derivations can disagree, and disagreeing here means pricing one leg
    and selling the other."""
    import inspect

    from sportsassets import live_executor as le

    src = inspect.getsource(le.mirror_exit)
    i = src.index("pmus.submit_fok")
    call = src[i:i + 300]
    assert "_oi" in call, (
        "mirror_exit prices one leg and then lets submit_fok work the "
        "side out separately")
