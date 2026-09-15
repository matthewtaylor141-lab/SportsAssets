# RUN 81A — FIRST_RETAINED_OBSERVATION_DRAG

PRICE DETERIORATION OBSERVED AT THE FIRST RETAINED EXACT BOOK OBSERVATION.

Job 103479683577, `psql exit=0`, ten statements, commit `d4a69f4`, sha256
`d14799d1b80b4323052ad5ef01dbff00d57a70500dd8dc14b302d28a29afe5ed`.
Drawn 2026-09-12T02:04–02:10Z. `AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z`.

No settlement. No `resolved_prices`. No `ai_trades`. No TRUEEDGE. No latency
figure — nothing here is produced by, named after, or divided by an elapsed
time. `mirror_live=false`. Run 81B not started.

---

## 0. THE FINDING THAT COMES FIRST — U2 IS BEING DELETED

| control | run 80 (00:16Z) | run 81A (02:04Z) | verdict |
|---|---|---|---|
| U0 (trades at the cutoff) | 962,509 | 962,509 | **PINNED** |
| U2 (probe-backed events) | 214,708 | **214,651** | **DRIFTED −57** |

U2 **shrank by 57 events in 1 h 48 m** while U0 did not move by one row. The
mechanism is not inferred — it is in the code:

- `backend/sportsassets/workers/retention.py` runs an hourly loop that
  **`DELETE`s from `copy_probes` oldest-first by `probe_at`**, in batches
  (`BATCH_ROWS = 5000`, `BATCH_SLEEP_S = 0.25`, capped per cycle).
- `COPY_PROBES_FLOOR_DAYS = 30 + 7` — a **37-day horizon**, overridable upward
  by `RETENTION_COPY_PROBES_DAYS` but never below that floor.
- Migration `053_copy_probes_probe_at_idx.sql` exists specifically to index
  `probe_at` for that loop, and documents the `DELETE` verbatim.

**Consequences, stated plainly:**

1. **Estimator A's entire evidence base has a hard 37-day horizon.** Probes
   older than ~2026-08-06 are already gone. Whatever the mirror era contained
   before that window is not recoverable from `copy_probes`.
2. **It erodes oldest-first, so the loss is not random.** The audit loses its
   earliest observations first. Any figure compared across time is affected in a
   known direction.
3. **This supersedes §8 of RUN805_FINDINGS.** There I wrote that the
   settlement-cohort drift originated in `markets` or `copy_probes` and that I
   would not guess which. It is now determined: `copy_probes` is demonstrably
   being deleted, and U2 is a cohort built on it. (`markets` may drift as well —
   that remains untested and is not claimed either way.)
4. **Every figure below is stamped to this draw.** Re-running 81A tomorrow will
   return a slightly smaller population. The figures are not wrong; they are
   dated.

This is the strongest argument yet for the snapshot design, and it widens it: it
is not only settlement metadata that needs freezing.

---

## 1. THE SEMANTIC GATE — EVERY ASSUMPTION VERIFIED, NONE ASSUMED

All 214,651 rows, zero exceptions on every test:

| test | result |
|---|---|
| `trades.side` = BUY | 214,651 / 214,651 (SELL: **0**) |
| `copy_probes.side` = BUY | 214,651 / 214,651 (other: **0**) |
| depth element 0 is price-shaped (0,1] | 214,651 (**0** not) |
| depth element 1 positive | 214,651 |
| `best_ask` equals the minimum level price | 214,651 (**0** disagree) |
| `trades.price` agrees with `copy_probes.his_price` | 214,651 (**0** disagree) |

The `best_ask` = min-level test is the load-bearing one: it confirms the element
order `[price, shares]`, that the array is the **ask** side, and that `best_ask`
describes the same snapshot — all three at once, on every row. The depth walk is
therefore reading what this file says it reads.

---

## 2. THE SELECTION LADDER — no attrition before the depth requirement

| stratum | U2 | valid ask | q definable | sup. Q_A | sup. Q_B | sup. Q_C |
|---|---|---|---|---|---|---|
| A_CHAIN | 195,648 | 195,648 | 195,648 | 195,314 (99.83%) | 193,098 (98.70%) | 160,190 (81.88%) |
| A_POLL | 17,407 | 17,407 | 17,407 | 17,365 (99.76%) | 17,098 (98.22%) | 14,223 (81.71%) |
| A_S1 | 1,596 | 1,596 | 1,596 | 1,593 (99.81%) | 1,567 (98.18%) | 1,416 (88.72%) |
| **A_ONLINE_COMBINED** | **214,651** | **214,651** | **214,651** | **214,272 (99.82%)** | **211,763 (98.65%)** | **175,829 (81.91%)** |

