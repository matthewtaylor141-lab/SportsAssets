# P3 result — incentive economics, identity dependency, go/no-go

Retrieved 2026-09-22T18:22–18:26Z. **4 of 6 public reads used, 2 held.**
Unauthenticated, no account request consumed, nine account requests
untouched. No order, no deployment, no probe re-arm.

| # | URL | result |
|---:|---|---|
| 1 | `docs.polymarket.us/llms.txt` | documentation index → found `incentives-schema.json` |
| 2 | `docs.polymarket.us/api-reference/oapi-schemas/incentives-schema.json` | full API contract |
| 3 | `gateway.polymarket.us/v1/incentives?statuses=active&program_type=liquidityProgram&page_size=40` | 40 programs, live |
| 4 | same + `&instrument_states=INSTRUMENT_STATE_OPEN&page_size=8` | 8 **open** programs |

No retries were needed. **`GET /v1/incentives` requires no authentication.**

---

## 1. Incentive economics, from live parameters

### 1.1 Eligible markets, periods, pools — measured

| program | pool $ | discountFactor | **targetSize** | period | instrument state |
|---|---:|---:|---:|---|---|
| culture (Billboard) | **50** | 0.25 | **500** | `daily_event` | **OPEN** |
| crypto 1h up/down | 30 | 0.25 | **500** | `daily_event` | CLOSED |
| eFootball moneyline | 35 | 0.50 | 5,000 | `day_of` | CLOSED |
| eFootball moneyline | **100** | 0.50 | 5,000 | `live` | CLOSED |

**Program `status: active` is not instrument openness.** Every program
on page 3 carried `instrumentState: INSTRUMENT_STATE_CLOSED`. Filtering
to `INSTRUMENT_STATE_OPEN` returned a different population entirely —
Billboard culture markets, `eventStartTime` 2026-12-27. Those two fields
must never be conflated, and a naive "active programs" count would have
been the wrong eligible set.

### 1.2 Qualifying price levels and the scoring identity

```
Score = discountFactor ^ (ticks from best price) × OrderSize
payout = pool × (our score ÷ Σ scores inside the Target Size walk)
```

At the best price `ticks = 0`, so **our score is simply our size**.
Target Size uses **raw** size for the walk; scoring uses **discounted**
size. Snapshots are taken every second and each side is normalised to
1.0 per snapshot, so every second of a period weighs equally.

### 1.3 Same-level participation is NOT exclusion — correcting my claim

I wrote that "a single competitor resting 20,000 at the touch excludes
us entirely." **That is wrong and the retrieved text says so.** The
exchange *"walks from the best price outward, accumulating orders until
Target Size is reached"* and *"If Target Size is reached **before your
price level**, your order will not score."*

- **Same level:** a competitor resting Target Size **at our own price**
  does not lock us out. The walk accumulates by price level, so the
  whole best-price level is inside it and we share that level. Our share
  falls; it does not vanish.
- **Worse price:** exclusion happens only when Target Size is met at
  prices **strictly better** than ours. Then our score is zero however
  close we are.

Two different failure modes, and I had collapsed them into the harsher
one.

### 1.4 Net reward, by competing size at our own level

Our clip is **100 contracts at the best price**. *C* = competitor size
at that same level.

| program | C = 0 | C = 400 | C = 2,000 | C = 10,000 |
|---|---:|---:|---:|---:|
| culture daily_event | $50.00 | **$10.00** | $2.38 | $0.50 ✗ |

> **These use `100/(100+C)`, which is a special case.** The full
> calculation is implemented in `bettor_incentive_score.py`; at
> C = 200 the true answer is **$0.00**, because the side never
> reaches Target Size at all.

| crypto 1h up/down | $30.00 | $6.00 | $1.43 | $0.30 ✗ |
| eFootball day_of | $35.00 | $7.00 | $1.67 | $0.35 ✗ |
| eFootball live | $100.00 | **$20.00** | $4.76 | $0.99 ✗ |

✗ = under the $1.00 floor. **Exclusion case, separately:** if ≥ Target
Size rests at strictly better prices, reward is $0.00 in every column.

### 1.5 The payout unit is (market, date) — another correction

> **OVERSTATED; corrected in `PACKAGE_B_INCENTIVE_MEASUREMENT.md` §2.**
> The documentation says only *"Rewards under $1.00 are not paid
> out."* A response GROUPED by (market, date) describes how a
> reporting endpoint groups rows; it does not establish that the
> floor is applied to that grouping, nor that $35 and $100 become one
> economically interchangeable pool. Three candidate aggregations are
> now carried, and the decision rule uses the STRICTEST.


