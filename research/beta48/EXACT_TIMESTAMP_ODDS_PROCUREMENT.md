# EXACT-TIMESTAMP ODDS — PROCUREMENT DECISION PACKAGE

Status: RESEARCH ONLY. **Nothing has been purchased. Nothing has been
requested. No credential exists.** No orders, no capital, `mirror_live=false`.

This document exists to let management decide what data access to buy, if any.
It reports what the experiment needs in requests and credits, and it is explicit
about which numbers were verified and which were not.

---

## 1. Why this is now the primary data question

Settlement forecasting has been measured and the market wins. On 47 common
events both independent lanes are worse than `P_MARKET_RAW` standalone, and
neither adds detected incremental value. The corrected incremental ladder needs
**4,654 events at the point estimate and 8,702 at P90** to resolve a 0.010
effect in the high-integrity lane. That is not reachable from the current
corpus.

Meanwhile the one genuinely independent *market* observation the programme could
hold — an outside consensus priced at a known instant — does not exist. Current
external odds are `COARSE_PREMATCH_UNTIMESTAMPED`: without a snapshot time there
is no way to say whether the consensus a model is compared against was formed
before or after the observation being scored, and that *is* the comparison.

So the gap is timing, not sport.

## 2. What was verified, and what was not

**Egress from this environment is blocked to every provider host:**

| Host | Result |
|---|---|
| `api.the-odds-api.com` | no response through the proxy |
| `the-odds-api.com` | no response through the proxy |
| `historicaldata.betfair.com` | no response through the proxy |
| `developer.betfair.com` | no response through the proxy |

Consequently **no price list and no quota formula was read.** Everything below
that depends on either is labelled `NOT_VERIFIED` and is held as a *parameter*
in `odds_api_adapter.CREDIT_FORMULA`, so correcting one constant re-computes
every scenario.

**Cost in currency is deliberately left empty.** A fabricated price standing
beside real request counts would be the most misleading number in this document.

## 3. Provider comparison

Ranked on **data suitability**, not vendor prestige and not price.

### Rank 1 — The Odds API

| | |
|---|---|
| Historical start | 2020-06-06 (featured markets) |
| Snapshot resolution | 5-minute from 2022-09; coarser before |
| Books / exchange | ~40 books incl. Pinnacle, Betfair, US books |
| Sports | soccer, NFL, NBA, MLB, NHL, tennis, more |
| Market types | h2h / moneyline, spreads, totals, some player props |
| Point-in-time guarantee | **yes** — returns the closest snapshot at or *earlier* than the requested time |
| Access | REST, historical endpoint, by date range |
| Pricing model | credit-based; **formula NOT_VERIFIED** |
| Expected match rate | high for the eight evaluated European leagues |

**Advantages.** The only option pairing a per-snapshot timestamp with a
documented sub-hourly cadence across the leagues this programme evaluates. Its
selection rule is already this programme's invariant, implemented provider-side.

**Limitations.** Paid for historical access. 5-minute granularity starts
2022-09, so earlier seasons are coarser. Credit cost scales with regions.

**Best BETTOR use.** The primary `P_EXTERNAL_TIMESTAMPED` consensus — several
books at a known instant, which is what a consensus requires.

### Rank 2 — Betfair Exchange historical

| | |
|---|---|
| Historical start | 2015-05 |
| Snapshot resolution | stream publish times; package-dependent |
| Books / exchange | one exchange — not a consensus |
| Market types | h2h, totals, correct score, Asian lines |
| Point-in-time guarantee | **yes** — publish time per record |
| Access | bulk file download |
| Pricing model | per-month archive packages; **NOT_VERIFIED** |

**Advantages.** The best timestamps and the only real microstructure: back, lay,
spread and traded volume rather than a single quote. Deep history.

**Limitations.** It is *one venue*. Restrictive licensing with explicit
non-redistribution. Which price objects a file carries depends on the package.