Every U2 row has a valid ask and a definable q — **zero attrition** before the
depth requirement. So the top-of-book population and the U2 population are the
same 214,651 rows, and only the depth requirement trims anything.

**Backfill contributes zero rows.** Witness-ledger tests 1 and 3 both return
214,651: every U2 row is on an online lane. `A_BACKFILL_DIAGNOSTIC` does not
appear in any output because it is empty, confirming run 80.

---

## 3. TOP_OF_BOOK_MOVE — the primary result, on ALL 214,651 rows

**"The market had already moved."** Cents per share, `best_ask − p_h`. No depth
requirement, so the depth question does not touch this population.

| stratum | events | conditions | source notional | mean | median | p10 | p25 | p75 | p90 | % pos | % zero | % neg |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_CHAIN | 195,648 | 15,397 | $42,097,682.56 | 2.2890 | 1.0000 | 0.1705 | 1.0000 | 2.0000 | 4.0000 | 91.227 | 4.897 | 3.877 |
| A_POLL | 17,407 | 4,051 | $4,418,024.37 | 2.7800 | 2.0000 | −3.0000 | 0.0000 | 4.6000 | 9.0000 | 70.828 | 8.089 | 21.083 |
| A_S1 | 1,596 | 754 | $1,811,681.26 | 1.6755 | 1.0000 | 0.0000 | 1.0000 | 2.0000 | 3.0000 | 85.526 | 11.779 | 2.694 |
| **A_ONLINE_COMBINED** | **214,651** | **17,755** | **$48,327,388.19** | **2.3243** | **1.0000** | **0.0000** | **1.0000** | **2.0000** | **4.0000** | **89.530** | **5.207** | **5.263** |

**The median is one cent.** The mean is 2.3 cents and the p90 is 4 cents, so the
distribution is right-skewed: a minority of rows carry most of the deterioration.

Two lane facts worth keeping separate:

- **`s1` — the only physically plausible lane — shows the LOWEST deterioration**
  (mean 1.6755 cps, p90 3.0, 2.694% negative). It is also the smallest lane at
  1,596 events, so it carries the least weight in the combined figure.
- **`poll` is the widest and the most two-sided**: p10 −3.0, p90 +9.0, and
  **21.083% negative**. That is consistent with a cadence-sampled lane whose
  probe lands in an uncontrolled relation to the fill. It is not evidence about
  time — no clock is used here — but it is a reason not to read the combined
  mean as one homogeneous quantity.

Negative rows are carried with their sign throughout. Nothing is floored.

---

## 4. DEPTH SUPPORT, per scenario (Correction 1 quantities)

Pre-registered in shares: `Q_A = 0.10 × trades.size`, `Q_B = trades.size`,
`Q_C = 1000 / p_h`.

| scenario | stratum | requested | supported | **exhausted** | retention | requested shares | supported shares | support fraction |
|---|---|---|---|---|---|---|---|---|
| Q_A | A_CHAIN | 195,648 | 195,314 | 334 | 99.829% | 8,735,847.04 | 8,687,092.16 | 0.994419 |
| Q_A | A_POLL | 17,407 | 17,365 | 42 | 99.759% | 904,609.13 | 900,884.73 | 0.995883 |
| Q_A | A_S1 | 1,596 | 1,593 | 3 | 99.812% | 342,223.25 | 341,774.13 | 0.998688 |
| Q_B | A_CHAIN | 195,648 | 193,098 | 2,550 | 98.697% | 87,358,470.40 | 83,822,019.58 | 0.959518 |
| Q_B | A_POLL | 17,407 | 17,098 | 309 | 98.225% | 9,046,091.30 | 8,645,144.44 | 0.955677 |
| Q_B | A_S1 | 1,596 | 1,567 | 29 | 98.183% | 3,422,232.46 | 3,367,666.56 | 0.984055 |
| Q_C | A_CHAIN | 195,648 | 160,190 | **35,458** | 81.877% | 2,814,300,427.67 | 649,839,071.07 | **0.230906** |
| Q_C | A_POLL | 17,407 | 14,223 | **3,184** | 81.709% | 133,618,293.82 | 55,217,152.39 | **0.413245** |
| Q_C | A_S1 | 1,596 | 1,416 | 180 | 88.722% | 6,707,329.93 | 4,840,797.94 | 0.721718 |

