# BETTOR — decision package

**Status: the replay was repaired before any strategy was selected from
it. The repair changed the answer. One candidate is now positive and
robust to the binding unknown; it is not yet established.**

Supersedes `ECONOMIC_PACKAGE.md` wherever they disagree.

---

## 1. The replay defect, and what it cost

`bidDepth` / `askDepth` are **level counts, not quantities.** Verified
across all 30,590 BBO bodies: both fields are integers in [0, 67] with
about 60 distinct values, and `aec-cfb-portst-ore` reports
`bidDepth: 1` on a market with 61,918 shares traded.

The episode replay used them as the queue ahead of our order. It
therefore subtracted **1–67 shares** where the real queue is tens of
thousands. Both "queue models" in the previous sweep were effectively
front-of-queue.

**The −223.20 headline was substantially an artefact of over-filling.**
More fills meant more *one-sided* fills, and one-sided fills are what
pay the taker exit. Every fill rate in the previous package — q,
P(any fill), the 0.0753 double-fill rate — is an upper bound and is
withdrawn.

### The repair

30,588 `/v1/markets/{slug}/book` ladders were already in the capture,
carrying real per-level `qty`. The same market shows **one bid level of
72,217.52 contracts** — both the true queue and the confirmation that
`bidDepth: 1` is a count.

| | |
|---|---|
| join | ladder → BBO by nearest timestamp; **30,586 of 30,590 joined**, median skew 2.5 s, p90 2.5 s, 4 unjoined and reported |
| queue | quantity **strictly better** than us is always ahead; a **swept fraction** of the quantity at our own price is ahead too |
| prints | volume that can reach us is capped by the quantity that actually **disappeared** at our price or better between the two ladders |
| time | horizons in **seconds**, not observation counts |
| no ladder | refuse the fill and **count** it |

**Why the queue fraction is a swept parameter and not a number.** The
venue publishes aggregate ladder quantity, not per-order queues. Our
position *within* a price level is unobservable from any recorded data.
Pretending to know it is exactly the error that produced the last
result.

**Time & Sales: NOT AVAILABLE.** The SDK surface carries no public
trades endpoint — `/v1/portfolio/activities` is our own account only.
Per-print prices remain unobserved, which is why the ladder-consumption
bound replaces attributing an interval's whole volume to one price.

---

## 2. Observed vs simulated — the labelling correction

**This is replay P&L under an execution model. It is not measured
trading P&L.** No order in it was ours.

| quantity | status |
|---|---|
| touch prices, ladder quantities, cumulative `sharesTraded`, `lastTradePx`, market state | **OBSERVED** (verbatim HTTP 200 bodies) |
| post-expiry settlement outcome, 10 markets | **OBSERVED** |
| fee and rebate arithmetic | **COMPUTED** from the verified schedule |
| **our fills** | **SIMULATED** |
| **our queue position** | **SIMULATED — and unobservable**; swept |
| **partial fill sizes** | **SIMULATED**, bounded by ladder consumption |
| **cancellation race** | **SIMULATED**; charged against us; `ORDER_STATE_PENDING_CANCEL` confirms it is a real venue state |
| **exits and liquidation** | **SIMULATED** |
| per-print prices | **UNOBSERVED** — no Time & Sales |
| incentive payouts | **ESTIMATED** — see §5 |

Market touches alone cannot establish our fills. Nothing below claims
they do.

---

## 3. The declared candidate set, and the result

Four candidates, four queue assumptions. **A small declared set, run
once. No search for a positive backtest.**

