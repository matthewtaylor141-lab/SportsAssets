# US price-band pilot study — results

**Panel:** 2026-09-02 → 2026-09-10 (9 days), Polymarket US.
**Run:** GitHub Actions `research-sql` run 34528174319, 2026-09-10T20:44:41Z,
`research/us_band_pilot.sql` sha256 `13c785b2…8ec896`, `psql exit=0`.
**Status:** A PILOT. It establishes the pipeline and the definitions. It is not the
12-month study — see `docs/us-band-study-data-audit.md` for why that one is not testable.

---

## Executive conclusion

**No entry band, and no target/stop configuration, showed positive expectancy. Not one of
the 29 configurations tested was profitable — every single cell lost money, and the
headline configuration lost 22.65% of stake per trade.**

The result is not marginal and not a parameter-tuning problem. It is decisively negative
under the *most optimistic* assumption the data permits, and the true figure is worse.

---

## 1. The panel

| obs | slugs | slugs ≥30 obs | slugs ≥100 obs | first | last | days | mean spread | median | p90 |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|
| 170,309 | 2,243 | 1,632 | 658 | 2026-09-02 | 2026-09-10 | 9 | 0.0287 | 0.0100 | 0.0500 |

Universe for everything below: the 1,632 US contracts with ≥30 two-sided quotes.

## 2. Entry-band sweep — target = ask+0.05 (resting), stop = ask−0.05 (aggressive)

One entry per contract per band, the first time the ask enters it. No pyramiding.

| band | entries | events | target 1st | stop 1st | settled | censored | target % | avg entry | net/share | net ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.10–0.15 | 701 | 701 | 196 | 466 | 19 | 20 | 29.6 | 0.1250 | −0.0411 | **−32.79%** |
| 0.15–0.20 | 750 | 750 | 195 | 532 | 12 | 11 | 26.8 | 0.1748 | −0.0550 | −31.48% |
| 0.20–0.25 | 763 | 763 | 239 | 512 | 8 | 4 | 31.8 | 0.2235 | −0.0592 | −26.47% |
| 0.25–0.30 | 788 | 788 | 244 | 540 | 3 | 1 | 31.1 | 0.2727 | −0.0633 | −23.22% |
| **0.30–0.35** | **817** | **817** | **242** | **567** | 2 | 6 | **29.9** | 0.3226 | −0.0706 | **−21.88%** |
| 0.35–0.40 | 813 | 813 | 231 | 577 | 2 | 3 | 28.6 | 0.3725 | −0.0716 | −19.22% |
| 0.40–0.45 | 823 | 823 | 221 | 590 | 7 | 5 | 27.3 | 0.4224 | −0.0799 | −18.93% |
| 0.45–0.50 | 825 | 825 | 258 | 556 | 4 | 7 | 31.7 | 0.4715 | −0.0749 | −15.88% |
| 0.50–0.55 | 836 | 836 | 226 | 605 | 0 | 5 | 27.2 | 0.5190 | −0.0900 | −17.33% |
| 0.55–0.60 | 805 | 805 | 253 | 544 | 2 | 6 | 31.7 | 0.5705 | −0.0831 | −14.57% |
| 0.60–0.65 | 775 | 775 | 258 | 511 | 4 | 2 | 33.6 | 0.6195 | −0.0776 | −12.52% |
| 0.65–0.70 | 760 | 760 | 238 | 513 | 6 | 3 | 31.7 | 0.6684 | −0.0779 | −11.66% |
| 0.70–0.75 | 751 | 751 | 261 | 483 | 3 | 4 | 35.1 | 0.7185 | −0.0815 | −11.34% |
| 0.75–0.80 | 751 | 751 | 219 | 524 | 5 | 3 | 29.5 | 0.7672 | −0.0865 | −11.28% |
| 0.80–0.85 | 709 | 709 | 234 | 458 | 13 | 4 | 33.8 | 0.8180 | −0.0746 | **−9.12%** |
| 0.85–0.90 | 718 | 718 | 254 | 433 | 20 | 11 | 37.0 | 0.8657 | −0.0810 | −9.35% |

Two things to read here.

**Every band loses, and the net loss per share is nearly constant** — between −0.041 and
−0.090, clustered around −0.07 — while the ROI ranges from −33% to −9%. The ROI improves
with the entry price only because the same fixed per-share cost is being divided by a larger
stake. That is arithmetic, not an edge at high prices.

