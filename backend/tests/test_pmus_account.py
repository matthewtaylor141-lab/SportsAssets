"""Live PM-US account normalizer: venue payload shapes -> platform card."""

import pytest

from sportsassets.api.pmus_account import normalize


def test_normalize_full_account():
    balances = {"balances": [{
        "currentBalance": 412.35, "buyingPower": 400.0,
        "assetNotional": 87.6, "unsettledFunds": 5.0}]}
    positions = {
        "nba-lal-bos-lal": {
            "netPosition": "20", "cost": {"value": "9.40", "currency": "USD"},
            "cashValue": {"value": "10.60", "currency": "USD"},
            "realized": {"value": "0", "currency": "USD"},
            "marketMetadata": {"title": "Lakers vs. Celtics", "outcome": "Lakers"},
        },
        "nfl-kc-phi-kc": {
            "netPosition": "0", "expired": True,
            "cost": {"value": "0", "currency": "USD"},
            "cashValue": {"value": "0", "currency": "USD"},
            "realized": {"value": "1.06", "currency": "USD"},
            "marketMetadata": {"title": "Chiefs vs. Eagles", "outcome": "Chiefs"},
        },
    }
    acts = [
        {"type": "ACTIVITY_TYPE_TRADE", "trade": {
            "createTime": "2026-07-24T01:00:00Z", "marketSlug": "nba-lal-bos-lal",
            "qty": "20", "price": {"value": "0.47", "currency": "USD"},
            "realizedPnl": {"value": "0", "currency": "USD"}}},
        {"type": "ACTIVITY_TYPE_ACCOUNT_DEPOSIT"},  # non-trade: skipped
    ]
    out = normalize(balances, positions, acts)
    assert out["account_value"] == 499.95        # cash + open value
    assert out["cash"] == 412.35
    assert out["open_value"] == 87.6
    assert out["realized_pnl"] == 1.06
    assert out["open_count"] == 1 and out["settled_count"] == 1
    assert out["open_positions"][0]["title"] == "Lakers vs. Celtics"
    assert out["open_positions"][0]["value"] == 10.6
    assert len(out["recent_trades"]) == 1
    assert out["recent_trades"][0]["price"] == 0.47


def test_normalize_empty_account():
    out = normalize({"balances": []}, {}, [])
    assert out["account_value"] == 0.0
    assert out["open_count"] == 0 and out["settled_count"] == 0
    assert out["recent_trades"] == []


# ── system vs manual: the only performance split that means anything ────

def _pos(cost, realized=0.0, qty=0.0, expired=True, title="t"):
    return {"netPosition": str(qty), "cost": {"value": str(cost)},
            "cashValue": {"value": "0"}, "realized": {"value": str(realized)},
            "expired": expired, "marketMetadata": {"title": title}}


def _bal(cash=1008.4):
    return {"balances": [{"currentBalance": cash, "buyingPower": cash,
                          "assetNotional": 0}]}


def test_one_manual_bet_cannot_swamp_a_hundred_system_fills():
    """A single $200 manual position dwarfs every $1 ticket the engine has
    ever placed. Reporting them together answers no question at all."""
    from sportsassets.api.pmus_account import normalize

    positions = {f"sys-{i}": _pos(1.0, realized=0.10) for i in range(100)}
    positions["manual-1"] = _pos(200.0, realized=-40.0)
    out = normalize(_bal(), positions, [])

    assert out["system"]["positions"] == 100
    assert out["system"]["realized"] == pytest.approx(10.0)
    assert out["system"]["roi"] == pytest.approx(0.10)
    assert out["manual"]["positions"] == 1
    assert out["manual"]["realized"] == pytest.approx(-40.0)
    # Blended, the system's +$10 would read as a $30 loss.
    assert out["system"]["realized"] > 0 > (out["system"]["realized"]
                                            + out["manual"]["realized"])