`GetIncentivesEarnedResponse.UserReward = {reward, programType,
marketSlug, date, status}`, and the endpoint states each entry *"sums
all payouts for a single (market, date) pair, where date is in Eastern
Time."*

**So the $1.00 floor applies to a market-day total, not to each time
period separately.** eFootball's `day_of` $35 and `live` $100 on one
market-day are **one $135 pool against one floor**, not two independent
tests. My per-period framing used the wrong unit and was too pessimistic.

### 1.6 Against the measured hurdle

> **WITHDRAWN; see `PACKAGE_B_INCENTIVE_MEASUREMENT.md` §3.** The
> $1.47/day came from C4 on NINE SPORTS MARKETS under a different
> policy. Culture markets are a different population. Subtracting one
> from the other establishes nothing. Rewards are reported instead as
> a CEILING on the trading loss they could absorb, and the trading
> loss for these markets at this size is measured by the same run.


C4's replay trading result was **−$10.23 over 6.96 days = −$1.47/day**,
already net of $6.48 of validated maker rebates, on ~$302.53 mean
concurrent capital across 9 markets.

| scenario | reward per market-day | days of hurdle covered |
|---|---:|---:|
| culture, C = 400 | $10.00 | **6.8** |
| eFootball live, C = 400 | $20.00 | 13.6 |
| culture, C = 2,000 | $2.38 | 1.6 |
| eFootball live, C = 2,000 | $4.76 | 3.2 |
| any program, C = 10,000 | < $1.00 | **0 — forfeited** |

**One eligible market-day at C = 400 more than covers a full week of the
trading shortfall.** At C = 10,000 the reward is forfeited entirely.

**The entire economic question now reduces to one unmeasured
quantity: *C*, the competing resting size at the touch in eligible
markets** — and to whether Target Size is met at better prices before we
get there.

---

## 2. Identity — what the order-ID match does and does not establish

**What it establishes.** If a venue read returns one of the 10,865
order ids we recorded at submission in 2026-09-06..10, that establishes
**continuity**: the credentials now in use address the same venue
account that produced those records — *subject to* two qualifications
that must be stated with it:

- **Provenance.** Our stored ids came from responses to our own
  submissions through `mirror_live.py`. They are trustworthy only as far
  as that ingestion path is.
- **Access scope.** It assumes venue order ids are visible only to the
  placing account. That is the normal design and it is **not something
  we have verified from the documentation.**

**What it does NOT establish.** Continuity with a *record* is not
identification of a *legal entity*. It cannot tell us the account is the
intended one — the correct legal account, the correct entity, the
correct regulatory registration. An account could be the one that placed
our historical orders and still not be the account management intends
this desk to trade.

**What independently establishes that mapping:** management's own
account records — the venue's account UI or the onboarding
documentation — which are outside this system and outside the venue API.

### The exact configuration action

| | |
|---|---|
| **field** | `pmus_account_ref` (env `PMUS_ACCOUNT_REF`), added in this package, default `""` |
| **value source** | the account identifier as shown in **management's Polymarket US account UI / onboarding record** — the field `account_identity()` will match against is whichever of `accountId`/`account_id`/`wallet`/`address`/`proxy` the venue returns |
| **where set** | `sportsassets-api` service environment, via the Render dashboard |
| **how** | set it directly in the Render UI. **Do not paste the value into this conversation, a commit, a workflow input or a log.** |
| **verification** | call `GET /api/desk/venue-identity`; it returns `{"verdict": "match", "field": "<name>"}` and **never the value** |
| **if unset** | verdict `no_expected` — an honest blocker; attribution stays "the authenticated account" |

I am not requesting the value and it must not be sent to me.

---

## 3. Go / no-go on the long collection

### NO-GO on the 72-hour, 5-cluster C0 run as specified.

It was aimed at the wrong uncertainty. Its own arithmetic said five
clusters could not resolve an event-level return effect, and C0 is a
baseline nobody would fund — presenting its collection as progress
toward validating an investment policy would be exactly the substitution
you flagged.

### GO on a much smaller, differently-aimed collection.

**The actionable uncertainty is now singular and measurable without
orders:** the competing resting size at the touch in incentive-eligible
markets, and whether Target Size is met at better prices.

