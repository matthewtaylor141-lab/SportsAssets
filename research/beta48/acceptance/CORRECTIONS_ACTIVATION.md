# CORRECTIONS — activation review of `c87d1f4`

Five claims I made were wrong or too loose. Each is restated here with
its actual provenance, and the source documents are corrected.

---

## 1. "RN1 was the taker; the maker lost 0.90¢/share"

**What I wrote** (MAKER_EXPERIMENT_V3 §0): the number, with a venue and
a date range, used to argue that a BETTOR maker quote sits on "the
measured losing side".

**The full provenance**, which was already in the repository —
`BETA48_CLOSEOUT.md` §5 finding 2, `FOUR_WHALE_INTELLIGENCE_AUDIT.md`,
full detail in `MAKER_ENGINE_GATE_V2.md`:

| | |
|---|---|
| **Figure** | **−$0.0090/share net**, 95% CI **[−0.0143, −0.0038]** |
| **Cohort** | **112,553 trades over 9,337 conditions**, clustered by condition |
| **Venue** | **Polymarket CLOB** — *a different venue from every other figure in that document*, and a different venue from PMUS |
| **Window** | 2026-08-06 .. 09-11 |
| **Counterparty** | **ONE** informed taker (RN1) |
| **The maker** | **an anonymous resting offer that was not ours** |
| **Fills** | **observed, not modelled** |
| **Classification** | every row of the CLOB probe is `side=BUY` — that is how "he takes" was established |
| **Economic measure** | **MARKED TO SETTLEMENT** |
| **Repository's own verdict** | `THIS_IS_ACTUAL_BETTOR_ADVERSE_SELECTION = NO` |

### The three quantities are not the same, and this one is the third

| quantity | what it measures | is this it? |
|---|---|---|
| **markout** | midpoint at fill + Δt − midpoint at fill. Adverse selection proper. | **no** |
| **settlement payoff** | terminal value − entry, held to resolution | **YES — this is the 0.90¢** |
| **realized P&L** | cash from an actual round trip, fees included | **no** |

I used a settlement-marked number to argue about **markout**, which is
what a maker quoting and exiting within 300 s would actually face.
Those differ by construction: a settlement mark includes the entire
path to resolution, a markout includes 60 seconds of it.

### What it does and does not establish

**Does:** resting at the touch against *this* counterparty, on *that*
venue, over *that* window, held to settlement, was value-destructive by
0.90¢/share, and adverse selection was 2.8× the half-spread earned.

**Does NOT:** that every maker policy loses. One counterparty is not
the flow; one venue is not this venue; a settlement mark is not a
markout; and an anonymous quoter's queue position is not ours.

**Corrected in v3:** H2 is no longer "beat −0.90¢". It is an
independent measurement of BETTOR's own post-fill markout, with the
0.90¢ cited as a **reference point from a different venue, counterparty
and economic measure** — a reason to take adverse selection seriously,
not a prior on our own number.

## 2. "RN1 sold nothing"

**Wrong.** `BETA48_CLOSEOUT.md`'s table says "zero sells"; the audit
gives the actual figures:

> **234 sells, $583,580 stake = 0.153% of activity, SELL_PNL
> +$370,281 (ROI +63.4%).**

Negligible as a fraction of activity, **not zero**, and *profitable*.
"He sold nothing, so he tells us nothing about what selling costs" was
therefore wrong twice: he did sell, and those sells made money.

**Also measured, and I said it was not:** RN1's **hold duration** —
t25 = 120 s, t50 = **600 s**, t75 = 1800 s (of eventual completions),
and completion timing F(5 s)=4.98%, F(60 s)=16.83%, F(1 h)=67.41%,
F(settlement)=76.51%, with **23.49% of first legs never completing**.

That is an external reference point for capital duration. It is on a
different venue with a merge mechanism, so it does not transfer — but
"no holding period has ever been measured" was false as stated.

