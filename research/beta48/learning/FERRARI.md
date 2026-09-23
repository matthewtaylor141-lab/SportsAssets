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