**Q_C is a different kind of scenario from the other two.** At a source price of
1 c, `1000 / p_h` is 100,000 shares, so Q_C requests 2.8 **billion** shares
across chain and the retained book covers 23% of them. Q_A and Q_B are sizes the
retained book can nearly always price; Q_C is a stress scenario whose
depth-exhausted arm is large and whose supported arm is selected toward rows with
unusually deep books. Read its figures accordingly.

---

## 5. DRAG ON THE DEPTH_SUPPORTED_SUBSET

Components are disjoint and the lanes are disjoint, so the combined row is the
sum of the three lanes. Cents per share first, as ordered.

### Q_A (0.10 × his shares) — retention 99.82%

| stratum | events | conditions | source notional | mean total | median | p10 | p25 | p75 | p90 | mean ToB | mean depth | % pos | % zero | % neg |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A_CHAIN | 195,314 | 15,356 | $41,563,781.43 | 2.4158 | 1.0000 | 0.3000 | 1.0000 | 2.0000 | 4.0000 | 2.2819 | 0.1339 | 92.453 | 3.919 | 3.627 |
| A_POLL | 17,365 | 4,035 | $4,376,529.33 | 2.8993 | 2.0000 | −3.0000 | 0.0000 | 5.0000 | 9.3512 | 2.7450 | 0.1543 | 71.938 | 6.887 | 21.175 |
| A_S1 | 1,593 | 753 | $1,805,634.73 | 1.8878 | 1.0000 | 0.0000 | 1.0000 | 2.0000 | 4.0000 | 1.6636 | 0.2242 | 89.203 | 8.349 | 2.448 |

Dollars (DEPTH_SUPPORTED_SUBSET, 214,272 events):

| | A_CHAIN | A_POLL | A_S1 | **online combined** |
|---|---|---|---|---|
| TOP_OF_BOOK_MOVE | $148,122.13 | $18,160.98 | $4,304.07 | **$170,587.18** |
| DEPTH_SLIPPAGE | $45,588.71 | $3,859.25 | $851.64 | **$50,299.60** |
| **TOTAL_OBSERVED_DRAG** | $193,710.84 | $22,020.23 | $5,155.71 | **$220,886.78** |

**At the 10% size the book walk is nearly irrelevant**: depth slippage is 0.1339
cps against 2.2819 cps of top-of-book move on chain — **94% of the drag is the
market having already moved**, not our size walking the book.

### Q_B (his own shares) — retention 98.65%

| stratum | events | mean total | median | mean ToB | mean depth | ToB $ | depth $ | total $ |
|---|---|---|---|---|---|---|---|---|
| A_CHAIN | 193,098 | 2.7042 | 1.0000 | 2.2386 | 0.4656 | $1,216,413.78 | $908,369.50 | $2,124,783.28 |
| A_POLL | 17,098 | 3.2105 | 2.0000 | 2.6593 | 0.5513 | $141,277.24 | $98,236.01 | $239,513.25 |
| A_S1 | 1,567 | 2.2741 | 1.5246 | 1.6578 | 0.6164 | $41,351.31 | $20,083.15 | $61,434.46 |
| **combined** | **211,763** | — | — | — | — | **$1,399,042.33** | **$1,026,688.66** | **$2,425,730.99** |

At his own size the depth component becomes material — 0.4656 cps against
2.2386 cps on chain, about **17% of the drag**, up from 6% at Q_A.

### Q_C (1000 / p_h shares) — retention 81.91%, selected subset

| stratum | events | mean total | median | mean ToB | mean depth | ToB $ | depth $ | total $ |
|---|---|---|---|---|---|---|---|---|
| A_CHAIN | 160,190 | 3.2372 | 1.4821 | 1.6869 | 1.5503 | $7,909,389.57 | $12,168,927.84 | $20,078,317.41 |
| A_POLL | 14,223 | 3.8624 | 2.0966 | 2.0916 | 1.7708 | $800,600.62 | $1,098,790.37 | $1,899,390.99 |
| A_S1 | 1,416 | 2.5859 | 1.6623 | 1.5573 | 1.0285 | $58,976.43 | $45,915.70 | $104,892.13 |
| **combined** | **175,829** | — | — | — | — | **$8,768,966.62** | **$13,313,633.91** | **$22,082,600.53** |

