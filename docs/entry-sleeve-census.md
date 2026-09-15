# Entry-sleeve census — what is stored, and how each sleeve has done

**Run:** `research-sql` 34530457527, 2026-09-10T21:08:21Z, `research/entry_sleeve_census.sql`,
`psql exit=0`. Every figure below is from that run.

---

## Headline

**The model-driven lane has recorded 349,411 plays and settled ZERO of them.** There is no
P&L for it — not a bad P&L, none at all. Nothing in the table has ever been graded against a
result, so the sleeve's historical performance cannot be stated, and nobody should quote a
number for it.

Two further facts change how the rest should be read:

- **It stopped writing on 2026-09-04**, six days ago. Consistent with the entry system being
  switched off.
- **Its own average edge is negative: −0.0499.** Mean price 0.4323 against mean fair value
  0.3849. If these were intended bets, the engine was recording buys ~5¢ above its own fair
  value, on average, across a third of a million rows.

---

## 1. What is actually stored, and where

| table | what it is | rows | span |
|---|---|---:|---|
| `engine_fills` | **the internal-model lane** — the one that would carry devigged prices | 349,411 | 2026-07-24 → 2026-09-04 |
| `ai_trades` | whale-copy **paper** account (his price, counterfactual at his price) | 582,691 | 2026-08-03 → 2026-09-05 |
| `live_orders` | **real venue orders**, labelled by sleeve | 166,585 | 2026-08-03 → 2026-09-10 |

**Where the model itself lives — this matters.** `engine_fills` rows arrive by HTTP `POST` to
`/api/engine/fills` behind an engine token (`api/app.py:1254`). The request carries
`fair_value`, `edge`, `league`, `band`, `limit_price` and a top-of-book snapshot. **The code
that computes fair value is not in this repository.** I searched for `pinnacle`, `devig`,
`no_vig`, `fair_prob`, `vig`, `juice`, `implied_prob` across the whole backend and found
nothing. So whatever devigs Pinnacle prices runs somewhere else and posts its conclusions
here. This database holds the *record*, not the method — I can tell you what it decided, never
why.

`engine_fills` columns: `ts, venue, market_id, outcome_id, league, band, limit_price,
size_usd, fair_value, edge, would_fill, whale_alignment, book (jsonb), settled, payout, pnl,
settled_at, created_at, dedupe_key`.

## 2. The model lane in detail

| | |
|---|---:|
| rows | 349,411 |
| **settled** | **0** |
| would_fill = true | 318,956 (91.3%) |
| have fair_value / edge | 349,179 (99.93%) |
| venues | 2 (polymarket-us 274,479 · kalshi 74,932) |
| leagues | 39 |
| bands | 21 |
| notional recorded | $3,487,601.59 |
| first / last | 2026-07-24 / 2026-09-04 |

By month:

| month | plays | days with plays | settled | notional |
|---|---:|---:|---:|---:|
| 2026-07 | 25,490 | 8 | **0** | $253,082.00 |
| 2026-08 | 260,888 | 20 | **0** | $2,604,327.06 |
| 2026-09 | 63,033 | 4 | **0** | $630,192.53 |

**No `pnl`, no `payout`, no `settled_at` anywhere in the table.** `analytics/engine.py` has a
settlement sweep for `ai_trades`; nothing has ever graded `engine_fills`.

### These are evaluations, not a bet list

There is **no column marking which rows the engine would actually have taken.** `would_fill`
is a book-depth flag (was the size available), not a decision. And some rows cannot be bets on
any reading:

| venue | league | band | plays | avg limit | avg fair | avg edge |
|---|---|---|---:|---:|---:|---:|
| polymarket-us | soccer_usa_mls | 0.95–1.00 | 4,087 | 0.9837 | 0.3401 | **−0.6409** |
| polymarket-us | soccer_concacaf_leagues_cup | 0.95–1.00 | 3,320 | 0.9892 | 0.3899 | **−0.5959** |
| polymarket-us | soccer_austria_bundesliga | (null) | 3,846 | 0.5028 | 0.3114 | −0.1905 |
| polymarket-us | soccer_league_of_ireland | (null) | 3,010 | 0.5535 | 0.3352 | −0.2173 |

Nobody pays 98¢ for something they price at 34¢. Those rows are the engine recording what it
looked at.

### A defect worth naming

The fair value **tracks the price band properly on MLB** — 0.10–0.15 → 0.1228, 0.35–0.40 →
0.4653, 0.45–0.50 → 0.4758, 0.60–0.65 → 0.5470. So the model is doing real work there.

But on **high-priced soccer contracts it pins near 1/3**: the mls 0.95–1.00 band prices at
0.9837 and is valued at 0.3401. Mid-band soccer tracks fine (mls 0.20–0.25 → 0.2195, 0.25–0.30
→ 0.2593, 0.30–0.35 → 0.3124), so this is specific to the expensive end.

**What I am not claiming.** Several people would look at soccer fair values averaging ~0.333
across leagues and conclude the model returns a uniform 1/3 prior. That inference is wrong:
for a three-way market the average fair value across all outcomes *must* be ~1/3 if the
probabilities sum to one, and likewise ~0.50 for two-way (kalshi nfl 0.4917, wta 0.5046, wnba
0.5006). The averages prove nothing either way. The confirming test is the **dispersion** of
`fair_value` within a league — a real model has spread, a constant has none. That is one more
query and I have not run it.

