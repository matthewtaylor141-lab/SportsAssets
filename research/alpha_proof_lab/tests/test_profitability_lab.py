import numpy as np
import pandas as pd

from bettor_alpha_lab.market import devig_binary_from_decimal, residual_probability
from bettor_alpha_lab.execution import CostBreakdown, binary_taker_ev, binary_maker_ev
from bettor_alpha_lab.arbitrage import cross_venue_complement_arb, mutually_exclusive_basket_arb
from bettor_alpha_lab.allocation import allocate_binary, binary_kelly_fraction
from bettor_alpha_lab.validation import (
    chronological_walkforward,
    probability_of_backtest_overfitting,
    deflated_sharpe_ratio,
    acceptance_gate,
)
from bettor_alpha_lab.calibration import BetaCalibrator, probability_metrics


def test_devig_binary_sums_to_one():
    a, b = devig_binary_from_decimal(1.90, 2.05)
    assert abs((a + b) - 1.0) < 1e-12


def test_market_residual_moves_from_market_prior():
    p = residual_probability(0.50, 0.4, alpha=0.5)
    assert p > 0.50


def test_taker_ev_rejects_fee_consumed_edge():
    costs = CostBreakdown(fees=0.01, slippage=0.005)
    r = binary_taker_ev(0.55, 0.54, costs)
    assert r.gross_edge > 0
    assert r.net_ev_per_contract < 0
    assert not r.admissible


def test_maker_ev_accounts_for_fill_probability_and_adverse_selection():
    costs = CostBreakdown(fees=0.002, adverse_selection=0.01)
    r = binary_maker_ev(0.56, 0.53, 0.25, costs)
    assert r.net_ev_per_contract > 0
    assert abs(r.expected_profit_per_posted_contract - 0.25 * r.net_ev_per_contract) < 1e-12


def test_cross_venue_complement_arb_positive_after_costs():
    r = cross_venue_complement_arb(0.45, 0.43, yes_fee=0.005, no_fee=0.005)
    assert r.executable
    assert r.guaranteed_profit > 0


def test_basket_arb_refuses_when_cost_exceeds_one():
    r = mutually_exclusive_basket_arb([0.30, 0.35, 0.36], total_fees=0.01)
    assert not r.executable


def test_negative_conservative_edge_goes_to_cash():
    a = allocate_binary(
        bankroll=100000,
        p=0.56,
        price=0.54,
        standard_error=0.03,
        fractional_kelly=0.25,
        all_in_cost_per_contract=0.005,
    )
    assert a.stake_usd == 0
    assert a.reason.startswith("CASH_WAIT")


def test_positive_edge_is_bounded_by_max_fraction():
    a = allocate_binary(
        bankroll=100000,
        p=0.70,
        price=0.50,
        standard_error=0.01,
        fractional_kelly=0.25,
        max_fraction=0.02,
    )
    assert 0 < a.stake_fraction <= 0.02
    assert a.stake_usd <= 2000


def test_walkforward_never_uses_future_in_train():
    folds = chronological_walkforward(100, train_min=40, test_size=10)
    assert folds
    for f in folds:
        assert f.train_idx.max() < f.test_idx.min()


def test_beta_calibration_outputs_probabilities():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.1, 0.9, 500)
    y = rng.binomial(1, p)
    cal = BetaCalibrator().fit(p[:400], y[:400])
    out = cal.predict(p[400:])
    assert np.all((out >= 0) & (out <= 1))
    m = probability_metrics(y[400:], out)
    assert 0 <= m["brier"] <= 1


def test_pbo_detects_strategy_selection_instability_shape():
    # four time blocks, three strategies whose winners rotate
    m = np.array([
        [2.0, 0.0, -1.0],
        [2.0, -1.0, 0.0],
        [-2.0, 2.0, 0.0],
        [-2.0, 0.0, 2.0],
    ])
    pbo = probability_of_backtest_overfitting(m)
    assert 0 <= pbo <= 1


def test_dsr_probability_is_valid():
    p = deflated_sharpe_ratio(0.5, n_returns=500, n_trials=20)
    assert 0 <= p <= 1


def test_acceptance_requires_positive_conservative_profit():
    g = acceptance_gate(
        mean_net_profit=0.01,
        conservative_net_profit=-0.001,
        calibration_error=0.02,
        pbo=0.2,
        dsr_probability=0.98,
    )
    assert not g["eligible"]
    assert "CONSERVATIVE_NET_PROFIT_NOT_POSITIVE" in g["reasons"]
