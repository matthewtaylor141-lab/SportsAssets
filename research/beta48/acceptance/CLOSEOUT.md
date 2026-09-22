# Open-order verdict, incentive-adjusted economics, out-of-sample result

No order placed, cancelled or closed. No production deployed. No
credential moved. **5 of 14 requests remain spent; 9 unspent** — no new
venue call was made this round, for the reason in §2.

---

## 1. The reconciliation verdict, narrowed

**Verdict, as it should have been stated:**

> At **2026-09-22T17:51:18Z**, the venue reported **no filled positions**
> for the authenticated account. Two orders of ours are of unknown
> disposition, so **total exposure is not established as zero.**

| | |
|---|---|
| read at | 2026-09-22T17:51:18Z |
| route | `GET /api/desk/accounts`, API service's own venue credentials |
| response completeness | `configured: True`, `error: None`, positions list present and empty, `cash 20972.89`, `open_value 0.00`, `account_value 20972.89` |
| **account identity** | **NOT ESTABLISHED.** The payload carries no identity key — *"identity keys present in pm: NONE — the account cannot be identified from this payload."* The read is of *an* authenticated account; that it is the account which generated our 11,183 orders is **inferred, not verified.** |
| filled positions | **none reported** |
| resting orders | **NOT READ.** `/api/admin/open-orders` is a database view of `live_orders WHERE whale_username='manual'`. |
| settlement vs traded-out | **not distinguished** |

**Still outstanding, and not netted to zero:**

| book | order id | local state | side | qty | wire | notional |
|---:|---|---|---|---:|---:|---:|
| 838 | *(none recorded)* | `lost` | SELL_LONG | 32 | 0.41 | $13.12 |
| 1200 | `CD0NCD3GESK5` | `unknown` | BUY_LONG | 54 | 0.35 | $18.90 |

**~$32.02 of notional is unresolved.** `open_value: 0.00` does not close
it: it is a positions figure, and $32 against a $20,972.89 balance is
inside the rounding of every number on that card. Book 838's order has
no venue id at all, so we cannot even ask about it by id.

I previously wrote "all seven carry zero current venue exposure." The
supportable claim is **no filled positions at that timestamp**.

---

## 2. The nine remaining requests — not spent, and why

**No existing deployed route can read venue resting orders.** Searched
and confirmed:

| route | reads | verdict |
|---|---|---|
| `/api/desk/accounts` | venue portfolio, 30 s cached | positions only; no resting orders |
| `/api/admin/open-orders` | `live_orders` table | **database**, not venue |
| `reconcile_read.resting_orders()` | `client.orders.list` — **the right call** | **exists, tested, no HTTP route** |
| `reconcile_read.historical_activity()` | TRADE + POSITION_RESOLUTION, paged | **exists, tested, no HTTP route** |
| `bettor-capability-probe.yml` | imports `reconcile_read` | **the credentialless CI route — not retried** |

The capability exists inside the running service and has no door. So per
instruction, the **smallest exact read-only change** is prepared and
**not deployed**:

```
backend/sportsassets/api/app.py   +40 lines, 0 changed, 0 removed

  GET /api/desk/venue-resting-orders   -> reconcile_read.resting_orders()
  GET /api/desk/venue-activity         -> reconcile_read.historical_activity()
```

Both behind `require_desk`, the guard `/api/admin/open-orders` already
uses. Both return the module's dict verbatim, including `ok`,
`complete`, `pages` and `stop_reason`, so a bounded walk cannot be read
as an empty book. **No new venue capability** (the SDK calls already
exist and are already exercised by `backend/tests/test_reconcile_read.py`),
no new credential, no new dependency, no write path, no change to any
existing route's behaviour. It sits on a branch no service tracks.

**With those two routes deployed, the nine requests close the file:**
R2 resting orders (1 request) answers the $32.02; R1 activity for
2026-09-08..10 (≤4) separates settled from traded-out; R3 (≤2) resolves
the two order ids. **Total ≤7 of 9.**

---

## 3. The fee count reconciles — and two different claims

**134 + 66 = 200, and I reported 201.** The missing order is the
`neither` column, which I printed and then failed to carry into the
sentence:

