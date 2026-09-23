# Ferrari: the primary case study

**Status: identity confirmed, prior analysis retrieved, decomposition in
progress.** This file is the Ferrari strand's own record. The RN1 work
is retained as a comparator and the same kernel, dataset, registry and
prediction ledger are reused — nothing here starts a second
implementation.

---

## 1. Identity, confirmed from the records

`render-ops sql ferrari-decomp`, read 2026-09-23T13:00:09Z:

| whale id | username | address | added |
|---:|---|---|---|
| **26** | **`ferrariChampions2026`** | **`0xfe787d2da716d60e8acff57fb87eb13cd4d10319`** | 2026-08-21 13:11:30Z |
| 3 | `0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-…` | `0x2c335066fe58fe9237c3d3dc7b275c2a034a0563` | 2026-07-22 |

**Ferrari is not `0x2c33`.** That is `w2c33`, a separate account, exactly
as `BETA48_STATE.md:99` recorded and `engine-diagnostic.yml:355` treats
them. Everything here joins on the **address**, never the username —
the casing differs between code (`FerrariChampions2026` in
`whale_roster.py`) and data (`ferrariChampions2026`).

**Source of the address:** `021_seed_dossier_promotions.sql:11`.

## 2. Prior analysis retrieved — and one finding is superseded

### What the prior work established

`WHALE_COHORT_LESSONS.md`:

```
ROWS                        1,958,843   RECONCILES YES ($0.00/$0.00)
LIFETIME_ROI                +7.06%  — BEST of the six
REALIZED_MERGE_PNL          +$10.80M
PAIR_BEHAVIOR               Pair-positive. Merges profitably.
RESIDUAL_INVENTORY_BEHAVIOR Settlement. SELL channel under 0.54% of stake.
CAPITAL_RECYCLING_BEHAVIOR  Merge-driven.
```

`STRATEGY_EVIDENCE_MAP.md`, the six-account channel table:

| account | MERGE (pair) | SETTLED (hold) | held to settlement |
|---|---:|---:|---:|
| **ferrariChampions2026** | **+$10,804,770** | **−$8,417,042** | **57.7%** |
| rn1 | +$10,364,423 | +$2,935,868 | 43.9% |
| swisstony | +$260,123 *(disputed)* | +$23,357,748 | 68.0% |
| homerunhazard | −$476,349 | +$2,961,211 | 61.7% |
| kch123 | −$673,482 | +$10,499,607 | 91.9% |
| w2c33 | −$7,880,573 | +$15,843,715 | 80.3% |

**The hypothesis is supported and sharper than stated.** Ferrari has the
largest positive pair channel *and* the only large negative settled
channel of the six. Its residual policy is **passive**: hold to
settlement, with an active SELL channel under **0.54% of stake**. It
essentially never manages a leg that fails to pair — which is precisely
the gap our residual-inventory policy is meant to occupy.

### Two prior cautions that constrain what may be concluded

**a. The entry rule is refuted as a band curve.** `WHALE_COHORT_LESSONS`
records `OBSERVED_FAILURE_MODE`: the band relationship that is perfect
in Ferrari's merges is **absent in its money** — total-economics rho
−0.2571 with signs `+-++-+`, and its 0.10–0.30 band flips **+32.34%
MERGE to −2.00% TOTAL**. `WHAT_BETTOR_REJECTS`: *"Using its
merge-channel band curve as an ENTRY rule. Its own total economics
refute that."* That rejection stands and this work does not revisit it.

**b. The completed-pair basis is a selected subset.**
`band_discriminator.py`: *"mean_pair_basis is computed over COMPLETED
pairs only, and the never-completed share ranges from 23.5% to 69.3%
across these accounts. The basis is therefore conditioned on a subset
each account selects differently: one that abandons its worst first
legs looks better here than one that completes them."* Any pair-price
statistic below must be reported **with its never-completed share**, or
it is measuring selection.

### What is superseded

`STRATEGY_EVIDENCE_MAP.md` recorded **`FERRARI_MECHANISM =
NOT_IDENTIFIED`** because *"RAW FILLS ABSENT — `whale-full-history.yml`'s
matrix is `rn1, w2c33, homerunhazard, swisstony, kch123` — it does not
cover Ferrari"*, and called adding it *"a named missing interface fact…
cheap to close"*.

