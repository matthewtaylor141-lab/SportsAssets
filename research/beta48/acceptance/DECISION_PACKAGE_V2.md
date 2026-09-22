# BETTOR — decision package v2

**C2 is a development candidate. Its results do not establish
profitability or queue robustness, and nothing below claims they do.**

Supersedes `DECISION_PACKAGE.md` and `ECONOMIC_PACKAGE.md`.

---

## 0. What the documentation changed

Both pages you cited are reachable — not from this container (the
egress proxy returns 403 CONNECT for `docs.polymarket.us`) but from the
GitHub runner via the existing read-only `fetch-docs` workflow. **My
"an absent SDK method means absent data" reasoning was wrong, and so
was the conclusion I drew from it.**

### Time & Sales exists — `https://docs.polymarket.us/faqs/execution-tape`

> "The Time & Sales Report is a minimal execution tape containing
> exactly 4 columns. It provides a pure log of executed trades without
> side, aggressor flag, or buyer/seller information."

| field | description |
|---|---|
| **Transaction Time** | timestamp of the executed trade |
| **Symbol** | contract identifier |
| **Last Price** | execution price (implied probability) |
| **Last Quantity** | size of the trade (number of contracts) |

File format `YYYYMMDD-time-and-sales.csv`; index at
`https://www.polymarketexchange.com/time-and-sales.html`; **reports
update at approximately 6:00 PM ET each day.**

**Retrieval status: NOT YET OBTAINED.** The index page builds its file
list in JavaScript ("Loading Time & Sales Reports…"), so the per-day
URLs are not in the served HTML. The exact ask is in §6.

**What it would fix, and what it would not.** It replaces the inferred
print model with actual per-print time/price/size — removing the
"unobserved volume at multiple prices" problem entirely. It **does
not** give aggressor identity, so whether a print lifted an offer or
hit a bid stays unknown, and our own queue position stays unknown. Both
uncertainties survive the upgrade.

### The fee page — and my engine was on the wrong regime

`https://docs.polymarket.us/fees`. All five worked examples use
**θ_taker = 0.0695**. `bettor_policy_ev.py` carried **0.06** as a
single constant, and a unit test pinned it there.

**Every taker fee the engine computed after 2026-09-17T03:59Z was
13.7% too small — and that error favours exactly the policies that
exit inventory as takers.** `forward/fees_v2.py` already carried the
SEP2026 coefficient with `THETA_TAKER_SEP2026_VERIFIED = False`; this
page is that verification.

**The capture straddles the cutover** (2026-09-13 → 2026-09-20), so a
single constant is wrong for one side of the corpus whichever value it
takes. `fee()` now selects by the fill's own timestamp. All five
published examples reproduce exactly:

```
1000 @ 0.10  taker  -6.26 (doc  -6.26)   maker +1.12 (doc +1.12)
1000 @ 0.65  taker -15.81 (doc -15.81)   maker +2.84 (doc +2.84)
1000 @ 0.30  taker -14.60 (doc -14.60)   maker +2.62 (doc +2.62)
1000 @ 0.90  taker  -6.26 (doc  -6.26)   maker +1.12 (doc +1.12)
1000 @ 0.50  taker -17.38 (doc -17.38)   maker +3.12 (doc +3.12)
```

**A multi-fill rule I did not have**, verbatim:

> "When an aggressive order fills against multiple resting orders, each
> fill is charged its banker's-rounded fee, adjusted so that the total
> commission collected across the order's fills never exceeds the
> banker's rounding of the cumulative exact fee. The adjustment can
> only reduce a fill's charge, never increase it. **Maker rebates are
> computed per fill, independently.**"

So per-fill rounding **over-charges a sweep**; `taker_fee_for_order()`
applies the cap. Maker rebates stay per-fill, which is what the replay
already did.

**And the rebate question is answered by the schedule**: "Maker rebates
are credited to your balance at the time of the fill." Still not
verified *on our account*, but no longer an open documentary question.

---

## 1. Corrected C2 comparison

Ladder-based queue, ladder-bounded prints, seconds-based horizons,
timestamp-selected fee regime.

