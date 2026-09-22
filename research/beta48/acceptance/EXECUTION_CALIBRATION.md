# Execution calibration, policy comparison, residual experiment

Commits `6579f4f` → `ff63bf3` on `claude/bettor-none-pool-fix`. No
orders, no capital, no credential movement, no production change. **0 of
the 14 read-only account requests spent.** Everything below is SQL
against our own database plus public documents.

> **Simulated vs actual.** Sections 1–3 are *actual account history* —
> real orders, real venue responses, real money charged. Section 5 is a
> *replay simulation* and is not account performance. They are never
> added together.

---

## 1. Provenance of the 11,183 orders

**Real submissions to a real venue.** `mirror_live.py` has no dry-run or
simulation path; it submits through the `polymarket_us` SDK to
`api.polymarket.us`. The evidence is not the absence of a flag, it is
the venue's own replies: 10,865 rows carry a venue-assigned `order_id`
(`CAKQRR2RAPPV`…), 15,592 executions carry venue execution ids and
`transactTime` stamps spanning 2026-09-06T00:40:38Z → 09-10T16:45:44Z,
and **$2,438.72 net of commission was actually charged**.

**Lane:** the RN1 **mirror** lane, not BETTOR. Read here as evidence
about *venue mechanics only*. No P&L from it enters BETTOR accounting.
`live_orders` shows three lanes — `ioc`, `mirror`, `rest` — 166,585 rows.

### "Genuinely resting" — derived, not taken on trust

Defined from the book stored at placement: `BUY_LONG` resting iff
`wire < ask_at_place`; `SELL_LONG` iff `wire > bid_at_place`; and not
`taker_at_placement`. Cross-checking our own flag against the book:

| our flag says | book says | orders | with fill |
|---|---|---:|---:|
| not taker | **crossed** | 1,418 | 38 |
| not taker | resting | **7,223** | 2,294 |
| taker | crossed | 1,701 | 1,701 |
| taker | **resting** | 473 | 473 |

**The flag is wrong about one order in six** (1,891 of 10,815). The
1,418 flagged-resting-but-crossing filled only 2.7% — they were priced
against a stale book. Using the flag alone would have poisoned the
denominator.

**Denominator:** 7,223. Excluded: 367 with no book at placement, 1 with
no terminal timestamp, 3,119 that crossed. 1,006 distinct markets.

### Censoring, and why the aggregate 26% is not a number to reuse

Of the 7,223: **4,916 ended in cancellation, 1,933 in a fill.** A
cancellation is the *observation ending*, not a failure to fill.

| terminal state | orders | median s | p90 s |
|---|---:|---:|---:|
| cancelled (censored) | 4,916 | 84.9 | 622.8 |
| **filled (event)** | 1,933 | 86.3 | 365.0 |
| rejected | 268 | 0.8 | 3.1 |
| expired | 72 | 298.2 | 1,356.0 |
| lost | 34 | 1,280.0 | 50,135.5 |

Median time-to-fill (86.3 s) and median time-to-cancel (84.9 s) are
nearly identical — the signature of *our cancel policy*, not the market,
setting the denominator.

Fill rate against exposure, which is the only form in which it means
anything:

| exposure | post_only=no | post_only=yes |
|---|---:|---:|
| under 5 s | — | 2.2% |
| 5–30 s | 23.4% | 23.2% |
| 30–120 s | 37.8% | 37.0% |
| 2–10 min | **46.9%** | 43.5% |
| over 10 min | **10.4%** | 10.0% |

The over-10-minute collapse is **selection, not decay**: an order still
resting after ten minutes is disproportionately in a market where
nothing trades. Reading it as time-decay inverts the cause.

**`post_only` makes no material difference at any exposure** — at most
3.4 pp, unsystematic. 198 `post_only_rejected` (8.5% of post-only
orders) is the price paid for that non-difference.

**Partial fills are real:** of 4,518 orders with any fill, 643 (14.2%)
partial — 459 under half (mean fraction 0.163), 184 at half or more
(0.751).

---

## 2. Calibration and forward validation — it does not transfer

Fit P(fill | exposure band, cents inside touch) on **2026-09-06..08**
(3,362 orders), predict **09-09..10** (3,861 orders). Chronological,
never random: adjacent orders in one market on one day are not
independent draws.