def test_win_rate_and_roi_count_only_settled_money():
    """An open position has not paid out. Charging its cost against zero
    return reports a loss that has not happened."""
    from sportsassets.api.pmus_account import normalize

    out = normalize(_bal(), {
        "won": _pos(1.0, realized=1.10),
        "lost": _pos(1.0, realized=-1.00),
        "still-open": _pos(1.0, qty=2, expired=False),
    }, [])
    s = out["system"]
    assert (s["settled"], s["wins"], s["losses"]) == (2, 1, 1)
    assert s["win_rate"] == 0.5
    assert s["open"] == 1 and s["open_cost"] == pytest.approx(1.0)
    assert s["roi"] == pytest.approx(0.05)      # +0.10 on $2 settled, not $3


def test_no_settled_positions_reports_unknown_not_zero():
    """Zero would read as 'breaking even'. The honest answer to 'how is it
    doing' before anything resolves is that we do not know yet."""
    from sportsassets.api.pmus_account import normalize

    out = normalize(_bal(), {"open": _pos(1.0, qty=2, expired=False)}, [])
    assert out["system"]["settled"] == 0
    assert out["system"]["win_rate"] is None and out["system"]["roi"] is None


def test_settlements_come_from_resolution_activities_not_trades():
    """A buy-and-hold book realizes nothing until resolution, so a trade feed
    alone shows every position as perpetually pending."""
    from sportsassets.api.pmus_account import normalize

    acts = [{"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
             "positionResolution": {"marketSlug": "sys-1",
                                    "beforePosition": {"cost": {"value": "0.97"}},
                                    "afterPosition": {"realized": {"value": "1.03"}}}}]
    out = normalize(_bal(), {"sys-1": _pos(0.97, qty=2, expired=False)}, acts)
    s = out["system"]
    assert s["settled"] == 1 and s["wins"] == 1
    assert s["realized"] == pytest.approx(1.03)
    assert s["open"] == 0


def test_the_size_boundary_is_explicit_and_reported():
    from sportsassets.api.pmus_account import normalize

    out = normalize(_bal(), {"a": _pos(5.0, realized=1.0),
                             "b": _pos(5.01, realized=1.0)}, [],
                    system_max_cost=5.0)
    assert out["system_max_cost"] == 5.0
    assert out["system"]["positions"] == 1 and out["manual"]["positions"] == 1


def test_tennis_slug_matcher_covers_all_tours():
    from sportsassets.api.pmus_account import _is_tennis_slug
    assert _is_tennis_slug("aec-atp-jiecui-azidou-2026-08-14")
    assert _is_tennis_slug("aec-wta-kamrak-kimbir-2026-08-13")
    assert _is_tennis_slug("aec-itfwo-marvog-laivla-2026-08-14")
    assert _is_tennis_slug("aec-itfme-briboz-johlit-2026-08-14")
    assert _is_tennis_slug("tsc-atp-doubles-kaleser-gorwal-2026-08-14")
    assert not _is_tennis_slug("aec-mlb-cle-det-2026-08-12")
    assert not _is_tennis_slug("tsc-ucl-skpu-fen-2026-08-11-2pt5")
    assert not _is_tennis_slug("")


def _trade(slug, qty, price, rp=0.0, side="BUY"):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "qty": qty, "price": price,
                      "realizedPnl": rp, "side": side,
                      "marketMetadata": {"title": slug}}}


def _resolution(slug, realized, cost):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "positionResolution": {
                "marketSlug": slug,
                "afterPosition": {"realized": realized,
                                  "marketMetadata": {"title": slug}},
                "beforePosition": {"cost": cost}}}


