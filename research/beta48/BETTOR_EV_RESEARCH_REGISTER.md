# BETTOR EV RESEARCH REGISTER

Every external method this programme draws on, and what happened when it met
BETTOR's own data. A published result is a HYPOTHESIS here until it validates on
our venue. Nothing is adopted because it is famous.

`VALIDATED_ON_BETTOR_DATA` is `YES` only where a walk-forward test on our own
events has been run and reported. `NOT_YET` means the machinery exists and the
test has not been run. `BLOCKED` means a required input does not exist in the
retained corpus.

---

## 1. Gneiting & Raftery — *Strictly Proper Scoring Rules, Prediction, and Estimation*

| | |
|---|---|
| **CLAIM** | Under a strictly proper score, the forecaster's optimal report is its true belief. Accuracy and win rate are not proper and reward overconfidence. |
| **DOMAIN** | General probabilistic forecasting |
| **WHY RELEVANT** | Decides what "better model" means before any model is built. |
| **IMPLEMENTED AS** | `ev_core_calibration.log_loss`, `.brier`, `.brier_decomposition`. Accuracy is deliberately absent from the module. |
| **ASSUMPTIONS** | Binary outcomes; scores compared on the same row set. |
| **VALIDATED ON BETTOR DATA** | **YES** — used as the selection rule throughout. |
| **RESULT** | Adopted as the primary metric. Model ranking by log loss and Brier reversed the ranking that in-sample edge suggested. |
| **VERDICT** | **KEEP** |

## 2. Kull, Silva Filho & Flach — *Beta Calibration*

| | |
|---|---|
| **CLAIM** | A two/three-parameter beta family calibrates better than Platt scaling for probabilities that are not logit-normal, and unlike isotonic it does not overfit small samples. |
| **DOMAIN** | Classifier calibration |
| **WHY RELEVANT** | A market price is already near-calibrated; any correction must be gentle and low-parameter or it destroys what is there. |
| **IMPLEMENTED AS** | Logistic (Platt-on-logit) recalibration in `ev_core_models.b4_global_logit`, with shrunken per-group variants. The beta and isotonic members are **NOT YET** built. |
| **ASSUMPTIONS** | A monotone distortion of the input probability. |
| **VALIDATED ON BETTOR DATA** | **YES (Platt variant)** |
| **RESULT** | Recalibration **did not beat the raw venue price** out of sample: mean log loss 0.5221 vs **0.5216**, worse in 4 of 5 folds. On the predeclared soccer totals + exact-score subset it won 4 of 5 folds by +0.001014, 95% event-clustered CI **[-0.002744, +0.004928]** — includes zero, before any correction for 16 subgroup looks. |
| **VERDICT** | **CHALLENGER** — kept in the zoo, not promoted. Isotonic explicitly withheld: with 88 test events per fold it would fit noise, exactly as the paper warns. |

## 3. Štrumbelj — *On determining probability forecasts from betting odds*

| | |
|---|---|
| **CLAIM** | Shin's method extracts better probabilities from bookmaker odds than basic normalisation, particularly at longshot prices. |
| **DOMAIN** | Sports betting odds → probabilities |
| **WHY RELEVANT** | Would supply `P_EXTERNAL_CONSENSUS`, the independent expert the ensemble most needs. |
| **IMPLEMENTED AS** | Nothing. |
| **ASSUMPTIONS** | Requires bookmaker prices across multiple books. |
| **VALIDATED ON BETTOR DATA** | **BLOCKED** |
| **RESULT** | `DATA_STATUS = NOT_AVAILABLE`. The retained corpus contains no bookmaker or exchange odds — only this venue's own prices. De-vig has nothing to operate on. |
| **VERDICT** | **CHALLENGER, BLOCKED ON DATA.** This is the single highest-value missing input. |

## 4. Ranjan & Gneiting — *Combining Probability Forecasts*

| | |
|---|---|
| **CLAIM** | A linear pool of calibrated forecasts is under-confident; beta-transformed linear pooling restores sharpness. |
| **DOMAIN** | Forecast combination |
| **WHY RELEVANT** | Governs how `P_VENUE`, `P_FUNDAMENTAL` and `P_CONSENSUS` should be merged — and warns against the naive average. |
| **IMPLEMENTED AS** | Nothing yet. |
| **ASSUMPTIONS** | Two or more genuinely distinct expert forecasts. |
| **VALIDATED ON BETTOR DATA** | **BLOCKED** |
| **RESULT** | **There is only one expert.** With `P_VENUE` alone there is nothing to pool. Building an ensemble now would be pooling a forecast with transformations of itself, which cannot add information. |
| **VERDICT** | **CHALLENGER, BLOCKED ON A SECOND EXPERT.** |

## 5. Dixon & Coles — *Modelling Association Football Scores*

| | |
|---|---|
| **CLAIM** | Independent Poisson scoring under-states low-score draws; a dependence correction on 0-0/1-0/0-1/1-1 plus time-decayed team strengths fits football better. |
| **DOMAIN** | Association football |
| **WHY RELEVANT** | The distribution-first model §1 demands: one latent score distribution generating moneyline, draw, totals, BTTS and exact scores coherently. |
| **IMPLEMENTED AS** | `ev_core_event_model` — Poisson and Dixon-Coles-corrected bivariate families with a shared score grid and contract derivation. |
| **ASSUMPTIONS** | Needs per-team attack/defence strengths, which need many observed scorelines per team. |
| **VALIDATED ON BETTOR DATA** | **NOT_YET** |
| **RESULT** | **Sample too small to fit a defensible champion.** Outcome reconstruction recovered an exact score for **156 of 1,130** soccer fixtures and a total for 230. Across 38 days most clubs appear two to four times. Fitting team strengths on that would be fitting noise. |
| **VERDICT** | **CHALLENGER, BLOCKED ON SAMPLE.** Machinery built and coherence-tested; not promoted. |

