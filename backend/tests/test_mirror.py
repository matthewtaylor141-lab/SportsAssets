"""Position mirroring, the pure arithmetic (owner order 2026-09-02,
"go for it, let's get this working"). Every rule the shadow worker
logs is checked here without a venue or a database, against the RN1
Nakashima v Michelsen book that motivated it."""
from sportsassets.analytics import mirror as mi

M, N = "tok-mich", "tok-nak"


def _f(asset, side, size, price, ts):
    return {"asset": asset, "side": side, "size": size, "price": price, "ts": ts}


def test_net_positions_add_buys_subtract_sells_and_never_go_short():
    pos = mi.net_positions([_f(M, "BUY", 2780, 0.31, 0), _f(M, "BUY", 5092.55, 0.30, 55),
                            _f(M, "SELL", 100, 0.5, 60), _f(N, "SELL", 5, 0.5, 61),
                            {"asset": "", "side": "BUY", "size": 1, "price": 0.5, "ts": 0},
                            _f(N, "BUY", "bad", 0.5, 0)])
    assert pos[M] == 7772.55 and pos[N] == 0.0


def test_his_net_is_long_minus_other():
    assert mi.his_net(28162.53, 52585.59) == -24423.06
    assert mi.his_net(11848.75, 367.42) == 11481.33


def test_opening_burst_is_his_buys_inside_the_window_of_his_first_buy():
    fills = [_f(M, "BUY", 2780, 0.31, 1000), _f(M, "BUY", 5092.55, 0.30, 1055),
             _f(M, "BUY", 2011.95, 0.31, 1055), _f(M, "BUY", 362.32, 0.31, 1135),
             _f(N, "BUY", 918, 0.46, 3800), _f(M, "SELL", 50, 0.4, 1010)]
    # first buy at 1000 -> window to 1060: 861.80 + 1527.765 + 623.7045
    assert mi.opening_burst(fills) == round(2780 * 0.31 + 5092.55 * 0.30 + 2011.95 * 0.31, 4)
    assert mi.opening_burst([]) == 0.0
    assert mi.opening_burst([_f(M, "SELL", 5, 0.5, 0)]) == 0.0


def test_mirror_ratio_maps_his_median_burst_to_the_measuring_clip():
    out = mi.mirror_ratio([900.0] * 12)
    assert out["n"] == 12 and out["anchor_usd"] == 900.0
    assert out["ratio"] == round(50.0 / 900.0, 6) and out["clip_usd"] == 50.0
    # too few markets -> no ratio (fail closed)
    assert mi.mirror_ratio([900.0] * 9)["ratio"] is None
    # clamps: a tiny burst never maps above one-for-one
    assert mi.mirror_ratio([1.0] * 12)["ratio"] == 1.0
    # zeros and Nones are not markets
    assert mi.mirror_ratio([0, None, 900.0] * 4)["n"] == 4


def test_target_is_ratio_times_net_whole_shares_capped_at_the_mark():
    # the cap is $2,500 per event since 2026-09-06 (owner order: "a hard
    # cap of no single event having more than $2.5k on it ... should
    # never force us to decline any of the possible copies"): it
    # SCALES, and `why` is never set by it
    assert mi.MARKET_NET_CAP_USD == 2500.0
    r = 50.0 / 861.8
    t = mi.target_shares(r, 24423.06, 0.4574)
    # 1,416 raw shares ($648 at the mark): under the $2,500 cap, whole
    assert t["capped"] is False and t["target"] == int(r * 24423.06) == 1416
    # a bigger net binds: capped at $2,500 / 0.4574 = 5,465 shares
    big = mi.target_shares(1.0, 24423.06, 0.4574)
    assert big["capped"] is True and big["target"] == int(2500.0 / 0.4574) == 5465 and big["why"] is None
    # 10% of his 100,000 sh @ 0.60 ($60,000): 10,000 raw -> 4,166 = $2,500
    ten = mi.target_shares(0.10, 100000.0, 0.60)
    assert ten["capped"] is True and ten["target"] == int(2500.0 / 0.60) == 4166 and ten["why"] is None
    t2 = mi.target_shares(r, 2780.0, 0.31)
    assert t2["target"] == int(r * 2780.0) and t2["capped"] is False
    # negative net: refused unless shorts are admitted; when admitted the
    # cap prices the short at 1 - mark (collateral)
    assert mi.target_shares(r, -24423.06, 0.4574)["target"] == 0
    s = mi.target_shares(1.0, -24423.06, 0.4574, allow_short=True)
    assert s["target"] == -int(2500.0 / (1 - 0.4574)) == -4607 and s["capped"] is True
    assert mi.target_shares(None, 100, 0.5)["why"] == "no ratio"