| candidate | qfrac | eps | any fill | both legs | sum $ | **per contract** | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| C0 base | 0.00 | 762 | 260 | 98 | +0.86 | −0.027545 | [−0.1388, +0.0837] |
| | 0.25 | 838 | 52 | 1 | −110.06 | −0.036004 | [−0.1153, +0.0433] |
| | 0.50 | 843 | 41 | 1 | −98.45 | −0.035951 | [−0.1153, +0.0434] |
| | 1.00 | 861 | **0** | 0 | 0.00 | — | never fills |
| **C2** | 0.00 | 354 | 79 | 8 | +1.23 | **+0.001953** | [−0.0059, +0.0098] |
| | 0.25 | 381 | 24 | 1 | +15.14 | **+0.002004** | [−0.0051, +0.0091] |
| | 0.50 | 385 | 18 | 1 | +19.59 | **+0.002028** | [−0.0046, +0.0086] |
| | 1.00 | 394 | **0** | 0 | 0.00 | — | never fills |

The fee correction moves C2 by 2 parts in 1,000 (+0.001994 →
+0.001953 at qfrac 0), because C2 rarely exits as a taker. It moves C0
barely at all in the point estimate but by **$4.20** in total cash.

### The 11-vs-5 cluster reconciliation

They are **different populations, not two views of one**.

| | clusters | why |
|---|---|---|
| C0 | **11** | quotes every two-sided book ≥1 tick, so it reaches all 11 markets |
| C2 | **5** | `min_spread_ticks=2` **excludes six markets entirely** — their books are never 2 ticks wide in this corpus |

So C2's k=5 is not a subset chosen after the fact; it is **everything
its own entry filter admits.** The six excluded markets contribute zero
episodes to C2 by construction.

**This is also C2's central weakness.** Its edge is measured on five
events, four of them college-football markets captured on adjacent
days. It is not a portfolio result.

### The per-contract denominator, defined

`per_contract = (realised_cash + residual_value) / QUOTE_SIZE`, with
`QUOTE_SIZE = 100` the size of **each resting leg**, not the sum of
both and not the filled quantity.

This is deliberately conservative in one direction and not the other:
an episode that fills 3 contracts and one that fills 100 both divide by
100, so **partial fills dilute the per-contract figure**. The
alternative — dividing by realised fill quantity — would report the
economics of the fills we happened to get while hiding how rarely we
got them. Both are computable; this one is the stated primary.

### Exploratory vs adjusted inference

| | |
|---|---|
| **every interval above** | **EXPLORATORY.** Nominal 95%, no multiplicity adjustment. They describe the development sample, and nothing more |
| variants run to date | 4 + 8 + 5 sweep cells, + 4 candidates × 4 queue fractions, + 2 re-runs after the fee fix = **37** |
| **adjusted threshold** | Bonferroni α = 0.05/37 = **0.00135**, i.e. a ~99.87% interval. C2's exploratory interval already spans zero, so **no adjusted interval can exclude it** |
| **conclusion** | C2 is **not statistically distinguishable from zero**, before or after adjustment. It is selected as the best *point* candidate under a pre-declared small variant set, not as a finding |

**Queue robustness is not established either.** C2's point estimate is
stable across qfrac 0–0.5, which is evidence of *insensitivity over
that range*. At qfrac = 1.0 it does not trade at all. "Stable where it
fills, zero where it does not" is not robustness; it is a narrow
operating window whose location is unmeasured.

### Failed liquidation, halts, residual exposure

**"Hard-flatten" cannot guarantee a fill, and the replay no longer
pretends it can.** `walk_for_size()` executes against the actual
ladder: a 100-contract exit into a book with one bid level of 40
contracts fills 40 and leaves **60 unliquidated**, which then rides to
settlement and is valued at the observed outcome.

The corpus contains the conditions that make this bite:

| | |
|---|---|
| **halts observed** | **155 `MARKET_STATE_HALTED` observations across 5 markets**: `portst-ore` 40, `boxing-canalv-chrmbi` 35, `kentst-ohiost` 30, `ame-tij` 25, `uwg-etnst` 25 |
| **single-level books** | `portst-ore` shows **one bid level** for long stretches — liquidation capacity is that level and nothing more |
| **no-ladder rows** | 4 of 30,590; the fill is refused and counted rather than guessed |