| candidate | qfrac | eps | any fill | both | sum $ | **per contract** | 95% CI (11 clusters) |
|---|---:|---:|---:|---:|---:|---:|---|
| C0 base two-sided | 0.00 | 762 | 260 | 98 | +5.06 | −0.027517 | [−0.1388, +0.0838] |
| | 0.25 | 838 | 52 | 1 | −109.55 | −0.036001 | [−0.1153, +0.0433] |
| | 0.50 | 843 | 41 | 1 | −98.13 | −0.035949 | [−0.1153, +0.0434] |
| | 1.00 | 861 | **0** | 0 | 0.00 | 0.000000 | — |
| C1 inventory-aware | 0.00 | 902 | 458 | 0 | −100.96 | −0.005527 | [−0.0173, +0.0062] |
| | 0.50 | 853 | 57 | 0 | −115.09 | −0.036032 | [−0.1153, +0.0433] |
| **C2 wide-only + flatten** | **0.00** | **354** | 79 | 8 | **+3.56** | **+0.001994** | [−0.0059, +0.0099] |
| | **0.25** | **381** | 24 | 1 | **+15.56** | **+0.002012** | [−0.0051, +0.0091] |
| | **0.50** | **385** | 18 | 1 | **+19.86** | **+0.002034** | [−0.0046, +0.0086] |
| | 1.00 | 394 | 0 | 0 | 0.00 | 0.000000 | — |
| C3 complete-not-exit | 0.00 | 873 | 346 | 127 | −86.10 | −0.008173 | [−0.0209, +0.0045] |
| | 0.50 | 860 | 42 | 2 | −62.48 | −0.035758 | [−0.1151, +0.0436] |

**Candidates whose 95% lower bound exceeds zero: NONE.**

### What the table actually says

**Queue position is the binding unknown.** At `qfrac = 1.0` — resting
at the back of our own price level — *nothing ever fills* in any
candidate. The touch levels are large enough relative to the flow that
a new order never reaches the front inside a 40-minute window. That is
a capacity fact about these markets, and it was completely invisible
under the level-count bug.

**C2 is the strongest defensible policy.** Quote only books **2+ ticks
wide**, **at the touch**, and **hard-flatten before expiry**. Its point
estimate is **+0.0020 per contract and barely moves across queue
assumptions** (+0.001994 → +0.002034). Every other candidate collapses
by an order of magnitude when the queue assumption tightens. C2 is
robust because it quotes fewer, wider books and never carries
inventory through settlement.

**It is not established.** Its lower bound is below zero on 5 event
clusters, and 5 clusters is not a population.

**C0's apparent −0.0275 at qfrac 0** is not the policy losing money on
its fills — it is the settlement tail again, now on far fewer episodes.

---

## 4. Loss attribution — which decisions cost the money

From the base policy, by end state, at the original (over-filling)
queue model — kept because it is the decomposition that identifies the
drivers:

| end state | n | sum $ | mean $ |
|---|---:|---:|---:|
| FLAT_PAIRED | 71 | **+46.77** | +0.66 |
| FLAT_EXITED_TAKER | 280 | **−150.45** | −0.54 |
| FLAT_EXITED_MAKER | 46 | −51.84 | −1.13 |
| CARRIED_SETTLED | 5 | **−66.46** | **−13.29** |
| CARRIED_OPEN | 1 | −1.22 | −1.22 |
| NEVER_FILLED | 540 | 0.00 | 0.00 |

Two decisions carry the losses:

1. **Carrying inventory through settlement.** Five episodes lost
   −66.46; mean −13.29 against +0.66 for a paired episode. `hard_flatten`
   is in both of the two best candidates for this reason.
2. **Exiting one-sided inventory as a taker.** −150.45 across 280
   episodes. Taker fees paid −94.44 against rebates received +46.13:
   **getting out costs more than twice what all the maker rebates
   earn.**

### Denominators — corrected

The previous report divided both-legs-filled by *all* episodes,
including those that never had a fill. Both populations, stated:

```
all episodes                        943
never filled                        540
with ANY entry fill                 403
both legs filled (MAKER entries)    118
reconciles (540 + 403 == 943)       True
```

`P(both | any fill) = 0.2928`, `P(any fill) = 0.4274`. The mixed ratio
was 0.1251.

Two further corrections: "both legs filled" is now read from **maker
entry fills**, not from the end-state label — the `COMPLETE_PAIR`
recovery buys the second leg as a *taker* and would have reported a
98.8% "double-fill rate". And two episodes that expired having never
filled were mislabelled `CARRIED_SETTLED`; they are `NEVER_FILLED`.

**The q\* break-even threshold is withdrawn entirely.** A single
double-fill rate cannot represent a portfolio ending six different ways
with six different cash profiles. Complete episode cash flows replace
it.