def test_plan_fails_closed_on_an_unreadable_or_disagreeing_venue():
    bk = mi.Book(bid=0.30, ask=0.32)
    assert mi.plan(100, 0, None, bk, 0.31, 0.31).side is None
    p = mi.plan(100, 40, 45, bk, 0.31, 0.31)
    assert p.side is None and p.reason.startswith("frozen")


def test_plan_buys_the_gap_at_his_level_never_above_him():
    bk = mi.Book(bid=0.30, ask=0.32)
    p = mi.plan(147, 0, 0, bk, his_last_px=0.31, mark=0.31)
    assert (p.side, p.qty, p.price) == ("BUY_LONG", 147, 0.30)
    assert p.would_fill is False                      # ask 0.32 > 0.30
    p2 = mi.plan(147, 0, 0, mi.Book(bid=0.30, ask=0.30), 0.31, 0.31)
    assert p2.would_fill is True


def test_plan_sells_down_at_his_equivalent_price_and_flattens_through_the_dead_band():
    bk = mi.Book(bid=0.53, ask=0.55)
    # his Nakashima buy at 0.46 is a Michelsen sale at 0.54; the ask is 0.55
    p = mi.plan(66, 147, 147, bk, his_last_px=0.54, mark=0.54)
    assert (p.side, p.qty, p.price) == ("SELL_LONG", 81, 0.55)
    assert p.would_fill is False and p.reason == "reduce toward target"
    # a flatten ignores the dollar dead band; it rests at max(his level,
    # the ask), so it fills now only when the bid already sits there
    p2 = mi.plan(0, 3, 3, mi.Book(bid=0.60, ask=0.62), 0.54, 0.60)
    assert (p2.side, p2.qty, p2.price, p2.reason) == ("SELL_LONG", 3, 0.62, "flatten")
    assert p2.would_fill is False
    p3 = mi.plan(0, 3, 3, mi.Book(bid=0.62, ask=0.62), 0.54, 0.62)
    assert p3.would_fill is True


def test_plan_dead_bands(monkeypatch):
    bk = mi.Book(bid=0.50, ask=0.52)
    assert mi.plan(100, 100, 100, bk, 0.5, 0.5).reason == "on target"
    # NO DOLLAR BAND (owner order ~14:10Z 2026-09-06, "Bets under $10,
    # take the full position (exact copy)"): 4 shares at 0.50 is $2 and
    # is a plan; the only floor is one whole share
    assert mi.MIN_MOVE_USD == 0.0
    p4 = mi.plan(104, 100, 100, bk, 0.5, 0.5)
    assert (p4.side, p4.qty) == ("BUY_LONG", 4)
    # one share at $0.50 is a plan (of a 41-share target: 1 of 101 is
    # inside the 2% hysteresis, which is unchanged); half a share is not
    assert mi.plan(41, 40, 40, bk, 0.5, 0.5).side == "BUY_LONG", "one share at $0.50 is a plan"
    assert mi.plan(101, 100, 100, bk, 0.5, 0.5).reason == "inside hysteresis"
    # (a sub-share delta cannot arise: delta is int(target) - int(ledger),
    # so `under one share` is declared for the reader and never reached)
    assert mi.plan(101, 100.5, 100.5, bk, 0.5, 0.5).reason == "inside hysteresis"
    # the dollar clause is kept as the operator's handle and reads the
    # constant at call time: at $5 the same $2 move is banded
    monkeypatch.setattr(mi, "MIN_MOVE_USD", 5.0)
    assert mi.plan(104, 100, 100, bk, 0.5, 0.5).reason == "under the dollar dead band"
    monkeypatch.setattr(mi, "MIN_MOVE_USD", 0.0)
    # 1% of a 2,000-share target is inside hysteresis even at $10
    assert mi.plan(2000, 1980, 1980, bk, 0.5, 0.5).reason == "inside hysteresis"
    # no price to rest at -> named, no fill claim
    p = mi.plan(100, 0, 0, mi.Book(), None, 0.5)
    assert p.side == "BUY_LONG" and p.price is None and p.reason == "no price to rest at"