## 3. `bettor_merge.permitted=False` read as "capital never returns"

**The module I cited says, in its own words, that this reading is
backwards.** §12 of `bettor_merge.py`:

> `merge_permitted` answers "is there a merge ACTION to call". That is
> NOT the same question as "does capital come back", and reading the
> first as the second gets RETAIL exactly backwards: there is no merge
> call on retail AND capital recycles immediately, because the venue
> nets at the second fill. An allocator that consulted
> `merge_permitted` alone would conclude that retail capital never
> returns, which is the opposite of the truth.

`capital_recycling(INSTITUTIONAL)` is the function that answers the
question I was asking, and its answer is:

| step | institutional |
|---|---|
| PAIR_COMPLETE | AVAILABLE |
| **MERGE_OR_NET** | **NOT_IDENTIFIED** ← the chain breaks here |
| RELEASE_CAPITAL | NOT_IDENTIFIED |
| RECORD_LOCKED_PAIR_PNL | **AVAILABLE INDEPENDENTLY OF MERGE** |
| CAPITAL_RETURNS_TO_ALLOCATOR | NOT_IDENTIFIED |
| **CAPITAL_RECYCLING_AVAILABLE** | **NOT_IDENTIFIED** |

> "It is not zero and it is not NO: either would be a claim about the
> venue we have not established."

**Corrected:** whether completing a pair releases capital on the
institutional account is `NOT_IDENTIFIED`, not `NO`. Separately
establishing it is a named task — and on **retail** the same venue
recycles capital immediately by netting at the second fill, with no
merge call at all, which is direct evidence that a missing merge action
does not imply missing recycling.

**Fourth instance of the same mistake.** Depth, settlement maturity,
the fee schedule, and now this: I read a module's surface instead of
what the module says. This one is worse than the others, because the
correction was a comment inside the function I quoted.

## 4. "Zero fills anywhere in this repository"

**Contradicted by the ledger.** `venue_reconcile`'s own premise is
*"52 rows in `status='filled'` carrying $16,180.53"*, and the mirror
lane has traded.

What is actually true, in the scope that matters:

| claim | status |
|---|---|
| zero fills in the firm's ledger | **FALSE** — 52 filled rows |
| zero fills from **any BETTOR-native strategy** | **true** |
| zero **BETTOR resting orders** ever placed | **true** — which is why `p_fill` is unmeasurable |
| zero rows in `bettor_state_settlements` | **true** — 0 rows, `is_not_a_fill IS FALSE` = 0 |

**Corrected wording everywhere:** *"no BETTOR-native strategy has ever
had a fill, and no BETTOR order has ever rested"* — which is the claim
that actually supports "`p_fill` is NOT_IDENTIFIED".

## 5. The `$396,361/day` figure

**Method, stated so it can be checked:**

```sql
SELECT count(*) AS markets, sum(v) AS total_shares, sum(v*p) AS total_notional_usd
  FROM (SELECT market_id,
               max(stats_shares_traded::numeric) AS v,
               max(mid::numeric)                 AS p
          FROM bettor_state_observations
         WHERE stats_shares_traded ~ '^[0-9.]+$' AND mid ~ '^[0-9.]+$'
         GROUP BY market_id) t;
```
— `research/bettor_traded_volume.sql` §2, run
[35642283447](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35642283447).

| | |
|---|---|
| **Window** | the whole table: 2026-09-20 19:23 → 2026-09-21 18:26 UTC, **≈23 h**. The query has **no time predicate**; "per day" is the table's span, not a filter. |
| **Deduplication** | `max()` per `market_id` — one figure per market, so repeated observations are not summed |
| **Units** | `stats_shares_traded` = contracts (may be fractional); `mid` = USD/contract; product = USD |
| **Population** | **396 markets**, being those with **both** a numeric volume and a numeric mid — **not** all 1,526 observed. 606 had a volume figure; 396 had both. |