```
discriminating orders (cap ≠ per-fill)   201
  venue matched the order-level cap      134
  venue matched the per-fill sum          66
  venue matched NEITHER                    1     <- the 201st
                                         ---
                                         201
```

That order is `CCY7S6CKCT75`: 6 executions, 40.00 shares, base 9.7582;
per-fill sum **0.57**, order-level cap **0.59**, actually charged
**0.58** — strictly between the two.

**Full reconciliation across all 1,929 aggressor orders:**

| | orders |
|---|---:|
| matched the order-level cap | **1,848** |
| below the cap, matched the per-fill sum | 66 |
| below the cap, matched **neither** rule | **15** |
| **total** | **1,929** |

Of the 15: 14 sit in the non-discriminating set (cap = per-fill, charged
one cent below both) and 1 is `CCY7S6CKCT75` above.

### The two claims, kept apart

| claim | status | evidence |
|---|---|---|
| **The charges satisfy the published bound.** Charge ≤ `banker(Θ·Σ n·p·(1−p))` | **TRUE, with zero violations** | 1,929 of 1,929 orders; every deviation is negative (−$0.01 on 77 orders, −$0.02 on 4) |
| **Our fee calculation reproduces every charge.** | **FALSE** | reproduces **1,848 of 1,929 = 95.80%**. 81 orders (4.20%) are charged 1–2 cents *below* what we compute. 15 are not reproduced by any rule tested. |

Our engine is **conservative and correct as a bound** — it never
under-charges — and **imprecise as a predictor** on about one multi-fill
order in twenty-four, by one or two cents. For a replay this is
immaterial; for a reconciliation that asserts "our arithmetic matches
the venue's," it is not, and I had been asserting the stronger claim.

**And the September regime remains unvalidated:** every fill is
2026-09-06..10, zero executions after the 2026-09-17T04:00Z cutover.
Θ_taker = 0.0695 has never met a real charge.

---

## 4. What $1.46/day actually is

Re-derived from the run rather than restated. **It is $1.47/day**; the
$1.46 used a rounded 7.0-day span against an actual 6.96.

| | |
|---|---|
| **window** | 2026-09-13T16:56:15Z → 2026-09-20T15:52:47Z, **6.96 days** |
| **policy** | C4 only, `queue_ahead_fraction = 0.00` — the most favourable of the four queue assumptions |
| **market population** | **9 of 12** markets, and concentrated: **30 of 44 episodes in two Liga MX markets** (`ame-tij` 20, `pue-tol` 10), one of which has a **65-tick median spread**. The four NFL markets contribute 11; three markets contribute 1 each. |
| **capital** | 100 contracts per leg; **50,505.7 capital-hours**; **mean concurrent capital $302.53** |
| **contracts filled** | 2,395.7 |
| **result** | **−$10.23** → **$1.47/day** |

**Costs included in that −$10.23:**

| | |
|---|---:|
| maker rebates received (published schedule, validated θ = −0.0125) | **+$6.48** |
| taker fees paid | $0.00 — C4 holds and never crosses |
| **net with rebates** | **−$10.23** |
| **net without rebates** | **−$16.71** → **$2.40/day** |

**Not included:** incentive rewards (run at zero), financing, slippage
beyond the replayed ladder, and any market impact from our own quote —
the recorded book never saw it.

### Against the actual incentive terms

The hurdle is **$10.23 over the window**. But rewards are paid
**per event-period**, with a **$1.00 floor per payout**. C4 quoted in 9
markets; at 3 qualifying periods each that is ~27 period-entries.

- $10.23 spread over 27 periods is **$0.38 per period** — **below the
  floor, so it pays nothing at all.**
- To be paid in most periods we would need ≥$1.00 in each, i.e. **≥$27
  over the window** — **2.6× the hurdle.**

**So the floor, not the total, is the binding constraint.** The question
is not "can this earn $10" — it is "can we place high enough in enough
individual pools to be paid at all." Clearing the floor would
*over*-cover the shortfall.

**Conservative share scenarios**, against ~$34 committed per market
(mean $302.53 over 9 markets):

| pool per period | share needed for $1.00 | plausible at $34 committed? |
|---:|---:|---|
| $200 | 0.50% | possibly |
| $1,000 | 0.10% | plausibly |
| $5,000 | 0.02% | plausibly |