At Q_C the depth component **overtakes** the top-of-book move (1.5503 vs 1.6869
cps on chain, and larger in dollars). Note that the mean top-of-book move on this
subset (1.6869) is *lower* than on the full population (2.2890): the rows whose
books are deep enough to support Q_C are not a random sample.

---

## 6. CLOSURE — all three scenarios close exactly

| scenario | witnesses | per-event violations | worst per-event gap | aggregate gap | verdict |
|---|---|---|---|---|---|
| Q_A | 214,272 | **0** | $0.00000000 | $0.000000 | **CLOSES** |
| Q_B | 211,763 | **0** | $0.00000000 | $0.000000 | **CLOSES** |
| Q_C | 175,829 | **0** | $0.00000000 | $0.000001 | **CLOSES** |

`TOP_OF_BOOK_MOVE_DOLLARS + DEPTH_SLIPPAGE_DOLLARS = TOTAL_OBSERVED_DRAG_DOLLARS`
holds to the cent on every supported event, at a tolerance of $0.005, with
witness counts in the hundreds of thousands. Not a vacuous pass.

---

## 7. DEPTH_EXHAUSTED — NOT IDENTIFIABLE FROM RETAINED DEPTH

No VWAP, no drag, no extrapolated last price. Only what the retained rows say:

| scenario | stratum | events | source notional | requested shares | observed shares | coverage | median row coverage |
|---|---|---|---|---|---|---|---|
| Q_A | A_CHAIN | 334 | $533,901.13 | 90,879.58 | 42,124.70 | 0.463522 | 0.458281 |
| Q_A | A_POLL | 42 | $41,495.04 | 8,113.68 | 4,389.28 | 0.540973 | 0.540941 |
| Q_A | A_S1 | 3 | $6,046.53 | 912.45 | 463.33 | 0.507787 | 0.406567 |
| Q_B | A_CHAIN | 2,550 | $3,036,124.09 | 6,633,913.41 | 3,097,462.59 | 0.466913 | 0.461647 |
| Q_B | A_POLL | 309 | $351,815.16 | 722,695.95 | 321,749.09 | 0.445207 | 0.421384 |
| Q_B | A_S1 | 29 | $56,447.82 | 93,437.61 | 38,871.72 | 0.416018 | 0.328571 |
| Q_C | A_CHAIN | 35,458 | $2,604,203.11 | 2,363,633,349.80 | 199,171,993.20 | 0.084265 | 0.358975 |
| Q_C | A_POLL | 3,184 | $284,528.07 | 91,833,200.41 | 13,432,058.98 | 0.146266 | 0.381371 |
| Q_C | A_S1 | 180 | $44,442.51 | 2,669,015.86 | 802,483.88 | 0.300667 | 0.434777 |

Where the retained book runs out it runs out **roughly halfway** at Q_A and Q_B
(median row coverage 0.33–0.54). Whether the other half was executable, and at
what prices, is not in the retained data and is not guessed at.

---

## 8. SEGMENTS — decision-time-valid variables only

No settlement variable appears. Online lanes only.

**By source price band** — deterioration falls monotonically as the contract gets
more expensive, while the median stays at exactly 1 cent in every band:

| band | events | source notional | mean ToB cps | median | Q_A retention | Q_A mean total cps |
|---|---|---|---|---|---|---|
| p1 [0.00,0.10) | 20,782 | $395,910.18 | **5.1845** | 1.0000 | 99.84% | 5.4944 |
| p2 [0.10,0.25) | 30,151 | $2,002,396.10 | 2.3489 | 1.0000 | 99.94% | 2.4896 |
| p3 [0.25,0.50) | 62,786 | $10,640,162.23 | 2.1658 | 1.0000 | 99.95% | 2.2867 |
| p4 [0.50,0.75) | 59,634 | $19,120,972.94 | 1.9735 | 1.0000 | 99.91% | 2.0822 |
| p5 [0.75,0.90) | 25,688 | $9,701,758.96 | 1.8550 | 1.0000 | 99.82% | 1.9248 |
| p6 [0.90,1.00) | 15,610 | $6,466,187.77 | **1.2185** | 1.0000 | 98.71% | 1.2488 |

A fixed one-cent tick is a far larger *proportional* move on a 5 c contract than
on a 95 c one, and the mean tracks that. The median does not move at all.