**Best BETTOR use.** An independent *exchange*-information source, modelled
separately from bookmaker consensus — never merged with it. A bookmaker quote
embeds one firm's margin and risk position; an exchange back/lay pair is other
participants' orders with a spread between them. Averaging them produces a
number that is neither, and the difference between them is itself a candidate
signal that merging would destroy.

### Rank 3 — OddsJam / OddsBlaze historical

| | |
|---|---|
| Historical start | ~2023 for most books |
| Snapshot resolution | sub-minute live; historical varies |
| Books | 100+ including offshore and US retail |
| Point-in-time guarantee | yes on the historical product |
| Pricing model | enterprise quote; **NOT_VERIFIED** |

**Advantages.** Widest book coverage; strong player-prop breadth.

**Limitations.** The 2023 start date is binding — it cannot price the training
history the models already use.

**Best BETTOR use.** Breadth expansion *later*, not the first purchase.

### Considered and excluded

**Football-Data.co.uk** (current source, via xgabora). Free and deep, but
`COARSE_PREMATCH_UNTIMESTAMPED` — no snapshot time at all, and opening versus
closing is not distinguished per row. It is why this procurement exists.

## 4. What the experiment costs

Requests = **events × horizons × markets**. The historical endpoint returns all
bookmakers for a sport, region and market in one response, so more books cost
*regions*, not requests.

Horizons: T−24H, T−12H, T−6H, T−3H, T−2H, T−1H, T−30M, T−15M, T−5M (9).
Markets: h2h, spreads, totals (3). Regions: uk, eu (2).

| Scenario | Events | Requests | Credits | Estimated cost |
|---|---|---|---|---|
| A | 100 | 2,700 | 54,000 | *not estimated* |
| B | 500 | 13,500 | 270,000 | *not estimated* |
| C | 1,000 | 27,000 | 540,000 | *not estimated* |
| D | 5,000 | 135,000 | 2,700,000 | *not estimated* |

Credits assume `markets × regions × 10` per historical request — **the
multiplier is NOT_VERIFIED.** Correct it in one place and the table re-computes.

## 5. Recommendation

**Do not start at scenario D.** 5,000 events would satisfy the settlement
ladder, but that is the expensive question and the one most likely to return
another null.

**Start with the lead/lag question**, which needs far fewer events because a
price move is observed on *every* event whereas a settlement is one binary
outcome per event.

> **Cheapest experiment that answers something real:** 500 events × 3 horizons
> (T−24H, T−2H, T−15M) × 1 market (h2h) = **30,000 credits** — nine times
> cheaper than scenario B and sufficient to measure whether external consensus
> leads Polymarket and by how much.

What that buys:

- **Can answer:** does external consensus lead Polymarket, and by how much,
  conditional on disagreement size, liquidity, spread and time-to-event.
- **Cannot answer:** whether external consensus improves settlement
  forecasting. That needs the full incremental ladder.

If the lead/lag result is positive, scenario B or C becomes justified on
evidence rather than hope. If it is null, the programme has spent the smallest
sum that could have told it so.

## 6. What is already built, awaiting only data

| Component | Status |
|---|---|
| `HistoricalOddsSnapshotProvider` | built; enforces `SNAPSHOT_TIMESTAMP <= REQUESTED_AS_OF_TIMESTAMP` |
| The Odds API adapter | built; refuses without transport or credential |
| Betfair historical adapter | built; five price objects modelled separately |
| Consensus estimators | built (mean, median, trimmed, quality-weighted, hierarchical) |
| Lead/lag experiment | built; event-clustered intervals |
| Disagreement features | built; nine candidates, none assumed to be alpha |
| Procurement calculator | built; this table is its output |

Every one of these returns `NO_EXTERNAL_DATA` today. That is the honest status,
and it is a status rather than a zero.

## 7. The decision being asked for

1. Authorise a credential for The Odds API at the **30,000-credit** first step,
   or decline.
2. Nothing else. Betfair is rank 2 and can wait for the lead/lag result;
   OddsJam's history starts too late to be a first purchase.

Nothing in this document has been purchased, requested, or committed to.