A halt is not an exit. Any position standing when a market halts is
exposed until it reopens or settles, and `hard_flatten` fires only on
the **last OPEN observation before expiry** — which, if the market
halts and never reopens, never arrives.

---

## 2. Fee tiers — scenarios, and ours is unverified

`forward/fees_v2.py` already encodes the published ladder:

| tier | rebate on the taker fee | prior-month taker notional |
|---|---:|---|
| BASE | 0% | — |
| T10 | **10%** | 250k – 1M |
| T25 | **25%** | 1M – 10M |
| T50 | **50%** | 10M+ |

`effective_theta_taker(regime, tier) = θ_taker × (1 − rebate)`:

| tier | effective θ_taker | taker fee, 100 contracts @ 0.50 |
|---|---:|---:|
| BASE | 0.069500 | −$1.74 |
| T10 | 0.062550 | −$1.56 |
| T25 | 0.052125 | −$1.30 |
| T50 | 0.034750 | −$0.87 |

**`BETTOR_TIER_ELIGIBILITY = NOT_IDENTIFIED` and `TIER_VERIFIED =
False`.** BASE is used in every deployable number above. A tier is
worth at most −$0.87 of the −$1.74 in the best case, and **C2's whole
edge is +$0.20 per 100 contracts** — so a tier cannot rescue a
taker-heavy policy, but it materially changes the cost of the
hard-flatten leg.

### The recommendation I got wrong

I wrote that accelerated placement would buy **queue priority**. It
does not. `ACCELERATED_TIER_PLACEMENT_ROUTE` is a **fee-tier**
route — a documented application to be placed in a volume tier ahead of
earning it on prior-month notional. **It changes what we pay, not where
we stand in the queue.** The commercial draft in §5 asks for the right
thing.

---

## 3. Incentives

`/v1/incentives` is documented and prior research recorded its observed
state: **53 open markets, all UFC**, `INCENTIVES_PAGINATION_ADVANCED =
NO`, `TRUE_ACTIVE_INCENTIVE_UNIVERSE_SIZE = NOT_IDENTIFIED`.

**The LP candidate is still not evaluable, and the reason is the
universe, not the model.** Our capture holds **zero UFC markets**, so
there is no market in our data where a scoring model could be applied
to anything. The two business models stay separated as
`MAKER_ELIGIBLE_UNIVERSE_V1` requires:

```
A. STRUCTURAL MAKER     TRADING_NET_EX_INCENTIVES > 0
B. INCENTIVE-SUPPORTED  TRADING_NET_EX_INCENTIVES <= 0
                        TOTAL_NET_INCL_VERIFIED_INCENTIVES > 0
```

C2 sits in neither: its trading net is **not distinguishable from
zero**, so it is not A, and there is no verified incentive to make it
B. The read needed to change that is in §6.

**Competition sensitivity, stated as the rule rather than a number:**
any reward pool is shared, so our share is `our_score / Σ scores`. With
`TRUE_ACTIVE_INCENTIVE_UNIVERSE_SIZE` unknown and competitor scores
unobservable, **no share below 100% can be justified and 100% must
never be assumed.** Nothing in this package books incentive revenue.

---

## 4. Capital — scenarios, not blanks

Documented scenarios from the API surface, with our account's values
still unmeasured. **These are scenario arithmetic, labelled as such.**

For C2 at 100 contracts per leg on a 2-tick book around mid 0.50:

| | committed collateral |
|---|---|
| **both legs resting, no fills** | `(p_bid + 1 − p_offer) × 100 = (1 − captured_spread) × 100` ≈ **$99.00** |
| one leg filled, other cancelled | ≈ $50.00 |
| matched pair held | ≈ $99.00 until release |

### Scenario drawdown and inventory-hours

Computed from the replay's own episode durations and residuals, at
qfrac 0.25 (the middle assumption C2 actually trades at):

