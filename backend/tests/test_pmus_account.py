"""Live PM-US account normalizer: venue payload shapes -> platform card."""

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