---

## 5. Statistical protocol — auditable

| | |
|---|---|
| **primary population** | **all 11 clusters.** The ≥10-episode restriction was chosen *after* looking at outcomes, so it is **sensitivity, not primary** — the previous package had that backwards |
| **cluster = ?** | **market and event are 1:1 here** (11 markets on 11 events), so event clustering buys no extra protection over market clustering. Stated because it was previously implied that it did |
| **weighting** | unweighted mean of cluster means. This over-weights the four clusters holding one episode each; that is visible rather than corrected away |
| **interval** | t on cluster means, k−1 df. Naive SE 0.001035 vs cluster SE 0.049866 — **×48** |
| **variants tried** | now 4 + 8 + 5 + 4 candidates × 4 queue fractions = **33**. Bonferroni α = 0.05/33 = 0.00152 |
| **power** | σ across events 0.1654 (all clusters) / 0.00327 (dense). Events to detect +0.0025 at 80%, Bonferroni: **63,709** / **25** |

**A confidence interval spanning zero does not establish negative
expected profit**, and I stated otherwise. **An upper bound below the
half-spread does not either — the half-spread is not the acceptance
threshold.** The acceptance threshold is net cash per capital-dollar-hour
above the cost of capital. Both claims are withdrawn.

---

## 6. Case-study attribution — the audit trail

| | |
|---|---|
| **population** | `research/snapshots/u2_events_v1.jsonl.gz`, **214,609 fills**, one researched account's detected trades on the global Polymarket CLOB |
| **denominator** | **9,542 resolved conditions** of 17,752 with fills. **8,210 (46%) had no resolved settlement in the snapshot and are excluded** — this bounds everything below |
| **fee treatment** | **GROSS.** No fee is applied. Different venue, different schedule |
| **non-overlap** | matched = min over outcomes of the long positions. Pair channel = `matched × (1 − Σ avg costs)`. Settlement channel = `residual × (payout − avg cost)` where `residual = position − matched`. **Every share is in exactly one channel**; the two sum to the total by construction |
| **convention** | average cost per outcome. The **total is convention-free; the split is not** — FIFO matching would move profit between channels without changing the total |

| | capital | profit |
|---|---:|---:|
| pair channel | 14,558,910 (58.2%) | **310,254 (65.2%)** |
| settlement | 10,468,652 (41.8%) | **165,479 (34.8%)** |

Per-condition mean **+$49.86**, SE $27.68, 95% CI **[−$4.40, +$104.11]**
— crosses zero, and that is the *naive* interval, a floor.

**On copying: venue mismatch is not the whole argument, and I over-read
it.** 0 of 214,609 fills sit in a PMUS slug namespace, which rules out
*mirroring those fills*. It does **not** rule out a transferable
decision rule. What bears on that is the slippage: median
`best_ask_at_detection − their_price = +0.0100`, already moved against
us in **89.5%** of cases — four times C2's entire +0.0020 edge. A rule
we could reproduce would have to beat that, and nothing tested does.

---

## 7. What is NOT done, and why

I am not presenting these as finished.