| band | orders | observed | predicted | obs | pred |
|---|---:|---:|---:|---:|---:|
| under 30 s | 929 | 202 | 153.1 | 21.7% | 16.5% |
| 30–120 s | 1,520 | 493 | 653.7 | 32.4% | 43.0% |
| 2–10 min | 890 | 355 | 441.6 | 39.9% | 49.6% |
| over 10 min | 522 | 49 | 50.9 | 9.4% | 9.8% |
| **TOTAL** | **3,861** | **1,099** | **1,299.3** | 28.5% | 33.7% |

**Held-out error −200.3 fills (−5.19 pp); binomial s.e. 29.4; z = −6.82.**

**The model does not transfer forward two days.** A fill rate fitted on
our own orders fails out-of-sample against our own orders. So the
aggregate 26% is not transferable to a different policy — it is not even
transferable to the *same* policy on a later day. Any replay tuned to
match a historical fill rate inherits this instability.

### The mechanism gap, reported rather than filled

This is a **reduced-form** model (exposure and distance in, probability
out). It does **not** validate the replay's *mechanism* — queue position
and volume crossing our price. That needs contemporaneous depth and
prints for 2026-09-06..10, and:

- `mirror_orders` spans **2026-09-06 → 09-10**
- the BETTOR BBO capture spans **2026-09-13 → 09-20**

**They do not overlap.** `mirror_shadow` (top-of-book, 09-02→09-22) and
`copy_probes` (ask + depth, 08-16→09-22) do span the order window, and
Time & Sales files for 20260906–20260911 are retrievable by the route
already built. That is the mechanism-calibration path; it is **not done**
and nothing here is presented as though it were.

---

## 3. Two corrections

### 3a. Identical timestamp and price ≠ one aggressor

I wrote that two prints at an identical timestamp and price with
different quantities were "one aggressor sweeping two resting orders."
**That does not follow.** The public tape has four columns and carries no
order id, no execution id and no aggressor flag; two simultaneous prints
at one price are equally consistent with two separate trades. The claim
is withdrawn. The replay never depended on it — it only tests whether
trading *reached* our price — but the sentence overstated the evidence.

*Separately:* our **own execution records do** carry an `aggressor`
boolean, populated on all 15,592. Aggressor is known for our orders and
unknown for the public tape. Those are different datasets.

### 3b. Null `venue_cost` meant our records lacked a fee, not that none was reported

I concluded "the venue never reported a fee." **Wrong, twice over.**

1. `venue_cost` is `cashOrderQty` from the **short preview guard**;
   `pmus.py` maps it to `None` when it is 0.0. It is populated only on
   BUY_SHORT paths, and it is a *preview cost*, not a fee.
2. Tracing the ingestion to the **execution** records shows the fee was
   there all along.

**The venue reports fees on every execution**, and our fee engine is now
validated against them:

| aggressor | execs | shares | Σ n·p·(1−p) | fee charged | implied θ | published |
|---|---:|---:|---:|---:|---:|---:|
| maker | 345 | 22,053.6 | 3,758.98 | **−$46.97** | **−0.012495** | −0.0125 |
| taker | 2,940 | 218,469.6 | 41,448.03 | **+$2,485.69** | **+0.059971** | +0.06 |

Against the published schedule to the cent: **exact on all 345 maker
fills and on 2,735 of 2,940 taker fills** (205 differ by one cent, the
rounding boundary). θ_taker is 0.06 because these fills predate the
2026-09-17 cutover.

**Maker rebates are real and credited** — 313 executions carry a
*negative* commission. First direct evidence on our own account.

**What is genuinely empty is the preview.** `commissionsBasisPoints` and
`makerCommissionsBasisPoints` are `"0"` on all 7,733 previews, and
`cashOrderQty` is `0.0000` on all of them. **Preview fee fields exist and
carry nothing; execution fee fields carry the actual charge.** I had
merged those two findings into one wrong conclusion.

**Further reports not yet used:** the documentation exposes
`search-executions`, `download-executions-csv`, `search-trades`,
`search-orders`, `download-orders-csv` and `get-trade-stats`. Fee and
execution history is available beyond what we happen to have stored.

---

## 4. Incentive terms — retrieved, and they change the question

Retrieved from `docs.polymarket.us/incentives/liquidity` (public docs, no
allowance spent). The programme is **not a rate on capital**:

- **Pooled and scored**, with target size, a distance discount factor,
  snapshot weighting and a per-person cap.
