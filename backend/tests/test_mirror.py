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
    r = 50.0 / 861.8
    t = mi.target_shares(r, 24423.06, 0.4574)
    # 1,416 raw shares -> capped at $250 / 0.4574 = 546 shares
    assert t["capped"] is True and t["target"] == int(250.0 / 0.4574)
    t2 = mi.target_shares(r, 2780.0, 0.31)
    assert t2["target"] == int(r * 2780.0) and t2["capped"] is False
    # negative net: refused unless shorts are admitted; when admitted the
    # cap prices the short at 1 - mark
    assert mi.target_shares(r, -24423.06, 0.4574)["target"] == 0
    s = mi.target_shares(r, -24423.06, 0.4574, allow_short=True)
    assert s["target"] == -int(250.0 / (1 - 0.4574)) and s["capped"] is True
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


def test_plan_dead_bands():
    bk = mi.Book(bid=0.50, ask=0.52)
    assert mi.plan(100, 100, 100, bk, 0.5, 0.5).reason == "on target"
    # 4 shares at 0.50 is $2: under the dollar band
    assert mi.plan(104, 100, 100, bk, 0.5, 0.5).reason == "under the dollar dead band"
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
    assert s < 0 and abs(s) <= int(250.0 / (1 - 0.545))


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


def test_venue_and_ledger_agree_within_one_share():
    from sportsassets.analytics import mirror as mi
    book = mi.Book(bid=0.30, ask=0.32)
    # 322.51 shares by the ledger against -323 at the venue is a rounding edge, not a freeze
    assert not mi.plan(0, -322.51, -323.0, book, 0.3, 0.31).reason.startswith("frozen")
    assert not mi.plan(400, 322.51, 323.0, book, 0.3, 0.31).reason.startswith("frozen")
    # more than a share apart is a real disagreement
    assert mi.plan(400, 322.0, 324.0, book, 0.3, 0.31).reason.startswith("frozen")
    assert mi.VENUE_LEDGER_TOL_SHARES == 1.0