### Three caveats that travel with it

1. **`stats_shares_traded` is very likely CUMULATIVE for the market's
   life**, not per-window. `max()` per market therefore captures
   lifetime volume as of the last observation, which may include
   trading from **before** our window. **$396,361 is an upper bound on
   a single day's activity in this sample, not a measurement of one
   day.** Establishing which it is requires differencing the figure
   across two observations of the same market — a named next query.
2. **It is the SAMPLED universe, not the venue.** 1,526 markets seen by
   a research sampler not built for coverage. It is **not a ceiling on
   future activity** and not a ceiling on the venue.
3. `mid` is a mid, not an execution price. Traded notional at actual
   prints would differ.

### Reconciling 396 markets with the 100-market figure

They are **different populations** and I used them in one sentence:

| population | size | what it is |
|---|---|---|
| observed | 1,526 | everything the sampler saw |
| **measured for volume** | **396** | had both a volume and a price figure — the **participation denominator** |
| **selected universe** | **≤ 100** | passes the liquidity/activity rule and fits one subscription — the **fill-count numerator's** population |

So: participation is *our share of the 396-market measured activity*.
Fills-per-market is *over the ≤100 we actually quote in*. Saying "79
fills per market per day across 100 markets" against a denominator
built from 396 markets was mixing them. The corrected derivation is
below.

### Required fills, from explicit assumptions

| assumption | value | source |
|---|---|---|
| average execution price | **$0.3583** | measured: $396,361 ÷ 1,106,312 shares |
| clip size | 5 contracts | `MAKER_EXPERIMENT_V3` §3 |
| selected universe | 100 markets | one subscription, documented ceiling |

At 5% participation of the **measured** activity:

| | |
|---|---|
| executed notional | 0.05 × $396,361 = **$19,818/day** |
| contracts | $19,818 ÷ $0.3583 = **55,311/day** |
| fills at 5 contracts | **11,062/day** = 0.128/sec |
| per market, over the **selected 100** | **111/day** |

*(The earlier version used $0.50 rather than the measured $0.3583 and
divided per-market fills by a population it had not selected.)*

### Capital must not be derived by halving

**I halved executed notional to get position notional.** That assumes
every entry is matched by an **exit trade of comparable notional** —
i.e. completed round trips. It is wrong for the strategy RN1 and
Ferrari actually ran, and wrong for anything held to resolution.

| inventory model | exit | executed : position | capital for $500k executed |
|---|---|---|---|
| **round trip** (buy then sell) | a trade | 2 : 1 | $250,000 ÷ turns |
| **settlement-only** (hold to resolution) | **a payout, not a trade** | **1 : 1** | **$500,000 ÷ turns** |
| mixed | both | between | between |

RN1 exited **76.51% by settlement** and sold 0.153% of activity. On
that model the exit generates **no executed notional at all**, so
$500,000 of executed notional is $500,000 of entries and the capital
requirement **doubles**.

| holding period | turns/day | round-trip capital | settlement-only capital |
|---|---|---|---|
| held to settlement | 1.0 | $250,000 | **$500,000** |
| 4 h | 6.0 | $41,667 | $83,333 |
| 1 h | 24.0 | $10,417 | $20,833 |

**Which model applies is `NOT_IDENTIFIED`**, because
`CAPITAL_RECYCLING_AVAILABLE` is `NOT_IDENTIFIED` on this account (§3).
Both columns are carried until it is settled.

### Expanding measured coverage

Discovery now **pages** (`BETTOR_LIVE_DISCOVERY_PAGES`, default 6 × 500
= 3,000 markets) and reports `pages_read` and
`listing_truncated_at_page_bound`, so a universe drawn from a prefix of
the venue says so. **Live subscriptions stay bounded at 100** — the
documented per-subscription ceiling. Measuring widely and subscribing
narrowly are different budgets and are now configured separately.
