# BETA48 Engine B — preregistration

**Committed before any result is observed.** Definitions below are
frozen. No threshold, split, metric or gate may move after the holdout
is read.

---

## Which mechanism classes are even selectable

| class | status | why |
|---|---|---|
| **A** — dislocation vs independent market/context information | **UNAVAILABLE** | we hold no independent context: no score state, no live state, no odds feed, no second book. There is nothing to disagree *with*. |
| **B** — relative value on a KNOWN payoff relationship | **CLOSED BY EVIDENCE** | measured in the inventory: `ask(leg0)+ask(leg1)` has median 1.0100 and minimum 1.0000 across 4,101 near-simultaneous pairs, **zero** sub-parity. `DUTCH_ARBITRAGE_ON_CLOB = NOT_PRESENT`. |
| **C** — executable-price reversion / momentum / book imbalance | **PARTIAL** | timestamping is good (dual clocks) but observations are event-triggered at RN1's trades, not a regular series, and depth exists on the **ask side only**. A prospective reversion target cannot be defined without a bid series. Usable only as a *conditioning* variable, which is M2. |
| **D** — calibrated fair-value disagreement large enough to survive spread, fees and uncertainty | **SELECTED (M1)** | the reconstructed mid makes this testable for the first time. |
| **E** — cross-venue disagreement with verified equivalence | **CLOSED** | Phase X frozen at `b5b2b63`, both hosts `connect_rejected`; Phase X1 recorded a side-orientation contradiction, so equivalence is *contradicted*, not merely unverified. |

**Two mechanisms are selected, not three.** Padding to three would mean
testing something I have already shown cannot be tested on this data.

---

## M1 — FAVOURITE–LONGSHOT BIAS AT THE RECONSTRUCTED MID

```
HYPOTHESIS      = The reconstructed mid misprices binary sports outcomes in a
                  direction identifiable at entry from the mid itself:
                  longshots (low mid) settle BELOW their mid, favourites
                  (high mid) settle ABOVE it.

ECONOMIC_REASON = The favourite-longshot bias is the most replicated
                  regularity in betting markets — risk-love, skew
                  preference and limited attention make cheap tickets
                  systematically expensive. It is a claim about
                  PREFERENCES, not about RN1, so it does not depend on his
                  future behaviour. Critically, it must be tested at the
                  MID: Track P tested it at the ASK, where the same
                  deficit appears on BOTH legs and is therefore the
                  spread. Only a mid-referenced bias can be traded.

ENTRY_INFORMATION
                = ask(leg0) and ask(leg1) from two probes <= 5 s apart,
                  and nothing else.
                    mid0 = (ask0 + 1 - ask1) / 2
                  RN1's trade price, size and side are used ONLY to
                  explain why the row exists. They never enter a decision.
                  No settlement field, no post-entry price, no future
                  probe.

TARGET          = one-hot settlement payout of leg 0 from settlement_v1,
                  admitted only when resolved_at is STRICTLY AFTER both
                  probe timestamps.

EXECUTION_ASSUMPTION
                = BUY only, TAKER only, at the observed best_ask of the
                  side bought, capped at the observed top-of-book size on
                  that side. No resting orders, no maker fills, no
                  selling, no assumption that an unfilled leg can be
                  exited. (BETTOR_PASSIVE_FILL_PROBABILITY is
                  NOT_IDENTIFIED and BLOCK_4 stands.)

FEE_MODEL       = CLOB fees are NOT_IDENTIFIED, so no single number is
                  asserted. Results are reported GROSS and on a declared
                  grid of 0 / 100 / 200 bp of notional, and the gate must
                  hold at >= 100 bp.

TRAIN_PERIOD      = 2026-08-06 .. 2026-08-21   1,568 obs /  350 conditions
VALIDATION_PERIOD = 2026-08-22 .. 2026-08-31   1,418 obs /  363 conditions
FINAL_HOLDOUT     = 2026-09-01 .. 2026-09-11   4,163 obs /  870 conditions

PRIMARY_METRIC  = net ROI per dollar deployed on the FINAL HOLDOUT, with
                  a 95% confidence interval CLUSTERED BY CONDITION (each
                  condition contributes one independent observation, not
                  each probe).

MINIMUM_ECONOMIC_GATE
                = net ROI > +3.3 pp AND the clustered 95% CI lower bound
                  > 0, at a fee of >= 100 bp, on >= 200 independent
                  holdout conditions traded.
                  The 3.3 pp floor is NOT arbitrary: with 870 independent
                  holdout conditions and a per-trade return sd of ~0.5,
                  the standard error of mean ROI is ~1.7 pp, so a 95%
                  interval is ~+/-3.3 pp. An edge smaller than that
                  CANNOT be distinguished from zero in this sample, and
                  claiming one would be claiming precision the data does
                  not have.

CAPACITY_METRIC = min(top-of-book notional) on the bought side at entry,
                  summed and per-trade median; reported, never assumed
                  scalable.

KILL_CONDITION  = ANY of: clustered holdout CI lower bound <= 0; net ROI
                  <= +3.3 pp at 100 bp; fewer than 200 independent
                  holdout conditions traded; or the train-period
                  calibration deficit at MID being within +/-1 cent of
                  zero (i.e. the bias is the spread, not a mispricing).
```