**By RN1 fill-size band** — deterioration falls as his trade gets bigger:

| band | events | source notional | mean ToB cps | median | Q_A retention | Q_A mean total cps |
|---|---|---|---|---|---|---|
| n1 <$10 | 91,530 | $284,350.74 | 2.7839 | 1.0000 | 99.99% | 2.8155 |
| n2 $10–100 | 65,255 | $2,482,165.99 | 2.2613 | 1.0000 | 99.88% | 2.3599 |
| n3 $100–1k | 45,790 | $16,209,155.86 | 1.7628 | 1.0000 | 99.67% | 2.0546 |
| n4 $1k–10k | 11,862 | $26,110,274.64 | 1.3102 | 1.0000 | 98.90% | 1.6724 |
| n5 ≥$10k | 214 | $3,241,440.96 | **1.2889** | **0.5131** | 96.26% | 1.5294 |

**His largest trades show the least top-of-book deterioration** — 1.29 cps and a
median of half a cent at ≥$10k, against 2.78 cps at <$10. The dollar weight of
the book sits in n3–n5 ($45.6M of $48.3M) where deterioration is lowest. This is
a decision-time-valid observation and nothing more: 81A does not test why.

**By sport:**

| sport | events | source notional | mean ToB cps | median | Q_A mean total cps |
|---|---|---|---|---|---|
| Non-Sports | 11,845 | $2,276,145.07 | 4.6253 | 1.0000 | 4.9622 |
| NFL | 8,909 | $1,814,282.44 | 3.6356 | 1.2000 | 3.8187 |
| Other-Sports | 16,283 | $3,105,270.60 | 2.6785 | 2.0000 | 2.8996 |
| MMA | 465 | $95,349.72 | 2.6214 | 1.0000 | 2.6696 |
| unclassified | 21,786 | $3,385,959.19 | 2.6394 | 1.0000 | 2.7554 |
| Soccer | 73,433 | $15,681,268.06 | 2.3216 | 1.0000 | 2.4630 |
| MLB | 10,117 | $2,194,126.96 | 1.8409 | 1.0000 | 1.9842 |
| Tennis | 70,966 | $19,645,176.22 | 1.6806 | 1.0000 | 1.7337 |
| NBA | 847 | $129,809.92 | 1.2125 | 1.0000 | 1.2471 |

Sports below 200 events are not shown. Tennis and Soccer carry $35.3M of the
$48.3M and both sit at or below the combined mean.

---

## 9. WITNESS LEDGER

| # | test | witnesses | verdict |
|---|---|---|---|
| 1 | U2 at the pinned cutoff | 214,651 | TESTED |
| 2 | rows with a valid best ask | 214,651 | TESTED |
| 3 | rows on an ONLINE lane | 214,651 | TESTED |
| 4 | rows on a lane outside the matrix — HALT if not zero | 0 | NOT TESTED |
| 5 | rows where q is definable | 214,651 | TESTED |
| 6 | FULLY_DEPTH_SUPPORTED at Q_A | 214,272 | TESTED |
| 7 | FULLY_DEPTH_SUPPORTED at Q_B | 211,763 | TESTED |
| 8 | FULLY_DEPTH_SUPPORTED at Q_C | 175,829 | TESTED |

Test 4 is correctly marked NOT TESTED: zero rows outside the matrix means the
test had no witnesses, which is not a pass. **The matrix's completeness is proven
instead by tests 1 and 3 both returning 214,651** — every U2 row is on a named
online lane, so none is unaccounted for. No HALT row was printed anywhere.

---

## 10. WHAT 81A DOES NOT SAY

- **Nothing about time.** No clock is used; `CLOCK_UNRESOLVED` stands. No figure
  here may be called a latency cost, converted into one, or divided by seconds.
- **Nothing about TRUEEDGE's $41,430.** 81A does not reproduce, approach, or
  compare against that claim. That is a run-84 question, asked once, at the end.
- **Nothing about what BETTOR would have achieved.** These are observed book
  states at the first retained probe under three pre-registered sensitivity
  sizes. None is "the BETTOR size", and the figures assume a fill at the walked
  VWAP, which is a property of the retained book and not a prediction.
- **Nothing about profit.** Drag is not loss; it is price deterioration against
  the source price. What survives it is an 81B question and 81B is gated.
- **Nothing about the rows retention has already destroyed.** §0.
