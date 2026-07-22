from edge.fairvalue.poisson import handicap_cover_prob, match_probs, score_matrix, total_over_prob


def test_score_matrix_normalized():
    m = score_matrix(1.4, 1.1, rho=-0.05)
    assert abs(sum(sum(r) for r in m) - 1) < 1e-9


def test_symmetric_rates_symmetric_result():
    p = match_probs(1.2, 1.2)
    assert abs(p["home"] - p["away"]) < 1e-9
    assert 0.2 < p["draw"] < 0.35


def test_stronger_home_wins_more():
    p = match_probs(2.0, 0.8)
    assert p["home"] > 0.6 > p["away"]


def test_total_and_handicap_monotonic():
    assert total_over_prob(1.5, 1.5, 1.2) > total_over_prob(3.5, 1.5, 1.2)
    assert handicap_cover_prob(+0.5, 1.5, 1.2) > handicap_cover_prob(-1.5, 1.5, 1.2)