def test_the_rn1_book_under_the_mirror():
    """His final book: 52,585.59 Nakashima, 28,162.53 Michelsen; our long
    token is Michelsen. Net is -24,423 (short Michelsen = long Nakashima);
    long-only mirroring ends flat, the short phase holds the residual."""
    pos = {N: 52585.59, M: 28162.53}
    net = mi.his_net(pos[M], pos[N])
    r = mi.mirror_ratio([861.8] * 12)["ratio"]
    assert mi.target_shares(r, net, 0.545)["target"] == 0
    s = mi.target_shares(r, net, 0.545, allow_short=True)["target"]
    assert s < 0 and abs(s) <= int(2500.0 / (1 - 0.545))


def test_the_ratio_reports_the_dollar_weighted_anchor_beside_the_median():
    from sportsassets.analytics import mirror as mi
    # nine $10 markets and one $1,000 market: the median says $10, the
    # dollars say $1,000 -- half of his opening money sits in bursts >= $1,000
    bursts = [10.0] * 9 + [1000.0] + [10.0] * 2
    out = mi.mirror_ratio(bursts, clip_usd=50.0)
    assert out["anchor_usd"] == 10.0 and out["ratio"] == 1.0          # 50/10 clamps to 1.0
    assert out["anchor_usd_weighted"] == 1000.0 and out["ratio_weighted"] == 0.05
    few = mi.mirror_ratio([10.0] * 3)
    assert few["ratio"] is None and few["ratio_weighted"] is None and "why" in few


def test_the_bankroll_ratio_is_bankroll_over_his_deployed_dollars():
    """The account's buying power against the MERGEPNL denominator (the
    two figures the program reads at probe:77 and probe:1843): r is
    0.126%, reported beside the two burst anchors and sizing nowhere."""
    out = mi.mirror_ratio([900.0] * 12, deployed_usd=25_086_278.0, bankroll_usd=31_502.13)
    assert out["ratio_bankroll"] == round(31_502.13 / 25_086_278.0, 6) == 0.001256
    assert out["deployed_usd"] == 25_086_278.0 and "why_bankroll" not in out
    # the burst anchors are untouched by the new inputs
    assert out["ratio"] == round(50.0 / 900.0, 6) and out["ratio_weighted"] == out["ratio"]
    # plain B/D, no weighting: ints read as dollars too
    assert mi.mirror_ratio([900.0] * 12, deployed_usd=200, bankroll_usd=50)["ratio_bankroll"] == 0.25
    assert mi.bankroll_ratio(200, 50) == (200.0, 0.25, None)


def test_the_bankroll_ratio_clamps_like_the_burst_ratio():
    # a bankroll larger than his book never maps above one-for-one
    assert mi.mirror_ratio([900.0] * 12, deployed_usd=10.0, bankroll_usd=1e6)["ratio_bankroll"] == mi.RATIO_MAX
    # a bankroll vanishingly small against him floors at RATIO_MIN, not 0
    assert mi.mirror_ratio([900.0] * 12, deployed_usd=1e12, bankroll_usd=1.0)["ratio_bankroll"] == mi.RATIO_MIN
    assert mi.RATIO_MAX == 1.0 and mi.RATIO_MIN == 1e-4


def test_the_bankroll_ratio_is_none_and_named_on_a_zero_or_unreadable_figure():
    def read(**kw):
        out = mi.mirror_ratio([900.0] * 12, **kw)
        return out["ratio_bankroll"], out["why_bankroll"], out["deployed_usd"]

    # nothing passed: the existing caller's dict reads deployed_unreadable
    assert read() == (None, "deployed_unreadable", None)
    assert read(bankroll_usd=100.0) == (None, "deployed_unreadable", None)
    # deployed refused first, then bankroll; a zero echoes the figure that refused it
    assert read(deployed_usd=0.0, bankroll_usd=100.0) == (None, "deployed_zero", 0.0)
    assert read(deployed_usd=-5.0, bankroll_usd=100.0) == (None, "deployed_zero", -5.0)
    assert read(deployed_usd=100.0) == (None, "bankroll_unreadable", 100.0)
    assert read(deployed_usd=100.0, bankroll_usd=0) == (None, "bankroll_zero", 100.0)
    assert read(deployed_usd=100.0, bankroll_usd=-1.0) == (None, "bankroll_zero", 100.0)
    # NaN, the infinities, a bool and a string (even a numeric one) are
    # not dollar figures: refused as unreadable, never coerced
    nan, inf = float("nan"), float("inf")
    for bad in (nan, inf, -inf, True, False, "100", "100.0", "", [100.0], {"usd": 100.0}):
        assert read(deployed_usd=bad, bankroll_usd=100.0) == (None, "deployed_unreadable", None), bad
        assert read(deployed_usd=100.0, bankroll_usd=bad) == (None, "bankroll_unreadable", 100.0), bad
    # the pure helper says the same
    assert mi.bankroll_ratio(None, 100.0) == (None, None, "deployed_unreadable")
    assert mi.bankroll_ratio(100.0, nan) == (100.0, None, "bankroll_unreadable")


