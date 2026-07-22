from edge.fairvalue.devig import devig_multiplicative, devig_power

def test_multiplicative_sums_to_one():
    p = devig_multiplicative([1.95, 1.95])
    assert abs(sum(p) - 1) < 1e-9 and abs(p[0] - 0.5) < 1e-9

def test_power_sums_to_one():
    p = devig_power([1.50, 4.40, 8.00])
    assert abs(sum(p) - 1) < 1e-6
    assert p[0] > 0.6


def test_power_heavy_vig_falls_back_gracefully():
    # Degenerate near-certain two-way (q sum ~1.96): no valid power bracket —
    # must return valid probabilities, never crash the cycle.
    p = devig_power([1.02, 1.02])
    assert abs(sum(p) - 1) < 1e-6
    assert all(0 < x < 1 for x in p)


def test_power_underround():
    # Sum of implied < 1 (exchange-style underround) — root is below 1.
    p = devig_power([2.2, 2.2])
    assert abs(sum(p) - 1) < 1e-6
    assert abs(p[0] - 0.5) < 1e-6


def test_power_extreme_field_widened_bracket():
    p = devig_power([1.05, 15.0, 30.0, 80.0])
    assert abs(sum(p) - 1) < 1e-6