## 6. Stoikov — *The Micro-Price*

| | |
|---|---|
| **CLAIM** | A depth-weighted adjustment of the mid predicts short-horizon price better than the mid itself. |
| **DOMAIN** | Equity limit-order books |
| **WHY RELEVANT** | `EXECUTION_FAIR_VALUE_H` (§12) is a short-horizon prediction, not a settlement probability. |
| **IMPLEMENTED AS** | Nothing yet; the sealed tick series carries the necessary ladder fields. |
| **VALIDATED ON BETTOR DATA** | **NOT_YET** |
| **RESULT** | The sealed tick evidence has 1,365 observations over 6 markets in 72 minutes, and the **best bid did not move once**. There is almost no short-horizon price variation to predict in that sample. |
| **VERDICT** | **CHALLENGER.** Re-test after the substantive capture lands. |

## 7. Cont, Kukanov & Stoikov — *The Price Impact of Order Book Events*

| | |
|---|---|
| **CLAIM** | Price changes are driven by order-flow imbalance roughly linearly, more robustly than by trade volume. |
| **DOMAIN** | Equity limit-order books |
| **WHY RELEVANT** | Would supply the core microstructure feature for execution fair value and fill hazard. |
| **IMPLEMENTED AS** | Nothing yet. `opportunity_arrival` already extracts the necessary book-transition primitives. |
| **VALIDATED ON BETTOR DATA** | **NOT_YET** |
| **RESULT** | Untested. An equity result is a hypothesis on a binary event venue with a frozen touch. |
| **VERDICT** | **CHALLENGER** |

## 8. Avellaneda & Stoikov — *High-frequency trading in a limit order book*

| | |
|---|---|
| **CLAIM** | An optimal market maker quotes around a reservation price skewed by inventory and time to horizon, with spread set by risk aversion and order arrival intensity. |
| **DOMAIN** | Equity/continuous markets |
| **WHY RELEVANT** | §18's inventory-aware quoting layer. |
| **IMPLEMENTED AS** | Nothing yet. |
| **ASSUMPTIONS** | Diffusive mid-price, exponential fill intensity, terminal liquidation. **A binary event contract violates the first and third**: it settles at 0 or 1 at a known time, and its variance collapses as information arrives rather than growing with horizon. |
| **VALIDATED ON BETTOR DATA** | **NOT_YET** |
| **RESULT** | Recorded as a structural caveat: the model's parameters must be re-estimated for binaries, and its diffusion assumption is known-wrong here rather than merely unverified. |
| **VERDICT** | **CHALLENGER, WITH A NAMED ASSUMPTION VIOLATION.** |

## 9. Bartlett & O'Hara — *Adverse Selection in Prediction Markets*

| | |
|---|---|
| **CLAIM** | Resting liquidity on prediction markets is adversely selected; passive fills arrive disproportionately when the quote is stale. |
| **DOMAIN** | Prediction markets (Kalshi) |
| **WHY RELEVANT** | Decides whether `VALUE_CONDITIONAL_ON_FILL` differs materially from unconditional fair value — §15. |
| **IMPLEMENTED AS** | Nothing yet. |
| **VALIDATED ON BETTOR DATA** | **BLOCKED** |
| **RESULT** | `DATA_STATUS = NOT_AVAILABLE`. BETTOR has never rested an order, so there are no BETTOR fills to condition on. The whale corpus is **BUY-only taker flow** and carries no passive-fill contrast. |
| **VERDICT** | **CHALLENGER, BLOCKED ON BETTOR-NATIVE FILLS.** Recorded with the double-count rule from §15: if adverse selection is captured in conditional fill value, it is not subtracted again. |

## 10. Kovalchik & Reid; Asif & McHale — *in-play win probability*

| | |
|---|---|
| **CLAIM** | Point-by-point / state-based in-play models beat pre-match models once the match state is observable. |
| **DOMAIN** | Tennis; football in-play |
| **WHY RELEVANT** | §2 forbids a generic final-score classifier where point/game/set state exists. |
| **IMPLEMENTED AS** | Nothing. |
| **VALIDATED ON BETTOR DATA** | **BLOCKED** |
| **RESULT** | The retained corpus carries **no live match state** — no score, clock, server, or possession. Tennis is 40,821 observations and 2,291 events, and not one carries a game state. |
| **VERDICT** | **BLOCKED ON DATA.** Notable because tennis would otherwise be a strong first target on volume alone. |

---

## What this register says about the programme

Of the ten methods, **one is validated** (proper scoring, as the selection rule),
**one was tested and failed to beat the market** (Platt recalibration), and
**eight are blocked or untested** — six of those blocked on data the retained
corpus does not contain rather than on effort.

That is the honest state. The binding constraint on BETTOR's fair value is not
modelling sophistication. It is that we hold exactly **one** probability source —
the venue's own price — and no independent expert to combine it with.
