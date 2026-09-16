# BETTOR MODEL VALIDATION STANDARD V2

```
OFFLINE. VENUE_CONTACT = 0  ORDERS = 0  CAPITAL = 0  mirror_live = false
```

## 1. THE RULE THAT OVERRIDES EVERY OTHER

**Never choose a model or a parameter on in-sample P&L.** A backtest is not a
promotion. This is stated first because every other rule here is a way of making
it enforceable.

## 2. EVENT-BLOCKED TEMPORAL VALIDATION

Sports contracts have long-lived, overlapping labels: a forecast made at T
resolves days later, and contracts from one event resolve together.

```
SPLIT             walk-forward in time, never random k-fold
GROUPING          all contracts from one EVENT stay in the same fold
PURGE             labels overlapping the fold boundary are dropped
EMBARGO           a gap after each training fold before scoring resumes
```

Random k-fold leaks: a model trained on one market of an event and scored on
another market of the same event has seen its own answer.

```
TRAIN              fit
CALIBRATION        fit the calibrator ONLY (never reused for selection)
VALIDATION         select among challengers
FINAL_UNTOUCHED_HOLDOUT   scored ONCE, at the end, never iterated on
```

The final holdout is touched once. A holdout scored twice is a validation set.

## 3. FIVE SEPARATED METRICS, NEVER ONE HEADLINE

```
PROBABILITY_MODEL_QUALITY      Brier + Murphy partition, log loss, calibration
                               intercept/slope with CIs, reliability curve
TRADING_EDGE_BEFORE_EXECUTION  FV vs price at decision time
EXECUTION_QUALITY              realised vs decision price, fill rates, markouts
INCENTIVE_PNL                  separate ledger (incentive_split already does this)
TOTAL_REALIZED_PNL             reported LAST
```

Profitability and prediction accuracy are different measurements, and the
relationship runs both ways: a profitable-looking model need not be accurate, so
**BETTOR must be able to reject it on weak statistical evidence**; and an
accurate model need not be profitable, so a good Brier score is **not** authority
to trade.

## 4. MULTIPLE TESTING

Any challenger promoted out of a family of variants must clear a
superior-predictive-ability style test against the incumbent, with **the full
family declared before the comparison, not after**. Investigate alongside:
Probability of Backtest Overfitting / CSCV, and deflated performance measures.

Without this, one of the challengers in this programme will look significant by
construction — there are several, across many segments.

## 5. THE EXPERIMENT REGISTRY

Every challenger is recorded whether it wins or loses, with: hypothesis, the
evidence that motivated it, the benchmark it had to beat, the split used, the
out-of-sample result, and the decision. **Failed challengers stay recorded** —
a registry that only holds winners is how the same idea gets re-tried until it
passes by chance.

## 6. THE COMPLEXITY STANDARD

For every proposed feature or model, answered before it is built:

1. What historical whale evidence motivates it?
2. What public research supports it?
3. Is that research from the same market structure?
4. What native BETTOR data is required?
5. Can we test it now?
6. What is the benchmark?
7. What would falsify it?
8. Does it improve out-of-sample economics?
9. Is the improvement worth the operational complexity?
10. **Could a simpler hierarchical/shrinkage model do the same job?**

Question 10 is the one that does the most work. BETTOR's problem is many thin
cells, not one thick one, and shrinkage answers that without a single hard
threshold.

**Deep RL and large neural systems: DEFER.** Not rejected on principle — deferred
until interpretable systems demonstrably prove insufficient. An uninterpretable
model cannot produce the EV provenance receipt that management is owed.

## 7. WHAT PROMOTION REQUIRES

```
1. an out-of-sample win against a NAMED benchmark on the right metric
2. that survives the multiple-testing control
3. on event-blocked temporal splits
4. with the final holdout scored once
5. recorded in the registry, win or lose
```

Whale support is **not** promotion evidence. Public research is **not**
promotion evidence. Both are reasons to test.