- **Periods:** Early/pre-game (listing → 6 h before), Day-of (6 h →
  start), Live (start → settlement), Daily (midnight-to-midnight ET).
- **Paid** within 5 business days of period end, credited within 2 more.
- **Minimum payout $1.00** — anything less is not paid.
- **Nothing for cancelled or postponed games.**

Two consequences. First, a pooled score means our reward depends on our
*share* of a pool, so no per-capital-hour rate can be asserted from the
terms alone. Second, **the $1.00 minimum makes the M1–M3 scale
ineligible by construction**: at 4 contracts nothing reaches a dollar.

I did **not** capture the exact scoring formula — it sits mid-page and my
retrieval took the page tail. Stated as missing rather than guessed.

---

## 5. Remaining policy comparison — simulated replay, not account performance

**A bug my own check caught.** `bettor_policy_final.py` asserts a
tape-backed run records zero snapshot intervals. It fired at once:
`_available` tested `if tprints:`, and an **empty tuple** — the tape
saying *nothing traded* — is falsy. Every quiet interval was falling back
to the snapshot proxy, where an inferred volume delta could still fill
us: exactly the defect the tape was retrieved to remove. Fixed to
`tprints is not None`. **The tape column in `TAPE_AND_HISTORY.md` is
superseded by the table below.**

### Tried-variant register, kept whole

| | policy | status |
|---|---|---|
| C0 | quote every two-sided book ≥ 1 tick | baseline |
| C2 | 2+ ticks, at touch, hard-flatten | **failed — retired, not retuned** |
| C3 | C2 + cancel other leg on fill, cap unmatched at ½ clip, complete the pair | inventory-aware |
| C4 | at touch, mid 0.20–0.80, 2 h horizon, hold to settlement | incentive-aware LP |

All are **named combinations of knobs that already existed**. Nothing was
swept; nothing chosen after seeing its result.

| candidate | qfrac | eps | net $ | per contract | capital-hours | **$/capital-hour** |
|---|---:|---:|---:|---:|---:|---:|
| C0 | 0.00 | 814 | −35.79 | −0.000506 | 65,233 | −0.000549 |
| C0 | 0.25 | 793 | −158.77 | −0.008574 | 82,400 | −0.001927 |
| C0 | 0.50 | 796 | −228.60 | −0.013795 | 82,783 | −0.002761 |
| C0 | 1.00 | 801 | −171.41 | −0.011676 | 81,169 | −0.002112 |
| C2 | 0.00 | 348 | −69.73 | −0.006598 | 25,245 | −0.002762 |
| C2 | 1.00 | 386 | −98.37 | −0.037449 | 27,389 | −0.003592 |
| C3 | 0.00 | 470 | −90.22 | −0.006507 | 27,955 | −0.003227 |
| C3 | 0.50 | 414 | −58.74 | −0.014250 | 27,957 | −0.002101 |
| C3 | 1.00 | 410 | −75.84 | −0.020753 | 27,936 | −0.002715 |
| C4 | 0.00 | 44 | −10.23 | −0.004268 | 50,506 | −0.000202 |
| C4 | 1.00 | 79 | −35.15 | −0.174639 | 51,242 | −0.000686 |

**Every candidate is negative at every queue assumption.**
Inventory-awareness does not rescue C2.

**C4's break-even reward** (run at zero incentive, since the achievable
share is unknown): **$0.000188–0.000976 per committed capital-hour**, or
**165%–855% annualised on committed capital**. Against a pooled programme
with a $1 minimum payout, that hurdle is not plausibly cleared.

### Market ranking on executable economics

| market | intervals with a real print | median spread | net $ | $/cap-hr |
|---|---:|---:|---:|---:|
| aec-cfb-portst-ore-2026-09-18 | **34.7%** | 1 tick | **+6.56** | +0.000501 |
| aec-cfb-kentst-ohiost-2026-09-19 | **17.3%** | 1 tick | **+0.55** | +0.000038 |
| aec-cfb-coast-del-2026-09-19 | 11.8% | 1 tick | −27.40 | −0.001990 |
| aec-cfb-uwg-etnst-2026-09-19 | 5.2% | 1 tick | −97.05 | −0.007033 |
| aec-boxing-canalv-chrmbi | 2.7% | 2 ticks | −38.05 | −0.002650 |
| atc-lmx-pue-tol-2026-09-04 | 1.3% | 1 tick | −0.20 | −0.000035 |
| atc-lmx-ame-tij-2026-09-05 | 0.2% | **65 ticks** | −4.28 | −0.000757 |