**That has since closed by a different route.** The live ingestion path
carries Ferrari: `cohort-inventory` (2026-09-23T12:10Z) shows
**908,046 fills, 35,120 markets, 98 active days, 2026-03-31 → now**, and
`ingest-latency` shows 59,173 chain-lane fills in the last 7 days. The
extraction workflow's matrix may still omit Ferrari; the database does
not. **Per-fill reconstruction is now possible and this strand does it.**

## 3. PMUS mechanics — what does and does not transfer

`bettor_merge.py` is explicit, and the answer differs by venue:

| | |
|---|---|
| `RETAIL_NATIVE_MERGE_AVAILABLE` | **NO** — *and the venue already nets.* Buying NO at 0.51 **is** selling YES at 0.49. The pair is realised **at the second fill**, returning capital immediately rather than at settlement |
| `INSTITUTIONAL_NATIVE_MERGE_AVAILABLE` (PMUS) | **NOT_IDENTIFIED** — `pmx_institutional.py` has no merge, redeem or split path, but it is MARKET DATA ONLY with `ORDER_SUBMISSION_IMPLEMENTATION = NONE`, so its silence is uninformative in both directions |

**Three consequences for this strategy:**

1. **Ferrari's MERGE/SETTLED split is a global-venue accounting, not a
   PMUS one.** Its +$10.80M "merge" channel is capital returned by an
   explicit merge call. On PMUS retail there is no such call; on PMUS
   institutional the capability is `NOT_IDENTIFIED`. The *economics* of
   completing a pair may transfer; **the mechanism does not, and no step
   here assumes it.**
2. **The 57.7% held to settlement is Ferrari's choice under a venue
   where merging was available.** Read as evidence about its residual
   policy, not about what PMUS forces.
3. `bettor_merge.py`'s own warning applies to our reconstruction:
   *"Never let complement acquisition look like a direct sell merely
   because the venue's implementation nets economically."* The
   reconstruction records the leg actually traded.

## 4. The three components, and what each may claim

| | Component | Predicts | Must NOT be read as |
|---|---|---|---|
| 1 | **Entry selection** — under what decision-time conditions Ferrari takes the first leg, new position vs adding | what Ferrari is likely to do | whether our order fills |
| 2 | **Pair completion** — timing, price, size, partial completion; completion probability **and** resulting net economics | what Ferrari is likely to do, and at what pair price | that completing is profitable — pairing frequently is not pairing profitably |
| 3 | **Our residual policy** — wait / adjust quote / reduce / exit / hold to settlement, from **our** cost basis, liquidity, fees and capital | which action has the best expected net outcome **for our account** | Ferrari's behaviour; this is the component where we deliberately differ |

**No stop-loss or forced exit is imposed because Ferrari suffered a
large loss.** Component 3 evaluates holding to settlement on the same
footing as every other action, and a large historical loss is a reason
to *measure* the alternatives, not to assume one.

The three predictions stay distinct throughout, as in the RN1 strand:
**what Ferrari does** ≠ **whether our order executes** (`P_FILL`, still
`NOT_IDENTIFIED`) ≠ **which action is best for us**.

## 5. Reused, not rebuilt

| Component | Reused from |
|---|---|
| estimators | `learn/kernel.py` — Ridge, Isotonic, Stumps, Hazard (sklearn cross-checked, 30/30) |
| scoring | `learn/metrics.py` |
| reconstruction | `learn/dataset.py` — entry rows, the clock rule, coverage exclusions, the conditional target |
| registry & ledger | `bettor_learn_model`, `bettor_learn_prediction` |
| decision surface | `bettor_ev_bridge`, `bettor_risk_engine`, `bettor_capital_allocator`, `bettor_inventory` |

The RN1 model (`rn1_complement_1h` v2, FROZEN) stays as the comparator.

---

## 6. The decomposition, from 908,046 fills

`render-ops sql ferrari-decomp`, run 35864172158, read 2026-09-23T13:01:59Z.
All 35,147 conditions Ferrari has touched, reconstructed per condition
from the fills — **not from an aggregate**.

### Position structure

| | | |
|---|---:|---|
| conditions touched | **35,147** | |
| both legs bought | 19,870 | 56.5% |
| **fully paired** (residual = 0) | **11** | **0.03%** |
| partly paired (residual > 0) | 19,859 | 56.5% |
| one leg only, never paired | 15,277 | 43.5% |
| **any SELL** | **0** | confirms `SELL channel under 0.54% of stake` — in this window it is exactly zero |
| **mean paired fraction of shares** | **0.2473** | |

### Capital