## 3. Every sleeve with real venue orders

`live_orders`, by the label each sleeve writes:

| sleeve | orders | filled | cashed out | settled | failed | filled $ | graded | P&L | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| RN1 *(old copy)* | 76,144 | 17 | 18 | 2,546 | 73,132 | $152,701.89 | 2,566 | **+$2,181.42** | +1.46% |
| swisstony | 45,919 | 16 | 2 | 672 | 45,229 | $11,962.95 | 674 | +$784.65 | +7.34% |
| ferrariChampions2026 | 23,783 | 5 | 30 | 411 | 23,277 | $41,667.97 | 442 | −$537.78 | −1.31% |
| HomeRunHazard | 7,659 | 4 | 26 | 171 | 7,425 | $23,824.62 | 197 | −$3,568.27 | −15.14% |
| 0x076daa87 | 7,494 | 2 | 25 | 317 | 7,093 | $26,821.73 | 341 | −$4,751.78 | −18.07% |
| 0x2c335066… | 3,747 | 1 | 3 | 92 | 3,651 | $8,406.06 | 95 | +$308.86 | +3.74% |
| **rn1** *(current mirror)* | 1,631 | 7 | 353 | 889 | **0** | $216,428.86 | 1,248 | **−$24,077.50** | **−11.13%** |
| underdog | 201 | 1 | 0 | 172 | 28 | $291.75 | 172 | −$1,619.71 | *see below* |
| manual | 7 | 0 | 0 | 0 | 7 | $0.00 | 0 | — | — |

**The old copy sleeves almost never filled.** RN1 96.0% failed (73,132 of 76,144), swisstony
98.5%. The current mirror is the opposite — **0 failed of 1,631** — which is the execution work
of the last week doing its job, on a sleeve that is nonetheless losing 11.13%.

**The underdog −558% is not a result, it is a defect.** 201 entries at ~$2 each, $291.75
actually filled, cannot produce a −$1,619.71 loss; the worst possible outcome on that book is
about −$292. Either `pnl` or `filled_usd` is wrong on those rows. Cash-out rate reads 0.0% for
August, which also contradicts the sleeve's own design. I would not report any underdog number
until that is reconciled.

## 4. The paper account, and the number that matters most

`ai_trades` — simulated copies at *our* achievable price, with a counterfactual at *his*:

| whale | rows | settled | staked | **actual P&L** | **at HIS price** | ROI |
|---|---:|---:|---:|---:|---:|---:|
| ferrariChampions2026 | 184,987 | 80,309 | $5,135,325 | −$24,834.37 | +$27,022.95 | −1.15% |
| swisstony | 159,893 | 64,384 | $1,747,685 | +$7,524.20 | +$26,064.12 | +1.09% |
| RN1 | 143,980 | 56,232 | $3,014,813 | −$309.39 | **+$41,120.61** | −0.03% |
| HomeRunHazard | 59,623 | 27,616 | $2,141,455 | +$20,836.31 | +$37,910.42 | +2.17% |
| 0x076daa87 | 20,247 | 8,413 | $584,070 | −$2,762.57 | −$1,962.24 | −1.08% |
| 0x2c335066… | 13,961 | 6,729 | $3,696,847 | −$100,507.55 | +$24,143.96 | −5.92% |
| **total** | **582,691** | **243,683** | **$16.3M** | **−$100,053.37** | **+$154,299.82** | |

**Read that last row twice.** On 243,683 settled paper trades, the edge measured at the
whales' own prices is **+$154,300**. What our execution achieved on the identical trades is
**−$100,053**. A gap of roughly **$254,000**, and the sign flips across it. Every whale except
0x076daa87 shows a positive counterfactual; only two show a positive realised figure.

That is the same finding as the mirror's 3.61x loss multiple, the same finding as the fee
evidence, and the same finding as today's band study: **the selection is not the problem. The
execution is.**

## 5. Direct answers

**What is stored for the software plays:** 349,411 rows in `engine_fills`, 2026-07-24 to
2026-09-04, across 2 venues, 39 leagues and 21 price bands, carrying the engine's `limit_price`,
its `fair_value`, its `edge`, a book snapshot and a `would_fill` flag. $3.49M of notional
recorded.

**How that sleeve has performed historically in shadow:** **unknown, and unknowable from the
stored data.** Zero rows settled, so no return exists. It also has no field distinguishing a
play the engine would take from a market it merely priced, so even after grading, a performance
figure would need that distinction added first.

**What would make it answerable:**
1. **Grade the rows.** `markets.resolved_prices` holds the settlement payout, and `outcome_id`
   should join to it. That is a backfill, and it is the whole difference between 349,411 dead
   rows and a real 6-week track record. It is the single highest-value thing available here.
2. **Add a decision flag** — or tell me the rule the external engine used, and I will apply it.
   Without it, "performance" means averaging bets nobody would have made.
3. **Run the dispersion check** on `fair_value` per league, to settle whether the soccer
   figures are a real model or a fallback.
4. **Fix the underdog P&L** before any underdog number is used.

Items 1 and 3 are read-only and I can do both on the existing surface.
