from edge.execution.engine import ExposureBook, Policy, decide

# Most tests here exercise loop and pricing MECHANICS, not trading policy.
# `blocked_categories` globally quarantines moneyline (measured -2.34c drift,
# retention 0.239 on our own fills), which would otherwise make every
# moneyline fixture untradeable and turn these into vacuous passes. The
# quarantine itself is pinned by its own tests in test_loop_health.py.
POLICY = Policy.load()
POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}


def test_dead_zone_rejected():
    d = decide(POLICY, ExposureBook(), "m1", "epl", price=0.42, fair=0.60)
    assert not d.trade and "dead" in d.reason


def test_blocked_league_rejected():
    d = decide(POLICY, ExposureBook(), "m1", "ucl", price=0.47, fair=0.60)
    assert not d.trade and "blocked" in d.reason


def test_edge_below_threshold_rejected():
    # 45-50c band needs 2.0c; offer only 1c.
    d = decide(POLICY, ExposureBook(), "m1", "epl", price=0.47, fair=0.48)
    assert not d.trade and "threshold" in d.reason


def test_qualifying_entry_sized_by_default_cap():
    d = decide(POLICY, ExposureBook(), "m1", "epl", price=0.47, fair=0.52)
    assert d.trade and d.size_usd == 5000.0


def test_kalshi_fee_moves_decision():
    fee = 0.07 * 0.47 * 0.53
    d = decide(POLICY, ExposureBook(), "m1", "epl", price=0.47, fair=0.495, venue_fee=fee)
    assert not d.trade  # edge 2.5c gross, ~0.76c net of fee — below 2.0c


def test_per_market_cap_enforced():
    ex = ExposureBook()
    ex.add("m1", 24000)
    d = decide(POLICY, ex, "m1", "epl", price=0.47, fair=0.52)
    assert d.trade and d.size_usd == 1000.0
    ex.add("m1", 1000)
    d2 = decide(POLICY, ex, "m1", "epl", price=0.47, fair=0.52)
    assert not d2.trade and "caps" in d2.reason