| | |
|---:|---|
| gross cost | **$251,098,013** |
| — committed to the **paired** portion | **$137,011,343** (54.6%) |
| — committed to the **residual** portion | **$114,086,670** (45.4%) |

### Pair economics

| | |
|---:|---|
| pair P&L at parity | **+$6,221,992** |
| return on paired capital | **+4.54%** |
| paired conditions below parity (clears) | 12,521 — **63.0%** |
| paired conditions at or above parity (**locks a loss**) | 7,349 — **37.0%** |
| median pair price | **0.9696** (clears 3.04¢) |
| p05 / p95 pair price | 0.6893 / **1.2147** |

**The pair arithmetic is venue-neutral.** Holding one YES and one NO to
settlement pays exactly $1.00 whether or not a merge call exists, so
`paired_qty × (1 − pair_price)` does not assume the global venue's merge
mechanism. Only the *timing* of the capital return depends on the venue.

### What this says about the hypothesis

**Supported, and materially reframed.**

1. **Pairing is genuinely profitable: +4.54% on $137.0M.** That is the
   mechanism worth learning.
2. **But 37% of completed pairs are at or above parity — they lock a
   loss.** p95 is 1.2147: a pair bought for $1.21 that pays $1.00.
   "Pairing frequently is not enough if completion locks losses" is not
   a hypothetical here; it is 7,349 conditions.
3. **Ferrari is not primarily a pair-completer.** Only **11 of 35,147**
   conditions end fully paired, the mean paired fraction is **24.7%**,
   and **45.4% of all capital sits in residual**. The pair channel is
   real and positive; it is a *minority of the book*.
4. **The residual is the dominant exposure and Ferrari does nothing
   with it.** $114.1M committed, zero sells, hold to settlement. The
   prior account-level aggregate puts that channel at **−$8,417,042**.
   That is the gap our component 3 exists to occupy, and it is now
   sized: **45.4% of the capital, managed passively.**

### Observed vs inferred — kept apart

| Class | Figure |
|---|---|
| **Observed cash flows** | every fill: side, outcome, size, price, both clocks. Gross cost $251.1M. Zero sells. |
| **Arithmetic on observed flows** | paired/residual split, pair price, pair P&L at parity. No model, no assumption beyond "one YES + one NO pays $1.00". |
| **Inferred / not yet resolved** | the **residual's outcome**. $114.1M of cost whose settlement is not in this query. The −$8.42M SETTLED figure is a prior account-level aggregate, not a per-condition reconstruction, and the two have not yet been reconciled. |
| **Not available** | fees, incentives, order submissions, cancellations, merges, redemptions, transfers. `lifecycle_observability = FILLS_ONLY`. |

### The selection caution, applied

`band_discriminator.py` warns that a pair-price statistic computed over
completed pairs only is conditioned on a subset each account selects.
Ferrari's **never-paired share is 43.5%**, inside the 23.5–69.3% cohort
range. So the median pair price of 0.9696 describes **the 56.5% of
conditions Ferrari chose to pair**, not its opportunity set — and it is
reported here with that denominator attached rather than alone.

### Still to reconcile

The per-condition reconstruction gives pair P&L **+$6.22M**; the prior
aggregate gives MERGE **+$10.80M**. These are different windows (our
`trades` starts 2026-03-31; the aggregate is lifetime) and different
methods. **They are not yet reconciled and neither is presented as
confirming the other.**

---

## 7. Components 1 and 2, built and measured

`backend/sportsassets/learn/ferrari.py` (`BETTOR_LEARN_FERRARI_V1`),
trained by `backend/tools/learn_train_ferrari.py`. It reuses the frozen
kernel, the metrics module, `dataset.split_by_time` and the clock and
coverage rules unchanged — no second implementation.

### The extract

`render-ops sql train-extract-ferrari`, run 35864978620, read
2026-09-23T13:08:14Z. Keyed on the **address**, 60 days,
**1 in 20 CONDITIONS by hashtext, then every fill of each sampled
condition** — the sampler keys on the market, not the fill, because
sampling fills would cut pairs in half and the reconstruction would
then be measuring the sampler.

| | |
|---|---:|
| fills | 19,890 |
| conditions | 1,006 |
| by lane | chain 16,167 · poll 3,397 · s1 326 |
| SELL fills | **0** |

### Missing prior inventory — asked, and answered