**Unknown, not ruled out.** Pool size, scoring formula, per-person cap
and competing liquidity are all unretrieved. A $1.47/day hurdle is small
enough to be worth investigating and is **not** established as feasible.
Annualising it establishes nothing in either direction, which is why it
does not appear here.

---

## 5. The genuinely out-of-sample result — and its leak, removed

The earlier model conditioned on the **exposure band**: how long the
order actually rested before filling or being cancelled. **That is an
outcome.** A policy pricing an order does not know it. Per-market order
counts were the same error — the *final* count is unknown at the first
order.

Refitted on **decision-time predictors only**: spread at placement,
distance inside the touch, and the count of orders *already* placed in
that market before this one (a running count, known when the decision is
made). Same chronological split, same market-clustered variance.

| | leaky model | **decision-time model** |
|---|---:|---:|
| observed | 1,099 | 1,099 |
| predicted | 1,299.3 | **1,317.5** |
| error | −200.3 | **−218.5 (−5.66 pp)** |
| s.e. independent | 27.29 | 28.69 |
| **s.e. market-clustered** | 41.13 | **45.68** |
| z independent | −7.34 | −7.61 |
| **z market-clustered** | **−4.87** | **−4.78** |
| design effect | 2.27 | **2.53** |

**Removing the leak barely moved the result.** The exposure band carried
most of the *apparent* signal — 16.5% / 43.8% / 51.7% / 11.2% across its
four levels — and none of it was usable. The honest decision-time
predictors are much weaker:

| predictor | training fill rate |
|---|---|
| spread 1 / 2 / 3 / 4+ cents | 38.0% / 34.3% / 37.7% / 27.2% — weak, non-monotonic |
| prior orders in that market 0 / 1–3 / 4–10 / 11+ | **40.6% / 37.7% / 34.3% / 29.4% — monotonic** |

**Diagnosis:** the failure is a **level shift**, not a broken shape. A
single recalibration factor of 1,099 / 1,317.5 = **0.834** removes it.
The model over-predicts held-out fills by a constant ~17% relative.

**What this is and is not.** It is a genuine out-of-sample result for
**fill prediction**, on days whose outcomes were not used in fitting,
with dependence-aware errors. It is **not** an out-of-sample *policy*
result: it says nothing about profitability, and the validation days
09-09..10 have now been examined, so they are spent for any future test.

---

## 6. The out-of-sample policy result does not exist yet — the exact missing measurement

**It cannot be produced from anything currently held.**

| candidate source | span | why it cannot serve |
|---|---|---|
| BBO capture + 8 T&S files | 09-13..20 | **inspected while selecting the rule** (§ `FROZEN_SELECTION_RULE.md`) |
| `mirror_orders` | 09-06..10 | inspected; also no ladders, and a different policy |
| `mirror_shadow` | 09-02..22 | **top-of-book only — no ladder.** Cannot support queue position. |
| `copy_probes` | 08-16..22 | `best_ask` + vwap depth, but **probe-triggered on whale fills**, not a time series |
| fresh T&S (09-21, 09-22) | retrievable | prints without a contemporaneous book are not a replay |

**The missing measurement, exactly:**

> A **BBO capture with full ladder depth**, at ≤30 s cadence, over
> **≥5 distinct event clusters** admitted by the frozen rule
> (F1 ≥ 0.15, F2 ≤ 2 ticks, F3 ≥ 60 print-minutes), on dates
> **after 2026-09-22**, none of which is inspected before the run
> completes. Paired with the Time & Sales files covering those dates.

That is the same instrument as the expired observation probe, pointed at
rule-admitted markets instead of a fixed list. It requires a **new
observation budget** and is a separate authorization. Without it there
is no honest out-of-sample policy number, and I am not going to
manufacture one from data I have already read.

---

## 7. Standing

- **M4 unexecuted.** No trading activity created to observe a balance.
- The two profitable markets are **development evidence**, permanently.
- Four negative replay results stay scoped to C0/C2/C3/C4, four queue
  assumptions, 12 markets, 09-13..20, 100 contracts, and Θ = 0.0695
  which is itself unvalidated.
- Trading controls, deployment boundaries and lane separation unchanged.
