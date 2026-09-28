# A combo is not an indirect hedge — it is the opposite trade

**The owner's instruction:** *"Do not substitute a combo contract for an indirect hedge. An API that creates or trades a multi-leg instrument does not by itself replicate holding two separate legs."*

The instruction was right and my suggestion was wrong **in the most important direction: a combo inverts the risk profile of the hedge.** Not "differs from" — inverts.

**Source:** `docs.polymarket.us/faqs/combos-faqs`, retrieved `2026-09-28T13:06:52Z` via `fetch-docs` run 104; plus `/api-reference/combos/overview` and `/api-reference/rfqs/overview` (run 100/101) and `/fees`.

---

## 1 · The exact payoff, in the venue's own words

> *"A combo combines 2 to 10 markets into a single position. Each market you add is a leg, and each leg carries the side you took on it, buy or sell. **Every leg has to resolve the way you took it for the combo to pay.**"*
>
> *"**A combo is a single position that settles once, at a single value.**"*
>
> *"Your payout is the full potential payout **multiplied by the value of every leg**: a leg that resolves the way you took it is worth \$1.00; a leg that resolves against you is worth \$0.00; a leg that cannot resolve at all is worth its last fair market price (LFMP)."*
>
> *"[If one leg resolves against you] that leg is worth \$0.00 and **the combo pays \$0.00. This holds however the other legs turn out.** One leg going against you is enough, and the remaining legs cannot make up for it."*

**The payoff is MULTIPLICATIVE.**

```
combo payout = potential_payout × Π (leg value),   leg value ∈ {1.00, 0.00, LFMP}
```

Separate holdings are **ADDITIVE**: each contract settles independently and the position's payout is the sum. A product with a zero factor is zero; a sum with a zero term is the other term. **That single difference is the whole hedge.**

## 2 · Bears ML / Panthers +4.5 — the complete payoff table

Hold **1 contract of each**, bought at `b` (Bears ML) and `p` (Panthers +4.5). The scenarios are exhaustive over the margin.

### (a) Separate holdings — two independent binary contracts

| Scenario | Bears ML | Panthers +4.5 | **Total payout** |
|---|:--:|:--:|---:|
| Bears win by ≥ 5 | \$1.00 | \$0.00 | **\$1.00** |
| **Bears win by 1–4** | \$1.00 | \$1.00 | **\$2.00** |
| Tie | \$0.00 | \$1.00 | **\$1.00** |
| Panthers win | \$0.00 | \$1.00 | **\$1.00** |

**Cost `b + p`. Floor \$1.00 — the position can never return \$0.00.** Exactly one leg always pays, and in the 1–4 band both do. *That guaranteed floor is what makes it a hedge.*

### (b) The proposed combo instrument — both legs bought, one position

| Scenario | Leg values | **Combo payout** |
|---|:--:|---:|
| Bears win by ≥ 5 | 1.00 × **0.00** | **\$0.00** |
| **Bears win by 1–4** | 1.00 × 1.00 | **full potential payout** |
| Tie | **0.00** × 1.00 | **\$0.00** |
| Panthers win | **0.00** × 1.00 | **\$0.00** |

**Cost is the combo's own quoted price `c`, and `c ≪ b + p`** — the fee page's worked examples price combos at \$0.10 and \$0.50 as a single `p`, consistent with a conjunction being less likely than either leg.

**The profile is inverted.** Separate holdings: **never zero**, floor \$1.00, extra in the middle band. Combo: **zero in three of four scenarios**, larger payout in one. A combo is a *leveraged directional bet on the conjunction*. Substituting it for the hedge would **replace a position with a guaranteed floor by one that is worthless unless a specific margin band lands** — and this is a lane whose authorized scope is *exposure-reducing only*. It would do the opposite.

### (c) Any supported offsetting order plan

The venue documents **one instrument per market — the YES side — and selling YES is buying NO.** So the only order-level offset for a Bears ML long is *selling Bears ML*:

| Scenario | After selling the Bears ML long | Panthers +4.5 held separately | Total |
|---|:--:|:--:|---:|
| Bears win by ≥ 5 | \$0.00 (position closed) | \$0.00 | **\$0.00** |
| Bears win by 1–4 | \$0.00 | \$1.00 | **\$1.00** |
| Tie | \$0.00 | \$1.00 | **\$1.00** |
| Panthers win | \$0.00 | \$1.00 | **\$1.00** |

Selling realises the Bears leg at the market price and leaves only the Panthers exposure. **There is no single supported order that creates the cross-market structure** — the separate-holdings column requires two positions in two markets, and the API offers no instrument that is equivalent to holding them.

### The LFMP column changes the shape again