| | **C2** | C0 |
|---|---:|---:|
| episodes | 381 | 838 |
| markets / events reached | **5 / 5** | 11 / 11 |
| total | **+$15.14** | −$110.06 |
| **worst single episode** | **−$12.79** | −$42.69 |
| **worst cumulative drawdown** (episode order) | **−$16.85** | −$110.06 |
| median episode duration, all | 2,495 s = **0.69 h** | 2,495 s = 0.69 h |
| median episode duration, **filled** | 3,750 s = **1.04 h** | 3,750 s = 1.04 h |
| episodes with any fill | 24 of 381 | 52 of 838 |
| profit per episode | **+$0.0397** | −$0.131 |
| capital-hours per episode (both legs resting, ≈$99 × 0.69 h) | **$68.3** | $68.3 |
| **return per capital-hour** | **+5.8 basis points** | negative |

**The north star from `MAKER_ELIGIBLE_UNIVERSE_V1` is dollars per
working-capital dollar per hour, and C2 returns 5.8 bp of it** — on a
development sample, with an interval spanning zero, on five events.

**That figure assumes capital recycles the instant an episode ends**,
which is exactly what `unsettledFunds` exists to prevent. If proceeds
are held to the next settlement cycle, the denominator grows by roughly
an order of magnitude and 5.8 bp becomes well under 1.

**This is why release timing is the binding measurement rather than a
blank column.** It is the denominator of the only metric that decides
whether C2 is worth capital, and it is unmeasured.

**Drawdown caveat.** −$16.85 is the worst cumulative excursion in the
order episodes happened to occur in this corpus, at one queue
assumption, on 381 episodes. It is a description of one path, not a
risk estimate.

### Remaining account-specific measurements

| | documented | verified on our account |
|---|---|---|
| resting orders encumber buying power | `UserBalance.openOrders` is a distinct line | **NO** |
| proceeds are not immediately redeployable | `UserBalance.unsettledFunds` is a distinct line | **NO** |
| some owned shares unavailable to sell | `UserPosition.qtyAvailable` ≠ `netPosition` | **NO** |
| cancel is not instantaneous | `ORDER_STATE_PENDING_CANCEL` exists | **NO** |
| unsolicited cancellation | not found in the SDK surface | **NO** |
| closing-short margin release | `ORDER_INTENT_BUY_SHORT`/`SELL_SHORT` exist | **NO** |
| removing an offset raising requirements | **not documented anywhere read** | **NO** |
| our fee tier | ladder published | **NO** |

---

## 5. Commercial request — DRAFT, not sent

> **To:** Polymarket US institutional / partnerships
> **Subject:** Fee-tier placement enquiry — institutional maker programme
>
> We operate a decision-only research system against Polymarket US and
> are evaluating a bounded two-sided quoting programme in college
> football and comparable markets.
>
> We would like to understand the **accelerated fee-tier placement**
> route: specifically whether placement into the 10%, 25% or 50%
> taker-rebate tiers can be granted on committed forward volume rather
> than earned on prior-month notional, what commitment and reporting
> that requires, and the review timeline.
>
> We would also welcome confirmation of:
>
> 1. whether resting orders encumber buying power, and whether
>    `UserBalance.openOrders` reflects that;
> 2. when proceeds leave `unsettledFunds` and become redeployable;
> 3. whether a matched YES/NO pair is netted before settlement, and if
>    so when the collateral is released;
> 4. programmatic access to the daily Time & Sales CSVs;
> 5. the current scope of the `/v1/incentives` programme beyond the 53
>    UFC markets our earlier read observed.
>
> We are not requesting queue priority and understand tier placement
> does not confer it.

**Not sent. Approval not assumed.** The last paragraph exists because I
previously mis-stated what this route provides.

---

## 6. Bounded read-only data request

One request, no probe re-arm, no budget expansion. The expired probe
stays expired.

| # | endpoint / URL | method | cap | deliverable |
|---|---|---|---|---|
| **D1** | `https://www.polymarketexchange.com/time-and-sales.html` — the JSON/XHR the page calls to build its file list | GET | **1 request** | the per-day CSV URL pattern |
| **D2** | `.../YYYYMMDD-time-and-sales.csv` for **20260913 … 20260920** | GET | **8 requests** | real prints for the whole capture window, to reconcile against 30,588 ladders |
| **D3** | `/v1/incentives` (+ pagination) | GET | **10 requests** | current programme scope, scoring, qualifying depth, payout minimum, exclusions, payment delay |
| **D4** | `/v1/account/balances` | GET | **2 requests** (before/after nothing) | our own `openOrders`, `unsettledFunds`, `buyingPower`, `marginRequirement` at rest |
| **D5** | `/v1/portfolio/positions` | GET | **2 requests** | `netPosition` vs `qtyAvailable` at rest |
| **D6** | `/v1/order/preview` | POST | **3 requests** | whether a preview reports collateral **without placing an order** — the cheapest route to §4 |