## M2 — TOUCH QUALITY (is the touch real supply?)

```
HYPOTHESIS      = When the best ask rests on a THIN top-of-book, the touch
                  is not representative supply, the mid built from it is
                  mismeasured, and calibration at that mid is worse than
                  when the touch is deep.

ECONOMIC_REASON = A microstructure claim, not a pattern: a small resting
                  order at the touch is frequently stale, a probe, or a
                  residual, while real supply sits behind it. A mid
                  anchored on a non-representative touch is a mismeasured
                  fair value, so any edge computed from it is partly
                  measurement error. This predicts WHERE M1's signal
                  should be unreliable, which is a falsifiable direction,
                  not a free parameter.

ENTRY_INFORMATION
                = the same mid, plus ONE pre-declared split on
                  min(top-of-book shares across the two legs) at the
                  TRAIN-period 25th percentile. That percentile is
                  computed on TRAIN ONLY and then frozen.
                  ONE split. No search over depth thresholds, ladder
                  slopes, level counts or spread bands.

TARGET / EXECUTION_ASSUMPTION / FEE_MODEL / PERIODS / PRIMARY_METRIC
                = identical to M1.

MINIMUM_ECONOMIC_GATE
                = identical to M1, AND M2 must beat M1 on VALIDATION to
                  be carried to the holdout at all. If it does not, M1
                  alone goes to the holdout and M2 is closed.

CAPACITY_METRIC = as M1.

KILL_CONDITION  = as M1, plus: if the thin/deep split shows no
                  calibration difference on TRAIN, M2 is dead before
                  validation.
```

---

## Rules binding both

- **One holdout read.** The holdout is touched once, after train and
  validation are complete and the rule is frozen. No peeking, no
  re-fitting, no second look.
- **No threshold mining.** The only free choices are the ones written
  above, and they are written before the data is read.
- **No post-hoc rescue.** A failed candidate is killed, not re-cut by
  sport, band, horizon or subset.
- **No RN1 future behaviour as a feature**, ever. His trade is the reason
  a row exists; it is never an input.
- **No settlement leakage**: `resolved_at > max(probe_at)` is enforced at
  row admission.
- **Reference-account profitability is never BETTOR profitability.**
- A candidate may **not** pass on calibration, Brier, log-loss or
  accuracy. The gate is net expectancy at executable prices.

## Limits that will be reported with any result, pass or fail

1. `SELECTION_BIAS = SEVERE` — every row exists because RN1 traded that
   token. This is not the tradable universe, so opportunity frequency
   here is **not** an estimate of opportunity frequency on the board.
2. `IMPACT_CONTAMINATION` — the probe fires ~37 s (median) after his
   trade, so the observed ask may contain his own impact.
3. `VENUE_MISMATCH` — this is Polymarket CLOB. BETTOR trades PMUS.
4. `CURRENT_REGIME_COVERAGE = NO` — the data ends 2026-09-11.
5. `FEES_VERIFIED = NOT_IDENTIFIED` for CLOB.
6. 1,583 independent conditions total, 870 in holdout — **below** the
   ~2,500 `DATA-B` set for resolving a 2 pp deviation.

Any of 1–5 alone is enough to bar a live deployment decision on this
evidence. A PASS here earns an isolated forward shadow, never capital.

`mirror_live = false`. Read only.