def test_tennis_week_aggregates_resolutions_and_sells():
    from sportsassets.api.pmus_account import aggregate_tennis_week
    days = ["2026-08-12", "2026-08-13", "2026-08-14"]
    acts = [
        # resolved winner: bought 100 @ .40, resolved, venue realized +60
        _trade("aec-atp-aaa-bbb-2026-08-12", 100, 0.40),
        _resolution("aec-atp-aaa-bbb-2026-08-12", 60.0, 40.0),
        # sold-out loser: bought 50 @ .60, sold with venue rp -10
        _trade("aec-wta-ccc-ddd-2026-08-13", 50, 0.60),
        _trade("aec-wta-ccc-ddd-2026-08-13", 50, 0.40, rp=-10.0,
               side="SELL"),
        # open position: bought, not sold, not resolved
        _trade("aec-itfwo-eee-fff-2026-08-14", 10, 0.30),
        # non-tennis and out-of-week rows are ignored
        _trade("aec-mlb-cle-det-2026-08-12", 100, 0.5),
        _trade("aec-atp-old-old-2026-08-05", 100, 0.5),
    ]
    out = aggregate_tennis_week(acts, days)
    assert out["markets"] == 3
    assert out["settled"] == 2 and out["open"] == 1
    assert out["won"] == 1 and out["lost"] == 1
    assert out["realized_total"] == 50.0          # +60 - 10
    assert out["open_cost"] == 3.0                # 10 @ .30
    slugs = {r["market_slug"]: r for r in out["rows"]}
    assert slugs["aec-atp-aaa-bbb-2026-08-12"]["realized"] == 60.0
    assert slugs["aec-wta-ccc-ddd-2026-08-13"]["realized"] == -10.0
    assert slugs["aec-itfwo-eee-fff-2026-08-14"]["open"] is True


def test_tennis_week_resolution_realized_wins_over_sell_sum():
    """The resolution row's cumulative realized already includes earlier
    sells — adding sell_rp on top would double-count them."""
    from sportsassets.api.pmus_account import aggregate_tennis_week
    days = ["2026-08-14"]
    acts = [
        _trade("aec-atp-ggg-hhh-2026-08-14", 100, 0.50),
        _trade("aec-atp-ggg-hhh-2026-08-14", 40, 0.70, rp=8.0,
               side="SELL"),
        _resolution("aec-atp-ggg-hhh-2026-08-14", 38.0, 50.0),
    ]
    out = aggregate_tennis_week(acts, days)
    assert out["realized_total"] == 38.0


def test_zero_asset_notional_falls_back_to_position_marks():
    """Live defect 2026-08-28 (14:49Z and 15:48Z probes): the venue's
    balances payload served assetNotional=0 while the positions API
    still marked real open positions — the card undercounted the
    account by the whole open book. The aggregate must agree with the
    rows it lists: when assetNotional is absent/zero but open
    positions carry marks, open_value is their sum."""
    balances = {"balances": [{
        "currentBalance": 35921.74, "buyingPower": 33009.74,
        "assetNotional": 0, "unsettledFunds": 0}]}
    positions = {
        "aec-wta-luchav-naohib-2026-08-27": {
            "netPosition": "278",
            "cost": {"value": "137.60", "currency": "USD"},
            "cashValue": {"value": "191.82", "currency": "USD"},
            "realized": {"value": "0", "currency": "USD"},
            "marketMetadata": {"title": "Havlickova vs. Hibino",
                               "outcome": "Havlickova"},
        },
        "aec-cfb-bayl-aubrn-2026-09-05": {
            "netPosition": "208",
            "cost": {"value": "85.14", "currency": "USD"},
            "cashValue": {"value": "59.70", "currency": "USD"},
            "realized": {"value": "0", "currency": "USD"},
            "marketMetadata": {"title": "Bears vs. Tigers",
                               "outcome": "Bears"},
        },
    }
    out = normalize(balances, positions, [])
    assert out["open_value"] == 251.52, "sum of the rows' own marks"
    assert out["account_value"] == 36173.26
    assert out["cash"] == 35921.74

    # and a genuinely flat account still reads zero — the fallback
    # only fires when rows contradict the aggregate
    flat = normalize(balances, {}, [])
    assert flat["open_value"] == 0.0