`prior_inventory` came back **empty: not one of the 1,006 sampled
conditions had a Ferrari fill before the window.** That is a strong
claim, so it is corroborated rather than accepted: the observed
**lifespan of a Ferrari condition inside the window is a median of 0.02
days (~30 minutes), p95 0.2 days, maximum 1.69 days.** Ferrari trades
short-lived in-play markets. A market that lives under two days cannot
have carried inventory from sixty days earlier, so an empty result is
what the market structure predicts.

**What that does and does not license.** Every entry row here is
therefore classified from what we can see, and the census below is not
hiding a pre-window leg. It remains true that `trades` begins
2026-03-31 and nothing before it exists in any form; that limit is
irrelevant *for this window* because no sampled market is old enough
to reach it, and it would matter again for any window drawn near the
data floor.

### Component 1 — the entry census

4,725 entry rows. An ENTRY is a buy taken while the complement is not
already held, so a row is a decision that is still open.

| class | rows | share |
|---|---:|---:|
| `NEW_POSITION_AS_RECORDED` | 1,006 | 21.3% |
| `ADD_TO_LEG_OPENED_INSIDE_THE_WINDOW` | 3,719 | 78.7% |
| `ADD_TO_LEG_OPENED_BEFORE_THE_WINDOW` | 0 | 0.0% |

Exactly one new position per sampled condition, and then **3.7 further
adds to the same unpaired leg before anything pairs.** Ferrari does not
take a position; it *builds* one, and the pairing question is asked
against a leg that is already several fills deep.

**No entry classifier was fitted, and that is a decision, not an
omission.** A NEW-vs-ADD classifier would be fitted on features that
contain the answer — `is_first_fill_in_market` *is* the label. An
ENTER-vs-SKIP classifier needs the markets Ferrari passed over, and
`trades` holds only what Ferrari did; manufacturing those negatives by
sampling other markets would be inventing the opportunity set and the
resulting score would measure the sampler. **The named missing
interface fact is a point-in-time record of the markets live at each
instant.** `bettor_*` books hold it going forward. They do not hold it
across Ferrari's history.

### Component 2 — completion, and whether completion clears

Two targets, identical features, identical split, identical estimator,
so a gap between them is a gap in predictability and not in what the
model saw:

| | |
|---|---|
| `complete` | a complementary BUY within H |
| `clears` | a complementary BUY within H at **entry price + completing price < 1.00** |

H = 3600 s, **frozen from the RN1 baseline before Ferrari's completion
times were looked at**, so the two accounts are comparable and no
horizon was selected by peeking.

`clears` ⊆ `complete` by construction. The pair price is a
**decision-level** quantity — this entry's price plus the price of the
fill that completes it — not the condition VWAP the §6 decomposition
used; a decision controls one fill. One YES and one NO pay exactly
$1.00 at settlement, so the arithmetic is venue-neutral and assumes no
merge call. **Every figure is GROSS: `trades` carries no fees, rebates
or incentives.**

| | |
|---|---:|
| entry rows | 4,725 |
| decided (horizon closed) | 4,682 |
| censored (horizon still open) | 43 |
| completed within the hour | 2,186 — **46.7%** |
| completed **and cleared** | 1,283 — **27.4%** |
| **cleared, given completed** | **58.7%** |
| median pair price | **0.9800** |
| p05 / p95 | 0.7539 / **1.1700** |
| mean gross edge per paired share | **+3.09¢** |
| mean paired share of the entry | 0.6924 |
| time to completion | p05 18 s · **median 603 s** · p95 2,829 s |

**41.3% of Ferrari's completions inside an hour lock a loss.** The
§6 condition-level figure was 37.0%; these are different measures on
different windows and neither confirms the other. Both say the same
thing qualitatively and the decomposition's warning is the operative
one: *pairing frequently is not pairing profitably.*

### What the models actually achieved: nothing yet, and here is the number

Chronological split, one condition to one part, 5 rows dropped where a
condition straddled a boundary:

| | TRAIN | CALIB | EVAL |
|---|---:|---:|---:|
| rows | 2,840 | 1,000 | 880 |
| decided | 2,840 | 1,000 | **837** |
| conditions | 626 | 208 | **172** |

| target | model | log loss | base rate | skill | AUC |
|---|---|---:|---:|---:|---:|
| complete | ridge | 0.7197 | 0.6890 | **−4.45%** | 0.5979 |
| complete | ridge + isotonic | 0.7137 | 0.6890 | **−3.59%** | 0.6031 |
| complete | stumps | 0.7057 | 0.6890 | −2.42% | 0.5806 |
| clears | ridge | 0.5995 | 0.5883 | **−1.90%** | 0.5835 |
| clears | ridge + isotonic | 0.5960 | 0.5883 | **−1.31%** | 0.5748 |
| clears | stumps | 0.6059 | 0.5883 | −2.98% | 0.5441 |