A leg that cannot resolve settles at LFMP and **is not removed**: *"It is assigned a fair price, and your payout is multiplied by that price."* So a void leg **scales the whole combo down** — on the venue's own example, a 3-leg \$80 combo with one leg at LFMP \$0.60 pays \$48.00, and with two legs at \$0.60 and \$0.25 pays \$12.00. And: *"Can a leg at LFMP rescue a combo that already has a losing leg? **No.**"*

For separate holdings a void leg affects **only that leg**. **A multiplicative void haircut has no analogue in additive holdings**, and it is a second reason the two are not substitutes. LFMP is set by the **Settlement Committee** and *"its decisions are final"* — a discretionary input in the payoff, which the settlement-evidence gate would have to treat as unestablished.

---

## 3 · The three things I must not claim, because they are NOT established

| Question | Status |
|---|---|
| **Supported leg combinations / correlation restrictions** | **NOT ESTABLISHED, and it may void the whole table above.** The API page says only that legs must be *"open, tradable, supported instruments"* and that *"duplicate symbols and **invalid combinations** are rejected"* — **without enumerating what makes a combination invalid.** Bears ML and Panthers +4.5 are **legs of the same game**, i.e. strongly correlated. Venues commonly restrict or specially price same-game correlated legs. **It is entirely possible this combo cannot be created at all**, and I built a payoff table for an instrument whose existence I have not verified. |
| **Inventory treatment** | **NOT ESTABLISHED.** A combo is *"a single position that settles once"* under a synthetic `caoc-…` symbol. Our funded book keys inventory on `us_market_slug` and `bettor_venue_position_model` assumes one signed net position per market. How `GET /v1/portfolio/get-user-positions` reports a combo, and whether it nets against legs held separately, is unread. |
| **Collateral / margin effect** | **NOT ESTABLISHED.** Nothing read says holding a combo economises margin against separately-held legs. |

---

## 4 · RFQ: taker-side removes a queue assumption, nothing more

I wrote that being taker-side means `P_FILL` is "not on that path". That is true of the **passive queue** assumption and **false as a claim about execution certainty.** The RFQ lifecycle has its own qualification, and the venue documents every step of it:

| Stage | What can happen | Consequence |
|---|---|---|
| `POST /v1/rfqs` | the RFQ is created | no quote is guaranteed to arrive |
| quotes arrive | or do not | **no liquidity is promised** |
| **quote expiry** | a quote can lapse before acceptance | acceptance must beat expiry |
| `PUT …/accept` | accepts **one side** of a quote | not an execution |
| **`PUT …/confirm`** | *"Confirm an accepted quote during **last look**"* | **the counterparty gets a last look**; this is a second gate we do not control |
| `QUOTE_STATUS_EXECUTED` | *"the paired exchange orders **were submitted** and their order IDs were recorded. **They do not mean the orders filled.**"* | submitted ≠ filled |
| after execution | *"If either `restRemainder` setting is true, unfilled quantity on that side may remain on the book."* | **partial fill with a resting remainder — which puts a passive queue back in the picture** |

**So an RFQ replaces one execution uncertainty with four**: quote arrival, quote expiry, counterparty last look, and partial fill leaving a remainder. It does **not** establish guaranteed execution and it does **not** eliminate execution qualification. And the final row is the sharpest: a resting remainder reintroduces exactly the passive-fill problem `P_FILL` exists for.

The same applies to crossing a combo's own book: it is subject to the **same unestablished book-currency predicate** that refuses every funded exit today. A newly found instrument is not a way around that gate.

---

## 5 · The four capabilities stay OPEN

Per the owner: *"Keep the four capabilities open until their actual required behavior is implemented and demonstrated. Documentation discovery is useful evidence, not completed integration."*

| Capability | State | What today changed |
|---|---|---|
| `FORM_INDIRECT_HEDGE` | **OPEN** | A combo is **not** a substitute — it inverts the payoff. My "second-venue premise removed" note is itself now suspect: the premise may be removed, but **nothing establishes that a same-game correlated combo can even be created**, and the instrument that *can* be created does not do the job. |
| `COMPLETE_PAIR` | **OPEN** | Unchanged. A combo is a new position, not a completion of two held legs. |
| `MERGE` | **OPEN** | Unchanged. No netting-to-cash operation in the enumerated API. |
| `POST_COMPLEMENT` | **OPEN** | The taker route is **narrower** than I said: RFQ carries quote expiry, last look, submitted-≠-filled, and a resting remainder that reintroduces `P_FILL`. |

**Zero of the four are implemented. Nothing was demonstrated.** What today produced is better-grounded documentation of what each would require — and one corrected claim that would have pointed the work in the wrong direction.

## 6 · The correction, stated plainly

I proposed a multi-leg instrument as a route to a hedge **without reading its payoff**. The payoff is multiplicative; the hedge's value is that it is additive with a floor. Had that gone into the register unchallenged, the next piece of work would have built toward an instrument that **increases** directional risk in a lane authorized only to reduce it.