| | |
|---|---|
| **hypothesis** | In incentive-eligible, currently-open markets, the time-weighted competing size at the best price is low enough that a 100-contract resting order earns ≥ $1.00 per market-day. |
| **measured** | per eligible market, per second-bucket: size at best price, cumulative size at strictly better prices vs Target Size, our implied score share, and the implied payout at the published pool |
| **needs no orders** | share is computed from the published ladder and the published formula |
| **instrument** | the existing stream — full `bids`/`offers` ladders and venue `transactTime` |
| **universe** | markets returned by `/v1/incentives` with `instrumentState = INSTRUMENT_STATE_OPEN` — **not** the frozen F1/F2/F3 rule, which selects for *trading* flow, not reward eligibility |
| **duration** | **24 hours**, not 72 |
| **coverage target** | ≥10 eligible market-days |
| **cost** | 1 WS subscription ≤100 slugs; ≤6 further public `/v1/incentives` reads for the universe; $0.00 at risk |

**How either result changes a trading decision:**

| result | decision |
|---|---|
| median implied payout ≥ $1.00/market-day **and** ≥ $3.00 in the top quartile | the reward stream plausibly covers the $1.47/day trading shortfall. **Escalate to M5** for a real resting order, to test whether a live order actually scores as the formula predicts. |
| median < $1.00/market-day, or Target Size routinely met at better prices | **stop.** The incentive route does not cover the shortfall and there is nothing left to fund. No further collection. |

That is a decision rule with a real off-ramp, on 24 hours of data,
against a measurement that is currently unknown and cheap to obtain.

---

## 4. The three statistical targets are different — which calculation was which

My earlier n = 18/71 calculation addressed **only** target A. It does
not size B or C.

| | **A. Event-level return test** | **B. Execution-model calibration** | **C. Incentive share measurement** |
|---|---|---|---|
| question | does the policy earn? | does the fill model predict? | what share can we score? |
| outcome | net $ per capital-hour | predicted vs observed fills | time-weighted 100/(100+C) |
| unit | **event cluster** | **order, or decision interval** | **market-day** |
| null | mean = 0 | calibration slope = 1, intercept = 0 | none — this is **estimation**, not a test |
| sizing | power against δ; **n = 18 at σ̂, 71 at σ_high** | driven by decision points, of which one market-day yields thousands; binding constraint is **book-condition diversity**, not count | precision of a mean; binding constraint is **number of distinct market-days**, not seconds within one |
| status | **not attempted — needs orders** | partially done (z_cluster −4.78, design effect 2.53) | **what the proposed 24 h run measures** |

**These are planning estimates, not schedules.** σ̂ = 0.002951 comes from
8 development clusters with a 95% χ² interval of [0.001951, 0.006006];
n scales with σ², so the true requirement spans a 4.1× range. The event
counts and the dates derived from them are **assumption-dependent
projections, not a guaranteed count or a completion date.** Eligible
event availability is itself unmeasured — which is part of what the
24-hour run would establish.

---

## 5. M5, scoped accurately — and still unexecuted

**One four-contract post-only order can demonstrate:**

- that a resting order is accepted and acknowledged;
- that cancellation works and is acknowledged;
- **if** a fill happens, that one fill's accounting — price, size, fee or
  rebate actually credited;
- that a resting order appears in the reward scoring at all.

**It cannot:**

- calibrate a fill distribution — **n = 1**, and a fill either happens or
  does not;
- establish time-to-fill, fill probability, or queue behaviour;
- establish profitability of anything;
- **close the execution gap.** One order is a smoke test of the
  mechanism, not a measurement of it.

Closing the execution gap needs **many** real resting orders across
varied book conditions — a different, larger and separately
authorized programme. M5 is the smallest step that would tell us whether
that programme is even wired correctly.

**M5 remains unexecuted and is not requested here. M4 likewise. No
trading activity will be created to observe a balance.**

---

## 6. Recommendation

1. **Deploy P1** (pinned `71918fc`) and run the ≤8-request read
   sequence. It closes the $32.02 and the seven books.
2. **Set `PMUS_ACCOUNT_REF`** in the Render dashboard from management's
   account record, so attribution stops being qualified. Value never
   enters this conversation.
3. **Replace the 72-hour C0 run with the 24-hour incentive-share
   measurement** above. It targets the one quantity that now decides the
   economics, needs no orders, and has an explicit stop rule.
4. **Do not authorize M5 yet.** Authorize it only if step 3 clears its
   threshold.

**The honest summary: the trading policies are all negative, and the
incentive programme is the only route that shows a credible path — one
eligible market-day at moderate competition covers a week of the trading
shortfall. Whether that competition is moderate is unmeasured, cheap to
measure, and measurable without risking a cent.**