def test_the_bankroll_ratio_fails_closed_under_min_markets_too():
    # a readable bankroll and deployed figure on a whale with too few
    # anchored markets: all three readings are None, and why says so
    few = mi.mirror_ratio([900.0] * 9, deployed_usd=1000.0, bankroll_usd=100.0)
    assert few["ratio"] is None and few["ratio_weighted"] is None and few["ratio_bankroll"] is None
    assert few["why_bankroll"] == few["why"] == "fewer than 10 markets with an opening burst"
    assert few["deployed_usd"] == 1000.0
    # an unreadable figure keeps its own, more specific, name there
    assert mi.mirror_ratio([900.0] * 9, deployed_usd=None, bankroll_usd=100.0)["why_bankroll"] == "deployed_unreadable"
    assert mi.mirror_ratio([900.0] * 9, deployed_usd=0.0, bankroll_usd=100.0)["why_bankroll"] == "deployed_zero"


def test_mirror_ratio_keeps_every_existing_key_and_value_for_existing_callers():
    """Pinned against the values the function returned before the
    bankroll inputs existed (git show HEAD:...mirror.py, d402e1d), on
    every fixture the older tests use. The new keys sit beside them;
    the shadow's `compute_ratio` passes no keyword and reads `ratio`."""
    why = "fewer than 10 markets with an opening burst"
    head = {
        ((900.0,) * 12, 50.0): {"n": 12, "anchor_usd": 900.0, "ratio": 0.055556, "clip_usd": 50.0,
                                "anchor_usd_weighted": 900.0, "ratio_weighted": 0.055556},
        ((900.0,) * 9, 50.0): {"n": 9, "anchor_usd": None, "ratio": None, "clip_usd": 50.0,
                               "anchor_usd_weighted": None, "ratio_weighted": None, "why": why},
        ((1.0,) * 12, 50.0): {"n": 12, "anchor_usd": 1.0, "ratio": 1.0, "clip_usd": 50.0,
                              "anchor_usd_weighted": 1.0, "ratio_weighted": 1.0},
        ((0, None, 900.0) * 4, 50.0): {"n": 4, "anchor_usd": None, "ratio": None, "clip_usd": 50.0,
                                       "anchor_usd_weighted": None, "ratio_weighted": None, "why": why},
        ((861.8,) * 12, 50.0): {"n": 12, "anchor_usd": 861.8, "ratio": 0.058018, "clip_usd": 50.0,
                                "anchor_usd_weighted": 861.8, "ratio_weighted": 0.058018},
        ((10.0,) * 9 + (1000.0,) + (10.0,) * 2, 50.0): {
            "n": 12, "anchor_usd": 10.0, "ratio": 1.0, "clip_usd": 50.0,
            "anchor_usd_weighted": 1000.0, "ratio_weighted": 0.05},
    }
    for (bursts, clip), expect in head.items():
        out = mi.mirror_ratio(list(bursts), clip_usd=clip)
        got = {k: out[k] for k in expect}
        assert got == expect, (bursts, got)
        assert all(type(got[k]) is type(expect[k]) for k in expect), bursts
        assert set(out) - set(expect) == {"deployed_usd", "ratio_bankroll", "why_bankroll"}
    # the new inputs are keyword-only: the positional shape is unchanged
    import inspect
    params = inspect.signature(mi.mirror_ratio).parameters
    assert [p.name for p in params.values() if p.kind is p.KEYWORD_ONLY] == ["deployed_usd", "bankroll_usd"]
    assert params["deployed_usd"].default is None and params["bankroll_usd"].default is None
    # the module still reads no environment
    src = inspect.getsource(mi)
    assert "os.environ" not in src and "getenv" not in src


