from edge.fairvalue.devig import devig_multiplicative, devig_power

def test_multiplicative_sums_to_one():
    p = devig_multiplicative([1.95, 1.95])
    assert abs(sum(p) - 1) < 1e-9 and abs(p[0] - 0.5) < 1e-9

def test_power_sums_to_one():
    p = devig_power([1.50, 4.40, 8.00])
    assert abs(sum(p) - 1) < 1e-6
    assert p[0] > 0.6