**Profitability tracks tradeable flow almost monotonically.** Only the
two markets whose intervals contain a real print more than 17% of the
time are positive, on +$7.11 combined — well inside noise. Everything
below 12% loses. Inventory risk concentrates in the same place: the
thin markets are where unmatched legs sit longest.

---

## 6. The residual experiment

**Question:** *how long after a closing trade or settlement do proceeds
become reusable buying power?*

**Why a balance read cannot answer it.** A read at time T gives a level.
A latency needs a position-changing event at a known time with
observations bracketing it.

**Does existing activity supply one? No — and this was checked, not
assumed.**

- **Zero** orders anywhere in 11,183 mention insufficient funds,
  balance, buying power, collateral or margin.
- The only rejection reasons are `post_only_rejected` and
  `preview_mismatch`.
- The account never hit a collateral wall, so no rejection-then-
  acceptance pair brackets a release.
- The lane has been **idle 12.0 days** (last order 2026-09-10T16:45Z).
- No schema table records account balance at all.

**Therefore no suitable existing activity is available, and I am
returning the bounded mechanics experiment for separate approval rather
than placing an order, activating another lane, or waiting for an event.**

### M4 — buying-power release latency (REQUIRES APPROVAL, NOT EXECUTED)

| | |
|---|---|
| **Question** | interval between a closing fill and the proceeds being spendable |
| **Instrument** | one market, two ticks wide, mid near 0.50 |
| **Sequence** | (1) read balance; (2) BUY_LONG 4 @ touch, taker, to guarantee a fill at a known instant; (3) poll balance; (4) SELL to close, taker; (5) poll balance until it reflects proceeds |
| **Measured** | t(closing fill) → t(first balance showing proceeds), and whether a subsequent order sized to require those proceeds is accepted |
| **Gross exposure** | 4 × 0.51 + 4 × 0.51 = **$4.08** |
| **Peak committed** | **$4.08** (sequential, not summed) |
| **Taker fees** | 2 × banker(0.0695 × 4 × 0.51 × 0.49) = **$0.14** |
| **Worst path** | one leg fills, the other does not; single leg settles at zero: −$2.04 − $0.07 = **−$2.11** |
| **Maximum loss** | **$2.11** |
| **Venue requests** | ≤ 12, inside the existing 14 allowance |
| **Runtime cap** | 30 minutes, then flatten and stop regardless of result |
| **Not included** | no maker leg, no second lane, no change to any trading flag |

Θ_taker = 0.0695 here, not 0.06: any execution now is after the
2026-09-17 cutover.

**Cheaper alternative, preferred if acceptable:** `search-executions` /
`download-executions-csv` may already carry per-execution settlement and
credit timestamps for the 889 settled books. That is read-only, needs no
order, and costs a handful of the 14. **It should be tried before M4 is
approved**, and I have not tried it because it reads account data and the
allowance is meant to be spent against a stated question — this is that
question.

---

## 7. Containment (item 6)

- **Write permission scoped.** `fetch` (reads arbitrary public URLs) now
  holds `contents: read` and cannot write the repository. `publish`
  holds `contents: write`, talks only to GitHub, and moves what `fetch`
  produced. No step that reads the open internet holds a write token.
- **Data branch preserved** — `claude/tape-data`, orphan, only `tape/`.
  Checksums preserved: each `*.report.json` carries
  `sha256_of_bytes_read`, `declared_content_length` and `bytes_read`.
- **No production deployment, verified from the Render API:**

| service | branch | autoDeploy |
|---|---|---|
| edge-shadow | claude/session-njaewf | yes (suspended) |
| sportsassets-workers | claude/session-njaewf | yes |
| sportsassets-api | claude/session-njaewf | yes |
| bettortoken-api | main | yes |

  Neither `claude/tape-data` nor `claude/bettor-none-pool-fix` is tracked
  by any service.
- No production file changed; all work is in `research/beta48/` and two
  research workflows.

---

## 8. Open, and flagged rather than acted on

**7 mirror books never reached `closed`** — 6 `closing` and 1 `frozen`,
$9,280.69 of peak exposure, opened 2026-09-08..10, untouched for 12 days.
Whether those positions still exist at the venue is unknown from our
records. This is a reconciliation gap, not part of the economic result,
and I have not acted on it.