def test_venue_and_ledger_agree_within_one_share():
    from sportsassets.analytics import mirror as mi
    book = mi.Book(bid=0.30, ask=0.32)
    # 322.51 shares by the ledger against -323 at the venue is a rounding edge, not a freeze
    assert not mi.plan(0, -322.51, -323.0, book, 0.3, 0.31).reason.startswith("frozen")
    assert not mi.plan(400, 322.51, 323.0, book, 0.3, 0.31).reason.startswith("frozen")
    # more than a share apart is a real disagreement
    assert mi.plan(400, 322.0, 324.0, book, 0.3, 0.31).reason.startswith("frozen")
    assert mi.VENUE_LEDGER_TOL_SHARES == 1.0


# ---------------------------------------------------------- P2 rung S0
# The short side's arithmetic (owner order 2026-09-05, "we need to make
# sure we are mirroring shorts"): the target's sign, cap and truncation
# with allow_short=True, and the sign-blind plan on either sign of the
# ledger. No code changed here; these pin what the live lane now rides.

def test_target_shares_allow_short_sign_cap_and_truncation_table():
    # (ratio, net, mark) -> (target, capped): the cap prices a short at
    # 1 - mark, whole shares toward zero on either sign
    # the cap is $2,500 per event (2026-09-06); 1 - 0.9 is 0.1 only to
    # float noise, so the short leg's figure is int(2500 / (1 - 0.9))
    # as the code computes it, not a hand-rounded 25,000
    table = [
        ((1.0, 100000.0, 0.9), (int(2500.0 / 0.9), True)),          # +2777: the long leg at 0.90
        ((1.0, -100000.0, 0.9), (-int(2500.0 / (1 - 0.9)), True)),  # the short leg at 0.10
        ((1.0, -100000.0, 0.1), (-int(2500.0 / (1 - 0.1)), True)),  # -2777 the other way round
        ((0.5, -300.0, 0.31), (-150, False)),
        ((0.5, 300.0, 0.31), (150, False)),
        ((0.2, -24423.06, 0.4574), (-int(2500.0 / (1 - 0.4574)), True)),   # $2,650 of collateral: capped
        ((0.05, -24423.06, 0.4574), (-int(0.05 * 24423.06), False)),   # -1221: $663 on the short leg, under
        ((0.10, -100000.0, 0.30), (-int(2500.0 / 0.70), True)),        # -3571: $2,500 of collateral at 0.70
        ((0.3, -7.5, 0.5), (-2, False)),                          # -2.25 truncates toward zero
        ((0.3, 7.5, 0.5), (2, False)),
        ((1.0, 0.0, 0.5), (0, False)),
    ]
    for (ratio, net, mark), (target, capped) in table:
        t = mi.target_shares(ratio, net, mark, allow_short=True)
        assert (t["target"], t["capped"]) == (target, capped), (ratio, net, mark, t)
        assert t["why"] is None
        assert isinstance(t["target"], int)
    assert mi.target_shares(1.0, 100000.0, 0.9, allow_short=True)["target"] == 2777
    short_leg = mi.target_shares(1.0, -100000.0, 0.9, allow_short=True)["target"]
    assert short_leg in (-24999, -25000) and short_leg == -int(2500.0 / (1 - 0.9))
    # both $2,500 on their own leg
    assert 2777 * 0.9 <= 2500.0 < 2778 * 0.9 and abs(short_leg) * (1 - 0.9) <= 2500.0 < (abs(short_leg) + 1) * (1 - 0.9)
    # the door shut: the same negative raw is 0, named
    t0 = mi.target_shares(1.0, -100000.0, 0.9)
    assert t0["target"] == 0 and t0["why"] == "short side not admitted" and t0["raw"] < 0


def test_plan_from_flat_to_a_short_target_sells_the_long_leg_at_his_equivalent_or_the_ask():
    book = mi.Book(bid=0.53, ask=0.55)
    # ledger 0 toward -N: SELL_LONG N at max(his equivalent, ask) -- the
    # shadow's would_px_short; his 0.54 (= 1 - 0.46) is under the ask
    p = mi.plan(-300, 0.0, 0.0, book, 0.54, 0.54)
    assert (p.side, p.qty, p.price, p.reason) == ("SELL_LONG", 300, 0.55, "reduce toward target")
    assert p.would_fill is False                       # bid 0.53 < 0.55
    # his equivalent above the ask rests at his level
    p2 = mi.plan(-300, 0.0, 0.0, book, 0.60, 0.54)
    assert (p2.side, p2.qty, p2.price) == ("SELL_LONG", 300, 0.60)
    # adding to a short: from -100 toward -300 is a SELL of 200 more
    p3 = mi.plan(-300, -100.0, -100.0, book, 0.54, 0.54)
    assert (p3.side, p3.qty, p3.reason) == ("SELL_LONG", 200, "reduce toward target")