**Target-first is ~30% everywhere, on a symmetric ±0.05 rule.** In a fair game it would be
near 50%. The asymmetry is caused by the entry, not by the market: you buy at the **ask** and
both exits are measured against the **bid**, so at the moment of entry the stop is already
(0.05 − spread) away while the target is (0.05 + spread) away. At the mean spread of 0.0287
that is roughly 2 cents versus 8 cents — the stop is four times closer before anything moves.

## 3. The headline band, absolute target/stop levels, realised break-even

Entry 0.30–0.35 (817 entries, avg entry 0.3226). `required_pct` is computed from the
**realised** average win and loss on these very trades, per the brief's section F.

| target | stop | target 1st | stop 1st | avg win | avg loss | **required** | **observed** | margin | net ROI |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.35 | 0.20 | 447 | 355 | 0.0134 | 0.1846 | 93.2% | 55.7% | −37.5 | −23.62% |
| 0.35 | 0.25 | 341 | 467 | 0.0123 | 0.1351 | 91.7% | 42.2% | −49.5 | −22.90% |
| 0.35 | 0.30 | 189 | 622 | 0.0086 | 0.0870 | 91.0% | 23.3% | −67.7 | −20.28% |
| 0.38 | 0.25 | 287 | 521 | 0.0424 | 0.1343 | 76.0% | 35.5% | −40.5 | −22.48% |
| 0.38 | 0.28 | 211 | 598 | 0.0409 | 0.1073 | 72.4% | 26.1% | −46.3 | −21.47% |
| 0.40 | 0.15 | 419 | 374 | 0.0638 | 0.2249 | 77.9% | 52.8% | −25.1 | −23.17% |
| **0.40** | **0.20** | **363** | **439** | **0.0637** | **0.1826** | **74.1%** | **45.3%** | **−28.8** | **−22.65%** |
| 0.40 | 0.25 | 256 | 552 | 0.0628 | 0.1342 | 68.1% | 31.7% | −36.4 | −22.54% |
| 0.40 | 0.30 | 116 | 695 | 0.0578 | 0.0870 | 60.1% | 14.3% | −45.8 | −20.76% |
| 0.45 | 0.20 | 312 | 486 | 0.1139 | 0.1825 | 61.6% | 39.1% | −22.5 | −21.30% |
| 0.45 | 0.25 | 210 | 596 | 0.1128 | 0.1343 | 54.4% | 26.1% | −28.3 | −21.81% |
| 0.50 | 0.20 | 265 | 532 | 0.1641 | 0.1825 | 52.7% | 33.2% | **−19.5** | −21.51% |
| 0.50 | 0.25 | 168 | 638 | 0.1625 | 0.1345 | 45.3% | 20.8% | −24.5 | −22.65% |

The margin column is observed minus required. **The best cell in the grid is still 19.5
percentage points short of break-even**, and the ROI is flat at −20% to −24% across every
combination. Widening the target raises the payoff and lowers the required rate, but the
observed rate falls faster — a clean signature of a cost that applies per trade regardless
of where the exits sit.

**The a-priori arithmetic was right.** Before seeing any data I computed that entry 0.325 →
0.40/0.20, taking both ways at the measured fee, needs ≈75.7%. The realised figure from 817
actual paths is **74.1%**. The model of the cost is sound; the strategy just cannot clear it.

**The stop does not hold at the stop.** Nominal loss from 0.3226 to 0.20 is 0.1226. Realised
avg loss is 0.1826, of which ≈0.021 is fees — so the aggressive exit actually filled around
**0.161, roughly 4 cents through the intended stop.** A protective exit at 0.20 is not an exit
at 0.20.

### Statistical strength

For the headline cell, 802 threshold-resolved trades at +0.0637 / −0.1826:

- observed target-first **45.3%**, 95% CI **[41.8%, 48.7%]** — required is 74.1%, so the
  entire interval sits ~25 points below break-even.
- net per share −0.0711, SE 0.0043, **95% CI [−0.0796, −0.0626]**, t ≈ −16.4.

The CI is nowhere near zero. **No multiple-testing correction is needed to reach this
conclusion**: correction guards against cherry-picking a winner from many tests, and here
all 29 configurations lost. There is nothing to correct.

## 4. By sport (entry 0.30–0.35 → target 0.40 / stop 0.20)