**An AUC of 0.60 on 837 rows is not 837 observations.** Those rows sit
in **169 markets**, one event drives every row in a market, and the
count that decides whether 0.60 means anything is the count of markets.
`metrics.clustered_jackknife` — delete-one-market, no randomness, so
the artifact still reproduces exactly — gives:

| target | model | AUC | 95% CI | skill | 95% CI |
|---|---|---:|---|---:|---|
| complete | ridge | 0.5979 | **[0.475, 0.721]** | −0.0445 | [−0.220, +0.131] |
| complete | ridge + isotonic | 0.6031 | **[0.485, 0.722]** | −0.0359 | [−0.228, +0.156] |
| clears | ridge | 0.5835 | **[0.475, 0.692]** | −0.0190 | [−0.126, +0.088] |
| clears | ridge + isotonic | 0.5748 | **[0.476, 0.674]** | −0.0131 | [−0.107, +0.081] |

**Every AUC interval contains 0.5 and every skill interval contains 0.
The honest verdict is NO DEMONSTRATED SKILL on either target.** The
apparent ranking signal is inside the noise of 169 markets. Reporting
"AUC 0.60" without that interval would have been precisely the error
this project keeps having to correct — a number quoted at a precision
its evidence does not carry.

No largest-cluster problem: the biggest market is 4.4% of the rows, so
the jackknife's own validity condition holds.

### Why, and what would change it

The feature set is **Ferrari's own fill history and the clock**. It has
no book, no opposing flow, no price path, no time-to-event-start. The
strongest weight in both models is `entry_price_dist_from_half`
(−4.58 / −3.13): completion is likelier when the entry is priced near
0.50, which is a statement about market structure, not about Ferrari.
With 626 training markets and nothing contemporaneous, a null result is
the expected result.

**This is a negative result and it is recorded as one.** It is not
retuned away. What would move it is the same missing fact component 1
named: a contemporaneous book at the decision instant.

## 8. A defect the Ferrari run found in the shared kernel

`Isotonic` returned **exactly 0.0** for a pooled block of zeros — a
finite sample asserting impossibility. On this run **12 of 837 held-out
rows were handed p = 0 and SIX of them completed.** Those six rows
alone contributed **0.198** of a 0.857 log loss; without them the
calibrated model scored **0.669 against a 0.689 base rate**. The
calibrator was helping, and one unearned certainty buried it.

Fixed by a shared affine shrink at fit time, `m → (n·m + 0.5)/(n + 1)`.

**The obvious fix is the wrong one.** A per-block Laplace correction,
`(w·m + 0.5)/(w + 1)`, shrinks a two-row block harder than a
thousand-row one, so a block at 0.0 can be lifted above a later block
at 0.05 — and the curve stops being monotone, which is the one property
PAV exists to provide. A single shared `n` cannot reorder anything.
Pinned by `test_the_correction_cannot_reorder_the_curve`, which asserts
the naive rule would have inverted that exact pair.

- Applied at **fit** time, so `from_dict` reproduces any earlier
  artifact unchanged. The frozen RN1 `rn1_complement_1h` v2 carries
  **no isotonic calibrator at all**, so nothing frozen moved.
- sklearn cross-check updated rather than weakened: it now unshrinks
  our curve before comparing (exact, 0 difference on all six cases) and
  adds three checks that sklearn *does* assert certainty here, that we
  do not, and that the whole difference is the affine map. **33/33.**
- Kernel tests 39 → 46.

## 9. What is still open on this strand

| | |
|---|---|
| **Component 3** | our residual-inventory policy — wait / adjust / reduce / exit / hold, from our cost basis, liquidity, fees, capital. Not started. This is where 45.4% of the capital sits. |
| **The three-way comparison** | reconstructed Ferrari vs Ferrari-inspired entry with our residual policies vs simple baselines, on the same opportunities. |
| **The residual's outcome** | $114.1M of cost whose settlement has still not been queried. |
| **+$6.22M vs +$10.80M** | still not reconciled, and still not presented as if it were. |

---

## 10. The residual's outcome — the open item, closed