def test_the_dead_band_on_a_short_is_priced_at_the_legs_own_price(monkeypatch):
    """Brief C6 (review, sign lens): a move on a SHORT ledger commits
    collateral at 1 - mark a share. 12 shares of a 0.70 leg are $8.40
    -- not "under $5" because the long token trades at 0.30 -- and 40
    shares of a 0.10 leg are $4 whatever 40 x 0.90 says. A long book's
    band is what it was. THE BAND IS $0 SINCE 2026-09-06 (U12c), so the
    leg-pricing rule is pinned under an explicit $5 and then shown
    inert at the default: every move below is a plan at $0."""
    book = mi.Book(bid=0.29, ask=0.31)
    assert mi.MIN_MOVE_USD == 0.0
    assert mi.plan(312, 300.0, 300.0, book, 0.30, 0.30).side == "BUY_LONG"
    assert mi.plan(-340, -300.0, -300.0, mi.Book(bid=0.89, ask=0.91), 0.90, 0.90).side == "SELL_LONG"
    assert mi.plan(-40, 0.0, 0.0, mi.Book(bid=0.89, ask=0.91), 0.90, 0.90).side == "SELL_LONG"
    monkeypatch.setattr(mi, "MIN_MOVE_USD", 5.0)
    p = mi.plan(-312, -300.0, -300.0, book, 0.30, 0.30)
    assert (p.side, p.qty, p.price, p.reason) == ("SELL_LONG", 12, 0.31, "reduce toward target")
    # the same 12 shares on a LONG ledger at mark 0.30 are $3.60: banded
    assert mi.plan(312, 300.0, 300.0, book, 0.30, 0.30).reason == "under the dollar dead band"
    hi = mi.Book(bid=0.89, ask=0.91)
    p2 = mi.plan(-340, -300.0, -300.0, hi, 0.90, 0.90)
    assert (p2.side, p2.reason, p2.detail) == (None, "under the dollar dead band", {"delta": -40})
    assert mi.plan(340, 300.0, 300.0, hi, 0.90, 0.90).side == "BUY_LONG"
    # from flat toward a short target the leg is the short one too
    assert mi.plan(-40, 0.0, 0.0, hi, 0.90, 0.90).reason == "under the dollar dead band"
    assert mi.plan(-12, 0.0, 0.0, book, 0.30, 0.30).side == "SELL_LONG"
    # a flatten never reads the band, on either sign; no mark, no band
    assert mi.plan(0, -3.0, -3.0, book, 0.30, 0.30).reason == "flatten"
    assert mi.plan(-312, -300.0, -300.0, book, 0.30, None).side == "SELL_LONG"


def test_plan_from_a_short_ledger_to_zero_is_a_buy_long_flatten_at_his_level_or_the_bid():
    book = mi.Book(bid=0.53, ask=0.55)
    p = mi.plan(0, -300.0, -300.0, book, 0.50, 0.54)
    assert (p.side, p.qty, p.price, p.reason) == ("BUY_LONG", 300, 0.50, "flatten")
    assert p.would_fill is False                       # ask 0.55 > 0.50
    # with no level of his, the bid alone
    p2 = mi.plan(0, -300.0, -300.0, book, None, 0.54)
    assert (p2.side, p2.qty, p2.price, p2.reason) == ("BUY_LONG", 300, 0.53, "flatten")
    # a partial cover from -300 toward -100 is a BUY of 200, a reduce
    p3 = mi.plan(-100, -300.0, -300.0, book, 0.50, 0.54)
    assert (p3.side, p3.qty, p3.reason) == ("BUY_LONG", 200, "increase toward target")
    # the venue/ledger tolerance is one signed subtraction on either sign
    assert mi.plan(0, -300.0, -300.6, book, 0.50, 0.54).side == "BUY_LONG"
    assert mi.plan(0, -300.0, -302.0, book, 0.50, 0.54).reason.startswith("frozen")
    assert mi.plan(0, -300.0, 300.0, book, 0.50, 0.54).reason.startswith("frozen")