| item | status |
|---|---|
| **Incentive-aware LP candidate (§2 of the directive)** | **NOT BUILT.** Prior research recorded `/v1/incentives` as covering **53 open markets, all UFC**, with `INCENTIVES_PAGINATION_ADVANCED = NO` and `TRUE_ACTIVE_INCENTIVE_UNIVERSE_SIZE = NOT_IDENTIFIED`. **Our capture contains zero UFC markets**, so the scoring model cannot be evaluated on any market we hold data for. Reading the live endpoint needs an acquisition allowance, which is spent, and I have not re-armed. The scoring implementation should follow a fresh read, not precede it |
| **Collateral mechanics (§4)** | **API surface audited, values NOT VERIFIED.** `UserBalance` carries `openOrders`, `unsettledFunds`, `qtyAvailable`, `marginRequirement`, `balanceReservation` as distinct lines — strong evidence that resting orders encumber and that proceeds do not recycle immediately. **No value has been read from our account.** Unsolicited cancellations, closing-short margin release, and offset-removal increasing requirements are all **NOT ESTABLISHED** |
| **Taker fee tiers (§5)** | **NOT ENUMERATED.** The verified schedule (`Θ_taker +0.06`, `Θ_maker −0.0125`, banker's rounding per fill) carries no tier structure in anything read so far. **Our actual tier is unverified** and is therefore excluded from the deployable case rather than assumed |
| **Decision table columns (§6)** | drawdown, capital-hours, inventory-release time and supportable turnover **cannot be computed** until the collateral mechanics above are measured. Release timing is the missing input for all four |

**The honest ordering is: measure the mechanics, then build the LP
candidate and the full table.** Building them on unverified collateral
behaviour would repeat exactly the mistake this package opens with.

---

## 8. The bounded execution experiment — for separate approval

**These place real orders. They are execution experiments, not
observations.** They test mechanics and **cannot establish
profitability.** Not requested here; trading stays disabled.

| | M1 collateral | M2 cancel race | M3 netting & release |
|---|---|---|---|
| **action** | place ONE resting buy, 4 contracts, ≥5 ticks below the touch (designed not to fill); read `UserBalance` before and after; cancel | place ONE resting buy at the touch, 4 contracts; issue cancel; poll order state through `PENDING_CANCEL` | buy 4 YES and 4 NO in one market as takers; hold the matched pair; read `UserPosition` and `UserBalance` each hour until release |
| **quantity** | 4 contracts | 4 contracts | 4 + 4 contracts |
| **max collateral** | ≤ $4 | ≤ $4 | ≤ $8 |
| **possible residual** | fill is possible despite the price → ≤ 4 contracts long | ≤ 4 contracts long if the race bites | a matched pair, which pays exactly 1 |
| **max loss** | ≤ $4 (the position goes to zero) | ≤ $4 | ≤ $8 minus the $4 the pair returns, plus two taker fees ≈ **$4.10** |
| **cleanup** | cancel; if filled, close via `/v1/order/close-position` | cancel; if filled, close | hold to settlement (the pair is self-liquidating) or close both legs |
| **establishes** | whether resting orders encumber `buyingPower` | whether a fill lands inside the cancel window, and the latency | when a matched pair leaves `unsettledFunds` |
| **does NOT establish** | anything about profitability | anything about profitability | anything about profitability |

**Total capital at risk across all three: under $20.** Every one is a
mechanics measurement. A separate, larger experiment would be needed to
test C2, and its design depends on what M1–M3 return.

**Commercial request for accelerated placement or market-maker terms:**
given that `qfrac = 1.0` means *never filling*, queue priority is
plausibly worth more than the entire spread edge. A request is worth
**drafting for management review**. It is not drafted here and must not
be sent or assumed approved.

---

## 9. The answer

**The strongest defensible policy is C2:** quote only books **two or
more ticks wide**, **at the touch**, **hard-flatten before expiry**,
never carry inventory through settlement.

- **+0.0020 per contract**, stable across every queue assumption where
  it fills at all.
- 95% CI [−0.0046, +0.0086] on **5 event clusters** — **not
  established**.
- Replay P&L under an execution model, on development data, with our
  queue position swept because it is unobservable.

**It is a candidate, not a result, and market making is not rejected.**
The previous conclusion rested on a fill model that was wrong in a
direction that hurt it.

**Recommended next step: M1–M3, under $20 total,** because release
timing is the missing input for capital-hours, supportable turnover and
the incentive candidate alike — and because C2's edge at +0.0020 per
contract lives or dies on how fast capital recycles.

---

## 10. Reproducing

```bash
python research/beta48/bettor_tape.py            # tape + ladders + settlement
python research/beta48/bettor_policy_sweep.py    # the declared candidate set
python research/beta48/bettor_power.py           # detectable edge, coverage
python research/beta48/bettor_rn1_attribution.py # case-study attribution
python research/beta48/bettor_copy_signal.py     # copy slippage, venue overlap
```

**Data status: DEVELOPMENT throughout. No untouched PMUS holdout
exists.** Every out-of-sample path is prospective. The settlement
labels were read for the first time in this work and are development
data from that moment; they are not relabelled as a holdout.
