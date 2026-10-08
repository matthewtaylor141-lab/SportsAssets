# BETTOR_EV_PROBABILITY_ENGINE_UPGRADE_V1

Research/PAPER-SHADOW-only package to attack the actual problem identified by the Profitability Stack: outcome variance and calibration failure, not execution residual.

## Goal

Build an event-clustered probability engine that refuses to deploy capital unless BETTOR's calibrated probabilities beat the market prior out of sample and produce positive all-in executable EV at the independent event level.

## Core principles

1. **Market prior first.** Every model is benchmarked against the sharp/no-vig market prior. BETTOR only earns the right to move away from the market when out-of-sample evidence proves the residual.
2. **Calibration before confidence.** Brier, log-loss and ECE determine quality. High confidence does not matter if calibration is poor.
3. **Event-clustered validation.** Ten evaluations of one game are one event for evidence purposes.
4. **Hierarchical calibration.** Fallback from sport/family/regime to broader segments when sample size is insufficient. No fake subgroup confidence.
5. **Kill weak segments.** Any sport/family/regime where the market prior beats BETTOR is marked fallback/KILL until repaired.
6. **Positive lower bound or CASH.** Mean EV is not enough. The event-clustered lower confidence bound must be positive.

## What Claude should integrate first

Create a separate research branch, import this unchanged, run tests, and connect real data into `run_event_clustered_probability_receipt` with columns:

- `event_id`, `event_time`, `sport`, `family`, `regime`
- `market_prior_p` (sharp no-vig / Pinnacle / closing where defensible)
- `bettor_raw_p`
- `outcome`
- `entry_price`
- optional all-in cost column

Required receipt:

- market prior vs BETTOR raw
- market prior vs BETTOR calibrated
- Brier/log-loss/ECE by segment
- event-clustered net EV lower bound
- loss contribution by sport/family/regime
- KILL/fallback list
- untouched holdout receipt

## Production rule

This package never activates live trading. If calibrated BETTOR does not beat the market prior with positive event-clustered lower-bound EV, the verdict is CASH.