**Total: 26 requests, all read-only, none placing an order.** Pacing at
the existing 0.10 req/s bound. D4–D6 touch our account and read no
credential; D6 is a POST but the documented preview path, and if it
turns out to place anything it is not run.

**Deliverables:** the corrected fill model on real prints; the
incentive candidate evaluated or refused with a reason; the four
capital columns filled; our fee tier named.

---

## 7. M1–M3 — execution experiments, for separate capital authorization

**These place real orders. They test mechanics. They cannot establish
profitability, and no result from them should be read as evidence about
C2's edge.**

Run only if D6 shows order preview does **not** report collateral.

| | **M1 collateral** | **M2 cancel race** | **M3 netting & release** |
|---|---|---|---|
| market | one 2-tick book, mid 0.40–0.60 | same market | same market |
| **order** | 1× BUY LONG, **4 contracts**, limit **5 ticks below best bid**, GTC | 1× BUY LONG, **4 contracts**, limit **at best bid**, GTC | 1× BUY LONG 4 @ ask (taker), then 1× BUY SHORT 4 @ (1−bid) (taker) |
| **intent** | priced not to fill | priced to fill | both fill immediately |
| reads | `/v1/account/balances` before, +30 s, after cancel | order state polled 1 Hz through `PENDING_CANCEL` | `positions` + `balances` hourly to settlement |
| **max collateral** | **$4.00** | **$4.00** | **$8.00** |
| **residual inventory** | ≤4 long if it fills despite the price | ≤4 long | a matched pair (pays exactly $4.00) |
| **max loss** | **$4.00** | **$4.00** | **$8.00 − $4.00 payout + 2 taker fees ≈ $4.20** |
| **cancellation handling** | cancel, then poll to terminal state; a fill inside the window is the M2 result arriving early and is kept | if a fill lands after the cancel request, that **is** the measurement | n/a — both are IOC-style takers |
| **cleanup** | if filled, `/v1/order/close-position` | if filled, `/v1/order/close-position` | hold the pair to settlement (self-liquidating) or close both legs |
| **establishes** | whether resting orders encumber buying power | cancel→ack latency and whether a fill lands inside it | when a matched pair leaves `unsettledFunds` |

**Maximum combined exposure: $16.00. Maximum combined loss: $12.20**
(M1 + M2 fully lost, M3 at its worst). Sequential, not concurrent —
M2 does not start until M1 is flat.

**Not executed. Trading stays disabled. Separate capital authorization
required.**

---

## 8. Where this stands

**C2 is the best point candidate under a small declared variant set. It
is not profitable, not established, and not shown to be queue-robust.**

- +0.0020/contract, exploratory CI spanning zero, on **5 event
  clusters that its own entry filter selects**.
- No fill at all when we assume we sit behind our own price level.
- **+0.30 basis points per capital-hour** before any settlement delay —
  the metric that actually decides this, and it is thin enough that
  release timing alone can move it by an order of magnitude.

**The next step is D1–D6, not capital.** Real prints would remove the
largest modelling assumption; the account reads would fill the only
metric that matters. Both are read-only.

---

## 9. Reproducing

**SHA: `fc5106837aa5a1b9bf46b79994d792d69d513265`**
(branch `claude/bettor-none-pool-fix`; production remains `ba87076`,
untouched).

```bash
python -m pytest research/beta48/test_bettor_policy_ev.py -q   # 28 tests
python research/beta48/bettor_tape.py                          # tape + ladders
python research/beta48/bettor_policy_sweep.py                  # declared set
python research/beta48/bettor_power.py                         # coverage
cd backend && python -m pytest tests/ -q -k bettor             # 1,464 tests
```

**Data status: DEVELOPMENT throughout.** No untouched PMUS holdout
exists; every out-of-sample path is prospective.