`render-ops sql ferrari-residual`, run 35866605168, read
2026-09-23T13:23:01Z. Three statements, because three different things
were unknown and collapsing them would hide which.

### Coverage first, because a P&L over whatever happens to have resolved is a selected sample

| | | |
|---|---:|---:|
| Ferrari conditions | 35,162 | |
| present in `markets` | 28,756 | 81.8% |
| **absent from `markets` entirely** | **6,406** | **18.2%** |
| resolved, with payouts | 23,083 | 65.6% |
| known unresolved | 5,673 | 16.1% |

### The residual, settled where settlement is known

| | |
|---:|---|
| residual conditions | 35,151 |
| settlement **known** | 23,074 — 65.6% |
| settlement **unknown** | 12,077 — 34.4% |
| residual cost, all | $114,110,783 |
| residual cost, settlement known | **$76,983,170** (67.5% of the cost) |
| residual payout received | $75,647,486 |
| **residual P&L** | **−$1,335,685** |
| **payout ÷ cost** | **0.9826** |
| conditions won / lost | 10,249 / 12,825 — **44.4% win rate** |
| mean residual entry price | **0.4460** |

**Capital lock-up**, first fill to resolution: p05 0.73 h, **median 3.30
h**, p95 8.83 h. (801 conditions show `resolved_at` BEFORE the first
fill. That is an anomaly, not a finding — most likely a backfilled
resolution timestamp — and it is flagged rather than averaged in.)

**An orientation check, because an index error here would invert
everything.** `resolved_prices` is indexed by outcome index; if the
residual leg were read off the wrong index, payout ÷ cost would land
near 1.25, not 0.98. It lands at 0.9826, and the mean entry price
(0.4460) sits beside a 44.4% win rate. The orientation is corroborated
by the magnitudes, not assumed from the column comment.

### This materially reframes the hypothesis, in Ferrari's favour

**Ferrari's residual returns 98.26 cents on the dollar.** Holding
unpaired inventory to settlement cost it **1.74% gross** over
$76.98M — not a catastrophe. Its entry prices were **nearly
calibrated**: it paid a mean 0.4460 for legs that won 44.4% of the
time, so the passive policy is roughly a fair bet minus a small edge
against.

**And that is the number an exit policy has to beat.** Any exit —
DIRECT_EXIT, TAKE_COMPLEMENT, a requote — crosses a spread. On these
markets a 1.74% gross drag is a *low* bar for a spread to clear.
**On this evidence there is no case for an aggressive residual-exit
policy**, and imposing one because §2's aggregate showed −$8.4M would
have been exactly the error the mandate warns against: *do not impose
a stop-loss or forced exit merely because Ferrari suffered a large
loss.* The alternatives now have a measured number to beat instead of
an assumed disaster to avoid.

### What this does NOT establish

1. **It is 67.5% of the residual capital, not all of it.** $37.1M sits
   in 12,077 conditions with no settlement in our records, and 6,406
   conditions are not in `markets` at all. If unresolved markets are
   systematically different — voided, disputed, long-dated — −1.74% is
   a **selected** estimate. The selection is measured above and is not
   argued away.
2. **Gross.** No fees, no incentives. `bettor_fee_schedule` carries the
   published PMUS schedule; applying it is a separate step, and a
   1.74% gross drag is thin enough that fees could change the sign of
   the comparison against any alternative.
3. **It still does not reconcile with §2.** The prior account-level
   aggregate puts the SETTLED channel at **−$8,417,042**; this
   per-condition reconstruction gives **−$1,335,685** on 67.5% of the
   cost. Scaling naively to the whole residual gives about −$2.0M,
   which is still nowhere near −$8.4M. Different windows, different
   methods, different venues. **Two of the three figures in this file
   now disagree with the prior aggregates and none of them is being
   presented as confirming another.**

### What it unblocks

`bettor_exit_engine` states its own central refusal: *"It CANNOT rank
the actions, because ranking needs EV_HOLD — the value of doing nothing
— and that requires an independent fair value that does not exist."*

This is the first measured input to EV_HOLD: at the portfolio level,
**E[payout] ≈ 0.98 × price**, with a 3.3-hour median capital duration.
It is an aggregate, not a per-position fair value, so it does not by
itself lift the engine's `NOT_IDENTIFIED`. What it does is fix the
scale of the question, and the per-price calibration curve — does a leg
bought at 0.30 win 30% of the time? — is the next artifact, fittable
with the same isotonic estimator now that §8 has made it safe to trust
at the tails.
