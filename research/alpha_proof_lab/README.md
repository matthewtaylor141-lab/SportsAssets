# BETTOR_ALPHA_PROOF_LAB_V1

Independent, read-only profitability research package for BETTOR.

## Purpose

This package is intentionally **not** a production trading engine and has no
order, cancel, credential, risk-limit, or capital-authority code.

Its job is to answer a narrower question:

> Does a sports-market strategy have defensible positive executable expectancy
> after calibration, fees, spread, slippage, adverse selection, management cost,
> correlation/capacity charges, and selection bias?

The lab is designed to falsify weak strategies before they can become capital
eligible.

## Core design

### 1. Market prior, not winner prediction in isolation

For event contracts, the model starts from a no-vig market probability and
learns only a residual correction:

    logit(p_bettor) = logit(p_market) + alpha * residual_model

This shrinks BETTOR toward the market unless out-of-sample evidence proves that
the model deserves to move away from it.

### 2. Calibration-first

Every candidate prediction is evaluated with:
- Brier score
- log loss
- reliability / calibration error
- optional isotonic calibration
- beta calibration
- sport x market-family x regime segmentation

If a segment is too small, it must fall back to a broader calibration group or
be marked UNMEASURED. The code never converts missing evidence to confidence.

### 3. Executable expected value

For a binary YES contract that pays $1:

    gross_edge = p_calibrated - expected_fill_price

    net_ev_per_contract =
        p_calibrated
        - expected_fill_price
        - fees
        - slippage
        - adverse_selection
        - management_cost
        - correlation_charge
        + earned_rebate

The maker path also exposes fill probability explicitly:

    expected_maker_profit =
        p_fill * (
            p_calibrated
            - expected_fill_price_if_filled
            - all_costs_if_filled
        )

Negative or zero net EV => CASH_WAIT / SHADOW.

### 4. Capacity is a ceiling, not a turnover target

The allocator never attempts to hit a fixed daily turnover quota. It sizes only
positive conservative opportunities and otherwise leaves capital in CASH.

### 5. Verification before promotion

Research candidates are evaluated through:
- chronological walk-forward folds
- untouched holdout support
- CLV diagnostics
- CSCV-style Probability of Backtest Overfitting (PBO)
- Deflated Sharpe Ratio-style selection-bias adjustment
- expected-vs-realized residual attribution
- immutable model cards with source/data hashes

## Package layout

- `bettor_alpha_lab/market.py`
  - de-vig binary probabilities
  - implied probabilities
  - logit/sigmoid helpers

- `bettor_alpha_lab/models.py`
  - dynamic Elo team-strength model
  - market-residual logistic model

- `bettor_alpha_lab/calibration.py`
  - isotonic calibration
  - beta calibration
  - calibration metrics

- `bettor_alpha_lab/execution.py`
  - taker and maker executable EV
  - fee/slippage/adverse-selection accounting
  - maker fill economics

- `bettor_alpha_lab/arbitrage.py`
  - exact cross-venue pair arbitrage
  - YES/NO complement checks
  - mutually-exclusive basket checks

- `bettor_alpha_lab/allocation.py`
  - conservative probability haircut
  - fractional Kelly
  - correlation/capacity-aware sizing
  - CASH fallback

- `bettor_alpha_lab/validation.py`
  - chronological walk-forward splits
  - Brier/log-loss/ROI metrics
  - CLV
  - PBO approximation
  - Deflated Sharpe Ratio approximation
  - model acceptance gate

- `bettor_alpha_lab/model_card.py`
  - signed-hash model card generation

- `bettor_alpha_lab/pipeline.py`
  - end-to-end research evaluator

- `schemas/`
  - normalized decision and result schemas

- `examples/`
  - synthetic example dataset + runnable demo

- `tests/`
  - named regressions for economics, calibration, sizing, arbitrage, and
    chronological validation

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest -q
```

## Run the demo

```bash
python examples/run_demo.py
```

The demo uses synthetic data only. It is not a profitability claim.

## Promotion policy

A strategy may not be described as `ACTIVE_CHAMPION` merely because it is the
least negative strategy.

Recommended status semantics:

- `RESEARCH_ONLY`
  - incomplete or non-forward evidence

- `SHADOW_ONLY`
  - research evidence exists but capital eligibility not proven

- `ACTIVE_CHALLENGER`
  - positive forward evidence but still probational / capacity limited

- `ACTIVE_CHAMPION`
  - positive forward **net** $/capital-hour after all costs
  - positive conservative lower bound
  - calibrated
  - acceptable PBO / selection-bias diagnostics
  - no material expected-vs-realized residual failure
  - adequate executable capacity

- `CASH`
  - no eligible positive recipient

## Critical integration rule

Claude should integrate this package as a **shadow/research lane first**.

Do not:
- modify historical PAPER P&L
- turn SMALL LIVE on
- weaken any existing freshness / settlement / risk / profitability gate
- auto-promote a model
- treat positive backtest ROI as proof of future profitability

Only signed forward evidence should change capital eligibility.