| sport | entries | events | target 1st | stop 1st | settled | censored | observed | net/share | net ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL** | 817 | 817 | 363 | 439 | 6 | 9 | 45.3% | −0.0731 | −22.65% |
| Tennis | 502 | 502 | 259 | 237 | 4 | 2 | 52.2% | −0.0514 | −15.97% |
| Soccer | 170 | 170 | 47 | 120 | 2 | 1 | 28.1% | −0.1274 | −39.40% |
| NFL | 57 | 57 | 21 | 30 | 0 | 6 | 41.2% | −0.0750 | −23.39% |
| Other-Sports | 36 | 36 | 15 | 21 | 0 | 0 | 41.7% | −0.0795 | −24.62% |
| MLB | 26 | 26 | 15 | 11 | 0 | 0 | 57.7% | −0.0460 | −13.93% |
| Non-Sports | 26 | 26 | 6 | 20 | 0 | 0 | 23.1% | −0.1515 | −46.07% |

Tennis is the least bad and still loses 16%. MLB's 57.7% is on 26 trades — far below any
sensible minimum sample, and it loses money anyway. Soccer and Non-Sports are the worst.

## 5. By market type

| family | type | entries | target 1st | stop 1st | observed | median sec to exit |
|---|---|---:|---:|---:|---:|---:|
| `aec` | moneyline / 2-way | 536 | 279 | 251 | 52.6% | 420 |
| `atc` | per-outcome (soccer 1X2) | 135 | 38 | 97 | 28.1% | 827 |
| `tsc` | totals | 113 | 36 | 69 | 34.3% | 503 |
| `astatc` | props | 18 | 6 | 12 | 33.3% | 271 |
| `asc` | spreads | 15 | 4 | 10 | 28.6% | 371 |

The brief forbids pooling market types; this is the split. `aec` contracts reach the target
first about twice as often as `atc` — moneylines move continuously, soccer 1X2 contracts are
stickier — and exits resolve in about 7 minutes, not hours.

---

## 6. Why it loses — the mechanism, in three costs

On a ~0.32 contract:

1. **Crossing the spread on entry: ~0.029 (mean), ~0.010 (median).** Paid on 100% of trades,
   immediately, before any price movement. It is also what turns a symmetric ±0.05 rule into a
   ~30/70 loss-to-win barrier problem.
2. **Taker fees both ways: ~0.021.** 0.06 × p × (1−p) on entry and on the aggressive exit.
3. **Gap through the stop: ~0.04 on losing trades.** The stop fills near 0.161, not 0.20.

Those three add to roughly the −0.07 per share the study measures on every band.

## 7. The result is an UPPER bound — the real figure is worse

Every "target" above assumes the resting sell filled the moment the bid reached it. There is
no bid-side depth in the database, so queue position cannot be reconstructed. **Our own
measured resting-sell fill rate is 30.7%.**

Illustrative sensitivity, not a measurement: if only 30.7% of targets filled and the rest ran
on to the stop, the headline cell's 363 targets become ~111 and stops rise to ~691, giving
roughly **−0.148 per share, about −46% ROI**. The truth sits between −22.65% and that, and it
is certainly not better than −22.65%.

## 8. Classification and limitations

Against the brief's section 14 scorecard, the honest classification for every configuration
tested is: **not a candidate for paper trading.** Per section 14's closing instruction —
*no statistically reliable profitable band was identified.*

What this pilot does **not** establish:

- **9 days is not a regime.** One quiet or violent week could move these numbers.
- **The panel is biased.** It covers the markets RN1 traded, not a random sample of the US
  board. Those are liquid, actively-traded contracts — if anything a *favourable* sample.
- **No out-of-sample split.** With 9 days there is nothing to hold out. Nothing was fitted,
  so nothing is overfitted, but nothing is validated either.
- **No time-series clustering adjustment.** The CIs above treat trades as independent; they
  share a 9-day window and correlated market moves, so the true intervals are wider. They are
  not 25 points wider.
- **Model 3 was never built.** No bid depth exists to build it from.

## 9. What this changes

The pilot's value is that it removes a question. Buying the ask and managing out with a
target and a stop is not a matter of finding the right band — the cost of crossing the spread
and paying the taker fee exceeds the edge in every band from 0.10 to 0.90, and the protective
exit gaps through its own level by four cents when it is needed.

Every one of the three costs is a **taker** cost. That is the same conclusion the fee
evidence reached from settlement rows, and the same one the case study reached about RN1: the
money is on the maker side of the book. A version of this study for a resting entry — buy on
the bid, not the ask — is the one worth running, and it needs the bid-depth collection named
in the audit before its fill assumptions mean anything.

## 10. Reproduce

```
gh workflow run research-sql.yml -f file=us_band_pilot.sql
```

Read-only by construction: the session runs under `default_transaction_read_only=on` and the
workflow refuses any file containing a mutating keyword outside a comment.
