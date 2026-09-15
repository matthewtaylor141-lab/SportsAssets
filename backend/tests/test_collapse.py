"""Burst collapsing + delivery preference filtering (spec §6)."""

from sportsassets.notifications.collapse import passes_prefs, plan_deliveries


def trade(i, whale_id=1, username="beachboy4", notional=10_000, side="BUY",
          outcome="Chiefs", event="Chiefs vs. Bills", sport="NFL"):
    return {
        "id": i, "whale_id": whale_id, "whale_username": username, "side": side,
        "outcome": outcome, "price": 0.61, "notional": notional,
        "event_title": event, "market_title": event, "sport": sport,
    }


def test_below_threshold_sends_singles():
    deliveries = plan_deliveries([trade(i) for i in range(1, 5)], {}, threshold=5)
    assert len(deliveries) == 4
    assert all(d.kind == "single" for d in deliveries)


def test_burst_collapses_to_single_summary():
    trades = [trade(i) for i in range(1, 13)]  # 12 > 5
    deliveries = plan_deliveries(trades, {}, threshold=5)
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.kind == "summary"
    assert "placed 12 trades" in d.title
    assert "Chiefs vs. Bills" in d.title
    assert sorted(d.trade_ids) == list(range(1, 13))


def test_summary_reports_net_direction():
    trades = [trade(i, notional=30_000) for i in range(1, 12)]
    trades.append(trade(12, notional=20_000, side="SELL"))
    d = plan_deliveries(trades, {}, threshold=5)[0]
    assert "net +$310K on Chiefs" in d.title


def test_history_across_ticks_triggers_collapse():
    # 3 already pushed within the window + 3 pending = 6 > 5 → collapse.
    deliveries = plan_deliveries([trade(i) for i in range(1, 4)], {1: 3}, threshold=5)
    assert len(deliveries) == 1
    assert deliveries[0].kind == "summary"


def test_collapse_is_per_whale():
    trades = [trade(i, whale_id=1) for i in range(1, 8)]  # whale 1 bursts
    trades += [trade(100 + i, whale_id=2, username="1j59y6nk") for i in range(2)]  # whale 2 quiet
    deliveries = plan_deliveries(trades, {}, threshold=5)
    kinds = sorted(d.kind for d in deliveries)
    assert kinds == ["single", "single", "summary"]


def test_prefs_min_notional():
    assert passes_prefs(trade(1, notional=50), {"min_notional": 0})
    assert not passes_prefs(trade(1, notional=50), {"min_notional": 100})


def test_prefs_mute_and_sport_filter():
    t = trade(1)
    assert not passes_prefs(t, {"muted_whales": [1]})
    assert passes_prefs(t, {"muted_whales": [2]})
    assert passes_prefs(t, {"sports": ["NFL"]})
    assert not passes_prefs(t, {"sports": ["NBA"]})
    # Unenriched trades pass sport filters (better early than filtered-late).
    assert passes_prefs(trade(2, sport="unclassified"), {"sports": ["NBA"]})
